# RUNBOOK OPERATIVO MARCUS

---

# 1. FEATURE FLAGS E CONFIGURAZIONE

## File Configurazione Primario: `marcus_config.yaml`

```yaml
# marcus_config.yaml (esempio)

system:
  environment: "development"  # development | staging | production
  log_level: "INFO"
  data_retention_days: 30

hardening:
  enabled: true
  unified_action_path: true
  skill_result_contract_strict: true

memory:
  enabled: true
  store_type: "chromadb"
  chromadb:
    host: "localhost"
    port: 8000
    persistence_dir: "/data/chromadb"
  
  episodic:
    ttl_days: 30
    cleanup_job_hour: 3  # 3 AM giornaliero
    max_entries: 100000
  
  semantic:
    retention: "permanent"
    confidence_decay_weeks: 12
  
  embedding:
    model: "gemini-embedding-001"
    dimension: 768
    cache_size: 100  # LRU entries
    batch_size: 10
    rate_limit:
      max_requests_per_minute: 1500
      backoff_strategy: "exponential"

nightly_dream:
  enabled: true  # false in produzione inizialmente
  run_time: "02:00"  # 2 AM UTC
  model: "gemini-3-pro"
  max_calls_per_run: 1
  chunking:
    enabled: true
    chunk_hours: 6
    max_chunks_per_night: 4
  
  output_file: "continuous_improvements.md"
  backup_file: "continuous_improvements_BACKUP.md"
  
  retry:
    max_attempts: 3
    delay_minutes: 10
    backoff_multiplier: 2.0
  
  rotation:
    strategy: "monthly"  # Mantieni file attivo + archivi mensili
    archive_pattern: "continuous_improvements_%Y_%m.md"

smart_learning:
  enabled: true
  intent_inference:
    model: "gemini-3-pro"
    confidence_threshold: 0.85
  
  alias_storage:
    type: "sqlite"
    path: "/data/learned_aliases.db"
  
  user_confirmation:
    required: true
    prompt_style: "conversational"
    timeout_seconds: 30

automation_gates:
  enabled: false  # Attivare solo dopo review design Sprint 4
  gate_a:
    enabled: false
    approval_channel: "slack"  # o "email"
  
  gate_b:
    enabled: false
    test_environment: "staging"
    max_duration_minutes: 30
  
  gate_c:
    enabled: false  # Differito
    production_deploy: false

api:
  gemini:
    model: "gemini-3-pro"
    max_retries: 3
    timeout_seconds: 30
    temperature: 0.7
  
  auth:
    api_key_env: "GEMINI_API_KEY"

security:
  pii_anonymization:
    enabled: true
    hash_names: true
    mask_phone: true
    mask_email: true
  
  audit_logging:
    enabled: true
    log_file: "/var/log/marcus/audit.log"
  
  access_control:
    user_filtering: true

monitoring:
  prometheus:
    enabled: true
    port: 9090
  
  metrics:
    track_retrieval_latency: true
    track_embedding_calls: true
    track_nightly_duration: true
    track_smart_learning_confidence: true
```

---

## Variabili d'Ambiente

```bash
# .env.example

# Gemini API
GEMINI_API_KEY="tua-api-key-qui"
GEMINI_MODEL="gemini-3-pro"

# ChromaDB
CHROMADB_HOST="localhost"
CHROMADB_PORT="8000"

# Storage
DATA_DIR="/data/marcus"
LOG_DIR="/var/log/marcus"

# Nightly Dream
NIGHTLY_DREAM_ENABLED="true"
NIGHTLY_DREAM_RUN_TIME="02:00"

# Smart Learning
SMART_LEARNING_ENABLED="true"
SMART_LEARNING_CONFIDENCE_THRESHOLD="0.85"

# Automation Gates
GATES_ENABLED="false"
GATES_APPROVAL_CHANNEL="slack"

# Monitoring
PROMETHEUS_ENABLED="true"
LOG_LEVEL="INFO"
```

---

# 2. PROCEDURE DI DEPLOYMENT

## 2.1 Deployment Sviluppo/Prototipo

### Prerequisiti
```bash
# Verifica Python 3.10+
python --version  # 3.10.x

# Clona repo
git clone https://github.com/tua-org/marcus.git
cd marcus

# Crea virtualenv
python -m venv venv
source venv/bin/activate

# Installa dipendenze
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Copia template config
cp marcus_config.yaml.example marcus_config.yaml

# Imposta variabili env
cp .env.example .env
# Modifica .env con la tua GEMINI_API_KEY
```

### Avvia Servizi (Docker Compose)

```bash
# docker-compose.dev.yml - include ChromaDB, logging

docker-compose -f docker/docker-compose.dev.yml up -d

# Verifica servizi
curl http://localhost:8000/api/v1/heartbeat  # ChromaDB
curl http://localhost:8000/health            # MARCUS API

# Controlla log
docker-compose -f docker/docker-compose.dev.yml logs -f marcus
```

