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

---

## 🛡️ Mitigazioni DFMEA Odometria & Navigazione (Agosto 2026)

### Gating Covariante e Freeze Calibrazione Ruote su Wheel Slip (FM-NAV-015)
* **Problema:** Nelle transizioni di pavimentazione (piastrelle $\rightarrow$ tappeto), lo slittamento delle ruote durante cali di tracking VIO avvelenava il fattore di scala metrica `wheel_scale_`.
* **Soluzione:** `fast_flow_vo_node.cpp` confronta in tempo reale l'accelerazione lineare delle ruote con l'accelerazione longitudinale IMU. Se $|\Delta a| > 0.25\text{ m/s}^2$ o gli inlier scendono sotto 30, la calibrazione viene congelata e `wheel_scale_` rimane confinato nel range di sicurezza $[0.85, 1.15]$.

### Blindatura Anti-Aliasing e Strict Geometric Verification in RTAB-Map (FM-NAV-016)
* **Problema:** Corridoi simmetrici e porte identiche inducevano falsi loop closure in DBoW3, distorcendo l'albero TF `map -> odom`.
* **Soluzione:** In `rtabmap.yaml`, soglia di similarità `Rtabmap/LoopThr` innalzata a `0.20`, tolleranza PnP `Vis/PnPReprojError: "2.5"`, e maschera ROI pavimento `Kp/RoiRatios: "0.0 0.0 0.10 0.0"` per escludere il 10% inferiore dell'immagine e prevenire falsi agganci su riflessi/fughe di piastrelle.

### ZUPT Dinamico & Compensazione Deriva Termica Bias Giroscopio Z (FM-NAV-017)
* **Problema:** Su sessioni lunghe (>30 min), il riscaldamento del Pi 5 e dell'Hailo-10H riscaldava l'IMU BMI270, generando drift termico dello zero-rate offset sull'asse Z.
* **Soluzione:** In `fast_flow_vo_node.cpp`, quando il robot è fermo a comandi nulli, il nodo accumula 50 campioni di $\omega_z$ raw per stimare il bias dinamico `gyro_z_bias_` e lo sottrae real-time da ogni pacchetto prima del deadband e dell'integrazione di posa.

### Safe Recovery Sequence & Costmap Persistence Policy (FM-NAV-019)
* **Problema:** In spazi stretti, lo svuotamento della costmap unito a uno spin a 90° faceva urtare il robot contro ostacoli laterali o posteriori non confermati dalla camera frontale (FOV $72.9^\circ$).
* **Soluzione:** Impostato `combination_method: 1` (Maximum) in `nav2_params_jazzy.yaml` per preservare gli ostacoli noti, e sostituito lo spin a 90° in `nav2_survival_bt.xml` con un disimpegno dolce (micro-backup da 8 cm, attesa di ricaricamento sensoriale e ripianificazione diretta).

### Allocazione Storage Database RTAB-Map su SSD e Prevenzione Runaway Log (FM-NAV-020)
* **Problema:** La persistenza SLAM non vincolata su MicroSD (`/home/robopy/.ros/rtabmap.db`) ha portato il database a superare 16 GB (95.959 nodi accumulati). A seguito di corruzione o svuotamento del dizionario visuale DBoW3 (`dict size=0`), RTAB-Map è entrato in un loop continuo di errori (`VWDictionary.cpp:741::addWordRef()`), generando oltre 4 GB di log in `/home/robopy/robopy/logs/rtabmap.log` e saturando al 100% la partizione root `/` (0 byte disponibili). Questo ha indotto `[Errno 28] No space left on device` a catena su tutti i processi Python e un Load Average della CPU a 18.10.
* **Soluzione:**
  1. **Storage su SSD Esterno:** `rtabmap.db` deve risiedere permanentemente su `/mnt/ssd/rtabmap.db` (SSD da 240GB con oltre 170GB liberi), collegato tramite symlink da `/home/robopy/.ros/rtabmap.db` per garantire compatibilità trasparente.
  2. **Ripristino Mappa Valida:** Utilizzata la mappa consolidata `salotto.db` (313 MB, 1.352 nodi e 335.360 parole visuali integre), azzerando istantaneamente il loop di errore `VWDictionary`.
  3. **Bonifica Log:** Troncatura periodica di `rtabmap.log` e svuotamento di `~/.ros/log/*`, liberando oltre 23 GB di spazio sulla MicroSD.

### Prevenzione Crash UException `Memory.cpp:3473::addLink()` e Modalità Localizzazione Pura su Mappe Esistenti (FM-NAV-025)
* **Problema:** L'avvio di RTAB-Map con `Mem/IncrementalMemory: "true"` (modalità mappatura attiva) su un database pre-mappato (`salotto.db`, 313 MB) fa sì che il nodo cerchi di espandere continuamente il grafo anche a robot fermo (frequenza 1.5 Hz), accumulando oltre 3 GB di dati sensoriali non compressi in pochi minuti. In presenza di loop closure rigettati con punteggio non positivo o connessioni tra nodi con pesi incongruenti, RTAB-Map genera l'eccezione fatale C++:
  ```text
  [FATAL] (Memory.cpp:3473::addLink()) Condition (fromS->getWeight() >= 0 && toS->getWeight() >= 0) not met!
  terminate called after throwing an instance of 'UException'
  [ros2run]: Aborted
  ```
