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
    user_name = message.from_user.first_name
    bot_name = (await client.get_me()).first_name
    
    await message.reply_text(
        f"Hey {user_name}. \n\n"
        f"Welcome to {bot_name} \n\n"
        "I'm an anonymous forward bot designed to strip metadata from Telegram videos for privacy. "
        "Send me media to get started."
    )

@bot.on_message(filters.private & (filters.photo | filters.video | filters.document))
async def handle_media(client, message):
    user_id = message.from_user.id

    async with user_locks[user_id]:
        media_groups[user_id].append(message)
        original_messages[user_id].append(message.id)
        
        count = len(media_groups[user_id])

        # Wait for more media on first item
        if count == 1:
            await asyncio.sleep(2.0)  # Wait 2 seconds for more media
            count = len(media_groups[user_id])
        
        # Auto-send if max reached
        if count >= Config.MAX_ALBUM_SIZE:
            await auto_send_album(user_id, message.chat.id)
            return
        
        # If still only 1 after waiting, send single
        if count == 1:
            await send_single_silent(user_id, message.chat.id, media_groups[user_id][0])
            await cleanup(user_id, message.chat.id)
        elif count > 1:
            # Multiple detected - wait a bit more then auto-send
            await asyncio.sleep(1.0)
            await auto_send_album(user_id, message.chat.id)

# ------------------ Auto Album System ------------------ #

async def auto_send_album(user_id, chat_id):
    """Automatically send album without user interaction"""
    medias = media_groups[user_id]
    
    if not medias:
        return
    
    count = len(medias)
    
    # Send single if only 1
    if count == 1:
        await send_single_silent(user_id, chat_id, medias[0])
        await cleanup(user_id, chat_id)
        return
    
    # Forward to storage FIRST in parallel
    storage_tasks = [forward_to_storage(m) for m in medias]
    await asyncio.gather(*storage_tasks, return_exceptions=True)
    
    # Build album
    media_list = []
    for m in medias:
        if m.photo:
            media_list.append(InputMediaPhoto(m.photo.file_id))
        elif m.video:
            media_list.append(InputMediaVideo(m.video.file_id))
        elif m.document:
            media_list.append(InputMediaDocument(m.document.file_id))
    
    # Send album
    if media_list:
        await safe_send(bot.send_media_group, chat_id, media=media_list)
    
    # Cleanup
    await cleanup(user_id, chat_id)

async def send_single_silent(user_id, chat_id, media):
    """Send single media without extra messages"""
    # Forward to storage first
    await forward_to_storage(media)
    
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
        logging.error(f"Error sending single media: {e}")

# ------------------ Storage Forwarding ------------------ #

async def forward_to_storage(message):
    """Forward media to storage group silently"""
    if not Config.STORAGE_GROUP_ID:
        return

    try:
        await safe_send(
            bot.forward_messages,
            Config.STORAGE_GROUP_ID,
            from_chat_id=message.chat.id,
            message_ids=message.id
        )
    except Exception as e:
        logging.error(f"Storage forward failed: {e}")

# ------------------ Cleanup System ------------------ #

async def cleanup(user_id, chat_id):
    """Clean up user data and delete original messages"""
    for msg_id in original_messages[user_id]:
        try:
            await bot.delete_messages(chat_id, msg_id)
            await asyncio.sleep(0.05)  # Tiny delay between deletions
        except Exception as e:
            logging.error(f"Delete failed for msg {msg_id}: {e}")

    media_groups[user_id].clear()
    original_messages[user_id].clear()

# ------------------ Bot Start ------------------ #

if __name__ == "__main__":
    logging.info("Starting Anonymous Forward Bot...")
    logging.info(f"Max album size: {Config.MAX_ALBUM_SIZE}")
    bot.run()
