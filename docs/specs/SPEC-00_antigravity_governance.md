# 📋 SPEC-00: Meta-Regole di Auto-Evoluzione & Governance Antigravity

## 1. Identificazione e Scopo
- **ID Specifica:** `SPEC-00`
- **Ambito:** Governance Globale dell'Agente Antigravity residente su Marcus (Raspberry Pi 5 / Gemini 3.8).
- **Obiettivo:** Regolamentare le modalità operative con cui l'IA legge, modifica, testa e promuove codice, impedendo bricking del robot, regressioni del software o saturazione delle risorse host.
- **DFMEA Correlati:** `FM-SYS-001` (OOM Kill), `FM-SYS-008` (RAM Pressure), `FM-SIM-001` (Shadow Sandbox).

---

## 2. Modello Operativo dell'Agente su Pi 5

Quando Antigravity è attivo sul robot:
1. **Lavora sempre in isolamento (Git Feature Branch):** È severamente vietato modificare direttamente il branch `main` o i file installati in produzione (`/mnt/ssd/robopy_controller_host/install/`). Ogni sviluppo deve avvenire su un branch dedicato `agent/IMP-XXX-descrizione`.
2. **Validazione Sandboxed:** Prima di proporre o unire codice, l'agente DEVE eseguire i test di non-regressione locali (`pytest`, check sintassi, test di importazione) su un'istanza shadow o mock.
3. **Verifica Risorse:** Durante la generazione o il test, l'agente deve monitorare che il consumo di memoria RAM non superi l'80% (3.2 GB su 4.0 GB totali).
4. **Divieto Assoluto di Forzatura Soluzioni su Raspberry Pi (Zero-Forcing Policy):** Mentre su PC lo sviluppatore umano può decidere di forzare una modifica o ignorare un warning, sul Raspberry Pi l'agente Antigravity NON HA ALCUNA AUTORIZZAZIONE A FORZARE. Se una soluzione fallisce la validazione AST (`SecurityValidator`) o lo smoke test cinematico in `SkillSandbox`, viene scartata (max 3 tentativi di retry, poi abort); se tocca la Zona Rossa, viene obbligatoriamente dirottata in `RED_ZONE_IDEAS_RFC.md`.
5. **Dicitura Obbligatoria negli ECO:** Ogni Engineering Change Order creato a seguito di codice o refactoring generato dal robot deve riportare esplicitamente: `* **Autore:** 🤖 **Generata autonomamente da Marcus** (Antigravity Engine)`.
6. **Modello AI Primario:** L'agente adotta come modello primario la famiglia **Gemini 3.8** (`gemini-3.8-flash` con extended thinking integrato / `gemini-3.8-pro`), con fallback trasparente a serie 2.5.

---

## 3. 🔴 ZONA ROSSA (Inviolabili - NO AUTONOMOUS TOUCH)

Gli elementi di questa tabella sono **protetti a livello di sistema**. L'agente non può alterarli in nessun caso.

| Vincolo di Sicurezza | Valore / Regola Inviolabile | Rischio Ingegneristico | DFMEA |
| :--- | :--- | :--- | :--- |
| **Branch di Produzione** | Divieto di commit/push diretto su `main` o `master` | Rilascio di codice instabile in esecuzione attiva sul robot | FM-SYS-008 |
| **Bypass delle Schede Tecniche** | Divieto di disabilitare o ignorare le prescrizioni `SPEC-XX` | Violazione dei limiti fisici del robot con rottura hardware | Tutti |
| **Kill Switch di Sicurezza** | Non toccare la priorità di arresto su `/cmd_vel_mux/input/safety_override` | Impossibilità per il supervisore di bloccare il robot | FM-MOT-001 |
| **Compilazione Concorrente** | Divieto di lanciare `colcon build` con più di 1 worker (`MAKEFLAGS="-j1"`) | OOM Kill immediato da parte del kernel Linux e crash Pi 5 | FM-SYS-001 |
| **Arresto Pre-Build** | Obbligo di arrestare tutti i nodi e il watchdog prima di compilare | Mancanza di RAM sufficiente per il compilatore C++ clang++ | FM-SYS-001 |
| **Secrets & Credenziali** | Divieto di modificare o committare file di segreti (`secrets.yaml`, `.env`) | Esposizione di chiavi API private Gemini/Home Assistant | - |

