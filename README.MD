# LyricLocate Lyrics API

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.68.0+-green.svg)](https://fastapi.tiangolo.com)

This project is a FastAPI-based service that fetches song lyrics from the web. It combines multiple sources including Genius API, Spotify, Musixmatch, and Google search to find and scrape lyrics. The lyrics are cached in a local SQLite database to improve performance and reduce redundant requests.

## Features

- Fetch lyrics for a given song title and artist
- Fetch lyrics directly from Spotify track URLs
- Works with or without Genius API key (falls back to web scraping)
- Smart caching system using SQLite database
- Multiple lyrics sources (Genius API, Spotify, Musixmatch, Google Search)
- Support for different languages, including English translations
- Automatic alternate language lyrics detection and caching
- Modern web frontend

## Installation

1. Clone the repository:
   ```sh
   git clone https://github.com/vwkyc/lyriclocate.git
   cd lyriclocate
   ```

2. Create a virtual environment and activate it:
   ```sh
   python -m venv venv
   venv\Scripts\activate  # On Windows
   source venv/bin/activate  # On Unix/MacOS
   ```

3. Install the required packages:
   ```sh
   pip install -r requirements.txt
   ```

4. Set up environment variables:
   Create a `.env` file in the project root:
   ```env
   GENIUS_CLIENT_ACCESS_TOKEN=your_genius_api_key
   SPOTIFY_CLIENT_ID=your_spotify_client_id
   SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
   ```
   Note: The service can work without API keys by using web scraping methods.

## Usage

1. Start the server:
   ```sh
   cd src && python main.py
   ```

2. Access the service:
   - Web Interface: [http://localhost:19999](http://localhost:19999)
   - API Endpoints: 
     ```sh
     # Using title and artist
     curl -X GET "http://localhost:19999/api/get_lyrics?title=Sleepless&artist=deadmau5"
     
     # Using Spotify URL
     curl -X GET "http://localhost:19999/api/get_lyrics_from_spotify?spotify_url=https://open.spotify.com/track/781KGu6ckiXdOYmgkzRJ42"
     ```

## API Endpoints

### GET /api/get_lyrics
Fetch lyrics using song title and artist.

**Query Parameters:**
- `title` (required): Song title
- `artist` (required): Artist name
- `language` (optional): Target language code (e.g., 'en' for English)

### GET /api/get_lyrics_from_spotify
Fetch lyrics using a Spotify track URL.

**Query Parameters:**
- `spotify_url` (required): Full Spotify track URL
- `language` (optional): Target language code (e.g., 'en' for English)

**Responses:**
- `200 OK`: Returns lyrics JSON object
- `404 Not Found`: Lyrics not found

## Database Structure

The SQLite database (`lyriclocate/cache/lyrics.db`) uses the following schemas:

```sql
CREATE TABLE IF NOT EXISTS lyrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT UNIQUE,
    title TEXT,
    artist TEXT,
    language TEXT,
    lyrics TEXT,
    timestamp DATETIME,
    UNIQUE(title, artist, language)
);

CREATE TABLE IF NOT EXISTS spotify_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_url TEXT UNIQUE,
    title TEXT,
    artist TEXT,
    timestamp DATETIME
);
```

## Environment Variables

- `GENIUS_CLIENT_ACCESS_TOKEN`: (Optional) Your Genius API key
- `SPOTIFY_CLIENT_ID`: (Optional) Your Spotify API client ID
- `SPOTIFY_CLIENT_SECRET`: (Optional) Your Spotify API client secret

The service will use web scraping as a fallback if API keys are not provided.

## Development

Run in development mode with auto-reload:
```sh
uvicorn main:app --reload --host 0.0.0.0 --port 19999
```
