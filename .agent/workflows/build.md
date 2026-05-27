---
description: Build the robopy_controller ROS 2 package
---

// turbo-all

> ## Nota per l’IA
> Prima di rispondere a qualsiasi prompt su questo progetto:
> 1. Apri e leggi tutti i file classificati come "Alta" in `file_index.md`
> 2. Verifica se esistono già file simili prima di crearne di nuovi
> 3. Usa `file_index.md` come sorgente primaria di navigazione del repository

### File ad alta importanza

- `./CMakeLists.txt` — Configurazione build C++ per il pacchetto principale
- `./package.xml` — Metadati e dipendenze del pacchetto ROS 2
- `./setup.py` — Entry points e installazione dei nodi Python
- `./setup.cfg` — Configurazioni aggiuntive per Python e linting
- `./.env` — Variabili d'ambiente, chiavi API e segreti di configurazione
- `./00_START_HERE.txt` — Punto di ingresso e panoramica della documentazione
- `./INDEX.md` — Hub di navigazione per la ristrutturazione dei frame TF
- `./QUICK_START.md` — Guida rapida all'avvio e test del sistema
- `./README_FRAMES.md` — Documentazione principale per il sistema di coordinate
- `./TF_RESTRUCTURE_SUMMARY.md` — Dettagli tecnici sull'architettura dei frame e trasformazioni
- `./src/fast_flow_vo_node.cpp` — Nodo C++ principale per l'odometria visuale ad alte prestazioni
- `./robopy_controller/nodes/superpoint_node.py` — Nodo Python per l'estrazione feature basata su AI
- `./marcus_robot/package.xml` — Definizione del pacchetto robot Marcus e dipendenze
- `./weights/Marcus_architecture.md` — Architettura del sistema di intelligenza artificiale Marcus
- `./.agent/workflows/build.md` — Istruzioni per la compilazione e linee guida per l'IA

7. **IMPORTANTE: Gestione Ambienti**
   - **Compilazione ROS 2 Core (`~/ros2_jazzy`):** Deve avvenire SEMPRE in ambiente di sistema base. **NON** attivare `ros2env` per compilare il core.
   - **Esecuzione/Lancio Robot (`robopy_controller`):** Deve avvenire in ambiente virtuale `ros2env`.

8. Per eseguire il robot (DOPO la compilazione):
```bash
source ~/ros2env/bin/activate
```

2. Build the package:
```bash
cd /home/robopy/robopy/robopi_controller/robopy_controller_host
colcon build --packages-select robopy_controller \
  --symlink-install \
  --cmake-clean-first \
  --cmake-args -GNinja \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -C /home/robopy/ros2_jazzy/pi5_clang_optimization.cmake
```

3. Source the install:
```bash
source install/setup.bash
```

4. I pacchetti ROS2 sono installati in:
'''bash
cd ~/ros2_jazzy
'''

e per compilarli uso (DA AMBIENTE DI SISTEMA BASE - NO VENV!!):

'''bash

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
'''

per attivare la chiave IA devi usare:

source /home/robopy/robopy/robopi_controller/robopy_controller_host/setup_keys.sh

prima di lanciare ogni nodo ROS 2 o il robot.

## 🔄 Sincronizzazione ed Esecuzione Rapida (Hot-Swap)
Durante lo sviluppo sul PC Windows, puoi sincronizzare ed eseguire il codice a caldo (senza dover ricostruire il workspace completo `colcon` per modifiche puramente Python):
1. Esegui `.\sync_marcus.bat` da PowerShell sul PC Windows. Questo script usa SSH ControlMaster e rsync per copiare i file modificati sia nei sorgenti sia direttamente in `install/` sul Raspberry Pi in pochissimi secondi.
2. Per riavviare i nodi AI e VUI sul robot, esegui:
   ```bash
   ssh robopy@marcus
   bash /mnt/ssd/robopy_controller_host/restart.sh
   ```
3. **Watchdog Cognitivo (Systemd)**:
   Il robot è protetto dal servizio di monitoraggio automatico `marcus-watchdog.service`. In caso di 3 crash consecutivi in 60s, il demone esegue automaticamente lo swap del symlink di produzione puntandolo alla versione precedente stabile `install_v15` (rollback A/B a caldo).
   Per controllare e gestire il servizio systemd sul Raspberry Pi:
   ```bash
   # Controlla lo stato del servizio di sopravvivenza
   sudo systemctl status marcus-watchdog.service
   # Riavvia il servizio watchdog
   sudo systemctl restart marcus-watchdog.service
   # Visualizza il log delle anomalie e dei crash
   cat /home/robopy/logs/watchdog.log
   ```

## ⚠️ IMPORTANTE: Aggiungere un nuovo Nodo Python a robopy_controller
Poiché `robopy_controller` è un pacchetto misto C++/Python compilato con `ament_cmake`, aggiungere un nodo Python al `console_scripts` del `setup.py` **NON BASTA**. La macro `ament_python_install_package` non genererà automaticamente gli eseguibili nella cartella `libexec` durante una build `ament_cmake` in ROS 2 Jazzy.

