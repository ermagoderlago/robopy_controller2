# Lezioni Apprese - Audio & VUI Pipeline

Questo documento raccoglie le lezioni apprese, i bug riscontrati e le soluzioni relative alla Voice User Interface (VUI) e alla gestione audio di Marcus.

---

## 🎙️ Hardware e Integrazione ReSpeaker Lite

### ReSpeaker Lite: Modalità USB (Firmware vs Bootloader)
* **Problema:** Dopo il flash del firmware, la porta seriale `/dev/ttyACM0` scompare da Linux.
* **Causa:** Il firmware disabilita la porta console JTAG seriale USB.
* **Soluzione:** Eseguire un reset fisico premendo il tasto BOOT all'inserimento USB e configurare il firmware con:
  ```yaml
  CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG: y
  ```

### ReSpeaker Lite: Integrazione Hardware ESPHome
* **Componente Esterno:** L'uso del componente `respeaker_lite` (repository `formatBCE`) è **obbligatorio** per stabilizzare i clock I2S del chip XMOS XU316. La configurazione manuale pura causa il blocco del DMA (dati piatti a 32743).
* **Pinout Corretto (XIAO S3 + Lite):**
  * **MCLK:** GPIO9
  * **BCLK:** GPIO8
  * **LRCLK:** GPIO7
  * **DIN:** GPIO43
  * **DOUT:** GPIO44
  * **RESET (XMOS):** GPIO2 (Deve essere mantenuto stabilmente ad `HIGH` per evitare che la XMOS sparisca).
* **Dipendenze:** I blocchi `i2c:` e `i2s_audio:` devono essere comunque definiti nel YAML per esporre il bus hardware sottostante.
* **Incompatibilità Versioni (Errore 'synchronous'):** Le versioni recenti del repo `respeaker_lite` usano parametri incompatibili con ESPHome 2026.2.4. Bloccare il componente al commit `ce94338`:
  ```yaml
  external_components:
    - source:
        type: git
        url: https://github.com/formatBCE/Respeaker-Lite-ESPHome-integration
        ref: ce94338a9284b940bb436a8aabd48b58b430da43
  ```
  *Nota: In questa versione la classe `RespeakerLite` non ha il metodo `.reset()`. Usare `.start_dfu_update()` per avviare il flashing XMOS.*

### Conflitti Bus (I2S vs USB)
* **Regola:** L'inizializzazione di `i2s_audio` sullo XIAO ESP32 crea contese di clock che impediscono alla XMOS di essere vista come periferica USB. L'architettura definitiva è **"USB-Pure"**: XMOS gestisce solo l'audio via USB, lo XIAO gestisce solo il controllo (I2C/Seriale) ed effetti DSP (AEC, AGC, NS) via protocollo seriale dedicato a 115200 baud.
* **I2C Scanning:** La XMOS richiede 4 secondi di stabilità al boot per l'enumerazione USB. Disattivare lo scan I2C di ESPHome (`scan: false`) e usare indirizzi statici per evitare interferenze.

---

## 🔊 Architettura Audio e Sample Rates

### "Darth Vader" & "Chipmunk" Effects (Sample Rate Mismatch)
* **Frequenza Standard di Pipeline:** 16000 Hz mono PCM.
* **Problema 1 (Darth Vader):** Gemini Live invia audio a 24kHz. Se riprodotto a 16kHz, rallenta del 33%.
* **Problema 2 (Chipmunk):** Il DAC hardware del ReSpeaker lavora nativamente a **48kHz**. Se aperto a 16kHz e l'hardware ignora la richiesta, l'audio viene riprodotto 3 volte più velocemente.
* **Soluzione (Unificazione a 16kHz & Resampling):**
  * Il firmware acquisisce a 16kHz.
  * Il bus ROS trasporta 16kHz mono PCM.
  * **Regola Permanente:** Non usare il parametro ROS `sample_rate` per determinare il resampling. Ottenere la frequenza nativa hardware `_out_hw_rate` da PyAudio via `defaultSampleRate`. Aprire lo stream di output alla frequenza nativa HW ed eseguire il resampling `16k ➔ 48k` via `audioop.ratecv` prima di scrivere sul device.

---

## 🎤 Calibrazione VAD, Noise Gate e Clipping

