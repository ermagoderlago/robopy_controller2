# Lezioni Apprese - Orchestrazione & RAG (Retrieval-Augmented Generation)

Questo documento descrive le lezioni apprese in merito alla gestione dei servizi del robot, l'integrazione di Home Assistant, l'archiviazione di memorie su database vettoriali e l'allineamento cognitivo dopaminergico.

---

## 🚀 Orchestrazione e Inizializzazione Servizi

### Blocco Startup su Home Assistant
* **Problema:** L'orchestratore non raggiungeva lo stato "READY" se Home Assistant era irraggiungibile o lento all'avvio.
* **Causa:** Chiamata sincrona/sequenziale bloccante `await self.ha_client.connect()` all'interno dell'inizializzazione delle risorse.
* **Risoluzione:** Spostare l'inizializzazione di HA in un `asyncio.create_task` separato in background (non-blocking). Il sistema deve potersi avviare regolarmente anche se HA è temporaneamente offline.

### Errori di Attributo all'Avvio
* **Problema:** Crash immediati su `MemoryStore.initialize()` e `StateMachine.current_state`.
* **Causa:** Metodi rinominati o inesistenti a seguito di refactoring affrettati.
* **Risoluzione:** `MemoryStore` viene inizializzato direttamente nel costruttore; l'accesso allo stato della `StateMachine` deve usare la proprietà `.state`.

### Topic ROS di Risposta Mancanti
* **Problema:** Il topic `/ai/conversation/response` non veniva pubblicato.
* **Causa:** `AIOrchestrator` non definiva il publisher e `ConversationManager` non aveva l'aggancio diretto a ROS per inviare testo.
* **Risoluzione:** Configurare publisher espliciti in `AIOrchestrator` e registrare un sistema di callback asincrono in `ConversationManager`.

---

## 🗄️ RAG e ChromaDB Nativo

### Sostituzione di LlamaIndex con ChromaDB Nativo
* **Causa del refactoring:** Il bridge `RobopyEmbedding` è strettamente asincrono. LlamaIndex, se chiamato con metodi sincroni (`insert`), tentava di eseguire chiamate asincrone bloccando il thread. Inoltre, causava overhead e instabilità di thread su ROS.
* **Soluzione (ChromaNativeStore):** Migrazione a ChromaDB nativo diretto, aggirando completamente LlamaIndex.
* **Thread-Safety:** Poiché il nodo gira in un ROS 2 `MultiThreadedExecutor`, il client di ChromaDB nativo deve essere protetto da un lock globale (`threading.Lock`) e ogni operazione di lettura/scrittura deve usare lock rientranti (`self._lock = threading.RLock()`).
* **Conflitto di Metadati:** Il passaggio a ChromaDB nativo su database pre-esistenti creati da LlamaIndex causa l'eccezione `Inizializzazione ChromaNativeStore fallita: object of type 'int' has no len()`. Svuotare o rinominare la directory del vecchio database (`mv /home/robopy/ChromaDB_Llama /home/robopy/ChromaDB_Llama_backup`) per consentire la creazione di uno schema pulito.
* **Prevenzione Corruzione Spazio Vettoriale:** Validare sempre la dimensione di ogni embedding (es. 768) prima di eseguire `add()`, scartando record incongruenti per evitare corruzioni.

---

## 🧠 Allineamento Dopaminergico e Grafo Cognitivo (RPE)

### Sistema di Allineamento Dopaminergico Asincrono
* **Architettura:** Struttura a grafo asincrono (`cognitive_graph.py`) che modella lo stato dell'agente (`MarcusAgentState`).
* **CriticEvaluatorNode:** Valuta i feedback dell'utente (positivi o negativi) e i fallimenti delle skill ROS 2 per determinare il Reward Prediction Error:
  $$\delta = \text{Feedback} - \text{Expectation}$$
  * Salva le memorie episodiche su ChromaDB in caso di scostamenti significativi ($|RPE| \ge 0.3$).
* **PredictiveRouterNode:** Esegue query vettoriali preventive su ChromaDB per applicare inibizioni sinaptiche ed evitare di ripetere errori passati (es. tentativi falliti in loop su nodi non raggiungibili), modificando temporaneamente il system prompt dell'LLM prima della generazione del turno.

---

## 🔬 Dinamica Sinaptica ed Oblio Bio-Ispirato (Sprint 5)

L'infrastruttura cognitiva di Marcus implementa la curva dell'oblio di Ebbinghaus per ottimizzare lo spazio vettoriale su hardware limitato (4GB RAM) senza creare database ausiliari.

### 1. Metadati di Controllo Sinaptico
Ogni record salvato in ChromaDB è corredato dai seguenti campi di controllo:
* `synaptic_strength` (float): La forza del ricordo (inizializzata a 100.0, ridotta nel tempo).
* `recall_count` (int): Numero di volte in cui il ricordo è stato richiamato o rinforzato.
* `lambda_decay` (float): Il tasso di decadimento temporale specifico del ricordo.
* `amygdala_protected` (string `"true"`/`"false"`): Flag per indicare ricordi immuni all'oblio.

### 2. Equazione del Decadimento
Durante il ciclo notturno `SOGNO`, per ciascun record non protetto viene calcolata la forza sinaptica residua:
$$S(t) = S_0 \cdot e^{-\lambda \cdot \Delta t}$$
dove $\Delta t$ rappresenta il tempo trascorso (in ore) dall'ultimo aggiornamento o creazione del record.

### 3. Potatura Sinaptica (Pruning)
Tutti i record che presentano una forza sinaptica $S(t) < 30.0$ e che sono stati richiamati meno di due volte (`recall_count` < 2) vengono fisicamente eliminati da ChromaDB. Questo processo riduce la frammentazione e previene il crash da esaurimento di memoria RAM.
Dopo la potatura, il sistema invoca un Garbage Collection (`gc.collect()`) forzato per liberare la memoria dell'host.

---

## 🏷️ Identità dell'Acronimo e Ricerca Semantica RAG Attiva (Sprint 6)

