"""
Robot AI Skills - Skill Generator Pipeline
============================================
Pipeline completa per la generazione, validazione e promozione di skill.

Fasi:
1. Analisi e preparazione contesto RAK
2. Generazione codice (tramite AI agent esterno)
3. Quality Gate (AST + Smoke Test + Sandbox)
4. Logging completo
4.5. Report di Fallimento (se 3/3 iterazioni falliscono)
5. Approvazione e staging controllato
6. Aggiornamento RAK post-approvazione
"""

import asyncio
import hashlib
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .security_validator import SecurityValidator, ValidationResult
from .skill_sandbox import SkillSandbox, SandboxResult
from .manifest_manager import ManifestManager


logger = logging.getLogger("robot_ai.skill_generator")


# ===========================================================================
# Data Classes
# ===========================================================================


@dataclass
class SkillRequest:
    """Richiesta di generazione skill."""
    name: str                          # Nome della skill (PascalCase)
    description: str                    # Descrizione funzionale
    capabilities: List[str] = field(default_factory=list)
    topics_sub: List[str] = field(default_factory=list)
    topics_pub: List[str] = field(default_factory=list)
    test_utterances: List[str] = field(default_factory=list)
    extra_context: str = ""            # Contesto aggiuntivo per il prompt

    @property
    def snake_name(self) -> str:
        """Converte PascalCase in snake_case (per filename)."""
        import re
        name = re.sub(r'(?<!^)(?=[A-Z])', '_', self.name).lower()
        # Garantisce il suffisso _skill
        if not name.endswith('_skill'):
            name += '_skill'
        return name


@dataclass
class IterationLog:
    """Log di una singola iterazione di generazione."""
    iteration: int
    timestamp: str
    prompt_sent: str
    code_generated: str
    ast_result: Optional[ValidationResult] = None
    smoke_result: Optional[str] = None
    sandbox_result: Optional[SandboxResult] = None
    action_taken: str = ""


@dataclass
class SkillGenerationResult:
    """Risultato finale della pipeline di generazione."""
    success: bool
    skill_name: str
    file_path: Optional[Path] = None
    iterations: int = 0
    iteration_logs: List[IterationLog] = field(default_factory=list)
    failure_report: Optional[str] = None
    manifest_entry: Optional[Dict[str, Any]] = None


# ===========================================================================
# Constants
# ===========================================================================

PROMPT_VERSION = "MARCUS_PROMPT_v2.1"
MAX_ITERATIONS = 3

SKILL_HEADER_TEMPLATE = """\
# =============================================================================
# SKILL: {class_name}
# Generata il:        {timestamp}
# Iterazione:         {iteration}/{max_iter}
# Versione prompt:    {prompt_version}
# Hash contesto RAK:  sha256:{rak_hash}
# Capability:         {capabilities}
# Topic usati:        SUB={topics_sub} PUB={topics_pub}
# Stato:              {status}
# =============================================================================

"""


# ===========================================================================
# Pipeline Core
# ===========================================================================


