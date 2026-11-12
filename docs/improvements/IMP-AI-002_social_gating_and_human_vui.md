# Progetto di Miglioramento: Percezione Sociale, Memoria Condivisa TRINITY & Comportamento Vocale Naturale (IMP-AI-002)

## Identificazione
- **ID Progetto:** `IMP-AI-002`
- **Failure Modes Correlati:** `FM-COG-008`, `FM-VIS-014`
- **Dominio:** `cognitive_ai` / `audio_vui` / `vision_hailo`
- **Componenti Interessati:** `trinity_engine.py`, `skill_executor.py`, `conversation.py`, `orchestrator.py`, `cognitive_core_node.py`, `respeaker_vui_node.py`, `tts_service.py`, `dsp_pipeline.py`
- **Stato:** `MITIGATED` (Verificato via suite PC `pytest`; in attesa collaudo live sensori/NPU/USB su Raspberry Pi 5)

---

## Obiettivo
Portare Marcus a comportarsi come un compagno umano coerente e contestuale: memoria unificata tra voce cloud (Gemini Live) e voce locale (Qwen/TRINITY), proattività filtrata dallo stato sociale e dall'attenzione dell'utente, risposte rapide `<300ms` durante tool lunghi, voce offline naturale con Piper TTS, esecuzione reale di tutte le 10 skill dichiarate e interrogazione visiva VLM conforme alla guardia termica `SPEC-03`.

---

## Mitigazioni Implementate (P1.8, P2.3-P2.4, P3, P4)
1. **Memoria Unificata Live <-> Qwen (`trinity_engine.py`, `live_connection_manager.py`, `llm_service.py`):**
   - Aggiunto `TrinityEngine.build_live_context_digest(max_tokens=2500)` iniettato al connect di `LiveConnectionManager` per condividere fatti semantici, ultimi episodi e stato stanza senza sforare il budget prompt.
   - Aggiunto `TrinityEngine.record_live_turn_sync(...)` invocato da Gemini Live su `turn_complete`, rendendo subito visibili a Qwen/TRINITY i turni sostenuti su Live (e viceversa).
2. **Copertura Completa 10 Skill + Risultato Reale Parlato (`skill_executor.py`, `conversation.py`):**
   - Esteso il mapping di `SkillExecutor` a tutte le 10 `ALLOWED_FUNCTION_NAMES` (`query_knowledge`, `set_smart_device`, `pause_or_resume_exploration`, `get_battery_status`, `get_system_health` oltre a navigazione/esplorazione).
   - `ConversationManager._handle_local_qwen` cattura e vocalizza i risultati reali restituiti dai tool/telemetria anziché scartare `await res`.
3. **Percezione Sociale & Proattività in Coda (`orchestrator.py`, `respeaker_vui_node.py`, `cognitive_core_node.py`, `dialogue_arbiter.py`):**
   - Sottoscrizioni attive a `/engagement/status`, `/marcus/social_context/tom_state`, `/identity/active_person` e `/ai/live/session_active`.
   - Rimozione del bypass cieco `/ai/vui/proactive_listen = True` da `cognitive_core_node`; tutta la proattività transita su `/ai/conversation/proactive_speech` in coda ordinata su `DialogueArbiter` (sospesa se pavimento occupato, `DISENGAGED` prolungato, o `DO_NOT_DISTURB` / `REST`, con attenuazione volume TTS `0.65x` in modalità discreta).
   - Follow-up post-risposta aperto solo se presenza/impegno sociale lo giustificano.
4. **Comportamento Vocale Umano (`tts_service.py`, `respeaker_vui_node.py`, `orchestrator.py`, `dsp_pipeline.py`):**
   - **Piper TTS Offline Primario:** `TTSService` utilizza `PiperTTSClient` (`16kHz -> 24kHz` resampling lineare + scaling volume) come backend offline prioritario prima del fallback online `gTTS`, pubblicando stabilmente `/ai/tts/speaking` `True`/`False` anche sui chunk `play_raw_pcm`.
   - **Backchannel < 300ms & Barge-In Sostenuto (>= 250ms):** Prima di tool lunghi (`navigate_to_pose`, `ask_visual_question`, `search_object`, ecc.) viene emesso un breve feedback naturale entro cooldown dedicato; durante il TTS, `TurnManager.process_chunk` resta valutato e interrompe l'audio solo su parlato umano sostenuto `>= 0.25s`.
   - **Noise Floor DSP (`dsp_pipeline.py`):** Soglia minima elevata da `60.0` a `120.0` RMS per evitare che AGC amplifichi ronzio di ventole/TV nel VAD.
5. **Allineamento VLM & Affettività Unificata (`conversation.py`, `orchestrator.py`, `cognitive_core_node.py`):**
   - Corretto nome servizio ROS 2 VLM in `/hailo/vlm/ask_question` con guardia termica `< 80.0 C` (`SPEC-03` Zona Gialla) e rimossa la collisione del publisher `String` `pub_hailo_vlm` in `orchestrator.py`.
   - Stati affettivi/umorali unificati su `/ai/affective_state`.
6. **Verifica P3.2 & Fattibilità P3.3 (ARM64 Pi 5):**
   - Verificato ordinamento causale `DSPPipeline` (HPF 140Hz + Soft Noise Gate 120 RMS + AGC) -> `TurnManager` -> VAD/KWS in `respeaker_vui_node.py`.
   - Documentato lo stato di `TurnManager` su ARM64 (`HAS_ONNX` disabilitato in assenza di `onnxruntime`/modello `silero_vad.onnx`, con fallback attivo su energy+hangover gating) e la fattibilità hardware DoA su **ReSpeaker Lite (XMOS XU316, 2 mic)**: richiede verifica fisica su Pi 5 dei descrittori USB HID/vendor control (`pyusb`) rispetto alle API storiche del 4-mic XVF3000.
