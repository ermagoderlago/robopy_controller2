# Engineering Change Orders - Attuazione & Controllo

Questo documento raccoglie la cronologia delle modifiche ingegneristiche (ECO) apportate ai sistemi di attuazione, cinematica differenziale e controllo motori di Marcus.

---

## 📈 ECO-2026-06-03-001: Waveshare General Driver (ESP32) Integration
* **Stato:** ✅ **Completato, Sincronizzato e Compilato**
* **Descrizione:** Progettazione ed implementazione di un nuovo nodo ROS 2 standalone in Python chiamato `waveshare_motor_driver` per il controllo a basso livello e odometria della nuova scheda Waveshare General Driver (ESP32) via seriale USB.
* **Modifiche apportate:**
  * Creato `waveshare_motor_driver.py` (comunicazione seriale JSON con ESP32, sottoscrizione `/cmd_vel` ed invio `{"T": 1, "L": v_L, "R": v_R}`, lettura feedback tick encoder, calcolo ed integrazione geometrica odometria con pubblicazione su `/odom` e TF `odom ➔ base_link`, watchdog di stop a 500ms).
  * Creato lo script wrapper `scripts/waveshare_motor_driver` per l'avvio del nodo.
  * Aggiunto l'entry point `waveshare_motor_driver` in `setup.py` e lo script wrapper in `CMakeLists.txt` per l'installazione in `lib/${PROJECT_NAME}`.

---

## 📈 ECO-2026-07-03-002: Lego Build HAT Deprecation & Waveshare Motor Driver Transition
* **Stato:** ✅ **Completato in Locale, in attesa di Sincronizzazione ed SSD**
* **Descrizione:** Rimozione definitiva di tutti i file e i riferimenti al Lego Build HAT precedentemente dismesso. Transizione completa di tutti i file di launch ROS 2 e degli script di sistema al driver `waveshare_motor_driver` con odometria attiva.
* **Modifiche apportate:**
  * Eliminati i file obsoleti: `smart_buildhat_driver.py`, `motor_control_node.py`, `test_lego_encoder_motor.py` e relativi wrapper in `scripts/`.
  * Rimosse le dipendenze da `package.xml`, `CMakeLists.txt` e `setup.py`.
  * Aggiornati 11 file di launch ROS 2 per avviare il nodo `waveshare_motor_driver` al posto del vecchio `motor_control_node`.
  * Modificata la configurazione dell'AI Orchestrator (`orchestrator.py`) per pubblicare messaggi di `Twist` direttamente su `/cmd_vel` anziché su `/bluedot_input`.
  * Aggiornata la lista dei nodi attesi in `system_inspector.py` e la documentazione del workspace (`WORKSPACE_STATE.md`, `files_topic.md`).
  * Riscritto `calibration_skill.py` (V2): calibrazione closed-loop iterativa (max 10 loop, stop < 2% errore) di raggio e traccia ruote confrontando `/vo/odom` (ground truth) e `/odom`, con profilazione della caduta di tensione a vuoto/carico e calcolo della velocità massima dinamica.
  * Modificato `waveshare_motor_driver.py` per includere la sottoscrizione a `/vo/odom`, un callback per la scrittura dinamica dei parametri ed un sistema di diagnostica attiva (stallo se le ruote non girano sotto sforzo, slittamento se le ruote girano ma il robot è fermo visivamente, sovraccarico se si ha una caduta di tensione della batteria $> 2.0$ V) con pubblicazione su `/diagnostics`.
  * Aggiornato `orchestrator.py` per ricevere i diagnostici di `motor_stall`, ordinare l'arresto d'emergenza (`emergency_stop()`) e pronunciare vocalmente l'avviso di ostacolo incontrato.

---

