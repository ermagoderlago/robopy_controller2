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

---

## 📈 ECO-2026-07-27-001: Refactoring VIO VINS-Fusion C++, CAD Static TFs, Anti-Reflection Floor Filter e White Wall Guardrails
* **Stato:** ✅ **Implementato e Committato in Workspace Locale (Pronto per Sincronizzazione)**
* **Descrizione:** Integrazione completa del refactoring per odometria visivo-inerziale VINS-Fusion C++, allineamento delle trasformate statiche alle quote esatte CAD 3D, applicazione del filtro ground clearance anti-riflesso per la costmap 2D, e configurazione dei guardrail di stabilità RTAB-Map per pareti spoglie.
* **Modifiche apportate:**
  * **[DRIVER MOTORI ESP32]** `robopy_controller/nodes/waveshare_motor_driver.py`:
    - Impostato `publish_tf` default a `False` per non pubblicare la TF `odom -> base_link` (riservata a VIO).
    - Aggiunto parametro `odom_topic` con valore `/odom_wheel` per isolare la telemetria ruote da `/odom`.
  * **[CAD STATIC TRANSFORMS]** `restart_hailo.sh`:
    - Aggiornate quote CAD: `base_link -> oak_camera_link` con $X=0.0332$ m, $Y=0.0$, $Z=0.2616$ m, Pitch $=8.00^\circ$ ($0.1396$ rad).
    - Definite TF statiche per `oak_imu_frame`, `camera_optical_frame`, e `oak_left_camera_optical_frame`.
  * **[FILTRO ANTI-RIFLESSO PAVIMENTO]** `restart_hailo.sh`:
    - Configurato `depthimage_to_laserscan_node` con `target_frame:=base_link`, `min_height:=0.04` (4cm dal suolo) e `max_height:=0.80`. Scarta i riflessi del pavimento a $Z \le 0$.
  * **[GUARDRAIL RTAB-MAP & SLAM]** `robopy_controller/config/rtabmap.yaml`:
    - Abilitato `subscribe_scan: true` per il LaserScan 2D sintetico.
    - Configurato `Grid/Sensor: "1"` e `Grid/FromDepth: "true"` (2D occupancy grid dal depth).
    - Aggiunti guardrail per pareti spoglie: `Kp/MinFeatures: "30"`, `Vis/MinInliers: "15"`, e `RGBD/LoopClosureRejectionWithGraph: "true"`.

---

## 📈 ECO-2026-07-27-002: State Machine Fallback Odometria Ruote in `fast_flow_vo_cpp`
* **Stato:** ✅ **Implementato e Compilato in Workspace Locale**
* **Descrizione:** Risoluzione della disconnessione tra l'odometria ruote ESP32 ed il tracciamento visivo VIO. Integrata una State Machine di Fallback in `fast_flow_vo_cpp` per consumare il topic `/odom_wheel` e garantire la continuità ininterrotta della TF `odom -> base_link` a 30 Hz in caso di perdita temporanea di tracciamento visivo (pareti spoglie, blackout, blip USB).
* **Modifiche apportate:**
  * **[ENGINE C++ VIO]** `src/fast_flow_vo_node.hpp` e `src/fast_flow_vo_node.cpp`:
    - Sottoscritto topic `/odom_wheel` (`nav_msgs/msg/Odometry`).
    - Implementato `wheelOdomCallback` per accumulare i delta di movimento della base $(\Delta x, \Delta y, \Delta \theta)_{wheel}$.
    - In `processFrame()`, quando il PnP visivo ha successo, aggiorna la posa con la VIO e resetta i delta ruota accumulati.
    - Quando la VIO perde il tracciamento visivo (`TRACKING_LOST` o inliers insufficienti), applica atomicamente i delta dell'odometria ruote alla posa $T_{odom \to base}$, mantenendo attiva e fluida la TF `odom -> base_link` senza strappi o nodi EKF secondari.
  * **[ORCHESTRATORE]** `restart_hailo.sh`:
    - Rinominato il file di log da `vins_fusion.log` a `fast_flow_vo.log` per riflettere accuratamente l'engine in esecuzione.

---

