import os
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import logging
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher
import re
import unidecode
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from typing import Optional
from pydantic import BaseModel
import threading

# Load environment variables from .env file
load_dotenv("../.env")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LyricsDatabase:
    """Handles all database operations for lyrics caching"""
    
    def __init__(self, db_path=None):
        """Initialize database connection and create tables"""
        if db_path is None:
            db_path = "../cache/lyrics.db"
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self.init_db()

    def init_db(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS lyrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT UNIQUE,
                    title TEXT,
                    artist TEXT,
                    language TEXT,
                    lyrics TEXT,
                    timestamp DATETIME,
                    UNIQUE(title, artist, language)
                )
            """)
            # Create indexes for better query performance
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_key ON lyrics(cache_key)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON lyrics(timestamp)")

class LyricLocate:
    """Main class for scraping and managing lyrics"""

    EXPIRY_DAYS = 24

    def __init__(self):
        """Initialize the scraper with necessary configurations"""
        self.api_key = os.getenv("GENIUS_CLIENT_ACCESS_TOKEN")
        if not self.api_key:
            logger.warning("GENIUS_CLIENT_ACCESS_TOKEN environment variable not set. Genius API will not work.")
        self.genius_headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
        self.google_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/'
        }
        self.google_params = {'hl': 'en'}
        self.db = LyricsDatabase()

    @staticmethod
    def get_cache_key(title: str, artist: str, language: str = None) -> str:
        import hashlib
        key = f"{title.lower()}_{artist.lower()}"
        if language:
            key += f"_{language.lower()}"
        return hashlib.md5(key.encode()).hexdigest()

    def get_cached_data(self, title: str, artist: str, language: str = None) -> Optional[str]:
        cache_key = self.get_cache_key(title, artist, language)
        with self.db.lock:
            cursor = self.db.conn.cursor()
            if language is None:
                cursor.execute("""
                    SELECT lyrics FROM lyrics 
                    WHERE cache_key = ? AND 
                    language IS NULL AND
                    datetime(timestamp) > datetime('now', '-{} days')
                """.format(self.EXPIRY_DAYS), (cache_key,))
            else:
                cursor.execute("""
                    SELECT lyrics FROM lyrics 
                    WHERE cache_key = ? AND 
                    language = ? AND
                    datetime(timestamp) > datetime('now', '-{} days')
                """.format(self.EXPIRY_DAYS), (cache_key, language))
            result = cursor.fetchone()
            if result:
                logger.info(f"Cache hit for {title} by {artist} with language {language}")
                return result[0]
        return None

    def save_to_cache(self, title: str, artist: str, lyrics: str, language: str = None):
        cache_key = self.get_cache_key(title, artist, language)
        with self.db.lock:
            if language is None:
                self.db.conn.execute("""
                    INSERT OR REPLACE INTO lyrics (cache_key, title, artist, language, lyrics, timestamp)
                    VALUES (?, ?, ?, NULL, ?, datetime('now'))
                """, (cache_key, title, artist, lyrics))
            else:
                self.db.conn.execute("""
                    INSERT OR REPLACE INTO lyrics (cache_key, title, artist, language, lyrics, timestamp)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (cache_key, title, artist, language, lyrics))
            self.db.conn.commit()

    @staticmethod
    def clean_string(text):
        return unidecode.unidecode(text).lower().strip() if text else text

    @staticmethod
    def clean_title(title):
        if title:
            title = re.sub(r'\b(feat\.|ft\.)\s+\w+', '', title, flags=re.IGNORECASE)
            return title.strip().lower()
        return title

    @staticmethod
    def clean_artist(artist):
        return [name.strip() for name in re.split(r'[;,]', artist)] if artist else []

    def find_genius_url(self, title, artist, language=None) -> Optional[str]:
        if not self.api_key:
            return None
        search_url = "https://api.genius.com/search"
        query_title = self.clean_string(self.clean_title(title))
        query_artists = [self.clean_string(name) for name in self.clean_artist(artist)]

        search_query = f"{title} {artist}"
        if language and language.lower() == 'en':
            search_query += ' english translation'

        logger.info(f"Searching for '{title}' by '{artist}' on Genius with language '{language}'")

        params = {'q': search_query}
        try:
            response = requests.get(search_url, headers=self.genius_headers, params=params)
            if response.status_code == 429:
                logger.error("Rate limit exceeded for Genius API")
                return None
            response.raise_for_status()
            data = response.json()
            hits = data.get("response", {}).get("hits", [])

            if hits:
                logger.info(f"Search results from Genius for '{title}': {[hit['result']['title'] for hit in hits]}")

                for hit in hits:
                    if self.is_match(hit, query_title, query_artists):
                        logger.info(f"Match found for '{title}' by '{artist}'")
                        return hit['result']['url']

                logger.warning(f"No valid match found for '{title}' by '{artist}'")
            else:
                logger.warning(f"No results found for {title} by {artist}")
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
        return None

    def is_match(self, hit, query_title, query_artists):
        result_title = self.clean_string(hit['result']['title'])
        parenthetical_matches = re.findall(r'\((.*?)\)', result_title)
        paren_texts = [match.strip() for match in parenthetical_matches]
        result_title_no_paren = re.sub(r'\s*\(.*?\)', '', result_title).strip()
        title_variants = [result_title_no_paren] + paren_texts + [result_title]

        title_match_ratio = max(
            SequenceMatcher(None, query_title, title_variant).ratio()
            for title_variant in title_variants
        )

        result_artist = self.clean_string(hit['result']['primary_artist']['name'])
        artist_match_ratio = max(
            SequenceMatcher(None, query_artist, result_artist).ratio()
            for query_artist in query_artists
        )

        logger.info(f"Result - Title: {hit['result']['title']}, Artist: {hit['result']['primary_artist']['name']}, Match - Title Ratio: {title_match_ratio}, Artist Ratio: {artist_match_ratio}")

        if result_artist in ["genius romanizations", "genius english translations"]:
            artist_match_ratio = 1.0
            logger.info("Skipping matching due to Genius Romanization/Translation")

        return title_match_ratio > 0.6 and artist_match_ratio > 0.45

    def scrape_lyrics(self, url) -> Optional[str]:
        if not url:
            return None
        
        try:
            response = requests.get(url)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Request failed while scraping lyrics from {url}: {e}")
            return None

        try:
            soup = BeautifulSoup(response.text, 'html.parser')
            lyrics_containers = soup.find_all("div", attrs={"data-lyrics-container": "true"})
            
            if not lyrics_containers:
                instrumental_tag = soup.find("div", string="This song is an instrumental")
                if instrumental_tag:
                    logger.info(f"Song marked as instrumental on Genius page: {url}")
                    return "This song is an instrumental"
                logger.error(f"Lyrics container not found on page: {url}")
                return None
            
            lyrics = "\n".join([container.get_text(separator="\n").strip() for container in lyrics_containers])
            return lyrics
        except Exception as e:
            logger.error(f"Error parsing lyrics from {url}: {e}")
            return None

    def search_and_scrape_genius_google(self, title: str, artist: str, language: str = None) -> Optional[str]:
        query = f"{title} {artist} genius.com lyrics"
        if language and language.lower() == 'en':
            query += ' english translation'
        url = "https://www.google.com/search"
        params = {**self.google_params, 'q': query}
        
        try:
            response = requests.get(url, headers=self.google_headers, params=params)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            search_results = soup.select('a[href]')
            logger.debug(f"Search results: {[result.get('href') for result in search_results]}")

            for result in search_results:
                link = result.get('href')
                if "genius.com" in link:
                    if link.startswith('/url?q='):
                        link = link.split('/url?q=')[1].split('&')[0]
                    if link.startswith('/search'):
                        continue

                    genius_url = re.search(r'(https?:\/\/genius\.com\/[^\s]+)', link)
                    if genius_url:
                        genius_url = genius_url.group()
                        logger.info(f"Found Genius link via Google: {genius_url}")

                        lyrics = self.scrape_lyrics(genius_url)
                        if lyrics and self.verify_genius_google_artist_and_title(genius_url, artist, title):
                            logger.info(f"Verified artist and title match for '{artist}' and '{title}'")
                            return lyrics
                        else:
                            logger.warning(f"Artist or title verification failed for '{artist}' and '{title}' on {genius_url}")
                            break

            logger.info(f"No valid Genius link found in Google results for '{title}' by '{artist}'")
        except requests.RequestException as e:
            logger.error(f"Request to Google search failed: {e}")
        
        return None

    def verify_genius_google_artist_and_title(self, url: str, expected_artist: str, expected_title: str) -> bool:
        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            page_artist_elem = soup.find('a', class_=re.compile(r'[^ ]*Artist$'))
            if page_artist_elem:
                genius_artist = self.clean_string(page_artist_elem.get_text())
                expected_artist_clean = self.clean_string(expected_artist)

                artist_match_ratio = SequenceMatcher(None, genius_artist, expected_artist_clean).ratio()
                logger.info(f"Genius artist: '{genius_artist}', Expected artist: '{expected_artist_clean}', Match ratio: {artist_match_ratio}")

                page_title_elem = soup.find('h1', class_=re.compile(r'[^ ]*Title$'))
                if page_title_elem:
                    genius_title = self.clean_string(page_title_elem.get_text())
                    expected_title_clean = self.clean_string(expected_title)

                    title_match_ratio = SequenceMatcher(None, genius_title, expected_title_clean).ratio()
                    logger.info(f"Genius title: '{genius_title}', Expected title: '{expected_title_clean}', Match ratio: {title_match_ratio}")

                    return artist_match_ratio > 0.6 and title_match_ratio > 0.45

            logger.error(f"Artist or title element not found on Genius page: {url}")
        except requests.RequestException as e:
            logger.error(f"Request failed during artist and title verification: {e}")
        
        return False

    def google_search(self, title: str, artist: str, language: str = None) -> Optional[str]:
        clean_title = self.clean_title(title)
        clean_artists = self.clean_artist(artist)

        queries = [
            f"{clean_title} {clean_artists[0]} lyrics",
            f"{clean_title} lyrics"
        ]
        if language and language.lower() == 'en':
            queries = [q + ' english translation' for q in queries]

        for query in queries:
            lyrics = self.scrape_google_lyrics(query, artist_verification=(query == queries[1]), artist=artist)
            if lyrics:
                return lyrics

        return None

    def scrape_google_lyrics(self, query: str, artist_verification: bool, artist: str = None) -> Optional[str]:
        url = "https://www.google.com/search"
        params = {**self.google_params, 'q': query}
        exclude_keywords = ["Spotify", "YouTube", "Album"]
        try:
            response = requests.get(url, headers=self.google_headers, params=params)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
        
            artist_found = False
            song_info = soup.select_one('div.PZPZlf[data-attrid="subtitle"]')
            if song_info:
                song_info_text = song_info.text.lower()
                if artist_verification and artist:
                    artist_names = [name.strip().lower() for name in artist.split(',')]
                    if any(name in song_info_text for name in artist_names):
                        artist_found = True
            
            if artist_verification and not artist_found:
                logger.warning(f"Artist verification failed for '{artist}'")
                return None

            selectors = ['div.ujudUb', 'div.PZPZlf', 'div[data-lyricid]', 'div.PZPZlf.zloOqf']
                         
            for selector in selectors:
                lyrics_divs = soup.select(selector)
                if lyrics_divs:
                    lyrics = '\n'.join([div.get_text(separator='\n') for div in lyrics_divs])
                    if len(lyrics.split('\n')) > 4:
                        return lyrics.strip()
            
            logger.warning(f"No lyrics found for {query}")
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
        return None

    def get_lyrics(self, title, artist, language=None):
        cached_lyrics = self.get_cached_data(title, artist, language)
        if cached_lyrics:
            return cached_lyrics

        lyrics = self.search_song(title, artist, language) or \
                self.search_and_scrape_genius_google(title, artist, language) or \
                self.google_search(title, artist, language)

        if lyrics:
            self.save_to_cache(title, artist, lyrics, language)
            return lyrics

        # Return a placeholder string if no lyrics found
        return "Lyrics not found"

    def fetch_and_cache_alternate(self, title, artist, language):
        alternate_language = None if language and language.lower() == 'en' else 'EN'
        # Fetch and cache the alternate lyrics without returning them
        self.get_lyrics(title, artist, alternate_language)

    def search_song(self, title, artist, language=None):
        url = self.find_genius_url(title, artist, language)
        return self.scrape_lyrics(url) if url else None

# Define response model
class LyricsResponse(BaseModel):
    title: str
    artist: str
    language: Optional[str]
    lyrics: str

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://lyriclocate.kmst.me"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)

scraper = LyricLocate()

@app.get("/api/get_lyrics", response_model=LyricsResponse)
def get_lyrics(title: str, artist: str, language: Optional[str] = None, background_tasks: BackgroundTasks = None):
    # First check cache
    cached_lyrics = scraper.get_cached_data(title, artist, language)
    if cached_lyrics:
        return LyricsResponse(title=title, artist=artist, language=language or "original", lyrics=cached_lyrics)
    
    # If not in cache, fetch fresh lyrics
    lyrics = scraper.get_lyrics(title, artist, language)
    if lyrics and lyrics != "Lyrics not found":
        if background_tasks:
            background_tasks.add_task(scraper.fetch_and_cache_alternate, title, artist, language)
        return LyricsResponse(title=title, artist=artist, language=language or "original", lyrics=lyrics)
    else:
        raise HTTPException(status_code=404, detail="Lyrics not found")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=19999)
