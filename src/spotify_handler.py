import os
import base64
import logging
import requests
from urllib.parse import urlparse
from typing import Optional, Tuple
from bs4 import BeautifulSoup
from database import LyricsDatabase

logger = logging.getLogger(__name__)

class SpotifyHandler:
    def __init__(self):
        self.spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        self.spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        self.spotify_access_token = (
            self._get_spotify_token()
            if self.spotify_client_id and self.spotify_client_secret
            else None
        )
        self.db = LyricsDatabase()

    def _get_spotify_token(self) -> Optional[str]:
        """Get Spotify access token using client credentials"""
        if not (self.spotify_client_id and self.spotify_client_secret):
            logger.warning("Spotify API credentials missing")
            return None
        try:
            auth = base64.b64encode(f"{self.spotify_client_id}:{self.spotify_client_secret}".encode()).decode()
            response = requests.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {auth}"},
                data={"grant_type": "client_credentials"}
            )
            response.raise_for_status()
            return response.json().get("access_token")
        except requests.RequestException as e:
            logger.error(f"Failed to get Spotify token: {e}")
            pass

    def extract_track_id(self, spotify_url: str) -> Optional[str]:
        try:
            parsed = urlparse(spotify_url.split('?', 1)[0])
            if parsed.netloc not in ['open.spotify.com', 'spotify.com']:
                logger.warning(f"Invalid Spotify URL: {spotify_url}")
                return None
            path_parts = parsed.path.strip('/').split('/')
            if len(path_parts) != 2 or path_parts[0] != 'track':
                logger.warning(f"Invalid Spotify track URL: {spotify_url}")
                return None
            track_id = path_parts[1]
            logger.info(f"Extracted Spotify track ID: {track_id}")
            return track_id
        except Exception as e:
            logger.error(f"Failed to extract track ID from URL {spotify_url}: {e}")
            pass

    def get_track_info(self, spotify_url: str) -> Optional[Tuple[str, str]]:
        """Get track info from Spotify API or web scraping"""
        cached = self.db.get_cached_spotify_track(spotify_url)
        if cached:
            return cached

        track_id = self.extract_track_id(spotify_url)
        if not track_id:
            return None

        if self.spotify_access_token:
            track_info = self._fetch_track_info_api(track_id)
            if track_info:
                self.db.cache_spotify_track(spotify_url, *track_info)
                return track_info

        logger.warning("Falling back to web scraping for Spotify track info")
        track_info = self._fetch_track_info_scrape(spotify_url)
        if track_info:
            self.db.cache_spotify_track(spotify_url, *track_info)
            return track_info

        return None

    def _fetch_track_info_api(self, track_id: str) -> Optional[Tuple[str, str]]:
        try:
            response = requests.get(
                f"https://api.spotify.com/v1/tracks/{track_id}",
                headers={"Authorization": f"Bearer {self.spotify_access_token}"}
            )
            response.raise_for_status()
            track = response.json()
            title = track.get("name", "")
            artist = ", ".join(artist["name"] for artist in track.get("artists", []))
            return title, artist
        except requests.RequestException as e:
            logger.error(f"Failed to get track info from Spotify API: {e}")
            pass

    def _fetch_track_info_scrape(self, spotify_url: str) -> Optional[Tuple[str, str]]:
        try:
            response = requests.get(spotify_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            title_tag = soup.find('meta', property='og:title')
            artist_tag = soup.find('meta', property='og:description')
            title = title_tag.get('content', '').split(' - ')[0].strip() if title_tag else ''
            artist = artist_tag.get('content', '').split(' · ')[0].strip() if artist_tag else ''
            if title and artist:
                return title, artist
        except requests.RequestException as e:
            logger.error(f"Failed to scrape track info from Spotify page: {e}")
        pass

    def get_cached_spotify_track(self, spotify_url: str) -> Optional[Tuple[str, str]]:
        return self.db.get_cached_spotify_track(spotify_url)

    def cache_spotify_track(self, spotify_url: str, title: str, artist: str):
        self.db.cache_spotify_track(spotify_url, title, artist)
