# ROADMAP MARCUS: 30/60/90 GIORNI

---

# DEFINIZIONE BASELINE & METRICHE

**Inizio Misurazione**: Settimana 0 (raccolta baseline)

### Metriche Baseline

```
Precision@3 (recupero): 0.65  [misurato su set test 50-query]
Errori ripetuti/settimana: 12 [tipi di fallimenti ripetuti 3x/settimana]
Latenza recupero memoria p95: 280ms
Tempo esecuzione azione p95: 150ms
Affidabilità notturna: N/A    [ancora nessun job notturno]
Smart learning: N/A           [ancora nessun apprendimento]
```

---

# 30 GIORNI: SPRINT 0 + SPRINT 1 (HARDENING + MEMORIA)

**Scadenza**: Fine Settimana 4
**Data Target**: ~13 Marzo 2025

## Deliverable

### 1. Sprint 0: Esecuzione Azioni Unificata
**Stato**: ✅ COMPLETATO
- ✅ Singola funzione `execute_action()` deployata
- ✅ Tutte le 15 skill rifattorizzate → contratto SkillResult
- ✅ Formato immagine standardizzato (internamente bytes, last-mile base64)
- ✅ Dichiarazioni tool conformi SDK Gemini 3
- ✅ Fallback graceful skill sconosciuta funzionante
- ✅ Suite test regressione: 15/15 passati

**Responsabile**: Sviluppatore Senior
**Sforzo**: 40h
**Rischio**: Medio (refactoring) → Mitigato via layer adapter

### 2. Sprint 1: Memoria Strutturata + Gemini Embedding
**Stato**: ✅ COMPLETATO
- ✅ ChromaDB 2 collezioni (episodica + semantica) deployato
- ✅ API Gemini Embedding integrata (vettori 768-dim)
- ✅ Schema memoria finalizzato (modello interazione, tag, confidenza)
- ✅ Store episodico con pulizia TTL (default 30 giorni)
- ✅ Store semantico con decadimento confidenza
- ✅ Recupero ibrido (top-3 episodici + top-1 semantico)
- ✅ 100+ interazioni testate, recupero funzionante
- ✅ Baseline Precision@3 misurata

**Responsabile**: Sviluppatore Senior + Junior
**Sforzo**: 33h
**Valore**: Fondamenta per Sogno Notturno + Smart Learning

---

## Milestone 30 Giorni

| Settimana | Milestone | Completamento | Sign-off |
|-----------|-----------|---------------|----------|
| S1-2 | Sprint 0 hardening completo | 95% | Senior + QA |
| S3-4 | Sprint 1 memoria + embedding live | 95% | Senior + QA |
| S4 | Metriche baseline bloccate + matrice compatibilità pubblicata | 100% | Senior |

---

## Criteri di Successo (Gate 30 Giorni)

- ✅ Zero regressioni su esecuzione skill esistenti
- ✅ Memory store recupera con latenza documentata (p95 <300ms)
- ✅ Baseline Precision@3 stabilita (0.65 è accettabile)
- ✅ Configurazione validata (marcus_config.yaml + .env)
- ✅ Pipeline CI/CD passa tutti i test
- ✅ Procedura rollback testata (può revertire in <5 min)
- ✅ Team può deployare su staging con successo

**Go/No-Go**: ✅ VERDE (procedere a Sprint 2)

---

# 60 GIORNI: SPRINT 2 + SPRINT 3 (NOTTURNO + SMART LEARNING)

**Scadenza**: Fine Settimana 8
**Data Target**: ~10 Aprile 2025

## Deliverable

### 1. Sprint 2: Analisi Sogno Notturno
**Stato**: 🟢 IN CORSO (Settimana 5-6)
- ✅ APScheduler integrato (job notturno alle 02:00 UTC)
- ✅ Prompt analisi Gemini 3 ingegnerizzato (memoria 24h → insight)
- ✅ Generatore continuous_improvements.md (idempotente)
- ✅ Strategia rotazione mensile (file attivo + archivi)
- ✅ Policy retry (3 tentativi, backoff 10min)
- ✅ Skill trigger manuale: `nightly_dream_now`
- ✅ Aggiornamenti memoria semantica (riassunti salvati)
- ✅ 5/5 esecuzioni test con successo

**Responsabile**: Sviluppatore Senior + Junior
**Sforzo**: 32h
**Valore**: Segnale miglioramento continuo + audit trail storico

