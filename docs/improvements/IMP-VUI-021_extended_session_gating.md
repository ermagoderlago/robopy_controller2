# Progetto di Miglioramento: IMP-VUI-021 (Extended Conversation Session & Follow-Up Gating)

## 📌 Metadati
* **ID Failure Mode:** `FM-VUI-021`
* **Sottosistema:** VUI Audio / AI
* **Componenti Coinvolti:** `respeaker_vui_node.py`, `live_connection_manager.py`, `llm_service.py`
* **Stato:** `COMPLETED`
* **Dominio:** `audio_vui`
* **Data Creazione:** 2026-08-21
* **ECO Associato:** [`docs/ecos/audio_vui_ecos.md#ECO-2026-08-21-001`](docs/ecos/audio_vui_ecos.md#ECO-2026-08-21-001)
* **Lezione Appresa:** [`docs/lessons/audio_vui_pipeline.md#gestione-sessione-conversazionale-a-3-minuti`](docs/lessons/audio_vui_pipeline.md#gestione-sessione-conversazionale-a-3-minuti)

---

## 🎯 Obiettivo
Eliminare l'interruzione prematura dell'ascolto vocale dopo 8 secondi mantenendo il canale di conversazione con Marcus aperto per 3 minuti (180s), evitando contemporaneamente che il robot intervenga o risponda a conversazioni altrui di sottofondo nei turni successivi.

---

## 🔍 Causa Radice
1. Override forzato `self._listen_timeout_sec = 8.0` in `respeaker_vui_node.py` che scatenava il timeout dopo 8s di silenzio.
2. Finestra di inattività troppo stretta (30s/60s) in `live_connection_manager.py` con disconnessione aggressiva e silenziamento microfono.
3. Mancanza di discriminazione tra Turno 1 (attivazione da wake word) e Turni $\ge 2$ (follow-up mirati) e gestione distruttiva del token `<IGNORE_TURN>`.

---

## 🛠️ Mitigazioni Applicate
1. **Estensione Parametrica a 180s:** Parametro `listen_timeout_sec` impostato a `180.0` secondi di default e rimozione dell'override a 8s.
2. **Reset Dinamico del Timer:** Il timer di 180s viene riavviato sia al termine di ogni frase utente sia quando Marcus finisce di parlare (`_tts_speaking_cb: False`).
3. **Directed Follow-up Gating:**
   - Turno 1: Risposta immediata all'invocazione di "Marcus".
   - Turni Successivi: Marcus risponde a voce solo se interpellato direttamente (es. *"Marcus dimmi..."*).
   - Se rileva dialoghi tra terzi o rumore, il modello emette `<IGNORE_TURN>`, che viene assorbito silenziosamente **senza mutare il microfono né chiudere il canale WebSocket**, preservando l'ascolto attivo per 3 minuti.

---

## ✅ Risultati e Verifica
* $RPN_{init} = 168 \longrightarrow RPN_{res} = 14$ ($S=7, O=2, D=1$).
* Rischio ridotto a `LOW`.
* Sessione conversazionale fluida e stabile senza interferenze ambientali.
