# Lezioni Apprese - Attuazione & Controllo (Waveshare ESP32)

Questo documento raccoglie le lezioni apprese sull'interfacciamento seriale a basso livello con la scheda Waveshare General Driver (ESP32) per la cinematica differenziale e la telemetria di Marcus.

---

## 🔌 Interfaccia Seriale e Inizializzazione Hardware

### Reset Seriale all'Avvio (DTR/RTS)
* **Contesto:** L'ESP32 a bordo della scheda Waveshare può rimanere in uno stato inconsistente o bloccato. Per garantire un avvio pulito, il nodo ROS 2 (`waveshare_motor_driver.py`) deve eseguire una sequenza di reset forzato agendo sulle linee DTR e RTS all'apertura del socket seriale:
  1. Impostare `DTR = True` e `RTS = True` (abbassa la linea EN resettando la scheda).
  2. Dormire per `0.1s`.
  3. Impostare `DTR = False` e `RTS = False` (rilascia EN, avviando l'ESP32).
  4. Attendere `3.0s` stabili per consentire il boot completo del firmware.
  5. Svuotare i buffer seriali (`reset_input_buffer()`, `reset_output_buffer()`).

### Handshake e Telemetria
* All'avvio, inviare esplicitamente i comandi JSON di abilitazione telemetria:
  * Abilitazione feedback continuo: `{"T":131,"cmd":1}\n`
  * Query iniziale chassis: `{"T":1001}\n`
* Attendere il pacchetto JSON di risposta con `"T": 1001` per validare l'handshake.

---

## ⚙️ Cinematica Differenziale e Watchdog

### Comandi di Velocità
* Il nodo traduce i comandi `/cmd_vel` in velocità lineare delle ruote sinistra (`L`) e destra (`R`) in m/s, trasmettendoli a 20Hz tramite il comando JSON:
  `{"T": 1, "L": v_L, "R": v_R}\n`
* Le velocità sono calcolate tramite cinematica differenziale classica:
  * $v_L = v - \frac{\omega \cdot W}{2.0}$
  * $v_R = v + \frac{\omega \cdot W}{2.0}$
  * Dove $W$ è la separazione tra le ruote (wheel separation).

### Watchdog di Sicurezza Hardware
* **Regola Permanente:** Il driver deve verificare periodicamente (a 10Hz) la ricezione dei comandi ROS. Se non arrivano nuovi comandi `/cmd_vel` per oltre 500ms, inviare immediatamente il comando seriale di stop `{"T": 1, "L": 0.0, "R": 0.0}\n` per prevenire derive incontrollate in caso di crash della brain.

---

## 📈 Parsing Telemetria ed Odometria

### Encoder e Wrap-Around
* La telemetria di ritorno riporta i tick cumulativi dell'encoder sinistro (`odl`) e destro (`odr`).
* Il codice deve calcolare la posa geometrica ($X, Y, \theta$) integrando lo spostamento dei tick per ogni intervallo temporale.
* **Filtro anomalie:** I salti repentini anomali dovuti a reset della scheda o overflow dei registri (es. delta tick maggiori del limite fisico di rotazione) devono essere intercettati e scartati per evitare balzi della stima di odometria.

### Monitoraggio Batteria
* La telemetria fornisce la tensione di batteria (`v`) in millivolt. Per una batteria LiPo 3S, impostare il monitoraggio per range nominali 9.9V (0%) - 12.6V (100%) per innescare allarmi vocali di sottotensione ed evitare il danneggiamento delle celle.
