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

---

## 🚀 ECO-00022 (Luglio 2026) - Fusione Odometria Ruote/VIO Dedicata & Supervisor di Sicurezza

**ECO ID:** ECO-00022  
**Data:** 2026-07-28  
**Modulo:** `localization_fuser_node.py` / `robot_health_supervisor.py` / `marcus_bringup.launch.py`  
**Autore:** Antigravity / Marcus AI  

### 📐 Modifiche Effettuate
1. **Fusione Ruote/VIO Dedicata (`localization_fuser_node.py`):**
   - Implementato nodo fuser dedicato con monitoraggio confidenza VIO su `/vins/quality_metrics` ($C \in [0, 100]$).
   - Inflazione di covarianza $R_{VIO}$ regolata dall'equazione master $R_{VIO} = R_{base} \cdot \min(M_{max}, S(C_{smooth}) \cdot e^{\alpha \Delta t})$ con saturazione rigida $M_{max} = 100.0$.
   - Wheel slip detection via confronto $\left| \omega_{wheels} - \omega_{IMU} \right| > 0.25\,\text{rad/s}$.
   - Piano pavimento dinamico ruotato a 200 Hz tramite gli angoli istantanei di Roll ($\phi$) e Pitch ($\theta$) misurati dall'IMU OAK-D.
2. **Supervisor di Sicurezza & Arbitraggio (`robot_health_supervisor.py`):**
   - Soglie numeriche per stati GREEN / YELLOW / RED basate su confidenza VIO, temperatura CPU, occupazione RAM e tensione batteria.
   - Arbitraggio ad alta priorità (Priority 0) su `/cmd_vel_mux/input/safety_override` per blocco immediato in caso di anomalie critiche.
3. **Orchestrazione Launch Nativa (`marcus_bringup.launch.py`):**
   - Sostituiti gli script bash con launchfile ROS 2 nativo. Integrazione demone RouDi per DDS Zero-Copy e pinning CPU Core 2,3 per nodi NPU/visione.

