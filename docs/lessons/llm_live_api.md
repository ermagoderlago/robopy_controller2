# Lezioni Apprese - LLM & Live API (bidiGenerateContent)

Questo documento documenta la gestione del bidi-streaming vocale, la latenza dei WebSocket e la stabilità di rete per la Live API di Gemini.

---

## 🔌 Gestione del WebSocket e Stabilità di Rete

### Turn 1 Closed (Errore 1008)
* **Problema:** La sessione si chiudeva dopo una singola risposta.
* **Causa:** Modelli non supportati per il bidi-streaming all'endpoint beta (es. `gemini-2.5-flash` su endpoint testuale).
* **Risoluzione:** Vincolare la Live API esclusivamente a modelli esplicitamente testati e compatibili con il bidi-streaming vocale, in particolare **Gemini 2.5 Flash Native Audio** (`gemini-2.5-flash-native-audio-latest`).

### Errore 'language_codes'
* **Problema:** Connessione fallita a causa di parametri non supportati nell'SDK.
* **Causa:** L'SDK `google-genai` (v1.x) rifiuta `language_codes=["it-IT"]` dentro `AudioTranscriptionConfig`.
* **Risoluzione:** Rimuovere il parametro lasciandolo vuoto. Gemini rileva automaticamente la lingua parlata e risponde in italiano.

### Prevenzione dei Blocchi Zombie su WebSocket (try...finally)
* **Problema:** Quando il WebSocket si disconnetteva pulitamente (code 1000), il connection manager rimaneva bloccato credendo la sessione ancora attiva.
* **Risoluzione:** Avvolgere il ciclo di ricezione asincrono in un blocco `try...finally` per azzerare sempre `self._live_session = None` e forzare la riconnessione automatica.

---

## 🗣️ Turn-Taking, Barge-In e Gestione Code Audio

### Auto-Interruzione Post-activity_end
* **Problema:** Marcus riceveva la wake word e iniziava l'elaborazione, ma veniva immediatamente interrotto con `🤫 [Live] Interruzione rilevata dal server!` prima di rispondere.
* **Causa:** Il VUI continuava a inviare audio residuo o rumore che Gemini interpretava come un nuovo `activity_start`, interrompendo l'elaborazione del turno precedente.
* **Risoluzione:** Introdurre un flag `_turn_in_progress`. Impostarlo a `True` dopo `activity_end` per bloccare l'invio di ulteriori `activity_start`. Resettarlo a `False` solo al ricevimento di `turn_complete` o `interrupted`.

### Audio Mixing ("Parole che si mischiano") e Flooding Log (OOM)
* **Problema 1:** Warning a 20Hz: `⚠️ OOM Prevention: Coda PCM piena. Scarto il chunk più vecchio.` se la Live API si disconnetteva ma il microfono continuava a inviare dati.
* **Problema 2:** All'avvio del turno successivo, Gemini riceveva 3 secondi di rumore ambientale accumulati in coda, confondendo la risposta.
* **Risoluzione:**
  1. Aggiungere il controllo `if not self._live_session or self._turn_in_progress: return` all'inizio del callback di enqueueing.
  2. Se non c'è una sessione attiva o se il robot sta elaborando/parlando, i chunk in ingresso dal microfono vengono scartati all'istante, eliminando il flooding dei log e la saturazione della coda con rumore stantio.

---

## 🤖 Scelta Modelli e Strategia Fallback

### Strategia a due modelli
1. **Gemini 2.5 Flash Native Audio Dialog** (`gemini-2.5-flash-native-audio-latest`): **OBBLIGATORIO per AUDIO/LIVE**. Supporta esclusivamente audio.
2. **Gemini 3.1-flash-lite-preview** (o modelli testuali correnti): **PREDEFINITO per CHAT/TESTO/RAG**.
* **Strategia Fallback:** Se il modello primario non risponde entro **20 secondi**, il sistema effettua automaticamente il downgrade a `gemini-1.5-flash` per garantire la continuità di servizio.
* **Billing Cap (Spending Cap):** Se viene superato il cap di spesa su Google AI Studio, la Live API WebSocket restituisce errore 1011 e chiude la connessione. Il gestore di enqueueing deve silenziare i log ed evitare il flooding della CPU scartando i chunk.

---

## 🔧 Validazione Strutturata dei Tool (SDK v1.x)

* **Problema:** Errore `Extra inputs are not permitted` per `tools.0.Tool.name` o `tools.0.callable` dovuto alla validazione Pydantic rigida nell'SDK `google.genai` (v1.x).
* **Risoluzione:** Implementare una funzione helper `_format_tools_for_google` per ripulire i dizionari dei tool custom del sistema Marcus, rimuovendo campi extra (come `callable`) e incapsulandoli in oggetti `types.FunctionDeclaration` dentro `types.Tool(function_declarations=[...])`.

