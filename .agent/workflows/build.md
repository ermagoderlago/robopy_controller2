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

## 🔌 Compilazione e Flash del Firmware ESPHome (Waveshare General Driver)

La scheda Waveshare General Driver (ESP32) gestisce il controllo dei motori e la lettura degli encoder. Per compilarla e flasharla senza ricorrere alla MicroSD, segui questa procedura:

### 1. Risoluzione Problemi di Alimentazione e Disconnessione SSH
> [!IMPORTANT]
> **Vincolo Fisico di Alimentazione (Overcurrent & Crash WiFi)**:
> Se l'alimentazione esterna (batteria) della Waveshare è spenta durante il collegamento USB con il Raspberry Pi 5, la scheda proverà ad alimentare i circuiti dei motori tramite la porta USB del Pi. 
> Questo causa un sovraccarico (overcurrent) che fa calare la tensione a 3.3V sulla scheda di rete del Pi 5, provocando la disconnessione immediata della sessione SSH (errore `Connection closed` o `Resource temporarily unavailable`).
> **Soluzione:** Accendere sempre l'alimentazione esterna della Waveshare prima di eseguire test seriali.

### 2. Compilazione Offline sul Raspberry Pi (Evitare il Firewall Aziendale)
Il firewall aziendale blocca i download di grossi file compressi (come la toolchain ESP-IDF) da GitHub, causando disconnessioni di rete sul Pi.
Per aggirare questo blocco, tutti i pacchetti necessari sono stati pre-scaricati e salvati nella cache di PlatformIO sul Pi (`/home/robopy/.platformio_local/packages`).
Per compilare senza scaricare nulla da internet:
1. Accedi al Pi via SSH:
   ```bash
   ssh robopy@marcus
   ```
2. Compila in modalità offline ed esegui la build sui core CPU 0 e 1 (per evitare picchi di calore/corrente):
   ```bash
   cd /home/robopy/waveshare_build_pi
   export PLATFORMIO_RUN_OFFLINE=true
   source /home/robopy/esphome_venv/bin/activate
   taskset -c 0,1 esphome compile waveshare_driver.yaml
   ```

### 3. Flashing sul Robot
1. Arresta eventuali script o nodi ROS che occupano la porta `/dev/ttyUSB0`:
   ```bash
   sudo fuser -k /dev/ttyUSB0 || true
   ```