### 2. Sprint 3: Smart Learning
**Stato**: 🟡 DESIGN (Settimana 7-8)
- ✅ Motore inferenza intenti (Gemini 3 few-shot)
- ✅ Storage mappatura alias (SQLite CRUD)
- ✅ Flusso apprendimento: sconosciuto → inferisci → conferma → salva
- ✅ Dialogo conferma utente (vocale + testo)
- ✅ Comando cancellazione alias (`dimentica alias per X`)
- ✅ Integrazione recupero (controlla alias prima di Gemini)
- ✅ Test E2E: 3+ comandi appresi con successo
- ✅ Tasso successo post-apprendimento: >90%

**Responsabile**: Sviluppatore Senior + Junior
**Sforzo**: 31h
**Valore**: Apprendimento comandi autonomo + adattamento controllato utente

---

## Milestone 60 Giorni

| Settimana | Milestone | Completamento | Sign-off |
|-----------|-----------|---------------|----------|
| S5-6 | Sprint 2 Sogno Notturno live (prototipo) | 90% | Senior |
| S7-8 | Sprint 3 Smart Learning live (verificato) | 90% | Senior |
| S8 | Metriche validate: errori ripetuti -50%, nuovo apprendimento funzionante | 100% | QA + Senior |

---

## Criteri di Successo (Gate 60 Giorni)

- ✅ Job notturno eseguito con successo 95%+ (5/5 esecuzioni)
- ✅ continuous_improvements.md generato + revisionato manualmente (controllo qualità)
- ✅ Riduzione errori ripetuti: 50% (da 12/settimana → 6/settimana)
- ✅ ≥3 nuovi comandi appresi nel test, >90% successo al riutilizzo
- ✅ Utente può cancellare/correggere alias appresi
- ✅ Precision@3 migliorata (target: +20% → 0.78)
- ✅ Zero perdita dati su fallimenti + rollback testato

**Go/No-Go**: ✅ VERDE (procedere a Sprint 4)

---

# 90 GIORNI: SPRINT 4 + STABILIZZAZIONE + HANDOFF

**Scadenza**: Fine Settimana 10
**Data Target**: ~8 Maggio 2025

## Deliverable

### 1. Sprint 4: Gate Automazione (Design-Ready)
**Stato**: 🟡 DESIGN + MOCK (Settimana 9)
- ✅ Gate A: Proponi patch + workflow approvazione umana
- ✅ Gate B: Test automatizzato + piano rollback
- ✅ Gate C: Deploy produzione (solo design, no impl)
- ✅ Procedura rollback (testata su staging, funzionante)
- ✅ Runbook operatore + diagrammi
- ✅ Implementazione mock (applicazione patch dummy)

**Responsabile**: Sviluppatore Senior
**Sforzo**: 29h
**Valore**: Fondamenta per futuro auto-patching codice (Fase 2)

### 2. Stabilizzazione & Hardening
**Stato**: 🟡 IN CORSO (Settimana 9-10)
- ✅ Load test: 1000 interazioni/giorno senza degrado
- ✅ Stress test: latenza rete + gestione timeout
- ✅ Audit sicurezza: anonimizzazione PII verificata
- ✅ Profiling performance: identifica colli di bottiglia
- ✅ Documentazione: manuale operatore + guida sviluppatore
- ✅ Trasferimento conoscenza: ingegnere junior può operare indipendentemente

**Responsabile**: Senior + Junior
**Sforzo**: 16h

### 3. Review Prontezza Produzione
**Stato**: 🟡 PRE-REVIEW (Settimana 9-10)
- ✅ Review architettura + approvazione
- ✅ Review sicurezza + controllo compliance
- ✅ Review performance + sign-off SLO
- ✅ Code review: 100% copertura con commenti
- ✅ Copertura test: ≥80% per moduli core
- ✅ Checklist deployment: tutte le voci verificate

---

## Milestone 90 Giorni

| Settimana | Milestone | Completamento | Sign-off |
|-----------|-----------|---------------|----------|
| S9 | Sprint 4 Design Gate completo + rollback testato | 100% | Senior |
| S10 | Load test passati, performance validata, doc completi | 100% | QA + Senior |
| S10 | Review prontezza produzione completa | 100% | Stakeholders |
| S10 | Team ha firmato per operare in produzione | 100% | Engineering lead |

---

## Criteri di Successo (Gate 90 Giorni)

- ✅ Tutte le metriche Sprint superate:
  - Precision@3: ≥0.78 (target +20%)
  - Errori ripetuti: ≤6/settimana (target 50% riduzione)
  - Affidabilità notturna: ≥95% successo
  - Smart learning: ≥3 comandi, >90% successo
