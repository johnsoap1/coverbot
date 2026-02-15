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
user_timers = {}  # Track pending timers

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
        # Add media to collection
        media_groups[user_id].append(message)
        original_messages[user_id].append(message.id)
        
        count = len(media_groups[user_id])
        
        logging.info(f"User {user_id} sent media. Total count: {count}")
        
        # Cancel existing timer if any
        if user_id in user_timers:
            user_timers[user_id].cancel()
        
        # Check if max album size reached
        if count >= Config.MAX_ALBUM_SIZE:
            logging.info(f"User {user_id} reached max album size. Sending now.")
            if user_id in user_timers:
                del user_timers[user_id]
            await process_and_send(user_id, message.chat.id)
        else:
            # Create new timer - wait for more media
            timer = asyncio.create_task(wait_and_send(user_id, message.chat.id))
            user_timers[user_id] = timer

async def wait_and_send(user_id, chat_id):
    """Wait for more media, then send what we have"""
    try:
        # Wait 3 seconds for more media to arrive
        await asyncio.sleep(3.0)
        
        # After waiting, process and send
        async with user_locks[user_id]:
            if user_id in user_timers:
                del user_timers[user_id]
            await process_and_send(user_id, chat_id)
            
    except asyncio.CancelledError:
        # Timer was cancelled, do nothing
        pass

async def process_and_send(user_id, chat_id):
    """Process media collection and send as album or single"""
    medias = media_groups[user_id]
    
    if not medias:
        return
    
    count = len(medias)
    logging.info(f"Processing {count} media items for user {user_id}")
    
    # Forward to storage FIRST in parallel
    storage_tasks = [forward_to_storage(m) for m in medias]
    await asyncio.gather(*storage_tasks, return_exceptions=True)
    
    # Send based on count
    if count == 1:
        # Single media
        await send_single_media(chat_id, medias[0])
    else:
        # Multiple media - send as album
        await send_album(chat_id, medias)
    
    # Cleanup
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
        logging.info(f"Sent single media to chat {chat_id}")
    except Exception as e:
        logging.error(f"Error sending single media: {e}")

async def send_album(chat_id, medias):
    """Send multiple media as an album"""
    media_list = []
    
    for m in medias:
        if m.photo:
            media_list.append(InputMediaPhoto(m.photo.file_id))
        elif m.video:
            media_list.append(InputMediaVideo(m.video.file_id))
        elif m.document:
            media_list.append(InputMediaDocument(m.document.file_id))
    
    if media_list:
        await safe_send(bot.send_media_group, chat_id, media=media_list)
        logging.info(f"Sent album of {len(media_list)} items to chat {chat_id}")

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
    logging.info(f"Cleaned up data for user {user_id}")

# ------------------ Bot Start ------------------ #

if __name__ == "__main__":
    logging.info("Starting Anonymous Forward Bot...")
    logging.info(f"Max album size: {Config.MAX_ALBUM_SIZE}")
    logging.info(f"Storage group: {Config.STORAGE_GROUP_ID or 'Not configured'}")
    bot.run()
