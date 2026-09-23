# Marcus Core Rules - Nucleo Residente (V2)

Questo documento contiene le **Regole d'Oro** e i **vincoli fisici assoluti** per il robot Marcus. È l'unico file di documentazione globale destinato a rimanere costantemente nel contesto di programmazione dell'IA.

---

## ⚡ Regole d'Oro e Vincoli Fisici Assoluti

Per garantire la sopravvivenza hardware, la fluidità di esecuzione ed evitare il collasso del sistema su Raspberry Pi 5, attenersi rigorosamente ai seguenti vincoli architetturali:

### 1. Memoria RAM, Procedura di Compilazione ed Ottimizzazione Host Pi 5
* **Tetto massimo RAM:** Il Raspberry Pi 5 host dispone di **4GB di RAM** utilizzabili.
* **Arresto Preventivo Obbligatorio dei Nodi e del Watchdog:** **PRIMA di avviare qualsiasi compilazione (`colcon build`) sul robot, DEVI fermare tutti i nodi attivi ed il watchdog** (`sudo systemctl stop marcus-watchdog`, `pkill -9 -f watchdog.sh`, arresto dei processi Python/ROS 2). In caso contrario, l'elevato consumo di RAM/CPU dei nodi in esecuzione causa l'OOM Kill indotto dal compilatore con conseguente fallimento della build.
* **Compilazione Sequenziale & Ottimizzata Raspberry Pi 5 (`-O3` & Cortex-A76):** Deve essere eseguita in modo **sequenziale** con ottimizzazione Release per la CPU del Pi 5 (Cortex-A76):
  * **Comando obbligatorio:**
    ```bash
    MAKEFLAGS="-j1" colcon build --parallel-workers 1 --cmake-args -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-O3 -mcpu=cortex-a76+crypto" ...
    ```
* **BOM Warning:** Rimuovere il Byte Order Mark (BOM) UTF-8 (`\xEF\xBB\xBF`) dagli script prima del lancio per evitare `OSError: [Errno 8] Exec format error`.

### 2. Vincolo di Mappatura e SLAM (No STVL)
* **Divieto di STVL:** È **severamente vietato** utilizzare la mappatura volumetrica continua 3D (Spatiotemporal Voxel Layer - STVL) a causa dell'overhead insostenibile su CPU e memoria.
* **Filtro Semantico 2.5D:** Utilizzare esclusivamente la proiezione geometrica 3D ➔ 2D su griglia locale con decadimento temporale (implementata in `semantic_costmap_injector.py`).

### 3. Pipeline VUI e Audio PCM Streaming
* **Frequenza di campionamento fissa:** Lo streaming audio bidirezionale (Client ↔ Gemini Live API WebSocket) deve operare a **16kHz mono PCM** a 16-bit.
* **Hardware DAC a 48kHz:** PyAudio deve aprire lo stream al rate nativo dell'hardware (rilevato da `_out_hw_rate`) ed eseguire il resampling `16kHz ➔ 48kHz` tramite `audioop.ratecv` prima della riproduzione, per evitare l'effetto "Chipmunk/Darth Vader".
* **Isolamento ALSA/PipeWire:** PyAudio blocca il device audio in modalità esclusiva. I player esterni (come Spotify/raspotify) non devono competere per il device hardware locale se non tramite routing di rete o audio secondario.
* **Peak Limiter / AGC:** Attenuazione software in tempo reale nel thread di cattura VUI per evitare clipping da boost digitali (`stt_gain` ridotto a 0.1x durante il TTS per barge-in sicuro).

### 4. Gestione CPU e Core Pinning
* **C++ Core Pinning:** I nodi C++ ad alto consumo computazionale (es. `marcus_semantic_mapper_cpp` e `hailo_bridge_node`) devono essere vincolati esplicitamente ai **CPU Core 2 e 3** del Raspberry Pi 5 per evitare interferenze con i thread asincroni del kernel e di I/O (Core 0-1).
* **Zero Allocazioni in Callback:** Le callback di processing critiche (es. mapper 3D) devono operare su strutture pre-allocate per evitare garbage collection e contese sul thread real-time.

