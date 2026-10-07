# Engineering Change Orders - Orchestrazione & RAG

Questo documento raccoglie la cronologia delle modifiche ingegneristiche (ECO) apportate ai sistemi di orchestrazione, RAG (database vettoriale) e allineamento comportamentale di Marcus.

---

## 📈 ECO-2026-05-27-001: Marcus AI v16.0 (AI_ver3) - Cognitive and RAG Overhaul
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato**
* **Descrizione:** Riprogettazione dell'architettura RAG ed elaborazione asincrona. Eliminazione di LlamaIndex per instradare le memorie direttamente via ChromaDB nativo (`ChromaNativeStore`), isolando la thread-safety e le dimensioni vettoriali. Transizione del bidi-streaming Live API a composizione pura via `LiveConnectionManager` con coda PCM audio FIFO scorrevole con oldest-drop (maxsize=50) per contenere il GIL sul Raspberry Pi 5. Configurazione e abilitazione del watchdog cognitivo via systemd per il rollback A/B.
* **Modifiche apportate:**
  * Creato `chroma_native_store.py` (store nativo ultra-veloce eliminando LlamaIndex).
  * Implementato singleton thread-safe e `RLock` sulle operazioni di lettura/scrittura.
  * Aggiunta validazione della dimensione dell'embedding a 768 per evitare derive vettoriali.
  * Rimosso completamente `llama_index_store.py` per pulizia architetturale.
  * Creato `live_connection_manager.py` (sostituendo ereditarietà con composizione pura in `llm_live_api.py`, che viene eliminato).
  * Implementato script `watchdog.sh` (monitoraggio crash con swap automatico del symlink `install` per rollback A/B) e registrato il servizio `marcus-watchdog.service` su systemd.
  * Rimosso il database `ChromaDB_Llama` a favore di una cartella pulita (`ChromaDB_Llama_backup`) per risolvere conflitti di metadati (`object of type 'int' has no len()`).

---

## 📈 ECO-2026-06-01-001: Dopamine Biometric Alignment System
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato**
* **Descrizione:** Introduzione di un sistema di allineamento biomimetico a grafo asincrono basato su un meccanismo dopaminergico di ricompensa e punizione (RPE - Reward Prediction Error) integrato nella pipeline cognitiva di Marcus.
* **Modifiche apportate:**
  * Creato il modulo `cognitive_graph.py` per modellare lo stato dell'agente (`MarcusAgentState`).
  * Implementato `CriticEvaluatorNode` per valutare feedback positivi o negativi e determinare l'RPE con salvataggio automatico su ChromaDB per scostamenti $|RPE| \ge 0.3$.
  * Implementato `PredictiveRouterNode` con inibizione sinaptica preventiva tramite query vettoriale.
  * Integrato il ciclo di input in `conversation.py` tramite `MarcusStateGraph` ed inserita l'intercettazione delle eccezioni sulle skill per settare `flag_tool_failure()`.
  * Creato lo script di test isolato `test_dopamine_alignment.py`.

---

## 📈 ECO-2026-06-24-001: Sprint 2 Optimization Pack
* **Stato:** ✅ **Completato e Sincronizzato Localmente**
* **Descrizione:** Ottimizzazione globale delle performance, riduzione dei consumi di RAM e throttling dei messaggi ROS 2 per stabilizzare il Raspberry Pi 5.
* **Modifiche apportate:**
  * Ridotta la cache degli embedding da 256 a 64 in `config_manager.py` e `embedding_service.py` (risparmio RAM).
  * Throttlato `engagement_monitor.py` a 2Hz con pubblicazione selettiva su variazione di stato/zona/distanza (riduzione traffico bus ROS 2).
  * Convertito `cloud_watchdog_node.py` a singolo thread worker persistente invece di thread-per-ping.
  * Caching delle function declarations in `skill_registry.py` e `conversation.py` per evitare list comprehension ad ogni query.
  * Limitata l'esposizione del contesto di Home Assistant a un massimo di 30 entità prioritarie in `ha_context.py`.

---