### 1. Dichiarazione Residente dell'Acronimo MARCUS
* **Problema:** Marcus non riconosceva o allucinava la definizione del suo nome quando interrogato.
* **Causa:** Il parametro `system_prompt` predefinito in `llm_service.py` non conteneva l'espansione dell'acronimo.
* **Risoluzione:** Registrazione esplicita nel system prompt residente dell'identità:
  `MARCUS — Modular Autonomous Robotic Control Unit System`.

### 2. Recupero Semantico RAG nel Flusso Conversazionale
* **Problema:** Le memorie venivano archiviate su ChromaDB in background ma mai richiamate durante il dialogo.
* **Causa:** `ConversationManager` non eseguiva alcuna query vettoriale prima di generare la risposta dell'LLM.
* **Risoluzione:** Integrazione della chiamata `memory_store.search(clean_text, top_k=3)` in `_process_locked`. I risultati rilevanti (score $\ge 0.40$) vengono iniettati nella sezione `[MEMORIE EPISODICHE E FATTI APPRESI (RAG)]` del prompt.

### 3. Protezione Fatti Appresi (`LEARNED_FACT`)
* **Meccanismo:** Le affermazioni fattuali o le definizioni fornite dall'utente vengono classificate come `MemoryType.LEARNED_FACT` con `importance=1.0`, `synaptic_strength=100.0` e `amygdala_protected="true"`, rendendole immuni all'oblio Ebbinghaus notturno.

---

## 🧬 Architettura TRINITY: Il Cervello Triadico di Marcus (RAG + CAG + MAG)

### 1. Fondamenti Architetturali e Separazione delle Responsabilità
L'architettura TRINITY integra i tre paradigmi di memoria e contesto operando a compartimenti stagni per massimizzare la rilevanza e minimizzare l'overhead di calcolo e RAM (vincolo 4GB Raspberry Pi 5):

1. **RAG (Knowledge & Code):**
   - **ChromaDB Dual Collection:** `robot_memories` per la memoria conversazionale residente + `marcus_knowledge_base` per documenti tecnici, datasheet hardware, file `.py` e guide.
   - **Chunking AST & Headers:** Suddivisione del codice Python per blocchi di classi/funzioni e documentazione Markdown per sezioni logiche.
   - **Float16 Quantization:** Tutti gli embedding sono quantizzati a 16-bit per dimezzare il consumo di RAM.

2. **CAG (Context-Augmented Generation):**
   - **Context Awareness in Tempo Reale:** Aggregatore modulare (`ContextAggregator`) con TTL cache circolare a 5 secondi.
   - **Ispezione Multi-Sensoriale:**
     - *Hardware:* Carico CPU per-core, temperatura SoC (`/sys/class/thermal/thermal_zone0/temp`), memoria RAM libera, tensione batteria, stato interfacce di rete.
     - *Topologia ROS 2:* Nodi attivi, topic diagnostici, integrità dell'albero TF.
     - *Error Tracking:* Ring buffer degli ultimi 5 errori o traceback di esecuzione/compilazione.
     - *Environment Snapshot:* Posizione stimata, stanza (VPR NetVLAD), persone riconosciute (Face/Speaker ID), oggetti rilevati (YOLOv8) e stato Smart Home.
   - **Budget Token Rigido:** Massimo 400 token dedicati al CAG per turno.

3. **MAG (Memory-Augmented Generation & Autobiographical Persistence):**
   - **SQLite + WAL Protection:** Database transazionale atomico `mag_trinity.db` con FTS5 (Full-Text Search) e storage BLOB degli embedding.
   - **Zettelkasten Atomica (`SemanticFactStore`):** Apprendimento incrementale di fatti e configurazioni hardware con deduplicazione semantica (cosine similarity > 0.85) e confidence scoring.
   - **User Profile Engine:** Tracciamento persistente delle preferenze utente (stile di programmazione, linguaggi preferiti, baudrate, livello tecnico).
   - **Hybrid Search (RRF):** Ricerca ibrida basata su Reciprocal Rank Fusion che combina FTS5 BM25 full-text con similarità vettoriale.

4. **Metaprompt Fusion Engine:**
   - Composizione deterministica e bilanciata delle sezioni:
     `[RUOLO DEL ROBOT]` $\rightarrow$ `[MEMORIA STORICA (MAG)]` $\rightarrow$ `[CONTESTO ATTUALE (CAG)]` $\rightarrow$ `[CONOSCENZA RECUPERATA (RAG)]` $\rightarrow$ `[DATA LOCALE]` $\rightarrow$ `Utente: {richiesta}`.
   - Troncamento intelligente per-sezione con budget totale controllato (~2200 token).

---

## 🛡️ Memory Pressure Sentinel & Gestione Ciclo di Vita (FM-SYS-008)

### 1. Monitoraggio Kernel Linux PSI (`/proc/pressure/memory`)
* **Problema:** Su Raspberry Pi 5 con 4GB di RAM host, la concorrenza tra Nav2, RTAB-Map, ChromaDB e pipeline audio VUI causava memory creep progressivo e OOM crash irreversibili. I riavvii da watchdog bash erano distruttivi (`pkill -9`).
* **Soluzione Architetturale (`system_lifecycle_coordinator_node.py`):**
  - **Soglia 1 (`full avg10 >= 0.30` o RAM > 3.4 GB):** Allarme `WARNING_FREEZE`. Congelamento immediato (`freeze_embeddings()`) dell'accodamento di nuovi vettori in `memory_manager.py`.
  - **Soglia 2 (`full avg10 >= 0.60` o RAM > 3.75 GB):** Allarme `CRITICAL_EVICT`. Eviction forzata dei buffer e delle cache transitorie, invocazione di `gc.collect()` e rilascio heap al kernel con `ctypes.CDLL('libc.so.6').malloc_trim(0)`.

