# 🛠️ Progetto di Miglioramento IMP-SYS-008
> **Target Failure Mode:** `FM-SYS-008` (OOM Crash da Memory Creep su 4GB RAM e Riavvio Distruttivo da Watchdog Bash)  
> **Priorità RPN Iniziale:** 448 -> **RPN Residuo:** 24 | **Stato:** COMPLETED | **Dominio:** Cloud, Memory & Orchestration (System Lifecycle)

---

## 1. Analisi del Problema & Cause Radice

### Problema
Su Raspberry Pi 5 con 4GB di RAM host, la coesistenza simultanea di ROS 2 Nav2, RTAB-Map SLAM, ChromaDB, inferenza NPU e pipeline audio VUI comportava un rischio elevato di accumulo incontrollato di memoria condivisa e code DDS (*memory creep*). I riavvii eseguiti tramite watchdog bash script (`pkill -9`) erano distruttivi, interrompendo bruscamente le missioni in corso e cancellando la continuità operativa del robot.

### Soluzione Implementata (System Lifecycle Coordinator & Memory Pressure Sentinel)
1. **Memory Pressure Sentinel (Kernel Linux PSI):**
   - Monitoraggio continuo a 2 Hz di `/proc/pressure/memory` con parsing metrico `some avg10` e `full avg10`.
   - **Soglia Warning (`full avg10 >= 0.30` o RAM > 3.4 GB):** Congelamento immediato (`freeze_embeddings()`) dell'accodamento di nuovi vettori e drop delle elaborazioni pesanti in `memory_manager.py`.
   - **Soglia Critical (`full avg10 >= 0.60` o RAM > 3.75 GB):** Eviction forzata dei buffer e delle cache transitorie (`clear_transient_buffers()`), forzando `gc.collect()` e liberazione delle pagine heap al kernel con `ctypes.CDLL('libc.so.6').malloc_trim(0)`.
2. **Macchina a Stati del Ciclo di Vita (`OperatingState`):**
   - **`NAVIGATION_ACTIVE`:** VIO, SLAM (RTAB-Map a 1.5 Hz nominali) e Nav2 MPPI operano a piena banda CPU/RAM; i servizi di analisi notturna e daydreaming (`nightly_dream_service`) vengono sospesi (`suspend()`).
   - **`DOCKED_DREAM`:** Attivato al rientro sulla stazione di ricarica ($V \ge 12.65\text{V}$ o stato `DOCKED`); lo stack Nav2 e VIO vengono messi in pausa/inattivi liberando risorse per l'elaborazione del consolidamento notturno dei log e l'inferenza DeepSeek.
   - **`HUMAN_INTERACTION_MODE`:** All'intercettazione del wake word o durante conversazioni attive, la frequenza di RTAB-Map viene ridotta a **0.25 Hz** (1 frame ogni 4 secondi) e il nodo audio VUI (`respeaker_vui_node`) riceve un boost di priorità real-time (`os.sched_setscheduler` `SCHED_RR` / `os.nice(-10)`), azzerando glitch e latenze di risposta.

---

## 2. File Modificati & Creati
- `robopy_controller/nodes/system_lifecycle_coordinator_node.py` (Nuovo Coordinatore & Sentinel)
- `robopy_controller/robot_ai/orchestration/memory_manager.py` (Freeze & Eviction buffer)
- `robopy_controller/robot_ai/services/nightly_dream_service.py` (Suspend / Resume lifecycle)
- `robopy_controller/robot_ai/orchestration/orchestrator.py` (Sottoscrizioni /system/operating_state, /system/memory_freeze, /system/emergency_evict)
- `robopy_controller/nodes/respeaker_vui_node.py` (Boost priorità real-time scheduler)
- `setup.py` (Registrazione entry point)
- `restart_hailo.sh` (Avvio sequenziale del coordinator)
- `test/unit/test_memory_pressure_sentinel.py` (Suite di test unitari 7/7 PASS)
