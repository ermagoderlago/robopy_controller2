# AI_CONTEXT.md

## 1) Contesto e Vincoli

### Sistema target
- **Repository ROS 2 principale:** `robopy_controller` (package format 3, build ibrido `ament_cmake` + Python install). 
- **ROS 2 target dichiarato nei file di progetto:** Jazzy (coerente con naming launch/config e dipendenze Nav2/RTAB-Map). 
- **Hardware target operativo:** Raspberry Pi 5 (inferenziale dai nodi hardware-specifici: BuildHAT, BlueDot, ReSpeaker, OAK-D Lite, V4L2/Picamera). 
- **Python rilevato nell'ambiente corrente:** `Python 3.10.19`.

### Vincoli embedded (da rispettare nello sviluppo futuro)
- **RAM limitata (4–8 GB):** evitare accumulo frame, queue non bounded, cache non controllate.
- **CPU ARM senza GPU dedicata generale:** preferire modelli leggeri, rate limiting, task async e degradazione controllata.
- **I/O storage lento (tipicamente SD):** ridurre logging verboso su disco, batching e rotazione file.

### Implicazioni tecniche obbligatorie
- Evitare allocazioni grandi e copie superflue (soprattutto immagini/audio).
- Minimizzare copie di `bytes`/`numpy` tra pipeline camera → AI.
- Preferire streaming/chunking (audio, frame, RAG ingestion).
- Non bloccare callback ROS o event loop asyncio.

---

## 2) Mappa dell’Architettura (File-Level)

> **Nota:** questa mappa riflette solo componenti realmente trovati nel workspace.

### Core Memory
- `robopy_controller/robot_ai/orchestration/memory_manager.py`  
  Manager ad alto livello con coda async (`asyncio.Queue`) e worker background per storage/search.
- `robopy_controller/robot_ai/rag/base_memory_store.py`  
  Contratto astratto per memory store (add/search async).
- `robopy_controller/robot_ai/rag/memory_store.py`  
  Modello memoria + implementazioni di base del layer RAG.
- `robopy_controller/robot_ai/rag/llama_index_store.py`  
  Backend dichiarato “state of the art” usato dall’orchestratore (`LlamaIndexMemoryStore`).
- `robopy_controller/robot_ai/rag/metadata_manager.py`  
  Gestione metadata associata a memorie/indicizzazione.

### Servizi AI
- `robopy_controller/robot_ai/services/llm_service.py`  
  Nodo LLM ROS2 + loop asyncio dedicato; integrazione Gemini (standard + live audio), circuit breaker, retry/backoff.
- `robopy_controller/robot_ai/services/embedding_service.py`  
  Servizio embedding usato dal layer memoria.
- `robopy_controller/robot_ai/services/deepseek_service.py`  
  Integrazione LLM secondaria (deepseek) usata in workflow notturni/fallback.
- `robopy_controller/robot_ai/services/asr_service.py`  
  Servizio speech-to-text (pipeline voce).
- `robopy_controller/robot_ai/services/tts_service.py`  
  Servizio text-to-speech (output voce e audio chunk).
- `robopy_controller/robot_ai/services/visual_memory_service.py`  
  Memoria visuale e contestualizzazione immagini.
- `robopy_controller/robot_ai/services/face_recognition_service.py`  
  Riconoscimento volti e aggiornamento contesto persona.
- `robopy_controller/robot_ai/services/nightly_dream_service.py`  
  Processo batch pianificato (analisi notturna memorie).

### Orchestrazione
- `robopy_controller/nodes/robot_ai_node.py`  
  Entry point: inizializza `AIOrchestrator` + `LLMService` in `MultiThreadedExecutor`.
- `robopy_controller/robot_ai/orchestration/orchestrator.py`  
  Nodo `robot_ai_orchestrator`, wiring di servizi AI, skill, event bus, ROS I/O, scheduler notturno.
- `robopy_controller/robot_ai/orchestration/conversation.py`  
  Gestione conversazione: sanitizer, RAG context, routing Live/Standard LLM, skill actions, auto-mute.
- `robopy_controller/robot_ai/orchestration/skill_executor.py`  
  Esecuzione skill/tool chiamati dal LLM o fast-path intent.
- `robopy_controller/robot_ai/orchestration/world_model.py`  
  Stato mondo e contesto breve termine.
- `robopy_controller/robot_ai/orchestration/ha_context.py`  
  Sync contesto Home Assistant.
- `robopy_controller/robot_ai/orchestration/reactive_safety.py`  
  Safety reattiva (stop/move relative) con pubblicazione Twist.
- `robopy_controller/robot_ai/orchestration/metrics.py`  
  Metriche latenza/errori AI e stato connettività.

