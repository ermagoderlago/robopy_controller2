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

## 10. Fast-Path Skill Match vs LLM Tool Calling
**Problem**: Le skill (come Spotify) con regex troppo flessibili (es. solo la parola "spotify") attivavano il fast-path (match >= 0.95), bypassando l'LLM. Questo impediva a Gemini di estrarre gli argomenti strutturati (es. la query della canzone) e causava fallimenti silenziosi o azioni vuote. Inoltre, il fast-path in `conversation.py` non pubblicava la risposta sul topic ROS `/ai/conversation/response`.
**Solution**: 1. Abbassato il punteggio di match grezzo in `spotify_skill.py` (da 0.95 a 0.8) per lasciare a Gemini il compito di gestire il tool calling tramite le sue function. 2. Aggiunto `self.response_callback(response_text)` e aggiornato `recent_interactions` nel fast-path di `conversation.py` per propagare correttamente le risposte sul frontend Foxglove.

## 11. Spotify Connect (Raspotify/Librespot) vs ALSA/PulseAudio Sandbox
**Problem**: Usando il servizio di sistema di default `raspotify` su Raspberry Pi, il client Spotify non riusciva ad aprire la scheda audio (Errore 16 ALSA Device Busy o PulseAudio Permission Denied) perché la scheda era in uso dai nodi vocali ROS di `robopy` (TTS e microfono). Inoltre le regole di sandboxing di systemd (`ProtectHome=true`, `PrivateUsers=true`) impedivano la condivisione dell'IPC audio, facendo crashare il demone Spotify quando selezionato dal telefono.
**Solution**: Disabilitare il servizio di sistema di raspotify e configurare `librespot` come **Servizio Utente** (`systemctl --user`). Avviando il servizio tramite l'ambiente di `robopy`, `librespot` si aggancia istantaneamente al demone PulseAudio dell'utente, permettendo il mixing automatico dell'audio (la musica si abbassa/mixa con la voce del robot senza conflitti ALSA).

## 12. Errore 403 Spotify API "Cannot control device volume"
**Problem**: Chiedendo a Marcus di modificare il volume di Spotify, le API Web di Spotify restituivano un errore `403` rifiutandosi di modificare il volume di Raspotify/Librespot. Questo perché Spotify blocca nativamente le richieste API di volume verso client Connect di terze parti per evitare danni agli speaker.
**Solution**: Aggirare completamente le API Web di Spotify nello script Python `spotify_skill.py`. Quando viene intercettato il comando di volume, l'azione `volume_set` esegue direttamente a livello di sistema operativo `subprocess.run(['amixer', 'sset', 'Master', 'X%'])`. Questo modifica il volume hardware globale dell'intero speaker del robot, coerentemente con come operano i veri assistenti vocali.

## 13. Sincronizzazione manuale "Hot-Swap" dei file Python
**Problem**: In caso di debug e correzione rapida degli script Python senza usare `sync_marcus.sh` o senza voler ricompilare l'intero workspace ROS 2 con `colcon build`, i file sorgenti modificati non venivano caricati dal robot al riavvio.
**Solution**: ROS 2 carica gli script Python dalla cartella `install/`, non da `robopy_controller/`. Per aggiornare al volo un file Python sul Raspberry Pi:
1. Copiare il file nel codice sorgente locale: `scp my_skill.py robopy@marcus:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/active/`
2. **Copiare il file anche nella cartella installata (site-packages)**: `scp my_skill.py robopy@marcus:/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/robot_ai/skills/active/`
3. Riavviare il demone (`bash restart.sh`) per caricare la modifica all'istante senza build.

## 14. PipeWire "auto_null" Dummy Output & PyAudio ALSA Lock
**Problem**: Nonostante Spotify Connect/Librespot fosse configurato perfettamente su PulseAudio e l'API rispondesse `success=True`, le casse rimanevano mute. Analizzando `pactl list sinks`, si è scoperto che il server audio PipeWire stava instradando l'output verso `auto_null` (un dispositivo virtuale "buco nero" silente). Questo accadeva perché lo script Python `respeaker_vui_node.py` usava PyAudio per agganciarsi al nome hardware "ReSpeaker", acquisendo un **lock esclusivo (hw:0) su ALSA**. Trovando la scheda fisica occupata da ROS, PipeWire non poteva accedervi e creava il sink fittizio `auto_null` dove scaricava la musica di Spotify in silenzio.
**Solution**: Modificato `_find_audio_devices` in `respeaker_vui_node.py` per ignorare il nome hardware fisico e forzare la ricerca di dispositivi virtuali nominati `"pulse"` o `"default"`. Costringendo PyAudio a connettersi al server virtuale (PipeWire) anziché direttamente all'hardware, il server PipeWire diventa l'unico padrone della scheda fisica e permette il mixing simultaneo (condivisione) di ROS TTS, microfono e Spotify Connect senza creare dispositivi dummy.
