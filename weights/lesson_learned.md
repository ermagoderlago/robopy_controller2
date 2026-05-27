# Lesson Learned - Robot AI Project

Questo documento raccoglie gli errori riscontrati, le cause identificate e le soluzioni applicate per evitare regressioni.

## Inizializzazione Servizi e Orchestrazione

### Blocco Startup su Home Assistant
- **Problema**: L'orchestratore non raggiungeva lo stato "READY" se Home Assistant era irraggiungibile o lento.
- **Causa**: Chiamata `await self.ha_client.connect()` sequenziale nel metodo `_init_resources`.
- **Risoluzione**: Spostata l'inizializzazione di HA in un `asyncio.create_task` separato (non-blocking). Ora il sistema parte anche se HA è offline.

### Errori di Attributo in Startup
- **Problema**: Crash immediato all'avvio su `MemoryStore.initialize()` e `StateMachine.current_state`.
- **Causa**: Metodi inesistenti o rinominati durante il refactoring. `MemoryStore` viene inizializzato nel costruttore; `StateMachine` usa `.state`.
- **Risoluzione**: Rimossa la chiamata ridondante a `initialize()` e corretto l'accesso alla proprietà dello stato.

## Integrazione ROS 2

### Topic di Risposta Mancanti
- **Problema**: Il topic `/ai/conversation/response` non veniva pubblicato.
- **Causa**: Il nuovo `AIOrchestrator` non definiva il publisher e `ConversationManager` non aveva un modo per inviare testo su ROS.
- **Risoluzione**: Aggiunti publisher in `AIOrchestrator` e implementato un sistema di callback in `ConversationManager`.

## LLM e Live API

### Riconnessioni Frequenti (Turn 1 Closed)
- **Problema**: La sessione Live si chiudeva dopo ogni risposta.
- **Causa**: Utilizzo di modelli placeholder o non pienamente supportati per il bidi-streaming (`gemini-2.0-flash-exp` ha dato errore 1008). 
- **Risoluzione**: Switch a `gemini-1.5-flash` per la Live API (fase intermedia), ma stabilizzato infine su **Gemini 2.5 Flash Native Audio Dialog** per i task audio.

### Scelta Modelli e Ottimizzazione Costi (REGOLA PERMANENTE)
- **NON CAMBIARE I MODELLI SENZA TEST PRELIMINARI**: Le iterazioni precedenti hanno dimostrato che nomi futuristici (es. gemini-3.1-flash-lite-preview) o modelli specifici (Native Audio) devono essere usati con precisione per evitare errori 1007/1008.
- **Strategia a 2 Modelli**:
    1. **Gemini 2.5 Flash Native Audio Dialog** (`gemini-2.5-flash-native-audio-latest`): **OBBLIGATORIO per AUDIO/LIVE**. Ottimizzato per bidi-streaming. Quasi gratuito per audio. *Nota: Supporta solo AUDIO.*
    2. **Gemini 3.1-flash-lite-preview**: **PREDEFINITO per CHAT/TESTO**. Estremamente veloce ma può presentare latenze variabili.
- **Strategia Fallback**: Se `gemini-3.1` non risponde entro **20 secondi**, il sistema effettua automaticamente il downgrade a `gemini-1.5-flash` per garantire la continuità del servizio.

### Validazione Rigorosa SDK v1.x (Extra inputs)
- **Problema**: Errore `Extra inputs are not permitted` per `tools.0.Tool.name` o `tools.0.callable`.
- **Causa**: Il nuovo SDK `google.genai` (v1.x) utilizza Pydantic per la validazione delle configurazioni. Passare i dizionari dei tool del sistema Marcus (che contengono campi extra come `callable`) direttamente a `GenerateContentConfig.tools` genera un errore di validazione immediato.
- **Risoluzione**: Implementata la funzione helper `_format_tools_for_google` che filtra solo i campi ammessi (`name`, `description`, `parameters`) e li incapsula in oggetti `types.FunctionDeclaration` dentro `types.Tool(function_declarations=[...])`.

### Blocco della Sessione Live, Errore 'language_codes', Perdita del Contesto e Mancata Esecuzione delle Skill Vocali (v15.0/v15.1 - Maggio 2026)
- **Problemi riscontrati**:
    1. *Turno Singolo*: Marcus rispondeva esattamente una sola volta all'avvio, dopodiché rimaneva in ascolto ma non rispondeva più alle domande successive.
    2. *Connessione Fallita*: Errore `language_codes parameter is not supported in Gemini API.` che faceva fallire la connessione in loop.
    3. *Perdita del Contesto*: Marcus dimenticava tutto il contesto a ogni turno vocale successivo, comportandosi come se parlasse per la prima volta.
    4. *Mancata Esecuzione delle Skill*: Marcus ignorava o non eseguiva le skill custom dell'utente (es. "leggi le mail" o "suona musica") durante la conversazione vocale, rispondendo che non aveva la capacità tecnica per farlo.
- **Cause**:
    1. Quando il WebSocket si disconnetteva pulitamente (`code 1000`), l'assenza di eccezione non azzerava `self._live_session`, lasciando il connection manager bloccato.
    2. Il nuovo SDK `google-genai` rifiuta esplicitamente `language_codes=["it-IT"]` dentro `AudioTranscriptionConfig`.
    3. Poiché la Live API invia solo flussi audio in uscita (PCM), la variabile di testo della risposta di Marcus era vuota, per cui non registravamo alcuna trascrizione delle sue risposte vocali nella cronologia `_live_conversation_history`.
    4. All'avvio del robot, `orchestrator.py` chiamava `start_persistent_live()` prima di scoprire e registrare le abilità (`SkillRegistry`), per cui la connessione WebSocket iniziale veniva stabilita con `tools=None`.
    5. Anche dopo aver passato i tool corretti, la Live API (WebSocket) riceve le richieste di esecuzione dei tool asincrone tramite messaggi top-level `tool_call`. La nostra implementazione non intercettava `msg.tool_call`, non eseguiva le funzioni tramite il registro e non inviava a Gemini il messaggio di risposta `tool_response` (richiesto dal protocollo bidirezionale), bloccando l'esecuzione.
- **Risoluzione**:
    1. Avvolto il ciclo di ricezione in un blocco `try...finally` per azzerare `self._live_session = None` e garantire la riconnessione istantanea.
    2. Rimosso `language_codes` da `AudioTranscriptionConfig()` lasciandolo vuoto. Gemini rileva automaticamente la lingua parlata e la trascrive perfettamente in italiano.
    3. Abilitato `output_audio_transcription` nella configurazione e catturata la trascrizione in tempo reale del parlato del modello (`sc.output_transcription.text`) per popolare e salvare i turni in memoria (fino a 30 scambi di contesto continuo) iniettati nel prompt di sistema delle sessioni successive.
    4. Modificato `start_persistent_live` in `llm_live_api.py` per accettare le `functions` e innescare una riconnessione automatica (`self._reconnect_live()`) per iniettare i tool aggiornati. Modificato `_init_resources` in `orchestrator.py` per estrarre `self.skill_registry.get_function_declarations()` e passarle durante l'avvio.
    5. Implementata la gestione asincrona completa dei `tool_call` ricevuti via WebSocket in `llm_live_api.py`. Creato un task in background che invoca il callback registrato dall'orchestratore, esegue in modo sicuro la skill (`skill.safe_execute()`), formatta il risultato in un oggetto `types.FunctionResponse` e lo reinvia su WebSocket a Gemini usando `session.send_tool_response(function_responses=...)`. Hookato l'orchestratore all'avvio con `self.llm_service.register_tool_executor(self._execute_tool_live)`.

> [!IMPORTANT]
> Il modello **Native Audio** non deve mai essere forzato in modalità TEXT, altrimenti restituirà `Invalid Argument (1007)`.

## Hardware e VUI (Voice User Interface)

