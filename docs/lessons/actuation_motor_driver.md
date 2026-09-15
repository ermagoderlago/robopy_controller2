# Lezioni Apprese - Attuazione & Controllo (Waveshare ESP32)

Questo documento raccoglie le lezioni apprese sull'interfacciamento seriale a basso livello con la scheda Waveshare General Driver (ESP32) per la cinematica differenziale e la telemetria di Marcus.

---

## 🔌 Interfaccia Seriale e Inizializzazione Hardware

### Reset Seriale all'Avvio (DTR/RTS)
* **Contesto:** L'ESP32 a bordo della scheda Waveshare può rimanere in uno stato inconsistente o bloccato. Per garantire un avvio pulito, il nodo ROS 2 (`waveshare_motor_driver.py`) deve eseguire una sequenza di reset forzato agendo sulle linee DTR e RTS all'apertura del socket seriale:
  1. Impostare `DTR = True` e `RTS = True` (abbassa la linea EN resettando la scheda).
  2. Dormire per `0.1s`.
  3. Impostare `DTR = False` e `RTS = False` (rilascia EN, avviando l'ESP32).
  4. Attendere `3.0s` stabili per consentire il boot completo del firmware.
  5. Svuotare i buffer seriali (`reset_input_buffer()`, `reset_output_buffer()`).

### Handshake e Telemetria
* All'avvio, inviare esplicitamente i comandi JSON di abilitazione telemetria:
  * Abilitazione feedback continuo: `{"T":131,"cmd":1}\n`
  * Query iniziale chassis: `{"T":1001}\n`
* Attendere il pacchetto JSON di risposta con `"T": 1001` per validare l'handshake.

---

## ⚙️ Cinematica Differenziale e Watchdog

### Comandi di Velocità
* Il nodo traduce i comandi `/cmd_vel` in velocità lineare delle ruote sinistra (`L`) e destra (`R`) in m/s, trasmettendoli a 20Hz tramite il comando JSON:
  `{"T": 1, "L": v_L, "R": v_R}\n`
* Le velocità sono calcolate tramite cinematica differenziale classica:
  * $v_L = v - \frac{\omega \cdot W}{2.0}$
  * $v_R = v + \frac{\omega \cdot W}{2.0}$
  * Dove $W$ è la separazione tra le ruote (wheel separation).

### Watchdog di Sicurezza Hardware
* **Regola Permanente:** Il driver deve verificare periodicamente (a 10Hz) la ricezione dei comandi ROS. Se non arrivano nuovi comandi `/cmd_vel` per oltre 500ms, inviare immediatamente il comando seriale di stop `{"T": 1, "L": 0.0, "R": 0.0}\n` per prevenire derive incontrollate in caso di crash della brain.

---

## 📈 Parsing Telemetria ed Odometria

### Encoder e Wrap-Around
* La telemetria di ritorno riporta i tick cumulativi dell'encoder sinistro (`odl`) e destro (`odr`).
* Il codice deve calcolare la posa geometrica ($X, Y, \theta$) integrando lo spostamento dei tick per ogni intervallo temporale.
* **Filtro anomalie:** I salti repentini anomali dovuti a reset della scheda o overflow dei registri (es. delta tick maggiori del limite fisico di rotazione) devono essere intercettati e scartati per evitare balzi della stima di odometria.

### Monitoraggio Batteria
* La telemetria fornisce la tensione di batteria (`v`) in millivolt. Per una batteria LiPo 3S, impostare il monitoraggio per range nominali 9.9V (0%) - 12.6V (100%) per innescare allarmi vocali di sottotensione ed evitare il danneggiamento delle celle.

---

## 🛠️ Risoluzione Problemi e Compilazione (Troubleshooting)

### 1. Conflitto di Alimentazione e Reset della Rete
* **Sintomo:** Sessione SSH interrotta con `Connection closed` o `Resource temporarily unavailable` appena viene aperto `/dev/ttyUSB0` o durante il flashing.
* **Causa:** Con la batteria della Waveshare spenta, il chip CP2102 ed i gate driver tentano di assorbire energia dalla porta USB del Raspberry Pi. All'attivazione delle linee DTR/RTS o all'inizializzazione dei pin, il picco di corrente supera i limiti della porta USB del Pi, causando un drop di tensione sul bus da 3.3V che alimenta anche il chip WiFi Broadcom. Questo manda temporaneamente in crash la connettività di rete del Pi senza riavviare la CPU.
* **Risoluzione:** Accendere la batteria esterna della Waveshare prima di qualsiasi interazione seriale o di debug per separare i carichi di potenza.

### 2. Errore di compilazione con schema su Python 3.11+
* **Sintomo:** Durante la generazione delle dipendenze ESP-IDF con `esphome compile`, Python lancia `TypeError: expected string or bytes-like object, got 're.Pattern'`.
* **Causa:** Il modulo `schema` di ESP-IDF non gestisce i tipi pre-compilati `re.Pattern` introdotti in Python 3.11+.
* **Risoluzione:** Applicare una patch manuale sul modulo del virtualenv `__init__.py` di schema (`/home/robopy/.platformio/penv/.espidf-5.5.2/lib/python3.11/site-packages/schema/__init__.py`), sostituendo `, s.pattern_str` con `, (s.pattern_str.pattern if hasattr(s.pattern_str, "pattern") else s.pattern_str)` a riga 750.

### 3. Upgrade delle API LEDC (Arduino Core 3.0+)
* **Sintomo:** Errori di compilazione per funzioni non dichiarate `ledcSetup` e `ledcAttachPin`.
* **Causa:** La piattaforma `pioarduino` aggiornata usa il Core Arduino ESP32 v3.0+, che ha rimosso la gestione esplicita dei canali PWM.
* **Risoluzione:** Migrare all'API unificata basata direttamente sul pin fisico:
  * Sostituire `ledcSetup(channel, freq, res)` e `ledcAttachPin(pin, channel)` con `ledcAttach(pin, freq, res)`.
  * Sostituire `ledcWrite(channel, duty)` con `ledcWrite(pin, duty)`.

### 4. Connettore Motore con Encoder — Pinout Fisico Scheda Waveshare

**Contesto:** La scheda Waveshare General Driver for Robots ha DUE connettori motore PH2.0 6P:
- **Motor A** (`MA1/MA2/AC1/AC2/3V3/GND`): encoder su GPIO **35** (AC2, interrupt CHANGE) e **34** (AC1, direzione)
- **Motor B** (`MB1/MB2/BC1/BC2/3V3/GND`): encoder su GPIO **16** (BC2, interrupt CHANGE) e **27** (BC1, direzione)

**Ordine fisico pin da sinistra a destra (Motor B):** `MB1 | 3V3 | BC1 | BC2 | GND | MB2`

**Cavi JGB37-520B Songpuwei (colori non standard):** Verde=M1(+), Arancione=VCC, Giallo=C1, Bianco=C2, Rosso=GND(!), Nero=M2(-).

### 5. GPIO Input-Only su ESP32 — Nessun Pull-Up su 34/35/36/39

I GPIO **34, 35, 36, 39** non hanno transistor di pull-up. Chiamare `INPUT_PULLUP` è silenziosamente ignorato. Usare `INPUT` per Motor A. Usare `INPUT_PULLUP` per Motor B (GPIO 16/27 sono bidirezionali e supportano pull-up).

### 6. Diagnostica Encoder con Motore ad Alto Rapporto di Riduzione

Con JGB37-520B a 7RPM (riduzione ~143:1), **girare la ruota manualmente è impossibile** (gearbox autobloccante). Metodologia corretta: alimentare il motore via software (`{"T":1,"L":0.0,"R":0.3}`) e leggere i tick dalla telemetria `odr`. Risultato validato: **1179 tick in 5s al 30%** = encoder funzionante. ISR con direzione: `if (digitalRead(ENCA) == digitalRead(ENCB)) ticks++; else ticks--;`

### 7. Dead-zone Encoder e Zero-Velocity Lock (Odometria da Fermo)

* **Sintomo:** A robot fermo, l'odometria (e di conseguenza il TF `odom → base_link` e la camera in RViz) "impazzisce", accumulando drift casuale sulla posa x, y, θ.
* **Causa:** Gli encoder JGB37-520B producono rumore elettrico (bounce dei contatti, EMI dai motori) di ±1-3 tick anche a motori fermi. Senza filtro, ogni micro-variazione veniva integrata nella posa dall'odometria differenziale.
* **Risoluzione (triplice):**
  1. **`encoder_dead_zone` (parametro, default=2 tick):** Se entrambi i delta wheel sono ≤ alla soglia, vengono azzerati. A 20Hz, anche 0.01 m/s produce ~4-5 tick/ciclo, quindi 2 tick è sicuro.
  2. **Zero-velocity lock:** Quando `motors_stopped == True` (watchdog ha fermato i motori per mancanza di `cmd_vel` per >500ms), i delta tick vengono forzati a zero indipendentemente dalla lettura encoder. Difesa primaria.
  3. **Covarianze dinamiche:** L'Odometry message usa covarianze basse in movimento (1e-5) e alte da fermo (1e-3) per segnalare incertezza ai filtri EKF downstream.
* **Impatto:** Nessuna perdita di risoluzione in movimento; odometria completamente stabile a robot fermo.

### 8. Conflitto Doppio TF Broadcaster e Collisione Topic IMU

* **Sintomo:** In `fast_flow_launch.py`, il frame `odom → base_link` "sfarfalla" tra due pose diverse, causando movimenti erratici in RViz.
* **Causa:** Sia `waveshare_motor_driver` che `fast_flow_vo` pubblicavano entrambi il TF `odom → base_link`. RViz alternava tra le due stime di posa.
* **Causa secondaria:** Sia `waveshare_motor_driver` (IMU ESP32) che `madgwick_node` (IMU OAK-D) pubblicavano su `/imu/data`, interlacciando dati da due sensori diversi.
* **Risoluzione:**
  1. **`publish_tf` (parametro, default=True):** Aggiunto al `waveshare_motor_driver`. In `fast_flow_launch.py` e `restart.sh` impostato a `False` poiché `fast_flow_vo` is l'autorità TF per `odom → base_link`.
  2. **Rinomina topic IMU:** Il publisher IMU di `waveshare_motor_driver` rinominato da `/imu/data` a `/imu/esp32` per evitare collisione con il `madgwick_node`.

### 9. Diagnostica Attiva per Stallo, Slittamento ed Assorbimento Batteria (Collision Detection)
* **Contesto:** Il robot deve rilevare autonomamente se incontra un ostacolo invisibile ai sensori (es. urto meccanico o blocco ruota). La telemetria non ha sensori INA219 abilitati sul firmware, quindi l'assorbimento di potenza e l'ostacolo devono essere dedotti incrociando i dati sensoriali.
* **Metodologia:**
  1. **Stallo meccanico (Stall):** Se viene inviato un comando `/cmd_vel` non nullo ($v > 0.05$ m/s o $\omega > 0.1$ rad/s) ma le ruote non girano (velocità da encoder $\approx 0$ m/s) per $> 1.0$ s.
  2. **Slittamento (Slipping):** Se le ruote girano ($v_{odom} > 0.03$ m/s) ma il robot è fermo spazialmente rispetto all'ambiente (rilevato da `/vo/odom` con velocità $v_{vo} < 0.008$ m/s) per $> 1.0$ s.
  3. **Sovraccarico Batteria (Overload):** Se si registra una caduta di tensione della batteria $\Delta V = V_{idle} - V_{load} > 2.0$ V durante l'attuazione.
* **Reazione di sicurezza:** Il driver pubblica lo stato `ERROR` del nodo `"motor_stall"` sul topic ROS `/diagnostics`. L'orchestratore AI intercetta questo stato, attiva l'arresto d'emergenza (`emergency_stop()`) e notifica vocalmente l'utente dell'ostacolo rilevato, consentendo di ripianificare il percorso o cambiare comportamento.
* **Calibrazione closed-loop:** Per allineare l'odometria a ruote, la skill `calibration` esegue una calibrazione closed-loop iterativa (max 10 passi, tolleranza $< 2\%$ di errore residuo) confrontando gli spostamenti effettivi `/vo/odom` con quelli stimati `/odom`, correggendo dinamicamente `wheel_radius` e `wheel_separation` tramite parametri ROS 2.

### 10. Sensibilità al Formato Spazi del Parser JSON (ESP32)
* **Sintomo:** Le ruote non girano quando comandate tramite il nodo ROS 2, sebbene la comunicazione seriale sia attiva e i test seriali raw a basso livello funzionino.
* **Causa:** Il firmware ESP32 utilizza l'istruzione `input.indexOf("\"T\":1")` senza tolleranza per gli spazi. In Python, il comando `json.dumps()` per default serializza i dizionari inserendo spazi dopo i due punti (es. `{"T": 1, ...}`), impedendo all'ESP32 di riconoscere il token del comando e portando al rigetto silenzioso del pacchetto.
* **Risoluzione:** Serializzare i comandi nel driver forzando l'assenza di spazi tramite il parametro `separators=(',', ':')` in `json.dumps`.

### 11. Inversione Encoder, Allineamento Cinematico Definitivo e Parametri Geometrici Fisici
* **Sintomo:** Il robot ruota su se stesso, si muove all'indietro o curva in modo asimmetrico ad ogni comando lineare, accumulando enormi drift di localizzazione.
* **Causa:** Il cablaggio fisico speculare dei motori e degli encoder richiede direzioni e canali specifici:
  1. **Assegnazione Canali Seriale:** Il pin seriale `L` aziona la ruota sinistra fisica, mentre `R` aziona la ruota destra fisica.
  2. **Assegnazione Canali Encoder:** Il canale feedback `odl` appartiene all'encoder sinistro, mentre `odr` al destro.
  3. **Segni di Attuazione:** Per avanzare, la ruota sinistra (serial `L`) richiede tensioni/velocità negative (`L < 0` per montaggio speculare), mentre la ruota destra (serial `R`) richiede tensioni positive (`R > 0`).
  4. **Segni di Encoder (Fisica Reale):** Entrambi gli encoder magnetici contano in decremento (valori negativi) durante l'avanzamento lineare (`odl` decresce, `odr` decresce). Muovendosi all'indietro, entrambi contano in incremento (valori positivi). Pertanto, **entrambi i canali richiedono l'inversione software del segno**.
* **Risoluzione permanente:**
  1. **Cinematica nel nodo:** Calcolare la cinematica standard $v_L, v_R$ e applicare le polarità fisiche direttamente prima di inviare: `self.send_speeds(-v_L, v_R)`.
  2. **Parsing Encoder:** Mappare direttamente `left_ticks = data.get('odl')` e `right_ticks = data.get('odr')`.
  3. **Parametri di Inversione:** Impostare **`invert_left_encoder:=True`** e **`invert_right_encoder:=True`** in modo che per entrambi i canali il moto in avanti generi $\Delta s > 0$. In rotazione sul posto (w > 0, CCW / SX): la ruota destra avanza ($\Delta s_R > 0$), la sinistra retrocede ($\Delta s_L < 0$), generando $\Delta \theta = (\Delta s_R - \Delta s_L) / W > 0$ concorde con REP-103. Mantenere `invert_left_motor:=False` e `invert_right_motor:=False` poiché le polarità sono gestite nativamente da `send_speeds`.
  4. **Geometria reale:** Utilizzare `wheel_radius:=0.0335` (diametro 67mm calcolato) e `wheel_separation:=0.285` (carreggiata 285mm) per evitare errori di scala della velocità lineare e angolare.

### 12. Deadlock dei Client di Parametri ROS 2 su Event Loop di Asyncio
* **Sintomo:** Chiamate bloccanti all'interfaccia dynamic parameters tramite `await client.call_async(req)` sollevano eccezioni o causano un blocco asincrono (deadlock) all'avvio della calibrazione.
* **Causa:** Nei nodi ROS 2 scritti in Python che integrano cicli asincroni tramite `asyncio` su thread separati dal MultiThreadedExecutor di `rclpy`, fare l'await di un `rclpy.task.Future` direttamente all'interno dell'event loop di `asyncio` può portare a conflitti di esecuzione.
* **Risoluzione:** Effettuare un polling non bloccante controllando lo stato `future.done()` in un loop con `await asyncio.sleep(0.05)` prima di estrarre il risultato con `future.result()`.

### 13. Forza di Strizione e Parametri di Test per la Calibrazione
* **Sintomo:** Le ruote non iniziano a ruotare o ruotano in ritardo nel test di calibrazione closed-loop a bassa velocità (0.2 m/s), attivando la protezione di stallo.
* **Causa:** La ruota destra presenta una forza di strizione (static friction) del gearbox superiore a quella sinistra. Al 20% di PWM (0.2 m/s), il motore destro rimane fermo finché non supera la soglia di attrito, accumulando un ritardo di attivazione superiore a 1 secondo.
* **Risoluzione:** Incrementare la velocità lineare di calibrazione a `0.45` m/s (45% PWM) e quella angolare a `5.0` rad/s (40% PWM) per garantire una coppia di spunto sufficiente a superare istantaneamente la strizione su entrambi i motori. Aumentare il timeout del controllo di stallo a `2.0` secondi per gestire l'inerzia iniziale senza falsi positivi.

### 14. Limiti di Coppia Dinamica e Protezione Sottotensione (Saturazione a 0.40 m/s)
* **Sintomo:** Il robot rettilineo risponde in modo corretto a velocità moderate (0.15 m/s - 0.28 m/s), ma a velocità lineari elevate (>= 0.40 m/s) decelera drasticamente o si arresta quasi completamente (registrando solo 6cm di spostamento).
* **Causa:** L'assorbimento di corrente simultaneo di entrambi i motori a elevato PWM causa una caduta di tensione della batteria. Se la tensione di bordo scende sotto una soglia critica, i driver Waveshare attivano la protezione da sottotensione tagliando temporaneamente l'alimentazione ai motori per prevenire il reset dell'ESP32.
* **Risoluzione:** Limitare la velocità massima lineare comandata dal planner locale di Nav2 a `0.18 m/s` (e quella angolare a `0.8 rad/s`), dove i motori lavorano stabilmente a piena coppia, preservando la linearità geometrica dell'odometria ruote e prevenendo sovraccarichi elettrici.

### 15. Allineamento dei Segni degli Encoder per la Visualizzazione Virtuale (Foxglove)
* **Sintomo:** Il robot reale si sposta in avanti e ruota in senso antiorario, ma la visualizzazione virtuale in Foxglove si muove all'indietro e ruota in senso orario.
* **Causa:** I canali di quadratura degli encoder fisici cablati sull'ESP32 leggono i fronti con polarità invertite rispetto alla convenzione ROS standard (destrorsa) per il movimento delle ruote.
* **Risoluzione:** Invertire i flag degli encoder nel driver impostando `invert_left_encoder := False` e `invert_right_encoder := True`. Questo ha riallineato il segno matematico dell'odometria fusa, garantendo la concordanza perfetta della posa 2D in Foxglove rispetto alla realtà fisica.

### 16. Risoluzione dei Conflitti di Stato del Lifecycle Manager di Nav2
* **Sintomo:** I nodi Nav2 (`controller_server`, `planner_server`, ecc.) falliscono l'avvio ed entrano permanentemente in stato `unconfigured` a causa di un crash di bond.
* **Causa:** La presenza di costmap interni come `global_costmap/global_costmap` e `local_costmap/local_costmap` nella lista dei nodi gestiti (`node_names`) del `lifecycle_manager_navigation` forza transizioni non registrate, mandando in timeout il bond.
* **Risoluzione:** Rimuovere le sotto-istanze costmap dall'elenco del manager in `custom_nav2_launch.py`, lasciando che siano gestite in cascata dai rispettivi nodi padre planner e controller.

### 17. Architettura di Movimento Relativo (`MotionManager`), Impulso di Spunto (Stiction Kick) e Soglia Batteria
* **Sintomo:** I comandi vocali o testuali di avanzamento con misura (es. "muoviti in avanti di 30cm") producevano un rumore sordo / vibrazione ma nessun movimento effettivo delle ruote.
* **Causa:** 
  1. I parametri `distance_cm`, `distance_m` e `degrees` non erano esposti nello schema LLM di `NavigationSkill`.
  2. I riduttori JGB37-520B con elevato rapporto di riduzione (~143:1) presentano una forza di attrito statico (stiction). Con batterie LiPo non a piena carica (tensione sotto ~11.0V - 11.2V), la velocità di crociera nominale (0.15 m/s) non erogava una coppia sufficiente per sbloccare l'attrito iniziale delle ruote.
* **Risoluzione:** 
  1. Implementato il pacchetto `robot_ai.motion` (`MotionPrimitive`, `MotionSequence`, `MotionManager`) con un **impulso di spunto iniziale (Stiction Compensation Kick)** nei primi 150ms ($v_{kick} = 0.22 - 0.25$ m/s, $\omega_{kick} = 0.8 - 1.0$ rad/s) che sblocco l'attrito meccanico, seguito dalla marcia di crociera ($0.18$ m/s).
  2. Elevata la velocità di crociera predefinita a $0.18$ m/s e la velocità angolare a $0.6$ rad/s.
  3. Aggiornato lo schema Function Declaration di `NavigationSkill` per esporre `distance_cm`, `distance_m` e `degrees`.

### 18. Alignment Cinematica Ruota Sinistra e Controllo PID Closed-Loop su Odometria Reale Encoder
* **Sintomo:** Nel movimento in avanti, la ruota destra avanzava ma la ruota sinistra girava all'indietro (causando rotazione sul posto o nessun avanzamento). Inoltre, nei piccoli spostamenti il robot si fermava prima del target per attrito.
* **Causa:** 
  1. In `waveshare_motor_driver.py`, il comando per la ruota sinistra venendo calcolato come `v_L_cmd = -v_L` forzava la marcia indietro quando comandato in avanti ($v > 0$).
  2. L'esecutore di movimento relativo era aperto in tempo (open-loop $t=d/v$) anziché in feedback closed-loop basato sulla distanza/angolo reale misurati dagli encoder.
* **Risoluzione:**
  1. Corretta la cinematica differenziale in `waveshare_motor_driver.py`: sia `v_L_cmd = v_L` che `v_R_cmd = v_R` ricevono ora comandi positivi per avanzare concorde.
  2. Integrato in `MotionManager` un **anello di controllo Closed-Loop PID** agganciato al topic `/odom` (encoder reali). Se il robot trova resistenza o rallenta, il guadagno PID ed il termine integrativo $K_i \cdot \int e$ aumentano automaticamente la coppia/velocità finché l'odometria misurata non registra esattamente il raggiungimento della distanza $D_{target}$ (o dell'angolo $\theta_{target}$), fermando i motori immediatamente all'arrivo.

