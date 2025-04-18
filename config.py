# config.py
import os
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file if it exists

class Config:
    # Bot Essentials
    API_ID = int(os.environ.get("API_ID", "0")) # Get from my.telegram.org
    API_HASH = os.environ.get("API_HASH", "")   # Get from my.telegram.org
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "") # Get from @BotFather

    # Database
    DATABASE_URI = os.environ.get("DATABASE_URI", "") # MongoDB connection string
    DATABASE_NAME = os.environ.get("DATABASE_NAME", "TelegramAutoFilterBot")

    # Bot Owner and Logging
    OWNER_ID = int(os.environ.get("OWNER_ID", "0")) # Your Telegram User ID
    LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0")) # Log channel ID (must be an integer) Make bot admin in this channel

    # Force Subscription and Bot Info
    UPDATES_CHANNEL = os.environ.get("UPDATES_CHANNEL", None) # Username or ID of channel for force subscribe. Make bot admin in this channel
    PICS = os.environ.get("PICS", "https://telegra.ph/file/d7570d3817181f9a67b6c.jpg").split() # List of space-separated URLs for start message photos

    # Indexing (Source Channel) - IMPORTANT!
    # Bot needs to be admin in the source channel(s) to read messages
    # You can add multiple channels here if needed, separated by spaces in ENV
    # Example: "-100123456789 -100987654321 @PublicChannelUsername"
    # Make sure the IDs are integers (for private channels, prefix with -100)
    INDEX_CHANNELS_STR = os.environ.get("INDEX_CHANNELS", "")
    try:
        INDEX_CHANNELS = [int(ch) if ch.startswith('-') else ch for ch in INDEX_CHANNELS_STR.split()]
    except ValueError:
        print("Warning: INDEX_CHANNELS contains non-integer values that don't start with '-'. Ensure usernames are correct.")
        INDEX_CHANNELS = [ch for ch in INDEX_CHANNELS_STR.split()]
    except Exception as e:
        print(f"Error parsing INDEX_CHANNELS: {e}. Setting to empty list.")
        INDEX_CHANNELS = []


    # Optional: Customize messages
    START_MSG = os.environ.get("START_MSG", "Hello {mention},\nI am an Auto Filter Bot. I can search for media in connected channels for you. Just send me the movie/series name in the group!")
    FORCE_SUB_MSG = os.environ.get("FORCE_SUB_MSG", "Hello {mention},\n\nYou need to join my updates channel to use me!\n\nPlease join [{channel_name}]({channel_link}) and then try again.")
    NOT_FOUND_MSG = os.environ.get("NOT_FOUND_MSG", "Sorry, I couldn't find any media matching your query: `{query}`")
    REQUEST_MOVIE_BUTTON_TEXT = os.environ.get("REQUEST_MOVIE_BUTTON_TEXT", "❓ Request Movie")
    REQUEST_MOVIE_URL = os.environ.get("REQUEST_MOVIE_URL", None) # URL for the request button (e.g., link to a request group/form)

    # Other settings
    MAX_RESULTS = int(os.environ.get("MAX_RESULTS", 5)) # Max results to show per search
    CACHE_TIME = int(os.environ.get("CACHE_TIME", 300)) # Cache time for inline results (if using inline mode later)
    SLEEP_TIME_BCAST = int(os.environ.get("SLEEP_TIME_BCAST", 2)) # Sleep time between messages during broadcast (to avoid flood limits)


cfg = Config()

# Basic validation
if not all([cfg.API_ID, cfg.API_HASH, cfg.BOT_TOKEN, cfg.DATABASE_URI, cfg.OWNER_ID]):
    raise ValueError("Missing essential environment variables (API_ID, API_HASH, BOT_TOKEN, DATABASE_URI, OWNER_ID)")
if not cfg.INDEX_CHANNELS:
     print("Warning: INDEX_CHANNELS is not set. The bot cannot index or search media without it.")
if not cfg.LOG_CHANNEL:
     print("Warning: LOG_CHANNEL is not set. Bot errors and status updates will not be logged.")