## 📈 ECO-2026-07-16-003: ESP32 Parser Compatibility, Right Wheel Kinematic Alignment and Calibration Tuning
* **Stato:** ✅ **Completato e Sincronizzato**
* **Descrizione:** Correzione di tre bug bloccanti sul modulo di attuazione Waveshare e calibrazione closed-loop: disallineamento cinematico dell'encoder destro, incompatibilità di stringhe seriali JSON con il parser ESP32, deadlock del client parametri ROS 2 e ottimizzazione delle velocità di calibrazione contro la forza di strizione.
* **Modifiche apportate:**
  * **Formattazione seriale (ESP32):** Modificato `send_speeds` in `waveshare_motor_driver.py` per includere `separators=(',', ':')` nella serializzazione JSON. Questo ha rimosso gli spazi bianchi dopo i due punti generati per default da Python, risolvendo il blocco dell'ESP32 che cercava rigidamente la sottostringa `"T":1`.
  * **Inversione cinematica:** Abilitato `invert_right_encoder := True` nei default del driver in `waveshare_motor_driver.py` e in `restart.sh`. Questo assicura che il conteggio dei tick per la ruota destra (che gira in senso inverso a causa del montaggio speculare) sia invertito a livello software prima di calcolare l'odometria lineare, prevenendo la cancellazione reciproca delle velocità delle ruote ($v_L + v_R = 0$) che causava un falso allarme stallo immediato.
  * **Risoluzione deadlock parameters:** Modificata l'interazione con il client dei parametri dinamici in `calibration_skill.py` (`get_motor_params` e `set_motor_params`) per fare il polling manuale e non bloccante dell'attributo `future.done()` con `await asyncio.sleep(0.05)`, evitando deadlock di integrazione tra il ciclo asincrono di `asyncio` ed il thread MultiThreadedExecutor di `rclpy`.
  * **Superamento strizione (Stiction):** Modificata la skill di calibrazione (`calibration_skill.py`) elevando i parametri di test standard a `0.45` m/s (velocità lineare) e `5.0` rad/s (velocità angolare) per garantire coppia di spunto sufficiente a muovere la ruota destra bloccata dall'attrito della riduzione. Aumentato il timeout di controllo stallo della skill a `2.0` secondi.

---

## 📈 ECO-2026-07-17-004: Correct Encoder Polarity Alignment
* **Stato:** ✅ **Completato e Sincronizzato**
* **Descrizione:** Corretto l'allineamento dei parametri di inversione degli encoder per riflettere le letture fisiche reali del firmware ESP32.
* **Modifiche apportate:**
  * Impostato `invert_left_encoder := False` e `invert_right_encoder := False` sia in `waveshare_motor_driver.py` che in `restart.sh` dopo aver verificato che il firmware ESP32 restituisce tick positivi per la rotazione in avanti su entrambi i canali.


## 📈 ECO-2026-07-17-005: Left Encoder Hardware Fault Diagnosis
* **Stato:** ⚠️ **Rilevato Errore Hardware (Azione Utente Richiesta)**
* **Descrizione:** Diagnosi di guasto hardware critico sul canale dell'encoder sinistro (`odl`).
* **Modifiche apportate:**
  * Eseguito test seriale a basso livello (`test_backward.py`) escludendo ROS 2.
  * Rilevato che, a fronte di rotazione fisica costante e corretta di entrambi i motori (movimento rettilineo), l'encoder sinistro ha registrato solo 27 tick contro i 4417 del destro.
  * Il malfunzionamento del canale in quadratura sinistro spiega perché l'odometria fusa sul robot calcola una rotazione fittizia a sinistra ad ogni spostamento lineare, disallineando la localizzazione della mappa.

---

## 📈 ECO-2026-07-18-007: Kinematic Symmetry, Wheel Geometry Tuning & Encoder Polarity Update
* **Stato:** ✅ **Completato e Sincronizzato**
* **Descrizione:** Allineamento finale della cinematica differenziale e dell'odometria ruote basato su test sistematici di isolamento di singola ruota. Rettifica dei parametri geometrici del robot misurati fisicamente e inversione polare dei canali encoder.
* **Modifiche apportate:**
  * **Configurazione Geometrica:** Impostato `wheel_radius := 0.0325` (diametro misurato 65mm) e `wheel_separation := 0.29` (carreggiata misurata 290mm) in `full_robot_launch.py`, `restart_hailo.sh` e nel firmware/driver.
  * **Correzione Cinematica in Python:** Modificata la formula cinematica in `waveshare_motor_driver.py` per inviare i comandi corretti di rotazione e traslazione. La ruota sinistra richiede comandi negativi sulla seriale per avanzare, mentre la destra positivi. La mappatura seriale è stata definita come: `L_serial = -v_left` e `R_serial = v_right`.
  * **Parsing Encoder:** Modificato il parsing del feedback in `waveshare_motor_driver.py` mappando `left_ticks = odl` e `right_ticks = odr`. Per garantire che i tick dell'encoder sinistro aumentino durante la marcia in avanti (che richiede un comando negativo fisicamente), è stato impostato `invert_left_encoder := True` e `invert_right_encoder := False`.
  * **Verifica del moto:** Eseguito test con successo (`test_forward_back.py`) con errore residuo di posa di soli 0.8cm e drift angolare minimo (-5.44°).

