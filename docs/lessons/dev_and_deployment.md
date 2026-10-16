# Lezioni Apprese - Sviluppo, Build & Deployment

Questo documento raccoglie le linee guida operative, le ricette di build e le lezioni apprese in merito all'ambiente di sviluppo e deploy di Marcus.

---

## 🛠️ Compilazione ROS 2 e Limiti Hardware Pi 5

### Crash del Compilatore per OOM (Out-of-Memory)
* **Problema:** Compilando il pacchetto `robopy_controller` sul Raspberry Pi 5 usando il parallelismo di default di `colcon build`, la memoria RAM si esaurisce costringendo il sistema a killare il processo `clang++` (conseguente corruzione della directory `install/`).
* **Risoluzione:** Eseguire tassativamente la compilazione in modalità sequenziale limitando a 1 i thread di build ed i worker simultanei:
  ```bash
  MAKEFLAGS="-j1" colcon build --parallel-workers 1 --packages-select robopy_controller
  ```

### Isolamento degli Ambienti Virtuali (ROS vs Python)
* **Regola Permanente:** La compilazione di ROS 2 Core (`~/ros2_jazzy`) deve avvenire sempre nell'ambiente di sistema base. Non attivare mai `ros2env` o altri venv durante la compilazione del core ROS.
* L'esecuzione dei nodi del robot (`robopy_controller`), invece, richiede l'attivazione dell'ambiente virtuale dedicato:
  ```bash
  source ~/ros2env/bin/activate
  ```

---

## 🔄 Sincronizzazione Workspace e Hot-Swap

### Sincronizzazione Windows-Pi
* Durante lo sviluppo locale su PC Windows, le modifiche possono essere sincronizzate rapidamente senza rebuild intere sul robot tramite lo script:
  ```powershell
  .\sync_marcus.bat
  ```
  Questo script propaga i file Python modificati sia in `src/` che direttamente nei rispettivi percorsi in `install/` sul robot.

### Riavvio dei Nodi e Interlock del Watchdog
* Per riavviare i nodi sul robot, eseguire:
  ```bash
  bash /mnt/ssd/robopy_controller_host/restart.sh
  ```
* **Conflitto e Race Condition del Watchdog (FM-SYS-007):** Lo script/servizio `watchdog.sh` (o `marcus-watchdog.service`) monitora continuamente il processo `robot_ai_node`. Se l'operatore arresta o riavvia manualmente lo stack, l'avvio temporizzato di ROS 2 (sleep di 15-30s per inizializzazione camera e SLAM) viene scambiato dal watchdog per un crash. Il loop a 5s del watchdog lancia quindi ricorsivamente multiple istanze concorrenti di `restart_hailo.sh`, causando la moltiplicazione dei nodi (es. 8+ istanze di `robot_health_supervisor`), la saturazione dei 4 core CPU al 100% (Load Average > 35) ed il freeze del sistema.
* **Soluzione Interlock a Triplo Livello:** 
  1. Lo script `restart_hailo.sh` controlla la variabile `FROM_WATCHDOG`. Se vuota (riavvio manuale), esegue preventivamente `sudo systemctl stop marcus-watchdog.service` e `systemctl --user stop marcus-watchdog.service`.
  2. Per eliminare istanze standalone disaccoppiate da systemd, viene eseguito `pkill -9 -f watchdog.sh` all'inizio dello script di startup prima dello spawn dei nuovi nodi.
  3. Al termine dell'inizializzazione sequenziale, il watchdog viene riattivato in sicurezza.

---

## 📄 Incompatibilità dei Formati e Credenziali

### Incompatibilità Fine Riga (CRLF vs LF)
* **Problema:** Gli script shell (`.sh`) e i file Python con shebang (`#!/usr/bin/env python3`) modificati o creati su Windows vengono salvati con fine riga `\r\n` (CRLF). All'avvio su Linux, i nodi falliscono istantaneamente con:
  `/usr/bin/env: ‘python3\r’: No such file or directory` (exit code 127).
* **Risoluzione:** Sanare tassativamente con `sed -i 's/\r$//' <file>` sia nei sorgenti che nei binari installati sotto `install/robopy_controller/lib/robopy_controller/`.

