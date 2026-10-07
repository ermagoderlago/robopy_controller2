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

---

## 📈 ECO-2026-09-03-002: Vosk Targeted Wake Grammar, End-Of-Speech Forwarding & Virtualenv Path Injection
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato sul Robot**
* **Descrizione:** Risoluzione definitiva del blocco di ascolto e mancata risposta vocale del robot a seguito della chiamata "Marcus" (FM-VUI-024).
* **Modifiche VUI, LLM e Pipeline Audio:**
  * **[TARGETED WAKE GRAMMAR]** `robopy_controller/robot_ai/services/local_asr_vosk.py`:
    - Sostituito il vocabolario aperto con grammatica vincolata JSON `["marcus", "marco", "marcos", "markus", "robot", "ascolta", "fermati", "stop", "zitto", "silenzio", "io sono", "mi chiamo", "[unk]"]`.
    - Kaldi compila un automa a 16 stati: zero allucinazioni su parole non pertinenti (es. 'plauso', 'l'uso', 'p'), latenza ridotta a <20ms e carico CPU <1%.
  * **[END-OF-SPEECH SIGNAL FORWARDING]** `robopy_controller/robot_ai/services/llm_service.py`:
    - Modificata `audio_callback_ros` per inoltrare i pacchetti vuoti (`len(raw_bytes) == 0`) a `_live_mgr.send_audio_chunk(b'')`, consentendo l'emissione di `activity_end=types.ActivityEnd()` su Gemini Live WebSocket e sbloccando la sintesi vocale.
  * **[VIRTUALENV SYS.PATH INJECTION]** `robopy_controller/robot_ai/services/local_asr_vosk.py`:
    - Inserita in testa al modulo l'aggiunta dinamica dei percorsi `/home/robopy/ros2_venv/lib/python3.11/site-packages` in `sys.path` per abilitare l'import di `vosk` quando `respeaker_vui_node` viene eseguito da `/usr/bin/python3`.
  * **[DEBOUNCE RE-TRIGGER WAKE WORD]** `robopy_controller/nodes/respeaker_vui_node.py`:
    - Sostituito il blocco rigido `if self._ev_listening.is_set(): return` con un debounce di 2 secondi, garantendo feedback acustico (beep a 1000Hz) e riapertura del turno ad ogni chiamata diretta.
  * **[CHAT SILENCING UNMUTE FIX]** `robopy_controller/robot_ai/orchestration/conversation.py`:
    - Incapsulata l'elaborazione di `process_input` in `try ... finally: self._current_source = ""` prevenendo il silenziamento involontario permanente delle risposte vocali dopo messaggi testuali da Foxglove/Web.

---

## 📈 ECO-2026-09-04-002: Definitive Porcupine Code Purge & Legacy Artifacts Cleanup
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato sul Robot**
* **Descrizione:** Bonifica radicale e definitiva di ogni traccia residua del framework proprietario Picovoice Porcupine (EOL) da codice sorgente, nodi ROS 2, entry point e configurazioni del workspace (FM-VUI-004).
* **Modifiche apportate:**
  * **[ELIMINAZIONE FILE E ARTEFATTI OBSOLETI]**:
    - Rimossi `tmp_vui.py` e `test_porcupine.py`.
    - Rimosso il nodo orfano `robopy_controller/nodes/wake_word_node.py`.
    - Eliminati i modelli binari Picovoice `robopy_controller/config/wake_word/porcupine_params_it.pv` e `marcus.ppn`.
  * **[BONIFICA RESPEAKER VUI NODE]** `robopy_controller/nodes/respeaker_vui_node.py`:
    - Rimossi i buffer residui allocati in `__init__` (`_porcupine_residual_buf`, `_porcupine_residual_len`, `_porcupine_assembly_buf`).
    - Eliminato `self.porcupine = None` e aggiornati tutti i commenti/log tecnici verso Vosk / Hailo KWS.
  * **[BONIFICA BUILD SYSTEM & LAUNCH]** `setup.py`, `launch/fast_flow_launch.py`:
    - Rimosso l'entry point `wake_word_node` da `console_scripts`.
    - Rimosso il nodo `wake_word_node` dalla `LaunchDescription`.
  * **[FMEA]** Failure mode `FM-VUI-004` validato e chiuso (`CLOSED`).

---

## 📈 ECO-2026-09-10-001: Persistenza Seriale Udev `/dev/respeaker` e Auto-Discovery Dinamico (FM-VUI-024)
* **Stato:** ✅ **Completato, Testato Unitariamente e Attivo**
* **Descrizione:** Risoluzione del fallimento di connessione seriale in `respeaker_interface_node` (`[Errno 2] No such file or directory: '/dev/ttyACM0'`) causato dall'inserimento di nuove periferiche USB (LiDAR ToF C1) o ri-enumerazione del bus.
* **Modifiche apportate:**
  * **[REGOLA UDEV PERSISTENTE]** `/etc/udev/rules.d/99-marcus-serial.rules`:
    - Aggiunte regole deterministiche per il microcontrollore Seeed XIAO ESP32-S3 (USB VID `303a`, PID `1001`/`0002` e VID `2886`) che assegnano stabilmente il symlink persistente `/dev/respeaker`.
  * **[AUTO-DISCOVERY & RESILIENZA RUNTIME]** `robopy_controller/nodes/respeaker_interface_node.py`:
    - Aggiornata porta di default da `/dev/ttyACM0` a `/dev/respeaker`.
    - Implementato metodo `_resolve_port()` con fallback a cascata su: 1) porta configurata, 2) `/dev/respeaker`, 3) `/dev/serial/by-id/*Espressif*` / `*Seeed*`, 4) `/dev/ttyACM*`.
    - Se l'inserimento del LiDAR fa slittare l'ESP32 su `ttyACM1`, il nodo si riaggancia autonomamente senza andare in eccezione bloccante.
  * **[LAUNCH & RUNTIME CONFIG]** `restart_hailo.sh`, `launch/fast_flow_launch.py`, `launch/robot_ia_launch.py`:
    - Aggiornato parametro `uart_port` a `/dev/respeaker`.
  * **[TEST UNITARI]** `test/unit/test_respeaker_interface_port_resolver.py`:
    - Creato test unitario a copertura completa (5 scenari) eseguito con successo al 100%.