## 📈 ECO-2026-07-03-001: Amigdala Digitale e Potatura Sinaptica (Sprint 5)
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato**
* **Descrizione:** Introduzione del pacchetto cognitivo composto da 4 nodi Python (`chroma_synaptic_manager`, `cognitive_amygdala`, `cognitive_core_node`, `neuro_vegetative_bridge`) e dal servizio `MemoryRecall.srv`. Implementata la curva di decadimento per i metadati di ChromaDB ed il pruning notturno in modalità sogno, con attivazione del DMN riflessivo e del riflesso di startle.
* **Modifiche apportate:**
  * Creato `MemoryRecall.srv` ed integrato in `CMakeLists.txt` per la compilazione dei messaggi.
  * Registrate le dipendenze per le action Nav2 (`nav2_msgs`, `action_msgs`, `rcl_interfaces`) in `package.xml`.
  * Creato il sotto-modulo `robopy_controller.robot_ai.cognitive` contenente i 4 file sorgente dei nodi cognitivi.
  * Modificato `servo_coda_node.py` per accogliere le variazioni di scodinzolio in base a `/ai/conversation/mood`.
  * Registrati gli entry points in `setup.py` per consentire il lancio dei nodi via ROS 2.

---

## 📈 ECO-2026-07-30-002: MARCUS Acronym Identity & RAG Retrieval Activation (Sprint 6)
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato**
* **Descrizione:** Correzione dell'identità del robot con registrazione esplicita dell'acronimo MARCUS (Modular Autonomous Robotic Control Unit System) nel system prompt e attivazione del recupero semantico RAG attivo durante le interlocuzioni con l'utente.
* **Modifiche apportate:**
  * Modificato `llm_service.py`: aggiornato il prompt di sistema di default per includere *"MARCUS — Modular Autonomous Robotic Control Unit System"*.
  * Modificato `conversation.py`: integrata la query semantica asincrona `memory_store.search(clean_text, top_k=3)` in `_process_locked` ed iniezione delle memorie rilevanti nella sezione `[MEMORIE EPISODICHE E FATTI APPRESI (RAG)]` del prompt.
  * Modificato `memory_manager.py`: impostati `importance=1.0`, `synaptic_strength=100.0` e `amygdala_protected="true"` sui ricordi di tipo `LEARNED_FACT` per prevenire la potatura sinaptica notturna.
  * Creato `test/unit/test_rag_acronym_memory.py`: unit test per la verifica dell'acronimo e dell'iniezione RAG.

---

## 📈 ECO-2026-08-21-TRINITY: Triadic Brain Architecture (RAG + CAG + MAG)
* **Stato:** ✅ **Completato, Integrato e Testato**
* **Descrizione:** Implementazione completa dell'architettura cognitiva triadica TRINITY che integra e armonizza la conoscenza esterna documentale (RAG), la consapevolezza del contesto immediato e sensoriale (CAG), e la memoria autobiografica persistente a lungo termine (MAG) con metaprompt deterministico token-budgeted.
* **Modifiche apportate:**
  * Creato il nuovo package `robopy_controller/robot_ai/trinity/` con 17 file modulari:
    * `intent_router.py`: Classificazione query e routing selettivo dei pesi di retrieval.
    * `rag_document_indexer.py`: Chunking AST e indexing di codice `.py`, guide `.md`, configurazioni `.yaml` e datasheet PDF su collection `marcus_knowledge_base`.
    * `rag_knowledge_query.py`: Query semantica con reranking del codice e formattazione prompt.
    * `cag_hardware_collector.py`: Raccolta telemetria SoC, RAM, CPU load per core, temperatura, rete, batteria.
    * `cag_ros_inspector.py`: Ispezione nodi, topic e integrità TF tree ROS 2.
    * `cag_error_tracker.py`: Ring buffer degli ultimi 5 errori e traceback di esecuzione.
    * `cag_environment.py`: Snapshot ambiente, stanze, oggetti YOLO e smart home.
    * `cag_aggregator.py`: Aggregatore CAG con TTL cache circolare a 5s per minimizzare CPU overhead.
    * `mag_database.py`: Database relazionale SQLite atomico con modalità WAL e FTS5.
    * `mag_zettelkasten.py`: Store atomico di fatti semantici con deduplicazione (cosine > 0.85).
    * `mag_user_profile.py`: Engine di profilazione utente incrementale con confidence scoring.
    * `mag_hybrid_search.py`: Motore di ricerca ibrido con Reciprocal Rank Fusion (RRF).
    * `mag_episodic.py`: Motore di memoria autobiografica episodica e prompt formatting.
    * `mag_dream_consolidation.py`: Consolidamento notturno delle memorie episodiche nel sogno.
    * `metaprompt_fusion.py`: Assemblatore del metaprompt strutturato con token budget rigido.
    * `trinity_engine.py`: Orchestratore principale con esecuzione parallela `asyncio.gather`.
  * Modificato `conversation.py`: Integrazione di `TrinityEngine` nel flusso conversazionale sia per la generazione del metaprompt che per l'aggancio asincrono post-task `record_interaction`.
  * Modificato `chroma_native_store.py`: Aggiornato `get_chroma_client` con valore di default sicuro per `persist_dir`.
  * Registrati i Failure Modes FM-TRI-001..FM-TRI-007 in `fmea/dfmea.yaml`.
  * Creata la test suite completa in `tests/test_trinity_full.py`.