### ReSpeaker Lite: Modalità USB (Firmware vs Bootloader)
- **Problema:** Dopo flash, porta `/dev/ttyACM0` sparita.
- **Causa:** Firmware disabilita serial JTAG.
- **Soluzione:** Reset fisico (tasto BOOT all'inserimento USB) e config firmware `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG: y`.

### ReSpeaker Lite: Integrazione Hardware Professionale (v8.0)
- **Componente Esterno**: L'uso del componente `respeaker_lite` (repo `formatBCE`) è **obbligatorio** per stabilizzare i clock I2S dell'XMOS XU316. La configurazione puramente manuale causa spesso il freeze del DMA (dati piatti a 32743).
- **Pinout Corretto (XIAO S3 + Lite)**:
    - **MCLK**: GPIO9 (NON GPIO10 o GPIO17).
    - **BCLK**: GPIO8
    - **LRCLK**: GPIO7
    - **DIN**: GPIO43
    - **DOUT**: GPIO44
    - **RESET (XMOS)**: GPIO2
- **Gestione Dipendenze**: Il componente esterno richiede che i blocchi `i2c:` e `i2s_audio:` siano comunque definiti nel YAML per fornire il bus hardware sottostante.

### Compatibilità ESPHome e Versioning
- **Errore 'synchronous'**: Le versioni recenti del repository `respeaker_lite` usano parametri incompatibili con ESPHome 2026.2.4 (es. `register_action(..., synchronous=False)`).
- **Risoluzione (Pinning)**: Se la compilazione fallisce, bisogna "bloccare" il componente a un commit stabile e compatibile (`ce94338`).
- **Attenzione API**: In questa specifica versione (`ce94338`), la classe `RespeakerLite` **non ha** il metodo `.reset()`. Bisogna usare **`.start_dfu_update()`** per avviare la modalità flash dell'XMOS.
  ```yaml
  external_components:
    - source:
        type: git
        url: https://github.com/formatBCE/Respeaker-Lite-ESPHome-integration
        ref: ce94338a9284b940bb436a8aabd48b58b430da43
  ```

### Architettura Audio Marcus-Gemini (Definitiva)
- **Rate Standard: 16kHz**: Nonostante l'hardware supporti i 48kHz, l'intera pipeline (Firmware → USB → ROS → Gemini) deve lavorare a **16000 Hz** per compatibilità con i modelli di intelligenza artificiale.
- **Streaming USB-JTAG**: Per inviare l'audio al Pi, è necessario definire manualmente i blocchi `microphone` e `speaker` (usando gli ID standard `respeaker_microphone` e `respeaker_speaker`) per agganciare il callback `on_data` e il protocollo custom `AUDIO_PCM:<len>:<timestamp>`.
- **Thread-Safety (FreeRTOS)**: La riproduzione dello speaker richiede un task dedicato (`spk_task`) e un Ring Buffer thread-safe in C++ (`portMUX_TYPE`) per evitare stuttering e race conditions tra il polling USB e il driver I2S.

### "Darth Vader" & "Chipmunk" Effects (Sample Rate Mismatch)
- **Problema 1 (Darth Vader - Voce lenta/grave)**: Gemini Live invia audio a 24kHz. Se riprodotto a 16kHz, l'audio rallenta del 33%.
- **Problema 2 (Chipmunk/Scoiattolo - Voce veloce/acuta)**: Il DAC del ReSpeaker Lite USB lavora nativamente a **48kHz**. Se il driver PyAudio apre lo stream a 16kHz ma l'hardware ignora la richiesta e consuma dati alla sua frequenza nativa, l'audio viene riprodotto 3 volte più velocemente (48/16).
- **Architettura Finale Standardizzata (v8.0)**:
    1. **Firmware**: Campionamento diretto a **16kHz** (più leggero e stabile per il DMA).
    2. **Bus ROS**: Tutto l'audio viaggia sui topic a **16kHz mono PCM**.
    3. **Hardware (VUI Node)**: Riceve i dati via USB-JTAG e li inietta direttamente nelle pipeline AI senza necessità di upsampling complesso sul Raspberry Pi 5.
- **Nota Tecnica**: Usa sempre `audioop` per il resampling; è più performante e preciso di `numpy.interp` per il filtraggio PCM.
- **Voce (Aggiornamento)**: Inizialmente era stato forzato un volume `0.2x` e un pacing lento per aggirare i difetti di VAD e riproduzione. Successivamente, la Live API ha restituito inavvertitamente una voce femminile. Abbiamo quindi dovuto re-impostare esplicitamente la voce maschile per Gemini Live. Inoltre, la lentezza e i blocchi non andavano risolti forzando il pacing vocale, ma unificando gli stati del turn-taking nel VUI (vedi Sezione "Unificazione Turn-Taking e Barge-in").

> [!CAUTION]
> **REGOLA PERMANENTE — Rate HW vs Rate Parametro**: Il parametro ROS `sample_rate` (16000) NON corrisponde al rate nativo del DAC hardware (48000). Non usare MAI `self.sample_rate` per determinare se serve resampling. Usare **`self._out_hw_rate`** ottenuto da `pa.get_device_info_by_index(idx)['defaultSampleRate']`. Lo stream di output deve essere aperto al rate nativo HW, e il resampling `16k→48k` via `audioop.ratecv` deve essere sempre applicato prima di scrivere.

### Errore Concettuale: Acquisizione Audio USB Nativo e Clipping Saturato
- **Problema 1 (Saturazione Porcupine)**: Il rilevatore wake-word non funzionava e il log mostrava volumi incontrollabili. In passato era stato ipotizzato falsamente che l'audio fosse 'troppo basso' aggiungendo un Boost digitale 5x.
- **Verità Scoperta (v3.7)**: L'hardware ReSpeaker sfora i livelli di picco (Peak-to-Peak \> 48000) distorcendo l'audio in onda quadra (clipping puro).
- **Risoluzione 1**: Eliminare i boost digitali. Prendere ESCLUSIVAMENTE il canale Sinistro (per evitare *phase cancellation* da downmix stereo). APPLICARE UN'ATTENUAZIONE `3x` in input (`/ 3.0`) via NumPy prima di convertire a `int16` per alimentare il VAD WebRTC e Porcupine. I livelli RMS devono stare tra 5000 e 15000.

### Riproduzione Notturna: DAC Auto-Mute (Stuttering)
- **Problema**: Disconnessioni continue e balbuzie nell'audio in uscita ('stuttering' tra le sillabe) quando il volume di Gemini Live viene abbassato per uso notturno (5%).
- **Causa (DAC Noise Gate)**: I chip hardware USB economici come il ReSpeaker utilizzano un noise-gate per il risparmio energetico DSP: se l'audio si abbassa a livelli near-zero (anche fisiologicamente tra una consonante e l'altra), l'amplificatore viene brutalmente staccato causando dropout fisici.
- **Risoluzione**: Aggiunta dinamica di "Dithering" (rumore bianco inaudibile). Quando il volume è inferiore al 15%, si aggiunge `np.random.randint(-12, 13)` al segnale digitale prima del cast a `int16`. Il rumore a -68dBFS bypassa il gate del DAC hardware tenendolo sempre sveglio e mantenendo fluido l'audio al 5%.

### Rumore Ventola/VAD e Feedback
- **Problema (Falsi Positivi VAD)**: Marcus crede di sentire l'utente a causa del rumore della ventola del Raspberry Pi 5.
- **Risoluzione**: Abbassato `stt_gain` da `0.6` a `0.15` per isolare meglio la voce umana dal rumore di fondo stazionario.
- **Feedback Audio (Beep)**: Volume del beep ridotto e normalizzato (amp 3276, 10% del totale) per evitare fastidio notturno e prevenire lo sbalzo dinamico prima di avviare il receiver.

