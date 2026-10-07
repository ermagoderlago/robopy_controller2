# Progetto di Miglioramento: Gating Conversazionale ad ActivityStart vs Trappola degli 8 Secondi (IMP-VUI-035b)

## 📌 Identificazione
- **ID Progetto:** `IMP-VUI-035b`
- **Failure Mode Correlato:** `FM-VUI-035b`
- **Dominio:** `audio_vui`
- **Componenti Interessati:** `live_connection_manager.py`, `respeaker_vui_node.py`
- **Stato:** `COMPLETED`

---

## 🎯 Obiettivo
Eliminare il fenomeno della caduta silenziosa del parlato dell'utente durante conversazioni naturali ("a volte non risponde"), impedendo che frasi pronunciate legittimamente entro la finestra conversazionale vengano scartate alla ricezione dell'End-Of-Speech (EOS).

---

## 🔍 Causa Radice
Nel commit precedente (FM-VUI-035), per evitare risposte indesiderate alla TV o dialoghi ambientali, il timeout della sessione attiva era stato ristretto a 8.0s. Tuttavia, il controllo `is_active` veniva eseguito all'EOS (fine frase):
```
Tempo totale = Pausa di riflessione (1.5s) + Parlato utente (5.5s) + Tolleranza silenzio VAD (0.9s) = 7.9s - 8.2s
```
Se la somma superava 8.0s, il turno veniva scartato come fuori finestra e veniva invocato `_reconnect()` sul WebSocket, distruggendo la connessione.

---

## 🛠️ Mitigazione Implementata (FIX-01, FIX-04)
1. **ActivityStart Evaluation:** La validità temporale della finestra viene valutata quando l'utente comincia a parlare (`activity_start`) e salvata nel flag atomico `self._turn_started_within_window`.
2. **EOS Check:** Alla ricezione dell'EOS viene verificato il flag pre-calcolato. Se il turno è iniziato entro la finestra attiva, viene sempre inviato `activity_end` a Gemini Live indipendentemente dalla durata della frase.
3. **Non-destructive Skip:** Se un frame audio o turno è fuori finestra, viene scartato senza distruggere il socket (`_reconnect()` non viene chiamato).
4. **Wake Word Re-Trigger:** In `respeaker_vui_node.py`, se l'utente pronuncia nuovamente "Marcus" durante una sessione attiva (`already_listening == True`), viene estesa la finestra temporale (`_start_listen_timer()`) anziché ignorare la chiamata.
