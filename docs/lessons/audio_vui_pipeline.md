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
* **Soluzione Definitiva 100% NPU Locale (Hailo-10H):** Migrare completamente da Picovoice Porcupine a **Hailo-10H KWS (Keyword Spotting)**.
   * **Continuous Ambient Listening & Speaker Voice Print:** Ascolto passivo del parlato ambientale tramite modello di Speaker Embedding eseguito su Hailo-10H per associare il timbro vocale degli interlocutori (similarità coseno >= 0.72).
   * **Memory Decay Engine (Algoritmo di Oblio):** Memorizzazione delle conversazioni/avvenimenti nel buffer a breve termine (immuni gli ultimi 3 minuti) con pulizia automatica delle frasi futili o irrilevanti per non intasare la memoria.
   * **Context Handoff a Gemini Live API:** Quando viene rilevata la parola chiave "Marcus" su Hailo-10H, si mantiene inalterato il comportamento visivo dei LED e viene avviata la sessione Gemini Live ereditando nel contesto iniziale la memoria ambientale recente.