* **Conseguenze a Catena sul Sistema:**
  1. **Crollo dell'Albero TF:** Con la terminazione di `rtabmap`, svanisce istantaneamente la trasformata `map -> odom`.
  2. **Freeze di Nav2:** Il lifecycle manager e i server di pianificazione globale/locale bloccano la navigazione in attesa dell'autorità globale di posa.
  3. **Blackout Foxglove Studio:** Venendo a mancare il frame `map` e il topic `/map`, Foxglove disconnette i canali 3D e visualizza schermo nero ("tutto spento").
* **Soluzione & Regola Permanente di Architettura:**
  1. **Modalità Localizzazione Pura su Mappe Esistenti:** In `robopy_controller/config/rtabmap.yaml`, impostare tassativamente:
     ```yaml
     Mem/IncrementalMemory: "false"
     ```
     In modalità localizzazione pura, RTAB-Map non inserisce nuovi nodi nel database, azzera le scritture su disco, abbatte il consumo RAM (<250 MB) ed elimina qualsiasi rischio di eccezione `addLink()`, garantendo la pubblicazione continua e stabile del TF `map -> odom`.
  2. **Workflow per Nuove Mappature:** Se si desidera cartografare un nuovo ambiente, iniziare sempre con un database vergine/vuoto (`/mnt/ssd/rtabmap.db`) e non eseguire mai append incrementale su mappe legacy complesse.

### Integrazione Hardware Slamtec RPLIDAR C1 & Coesistenza Multi-Sensore (FM-NAV-005)
* **Contesto:** Per superare i limiti di campo visivo ristretto della sola camera OAK-D Lite (75° HFOV, cecità a 360° durante manovre e superfici vetrate/specchiate), è stato integrato un sensore LiDAR planare a tempo di volo **Slamtec RPLIDAR C1** collegato via USB (`/dev/rplidar` a 460800 baud).
* **Regola Udev Persistente:** Poiché la scheda motori Waveshare e l'adattatore RPLIDAR C1 impiegano entrambi ponti CP2102N (`10c4:ea60`), è stata creata la regola `/etc/udev/rules.d/99-marcus-serial.rules` basata sui numeri di serie univoci:
  - `/dev/rplidar` $\rightarrow$ S/N `1af3e590ed31f11197da945f30d20014` (RPLIDAR C1)
  - `/dev/motor_driver` $\rightarrow$ S/N `4c7fd634626cef11acaca4adc169b110` (Scheda Motori ESP32)
* **Architettura a Doppia Scala (Local Fuser + Global ICP):**
  - **Scala Locale (`odom -> base_link` @ 30 Hz):** Gestita da `localization_fuser_node.py` unendo cinematica encoder ruote, IMU BMI270 200 Hz con pitch sag compensato ($8^\circ$) e confidenza VIO. Non si usa `robot_localization` EKF per prevenire deadlock da flag IMU non conformi (`FM-NAV-004`).
  - **Scala Globale & SLAM (`map -> odom` @ 1.5 Hz):** In `rtabmap.yaml`, abilitato `subscribe_scan: true`, `Reg/Strategy: "2"` (Visual + ICP) e `Grid/Sensor: "2"` (Both). Il LiDAR C1 fornisce scan matching metrico 2D continuo per sopprimere ogni deriva di orientamento, mentre la camera garantisce loop closure semantica e DBoW3.
* **Coesistenza con OAK-D Lite (Salvaguardia Ostacoli Negativi - FM-NAV-009):**
  - Il LiDAR 2D planare spara orizzontalmente nel vuoto e non può rilevare gradini o scale in discesa.
  - La camera OAK-D Lite resta attiva in parallelo su `semantic_costmap_injector.py` (Depth Hole Raycasting, $\Delta Z > 15\text{ cm}$) e mascheramento dinamico YOLOv8 su NPU Hailo-10H.
* **Ottimizzazione CPU Pi 5:**
  - Dismesso il nodo software `depthimage_to_laserscan_node`, risparmiando cicli CPU critici per il middleware DDS e la navigazione.
* **Foxglove Studio Visualizzazione `/scan` & Risoluzione Alert Rosso:**
  - Se nel pannello 3D di Foxglove compare un'icona rossa di allert `! /scan`, la causa è l'impostazione di default `Color mode: Color map` con `Color field: intensity` su `auto`.
  - Poiché il driver sllidar_ros2 in modalità standard fornisce intensità uniforme, la colormap non ha range dinamico e genera un warning visivo pur visualizzando correttamente i punti geometrici a 360°.
  - **Soluzione:** Impostare in Foxglove `Color mode: Flat` (colore fisso) o `Color field: range`.
* **Workflow Ciclo di Vita Mappe SLAM (Nuova Mappatura vs Localizzazione):**
  - Per generare una nuova mappa metricamente accurata con il LiDAR a 360°, la mappa legacy (creata con sola camera 70°) deve essere archiviata (`/mnt/ssd/rtabmap_pre_lidar_backup.db`).
  - Si avvia RTAB-Map con `Mem/IncrementalMemory: "true"` su un database vergine (`/mnt/ssd/rtabmap.db`).
  - Una volta completato il giro dell'ambiente con chiusura dell'anello (loop closure), si imposta nuovamente `Mem/IncrementalMemory: "false"` per blindare la mappa contro corruzioni o crash da allocazione continua di nodi.



