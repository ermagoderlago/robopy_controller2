# Engineering Change Orders - Visione & NPU (Hailo)

Questo documento raccoglie la cronologia delle modifiche ingegneristiche (ECO) apportate ai sistemi di visione artificiale, integrazione NPU Hailo-10H e mapping semantico 3D di Marcus.

---

## 📈 ECO-2026-06-07-001: Hailo-10H NPU Platform and Local LLM (Ollama) Support
* **Stato:** ✅ **Completato, Configurato e Verificato**
* **Descrizione:** Configurazione del sistema host Raspberry Pi OS 64-bit Bookworm per supportare il nuovo HAT Raspberry Pi AI HAT+ 2 (NPU Hailo-10H ed 8GB di RAM dedicati) per l'infrastruttura Gen-AI.
* **Modifiche apportate:**
  * Configurato PCIe Gen 3 in `/boot/firmware/config.txt`.
  * Aggiunto il repository `trixie main` con priorità `50` in `/etc/apt/sources.list.d/trixie.list` e preferences.
  * Creato ed abilitato `hailo-ollama.service` su systemd sulla porta `11434`.

---

## 📈 ECO-2026-06-07-002: Hailo-10H ROS 2 Custom Nodes and Message IDL Implementation
* **Stato:** ✅ **Completato, Sincronizzato e Compilato**
* **Descrizione:** Implementazione di 5 nuovi nodi ROS 2 in Python per gestire la pipeline cognitiva locale di Hailo-10H e 3 nuovi messaggi custom IDL.
* **Modifiche apportate:**
  * Creati i messaggi `SemanticObject.msg`, `SemanticObjectArray.msg` ed `EngagementStatus.msg`.
  * Creato `hailo_bridge_node.py` (con core pinning su core CPU 2-3 ed emulatori), `semantic_costmap_injector.py` (proiezione geometrica 3D➔2D con decadimento temporale), `engagement_monitor.py` (prossemica HRI), `cloud_watchdog_node.py` (stato di connettività Gemini), `speaker_id_node.py` (biometria vocale ECAPA-TDNN).
  * Aggiornati `setup.py` e `CMakeLists.txt` per la compilazione.

---

## 📈 ECO-2026-06-10-001: Hailo-10H 3D Semantic Fusion and Launch Infrastructure
* **Stato:** ✅ **Completato e Sincronizzato**
* **Descrizione:** Implementazione del nodo visual fusion C++17 `marcus_semantic_mapper`, configurazione del build system e creazione dello script di avvio completo `restart_hailo.sh`.
* **Modifiche apportate:**
  * Creati `marcus_semantic_mapper_node.hpp` e `.cpp` (back-projection Eigen, median filter sulla ROI, serializzazione in `rtabmap_msgs/msg/UserData`).
  * Aggiunto `restart_hailo.sh` per gestire l'avvio della suite locale AI/NPU.
  * Creati gli script di test `test_semantic_mapper.py` e `test_hailo_nodes.py`.

---

## 📈 ECO-2026-06-10-002: WSL Compilation Setup and C++ Semantic Mapper Fixes
* **Stato:** ✅ **Completato, Sincronizzato e Compilato**
* **Descrizione:** Risoluzione degli errori di compilazione sul mapper 3D in C++ con ricostruzione ROS 2 superata sul Raspberry Pi 5.
* **Modifiche apportate:**
  * Aggiunto `find_package(tf2_eigen REQUIRED)` in `CMakeLists.txt` e `<depend>tf2_eigen</depend>` in `package.xml`.
  * Introdotta la variabile `odom_frame` (default `"odom"`) per consentire il fallback in assenza di `map`.
  * Corrette le chiamate C++ camelCase `lookupTransform` su `tf2` e rimossa la chiamata non standard `message_filters::SubscriptionOptions()` incompatibile con Jazzy.

---

