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
