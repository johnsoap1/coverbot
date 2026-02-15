import os

class Config:
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
