# Original User Request

## Initial Request — 2026-09-17T12:39:12Z

# Teamwork Project Prompt

Use a full team of agents (Perception/NPU, Navigation Nav2/NOMAD, Cognitive VUI, Verification QA).

Sistema integrato di percezione e navigazione autonoma per Marcus: mappatura a frontiera autonoma, localizzazione multimodale (VPR CosPlace 512D su NPU Hailo-10H e LiDAR ToF al buio), gestione semantica degli ambienti su mappa globale continua, dialogo vocale bidirezionale (VUI / TRINITY) e ricerca visuale di target tramite NOMAD e YOLO.

Working directory: robopy_controller
Integrity mode: development

## Vincoli di Sistema e Risorse Hardware (Controlled Infrastructure)
- **Host Pi 5 (4GB RAM)**: Tetto massimo 4GB RAM. Mappatura 2.5D (vietato STVL 3D). Persistenza obbligatoria su SSD NVMe (`/mnt/ssd/`). Nessun ricaricamento frammentato o distruttivo di mappe in navigazione.
- **Sensori**: RPLIDAR C1 (360° ToF laser a 905nm su `/dev/rplidar`), OAK-D Lite RGB-D (Depth 16UC1), microfoni ReSpeaker USB con AEC hardware.
- **Acceleratore NPU**: Hailo-10H PCIe per estrazione vettoriale CosPlace 512D e rilevamento oggetti YOLOv8.

## Requirements

### R1. Mappa Globale Continua con Partizioni Semantiche degli Ambienti
- Gestire un'unica mappa metrica 2D continua per ciascun piano dell'edificio, rappresentata in formato standard Nav2 (YAML + PGM).
- Mantenere un registro descrittivo semantico delle stanze (`rooms_metadata.yaml` / SQLite WAL) associato alla mappa:
  - Bounding box poligonali e centroide geometrico di ciascun ambiente (es. `cucina`, `salotto`, `camera da letto`, `corridoio`).
  - Impronte descrittive per il riconoscimento rapido (cluster di embedding vettoriali VPR 512D).
  - Firme geometriche LiDAR 360° per il riconoscimento al buio.
- Consentire la creazione di mappe separate esclusivamente in presenza di piani disconnessi o dislivelli non superabili a ruote.

### R2. Macchina a Stati per la Mappatura Autonoma Sicura & Frontier Exploration
- Verificare la luminosità ambientale prima dell'avvio: inibire la mappatura visiva se il livello medio di illuminazione dell'immagine è inferiore alla soglia operativa.
- Implementare il protocollo di sicurezza HRI vocale: richiesta esplicita di autorizzazione alla mappatura, attesa con sollecito a 120s e aborto automatico con standby a 300s se non confermato.
- Eseguire l'esplorazione autonoma mediante Frontier Exploration, identificando le celle di frontiera libere verso le regioni inesplorate e navigando fino all'esaurimento delle frontiere raggiungibili.
- Alla conclusione dell'esplorazione: fermare i motori, eseguire l'ottimizzazione globale del grafo SLAM (RTAB-Map bundle adjustment), esportare la mappa 2D su SSD (`/mnt/ssd/maps/<ambiente>.yaml`), estrarre le impronte VPR della stanza e notificare vocalmente il completamento.

### R3. Riconoscimento Ambienti Multi-Modale & Localizzazione Resiliente al Buio
- In condizioni di illuminazione diurna/sufficiente: identificare la stanza corrente entro 3 secondi combinando le coordinate di posa con il matching dei descrittori VPR CosPlace 512D (NPU Hailo-10H) e chiusura del loop RTAB-Map.
- Al buio totale: disattivare l'analisi visiva per evitare falsi positivi; identificare l'ambiente mediante rotazione controllata a 360° con LiDAR ToF e convergenza rapida del filtro particellare AMCL (o Scan Matching) rispetto al perimetro geometrico delle stanze note (traccia di covarianza < 0.08).
- Risolvere automaticamente il problema del "kidnapped robot" all'avvio in qualsiasi stanza nota.