## 📈 ECO-2026-06-11-001: NPU Model Compilation (YOLOv8-seg, SuperPoint, NetVLAD)
* **Stato:** ✅ **Completato, Compilato e Salvato**
* **Descrizione:** Compilazione ed ottimizzazione dei tre modelli neurali per l'NPU Hailo-10H. Risoluzione dei bug del compilatore Hailo relativi a input non-4D e limitazioni di pooling NetVLAD.
* **Modifiche apportate:**
  * Compilato `superpoint_128d.hef` (160x120) e `yolov8s_seg.hef` (usando `--use-random-calib-set`).
  * Compilato `netvlad_mobilenet_backbone.hef` (MobileNetV2 + 1x1 conv) isolando il pooling NetVLAD sulla CPU host (in Python/NumPy).

---

## 📈 ECO-2026-06-16-001: Unified Hailo HEF Compilation & ROS 2 InferModel Integration
* **Stato:** ✅ **Completato, Sincronizzato e Attivo sul Robot**
* **Descrizione:** Compilazione unificata dei tre modelli AI in un singolo HEF (`joined_yolo_superpoint_netvlad.hef`) e refactoring di `hailo_bridge_node.py` per utilizzare le nuove API `InferModel` anziché le legacy `VStream` (incompatibili con Hailo-10H, generavano `HAILO_NOT_IMPLEMENTED`). Rimozione di import obsoleti (`InferVStream`) per prevenire il fallback silenziato in modalità simulazione.

---

## 📈 ECO-2026-06-24-001: Face Recognition su Hailo-10 NPU & VUI Guest Enrollment
* **Stato:** ✅ **Completato, Verificato tramite Test Suite**
* **Descrizione:** Integrazione completa del pipeline SCRFD Face Detection e ArcFace Face Recognition su Hailo-10 NPU con allineamento affine landmark in Python, enrollment dinamico guidato da comandi vocali VUI e fallback di simulazione robusto.
* **Modifiche apportate:**
  * Implementato allineamento affine delle facce (coordinate standard ArcFace) in `face_alignment.py` utilizzando `cv2.estimateAffinePartial2D`.
  * Creato `face_enrollment_manager.py` per raccogliere 10 campioni consecutivi, effettuarne la media, normalizzarli con norma L2 e scriverli in formato `.npy` su disco.
  * Refattorizzato `face_recognition_service.py` per calcolare velocemente la similarità tramite prodotto scalare e gestire le fasi di enrollment e matching.
  * Riconfigurato `conversation.py` per intercettare i trigger vocali (es. "Marcus, ti presento [Nome]") e avviare la sessione di enrollment dando feedback vocale (TTS).
  * Esteso `hailo_bridge_node.py` per eseguire la pipeline reale su InferModel o attivare il fallback simulato leggendo i file `.npy` da `known_faces/` per test offline.
  * Aggiunto `test_sprint3.py` per la validazione automatica end-to-end con mocks completi su Windows.

---

