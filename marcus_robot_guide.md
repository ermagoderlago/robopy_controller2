# 🤖 Marcus - Robot Capabilities & Architecture Guide

Questo documento descrive in dettaglio le capacità cognitive, motorie e visive di **Marcus**, l'architettura software residente su Raspberry Pi 5 con accelerazione Hailo-10 NPU, l'integrazione con i Large Language Models (LLM) in cloud e la roadmap per gli sviluppi futuri.

---

## 🎯 Visione & Obiettivi (Cosa fa Marcus)

Marcus è progettato per essere un **assistente domestico multimodale ed empatico**. Non è un semplice robot giocattolo, ma un agente intelligente in grado di:
1. **Comprendere l'ambiente domestico:** Mappare stanze, mobili e oggetti in 3D per muoversi e localizzarsi in sicurezza.
2. **Riconoscere gli abitanti della casa:** Identificare volti (Face Recognition) e voci (Speaker Verification) per adattare la propria personalità, preferenze e segreti a ciascun membro della famiglia.
3. **Controllare la Smart Home:** Interagire nativamente con Home Assistant per accendere luci, controllare la temperatura, avviare elettrodomestici o riprodurre musica.
4. **Fornire assistenza proattiva:** Ricordare impegni, leggere e filtrare email importanti, calcolare riassunti delle interazioni quotidiane e fare "sogni cognitivi" notturni per riordinare la propria memoria a lungo termine.

---

## ⚙️ Architettura di Sistema

```mermaid
graph TB
    subgraph "Sensori & Attuatori"
        CAM["OAK-D Lite Camera"]
        MIC["ReSpeaker USB Mic Array"]
        MOT["Waveshare Motor Driver"]
        US["Sensore Ultrasuoni"]
    end

    subgraph "Raspberry Pi 5 Host (4GB RAM)"
        NAV["Nav2 Stack & SLAM"]
        VUI["respeaker_vui_node (VAD & DSP)"]
        ORCH["AI Orchestrator (robot_ai_node)"]
        RAG["ChromaNative Store (Memory)"]
        GRAPH["Cognitive Graph (Dopamine State)"]
        SKILL["Skill Registry (HA, Nav, Alarm, Email)"]
    end

    subgraph "Hailo-10 NPU (40 TOPS PCIe)"
        YOLO["YOLOv8 Object Detection"]
        SP["SuperPoint (VO Features)"]
        SCRFD["SCRFD Face Detection"]
        ARCF["ArcFace Face Recognition"]
        ECAPA["ECAPA-TDNN (Speaker Verification)"]
    end

    subgraph "Cloud Services"
        GEMINI["Gemini Live API (WebSocket)"]
        DEEP["DeepSeek (Nightly Analyst)"]
    end

    CAM --> SP
    CAM --> YOLO
    CAM --> SCRFD
    SCRFD --> ARCF
    MIC --> VUI
    VUI --> ECAPA
    ECAPA --> ORCH
    ARCF --> ORCH
    YOLO --> ORCH
    SP --> NAV
    ORCH --> RAG
    ORCH --> GRAPH
    ORCH --> SKILL
    SKILL --> MOT
    ORCH --> GEMINI
    ORCH --> DEEP
    US --> NAV
```

---

## 🏃‍♂️ Attuazione & Navigazione (Come si muove)

Marcus si muove su una base mobile differenziale, gestendo il movimento e la mappatura in modo robusto tramite ROS 2:

* **Controllo Motori e Telemetria:** Il nodo `waveshare_motor_driver` comunica via seriale (`/dev/ttyUSB0`) con la board motori ESP32. Riceve comandi di velocità lineare/angolare `/cmd_vel` e pubblica i dati di odometria `/odom` calcolati dagli encoder delle ruote (1440 tick per rivoluzione). Supporta la calibrazione dinamica closed-loop (tramite la skill `calibration` V2) confrontando la posa reale di `/vo/odom` con la posa stimata di `/odom` per auto-calibrare il raggio ruota (`wheel_radius`) e la traccia (`wheel_separation`) sotto il 2% di errore residuo.
* **Architettura di Movimento Relativo (`MotionManager`):** Il pacchetto `robot_ai.motion` (`MotionPrimitive`, `MotionSequence`, `MotionManager`) fornisce l'ossatura cinematico-temporale per l'esecuzione di comandi di movimento diretto a distanza e angolo (es. *"muoviti in avanti di 30cm"*, *"gira a sinistra di 90 gradi"*, *"spostati indietro di 0.5 metri"*). Esegue conversioni automatiche di misura, calcola la durata precisa $t = d/v$ e applica i limiti di sicurezza hardware ($v \le 0.18$ m/s, $\omega \le 0.8$ rad/s) integrandosi sia con le Live Tool Calls di Gemini che con l'input testuale.
* **Sicurezza Attiva & Diagnostica Chassis:** Il driver motori monitora costantemente lo stato del movimento per prevenire collisioni e sovraccarichi:
  * *Stallo meccanico:* se viene impartito un comando di moto ma le ruote non girano (velocità da encoder $\approx 0$ per $> 1.0$ s).
  * *Slittamento/Ostacolo:* se le ruote girano ma il robot è fermo visivamente rispetto allo SLAM (velocità visiva $v_{vo} < 0.008$ m/s per $> 1.0$ s).
  * *Sovraccarico elettrico:* se si registra una caduta di tensione della batteria $> 2.0$ V rispetto allo stato di riposo (corrispondente a un picco anomalo di assorbimento).
  Al rilevamento di una di queste condizioni, viene pubblicato uno stato `ERROR` sul topic `/diagnostics` (nodo `motor_stall`). L'orchestratore AI intercetta il diagnostico, attiva l'arresto di emergenza (`emergency_stop()`) e avverte l'utente vocalmente.
* **SLAM e Localizzazione:** Utilizza **RTAB-Map** (`rtabmap_slam`) per generare mappe d'ambiente 2D/3D in modalità multi-sessione incrementale (`Mem/IncrementalMemory: true`). Se spento e riacceso in una stanza diversa, avvia una nuova sessione autonoma per prevenire la corruzione delle mappe esistenti, consentendo il merge automatico in caso di loop closure.
* **Evitamento Ostacoli (No STVL):**
  > [!IMPORTANT]
  > Per preservare la CPU e la RAM sul Raspberry Pi 5, **è vietato l'uso di STVL 3D**. Marcus proietta i dati visivi 3D in una **griglia 2.5D locale** con decadimento temporale tramite il nodo custom `semantic_costmap_injector.py`.