### 2. Macchina a Stati Operativi del Ciclo di Vita
* **`NAVIGATION_ACTIVE`:** Piena banda CPU/RAM riservata a VIO, RTAB-Map (1.5 Hz) e Nav2 MPPI; sospensione di `nightly_dream_service` ed estrazioni vettoriali pesanti.
* **`DOCKED_DREAM`:** Robot in carica ($V \ge 12.65\text{V}$ o stato `DOCKED`); disattivazione stack Nav2 e VIO per riallocare le risorse al consolidamento notturno dei log e all'inferenza DeepSeek.
* **`HUMAN_INTERACTION_MODE`:** RTAB-Map throttled a **0.25 Hz** (1 frame ogni 4s) e priority boost real-time sul processo audio VUI (`respeaker_vui_node`) con `os.sched_setscheduler` (`SCHED_RR` / `os.nice(-10)`), azzerando jitter e latenze vocali.

---

## 🔁 Triplice Sottoscrizione Topic Testo e Prevenzione Echo Loop (FM-COG-003)

* **Problema:** Quando l'operatore pubblicava un messaggio testuale dal pannello Foxglove Studio o dalla Web UI sul topic `/robopy/conversation_rx`, Marcus elaborava la richiesta per tre volte consecutive in parallelo. Al terzo turno scattava la condizione `repeat_count >= 2` del filtro contestuale in `conversation.py`, che iniettava nell'LLM il suggerimento: *"L'utente ti sta ponendo questa domanda per la terza volta... chiedigli in modo amichevole se c'è un problema di connessione o se non ti ha sentito bene"*. L'LLM generava quindi risposte di stupore per l'apparente insistenza dell'utente (*"Uhm... ciao Luca. Beh, mi sembra di avertelo appena detto, sai? Forse c'è un problema di connessione..."*).
* **Cause Radice Identificate:**
  1. **Sovrapposizione di Remapping e Sottoscrizioni Concorrenti:** Nel nodo `robot_ai_orchestrator` (`orchestrator.py`), erano registrate contemporaneamente tre sottoscrizioni:
     ```python
     self.create_subscription(String, '/ai/input/text', self._text_input_callback, 10)
     self.create_subscription(String, 'ai/input/text', self._text_input_callback, 10)
     self.create_subscription(String, '/robopy/conversation_rx', self._text_input_callback, 10)
     ```
     Contemporaneamente, nello script di lancio [`restart_hailo.sh`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/restart_hailo.sh) e in `robot_ia_launch.py` veniva passato il remapping `--ros-args -r /ai/input/text:=/robopy/conversation_rx`.
     In ROS 2, la regola di remapping trasformava sia `/ai/input/text` sia `ai/input/text` in `/robopy/conversation_rx`. Unendosi alla sottoscrizione diretta, DDS generava **3 distinti endpoint di sottoscrizione (Subscription count: 3)** per lo stesso topic sullo stesso nodo, scatenando 3 esecuzioni di callback per ogni singolo messaggio.
  2. **Assenza di Deduplicazione Testuale:** Il callback `_text_input_callback` inoltrava acriticamente qualsiasi stringa non vuota ad `asyncio.run_coroutine_threadsafe(self.conversation_manager.process_input)`.
* **Soluzione Architetturale a Tre Livelli:**
  1. **Normalizzazione Sottoscrizioni ROS 2:** Rimosso il remapping `--ros-args -r /ai/input/text:=/robopy/conversation_rx` da `restart_hailo.sh` e `robot_ia_launch.py`. In `orchestrator.py`, registrata esattamente **una sola** sottoscrizione a `/robopy/conversation_rx` ed **una sola** a `/ai/input/text`, riducendo il conteggio di DDS a 1 endpoint univoco.
  2. **Deduplicatore Reattivo ROS 2 (Livello Callback):** In `_text_input_callback`, introdotta la memoria `self._last_text_input` con finestra mobile di 10.0 secondi. Qualsiasi messaggio identico ricevuto entro 10.0s viene scartato con warning di log prima di entrare nella pipeline asyncio.
  3. **Anti-Echo Cache Cognitiva (Livello Conversazionale):** In `conversation.py`, introdotto il ring buffer `self._anti_echo_cache` (separato da `self._recent_inputs` per evitare type conflict tra tuple e dizionari) che intercetta e silenzia duplicati testuali entro 10.0s anche in caso di canali concorrenti o ritrasmissioni DDS.

---

## 🧠 Recupero Memoria RAG/MAG, Acronimo MARCUS, MemoryInfoSkill e TTS Chat (FM-COG-004)

