---
description: serve ad implementare nav2 con depthimage_to_laserscan
---

TASK DI CODICE RICHIESTI

anzitutto bisogna installare depthimage_to_laserscan nella directory /home/robopy/ros2_jazzy
probabilmente non esiste il pacchetto per raspberry OS, dovrai compilarlo, fai riferimento al file build.md per le informazioni sulla compilazione

1. perception_composition.launch.py – Percezione a impatto RAM minimo
Crea un file di lancio che carichi i seguenti nodi in un container multithread (component_container_mt) con intra-process communication abilitata per garantire zero-copy tra i nodi.

Nodo A – Driver OAK-D (depthai_ros_driver)

Risoluzione depth: QVGA (320×200) – bilancia dettaglio e consumo di banda/CPU.

Framerate: 12 FPS – sufficiente per navigazione indoor.

Disabilita la pubblicazione di point cloud (enable_pointcloud: false) – la point cloud è molto pesante in RAM.

Disabilita ogni pipeline di Neural Network (enable_nn: false).

Output richiesto: solo il topic /oak/depth/image_raw (e eventualmente camera info).

Nodo B – Convertitore Depth → LaserScan (depthimage_to_laserscan)

Iscrizione al topic depth del driver.

Parametri:

scan_height: 6 – considera solo 6 righe centrali per ridurre il carico.

range_max: 5.0 – portata massima 5 metri.

scan_time: 0.1 – pubblicazione a circa 10 Hz.

Topic di output: /scan (LaserScan).

Vincolo fondamentale: la comunicazione tra A e B deve avvenire senza serializzazione (intra-process). Usa extra_arguments=[{'use_intra_process_comms': True}] per entrambi i nodi.

2. nav2_params_jazzy.yaml – Profilo Nav2 per 4GB RAM
Fornisci un file di parametri per Nav2 ottimizzato per memoria ridotta.

Costmap globali e locali:

Global costmap: basata su mappa statica (static layer) + obstacle layer (2D) + inflation layer. Nessun voxel layer.

Local costmap: rolling window di dimensioni ridotte (3×3 m) per limitare il numero di celle. Aggiornamento a 5 Hz.

Risoluzione: 0.1 m.

Obstacle layer: si basa solo sul topic /scan.

Planner:

NavfnPlanner (più leggero di A* o Smac).

Frequenza di pianificazione: 1 Hz (expected_planner_frequency).

Controller:

Preferire Regulated Pure Pursuit (RPP) per il minor carico computazionale rispetto a DWB.

Frequenza di controllo: 10 Hz (controller_frequency).

Velocità massime conservative: max_vel_x: 0.5, max_vel_theta: 1.0.

Recovery:

Abilitare solo spin e backup con parametri non aggressivi per evitare cicli di recupero frequenti.

Altro:

Disabilitare amcl (sarà RTAB‑Map a fornire la localizzazione).

Assicurarsi che use_sim_time sia false ovunque.

3. fast_flow_launch.py – L’Orchestratore
Scrivi il launch file Python principale che implementa la logica dei flussi di lavoro.

Struttura:

Include perception_composition.launch.py (sempre attivo).

Gestione database RTAB‑Map:

Se delete_db è true, passa l’argomento '-d' al nodo rtabmap (cancella il database all’avvio).

Se localization è true, imposta il parametro Mem/IncrementalMemory: false (modalità localizzazione sola lettura). Altrimenti, Mem/IncrementalMemory: true.

Odometria:

Se use_wheel_odom è false (default), avvia il nodo rgbd_odometry con parametri ottimizzati per ARM:

Vis/FeatureType: 6 (GFTT/ORB) – buon compromesso velocità/robustezza.

Vis/MaxFeatures: 500 – limita il numero di feature.

QueueSize: 5 – riduce la memoria occupata dalle code.

Pubblica su /odom e tf odom → base_footprint.

Se use_wheel_odom è true, non avviare rgbd_odometry; assumi che esista già un nodo che pubblica /odom (encoder).

RTAB‑Map SLAM:

Nodo rtabmap (o rtabmap_slam) connesso ai topic:

Sottoscrizione a /rgb/image, /depth/image, /camera_info (se disponibili; altrimenti usa i topic della OAK).

Sottoscrizione a /odom (dalla sorgente scelta).

Parametri cruciali per il risparmio di RAM:

Rtabmap/CreateIntermediateNodes: false – evita la creazione di nodi intermedi ridondanti.

Mem/NotLinkedNodesKept: false – in localizzazione, non mantenere nodi non collegati.

Rtabmap/TimeThr: 500 – riduce la frequenza di aggiunta nodi.

RGBD/LinearUpdate: 0.2 – aggiornamento ogni 20 cm.

RGBD/AngularUpdate: 0.1 – ogni 0.1 rad (~5.7°).

Pubblica la trasformazione map → odom (usata da Nav2).

Nav2 stack:

Avvia i server di navigazione (controller, planner, costmap, behavior, ecc.) solo se enable_nav2 è true.

Passa il file nav2_params_jazzy.yaml come parametro.

Importante: non avviare amcl; RTAB‑Map già fornisce la localizzazione.

Node di utilità:

robot_state_publisher con un URDF minimale (se non già presente nel sistema).

static_transform_publisher per base_footprint → base_link se necessario.

Vincoli di risorse:

Tutti i nodi devono essere lanciati con use_sim_time=False.

I nodi che usano molta memoria (es. RTAB‑Map) devono avere code limitate (QueueSize).

Assicurarsi che i file di configurazione non contengano percorsi assoluti ma utilizzino variabili d’ambiente o percoli relativi.

OUTPUT ATTESO
perception_composition.launch.py – codice completo commentato, con spiegazione delle scelte di zero-copy e limitazione della risoluzione.

nav2_params_jazzy.yaml – file di parametri Nav2 commentato, con enfasi sulle opzioni che riducono il carico di memoria.

fast_flow_launch.py – orchestratore principale con logica condizionale e commenti che spiegano ogni blocco.

Breve relazione tecnica (max 2000 caratteri) in cui si spiega:

Come i parametri scelti proteggono i 4GB di RAM dal crash (OOM Killer).

Quali accorgimenti specifici per ARM64 sono stati adottati (NEON, affinity, ecc.).

Eventuali trade-off accettati (es. riduzione FPS per stabilità).