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
