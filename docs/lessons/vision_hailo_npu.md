# Lezioni Apprese - Visione & Calcolo NPU (Hailo)

Questo documento descrive le lezioni apprese su OAK-D Lite, l'acceleratore NPU Hailo-10H, la fusione semantica 3D e i relativi driver C++ e Python di Marcus.

---

## 🔌 Hardware e Connettività USB (X_LINK_ERROR - Risolto con BEC Droni)

### Saturazione del Bus USB 2.0 e Fallback da Voltage Sag (FM-VIS-004)
* **Problema Storico:** La camera OAK-D Lite si disconnetteva bruscamente dopo 60-90 secondi di streaming, entrando in crash loop con l'errore:
  `Couldn't read data from stream: 'rect' (X_LINK_ERROR)`.
* **Causa Radice Identificata:** Il transceiver USB 3.0 dell'OAK-D Lite richiede un'alimentazione a 5V priva di ripple per il link training ad alta frequenza (5 Gbps differential pairs). Sotto carichi AI combinati (Hailo NPU a 40 TOPS + SSD NVMe + spunti motori), il rail 5V del Pi 5 subiva un transitorio di voltage sag (<4.75V) che faceva fallire la negoziazione fisica SuperSpeed, forzando la camera a de-enumerare e degradarsi a USB 2.0 High-Speed (480 Mbps). Il flusso RGB compresso + profondità raw 16UC1 + IMU saturava i ~35 MB/s effettivi di USB 2.0, provocando la perdita di pacchetti XLink e il crash del driver DepthAI.
* **Risoluzione Definitiva Hardware (Ottobre 2026):**
  1. **Modulo di Alimentazione Drone-Grade (BEC Step-Down):** Inserito modulo buck switching ad altissima efficienza e bassa ESR derivato da componentistica per droni/FPV. Eroga 5.25V ultra-stabili con capacità di picco 6-10A e ripple <20mV.
  2. **USB 3.0 SuperSpeed Reale (5 Gbps):** Con l'alimentazione stabilizzata, la OAK-D Lite negozia stabilmente e permanentemente la modalità **SuperSpeed (5000M)**, confermata da dmesg (`SuperSpeed USB device`).
  3. **Headroom di Banda > 88%:** Con un throughput fisico di ~400-450 MB/s e il decoupling delle reti neurali da MyriadX a Hailo-10H (Depth + RGB preview occupano solo ~35-45 MB/s), il margine di banda è superiore all'88%, azzerando il rischio di saturazione e chiudendo definitivamente il failure mode `FM-VIS-004`.

### Caduta di Tensione e Reset dell'SSD (FM-PWR-002 - Risolto)
* **Risoluzione Definitiva:** L'alimentazione stabilizzata a 5.25V tramite BEC di derivazione drone ha eliminato sia il rischio di reset dell'SSD host NVMe (`reset SuperSpeed USB device`) sia gli allarmi di undervoltage del PMIC Pi 5 (`hwmon3 Undervoltage detected`), fornendo una base hardware ultra-stabile a tutti i carichi AI simultanei.

---

## 🧠 Compilazione Modelli NPU (Hailo HEF)

### Limitazioni Hardware Hailo-10H e API InferModel
* **API Legacy:** Le API legacy `VStream` e il comando `ConfigureParams.create_from_hef` non sono supportati su Hailo-10H e sollevano l'errore `HAILO_NOT_IMPLEMENTED`.
* **Risoluzione:** Riscrivere i nodi per utilizzare le moderne API `InferModel` tramite `VDevice.create_infer_model(hef_path)`.
* **Crash di Fallback Silenzioso:** Assicurarsi che nel modulo di importazione non ci siano classi obsolete o mancanti (es. `InferVStream` rimosso nelle nuove versioni SDK) che sollevino `ImportError` silenziati nei blocchi `try...except`, inducendo il nodo a ricadere nella simulazione software (`sim_mode:=True`).

### Unificazione dei Contesti (Joined HEF)
* **Contesto:** Eseguire reti multiple su Hailo-10H richiede un file HEF unificato per evitare contese hardware e tempi di caricamento alternati sul bus PCIe.
* **Join dei Modelli:** Si esegue unendo i modelli nativi in formato `.har` tramite `hailo join`, e successivamente compilando l'intero pacchetto tramite `hailo optimize` e `hailo compiler`.
* **Suddivisione Host-NPU (Esempio NetVLAD):** Il compilatore Hailo fallisce la traduzione dell'intero modello NetVLAD a causa dei nodi di pooling (reshape 4D➔3D e normalizzazioni). La soluzione ottimale è compilare su NPU il solo backbone di estrazione (MobileNetV2 + reducer 1x1) e calcolare il pooling NetVLAD sulla CPU host tramite NumPy/C++ (<1ms).

---

## 📐 C++ Semantic Mapper e Depth image Encoding

### Incompatibilità Encoding Profondità
* **Problema:** Il nodo standard `depthimage_to_laserscan` (che genera `/scan` per Nav2) rifiuta lo stream di profondità C++ della fotocamera.
* **Causa:** Il driver C++ pubblicava la profondità come `"mono16"`, mentre il nodo richiede specificamente `"16UC1"` o `"32FC1"`.
* **Risoluzione:** Modificare il driver C++ per forzare l'encoding `"16UC1"`.

