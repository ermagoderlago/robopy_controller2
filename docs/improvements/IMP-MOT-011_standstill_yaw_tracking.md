# IMP-MOT-011: Tracciamento Giroscopico Yaw a Veicolo Fermo, Rimozione Standstill Zero-Lock Rotazionale e Allineamento Polare REP-103

## 1. Identificazione e Riferimenti
- **ID Progetto:** `IMP-MOT-011`
- **Failure Mode Principale:** `FM-MOT-011` (Blocco dell'orientamento odometrico su TF a veicolo fermo con rotazione solidale dello scan laser in mappa)
- **Failure Mode Correlati:** `FM-MOT-006` (Jitter encoder Hall a riposo), `FM-MOT-010`, `FM-NAV-030`, `FM-NAV-039`
- **Ambito:** Actuation & Motion / TF Odometry / RPLIDAR C1 AMCL Alignment
- **Stato:** `COMPLETED`
- **Data:** 2026-10-06

---

## 2. Descrizione del Problema
1. Ruotando il robot sul posto (a mano o con manovre di prova a motori spenti), i punti dello scan LiDAR RPLIDAR C1 delle pareti ruotavano solidalmente con il robot su RViz/Foxglove invece di restare stabili sulle pareti fisse della mappa.
2. La trasformazione TF `odom -> base_link` rimaneva congelata al vecchio angolo yaw $\theta$, disallineando i ritorni laser rispetto alla mappa statica (`/mnt/ssd/maps/piano_terra_opt.yaml`).
3. Di conseguenza, l'algoritmo AMCL disperdeva le particelle probabilistiche e perdeva completamente la localizzazione della posa del robot nell'ambiente.

---

## 3. Diagnosi & Causa Radice
1. **Standstill Zero-Velocity Lock Rotazionale Eccessivo:**
   - In `robopy_controller/nodes/waveshare_motor_driver.py` (callback `oak_imu_callback`):
     ```python
     if getattr(self, 'motors_stopped', True) and not getattr(self, 'use_imu_for_rotation', False):
         self.oak_yaw_rate = 0.0
         return
     ```
     Con `use_imu_for_rotation:=False` (adottato per isolare le vibrazioni dell'asta telecamera), non appena `/cmd_vel` era fermo (`motors_stopped == True`), la velocità angolare giroscopica veniva forzata a $0.0\text{ rad/s}$.
   - Nel calcolo di odometria in `process_encoder_feedback`:
     `if self.enable_chassis_yaw_fusion and not self.motors_stopped:`
     la fusione giroscopica veniva bypassata a motori fermi, ponendo $\Delta \theta = 0.0$.
   - Di conseguenza, qualsiasi rotazione reale a comandi nulli non produceva alcun incremento angolare nell'odometria.
2. **Polarità Angolare Gyro OAK-D Lite:**
   - La lettura gyro da OAK-D Lite via `fast_flow_vo_node.cpp` per rotazioni fisiche orarie (destra) produce un valore positivo; la convenzione ROS REP-103 richiede invece una velocità angolare negativa ($\omega < 0$) per virate a destra.
   - Il parametro `invert_imu_yaw:=True` converte tale valore nel segno corretto conforme a REP-103.

---

## 4. Azioni Correttive Applicate
1. **`robopy_controller/nodes/waveshare_motor_driver.py`:**
   - Rimosso il bypass da `oak_imu_callback`: il rate giroscopico `oak_yaw_rate` viene continuamente campionato e validato tramite la deadband a $0.015\text{ rad/s}$ ($0.8^\circ/\text{s}$). A veicolo fermo non vi è alcun drift, ma ogni rotazione fisica reale viene catturata.
   - Differenziata la fusione in `process_encoder_feedback`: quando `motors_stopped == True`, gli encoder restano bloccati a $0$ per eliminare il jitter dei sensori Hall (FM-MOT-006), mentre l'orientamento $\theta$ integra al $100\%$ il giroscopio (`delta_theta = oak_yaw_rate * dt`, modalità `OAK_MANUAL`).
   - Impostato `invert_imu_yaw = True` di default (allineato a ROS REP-103).
2. **Script di Lancio:**
   - Configurati `restart_hailo.sh` e `scripts/start_driver.sh` con `-p invert_imu_yaw:=True -p left_motor_trim:=1.0`.
3. **Suite di Test & Diagnostica:**
   - Aggiunto test unitario `test_14_manual_rotation_during_standstill_lock` in `test/unit/test_yaw_fusion_and_scurve.py`.
   - Aggiornato `test_13_oak_imu_callback_polarity` per verificare la corretta inversione polare.
   - Sviluppato ed eseguito lo script diagnostico `scripts/check_tf_laser.py` su Marcus: verificata la corretta relazione angolare nella catena `map -> odom -> base_link -> laser` con AMCL locked ($\sigma < 0.04\text{ m}$).
