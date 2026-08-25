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

---

## 🔄 Routine Scansione Mappatura Stanza (720° Step-and-Pause)

### Architettura Nodo Scansione Mappatura (`room_mapping_scan_node.py`)
* **Scopo:** Eseguire una rotazione completa controllata di 720° (2 giri completi) in passi discreti di 15° con una pausa di 5 secondi ad ogni stop.
* **Prevenzione Motion Blur:** Durante la pausa di 5s, il robot invia comandi nullo (`cmd_vel = 0`) consentendo alla videocamera stereo OAK-D Lite ed a RTAB-Map di catturare keyframe visivi nitidi e nuvole di punti esenti da sfocature da movimento.
* **Esecuzione:** Avviabile via `ros2 run robopy_controller room_mapping_scan_node` o direttamente tramite script `python robopy_controller/nodes/room_mapping_scan_node.py`.

---

## 🧭 NOMAD Visual Foundation Navigation & SLAM Map Recovery (Agosto 2026 - FM-NAV-010)

### Risoluzione Errori Foxglove Camera & Stallo Mappa RTAB-Map
* **Conflitto TF `odom -> base_link`:** `localization_fuser_node.py` e `fast_flow_vo_cpp` pubblicavano contemporaneamente il medesimo transform a frequenze sfasate, inducendo saltelli nell'albero TF e impedendo a RTAB-Map di agganciare la nuvola di punti della camera alla mappa. Risolto impostando `publish_tf: False` come default su `localization_fuser_node` e confinando la pubblicazione esclusivamente a `fast_flow_vo_cpp`.
* **Detection Rate RTAB-Map su RPi5:** `restart_hailo.sh` forzava `-p Rtabmap/DetectionRate:=5.0`, sovraccaricando la CPU e provocando il drop di frame in `approx_sync`. Risolto impostando rigidamente il rate a **1.5 Hz** ed espandendo `queue_size: 20` in `rtabmap.yaml`.

### Architettura NOMAD Edge (`nomad_navigator_node.py`)
* **Paradigma:** NoMaD (Goal-Masked & Goal-Conditioned Visual Navigation). Elabora un buffer di $K=3$ frame RGB ($I_{t-2}, I_{t-1}, I_t$) ridimensionati a 128x128.
* **Doppia Modalità:**
  - **Unconditioned Exploration:** Goal mascherato; la policy predice traiettorie locali verso lo spazio libero e le frontiere visive inesplorate.
  - **Goal-Conditioned Navigation:** Guida il robot verso un'immagine target ($I_{goal}$) memorizzata in ChromaDB (memoria visiva topologica).
* **Pure Pursuit Local Controller:** Converte i 6 waypoint locali in comandi di velocità `geometry_msgs/Twist` pubblicati direttamente su `/cmd_vel` con scaling adattivo in curva ($v_{max} = 0.18\,\text{m/s}$, $\omega_{max} = 0.45\,\text{rad/s}$).
* **Senza LiDAR (Attuale) vs Con LiDAR (Futuro):**
  - *Senza LiDAR:* NOMAD opera come navigatore autonomo visivo con il costmap 2.5D da Depth camera e `semantic_costmap_injector` come guardrail.
  - *Con LiDAR:* Quando verrà aggiunto un LiDAR 2D 360°, la costmap di Nav2 fungerà da validatore geometrico hard per i waypoint visivi generati da NOMAD.

### Integrazione AI Orchestrator e Tolleranza Fonetica ASR
* **QoS Matching ROS 2:** `fast_flow_vo_cpp` pubblica `/rgb/image` con QoS `RELIABLE`. Sottoscrizioni con QoS `BEST_EFFORT` non ricevono messaggi a causa dell'incompatibilità DDS. `nomad_navigator_node.py` deve usare `ReliabilityPolicy.RELIABLE`.
* **Topic di Azionamento Diretto:** In assenza di multiplexer `twist_mux`, i comandi di velocità devono essere pubblicati su `/cmd_vel` per raggiungere direttamente `waveshare_motor_driver`.
* **Tolleranza Fonetica Vocale:** Trascrizioni ASR in lingua italiana (Vosk/Gemini) trascrivono spesso l'acronimo "NOMAD" come "nomade", "nomadi", "norman", "noma", o usano espressioni verbali generiche ("esplora la stanza", "fai una ricognizione"). La `NomadExplorationSkill` include regex flessibili per intercettare sia le varianti fonetiche che i verbi diretti d'esplorazione.