### Limite Partecipanti CycloneDDS su Dominio 42 (FM-DDS-007)
* **Problema:** Con oltre 30 nodi attivi sul Pi 5, i comandi CLI (`ros2 topic echo`, `ros2 topic hz`, `ros2 node info`) falliscono con:
  `Failed to find a free participant index for domain 42` / `rmw_create_node: failed to create domain`.
* **Causa:** Il file CycloneDDS di default limita l'allocazione automatica di porte RTPS a 32 partecipanti.
* **Risoluzione:** Esportare sempre la configurazione estesa generata all'avvio:
  ```bash
  export ROS_DOMAIN_ID=42
  export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
  export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
  ```

### Credenziali Google ADC vs Gemini API Key
* **Problema:** Errore `Your default credentials were not found` sui servizi ASR/TTS.
* **Causa:** I modelli Gemini Pro utilizzano `GEMINI_API_KEY`, mentre i servizi professionali Google Cloud (Text-to-Speech e Speech-to-Text) richiedono obbligatoriamente le Application Default Credentials (ADC) tramite JSON di Service Account.
* **Risoluzione:** Senza ADC, il TTS Google fallirà. Configurare il percorso del JSON in `GOOGLE_APPLICATION_CREDENTIALS` nel file `setup_keys.sh` per attivare i servizi professionali, oppure forzare il fallback all'API Key di Gemini (se supportato dalle ultime versioni del client).

---

## 📡 Convenzioni QoS ROS 2 (BEST_EFFORT vs RELIABLE - FM-DDS-006)

* **Problema:** L'utilizzo del profilo QoS `BEST_EFFORT` sui publisher video/annotazioni visive (es. `/hailo/annotated_image/compressed`) o su topic di stato causa una perdita silenziosa del flusso dati su Foxglove Studio, Foxglove Bridge o la Web UI. I client di visualizzazione sottoscrivono di default con profilo `RELIABLE` e scartano automaticamente i pacchetti ricevuti via `BEST_EFFORT`.
* **Lezione Appresa & Regola Permanente di Architettura:**
  1. **Topic Visivi e Bridge (Foxglove/Web UI):** Tutti i publisher di immagini, annotazioni visive (YOLO/Volti/Semantica) e telemetria destinati a visualizzatori esterni DEVONO utilizzare la garanzia `qos_reliable` (o `10` depth queue).
  2. **Topic Audio a Bassa Latenza:** Solo i flussi di audio raw PCM a 16kHz fra nodi interni che richiedono latenza minima in real-time possono utilizzare `BEST_EFFORT`.
  3. **Ispezione Incompatibilità:** Durante i test di integrazione, verificare sempre la compatibilità QoS dei nodi con:
     ```bash
     ros2 topic info /hailo/annotated_image/compressed --verbose
     ```

---

## 🔒 Esecuzione Script in Background e Compatibilità Sudo (FM-SYS-009)

* **Problema:** L'invocazione di comandi con privilegi (`sudo systemctl ...`) all'interno di script di avvio lanciati in background o tramite agenti headless (`nohup ... &` o subshell non interattive senza TTY) provoca il blocco asintotico del processo se sudo richiede la password, congelando l'intero script prima dello spawn dei nodi successivi.
* **Risoluzione & Regola Permanente:**
  1. Negli script di orchestrazione (`restart_hailo.sh`), usare tassativamente il flag **`-n`** (non-interactive):
     ```bash
     sudo -n systemctl stop marcus-watchdog.service 2>/dev/null || true
     ```
  2. Prevedere bypass basati su variabili d'ambiente (es. `FROM_WATCHDOG=1`) per evitare chiamate privilegiate ridondanti durante riavvii automatici o remoti.

---

## 🐍 Risoluzione Percorsi Virtualenv vs Python di Sistema (FM-SYS-010)

