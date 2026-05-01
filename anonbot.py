import asyncio
import time
import logging
import logging.handlers
import json
import os
import signal
import traceback
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import (
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
)

from Config import Config

# ── Log channel (set in Config) ──────────────────────────────
# Config.LOG_CHANNEL_ID = -100xxxxxxxxxx  (your log group/channel)

LOG_FORMAT = "[%(asctime)s] [%(levelname)-8s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

os.makedirs("logs", exist_ok=True)

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Console — INFO+
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

# Rolling file — DEBUG+ (7 day retention)
file_handler = logging.handlers.TimedRotatingFileHandler(
    "logs/bot.log", when="midnight", backupCount=7, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

# Errors-only file
error_handler = logging.handlers.RotatingFileHandler(
    "logs/errors.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
error_handler.setLevel(logging.WARNING)
error_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)
root_logger.addHandler(error_handler)

# Silence pyrogram's own noise
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyrogram.client").setLevel(logging.WARNING)
logging.getLogger("pyrogram.session").setLevel(logging.WARNING)
logging.getLogger("pyrogram.connection").setLevel(logging.WARNING)

log = logging.getLogger("AnonBot")

# ------------------ Bot Init ------------------
bot = Client(
    "AnonForwardBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

# ── Telegram log channel handler ────────────────────────────

class TelegramLogHandler(logging.Handler):
    """Sends WARNING+ logs to a Telegram channel/group asynchronously."""

    ICONS = {
        logging.DEBUG:    "🔍",
        logging.INFO:     "ℹ️",
        logging.WARNING:  "⚠️",
        logging.ERROR:    "❌",
        logging.CRITICAL: "🚨",
    }

    def __init__(self, bot_client, channel_id):
        super().__init__(level=logging.WARNING)
        self.bot_client = bot_client
        self.channel_id = channel_id
        self._queue = asyncio.Queue()
        self._task = None

    def start(self):
        """Call once the event loop is running."""
        self._task = asyncio.create_task(self._worker())

    def emit(self, record):
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            pass  # Drop if queue is full — never block the bot

    async def _worker(self):
        while True:
            record = await self._queue.get()
            try:
                icon = self.ICONS.get(record.levelno, "📋")
                level = record.levelname
                msg = self.format(record)

                # Append traceback for exceptions
                if record.exc_info:
                    tb = "".join(traceback.format_exception(*record.exc_info))
                    text = f"{icon} <b>[{level}]</b>\n<code>{msg}</code>\n\n<pre>{tb[:1500]}</pre>"
                else:
                    text = f"{icon} <b>[{level}]</b>\n<code>{msg}</code>"

                await self.bot_client.send_message(
                    self.channel_id,
                    text,
                    parse_mode="html",
                    disable_notification=(record.levelno < logging.ERROR)
                )
            except Exception:
                pass  # Never let logging crash the bot
            finally:
                self._queue.task_done()
                await asyncio.sleep(0.5)  # Respect Telegram rate limits on log channel

tg_log_handler = None
if Config.LOG_CHANNEL_ID:
    tg_log_handler = TelegramLogHandler(bot, Config.LOG_CHANNEL_ID)
    tg_log_handler.setFormatter(logging.Formatter("%(name)s | %(message)s"))
    root_logger.addHandler(tg_log_handler)

# ------------------ State Storage ------------------
media_groups = defaultdict(list)
original_messages = defaultdict(list)
user_locks = defaultdict(asyncio.Lock)
last_send_time = defaultdict(float)
global_timestamps = []
processed_groups = set()  # NEW: Track processed media groups
start_time = time.time()

# ------------------ Ignore List ------------------
IGNORE_FILE = "ignored_users.json"

def load_ignored_users():
    if os.path.exists(IGNORE_FILE):
        try:
            with open(IGNORE_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_ignored_users():
    try:
        with open(IGNORE_FILE, "w") as f:
            json.dump(list(ignored_users), f)
    except Exception as e:
        logging.error(f"Failed to save ignored_users: {e}")

ignored_users = load_ignored_users()  # user_ids that won't be forwarded to storage

class InvalidFileError(Exception):
    """Raised when a file_id is invalid and shouldn't be retried."""
    pass

# ------------------ Rate Limiter ------------------
async def rate_limit(chat_id):
    global global_timestamps
    now = time.time()
    global_timestamps = [t for t in global_timestamps if now - t < 1][-500:]
    if len(global_timestamps) >= Config.RATE_LIMIT_GLOBAL:
        await asyncio.sleep(1)
    delta = now - last_send_time[chat_id]
    if delta < Config.RATE_LIMIT_PER_CHAT:
        await asyncio.sleep(Config.RATE_LIMIT_PER_CHAT - delta)

async def safe_send(func, chat_id, **kwargs):
    attempt = 0
    while True:
        attempt += 1
        log.debug(f"safe_send | func={func.__name__} chat={chat_id} attempt={attempt} kwargs_keys={list(kwargs.keys())}")
        try:
            await rate_limit(chat_id)
            result = await func(chat_id=chat_id, **kwargs)
            now = time.time()
            last_send_time[chat_id] = now
            global_timestamps.append(now)
            log.debug(f"safe_send OK | func={func.__name__} chat={chat_id}")
            return result

        except FloodWait as e:
            log.warning(f"FloodWait | func={func.__name__} chat={chat_id} wait={e.value}s attempt={attempt}")
            await asyncio.sleep(e.value)

        except RPCError as e:
            if e.CODE == 400 and "FILE_ID_INVALID" in str(e).upper():
                log.warning(f"InvalidFileID | func={func.__name__} chat={chat_id} error={e}")
                raise InvalidFileError(str(e))
            log.error(f"RPCError | func={func.__name__} chat={chat_id} code={e.CODE} error={e}")
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
                log.info(f"Cleaning stale session for user {user_id}: {len(media_groups[user_id])} pending media")
            media_groups.pop(user_id, None)
            original_messages.pop(user_id, None)
            last_send_time.pop(user_id, None)
            
            if user_id in user_locks:
                lock = user_locks[user_id]
                if lock.locked():
                    log.warning(f"Stale lock still held for user {user_id} — skipping lock removal to avoid corruption")
                else:
                    del user_locks[user_id]

# ------------------ Core Handlers ------------------
@bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    global tg_log_handler
    if tg_log_handler and not tg_log_handler._task:
        tg_log_handler.start()
        log.info("Telegram log handler started")

    user_name = message.from_user.first_name
    bot_name = (await client.get_me()).first_name
    log.info(f"START command | user={message.from_user.id} name={user_name!r}")

    await message.reply_text(
        f"Hey {user_name}. \n\n"
        f"Welcome to {bot_name} \n\n"
        "I'm an anonymous forward bot designed to strip metadata from Telegram media for privacy. "
        "Send me media to get started."
    )

@bot.on_message(filters.command("ignore") & filters.reply)
async def ignore_user_command(client, message):
    """Allow storage group members to ignore a user from storage forwarding."""
    
    # Only allow from storage group
    if not Config.STORAGE_GROUP_ID or message.chat.id != Config.STORAGE_GROUP_ID:
        return
    
    replied = message.reply_to_message
    if not replied:
        await message.reply_text("❌ Reply to a forwarded message to ignore that user.")
        return
    
    # Get the original sender's ID from the forwarded message
    target_user_id = None
    target_name = "Unknown"
    
    if replied.forward_from:
        target_user_id = replied.forward_from.id
        target_name = replied.forward_from.first_name or str(target_user_id)
    elif replied.forward_sender_name:
        # User has hidden their account - can't get ID
        await message.reply_text(
            "⚠️ This user has hidden their account. Cannot retrieve their ID to ignore them."
        )
        return
    else:
        await message.reply_text("❌ Could not identify the original sender of this message.")
        return
    
    if target_user_id in ignored_users:
        await message.reply_text(f"ℹ️ User **{target_name}** (`{target_user_id}`) is already ignored.")
        return
    
    ignored_users.add(target_user_id)
    log.info(f"User {target_user_id} ({target_name}) added to ignore list by {message.from_user.id}")
    save_ignored_users()
    
    # Delete all messages in storage group from this user
    deleted_count = 0
    async for msg in client.search_messages(Config.STORAGE_GROUP_ID, from_user=target_user_id):
        try:
            await client.delete_messages(Config.STORAGE_GROUP_ID, msg.id)
            deleted_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            log.warning(f"Could not delete storage msg {msg.id}: {e}")
    
    await message.reply_text(
        f"✅ **{target_name}** (`{target_user_id}`) is now ignored.\n"
        f"🗑️ Deleted **{deleted_count}** of their messages from storage.\n"
        f"Their media will no longer be forwarded here."
    )


@bot.on_message(filters.command("unignore") & filters.reply)
async def unignore_user_command(client, message):
    """Remove a user from the ignore list."""
    
    if not Config.STORAGE_GROUP_ID or message.chat.id != Config.STORAGE_GROUP_ID:
        return
    
    replied = message.reply_to_message
    if not replied or not replied.forward_from:
        await message.reply_text("❌ Reply to a forwarded message to unignore that user.")
        return
    
    target_user_id = replied.forward_from.id
    target_name = replied.forward_from.first_name or str(target_user_id)
    
    if target_user_id not in ignored_users:
        await message.reply_text(f"ℹ️ User **{target_name}** (`{target_user_id}`) is not ignored.")
        return
    
    ignored_users.discard(target_user_id)
    log.info(f"User {target_user_id} ({target_name}) removed from ignore list by {message.from_user.id}")
    save_ignored_users()
    
    await message.reply_text(f"✅ **{target_name}** (`{target_user_id}`) has been unignored. Their media will now be forwarded to storage again.")

@bot.on_message(filters.command("ignored"))
async def list_ignored(client, message):
    if not Config.STORAGE_GROUP_ID or message.chat.id != Config.STORAGE_GROUP_ID:
        return

    if not ignored_users:
        await message.reply_text("✅ No users are currently ignored.")
        return

    lines = [f"• `{uid}`" for uid in sorted(ignored_users)]
    await message.reply_text("🚫 **Ignored users:**\n" + "\n".join(lines))

@bot.on_message(filters.private & (filters.photo | filters.video | filters.document) & ~filters.me)
async def handle_media(client, message):
    group_id = message.media_group_id
    user_id = message.from_user.id
    media_type = str(message.media).split(".")[-1] if message.media else "unknown"

    log.info(f"MEDIA_IN | user={user_id} msg={message.id} type={media_type} group={group_id or 'none'}")

    # Ignore bot's own messages
    if message.from_user and message.from_user.is_bot and message.from_user.id == (await client.get_me()).id:
        log.info("Ignoring bot's own message")
        return

    # Add media to user's queue (lock only for list manipulation)
    async with user_locks[user_id]:
        media_groups[user_id].append(message)
        original_messages[user_id].append(message.id)
        count = len(media_groups[user_id])
        is_first = (count == 1)
        log.debug(f"QUEUE | user={user_id} queued={count} msg={message.id}")
    
    # CASE 1: True album (has media_group_id)
    if group_id:
        group_key = f"{user_id}_{group_id}"

        # Check if already being processed
        if group_key in processed_groups:
            log.info(f"User {user_id}: Album {group_id} already being processed, skipping")
            return

        # Only first message of album processes it
        if is_first:
            processed_groups.add(group_key)
            log.info(f"User {user_id}: FIRST of TRUE ALBUM {group_id}, sleeping 1s to collect all items")
            await asyncio.sleep(1.0)

            async with user_locks[user_id]:
                final_count = len(media_groups[user_id])

            log.info(f"User {user_id}: True album complete with {final_count} items, processing...")
            await auto_send_album(user_id, message.chat.id)
            processed_groups.discard(group_key)

        log.info(f"=== MEDIA HANDLER END user={user_id} ===")
        return

    # CASE 2: Individual files (no media_group_id) - use time-based batching
    if is_first:
        log.info(f"User {user_id}: FIRST individual media, sleeping 2s to collect more")
        await asyncio.sleep(2.0)

        async with user_locks[user_id]:
            final_count = len(media_groups[user_id])

        log.info(f"User {user_id}: After 2s wait, count={final_count}")

        # Check if max size reached
        if final_count >= Config.MAX_ALBUM_SIZE:
            log.info(f"User {user_id}: MAX SIZE {Config.MAX_ALBUM_SIZE} hit, calling auto_send_album")
            await auto_send_album(user_id, message.chat.id)
            log.info(f"=== MEDIA HANDLER END user={user_id} ===")
            return

        # Single after wait
        if final_count == 1:
            log.info(f"User {user_id}: SINGLE after wait, send_single_silent")
            await send_single_silent(user_id, message.chat.id, media_groups[user_id][0])
            await cleanup(user_id, message.chat.id)
        else:
            # Multiple individual files collected - send as album
            log.info(f"User {user_id}: MULTIPLE individual files ({final_count}), sleep 1s then auto_send_album")
            await asyncio.sleep(1.0)
            await auto_send_album(user_id, message.chat.id)

    log.info(f"=== MEDIA HANDLER END user={user_id} ===")

# ------------------ Auto Album System ------------------
async def auto_send_album(user_id, chat_id):
    log.info(f"=== AUTO_SEND_ALBUM: user={user_id} count={len(media_groups[user_id])} ===")
    medias = list(media_groups[user_id])

    if not medias:
        return

    if len(medias) == 1:
        await send_single_silent(user_id, chat_id, medias[0])
        return

    # Forward all to storage first
    for m in medias:
        await forward_to_storage(m)

    # Track outcomes per message
    sent_ids = []      # originals to delete
    flagged_ids = []   # originals to leave in DM

    # Build album list, pairing each InputMedia back to its source message
    paired = []
    for m in medias:
        if m.photo:
            paired.append((m, InputMediaPhoto(m.photo.file_id)))
        elif m.video:
            paired.append((m, InputMediaVideo(m.video.file_id)))
        elif m.document:
            paired.append((m, InputMediaDocument(m.document.file_id)))
        elif m.audio:
            # Audio can't go in albums — send individually
            ok = await send_single_by_media(chat_id, m)
            (sent_ids if ok else flagged_ids).append(m.id)
            await asyncio.sleep(0.2)

    # Send in chunks of 10
    CHUNK_SIZE = 10
    for i in range(0, len(paired), CHUNK_SIZE):
        chunk = paired[i:i + CHUNK_SIZE]
        chunk_msgs = [item[0] for item in chunk]
        chunk_media = [item[1] for item in chunk]

        try:
            result = await safe_send(bot.send_media_group, chat_id, media=chunk_media)
            if result:
                sent_ids.extend(m.id for m in chunk_msgs)
            else:
                # send_media_group returned None (non-flood RPC error) — try one by one
                for m, _ in chunk:
                    ok = await send_single_by_media(chat_id, m)
                    (sent_ids if ok else flagged_ids).append(m.id)
                    await asyncio.sleep(0.2)
        except InvalidFileError:
            # One bad file poisoned the chunk — fall back to per-item
            log.warning(f"Invalid file in chunk {i//CHUNK_SIZE + 1}, retrying per-item")
            for m, _ in chunk:
                ok = await send_single_by_media(chat_id, m)
                (sent_ids if ok else flagged_ids).append(m.id)
                await asyncio.sleep(0.2)

        if i + CHUNK_SIZE < len(paired):
            await asyncio.sleep(0.5)

    # Delete only what was successfully sent
    for msg_id in sent_ids:
        try:
            await bot.delete_messages(chat_id, msg_id)
            await asyncio.sleep(0.05)
        except Exception as e:
            log.warning(f"Could not delete msg {msg_id}: {e}")

    # One consolidated warning if anything was flagged
    if flagged_ids:
        await bot.send_message(
            chat_id,
            f"⚠️ {len(flagged_ids)} file(s) could not be processed — Telegram has flagged or restricted them. "
            f"Alternatively upload media from your device to give it a new file id."
        )

    media_groups[user_id].clear()
    original_messages[user_id].clear()
    log.info(f"=== AUTO_SEND_ALBUM END: sent={len(sent_ids)} flagged={len(flagged_ids)} ===")

async def send_single_silent(user_id, chat_id, media):
    log.info(f"=== SINGLE SEND: user={user_id}, media={media.media} ===")

    await forward_to_storage(media)

    sent = False
    try:
        if media.photo:
            await safe_send(bot.send_photo, chat_id, photo=media.photo.file_id)
        elif media.video:
            await safe_send(bot.send_video, chat_id, video=media.video.file_id)
        elif media.document:
            await safe_send(bot.send_document, chat_id, document=media.document.file_id)
        elif media.audio:
            await safe_send(bot.send_audio, chat_id, audio=media.audio.file_id)
        sent = True
    except InvalidFileError:
        log.warning(f"Invalid file_id msg {media.id} — leaving in DM")
        await bot.send_message(chat_id, "⚠️ 1 file could not be processed — Telegram has flagged it. It has been left in your chat.")
    except Exception as e:
        log.error(f"Unexpected error sending single: {e}")

    if sent:
        try:
            await bot.delete_messages(chat_id, media.id)
        except Exception as e:
            log.warning(f"Could not delete msg {media.id}: {e}")

    media_groups[user_id].clear()
    original_messages[user_id].clear()
    log.info(f"=== SINGLE SEND END ===")

async def send_single_by_media(chat_id, media):
    """Send one item. Returns True on success, False on invalid file. Raises on other errors."""
    try:
        if media.photo:
            await safe_send(bot.send_photo, chat_id, photo=media.photo.file_id)
        elif media.video:
            await safe_send(bot.send_video, chat_id, video=media.video.file_id)
        elif media.document:
            await safe_send(bot.send_document, chat_id, document=media.document.file_id)
        elif media.audio:
            await safe_send(bot.send_audio, chat_id, audio=media.audio.file_id)
        return True
    except InvalidFileError:
        return False

# ------------------ Storage & Cleanup ------------------
async def forward_to_storage(message):
    if not Config.STORAGE_GROUP_ID:
        return
    if not message.from_user:
        log.debug(f"STORAGE_SKIP | msg={message.id} reason=no_user")
        return
    if message.from_user.id in ignored_users:
        log.info(f"STORAGE_IGNORED | user={message.from_user.id} msg={message.id}")
        return

    media_type = str(message.media).split(".")[-1] if message.media else "unknown"
    log.debug(f"STORAGE_COPY | user={message.from_user.id} msg={message.id} type={media_type}")

    try:
        if message.photo:
            await safe_send(bot.send_photo, Config.STORAGE_GROUP_ID, photo=message.photo.file_id)
        elif message.video:
            await safe_send(bot.send_video, Config.STORAGE_GROUP_ID, video=message.video.file_id)
        elif message.document:
            await safe_send(bot.send_document, Config.STORAGE_GROUP_ID, document=message.document.file_id)
        elif message.audio:
            await safe_send(bot.send_audio, Config.STORAGE_GROUP_ID, audio=message.audio.file_id)
        log.debug(f"STORAGE_COPY_OK | user={message.from_user.id} msg={message.id}")
    except InvalidFileError:
        log.warning(f"STORAGE_INVALID_FILE | user={message.from_user.id} msg={message.id} skipped")
    except Exception as e:
        log.error(f"STORAGE_COPY_FAIL | user={message.from_user.id} msg={message.id} error={e}")
async def cleanup(user_id, chat_id):
    msg_ids = original_messages[user_id].copy()
    count = len(msg_ids)
    log.debug(f"CLEANUP_START | user={user_id} msgs_to_delete={count}")

    deleted = 0
    failed = 0
    for msg_id in msg_ids:
        try:
            await bot.delete_messages(chat_id, msg_id)
            deleted += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            log.debug(f"CLEANUP_DELETE_FAIL | user={user_id} msg={msg_id} error={e}")

    media_groups[user_id].clear()
    original_messages[user_id].clear()
    log.info(f"CLEANUP_DONE | user={user_id} deleted={deleted} failed={failed}")

# ------------------ Bot Start ------------------
if __name__ == "__main__":
    log.info("Starting Anonymous Forward Bot...")
    log.info(f"Max album size: {Config.MAX_ALBUM_SIZE}")
    
    # Start background cleanup task
    bot.loop.create_task(cleanup_stale_sessions())
    log.info("Background cleanup task started (runs every 10 minutes)")
    
    # Graceful shutdown handler
    async def shutdown_handler():
        log.info("Shutdown signal received — flushing pending media...")
        tasks = []
        for user_id, medias in list(media_groups.items()):
            if medias:
                chat_id = medias[0].chat.id
                log.info(f"Flushing {len(medias)} items for user {user_id}")
                tasks.append(auto_send_album(user_id, chat_id))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        log.info("Flush complete. Shutting down.")

    def handle_signal(sig, frame):
        loop = asyncio.get_event_loop()
        loop.create_task(shutdown_handler())

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    bot.run()
