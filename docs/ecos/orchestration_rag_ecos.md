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