### Esegui Test Hardening (Sprint 0)

```bash
# Unit test
pytest tests/unit/test_action_controller.py -v

# Integration test
pytest tests/integration/test_action_integration.py -v

# Atteso: 15/15 passati, <2min totale
```

### Attiva Sogno Notturno (Manuale - Prototipo)

```bash
# Linea di comando (per test)
MARCUS_NIGHTLY_DREAM_NOW=1 python -m marcus.jobs.nightly_dream

# Interfaccia skill ROS (se integrata)
ros2 service call /marcus_nightly_dream std_srvs/srv/Trigger "{}"

# Output atteso:
# continuous_improvements.md generato in /data/marcus/continuous_improvements.md
```

### Attiva Smart Learning (Manuale - Test)

```bash
# Simula comando sconosciuto
echo "turn_on_the_lamp_in_bedroom" | python -m marcus.skills.smart_learning

# Atteso:
# Sistema: "Intendevi: accendere luci in camera da letto? (Di sì/no)"
# Utente: "sì"
# Sistema: "Capito! La prossima volta capirò 'turn_on_the_lamp_in_bedroom'"
```

---

## 2.2 Deployment Staging

### Compila Immagine Docker

```bash
# Compila immagine
docker build -t marcus:$(git rev-parse --short HEAD) .

# Tag per registry staging
docker tag marcus:$(git rev-parse --short HEAD) \
  staging.registry.local/marcus:$(git rev-parse --short HEAD)

# Push
docker push staging.registry.local/marcus:$(git rev-parse --short HEAD)
```

### Deploy su Staging

```bash
# SSH su host staging
ssh ubuntu@staging.marcus.local

# Naviga dir app
cd /app/marcus

# Pull ultimo codice
git fetch origin && git checkout main

# Pull ultima immagine
docker pull staging.registry.local/marcus:$(git rev-parse --short HEAD)

# Abbatti vecchi servizi
docker-compose -f docker/docker-compose.staging.yml down

# Alza nuovi servizi
docker-compose -f docker/docker-compose.staging.yml up -d

# Attendi health
sleep 10
curl -f http://localhost:8000/health || exit 1
```

### Esegui Smoke Test

```bash
# Su host staging
cd /app/marcus
pytest tests/smoke/ -v --host localhost:8000

# Atteso: Tutti gli smoke test passati
```

### Esegui Regression Test

```bash
pytest tests/regression/ -v --dataset tests/data/regression_dataset.json

# Atteso: accuratezza 90%+ mantenuta
```

---

## 2.3 Deployment Produzione

### Checklist Pre-Deployment

```bash
# 1. Tutti i test passati su branch main?
git log --oneline main -n 5
# Verifica CI/CD pipeline: ✅ tutto verde

# 2. Code review completata?
git log --format="%H %an" main -n 1
# Verifica approvazione da senior engineer

# 3. Feature flag corretti?
grep -A 10 "automation_gates:" marcus_config.yaml | grep "enabled: false"
# Deve essere false per produzione inizialmente

# 4. Backup database esistente
ssh ubuntu@prod.marcus.local \
  "docker exec marcus-chromadb /bin/bash -c \
   'tar czf /backups/chromadb_$(date +%s).tar.gz /chroma_data'"

# 5. Notifica team
# Messaggio Slack: "🚀 MARCUS deploy produzione in corso. ETA 10min. Piano rollback: git revert + docker-compose restart"
```

### Comando Deploy (Approvazione Manuale)

```bash
# Richiede approvazione esplicita via UI GitHub on trigger manuale

# Trigger via GitHub Actions (workflow_dispatch)
gh workflow run deploy_production.yml -f confirm=CONFIRM

# O manualmente:
ssh ubuntu@prod.marcus.local << 'EOF'
cd /app/marcus

# Pull ultimo
git fetch origin && git checkout main

# Stop vecchi servizi
docker-compose -f docker/docker-compose.prod.yml down

# Pull nuova immagine
docker pull registry.local/marcus:$(git rev-parse --short HEAD)

# Start nuovi servizi
docker-compose -f docker/docker-compose.prod.yml up -d

# Verifica health
sleep 15
curl -f http://localhost:8000/health

# Log deployment
echo "Deployed $(git rev-parse --short HEAD) at $(date)" >> /var/log/marcus/DEPLOYMENTS.log
EOF
```

### Validazione Post-Deployment

