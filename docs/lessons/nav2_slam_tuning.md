# Lezioni Apprese - Navigazione & SLAM Tuning

Questo documento raccoglie le lezioni apprese e le configurazioni relative a RTAB-Map, Nav2 e la gestione dei sensori geometrici di Marcus.

---

## 🗺️ RTAB-Map e Costmap Nav2

### Divieto di STVL e Proiezione 2.5D
* **Regola:** Non implementare la mappatura volumetrica continua 3D (STVL) su Raspberry Pi 5. La CPU ed il bus di memoria non sono in grado di sostenerne il calcolo.
* **Soluzione:** Proiettare gli ostacoli 3D estratti dalla visione artificiale in ostacoli costmap 2D localizzati (2.5D). Il nodo `semantic_costmap_injector.py` converte i bounding box tridimensionali in coordinate 2D e li inietta nel costmap Nav2 con un decadimento temporale associato.
* **Rilevamento Ostacoli Negativi (Scale / Dislivelli - FM-NAV-009):** Il nodo `semantic_costmap_injector.py` sottoscrive `/camera/depth/image_raw` ed esegue il *Depth-Gradient Hole Raycasting* lungo i campioni verticali dell'immagine. Quando rileva un dislivello $\Delta Z > 15\text{ cm}$ sotto il piano del terreno ($Z_{base} = 0.0\text{m}$), inietta ostacoli letali sul topic `/hailo_semantic_obstacles_pc` per forzare Nav2 ad evitare il bordo del precipizio.

### Allineamento dei Frame ID e `/scan`
* **Errore:** RTAB-Map fallisce l'aggiornamento con il messaggio `Could not convert laser scan msg! Aborting rtabmap update...`.
* **Causa:** Il frame associato ai messaggi del topic `/scan` generati da `depthimage_to_laserscan` non corrisponde all'albero statico delle trasformazioni geometriche.
* **Risoluzione:** Allineare i parametri di `depthimage_to_laserscan` impostando l'argomento `-p output_frame:=camera_link` per agganciarlo alla static TF `base_link ➔ camera_link`.

### Mappatura 2D Occupancy Grid Senza LiDAR (`Grid/FromDepth: true`)
* **Problema:** `/map` non viene pubblicato se RTAB-Map è sottoscritto a `/scan` ma `approx_sync` attende il sincronismo perfetto tra 4 topic (`/rgb/image`, `/camera/depth/image_raw`, `/camera/camera_info`, `/scan`). Inoltre un guard `has_rtabmap_sub` nel publisher VIO impediva la scoperta DDS dei topic delle immagini.
* **Soluzione:**
  1. Rimuovere il guard di sottoscrizione in `fast_flow_vo_node.cpp` per garantire la pubblicazione continua di RGB, Depth e CameraInfo a 20 Hz.
  2. Impostare `subscribe_scan: false` in `rtabmap.yaml` e rimuovere `-r scan:=/scan` da `restart_hailo.sh`.
  3. RTAB-Map con `Grid/Sensor: 1` e `Grid/FromDepth: true` genera nativamente la griglia d'occupabilità 2D `/map` direttamente dall'immagine di profondità RGB-D a 1.5 Hz.

### Risoluzione Cicli TF (`base_link -> base_link`) e Matching `/camera/camera_info`
* **Problema:** Foxglove e tf2 segnalavano `Transform tree cycle detected: parent "base_link" -> child "base_link"` e un warning `!` su `/camera/camera_info`.
* **Causa:** Il publisher `/vo/guess` in `fast_flow_vo_node.cpp` impostava sia `frame_id` che `child_frame_id` a `base_link`. Inoltre Foxglove richiede `/rgb/camera_info` accoppiato a `/rgb/image`.
* **Soluzione:**
  1. Impostato `msg.header.frame_id = config_.odom_frame` ("odom") e `msg.child_frame_id = config_.base_frame` ("base_link") in `publishGuess()`.
  2. Aggiunto il publisher duplicato `/rgb/camera_info` in `fast_flow_vo_node.cpp` per eliminare l'allarme dei visualizzatori ROS.

---

## 📐 Trasformazioni Geometriche (Static TFs)

### Albero delle Trasformazioni Standard ROS
* La gerarchia dei frame per Marcus deve rispettare rigorosamente gli standard ROS (REP-103 e REP-105):
  ```
  odom ➔ base_link ➔ camera_link ➔ camera_optical_frame
                  ➔ imu_link
  ```
* **camera_link:** Convenzione robotica (X=avanti, Y=sinistra, Z=alto).
* **camera_optical_frame:** Convenzione computer vision (X=destra, Y=basso, Z=avanti). Utilizzato come frame di riferimento per i dati dell'immagine e della nuvola di punti della camera.

---

## 🤖 Nav2 Behavior Trees e Parametri