### Skills built-in
- `robopy_controller/robot_ai/skills/skill_registry.py`
- `robopy_controller/robot_ai/skills/base_skill.py`
- `robopy_controller/robot_ai/skills/builtin/navigation_skill.py`
- `robopy_controller/robot_ai/skills/builtin/ha_skill.py`
- `robopy_controller/robot_ai/skills/builtin/search_skill.py`
- `robopy_controller/robot_ai/skills/builtin/visual_exploration_skill.py`
- `robopy_controller/robot_ai/skills/builtin/nightly_dream_skill.py`
- `robopy_controller/robot_ai/skills/builtin/calibration_skill.py`

---

## 3) Architettura ROS2 Runtime

## 3.1 Package ROS2 trovati
- `package.xml` (root): package **`robopy_controller`**.
- `marcus_robot/package.xml`: package **`marcus_robot`** (SITL/Gazebo bridge).
- `setup.py` + `setup.cfg`: packaging Python/entry points per `robopy_controller`.

## 3.2 Interfacce ROS custom trovate
- Messaggi:
  - `msg/AudioData.msg`
  - `msg/KeypointsCompressed.msg`
  - `msg/DescriptorsCompressed.msg`
  - `msg/OAKSyncFrame.msg`
- Servizi:
  - `srv/MemorySearch.srv`
  - `srv/AskVisualQuestion.srv`
- Action custom: **non trovate** nel workspace.

## 3.3 Nodi principali (responsabilità)

### Layer AI / orchestration
- **`robot_ai_orchestrator`** (`AIOrchestrator`)  
  Coordinamento conversazione, skill, memoria, sicurezza reattiva, bridge con HA/Nav.
- **`llm_service_node`** (`LLMServiceNode`)  
  Gateway Gemini Live/Standard, gestione sessione live audio, metriche, reconnect service.

### Layer sensing/perception/control (real-time oriented)
- **`oakd_v3_node`** (`OakDv3Node`)  
  Pubblica RGB/depth/IMU/detections da OAK-D, con sincronizzazione timestamp hardware.
- **`madgwick_filter`** (`MadgwickNode`)  
  Filtraggio IMU, stima orientamento e linear acceleration.
- **`motor_control_node`** (`MotorControlNode`)  
  Controllo motori BuildHAT (bang-bang da `cmd_vel` + input manuale `bluedot_input`).
- **`bluedot_node`** (`BlueDotNode`)  
  Teleop manuale joystick e comando servo.

> Il repository contiene molti altri nodi (`robopy_controller/nodes/*.py`), ma i sopra sono quelli maggiormente connessi alla pipeline runtime AI+robot deducibile dai launch principali.

## 3.4 Topic principali (dedotti dal codice)

### AI orchestrator (`orchestrator.py`)
**Publisher**
- `/bluedot_input` → `geometry_msgs/Twist` (comando movimento via reactive safety)
- `/ai/conversation/response` → `std_msgs/String`
- `/ai/conversation/status` → `std_msgs/String`
- `/respeaker/led_command` → `std_msgs/String`
- `/respeaker/audio_control` → `std_msgs/String`
- `/ai/input/mic_mute` → `std_msgs/Bool`
- `ai/input/voice_test` → `std_msgs/String` (**senza slash iniziale**)
- `/respeaker/speaker_audio` → `robopy_controller/msg/AudioData`

**Subscriber**
- `/rgb/image/compressed` → `sensor_msgs/CompressedImage`
- `/diagnostics` → `diagnostic_msgs/DiagnosticArray`
- `/ai/input/text` → `std_msgs/String`
- `/robopy/conversation_rx` → `std_msgs/String`
- `/ai/input/voice_test` → `std_msgs/String` (**con slash iniziale**) 
- `/ai/input/mic_mute` → `std_msgs/Bool`
- `/ai/conversation/audio_chunk` → `robopy_controller/msg/AudioData`

**Service server**
- `memory_search` → `robopy_controller/srv/MemorySearch`

### LLM service (`llm_service.py`)
**Publisher**
- `/ai/conversation/response` → `std_msgs/String`
- `/ai/conversation/audio_chunk` → `robopy_controller/msg/AudioData`
- `~/stats` → `std_msgs/String`

**Subscriber**
- `/audio_data` → `robopy_controller/msg/AudioData`

**Service server**
- `~/reconnect_live` → `example_interfaces/srv/Trigger`

### Motor + teleop
- `bluedot_node` pubblica:
  - `bluedot_input` → `std_msgs/Float64MultiArray`
  - `servo_angle` → `std_msgs/Float32`
- `motor_control_node` sottoscrive:
  - `bluedot_input` → `std_msgs/Float64MultiArray`
  - `cmd_vel` → `geometry_msgs/Twist`

