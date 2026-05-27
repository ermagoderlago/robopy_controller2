# =============================================================================
# SKILL: SpotifySkill
# Generata il:        2026-04-29T20:33:31.968567
# Iterazione:         2/3
# Versione prompt:    MARCUS_PROMPT_v2.1
# Hash contesto RAK:  sha256:8c971eff4efd5c83
# Capability:         ['web.search']
# Topic usati:        SUB=[] PUB=[]
# Stato:              ACTIVE
# =============================================================================

from robopy_controller.robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode
from typing import Any, Dict
import asyncio
import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path

logger = logging.getLogger(__name__)

class SpotifySkill(BaseSkill):
    """Skill per controllare Spotify Premium."""

    def __init__(self):
        super().__init__()
        self.sp = None
        self.cache_path = str(Path.home() / ".spotipy_cache")

    def _init_spotify(self):
        if self.sp is None:
            try:
                auth_manager = SpotifyOAuth(
                    scope="user-modify-playback-state user-read-playback-state",
                    cache_path=self.cache_path,
                    open_browser=False
                )
                
                # Se non c'e' il token in cache, l'autenticazione fallisce
                if not auth_manager.get_cached_token():
                    logger.error(f"Token Spotify mancante in {self.cache_path}.")
                    return False
                    
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
                return True
            except Exception as e:
                logger.error(f"Errore inizializzazione Spotify: {e}")
                return False
        return True
    def _get_device_id(self):
        """Ritorna l'ID del device Spotify preferito.
        
        [v11.3] Il device 'raspotify (marcus)' sul Pi NON può suonare
        perché il VUI node tiene il device ALSA aperto in esclusiva.
        ESCLUSO SEMPRE dalla selezione. La musica va su device esterni
        (telefono, PC, speaker smart).
        """
        for attempt in range(2):
            try:
                devices_info = self.sp.devices()
                devices = devices_info.get('devices', [])
                if not devices:
                    logger.warning("[Spotify] Nessun device disponibile nella lista.")
                    return None

                names = [d.get('name', '?') for d in devices]
                logger.info(f"[Spotify] Device disponibili: {names}")

                # Filtra via raspotify/librespot — non possono suonare con VUI attivo
                usable = [d for d in devices
                          if 'raspotify' not in d.get('name', '').lower()
                          and 'librespot' not in d.get('name', '').lower()]

                if not usable:
                    logger.warning("[Spotify] Solo raspotify disponibile — audio non può uscire (VUI lock). Apri Spotify sul telefono!")
                    return None

                # Priorità 1: device esterno attivo
                for d in usable:
                    if d.get('is_active'):
                        logger.info(f"[Spotify] Usando device attivo: {d['name']}")
                        return d['id']

                # Priorità 2: primo device esterno + transfer forzato
                chosen = usable[0]
                logger.info(f"[Spotify] Transfer playback su: {chosen['name']}")
                try:
                    self.sp.transfer_playback(chosen['id'], force_play=False)
                except Exception as te:
                    logger.warning(f"[Spotify] transfer_playback fallito: {te}")
                return chosen['id']

            except Exception as e:
                logger.warning(f"[Spotify] Tentativo {attempt+1}/2 fallito nel recuperare device: {e}")
                if attempt == 0:
                    import time
                    time.sleep(1)
        logger.error("[Spotify] Impossibile recuperare device dopo 2 tentativi.")
        return None




    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="spotify_skill",
            description="Controlla la riproduzione musicale su Spotify Premium (richiede account). Puoi mettere in pausa, riprendere, saltare traccia, controllare il volume, e cercare specifici brani (search_play) o playlist (search_playlist). Se l'utente chiede 'metti un po' di musica' o 'musica in base ai miei gusti' o 'musica nuova', DEVI USARE le preferenze musicali passate dell'utente estratte dal RAG e usare 'search_playlist' con quel genere (es. 'Nuove uscite Rock' o '[Genere] Mix').",
            keywords=["spotify", "musica", "canzone", "brano", "riproduci", "pausa", "avanti", "indietro"],
            priority=8,
            requires_internet=True
        )

    def get_parameters_schema(self) -> Dict[str, Any]:
        """Definisce i parametri per il Function Calling di Gemini."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "L'azione da eseguire su Spotify. DEVE essere una tra: play, pause, next, previous, volume_set, volume_up, volume_down, search_play, search_playlist, what_is_playing, save_track."
                },
                "query": {
                    "type": "string",
                    "description": "Termine di ricerca per brani, artisti o playlist."
                },
                "volume_percent": {
                    "type": "integer",
                    "description": "Valore del volume (0-100) da usare SOLO quando l'azione è 'volume_set'."
                }
            },
            "required": ["action"]
        }

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        text_lower = text.lower()
        if "spotify" in text_lower:
            return 0.8  # Abbassato da 0.95 per evitare il fast-path forzato e lasciare che Gemini processi il context!
        keywords = ["riproduci musica", "metti pausa", "canzone", "brano", "volume musica", "avanti", "indietro", "prossima traccia"]
        if any(k in text_lower for k in keywords):
            return 0.6
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        try:
            # Se context contiene parametri dal Tool Calling e un'action valida, li usiamo
            if context and context.get("action"):
                res = await asyncio.to_thread(self._execute_structured, context)
                
                # [v11.0] Salva le preferenze in memoria musicale se è stata riprodotta nuova musica
                action = context.get("action")
                if res.success and action in ["search_play", "search_playlist"]:
                    if hasattr(self, 'memory_manager') and getattr(self, 'memory_manager', None):
                        try:
                            from datetime import datetime
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            day_str = datetime.now().strftime("%A")
                            query = context.get("query", "musica")
                            
                            user_req = f"L'utente ha chiesto di ascoltare: '{query}' il {day_str} alle {now_str}"
                            robot_act = res.speak if res.speak else f"In riproduzione {query} su Spotify."
                            
                            # Salva background (fire-and-forget)
                            await self.memory_manager.store_background(user_req, robot_act, "preference")
                            logger.info(f"🎵 Preferenza musicale salvata: {query}")
                        except Exception as mem_e:
                            logger.warning(f"Errore salvataggio preferenza Spotify nel RAG: {mem_e}")
                            
                return res
            
            # Altrimenti fallback sul parsing del testo
            return await asyncio.to_thread(self._sync_execute, text)
        except Exception as e:
            return SkillResult.failure_result(f"Errore esecuzione thread Spotify: {str(e)}")

    def _execute_structured(self, args: Dict[str, Any]) -> SkillResult:
        """Esegue la skill usando parametri strutturati da Gemini."""
        if not self._init_spotify():
            return SkillResult.failure_result("Spotify non inizializzato (mancano le credenziali o il token non è valido).")

        action = args.get("action")
        device_id = self._get_device_id()

        # [v11.2] Se non c'è nessun device disponibile, fallisce subito con messaggio chiaro
        if device_id is None and action not in ["what_is_playing", "save_track"]:
            return SkillResult.failure_result(
                "Nessun device Spotify attivo trovato.",
                speak="Non trovo nessun device Spotify attivo. Assicurati che Spotify sia aperto sul telefono o sul Pi, poi riprova."
            )

        try:
            if action == "pause":
                self.sp.pause_playback(device_id=device_id)
                return SkillResult.success_result("Musica in pausa.", speak="Ho messo in pausa la musica su Spotify.")
            elif action == "play":
                self.sp.start_playback(device_id=device_id)
                return SkillResult.success_result("Riproduzione ripresa.", speak="Riprendo a suonare la musica.")
            elif action == "next":
                self.sp.next_track(device_id=device_id)
                return SkillResult.success_result("Passo alla prossima traccia.", speak="Passo alla prossima traccia.")
            elif action == "previous":
                self.sp.previous_track(device_id=device_id)
                return SkillResult.success_result("Torno alla traccia precedente.", speak="Torno alla traccia precedente.")
            
            elif action == "volume_set":
                vol = args.get("volume_percent")
                if vol is not None:
                    vol = max(0, min(100, vol))
                    try:
                        import subprocess
                        subprocess.run(['amixer', 'sset', 'Master', f'{vol}%'], check=True, capture_output=True)
                        return SkillResult.success_result(f"Volume impostato al {vol} percento.", speak=f"Ho impostato il volume al {vol} percento.")
                    except Exception as e:
                        return SkillResult.failure_result(f"Impossibile impostare volume di sistema: {e}")
                return SkillResult.failure_result("Manca la percentuale del volume.")
                
            elif action == "volume_up":
                try:
                    import subprocess
                    # Incrementa il volume di Master del 20%
                    subprocess.run(['amixer', 'sset', 'Master', '20%+'], check=True, capture_output=True)
                    return SkillResult.success_result("Ho alzato il volume.", speak="Ho alzato il volume.")
                except Exception as e:
                    return SkillResult.failure_result(f"Non riesco ad alzare il volume: {e}", speak="Non riesco ad alzare il volume.")
                
            elif action == "volume_down":
                try:
                    import subprocess
                    # Decrementa il volume di Master del 20%
                    subprocess.run(['amixer', 'sset', 'Master', '20%-'], check=True, capture_output=True)
                    return SkillResult.success_result("Ho abbassato il volume.", speak="Ho abbassato il volume.")
                except Exception as e:
                    return SkillResult.failure_result(f"Non riesco ad abbassare il volume: {e}", speak="Non riesco ad abbassare il volume.")
                
            elif action == "what_is_playing":
                curr = self.sp.current_playback()
                if curr and curr.get('is_playing') and curr.get('item'):
                    track = curr['item']['name']
                    artist = curr['item']['artists'][0]['name']
                    return SkillResult.success_result(f"Sto suonando {track} di {artist}.", speak=f"In questo momento sto suonando {track} di {artist}.")
                return SkillResult.success_result("Al momento non c'è nessuna musica in riproduzione.", speak="Al momento non c'è nessuna musica in riproduzione.")
                
            elif action == "save_track":
                curr = self.sp.current_playback()
                if curr and curr.get('item'):
                    track_id = curr['item']['id']
                    track_name = curr['item']['name']
                    self.sp.current_user_saved_tracks_add([track_id])
                    return SkillResult.success_result(f"Ho aggiunto {track_name} ai tuoi brani preferiti.", speak=f"Ho aggiunto {track_name} ai tuoi brani preferiti su Spotify.")
                return SkillResult.failure_result("Nessun brano in riproduzione da salvare.", speak="Non c'è nessun brano in riproduzione da poter salvare.")

            elif action == "search_play":
                query = args.get("query")
                if query:
                    results = self.sp.search(q=query, type='track', limit=1)
                    if results and results['tracks']['items']:
                        track_uri = results['tracks']['items'][0]['uri']
                        self.sp.start_playback(device_id=device_id, uris=[track_uri])
                        track_name = results['tracks']['items'][0]['name']
                        artist_name = results['tracks']['items'][0]['artists'][0]['name']
                        return SkillResult.success_result(f"Ho trovato e riprodotto {track_name} di {artist_name}.", speak=f"Ho trovato e riprodotto {track_name} di {artist_name}.")
                    return SkillResult.failure_result(f"Non ho trovato nessun brano corrispondente a {query}.", speak=f"Non sono riuscito a trovare {query} su Spotify.")
                return SkillResult.failure_result("Non hai specificato cosa cercare.", speak="Non hai specificato cosa cercare.")
                
            elif action == "search_playlist":
                query = args.get("query")
                if query:
                    results = self.sp.search(q=query, type='playlist', limit=1)
                    if results and results['playlists']['items']:
                        playlist_uri = results['playlists']['items'][0]['uri']
                        self.sp.start_playback(device_id=device_id, context_uri=playlist_uri)
                        playlist_name = results['playlists']['items'][0]['name']
                        return SkillResult.success_result(f"Ho avviato la playlist {playlist_name}.", speak=f"Ho avviato la playlist {playlist_name}.")
                    return SkillResult.failure_result(f"Non ho trovato nessuna playlist chiamata {query}.", speak=f"Non ho trovato nessuna playlist chiamata {query}.")
                return SkillResult.failure_result("Non hai specificato il nome della playlist.", speak="Non hai specificato il nome della playlist.")
            
            return SkillResult.failure_result(f"Azione {action} non riconosciuta o parametri errati.", speak="Non ho capito bene quale azione musicale vuoi eseguire.")
        except spotipy.SpotifyException as e:
            return SkillResult.failure_result(f"Errore API Spotify: {e.msg}", speak="C'è stato un problema con l'API di Spotify.")
        except Exception as e:
            logger.error(f"[Spotify] Errore imprevisto in _execute_structured: {e}")
            return SkillResult.failure_result(f"Errore imprevisto Spotify: {str(e)}", speak="C'è stato un problema imprevisto con Spotify.")

    def _sync_execute(self, text: str) -> SkillResult:
        # Mantengo il vecchio metodo per compatibilità con il matching testuale diretto
        if not self._init_spotify():
            return SkillResult.failure_result("Non riesco ad accedere a Spotify. Assicurati di aver configurato il token e le credenziali (.env) con lo script spotify_auth.py.")

        text_lower = text.lower()

        try:
            device_id = self._get_device_id()
            if "pausa" in text_lower or "ferma" in text_lower:
                self.sp.pause_playback(device_id=device_id)
                return SkillResult.success_result("Musica in pausa.")
            
            elif "riproduci" in text_lower and "su spotify" not in text_lower and "cerca" not in text_lower:
                self.sp.start_playback(device_id=device_id)
                return SkillResult.success_result("Riproduzione ripresa.")
                
            elif "avanti" in text_lower or "prossima" in text_lower:
                self.sp.next_track(device_id=device_id)
                return SkillResult.success_result("Passo alla prossima traccia.")
                
            elif "indietro" in text_lower or "precedente" in text_lower:
                self.sp.previous_track(device_id=device_id)
                return SkillResult.success_result("Torno alla traccia precedente.")
                
            elif "volume" in text_lower:
                import re
                import subprocess
                match = re.search(r'al (\d+)', text_lower)
                if match:
                    vol = int(match.group(1))
                    vol = max(0, min(100, vol))
                    subprocess.run(['amixer', 'sset', 'Master', f'{vol}%'], check=True, capture_output=True)
                    return SkillResult.success_result(f"Volume musica impostato al {vol} percento.")
                
                if "alza" in text_lower or "aumenta" in text_lower:
                    subprocess.run(['amixer', 'sset', 'Master', '20%+'], check=True, capture_output=True)
                    return SkillResult.success_result("Volume musica alzato.")
                elif "abbassa" in text_lower or "diminuisci" in text_lower:
                    subprocess.run(['amixer', 'sset', 'Master', '20%-'], check=True, capture_output=True)
                    return SkillResult.success_result("Volume musica abbassato.")
                
                return SkillResult.success_result("Comando volume non specifico.")
            
            elif "cerca" in text_lower or "riproduci" in text_lower or "suona" in text_lower:
                query = text_lower.replace("cerca", "").replace("su spotify", "").replace("riproduci", "").replace("suona", "").strip()
                if query:
                    results = self.sp.search(q=query, type='track', limit=1)
                    if results and results['tracks']['items']:
                        track_uri = results['tracks']['items'][0]['uri']
                        self.sp.start_playback(device_id=device_id, uris=[track_uri])
                        track_name = results['tracks']['items'][0]['name']
                        return SkillResult.success_result(f"Sto riproducendo {track_name} su Spotify.")
                    else:
                        return SkillResult.failure_result(f"Non ho trovato canzoni corrispondenti a {query}.")
            
            return SkillResult.success_result("Comando Spotify compreso ma nessuna azione intrapresa.")
        except spotipy.SpotifyException as e:
            return SkillResult.failure_result(f"Errore API Spotify: controlla che Spotify sia aperto su un dispositivo. Dettagli: {e.msg}")
        except Exception as e:
            return SkillResult.failure_result(f"Errore inatteso: {str(e)}")