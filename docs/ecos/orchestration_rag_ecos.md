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

