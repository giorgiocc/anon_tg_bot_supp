import os
import logging
import threading
import asyncio
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from pymongo import MongoClient
from dotenv import load_dotenv
from bson import ObjectId
import certifi

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MONGO_DB = os.getenv("MONGO_DB")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

mongo_client = MongoClient(MONGO_DB, tls=True, tlsCAFile=certifi.where())
db = mongo_client.get_default_database("support")  
tickets_collection = db.tickets 

app = Flask(__name__)

bot_app = Application.builder().token(BOT_TOKEN).build()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message when user sends /start."""
    await update.message.reply_text("მოგესალმებით! რით შეგვიძლია დაგეხმაროთ? გთხოვთ მოიწერეთ სრული ტექსტი.")

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user messages by creating a ticket and notifying the admin."""
    user = update.message.from_user
    chat_id = update.message.chat_id
    text = update.message.text

    logger.info(f"Received message from {user.id} ({user.username}): {text}")

    if user.id == ADMIN_ID:
        await update.message.reply_text("Admin message received. Use /reply to respond to a ticket.")
        return

    username = user.username if user.username else f"{user.first_name or ''} {user.last_name or ''}".strip()

    ticket = {
        "user_id": user.id,
        "user_chat_id": chat_id,
        "username": username,
        "message": text,
        "status": "new"
    }
    ticket_id = await asyncio.to_thread(
        lambda: str(tickets_collection.insert_one(ticket).inserted_id)
    )

    keyboard = [
        [InlineKeyboardButton("Mark as Read", callback_data=f"read_ticket|{ticket_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    admin_message = (
        f"📩 *New Message from User*\n"
        f"👤 *User:* {user.first_name} (@{username})\n"
        f"🆔 *UID:* `{user.id}`\n\n"
        f"💬 *Message:*\n{text}\n\n"
        f"📝 *Ticket ID:* `{ticket_id}`"
    )

    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_message, reply_markup=reply_markup, parse_mode="Markdown")
        logger.info(f"Sent message to admin ({ADMIN_ID}).")
    except Exception as e:
        logger.error(f"Failed to send message to admin: {e}")

    await update.message.reply_text("Your message has been sent to the admin. You will receive a response shortly.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for the admin button to mark ticket as read."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("read_ticket"):
        _, ticket_id = data.split("|")
        await asyncio.to_thread(
            lambda: tickets_collection.update_one(
                {"_id": ObjectId(ticket_id)},
                {"$set": {"status": "read"}}
            )
        )
        ticket = await asyncio.to_thread(
            lambda: tickets_collection.find_one({"_id": ObjectId(ticket_id)})
        )
        if ticket:
            user_chat_id = ticket["user_chat_id"]
            await context.bot.send_message(
                chat_id=user_chat_id,
                text="Your ticket has been read and is being processed."
            )
        await query.edit_message_text(text="Ticket marked as read.")
        
async def test_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text="🔧 Admin test message.")
        logger.info(f"Successfully sent test message to ADMIN_ID ({ADMIN_ID}).")
    except Exception as e:
        logger.error(f"Failed to send test message to admin: {e}")

bot_app.add_handler(CommandHandler("testadmin", test_admin_message))

async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Allows the admin to reply to a ticket.
    Usage: /reply <ticket_id> <message>
    """
    user = update.message.from_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("Unauthorized command.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /reply <ticket_id> <message>")
        return

    ticket_id = args[0]
    reply_text = " ".join(args[1:])

    try:
        ticket = await asyncio.to_thread(
            lambda: tickets_collection.find_one({"_id": ObjectId(ticket_id)})
        )
    except Exception:
        await update.message.reply_text("Invalid ticket ID.")
        return

    if not ticket:
        await update.message.reply_text("Ticket not found.")
        return

    user_chat_id = ticket["user_chat_id"]
    await context.bot.send_message(chat_id=user_chat_id, text=f"Admin reply: {reply_text}")
    await update.message.reply_text("Reply sent to the user.")

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_user_message))
bot_app.add_handler(CallbackQueryHandler(button_callback))
bot_app.add_handler(CommandHandler("reply", reply_command))


@app.route('/')
def index():
    """Minimal Flask endpoint that returns 'hello'."""
    return 'hello'


def main():
    flask_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))),
        daemon=True
    )
    flask_thread.start()

    bot_app.run_polling()

if __name__ == '__main__':
    main()
