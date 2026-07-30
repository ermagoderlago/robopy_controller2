# 🛠️ Progetto di Miglioramento IMP-NAV-009
> **Target Failure Mode:** `FM-NAV-009` (Caduta dalle Scale / Ostacoli Negativi)  
> **Priorità RPN:** 210 (Mitigato SW / REVISION_MANDATORY) | **Stato:** IN_PROGRESS | **Dominio:** Navigazione & Visione

---

## 1. Analisi del Problema & Workaround Provvisorio

### Problema
I sensori 3D (OAK-D Lite) e LiDAR 2D non proiettano punti nello spazio quando incontrano uno scalino in discesa o un vuoto (ostacolo negativo). La costmap di Nav2 interpreta l'assenza di punti come "spazio libero", esponendo Marcus al rischio di cadere dalle scale.

### Workaround Temporaneo (Prima dell'implementazione HW/SW)
- Definizione di **Keepout Zones (Zone Proibite Vettoriali)** statiche sul file di mappa `.yaml` di Nav2 in corrispondenza del perimetro delle scale in salotto.
- Limitazione della velocità massima della base mobile (`max_vel_x` = 0.2 m/s) nella stanza del salotto tramite filtro costmap.

---

## 2. Specifiche del Progetto di Miglioramento (Soluzione Definitiva)

### Modulo SW: `semantic_costmap_injector` (Node ROS 2 Python) - [COMPLETATO]
- **Funzione:** Analisi della matrice di profondità (Depth Image) prodotta dalla OAK-D Lite.
- **Algoritmo (Depth-Gradient Hole Raycasting):**
  1. Riceve `/camera/depth/image_raw` (16UC1 / 32FC1).
  2. Identifica celle dove la profondità misurata eccede di oltre $\Delta Z > 15\text{ cm}$ rispetto al piano terra teorico ($Z_{base} = 0.0\text{m}$).
  3. Inietta un ostacolo letale nella costmap locale a livello del bordo del dislivello tramite il topic `/hailo_semantic_obstacles_pc`.

### Modulo HW: Integrazione Cliff Sensors (ESP32) - [IN CORSO]
- **Funzione:** Override fisico a bassissima latenza.
- **Specifica:** 3 sensori ottici a infrarossi a corto raggio (Sharp GP2Y0A51SK0F o simili) installati sotto il paraurti anteriore e collegati ai pin GPIO di interrupt dell'ESP32.
- **Comportamento Interrupt:** Se un sensore legge $d > 10\text{ cm}$ (distacco dal suolo), l'ESP32 forza i pin PWM dei motori a 0 entro 10ms, indipendentemente dai comandi `/cmd_vel` di ROS 2.

---

## 3. Checklist dei Task di Sviluppo (Compartimentata)

- [x] **Task 1 (SW):** Implementare l'algoritmo Depth-Gradient Hole Raycasting in `semantic_costmap_injector.py`.
- [x] **Task 2 (SW):** Sottoscrivere `/camera/depth/image_raw` e pubblicare ostacoli letali su `/hailo_semantic_obstacles_pc`.
- [ ] **Task 3 (HW):** Configurare la routine di interrupt GPIO sull'ESP32 per il feedback dai 3 Cliff Sensor.
- [x] **Task 4 (Test):** Creare ed eseguire il test unitario `test/unit/test_negative_obstacle_injector.py` (OK).

---

## 4. Criteri di Accettazione & Validazione

- **Accettazione SW:** Il robot rileva uno scalino di 10cm da almeno 80cm di distanza e arresta la traiettoria prima del bordo. (Validato via `test_negative_obstacle_injector.py`)
- **Accettazione HW:** Se spinto manualmente verso il vuoto, i motori si bloccano istantaneamente al superamento del bordo.