* **Problema:** Quando l'utente interrogava Marcus dalla chat (topic `/robopy/conversation_rx`) con domande sui suoi ricordi ("cosa hai appreso oggi?"), Marcus non estraeva alcuna informazione o cadeva in echo loop ripetitivi (*"non ho visto molto di nuovo, ho fatto il mio sogno notturno"*). Se interrogato sul significato del suo nome ("cosa significa Marcus?"), Marcus inventava una derivazione dall'etimologia latina e dal dio Marte, ignorando il proprio acronimo. Inoltre, chiedendo se avesse annotato qualcosa in memoria, `MemoryInfoSkill` andava in crash con `AttributeError: 'MemoryManager' object has no attribute 'get_stats'`, e le risposte testuali risultavano completamente mute sullo speaker del robot.
* **Cause Radice Identificate:**
  1. **Cancellazione Distruttiva del System Prompt:** In `conversation.py` (linee 337 e 395), il codice eseguiva prima di ogni inferenza `self.llm.set_system_prompt(self.agent_state.system_prompt_override)`. Poiché l'override era solitamente stringa vuota `""`, l'intero prompt di sistema fondamentale di Marcus (comprensivo del ruolo e dell'identità) veniva cancellato, lasciando l'LLM senza istruzioni di personalità.
  2. **Bug di Troncamento Token in `metaprompt_fusion.py`:** La funzione `_truncate_to_budget` effettuava `lines = text.split('\n')` e interrompeva il ciclo al primo elemento se la riga superava il budget, restituendo `""` e scartando completamente l'intero blocco `sys_block`.
  3. **Assenza di Vincolo Comportamentale sull'Acronimo:** Nel Metaprompt mancava una regola che vietasse esplicitamente divagazioni mitologiche o latine sul nome Marcus, imponendo la definizione tecnica esatta.
  4. **Intent Router Monolingua Inglese:** `intent_router.py` categorizzava come `MEMORY` solo parole chiave inglesi (`remember`, `recall`). Domande in italiano come *"cosa hai appreso?"* o *"cosa ricordi?"* venivano declassate a `GENERAL`.
  5. **Mancata Estrazione Fatti Zettelkasten:** `conversation.py` non passava fatti semantici a `record_interaction`, lasciando la tabella `semantic_facts` in SQLite con zero record.
  6. **Dirottamento e Crash di `MemoryInfoSkill`:** La regex di match catturava indebitamente qualsiasi frase con "memoria" + "cosa" (confidenza 0.98), invocando poi metodi inesistenti (`get_stats()`, `list_loaded_documents()`) su `MemoryManager`.
  7. **Muting Vocale su Chat Testuale:** Un wrapper su `self.tts.speak` silenziava l'audio ogni volta che la sorgente era `"text"`, impedendo a Marcus di parlare quando riceveva messaggi da Foxglove.
* **Soluzione Architetturale Implementata:**
  1. **Preservazione System Prompt:** Rimossa la sovrascrittura di `self.llm._system_prompt` in `conversation.py`. L'inhibition prompt viene fuso nativamente nel metaprompt TRINITY.
  2. **Robustezza Troncamento e Acronimo Blindato:** In `metaprompt_fusion.py`, `_truncate_to_budget` esegue fallback per troncamento caratteri anziché scartare il testo. Nel blocco `[RUOLO DEL ROBOT]` è inserita la regola tassativa che MARCUS è esclusivamente l'acronimo di *"Modular Autonomous Robotic Control Unit System"*, con divieto esplicito di citare divinità latine o Marte.
  3. **Bilingual Intent Routing & Anti-Echo RAG/MAG:** In `intent_router.py` aggiunte regex in italiano per `MEMORY` e gli altri intenti. In `trinity_engine.py` e `mag_episodic.py`, filtrati i vecchi template di risposta negativi e recuperati prioritariamente i `LEARNED_FACT` dalla memoria a lungo termine.
  4. **Armonizzazione `MemoryInfoSkill` & `MemoryManager`:** In `MemoryManager` implementati `get_stats()` e `list_loaded_documents()`. Ristretto il matcher di `MemoryInfoSkill` affinché operi solo su richieste esplicite di file/documenti tecnici caricati.
  5. **TTS Unmute:** Rimosso il blocco muto da `conversation.py`: Marcus parla ad alta voce anche in risposta a comandi testuali da Foxglove Studio.

---

## 🤖 Governance Antigravity, Modelli Gemini Flagship e Rolling Quota Tracker 4h (Sprint Auto-Evoluzione)

* **Contesto & Obiettivo:** Integrazione dell'agente autonomo Google Antigravity a bordo del Raspberry Pi 5 per l'auto-evoluzione continua, creazione autonoma di skill e risoluzione automatica delle failure DFMEA.
* **Modello AI Utilizzato:**
  - L'SDK nativo Antigravity (aarch64 `localharness`) adotta come predefinito `gemini-3.8-flash` (`DEFAULT_MODEL`) e `gemini-3.1-flash-lite-image`.
  - In `AntigravityAgentService`, è implementata la prioritizzazione dinamica verso i modelli flagship più avanzati: prioritario `gemini-2.5-pro` (massima capacità di reasoning e code architecture), con fallback trasparente a `gemini-3.8-flash` e `gemini-2.5-flash`.
* **Vincolo Quota Token a 4 Ore (Soglia Rigida 90%):**
  - **Problema:** Gli account con abbonamento Antigravity/Google AI operano con finestre di quota scorrevoli (rolling window) di 4 ore (14.400 secondi). Se il robot esaurisse la quota durante la notte o in compiti pesanti, rischierebbe l'interruzione brusca (429 Rate Limit) lasciando codice a metà o bloccando le comunicazioni vocali.
  - **Soluzione Architetturale (`TokenQuotaTracker`):**
    - Monitoraggio continuo tramite ledger persistente (`docs/evolution/token_quota_ledger.json`).
    - Calcolo dell'utilizzo reale ricavato dai metadati restituiti dall'API (`usage_metadata.total_token_count`).
    - **Soglia di Sicurezza al 90%:** Quando l'uso cumulato proiettato raggiunge o supera il 90% del budget a 4 ore, ogni nuova operazione pesante viene congelata in sicurezza senza sollevare eccezioni distruttive.
* **Persistenza Stato & Ripresa nelle Sedute Successive (`EvolutionCheckpointManager`):**
  - Quando scatta il blocco del 90%, l'attività viene serializzata in `docs/evolution/evolution_checkpoint.json` conservando `task_id`, `conversation_id`, step completati, artifact parziali e timestamp di ripresa.
  - Grazie al supporto nativo dell'SDK per `SessionContinuationMode.RESUME`, nelle sedute successive (quando la finestra a 4 ore scende sotto l'80% o al ciclo notturno successivo) il robot si riallaccia esattamente allo stesso thread di conversazione senza perdere il contesto e finalizza l'attività senza sprecare token doppi.

---

## 📚 Accesso alla Conoscenza Tecnica Completa, Dialogo On-Demand e Streaming Real-Time delle Skill

