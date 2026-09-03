# Engineering Change Orders - LLM & Live API

Questo documento traccia la cronologia delle modifiche ingegneristiche (ECO) apportate al modulo LLM, alla Live API WebSocket e ai servizi cognitivi basati su cloud di Marcus.

---

## 📈 ECO-2026-05-25-002: Gemini Live API Best Practices Compliance
* **Stato:** ✅ **Completato, Modificato e Sincronizzato**
* **Descrizione:** Allineamento del sistema vocale alle linee guida ufficiali **Google Gemini Live API Best Practices**: gestione avanzata delle interruzioni con barge-in server-side e inibizione del buffer audio, prevenzione delle derive linguistiche in bidi-streaming.
* **Modifiche apportate:**
  * Sottoscritto il topic `/ai/conversation/interrupt` (`std_msgs/Bool`) in `respeaker_vui_node.py` per svuotare all'istante la coda di riproduzione audio `self._audio_out_queue` all'intercettazione dell'interruzione.
  * Aggiornato l'algoritmo di composizione del prompt `_get_active_system_prompt()` in `llm_service.py` per inserire le direttive rigide: `RESPOND IN ITALIAN. YOU MUST RESPOND UNMISTAKABLY IN ITALIAN.`.
  * In `live_connection_manager.py` (precedentemente `llm_live_api.py`), intercettato il flag `msg.server_content.interrupted == True` inviato da Gemini per svuotare i buffer temporanei e le trascrizioni parziali del turno interrotto, pubblicando il segnale di stop su `/ai/conversation/interrupt`.

---

## 📈 ECO-2026-05-31-001: EmailSkill Synchronous Refactoring
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato**
* **Descrizione:** Refactoring strutturale di `EmailSkill.execute` da `AsyncGenerator` a coroutine standard a ritorno singolo (`SkillResult`) per eliminare l'uso di notifiche offline TTS sgradevoli ed abilitare la lettura nativa tramite Gemini Live.
* **Modifiche apportate:**
  * Modificata la firma di `execute` per restituire `SkillResult` direttamente.
  * Rimossi tutti i `yield` intermedi che provocavano disconnessioni di sessione o forzavano l'offline TTS robotico.
  * Gemini Live riceve ora il `SkillResult` serializzato come risposta asincrona alla chiamata del tool e lo sintetizza nativamente con la sua voce.

---

## 📈 ECO-2026-07-03-001: Prevenzione Double-Voice in Conversazioni Vocali (Bidi Live)
* **Stato:** ✅ **Completato, Modificato Localmente**
* **Descrizione:** Risoluzione del problema delle voci sovrapposte (doppia chiamata standard LLM + Live API) durante i turni ad attivazione vocale.
* **Modifiche apportate:**
  * Modificato `conversation.py` per introdurre la variabile di tracciamento `is_live`.
  * Se la chiamata a `self.llm.generate_live(...)` ha successo, `is_live` viene impostato a `True` per segnalare che la Live API WebSocket sta gestendo nativamente l'audio in tempo reale.
  * Inibite tutte le chiamate locali a `self.tts.speak(...)` (sintesi vocale locale gTTS) all'interno di `conversation.py` (sia per il testo di risposta che per i feedback delle skill) quando `is_live` è attivo.

---

## 📈 ECO-2026-07-18-001: Soppressione dell'input di testo duplicato (Client-Side ASR Double-Voice)
* **Stato:** ✅ **Completato e Sincronizzato**
* **Descrizione:** Risoluzione del problema della doppia voce causata da client esterni (es. Foxglove o Web UI) che trascrivono l'audio localmente e inviano il testo a `/ai/input/text` in parallelo allo streaming audio nativo della Live API.
* **Modifiche apportate:**
  * Modificato `live_connection_manager.py` per inizializzare e popolare `self.recent_user_transcripts` con timestamp relativi all'arrivo delle trascrizioni (parziali e finali) del parlato utente dalla Live API.
  * Aggiunto il metodo `is_duplicate_text(text)` in `LiveConnectionManager` e bridge in `LLMServiceNode` per verificare la presenza di corrispondenze semantiche e substring nei 15 secondi precedenti.
  * Aggiornato `ConversationManager.process_input` in `conversation.py` per scartare silenziosamente i messaggi di testo duplicati rilevati.

---