### Acquisizione Audio e Clipping Saturato
* **Problema:** Il microfono ReSpeaker Lite satura facilmente producendo clipping a onda quadra (Picco > 48000), rendendo inefficace il wake-word detector.
* **Soluzione:** Eliminare qualsiasi boost digitale sul segnale di input microfonico. Estrarre esclusivamente il canale Sinistro (evita *phase cancellation* stereo). Applicare un'attenuazione software di **3.0x** (`/ 3.0`) via NumPy prima del cast a `int16` per alimentare Porcupine e VAD. I livelli RMS ideali devono risiedere tra 5000 e 15000.

## Lezioni Apprese Storiche
1. **Thread Contention:** Eseguire VAD, AEC e Inference (Vosk) nello stesso thread del loop USB mandava in overflow PyAudio (ALSA Error -32). Adesso `local_asr_vosk.py` gira su un thread isolato con una coda thread-safe e `webrtcvad` elabora chunk raw su buffer NumPy pre-allocati.
2. **Risparmio Costi (Vosk vs Gemini):** Mantenere Gemini Live sempre in ascolto superava il limite mensile (5€). Abbiamo migrato il Wake Word detector a Vosk (offline ASR, modello IT piccolo) per fungere da sentinella, inviando a Gemini solo i comandi successivi al trigger "Marcus", "Marco" o "Robot".
3. **Partial Results per Wake Word Rapido:** Vosk può richiedere tempo per emettere un risultato finale a causa del timeout sul silenzio. Abilitare il parsing di `recognizer.PartialResult()` permette il rilevamento istantaneo della parola chiave.

### Falsi Positivi VAD (Rumore Ventola)
* **Soluzione:** Abbassare il guadagno `stt_gain` da `0.6` a `0.15` in ambiente stazionario per isolare meglio la voce umana dal rumore della ventola del Pi 5.

### Beep di Feedback
* **Soluzione:** Volume del beep ridotto all'ampiezza 3276 (10% del totale) per prevenire fastidi e sbalzi dinamici.

### DAC Auto-Mute (Stuttering Notturno)
* **Problema:** A bassi volumi (5%), i chip economici attivano un noise-gate DSP hardware che spegne fisicamente l'amplificatore, causando balbuzie tra le parole.
* **Soluzione (Dithering):** Aggiunta di rumore bianco inaudibile. Se il volume è <15%, sommare `np.random.randint(-12, 13)` al segnale digitale prima del cast a `int16` per tenere sveglio il DAC.

### Sensibilità Far-Field (1-3m) e Soppressione Rumore Ventola Pi 5 (v17.0)
* **Problema:** Il rumore a bassa frequenza della ventola del Raspberry Pi 5 (<140 Hz) veniva amplificato dal guadagno software (`stt_gain` = 25.0x), portando l'EMA del rumore ambientale a valori elevati (~400-600 RMS raw) e facendo schizzare la soglia dinamica `noise_gate_threshold` oltre i 20.000 RMS. Di conseguenza, per far ascoltare la voce da lontano bisognava urlare vicini al robot, e Gemini segnalava "ci sono rumori".
* **Soluzione:**
  1. **Filtro Passa-Alto Butterworth @ 140 Hz (HPF):** Applicato prima di VAD, Porcupine e dello streaming a Gemini. Elimina completamente il ronzio della ventola senza alterare le armoniche vocali umane.
  2. **Selezione canale a maggior energia (Left/Right):** Valuta i canali Left e Right dell'array ReSpeaker Lite per usare il canale con segnale vocale pulito più forte.
  3. **Clamp Soglia Dinamica Far-Field:** L'EMA viene calcolato strictly sul segnale filtrato HPF in assenza di parlato ed il gate dinamico viene confinato nell'intervallo `[800.0, 4500.0]`. La voce pronunciata normalmente da 1 a 3 metri (RMS ~1500-3000) attiva istantaneamente il VAD.
  4. **Reattività VAD (MIN_SPEECH_FRAMES = 4):** Ridotto a 4 frame (80ms) per evitare il troncamento delle consonanti iniziali.
  5. **Invio segnale HPF a WebRTC VAD (End-Of-Speech Fix):** Passaggio obbligatorio del segnale filtrato HPF (`selected_hp_all_int16`) a `webrtcvad.is_speech()` al posto del canale grezzo `l_ch`, garantendo il rilevamento immediato del silenzio post-frase e lo sblocco del turno LED.

---

## 🛑 Gestione delle Interruzioni (Barge-In)

