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
