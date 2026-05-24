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

prima di lanciare og

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

## Collegamenti a frammenti correlati

- `./weights/Marcus_architecture.md` — Descrizione dell'architettura neurale e logica di Marcus
- `./weights/lesson_learned.md` — Archivio delle lezioni apprese durante lo sviluppo qui ci sono tutte le lezioni imparate per non commettere gli stessi errori, se devi modificare un file leggi sempre prima questo file, avvisa se violi le regole di lesson_learned ed interrompiti, procedi solo dopo esplicito via libera dall'utente.

## Accesso Rapido a WSL, se servisse
Per collegarsi rapidamente all'ambiente di sviluppo WSL dall'host Windows, usa il comando:
```bash
ssh wsl
```