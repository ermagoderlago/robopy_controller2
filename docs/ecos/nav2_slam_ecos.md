# Engineering Change Orders - Navigazione & SLAM

Questo documento raccoglie la cronologia delle modifiche ingegneristiche (ECO) apportate al modulo di navigazione, SLAM e allineamento geometrico di Marcus.

---

## 📈 ECO-2026-06-13-001: ROS 2 Jazzy Compilation Stability, Depth Image Compatibility, and NAV2 Launch Fixes
* **Stato:** ✅ **Completato, Sincronizzato e Compilato sul Robot**
* **Descrizione:** Risoluzione delle problematiche di stabilità della compilazione e integrazione hardware/software della telecamera OAK-D Lite con NAV2 sul Raspberry Pi 5.
* **Modifiche apportate:**
  * Modificata la codifica del frame di profondità da `"mono16"` a `"16UC1"` in `oak_superpoint_odometry_node.cpp` per garantire la compatibilità con il nodo `depthimage_to_laserscan`.
  * Aggiornati i parametri `default_nav_to_pose_bt_xml` e `default_bt_xml_filename` in `nav2_params_jazzy.yaml` e `nav2_params.yaml` per puntare al percorso corretto installato `/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/nav2_survival_bt.xml`.

---

## 📈 ECO-2026-06-14-001: USB Power Stabilization and Depth-to-LaserScan TF Fixes
* **Stato:** ✅ **Completato, Sincronizzato e Attivo sul Robot**
* **Descrizione:** Diagnosi e risoluzione dei blocchi di tensione indotti sul bus USB del Pi 5 all'accensione della camera (SSD andava in sola lettura per brownout) tramite l'introduzione di un USB Hub alimentato esternamente. Risoluzione degli errori di conversione laser scan di RTAB-Map SLAM tramite l'allineamento dei parametri di `depthimage_to_laserscan`.
* **Modifiche apportate:**
  * Aggiunto il parametro `-p output_frame:=camera_link` all'invocazione di `depthimage_to_laserscan_node` nel launch script `restart_hailo.sh` per allineare il frame_id del topic `/scan` alle static TFs caricate in memoria.
  * Spostate camera OAK-D Lite ed SSD su un hub USB alimentato esternamente (tensione stabile, `vcgencmd get_throttled` fisso a `0x0`). RTAB-Map ora si sincronizza correttamente a 1.0Hz aggiornando le mappe.

---

## 📈 ECO-2026-06-25-001: RTAB-Map Multi-session Configuration and Dynamic ChromaDB Waypoint Navigation
* **Stato:** ✅ **Completato in Workspace Locale (In Attesa di Avvio Robot)**
* **Descrizione:** Integrazione della navigazione semantica dinamica tramite database vettoriale locale (ChromaDB) e sincronizzazione multi-sessione con RTAB-Map SLAM per risolvere lo scenario del robot rapito (kidnapped robot) senza alterare/sovrascrivere le mappe passate.
* **Modifiche apportate:**
  * Configurato il parametro `Mem/IncrementalMemory` a `"true"` in `rtabmap.yaml` e `rtabmap_params.yaml` per consentire il salvataggio incrementale di più sessioni di mappatura.
  * Modificato `orchestrator.py` per iniettare `ChromaNativeStore` in `NavigationSkill`.
  * Aggiornata la skill di navigazione `navigation_skill.py` per tracciare dinamicamente l'active session ID tramite sottoscrizione a `/rtabmap/info`.
  * Implementato in `_handle_goto` un doppio livello di ricerca in ChromaDB (`MemoryType.LOCATION` e `MemoryType.VISUAL_OBSERVATION`) con ordinamento temporale e filtraggio per session ID per consentire il raggiungimento di oggetti rilevati visivamente e stanze apprese dinamicamente.
  * Aggiornato `add_waypoint` per persistere i nuovi landmark su ChromaDB.
  * Sincronizzata la sessione anche su `visual_memory_service.py` per associare le osservazioni all'active session ID.
  * Risolto un bug nel fallback locale Qwen2-VL che sovrascriveva la risposta VQA corretta con un NameError su `response.text`.

---

## 📈 ECO-2026-07-26-001: Sostituzione EKF con SpectacularVIO — Refactoring Stack Odometria

* **Stato:** ✅ **Implementato — In attesa di verifica su robot fisico**
* **Descrizione:** Rimozione completa di `robot_localization` (EKF) e sostituzione con nodo VIO nativo `spectacular_vio_node.py` basato su DepthAI SDK. Il nuovo nodo è l'unica autorità TF `odom→base_link` a 30 Hz. Rimosso SuperPoint Python dalla loop closure di RTAB-Map.