### Architettura Barge-In Unificata
* **Contesto:** Il ReSpeaker Lite ha l'AEC hardware attivo (XMOS XU316). Non serve silenziare il microfono durante il TTS.
* **Funzionamento:**
  1. Il nodo VUI esegue Porcupine e VAD costantemente.
  2. Se il VAD rileva voce sostenuta per 15 frame (~300ms) dopo 1.5s dall'inizio del TTS, invia il segnale `/ai/barge_in`.
  3. L'orchestratore riceve la preemption e cancella il turno corrente sul LLM (`cancel_current_turn()`), svuotando le code audio.
* **Unificazione Stati:** In `respeaker_vui_node.py` lo stato `speaking` deve combinare sia il TTS classico (`_is_tts_speaking`) che lo stream Live API (`_is_playing_out`).
* **Regola Permanente:** Nessun componente deve pubblicare `mic_mute=True` durante il TTS. La soppressione dell'eco del parlato del robot si ottiene abbattendo temporaneamente lo `stt_gain` a **0.1x** nel nodo VUI, lasciando il microfono aperto per interruzioni tramite wake-word ("Marcus" o "Zitto Marcus").

---

## 🔑 Picovoice Free Tier EOL & Transizione Wake Word

### Dismissione Free Tier Picovoice Porcupine (Giugno 2026)
* **Problema:** Picovoice ha ufficialmente disattivato il piano gratuito ("Free Tier AccessKeys"). Tutte le AccessKey gratuite esistenti sono state invalidate lato server, bloccando l'inizializzazione del motore Porcupine nei nodi VUI.
* **Impatto:** All'avvio di `respeaker_vui_node` o `wake_word_node`, l'SDK `pvporcupine` restituisce errore di attivazione licenza, disabilitando la risposta alla parola chiave "Hey Marcus".
### Rimozione Porcupine: Modalità Ascolto Continuo VAD & Registrazione Conversazioni (v6.5)
* **Problema:** Con la disattivazione di Porcupine (`self.porcupine = None`), il worker `_audio_processing_worker()` in `respeaker_vui_node.py` conteneva un check residuo `if self.porcupine is None: continue` che scartava silenziosamente tutti i chunk audio dal microfono. Marcus non ascoltava la voce, i LED non transizionavano e le conversazioni non venivano elaborate.
* **Soluzione:**
  1. **Rimozione del blocco early-exit:** Rimosso il check `if self.porcupine is None: continue` per consentire all'elaborazione VAD e al filtro HPF 140Hz di processare costantemente lo stream audio.
  2. **Attivazione Modalità Continuous Listening VAD:** Inizializzazione di `self._ev_listening.set()` a `True` di default e preservazione dello stato di ascolto attivo durante il timeout per permettere a VAD di inviare direttamente i frame a Gemini Live API quando l'utente parla.
  3. **Stati LED e Ciclo di Vita:** Rispettata la sequenza LED: `LISTENING` durante l'ascolto dell'utente, `THINKING` all'invio del frame End-of-Speech (`_publish_end_of_speech`), `SPEAKING:<intensity>` / `SUCCESS` durante la riproduzione audio TTS/Live di Gemini, e `IDLE` a fine risposta.
  4. **Registrazione Persistente delle Conversazioni:** Aggiunta la funzione `_save_conversation_turn()` in `llm_service.py` per salvare in modo atomico ogni turno utente-Marcus in formato JSONL timestampato su `/mnt/ssd/robopy_controller_host/logs/conversations.jsonl` e `~/robopy/logs/conversations.jsonl`.

---

## ⚠️ Crash da Costanti Globali Mancanti in `respeaker_vui_node.py` (v12.1 — 2026-08-01)

### Problema: NameError su costanti audio durante il refactoring

* **Contesto:** Durante il refactoring del blocco `import webrtcvad` (per renderlo opzionale con `try/except`), il placeholder `# ... (omessi import)` aveva sostituito in modo silenzioso l'intero blocco di costanti globali audio nel file sorgente su Windows.
* **Sintomi:** Il nodo `respeaker_vui_node` crashava con `NameError: name 'X' is not defined` in sequenza su: `np` (numpy), `CHUNK_SIZE`, `PRE_ROLL_BYTES`, `MIN_SPEECH_FRAMES`, `_NATIVE_AUDIO_RATE`, `_NATIVE_AUDIO_CHUNK_THRESHOLD`.
* **Causa Secondaria:** Il file `.py` su Windows aveva line endings `CRLF` che causavano inconsistenze durante la copia su Linux/Raspberry Pi. Il robot usava ancora la cache `.pyc` stantia anche dopo la copia del file aggiornato.

