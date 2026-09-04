# 🎙️ SPEC-04: Voice User Interface (VUI) & Audio Pipeline

## 1. Identificazione e Scopo
- **ID Specifica:** `SPEC-04`
- **Ambito:** Acquisizione audio multicanale, cancellazione del rumore (DSP & VAD), Keyword Spotting su NPU (KWS "Marcus"), biometria vocale (ECAPA-TDNN), streaming bidirezionale con Gemini Live API e riproduzione TTS senza artefatti.
- **Nodi & Moduli ROS 2:**
  - `robopy_controller.nodes.respeaker_vui_node` (`respeaker_vui_node.py`)
  - `robopy_controller.nodes.respeaker_interface_node`
  - `robopy_controller.robot_ai.core.audio_buffer_manager` (`audio_buffer_manager.py`)
  - `robopy_controller.nodes.voiceprint_manager` (`voiceprint_manager.py`)
  - `robopy_controller.nodes.memory_decay_engine` (`memory_decay_engine.py`)
- **Hardware Diretto:** Array microfonico USB ReSpeaker Lite (XMOS XU316 + XIAO ESP32-S3), Altoparlante con DAC I2S/USB (48kHz nativo).
- **DFMEA Correlati:** `FM-VUI-001` (Effetti Chipmunk/Darth Vader per mismatch sample rate), `FM-VUI-002` (Contesa device ALSA esclusivo), `FM-VUI-003` (Clipping e saturazione microfonica), `FM-VUI-004` (KWS su NPU), `FM-VUI-021` (Sessione estesa 3 minuti e directed follow-up), `FM-VUI-023` (Modularizzazione audio).

---

## 2. Architettura della Pipeline Audio

```mermaid
graph LR
    MIC["ReSpeaker Mic Array (USB)"] -->|Canale Sinistro Raw| DSP["respeaker_vui_node (HPF 140Hz & Attenuazione 3.0x)"]
    DSP -->|16kHz Mono PCM| KWS["Hailo-10H KWS ('Marcus')"]
    DSP -->|16kHz Mono PCM| ABM["AudioBufferManager (Ring Buffer)"]
    DSP -->|Audio Features| ECAPA["ECAPA-TDNN (Voiceprint)"]
    
    KWS -->|Trigger Rilevato| LIVE["Gemini Live WebSocket Bridge"]
    ABM -->|Pre-Roll 500ms + Audio Stream| LIVE
    LIVE -->|TTS Audio Chunks (24kHz/16kHz)| RESAMP["Resampler (audioop.ratecv)"]
    RESAMP -->|48kHz Nativo HW| DAC["DAC Hardware PyAudio"]
```

---

## 3. 🔴 ZONA ROSSA (Inviolabili - NO AUTONOMOUS TOUCH)

Le seguenti prescrizioni sono categoriche. La loro alterazione distrugge la comprensione vocale o causa crash del sottosistema sonoro.

| Vincolo di Pipeline / Audio | Regola Inviolabile | Rischio Ingegneristico | DFMEA |
| :--- | :--- | :--- | :--- |
| **Frequenza di Pipeline ROS** | **16000 Hz Mono PCM a 16-bit** little-endian | Incompatibilità totale con WebSocket Gemini Live API | FM-VUI-001 |
| **Resampling Hardware DAC** | Rilevamento `_out_hw_rate` (48k) & `audioop.ratecv` | Effetto "Chipmunk" (audio 3x accelerato) o "Darth Vader" | FM-VUI-001 |
| **Estrazione Canale Audio** | Solo **Canale Sinistro** (`data[:, 0]`) | Cancellazione di fase acustica e abbattimento SNR | FM-VUI-003 |
| **Attenuazione Software Input** | Attenuazione **3.0x** (`/ 3.0`) pre-cast `int16` | Saturazione ad onda quadra (RMS > 48000) e sordità KWS | FM-VUI-003 |
| **Barge-in Safety Limiter** | `stt_gain` attenuato a **0.1x** durante riproduzione TTS | Auto-ascolto del robot in loop acustico (*echo feedback*) | FM-VUI-002 |
| **Isolamento Device ALSA** | Modalità esclusiva PyAudio protetta da lock | Conflitto di bus audio con blocchi I/O del processo | FM-VUI-002 |
| **Abolizione Porcupine** | Vietato reintrodurre librerie terze a licenza | Mancato avvio in assenza di token cloud proprietario | FM-VUI-004 |

