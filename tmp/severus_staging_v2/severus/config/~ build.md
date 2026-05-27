---
description: Build the severus ROS 2 package
---

// turbo-all

# 🤖 CONTESTO PER L'IA E WORKFLOW DI SVILUPPO
**ATTENZIONE IA (Antigravity):** Attualmente stai girando in locale su un PC **Windows**. Il target di esecuzione del codice è un **Raspberry Pi 5** (ARM64, Linux) connesso tramite rete.
- **Sincronizzazione:** Le modifiche ai file sorgente vengono fatte da te (IA) in locale su Windows. Al salvataggio, i file vengono inviati automaticamente al Raspberry tramite SFTP.
- **Esecuzione Comandi:** NON puoi eseguire comandi Linux localmente. Tutti i comandi bash presenti in questo documento o suggeriti da te devono essere eseguiti dall'utente in un terminale separato connesso in **SSH** (`ssh robopy@marcus` - l'utente ha già configurato l'accesso senza password).
- **Cartelle Ignorate:** Le cartelle `build/`, `install/` e `log/` esistono solo sul Raspberry e non vengono scaricate in locale per evitare conflitti con i symlink di ROS 2. Non tentare di leggerle o modificarle.

---

# 🛠️ REGOLE DI SISTEMA E COMPILAZIONE

## 1. IMPORTANTE: Gestione Ambienti
- **Compilazione ROS 2 Core (`~/ros2_jazzy`):** Deve avvenire SEMPRE in ambiente di sistema base. **NON** attivare `ros2env` per compilare il core.
- **Esecuzione/Lancio Robot (`severus`):** Deve avvenire in ambiente virtuale `ros2env`.

## 2. Compilazione pacchetti ROS 2 Core
I pacchetti ROS2 sono installati in:
```bash
cd ~/ros2_jazzy
```
E per compilarli uso (DA AMBIENTE DI SISTEMA BASE - NO VENV!!):

```bash
colcon build \
  --symlink-install \
  --packages-select depthai_core \
  --cmake-args \
    -GNinja \
    -DBUILD_TESTING=OFF \
    -C ~/ros2_jazzy/pi5_optimization.cmake \
    -DCMAKE_VERBOSE_MAKEFILE=ON \
    -DCMAKE_PREFIX_PATH="$HOME/ros2_jazzy/install" \
    -DCMAKE_FIND_ROOT_PATH="$HOME/ros2_jazzy/install"
```
🚨 ATTENZIONE MAI CANCELLARE I FILE COMPILATI DI ROS2 NELLA CARTELLA  ~/ros2_jazzy!!!!   MAI!!! ATTENZIONE

## 3. Build the package (severus)
```bash
cd /mnt/ssd/severus_host
colcon build --packages-select severus \
  --symlink-install \
  --cmake-clean-first \
  --cmake-args -GNinja \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -C /mnt/ssd/ros2_jazzy/pi5_clang_optimization.cmake
```

## 4. Per eseguire il robot (DOPO la compilazione)
Prima di lanciare qualsiasi nodo, per attivare la chiave IA devi usare:

```bash
source /mnt/ssd/severus_host/setup_keys.sh
```
Quindi, attiva l'ambiente ed esegui il source dell'installazione:

```bash
source ~/ros2env/bin/activate
cd /mnt/ssd/severus_host
source install/setup.bash
```

⚠️ IMPORTANTE: Aggiungere un nuovo Nodo Python a severus
Poiché severus è un pacchetto misto C++/Python compilato con ament_cmake, aggiungere un nodo Python al console_scripts del setup.py NON BASTA. La macro ament_python_install_package non genererà automaticamente gli eseguibili nella cartella libexec durante una build ament_cmake in ROS 2 Jazzy.

Quando crei un nuovo nodo Python, DEVI seguire esattamente questi 5 passaggi:

1. Crea il tuo codice Python (es. severus/nodes/mio_nuovo_nodo.py).
2. Aggiungilo a console_scripts in setup.py.
3. Crea uno script eseguibile "wrapper" nella cartella scripts/ (es. scripts/mio_nuovo_nodo senza estensione .py).
4. Rendi lo script eseguibile: `chmod +x scripts/mio_nuovo_nodo`
5. Registralo nel CMakeLists.txt aggiungendo la riga scripts/mio_nuovo_nodo nel blocco esistente `install(PROGRAMS ... DESTINATION lib/${PROJECT_NAME})`!

🛠️ Monitoraggio SSD e Ottimizzazione
Il workspace è ora su SSD (/mnt/ssd/ros2_jazzy).
Monitora lo spazio con: `df -h /mnt/ssd`.
Le build ora usano lo stripping dei simboli (-Wl,--strip-all) per ridurre l'impronta su disco e RAM.