* **Problema:** I nodi eseguiti con il comando `ros2 run <package> <node>` impiegano di default l'interprete `/usr/bin/python3` del sistema operativo host, privo dei pacchetti installati nella virtualenv utente (`~/ros2_venv/`). Moduli come `vosk`, `depthai` o librerie scientifiche compilate falliscono l'importazione con `ModuleNotFoundError` o vengono disabilitati da blocchi `try ... except ImportError:` silenziosi.
* **Risoluzione:** I moduli che dipendono da librerie situate in virtualenv isolate devono includere nei percorsi di import un meccanismo di iniezione difensiva:
  ```python
  for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages", "/home/robopy/ros2_venv/lib/python3.12/site-packages"]:
      if os.path.exists(p) and p not in sys.path:
          sys.path.append(p)
  ```
  Questo garantisce l'esecuzione trasparente sia all'interno che all'esterno di virtualenv attive.

---

## 🌐 Monitoring ROS 2 via SSH — ROS_DOMAIN_ID=42 e CycloneDDS (FM-DDS-007)

> [!IMPORTANT]
> **Lezione critica appresa il 2026-09-23.** Questa sezione va letta prima di qualsiasi sessione di monitoring remoto su Marcus. L'errore `Failed to find a free participant index for domain 42` ha già causato perdita di tempo in più sessioni.

### Problema Ricorrente

Marcus gira con **`ROS_DOMAIN_ID=42`** e **`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`**. Con ~38 nodi attivi, il limite default di CycloneDDS (32 partecipanti) è già saturo. Le sessioni SSH nuove non ereditano le variabili d'ambiente di `restart_hailo.sh`, quindi:

- `ros2 node list` / `ros2 topic list` → **funzionano** (usano il daemon già attivo)
- `ros2 topic hz /scan` / `ros2 topic echo /battery_state` → **FALLISCONO** con:
  ```
  Failed to find a free participant index for domain 42
  rmw_create_node: failed to create domain, error Error
  ```
  perché questi comandi creano un **nuovo** nodo ROS 2 (subscriber/partecipante DDS) e il limite di 32 è già pieno.

Il file `/tmp/cyclonedds_robopy.xml` (generato da `restart_hailo.sh`) alza il limite a 200, ma solo i processi avviati con `CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml` lo vedono.

### Soluzione: Prefisso Obbligatorio

Ogni comando SSH che crea nuovi nodi ROS 2 deve includere:
```bash
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash
```

In WSL/PowerShell riga singola:
```
wsl ssh robopy@marcus "export ROS_DOMAIN_ID=42; export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml; source /home/robopy/ros2_jazzy/install/setup.bash; source /mnt/ssd/robopy_controller_host/install/setup.bash; <COMANDO>"
```

### Comandi di Monitoring Verificati (con CYCLONEDDS_URI)

| Comando | Freq. Attesa | Note |
|---|---|---|
| `timeout 5 ros2 topic hz /scan` | ~10 Hz | LiDAR RPLiDAR A1/A2 |
| `timeout 4 ros2 topic hz /odom` | ~20 Hz | Encoder WaveShare |
| `timeout 5 ros2 topic echo /battery_state --once` | — | voltage ~12.6V full |
| `timeout 5 ros2 topic echo /amcl_pose --once` | — | x, y localizzazione |
| `timeout 5 ros2 topic echo /diagnostics --once` | — | battery, motor_stall |
| `timeout 3 ros2 topic hz /ultrasonic_range` | ~5-10 Hz | sensore HC-SR04 |

### Fix Permanente Consigliato

