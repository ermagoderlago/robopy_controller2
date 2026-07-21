# Engineering Change Orders - Audio & VUI

Questo documento traccia la cronologia delle modifiche ingegneristiche (ECO) apportate al modulo VUI e audio del robot Marcus.

---

## 📈 ECO-2026-05-25-001: Marcus AI v14.2 (Anima Robotica) - Sezione VUI & LED
* **Stato:** ✅ **Completato, Flashato e Verificato**
* **Descrizione:** Sincronizzazione dinamica visiva LED basata sull'umore cognitivo ed emozionale elaborato dal LLM (Gemini 2.5 Flash), espandendo il firmware ESPHome con 4 stati emotivi e ottimizzando le routine VAD/Porcupine.
* **Modifiche VUI:**
  * Sottoscritto il topic `/ai/conversation/mood` (`std_msgs/String`) in `respeaker_vui_node.py`.
  * Gestito il ripristino dell'effetto LED all'umore corrente al termine di ogni turno audio.
  * [v14.1 Hot-Fix] Abbassato il noise gate minimo a `300.0` e incrementato il silence timeout a `40 frames` (~800ms) per evitare truncations precoci.
  * Creati i 4 effetti LED RMT nel firmware: `HAPPY` (oro), `TIRED` (viola indaco), `APOLOGETIC` (arancione), `LONELY` (turchese).
  * Integrati i LED per mostrare `THINKING` (blu flicker) alla fine del parlato e `SUCCESS` (verde fisso) allo start del TTS.

---

## 📈 ECO-2026-05-27-002: ReSpeaker Direct Hardware Capture
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato**
* **Descrizione:** Risoluzione del problema del microfono silenzioso (RMS ~40) dovuto al routing automatico errato su virtual device PipeWire.
* **Modifiche VUI:**
  * Invertita la logica in `_find_audio_devices()` in `respeaker_vui_node.py`: ora cerca prioritariamente `device_name_target` ("ReSpeaker") per forzare l'apertura hardware diretta del device ALSA (`hw:0,0`).
  * Ottenuto un RMS stazionario di fondo di `~55.7`, consentendo alla soglia adattiva del noise gate di auto-calibrarsi a `~1821.3` (ampio margine per il parlato a ~3000+ RMS).

---

## 📈 ECO-2026-06-02-001: Peak Limiter / AGC Software in Tempo Reale
* **Stato:** ✅ **Completato, Sincronizzato e Riavviato**
* **Descrizione:** Progettazione e implementazione di un algoritmo di compressione e limitazione digitale (Peak Limiter / AGC software) in tempo reale direttamente nel ciclo di cattura PCM (16kHz, 16-bit mono) nel nodo VUI.
* **Modifiche VUI:**
  * Inizializzato lo stato del limitatore (`self._limiter_gain = 1.0` e `self._limiter_release_rate = 0.0667`) in `__init__`.
  * Implementato l'algoritmo vettoriale in `_audio_processing_worker` che monitora il picco assoluto di ogni chunk. Se supera `26000`, applica un'attenuazione istantanea (tempo di attacco 0ms) per bloccare i campioni entro `30000.0`.
  * Configurato il tempo di rilascio lineare (~900ms totali) per far risalire il guadagno a `1.0` eliminando gli effetti di "pompaggio" acustico.

---

## 📈 ECO-2026-07-21-001: Far-Field Sensitivity & Fan Noise HPF Mitigation
* **Stato:** ✅ **Completato e Sincronizzato**
* **Descrizione:** Risoluzione dell'insufficienza di sensibilità microfonica in far-field (1-3m) ed eliminazione dei falsi segnali di rumore inviati a Gemini Live generati dalla ventola di raffreddamento del Pi 5.
* **Modifiche VUI:**
  * Implementato un filtro passa-alto Butterworth 2° ordine @ 140 Hz (HPF) nel loop di acquisizione audio di `respeaker_vui_node.py` prima di VAD, Porcupine e Gemini Live, con fallback su RC filter se SciPy non presente.
  * Corretto l'input di `webrtcvad.is_speech()` trasmettendo il segnale filtrato `selected_hp_all_int16` al posto di `l_ch` per consentire il rilevamento istantaneo della fine della frase (End-Of-Speech) e sbloccare lo stato del LED.
  * Introdotta la selezione dinamica del canale con maggior energia vocale tra Left e Right dell'array ReSpeaker Lite.
  * Riconfigurata l'auto-calibrazione della soglia `noise_gate_threshold` sul segnale HPF, limitando la soglia massima nell'intervallo `[800.0, 4500.0]`.
  * Ridotto `MIN_SPEECH_FRAMES` da 10 a 4 frame (80ms) per l'apertura immediata del gate vocale.
  * Regolato `stt_gain` predefinito a `18.0x` in `robot_ia_launch.py`.
