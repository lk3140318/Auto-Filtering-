# main.py
import os
import time
import datetime
import logging
import asyncio
import random
import math  # Ensure math is imported
from pyrogram import Client, filters, enums
from pyrogram.errors import UserNotParticipant, FloodWait, PeerIdInvalid, ChannelPrivate, RPCError
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import cfg
from database import db

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Pyrogram Client Initialization ---
# plugins = dict(root="plugins") # Although not used directly here, good practice for structure

app = Client(
    "AutoFilterBot",
    api_id=cfg.API_ID,
    api_hash=cfg.API_HASH,
    bot_token=cfg.BOT_TOKEN,
    # plugins=plugins # If using separate plugin files
)

# --- Helper Functions ---

async def is_admin(user_id: int) -> bool:
    """Check if user is the owner."""
    return user_id == cfg.OWNER_ID

async def is_req_grp_admin(message: Message) -> bool:
    """Check if user is admin in the group they sent the command."""
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        try:
            member = await app.get_chat_member(message.chat.id, message.from_user.id)
            return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
        except Exception:
            return False
    return False # Not applicable in private chats

async def get_user_status(user_id: int):
    """Get user's ban status."""
    banned = await db.is_user_banned(user_id)
    return "Banned" if banned else "Active"

def get_readable_size(size_bytes):
    """Converts bytes to a readable format."""
    if size_bytes is None or size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    try:
        i = int(math.floor(math.log(abs(size_bytes), 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        # Ensure index i is within bounds
        if i < len(size_name):
            return f"{s} {size_name[i]}"
        else:
             # Handle extremely large sizes beyond YB if necessary
             return f"{size_bytes} B" # Fallback to bytes
    except ValueError: # Handles log(0) or negative numbers if abs wasn't used
        return "0 B"
    except Exception: # Generic fallback
        return f"{size_bytes} B"


async def get_media_link(channel_id, message_id):
    """Generate a direct link to the media message."""
    link = f"https://t.me/"
    if isinstance(channel_id, int) and str(channel_id).startswith("-100"): # Private channel
        link += f"c/{str(channel_id).replace('-100', '')}/{message_id}"
    elif isinstance(channel_id, str) and channel_id.startswith('@'): # Public channel username
        try:
            chat = await app.get_chat(channel_id)
            link += f"{chat.username}/{message_id}"
        except Exception:
             logger.warning(f"Could not get chat for username {channel_id}, using direct username link.")
             link += f"{channel_id.replace('@', '')}/{message_id}" # Fallback to direct username link

    else: # Public channel ID (less common) or other cases
        # This is often incorrect for public channels; username is preferred.
        # Let's try to handle numeric public IDs better if possible, though less common.
        # A better approach would be to always store username for public channels if available.
        # Fallback/guess for numeric public ID or potentially misconfigured private ID without -100
         try:
            chat = await app.get_chat(channel_id)
            if chat.username:
                 link += f"{chat.username}/{message_id}"
            else: # Numeric ID, likely private/supergroup - try the 'c/' format
                 link += f"c/{abs(channel_id)}/{message_id}" # Best guess
         except Exception:
            logger.warning(f"Could not get chat for ID {channel_id}, using best-guess link.")
            link += f"c/{abs(channel_id)}/{message_id}" # Fallback guess

    return link

async def log_message(message: str):
    """Sends a message to the log channel."""
    if cfg.LOG_CHANNEL:
        try:
            await app.send_message(cfg.LOG_CHANNEL, text=message, disable_web_page_preview=True)
        except PeerIdInvalid:
             logger.error(f"Log channel ID {cfg.LOG_CHANNEL} is invalid. Make sure it's correct and the bot is in the channel.")
        except ChannelPrivate:
             logger.error(f"Bot is not an admin in the log channel {cfg.LOG_CHANNEL} or the channel is private/inaccessible.")
        except Exception as e:
            logger.error(f"Failed to log message to channel {cfg.LOG_CHANNEL}: {e}")
    else:
        logger.info(f"Log message (LOG_CHANNEL not set): {message}")


# --- Force Subscribe Handler ---
async def check_force_sub(message: Message):
    """Check if user is subscribed to the updates channel."""
    if not cfg.UPDATES_CHANNEL:
        return True # No force sub configured

    user_id = message.from_user.id
    if await is_admin(user_id): # Admins skip force sub
        return True

    try:
        member = await app.get_chat_member(cfg.UPDATES_CHANNEL, user_id)
        # Allow members and admins/owners/restricted users
        if member.status not in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
            return True
    except UserNotParticipant:
        # User is not in the channel, proceed to show join message
        pass
    except PeerIdInvalid:
        await log_message(f"Error: Force Subscribe Channel ID/Username '{cfg.UPDATES_CHANNEL}' is invalid.")
        # Allow user to proceed if channel is misconfigured, but log error
        return True
    except ChannelPrivate:
         await log_message(f"Error: Bot is not an admin in the Force Subscribe Channel '{cfg.UPDATES_CHANNEL}' or channel is private/inaccessible.")
         # Allow user to proceed if channel is misconfigured, but log error
         return True
    except RPCError as e:
         await log_message(f"RPCError checking Force Subscribe for {user_id} in {cfg.UPDATES_CHANNEL}: {e}")
         # Allow user to proceed on RPC errors to avoid blocking users unnecessarily
         return True
    except Exception as e:
        await log_message(f"Unexpected error checking Force Subscribe for {user_id} in {cfg.UPDATES_CHANNEL}: {e}")
        # Allow user to proceed on unknown errors
        return True

    # If user needs to join (UserNotParticipant or Left status)
    try:
        channel = await app.get_chat(cfg.UPDATES_CHANNEL)
        channel_link = channel.invite_link or f"https://t.me/{channel.username}" if channel.username else None
        channel_name = channel.title

        if not channel_link:
             # Fallback if no invite link and no username - cannot generate join button
             await log_message(f"Warning: Force Subscribe channel {cfg.UPDATES_CHANNEL} has no username or accessible invite link. Cannot enforce.")
             return True # Allow user if we can't provide a link

        fsub_msg = cfg.FORCE_SUB_MSG.format(
            mention=message.from_user.mention,
            channel_name=channel_name,
            channel_link=channel_link
        )
        button = [[InlineKeyboardButton("👉 Join Channel 👈", url=channel_link)]]
        await message.reply_text(fsub_msg, reply_markup=InlineKeyboardMarkup(button), disable_web_page_preview=True)
        return False # User failed the check
    except Exception as e:
        await log_message(f"Error sending Force Subscribe message or getting channel details for {cfg.UPDATES_CHANNEL}: {e}")
        # Allow user to proceed if we can't even send the join message
        return True

# --- Bot Command Handlers ---

@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username

    # Add or update user in DB
    await db.add_user(user_id, first_name, username)

    # Check Force Subscribe
    if not await check_force_sub(message):
        return # Stop processing if user needs to subscribe

    # Send welcome message
    start_text = cfg.START_MSG.format(mention=message.from_user.mention)
    photo_url = random.choice(cfg.PICS) if cfg.PICS else None

    # Prepare buttons
    buttons = []
    help_updates_row = []
    help_updates_row.append(InlineKeyboardButton("ℹ️ Help", callback_data="help_cb"))
    if cfg.UPDATES_CHANNEL:
         try:
            # Attempt to create a direct link
            fsub_chat = await app.get_chat(cfg.UPDATES_CHANNEL)
            fsub_link = fsub_chat.invite_link or (f"https://t.me/{fsub_chat.username}" if fsub_chat.username else "#")
            if fsub_link != "#":
                 help_updates_row.append(InlineKeyboardButton("📢 Updates", url=fsub_link))
         except Exception as e:
             logger.warning(f"Could not generate updates channel link for start button: {e}")
    buttons.append(help_updates_row)

    # Add request button if URL is set
    if cfg.REQUEST_MOVIE_URL and cfg.REQUEST_MOVIE_BUTTON_TEXT:
        buttons.append([InlineKeyboardButton(cfg.REQUEST_MOVIE_BUTTON_TEXT, url=cfg.REQUEST_MOVIE_URL)])


    try:
        if photo_url:
            await message.reply_photo(
                photo=photo_url,
                caption=start_text,
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
            )
        else:
            await message.reply_text(
                start_text,
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                disable_web_page_preview=True
            )
        await log_message(f"User {user_id} ({first_name}) started the bot.")
    except Exception as e:
        logger.error(f"Error sending start message to {user_id}: {e}")
        # Fallback to text message if photo fails or for other errors
        try:
            await message.reply_text(
                start_text,
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                disable_web_page_preview=True
            )
        except Exception as fallback_e:
            logger.error(f"Fallback start message failed for {user_id}: {fallback_e}")
            await log_message(f"CRITICAL: Could not send start message to {user_id}. Error: {fallback_e}")


@app.on_message(filters.command("help") & filters.private)
async def help_command(client: Client, message: Message):
     # Check Force Subscribe first
     if not await check_force_sub(message):
         return

     is_owner = await is_admin(message.from_user.id)

     help_text = """**Auto Filter Bot Help**

**How it works:**
1. Add me to your group.
2. Make sure the bot owner has configured channels for me to index.
3. Send any movie or series name in the group.
4. I will search the indexed channels and provide links if found.

**Group Usage:**
- Just send the name of the media you want to find!

**Note:** Force Subscription might be enabled. If so, you must join the specified channel to use the bot.
    """

     admin_help_text = """

**Available Commands (Owner Only):**
- `/start`: Check if the bot is alive.
- `/help`: Show this help message.
- `/index [channel_id/username]`: Start indexing media from a specific channel. The bot must be an admin in the channel. Use `-100...` format for private channel IDs.
- `/index`: Index all channels listed in `INDEX_CHANNELS` ENV variable.
- `/clearindex [channel_id/username]`: Remove all indexed data for a specific channel. Use with caution!
- `/clearindex all`: **DANGEROUS!** Deletes ALL indexed media. Requires confirmation.
- `/status` or `/stats`: Show bot statistics.
- `/broadcast [message]` or reply `/broadcast`: Send a message to all users. (Use with caution!)
- `/ban [user_id] [reason (optional)]`: Ban a user from using the bot.
- `/unban [user_id]`: Unban a user.
- `/banned`: List banned users.
    """

     if is_owner:
         help_text += admin_help_text

     await message.reply_text(help_text, disable_web_page_preview=True)


# --- Admin Commands ---

@app.on_message(filters.command(["status", "stats"]) & filters.user(cfg.OWNER_ID))
async def status_command(client: Client, message: Message):
    try:
        total_users = await db.total_users_count()
        banned_users = await db.total_banned_users_count()
        active_users = total_users - banned_users
        total_media = await db.total_media_count()
        db_stats = await db.db.command('dbstats') # Get DB stats
        db_size = get_readable_size(db_stats['dataSize'])
        storage_size = get_readable_size(db_stats['storageSize'])

        # Bot uptime calculation
        bot_uptime = "N/A"
        if 'START_TIME' in globals():
             uptime_delta = datetime.datetime.now() - START_TIME
             bot_uptime = str(uptime_delta).split('.')[0] # Remove microseconds

        status_text = f"""**Bot Status** ✨

📊 **Users:**
   - Total Users: `{total_users}`
   - Active Users: `{active_users}`
   - Banned Users: `{banned_users}`

🎬 **Media:**
   - Total Indexed Files: `{total_media}`

🗄️ **Database:**
   - DB Name: `{cfg.DATABASE_NAME}`
   - Data Size: `{db_size}`
   - Storage Size: `{storage_size}`

⚙️ **Configuration:**
   - Force Subscribe: `{cfg.UPDATES_CHANNEL if cfg.UPDATES_CHANNEL else 'Disabled'}`
   - Log Channel: `{cfg.LOG_CHANNEL if cfg.LOG_CHANNEL else 'Disabled'}`
   - Index Channels: `{', '.join(map(str, cfg.INDEX_CHANNELS)) if cfg.INDEX_CHANNELS else 'None Configured'}`

⏰ Bot Uptime: `{bot_uptime}`
    """
        await message.reply_text(status_text, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        await message.reply_text(f"Failed to retrieve bot status. Error: {e}")
        await log_message(f"Error in /status command: {e}")


@app.on_message(filters.command("broadcast") & filters.user(cfg.OWNER_ID))
async def broadcast_command(client: Client, message: Message):
    if not message.reply_to_message and len(message.command) < 2:
        await message.reply_text("Usage: `/broadcast [message]` or reply to a message with `/broadcast`")
        return

    bcast_msg = message.reply_to_message if message.reply_to_message else message # The message to broadcast
    query_msg = message # The command message itself
    text_to_send = message.text.split(None, 1)[1] if len(message.command) > 1 and not message.reply_to_message else None

    total_users = await db.total_users_count()
    banned_users_count = await db.total_banned_users_count()
    eligible_users = total_users - banned_users_count

    if eligible_users == 0:
        await query_msg.reply_text("No active users found to broadcast to.")
        return

    confirm_msg = await query_msg.reply_text(
        f"Starting broadcast to approximately {eligible_users} active users...\n\n"
        "**Warning:** This can take a while and might hit Telegram limits. "
        #"Send `/cancel` to stop." # Cancel feature is tricky to implement reliably here
    )

    start_time = time.time()
    sent_count = 0
    failed_count = 0
    users_cursor = db.get_all_users() # Get active users cursor

    async for user in users_cursor:
        user_id = user['user_id']
        try:
            if message.reply_to_message:
                 # Forward the replied message
                 await bcast_msg.forward(user_id)
            elif text_to_send:
                 # Send the text provided after /broadcast
                 await client.send_message(user_id, text_to_send)
            else:
                 # Should not happen based on initial check, but as a fallback
                 await confirm_msg.edit_text("Error: No message content found to broadcast. Aborting.")
                 await log_message("Broadcast aborted: No content found.")
                 return

            sent_count += 1
            # Sleep to avoid flood limits
            await asyncio.sleep(cfg.SLEEP_TIME_BCAST)

        except FloodWait as fw:
            logger.warning(f"FloodWait during broadcast: Waiting {fw.value} seconds.")
            await log_message(f"FloodWait encountered during broadcast. Sleeping for {fw.value}s.")
            await asyncio.sleep(fw.value + 5) # Wait longer than required
             # Retry sending to the same user after waiting
            try:
                 if message.reply_to_message:
                     await bcast_msg.forward(user_id)
                 elif text_to_send:
                     await client.send_message(user_id, text_to_send)
                 sent_count += 1 # Count as success if retry works
                 await asyncio.sleep(cfg.SLEEP_TIME_BCAST) # Sleep after retry too
            except Exception as retry_e:
                 logger.error(f"Retry failed sending broadcast to {user_id}: {retry_e}")
                 failed_count += 1
                 await db.set_ban_status(user_id, True) # Ban user if persistent error
                 await log_message(f"Broadcast failed for user {user_id} after FloodWait. Error: {retry_e}. User banned.")

        except (PeerIdInvalid, ChannelPrivate): # User deleted account or similar issues
             logger.warning(f"User ID {user_id} is invalid or inaccessible. Skipping & Banning.")
             failed_count += 1
             await db.set_ban_status(user_id, True) # Ban invalid users
        except RPCError as rpc_err:
             logger.error(f"RPCError sending broadcast to {user_id}: {rpc_err}")
             failed_count += 1
             # Ban user if they blocked the bot or deactivated account etc.
             if "USER_IS_BLOCKED" in str(rpc_err) or "PEER_ID_INVALID" in str(rpc_err) or "USER_DEACTIVATED" in str(rpc_err):
                await db.set_ban_status(user_id, True)
                await log_message(f"Broadcast failed for user {user_id} due to {rpc_err}. User banned.")
             else:
                await log_message(f"Broadcast failed for user {user_id} due to RPCError: {rpc_err}. User NOT banned.")
        except Exception as e:
            logger.error(f"Unexpected error sending broadcast to {user_id}: {e}")
            failed_count += 1
            # Ban on generic errors too, as they likely indicate persistent issues
            await db.set_ban_status(user_id, True)
            await log_message(f"Broadcast failed for user {user_id} due to unexpected error: {e}. User banned.")

        # Update progress message occasionally (e.g., every 20 users)
        if (sent_count + failed_count) % 20 == 0 and (sent_count + failed_count) > 0:
            elapsed_time = time.time() - start_time
            try:
                await confirm_msg.edit_text(
                    f"Broadcast in progress...\n\n"
                    f"Sent: {sent_count}\n"
                    f"Failed: {failed_count}\n"
                    f"Total processed: {sent_count + failed_count} / {eligible_users}\n"
                    f"Elapsed Time: {str(datetime.timedelta(seconds=int(elapsed_time)))}\n\n"
                    #"Send `/cancel` to stop."
                )
            except FloodWait as fw:
                logger.warning(f"FloodWait while editing broadcast status: {fw.value}s")
                await asyncio.sleep(fw.value) # Wait if editing status message gets throttled
            except Exception as edit_err:
                logger.warning(f"Could not edit broadcast status message: {edit_err}")
                pass # Ignore errors editing status msg

    end_time = time.time()
    total_time = str(datetime.timedelta(seconds=int(end_time - start_time)))
    final_text = f"""Broadcast Finished! ✅

Sent: `{sent_count}`
Failed: `{failed_count}` (Users who failed have been banned)
Total Time: `{total_time}`
    """
    try:
         await confirm_msg.edit_text(final_text) # Try editing one last time
    except Exception:
         await query_msg.reply_text(final_text) # Send as new message if editing fails

    await log_message(f"Broadcast Summary:\n{final_text}")


@app.on_message(filters.command("ban") & filters.user(cfg.OWNER_ID))
async def ban_command(client: Client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        # Allow banning via reply
        if message.reply_to_message and message.reply_to_message.from_user:
            user_id_to_ban = message.reply_to_message.from_user.id
            reason = " ".join(message.command[1:]) if len(message.command) > 1 else "No reason specified."
        else:
            await message.reply_text("Usage: `/ban [user_id] [reason]` or reply to a user's message with `/ban [reason]`")
            return
    else:
        user_id_to_ban = int(message.command[1])
        reason = " ".join(message.command[2:]) if len(message.command) > 2 else "No reason specified."

    if user_id_to_ban == cfg.OWNER_ID:
        await message.reply_text("Cannot ban the owner.")
        return
    if user_id_to_ban == client.me.id:
         await message.reply_text("Cannot ban myself.")
         return

    # Check if user exists in DB (optional, but good)
    user_info = await db.get_user(user_id_to_ban)
    target_info = f"`{user_id_to_ban}`"
    if user_info:
        target_info = f"{user_info.get('first_name', 'User')} (`{user_id_to_ban}`)"


    await db.set_ban_status(user_id_to_ban, True)
    await message.reply_text(f"User {target_info} has been banned. Reason: {reason}")
    await log_message(f"User {user_id_to_ban} banned by owner {cfg.OWNER_ID}. Reason: {reason}")

    # Try notifying the banned user
    try:
        await client.send_message(user_id_to_ban, f"You have been banned from using this bot. Reason: {reason}")
    except Exception:
        pass # Ignore if user blocked the bot etc.

@app.on_message(filters.command("unban") & filters.user(cfg.OWNER_ID))
async def unban_command(client: Client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        # Allow unbanning via reply
        if message.reply_to_message and message.reply_to_message.from_user:
             user_id_to_unban = message.reply_to_message.from_user.id
        else:
            await message.reply_text("Usage: `/unban [user_id]` or reply to a user's message with `/unban`")
            return
    else:
         user_id_to_unban = int(message.command[1])

    # Check if user exists in DB (optional)
    user_info = await db.get_user(user_id_to_unban)
    target_info = f"`{user_id_to_unban}`"
    if user_info:
        target_info = f"{user_info.get('first_name', 'User')} (`{user_id_to_unban}`)"


    await db.set_ban_status(user_id_to_unban, False)
    await message.reply_text(f"User {target_info} has been unbanned.")
    await log_message(f"User {user_id_to_unban} unbanned by owner {cfg.OWNER_ID}.")

    # Try notifying the unbanned user
    try:
        await client.send_message(user_id_to_unban, "You have been unbanned and can now use this bot again.")
    except Exception:
        pass

@app.on_message(filters.command("banned") & filters.user(cfg.OWNER_ID))
async def list_banned_command(client: Client, message: Message):
    banned_users_cursor = db.get_all_banned_users()
    banned_list = []
    count = 0
    async for user in banned_users_cursor:
        user_id = user['user_id']
        first_name = user.get('first_name', 'N/A')
        username = user.get('username', None)
        user_info = f"- `{user_id}` ({first_name}" + (f" @{username}" if username else "") + ")"
        banned_list.append(user_info)
        count += 1

    if not banned_list:
        await message.reply_text("No users are currently banned.")
        return

    header = f"**Banned Users ({count}):**\n\n"
    banned_text = header + "\n".join(banned_list)

    # Handle potential message length limits
    if len(banned_text) > 4096:
        # Split into multiple messages or send as file
        output = f"Found {count} banned users. List is too long for one message.\n\n"
        temp_msg = output
        for item in banned_list:
            if len(temp_msg) + len(item) + 1 < 4096:
                temp_msg += item + "\n"
            else:
                await message.reply_text(temp_msg)
                temp_msg = item + "\n"
                await asyncio.sleep(1) # Avoid floodwait
        if temp_msg != output: # Send remaining part
             await message.reply_text(temp_msg)

        # Alternative: Send as file
        # try:
        #     with open("banned_users.txt", "w") as f:
        #         f.write(f"Total Banned: {count}\n\n")
        #         f.write("\n".join([u.replace("- ","") for u in banned_list])) # cleaner list for file
        #     await message.reply_document("banned_users.txt", caption=f"{count} Banned Users")
        #     os.remove("banned_users.txt")
        # except Exception as file_err:
        #      logger.error(f"Error creating banned users file: {file_err}")
        #      await message.reply_text("Could not generate banned list file.")

    else:
        await message.reply_text(banned_text)


# --- Indexing Commands ---

async def index_channel(client: Client, command_message: Message, channel_target):
    """Helper function to index a single channel."""
    status_msg = await command_message.reply_text(f"⏳ Resolving channel `{channel_target}`...")
    processed_count = 0
    skipped_count = 0
    failed_count = 0
    total_messages_scanned = 0

    try:
        # Resolve channel ID/username
        try:
            target_chat = await client.get_chat(channel_target)
            channel_id = target_chat.id
            channel_name = target_chat.title or target_chat.username or f"ID: {channel_id}"
            await status_msg.edit_text(f"✅ Found channel: **{channel_name}** (`{channel_id}`)\n\n⏳ Starting message scan...")
            await log_message(f"Starting indexing for {channel_name} ({channel_id}) triggered by owner.")
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: Could not access channel `{channel_target}`. Ensure the bot is an admin or the username/ID is correct.\nDetails: `{e}`")
            await log_message(f"Indexing failed for {channel_target}. Could not get chat info. Error: {e}")
            return

        # --- Get chat history ---
        # Consider adding a limit or date range if needed for very large channels
        # Or implement resuming logic using last indexed message ID from DB
        async for msg in client.get_chat_history(channel_id):
            total_messages_scanned += 1

            # --- Filter Media ---
            media = msg.video or msg.document or msg.audio # Add msg.photo if needed
            if not media:
                skipped_count += 1
                continue # Skip messages without relevant media

            # --- Extract Details ---
            file_id = media.file_id
            file_name = getattr(media, 'file_name', None)
            caption = msg.caption or ""
            file_size = getattr(media, 'file_size', 0)
            media_type = msg.media.value if msg.media else "unknown" # e.g., 'video', 'document'

            if not file_name:
                 # Try to generate a fallback name
                 ext = getattr(media, 'mime_type', '').split('/')[-1] or 'file'
                 file_name = f"{media_type}_{msg.id}.{ext}"

            # --- Optional: Adult Content Filter (Basic Keyword Check) ---
            # adult_keywords = ["xxx", "porn", "18+", "adult"] # Example keywords
            # combined_text = (file_name + " " + caption).lower()
            # if any(keyword in combined_text for keyword in adult_keywords):
            #     logger.info(f"Skipping potential adult content (Msg ID {msg.id} in {channel_id}): {file_name}")
            #     skipped_count += 1
            #     await log_message(f"Skipped potential adult content in {channel_name} (Msg {msg.id}): {file_name}")
            #     continue
            # ------------------------------------------------------------

            # --- Add to Database ---
            try:
                await db.add_media(
                    channel_id=channel_id,
                    message_id=msg.id,
                    file_id=file_id,
                    file_name=file_name,
                    caption=caption.html if caption else None, # Store caption as HTML
                    file_type=media_type,
                    file_size=file_size
                )
                processed_count += 1

            except Exception as db_err:
                logger.error(f"Error adding media (Msg ID: {msg.id}, Channel: {channel_id}) to DB: {db_err}")
                failed_count += 1
                # Don't log every single DB error to avoid spam, maybe sample?
                if failed_count % 10 == 0:
                     await log_message(f"DB Error count reached {failed_count} during indexing of {channel_name}. Last error: {db_err}")

            # --- Update Status Periodically ---
            if total_messages_scanned % 200 == 0: # Update every 200 messages scanned
                 try:
                     await status_msg.edit_text(
                         f"⏳ Indexing **{channel_name}**...\n\n"
                         f"💬 Scanned: {total_messages_scanned}\n"
                         f"✅ Added/Updated: {processed_count}\n"
                         f"⏭️ Skipped: {skipped_count}\n"
                         f"❌ Failed DB Writes: {failed_count}\n"
                         f"📄 Last checked msg ID: {msg.id}\n\n"
                         "This can take a while..."
                     )
                     # Small delay to avoid hitting edit limits too aggressively, and yield control
                     await asyncio.sleep(1.5)
                 except FloodWait as fw:
                      logger.warning(f"FloodWait while editing index status: {fw.value}s. Sleeping.")
                      await asyncio.sleep(fw.value + 5)
                 except Exception as edit_err:
                      logger.warning(f"Could not edit index status message: {edit_err}")
                      # Continue indexing even if status update fails


    except FloodWait as fw:
         wait_time = fw.value + 5
         await status_msg.edit_text(f"⚠️ FloodWait encountered while getting history for {channel_name}. Pausing for {wait_time} seconds...")
         await log_message(f"FloodWait during indexing of {channel_name}. Sleeping for {wait_time}s.")
         await asyncio.sleep(wait_time)
         await status_msg.edit_text(f"⏳ Resuming indexing for {channel_name} (may restart scan depending on implementation).")
         # Ideally, implement resuming logic here based on last scanned ID
         # For now, it might restart the scan from the beginning after a FloodWait
    except (ChannelPrivate, PeerIdInvalid) as access_err:
         error_text = f"❌ Error: Could not access channel **{channel_name}** (`{channel_id}`) during indexing. Bot might have been kicked or permissions changed.\n`{access_err}`"
         await status_msg.edit_text(error_text)
         await log_message(f"Indexing stopped for {channel_name} ({channel_id}). Access Error: {access_err}")
         return # Stop if access is lost
    except Exception as e:
        error_text = f"❌ An unexpected error occurred during indexing of `{channel_target}`:\n`{e}`\n\nCheck logs for details."
        await status_msg.edit_text(error_text)
        logger.error(f"Unexpected indexing error for {channel_target}: {e}", exc_info=True) # Log full traceback
        await log_message(f"Indexing failed unexpectedly for {channel_target}. Error: {e}")
        return # Stop on major errors

    # Indexing finished for this channel
    final_status = f"""✅ Indexing Finished for **{channel_name}** (`{channel_id}`)

💬 Total Messages Scanned: `{total_messages_scanned}`
✅ Added/Updated Media: `{processed_count}`
⏭️ Skipped (Non-media/Filtered): `{skipped_count}`
❌ Failed DB Writes: `{failed_count}`
"""
    try:
         await status_msg.edit_text(final_status)
    except Exception as final_edit_err:
         logger.warning(f"Could not edit final index status: {final_edit_err}")
         await command_message.reply_text(final_status) # Send as new msg if edit fails
    await log_message(f"Indexing Summary for {channel_name}:\n{final_status}")


@app.on_message(filters.command("index") & filters.user(cfg.OWNER_ID))
async def index_command(client: Client, message: Message):
    target_channels = []
    if len(message.command) > 1:
        # Index specific channel provided
        channel_arg = message.command[1]
        # Try converting to int first for IDs, otherwise treat as username
        try:
             target_channels.append(int(channel_arg))
        except ValueError:
             if channel_arg.startswith('@') or not channel_arg.startswith('-'): # Allow usernames or public channel links without @
                 target_channels.append(channel_arg)
             else:
                await message.reply_text("Invalid channel format. Use username (e.g., `@mychannel`), public link, or numeric ID (e.g., `-100123456789`).")
                return
    else:
        # Index all channels from ENV config
        if not cfg.INDEX_CHANNELS:
             await message.reply_text("No channels specified in the command and `INDEX_CHANNELS` ENV variable is not set.")
             return
        target_channels = cfg.INDEX_CHANNELS

    if not target_channels:
        await message.reply_text("No valid channels found to index.")
        return

    await message.reply_text(f"🚀 Indexing process initiated for {len(target_channels)} channel(s): `{', '.join(map(str, target_channels))}`")
    await log_message(f"Owner {cfg.OWNER_ID} triggered indexing for: {', '.join(map(str, target_channels))}")

    for i, channel in enumerate(target_channels):
        await index_channel(client, message, channel)
        if i < len(target_channels) - 1: # If not the last channel
            await message.reply_text(f"Finished indexing `{channel}`. Starting next channel in 5 seconds...")
            await asyncio.sleep(5) # Small delay between channels

    await message.reply_text("✅ All requested indexing tasks have been processed.")

@app.on_message(filters.command("clearindex") & filters.user(cfg.OWNER_ID))
async def clear_index_command(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: `/clearindex [channel_id/username]` or `/clearindex all`\n**WARNING:** `/clearindex all` deletes EVERYTHING!")
        return

    target = message.command[1]

    if target.lower() == "all":
         confirm_text = ("⚠️ **EXTREME WARNING!** ⚠️\n\n"
                         "You are about to delete **ALL** indexed media data from the database. "
                         "This action is **IRREVERSIBLE** and will require re-indexing everything.\n\n"
                         "Type `YES I AM ABSOLUTELY SURE` to confirm.")
         confirm_msg = await message.reply_text(confirm_text)
         try:
             # Use conversation utilities if available, or simple listener
             response = await client.listen(
                 chat_id=message.chat.id,
                 filters=filters.user(cfg.OWNER_ID) & filters.text,
                 timeout=30 # 30 second timeout
             )
             if response and response.text == "YES I AM ABSOLUTELY SURE":
                 await confirm_msg.edit_text("⏳ Deleting all indexed media data...")
                 deleted_media = await db.media.delete_many({}) # Delete all media
                 deleted_settings = await db.settings.delete_many({"_id": {"$regex": "^last_indexed_"}}) # Delete all index markers
                 result_count = deleted_media.deleted_count
                 await confirm_msg.edit_text(f"✅ Successfully deleted all (`{result_count}`) indexed media items and associated settings.")
                 await log_message(f"🚨 Owner {cfg.OWNER_ID} cleared ALL indexed data ({result_count} items).")
             else:
                 await confirm_msg.edit_text("❌ Deletion cancelled or incorrect confirmation.")
         except asyncio.TimeoutError:
             await confirm_msg.edit_text("❌ Confirmation timed out. Deletion cancelled.")
         except Exception as e:
              await confirm_msg.edit_text(f"❌ An error occurred during confirmation: {e}")
              logger.error(f"Error during /clearindex all confirmation: {e}")
         return

    # Clear specific channel
    status_msg = await message.reply_text(f"⏳ Trying to resolve channel `{target}` to clear its index...")
    try:
        # Resolve channel ID/username
        try:
             target_chat = await client.get_chat(target if not target.isdigit() else int(target))
             channel_id = target_chat.id
             channel_name = target_chat.title or target_chat.username or f"ID: {channel_id}"
        except ValueError: # Handle non-integer IDs that aren't usernames cleanly
             await status_msg.edit_text(f"❌ Error: Invalid format for channel `{target}`. Use username or numeric ID.")
             return
        except Exception as e:
             await status_msg.edit_text(f"❌ Error: Could not find or access channel `{target}`. Details: `{e}`")
             return

        await status_msg.edit_text(f"⏳ Clearing index data for channel **{channel_name}** (`{channel_id}`)...")
        deleted_count = await db.delete_media_by_channel(channel_id) # Deletes media and settings for this channel
        await status_msg.edit_text(f"✅ Cleared `{deleted_count}` indexed items for channel **{channel_name}** (`{channel_id}`).")
        await log_message(f"Owner {cfg.OWNER_ID} cleared index for channel {channel_name} ({channel_id}). Deleted {deleted_count} items.")

    except Exception as e:
        await status_msg.edit_text(f"❌ An unexpected error occurred while clearing index for `{target}`:\n`{e}`")
        logger.error(f"Error clearing index for {target}: {e}")


# --- Group Message Filter Handler ---

# CORRECTED LINE: Added () after filters.command
@app.on_message(filters.group & filters.text & ~filters.command() & ~filters.via_bot & ~filters.forwarded)
async def group_filter_handler(client: Client, message: Message):
    if not message.from_user:
        return # Ignore messages without a user (e.g., channel posts in group)

    user_id = message.from_user.id

    # 1. Check if user is banned
    if await db.is_user_banned(user_id):
        # Ignore banned users silently in groups
        # logger.info(f"Ignoring message from banned user {user_id} in group {message.chat.id}")
        return

    # 2. Check Force Subscribe
    if not await check_force_sub(message):
        return # Stop if user needs to subscribe

    # 3. Add/Update user in DB (do this even if no results are found, tracks activity)
    # Consider doing this less frequently if DB performance is an issue
    # await db.add_user(user_id, message.from_user.first_name, message.from_user.username)

    # 4. Process the search query
    query = message.text.strip()
    # More robust filtering: ignore short, purely numeric, or single emoji/symbol messages
    if len(query) < 3 or query.isdigit() or (len(query) == 1 and not query.isalnum()):
        return

    start_time = time.time()
    logger.info(f"Group {message.chat.id}: User {user_id} searching for '{query}'")

    try:
        # Use the search function from database.py
        results = await db.search_media(query, max_results=cfg.MAX_RESULTS)
        end_time = time.time()
        search_time = end_time - start_time

        if not results:
            logger.info(f"No results found for '{query}' by {user_id}. Time: {search_time:.3f}s")
            # Optional: Send "Not Found" message with Request button ONLY if configured
            if cfg.REQUEST_MOVIE_URL and cfg.REQUEST_MOVIE_BUTTON_TEXT:
                 button = [[InlineKeyboardButton(cfg.REQUEST_MOVIE_BUTTON_TEXT, url=cfg.REQUEST_MOVIE_URL)]]
                 try:
                    await message.reply_text(
                        cfg.NOT_FOUND_MSG.format(query=query),
                        reply_markup=InlineKeyboardMarkup(button),
                        disable_web_page_preview=True,
                        quote=True # Reply to the user's query message
                    )
                 except Exception as e:
                     logger.error(f"Error sending 'Not Found' message: {e}")
            # else: Do nothing if no results and no request button configured (reduces group spam)
            return

        # 5. Format and send results
        result_text_header = f"✨ Found Results for: **{query}**\n_{len(results)} items retrieved in {search_time:.2f}s_\n\n"
        result_text_body = ""
        buttons = []

        for i, item in enumerate(results):
            file_name = item.get('file_name', 'Unknown File')
            file_size = get_readable_size(item.get('file_size', 0))
            channel_id = item['channel_id']
            message_id = item['message_id']

            # Try/catch link generation as it might fail for inaccessible channels/messages
            try:
                link = await get_media_link(channel_id, message_id)
                # Append file info to the text body
                result_text_body += f"**{i+1}. {file_name}** [{file_size}]\n"
                # Create button with link
                # Truncate long names in buttons for better display
                button_text = f"{i+1}. {file_name[:45]}" + ("..." if len(file_name) > 45 else "")
                buttons.append([InlineKeyboardButton(button_text, url=link)])
            except Exception as link_err:
                 logger.error(f"Error generating link for msg {message_id} in chan {channel_id}: {link_err}")
                 result_text_body += f"**{i+1}. {file_name}** [{file_size}] - _Error getting link_\n"
                 # Optionally add a placeholder button or skip it
                 # buttons.append([InlineKeyboardButton(f"{i+1}. {file_name[:40]}... (Link Error)", callback_data="link_error_cb")])


        # Add a final row for Request Movie if configured and results were found
        if cfg.REQUEST_MOVIE_URL and cfg.REQUEST_MOVIE_BUTTON_TEXT:
             buttons.append([InlineKeyboardButton(cfg.REQUEST_MOVIE_BUTTON_TEXT, url=cfg.REQUEST_MOVIE_URL)])

        final_text = result_text_header + result_text_body

        # Send the message
        try:
            await message.reply_text(
                final_text,
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                disable_web_page_preview=True, # Disable link previews in the message body
                quote=True # Reply to the user's query message
            )
            logger.info(f"Sent {len(results)} results for '{query}' to {user_id}. Time: {search_time:.3f}s")
            # Update user activity after successful interaction
            await db.add_user(user_id, message.from_user.first_name, message.from_user.username)

        except Exception as send_err:
             logger.error(f"Error sending search results for '{query}' to {user_id}: {send_err}")
             await log_message(f"Error sending results for query '{query}' in chat {message.chat.id}. User: {user_id}. Error: {send_err}")
             # Maybe send a simple error message back to the user
             try:
                 await message.reply_text("Sorry, an error occurred while sending the results. Please try again.", quote=True)
             except Exception:
                 pass # Ignore if even error sending fails

    except Exception as e:
        logger.error(f"Error processing group message filter for query '{query}' by {user_id}: {e}", exc_info=True)
        await log_message(f"Critical Error in group filter for query '{query}' in chat {message.chat.id}. User: {user_id}. Error: {e}")
        # Notify the user an internal error occurred
        try:
            await message.reply_text("Sorry, an internal error occurred while searching. The admin has been notified.", quote=True)
        except Exception:
            pass # Ignore if error sending fails


# --- Callback Query Handler (for buttons like Help) ---

@app.on_callback_query()
async def callback_query_handler(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id

    # Optional: Check Force Subscribe on callbacks too?
    # Requires constructing a mock Message object which can be complex.
    # Simpler approach: Rely on the check during command/message execution.
    # If you need strict check here, you'd fetch message context.

    # Check if user is banned before processing callbacks
    if await db.is_user_banned(user_id):
        try:
             await callback_query.answer("You are banned from using this bot.", show_alert=True)
        except Exception: pass # Ignore errors answering callback for banned user
        return


    if data == "help_cb":
        await callback_query.answer() # Answer the callback first
        is_owner = await is_admin(user_id)
        # Reuse the help text logic
        help_text = """**How to Use Me:**
- Add me to your group.
- Ensure the owner has set up channels for me to index.
- Send the name of a movie or series in the group.
- I will search and provide links!

For group admins, please ensure the bot has necessary permissions.
        """
        admin_help_text = "\n\n**Admin Info:** Check `/help` in our private chat for owner commands."

        if is_owner:
            help_text += admin_help_text

        # Edit the original message if possible
        try:
            # Check if message exists and has markup before editing
            if callback_query.message and callback_query.message.reply_markup:
                await callback_query.edit_message_text(
                     help_text,
                     reply_markup=callback_query.message.reply_markup, # Keep original buttons if needed
                     disable_web_page_preview=True
                 )
            else: # If message can't be edited (e.g., deleted) or has no buttons
                 await client.send_message(user_id, help_text, disable_web_page_preview=True)

        except Exception as e:
             logger.error(f"Error editing help message on callback: {e}")
             # Fallback: send as new message if edit fails
             try:
                 await client.send_message(user_id, help_text, disable_web_page_preview=True)
             except Exception as send_err:
                 logger.error(f"Failed to send fallback help message: {send_err}")


    elif data == "close_cb": # Example for a close button
         try:
            await callback_query.message.delete() # Delete the message the button is attached to
            await callback_query.answer("Closed.")
         except Exception as e:
             logger.warning(f"Could not delete message on close_cb: {e}")
             await callback_query.answer("Could not close.", show_alert=True)

    # Add more elif conditions for other callback data if needed
    # elif data == "link_error_cb":
    #     await callback_query.answer("Sorry, there was an error generating the link for this file.", show_alert=True)

    else:
        # Answer silently if the callback is not recognized or requires no user feedback
        # await callback_query.answer()
        # Or show an alert for clearly invalid buttons
        await callback_query.answer("Button action not implemented or invalid.", show_alert=False)


# --- Main Execution ---
START_TIME = None # Initialize START_TIME

async def main():
    global START_TIME
    START_TIME = datetime.datetime.now() # Set START_TIME when main function begins execution

    logger.info("Initializing Database...")
    try:
        await db.ensure_indexes() # Create DB indexes on startup
        logger.info("Database Initialized and Indexes Ensured.")
    except Exception as db_init_err:
        logger.critical(f"FATAL: Could not connect to or initialize the database: {db_init_err}", exc_info=True)
        logger.critical("Bot cannot start without a database connection. Exiting.")
        return # Stop execution if DB fails

    logger.info("Starting Bot Client...")
    try:
        await app.start()
        bot_info = await app.get_me()
        logger.info(f"Bot Started as @{bot_info.username} (ID: {bot_info.id})")
        startup_message = (f"🚀 Bot Started Successfully!\n\n"
                           f"Bot Name: {bot_info.first_name}\n"
                           f"Bot Username: @{bot_info.username}\n"
                           f"Bot ID: `{bot_info.id}`\n"
                           f"Owner ID: `{cfg.OWNER_ID}`\n"
                           f"Pyrogram v{app.version}\n"
                           f"Start Time: {START_TIME.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        await log_message(startup_message)

    except Exception as start_err:
        logger.critical(f"FATAL: Failed to start the Pyrogram client: {start_err}", exc_info=True)
        logger.critical("Bot startup failed. Exiting.")
        # Attempt to log error even if client didn't fully start (if possible)
        # await log_message(f"CRITICAL ERROR: Bot failed to start. Error: {start_err}")
        return # Stop execution if client start fails

    # Keep the bot running using asyncio's Future
    await asyncio.Future() # This will wait indefinitely until cancelled

    # --- Shutdown Sequence (usually reached only by external signal/error) ---
    logger.info("Initiating Bot Shutdown...")
    try:
        await log_message("💤 Bot is shutting down...")
        await app.stop()
        logger.info("Pyrogram client stopped.")
    except Exception as stop_err:
        logger.error(f"Error during bot stop sequence: {stop_err}")
    finally:
        logger.info("Bot shutdown complete.")

if __name__ == "__main__":
    # Setup asyncio event loop
    try:
        # For Python 3.7+, get_running_loop is preferred if a loop is already running
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError: # 'RuntimeError: There is no current event loop...'
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(main())

    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
         # Log critical errors that might occur outside the main() try/except
         logger.critical(f"Bot encountered a critical error during setup or shutdown: {e}", exc_info=True)
    finally:
         # Ensure loop closes cleanly if run_until_complete finishes or is interrupted
         if 'loop' in locals() and loop.is_running():
             # Give pending tasks a moment to cancel/complete
             tasks = asyncio.all_tasks(loop=loop)
             for task in tasks:
                 task.cancel()
             group = asyncio.gather(*tasks, return_exceptions=True)
             loop.run_until_complete(group)
             loop.close()
             logger.info("Asyncio event loop closed.")
