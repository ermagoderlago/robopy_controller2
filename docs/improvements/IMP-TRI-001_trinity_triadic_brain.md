# Progetto di Miglioramento: IMP-TRI-001 — Architettura Triadica TRINITY (RAG + CAG + MAG)

* **Failure Mode Associato:** `FM-TRI-001`, `FM-TRI-002`, `FM-TRI-003`, `FM-TRI-004`, `FM-TRI-005`, `FM-TRI-006`, `FM-TRI-007`
* **Dominio:** `cognitive_ai` / `orchestration_and_rag`
* **Stato:** `COMPLETED`
* **Data Creazione:** 2026-08-21
* **Data Chiusura:** 2026-08-21

---

## 🎯 Obiettivo del Progetto
Evolvere il cervello cognitivo di Marcus implementando simultaneamente e armoniosamente tre paradigmi di memoria e contesto:
1. **RAG (Retrieval-Augmented Generation):** Accesso e indicizzazione di documentazione esterna, datasheet hardware, librerie e codice sorgente Python locale.
2. **CAG (Context-Augmented Generation):** Context awareness in tempo reale dello stato hardware (CPU, RAM, temperatura, batteria, rete), topologia ROS 2, ring buffer degli errori recenti, e snapshot ambiente.
3. **MAG (Memory-Augmented Generation):** Memoria autobiografica ed episodica a lungo termine su database atomico SQLite in modalità WAL, con Zettelkasten di fatti semantici e profili utente persistenti.

---

## 🛠️ Moduli Implementati

1. **`trinity/intent_router.py`**: Classificatore leggero di query con routing selettivo dei pesi dei sotto-moduli.
2. **`trinity/rag_document_indexer.py`**: Chunker AST e indicizzatore di file `.py`, `.md`, `.yaml`, `.pdf` su collection `marcus_knowledge_base`.
3. **`trinity/rag_knowledge_query.py`**: Ricerca semantica documentale con reranking per snippet di codice.
4. **`trinity/cag_hardware_collector.py`**: Telemetria SoC, RAM, carico CPU, temperatura e stato interfacce.
5. **`trinity/cag_ros_inspector.py`**: Monitoraggio nodi, topic e albero TF ROS 2.
6. **`trinity/cag_error_tracker.py`**: Tracciamento ring buffer degli ultimi 5 errori/traceback.
7. **`trinity/cag_environment.py`**: Snapshot semantico dell'ambiente (stanze, persone, oggetti, Home Assistant).
8. **`trinity/cag_aggregator.py`**: Aggregatore con TTL cache a 5.0s per minimizzare CPU overhead sul Pi 5.
9. **`trinity/mag_database.py`**: Database relazionale SQLite con modalità WAL e FTS5 full-text.
10. **`trinity/mag_zettelkasten.py`**: Gestore fatti atomici con deduplicazione (cosine > 0.85).
11. **`trinity/mag_user_profile.py`**: Tracciamento persistente delle preferenze utente.
12. **`trinity/mag_hybrid_search.py`**: Ricerca ibrida con Reciprocal Rank Fusion (RRF).
13. **`trinity/mag_episodic.py`**: Motore di memoria autobiografica episodica.
14. **`trinity/mag_dream_consolidation.py`**: Consolidamento notturno delle memorie nel sogno.
15. **`trinity/metaprompt_fusion.py`**: Assemblatore del metaprompt strutturato con token budget rigido.
16. **`trinity/trinity_engine.py`**: Orchestratore principale con recupero parallelo `asyncio.gather`.

---

## 🔒 Rispetto dei Vincoli Fisici (Raspberry Pi 5)
* **RAM:** Budget rigoroso < 100MB per MAG SQLite (cache 2000 pagine), float16 embedding packing.
* **CPU:** Caching a 5.0s in `ContextAggregator` per prevenire polling continui del filesystem.
* **Sicurezza Dati:** SQLite in modalità WAL per resistere a shutdown improvvisi (anti-battery cliff).

---

## 🧪 Validazione e Test
* Creata la test suite `tests/test_trinity_full.py` per testare la classificazione intent, il token budgeting, il CRUD SQLite/FTS5, il CAG snapshotting e l'integrazione asincrona in `TrinityEngine`.
