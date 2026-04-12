import os
import logging
from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler

# Enable logging (helps you see errors in Railway logs)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Load environment variables from Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Basic /start command
async def start(update, context):
    await update.message.reply_text("Bot is running!")

# Simple function to send a test message on startup
async def send_startup_message(app):
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="Bot started successfully on Railway!")

def main():
    # Build the bot application
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add commands
    app.add_handler(CommandHandler("start", start))

    # Send a message when the bot boots
   app = ApplicationBuilder().token(BOT_TOKEN).post_init(send_startup_message).build()


    # Start polling
    app.run_polling()

if __name__ == "__main__":
    main()
