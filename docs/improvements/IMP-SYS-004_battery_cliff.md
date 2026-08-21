# IMP-SYS-004: Battery Management, Anti-Sag Persistence & OS Graceful Shutdown Daemon

## 1. Failure Mode Reference
- **ID:** `FM-SYS-004`
- **Subsystem:** Hardware / Power (`battery_management / OS`)
- **Failure:** Corruzione del filesystem (SSD NVMe / micro-SD) e freeze da spegnimento brutale per crollo di tensione (Battery Cliff).
- **Cause:** Stima SoC puramente lineare ingannata da cali di tensione transitori o scarica rapida senza filtro di persistenza; intervento del BMS hardware con taglio improvviso dell'alimentazione.
- **Initial RPN:** 315 (Severity: 9, Occurrence: 5, Detection: 7)

---

## 2. Solution Architecture & Implementation

### 1. Dual-Rail Power Path OR-ing Awareness
- Rilevamento dello stato di alimentazione da rete o docking station ($V \ge 12.70\text{V}$, bus tarato a **12.80V**).
- Isolamento del circuito di carica CC-CV tramite diodi ideali: SoC convenzionale (`percentage = -1.0`), inibizione allarmi docking/shutdown e segnalazione esplicita su Foxglove Studio `"IN CARICA (12.8V)"`.

### 2. Moving Average Anti-Sag Filtering & Persistence Timer
- **Buffer circolare FIFO:** Buffer di 20 campioni ADC a 5Hz ($V_{filtrata} = \frac{1}{N}\sum V_i$) per eliminare rumore e cadute transitorie dovute a picchi di spunto motore.
- **Timer di Persistenza (3.0s):** Transizioni di allarme critiche (Rientro alla Base e Shutdown di Sicurezza) innescate solo se la tensione filtrata permane al di sotto della soglia per almeno 3 secondi continuativi.

### 3. State Machine & Topic Routing
1. `CHARGING` ($V \ge 12.70\text{V}$): Status `POWER_SUPPLY_STATUS_CHARGING`, gauge Foxglove a 100%, blocco comandi di dock.
2. `BATTERIA OK` ($10.20\text{V} \le V < 12.65\text{V}$): SoC lineare $0\% - 100\%$, limite velocità $100\%$.
3. `ECO MODE` ($9.90\text{V} \le V < 10.20\text{V}$): Pubblicazione su `/speed_limit` di Nav2 con riduzione velocità ed accelerazione al 50% per abbattere il voltage sag.
4. `ALLARME RIENTRO` ($9.00\text{V} < V < 9.90\text{V}$ per $>3.0\text{s}$): Pubblicazione `True` su `/robot/docking/trigger`.
5. `CRITICAL SHUTDOWN` ($V \le 9.00\text{V}$ per $>3.0\text{s}$): Pubblicazione su `/robot/system/shutdown`, stop immediato motori a priorità 0 su `/cmd_vel_mux/input/safety_override`, flush filesystem (`sync`) ed esecuzione controllata di `sudo systemctl poweroff`.

### 4. Feed-Forward Voltage PID Compensation
$$PWM_{compensato} = PWM_{PID} \times \text{clamp}\left(\frac{11.10\text{V}}{V_{effettiva}}, 0.70, 1.40\right)$$
- $V_{effettiva} = 12.80\text{V}$ se $V \ge 12.70\text{V}$ (rete elettrica); $V_{effettiva} = V_{misurata}$ a batteria.

---

## 3. Residual Risk & RPN Scoring
- **Severity:** 9 (Preservata a 9 in quanto il potenziale danno hardware/filesystem è critico).
- **Occurrence:** 1 (Ridotta da 5 a 1 grazie al demone automatico di graceful shutdown, anti-sag filtering e dynamic speed throttling).
- **Detection:** 1 (Ridotta da 7 a 1 grazie alla media mobile ADC continua a 5Hz, telemetria Foxglove e timer deterministici di persistenza).
- **Residual RPN:** $9 \times 1 \times 1 = 9$ (Ridotto da 315 a 9, Rischio Completamente Mitigato e Chiuso).