---

## 📈 ECO-2026-08-28-LIFECYCLE-SENTINEL: Memory Pressure Sentinel & Gestione Ciclo di Vita su 4GB RAM
* **Stato:** ✅ **Completato in Workspace Locale (Pronto per Deploy)**
* **Descrizione:** Implementazione del coordinatore centralizzato del ciclo di vita ROS 2 (`system_lifecycle_coordinator_node.py`) e del Memory Pressure Sentinel basato su Linux PSI (`/proc/pressure/memory`), per prevenire memory creep ed eliminare i riavvii brutali da watchdog bash.
* **Modifiche apportate:**
  * Creato `robopy_controller/nodes/system_lifecycle_coordinator_node.py`:
    - Monitoraggio Linux PSI a 2 Hz con soglia `full avg10 >= 0.30` (WARNING_FREEZE) e `full avg10 >= 0.60` (CRITICAL_EVICT).
    - Gestione FSM a 3 stati: `NAVIGATION_ACTIVE` (navigazione/SLAM prioritaria, daydreaming sospeso), `DOCKED_DREAM` (navigazione inattiva, consolidamento e DeepSeek attivi), `HUMAN_INTERACTION_MODE` (RTAB-Map a 0.25 Hz, priority boost su VUI audio).
  * Modificato `robopy_controller/robot_ai/orchestration/memory_manager.py`:
    - Aggiunte funzioni `freeze_embeddings()`, `unfreeze_embeddings()` e `clear_transient_buffers()` con gating sui vettori.
  * Modificato `robopy_controller/robot_ai/services/nightly_dream_service.py`:
    - Aggiunti metodi di controllo `suspend()` e `resume()`.
  * Modificato `robopy_controller/robot_ai/orchestration/orchestrator.py`:
    - Sottoscrizione ai topic `/system/operating_state`, `/system/memory_freeze`, `/system/emergency_evict`.
  * Modificato `robopy_controller/nodes/respeaker_vui_node.py`:
    - Implementato `apply_realtime_priority()` con `os.sched_setscheduler` (`SCHED_RR` / `os.nice(-10)`).
  * Modificato `setup.py` e `restart_hailo.sh`:
    - Registrato entry point e inserito l'avvio sequenziale del coordinator.
  * Creata suite di test unitari `test/unit/test_memory_pressure_sentinel.py` (7/7 PASS).
  * Registrato il Failure Mode `FM-SYS-008` in `fmea/dfmea.yaml` e generato `IMP-SYS-008_lifecycle_memory_sentinel.md`.

---

## 📈 ECO-2026-09-03-003: Anti-Echo Text Input Deduplication & Subscription Normalization on /robopy/conversation_rx
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato sul Robot**
* **Descrizione:** Risoluzione definitiva del triplice trigger dei comandi testuali e dell'attivazione impropria del prompt di insistenza/ripetizione dell'LLM (FM-COG-003).
* **Modifiche apportate:**
  * **[NORMALIZZAZIONE SOTTOSCRIZIONI ROS 2]** `restart_hailo.sh`, `launch/robot_ia_launch.py`, `robopy_controller/robot_ai/orchestration/orchestrator.py`:
    - Rimosso il remapping concorrente `--ros-args -r /ai/input/text:=/robopy/conversation_rx`.
    - Eliminata la doppia sottoscrizione `/ai/input/text` e `ai/input/text` nel nodo `robot_ai_orchestrator`, lasciando esattamente 1 sottoscrizione a `/robopy/conversation_rx` ed 1 a `/ai/input/text`.
    - `Subscription count` sul topic `/robopy/conversation_rx` verificato e ridotto da 3 a **1**.
  * **[DEDUPLICATORE ROS 2 CALLBACK]** `robopy_controller/robot_ai/orchestration/orchestrator.py`:
    - Implementato filtro reattivo in `_text_input_callback` con blocco temporale di 10.0 secondi su messaggi identici consecutivi.
  * **[ANTI-ECHO CACHE CONVERSAZIONALE]** `robopy_controller/robot_ai/orchestration/conversation.py`:
    - Introdotto ring buffer `_anti_echo_cache` (15s TTL) in `process_input` per scartare stringhe identiche ricevute entro 10.0 secondi, isolato da `_recent_inputs` per prevenire regressioni di tipo.
  * **[FMEA]** Registrato `FM-COG-003` in `fmea/dfmea.yaml` (RPN 378 -> 12).

