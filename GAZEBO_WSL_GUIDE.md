# 🚀 Guida Operativa Simulazione Robot Marcus su WSL2 (Gazebo & Foxglove)

Questa guida illustra passo-passo come avviare l'ambiente di simulazione su Windows 10 tramite WSL2, comandare i movimenti del robot differenziale, e visualizzarne lo stato, le telecamere e le detection dell'NPU tramite **Foxglove Studio** o **RViz2**.

---

## 1. Accesso all'Ambiente WSL2 e Preparazione

Apri un terminale **PowerShell** o **Windows Terminal** e accedi all'ambiente Ubuntu 24.04 (ROS 2 Jazzy):

```powershell
wsl -d Ubuntu-24.04
```

Una volta dentro WSL, spostati nel workspace ed imposta l'ambiente ROS 2:

```bash
cd "/mnt/c/Users/lsuffia/OneDrive - BRUGOLA OEB INDUSTRIALE SPA/Documents/robopy/antigravity"
source /opt/ros/jazzy/setup.bash
source install/setup.bash
export ROS_DOMAIN_ID=42
```

---

## 2. Avvio della Simulazione

### Opzione A: Simulazione SIL Nativa (Consigliata e Pronta all'Uso)
Esegue la cinematica differenziale a 50 Hz, il `robot_state_publisher`, il TF tree e il mock NPU Hailo senza dipendenze da binary esterni Gazebo:

```bash
ros2 launch robot_simulation mock_sim_bringup.launch.py
```

