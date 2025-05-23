import os
import logging
from datetime import time
import requests
import re
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes,
    JobQueue
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variables to store article data
user_articles = {}  # Format: {user_id: {"article": article_data, "read": bool}}
BASE_URL = "https://www.scientificamerican.com"

async def fetch_latest_article():
    """Fetch the latest article from Scientific American"""
    try:
        response = requests.get(BASE_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        article_link = soup.find('a', class_='articleLink-2OMNo')['href']
        if not article_link.startswith('http'):
            article_link = BASE_URL + article_link
        
        article_response = requests.get(article_link)
        article_response.raise_for_status()
        article_soup = BeautifulSoup(article_response.text, 'html.parser')
        
        title = article_soup.find('h1', class_='article_hed-9vUZD').get_text(strip=True)
        summary = article_soup.find('div', class_='body-n28ll prose-Yw0x0 prose-v4bYC article__body-ivA3W').get_text(strip=True)
        article_body = article_soup.find('div', class_='body-n28ll prose-Yw0x0 prose-v4bYC article__body-ivA3W')
        content = " ".join([p.get_text(strip=True) for p in article_body.find_all('p')])

        raw_words = content.split()[:20]
        clean_words = []
        
        for word in raw_words:
            clean_word = re.sub(r"[^\w']", '', word.lower())
            
            if len(clean_word) > 5:
                clean_words.append(clean_word)

        key_words = list(set(clean_words))[:5]
        #words = content.split()[:20]
        #key_words = list(set([word.lower() for word in words if len(word) > 5]))[:5]
        
        definitions = {}
        key = os.getenv('X-RapidAPI-Key')
        headers = {
            "X-RapidAPI-Key": f"{key}",
            "X-RapidAPI-Host": "wordsapiv1.p.rapidapi.com"
        }
        for word in key_words:
            freq_url = f"https://wordsapiv1.p.rapidapi.com/words/{word}/frequency"
            freq_resp = requests.get(freq_url, headers=headers)

            if freq_resp.status_code == 200:
                freq_data = freq_resp.json().get("frequency", {})
                zipf = freq_data.get("zipf", 0)
                diversity = freq_data.get("diversity", 1)

                if zipf <= 4.5 and diversity <= 0.3:
                    url = f"https://wordsapiv1.p.rapidapi.com/words/{word}/definitions"
                    response = requests.get(url, headers=headers)
                    
                    if response.status_code == 200:
                        data = response.json()
                        defs = data.get("definitions", [])
                        
                        if defs:
                            definitions[word] = [d['definition'] for d in defs[:3]]
                        else:
                            definitions[word] = "No definition found."
                
        return {
            "title": title,
            "link": article_link,
            "key_words": definitions
        }
    except Exception as e:
        logger.error(f"Error fetching article: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_text(
        f"Hi {user.first_name}! I'll send you daily articles from Scientific American at 9:30 AM. "
        "You'll receive reminders until you mark them as read."
    )
    
    # Store user in context for job scheduling
    context.job_queue.run_daily(
        send_daily_article,
        time=time(hour=9, minute=30),
        days=(0, 1, 2, 3, 4, 5, 6),
        chat_id=update.effective_chat.id,
    )

async def send_daily_article(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the daily article to all users"""
    job = context.job
    article = await fetch_latest_article()
    
    if not article:
        await context.bot.send_message(job.chat_id, text="Sorry, couldn't fetch an article today.")
        return
    
    user_articles[job.chat_id] = {"article": article, "read": False}
    
    key_words_text = ""

    for word, defs in article["key_words"].items():
        if isinstance(defs, list):
            definitions_formatted = "\n".join([f"   {i+1}. {definition}" for i, definition in enumerate(defs)])
        else:
            definitions_formatted = f"   {defs}"
    
        key_words_text += f"• *{word}*:\n{definitions_formatted}\n\n"
    
    message = (
        f"📚 *{article['title']}*\n\n"
        f"*Key Words:*\n{key_words_text}\n\n"
        f"[Read full article]({article['link']})"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Mark as read", callback_data='mark_read')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        job.chat_id,
        text=message,
        parse_mode='Markdown',
        disable_web_page_preview=True,
        reply_markup=reply_markup
    )

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send reminder if article hasn't been read"""
    job = context.job
    if job.chat_id in user_articles and not user_articles[job.chat_id]["read"]:
        article = user_articles[job.chat_id]["article"]
        
        keyboard = [
            [InlineKeyboardButton("✅ Mark as read", callback_data='mark_read')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            job.chat_id,
            text=f"⏰ Reminder: Have you read today's article?\n*{article['title']}*",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button clicks"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data == 'mark_read':
        if user_id in user_articles:
            user_articles[user_id]["read"] = True
            await query.answer("Article marked as read!")
            await query.edit_message_reply_markup(reply_markup=None)
            
            # Remove any pending reminders
            job_names = [f"reminder_{user_id}_15", f"reminder_{user_id}_18", f"reminder_{user_id}_21"]
            for job_name in job_names:
                if context.job_queue.get_jobs_by_name(job_name):
                    for job in context.job_queue.get_jobs_by_name(job_name):
                        job.schedule_removal()
        else:
            await query.answer("No active article to mark as read.")

def main() -> None:
    """Run the bot."""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("Please set the TELEGRAM_BOT_TOKEN environment variable")
    
    # Create the Application
    application = ApplicationBuilder().token(token).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_click))

    # Schedule reminders
    scheduler = BackgroundScheduler()
    for hour in [15, 18, 21]:
        scheduler.add_job(
            send_reminder,
            trigger=CronTrigger(hour=hour, minute=0),
            args=[application],
            name=f"reminder_{hour}"
        )
    scheduler.start()

    # Run the bot
    application.run_polling()

if __name__ == '__main__':
    main()