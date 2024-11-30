import os
import re
import threading
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
import sqlite3
from bs4 import BeautifulSoup
from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
from difflib import SequenceMatcher
import unidecode

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
dotenv_path = find_dotenv(".env")
if dotenv_path:
    load_dotenv(dotenv_path)
    logger.info("Loaded environment variables from .env")
else:
    logger.warning("No .env file found. It's recommended to provide a GENIUS_CLIENT_ACCESS_TOKEN environment variable for full functionality.")

class LyricsDatabase:
    """Handles all database operations for lyrics caching"""

    def __init__(self, db_path="../cache/lyrics.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
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
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_key ON lyrics(cache_key)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON lyrics(timestamp)")

class LyricLocate:
    """Main class for scraping and managing lyrics"""

    EXPIRY_DAYS = 24

    def __init__(self):
        self.api_key = os.getenv("GENIUS_CLIENT_ACCESS_TOKEN")
        if not self.api_key:
            logger.warning("GENIUS_CLIENT_ACCESS_TOKEN not set. Genius API will not work.")
        self.genius_headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
        self.google_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/'
        }
        self.google_params = {'hl': 'en'}
        self.db = LyricsDatabase()

    @staticmethod
    def get_cache_key(title: str, artist: str, language: str = None) -> str:
        key = f"{title.lower()}_{artist.lower()}"
        if language:
            key += f"_{language.lower()}"
        return hashlib.md5(key.encode()).hexdigest()

    def get_cached_data(self, title: str, artist: str, language: str = None) -> Optional[str]:
        cache_key = self.get_cache_key(title, artist, language)
        with self.db.lock:
            cursor = self.db.conn.cursor()
            query = """
                SELECT lyrics FROM lyrics 
                WHERE cache_key = ? AND 
                      (? IS NULL OR language = ?) AND
                      datetime(timestamp) > datetime('now', '-{} days')
            """.format(self.EXPIRY_DAYS)
            cursor.execute(query, (cache_key, language, language))
            result = cursor.fetchone()
            if result:
                logger.info(f"Cache hit for '{title}' by '{artist}' with language '{language}'")
                return result[0]
        return None

    def save_to_cache(self, title: str, artist: str, lyrics: str, language: str = None):
        cache_key = self.get_cache_key(title, artist, language)
        with self.db.lock:
            self.db.conn.execute("""
                INSERT OR REPLACE INTO lyrics (cache_key, title, artist, language, lyrics, timestamp)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (cache_key, title, artist, language, lyrics))
            self.db.conn.commit()

    @staticmethod
    def clean_text(text: str) -> str:
        return unidecode.unidecode(text).lower().strip() if text else text

    @staticmethod
    def clean_title(title: str) -> str:
        return re.sub(r'\b(feat\.|ft\.)\s+\w+', '', title, flags=re.IGNORECASE).strip().lower() if title else title

    @staticmethod
    def clean_artists(artist: str) -> list:
        return [name.strip() for name in re.split(r'[;,]', artist)] if artist else []

    def is_match(self, extracted_artist: str, extracted_title: str, expected_artist: str, expected_title: str) -> bool:
        try:
            logger.info(f"Comparing '{extracted_artist}', '{extracted_title}' with '{expected_artist}', '{expected_title}'")
            query_artists = self.clean_artists(expected_artist)
            query_title = self.clean_title(expected_title)
            title_no_paren = re.sub(r'\s*\([^)]*\)', '', extracted_title).strip()

            artist_in_title = any(artist.lower() in title_no_paren.lower() for artist in query_artists)
            title_in_result = query_title.lower() in title_no_paren.lower()

            if artist_in_title and title_in_result:
                logger.info("Direct match found based on artist in title and title in result.")
                return True

            if extracted_artist.lower() in ["genius romanizations", "genius english translations"]:
                artist_match_ratio = 1.0
            else:
                artist_match_ratio = max(SequenceMatcher(None, a.lower(), extracted_artist.lower()).ratio() for a in query_artists)

            parenthetical = re.findall(r'\((.*?)\)', extracted_title)
            variants = [re.sub(r'\s*\(.*?\)', '', extracted_title).strip()] + parenthetical + [extracted_title]

            title_match_ratio = max(SequenceMatcher(None, query_title.lower(), variant.lower()).ratio() for variant in variants)

            if title_match_ratio > 0.6 and artist_match_ratio > 0.45:
                logger.info(f"Match found with title ratio {title_match_ratio} and artist ratio {artist_match_ratio}.")
                return True
            else:
                logger.info(f"No sufficient match. Title ratio: {title_match_ratio}, Artist ratio: {artist_match_ratio}.")
                return False
        except Exception as e:
            logger.error(f"Error in is_match: {e}")
            return False

    def reformat_lyrics_text(self, lyrics: str) -> str:
        patterns = [
            (r'\[\s*([^]]*?)\s*&\s*(?:\r?\n\s*)?([^]]*?)\s*\]', r'[\1 & \2]'),
            (r'\[([^]]+?):\s*([^]]+?)\s*&\s*(?:\r?\n\s*)?([^]]+?)\s*\]', r'[\1: \2 & \3]'),
            (r'\[([^\]]+?)\s*\r?\n\s*([^\]]+?)\]', r'[\1 \2]'),
            (r'\(\s*\r?\n\s*', '('),
            (r'\s*\r?\n\s*\)', ')'),
            (r'\s+\)', ')'),
            (r'\[\s*\r?\n\s*', '['),
            (r'\s*\r?\n\s*\]', ']'),
            (r'\s+\]', ']'),
            (r'!\s*\r?\n\s*([A-Za-z])', r'! \1'),
            (r'!\s+([A-Za-z])', r'! \1'),
            (r'\s*!\s*', '! '),
            (r'¡\s+', '¡'),
            (r'([.!?])\s+(?!¡)([A-Z])', r'\1\n\2'),
            (r'\n{2,}', '\n\n'),
            (r']\s*([A-Za-z¡])', r']\n\1'),
            (r'\(\\s*', '('),
            (r'\s*\)', ')'),
            (r'\n\s*\(', ' ('),
            (r'(?<!\n)\n?\s*\[([^\]]+)\]', r'\n\n[\1]'),
            (r'\n\s*\[Instrumental\]\s*\n', r'\n\n[Instrumental]\n\n'),
        ]
        for pattern, repl in patterns:
            lyrics = re.sub(pattern, repl, lyrics)
        return lyrics.strip()

    def scrape_lyrics(self, url: str) -> Optional[str]:
        if not url:
            logger.info("No URL provided for scraping lyrics.")
            return None
        logger.info(f"Scraping lyrics from URL: {url}")
        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            lyrics_containers = soup.find_all("div", attrs={"data-lyrics-container": "true"})
            if not lyrics_containers:
                if soup.find("div", string="This song is an instrumental"):
                    logger.info("The song is instrumental.")
                    return "This song is an instrumental"
                logger.warning("Lyrics containers not found.")
                return None
            lyrics = "\n".join([container.get_text(separator="\n").strip() for container in lyrics_containers])
            logger.info("Lyrics scraped successfully.")
            return self.reformat_lyrics_text(lyrics)
        except requests.RequestException as e:
            logger.error(f"Error scraping lyrics from {url}: {e}")
            return None

    def find_url_on_genius(self, title: str, artist: str, language: str = None) -> Optional[str]:
        if not self.api_key:
            logger.info("Genius API key not provided. Skipping Genius API search.")
            return None
        search_url = "https://api.genius.com/search"
        query = f"{title} {artist}"
        if language and language.lower() == 'en':
            query += ' english translation'
        logger.info(f"Searching Genius API for: {query}")
        params = {'q': query}
        try:
            response = requests.get(search_url, headers=self.genius_headers, params=params)
            if response.status_code == 429:
                logger.error("Genius API rate limit exceeded.")
                return None
            response.raise_for_status()
            hits = response.json().get("response", {}).get("hits", [])
            logger.info(f"Genius API returned {len(hits)} hits.")
            for hit in hits:
                result = hit['result']
                if self.is_match(result['primary_artist']['name'], result['title'], artist, title):
                    logger.info(f"Matching lyrics found on Genius: {result['url']}")
                    return result['url']
            logger.info("No matching lyrics found on Genius.")
        except requests.RequestException as e:
            logger.error(f"Genius search failed: {e}")
        return None

    def find_genius_url_using_google_if_no_genius_api(self, title: str, artist: str, language: str = None, initial_genius_url: str = None) -> Optional[str]:
        query = f"{title} {artist} genius.com lyrics"
        if language and language.lower() == 'en':
            query += ' english translation'
        logger.info(f"Searching Genius via Google for: {query}")
        params = {**self.google_params, 'q': query}
        try:
            response = requests.get("https://www.google.com/search", headers=self.google_headers, params=params)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.select('a[href]'):
                link = a['href']
                if "genius.com" in link:
                    link_match = re.search(r'(https?://genius\.com/[^\s&]+)', link)
                    if link_match:
                        url = link_match.group()

                        if initial_genius_url and url == initial_genius_url:
                            logger.info("Found same URL as Genius API - skipping verification")
                            return None

                        match = re.match(r'https?://genius\.com/(?P<extracted_artist>[^/]+)-(?P<extracted_title>[^/]+)-lyrics', url)
                        if match:
                            extracted_artist = match.group('extracted_artist').replace('-', ' ').title()
                            extracted_title = match.group('extracted_title').replace('-', ' ').title()
                            if self.is_match(extracted_artist, extracted_title, artist, title):
                                lyrics = self.scrape_lyrics(url)
                                if lyrics:
                                    logger.info("Lyrics found via find_genius_url_using_google_if_no_genius_api matching verified.")
                                    return lyrics
            logger.info("No matching lyrics found via find_genius_url_using_google_if_no_genius_api.")
        except requests.RequestException as e:
            logger.error(f"Google search failed: {e}")
        return None

    def google_search(self, title: str, artist: str, language: str = None) -> Optional[str]:
        queries = [
            f"{self.clean_title(title)} {self.clean_artists(artist)[0]} lyrics",
            f"{self.clean_title(title)} lyrics"
        ]
        for query in queries:
            logger.info(f"Performing Google search with query: '{query}'")
            lyrics = self.scrape_google_lyrics(query, artist_verification=(query == queries[1]), artist=artist)
            if lyrics:
                logger.info("Lyrics found via Google search.")
                return lyrics
        logger.info("No lyrics found via Google search.")
        return None

    def scrape_google_lyrics(self, query: str, artist_verification: bool, artist: str = None) -> Optional[str]:
        params = {**self.google_params, 'q': query}
        try:
            response = requests.get("https://www.google.com/search", headers=self.google_headers, params=params)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            if artist_verification and artist:
                if not any(name.lower() in soup.text.lower() for name in self.clean_artists(artist)):
                    logger.info("Artist verification failed in Google search results.")
                    return None
            for div in soup.select('div.ujudUb, div.PZPZlf, div[data-lyricid]'):
                lyrics = div.get_text(separator='\n').strip()
                if len(lyrics.split('\n')) > 4:
                    if not any(keyword in lyrics for keyword in ["Spotify", "YouTube", "Album"]):
                        logger.info("Valid lyrics found in Google search results.")
                        return self.reformat_lyrics_text(lyrics)
        except requests.RequestException as e:
            logger.error(f"Google scrape failed: {e}")
        return None

    def is_lyrics_in_english(self, lyrics: str) -> bool:
        if not lyrics:
            return False
        num_ascii = sum(1 for c in lyrics if ord(c) < 128)
        is_english = (num_ascii / len(lyrics)) > 0.9
        logger.info(f"Lyrics are {'mostly' if is_english else 'not'} in English.")
        return is_english

    def get_lyrics(self, title: str, artist: str, language: str = None, skip_google_search: bool = False, should_cache: bool = False) -> str:
        logger.info(f"Getting lyrics for Title: '{title}', Artist: '{artist}', Language: '{language}'")
        cached = self.get_cached_data(title, artist, language)
        if cached:
            logger.info("Returning cached lyrics.")
            return cached

        genius_url = self.find_url_on_genius(title, artist, language)
        lyrics = self.scrape_lyrics(genius_url) if genius_url else None

        if not lyrics:
            google_genius_result = self.find_genius_url_using_google_if_no_genius_api(title, artist, language, initial_genius_url=genius_url)
            if google_genius_result:
                lyrics = google_genius_result

        if not lyrics and not skip_google_search:
            lyrics = self.google_search(title, artist, language)

        if lyrics:
            if should_cache:
                self.save_to_cache(title, artist, lyrics, language)
                logger.info("Lyrics retrieved and cached successfully.")
            return lyrics

        return "Lyrics not found"

    def search_song(self, title: str, artist: str, language: str = None) -> Optional[str]:
        logger.info("Initiating search using Genius API.")
        url = self.find_url_on_genius(title, artist, language)
        if url:
            logger.info("Genius API search successful.")
            return self.scrape_lyrics(url)
        else:
            logger.info("Genius API search did not find any results.")
            return None

    def search_fetch_and_cache_alternate(self, title: str, artist: str, language: str):
        alternate = 'en'
        logger.info(f"Fetching and caching alternate language lyrics: '{alternate}'")
        lyrics = self.get_lyrics(title, artist, alternate, skip_google_search=True, should_cache=False)
        if lyrics and ("english" in title.lower() or "english" in artist.lower()):
            self.save_to_cache(title, artist, lyrics, alternate)
            logger.info("Alternate language lyrics cached successfully.")
        else:
            logger.info("Alternate language lyrics do not contain 'english' in title or artist. Not caching.")

app = FastAPI()
scraper = LyricLocate()

STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class LyricsResponse(BaseModel):
    title: str
    artist: str
    language: Optional[str]
    lyrics: str

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/get_lyrics", response_model=LyricsResponse)
def get_lyrics_endpoint(title: str, artist: str, language: Optional[str] = None, background_tasks: BackgroundTasks = None):
    logger.info(f"API request received for Title: '{title}', Artist: '{artist}', Language: '{language}'")
    
    should_cache = False
    if language is None:
        should_cache = True
    elif language.lower() == 'en':
        if "english" in title.lower() or "english" in artist.lower():
            should_cache = True
    
    cached = scraper.get_cached_data(title, artist, language)
    if cached:
        logger.info("Returning cached lyrics via API.")
        return LyricsResponse(title=title, artist=artist, language=language or "original", lyrics=cached)
    
    lyrics = scraper.get_lyrics(title, artist, language, should_cache=should_cache)
    if lyrics != "Lyrics not found":
        if not scraper.is_lyrics_in_english(lyrics) and language is None and background_tasks:
            logger.info("Lyrics not in English. Scheduling alternate language search.")
            background_tasks.add_task(scraper.search_fetch_and_cache_alternate, title, artist, language)
        logger.info("Returning fetched lyrics via API.")
        return LyricsResponse(title=title, artist=artist, language=language or "original", lyrics=lyrics)
    
    logger.warning("Lyrics not found. Raising HTTP 404.")
    raise HTTPException(status_code=404, detail="Lyrics not found")

if __name__ == "__main__":
    logger.info("Starting Uvicorn server.")
    uvicorn.run(app, host="0.0.0.0", port=19999)