### 19. Calibrazione Empirica Risoluzione Encoder Ticks/Rev per la Scheda Waveshare ESP32
* **Sintomo:** Comandando un avanzamento di 30 cm con odometria `/odom` corretta, il robot percorreva fisicamente a terra ~38 cm (o ~2 metri quando configurato a 594 ticks/rev).
* **Causa:** Il firmware ESP32 della scheda Waveshare accumula ed invia i fronti di quadratura degli encoder con un fattore di scala reale di **`70` ticks per giro completo di ruota** (con ruote da $D = 65\text{ mm}$, circonferenza $C = 0.2042\text{ m}$).
* **Risoluzione:** Tarato il parametro `ticks_per_rev := 70` sia in `restart.sh` che in `waveshare_motor_driver.py`, ed impostati i guadagni PID a $K_p = 2.5, K_i = 0.8$. Lo spostamento calcolato da `/odom` risponde ora con precisione millimetrica allo spostamento misurato col metro sul pavimento.

### 20. Fusione Sensoriale EKF (Odometria Ruote + IMU OAK-D Lite BNO085) e Nome Nodo YAML
* **Sintomo:** Il nodo `robot_localization` `ekf_node` si avviava senza errori ma non pubblicava il TF `odom → base_link` né l'odometria fusa `/odometry/filtered`, facendo fallire RTAB-Map SLAM per mancanza di TF.
* **Causa:** 
  1. Il file `ekf.yaml` aveva come intestazione radice `ekf_localization:`, mentre il nome registrato nel grafo ROS 2 dal binario `ekf_node` è `/ekf_filter_node`. Di conseguenza, il parser dei parametri ROS 2 ignorava silenziosamente tutta la configurazione.
  2. L'IMU della scheda Waveshare ESP32 non trasmette pacchetti telemetrici con accelerazione/giroscopio (`/imu/esp32` silenzioso).
