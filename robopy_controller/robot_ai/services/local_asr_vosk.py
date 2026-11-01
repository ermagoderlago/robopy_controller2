import os
import sys
import json
import queue
import threading
import datetime
import time
import gc
import numpy as np

# Garantisce l'accesso a vosk anche se eseguito da /usr/bin/python3
for venv_site in [
    "/home/robopy/ros2_venv/lib/python3.11/site-packages",
    "/home/robopy/ros2_venv/lib/python3.12/site-packages"
]:
    if os.path.exists(venv_site) and venv_site not in sys.path:
        sys.path.append(venv_site)

try:
    import vosk
    HAS_VOSK = True
except ImportError:
    HAS_VOSK = False


class VoskASRManager:
    """
    Gestore per il riconoscimento vocale offline (Vosk).
    Lavora in un thread separato tramite coda thread-safe per non bloccare
    il thread audio ad alta priorità di PyAudio.
    """
    def __init__(self, model_path="/mnt/ssd/robopy_controller_host/models/vosk-model-it-0.22", sample_rate=16000, on_text_cb=None):
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.on_text_cb = on_text_cb
        
        self._audio_queue = queue.Queue(maxsize=300)
        self._shutdown = False
        self._worker_thread = None
        self._drop_count = 0  # [v19.6] Contatore frame scartati per debug drop sotto carico CPU

        # [F3 - VAD Energy Gating per Zero-Idle CPU Overload con sensibilità far-field SPEC-04]
        self.energy_gating = True
        self._noise_floor_ema = 60.0
        self._hangover_chunks = 0
        self._max_hangover_chunks = 15  # ~300-450ms di margine post-voce
        self._min_rms_threshold = 80.0
        self._last_was_speech = False
        
        self.model = None
        self.recognizer = None
        
        if not HAS_VOSK:
            print("⚠️ [VoskASR] Libreria vosk non installata. Installa con 'pip install vosk'.")
            return
            
        if not (os.path.exists(self.model_path) and os.path.isdir(self.model_path)):
            base_dir = os.path.dirname(self.model_path)
            candidates = [
                os.path.join(base_dir, "vosk-model-small-it-0.22"),
                os.path.join(base_dir, "vosk-model-it-0.22")
            ]
            found = False
            for cand in candidates:
                if os.path.exists(cand) and os.path.isdir(cand):
                    print(f"💡 [VoskASR] Trovato modello locale in {cand}")
                    self.model_path = cand
                    found = True
                    break
            
            if not found:
                print(f"⚠️ [VoskASR] Nessun modello valido trovato in {self.model_path}. Avvio download automatico...")
                try:
                    import urllib.request
                    import zipfile
                    url = "https://alphacephei.com/vosk/models/vosk-model-it-0.22.zip"
                    zip_path = self.model_path + ".zip"
                    
                    os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
                    print(f"Scaricando {url}...")
                    urllib.request.urlretrieve(url, zip_path)
                    
                    print(f"Estrazione in corso...")
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        # Estrae nella cartella genitore
                        zip_ref.extractall(os.path.dirname(self.model_path))
                    
                    # Cleanup
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                    print("✅ [VoskASR] Download e installazione modello completata.")
                except Exception as e:
                    print(f"❌ [VoskASR] Errore durante il download del modello: {e}")
                    return
            
        try:
            from collections import deque
            import re
            # Vosk logs too much by default
            vosk.SetLogLevel(-1)
            self.model = vosk.Model(self.model_path)
            # [v21.1] Grammar mirata per Wake Word istantaneo e preciso su Vosk offline (standby)
            self.wake_grammar = json.dumps([
                "marcus", "marcos", "markus", "robot",
                "ascolta", "fermati", "stop", "zitto", "silenzio",
                "io sono", "mi chiamo", "[unk]"
            ])
            self._is_listening = False
            self._target_listening = False
            self._speech_ring = deque(maxlen=25)
            self.recognizer = vosk.KaldiRecognizer(self.model, self.sample_rate, self.wake_grammar)
            self.recognizer.SetWords(True) # [v20.0] Abilita timestamp per parola nell'output JSON
            
            self._worker_thread = threading.Thread(target=self._worker, daemon=True, name="vosk_worker")
            self._worker_thread.start()
            print("✅ [VoskASR] Motore offline (Dual-Mode Wake-Grammar + Full Italian ASR) inizializzato!")
        except Exception as e:
            print(f"❌ [VoskASR] Errore inizializzazione: {e}")

    def _clean_vosk_text(self, raw_text: str) -> str:
        if not raw_text:
            return ""
        import re
        cleaned = re.sub(r'\b\[unk\]\b', '', raw_text, flags=re.IGNORECASE)
        cleaned = re.sub(r'\[unk\]', '', cleaned, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', cleaned).strip()

    def _recreate_recognizer_in_worker(self, full_vocab: bool):
        del self.recognizer
        self.recognizer = None
        gc.collect()
        if full_vocab:
            self.recognizer = vosk.KaldiRecognizer(self.model, self.sample_rate)
        else:
            self.recognizer = vosk.KaldiRecognizer(self.model, self.sample_rate, self.wake_grammar)
        self.recognizer.SetWords(True)

    def set_listening_mode(self, is_listening: bool):
        """Commuta in modo thread-safe (Sentinel Pattern FM-VUI-011) tra grammatica
        wake-word (standby) e dizionario italiano completo (ascolto attivo post-Marcus)."""
        target = bool(is_listening)
        if target != getattr(self, "_target_listening", False):
            self._target_listening = target
            if self.is_active():
                cmd = b"MODE_FULL_CMD" if target else b"MODE_WAKE_CMD"
                try:
                    self._audio_queue.put_nowait(cmd)
                except queue.Full:
                    pass

    def is_active(self):
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def set_energy_gating(self, enabled: bool):
        """Abilita o disabilita il pre-filtraggio energetico dei frame audio."""
        self.energy_gating = bool(enabled)

    def process_audio(self, pcm_bytes: bytes):
        """Inserisce chunk audio (16kHz mono int16) nella coda per l'elaborazione.
        Se energy_gating è attivo, i chunk di silenzio puro vengono scartati a monte
        senza risvegliare il decoder Kaldi, abbattendo la CPU in idle sotto l'1%."""
        if not self.is_active():
            return

        if self.energy_gating and len(pcm_bytes) >= 32:
            chunk_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
            rms = float(np.sqrt(np.mean(chunk_arr.astype(np.float32) ** 2)))

            # Soglia dinamica: voce presente se RMS supera il noise floor adattivo (SPEC-04 far-field)
            is_voice = rms > max(self._min_rms_threshold, self._noise_floor_ema * 1.25)

            if is_voice:
                self._hangover_chunks = self._max_hangover_chunks
                self._last_was_speech = True
            else:
                # Tracciamento lento del rumore di fondo solo durante il silenzio con clamping anti-drift
                self._noise_floor_ema = min(120.0, max(30.0, 0.98 * self._noise_floor_ema + 0.02 * rms))
                if self._hangover_chunks > 0:
                    self._hangover_chunks -= 1
                elif self._last_was_speech:
                    # Chiusura coda fonetica con padding silenzioso
                    self._last_was_speech = False
                    try:
                        self._audio_queue.put_nowait(bytes(len(pcm_bytes)))
                    except queue.Full:
                        pass
                    return
                else:
                    # Silenzio puro: azzeramento totale del carico di decodifica Kaldi
                    return

        try:
            self._audio_queue.put_nowait(pcm_bytes)
        except queue.Full:
            # [v19.6] Drop frame: log periodico ogni 50 drop per evidenziare CPU overload
            self._drop_count += 1
            if self._drop_count % 50 == 0:
                print(f"⚠️ [VoskASR] {self._drop_count} frame scartati (queue piena) — possibile CPU overload")

    def _worker(self):
        """Thread background che consuma l'audio ed esegue l'inferenza STT."""
        while not self._shutdown:
            try:
                frame = self._audio_queue.get(timeout=0.1)

                if frame == b"MODE_FULL_CMD":
                    if not self._is_listening:
                        self._is_listening = True
                        self._recreate_recognizer_in_worker(full_vocab=True)
                        # Riproduce gli ultimi frame vocali nel riconoscitore full-vocab per non perdere
                        # le parole pronunciate subito dopo "Marcus" nella stessa emissione di fiato
                        for bf in list(self._speech_ring):
                            try:
                                self.recognizer.AcceptWaveform(bf)
                            except Exception:
                                pass
                        self._speech_ring.clear()
                    continue

                if frame == b"MODE_WAKE_CMD":
                    if self._is_listening:
                        if self.recognizer:
                            final_text = self.recognizer.FinalResult()
                            text = self._clean_vosk_text(json.loads(final_text).get("text", ""))
                            if text:
                                self._handle_transcription(text, is_partial=False)
                        self._is_listening = False
                        self._recreate_recognizer_in_worker(full_vocab=False)
                        self._speech_ring.clear()
                    continue

                # [v20.0] Gestione sicura del flush tramite Sentinel (thread-safe, no race conditions)
                if frame == b"FLUSH_CMD":
                    if self.recognizer:
                        final_text = self.recognizer.FinalResult()
                        text = self._clean_vosk_text(json.loads(final_text).get("text", ""))
                        if text:
                            self._handle_transcription(text, is_partial=False)
                        # Ricostruisce il riconoscitore nel worker thread mantenendo la modalità corrente
                        # [v20.2 FM-VUI-014 Fix] Rilascio GC della memoria C++ SWIG nativa
                        self._recreate_recognizer_in_worker(full_vocab=self._is_listening)
                        self._speech_ring.clear()
                    continue

                if not self._is_listening:
                    self._speech_ring.append(frame)

                rec = self.recognizer
                if rec.AcceptWaveform(frame):
                    res = rec.Result()
                    text = self._clean_vosk_text(json.loads(res).get("text", ""))
                    if not self._is_listening:
                        self._speech_ring.clear()
                    if text:
                        self._handle_transcription(text, is_partial=False)
                else:
                    # Partial result for instant wake word detection
                    part = self._clean_vosk_text(json.loads(rec.PartialResult()).get("partial", ""))
                    if part:
                        self._handle_transcription(part, is_partial=True)
            except queue.Empty:
                pass
            except Exception as e:
                print(f"⚠️ [VoskASR] Errore nel worker: {e}")
                time.sleep(1.0)

    def force_flush(self):
        """Forza la restituzione del testo non ancora finalizzato (utile a fine frase).
        Invia un comando Sentinel alla coda per eseguire il flush in modo thread-safe."""
        if not self.is_active():
            return
        try:
            self._audio_queue.put_nowait(b"FLUSH_CMD")
        except queue.Full:
            pass

    def _handle_transcription(self, text: str, is_partial: bool = False):
        """Gestisce il testo trascritto: log e callback."""
        if not text:
            return
        
        # Chiama il callback per il rilevamento intent/wake word e per il logging
        if self.on_text_cb:
            self.on_text_cb(text, is_partial)

    def stop(self):
        self._shutdown = True
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
