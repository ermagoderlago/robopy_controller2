#!/bin/bash
# test_play_and_audio.sh — Testa se Spotify avvia la riproduzione e se il sink PipeWire si attiva
source /home/robopy/ros2_venv/bin/activate 2>/dev/null
source /mnt/ssd/robopy_controller_host/setup_keys.sh 2>/dev/null

echo "=== PRIMA: Stato Sink ==="
pactl list sinks short 2>/dev/null

echo ""
echo "=== Avvio playback Metallica ==="
python3 << 'PYEOF'
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path

auth = SpotifyOAuth(
    scope="user-modify-playback-state user-read-playback-state",
    cache_path=str(Path.home() / ".spotipy_cache"),
    open_browser=False
)
sp = spotipy.Spotify(auth_manager=auth)

# Trova device
devices = sp.devices()
print(f"Devices: {[d['name'] for d in devices.get('devices', [])]}")

# Cerca Metallica
results = sp.search(q="Metallica", type='track', limit=1)
track = results['tracks']['items'][0]
print(f"Track: {track['name']} - {track['artists'][0]['name']}")

# Avvia
for d in devices.get('devices', []):
    if 'raspotify' in d['name'].lower() or 'marcus' in d['name'].lower():
        device_id = d['id']
        print(f"Usando device: {d['name']} (id={device_id})")
        sp.start_playback(device_id=device_id, uris=[track['uri']])
        print("start_playback OK!")
        break
else:
    print("Device Pi NON trovato! Uso il primo...")
    if devices['devices']:
        d = devices['devices'][0]
        sp.start_playback(device_id=d['id'], uris=[track['uri']])
        print(f"start_playback su {d['name']} OK!")

import time
time.sleep(3)

curr = sp.current_playback()
if curr and curr.get('is_playing'):
    print(f"IN RIPRODUZIONE: {curr['item']['name']} su {curr['device']['name']}")
    print(f"Volume: {curr['device']['volume_percent']}%")
else:
    print("NON in riproduzione!")
    if curr:
        print(f"Device: {curr.get('device', {}).get('name')}, is_playing: {curr.get('is_playing')}")
PYEOF

echo ""
echo "=== DOPO: Stato Sink ==="
pactl list sinks short 2>/dev/null

echo ""
echo "=== Volume ALSA ==="
amixer sget Master 2>/dev/null | grep -E '%|Mono'