* **Risoluzione:**
  1. Rinominata la radice YAML in `ekf_filter_node:` in `ekf.yaml`.
  2. Reindirizzata la fusione IMU sull'**IMU integrata della OAK-D Lite (`/oak/imu/data`)**, equipaggiata con sensore BNO085 che trasmette a **42 Hz** stabili.
  3. Configurato l'EKF per utilizzare la velocità angolare giroscopica $w_z$ dall'OAK IMU unita alla posizione lineare $(x,y)$ ed alla velocità $v_x$ dall'odometria ruote, generando `/odometry/filtered` a 30Hz ed il TF `odom → base_link`.

---

<a id="power-path-oring"></a>
### 21. Gestione Alimentazione Power Path OR-ing, Bus 12.80V in Carica, Filtro Anti-Sag e Compensazione Feed-Forward di Tensione
* **Contesto Architetturale:** Il robot mobile adotta un'architettura di alimentazione a doppio binario (Power Path OR-ing tramite diodi ideali):
  - **Funzionamento a batteria:** Pacco Li-ion 3S (Range operativo 9.0V - 12.6V, Nominale 11.1V, scarica 10.2V @20%, docking 9.9V @12%, shutdown 9.0V @0%).
  - **Funzionamento da rete / cuccia:** Bus alimentato da alimentatore 24V con Step-Down regolato a **12.80V**.
* **Vincolo Elettrico di Carica ($V \ge 12.70\text{V}$):** Quando il robot è agganciato alla cuccia o all'alimentatore, la tensione misurata sul bus è quella dell'alimentatore esterno (12.80V) e non riflette la tensione interna della batteria (isolata dal diodo ideale 2 e caricata tramite CC-CV separato). Il nodo `battery_manager_node`:
  - Imposta `power_supply_status = POWER_SUPPLY_STATUS_CHARGING`.
  - Inibisce tassativamente tutti i trigger di rientro in base (`/robot/docking/trigger`) e shutdown critico.
  - Pubblica su Foxglove Studio lo stato `"IN CARICA (12.8V)"` con gauge fissa al 100% (o valore convenzionale SoC = -1.0).
* **Filtro Rumore e Anti-Sag con Timer di Persistenza (3.0s):**
  - Le correnti di spunto dei motori possono indurre cadute istantanee di tensione (*voltage sag*). Per prevenire falsi allarmi, il nodo implementa una media mobile (Moving Average su buffer circolare di 20 campioni a 5Hz).
  - Gli allarmi di transizione critica (`ALLARME RIENTRO` a 9.90V e `CRITICO SHUTDOWN` a 9.00V) richiedono che la tensione filtrata rimanga costantemente sotto la soglia per almeno **3.0 secondi consecutivi**.
