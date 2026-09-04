# 🔋 SPEC-06: Alimentazione (BMS), Hardware Safety & Monitoraggio Termico

## 1. Identificazione e Scopo
- **ID Specifica:** `SPEC-06`
- **Ambito:** Monitoraggio stato di carica della batteria LiPo 3S, filtraggio anti-sag durante gli spunti motori, compensazione feed-forward di tensione, protezione da scarica profonda (battery cliff), gestione termica SoC/NPU e interlock di sicurezza attiva.
- **Nodi & Moduli ROS 2:**
  - `robopy_controller.nodes.battery_manager_node` (`battery_manager_node.py`)
  - `robopy_controller.nodes.robot_health_supervisor` (`robot_health_supervisor.py`)
- **Hardware Diretto:** Pacco batteria 3S LiPo/Li-ion (nominale 11.1V, max 12.6V), Power Path OR-ing (diodi ideali), ADC partitore ESP32 Waveshare, PMIC Raspberry Pi 5, Sensori termici SoC e Hailo-10H, Ventola tachimetrica PWM.
- **DFMEA Correlati:** `FM-SYS-003` (Scarica profonda e distruzione LiPo), `FM-SYS-004` (Battery cliff e crash istantaneo Pi 5), `FM-SYS-005` (Thermal throttling CPU/NPU), `FM-SYS-006` (Voltage sag su spunti motori).

---

## 2. Architettura della Gestione Energetica

```mermaid
graph TD
    BATT["Batteria LiPo 3S / Alimentatore Rete"] --> ORING["Power Path OR-ing (Diodi Ideali)"]
    ORING --> ADC["Partitore Resistivo ADC (ESP32)"]
    ADC -->|Telemetria 'v' (mV) via Seriale| BM["battery_manager_node.py"]
    
    subgraph "Filtro Anti-Sag & Persistenza"
        MA["Media Mobile Circolare (20 Campioni @ 5Hz)"]
        TIMER["Timer Persistenza Allarmi (3.0s)"]
    end
    
    BM --> MA --> TIMER
    TIMER -->|V >= 12.70V| CHARGE["Stato: IN CARICA (12.8V)<br/>Inibizione Docking & Allarmi"]
    TIMER -->|V <= 10.20V| ECO["Modalità ECO: Limitatore PWM 50%"]
    TIMER -->|V <= 9.90V| DOCK["Trigger Rientro Cuccia (/robot/docking/trigger)"]
    TIMER -->|V <= 9.00V (3s persisti)| SHUT["Graceful OS Shutdown (/robot/system/shutdown)"]
    
    BM -->|Compensazione Dinamica| FF["Voltage Feed-Forward: PWM * (11.10V / V_eff)"]
    FF --> MOT["Driver Motori Waveshare"]
```

---

## 3. 🔴 ZONA ROSSA (Inviolabili - NO AUTONOMOUS TOUCH)

Le seguenti soglie di tensione e temperatura sono limiti fisici di sopravvivenza. La loro violazione può causare l'incendio delle celle LiPo o la distruzione del filesystem.

| Parametro / Soglia Fisica | Valore Inviolabile | Rischio Ingegneristico | DFMEA |
| :--- | :--- | :--- | :--- |
| **Soglia Spegnimento Critico**| **9.00 V** (Persistenza: **3.0 s**) | Scarica distruttiva LiPo e battery cliff con freeze SSD | FM-SYS-004 |
| **Soglia Docking Batteria** | **9.90 V** (Persistenza: **3.0 s**) | Esaurimento batteria con robot bloccato lontano dalla base | FM-SYS-003 |
| **Rilevamento Alimentatore Rete**| Tensione $V \ge \mathbf{12.70\text{ V}}$ | Conflitto logico docking durante alimentazione esterna | FM-SYS-007 |
| **Filtro Anti-Sag Motori** | Minimo **20 campioni (5Hz)** & finestra **3.0s** | Falsi spegnimenti d'emergenza su normali spunti di spinta | FM-SYS-006 |
| **Temperatura Massima CPU** | $T_{CPU} \ge \mathbf{80^\circ\text{C}}$ innesca arresto moto | Degradazione silicio e thermal throttling incontrollato | FM-SYS-005 |
| **Temperatura Massima NPU** | $T_{NPU} \ge \mathbf{85^\circ\text{C}}$ innesca stop inferenza | Protezione termica dell'acceleratore Hailo-10H | FM-SYS-005 |
| **Chiusura Filesystem OS** | `sync; sudo shutdown -h now` entro 3s | Corruzione irreversibile delle tabelle di allocazione NVMe | FM-SYS-004 |

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

1. **Abbassamento Soglie di Tensione:** Riduzione della soglia di spegnimento sotto 9.00V o della soglia di docking sotto 9.90V.
2. **Modifica Comandi Root di Sistema:** Alterazione della sintassi di spegnimento OS o dei permessi sudoers per `battery_manager_node`.
3. **Calibrazione Partitore Hardware:** Modifica della costante di conversione ADC ($mV / tick$) memorizzata nel nodo o nel firmware.
4. **Sostituzione Chimica Accumulatore:** Transizione da pacco LiPo standard ad accumulatori LiFePO4 o celle 18650 Li-ion con curve di scarica differenti.

---

## 6. Procedura di Verifica & Test di Non-Regressione

Prima di confermare modifiche alla gestione energetica o ai supervisori di sicurezza, l'agente DEVE eseguire con successo:

```bash
# 1. Test unitario del filtro anti-sag e timer di persistenza (FM-SYS-004/006)
pytest tests/test_battery_manager.py -v

# 2. Test di simulazione dello spunto motore (dip transitorio a 8.8V per 1.5s senza shutdown)
pytest tests/test_battery_transient_sag.py -v

# 3. Test del Robot Health Supervisor e transizioni GREEN / YELLOW / RED
pytest tests/test_robot_health_supervisor.py -v
```
I test devono confermare:
- Immunità assoluta a transitori di tensione inferiori a 3.0 secondi.
- Attivazione deterministica del graceful shutdown se la tensione permane stabilmente sotto 9.00V per oltre 3.0s.
- Riduzione della coppia e della velocità massima del 50% quando la modalità ECO è attiva.