### C++ Core Pinning e Ottimizzazione
* **Core Pinning:** `hailo_bridge_node` e `marcus_semantic_mapper_cpp` devono essere vincolati ai Core CPU 2 e 3 del Raspberry Pi 5 per isolare il calcolo intensivo dai thread di I/O.
* **Eigen vs PCL:** Per ottimizzare i tempi di calcolo, la back-projection geometrica C++ del mapper deve utilizzare matrici Eigen pure ed intrinseci camera pre-allocati, evitando l'overhead e le runtime allocations della libreria PCL.

---

## 👥 Riconoscimento Facciale & Dynamic Enrollment (Sprint 3)

### Pipeline Integrata (Detect -> Align -> Embed -> Match)
* **Landmarks & Alignment:** L'allineamento affine tramite `cv2.estimateAffinePartial2D` basato sui 5 punti landmark di SCRFD è essenziale prima di passare l'immagine ad ArcFace. Se l'allineamento fallisce, un crop standard basato sulla bounding box ridimensionata è usato come fallback.
* **Normalizzazione L2:** Gli embedding ArcFace (512-dim) devono essere normalizzati con norma L2 (divisi per la norma euclidea). Questo permette di calcolare la similarità del coseno tramite un semplice prodotto scalare (`np.dot`), abbattendo i tempi CPU a pochi microsecondi per confronto.
* **Dynamic Enrollment:** Per evitare falsi positivi causati dal rumore del singolo frame, l'enrollment ospite accumula 10 campioni temporali consecutivi, ne calcola la media vettoriale, esegue una nuova normalizzazione L2 e salva il vettore risultante come file `.npy` sotto `known_faces/<nome>/`.
* **Fallback di Simulazione Robust:** Il nodo `hailo_bridge_node.py` implementa un controllo a run-time per verificare se lo stream SCRFD/ArcFace è presente nell'HEF caricato. In caso di assenza, attiva automaticamente la simulazione leggendo i file `.npy` da disco e pubblicando periodicamente i vettori per consentire il funzionamento e il test del sistema orchestrator/RAG in qualsiasi configurazione di modello HEF.

---

## 📐 Allineamento delle Risoluzioni Modello & Risoluzione dei Mismatch (Sprint 5)

### Allineamento della Risoluzione SuperPoint
* **Problema:** Mismatch di risoluzione tra le dimensioni configurate nell'host (`480x360`) e quelle reali del modello `.blob` caricato su NPU (`320x200`). Questo genera warning continui da parte di DepthAI e provoca letture fuori dai limiti del vettore di heatmap (heap memory overflow silente), portando all'estrazione di keypoint spuri e a derive esponenziali dell'odometria visuale (che a sua volta causa falsi movimenti e inclinazioni 3D del robot).
* **Soluzione:** Configurare rigidamente le costanti di ridimensionamento del frame (`SP_W` e `SP_H`) per allinearsi esattamente alle dimensioni di input del modello compilato (320x200).

