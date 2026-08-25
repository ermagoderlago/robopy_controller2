# Progetto di Miglioramento IMP-NOM-001 — Pipeline NoMaD v2 Reattiva

## 1. Informazioni Generali
- **ID Progetto:** IMP-NOM-001
- **Failure Mode Associati:** FM-NOM-001, FM-NOM-002, FM-NOM-003, FM-NOM-004, FM-NOM-005
- **Dominio:** `nav2_slam`
- **Stato:** `COMPLETED` (Codice e test implementati)
- **Data Inizio:** 2026-08-24
- **Autore:** Senior Principal Robotics & Edge AI Architect

---

## 2. Descrizione Tecnica e Rationale
Sostituzione dell'implementazione sperimentale di NOMAD con un nodo reattivo production-grade (`nomad_reactive_pipeline_node.py`):
1. **Core Pinning:** CPU affinity su Cores 2, 3 per isolare il carico ONNX / Python da CycloneDDS e kernel (Cores 0, 1).
2. **Hybrid Inference Engine:** ViNT backbone NPU + DDIM 4-step su ONNX Runtime (`intra_op_num_threads=2`, `inter_op_num_threads=1`).
3. **Multi-Tier Fallback:** In caso di 2 timeout consecutivi (>100ms), commutazione immediata ad Action Chunking MLP (<5ms) o propagazione waypoint con filtro EMA.
4. **Adaptive EMA Smoothing:** Filtro vettorializzato $\alpha=0.3$ con boost a $\alpha=0.7$ in caso di delta-heading $>30^\circ$.
5. **Pure Pursuit Integrato:** Controllo cinematico locale che pubblica direttamente su `/cmd_vel_nomad` e visualizzazione su `/nomad/path_smoothed`.
6. **Watchdog 300ms:** Arresto immediato con zero-velocity in caso di stallo pipeline.

---

## 3. Matrice di Riduzione Rischio FMEA

| Failure Mode | RPN Iniziale | RPN Residuo | Riduzione | Stato |
|:---|:---:|:---:|:---:|:---:|
| **FM-NOM-001** (DDIM latency spike) | 126 | **36** | -71% | MITIGATED |
| **FM-NOM-002** (ViNT-DDIM desync) | 140 | **28** | -80% | MITIGATED |
| **FM-NOM-003** (MLP fallback degradation) | 100 | **30** | -70% | MITIGATED |
| **FM-NOM-004** (EMA over-smoothing) | 150 | **36** | -76% | MITIGATED |
| **FM-NOM-005** (Watchdog false positive) | 72 | **16** | -78% | MITIGATED |

---

## 4. Validazione e Test
- **Unit Tests:** `test/unit/test_nomad_reactive_pipeline.py` (16 test cases).
- **Integration Tests:** `test/integration/test_nomad_vpr_integration.py`.
- **HIL Verification:** Target latenza DDIM < 95ms su Raspberry Pi 5.
