# IMPLEMENTAZIONE MARCUS
## Registro Rischi, Strategia di Test, CI/CD

---

# 1. REGISTRO RISCHI E MITIGAZIONI

## RISCHIO 1: Percorsi Azioni Duplicati Rompono Skill Esistenti
**Gravità**: ALTA
**Probabilità**: MEDIA (refactoring sempre rischioso)
**Impatto**: 15+ skill non funzionanti simultaneamente

**Mitigazione**:
1. Layer adapter: vecchio `call_action()` → nuovo `execute_action()` (1 giorno per revert)
2. E eseguire unit test di **tutte le 15 skill** prima del merge
3. Test in ambiente staging (1 giorno)
4. Rollback: Git revert + riavvio servizi (5 min)

**Responsabile**: Sviluppatore Senior
**Tempistica**: Sprint 0, Settimana 1
**DOR**: Tutte le 15 skill passano i test su staging

---

## RISCHIO 2: Limiti Rate API Gemini Embedding
**Gravità**: MEDIA
**Probabilità**: MEDIA (100K embedding in 30 giorni = ~3.5K/giorno)
**Impatto**: Recupero memoria bloccato se quota esaurita

**Mitigazione**:
1. Cache LRU locale (100 embedding in memoria)
2. Richieste embedding in batch (max 10 concorrenti)
3. Backoff esponenziale su rate limit (partenza 1s, tetto 30s)
4. Fallback su ricerca semantica metadati ChromaDB (senza embedding)

**Responsabile**: Sviluppatore Senior
**Tempistica**: Sprint 1, Settimana 3-4
**DOR**: Test rate limit passato (simula esaurimento, verifica cache + fallback)

---

## RISCHIO 3: Esplosione Memoria (100K+ Embedding)
**Gravità**: MEDIA
**Probabilità**: MEDIA (30 giorni * 3K/giorno = 90K voci episodiche)
**Impatto**: Recupero ChromaDB lento, spazio disco

**Mitigazione**:
1. Job pulizia TTL (giornaliero): rimuovi voci episodiche >30gg
2. Archivia in collezione archive_episodic dopo 30gg
3. Compressione semantica: job giornaliero consolida vecchi episodici → semantici
4. Monitoraggio: traccia dimensione collezione, alert se >1GB

**Responsabile**: Sviluppatore Junior
**Tempistica**: Sprint 1, Settimana 4 (dopo carico iniziale)
**DOR**: Job pulizia passa test 5-giorni, uso disco stabile

---

## RISCHIO 4: Timeout Sogno Notturno (Analisi Memoria Grande)
**Gravità**: MEDIA
**Probabilità**: MEDIA (1 chiamata con 100+ embedding = latenza potenziale)
**Impatto**: Job notturno fallisce, continuous_improvements.md non aggiornato

**Mitigazione**:
1. Chunking: analizza memorie 24h in lotti (finestre di 6 ore, 4 chiamate max/notte)
2. Timeout: 30s per chiamata, retry 3x con ritardo 10min
3. Fallback: se tutti i retry falliscono, salta quella notte (log errore, nessuna perdita dati)
4. Alternativa: riassumere a livello memory-store (precalcola riassunti 6h)

**Responsabile**: Sviluppatore Senior
**Tempistica**: Sprint 2, Settimana 5-6
**DOR**: Test timeout con 100 interazioni passato, policy retry validata

---

## RISCHIO 5: Perdita/Corruzione Dati continuous_improvements.md
**Gravità**: ALTA
**Probabilità**: BASSA (ops file dovrebbero essere sicure)
**Impatto**: Perdita di 30 giorni di storia miglioramenti

**Mitigazione**:
1. Scritture idempotenti: appendi sempre con timestamp
2. Backup: copia giornaliera su continuous_improvements_BACKUP.md
3. Rotazione mensile: continuous_improvements_2025_02.md (immutabile dopo fine mese)
4. File locking: journal stile SQLite per sicurezza

**Responsabile**: Sviluppatore Junior
**Tempistica**: Sprint 2, Settimana 5 (prima primo run notturno)
**DOR**: Test fallimento (kill processo mid-write), verifica no corruzione + rollback successo

---

## RISCHIO 6: Smart Learning Salva Alias Ambigui
**Gravità**: MEDIA
**Probabilità**: MEDIA (utente potrebbe confermare intento poco chiaro)
**Impatto**: Misinterpretazione persistente (imparato una volta = ripetuto)