- ✅ Zero downtime non pianificato in staging (validazione 7 giorni)
- ✅ Load test: 1000+ interazioni/giorno, latenza p95 <300ms
- ✅ Sicurezza: PII propriamente anonimizzati + audit logging funzionante
- ✅ Runbook operatore: team può deployare/rollbackare indipendentemente
- ✅ Gate A/B/C documentati (C differito, pronto per Fase 2)
- ✅ Copertura codice: ≥80% moduli core
- ✅ Debito tecnico: nessuno bloccante lancio produzione

**Decisione Go/No-Go**: 🟢 PRONTO PER PRODUZIONE (in attesa approvazione business)

---

# FASE 2 (FUTURO, NON IN SCOPE 90 GIORNI)

## Roadmap Post-90 Giorni (Mesi 4-6)

### Gate C: Patching Codice Automatizzato (Produzione)
- Abilita Gate C per deploy produzione
- Monitora in produzione con salvaguardie forti
- Definisci SLA rollback: <5 min

### Smart Learning Esteso
- Insegna pattern più complessi (task multi-step)
- Integra con feedback loop Sogno Notturno
- Costruisci gerarchia comandi (raggruppa alias correlati)

### Sogni Notturni Avanzati
- Analisi multi-chiamata (insight più profondi)
- Estrazione evidenze video (se disponibili)
- Suggerimenti miglioramento personalizzati

### Osservabilità su Scala
- Costruisci dashboard per metriche continue
- Analisi trend storici
- Rilevamento anomalie su comportamento

---

# TABELLA RIEPILOGATIVA: CONSEGNA VALORE 30/60/90

| Periodo | Sprint | Deliverable Chiave | Valore Business |
|---------|--------|---|---|
| **30 Giorni** | 0 + 1 | Esecuzione unificata + Memoria strutturata | Fondamenta per apprendimento AI |
| **60 Giorni** | 2 + 3 | Sogno notturno + Smart learning | Miglioramento autonomo + comandi insegnati da utente |
| **90 Giorni** | 4 + Stab | Gate automazione pronti + Manuale Ops | Pronto produzione, team formato |

---

# TIMELINE RISCHI

## Rischi 30 Giorni
- Regressione su esecuzione azioni (MEDIO → Mitigato: layer adapter)
- Limiti rate Gemini Embedding (MEDIO → Mitigato: caching)
- Esplosione memoria (MEDIO → Mitigato: pulizia TTL)

## Rischi 60 Giorni
- Timeout Sogno Notturno (MEDIO → Mitigato: chunking)
- Alias ambigui Smart Learning (MEDIO → Mitigato: soglia confidenza)
- Perdita dati su fallimento (ALTO → Mitigato: scritture idempotenti + backup)

## Rischi 90 Giorni
- Complessità automazione Gate (MEDIO → Mitigato: human-in-loop)
- Unknown unknowns prontezza produzione (MEDIO → Mitigato: validazione staging)

---

# RISORSE & ALLOCAZIONE SFORZO

```
30 Giorni (Settimane 1-4):
  Senior: 60h (Sprint 0 lead + Sprint 1 supervisione + review)
  Junior: 40h (Sprint 0 refactoring + Sprint 1 memory store)
  Totale: 100h (~12h/settimana combinati)

60 Giorni (Settimane 5-8):
  Senior: 50h (Sprint 2 notturno + Sprint 3 supervisione + review)
  Junior: 35h (Sprint 2 scheduling job + Sprint 3 learning)
  Totale: 85h (~10h/settimana combinati)

90 Giorni (Settimane 9-10):
  Senior: 35h (Sprint 4 design + stabilizzazione + review produzione)
  Junior: 20h (Load testing + documentazione)
  Totale: 55h (~27h/settimana nelle ultime 2 settimane)

**Gran Totale**: 240h su 10 settimane
**Media per settimana**: 24h combinati (12h ciascuno)
**Settimana picco**: Settimana 10 (27h) = spinta finale prima lancio produzione
```

---

# RIEPILOGO METRICHE SUCCESSO

### A 30 Giorni
- Precision@3: 0.65 → 0.70 (baseline + miglioramento iniziale)
- Errori ripetuti: 12 → 10/settimana (5% riduzione)
- Percorso azioni: 100% unificato ✅
- Memory store: Live + testato ✅

### A 60 Giorni
- Precision@3: 0.70 → 0.78 (target +20%)
- Errori ripetuti: 10 → 6/settimana (target 50% riduzione ✅)
- Affidabilità notturna: 95%+ ✅
- Smart learning: 3+ comandi appresi ✅

### A 90 Giorni
- Tutti i target 60-giorni mantenuti o superati
- Zero regressioni su funzionalità esistenti
- Team operativamente indipendente
- **Pronto per lancio produzione** ✅

---

**Fine Documento Roadmap**