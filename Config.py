import os

class Config:
    API_ID = int(os.environ.get("API_ID", "12345"))
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    
    # Storage group ID (set to 0 or None to disable)
    # Use negative ID for groups/channels (e.g., -1001234567890)
    STORAGE_GROUP_ID = int(os.environ.get("STORAGE_GROUP_ID", "0")) or None
    
    # Optional: Owner ID for admin features
    OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
