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
    
    devices_info = sp.devices()
    devices = devices_info.get('devices', [])
    print(f"Total devices: {len(devices)}")
    for d in devices:
        print(f"- {d['name']} (active: {d['is_active']}) ID: {d['id']}")
        
    # Test our logic
    device_id = None
    for d in devices:
        name = d.get('name', '').lower()
        if 'raspotify' in name or 'marcus' in name or 'respeaker' in name or 'librespot' in name:
            device_id = d['id']
            print(f"MATCHED RASPBERRY: {device_id}")
            break
            
    if not device_id:
        for d in devices:
            if d.get('is_active'):
                device_id = d['id']
                print(f"MATCHED ACTIVE: {device_id}")
                break

if __name__ == '__main__':
    test_devices()
