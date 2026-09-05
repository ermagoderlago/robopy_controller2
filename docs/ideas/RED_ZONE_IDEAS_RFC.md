# 💡 Registro Idee & RFC Zona Rossa (Human-in-the-Loop Supervision)

Questo documento raccoglie tutte le proposte di modifica generate dal motore di auto-evoluzione e curiosità di **Marcus** che toccano o richiedono il rilassamento di vincoli categorici di **🔴 Zona Rossa** definiti in `marcus_core_rules.md` e nelle Schede Tecniche (`SPEC-00` .. `SPEC-07`).

> [!CAUTION]
> **REGOLA DI SICUREZZA ASSOLUTA:**
> Nessuna proposta presente in questo registro può essere applicata autonomamente dal robot. Ciascun RFC richiede l'analisi, il consenso esplicito e il merge manuale da parte dell'operatore umano.

---

## 📑 Indice delle Proposte RFC

| ID RFC | Data | Sottosistema | Titolo Proposta | Vincolo Violato | Stato |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `RFC-INIT-001` | 2026-09-05 | System/DDS | Architettura Registri e Safe Gating Zona Rossa | SPEC-00 (Self-Gating) | `APPROVED` |

---

## 📝 Dettaglio RFC

### `RFC-INIT-001`: Istituzione del Registro Idee e Safe Gating per Zona Rossa
- **Data Generazione:** 2026-09-05 20:35:00
- **Autore:** Marcus Autonomous Evolution Engine & Antigravity
- **Sottosistema Target:** Governance / Core Robot Safety
- **Vincolo di Zona Rossa Coinvolto:** `SPEC-00` - Divieto di auto-modifica a contratti protetti, limiti fisici e parametri architetturali critici.
- **Descrizione della Proposta:**
  Creazione di un canale permanente di raccolta per intuizioni, refactoring complessi, aggiornamenti architetturali o ottimizzazioni che eccedono il perimetro della Zona Verde (es. variazioni PID motori oltre soglia, cambio pipeline VUI o integrazione modelli Hailo NPU differenti).
- **Benefici Potenziali:**
  Permette a Marcus di continuare ad analizzare il proprio codice ed elaborare idee creative senza rischiare di bloccare o danneggiare il robot.
- **Rischi Ingegneristici:**
  Nessun rischio diretto, in quanto l'esecuzione autonoma è inibita a livello di motore decisionale.
- **Decisione Operatore Umano:** `APPROVED` (Implementato come standard di governance).

---

*(Le nuove proposte generate dal robot verranno accodate qui automaticamente con stato `AWAITING_HUMAN_REVIEW`)*

### `RFC-AUTO-1788633804`: Richiesta aumento velocità massima lineare a 1.2 m/s
- **Data Generazione:** 2026-09-05 20:43:24
- **Autore:** Marcus Autonomous Curiosity Engine
- **Sottosistema Target:** Chassis & Motion
- **Vincolo di Zona Rossa Coinvolto:** `SPEC-01` - Divieto di alterare parametri di velocità lineare massima in autonomia
- **Descrizione della Proposta:**
  Proposta formulata per accorciare i tempi di transito nei corridoi lunghi.
- **Benefici Potenziali:**
  Riduzione del 50% dei tempi di navigazione in ambienti aperti.
- **Rischi Ingegneristici Identificati:**
  Superamento limiti di aderenza ruote, ribaltamento o collisioni con stop distance insufficiente.
- **Decisione Operatore Umano:** `AWAITING_HUMAN_REVIEW`

---
