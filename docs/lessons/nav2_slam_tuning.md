# Lezioni Apprese - Navigazione & SLAM Tuning

Questo documento raccoglie le lezioni apprese e le configurazioni relative a RTAB-Map, Nav2 e la gestione dei sensori geometrici di Marcus.

---

## 🗺️ RTAB-Map e Costmap Nav2

### Divieto di STVL e Proiezione 2.5D
* **Regola:** Non implementare la mappatura volumetrica continua 3D (STVL) su Raspberry Pi 5. La CPU ed il bus di memoria non sono in grado di sostenerne il calcolo.
* **Soluzione:** Proiettare gli ostacoli 3D estratti dalla visione artificiale in ostacoli costmap 2D localizzati (2.5D). Il nodo `semantic_costmap_injector.py` converte i bounding box tridimensionali in coordinate 2D e li inietta nel costmap Nav2 con un decadimento temporale associato.

### Allineamento dei Frame ID e `/scan`
* **Errore:** RTAB-Map fallisce l'aggiornamento con il messaggio `Could not convert laser scan msg! Aborting rtabmap update...`.
* **Causa:** Il frame associato ai messaggi del topic `/scan` generati da `depthimage_to_laserscan` non corrisponde all'albero statico delle trasformazioni geometriche.
* **Risoluzione:** Allineare i parametri di `depthimage_to_laserscan` impostando l'argomento `-p output_frame:=camera_link` per agganciarlo alla static TF `base_link ➔ camera_link`.

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
* Loop closure: `Vis/CorType: 0` → GFTT/BRIEF nativo C++ (DBoW2), **no SuperPoint Python**.
* `Vis/FeatureType: 6` → GFTT/BRIEF: veloce, stabile su RPi5.

