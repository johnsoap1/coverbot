import os
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from dotenv import load_dotenv
load_dotenv()
from Config import Config
from collections import defaultdict
import asyncio
import time
from pyrogram.errors import FloodWait, MessageDeleteForbidden

# Initialize bot
Bot = Client(
    "AnonForwardBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

# Store media groups temporarily (user_id: [media_list])
media_groups = defaultdict(list)
# Store original message IDs for deletion (user_id: [message_ids])
original_messages = defaultdict(list)
# Lock for thread-safe operations
user_locks = defaultdict(asyncio.Lock)

# Rate limiting tracking
last_send_time = defaultdict(float)  # chat_id: timestamp
global_send_times = []  # List of timestamps for global rate limit

# Maximum media items in album
MAX_ALBUM_SIZE = 10

# Rate limit constants (based on Telegram API limits)
RATE_LIMIT_PER_CHAT = 1.0  # 1 message per second per chat
RATE_LIMIT_GLOBAL = 30  # 30 messages per second globally
GROUP_RATE_LIMIT = 20  # 20 messages per minute in groups (we'll use per 60s)

async def rate_limit_check(chat_id, is_group=False):
    """
    Check and enforce rate limits for Telegram API
    Returns: seconds to wait (0 if ok to send)
    """
    global global_send_times
    current_time = time.time()
    
    # Clean up old global timestamps (older than 1 second)
    global_send_times = [t for t in global_send_times if current_time - t < 1.0]
    
    # Check global rate limit (30 msg/s)
    if len(global_send_times) >= RATE_LIMIT_GLOBAL:
        return 1.0 - (current_time - min(global_send_times))
    
    # Check per-chat rate limit (1 msg/s per chat)
    if chat_id in last_send_time:
        time_since_last = current_time - last_send_time[chat_id]
        if time_since_last < RATE_LIMIT_PER_CHAT:
            return RATE_LIMIT_PER_CHAT - time_since_last
    
    return 0

<<<<<<< HEAD
async def send_with_rate_limit(send_func, target_chat_id, is_group=False, *args, **kwargs):
=======
async def send_with_rate_limit(send_func, *args, chat_id=None, is_group=False, **kwargs):
>>>>>>> e7eb98b (pedro updates)
    """
    Send message with automatic rate limiting
    chat_id must be passed as keyword argument
    """
    if chat_id is None:
        raise ValueError("chat_id must be provided")
    
    while True:
        wait_time = await rate_limit_check(target_chat_id, is_group)
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        else:
            break
    
    try:
        # Pass chat_id as kwarg to the actual send function
        result = await send_func(*args, chat_id=chat_id, **kwargs)
        current_time = time.time()
        last_send_time[target_chat_id] = current_time
        global_send_times.append(current_time)
        return result
    except FloodWait as e:
        print(f"FloodWait: Waiting {e.value} seconds")
        await asyncio.sleep(e.value)
<<<<<<< HEAD
        return await send_with_rate_limit(send_func, target_chat_id, is_group, *args, **kwargs)
=======
        return await send_with_rate_limit(send_func, *args, chat_id=chat_id, is_group=is_group, **kwargs)
>>>>>>> e7eb98b (pedro updates)

@Bot.on_message(filters.private & filters.command("start"))
async def start(client, message):
    await message.reply_text(
        "**🕶️ Anonymous Forward Bot**\n\n"
        "Send me any media (photo/video/document) and I'll:\n"
        "✓ Remove your name\n"
        "✓ Remove captions\n"
        "✓ Delete your original message\n"
        "✓ Store in backup group (if configured)\n"
        "✓ Let you forward with bot's name only\n\n"
        "**Features:**\n"
        "• Send multiple media to create an album (max 10)\n"
        "• Grouped media preserved automatically\n"
        "• All metadata stripped\n"
        "• Respects Telegram API limits\n\n"
        "Just send me your media!"
    )

@Bot.on_message(filters.private & (filters.photo | filters.video | filters.document | filters.audio))
async def handle_media(client, message):
    user_id = message.from_user.id
    
    async with user_locks[user_id]:
        # Store original message ID for deletion
        original_messages[user_id].append(message.id)
        
        # Add media to user's collection
        media_groups[user_id].append(message)
        
        current_count = len(media_groups[user_id])
        
        # If they've sent media before, show album option
        if current_count == 1:
            # First media - wait a moment to see if more coming
            await asyncio.sleep(1.5)
            
            # Check again after delay
            if len(media_groups[user_id]) == 1:
                # Single media
                await send_single_media(client, message, user_id)
            else:
                # Multiple media detected
                await show_album_options(client, message, user_id)
        
        elif current_count < MAX_ALBUM_SIZE:
            # Update the existing message with new count
            await show_album_options(client, message, user_id, update=True)
        
        elif current_count == MAX_ALBUM_SIZE:
            # Max reached
            await message.reply_text(
                f"⚠️ Maximum album size ({MAX_ALBUM_SIZE}) reached!\n"
                "Use the buttons below to create your album or send individually."
            )

async def delete_original_messages(client, user_id, chat_id):
    """Delete all original messages sent by user"""
    if user_id not in original_messages or not original_messages[user_id]:
        return
    
    deleted_count = 0
    for msg_id in original_messages[user_id]:
        try:
            await client.delete_messages(chat_id, msg_id)
            deleted_count += 1
            await asyncio.sleep(0.1)  # Small delay between deletions
        except MessageDeleteForbidden:
            print(f"Cannot delete message {msg_id}")
        except Exception as e:
            print(f"Error deleting message {msg_id}: {e}")
    
    # Clear the list
    original_messages[user_id].clear()
    
    if deleted_count > 0:
        print(f"Deleted {deleted_count} original messages for user {user_id}")

async def forward_to_storage(client, media_message, user_id):
    """Forward media to storage group if configured"""
    if not Config.STORAGE_GROUP_ID:
        return None
    
    try:
<<<<<<< HEAD
        forwarded = await send_with_rate_limit(
            client.forward_messages,
            Config.STORAGE_GROUP_ID,
            is_group=True,
            chat_id=Config.STORAGE_GROUP_ID,
            from_chat_id=media_message.chat.id,
            message_ids=media_message.id
=======
        # Fixed: Use chat_id keyword argument
        forwarded = await send_with_rate_limit(
            client.forward_messages,
            chat_id=Config.STORAGE_GROUP_ID,      # Destination chat
            from_chat_id=media_message.chat.id,   # Source chat  
            message_ids=media_message.id,
            is_group=True
>>>>>>> e7eb98b (pedro updates)
        )

        print(f"Forwarded media to storage group for user {user_id}")
        return forwarded
        
    except Exception as e:
        print(f"Error forwarding to storage group: {e}")
        return None

async def send_single_media(client, message, user_id):
    """Send a single media without caption"""
    media = media_groups[user_id][0]
    
    # Forward to storage group first
    await forward_to_storage(client, media, user_id)
    
    # Send to user with rate limiting
    try:
        if media.photo:
            sent = await send_with_rate_limit(
                client.send_photo,
<<<<<<< HEAD
                message.chat.id,
=======
                chat_id=message.chat.id,
>>>>>>> e7eb98b (pedro updates)
                photo=media.photo.file_id,
                caption="✅ **Anonymous Media Ready**\nForward this to any chat - no attribution!"
            )
        elif media.video:
            sent = await send_with_rate_limit(
                client.send_video,
<<<<<<< HEAD
                message.chat.id,
=======
                chat_id=message.chat.id,
>>>>>>> e7eb98b (pedro updates)
                video=media.video.file_id,
                caption="✅ **Anonymous Video Ready**"
            )
        elif media.document:
            sent = await send_with_rate_limit(
                client.send_document,
<<<<<<< HEAD
                message.chat.id,
=======
                chat_id=message.chat.id,
>>>>>>> e7eb98b (pedro updates)
                document=media.document.file_id,
                caption="✅ **Anonymous Document Ready**"
            )
        elif media.audio:
            sent = await send_with_rate_limit(
                client.send_audio,
<<<<<<< HEAD
                message.chat.id,
=======
                chat_id=message.chat.id,
>>>>>>> e7eb98b (pedro updates)
                audio=media.audio.file_id,
                caption="✅ **Anonymous Audio Ready**"
)
    except Exception as e:
        await message.reply_text(f"❌ Error sending media: {str(e)}")
        media_groups[user_id].clear()
        return
    
    # Delete original messages
    await delete_original_messages(client, user_id, message.chat.id)
    
    # Clear user's media group
    media_groups[user_id].clear()

async def show_album_options(client, message, user_id, update=False):
    """Show options for creating album or sending individually"""
    count = len(media_groups[user_id])
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"📚 Create Album ({count} items)", 
                callback_data="create_album"
            )
        ],
        [
            InlineKeyboardButton(
                "📤 Send Individually", 
                callback_data="send_individual"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Add More Media", 
                callback_data="add_more"
            ),
            InlineKeyboardButton(
                "🗑️ Cancel", 
                callback_data="cancel_album"
            )
        ]
    ])
    
    text = (
        f"**📸 Media Collection ({count}/{MAX_ALBUM_SIZE})**\n\n"
        "Choose how to send your media:\n"
        "• **Album** - Send all together as a media group\n"
        "• **Individual** - Send each separately\n"
        f"• **Add More** - Add up to {MAX_ALBUM_SIZE - count} more items\n\n"
        "⚠️ Original messages will be deleted after processing"
    )
    
    if not update or count == 1:
        await message.reply_text(text, reply_markup=keyboard)