### Percorsi dei File XML per i Behavior Tree (BT)
* **Problema:** Il nodo `bt_navigator` di Nav2 fallisce l'attivazione a causa di percorsi XML errati o non trovati nei parametri YAML.
* **Soluzione:** Nelle configurazioni `nav2_params_jazzy.yaml` e `nav2_params.yaml`, i parametri `default_nav_to_pose_bt_xml` e `default_bt_xml_filename` devono puntare esattamente al percorso del workspace installato:
  `/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/nav2_survival_bt.xml`

---

## 🗺️ Localizzazione Multi-sessione & Kidnapped Robot

### Mappatura Incrementale e Sessioni Multiple
* **Contesto:** Se il robot viene spento e riacceso in un altro luogo (scenario del robot rapito), RTAB-Map non deve sovrascrivere o corrompere la mappa precedente, ma creare una nuova sessione all'interno del medesimo database per consentire la successiva fusione delle mappe (loop closure) quando il robot torna in zone note.
* **Soluzione:** Configurare `Mem/IncrementalMemory: "true"` per forzare la modalità SLAM incrementale. Evitare l'argomento `--delete_db_on_start` o `-d` nel nodo RTAB-Map se si desidera la persistenza multi-sessione.

### Waypoint e Memorie Dinamiche filtrate per Sessione
* **Problema:** Quando si opera su sessioni diverse, i vecchi waypoint o le memorie visive non più allineate geometricamente con il sistema di coordinate locale dell'odometria corrente possono indurre in errore la navigazione.
* **Soluzione:** Sottoscrivere al topic `/rtabmap/info` per estrarre l'active session ID (`map_id`) pubblicato nelle statistiche interne di RTAB-Map. Filtrare le query in ChromaDB (`MemoryType.LOCATION` e `MemoryType.VISUAL_OBSERVATION`) per l'ID della sessione attiva prima di considerare i waypoint storici o statici di fallback.

---

## 🔧 EKF (robot_localization) — Problemi noti su Marcus

### OAK-D IMU: `orientation_covariance[0] = -1.0` blocca l'EKF
* **Causa Radice:** Il driver OAK-D DepthAI SDK pubblica `orientation_covariance[0] = -1.0` nel messaggio `sensor_msgs/Imu`. Secondo lo standard ROS2, questo flag indica "dati di orientamento non disponibili". Il nodo `ekf_filter_node` di `robot_localization` interpreta questo flag e **non inizializza** il filtro se non riceve un messaggio con orientamento valido, quindi **non pubblica mai il TF `odom→base_link`** anche con `publish_tf: true` nel YAML.
* **Fix applicato:** Aggiunta di `initial_estimate_covariance` al `ekf.yaml` con valori grandi → l'EKF si inizializza senza aspettare l'orientamento IMU.
* **Fix definitivo:** Sostituzione EKF con `spectacular_vio_node.py` (vedi sotto).

### Zombie EKF: istanze multiple dal multi-restart
* **Causa:** Ogni `restart_hailo.sh` lanciato manualmente aggiunge una nuova istanza `robot_localization` se il pkill non termina la precedente in tempo.
* **Effetto:** Saturazione DDS (`Failed to find a free participant index for domain 42`).
* **Fix:** Il blocco `pkill` in `restart_hailo.sh` ora include esplicitamente `robot_localization`, `ekf_node`, `spectacular_vio_node`, `fast_flow_vo`, `superpoint_node`.

---

## 🎯 SpectacularVIO — Architettura Nodo VIO Nativo

### Struttura `spectacular_vio_node.py`
* **Nodo:** `robopy_controller.nodes.spectacular_vio_node`
* **Autorità TF:** Unica autorità su `odom → base_link` a **30 Hz**
* **Strategia di fusione (ibrida Gyro + Encoder):**
  - **Heading (θ):** Integrazione giroscopio BMI270 OAK-D a 200 Hz (via DepthAI SDK nativo). Meno drift angolare rispetto agli encoder discreti.
  - **Posizione (x, y):** Encoder ruote (odometria differenziale). Più stabile del VIO puro per traslazioni corte.
  - **Velocità:** Encoder (lineare) + Giroscopio (angolare).
