# Telegram Auto Media Filter Bot

A Python Telegram bot using Pyrogram and MongoDB to automatically index media from specified channels and provide search results with direct links in groups.

## Features

-   **Auto Indexing:** Indexes media (videos, documents, audio) from configured Telegram channels.
-   **Group Filtering:** Searches indexed media when users send text queries in groups.
-   **Direct Links:** Provides inline buttons linking directly to the media post in the source channel.
-   **Force Subscription:** Requires users to join a specified channel (`UPDATES_CHANNEL`) before using the bot.
-   **Admin Commands:**
    -   `/start`, `/help`
    -   `/index [channel]`: Index a specific channel or all configured channels.
    -   `/clearindex [channel]`: Remove indexed data for a channel.
    -   `/status`, `/stats`: Show bot statistics.
    -   `/broadcast`: Send messages to all active users.
    -   `/ban`, `/unban`, `/banned`: Manage user access.
-   **Customization:** Supports custom start messages, pictures, and request button via environment variables.
-   **Database:** Uses MongoDB for storing user and media data.
-   **Deployment:** Designed for easy deployment on platforms like Koyeb.
-   **Logging:** Logs important events and errors to a specified log channel.

## Required Environment Variables

Create a `.env` file or set these environment variables directly on your hosting platform (like Koyeb):

```ini
# Bot Credentials (Get from my.telegram.org & @BotFather)
API_ID=YOUR_API_ID
API_HASH=YOUR_API_HASH
BOT_TOKEN=YOUR_BOT_TOKEN

# Database (Get a free cluster from cloud.mongodb.com)
DATABASE_URI=YOUR_MONGODB_CONNECTION_STRING
DATABASE_NAME=TelegramAutoFilterBot # Optional, default is provided

# Bot Admin & Logging
OWNER_ID=YOUR_TELEGRAM_USER_ID # Your numeric Telegram ID
LOG_CHANNEL=-100YOUR_LOG_CHANNEL_ID # Numeric ID of the channel where logs will be sent (Bot MUST be admin here)

# Functionality
UPDATES_CHANNEL=@YourUpdatesChannel # Username or ID (-100...) of the channel for Force Subscribe (Bot MUST be admin here)
INDEX_CHANNELS="-10012345678 -10098765432 @PublicChannel" # Space-separated list of Channel IDs/Usernames to index (Bot MUST be admin/member)

# Appearance (Optional)
PICS="https://telegra.ph/file/d7570d3817181f9a67b6c.jpg https://example.com/another_pic.jpg" # Space-separated URLs for /start photo (optional)
START_MSG="Hello {mention}, I am an Auto Filter Bot..." # Custom start message (optional)
FORCE_SUB_MSG="Hello {mention}, Please join {channel_name}..." # Custom FSub message (optional)
NOT_FOUND_MSG="Sorry, couldn't find: `{query}`" # Custom not found message (optional)
REQUEST_MOVIE_BUTTON_TEXT="❓ Request Movie" # Text for the request button (optional)
REQUEST_MOVIE_URL="https://t.me/YourRequestGroup" # URL for the request button (optional, enables the button)
