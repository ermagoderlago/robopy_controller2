# 🗺️ SPEC-02: Navigazione, SLAM, Costmap 2.5D & Evitamento Ostacoli

## 1. Identificazione e Scopo
- **ID Specifica:** `SPEC-02`
- **Ambito:** Mappatura simultanea e localizzazione (SLAM), fusione odometrica, navigazione autonoma con Nav2 e modello visivo NOMAD, proiezione semantica 2.5D e sicurezza contro dislivelli.
- **Nodi & Moduli ROS 2:**
  - `sllidar_ros2.sllidar_node` (Driver hardware Slamtec RPLIDAR C1 ToF su `/dev/rplidar`)
  - `robopy_controller.nodes.semantic_costmap_injector` (`semantic_costmap_injector.py`)
  - `robopy_controller.nodes.extrinsic_camera_calibrator` (`extrinsic_camera_calibrator.py`)
  - `robopy_controller.nodes.nomad_navigator_node` (`nomad_navigator_node.py`)
  - `robopy_controller.nodes.fast_flow_vo_node` (`fast_flow_vo_node.cpp`)
  - `rtabmap_slam`, `nav2_bt_navigator`, `nav2_controller`
- **File di Configurazione Chiave:**
  - `config/nav2_params.yaml`, `config/nav2_survival_bt.xml`, `config/rtabmap.yaml`
- **DFMEA Correlati:** `FM-NAV-001` (Overhead STVL 3D), `FM-NAV-005` (Eliminazione blind-spot 360° con LiDAR), `FM-NAV-009` (Ostacoli negativi e caduta scale), `FM-NAV-010` (NOMAD con LiDAR), `FM-NAV-016` (Aliasing percettivo RTAB-Map), `FM-NAV-017` (Deriva termica BMI270 ZUPT), `FM-NAV-019` (Recovery BT cieca), `FM-VIS-003` (Camera pitch sag).

---

## 2. Architettura di Mappatura e Navigazione

```mermaid
graph TD
    LIDAR["RPLIDAR C1 (360° ToF 10Hz)"] -->|/scan| RTAB["RTAB-Map (DBoW3 Visual + ICP Laser 1.5Hz)"]
    LIDAR -->|/scan Planar Costmap| COSTMAP["Nav2 Costmap (Global & Local)"]
    RGBD["OAK-D Lite (Depth 16UC1 + RGB)"] --> VIO["fast_flow_vo_cpp (VIO 20Hz)"]
    RGBD --> SCI["semantic_costmap_injector.py"]
    RGBD --> CALIB["extrinsic_camera_calibrator.py"]
    RGBD --> RTAB
    
    SCI -->|Hole Raycasting & 2.5D Obstacles| COSTMAP
    CALIB -->|TF Dynamic Pitch Sag| TF["Transform Tree (tf2)"]
    VIO -->|/vo/odom (ZUPT Dynamic Drift Nulling)| EKF["Odometria & EKF"]
    
    RTAB -->|/map Occupancy Grid & ICP Localization| NAV2["Nav2 Stack (Survival BT)"]
    RGB -->|Visual Waypoints| NOMAD["NOMAD Visual Navigator"]
    NOMAD -->|Pure Pursuit| CMD["/cmd_vel"]
    NAV2 --> CMD
```

---

## 3. 🔴 ZONA ROSSA (Inviolabili - NO AUTONOMOUS TOUCH)

Le seguenti prescrizioni sono categoriche. La loro violazione comporta il crash immediato del Pi 5 per esaurimento risorse o la caduta fisica del robot da scale e dislivelli.

| Vincolo Architetturale / Parametro | Valore Inviolabile | Rischio Ingegneristico | DFMEA |
| :--- | :--- | :--- | :--- |
| **Divieto Assoluto STVL** | **STVL 3D Proibito** (usare solo griglia 2.5D) | Saturazione RAM host (OOM Kill) e 100% CPU lock | FM-NAV-001 |
| **Rilevamento Ostacoli Negativi** | Depth Hole Raycasting attivo ($\Delta Z > \mathbf{15\text{ cm}}$) | **Caduta dalle scale** o precipizi con distruzione hardware | FM-NAV-009 |
| **Policy Costmap Combination** | `combination_method: 1` (Maximum) obbligatorio | Cancellazione spuria di ostacoli reali da voxel vuoti | FM-NAV-019 |
| **No Blind Recovery Rotations** | Vietate rotazioni sul posto cieche a 90°/180° | Collisione della coda e dello chassis contro ostacoli ciechi | FM-NAV-019 |
| **Gerarchia Albero TF (REP-105)** | `odom ➔ base_link ➔ camera_link ➔ camera_optical_frame` | Cicli TF (`base_link ➔ base_link`) e disallineamento odometrico | FM-NAV-003 |
| **Encoding Immagine di Profondità** | Forzare `"16UC1"` in C++ (non usare `"mono16"`) | Rifiuto dello stream da `depthimage_to_laserscan` | FM-VIS-002 |
| **Percorso File Behavior Tree** | `nav2_survival_bt.xml` nel path installato | Fallimento attivazione `bt_navigator` all'avvio | FM-NAV-004 |

