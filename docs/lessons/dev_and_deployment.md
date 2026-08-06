# Lezioni Apprese - Sviluppo, Build & Deployment

Questo documento raccoglie le linee guida operative, le ricette di build e le lezioni apprese in merito all'ambiente di sviluppo e deploy di Marcus.

---

## 🛠️ Compilazione ROS 2 e Limiti Hardware Pi 5

### Crash del Compilatore per OOM (Out-of-Memory)
* **Problema:** Compilando il pacchetto `robopy_controller` sul Raspberry Pi 5 usando il parallelismo di default di `colcon build`, la memoria RAM si esaurisce costringendo il sistema a killare il processo `clang++` (conseguente corruzione della directory `install/`).
* **Risoluzione:** Eseguire tassativamente la compilazione in modalità sequenziale limitando a 1 i thread di build ed i worker simultanei:
  ```bash
  MAKEFLAGS="-j1" colcon build --parallel-workers 1 --packages-select robopy_controller
  ```

### Isolamento degli Ambienti Virtuali (ROS vs Python)
* **Regola Permanente:** La compilazione di ROS 2 Core (`~/ros2_jazzy`) deve avvenire sempre nell'ambiente di sistema base. Non attivare mai `ros2env` o altri venv durante la compilazione del core ROS.
* L'esecuzione dei nodi del robot (`robopy_controller`), invece, richiede l'attivazione dell'ambiente virtuale dedicato:
  ```bash
  source ~/ros2env/bin/activate
  ```

---

## 🔄 Sincronizzazione Workspace e Hot-Swap

### Sincronizzazione Windows-Pi
* Durante lo sviluppo locale su PC Windows, le modifiche possono essere sincronizzate rapidamente senza rebuild intere sul robot tramite lo script:
  ```powershell
  .\sync_marcus.bat
  ```
  Questo script propaga i file Python modificati sia in `src/` che direttamente nei rispettivi percorsi in `install/` sul robot.

### Riavvio dei Nodi e Interlock del Watchdog
* Per riavviare i nodi sul robot, eseguire:
  ```bash
  bash /mnt/ssd/robopy_controller_host/restart.sh
  ```
* **Conflitto e Race Condition del Watchdog (FM-SYS-007):** Lo script/servizio `watchdog.sh` (o `marcus-watchdog.service`) monitora continuamente il processo `robot_ai_node`. Se l'operatore arresta o riavvia manualmente lo stack, l'avvio temporizzato di ROS 2 (sleep di 15-30s per inizializzazione camera e SLAM) viene scambiato dal watchdog per un crash. Il loop a 5s del watchdog lancia quindi ricorsivamente multiple istanze concorrenti di `restart_hailo.sh`, causando la moltiplicazione dei nodi (es. 8+ istanze di `robot_health_supervisor`), la saturazione dei 4 core CPU al 100% (Load Average > 35) ed il freeze del sistema.
* **Soluzione Interlock a Triplo Livello:** 
  1. Lo script `restart_hailo.sh` controlla la variabile `FROM_WATCHDOG`. Se vuota (riavvio manuale), esegue preventivamente `sudo systemctl stop marcus-watchdog.service` e `systemctl --user stop marcus-watchdog.service`.
  2. Per eliminare istanze standalone disaccoppiate da systemd, viene eseguito `pkill -9 -f watchdog.sh` all'inizio dello script di startup prima dello spawn dei nuovi nodi.
  3. Al termine dell'inizializzazione sequenziale, il watchdog viene riattivato in sicurezza.

---

## 📄 Incompatibilità dei Formati e Credenziali

### Incompatibilità Fine Riga (CRLF vs LF)
* **Problema:** Gli script shell (`.sh`) salvati su Windows falliscono l'esecuzione su Linux con errori `-bash: $'\r': command not found`.
* **Risoluzione:** Convertire i file in formato **LF** prima del salvataggio. Su Linux, sanare con: `sed -i 's/\r$//' <file>`.

### Credenziali Google ADC vs Gemini API Key
* **Problema:** Errore `Your default credentials were not found` sui servizi ASR/TTS.
* **Causa:** I modelli Gemini Pro utilizzano `GEMINI_API_KEY`, mentre i servizi professionali Google Cloud (Text-to-Speech e Speech-to-Text) richiedono obbligatoriamente le Application Default Credentials (ADC) tramite JSON di Service Account.
* **Risoluzione:** Senza ADC, il TTS Google fallirà. Configurare il percorso del JSON in `GOOGLE_APPLICATION_CREDENTIALS` nel file `setup_keys.sh` per attivare i servizi professionali, oppure forzare il fallback all'API Key di Gemini (se supportato dalle ultime versioni del client).

---

## 📡 Convenzioni QoS ROS 2 (BEST_EFFORT vs RELIABLE - FM-DDS-006)

* **Problema:** L'utilizzo del profilo QoS `BEST_EFFORT` sui publisher video/annotazioni visive (es. `/hailo/annotated_image/compressed`) o su topic di stato causa una perdita silenziosa del flusso dati su Foxglove Studio, Foxglove Bridge o la Web UI. I client di visualizzazione sottoscrivono di default con profilo `RELIABLE` e scartano automaticamente i pacchetti ricevuti via `BEST_EFFORT`.
* **Lezione Appresa & Regola Permanente di Architettura:**
  1. **Topic Visivi e Bridge (Foxglove/Web UI):** Tutti i publisher di immagini, annotazioni visive (YOLO/Volti/Semantica) e telemetria destinati a visualizzatori esterni DEVONO utilizzare la garanzia `qos_reliable` (o `10` depth queue).
  2. **Topic Audio a Bassa Latenza:** Solo i flussi di audio raw PCM a 16kHz fra nodi interni che richiedono latenza minima in real-time possono utilizzare `BEST_EFFORT`.
  3. **Ispezione Incompatibilità:** Durante i test di integrazione, verificare sempre la compatibilità QoS dei nodi con:
     ```bash
     ros2 topic info /hailo/annotated_image/compressed --verbose
     ```