---

## 📈 ECO-2026-09-26-006: Sblocco Conversazionale Post-Wakeword & Watchdog Antistallo (FM-VUI-030)
* **Stato:** ✅ **Completato, Testato Unitariamente e Attivo**
* **Descrizione:** Risoluzione del doppio deadlock silente che impediva a Marcus di rispondere all'utente dopo aver rilevato la wake word "Marcus" via Vosk.
* **Modifiche apportate:**
  * **[RESET GATE SU WAKE WORD & WATCHDOG 8s]** `robopy_controller/robot_ai/services/live_connection_manager.py`:
    - `on_wakeword_detected()`: reset incondizionato di `_turn_in_progress = False`, `_activity_started = False` e drenaggio immediato dei chunk stantii dalla coda audio.
    - `_enqueue_audio()` e `_audio_sender_loop()`: introdotto watchdog a 8.0s su `_turn_in_progress` che sblocca automaticamente l'invio audio se Gemini Live non emette `turn_complete`.
    - Gestori `sc.interrupted`, `sc.turn_complete` e `<ignore_turn>`: allineamento del timestamp di guardia.
  * **[AUTO-DECAY 2.5s & RESET ECO SU WAKE WORD]** `robopy_controller/robot_ai/services/audio_buffer_manager.py`:
    - `is_speaker_playing()` e `push_mic_chunk()`: implementato auto-decay temporale (2.5s senza nuovi chunk) dello stato altoparlante, prevenendo la soppressione acustica spuria del parlato a volume normale.
  * **[HANDSHAKE WAKE WORD ROS 2]** `robopy_controller/robot_ai/services/llm_service.py`:
    - `wakeword_callback_ros`: reset immediato di `audio_buffer.set_speaker_playing(False)` e pulizia dei buffer microfono/altoparlante all'innesco di `/wake_word`.
  * **[FMEA & DOCUMENTAZIONE]** Failure mode `FM-VUI-030` registrato in `fmea/dfmea.yaml` e documentato in `docs/lessons/audio_vui_pipeline.md`.

---

