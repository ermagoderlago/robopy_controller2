import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
import os
from dotenv import load_dotenv

def test_devices():
    load_dotenv('/mnt/ssd/robopy_controller_host/.env')
    cache_path = str(Path.home() / '.spotipy_cache')
    auth_manager = SpotifyOAuth(scope='user-modify-playback-state user-read-playback-state', cache_path=cache_path, open_browser=False)
    sp = spotipy.Spotify(auth_manager=auth_manager)
    print("Devices:")
    print(sp.devices())

if __name__ == '__main__':
    test_devices()
