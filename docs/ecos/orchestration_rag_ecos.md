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