## 📈 ECO-2026-07-27-003: Auto-Calibrazione Online Ruote-VIO, Covarianza Dinamica Adattiva e Upgrade CalibrationSkill
* **Stato:** ✅ **Implementato e Committato in Workspace Locale (Pronto per Sincronizzazione)**
* **Descrizione:** Implementazione dei 5 miglioramenti ad alto impatto per l'odometria ed il tracciamento di Marcus: stima online in continuo del fattore di scala e dell'offset angolare ruote-VIO in `fast_flow_vo_cpp`, covarianza dinamica adattiva su `/odom`, e rifattorizzazione completa della skill AI `calibration_skill.py`.
* **Modifiche apportate:**
  * **[AUTO-CALIBRAZIONE ONLINE RUOTE-VIO]** `src/fast_flow_vo_node.hpp` e `src/fast_flow_vo_node.cpp`:
    - In regime visivo valido (`TRACKING_GOOD`), stima in continuo il fattore di scala ruote `wheel_scale_` ed il disallineamento angolare `wheel_yaw_offset_`.
    - Durante il fallback ruote su parete spoglia, applica la trasformazione ruota scalata e ruotata secondo gli ultimi parametri calibrati, azzerando i salti di heading.
  * **[COVARIANZA DINAMICA ADATTIVA]** `src/fast_flow_vo_node.cpp`:
    - In `publishOdometry()`, imposta la covarianza diagonale in modo dinamico: $10^{-5}$ per `TRACKING_GOOD`, $10^{-3}$ per `TRACKING_WEAK`, e $10^{-2}$ per `using_wheel_fallback_`.
  * **[SKILL AI CALIBRAZIONE]** `robopy_controller/robot_ai/skills/builtin/calibration_skill.py`:
    - Doppia sottoscrizione contemporanea su `/odom` (VIO) e `/odom_wheel` (Encoder ESP32).
    - Impostate velocità di test sicure per ambienti interni ($v_x = 0.15$ m/s, $w_z = 0.4$ rad/s).
    - Calcola esattamente i rapporti di scala $s_{linear}$ e $s_{angular}$ e salva i valori consigliati di `ticks_per_rev` e `rotational_wheel_separation` in `/home/robopy/robopy/logs/calibration_report.md`.

---

## 📈 ECO-2026-07-27-004: RTAB-Map Pure RGB-D 2D Grid Mapping, TF Cycle Resolution & CameraInfo Matching
* **Stato:** ✅ **Completato e Verificato su Robot Fisico**
* **Descrizione:** Correzione dell'inclinazione fisica della telecamera a 8° verso l'alto (-0.1396 rad pitch), risoluzione dello stallo di generazione della mappa 2D d'occupabilità (`/map`), eliminazione del ciclo TF `base_link -> base_link`, e pubblicazione del topic accoppiato `/rgb/camera_info`.
* **Modifiche apportate:**
  * **[STATIC TF & URDF CAMERA PITCH]** `restart_hailo.sh` e `urdf/robopy.urdf`:
    - Impostato pitch static TF `--pitch -0.1396` (-8° inclinazione verso l'alto) per `base_link -> oak_camera_link`.
    - Aggiornato `camera_joint` origin in `robopy.urdf` con `rpy="0 -0.1396 0"`.
  * **[RTAB-MAP 2D OCCUPANCY GRID MAPPING]** `robopy_controller/config/rtabmap.yaml` e `restart_hailo.sh`:
    - Impostato `subscribe_scan: false` in `rtabmap.yaml` e rimosso `-r scan:=/scan` dal comando di avvio RTAB-Map in `restart_hailo.sh`.
    - RTAB-Map genera direttamente la griglia d'occupabilità 2D `/map` da RGB-D (`Grid/FromDepth: true`, `Grid/Sensor: 1`) a 1.5 Hz senza stalli DDS.
  * **[DDS DISCOVERY DEADLOCK FIX]** `src/fast_flow_vo_node.cpp`:
    - Rimosso il guard di sottoscrizione `has_rtabmap_sub` in `publishImages()`.
  * **[RISOLUZIONE CICLO TF & CAMERA_INFO]** `src/fast_flow_vo_node.hpp` e `src/fast_flow_vo_node.cpp`:
    - Corretto `publishGuess()`: impostato `msg.header.frame_id = config_.odom_frame` ("odom") e `msg.child_frame_id = config_.base_frame` ("base_link"), eliminando l'errore `Transform tree cycle detected: parent "base_link" -> child "base_link"`.
    - Aggiunto publisher `rgb_camera_info_pub_` sul topic `/rgb/camera_info` in sintonia con `/rgb/image`, eliminando i warning di Foxglove Studio.


