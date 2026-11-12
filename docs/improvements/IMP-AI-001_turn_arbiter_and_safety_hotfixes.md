# Progetto di Miglioramento: Arbitro Unico di Turno (`DialogueArbiter`), Tracciabilità E2E e Hotfix di Sicurezza (IMP-AI-001)

## Identificazione
- **ID Progetto:** `IMP-AI-001`
- **Failure Modes Correlati:** `FM-LLM-021`, `FM-LLM-022`, `FM-VUI-054`
- **Dominio:** `cognitive_ai` / `audio_vui`
- **Componenti Interessati:** `dialogue_trace.py`, `dialogue_arbiter.py`, `hailo_qwen_client.py`, `intent_classifier.py`, `live_connection_manager.py`, `llm_service.py`, `conversation.py`, `respeaker_vui_node.py`, `orchestrator.py`, `attention_supervisor_node.py`, `respeaker_interface_node.py`, `slm_metaprompt_fusion.py`, `metaprompt_fusion.py`
- **Stato:** `MITIGATED` (Verificato su PC/pytest; in attesa validazione hardware live su Raspberry Pi 5 per P0.3 TV soak e ReSpeaker USB/Hailo NPU)

---

## Obiettivo
Risolvere definitivamente le risposte doppie sovrapposte Gemini Live + Local Qwen, le attivazioni su rumori ambientali/TV e parziali ASR, le risposte senza senso / allucinazioni da prompt e le race condition su `/ai/input/mic_mute`, introducendo al contempo tracciabilità end-to-end per ogni turno conversazionale (`turn_id`).

---

## Cause Radici Individuate
1. **Assenza di Arbitraggio Turno & Doppia Pipeline Vocale:** `ReSpeakerVUINode` pubblicava trascrizioni su `/ai/input/voice_command` (innescando `ConversationManager` + `HailoQwenClient` + `TTS`) mentre `LiveConnectionManager` trasmetteva lo stesso audio in streaming a Gemini Live.
2. **Clock Skew `time.time()` vs `time.monotonic()` & Finestra Auto-Rinnovante:** `llm_service.py` assegnava `time.time()` (~`1.79e9`) a `last_successful_turn_time`, mentre `LiveConnectionManager` lo sottraeva da `time.monotonic()`, ottenendo delta negativi sempre `< 25s` e bloccando aperta all'infinito la finestra vocale. Inoltre `on_audio_chunk` e `turn_complete` riarmavano continuamente il timer.
3. **Mock Qwen Attivo in Produzione con Sintesi Motoria (`SPEC-03`/`SPEC-05` Zona Rossa):** `HailoQwenConfig.enable_mock_fallback` era `True` di default e `_synthesize_mock_tokens` generava JSON `<action>` per movimenti fisici (`rotate_angle`, `move_distance`, `navigate_to`, `start_frontier_exploration`).
4. **Wakeword & Enrollment Parziali / Non Gated:** In standby (`not _ev_listening`), ipotesi parziali Vosk e frasi accidentali (`"io sono ..."`) aprivano la finestra microfono e lanciavano il face enrollment.
5. **Allucinazioni Statiche da Prompt & Regex a Sottostringa:** `llm_service.py` iniettava al mattino un prompt poetico sul *"sogno"*; `slm_metaprompt_fusion.py` iniettava finte email Amazon/VIP di test e `GROUNDED_FAMILY_FACTS` non richiesti; `intent_classifier.py` usava sottostringhe (`"che"`, `"dov"`) che intercettavano `"perché"`, `"dovrei"`, `"che bello"`.
6. **Multi-Publisher `/ai/input/mic_mute` & Svuotamento Distruttivo Coda TTS:** Quattro nodi pubblicavano direttamente su `/ai/input/mic_mute`; inoltre `_mic_mute_cb(True)` svuotava `_audio_out_queue`, troncando le risposte TTS legittime.

---

## Mitigazioni Implementate (P0, P1, P2.1-P2.2, P2.5)
1. **Osservabilità `turn_id` e Trace JSONL (`dialogue_trace.py`):** Log strutturato rotante in `logs/dialogue_trace.jsonl` (max 5 MB + ring buffer 250 eventi) con contatori atomici (`turns_total`, `double_response_blocked`, `double_response_count`, `out_of_window_turns`, `mock_activations`, `trinity_timeouts`, `proactive_suppressed`, `backchannels_emitted`).
2. **Disattivazione Mock Qwen in Produzione & Rimozione `<action>` Sintetiche (`hailo_qwen_client.py`):** `enable_mock_fallback` impostato a `MARCUS_QWEN_ALLOW_MOCK == "1"` (`False` di default), eliminato qualsiasi tag `<action>` motorio/navigazionale dal mock, aggiunto evento trace `mock_activation`, e risolto bug concorrente di telemetria in `generate()`.
3. **Arbitro In-Process `DialogueArbiter` (`dialogue_arbiter.py`, `orchestrator.py`, `respeaker_vui_node.py`):** Implementa gerarchia **Opzione A** (`ONLINE` -> Gemini Live proprietario della voce; `DEGRADED`/`OFFLINE_LOCAL` o Fast-Path Stop `<10ms` -> Qwen locale) e FSM esclusiva sul pavimento (`FREE`, `USER_SPEAKING`, `LIVE_SPEAKING`, `QWEN_SPEAKING`, `PROACTIVE_SPEAKING`, `EMERGENCY_STOPPING`) con scadenza automatica dei lease orfani.
4. **Correzione Orologio & Finestra Non Auto-Rinnovante (`llm_service.py`, `live_connection_manager.py`):** Uso uniforme di `time.monotonic()` con guardia contro delta negativi; `followup_window_sec = 8.0` ancorata a `_last_ai_speech_end_time` senza auto-estensione su nuovi turni; `<IGNORE_TURN>` sopprime il turno senza estendere la finestra; logging in `recent_user_transcripts` solo a sessione realmente attiva.
5. **Igiene Wakeword, Enrollment, Grounding e Intent Regex (`respeaker_vui_node.py`, `slm_metaprompt_fusion.py`, `metaprompt_fusion.py`, `intent_classifier.py`):**
   - Standby wakeword accetta solo risultati Vosk `FINAL`; enrollment consentito solo a ascolto già aperto o con wakeword esplicita.
   - Rimozione `dream_greeting` mattutino, rimozione finte email Amazon hardcoded, iniezione condizionale `GROUNDED_FAMILY_FACTS`, `MetapromptFusion.BUDGET_SYSTEM = 450` (`<= 2500` budget totale).
   - Introduzione regex a parola intera `RE_SITUATIONAL_OPERATIONAL` con `\b` in `intent_classifier.py`.
6. **Arbitraggio Centrale `/ai/input/mic_mute` & Decoupling Mute/Flush (`orchestrator.py`, `respeaker_vui_node.py`, `attention_supervisor_node.py`, `respeaker_interface_node.py`):** Richieste convogliate su `/ai/input/mic_mute_request` verso l'unico publisher autorevole in `AIOrchestrator` con priorità per owner (`EMERGENCY`, `TTS_ECHO`, `MOVEMENT`, `SYSTEM`); `_mic_mute_cb(True)` non svuota più `_audio_out_queue` (riservando il flush a barge-in / interrupt).