### 5. Allocazione Mappa e Database SLAM (Obbligo Vincolante SSD NVMe - FM-NAV-020)
* **Regola Inviolabile:** Il database cartografico RTAB-Map (`rtabmap.db`) e qualsiasi file di persistenza del grafo SLAM DEVONO risiedere **esclusivamente su supporto SSD esterno/NVMe** montato su `/mnt/ssd/rtabmap.db`.
* **Divieto Assoluto Scrittura Mappa su MicroSD:** È severamente vietato salvare, copiare o lasciare che RTAB-Map generi il database cartografico sulla memoria flash MicroSD (`/dev/mmcblk0p2` o directory `~/.ros/` senza symlink integro). L'accumulo di nodi e descrittori visivi DBoW3 provoca la saturazione del 100% della MicroSD (`[Errno 28] No space left on device`), runaway log e usura distruttiva delle celle flash.
* **Configurazione Obbligatoria:** Sia in `robopy_controller/config/rtabmap.yaml` che nei parametri di avvio in `restart_hailo.sh`, il parametro `database_path` deve essere esplicitamente e permanentemente impostato su `/mnt/ssd/rtabmap.db`.

### 6. Modalità Operative di Avvio: Crea Mappa (SLAM) vs Navigazione (AMCL 2D - FM-NAV-030)
* **Modalità Crea Mappa (SLAM 3D/2D con RTAB-Map):**
  * Avvio: `./restart_hailo.sh --slam` (oppure default `./restart_hailo.sh` / `./restart.sh`).
  * Salvataggio rapido mappa 2D: `./scripts/save_map.sh <nome_mappa>` (esporta in `/mnt/ssd/maps/<nome_mappa>.yaml` e `.pgm` via `nav2_map_server map_saver_cli`).
* **Modalità Naviga (Navigazione Mappa Statica con AMCL 2D):**
  * Avvio: `./restart_hailo.sh --amcl --map=/mnt/ssd/maps/<nome_mappa>.yaml` (default se omesso: `/mnt/ssd/maps/salotto.yaml`).
  * **Regola Inviolabile Unicità Autorità TF `map -> odom` (REP-105):** In modalità AMCL, RTAB-Map viene avviato con `publish_tf:=false` e `Mem/IncrementalMemory:=false`. È severamente vietato che AMCL e RTAB-Map pubblichino contemporaneamente `map -> odom`. Questa modalità azzera lo sfasamento e la duplicazione dei muri da wheel scrub, mantenendo al contempo attive la proiezione semantica 2.5D, la prevenzione scale e le memorie visive TRINITY.

### 7. Auto-Aggiornamento del Progetto e Disciplina di Sincronizzazione (Book-to-Skill V2)
* **Sincronizzazione Bidirezionale Host (PC ↔ Pi 5):** Tramite `./sync_marcus.sh` con tunnel multiplexato SSH ControlMaster.
  * **Anti-Flattening Back-Sync (Robot ➔ PC):** Recupera automaticamente dal robot skills generate dinamicamente, diari evolutivi, log, DFMEA (`fmea/dfmea.yaml`), schede tecniche e lezioni prima di qualsiasi push.
  * **Forward-Sync (PC ➔ Robot):** Esegue rsync con `-u` (`--update`) per non sovrascrivere mai file aventi data più recente sul robot.
  * **Python Hot-Swap:** Aggiorna a caldo `nodes/`, `robot_ai/` e `launch/` nella cartella `install/` senza richiedere ricompilazioni `colcon build`.
* **Ciclo di Auto-Evoluzione Autonoma (Project Autopoiesis):** Eseguibile sul robot via `python scripts/run_autonomous_evolution_cycle.py`. Seleziona i failure mode a più alto RPN da `fmea/dfmea.yaml`, valida il codice con `SecurityValidator` (AST), lo collauda in `SkillSandbox`, e obbliga l'aggiornamento simultaneo di `/docs/lessons/`, `/docs/ecos/`, DFMEA (`python fmea/calculate_and_report_fmea.py`) ed `evolution_journal.md`. Qualsiasi tocco alla Zona Rossa deve essere respinto con apertura RFC in `docs/ideas/RED_ZONE_IDEAS_RFC.md`.

