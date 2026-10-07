# 🔋 SPEC-06: Alimentazione (BMS), Hardware Safety & Monitoraggio Termico

## 1. Identificazione e Scopo
- **ID Specifica:** `SPEC-06`
- **Ambito:** Monitoraggio stato di carica del pacco batteria LiPo 3S2P (6 celle totali: 2 pacchi 3S in parallelo), filtraggio anti-sag durante gli spunti motori, compensazione feed-forward di tensione, protezione da scarica profonda (battery cliff), gestione termica SoC/NPU e interlock di sicurezza attiva.
- **Nodi & Moduli ROS 2:**
  - `robopy_controller.nodes.battery_manager_node` (`battery_manager_node.py`)
  - `robopy_controller.nodes.robot_health_supervisor` (`robot_health_supervisor.py`)
- **Hardware Diretto:** Pacco batterie LiPo/Li-ion in configurazione **3S2P (6 celle totali: 2 pacchi 3S collegati in parallelo)** con tensione nominale 11.1V (3 x 3.7V) e max 12.6V (3 x 4.2V). Il parallelo 2P garantisce il raddoppio della capacità in Ah e il dimezzamento della resistenza interna equivalente ($R_{ESR}/2$), riducendo drasticamente il sag di tensione durante gli spunti motori; Power Path OR-ing (diodi ideali), ADC partitore ESP32 Waveshare, PMIC Raspberry Pi 5, Sensori termici SoC e Hailo-10H, Ventola tachimetrica PWM.
- **DFMEA Correlati:** `FM-SYS-003` (Scarica profonda e distruzione LiPo), `FM-SYS-004` (Battery cliff e crash istantaneo Pi 5), `FM-SYS-005` (Thermal throttling CPU/NPU), `FM-SYS-006` (Voltage sag su spunti motori).

---

## 2. Architettura della Gestione Energetica

```mermaid
graph TD
    BATT["Batteria LiPo 3S2P (6 celle: 2x 3S in parallelo) / Rete"] --> ORING["Power Path OR-ing (Diodi Ideali)"]
    ORING --> ADC["Partitore Resistivo ADC (ESP32)"]
    ADC -->|Telemetria 'v' (mV) via Seriale| BM["battery_manager_node.py"]
    
    subgraph "Filtro Anti-Sag & Persistenza"
        MA["Media Mobile Circolare (20 Campioni @ 5Hz)"]
        TIMER["Timer Persistenza Allarmi (3.0s)"]
    end
    
    BM --> MA --> TIMER
    TIMER -->|V >= 12.70V| CHARGE["Stato: IN CARICA (12.8V)<br/>Inibizione Docking & Allarmi"]
    TIMER -->|V <= 10.20V| ECO["Modalità ECO: Limitatore PWM 50% & Accel ridotta"]
    TIMER -->|V <= 9.90V| DOCK["Trigger Rientro Cuccia (/robot/docking/trigger)"]
    TIMER -->|V <= 9.60V (3s persisti o <= 9.50V)| SHUT["Graceful Emergency OS Shutdown:<br/>1. Stop Motori (Twist 0.0, rimozione sag I*R)<br/>2. Chime + Avviso Vocale ReSpeaker<br/>3. Registrazione Trauma TRINITY (MAG/RAG)<br/>4. Salvataggio Mappe Nav2/RTAB-Map su NVMe<br/>5. Unconfigure Nodi & sync/poweroff OS"]
    
    BM -->|Compensazione Dinamica| FF["Voltage Feed-Forward: PWM * (11.10V / V_eff)"]
    FF --> MOT["Driver Motori Waveshare"]
```

---

## 3. 🔴 ZONA ROSSA (Inviolabili - NO AUTONOMOUS TOUCH)

Le seguenti soglie di tensione e temperatura sono limiti fisici di sopravvivenza. La loro violazione può causare il danneggiamento delle celle 18650 Li-ion o la corruzione irreversibile del filesystem su NVMe.