* **Problema Risolto:** Marcus necessitava di poter discutere liberamente di tutta la propria architettura ingegneristica (DFMEA, ECO, Schede Tecniche SPEC, Lessons Learned), consultare Antigravity all'occorrenza per pareri architetturali, mantenere memoria autobiografica dei propri step evolutivi, e informare l'utente in tempo reale e con linguaggio naturale durante la sintesi di nuove capacità operative.
* **Architettura Implementata:**
  1. **Servizio di Documentazione Unificato (`RobotDocumentationService`):**
     - Parser e motore di ricerca semantica/strutturata per `fmea/dfmea.yaml`, `docs/ecos/`, `docs/specs/`, `docs/lessons/`, e file di configurazione del workspace.
     - Blocco perimetrale di sicurezza (SPEC-05/SPEC-00): divieto categorico di accedere a `.env` e `secrets.yaml`.
     - Risolutore in linguaggio naturale: smista le domande dell'utente fornendo risposte immediate ed esatte.
  2. **Skill `ConsultDocumentationSkill` & `ConsultAntigravitySkill`:**
     - Esposizione verso Gemini Live API sia via Tool Calling che matching in linguaggio naturale.
     - Marcus può dialogare on-demand con l'Agente Antigravity (Gemini 3.8) come consulente senior per analizzare codice o risolvere problemi complessi, traducendo la risposta per l'utente.
  3. **Integrazione TRINITY Metaprompt Fusion:**
     - Nuova categoria `IntentCategory.DOCUMENTATION` in `IntentRouter` con boosting di `rag_knowledge_enabled` e `mag_facts` a 1.0.
     - Fallback arricchito in `TrinityEngine._retrieve_rag_knowledge`: la documentazione ufficiale viene fusa direttamente nel prompt se ChromaDB non ha corrispondenze esatte.
  4. **Pipeline Skill con Osservabilità Real-Time & Streaming VUI:**
     - In `SkillGeneratorPipeline` introdotto il callback asincrono `on_progress` per emettere stati strutturati: `ANALYZING` ➔ `ANTIGRAVITY_THINKING` ➔ `CODE_RECEIVED` ➔ `AST_CHECKING` ➔ `SMOKE_TESTING` ➔ `SANDBOX_TESTING` ➔ `ISSUE_DETECTED` ➔ `COMPLETED` o `QUOTA_SUSPENDED`.
     - In `CreaSkill` streaming tramite `asyncio.Queue` che permette all'Orchestratore di verbalizzare vocalmente e in tempo reale ogni passaggio all'utente con tono amichevole, rassicurante e trasparente.
     - Risposta alle query di stato dell'utente ("A che punto è la skill?", "Cosa sta facendo Antigravity?").
  5. **Memoria Autobiografica Persistente (MAG & Diario):**
     - Ogni skill creata o ciclo evolutivo viene automaticamente archiviato in `mag_database.db` (`episodes` e `semantic_facts`) e nel diario `docs/evolution/evolution_journal.md`, consentendo a Marcus di ricordare e raccontare con continuità identitaria la propria crescita nel tempo.

---

## 🧬 Project Autopoiesis: Data Mining Misurato con Idle Gating e Pipeline Multi-Modello (FM-EVO-001)

* **Contesto:** Progettazione del sistema di auto-miglioramento continuo in piena autonomia per Marcus.
* **Principio Ingegneristico Cardine:** *"Ciò che non si misura non si ha sotto controllo"*. Il miglioramento continuo non può basarsi su mere allucinazioni o ipotesi teoriche, ma deve fondarsi su evidenze telemetriche oggettive raccolte durante l'attività sul campo.
* **Regola Inviolabile dell'Idle Gating (FM-EVO-001):**
  - **Problema:** Campionare telemetria continuamente anche quando il robot è fermo al dock o con navigazione spenta produce runaway I/O, usura inutile dell'SSD NVMe e spreco di cicli CPU su Raspberry Pi 5.
  - **Soluzione Implementata (`MarcusDataMiner`):**
    - Il nodo controlla congiuntamente lo stato delle missioni Nav2 (`/navigate_to_pose/_action/status`) e la cinematica reale (`/cmd_vel`, `/odometry/filtered`).
    - **A navigazione spenta e robot fermo, la raccolta dati è COMPLETAMENTE SOSPESA** (`is_idle() == True` -> skip cycle a 0 Hz).
    - Quando il robot naviga o è in movimento, campiona a 0.1 Hz (ogni 10s) cross-track error, jitter angolare, recovery count e risorse RAM/CPU.
    - Buffer limitato a 60 campioni (10 minuti di moto effettivo) e batch flush su SSD in un'unica transazione SQLite WAL (`PRAGMA synchronous = NORMAL`).
* **Selezione Autonoma Quotidiana ("Tema del Giorno"):**
  - Ogni notte alle 03:00 (durante `NightlyDreamService`), `CuriosityEvolutionEngine.select_daily_focus_theme()` incrocia i colli di bottiglia telemetrici reali rilevati sul campo con i massimi RPN aperti nel database FMEA (`dfmea.yaml`).
  - La decisione viene notificata su `docs/evolution/daily_focus_notifications.md` e sul topic `/robot_ai/notifications`, senza dipendenze instabili verso Home Assistant.
* **Specializzazione dei Ruoli Multi-Modello:**
  - **Orchestratore (Gemini 3.1 Pro):** Si attiva di notte per sviscerare a fondo la causa radice, consultare le schede tecniche SPEC e scomporre il lavoro in micro-task (max 50-100 righe di codice).
  - **Coder (Gemini 3.8 Flash):** Riceve i micro-task ed esegue la scrittura del codice, la compilazione e la validazione in Sandbox AST/Pytest ad altissima velocità e con consumo minimo di token.

---

## 🗺️ Loop Decisionale Esplorazione & Memoria TRINITY (Fase 4 - Milestone 2.5)