* **Navigazione Autonoma:** Gestita dallo stack **Nav2** (planner, controller e comportamento ad albero decisionale) che pianifica traiettorie sicure. Le destinazioni non sono rigide, ma risolte dinamicamente tramite interrogazione a ChromaDB (sia per stanze apprese `MemoryType.LOCATION` che per oggetti identificati recentemente `MemoryType.VISUAL_OBSERVATION` filtrati per l'active session ID).
* **Sicurezza Attiva:** Il modulo `reactive_safety.py` gestisce l'arresto d'emergenza in caso di rilevamento ostacolo immediato tramite il sensore a ultrasuoni (`ultrasonic_sensor`) o disconnessioni della telecamera.

---

## 💬 Interfaccia Vocale & Conversazione (VUI)

La Voice User Interface (VUI) è il canale primario di interazione di Marcus:

* **DSP & VAD (Voice Activity Detection):** Il nodo `respeaker_vui_node.py` elabora l'audio catturato dall'array microfonico USB ReSpeaker a 16kHz PCM a 16-bit. Un filtro Butterworth Passa-Alto @ 140 Hz (HPF) 2° ordine elimina alla radice il ronzio a bassa frequenza della ventola del Pi 5, la selezione dinamica del canale sceglie la traccia con maggior energia vocale pulita, e la soglia ad-hoc del noise gate (`[800.0, 4500.0]`) garantisce un'elevata sensibilità sia da vicino che da lontano (1-3 metri).
* **Barge-in Sicuro:** Marcus supporta il "barge-in". Se l'utente parla mentre Marcus sta riproducendo sintesi vocale (TTS), il robot attenua immediatamente il proprio guadagno microfonico (`stt_gain` ridotto a 0.1x) e interrompe la riproduzione audio per ascoltare la nuova frase dell'utente.
* **Gemini Live API (WebSocket):** L'orchestratore stabilisce una connessione WebSocket persistente e bidirezionale in tempo reale con le API di Gemini. L'audio catturato viene trasmesso in streaming e il robot riceve indietro audio PCM crudo da riprodurre direttamente sull'hardware (`play_raw_pcm`), riducendo la latenza percepita di risposta a meno di 1.5 secondi.

---

## 🧠 Accelerazione Hardware (Hailo-10 NPU)

L'HAT Hardware Raspberry Pi AI HAT+ ospita una **Hailo-10 NPU** da 40 TOPS collegata via PCIe Gen 3. Marcus la sfrutta per eseguire reti neurali pesanti localmente a FPS elevati con bassissimo consumo CPU:

1. **YOLOv8-seg:** Rilevamento e segmentazione in tempo reale di 80 classi di oggetti comuni (persone, sedie, bottiglie, zaini) per popolare la memoria semantica.
2. **SuperPoint:** Estrattore di feature visive puntiformi integrato nel driver della telecamera per l'odometria visuale C++.
3. **NetVLAD (Backbone):** Modello di localizzazione globale (VPR) che permette al robot di riconoscere istantaneamente in quale stanza si trova. Il backbone di estrazione gira su NPU e il pooling (soft-assignment) viene eseguito in host CPU tramite NumPy/C++ per aggirare i limiti del compilatore, pubblicando descrittori a 4096 dimensioni su `/hailo/vpr/descriptor`.
4. **SCRFD + ArcFace (Sprint 3):** Rilevamento dei volti ultra-veloce (SCRFD) e calcolo degli embedding di identificazione a 512 dimensioni (ArcFace) eseguiti in meno di 10ms su NPU.
5. **Local VLM (Qwen2-VL) (Fase 3):** Vision-Language Model Qwen2-VL-1.5B integrato localmente tramite la suite Hailo GenAI, condividendo l'hardware con l'NPU bridge tramite un VDevice condiviso per consentire analisi visive ed esplorative completamente offline.
6. **ECAPA-TDNN (Speaker Verification):** Estrazione dell'impronta vocale (embedding 192-dim) per verificare chi sta parlando.

---

## 🧠 Il Cervello Cognitivo (Come usa le LLM)

Marcus combina diversi servizi e moduli per emulare un cervello autonomo e coerente:

* **World Model (`world_model.py`):** Tiene traccia dello stato corrente del robot (dove si trova, che batteria ha, chi ha di fronte, quale compito sta svolgendo e la cronologia degli eventi recenti). Queste informazioni vengono iniettate costantemente in ogni prompt inviato alla LLM per darle memoria del contesto fisico.
* **Memory RAG (`chroma_native_store.py`):** Un database vettoriale locale (ChromaDB) in cui vengono registrate tutte le interazioni dell'utente. Quando l'utente fa una domanda, il sistema effettua una ricerca semantica basata su embedding per richiamare i ricordi pertinenti e rispondere in modo coerente nel tempo.
* **Skill Registry:** Un catalogo di strumenti fisici ("tool calling") che la LLM può richiamare dinamicamente:
  * *HomeAssistantSkill:* Controlla la smart home.
  * *NavigationSkill:* Ordina a Marcus di spostarsi in una stanza o seguire una persona.
  * *VisualExplorationSkill:* Marcus si guarda intorno per identificare e mappare oggetti.
  * *AlarmSkill & TimerSkill:* Schedula promemoria e sveglie locali.
  * *EmailSkill:* Controlla, filtra e invia messaggi email.
* **Nightly Dream (`nightly_dream_service.py`):** Alle 03:00 del mattino, Marcus entra in modalità "Sogno". Analizza i registri della giornata ed esegue una discussione collaborativa a 4 turni tra due modelli (Gemini come mente creativa, DeepSeek come analista critico) per estrarre insight sulla personalità dell'utente, correggere bug nei propri prompt ed archiviare i dati inutili.

---

## 📈 Stato Attuale (Cosa funziona bene ora)

* **Mappatura Semantica e YOLO Real-Time:** Integrazione end-to-end delle rilevazioni YOLO reali dell'NPU Hailo-10H con la costmap Nav2 e il mapper C++ (Visual Fusion), dotata di stima 3D geometrica thread-safe e temporal decay dinamico degli ostacoli.
* **Riconoscimento Facciale Integrato:** Riconoscimento offline di estranei e persone della casa basato su NPU (SCRFD + ArcFace).
* **Enrollment Dinamico VUI (Volto):** Dicendo *"Marcus, ti presento Edoardo"*, il robot avvia una sessione di cattura automatica del volto, calcola la media degli embedding estratti su NPU, crea un profilo `.npy` robusto e inizia immediatamente a riconoscere Edoardo nei frame successivi.
* **Biometria Vocale (Speaker ID):** Riconoscimento dell'identità dell'utente semplicemente ascoltando la sua impronta vocale (192-dim embeddings), consentendo l'identificazione anche a robot di spalle.
* **Enrollment Dinamico VUI (Voce):** Dicendo *"Marcus, registra la mia voce como Luca"*, il robot avvia la sessione di cattura accumulando segmenti audio puliti, mediando e normalizzando i vettori di embedding vocali per poi salvarli come impronta permanente `.npy`.
* **DSP Audio Robust:** Il filtro passa-banda e l'algoritmo di residuo catturano frasi pulite anche con rumore di fondo domestico o durante la riproduzione TTS.
* **Bassi Consumi RAM/CPU:** LRU Cache ridotta a 64 vettori, uso di float16 reale, core pinning del bridge NPU sui core CPU 2 e 3 e limitazioni del contesto HA mantengono l'utilizzo di memoria stabilmente sotto il tetto critico dei 4GB.
* **Affidabilità Testata:** Test suite automatica locale ed eseguita su Raspberry Pi con pytest e script specifici (`test_sprint3.py` e `test_sprint4.py`).

---

## 🚀 Sviluppi Futuri (Roadmap)

### Sprint 5: Memory Decay Bio-Ispirato, Amigdala e DMN (Completato)
* **Obiettivo:** Implementare la curva dell'oblio di Ebbinghaus per la gestione della memoria ChromaDB ed il cervello cognitivo superiore.
* **Funzionalità:** 
  * Il sotto-modulo `robopy_controller/robot_ai/cognitive/` gestisce la dinamica sinaptica (`synaptic_strength`, `recall_count`, `lambda_decay`, `amygdala_protected`).
  * L'Amigdala Digitale esegue l'elaborazione rapida Low Road (RMS/ZCR per stress vocale, pericoli ambientali, anomalie hardware) ed applica l'Hijack (arresto ed interrupt) o l'Anxious Vigilance (riduzione velocità MPPI al 50% tramite Fear Conditioning).
  * Il Default Mode Network (DMN) estrae intenzioni proattive durante l'inattività del robot.
  * Il ciclo del Sogno Notturno esegue la potatura sinaptica fisica su ChromaDB liberando la memoria RAM.


### Sprint 6: Embedding Locali offline (Medio termine)
* **Obiettivo:** Eseguire `all-MiniLM-L6-v2` in formato ONNX ottimizzato localmente sulla CPU host tramite ONNX Runtime.
* **Funzionalità:** Permettere a Marcus di memorizzare e cercare nel proprio database di ricordi (RAG) in modo completamente offline senza dipendere dalle API Gemini cloud.

### Sinergia Multimodale (Lungo termine)
* **Obiettivo:** Fondere face embedding + speaker embedding in un punteggio di confidenza biometrico unico per prevenire spoofing ed impersonificazioni.