**Mitigazione**:
1. Soglia confidenza: salva solo se confidenza intento Gemini >0.85
2. Review utente: mostra intento inferito + esempi prima della conferma
3. Comando cancella: "Dimentica alias per X" con audit trail
4. Rollback: rimuovi alias ultime 24h se rilevato fallimento sistematico

**Responsabile**: Sviluppatore Senior
**Tempistica**: Sprint 3, Settimana 7-8
**DOR**: Test soglia confidenza + comando cancella funzionante, 0 falsi positivi nel test

---

## RISCHIO 7: Ambiente Gate B (Test) Non Corrisponde a Produzione
**Gravità**: MEDIA
**Probabilità**: MEDIA (ambienti diversi = comportamenti diversi)
**Impatto**: Test passa, produzione fallisce (regressione)

**Mitigazione**:
1. Staging = clone esatto di prod (stessa config, campione dati)
2. Smoke test: esegui suite skill completa dopo applicazione patch
3. Dataset regression test: 50 interazioni passate, esiti attesi documentati
4. Opzione A/B test: deploy su 10% traffico prima (se applicabile)

**Responsabile**: Sviluppatore Senior
**Tempistica**: Sprint 4, Settimana 9
**DOR**: Parità Staging = prod verificata, suite regression test documentata

---

## RISCHIO 8: Rottura Retrocompatibilità (Cambiamenti API)
**Gravità**: ALTA
**Probabilità**: MEDIA (refactoring Sprint 0)
**Impatto**: Integrazioni esistenti falliscono (topic ROS, chiamate skill esterne)

**Mitigazione**:
1. Matrice compatibilità: documenta tutti i cambiamenti breaking/non-breaking
2. Layer adapter: supporta vecchia API accanto alla nuova per 1 sprint
3. Warning deprecazione: log quando usata vecchia API
4. Guida migrazione: passo-passo per aggiornare codice esterno

**Responsabile**: Sviluppatore Senior
**Tempistica**: Sprint 0, Settimana 2 (fine sprint)
**DOR**: Matrice compatibilità pubblicata, layer adapter testato su staging

---

## RISCHIO 9: Sicurezza: Perdita PII in Memoria/Log
**Gravità**: ALTA
**Probabilità**: BASSA (se non gestita attivamente)
**Impatto**: Data breach, violazione privacy

**Mitigazione**:
1. Anonimizzazione: hash nomi, maschera telefono/indirizzo prima di salvare
2. Audit logging: traccia chi ha acceduto a PII, quando
3. Ritenzione: cancella PII più vecchi di 30 giorni (tieni solo semantica)
4. Controllo accesso: query memoria filtrate per user_id

**Responsabile**: Sviluppatore Senior
**Tempistica**: Sprint 1, Settimana 3-4 (prima primo storage dati)
**DOR**: PII rimossi da dataset test 1000-voci, hash/mask verificato

---

## RISCHIO 10: Velocità Sviluppo Skill Cala Post-Hardening
**Gravità**: BASSA
**Probabilità**: BASSA (refactoring potrebbe sembrare più lento inizialmente)
**Impatto**: Ritardo consegna

**Mitigazione**:
1. Documentazione: aggiorna guida dev skill con nuovo contratto SkillResult
2. Template: fornisci template skill che gli sviluppatori copiano
3. Code review: ingegnere senior su ogni nuova skill (SLA 1 giorno)

**Responsabile**: Sviluppatore Senior
**Tempistica**: Sprint 0, fine (pubblica guida dev)
**DOR**: 2 nuove skill sviluppate usando nuovo contratto in <1h ciascuna

---

# 2. STRATEGIA DI TEST

## Unit Test

**Copertura Target**: ≥85% (moduli core)

### Sprint 0: Hardening
```
test_action_controller.py:
  - test_execute_action_success (15 scenari, 1 per tipo skill)
  - test_execute_action_invalid_skill (skill sconosciuta → fallback)
  - test_execute_action_timeout (skill si appende → errore)
  - test_skillresult_contract (tutti i campi presenti, tipizzati)
  - test_image_format_internal_bytes (nessuna conversione silenziosa)
  - test_image_format_last_mile_base64 (conversione solo su export)
  - test_tool_declaration_schema (conforme SDK Gemini)
  - test_error_handling_graceful (nessun leak eccezioni)

Totale: 15 unit test, ~200 righe
```

### Sprint 1: Memoria
```
test_memory_manager.py:
  - test_store_episodic_interaction (salva + recupera)
  - test_store_semantic_fact (permanente, decadimento confidenza)
  - test_hybrid_retrieval (top-3 episodici + top-1 semantico)
  - test_gemini_embedding_caching (tasso hit/miss)
  - test_ttl_cleanup_episodic (>30gg rimossi)
  - test_conflict_detection (episodico vs semantico)

Totale: 6 unit test, ~150 righe
```

