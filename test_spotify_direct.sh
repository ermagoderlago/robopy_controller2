#!/bin/bash
# test_spotify_direct.sh — Testa spotipy direttamente sul Pi
source /home/robopy/ros2_venv/bin/activate 2>/dev/null
source /mnt/ssd/robopy_controller_host/setup_keys.sh 2>/dev/null

python3 << 'PYEOF'
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
import json

print("=== Init Spotify ===")
try:
    auth = SpotifyOAuth(
        scope="user-modify-playback-state user-read-playback-state",
        cache_path=str(Path.home() / ".spotipy_cache"),
        open_browser=False
    )
    
    cached = auth.get_cached_token()
    if cached:
        print(f"Token in cache: expires_at={cached.get('expires_at')}, scaduto={auth.is_token_expired(cached)}")
    else:
        print("ERRORE: Nessun token in cache!")
    
    sp = spotipy.Spotify(auth_manager=auth)
    
    print("\n=== Devices ===")
    try:
        d = sp.devices()
        print(json.dumps(d, indent=2))
    except Exception as e:
        print(f"ERRORE devices(): {e}")
    
    print("\n=== Current Playback ===")
    try:
        curr = sp.current_playback()
        print(json.dumps(curr, indent=2) if curr else "Nessuna riproduzione attiva")
    except Exception as e:
        print(f"ERRORE current_playback(): {e}")
        
    print("\n=== Test search_play: Metallica ===")
    try:
        results = sp.search(q="Metallica", type='track', limit=1)
        if results and results['tracks']['items']:
            track = results['tracks']['items'][0]
            print(f"Trovato: {track['name']} - {track['artists'][0]['name']} (uri={track['uri']})")
            
            # Prova start_playback senza device_id (usa qualsiasi device attivo)
            print("Avvio playback senza device_id...")
            sp.start_playback(uris=[track['uri']])
            print("SUCCESS!")
        else:
            print("Nessun brano trovato")
    except Exception as e:
        print(f"ERRORE playback: {type(e).__name__}: {e}")

except Exception as e:
    print(f"ERRORE GENERALE: {e}")
PYEOF
