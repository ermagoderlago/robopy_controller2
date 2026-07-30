# IMP-004: Transizione da Porcupine ad Hailo-10H VUI, Voice Print & Memory Decay Engine

## 🎯 Obiettivo del Progetto
Sostituire completamente la dipendenza a pagamento da Picovoice Porcupine con un'infrastruttura VUI 100% locale operante su NPU Hailo-10H, introducendo:
1. **Hailo-10H Continuous Listening & KWS**: Ascolto ambientale continuo e rilevamento della wakeword "Marcus" tramite NPU.
2. **Speaker Voice Print & Enrollment**: Riconoscimento del timbro vocale degli interlocutori (Speaker ID / Embeddings) e codice di associazione dell'impronta vocale alle persone.
3. **Ambient Memory & Memory Decay Engine (Oblio)**: Registrazione del contesto ambientale circostante con algoritmo di oblio per eliminare dati irrilevanti e mantenere solo informazioni salienti.
4. **Context Handoff a Gemini Live API**: Preservazione del comportamento visivo dei LED (identico a prima) ed eredità automatica delle conversazioni/avvenimenti recenti nel contesto WebSocket di Gemini Live quando viene pronunciata la parola "Marcus".

---

## ⚡ Failure Modes Mitigati (DFMEA)
- **FM-VUI-004**: Blocco dell'avvio o eccezione di licenza per Picovoice Porcupine EOL / piano a pagamento.
- **FM-VUI-005**: Latenza ed accumulo buffer audio per estrazione continua di Speaker Embeddings su Hailo-10H.
- **FM-VUI-006**: Errata identificazione dell'impronta vocale (False Speaker Match).
- **FM-VUI-007**: Oblio aggressivo di informazioni importanti nel buffer di memoria ambientale.
- **FM-VUI-008**: Conflitto di I/O audio durante l'handoff tra ascolto passivo Hailo e streaming attivo Gemini Live.

---

## 🏗️ Architettura del Modulo e Componenti
1. `hailo_vui_node.py` / `hailo_kws_engine.py`: Gestione dell'ascolto continuo, filtro HPF 140Hz, VAD e Keyword Spotting su Hailo-10H.
2. `hailo_voiceprint_manager.py`: Estrattore di vettori di embedding vocali (Cosine similarity threshold >= 0.72) e registro dei profili vocali utenti in `user_voice_prints.json`.
3. `memory_decay_engine.py`: Buffer di memoria a due livelli (Short-Term 3min + Long-Term Salience Filter) per l'oblio delle trascrizioni irrilevanti.
4. `respeaker_vui_node.py` (Refactored): Rimozione di `pvporcupine`, inoltro del contesto ambientale ereditato verso `conversation.py` / `live_connection_manager.py`.

---

## 📋 Piano di Verifica e Test
- **Test KWS**: Pronuncia della parola "Marcus" con rumore di fondo e verifica reattività LED (`LISTENING`).
- **Test Enrollment Vocale**: Registrazione dell'impronta vocale per "Utente A" e "Utente B" con verifica matching >= 0.72.
- **Test Algoritmo Oblio**: Verifica che trascrizioni vuote/superflue vengano eliminate dopo 3 minuti e che fatti importanti siano conservati.
- **Test Handoff Gemini Live**: Verifica dell'eredità del contesto e assenza di lag all'avvio della sessione WebSocket.