### Soluzione Permanente

1. **Blocco Costanti Esplicito:** Tutte le costanti globali devono essere definite in un blocco esplicito commentato PRIMA della prima classe nel file, mai come commenti `# omessi`. Le costanti canoniche sono:
   ```python
   SAMPLE_RATE    = 16000      # Hz microfono ReSpeaker Lite
   FRAME_SIZE     = 320        # campioni/frame VAD (20ms @ 16kHz)
   CHUNK_SIZE     = 960        # 3 frame VAD (3 × 320)
   MAX_RESIDUAL   = CHUNK_SIZE * 4
   MAX_RING_FRAMES = int(2.5 * SAMPLE_RATE / FRAME_SIZE)  # ~125 frame preroll
   PRE_ROLL_FRAMES = MAX_RING_FRAMES
   PRE_ROLL_BYTES  = PRE_ROLL_FRAMES * FRAME_SIZE * 2
   MIN_SPEECH_FRAMES = 4       # 80ms voce sostenuta per trigger VAD
   _NATIVE_AUDIO_RATE = 24000  # Hz output Gemini Live API
   _STD_AUDIO_RATE    = 24000  # Hz output TTS pre-generato
   _NATIVE_AUDIO_CHUNK_THRESHOLD = 4096  # byte soglia live vs TTS
   ```
2. **Deploy con LF Normalization:** Usare sempre `sed 's/\r//'` quando si copiano file `.py` da Windows a Linux:
   ```bash
   sed 's/\r//' file.py | ssh robopy@marcus 'cat > /remote/path/file.py'
   ```
3. **Pulizia Cache `.pyc`:** Dopo ogni hot-swap, rimuovere la cache bytecode:
   ```bash
   ssh robopy@marcus 'rm -f /path/to/__pycache__/respeaker_vui_node.cpython-311.pyc'
   ```
4. **Verifica Pre-Deploy:** Prima di deployare, verificare sempre che `import numpy as np` sia presente e che tutte le costanti uppercase usate nella classe siano definite a livello modulo.

---

## 🎤 Guadagno Dinamico Microfonico, Profilazione del Silenzio & Pre-Roll 500ms (v18.0 — 2026-08-02)

### Problema: Incomprensione ASR per Clipping e Latenza Pre-Roll
* **Sintomo:** Marcus rileva la wake word "Marcus", ma spesso non riesce a comprendere la frase pronunciata dall'utente.
* **Cause Radice Identificate:**
  1. **Guadagno Rigido 30.0x:** L'amplificazione fissa a 30x provocava il clipping digitale dei campioni int16, producendo forme d'onda quadre e distorsione armonica che mandavano in stallo il riconoscitore ASR (Gemini Live / Vosk).
  2. **Pre-Roll da 2.5s:** `MAX_RING_FRAMES` a 125 frame (2.5s) inviava 2.5 secondi di rumore pregresso a Gemini prima del parlato reale, introducendo latenza e corrompendo i primi token ASR.
  3. **Ronzio di Fondo Incontrollato:** Assenza di registrazione/profilazione del rumore di fondo del silenzio.

### Soluzioni Implementate
1. **Profilazione del Silenzio (Noise Floor Profile Subtraction):** Registrazione automatica dei primi 2 secondi di silenzio dopo l'avvio del nodo per determinare la baseline di rumore ambientale ed applicare la soppressione soft sulle frequenze/ampiezze del rumore di fondo.
2. **AGC Dinamico (1.0x - 4.0x):** Guadagno di base ridotto da 30.0x a 2.5x con modulazione dinamica automatica basata sul target speech RMS (~8000), eliminando ogni fenomeno di saturazione.
3. **Pre-Roll a 500ms:** `PRE_ROLL_FRAMES` impostato a 25 frame (500ms), annullando 2.0s di ritardo ingiustificato nell'inizio della risposta.
4. **Script di Calibrazione:** Creato `scripts/test_mic_calibration.py` per testare silenzio/parlato e verificare i valori ottimali sul robot reale.

---

## 🖥️ CPU Budget e Offload ESP32 — Analisi Architettuale (2026-08-02)

