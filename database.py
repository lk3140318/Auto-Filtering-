# database.py
import motor.motor_asyncio
import datetime
import re
from config import cfg

class Database:
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.users = self.db.users
        self.media = self.db.media
        self.settings = self.db.settings # For storing info like last indexed message ID

    async def ensure_indexes(self):
        """Creates necessary indexes if they don't exist."""
        # Index for searching media efficiently (case-insensitive)
        await self.media.create_index([("search_tags", "text")], default_language='english', background=True)
        # Index for fast user lookups
        await self.users.create_index("user_id", unique=True, background=True)
        # Index for finding media by message ID and channel ID (for updates/duplicates)
        await self.media.create_index([("channel_id", 1), ("message_id", 1)], unique=True, background=True)
        print("Database indexes checked/created.")

    async def add_user(self, user_id, first_name, username):
        user_data = {
            'user_id': user_id,
            'first_name': first_name,
            'username': username,
            'join_date': datetime.datetime.utcnow(),
            'banned': False
        }
        await self.users.update_one({'user_id': user_id}, {'$set': user_data}, upsert=True)

    async def get_user(self, user_id):
        return await self.users.find_one({'user_id': user_id})

    async def is_user_banned(self, user_id):
        user = await self.get_user(user_id)
        return user and user.get('banned', False)

    async def set_ban_status(self, user_id, status: bool):
        await self.users.update_one({'user_id': user_id}, {'$set': {'banned': status}}, upsert=True)
        # If upserting, ensure other fields are added if the user wasn't known before
        await self.users.update_one({'user_id': user_id}, {'$setOnInsert': {'join_date': datetime.datetime.utcnow()}}, upsert=True)


    async def get_all_users(self):
        return self.users.find({'banned': False}) # Find users who are not banned

    async def get_all_banned_users(self):
        return self.users.find({'banned': True})

    async def total_users_count(self):
        return await self.users.count_documents({})

    async def total_banned_users_count(self):
        return await self.users.count_documents({'banned': True})

    async def add_media(self, channel_id, message_id, file_id, file_name, caption, file_type, file_size):
        # Basic text processing for better search
        search_text = (file_name or "") + " " + (caption or "")
        search_tags = ' '.join(re.findall(r'\w+', search_text.lower())) # Extract words

        media_data = {
            'channel_id': channel_id,
            'message_id': message_id,
            'file_id': file_id,
            'file_name': file_name,
            'caption': caption,
            'file_type': file_type,
            'file_size': file_size,
            'search_tags': search_tags, # Store processed text for text index
            'indexed_at': datetime.datetime.utcnow()
        }
        # Use message_id and channel_id as unique identifier to avoid duplicates
        await self.media.update_one(
            {'channel_id': channel_id, 'message_id': message_id},
            {'$set': media_data},
            upsert=True
        )

    async def search_media(self, query: str, max_results: int = 5):
        # Clean query: lowercase, remove extra spaces, keep alphanumeric and spaces
        cleaned_query = ' '.join(re.findall(r'\w+', query.lower()))
        if not cleaned_query:
            return []

        # Use text index for searching
        # Sort by text score first, then maybe by message_id descending (newer first)
        cursor = self.media.find(
            {'$text': {'$search': cleaned_query}},
            {'score': {'$meta': 'textScore'}}
        ).sort([('score', {'$meta': 'textScore'}), ('message_id', -1)]).limit(max_results) # Sort by relevance score

        # Alternative (less efficient) Regex search if text index fails or isn't setup
        # regex_query = re.compile(cleaned_query.replace(" ", ".*"), re.IGNORECASE)
        # cursor = self.media.find({
        #     '$or': [
        #         {'file_name': regex_query},
        #         {'caption': regex_query}
        #     ]
        # }).limit(max_results)

        return await cursor.to_list(length=max_results)

    async def total_media_count(self):
        return await self.media.count_documents({})

    async def get_last_indexed_message_id(self, channel_id):
        setting = await self.settings.find_one({"_id": f"last_indexed_{channel_id}"})
        return setting.get("message_id", 0) if setting else 0

    async def set_last_indexed_message_id(self, channel_id, message_id):
        await self.settings.update_one(
            {"_id": f"last_indexed_{channel_id}"},
            {"$set": {"message_id": message_id}},
            upsert=True
        )

    async def delete_media_by_channel(self, channel_id):
        result = await self.media.delete_many({"channel_id": channel_id})
        await self.settings.delete_one({"_id": f"last_indexed_{channel_id}"})
        return result.deleted_count


# Initialize database connection
db = Database(cfg.DATABASE_URI, cfg.DATABASE_NAME)