Aggiungere a `/home/robopy/.bashrc` sul robot (da fare una volta sola):
```bash
# ROS 2 Marcus — environment permanente
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null
```
⚠️ `/tmp/cyclonedds_robopy.xml` viene ricreato da `restart_hailo.sh` a ogni avvio — non è un problema se il file non esiste al boot (CycloneDDS userà il default con limit=32 solo per la breve finestra prima dell'avvio dello stack).

---

## ⚡ Prevenzione Brownout Pi 5 & Ottimizzazione CPU con Foxglove (FM-PWR-002, FM-SYS-011)

### Sintomo & Causa Radice
* **Sintomo:** Connettendo Foxglove Studio o in condizioni idle prolungate con ~38 nodi attivi, la CPU schizzava al 99-100% (Load Average > 12.0), innescando un picco di assorbimento sul rail 5V step-down del Raspberry Pi 5. Il PMIC DA9091 interveniva con un hard shutdown di protezione da brownout (< 4.63V), arrestando il robot e disconnettendo la rete.
* **Causa Radice:**
  1. **Python GIL Contention su IMU a 200 Hz:** Multipli nodi Python (`sensor_standby_manager`, `robot_health_supervisor`, `waveshare_motor_driver`) sottoscrivevano l'IMU OAK-D a 200 Hz. Ciascuna callback risvegliava i thread Python 200 volte/sec anche a robot fermo, bloccando il Global Interpreter Lock (GIL).
  2. **Doppio Ciclo Python in Raycasting Profondità:** `semantic_costmap_injector.py` eseguiva un loop annidato `for y in ... for x in ...` su una griglia di profondità ($32 \times 32 = 1024$ iterazioni) ogni frame, consumando oltre il 58% di CPU con tempi di 150 ms per frame.
  3. **Packet Storm WiFi DDS:** CycloneDDS inviava i pacchetti multicast tra i 38 nodi interni sull'interfaccia WiFi (`wlan0`), saturando il router WiFi e sovraccaricando il kernel Linux di interrupt di rete.
  4. **Frequenza CPU 2.4 GHz non vincolata:** In assenza di governor cap, tutti e 4 i core Cortex-A76 salivano a 2.4 GHz durante le richieste di layout/parametri di Foxglove, superando l'erogazione istantanea dello step-down a 5V.

### Soluzione Architetturale Implementata
1. **Isolamento CycloneDDS su Loopback (`lo`):** Configurato `<NetworkInterfaceAddress>lo</NetworkInterfaceAddress>` in `/tmp/cyclonedds_robopy.xml` generato da `restart_hailo.sh`. Il traffico interno dei nodi resta al 100% confinato nella RAM del kernel.
2. **Frequenza Massima CPU Pi 5 Calibrata a 2.0 GHz (Sweet Spot Energetico):** Inizialmente sbloccata a 2.4 GHz a seguito dell'introduzione del modulo BEC droni, i test di endurance a pieno carico AI (YOLO NPU a 8.5 Hz + OAK-D Lite USB 3.0 + Nav2) hanno evidenziato ricorrenti allarmi di undervoltage del PMIC DA9091 (`[hwmon3] Undervoltage detected!` < 4.63V) con rischio di hard brownout cutoff. La frequenza massima è stata quindi tarata stabilmente a **2.0 GHz** (`TARGET_CPU_FREQ="2000000"` in `restart_hailo.sh`). A 2.0 GHz, la potenza dinamica $P \propto V^2 f$ si riduce drasticamente, azzerando le cadute di tensione sul rail 5V e garantendo temperature <65°C con il 100% delle prestazioni real-time su Nav2 e percezione.
3. **Consistenza Schemi Messaggi su Bridge Foxglove (FM-DDS-006):** Tutti i nodi che pubblicano o sottoscrivono lo stesso nome di topic devono impiegare rigorosamente lo stesso tipo di messaggio ROS 2 (es. `/cloud/status` come `std_msgs/msg/String` in `cloud_watchdog_node` e `resource_governor_node`), altrimenti Foxglove Studio segnala conflitto di schema e rifiuta l'aggregazione dei canali.
4. **Vettorizzazione SIMD Tensoriale NumPy:** In `semantic_costmap_injector.py`, il doppio loop Python è stato interamente sostituito da matrici vettorizzate in C-BLAS (`np.meshgrid`, `np.ix_`, `pts_cam @ rot_mat.T`) con gating a 1.25 Hz. Tempo di calcolo per frame abbattuto da 150 ms a **0.5 ms** (CPU da 58.5% a ~1.5%).
5. **Throttling Callback IMU a 10 Hz & Bypass Standstill:**
   - `sensor_standby_manager.py`: throttle a 10 Hz (CPU da 18.9% a ~0.2%).
   - `robot_health_supervisor.py`: heartbeat throttle a 10 Hz (CPU da 16.4% a ~0.5%).
   - `waveshare_motor_driver.py`: bypass immediato a robot fermo (`motors_stopped=True`) e throttle a 40 Hz in moto.
6. **Protezione Foxglove Bridge:** Lanciato con parametri difensivi `send_buffer_limit:=10000000`, `max_subscription_rate:=4.0` e `ignore_unresponsive_param_nodes:=true`.

---

## 🔄 Architettura Symlink Hot-Swap in `install/site-packages` e Prevenzione Discrepanze Orfane (FM-SYS-010)

### Sintomo & Diagnosi del Guasto
* **Sintomo:** Durante lo sviluppo e il collaudo remoto dei nodi Python (es. `robot_ai_node`, `respeaker_vui_node`, `conversation.py`), il codice sorgente modificato in `/mnt/ssd/robopy_controller_host/robopy_controller/` risultava perfettamente allineato al PC e su Git. Tuttavia, all'esecuzione dei comandi `ros2 run robopy_controller ...` o al lancio da `restart_hailo.sh`, il robot continuava a manifestare i vecchi bug (es. timeout di 20s, mancate risposte da Qwen, retry infiniti sul cloud) ignorando completamente il codice modificato.
* **Causa Radice:**
  1. **Directory Fisiche Duplicate in `install/`:** In una precedente invocazione di `colcon build` eseguita senza il flag `--symlink-install` (o con opzioni di copia statica), l'ambiente di build aveva duplicato fisicamente le cartelle `robot_ai` e `nodes` all'interno di:
     `/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/`.
  2. **Precedenza del PYTHONPATH in ROS 2:** Quando viene fatto il `source install/setup.bash`, ROS 2 prepone `install/.../site-packages` nel `sys.path`. Di conseguenza, qualsiasi import di `from robopy_controller.robot_ai...` o `from robopy_controller.nodes...` caricava i file Python congelati e orfani della directory di installazione, mascherando silenziosamente i sorgenti aggiornati.
  3. **Falsa Sicurezza del Forward-Sync:** Lo script `sync_marcus.sh` eseguiva l'hot-swap copiando i singoli file modificati, ma se una nuova struttura o un refactoring non intercettava tutti i percorsi esatti, o se venivano compilate estensioni, le directory statiche orfane continuavano a generare divergenza.

### Soluzione Architetturale Implementata
1. **Architettura a Symlink Canonici Forzati:**
   Le directory `nodes/` e `robot_ai/` dentro `install/robopy_controller/lib/python3.11/site-packages/robopy_controller/` NON devono mai essere cartelle fisiche indipendenti, ma collegamenti simbolici diretti (`ln -sfn`) alla radice sorgente attiva:
   ```bash
   SRC_ROOT="/mnt/ssd/robopy_controller_host/robopy_controller"
   PKG_DEST="/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller"
   
   rm -rf "$PKG_DEST/nodes" "$PKG_DEST/robot_ai"
   ln -sfn "$SRC_ROOT/nodes" "$PKG_DEST/nodes"
   ln -sfn "$SRC_ROOT/robot_ai" "$PKG_DEST/robot_ai"
   ```
2. **Auto-Riparazione Automatica in `restart_hailo.sh` e `sync_marcus.sh`:**
   Sia lo script di avvio principale sul robot (`restart_hailo.sh`) che lo script di sincronizzazione dal PC (`sync_marcus.sh`) contengono ora un assertion check preventivo:
   Se `$PKG_DEST/robot_ai` o `$PKG_DEST/nodes` esistono ma non sono symlink (`[ ! -L "$PKG_DEST/..." ]`), vengono automaticamente rimossi e ricreati come link simbolici verso `$SRC_ROOT`.
3. **Verifica Permanente Post-Build / Post-Sync:**
   È possibile verificare istantaneamente la salute dei symlink sul robot con:
   ```bash
   ls -la /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/
   # Atteso:
   # nodes -> /mnt/ssd/robopy_controller_host/robopy_controller/nodes
   # robot_ai -> /mnt/ssd/robopy_controller_host/robopy_controller/robot_ai
   ```
4. **Impatto sul Processo di Sviluppo:**
   Con i symlink integri, ogni modifica salvata nei sorgenti (o sincronizzata da Windows via `sync_marcus.sh` o rsync) è **immediatamente attiva al 100%** al riavvio del processo ROS 2, senza richiedere `colcon build` e con zero rischio di regressioni silenziose da versioni orfane.

---

## ⚡ Ottimizzazione Globale Carico CPU & RAM (FM-SYS-012)

* **Problema:** A robot fermo (`STATIONARY`), il carico CPU su Raspberry Pi 5 Cortex-A76 raggiungeva il **377% su 400%** (quasi 4 core saturi) con Load Average > 35 e `Sys` CPU all'81.8%, nonostante i grandi nodi C++/AI consumassero meno di 90% complessivo.
* **Cause Radice Individuate (via `cProfile`, `strace`, `gdb`):**
  1. **CycloneDDS Loopback Multicast Flooding:** L'impostazione di default di CycloneDDS trasmetteva tutti i dati dei topic video (`/rgb/image`, `/camera/depth/image_raw` a frammenti UDP da 13.5KB) all'indirizzo multicast `239.255.0.1:17901`. Il kernel clonava ogni frammento sui socket `recvMC` di tutti i 38 processi ROS 2 (generando ~1.500 `recvmsg()`/s a vuoto su ciascun processo).
  2. **Nav2 `/bond` Heartbeat Storm:** `bond_timeout: 120.0` generava una frequenza di 57.2 Hz con 14 publisher/subscriber (800.9 msg/s consegnati), costringendo i 9 nodi Nav2 (`lifecycle_manager_navigation`, `map_server`, ecc.) a risvegli e ricostruzioni continue del `WaitSet`.
  3. **Overhead di Ricostruzione WaitSet in `rclpy` Jazzy:** In ROS 2 Jazzy, ogni sveglia da `rcl_wait` distrugge e ricrea il WaitSet DDS iterando sugli EventHandler QoS (81.000 chiamate/s in un nodo a 55 Hz). Il falso throttling dentro i callback (`if now - last < dt: return`) non previene il risveglio DDS né la deserializzazione dei messaggi in Python.
  4. **Duplicazione di 7 Processi `static_transform_publisher`:** 7 trasformi statici CAD erano lanciati come processi separati in `restart_hailo.sh` nonostante fossero già definiti al 100% dentro `urdf/robopy.urdf` e pubblicati da `robot_state_publisher` con `TRANSIENT_LOCAL`.
  5. **Frammentazione di 10 Micro-Nodi Python:** 10 interpreti e 10 partecipanti DDS indipendenti consumavano ~550 MB RAM e duplicavano le sottoscrizioni a `/odom` e `/battery`.
* **Soluzione Architetturale a 4 Fasi:**
  1. **Fase 1 (DDS & Nav2):** Configurato `<AllowMulticast>spdp</AllowMulticast>` in `restart_hailo.sh` (multicast limitato al solo discovery SPDP, traffico dati su Unicast punto-punto ai soli veri subscriber) e `bond_timeout: 0.0` in `custom_nav2_launch.py`.
  2. **Fase 2 (Publisher-Side Throttling & Lazy Subs):** Throttling di `/battery/raw` a 2 Hz e di `/odom` a 5 Hz a robot fermo in `waveshare_motor_driver.py`; rimozione doppie sottoscrizioni in `battery_manager_node.py`; disiscrizione dinamica a riposo in `frontier_explorer_node.py`; de-sottoscrizione da depth continuo in `semantic_costmap_injector.py`; rate-limiting a 2 Hz in `resource_governor_node.py` e `multimodal_identity_node.py`; dinamizzazione IMU in `sensor_standby_manager.py`.
  3. **Fase 3 (Consolidamento URDF TF):** Rimossi i 7 processi `static_transform_publisher` da `restart_hailo.sh`, delegando la pubblicazione interamente a `robot_state_publisher` su `robopy.urdf` (-13.4% CPU, -330 MB RAM).
  4. **Fase 4 (Demone Supervisori Unificato):** Creato `robopy_controller/nodes/marcus_supervisors_daemon.py` che orchestra in un unico processo ed un unico partecipante DDS i 6 micro-nodi di supervisione (`battery_manager`, `robot_health`, `sensor_standby`, `resource_governor`, `cloud_watchdog`, `foxglove_bridge`), risparmiando ~300 MB RAM e ~35% CPU.

