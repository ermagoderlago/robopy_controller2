---
description: Build the robopy_controller ROS 2 package
---

// turbo-all

# 🤖 CONTESTO PER L'IA E WORKFLOW DI SVILUPPO
**ATTENZIONE IA (Antigravity):** Attualmente stai girando in locale su un PC **Windows**. Il target di esecuzione del codice è un **Raspberry Pi 5** (ARM64, Linux) connesso tramite rete.
- **Sincronizzazione:** Le modifiche ai file sorgente vengono fatte da te (IA) in locale su Windows. Al salvataggio, i file vengono inviati automaticamente al Raspberry tramite SFTP.
- **Esecuzione Comandi:** NON puoi eseguire comandi Linux localmente. Tutti i comandi bash presenti in questo documento o suggeriti da te devono essere eseguiti dall'utente in un terminale separato connesso in **SSH** (`ssh robopy@marcus` - l'utente ha già configurato l'accesso senza password).
- **Cartelle Ignorate:** Le cartelle `build/`, `install/` e `log/` esistono solo sul Raspberry e non vengono scaricate in locale per evitare conflitti con i symlink di ROS 2. Non tentare di leggerle o modificarle.

---

# 🗺️ MAPPA DEL PROGETTO (CONTESTO)
**IMPORTANTE PER L'IA:** Prima di ogni modifica, consulta i seguenti file per capire chi sei, la struttura del progetto, i topic ROS 2 e le limitazioni hardware:

### 📁 File Identità di Marcus (LEGGERE SEMPRE ALL'AVVIO)
Questi file definiscono l'anima, il comportamento e la memoria di Marcus. Vengono caricati dal `NightlyDreamService` e aggiornati automaticamente durante il "dream" notturno. Devono essere sincronizzati sul Pi via `sync_marcus.sh`.

- [AGENTS.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/AGENTS.md): **Il tuo Spazio di Lavoro e regole comportamentali.** Leggilo SEMPRE all'avvio. Contiene le regole operative, la gestione della memoria e come interagire.
- [SOUL.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/SOUL.md): **L'anima di Marcus** — valori, identità fisica, vincoli etici. Viene iniettato nel manifest del Nightly Dream. Marcus può proporre aggiornamenti tramite il dream.
- [USER.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/USER.md): **Il profilo di Luca** — preferenze, abitudini, contesto. Aggiornato automaticamente dal Nightly Dream con osservazioni concrete.
- [MEMORY.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/MEMORY.md): **Memoria a lungo termine curata** — lezioni, pattern, idee. Aggiornata ogni notte dal Nightly Dream con le nuove conoscenze distillate.

### 📁 File Architettura e Contesto Tecnico
- [ai_context.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/ai_context.md): Descrizione manuale dell'architettura e dei vincoli embedded. **Questa è la tua fonte di verità principale.**
- [files_topic.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/files_topic.md): Mappatura dettagliata di ogni file con i relativi topic e servizi ROS 2. **Leggilo per capire le interconnessioni.**
- [WORKSPACE_STATE.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/WORKSPACE_STATE.md): Stato aggiornato del workspace (lista file e topic globali).
- [weights/lesson_learned.md](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/weights/lesson_learned.md): Archivio degli errori passati e lezioni imparate. **LEGGILO SEMPRE per evitare di ripetere errori già commessi in precedenza.**

---

# 🧠 CICLO DI AUTO-MIGLIORAMENTO DI MARCUS (IDENTITY FILES)

Marcus possiede un sistema di identità persistente basato su file che cresce nel tempo:

```
[Interazioni diurne] → [Memorie ChromaDB] → [Nightly Dream 03:00]
                                                      │
                         ┌────────────────────────────┼─────────────────────────┐
                         ▼                            ▼                         ▼
                    MEMORY.md              USER.md (profilo Luca)       continuous_improvements.md
               (memoria curata)        (preferenze osservate)          (log tecnico grezzo)
```

**Flusso del Nightly Dream (`NightlyDreamService.run_analysis()`):**
1. Recupera le ultime 24h di memorie da ChromaDB
2. Carica `SOUL.md`, `USER.md`, `AGENTS.md`, `MEMORY.md` dal filesystem
3. Esegue analisi (Gemini single-pass o Gemini+DeepSeek collaborativo)
4. Estrae insight strutturati con Gemini
5. Aggiorna `MEMORY.md` con nuove osservazioni, lezioni, idee
6. Salva il report grezzo in `logs/continuous_improvements.md`
7. Se DeepSeek è disponibile, genera un **Master Prompt** da preporre al system prompt