* **Contesto:** Integrazione dello stack di navigazione autonoma a frontiere (`frontier_explorer_node.py` / `explore_lite`) con il Cervello Triadico TRINITY (CAG, MAG, RAG) per decisioni contestuali, grounding vocale e dismissione completa di NoMaD.
* **Componenti Integrate:**
  1. **CAG Aggregator (`cag_environment.py`):**
     - Aggiunta della telemetria in tempo reale dello stato dell'esploratore: `exploration_active`, `exploration_mode: EXPLORE | HUNT`, `exploration_target`, `remaining_frontiers`.
     - Output compatto per metaprompt fusion in `EnvironmentSnapshot.to_text()`: stringa token-efficient ($\le 15$ token, es. `[ENV] ... | Exploration: HUNT(persona, frontiers=3)`), che consente a Gemini Live o LLM locale di sapere esattamente cosa sta facendo Marcus senza polling ROS asincrono o allucinazioni.
  2. **MAG Memory & Eventi Target (`trinity_engine.py` / `mag_zettelkasten.py`):**
     - Sottoscrizione asincrona agli eventi `/frontier_exploration/status` e `/exploration/target_event`.
     - All'avvenuto aggancio bersaglio (`event == "TARGET_ACQUIRED"`), archiviazione automatica di un fatto semantico in SQLite WAL (`semantic_facts` con `fact_type = "SEMANTIC_LANDMARK"`): es. `Bersaglio 'chiavi' individuato a coordinate (2.45, -1.30) nella stanza 'salotto'`.
     - Metodo di interrogazione rapida `TrinityEngine.find_target_location(target_name)` via ricerca full-text (FTS5) e parsing regex delle coordinate $(x, y)$.
  3. **FrontierExplorationSkill & Epurazione Definitiva NoMaD:**
     - Nuova skill `FrontierExplorationSkill` con gestione vocale di intenti multipli: esplorazione pura (`EXPLORE`), caccia guidata da semantica (`HUNT`), stop immediato.
     - Nel flusso `HUNT` ("trova le chiavi", "cerca Marco"), la skill interroga preventivamente MAG: se la posizione è nota e recente, invia Nav2 direttamente su quelle coordinate; se non nota, attiva l'esplorazione autonoma a frontiere con bias semantico.
     - **Epurazione Radicale NoMaD:** Il modello e tutti i file/pacchetti associati a NoMaD sono stati fisicamente eliminati dal workspace (inclusi `nomad_navigator_node.py`, `nomad_reactive_pipeline_node.py`, `nomad_exploration_skill.py`, launch script e test dedicati). `FrontierExplorationSkill` è ora l'unico oracolo di esplorazione autonoma di Marcus.

---

## 🗣️ Controllo Cognitivo TRINITY della Navigazione & Conversational Barge-In (Milestone 2.5)

### 1. Eliminazione del Fast-Path Regex per Comandi di Moto
* **Problema:** In precedenza, parole d'ordine come "esplora", "naviga" o "fermati" venivano intercettate da un regex fast-path (`find_best_match >= 0.95`) in `conversation.py`. Questo causava false attivazioni su frasi ordinarie, bypassava completamente l'LLM e privava Marcus della consapevolezza contestuale.
* **Soluzione:** Bypassati i match ad alta confidenza per le skill di navigazione (`navigation`, `frontier_exploration`, `visual_exploration`). Tutti i comandi verbali di moto vengono ora instradati al TRINITY LLM (Gemini Live API e standard API) tramite **Function Calling nativo** (`start_frontier_exploration`, `stop_navigation`, `get_navigation_status`, `search_target`).
* **Vantaggio:** L'LLM decide intenzionalmente se, come e dove navigare, può motivare le sue scelte ("Parto subito a esplorare il salotto"), monitorare le prestazioni e adottare strategie di problem-solving.