---

## 📈 ECO-2026-09-04-001: TRINITY Memory Retrieval, Acronym Identity Enforcing, MemoryInfoSkill Bugfix & Audio Unmute
* **Stato:** ✅ **Completato, Sincronizzato e Collaudato sul Robot**
* **Descrizione:** Risoluzione integrata delle criticità di amnesia sull'acronimo MARCUS, assenza di recupero memorie RAG/MAG, blocco di `MemoryInfoSkill` e silenziamento vocale su chat da Foxglove (FM-COG-004).
* **Modifiche apportate:**
  * **[PRESERVAZIONE PROMPT DI SISTEMA]** `robopy_controller/robot_ai/orchestration/conversation.py`:
    - Rimosso `self.llm.set_system_prompt(self.agent_state.system_prompt_override)` che azzerava l'identità del robot prima di ogni chiamata LLM.
    - Rimosso `wrapped_speak` che silenziava sistematicamente il parlato quando l'input proveniva da chat (`source == "text"`).
    - Aggiunta estrazione e persistenza di `extracted_facts` a `trinity_engine.record_interaction(...)` per popolare la tabella Zettelkasten `semantic_facts`.
  * **[ROBUSTEZZA METAPROMPT E ACRONIMO BLINDATO]** `robopy_controller/robot_ai/trinity/metaprompt_fusion.py`:
    - Corretto il metodo `_truncate_to_budget` per troncare su caratteri invece di restituire stringa vuota al superamento del budget.
    - Blindata l'identità nel blocco `[RUOLO DEL ROBOT]`: vincolo stringente sull'acronimo "Modular Autonomous Robotic Control Unit System" e divieto di divagazioni latine/Marte.
  * **[INTENT ROUTING BILINGUE E ANTI-ECHO RAG/MAG]** `robopy_controller/robot_ai/trinity/intent_router.py`, `trinity_engine.py`, `mag_episodic.py`:
    - Aggiunte keyword italiane per la classificazione di `MEMORY`.
    - Implementato recupero prioritario di `LEARNED_FACT` in `_retrieve_rag_conversational`.
    - Filtrati i vecchi turni con risposte negative/evasive ("non ho visto molto di nuovo") sia in RAG che negli episodi MAG per rompere l'echo loop.
  * **[MEMORY MANAGER & SKILL REFACTOR]** `robopy_controller/robot_ai/orchestration/memory_manager.py`, `robopy_controller/robot_ai/skills/builtin/memory_info_skill.py`:
    - Aggiunti i metodi asincroni `get_stats()` e `list_loaded_documents()` in `MemoryManager`.
    - Ristretto il matcher di `MemoryInfoSkill` per evitare l'intercettazione indebita di conversazioni naturali.
  * **[FMEA]** Registrato `FM-COG-004` in `fmea/dfmea.yaml` (RPN 336 -> 12).




---

## 📈 ECO-2026-09-05-MARCUS-001: Integrazione Antigravity Gemini 3.8 e Policy Zero Forzature
* **Autore:** 🤖 **Generata autonomamente da Marcus** (Antigravity Autonomous Evolution Engine)
* **Data Creazione:** 2026-09-05 21:24:16
* **Sottosistema:** `AI/Cognitive`
* **Stato:** ✅ **Completato e Validato in Sandbox** (Nessuna forzatura: 100% verificato)
* **Descrizione:** Abilitazione auto-evoluzione autonoma con Gemini 3.8 Flash nativo e divieto assoluto di forzature su Pi 5.
* **Modifiche apportate:**
  * Gemini 3.8 Flash impostato come modello primario assoluto
  * Enforcement Zero-Forcing Policy: rigetto o routing a RFC se i test falliscono
  * Dicitura autore obbligatoria impostata su: Generata autonomamente da Marcus
* **Esito Validazione:** 11/11 test di collaudo superati a pieni voti su Raspberry Pi 5.