### Sprint 2: Sogno Notturno
```
test_nightly_dream.py:
  - test_analyze_24h_memories (chiamata Gemini successo)
  - test_output_parsing_valid (JSON → strutturato)
  - test_output_parsing_invalid (fallimento graceful)
  - test_idempotent_file_write (nessun duplicato in continuous_improvements.md)
  - test_monthly_rotation (file YYYY_MM creati correttamente)
  - test_retry_policy (3 tentativi, ritardo 10min)

Totale: 6 unit test, ~150 righe
```

### Sprint 3: Smart Learning
```
test_smart_learning.py:
  - test_intent_inference_high_confidence (>0.85)
  - test_intent_inference_low_confidence (<0.85, rifiuta)
  - test_alias_save_and_retrieval (persisti + riusa)
  - test_alias_delete (rimuovi + verifica)
  - test_no_save_ambiguous (soglia confidenza)

Totale: 5 unit test, ~120 righe
```

### Sprint 4: Gate Automazione
```
test_gates_design.py:
  - test_gate_a_propose_patch (workflow approvazione mock)
  - test_gate_b_run_tests (smoke + regressione)
  - test_rollback_script (git revert + riavvio servizi)

Totale: 3 unit test, ~100 righe
```

---

## Integration Test

**Copertura**: Flussi End-to-end
**Ambiente**: Staging (Docker Compose)

### Sprint 0 Integration
```
test_action_integration.py:
  - Skill accendi luce (visione → azione → risultato)
  - Skill chiudi tapparelle (con gestione timeout)
  - Skill sconosciuta → dialogo fallback
  - 15 skill in sequenza (nessun side-effect)
```

### Sprint 1 Integration
```
test_memory_integration.py:
  - Salva 100 interazioni + recupera top-5 per query
  - Misurazione baseline Precision@3
  - Pulizia TTL (salva vecchia voce, verifica rimozione dopo 30gg)
```

### Sprint 2 Integration
```
test_nightly_integration.py:
  - Salva 24h interazioni
  - Esegui job sogno notturno
  - Verifica continuous_improvements.md generato + valido
```

### Sprint 3 Integration
```
test_smart_learning_integration.py:
  - Utente dice comando sconosciuto
  - Sistema inferisce intento + chiede conferma
  - Utente conferma + alias salvato
  - Utente ri-emette stesso comando → esegue via alias
```

### Sprint 4 Integration
```
test_gates_integration.py:
  - Crea patch + proponi via Gate A
  - Gate B: applica patch + esegui test
  - Rollback: revert patch + verifica stabile
```

---

## Regression Test

**Dataset**: 50 interazioni passate con esiti attesi documentati

```
regression_test_dataset.json:
[
  {
    "user_input": "Accendi la luce in salotto",
    "expected_intent": "turn_on",
    "expected_entity": "lights",
    "expected_entity_params": {"room": "living_room"},
    "expected_success": true,
    "test_sprints": ["0", "1", "3"]  // Ri-esegui in questi sprint
  },
  ...
]
```

**Esecuzione**:
- Prima di ogni sprint: stabilisci baseline
- Dopo ogni sprint: verifica no regressioni
- Metrica: accuratezza mantenuta al 90%+

---

## Safety Test

**Categoria**: Azioni ad alto rischio (validazione SkillResult)

```
test_safety.py:
  - Blocca porta senza conferma esplicita (RIFIUTA)
  - Controllo forno con timeout (OK se confermato)
  - Cancella dati con conferma (OK se ricevuto "sì")
  - Dispositivo sconosciuto (errore, nessuna azione autonoma)
```

---

# 3. PIPELINE CI/CD

## Workflow GitHub Actions

### File: `.github/workflows/marcus_pipeline.yml`

