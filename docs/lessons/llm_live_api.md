# Lezioni Apprese - LLM & Live API (bidiGenerateContent)

Questo documento documenta la gestione del bidi-streaming vocale, la latenza dei WebSocket e la stabilità di rete per la Live API di Gemini.

---

## 🔌 Gestione del WebSocket e Stabilità di Rete

### Turn 1 Closed (Errore 1008)
* **Problema:** La sessione si chiudeva dopo una singola risposta.
* **Causa:** Modelli non supportati per il bidi-streaming all'endpoint beta (es. `gemini-2.5-flash` su endpoint testuale).
* **Risoluzione:** Vincolare la Live API esclusivamente a modelli esplicitamente testati e compatibili con il bidi-streaming vocale, in particolare **Gemini 2.5 Flash Native Audio** (`gemini-2.5-flash-native-audio-latest`).

### Errore 'language_codes'
* **Problema:** Connessione fallita a causa di parametri non supportati nell'SDK.
* **Causa:** L'SDK `google-genai` (v1.x) rifiuta `language_codes=["it-IT"]` dentro `AudioTranscriptionConfig`.
* **Risoluzione:** Rimuovere il parametro lasciandolo vuoto. Gemini rileva automaticamente la lingua parlata e risponde in italiano.

### Prevenzione dei Blocchi Zombie su WebSocket (try...finally)
* **Problema:** Quando il WebSocket si disconnetteva pulitamente (code 1000), il connection manager rimaneva bloccato credendo la sessione ancora attiva.
* **Risoluzione:** Avvolgere il ciclo di ricezione asincrono in un blocco `try...finally` per azzerare sempre `self._live_session = None` e forzare la riconnessione automatica.

---

## 🗣️ Turn-Taking, Barge-In e Gestione Code Audio

### Auto-Interruzione Post-activity_end
* **Problema:** Marcus riceveva la wake word e iniziava l'elaborazione, ma veniva immediatamente interrotto con `🤫 [Live] Interruzione rilevata dal server!` prima di rispondere.
* **Causa:** Il VUI continuava a inviare audio residuo o rumore che Gemini interpretava come un nuovo `activity_start`, interrompendo l'elaborazione del turno precedente.
* **Risoluzione:** Introdurre un flag `_turn_in_progress`. Impostarlo a `True` dopo `activity_end` per bloccare l'invio di ulteriori `activity_start`. Resettarlo a `False` solo al ricevimento di `turn_complete` o `interrupted`.

### Audio Mixing ("Parole che si mischiano") e Flooding Log (OOM)
* **Problema 1:** Warning a 20Hz: `⚠️ OOM Prevention: Coda PCM piena. Scarto il chunk più vecchio.` se la Live API si disconnetteva ma il microfono continuava a inviare dati.
* **Problema 2:** All'avvio del turno successivo, Gemini riceveva 3 secondi di rumore ambientale accumulati in coda, confondendo la risposta.
* **Risoluzione:**
  1. Aggiungere il controllo `if not self._live_session or self._turn_in_progress: return` all'inizio del callback di enqueueing.
  2. Se non c'è una sessione attiva o se il robot sta elaborando/parlando, i chunk in ingresso dal microfono vengono scartati all'istante, eliminando il flooding dei log e la saturazione della coda con rumore stantio.

---

## 🤖 Scelta Modelli, Auto-Discovery e Strategia Fallback (FM-VUI-009)

### Modelli e Auto-Discovery Dinamica
1. **Gemini Live Native Audio** (`gemini-2.5-flash-native-audio-preview-12-2025` / `gemini-2.0-flash-exp`): **OBBLIGATORIO per AUDIO/LIVE**.
2. **Gemini Flash Standard** (`gemini-2.0-flash` / `gemini-1.5-flash`): **PREDEFINITO per CHAT/TESTO/RAG**.
* **Diagnostica & Auto-Discovery (`scripts/test_gemini_models.py`):** Per prevenire blocchi dovuti alla deprecazione o al rinominamento dei modelli da parte di Google Cloud, utilizzare lo script autonomo:
  ```bash
  python3 scripts/test_gemini_models.py --update-env
  ```
  Lo script interroga `client.models.list()`, valida la bidi-streaming Live API con `response_modalities=["AUDIO"]` e seleziona il modello funzionante migliore aggiornando il file `.env`.

