# LyricLocate Lyrics API

This project is a FastAPI-based service that fetches song lyrics from the web. It uses the Genius API and Google search to find and scrape lyrics. The lyrics are cached in a local SQLite database to improve performance and reduce redundant requests.

## Features

- Fetch lyrics for a given song title and artist.
- Cache lyrics in a local SQLite database to reduce redundant requests.
- Use the Genius API and Google search to find lyrics.
- Support for fetching lyrics in different languages, including English translations.

## Installation

1. Clone the repository:

   ```sh
   git clone https://github.com/vwkyc/lyriclocate.git
   cd lyriclocate
   ```

2. Create a virtual environment and activate it:

   ```sh
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. Install the required packages:

   ```sh
   pip install -r requirements.txt
   ```

4. Set the Genius API key as an environment variable:

   ```sh
   export GENIUS_CLIENT_ACCESS_TOKEN=your_genius_api_key  # On Windows use `set GENIUS_CLIENT_ACCESS_TOKEN=your_genius_api_key`
   ```

## Usage

1. Start the FastAPI server:

   ```sh
   cd src && python main.py
   ```

2. Fetch lyrics by making a GET request to the `/api/get_lyrics` endpoint with `title`, `artist`, and optional `language` query parameters:

   ```sh
   curl -X GET "http://localhost:19999/api/get_lyrics?title=Sleepless&artist=deadmau5"
   ```
   OR

   front end [http://localhost:19999](http://localhost:19999)

## API Endpoints

### GET /get_lyrics

Fetch lyrics for a given song title and artist.

- **Query Parameters:**
  - `title` (str): The title of the song.
  - `artist` (str): The artist of the song.
  - `language` (str, optional): The language of the lyrics, default is 'original'. If not specified, it will return the original song lyrics in whatever language they are in. Only 'en' is supported for translated lyrics.

- **Responses:**
  - `200 OK`: Returns the lyrics of the song.
  - `404 Not Found`: Lyrics not found.

## Database

The lyrics are cached in a local SQLite database located at `cache/lyrics.db`. The database schema is as follows:

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
