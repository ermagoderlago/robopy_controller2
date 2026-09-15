# IMP-NAV-029: Fusione Complementare Yaw Odometria Ruote e Giroscopio Chassis ESP32 con Auto-Bias Tracking

## 1. Failure Mode Reference
- **ID:** `FM-NAV-029`
- **Subsystem:** Nav2 / Localization (`waveshare_motor_driver.py`)
- **Failure:** Deriva odometrica angolare e accumulo quadratico di errore posa (x, y) indotto da micro-slittamenti differenziali delle ruote su curvature o pavimenti lisci.
- **Cause:** Odometria differenziale puramente cinematica da encoder ruote non compensata da sensori inerziali dello chassis.
- **Initial RPN:** 196 (Severity: 7, Occurrence: 7, Detection: 4)

---

## 2. Solution Architecture & Implementation

### 1. Root Cause Analysis
1. **Limiti del Rotolamento Puro:** L'odometria differenziale calcola la rotazione angolare del veicolo esclusivamente dalla differenza di percorso percorso dalle ruote: $\Delta \theta_{wheel} = (\Delta s_R - \Delta s_L) / W$.
2. **Amplificazione Quadratica dell'Errore:** Su pavimenti a bassa aderenza, piastrelle o durante rotazioni veloci, una minima asimmetria di attrito o micro-slittamento genera un errore di qualche grado su $\theta$. Tale errore angolare si traduce in un errore di posa lineare che cresce quadraticamente col tempo e con la distanza percorsa ($x(t) \sim \int \cos(\theta(t)) dt$), deformando la mappa locale e disallineando il corridoio di navigazione prima dell'intervento di SLAM/EKF.

### 2. Complementary Yaw Fusion & Auto-Bias Tracking
Implementato in `waveshare_motor_driver.py`:
- **Giroscopio Chassis Diretto:** La scheda ESP32 integra un sensore inerziale 6-DOF montato rigidamente sullo chassis (asse Z verticale mappato su `gy` in standard REP-103).
- **Fusione Complementare ad Anello Aperto:**
  $$\Delta \theta_{fused} = \alpha \cdot (\omega_{chassis} \cdot \Delta t) + (1 - \alpha) \cdot \Delta \theta_{wheel}, \quad \alpha = 0.88$$
  - Il giroscopio risponde alle variazioni istantanee ad alta frequenza senza risentire dello slittamento delle ruote.
  - L'odometria da ruote fornisce la baseline stabile a lungo termine senza accumulo di deriva infinita da drift del giroscopio.
- **Auto-Bias Tracking Stazionario:** Durante la sosta (`motors_stopped = True`), il driver esegue una stima continua del bias di zero dell'IMU chassis tramite filtro passa-basso EMA ($\alpha_{bias} = 0.05$):
  $$\text{bias}_{gyro} \leftarrow 0.95 \cdot \text{bias}_{gyro} + 0.05 \cdot \omega_{raw}$$
  Durante il moto (`motors_stopped = False`), il bias stimato viene sottratto dalla velocità angolare misurata.
- **Degrado Trasparente:** Se la telemetria IMU chassis ritarda oltre 250 ms, il driver passa automaticamente al 100% differenziale ruote senza interruzioni del servizio o errori numerici.
- **Parametri Dinamici:** `enable_chassis_yaw_fusion` e `yaw_fusion_alpha` configurabili a runtime via `parameter_callback`.

---

## 3. Residual Risk & RPN Scoring
- **Severity:** 7 (Preservata accuratezza di navigazione e localizzazione).
- **Occurrence:** 1 (Abbattuta da 7 a 1: compensazione inerziale continua dello yaw con stima automatica del bias).
- **Detection:** 1 (Migliorata da 4 a 1: test unitario dedicato `test_yaw_fusion_and_scurve.py` e diagnostica continua).
- **Residual RPN:** $7 \times 1 \times 1 = 7$ (Ridotto da 196 a 7, Rischio Mitigato e Chiuso).
