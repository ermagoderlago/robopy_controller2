# 🛠️ Progetto di Miglioramento: IMP-NOM-011

## Risoluzione Tool Shadowing ed Esecuzione Deterministica Skill NOMAD Exploration

- **ID Failure Correlato:** `FM-NOM-011`
- **Componenti:** `robopy_controller.robot_ai.skills.builtin.nomad_exploration_skill`, `visual_exploration_skill`, `orchestrator.py`
- **Stato:** `COMPLETED`
- **Data Attivazione:** 2026-09-27
- **Ambito:** Navigazione Reattiva, AI Tool Calling, NOMAD

---

### 1. Descrizione del Problema
Quando l'utente chiedeva a voce: *"esplora la stanza con nomad"*, Marcus rispondeva vocalmente che avrebbe avviato l'esplorazione, ma subito dopo dichiarava: *"Uhm... ops! Ho avuto un piccolo intoppo durante l'esplorazione."* e rimaneva completamente immobile senza avviare il nodo NOMAD.

### 2. Analisi delle Cause Radice
1. **Collisione Semantica dei Tool (Shadowing):**
   Nel registry convivevano due skill di esplorazione: `visual_exploration` (vecchio prototipo basato su screenshot e prompt cloud) e `nomad_exploration` (il controller reattivo a 4 Hz `nomad_reactive_pipeline_node`). Poiché `visual_exploration` aveva nella descrizione *"Esplora la stanza analizzando le immagini con Gemini..."*, l'LLM invocava `visual_exploration` anziché `nomad_exploration`.
2. **Fallimento Esecutivo di `visual_exploration`:**
   Invocata durante lo streaming Live, la skill tentava una chiamata REST secondaria `generate()` con parsing JSON che andava in errore o timeout, fallendo con il messaggio *"piccolo intoppo"*.
3. **Mancanza di parametri strutturati in `nomad_exploration`:**
   `nomad_exploration` non esponeva uno schema parametri chiaro (`action: start/stop`) a Gemini, affidandosi solo a regex di testo grezzo nel context.

### 3. Soluzione Ingegneristica (Mitigazioni Applicate)
1. **Consolidamento Unico Tool Esplorativo:**
   `nomad_exploration` diventa l'unico tool di esplorazione registrato nell'Orchestrator per l'LLM, con descrizione chiara ed univoca.
2. **Schema Parametri Esplicito:**
   Aggiunto `get_parameters_schema()` con parametro obbligatorio `action: ["start", "stop"]`.
3. **Gestione Flessibile in `execute()`:**
   La skill gestisce sia parametri strutturati da Tool Call (`context={"action": "start"}`) sia input da testo naturale.
4. **Delega Trasparente di Sicurezza:**
   In `visual_exploration_skill.py`, se per qualsiasi motivo dovesse essere invocata, la skill reindirizza l'esecuzione direttamente a `NomadExplorationSkill`, garantendo che l'esplorazione della stanza attivi sempre e comunque il pipeline reattivo `/nomad/enable`.

### 4. Metriche di Verifica e RPN
- RPN Iniziale: 294 (S:7, O:7, D:6)
- RPN Residuo: 14 (S:7, O:1, D:2)
- Collaudo: Invocazione vocale *"esplora la stanza con nomad"* -> chiamata a `nomad_exploration` con `{"action": "start"}`, pubblicazione su `/nomad/set_mode` e `/nomad/enable`, avvio del moto con scansione ostacoli.
