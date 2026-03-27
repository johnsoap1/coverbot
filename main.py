import asyncio
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

BOT_ID = None

# ------------------ State Storage ------------------ #

# ✅ FIX: per-user + per-album
media_groups = defaultdict(lambda: defaultdict(list))
original_messages = defaultdict(list)
user_locks = defaultdict(asyncio.Lock)

# ------------------ Safe Send ------------------ #

async def safe_send(func, chat_id, **kwargs):
    while True:
        try:
            return await func(chat_id=chat_id, **kwargs)
        except FloodWait as e:
            logging.warning(f"FloodWait {e.value}s")
            await asyncio.sleep(e.value)
        except RPCError as e:
            logging.error(f"RPCError: {e}")
            return None

# ------------------ Start ------------------ #

@bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    global BOT_ID
    if BOT_ID is None:
        BOT_ID = (await client.get_me()).id

    user_name = message.from_user.first_name
    bot_name = (await client.get_me()).first_name

    await message.reply_text(
        f"Hey {user_name} 👋\n\n"
        f"Welcome to {bot_name}\n\n"
        "Send me media and I’ll return it anonymously."
    )

# ------------------ Media Handler ------------------ #

@bot.on_message(filters.private & (filters.photo | filters.video | filters.document | filters.audio))
async def handle_media(client, message):
    global BOT_ID

    if BOT_ID is None:
        BOT_ID = (await client.get_me()).id

    user_id = message.from_user.id
    chat_id = message.chat.id

    # Ignore bot messages
    if user_id == BOT_ID:
        return

    # ✅ KEY FIX: real album grouping
    group_id = message.media_group_id or f"single_{message.id}"

    async with user_locks[user_id]:
        media_groups[user_id][group_id].append(message)
        original_messages[user_id].append(message.id)

    # ✅ tiny debounce (enough for Telegram album)
    await asyncio.sleep(0.3)

    async with user_locks[user_id]:
        medias = media_groups[user_id].pop(group_id, [])

    if not medias:
        return

    try:
        if len(medias) == 1:
            await send_single(chat_id, medias[0])
        else:
            await send_album(chat_id, medias)

        # ✅ storage in background (NO DELAY)
        if Config.STORAGE_GROUP_ID:
            asyncio.create_task(
                asyncio.gather(*[forward_to_storage(m) for m in medias])
            )

        await cleanup(user_id, chat_id)

    except Exception as e:
        logging.error(f"Error sending media: {e}", exc_info=True)
        await cleanup(user_id, chat_id)

# ------------------ Send Functions ------------------ #

async def send_single(chat_id, media):
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
        logging.error(f"Error sending single: {e}")

async def send_album(chat_id, medias):
    media_list = []

    for m in medias:
        if m.photo:
            media_list.append(InputMediaPhoto(m.photo.file_id))
        elif m.video:
            media_list.append(InputMediaVideo(m.video.file_id))
        elif m.document:
            media_list.append(InputMediaDocument(m.document.file_id))

    if not media_list:
        return

    # Telegram limit = 10
    chunks = [media_list[i:i+10] for i in range(0, len(media_list), 10)]

    for chunk in chunks:
        await safe_send(bot.send_media_group, chat_id, media=chunk)

# ------------------ Storage ------------------ #

async def forward_to_storage(message):
    if not Config.STORAGE_GROUP_ID:
        return

    try:
        await bot.forward_messages(
            Config.STORAGE_GROUP_ID,
            from_chat_id=message.chat.id,
            message_ids=message.id
        )
    except Exception as e:
        logging.error(f"Storage failed: {e}")

# ------------------ Cleanup ------------------ #

async def cleanup(user_id, chat_id):
    for msg_id in original_messages[user_id]:
        try:
            await bot.delete_messages(chat_id, msg_id)
        except Exception:
            pass

    media_groups[user_id].clear()
    original_messages[user_id].clear()

# ------------------ Run ------------------ #

if __name__ == "__main__":
    logging.info("🚀 Bot started (FAST MODE)")
    bot.run()
