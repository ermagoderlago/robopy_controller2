# IMP-MOT-010: Risoluzione Doppia Inversione Polarità Giroscopio Z OAK-D Lite e Ripristino Trim Simmetrici

## 1. Identificazione e Riferimenti
- **ID Progetto:** `IMP-MOT-010`
- **Failure Mode Principale:** `FM-MOT-010` (Deviazione a sinistra in rettilineo e traiettorie divergenti con collisione ostacoli durante tracking Nav2)
- **Failure Mode Correlati:** `FM-MOT-008`, `FM-NAV-012`, `FM-NAV-015`, `FM-NAV-038`
- **Ambito:** Actuation & Motion / Nav2 Trajectory Tracking
- **Stato:** `COMPLETED`
- **Data:** 2026-10-02

---

## 2. Descrizione del Problema
1. Durante il moto rettilineo puro (`cmd_vel.linear.x > 0, cmd_vel.angular.z == 0`), il robot virava sistematicamente verso sinistra anziché avanzare diritto.
2. Durante l'inseguimento di percorsi generati da Nav2 (MPPI/DWB), mentre sulla mappa di RViz la traiettoria globale e locale apparivano libere e corrette, il robot fisico eseguiva manovre completamente divergenti, zigzagando fino a collidere contro ostacoli e pareti.

---

## 3. Diagnosi & Causa Radice
1. **Doppia Inversione Gyro Z OAK-D Lite:**
   - Nel nodo C++ `src/fast_flow_vo_node.cpp` (riga 434), la lettura del giroscopio OAK-D Lite veniva già convertita nella convenzione standard ROS REP-103 (+Z = rotazione antioraria / sinistra):
     ```cpp
     gz_ros = -packet.gyroscope.y;
     ```
   - Nel driver Python `robopy_controller/nodes/waveshare_motor_driver.py` (riga 43), il parametro `invert_imu_yaw` era impostato a `True` di default, eseguendo `w = -raw_w` nella callback `oak_imu_callback`.
   - Questa seconda inversione faceva sì che una rotazione fisica a sinistra producesse un valore di `oak_yaw_rate` negativo (svolta a destra).
2. **Anello di Retroazione Positiva (Positive Feedback Loop):**
   - Nello stabilizzatore di rotta a 42 Hz (`send_speeds`), un'inclinazione fisica a sinistra veniva interpretata come una svolta a destra, inducendo il correttore a sottrarre duty alla ruota sinistra e incrementarlo sulla ruota destra, accentuando attivamente la svolta a sinistra.
3. **Corruzione Odometria `/odom` e Inseguimento Nav2 MPPI:**
   - Il filtro complementare integrava `oak_yaw_rate * dt`. Con segno opposto, una virata reale a sinistra veniva registrata in `/odom` come virata a destra.
   - Il controllore MPPI di Nav2, rilevando il robot a destra della traiettoria pianificata, comandava massima sterzata a sinistra, portando il robot reale alla collisione fisica immediata.
4. **Penalizzazione Asimmetrica Trim Sinistro:**
   - `left_motor_trim` era impostato a `0.88`, tagliando del 12% la potenza erogata alla sola ruota sinistra in avanzamento.

---

## 4. Azioni Correttive Applicate
1. **`robopy_controller/nodes/waveshare_motor_driver.py`:**
   - Impostato `left_motor_trim = 1.0` di default (simmetrico 1:1 in avanti).
   - Impostato `invert_imu_yaw = False` di default (allineamento ROS REP-103).
   - Aggiunto `invert_imu_yaw` alla callback dinamica `parameter_callback`.
2. **Script di Esecuzione:**
   - Aggiornati `restart_hailo.sh` e `scripts/start_driver.sh` con `-p invert_imu_yaw:=False` e `-p left_motor_trim:=1.0`.
3. **Validazione & Test:**
   - Aggiunto `test_13_oak_imu_callback_polarity` in `test/unit/test_yaw_fusion_and_scurve.py`.
   - Eseguiti e validati con successo tutti i 41 test di cinematica, PID, anti-stiction e yaw fusion (`pytest`).
