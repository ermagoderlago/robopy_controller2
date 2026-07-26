# Engineering Change Order (ECO) - EKF Sensor Fusion & SLAM Loop Closure Optimization

**ECO ID:** ECO-00021  
**Data:** 2026-07-26  
**Modulo:** `robopy_controller` / `ekf_filter_node` / `rtabmap_slam` / `waveshare_motor_driver`  
**Autore:** Antigravity / Marcus AI  

---

## 🎯 Scopo delle Modifiche

Risolvere il drift angolare dell'odometria meccanica durante le rotazioni sul posto e la navigazione Nav2, eliminando gli scatti della mappa e garantendo la chiusura di loop visiva a $360^\circ$.

---

## 📐 Modifiche Effettuate

### 1. Geometria Fisica dell'Autotelaio
- Parametri `wheel_separation` e `rotational_wheel_separation` impostati stabilmente a **`0.285` m (285 mm)** in `waveshare_motor_driver.py`, `restart_hailo.sh` e `restart.sh`.

### 2. Integrazione EKF Sensor Fusion (`robot_localization`)
- **Correzione Header YAML:** Modificata la chiave radice in `ekf.yaml` da `ekf_localization:` a **`ekf_filter_node:`** per consentire al parser ROS 2 di caricare correttamente i parametri nel nodo EKF.
- **Integrazione IMU OAK-D Lite (BNO085):** Reindirizzato l'ingresso IMU su **`/oak/imu/data`** (sensore BNO085 a 42 Hz stabili), in sostituzione della telemetria ESP32 priva di pacchetti giroscopici.
- **Configurazione Canali (`odom0_config`):** Abilitati sia la traslazione lineare $v_x$ che la velocità angolare $w_z$ (`vyaw: true`) dall'odometria ruote, fondendoli con la velocità angolare giroscopica $w_z$ della OAK IMU.
- **Frequenza Output:** Odometria fusa pubblicata su `/odometry/filtered` a **30.0 Hz** ed autorità sul TF `odom → base_link`.

### 3. Tuning RTAB-Map SLAM
- `Rtabmap/DetectionRate`: Aumentata da 1.0 Hz a **`2.0` Hz** per raddoppiare la densità visiva dei keyframe in memoria.
- `RGBD/OptimizeMaxError`: Aumentata da 1.0 a **`3.0`** per consentire ad RTAB-Map di accettare chiusure di loop visive anche con derive angolari accumulate fino a $10^\circ - 15^\circ$, saldando perfettamente la mappa a $360^\circ$.

---

## 🔬 Impatto e Validazione

- **Accuratezza Angolare:** Rotazione su 36 step ($360^\circ$) completata con uno scarto inferiore al $6\%$ ($381.3^\circ$ accumulati).
- **Chiusura del Loop:** Verificata l'accettazione visiva delle chiusure di loop con l'unione dell'anello di mappa senza spazi aperti o sdoppiamenti dei muri.
- **Stabilità RPi5:** Utilizzo RAM stazionario sotto i 400 MB per RTAB-Map, compatibile con i 4GB di bordo.