---

## 📈 ECO-2026-07-18-008: Nav2 Lifecycle Resolution, Visual Odometry Auto-Reset and Final Geometric Calibration
* **Stato:** ✅ **Completato, Sincronizzato e Compilato**
* **Descrizione:** Risoluzione del crash di startup dello stack Nav2, implementazione di un recupero automatico per il blocco del tracking dell'odometria visiva (VO) e calibrazione finale dei parametri geometrici fisici.
* **Modifiche apportate:**
  * **Auto-Reset VO:** Aggiunto un monitor di timeout del tracciamento in `oak_superpoint_odometry_node.cpp`. Se il tracking della camera va in stato `LOST` e non si relocalizza per 3 secondi (60 frame), il nodo forza autonomamente il reset delle pose storiche per ricominciare il tracking ORB dal frame corrente. Questo ha risolto lo stallo sistematico della VO a 0.000m.
  * **Nav2 Lifecycle Resolution:** Rimosse le stringhe fittizie `global_costmap/global_costmap` e `local_costmap/local_costmap` dalla lista `node_names` gestita da `lifecycle_manager_navigation` in `launch/custom_nav2_launch.py`. Questo ha eliminato la race condition che mandava in crash i server di navigazione all'avvio, portando Nav2 in stato `active` stabile.
  * **Inversione Encoder & Scala:** Rallineata la direzione del moto virtuale in Foxglove rispetto a quello reale impostando `invert_left_encoder := False` e `invert_right_encoder := True`.
  * **Calibrazione Finale Parametri Geometrici:** Impostato `ticks_per_rev := 594` (rapporto di scala reale 1x sul Pi5) per allineare l'odometria ruote con quella visiva. Tarato il raggio dinamico a `wheel_radius := 0.0361` e la carreggiata a `wheel_separation := 0.091` ottenendo un'accuratezza sul giro a 360° pari allo **0.7%** di errore (yaw residuo di solo -2.6°).
  * **Ottimizzazione Velocità MPPI:** Elevate le velocità massime operative di Nav2 in `nav2_params_jazzy.yaml` (`vx_max := 0.18 m/s` e `wz_max := 0.8 rad/s`) per consentire al planner locale di erogare coppia fluida superiore alla soglia di strizione minima (`0.15 m/s`).

---

## 📈 ECO-2026-07-22-009: MotionManager Architecture & Relative Movement Schema Extension
* **Stato:** ✅ **Completato, Sincronizzato e Validato**
* **Descrizione:** Implementazione del pacchetto cinematico `MotionManager` e correzione dei comandi di movimento relativo a distanza/angolo (es. "muoviti in avanti di 30cm", "gira a sinistra di 90°") sia per la Voice UI (Gemini Live API Tool Calls) che per l'input testuale.
* **Modifiche apportate:**
  * **Modulo Motion (`robot_ai.motion`):** Creato `MotionPrimitive`, `MotionSequence` e `MotionManager` in `robopy_controller/robot_ai/motion/`. Gestione unificata del calcolo dei tempi $t = d/v$ e $t = \theta/\omega$, conversioni tra metri, centimetri e gradi, e saturazione di sicurezza ($v \le 0.18$ m/s, $\omega \le 0.8$ rad/s).
  * **Skill Navigation:** Esteso `get_parameters_schema()` in `navigation_skill.py` con `distance_cm`, `distance_m` e `degrees`. Aggiornato `_parse_intent()` per ispezionare sia il `context` (dati strutturati da Gemini Live) sia regex avanzate su testo.
  * **Client Integrazione & Orchestratore:** Aggiornati `NavigationClient` in `navigation.py` e `_skill_move_handler` in `orchestrator.py` per inoltrare `distance_m` e `degrees` al motore `MotionManager`.
  * **Test Suite:** Verificati con successo i test unitari scratch in `test_motion.py`.

---

