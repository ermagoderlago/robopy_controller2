# Progetto di Miglioramento: IMP-PWR-003
## Telemetria Batteria via Sensore I2C INA219 e Rilevamento Dinamico Carica/Alimentazione Esterna

- **ID Progetto:** `IMP-PWR-003`
- **Failure Mode Correlato:** `FM-PWR-003` (DFMEA)
- **Dominio:** `hardware_power`
- **Stato:** `COMPLETED`
- **Data Completamento:** 2026-09-25

---

### 1. Descrizione del Problema
Il topic `/battery/raw` e `/battery_state` mostravano costantemente una tensione congelata a 12.60V (anche con batteria a differente livello di carica) e lo stato di alimentazione segnalava perennemente `POWER_SUPPLY_STATUS_DISCHARGING`, perfino quando il robot era alimentato da rete o in carica.

### 2. Causa Radice Identificata
1. **Assenza di Partitore Resistivo Analogico su Pin ESP32:**
   La scheda Waveshare General Driver for Robots non collega la linea `DC_IN` a nessun pin ADC dell'ESP32.
2. **Presenza Chip Digitale INA219:**
   La misura della tensione del bus di alimentazione e della corrente è affidata a un monitor Texas Instruments **INA219 (SOP-8)** connesso via **I2C all'indirizzo `0x42`** su **SDA = GPIO 32** e **SCL = GPIO 33**, con resistenza di shunt da $0.01\,\Omega$ ($10\text{ m}\Omega$) in serie su `DC_IN`.
3. **Lettura Fittizia nel Vecchio Firmware:**
   Il vecchio firmware `waveshare_bridge.h` eseguiva `analogRead(33)` definendo `PIN_BATTERY = 33`. Poiché GPIO 33 è la linea di clock I2C mantenuta a livello logico alto (3.3V) dalle resistenze di pull-up, l'ADC leggeva costantemente ~4095, calcolando ~36300 mV. Il nodo ROS 2 divideva per 2880.95 ottenendo stabilmente 12.60V.
4. **Stato Discharging Hardcoded:**
   Nel driver `waveshare_motor_driver.py` (linea 1129) il messaggio `BatteryState` pubblicava sempre e comunque `POWER_SUPPLY_STATUS_DISCHARGING`. Inoltre, poiché 12.60V < 12.70V (soglia di carica), il nodo `battery_manager_node` non transitava mai in stato "IN CARICA".

### 3. Azioni Correttive e Architettura della Soluzione
1. **Firmware ESP32 (`waveshare_bridge.h`):**
   - Eliminato `PIN_BATTERY = 33` e le letture ADC obsolete.
   - Implementato driver nativo ESP-IDF (`driver/i2c.h`) configurato sulla porta `I2C_NUM_0` (SDA=32, SCL=33, 100 kHz).
   - Inizializzazione registri INA219 (32V FSR, PGA 320mV, 12-bit ADC continuo).
   - Acquisizione registri Bus Voltage (0x02, mV reali) e Shunt Voltage (0x01, corrente in mA con fattore di shunt 10 mOhm).
   - Pubblicazione nel pacchetto seriale `T:1001` dei campi `"v": voltage_mv` e `"c": current_ma`.
2. **Compilazione Firmware WSL:**
   - Aggiornato script `compile_waveshare_wsl.sh` rimuovendo `PYTHONPATH` conflittuale e abilitando `export IDF_MAINTAINER=1`.
   - Generato il binario di produzione pronto per il flash:
     `/home/robopy/waveshare_build/output/waveshare_driver.factory.bin`.
3. **Nodi ROS 2:**
   - `waveshare_motor_driver.py`: aggiunto parametro `charging_threshold_voltage` (12.70V), supporto ricezione millivolti nativi ($500 < V \le 30000$) e corrente mA, classificazione dinamica `POWER_SUPPLY_STATUS_CHARGING` ($V \ge 12.70\text{V}$) o `DISCHARGING` ($V < 12.70\text{V}$), e pubblicazione della corrente su `bat_msg.current`.
   - `battery_manager_node.py`:
     - Parametrizzazione per 6 celle **Panasonic NCR18650B in configurazione 3S2P (6800 mAh, 75.5 Wh)**.
     - Implementazione della **Curva OCV empirica calibrata a 14 punti** per celle NCR18650B (eliminando l'errore dell'interpolazione lineare).
     - **Compensazione dinamica del Voltage Sag ohmico** ($V_{ocv} = V_{filt} + I \cdot R_{int}$, con $R_{int} = 0.085\,\Omega$ per il pacco 3S2P comprensivo di BMS e cablaggio).
     - Stima del carico base a riposo dello Step-Down 2 ($I_{quiescent} \approx 1.20\text{ A}$ a 12V per Pi 5 + Hailo + LiDAR + OAK-D).
     - Campi `capacity = 6.8 Ah`, `design_capacity = 6.8 Ah`, `charge = soc_ratio * 6.8 Ah` e `power_supply_technology = LION`.

### 4. Analisi Cablaggio Hardware e Rilevamento Corrente Totale
Dall'analisi dello schema di alimentazione (`media_1790327274569.png`), la corrente dello `STEP-DOWN 2` (Raspberry Pi 5 + Hub USB) non transita dallo shunt dell'INA219 della scheda Waveshare, che misura esclusivamente i motori DC.
- **Soluzione Software Immediata (Attiva):** Il nodo `battery_manager_node` somma la corrente reale dei motori (da INA219) al carico di base continuo del Pi 5 (1.20A), fornendo stima di SoC e Coulomb Counting senza modificare i cavi.
- **Miglioramento Professionale Hardware Futuro:** Inserimento di un modulo I2C INA219/INA226 dedicato sul cavo arancione (tra `P+` del BMS e l'Anodo del `DIODO IDEALE 2`), collegato direttamente all'I2C del Raspberry Pi 5 (GPIO 2/3), consentendo la misura bidirezionale hardware del 100% della corrente di scarica e di carica dal caricatore CC-CV 24V->12.6V.

### 5. Verifica e Collaudo
- Test unitari automatizzati `test/unit/test_battery_monitoring.py`:
  - `test_battery_manager_sample_normalization`: PASS
  - `test_battery_manager_charging_state_transition`: PASS
  - `test_battery_manager_discharging_state_transition`: PASS (Verificata curva NCR18650B OCV, capacità 6.80 Ah e tecnologia LION)
  - `test_waveshare_driver_charging_telemetry`: PASS
  - `test_waveshare_driver_discharging_telemetry`: PASS