* **Causa che ha motivato il cambio:**
  * `robot_localization` EKF non pubblicava il TF `odom→base_link` perché il driver OAK-D pubblica `orientation_covariance[0] = -1.0` (flag ROS2 = "orientamento non disponibile") → l'EKF si bloccava in attesa di un orientamento valido.
  * Soluzione temporanea applicata: `initial_estimate_covariance` nel YAML → EKF tornato operativo.
  * Soluzione definitiva: Sostituzione con fusione nativa IMU+Encoder.

* **Modifiche strutturali:**
  * **[NUOVO]** `robopy_controller/nodes/spectacular_vio_node.py` — Nodo VIO con:
    - Lettura IMU BMI270 via DepthAI SDK a 200 Hz (pipeline DAI nativa)
    - Calibrazione bias giroscopio automatica all'avvio (100 campioni)
    - Fusione heading: giroscopio integrato (85% peso) + encoder (fallback)
    - Posizione x,y: encoder ruote (più stabile per traslazione breve)
    - Failsafe automatico a encoder-only se OAK-D non disponibile
  * **[MODIFICATO]** `restart_hailo.sh`:
    - Rimosso blocco `ekf_node` / `robot_localization`
    - Aggiunto avvio `spectacular_vio_node`
    - Kill section aggiornata: `spectacular_vio_node`, `fast_flow_vo`, `superpoint_node`, `madgwick_node`, `oak_visual_odometry_cpp`, `rgbd_odometry`
  * **[MODIFICATO]** `robopy_controller/config/rtabmap.yaml`:
    - Rimossa sezione `rgbd_odometry` (non più usata)
    - Confermato `Vis/CorType: 0` (GFTT/BRIEF nativo, NO SuperPoint)
  * **[MODIFICATO]** `setup.py`: aggiunto entry point `spectacular_vio_node`
  * **[MODIFICATO]** `docs/lessons/nav2_slam_tuning.md`: documentate cause EKF + architettura VIO

* **Architettura TF risultante:**
  ```
  map ──(RTAB-Map, 2Hz)──► odom ──(SpectacularVIO, 30Hz)──► base_link
  ```

* **Note su spectacularAI pip:**
  * Il pacchetto `spectacularAI` su PyPI non ha wheel ARM64 (solo x86_64).
  * Implementato VIO equivalente con DepthAI SDK nativo (`depthai==2.31.0`), già installato.

---

## 📈 ECO-2026-07-26-002: Refactoring VIO Nativo C++ (`fast_flow_vo_cpp`) + RTAB-Map DBoW3
* **Stato:** ✅ **Completato, Sincronizzato, Compilato e Verificato su Robot Fisico**
* **Descrizione:** Implementazione del nodo VIO C++ nativo `fast_flow_vo_cpp` basato su hardware offloading MyriadX VPU (`dai::node::FeatureTracker` + IMU BMI270 200 Hz) e solver C++ EPNP 3D. Rimosse dipendenze SpectacularAI (licenza a pagamento/non disponibile su ARM64), Python SuperPoint ed EKF. Assegnata l'autorità TF `odom -> base_link` esclusiva a `fast_flow_vo_cpp` e `map -> odom` a RTAB-Map via DBoW3 nativo C++ (`Kp/DetectorStrategy: 8`).
* **Modifiche apportate:**
  * **[SORGENTE C++]** `src/fast_flow_vo_node.cpp` e `src/fast_flow_vo_node.hpp`:
    - Aggiornati topic odometria a `/odom` e frame ID di default a `camera_optical_frame`.
    - Offloading hardware del tracciamento feature su MyriadX VPU (<10% CPU su RPi5).
  * **[CONFIG SLAM]** `robopy_controller/config/rtabmap.yaml`:
    - Impostati `subscribe_scan: false`, `Rtabmap/DetectionRate: "1.5"`, `Kp/DetectorStrategy: "8"` (DBoW3 ORB/FAST).
  * **[ORCHESTRATORE]** `restart_hailo.sh`:
    - Disabilitato `oak_superpoint_odometry_cpp`.
    - Avvio in background di `fast_flow_vo_cpp --ros-args -p publish_tf:=true -p odom_frame:=odom -p base_frame:=base_link -p camera_frame:=camera_optical_frame`.
    - Aggiunto static TF publisher per `oak_left_camera_optical_frame`.
  * **[DRIVER MOTORI]** `robopy_controller/nodes/waveshare_motor_driver.py`:
    - Aggiunta finestra di soppressione di 350 ms per i comandi zero provenienti dai nodi inattivi di Nav2.
  * **[VERIFICA FISICA]** `scripts/test_spin_360.py`:
    - Sottoscrizione aggiornata a `/odom`.
    - Test 360° eseguito con successo: rotazione VIO misurata **358.4°** su **360.0°** (Errore <0.4%), compensando 63.3° di slittamento meccanico degli encoder delle ruote.