* **Limitatore Dinamica Bassa Batteria (< 20% / 10.20V):**
  - All'ingresso in `ECO MODE`, il nodo pubblica su `/speed_limit` di Nav2 e scala la velocità massima e l'accelerazione al **50%** ($0.5 \times v_{max}$), abbattendo il voltage sag per garantire che il robot raggiunga la cuccia di ricarica.
* **Compensazione Tensione Feed-Forward:**
  - Nel controller motori `waveshare_motor_driver.py` e nel modulo `MotionManager`:
    $$PWM_{compensato} = PWM_{PID} \times \text{clamp}\left(\frac{11.10\text{V}}{V_{effettiva}}, 0.70, 1.40\right)$$
    dove $V_{effettiva} = 12.80\text{V}$ se $V \ge 12.70\text{V}$ (mains power), altrimenti $V_{effettiva} = \text{clamp}(V_{misurata}, 9.0, 12.8)$.
  - Questa compensazione garantisce che a batteria scarica (es. 10.0V) i motori ricevano una tensione equivalente a quella nominale (11.1V), mantenendo la velocità e la dinamica cinematiche stabili e costanti.
* **Fattore di Conversione Telemetria ESP32 Board (3S Pack):**
  - Il firmware ESP32 della scheda Waveshare trasmette il valore telemetrico della batteria `v` moltiplicato per il partitore 3S (es. `36300` per un pacco a 12.10V effettivi).
  - La normalizzazione in Volt opera tramite la relazione $V = \frac{v_{raw}}{3000.0}\text{ V}$, mappando accuratamente il range reale $9.00\text{V} - 12.60\text{V}$ ($v_{raw} \in [27000, 37800]$) e la tensione di carica $12.80\text{V}$ ($v_{raw} \approx 38400$).

---

### 22. Calibrazione Sperimentale CPR Motori (657 CPR), Asimmetria GPIO ESP32 e Formattazione JSON Compatta
* **Risoluzione Reale Encoder a Banco:**
  - Misurato con rotazione manuale e controllo PID di precisione a 360° il valore reale: **`657` ticks/giro ruota** (motori JGB37-520 con riduzione 1:30 ed 11 poli magnetici a 2 fronti di interrupt).
* **Asimmetria Hardware GPIO ESP32 (GPIO 34/35 vs GPIO 16/27):**
  - La ruota destra (Motor 2) è collegata a GPIO 16 e 27, che supportano la resistenza di pull-up interna (`INPUT_PULLUP`). Conta stabilmente tutti i 657 tick/giro a qualsiasi velocità.
  - La ruota sinistra (Motor 1) è collegata a GPIO 34 e 35 (pin analogici input-only dell'ESP32 privi di resistori di pull-up interni). A rotazione manuale molto lenta, i fronti dei sensori Hall open-collector possono fluttuare; a regime di moto alimentato il conteggio è agganciato.
  - **Mitigazione Architetturale Definitiva:** Per la stima dell'angolo d'imbardata durante le svolte sul posto, si integra a 200 Hz il giroscopio dell'IMU OAK-D Lite (con correzione dell'angolo di pitch a 8.0°), rendendo l'orientamento SLAM completamente immune a discrepanze o scivolamenti ruote.
* **Formattazione Rigida del Parser JSON ESP32:**
  - Il firmware C++ dell'ESP32 cerca la sottostringa `"\"T\":1"` senza spazi.
  - Se il codice Python invia `json.dumps({"T": 1, ...})` con spaziatura standard, il comando viene scartato silenziosamente.
  - È **obbligatorio** formattare i payload seriali con `json.dumps(cmd, separators=(',', ':'))` o f-string compatti `f'{{"T":1,"L":{l:.3f},"R":{r:.3f}}}\n'`.

---

<a id="smart-standby-motion-gating"></a>
### 23. Smart Standby, Salvaguardia Usura LiDAR e Hardware Motion Gating (`/robot/motion_gate`)
* **Problema:** Quando il robot sosta a lungo (inattività > 2 minuti), mantenere il rotore ottico del LiDAR RPLIDAR C1 in rotazione continua a vuoto provoca usura precoce dei cuscinetti e del diodo laser, consumo parassita della batteria LiPo e accumulo ridondante di nodi nel database SLAM RTAB-Map.
* **Architettura a Risparmio Energetico:**
  - Il nodo `sensor_standby_manager.py` controlla l'immobilità fisica via IMU (`/oak/imu/data`), comandi `/cmd_vel` e odometria `/odom_wheel`.
  - Dopo 120s di assenza di perturbazioni esterne (accelerazioni $\le g \pm 0.35\text{ m/s}^2$ e $\omega \le 0.15\text{ rad/s}$), arresta il motore del LiDAR (`/stop_motor`) e congela RTAB-Map (`/rtabmap/pause`).
* **Motion Gating Deterministico al Risveglio:**
  - Su risveglio (innescato da spinta/urto rilevato da IMU o da ricezione di `/cmd_vel`), il rotore del LiDAR C1 impiega circa 0.8–1.2s per raggiungere i 10 Hz di regime.
  - Durante lo spin-up, `waveshare_motor_driver.py` sottoscrive `/robot/motion_gate` (`std_msgs/msg/Bool`): se `motion_gate == False`, memorizza il target `cmd_vel` ma inibisce fisicamente il moto delle ruote inviando $0.0\text{ m/s}, 0.0\text{ rad/s}$.
  - Appena `sensor_standby_manager` valida l'arrivo dei primi 2 pacchetti `/scan`, il gate si apre (`motion_gate = True`) e il robot eroga fluidamente il movimento alle ruote senza rischio di collisioni a cieco.

### 24. Correzione Assegnazione Hardware Canali Motore ESP32 (L/R) e Polarità Encoder (FM-MOT-005)
* **Sintomo:** Quando l'operatore comanda una svolta a destra (`angular.z < 0`), il robot fisico sterza a sinistra, e viceversa. Inoltre, durante le svolte la mappa generata da RTAB-Map si deforma ad arco e l'odometria salta bruscamente.
* **Causa Radice:**
  1. **Inversione Cablaggio Seriale:** Sulla scheda Waveshare General Driver (ESP32), il canale PWM etichettato `L` pilota fisicamente il motore della ruota **DESTRA**, mentre il canale `R` pilota fisicamente il motore della ruota **SINISTRA**.
  2. **Inversione Telemetria Encoder:** Il contatore `odl` corrisponde all'encoder della ruota **DESTRA**, mentre `odr` corrisponde all'encoder della ruota **SINISTRA**.
  3. L'omissione dello swap canale nel driver faceva sì che la cinematica inviasse $v_L$ (ruota sinistra) al motore destro, e $v_R$ (ruota destra) al motore sinistro, invertendo fisicamente il verso di imbardata del robot rispetto ai comandi e alla convenzione ROS standard (REP-103).
* **Risoluzione Permanente:**
  1. In `send_speeds(left, right)`:
     Mappare `"L": round(duty_right, 4)` e `"R": round(duty_left, 4)`.
  2. In `process_encoder_feedback(left_ticks, right_ticks)`:
     Calcolare `delta_ticks_right = left_ticks - prev_left_ticks` e `delta_ticks_left = right_ticks - prev_right_ticks`.
  3. Mantenere `invert_left_encoder := False` e `invert_right_encoder := False`.
  4. In `src/fast_flow_vo_node.cpp`: sigillare il Motion Gate azzerando esplicitamente $\Delta t$ e $\Delta \text{yaw}$ quando il robot è fermo (`!isRobotMoving()`), impedendo al rumore subpixel della camera di accumulare deriva a veicolo fermo.

---

<a id="filtraggio-jitter-hall-a-fermo"></a>
### 25. Filtraggio Jitter di Bordo dei Sensori Hall a Veicolo Fermo (FM-ACT-009)
* **Sintomo:** A robot fermo sul pavimento senza comandi `/cmd_vel` attivi, si verificava occasionalmente una micro-deriva o un conteggio spurio di pochi tick (es. 4-5 tick su una ruota) che provocava un impercettibile avanzamento virtuale del robot sulla mappa. Muovendo leggermente la ruota a mano o azionando il motore per un istante, il fenomeno spariva.
* **Analisi Causale Radice:**
  1. **Oscillazione sul Fronte di Transizione Magnetica:** Quando la ruota si arresta esattamente in corrispondenza del fronte di commutazione tra un polo magnetico Nord e Sud della ruota fonica, il sensore di Hall open-collector può oscillare avanti e indietro attorno alla soglia logica a causa di vibrazioni ambientali minime o rumore sui GPIO dell'ESP32.
  2. **Accoppiamento Logico Errato (AND) nel Filtro di Standstill:** Il driver precedente valutava la soppressione del rumore con la condizione congiunta `if abs(delta_ticks_left) <= 3 and abs(delta_ticks_right) <= 3:`. Se una sola ruota generava 4 tick mentre l'altra era perfettamente a 0, la condizione `AND` falliva per entrambi i canali, lasciando trafilare i 4 tick e integrando uno spostamento lineare fittizio.
