# 🖥️ SPEC-07: Sistema Operativo Pi 5 (4GB RAM), Build & Lifecycle

## 1. Identificazione e Scopo
- **ID Specifica:** `SPEC-07`
- **Ambito:** Gestione delle risorse fisiche del Raspberry Pi 5 (4GB RAM), disciplina del build system sequenziale, prevenzione dell'OOM Killer del kernel Linux, gestione del ciclo di vita dei nodi ROS 2 e salvaguardia del supporto SSD NVMe.
- **Nodi & Moduli ROS 2:**
  - `robopy_controller.nodes.system_lifecycle_coordinator_node`
  - `robopy_controller.nodes.cloud_watchdog_node`
  - `robopy_controller.nodes.performance_monitor`
- **Script di Build, Deploy & Sistema:**
  - `sync_marcus.sh`, `compile_wsl.sh`, `restart.sh`, `restart_hailo.sh`, `CMakeLists.txt`, `setup.py`, `package.xml`
  - `/etc/udev/rules.d/99-marcus-serial.rules` (Mappatura deterministica porte seriali USB CP2102N per RPLIDAR e Motor Driver)
  - `/home/robopy/lidar_ws` (Workspace isolato driver C1 compilato con `MAKEFLAGS="-j1"`)
- **Hardware Diretto:** Raspberry Pi 5 (Broadcom BCM2712 Quad-core Cortex-A76 @ 2.4GHz, 4GB LPDDR4X SDRAM, HAT PCIe NVMe SSD).
- **DFMEA Correlati:** `FM-SYS-001` (OOM Kill indotto dal compilatore), `FM-SYS-002` (Exec format error BOM UTF-8), `FM-SYS-008` (RAM Pressure e coordinamento lifecycle), `FM-MOT-004` (Conflitto enumerazione porte USB CP2102N).

---

## 2. Architettura della Gestione Risorse

```mermaid
graph TD
    subgraph "Raspberry Pi 5 (4GB RAM)"
        subgraph "CPU Core 0 & Core 1"
            KERNEL["Kernel Linux & Interrupt I/O"]
            DDS["ROS 2 Middleware DDS & Network"]
            VUI["respeaker_vui_node & Python Tasks"]
        end
        
        subgraph "CPU Core 2 & Core 3 (Pinned)"
            MAP["marcus_semantic_mapper_node (C++)"]
            VIO["fast_flow_vo_node (C++)"]
            HAILO["hailo_bridge_node (C++)"]
        end
        
        SENTINEL["system_lifecycle_coordinator_node (Memory Sentinel)"]
        WATCHDOG["marcus-watchdog (Systemd Daemon)"]
    end
    
    SENTINEL -->|RAM > 75% (3.0 GB)| GC["Trigger Garbage Collection & Truncate Caches"]
    SENTINEL -->|RAM > 85% (3.4 GB)| SHED["Load Shedding: Stop VPR / NetVLAD & Lazy Publishing"]
    WATCHDOG -->|Process Freeze > 10s| RESTART["Restart Servizio Systemd"]
```

---

## 3. 🔴 ZONA ROSSA (Inviolabili - NO AUTONOMOUS TOUCH)

Le prescrizioni di questa sezione sono assoluti fisici. La loro inosservanza provoca l'arresto immediato del Raspberry Pi 5 o l'impossibilità di compilare ed eseguire il software.