---

## 4. 🟢 ZONA VERDE (Auto-Evolution - MIGLIORAMENTO AUTONOMO CONSENTITO)

L'agente Antigravity **è autorizzato e incoraggiato** a compiere le seguenti attività in piena autonomia:

| Ambito di Miglioramento | Azioni Consentite | Limiti e Condizioni di Accettazione |
| :--- | :--- | :--- |
| **Refactoring di Codice Interno** | Semplificazione algoritmi, rimozione duplicazioni, pulizia funzioni | A parità di interfaccia pubblica; 100% test unitari superati |
| **Ottimizzazione RAM & CPU** | Conversione array in `np.float16`, pre-allocazioni, LRU cache limitate | Test di non-regressione prestazionale e memory leak audit |
| **Tuning Iperparametri** | Aggiustamento pesi filtri, decay rate, finestre temporali | Esclusivamente entro i range consentiti nelle singole `SPEC-XX` |
| **Prompt Engineering** | Miglioramento prompt RAG, TRINITY, CAG, tool description | Rispetto del token budget (~2200 token max per sessione) |
| **Testing & Copertura** | Creazione di nuovi test unitari (`test_*.py`), mock di sensori | Non devono dipendere da hardware fisico inesistente o offline |
| **Documentazione & FMEA** | Aggiornamento `/docs/lessons/`, `/docs/ecos/`, `dfmea.yaml` | Coerenza e sincronizzazione con il codice effettivo |

---

## 5. 🟡 ZONA GIALLA (Human-in-the-Loop - APPROVAZIONE UMANA OBBLIGATORIA)

Per le seguenti modifiche l'agente DEVE generare una proposta strutturata (PR / ECO proposal) e attendere l'approvazione dell'operatore umano:

1. **Alterazione Contratti ROS 2:** Creazione, rinominazione o cancellazione di Topic, Service o Action; modifica del formato di messaggi custom (`.msg`, `.srv`, `.action`).
2. **Aggiornamento Dipendenze Host:** Modifiche a `requirements_ai.txt`, `package.xml`, installazione pacchetti APT (`sudo apt install`), aggiornamento librerie HailoRT o PyTorch.
3. **Modifica Modelli NPU:** Sostituzione di file HEF (`.hef`), unione di contesti con `hailo join`, cambio di risoluzioni di input della telecamera.
4. **Modifiche a Systemd & Boot:** Alterazione dei servizi di sistema (`marcus-watchdog.service`, `marcus-robot.service`, `/etc/systemd/system/`).
5. **Variazioni FMEA Severity:** Modifica dei punteggi di Gravità (Severity) nel file `fmea/dfmea.yaml` (la severità è un giudizio di rischio umano).

---

## 6. Procedura di Verifica & Rollback Autonomo (Pre-Merge Protocol)

Prima di consolidare qualsiasi lavoro sul branch di feature, l'agente DEVE eseguire la seguente sequenza:

```bash
# 1. Verifica statica della sintassi e assenza di BOM
python3 -m py_compile $(git diff --name-only --diff-filter=d | grep '\.py$')

# 2. Esecuzione test unitari di regressione
pytest tests/ -v --maxfail=1

# 3. Controllo memory leak / allocazioni su mock
python3 tests/test_memory_leak_guard.py

# 4. In caso di fallimento: rollback automatico e pulizia
git checkout -- .
```
Se anche un solo controllo fallisce, l'agente deve annullare la modifica (`rollback`), isolare la failure in un log di diagnostica e ripianificare l'intervento.