```bash
# 1. Health check
curl -f http://prod.marcus.local/health

# 2. Esecuzione skill campione
# Test: accendi luce
ros2 service call /marcus_execute_skill ... --request '{"skill": "turn_on_light", ...}'

# 3. Recupero memoria
# Query memoria per interazione recente
curl -X POST http://prod.marcus.local/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query": "last interaction", "limit": 5}'

# 4. Stato job notturno
curl http://prod.marcus.local/jobs/nightly_dream/status

# 5. Avviso team monitoraggio
# Slack: "✅ MARCUS deploy produzione riuscito. Monitoraggio..."
```

---

# 3. PROCEDURE DI ROLLBACK

## 3.1 Rollback via Git Revert (Più veloce)

```bash
# Su host produzione
cd /app/marcus

# Ottieni hash commit precedente
PREV_COMMIT=$(git log --oneline -n 2 | tail -n 1 | awk '{print $1}')

# Revert al commit precedente
git revert --no-edit $PREV_COMMIT
git push origin main

# Pull ultimo (dovrebbe essere commit revertito)
git fetch origin && git checkout main

# Riavvia servizi
docker-compose -f docker/docker-compose.prod.yml down
docker-compose -f docker/docker-compose.prod.yml up -d

# Verifica
sleep 10
curl -f http://localhost:8000/health

# Log rollback
echo "Rolled back to $PREV_COMMIT at $(date)" >> /var/log/marcus/DEPLOYMENTS.log
```

## 3.2 Rollback via Backup Database (Recupero Perdita Dati)

```bash
# Se avvenuta corruzione dati durante deployment

# Stop servizi
docker-compose -f docker/docker-compose.prod.yml down

# Ripristina backup
LATEST_BACKUP=$(ls -t /backups/chromadb_*.tar.gz | head -n 1)
tar xzf $LATEST_BACKUP -C /

# Riavvia
docker-compose -f docker/docker-compose.prod.yml up -d

# Verifica
curl -f http://localhost:8000/health
```

## 3.3 Rollback Parziale (Feature Flag)

```bash
# Se una feature (es. Sogno Notturno) è rotta

# Modifica config
ssh ubuntu@prod.marcus.local
vi /app/marcus/marcus_config.yaml

# Imposta feature rotta a enabled: false
# Esempio:
# nightly_dream:
#   enabled: false  # <-- Modificato

# Ricarica config (nessun riavvio necessario se hot-reload implementato)
curl -X POST http://localhost:8000/config/reload

# O riavvia servizi
docker-compose -f docker/docker-compose.prod.yml restart marcus

# Verifica
curl http://localhost:8000/config/status | grep nightly_dream
```

## 3.4 Rollback Automatico (Se Health Check Fallisce)

```bash
# GitHub Actions farà rollback automatico se smoke test post-deploy fallisce

# Meccanismo (in deploy_production.yml):
# 1. Deploy immagine
# 2. Esegui smoke test
# 3. Se un test fallisce:
#    - git revert
#    - docker-compose restart
#    - notifica team

# Esempio dal workflow:
# - name: Rollback on failure
#   if: failure()
#   run: |
#     ssh ubuntu@prod.marcus.local \
#       "cd /app/marcus && git revert HEAD && docker-compose restart"
```

---

# 4. ATTIVAZIONE SOGNO NOTTURNO

## 4.1 Abilita Sogno Notturno (Fase Prototipo)

```bash
# Modifica config
vi marcus_config.yaml

# Cambia:
nightly_dream:
  enabled: true  # <-- Cambiato da false
  run_time: "02:00"
  ...
```

## 4.2 Trigger Manuale (Testing)

```bash
# Linea di comando
MARCUS_NIGHTLY_DREAM_NOW=1 python -m marcus.jobs.nightly_dream

# Output atteso (stdout):
# [2025-02-13 14:23:45] Starting nightly dream analysis...
# [2025-02-13 14:23:46] Loaded 247 episodic memories (last 24h)
# [2025-02-13 14:23:52] Gemini analysis complete
# [2025-02-13 14:23:53] continuous_improvements.md written (2.4 KB)
# [2025-02-13 14:23:54] Semantic summary saved
# [2025-02-13 14:23:55] ✅ Nightly dream completed successfully

# O via ROS
ros2 service call /marcus/nightly_dream std_srvs/srv/Trigger "{}"

# Risposta:
# success: true
# message: "Nightly dream analysis completed. 247 memories analyzed."
```

## 4.3 Esecuzione Schedulata (Cron + APScheduler)

```python
# In marcus/jobs/scheduler.py

from apscheduler.schedulers.background import BackgroundScheduler
from marcus.jobs.nightly_dream import run_nightly_dream

scheduler = BackgroundScheduler()

# Schedula sogno notturno alle 02:00 UTC giornalmente
scheduler.add_job(
    run_nightly_dream,
    'cron',
    hour=2,
    minute=0,
    id='nightly_dream',
    replace_existing=True
)

scheduler.start()
```

---

# 5. ATTIVAZIONE SMART LEARNING

## 5.1 Abilita Smart Learning

