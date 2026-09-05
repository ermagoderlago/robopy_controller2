"""
Robot AI Skills - Skill Generator Wrapper (Meta-Skill)
=====================================================
Skill che permette a Marcus di generare nuove capacità usando l'Agente Antigravity (Gemini 3.8),
tracciare e verbalizzare in tempo reale ogni fase della pipeline (analisi, generazione codice,
validazione AST, sandbox cinematico, autorigenerazione per errori, quota guard 90%),
rispondere alle richieste di stato e registrare ogni successo nella memoria autobiografica di Marcus.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability
from ..skill_generator import SkillGeneratorPipeline, SkillRequest
from ...services.antigravity_agent_service import AntigravityAgentService

logger = logging.getLogger("robot_ai.skills.crea_skill")


class CreaSkill(BaseSkill):
    """
    Skill per la generazione autonoma e osservabile di nuove capacità per il robot.
    
    Permette a Marcus di:
    1. Ricevere la richiesta di una nuova funzionalità dall'utente.
    2. Usare l'Agente Antigravity autonomo (Gemini 3.8) con Quota Guard a 4h.
    3. Notificare l'utente in tempo reale sullo stato di avanzamento.
    4. Validare la skill tramite Quality Gate (AST + Smoke Test + Sandbox ROS).
    5. Spiegare con tono umano e naturale qualsiasi intoppo o la sospensione per quota (90%).
    6. Rispondere a domande sullo stato dei lavori ("A che punto è la skill?", "Cosa fa Antigravity?").
    7. Fissare i risultati nella memoria autobiografica e nel diario evolutivo.
    """

    def __init__(self, llm_service=None, pipeline=None, antigravity_service=None):
        super().__init__()
        self.llm_service = llm_service
        self.pipeline = pipeline or SkillGeneratorPipeline()
        self.antigravity_service = antigravity_service or AntigravityAgentService()
        
        # Stato globale per osservabilità concorrente
        self.current_task_status: Optional[Dict[str, Any]] = None
        self.last_completed_task: Optional[Dict[str, Any]] = None

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="crea_skill",
            description="Genera una nuova skill ROS 2 per aggiungere capacità al robot, con monitoraggio real-time di Antigravity.",
            version="2.0.0",
            keywords=[
                "crea skill", "genera abilità", "nuova funzione", "impara a",
                "costruisci una skill", "stato skill", "a che punto è la skill",
                "cosa sta facendo antigravity"
            ],
            priority=10,
            capabilities=[Capability.HA_READ]
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Individua richieste di creazione o interrogazioni sullo stato dei lavori."""
        text_lower = text.lower()
        
        # 1. Richieste di stato
        if "a che punto" in text_lower:
            return 0.98
        if "antigravity" in text_lower and any(w in text_lower for w in ["facendo", "fa", "stato", "lavorando"]):
            return 0.98
        status_patterns = [
            "stato creazione", "stato della skill", "come procede la skill", "a che punto"
        ]
        if any(p in text_lower for p in status_patterns):
            return 0.98

        # 2. Richieste di creazione
        creation_patterns = [
            "crea una skill", "crea la skill", "crea skill", "genera una skill", "genera la skill",
            "impara a", "crea una nuova abilità", "costruisci una skill", "sviluppa una skill", "nuova skill"
        ]
        if any(p in text_lower for p in creation_patterns):
            return 0.96
            
        return 0.0

    def _record_to_autobiographical_memory(self, skill_name: str, description: str, success: bool, details: str) -> None:
        """Registra l'evoluzione personale di Marcus in MAG e nel diario di bordo."""
        try:
            from ...services.curiosity_evolution_engine import CuriosityEvolutionEngine
            engine = CuriosityEvolutionEngine()
            outcome = "SUCCESS_AUTONOMOUS_SKILL" if success else "FAILED_SKILL_GENERATION"
            engine.log_evolution_experience(
                cycle_name=f"Creazione Skill {skill_name}",
                subsystem="AI/Cognitive/Skills",
                failure_mode_id=None,
                inquiry=f"Come implementare l'abilità '{skill_name}': {description}?",
                action_taken=f"Sintetizzato codice Python via Antigravity, validato AST e testato in sandbox. {details}",
                outcome=outcome
            )
        except Exception as err:
            logger.warning(f"Errore registrazione diario evolutivo per skill {skill_name}: {err}")

        # Tenta anche registrazione in MAGDatabase se accessibile
        try:
            from ...trinity.mag_database import MAGDatabase
            mag_db = MAGDatabase()
            ep_text = f"Marcus ha generato l'abilità '{skill_name}': {description}"
            mag_db.insert_episode(
                user_input=f"Crea skill {skill_name}",
                robot_response=details,
                summary=ep_text,
                user_id="MarcusSelfEvolution",
                emotion_tag="pride" if success else "learning",
                importance=0.9,
                actions_taken=[f"create_skill:{skill_name}", f"success:{success}"],
                was_successful=success
            )
            mag_db.insert_fact(
                fact_text=ep_text,
                fact_type="self_evolution",
                confidence=0.95
            )
            logger.info(f"Fissato evento evolutivo '{skill_name}' nella memoria autobiografica MAG.")
        except Exception as mag_err:
            logger.debug(f"MAG database non raggiungibile per registrazione diretta: {mag_err}")

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """Esegue il processo di generazione o risponde allo stato di avanzamento."""
        context = context or {}
        text_lower = text.lower()

        # --- A. Risposta all'interrogazione di stato in corso ---
        status_inquiry = any(p in text_lower for p in [
            "a che punto", "cosa sta facendo", "stato della skill", "come procede"
        ]) or context.get("action") == "check_status"

        if status_inquiry:
            if self.current_task_status:
                task = self.current_task_status
                msg = (
                    f"In questo momento Antigravity sta lavorando alla skill '{task['name']}'. "
                    f"Siamo nella fase: {task.get('phase_text', 'elaborazione')}. "
                    f"L'ultimo aggiornamento è stato: \"{task.get('last_message', '')}\"."
                )
                yield SkillResult(success=True, message=msg, speak=msg, data=task)
                return
            elif self.last_completed_task:
                last = self.last_completed_task
                status_str = "con successo" if last.get("success") else "con un intoppo"
                msg = (
                    f"Al momento non ci sono skill in fase di creazione. "
                    f"L'ultima attività ha riguardato la skill '{last['name']}', completata {status_str}."
                )
                yield SkillResult(success=True, message=msg, speak=msg, data=last)
                return
            else:
                msg = "Al momento non ci sono attività di generazione skill attive o recenti."
                yield SkillResult(success=True, message=msg, speak=msg)
                return

        # --- B. Pipeline di generazione della skill ---
        name_suggestion = context.get("name") or "NuovaSkill"
        description_suggestion = context.get("description") or text

        # Pulizia nome
        if not name_suggestion.endswith("Skill") and name_suggestion != "NuovaSkill":
            name_suggestion += "Skill"

        self.current_task_status = {
            "name": name_suggestion,
            "description": description_suggestion,
            "started_at": time.time(),
            "phase": "ANALYZING",
            "phase_text": "analisi della richiesta",
            "last_message": "Sto analizzando la richiesta per comprendere quali capacità servono."
        }

        # Coda asincrona per streaming del progresso tra pipeline ed execute generator
        progress_queue = asyncio.Queue()

        async def progress_callback(status: str, natural_phrase: str, details: Dict[str, Any]):
            if self.current_task_status:
                self.current_task_status["phase"] = status
                self.current_task_status["phase_text"] = natural_phrase
                self.current_task_status["last_message"] = natural_phrase
            await progress_queue.put((status, natural_phrase, details))

        # Primo feedback vocale immediato
        yield SkillResult(
            success=True,
            message="Avvio pipeline generazione skill",
            speak="Certamente. Sto analizzando la tua richiesta per comprendere quali capacità ROS 2 servono."
        )

        try:
            # Code provider collegato ad Antigravity con Gemini 3.8
            async def code_provider(prompt: str) -> str:
                if self.antigravity_service.is_available:
                    try:
                        logger.info("Utilizzo dell'Agente Antigravity autonomo (Gemini 3.8) per la sintesi...")
                        return await self.antigravity_service.generate_code_autonomous(prompt)
                    except Exception as agy_err:
                        if "QUOTA_90_PERCENT" in str(agy_err):
                            raise agy_err
                        logger.warning(f"Antigravity Agent fallito ({agy_err}), fallback su llm_service base...")
                if self.llm_service:
                    response = await self.llm_service.generate(prompt)
                    return response.text
                raise RuntimeError("Nessun provider LLM disponibile per la generazione del codice.")

            request = SkillRequest(
                name=name_suggestion,
                description=description_suggestion,
                test_utterances=[text]
            )

            # Lancia la pipeline in task concorrente per consentire lo svuotamento in tempo reale della coda
            pipeline_task = asyncio.create_task(
                self.pipeline.run_full_pipeline(request, code_provider, on_progress=progress_callback)
            )

            # Stream degli eventi intermedi alla VUI e all'utente
            while not pipeline_task.done() or not progress_queue.empty():
                try:
                    status, natural_phrase, details = await asyncio.wait_for(progress_queue.get(), timeout=0.25)
                    yield SkillResult(
                        success=True,
                        message=f"[{status}] {natural_phrase}",
                        speak=natural_phrase,
                        data=details
                    )
                except asyncio.TimeoutError:
                    continue
                except Exception as q_err:
                    logger.debug(f"Queue streaming error: {q_err}")
                    break

            result = await pipeline_task

            # Salva stato per consultazione futura
            self.last_completed_task = {
                "name": result.skill_name,
                "success": result.success,
                "iterations": result.iterations,
                "timestamp": time.time()
            }
            self.current_task_status = None

            # Gestione esito finale
            if result.success:
                # Approvazione e promozione automatica
                self.pipeline.approve_skill(request)
                self.pipeline.enable_skill(request.name)
                self.pipeline.update_rak_for_skill(request)

                self._record_to_autobiographical_memory(
                    skill_name=result.skill_name,
                    description=description_suggestion,
                    success=True,
                    details=f"Validata in {result.iterations} iterazioni, promossa in active e registrata."
                )

                final_msg = (
                    f"Ho completato con successo la nuova abilità '{result.skill_name}'. "
                    f"Ha superato i controlli di sicurezza AST e il collaudo in sandbox, "
                    f"ed è ora registrata e attiva nel mio sistema!"
                )
                yield SkillResult(
                    success=True,
                    message=f"Skill '{result.skill_name}' attiva in produzione",
                    speak=final_msg,
                    data={"skill_name": result.skill_name, "path": str(result.file_path)}
                )
            else:
                self._record_to_autobiographical_memory(
                    skill_name=result.skill_name,
                    description=description_suggestion,
                    success=False,
                    details=result.failure_report or "Fallimento iterazioni"
                )

                fail_msg = (
                    f"Non sono riuscito a completare la skill '{result.skill_name}'. "
                    f"{result.failure_report}"
                )
                yield SkillResult(
                    success=False,
                    message="Generazione fallita o sospesa",
                    speak=fail_msg,
                    data={"report": result.failure_report}
                )

        except Exception as e:
            logger.error(f"Errore durante CreaSkill: {e}", exc_info=True)
            self.current_task_status = None
            err_phrase = (
                "Abbiamo raggiunto il 90% della quota token per questa sessione di 4 ore. Ho salvato il lavoro per la prossima sessione."
                if "QUOTA_90_PERCENT" in str(e) else
                f"Si è verificato un errore durante la creazione: {str(e)}"
            )
            yield SkillResult(
                success=False,
                message=f"Errore: {str(e)}",
                speak=err_phrase
            )

    def to_function_declaration(self) -> Dict[str, Any]:
        """Dichiarazione per il Tool Calling del LLM."""
        return {
            "name": "crea_skill",
            "description": (
                "Genera o monitora una nuova skill (capacità ROS 2) per il robot Marcus usando Antigravity. "
                "Usa questo tool quando l'utente ti chiede di imparare una nuova abilità o di verificare "
                "a che punto è la creazione della skill o cosa sta facendo Antigravity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "check_status"],
                        "description": "Azione da eseguire: 'create' per creare, 'check_status' per verificare l'avanzamento"
                    },
                    "name": {
                        "type": "string",
                        "description": "Nome della skill in PascalCase (es. MeteoDomoticaSkill)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Descrizione dettagliata di cosa deve fare la skill"
                    },
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Elenco delle capability richieste (es. ha.read, nav.move)"
                    }
                }
            }
        }