**Percorsi sul Raspberry Pi:**
```
/mnt/ssd/robopy_controller_host/
├── SOUL.md          ← chi è Marcus (identità)
├── USER.md          ← chi è Luca (profilo utente)
├── AGENTS.md        ← regole operative
├── MEMORY.md        ← memoria a lungo termine curata
└── robopy_controller/logs/
    ├── continuous_improvements.md  ← log grezzo Nightly Dream
    └── master_prompt.txt           ← prompt evolutivo generato da DeepSeek
```

**Sincronizzazione:** I file identità vengono sincronizzati Windows→Pi tramite `sync_marcus.sh`. Dopo un Nightly Dream, usa `sync_from_marcus.sh` per recuperare `MEMORY.md` aggiornato.

> ⚠️ **IMPORTANTE:** Se aggiungi nuovi file di identità, aggiornali anche in `NightlyDreamService._load_identity_context()` e assicurati che `sync_marcus.sh` li includa.

---

# 🔄 SINCRONIZZAZIONE FILE
Sebbene VS Code possa sincronizzare i file via SFTP, per una **sincronizzazione completa e sicura** (inclusi permessi e cartelle specifiche), usa lo script di sincronizzazione modernizzato:

```powershell
# Da terminale PowerShell locale su Windows (usando bash per lo script):
bash sync_marcus.sh
```

> ⚠️ **REGOLA CRITICA PER L'IA SUI PATH DEI SORGENTI:**
> Non modificare MAI copie di backup dei file che si trovano nella root del progetto. Quando modifichi file Python come le Skill o i Nodi, **DEVI TASSATIVAMENTE modificare il file sorgente originale situato all'interno della cartella `robopy_controller/`** (es. `robopy_controller/robot_ai/skills/builtin/email_skill.py`). Se modifichi file clonati o temporanei presenti nella cartella root di Windows, `sync_marcus.sh` li ignorerà!

Lo script si occupa di:
1. Copiare `CMakeLists.txt`, `srv/`, `msg/`, `launch/` e `robopy_controller/robot_ai/`.
2. Aggiornare i nodi in `robopy_controller/nodes/`.
3. Impostare i permessi di esecuzione (`chmod +x`) sui nodi remoti.

bash restart.sh
```

---

# ⏪ SINCRONIZZAZIONE INVERSA (BACK-SYNC)
Se le skill sono state generate, approvate o il manifest è stato modificato direttamente sul Raspberry Pi (o se i log sono stati aggiornati), DEVI recuperare questi file sul PC locale prima di fare un nuovo forward-sync (per evitare di sovrascriverli con versioni vecchie).

```powershell
# Da terminale local su Windows:
bash sync_from_marcus.sh
```

Lo script recupera:
1. Nuove skill in `active/`, `staging/` o `failed/`.
2. Il file `skills_manifest.json` aggiornato.
3. I log di generazione e di runtime in `robopy_controller/logs/`.
4. Eventuali aggiornamenti automatici a `WORKSPACE_STATE.md` e `files_topic.md`.

---

# 📝 AGGIORNAMENTO CONTESTO (MANDATORIO)
**IMPORTANTE PER L'IA:** Se aggiungi nuovi file, rinomini nodi o modifichi i topic/servizi ROS 2, DEVI aggiornare la documentazione automatica eseguendo:

```powershell
# Da terminale PowerShell locale su Windows:
Questo comando aggiornerà `WORKSPACE_STATE.md` e `files_topic.md`. Se le modifiche impattano l'architettura generale, ricordati di aggiornare manualmente anche `ai_context.md`.

Inoltre, se modifichi il manuale tecnico (`weights/gemini_autocoscienza_robot.md`), devi aggiornare la memoria a lungo termine (RAG/RAK) di Marcus sul Raspberry Pi:

```bash
# Da terminale SSH sul Raspberry Pi (in ambiente venv):
source /home/robopy/ros2_venv/bin/activate
python3 /mnt/ssd/robopy_controller_host/weights/ingest_knowledge.py
```

---

# 🛠️ REGOLE DI SISTEMA E COMPILAZIONE

## 1. Gestione Ambienti
- **Compilazione ROS 2 Core (`~/ros2_jazzy`):** Deve avvenire SEMPRE in ambiente di sistema base. **NON** attivare `ros2_venv` per compilare il core.
- **Esecuzione/Lancio Robot (`robopy_controller`):** Deve avvenire in ambiente virtuale `ros2_venv`.

