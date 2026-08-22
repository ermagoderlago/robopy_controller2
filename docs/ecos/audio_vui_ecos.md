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
---

## 📈 ECO-2026-08-01-001: Porcupine Removal Fix & Continuous VAD Listening Mode
* **Stato:** ✅ **Completato, Sincronizzato e Verificato**
* **Descrizione:** Risoluzione del blocco di acquisizione audio microfonica causato dal check residuo `if self.porcupine is None: continue` in `respeaker_vui_node.py` e introduzione della registrazione automatica di tutte le conversazioni su disk log.
* **Modifiche VUI & LLM:**
  * **Sblocco Worker Audio:** Rimosso il check `if self.porcupine is None: continue` da `_audio_processing_worker` per ripristinare il flusso dei dati PCM dal microfono ReSpeaker Lite verso il VAD e Gemini Live API.
  * **Continuous Listening Mode:** Impostata `self._ev_listening.set()` a `True` di default e modificata la routine `_on_listen_timeout` per preservare l'ascolto continuo attivo senza mutare il microfono.
  * **Ciclo di Vita LED:** Mantenute le transizioni LED `LISTENING` (voce utente in corso) ➔ `THINKING` (fine frase / in attesa di risposta Gemini) ➔ `SPEAKING:<intensity>` / `SUCCESS` (riproduzione parlato Marcus) ➔ `IDLE` (ritorno a umore base).
  * **Registrazione Conversazioni Persistente:** Implementata la funzione `_save_conversation_turn()` in `llm_service.py` per registrare ogni scambio di battute utente/Marcus in formato JSONL con timestamp su `/mnt/ssd/robopy_controller_host/logs/conversations.jsonl` e `~/robopy/logs/conversations.jsonl`.

---

## 📈 ECO-2026-08-02-002: Dynamic Gain Calibration, Noise Floor Subtraction & 500ms Pre-Roll Latency Fix
* **Stato:** ✅ **Completato, Sincronizzato e Verificato**
* **Descrizione:** Risoluzione delle incomprensioni ASR e del ritardo di risposta VUI mediante passaggio a guadagno base 2.5x con AGC dinamico 1.0x-4.0x, profilazione/soppressione del rumore di silenzio e riduzione pre-roll a 500ms.
* **Modifiche VUI & DFMEA:**
  * **Guadagno Dinamico & AGC:** Ridotto `stt_gain` di default da 30.0x a 2.5x ed inserito AGC dinamico con target speech RMS (~8000), eliminando il clipping 16-bit e la distorsione armonica del parlato.
  * **Profilazione del Silenzio:** Implementata la calibrazione nei primi 2s di avvio per registrare il rumore di fondo della stanza ed effettuare la soppressione soft del rumore di fondo dal flusso audio PCM.
  * **Pre-Roll 500ms:** Ridotto `PRE_ROLL_FRAMES` a 25 frame (500ms), annullando 2.0s di latenza nell'invio audio a Gemini Live API.
  * **Script di Calibrazione:** Creato `scripts/test_mic_calibration.py` per l'ispezione SNR, clipping e calibrazione guidata dei parametri microfonici.
  * **Registro FMEA:** Inserito Failure Mode `FM-VUI-010` nel database DFMEA ed aggiornato il report esecutivo.

---

## 📈 ECO-2026-08-06-001: NotebookLM Joint Audio Acquisition Benchmark & Mechanical Isolation Plan
* **Stato:** ✅ **Completato, Pianificato e Verificato**
* **Descrizione:** Analisi incrociata delle raccomandazioni di acquisizione audio in collaborazione con NotebookLM (`Marcus_ROS2_Docs`) e redazione del piano d'azione per l'ottimizzazione dell'ASR e il disaccoppiamento meccanico della testa.
* **Modifiche VUI & Lezioni:**
  * **Verifica Architetturale:** Confermato il flusso 16kHz mono PCM nativo USB per l'input e il resampling 48kHz obbligatorio via `audioop.ratecv` per l'output DAC PyAudio.
  * **ReSpeaker Lite DSP:** Confermato l'uso del canale sinistro (`l_ch`) per evitare phase cancellation e disattivazione AGC hardware ReSpeaker in favore del Limiter software vettoriale + Butterworth HPF @ 140Hz.
  * **Isolamento Meccanico:** Definiti i requisiti per la stampa 3D di gommini antivibranti in TPU e l'inserimento di schiuma fonoassorbente (foam 5mm) nella cavità della testa di pib per isolare i servomotori (MG996R/DS3225MG).
  * **Aggiornamento Documentazione:** Aggiornati `docs/lessons/audio_vui_pipeline.md` e generato l'artifact `implementation_plan.md`.

---

## 📈 ECO-2026-08-06-002: ReSpeaker Dual-Chip Firmware Benchmark & XMOS Beamforming Integration
* **Stato:** ✅ **Completato, Sincronizzato e Verificato**
* **Descrizione:** Validazione della compatibilità del firmware in uso sulla scheda ReSpeaker Lite (Seeed Factory su XMOS XU316 ed ESPHome v14.0-LED su XIAO ESP32-S3) e integrazione dell'architettura di Beamforming broadside a 0% CPU host.
* **Modifiche & Analisi:**
  * **Verifica Firmware XMOS XU316:** Confirmata la presenza di AEC, NS e 2-Mic Broadside Beamforming integrati nel firmware di fabbrica XMOS con output audio USB ALSA diretto.
  * **Verifica Firmware ESPHome XIAO:** Confermato l'uso del firmware v14.0-LED in modalità USB-Pure (gestione LED RMT via USB Serial JTAG a 115200 baud senza interferenze sul clock audio).
  * **Integrazione Beamforming:** Verificata l'assenza di carico CPU (0%) e l'orientamento polare broadside dei 2 microfoni MEMS per la massima sensibilità frontale.
  * **Piano d'Azione v3.0:** Aggiornati l'artifact `implementation_plan.md` e lo script di pre-test `scripts/test_vui_audio_pretest.py`.

---

## 📈 ECO-2026-08-21-001: 3-Minute Extended Conversation Session Window & Directed Follow-Up Gating
* **Stato:** ✅ **Completato, Sincronizzato e Verificato**
* **Descrizione:** Risoluzione del problema di chiusura prematura della conversazione dopo 8 secondi e prevenzione delle risposte a conversazioni di terzi in sottofondo mediante finestra conversazionale a 180s e Directed Follow-up Gating.
* **Modifiche VUI, Live Connection Manager & LLM:**
  * **Rimozione Override 8s:** Eliminato `self._listen_timeout_sec = 8.0` in `respeaker_vui_node.py` e impostato il default a 180.0 secondi (3 minuti).
  * **Reset Dinamico del Timer:** Riavvio automatico del timer di 180s al termine del parlato AI (`_tts_speaking_cb: False`) e al completamento di ogni frase utente (`_publish_end_of_speech`).
  * **Estensione Finestra Live API:** Aggiornato `LiveConnectionManager.active_session_timeout` a 180.0s con tracciamento `turns_since_wakeword`.
  * **Non-Destructive `<IGNORE_TURN>`:** Gestione del token di silenzio senza mutare il microfono né disconnettere il WebSocket, preservando la sessione attiva per 3 minuti.
  * **Allineamento System Prompt:** Istruito il modello a rispondere vocalmente solo a richieste dirette a Marcus durante i turni successivi di sessione aperta.



