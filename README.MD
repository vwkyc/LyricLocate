# LyricLocate Lyrics API

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.68.0+-green.svg)](https://fastapi.tiangolo.com)

This project is a FastAPI-based service that fetches song lyrics from the web. It combines multiple sources including Genius API and Google search to find and scrape lyrics. The lyrics are cached in a local SQLite database to improve performance and reduce redundant requests.

## Features

- Fetch lyrics for a given song title and artist
- Works with or without Genius API key (falls back to web scraping if no API key is provided)
- Smart caching system using SQLite database
- Multiple lyrics sources (Genius API, Google Search)
- Support for different languages, including English translations
- Automatic alternate language lyrics detection and caching
- Clean and simple web interface

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

4. (Optional) Set up the Genius API key:
   ```sh
   set GENIUS_CLIENT_ACCESS_TOKEN=your_genius_api_key  # On Windows
   export GENIUS_CLIENT_ACCESS_TOKEN=your_genius_api_key  # On Unix/MacOS
   ```
   Note: The service will work without an API key by using web scraping methods.

## Usage

1. Start the server:
   ```sh
   cd src && python main.py
   ```

2. Access the service:
   - Web Interface: [http://localhost:19999](http://localhost:19999)
   - API Endpoint: 
     ```sh
     curl -X GET "http://localhost:19999/api/get_lyrics?title=Sleepless&artist=deadmau5"
     ```

## API Endpoints

### GET /api/get_lyrics

Fetch lyrics for a given song.

**Query Parameters:**
- `title` (required): Song title
- `artist` (required): Artist name
- `language` (optional): Target language code (e.g., 'en' for English)

**Responses:**
- `200 OK`: Returns lyrics JSON object
- `404 Not Found`: Lyrics not found

## Database Structure

The SQLite database (`lyriclocate/cache/lyrics.db`) uses the following schema:

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
```

## Environment Variables

- `GENIUS_CLIENT_ACCESS_TOKEN`: (Optional) Your Genius API key. The service will use web scraping as a fallback if no API key is provided.

## Logging

The application uses Python's logging module with:
- Detailed operation logs
- Cache hit/miss tracking
- API request monitoring
- Error tracking

## Development

Run in development mode with auto-reload:
```sh
uvicorn main:app --reload --host 0.0.0.0 --port 19999
```