---

## 4. 🟢 ZONA VERDE (Auto-Evolution - MIGLIORAMENTO AUTONOMO CONSENTITO)

L'agente Antigravity può ottimizzare e ricalibrare autonomamente le seguenti componenti:

| Area di Ottimizzazione | Metodo & Logica Ammessa | Range & Vincoli di Accettazione |
| :--- | :--- | :--- |
| **Filtro Passa-Alto (HPF)** | Taglio Butterworth del ronzio della ventola del Pi 5 | Frequenza di taglio $f_c \in [120\text{ Hz}, 160\text{ Hz}]$; ordine 2 |
| **Noise Floor Adaptor** | Sottrazione adattiva del rumore di fondo domestico | Aggiornamento soglia silenzio durante pause $> 1.0\text{ s}$ |
| **Pre-roll Buffer VUI** | Accumulo circolare audio prima dell'innesco wakeword | $T_{preroll} \in [300\text{ ms}, 600\text{ ms}]$; default: $500\text{ ms}$ |
| **Voiceprint Match Threshold**| Soglia similarità coseno embedding ECAPA-TDNN | $thresh \in [0.68, 0.78]$; default: $0.72$ |
| **Directed Follow-up Gating** | Riconoscimento frasi rivolte a terzi vs rivolte a Marcus | Soppressione silenciosa `<IGNORE_TURN>` senza chiusura WebSocket |
| **Memory Decay Window** | Immunità temporale della memoria acustica a breve termine| Finestra immunità: $[120\text{ s}, 240\text{ s}]$; default: $180\text{ s}$ |

---

## 5. 🟡 ZONA GIALLA (Human-in-the-Loop - APPROVAZIONE UMANA OBBLIGATORIA)

Le seguenti modifiche richiedono proposta formale e validazione dell'operatore umano:

1. **Modifica Wakeword:** Cambio della parola chiave di risveglio (passaggio da "Marcus" ad altro trigger su modello NPU).
2. **Firmware XMOS / ESP32-S3:** Riconfigurazione dei clock I2S (MCLK, BCLK, LRCLK) o aggiornamento YAML ESPHome.
3. **Architettura WebSocket Gemini:** Modifica dei parametri di connessione o del formato di frame audio inviato a Google Cloud.
4. **Privacy Audio Storage:** Aggiunta di logiche di registrazione permanente o trasmissione all'esterno di tracce audio grezze.

---

## 6. Procedura di Verifica & Test di Non-Regressione

Prima di confermare modifiche alla VUI o alla pipeline audio, l'agente DEVE eseguire con successo:

```bash
# 1. Test unitario del gestore buffer circolare e lock
pytest tests/test_audio_buffer_manager.py -v

# 2. Test matematico del resampling audioop da 16k a 48k e 24k
pytest tests/test_vui_audio_resampling.py -v

# 3. Test del Voiceprint Manager e normalizzazione ECAPA
pytest tests/test_voiceprint_manager.py -v

# 4. Test della macchina a stati di gating della sessione a 3 minuti
pytest tests/test_extended_session_gating.py -v
```
I test devono confermare:
- Zero clipping sui campioni microfonici normalizzati.
- Resampling matematicamente privo di sfasamenti temporali (lunghezza buffer esatta).
- Chiusura deterministica della sessione WebSocket dopo 180s di inattività.