---

## 🎯 Stabilità Odometria & Correzione Dinamica di Rotazione (v21.2 — Agosto 2026 - FM-NAV-012)

### Problema Riscontrato: Movimento Convulso della Camera in Foxglove durante le Rotazioni
* **Sintomi:** Quando il robot ruotava anche di pochi gradi a sinistra o a destra, la visualizzazione della camera in Foxglove si muoveva in modo convulso e saltellante, distruggendo la coerenza geometrica delle mappe SLAM in RTAB-Map anche a basse velocità.
* **Cause Radice Identificate:**
  1. **Inversione Cinematica di Heading in `waveshare_motor_driver.py`:**
     Nel calcolo dell'angolo di imbardata (yaw $\theta$) da encoder differenziali:
     - Durante una rotazione a sinistra (CCW / $+Z$ in terna destrorsa ROS 2), la ruota destra avanza ($s_R > 0$) e la ruota sinistra indietreggia ($s_L < 0$).
     - La formula corretta è $\Delta \theta = \frac{s_R - s_L}{L}$.
     - Nel driver era implementato erroneamente `delta_theta = (delta_s_left - delta_s_right) / L`, calcolando un valore **negativo** (rotazione a destra) e invertendo l'angolo reale!
  2. **Proiezione Frame Globale vs Locale nel Fallback Ruote di `fast_flow_vo_node.cpp`:**
     In caso di rapido cambio visivo o perdita temporanea di feature durante le rotazioni, il nodo attivava il fallback `wheel_delta`.
     Tuttavia, `wheel_delta_x_` e `wheel_delta_y_` venivano sommati come delta di posizione globali in terna `odom` anziché essere ruotati nella terna locale `base_link` del robot al passo precedente tramite $\theta_{prev}$. La moltiplicazione successiva per `pose_` ruotava il vettore una seconda volta, inducendo salti e traslazioni spurie.
  3. **Mancanza di Vincolo Non-Oloconomo sul Passo 2D:**
     In un robot a trazione differenziale terrestre, la traslazione laterale è fisicamente nulla ($v_y \equiv 0$). L'algoritmo visivo PnP, a causa di rumore ottico durante la rotazione, produceva componenti $t_y$ spurie (ambiguità rotazione-traslazione) che facevano sobbalzare lateralmente la camera.

### Soluzioni Implementate:
1. **Correzione Formula Cinematica in `waveshare_motor_driver.py`:**
   `delta_theta = (delta_s_right - delta_s_left) / self.rotational_wheel_separation`.
2. **Trasformazione Coordinate in `fast_flow_vo_node.cpp` (`wheelOdomCallback`):**
   ```cpp
   double dx_local = dx_global * std::cos(yaw_prev) + dy_global * std::sin(yaw_prev);
   double dy_local = -dx_global * std::sin(yaw_prev) + dy_global * std::cos(yaw_prev);
   wheel_delta_x_ += dx_local;
   wheel_delta_yaw_ += dyaw;
   ```
3. **Vincolo Non-Oloconomo Rigido in `updatePose`:**
   `delta_2d.translation() = Eigen::Vector3d(t_filtered.x(), 0.0, 0.0);`
   Eliminata qualsiasi deriva laterale o sfarfallio trasversale durante le manovre sul posto.

---

## 🧭 Pipeline NoMaD v2 Reattiva & Controllore Pure Pursuit Integrato (Agosto 2026)