## 📈 ECO-2026-09-26-007: PyAudio Stream Watchdog & Gemini Live WebSocket Receive Watchdog (FM-VUI-031 & FM-VUI-032)
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato sul Robot**
* **Descrizione:** Risoluzione definitiva della sordità silente da disconnessione cavo USB ReSpeaker e del freeze asincrono del nodo cognitivo a 90% CPU su stallo WebSocket cloud.
* **Modifiche apportate:**
  * **[PYAUDIO STREAM LIVENESS WATCHDOG]** `robopy_controller/nodes/respeaker_vui_node.py`:
    - `_audio_input_callback`: tracciamento di `_last_input_chunk_time = time.monotonic()`.
    - `_audio_stream_watchdog`: timer a 1.0Hz per rilevare assenza di chunk audio (> 3.0s).
    - `_recover_audio_stream`: procedura asincrona non-bloccante di auto-recovery ALSA (chiusura stream, distruzione e re-istanza PyAudio, re-discovery ReSpeaker Lite e riapertura stream).
  * **[WEBSOCKET RECEIVE TIMEOUT & AUTO-RECONNECT]** `robopy_controller/robot_ai/services/live_connection_manager.py`:
    - Sostituito `async for msg in session.receive():` con iteratore asincrono protetto da `asyncio.wait_for`.
    - Timeout di guardia a 15.0s durante turno in corso (`_turn_in_progress=True`) e a 60.0s durante idle standby: se il socket si blocca, viene forzato il reset del turno e una riconnessione pulita immediata.
  * **[DFMEA & REPORT]** Registrati `FM-VUI-031` e `FM-VUI-032` in `fmea/dfmea.yaml`, aggiornato `fmea/IMPROVEMENT_INDEX.yaml` e ricalcolati gli indici RPN esecutivi.

---

## 📈 ECO-2026-09-27-001: Adaptive Noise Gate Uncapped, Filtro Non-Speech ASR e Consolidamento NOMAD Exploration (FM-VUI-033 & FM-NOM-011)
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato sul Robot**
* **Descrizione:** Risoluzione del loop di allucinazioni vocali da rumore di fondo ("Marcus parla a vanvera") e dell'errore nell'avvio vocale dell'esplorazione autonoma reattiva NOMAD.
* **Modifiche apportate:**
  * **[ADAPTIVE NOISE GATE & MIN SPEECH FRAMES]** `robopy_controller/nodes/respeaker_vui_node.py`:
    - Corretta la formula della soglia adattiva da `min(current_threshold, adaptive_target)` a `max(base_threshold, ambient_floor * 2.2)`. La soglia ora sale dinamicamente sopra il rumore ambientale della ventola (fino a 400+ RMS).
    - Aumentato `current_min_speech` da 2 a 5 frame (100ms) durante la conversazione attiva e a 7 frame (140ms) in idle, azzerando i falsi trigger su click e rumore stazionario.
  * **[FILTRO NON-SPEECH & ANTI-CHATTER GATING]** `robopy_controller/robot_ai/services/live_connection_manager.py`:
    - Intercettazione dei token non-speech ASR (`<noise>`, `<laughter>`, `<cough>`, `<sigh>`) ed esclusione dal buffer conversazionale.
    - Soppressione totale dell'output audio sintetizzato e delle tool calls quando l'utente non ha pronunciato parole reali.
    - Aggiornamento di `last_successful_turn_time` vincolato alla presenza di parole umane intelligibili, evitando che le risposte allucinate azzerino perennemente il timeout di 180 secondi.
  * **[UNIFICAZIONE SKILL NOMAD EXPLORATION]** `robopy_controller/robot_ai/skills/builtin/nomad_exploration_skill.py` & `visual_exploration_skill.py`:
    - Definito schema parametri esplicito `{"action": "start"|"stop"}` per `nomad_exploration`.
    - Rimosso lo shadowing di `visual_exploration` in `orchestrator.py` e introdotta delega trasparente verso NOMAD.




---

## 📈 ECO-2026-10-01-001: VUI Diagnostic Batch — 7 Fix Responsività Ascolto/Risposta