## 📈 ECO-2026-07-19-001: Silenziamento Chat e Ottimizzazione Stream Audio Sequenziale Gemini Live
* **Stato:** ✅ **Completato e Sincronizzato**
* **Descrizione:** Eliminazione vocale locale per interazioni testuali (chat), introduzione di filtri temporali anti-allucinazione ASR e ottimizzazione dello streaming audio real-time per risolvere gli artefatti audio dovuti a concorrenza MultiThreadedExecutor.
* **Modifiche apportate:**
  * In `conversation.py`, implementato il muting vocale automatico se l'input proviene da tastiera (`source == "text"`), avvolgendo dinamicamente `tts.speak`.
  * In `conversation.py`, esteso il filtro duplicati ASR con un controllo temporale a soglia (<3.5 secondi dall'ultimo chunk audio microfonico registrato in `LiveConnectionManager._last_mic_audio_time`), prevenendo la doppia voce in caso di errori di lingua/allucinazione dell'ASR cloud.
  * In `llm_service.py` e `orchestrator.py`, introdotto il meccanismo `_direct_audio_chunk_callback` in Python per bypassare il topic ROS `/ai/conversation/audio_chunk`. Questo costringe lo stream di chunk audio di Gemini Live a essere instradato verso il trasmettitore in modo strettamente sequenziale, prevenendo il riordinamento (jitter) da esecuzione multi-threaded e ripulendo completamente l'audio riprodotto.

---

## 📈 ECO-2026-08-02-001: Diagnostica Modelli Gemini, Auto-Discovery e Fallback Locale Qwen NPU
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato**
* **Descrizione:** Introduzione dello script autonomo di diagnostica modelli Gemini (`scripts/test_gemini_models.py`), auto-discovery del modello `gemini-2.5-flash-native-audio-preview-12-2025` con risposta audio nativa e meccanismo di fallback automatico su Qwen2-VL (Hailo NPU) in caso di inaccessibilità/deprecazione dei modelli Cloud.
* **Modifiche apportate:**
  * Creato `scripts/test_gemini_models.py` per l'auto-discovery dinamica via `client.models.list()` ed il test di bidi-streaming con `response_modalities=["AUDIO"]`, con supporto al flag `--update-env` per la persistenza in `/mnt/ssd/robopy_controller_host/.env`.
  * Aggiornato `llm_service.py` per leggere `LIVE_MODEL_NAME` e `MODEL_NAME` in priorità dal file `.env`, impostando di default il modello verificato `gemini-2.5-flash-native-audio-preview-12-2025`.
  * In `live_connection_manager.py`, `llm_service.py` ed `orchestrator.py`, introdotto il callback `on_fallback_needed` ed il topic `/ai/live/fallback` per rilevare errori 404/1008 o disconnessioni Gemini, avvisando l'utente via TTS locale (*"Gemini non è al momento raggiungibile..."*) e dirottando le richieste dell'utente sull'NPU locale via `/hailo/vlm/ask_question`.
  * Aggiornato `sync_marcus.sh` nello Step 1 (Back-Sync) per sincronizzare il file `.env` dal robot al PC, evitando la sovrascrittura da parte del PC dei modelli auto-scoperti dal robot.

---

## 📈 ECO-2026-08-28-COGNITIVE-PIPELINE-SPLIT: Smembramento Architetturale Monolite `llm_service.py`
* **Stato:** ✅ **Completato in Workspace Locale (Pronto per Deploy)**
* **Descrizione:** Smembramento del monolite `llm_service.py` (>46 KB) in tre sottostrutture indipendenti e disaccoppiate: gestione WebSocket Live bidi-streaming (`live_connection_bridge_node`), gestione buffering audio PCM 16kHz & echo suppression (`audio_buffer_manager`), e action server asincrono per l'orchestrazione delle skill (`skill_action_server`) con streaming feedback e preemption.
* **Modifiche apportate:**
  * Creato `robopy_controller/robot_ai/services/audio_buffer_manager.py`: gestione bounded FIFO deques, calcolo RMS normalized energy, cancellazione software eco e barge-in speech detector.
  * Creato `robopy_controller/robot_ai/orchestration/skill_action_server.py`: Action Server asincrono con tracciamento `GoalStatus`, streaming feedback da generatori asincroni e preemption immediata con stop motore (`nav_client.stop()`).
  * Creato `robopy_controller/robot_ai/services/live_connection_bridge_node.py`: nodo ROS 2 dedicato unicamente alla gestione del socket WebSocket bidi-streaming della Gemini Live API.
  * Refactoring di `robopy_controller/robot_ai/services/llm_service.py`: alleggerito e integrato con `AudioBufferManager` mantenendo piena retrocompatibilità.
  * Registrato `live_connection_bridge_node` in `setup.py` (entry points).
  * Creata test suite completa in `test/unit/test_cognitive_pipeline_split.py` (7/7 PASS).
  * Registrato il Failure Mode `FM-VUI-023` in `fmea/dfmea.yaml` ed elaborato `IMP-VUI-023_cognitive_pipeline_modularization.md`.