## ⚡ 7. COMPILAZIONE FIRMWARE ESP32 (RESPEAKER)
- **Ambiente di Compilazione:** Il firmware del microfono DEVE essere compilato usando **WSL (Ubuntu su Windows)** in locale. Non compilarlo MAI sul Raspberry Pi.
- **Workflow per l'IA:**
  1. Istruisci l'utente ad aprire un terminale **WSL** integrato in VS Code.
  2. Fornisci il comando per la SOLA compilazione: `esphome compile respeaker_lite_firmware.yaml`
  3. **Regola per il Flashing:** Una volta terminata la compilazione, scrivi all'utente di aprire il browser su Windows, andare su `https://web.esphome.io` e flashare il firmware da lì.

## ⚡ 8. WORKFLOW ESP32 (FLASHING)
Quando devi far testare una modifica al microfono all'utente, usa uno di questi metodi:

**Opzione A: Aggiornamento Wi-Fi (OTA)**
Chiedi all'utente di aprire il terminale **WSL locale** ed eseguire:
`esphome run respeaker_lite_firmware.yaml`

**Opzione B: Passaggio file per flash via cavo USB (Emergenza)**
1. Compila in **WSL locale**: `esphome compile respeaker_lite_firmware.yaml`
2. Estrai il file `.bin`: `mkdir -p firmware_pronto && cp .esphome/build/*/.*/*/firmware.bin firmware_pronto/respeaker.bin`
3. Flash da **terminale SSH** del Pi:
   `source ~/esphome_venv/bin/activate`
   `esphome run respeaker_lite_firmware.yaml --device /dev/ttyACM0`

**Opzione C: Flash manuale con esptool (Metodo Diretto)**
1. Compila in **WSL locale**:
   `esphome compile respeaker_lite_firmware.yaml`
2. Copia il file binario sul Raspberry Pi (Marcus):
   `scp .esphome/build/respeaker-lite/.pioenvs/respeaker-lite/firmware.factory.bin robopy@marcus:/tmp/firmware2.factory.bin`
3. Esegui il flash da **terminale SSH** sul Raspberry Pi:
   `source /home/robopy/esphome_venv/bin/activate`
   `esptool --port /dev/ttyACM0 --baud 115200 write_flash 0x0 /tmp/firmware2.factory.bin`

## ⚡ 9. ESECUZIONE COMANDI SSH DA POWERSHELL (WINDOWS)
**ATTENZIONE IA:** Da PowerShell, i comandi SSH con `source` e heredoc **non funzionano** direttamente perché SSH di Windows usa `sh` e non `bash`. Usare uno di questi pattern:

**Pattern A: Script file (CONSIGLIATO)**
1. Crea un file `.sh` in locale (es. `/tmp/mio_script.sh`)
2. Copialo sul Pi: `scp /tmp/mio_script.sh robopy@marcus:/tmp/mio_script.sh`
3. Eseguilo: `ssh robopy@marcus "bash /tmp/mio_script.sh"`

Lo script deve contenere tutti i `source` necessari. Template standard:
```bash
#!/bin/bash
source /mnt/ssd/ros2_jazzy/install/setup.bash
source /mnt/ssd/severus_host/install/setup.bash
# ...i tuoi comandi ros2 qui...
```

**Pattern B: Comandi semplici (senza source)**
Per comandi che non richiedono ambiente ROS 2:
```powershell
ssh robopy@marcus "ls /dev/ttyACM*"
ssh robopy@marcus "cat /tmp/output.log"
```

**⚠️ NON FUNZIONA da PowerShell:**
```powershell
# ERRORE: source non funziona in sh
ssh robopy@marcus "source ~/ros2env/bin/activate && ros2 node list"
# ERRORE: heredoc rotto dal quoting PowerShell
ssh robopy@marcus "cat > /tmp/x.sh << 'EOF' ..."
```


## 🎤 10. NUOVA MODALITA' DI TEST AUDIO (RESPEAKER USB)
Per testare se il microfono USB del ReSpeaker funziona nativamente (bypassando Porcupine/ROS), usa questo comando di sistema per registrare a **2 canali (Stereo)**, a 16kHz, formato 16-bit:

```bash
# Sostituisci hw:1,0 o hw:3,0 con il device id hardware che trovi in `arecord -l` 
arecord -D hw:1,0 -c 2 -f S16_LE -r 16000 -d 5 test_stereo.wav
```
*(Nota: Il ReSpeakerLite produce segnale su 2 canali, estrarre il mono richiede software)*