* **Risoluzione Implementata:**
  1. **Disaccoppiamento per Singola Ruota:** Il filtraggio a veicolo fermo (`motors_stopped == True`) è ora applicato in modo strettamente indipendente a ciascuna ruota (`if abs(delta_ticks_left) <= deadband: delta_ticks_left = 0`, e analogamente per la ruota destra).
  2. **Introduzione Parametro `standstill_encoder_deadband`:** Parametro configurabile impostato di default a **`8` tick** ($\approx 2.5\text{ mm}$). Qualsiasi oscillazione di bordo o rumore elettrico inferiore a 8 tick a motori fermi viene completamente azzerata.
  3. **Zero-Velocity Lock Lineare:** Se i tick filtrati sono nulli, l'odometria impone $\Delta s = 0.0$ e $v = 0.0$, garantendo la perfetta immobilità della posa e della mappa SLAM durante le soste.

---

<a id="hall-standstill-jitter"></a>
### 26. Runaway ad Alta Frequenza dell'Encoder a Veicolo Fermo e Multi-Tier Standstill Lock (FM-MOT-006)
* **Sintomo Empirico (Foxglove & Log):**
  - Con robot fisicamente immobile sul pavimento e comandi `/cmd_vel` nulli (`v=0, w=0`), il grafico Foxglove "Velocità comandata vs reale" mostrava improvvisamente una raffica continua di picchi negativi su `odom vx` tra **-0.5 m/s e -3.5 m/s** (`media_1788897266317.png`).
  - Nei log di `waveshare_motor_driver.log`, `odl` (ruota sinistra) rimaneva immobile a `13957`, mentre `odr` (ruota destra) correva oltre `410,254` accumulando **~308 tick ogni 50 ms** (~6,000 pulse/sec).
  - L'odometria lineare integrava $\Delta s \approx -0.035\text{ m}$ per ciclo, teletrasportando virtualmente il robot all'indietro a velocità folle e distruggendo la mappa SLAM 2D.
* **Causa Radice Fisica:**
  - Se il motore si arresta con il magnete permanente esattamente allineato sulla soglia di commutazione del sensore di Hall, la tensione di uscita rimane nello stato indeterminato (1.65V) in assenza di sufficiente isteresi di Schmitt o pull-up hardware aggressivo sull'ingresso GPIO ESP32.
  - L'ingresso digitale dell'ESP32 commuta ad alta frequenza per rumore termico/elettrico, scatenando una pioggia ininterrotta di interrupt/conteggi PCNT a circa 6 kHz su un solo canale.
* **Architettura di Protezione Multi-Tier:**
  1. **Tier 1 (Absolute Standstill Zero-Velocity Lock):** Quando `motors_stopped == True` (motori disalimentati da watchdog 500ms o assenza di comando `/cmd_vel`), i delta tick sono incondizionatamente forzati a `0` e `delta_s = 0.0`, `v_robot = 0.0`. Nessun runaway o jitter hardware a fermo può alterare le coordinate $X, Y$ o la velocità odometrica.
  2. **Tier 2 (Red-Zone Velocity Outlier Clamp):** In conformità a `SPEC-01` ($v_{max} = 0.40\text{ m/s}$), qualsiasi delta tick per ciclo che superi il limite fisico massimo di 0.45 m/s (~80 tick in 50ms) viene classificato come spike anomalo e scartato (`[ENCODER_GLITCH]`).
  3. **Tier 3 (Kinematic Asymmetry Filter vs IMU Gyro 42Hz):** Un robot differenziale rigido non può muovere una ruota a 0.5 m/s mantenendo l'altra ferma senza ruotare a $\omega = (v_R - v_L)/W \approx 1.8\text{ rad/s}$ ($100^\circ/\text{s}$). Se il giroscopio OAK-D Lite misura $|\omega_{IMU}| < 0.2\text{ rad/s}$, il burst asimmetrico a ruota singola viene identificato come glitch elettrico e neutralizzato (`[ENCODER_ASYMMETRY]`).

---

<a id="camera-mast-imu-vibration"></a>
### 27. Oscillazione della Velocità Angolare (odom wz) da Vibrazione dell'Asta Telecamera e Odometria Cinematica Pura (FM-MOT-007)
* **Sintomo Empirico (Foxglove & Log):**
  - Durante il movimento in linea retta avanti/indietro (`cmd vx = 0.20-0.30 m/s`, `cmd wz = 0.00 rad/s`), la velocità lineare `odom vx` risultava pulita, ma la velocità angolare `odom wz` (linea rossa su Foxglove) oscillava violentemente tra **-0.20 rad/s e +0.16 rad/s** ($\pm 11^\circ/\text{s}$).
  - Visivamente, il robot si muoveva perfettamente dritto sul pavimento, ma la mappa SLAM 2D in RTAB-Map sfarfallava e oscillava a destra e a sinistra ("la mappa sballa a destra e sinistra"), accumulando errori di rotazione e deformando i corridoi.
* **Causa Radice Fisica:**
  - La telecamera OAK-D Lite è montata su un'asta strutturale alta ($Z=0.2616\text{ m}$) inclinata a $8^\circ$ di pitch verso l'alto.
  - Il rotolamento delle ruote sul pavimento e le micro-rugosità inducono una vibrazione di flessione/torsione sull'asta della camera con frequenza nell'intorno dei 10-20 Hz.
  - La funzione `oak_imu_callback` integrava in continuo l'orientamento odometrico `self.theta += w * dt_imu` direttamente dal giroscopio asse Z della OAK-D Lite a 42 Hz.
  - Questo introduceva continuamente oscillazioni angolari artificiali nell'odometria ruote `/odom`, ingannando RTAB-Map che vedeva il robot ruotare pur procedendo dritto.
* **Risoluzione Architetturale (Odometria Cinematica Teorica Pura):**
  1. **Attivazione `use_cmd_vel_odometry:=True`:** L'odometria (`/odom`) viene generata per integrazione diretta a punto medio del comando di velocità cinematico teorico (`/cmd_vel`). Quando il robot avanza dritto (`cmd_w = 0`), la velocità angolare `odom wz` è rigorosamente $0.00\text{ rad/s}$ (flatline perfetta su Foxglove).
  2. **Disaccoppiamento del Giroscopio OAK-D (`use_imu_for_rotation:=False`):** La vibrazione meccanica della camera viene completamente isolata dall'odometria delle ruote.
  3. **Affidamento Chiusura Anello a RPLIDAR C1 (ICP Scan Matching):** RTAB-Map SLAM utilizza il laser scan matching 2D a 360° (`RGBD/NeighborLinkRefining: "true"`) con 12Hz di scan rate e precisione millimetrica su pareti e ostacoli fisici, compensando all'istante qualsiasi minimo scostamento reale senza subire il rumore di sensori a bordo chassis.

---

<a id="migrazione-pcnt-hardware"></a>
### 28. Migrazione al Modulo Hardware PCNT (Pulse Counter) ESP32 e Soppressione Definitiva del Jitter Hall (FM-ACT-009)
* **Problema Storico:** Il firmware ESP32 precedente utilizzava banali interrupt software (`attachInterrupt(..., CHANGE)`) su singolo pin. Quando una ruota sostava su una transizione magnetica o in presenza di rumore PWM del ponte H, il pin oscillava ad alta frequenza generando migliaia di interrupt/sec nella stessa direzione (~6.000 impulsi/s a veicolo fermo), provocando falsi movimenti e balzi odometrici.
* **Risoluzione Radice Hardware (ESP-IDF v5 Pulse Counter API):**
  1. **Decodifica Quadratura Hardware 4X:** Implementata la decodifica a quadratura a 4 fronti (rising/falling edge di entrambi i canali A e B) interamente gestita nel silicio periferico dell'ESP32 tramite unità PCNT (`pcnt_unit_m1`, `pcnt_unit_m2`) e canali incrociati (`chan_a`, `chan_b`).
  2. **Digital Glitch Filter Integrato (2000 ns):** Attivato il filtro hardware sul silicio (`pcnt_unit_set_glitch_filter` con `max_glitch_ns = 2000`). Qualsiasi spike capacitivo indotto dal PWM a 5 kHz o micro-rimbalzo inferiore a 2.0 microsecondi viene scartato a livello gate fisico.
  3. **Zero Carico CPU ESP32:** Nessuna interruzione software durante il conteggio degli encoder; la telemetria si limita a interrogare i registri accumulati a 20 Hz (`pcnt_unit_get_count`).
  4. **Watchdog Hardware:** Integrato timeout 500ms lato ESP32 per arrestare autonomamente i motori se la comunicazione seriale si interrompe.
* **Risultati Sperimentali Validati:**
  - A robot fermo: **zero assoluto di drift odometrico (0 tick)** per qualsiasi durata di sosta.
  - In rotazione ruota comandata (+30%, +50%, -50% duty): conteggio perfettamente simmetrico e fluido, privo di perdite di passo o balzi.

---

