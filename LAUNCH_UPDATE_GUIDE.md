# 📋 Checklist Aggiornamento Launch File

Questa guida aiuta ad aggiornare i rimanenti launch file per usare la nuova struttura frame TF.

---

## 🎯 Launch File da Controllare

| Launch File | Status | Azioni |
|------------|--------|--------|
| `test_odometry_launch.py` | ✅ AGGIORNATO | Nessuna |
| `test2_launch.py` | ✅ AGGIORNATO | Nessuna |
| `robopy_launch.py` | ⚠️ VERIFICA | Vedi sotto |
| `robopy_stable_launch.py` | ⚠️ VERIFICA | Vedi sotto |
| `robopy_mapping_launch.py` | ⚠️ VERIFICA | Vedi sotto |
| `full_robot_launch.py` | ⚠️ VERIFICA | Vedi sotto |
| Altro | ❓ | Vedi sotto |

---

## 🔍 Come Aggiornare un Launch File

### Step 1: Cercare Vecchi Frame Names

Aprire il launch file e cercare:
```bash
grep -n "oak_mono\|oak_depth\|oak_imu" <launch_file.py>
```

Se trova risultati, proseguire ai step successivi.

### Step 2: Cercare Hardcoded Frame ID nei Parametri

Cercare linee come:
```python
'camera_frame': 'oak_mono_camera_frame',
'camera_optical_frame': 'oak_mono_camera_optical_frame',
```

**Azione**: Rimuoverle (il nodo usa ora frame standard)

### Step 3: Cercare Static Transform Publishers

Cercare nodi `static_transform_publisher` che usano vecchi frame names.

**Azione**: Sostituire con struttura corretta:

```python
# ✅ CORRETTO - Nuova struttura
Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    arguments=['0.1', '0', '0.15', '0', '0', '0', 'base_link', 'camera_link'],
),
Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    arguments=['0', '0', '0', '-1.5708', '0', '-1.5708', 'camera_link', 'camera_optical_frame'],
),
Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'imu_link'],
),
```

### Step 4: Verificare Remappings

Se presenti remappings su topics che dipendono da frame (rare), verificare che usino i nuovi frame names nei topic names (se applicabile).

### Step 5: Testare

```bash
# 1. Lanciare il file
ros2 launch robopy_controller <launch_file.py>

# 2. In altro terminale - verificare frame hierarchy
ros2 run tf2_tools view_frames

# 3. Verificare TF
ros2 run tf2_ros tf2_echo base_link camera_optical_frame
```

---

## 📝 Esempio Modifica Completa

### PRIMA:
```python
def generate_launch_description():
    nodes = [
        Node(
            package='robopy_controller',
            executable='superpoint_node',
            parameters=[{
                'camera_frame': 'oak_mono_camera_frame',
                'camera_optical_frame': 'oak_mono_camera_optical_frame',
            }]
        ),
        
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0.1', '0', '0.15', '0', '0', '0', 'base_link', 'oak_mono_camera_frame'],
        ),
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0.1', '0', '0.15', '0', '0', '0', 'base_link', 'oak_depth_frame'],
        ),
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0.1', '0', '0.15', '0', '0', '0', 'base_link', 'oak_imu_frame'],
        ),
    ]
```

### DOPO:
```python
def generate_launch_description():
    nodes = [
        Node(
            package='robopy_controller',
            executable='superpoint_node',
            # ✅ Rimossi parametri camera_frame - nodo usa frame standard
            parameters=[{
                # ... altri parametri ...
            }]
        ),
        
        # ✅ TF statiche nuove - frame standard ROS
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0.1', '0', '0.15', '0', '0', '0', 'base_link', 'camera_link'],
        ),
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0', '0', '0', '-1.5708', '0', '-1.5708', 'camera_link', 'camera_optical_frame'],
        ),
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            arguments=['0', '0', '0', '0', '0', '0', 'base_link', 'imu_link'],
        ),
    ]
```

---

## 🔧 Parametri da Considerare

Alcuni launch file potrebbero avere parametri di posizione camera:

```python
# Se presenti, modificare così:
camera_x = 0.1      # distanza avanti (m)
camera_y = 0.0      # distanza laterale (m)
camera_z = 0.15     # distanza verticale (m)

# Uso in TF:
Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    arguments=[str(camera_x), str(camera_y), str(camera_z), 
               '0', '0', '0', 'base_link', 'camera_link'],
),
```

---

## ✅ Validazione Post-Modifica

Dopo aver aggiornato un launch file:

1. **Sintassi Python**: 
   ```bash
   python3 -m py_compile <launch_file.py>
   ```

2. **Launch**:
   ```bash
   ros2 launch robopy_controller <launch_file.py>
   ```

3. **TF Hierarchy**:
   ```bash
   ros2 run tf2_tools view_frames
   ```

4. **Odometria** (se il nodo pubblica odom):
   ```bash
   ros2 topic echo /odom/header --field frame_id -n 1
   ros2 topic echo /odom --field child_frame_id -n 1
   ```

---

## 🚀 Configurazione Finale Consigliata

Una volta aggiornato un launch file, il suo stack di frame dovrebbe essere:

```
odom
  └─ base_link
      ├─ camera_link (X=0.1, Y=0, Z=0.15m)
      │   └─ camera_optical_frame (Rotazione: -90°X, -90°Z)
      └─ imu_link (X=0, Y=0, Z=0m - coincide con base_link)
```

---

## 📞 Troubleshooting

### Errore: "Frame 'camera_optical_frame' non trovato"
**Causa**: TF statica camera_link → camera_optical_frame non pubblicata
**Soluzione**: Verificare che il nodo sia presente nel launch

### Errore: "Transform too old"
**Causa**: Nodi TF statici non avviati prima del nodo odometry
**Soluzione**: Aggiungere `TimerAction` per ritardare odometry

Esempio:
```python
from launch.actions import TimerAction

TimerAction(
    period=2.0,  # Attendi 2 secondi
    actions=[Node(...)]  # Nodo da ritardare
)
```

### Image frame_id sbagliato
**Causa**: CameraInfo publisher usa vecchio frame
**Soluzione**: Verificare parametri launch per camera_info_publisher

---

## 📋 Passo Dopo Passo per Principianti

1. Apri il launch file con un editor
2. Cerca "oak_" usando Ctrl+F
3. Per ogni occorrenza:
   - Se è un parametro `camera_frame`: **Rimuovi la riga**
   - Se è in `static_transform_publisher`: **Sostituisci con struttura nuova**
4. Salva il file
5. Testa con `ros2 launch`
6. Controlla con `ros2 run tf2_tools view_frames`

---

**Nota**: Consultare [TF_RESTRUCTURE_SUMMARY.md](TF_RESTRUCTURE_SUMMARY.md) per documentazione completa.

**Ultimo aggiornamento**: 15 Gennaio 2026
