# IMP-MOT-009: Profiler S-Curve Jerk Limiter Continuo di 2° Ordine (C^1) per Soppressione Beccheggio

## 1. Failure Mode Reference
- **ID:** `FM-MOT-009`
- **Subsystem:** Actuation & Motion (`waveshare_motor_driver.py`)
- **Failure:** Scatti di accelerazione a gradino (jerk infinito) e beccheggio dell'albero sensori (OAK-D Lite, LiDAR C1), con perturbazione della costmap e della visual odometry.
- **Cause:** Profilo di velocità del primo ordine con accelerazione costante a gradino; assenza di limitazione continua del jerk.
- **Initial RPN:** 147 (Severity: 7, Occurrence: 7, Detection: 3)

---

## 2. Solution Architecture & Implementation

### 1. Root Cause Analysis
1. **Discontinuità dell'Accelerazione ($C^0$):** Un limitatore di slew rate del primo ordine applica una rampa lineare di velocità ($v(t) = v_0 + a \cdot t$), che implica che la derivata prima della velocità (l'accelerazione $a(t)$) compie salti istantanei a gradino al cambio di target.
2. **Jerk Infinito e Oscillazione Elastica:** Poiché il jerk è la derivata dell'accelerazione ($j = da/dt$), un salto a gradino di accelerazione corrisponde a un picco teoricamente infinito di jerk ($j \to \infty$). Sul robot fisico Marcus, questo impulso improvviso di coppia meccanica sollecita elasticamente il montante verticale (albero sensori), provocando un beccheggio transitorio di diversi gradi. L'inclinazione repentina del piano di scansione del LiDAR C1 e della camera OAK-D Lite genera falsi ostacoli sul pavimento nella costmap locale e degrada il tracking delle feature visive.

### 2. S-Curve Jerk Limiter di 2° Ordine ($C^1$ Continuity)
Implementato in `send_speeds` all'interno di `waveshare_motor_driver.py`:
- **Modello di Dinamica Continua:**
  $$\text{err} = \text{target\_duty} - \text{current\_duty}$$
  $$a_{des} = \text{clamp}\left(\frac{\text{err}}{\tau_{accel}}, -a_{max}, +a_{max}\right), \quad \tau_{accel} = 0.15\text{ s}, \quad a_{max} = 5.0\text{ duty/s}$$
  $$\Delta a = \text{clamp}(a_{des} - a_{curr}, -j_{max} \cdot \Delta t, +j_{max} \cdot \Delta t), \quad j_{max} = 25.0\text{ duty/s}^2$$
  $$a_{curr} \leftarrow a_{curr} + \Delta a, \quad \text{duty}_{curr} \leftarrow \text{duty}_{curr} + a_{curr} \cdot \Delta t$$
- **Clamp Failsafe di Arresto Immediato:** Quando viene ricevuto un comando di stop ($v=0, \omega=0$), il ciclo azzera immediatamente $duty$ ed accelerazione per garantire il rispetto del vincolo di sicurezza di arresto hardware entro i 500 ms di watchdog.
- **Parametri Dinamici:** `max_duty_accel` e `max_duty_jerk` sono configurabili dinamicamente tramite `parameter_callback`.

---

## 3. Residual Risk & RPN Scoring
- **Severity:** 7 (Preservata stabilità del robot e integrità dell'albero sensori).
- **Occurrence:** 1 (Abbattuta da 7 a 1: profilo continuo $C^1$ a jerk finito per ogni transizione di marcia).
- **Detection:** 1 (Migliorata da 3 a 1: test unitario dedicato `test_yaw_fusion_and_scurve.py`, verifica numerica jerk e telemetria).
- **Residual RPN:** $7 \times 1 \times 1 = 7$ (Ridotto da 147 a 7, Rischio Mitigato e Chiuso).