### 2. Conversational Barge-In con Sospensione/Ripresa dell'Esplorazione
* **Problema:** Quando Marcus era in movimento durante l'esplorazione e l'utente gli rivolgeva la parola, il robot continuava a marciare, generando rumore meccanico nei microfoni ReSpeaker e distraendosi dal dialogo.
* **Soluzione:** Implementata la logica di **Conversational Barge-In** in `ConversationManager`:
  1. All'inizio del turno vocale, se `frontier_skill.is_exploring == True`, viene invocato `pause_for_dialogue()`, azzerando istantaneamente `/cmd_vel` e sospendendo l'invio di goal di frontiera.
  2. Nel prompt viene iniettata una nota contestuale esplicita: il robot sa di essere in pausa e spiega cosa stava facendo se interrogato ("Mi sono fermato per ascoltarti; stavo esplorando il corridoio").
  3. Al termine della risposta vocale (o dell'elaborazione), se l'utente non ha esplicitamente richiesto di fermarsi (es. "fermati", "annulla esplorazione"), il sistema invoca automaticamente `resume_after_dialogue()`, riattivando l'esplorazione a frontiere dal punto in cui era stata sospesa.

### 3. Persistenza Autobiografica degli Eventi di Navigazione in MAG (SQLite WAL)
* **Tracciamento Fatti:** Ogni avvio di missione esplorativa e ogni scoperta di landmark semantico viene registrato nella tabella `semantic_facts` con categoria `NAVIGATION_EVENT`.
* **Fix Deduplicazione Semantica FTS5:** Risolto un bug critico in `MAGDatabase.search_similar_facts`: in precedenza, qualsiasi query FTS5 restituiva corrispondenze generiche che venivano scartate a priori come duplicati. È stata introdotta la verifica di similarità metrica Jaccard sui token ($\ge 0.85$), consentendo la persistenza affidabile di eventi distinti anche se condividono parole chiave (es. "stanza", "esplorazione").

---

## 🧠 Risoluzione Ricerche in Memoria, QueryMemorySkill e Prevenzione Silenzio (FM-COG-004)

### 1. Il Problema Rilevato
* **Sintomo:** Chiedendo al robot di accedere ai dati della memoria ("accedi ai dati della memoria", "fai una ricerca nella memoria", "cosa c'è nella memoria?") o ponendo domande che richiedevano l'interrogazione dei ricordi, Marcus non riusciva a recuperare le informazioni e non produceva alcuna risposta vocale o testuale (rimaneva completamente muto).
* **Cause Radice Identificate:**
  1. **Mancata implementazione del tool `query_memory`:** In `tool_declarations.py` era dichiarato lo strumento `query_memory`, ma nessuna classe `QueryMemorySkill` era stata implementata in `robot_ai/skills/builtin/` né registrata nel `SkillRegistry` di `orchestrator.py`. Quando Gemini function-calling invocava `query_memory`, `skill_executor` non trovava il tool, loggava un warning e restituiva una lista vuota.
  2. **Crash runtime in `MemoryInfoSkill`:** Nel metodo `execute()` veniva invocato `SkillResult.success_result(..., formatted_document=markdown)`. Poiché la factory `SkillResult.success_result` in `base_skill.py` non accetta `formatted_document` come parametro esplicito, veniva sollevata un'eccezione non gestita `TypeError: unexpected keyword argument 'formatted_document'`. Inoltre, essendo un generatore asincrono con un primo yield vuoto di attesa, `_execute_tool_live` scartava il risultato effettivo.
  3. **Fallimento FTS su query meta/esplorative:** Frasi come *"accedi ai dati della memoria"* venivano tokenizzate e cercate tramite FTS5 su `episodes_fts` e `facts_fts`. Poiché gli episodi storici contengono eventi specifici (es. "Ho acceso la luce", "Mi chiamo Luca") e non le parole "accedi" o "memoria", FTS restituiva 0 righe. In assenza di fallback, `HybridSearchEngine` ritornava liste vuote.
  4. **Vuoto conversazionale su Function Calling muto:** In `ConversationManager._process_locked`, quando Gemini produceva una function call, `response.text` era vuoto (`""`). Se il tool invocato non emetteva testo parlato o falliva silenziosamente, né `self.tts.speak()` né `self.response_callback()` venivano invocati, lasciando il robot in totale silenzio.

### 2. Risoluzioni Implementate
1. **Creazione di `QueryMemorySkill` (`robot_ai.skills.builtin.query_memory_skill`):**
   - Implementa l'interfaccia `BaseSkill` con nome `"query_memory"`, priority 6, e parametri `query`, `limit`, `type`/`memory_type`.
   - Matching ad alta confidenza ($\ge 0.95$) su trigger diretti in italiano (*"accedi ai dati della memoria"*, *"cosa c'è nella memoria"*, *"cosa ti ricordi"*, *"fatti appresi"*).
   - Interrogazione sinergica del database autobiografico MAG (SQLite WAL: episodi recenti, fatti Zettelkasten, profili utente) e del vector store ChromaDB (memorie semantiche recenti).
   - Generazione di un resoconto vocale fluido e sintetico in lingua italiana (`speak`), unitamente a un documento Markdown strutturato per l'interfaccia Foxglove (`message` e `data`).
2. **Hardening di `MemoryInfoSkill`:**
   - Rimozione del parametro illegale `formatted_document` dalla factory `SkillResult.success_result`; il documento Markdown viene ora veicolato correttamente all'interno del dizionario `data={"formatted_document": markdown, ...}`.
   - Conversione da generatore asincrono a coroutine diretta (`async def execute(...) -> SkillResult`), garantendo che `_execute_tool_live` riceva immediatamente il report completo.
3. **Fallback per Query Esplorative in `HybridSearchEngine`:**
   - In `mag_hybrid_search.py`, se una query è esplorativa o se la ricerca FTS/vettoriale produce 0 risultati, il motore ricade deterministicamente su `get_recent_episodes(limit=top_k)` e `get_all_facts()[:top_k]`.
4. **Protezione Anti-Silenzio in `ConversationManager` e `SkillExecutor`:**
   - In `skill_executor.py`: supporto all'estrazione di `query` in fallback su `execution_text`, e fallback automatico su `result.message` se `result.speak` è assente.
   - In `conversation.py`: aggiunta della guardia anti-silenzio. Se sono state eseguite azioni esplicative ma nessun parlato è stato emesso e `response_text` è vuoto, viene pronunciata una risposta di stato gentile (*"Ho controllato la mia memoria, ma non ho trovato informazioni specifiche a riguardo"*), garantendo che Marcus fornisca sempre un riscontro all'interlocutore.

---

## 🧠 Risoluzione Amnesia Multi-Turn ('Effetto Parkinson') & Working Memory TRINITY (FM-TRI-008)

### 1. Il Problema Rilevato
* **Sintomi:**
  1. Marcus sembrava soffrire di perdita totale di memoria ad ogni frase ("effetto Parkinson"): ogni nuovo input dell'utente veniva trattato come una conversazione completamente nuova, incapace di risolvere riferimenti al contesto o anafore ("l'hai trovato?", "di cosa stavamo parlando?", "e adesso?").
  2. Ponendo domande del tipo *"ti ricordi cosa ti ho detto di cercare?"*, Marcus rispondeva in modo rigido e stereotipato affermando unicamente di ricordare il proprio nome e acronimo: *"Ho consultato la mia memoria: ricordo ad esempio che MARCUS ? un acronimo tecnico... Inoltre ho registrato altri 4 fatti appresi."*
* **Cause Radice Identificate:**
  1. **Assenza della Working Memory nel Metaprompt:** `MetapromptFusion` e `ConversationManager` non includevano la cronologia dialogica recente (`recent_dialogue`) nel prompt inviato all'LLM. Ogni chiamata di generazione standard non riceveva alcun contesto dei turni precedenti, rendendo l'LLM totalmente all'oscuro di ciò che era stato detto pochi secondi prima.
  2. **Hijacking Indebito via Fast-Path Regex:** In `QueryMemorySkill.match()`, le frasi colloquiali come *"ti ricordi cosa"*, *"cosa ti ricordi"*, *"cosa abbiamo fatto"* restituivano un punteggio di confidenza di 0.98. In `ConversationManager._process_locked`, qualsiasi skill con confidenza $\ge 0.95$ veniva eseguita via fast-path, bypassando completamente l'LLM e il motore TRINITY!
  3. **Fallback Forzato su Fatti Generici:** In `QueryMemorySkill.execute()`, se la ricerca FTS per la frase colloquiale non produceva risultati (poiché non era memorizzata tra i fatti), il codice ricadeva forzatamente su `get_all_facts()[:limit]`. Il primo fatto del database era tassativamente la definizione dell'acronimo MARCUS, mentre la presenza di qualsiasi fatto causava l'oscuramento completo della visualizzazione degli episodi dialogici (`elif episodes_summary`).

### 2. Risoluzioni Implementate
1. **Iniezione della Working Memory (`[CONVERSAZIONE RECENTE]`):**
   - Introdotto un buffer circolare `conversation_history` (ultimi 10 turni = 5 scambi) in `ConversationManager`.
   - Aggiunta la sezione `[CONVERSAZIONE RECENTE]` in `MetapromptFusion` con budget token dedicato `BUDGET_DIALOGUE = 350` token (target complessivo calibrato a 2150 token, strettamente sotto il tetto massimo di 2500 prescritto in SPEC-05 ZONA ROSSA).
   - Propagazione trasparente della cronologia dialogica tramite `TrinityEngine.build_augmented_prompt()` e nel fallback `_build_prompt()`.
2. **Inibizione del Fast-Path per Skill Cognitive e di Memoria:**
   - Applicato il principio del **Primato Cognitivo Assoluto dell'LLM** (SPEC-05, SPEC-01 e marcus_core_rules.md): le skill cognitive come `query_memory`, `memory_info`, `consult_antigravity` e `consult_documentation` non possono MAI essere eseguite in fast-path bypassando l'LLM. Il fast-path on-device immediato (<10ms) rimane rigorosamente riservato all'arresto di emergenza.
   - Rimosse da `QueryMemorySkill.match()` tutte le frasi colloquiali che appartengono all'interazione naturale dell'LLM, limitando il trigger alle sole richieste esplicite di dump o diagnostica con confidenza limitata a $\le 0.85$.
3. **Correzione Logica di Sintesi in `QueryMemorySkill`:**
   - Eliminato il fallback cieco a `get_all_facts()` per query specifiche.
   - Se esistono sia fatti che interazioni/episodi pertinenti, entrambi vengono inclusi nella risposta vocale e nel report Markdown.
   - Soppressione dell'emissione di header vuoti (`RECENT EPISODES:` e `ZETTELKASTEN FACTS:`) quando i rispettivi array sono privi di elementi.

---

## 📅 Conservazione Temporale, Datatura dei Ricordi Autobiografici (MAG) & Analisi di Frequenza (FM-TRI-009)

### 1. Il Problema Rilevato
* **Sintomi:**
  1. Marcus rispondeva a domande dell'utente sulle date dei ricordi affermando: *"Uhm... Mi dispiace, Luca. Sembra che ci sia un limite alla precisione con cui posso registrare le date esatte dei miei ricordi."*
  2. Impossibilità per l'utente di chiedere cosa fosse successo un determinato giorno ("cosa abbiamo fatto ieri?", "cosa è successo il 2 ottobre?"), quanti ricordi fossero stati registrati in un intervallo o fare analisi temporali e di frequenza.
* **Cause Radice Identificate:**
  1. **Dispersione del Timestamp nella Prompt Synthesis:** Nonostante SQLite WAL memorizzasse fin dall'inizio il timestamp float (`episodes.timestamp` e `semantic_facts.created_at`), i metodi `to_prompt_sections()` in `mag_episodic.py` e `to_prompt_section()` in `mag_zettelkasten.py` formattavano gli episodi come stringhe senza data (`- Q: ... | A: ...`). L'LLM vedeva stringhe prive di riferimenti temporali e allucinava l'inesistenza della datatura nel robot.
  2. **Incapacità di Ricerca per Data e Intervallo:** Le interrogazioni temporali dell'utente (es. "ieri", "2 ottobre") venivano inviate direttamente alla ricerca Full-Text (FTS5). Poiché le parole "ieri" o "ottobre" non comparivano nel testo delle conversazioni passate, la ricerca restituiva 0 risultati. Mancava un parser temporale in linguaggio naturale e un metodo per interrogare il DB SQLite su intervalli di timestamp Unix.
  3. **Assenza di Statistiche di Frequenza:** Mancava una query aggregata per calcolare il numero di episodi per giorno e la media temporale.

### 2. Risoluzioni Implementate
1. **Iniezione del Timestamp Umano nelle Sezioni Prompt:**
   - In `mag_episodic.py`: ogni episodio viene formattato come `- [DD/MM/YYYY HH:MM] Q: ... | A: ...` localizzato sul fuso `Europe/Rome`.
   - In `mag_zettelkasten.py`: ogni fatto semantico riporta la data di acquisizione `- [FACT_TYPE | DD/MM/YYYY] ...`.
   - In `trinity_engine.py`: formattazione timestamp estesa anche ai ricordi conversazionali ChromaDB.
2. **Parser Temporale Italiano Dedicato (`MAGTemporalParser`):**
   - Modulo leggero e deterministico (`mag_temporal_parser.py`) che traduce espressioni temporali italiane ("oggi", "ieri", "l'altro ieri", "ultimi N giorni", "questa settimana", date numeriche `DD/MM/YYYY` e nominali `2 ottobre`) in tuple $[start\_timestamp, end\_timestamp]$.
   - Estrazione di token semantici sostantivi (`clean_temporal_tokens`) per consentire il ranking di argomenti all'interno di una specifica finestra temporale (es. "cosa abbiamo cercato ieri?").
   - Rilevamento intenti di frequenza/statistiche (`is_frequency_or_stats_query`).
3. **Estensione API Database `MAGDatabase` (SQLite WAL):**
   - Implementati `get_episodes_by_timerange(start_ts, end_ts)` e `get_facts_by_timerange(start_ts, end_ts)`.
   - Implementato `get_episodes_frequency_stats(days=7)` con raggruppamento SQL `date(timestamp, 'unixepoch', 'localtime')`.
4. **Integrazione in `HybridSearchEngine` e `MetapromptFusion`:**
   - In `mag_hybrid_search.py`: la ricerca riconosce le query temporali e recupera prioritariamente i ricordi appartenenti all'intervallo temporale specificato.
   - In `metaprompt_fusion.py`: posizionato `[DATA E ORA ATTUALE: {timestamp}]` in evidenza all'inizio del prompt e aggiunta la chiara istruzione: *"I ricordi ed episodi sottostanti contengono timestamp e date precise [GG/MM/AAAA HH:MM]. La tua memoria mappa esattamente le date degli eventi: usale per rispondere a domande cronologiche, sapere cosa è successo oggi, ieri o nei giorni passati, ed effettuare analisi su frequenza ed episodi."*
5. **Supporto in `QueryMemorySkill`:**
   - Supporto nativo per query statistiche ("quante volte abbiamo parlato questa settimana?", "frequenza dei ricordi").
   - Risposta assertiva e accurata alla domanda sulla persistenza delle date, con restituzione di date/orari precisi sia a voce che in Markdown.
