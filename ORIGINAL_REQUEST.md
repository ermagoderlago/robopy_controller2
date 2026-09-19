# Original User Request

## 2026-09-17T12:39:12Z

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
