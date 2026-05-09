#!/bin/bash
# check_spotify.sh — Diagnostica token Spotify e dispositivi attivi
echo "=== TOKEN CACHE ==="
CACHE="$HOME/.spotipy_cache"
if [ -f "$CACHE" ]; then
    python3 -c "
import json, datetime
with open('$CACHE') as f:
    d = json.load(f)
exp = d.get('expires_at', 0)
exp_dt = datetime.datetime.fromtimestamp(exp)
now = datetime.datetime.now()
print('Token expires_at:', exp_dt)
print('Scaduto:', exp_dt < now)
print('Scope:', d.get('scope','N/A'))
print('Has refresh_token:', bool(d.get('refresh_token')))
"
else
    echo "Cache NON trovata in $CACHE"
fi

echo ""
echo "=== RASPOTIFY STATUS ==="
systemctl status raspotify --no-pager -l 2>/dev/null | head -20 || echo "raspotify non trovato come servizio systemd"

echo ""
echo "=== SPOTIFY DEVICES (via spotipy) ==="
source /home/robopy/ros2_venv/bin/activate 2>/dev/null
source /mnt/ssd/robopy_controller_host/setup_keys.sh 2>/dev/null
python3 -c "
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
try:
    auth = SpotifyOAuth(
        scope='user-modify-playback-state user-read-playback-state',
        cache_path=str(Path.home() / '.spotipy_cache'),
        open_browser=False
    )
    sp = spotipy.Spotify(auth_manager=auth)
    devices = sp.devices()
    print('Devices:', devices)
    curr = sp.current_playback()
    print('Current playback:', curr)
except Exception as e:
    print('ERRORE:', e)
" 2>&1