```bash
vi marcus_config.yaml

smart_learning:
  enabled: true
  intent_inference:
    confidence_threshold: 0.85
```

## 5.2 Test Smart Learning

```bash
# Simula comando sconosciuto

python -c "
from marcus.skills.smart_learning import learn_new_command

result = learn_new_command(
    user_input='activate the ceiling fan in the bedroom',
    user_confirmed=False  # Primo passo: genera solo ipotesi
)

# Output atteso:
# {
#   'inferred_intent': 'turn_on',
#   'inferred_entity': 'fan',
#   'inferred_params': {'location': 'bedroom', 'target': 'ceiling_fan'},
#   'confidence': 0.92,
#   'ask_confirmation': True,
#   'confirmation_prompt': 'Did you mean: Turn ON the ceiling FAN in BEDROOM?'
# }
"

# Utente conferma
python -c "
from marcus.skills.smart_learning import save_alias

save_alias(
    user_input='activate the ceiling fan in the bedroom',
    intent='turn_on',
    entity='fan',
    params={'location': 'bedroom'},
)

# Atteso:
# ✅ Alias saved: 'activate the ceiling fan in the bedroom' → turn_on(fan, location=bedroom)
"

# Test riutilizzo
python -c "
from marcus.skills.smart_learning import execute_learned_command

result = execute_learned_command('activate the ceiling fan in the bedroom')
# Atteso:
# ✅ Executed via learned alias (confidence: 0.92)
# Result: turn_on(fan, location=bedroom) → success
"
```

---

# 6. MONITORAGGIO & OSSERVABILITÀ

## 6.1 Metriche Chiave (Prometheus)

```bash
# Endpoint Prometheus: http://localhost:9090

# Esempi query:

# 1. Latenza recupero (p95)
histogram_quantile(0.95, rate(marcus_retrieval_latency_ms[5m]))

# 2. Chiamate API Embedding al minuto
rate(marcus_gemini_embedding_calls_total[1m])

# 3. Durata sogno notturno
marcus_nightly_dream_duration_ms

# 4. Confidenza smart learning
marcus_smart_learning_confidence{quantile="p90"}

# 5. Tasso successo esecuzione azioni
rate(marcus_action_success_total[5m]) / rate(marcus_action_total[5m])
```

## 6.2 Regole Alerting

```yaml
# prometheus/rules.yml

groups:
  - name: marcus_alerts
    rules:
      - alert: HighRetrievalLatency
        expr: histogram_quantile(0.95, rate(marcus_retrieval_latency_ms[5m])) > 500
        for: 5m
        annotations:
          summary: "Retrieval latency >500ms p95"
      
      - alert: NightlyDreamFailed
        expr: rate(marcus_nightly_dream_failures_total[1h]) > 0
        for: 1m
        annotations:
          summary: "Nightly dream job failed"
      
      - alert: SmartLearningLowConfidence
        expr: marcus_smart_learning_confidence{quantile="p50"} < 0.70
        for: 10m
        annotations:
          summary: "Smart learning confidence dropped"
```

---

# 7. RISOLUZIONE PROBLEMI (TROUBLESHOOTING)

## Problema: Timeout Sogno Notturno

```bash
# Sintomi: Job parte ma si blocca dopo 60s

# Controlla log
docker logs marcus-gemini | grep "nightly_dream" | tail -20

# Errore atteso:
# [ERROR] Gemini API timeout after 30s, retrying...
# [INFO] Retry 1/3: waiting 10 minutes before next attempt

# Soluzione: Abilita chunking
# Modifica marcus_config.yaml:
# nightly_dream:
#   chunking:
#     enabled: true
#     chunk_hours: 6  # Analizza finestre di 6h invece di 24h in una volta

# Riavvia
docker-compose restart marcus
```

## Problema: Smart Learning Salva Alias Errati

```bash
# Sintomi: Utente conferma intento sbagliato, sistema lo impara persistentemente

# Controlla log
sqlite3 /data/learned_aliases.db "SELECT * FROM aliases WHERE created_at > datetime('now', '-1 day');"

# Trova alias sbagliato
| user_input | intent | confidence | created_at |
| "turn off the light" | "turn_on" | 0.82 | 2025-02-13 14:00 |  <-- ERRATO

# Cancellalo
python -c "
from marcus.skills.smart_learning import delete_alias
delete_alias('turn off the light')
# ✅ Alias removed
"

# Causa radice: Soglia confidenza 0.82 > 0.85? No, avrebbe dovuto rifiutare.
# Controlla: modello inferenza intento potrebbe essere cambiato.
# Mitigazione: Alza soglia a 0.90
vi marcus_config.yaml
# smart_learning:
#   intent_inference:
#     confidence_threshold: 0.90  # Era 0.85
```

---

**Fine Runbook**