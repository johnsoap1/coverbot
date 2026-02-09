# Anonymous Forward Bot

A Telegram bot that forwards messages anonymously with rate limiting, album support, and storage group functionality.

## Features

- **Anonymous Forwarding**: Forward messages without revealing the original sender
- **Rate Limiting**: Built-in protection against API bans
- **Album Support**: Handle multiple media items as albums
- **Storage Group**: Store media in a designated group
- **Delete Original**: Option to delete original messages after forwarding
- **Thread-Safe**: Concurrent user handling with proper locking

## Requirements

- Python 3.7+
- Telegram Bot API credentials
- Linux server (recommended) or any system supporting Python

## Installation on Linux Python Server

### 1. System Requirements

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install Python and pip if not already installed
sudo apt install python3 python3-pip python3-venv -y

# Install git if not available
sudo apt install git -y
```

### 2. Clone the Repository

```bash
# Clone the bot repository
git clone <repository-url>
cd Anon-Forward-bot-main
```

### 3. Create Virtual Environment

```bash
# Create Python virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate
```

### 4. Install Dependencies

```bash
# Install required Python packages
pip install -r requirements.txt
```

### 5. Configure Environment Variables

Create a `.env` file in the project root:

```bash
nano .env
```

Add the following mandatory variables:

```env
# Required: Get these from https://my.telegram.org/auth
API_ID=your_api_id_here
API_HASH=your_api_hash_here

# Required: Get this from @BotFather on Telegram
BOT_TOKEN=your_bot_token_here

# Optional: Storage group ID (use negative ID for groups/channels)
STORAGE_GROUP_ID=-1001234567890

# Optional: Owner ID for admin features
OWNER_ID=123456789
```

### 6. Run the Bot

```bash
# Make sure virtual environment is active
source venv/bin/activate

# Run the bot
python anonbot.py
```

## Deployment Options

### Option 1: Using Screen (Recommended for simple deployment)

```bash
# Install screen
sudo apt install screen -y

# Create a new screen session
screen -S anonbot

# Activate virtual environment and run bot
source venv/bin/activate
python anonbot.py

# Detach from screen (Ctrl+A, then D)
# Reattach later with: screen -r anonbot
```

### Option 2: Using systemd (Production deployment)

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/anonbot.service
```

Add the following content (adjust paths as needed):

```ini
[Unit]
Description=Anonymous Forward Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/Anon-Forward-bot-main
Environment=PATH=/path/to/Anon-Forward-bot-main/venv/bin
ExecStart=/path/to/Anon-Forward-bot-main/venv/bin/python anonbot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable anonbot
sudo systemctl start anonbot

# Check status
sudo systemctl status anonbot
```

### Option 3: Using Docker

Create a `Dockerfile`:

```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "anonbot.py"]
```

Build and run:

```bash
docker build -t anonbot .
docker run -d --name anonbot --restart unless-stopped anonbot
```

## Getting Required Credentials

### 1. API_ID and API_HASH

1. Visit [my.telegram.org](https://my.telegram.org/auth)
2. Sign in with your phone number
3. Go to "API development tools"
4. Create a new application
5. Copy the `API_ID` and `API_HASH`

### 2. BOT_TOKEN

1. Start a chat with [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`
3. Follow the prompts to create your bot
4. Copy the bot token provided

## Configuration Options

- **STORAGE_GROUP_ID**: Set to a negative group/channel ID to enable media storage
- **OWNER_ID**: Your Telegram user ID for admin features
- **RATE_LIMIT_PER_CHAT**: Messages per second per chat (default: 1)
- **RATE_LIMIT_GLOBAL**: Global messages per second (default: 30)
- **MAX_ALBUM_SIZE**: Maximum media items in an album (default: 10)

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure virtual environment is activated
2. **Permission Denied**: Check file permissions and user access
3. **Bot Not Responding**: Verify API credentials and internet connection
4. **Rate Limit Errors**: Built-in rate limiting should prevent this

### Logs

Check the bot logs for errors:

```bash
# If using screen
screen -r anonbot

# If using systemd
sudo journalctl -u anonbot -f

# If using Docker
docker logs anonbot -f
```

## Security Recommendations

1. Never share your API credentials
2. Use a dedicated user for running the bot
3. Regularly update dependencies
4. Monitor bot activity and logs
5. Use firewall rules to restrict unnecessary access

## License

This project is licensed under the terms specified in the LICENSE file.