### Architettura Ibrida ViNT + DDIM 4-step & Multi-Tier Fallback (FM-NOM-001..005)
* **Design Reattivo:** Il nodo `nomad_reactive_pipeline_node.py` implementa il modello NoMaD v2 a 4 Hz (250ms periodo):
  1. **ViNT Backbone:** Eseguito su Hailo-10H (Network Group A) per estrazione latente a latenza ~2.5ms.
  2. **DDIM 4-step Sampler:** Eseguito su CPU (Core 2-3) tramite ONNX Runtime (`intra_op_num_threads=2`, `inter_op_num_threads=1`).
  3. **Fallback Automatico Action Chunking:** Se la latenza DDIM supera 100ms per 2 cicli consecutivi, il nodo commuta istantaneamente sul modello MLP a singolo step (<5ms) per garantire la continuità temporale.
  4. **Filtro EMA Vettorializzato:** Smoothing $(N, 2)$ con $\alpha=0.30$ e boost dinamico ad $\alpha=0.70$ su deviazioni di yaw $>30^\circ$.
  5. **Pure Pursuit Locale:** Calcolo diretto di `/cmd_vel_nomad` con scaling cosenoidale della velocità lineare e limitazione angolare a 1.5 rad/s.
  6. **Watchdog di Sicurezza (300ms):** Se nessun nuovo path viene generato entro 300ms, il robot pubblica `Twist()` zero e stato `RECOVERY`.
  7. **Rilevamento Collisioni tramite IMU Camera (`/oak/imu/data` - FM-NOM-006):** Monitoraggio Jerk/Shock a 100 Hz con soglia calibrata ad $a_{xy} > 2.5\text{ m/s}^2$ e $J_{xy} > 28.0\text{ m/s}^3$ (LPF 15 Hz + debounce 20 ms), con FSM di arresto d'emergenza (0.2s), safe backoff (-0.10 m/s per 0.8s), rotazione di disimpegno (0.35 rad/s) e reset totale di buffer e filtri per ricalcolo immediato della traiettoria.
  8. **Protezione Pareti Bianche Monocromatiche & Sensore Ultrasonico (FM-NOM-007):** Su pareti lisce o porte senza contrasto ottico ($\sigma^2_{\text{Lap}} < 18.0$), il generatore di affordance viene inibito prima dell'urto; il sensore ultrasonico `/ultrasonic_range` a $< 0.30\text{ m}$ e il mismatch tra velocità ruote e VIO ($|v_{\text{wheel}}| > 0.05$ vs $|v_{\text{vio}}| < 0.015\text{ m/s}$) attivano istantaneamente la rotazione forzata di ricerca texture.
  9. **Protezione Meccanica da Stallo e Sovracorrente Motori (FM-MOT-004):** Se la velocità comandata $|v_{\text{cmd}}| > 0.08\text{ m/s}$ persiste con ruote bloccate $|v_{\text{wheel}}| < 0.02\text{ m/s}$ per $> 0.40\text{ s}$, la potenza PWM viene disarmata e si avvia la manovra di backoff per prevenire sovraccarichi termici.
  10. **Persistenza Database RTAB-Map & Rimozione `--delete_db_on_start` (FM-NAV-014):** In produzione il flag `--delete_db_on_start` è severamente vietato (Regola 6 di `marcus_core_rules.md`). La mappa SLAM deve persistere tra i riavvii del servizio e del watchdog per consentire il loop closure continuo e la navigazione basata su mappa globale consolidata.
  11. **Allineamento Topic Odometria per Nav2 BT Navigator (FM-NAV-013):** `fast_flow_vo_cpp` pubblica l'odometria VIO a 30 Hz su `/odom`. Il parametro `odom_topic` in `bt_navigator` (`nav2_params_jazzy.yaml`) deve essere configurato su `/odom` (e non `/vo/odom`) per garantire che i condition node del Behavior Tree ricevano tempestivamente la velocità e la posa del robot.
  12. **Iniezione Ostacoli Semantici NPU & Deproiezione 3D (FM-SEM-001):** `hailo_bridge_node_cpp` pubblica bounding box 2D normalizzati su `/hailo/semantic_objects`. `semantic_costmap_injector.py` deproietta automaticamente il centro del bounding box nello spazio 3D campionando la matrice di profondità (`/camera/depth/image_raw`) con patch 3x3 mediana, inserendo l'ostacolo semantico 3D proiettato a 2D nella costmap Nav2 anche senza VLM cloud.
  13. **Contenimento Memoria RAM & Raycasting Vettorializzato su Pi 5 (FM-MEM-011):** `semantic_costmap_injector.py` impone un cap a 500 ostacoli attivi con politica FIFO per prevenire leak di memoria durante lunghe sessioni. Il raycasting degli ostacoli negativi esegue un singolo lookup TF per frame con rotazione matriciale numpy vettorizzata, riducendo del 99% l'overhead TF su CPU.