### Contesto: VUI già ottimizzata in v5.0
La v5.0 del `respeaker_vui_node.py` ha eliminato l'intero stack DSP float32 (SciPy Butterworth, sosfilt, scalatura), portando la CPU del VUI da ~40% a **~2-3%**. Il nodo attuale usa una pipeline **int16 raw** senza DSP float32 nel hot-path.

**Implicazione:** Qualsiasi sforzo di offload DSP audio verso l'ESP32 ha impatto marginale (<1% CPU) dato che il carico è già minimo.

### Architettura USB vs I2S ESP32 (vincolo critico)
Il ReSpeaker Lite espone **due pipeline audio parallele e indipendenti**:
1. **XMOS XU316 → USB Audio** → Pi5 (come device `/dev/snd/...`) — usata dal VUI
2. **XMOS XU316 → I2S → ESP32** — usata solo da ESPHome per micro_wake_word

**Regola:** L'ESP32 **non può pre-processare** l'audio che arriva al Pi5 via USB, perché si tratta di bus hardware separati. Il XMOS gestisce entrambi ma le uscite sono fisicamente distinte.

### Cosa può fare l'ESP32 per il CPU budget
| Feature | Fattibilità | Impatto |
|---|---|---|
| DSP HPF/AGC offload | ❌ Non applicabile (USB bypass) | 0% |
| AUDIO_LEVEL RMS reale | ⚠️ Possibile (firmware update) | <0.5% CPU |
| **micro_wake_word TFLite** | ✅ Supportato da ESPHome | **-3-5% CPU standby** |

### micro_wake_word su ESP32 (azione futura raccomandata)
ESPHome `micro_wake_word` gira modelli TFLite quantizzati sull'ESP32S3. Il Pi5 riceve solo l'evento `WAKE_WORD_DETECTED` via UART e non esegue più Vosk ASR in standby continuo.
- **Requisito:** Modello `.tflite` addestrato su "Marcus" (tool: microWakeWord trainer su GitHub)
- **Firmware:** Aggiunta componente `micro_wake_word` in YAML ESPHome + comando UART `MARCUS_DETECTED\n`
- **Lato ROS:** Modifica `respeaker_interface_node.py` per intercettare `MARCUS_DETECTED` e chiamare `_on_wakeword_detected()` via topic ROS
- **Risparmio:** Vosk ASR non gira più in continuo in standby → -3-5% CPU, -50mW consume Pi5

### Hotspot CPU Principale (non-audio): FM-CPU-001
Il vero hotspot CPU era `annotate_and_publish_image` a 30Hz in `hailo_bridge_node.py`.
Applicato throttle a 5Hz → -15% CPU stimato. Vedi `fmea/dfmea.yaml#FM-CPU-001`.

---

## 🐛 Bug Critici Risolti — v19.6 (2026-08-04)

### BUG-1: `force_flush()` Vosk non chiamato a fine frase
* **Sintomo:** Frasi brevi e wake word pronunciate rapidamente non riconosciute.
* **Causa:** `_publish_end_of_speech()` inviava il frame vuoto EOS a Gemini senza prima forzare Vosk a emettere il testo ancora nel buffer interno. Vosk è un motore statistico che accumula audio e aspetta silenzio o reset.
* **Fix (v19.6):** `force_flush()` viene chiamato in `_publish_end_of_speech()` prima del publish del frame vuoto. Questo garantisce che l'ultima parola (anche "Marcus") sia sempre consegnata al callback.
* **File:** `respeaker_vui_node.py` — metodo `_publish_end_of_speech()`

### BUG-2: Noise gate minimo 600 RMS — voci deboli/distanti non rilevate
* **Sintomo:** Marcus non inizia l'ascolto se l'utente parla da > 1m o a voce moderata.
* **Causa:** Il clamp inferiore del noise gate adattivo era 600 RMS, troppo alto per segnali deboli amplificati 2.5x.
* **Fix (v19.6):** Clamp abbassato da 600 → 400 RMS. Mantiene protezione dal rumore ambientale.
* **File:** `respeaker_vui_node.py` — riga costante `noise_gate_threshold` adattiva.