### R4. Interfaccia Vocale Naturale & Situational Awareness (VUI / TRINITY)
- Rispondere fluidamente a domande dell'operatore sulla posizione e lo stato:
  - *"Dove ti trovi?"* (riporta stanza corrente, prossimità e mappa attiva).
  - *"In quale mappa stai navigando?"* (riporta nome mappa e livello di accuratezza della localizzazione).
  - *"Cosa vedi?"* (acquisisce frame visivo, esegue inferenza semantica e descrive vocalmente la scena).
- Interpretare comandi vocali per mappare un nuovo ambiente, aggiornare/sostituire la mappa di una stanza esistente o navigare verso un locale designato, richiedendo sempre conferma prima di operazioni distruttive.

### R5. Navigazione Ibrida Gerarchica (Nav2 + NOMAD Target Seeking)
- Pianificare ed eseguire la navigazione macro verso qualsiasi stanza registrata sulla mappa globale tramite Nav2 stack.
- Eseguire missioni di ricerca target ("cerca un oggetto" o "cerca una persona"):
  - Navigazione Nav2 verso l'ambiente target.
  - Attivazione in loco della pipeline reattiva NOMAD per la perlustrazione visiva dei punti ciechi combinata con Hailo YOLO per l'avvistamento e l'avvicinamento al target.

---

## Acceptance Criteria

### TC1. Luminance Safety Gate
- [ ] Il sistema rifiuta la mappatura se la luminosità media del frame è inferiore a 25/255 (buio) ed emette opportuno avviso vocale.
- [ ] Il sistema autorizza la procedura se la luminosità media supera 30/255.

### TC2. Macchina a Stati HRI e Timer di Sicurezza
- [ ] Richiesta vocale di conferma prima di iniziare a muoversi.
- [ ] Invio del promemoria di sollecito dopo 120 secondi di silenzio.
- [ ] Aborto sicuro e transizione a standby dopo 300 secondi complessivi senza risposta.
- [ ] Avvio immediato della mappatura in caso di risposta affermativa dell'utente.

### TC3. Frontier Exploration & Completamento Autonomo
- [ ] Rilevamento accurato delle frontiere di occupanza (celle note libere adiacenti a celle sconosciute).
- [ ] Pianificazione progressiva dei waypoint di frontiera senza intervento manuale.
- [ ] Arresto automatico dell'esplorazione quando la dimensione delle frontiere residue è < 0.40m.

### TC4. Ottimizzazione e Persistenza Mappe su SSD
- [ ] Chiamata riuscita al servizio di ottimizzazione globale del grafo SLAM prima del salvataggio.
- [ ] Esportazione dei file `.yaml` e `.pgm` nella directory dedicata `/mnt/ssd/maps/`.
- [ ] Incremento di memoria RAM durante l'ottimizzazione limitato a < 80MB (nessun OOM crash).

### TC5. Localizzazione al Buio via LiDAR RPLIDAR C1
- [ ] In stanza completamente buia (lux = 0), il sistema identifica la stanza corretta tramite rotazione a 360° e scansione ToF.
- [ ] Raggiungimento di una traccia di covarianza AMCL < 0.08 entro 2 rotazioni complete.

### TC6. Visual Place Recognition (VPR) su Hailo NPU
- [ ] Estrazione dell'embedding CosPlace 512D in < 50ms per frame.
- [ ] Riconoscimento corretto dell'ambiente registrato con similarità cosenica > 0.84.

### TC7. Dialogo Vocale e Query di Stato (VUI / TRINITY)
- [ ] Risposta coerente e corretta alle query vocali "Dove ti trovi?", "In che mappa navighi?", "Cosa vedi?".
- [ ] Convalida delle intenzioni complesse di navigazione e gestione sicura delle conferme di sovrascrittura mappa.

