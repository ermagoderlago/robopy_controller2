# Matrice di Valutazione Standard FMEA-Lite Evoluto (AIAG-VDA Standard)
## Progetto Robotico Marcus (ROS 2 Jazzy / Raspberry Pi 5 / Hailo-10H / Waveshare ESP32)

Il presente documento stabilisce le scale oggettive e standardizzate (1-10) di **Severità (S)**, **Occorrenza (O)** e **Rilevabilità (D)**, conformi allo standard **AIAG-VDA FMEA**, modellate sui vincoli fisici, prestazionali e di sopravvivenza del robot Marcus.

---

### 1. Scala di Severità (Severity - S)
La Severità valuta l'impatto sul robot, sugli utenti, sull'ambiente circostante e sull'integrità del sistema in caso di manifestazione del guasto.

| Punteggio | Livello | Descrizione Tecnica & Impatto Operativo per Marcus |
| :---: | :--- | :--- |
| **1 - 2** | **Bassa** | Glitch visivo su GUI/Foxglove, warning non bloccante nei log ROS 2, lieve degrado estetico del feedback LED o delle trasmissioni secondarie. Nessun impatto sulla missione. |
| **3 - 4** | **Lieve** | Delay nella risposta vocale VUI/Gemini Live API (1-2s), jitter minore nel frame rate della camera RGB/Depth (< 15 fps temporaneo), flickering LED non critico. |
| **5 - 6** | **Moderata** | Degradazione o fallimento isolato di una Skill secondaria (es. Face Enrollment, RAG query), riavvio isolato e trasparente di un nodo non critico gestito da systemd/watchdog. |
| **7 - 8** | **Elevata** | Freeze dello stack Nav2, lock della porta seriale ESP32 Waveshare (`/dev/ttyUSB0`), contesa esclusiva audio ALSA/PipeWire con mutismo VUI, stallo della conversazione cognitive graph. |
| **9 - 10** | **Critica** | **Rischio di sicurezza fisica:** collisione improvvisa con persone, bambini o animali domestici; danno hardware permanente (es. surriscaldamento motori/NPU); Kernel Panic; OOM Kill dell'OS indotto da RAM; corruzione permanente del DB mnemonico/checkpoint. |

---

### 2. Scala di Frequenza (Occurrence - O)
L'Occorrenza misura la frequenza stimata o rilevata sul campo di accadimento della causa radice del guasto prima dell'applicazione dei controlli di prevenzione.

| Punteggio | Livello | Frequenza Operativa / Probabilità di Manifestazione |
| :---: | :--- | :--- |
| **1 - 2** | **Rarissima** | Anomalia estremamente rara, manifestatasi in **< 1%** delle sessioni operative (< 1 volta ogni 100 ore di funzionamento). |
| **3 - 4** | **Bassa** | Frequenza contenuta (**1% - 10%** delle sessioni), legata a condizioni ambientali infrequenti o combinazioni rare di input. |
| **5 - 6** | **Moderata** | Frequenza riscontrata con regolarità (**10% - 30%** delle sessioni), tipica di stiction meccanica, picchi di carico Wi-Fi o variazioni di tensione batteria. |
| **7 - 8** | **Alta** | Frequenza elevata (**30% - 70%** delle sessioni), presente in scenari d'uso standard privi di contromisure dedicate. |
| **9 - 10** | **Sistematica** | **Guasto deterministico (> 70% delle sessioni o ad ogni avvio)** dovuto a difetti di compilazione, incompatibilità API, race condition deterministiche o errori di schema. |

---

### 3. Scala di Rilevabilità (Detection - D)
La Rilevabilità definisce l'efficacia dei meccanismi di monitoraggio, diagnostica ed eccezione nel rilevare la presenza della causa o del modo di guasto prima che causi danni.

| Punteggio | Livello | Meccanismo di Diagnostica & Tempi di Rilevamento |
| :---: | :--- | :--- |
| **1 - 2** | **Istantanea** | Rilevamento istantaneo (< 100ms) tramite pubblicazione su topic ROS 2 `/diagnostics` o eccezione esplicita e gestita nel log (`rclpy/rclcpp`). |
| **3 - 4** | **Rapida (Watchdog)** | Rilevamento automatico dal Watchdog software (heartbeat node o systemd service) entro **< 5 secondi**. |
| **5 - 6** | **Comportamentale** | Anomalia comportamentale non intercettata direttamente dai log ma visibile dall'utente durante l'uso sul campo (es. deriva di odometria, risposte vocali incoerenti). |
| **7 - 8** | **Latente / Post-Mortem** | Memory leak graduale, degrado prestazionale latente o errore intermedio isolabile esclusivamente tramite analisi post-mortem approfondita dei file di log. |
| **9 - 10** | **Invisibile** | **Guasto invisibile / Silent race condition** priva di eccezioni nei log, degradazione non allarmata o corruzione silenziosa delle strutture dati in memoria. |

---

### 4. Matrice di Rischio RPN e Soglie Esecutive

Il valore di **Risk Priority Number (RPN)** è determinato dal prodotto cartesiano dei tre fattori:
$$RPN = Severity \times Occurrence \times Detection$$

#### Classificazione del Rischio Residuo ($RPN_{res} = S_{res} \times O_{res} \times D_{res}$):
- 🟢 **LOW ($RPN \le 50$):** Rischio sotto controllo. Nessuna azione correttiva immediata richiesta.
- 🟡 **MEDIUM ($51 \le RPN \le 199$):** Rischio moderato. Monitoraggio attivo e mitigazione raccomandata nei successivi sprint.
- 🟠 **HIGH ($200 \le RPN \le 349$):** Rischio elevato. Piano di mitigazione obbligatorio e prioritario.
- 🔴 **CRITICAL ($RPN \ge 350$):** Rischio critico. Blocco immediato dei rilasci software / Intervento ingegneristico prioritario.

---

### 🚨 REGOLA OPERATIVA INTEGRATA: OVERRIDE SEVERITÀ CRITICA
> [!CAUTION]
> **REVISION_MANDATORY (Override per Sicurezza e Danni Hardware):**
> Qualsiasi modalità di guasto avente **$S_{init} \ge 9$** oppure **$S_{res} \ge 9$** viene classificata **AUTOMATICAMENTE** come **`REVISION_MANDATORY`**, indipendentemente dal valore finale di RPN calcolato.
> 
> *Motivazione:* Nei sistemi robotici fisici autonomi (AGV/AMR), guasti con Severità 9 o 10 (es. collisioni con esseri umani o animali domestici, danni irreversibili alla batteria/scheda madre, OOM Panic dell'OS) non possono mai essere tollerati, neanche a fronte di una frequenza residua bassissima ($O=1, D=1 \implies RPN=9$).
