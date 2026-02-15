import asyncio
import time
import logging
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
)

from config import Config

# ------------------ Logging ------------------ #

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)

# ------------------ Bot Init ------------------ #

bot = Client(
    "AnonForwardBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

# ------------------ State Storage ------------------ #

media_groups = defaultdict(list)
original_messages = defaultdict(list)
user_locks = defaultdict(asyncio.Lock)

last_send_time = defaultdict(float)
global_timestamps = []

# ------------------ Rate Limiter ------------------ #

async def rate_limit(chat_id):
    global global_timestamps
    now = time.time()

    global_timestamps = [t for t in global_timestamps if now - t < 1]

    if len(global_timestamps) >= Config.RATE_LIMIT_GLOBAL:
        await asyncio.sleep(1)

    delta = now - last_send_time[chat_id]
    if delta < Config.RATE_LIMIT_PER_CHAT:
        await asyncio.sleep(Config.RATE_LIMIT_PER_CHAT - delta)

async def safe_send(func, chat_id, **kwargs):
    while True:
        try:
            await rate_limit(chat_id)
            result = await func(chat_id=chat_id, **kwargs)

            now = time.time()
            last_send_time[chat_id] = now
            global_timestamps.append(now)

            return result

        except FloodWait as e:
            logging.warning(f"FloodWait {e.value}s")
            await asyncio.sleep(e.value)

        except RPCError as e:
            logging.error(f"RPCError: {e}")
            return None

# ------------------ Core Handlers ------------------ #

@bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    await message.reply_text(
        "**🕶️ Anonymous Forward Bot**\n\n"
        "Send me any media and I'll make it anonymous:\n"
        "✓ Remove your name\n"
        "✓ Remove captions\n"
        "✓ Delete your original message\n"
        "✓ Store in backup group (if configured)\n\n"
        "Send multiple media quickly to create an album!"
    )

@bot.on_message(filters.private & (filters.photo | filters.video | filters.document))
async def handle_media(client, message):
    user_id = message.from_user.id

    async with user_locks[user_id]:
        media_groups[user_id].append(message)
        original_messages[user_id].append(message.id)

        if len(media_groups[user_id]) == 1:
            await asyncio.sleep(1.2)

        if len(media_groups[user_id]) == 1:
            await send_single(user_id, message)
        else:
            await show_album_menu(message, user_id)

# ------------------ Single Media Handler ------------------ #

async def send_single(user_id, message):
    """Send a single media item"""
    media = message
    
    # Forward to storage first
    await forward_to_storage(media)
    
    # Send to user
    if media.photo:
        await safe_send(
            bot.send_photo,
            message.chat.id,
            photo=media.photo.file_id,
            caption="✅ **Anonymous Media Ready**\nForward this to any chat - no attribution!"
        )
    elif media.video:
        await safe_send(
            bot.send_video,
            message.chat.id,
            video=media.video.file_id,
            caption="✅ **Anonymous Video Ready**\nForward this to any chat - no attribution!"
        )
    elif media.document:
        await safe_send(
            bot.send_document,
            message.chat.id,
            document=media.document.file_id,
            caption="✅ **Anonymous Document Ready**\nForward this to any chat - no attribution!"
        )
    elif media.audio:
        await safe_send(
            bot.send_audio,
            message.chat.id,
            audio=media.audio.file_id,
            caption="✅ **Anonymous Audio Ready**\nForward this to any chat - no attribution!"
        )
    
    # Cleanup
    await cleanup(user_id, message.chat.id)

# ------------------ Album System ------------------ #

async def show_album_menu(message, user_id):
    """Show album creation menu"""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Create Album", callback_data=f"create_album_{user_id}"),
            InlineKeyboardButton("📤 Send Individually", callback_data=f"send_individual_{user_id}")
        ],
        [
            InlineKeyboardButton("🗑️ Clear All", callback_data=f"clear_all_{user_id}")
        ]
    ])
    
    await message.reply_text(
        f"📦 **Album Mode** ({len(media_groups[user_id])} items)\n\n"
        "Choose how to send these media files:",
        reply_markup=keyboard
    )

@bot.on_callback_query()
async def handle_callback(client, callback_query):
    user_id = callback_query.from_user.id
    action = callback_query.data.split("_", 2)[0]
    
    if action == "create":
        await create_album(user_id, callback_query.message.chat.id)
        await callback_query.message.edit_text("✅ **Album Created!**\n\nForward these to any chat - completely anonymous!")
        
    elif action == "send":
        await send_individual(user_id, callback_query)
        
    elif action == "clear":
        await cleanup(user_id, callback_query.message.chat.id)
        await callback_query.message.edit_text("🗑️ **Cleared all media**")

async def create_album(user_id, chat_id):
    """Create and send album"""
    medias = media_groups[user_id]
    if not medias:
        return

    media_list = []

    for i, m in enumerate(medias):
        # Forward to storage first
        await forward_to_storage(m)
        
        caption = "📚 Anonymous Album" if i == 0 else ""

        if m.photo:
            media_list.append(InputMediaPhoto(m.photo.file_id, caption=caption))
        elif m.video:
            media_list.append(InputMediaVideo(m.video.file_id, caption=caption))
        elif m.document:
            media_list.append(InputMediaDocument(m.document.file_id, caption=caption))

    await safe_send(bot.send_media_group, chat_id, media=media_list)
    await cleanup(user_id, chat_id)

async def send_individual(user_id, callback_query):
    """Send media items individually"""
    sent_count = 0
    
    for msg in media_groups[user_id]:
        # Forward to storage first
        await forward_to_storage(msg)
        
        if msg.photo:
            await safe_send(
                bot.send_photo,
                callback_query.message.chat.id,
                photo=msg.photo.file_id
            )
        elif msg.video:
            await safe_send(
                bot.send_video,
                callback_query.message.chat.id,
                video=msg.video.file_id
            )
        elif msg.document:
            await safe_send(
                bot.send_document,
                callback_query.message.chat.id,
                document=msg.document.file_id
            )
        elif msg.audio:
            await safe_send(
                bot.send_audio,
                callback_query.message.chat.id,
                audio=msg.audio.file_id
            )
        
        sent_count += 1
    
    await callback_query.message.edit_text(
        f"✅ **Sent {sent_count} items individually!**\n\n"
        "Forward these to any chat - completely anonymous!"
    )
    
    await cleanup(user_id, callback_query.message.chat.id)

# ------------------ Storage Forwarding ------------------ #

async def forward_to_storage(message):
    """Forward media to storage group if configured"""
    if not Config.STORAGE_GROUP_ID:
        return

    await safe_send(
        bot.forward_messages,
        Config.STORAGE_GROUP_ID,
        from_chat_id=message.chat.id,
        message_ids=message.id
    )

# ------------------ Cleanup System ------------------ #

async def cleanup(user_id, chat_id):
    """Clean up user data and delete original messages"""
    for msg_id in original_messages[user_id]:
        try:
            await bot.delete_messages(chat_id, msg_id)
        except:
            pass

    media_groups[user_id].clear()
    original_messages[user_id].clear()

# ------------------ Bot Start ------------------ #

if __name__ == "__main__":
    logging.info("Starting Anonymous Forward Bot...")
    bot.run()
