# 📡 Telemetria Continua e Auto-Tuning Dinamico (Self-Tuning Robot)

Questo documento definisce l'architettura concettuale e operativa per l'evoluzione di Marcus da robot a configurazione statica a **sistema auto-adattivo**. 
Lo scopo è mitigare i difetti di setup, le variazioni ambientali (es. rumore acustico, consistenza visiva delle stanze) e le divergenze dei parametri tramite l'analisi dei log in background e il tuning a caldo, come formalizzato nei Failure Modes: `FM-VUI-005`, `FM-VUI-006`, `FM-NAV-007` e `FM-NAV-008`.

---

## 1. Il Paradigma dell'Auto-Tuning a Caldo (Hot-Swapping)

I parametri vitali di un robot ROS 2 (RTAB-Map, Nav2, Audio) non dovrebbero essere vincolati ai file YAML di boot. L'ambiente fisico di una casa muta (giorno/notte, salotto ricco di feature visive vs corridoi spogli, rumore di fondo).

### Meccanismo Architetturale
Viene introdotto il concetto di **`marcus_autotuner_node`**, un demone in background che:
1. Sottoscrive i topic di diagnostica ad alta frequenza (es. `/rtabmap/info`, `/audio/rms`).
2. Valuta lo stato di salute dei sottosistemi rispetto a soglie critiche.
3. Se rileva un degrado prestazionale causato da parametri inadatti all'ambiente istantaneo, esegue una chiamata client asincrona al servizio `~/set_parameters` (standard `rcl_interfaces`) del nodo target per mutarne il comportamento a runtime, senza riavviare l'eseguibile.

---

## 2. Parametri Target (I Candidati all'Auto-Tuning)

Di seguito l'analisi ingegneristica dei parametri che ha senso manipolare a caldo e dei piani prova associati.

### A. RTAB-Map SLAM (Mitigazione `FM-NAV-007`)
Quando il robot passa da ambienti omogenei e non strutturati (muri bianchi) ad ambienti texturizzati.

| Parametro Target (ROS 2) | Effetto Fisico | Condizione di Trigger per la modifica a caldo |
| :--- | :--- | :--- |
| **`Vis/MinInliers`** | Determina quanti punti visivi validi servono per confermare il calcolo dell'odometria. | **Trigger di Abbassamento:** Se il topic `/rtabmap/info` riporta `inliers < 15` (es. in corridoio), abbassare il parametro a `10` per evitare la disconnessione del TF, a patto di ridurre la velocità lineare. |
| **`Kp/MaxFeatures`** | Quante feature ORB/SuperPoint estrarre dal frame. | **Trigger di Aumento:** Se il robot entra in una stanza buia o spoglia, aumentare da `500` a `1000` per massimizzare la probabilità di trovare spigoli utili, bilanciando col carico CPU. |
| **`RGBD/OptimizeMaxError`** | Rigidità di correzione del Grafo al rilevamento di un Loop Closure. | Se la covarianza dell'odometria ruote è salita alle stelle (slip confermato), aumentare questo parametro per consentire una "saldatura" forzata della mappa. |

### B. VUI & Audio Pipeline (Mitigazione `FM-VUI-005` e `FM-VUI-006`)
Il guadagno dei microfoni non può essere fisso se l'utente parla da distanze variabili o il rumore ambientale muta.

| Parametro Target | Effetto Fisico | Condizione di Trigger per la modifica a caldo |
| :--- | :--- | :--- |
| **`Capture Gain` (ALSA/PipeWire)** | Sensibilità del microfono ReSpeaker. | **Trigger Dinamico (AGC):** Se il nodo LangGraph rileva un "Semantic Coherence Drop" (il prompt ASR non ha senso sintattico a causa della voce lontana), alza l'input gain di 10dB per il turno conversazionale successivo. |
| **`Master Volume Limit`** | Prevenzione clipping e rottura altoparlante. | **Software DRC Limit:** Se il calcolo RMS dell'uscita TTS rileva campioni in clipping saturato (>98% range 16-bit PCM), l'audio limiter stringe permanentemente l'ampiezza massima (Dynamic Range Compression). |

---

## 3. Ottimizzazione Statistica in Background (Data Lake Offline)

Non tutti i parametri possono essere cambiati a caldo; alcuni richiedono analisi storiche (Mitigazione `FM-NAV-008`).

### Piani di Prova & Background Learning
- **Rosbag Data Lake:** Implementare un demone che registra a basso framerate (1Hz) i topic chiave: `/diagnostics`, `/mppi/path_deviations`, `/odom/filtered`.
- **Nightly Grid Search:** Mentre il robot è in ricarica (idle state), uno script Python statistico legge i dati del giorno.
- **Caso d'uso (MPPI Trajectory):** Se il log mostra che il robot esegue continuamente micro-correzioni angolari in prossimità dei muri, l'ottimizzatore calcola che il raggio `inflation_radius` della costmap è settato in modo sub-ottimale per la ristrettezza dei corridoi fisici di quella casa. Lo script aggiorna la configurazione YAML residente in `/config/nav2_params.yaml`, che verrà letta al riavvio del giorno successivo.

*Queste strategie sfidanti non sono semplici rattoppi, ma infondono in Marcus una vera resilienza operativa, in cui il software si modella fluidamente sulla realtà fisica dell'hardware e dell'ambiente.*
