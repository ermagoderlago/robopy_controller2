# 🛠️ Progetto di Miglioramento: IMP-VUI-031

## Liveness Watchdog Stream Microfonico e Auto-Recovery Hardware ALSA/PyAudio su USB Disconnect

- **ID Failure Correlato:** `FM-VUI-031`
- **Componente:** `robopy_controller.nodes.respeaker_vui_node` (`respeaker_vui_node.py`)
- **Stato:** `COMPLETED`
- **Data Attivazione:** 2026-09-26
- **Ambito:** Audio VUI, ReSpeaker Lite, ALSA Kernel Driver

---

### 1. Descrizione del Problema
Quando il microfono USB ReSpeaker Lite viene fisicamente scollegato, ricollegato o cambiato di porta (oppure subisce un glitch di alimentazione/ESD sul bus USB del Pi 5), il file descriptor del dispositivo di cattura ALSA (`/dev/snd/pcmC0D0c`) viene invalidato dal kernel Linux.
PyAudio, basato sui binding PortAudio nativi in C, non genera un'eccezione Python e non termina il processo: il suo thread interno entra in un ciclo infinito di timeout:
```text
ppoll([{fd=19, events=POLLIN|POLLERR|POLLNVAL}], 1, {tv_sec=0, tv_nsec=60000000}, NULL, 0) = 0 (Timeout)
```
La callback `_audio_input_callback` smette di essere invocata. Il nodo VUI rimane formalmente in esecuzione ma diventa completamente sordo: nessun dato audio raggiunge più Vosk né Gemini Live.

### 2. Analisi delle Cause Radice
1. **Assenza di Watchdog di Flusso:** `respeaker_vui_node` non teneva traccia dell'intervallo trascorso tra un chunk audio in ingresso e il successivo.
2. **Nessun Meccanismo di Riconnessione Hardware ALSA a Caldo:** L'istanza di `pyaudio.PyAudio()` e lo stream di ingresso venivano aperti una sola volta all'avvio nel costruttore `__init__`. Se il device ALSA cadeva, non veniva effettuato alcun teardown o re-discovery.

### 3. Architettura della Soluzione (Mitigazione Implementata)
1. **Timestamping dei Frame in Ingresso:**
   In `_audio_input_callback(in_data, ...)` viene aggiornato atomicamente `self._last_input_chunk_time = time.monotonic()`.
2. **Watchdog Timer a 1.0 Hz (`_audio_stream_watchdog`):**
   Un timer ROS 2 controlla se `time.monotonic() - self._last_input_chunk_time > 3.0` secondi. In caso di stallo:
   - Registra errore critico nel log.
   - Avvia un thread non-bloccante di auto-recovery hardware (`_recover_audio_stream`).
3. **Auto-Recovery Deterministico Hardware:**
   - Esegue la chiusura sicura di `self.in_stream`.
   - Distrugge e re-istanzia `self.pa = pyaudio.PyAudio()`.
   - Esegue `_find_audio_devices()` per rilevare la nuova scheda ALSA assegnata dal kernel.
   - Riapre e riavvia `self.in_stream` a 16kHz mono/stereo.
   - Se il dispositivo non è ancora presente sul bus, ritenta periodicamente fino al ripristino o al fail-safe.

### 4. Metriche di Verifica e RPN
- RPN Iniziale: 448 (S:8, O:7, D:8)
- RPN Residuo: 32 (S:8, O:2, D:2)
- Collaudo: Disconnessione e riconnessione del cavo USB con ripristino automatico dello stream entro 3.5 secondi senza intervento manuale.
