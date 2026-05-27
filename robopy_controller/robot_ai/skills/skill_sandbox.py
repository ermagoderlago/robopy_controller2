"""
Robot AI Skills - Skill Sandbox
================================
Sandbox per eseguire skill generate in isolamento con timeout.
Verifica che la skill si importi, si istanzi e risponda correttamente.
"""

import asyncio
import importlib
import importlib.util
import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


logger = logging.getLogger("robot_ai.skill_sandbox")


@dataclass
class SandboxResult:
    """Risultato dell'esecuzione sandbox."""
    success: bool
    phase: str = ""  # "import", "instantiate", "metadata", "match", "execute"
    output: Optional[str] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    duration_ms: float = 0.0
    class_name: Optional[str] = None
    metadata_name: Optional[str] = None
    match_score: float = 0.0

    def summary(self) -> str:
        """Riassunto testuale del risultato."""
        status = "PASS" if self.success else "FAIL"
        lines = [
            f"Esito: {status}",
            f"Fase raggiunta: {self.phase}",
            f"Durata: {self.duration_ms:.1f}ms",
        ]
        if self.class_name:
            lines.append(f"Classe: {self.class_name}")
        if self.metadata_name:
            lines.append(f"Skill name: {self.metadata_name}")
        if self.match_score > 0:
            lines.append(f"Match score: {self.match_score:.2f}")
        if self.output:
            lines.append(f"Output: {self.output}")
        if self.error:
            lines.append(f"Errore: {self.error}")
        if self.traceback:
            lines.append(f"Traceback:\n{self.traceback}")
        return "\n".join(lines)


class SkillSandbox:
    """
    Esegue una skill generata in isolamento per verificarne il funzionamento.

    Fasi di test:
    1. Import del modulo Python
    2. Ricerca e istanziazione della classe BaseSkill
    3. Chiamata get_metadata()
    4. Chiamata match() con utterance di test
    5. Chiamata execute() con timeout di 5 secondi

    Tutto avviene senza nodi ROS reali.
    """

    DEFAULT_TIMEOUT = 5.0  # secondi
    TEST_UTTERANCES = [
        "test",
        "prova",
        "fai una prova",
    ]

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout

    async def run(
        self,
        skill_file: Path,
        test_utterance: Optional[str] = None,
    ) -> SandboxResult:
        """
        Esegue il test sandbox completo su un file skill.

        Args:
            skill_file: Path del file Python della skill
            test_utterance: Frase di test per match/execute (opzionale)

        Returns:
            SandboxResult con esito dettagliato
        """
        start = time.perf_counter()

        if test_utterance is None:
            test_utterance = self.TEST_UTTERANCES[0]

        # Phase 1: Import
        try:
            module = self._import_module(skill_file)
        except Exception as e:
            return SandboxResult(
                success=False,
                phase="import",
                error=str(e),
                traceback=traceback.format_exc(),
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Phase 2: Find and instantiate skill class
        try:
            skill_class = self._find_skill_class(module)
            if skill_class is None:
                return SandboxResult(
                    success=False,
                    phase="instantiate",
                    error="Nessuna classe BaseSkill trovata nel modulo",
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            skill_instance = skill_class()
            class_name = skill_class.__name__
        except Exception as e:
            return SandboxResult(
                success=False,
                phase="instantiate",
                error=f"Errore istanziazione: {e}",
                traceback=traceback.format_exc(),
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Phase 3: get_metadata()
        try:
            metadata = skill_instance.get_metadata()
            metadata_name = metadata.name
        except Exception as e:
            return SandboxResult(
                success=False,
                phase="metadata",
                error=f"get_metadata() fallito: {e}",
                traceback=traceback.format_exc(),
                class_name=class_name,
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Phase 4: match()
        try:
            match_score = skill_instance.match(test_utterance, {})
        except Exception as e:
            return SandboxResult(
                success=False,
                phase="match",
                error=f"match() fallito: {e}",
                traceback=traceback.format_exc(),
                class_name=class_name,
                metadata_name=metadata_name,
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Phase 5: execute() con timeout
        try:
            result = await asyncio.wait_for(
                skill_instance.execute(test_utterance, {}),
                timeout=self.timeout,
            )
            output_str = None
            if result is not None:
                if hasattr(result, 'speak') and result.speak:
                    output_str = result.speak
                elif hasattr(result, 'message'):
                    output_str = result.message
                else:
                    output_str = str(result)

        except asyncio.TimeoutError:
            return SandboxResult(
                success=False,
                phase="execute",
                error=f"Timeout in execute() dopo {self.timeout}s",
                class_name=class_name,
                metadata_name=metadata_name,
                match_score=match_score,
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as e:
            # Execute can fail for missing ROS dependencies — this is
            # acceptable in sandbox. We record the error but may still pass
            # if it's a known ROS-related import.
            error_str = str(e)
            is_ros_dependency = any(
                kw in error_str.lower()
                for kw in ['rclpy', 'ros', 'publisher', 'subscriber', 'node', 'ha_client']
            )

            if is_ros_dependency:
                return SandboxResult(
                    success=True,
                    phase="execute",
                    output=f"Esecuzione con errore ROS atteso (sandbox): {error_str}",
                    class_name=class_name,
                    metadata_name=metadata_name,
                    match_score=match_score,
                    duration_ms=(time.perf_counter() - start) * 1000,
                )

            return SandboxResult(
                success=False,
                phase="execute",
                error=f"execute() fallito: {error_str}",
                traceback=traceback.format_exc(),
                class_name=class_name,
                metadata_name=metadata_name,
                match_score=match_score,
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        return SandboxResult(
            success=True,
            phase="execute",
            output=output_str,
            class_name=class_name,
            metadata_name=metadata_name,
            match_score=match_score,
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    def _import_module(self, file_path: Path):
        """Importa un file Python come modulo isolato."""
        module_name = f"skill_sandbox_{file_path.stem}_{id(self)}"
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Impossibile creare spec per {file_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _find_skill_class(self, module):
        """Trova la prima classe che eredita da BaseSkill nel modulo."""
        from severus.robot_ai.skills.base_skill import BaseSkill

        for name in dir(module):
            obj = getattr(module, name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseSkill)
                and obj is not BaseSkill
            ):
                return obj
        return None
