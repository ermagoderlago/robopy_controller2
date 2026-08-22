# 🛠️ Progetto di Miglioramento IMP-NAV-010
> **Target Failure Modes:** `FM-NAV-010`, `FM-NAV-011`, `FM-VUI-022`  
> **Priorità RPN:** 432 -> 32 (`FM-NAV-010`), 336 -> 7 (`FM-NAV-011`), 336 -> 6 (`FM-VUI-022`) | **Stato:** COMPLETED | **Dominio:** Navigazione & SLAM (NOMAD / RTAB-Map / VUI)

---

## 1. Analisi del Problema & Cause Radice

### Problemi Riscontrati
1. In assenza di un LiDAR 2D 360°, la navigazione classica basata su occupancy grid e costmap dense risente delle limitazioni di campo visivo (HFOV 69° OAK-D Lite) e del sovraccarico computazionale sul Raspberry Pi 5.
2. Errori continui in Foxglove Studio causati da conflitti di autorità TF contemporanei (`fast_flow_vo_cpp` e `localization_fuser_node` entrambi su `odom -> base_link`).
3. RTAB-Map SLAM bloccato da type mismatch su parametro CLI `DetectionRate` e mancata generazione della mappa `/map`.
4. Stallo fisico del robot durante l'esplorazione: il nodo NOMAD pubblicava su topic `/cmd_vel_mux/input/nomad`, mentre l'hardware `waveshare_motor_driver` ascolta direttamente su `/cmd_vel`.
5. Mancata ricezione frame video: il publisher C++ `fast_flow_vo_cpp` usa QoS `RELIABLE` mentre il nodo NOMAD sottoscriveva con QoS `BEST_EFFORT` (incompatibilità DDS).
6. Disallineamento fonetico ASR: i motori vocali trascrivevano "NOMAD" come "nomade", "nomadi", "norman" e la skill non era registrata nel registry dell'AI Orchestrator.

### Soluzione Architetturale Implementata
1. **Risoluzione TF & SLAM 2.5D**:
   - Assegnata l'autorità TF `odom -> base_link` in modo esclusivo a `fast_flow_vo_cpp`.
   - Disabilitata la pubblicazione concorrente di TF in `localization_fuser_node` (`publish_tf:=False`).
   - Riportato il detection rate di RTAB-Map a **1.5 Hz** ed espansa la coda DDS `queue_size` a 20 in `rtabmap.yaml`.
2. **Nodo NOMAD Nativo Edge (`nomad_navigator_node.py`)**:
   - Finestra temporale scorrevole di 3 frame RGB ($I_{t-2}, I_{t-1}, I_t$) ridimensionati a 128x128.
   - Sottoscrizione `/rgb/image` con `ReliabilityPolicy.RELIABLE`.
   - Doppia modalità: *Unconditioned Exploration* (goal mascherato) per scoprire nuovi spazi ed avanzare in aree aperte; *Goal-Conditioned Navigation* per dirigersi verso un'immagine target da ChromaDB.
   - Controller locale Pure Pursuit che traduce i waypoint predetti in comandi `geometry_msgs/Twist` pubblicati direttamente su **`/cmd_vel`**.
   - Visualizzazione della traiettoria in tempo reale su Foxglove (`/nomad/trajectory`).
3. **Skill di Esplorazione NOMAD (`NomadExplorationSkill`)**:
   - Riconoscimento vocale di intenti di esplorazione con tolleranza fonetica ("Marcus, esplora con NOMAD", "Esplora con nomade", "Fai una ricognizione", "Esplora la casa").
   - Registrazione permanente in `AIOrchestrator` (`orchestrator.py`) ed esportazione nel Tool Calling di Gemini Live API.
   - Loop reattivo locale a 4 Hz con aggiornamento contemporaneo della mappa SLAM `/map`.
4. **Scalabilità Futura LiDAR**:
   - L'architettura è predisposta per accogliere un sensore LiDAR 2D 360° come strato di sicurezza geometrica (Nav2 Costmap / safety arbitrator), mantenendo NOMAD come pianificatore topologico/visivo ad alto livello.

---

## 2. Specifiche dei Moduli Software

### Modulo SW 1: `nomad_navigator_node.py`
- **Topic Sottoscritti:** `/rgb/image` (`sensor_msgs/Image`, QoS RELIABLE), `/nomad/enable` (`std_msgs/Bool`), `/nomad/set_mode` (`std_msgs/String`), `/nomad/set_goal_image` (`sensor_msgs/Image`).
- **Topic Pubblicati:** `/nomad/trajectory` (`nav_msgs/Path`), `/nomad/goal_distance` (`std_msgs/Float32`), `/nomad/status` (`std_msgs/String`), `/cmd_vel` (`geometry_msgs/Twist`).
- **Parametri Principali:** `inference_rate_hz` (4.0), `context_size` (3), `input_width` (128), `input_height` (128), `cmd_vel_topic` (`/cmd_vel`), `max_linear_speed` (0.18 m/s), `max_angular_speed` (0.45 rad/s).

### Modulo SW 2: `nomad_exploration_skill.py`
- **Keywords:** "nomad", "nomade", "esplora", "esplorazione", "ricognizione", "perlustra", "mappa".
- **Pattern:** Regex fonetiche e verbali (`nomad`, `nomade`, `esplora`, `ricognizione`, `perlustra`, `fai un giro`, `fermati`, `stop nomad`).
- **Interfaccia:** Abilita e arresta reattivamente il nodo NOMAD con feedback vocale e pubblicazione su `/nomad/enable` e `/nomad/set_mode`.

---

## 3. Checklist dei Task di Sviluppo & Validazione

- [x] **Task 1 (Fix Camera & TF):** Eliminata doppia pubblicazione TF in `localization_fuser_node` e regolato RTAB-Map a 1.5 Hz.
- [x] **Task 2 (NOMAD Node):** Creato `nomad_navigator_node.py` con buffer contestuale, policy exploration/goal e controller Pure Pursuit su `/cmd_vel`.
- [x] **Task 3 (NOMAD Skill & Orchestrator):** Creata `NomadExplorationSkill`, estese regex fonetiche e registrata in `AIOrchestrator`.
- [x] **Task 4 (Script Eseguibile & Launch):** Creato wrapper `scripts/nomad_navigator_node` e configurato avvio in `restart_hailo.sh`.
- [x] **Task 5 (Test Unitari 100% Pass):** Eseguiti con successo `test_nomad_navigator.py` (3/3), `test_nomad_skill.py` (3/3) e `test_camera_slam_pipeline.py` (4/4).
- [x] **Task 6 (Validazione Live su Robot):** Testata attivazione su robot reale con ricezione comandi `/cmd_vel` ($v_x = 0.17\,\text{m/s}$, $\omega_z = -0.45\,\text{rad/s}$) ed arresto sicuro.
- [x] **Task 7 (FMEA & Lessons Learned):** Aggiornati `fmea/dfmea.yaml` (`FM-NAV-010`, `FM-NAV-011`, `FM-VUI-022`), `nav2_slam_tuning.md`, `audio_vui_pipeline.md` e generato report esecutivo.