### Strategia Fallback Locale su Qwen (NPU Hailo-10H) & Quota Exhausted Failover (FM-LLM-005)
* **Diagnosi del Guasto Storico:** Quando il progetto superava lo spending cap mensile o esauriva la quota Gemini (`429 RESOURCE_EXHAUSTED`), il client andava in retry loop con delay fino a 18s e poi falliva su `gemini-2.0-flash` (deprecato con `404 NOT_FOUND`), causando il timeout di 20s e la risposta rigida *"Non riesco a connettermi al cervello."*
* **Risoluzione Implementata:**
  1. `llm_circuit_breaker.py`: introdotto fail-fast immediato su stringhe `spending cap`, `RESOURCE_EXHAUSTED` e `429` (zero retry lenti cloud).
  2. `conversation.py`: `ConversationManager` intercetta immediatamente l'eccezione ed esegue il failover trasparente su `HailoQwenClient` in NPU, sintetizzando la risposta via TTS e pubblicandola su `/ai/conversation/response`.
  3. `restart_hailo.sh` / `respeaker_vui_node.py`: ridotto `wakeword_sensitivity` da 0.95 a 0.75, `listen_timeout_sec` da 20.0s a 6.0s e `stt_gain` da 2.2x a 1.3x con tetto sessione a 12s, eliminando il loop di riattivazione continuo innescato dalla TV o rumori di fondo.

---

## 🔧 Validazione Strutturata dei Tool (SDK v1.x)

* **Problema:** Errore `Extra inputs are not permitted` per `tools.0.Tool.name` o `tools.0.callable` dovuto alla validazione Pydantic rigida nell'SDK `google.genai` (v1.x).
* **Risoluzione:** Implementare una funzione helper `_format_tools_for_google` per ripulire i dizionari dei tool custom del sistema Marcus, rimuovendo campi extra (come `callable`) e incapsulandoli in oggetti `types.FunctionDeclaration` dentro `types.Tool(function_declarations=[...])`.

---

## 🎙️ Prevenzione della Sovrapposizione Audio (Double Voice / Double LLM Calls)
* **Problema:** Quando si avviava una sessione vocale, si udivano due voci sovrapposte che rispondevano contemporaneamente (la voce fluida nativa di Gemini Live e la voce robotica standard di gTTS).
* **Causa:** La Live API genera e riproduce autonomamente l'audio tramite il bidi WebSocket. Tuttavia, `conversation.py` riceve anche la trascrizione testuale finale e la passava a `self.tts.speak(...)` generando una seconda istanza di riproduzione vocale locale.
* **Soluzione:** Introdurre un flag `is_live` per tracciare se il turno è stato risposto con successo dalla Live API. Inibire qualsiasi chiamata locale a `self.tts.speak(...)` in `conversation.py` se `is_live` è `True`. Se la Live API fallisce o scatta il fallback standard su REST, `is_live` rimane `False` e la sintesi vocale locale gTTS viene regolarmente utilizzata.
* **Integrazione Client-Side ASR (Doppia Chiamata / Doppia Voce):**
  * **Problema:** Se l'utente usa un client con ASR integrata (es. Foxglove o web app), il client transceve e invia la trascrizione sul topic `/ai/input/text` (con `source="text"`), provocando comunque la doppia risposta (quella nativa Live e quella REST locale).
  * **Soluzione [v15.2]:** Memorizzare le trascrizioni recenti utente della Live API in `LiveConnectionManager.recent_user_transcripts`. In `ConversationManager.process_input`, se arriva un input di testo che corrisponde (o è substring) a una delle trascrizioni Live recenti (finestra di 15 secondi) o in corso, l'input testuale duplicato viene scartato silenziosamente.
  * **Rimozione Filtro Temporale time_since_mic per Chat Testuale [v15.4]:**
    - **Problema:** Il controllo `time_since_mic < 3.5s` scartava il 100% dei messaggi digitati in chat dall'utente in presenza del microfono attivo in background, poiché `_last_mic_audio_time` veniva aggiornato continuamente dal flusso PCM. La risposta su `/ai/conversation/response` non veniva quindi mai pubblicata (o solo dopo tentativi se il mic andava in idle).
    - **Soluzione:** Rimosso il controllo indiscriminato su `time_since_mic < 3.5s` mantenendo esclusivamente la verifica di duplicazione reale `is_duplicate_text` sulle trascrizioni effettive. Ora la chat testuale riceve risposte immediate e consistenti ad ogni invio.
  * **Silenziamento Conversazione da Chat [v15.3]:**
    - Per evitare che Marcus risponda a voce quando l'utente comunica via tastiera/chat, si effettua un wrapping dinamico di `self.tts.speak` in `ConversationManager.__init__`. Se `_current_source == "text"`, il parlato locale gTTS viene soppresso loggando l'evento `[MUTE]`.

