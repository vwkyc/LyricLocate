# demo a non-english song lyrics request + english translated lyrics request
import requests
endpoint = 'http://localhost:19999/get_lyrics'

params = {
    'title': 'Страсть к курению',
    'artist': 'Buerak'
}

params_en = {
    'title': 'Страсть к курению',
    'artist': 'Buerak',
    'language': 'en'
}

original_response = requests.get(endpoint, params=params)
print("original lyrics:" + original_response.text)

en_response = requests.get(endpoint, params=params_en)
print("english lyrics:" + en_response.text)