---

## 📈 ECO-2026-09-05-MARCUS-002: Accesso Documentazione, Dialogo Antigravity On-Demand, Memoria Autobiografica & Feedback Naturale Skill
* **Autore:** 🤖 **Generata autonomamente da Marcus** (Antigravity Autonomous Evolution Engine)
* **Data Creazione:** 2026-09-05 21:42:00
* **Sottosistema:** `AI/Cognitive`
* **Stato:** ✅ **Completato e Validato in Sandbox** (Nessuna forzatura: 100% verificato)
* **Descrizione:** Abilitazione dell'accesso completo e conversazionale a DFMEA, ECO, Schede Tecniche (SPEC), Lessons Learned e file di configurazione, dialogo on-demand con l'agente Antigravity, memoria autobiografica MAG per gli step evolutivi, e osservabilità in tempo reale del ciclo di vita delle skill con verbalizzazione fluida e naturale.
* **Modifiche apportate:**
  * Creazione di `RobotDocumentationService` (`robot_documentation_service.py`) per query strutturate e semantiche su DFMEA, ECO, SPEC, Lessons e file di configurazione (con blocco perimetrale di `.env` e `secrets.yaml`).
  * Creazione della skill builtin `ConsultDocumentationSkill` (`consult_documentation_skill.py`) per tool calling e matching naturale.
  * Creazione della skill builtin `ConsultAntigravitySkill` (`consult_antigravity_skill.py`) e del metodo `consult_antigravity_dialogue` in `AntigravityAgentService` per consulenze tecniche peer-to-peer con Gemini 3.8.
  * Aggiunta della categoria `IntentCategory.DOCUMENTATION` in `IntentRouter` ed arricchimento dinamico del context in `TrinityEngine._retrieve_rag_knowledge`.
  * Riprogettazione di `CreaSkill` (`crea_skill.py`) e `SkillGeneratorPipeline` (`skill_generator.py`) con callback `on_progress` e coda asincrona per streaming degli stati e frasi empatiche ("Sto analizzando...", "Ho aperto una sessione con Antigravity...", "Collaudo in sandbox...", "Sospeso per quota 90%...", "Abilità attiva!").
  * Integrazione della memoria autobiografica in MAG (`mag_database.db`) e nel diario di bordo (`evolution_journal.md`).
* **Esito Validazione:** 23/23 test superati con successo in `tests/test_robot_documentation_and_dialogue.py` e `tests/test_antigravity_evolution_suite.py`.

---

## 📈 ECO-2026-09-07-MARCUS-001: Project Autopoiesis - Data Mining con Idle Gating, Daily Focus Autonomo e Pipeline Multi-Modello (Pro Orchestrator + Flash Coder)
* **Autore:** 🤖 **Generata autonomamente da Marcus** (Antigravity Autonomous Evolution Engine)
* **Data Creazione:** 2026-09-07 16:00:00
* **Sottosistema:** `AI/Cognitive & System/Evolution`
* **Stato:** ✅ **Completato e Validato in Sandbox** (Nessuna forzatura: 100% verificato)
* **Descrizione:** Implementazione del nucleo di auto-miglioramento continuo misurato (Project Autopoiesis):
  1. `MarcusDataMiner` per raccolta dati telemetrici e diagnostici a 0.1 Hz con idle gating mandatorio (sospensione totale a navigazione spenta e robot fermo).
  2. Persistenza su SSD in formato SQLite WAL con batch flushing ogni 10 minuti (60 campioni) per evitare usura flash e consumo di RAM.
  3. Selezione autonoma quotidiana del "Tema del Giorno" in `CuriosityEvolutionEngine` e `NightlyDreamService` che incrocia colli di bottiglia telemetrici sul campo con i massimi RPN da `dfmea.yaml`.
  4. Pipeline multi-modello: `Gemini 3.1 Pro` per l'orchestrazione architetturale e scomposizione in micro-task; `Gemini 3.8 Flash` per la scrittura e validazione veloce del codice.
  5. Canale di notifica persistente su file `docs/evolution/daily_focus_notifications.md` e topic ROS 2 `/robot_ai/notifications` (disaccoppiato da Home Assistant).