### OAK + IMU
- `oakd_v3_node` pubblica:
  - `/oak/rgb/image_raw` → `sensor_msgs/Image`
  - `/oak/rgb/camera_info` → `sensor_msgs/CameraInfo`
  - `/oak/rgb/image_annotated` → `sensor_msgs/Image`
  - `/oak/stereo/image_raw` → `sensor_msgs/Image`
  - `/oak/imu/data` → `sensor_msgs/Imu`
  - `/oak/detections` → `vision_msgs/Detection2DArray`
- `madgwick_filter` sottoscrive `/oak/imu/data` e pubblica `/imu/data` + `/imu/linear`.

## 3.5 Service / Action
- Service custom effettivo usato da orchestratore: `memory_search`.
- Service custom VQA definito (`AskVisualQuestion.srv`) e client creato in `ConversationManager` verso `ask_visual_question`.
- Action custom: non definite.

## 3.6 QoS (dove esplicitato)
- `oakd_camera_publisher_node.py` usa `QoSProfile` con affidabilità `RELIABLE` per stream principali camera/imu/depth.
- Diversi altri nodi usano interi (depth 10/50), quindi QoS default ROS2 per quel costrutto.

---

## 4) Separazione Critica del Sistema

## 4.1 Layer Real-Time (CRITICO, non bloccabile)
- Sensori e visione base:
  - `robopy_controller/nodes/oakd_camera_publisher_node.py`
  - `robopy_controller/nodes/madgwick_node.py`
- Odometria/localizzazione:
  - pipeline launch con `rtabmap_odom`, `robot_localization`, tf
- Controllo robot:
  - `robopy_controller/motor_control_node.py`
  - input manuale/teleop (`bluedot_node`, teleop bridge)

**Regola:** nessuna operazione AI cloud o RAG deve bloccare callback/timer di questi nodi.

## 4.2 Layer AI (NON CRITICO, degradabile)
- LLM, memoria semantica, VQA, HA context, riconoscimento facciale.
- Componenti: `robopy_controller/robot_ai/**`.

**Obblighi architetturali:**
- asincrono,
- isolato da control loop,
- fallback e timeout,
- degradazione senza fermare robot core.

---

## 5) Regole d’Oro per l’AI (OBBLIGATORIE)

1. **Asincronia totale sulle chiamate I/O esterne**
   - usare `async/await`;
   - preferire librerie async (`aiohttp`, `aiofiles`, websocket async);
   - evitare lock bloccanti nel path async.

2. **Lock policy**
   - nel layer async usare `asyncio.Lock`;
   - evitare `threading.Lock` in percorsi hot async (accettabile solo per bridging thread ROS↔async, minimizzato).

3. **Timeout obbligatori**
   - default consigliato: **5s** chiamate esterne;
   - già presenti timeout più alti in LLM (`30s/60s`): ridurre quando possibile in produzione embedded.

4. **Fallback sempre presente**
   - Live API → Standard API → risposta locale degradatа;
   - nessuna eccezione non gestita che faccia crash del nodo.

5. **Load shedding**
   - interrompere task lenti/obsoleti;
   - queue bounded con drop policy esplicita;
   - warning log su drop/backpressure.

6. **Logging ROS2 only nei nodi ROS**
   - usare `self.get_logger()`;
   - vietato `print()` nei nodi runtime.

---

## 6) Gestione Risorse e Performance

- Evitare copie immagine inutili (tenere frame ultimo, non storico completo).
- Usare queue piccole (`maxsize`) su pipeline ad alto rate.
- Evitare loop CPU-bound in callback; usare timer e task async cooperativi.
- Ridurre frequenza nodi AI quando CPU > soglia.
- Favorire modelli leggeri e inferenza on-device solo se sostenibile (rate/fps controllati).
- Limitare serializzazione JSON grande su topic ad alta frequenza.

---

## 7) Degradazione del Sistema

Strategie operative consigliate e coerenti con il codice:

- **CPU alta**
  - ridurre frequenza AI (polling HA, analisi visuale, skill esplorazione);
  - sospendere Nav2 se inattivo (già previsto watchdog in orchestrator).

- **Timeout LLM / cloud down**
  - fallback standard/local e risposta breve “offline mode”;
  - non interrompere nodo controllo/sensori.

- **Memoria bassa**
  - svuotare cache non critica;
  - limitare queue memoria e scartare elementi vecchi.

- **Errori AI**
  - isolare perimetro AI con `try/except` locale;
  - pubblicare stato degradato su topic status senza impattare ROS core.

---

## 8) Testing e Debug

## 8.1 Principio
Ogni nodo deve poter essere avviato standalone (`ros2 run`) e validato con `topic echo/pub`.