* **Calibrazione bias:** I primi 100 campioni IMU vengono usati per stimare il bias del giroscopio Z (robot fermo all'avvio).
* **Failsafe:** Se OAK-D non è accessibile → modalità encoder-only automatica.
* **Topic output:**
  - TF `odom → base_link` (30 Hz)
  - `/odometry/filtered` (nav_msgs/Odometry, consumato da RTAB-Map)
  - `/vio/pose` (geometry_msgs/PoseStamped, per Foxglove debug)

### Perché NON spectacularAI pip package
* Il pacchetto pip `spectacularAI` **non ha wheel per ARM64** (RPi5). Richiede licensing commerciale per l'SDK ARM.
* Soluzione alternativa: uso del modulo IMU nativo di DepthAI SDK (`depthai>=2.31.0`), già installato su Marcus.

### RTAB-Map post-VIO
* `rtabmap.yaml` non ha più sezione `rgbd_odometry` (rimossa).
* Loop closure: `Kp/DetectorStrategy: 8` → DBoW3 nativo C++ (ORB/FAST), **no SuperPoint Python**.
* `Rtabmap/DetectionRate: 1.5` Hz → Consumo CPU <10% su Raspberry Pi 5.

---

## ⚡ FastFlowVO C++ (`fast_flow_vo_cpp`) — Architettura VIO C++ Nativo su MyriadX

### Componenti ed Offloading Hardware
* **Eseguibile:** `fast_flow_vo_cpp` (compilato in C++17 nativo via `colcon build`).
* **MyriadX VPU Offloading:** `dai::node::FeatureTracker` + KLT Optical Flow hardware su chip OAK-D Lite. Zero carico CPU su Raspberry Pi 5 per l'estrazione e tracciamento dei punti salienti.
* **Fusione Inerziale 200 Hz:** Lettura diretta del sensore Bosch BMI270 (modalità `RAW`) via `dai::node::IMU`.
* **Solver PnP C++:** EPNP RANSAC 3D in C++ + Integrazione Giroscopio Z per orientamento e soppressione dello slittamento delle ruote.
* **Autorità TF `odom -> base_link`:** Assegnata a `fast_flow_vo_cpp` a **30 Hz**.
* **Topic output:** `/odom` (nav_msgs/Odometry), `/rgb/image`, `/camera/depth/image_raw`, `/camera/camera_info`.

---

## 🛡️ Dedicated Localization Fuser & Health Supervisor (Luglio 2026)

### Architettura Fuser Dedicato (`localization_fuser_node.py`)
* **Metriche Qualità VIO:** Pubblica la confidenza VIO $C \in [0, 100]$ su `/vins/quality_metrics` combinando numero di feature attive ($N_{target}=150$), errore di riproiezione ($E_{thresh}=3.0\,\text{px}$) e condizionamento Hessiana di marginalizzazione.
* **Inflazione Covarianza Scalata & Saturata:** L'inflazione $R_{VIO}$ segue la funzione master $R_{VIO} = R_{base} \cdot \min(M_{max}, S(C_{smooth}) \cdot e^{\alpha \Delta t})$ dove $M_{max} = 100.0$ costituisce il tetto rigido contro instabilità numeriche nell'EKF.
* **Smoothing Adattivo Window Switching:** La finestra EMA $T_{EMA}$ passa da $0.5\,\text{s}$ a $0.1\,\text{s}$ durante rotazioni rapide ($\omega_{IMU} > 0.3\,\text{rad/s}$) per rilevare sfarfallii e cali di confidenza entro 100 ms.
* **Wheel Slip Detection:** Confronto continuo $\left| \omega_{wheels} - \omega_{IMU} \right| > 0.25\,\text{rad/s}$. In caso di slittamento, la fusione riduce temporaneamente il peso dell'odometria ruote.
* **Piano Pavimento Dinamico (200 Hz):** Vettore normale $\hat{n}_{floor}(t) = [-\sin\theta, \sin\phi\cos\theta, \cos\phi\cos\theta]^T$ ricalcolato ad ogni pacchetto IMU per adeguare la tolleranza del pavimento a rampe ed inclinazioni reali.

### System Health Supervisor (`robot_health_supervisor.py`)
* **Stati:** GREEN (normal), YELLOW (velocità max -50%), RED (arresto immediato).
* **Hard Arbitration Priority 0:** Pubblica pacchetti di frenata attiva su `/cmd_vel_mux/input/safety_override`, prevaricando Nav2 e teleoperazione sul nodo `twist_mux`.

---

## 📈 MPPI Trajectory Telemetry & Offline Background Optimization (FM-NAV-008)

### Architettura Telemetria & Autotuning (`mppi_telemetry_logger.py` & `mppi_offline_autotuner.py`)
* **Telemetry Logger:** Registra a 1 Hz lo scostamento trasversale (Cross-Track Error $e_{ct}$), l'oscillazione angolare (Angular Jitter $J_\omega$) ed i cambi di ritmo stop-and-go in `~/.marcus/telemetry/mppi_nav_telemetry.jsonl` con impatto CPU <1% su RPi5.
* **Offline Autotuner:** Job notturno / idle che calcola la funzione di costo $J = 15 J_\omega + 25 \bar{e}_{ct} + 2 N_{stop}$ per ottimizzare euristica `inflation_radius`, `cost_scaling_factor` e i pesi MPPI (`PathAlign`, `Obstacle`) aggiornando automaticamente `nav2_params.yaml`.



