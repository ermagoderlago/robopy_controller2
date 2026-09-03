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


