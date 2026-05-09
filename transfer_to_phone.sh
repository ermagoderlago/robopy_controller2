#!/bin/bash
source /home/robopy/ros2_venv/bin/activate
source /mnt/ssd/robopy_controller_host/setup_keys.sh
python3 -c "
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
auth = SpotifyOAuth(scope='user-modify-playback-state user-read-playback-state', cache_path=str(Path.home() / '.spotipy_cache'), open_browser=False)
sp = spotipy.Spotify(auth_manager=auth)
try:
    sp.pause_playback()
    print('Paused')
except:
    print('Already paused')
import time; time.sleep(2)
devices = sp.devices()
for d in devices.get('devices', []):
    n = d['name']
    a = d.get('is_active', False)
    print(f'  {n}: active={a}')
    if 'luca' in n.lower():
        sp.transfer_playback(d['id'], force_play=False)
        print(f'Transferred to {n}')
" 2>&1
