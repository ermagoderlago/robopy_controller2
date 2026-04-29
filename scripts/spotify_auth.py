import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# Carica credenziali dal file .env se presente
load_dotenv()

def setup_spotify():
    print("=== Configurazione Spotify Premium per Marcus ===")
    print("Assicurati di aver creato un'app su https://developer.spotify.com/dashboard")
    print("Imposta il Redirect URI nelle impostazioni dell'app (es. http://localhost:8888/callback)")
    print()
    
    client_id = os.environ.get("SPOTIPY_CLIENT_ID") or input("Inserisci SPOTIPY_CLIENT_ID: ")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET") or input("Inserisci SPOTIPY_CLIENT_SECRET: ")
    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI") or input("Inserisci SPOTIPY_REDIRECT_URI (es. https://my.home-assistant.io/redirect/oauth): ")
    
    os.environ["SPOTIPY_CLIENT_ID"] = client_id
    os.environ["SPOTIPY_CLIENT_SECRET"] = client_secret
    os.environ["SPOTIPY_REDIRECT_URI"] = redirect_uri
    
    cache_path = os.path.expanduser("~/.spotipy_cache")
    
    auth_manager = SpotifyOAuth(
        scope="user-modify-playback-state user-read-playback-state",
        cache_path=cache_path,
        open_browser=False
    )
    
    url = auth_manager.get_authorize_url()
    print(f"\n1. Vai a questo URL nel tuo browser:\n{url}")
    print("\n2. Accedi e autorizza l'app.")
    print("3. Verrai reindirizzato a un URL (potrebbe darti errore, è normale se non hai un web server locale in ascolto).")
    
    response_url = input("\nIncolla l'intero URL a cui sei stato reindirizzato: ")
    code = auth_manager.parse_response_code(response_url)
    auth_manager.get_access_token(code)
    
    print(f"\nAutenticazione completata! Il token è stato salvato in: {cache_path}")
    print("Assicurati di copiare questo file sul Raspberry Pi se hai eseguito questo script dal PC.")
    print("Sul Raspberry Pi, il file deve trovarsi in ~/.spotipy_cache (nella home dell'utente robopy).")
    print("\nInoltre, ricordati di aggiungere SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET e SPOTIPY_REDIRECT_URI nel file .env del Raspberry Pi.")

if __name__ == "__main__":
    setup_spotify()
