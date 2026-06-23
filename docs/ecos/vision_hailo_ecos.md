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