---

## 8. 🔧 Note Operative SSH su Marcus — ROS 2 Monitoring (LEGGERE SEMPRE!)

> [!IMPORTANT]
> **Ogni volta che ti connetti via SSH a Marcus per ispezionare topic, nodi o frequenze, DEVI impostare queste tre variabili d'ambiente.** Senza di esse, `ros2 topic list` mostra nodi ma `ros2 topic hz/echo` fallisce con `Failed to find a free participant index for domain 42`.

### 8.1 Dominio ROS 2 e Configurazione CycloneDDS

Marcus usa **`ROS_DOMAIN_ID=42`** e **`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`**. Il file di configurazione CycloneDDS (che alza il limite di partecipanti da 32 a 200) risiede in `/tmp/cyclonedds_robopy.xml` (generato da `restart_hailo.sh`).

⚠️ Le sessioni SSH normali **NON ereditano** queste variabili (non sono in `.bashrc`). Risultato: `ros2 topic hz` e `ros2 topic echo` falliscono con "Failed to find a free participant index" perché vedono il limite CycloneDDS default (32) già saturo dai ~38 nodi attivi.

### 8.2 Template SSH Corretto (usare sempre questo)

Per qualsiasi comando `ros2 topic`, `ros2 node info`, `ros2 service call` da SSH, usare **sempre questo prefisso** nella stessa sessione:

```bash
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash
```

In PowerShell/WSL una riga sola:
```
wsl ssh robopy@marcus "export ROS_DOMAIN_ID=42; export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml; source /home/robopy/ros2_jazzy/install/setup.bash; source /mnt/ssd/robopy_controller_host/install/setup.bash; <COMANDO>"
```

### 8.3 Comandi di Monitoring Verificati e Funzionanti

```bash
# ros2 node list e topic list funzionano anche senza CYCLONEDDS_URI (usano daemon)
ros2 node list
ros2 topic list

# Hz e echo RICHIEDONO CYCLONEDDS_URI (creano un nuovo subscriber/partecipante DDS)
timeout 5 ros2 topic hz /scan          # atteso ~10 Hz
timeout 4 ros2 topic hz /odom          # atteso ~20 Hz
timeout 3 ros2 topic hz /ultrasonic_range
timeout 5 ros2 topic echo /battery_state --once    # voltage, percentage
timeout 5 ros2 topic echo /amcl_pose --once        # x, y localizzazione
timeout 5 ros2 topic echo /diagnostics --once      # errori e warning
```

### 8.4 Fix Permanente Consigliato

Aggiungere a `/home/robopy/.bashrc` sul robot per non dover reimpostare le variabili a ogni sessione SSH:
```bash
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null
```

### 8.5 Symlink robot_ai (verifica post-build)

Se viene eseguito `colcon build`, verificare che il symlink sia ancora intatto:
```bash
SITE_PKG="/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages"
ls -la ${SITE_PKG}/robot_ai   # deve essere un link a .../robopy_controller/robot_ai
```
Già inserito in `restart_hailo.sh`. In caso di assenza: `ln -sfn "${SITE_PKG}/robopy_controller/robot_ai" "${SITE_PKG}/robot_ai"`

---

## 🗺️ Indice Operativo dei Domini (Mappa Spoke)

