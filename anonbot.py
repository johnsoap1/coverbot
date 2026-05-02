import asyncio
import time
import logging
import logging.handlers
import json
import os
import traceback
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument
from Config import Config

# ── Logging setup ────────────────────────────────────────────
LOG_FORMAT  = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

os.makedirs("logs", exist_ok=True)

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Console — INFO+ only (keeps terminal clean)
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

# Rotating file — DEBUG+ (7 day retention)
_fh = logging.handlers.TimedRotatingFileHandler(
    "logs/bot.log", when="midnight", backupCount=7, encoding="utf-8"
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

# Errors-only file — WARNING+
_eh = logging.handlers.RotatingFileHandler(
    "logs/errors.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_eh.setLevel(logging.WARNING)
_eh.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

root_logger.addHandler(_ch)
root_logger.addHandler(_fh)
root_logger.addHandler(_eh)

# Silence pyrogram internals
for _n in ("pyrogram", "pyrogram.client", "pyrogram.session",
           "pyrogram.connection", "pyrogram.dispatcher"):
    logging.getLogger(_n).setLevel(logging.WARNING)

log = logging.getLogger("AnonBot")

# ------------------ Bot Init ------------------
bot = Client(
    "AnonForwardBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

# ── Telegram log channel handler ────────────────────────────

class TelegramLogHandler(logging.Handler):
    """
    Async handler that ships WARNING+ logs to a Telegram group/channel.
    Uses an internal queue so it never blocks the event loop.
    Errors inside the handler are swallowed — logging must never crash the bot.
    """
    ICONS = {
        logging.DEBUG:    "🔍",
        logging.INFO:     "ℹ️",
        logging.WARNING:  "⚠️",
        logging.ERROR:    "❌",
        logging.CRITICAL: "🚨",
    }

    def __init__(self, bot_client, channel_id):
        super().__init__(level=logging.WARNING)  # WARNING+ to Telegram only
        self.bot_client  = bot_client
        self.channel_id  = channel_id
        self._queue: asyncio.Queue = None   # created after loop starts
        self._task:  asyncio.Task  = None

    def _ensure_queue(self):
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=200)

    def start(self):
        """Must be called once the asyncio event loop is running."""
        self._ensure_queue()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())
            log.info("TG_LOG_HANDLER | worker started")

    def emit(self, record: logging.LogRecord):
        self._ensure_queue()
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            pass  # Drop — never block the bot

    async def _worker(self):
        while True:
            try:
                record = await self._queue.get()
                await self._ship(record)
            except asyncio.CancelledError:
                break
            except Exception:
                pass  # Swallow everything
            finally:
                try:
                    self._queue.task_done()
                except Exception:
                    pass
                await asyncio.sleep(0.4)  # ~2.5 msgs/sec max to log channel

    async def _ship(self, record: logging.LogRecord):
        try:
            icon  = self.ICONS.get(record.levelno, "📋")
            msg   = self.format(record)
            # Escape HTML special chars in the log line
            safe  = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            if record.exc_info:
                tb   = "".join(traceback.format_exception(*record.exc_info))
                # Truncate to stay under Telegram's 4096 char limit
                tb   = tb[-1800:]
                safe_tb = tb.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                text = f"{icon} <b>[{record.levelname}]</b>\n<code>{safe}</code>\n\n<pre>{safe_tb}</pre>"
            else:
                text = f"{icon} <b>[{record.levelname}]</b>\n<code>{safe}</code>"

            # Hard cap at 4096
            text = text[:4090]

            await self.bot_client.send_message(
                self.channel_id,
                text,
                parse_mode="html",
                disable_notification=(record.levelno < logging.ERROR),
                disable_web_page_preview=True,
            )
        except Exception:
            pass  # Never propagate

# Attach Telegram log handler if channel configured
tg_log_handler: TelegramLogHandler | None = None
if getattr(Config, "LOG_CHANNEL_ID", None):
    tg_log_handler = TelegramLogHandler(bot, Config.LOG_CHANNEL_ID)
    tg_log_handler.setFormatter(logging.Formatter(
        "%(name)s | %(funcName)s | %(message)s"
    ))
    root_logger.addHandler(tg_log_handler)
    log.debug("TG_LOG_HANDLER | registered (not started yet — waiting for loop)")

# ------------------ State Storage ------------------
media_groups = defaultdict(list)
original_messages = defaultdict(list)
user_locks = defaultdict(asyncio.Lock)
user_send_tasks = {}
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
    """Raised on Telegram 400 FILE_ID_INVALID — do not retry."""
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
        log.debug(
            f"SAFE_SEND | func={func.__name__} chat={chat_id} "
            f"attempt={attempt} keys={list(kwargs.keys())}"
        )
        try:
            await rate_limit(chat_id)
            result = await func(chat_id=chat_id, **kwargs)
            now = time.time()
            last_send_time[chat_id] = now
            global_timestamps.append(now)
            log.debug(f"SAFE_SEND_OK | func={func.__name__} chat={chat_id}")
            return result

        except FloodWait as e:
            log.warning(
                f"FLOOD_WAIT | func={func.__name__} chat={chat_id} "
                f"wait={e.value}s attempt={attempt}"
            )
            await asyncio.sleep(e.value)

        except RPCError as e:
            err_str = str(e).upper()
            if e.CODE == 400 and "FILE_ID_INVALID" in err_str:
                log.warning(
                    f"INVALID_FILE_ID | func={func.__name__} chat={chat_id} error={e}"
                )
                raise InvalidFileError(str(e))
            log.error(
                f"RPC_ERROR | func={func.__name__} chat={chat_id} "
                f"code={e.CODE} error={e}",
                exc_info=True
            )
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
    user_name = message.from_user.first_name
    bot_name = (await client.get_me()).first_name
    log.info(f"START | user={message.from_user.id} name={user_name!r}")
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

@bot.on_message(
    filters.private
    & (filters.photo | filters.video | filters.document | filters.audio)
    & ~filters.me
)
async def handle_media(client, message):
    user_id    = message.from_user.id
    chat_id    = message.chat.id
    media_type = str(message.media).split(".")[-1] if message.media else "unknown"
    group_id   = message.media_group_id

    log.info(
        f"MEDIA_IN | user={user_id} msg={message.id} "
        f"type={media_type} group={group_id or 'none'}"
    )

    async with user_locks[user_id]:
        media_groups[user_id].append(message)
        original_messages[user_id].append(message.id)
        count = len(media_groups[user_id])
        log.debug(f"QUEUE | user={user_id} depth={count}")

        if user_id in user_send_tasks:
            log.debug(f"TIMER_CANCEL | user={user_id} depth={count}")
            user_send_tasks[user_id].cancel()
            del user_send_tasks[user_id]

        if count >= Config.MAX_ALBUM_SIZE:
            log.info(f"MAX_SIZE | user={user_id} count={count} firing immediately")
            asyncio.create_task(send_user_media(user_id, chat_id))
        else:
            delay = 1.0 if group_id else 3.0
            log.debug(f"TIMER_SET | user={user_id} delay={delay}s")
            task = asyncio.create_task(delayed_send(user_id, chat_id, delay))
            user_send_tasks[user_id] = task

# ------------------ Send Functions ------------------

async def delayed_send(user_id, chat_id, delay):
    try:
        log.debug(f"TIMER_WAIT | user={user_id} delay={delay}s")
        await asyncio.sleep(delay)
        log.debug(f"TIMER_FIRE | user={user_id}")
        await send_user_media(user_id, chat_id)
    except asyncio.CancelledError:
        log.debug(f"TIMER_CANCELLED | user={user_id}")


async def send_user_media(user_id, chat_id):
    user_send_tasks.pop(user_id, None)

    medias = media_groups[user_id].copy()
    count  = len(medias)

    if not medias:
        log.warning(f"SEND_EMPTY | user={user_id}")
        return

    log.info(f"SEND_START | user={user_id} chat={chat_id} count={count}")
    t0 = time.time()

    try:
        if count == 1:
            await send_single_silent(user_id, chat_id, medias[0])
        else:
            await auto_send_album(user_id, chat_id)

        log.info(
            f"SEND_DONE | user={user_id} count={count} "
            f"elapsed={round(time.time()-t0, 2)}s"
        )
    except Exception as e:
        log.error(f"SEND_FATAL | user={user_id} error={e}", exc_info=True)
        media_groups[user_id].clear()
        original_messages[user_id].clear()

# ------------------ Auto Album System ------------------
async def auto_send_album(user_id, chat_id):
    log.info(f"=== AUTO_SEND_ALBUM: user={user_id} count={len(media_groups[user_id])} ===")
    medias = list(media_groups[user_id])

    if not medias:
        return

    if len(medias) == 1:
        await send_single_silent(user_id, chat_id, medias[0])
        return

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
    """Copy raw media to storage — no caption, no forward header."""
    if not getattr(Config, "STORAGE_GROUP_ID", None):
        return
    if not message.from_user:
        log.debug(f"STORAGE_SKIP | msg={message.id} reason=no_user")
        return
    if message.from_user.id in ignored_users:
        log.debug(f"STORAGE_IGNORED | user={message.from_user.id} msg={message.id}")
        return

    log.debug(f"STORAGE_COPY | user={message.from_user.id} msg={message.id}")
    try:
        if message.photo:
            await safe_send(bot.send_photo,    Config.STORAGE_GROUP_ID, photo=message.photo.file_id)
        elif message.video:
            await safe_send(bot.send_video,    Config.STORAGE_GROUP_ID, video=message.video.file_id)
        elif message.document:
            await safe_send(bot.send_document, Config.STORAGE_GROUP_ID, document=message.document.file_id)
        elif message.audio:
            await safe_send(bot.send_audio,    Config.STORAGE_GROUP_ID, audio=message.audio.file_id)
        log.debug(f"STORAGE_COPY_OK | user={message.from_user.id} msg={message.id}")
    except InvalidFileError:
        log.warning(f"STORAGE_INVALID_FILE | user={message.from_user.id} msg={message.id}")
    except Exception as e:
        log.error(f"STORAGE_COPY_FAIL | user={message.from_user.id} msg={message.id} error={e}", exc_info=True)
async def cleanup(user_id, chat_id):
    msg_ids = original_messages[user_id].copy()
    log.debug(f"CLEANUP | user={user_id} deleting={len(msg_ids)}")
    deleted = failed = 0
    for msg_id in msg_ids:
        try:
            await bot.delete_messages(chat_id, msg_id)
            deleted += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            log.debug(f"CLEANUP_FAIL | user={user_id} msg={msg_id} error={e}")
    media_groups[user_id].clear()
    original_messages[user_id].clear()
    log.info(f"CLEANUP_DONE | user={user_id} deleted={deleted} failed={failed}")

# ------------------ Bot Start ------------------
async def on_startup():
    """Runs once the event loop and pyrogram session are both live."""
    # Start Telegram log handler now that the loop exists
    if tg_log_handler:
        tg_log_handler.start()

    # Kick off background maintenance
    asyncio.create_task(cleanup_stale_sessions())

    log.warning(
        f"BOT_ONLINE | "
        f"max_album={Config.MAX_ALBUM_SIZE} "
        f"storage={getattr(Config, 'STORAGE_GROUP_ID', None) or 'none'} "
        f"log_channel={getattr(Config, 'LOG_CHANNEL_ID', None) or 'none'} "
        f"rate_global={Config.RATE_LIMIT_GLOBAL} "
        f"rate_per_chat={Config.RATE_LIMIT_PER_CHAT}"
    )


if __name__ == "__main__":
    async def main():
        log.info("BOT_STARTING | connecting to Telegram...")
        await bot.start()
        await on_startup()
        log.info("BOT_READY | listening for messages")
        await asyncio.Event().wait()   # keep alive forever

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.warning("BOT_SHUTDOWN | KeyboardInterrupt — goodbye")