### Robustezza del Parsing dei Tensor (Dimension-Independent)
* **Problema:** I controlli basati su soglie di dimensione fissa (`data.size() > 500000` per i descrittori e `> 100000` per l'heatmap) falliscono silenziosamente quando la risoluzione del modello cambia, impedendo il caricamento dei dati.
* **Soluzione:** Confrontare le dimensioni relative dei due layer di output in modo indipendente dalla risoluzione. Poiché i descrittori hanno 256 canali e l'heatmap 65 canali su una griglia di identiche dimensioni ($H \times W$), il vettore dei descrittori è sempre circa 4 volte più grande dell'heatmap. Il confronto relativo `data0.size() > data1.size()` garantisce la corretta assegnazione in qualsiasi risoluzione.

---

## 🗺️ Visual Odometry & SLAM Map Drift Resolution (Sprint 5)

### Zero Velocity Update (ZUPT) in Visual Odometry
* **Problema:** Anche a robot fermo, il rumore dei singoli pixel della camera e le variazioni di illuminazione portano i nodi di Visual Odometry (sia C++ che Python) a calcolare piccoli delta di posa fittizi (drift). RTAB-Map SLAM, leggendo questa odometria visuale derivata, muove progressivamente il robot all'interno della mappa globale.
* **Soluzione:** Implementare un filtro ZUPT all'interno del nodo VO C++ (`oak_superpoint_odometry_cpp`). Sottoscrivendosi al topic `/cmd_vel`, se il robot non riceve comandi di movimento attivi (linear/angular > 0.001) per più di 500ms, il nodo VO smette di accumulare la matrice di posa (`pose_matrix_`), bloccando a zero qualsiasi deriva spaziale da fermo.

### Esclusione Concorrente del TF `odom → base_link`
* **Regola Permanente:** Solo un nodo alla volta può pubblicare il transform `odom → base_link` in ROS 2. Per evitare sfarfallii in RViz, disabilitare `publish_tf` su tutti i nodi di Visual Odometry (`fast_flow_vo` e `oak_superpoint_odometry`) tramite i parametri di launch e startup, ed abilitarlo esclusivamente sul driver motori (`waveshare_motor_driver`), che è basato su encoder fisici e filtrato da dead-zone ed è quindi il sensore più affidabile a robot fermo.

---

## 👁️ Integrazione YOLO, Mappatura Semantica e Debug Image (Sprint 5)

### Eliminazione degli Ostacoli Spuri Persistenti (Ghost Obstacles)
* **Problema:** In Foxglove/RTAB-Map veniva costantemente visualizzata una sedia mock all'interno della costmap che non si degradava mai.
* **Causa:** Il nodo `hailo_bridge_node` in modalità simulata (`sim_mode`) pubblicava a frequenza fissa (1.5 Hz) l'ostacolo fittizio "sedia" sul topic `/hailo/vlm/semantic_objects`. Poiché l'intervallo di pubblicazione era inferiore al tempo di decadimento temporale di `semantic_costmap_injector` (5.0s), il timestamp veniva continuamente aggiornato impedendone il decadimento.
* **Risoluzione:** Introdotto il parametro `publish_sim_sedia` (default: `False`). In questo modo l'ostacolo simulato non inquina la navigazione a meno che non sia esplicitamente configurato a `True` per test.

### Mappatura Semantica Diretta da YOLO in Real Mode
* **Problema:** Quando si eseguiva il robot in modalità reale, nessuna informazione semantica veniva inviata al costmap injector o al mapper C++ perché il topic `/hailo/vlm/semantic_objects` non riceveva pubblicazioni.
* **Risoluzione:** Collegati i rilevamenti YOLO reali alla pipeline semantica. Le classi COCO rilevate (sedie, persone, tavoli, ecc.) vengono tradotte in italiano e mappate in messaggi `SemanticObject`. Le coordinate Z (profondità) vengono stimate proiettando il centro del box 2D sul frame di profondità tramite `self.latest_depth`, mentre le dimensioni fisiche dell'ostacolo vengono ricavate tramite regressione geometrica basata su FOV e profondità.

### Stream di Debug Annotato (Annotated Compressed Video)
* **Problema:** Difficoltà nel diagnosticare la bontà del riconoscimento oggetti e della semantica direttamente da Foxglove in assenza di un feed video annotato.
* **Risoluzione:** Implementato il topic `/hailo/annotated_image/compressed` in `hailo_bridge_node`. Sovrappone in tempo reale sul flusso RGB i box YOLO (in verde), i volti riconosciuti (in arancione) e gli oggetti semantici (in viola) con indicazione del nome classe, confidenza e coordinate 3D stimate, ricomprimendo il frame in JPEG prima dell'invio.

---

## 🔧 Correzioni Critiche Pipeline YOLO & Semantica (Luglio 2026)

### Esecuzione YOLO con InferModel API
* **Problema:** L'inferenza reale YOLO non partiva mai con le nuove API `InferModel` (standard per Hailo-10H) a causa del controllo rigido `self.yolo_network_group is not None` (valido solo per le API legacy).
* **Risoluzione:** Modificata la condizione a riga 674 per includere `(self.use_infer_model_api and self.has_yolo)`, abilitando l'esecuzione dell'inferenza reale YOLO sulla NPU.

### Accesso Concorrente alla NPU (NPU Thread Safety)
* **Problema:** I tre thread concorrenti (YOLO, Face Recognition/SCRFD, NetVLAD) utilizzavano lo stesso oggetto `self.bindings` ed eseguivano `configured_infer_model.run` senza alcuna mutua esclusione, portando a collisioni di memoria e corruzione dei dati.
* **Risoluzione:** Introdotto un mutex globale `self._infer_lock = threading.Lock()` in `hailo_bridge_node.py` per proteggere tutte le operazioni di `set_buffer` e `run` dell'NPU, assicurando l'accesso thread-safe.

### Sottoscrizione Immagine Raw
* **Problema:** `hailo_bridge` sottoscriveva a `/rgb/image/compressed` ma la camera pubblicava `/rgb/image` raw, bloccando silenziosamente l'arrivo dei frame.
* **Risoluzione:** Modificata la sottoscrizione a `/rgb/image` raw e introdotto `CvBridge` per la decodifica efficiente a livello di thread.

### Integrazione Nav2 Costmap (QoS & Z-Coordinate)
* **Problema 1:** Nav2 non riceveva gli ostacoli perché il publisher PointCloud2 del costmap injector usava QoS `BEST_EFFORT` invece del QoS `RELIABLE` atteso. Inoltre, `obstacle_layer` non era abilitato nella lista dei plugins di `global_costmap` in `nav2_params_jazzy.yaml`.
* **Problema 2:** L'ingombro 3D dell'ostacolo veniva espanso a `z = -0.5m` sotto terra, rischiando di essere filtrato a causa delle soglie di altezza minima del robot.
* **Risoluzione:** Impostato il publisher di PointCloud2 su `RELIABLE`, aggiunto `obstacle_layer` ai plugin del global_costmap, e semplificata la generazione del PointCloud proiettando tutti i punti sul piano `z = 0.1m`.

### Correzione Coordinate centroid_2d
* **Problema:** `centroid_2d` conteneva erroneamente i valori fisici X/Y in metri, causando errori nei marker e nelle visualizzazioni.
* **Risoluzione:** Riconfigurato per contenere le coordinate 2D normalizzate centrali `[0.0, 1.0]` basate sulle coordinate del bounding box 2D.

### Correzione TOCTOU C++ Mapper
* **Problema:** Nel mapper C++, la dimensione del buffer veniva letta sotto un lock, per poi iterare sul buffer riacquisendo il lock per ogni singolo elemento, permettendo al thread di scrittura `syncCallback` di mutare il buffer ed invalidare l'indice, provocando corruzione o crash.
* **Risoluzione:** Modificato il codice in `publishSemanticObjects` e `publishMarkers` per copiare localmente l'intero array degli oggetti attivi sotto un unico lock prima di processarlo ed inviarlo.

---

## 👥 Face Recognition Reale — Matching Embedding (Luglio 2026)

### Architettura Pipeline Completa (SCRFD → ArcFace → FaceDatabase)
* **Flusso:** 1) SCRFD rileva bounding box + 5 landmark facciali → 2) Allineamento affine (`cv2.estimateAffinePartial2D`) usando i landmark → 3) ArcFace sulla NPU estrae embedding 512-dim → 4) `FaceDatabase.identify()` confronta via prodotto scalare (coseno su vettori normalizzati L2) con i volti noti.
* **Thread Safety NPU:** Tutte le operazioni ArcFace (sia SCRFD che embedding) avvengono sequenzialmente sotto `self._infer_lock` per evitare accessi concorrenti all'NPU.

