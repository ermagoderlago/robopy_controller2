import os
import json
import queue
import threading
import datetime
import time

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
        
        self.model = None
        self.recognizer = None
        
        if not HAS_VOSK:
            print("⚠️ [VoskASR] Libreria vosk non installata. Installa con 'pip install vosk'.")
            return
            
        if not os.path.exists(self.model_path):
            print(f"⚠️ [VoskASR] Modello non trovato in {self.model_path}. Avvio download automatico...")
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
            # Vosk logs too much by default
            vosk.SetLogLevel(-1)
            self.model = vosk.Model(self.model_path)
            # [v19.4] Vocabolario completo aperto per riconoscimento italiano preciso e zero allucinazioni su rumori
            self.recognizer = vosk.KaldiRecognizer(self.model, self.sample_rate)
            self.recognizer.SetWords(True) # [v20.0] Abilita timestamp per parola nell'output JSON
            
            self._worker_thread = threading.Thread(target=self._worker, daemon=True, name="vosk_worker")
            self._worker_thread.start()
            print("✅ [VoskASR] Motore offline (Full Italian Vocabulary ASR) inizializzato!")
        except Exception as e:
            print(f"❌ [VoskASR] Errore inizializzazione: {e}")

    def set_listening_mode(self, is_listening: bool):
        # Mantenuto per compatibilità interfaccia
        pass

    def is_active(self):
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def process_audio(self, pcm_bytes: bytes):
        """Inserisce chunk audio (16kHz mono int16) nella coda per l'elaborazione."""
        if not self.is_active():
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
                
                # [v20.0] Gestione sicura del flush tramite Sentinel (thread-safe, no race conditions)
                if frame == b"FLUSH_CMD":
                    if self.recognizer:
                        final_text = self.recognizer.FinalResult()
                        text = json.loads(final_text).get("text", "").strip()
                        if text:
                            self._handle_transcription(text, is_partial=False)
                        # Ricostruisce il riconoscitore nel worker thread (thread-safe, workaround per mancanza di Reset() nell'API pubblica)
                        self.recognizer = vosk.KaldiRecognizer(self.model, self.sample_rate)
                        self.recognizer.SetWords(True)
                    continue

                rec = self.recognizer
                if rec.AcceptWaveform(frame):
                    res = rec.Result()
                    text = json.loads(res).get("text", "").strip()
                    if text:
                        self._handle_transcription(text, is_partial=False)
                else:
                    # Partial result for instant wake word detection
                    part = json.loads(rec.PartialResult()).get("partial", "").strip()
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