---

## 🎙️ Prevenzione della Sovrapposizione Audio (Double Voice / Double LLM Calls)
* **Problema:** Quando si avviava una sessione vocale, si udivano due voci sovrapposte che rispondevano contemporaneamente (la voce fluida nativa di Gemini Live e la voce robotica standard di gTTS).
* **Causa:** La Live API genera e riproduce autonomamente l'audio tramite il bidi WebSocket. Tuttavia, `conversation.py` riceve anche la trascrizione testuale finale e la passava a `self.tts.speak(...)` generando una seconda istanza di riproduzione vocale locale.
* **Soluzione:** Introdurre un flag `is_live` per tracciare se il turno è stato risposto con successo dalla Live API. Inibire qualsiasi chiamata locale a `self.tts.speak(...)` in `conversation.py` se `is_live` è `True`. Se la Live API fallisce o scatta il fallback standard su REST, `is_live` rimane `False` e la sintesi vocale locale gTTS viene regolarmente utilizzata.
* **Integrazione Client-Side ASR (Doppia Chiamata / Doppia Voce):**
  * **Problema:** Se l'utente usa un client con ASR integrata (es. Foxglove o web app), il client transceve e invia la trascrizione sul topic `/ai/input/text` (con `source="text"`), provocando comunque la doppia risposta (quella nativa Live e quella REST locale).
  * **Soluzione [v15.2]:** Memorizzare le trascrizioni recenti utente della Live API in `LiveConnectionManager.recent_user_transcripts`. In `ConversationManager.process_input`, se arriva un input di testo che corrisponde (o è substring) a una delle trascrizioni Live recenti (finestra di 15 secondi) o in corso, l'input testuale duplicato viene scartato silenziosamente.
  * **Filtro Temporale Anti-Allucinazione ASR [v15.2]:**
    - Se l'ASR di Gemini Live allucina la trascrizione in un'altra lingua (es. italiano trascritto come portoghese a causa di disturbi), il controllo testuale di uguaglianza fallisce.
    - **Soluzione:** Tracciare `_last_mic_audio_time` ad ogni chunk registrato dal microfono fisico del robot. Qualsiasi messaggio testuale (`source="text"`) che giunge entro **3.5 secondi** dall'ultimo input del microfono viene catalogato come ASR client-side concorrente e scartato a prescindere dal testo.
  * **Silenziamento Conversazione da Chat [v15.3]:**
    - Per evitare che Marcus risponda a voce quando l'utente comunica via tastiera/chat, si effettua un wrapping dinamico di `self.tts.speak` in `ConversationManager.__init__`. Se `_current_source == "text"`, il parlato locale gTTS viene soppresso loggando l'evento `[MUTE]`.

---

## 🔊 Ottimizzazione Streaming Audio PCM e Sequenzialità

### Jitter e Out-of-Order sotto MultiThreadedExecutor [v15.3]
* **Problema:** Lo streaming vocale di Gemini Live partiva correttamente, per poi arricchirsi di parole distorte e sovrapposte, rendendo l'audio del robot incomprensibile.
* **Causa:** Il nodo AI e l'orchestratore sono eseguiti all'interno di un `MultiThreadedExecutor` a 4 thread per ragioni di efficienza asincrona. I chunk audio ricevuti da Gemini venivano pubblicati via ROS su `/ai/conversation/audio_chunk`. A causa della concorrenza del pool di thread dell'esecutore, i callback di ricezione venivano eseguiti in parallelo o fuori ordine (es. il chunk 2 veniva spedito sul bus `/respeaker/speaker_audio` prima del chunk 1), provocando sfasamenti e scatti del tasso di campionamento (resampling state corrotto).
* **Risoluzione (Bypass ROS via Callback Diretta):**
  - Eliminare il passaggio intermedio via topic ROS `/ai/conversation/audio_chunk` per lo streaming vocale.
  - Implementare una callback Python sincrona e diretta `register_audio_callback` tra `LLMServiceNode` e `AIOrchestrator`.
  - Poiché il loop di ricezione WebSocket di Gemini gira sul thread a ciclo singolo asincrono di asyncio, la callback diretta inoltra i chunk in ordine cronologico perfetto e sequenziale. La pubblicazione finale su `/respeaker/speaker_audio` avviene così in modo strettamente ordinato, ripristinando la pulizia assoluta dell'audio in cuffia/altoparlante.


