# 🛠️ Progetto di Miglioramento: IMP-VUI-032

## WebSocket Receive Watchdog e Turn Inactivity Reconnect in LiveConnectionManager

- **ID Failure Correlato:** `FM-VUI-032`
- **Componente:** `robopy_controller.robot_ai.services.live_connection_manager` (`live_connection_manager.py`)
- **Stato:** `COMPLETED`
- **Data Attivazione:** 2026-09-26
- **Ambito:** Cognitive AI, Gemini Live API, WebSocket Resilience

---

### 1. Descrizione del Problema
In `robot_ai_node`, quando un turno vocale viene completato lato client con `activity_end`, il manager si pone in attesa di messaggi di risposta (`session.receive()`) dal cloud.
Se il server di Google Cloud interrompe silenziosamente il flusso senza inviare `turn_complete` (es. per timeout di inattività server-side, perdita di pacchetti TCP o socket chiuso unilateralmente in CLOSE_WAIT), il costrutto `async for msg in session.receive():` si blocca indefinitamente senza mai sollevare eccezioni.
Il processo `robot_ai_node` entra in un loop asincrono che consuma fino al 90% di CPU su un core, congelando l'intero stack cognitivo per ore e impedendo qualsiasi ulteriore dialogo o comprensione dei comandi.

### 2. Analisi delle Cause Radice
1. **Iterazione Illimitata su `session.receive()`:**
   L'iterazione asincrona non imponeva alcun timeout per-message né un limite massimo di attesa risposta tra `activity_end` e `turn_complete`.
2. **Assenza di Socket Teardown Proattivo:**
   Anche se un watchdog interno resettava il flag `_turn_in_progress = False`, la coroutine che consumava `session.receive()` rimaneva orfana e sospesa sulla sessione morta, impedendo al ciclo di connessione di avviare `_reconnect()`.

### 3. Architettura della Soluzione (Mitigazione Implementata)
1. **Consumo a Timeout con `__anext__()` e `asyncio.wait_for`:**
   Sostituito il ciclo `async for` grezzo con un iteratore asincrono esplicito:
   ```python
   receive_iter = session.receive().__aiter__()
   while self._live_session == session:
       timeout = 15.0 if self._turn_in_progress else 60.0
       try:
           msg = await asyncio.wait_for(receive_iter.__anext__(), timeout=timeout)
           await self._handle_live_message(msg)
       except asyncio.TimeoutError:
           if self._turn_in_progress:
               self.logger.warning("⏱️ Turn response timeout (>15s). Resetting turn and reconnecting session...")
               self._turn_in_progress = False
               self._activity_started = False
               break
           else:
               continue
       except StopAsyncIteration:
           break
   ```
2. **Auto-Recovery Automatico:**
   All'interruzione del ciclo `while`, la clausola `finally` chiude la sessione e cancella il sender task, consentendo a `_live_connection_manager_loop` di stabilire una nuova sessione pulita ed efficiente.

### 4. Metriche di Verifica e RPN
- RPN Iniziale: 336 (S:8, O:6, D:7)
- RPN Residuo: 32 (S:8, O:2, D:2)
- Collaudo: Simulazione stallo cloud post-activity_end con verifica di sblocco e riconnessione entro 15.5 secondi.
