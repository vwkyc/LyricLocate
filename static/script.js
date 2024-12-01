document.addEventListener('DOMContentLoaded', function() {
  const lyricsForm = document.getElementById('lyricsForm');
  const searchMethodRadios = document.getElementsByName('searchMethod');
  const manualInputs = document.querySelectorAll('.manual-input');
  const spotifyInput = document.querySelector('.spotify-input');
  const lyricsDiv = document.getElementById('lyrics');
  const submitButton = lyricsForm.querySelector('button[type="submit"]');
  const spotifyUrlInput = document.getElementById('spotifyUrl');
  const languageSelect = document.getElementById('language');

  function isValidSpotifyUrl(url) {
      return url.match(/^(https:\/\/open\.spotify\.com\/track\/|spotify:track:)[a-zA-Z0-9]+/);
  }

  searchMethodRadios.forEach(radio => {
      radio.addEventListener('change', function() {
          if (this.value === 'manual') {
              manualInputs.forEach(input => {
                  input.style.display = 'block';
                  input.querySelector('input').required = true;
              });
              spotifyInput.style.display = 'none';
              spotifyUrlInput.required = false;
          } else {
              manualInputs.forEach(input => {
                  input.style.display = 'none';
                  input.querySelector('input').required = false;
              });
              spotifyInput.style.display = 'block';
              spotifyUrlInput.required = true;
          }
          lyricsDiv.textContent = '';
          lyricsDiv.style.display = 'none'; // Hide lyrics div
      });
  });

  lyricsForm.addEventListener('submit', async function(event) {
      event.preventDefault();
      console.log('Form submitted'); // Debug log

      const selectedMethod = document.querySelector('input[name="searchMethod"]:checked').value;
      console.log('Selected method:', selectedMethod); // Debug log

      lyricsDiv.textContent = 'Fetching lyrics...';
      lyricsDiv.style.display = 'block'; // Show lyrics div
      submitButton.disabled = true;

      try {
          let url;
          const language = languageSelect.value;
          if (selectedMethod === 'spotify') {
              const spotifyUrl = spotifyUrlInput.value.trim();
              console.log('Spotify URL:', spotifyUrl); // Debug log

              if (!spotifyUrl || !isValidSpotifyUrl(spotifyUrl)) {
                  throw new Error('Please enter a valid Spotify track URL');
              }

              url = `/api/get_lyrics_from_spotify?spotify_url=${encodeURIComponent(spotifyUrl)}&language=${encodeURIComponent(language)}`;
          } else {
              const title = document.getElementById('title').value.trim();
              const artist = document.getElementById('artist').value.trim();

              if (!title || !artist) {
                  throw new Error('Please enter both song title and artist');
              }

              url = `/api/get_lyrics?title=${encodeURIComponent(title)}&artist=${encodeURIComponent(artist)}&language=${encodeURIComponent(language)}`;
          }

          console.log('Fetching URL:', url); // Debug log
          const response = await fetch(url);
          console.log('Response status:', response.status); // Debug log

          if (!response.ok) {
              throw new Error(response.status === 404 ? 'Lyrics not found' : 'Failed to fetch lyrics');
          }

          const data = await response.json();
          lyricsDiv.textContent = data.lyrics || 'No lyrics found';

      } catch (error) {
          console.error('Error:', error); // Debug log
          lyricsDiv.textContent = `Error: ${error.message}`;
      } finally {
          submitButton.disabled = false;
      }
  });
});

document.querySelectorAll('.search-toggle').forEach(toggle => {
  toggle.addEventListener('click', function() {
    document.querySelectorAll('.search-toggle').forEach(t => t.classList.remove('active'));
    this.classList.add('active');
  });
});