## 2. Compilazione pacchetti ROS 2 Core
I pacchetti ROS2 sono installati in:
```bash
cd ~/ros2_jazzy
```
E per compilarli uso (DA AMBIENTE DI SISTEMA BASE - NO VENV!!):

```bash
colcon build \
  --symlink-install \
  --packages-select depthai_core \
  --cmake-args \
    -GNinja \
    -DBUILD_TESTING=OFF \
    -C ~/ros2_jazzy/pi5_optimization.cmake \
    -DCMAKE_VERBOSE_MAKEFILE=ON \
    -DCMAKE_PREFIX_PATH="$HOME/ros2_jazzy/install" \
    -DCMAKE_FIND_ROOT_PATH="$HOME/ros2_jazzy/install"
```
🚨 ATTENZIONE MAI CANCELLARE I FILE COMPILATI DI ROS2 NELLA CARTELLA  ~/ros2_jazzy!!!!   MAI!!! ATTENZIONE

## 3. Build the package (robopy_controller)
```bash
cd /mnt/ssd/robopy_controller_host
colcon build --packages-select robopy_controller \
  --symlink-install \
  --cmake-clean-first \
  --cmake-args -GNinja \
    -DBUILD_TESTING=OFF \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -C /mnt/ssd/ros2_jazzy/pi5_clang_optimization.cmake
```

## 4. Per eseguire il robot (DOPO la compilazione)
Prima di lanciare qualsiasi nodo, per attivare la chiave IA devi usare:

```bash
source /mnt/ssd/robopy_controller_host/setup_keys.sh
```
Quindi, attiva l'ambiente ed esegui il source dell'installazione:

```bash
source ~/ros2_venv/bin/activate
cd /mnt/ssd/robopy_controller_host
source install/setup.bash
```

⚠️ IMPORTANTE: Aggiungere un nuovo Nodo Python a robopy_controller
Poiché robopy_controller è un pacchetto misto C++/Python compilato con ament_cmake, aggiungere un nodo Python al console_scripts del setup.py NON BASTA. La macro ament_python_install_package non genererà automaticamente gli eseguibili nella cartella libexec durante una build ament_cmake in ROS 2 Jazzy.

Quando crei un nuovo nodo Python, DEVI seguire esattamente questi 5 passaggi:

1. Crea il tuo codice Python (es. robopy_controller/nodes/mio_nuovo_nodo.py).
2. Aggiungilo a console_scripts in setup.py.
3. Crea uno script eseguibile "wrapper" nella cartella scripts/ (es. scripts/mio_nuovo_nodo senza estensione .py).
4. Rendi lo script eseguibile: `chmod +x scripts/mio_nuovo_nodo`
5. Registralo nel CMakeLists.txt aggiungendo la riga scripts/mio_nuovo_nodo nel blocco esistente `install(PROGRAMS ... DESTINATION lib/${PROJECT_NAME})`!

🛠️ Monitoraggio SSD e Ottimizzazione
Il workspace è ora su SSD (/mnt/ssd/ros2_jazzy).
Monitora lo spazio con: `df -h /mnt/ssd`.
Le build ora usano lo stripping dei simboli (-Wl,--strip-all) per ridurre l'impronta su disco e RAM.

## ⚡ 7. COMPILAZIONE FIRMWARE ESP32 (RESPEAKER)
- **Ambiente di Compilazione:** Il firmware del microfono DEVE essere compilato usando **WSL (Ubuntu su Windows)** in locale. Non compilarlo MAI sul Raspberry Pi.
- **Workflow per l'IA:**
  1. Istruisci l'utente ad aprire un terminale **WSL** integrato in VS Code.
  2. Fornisci il comando per la SOLA compilazione: `esphome compile respeaker_lite_firmware.yaml`
  3. **Regola per il Flashing:** Una volta terminata la compilazione, scrivi all'utente di aprire il browser su Windows, andare su `https://web.esphome.io` e flashare il firmware da lì.

## ⚡ 8. WORKFLOW ESP32 (FLASHING)
Quando devi far testare una modifica al microfono all'utente, usa uno di questi metodi:

**Opzione A: Aggiornamento Wi-Fi (OTA)**
Chiedi all'utente di aprire il terminale **WSL locale** ed eseguire:
`esphome run respeaker_lite_firmware.yaml`

**Opzione B: Passaggio file per flash via cavo USB (Emergenza)**
1. Compila in **WSL locale**: `esphome compile respeaker_lite_firmware.yaml`
2. Estrai il file `.bin`: `mkdir -p firmware_pronto && cp .esphome/build/*/.*/*/firmware.bin firmware_pronto/respeaker.bin`
3. Flash da **terminale SSH** del Pi:
   `source ~/esphome_venv/bin/activate`
   `esphome run respeaker_lite_firmware.yaml --device /dev/ttyACM0`