@Bot.on_callback_query()
async def handle_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if data == "create_album":
        await create_album(client, callback_query, user_id)
    
    elif data == "send_individual":
        await send_individually(client, callback_query, user_id)
    
    elif data == "add_more":
        count = len(media_groups[user_id])
        if count < MAX_ALBUM_SIZE:
            await callback_query.answer(
                f"Send {MAX_ALBUM_SIZE - count} more media items",
                show_alert=False
            )
        else:
            await callback_query.answer(
                "Maximum album size reached!",
                show_alert=True
            )
    
    elif data == "cancel_album":
        # Delete original messages
        await delete_original_messages(client, user_id, callback_query.message.chat.id)
        media_groups[user_id].clear()
        await callback_query.message.edit_text("❌ Cancelled. Your original messages have been deleted.")
        await callback_query.answer("Cancelled")

async def create_album(client, callback_query, user_id):
    """Create and send media album"""
    medias = media_groups[user_id]
    
    if not medias:
        await callback_query.answer("No media to send!", show_alert=True)
        return
    
    await callback_query.message.edit_text(
        f"⏳ Creating album with {len(medias)} items...\n"
        "Please wait, respecting API rate limits..."
    )
    
    # Forward each to storage group first
    for media in medias:
        await forward_to_storage(client, media, user_id)
        await asyncio.sleep(0.3)  # Small delay between forwards
    
    # Prepare media group
    media_list = []
    
    for idx, msg in enumerate(medias):
        if msg.photo:
            from pyrogram.types import InputMediaPhoto
            media_list.append(
                InputMediaPhoto(
                    msg.photo.file_id,
                    caption="📚 Anonymous Album" if idx == 0 else ""
                )
            )
        elif msg.video:
            from pyrogram.types import InputMediaVideo
            media_list.append(
                InputMediaVideo(
                    msg.video.file_id,
                    caption="📚 Anonymous Album" if idx == 0 else ""
                )
            )
        elif msg.document:
            from pyrogram.types import InputMediaDocument
            media_list.append(
                InputMediaDocument(
                    msg.document.file_id,
                    caption="📚 Anonymous Album" if idx == 0 else ""
                )
            )
    
    # Send the album with rate limiting
    try:
        await send_with_rate_limit(
            client.send_media_group,
<<<<<<< HEAD
            callback_query.message.chat.id,
=======
            chat_id=callback_query.message.chat.id,
>>>>>>> e7eb98b (pedro updates)
            media=media_list
        )
        
        await callback_query.message.edit_text(
            f"✅ **Album Created!** ({len(medias)} items)\n\n"
            "Forward these to any chat - completely anonymous!\n"
            "Original messages have been deleted."
        )
        
        # Delete original messages
        await delete_original_messages(client, user_id, callback_query.message.chat.id)
        
        # Clear the media group
        media_groups[user_id].clear()
        await callback_query.answer("Album created!")
        
    except Exception as e:
        await callback_query.message.edit_text(
            f"❌ Error creating album: {str(e)}\n\n"
            "Try sending items individually instead."
        )
        await callback_query.answer("Error occurred", show_alert=True)

