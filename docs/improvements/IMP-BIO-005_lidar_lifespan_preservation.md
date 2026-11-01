# IMP-BIO-005: Preservazione Vita Utile RPLIDAR C1 via Kinematic Sensor Standby e TF Keepalive

## 1. Dati del Failure Mode
- **ID Failure Mode:** `FM-BIO-005`
- **Descrizione:** Usura precoce del rotore ottico e diodo laser RPLIDAR C1 per rotazione continua h24 in assenza di moto (MTBF nominale ~5000 ore ridotto a <8 mesi).
- **Severity iniziale:** 7 | **Occurrence iniziale:** 7 | **Detection iniziale:** 4 | **RPN iniziale:** 196
- **Severity residua (target):** 4 | **Occurrence residua:** 2 | **Detection residua:** 2 | **RPN residuo:** 16

---

## 2. Analisi Root Cause (5 Whys)
1. *Perché il rotore ottico del RPLIDAR C1 si usura precocemente?* Perché gira ininterrottamente a 10 Hz anche quando Marcus è fermo in salotto per ore o giorni.
2. *Perché non veniva spento quando fermo?* Perché spegnere il motore interrompe `/scan`, bloccando `nav2_amcl` che fa scadere la trasformata `map -> odom`, mandando in errore `bt_navigator` e `local_costmap`.
3. *Perché AMCL smette di pubblicare `map -> odom`?* Perché in ROS 2 Jazzy `nav2_amcl` aggiorna e pubblica il TF primario solo all'interno del callback `laserReceived`.
4. *Perché non veniva fatto fuori dalla dock?* Perché si temeva uno spostamento passivo del robot a sensori spenti con conseguente collisione alla ripartenza.
5. *Come risolvere strutturalmente?*
   - Implementare `StandbyTFKeepalive` a 1.0 Hz nel `SensorStandbyManager` che preserva la trasformata latciata `map -> odom` con timestamp aggiornato durante lo spegnimento motori.
   - Introdurre una soglia di inattività cinematica di 45 secondi fuori dalla base di ricarica con isteresi anti-cycling di 60 secondi.
   - Implementare un 5-Step Wake-Before-Move Handshake rigoroso prima di qualsiasi sblocco comandi motori: accensione motore LiDAR, attesa regime 10 Hz (1.5 s), kill di `StandbyTFKeepalive`, invocazione servizio `/request_nomotion_update` di AMCL, pulizia costmap e verifica dislocazione passiva con IMU/Visual Odometry.
   - Mantenere un registro persistente dell'usura su `/mnt/ssd/telemetry/lidar_lifespan_ledger.json`.

---

## 3. Piano di Azione e Implementazione
1. **Modifica `robopy_controller/robot_ai/services/resource_governor.py`:**
   - Aggiornamento Invariante 6: ammettere lo spegnimento del RPLIDAR C1 fuori dalla dock dopo 45 s di arresto cinematico confermato.
2. **Modifica `robopy_controller/nodes/sensor_standby_manager.py`:**
   - Istanziazione del thread `StandbyTFKeepalive` a 1.0 Hz (invio trasformata `map -> odom` con `now() + 100ms`).
   - Gestione logica anti-cycling (blocco re-sleep prima di 60 s di veglia).
   - Servizio o hook per 5-Step Handshake asincrono non bloccante.
   - Scrittura del ledger ore operative in `/mnt/ssd/telemetry/lidar_lifespan_ledger.json`.

---

## 4. Criteri di Validazione e Test
- **Test Unitario:** `tests/test_sensor_standby_manager.py` / `tests/test_biocognitive_evolution.py`: verifica che a robot fermo per 45 s parta la richiesta di standby e si attivi `StandbyTFKeepalive`; verifica che una richiesta di moto risvegli il sensore e termini il keepalive prima del moto.
- **Test sul Campo:** All'avvio di Marcus, monitorare con `ros2 topic echo /tf` che `map -> odom` non vada mai in timeout durante lo standby e che il motore si spenga fisicamente.
