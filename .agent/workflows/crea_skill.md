---
description: Genera una nuova skill per il robot Marcus usando la pipeline skill_generator
---

// turbo-all

# 🤖 WORKFLOW: Generazione Skill Marcus

Questo workflow descrive come usare la pipeline `skill_generator.py` per generare
una nuova skill ROS2 per il robot Marcus.

## Prerequisiti

- Leggere SEMPRE @build.md per il contesto del progetto
- Leggere SEMPRE @ai_context.md per i vincoli hardware
- Leggere SEMPRE @files_topic.md per verificare i topic ROS2 disponibili

---

## Fasi Operative

### 1. Analisi della Richiesta

Comprendere cosa l'utente vuole che la skill faccia:
- Quale funzionalità implementa?
- Quali topic ROS2 servono (verificare su files_topic.md)?
- Quali capability sono necessarie (HA_READ, HA_WRITE, NAV_MOVE, CAMERA_READ, ecc.)?
- Quali frasi di test (utterances) l'utente potrebbe dire?

### 2. Preparare la SkillRequest

Creare una `SkillRequest` con i parametri corretti:

```python
from robopy_controller.robot_ai.skills.skill_generator import (
    SkillGeneratorPipeline, SkillRequest
)

request = SkillRequest(
    name="NomeSkillPascalCase",
    description="Descrizione chiara della skill",
    capabilities=["ha.write"],  # Capability dal enum Capability
    topics_sub=[],               # Topic sottoscritti
    topics_pub=["/ai/conversation/response"],  # Topic pubblicati
    test_utterances=["frase test uno", "frase test due"],
    extra_context="Eventuali dettagli aggiuntivi"
)
```

### 3. Generare il Prompt

```python
pipeline = SkillGeneratorPipeline()
prompt = pipeline.prepare_prompt(request)
```

Il prompt contiene:
- Vincoli hardware RPi5
- Contesto RAK (ai_context.md, TOPIC_MAP)
- Contratto BaseSkill completo
- Template skill da seguire
- Regole non negoziabili

### 4. Generare il Codice

Usare il prompt per generare il codice Python della skill.
Il codice DEVE essere racchiuso tra `<SKILL_CODE>` e `</SKILL_CODE>`.

### 5. Validare e Testare

```python
import asyncio

result = asyncio.run(pipeline.process_generated_code(
    request=request,
    raw_code=codice_generato,
    iteration=1
))
```

Se `result.success == False`:
- Leggere `result.failure_report` per gli errori
- Generare un repair prompt: `pipeline.prepare_repair_prompt(request, codice, errori)`
- Ripetere dalla fase 4 (max 3 iterazioni)

### 6. Approvazione

Se il Quality Gate è superato, chiedere all'utente se vuole approvare:

```python
manifest_entry = pipeline.approve_skill(request)
```

Questo:
- Sposta il file da `staging/` a `active/`
- Aggiorna `skills_manifest.json` con `enabled: false`

### 7. Attivazione

L'utente attiva la skill sul robot con:
```bash
ros2 topic pub /ai/input/text std_msgs/msg/String "{data: 'attiva skill NomeSkill'}" -1
```

Oppure modificando `enabled: true` nel manifest.

### 8. Aggiornamento RAK

Dopo l'approvazione:
```python
pipeline.update_rak_for_skill(request)
```

---

## Struttura File Generati

```
robopy_controller/robot_ai/skills/
├── staging/          ← skill in fase di validazione
├── active/
│   ├── skills_manifest.json  ← registro centrale
│   └── nome_skill.py         ← skill approvate
├── failed/           ← skill che non hanno superato i controlli
├── security_validator.py     ← validatore AST
├── skill_sandbox.py          ← sandbox test isolato
├── skill_generator.py        ← pipeline generazione
└── manifest_manager.py       ← gestore manifest

robopy_controller/logs/
├── LOG_nome_skill_001.txt    ← log iterazione 1
├── LOG_nome_skill_002.txt    ← log iterazione 2
├── LOG_nome_skill_003.txt    ← log iterazione 3
└── FAILURE_REPORT_nome_skill.txt  ← report fallimento
```

## Regole d'Oro

1. **MAI** generare import da: os, sys, subprocess, shutil
2. **MAI** usare: eval, exec, open, __import__
3. **SEMPRE** async su I/O
4. **SEMPRE** timeout 5s su chiamate esterne
5. **SEMPRE** ereditare da BaseSkill con get_metadata(), match(), async execute()
6. **MAI** print() — usare logging
7. Il file in `active/` deve avere l'header obbligatorio