### ReSpeaker Lite: Evoluzione USB-Tethered e Piano di Controllo (v0.1.x)
- **Evitare Conflitti Bus (I2S vs USB)**: L'inizializzazione del componente `i2s_audio` sullo XIAO ESP32 (anche se solo in ascolto) crea contese sui clocks che impediscono alla XMOS di essere vista come periferica USB dal Raspberry Pi. L'architettura definitiva è **"USB-Pure"**: XMOS gestisce solo l'audio via USB, XIAO gestisce solo il controllo (I2C/Seriale).
- **Sensibilità al Boot e I2C Scanning**: La XMOS richiede una finestra di circa **4 secondi** di stabilità hardware al boot per completare l'enumerazione USB High-Speed. Lo `scan: true` del bus I2C di ESPHome può interferire con questa fase; è obbligatorio usare `scan: false` e indirizzi statici.
- **Control Plane Seriale (115200 baud)**: Spostata la gestione di LED ed effetti DSP (AEC, AGC, NS) su un protocollo seriale dedicato tra Pi e XIAO. Questo riduce il carico sul bus I2C ed evita glitch audio.
- **Bootloader e GPIO 2**: Il pin GPIO2 controlla il Reset della XMOS. In configurazione USB-Audio, deve essere tenuto stabilmente ad `HIGH`. Se il firmware dello XIAO non lo gestisce esplicitamente o lo resetta durante il boot, la XMOS sparirà dal sistema operativo del Pi.
- **Vantaggi Offloading DSP**: Spostare AEC (Echo Cancellation) e NS (Noise Suppression) nel firmware hardware dell'XMOS (invece di farlo in Python/NumPy) rimuove drasticamente il carico CPU sul Pi 5 e garantisce una qualità audio superiore.
- **Audio Relay Zero-Latency (v5.8)**: Invece di usare un topic ROS interno per passare l'audio da `LLMService` a `Orchestrator`, usiamo ora l' `EventBus` (in-process). Questo risolve i problemi di "audio muto" e riduce il lag di circa 50-100ms.
- **VAD Stability (v5.8)**: Soglie VAD aumentate (Confirm: 200ms, Silence: 500ms) per evitare interruzioni frammentate durante le normali pause del parlato umano.
- **Orphaned Launch Fix (v5.8)**: Scoperto che `ros2 launch` caricava file obsoleti dalla cartella `install/` che non riflettevano le modifiche ai parametri `stt_gain` e `default_volume`. La ricompilazione con `colcon build` e il sync manuale della cartella `share/` sono ora parte integrante del processo di deploy per i parametri di lancio.
- **Normal Mode (v5.7.1)**: Volume hardware impostato all'**1%** reale tramite il corretto launch file `install/`. Guadagno digitale mantenuto al **100%** per fedeltà.
- **ECO AUDIO DOPPIO (v5.9 — Bug Critico)**: Quando Gemini Live API risponde con audio, il chunk veniva pubblicato su DUE canali distinti: il ROS topic `/ai/conversation/audio_chunk` (catturato da `_audio_chunk_callback` in orchestrator e forwardato allo speaker) E l'EventBus `LIVE_AUDIO_CHUNK` (catturato da `_on_live_audio_chunk` e forwardato allo speaker). Risultato: l'utente sentiva l'eco (stesso audio due volte). **Fix**: rimossa la pubblicazione ROS in `_handle_live_message`. Usare SOLO l'EventBus per il relay live audio. Il publisher `pub_audio_chunk` rimane per debug esterno senza relay.
- **MARCUS NON RISPONDE ALLA VOCE (v5.9 — Bug Critico)**: La Live API di Gemini ha due modalità: (1) testo-triggered — `_async_generate_live()` crea `_live_response_future` e attende la risposta; (2) audio-streaming autonoma — Gemini risponde all'audio del microfono senza che nessuno abbia chiamato `generate_live()`. In questo caso `_live_response_future` è `None`, la risposta arriva ma va nel branch `else: pass` di `_handle_live_message → turn_complete`, quindi viene scartata. **Fix**: aggiunto branch `else` in `turn_complete` che pubblica il testo su `/ai/conversation/response` e invia `EventType.LIVE_TURN_COMPLETE`; l'orchestratore gestisce il reset del VUI con `_on_live_turn_complete → _delayed_vui_release(1.2s)`.
- **PORCUPINE BEEP DURANTE TTS (v5.9)**: Porcupine con boost 30x rilevava il proprio nome nell'audio residuo del TTS (anche con AEC hardware). **Fix**: nel callback audio, se `tts_now=True` si salta completamente il loop Porcupine e si azzera il buffer residuo per evitare "fantasmi" al termine del TTS.
- **MICROFONO BLOCCATO E "STALLO" MARCUS (v6.0)**: Il parametro `noise_gate_threshold` era a `400.0`, inferiore al rumore di fondo della stanza (`~523.1 RMS`). Il nodo VUI non rilevava mai il silenzio (falsi positivi VAD continui) restando bloccato in "registrazione in corso", senza mai inviare `end_of_speech` al LLM. **Fix**: Alzato `noise_gate_threshold` a `1500.0` nel nodo VUI.
- **MANCATA PUBBLICAZIONE STATO TTS E CONSOLE FALLBACK (v6.0)**: Il servizio TTS falliva l'inizializzazione perché `GOOGLE_APPLICATION_CREDENTIALS` non veniva letto correttamente al di fuori della shell (non faceva il `load_dotenv`), causando un fallback silenzioso a `CONSOLE_TTS`. Inoltre, il servizio TTS non pubblicava sul topic ROS `/ai/tts/speaking`, impedendo ai LED di accendersi (THINKING state) e impedendo al VUI di raddoppiare il noise gate per sopprimere l'eco dello speaker. **Fix**: Inserito `load_dotenv` esplicito in `tts_service.py` e aggiunto il publisher per `/ai/tts/speaking` allo start/stop della generazione TTS.

---

## Sviluppo e Deployment

### Sincronizzazione Workspace e Symlink
- **Problema**: Modifiche non rilevate da `colcon build`.
- **Causa**: Directory `/home/robopy/...` era diventata una cartella reale invece di un symlink all'SSD.
- **Risoluzione**: Ripristinato symlink `ln -s /mnt/ssd/... /home/robopy/...`.
- **Regola**: Verificare sempre la sincronia con `bash sync_marcus.sh`.

### Modifica di Copie Backup invece dei Sorgenti Reali (Sincronizzazione Fallita)
- **Problema**: L'AI apportava modifiche a un file (es. `email_skill.py`) ma lo script `sync_marcus.sh` non le propagava sul Raspberry Pi.
- **Causa**: L'AI modificava una copia temporanea del file situata nella cartella root del progetto (`/email_skill.py`), mentre lo script di sincronizzazione legge solo i file originali all'interno di `robopy_controller/...`.
- **Risoluzione**: Verificare SEMPRE il path assoluto prima di modificare un file. Modificare tassativamente i file originali dentro `robopy_controller/` (es. `robopy_controller/robot_ai/skills/builtin/email_skill.py`) e ignorare i file cloni nella root del workspace su Windows.

### Affidabilità USB Serial JTAG
- **Problema**: Audio interrotto o drop di chunk.
- **Risoluzione**: Timeout firmware a `15ms` e `reset_input_buffer()` lato Python in caso di errore di framing.


## Stabilità Memoria e Rete

### LlamaIndex: Errore "RobopyEmbedding is async-only"
- **Problema**: Crash del nodo AI durante l'inserimento di nuove memorie.
- **Causa**: Il bridge `RobopyEmbedding` è strettamente asincrono. LlamaIndex, se chiamato tramite metodi sincroni (`insert`) o wrapper non corretti (`run_in_executor`), tenta di invocare l'embedding in modo bloccante, fallendo.
- **Risoluzione**: Utilizzare **sempre** `await self.index.ainsert(doc)` all'interno del loop principale. Evitare `to_thread` per operazioni che coinvolgono embedding asincroni nativi.

### Connettività in Reti Robot Instabili (Latenza e IPv6)
- **Problema**: Timeout persistenti e `gaierror` (DNS failure) su Home Assistant ed Email, nonostante il ping funzionante.
- **Causa 1 (IPv6)**: Molti stack di rete (es. Raspberry Pi 5 con NetworkManager) tentano risoluzioni IPv6 che rimangono "appese" se la rete non è configurata correttamente, causando timeout di 10-20s.
- **Causa 2 (DNS locale)**: Il DNS locale del robot può fallire sporadicamente sotto carico o per cattiva configurazione di `systemd-resolved`.
- **Risoluzione**: 
    1. **Forzare IPv4**: Usare `family=socket.AF_INET` in tutte le chiamate `getaddrinfo`, `websockets.connect` e `aioimaplib`.
    2. **DNS Fallback Manuale**: In caso di `gaierror`, implementare nel codice un fallback a IP statici noti (es. cluster Gmail `74.125.206.108`) per i servizi critici.
    3. **Timeout Estesi**: Su reti con latenza >200ms, il timeout di connessione IMAP/SMTP deve essere di almeno **30 secondi**.
    4. **Nameserver Esterni**: Aggiungere `nameserver 1.1.1.1` in `/etc/resolv.conf` aiuta a bypassare router locali problematici.

### Credenziali: Google ADC vs Gemini API Key
- **Problema**: Messaggi `Failed to create Google TTS/ASR client: Your default credentials were not found`.
- **Causa**: Distinzione tra i modelli Gemini Pro (accessibili via `GEMINI_API_KEY`) e i servizi professionali di architettura v1 (Google Cloud Text-to-Speech e Speech-to-Text). Questi ultimi richiedono obbligatoriamente le **Application Default Credentials (ADC)** tramite un file JSON di Service Account.
- **Risoluzione**: Senza ADC, il sistema può comunque conversare via chat (LLM), ma la voce e il riconoscimento professionale falliranno al fallback. È necessario caricare il JSON e impostare la variabile `GOOGLE_APPLICATION_CREDENTIALS` nel `setup_keys.sh`.

### ROS 2: Sincronizzazione "Install" vs "Src" (Lezione Critica)
- **Problema**: Modifiche al codice Python caricate ma non attive sul robot, nonostante il file sorgente fosse aggiornato.
- **Causa**: I nodi lanciati con `ros2 launch` (senza `--symlink-install`) leggono i file dalla directory `install/`. La sincronizzazione tramite script `sync_marcus.sh` spesso aggiorna la `src/` ma non ricostruisce la `install/` automaticamente se l'ambiente di build è instabile.
- **Risoluzione (Hot-fix)**: Durante lo sviluppo rapido, sincronizzare i file `.py` **direttamente** nel percorso corrispondente dentro `install/robopy_controller/lib/python3.11/site-packages/...`.
- **ATTENZIONE BOM (v2.0)**: Alcuni strumenti di editing su Windows possono aggiungere un Byte Order Mark (BOM) UTF-8 all'inizio dei file. Se questo accade agli script wrapper (es. quelli in `install/.../bin/`), Linux fallirà l'esecuzione con `OSError: [Errno 8] Exec format error` perché il shebang `#!` non è più al primo byte. Usare `sed -i '1s/^\xEF\xBB\xBF//' <file>` per rimuoverlo.

---

## Miglioramento Sensibilità Wake-Word (v5.2)

### Incremento Digitale Segnale (stt_gain)
- **Problema**: La parola chiave "Marcus" non veniva rilevata nonostante la sensibilità di Porcupine fosse al massimo (0.92).
- **Causa**: Il segnale grezzo del ReSpeaker Lite USB è estremamente silenzioso (RMS ~900), mentre Porcupine lavora in modo ottimale con RMS tra 5000 e 15000.
- **Risoluzione**: Implementato un boost digitale `stt_gain` (default `5.0`) applicato direttamente nel callback audio *prima* dei motori VAD/Porcupine. 
    ```python
    boosted_l = (l_ch[:n].astype(np.float32) * self.stt_gain)
    np.copyto(self._int16_vad_buf[:n], np.clip(boosted_l, -32768, 32767).astype(np.int16))
    ```