## 📈 ECO-2026-07-22-010: Stiction Compensation Kick & Torque Tuning for Low Battery Operation
* **Stato:** ✅ **Completato, Sincronizzato e Compilato**
* **Descrizione:** Risoluzione del problema di immobilità con ronzio motore (stiction lock su batterie parzialmente scariche) tramite impulso di spunto iniziale di coppia nei primi 150ms di movimento.
* **Modifiche apportate:**
  * **Stiction Kick:** Integrato in `MotionManager.execute_primitive` un impulso iniziale (150ms) con spunto di velocità $v_{kick} = 1.30 \cdot v_{cruise}$ (max 0.25 m/s) e $\omega_{kick} = 1.30 \cdot \omega_{cruise}$ (max 1.0 rad/s) per rompere l'attrito statico dei riduttori JGB37-520B prima di passare alla velocità di crociera nominale.
  * **Tuning Velocità di Crociera:** Elevate la velocità di crociera lineare di default a $0.18$ m/s (con limite max 0.25 m/s) e la velocità angolare a $0.6$ rad/s (con limite max 1.0 rad/s).

---

## 📈 ECO-2026-07-22-011: Left Wheel Kinematic Alignment & Real Encoder PID Closed-Loop Motion Control
* **Stato:** ✅ **Completato, Sincronizzato e Validato**
* **Descrizione:** Correzione della direzione della ruota sinistra per avanzamento concordato e introduzione dell'anello di controllo Closed-Loop PID su odometria reale per comandi di movimento relativo.
* **Modifiche apportate:**
  * **Cinematica Driver Motori:** Modificata la formula in `waveshare_motor_driver.py` impostando `v_L_cmd = v_L` e `v_R_cmd = v_R`. Questo assicura che comandando la marcia in avanti ($v > 0$), entrambe le ruote ricevano comandi di velocità positivi avazando in sintonia invece di far girare la ruota sinistra al contrario.
  * **Odometria Reale da Encoder:** Verificato che la pubblicazione dell'odometria `/odom` calcoli $\Delta s$ e $\Delta \theta$ rigorosamente dalle variazioni reali dei tick registrati dagli encoder fisici dei motori (`odl`, `odr`).
  * **Controllo PID Closed-Loop (`MotionManager`):** Integrata in `MotionManager.execute_primitive` e `NavigationClient` la sottoscrizione alla posa reale di `/odom`. Se il robot incontra resistenza o si muove lentamente nei piccoli spostamenti, il loop PID aumenta dinamicamente la velocità e la coppia erogata fino al raggiungimento esatto della distanza $D_{target}$ o dell'angolo $\theta_{target}$, arrestando i motori con precisione centrimetrica.

---

## 📈 ECO-2026-08-21-012: Dual-Rail Power Path OR-ing, Battery Management System & Voltage Feed-Forward PID
* **Stato:** ✅ **Completato e Validato**
* **Descrizione:** Implementazione completa dell'architettura di monitoraggio della batteria con gestione del doppio binario di alimentazione (Power Path OR-ing), compensazione feed-forward di tensione per il controllo motori e demone OS graceful shutdown.
* **Modifiche apportate:**
  * **Nodo ROS 2 BMS (`battery_manager_node.py`):** Creato nodo dedicato per campionamento a 5Hz, media mobile circolare (N=20) anti-sag, gestione stati (CHARGING, BATTERIA OK, ECO, ALLARME RIENTRO, CRITICO SHUTDOWN) con timer di persistenza a 3.0s.
  * **Inibizione Stato di Carica:** Rilevamento bus a 12.80V ($V \ge 12.70\text{V}$) con inibizione di tutti i trigger di docking e blocco allarmi di scarica; pubblicazione stato esplicito su Foxglove Studio (`/foxglove/power_status` e `/foxglove/battery_pct`).
  * **Feed-Forward Voltage PID Scaling:** Integrata formula di normalizzazione $PWM_{comp} = PWM \times (11.10\text{V} / V_{eff})$ in `waveshare_motor_driver.py` e `motion_manager.py` con clamping $[0.70, 1.40]$ e riduzione dinamica al 50% in modalità ECO ($V \le 10.20\text{V}$).
  * **Prevenzione Battery Cliff (DFMEA FM-SYS-004):** Chiuso failure mode con creazione del progetto `IMP-SYS-004_battery_cliff.md` e procedura di shutdown controllato `sudo systemctl poweroff` previa sincronizzazione file `sync`.
  * **Configurazione e Build:** Creato `battery_params.yaml`, wrapper `scripts/battery_manager_node`, e registrati entry points in `setup.py` e `CMakeLists.txt`.






