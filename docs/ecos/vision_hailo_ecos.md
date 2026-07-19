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