- **Nota (v5.4)**: Bilanciamento Guadagno Digitale vs Hardware DSP. 
    1. Un guadagno eccessivo (es. **15.0**) amplifica troppo il rumore di fondo (RMS 700 -> 10k), "sordendo" Porcupine.
    2. Disabilitare il DSP hardware (AGC/AEC/NS) rimuove la pulizia nativa del firmware; per il ReSpeaker Lite USB è preferibile tenerlo **ON** (default firmware) e limitare il boost digitale a **5.0x**.

---

### Incompatibilità Fine Riga (CRLF vs LF)
- **Problema**: Script shell (`.sh`) salvati su Windows falliscono su Linux con l'errore `-bash: $'\r': command not found`.
- **Causa**: Windows usa `\r\n` (CRLF), Linux vuole solo `\n` (LF). Il carattere `\r` (Carriage Return) viene interpretato da Bash come un comando inesistente.
- **Risoluzione**: Convertire sempre i file in formato **LF**. Usare `sed -i 's/\r$//' <file>` sul Raspberry o configurare l'editor (VS Code) per usare LF come predefinito per il progetto.

### Autenticazione Gmail (IMAP/SMTP)
- **Problema**: L'accesso alla posta fallisce con `[ALERT] Application-specific password required`.
- **Causa**: Google richiede la "Verifica in 2 passaggi" e non accetta più la password standard dell'account per le app di terze parti (Less Secure Apps). 
- **Risoluzione**: Generare una **Password per le app** (codice di 16 caratteri) dalle impostazioni di sicurezza dell'account Google e assicurarsi che l'accesso IMAP sia abilitato nelle impostazioni di Gmail.