### BUG-3: Finestra blocco Vosk 1.5s post-TTS — prime parole perse
* **Sintomo:** Se l'utente inizia a parlare subito dopo la risposta di Marcus, le prime 1-1.5 secondi di audio non vengono processate da Vosk → wake word o risposta persa.
* **Causa:** `is_speaker_active` usava una finestra di 1.5s dopo `_last_ai_speaking_time`.
* **Fix (v19.6):** Ridotta a 0.6s: sufficiente per dissipare l'eco hardware del beep, ma non taglia le prime parole dell'utente.
* **File:** `respeaker_vui_node.py` — `_on_vosk_text()` (riga 910) e `_audio_processing_worker()` (riga 1173).

### BUG-4: `max_silence=28 frame` (560ms) — frasi tagliate in ambienti silenziosi
* **Sintomo:** In ambienti silenziosi (es. notturno), le frasi venivano tagliate a metà e Marcus rispondeva a frasi parziali.
* **Causa:** L'adattivo portava `_cfg_max_silence` a 28 frame (560ms) per `ambient_noise_ema < 200`. La pausa naturale tra parole umane è 600-800ms → la fine-frase scattava troppo presto.
* **Fix (v19.6):** Tutti i case dell'adattivo uniformati a 40-50 frame (800-1000ms).
* **Tabella aggiornata:**

| Ambient EMA | max_silence (v19.5) | max_silence (v19.6) |
|---|---|---|
| < 100 | 40 frame (800ms) | 40 frame (800ms) ✅ |
| < 200 | **28 frame (560ms) ❌** | **40 frame (800ms) ✅** |
| < 350 | 35 frame (700ms) | 40 frame (800ms) ✅ |
| ≥ 350 | 45 frame (900ms) | 50 frame (1000ms) ✅ |

### BUG-5: Reset VAD durante tutto il cooldown (600ms) — inizio frasi cancellato
* **Sintomo:** Se l'utente inizia a parlare durante i 600ms di cooldown post-TTS, il ring buffer e i contatori VAD vengono azzerati a ogni chunk → l'inizio della frase non viene mai registrato.
* **Causa:** La condizione `if (ai_speaking_was and not ai_speaking_now) or ai_cooldown_active:` eseguiva il reset per tutta la durata del cooldown (600ms = 10 chunk × 60ms).
* **Fix (v19.6):** Reset VAD solo alla transizione `True→False` (singolo evento), non per tutta la durata del cooldown. Il cooldown rimane attivo per il gain e per il blocco Vosk.
* **File:** `respeaker_vui_node.py` — `_audio_processing_worker()` riga ~1110.

### MED-1: Drop silenzioso frame Vosk sotto carico CPU
* **Sintomo:** Sotto carico CPU intenso, frame audio venivano scartati silenziosamente senza alcun log.
* **Fix (v19.6):** Aggiunto contatore `_drop_count` e log periodico ogni 50 drop.
* **File:** `local_asr_vosk.py` — metodo `process_audio()`.

---

## 🏗️ Refactoring Architetturale Loop VAD e Vosk — v20.0 (2026-08-04)

### ARCH-1: Race Condition in Vosk ASR (`force_flush`)
* **Problema (Bug architetturale):** L'approccio scolastico di chiamare `recognizer.Reset()` o ricreare `self.recognizer = vosk.KaldiRecognizer(...)` dal thread principale ROS 2 causava race condition col worker thread Vosk (segmentation fault) ed allocazioni bloccanti. Il metodo `Reset()` inoltre non è esposto ufficialmente nella Python API di Vosk e provoca instabilità.
* **Soluzione (Sentinel Value Pattern):** Introdotto un comando speciale `b"FLUSH_CMD"` iniettato nella coda audio dal main thread. Il worker thread estrae il sentinel in ordine cronologico, esegue `FinalResult()` e **ricrea il riconoscitore localmente nel proprio thread**. Nessun lock, zero race condition, elaborazione pipeline asincrona sicura. Modello Vosk aggiornato a `vosk-model-it-0.22` (full version) per accuratezza maggiore.

### ARCH-2: Isteresi 'Attentive State' per il VAD
* **Problema:** I parametri statici del VAD causavano false partenze (click spuri in idle) o tagli anticipati di frasi con pause interne (utente che riflette e si ferma).
* **Soluzione:** Introduzione dello stato dinamico `is_attentive = _ev_listening.is_set()`.
  1. **Noise Gate:** Quando in idle, soglia base rigida (clamp 400). Quando `is_attentive=True`, soglia addolcita (clamp 350, multiplier 1.15x) per catturare i sussurri di risposta.
  2. **Max Silence Timeout:** In idle, massimo 800-1000ms. In conversazione, timeout esteso a 900-1100ms (`_cfg_max_silence` 45-55 frame) per tollerare pause naturali a metà frase.
  3. **Reattività (MIN_SPEECH_FRAMES):** In idle, 4 frame (80ms) per immunità ai disturbi. In conversazione, 2 frame (40ms) per non tagliare le consonanti dei "Sì/No" brevi.