2. Attiva l'ambiente virtuale ed esegui il flashing tramite `esptool`:
   ```bash
   source /home/robopy/esphome_venv/bin/activate
   esptool --port /dev/ttyUSB0 --baud 115200 write-flash 0x0 /home/robopy/waveshare_build_pi/.esphome/build/waveshare-motor-driver/.pioenvs/waveshare-motor-driver/firmware.factory.bin
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


## 🚀 Compilazione Modelli NPU Hailo su WSL

Per compilare ed ottimizzare i modelli di Deep Learning (es. YOLO, SuperPoint) da formato **ONNX/TFLite** a binario **HEF** compatibile con l'NPU Hailo-10H del robot:

1. Accedi a WSL2 sul PC ed attiva l'ambiente dedicato:
   ```bash
   source ~/hailo_env/bin/activate
   ```
2. **Utilizzo di Hailo Model Zoo (`hailomz`)**:
   L'utility gestisce il download, il parsing, la quantizzazione e la compilazione automatica dei modelli noti:
   ```bash
   # Visualizza informazioni su un modello (es. yolov8n)
   hailomz info yolov8n
   
   # Esegui la compilazione in formato HEF per Hailo-10H
   hailomz compile yolov8n
   ```
3. **Utilizzo di Hailo Dataflow Compiler (`hailo` CLI)**:
   Per compilazioni custom o passaggi manuali di ottimizzazione (quantizzazione INT8) tramite SDK:
   ```bash
   hailo --help
   # Parsing di un ONNX personalizzato in formato HAR (Hailo Archive):
   hailo parser onnx /path/to/model.onnx
   ```

#### 📦 Ricette di Compilazione per i Modelli di Marcus:

##### A. YOLOv8s-seg (Segmentazione)
Per aggirare la mancanza del dataset COCO TFRecord locale richiesto da `hailomz compile`:
1. Esporta in ONNX via ultralytics:
   ```bash
   yolo export model=yolov8s-seg.pt format=onnx imgsz=640 opset=12
   ```
2. Converti in HAR:
   ```bash
   hailo parser onnx --hw-arch hailo10h --net-name yolov8s_seg yolov8s-seg.onnx
   ```
3. Ottimizza con calibrazione sintetica (bypassando l'errore JIT compiler CUDA in WSL):
   ```bash
   export CUDA_VISIBLE_DEVICES=""
   hailo optimize --hw-arch hailo10h --use-random-calib-set yolov8s_seg.har
   ```
4. Compila in HEF:
   ```bash
   hailo compiler --hw-arch hailo10h yolov8s_seg_optimized.har
   ```

##### B. SuperPoint (Odometria Visuale)
1. Esporta il modello in ONNX tramite lo script python dedicato.
2. Converti in HAR:
   ```bash
   hailo parser onnx --hw-arch hailo10h --net-name superpoint_128d superpoint_128d.onnx
   ```
3. Ottimizza (quantizzazione):
   ```bash
   export CUDA_VISIBLE_DEVICES=""
   hailo optimize --hw-arch hailo10h --use-random-calib-set superpoint_128d.har
   ```
4. Compila in HEF:
   ```bash
   hailo compiler --hw-arch hailo10h superpoint_128d_optimized.har
   ```

##### C. NetVLAD (Localizzazione Globale / VPR)
*Nota Critica: Il modello NetVLAD completo fallisce il parsing Hailo con errore `ValueError: width is not in list` nel nodo di reshape. Le operazioni di flattening ed L2-norm multidimensionali non sono supportate su NPU. L'architettura è stata divisa: il feature extractor gira su NPU, mentre il pooling gira in millisecondi sulla CPU dell'host.*

1. Esporta il solo Backbone (MobileNetV2 + 1x1 conv reducer) usando JIT trace e inibendo dynamo:
   ```bash
   # Esegui lo script export_netvlad.py (forzando dynamo=False)
   python3 scratch/export_netvlad.py
   ```
2. Converti il backbone in HAR:
   ```bash
   hailo parser onnx --hw-arch hailo10h --net-name netvlad_mobilenet_backbone netvlad_mobilenet_backbone.onnx
   ```
3. Ottimizza (quantizzazione):
   ```bash
   export CUDA_VISIBLE_DEVICES=""
   hailo optimize --hw-arch hailo10h --use-random-calib-set netvlad_mobilenet_backbone.har
   ```
4. Compila in HEF:
   ```bash
   hailo compiler --hw-arch hailo10h netvlad_mobilenet_backbone_optimized.har
   ```
   *Questo genera `netvlad_mobilenet_backbone.hef` che restituisce una costmap ridotta di `[1, 128, 7, 10]`. Il nodo ROS esegue il softmax ed i residui di pooling in CPU (<1ms).*


## Collegamenti a frammenti correlati

- `./weights/Marcus_architecture.md` — Descrizione dell'architettura neurale e logica di Marcus
- `./weights/lesson_learned.md` — Archivio delle lezioni apprese durante lo sviluppo qui ci sono tutte le lezioni imparate per non commettere gli stessi errori, se devi modificare un file leggi sempre prima questo file, avvisa se violi le regole di lesson_learned ed interrompiti, procedi solo dopo esplicito via libera dall'utente.

## Accesso Rapido a WSL, se servisse
Per collegarsi rapidamente all'ambiente di sviluppo WSL dall'host Windows, usa il comando:
```bash
ssh wsl
```