* **Stato:** ✅ **Completato, Collaudato (syntax check) e Pronto per Sync**
* **Motivazione:** L'utente ha segnalato i sintomi: "a volte non risponde", "non sente", "dopo prolungata inattività non capisce/non risponde". Analisi con 3 ricercatori in parallelo ha identificato 6 cause radice.
* **DFMEA Correlati:** FM-VUI-035b (nuovo), FM-VUI-036 (nuovo), FM-VUI-031, FM-VUI-032

### File Modificati

| File | Fix | Descrizione |
|:-----|:----|:------------|
| `robopy_controller/robot_ai/services/live_connection_manager.py` | FIX-01 | Gating finestra conversazionale spostato da EOS ad ActivityStart |
| `robopy_controller/robot_ai/services/live_connection_manager.py` | FIX-02 | Riconnessione immediata su errore `send_realtime_input` |
| `robopy_controller/robot_ai/services/live_connection_manager.py` | FIX-06 | Reset token di resumption su sessione scaduta |
| `robopy_controller/robot_ai/services/live_connection_manager.py` | FIX-07 | Keepalive periodico WebSocket ogni 120s (`_keepalive_loop`) |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-03 | Separazione guadagno Vosk (pieno) dal guadagno Gemini (attenuato) |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-04 | Ri-trigger wake word sempre possibile (reset timer se already_listening) |
| `scripts/watchdog.sh` | FIX-05 | Monitoraggio e respawn automatico di `respeaker_vui_node` |

### Dettaglio Modifiche Chiave