### ARCH-3: Finestra Barge-in Ottimizzata
* **Problema:** Marcus si bloccava troppo a lungo dopo aver parlato, impedendo un'interazione naturale a ritmo sostenuto (ping-pong verbale).
* **Soluzione:** Ridotto il timeout hardware `time_since_speaker` da `0.6s` a `0.4s` sia per l'inibizione dell'eco in Vosk sia per il cooldown in `_audio_processing_worker`. L'AEC XMOS è sufficiente a prevenire loop acustici. Abilitato inoltro timestamps con `SetWords(True)`.

---

## 🔊 Dynamic AGC Software su Parlato Debole / Lontano — v20.1 (2026-08-04)

### Problema (FM-VUI-005)
* **Sintomo:** Trascrizione ASR incomprensibile o allucinata quando l'utente parla da distanze maggiori (> 3 metri) o con voce flebile.
* **Causa:** Il guadagno software statico a 2.5x non era sufficiente a portare l'ampiezza del segnale debole sopra la soglia ottimale per Gemini Live e Vosk.

### Soluzione Implementata (`respeaker_vui_node.py`)
* **Dynamic AGC Software:** Quando lo stato è `is_attentive` (listening attivo o parlato in corso), se l'RMS dell'audio in ingresso supera la soglia del gate ma rimane sotto i 1500 RMS (segnale debole), viene calcolato un moltiplicatore proporzionale `agc_multiplier = min(2.0, 1500.0 / max(rms_l, 100.0))`.
* **Guadagno Dinamico:** Il guadagno effettivo scala automaticamente da **2.5x fino a 5.0x max**.
* **Protezione Limiter:** Il Peak Limiter vettoriale esistente garantisce l'assenza totale di clipping digitale (saturazione campioni int16 a 30000).
* **Esito FMEA:** Failure mode `FM-VUI-005` chiuso con RPN ridotto da **144 a 24**.

---

## ⚡ Streaming KWS 50 FPS, Buffer Pre-trigger 1.2s & SWIG Memory Test — v20.2 (2026-08-05)

### Modello Streaming KWS su Hailo-10H NPU (<180ms Latenza)
* **Problema:** Una finestra audio fissa da 1.0s con hop 100ms introduceva una latenza avvertibile ("legnosità" nella reazione alla wake word).
* **Soluzione:** Modello **Streaming KWS** in [`hailo_kws_service.py`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/robopy_controller/robot_ai/services/hailo_kws_service.py) con finestra da **250ms (4000 campioni)** e stride da **20ms (320 campioni)**. L'inferenza gira a 50 FPS sulla NPU Hailo-10H con latenza $< 180\text{ ms}$ (misurati sul robot: **40.3 ms** NPU, **50.4 ms** E2E).

### Buffer Circolare Pre-trigger (1.2s)
* **Problema:** All'attivazione della wake word "Marcus", la prima parola della frase veniva tagliata ("Marcus, che tempo fa?" ➔ "tempo fa?").
* **Soluzione:** In [`respeaker_vui_node.py`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/robopy_controller/nodes/respeaker_vui_node.py), all'arrivo del trigger `/hailo/wakeword_trigger`, lo stream audio scarica ed invia a Vosk e Gemini Live l'intero pre-roll buffer da **1.2 secondi** (`MAX_RING_FRAMES = 60`), preservando l'intera frase dell'utente.

### Endurance Test Memoria Vosk (200 Cicli su RPi 5 Reale)
* **Verifica Empirica:** Eseguito `scripts/test_vosk_memory_endurance.py` direttamente sul Raspberry Pi 5.
* **Risultati:** Dopo l'assestamento iniziale del modello (416 MB ➔ 428 MB), la memoria si è **stabilizzata a plateau a 428.81 MB** per oltre 100 flushes consecutivi (variazioni $+0.00\text{ MB}$), confermando l'assenza totale di SWIG native memory leaks (`FM-VUI-014` chiuso).

