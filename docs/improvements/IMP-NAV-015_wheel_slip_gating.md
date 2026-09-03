# 🛠️ Progetto di Miglioramento IMP-NAV-015
> **Target Failure Mode:** `FM-NAV-015` (Avvelenamento Scala Odometria da Wheel Slip durante Transizioni di Superficie)  
> **Priorità RPN Iniziale:** 448 -> **RPN Residuo:** 32 | **Stato:** COMPLETED | **Dominio:** Navigazione & Odometria (VIO)

---

## 1. Analisi del Problema & Cause Radice

### Problema
Durante il passaggio tra superfici a diverso coefficiente d'attrito (es. piastrelle $\rightarrow$ parquet $\rightarrow$ tappeto spesso), le ruote motrici subiscono micro-slittamenti asimmetrici (*wheel slip*). Se contemporaneamente la Visual Odometry (VIO) opera in regime di tracking debole (basso numero di inlier per rotazione veloce o parete spoglia), l'algoritmo di calibrazione continua divide lo spostamento visivo incerto per i tick degli encoder, **avvelenando permanentemente il fattore di scala** (`wheel_scale_`).

### Soluzione Implementata (Covariance & Slip Gated Calibration)
1. **Rilevamento Dinamico dello Slittamento:** Calcolo continuo dell'accelerazione tangenziale delle ruote $a_{wheel} = \Delta v / \Delta t$ e confronto con l'accelerazione longitudinale letta dall'IMU $a_{imu} = -a_{z,cam}$. Se $|\Delta a| > 0.25\text{ m/s}^2$, scatta il flag `wheel_slip_detected_ = True`.
2. **Gating Rigido dell'Auto-Calibrazione:** Aggiornamento della scala (`wheel_scale_`) e del disallineamento angolare (`wheel_yaw_offset_`) autorizzato esclusivamente se:
   - $N_{inliers} \ge 30$ (VIO ad alta confidenza).
   - Nessuno slittamento in corso (`!wheel_slip_detected_`).
   - Spostamento fisico significativo ($\Delta s_{wheel} > 3\text{ cm}$ e $\Delta s_{vio} > 2\text{ cm}$).
   - Rapporto istantaneo plausibile $\Delta s_{vio} / \Delta s_{wheel} \in [0.75, 1.25]$.
3. **Clamping & Bounded Fallback:** Limite rigido $0.85 \le \text{wheel\_scale} \le 1.15$ e $-0.15 \le \text{wheel\_yaw\_offset} \le 0.15$ rad. In caso di perdita totale della VIO, l'odometria ruote di fallback applica la scala protetta.

---

## 2. File Modificati
- `src/fast_flow_vo_node.hpp`
- `src/fast_flow_vo_node.cpp`
- `test/unit/test_dfmea_nav_mitigations.py`