### Classe FaceDatabase
* **Caricamento:** All'avvio di `hailo_bridge_node`, `FaceDatabase.load()` scansiona `known_faces/<nome>/embedding.npy` e carica tutti gli embedding in un dizionario in memoria. I vettori sono normalizzati L2 internamente.
* **Matching Coseno:** `identify(embedding, threshold)` normalizza il vettore di query e calcola il prodotto scalare (equivalente alla similarità del coseno) con ogni embedding noto. Complessità: O(N) con N = numero persone note, <1ms anche per N=100.
* **Parametro soglia:** `face_identity_threshold` (default 0.45, range 0-1). Valori consigliati: 0.40 (più permissivo) fino a 0.55 (più restrittivo). Regolare in base al numero di falsi positivi/negativi osservati.

### Topic Nuovo: /hailo/face/identity
* Pubblica il **nome della persona riconosciuta** (o `'unknown'`) come `std_msgs/String` con QoS RELIABLE ogni volta che un volto viene rilevato e comparato.
* Formato speciale per enrollment completato: `'enrolled:<nome>'`.

### Enrollment Runtime via /hailo/face/enroll
* Pubblicare il nome persona su `/hailo/face/enroll` mentre il soggetto è inquadrato. Il nodo accumula 10 embedding ArcFace reali, calcola la media vettoriale, normalizza L2 e salva `embedding.npy`.
* Comandi supportati: `'<nome>'` (avvia enrollment), `'cancel:<nome>'` (annulla), `'reload'` (ricarica DB da disco).

### Script face_enrollment_offline.py
* Script standalone (no ROS 2, no Hailo) per generare embedding **placeholder** da foto `.jpg` nelle cartelle `known_faces/<nome>/`.
* Usa HOG features (128x128, 9 bin) proiettate a 512-dim tramite matrice deterministica. Normalizzazione L2 finale.
* **LIMITAZIONE CRITICA:** I placeholder NON riconoscono i volti in modo corretto. Servono solo per testare il flusso sistema (caricamento DB, topic, annotazione) prima dell'enrollment reale su Marcus.
* Uso: `python3 face_enrollment_offline.py --faces-dir known_faces --dim 512 --force`

### Annotazione Visiva Aggiornata
* I box dei volti sull'immagine annotata `/hailo/annotated_image/compressed` mostrano ora il **nome riconosciuto** (o `unknown`) invece del generico `face`, con lo score di similarità.

---

## 🎯 Correzione Dequantizzazione e Decoder YOLOv8 DFL (Luglio 2026)

### Dequantizzazione Automatica Output (`FormatType.FLOAT32`)
* **Problema:** Gli output dell'API `InferModel` di HailoRT restituivano buffer raw di byte non dequantizzati (0-255 uint8). Interpretando questi dati come float32 senza configurazione del formato, le confidenze risultavano superiori a 30,000, convertendosi in score del **3,595,500%** su Foxglove 3D e saturando il frame di falsi positivi.
* **Risoluzione:** Applicata la chiamata `outp.set_format_type(FormatType.FLOAT32)` prima di `infer_model.configure()`, forzando HailoRT a dequantizzare automaticamente gli output a `float32` ed allocando i buffer come `dtype=np.float32`.

### Decoder Multi-Scala YOLOv8 DFL per Output Separati
* **Problema:** Il parser legacy `_parse_yolo_output` iterava indistintamente su tutte le 10 viste di output (`yolo/conv44`, `yolo/conv45`, `yolo/conv46`, `yolo/conv48`, `yolo/conv60`, `yolo/conv61`, `yolo/conv62`, `yolo/conv73`, `yolo/conv74`, `yolo/conv75`). Le mappe di feature di regressione DFL (`conv44`, `conv60`, `conv73`) e proto-mask (`conv48`) venivano scambiate per coordinate + confidenza, creando oltre 3,000 falsi box al secondo su tutta l'immagine.

---

## 🚀 Optimized Hailo Multiplexing & Camera Geometry (Luglio 2026)