async def send_individually(client, callback_query, user_id):
    """Send each media separately"""
    medias = media_groups[user_id]
    
    if not medias:
        await callback_query.answer("No media to send!", show_alert=True)
        return
    
    await callback_query.message.edit_text(
        f"⏳ Sending {len(medias)} items individually...\n"
        "Please wait, respecting API rate limits..."
    )
    
    sent_count = 0
    for msg in medias:
        try:
            # Forward to storage first
            await forward_to_storage(client, msg, user_id)
            
            # Send to user with rate limiting
            if msg.photo:
                await send_with_rate_limit(
                    client.send_photo,
                    callback_query.message.chat.id,
                    photo=msg.photo.file_id
                )
            elif msg.video:
                await send_with_rate_limit(
                    client.send_video,
                    callback_query.message.chat.id,
                    video=msg.video.file_id
                )
            elif msg.document:
                await send_with_rate_limit(
                    client.send_document,
                    callback_query.message.chat.id,
                    document=msg.document.file_id
                )
            elif msg.audio:
                await send_with_rate_limit(
                    client.send_audio,
                    callback_query.message.chat.id,
                    audio=msg.audio.file_id
                )
            
            sent_count += 1
        
        except Exception as e:
            print(f"Error sending media: {e}")
    
    await callback_query.message.edit_text(
        f"✅ **Sent {sent_count} items individually!**\n\n"
        "Forward these to any chat - completely anonymous!\n"
        "Original messages have been deleted."
    )
    
    # Delete original messages
    await delete_original_messages(client, user_id, callback_query.message.chat.id)
    
    # Clear the media group
    media_groups[user_id].clear()
    await callback_query.answer(f"Sent {sent_count} items!")

# Handle text messages (original functionality)
@Bot.on_message(filters.private & filters.text & ~filters.command("start"))
async def handle_text(client, message):
    sent = await message.reply_text(message.text)
    
    # Delete original message
    try:
        await message.delete()
    except:
        pass

# Support for stickers
@Bot.on_message(filters.private & filters.sticker)
async def handle_sticker(client, message):
    sent = await message.reply_sticker(message.sticker.file_id)
    
    # Delete original message
    try:
        await message.delete()
    except:
        pass

# Run the bot
if __name__ == "__main__":
    print("🚀 Anonymous Forward Bot Started!")
    print(f"Rate limits: {RATE_LIMIT_GLOBAL} msg/s global, {RATE_LIMIT_PER_CHAT} msg/s per chat")
    if Config.STORAGE_GROUP_ID:
        print(f"Storage Group: {Config.STORAGE_GROUP_ID}")
    else:
        print("⚠️ No storage group configured")
    Bot.run()
