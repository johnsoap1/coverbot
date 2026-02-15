from pyrogram.types import InlineKeyboardButton

class Data:
    # Start Message
    START = "Hey {}. \n\nWelcome to {} \n\nI'm an anonymous forward bot designed to strip metadata from Telegram videos for privacy. Send me media to get started."
    
    # About Message
    ABOUT = """
**About This Bot** 

A privacy-focused bot that removes metadata from your media files.

**Features:**
- Strips all metadata from videos and photos
- Removes forwarded tags
- Ensures your privacy
- Fast and secure processing
    """
    
    # Home Button
    home_button = [[InlineKeyboardButton(text="🏠 Return Home 🏠", callback_data="home")]]
    
    # Rest Buttons
    buttons = [
        [InlineKeyboardButton("🎪 About The Bot 🎪", callback_data="about")],
        [InlineKeyboardButton("♥ More Bots ♥", callback_data="more_bots")],
        [InlineKeyboardButton("ℹ️ Help & Info ℹ️", callback_data="help_info")],
    ]
