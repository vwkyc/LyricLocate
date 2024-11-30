import os
from dotenv import load_dotenv, find_dotenv
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
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.responses import FileResponse
import uvicorn
from typing import Optional
from pydantic import BaseModel
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables from .env file
dotenv_path = find_dotenv(".env")
if dotenv_path:
    load_dotenv(dotenv_path)
    logger.info("Loaded environment variables from .env")
else:
    logger.warning("No .env file found. It's recommended to provide a GENIUS_CLIENT_ACCESS_TOKEN environment variable for full functionality.")

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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36',
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
                logger.info(f"Cache hit for '{title}' by '{artist}' with language '{language}'")
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

    def find_url_on_genius(self, title, artist, language=None) -> Optional[str]:
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
                    extracted_artist = hit['result']['primary_artist']['name']
                    extracted_title = hit['result']['title']
                    if self.is_match(extracted_artist, extracted_title, artist, title):
                        logger.info(f"Match found for '{title}' by '{artist}'")
                        return hit['result']['url']

                logger.warning(f"No valid match found for '{title}' by '{artist}' on Genius")
            else:
                logger.warning(f"No results found for {title} by {artist} on Genius")
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
        return None

    def is_match(self, extracted_artist: str, extracted_title: str, expected_artist: str, expected_title: str) -> bool:
        try:
            logger.info(f"Comparing extracted artist: '{extracted_artist}', extracted title: '{extracted_title}' with expected artist: '{expected_artist}', expected title: '{expected_title}'")
            # Use the same matching logic as before
            query_artists = [name.strip() for name in re.split(r'[;,]', expected_artist)]
            query_title = self.clean_title(expected_title)

            # Check if title contains both artist and song title
            title_without_paren = re.sub(r'\s*\([^)]*\)', '', extracted_title).strip()
            
            # Check if any artist name is in the title and the query title is also in the title
            artist_in_title = any(artist.lower() in title_without_paren.lower() for artist in query_artists)
            title_in_result = query_title.lower() in title_without_paren.lower()
            
            if artist_in_title and title_in_result:
                logger.info("Combined artist and title found in result title. Bypassing ratio checks.")
                return True

            if extracted_artist.lower() in ["genius romanizations", "genius english translations"]:
                artist_match_ratio = 1.0
                logger.info("Skipping artist matching due to Genius Romanization/Translation")
            else:
                artist_match_ratio = max(
                    SequenceMatcher(None, query_artist.lower(), extracted_artist.lower()).ratio()
                    for query_artist in query_artists
                )

            # Title matching with variants
            parenthetical_matches = re.findall(r'\((.*?)\)', extracted_title)
            paren_texts = [match.strip() for match in parenthetical_matches]
            result_title_no_paren = re.sub(r'\s*\(.*?\)', '', extracted_title).strip()
            title_variants = [result_title_no_paren] + paren_texts + [extracted_title]

            title_match_ratio = max(
                SequenceMatcher(None, query_title.lower(), variant.lower()).ratio()
                for variant in title_variants
            )

            logger.info(f"Title match ratio: {title_match_ratio:.3f}, Artist match ratio: {artist_match_ratio:.3f}")
            
            return title_match_ratio > 0.6 and artist_match_ratio > 0.45

        except Exception as e:
            logger.error(f"Error in is_match: {e}")
            return False

    def clean_lyrics_text(self, lyrics: str) -> str:
        """Clean up the lyrics by removing unnecessary spaces and newlines."""
        if not lyrics:
            return ""

        # 1. Fix section headers with ampersands, handling possible Windows-style newlines
        lyrics = re.sub(
            r'\[\s*([^]]*?)\s*&\s*(?:\r?\n\s*)?([^]]*?)\s*\]',
            r'[\1 & \2]',
            lyrics
        )
        lyrics = re.sub(
            r'\[([^]]+?):\s*([^]]+?)\s*&\s*(?:\r?\n\s*)?([^]]+?)\s*\]',
            r'[\1: \2 & \3]',
            lyrics
        )

        # 2. Remove any remaining newlines within square brackets
        lyrics = re.sub(
            r'\[([^\]]+?)\s*\r?\n\s*([^\]]+?)\]',
            r'[\1 \2]',
            lyrics
        )

        # 3. Clean up parentheses by removing newlines inside them
        lyrics = re.sub(r'\(\s*\r?\n\s*', '(', lyrics)
        lyrics = re.sub(r'\s*\r?\n\s*\)', ')', lyrics)
        lyrics = re.sub(r'\s+\)', ')', lyrics)

        # 4. Clean up square brackets by removing newlines inside them
        lyrics = re.sub(r'\[\s*\r?\n\s*', '[', lyrics)
        lyrics = re.sub(r'\s*\r?\n\s*\]', ']', lyrics)
        lyrics = re.sub(r'\s+\]', ']', lyrics)

        # 5. Fix exclamation marks followed by text without unwanted spaces
        lyrics = re.sub(r'!\s*\r?\n\s*([A-Za-z])', r'! \1', lyrics)
        lyrics = re.sub(r'!\s+([A-Za-z])', r'! \1', lyrics)

        # 6. Normalize spacing around punctuation
        lyrics = re.sub(r'\s*!\s*', '! ', lyrics)
        lyrics = re.sub(r'¡\s+', '¡', lyrics)

        # 7. Ensure punctuation followed by uppercase letters starts a new line,
        # but avoid splitting when followed by '¡' to handle cases like "¡DY!"
        lyrics = re.sub(r'([.!?])\s+(?!¡)([A-Z])', r'\1\n\2', lyrics)

        # 8. Replace multiple newlines with double newlines (for paragraph spacing)
        lyrics = re.sub(r'\n{2,}', '\n\n', lyrics)

        # 9. Ensure there is a newline after section headers if missing
        lyrics = re.sub(r']\s*([A-Za-z¡])', r']\n\1', lyrics)

        # 10. Final cleanup: Remove any residual newlines within square brackets
        lyrics = re.sub(
            r'\[([^\]]+?)\]',
            lambda m: '[' + m.group(1).replace('\n', ' ').replace('\r', ' ').strip() + ']',
            lyrics
        )

        # 11. Remove any space before closing parentheses to fix cases like "(¡DY! )"
        lyrics = re.sub(r'\(\s*', '(', lyrics)  # Remove space after '('
        lyrics = re.sub(r'\s*\)', ')', lyrics)  # Remove space before ')'

        # 12. Remove newlines before opening parentheses to keep parentheticals on the same line
        lyrics = re.sub(r'\n\s*\(', ' (', lyrics)

        # 13. Add spacing above bracketed sections like [Verse], [Chorus], etc.
        lyrics = re.sub(r'(?<!\n)\n?\s*\[([^\]]+)\]', r'\n\n[\1]', lyrics)

        # 14. Add spacing above and below [Instrumental] sections
        lyrics = re.sub(r'\n\s*\[Instrumental\]\s*\n', r'\n\n[Instrumental]\n\n', lyrics)

        return lyrics.strip()

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
            lyrics = self.clean_lyrics_text(lyrics)
            return lyrics
        except Exception as e:
            logger.error(f"Error parsing lyrics from {url}: {e}")
            return None

    def gplusg_search_and_scrape(self, title: str, artist: str, language: str = None) -> Optional[str]:
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
                        if lyrics:
                            # Extract artist and title from the Genius page
                            try:
                                response_genius = requests.get(genius_url)
                                response_genius.raise_for_status()
                                soup_genius = BeautifulSoup(response_genius.text, 'html.parser')

                                # Define selectors
                                artist_selectors = [
                                    '.HeaderArtistAndTracklistdesktop__ListArtists-sc-4vdeb8-1',
                                    'a[class*="Artist"]',
                                    '[data-testid="artist-name"]',
                                ]

                                title_selectors = [
                                    '.SongHeaderdesktop__HiddenMask-sc-1effuo1-11',
                                    'h1[class*="Title"]',
                                    '[data-testid="song-title"]',
                                ]

                                # Get genius artist and title
                                genius_artist = None
                                genius_title = None

                                for selector in artist_selectors:
                                    artist_elem = soup_genius.select_one(selector)
                                    if artist_elem:
                                        genius_artist = artist_elem.get_text().strip()
                                        logger.info(f"Found artist: '{genius_artist}' using selector: {selector}")
                                        break

                                for selector in title_selectors:
                                    title_elem = soup_genius.select_one(selector)
                                    if title_elem:
                                        genius_title = title_elem.get_text().strip()
                                        logger.info(f"Found title: '{genius_title}' using selector: {selector}")
                                        break

                                if genius_artist and genius_title:
                                    if self.is_match(genius_artist, genius_title, artist, title):
                                        logger.info(f"Verified artist and title match for '{artist}' and '{title}'")
                                        return lyrics
                                    else:
                                        logger.warning(f"Artist or title verification failed for '{artist}' and '{title}' on {genius_url}")
                            except requests.RequestException as e:
                                logger.error(f"Request failed while verifying Genius page {genius_url}: {e}")
                                continue

            logger.info(f"No valid Genius link found in Google results for '{title}' by '{artist}'")
        except requests.RequestException as e:
            logger.error(f"Request to Google search failed: {e}")
        
        return None

    def google_search(self, title: str, artist: str, language: str = None) -> Optional[str]:
        clean_title = self.clean_title(title)
        clean_artists = self.clean_artist(artist)

        queries = [
            f"{clean_title} {clean_artists[0]} lyrics",
            f"{clean_title} lyrics"
        ]

        for query in queries:
            lyrics = self.scrape_google_lyrics(query, artist_verification=(query == queries[1]), artist=artist)
            if lyrics:
                return lyrics

        return None

    def scrape_google_lyrics(self, query: str, artist_verification: bool, artist: str = None) -> Optional[str]:
        url = "https://www.google.com/search"
        params = {**self.google_params, 'q': query}
        
        # Add logging for search URL and query with proper URL encoding
        import urllib.parse
        search_url = f"{url}?{urllib.parse.urlencode(params)}"
        logger.info(f"Performing Google search with URL: {search_url}")
        logger.info(f"Search query: {query}")
        
        exclude_keywords = ["Spotify", "YouTube", "Album"]
        try:
            response = requests.get(url, headers=self.google_headers, params=params)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
            
                artist_found = False
                song_info = soup.select_one('div.PZPZlf[data-attrid="subtitle"]')
                if song_info:
                    song_info_text = song_info.text.lower()
                    if artist_verification and artist:
                        artist_names = [name.strip().lower() for name in artist.split(',')]
                    
                        if any(name in song_info_text for name in artist_names):
                            artist_found = True
                        else:
                            logger.info(f"Artist not found in subtitle. Checking About section.")
                
                if not artist_found and artist_verification and artist:
                    about_section = soup.find('span', class_='mgAbYb OSrXXb RES9jf pb3iw', string='About')
                    if about_section:
                        about_content = about_section.find_next('div', class_='PZPZlf')
                        if about_content:
                            about_text = about_content.get_text().lower()
                            if any(name.lower() in about_text for name in artist.split(',')):
                                logger.info(f"Artist '{artist}' found in About section")
                                artist_found = True
                            else:
                                logger.warning(f"Artist '{artist}' not found in About section")
                
                if not artist_found and artist_verification:
                    logger.warning(f"Found lyrics, but they don't match the artist: '{artist}'. Lyrics won't be saved.")
                    return None

                selectors = ['div.ujudUb', 'div.PZPZlf', 'div[data-lyricid]', 'div.PZPZlf.zloOqf']
                             
                for selector in selectors:
                    lyrics_divs = soup.select(selector)
                    if lyrics_divs:
                        lyrics = '\n'.join([div.get_text(separator='\n') for div in lyrics_divs])
                        if len(lyrics.split('\n')) > 4:
                            lines = lyrics.split('\n')
                            youtube_count = sum(1 for line in lines if 'YouTube' in line)

                            yt_count_exceeded = youtube_count >= 2
                            r_keywords = any(keyword.lower() in lyrics.lower() for keyword in exclude_keywords)
                            r_genius_and_youtube = "Genius Lyrics" in lyrics and "YouTube" in lyrics
                            r_official_lyric_video = "Official Lyric Video" in lyrics
                            r_youtube_and_shazam = "YouTube" in lyrics and "Shazam" in lyrics
                            r_youtube_mentions = any('YouTube' in lines[i] and 'YouTube' in lines[i+1] for i in range(len(lines) - 1))
                            r_youtube_in_first_three_lines = any(line.strip().endswith('YouTube') for line in lines[:3])
                            r_wikipedia_lyrics_description = any(keyword.lower() in line.lower() for keyword in ["Wikipedia", "Lyrics", "Description"] for line in lines)
                            r_contains_song_by = any(keyword.lower() in line.lower() for keyword in ["Artist", ":", "Song by"] for line in lines)

                            if (
                                yt_count_exceeded or r_keywords or r_genius_and_youtube or r_official_lyric_video or r_youtube_and_shazam or
                                r_youtube_mentions or r_youtube_in_first_three_lines or r_wikipedia_lyrics_description or r_contains_song_by
                            ):
                                logger.warning(f"Excluded non-lyric content for '{query}' using selector '{selector}'")
                                return None
                            logger.debug(f"Lyrics found for '{query}' using selector '{selector}'")
                            return lyrics.strip()                
            else:
                logger.error(f"Error fetching page: {response.status_code}")
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
        return None

    def is_lyrics_in_english(self, lyrics: str) -> bool:
        """Determine if the lyrics are predominantly in English."""
        if not lyrics:
            return False
        num_ascii = sum(1 for c in lyrics if ord(c) < 128)
        return (num_ascii / len(lyrics)) > 0.9

    def get_lyrics(self, title, artist, language=None, skip_google_search=False):
        cached_lyrics = self.get_cached_data(title, artist, language)
        if cached_lyrics:
            return cached_lyrics

        # Try Genius first, then google+genius
        lyrics = self.search_song(title, artist, language) or \
                 self.gplusg_search_and_scrape(title, artist, language)
        
        # Conditionally add google_search
        if not skip_google_search:
            lyrics = lyrics or self.google_search(title, artist, language)

        if lyrics:
            self.save_to_cache(title, artist, lyrics, language)
            return lyrics

        # Return a placeholder string if no lyrics found
        return "Lyrics not found"

    def search_fetch_and_cache_alternate(self, title, artist, language):
        alternate_language = 'en'
        # Fetch and cache the alternate lyrics without using google_search
        alternate_lyrics = self.get_lyrics(title, artist, alternate_language, skip_google_search=True)
        if alternate_lyrics:
            self.save_to_cache(title, artist, alternate_lyrics, alternate_language)

    def search_song(self, title, artist, language=None):
        url = self.find_url_on_genius(title, artist, language)
        return self.scrape_lyrics(url) if url else None

app = FastAPI()

class LyricsResponse(BaseModel):
    title: str
    artist: str
    language: Optional[str]
    lyrics: str

scraper = LyricLocate()

STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/get_lyrics", response_model=LyricsResponse)
def get_lyrics(title: str, artist: str, language: Optional[str] = None, background_tasks: BackgroundTasks = None):
    # First check cache
    cached_lyrics = scraper.get_cached_data(title, artist, language)
    if cached_lyrics:
        return LyricsResponse(title=title, artist=artist, language=language or "original", lyrics=cached_lyrics)
    
    # If not in cache, fetch fresh lyrics
    lyrics = scraper.get_lyrics(title, artist, language)
    if lyrics and lyrics != "Lyrics not found":
        is_english = scraper.is_lyrics_in_english(lyrics)
        # Only add background task if lyrics are not in English and language is not specified
        if not is_english and language is None:
            if background_tasks:
                background_tasks.add_task(scraper.search_fetch_and_cache_alternate, title, artist, language)
        return LyricsResponse(title=title, artist=artist, language=language or "original", lyrics=lyrics)
    else:
        raise HTTPException(status_code=404, detail="Lyrics not found")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=19999)
