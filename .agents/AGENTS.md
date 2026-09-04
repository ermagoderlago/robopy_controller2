# Regole del Workspace - Marcus AI

Questo file definisce le istruzioni di sistema obbligatorie per tutte le IA che operano su questo workspace.

## 🔄 Workflow Obbligatorio di Programmazione (Book-to-Skill V2)

Quando ti viene richiesto di analizzare, correggere o implementare un modulo o un nodo ROS 2, DEVI seguire rigorosamente questa pipeline sequenziale:

1. **Allineamento dei Vincoli:** Leggi prima di tutto `marcus_core_rules.md` nella radice del workspace per comprendere i vincoli hardware critici e di sopravvivenza del robot (RAM, audio, CPU core pinning, 2.5D mapping).
2. **Consultazione Obbligatoria della Scheda Tecnica (`view_file` PREVENTIVO):**
   > [!CAUTION]
   > **DIVIETO ASSOLUTO DI SCRITTURA CODICE SENZA PREVIA LETTURA DELLA SCHEDA TECNICA.**
   > Prima di invocare qualsiasi tool di modifica o creazione codice (`write_to_file`, `replace_file_content`, o comandi shell che alterano file), l'agente DEVE eseguire `view_file` sulla specifica tecnica (`/docs/specs/SPEC-XX.md`) corrispondente al file target.
   - **Come identificare quale file leggere:**
     - Consulta la tabella di instradamento machine-readable in [`docs/specs/SPECS_ROUTING.yaml`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPECS_ROUTING.yaml) o l'indice in [`docs/specs/INDEX_SCHEDE_TECNICHE.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/INDEX_SCHEDE_TECNICHE.md).
     - Identifica il path pattern del modulo da modificare (es. `waveshare_motor_driver.py` ➔ `SPEC-01`, `semantic_costmap_injector.py` ➔ `SPEC-02`, `hailo_bridge_node.py` ➔ `SPEC-03`, `respeaker_vui_node.py` ➔ `SPEC-04`, `trinity/**` ➔ `SPEC-05`, `battery_manager_node.py` ➔ `SPEC-06`, `CMakeLists.txt` o script di build ➔ `SPEC-07`).
     - Esegui `view_file` sulla Scheda Tecnica identificata.
   - **Verifica delle Zone di Rischio nella Scheda:**
     - 🔴 **Zona Rossa (No-Touch):** Se la modifica proposta tocca o rilassa un vincolo di Zona Rossa, **DEVI RIFIUTARE L'AZIONE** e spiegare il limite fisico violato.
     - 🟡 **Zona Gialla (Human Gate):** Se la modifica altera interfacce ROS 2, pinout o modelli HEF, **NON MODIFICARE IL CODICE**: prepara una proposta/diff formale e chiedi autorizzazione esplicita all'utente.
     - 🟢 **Zona Verde (Auto-Evolution):** Se l'intervento rispetta i range validati e le prescrizioni della Zona Verde, procedi autonomamente.
3. **Aggiornamento Continuo delle Lezioni, ECO e Schede Tecniche:** 
   - Se risolvi un bug, implementi una nuova feature o scopri una specificità hardware, **DEVI aggiornare immediatamente** il file di lezioni tematiche corrispondente sotto `/docs/lessons/` (es. aggiungendo dettagli sul resampling audio in `audio_vui_pipeline.md`) e, se opportuno, la scheda tecnica in `/docs/specs/`.
   - Se effettui una modifica strutturale all'architettura o al build system, **DEVI registrare un nuovo ECO** compilando il relativo file in `/docs/ecos/` (es. `vision_hailo_ecos.md`).
   - Se le modifiche alterano le funzionalità generali del robot, gli scenari d'uso, o lo stato dei moduli principali, **DEVI aggiornare** la guida globale `marcus_robot_guide.md` nella radice.
4. **Verifica Cognitiva (`[COGNITIVE_CHECK]`):** Prima di emettere qualsiasi codice modificato, produci all'inizio della tua risposta un blocco di testo chiaramente visibile prefissato con **`[COGNITIVE_CHECK]`**. In questo blocco devi:
   - Dichiarare formalmente il path esatto della Scheda Tecnica letta tramite `view_file`: `Scheda Tecnica letta: /docs/specs/SPEC-XX.md`.
   - Attestare esplicitamente il rispetto dei vincoli di **🔴 Zona Rossa** di quella Scheda Tecnica e di `marcus_core_rules.md`.
   - Dichiarare la classificazione della modifica (**🟢 Zona Verde** o approvazione ottenuta per **🟡 Zona Gialla**).
   - Dichiarare quali file di specifiche (`/docs/specs/`), lezioni (`/docs/lessons/`), registri ECO (`/docs/ecos/`) o guide (`marcus_robot_guide.md`) hai letto ed eventualmente aggiornato.
5. **Integrazione FMEA-Lite (DFMEA):** Prima di iniziare l'implementazione o modificare codice, DEVI ispezionare il database DFMEA (`fmea/dfmea.yaml`) tramite tool di lettura file per identificare i rischi storici correlati. 
   - Se risolvi un guasto o introduci un nuovo rischio, aggiorna `fmea/dfmea.yaml` (creando un nodo o aggiornando history e punteggi residui).
   - Esegui autonomamente lo script `python fmea/calculate_and_report_fmea.py` tramite `run_command` per ricalcolare i Risk Priority Number (RPN) e generare il report esecutivo, prima di chiudere il task.
6. **Gestione Compartimentata dei Progetti di Miglioramento (Anti-Saturazione Contesto):**
   - Ogni Failure Mode (o gruppo di failure correlati) genera un **Progetto di Miglioramento autonomo** salvato come file Markdown isolato in `docs/improvements/IMP-XXX.md`.
   - **DIVIETO DI CARICAMENTO GLOBALE:** È severamente vietato leggere tutti i file di miglioramento contemporaneamente. Per evitare la saturazione della finestra di contesto dell'IA, consulta l'indice leggero `fmea/IMPROVEMENT_INDEX.yaml`.
   - Quando vieni incaricato di sviluppare o programmare una specifica mitigazione, **DEVI leggere esclusivamente il singolo file `docs/improvements/IMP-XXX.md` associato a quel task**, operando a compartimenti stagni.