class SkillGeneratorPipeline:
    """
    Pipeline di generazione skill con validazione multi-fase.

    Uso tipico:
        pipeline = SkillGeneratorPipeline()
        request = SkillRequest(name="MiaSkill", description="Fa qualcosa")

        # Fase 1: Preparare il prompt
        prompt = pipeline.prepare_prompt(request)

        # Fase 2: Codice generato dall'AI agent (Gemini)
        code = "..."  # da Gemini

        # Fase 3-5: Validazione e promozione
        result = await pipeline.process_generated_code(request, code)
    """

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
        logs_dir: Optional[Path] = None,
    ):
        # Risolvi paths
        if workspace_root is None:
            # Risali dalla posizione di questo file (skills/) al package root (severus/)
            # skill_generator.py → skills/ → robot_ai/ → severus/
            workspace_root = Path(__file__).parent.parent.parent
        self.workspace_root = workspace_root

        if skills_dir is None:
            skills_dir = workspace_root / "robot_ai" / "skills"
        self.skills_dir = skills_dir

        if logs_dir is None:
            logs_dir = workspace_root / "logs"
        self.logs_dir = logs_dir

        # Ensure directories exist
        self.staging_dir = self.skills_dir / "staging"
        self.active_dir = self.skills_dir / "active"
        self.failed_dir = self.skills_dir / "failed"
        for d in [self.staging_dir, self.active_dir, self.failed_dir, self.logs_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self.validator = SecurityValidator()
        self.sandbox = SkillSandbox()
        self.manifest = ManifestManager(self.active_dir / "skills_manifest.json")

        self._iteration_logs: List[IterationLog] = []
        self._current_iteration = 0

    # ------------------------------------------------------------------
    # Fase 1: Preparazione contesto e prompt
    # ------------------------------------------------------------------

    def compute_rak_hash(self) -> str:
        """Calcola hash SHA256 dei file di contesto RAK."""
        content = ""
        for filename in ["ai_context.md", "files_topic.md", "WORKSPACE_STATE.md"]:
            filepath = self.workspace_root.parent / filename
            if filepath.exists():
                try:
                    content += filepath.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    pass
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def prepare_prompt(self, request: SkillRequest) -> str:
        """
        Costruisce il prompt completo per Gemini.

        Include:
        - Vincoli hardware RPi5
        - Contesto RAK (ai_context.md, TOPIC_MAP.md)
        - Contratto BaseSkill
        - Richiesta specifica
        - Regole non negoziabili per la generazione

        Returns:
            Prompt completo pronto per Gemini
        """
        # Leggi contesto RAK
        rak_context = self._read_rak_context()

        # Leggi base_skill.py
        base_skill_content = self._read_file(
            self.skills_dir / "base_skill.py"
        )

        # Leggi skill_registry.py (per capire il caricamento)
        registry_content = self._read_file(
            self.skills_dir / "skill_registry.py"
        )

        rak_hash = self.compute_rak_hash()

        prompt = f"""## RICHIESTA DI GENERAZIONE SKILL

### Vincoli Hardware (OBBLIGATORI)
Il sistema gira su Raspberry Pi 5, CPU ARM, 4-8 GB RAM, senza GPU.
Tutto il codice deve essere async, non bloccare callback ROS, usare queue bounded,
evitare copie inutili di array numpy/immagini. Timeout default 5s su chiamate esterne.
Logging solo via self.get_logger() se nel contesto ROS, altrimenti standard Python logging.

### Contesto RAK
Hash contesto: sha256:{rak_hash}

{rak_context}

### Contratto BaseSkill (da rispettare TASSATIVAMENTE)
```python
{base_skill_content}
```

### Dettagli Skill Richiesta
- **Nome classe:** {request.name}
- **Descrizione:** {request.description}
- **Capability:** {request.capabilities}
- **Topic SUB:** {request.topics_sub}
- **Topic PUB:** {request.topics_pub}
- **Utterances di test:** {request.test_utterances}
{f"- **Contesto aggiuntivo:** {request.extra_context}" if request.extra_context else ""}

### Regole tassative (NON NEGOZIABILI)
1. Restituire SOLO codice Python tra i tag <SKILL_CODE> e </SKILL_CODE>, senza markdown.
2. Nessun import da: os, sys, subprocess, shutil.
3. Nessun uso di: eval, exec, open, __import__.
4. Tutto il codice I/O deve essere async/await.
5. Nessuna allocazione non bounded di array/immagini.
6. Logging via logging (non self.get_logger(), dato che la skill non è un nodo ROS).
7. La classe DEVE ereditare da BaseSkill.
8. DEVE implementare: get_metadata() -> SkillMetadata, match(text, context) -> float, async execute(text, context) -> SkillResult
9. L'import di BaseSkill deve essere: from robopy_controller.robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability
10. Nessun print() — usare logging.

### Template da seguire
```python
from robopy_controller.robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability
from typing import Any, Dict, List
import asyncio
import logging

logger = logging.getLogger(__name__)

class {request.name}(BaseSkill):
    \"\"\"Descrizione.\"\"\"

    def __init__(self):
        super().__init__()

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="{request.snake_name}",
            description="...",
            keywords=[...],
            priority=5,
            capabilities=[...],
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        ...

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        ...
```
"""
        return prompt

    def prepare_repair_prompt(
        self,
        request: SkillRequest,
        previous_code: str,
        errors: List[str],
    ) -> str:
        """
        Costruisce un prompt di self-repair per Gemini.

        Args:
            request: Richiesta originale
            previous_code: Codice che ha fallito il Quality Gate
            errors: Lista errori rilevati

        Returns:
            Prompt di repair
        """
        error_text = "\n".join(f"  - {e}" for e in errors)

        return f"""## SELF-REPAIR: Correzione Skill {request.name}

Il codice generato ha fallito il Quality Gate. Correggi gli errori seguenti.

### Errori rilevati:
{error_text}

### Codice precedente (con errori):
```python
{previous_code}
```

### Regole (INVARIANTI):
1. Restituire SOLO codice Python tra <SKILL_CODE> e </SKILL_CODE>, senza markdown.
2. Correggere SOLO gli errori indicati, mantenere la logica originale.
3. Nessun import da: os, sys, subprocess, shutil.
4. Nessun uso di: eval, exec, open, __import__.
5. execute() DEVE essere async.
6. La classe DEVE ereditare da BaseSkill.
"""

    # ------------------------------------------------------------------
    # Fase 2-3: Processing del codice generato
    # ------------------------------------------------------------------

    async def process_generated_code(
        self,
        request: SkillRequest,
        raw_code: str,
        iteration: int = 1,
    ) -> SkillGenerationResult:
        """
        Processa il codice generato: estrae, valida, testa e logga.

        Args:
            request: Richiesta di generazione
            raw_code: Codice grezzo (può contenere tag <SKILL_CODE>)
            iteration: Numero iterazione corrente (1-based)

        Returns:
            SkillGenerationResult con esito completo
        """
        self._current_iteration = iteration
        timestamp = datetime.utcnow().isoformat()

        # Estrai codice dai tag
        code = self._extract_code(raw_code)
        if not code:
            code = raw_code  # Fallback: usa tutto come codice

        # Aggiungi header
        rak_hash = self.compute_rak_hash()
        header = SKILL_HEADER_TEMPLATE.format(
            class_name=request.name,
            timestamp=timestamp,
            iteration=iteration,
            max_iter=MAX_ITERATIONS,
            prompt_version=PROMPT_VERSION,
            rak_hash=rak_hash,
            capabilities=request.capabilities,
            topics_sub=request.topics_sub,
            topics_pub=request.topics_pub,
            status="STAGING",
        )
        full_code = header + code

        # Salva in staging
        staging_file = self.staging_dir / f"{request.snake_name}.py"
        staging_file.write_text(full_code, encoding='utf-8')
        logger.info(f"Skill salvata in staging: {staging_file}")

        # --- Quality Gate ---
        errors = []

        # 3a: Validazione AST
        ast_result = self.validator.validate(code)
        if not ast_result.is_valid:
            errors.extend(ast_result.errors)

        # 3b: Smoke Test (import isolato)
        smoke_error = None
        if ast_result.is_safe:
            smoke_error = await self._smoke_test(staging_file)
            if smoke_error:
                errors.append(f"Smoke test fallito: {smoke_error}")

        # 3c: Sandbox ROS
        sandbox_result = None
        if not errors:
            test_utt = request.test_utterances[0] if request.test_utterances else "test"
            sandbox_result = await self.sandbox.run(staging_file, test_utt)
            if not sandbox_result.success:
                errors.append(f"Sandbox fallita: {sandbox_result.error}")

        # --- Logging ---
        iter_log = IterationLog(
            iteration=iteration,
            timestamp=timestamp,
            prompt_sent="[vedi prompt preparato]",
            code_generated=code,
            ast_result=ast_result,
            smoke_result=smoke_error,
            sandbox_result=sandbox_result,
            action_taken="",
        )

        # Determina azione
        if errors:
            if iteration >= MAX_ITERATIONS:
                iter_log.action_taken = f"Abort - max iterazioni raggiunto ({MAX_ITERATIONS})"
                # Sposta in failed
                failed_file = self.failed_dir / f"{request.snake_name}.py"
                if staging_file.exists():
                    shutil.move(str(staging_file), str(failed_file))

                # Scrivi log e failure report
                self._write_iteration_log(request, iter_log)
                failure_report = self._write_failure_report(request, errors)

                return SkillGenerationResult(
                    success=False,
                    skill_name=request.name,
                    iterations=iteration,
                    iteration_logs=[iter_log],
                    failure_report=failure_report,
                )
            else:
                iter_log.action_taken = "Self-repair prompt necessario"
                self._write_iteration_log(request, iter_log)

                return SkillGenerationResult(
                    success=False,
                    skill_name=request.name,
                    iterations=iteration,
                    iteration_logs=[iter_log],
                    failure_report="\n".join(errors),
                )
        else:
            iter_log.action_taken = "Quality Gate superato - skill pronta per approvazione"
            self._write_iteration_log(request, iter_log)

            return SkillGenerationResult(
                success=True,
                skill_name=request.name,
                file_path=staging_file,
                iterations=iteration,
                iteration_logs=[iter_log],
            )

    # ------------------------------------------------------------------
    # Fase 5: Approvazione e promozione
    # ------------------------------------------------------------------

    def approve_skill(self, request: SkillRequest) -> Optional[Dict[str, Any]]:
        """
        Promuove una skill da staging ad active e aggiorna il manifest.

        Args:
            request: Richiesta di generazione

        Returns:
            Entry del manifest o None se il file non esiste
        """
        staging_file = self.staging_dir / f"{request.snake_name}.py"
        if not staging_file.exists():
            logger.error(f"File staging non trovato: {staging_file}")
            return None

        # Sposta in active
        active_file = self.active_dir / f"{request.snake_name}.py"
        shutil.move(str(staging_file), str(active_file))
        logger.info(f"Skill promossa: {staging_file} → {active_file}")

        # Aggiorna manifest
        entry = self.manifest.add_skill(
            name=request.name,
            file_name=f"{request.snake_name}.py",
            capabilities=request.capabilities,
            topics_sub=request.topics_sub,
            topics_pub=request.topics_pub,
            notes="In attesa di attivazione manuale",
        )

        return entry

    def enable_skill(self, name: str) -> bool:
        """Abilita una skill nel manifest."""
        return self.manifest.enable_skill(name)

    # ------------------------------------------------------------------
    # Fase 6: Aggiornamento RAK
    # ------------------------------------------------------------------

    def update_rak_for_skill(self, request: SkillRequest) -> bool:
        """
        Aggiorna TOPIC_MAP (files_topic.md) e WORKSPACE_STATE con la nuova skill.

        Args:
            request: Richiesta con topic sub/pub

        Returns:
            True se aggiornato con successo
        """
        try:
            # Aggiorna files_topic.md
            topic_map_path = self.workspace_root.parent / "files_topic.md"
            if topic_map_path.exists():
                content = topic_map_path.read_text(encoding='utf-8')

                # Aggiungi sezione per la nuova skill
                new_section = f"\n## robopy_controller\\robot_ai\\skills\\active\\{request.snake_name}.py\n"
                if request.topics_sub:
                    new_section += "- **Subscribes to:**\n"
                    for topic in request.topics_sub:
                        new_section += f"  - `{topic}`\n"
                else:
                    new_section += "- **Subscribes to:** None\n"

                if request.topics_pub:
                    new_section += "- **Transmits (Publishes) to:**\n"
                    for topic in request.topics_pub:
                        new_section += f"  - `{topic}`\n"
                else:
                    new_section += "- **Transmits (Publishes) to:** None\n"

                # Evita duplicati
                check_header = f"## robopy_controller\\robot_ai\\skills\\active\\{request.snake_name}.py"
                if check_header not in content:
                    content += new_section
                    topic_map_path.write_text(content, encoding='utf-8')
                    logger.info(f"files_topic.md aggiornato per {request.name}")

            # Aggiorna WORKSPACE_STATE.md
            ws_path = self.workspace_root.parent / "WORKSPACE_STATE.md"
            if ws_path.exists():
                ws_content = ws_path.read_text(encoding='utf-8')
                new_entry = f"  - `robot_ai/skills/active/{request.snake_name}.py`"
                if new_entry not in ws_content:
                    # Trova la sezione skills e aggiungi
                    ws_content += f"\n{new_entry}\n"
                    ws_path.write_text(ws_content, encoding='utf-8')
                    logger.info(f"WORKSPACE_STATE.md aggiornato per {request.name}")

            return True

        except Exception as e:
            logger.error(f"Errore aggiornamento RAK: {e}")
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_rak_context(self) -> str:
        """Legge il contesto RAK (ai_context.md, files_topic.md)."""
        context_parts = []
        for filename in ["ai_context.md", "files_topic.md"]:
            content = self._read_file(self.workspace_root.parent / filename)
            if content:
                context_parts.append(f"=== {filename} ===\n{content[:3000]}")  # Limita dimensione
        return "\n\n".join(context_parts) if context_parts else "[Contesto RAK non disponibile]"

    def _read_file(self, path: Path) -> str:
        """Legge un file in modo sicuro."""
        try:
            if path.exists():
                return path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"Impossibile leggere {path}: {e}")
        return ""

    def _extract_code(self, raw: str) -> Optional[str]:
        """Estrae codice dai tag <SKILL_CODE>...</SKILL_CODE>."""
        import re
        match = re.search(
            r'<SKILL_CODE>\s*(.*?)\s*</SKILL_CODE>',
            raw,
            re.DOTALL,
        )
        return match.group(1) if match else None

    async def _smoke_test(self, file_path: Path) -> Optional[str]:
        """
        Smoke test: tenta l'import del modulo con timeout.

        Returns:
            None se OK, stringa errore se fallimento
        """
        import importlib.util
        import concurrent.futures

        def _do_import():
            try:
                module_name = f"smoke_test_{file_path.stem}_{int(time.time())}"
                spec = importlib.util.spec_from_file_location(module_name, str(file_path))
                if spec is None or spec.loader is None:
                    return "Impossibile creare spec per il modulo"
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return None  # Success
            except Exception as e:
                return str(e)

        try:
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = await asyncio.wait_for(
                    loop.run_in_executor(pool, _do_import),
                    timeout=5.0,
                )
            return result
        except asyncio.TimeoutError:
            return "Timeout durante l'import (>5s)"
        except Exception as e:
            return str(e)

    def _write_iteration_log(self, request: SkillRequest, log: IterationLog):
        """Scrive il log di una iterazione su disco."""
        log_file = self.logs_dir / f"LOG_{request.snake_name}_{log.iteration:03d}.txt"

        ast_section = "N/A"
        if log.ast_result:
            ast_section = f"Esito: {'PASS' if log.ast_result.is_safe else 'FAIL'}\n"
            if log.ast_result.errors:
                ast_section += "Errori:\n" + "\n".join(f"  - {e}" for e in log.ast_result.errors)

        smoke_section = "N/A"
        if log.smoke_result is not None:
            smoke_section = f"Esito: FAIL\nDettaglio: {log.smoke_result}"
        elif log.ast_result and log.ast_result.is_safe:
            smoke_section = "Esito: PASS"

        sandbox_section = "N/A"
        if log.sandbox_result:
            sandbox_section = log.sandbox_result.summary()

        content = f"""=== LOG SKILL GENERATOR ===
Skill:          {request.name}
Iterazione:     {log.iteration}/{MAX_ITERATIONS}
Timestamp:      {log.timestamp}
Prompt Marcus:  {PROMPT_VERSION}

--- PROMPT INVIATO A GEMINI ---
{log.prompt_sent}

--- CODICE GENERATO DA GEMINI ---
{log.code_generated}

--- RISULTATO QUALITY GATE AST ---
{ast_section}

--- RISULTATO SMOKE TEST ---
{smoke_section}

--- RISULTATO SANDBOX ROS ---
{sandbox_section}

--- AZIONE INTRAPRESA ---
{log.action_taken}
"""
        log_file.write_text(content, encoding='utf-8')
        logger.info(f"Log scritto: {log_file}")

    def _write_failure_report(
        self, request: SkillRequest, final_errors: List[str]
    ) -> str:
        """Scrive il report di fallimento completo."""
        report_file = self.logs_dir / f"FAILURE_REPORT_{request.snake_name}.txt"

        # Raccogli errori da tutti i log esistenti
        iter_summaries = []
        for i in range(1, MAX_ITERATIONS + 1):
            log_path = self.logs_dir / f"LOG_{request.snake_name}_{i:03d}.txt"
            if log_path.exists():
                iter_summaries.append(f"  - Iter {i}: vedi {log_path.name}")
            else:
                iter_summaries.append(f"  - Iter {i}: log non disponibile")

        iter_text = "\n".join(iter_summaries)
        errors_text = "\n".join(f"  - {e}" for e in final_errors)

        content = f"""=== REPORT DI FALLIMENTO GENERAZIONE SKILL ===
Skill richiesta:   {request.name}
Data:              {datetime.utcnow().isoformat()}Z
Iterazioni totali: {MAX_ITERATIONS}/{MAX_ITERATIONS}

RIEPILOGO ERRORI PER ITERAZIONE:
{iter_text}

ERRORI FINALI:
{errors_text}

AZIONI SUGGERITE PER INTERVENTO MANUALE:
1. Consultare i file di log per i traceback completi
2. Verificare che i topic richiesti esistano in files_topic.md
3. Rivedere le capability richieste
4. Provare a riformulare la richiesta con più contesto

File di log disponibili:
"""
        for i in range(1, MAX_ITERATIONS + 1):
            content += f"  - logs/LOG_{request.snake_name}_{i:03d}.txt\n"

        report_file.write_text(content, encoding='utf-8')
        logger.info(f"Failure report scritto: {report_file}")
        return content

    # ------------------------------------------------------------------
    # Convenience: full pipeline run
    # ------------------------------------------------------------------

    async def run_full_pipeline(
        self,
        request: SkillRequest,
        code_provider,  # Callable[[str], Awaitable[str]] - funzione che chiama Gemini
    ) -> SkillGenerationResult:
        """
        Esegue l'intera pipeline di generazione con retry automatico.

        Args:
            request: Richiesta di generazione
            code_provider: Async callable che riceve un prompt e restituisce il codice

        Returns:
            SkillGenerationResult finale
        """
        last_result = None
        last_code = ""
        errors = []

        for iteration in range(1, MAX_ITERATIONS + 1):
            # Prepara prompt
            if iteration == 1:
                prompt = self.prepare_prompt(request)
            else:
                prompt = self.prepare_repair_prompt(
                    request, last_code, errors
                )

            # Ottieni codice
            try:
                raw_code = await code_provider(prompt)
            except Exception as e:
                logger.error(f"Code provider fallito iter {iteration}: {e}")
                continue

            last_code = raw_code

            # Processa
            result = await self.process_generated_code(
                request, raw_code, iteration
            )

            if result.success:
                return result

            # Raccogli errori per la prossima iterazione
            errors = []
            if result.failure_report:
                errors = result.failure_report.split("\n")
            last_result = result

        return last_result or SkillGenerationResult(
            success=False,
            skill_name=request.name,
            iterations=MAX_ITERATIONS,
            failure_report="Pipeline terminata senza risultati",
        )
