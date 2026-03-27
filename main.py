import asyncio
import time
import logging
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import (
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
)

from Config import Config

# ------------------ Logging ------------------ #

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)

# ------------------ Bot Init ------------------ #

bot = Client(
    "AnonForwardBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

# Bot ID - will be set on startup
BOT_ID = None

# ------------------ State Storage ------------------ #

media_groups = defaultdict(list)
original_messages = defaultdict(list)
user_locks = defaultdict(asyncio.Lock)
user_send_tasks = {}

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

# ------------------ Startup ------------------ #

@bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    global BOT_ID
    if BOT_ID is None:
        BOT_ID = (await client.get_me()).id
        logging.info(f"🤖 Bot ID set to: {BOT_ID}")
    
    user_name = message.from_user.first_name
    bot_name = (await client.get_me()).first_name
    
    await message.reply_text(
        f"Hey {user_name}. \n\n"
        f"Welcome to {bot_name} \n\n"
        "I'm an anonymous forward bot designed to strip metadata from Telegram videos for privacy. "
        "Send me media to get started."
    )

# ------------------ Media Handler ------------------ #

@bot.on_message(filters.private & (filters.photo | filters.video | filters.document | filters.audio))
async def handle_media(client, message):
    global BOT_ID
    if BOT_ID is None:
        BOT_ID = (await client.get_me()).id
        logging.info(f"🤖 Bot ID set to: {BOT_ID}")
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Ignore messages from the bot itself
    if user_id == BOT_ID:
        logging.debug(f"🤖 Ignoring message from bot itself")
        return
    
    logging.info(f"📥 Received media from user {user_id}")
    
    media_groups[user_id].append(message)
    original_messages[user_id].append(message.id)
    
    count = len(media_groups[user_id])
    logging.info(f"📊 User {user_id} now has {count} media items queued")
    
    if user_id in user_send_tasks:
        logging.info(f"⏰ Cancelling previous timer for user {user_id}")
        user_send_tasks[user_id].cancel()
    
    if count >= Config.MAX_ALBUM_SIZE:
        logging.info(f"🚀 Max size reached for user {user_id}, sending immediately")
        await send_user_media(user_id, chat_id)
    else:
        logging.info(f"⏰ Scheduling send in 3 seconds for user {user_id}")
        task = asyncio.create_task(delayed_send(user_id, chat_id, 3.0))
        user_send_tasks[user_id] = task

async def delayed_send(user_id, chat_id, delay):
    """Wait and then send media"""
    try:
        logging.info(f"⏳ Timer started for user {user_id} - waiting {delay}s")
        await asyncio.sleep(delay)
        logging.info(f"✅ Timer expired for user {user_id} - sending now")
        await send_user_media(user_id, chat_id)
    except asyncio.CancelledError:
        logging.info(f"❌ Timer cancelled for user {user_id}")
        pass

async def send_user_media(user_id, chat_id):
    """Send all queued media for a user"""
    if user_id in user_send_tasks:
        del user_send_tasks[user_id]
    
    medias = media_groups[user_id].copy()
    
    if not medias:
        logging.warning(f"⚠️ No media to send for user {user_id}")
        return
    
    count = len(medias)
    logging.info(f"📤 Sending {count} media items for user {user_id}")
    
    try:
        # Forward to storage first (ONLY user messages)
        if Config.STORAGE_GROUP_ID:
            logging.info(f"💾 Forwarding {count} items to storage...")
            storage_tasks = [forward_to_storage(m) for m in medias]
            await asyncio.gather(*storage_tasks, return_exceptions=True)
            logging.info(f"✅ Storage forwarding complete")
        
        # Send to user
        if count == 1:
            logging.info(f"📨 Sending single media")
            await send_single_media(chat_id, medias[0])
        else:
            logging.info(f"📚 Sending album with {count} items")
            await send_album(chat_id, medias)
        
        # Delete originals
        logging.info(f"🗑️ Deleting {len(original_messages[user_id])} original messages")
        await cleanup(user_id, chat_id)
        
        logging.info(f"✅ Complete! Sent {count} items to user {user_id}")
        
    except Exception as e:
        logging.error(f"❌ Error in send_user_media: {e}", exc_info=True)
        await cleanup(user_id, chat_id)

# ------------------ Send Functions ------------------ #

async def send_single_media(chat_id, media):
    """Send a single media item"""
    try:
        if media.photo:
            await safe_send(bot.send_photo, chat_id, photo=media.photo.file_id)
        elif media.video:
            await safe_send(bot.send_video, chat_id, video=media.video.file_id)
        elif media.document:
            await safe_send(bot.send_document, chat_id, document=media.document.file_id)
        elif media.audio:
            await safe_send(bot.send_audio, chat_id, audio=media.audio.file_id)
    except Exception as e:
        logging.error(f"❌ Error sending single media: {e}")
        raise

async def send_album(chat_id, medias):
    """Send multiple media as an album"""
    media_list = []
    
    for m in medias:
        try:
            if m.photo:
                media_list.append(InputMediaPhoto(m.photo.file_id))
            elif m.video:
                media_list.append(InputMediaVideo(m.video.file_id))
            elif m.document:
                media_list.append(InputMediaDocument(m.document.file_id))
            elif m.audio:
                logging.warning(f"⚠️ Skipping audio in album")
                continue
        except Exception as e:
            logging.error(f"❌ Error adding media to album: {e}")
    
    if not media_list:
        logging.error(f"❌ No valid media in album!")
        return
    
    logging.info(f"📚 Sending album with {len(media_list)} items")
    await safe_send(bot.send_media_group, chat_id, media=media_list)

# ------------------ Storage Forwarding ------------------ #

async def forward_to_storage(message):
    """Forward media to storage group - ONLY from users, not bot"""
    if not Config.STORAGE_GROUP_ID:
        return
    
    # Only forward original user messages, not bot's re-sent messages
    if message.from_user and message.from_user.id != BOT_ID:
        try:
            await safe_send(
                bot.forward_messages,
                Config.STORAGE_GROUP_ID,
                from_chat_id=message.chat.id,
                message_ids=message.id
            )
            logging.info(f"💾 Forwarded msg {message.id} to storage")
        except Exception as e:
            logging.error(f"❌ Storage forward failed: {e}")
    else:
        logging.debug(f"⏭️ Skipping storage forward (message from bot)")

# ------------------ Cleanup System ------------------ #

async def cleanup(user_id, chat_id):
    """Clean up user data and delete original messages"""
    msg_ids = original_messages[user_id].copy()
    
    for msg_id in msg_ids:
        try:
            await bot.delete_messages(chat_id, msg_id)
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.debug(f"Delete failed for msg {msg_id}: {e}")

    media_groups[user_id].clear()
    original_messages[user_id].clear()

# ------------------ Bot Start ------------------ #

if __name__ == "__main__":
    logging.info("=" * 50)
    logging.info("🚀 Starting Anonymous Forward Bot")
    logging.info("=" * 50)
    logging.info(f"📊 Max album size: {Config.MAX_ALBUM_SIZE}")
    logging.info(f"💾 Storage group: {Config.STORAGE_GROUP_ID or 'Not configured'}")
    logging.info(f"⚡ Rate limits: {Config.RATE_LIMIT_GLOBAL} global, {Config.RATE_LIMIT_PER_CHAT} per chat")
    logging.info("=" * 50)
    bot.run()
