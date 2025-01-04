import os
import re
import hashlib
import logging
import requests
from difflib import SequenceMatcher
from typing import Optional
from bs4 import BeautifulSoup
from urllib.parse import unquote
# local imports
from database import LyricsDatabase
from spotify_handler import SpotifyHandler
from transliteration import transliterate_arabic

logger = logging.getLogger(__name__)

class LyricLocate:
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
        self.spotify_handler = SpotifyHandler()

        if not self.api_key:
            logger.warning("GENIUS_CLIENT_ACCESS_TOKEN not set - lyrics searches will be limited")
        if not (self.spotify_handler.spotify_client_id and self.spotify_handler.spotify_client_secret):
            logger.warning("Spotify API credentials missing - Spotify URL handling will be limited")

    @staticmethod
    def get_cache_key(title: str, artist: str, language: str = None) -> str:
        key = f"{title.lower()}_{artist.lower()}"
        if language:
            key += f"_{language.lower()}"
        return hashlib.md5(key.encode()).hexdigest()
    
    @staticmethod
    def normalize_url(url: str) -> str:
        return unquote(url)
    
    @staticmethod
    def normalize_text(text: str) -> str:
        text = re.sub(r'\([^)]*\)', '', text)
        text = re.sub(r'[^\w\s\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]', '', text)
        return text.lower().strip()

    @staticmethod
    def clean_title(title: str) -> str:
        return re.sub(r'\b(feat\.|ft\.)\s+\w+', '', title, flags=re.IGNORECASE).strip().lower() if title else title

    @staticmethod
    def clean_artists(artist: str) -> list:
        return [name.strip() for name in re.split(r'[;,]', artist)] if artist else []

    def get_cached_data(self, title: str, artist: str, language: str = "original") -> Optional[str]:
        return self.db.get_cached_data(title, artist, language)

    def save_to_cache(self, title: str, artist: str, lyrics: str, language: str = "original"):
        self.db.save_to_cache(title, artist, lyrics, language)

    def is_lyrics_in_english(self, lyrics: str) -> bool:
        if not lyrics:
            return False
        num_ascii = sum(1 for c in lyrics if ord(c) < 128)
        return (num_ascii / len(lyrics)) > 0.9

    def is_match(self, extracted_artist: str, extracted_title: str, expected_artist: str, expected_title: str) -> bool:
        logger.info(f"Comparing '{extracted_artist}', '{extracted_title}' with '{expected_artist}', '{expected_title}'")

        # Try matching without transliteration first
        if self._try_match(extracted_artist, extracted_title, expected_artist, expected_title, use_transliteration=False):
            return True

        # If no match and text contains non-ASCII, try with transliteration
        if (not extracted_title.isascii() or not extracted_artist.isascii() or 
            not expected_title.isascii() or not expected_artist.isascii()):
            return self._try_match(extracted_artist, extracted_title, expected_artist, expected_title, use_transliteration=True)

        return False

    def _try_match(self, extracted_artist: str, extracted_title: str, expected_artist: str, expected_title: str, use_transliteration: bool) -> bool:
        if "(instrumental)" in extracted_title.lower():
            logger.info("Ignoring instrumental version.")
            return False

        if use_transliteration:
            if not extracted_title.isascii():
                extracted_title = transliterate_arabic(extracted_title)
            if not extracted_artist.isascii():
                extracted_artist = transliterate_arabic(extracted_artist)
            if not expected_title.isascii():
                expected_title = transliterate_arabic(expected_title)
            if not expected_artist.isascii():
                expected_artist = transliterate_arabic(expected_artist)

        query_artists = self.clean_artists(expected_artist)
        query_title = self.clean_title(expected_title)
        title_no_paren = re.sub(r'\s*\([^)]*\)', '', extracted_title).strip()

        artist_in_title = any(artist.lower() in extracted_artist.lower() for artist in query_artists)
        title_in_result = query_title.lower() in title_no_paren.lower()

        if artist_in_title and title_in_result:
            return True

        if extracted_artist.lower() in ["genius romanizations", "genius english translations"]:
            artist_match_ratio = 1.0
        else:
            artist_match_ratio = max(SequenceMatcher(None, a.lower(), extracted_artist.lower()).ratio() for a in query_artists)

        parenthetical = re.findall(r'\((.*?)\)', extracted_title)
        variants = [re.sub(r'\s*\(.*?\)', '', extracted_title).strip()] + parenthetical + [extracted_title]

        title_match_ratio = max(SequenceMatcher(None, query_title.lower(), variant.lower()).ratio() for variant in variants)

        return title_match_ratio > 0.6 and artist_match_ratio > 0.45

    def reformat_lyrics_text(self, lyrics: str, language: str = None) -> str:
        unwanted_phrases = [
            "Something went wrong.",
            "Please try again.",
            "Translate to English",
            "Musixmatch"
        ]
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
        for phrase in unwanted_phrases:
            if phrase in lyrics:
                lyrics = lyrics.split(phrase)[0].strip()
                break
        lyrics_lines = lyrics.split('\n')
        lyrics_lines = [line for line in lyrics_lines if not line.startswith("Source:") and not line.startswith("Songwriters:")]

        if language == 'en':
            lyrics_lines = [line for line in lyrics_lines if all(ord(c) < 128 for c in line)]

        return '\n'.join(lyrics_lines)

    def scrape_lyrics(self, url: str) -> Optional[str]:
        if not url:
            return None
        logger.info(f"Scraping lyrics from URL: {url}")
        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            not_released_message = soup.find(string="Lyrics for this song have yet to be released. Please check back once the song has been released.")
            if not_released_message:
                logger.info("Lyrics have not been released yet.")
                return "Lyrics not found"

            lyrics_containers = soup.find_all("div", attrs={"data-lyrics-container": "true"})
            if not lyrics_containers:
                if soup.find("div", string="This song is an instrumental"):
                    return "This song is an instrumental"
                return None
            lyrics = "\n".join([container.get_text(separator="\n").strip() for container in lyrics_containers])
            return self.reformat_lyrics_text(lyrics)
        except requests.RequestException as e:
            logger.error(f"Error scraping lyrics from {url}: {e}")
            return None

    def find_genius_url(self, title: str, artist: str, language: str = "original") -> Optional[str]:
        if self.api_key:
            search_url = "https://api.genius.com/search"
            query = f"{title} {artist}"
            if language == 'en':
                query += ' english translation'
            params = {'q': query}
            headers = self.genius_headers
        else:
            search_url = "https://www.google.com/search"
            query = f"{title} {artist} genius.com lyrics"
            if language == 'en':
                query += ' english translation'
            params = {**self.google_params, 'q': query}
            headers = self.google_headers

        logger.info(f"Searching for Genius URL on google with query: {params['q']}")
        try:
            response = requests.get(search_url, headers=headers, params=params)
            if response.status_code == 429:
                logger.error("API rate limit exceeded.")
                return None
            response.raise_for_status()
            if self.api_key:
                hits = response.json().get("response", {}).get("hits", [])
                for hit in hits:
                    result = hit['result']
                    if self.is_match(result['primary_artist']['name'], result['title'], artist, title):
                        return result['url']
                # Retry with first artist if initial search fails
                first_artist = self.clean_artists(artist)[0] if self.clean_artists(artist) else ""
                if first_artist:
                    retry_query = f"{title} {first_artist}"
                    if language == 'en':
                        retry_query += ' english translation'
                    logger.info(f"Retrying search with first artist: {first_artist}, query: {retry_query}")
                    params['q'] = retry_query
                    response = requests.get(search_url, headers=headers, params=params)
                    response.raise_for_status()
                    hits = response.json().get("response", {}).get("hits", [])
                    for hit in hits:
                        result = hit['result']
                        if self.is_match(result['primary_artist']['name'], result['title'], first_artist, title):
                            return result['url']
            else:
                soup = BeautifulSoup(response.text, 'html.parser')
                for a in soup.select('a[href]'):
                    link = a['href']
                    if "genius.com" in link:
                        link_match = re.search(r'(https?://genius\.com/[^\s&]+)', link)
                        if link_match:
                            return link_match.group()
        except requests.RequestException as e:
            logger.error(f"Search failed: {e}")
        return None

    def scrape_google(self, title: str, artist: str, language: str = "original") -> Optional[str]:
        problematic_keywords = ["Genres", "Dance-pop", "Electronic dance music", "K-pop", "Spotify", "Apple Music", "YouTube", "YouTube Music", "Deezer", "Artist", "Album"]
        queries = [
            f"{self.clean_title(title)} {self.clean_artists(artist)[0]} lyrics",
            f"{self.clean_title(title)} lyrics"
        ]
        if language == 'en':
            queries = [
                f"{self.clean_title(title)} {self.clean_artists(artist)[0]} english translation lyrics",
                f"{self.clean_title(title)} english translation lyrics"
            ]
        for query in queries:
            logger.info(f"Performing Google search with query: '{query}'")
            params = {**self.google_params, 'q': query}
            try:
                response = requests.get("https://www.google.com/search", headers=self.google_headers, params=params)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')

                if query.endswith('lyrics') and artist:
                    extracted_artists = [div.get_text().strip() for div in soup.find_all('div', class_=['rVusze', 'iAIpCb PZPZlf'])]
                    if not any(self.is_match(extracted_artist, "", artist, "") for extracted_artist in extracted_artists):
                        continue

                for div in soup.select('div.ujudUb, div.PZPZlf, div[data-lyricid]'):
                    lyrics = div.get_text(separator='\n').strip()
                    if len(lyrics.split('\n')) > 4: # If the content has more than N lines, it is likely to be actual lyrics
                        found_keywords = [keyword for keyword in problematic_keywords if keyword in lyrics]
                        if len(found_keywords) >= 3:
                            logger.info(f"Skipping lyrics due to problematic keywords: {found_keywords}")
                            logger.info(f"Problematic lyrics: {lyrics}")
                            break  # Skip to the next query
                        logger.info(f"Scraped lyrics: {lyrics}")  # Log the scraped lyrics
                        formatted_lyrics = self.reformat_lyrics_text(lyrics, language)
                        return formatted_lyrics
            except requests.RequestException as e:
                logger.error(f"Google scrape failed: {e}")
        return None

    def scrape_musixmatch(self, title: str, artist: str, language: str = "original") -> Optional[str]:
        query = f"{title} {artist} lyrics site:musixmatch.com/lyrics"
        if language == 'en':
            query += " english translation"
        logger.info(f"Searching '{query}' on google")
        params = {**self.google_params, 'q': query}
        
        try:
            response = requests.get("https://www.google.com/search", headers=self.google_headers, params=params)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for a in soup.select('a[href]'):
                link = a['href']
                if "musixmatch.com/lyrics" in link:
                    url_match = re.search(r'(https?://www\.musixmatch\.com/lyrics/[^\s&]+)', link)
                    if url_match:
                        lyrics_url = unquote(url_match.group())
                        
                        if language != 'en':
                            lyrics_url = re.sub(r'/translation/.*$', '', lyrics_url)
                        
                        logger.info(f"Found Musixmatch URL: {lyrics_url}")
                        try:
                            lyrics_response = requests.get(lyrics_url, headers=self.google_headers)
                            lyrics_response.raise_for_status()
                            lyrics_soup = BeautifulSoup(lyrics_response.text, 'html.parser')
                            
                            # Simple fuzzy match on title
                            title_element = lyrics_soup.find(attrs={"data-testid": "lyrics-track-title"})
                            if title_element:
                                page_title = title_element.get_text().lower()
                                search_title = title.lower()
                                
                                # Transliterate if necessary
                                if not page_title.isascii():
                                    page_title = transliterate_arabic(page_title)
                                if not artist.isascii():
                                    artist = transliterate_arabic(artist)
                                
                                if self.is_match(extracted_artist=artist, extracted_title=page_title, expected_artist=artist, expected_title=search_title):
                                    lyrics_spans = lyrics_soup.select('.css-175oi2r.r-zd98yo')
                                    if lyrics_spans:
                                        lyrics = "\n".join(span.get_text(separator="\n").strip() for span in lyrics_spans)
                                        return self.reformat_lyrics_text(lyrics, language)
                        except requests.RequestException as e:
                            logger.error(f"Error fetching lyrics from {lyrics_url}: {e}")
                            continue
                        break
            return None
        except requests.RequestException as e:
            logger.error(f"Musixmatch scrape failed: {e}")
            return None

    def get_lyrics(
        self,
        title: str,
        artist: str,
        language: str = "original",
        should_cache: bool = False,
        attempted_remix_removal: bool = False
    ) -> str:
        logger.info(f"Getting lyrics for Title: '{title}', Artist: '{artist}', Language: '{language}'")

        cached = self.get_cached_data(title, artist, language)
        if cached and cached != "Lyrics not found":
            return cached

        if language == 'en':
            original_lyrics = self.get_lyrics(title, artist, 'original', should_cache)
            if original_lyrics and original_lyrics != "Lyrics not found":
                if self.is_lyrics_in_english(original_lyrics):
                    if should_cache:
                        self.save_to_cache(title, artist, original_lyrics, 'en')
                    return original_lyrics
                else:
                    logger.info("Original lyrics not in English. Searching for translation.")

        genius_url = self.find_genius_url(title, artist, language)
        lyrics = self.scrape_lyrics(genius_url) if genius_url else None

        if not lyrics:
            lyrics = self.scrape_google(title, artist, language)

        if lyrics and lyrics != "Lyrics not found":
            if language == 'en':
                if self.is_lyrics_in_english(lyrics):
                    if should_cache:
                        self.save_to_cache(title, artist, lyrics, 'en')
                    return lyrics
                else:
                    logger.info("Fetched lyrics are not in English. Not caching under 'en'.")
                    return "Lyrics not found"
            else:
                if should_cache:
                    self.save_to_cache(title, artist, lyrics, 'original')
                    if self.is_lyrics_in_english(lyrics):
                        self.save_to_cache(title, artist, lyrics, 'en')
                return lyrics

        if lyrics is None:
            lyrics = self.scrape_musixmatch(title, artist, language)

        if lyrics and lyrics != "Lyrics not found":
            if language == 'en':
                if self.is_lyrics_in_english(lyrics):
                    if should_cache:
                        self.save_to_cache(title, artist, lyrics, 'en')
                    return lyrics
                else:
                    logger.info("Fetched lyrics are not in English. Not caching under 'en'.")
                    return "Lyrics not found"
            else:
                if should_cache:
                    self.save_to_cache(title, artist, lyrics, 'original')
                    if self.is_lyrics_in_english(lyrics):
                        self.save_to_cache(title, artist, lyrics, 'en')
                return lyrics

        if not lyrics and not attempted_remix_removal and 'remix' in title.lower():
            new_title = re.sub(r'\s*\(.*remix.*\)', '', title, flags=re.IGNORECASE).strip()
            if new_title != title:
                logger.info(f"No lyrics found. Retrying with title without remix: '{new_title}'")
                lyrics = self.get_lyrics(new_title, artist, language, should_cache, attempted_remix_removal=True)
                if lyrics and lyrics != "Lyrics not found" and should_cache:
                    self.save_to_cache(title, artist, lyrics, language)
                return lyrics

        return "Lyrics not found"

    def fetch_lyrics_background(self, title: str, artist: str, language: str):
        logger.info(f"Background Task: Fetching {language} lyrics for Title: '{title}', Artist: '{artist}'")

        if 'remix' in title.lower():
            clean_title = re.sub(r'\s*\(.*remix.*\)', '', title, flags=re.IGNORECASE).strip()
            if clean_title != title:
                logger.info(f"Background Task: Removing remix from title. New title: '{clean_title}'")
                title = clean_title

        lyrics = self.get_lyrics(title, artist, language, should_cache=True, attempted_remix_removal=True)
        if lyrics and lyrics != "Lyrics not found":
            logger.info(f"Background Task: {language.capitalize()} lyrics fetched and cached successfully.")
        else:
            logger.warning(f"Background Task: {language.capitalize()} lyrics could not be fetched.")
