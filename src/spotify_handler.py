import os
import base64
import logging
import requests
from urllib.parse import urlparse
from typing import Optional, Tuple
from database import LyricsDatabase
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class SpotifyHandler:
    def __init__(self):
        self.spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        self.spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        self.spotify_access_token = self._get_spotify_token() if self.spotify_client_id and self.spotify_client_secret else None
        self.db = LyricsDatabase()

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

    def get_track_info(self, spotify_url: str) -> Optional[Tuple[str, str]]:
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
                self.cache_spotify_track(spotify_url, title, artist)
                return title, artist

            except Exception as e:
                logger.error(f"Failed to get track info from Spotify API: {e}")

        logger.warning("Falling back to web scraping for Spotify track info")
        try:
            response = requests.get(spotify_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            title_tag = soup.find('meta', property='og:title')
            artist_tag = soup.find('meta', property='og:description')
            
            if title_tag and artist_tag:
                title = title_tag.get('content', '').split(' - ')[0].strip()
                artist = artist_tag.get('content', '').split(' · ')[0].strip()
                if title and artist:
                    self.cache_spotify_track(spotify_url, title, artist)
                    return title, artist

        except Exception as e:
            logger.error(f"Failed to scrape track info from Spotify page: {e}")

        return None

    def get_cached_spotify_track(self, spotify_url: str) -> Optional[Tuple[str, str]]:
        return self.db.get_cached_spotify_track(spotify_url)

    def cache_spotify_track(self, spotify_url: str, title: str, artist: str):
        self.db.cache_spotify_track(spotify_url, title, artist)
