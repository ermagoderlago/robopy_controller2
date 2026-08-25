# Progetto di Miglioramento IMP-VPR-001 — VPR Topologico con CosPlace e Grafo SQLite/NetworkX

## 1. Informazioni Generali
- **ID Progetto:** IMP-VPR-001
- **Failure Mode Associati:** FM-VPR-001, FM-VPR-002, FM-VPR-003, FM-VPR-004
- **Dominio:** `vision_hailo`
- **Stato:** `COMPLETED` (Codice e test implementati)
- **Data Inizio:** 2026-08-24
- **Autore:** Senior Principal Robotics & Edge AI Architect

---

## 2. Descrizione Tecnica e Rationale
Creazione del modulo di Visual Place Recognition (VPR) e gestione della memoria topologica a lungo termine (`vpr_topological_graph_node.py`):
1. **Odometric Triggering:** Generazione keyframe solo su reale avanzamento ($\Delta d > 0.8\text{ m}$ o $\Delta \theta > 45^\circ$), throttled a $\ge 2.0\text{ s}$.
2. **CosPlace 512D Descriptor:** Estrazione embedding su Hailo-10H (Network Group B) con timeout 40ms e fallback CPU ONNX.
3. **ChromaDB 512D Collection:** Collection `vpr_embeddings` persistita su NVMe con validazione dimensionale a 512D.
4. **Multi-Stage Loop Closure Filtering:**
   - Temporale: $\ge 5$ keyframe di distanza.
   - Similarità: similarità cosenica $> 0.84$.
   - Geometrico: distanza euclidea odometrica $> 3.0\text{ m}$.
5. **Topological Graph:** Database SQLite WAL (`topological_graph.db`) sincronizzato con grafo in-memory NetworkX.
6. **10k Nodes Pruning:** All'inserimento del 10.001-esimo nodo, sfoltimento automatico dei nodi densi ($< 0.3\text{ m}$) con minor connettività.

---

## 3. Matrice di Riduzione Rischio FMEA

| Failure Mode | RPN Iniziale | RPN Residuo | Riduzione | Stato |
|:---|:---:|:---:|:---:|:---:|
| **FM-VPR-001** (NPU context switch timeout) | 140 | **28** | -80% | MITIGATED |
| **FM-VPR-002** (Aliasing percettivo) | 288 | **72** | -75% | MITIGATED |
| **FM-VPR-003** (ChromaDB 512D corruption) | 224 | **16** | -93% | MITIGATED |
| **FM-VPR-004** (SQLite WAL contention) | 60 | **16** | -73% | MITIGATED |

---

## 4. Validazione e Test
- **Unit Tests:** `test/unit/test_vpr_topological_graph.py` (16 test cases).
- **Integration Tests:** `test/integration/test_nomad_vpr_integration.py`.
- **HIL Verification:** Percorso a loop chiuso (~15m) con trigger evento su `/vpr/loop_closure_event`.
