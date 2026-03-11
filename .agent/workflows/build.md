---
description: Build the robopy_controller ROS 2 package
---

// turbo-all

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
2. Aggiungilo a `console_scripts` in `setup.py` (male non fa).
3. Crea uno script eseguibile "wrapper" nella cartella `scripts/` (es. `scripts/mio_nuovo_nodo` *senza* estensione `.py`) che importa ed esegue il tuo `main()`. Esempio:
   ```python
   #!/usr/bin/env python3
   import sys
   from robopy_controller.nodes.mio_nuovo_nodo import main
   if __name__ == '__main__':
       sys.exit(main())
   ```
4. Rendi lo script eseguibile: `chmod +x scripts/mio_nuovo_nodo`
5. Registralo nel `CMakeLists.txt` aggiungendo la riga `scripts/mio_nuovo_nodo` nel blocco esistente `install(PROGRAMS ... DESTINATION lib/${PROJECT_NAME})`!


ATTENZIONE MAI CANCELLARE I FILE COMPILATI DI ROS2 NELLA CARTELLA  ~/ros2_jazzy!!!!   MAI!!! ATTENZIONE