## 8.2 Comandi esempio (debug rapido)
- Verifica nodi:
  - `ros2 node list`
- Verifica topic:
  - `ros2 topic list`
  - `ros2 topic echo /ai/conversation/response`
  - `ros2 topic echo /oak/imu/data`
- Stimolo input testo AI:
  - `ros2 topic pub /ai/input/text std_msgs/msg/String "{data: 'ciao Marcus'}" -1`
- Test emergency stop via testo:
  - `ros2 topic pub /ai/input/text std_msgs/msg/String "{data: 'stop'}" -1`
- Test servizio memoria:
  - `ros2 service call /memory_search robopy_controller/srv/MemorySearch "{query: 'cucina', limit: 3}"`

## 8.3 Logging
- Preferire logging strutturato e livelli (`debug/info/warn/error`).
- Nei nodi ROS usare solo `self.get_logger()`.
- Ridurre log ad alta frequenza in produzione Pi 5.

---

## 9) Dipendenze Critiche

## 9.1 ROS2 (da `package.xml` / `CMakeLists.txt`)
- Core: `rclpy`, `rclcpp`, `std_msgs`, `sensor_msgs`, `geometry_msgs`, `nav_msgs`
- TF: `tf2`, `tf2_ros`, `tf2_geometry_msgs`
- Vision: `cv_bridge`, `image_transport`, `vision_msgs`, `diagnostic_msgs`
- Localization: `robot_localization`
- Build interfaces: `rosidl_default_generators`, `rosidl_default_runtime`
- Hardware/libs: `depthai`, `buildhat`, `bluedot`, `picamera2`, `audio_capture`, `audio_common_msgs`

## 9.2 Python (da `requirements_ai.txt`)
- `google-generativeai>=0.3.0`
- `chromadb>=0.4.0`
- `websockets>=10.0`
- `google-cloud-texttospeech>=2.0.0`
- `google-cloud-speech>=2.0.0`
- `pygame>=2.0.0`
- `pydantic>=2.0.0`
- `pyyaml>=6.0`
- `paramiko>=3.0.0`

---

## 10) Comandi Utili

- Launch stack robot:
  - `ros2 launch robopy_controller full_robot_launch.py`
  - `ros2 launch robopy_controller robopy_launch.py`
  - `ros2 launch robopy_controller robopy_stable_launch.py`
- Esecuzione nodo singolo:
  - `ros2 run robopy_controller robot_ai_node`
  - `ros2 run robopy_controller llm_service_node`
  - `ros2 run robopy_controller madgwick_node`
  - `ros2 run robopy_controller ultrasonic_sensor`
- Introspection:
  - `ros2 node list`
  - `ros2 topic list`
  - `ros2 service list`
  - `ros2 interface show robopy_controller/srv/MemorySearch`

---

## Incoerenze rilevate (NON corrette automaticamente)

1. **Import verso moduli `robot_ai.core` non presenti nel tree.**  
   Esempio: `orchestrator.py` importa `robot_ai.core.*`, ma la directory `robopy_controller/robot_ai/core/` non è presente nel workspace.

2. **Entry points `setup.py` puntano a percorsi mancanti sotto `robopy_controller/nodes/`.**  
   Esempi mancanti: `lite_mono_node.py`, `lite_depth_node.py`, `sync_publisher_node.py`, `depth_to_pointcloud_node.py`, `ultrasonic_sensor.py` (presenti però varianti in root package non `nodes/`).

3. **Mismatch tipo topic `/bluedot_input`.**  
   - `AIOrchestrator` pubblica `geometry_msgs/Twist` su `/bluedot_input`;  
   - `MotorControlNode` su `bluedot_input` attende `std_msgs/Float64MultiArray`;  
   possibili conflitti runtime se remap non gestito.

4. **Incoerenza naming topic voice test.**  
   - publisher su `ai/input/voice_test` (relativo),  
   - subscriber su `/ai/input/voice_test` (assoluto).

5. **Uso misto logging ROS e `print()` in nodi runtime AI.**  
   `robot_ai_node.py` usa diversi `print()`, in contrasto con linea guida robusta ROS2-only logging.

6. **`package.xml` dichiara `ament_cmake`, mentre è presente anche `setup.py` Python packaging.**  
   Funziona come setup ibrido, ma aumenta rischio drift tra install CMake e entry points Python.

---

## Fonte di verità operativa (per future AI)

- In caso di dubbio, **il codice sorgente attuale prevale su documentazione esterna**.
- Non assumere moduli “desiderati” ma non presenti.
- Prima di introdurre nuove feature AI, verificare:
  1) isolamento dal layer real-time,
  2) timeout+fallback,
  3) impatto memoria/CPU su Pi 5,
  4) coerenza topic/type ROS.

