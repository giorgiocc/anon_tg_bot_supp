from datetime import datetime, timezone
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
from dateutil import parser  

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MONGO_DB = os.getenv("MONGO_DB")
MONGO_CHECK_URI = os.getenv("MONGO_CHECK_URI") 

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

mongo_client = MongoClient(MONGO_DB)
db = mongo_client.get_default_database("support")
tickets_collection = db.tickets

mongo_check_client = MongoClient(MONGO_CHECK_URI)
check_db = mongo_check_client.GeorgiaChatbot  

app = Flask(__name__)
bot_app = Application.builder().token(BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id == ADMIN_ID:
        if 'reply_ticket_id' in context.user_data:
            del context.user_data['reply_ticket_id']
    await update.message.reply_text("მოგესალმებით! რით შეგვიძლია დაგეხმაროთ? გთხოვთ მოიწერეთ სრული ტექსტი.")

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    chat_id = update.message.chat_id
    text = ""
    media_type = None
    file_id = None

    if update.message.photo:
        media_type = 'photo'
        file_id = update.message.photo[-1].file_id
        text = update.message.caption or ""
    elif update.message.voice:
        media_type = 'voice'
        file_id = update.message.voice.file_id
        text = update.message.caption or ""
    elif update.message.video:
        media_type = 'video'
        file_id = update.message.video.file_id
        text = update.message.caption or ""
    else:
        text = update.message.text

    blocked = await asyncio.to_thread(
        lambda: db.blocked_users.find_one({"user_id": user.id})
    )
    if blocked:
        await update.message.reply_text("თქვენ დაბლოკილი ხართ და აღარ შეგიძლიათ შეტყობინებების გაგზავნა")
        return

    if user.id == ADMIN_ID:
        if 'reply_ticket_id' in context.user_data:
            return
        await update.message.reply_text("Use the Reply button to respond to tickets.")
        return

    username = user.username if user.username else f"{user.first_name or ''} {user.last_name or ''}".strip()

    ticket = {
        "user_id": user.id,
        "user_chat_id": chat_id,
        "username": username,
        "message": text,
        "status": "new",
        "media": {"type": media_type, "file_id": file_id} if media_type else None
    }
    ticket_id = await asyncio.to_thread(
        lambda: str(tickets_collection.insert_one(ticket).inserted_id)
    )

    keyboard = [
        [InlineKeyboardButton("Mark as Read", callback_data=f"read_ticket|{ticket_id}")],
        [InlineKeyboardButton("Reply", callback_data=f"reply_ticket|{ticket_id}")],
        [
            InlineKeyboardButton("Block User", callback_data=f"block_user|{user.id}"),
            InlineKeyboardButton("Check User", callback_data=f"check_user|{user.id}")
        ]
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
        if media_type == 'photo':
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=file_id,
                caption=admin_message,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        elif media_type == 'voice':
            await context.bot.send_voice(
                chat_id=ADMIN_ID,
                voice=file_id,
                caption=admin_message,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        elif media_type == 'video':
            await context.bot.send_video(
                chat_id=ADMIN_ID,
                video=file_id,
                caption=admin_message,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_message,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        logger.info(f"✅ Sent message to admin ({ADMIN_ID}) from {user.id}.")
    except Exception as e:
        logger.error(f"❌ Failed to send message to admin ({ADMIN_ID}): {e}")

    await update.message.reply_text("შეტყობინება გაგზავნილია ადმინისტრაციაში, გთოხვ დაელოდო პასუხს!")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                text="თქვენი მოთხოვნა განიხილა და საკითხი დახურულია!"
            )
        await query.edit_message_text(text="ბილეთი დახურულია!")
    elif data.startswith("reply_ticket"):
        _, ticket_id = data.split("|")
        context.user_data['reply_ticket_id'] = ticket_id
        await query.message.reply_text("შეიყვანე შეტყობინება: ")

    elif data.startswith("block_user"):
        _, user_id = data.split("|")
        user_id = int(user_id)
        blocked_user = {
            "user_id": user_id,
            "blocked_at": datetime.now(timezone.utc)
        }
        await asyncio.to_thread(
            lambda: db.blocked_users.insert_one(blocked_user)
        )
        await query.edit_message_text(text=f"User {user_id} has been blocked.")

    elif data.startswith("check_user"):
        _, user_id = data.split("|")
        user_id = int(user_id)
        
        user = await asyncio.to_thread(
            lambda: check_db.users.find_one({"_id": user_id})
        )
        
        if not user:
            await query.message.reply_text(f"User {user_id} not found in check database.")
            return

        current_time = datetime.now(timezone.utc)
        premium_until = user.get("premium_until")
        if isinstance(premium_until, str):
            premium_until = parser.parse(premium_until)
        if premium_until and premium_until.tzinfo is None:
            premium_until = premium_until.replace(tzinfo=timezone.utc)
        is_premium = user.get("premium", False) and premium_until and premium_until > current_time
        
        ban_until = user.get("ban_until")
        is_banned = False
        if ban_until:
            if isinstance(ban_until, str):
                ban_until = parser.parse(ban_until)
            if ban_until.tzinfo is None:
                ban_until = ban_until.replace(tzinfo=timezone.utc)
            is_banned = ban_until > current_time
        
        message = (
            f"🔍 *User Check Results*\n"
            f"🆔 User ID: `{user_id}`\n"
            f"👤 Username: @{user.get('username', 'N/A')}\n"
            f"⚧ Gender: {user.get('gender', 'Not set')}\n"
            f"💎 Premium: {'Yes' if is_premium else 'No'}\n"
            f"🚫 Banned: {'Yes' if is_banned else 'No'}\n"
            f"📅 Last Active: {user.get('last_active', 'N/A')}\n"
            f"🔨 Ban Count: {len(user.get('ban_history', []))}\n"
            f"📝 Auto Delete: {'Enabled' if user.get('auto_delete', True) else 'Disabled'}"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            parse_mode="Markdown"
        )

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id != ADMIN_ID:
        return

    ticket_id = context.user_data.get('reply_ticket_id')
    if not ticket_id:
        return

    ticket = await asyncio.to_thread(
        lambda: tickets_collection.find_one({"_id": ObjectId(ticket_id)})
    )
    if not ticket:
        await update.message.reply_text("Ticket not found.")
        del context.user_data['reply_ticket_id']
        return

    user_chat_id = ticket["user_chat_id"]

    if update.message.text:
        reply_text = update.message.text
        await context.bot.send_message(chat_id=user_chat_id, text=f"Admin: {reply_text}")
    elif update.message.photo:
        photo = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        await context.bot.send_photo(
            chat_id=user_chat_id,
            photo=photo,
            caption=f"Admin: {caption}"
        )
    elif update.message.voice:
        voice = update.message.voice.file_id
        caption = update.message.caption or ""
        await context.bot.send_voice(
            chat_id=user_chat_id,
            voice=voice,
            caption=f"Admin: {caption}"
        )
    elif update.message.video:
        video = update.message.video.file_id
        caption = update.message.caption or ""
        await context.bot.send_video(
            chat_id=user_chat_id,
            video=video,
            caption=f"Admin: {caption}"
        )

    await update.message.reply_text("შეტყობინება გაიგზავნა მომხმარებელთან!")
    del context.user_data['reply_ticket_id']

async def test_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'reply_ticket_id' in context.user_data:
        del context.user_data['reply_ticket_id']
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text="🔧 Admin test message.")
        logger.info(f"Successfully sent test message to ADMIN_ID ({ADMIN_ID}).")
    except Exception as e:
        logger.error(f"Failed to send test message to admin: {e}")

async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("Unauthorized command.")
        return

    if 'reply_ticket_id' in context.user_data:
        del context.user_data['reply_ticket_id']

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
    await context.bot.send_message(chat_id=user_chat_id, text=f"Admin: {reply_text}")
    await update.message.reply_text("Reply sent to the user.")

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(MessageHandler(
    (filters.TEXT | filters.PHOTO | filters.VOICE | filters.VIDEO) & (~filters.COMMAND),
    handle_user_message
))
bot_app.add_handler(CallbackQueryHandler(button_callback))
bot_app.add_handler(CommandHandler("reply", reply_command))
bot_app.add_handler(CommandHandler("testadmin", test_admin_message))
bot_app.add_handler(MessageHandler(
    filters.Chat(chat_id=ADMIN_ID) & (filters.TEXT | filters.PHOTO | filters.VOICE | filters.VIDEO) & (~filters.COMMAND),
    handle_admin_reply
), group=1)

@app.route('/')
def index():
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