---

## 🔊 Ottimizzazione Streaming Audio PCM e Sequenzialità

### Jitter e Out-of-Order sotto MultiThreadedExecutor [v15.3]
* **Problema:** Lo streaming vocale di Gemini Live partiva correttamente, per poi arricchirsi di parole distorte e sovrapposte, rendendo l'audio del robot incomprensibile.
* **Causa:** Il nodo AI e l'orchestratore sono eseguiti all'interno di un `MultiThreadedExecutor` a 4 thread per ragioni di efficienza asincrona. I chunk audio ricevuti da Gemini venivano pubblicati via ROS su `/ai/conversation/audio_chunk`. A causa della concorrenza del pool di thread dell'esecutore, i callback di ricezione venivano eseguiti in parallelo o fuori ordine (es. il chunk 2 veniva spedito sul bus `/respeaker/speaker_audio` prima del chunk 1), provocando sfasamenti e scatti del tasso di campionamento (resampling state corrotto).
* **Risoluzione (Bypass ROS via Callback Diretta):**
  - Eliminare il passaggio intermedio via topic ROS `/ai/conversation/audio_chunk` per lo streaming vocale.
  - Implementare una callback Python sincrona e diretta `register_audio_callback` tra `LLMServiceNode` e `AIOrchestrator`.
  - Poiché il loop di ricezione WebSocket di Gemini gira sul thread a ciclo singolo asincrono di asyncio, la callback diretta inoltra i chunk in ordine cronologico perfetto e sequenziale. La pubblicazione finale su `/respeaker/speaker_audio` avviene così in modo strettamente ordinato, ripristinando la pulizia assoluta dell'audio in cuffia/altoparlante.

---

## 🧩 Smembramento Architetturale Monolite `llm_service.py` (FM-VUI-023)

### 1. Scomposizione Modulare
* **Problema:** Il monolite originario (>46 KB) concentrava socket WebSocket, ring buffer FIFO, turn-taking, software AEC e skill execution nello stesso thread/processo, causando contesa del GIL e audio glitch.
* **Architettura a 3 Sottostrutture:**
  1. **`live_connection_bridge_node` (`robopy_controller/robot_ai/services/live_connection_bridge_node.py`):** Nodo leggero e focalizzato unicamente sul ciclo di vita del WebSocket asincrono bidi-streaming di Gemini Live.
  2. **`audio_buffer_manager` (`robopy_controller/robot_ai/services/audio_buffer_manager.py`):** Modulo thread-safe isolato con calcolo RMS continuo, Acoustic Echo Suppression (scarto dei frame mic a bassa energia durante il parlato dello speaker) e barge-in reattivo (flushing istantaneo del buffer speaker su interruzione vocale).
  3. **`skill_action_server` (`robopy_controller/robot_ai/orchestration/skill_action_server.py`):** Server di azioni asincrono per l'orchestrazione delle skill con feedback di avanzamento in streaming continuo (es. stanze esplorate in `search_skill`) e gestione nativa della preemption con blocco immediato della navigazione.





