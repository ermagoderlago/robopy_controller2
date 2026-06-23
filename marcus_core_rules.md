# Marcus Core Rules - Nucleo Residente (V2)

Questo documento contiene le **Regole d'Oro** e i **vincoli fisici assoluti** per il robot Marcus. È l'unico file di documentazione globale destinato a rimanere costantemente nel contesto di programmazione dell'IA.

---

## ⚡ Regole d'Oro e Vincoli Fisici Assoluti

Per garantire la sopravvivenza hardware, la fluidità di esecuzione ed evitare il collasso del sistema su Raspberry Pi 5, attenersi rigorosamente ai seguenti vincoli architetturali:

### 1. Memoria RAM e Limite Host
* **Tetto massimo RAM:** Il Raspberry Pi 5 host dispone di **4GB di RAM** utilizzabili.
* **Compilazione ROS 2:** Deve essere eseguita in modo **sequenziale** per prevenire crash di Out-Of-Memory (OOM) indotti dal compilatore `clang++`.
  * **Comando obbligatorio:** `MAKEFLAGS="-j1" colcon build --parallel-workers 1 ...`
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

Per ogni modifica, fare riferimento esclusivo al documento tematico del dominio interessato. È vietato caricare file non appartenenti al dominio del task corrente.

| Macro-Area del Robot | File di Approfondimento (Lessons) | Registro Storico (ECOs) | Nodi e Moduli Chiave |
| :--- | :--- | :--- | :--- |
| **Navigazione e SLAM** | [/docs/lessons/nav2_slam_tuning.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/nav2_slam_tuning.md) | [/docs/ecos/nav2_slam_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/nav2_slam_ecos.md) | `semantic_costmap_injector.py`, `nav2_params.yaml`, `rtabmap` |
| **Voice User Interface (VUI)** | [/docs/lessons/audio_vui_pipeline.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/audio_vui_pipeline.md) | [/docs/ecos/audio_vui_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/audio_vui_ecos.md) | `respeaker_vui_node.py`, firmware ESP32 ReSpeaker |
| **Visione e Calcolo NPU (Hailo)** | [/docs/lessons/vision_hailo_npu.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/vision_hailo_npu.md) | [/docs/ecos/vision_hailo_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/vision_hailo_ecos.md) | `hailo_bridge_node.py`, `marcus_semantic_mapper_node.cpp`, YOLO/SuperPoint HEF |
| **Cloud, Memory & Orchestration** | [/docs/lessons/orchestration_and_rag.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/orchestration_and_rag.md) | [/docs/ecos/orchestration_rag_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/orchestration_rag_ecos.md) | `conversation.py`, `chroma_native_store.py`, `cognitive_graph.py` |
| **Connettività & Live API** | [/docs/lessons/llm_live_api.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/llm_live_api.md) | [/docs/ecos/llm_live_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/llm_live_ecos.md) | `live_connection_manager.py`, `llm_service.py`, watchdog |
| **Attuazione e Controllo** | [/docs/lessons/actuation_motor_driver.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/actuation_motor_driver.md) | [/docs/ecos/actuation_ecos.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/ecos/actuation_ecos.md) | `waveshare_motor_driver.py`, firmware ESP32 Waveshare |
| **Ambiente, Build & Deploy** | [/docs/lessons/dev_and_deployment.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/lessons/dev_and_deployment.md) | *(Fa riferimento ai singoli ECO)* | `setup.py`, `CMakeLists.txt`, `sync_marcus.sh`, `restart.sh` |