| Vincolo di Risorsa / Compilazione | Regola Inviolabile | Rischio Ingegneristico | DFMEA |
| :--- | :--- | :--- | :--- |
| **Tetto Massimo RAM Host** | **4.0 GB RAM fisica** utilizzabile | OOM Kill casuale di nodi vitali (motori, VUI, SLAM) | FM-SYS-008 |
| **Flag di Compilazione Obbligatori**| `MAKEFLAGS="-j1" colcon build --parallel-workers 1` | Esaurimento RAM durante il link C++ e freeze completo | FM-SYS-001 |
| **Ottimizzazione CPU Pi 5** | `-O3 -mcpu=cortex-a76+crypto` obbligatorio | Perdita fino al 40% di throughput vettoriale SIMD NEON | FM-SYS-001 |
| **Arresto Preventivo Nodi** | `sudo systemctl stop marcus-watchdog` e pkill prima di build | Concorrenza di memoria tra runtime e compilatore | FM-SYS-001 |
| **BOM UTF-8 (`\xEF\xBB\xBF`)** | Divieto assoluto di caratteri BOM nei file sorgente | `OSError: [Errno 8] Exec format error` all'avvio ROS 2 | FM-SYS-002 |
| **Pinning Core CPU** | Nodi C++ vincolati ai Core 2 e 3; I/O su Core 0 e 1 | Starvation dei thread DDS e perdita pacchetti seriali | FM-VIS-006 |
| **Politica di Scrittura su SSD** | Divieto di log non compressi o print continui a disco | Usura prematura delle celle flash e degrado I/O | FM-SYS-004 |

---

## 4. 🟢 ZONA VERDE (Auto-Evolution - MIGLIORAMENTO AUTONOMO CONSENTITO)

L'agente Antigravity può ottimizzare e ricalibrare autonomamente le seguenti componenti:

| Area di Ottimizzazione | Metodo & Logica Ammessa | Range & Vincoli di Accettazione |
| :--- | :--- | :--- |
| **Soglie Memory Sentinel** | Calibrazione dei trigger soft e hard per il recupero RAM | Soft: $70\%\text{-}78\%$; Hard: $82\%\text{-}88\%$ della RAM totale |
| **Pulizia Cache Dinamica** | Svuotamento buffer non utilizzati e forced Python GC | Invocazione solo durante periodi di inattività o idle |
| **Target CMakeLists.txt** | Rimozione include ridondanti o librerie C++ non linkate | Riduzione tempi di build senza alterare i binari finali |
| **Watchdog Heartbeat Rate** | Frequenza di ping telemetrico verso il cloud watchdog | Frequenza: $T_{ping} \in [5\text{ s}, 15\text{ s}]$; default: $10\text{ s}$ |
| **Ottimizzazione Python Imports** | Lazy loading di librerie pesanti (es. torch, cv2, scipy) | Caricamento solo al primo utilizzo effettivo nel nodo |

---

## 5. 🟡 ZONA GIALLA (Human-in-the-Loop - APPROVAZIONE UMANA OBBLIGATORIA)

Le seguenti modifiche richiedono proposta formale e validazione dell'operatore umano:

1. **Aggiunta Dipendenze ROS 2 o di Sistema:** Modifica dei file `package.xml`, `setup.py` o introduzione di librerie installabili via `apt`.
2. **Modifica Configurazioni Systemd:** Variazione dei file di unità servizio (`/etc/systemd/system/marcus-*.service`).
3. **Parametri di Boot Kernel Linux:** Modifiche a `/boot/firmware/cmdline.txt` o `/boot/firmware/config.txt` (es. overclock, swap size, PCIe config).
4. **Aggiornamento ROS 2 Core Distribution:** Transizione di versione del middleware ROS 2.

---

## 6. Procedura di Verifica & Test di Non-Regressione

Prima di confermare modifiche all'infrastruttura di compilazione o al monitoraggio di sistema, l'agente DEVE eseguire con successo:

```bash
# 1. Verifica assenza BOM UTF-8 su tutti gli script Python e file shell
pytest tests/test_system_bom_cleanliness.py -v

# 2. Test unitario del System Lifecycle Coordinator e logiche di load shedding
pytest tests/test_lifecycle_coordinator.py -v

# 3. Test di conformità del comando di compilazione in sync_marcus.sh
bash -c "grep -q 'MAKEFLAGS=\"-j1\"' sync_marcus.sh"

# 4. Verifica della stabilità della memoria sotto stress mock
pytest tests/test_memory_leak_guard.py -v
```
I test devono confermare:
- Totale assenza di byte mark non-ASCII all'inizio degli script interpretati.
- Riconoscimento immediato del picco di RAM da parte del Memory Sentinel con rilascio risorse misurato.
- Perfetta sequenzialità e isolamento dei job di build.
