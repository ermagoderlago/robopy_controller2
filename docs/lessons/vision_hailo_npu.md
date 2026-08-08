# Lezioni Apprese - Visione & Calcolo NPU (Hailo)

Questo documento descrive le lezioni apprese su OAK-D Lite, l'acceleratore NPU Hailo-10H, la fusione semantica 3D e i relativi driver C++ e Python di Marcus.

---

## 🔌 Hardware e Connettività USB (X_LINK_ERROR)

### Saturazione del Bus USB 2.0
* **Problema:** La camera OAK-D Lite si disconnette bruscamente dopo 60-90 secondi di streaming, entrando in crash loop con l'errore:
  `Couldn't read data from stream: 'rect' (X_LINK_ERROR)`
* **Causa:** Il kernel rileva la camera su bus USB "High-Speed" (USB 2.0 a 480 Mbps). Il flusso RGB compresso + profondità raw + features SuperPoint satura completamente il bus, causando la perdita di pacchetti di controllo. DepthAI interpreta il ping mancato come disconnessione hardware.
* **Risoluzione:**
  1. Collegare la fotocamera esclusivamente a una **porta USB 3.0 (blu)** del Raspberry Pi 5 o dell'Hub USB alimentato.
  2. Verificare che il cavo utilizzato supporti la larghezza di banda USB 3.0 (i cavi standard per sola ricarica degradano la connessione a USB 2.0).
  3. Controllare tramite `dmesg` che venga stampato `SuperSpeed USB device`.

### Caduta di Tensione e Reset dell'SSD
* **Regola Permanente:** All'avvio simultaneo di fotocamera, NPU ed array microfonico, il picco di assorbimento manda in sottotensione le porte USB del Pi 5, provocando il reset dell'SSD host (`reset SuperSpeed USB device`) e bloccando il filesystem in sola lettura. Utilizzare sempre un **Hub USB alimentato esternamente** per camera ed SSD.

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
  - **Inizializzazione NPU:** Da 12.4s (GIL Python) a 0.18s (C++ Nativo).



