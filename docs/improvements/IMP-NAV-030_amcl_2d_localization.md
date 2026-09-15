# IMP-NAV-030: Best Practice AMCL 2D e Map Server per Localizzazione ad Alta Frequenza (10Hz) su Mappe Note

## 1. Identificazione e Contesto
- **ID Progetto:** `IMP-NAV-030`
- **DFMEA Associato:** `FM-NAV-030` (Mancato allineamento mappa-scansione e divergenza ICP globale in navigazione su mappa nota)
- **Dominio:** `nav2_slam`
- **Componenti Coinvolti:** `nav2_amcl`, `nav2_map_server`, `custom_nav2_launch.py`, `nav2_params_jazzy.yaml`, `rtabmap.yaml`, `waveshare_motor_driver.py`, `restart_hailo.sh`, `scripts/save_map.sh`
- **Data Attivazione:** 15 Settembre 2026
- **Stato:** COMPLETED

---

## 2. Descrizione del Guasto e Causa Radice
Durante la navigazione autonoma con Nav2 in ambienti già cartografati, il robot fisico presentava frequentemente uno sdoppiamento delle pareti sulla mappa o uno stallo nell'allineamento tra la scansione LiDAR 2D a 360° (Slamtec RPLIDAR C1) e la mappa d'occupabilità (`/map`), sebbene le letture laser fossero nitide e prive di rumore.

### Cause Radice Identificate:
1. **Discrepanza di Dinamica Temporale (1.0 Hz vs Deriva in Curva):** RTAB-Map esegue il campionamento a 1.0 Hz per proteggere la CPU a 4 core del Raspberry Pi 5. Durante una rotazione a $0.5\,\text{rad/s}$ ($28^\circ/\text{s}$), un micro-slittamento delle ruote differenziali accumula $8^\circ \div 12^\circ$ di errore in 1 secondo. A 3.5 metri di distanza, questo scostamento corrisponde a $>60\,\text{cm}$, superando il raggio massimo di corrispondenza ICP (`Icp/MaxCorrespondenceDistance: 0.35m`).
2. **Natura Deterministica All-or-Nothing dell'ICP:** Se un frame fallisce l'allineamento ICP, RTAB-Map non aggiorna $\mathbf{T}_{\text{map}\to\text{odom}}$. Al secondo successivo l'errore è raddoppiato, inducendo il fallimento a cascata permanente.
3. **Covarianza Odometrica Troppo Rigida (`1e-5`):** In `waveshare_motor_driver.py`, la covarianza sulla posa in movimento dichiarava un'incertezza irrealisticamente bassa ($\sim 0.17^\circ$), portando l'ottimizzatore a grafo GTSAM a rifiutare le correzioni angolari del laser ICP come outlier.
4. **Collo di Bottiglia `approx_sync`:** La sincronizzazione a 4 topic (RGB, Depth, CameraInfo, Scan) rallentava il rate effettivo del laser a causa di latenze sporadiche della telecamera.
5. **Memoria Incrementale in Navigazione:** RTAB-Map con `IncrementalMemory: true` creava continuamente nuovi nodi odometrici non agganciati, generando pareti fantasma a stella.

---

## 3. Soluzione Architetturale (Opzione A - Best Practice Nav2)

L'architettura adottata disaccoppia la localizzazione planare ad alta frequenza (AMCL 2D) dalla percezione 3D e semantica (RTAB-Map):

```mermaid
graph TD
    LIDAR["RPLIDAR C1 (10 Hz)"] -->|/scan| AMCL["nav2_amcl (Particle Filter @ 10Hz)"]
    WHEEL["Odometria Ruote (Covarianza yaw=0.02)"] -->|/odom| AMCL
    MAP_FILE["Mappa Statica (/mnt/ssd/maps/salotto.yaml)"] --> MAP_SRV["nav2_map_server"]
    MAP_SRV -->|/map| AMCL
    MAP_SRV -->|/map| NAV2["Nav2 Costmap & Planners"]
    
    AMCL -->|map -> odom (10 Hz Continuo)| NAV2
    
    RGBD["OAK-D Lite + Hailo-10H"] --> RTAB["RTAB-Map (publish_tf=false, Mem/IncrementalMemory=false)"]
    RTAB -->|Ostacoli Negativi & TRINITY Semantica| NAV2
```

### Dettaglio Interventi Implementati:
1. **`robopy_controller/config/nav2_params_jazzy.yaml`:**
   - Inserita configurazione completa di `map_server` (lettura file YAML da `/mnt/ssd/maps/salotto.yaml`).
   - Inserita configurazione di `amcl` ottimizzata per Pi 5:
     - Modello cinematico: `nav2_amcl::DifferentialMotionModel`
     - Modello sensoriale: `laser_model_type: "likelihood_field"`, max 60 raggi
     - Particelle: 300 min, 1500 max con KLD-sampling
     - Aggiornamento dinamico: `update_min_d: 0.08m`, `update_min_a: 0.08rad` (~4.6°)
     - Recovery anti-kidnap: `recovery_alpha_slow: 0.001`, `recovery_alpha_fast: 0.1`
2. **`launch/custom_nav2_launch.py`:**
   - Aggiunti argomenti `enable_amcl` (default: `False`) e `map` (default: `/mnt/ssd/maps/salotto.yaml`).
   - Inseriti i nodi `map_server` e `amcl` condizionali a `enable_amcl`, coordinati da un `lifecycle_manager_localization` dedicato per garantire transizioni di stato pulite e indipendenti da SLAM.
3. **`robopy_controller/nodes/waveshare_motor_driver.py`:**
   - Calibrata la matrice di covarianza dinamica in moto: incertezza lineare $10^{-4}\,\text{m}^2$, incertezza angolare $\text{yaw} = 0.02\,\text{rad}^2$ ($\sigma \approx 8^\circ$).
4. **`restart_hailo.sh`:**
   - Aggiunto supporto a `--amcl` e `--map=<file>`.
   - In modalità `--amcl`: RTAB-Map viene avviato con `-p publish_tf:=false -p Mem/IncrementalMemory:=false` nel rispetto assoluto di REP-105 (nessun doppio publisher su `map -> odom`).
   - Nav2 viene avviato con `enable_amcl:=true map:=<file>`.
5. **`scripts/save_map.sh`:**
   - Script eseguibile one-touch per esportare `/map` in `.yaml` + `.pgm` via `nav2_map_server map_saver_cli`.

---

## 4. Verifica e Collaudo
- **Test Unitario:** `test/unit/test_amcl_nav2_integration.py`
  - Validata coerenza parametri AMCL e Map Server in YAML.
  - Verificata la corretta segregazione dei nodi lifecycle.
  - Verificata la corretta covarianza angolare nel driver ruote.
- **DFMEA RPN Reduction:**
  - Punteggio Iniziale: $S=8, O=8, D=4 \Rightarrow \mathbf{RPN = 256}$
  - Punteggio Residuo: $S=8, O=1, D=1 \Rightarrow \mathbf{RPN = 8}$ (-97% rischio)
