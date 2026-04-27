# Ristrutturazione Frame TF secondo Standard ROS

## 📋 Sommario delle Modifiche

Riorganizzazione completa della gerarchia dei frame TF del robot secondo gli standard ROS, con transizione da frame names non-standard OAK-D specifici a frame names universali ROS.

---

## 🎯 Nuova Gerarchia Frame (Standard ROS)

```
odom (globale)
  ↓
base_link (frame principale robot)
  ↓
camera_link (frame fisico camera)
  ↓
camera_optical_frame (frame ottico per visione - convenze visione computer)
  
base_link
  ↓
imu_link (frame IMU)
```

### Definizioni Frame

| Frame | Descrizione | Convenzione Assi |
|-------|-------------|-----------------|
| `odom` | Frame odometria globale (fisso) | XY piano terreno, Z verticale |
| `base_link` | Frame principale robot | X avanti, Y sinistra, Z alto (robotica) |
| `camera_link` | Frame fisico camera | X avanti, Y sinistra, Z alto (robotica) |
| `camera_optical_frame` | Frame ottico camera | X destra, Y basso, Z avanti (visione) |
| `imu_link` | Frame IMU (sul robot) | Generalmente coincide con base_link |

---

## 📝 Modifiche Apportate

### 1. **superpoint_node.py** - Nodo principale odometria

#### Cambiamenti frame names:
```python
# PRIMA (non-standard):
self.camera_frame = 'oak_mono_camera_frame'
self.camera_optical_frame = 'oak_mono_camera_optical_frame'
self.depth_frame = 'oak_depth_frame'
self.imu_frame = 'oak_imu_frame'

# DOPO (standard ROS):
self.base_frame = 'base_link'
self.camera_frame = 'camera_link'
self.camera_optical_frame = 'camera_optical_frame'
self.depth_frame = 'camera_optical_frame'  # Depth usa frame ottico
self.imu_frame = 'imu_link'
```

#### Disabilitazione publish_static_tf():
- Il metodo `publish_static_tf()` è stato disabilitato
- Le trasformazioni statiche vanno nel **launch file** per modularità e flessibilità
- Questo consente di cambiare posizione camera senza modificare il codice

#### Correzioni frame_id:
- ✅ Tutte le immagini e CameraInfo usano `camera_optical_frame`
- ✅ Odometry pubblica `odom → base_link`
- ✅ IMU pubblica con `imu_link`

---

### 2. **test_odometry_launch.py** - Launch file test odometria

Aggiunto corretto stack di TF statiche:

```python
# base_link → camera_link (posizione fisica)
Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    arguments=['0.1', '0', '0.15', '0', '0', '0', 'base_link', 'camera_link'],
)

# camera_link → camera_optical_frame (rotazione fissa)
# Rotazione: -90° attorno X, -90° attorno Z
Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    arguments=['0', '0', '0', '-1.5708', '0', '-1.5708', 'camera_link', 'camera_optical_frame'],
)

# base_link → imu_link
Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'imu_link'],
)
```

---

### 3. **test2_launch.py** - Launch file avanzato

- ✅ Rimossi parametri `camera_frame` e `camera_optical_frame` dai parametri nodo
- ✅ Aggiornate TF statiche da `oak_mount_link` → frame standard ROS
- ✅ Rimosso nodo `imu_correction` (non più necessario)

---

### 4. **IMU_oakd_node.py** - Nodo IMU

```python
# PRIMA:
self.declare_parameter('frame_id', 'oak_imu_frame')

# DOPO:
self.declare_parameter('frame_id', 'imu_link')  # Frame IMU standard ROS
```

---

### 5. **camera_info_publisher.py** - Publisher CameraInfo

```python
# PRIMA:
self.declare_parameter('frame_id', 'oak_mono_camera_frame')
self.declare_parameter('camera_name', 'oak_mono')

# DOPO:
self.declare_parameter('frame_id', 'camera_optical_frame')  # Frame ottico standard
self.declare_parameter('camera_name', 'camera')
```

---

## 🔄 Significato della Trasformazione camera_link ↔ camera_optical_frame

La trasformazione tra frame è **fissa** e rappresenta il cambio di convenzione:

- **camera_link** (Robotica): X=avanti, Y=sinistra, Z=alto
- **camera_optical_frame** (Visione): X=destra, Y=basso, Z=avanti

### Matrice Rotazione Equivalente:
```
R_camera_to_optical = [
    [0,  0,  1],   # X_optical = Z_camera
    [-1, 0,  0],   # Y_optical = -X_camera  
    [0, -1,  0]    # Z_optical = -Y_camera
]
```

### Parametri tf2_ros::static_transform_publisher (roll, pitch, yaw):
- roll = -π/2 (-1.5708 rad) = -90° attorno X
- pitch = 0
- yaw = -π/2 (-1.5708 rad) = -90° attorno Z

---

## ✅ Verifiche Standard ROS

Comando per visualizzare la gerarchia frame:
```bash
ros2 run tf2_tools view_frames
```

Dovrebbe mostrare:
```
odom → base_link → camera_link → camera_optical_frame
                ↘ imu_link
```

Comando per controllare una trasformazione specifica:
```bash
ros2 run tf2_ros tf2_echo base_link camera_optical_frame
```

---

## 🎯 Vantaggi della Riorganizzazione

1. **Modularità**: Posizione camera modificabile dal launch file senza cambiare codice
2. **Compatibilità**: Frame names standard ROS funzionano con RVIZ, TF tools, SLAM, etc.
3. **Chiarezza**: Ogni frame ha ruolo ben definito
4. **Debug**: `tf view_frames` mostra gerarchia pulita e comprensibile
5. **Best Practice**: Segue le convenzioni ROS ufficiali

---

## ⚠️ Note Importanti

- **Non pubblicare TF statiche nel codice**: Vanno nel launch file per flessibilità
- **Tutti i messaggi camera usano** `camera_optical_frame` (per vision)
- **Odometry pubblica** `odom → base_link` (movimento robot globale)
- **Posizione camera** modificabile in launch (traslazione 0.1m X, 0.15m Z)
- **IMU co-locato** con base_link (no offset - modifica se necessario)

---

## 📚 Riferimenti

- [ROS REP 103: Standard Conventions](http://www.ros.org/reps/rep-0103.html)
- [ROS REP 104: Coordinate Frames](http://www.ros.org/reps/rep-0104.html)
- [Camera Calibration - ROS](http://wiki.ros.org/camera_calibration)
- [TF2 Documentation](https://docs.ros.org/en/humble/Concepts/Intermediate/Tf2/Main.html)

---

**Data Modifica**: 15 Gennaio 2026  
**Versione**: 1.0
