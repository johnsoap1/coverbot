import os

class Config:
<<<<<<< HEAD
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")

    STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID", "0")) or None
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))

    MAX_ALBUM_SIZE = 10

    # Rate limits
    RATE_LIMIT_PER_CHAT = 1.0
    RATE_LIMIT_GLOBAL = 30

    if not API_ID or not API_HASH or not BOT_TOKEN:
        raise ValueError("Missing required API credentials.")
=======
    API_ID = int(os.environ.get("API_ID", "0"))
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    
    # Storage group ID (set to 0 or None to disable)
    # Use negative ID for groups/channels (e.g., -1001234567890)
    STORAGE_GROUP_ID = int(os.environ.get("STORAGE_GROUP_ID", "0")) or None
    
    # Optional: Owner ID for admin features
    OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
>>>>>>> e7eb98b (pedro updates)