<a id="motor-stall-safety-memory"></a>
### 29. Rilevamento Stallo Meccanico (`motor_stall`), Annullamento Istantaneo Nav2 e Protezione Cognitiva nell'Amigdala (FM-MOT-004)
* **Contesto e Problema:**
  - Se il robot incontra un ostacolo insormontabile o incastra le ruote contro uno stipite, i motori tentano di erogare la coppia richiesta assorbendo corrente di stallo con rischio di surriscaldamento dei driver TB6612FNG o caduta distruttiva di tensione LiPo.
  - In precedenza, `orchestrator.py` eseguiva solo `emergency_stop()` e TTS, ma non inviava l'annullamento a Nav2 (costringendo ad attendere 5 secondi di timeout dal `progress_checker`), e l'anomalia non veniva memorizzata nella memoria a lungo termine del robot.
* **Architettura a Triplo Anello Implementata:**
  1. **Anello Sensoriale (`waveshare_motor_driver.py`):**
     - Watchdog a 10 Hz analizza costantemente la cinematica:
       * *Stallo Meccanico:* $|v_{cmd}| > 0.05\text{ m/s}$ ma $|v_{robot}| < 0.005\text{ m/s}$ per $t > 1.0\text{ s}$.
       * *Slittamento Ruote:* $|v_{robot}| > 0.03\text{ m/s}$ ma $|v_{VO}| < 0.008\text{ m/s}$ per $t > 1.0\text{ s}$.
       * *Sovraccarico Elettrico:* Caduta di tensione della LiPo $\Delta V > 2.0\text{ V}$ per $t > 1.0\text{ s}$.
     - Pubblica su `/diagnostics` con `name: "motor_stall"` e `level: DiagnosticStatus.ERROR (2)`.
  2. **Anello Esecutivo & Nav2 (`orchestrator.py`):**
     - Al ricevimento di `motor_stall` a livello `ERROR`:
       * Esegue `reactive_safety.emergency_stop()`.
       * Se Nav2 è in navigazione attiva (`nav_client.is_navigating`), schedula immediatamente `nav_client.cancel_navigation()` per interrompere l'action goal senza attendere il timeout passivo.
       * Emette avviso vocale TTS: *"Attenzione. Rilevato blocco o ostacolo nei motori. Fermo il movimento per sicurezza."*
       * Invia evento `DIAGNOSTIC_UPDATE` sull'`EventBus` e salva l'anomalia via `memory_manager.store_background(..., "system_event")`.
  3. **Anello Cognitivo & Memoria Indelebile (`cognitive_amygdala.py` & `memory_manager.py`):**
     - L'Amigdala (`cognitive_amygdala.py`) intercetta `motor_stall` a livello `ERROR` ed innesca l'**Amygdala Hijack** (`_trigger_hijack`):
       * Taglia l'output `/cmd_vel` a ripetizione e invia richiesta di cancellazione globale a Nav2.
       * Salva istantaneamente in **ChromaDB** un ricordo protetto permanente da trauma (`"amygdala_protected": "true"`, `"synaptic_strength": 100.0`, `"lambda_decay": 0.0`).
       * `MemoryManager` include ora `MemoryType.SYSTEM_EVENT` nella classe di protezione assoluta (nessun decadimento durante il Sogno Notturno).
     - Riarmo automatico: Quando il telaio si libera e il driver pubblica `DiagnosticStatus.OK`, l'Amigdala riarma lo stato da `HIJACK` a `CALM`.

---

<a id="esp32-pid-short-brake"></a>
### 30. Anello Chiuso di Velocità Diretto su ESP32 (50Hz Feedforward+PI) e Dynamic Short-Brake su TB6612FNG (FM-MOT-008)
* **Sintomi Osservati:**
  1. *Asimmetria in Moto:* In rettilineo una ruota gira leggermente più veloce dell'altra, provocando una deriva angolare e costringendo il robot a curvare se non continuamente compensato.
  2. *Torsione all'Arresto:* Al comando di sosta (`cmd_vel = 0`), una ruota sembra spinta dall'inerzia più avanti dell'altra, producendo un colpo di frusta o leggera rotazione parassita del robot.
* **Analisi della Causa Radice Fisica:**
  - *Sintomo 1 (Asimmetria):* In anello aperto (duty cycle PWM inviato grezzamente), due motori DC anche dello stesso lotto presentano minime differenze di attrito nei cuscinetti, usura delle spazzole, resistenza ohmica degli avvolgimenti e tolleranze meccaniche degli ingranaggi. Senza feedback in tempo reale, velocità uguali in $m/s$ non corrispondono mai a duty PWM identici.
  - *Sintomo 2 (Torsione da Inerzia):* Nel driver precedente, lo stop veniva attuato con `DIR1=LOW, DIR2=LOW, PWM=0`. Nel ponte ad H TB6612FNG, questa combinazione disattiva tutti i MOSFET del ponte, ponendo le uscite motore in **alta impedenza (High-Z Coast Mode)**. I motori girano a vuoto per inerzia: il motore con minore attrito residuo prosegue per qualche millisecondo in più, causando la torsione asimmetrica del robot a veicolo fermo.
* **Risoluzione Implementata:**
  1. **Dynamic Short-Brake Mode sul Ponte TB6612FNG:**
     - Quando il target di velocità è zero ($|duty| < 0.01$), il firmware porta entrambi i pin di direzione a `DIR1=HIGH, DIR2=HIGH` con `PWM=255`.
     - Questo attiva contemporaneamente entrambi i MOSFET low-side verso GND, mettendo in **cortocircuito controllato le bobine del motore**.
     - La forza contro-elettromotrice generata dalla rotazione inerziale (Back-EMF) produce un'immediata coppia frenante proporzionale alla velocità angolare, bloccando rigidamente e istantaneamente entrambe le ruote senza alcun tempo di scivolamento.
  2. **Anello Chiuso di Velocità a 50 Hz a Bordo Microcontrollore ESP32:**
     - Esecuzione di un regolatore Feedforward + PI ogni 20 ms basato sui tick hardware PCNT:
       $$\text{PWM} = \text{target} \times 255.0 + K_p \cdot e + K_i \cdot \int e\,dt + K_d \cdot \frac{de}{dt}$$
     - Parametri tarati: $K_p = 3.20, K_i = 0.22, K_d = 0.04$, anti-windup clamp $\pm 75$.
     - Il feedforward garantisce la tensione di base immediata (zero ritardo); la componente PI corregge in soli 20 ms qualsiasi minima discrepanza di carico o attrito tra le ruote, garantendo una perfetta marcia in linea retta.
  3. **Integrazione Bidirezionale ROS 2 (`waveshare_motor_driver.py`):**
     - Parametri dinamici: `enable_esp32_pid` (default: `True`), `esp32_pid_kp`, `esp32_pid_ki`, `esp32_pid_kd`.
     - Handshake e tuning a caldo via seriale JSON con protocollo `{"T":133,"pid":1,"kp":3.2,"ki":0.22,"kd":0.04}\n`.
     - Stato dell'anello chiuso integrato nel messaggio diagnostico `/diagnostics` (`esp32_pid_active`).

---

<a id="real-encoder-odometry-switch"></a>
### 31. Passaggio all'Odometria Reale su Spostamenti da Encoder PCNT (FM-NAV-015, FM-MOT-008)
* **Contesto Storico:**
  - In precedenza, a causa del segnale encoder interrotto/flottante sul canale C2 (GPIO 35) e dei burst di falso movimento/rumore Hall da fermo, il robot utilizzava l'odometria teorica da `/cmd_vel` (`use_cmd_vel_odometry := True`).
  - L'odometria teorica era immune al rumore degli encoder, ma presentava un limite fisico insito: non misurava l'effettivo spostamento reale delle ruote su terreno reale, non rilevava micro-slittamenti su moquette/pavimento, e soffriva di drift in curva a catena aperta prima dei vincoli ICP/LiDAR.
* **Risoluzione con Hardware PCNT + Anello Chiuso:**
  1. **Attivazione Odometria Reale:**
     - Impostato `use_cmd_vel_odometry := False` e `use_encoder_for_linear := True` come configurazione nominale sia in `waveshare_motor_driver.py` che in `restart_hailo.sh`.
     - Impostato `invert_left_encoder := False` e `invert_right_encoder := False`: con il nuovo firmware PCNT, entrambi i canali incrementano regolarmente per moto in avanti ($dl > 0, dr > 0$) e decrementano in retromarcia ($dl < 0, dr < 0$).
  2. **Integrazione a Punto Medio Runge-Kutta 2° Ordine:**
     - Calcolo metrico rigoroso per cinematica differenziale su ogni ciclo:
       $$\Delta s_L = \Delta T_L \times \frac{2 \pi R_{wheel}}{CPR}, \quad \Delta s_R = \Delta T_R \times \frac{2 \pi R_{wheel}}{CPR}$$
       $$\Delta s = \frac{\Delta s_R + \Delta s_L}{2}, \quad \Delta \theta = \frac{\Delta s_R - \Delta s_L}{W_{separation}}$$
       $$\theta_{mid} = \theta + \frac{\Delta \theta}{2}$$
       $$x \leftarrow x + \Delta s \cos(\theta_{mid}), \quad y \leftarrow y + \Delta s \sin(\theta_{mid}), \quad \theta \leftarrow \text{atan2}(\sin(\theta + \Delta \theta), \cos(\theta + \Delta \theta))$$
       $$v_{robot} = \frac{\Delta s}{\Delta t}, \quad w_{robot} = \frac{\Delta \theta}{\Delta t}$$
  3. **Zero Phantom Drift a Fermo:**
     - Il Tier 1 Zero-Velocity Standstill Lock blocca $\Delta T_L = 0, \Delta T_R = 0$ quando `motors_stopped` è attivo, azzerando qualsiasi jitter magnetico residuo.
  4. **Guardia Anti-Asimmetria Selettiva:**
     - Il filtro di asimmetria tra ruote (Tier 3) opera ora esclusivamente in marcia rettilinea comandata ($|\omega_{cmd}| < 0.10\text{ rad/s}$) con IMU attiva, senza interferire con le rotazioni intenzionali sul posto comandate da Nav2.

