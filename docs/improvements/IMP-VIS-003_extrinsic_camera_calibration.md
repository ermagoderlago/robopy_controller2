# IMP-VIS-003: Continuous Extrinsic Auto-Calibration & Camera Pitch Sag Compensation

## 1. Failure Mode Reference
- **ID:** `FM-VIS-003`
- **Subsystem:** Vision / Hardware (`oak_d_mount / tf_broadcaster`)
- **Failure:** Falso rilevamento ostacoli (Muri Inesistenti) o mancata rilevazione pavimento causati da Drift Meccanico (Sag) della telecamera.
- **Cause:** Vibrazioni del telaio/motori che allentano la staffa fisica della OAK-D Lite, alterando il pitch reale rispetto all'URDF.
- **Initial RPN:** 336 (Severity: 8, Occurrence: 6, Detection: 7)

---

## 2. Solution Architecture & Awareness
1. **Continuous Ground Plane Fit (RANSAC / SVD):**
   Un nuovo nodo `extrinsic_camera_calibrator.py` campiona il flusso di profondità `sensor_msgs/Image` nella regione inferiore del campo visivo (dove il pavimento fisso è visibile a 0.5m - 2.0m davanti al robot).
   Converte i pixel 2D in punti 3D nel frame `base_link` usando gli intrinseci di `CameraInfo`.
   Applica una regressione RANSAC per identificare il piano del terreno $Ax + By + Cz + D = 0$.

2. **System Awareness & Diagnostics:**
   - La normale del pavimento stimata $\vec{n} = [n_x, n_y, n_z]$ viene confrontata con la normale ideale $[0, 0, 1]^T$ in `base_link`.
   - Se l'inclinazione di pitch $\Delta \theta_{pitch} = \arcsin(n_x)$ devia oltre $1.5^\circ$, il nodo emette una diagnostica ROS 2 su `/diagnostics` (HardwareID: `OAK-D-Lite`, Level: `WARN` o `ERROR`) ed un messaggio di stato su `/robot/health_status`.

3. **Proactive Self-Healing (A caldo):**
   - **TF Correction:** Trasmette l'offset angolare correttivo $\Delta \theta_{pitch}$ a `dynamic_camera_tf_node.py` tramite il topic `/camera/extrinsic_pitch_correction`.
   - **Costmap Clearing:** Richiede un flush dinamico delle costmap ostacolo in `semantic_costmap_injector.py`, eliminando all'istante i "ghost obstacles" generati dall'errata proiezione del pavimento.
   - **Operator Alert Threshold:** Se l'inclinazione supera $\pm 10^\circ$, notifica la necessità di un serraggio fisico della staffa.

---

## 3. Residual Risk & RPN Scoring
- **Severity:** 8 (Resta immutata poiché la perdita visiva da danno grave è critica).
- **Occurrence:** 6 (Dipende da sollecitazioni meccaniche hardware).
- **Detection:** 2 (Migliorata da 7 a 2 grazie al rilevamento automatico continuo e diagnostica ROS 2).
- **Residual RPN:** $8 \times 6 \times 2 = 96$ (Ridotto da 336 a 96, Rischio gestito).