### Opzione B: Simulazione 3D Gazebo Sim (Harmonic) — Palestra Cognitiva Completa & Mondi Progressivi
*(Avvia Gazebo Harmonic con la casa multi-stanza `marcus_smart_home.sdf` o i mondi progressivi `world_01_simple_room.sdf`, `world_02_medium_apartment.sdf`, `world_03_extreme_labyrinth.sdf`, il bridge hardware ESP32 + 3S2P BMS, l'Emulatore NPU Hailo-10H, gli Attori Umani Virtuali Interattivi, il Valutatore Spaziale e la Shadow Memory Sandbox)*

Mondi SDF inclusi:
- `robot_simulation/worlds/world_01_simple_room.sdf`: Stanza quadrata 6x6m con ostacoli geometrici per test di allineamento e prima navigazione.
- `robot_simulation/worlds/world_02_medium_apartment.sdf`: Appartamento multi-vano con corridoio e porte strette (0.85m) per collaudo navigazione metrica.
- `robot_simulation/worlds/world_03_extreme_labyrinth.sdf`: Labirinto estremo ad alto grado di complessità con bivi, vicoli ciechi e trappole a U per stress test del planner e controller Nav2.
- `robot_simulation/worlds/marcus_smart_home.sdf`: Casa completa arredata con dislivello scale, docking station e attori umani interattivi.

```bash
cd /mnt/d/LUCA/robopy
# Avvio mondo Smart Home predefinito:
bash robot_simulation/scripts/start_gazebo_marcus.sh

# Oppure con mondo specifico a scelta:
bash robot_simulation/scripts/start_gazebo_marcus.sh --world robot_simulation/worlds/world_01_simple_room.sdf
```

> **Prima installazione su PC Windows (WSL2 Ubuntu 24.04):**
> 1. Da PowerShell (Amministratore): esegui `.\robot_simulation\scripts\configure_wsl2_host.bat`
> 2. Dentro Ubuntu-24.04: esegui `sudo bash /mnt/d/LUCA/robopy/robot_simulation/scripts/install_deps.sh /mnt/d/LUCA/robopy`

### Opzione C: Validazione Automatica Immediata (SIL / Pytest senza GUI)
Puoi eseguire i **25 test automatici** (Mondi Progressivi 3D, Hardware ESP32/BMS, Emulatore NPU Hailo-10H, Protezione Scale $>15\text{ cm}$, Missioni Chiusura ad Anello Smart Home Nav2, Curiosità, Dopamina RPE, Teoria della Mente e Istinti) direttamente da PowerShell su Windows in $< 1.5\text{ s}$:

```powershell
# Esecuzione istantanea tramite script batch:
.\run_sim_tests.bat

# Oppure tramite invocazione uv diretta:
& "$HOME\.local\bin\uv.exe" run --python 3.11 --with pytest --with numpy --with pyyaml --with httpx python -m pytest test/sim/ -v
```

---

## 2.5 Interagire con gli Umani Virtuali e Stimolare la Curiosità di Marcus

Quando la simulazione è attiva, puoi controllare gli attori umani virtuali (`human_luca`, `human_guest`) o far apparire oggetti insoliti per osservare la reazione autonoma di Marcus:

- **Spostare Luca nello Studio in modalità Lavoro Concentrato (`DO_NOT_DISTURB_DESK` -> inibisce dell'85% Curiosità/Socialità):**
  ```bash
  ros2 topic pub /sim/human_actor/command std_msgs/msg/String "{data: '{\"actor_id\": \"human_luca\", \"room\": \"studio\", \"posture\": \"DO_NOT_DISTURB_DESK\"}'}" --once
  ```
- **Far parlare Luca a Marcus:**
  ```bash
  ros2 topic pub /sim/human_actor/command std_msgs/msg/String "{data: '{\"actor_id\": \"human_luca\", \"say\": \"Ciao Marcus, vieni in salotto!\"}'}" --once
  ```
- **Iniettare un oggetto insolito (Sorpresa Spaziale 3D -> innesca la Curiosità Epistemica di Marcus):**
  ```bash
  ros2 topic pub /sim/environment/spawn_anomaly std_msgs/msg/String "{data: '{\"id\": \"valigia_misteriosa\", \"label\": \"suitcase\", \"x\": -1.8, \"y\": 1.1, \"z\": 0.35, \"surprise_score\": 0.85}'}" --once
  ```
- **Monitorare le Metriche Evolutive e i Colli di Bottiglia di Navigazione:**
  ```bash
  ros2 topic echo /sim/gym/metrics
  ```

---

## 3. Come Muovere il Robot Marcus

Apri un **secondo terminale WSL** (`wsl -d Ubuntu-24.04`), fai il setup dell'ambiente:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
```

### Metodo 1: Teleoperazione da Tastiera (Consigliato)
Esegui il nodo standard di teleoperazione:

```bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```

Controlli da tastiera:
- **`i`**: Avanza diritto
- **`,`**: Retromarcia
- **`j`**: Ruota a sinistra su se stesso
- **`l`**: Ruota a destra su se stesso
- **`u` / `o`**: Curva avanti a sinistra / destra
- **`k`**: STOP immediato (arresta i motori)
- **`q` / `z`**: Aumenta / Riduci la velocità lineare del 10%
- **`w` / `x`**: Aumenta / Riduci la velocità angolare del 10%

### Metodo 2: Invio di Comandi Diretti via ROS 2 Topic

Per inviare un impulso di movimento (es. traslazione avanti a $0.2 \text{ m/s}$ e rotazione a $0.1 \text{ rad/s}$):

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.2, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.1}}" -r 10
```

Per arrestare il robot:

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}" --once
```

---

## 4. Visualizzazione con Foxglove Studio (Consigliato su Windows)

**Foxglove Studio** permette di visualizzare il robot in 3D, gli stream video e controllare il moto direttamente da una dashboard moderna su Windows.

### Passo 1: Avviare il WebSocket Bridge su WSL
In un terminale WSL dedicato, avvia `foxglove_bridge`:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
ros2 run foxglove_bridge foxglove_bridge
```
*(Nota: l'eseguibile corretto è `foxglove_bridge` senza `_node`).*

### Passo 2: Connettere Foxglove Studio
1. Apri **Foxglove Studio** su Windows (scaricabile da [foxglove.dev](https://foxglove.dev) o accessibile dal browser su `https://studio.foxglove.dev`).
2. Clicca su **Open connection**.
3. Seleziona **Foxglove WebSocket**.
4. Inserisci l'URL: `ws://localhost:8765` e clicca **Connect**.

### Passo 3: Configurare i Pannelli di Foxglove
Una volta connesso:
1. **Pannello 3D:**
   - Aggiungi un pannello **3D**.
   - Imposta il Frame di Riferimento su `odom`.
   - Attiva le spunte per **Robot Model** (pubblicato su `/robot_description`) e **Transforms (TF)** (`odom` $\to$ `base_link` $\to$ `camera_link`).
2. **Pannello Telecamera / Visione:**
   - Aggiungi un pannello **Image**.
   - Seleziona il topic `/camera/color/image_raw`.
3. **Pannello Teleoperazione (Joystick):**
   - Aggiungi un pannello **Teleop**.
   - Mappa il topic target su `/cmd_vel`. Ora potrai muovere il robot trascinando il joystick virtuale con il mouse!
4. **Pannello NPU Bounding Box:**
   - Aggiungi un pannello **Raw Messages** o **Plot** ascoltando su `/hailo_detections` per osservare in tempo reale le detection e la latenza ($50\text{ ms}$).

---

## 5. Visualizzazione Nativa con RViz2

Se preferisci usare **RViz2** nativamente dentro WSL:

In un terminale WSL:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
rviz2
```

In RViz2:
1. In alto a sinistra, imposta **Global Options $\to$ Fixed Frame** su `odom`.
2. Clicca su **Add** (in basso a sinistra):
   - Aggiungi **RobotModel** (Topic `/robot_description`).
   - Aggiungi **TF** per vedere l'albero delle trasformate in tempo reale.
   - Aggiungi **Image** impostando il topic `/camera/color/image_raw`.

---

## 6. Diagnostica e Ispezione dei Topic

Da terminale WSL puoi verificare la frequenza ed il contenuto dei dati generati dalla simulazione:

- **Verifica Frequenza Odometria (attesa: 50 Hz):**
  ```bash
  ros2 topic hz /odom
  ```
- **Lettura delle Pose Odometriche:**
  ```bash
  ros2 topic echo /odom
  ```
- **Verifica dell'albero TF (distanza e orientamento `odom` $\to$ `base_link`):**
  ```bash
  ros2 run tf2_ros tf2_echo odom base_link
  ```
- **Ispezione delle Detections Simulate Hailo NPU:**
  ```bash
  ros2 topic echo /hailo_detections
  ```

---

## 💡 Risoluzione Problemi Rapida

- **Nome eseguibile Foxglove Bridge:**
  Il comando esatto è `ros2 run foxglove_bridge foxglove_bridge` (senza `_node` finale).
- **Package `ros_gz_sim` not found:**
  Se non sono installati i pacchetti grafici 3D di Gazebo su WSL, utilizza la simulazione nativa `mock_sim_bringup.launch.py` che fornisce tutto l'ambiente SIL (Odometria 50 Hz, TF tree, camera e mock NPU) senza richiedere pacchetti binari esterni.
- **Se i comandi non hanno effetto:**
  Assicurati che **tutti** i terminali aperti abbiano eseguito `export ROS_DOMAIN_ID=42`.