| Parametro / Soglia Fisica | Valore Inviolabile | Rischio Ingegneristico | DFMEA |
| :--- | :--- | :--- | :--- |
| **Soglia Spegnimento Critico**| **9.60 V** (Persistenza: **3.0 s**) / istantaneo **<= 9.50 V** | Con immediato taglio motori ($I=0$), si elimina la caduta $I \cdot R_{int}$ garantendo margine prima dell'intervento hardware cutoff BMS (~9.74V sotto carico) | FM-SYS-004, FM-PWR-005 |
| **Soglia Docking Batteria** | **9.90 V** (Persistenza: **3.0 s**) | Rientro preventivo alla base di ricarica con riserva energetica sufficiente per il percorso | FM-SYS-003, FM-PWR-004 |
| **Soglia Modalità ECO** | **10.20 V** (Persistenza: **3.0 s**) | Limitazione della velocità al 50% per ridurre lo spike di corrente e prolungare l'autonomia residua | FM-SYS-006 |
| **Piena Carica (18650 3S2P)**| $\mathbf{12.10\text{ V} - 12.20\text{ V}}$ | Range di riposo/carico base calibrato per compensare la caduta per resistenza interna | FM-SYS-003 |
| **Rilevamento Alimentatore Rete**| Tensione $V \ge \mathbf{12.70\text{ V}}$ | Conflitto logico docking durante alimentazione esterna / bus a 12.80V | FM-SYS-007 |
| **Filtro Anti-Sag Motori** | Minimo **20 campioni (5Hz)** & finestra **3.0s** | Falsi spegnimenti d'emergenza su normali spunti di spinta transitori | FM-SYS-006 |
| **Temperatura Massima CPU** | $T_{CPU} \ge \mathbf{80^\circ\text{C}}$ innesca arresto moto | Degradazione silicio e thermal throttling incontrollato | FM-SYS-005 |
| **Temperatura Massima NPU** | $T_{NPU} \ge \mathbf{85^\circ\text{C}}$ innesca stop inferenza | Protezione termica dell'acceleratore Hailo-10H | FM-SYS-005 |
| **Chiusura Filesystem OS** | `sync; sudo shutdown -h now` entro 3s | Corruzione irreversibile delle tabelle di allocazione NVMe | FM-SYS-004, FM-PWR-005 |
| **Tensione Rail 5V Pi 5** | $V_{in} \ge \mathbf{4.75\text{V}}$; taratura step-down $\mathbf{5.20V - 5.25V}$ (max $\mathbf{5.30V}$) | Undervoltage PMIC (<4.63V), brownout reset (<4.50V) sotto carico AI | FM-PWR-002 |

---

## 4. 🟢 ZONA VERDE (Auto-Evolution - MIGLIORAMENTO AUTONOMO CONSENTITO)

L'agente Antigravity può ottimizzare e ricalibrare autonomamente le seguenti componenti:

| Area di Ottimizzazione | Metodo & Logica Ammessa | Range & Vincoli di Accettazione |
| :--- | :--- | :--- |
| **Compensazione Feed-Forward** | Adattamento $PWM_{comp} = PWM \times (11.10\text{V} / V_{eff})$ | $V_{eff} \in [9.0\text{V}, 12.6\text{V}]$; clamping dinamico output |
| **Curva PWM Ventola di Raffreddamento** | Profilo acustico silenzioso durante sessioni VUI | Ventola al minimo per $T < 55^\circ\text{C}$; 100% solo se $T > 72^\circ\text{C}$ |
| **Smoothing Filtro Tensione** | Numero campioni media mobile anti-rumore | Finestra mobile: $N \in [15, 30]$ campioni @ 5Hz |
| **Avvisi Vocali Livello Batteria** | Trigger frasi contestuali VUI ("Ho fame", "Batteria al 20%") | Innesco su $V = 10.50\text{V}$ e $V = 10.00\text{V}$ una sola volta |
| **Strategia Cooldown Navigazione** | Riduzione transitoria velocità NOMAD/Nav2 sotto carico termico | Riduzione velocità al 70% se $T_{CPU} \in [72^\circ\text{C}, 78^\circ\text{C}]$ |

---

## 5. 🟡 ZONA GIALLA (Human-in-the-Loop - APPROVAZIONE UMANA OBBLIGATORIA)

Le seguenti modifiche richiedono proposta formale e validazione dell'operatore umano:

1. **Abbassamento Soglie di Tensione:** Riduzione della soglia di spegnimento sotto 9.60V o della soglia di docking sotto 9.90V. (Approvato in data 2026-10-01 per celle Panasonic NCR18650B 3S2P con $R_{ESR}$ e caduta di tensione sotto carico).
2. **Modifica Comandi Root di Sistema:** Alterazione della sintassi di spegnimento OS o dei permessi sudoers per `battery_manager_node` e `scripts/graceful_emergency_shutdown.py`.
3. **Calibrazione Partitore Hardware:** Modifica della costante di conversione ADC ($mV / tick$) memorizzata nel nodo o nel firmware.
4. **Sostituzione Chimica Accumulatore:** Transizione tra celle LiPo pouch e celle cilindriche 18650 Li-ion o LiFePO4 con curve OCV differenti.

---

## 6. Procedura di Verifica & Test di Non-Regressione

Prima di confermare modifiche alla gestione energetica o ai supervisori di sicurezza, l'agente DEVE eseguire con successo:

```bash
# 1. Test unitario del monitoraggio batteria, INA219, ECO mode e shutdown a 9.60V (FM-SYS-004/006, FM-PWR-005)
pytest test/unit/test_battery_monitoring.py -v

# 2. Test del Robot Health Supervisor e transizioni GREEN / YELLOW / RED
pytest test/unit/test_system_health.py -v
```
I test devono confermare:
- Immunità assoluta a transitori di tensione inferiori a 3.0 secondi.
- Attivazione deterministica del graceful shutdown se la tensione permane stabilmente sotto 9.60V per oltre 3.0s (o <= 9.50V istantaneo).
- Riduzione della velocità massima al 50% quando la modalità ECO è attiva (tensione <= 10.20V).
- Arresto istantaneo dei motori (`Twist 0.0` su safety override) e annuncio vocale ReSpeaker prima del distacco del filesystem.