**FIX-01 — Trappola degli 8 Secondi (CAUSA RADICE #1):**
- Aggiunta variabile `self._turn_started_within_window: bool` per memorizzare la validità del turno al momento dell'ActivityStart.
- Al momento dell'ActivityStart: valutazione `is_active` e salvataggio in `_turn_started_within_window`.
- Al momento dell'EOS: check su `_turn_started_within_window` invece di ricalcolare il tempo.
- Turni fuori finestra scartati **senza** chiamare `_reconnect()` — WebSocket resta vivo.

**FIX-07 — Keepalive Periodico (CAUSA RADICE WebSocket Zombie):**
- Nuovo metodo asincrono `_keepalive_loop()` lanciato da `start_loop()`.
- Ogni 120s in idle, invia `LiveClientContent(turns=[], turn_complete=False)`.
- Se il keepalive fallisce, trigger immediato di `_reconnect()`.

**FIX-03 — Guadagno Vosk Separato:**
- In `_audio_processing_worker`: quando TTS o cooldown attivi, Vosk riceve segnale con `vosk_gain = self.stt_gain` (guadagno base, non attenuato).
- Corregge il guadagno effettivo da 0.6x a 2.5x per il rilevamento far-field della wake word.

### Impatto Atteso

| Metrica | Prima | Dopo |
|:--------|:------|:-----|
| Tasso risposta su prima chiamata | ~60-70% | >95% |
| Risveglio dopo 30min inattività | Fallisce quasi sempre | <3s latenza |
| Range ascolto wake word | ~1.5m max | ~3m |
| Recovery post-crash VUI | Manuale | Automatico <10s |

### Addendum Collaudo Live & Risoluzione Sordità Far-Field (FIX-08)
* **Data:** 2026-10-02 19:42
* **Analisi Log Live:** Rilevato nel log `/home/robopy/robopy/logs/respeaker_vui_node.log` che `Gate` rimaneva forzatamente a `400.0`, bloccando il VAD vocale su frasi a volume normale (~150-250 RMS amplificato).
* **Risoluzione:**
  1. Identificato disallineamento nei wrapper `/mnt/ssd/robopy_controller_host/scripts/respeaker_vui_node` causato dal flag `-u` in `sync_marcus.sh`.
  2. Implementato sync forzato di `scripts/` in `sync_marcus.sh` e ripristinati tutti i corretti script forwarder.
  3. Ricalibrato `base_clamp` da 250/150 a `95.0` (idle) e `70.0` (attentive) in `respeaker_vui_node.py` e aumentato `stt_gain` a `2.2` in `restart_hailo.sh`.
  4. Misurazione telemetrica a regime post-riavvio: `Ambient_EMA = 30.0` -> `Gate = 97.6` con amplificazione nominale 2.20x.

---

## 📈 ECO-2026-10-02-001: Disaccoppiamento Beep da AI Speaking, Riduzione Cooldown ed Espansione Fonetica Wake Word (FIX-09)
* **Stato:** ✅ **Completato, Collaudato (108 unit tests passati) e Pronto per Deploy**
* **Motivazione:** Risoluzione del doppio fallimento: la parola "Marcus" non veniva rilevata con costanza (60-70% di drop rate su Vosk), e quando veniva rilevata, il robot non capiva l'utente e non rispondeva per colpa del beep di sveglia che auto-attenuava il microfono a 0.1x (600ms di muting) inducendo Gemini Live a produrre un tag `<IGNORE_TURN>` su audio ritenuto "non italiano".
* **DFMEA Correlati:** FM-VUI-037 (nuovo), FM-VUI-002, FM-VUI-003, FM-VUI-027

### File Modificati
| File | Modifica | Descrizione |
|:-----|:---------|:------------|
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-09a | Espansione `wakeword_tokens = ["marcus", "markus", "marcos", "marco"]` |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-09b | Disaccoppiamento riproduzione beep: `_play_beep()` con flag `is_speech=False`, tracciamento `_is_playing_tts` separato da `_is_playing_out` |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-09c | Rimozione di `_last_ai_speaking_time` da tutti i beep (sveglia 80ms, timeout, mute) |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-09d | Riduzione cooldown post-TTS reale da 400ms a 150ms e finestra protezione Vosk da 0.6s a 0.25s |
| `robopy_controller/robot_ai/services/llm_service.py` | FIX-09e | Riformulazione del `system_prompt`: vincolo di interpretazione in lingua italiana per fonemi ambigui, limitazione di `<IGNORE_TURN>` a vero silenzio o terzi |

---

## 📈 ECO-2026-10-03-001: Resilienza PyAudio DAC Stream Sleep (-9988), Auto-Reconnect Zombie WebSocket e Reset VAD (FIX-10)
* **Stato:** ✅ **Completato, Collaudato (176 unit tests passati) e Pronto per Deploy**
* **Motivazione:** Risoluzione del problema di sordità/mutismo insorto dopo diverse ore di inattività: crash silenzioso di `out_stream` (`[Errno -9988] Stream closed`), blocco `_is_playing_out=True`, falso EOS immediato da frame VAD residui post-wakeword e stallo del WebSocket Gemini Live su watchdog senza riconnessione.
* **DFMEA Correlati:** FM-VUI-038 (nuovo), FM-VUI-039 (nuovo), FM-VUI-035b, FM-VUI-036

### File Modificati
| File | Modifica | Descrizione |
|:-----|:---------|:------------|
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-10a | Implementazione `_ensure_out_stream()` con auto-riapertura DAC 48kHz stereo e cattura sicura errori in `_playback_worker` con `try...finally` |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-10b | Reset esplicito contatori e stato VAD (`_speech_frame_count=0`, `_silence_frame_count=0`, `_is_speech_active=False`, `_vad_residual_len=0`) in `_on_wakeword_detected()` |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-10c | Debounce ri-trigger timer finestra su Vosk parziale per prevenire log flood ad ogni frame audio |
| `robopy_controller/robot_ai/services/live_connection_manager.py` | FIX-10d | Inizializzazione `_turn_started_within_window = True` in `on_wakeword_detected()` per accettare la frase utente post-wakeword |
| `robopy_controller/robot_ai/services/live_connection_manager.py` | FIX-10e | Riconnessione attiva (`_reconnect()`) su scadenza watchdog `turn_in_progress` a 8.0s (abbattimento socket zombie) |
| `robopy_controller/robot_ai/services/live_connection_manager.py` | FIX-10f | Riduzione `KEEPALIVE_INTERVAL` da 120s a 45s per prevenire chiusure silenti NAT/Google |

---

## 📈 ECO-2026-10-05-001: Jitter Buffer Anti-Starvation, Risoluzione Parlato Scattoso, Hard Ceiling Ascolto e Telemetria VUI (Fasi 4 & 5)
* **Stato:** ✅ **Completato, Collaudato sul Robot Live e Confermato**
* **Motivazione:** Risoluzione del parlato TTS "scattoso, poco fluido e con interruzioni", eliminazione del blocco della wake word da ascolto perpetuo, e completamento delle Fasi 4 e 5 del Piano di Ottimizzazione Audio.
* **DFMEA Correlati:** FM-VUI-040 (nuovo, RPN 126 -> 14), FM-VUI-041 (nuovo, RPN 162 -> 18), FM-VUI-001, FM-VUI-037

### File Modificati
| File | Modifica | Descrizione |
|:-----|:---------|:------------|
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-11a | Jitter Buffer Adattivo in `_playback_worker`: prebuffer a 4 chunk (~160ms) e timeout drain di 100ms in `_audio_out_queue.get(timeout=0.100)` per eliminare ALSA underrun (XRUN) |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-11b | Conservazione stato filtri `audioop.ratecv` tra chunk contigui e deduplicazione pacchetti audio in `_speaker_audio_cb` |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-11c | Hard Ceiling di 15s in `_on_listen_timeout` per prevenire il loop infinito d'ascolto indotto da rumori o TTS echo |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-11d | Feedback acustico beep (80ms) abilitato anche su wake word ripetuta durante la finestra d'ascolto |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-11e | [Fase 5] Publisher telemetrico periodico su `/robopy/vui/diagnostics` per monitoraggio stato VAD, RMS e coda |
| `robopy_controller/robot_ai/audio/dsp_pipeline.py` | FIX-11f | Smoothing esponenziale AGC per chunk (attack 50ms, release 800ms) e conservazione guadagno nominale 2.0x su silenzio |
| `robopy_controller/robot_ai/audio/turn_manager.py` | FIX-11g | WebRTC VAD Mode 2 (Balanced) con pre-gate `rms > 50.0` e isteresi a 120ms |
| `restart_hailo.sh` / `scripts/restart_vui_ai.sh` | FIX-11h | [Fase 4] Incremento `playback_volume` da 0.08 a 0.35 per volume vocale pieno e naturale |

---

## 📈 ECO-2026-10-06-001: Rigetto Rumore TV / Terzi, Soppressione Frame Duplicati e Calibrazione Volume al 10% (FIX-12)
* **Stato:** ✅ **Completato, Sincronizzato e Pronto per Deploy**
* **Motivazione:** Risoluzione di: (1) risvegli spuri da TV con Marcus che parla da solo lamentando "rumore di fondo", (2) risposte tardive o a sproposito dovute a spezzoni TV inviati a Gemini Live, (3) voce frammentata/balbettante causata da rimbalzo duplicato su topic ROS 2 multipli, (4) volume troppo alto riportato a 10%.
* **DFMEA Correlati:** FM-VUI-042 (nuovo, RPN 144 -> 16), FM-VUI-003, FM-VUI-033, FM-VUI-040

### File Modificati
| File | Modifica | Descrizione |
|:-----|:---------|:------------|
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-12a | Rimosso `"marco"` da wake word tokens; matching vincolato a `\b(marcus|markus|marcos)\b` con regex |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-12b | Rimossa sottoscrizione duplicata su `/ai/conversation/audio_chunk`, mantenendo solo `/respeaker/speaker_audio` |
| `robopy_controller/nodes/respeaker_vui_node.py` | FIX-12c | Volume di default impostato a `0.10` (10%) |
| `robopy_controller/robot_ai/audio/turn_manager.py` | FIX-12d | WebRTC VAD impostato su Mode 3 (Aggressive noise rejection); pre-gate RMS elevato da 50.0 a 110.0 |
| `robopy_controller/robot_ai/audio/dsp_pipeline.py` | FIX-12e | Calibrazione AGC: guadagno fissato a 1.0x (nessun boost) per segnali sotto 130 RMS (TV/ventole); `max_gain` limitato a 2.0x |
| `robopy_controller/robot_ai/services/live_connection_manager.py` | FIX-12f | Interruzione immediata e svuotamento coda altoparlante su ricezione di `<IGNORE_TURN>` |
| `robopy_controller/robot_ai/orchestration/orchestrator.py` | FIX-12g | Rimossa sottoscrizione duplicata con re-inoltro a `play_raw_pcm` |
| `scripts/restart_vui_ai.sh` / `restart_hailo.sh` | FIX-12h | `playback_volume` fissato a `0.10` nei parametri di lancio |


