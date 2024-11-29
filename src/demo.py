# demo a non-english song lyrics request + english translated lyrics request
import requests

params = {
    'title': 'Страсть к курению',
    'artist': 'Buerak'
}

params_en = {
    'title': 'Страсть к курению',
    'artist': 'Buerak',
    'language': 'en'
}

original_response = requests.get('http://localhost:19999/get_lyrics', params=params)
print("original lyrics:" + original_response.text)

en_response = requests.get('http://localhost:19999/get_lyrics', params=params_en)
print("english lyrics:" + en_response.text)
