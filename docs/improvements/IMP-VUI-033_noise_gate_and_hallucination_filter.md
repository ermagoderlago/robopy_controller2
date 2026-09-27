# 🛠️ Progetto di Miglioramento: IMP-VUI-033

## Calibrazione Adaptive Noise Gate, Filtro Non-Speech e Anti-Chatter Turn Gating

- **ID Failure Correlato:** `FM-VUI-033`
- **Componenti:** `robopy_controller.nodes.respeaker_vui_node`, `robopy_controller.robot_ai.services.live_connection_manager`
- **Stato:** `COMPLETED`
- **Data Attivazione:** 2026-09-27
- **Ambito:** VUI Audio, Gemini Live API, Conversational Gating

---

### 1. Descrizione del Problema
Marcus entrava in un loop auto-alimentato in cui parlava da solo ("a vanvera"), generando risposte inventate e persino chiamate a strumenti (es. accensione luci su Home Assistant) in assenza di qualsiasi voce umana.
L'anomalia si manifestava in ambienti domestici normali con rumore di fondo della ventola del Pi 5 (~180-220 RMS).

### 2. Analisi delle Cause Radice
1. **Errore nella formula della soglia adattiva (`respeaker_vui_node.py`):**
   La formula utilizzava `min(current_threshold, adaptive_target)` dove `current_threshold` era impostato a 120.0. Anche quando il rumore ambientale (`_ambient_noise_ema`) saliva a 190 RMS (con target a $190 \times 2.2 = 418$), il gate veniva cappato a 120.0. Di conseguenza, il rumore superava costantemente la soglia, aprendo il VAD per errore.
2. **Min Speech Frames troppo basso in ascolto:**
   `MIN_SPEECH_FRAMES` era abbassato a soli 2 frame (40ms), rendendo il VAD ipersensibile a qualsiasi click, respiro o turbolenza d'aria della ventola.
3. **Mancato filtraggio di `<noise>` in `live_connection_manager.py`:**
   Gemini Live trascriveva l'audio del microfono come `<noise>`, ma il manager continuava a processare la risposta audio e le function calls del modello.
4. **Loop perpetuo del timer di inattività (`last_turn_ago < 180s`):**
   Ogni risposta allucinata generata dal robot chiamava `self.last_successful_turn_time = time.time()`, azzerando `last_turn_ago` e mantenendo aperta la sessione attiva per altri 180 secondi, intrappolando il robot in una conversazione infinita con il proprio rumore di fondo.

### 3. Soluzione Ingegneristica (Mitigazioni Applicate)
1. **Adaptive Noise Gate Uncapped:**
   In `respeaker_vui_node.py`, la soglia adattiva è ora vincolata a `max(base_threshold, ambient_floor * 2.2)`. Se il rumore ambientale è a 180 RMS, il gate sale a ~400 RMS.
2. **Aumento MIN_SPEECH_FRAMES:**
   Portato a 5 frame (100ms) durante la conversazione attiva e a 7 frame (140ms) in idle.
3. **Filtro Rigido Non-Speech & Tag Rumore:**
   In `live_connection_manager.py`, se la trascrizione dell'utente contiene solo tag di rumore (`<noise>`, `<cough>`, `<laughter>`, `<sigh>`) o nessun carattere alfanumerico reale, il turno viene soppresso: audio cancellato, tool calls bloccate e canale resettato.
4. **Aggiornamento Selettivo di `last_successful_turn_time`:**
   Il timestamp dell'ultimo turno valido viene aggiornato esclusivamente se la trascrizione utente contiene parole umane reali ($> 1$ carattere non-tag), impedendo il reset fraudolento del timeout di 180 secondi.

### 4. Metriche di Verifica e RPN
- RPN Iniziale: 336 (S:7, O:8, D:6)
- RPN Residuo: 28 (S:7, O:2, D:2)
- Collaudo: Test prolungato in silenzio con ventola attiva al 100%: zero turni aperti e zero risposte a vanvera.
