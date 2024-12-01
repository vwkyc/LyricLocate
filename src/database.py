import os
import threading
import hashlib
import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

class LyricsDatabase:
    EXPIRY_DAYS = 24  # Define the expiry duration for cached lyrics

    def __init__(self, db_path="../cache/lyrics.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.local = threading.local()
        self.init_db()

    @staticmethod
    def get_cache_key(title: str, artist: str, language: str = None) -> str:
        key = f"{title.lower()}_{artist.lower()}"
        if language:
            key += f"_{language.lower()}"
        return hashlib.md5(key.encode()).hexdigest()

    def _get_connection(self):
        if not hasattr(self.local, 'conn'):
            self.local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return self.local.conn

    def init_db(self):
        conn = self._get_connection()
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS lyrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT UNIQUE,
                title TEXT,
                artist TEXT,
                language TEXT,
                lyrics TEXT,
                timestamp DATETIME,
                UNIQUE(title, artist, language)
            )""")
            
            conn.execute("""CREATE TABLE IF NOT EXISTS spotify_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spotify_url TEXT UNIQUE,
                title TEXT,
                artist TEXT,
                timestamp DATETIME
            )""")
            
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_key ON lyrics(cache_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON lyrics(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spotify_url ON spotify_cache(spotify_url)")

    def get_cached_data(self, title: str, artist: str, language: str = "original") -> Optional[str]:
        cache_key = self.get_cache_key(title, artist, language)
        query_languages = [language]
        
        placeholders = ','.join(['?'] * len(query_languages))
        query = f"""
            SELECT lyrics FROM lyrics 
            WHERE cache_key = ? AND 
                language IN ({placeholders}) AND
                datetime(timestamp) > datetime('now', '-{self.EXPIRY_DAYS} days')
        """
        params = [cache_key] + query_languages
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        result = cursor.fetchone()
        if result:
            logger.info(f"Cache hit for '{title}' by '{artist}' with language '{language}'")
            return result[0]
        return None

    def cache(self, title: str, artist: str, lyrics: str, language: str = "original"):
        storage_language = language
        cache_key = self.get_cache_key(title, artist, storage_language)
        
        conn = self._get_connection()
        with conn:
            conn.execute("""
                INSERT OR REPLACE INTO lyrics (cache_key, title, artist, language, lyrics, timestamp)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (cache_key, title, artist, storage_language, lyrics))

    def delete_cached_lyrics(self, title: str, artist: str, language: str):
        cache_key = self.get_cache_key(title, artist, language)
        conn = self._get_connection()
        with conn:
            conn.execute("""
                DELETE FROM lyrics 
                WHERE cache_key = ? AND language = ?
            """, (cache_key, language))
            logger.info(f"Deleted cached lyrics for '{title}' by '{artist}' with language '{language}'")

    def get_cached_spotify_track(self, spotify_url: str) -> Optional[tuple[str, str]]:
        try:
            base_url = spotify_url.split('?')[0] if '&' not in spotify_url else spotify_url.split('&')[0]
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT title, artist FROM spotify_cache 
                WHERE spotify_url = ? AND
                datetime(timestamp) > datetime('now', '-24 hours')
            """, (base_url,))
            result = cursor.fetchone()
            if result:
                logger.info(f"Spotify cache hit for URL: {base_url}")
                return result[0], result[1]
            return None
        except Exception as e:
            logger.error(f"Error retrieving from Spotify cache: {e}")
            return None

    def cache_spotify_track(self, spotify_url: str, title: str, artist: str):
        try:
            base_url = spotify_url.split('?')[0] if '&' not in spotify_url else spotify_url.split('&')[0]
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO spotify_cache 
                    (spotify_url, title, artist, timestamp)
                    VALUES (?, ?, ?, datetime('now'))
                """, (base_url, title, artist))
                logger.info(f"Cached Spotify track info for URL: {base_url}")
        except Exception as e:
            logger.error(f"Error caching Spotify track info: {e}")