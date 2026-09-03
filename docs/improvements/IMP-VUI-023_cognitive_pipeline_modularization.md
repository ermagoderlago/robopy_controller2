# 🛠️ Progetto di Miglioramento IMP-VUI-023
> **Target Failure Mode:** `FM-VUI-023` (Contesa del GIL e Latenza Audio da Monolite `llm_service.py`)  
> **Priorità RPN Iniziale:** 392 -> **RPN Residuo:** 14 | **Stato:** COMPLETED | **Dominio:** Audio & VUI (Cognitive Pipeline)

---

## 1. Analisi del Problema & Cause Radice

### Problema
Lo script `llm_service.py` aveva superato i 46 KB di codice monolitico, raggruppando all'interno di un unico processo la connessione WebSocket alla Gemini Live API, il ring buffer FIFO PCM 16kHz, la logica di turn-taking, la cancellazione dell'eco software (AEC) e il dispatching sincrono/asincrono delle skill. Questa concentrazione causava contesa frequente sul Global Interpreter Lock (GIL) di Python, provocando jitter nella trasmissione dei frame audio e rendendo impossibile la cancellazione/preemption pulita delle skill di lunga durata.

### Soluzione Implementata (Smembramento in 3 Sottostrutture)
1. **`live_connection_bridge_node` (`robopy_controller/robot_ai/services/live_connection_bridge_node.py`):**
   - Nodo ROS 2 leggero e dedicato unicamente alla gestione a bassissima latenza del socket WebSocket bidi-streaming della Gemini Live API (`gemini-2.5-flash-native-audio-latest`).
   - Sottoscrizioni dedicate e disaccoppiate dai carichi di calcolo dell'orchestratore.
2. **`audio_buffer_manager` (`robopy_controller/robot_ai/services/audio_buffer_manager.py`):**
   - Modulo isolato thread-safe per il buffering FIFO di chunk audio PCM grezzi a 16kHz mono int16.
   - Calcolo RMS real-time, Acoustic Echo Suppression (AES) che scarta il microfono durante la riproduzione dello speaker, e barge-in detection reattivo (svuotamento istantaneo del buffer speaker quando l'utente parla).
3. **`skill_action_server` (`robopy_controller/robot_ai/orchestration/skill_action_server.py`):**
   - ROS 2 Action Server asincrono nativo con supporto a feedback periodico in streaming (es. `search_skill.py`) e cancellazione/preemption immediata (`cancel_goal()`) con frenata d'emergenza del robot.

---

## 2. File Modificati & Creati
- `robopy_controller/robot_ai/services/audio_buffer_manager.py` [NEW]
- `robopy_controller/robot_ai/orchestration/skill_action_server.py` [NEW]
- `robopy_controller/robot_ai/services/live_connection_bridge_node.py` [NEW]
- `robopy_controller/robot_ai/services/llm_service.py` [MODIFY]
- `robopy_controller/robot_ai/services/__init__.py` [MODIFY]
- `setup.py` [MODIFY]
- `test/unit/test_cognitive_pipeline_split.py` [NEW - 7/7 PASS]
