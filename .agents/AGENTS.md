# Regole del Workspace - Marcus AI

Questo file definisce le istruzioni di sistema obbligatorie per tutte le IA che operano su questo workspace.

## 🔄 Workflow Obbligatorio di Programmazione (Book-to-Skill V2)

Quando ti viene richiesto di analizzare, correggere o implementare un modulo o un nodo ROS 2, DEVI seguire rigorosamente questa pipeline sequenziale:

1. **Allineamento dei Vincoli:** Leggi prima di tutto `marcus_core_rules.md` nella radice del workspace per comprendere i vincoli hardware critici e di sopravvivenza del robot (RAM, audio, CPU core pinning, 2.5D mapping).
2. **Consultazione della Mappa dei Domini:** Identifica nella Mappa Spoke di `marcus_core_rules.md` il dominio del task corrente e leggi esclusivamente i relativi file di lezioni ed ECO tematici (situati in `/docs/lessons/` e `/docs/ecos/`). È vietato caricare documentazione non pertinente.
3. **Aggiornamento Continuo delle Lezioni ed ECO:** 
   - Se risolvi un bug, implementi una nuova feature o scopri una specificità hardware, **DEVI aggiornare immediatamente** il file di lezioni tematiche corrispondente sotto `/docs/lessons/` (es. aggiungendo dettagli sul resampling audio in `audio_vui_pipeline.md`).
   - Se effettui una modifica strutturale all'architettura o al build system, **DEVI registrare un nuovo ECO** compilando il relativo file in `/docs/ecos/` (es. `vision_hailo_ecos.md`).
   - Se le modifiche alterano le funzionalità generali del robot, gli scenari d'uso, o lo stato dei moduli principali, **DEVI aggiornare** la guida globale `marcus_robot_guide.md` nella radice.
4. **Verifica Cognitiva (`[COGNITIVE_CHECK]`):** Prima di emettere qualsiasi codice modificato, produci all'inizio della tua risposta un blocco di testo chiaramente visibile prefissato con **`[COGNITIVE_CHECK]`**. In questo blocco devi:
   - Attestare esplicitamente il rispetto dei vincoli fisici di `marcus_core_rules.md` applicati al codice che stai per scrivere.
   - Dichiarare quali file di lezioni (`/docs/lessons/`), registri ECO (`/docs/ecos/`) o guide (`marcus_robot_guide.md`) hai letto ed eventualmente aggiornato per rispecchiare la modifica.
5. **Integrazione FMEA-Lite (DFMEA):** Prima di iniziare l'implementazione o modificare codice, DEVI ispezionare il database DFMEA (`fmea/dfmea.yaml`) tramite tool di lettura file per identificare i rischi storici correlati. 
   - Se risolvi un guasto o introduci un nuovo rischio, aggiorna `fmea/dfmea.yaml` (creando un nodo o aggiornando history e punteggi residui).
   - Esegui autonomamente lo script `python fmea/calculate_and_report_fmea.py` tramite `run_command` per ricalcolare i Risk Priority Number (RPN) e generare il report esecutivo, prima di chiudere il task.
6. **Gestione Compartimentata dei Progetti di Miglioramento (Anti-Saturazione Contesto):**
   - Ogni Failure Mode (o gruppo di failure correlati) genera un **Progetto di Miglioramento autonomo** salvato come file Markdown isolato in `docs/improvements/IMP-XXX.md`.
   - **DIVIETO DI CARICAMENTO GLOBALE:** È severamente vietato leggere tutti i file di miglioramento contemporaneamente. Per evitare la saturazione della finestra di contesto dell'IA, consulta l'indice leggero `fmea/IMPROVEMENT_INDEX.yaml`.
   - Quando vieni incaricato di sviluppare o programmare una specifica mitigazione, **DEVI leggere esclusivamente il singolo file `docs/improvements/IMP-XXX.md` associato a quel task**, operando a compartimenti stagni.