### TC8. Ricerca Ibrida di Oggetti / Persone (Nav2 + NOMAD)
- [ ] Raggiungimento della stanza designata tramite Nav2.
- [ ] Handover fluido alla pipeline visiva NOMAD per la ricerca locale con rilevamento YOLO e aggancio dell'obiettivo.

## Follow-up — 2026-09-17T18:40:07Z

Il server è stato riavviato e la quota si è resettata. Continua l'orchestrazione del progetto dal punto in cui era arrivata (Milestone 2 - Percezione Multimodale & Riconoscimento Ambienti).

## Follow-up — 2026-09-18T08:14:22Z

La quota si è azzerata ed il server è ripartito. Continua l'orchestrazione della missione completando il gate di verifica della Milestone 3 e procedendo con Milestone 4 (VUI & TRINITY Dialogue) e Milestone 5 (Hybrid Nav2 + NOMAD).

## Follow-up — 2026-09-18T13:13:34Z

La quota si è azzerata ed il server è ripartito. Continua l'orchestrazione del progetto: completa l'hardening della Milestone 3 (worker_m3_2), certifica il Gate 3 e procedi con la Milestone 4 (VUI & TRINITY Dialogue) e Milestone 5 (Hybrid Nav2 + NOMAD).

## Follow-up — 2026-09-18T19:38:08Z

La quota si è azzerata ed il server è ripartito. Continua l'orchestrazione del progetto: completa la certificazione del Gate 4 (Milestone 4 - Conversational VUI & TRINITY Dialogue) e procedi speditamente con la Milestone 5 (Hybrid Nav2 + NOMAD Target Seeking) e la Milestone 6 di verifica finale.

## Follow-up — 2026-09-30T10:41:59Z

# Teamwork Project Prompt — Final

> Status: Launched
> Goal: Execute multi-agent parallel implementation for Marcus Nav2, Closed-Loop Motion, NOMAD Cleanup, and TRINITY LLM Awareness
> Requested team: Full team (Actuation & PID, Nav2 Navigation, NOMAD Deprecation, TRINITY LLM Cognitive Integration)

Risoluzione dei problemi di mobilità e navigazione autonoma in Nav2 (frontiere calcolate ma robot fermo, deviazione a sinistra, arresti a scatti), implementazione della logica ad anello chiuso anti-stallo / boost di coppia per attrito statico sul Raspberry Pi 5, rimozione definitiva di NOMAD, e passaggio del controllo navigazione ed esplorazione alla LLM cognitiva TRINITY (RAG/CAG/MAG) con consapevolezza di stato, pausa conversazionale automatica e feedback vocale naturale.

Working directory: c:\Users\lsuffia\OneDrive - BRUGOLA OEB INDUSTRIALE SPA\Documents\robopy\antigravity
Integrity mode: development

## Requirements

### R1. Sblocco Movimento Nav2 & Controller Server
- Risolvere la causa per cui la mappa calcola il percorso delle frontiere ma il robot rimane immobile:
  - In `robopy_controller/config/nav2_params_jazzy.yaml`:
    - Rimuovere il vincolo rigido non-olonomo ad arco nel controller MPPI (`FollowPath`), impostando `kinematics: base_min_turning_radius: 0.0` affinché il robot differenziale possa effettuare rotazioni sul posto e seguire curve strette.
    - Ridurre `min_x_velocity_threshold: 0.02` per non sopprimere comandi a bassa velocità controllata.
    - Aumentare `batch_size: 200`, `vx_std: 0.12`, `wz_std: 0.25` per generare traiettorie di velocità fluide e consistenti.
    - In `collision_monitor`: rimodellare `PolygonStop` (`[[0.20, 0.14], [0.20, -0.14], [-0.14, -0.14], [-0.14, 0.14]]`) e impostare `min_range: 0.06` per scartare l'asta della camera o riflessi interni del telaio. Portare `source_timeout: 5.0` e rendere tollerante l'assenza temporanea del topic pointcloud da Hailo.
  - Verificare che il topic `cmd_vel` emesso da `collision_monitor` raggiunga fluidamente `waveshare_motor_driver`.

