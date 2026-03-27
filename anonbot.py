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

logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyrogram.client").setLevel(logging.WARNING)
logging.getLogger("pyrogram.session").setLevel(logging.WARNING)

# ------------------ Logging ------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)

# ------------------ Bot Init ------------------
bot = Client(
    "AnonForwardBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

# ------------------ State Storage ------------------
media_groups = defaultdict(list)
original_messages = defaultdict(list)
user_locks = defaultdict(asyncio.Lock)
last_send_time = defaultdict(float)
global_timestamps = []
processed_groups = set()  # NEW: Track processed media groups
start_time = time.time()

# ------------------ Rate Limiter ------------------
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
    logging.debug(f"safe_send to {chat_id}: {func.__name__}")
    while True:
        try:
            await rate_limit(chat_id)
            result = await func(chat_id=chat_id, **kwargs)
            now = time.time()
            last_send_time[chat_id] = now
            global_timestamps.append(now)
            logging.debug(f"safe_send success: {func.__name__}")
            return result
        except FloodWait as e:
            logging.warning(f"FloodWait {e.value}s")
            await asyncio.sleep(e.value)
        except RPCError as e:
            logging.error(f"RPCError in safe_send {func.__name__}: {e}")
            return None

# ------------------ Memory Leak Prevention ------------------
async def cleanup_stale_sessions():
    """Background task to clean abandoned sessions every 10 minutes"""
    while True:
        await asyncio.sleep(600)  # 10 minutes
        now = time.time()
        
        stale_users = []
        for user_id, last_time in list(last_send_time.items()):
            if now - last_time > 1800:  # 30 minutes idle
                stale_users.append(user_id)
        
        for user_id in stale_users:
            if user_id in media_groups:
                logging.info(f"Cleaning stale session for user {user_id}: {len(media_groups[user_id])} pending media")
            media_groups.pop(user_id, None)
            original_messages.pop(user_id, None)
            last_send_time.pop(user_id, None)
            # Clean up locks (they auto-recreate)
            if user_id in user_locks:
                del user_locks[user_id]

# ------------------ Core Handlers ------------------
@bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    logging.info(f"Start command from {message.from_user.id}")
    user_name = message.from_user.first_name
    bot_name = (await client.get_me()).first_name
    await message.reply_text(
        f"Hey {user_name}. \n\n"
        f"Welcome to {bot_name} \n\n"
        "I'm an anonymous forward bot designed to strip metadata from Telegram media for privacy. "
        "Send me media to get started."
    )

@bot.on_message(filters.private & (filters.photo | filters.video | filters.document) & ~filters.me)
async def handle_media(client, message):
    group_id = message.media_group_id
    user_id = message.from_user.id
    
    logging.info(f"=== MEDIA HANDLER START: user={user_id}, media={message.media}, group_id={group_id} ===")
    
    # Ignore bot's own messages
    if message.from_user and message.from_user.is_bot and message.from_user.id == (await client.get_me()).id:
        logging.info("Ignoring bot's own message")
        return
    
    # Add media to user's queue (lock only for list manipulation)
    async with user_locks[user_id]:
        media_groups[user_id].append(message)
        original_messages[user_id].append(message.id)
        count = len(media_groups[user_id])
        is_first = (count == 1)
        logging.info(f"User {user_id}: Added media, total count={count}")
    
    # CASE 1: True album (has media_group_id)
    if group_id:
        group_key = f"{user_id}_{group_id}"
        
        # Check if already being processed
        if group_key in processed_groups:
            logging.info(f"User {user_id}: Album {group_id} already being processed, skipping")
            return
        
        # Only first message of album processes it
        if is_first:
            processed_groups.add(group_key)
            logging.info(f"User {user_id}: FIRST of TRUE ALBUM {group_id}, sleeping 1s to collect all items")
            await asyncio.sleep(1.0)
            
            async with user_locks[user_id]:
                final_count = len(media_groups[user_id])
            
            logging.info(f"User {user_id}: True album complete with {final_count} items, processing...")
            await auto_send_album(user_id, message.chat.id)
            processed_groups.discard(group_key)
        
        logging.info(f"=== MEDIA HANDLER END user={user_id} ===")
        return
    
    # CASE 2: Individual files (no media_group_id) - use time-based batching
    if is_first:
        logging.info(f"User {user_id}: FIRST individual media, sleeping 2s to collect more")
        await asyncio.sleep(2.0)
        
        async with user_locks[user_id]:
            final_count = len(media_groups[user_id])
        
        logging.info(f"User {user_id}: After 2s wait, count={final_count}")
        
        # Check if max size reached
        if final_count >= Config.MAX_ALBUM_SIZE:
            logging.info(f"User {user_id}: MAX SIZE {Config.MAX_ALBUM_SIZE} hit, calling auto_send_album")
            await auto_send_album(user_id, message.chat.id)
            logging.info(f"=== MEDIA HANDLER END user={user_id} ===")
            return
        
        # Single after wait
        if final_count == 1:
            logging.info(f"User {user_id}: SINGLE after wait, send_single_silent")
            await send_single_silent(user_id, message.chat.id, media_groups[user_id][0])
            await cleanup(user_id, message.chat.id)
        else:
            # Multiple individual files collected - send as album
            logging.info(f"User {user_id}: MULTIPLE individual files ({final_count}), sleep 1s then auto_send_album")
            await asyncio.sleep(1.0)
            await auto_send_album(user_id, message.chat.id)
    
    logging.info(f"=== MEDIA HANDLER END user={user_id} ===")

# ------------------ Auto Album System ------------------
async def auto_send_album(user_id, chat_id):
    logging.info(f"=== AUTO_SEND_ALBUM: user={user_id}, chat={chat_id} ===")
    medias = media_groups[user_id]
    if not medias:
        logging.warning(f"User {user_id}: No medias in auto_send_album")
        return
    
    count = len(medias)
    logging.info(f"auto_send_album: {count} medias")
    
    # Single media edge case
    if count == 1:
        logging.info(f"auto_send_album: SINGLE, calling send_single_silent")
        await send_single_silent(user_id, chat_id, medias[0])
        await cleanup(user_id, chat_id)
        return
    
    # Forward entire album to storage as group
    if Config.STORAGE_GROUP_ID:
        logging.info(f"auto_send_album: Forwarding album of {count} to storage")
        try:
            message_ids = [m.id for m in medias]
            await safe_send(
                bot.forward_messages,
                Config.STORAGE_GROUP_ID,
                from_chat_id=chat_id,
                message_ids=message_ids
            )
        except Exception as e:
            logging.error(f"Storage album forward failed: {e}")
    
    # Build media list
    all_media = []
    for i, m in enumerate(medias):
        logging.debug(f"Building media_list[{i}]: {m.media}")
        if m.photo:
            all_media.append(InputMediaPhoto(m.photo.file_id))
        elif m.video:
            all_media.append(InputMediaVideo(m.video.file_id))
        elif m.document:
            all_media.append(InputMediaDocument(m.document.file_id))
    
    logging.info(f"media_list built: {len(all_media)} items")
    
    # Split into chunks of 10 (Telegram's limit)
    CHUNK_SIZE = 10
    total_chunks = (len(all_media) + CHUNK_SIZE - 1) // CHUNK_SIZE
    success_count = 0
    
    for i in range(0, len(all_media), CHUNK_SIZE):
        chunk = all_media[i:i+CHUNK_SIZE]
        chunk_num = (i // CHUNK_SIZE) + 1
        
        logging.info(f"Sending chunk {chunk_num}/{total_chunks} with {len(chunk)} items")
        result = await safe_send(bot.send_media_group, chat_id, media=chunk)
        
        if result:
            success_count += 1
            logging.info(f"Chunk {chunk_num}/{total_chunks} sent successfully")
        else:
            logging.error(f"Failed to send chunk {chunk_num}/{total_chunks}")
            await bot.send_message(chat_id, "⚠️ Some media failed to send. Please try again.")
            logging.info(f"=== AUTO_SEND_ALBUM END (FAILED) user={user_id} ===")
            return  # Don't cleanup on failure
        
        # Small delay between chunks to avoid flood
        if i + CHUNK_SIZE < len(all_media):
            await asyncio.sleep(0.5)
    
    # Only cleanup if all chunks sent successfully
    if success_count == total_chunks:
        logging.info(f"All {total_chunks} chunks sent successfully, cleaning up")
        await cleanup(user_id, chat_id)
    else:
        logging.warning(f"Only {success_count}/{total_chunks} chunks succeeded, skipping cleanup")
    
    logging.info(f"=== AUTO_SEND_ALBUM END user={user_id} ===")

async def send_single_silent(user_id, chat_id, media):
    logging.info(f"=== SINGLE SEND: user={user_id}, media={media.media} ===")
    
    # Forward to storage first
    if Config.STORAGE_GROUP_ID:
        try:
            await safe_send(
                bot.forward_messages,
                Config.STORAGE_GROUP_ID,
                from_chat_id=chat_id,
                message_ids=media.id
            )
        except Exception as e:
            logging.error(f"Storage forward failed: {e}")
    
    # Send the media
    result = None
    try:
        if media.photo:
            logging.info("Sending photo")
            result = await safe_send(bot.send_photo, chat_id, photo=media.photo.file_id)
        elif media.video:
            logging.info("Sending video")
            result = await safe_send(bot.send_video, chat_id, video=media.video.file_id)
        elif media.document:
            logging.info("Sending document")
            result = await safe_send(bot.send_document, chat_id, document=media.document.file_id)
        elif media.audio:
            logging.info("Sending audio")
            result = await safe_send(bot.send_audio, chat_id, audio=media.audio.file_id)
    except Exception as e:
        logging.error(f"Error sending single media: {e}")
    
    if not result:
        logging.error("Single send failed, skipping cleanup")
        await bot.send_message(chat_id, "⚠️ Failed to send media. Please try again.")
    
    logging.info(f"=== SINGLE SEND END ===")

# ------------------ Storage & Cleanup ------------------
async def cleanup(user_id, chat_id):
    logging.debug(f"cleanup user {user_id}: {len(original_messages[user_id])} msgs")
    try:
        for msg_id in original_messages[user_id][:]:
            try:
                await bot.delete_messages(chat_id, msg_id)
                await asyncio.sleep(0.05)
            except Exception as e:
                logging.warning(f"Could not delete message {msg_id}: {e}")
        
        media_groups[user_id].clear()
        original_messages[user_id].clear()
        logging.info(f"Cleaned up user {user_id}")
    except Exception as e:
        logging.error(f"Cleanup failed for {user_id}: {e}")

# ------------------ Bot Start ------------------
if __name__ == "__main__":
    logging.info("Starting Anonymous Forward Bot...")
    logging.info(f"Max album size: {Config.MAX_ALBUM_SIZE}")
    
    # Start background cleanup task
    bot.loop.create_task(cleanup_stale_sessions())
    logging.info("Background cleanup task started (runs every 10 minutes)")
    
    bot.run()
