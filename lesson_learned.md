# Lesson Learned - Marcus AI System Stabilization

## 1. Workspace Isolation & Collision
**Problem**: Having duplicate packages across multiple workspaces (MMC and SSD) caused ROS 2 to link files using absolute paths that became invalid when folders were renamed or moved.
**Solution**: Consolidate all activity into a single, high-performance workspace on the SSD (`/mnt/ssd/robopy_controller_host`). Avoid `--symlink-install` during major structural changes, as it creates brittle persistent paths.

## 2. Porcupine Wake-Word Discovery
**Problem**: The `respeaker_vui_node` was failing to initialize because the `.ppn` model file was missing from the expected `install/share` directory.
**Solution**: 
- Ensure `setup.py` correctly globs the `config/` directory.
- Verify that `ament_index_python` correctly resolves the path at runtime.
- Use verbose logging (`[VUI-INIT]`) to confirm model paths during node startup.

## 3. Python installation & setup.py
**Problem**: A rogue directory named `install` inside the `models/` folder caused `colcon build` to fail because `setuptools` tried to copy it as a file.
**Solution**: Modernized `setup.py` to use a list comprehension that filters for files specifically: `[f for f in glob('robopy_controller/models/*') if os.path.isfile(f)]`. This prevents build failures from temporary or hidden directories.

## 4. Startup Race Conditions (Greetings)
**Problem**: The AI Orchestrator may attempt to send a greeting message before the audio playback nodes are fully initialized.
**Solution**: Implemented a retry mechanism for greetings that waits for subscribers to appear on the TTS/Audio topics before attempting to play the initial welcome message.

## 5. Gmail Authentication (IMAP/SMTP)
**Problem**: Google blocks access to "Less Secure Apps" and standard passwords if 2FA is enabled, causing `EmailSkill` login failures.
**Solution**: Must use a 16-character **Application-specific Password** (Google App Passwords) and ensure IMAP is enabled in Gmail settings. Normal passwords will always fail.

## 6. Shell Script Line Endings (CRLF vs LF)
**Problem**: Shell scripts (`.sh`) edited on Windows and saved with CRLF (`\r\n`) cause `-bash: $'\r': command not found` errors on Linux.
**Solution**: Always ensure scripts are saved with **LF** only. Use tools like `sed -i 's/\r$//' filename.sh` or editor settings to prevent injection of carriage returns.

## 7. LLMResponse Attribute: `.text` non `.response_text` (BUG CRITICO COMUNICAZIONE)
**Problem**: Il `ConversationManager` accedeva a `response.response_text` ma `LLMResponse` è una dataclass con attributo `.text`. Questo causava `response_text = ""` per ogni risposta, silenziosamente. Il robot non parlava, non pubblicava su ROS e non salvava in RAG.
**Solution**: Usare sempre `getattr(response, "text", "")` per accedere al testo della risposta LLM. Verificare che l'attributo esista prima di usare `get()` (solo per dict).

## 8. Modelli Gemini Free Tier (Nomenclatura Corretta - Aprile 2026)
**Problem**: Il codice usava `gemini-3.1-flash-lite-preview` (inesistente, causava 404) e `gemini-2.0-flash` (non più il modello Live ottimale).
**Solution**:
- **Standard API** (generate_content): usare `gemini-2.5-flash-preview-05-20` — free tier con molti token
- **Live API** (audio bidirezionale): usare `gemini-2.0-flash-live-001` — free tier con richieste illimitate, audio stabile
- **Fallback**: usare `gemini-2.0-flash-lite` come fallback leggero
- Verificare i modelli disponibili con: `python3 list_gemini_models.py`

## 9. SSH sync_marcus.sh - Password Multiple (SSH ControlMaster)
**Problem**: `sync_marcus.sh` apriva 4-5 connessioni SSH separate (per rsync, per PYTHON_VER, per chmod), ognuna richiedeva la password interattiva anche con chiave SSH configurata su Windows/Git Bash.
**Solution**: Usare SSH ControlMaster con socket di controllo: `ssh -o ControlMaster=auto -o ControlPath=/tmp/ctrl_sock -o ControlPersist=60`. La prima connessione autentica, tutte le successive riusano il tunnel senza password. Funziona sia su Linux che su Windows/Git Bash.

