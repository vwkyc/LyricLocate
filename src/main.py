import os
import re
import threading
import hashlib
import logging
import time
import base64
import json
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
import sqlite3
from bs4 import BeautifulSoup
from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
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

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    logger.warning("SPOTIFY_CLIENT_ID and/or SPOTIFY_CLIENT_SECRET not set. Will fallback to web scraping for Spotify links.")

class LyricsDatabase:
    def __init__(self, db_path="../cache/lyrics.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self.init_db()

    def init_db(self):
        with self.conn:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS lyrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT UNIQUE,
                title TEXT,
                artist TEXT,
                language TEXT,
                lyrics TEXT,
                timestamp DATETIME,
                UNIQUE(title, artist, language)
            )""")
            
            self.conn.execute("""CREATE TABLE IF NOT EXISTS spotify_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spotify_url TEXT UNIQUE,
                title TEXT,
                artist TEXT,
                timestamp DATETIME
            )""")
            
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_key ON lyrics(cache_key)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON lyrics(timestamp)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_spotify_url ON spotify_cache(spotify_url)")

class LyricLocate:
    EXPIRY_DAYS = 24
    def __init__(self):
        self.api_key = os.getenv("GENIUS_CLIENT_ACCESS_TOKEN")
        self.genius_headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
        self.google_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/'
        }
        self.google_params = {'hl': 'en'}
        self.db = LyricsDatabase()

        self.spotify_client_id = SPOTIFY_CLIENT_ID
        self.spotify_client_secret = SPOTIFY_CLIENT_SECRET
        self.spotify_access_token = self._get_spotify_token() if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET else None

    @staticmethod
    def get_cache_key(title: str, artist: str, language: str = None) -> str:
        key = f"{title.lower()}_{artist.lower()}"
        if language:
            key += f"_{language.lower()}"
        return hashlib.md5(key.encode()).hexdigest()

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
        
        with self.db.lock:
            cursor = self.db.conn.cursor()
            cursor.execute(query, params)
            result = cursor.fetchone()
            if result:
                logger.info(f"Cache hit for '{title}' by '{artist}' with language '{language}'")
                return result[0]
        return None

    def save_to_cache(self, title: str, artist: str, lyrics: str, language: str = "original"):
        storage_language = language
        
        cache_key = self.get_cache_key(title, artist, storage_language)
        with self.db.lock:
            self.db.conn.execute("""
                INSERT OR REPLACE INTO lyrics (cache_key, title, artist, language, lyrics, timestamp)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (cache_key, title, artist, storage_language, lyrics))
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
            (r'([.!?])\s+(?![^\(]*\))(?=[A-Z])', r'\1\n'),
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

    def find_url_on_genius(self, title: str, artist: str, language: str = "original") -> Optional[str]:
        if not self.api_key:
            logger.info("Genius API key not provided. Skipping Genius API search.")
            return None
        search_url = "https://api.genius.com/search"
        query = f"{title} {artist}"
        if language == 'en':
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

    def find_genius_url_using_google_if_no_genius_api(self, title: str, artist: str, language: str = "original", initial_genius_url: str = None) -> Optional[str]:
        query = f"{title} {artist} genius.com lyrics"
        if language == 'en':
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

    def google_search(self, title: str, artist: str, language: str = "original") -> Optional[str]:
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
                extracted_artists = [div.get_text().strip() for div in soup.find_all('div', class_='rVusze')]
                if not any(
                    self.is_match(extracted_artist, "", artist, "") 
                    for extracted_artist in extracted_artists
                ):
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
    
    def get_cached_spotify_track(self, spotify_url: str) -> Optional[tuple[str, str]]:
        try:
            base_url = spotify_url.split('?')[0] if '&' not in spotify_url else spotify_url.split('&')[0]
            with self.db.lock:
                cursor = self.db.conn.cursor()
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
            with self.db.lock:
                self.db.conn.execute("""
                    INSERT OR REPLACE INTO spotify_cache 
                    (spotify_url, title, artist, timestamp)
                    VALUES (?, ?, ?, datetime('now'))
                """, (base_url, title, artist))
                self.db.conn.commit()
                logger.info(f"Cached Spotify track info for URL: {base_url}")
        except Exception as e:
            logger.error(f"Error caching Spotify track info: {e}")

    def _get_spotify_token(self) -> Optional[str]:
        """Get Spotify access token using client credentials"""
        try:
            auth = base64.b64encode(f"{self.spotify_client_id}:{self.spotify_client_secret}".encode()).decode()
            response = requests.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {auth}"},
                data={"grant_type": "client_credentials"}
            )
            response.raise_for_status()
            return response.json()["access_token"]
        except Exception as e:
            logger.error(f"Failed to get Spotify token: {e}")
            return None

    def extract_track_id(self, spotify_url: str) -> Optional[str]:
        try:
            base_url = spotify_url.split('?')[0] if '&' not in spotify_url else spotify_url.split('&')[0]
            
            parsed = urlparse(base_url)
            if parsed.netloc not in ['open.spotify.com', 'spotify.com']:
                logger.warning(f"Not a valid Spotify URL: {spotify_url}")
                return None

            path_parts = parsed.path.split('/')
            if len(path_parts) < 3 or path_parts[1] != 'track':
                logger.warning(f"Not a valid Spotify track URL: {spotify_url}")
                return None

            track_id = path_parts[2]
            logger.info(f"Extracted Spotify track ID: {track_id}")
            return track_id

        except Exception as e:
            logger.error(f"Failed to extract track ID from URL {spotify_url}: {e}")
            return None

    def get_track_info(self, spotify_url: str) -> Optional[tuple[str, str]]:
        """Get track info from Spotify API or web scraping"""
        cached = self.get_cached_spotify_track(spotify_url)
        if cached:
            return cached

        track_id = self.extract_track_id(spotify_url)
        if not track_id:
            return None

        if self.spotify_access_token:
            try:
                response = requests.get(
                    f"https://api.spotify.com/v1/tracks/{track_id}",
                    headers={"Authorization": f"Bearer {self.spotify_access_token}"}
                )
                response.raise_for_status()
                track = response.json()
                title = track["name"]
                artist = ", ".join(artist["name"] for artist in track["artists"])
                
                # Cache the result
                self.cache_spotify_track(spotify_url, title, artist)
                return title, artist
                
            except Exception as e:
                logger.error(f"Failed to get track info from Spotify API: {e}")
        
        # If we get here, either API failed or we don't have credentials
        logger.warning("Falling back to web scraping for Spotify track info")
        try:
            response = requests.get(spotify_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try to extract from meta tags
            title_tag = soup.find('meta', property='og:title')
            artist_tag = soup.find('meta', property='og:description')
            
            if title_tag and artist_tag:
                title = title_tag.get('content', '').split(' - ')[0].strip()
                artist = artist_tag.get('content', '').split(' · ')[0].strip()
                if title and artist:
                    # Cache the scraped result
                    self.cache_spotify_track(spotify_url, title, artist)
                    return title, artist
                    
        except Exception as e:
            logger.error(f"Failed to scrape track info from Spotify page: {e}")
        
        return None

    def get_lyrics(self, title: str, artist: str, language: str = "original", skip_google_search: bool = False, should_cache: bool = False) -> str:
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

        if lyrics and lyrics != "Lyrics not found":
            if should_cache:
                self.save_to_cache(title, artist, lyrics, language)
                logger.info("Lyrics retrieved and cached successfully.")
                
                # Check if 'original' lyrics are in English and cache as 'en'
                if language == 'original' and self.is_lyrics_in_english(lyrics):
                    self.save_to_cache(title, artist, lyrics, 'en')
                    logger.info("Original lyrics are in English. Cached as 'en' as well.")
            
            return lyrics

        return "Lyrics not found"

    def fetch_original_lyrics(self, title: str, artist: str) -> None:
        logger.info(f"Background Task: Fetching original lyrics for Title: '{title}', Artist: '{artist}'")
        original_lyrics = self.get_lyrics(title, artist, language='original', skip_google_search=False, should_cache=True)
        
        if original_lyrics and original_lyrics != "Lyrics not found":
            logger.info("Background Task: Original lyrics fetched and cached successfully.")
            
            # Check if 'original' lyrics are in English
            if self.is_lyrics_in_english(original_lyrics):
                # Cache the 'original' lyrics again under 'en'
                self.save_to_cache(title, artist, original_lyrics, 'en')
                logger.info("Background Task: Original lyrics are in English. Cached as 'en' as well.")
            else:
                logger.info("Background Task: Original lyrics are not in English. No additional caching required.")
        else:
            logger.warning("Background Task: Original lyrics could not be fetched.")

    def search_song(self, title: str, artist: str, language: str = "original") -> Optional[str]:
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
        logger.info(f"Background Task: Fetching and caching alternate language lyrics: '{alternate}'")
        
        # Check if 'en' lyrics are already cached
        cached_en = self.get_cached_data(title, artist, 'en')
        if cached_en:
            logger.info("Background Task: 'en' lyrics are already cached. No need to fetch again.")
            return
        
        # Fetch 'en' lyrics and cache them
        lyrics = self.get_lyrics(title, artist, alternate, skip_google_search=False, should_cache=True)
        if lyrics and lyrics != "Lyrics not found":
            if self.is_lyrics_in_english(lyrics):
                logger.info("Background Task: Alternate language lyrics fetched and cached successfully.")
            else:
                logger.info("Background Task: Alternate language lyrics are not in English. Not caching.")
        else:
            logger.info("Background Task: Alternate language lyrics not found. Not caching.")

app = FastAPI()
lyric_locator = LyricLocate()

STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class LyricsResponse(BaseModel):
    title: str
    artist: str
    language: Optional[str]
    lyrics: str

def validate_language(language: Optional[str]) -> Optional[str]:
    if language:
        sanitized = language.strip().lower()
        if sanitized == "en":
            return "en"
        elif sanitized in ["original", "none"]:
            return "original"
        else:
            raise HTTPException(status_code=400, detail="Invalid language parameter. Only 'en' and 'original' are accepted.")
    return "original"

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/get_lyrics_from_spotify", response_model=LyricsResponse)
def get_lyrics_from_spotify_endpoint(
    spotify_url: str, 
    language: Optional[str] = Query(None, regex="^(en|original)$", description="Language code, 'en' or 'original' are accepted."),
    background_tasks: BackgroundTasks = None
):
    logger.info(f"API request received for Spotify URL: '{spotify_url}' with language: '{language}'")
    
    sanitized_language = validate_language(language)
    
    track_info = lyric_locator.get_track_info(spotify_url)
    if not track_info:
        raise HTTPException(status_code=400, detail="Could not extract track information from Spotify URL")
    
    title, artist = track_info
    return get_lyrics_endpoint(title=title, artist=artist, language=sanitized_language, background_tasks=background_tasks)

@app.get("/api/get_lyrics", response_model=LyricsResponse)
def get_lyrics_endpoint(
    title: str, 
    artist: str, 
    language: Optional[str] = Query(None, regex="^(en|original)$", description="Language code, 'en' or 'original' are accepted."),
    background_tasks: BackgroundTasks = None
):
    logger.info(f"API request received for Title: '{title}', Artist: '{artist}', Language: '{language}'")
    
    sanitized_language = validate_language(language)
    
    should_cache = False
    if sanitized_language in ['original', 'en']:
        should_cache = True  # Always cache when language is 'original' or 'en'
    
    cached = lyric_locator.get_cached_data(title, artist, sanitized_language)
    if cached:
        response_language = sanitized_language
        logger.info("Returning cached lyrics via API.")
        return LyricsResponse(title=title, artist=artist, language=response_language, lyrics=cached)
    
    lyrics = lyric_locator.get_lyrics(title, artist, sanitized_language, should_cache=should_cache)
    if lyrics != "Lyrics not found":
        if sanitized_language == 'original' and not lyric_locator.is_lyrics_in_english(lyrics) and background_tasks:
            logger.info("Lyrics not in English. Scheduling alternate language search.")
            background_tasks.add_task(lyric_locator.search_fetch_and_cache_alternate, title, artist, sanitized_language)
        
        if sanitized_language == 'en' and background_tasks:
            logger.info("Scheduling background task to fetch and cache 'original' lyrics.")
            background_tasks.add_task(lyric_locator.fetch_original_lyrics, title, artist)
        
        response_language = sanitized_language
        logger.info("Returning fetched lyrics via API.")
        return LyricsResponse(title=title, artist=artist, language=response_language, lyrics=lyrics)
    
    logger.warning("Lyrics not found. Raising HTTP 404.")
    raise HTTPException(status_code=404, detail="Lyrics not found")

if __name__ == "__main__":
    logger.info("Starting Uvicorn server.")
    uvicorn.run(app, host="0.0.0.0", port=19999)