---

<a id="scurve-jerk-and-yaw-fusion"></a>
### 32. Profiler S-Curve Jerk Limiter di 2° Ordine e Fusione Complementare Yaw Chassis (FM-MOT-009, FM-NAV-016)
* **Contesto e Problemi Meccanici / Odometrici:**
  - *Jerk e Vibrazione Albero Sensori (FM-MOT-009):* Le transizioni di velocità con slew-rate del primo ordine (accelerazione costante a gradino) generavano un jerk teoricamente infinito ad ogni variazione di comando. Questo produceva un colpo di frusta meccanico sul telaio, facendo oscillare l'albero su cui sono montati LiDAR C1 e camera OAK-D Lite. L'oscillazione beccheggio/rollio perturbava l'odometria visiva (VO) e causava falsi ostacoli nella costmap locale 2.5D.
  - *Deriva Differenziale su Curvature e Pavimenti Lisci (FM-NAV-016):* L'odometria differenziale da ruote presuppone rotolamento perfetto senza strisciamento. Su pavimenti lisci o giunti, le ruote soffrono di micro-slittamenti angolari che deviano lo yaw del veicolo, portando a errori cumulativi nella posa cartografica prima della chiusura loop SLAM.
* **Soluzione Implementata nel Driver ROS 2 (`waveshare_motor_driver.py`):**
  1. **S-Curve Jerk Limiter Continuo di 2° Ordine ($C^1$ Continuity):**
     - Sostituito il vecchio limitatore a gradino con integrazione continua di accelerazione e jerk:
       $$\text{err} = \text{target\_duty} - \text{current\_duty}$$
       $$a_{des} = \text{clamp}\left(\frac{\text{err}}{\tau_{accel}}, -a_{max}, +a_{max}\right), \quad \tau_{accel} = 0.15\text{ s}, \quad a_{max} = 5.0\text{ duty/s}$$
       $$\Delta a = \text{clamp}(a_{des} - a_{curr}, -j_{max} \cdot \Delta t, +j_{max} \cdot \Delta t), \quad j_{max} = 25.0\text{ duty/s}^2$$
       $$a_{curr} \leftarrow a_{curr} + \Delta a, \quad \text{duty}_{curr} \leftarrow \text{duty}_{curr} + a_{curr} \cdot \Delta t$$
     - *Proprietà fisiche:* L'accelerazione non salta mai istantaneamente; la curva di accelerazione cresce e decresce ad $S$, eliminando le vibrazioni strutturali dell'albero sensori.
     - *Safety Stop Immediato:* Quando il comando è fermo ($v=0, \omega=0$), il ciclo azzera immediatamente $duty$ ed $accel$ per garantire il tempo di arresto hardware di emergenza senza code di rampa.
  2. **Fusione Complementare Yaw ($\Delta \theta_{fused}$):**
     - L'ESP32 include un'IMU montata rigidamente sullo chassis (giroscopio asse Z, registrato come `gy` nel frame REP-103).
     - Integrazione complementare pesata:
       $$\Delta \theta_{fused} = \alpha \cdot (\omega_{chassis} \cdot \Delta t) + (1 - \alpha) \cdot \Delta \theta_{wheel}, \quad \alpha = 0.88$$
     - Il giroscopio chassis risponde istantaneamente alle perturbazioni dinamiche ad alta frequenza senza risentire dello slittamento ruote; l'odometria differenziale garantisce la stabilità a lungo termine a bassa frequenza.
  3. **Auto-Bias Tracking Stazionario:**
     - Quando `motors_stopped = True`, le letture del giroscopio vengono filtrate con un esponenziale a media mobile (EMA $\alpha = 0.05$):
       $$\text{bias}_{gyro} \leftarrow 0.95 \cdot \text{bias}_{gyro} + 0.05 \cdot \omega_{raw}$$
     - Durante la marcia (`motors_stopped = False`), il bias stimato viene sottratto dalla velocità angolare, azzerando la deriva di zero dell'IMU.
  4. **Degrado Trasparente su Telemetria Stale:**
     - Se i pacchetti IMU chassis non arrivano per oltre $250\text{ ms}$, il driver esclude automaticamente il termine giroscopico, passando al 100% differenziale ruote senza interruzioni del servizio né deadlock.
  5. **Risoluzione Deadlock Lock Seriale:**
     - `self.serial_lock` convertito da `threading.Lock()` a `threading.RLock()`, risolvendo il freeze all'avvio in cui `connect_serial()` acquisiva il lock ed invocava `send_esp32_pid_config()` che richiedeva lo stesso lock.

---

<a id="pid-speed-duty-stop-tuning"></a>
### 33. Reattività Immediata dello Stop, Ricalibrazione Metrica Duty per Anello Chiuso ESP32 e Filtro Direzionale Tier 5 (FM-MOT-001, FM-MOT-007, FM-MOT-008)
* **Sintomi Rilevati nei Test Fisici:**
  1. *Overshoot di Distanza a Bassa Velocità:* Inviando un comando di micro-movimento $v = 0.08\text{ m/s}$ per $0.50\text{ s}$ (teorico $\sim 4\text{ cm}$), il robot percorreva fisicamente a terra quasi $40\text{ cm}$ ad una velocità reale di oltre $0.35\text{ m/s}$.
  2. *Ritardo di Arresto all'Invio di Zero:* Al rilascio del tasto o termine dello script, i motori continuavano a ruotare per mezzo secondo extra prima di fermarsi bruscamente per intervento del watchdog (500 ms).
  3. *Oscillazione Iniziale di Heading:* Nei primi cicli di marcia rettilinea, la ruota sinistra registrava occasionalmente tick negativi fittizi, inducendo una deviazione iniziale di rotta prima della correzione PID.
* **Causa Radice:**
  1. *Filtro Anti-Chatter Bloccante sui Comandi Zero:* In `cmd_vel_callback`, la guardia `if (now - last_active_cmd_time) < 0.35: return` scartava incondizionatamente qualsiasi messaggio con $v=0, \omega=0$ se inviato entro 350 ms dall'ultimo comando attivo. Di conseguenza, i comandi di stop inviati immediatamente dopo l'avanzamento venivano tutti ignorati, costringendo il veicolo a muoversi fino all'intervento del watchdog a 500 ms (durata totale $0.5\text{s} + 0.5\text{s} = 1.0\text{s}$).
  2. *Offset Statico di Attrito (`motor_min_duty_cycle = 0.18`) e Scala Massima Errata:* `speed_to_duty()` sommava artificialmente un duty minimo del 18% mappando $[0, v_{max}]$ su $[0.18, 1.0]$. Inoltre, sul firmware ESP32, il setpoint target dei tick a 50Hz è calcolato come $\text{target\_ticks} = \text{target\_duty} \times 118$, dove 118 tick/20ms corrispondono fisicamente a $v_{100\%} \approx 1.89\text{ m/s}$ (e non al tetto software di $0.40\text{ m/s}$). Il duty inviato per soli 0.08 m/s era quindi $0.344$ (34% PWM), che sull'ESP32 imponeva un setpoint di oltre $0.65\text{ m/s}$.
  3. *Distorsione Feed-Forward di Tensione su PID:* La moltiplicazione di `target_duty` per il fattore di tensione $11.10\text{V} / 12.60\text{V} = 0.88$ in modalità PID alterava il setpoint di velocità anziché scalare solo il duty open-loop.
  4. *Glitches di Transizione sul Livello di Direzione PCNT:* L'encoder del motore 1 usa il pin di direzione `PIN_M1_DIR1` per determinare il conteggio up/down del contatore hardware PCNT. A riposo (`motors_stopped`), dopo la frenata dinamica di 250ms il pin transita a `LOW`. Nel primo millisecondo di avvio in avanti, prima che il comando forzi `DIR1=HIGH`, eventuali fronti sul canale B venivano contati come negativi.
