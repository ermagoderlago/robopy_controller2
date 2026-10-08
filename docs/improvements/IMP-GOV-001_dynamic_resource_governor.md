# IMP-GOV-001: Dynamic Resource Governor, Pose su Hailo-10H e Biometria Residente

## 🎯 Obiettivo del Progetto
Introdurre un **Resource Governor deterministico** che modula frequenze di esecuzione, priorità dello scheduler HailoRT e stato dei lifecycle node in funzione dello stato cinematico e cognitivo di Marcus, spostando contestualmente il Pose Tracking dalla OAK-D Lite all'Hailo-10H e riattivando la biometria (SCRFD + ArcFace, ECAPA-TDNN).

**Principio guida:** *residenti sempre, governati in frequenza, guidati dagli eventi.* Il Governor **non scarica** i modelli piccoli, **non spegne** il pavimento uditivo e **non comanda moto**.

Piano architetturale completo (discussione RE/QE/SME, statechart, sequenze, telemetria): artifact `resource_governor_architecture_plan.md` della conversazione del 2026-10-07.

---

## ⚡ Failure Modes Mitigati (DFMEA)
| ID | Sintesi | RPN iniziale → residuo |
| :--- | :--- | :--- |
| FM-GOV-001 | Qwen2-VL monopolizza l'NPU in moto | 240 → 32 |
| FM-GOV-002 | Biometria scaricata all'arrivo di voce/volto | 336 → 28 |
| FM-GOV-003 | Speaker ID sul solo wake word | 294 → 63 |
| FM-GOV-004 | Picco RAM host in transizione | 180 → 36 |
| FM-GOV-005 | Spinta con rotolamento in sleep | 112 → 28 |
| FM-GOV-006 | Kidnapping / slittamento laterale in sleep | 168 → 48 |
| FM-GOV-007 | Scan stantii e ghost in costmap al risveglio | 144 → 24 |
| FM-GOV-008 | RTAB-Map ripreso in SLAM dopo salto odometrico | 192 → 16 |
| FM-GOV-009 | Sordità selettiva (mic/VAD/KWS spenti) | 350 → 20 |
| FM-GOV-010 | KWS Vosk perde frame sotto carico | 210 → 42 |
| FM-GOV-011 | Flapping di stato | 90 → 20 |
| FM-GOV-012 | Crash del Governor | 135 → 18 |
| FM-GOV-013 | Arbitri multipli in conflitto | 294 → 28 |
| FM-GOV-014 | Crash proprietario VDevice / PCIe | 63 → 42 |
| FM-GOV-015 | Throttling termico | 90 → 24 |
| FM-GOV-016 | Decode pose in Python su Core 0-1 | 144 → 24 |
| FM-GOV-017 | Rotazione automatica senza LLM | 96 → 16 |

---

## 🔒 Invarianti di Design (non negoziabili)
1. **Pavimento uditivo sempre attivo** (cattura, ring buffer ≥ 5 s, VAD, KWS "Marcus", KWS stop-words, ECAPA): il Governor non espone servizi per spegnerlo (regola 6.1).
2. **Modelli piccoli residenti** su NPU/CPU; si governa solo la frequenza.
3. **Fail-safe = SAFE_FULL**: frequenze nominali, Nav2 inattivo, motion gate chiuso.
4. **Autorità unica**: Memory Sentinel = sensore, Sensor Standby Manager = attuatore.
5. **Nessun accesso a `/cmd_vel`** dal Governor.
6. **LiDAR fermo solo in DOCKED_SLEEP**; sensor sleep vietato in `--slam` fuori dal dock.
7. **Consistency check scan-to-map ad ogni risveglio verso il moto**, indipendentemente dall'IMU.
8. **Qwen2-VL solo in STATIONARY**, previo superamento del Gate G0.

---

## 🏗️ Fasi e Gate
| Fase | Contenuto | Zona | Stato |
| :--- | :--- | :--- | :--- |
| F0 | Baseline misure (CPU/RAM/PSI/NPU/tempi configure HEF) | 🟢 | ✅ COMPLETATO (Report F0) |
| G0 | Coesistenza GenAI + InferModel sul VDevice | 🟡 SPEC-03 §5.4 | ✅ COMPLETATO (Interlock STATIONARY) |
| F1 | Pose su Hailo (HEF + decode C++ nel bridge, decoupling OAK) | 🟡 SPEC-03 §5.1 | ✅ DEPLOYED & VERIFICATO SU HW |
| F2 | Biometria residente + identity tracker multimodale | 🟡 interfacce ROS | ✅ DEPLOYED & VERIFICATO SU HW |
| F3 | VAD Energy-Gated KWS + stop-words (<1% CPU in idle) | 🟢 SPEC-04 | ✅ IMPLEMENTATO & TESTATO (5/5 unit test OK) |
| F4 | Governor in shadow mode (Statechart ortogonale + Invariants) | 🟡 interfacce ROS | ✅ DEPLOYED & ATTIVO IN SHADOW MODE |
| F5 | Governor attivo (frequenze + lifecycle Nav2) | 🟡 | — |
| F6 | Stato LOCAL_VLM (Interlock Qwen2-VL, break-before-make) | 🟡 SPEC-03 | ✅ IMPLEMENTATO & TESTATO |
| F7 | Integrazione sleep in dock (sensor_standby_manager attuatore) | 🟢 range SPEC-02 | ✅ IMPLEMENTATO & TESTATO |

---

## 📋 Piano di Verifica e Test
- **L1:** test basati su proprietà della statechart (invarianti 1, 3, 5, 8 mai violati su sequenze casuali di eventi).
- **L2 Gazebo:** SIM-01..08 (spinta, slittamento, kidnapping senza firma IMU, cross-room, ostacolo in spin-up, flapping, kill Governor, modalità SLAM); Monte Carlo ≥ 100 casi.
- **L3 HIL:** corpus audio 8 direzioni × 3 distanze in ogni stato/transizione; contesa NPU con Qwen; soak termico 30 min; fault injection HailoRT/OAK/WebSocket/RAM.
- **KPI di accettazione:** recall KWS ≥ 95 %; ID vocale provvisorio ≤ 300 ms; ID volto ≤ 150 ms; STATIONARY→MOVING ≤ 3 s; nessun gap YOLO > 200 ms in MOVING; 0 OOM; 0 swap-in in MOVING; 0 flag di throttling.
