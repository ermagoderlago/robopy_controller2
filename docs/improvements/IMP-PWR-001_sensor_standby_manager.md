# IMP-PWR-001: Smart Standby & Sensor Power-Save Manager (LiDAR & SLAM Freeze su Inattività)

## 1. Failure Mode Reference
- **ID:** `FM-PWR-001`
- **Subsystem:** Hardware / Power & Sensors (`sensor_standby_manager / RPLIDAR C1 / RTAB-Map`)
- **Failure:** Usura continua a vuoto del rotore ottico RPLIDAR C1, consumo elettrico parassita della batteria LiPo e sovraccarico computazionale di RTAB-Map quando il robot è fermo per > 2 minuti.
- **Cause:** Assenza di un supervisore di risparmio energetico perimetrale per i sensori attivi a 360° e mantenimento continuo dell'aggiornamento mappa SLAM con motori fermi.
- **Initial RPN:** 112 (Severity: 7, Occurrence: 8, Detection: 2)

---

## 2. Solution Architecture & Implementation

### 1. Inactivity & Stillness Detection (120s Timeout)
- Il nodo `sensor_standby_manager.py` monitora:
  - Odometria ruote (`/odom_wheel`): $|v_{lin}| \le 0.005\text{ m/s}$ e $|w_{ang}| \le 0.01\text{ rad/s}$.
  - Comandi di velocità (`/cmd_vel`): $|v| \le 0.005\text{ m/s}$ e $|w| \le 0.005\text{ rad/s}$.
  - IMU 6-DoF (`/oak/imu/data`): Norma di accelerazione limitata alla gravità ($\|\vec{a}\| \approx 9.81\text{ m/s}^2 \pm 0.35\text{ m/s}^2$) e velocità angolare $\|\vec{\omega}\| \le 0.15\text{ rad/s}$.
- Se Marcus rimane in stato di quiete per oltre **120 secondi (2 minuti)** continui, il supervisore attiva la transizione nello stato `STANDBY`.

### 2. Standby Power-Save Actions
1. Invocazione asincrona del servizio `/stop_motor` su `sllidar_node`: spegnimento immediato del rotore ottico ToF.
2. Invocazione asincrona del servizio `/rtabmap/pause` su `rtabmap`: congelamento del loop SLAM, arresto delle scritture su SQLite (`/mnt/ssd/rtabmap.db`) e abbattimento del consumo CPU.
3. Chiusura del gate hardware di movimento: pubblicazione di `False` su `/robot/motion_gate`.

### 3. Reactive Wake-up & External Force Detection
- Risveglio istantaneo su due trigger autonomi:
  - **Perturbazione esterna (Urto / Spinta / Sollevamento):** L'IMU BMI270 dell'OAK-D Lite rileva una variazione accelerometrica $|\Delta a| = |\|\vec{a}\| - g| > 0.35\text{ m/s}^2$ o una velocità di rotazione $\|\vec{\omega}\| > 0.15\text{ rad/s}$.
  - **Comando di movimento:** Arrivo di un Twist non nullo su `/cmd_vel`.
  - **Servizio manuale:** Invocazione del servizio `/robot/wake_sensors`.

### 4. Hardware Motion Gating ("Aspetta che la mappa sia su, poi si muove")
- All'avvio del risveglio (`WAKING_UP`), vengono invocati `/start_motor` e `/rtabmap/resume`.
- Il rotore del LiDAR C1 richiede circa **0.8–1.2 secondi** per raggiungere il regime operativo di 10 Hz.
- Durante questa finestra transitoria, `waveshare_motor_driver.py` rispetta il vincolo `motion_gate == False`:
  - Memorizza in cache il comando `cmd_vel` ricevuto.
  - Invia velocità zero ($0.0\text{ m/s}, 0.0\text{ rad/s}$) all'H-Bridge ESP32.
- Non appena `sensor_standby_manager` riceve scansioni valide su `/scan`, pubblica `/robot/motion_gate = True`: il gate si apre e il robot eroga fluidamente il moto fisico alle ruote con la percezione 360° e lo SLAM già operativi.

---

## 3. Residual Risk & RPN Scoring
- **Severity:** 7 (Preservata a 7 per rischio di degrado hardware a lungo termine).
- **Occurrence:** 1 (Abbattuta da 8 a 1: arresto automatico deterministico ad ogni sosta > 2 minuti).
- **Detection:** 1 (Migliorata da 2 a 1: supervisione costante IMU 10Hz e telemetria su `/robot/sensor_power_state`).
- **Residual RPN:** $7 \times 1 \times 1 = 7$ (Ridotto da 112 a 7, Rischio Mitigato e Chiuso).
