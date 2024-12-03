import os
import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import uvicorn
from lyric_locator import LyricLocate
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI()
lyric_locator = LyricLocate()
STATIC_DIR = "../static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class LyricsResponse(BaseModel):
    title: str
    artist: str
    language: Optional[str]
    lyrics: str

def validate_language(language: Optional[str]) -> str:
    if language:
        language = language.strip().lower()
        if language in ["en", "original"]:
            return language
        raise HTTPException(status_code=400, detail="Invalid language parameter. Only 'en' and 'original' are accepted.")
    return "original"

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/api/get_lyrics_from_spotify", response_model=LyricsResponse)
def get_lyrics_from_spotify_endpoint(
    spotify_url: str,
    language: Optional[str] = Query(None, pattern="^(en|original)$", description="Language code, 'en' or 'original' are accepted."),
    background_tasks: BackgroundTasks = None
):
    logger.info(f"API request received for Spotify URL: '{spotify_url}' with language: '{language}'")
    sanitized_language = validate_language(language)

    track_info = lyric_locator.spotify_handler.get_track_info(spotify_url)
    if not track_info:
        raise HTTPException(status_code=400, detail="Could not extract track information from Spotify URL")

    title, artist = track_info
    return get_lyrics_endpoint(title=title, artist=artist, language=sanitized_language, background_tasks=background_tasks)

@app.get("/api/get_lyrics", response_model=LyricsResponse)
def get_lyrics_endpoint(
    title: str,
    artist: str,
    language: Optional[str] = Query(
        None,
        pattern="^(en|original)$",
        description="Language code, 'en' or 'original' are accepted."
    ),
    background_tasks: BackgroundTasks = None
):
    logger.info(f"API request received for Title: '{title}', Artist: '{artist}', Language: '{language}'")
    sanitized_language = validate_language(language)

    try:
        lyrics = lyric_locator.get_lyrics(title, artist, sanitized_language, should_cache=True)
        if lyrics != "Lyrics not found":
            if sanitized_language == 'en' and not lyric_locator.is_lyrics_in_english(lyrics):
                if background_tasks:
                    background_tasks.add_task(lyric_locator.fetch_lyrics_background, title, artist, 'en')
                return JSONResponse(status_code=404, content={"detail": "English lyrics not found"})
            if sanitized_language == 'original':
                if not lyric_locator.is_lyrics_in_english(lyrics):
                    if background_tasks:
                        background_tasks.add_task(lyric_locator.fetch_lyrics_background, title, artist, 'en')
                    return LyricsResponse(title=title, artist=artist, language=sanitized_language, lyrics=lyrics)
                return LyricsResponse(title=title, artist=artist, language=sanitized_language, lyrics=lyrics)
            return LyricsResponse(title=title, artist=artist, language=sanitized_language, lyrics=lyrics)

        if background_tasks and sanitized_language == 'en':
            background_tasks.add_task(lyric_locator.fetch_lyrics_background, title, artist, sanitized_language)
        return JSONResponse(status_code=404, content={"detail": "Lyrics not found"})
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

if __name__ == "__main__":
    logger.info("Starting Uvicorn server.")
    uvicorn.run(app, host="0.0.0.0", port=19999)
