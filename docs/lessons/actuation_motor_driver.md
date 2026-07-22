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
  3. **Segni di Attuazione:** Per avanzare, la ruota sinistra (serial `L`) richiede tensioni/velocità negative (`L < 0`), mentre la ruota destra (serial `R`) richiede tensioni positive (`R > 0`).
  4. **Segni di Encoder:** Muovendosi in avanti, l'encoder destro (`odr`) conta in positivo. L'encoder sinistro (`odl`) conta in negativo a causa della rotazione opposta del motore, richiedendo l'inversione software del segno.
* **Risoluzione permanente:**
  1. **Cinematica nel nodo:** Calcolare la cinematica standard $v_L, v_R$ e applicare le polarità fisiche direttamente prima di inviare: `self.send_speeds(-v_L, v_R)`.
  2. **Parsing Encoder:** Mappare direttamente `left_ticks = data.get('odl')` e `right_ticks = data.get('odr')`.
  3. **Parametri di Inversione:** Impostare `invert_left_encoder:=True` e `invert_right_encoder:=False` per far sì che entrambi i delta tick contribuiscano positivamente all'avanzamento lineare. Mantenere `invert_left_motor:=False` e `invert_right_motor:=False` poiché i segni sono già compensati dal driver.
  4. **Geometria reale:** Utilizzare sempre `wheel_radius:=0.0325` (diametro 65mm) e `wheel_separation:=0.29` (carreggiata 290mm) per evitare errori di scala della velocità lineare e angolare.

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