* **Modifiche apportate:**
  * Creazione di `robopy_controller/robot_ai/services/marcus_data_miner.py` e relativo nodo ROS 2 `marcus_data_miner_node.py`.
  * Aggiornamento di `curiosity_evolution_engine.py` con `select_daily_focus_theme()` e `notify_daily_focus()`.
  * Aggiornamento di `nightly_dream_service.py` per invocare la selezione autonoma del tema notturno.
  * Aggiornamento di `antigravity_agent_service.py` con ruoli differenziati (`ORCHESTRATOR_GEMINI_MODELS` = `gemini-3.1-pro`, `CODER_GEMINI_MODELS` = `gemini-3.8-flash`) e metodo `plan_evolution_task_autonomous()`.
  * Registrazione di `FM-EVO-001` in `fmea/dfmea.yaml` e ricalcolo report FMEA.
  * Creazione delle suite di test `tests/test_marcus_data_miner.py` e `tests/test_autonomous_evolution.py`.
* **Esito Validazione:** 8/8 test unitari superati con successo in ambiente pytest. Consumo RAM rigorosamente delimitato e zero scritture a robot fermo.

---

## 📈 ECO-2026-09-30-MARCUS-001: QueryMemorySkill, Hardening MemoryInfoSkill, Fallback Ibrido RRF & Protezione Anti-Silenzio (FM-COG-004)
* **Autore:** 🤖 **Generata autonomamente da Marcus** (Antigravity Autonomous Evolution Engine)
* **Data Creazione:** 2026-09-30 17:08:00
* **Sottosistema:** `AI/Cognitive & Orchestration / RAG`
* **Stato:** ✅ **Completato e Validato in Sandbox** (Nessuna forzatura: 100% verificato)
* **Descrizione:** Risoluzione del malfunzionamento durante l'accesso e la ricerca nei dati della memoria del robot (es. richieste come *"accedi ai dati della memoria"*, *"cosa c'è nella memoria?"*, *"cosa ti ricordi?"*) che portavano al fallimento dell'interrogazione e al mutismo completo di Marcus:
  1. **Implementazione di `QueryMemorySkill`:** Creazione e registrazione della skill mancante `query_memory` per soddisfare le function calling di Gemini Live / Standard e consentire ricerche con matching ad alta confidenza ($\ge 0.95$).
  2. **Hardening di `MemoryInfoSkill`:** Rimozione del parametro non valido `formatted_document` in `SkillResult.success_result()` (causa di `TypeError`) e transizione da generatore asincrono a coroutine singola, prevenendo lo scarto del risultato in `_execute_tool_live`.
  3. **Fallback Ibrido RRF per Query Esplorative:** In `mag_hybrid_search.py`, aggiunta della ricaduta su `get_recent_episodes` e `get_all_facts` in caso di zero match FTS5 su query generali o prive di token puntuali.
  4. **Protezione Anti-Silenzio in `ConversationManager` e `SkillExecutor`:** Mappatura automatica dell'argomento `query` e iniezione di un feedback vocale di fallback garantito qualora un tool invocato non produca parlato, impedendo categoricamente che il robot resti muto.
* **Modifiche apportate:**
  * `robopy_controller/robot_ai/skills/builtin/query_memory_skill.py`: Creata la skill `QueryMemorySkill` integrata con MAG (SQLite WAL) e ChromaDB.
  * `robopy_controller/robot_ai/skills/builtin/memory_info_skill.py`: Risolto `TypeError`, incapsulato `formatted_document` in `data`, ritorno diretto di `SkillResult`.
  * `robopy_controller/robot_ai/skills/builtin/__init__.py`: Esportate `QueryMemorySkill` e `MemoryInfoSkill`.
  * `robopy_controller/robot_ai/orchestration/orchestrator.py`: Registrata `QueryMemorySkill` collegata a `MemoryManager` e `TrinityEngine`.
  * `robopy_controller/robot_ai/orchestration/skill_executor.py`: Supporto all'estrazione di `args.get("query")` e fallback a `message` se `speak` è vuoto.
  * `robopy_controller/robot_ai/orchestration/memory_manager.py`: Introdotti metodi di delega `search` e `get_recent`.
  * `robopy_controller/robot_ai/orchestration/conversation.py`: Inserita la guardia anti-silenzio a valle di `explicit_actions`.
  * `robopy_controller/robot_ai/trinity/mag_hybrid_search.py`: Fallback esplorativo automatico su episodi e fatti recenti.
  * `robopy_controller/robot_ai/services/__init__.py`: Protezione da `ImportError` su percorsi host.
  * `test/unit/test_query_memory_skill.py`: Suite di collaudo con 6 unit test dedicati.
  * `fmea/dfmea.yaml`: Aggiornato `FM-COG-004` con storico e riduzione RPN a 6.
  * `marcus_robot_guide.md`: Documentata l'interrogazione autobiografica e semantica al punto 6.
