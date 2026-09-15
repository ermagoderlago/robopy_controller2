# PIANO DI IMPLEMENTAZIONE MARCUS
## Roadmap Ingegneristica di 5 Sprint con Gemini 3

**Progetto**: MARCUS Hardening + Memoria + Apprendimento + Automazione
**Dimensione Team**: 2 sviluppatori (assunto: 1 senior, 1 junior)
**Durata**: 10 settimane (~60 giorni) fino a Smart Learning stabile + Gates A/B
**Obiettivo**: Pronto per la produzione in 90 giorni
**Modello Gemini**: Gemini 3 (ultimo)

---

# 1. EXECUTIVE SUMMARY

**Obiettivo**: Trasformare MARCUS da prototipo a robot cognitivo di livello produttivo con:
1. Hardening a runtime (percorso azioni unificato, type safety, gestione errori)
2. Memoria strutturata con Gemini Embedding (separazione episodica/semantica)
3. Analisi Notturna dei Sogni (consolidamento memoria 24h + miglioramenti continui)
4. Smart Learning (inferenza comandi + alias confermati dall'utente)
5. Gate di Automazione (pipeline pronta per il design per future modifiche automatiche del codice)

**Risultati Chiave**:
- ✅ Percorso esecuzione azioni unificato al 100% (nessun duplicato, nessun fallimento silenzioso)
- ✅ Gemini Embedding integrato (ricerca semantica + rilevamento conflitti)
- ✅ Sogno Notturno: 1 job/notte → continuous_improvements.md (idempotente)
- ✅ Smart Learning: 3+ nuovi comandi appresi nei test (>90% successo post-apprendimento)
- ✅ Gate di Automazione: Design-ready per patching del codice approvato dall'uomo
- ✅ Metriche: +20% pertinenza recupero, 50% riduzione errori ripetuti, >95% affidabilità notturna

**Criteri di Successo**:
- Precision@3 sul recupero memoria: **+20%** (baseline vs v5.2)
- Riduzione errori ripetuti: **50%** in 4 settimane
- Tasso di successo job notturno: **>95%** (con retry)
- Comandi Smart Learning appresi: **≥3** con **>90%** successo post-apprendimento
- Zero perdita dati su guasto + capacità di rollback completo

---

# 2. RIEPILOGO DECISIONI ARCHITETTURALI

## Decisione 1: Percorso Esecuzione Azioni Unificato
**Scelta**: Singola funzione `execute_action()` in `action_controller.py`
**Logica**:
- Eliminare logica duplicata (vecchio `call_action()` vs `run_skill()`)
- Type safety in un unico punto
- Gestione errori unificata + osservabilità

**Compromesso**: Refactoring minore dell'interfaccia skill esistente (~8h)
**Alternativa Scartata**: Mantenere doppi percorsi (incubo manutenzione, fallimenti silenziosi)

---

## Decisione 2: Standard Formato Immagine (bytes → base64 solo all'ultimo miglio)
**Scelta**: Standard interno = `bytes` (raw)
**Logica**:
- Efficienza memoria (40% più piccolo di base64)
- Nessuna conversione silenziosa che causa bug
- Ultimo miglio (upload API) = conversione esplicita base64 solo lì

**Compromesso**: Logica di conversione lato consumer
**Alternativa Scartata**: Memorizzare come base64 (spazio sprecato, più difficile da debuggare)

---

## Decisione 3: Gemini Embedding (gemini-embedding-001)
**Scelta**: Usare embedding nativo di Gemini 3 con vettore a 768 dimensioni
**Logica**:
- Compatibilità nativa Gemini 3 (nessun problema cross-model)
- 768 dimensioni sufficienti per split episodico/semantico
- Embedding unificato → recupero unificato

**Compromesso**: Dipendenza da API Gemini Embedding (limiti rate: 1500 req/min)
**Alternativa Scartata**: Embedding multi-modello (complessità + latenza)

---

## Decisione 4: Separazione Memoria (episodica 30gg TTL, semantica permanente)
**Scelta**: ChromaDB 2 collezioni + recupero ibrido
**Logica**:
- Episodica: conversazioni, eventi (ritenzione 30gg, poi archivio/aggregazione)
- Semantica: fatti appresi, preferenze, routine (permanente con decadimento confidenza)
- Ibrido: top-3 episodici + top-1 semantico per query

**Compromesso**: Necessità di gestire 2 collezioni + job pulizia TTL
**Alternativa Scartata**: Collezione singola (troppo rumorosa dopo 30gg)

---

## Decisione 5: Sogno Notturno = Analisi one-shot Gemini 3
**Scelta**: 1 chiamata API/notte, analizza memorie ultime 24h
**Logica**:
- Costo efficace (1 chiamata = ~$0.001)
- Output idempotente (`continuous_improvements.md`)
- Human-in-loop (proponi, non applicare automaticamente)

**Compromesso**: Non può fare ragionamento profondo multi-turn (accettabile per prototipo)
**Alternativa Scartata**: Catena multi-chiamata (eccessivo, $$)

---

## Decisione 6: Smart Learning = Alias Comandi + Conferma
**Scelta**: Inferire intento → chiedere utente → salvare mappatura
**Logica**:
- Basso rischio (l'utente controlla l'apprendimento)
- Utile (3+ comandi in scenario test)
- Reversibile (l'utente può cancellare alias)

**Compromesso**: Richiede conferma utente (non completamente autonomo)
**Alternativa Scartata**: Auto-apprendimento (troppo rischioso, potenziale per errori persistenti)

---

## Decisione 7: Gate Automazione = Design-ready (no full-auto in prod ancora)
**Scelta**: Gate A (proponi) + Gate B (test) solo; Gate C differito
**Logica**:
- Sicurezza prima di tutto (approvazione umana prima modifiche codice)
- Impara in ambiente test prima
- Rollback provato prima della produzione

**Compromesso**: Iterazione più lenta (accettabile per sicurezza)
**Alternativa Scartata**: Full-auto in produzione (rischio inaccettabile)

---

## Decisione 8: continuous_improvements.md = Strategia rotazione mensile
**Scelta**: Mantenere file attivo + archivio mensile (`continuous_improvements_YYYY_MM.md`)
**Logica**:
- Previene crescita illimitata
- Traccia storica (vagliabile)
- Mese corrente sempre piccolo (<1MB)

**Compromesso**: Necessità job rotazione (5 righe di codice)
**Alternativa Scartata**: File singolo con TTL (perde storia, più difficile da vagliare)

---

# 3. PIANIFICAZIONE SPRINT-BY-SPRINT

## SPRINT 0: RUNTIME HARDENING (Settimana 1-2)
**Dipendenza**: Nessuna (livello base)
**Deliverable**: Esecuzione azioni unificata + contratto SkillResult

### Attività

| Attività | Responsabile | Sforzo | Note |
|----------|--------------|--------|------|
| Audit percorsi azioni esistenti (call_action, run_skill, invoke_skill) | Senior | 4h | Lista tutte le varianti + utilizzo |
| Progettazione contratto dataclass SkillResult | Senior | 2h | Immutabile, tipizzato, completo |
| Refactoring action_controller.py → execute_action() | Senior | 6h | Punto ingresso singolo, validazione |
| Aggiornamento tutte le 15 skill al nuovo contratto SkillResult | Junior + Senior | 12h | 30min per skill media |
| Standardizzazione gestione immagini (interna bytes, last-mile base64) | Junior | 4h | Aggiornamento vision_interface.py + test |
| Validazione dichiarazioni tool (conformità Gemini SDK) | Senior | 3h | Controllo schema + pytest |
| Gestione errori + fallback skill sconosciuta | Senior | 3h | Test con nome skill invalido |
| Unit test per hardening (test_action_controller.py) | Junior | 6h | 15+ casi di test |
| **TOTALE** | | **40h** | |

**Rischi**:
- Rottura chiamate skill esistenti → mitigare con layer adapter (1 giorno per revert)
- Cambio formato immagine causa bug encoding → mitigare con test completi

**Definizione di Fatto (DoD)**:
- ✅ 100% percorsi azioni usano execute_action()
- ✅ Tutte le 15 skill restituiscono contratto SkillResult
- ✅ Formato immagine controllato (bytes interni)
- ✅ Dichiarazioni tool superano validazione schema Gemini
- ✅ 15 unit test passati, 0 regressioni

---

## SPRINT 1: STRUTTURAZIONE MEMORIA (Settimana 3-4)
**Dipendenza**: Sprint 0 (per percorso azioni stabile)
**Deliverable**: Integrazione Gemini Embedding + schema ChromaDB

### Attività

| Attività | Responsabile | Sforzo | Note |
|----------|--------------|--------|------|
| Setup ChromaDB 2-collezioni (episodica + semantica) | Junior | 3h | Docker + migrazione |
| Integrazione API Gemini Embedding (test vettori 768-dim) | Senior | 4h | Gestione rate limit |
| Progettazione schema memoria (modello interazione + tag) | Senior | 3h | intent, entities, confidence, ecc |
| Implementazione store episodico (salvataggio conversazioni) | Junior | 6h | Gestione TTL, pulizia |
| Store semantico + algoritmo decadimento confidenza | Senior | 5h | Storage permanente + decadimento |
| Funzione recupero ibrido (top-3 episodici + top-1 semantico) | Senior | 4h | Strategia ranking |
| Test integrazione: salva 100 interazioni, recupera per query | Junior | 4h | Baseline performance |
| Matrice compatibilità (topic ROS, contratti API) | Senior | 2h | Doc cambiamenti breaking |
| Schema config (memory.yaml) con default | Junior | 2h | YAML + validazione |
| **TOTALE** | | **33h** | |

**Rischi**:
- Rate limit Gemini Embedding raggiunti → mitigare con caching locale + batching
- Esplosione memoria (100K embedding) → mitigare con pulizia TTL + archivio

**Definizione di Fatto (DoD)**:
- ✅ ChromaDB in esecuzione con 2 collezioni
- ✅ 100 interazioni salvate + recuperate
- ✅ Precision@3 baseline misurata (punteggio: X%)
- ✅ Zero perdita dati su errori
- ✅ Matrice compatibilità pubblicata

---

## SPRINT 2: ANALISI SOGNO NOTTURNO (Settimana 5-6)
**Dipendenza**: Sprint 1 (struttura memoria)
**Deliverable**: Job notturno + continuous_improvements.md

### Attività

| Attività | Responsabile | Sforzo | Note |
|----------|--------------|--------|------|
| Scheletro job Sogno Notturno (scheduler + chiamata Gemini 3) | Senior | 4h | Integrazione APScheduler |
| Prompt engineering (memoria 24h → insight) | Senior | 5h | Test su dati campione |
| Parsing output (Gemini JSON → strutturato) | Junior | 3h | Validazione + gestione errori |
| Generatore continuous_improvements.md (idempotente) | Junior | 4h | Logica rotazione mensile |
| Modulo analisi memoria (classifica esperienze, errori, gap) | Senior | 5h | Tag + punteggio interazioni |
| Policy retry (ritardo 10min, 3 tentativi) | Junior | 2h | Backoff esponenziale |
| Trigger comando manuale (skill: "nightly_dream_now") | Junior | 2h | Debug/test |
| Config (nightly_dream.yaml): abilitato, orario, modello, retry | Junior | 1h | Default |
| Test: simula 24h interazioni, esegui job, verifica output | Senior | 4h | Test regressione |
| Integrazione: salva riassunti in memoria semantica | Junior | 2h | Consolidamento |
| **TOTALE** | | **32h** | |

**Rischi**:
- Timeout Gemini su analisi memoria grande → mitigare con chunking (1h fix)
- continuous_improvements.md diventa stantio/inconsistente → mitigare con controlli idempotenza

**Definizione di Fatto (DoD)**:
- ✅ Job notturno eseguito con successo 95%+ (5/5 test)
- ✅ continuous_improvements.md generato + valido
- ✅ Policy retry testata (simula fallimenti)
- ✅ Trigger manuale funzionante
- ✅ 0 perdita dati su timeout

---

## SPRINT 3: SMART LEARNING (Settimana 7-8)
**Dipendenza**: Sprint 1 (memoria) + Sprint 2 (notturno per feedback)
**Deliverable**: Inferenza comandi + apprendimento alias

### Attività

| Attività | Responsabile | Sforzo | Note |
|----------|--------------|--------|------|
| Motore inferenza intenti (usa Gemini 3 few-shot) | Senior | 5h | "Luci accese" → intent: turn_on, entity: lights |
| Storage mappatura alias (SQLite: user_input → canonical_intent/skill) | Junior | 3h | CRUD + validazione |
| Flusso apprendimento: sconosciuto → inferisci → chiedi → salva | Senior | 6h | Macchina stati dialogo |
| UI conferma utente (opzioni vocali + testo) | Junior | 4h | "Intendevi X? Di sì/no" |
| Comando correzione/cancellazione alias | Junior | 2h | "Dimentica l'alias per X" |
| Integrazione recupero: controlla alias prima inferenza Gemini | Senior | 2h | Ottimizzazione fast path |
| Scenario test: 3+ comandi appresi (valuta, insegna, riusa) | Senior + Junior | 6h | Test end-to-end |
| Metriche: tasso successo pre/post-apprendimento | Junior | 2h | Logging + analisi |
| Controllo retrocompatibilità (vecchi comandi funzionano ancora) | Senior | 1h | Smoke test |
| **TOTALE** | | **31h** | |

**Rischi**:
- Utente insegna alias sbagliato → mitigare con soglia confidenza + comando cancella
- Ambiguità alias (stessa frase per 2 intenti diversi) → mitigare con prompt contesto

**Definizione di Fatto (DoD)**:
- ✅ ≥3 comandi appresi nel test
- ✅ Tasso successo post-apprendimento >90%
- ✅ Utente può cancellare/correggere alias
- ✅ 0 regressioni su comandi esistenti
- ✅ Metriche loggate + analizzate

---

## SPRINT 4: DESIGN GATE AUTOMAZIONE (Settimana 9)
**Dipendenza**: Tutti gli sprint precedenti (solo design + firme)
**Deliverable**: Doc design Gate A + Gate B + implementazione mock

### Attività

| Attività | Responsabile | Sforzo | Note |
|----------|--------------|--------|------|
| Design Gate A: proponi patch (revisione insight Notturni) | Senior | 4h | Quali patch sono sicure? |
| Gate A: workflow approvazione umana (notifica email/Slack) | Senior | 3h | Interfaccia approvazione mock |
| Design Gate B: test su branch + piano rollback | Senior | 4h | GitHub Actions + script revert |
| Gate B: test harness automatizzato (smoke + regressione) | Junior | 5h | Esegui sottoinsieme test |
| Design Gate C: deploy produzione (implementazione differita) | Senior | 2h | Solo design, no codice |
| Procedura rollback (testata su staging) | Senior + Junior | 6h | Revert completo testato |
| Documentazione: diagramma workflow gate + runbook operatore | Senior | 2h | Visio/Mermaid + markdown |
| Implementazione mock (applicazione patch dummy) | Junior | 3h | Solo scheletro |
| **TOTALE** | | **29h** | |

**Rischi**:
- Automazione rompe produzione → mitigare con test rigorosi Gate B + solo staging in questo sprint

**Definizione di Fatto (DoD)**:
- ✅ Doc design Gate A + workflow approvazione mock
- ✅ Test harness Gate B + rollback testato su staging
- ✅ Design Gate C documentato (nessuna implementazione)
- ✅ Runbook operatore scritto
- ✅ Zero downtime non pianificato nei test staging

---

## STIMA SFORZO TOTALE
- Sprint 0: 40h
- Sprint 1: 33h
- Sprint 2: 32h
- Sprint 3: 31h
- Sprint 4: 29h
- **Totale**: 165h ≈ **10 settimane** (con team 2 persone, 20h/settimana ciascuno = 40h/settimana combinati)

**Cronologia**:
- Settimana 1-2: Sprint 0
- Settimana 3-4: Sprint 1
- Settimana 5-6: Sprint 2
- Settimana 7-8: Sprint 3
- Settimana 9-10: Sprint 4 + stabilizzazione + testing

---

# 4. DIPENDENZE E PARALLELIZZAZIONE

```
Sprint 0 (Hardening)
    ↓
Sprint 1 (Memoria)
    ↓
Sprint 2 (Notturno) ──┐
                      │
Sprint 3 (Smart Learning) ← usa feedback Notturno
    ↓
Sprint 4 (Design Gate)
```

**Possibile Parallelizzazione**:
- Design Sprint 4 può iniziare durante Settimana 7 (mentre Sprint 3 è in corso)
- Riduce percorso critico di 1 settimana se team design separato

---

# 5. METRICHE DI SUCCESSO

| Metrica | Target | Misurazione |
|---------|--------|-------------|
| Precision@3 (recupero) | +20% vs baseline | Test set di 50 query, prima/dopo |
| Errori ripetuti | 50% riduzione in 4 settimane | Traccia categorie errori settimanalmente |
| Affidabilità notturna | >95% tasso successo | 5/5 esecuzioni con successo (con retry) |
| Smart Learning | ≥3 comandi appresi | Scenario test con valutazione |
| Successo Smart Learning | >90% post-apprendimento | Utente ri-emette comando appreso, esegue correttamente |
| Zero perdita dati | 100% | Test guasto + recupero + audit trail |
| Tempo rollback | <5 min | Tempo da comando "rollback" a stato stabile verificato |

---

# 6. ASSUNZIONI E VINCOLI

**Assunzioni**:
- ChromaDB già deployato + accessibile
- Quota API Gemini 3 allocata (1500+ req/min)
- ROS 2 Humble + Python 3.10+ in loco
- Team: 1 senior (architetto/code review), 1 junior (implementazione)
- 15 skill esistenti + 3 moduli core (visione, azione, memoria)

**Vincoli**:
- Job notturno max 1 chiamata API/esec (costo + semplicità)
- Nessun deploy codice full-auto in produzione in 90gg (solo Gate A/B)
- Embedding memoria: 768-dim Gemini Embedding (fisso)
- TTL Episodico: 30 giorni (limite rigido)

---

# 7. REGISTRO RISCHI E MITIGAZIONI

(Vedi documento successivo: `MARCUS_IMPLEMENTATION_RISKS.md`)

---

# 8. STRATEGIA CI/CD E TEST

(Vedi documento successivo: `MARCUS_IMPLEMENTATION_TESTS.md`)

---

# 9. RUNBOOK E PROCEDURE OPERATIVE

(Vedi documento successivo: `MARCUS_IMPLEMENTATION_RUNBOOK.md`)

---

# 10. ROADMAP 30/60/90

(Vedi documento successivo: `MARCUS_IMPLEMENTATION_ROADMAP.md`)

---

**Fine Documento di Pianificazione**

Prossimi documenti:
- MARCUS_IMPLEMENTATION_RISKS.md
- MARCUS_IMPLEMENTATION_TESTS.md
- MARCUS_IMPLEMENTATION_RUNBOOK.md
- MARCUS_IMPLEMENTATION_ROADMAP.md
- MARCUS_SPRINT_0_PATCHES.py