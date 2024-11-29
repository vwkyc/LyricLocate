document.getElementById('lyricsForm').addEventListener('submit', async function(event) {
    event.preventDefault();
    const title = document.getElementById('title').value.trim();
    const artist = document.getElementById('artist').value.trim();
    const lyricsDiv = document.getElementById('lyrics');
    const submitButton = this.querySelector('button[type="submit"]');

    if (!title || !artist) {
        lyricsDiv.textContent = 'Please enter both song title and artist.';
        return;
    }

    // Update UI to loading state
    lyricsDiv.textContent = 'Fetching lyrics...';
    submitButton.disabled = true;

    try {
        const response = await fetch(`/api/get_lyrics?title=${encodeURIComponent(title)}&artist=${encodeURIComponent(artist)}`, {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            },
            timeout: 5000 // 5 second timeout
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        
        if (!data.lyrics) {
            throw new Error('No lyrics found in response');
        }

        lyricsDiv.textContent = data.lyrics;

    } catch (error) {
        if (error instanceof TypeError && error.message.includes('NetworkError')) {
            lyricsDiv.textContent = 'Network error - Please check your internet connection and try again.';
        } else {
            lyricsDiv.textContent = `Error: ${error.message}`;
        }
    } finally {
        submitButton.disabled = false;
    }
});