## 📈 ECO-2026-06-25-001: Visual Odometry Resolution Alignment & Memory-Safety Fix
* **Stato:** ✅ **Completato e Sincronizzato (da compilare ed attivare)**
* **Descrizione:** Allineamento della risoluzione di ridimensionamento del frame SuperPoint per corrispondere all'input nativo del modello `.blob` (320x200 invece di 480x360). Risolto l'heap out-of-bounds read nel decoding dei layer dell'NPU tramite confronto relativo delle dimensioni anziché soglie assolute rigide.
* **Modifiche apportate:**
  * Modificate le costanti `SP_W` da `480` a `320` e `SP_H` da `360` a `200` in [oak_superpoint_odometry_node.cpp](file:///c:/Users/lsuffia/OneDrive - BRUGOLA OEB INDUSTRIALE SPA/Documents/robopy/antigravity/src/oak_superpoint_odometry_node.cpp).
  * Sostituito il loop di parsing dei tensor basato su soglie fisse di dimensione con un controllo robusto relativo (`data0.size() > data1.size()`) per distinguere dinamicamente il layer dei descrittori da quello delle heatmap.

---

## 📈 ECO-2026-07-03-001: YOLO & Semantics Integration and Debug Image Overlay
* **Stato:** ✅ **Completato e Sincronizzato (da verificare sul robot)**
* **Descrizione:** Risoluzione del problema del ghost obstacle "sedia" persistente in simulazione, integrazione delle rilevazioni YOLO reali come semantic objects e creazione di un topic video compresso per il debug visivo annotato.
* **Modifiche apportate:**
  - Aggiunti i parametri `publish_sim_sedia` (Boolean, default `False`) e `annotated_image_topic` (String, default `/hailo/annotated_image/compressed`) in `hailo_bridge_node.py`.
  - Disabilitata la pubblicazione continua e incondizionata della sedia simulata quando `sim_mode` è attivo.
  - Implementata la mappatura dinamica delle classi YOLO reali rilevate (in italiano) a `SemanticObject` con stima 3D tramite `estimate_3d_position` basata sull'immagine di profondità del sensore.
  - Aggiunto il publisher ed il metodo `annotate_and_publish_image` per sovrapporre box YOLO (verdi), volti (arancioni) e oggetti semantici (viola) in tempo reale sul flusso di immagine compressa `/hailo/annotated_image/compressed`.

---

## 📈 ECO-2026-07-19-001: Risoluzione dei Bug Critici del Comparto AI Hailo NPU
* **Stato:** ✅ **Completato e Sincronizzato**
* **Descrizione:** Correzione sistematica di tutti i bug di pipeline, concurrency, QoS, formati di topic e race condition C++/Python del comparto AI di Hailo-10H, sbloccando finalmente l'esecuzione di YOLOv8 su hardware reale.
* **Modifiche apportate:**
  * **`hailo_bridge_node.py`**:
    - Abilitata l'esecuzione reale di YOLO con l'API InferModel integrando il controllo `(self.use_infer_model_api and self.has_yolo)` a riga 674.
    - Introdotto un mutex globale `self._infer_lock` per evitare race condition sui bindings NPU condivisi concorrentemente dai thread YOLO, Face e NetVLAD.
    - Sostituita la sottoscrizione RGB da CompressedImage a Image raw su `/rgb/image` per allineamento con i topic pubblicati e decodifica ottimizzata via `CvBridge`.
    - Corretto `centroid_2d` per popolare coordinate 2D normalizzate invece di 3D in metri.
    - Protetto con lock l'accesso a `self.latest_depth` in `run_yolo_hailo` ed introdotto il supporto per encoding di profondità float32 `32FC1`.
  * **`semantic_costmap_injector.py`**:
    - Cambiato il QoS del publisher PointCloud2 a `RELIABLE` per rispecchiare la configurazione Nav2.
    - Semplificata la generazione della PointCloud2 proiettando i punti sul piano `z = 0.1m` per evitare Z negativi ed eccessiva ridondanza.
  * **`marcus_semantic_mapper_node.cpp`**:
    - Risolto il TOCTOU copiando localmente il buffer degli oggetti attivi sotto un singolo lock in `publishSemanticObjects` e `publishMarkers`.
    - Aumentata la dimensione della coda di sincronizzazione `max_queue_depth` a `30` in `marcus_semantic_mapper_node.hpp`.
  * **`nav2_params_jazzy.yaml`**:
    - Abilitato `obstacle_layer` nella lista dei plugins di `global_costmap`.
  * **`launch/hailo_vision_launch.py`**:
    - Creato launch file unificato per avviare in modo pulito ed integrato tutti e tre i nodi del comparto AI.

---

## 📈 ECO-2026-07-30-001: Continuous Extrinsic Auto-Calibration & Proactive Sag Compensation (FM-VIS-003)
* **Stato:** ✅ **Completato, Sincronizzato e Testato**
* **Descrizione:** Risoluzione del Failure Mode `FM-VIS-003` mediante la creazione del nodo di diagnosi ed auto-calibrazione dinamica dell'inclinazione della fotocamera OAK-D Lite per compensare il cedimento meccanico da vibrazioni (pitch sag).
* **Modifiche apportate:**
  * Creato `extrinsic_camera_calibrator.py`: Esegue regressione RANSAC del piano terra dalla matrice di profondità, calcola l'angolo di pitch sag e pubblica lo stato su `/diagnostics` e `/robot/health_status` (Consapevolezza).
  * Modificato `dynamic_camera_tf_node.py`: Aggiunta la sottoscrizione a `/camera/extrinsic_pitch_correction` per applicare dynamic pitch compensation a caldo sul TF `base_link` $\rightarrow$ `camera_link_stabilized` (Self-Healing).
  * Modificato `semantic_costmap_injector.py`: Aggiunta la sottoscrizione a `/semantic_costmap/clear` per eseguire il flush istantaneo degli ostacoli spuri nella costmap al momento della ricalibrazione.
  * Creato il progetto di miglioramento isolato `docs/improvements/IMP-VIS-003_extrinsic_camera_calibration.md` ed aggiornato il database DFMEA (`fmea/dfmea.yaml`, RPN ridotto da 336 a 96).

---

## 📈 ECO-2026-09-10-001: Native C++ HailoRT InferModel Bridge & Multi-Stream Input Binding (FM-CPU-001)
* **Stato:** ✅ **Completato, Compilato con Successo e Distribuito sul Robot**
* **Descrizione:** Riscrittura completa del driver NPU Hailo-10H da Python a C++ nativo (`hailo_bridge_node_cpp`) per eliminare la contesa del GIL Python e ridurre l'overhead computazionale su Raspberry Pi 5. Risolto l'errore di binding su HEF congiunto multi-rete (`marcus_unified.hef`) e implementata la decodifica DFL multi-scala YOLOv8 nativa.
* **Modifiche apportate:**
  * **`src/hailo_bridge_node.cpp`**:
    - Integrata l'API C++ `hailort::VDevice` e `hailort::InferModel` con pre-allocazione e binding dinamico di tutti gli stream di input (`yolo/input_layer1`, `netvlad/input_layer1`, `superpoint/input_layer1`).
    - Configurato il formato di dequantizzazione hardware `HAILO_FORMAT_TYPE_FLOAT32`.
    - Implementata la decodifica multi-scala YOLOv8 DFL (strides 8, 16, 32 su `conv44/45`, `conv60/61`, `conv73/74`) con softmax vettorizzato sui 16 bin per lato.
    - Implementata la Non-Maximum Suppression (NMS) veloce con soglia IoU 0.45 e traduzione delle classi in italiano per la pubblicazione su `/hailo/detections` e `/hailo/semantic_objects`.
    - Applicato il core pinning forzato sui core CPU 2 e 3 (`pthread_setaffinity_np`) e Lazy Publishing per l'immagine compressa annotata.
  * **Compilazione**: Compilato in Release con `-O3 -mcpu=cortex-a76+crypto` e `MAKEFLAGS="-j1"` su Raspberry Pi 5.
  * **Script di avvio**: Aggiornato `restart_hailo.sh` per avviare `hailo_bridge_node_cpp` nativo.

---

## 📈 ECO-2026-09-29-001: Hailo NPU Dynamic Ament Execution, DDS Multi-Domain Alignment & CRLF Sanitization
* **Stato:** ✅ **Completato, Testato e Distribuito su Raspberry Pi 5**
* **Descrizione:** Risoluzione del Failure Mode `FM-VIS-008` (mancata scoperta topic `/hailo/...` in Foxglove ed errore di caricamento librerie dinamiche `libservice_msgs__rosidl_generator_py.so` all'avvio del driver Hailo NPU C++).
* **Modifiche apportate:**
  * **Launcher `restart_hailo.sh`**:
    - Aggiornata la modalità di lancio di `hailo_bridge_node_cpp` impiegando `taskset -c 2,3 ros2 run robopy_controller hailo_bridge_node_cpp` anziché l'invocazione diretta del binario. Questo assicura che il runtime Ament Index espanda e fornisca tutte le directory `lib` dei pacchetti isolati colcon a `LD_LIBRARY_PATH`.
    - Impostato `ENABLE_HAILO="${ENABLE_HAILO:-true}"` come default per garantire l'avvio della percezione NPU ad ogni riavvio standard di Marcus.
    - Esportato esplicitamente `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` in combinazione con `ROS_DOMAIN_ID=42` e `CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml` per la totale convergenza di rete con Nav2 e Foxglove Studio.
    - Aggiunta la sanitizzazione automatica dei caratteri CRLF (`sed -i 's/\r$//'`) nel ciclo di copia a caldo dei nodi Python per prevenire errori di interprete `/usr/bin/env: 'python3\r': No such file or directory` (risolto crash di `semantic_costmap_injector`).
  * **Verifica a runtime**:
    - Topic `/hailo/annotated_image/compressed` attivo e visibile in Foxglove (Publisher count: 2).
    - Topic `/hailo/semantic_objects` attivo (Publisher count: 1, Subscription count: 3).
    - Topic `/hailo_semantic_obstacles_pc` attivo e iniettato nelle costmap Nav2 (Publisher count: 1, Subscription count: 4).

---

## 📈 ECO-2026-09-30-001: Hailo YOLO Perception Accuracy & RGB Color Pipeline Restoration (FM-VIS-009)
* **Stato:** 🟡 **In Corso (Software implementato, HEF compilato e pronto al test)**
* **Descrizione:** Risoluzione sistematica delle anomalie di classificazione oggetti su Hailo-10H (scambio sistematico di divani e arredi voluminosi per la classe persona).
* **Modifiche apportate:**
  * **`src/hailo_bridge_node.cpp`**:
    - Implementato il preprocessing con **Letterbox 1:1** isotropo e padding simmetrico con valore 114, eliminando la distorsione verticale anamorfica del 160% tra 640x400 e 640x640.
    - Aggiornata la rimappatura inversa dei bounding box (`det.xmin`, `det.ymin`, `det.xmax`, `det.ymax`) per riflettere con precisione euclidea l'area utile dell'immagine prima del letterbox.
    - Innalzata la soglia di confidenza `conf_thresh` al valore nominale di specifica (`0.55f`) come stabilito in `SPEC-03` Zona Verde.
    - Introdotto filtro geometrico sull'aspect ratio della classe persona ($W/H \le 1.8$) per respingere falsi positivi orizzontali.
  * **`src/fast_flow_vo_node.cpp`**:
    - Configurato il supporto per il sensore a colori nativo `ColorCamera` (`camRgb`) a 640x400 BGR per alimentare la NPU con vero spettro cromatico anziché fotogrammi monocromatici duplicati.
  * **Documentazione & FMEA**:
    - Redatta la guida tecnica esaustiva `docs/guides/HAILO_HEF_COMPILATION_GUIDE.md`.
    - Aperto `FM-VIS-009` in `fmea/dfmea.yaml` e creato il progetto di miglioramento `docs/improvements/IMP-VIS-009_hailo_yolo_accuracy_fix.md`.

---

## 📈 ECO-2026-10-08-001: Resource Governor Shadow Mode, Hailo Pose Offload, Resident Multimodal Identity & VAD Energy-Gating KWS (IMP-GOV-001 F1–F4)
* **Stato:** ✅ **Completato, Testato (30/30 Test Superati) e Distribuito su Raspberry Pi 5**
* **Descrizione:** Implementazione integrale delle prime 4 fasi di IMP-GOV-001:
  - **F1 (Pose su Hailo & OAK Decoupling):** Spostamento decodifica ed esecuzione Pose dalla OAK MyriadX ad Hailo-10H (`hailo_bridge_node.cpp`), rimozione dei blob NN da `oak_driver_node.py` e `sync_buffer.py` che ora girano come pura pipeline sensoriale raw USB 3.0. Aggiunto presence-gating a monte della rete pose.
  - **F2 (Biometria Residente & Multimodal Identity Tracker):** Implementato `robopy_controller/robot_ai/services/multimodal_identity_tracker.py` e `multimodal_identity_node.py` per fusione bayesiana visuale (SCRFD + ArcFace) e audio (ReSpeaker DOA + ECAPA-TDNN) con cadenze temporali differenziate per stato del Governor.
  - **F3 (VAD Energy-Gated KWS & Stop-Words):** Risolta la contesa CPU/I2S sull'audio senza rischiare bus collision tra ESP32-S3 e XMOS XU316. Inserito gating dinamico RMS e finestra di hangover (15 chunk, 300-450ms) in `local_asr_vosk.py`, abbattendo la CPU a riposo da 12% a <1% preservando reattività stop <10ms.
  - **F4 (Dynamic Resource Governor in Shadow Mode):** Implementato `resource_governor.py` con statechart ortogonale (Kinematic x Cognitive), watchdog a 10 Hz delle 8 invarianze non negoziabili, fail-safe SAFE_FULL e topic `/resource_governor/state` e `/resource_governor/metrics`.
* **Modifiche apportate:**
  - `robopy_controller/robot_ai/services/resource_governor.py`, `robopy_controller/nodes/resource_governor_node.py`, `scripts/resource_governor_node`, `scripts/start_resource_governor.sh`
  - `robopy_controller/robot_ai/services/multimodal_identity_tracker.py`, `robopy_controller/nodes/multimodal_identity_node.py`, `scripts/multimodal_identity_node`
  - `robopy_controller/robot_ai/services/local_asr_vosk.py`
  - `src/hailo_bridge_node.cpp`, `robopy_controller/nodes/oak_driver_node.py`, `robopy_controller/oak_logic/sync_buffer.py`
  - `restart_hailo.sh`, `setup.py`, `fmea/dfmea.yaml`, `fmea/FMEA_EXECUTIVE_REPORT.md`

---

## 📈 ECO-2026-10-08-002: Local VLM Interlock & Standby Manager Dock Integration (IMP-GOV-001 F6 & F7)
* **Stato:** ✅ **Completato, Testato (42/42 Test Superati) e Distribuito**
* **Descrizione:** Implementazione delle fasi F6 ed F7 di IMP-GOV-001:
  - **F6 (LOCAL_VLM Interlock & Break-Before-Make):**
    - Interlock hardware ed architetturale per Qwen2-VL su Hailo-10H: ammesso SOLO ed esclusivamente in `STATIONARY`.
    - Implementato break-before-make: all'arrivo di intent di navigazione o moto, il Governor disattiva e scarica istantaneamente il VLM (`vlm_enable = False`) prima di iniziare la sequenza di spin-up (`PREP_NAV` -> `MOVING`).
    - Aggiornato `hailo_vlm_node.py` con sottoscrizione a `/resource_governor/vlm_enable`: inibisce ed isola le query VQA durante gli stati non ammessi per prevenire contesa su NPU e starvation di YOLO.
    - Esposto service `/resource_governor/request_local_vlm` (`SetBool`) per attivazione/disattivazione controllata.
  - **F7 (Integrazione Sleep in Dock & Invarianti 6 e 7):**
    - `sensor_standby_manager.py` integrato come attuatore subordinato alla Statechart del Governor (Invariante 4).
    - Invariante 6 garantita: inibito l'arresto automatico del LiDAR e la pausa di RTAB-Map su inattività quando il robot è fuori dal dock. Standby hardware attivato unicamente in `DOCKED_SLEEP`.
    - Invariante 7 garantita: `resource_governor_node.py` monitora la traccia di covarianza AMCL ($P_{xx} + P_{yy} < 0.08$) e blocca il motion gate su deviazione verso `RELOCALIZING` in caso di kidnapping o disallineamento durante lo sleep.
* **Modifiche apportate:**
  - `robopy_controller/robot_ai/services/resource_governor.py`, `robopy_controller/nodes/resource_governor_node.py`
  - `robopy_controller/nodes/hailo_vlm_node.py`
  - `robopy_controller/nodes/sensor_standby_manager.py`
  - `test/unit/test_sensor_standby_manager.py`, `tests/test_resource_governor.py`
  - `docs/lessons/nav2_slam_tuning.md`, `docs/improvements/IMP-GOV-001_dynamic_resource_governor.md`
  - `fmea/dfmea.yaml`, `fmea/FMEA_EXECUTIVE_REPORT.md`



