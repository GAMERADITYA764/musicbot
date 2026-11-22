"""
Database layer for Discord Music Bot using aiosqlite.
Manages guild settings, DJ roles, 24/7 mode, request channels,
user favorites, custom playlists, and song playback history.
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
import aiosqlite

from core.config import DATABASE_PATH, LEGACY_DATA_FILE

logger = logging.getLogger("musicbot.db")


class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Initialize SQLite database, enable WAL mode, and build schema."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        
        # Optimize SQLite for concurrency and low resource VPS
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self._conn.execute("PRAGMA synchronous = NORMAL;")
        await self._conn.execute("PRAGMA foreign_keys = ON;")

        await self._create_tables()
        await self._migrate_legacy_data()
        logger.info("Database initialized successfully at %s", self.db_path)

    async def close(self):
        """Close the database connection cleanly."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")

    async def _create_tables(self):
        """Create necessary relational tables if not present."""
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                dj_role_id INTEGER DEFAULT NULL,
                request_channel_id INTEGER DEFAULT NULL,
                default_volume REAL DEFAULT 0.5,
                mode_24_7 INTEGER DEFAULT 0,
                autoplay INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                query TEXT NOT NULL,
                url TEXT,
                author TEXT,
                duration INTEGER DEFAULT 0,
                thumbnail TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, query)
            );

            CREATE TABLE IF NOT EXISTS custom_playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                source_url TEXT,
                tracks_json TEXT NOT NULL DEFAULT '[]',
                synced_at INTEGER DEFAULT 0,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, name)
            );

            CREATE TABLE IF NOT EXISTS playback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                query TEXT NOT NULL,
                author TEXT,
                requester TEXT,
                duration INTEGER DEFAULT 0,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_history_guild ON playback_history(guild_id, played_at DESC);
            CREATE INDEX IF NOT EXISTS idx_fav_user ON user_favorites(user_id);
            CREATE INDEX IF NOT EXISTS idx_playlist_guild ON custom_playlists(guild_id);
        """)
        await self._conn.commit()

    async def _migrate_legacy_data(self):
        """Migrates legacy playlists.json into custom_playlists table if found."""
        if not os.path.exists(LEGACY_DATA_FILE):
            return
        try:
            with open(LEGACY_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            migrated_count = 0
            for guild_str, playlists in data.items():
                try:
                    guild_id = int(guild_str)
                except ValueError:
                    continue
                for name, entry in playlists.items():
                    source_url = entry.get("url", "")
                    tracks = entry.get("tracks", [])
                    synced_at = entry.get("synced_at", 0)
                    error = entry.get("error", None)

                    await self._conn.execute("""
                        INSERT OR REPLACE INTO custom_playlists 
                        (guild_id, name, source_url, tracks_json, synced_at, error)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (guild_id, name.lower(), source_url, json.dumps(tracks, ensure_ascii=False), synced_at, error))
                    migrated_count += 1

            await self._conn.commit()
            if migrated_count > 0:
                logger.info("Migrated %d legacy playlists into SQLite database.", migrated_count)
                # Rename old file to .bak to avoid re-parsing
                bak_path = LEGACY_DATA_FILE + ".bak"
                if not os.path.exists(bak_path):
                    os.rename(LEGACY_DATA_FILE, bak_path)
        except Exception as e:
            logger.warning("Could not migrate legacy playlists.json: %s", e)

    # ---------------------------------------------------------
    # Guild Settings
    # ---------------------------------------------------------
    async def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        async with self._conn.execute(
            "SELECT dj_role_id, request_channel_id, default_volume, mode_24_7, autoplay FROM guild_settings WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "dj_role_id": row["dj_role_id"],
                    "request_channel_id": row["request_channel_id"],
                    "default_volume": row["default_volume"],
                    "mode_24_7": bool(row["mode_24_7"]),
                    "autoplay": bool(row["autoplay"])
                }
            return {
                "dj_role_id": None,
                "request_channel_id": None,
                "default_volume": 0.5,
                "mode_24_7": False,
                "autoplay": False
            }

    async def update_guild_setting(self, guild_id: int, **kwargs):
        allowed_keys = {"dj_role_id", "request_channel_id", "default_volume", "mode_24_7", "autoplay"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_keys}
        if not updates:
            return

        # Ensure row exists
        await self._conn.execute(
            "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)",
            (guild_id,)
        )
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [guild_id]
        await self._conn.execute(f"UPDATE guild_settings SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ?", values)
        await self._conn.commit()

    # ---------------------------------------------------------
    # Custom Playlists
    # ---------------------------------------------------------
    async def get_playlist(self, guild_id: int, name: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT name, source_url, tracks_json, synced_at, error FROM custom_playlists WHERE guild_id = ? AND name = ?",
            (guild_id, name.lower())
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "name": row["name"],
                "url": row["source_url"],
                "tracks": json.loads(row["tracks_json"]),
                "synced_at": row["synced_at"],
                "error": row["error"]
            }

    async def get_all_guild_playlists(self, guild_id: int) -> Dict[str, Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT name, source_url, tracks_json, synced_at, error FROM custom_playlists WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            result = {}
            for row in rows:
                result[row["name"]] = {
                    "url": row["source_url"],
                    "tracks": json.loads(row["tracks_json"]),
                    "synced_at": row["synced_at"],
                    "error": row["error"]
                }
            return result

    async def get_all_syncable_playlists(self) -> List[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT id, guild_id, name, source_url, tracks_json, synced_at FROM custom_playlists WHERE source_url IS NOT NULL AND source_url != ''"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "guild_id": row["guild_id"],
                    "name": row["name"],
                    "source_url": row["source_url"],
                    "tracks": json.loads(row["tracks_json"]),
                    "synced_at": row["synced_at"]
                }
                for row in rows
            ]

    async def save_playlist(self, guild_id: int, name: str, source_url: str, tracks: List[Dict[str, Any]], error: Optional[str] = None):
        tracks_json = json.dumps(tracks, ensure_ascii=False)
        import time
        synced_at = int(time.time())
        await self._conn.execute("""
            INSERT INTO custom_playlists (guild_id, name, source_url, tracks_json, synced_at, error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, name) DO UPDATE SET
                source_url = excluded.source_url,
                tracks_json = excluded.tracks_json,
                synced_at = excluded.synced_at,
                error = excluded.error
        """, (guild_id, name.lower(), source_url, tracks_json, synced_at, error))
        await self._conn.commit()

    async def delete_playlist(self, guild_id: int, name: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM custom_playlists WHERE guild_id = ? AND name = ?",
            (guild_id, name.lower())
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    # ---------------------------------------------------------
    # User Favorites
    # ---------------------------------------------------------
    async def add_favorite(self, user_id: int, title: str, query: str, url: str = "", author: str = "", duration: int = 0, thumbnail: str = "") -> bool:
        try:
            await self._conn.execute("""
                INSERT INTO user_favorites (user_id, title, query, url, author, duration, thumbnail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, query) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    author = excluded.author,
                    duration = excluded.duration,
                    thumbnail = excluded.thumbnail
            """, (user_id, title, query, url, author, duration, thumbnail))
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to add favorite: %s", e)
            return False

    async def get_user_favorites(self, user_id: int, limit: int = 25) -> List[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT title, query, url, author, duration, thumbnail FROM user_favorites WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def remove_favorite(self, user_id: int, query_or_title: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM user_favorites WHERE user_id = ? AND (query = ? OR title LIKE ?)",
            (user_id, query_or_title, f"%{query_or_title}%")
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    # ---------------------------------------------------------
    # Playback History
    # ---------------------------------------------------------
    async def record_history(self, guild_id: int, title: str, query: str, author: str = "", requester: str = "", duration: int = 0):
        try:
            await self._conn.execute("""
                INSERT INTO playback_history (guild_id, title, query, author, requester, duration)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (guild_id, title, query, author, requester, duration))
            await self._conn.commit()
        except Exception as e:
            logger.warning("Failed to record history: %s", e)

    async def get_guild_history(self, guild_id: int, limit: int = 15) -> List[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT title, query, author, requester, duration, played_at FROM playback_history WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (guild_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


db = Database()