### Bug Flusso Conversazionale Duale e topic `/ai/conversation/response` vuoto
- **Problema**: Il topic `/ai/conversation/response` pubblicava `data: ""`. Marcus parlava (via TTS) ma il canale testuale (Foxglove/chat) riceveva stringa vuota. EmailSkill non veniva invocata correttamente. Nessuna conversazione finiva nel RAG.
- **Causa (3 bug distinti)**:
    1. Il `response_callback` (che pubblica sul topic ROS) era chiamato solo quando `response_text != ""`. Ma quando Gemini risponde con una `function_call` (skill), `response_text` è vuoto → topic vuoto.
    2. `check_emails` non aveva un handler dedicato in `conversation.py` (come invece ce l'ha `check_home_assistant`). I `speak` dell'AsyncGenerator venivano persi, non finivano nel topic ROS.
    3. Il salvataggio in RAG era gated su `rag.enabled` dalla config: se la config non aveva la sezione `rag`, nessuna conversazione veniva mai salvata.
- **Risoluzione**:
    - `conversation.py`: aggiunto handler `check_emails` dedicato con raccolta di tutti gli speak della skill; `response_callback` ora viene chiamato usando i testi skill quando l'LLM restituisce function_call; RAG ora sempre attivo (rimosso il gate sulla config).
    - `skill_executor.py`: aggiunto routing esplicito per `check_emails` che forwarda correttamente `{intent, account, limit}` dalla function_call di Gemini.

---


### Bug Critico: Wake-Word "Marcus" Mai Rilevata (TTS Block Post-Greeting)
- **Problema**: Dopo il messaggio di benvenuto all'avvio, la parola di attivazione "Marcus" non veniva **mai** rilevata da Porcupine.
- **Causa**: `_speak_greeting_with_retry` in `orchestrator.py` chiama `llm_service.generate_live()` direttamente, senza passare per `conversation_manager.process_input()`. Il `_audio_chunk_callback` pubblica `ai/tts/speaking = True` ogni volta che arriva un chunk audio dal saluto, ma **nessuno pubblicava `speaking=False`** al termine. Il flag `_ev_tts` del VUI node rimaneva `True` per sempre, bloccando l'intero loop di Porcupine nel callback audio.
- **Risoluzione (v5.6)**: Aggiunto blocco `finally` in `_speak_greeting_with_retry` che pubblica `speaking=False` tramite `conversation_manager._set_vui_speaking(False)` con un ritardo di 1.5s (per lasciare al DAC il tempo di scaricare l'audio) — in **qualsiasi** caso di uscita (successo, timeout, errore).
- **Regola**: **Qualsiasi funzione che riproduce audio direttamente tramite `llm_service` o `tts_service`** (bypassando `ConversationManager`) DEVE pubblicare `speaking=False` al termine. Solo `ConversationManager.process_input()` lo fa automaticamente.


### Architettura Barge-In (v5.6) — Mic Sempre Aperto con AEC Hardware

- **Contesto**: ReSpeaker Lite ha AEC (Acoustic Echo Cancellation) hardware attivo via firmware ESPHome. L'eco della voce di Marcus durante TTS è gestita a livello chip XMOS XU316. Non serve bloccare software il microfono.
- **Errore Precedente**: `_set_vui_speaking(True)` pubblicava anche su `mic_mute_pub` → il microfono veniva chiuso durante TTS → impossibile interrompere Marcus.
- **Architettura Corretta (v5.6)**:
  1. **VUI node**: rimosso il `return (None, paContinue)` durante TTS. Porcupine e VAD girano **sempre**.
  2. **Barge-in**: Se VAD rileva voce sostenuta (6 frame = ~120ms) dopo 1.5s di TTS → svuota `_audio_out_queue`, pubblica su `/ai/barge_in`.
  3. **ConversationManager**: `cancel_current_turn()` chiama `task.cancel()` iniettando `CancelledError` nella coroutine LLM. `_set_vui_speaking` NON tocca più `mic_mute_pub`.
  4. **Orchestrator**: subscriber su `/ai/barge_in` → chiama `cancel_current_turn()`.
- **Separazione stati fondamentale**:
  - `/ai/tts/speaking` (Bool) = Marcus sta riproducendo audio (per barge-in detection VUI)
  - `/ai/input/mic_mute` (Bool) = Il LLM deve ignorare l'audio in arrivo → **MAI impostato automaticamente durante TTS**
- **Regola permanente**: Nessun componente deve pubblicare `mic_mute=True` durante TTS. Il `mic_mute` è un controllo esplicito utente/orchestratore, non uno stato derivato dal TTS.
- **Grace period barge-in**: 1500ms post-inizio TTS prima che il barge-in si attivi, per evitare falsi positivi da transienti audio.
- **Parametri Tunable (v5.6)**: 
    - `barge_in_min_tts_ms` (default 1500.0): ms di attesa prima di attivare il rilevamento interruzioni.
    - `barge_in_min_frames` (default 6): numero di frame di voce consecutivi (~120ms) necessari per scatenare il barge-in.
- **Verifica Funzionamento (Logs)**:
    - `🎤 [GREETING] Rilascio microfono (speaking=False)` → Porcupine torna in ascolto post-saluto.
    - `🎤 [BARGE-IN] Voce rilevata durante TTS` → Marcus ha rilevato l'utente e si sta interrompendo.
    - `🛑 [BARGE-IN] Cancellazione turno LLM in corso...` → L'orchestratore ha ricevuto il segnale e sta resettando il cervello.

### Bug: Wake-Word Rilevata ma Nessuna Risposta (VAD Silent)
- **Problema**: Porcupine rileva la parola "Marcus", ma il sistema non risponde e non appare l'ascolto (VAD) nei log.
- **Causa**: Il file `robot_ia_launch.py` nell'installazione sovrascriveva il parametro `stt_gain` a **1.5**. Con un volume raw di ~70-90, il segnale "boosted" arrivava solo a ~130, troppo poco per la soglia del VAD WebRTC (che richiede tipicamente >400 per attivarsi).
- **Risoluzione**: Impostato esplicitamente **`stt_gain: 30.0`** e **`noise_gate_threshold: 150.0`** nel launch file sorgente e sincronizzato l'installazione. Con gain 30x, l'RMS risultante è ~2100-2700, garantendo l'apertura affidabile della gate VAD.
- **Regola**: Verificare sempre che il valore di `FORCED_BOOSTED` nei log sia significativamente superiore a `noise_gate_threshold` durante il parlato.

### Risoluzione Echo e "Zitto Marcus" (v10.3)
- **Problema**: Marcus ascoltava la propria voce e si fermava rispondendo a se stesso. Porcupine era stato soppresso durante il TTS, rendendo impossibile fermarlo con frasi come "zitto marcus".
- **Causa**: Il flag `_is_tts_speaking` in `respeaker_vui_node.py` non veniva mai aggiornato correttamente dalla callback ROS, quindi il drop del gain (a 0.5x per evitare eco) non entrava mai in funzione. L'upload audio a Gemini (VAD gate) non veniva soppresso.
- **Risoluzione**:
    1. Aggiornato `self._is_tts_speaking = msg.data` in `_tts_speaking_cb`. Questo attiva il drop a 0.5x del `stt_gain` e sopprime l'invio audio a Gemini durante il TTS.
    2. Rimossa la soppressione di Porcupine durante il TTS. Con il gain a 0.5x, l'eco non genera falsi positivi su Porcupine, ma la parola chiave ("Marcus", anche dentro a "zitto marcus") può essere pronunciata dall'utente per interrompere istantaneamente l'audio e fermarlo.
    
### Troncamento Frasi e Auto-Interruzione (v10.4)
- **Problema**: Marcus si interrompeva da solo mentre parlava, troncando le frasi finali.
- **Causa**: Il volume dello speaker, accoppiato a un leggero eco, superava la soglia del VAD e scattava il meccanismo di Barge-in (che si attivava dopo soli 6 frame / ~120ms di "voce" percepita durante il TTS). Il drop dello `stt_gain` a 0.5x non era sufficiente per silenziare l'eco ai volumi più alti.
- **Risoluzione**: 
    1. Aumentato `barge_in_min_frames` da 6 a 15 (circa 300ms di voce continuativa) per rendere il Barge-in più resistente ai falsi positivi.
    2. Ridotto ulteriormente `stt_gain` durante il TTS da 0.5x a 0.1x in `respeaker_vui_node.py` per sopprimere più aggressivamente il segnale in ascolto quando l'AI parla, senza però chiudere completamente il microfono (consentendo ancora di fermarlo con "zitto Marcus").

*Queste note servono a non ripetere gli stessi errori e a ricordare le scelte tecniche per la parte hardware/software del progetto.*

---

## Bug: `respeaker_vui_node` Zombie (Maggio 2026)

- **Problema**: Marcus avviato, nessuna risposta Spotify. Log mostrava `[BARGE-IN]` immediatamente anche senza parole. Il VUI sembrava "confuso".
- **Causa**: Il nodo `respeaker_vui_node` era stato avviato in background in una sessione precedente (PID 289416, avviato il 30 Aprile) senza i `--ros-args` corretti. Il nuovo launch ne avviava un secondo (PID 333218). **Due istanze** competitive sugli stessi topic ROS 2 (`/respeaker/speaker_audio`, `/ai/barge_in`, `/ai/tts/speaking`) causavano interferenze: l'audio TTS arrivava al nodo sbagliato, i barge-in scattavano spuriamente.
- **Risoluzione**:
    1. Identificare i zombie con `ps aux | grep respeaker_vui | grep -v grep`.
    2. Killare il processo senza `--ros-args` (il più vecchio): `kill -9 <PID_ZOMBIE>`.
    3. Riavviare il launch.
- **Regola**: Prima di ogni avvio, eseguire `pkill -f respeaker_vui_node` per garantire una slate pulita, oppure aggiungere al `restart.sh` un `pkill -f robot_ai_node && pkill -f respeaker_vui_node` preventivo.

## Bug: `'SkillRegistry' object has no attribute 'get_skill'` (Maggio 2026)

- **Problema**: Fatal crash in `robot_ai_node.py`: `'SkillRegistry' object has no attribute 'get_skill'`.
- **Causa**: Il codice in `orchestrator.py` (linea 334, callback `_barge_in_callback`) chiamava `self.skill_registry.get_skill("spotify_skill")` per mettere in pausa Spotify all'intercettazione della wake word. La classe `SkillRegistry` espone solo il metodo `get()`, non `get_skill()`.
- **Contesto**: Il crash avveniva OGNI volta che l'utente pronunciava la wake word "Marcus" (o qualsiasi frase), perché il barge-in scattava, invocava il callback, e il crash abbatteva l'intero nodo.
- **Risoluzione**: Aggiunto alias `get_skill(name)` in `skill_registry.py` che delega a `get(name)`. File aggiornato sia in `src/` che in `install/` via SCP diretto (hot-fix senza rebuild).
- **Lezione**: Quando si aggiungono nuovi riferimenti a metodi del registry in `orchestrator.py`, verificare SEMPRE che il metodo esista in `SkillRegistry`. Preferire `get()` come API standard e aggiungere alias solo per retrocompatibilità documentata.

## Bug Critico: `UnboundLocalError: rms_l` nel VUI Audio Callback (v11.1, Maggio 2026)

- **Problema**: Spotify veniva comandato correttamente (Gemini lo capiva), ma la musica non partiva. Il VAD contava migliaia di frame (`5000, 6000+...`) senza mai chiudersi → il voice end-of-speech non veniva mai pubblicato → la sessione di ascolto non terminava mai → il turno LLM con la function call Spotify veniva interrotto da un barge-in spurio.
- **Causa**: In `respeaker_vui_node.py`, le variabili `rms_l`, `rms_r` e `rms_boosted` venivano assegnate **solo** all'interno del blocco `if self._rms_chunk_count % 16 == 0:`, ma venivano **lette sempre** alle righe successive (`cfg_diag_mode`, barge-in log). Nei 15/16 dei callback (quelli dove `% 16 != 0`), Python lanciava `UnboundLocalError: cannot access local variable 'rms_l'`. Questa eccezione silenziosa faceva saltare il blocco VAD intero per ogni callback, con il warning `Errore callback audio`. Il VAD continuava a contare frame senza mai chiudersi.
- **Risoluzione (v11.1)**: Inizializzate le tre variabili a valori sicuri **prima** del blocco condizionale `% 16`:
    ```python
    rms_l = rms_current   # sempre valido (calcolato sopra)
    rms_r = 0.0
    rms_boosted = 0.0
    if self._rms_chunk_count % 16 == 0:
        r_ch  = audio_stereo[1::2]
        rms_r = np.sqrt(np.mean(r_ch[:n].astype(np.float32)**2))
    ```
- **Lezione**: **MAI usare variabili locali in un hot-path audio callback senza inizializzarle al top dello scope**, anche se sono usate solo in branch condizionali. Gli `UnboundLocalError` in callback PyAudio vengono inghiottiti dall'`except` generico e producono effetti collaterali devastanti e difficili da tracciare (VAD bloccato, turni LLM mai completati). Ogni variabile locale usata in più branch va inizializzata prima del primo `if`.

## Bug: Raspotify Non Appare Come Device Spotify (Maggio 2026)

- **Problema**: Marcus capisce il comando Spotify, la skill viene eseguita, ma ritorna `404 No active device found`. La lista `sp.devices()` è vuota.
- **Causa (catena)**: 4 problemi sovrapposti in `/etc/raspotify/conf`:
    1. **`LIBRESPOT_QUIET=`** (riga vuota uncommitata) → trattata come flag booleano attivo → librespot sopprime TUTTO il log, sembrava avviarsi ma non si registrava nel cloud.
    2. **`LIBRESPOT_DISABLE_CREDENTIAL_CACHE=`** (vuota) → disabilitava la cache delle credenziali → librespot non riusciva ad autenticarsi senza discovery interattiva.
    3. **`LIBRESPOT_BACKEND`** non configurato → usava ALSA direttamente → conflitto con PipeWire che gestisce il device ReSpeaker.
    4. **`LIBRESPOT_CACHE`** non configurato → librespot non trovava le credenziali salvate.
- **Soluzione**:
    1. Commentare tutte le righe con valore vuoto (es. `LIBRESPOT_QUIET=` → `#LIBRESPOT_QUIET=`).
    2. Impostare `LIBRESPOT_CACHE=/home/robopy/.cache/librespot`.
    3. Impostare `LIBRESPOT_BACKEND=alsa` e `LIBRESPOT_DEVICE=hw:0,0`.
    4. Impostare `LIBRESPOT_NAME=Marcus`.
    5. **La prima volta** dopo il riavvio, l'app Spotify mobile (stessa rete WiFi) deve selezionare "raspotify (marcus)" come device — questo lo registra nel cloud. Da quel momento l'API Web lo vede.
- **Lezione**: In raspotify, le variabili d'ambiente **senza valore** (es. `FLAG=`) sono interpretate come flag booleani ATTIVI. Usare `#FLAG=` per disabilitarle. Verificare sempre con `journalctl -u raspotify` che librespot stampi i log di avvio (autenticazione, discovery). Se il journal è vuoto dopo l'avvio → `LIBRESPOT_QUIET=` è attivo.
- **Nota**: Raspotify non va usato con `LIBRESPOT_DISABLE_DISCOVERY=` (vuoto) perché non si può pre-autenticare senza l'app. La discovery ZeroConf è necessaria almeno una volta per sessione.

## Bug: Raspotify Non Emette Audio — Device ALSA Bloccato dal VUI (v11.3, Maggio 2026)

- **Problema**: `sp.start_playback(device_id=raspotify_marcus)` ritorna SUCCESS, Spotify API dice `is_playing=True`, ma nessun suono esce dallo speaker. Il sink PipeWire resta `SUSPENDED`.
- **Causa**: Il VUI node (`respeaker_vui_node.py`) apre il device ALSA `/dev/snd/pcmC0D0p` in **esclusiva** tramite `self.pa.open(output=True)` al boot e lo tiene aperto per tutta la durata del processo. PipeWire non può aprire lo stesso device per librespot → l'audio finisce nel vuoto.
- **Verifica**: `lsof /dev/snd/pcmC0D0p` mostra `python3` (VUI) con il file descriptor aperto. `pactl list sinks short` mostra `SUSPENDED` anche durante la riproduzione.
- **Risoluzione (v11.3)**: Modificata `_get_device_id()` in `spotify_skill.py` per **escludere completamente** i device `raspotify`/`librespot` dalla selezione. La musica viene sempre instradata su device esterni (telefono, PC, speaker smart) tramite `transfer_playback`. Se l'unico device disponibile è raspotify, la skill ritorna un errore chiaro chiedendo di aprire Spotify sul telefono.
- **Lezione**: Su un sistema dove il VUI monopolizza il device ALSA, **qualsiasi altro processo che tenti di usare lo stesso device (incluso PipeWire/raspotify) fallirà silenziosamente**. Non basta avere PipeWire come mixer — se PyAudio apre il device hardware direttamente, il lock è esclusivo. La soluzione architetturale futura è far sì che il VUI usi PipeWire come backend PyAudio (non ALSA diretto), oppure usare un secondo device audio USB dedicato a raspotify.




## Routing LLM Tool Calling

### Gemini Confonde terminal_skill con spotify_skill (v5.x, Maggio 2026)
- **Problema**: Quando l'utente chiedeva "suona metallica su spotify", Marcus rispondeva "Errore: File spotify_skill.py non trovato."
- **Causa**: Gemini vedeva la `terminal_skill` con azione `run_existing` + parametro `filename` e la usava per "eseguire" `spotify_skill.py` come se fosse un file script. Questo generava il messaggio `f"File {filename} non trovato."` dalla `terminal_skill._run_script()` (linea 233).
- **Root cause**: Tre problemi concomitanti:
    1. Il prompt di sistema non vietava esplicitamente l'uso di `terminal_skill` per musica.
    2. La `ha_skill.py` aveva `musica|spotify` nei suoi pattern regex `media_player`, intercettando le richieste musicali come fallback domotico.
    3. Lo schema di `spotify_skill` usava `enum` che l'SDK GenAI non passava correttamente, facendo cadere Gemini sui tool sbagliati.
- **Risoluzione**:
    1. Aggiunta regola esplicita nel prompt: "NON usare MAI terminal_skill per musica/Spotify".
    2. Rimossi `musica|spotify` dai pattern regex di `ha_skill.py`.
    3. Sostituito `enum` con descrizione testuale nello schema di `spotify_skill`.
    4. Aggiunto parametro `speak` a tutti i `SkillResult` di Spotify per garantire feedback vocale.
- **Lezione**: Quando si aggiungono nuove skill con parametri generici (es. `filename`), il modello LLM potrebbe "inventarsi" usi impropri. Ogni skill deve avere regole di routing esplicite nel prompt di sistema.

### Fallimento Silenzioso Skill Caricate Dinamicamente (v5.x, Maggio 2026)
- **Problema**: `skill_registry.py` stampava "Caricate 0 skill attive dal manifest" e skill come `SpotifySkill` non venivano caricate o eseguite.
- **Causa**: La funzione `_load_skill_file` utilizzava `issubclass(obj, BaseSkill)` per identificare le classi che ereditavano da `BaseSkill`. Quando il modulo principale aggiungeva `PKG_BASE` al `sys.path`, Python importava `BaseSkill` in due namespace diversi: `robopy_controller.robot_ai.skills.base_skill` (nell'orchestratore) e `robot_ai.skills.base_skill` (dentro il file `spotify_skill.py` caricato con reflection e `spec_from_file_location`). Di conseguenza, l'oggetto in memoria non corrispondeva alla stessa istanza della classe base, facendo fallire `issubclass()`.
- **Risoluzione**: 
    1. Sostituito il controllo rigido di tipo con una verifica duck-typed usando `__mro__` (`'BaseSkill' in [base.__name__ for base in getattr(obj, '__mro__', [])]`).
    2. Modificato `_load_skill_file` per usare `inspect.getmembers()` con l'import corretto invece di `dir()` e `getattr()`.
- **Lezione**: In un'architettura ROS 2 o plugin-based dove i percorsi `sys.path` sono manipolati dinamicamente, MAI usare controlli rigorosi sull'identità delle classi (`isinstance`, `issubclass`) per moduli caricati dinamicamente da file system. Valutare invece i nomi nel Metaclass Resolution Order (`__mro__`).

## Strategia Modelli LLM: Migrazione da Free a Billing (Maggio 2026)

- **Scenario Piano Gratuito (Legacy)**:
    - **Modelli**: `gemini-1.5-flash` per Standard e Live.
    - **Pro**: Gratuito.
    - **Contro**: Limiti RPM/TPM stretti (15 RPM); latenze variabili; rischio di "OFFLINE" se le richieste si accumulano (es. VAD trigger o polling email).
    - **Lezione**: Con il piano gratuito, Marcus deve avere una gestione errori molto tollerante ("OFFLINE fallback") perché il 429 è frequente.

- **Scenario Fatturazione Attiva (Corrente)**:
    - **Modelli Standard**: **`gemini-2.0-flash`**.
    - **Modelli Live**: **`gemini-2.4-flash-native-audio-latest`** (o 2.0-flash).
    - **Pro**: Quota **Unlimited RPM** sui modelli Flash; latenza drasticamente ridotta (<2s per text input); stabilità dei tool-call.
    - **Cambiamenti apportati**:
        1. Passaggio a `gemini-2.0-flash` come default: più stabile delle versioni "lite-preview" e più intelligente della 1.5.
        2. Aumento timeout RAG a 5.0s (il Pi 5 può rallentare sotto carico).
        3. Aumento timeout Standard LLM a 30s per evitare fallback prematuri su modelli obsoleti.
- **Lezione**: Con la fatturazione attiva, lo stato "OFFLINE" di Marcus (basato su errori consecutivi) deve essere meno aggressivo, poiché i fallimenti API sono rari e solitamente dovuti a rete/timeout locali piuttosto che a limiti di quota di Google.


## 4. LlamaIndex & ChromaDB (RAG e Nightly Dream)
- **Problema:** L'esecuzione della skill "Nightly Dream" causava il crash dell'orchestratore con l'errore `'LlamaIndexMemoryStore' object has no attribute 'get_recent'`.
- **Causa:** Durante la migrazione da un `MemoryStore` base a `LlamaIndexMemoryStore`, ci si è affidati esclusivamente al `VectorStoreIndex` di LlamaIndex per la ricerca semantica (tramite `as_retriever`), omettendo i metodi ausiliari (come `get_recent`) che il resto del sistema utilizzava per iterare cronologicamente sulle memorie.
- **Risoluzione:** Il metodo `get_recent` è stato reimplementato all'interno di `LlamaIndexMemoryStore` interrogando direttamente il layer sottostante (`self.chroma_collection.get(...)`) aggirando l'interfaccia di LlamaIndex per quelle operazioni (come l'estrazione temporale) in cui i vector database crudi sono più adatti ed efficienti.

## 5. Gemini Live API (bidiGenerateContent) e le Versioni dei Modelli
- **Problema:** L'orchestratore riceveva errore `1008 None. models/gemini-2.5-flash is not found for API version v1beta, or is not supported for bidiGenerateContent`.
- **Causa:** La connessione WebSocket bidirezionale (Live API) di Gemini è altamente restrittiva e supporta solo specifici modelli approvati (attualmente `gemini-2.0-flash` o `gemini-2.0-flash-exp`). Tentare di usare versioni puramente testuali o versioni future all'endpoint Live genererà un errore di connessione WebSocket inesorabile.
- **Risoluzione:** 
  1. Configurare **due parametri distinti** nel nodo ROS: `model_name` (per operazioni testuali/RAG) e `live_model_name` (forzato a modelli testati e compatibili col Live streaming bidirezionale).
  2. Assicurarsi sempre che le modifiche ai file Python in un workspace ROS 2 (`src/`) vengano propagate al runtime (es. eseguendo `colcon build`) in quanto i file vengono copiati nella cartella `install/`.

## 6. Native Tool Calling al posto del Parsing JSON Regex
- **Problema:** Costruire prompt complessi forzando l'LLM a generare output JSON (es. blocchi testuali ```json ... ```) per invocare i Tool è intrinsecamente fragile e induce latenza (necessita la generazione dell'intero schema stringa).
- **Risoluzione:** Eliminata la dipendenza dal regex e JSON parser manuale in favore della funzionalità nativa dell'SDK "Function Calling". Passando l'array Open API `functions` al `GenerateContentConfig`, l'SDK esegue nativamente il tool routing popolandolo nel field `function_call`, incrementando enormemente l'affidabilità dell'esecuzione e restituendo testo pulito per l'assistente vocale.

## 7. Conflitto Risorse Audio (ALSA "Device or resource busy")
- **Problema:** Marcus andava in crash con l'errore `Errno -9999 Unanticipated host error` o `Device or resource busy` al primo tentativo di pronunciare una frase via TTS.
- **Causa:** Il nodo hardware `respeaker_vui_node` detiene giustamente un lock esclusivo sul dispositivo ALSA (es. `hw:0,0`) per microfono e speaker (UAC/I2S). Tuttavia, il servizio interno `tts_service.py` tentava di istanziare un PROPRIO stream audio diretto via `pygame.mixer` o `pyaudio`, andando in collisione con il lock del VUI Node.
- **Risoluzione:** Disaccoppiamento totale dell'hardware in architettura Pub/Sub:
  1. `tts_service` (se caricato dentro l'orchestratore ROS) ora ignora `pygame`/`pyaudio`.
  2. Richiede a Google TTS audio PCM `LINEAR16` a 24000Hz crudo (invece di `MP3`).
  3. Pubblica i chunk grezzi direttamente sul topic ROS `/respeaker/speaker_audio`.
  4. L'hardware node (`respeaker_vui_node`) ascolta il topic e sversa il PCM nel driver ALSA, che controlla in esclusiva in totale sicurezza.

## 8. Autenticazione Google Cloud TTS via API Key
- **Problema:** Errore "Your default credentials were not found" durante la sintesi vocale TTS.
- **Causa:** Le librerie `google-cloud-*` (come `google-cloud-texttospeech`) cercano di default il file JSON definito in `GOOGLE_APPLICATION_CREDENTIALS` (Service Account), bloccandosi se non esiste, ignorando l'environment di Gemini.
- **Risoluzione:** Inizializzato `TextToSpeechClient` forzando un'istanza esplicita di `ClientOptions(api_key=...)` alimentata dalla stessa `GEMINI_API_KEY` (configurata in `.env`). Questo consente di usare i servizi GCP di base senza dover esportare chiavi Service Account JSON, riducendo la frizione di setup del robot.

## 9. Unificazione Turn-Taking e Barge-in (Gemini Live vs TTS Classico) (v12.0)
- **Problema**: Marcus parlava tramite Gemini Live ma smetteva di rispondere e non rilevava la wake-word, oppure il VAD continuava ad aprirsi sui suoi stessi output in loop, bloccando la conversazione.
- **Causa**: La protezione del microfono (drop di `stt_gain` a 0.1x per evitare l'eco) dipendeva ESCLUSIVAMENTE dal topic ROS `/ai/tts/speaking` (`_is_tts_speaking`). Le risposte audio provenienti dalla Live API venivano iniettate direttamente nel buffer (`_is_playing_out=True`), bypassando l'aggiornamento del topic. Il microfono restava così al 100% della sensibilità, ascoltando l'audio in uscita e innescando loop.
- **Risoluzione (v12.0)**: In `respeaker_vui_node.py` lo stato è stato unificato creando la variabile `ai_speaking_now = self._is_tts_speaking or self._is_playing_out`. Nel loop audio è stato aggiunto `tts_now = ai_speaking_now` per garantire che la soppressione hardware (guadagno ridotto) si attivi correttamente per entrambe le fonti vocali.
- **Lezione (Regola Permanente)**: Qualsiasi sorgente che produce output audio dal robot (TTS offline o stream Live) DEVE triggerare le protezioni VAD nel nodo VUI unificando lo stato. Non dipendere unicamente dai topic ROS se l'audio è inserito anche tramite playback diretto I/O.

## 10. ReSpeaker Lite WS2812 LED non Funzionante (Maggio 2026)
- **Problema**: Il microfono e l'audio funzionano correttamente, ma l'addressable LED RGB WS2812 a bordo del ReSpeaker Lite non si accende mai per fornire feedback visivo degli stati (Listening, Thinking, IDLE).
- **Causa**: Nelle precedenti configurazioni di ESPHome (`respeaker.yaml` e `respeaker_lite_firmware.yaml`), il LED era mappato su `GPIO21`. Tuttavia, sul modulo Seeed Studio XIAO ESP32S3 integrato nella scheda ReSpeaker 2-Mic Lite, il pin fisico collegato al chipset WS2812 è **GPIO1**.
- **Soluzione di Sicurezza (Zero Regressioni)**:
  1. Per evitare qualsiasi rischio di regressione del microfono I2S/DMA (che causerebbe il blocco dei dati a 32743), si è scelto di mantenere intatto il firmware originale `respeaker_lite_firmware.yaml`.
  2. È stata creata una copia isolata denominata `respeaker_lite_firmware_led_v13.yaml` contenente la modifica del pin a `GPIO1`:
     ```yaml
     light:
       - platform: esp32_rmt_led_strip
         chipset: WS2812
         rgb_order: GRB
         pin: GPIO1
         num_leds: 1
         id: respeaker_led
         name: "ReSpeaker LED"
         restore_mode: ALWAYS_OFF
     ```
- **Istruzioni per la Compilazione (WSL locale)**:
  ```bash
  cd "/mnt/c/Users/lsuffia/OneDrive - BRUGOLA OEB INDUSTRIALE SPA/Documents/robopy/antigravity"
  esphome compile robopy_controller/files_utili/respeaker_lite_firmware_led_v13.yaml
  ```
- **Istruzioni per il Flashing (SSH sul Pi)**:
  1. Da **WSL locale**, copia il file binario compilato sul Raspberry Pi:
     ```bash
     scp .esphome/build/respeaker-lite/.pioenvs/respeaker-lite/firmware.factory.bin robopy@marcus:/tmp/firmware_led_v13.factory.bin
     ```
  2. Da **SSH sul Pi**, esegui il flash:
     ```bash
     source /home/robopy/esphome_venv/bin/activate
     esptool --port /dev/ttyACM0 --baud 115200 write_flash 0x0 /tmp/firmware_led_v13.factory.bin
     ```
- **Istruzioni di Ripristino (Rollback Immediato in caso di problemi al microfono)**:
  In caso di freeze o malfunzionamento del microfono I2S, ripristina all'istante l'ultimo firmware funzionante compilando e flashando `respeaker_lite_firmware.yaml`:
  1. Da **WSL locale**, ricompila il vecchio firmware:
     ```bash
     esphome compile robopy_controller/files_utili/respeaker_lite_firmware.yaml
     scp .esphome/build/respeaker-lite/.pioenvs/respeaker-lite/firmware.factory.bin robopy@marcus:/tmp/firmware_safe.factory.bin
     ```
  2. Da **SSH sul Pi**, esegui il flash del vecchio firmware:
     ```bash
     source /home/robopy/esphome_venv/bin/activate
     esptool --port /dev/ttyACM0 --baud 115200 write_flash 0x0 /tmp/firmware_safe.factory.bin
     ```

## Bug: Marcus non Risponde al Primo Turno e Secondo Beep Troppo Ravvicinato (v14.1, Maggio 2026)
- **Problema**: Dopo il flash del firmware dei LED, Marcus emetteva il primo beep (wake word) e quasi istantaneamente il secondo beep (fine ascolto), senza dare tempo all'utente di parlare. Inoltre, Marcus non rispondeva ai comandi.
- **Cause**:
    1. **Noise gate adattivo minimo a 1200**: In ambiente silenzioso, con un segnale boosted in idle di ~100-150, la soglia minima a 1200 impediva al VAD di percepire la voce umana debole/normale.
    2. **Silence timeout adattivo a 440ms (22 frames)**: Troppo breve in ambiente silenzioso.
    3. **Gating logic in `llm_live_api.py`**: Il primo turno assoluto della conversazione veniva silenziato perché `_last_successful_turn_time = 0.0` faceva fallire la finestra di conversazione attiva, e il wake word `/wake_word` non era sottoscritto da `llm_service.py`, impedendo a `_last_wakeword_time` di essere aggiornato.
- **Risoluzione (v14.1)**:
    1. Abbassata la soglia di noise gate minima da 1200 a 300 in `respeaker_vui_node.py`.
    2. Aumentato il silence timeout minimo da 22 a 40 frames (~800ms) in ambiente silenzioso.
    3. Sottoscritto il topic `/wake_word` in `llm_service.py` per aggiornare `self._last_wakeword_time = time.time()`.
    4. Aggiornata la logica di gating in `llm_live_api.py` per abilitare la risposta se il wake word è stato rilevato negli ultimi 60s o se la conversazione era attiva negli ultimi 30s.
    5. Integrati i LED per mostrare `THINKING` (blu flicker) alla fine del parlato e `SUCCESS` (verde fisso) allo start del TTS.

## Bug Critico ed Evoluzione Emotiva: Marcus AI v14.2 (Anima Robotica - Maggio 2026)

- **Scenario e Obiettivo**: Introduzione di una sincronizzazione dinamica visiva LED basata sull'umore cognitivo ed emozionale elaborato dal LLM (Gemini 2.5 Flash), espandendo il firmware ESPHome con 4 stati emotivi (`HAPPY` giallo oro, `TIRED` viola indaco, `APOLOGETIC` arancione, `LONELY` turchese).
- **Lezioni Apprese sull'Ambiente di Compilazione ESPHome & PlatformIO**:
    1. **Version Checking Mismatch in ESP-IDF v5.5.2**: ESP-IDF solleva un errore `FATAL_ERROR` se la versione della toolchain locale (`toolchain-xtensa-esp-elf` v14.2.0+20251107 scaricata da PlatformIO) differisce da quella dichiarata nel manifest `tools.json` (`esp-14.2.0_20260121`).
       * **Risoluzione**: Abbiamo scoperto che esportando `IDF_MAINTAINER=1` nel compilatore, ESP-IDF declassa il mismatch a warning non-blocking, permettendo la compilazione fluida del firmware.
    2. **Isolamento SCons e PEP 668 (Virtualenv Sandbox)**: Nelle distribuzioni Linux moderne (es. Ubuntu 24.04+ in WSL), SCons isola i sottoprocessi ed esegue l'interprete di sistema `/usr/bin/python3` che non ha accesso all'ambiente virtuale python locale, fallendo l'import della libreria `platformio` a causa del blocco PEP 668.
       * **Risoluzione**: Creare un link `.pth` all'interno della directory site-packages dell'utente (`~/.local/lib/python3.12/site-packages/esphome_venv.pth`) che punta direttamente al path site-packages dell'ambiente virtuale (`esphome_venv`). In questo modo, l'interprete di sistema eredita le dipendenze in modo pulito e non intrusivo.
    3. **Bootstrapping di .espidf-5.5.2**: L'ambiente virtuale interno autogenerato per ESP-IDF v5.5.2 può nascere vuoto, causando errori come `ModuleNotFoundError: No module named 'kconfgen'`.
       * **Risoluzione**: Installare forzatamente il pacchetto `esp-idf-kconfig` (e le altre 59 dipendenze) utilizzando `uv` e puntando esplicitamente all'interprete interno: `uv pip install --python /home/robopy/.platformio/penv/.espidf-5.5.2/bin/python -r requirements.core.txt`.
- **Lezioni Apprese sul Flashing e Conflitti sulla porta Seriale JTAG**:
    - **Blocco Seriale su /dev/ttyACM0**: Qualsiasi tentativo di flashare il firmware sul ReSpeaker tramite `esptool` fallirà bruscamente con `A fatal error occurred: The chip stopped responding. StopIteration` o simili se il device seriale USB è monopolizzato in lettura/scrittura da un altro nodo ROS in background (come `respeaker_interface_node` o `respeaker_vui_node`).
    - **Procedura di Sicurezza obbligatoria**: Prima di avviare il flashing, è **tassativo** arrestare tutti i nodi concorrenti con un segnale di terminazione forzata:
      ```bash
      pkill -9 -f respeaker_interface_node || true
      pkill -9 -f respeaker_vui_node || true
      pkill -9 -f robot_ai_node || true
      ```
      Una volta completato il flashing, è possibile rieseguire `restart.sh` in totale sicurezza.

## Rimozione LlamaIndex, ChromaDB Nativo e Watchdog Cognitivo: Marcus AI v16.0 (AI_ver3 - Maggio 2026)

- **Scenario e Obiettivo**: Migrazione completa del sistema RAG eliminando LlamaIndex per implementare una connessione nativa ultra-efficiente con ChromaDB (`ChromaNativeStore`), evitando crash asincroni e overhead, e introducendo un watchdog cognitivo per la resilienza automatica del robot con rollback A/B.
- **Lezioni Apprese su LlamaIndex vs ChromaDB Nativo**:
    1. **Conflitto di Metadati e Schema (object of type 'int' has no len())**: La transizione da LlamaIndex (che scrive propri metadati specifici nel DB) a un'integrazione ChromaDB nativa causa il fallimento dell'inizializzazione con l'eccezione `❌ Inizializzazione ChromaNativeStore fallita: object of type 'int' has no len()`. Questo accade perché ChromaDB non riesce a fondere o leggere lo schema ereditato da LlamaIndex nella stessa cartella di persistenza.
       * *Risoluzione*: Eseguire il backup e la rinomina della directory del vecchio database (`mv /home/robopy/ChromaDB_Llama /home/robopy/ChromaDB_Llama_backup`) per consentire a ChromaDB nativo di inizializzare una collezione pulita con lo schema aggiornato.
    2. **Thread-Safety nel Multi-Threaded Executor**: Poiché il nodo AI gira in un ROS 2 `MultiThreadedExecutor`, il client di ChromaDB nativo deve essere un singleton thread-safe gestito da un lock globale (`threading.Lock`), ed ogni operazione di scrittura/lettura della collezione deve essere protetta da lock rientranti (`self._lock = threading.RLock()`).
    3. **Prevenzione Corruzione Spazio Vettoriale**: Per evitare crash o risposte incoerenti del database vettoriale, è fondamentale validare la dimensione di ogni embedding prima di eseguire `add()`, scartando sul nascere qualsiasi record con dimensione diversa da 768.
- **Lezioni Apprese sulla Live API e Gestione Code Audio (GIL)**:
    1. **Mitigazione Contesa del GIL**: La migrazione da `LiveAPIMixin` a un gestore composto `LiveConnectionManager` richiede code asincrone FIFO per gestire i pacchetti audio PCM. Impostare una dimensione massima (`maxsize=50`) con logica oldest-drop (scarto del pacchetto più vecchio) previene i colli di bottiglia indotti dal Global Interpreter Lock (GIL) sul Raspberry Pi 5 e mantiene fluida la conversazione.
- **Lezioni Apprese sulla Resilienza e il Watchdog Cognitivo**:
    1. **Rollback A/B via Symlink**: Il watchdog bash (`watchdog.sh`) fornisce un meccanismo di emergenza A/B: se rileva 3 crash del nodo AI in 60 secondi, esegue lo swap istantaneo del symlink di produzione (`install` puntato a `/home/robopy/robopy/install_v15` stabile), zittisce l'audio e riavvia i nodi, garantendo la continuità operativa del robot senza intervento manuale.
    2. **Integrazione Systemd**: Registrare il watchdog come servizio systemd (`marcus-watchdog.service`) garantisce che il monitoraggio di Marcus sia avviato automaticamente all'accensione del Raspberry Pi e sia in grado di riavviarsi in caso di guasto.

## Accesso Audio Diretto a ReSpeaker e Risoluzione Conflitti Watchdog (v16.1, Maggio 2026)

- **Prioritarizzazione Dispositivi Audio (ReSpeaker Direct Access)**:
  - *Problema*: Il microfono in idle presentava un volume incredibilmente basso (`L_RMS ~40`), rendendo insensibile il VAD adattivo di Gemini che ignorava sistematicamente ogni frase con `🔇 [Live] Turno ignorato: rilevato solo silenzio o rumore ('')`.
  - *Causa*: La logica originaria di `_find_audio_devices()` cercava prioritariamente stringhe come `"pulse"`, `"default"`, o `"pipewire"` per facilitare il multiplexing. Tuttavia, la presenza del plug-in ALSA `sysdefault` portava il nodo ad auto-configurarsi su `Index 1: sysdefault` anziché sul reale hardware `Index 0: ReSpeaker Lite`. Questo causava un bypass dannoso dell'acquisizione pura, con perdita di guadagno e dinamica.
  - *Perché prima funzionava?*: Prima dell'implementazione completa di PipeWire, il sistema audio non aveva il demone PipeWire/PulseAudio in esecuzione costante come servizio utente systemd per l'utente `robopy`. Di conseguenza, all'interrogazione PyAudio, i dispositivi virtuali `"pulse"` o `"default"` non avevano canali di input attivi (`n_in == 0`), per cui la prima ricerca condizionale falliva. Il codice eseguiva quindi il fallback sul secondo ciclo, agganciando correttamente e direttamente la stringa `"ReSpeaker"` (`Idx=0`). Con le recenti attivazioni stabili dei servizi utente systemd di PipeWire, i plug-in virtuali hanno esposto canali di cattura attivi (`n_in > 0`), facendo scattare la prima condizione e agganciando stabilmente il dispositivo virtuale `sysdefault` (Idx=1), che tuttavia è configurato nel sistema operativo con livelli di gain di cattura drasticamente abbattuti o con routing errato.
  - *Risoluzione*: Invertita la priorità in `respeaker_vui_node.py` per cercare ed agganciare anzitutto la stringa `device_name_target` (es. "ReSpeaker"). In questo modo la cattura è vincolata al reale hardware `hw:0,0` (Idx=0), garantendo una calibrazione eccellente (`Ambient_EMA ~55.7`, `Gate ~1821.3`) che lascia ampio margine al parlato (~3000+ RMS) per attivare all'istante l'ascolto.
- **Risoluzione dei Conflitti di Riavvio ed Interlock (Watchdog Race Condition)**:
  - *Problema*: Quando si lanciava un riavvio manuale via `/mnt/ssd/robopy_controller_host/restart.sh`, il demone `marcus-watchdog.service` percepiva la temporanea uccisione del nodo `robot_ai_node` come un crash improvviso e scatenava un secondo riavvio concorrente. Questa sovrapposizione generava crash DDS catastrofici (`rcl node's context is invalid`) e fallimenti di occupazione del bus seriale USB e dell'audio.
  - *Risoluzione*: Implementato un meccanismo di interlock a livello di variabili d'ambiente.
    1. In `watchdog.sh`, tutte le chiamate a `restart.sh` sono state marcate con `FROM_WATCHDOG=1`.
    2. In `restart.sh`, si verifica la presenza di tale variabile. Se è vuota (riavvio manuale dell'operatore), lo script provvede anzitutto ad arrestare in modo pulito il watchdog via systemd (`sudo systemctl stop marcus-watchdog.service`), uccide i nodi, riavvia lo stack e riattiva il watchdog al termine. Se invece `FROM_WATCHDOG=1`, salta la chiamata a systemctl per evitare la ricorsione e il deadlock della cgroup systemd.


