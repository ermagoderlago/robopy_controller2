import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
import os
from dotenv import load_dotenv

def test_volume():
    load_dotenv('/mnt/ssd/robopy_controller_host/.env')
    cache_path = str(Path.home() / '.spotipy_cache')
    auth_manager = SpotifyOAuth(scope='user-modify-playback-state user-read-playback-state', cache_path=cache_path, open_browser=False)
    sp = spotipy.Spotify(auth_manager=auth_manager)
    
    devices = sp.devices().get('devices', [])
    device_id = None
    for d in devices:
        if 'raspotify' in d['name'].lower() or 'marcus' in d['name'].lower():
            device_id = d['id']
            break
            
    if device_id:
        print(f"Setting volume on {device_id}...")
        try:
            sp.volume(20, device_id=device_id)
            print("Volume set to 20%!")
        except Exception as e:
            print(f"Failed: {e}")
    else:
        print("Device not found")

if __name__ == '__main__':
    test_volume()
