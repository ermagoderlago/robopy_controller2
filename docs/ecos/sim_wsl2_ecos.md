# ECO-2026-08-06-SIM: Ambiente WSL2 e Gazebo Harmonic

## Descrizione Modifica
È stato introdotto l'ambiente di simulazione Software-In-the-Loop (SIL) per il robot Marcus su WSL2 (Windows 10/11) utilizzando Gazebo Sim (Harmonic) in conformità con ROS 2 Jazzy.

## Constraint Architetturali e Hardware
Al fine di garantire il porting su Raspberry Pi 5 e mantenere un ambiente realistico:
1. **Risorse Limitate:** Tramite `.wslconfig` sono stati limitati i core vCPU a 4 e la RAM a 8GB, forzando gli algoritmi a operare sotto stress, prevenendo un falso senso di sicurezza che una workstation Windows x86_64 potrebbe fornire.
2. **DDS e Networking:** FastDDS/CycloneDDS tendono a saturare o droppare pacchetti su WSL2 in modalità NAT. È stato documentato (e parzialmente mitigato) l'utilizzo di `networkingMode=mirrored` su Windows 11 o `localhostForwarding` su Windows 10 per facilitare la comunicazione DDS, con un rischio tracciato in `fmea/dfmea.yaml` (FM-SIM-001).
3. **Mock NPU Hailo:** Poiché l'acceleratore Hailo-10H fisico è presente solo sul robot reale, è stato sviluppato un mock node (`hailo_mock_node.py`) per simulare latenze e bounding box fittizi, mantenendo l'infrastruttura ROS intatta.

## Componenti Aggiunti
- `robot_simulation` package.
- `robot_simulation/urdf/marcus_sim.xacro`
- `robot_simulation/worlds/test_arena.sdf`
- Plugin differenziale `gz-sim-diff-drive-system`.
- Plugin sensori per OAK-D Lite.