---

## ⚖️ Milestone 2: Cognitive Intent & Fast-Path Safety Arbiter (FM-NAV-034, FM-NAV-035)

### 1. Fast-Path Emergency Stop (<10ms Hardware Bypass vs 1.5-3.0s Cloud Delay)
* **Problema:** Quando l'utente pronunciava un comando di stop d'emergenza ("fermati", "stop", "alt", "basta"), il flusso transitava sul WebSocket cloud di Gemini Live, introducendo una latenza inaccettabile di 1.5-3.0 secondi prima che il tool call azzerasse i motori.
* **Risoluzione:** Implementato `FastPathStopHandler` in `robopy_controller/robot_ai/arbitration/fast_path_stop.py`:
  - Regex precompilata `STOP_REGEX` su token/parziali ASR con intercettazione in **~2-15 µs**.
  - Pubblicazione istantanea di `Twist(0,0)` sul topic ad altissima priorità `/cmd_vel_mux/input/safety_override` e di `Bool(True)` su `/ai/conversation/interrupt`.
  - Supporto trasparente per ambienti ROS 2 attivi e modalità mock/standalone senza `rclpy`.
  - Tempo totale di dispatch: **< 2.0 ms**, ampiamente sotto il budget vincolante di **< 10.0 ms**.

### 2. Cognitive Intent Classifier (<1.5ms, 100% Accuratezza su 100 Prompt)
* **Problema:** Separare deterministicamente richieste operative (destinate a Qwen2 su NPU Hailo-10H per moto, navigazione, telemetria, domotica) e richieste conversazionali (destinate a Gemini Live Cloud) senza ricorrere a un LLM classificatore lento.
* **Risoluzione:** Implementato `CognitiveIntentClassifier` in `robopy_controller/robot_ai/arbitration/intent_classifier.py`:
  - Motore multi-pattern a due tier con scoring su categorie operative e conversazionali.
  - Testato sul dataset ufficiale di 100 prompt del benchmark (50 operativi, 50 conversazionali).
  - Risultato test gate: **100.00% di accuratezza** (superando la soglia richiesta del 96%).
  - Latenza media: **~26.1 µs**; latenza al 99° percentile (p99): **42.4 µs** (budget: < 1500 µs).

### 3. Connectivity Arbiter & Failover Locale (<100ms)
* **Problema:** Quando la latenza verso i server Google cresce oltre 500 ms o cade la connessione WAN, il robot rimane muto o in stallo.
* **Risoluzione:** Implementato `ConnectivityArbiter` in `robopy_controller/robot_ai/arbitration/connectivity_arbiter.py`:
  - Stima esponenziale della latenza: $EMA_{new} = 0.7 \cdot EMA_{old} + 0.3 \cdot sample$.
  - Stati operativi: `ONLINE` ($\le 250\text{ ms}$), `DEGRADED` ($250-500\text{ ms}$), `OFFLINE_LOCAL` ($> 500\text{ ms}$ o caduta socket).
  - Tasto di failover istantaneo (< 100 ms, misurato a < 5 µs): instrada al 100% su Qwen2 locale on-device.

### 4. Master Hybrid Router
* **Architettura:** `HybridRouter` in `robopy_controller/robot_ai/arbitration/hybrid_router.py` coordina i tre componenti offrendo un'interfaccia unificata `route(text: str, context: Optional[dict] = None) -> RoutingDecision`.
* **Verifica:** Test unitari completi e benchmark in `tests/test_intent_arbiter.py` (20/20 test superati).
