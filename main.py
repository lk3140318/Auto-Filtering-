# main.py
import os
import time
import datetime
import logging
import asyncio
import random
from pyrogram import Client, filters, enums
from pyrogram.errors import UserNotParticipant, FloodWait, PeerIdInvalid, ChannelPrivate, RPCError
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import cfg
from database import db

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Pyrogram Client Initialization ---
plugins = dict(root="plugins") # Although not used directly here, good practice for structure

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
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

async def get_media_link(channel_id, message_id):
    """Generate a direct link to the media message."""
    link = f"https://t.me/"
    if isinstance(channel_id, int) and str(channel_id).startswith("-100"): # Private channel
        link += f"c/{str(channel_id).replace('-100', '')}/{message_id}"
    elif isinstance(channel_id, str) and channel_id.startswith('@'): # Public channel username
         # Need to fetch chat to get ID if only username is stored, or store ID alongside username
         # For simplicity, assume numeric ID is stored or username is sufficient
         # Let's refine this: Fetch chat object if needed
         try:
            chat = await app.get_chat(channel_id)
            link += f"{chat.username}/{message_id}"
         except Exception:
             # Fallback if username doesn't work or chat not found
             # This part needs careful handling based on how INDEX_CHANNELS stores info
             # Assuming INDEX_CHANNELS has correct numeric IDs for private channels
             # And correct usernames for public ones
             if isinstance(channel_id, str):
                 link += f"{channel_id.replace('@', '')}/{message_id}" # Try direct username link
             else: # Should be private channel ID
                 link += f"c/{str(channel_id).replace('-100', '')}/{message_id}"

    else: # Public channel ID (less common) or other cases
        # This might need adjustment depending on how channel IDs are stored/handled
         link += f"c/{abs(channel_id)}/{message_id}" # Best guess for public numeric ID link structure
        # Fallback needed if username is stored but link needs ID or vice-versa

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
        if member.status in [
            enums.ChatMemberStatus.OWNER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.RESTRICTED # Consider restricted as joined
        ]:
            return True
        else:
            # User is KICKED or LEFT
            pass
    except UserNotParticipant:
        # User is not in the channel
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

    # If user needs to join
    try:
        channel = await app.get_chat(cfg.UPDATES_CHANNEL)
        channel_link = channel.invite_link or f"https://t.me/{channel.username}" if channel.username else "#" # Fallback link
        channel_name = channel.title

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

    # Prepare buttons (Example: add help or about button)
    buttons = [[
        InlineKeyboardButton("ℹ️ Help", callback_data="help_cb"),
        InlineKeyboardButton("📢 Updates", url=f"https://t.me/{cfg.UPDATES_CHANNEL.replace('@','')}" if cfg.UPDATES_CHANNEL else "#")
    ]]
    # Add request button if URL is set
    if cfg.REQUEST_MOVIE_URL:
        buttons.append([InlineKeyboardButton(cfg.REQUEST_MOVIE_BUTTON_TEXT, url=cfg.REQUEST_MOVIE_URL)])


    try:
        if photo_url:
            await message.reply_photo(
                photo=photo_url,
                caption=start_text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await message.reply_text(
                start_text,
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
        await log_message(f"User {user_id} ({first_name}) started the bot.")
    except Exception as e:
        logger.error(f"Error sending start message to {user_id}: {e}")
        # Fallback to text message if photo fails
        try:
            await message.reply_text(
                start_text,
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
        except Exception as fallback_e:
            logger.error(f"Fallback start message failed for {user_id}: {fallback_e}")
            await log_message(f"CRITICAL: Could not send start message to {user_id}. Error: {fallback_e}")


@app.on_message(filters.command("help") & filters.private)
async def help_command(client: Client, message: Message):
     help_text = """**Auto Filter Bot Help**

**How it works:**
1. Add me to your group.
2. Add the channels you want me to index using the `/index` command (Bot Owner only).
3. Send any movie or series name in the group.
4. I will search the indexed channels and provide links if found.

**Available Commands (Owner Only):**
- `/start`: Check if the bot is alive.
- `/help`: Show this help message.
- `/index [channel_id/username]`: Start indexing media from a specific channel. The bot must be an admin in the channel. Use `-100...` format for private channel IDs.
- `/index`: Index all channels listed in `INDEX_CHANNELS` ENV variable.
- `/clearindex [channel_id/username]`: Remove all indexed data for a specific channel.
- `/status` or `/stats`: Show bot statistics.
- `/broadcast [message]`: Send a message to all users. (Use with caution!)
- `/ban [user_id] [reason (optional)]`: Ban a user from using the bot.
- `/unban [user_id]`: Unban a user.
- `/banned`: List banned users.

**Group Usage:**
- Just send the name of the media you want to find!

**Note:** Force Subscription might be enabled. If so, you must join the specified channel to use the bot.
    """
     await message.reply_text(help_text)

# --- Admin Commands ---

@app.on_message(filters.command(["status", "stats"]) & filters.user(cfg.OWNER_ID))
async def status_command(client: Client, message: Message):
    try:
        total_users = await db.total_users_count()
        banned_users = await db.total_banned_users_count()
        active_users = total_users - banned_users
        total_media = await db.total_media_count()
        # Add more stats like DB size if needed (requires specific MongoDB commands)

        status_text = f"""**Bot Status** ✨

📊 **Users:**
   - Total Users: `{total_users}`
   - Active Users: `{active_users}`
   - Banned Users: `{banned_users}`

🎬 **Media:**
   - Total Indexed Files: `{total_media}`

⚙️ **Configuration:**
   - Force Subscribe: `{cfg.UPDATES_CHANNEL if cfg.UPDATES_CHANNEL else 'Disabled'}`
   - Log Channel: `{cfg.LOG_CHANNEL if cfg.LOG_CHANNEL else 'Disabled'}`
   - Index Channels: `{', '.join(map(str, cfg.INDEX_CHANNELS)) if cfg.INDEX_CHANNELS else 'None Configured'}`

⏰ Bot Uptime: `{str(datetime.datetime.now() - START_TIME).split('.')[0]}`
    """
        await message.reply_text(status_text)
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        await message.reply_text("Failed to retrieve bot status.")
        await log_message(f"Error in /status command: {e}")


@app.on_message(filters.command("broadcast") & filters.user(cfg.OWNER_ID))
async def broadcast_command(client: Client, message: Message):
    if not message.reply_to_message and len(message.command) < 2:
        await message.reply_text("Usage: `/broadcast [message]` or reply to a message with `/broadcast`")
        return

    bcast_msg = message.reply_to_message if message.reply_to_message else message # The message to broadcast
    query_msg = message # The command message itself
    text_to_send = message.text.split(None, 1)[1] if len(message.command) > 1 else None

    total_users = await db.total_users_count()
    banned_users_count = await db.total_banned_users_count()
    eligible_users = total_users - banned_users_count

    if eligible_users == 0:
        await query_msg.reply_text("No active users found to broadcast to.")
        return

    confirm_msg = await query_msg.reply_text(
        f"Starting broadcast to approximately {eligible_users} active users...\n\n"
        "**Warning:** This can take a while and might hit Telegram limits. "
        "Send `/cancel` to stop."
    )

    # Add cancel functionality (simple implementation)
    app.cancel_broadcast = False # Flag to control broadcast loop
    @app.on_message(filters.command("cancel") & filters.user(cfg.OWNER_ID))
    async def cancel_bcast(client, msg):
         if msg.id == query_msg.id + 2: # Very basic check, might not be robust
            app.cancel_broadcast = True
            await msg.reply_text("Broadcast cancellation requested. Stopping after the current user...")
            await log_message(f"Broadcast cancelled by owner {cfg.OWNER_ID}")

    start_time = time.time()
    sent_count = 0
    failed_count = 0
    users_cursor = db.get_all_users() # Get active users

    async for user in users_cursor:
        if app.cancel_broadcast:
            break
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
                 await query_msg.reply_text("Error: No message content found to broadcast.")
                 app.cancel_broadcast = True # Stop if something is wrong
                 break

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
            except Exception as retry_e:
                 logger.error(f"Retry failed sending broadcast to {user_id}: {retry_e}")
                 failed_count += 1
                 await db.set_ban_status(user_id, True) # Ban user if persistent error
                 await log_message(f"Broadcast failed for user {user_id} after FloodWait. Error: {retry_e}. User banned.")

        except (PeerIdInvalid):
             logger.warning(f"User ID {user_id} is invalid. Skipping.")
             failed_count += 1
             # Optionally remove user from DB if ID is truly invalid
        except Exception as e:
            logger.error(f"Error sending broadcast to {user_id}: {e}")
            failed_count += 1
            # Ban user if they blocked the bot or deactivated account etc.
            # Check specific error types if needed (e.g., UserIsBlocked)
            await db.set_ban_status(user_id, True)
            await log_message(f"Broadcast failed for user {user_id}. Error: {e}. User banned.")

        # Update progress message occasionally (e.g., every 20 users)
        if (sent_count + failed_count) % 20 == 0:
            elapsed_time = time.time() - start_time
            try:
                await confirm_msg.edit_text(
                    f"Broadcast in progress...\n\n"
                    f"Sent: {sent_count}\n"
                    f"Failed: {failed_count}\n"
                    f"Total processed: {sent_count + failed_count} / {eligible_users}\n"
                    f"Elapsed Time: {str(datetime.timedelta(seconds=int(elapsed_time)))}\n\n"
                    "Send `/cancel` to stop."
                )
            except FloodWait as fw:
                await asyncio.sleep(fw.value) # Wait if editing status message gets throttled
            except Exception:
                pass # Ignore errors editing status msg

    end_time = time.time()
    total_time = str(datetime.timedelta(seconds=int(end_time - start_time)))
    final_text = f"""Broadcast Finished! ✅

Sent: `{sent_count}`
Failed: `{failed_count}` (Users who failed have been banned)
Total Time: `{total_time}`
    """
    await query_msg.reply_text(final_text)
    await log_message(final_text)
    app.cancel_broadcast = False # Reset flag
    # Remove the cancel handler to prevent accidental triggers later
    # This requires a more sophisticated handler management, skipping for simplicity


@app.on_message(filters.command("ban") & filters.user(cfg.OWNER_ID))
async def ban_command(client: Client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.reply_text("Usage: `/ban [user_id] [reason (optional)]`")
        return

    user_id_to_ban = int(message.command[1])
    reason = " ".join(message.command[2:]) if len(message.command) > 2 else "No reason specified."

    if user_id_to_ban == cfg.OWNER_ID:
        await message.reply_text("Cannot ban the owner.")
        return
    if user_id_to_ban == client.me.id:
         await message.reply_text("Cannot ban myself.")
         return

    await db.set_ban_status(user_id_to_ban, True)
    await message.reply_text(f"User `{user_id_to_ban}` has been banned. Reason: {reason}")
    await log_message(f"User {user_id_to_ban} banned by owner {cfg.OWNER_ID}. Reason: {reason}")

    # Try notifying the banned user
    try:
        await client.send_message(user_id_to_ban, f"You have been banned from using this bot. Reason: {reason}")
    except Exception:
        pass # Ignore if user blocked the bot etc.

@app.on_message(filters.command("unban") & filters.user(cfg.OWNER_ID))
async def unban_command(client: Client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.reply_text("Usage: `/unban [user_id]`")
        return

    user_id_to_unban = int(message.command[1])
    await db.set_ban_status(user_id_to_unban, False)
    await message.reply_text(f"User `{user_id_to_unban}` has been unbanned.")
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
    async for user in banned_users_cursor:
        banned_list.append(f"- `{user['user_id']}` ({user.get('first_name', 'N/A')})")

    if not banned_list:
        await message.reply_text("No users are currently banned.")
        return

    banned_text = "**Banned Users:**\n" + "\n".join(banned_list)
    # Handle potential message length limits if many users are banned
    if len(banned_text) > 4096:
        await message.reply_text(f"Found {len(banned_list)} banned users. The list is too long to display here. Check logs or database.")
        # Optionally send as a file
        # with open("banned_users.txt", "w") as f:
        #     f.write("\n".join([u.split(" (")[0].replace("- `","").replace("`","") for u in banned_list]))
        # await message.reply_document("banned_users.txt", caption=f"{len(banned_list)} Banned Users")
        # os.remove("banned_users.txt")
    else:
        await message.reply_text(banned_text)


# --- Indexing Commands ---

async def index_channel(client: Client, message: Message, channel_target):
    """Helper function to index a single channel."""
    status_msg = await message.reply_text(f"⏳ Starting indexing for channel `{channel_target}`...")
    processed_count = 0
    skipped_count = 0
    failed_count = 0
    last_indexed_id = 0 # Track the last successfully processed message ID
    is_cancelled = False

    try:
        # Resolve channel ID/username
        try:
            target_chat = await client.get_chat(channel_target)
            channel_id = target_chat.id
            channel_name = target_chat.title or target_chat.username
            await status_msg.edit_text(f"⏳ Indexing channel: **{channel_name}** (`{channel_id}`)\nStarting message scan...")
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: Could not access channel `{channel_target}`. Ensure the bot is an admin or the username/ID is correct.\nDetails: {e}")
            await log_message(f"Indexing failed for {channel_target}. Could not get chat info. Error: {e}")
            return

        # Get last indexed message ID for this channel to potentially resume
        # start_offset_id = await db.get_last_indexed_message_id(channel_id)
        # print(f"Starting index for {channel_id} from message ID offset: {start_offset_id}")
        # Currently, we re-index everything for simplicity. To resume, use `offset_id` in get_chat_history.

        async for msg in client.get_chat_history(channel_id): # Get all history
            if not msg.media:
                skipped_count += 1
                continue

            media_type = msg.media.value # e.g., 'video', 'document', 'audio', 'photo'
            file_id = None
            file_name = None
            caption = msg.caption or ""
            file_size = 0

            # Extract details based on media type
            if media_type == 'video':
                file_id = msg.video.file_id
                file_name = msg.video.file_name or f"video_{msg.id}.mp4"
                file_size = msg.video.file_size
            elif media_type == 'document':
                file_id = msg.document.file_id
                file_name = msg.document.file_name or f"document_{msg.id}"
                file_size = msg.document.file_size
            elif media_type == 'audio':
                 file_id = msg.audio.file_id
                 file_name = msg.audio.file_name or f"audio_{msg.id}.mp3"
                 file_size = msg.audio.file_size
            # Add photo handling if needed
            # elif media_type == 'photo':
            #     file_id = msg.photo.file_id # Use largest available photo size?
            #     file_name = f"photo_{msg.id}.jpg"
            #     file_size = msg.photo.file_size # Approx. size
            else:
                skipped_count += 1 # Skip non-media or unsupported types
                continue

            # --- Optional: Adult Content Filter (Basic Keyword Check) ---
            # adult_keywords = ["xxx", "porn", "18+", "adult"] # Example keywords
            # combined_text = (file_name + " " + caption).lower()
            # if any(keyword in combined_text for keyword in adult_keywords):
            #     print(f"Skipping potential adult content: {file_name}")
            #     skipped_count += 1
            #     continue
            # ------------------------------------------------------------

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
                last_indexed_id = msg.id # Track the latest successfully processed message ID

                # Update status occasionally
                if (processed_count + skipped_count + failed_count) % 100 == 0:
                     await status_msg.edit_text(
                         f"⏳ Indexing **{channel_name}**...\n\n"
                         f"✅ Processed: {processed_count}\n"
                         f"⏭️ Skipped (Non-media/Other): {skipped_count}\n"
                         f"❌ Failed: {failed_count}\n"
                         f"📄 Last checked msg ID: {msg.id}\n\n"
                         "This can take a while..."
                     )
                     await asyncio.sleep(1) # Small delay to avoid hitting limits aggressively

            except Exception as db_err:
                logger.error(f"Error adding media (Msg ID: {msg.id}, Channel: {channel_id}) to DB: {db_err}")
                failed_count += 1
                await log_message(f"DB Error during indexing of {channel_name} (Msg {msg.id}): {db_err}")

    except FloodWait as fw:
         await status_msg.edit_text(f"⚠️ FloodWait encountered while indexing {channel_name}. Pausing for {fw.value} seconds...")
         await log_message(f"FloodWait during indexing of {channel_name}. Sleeping for {fw.value}s.")
         await asyncio.sleep(fw.value + 5)
         # Ideally, implement resuming logic here
         await status_msg.edit_text(f"⏳ Resuming indexing for {channel_name} (some messages might have been missed during pause).")
    except Exception as e:
        error_text = f"❌ An unexpected error occurred during indexing of `{channel_target}`:\n`{e}`"
        await status_msg.edit_text(error_text)
        await log_message(f"Indexing failed for {channel_target}. Error: {e}")
        return # Stop on major errors

    # Indexing finished for this channel
    # Store the last message ID checked (could be the very first message if iterated fully)
    # last_id_to_store = last_indexed_id # Store the last one *successfully* added
    # await db.set_last_indexed_message_id(channel_id, last_id_to_store)

    final_status = f"""✅ Indexing Finished for **{channel_name}** (`{channel_id}`)

Processed: `{processed_count}` new/updated media entries.
Skipped: `{skipped_count}` (non-media/other).
Failed: `{failed_count}` (check logs).
"""
    await status_msg.edit_text(final_status)
    await log_message(final_status)


@app.on_message(filters.command("index") & filters.user(cfg.OWNER_ID))
async def index_command(client: Client, message: Message):
    target_channels = []
    if len(message.command) > 1:
        # Index specific channel provided
        channel_arg = message.command[1]
        if channel_arg.startswith('@') or channel_arg.startswith('-') or channel_arg.isdigit():
             target_channels.append(channel_arg if not channel_arg.isdigit() else int(channel_arg))
        else:
            await message.reply_text("Invalid channel format. Use username (e.g., `@mychannel`) or ID (e.g., `-100123456789`).")
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

    await message.reply_text(f"Starting indexing process for {len(target_channels)} channel(s): {', '.join(map(str, target_channels))}")
    await log_message(f"Owner {cfg.OWNER_ID} triggered indexing for: {', '.join(map(str, target_channels))}")

    for channel in target_channels:
        await index_channel(client, message, channel)
        await asyncio.sleep(5) # Small delay between channels

    await message.reply_text("All requested indexing tasks initiated.")

@app.on_message(filters.command("clearindex") & filters.user(cfg.OWNER_ID))
async def clear_index_command(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: `/clearindex [channel_id/username]` or `/clearindex all` (Use 'all' with extreme caution!)")
        return

    target = message.command[1]

    if target.lower() == "all":
         confirm = await message.reply_text("⚠️ **WARNING:** Are you sure you want to delete ALL indexed media data from the database? This cannot be undone. Type `YESDELETEALL` to confirm.")
         try:
             response = await client.listen(message.chat.id, filters=filters.user(cfg.OWNER_ID), timeout=30)
             if response.text == "YESDELETEALL":
                 deleted_count = await db.media.delete_many({}) # Delete all media
                 await db.settings.delete_many({"_id": {"$regex": "^last_indexed_"}}) # Delete all index markers
                 await confirm.edit_text(f"✅ Successfully deleted all (`{deleted_count.deleted_count}`) indexed media items.")
                 await log_message(f"Owner {cfg.OWNER_ID} cleared ALL indexed data.")
             else:
                 await confirm.edit_text("❌ Deletion cancelled.")
         except asyncio.TimeoutError:
             await confirm.edit_text("❌ Confirmation timed out. Deletion cancelled.")
         return

    # Clear specific channel
    try:
        target_chat = await client.get_chat(target if not target.isdigit() else int(target))
        channel_id = target_chat.id
        channel_name = target_chat.title or target_chat.username
    except Exception as e:
        await message.reply_text(f"❌ Error: Could not find channel `{target}`. Details: {e}")
        return

    deleted_count = await db.delete_media_by_channel(channel_id)
    await message.reply_text(f"✅ Cleared `{deleted_count}` indexed items for channel **{channel_name}** (`{channel_id}`).")
    await log_message(f"Owner {cfg.OWNER_ID} cleared index for channel {channel_name} ({channel_id}). Deleted {deleted_count} items.")


# --- Group Message Filter Handler ---

@app.on_message(filters.group & filters.text & ~filters.command & ~filters.via_bot)
async def group_filter_handler(client: Client, message: Message):
    user_id = message.from_user.id

    # 1. Check if user is banned
    if await db.is_user_banned(user_id):
        # Maybe send a temporary message? Or just ignore. Ignoring is better for group spam.
        logger.info(f"Ignoring message from banned user {user_id} in group {message.chat.id}")
        return

    # 2. Check Force Subscribe
    if not await check_force_sub(message):
        return # Stop if user needs to subscribe

    # 3. Add/Update user in DB (do this even if no results are found)
    await db.add_user(user_id, message.from_user.first_name, message.from_user.username)

    # 4. Process the search query
    query = message.text.strip()
    if len(query) < 3: # Ignore very short messages
        return

    start_time = time.time()
    logger.info(f"Group {message.chat.id}: User {user_id} searching for '{query}'")

    try:
        results = await db.search_media(query, max_results=cfg.MAX_RESULTS)
        end_time = time.time()

        if not results:
            logger.info(f"No results found for '{query}' by {user_id}. Time: {end_time - start_time:.2f}s")
            # Optional: Send "Not Found" message with Request button
            if cfg.REQUEST_MOVIE_URL:
                 button = [[InlineKeyboardButton(cfg.REQUEST_MOVIE_BUTTON_TEXT, url=cfg.REQUEST_MOVIE_URL)]]
                 await message.reply_text(
                     cfg.NOT_FOUND_MSG.format(query=query),
                     reply_markup=InlineKeyboardMarkup(button),
                     disable_web_page_preview=True
                 )
            # else: No need to explicitly say "not found" in the group to reduce spam
            return

        # 5. Format and send results
        result_text = f"✨ Found Results for: **{query}**\n\n"
        buttons = []
        for i, item in enumerate(results):
            file_name = item.get('file_name', 'Unknown File')
            file_size = get_readable_size(item.get('file_size', 0))
            channel_id = item['channel_id']
            message_id = item['message_id']
            link = await get_media_link(channel_id, message_id)

            result_text += f"**{i+1}. {file_name}** [{file_size}]\n"
            buttons.append([InlineKeyboardButton(f"{i+1}. {file_name[:40]}...", url=link)]) # Truncate long names in buttons

        # Add a final row for Request Movie if configured
        if cfg.REQUEST_MOVIE_URL:
             buttons.append([InlineKeyboardButton(cfg.REQUEST_MOVIE_BUTTON_TEXT, url=cfg.REQUEST_MOVIE_URL)])


        await message.reply_text(
            result_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True,
            quote=True # Reply to the user's query message
        )
        logger.info(f"Sent {len(results)} results for '{query}' to {user_id}. Time: {end_time - start_time:.2f}s")

    except Exception as e:
        logger.error(f"Error processing group message filter for query '{query}' by {user_id}: {e}")
        await log_message(f"Error in group filter for query '{query}' in chat {message.chat.id}. User: {user_id}. Error: {e}")
        # Maybe notify the user that an error occurred?
        # await message.reply_text("Sorry, an error occurred while searching. Please try again later.", quote=True)


# --- Callback Query Handler (for buttons like Help) ---

@app.on_callback_query()
async def callback_query_handler(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id

    # Check Force Sub on callbacks too? Optional, but good practice.
    # message_mock = callback_query.message # Need a message object for check_force_sub
    # message_mock.from_user = callback_query.from_user # Patch user info
    # if not await check_force_sub(message_mock):
    #     await callback_query.answer("Please join our updates channel first!", show_alert=True)
    #     return

    if data == "help_cb":
        await callback_query.answer() # Answer the callback first
        # You can reuse the help_command logic or send a specific inline help text
        help_text = """**How to Use Me:**
        - Add me to your group.
        - Send the name of a movie or series in the group.
        - I will search connected channels and give you links!

        For admin commands, check `/help` in a private chat with me (Owner only).
        """
        # Edit the original message or send a new one. Editing is cleaner.
        try:
             # Check if message exists and has markup before editing
             if callback_query.message and callback_query.message.reply_markup:
                await callback_query.edit_message_text(
                     help_text,
                     # Keep original buttons or define new ones?
                     reply_markup=callback_query.message.reply_markup # Keep original buttons
                 )
             else: # If message can't be edited (e.g., deleted) or has no buttons
                 await client.send_message(user_id, help_text)

        except Exception as e:
             logger.error(f"Error editing help message on callback: {e}")
             # Fallback: send as new message
             await client.send_message(user_id, help_text)


    elif data == "close_cb": # Example for a close button
         await callback_query.message.delete() # Delete the message the button is attached to
         await callback_query.answer("Closed.")

    else:
        await callback_query.answer("Invalid button.", show_alert=True)


# --- Main Execution ---
import math # Import missing math module used in get_readable_size

START_TIME = datetime.datetime.now()

async def main():
    global START_TIME
    START_TIME = datetime.datetime.now() # Ensure START_TIME is set when main runs

    logger.info("Initializing Database...")
    await db.ensure_indexes() # Create DB indexes on startup
    logger.info("Database Initialized.")

    logger.info("Starting Bot...")
    await app.start()
    bot_info = await app.get_me()
    logger.info(f"Bot Started as @{bot_info.username} (ID: {bot_info.id})")
    startup_message = f"🚀 Bot Started!\nOwner ID: {cfg.OWNER_ID}\nBot ID: {bot_info.id}\nBot Username: @{bot_info.username}"
    await log_message(startup_message)

    # Keep the bot running
    await asyncio.Event().wait() # Keeps the script running indefinitely

    logger.info("Stopping Bot...")
    await app.stop()
    logger.info("Bot Stopped.")

if __name__ == "__main__":
    # Setup asyncio event loop
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
         logger.critical(f"Bot encountered a critical error: {e}", exc_info=True)
    finally:
         # Ensure loop closes if run_until_complete finishes (e.g., on error)
         if 'loop' in locals() and loop.is_running():
             loop.close()