**Opzione C: Flash manuale con esptool (Metodo Diretto)**
1. Compila in **WSL locale**:
   `esphome compile respeaker_lite_firmware.yaml`
2. Copia il file binario sul Raspberry Pi (Marcus):
   `scp .esphome/build/respeaker-lite/.pioenvs/respeaker-lite/firmware.factory.bin robopy@marcus:/tmp/firmware2.factory.bin`
3. Esegui il flash da **terminale SSH** sul Raspberry Pi:
   `source /home/robopy/ros2_venv/bin/activate`
   `esptool --port /dev/ttyACM0 --baud 115200 write_flash 0x0 /tmp/firmware2.factory.bin`

## ⚡ 9. ESECUZIONE COMANDI SSH DA POWERSHELL (WINDOWS)
**ATTENZIONE IA:** Da PowerShell, i comandi SSH con `source` e heredoc **non funzionano** direttamente perché SSH di Windows usa `sh` e non `bash`. Usare uno di questi pattern:

**Pattern A: Script file (CONSIGLIATO)**
1. Crea un file `.sh` in locale (es. `/tmp/mio_script.sh`)
2. Copialo sul Pi: `scp /tmp/mio_script.sh robopy@marcus:/tmp/mio_script.sh`
3. Eseguilo: `ssh robopy@marcus "bash /tmp/mio_script.sh"`

Lo script deve contenere tutti i `source` necessari. Template standard:
```bash
#!/bin/bash
source /opt/ros/jazzy/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash
# ...i tuoi comandi ros2 qui...
```

**Pattern B: Comandi semplici (senza source)**
Per comandi che non richiedono ambiente ROS 2:
```powershell
ssh robopy@marcus "ls /dev/ttyACM*"
ssh robopy@marcus "cat /tmp/output.log"
```

**⚠️ NON FUNZIONA da PowerShell:**
```powershell
# ERRORE: source non funziona in sh
ssh robopy@marcus "source ~/ros2_venv/bin/activate && ros2 node list"
# ERRORE: heredoc rotto dal quoting PowerShell
ssh robopy@marcus "cat > /tmp/x.sh << 'EOF' ..."
```

## 🧠 10. GENERAZIONE NUOVE SKILL PER L'IA
**ATTENZIONE IA (Antigravity):** Quando l'utente ti chiede di creare una nuova "Skill" per il robot (es. "fai in modo che Marcus sappia cercare su internet" o "fai una skill per accendere la TV"), **NON INIZIARE MAI A SCRIVERE CODICE A MANO O A CREARE FILE CASUALI.**

Devi **TASSATIVAMENTE** usare la pipeline generativa standard di Marcus.
Segui questi passi esatti:
1. Leggi il workflow automatico dedicato: `view_file` su `.agent/workflows/crea_skill.md`.
2. Lì troverai le istruzioni esatte su come usare lo script Python di orchestrazione (`SkillGeneratorPipeline`) che genererà il prompt e lo validerà per te in modo sicuro.
3. Se hai dubbi sulle capability disponibili, guarda in `base_skill.py`.
4. Una volta creata la skill e validata, ricorda di far aggiornare all'utente `skills_manifest.json` per attivarla.

---

## 🐙 11. DEPLOY SU GITHUB (AUTOMAZIONE)
**ATTENZIONE IA (Antigravity):** Se l'utente ti chiede di "caricare il codice su GitHub", "fare un push" o "preparare la release", hai a disposizione una pipeline dedicata che automatizza la pulizia e la validazione.

**Workflow da seguire:**
1. **Verifica:** Prima di caricare, assicurati che `.gitignore`, `README.md` e `requirements.txt` siano aggiornati (creati nell'aprile 2026).
2. **Esecuzione Pipeline:** Esegui lo script bash locale (Windows/PowerShell) che gestisce lo smoke test e il commit:
   ```powershell
   bash github_push.sh "messaggio descrittivo del commit"
   ```
3. **Commit:** Lo script userà automaticamente il prefisso `feat:` (Conventional Commits).
4. **Push:** Se il remote `origin` è configurato, lo script tenterà il push automatico. Se non lo è, chiedi all'utente l'URL del repository remoto.

💡 **Consiglio:** Se lo smoke test fallisce, NON forzare il push. Analizza i file con errori di sintassi e correggili prima di riprovare.