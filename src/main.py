import os
import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
from typing import Optional
from lyric_locator import LyricLocate

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

app = FastAPI()
lyric_locator = LyricLocate()

class LyricsResponse(BaseModel):
    title: str
    artist: str
    language: Optional[str]
    lyrics: str

STATIC_DIR = "../static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
        # Check cache first
        cached = lyric_locator.get_cached_data(title, artist, sanitized_language)
        if cached:
            if sanitized_language == 'en' and not lyric_locator.is_lyrics_in_english(cached):
                # Delete invalid English lyrics from cache
                lyric_locator.db.delete_cached_lyrics(title, artist, 'en')
                
                # Try to fetch new lyrics immediately
                lyrics = lyric_locator.get_lyrics(title, artist, sanitized_language, should_cache=True)
                if lyrics != "Lyrics not found" and lyric_locator.is_lyrics_in_english(lyrics):
                    return LyricsResponse(title=title, artist=artist, language=sanitized_language, lyrics=lyrics)
                
                if background_tasks:
                    background_tasks.add_task(lyric_locator.fetch_english_lyrics, title, artist)
                    background_tasks.add_task(lyric_locator.fetch_original_lyrics, title, artist)
                return JSONResponse(
                    status_code=404,
                    content={"detail": "English lyrics not found"}
                )
            
            if background_tasks:
                if sanitized_language == 'original' and not lyric_locator.get_cached_data(title, artist, 'en'):
                    background_tasks.add_task(lyric_locator.search_fetch_and_cache_alternate, title, artist, sanitized_language)
                elif sanitized_language == 'en' and not lyric_locator.get_cached_data(title, artist, 'original'):
                    background_tasks.add_task(lyric_locator.fetch_original_lyrics, title, artist)
                    
            return LyricsResponse(title=title, artist=artist, language=sanitized_language, lyrics=cached)
        
        # If not in cache, fetch new lyrics
        lyrics = lyric_locator.get_lyrics(title, artist, sanitized_language, should_cache=False)
        if lyrics != "Lyrics not found":
            if sanitized_language == 'en':
                if not lyric_locator.is_lyrics_in_english(lyrics):
                    if background_tasks:
                        background_tasks.add_task(lyric_locator.fetch_english_lyrics, title, artist)
                        background_tasks.add_task(lyric_locator.fetch_original_lyrics, title, artist)
                    return JSONResponse(
                        status_code=404,
                        content={"detail": "English lyrics not found"}
                    )
                
                lyric_locator.save_to_cache(title, artist, lyrics, sanitized_language)
                
                if background_tasks and not lyric_locator.get_cached_data(title, artist, 'original'):
                    background_tasks.add_task(lyric_locator.fetch_original_lyrics, title, artist)
            else:
                lyric_locator.save_to_cache(title, artist, lyrics, sanitized_language)
                
                if lyric_locator.is_lyrics_in_english(lyrics):
                    lyric_locator.save_to_cache(title, artist, lyrics, 'en')
                    logger.info("Original lyrics are in English. Cached as 'en' as well.")
                elif background_tasks and not lyric_locator.get_cached_data(title, artist, 'en'):
                    background_tasks.add_task(lyric_locator.search_fetch_and_cache_alternate, title, artist, sanitized_language)
            
            return LyricsResponse(title=title, artist=artist, language=sanitized_language, lyrics=lyrics)
        
        # Schedule background tasks and return 404
        if background_tasks:
            if sanitized_language == 'en':
                background_tasks.add_task(lyric_locator.fetch_english_lyrics, title, artist)
                background_tasks.add_task(lyric_locator.fetch_original_lyrics, title, artist)
            else:
                background_tasks.add_task(lyric_locator.fetch_original_lyrics, title, artist)
                background_tasks.add_task(lyric_locator.search_fetch_and_cache_alternate, title, artist, sanitized_language)
        
        return JSONResponse(
            status_code=404,
            content={"detail": "Lyrics not found"}
        )
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"}
        )

if __name__ == "__main__":
    logger.info("Starting Uvicorn server.")
    uvicorn.run(app, host="0.0.0.0", port=19999)