> [!CAUTION]
> **OBBLIGO DI LETTURA DELLA SCHEDA TECNICA PRIMA DI MODIFICARE IL CODICE:**
> Prima di aprire in scrittura o modificare qualsiasi file sorgente, l'agente DEVE eseguire `view_file` sulla relativa **Scheda Tecnica (`/docs/specs/SPEC-XX.md`)** per verificare i vincoli di Zona Rossa, Verde e Gialla.
> Per individuare istantaneamente quale specifica aprire per ciascun file sorgente, consulta il file di instradamento: [`docs/specs/SPECS_ROUTING.yaml`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPECS_ROUTING.yaml) oppure [`docs/specs/INDEX_SCHEDE_TECNICHE.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/INDEX_SCHEDE_TECNICHE.md).

| Macro-Area del Robot | Scheda Tecnica (Specs) | File di Approfondimento (Lessons) | Registro Storico (ECOs) | Nodi e Moduli Chiave |
| :--- | :--- | :--- | :--- | :--- |
| **Governance Antigravity** | [/docs/specs/SPEC-00_antigravity_governance.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-00_antigravity_governance.md) | [INDEX_SCHEDE_TECNICHE.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/INDEX_SCHEDE_TECNICHE.md) | *(Governance globale)* | Git flow sandbox, Pre-commit CI |
| **Navigazione e SLAM** | [/docs/specs/SPEC-02_navigation_and_slam.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-02_navigation_and_slam.md) | [/docs/lessons/nav2_slam_tuning.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/nav2_slam_tuning.md) | [/docs/ecos/nav2_slam_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/nav2_slam_ecos.md) | `semantic_costmap_injector.py`, `nav2_params.yaml`, `rtabmap` |
| **Voice User Interface (VUI)** | [/docs/specs/SPEC-04_audio_vui_pipeline.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-04_audio_vui_pipeline.md) | [/docs/lessons/audio_vui_pipeline.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/audio_vui_pipeline.md) | [/docs/ecos/audio_vui_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/audio_vui_ecos.md) | `respeaker_vui_node.py`, firmware ESP32 ReSpeaker |
| **Visione e Calcolo NPU (Hailo)** | [/docs/specs/SPEC-03_vision_and_hailo_npu.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-03_vision_and_hailo_npu.md) | [/docs/lessons/vision_hailo_npu.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/vision_hailo_npu.md) | [/docs/ecos/vision_hailo_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/vision_hailo_ecos.md) | `hailo_bridge_node.py`, `marcus_semantic_mapper_node.cpp`, YOLO/SuperPoint HEF |
| **Cervello Cognitivo TRINITY & RAG** | [/docs/specs/SPEC-05_cognitive_brain_trinity.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-05_cognitive_brain_trinity.md) | [/docs/lessons/orchestration_and_rag.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/orchestration_and_rag.md) | [/docs/ecos/orchestration_rag_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/orchestration_rag_ecos.md) | `trinity_engine.py`, `chroma_native_store.py`, `mag_database.py` |
| **Connettività & Live API** | [/docs/specs/SPEC-04_audio_vui_pipeline.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-04_audio_vui_pipeline.md) | [/docs/lessons/llm_live_api.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/llm_live_api.md) | [/docs/ecos/llm_live_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/llm_live_ecos.md) | `live_connection_bridge_node.py`, `llm_service.py`, watchdog |
| **Attuazione e Controllo Motori** | [/docs/specs/SPEC-01_actuation_and_motion.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-01_actuation_and_motion.md) | [/docs/lessons/actuation_motor_driver.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/actuation_motor_driver.md) | [/docs/ecos/actuation_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/actuation_ecos.md) | `waveshare_motor_driver.py`, `motion_manager.py` |
| **Alimentazione (BMS) & Safety** | [/docs/specs/SPEC-06_power_bms_thermal_safety.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-06_power_bms_thermal_safety.md) | [/docs/lessons/telemetry_and_autotuning.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/telemetry_and_autotuning.md) | *(Fa riferimento ai singoli ECO)* | `battery_manager_node.py`, `robot_health_supervisor.py` |
| **Host Pi 5, Build & Lifecycle** | [/docs/specs/SPEC-07_system_os_build_lifecycle.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-07_system_os_build_lifecycle.md) | [/docs/lessons/dev_and_deployment.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/dev_and_deployment.md) | *(Fa riferimento ai singoli ECO)* | `setup.py`, `CMakeLists.txt`, `sync_marcus.sh`, `system_lifecycle_coordinator_node.py` |