* **Esito Validazione:** 14/14 test superati con successo (100% PASSED) in `test_query_memory_skill.py`, `test_trinity_full.py`, `test_rag_acronym_memory.py`, confermando zero regressioni e conformità totale alla SPEC-05.

---

## 📈 ECO-2026-10-02-TRINITY-WORKING-MEMORY: Multi-Turn Working Memory in Metaprompt & Fast-Path Cognitive Guard
* **Stato:** ✅ **Completato, Testato e Validato con Non-Regressione**
* **Descrizione:** Risoluzione dell'amnesia conversazionale turno-per-turno ("effetto Parkinson") e dell'hijacking indebito delle domande conversazionali di memoria da parte di `QueryMemorySkill` (FM-TRI-008).
* **Cause Radice:**
  1. Assenza della sezione di cronologia recente nel prompt di `MetapromptFusion` e mancata propagazione dei turni precedenti da `ConversationManager`.
  2. Matching avido ($\ge 0.95$) su frasi colloquiali in `QueryMemorySkill.match()`, scatenando l'esecuzione deterministica fast-path che bypassava completamente il cervello LLM e rispondeva con il solo testo preimpostato dell'acronimo MARCUS.
* **Modifiche apportate:**
  * `robopy_controller/robot_ai/trinity/metaprompt_fusion.py`:
    - Aggiunta la sezione `[CONVERSAZIONE RECENTE]` e budget dedicato `BUDGET_DIALOGUE = 350` token.
    - Ricalibrati i budget di sezione (`SYSTEM=200`, `CAG=350`, `MAG=450`, `RAG=600`, `DIALOGUE=350`, `USER=200`) per un target di 2150 token, strettamente sotto il vincolo assoluto di 2500 token (SPEC-05 Zona Rossa).
  * `robopy_controller/robot_ai/trinity/trinity_engine.py`:
    - Accettazione e formattazione della lista `conversation_history` in `build_augmented_prompt()`.
  * `robopy_controller/robot_ai/orchestration/conversation.py`:
    - Inizializzato il buffer di Working Memory `conversation_history` (ultimi 10 turni = 5 scambi).
    - Esclusione esplicita di `query_memory`, `memory_info`, `consult_antigravity`, `consult_documentation` dal fast-path (riservato per legge fisica all'arresto di emergenza).
    - Propagazione della cronologia dialogica a `trinity_engine.build_augmented_prompt()` e al fallback `_build_prompt()`.
    - Registrazione post-risposta del turno (`user` e `assistant`) in `conversation_history`.
  * `robopy_controller/robot_ai/skills/builtin/query_memory_skill.py`:
    - Rimosse le trigger phrase colloquiali ("ti ricordi...", "cosa ricordi...") da `match()`, limitando la skill a richieste amministrative esplicite con confidenza $\le 0.85$.
    - Rimosso il fallback cieco su tutti i fatti del DB quando una query specifica FTS5 non produce riscontri.
    - Se esistono sia fatti che episodi/interazioni rilevanti, entrambi vengono riportati e inclusi nella risposta vocale.
  * `robopy_controller/robot_ai/trinity/mag_episodic.py` e `mag_zettelkasten.py`:
    - Soppressi header orfani (`RECENT EPISODES:`, `ZETTELKASTEN FACTS:`) in assenza di dati effettivi.
  * `tests/test_trinity_full.py`:
    - Aggiunti 3 test di non-regressione: `test_metaprompt_fusion_dialogue_working_memory`, `test_trinity_engine_conversation_history_propagation`, `test_query_memory_skill_not_hijacking_conversational_queries` (8/8 PASSED).
  * `fmea/dfmea.yaml`:
    - Registrato `FM-TRI-008` (RPN iniziale 144 -> residuo 8) e rigenerato il report esecutivo FMEA.
* **Esito Validazione:** 8/8 test superati in `test_trinity_full.py` e 71/71 in `test_challenger_m4_dialogue.py`.

---

## 📈 ECO-2026-10-03-TRINITY-TEMPORAL-MEMORY: Datatura Ricordi Autobiografici (MAG), Parser Temporale & Statistiche di Frequenza
* **Stato:** ✅ **Completato, Testato e Validato con Non-Regressione (12/12 PASS)**
* **Descrizione:** Risoluzione dell'amnesia temporale e dell'incapacità di collocare cronologicamente eventi e ricordi autobiografici o rispondere a domande sulle date e frequenze (FM-TRI-009).
* **Cause Radice:**
  1. I metodi di sintesi del metaprompt (`to_prompt_sections` in `mag_episodic.py` e `to_prompt_section` in `mag_zettelkasten.py`) scartavano il timestamp float registrato in SQLite WAL (`episodes.timestamp`), fornendo all'LLM stringhe prive di riferimenti a giorni, orari o date. Di conseguenza, l'LLM rispondeva all'utente che il robot non mappava le date esatte dei propri ricordi.
  2. Ricerca FTS5 cieca sulle parole temporali ("ieri", "2 ottobre", "oggi") che restituiva 0 risultati in quanto tali vocaboli non facevano parte del testo della conversazione passata. Mancava un parser temporale in linguaggio naturale e metodi di query su range temporali in SQLite WAL.
  3. Assenza di metodi per il calcolo aggregato delle statistiche di frequenza degli episodi.
* **Modifiche apportate:**
  * Creato `robopy_controller/robot_ai/trinity/mag_temporal_parser.py`:
    - Parser deterministico di espressioni temporali italiane ("oggi", "ieri", "l'altro ieri", "ultimi N giorni", "questa settimana", date numeriche `DD/MM/YYYY` e nominali `2 ottobre`) che produce intervalli $[start\_timestamp, end\_timestamp]$.
    - Funzione `clean_temporal_tokens` per isolare le parole chiave semantiche da cercare all'interno della finestra temporale.
    - Metodo `is_frequency_or_stats_query` per intercettare richieste di conteggio o frequenza.
  * Modificato `robopy_controller/robot_ai/trinity/mag_database.py`:
    - Aggiunti `get_episodes_by_timerange(start_time, end_time, limit, user_id)` e `get_facts_by_timerange(start_time, end_time, limit)`.
    - Aggiunto `get_episodes_frequency_stats(days)` con raggruppamento per giorno `date(timestamp, 'unixepoch', 'localtime')`.
  * Modificato `robopy_controller/robot_ai/trinity/mag_hybrid_search.py`:
    - Integrata la decodifica delle espressioni temporali per filtrare e raggruppare prioritariamente gli episodi dell'intervallo richiesto.
  * Modificato `robopy_controller/robot_ai/trinity/mag_episodic.py`:
    - Formattazione di ciascun episodio con timestamp leggibile: `- [DD/MM/YYYY HH:MM] Q: ... | A: ...` localizzato `Europe/Rome`.
  * Modificato `robopy_controller/robot_ai/trinity/mag_zettelkasten.py`:
    - Formattazione di ciascun fatto semantico con data di creazione: `- [FACT_TYPE | DD/MM/YYYY] ...`.
  * Modificato `robopy_controller/robot_ai/trinity/trinity_engine.py`:
    - Aggiunta la formattazione con data e ora per i ricordi conversazionali e fatti appresi recuperati da ChromaDB.
  * Modificato `robopy_controller/robot_ai/trinity/metaprompt_fusion.py`:
    - Posizionata `[DATA E ORA ATTUALE: {timestamp}]` in testa al metaprompt.
    - Istruzione esplicita in `[MEMORIA STORICA (MAG)]`: i ricordi hanno data e ora esatta da usare per collocare eventi passati ed eseguire analisi di frequenza.
  * Modificato `robopy_controller/robot_ai/skills/builtin/query_memory_skill.py`:
    - Gestione esplicita di query di verifica ("la tua memoria mappa le date?").
    - Gestione delle richieste di frequenza e statistiche con ritorno strutturato dei dati aggregati.
    - Formattazione vocale e Markdown contenente date e orari precisi per ogni fatto ed episodio.
  * Modificato `tests/test_trinity_full.py`:
    - Aggiunti 4 test unitari per parser temporale, query range e frequenza, formattazione date nei prompt e gestione skill (12/12 PASSED).
  * Modificato `fmea/dfmea.yaml`:
    - Registrato `FM-TRI-009` (RPN iniziale 168 -> residuo 7), eseguito `calculate_and_report_fmea.py`.
* **Esito Validazione:** 12/12 in `test_trinity_full.py` e 71/71 in `test_challenger_m4_dialogue.py` (83 test complessivi superati).