---

## 4. 🟢 ZONA VERDE (Auto-Evolution - MIGLIORAMENTO AUTONOMO CONSENTITO)

L'agente Antigravity può ottimizzare e ricalibrare autonomamente le seguenti componenti:

| Area di Ottimizzazione | Metodo & Logica Ammessa | Range & Vincoli di Accettazione |
| :--- | :--- | :--- |
| **Decadimento Costmap 2.5D** | Regolazione vita utile degli ostacoli semantici dinamici | $T_{decay} \in [2.0\text{ s}, 8.0\text{ s}]$; pulizia automatica ghost |
| **Tuning Pure Pursuit NOMAD** | Lookahead distance e guadagno angolare su waypoint | $L_{lookahead} \in [0.25\text{ m}, 0.60\text{ m}]$, $K_p \in [1.0, 2.5]$ |
| **Anti-Aliasing RTAB-Map** | Soglia similarità DBoW3 e maschera ROI pavimento | `Rtabmap/LoopThr` $\in [0.18, 0.30]$; ROI pavimento $8\%\text{-}15\%$ |
| **ZUPT IMU Dynamic Drift** | Soglia di velocità per auto-azzeramento bias giroscopio Z | Velocità $v \le 0.005\text{ m/s}$ e $\omega \le 0.008\text{ rad/s}$ per $>0.5\text{ s}$ |
| **Pitch Sag Auto-Heal** | Regressione RANSAC del piano terra per correggere offset TF | Correzione pitch ammessa: $\Delta \theta \in [-5.0^\circ, +5.0^\circ]$ |
| **Frequenza Aggiornamento /map** | Rate pubblicazione mappa RTAB-Map da Depth | Frequenza: $1.0\text{ Hz} \le f \le 2.0\text{ Hz}$ per proteggere la CPU |

---

## 5. 🟡 ZONA GIALLA (Human-in-the-Loop - APPROVAZIONE UMANA OBBLIGATORIA)

Le seguenti modifiche richiedono proposta formale e validazione dell'operatore umano:

1. **Integrazione Sensori Fisici Esterni:** Aggiunta di un sensore LiDAR 2D a 360° o modifica dell'URDF base per includere nuovi payload.
2. **Risoluzione Mappa Nav2:** Modifica della risoluzione geometrica globale della griglia (attualmente impostata a $0.05\text{ m/pixel}$).
3. **Footprint Meccanico:** Variazione del raggio di ingombro del robot (`robot_radius: 0.18m`) o dei poligoni di inflazione nelle costmap.
4. **Sostituzione del Controller Nav2:** Rimpiazzo del controller DWB o MPPI con nuovi algoritmi di traiettoria non pre-validati.
5. **Database RTAB-Map:** Cancellazione o reset del database cartografico multi-sessione persistente (`~/.ros/rtabmap.db`).

---

## 6. Procedura di Verifica & Test di Non-Regressione

Prima di confermare modifiche al sottosistema di navigazione, l'agente DEVE eseguire con successo:

```bash
# 1. Test di non-regressione per il rilevamento ostacoli negativi (FM-NAV-009)
pytest tests/test_negative_obstacles.py -v

# 2. Test del generatore e decadimento costmap 2.5D
pytest tests/test_semantic_costmap_injector.py -v

# 3. Test della calibrazione continua pitch sag OAK-D Lite
pytest tests/test_extrinsic_camera_calibrator.py -v

# 4. Verifica assenza cicli TF nell'albero delle trasformazioni
bash tf_verify.sh
```
I test devono confermare:
- Intervento istantaneo dell'ostacolo letale su dislivelli superiori a 15 cm.
- Nessuna allocazione di memoria incontrollata nel loop 2.5D durante 1000 iterazioni mock.
- Albero TF strettamente conforme a REP-105 senza cicli o frame orfani.