### Single Network Group HEF Multiplexing
* **Contesto:** Switchare tra due contesti HEF (Group 1: 15Hz segmentazione, Group 2: 2-3Hz YOLO) genera un overhead di 10-25ms per switch dal firmware.
* **Soluzione:** Compilazione unificata tramite `hailo compiler --join` producendo `joined_yolo_superpoint_netvlad.hef` con contesto unico di esecuzione (Zero context switching overhead su NPU).

### Standardizzazione Risoluzione & HFOV Overlap Verification
* **Risoluzione Standard:** Flussi RGB e stereo depth fisso a **640x480 @ 30 FPS**.
* **HFOV Luxonis OAK-D Lite:** RGB HFOV = 69°, Mono Depth HFOV = 71.8°.
* **Passo Angolare Scansione 35°:** Con la regola del 50% overlap, $0.5 \times 69^\circ = 34.5^\circ \approx 35^\circ$. Garantito $\ge 50\%$ di sovrapposizione visiva delle feature.

---

## ⚡ Real-Time Streaming Annotato a 30 FPS & Filtro Euristico Bounding Box (Luglio 2026)

### Sgancio dello Streaming Annotato dal Loop VLM (30 FPS Real-time)
* **Problema:** Il topic `/hailo/annotated_image/compressed` non si aggiornava in tempo reale sul dashboard/Foxglove, rimanendo congelato o aggiornandosi alla bassa frequenza del loop VLM (~1.5 Hz).
* **Causa:** La chiamata a `annotate_and_publish_image` era posizionata all'interno del loop di inferenza lenta del VLM anziché nel callback di ricezione dei fotogrammi della fotocamera.
* **Risoluzione:** Spostata la chiamata `annotate_and_publish_image` direttamente all'interno di `rgb_callback` a 30 Hz. L'immagine annotata viene ora compressa JPEG e pubblicata a frequenza di frame nativa.

### Filtro Euristico per Bounding Box Full-Frame a Bassa Confidenza
* **Problema:** In particolari condizioni di luce, YOLO generava rilevamenti spuri a schermo intero ($W \times H \approx \text{area totale}$) con confidenza medio-bassa ($<0.75$).
* **Risoluzione:** Inserito un filtro euristico in `_parse_yolo_output`: se un bounding box copre oltre l'80% dell'immagine ed ha confidenza $<0.75$, viene automaticamente scartato.

### Parametrizzazione e Dynamic Fallback dei Topic RGB/Depth per `hailo_bridge_node`
* **Problema:** `/hailo/annotated_image/compressed` risultava vuoto (0 Hz) se il driver OAK-D pubblicava su `/oak/rgb/image_raw` o `/camera/rgb/image_raw` anziché sul topic cablato `/rgb/image`.
* **Risoluzione:** Aggiunti i parametri ROS 2 `rgb_topic` (default `/rgb/image`) e `depth_topic` (default `/camera/depth/image_raw`) a `hailo_bridge_node.py` per consentire la riconfigurazione dinamica tramite launch file o `--ros-args -p rgb_topic:=/oak/rgb/image_raw`.

---

## 📐 Autocalibrazione Estrinsica Continua e Self-Healing Camera Sag (FM-VIS-003)

### Drift Meccanico e Muri Inesistenti (Ghost Obstacles)
* **Problema:** Le vibrazioni continue prodotte dai motori allentano progressivamente la staffa fisica di supporto della fotocamera OAK-D Lite, variandone il pitch (inclinazione) reale rispetto a quello configurato nell'URDF statico. Se la camera cede verso il basso (+pitch sag), la superficie del pavimento entra nel FOV e viene scambiata dal costmap injector per un ostacolo continuo (muro inesistente), causando lo stallo permanente del robot.
* **Soluzione Diagnostica & Consapevolezza:** Implementato il nodo `extrinsic_camera_calibrator.py`. Analizza la regione inferiore della Depth Map tramite fit RANSAC del piano del terreno $Ax + By + Cz + D = 0$. Calcola la normale $\vec{n}$ in `base_link` e deriva la deviazione di pitch $\Delta \theta_{pitch} = \arcsin(n_x)$. Pubblica lo stato su `/diagnostics` (Hardware ID: `OAK-D-Lite`) e `/robot/health_status`.
* **Soluzione Proattiva (Self-Healing a Caldo):**
  1. Il nodo calcola l'angolo correttivo e lo trasmette su `/camera/extrinsic_pitch_correction` a `dynamic_camera_tf_node.py`, che aggiorna a caldo il transform `base_link` $\rightarrow$ `camera_link_stabilized`.
  2. Invia un segnale di flush su `/semantic_costmap/clear` a `semantic_costmap_injector.py`, rimuovendo all'istante i ghost obstacles dalla costmap 2.5D.

---

## ⚡ Riscrittura Nativa C++ `hailo_bridge_node_cpp` & Azzeramento Overhead GIL (Agosto 2026)

### Profilazione Bottleneck Python (FM-CPU-001)
* **Problema:** Il nodo Python `hailo_bridge_node.py` saturava al 100% il CPU Core 1 del Raspberry Pi 5 a causa del Global Interpreter Lock (GIL) e della conversione continua dei buffer di memoria tra NumPy e OpenCV C++.
* **Riscrittura Nativa in C++:** Implementato `hailo_bridge_node_cpp` in C++ nativo con il driver `hailort` (`<hailo/hailort.hpp>`), zero-copy image transport, e Lazy Publishing (`getNumSubscribers() == 0`).
* **Risultati del Benchmark (Prima vs Dopo):**
  - **CPU Core 1 Usage:** Da **100% Satura** a **0.0%** (-100% overhead CPU).
  - **Load Average:** Da **12.33** a **1.72** (-86% carico complessivo).
  - **RAM Libera:** Da **85 MB** a **2.42 GB** (+2.33 GB RAM libera).
