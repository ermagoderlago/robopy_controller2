# 🛠️ Progetto di Miglioramento IMP-NAV-017
> **Target Failure Mode:** `FM-NAV-017` (Deriva Termica del Bias Giroscopico Z su Sessioni Lunghe)  
> **Priorità RPN Iniziale:** 448 -> **RPN Residuo:** 28 | **Stato:** COMPLETED | **Dominio:** Navigazione & IMU Sensing

---

## 1. Analisi del Problema & Cause Radice

### Problema
Durante missioni prolungate (>30 minuti), la dissipazione termica congiunta di Raspberry Pi 5 e della NPU Hailo-10H riscalda la struttura interna della testa del robot e l'alloggiamento della OAK-D Lite. Il sensore IMU Bosch BMI270 subisce una deriva termica non lineare dello *zero-rate offset* sul giroscopio asse Z, causando una rotazione apparente fittizia (yaw drift $\theta$) di diversi gradi al minuto anche a robot perfettamente fermo.

### Soluzione Implementata (Dynamic ZUPT Bias Auto-Nulling)
1. **Rilevamento Stato Statico:** Quando i comandi motore sono nulli (`!motors_active_`), la velocità ruote è zero e la magnitudine istantanea di rotazione è $< 0.08\text{ rad/s}$, il sistema riconosce la condizione di stazionarietà.
2. **Stima Continua del Bias Z:** Accumulo dei campioni di $\omega_z$ su un buffer a finestra mobile di 50 campioni. Calcolo della media e aggiornamento continuo tramite filtro esponenziale (`gyro_z_bias_`).
3. **Compensazione Dinamica Real-Time:** In `processIMU()`, la stima `gyro_z_bias_` viene sottratta istantaneamente dal segnale raw prima del deadband e della trasmissione al modulo di orientamento e motion gate.

---

## 2. File Modificati
- `src/fast_flow_vo_node.hpp`
- `src/fast_flow_vo_node.cpp`
- `test/unit/test_dfmea_nav_mitigations.py`
