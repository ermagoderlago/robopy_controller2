#!/bin/bash
# test_play_phone.sh — Testa Spotify forzando il device NON-raspotify (telefono)
source /home/robopy/ros2_venv/bin/activate 2>/dev/null
source /mnt/ssd/robopy_controller_host/setup_keys.sh 2>/dev/null

python3 << 'PYEOF'
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
import json

auth = SpotifyOAuth(
    scope="user-modify-playback-state user-read-playback-state",
    cache_path=str(Path.home() / ".spotipy_cache"),
    open_browser=False
)
sp = spotipy.Spotify(auth_manager=auth)

devices = sp.devices()
print(f"Devices: {[d['name'] for d in devices.get('devices', [])]}")

# Nuova logica: preferisci NON-raspotify
device_id = None
for d in devices.get('devices', []):
    if d.get('is_active'):
        device_id = d['id']
        print(f"Usando device ATTIVO: {d['name']}")
        break

if not device_id:
    for d in devices.get('devices', []):
        name = d.get('name', '').lower()
        if 'raspotify' not in name and 'librespot' not in name:
            device_id = d['id']
            print(f"Usando device ESTERNO: {d['name']}")
            break

if not device_id and devices.get('devices'):
    d = devices['devices'][0]
    device_id = d['id']
    print(f"FALLBACK su: {d['name']}")

if not device_id:
    print("NESSUN DEVICE DISPONIBILE!")
    exit(1)

results = sp.search(q="Metallica", type='track', limit=1)
track = results['tracks']['items'][0]
print(f"Track: {track['name']} - {track['artists'][0]['name']}")
print(f"Avvio playback su device_id={device_id}...")

try:
    sp.start_playback(device_id=device_id, uris=[track['uri']])
    print("start_playback OK!")
except Exception as e:
    print(f"ERRORE: {e}")
    # Se il device non è attivo, prova transfer_playback prima
    print("Tentativo con transfer_playback...")
    try:
        sp.transfer_playback(device_id=device_id, force_play=True)
        import time; time.sleep(1)
        sp.start_playback(device_id=device_id, uris=[track['uri']])
        print("SUCCESS dopo transfer!")
    except Exception as e2:
        print(f"ERRORE FINALE: {e2}")

import time; time.sleep(3)
curr = sp.current_playback()
if curr and curr.get('is_playing'):
    print(f"IN RIPRODUZIONE: {curr['item']['name']} su {curr['device']['name']} ✅")
else:
    print("NON in riproduzione ❌")
PYEOF
