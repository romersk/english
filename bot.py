import os
import logging
from datetime import time, datetime
import requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler, CallbackContext,
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

def fetch_latest_article():
    """Fetch the latest article from Scientific American"""
    try:
        response = requests.get(BASE_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find the latest article (this selector might need adjustment)
        article_link = soup.find('a', class_='listing-block__item__title')['href']
        if not article_link.startswith('http'):
            article_link = BASE_URL + article_link
        
        # Fetch article content
        article_response = requests.get(article_link)
        article_response.raise_for_status()
        article_soup = BeautifulSoup(article_response.text, 'html.parser')
        
        # Extract article details
        title = article_soup.find('h1', class_='article-header__title').get_text(strip=True)
        summary = article_soup.find('div', class_='article-header__dek').get_text(strip=True)
        content = " ".join([p.get_text(strip=True) for p in article_soup.find_all('p', class_='article-text')])
        
        # Extract key words (simplified example)
        words = content.split()[:20]  # Just take first 20 words as example
        key_words = list(set([word.lower() for word in words if len(word) > 5]))[:5]  # Get 5 longer words
        
        # Get definitions (mock - in real implementation you'd query Oxford dictionary API)
        definitions = {}
        for word in key_words:
            definitions[word] = f"Definition of {word} would appear here (from Oxford dictionary)"
        
        return {
            "title": title,
            "summary": summary,
            "link": article_link,
            "content": content[:1000] + "..." if len(content) > 1000 else content,  # Limit content length
            "key_words": definitions
        }
    except Exception as e:
        logger.error(f"Error fetching article: {e}")
        return None

def start(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    update.message.reply_text(
        f"Hi {user.first_name}! I'll send you daily articles from Scientific American at 9:30 AM. "
        "You'll receive reminders until you mark them as read."
    )

def send_daily_article(context: CallbackContext) -> None:
    """Send the daily article to all users"""
    job = context.job
    article = fetch_latest_article()
    
    if not article:
        context.bot.send_message(job.context, text="Sorry, couldn't fetch an article today.")
        return
    
    # Store article for user
    user_articles[job.context] = {"article": article, "read": False}
    
    # Format message with key words
    key_words_text = "\n".join([f"• {word}: {definition}" for word, definition in article["key_words"].items()])
    
    message = (
        f"📚 *{article['title']}*\n\n"
        f"{article['summary']}\n\n"
        f"*Key Words:*\n{key_words_text}\n\n"
        f"[Read full article]({article['link']})"
    )
    
    # Create inline keyboard with "Mark as read" button
    keyboard = [
        [InlineKeyboardButton("✅ Mark as read", callback_data='mark_read')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    context.bot.send_message(
        job.context,
        text=message,
        parse_mode='Markdown',
        disable_web_page_preview=True,
        reply_markup=reply_markup
    )

def send_reminder(context: CallbackContext) -> None:
    """Send reminder if article hasn't been read"""
    user_id = context.job.context
    if user_id in user_articles and not user_articles[user_id]["read"]:
        article = user_articles[user_id]["article"]
        
        keyboard = [
            [InlineKeyboardButton("✅ Mark as read", callback_data='mark_read')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        context.bot.send_message(
            user_id,
            text=f"⏰ Reminder: Have you read today's article?\n*{article['title']}*",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

def button_click(update: Update, context: CallbackContext) -> None:
    """Handle button clicks"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data == 'mark_read':
        if user_id in user_articles:
            user_articles[user_id]["read"] = True
            query.answer("Article marked as read!")
            query.edit_message_reply_markup(reply_markup=None)
            
            # Remove any pending reminders
            job_names = [f"reminder_{user_id}_15", f"reminder_{user_id}_18", f"reminder_{user_id}_21"]
            for job_name in job_names:
                if context.job_queue.get_jobs_by_name(job_name):
                    for job in context.job_queue.get_jobs_by_name(job_name):
                        job.schedule_removal()
        else:
            query.answer("No active article to mark as read.")

def main() -> None:
    """Run the bot."""
    # Get the token from environment variable
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("Please set the TELEGRAM_BOT_TOKEN environment variable")
    
    updater = Updater(token)
    dispatcher = updater.dispatcher

    # Register commands
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CallbackQueryHandler(button_click))

    # Schedule daily article at 9:30 AM
    job_queue = updater.job_queue
    
    # For testing, you can use this to send immediately:
    # job_queue.run_once(send_daily_article, 5, context=user_id)
    
    # In production, schedule for 9:30 AM daily
    job_queue.run_daily(
        send_daily_article,
        time=time(hour=9, minute=30),
        days=(0, 1, 2, 3, 4, 5, 6),
        context=None  # Will be set when user starts the bot
    )
    
    # Schedule reminders at 15:00, 18:00, 21:00
    scheduler = BackgroundScheduler()
    for hour in [15, 18, 21]:
        scheduler.add_job(
            send_reminder,
            trigger=CronTrigger(hour=hour, minute=0),
            args=[dispatcher.bot, JobQueue(updater)],
            name=f"reminder_{hour}"
        )
    scheduler.start()

    # Start the Bot
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()