### R2. Linearità di Marcia, Diagnosi PID e Chiusura Anello di Coppia (Host Anti-Stall PI Boost)
- Risolvere la deviazione a sinistra e le soste a scatti in `robopy_controller/nodes/waveshare_motor_driver.py`:
  - Mantenere disattivato il PID difettoso interno all'ESP32 (`enable_esp32_pid:=False`) ed implementare un **Anello Chiuso Software di Velocità e Anti-Stallo a 20 Hz sul Raspberry Pi 5**.
  - Ribilanciare i trim hardware in marcia avanti: `left_motor_trim = 0.88`, `linear_min_duty_left = 0.13`, `linear_min_duty_right = 0.13` (rimuovendo la disparità che sovraccaricava la ruota destra facendolo curvare a sinistra).
  - Ribilanciare i trim in retromarcia: `left_motor_trim_rev = 0.85` e `right_motor_trim_rev = 1.05`.
  - Logica Anti-Stallo Adattiva ad Anello Chiuso (Host PI Boost):
    - Se $|v_{cmd}| \ge 0.03\text{ m/s}$ e la velocità misurata delle ruote da encoder PCNT è inferiore al 50% di $v_{cmd}$ (ruota bloccata da attrito statico o piccolo ostacolo), incrementare dinamicamente un boost integrativo di coppia $\Delta duty_{stall} += K_I \cdot (v_{cmd} - v_{actual}) \cdot dt$ fino a un massimo di $0.30$ duty (rispettando il tetto termico di SPEC-01).
    - Appena la ruota gira a regime ($v_{actual} \ge 0.8 \cdot v_{cmd}$), decrementare dolcemente il boost fino ad azzerarlo.
    - Se dopo 1.0s di boost massimo le ruote restano ferme a zero, innescare la diagnostica di stallo protettivo (`FM-MOT-002`).
  - Sintonizzare lo stabilizzatore di rotta a 42 Hz su giroscopio OAK-D Lite (`enable_heading_stabilizer`) per annullare derive residue.
  - Regolarizzare la gestione del watchdog a 500 ms per prevenire l'effetto stop-and-go a singhiozzo.

### R3. Dismissione Totale e Pulizia di NOMAD
- Rimuovere fisicamente tutti i file legati all'architettura NOMAD deprecata:
  - `robopy_controller/nodes/nomad_navigator_node.py`
  - `robopy_controller/nodes/nomad_reactive_pipeline_node.py`
  - `robopy_controller/robot_ai/skills/builtin/nomad_exploration_skill.py`
  - `scripts/start_nomad_vpr.sh`, `scripts/nomad_navigator_node`, `scripts/test_inject_nomad.py`
- Aggiornare `orchestrator.py`: sostituire l'import e la registrazione di `NomadExplorationSkill` con `FrontierExplorationSkill`.
- Aggiornare `navigation_skill.py`, `visual_exploration_skill.py` e `__init__.py` rimuovendo ogni menzione di NOMAD.
- Rimuovere i comandi `pkill -9 -f nomad_*` da `restart_hailo.sh` e ripulire gli entry points in `setup.py`.

### R4. Controllo Cognitivo Navigazione/Esplorazione tramite LLM TRINITY
- In `robopy_controller/robot_ai/orchestration/conversation.py`:
  - Rimuovere il bypass rigido con regex (`find_best_match` con confidenza >= 0.95) che intercettava i comandi di navigazione ed esplorazione escludendo la LLM. Tutte le richieste transitano dal cervello decisionale (Gemini Live API e Standard API).
  - Gestione Pausa Conversazionale Automatica (Barge-in): Quando l'utente parla a Marcus mentre è in corso un'esplorazione (`is_exploring == True`), il robot arresta temporaneamente il moto delle ruote inviando `/cmd_vel = 0.0` per azzerare il rumore e dedicare l'attenzione all'utente. Al termine della risposta vocale, se l'utente non ha chiesto lo stop ("fermati"), Marcus riprende automaticamente l'esplorazione.
