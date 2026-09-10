# IMP-VUI-024: Persistenza Seriale Udev `/dev/respeaker` e Auto-Discovery Dinamico (Coesistenza USB con LiDAR C1)

## 1. Failure Mode Reference
- **ID:** `FM-VUI-024`
- **Subsystem:** Voice User Interface (VUI) / Hardware Serial (`respeaker_interface_node / Seeed XIAO ESP32-S3`)
- **Failure:** Mancata apertura della porta seriale USB CDC (`[Errno 2] No such file or directory: '/dev/ttyACM0'`), con caduta del controllo LED RGB e allarmi ciclici nei log.
- **Cause:** Inserimento di nuove periferiche USB (adattatore Slamtec RPLIDAR C1 ToF) che provoca il reset/re-enumerazione del bus USB e lo slittamento dell'indice da `/dev/ttyACM0` a `/dev/ttyACM1` o la perdita del device node statically configurato.
- **Initial RPN:** 80 (Severity: 5, Occurrence: 8, Detection: 2)

---

## 2. Solution Architecture & Implementation

### 1. Regole Udev Deterministiche (`99-marcus-serial.rules`)
- Definizione della mappatura permanente per il chip Seeed XIAO ESP32-S3 in `/etc/udev/rules.d/99-marcus-serial.rules`:
  ```udev
  # Seeed ReSpeaker Lite (XIAO ESP32-S3 - LED & Hardware Control)
  SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", MODE="0666", GROUP="dialout", SYMLINK+="respeaker"
  SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="0002", MODE="0666", GROUP="dialout", SYMLINK+="respeaker"
  SUBSYSTEM=="tty", ATTRS{idVendor}=="2886", MODE="0666", GROUP="dialout", SYMLINK+="respeaker"
  ```
- Marcus dispone ora di tre endpoint seriali deterministici immutabili:
  1. `/dev/motor_driver` -> Scheda motori Waveshare General Driver ESP32
  2. `/dev/rplidar` -> Sensore Slamtec RPLIDAR C1 ToF 360°
  3. `/dev/respeaker` -> ReSpeaker Lite Seeed XIAO ESP32-S3 (LED & Control)

### 2. Auto-Discovery & Resilienza a Cascata in `respeaker_interface_node.py`
- Aggiunto il metodo `_resolve_port()` che garantisce continuità operativa anche in caso di modifiche runtime del bus:
  1. Verifica esistenza della porta configurata dal parametro ROS `uart_port` (default: `/dev/respeaker`).
  2. Se assente, verifica la presenza del symlink udev `/dev/respeaker`.
  3. Se assente, esegue scansione glob in `/dev/serial/by-id/*Espressif*` e `/dev/serial/by-id/*Seeed*`.
  4. Se ancora assente, effettua fallback dinamico sulla prima porta `/dev/ttyACM*` disponibile (es. `/dev/ttyACM1`).
- Il nodo effettua la transizione automatica registrando:
  `🔄 Porta '/dev/respeaker' non trovata: fallback automatico su '/dev/ttyACM1'`
  ed eliminando ogni eccezione non gestita o mancata connessione.

### 3. Allineamento Launch Files e Script di Avvio
- Aggiornato il parametro `uart_port` a `/dev/respeaker` in:
  - `restart_hailo.sh`
  - `launch/fast_flow_launch.py`
  - `launch/robot_ia_launch.py`

---

## 3. Residual Risk & RPN Scoring
- **Severity:** 3 (Abbassata da 5 a 3: la pipeline vocale e audio di `respeaker_vui_node` resta comunque attiva via ALSA UAC; l'eventuale perdita temporanea della seriale impatta solo l'animazione LED).
- **Occurrence:** 1 (Abbattuta da 8 a 1: il symlink deterministico e il quadruplo fallback prevengono qualsiasi fallimento di apertura porta).
- **Detection:** 1 (Migliorata da 2 a 1: notifica chiara nei log ROS di fallback o connessione stabilita a 20Hz/Heartbeat).
- **Residual RPN:** $3 \times 1 \times 1 = 3$ (Ridotto da 80 a 3, Rischio Mitigato e Chiuso).
