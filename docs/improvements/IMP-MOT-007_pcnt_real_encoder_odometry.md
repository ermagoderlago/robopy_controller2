# 🚀 Progetto di Miglioramento: IMP-MOT-007
## Odometria Metrica Reale da Encoder Hardware PCNT ed Integrazione Runge-Kutta 2° Ordine

---

### 1. Scheda Informativa del Failure Mode
- **ID Failure Mode:** FM-MOT-007
- **Sottosistema:** Actuation & Motion / Nav2 & SLAM
- **Componente:** waveshare_motor_driver (waveshare_motor_driver.py), firmware ESP32 (waveshare_bridge.h)
- **Titolo del Problema:** Transizione da Odometria Teorica ad Odometria Fisica Reale da Encoder PCNT
- **RPN Iniziale:** Severity 7, Occurrence 8, Detection 2 -> RPN = 112
- **RPN Residuo:** Severity 7, Occurrence 1, Detection 1 -> RPN = 7 (LOW, CLOSED)

---

### 2. Causa Radice & Contesto Storico
In precedenza, sul telaio di Marcus il segnale encoder PIN_M1_ENCA (GPIO 35 / C2) era interrotto/aperto e le letture di interrupt software subivano burst spuri di 'falso movimento' fino a 6000 tick/s da fermo. Per evitare lo sfarfallio della mappa SLAM, era stata introdotta l'odometria teorica da /cmd_vel (use_cmd_vel_odometry := True).
Sebbene immune al rumore degli encoder, l'odometria teorica presentava un limite cinematico critico:
1. Non misurava l'effettiva aderenza delle ruote, accumulando deriva in curva a catena aperta prima che il LiDAR RPLIDAR C1 riuscisse a vincolare la scansione su RTAB-Map.
2. Non rifletteva micro-slittamenti su moquette o variazioni di carico della batteria.

---

### 3. Azioni Ingegneristiche Attuate
1. Hardware Glitch Filtering & Direction Gating in Silicon:
   - Attivato il contatore hardware ad impulsi (PCNT Unit 1) per contare entrambi i fronti del canale attivo PIN_M1_ENCB (GPIO 34), condizionando il conteggio up/down al pin di direzione PIN_M1_DIR1 (GPIO 21) via ESP32 GPIO Matrix.
   - Scalato il conteggio di un fattore 2 per allineare perfettamente la risoluzione metrica a 657 CPR su entrambe le ruote.
2. Attivazione Odometria Reale in ROS 2:
   - Impostato use_cmd_vel_odometry := False e use_encoder_for_linear := True come configurazione nominale in waveshare_motor_driver.py e in restart_hailo.sh.
   - Impostato invert_left_encoder := False e invert_right_encoder := False (risolto storico workaround di polarità).
3. Integrazione Runge-Kutta a Punto Medio 2° Ordine:
   - Calcolo metrico ad ogni ciclo Delta t con aggiornamento di x, y e theta a punto medio.
4. Protezione Standstill a Deriva Zero:
   - Tier 1 Zero-Velocity Standstill Lock impone Delta T = 0 quando motors_stopped e attivo, prevenendo qualsiasi deriva della posa da fermo.

---

### 4. Verifica e Risultati
- Test Unitari: 22/22 unit test superati.
- Collaudo Fisico su Telaio: Avanti Left = +690, Right = +675 ticks (97.8% simmetria), arresto Short-Brake istantaneo con 0 drift.
- Riferimento Lezioni: docs/lessons/actuation_motor_driver.md#real-encoder-odometry-switch (Lezione 31).
- Riferimento ECO: docs/ecos/actuation_ecos.md#ECO-2026-09-14-002.