* **Risoluzione Implementata:**
  1. *Stop Diretto Senza Ritardi con Filtro a 2 Campioni:* Sostituito il blocco temporale di 350ms con un contatore di comandi zero consecutivi: un singolo zero isolato durante uno stream attivo viene filtrato, ma 2 comandi zero consecutivi (o un comando dopo >200ms) attivano immediatamente l'arresto hardware, l'S-Curve e lo short brake a terra.
  2. *Mapping Fisico Rigoroso $v \to \text{duty}$ per ESP32 PID:* In modalità ad anello chiuso (`enable_esp32_pid = True`), la velocità $v$ viene mappata direttamente sul setpoint tick dell'ESP32 tramite la scala fisica reale:
     $$v_{100\%} = \frac{118 \times \text{meters\_per\_tick}}{0.020\text{ s}} \approx 1.890\text{ m/s}, \quad \text{duty} = \frac{\text{clamp}(v, 0, v_{max})}{v_{100\%}}$$
     A $v = 0.08\text{ m/s}$, il duty calcolato è esattamente $0.0423$, generando un setpoint di 5.0 tick/20ms che il PID 50Hz insegue con precisione millimetrica. L'offset artificiale `motor_min_duty_cycle` è preservato esclusivamente come fallback in modalità open-loop.
  3. *Feed-Forward Tensione Confinato all'Open-Loop:* La scalatura per tensione batteria opera solo se `enable_esp32_pid = False`, lasciando inalterato il setpoint del PID.
  4. *Filtro Direzionale Tier 5 in Odometria:* In marcia rettilinea comandata ($v > 0.02, |\omega| < 0.10$), i tick con segno opposto al moto vengono forzati a zero, eliminando gli spike di rotazione fittizi all'avvio.
* **Risultato del Collaudo Fisico su Marcus:**
  - Comando $v = 0.08\text{ m/s}$ per $0.50\text{ s}$ (teorico $4.00\text{ cm}$): spostamento reale registrato da `/odom` pari a **$4.23\text{ cm}$** (accuratezza **$94.6\%$**), deviazione angolare di soli **$1.03^\circ$** e arresto Short-Brake immediato senza alcun intervento del watchdog.

---

<a id="motor-asymmetry-and-heading-stabilizer"></a>
### 34. Calibrazione Empirica Asimmetria Motori, Stiction Differenziale, Trim Direzionali e Stabilizzatore Attivo di Heading (FM-MOT-008, ECO-2026-09-15-003)
* **Sintomi Rilevati nei Test Fisici:**
  1. *Veering Sistematico a Destra:* Inviando un comando di moto rettilineo in avanti ($v = +0.10\text{ m/s}$), il robot partiva regolarmente ma deviava costantemente verso destra, accumulando una rotazione oraria di $-15.14^\circ$ in soli 0.8s.
  2. *Asimmetria Marcata nelle Rotazioni sul Posto:* Il comando di svolta a sinistra ($\omega = +0.35\text{ rad/s}$) produceva una rotazione di $+111.94^\circ$, mentre la svolta a destra ($\omega = -0.35\text{ rad/s}$) produceva soli $-96.80^\circ$, con una distorsione netta di oltre $15^\circ$ in senso orario.
  3. *Traslazione Laterale Parassita:* Durante le rotazioni pure su se stesso, il robot scivolava lateralmente ($dx = -3.34\text{ cm}, dy = -7.30\text{ cm}$), perturbando la convergenza di AMCL e RTAB-Map.
* **Diagnosi e Misure di Banco (`measure_motor_stiction.py`):**
  - Eseguito sweep empirico su Marcus analizzando velocità effettiva e tick encoder per duty PWM:
    * *In avanti:* A parità di duty (0.15), la ruota sinistra sviluppa $0.241\text{ m/s}$ (452 tick) mentre la ruota destra sviluppa $0.159\text{ m/s}$ (297 tick). La ruota sinistra ha circa il 35-40% di attrito meccanico in meno rispetto alla destra ($R/L \approx 0.66$).
    * *In retromarcia:* A duty -0.15, la ruota sinistra gira a $-0.399\text{ m/s}$ (748 tick) mentre la destra a $-0.167\text{ m/s}$ (313 tick). In reverse il riduttore sinistro gira oltre 2 volte più veloce del destro, mentre il destro soffre di forte stiction inversa.
  - *Perché il PID ESP32 non compensava:* Il motore 1 ha un canale encoder non funzionante (hardware single-channel su GPIO 35). Il firmware ESP32 usava `PIN_M1_DIR1` per il verso PCNT: in closed-loop, le correzioni negative invertivano il conteggio hardware innescando un feedback positivo distruttivo.
* **Architettura di Risoluzione nel Driver ROS 2 (`waveshare_motor_driver.py`):**
  1. **Disattivazione del PID Difettoso su ESP32:**
     - Imposto `enable_esp32_pid:=False` di default (e via `restart_hailo.sh`), escludendo l'anello chiuso hardware corrotto.
  2. **Ricalibrazione Curva Duty Open-Loop:**
     - Mappatura $[0, 0.40\text{ m/s}]$ su $[0.095, 0.280]$ duty. Il minimo a 0.095 rompe la stiction del motore destro sotto carico, eliminando l'eccesso di velocità del vecchio offset al 18%.
  3. **Trim Direzionali Indipendenti (`left_motor_trim`, `left_motor_trim_rev`, `right_motor_trim_rev`):**
     - Marcia avanti sinistra scalata con `left_motor_trim = 0.73` (riduzione 27% per pareggiare il riduttore destro).
     - Marcia indietro sinistra scalata con `left_motor_trim_rev = 0.65` (riduzione 35% contro il runaway in reverse).
     - Marcia indietro destra incrementata con `right_motor_trim_rev = 1.25` (boost 25% per vincere la stiction inversa).
  4. **Stabilizzatore Attivo di Heading a 42Hz (`enable_heading_stabilizer = True`):**
     - Closed-loop PI a 42Hz alimentato dal giroscopio IMU OAK-D Lite (`/oak/imu/data`):
       * In marcia rettilinea ($|v| > 0.015, |\omega| < 0.05$): setpoint $\omega_{target} = 0.0$, applicando correzione differenziale $corr = K_p \cdot (0 - \omega_z) + K_i \cdot \int (0 - \omega_z)dt$ ($K_p=0.12, K_i=0.04$). Se il robot accenna a virare a destra ($\omega_z < 0$), riduce la ruota sinistra e incrementa la destra in tempo reale.
       * In rotazione sul posto ($|\omega| \ge 0.05, |v| < 0.02$): impone l'inseguimento esatto di $\omega_{target} = \omega_{cmd}$, garantendo rotazioni perfettamente simmetriche e centrate.
  5. **Fallback OAK-D IMU nella Fusione Complementare Yaw (`src_rot = "OAK_FUSED"`):**
     - Qualora l'IMU chassis ESP32 non trasmetta telemetria, il filtro complementare ($\alpha = 0.88$) impiega automaticamente il giroscopio a 42Hz della camera OAK-D Lite anziché regredire alla sola sottrazione di tick ruote.
* **Verifica Finale su Hardware Marcus (`test_rotation_symmetry.py`):**
  - **Avanzamento Rettilineo (+0.12 m/s per 0.8s):**
    * Distanza percorsa: **$7.30\text{ cm}$** (controllo stabile della velocità, runaway eliminato).
    * Deriva angolare dyaw: ridotta da **$-15.14^\circ$** a soli **$-0.51^\circ$** (veering verso destra totalmente azzerato!).
    * Giroscopio IMU velocità angolare media: **$-0.5\text{ deg/s}$** (traiettoria rigorosamente dritta).
  - **Rotazioni sul Posto ($\pm 0.50\text{ rad/s}$):**
    * Turn Left: **$+10.91^\circ$**
    * Turn Right: **$-14.28^\circ$**
    * Delta di asimmetria ridotto da oltre **$15.14^\circ$** a soli **$3.37^\circ$**!
    * Traslazione laterale spuria in svolta a destra azzerata ($dx = +1.20\text{ cm}, dy = +0.29\text{ cm}$).

### 35. Boost di Coppia Minima per Rotazione sul Posto (Anti Tire-Scrub Stiction - FM-MOT-008)
* **Sintomo:** Nelle rotazioni pure sul posto a bassa velocità ($v \approx 0, \omega \in [0.2, 0.4]\text{ rad/s}$), il robot sembrava privo di potenza o "bloccato", faticando a girare e stallando.
* **Causa Meccanica:** A differenza della marcia avanti dove le ruote rotolano, la rotazione sul posto impone alle gomme uno sfregamento laterale (*tire scrub*) contro il pavimento con un coefficiente di attrito statico $\mu_s$ molto superiore. A $\omega = 0.30\text{ rad/s}$, la velocità tangenziale ruote era di appena $0.042\text{ m/s}$, che mappava a un duty PWM open-loop di solo $0.09\text{-}0.11$. Dopo i trim hardware (0.65 su sinistra), il duty crollava a $0.074$, al di sotto della soglia di breakout.
* **Soluzione Implementata:**
  - Introdotto parametro `open_loop_spin_min_duty:=0.18` (range $[0.16, 0.22]$).
  - Quando $|v| < 0.02\text{ m/s}$ e $|\omega| \ge 0.05\text{ rad/s}$, il calcolo della tensione viene promosso direttamente al range di spin $[0.18, 0.28]$.
  - Applicato un pavimento assoluto post-trim: $\|duty_{left}\| \ge 0.13$ e $\|duty_{right}\| \ge 0.15$, garantendo coppia abbondante per vincere il tire scrub su qualsiasi pavimento/tappeto senza stalli.

