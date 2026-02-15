import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    API_ID = int(os.getenv("API_ID") or "0")
    API_HASH = os.getenv("API_HASH") or ""
    BOT_TOKEN = os.getenv("BOT_TOKEN") or ""

    STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID", "0")) or None
    OWNER_ID = int(os.getenv("OWNER_ID", "0")) or None

    MAX_ALBUM_SIZE = 10

    # Rate limits
    RATE_LIMIT_PER_CHAT = 1.0
    RATE_LIMIT_GLOBAL = 30

    if not API_ID or not API_HASH or not BOT_TOKEN:
        raise ValueError("Missing required API credentials.")