---

## 🧭 Integrazione IMU OAK-D Lite (BNO085) & Correzione Polarità Assi Gyro Z (Settembre 2026)

### Disallineamento Sistema di Riferimento Camera vs Robot (REP-103)
* **Sintomo:** Durante le rotazioni fisiche sul posto (in-place turn), la mappa in Foxglove / RViz ruotava violentemente nel senso opposto, per poi riallinearsi bruscamente generando "strappi" e deformazioni delle pareti nella occupancy grid di RTAB-Map. Comandando una rotazione a sinistra, il robot virtuale virava a destra e viceversa.
* **Causa Fondamentale:** Nel nodo C++ `fast_flow_vo_node.cpp`, i pacchetti inerziali DepthAI (`dai::IMUData`) venivano convertiti negli assi ROS `imu_link` tramite:
  ```cpp
  double gx_ros = packet.gyroscope.z;
  double gy_ros = -packet.gyroscope.x;
  double gz_ros = packet.gyroscope.y; // ❌ ERRORE: asse Y camera non invertito!
  ```
  Nel sistema di coordinate nativo della camera DepthAI (OAK-D Lite BNO085):
  - L'asse $X$ punta verso **destra** $\implies$ ROS $Y$ (sinistra) = $-X_{cam}$.
  - L'asse $Y$ punta verso il **basso** $\implies$ ROS $Z$ (in alto) = $-Y_{cam}$.
  - L'asse $Z$ punta in **avanti** $\implies$ ROS $X$ (avanti) = $+Z_{cam}$.
  Di conseguenza, ruotando il robot verso sinistra (rotazione antioraria/CCW attorno a ROS $+Z$ in alto), la velocità angolare attorno all'asse camera $+Y$ (rivolto verso il basso) risulta **negativa**.
  Mappando `gz_ros = packet.gyroscope.y` senza negazione, l'IMU pubblicava `angular_velocity.z < 0` (rotazione oraria / a destra) quando il robot girava fisicamente a sinistra!
* **Risoluzione Definitiva:**
  Applicare la corretta proiezione euclidea speculare:
  ```cpp
  double gz_ros = -packet.gyroscope.y; // ✅ Corretto: rotazione antioraria attorno a ROS +Z (UP) è -gyroscope.y
  ```
---

## 🚀 InferModel C++ API su HEF Unificato Multi-Rete (Settembre 2026)

### Binding Obbligatorio di Tutti gli Input in Joined HEF
* **Problema:** Quando si carica un file HEF con reti congiunte (`marcus_unified.hef` contenente `joined_yolo_superpoint_netvlad`), HailoRT C++ restituisce l'errore:
  `CHECK failed - Couldnt find input buffer for 'netvlad/input_layer1'` se viene associato solo l'input dello stream YOLO (`yolo/input_layer1`).
* **Causa:** Nelle pipeline di esecuzione di `ConfiguredInferModel`, se un modello HEF contiene più reti raggruppate in un singolo context group, tutti gli stream di input definiti in `infer_model->inputs()` devono avere un buffer di memoria valido associato in `bindings` prima di invocare `configured_infer_model->run()`, anche se l'inferenza mirata riguarda solo una delle teste (YOLOv8).
* **Risoluzione:**
  Iterare su tutti gli stream di input esposti da `infer_model_->inputs()` e pre-allocare vettori dedicati `input_buffers_[name]` collegandoli ai rispettivi binding:
  ```cpp
  for (const auto &inp : infer_model_->inputs()) {
      std::string name = inp.name();
      input_buffers_[name].resize(inp.get_frame_size(), 0);
      auto in_stream = bindings_->input(name);
      if (in_stream) {
          in_stream->set_buffer(hailort::MemoryView(
              input_buffers_[name].data(), input_buffers_[name].size()
          ));
      }
  }
  ```

### Decodifica Multi-Scala YOLOv8 DFL Nativa C++
* **Implementazione:** In `hailo_bridge_node_cpp`, la decodifica dell'architettura anchor-free YOLOv8 avviene direttamente in memoria float32 a 3 scale (stride 8: 80x80, stride 16: 40x40, stride 32: 20x20):
  - Calcolo Softmax su 16 bin per ciascuno dei 4 lati (distribuzione DFL: left, top, right, bottom).
  - Proiezione sulle coordinate immagine 640x640 e scaling alla risoluzione RGB nativa della camera.
  - Applicazione di Non-Maximum Suppression (NMS) veloce con soglia IoU 0.45.
  - Pubblicazione sincrona dei topic `/hailo/detections` e `/hailo/semantic_objects` (etichette COCO tradotte in italiano: persona, sedia, tavolo, ecc.).

---

## ⚡ Esecuzione Ament Isolata, Risoluzione Shared Libraries e DDS Multi-Domain (Settembre 2026)