Quando crei un nuovo nodo Python, DEVI seguire esattamente questi 5 passaggi:
1. Crea il tuo codice Python (es. `robopy_controller/nodes/mio_nuovo_nodo.py`).
2. Aggiungilo a `console_scripts` in `setup.py` (essenziale per registrare l'entry point in ROS).
3. Crea uno script eseguibile "wrapper" nella cartella `scripts/` (es. `scripts/mio_nuovo_nodo` *senza* estensione `.py`) che importa ed esegue il tuo `main()`. Esempio:
   ```python
   #!/usr/bin/env python3
   import sys
   from robopy_controller.nodes.mio_nuovo_nodo import main
   if __name__ == '__main__':
       sys.exit(main())
   ```
   **Nota**: L'import diretto tramite `importlib.util.spec_from_file_location` usato in passato è obsoleto e va evitato. Usa l'import standard come nell'esempio.
4. Rendi lo script eseguibile: `chmod +x scripts/mio_nuovo_nodo`
5. Registralo nel `CMakeLists.txt` aggiungendo la riga `scripts/mio_nuovo_nodo` nel blocco esistente `install(PROGRAMS ... DESTINATION lib/${PROJECT_NAME})`!

ATTENZIONE MAI CANCELLARE I FILE COMPILATI DI ROS2 NELLA CARTELLA  ~/ros2_jazzy!!!!   MAI!!! ATTENZIONE

## 🛠️ Monitoraggio SSD e Ottimizzazione
- Il workspace è ora su **SSD** (`/mnt/ssd/ros2_jazzy`).
- Monitora lo spazio con: `df -h /mnt/ssd`.
- Le build ora usano lo **stripping dei simboli** (`-Wl,--strip-all`) per ridurre l'impronta su disco e RAM.

## 🔌 Compilazione e Flash del Firmware ESPHome (ReSpeaker Lite)

Il microcontrollore del ReSpeaker Lite (XIAO ESP32S3) gestisce il controllo dei LED e la sincronizzazione hardware. Per evitare crash o rallentamenti sul Raspberry Pi, segui rigorosamente questa procedura di compilazione in WSL e flashing sul Pi:

> [!WARNING]
> **ATTENZIONE AGLI SPAZI NEL PATH (PlatformIO Limitation)**:
> La cartella del progetto locale su Windows si trova all'interno di OneDrive (`OneDrive - BRUGOLA OEB INDUSTRIALE SPA`), il cui percorso contiene spazi. Il compilatore di ESPHome (PlatformIO/GCC) **fallirà sistematicamente** se eseguito direttamente dentro una cartella con spazi nel percorso.
>
> Per aggirare questo limite, è stato predisposto lo script di automazione **`./compile_wsl.sh`** che copia i sorgenti del firmware in una cartella di build pulita e priva di spazi (`/home/robopy/respeaker_build`), compila in sicurezza e trasferisce il binario sul Pi.

### 1. Compilazione Standard (In WSL locale)
La compilazione richiede risorse significative ed è configurata in WSL.
1. Accedi a WSL dal PC Windows:
   ```bash
   ssh wsl
   ```
2. Spostati nella directory di lavoro ed esegui lo script di compilazione:
   ```bash
   cd "/mnt/c/Users/lsuffia/OneDrive - BRUGOLA OEB INDUSTRIALE SPA/Documents/robopy/antigravity"
   bash ./compile_wsl.sh
   ```
   *Nota: Lo script esporta automaticamente `IDF_MAINTAINER=1` per convertire la discrepanza di versione tra la toolchain `pioarduino` e il `framework-espidf` (versione 5.5.2) da errore fatale a semplice warning non-blocking.*

### 2. Flashing del Firmware sul Robot (Via SSH sul Pi)
1. Connettiti via SSH al Raspberry Pi:
   ```bash
   ssh robopy@marcus
   ```
2. **IMPORTANTE: Sblocca la porta seriale prima di flashare!** Se i nodi ROS 2 sono attivi, tengono `/dev/ttyACM0` bloccato in esclusiva. Forza il loro arresto:
   ```bash
   pkill -9 -f respeaker_interface_node || true
   pkill -9 -f respeaker_vui_node || true
   pkill -9 -f robot_ai_node || true
   ```
3. Attiva l'ambiente virtuale ed esegui il flashing tramite `esptool`:
   ```bash
   source /home/robopy/esphome_venv/bin/activate
   esptool --port /dev/ttyACM0 --baud 115200 write-flash 0x0 /tmp/firmware_led_v14.factory.bin
   ```
4. Riavvia la brain di Marcus:
   ```bash
   bash /mnt/ssd/robopy_controller_host/restart.sh
   ```

---

### 🛠️ Ricostruzione ed Installazione dell'Ambiente da Zero (Bootstrap & Troubleshooting)

Se l'ambiente WSL o PlatformIO dovesse corrompersi o se volessi reinstallarlo completamente da zero su un nuovo PC, segui questi passaggi architetturali precisi:

#### STEP 1: Installazione dell'Ambiente Virtuale di ESPHome
Crea e attiva l'ambiente virtuale python isolato per installare ESPHome in WSL:
```bash
python3 -m venv /home/robopy/esphome_venv
source /home/robopy/esphome_venv/bin/activate
pip install esphome tornado esptool
```

#### STEP 2: Risoluzione dell'Isolamento SCons (PEP 668 Link)
SCons esegue i sottoprocessi di compilazione con il Python di sistema (`/usr/bin/python3`), che in Ubuntu 24.04+ è bloccato (PEP 668) e manca delle librerie di PlatformIO. Per permettere a SCons di importare `platformio` senza inquinare il sistema:
1. Crea un link `.pth` locale all'interno della cartella site-packages dell'utente `/home/robopy/.local`:
   ```bash
   mkdir -p ~/.local/lib/python3.12/site-packages
   echo "/home/robopy/esphome_venv/lib/python3.12/site-packages" > ~/.local/lib/python3.12/site-packages/esphome_venv.pth
   ```
   Questo permette a qualsiasi esecuzione del Python di sistema di accedere alle dipendenze PlatformIO dell'ambiente virtuale.

#### STEP 3: Configurazione e Bootstrap del compilatore
Durante la prima esecuzione di `esphome compile respeaker.yaml`, PlatformIO inizializza i pacchetti sotto `~/.platformio/packages/`. A causa di dipendenze vuote o bootstrap interrotti, segui questi passaggi per sanare l'ambiente:
1. **Zero-Byte File templates**: Se l'installatore lamenta file `tools.json` corrotti, ripristina i template di base:
   - File template 1: `/home/robopy/.platformio/packages/framework-espidf/tools/tools.json`
   - File template 2: `/home/robopy/.platformio/packages/tool-esp_install/tools/tools.json`
2. **Installazione Dipendenze ESP-IDF**:
   Usa `uv` per popolare velocemente l'ambiente virtuale interno di ESP-IDF (`.espidf-5.5.2`) con le dipendenze core (tra cui `esp-idf-kconfig` per generare `kconfgen`):
   ```bash
   pip install uv
   uv pip install --python /home/robopy/.platformio/penv/.espidf-5.5.2/bin/python -r /home/robopy/.platformio/packages/framework-espidf/tools/requirements/requirements.core.txt
   ```
3. **Purge della Piattaforma 'win-arm64'**:
   Gli installer legacy di espressif v5.3 falliscono sistematicamente se rilevano la presenza di `"win-arm64"` nei file `tools.json` degli altri pacchetti scaricati. Rimuovi ricorsivamente ogni stringa `"win-arm64"` o riferimenti ad essa in tutti i `tools.json` sotto `/home/robopy/.platformio/packages/`.

---

### 3. Rollback in caso di Emergenza
Se riscontri problemi o freeze al microfono I2S/DMA, ripristina istantaneamente il firmware sicuro e collaudato:
1. In WSL locale, compila la versione standard sicura:
   ```bash
   mkdir -p /home/robopy/respeaker_build
   cp robopy_controller/files_utili/respeaker_lite_firmware.yaml /home/robopy/respeaker_build/respeaker.yaml
   cp robopy_controller/files_utili/respeaker_helper.h /home/robopy/respeaker_build/
   cp secrets.yaml /home/robopy/respeaker_build/
   
   cd /home/robopy/respeaker_build
   source /home/robopy/esphome_venv/bin/activate
   export IDF_MAINTAINER=1
   esphome compile respeaker.yaml
   scp .esphome/build/respeaker-lite/.pioenvs/respeaker-lite/firmware.factory.bin robopy@marcus:/tmp/firmware_safe.factory.bin
   ```
2. Sul Pi:
   ```bash
   source /home/robopy/esphome_venv/bin/activate
   esptool --port /dev/ttyACM0 --baud 115200 write-flash 0x0 /tmp/firmware_safe.factory.bin
   ```


## Collegamenti a frammenti correlati

- `./weights/Marcus_architecture.md` — Descrizione dell'architettura neurale e logica di Marcus
- `./weights/lesson_learned.md` — Archivio delle lezioni apprese durante lo sviluppo qui ci sono tutte le lezioni imparate per non commettere gli stessi errori, se devi modificare un file leggi sempre prima questo file, avvisa se violi le regole di lesson_learned ed interrompiti, procedi solo dopo esplicito via libera dall'utente.

## Accesso Rapido a WSL, se servisse
Per collegarsi rapidamente all'ambiente di sviluppo WSL dall'host Windows, usa il comando:
```bash
ssh wsl
```