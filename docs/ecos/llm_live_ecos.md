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