- In `robopy_controller/robot_ai/skills/builtin/frontier_exploration_skill.py`:
  - Definire Function Declarations per Gemini: `start_frontier_exploration`, `stop_navigation`, `get_navigation_status`, `search_target`.
  - Emettere conferme vocali naturali e contestuali ("Parto subito a esplorare l'ambiente e mappare le aree libere!").
  - Tracciare eventi salienti dell'esplorazione e scoperte di oggetti come memorie episodiche in TRINITY MAG (`mag_database.py` / `mag_episodic.py`) e RAG (ChromaDB).
- In `robopy_controller/robot_ai/trinity/cag_environment.py` e `trinity_engine.py`:
  - Iniettare nel Context Aggregator (CAG) il blocco di consapevolezza in tempo reale:
    `[STATO ATTIVITÀ ROBOT]: In esplorazione attiva delle frontiere (X zone sconosciute rimanenti). Velocità: 0.18 m/s. Direzione: [stanza/coordinate]. Ostacoli recenti: nessuno bloccante.`
  - Rendere la LLM pienamente consapevole di cosa sta facendo, permettendole di rispondere a domande spontanee come "Dove stai andando?" o "Cosa hai trovato finora?".

## Acceptance Criteria

### C1. Movimento Nav2 e Frontiere
- [ ] Con `frontier_explorer_node` attivo su `/map`, il robot riceve il percorso ed esegue fisicamente il movimento continuo delle ruote.
- [ ] Nessun falso stop emesso da `collision_monitor` per riflessi interni dello chassis o timeout di sensori assenti.

### C2. Rettilineità e Gestione Ostacoli
- [ ] Avanzamento e retromarcia procedono in linea retta senza derive visibili a sinistra (deviazione angolare < 2° su 1 metro).
- [ ] Nessun moto a singhiozzo (stop-and-go): la marcia è continua e fluida lungo il percorso pianificato.
- [ ] Su piccoli dislivelli o tappeti, l'anello chiuso host a 20Hz incrementa la coppia evitando il blocco meccanico senza superare i limiti di sicurezza di SPEC-01.

### C3. Rimozione NOMAD
- [ ] Nessun processo o script associato a NOMAD rimane nel repository o viene avviato in `restart_hailo.sh` o registrato in `setup.py`.
- [ ] I comandi di esplorazione fanno riferimento unicamente al motore a frontiere.

### C4. Consapevolezza LLM e Conversazione
- [ ] Nessuna falsa attivazione da regex: l'avvio della navigazione avviene tramite decisione e function call della LLM.
- [ ] Durante l'esplorazione, parlando al robot, Marcus si arresta per ascoltare e rispondere contestualmente sapendo dove si trova e cosa stava facendo, e riprende automaticamente la marcia se non fermato dall'utente.
- [ ] Gli eventi salienti dell'esplorazione vengono memorizzati in TRINITY MAG e sono interrogabili in conversazioni successive.

## Follow-up — 2026-09-30T10:43:47Z

Nota fondamentale dall'utente: il Raspberry Pi 5 fisico non è attualmente raggiungibile via rete/SSH. Il deploy sul robot avverrà più avanti.
Non tentare connessioni SSH a Marcus, né esecuzione di `./sync_marcus.sh` o comandi remoti.
Esegui tutte le modifiche, la pulizia dei moduli NOMAD, le implementazioni dell'anello chiuso/anti-stallo, l'aggiornamento di Nav2 e l'integrazione cognitiva TRINITY esclusivamente in locale nel repository/workspace del PC, convalidando le modifiche tramite i test unitari e di sintassi locali (pytest/python).
