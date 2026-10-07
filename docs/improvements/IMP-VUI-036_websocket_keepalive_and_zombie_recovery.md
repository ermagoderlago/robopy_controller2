# Progetto di Miglioramento: WebSocket Keepalive, Inattività Prolungata & Recovery (IMP-VUI-036)

## 📌 Identificazione
- **ID Progetto:** `IMP-VUI-036`
- **Failure Mode Correlato:** `FM-VUI-036`
- **Dominio:** `audio_vui`
- **Componenti Interessati:** `live_connection_manager.py`, `scripts/watchdog.sh`
- **Stato:** `COMPLETED`

---

## 🎯 Obiettivo
Garantire la costante responsività del robot Marcus anche dopo ore di silenzio o inattività prolungata ("dopo molta inattività sembra non riuscire a comprendere/rispondere"), prevenendo la chiusura silente della connessione WebSocket Gemini Live e ripristinando immediatamente l'audio in caso di anomalie.

---

## 🔍 Cause Radici
1. **Half-Open Socket:** Google Cloud e i gateway/router NAT domestici chiudono le sessioni TCP/TLS inattive dopo 10-15 minuti. In assenza di frame applicativi, il client Python non rileva la caduta.
2. **Sender Loop Block:** All'invio del primo chunk PCM post-inattività, `send_realtime_input` sollevava eccezione ma il gestore si limitava a loggare senza forzare il reconnect, congelando il canale per 15s.
3. **Session Token Expiration:** Il token `session_resumption` inviato dal server scadeva lato cloud, bloccando le successive riconnessioni ed esaurendo i tentativi fino al fallback locale Qwen.
4. **VUI Node Liveness:** Se il nodo ROS 2 `respeaker_vui_node` si arrestava per stallo USB ALSA, non esisteva alcuna supervisione nel watchdog host per riavviarlo.

---

## 🛠️ Mitigazioni Implementate (FIX-02, FIX-05, FIX-06, FIX-07)
1. **Periodic Keepalive Ping (FIX-07):** Task asincrono `_keepalive_loop()` attivo ogni 120s. Se il microfono è rimasto inattivo per più di 120s, invia un `LiveClientContent(turns=[], turn_complete=False)` leggero a Google Cloud. In caso di errore socket, scatena immediatamente `_reconnect()`.
2. **Immediate Reconnect on PCM Error (FIX-02):** In `_audio_sender_loop()`, se l'invio audio fallisce, viene immediatamente schedulata la riconnessione `asyncio.create_task(self._reconnect())` e interrotto il loop fallato.
3. **Resumption Token Invalidation (FIX-06):** In caso di errori legati a token scaduto (`expired`, `resume`, `1008`), `self._resumption_token` viene azzerato forzando una connessione pulita senza drop a catena.
4. **Host Watchdog VUI Respawn (FIX-05):** In `scripts/watchdog.sh` è stato integrato il monitoraggio del processo `respeaker_vui_node` con auto-restart deterministico e sourcing dell'ambiente ROS 2.
