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

### Opzione B: Simulazione 3D Gazebo Sim (Harmonic)
*(Richiede i pacchetti `ros-jazzy-ros-gz` installati su WSL)*

```bash
export LIBGL_ALWAYS_SOFTWARE=1
ros2 launch robot_simulation sim_bringup.launch.py
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
