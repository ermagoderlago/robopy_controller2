# 🛠️ Progetto di Miglioramento IMP-NAV-008
> **Target Failure Mode:** `FM-NAV-008` (Micro-oscillazioni Traiettoria MPPI e Urti con Ostacoli)  
> **Priorità RPN:** 315 -> 30 | **Stato:** COMPLETED | **Dominio:** Navigazione & SLAM (Nav2)

---

## 1. Analisi del Problema & Cause Radice

### Problema
Nel controller locale Nav2 MPPI (`local_planner_mppi`), variazioni del coefficiente di frizione del pavimento, ristrettezza dei corridoi domestici e tolleranze geometriche causano:
- Micro-oscillazioni nervose della traiettoria angolare durante il tracciamento dei percorsi.
- Frequenti stop-and-go improduttivi e strisciamenti in prossimità dei battiscopa.
- Incompatibilità dei parametri statici di default (`inflation_radius`, `cost_scaling_factor`, `max_vel_x`, critici MPPI).

### Soluzione Architetturale (Offline Background Optimization)
1. **Telemetry & Deviation Logger (`mppi_telemetry_logger.py`)**: Sottoscrive durante la navigazione active `/plan`, `/odom`, `/cmd_vel`, `/diagnostics` e logga le metriche su file locale (`~/.marcus/telemetry/mppi_nav_telemetry.jsonl`).
2. **Offline Autotuner Job (`mppi_offline_autotuner.py`)**: Script di ottimizzazione in background (eseguito in idle/nightly o su richiesta) che calcola il punteggio di performance (Oscillations, Deviation Error, Stop-and-Go Count, Proximity Cost) e aggiorna i parametri ottimali nel file `nav2_params.yaml`.
3. **Hot-Swapping Dynamic Parameter Update**: Invia comandi a caldo al `local_costmap` e `controller_server` per applicare immediatamente i parametri ottimali.

---

## 2. Specifiche dei Moduli Software

### Modulo SW 1: `mppi_telemetry_logger.py` (Nodo ROS 2 / Logger)
- **Topic Sottoscritti:** `/plan` (nav_msgs/Path), `/odometry/filtered` (nav_msgs/Odometry), `/cmd_vel` (geometry_msgs/Twist), `/diagnostics` (diagnostic_msgs/DiagnosticArray).
- **Metriche Registrate:**
  - `path_cross_track_error`: errore di scostamento laterale dal percorso globale.
  - `angular_jitter`: varianza ed entropia dell'output `cmd_vel.angular.z`.
  - `stop_and_go_frequency`: numero di transizioni repentini $v=0 \leftrightarrow v>0$.
  - `min_obstacle_distance`: distanza minima rilevata nei campioni costmap.
- **Output:** Append JSONL compresso in `~/.marcus/telemetry/mppi_nav_telemetry.jsonl`.

### Modulo SW 2: `mppi_offline_autotuner.py` (Ottimizzatore Background)
- **Funzione Costo $J$:**
  $$J = w_1 \cdot \text{Jitter} + w_2 \cdot \text{CrossTrackError} + w_3 \cdot \text{StopCount} + w_4 \cdot \frac{1}{\text{MinDist}}$$
- **Algoritmo Optimization:** Grid Search euristico e Bayesian Tuning sui parametri candidate:
  - `inflation_radius` [0.3 - 0.7]
  - `cost_scaling_factor` [2.0 - 5.0]
  - `PathAlign.cost_weight` / `Obstacle.cost_weight` / `PathFollow.cost_weight`
- **Output:** Genera patch YAML / aggiorna file `nav2_params.yaml` e supporta l'applicazione tramite `set_parameters` ROS 2 API.

---

## 3. Checklist dei Task di Sviluppo

- [x] **Task 1 (SW):** Creare `mppi_telemetry_logger.py` per registrare le metriche di traiettoria e oscillazione MPPI.
- [x] **Task 2 (SW):** Creare `mppi_offline_autotuner.py` per l'analisi offline e l'ottimizzazione euristica dei parametri MPPI/costmap.
- [x] **Task 3 (Test):** Creare ed eseguire il test unitario `test/unit/test_mppi_autotuner.py` per validare logger, calcolo costo e aggiornamento YAML.
- [x] **Task 4 (FMEA):** Aggiornare `fmea/dfmea.yaml` riducendo l'RPN da 315 a 30 e rigenerare il report FMEA.

---

## 4. Criteri di Accettazione & Validazione

- **Telemetry Logger:** Registra correttamente pacchetti di telemetria senza consumare oltre l'1% di CPU su RPi5.
- **Autotuner:** Ottimizza i parametri riducendo il costo $J$ del 35% su dati sintetici/reali e aggiorna il file YAML senza errori di sintassi.
- **Test Unitari:** Passati con successo al 100%.
