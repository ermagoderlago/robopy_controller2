# 🚀 Guida alla Migrazione Marcus su Nuovo PC

Questa guida contiene tutte le istruzioni per configurare il nuovo PC e continuare a programmare **Marcus** con **Antigravity** in meno di 5 minuti, senza perdere chiavi SSH, credenziali o configurazioni.

---

## 🤖 Istruzioni per Antigravity sul Nuovo PC
> [!IMPORTANT]
> **Se sei l'agente Antigravity in esecuzione sul nuovo PC e l'utente ti ha chiesto di leggere questo file:**
> 1. **Verifica il workspace corrente:** Assicurati di trovarti nella radice del repository `robopy_controller2` (branch `agent/milestone-2-5-autonomous-perception-nav`).
> 2. **Individua il backup estratto:** Cerca la cartella estratta da `MARCUS_ANTIGRAVITY_MIGRATION_BACKUP.zip` (potrebbe essere in `Downloads`, sul `Desktop`, o in `G:\Il mio Drive\` / `G:\My Drive\` se Google Drive è installato).
> 3. **Esegui il ripristino automatico:**
>    - Se presente, esegui lo script `powershell -ExecutionPolicy Bypass -File .\restore_marcus_backup.ps1` indicando il path del workspace.
>    - In alternativa esegui manualmente i 3 comandi:
>      - Copia `workspace_secrets/.env` in `<workspace>/.env`
>      - Copia `ssh_keys/*` in `~/.ssh/`
>      - Copia `gemini_config/*` in `~/.gemini/`
> 4. **Verifica finale:** Esegui un comando SSH non interattivo: `ssh -o BatchMode=yes robopy@192.168.1.11 "echo Marcus Pi5 Connected"` per confermare che Marcus è raggiungibile.

---

## 📦 1. Contenuto del Pacchetto di Backup

Il file **`MARCUS_ANTIGRAVITY_MIGRATION_BACKUP.zip`** (2.37 MB) contiene:

| Cartella / File | Descrizione | Perché non era su Git? |
| :--- | :--- | :--- |
| `workspace_secrets/.env` | Tutte le API Keys attive (Gemini, DeepSeek, Picovoice, Home Assistant, Spotify) | Sicurezza (escluso da `.gitignore`) |
| `ssh_keys/` | `id_ed25519`, `id_rsa`, `config`, `known_hosts` | Accesso SSH immediato a Marcus Pi 5 (`192.168.1.11`) senza password |
| `gemini_config/` | Configurazione Antigravity: server MCP NotebookLM, science plugins, `google_accounts.json`, `GEMINI.md` | Configurazione utente globale Antigravity |
| `maps/` | Tutte le mappe SLAM 2D/2.5D (`pianoterra2.*`, `pianoterra3.*`) | Mappe cartografiche |
| `restore_marcus_backup.ps1` | Script PowerShell per ripristinare tutto con 1 click | Automazione ripristino |
| `MIGRATION_GUIDE.md` | Questa guida | Documentazione |

---

## ☁️ 2. Caricamento su Google Drive (PC Attuale)

1. Apri Google Drive nel browser: [https://drive.google.com](https://drive.google.com)
2. Trascina dentro Google Drive i due file presenti sul tuo **Desktop**:
   - `MARCUS_ANTIGRAVITY_MIGRATION_BACKUP.zip`
   - `MIGRATION_GUIDE.md`

---

## 💻 3. Configurazione sul Nuovo PC (3 Passaggi)

### Passo 1: Clona il Repository da GitHub
Apri PowerShell sul nuovo PC nella cartella dei tuoi progetti (es. `C:\Users\<TuoUtente>\Documents\robopy`):
```powershell
git clone https://github.com/ermagoderlago/robopy_controller2.git antigravity
cd antigravity
git checkout agent/milestone-2-5-autonomous-perception-nav
```

### Passo 2: Scarica ed Estrai il Backup da Google Drive
Scarica `MARCUS_ANTIGRAVITY_MIGRATION_BACKUP.zip` da Google Drive sul nuovo PC ed estrailo (es. in `Downloads\backup` o dove preferisci).
Poi, da PowerShell nella cartella estratta:
```powershell
# Esegui lo script di ripristino automatico:
.\restore_marcus_backup.ps1 -WorkspacePath "C:\Users\<TuoUtente>\Documents\robopy\antigravity"
```
*Oppure manualmente:*
1. **.env:** Copia `workspace_secrets\.env` in `C:\Users\<TuoUtente>\Documents\robopy\antigravity\.env`
2. **SSH:** Copia `ssh_keys\*` in `$HOME\.ssh\`
3. **Gemini:** Copia `gemini_config\*` in `$HOME\.gemini\`

### Passo 3: Apri Antigravity
Apri Antigravity sul nuovo PC aprendo la cartella `antigravity`.
Tutti i prompt, le schede tecniche (`docs/specs/`), i vincoli operativi (`marcus_core_rules.md`), i registri DFMEA e la connessione SSH a Marcus funzioneranno immediatamente al 100%!
