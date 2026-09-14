# IMP-MOT-008: Anello Chiuso di Velocità 50Hz su ESP32 e Dynamic Short-Braking su TB6612FNG

## 1. Failure Mode Reference
- **ID:** `FM-MOT-008`
- **Subsystem:** Actuation & Motion (`waveshare_bridge.h / waveshare_motor_driver.py`)
- **Failure:** Asimmetria delle velocità delle due ruote motrici in marcia rettilinea (deviazione laterale) e scivolamento inerziale asimmetrico all'arresto con rotazione parassita dello chassis.
- **Cause:** Discrepanze meccaniche ed elettriche tra i due motoriduttori Brushed DC pilotati in open-loop (duty cycle PWM grezzo); stato di alta impedenza (*Coast Mode*: `IN1=0, IN2=0`) del driver TB6612FNG al comando di arresto `PWM=0` che lasciava le ruote libere di decelerare per inerzia e attrito differenziale.
- **Initial RPN:** 168 (Severity: 7, Occurrence: 8, Detection: 3)

---

## 2. Solution Architecture & Implementation

### 1. Root Cause Analysis
1. **Asimmetria in Moto:** In catena aperta (*open loop*), a parità di duty PWM inviato ai due canali, lievi tolleranze nella resistenza interna degli avvolgimenti dei motori, nei giochi del riduttore ad ingranaggi o nella pressione al suolo causano una discrepanza di rotazione del 5-10%, costringendo il robot a curvare anziché procedere in linea retta.
2. **Scivolamento Inerziale all'Arresto (*TB6612FNG Coast Mode*):** Nel codice precedente, l'invio di stop azzerava il PWM impostando `DIR1=0, DIR2=0`. Nel ponte ad H Toshiba TB6612FNG, questa combinazione disattiva tutti i MOSFET del ponte ponendo le uscite in **alta impedenza (High-Z Coast Mode)**: il rotore gira liberamente per inerzia. Poiché uno dei due riduttori ha intrinsecamente attriti leggermente inferiori o maggior slancio cinetico, quella ruota prosegue la corsa per diversi millimetri in più rispetto all'altra, inducendo una torsione netta dello chassis a veicolo fermo.

### 2. Dynamic Short-Brake Mode on TB6612FNG
Nella routine di attuazione a basso livello su ESP32 (`apply_motor_hardware`):
- Quando $|duty| < 0.01$, i pin di direzione vengono forzati a `DIR1=HIGH, DIR2=HIGH` con `PWM=255`.
- Nel silicio del TB6612FNG, questo porta in conduzione entrambi i MOSFET low-side dell'H-Bridge, mettendo in **cortocircuito controllato gli avvolgimenti del motore verso massa (Short Brake Mode)**.
- La forza contro-elettromotrice (Back-EMF) generata dall'inerzia del rotore produce all'istante una potente coppia elettromagnetica contraria proporzionale alla velocità angolare residua, frenando rigidamente entrambe le ruote in meno di 10 millisecondi e impedendo qualsiasi rotazione parassita del telaio.

### 3. 50 Hz Inner Closed-Loop Velocity Controller (Feedforward + PI)
Eseguito direttamente a bordo microcontrollore ESP32 (`run_pid_control` ogni 20 ms):
- **Campionamento Hardware PCNT:** I tick degli encoder magnetici a quadratura 4X (con filtro hardware 2000 ns) vengono letti a $\Delta t = 20\text{ ms}$.
- **Architettura Feedforward + Reazione:**
  $$\text{PWM}_{out} = \text{PWM}_{ff} + K_p \cdot e + K_i \cdot \int e\,dt + K_d \cdot \frac{de}{dt}$$
  - $\text{PWM}_{ff} = \text{target\_duty} \times 255.0$ (risposta immediata in open-loop senza attendere l'accumulo dell'errore).
  - $K_p = 3.20, K_i = 0.22, K_d = 0.04$ (compensazione rapida e priva di overshoot delle minime discrepanze tra le due ruote in 20 ms).
  - Anti-windup limitatore integrale su $\pm 75$ per evitare saturazioni della dinamica.
- **Riconfigurazione Dinamica ROS 2:**
  - Il nodo ROS 2 `waveshare_motor_driver.py` espone i parametri dinamici `enable_esp32_pid`, `esp32_pid_kp`, `esp32_pid_ki`, `esp32_pid_kd`.
  - All'avvio e ad ogni variazione parametrica tramite `ros2 param set`, invia il comando seriale `{"T":133,"pid":1,"kp":3.2,"ki":0.22,"kd":0.04}\n`.
  - La telemetria a 20Hz (`{"T":1001,...,"pid":1}`) conferma lo stato attivo dell'anello chiuso, esposto su `/diagnostics`.

---

## 3. Residual Risk & RPN Scoring
- **Severity:** 7 (Preservata per il vincolo di stabilità dinamica e cinematica del robot).
- **Occurrence:** 1 (Abbattuta da 8 a 1: correzione continua a 50Hz delle velocità ruote e frenata attiva magnetica su corto TB6612FNG).
- **Detection:** 1 (Migliorata da 3 a 1: telemetria encoder 20Hz, feedback bidirezionale `{"T":133}` e diagnostics supervisor).
- **Residual RPN:** $7 \times 1 \times 1 = 7$ (Ridotto da 168 a 7, Rischio Mitigato e Chiuso).