```yaml
name: MARCUS CI/CD

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}/marcus
  STAGING_HOST: staging.marcus.local
  PROD_HOST: prod.marcus.local

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      chromadb:
        image: chromadb/chroma
        ports:
          - 8000:8000
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
          cache: 'pip'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov pytest-timeout
      
      - name: Lint (flake8 + mypy)
        run: |
          flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
          mypy marcus/ --ignore-missing-imports
      
      - name: Unit tests
        run: |
          pytest tests/unit/ -v --cov=marcus --cov-report=xml --timeout=10
        env:
          CHROMADB_HOST: localhost
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      
      - name: Integration tests (staging)
        run: |
          docker-compose -f docker/staging.yml up -d
          sleep 5
          pytest tests/integration/ -v --timeout=30
          docker-compose -f docker/staging.yml down
        env:
          CHROMADB_HOST: localhost
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      
      - name: Regression tests
        run: |
          pytest tests/regression/ -v --cov-report=json
        env:
          CHROMADB_HOST: localhost
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
          fail_ci_if_error: true
      
      - name: Build Docker image
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        run: |
          docker build -t ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }} .
          docker push ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}
      
      - name: Deploy to staging (on main push)
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        run: |
          ssh -i ${{ secrets.STAGING_KEY }} ubuntu@${{ env.STAGING_HOST }} \
            "docker pull ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }} && \
             docker-compose -f /app/marcus/docker-compose.yml up -d"
      
      - name: Smoke test staging
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        run: |
          curl -f http://staging.marcus.local/health || exit 1
          pytest tests/smoke/ -v --host staging.marcus.local

  approval:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    
    steps:
      - name: Request approval for production
        uses: actions/github-script@v6
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: context.payload.pull_request.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: '🚀 Ready for production deploy. Requires approval from @owners.'
            })
      
      - name: Wait for approval (manual trigger)
        run: |
          echo "Waiting for manual approval via 'Deploy to Production' workflow..."
          # In practice, this is triggered via GitHub UI or second workflow
```

### File: `.github/workflows/deploy_production.yml`

```yaml
name: Deploy to Production

on:
  workflow_dispatch:  # Manual trigger
    inputs:
      confirm:
        description: 'Type CONFIRM to deploy'
        required: true

jobs:
  deploy:
    runs-on: ubuntu-latest
    
    steps:
      - name: Validate confirmation
        run: |
          [ "${{ github.event.inputs.confirm }}" == "CONFIRM" ] || exit 1
      
      - uses: actions/checkout@v3
      
      - name: Deploy to production
        run: |
          ssh -i ${{ secrets.PROD_KEY }} ubuntu@${{ env.PROD_HOST }} \
            "cd /app/marcus && \
             git fetch origin && \
             git checkout ${{ github.sha }} && \
             docker-compose up -d && \
             sleep 10 && \
             ./scripts/smoke_test.sh"
      
      - name: Verify production health
        run: |
          curl -f http://prod.marcus.local/health || exit 1
          # Additional health checks
      
      - name: Create deployment record
        run: |
          echo "Deployed ${{ github.sha }} at $(date)" >> DEPLOYMENTS.log
      
      - name: Notification (Slack)
        if: success()
        uses: slackapi/slack-github-action@v1
        with:
          webhook-url: ${{ secrets.SLACK_WEBHOOK }}
          payload: |
            {
              "text": "✅ MARCUS deployed to production (${{ github.sha }})"
            }
      
      - name: Rollback on failure
        if: failure()
        run: |
          ssh -i ${{ secrets.PROD_KEY }} ubuntu@${{ env.PROD_HOST }} \
            "cd /app/marcus && \
             git checkout main && \
             docker-compose down && \
             docker-compose up -d"
```

---

## Template Report Copertura Test

```
Coverage Report: Implementazione MARCUS

Sprint 0: Hardening
  ✅ test_action_controller.py: 15/15 passati
  ✅ Integration: 8/8 passati
  ✅ Code coverage: 87%
  ❌ 1 regressione (vecchio pattern chiamata API, risolto in Adapter)

Sprint 1: Memoria
  ✅ test_memory_manager.py: 6/6 passati
  ✅ Integration: 6/6 passati
  ✅ Precision@3 baseline: 0.65 (target: +20% → 0.78)
  ✅ Code coverage: 84%

Sprint 2: Sogno Notturno
  ✅ test_nightly_dream.py: 6/6 passati
  ✅ Integration: 5/5 passati (incluso scenario timeout)
  ✅ continuous_improvements.md generato + valido
  ✅ Policy retry: 3/3 test passati
  ✅ Code coverage: 82%

Sprint 3: Smart Learning
  ✅ test_smart_learning.py: 5/5 passati
  ✅ Integration: E2E test (3 comandi appresi, >90% successo)
  ✅ Cancellazione alias: funzionante
  ✅ Code coverage: 80%

Sprint 4: Gate
  ✅ test_gates_design.py: 3/3 passati
  ✅ Rollback: 10/10 reversioni con successo
  ✅ Code coverage: 78%

Totale Completo:
  Test totali: 47
  Passati: 46 (97.9%)
  Copertura: 82%
```

---

**Fine Documento Rischi + Test + CI**