### Esecuzione Nodi C++ tramite `ros2 run` vs Esecuzione Diretta del Binario
* **Problema:** Quando `hailo_bridge_node_cpp` veniva eseguito invocando direttamente il path del binario (`nohup taskset -c 2,3 /mnt/ssd/.../hailo_bridge_node_cpp`), l'eseguibile falliva immediatamente con:
  `error while loading shared libraries: libservice_msgs__rosidl_generator_py.so: cannot open shared object file: No such file or directory`.
* **Causa:** Nelle installazioni colcon a pacchetti isolati (come Jazzy su Pi 5), i pacchetti ROS 2 generano directory `lib` individuali (es. `/home/robopy/ros2_jazzy/install/service_msgs/lib`). Il comando `source setup.bash` in shell non interattive o script parziali non esporta tutte le cartelle in `LD_LIBRARY_PATH`. Al contrario, `ros2 run` interroga l'Ament Index ed espande a runtime l'intero `LD_LIBRARY_PATH` con tutte le dipendenze condivise.
* **Risoluzione:** Invocare sempre il nodo tramite `ros2 run`:
  ```bash
  nohup taskset -c 2,3 ros2 run robopy_controller hailo_bridge_node_cpp --ros-args ...
  ```

### Allineamento Dominio DDS 42 e Visibilità Foxglove
* **Problema:** I topic della telecamera e dell'NPU Hailo (`/hailo/annotated_image/compressed`, `/hailo/semantic_objects`) non apparivano nell'interfaccia Foxglove Studio né nelle liste topic globali.
* **Causa:** Il nodo C++ e gli script di test erano stati avviati senza ereditare esplicitamente le variabili `ROS_DOMAIN_ID=42`, `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` e `CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml`. Inoltre, `ENABLE_HAILO` nello script `restart_hailo.sh` era impostato di default a `false`.
* **Risoluzione:**
  1. Impostato `ENABLE_HAILO="${ENABLE_HAILO:-true}"` come default in `restart_hailo.sh` per garantire l'avvio della percezione Hailo al boot standard di Marcus.
  2. Esportato `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` in `restart_hailo.sh`.
  3. Aggiunta la sanitizzazione automatica dei terminatori di linea (`sed -i 's/\r$//'`) durante la copia a caldo dei nodi Python per prevenire crash `/usr/bin/env: 'python3\r': No such file or directory`.

---

## 🎯 Discriminazione Semantica YOLOv8, Stream RGB e Letterbox 1:1 (FM-VIS-009 - Settembre 2026)

### Scambio Sistematico di Divani/Arredi per Persone
* **Problema:** Il robot, pur iniettando correttamente gli ostacoli semantici nella mappa Nav2, rilevava continuamente `persona` in corrispondenza di divani, cuscini e tavoli lunghi, attivando impropriamente la prossemica vocale dell'engagement monitor.
* **Diagnosi delle Cause Radice:**
  1. **Stream Monocromatico (Zero Crominanza):** Il driver DepthAI C++ `fast_flow_vo_node.cpp` aveva disattivato `camRgb` per vecchi vincoli di banda USB 2.0 (`FM-VIS-001`), trasmettendo sul topic `/rgb/image` il fotogramma monocromatico sinistro `rect_left` replicato sui 3 canali (`cv::COLOR_GRAY2BGR`). I filtri convoluzionali di YOLOv8, addestrati su COCO RGB, venivano privati dell'informazione cromatica fondamentale (tonalità pelle, tessuti dei vestiti vs cuoio/stoffa dell'arredo).
  2. **Quantizzazione INT8 Sintetica (`--use-random-calib-set`):** L'archivio unificato `marcus_unified.hef` era stato compilato con dataset di calibrazione sintetico a rumore bianco e senza file `.alls` di normalizzazione, causando la saturazione e il degrado dei logit sigmoidei. In condizioni di rumore, il prior dominante della classe 0 (`persona`) in COCO prevaricava sistematicamente le classi secondarie.
  3. **Distorsione Anamorfica dell'Aspect Ratio:** L'immagine 640x400 veniva ridimensionata brutalmente a 640x640 con `cv::resize` semplice in `hailo_bridge_node.cpp`, stirando verticalmente la sagoma orizzontale di un divano del 160% e facendola coincidere geometricamente con il bounding box di una persona in piedi.
  4. **Soglia Confidenza Permissiva:** La costante `conf_thresh` in C++ era impostata a 0.35, facendo passare tutte le oscillazioni spurie dovute al rumore INT8.
* **Risoluzione Definitiva:**
  1. **Letterbox 1:1:** Implementato il preprocessing con scala isotropa e padding grigio 114, con rimappatura inversa corretta dei bounding box.
  2. **Allineamento SPEC-03:** Innalzata la soglia `conf_thresh` al valore nominale di specifica (`0.55f`) ed inserito un filtro di plausibilità d'aspetto ($W/H \le 1.8$ per la classe persona).
  3. **Guida HEF Ufficiale:** Redatta la documentazione completa in `docs/guides/HAILO_HEF_COMPILATION_GUIDE.md` per la ricompilazione ad alta fedeltà con dataset COCO reale in WSL 2.

---

## 🕺 Pose Tracking su Hailo-10H, Presence Gating & Decoupling OAK-D Lite (F1 Upgrade - Ottobre 2026)

### Spostamento del Pose Tracking da MyriadX VPU a Hailo-10H NPU
* **Problema:** Tentativi pregressi di eseguire reti neurali (YOLOv8-seg, SuperPoint) a bordo della VPU MyriadX dell'OAK-D Lite provocavano saturazione della banda USB 2.0/3.0, latenze incontrollate e crash ricorrenti con `X_LINK_ERROR` (`FM-VIS-001`).
* **Soluzione Architetturale (SPEC-03):** Decoupling totale. L'OAK-D Lite opera unicamente come sensore di profondità hardware stereo (16UC1), preview RGB e IMU senza alcun carico neurale a bordo del MyriadX. Tutti i modelli di percezione (YOLO, SuperPoint, Pose Tracking) sono migrati sull'NPU Hailo-10H (PCIe Gen 3, 40 TOPS).

### Presence-Gated Pose Execution (FM-GOV-016, FM-GOV-015)
* **Logica:** L'inferenza della stima di posa (YOLOv8-pose) e il relativo post-processing consumano cicli NPU e bandwidth DRAM anche quando la stanza è vuota.
* **Implementazione:** In `hailo_bridge_node_cpp`, l'inferenza di posa viene attivata **esclusivamente quando YOLO rileva almeno un'entità con classe `person`** con confidenza $\ge 0.50$.
* **Vantaggi Ingegneristici:**
  1. A stanza vuota, l'overhead NPU del modello di posa si azzera completamente (0 Hz).
  2. Nessun riscaldamento termico parassita dell'acceleratore Hailo-10H (`FM-GOV-015`).
  3. Al rilevamento di un soggetto umano, il modello di posa entra immediatamente in azione entro il medesimo ciclo frame, estraendo i 17 keypoint anatomici COCO.

### Post-Processing Keypoint C++ su Core 2-3 & Lazy Publishing
* **Zero Overhead Python su Core 0-1 (`FM-GOV-016`):** Il decode di DFL per bounding box e delle coordinate $(x, y, conf)$ per i 17 landmark anatomici è implementato interamente in C++ con strutture pre-allocate e core pinning forzato sui Core 2 e 3 del Raspberry Pi 5.
* **Proiezione Inverse Letterbox Isotropa:** Le coordinate dei keypoint vengono rimappate nello spazio immagine originale tenendo conto delle bande di padding (letterbox 1:1), garantendo che giunti e ossa coincidano geometricamente con il corpo umano.
* **Visualizzazione Scheletro:** Se presente almeno un subscriber (Foxglove Studio / RViz), lo scheletro viene renderizzato con 17 sfere per i nodi articolari e 18 linee per le connessioni ossee via `visualization_msgs::msg::MarkerArray` su `/hailo/pose/skeletons` e disegnato su `/hailo/annotated_image`. Se non ci sono subscriber, il lazy publishing salta completamente il disegno OpenCV, risparmiando oltre il 90% di CPU host.

### Compilazione HEF Reale YOLOv8s-Pose, Quantizzazione QAT e Sigmoid NPU Decoder (Ottobre 2026)
* **Pipeline di Compilazione (WSL 2 Ubuntu-24.04 con Hailo DFC 5.3.0):**
  1. **Parsing:** `hailomz parse yolov8s_pose --ckpt ./yolov8s_pose.onnx --hw-arch hailo10h` ➔ Generazione di `yolov8s_pose.har` (architettura Hailo-10H).
  2. **Quantizzazione QAT con Dataset Reale:** `hailomz optimize yolov8s_pose --har ./yolov8s_pose.har --calib-path /home/robopy/datasets/COCO/train2014 --hw-arch hailo10h`. *Nota per l'ambiente WSL:* Impostare `CUDA_VISIBLE_DEVICES=""` per eseguire l'ottimizzazione in CPU-mode, bypassando l'assenza di `libdevice.10.bc` nel DirectML di WSL2.
  3. **Compilazione HEF:** `hailomz compile yolov8s_pose --har ./yolov8s_pose.har --hw-arch hailo10h` ➔ HEF finale `yolov8s_pose.hef` (15 MB, 5 contesti NPU, multiscale 80x80, 40x40, 20x20).
* **Trappola Architetturale del Sigmoid nei File ALLS:**
  - Nel file `yolov8s_pose.alls` ufficiale di Hailo Model Zoo, la direttiva `change_output_activation(convXX, sigmoid)` applica la funzione sigmoidea direttamente nell'hardware NPU per i layer di classificazione (`conv44`, `conv58`, `conv71`).
  - I tensori in uscita in FLOAT32 contengono già probabilità in $[0.0, 1.0]$.
  - L'applicazione di un secondo sigmoide software nel decoding C++ (`1 / (1 + exp(-x))`) trasformava i valori di sfondo vicini a $0.0$ in $0.50$, facendo superare a tutte le 8400 celle di griglia la soglia confidenza ($\ge 0.50$) e saturando la scena con oltre 2000 falsi candidati.
  - **Fix:** Rilevare se il valore estratto dal tensore si trova già nell'intervallo $[0.0, 1.0]$. Se sì, usare direttamente la probabilità; altrimenti applicare la sigmoide ai logit grezzi. Inoltre, applicare il filtro proporzionale $W/H \le 1.8$ sul bounding box prima del gating NMS.


