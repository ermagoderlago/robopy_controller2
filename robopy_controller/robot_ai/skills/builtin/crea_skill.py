"""
Robot AI Skills - Skill Generator Wrapper (Meta-Skill) v2.0
============================================================
Skill che permette a Marcus di generare nuove skill usando la pipeline skill_generator.

Versione 2.0 — miglioramenti:
- Fix DI: costruttore con parametri opzionali (sopravvive al caricamento automatico)
- Pipeline completa con feedback step-by-step via yield (7 step)
- Pre-LLM: estrazione nome/descrizione/capabilities dal testo libero dell'utente
- Auto-approve + auto-enable dopo Quality Gate superato
- Hot-reload del SkillRegistry dopo abilitazione
- Aggiornamento ai_context.md per RAK persistente
- Update runtime RAG via memory_manager (no SSH richiesto)
- Log file dedicato: SKILL_LOG_<nome>_<timestamp>.txt
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from robot_ai.skills.base_skill import (
    BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability
)
from ..skill_generator import SkillGeneratorPipeline, SkillRequest

logger = logging.getLogger("robot_ai.skills.crea_skill")


class CreaSkill(BaseSkill):
    """
    Skill meta: permette a Marcus di generare nuove capacità autonomamente.

    Pipeline completa:
    1. Estrazione parametri skill dal testo utente (via LLM)
    2. Generazione codice (Gemini + prompt contesto RAK)
    3. Validazione Quality Gate (AST + Smoke + Sandbox), max 3 iterazioni
    4. Auto-approvazione e abilitazione nel manifest
    5. Hot-reload del SkillRegistry
    6. Aggiornamento ai_context.md (RAK persistente)
    7. Aggiornamento RAG runtime (memoria semantica di Marcus)

    Feedback voce/chat ad ogni step tramite yield SkillResult.
    Log file dedicato scritto su robopy_controller/logs/SKILL_LOG_<nome>_*.txt.

    NOTE DI ARCHITETTURA:
    - Questa skill richiede llm_service e node al costruttore.
    - Il SkillRegistry non può auto-istanziarla con obj(): usare register_with_deps().
    - L'orchestratore deve chiamare registry.register_with_deps(CreaSkill, llm_service=..., node=...)
      durante l'inizializzazione, dopo aver creato LLMService.
    """

    def __init__(self, llm_service=None, node=None, memory_manager=None):
        """
        Args:
            llm_service: Istanza di LLMService per la generazione codice via Gemini.
                         Se None, la skill segnalerà l'errore in execute().
            node:        Nodo ROS 2 per il logging (opzionale, usa logger std se None).
            memory_manager: MemoryManager per aggiornamento RAG runtime (opzionale).
        """
        super().__init__()
        self.llm_service = llm_service
        self.node = node
        self.memory_manager = memory_manager
        self.pipeline = SkillGeneratorPipeline()
        self._pending_request = None
        self._pending_original_text = None

    def _log(self, level: str, msg: str):
        """Logging unificato: usa ROS logger se disponibile, altrimenti standard."""
        if self.node is not None:
            ros_logger = self.node.get_logger()
            getattr(ros_logger, level)(msg)
        else:
            getattr(logger, level)(msg)

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="crea_skill",
            description=(
                "Genera una nuova skill ROS 2 per aggiungere capacità al robot Marcus. "
                "Usa questa skill quando l'utente chiede di imparare a fare qualcosa di nuovo."
            ),
            version="2.0.0",
            keywords=[
                "crea skill", "genera skill", "crea una skill",
                "impara a", "crea una nuova abilità", "nuova funzione",
                "costruisci una skill", "genera abilità", "insegnami a",
                "aggiungi la capacità di", "create skill", "new skill",
            ],
            priority=10,  # Alta priorità per meta-comandi
            capabilities=[],
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Individua richieste di creazione nuove skill o risposte di conferma."""
        text_lower = text.lower()
        
        # Se c'è una richiesta in sospeso, intercettiamo le conferme/rifiuti
        if getattr(self, '_pending_request', None) is not None:
            confirm_words = ["si", "sì", "ok", "procedi", "va bene", "certamente", "creala", "yes", "no", "annulla", "fermati", "cancella", "stop"]
            # Controlla se la frase contiene una di queste parole
            if any(w in text_lower.split() for w in confirm_words) or any(text_lower.strip() == w for w in confirm_words):
                return 0.99

        strong_patterns = [
            "crea una skill", "genera una skill", "crea skill",
            "genera skill", "nuova skill", "create skill",
            "nuova abilità", "nuova funzione",
        ]
        soft_patterns = [
            "impara a", "insegnami a", "puoi imparare",
            "aggiungi la capacità", "aggiungimi la possibilità",
        ]
        if any(p in text_lower for p in strong_patterns):
            return 0.92
        if any(p in text_lower for p in soft_patterns):
            return 0.75
        return 0.0

    async def execute(
        self, text: str, context: Dict[str, Any] = None
    ) -> AsyncIterator[SkillResult]:
        """
        Esegue la pipeline completa di generazione skill con feedback step-by-step.

        Ogni fase emette un SkillResult intermedio via yield per aggiornare Marcus
        in tempo reale (voce e chat). Il risultato finale indica successo o fallimento.
        """
        step_logs: List[tuple] = []  # (timestamp_str, messaggio)
        original_text = text

        def _ts() -> str:
            return datetime.utcnow().strftime("%H:%M:%S")

        def _log_step(msg: str):
            ts = _ts()
            step_logs.append((ts, msg))
            self._log("info", f"[CreaSkill] {msg}")

        # Gestione Human-in-the-Loop: Se c'è una richiesta in attesa di conferma
        if self._pending_request is not None:
            text_lower = text.lower()
            if any(w in text_lower.split() for w in ["no", "annulla", "fermati", "cancella", "stop", "non", "niente"]) or text_lower.strip() == "no":
                self._pending_request = None
                self._pending_original_text = None
                _log_step("Creazione skill annullata dall'utente.")
                yield SkillResult(True, "Annullato", "D'accordo, ho annullato la creazione della skill.")
                return
            elif any(w in text_lower.split() for w in ["si", "sì", "ok", "procedi", "va bene", "certamente", "creala", "yes", "fai pure"]) or text_lower.strip() in ["si", "sì"]:
                request = self._pending_request
                original_text = self._pending_original_text
                self._pending_request = None
                self._pending_original_text = None
                yield SkillResult(True, "Conferma ricevuta", f"Perfetto, inizio subito a generare il codice per la skill {request.name}.")
                # Prosegue con lo Step 3...
            else:
                yield SkillResult(False, "Attesa conferma", f"Scusa, non ho capito. Vuoi che crei la skill '{self._pending_request.name}'? Rispondi sì per procedere, o no per annullare.")
                return
        else:
            # NUOVA RICHIESTA
            # --- Step 0: Verifica prerequisiti ---
            if self.llm_service is None:
                msg = (
                    "Non posso creare skill: il servizio LLM non è disponibile. "
                    "Contatta l'amministratore per configurare CreaSkill con register_with_deps()."
                )
                _log_step(f"ERRORE: llm_service non configurato")
                yield SkillResult(False, "Errore configurazione", msg)
                return

            # --- Step 1: Analisi della richiesta ---
            yield SkillResult(
                True, "Avvio generazione",
                "Certamente! Sto analizzando la tua richiesta per creare una nuova capacità..."
            )
            _log_step("Step 1: Analisi richiesta avviata")

            # --- Step 2: Estrazione parametri via LLM ---
            yield SkillResult(
                True, "Estrazione parametri",
                "Sto estraendo i parametri della skill dalla tua richiesta..."
            )
            _log_step("Step 2: Chiamata LLM per estrazione parametri")

            try:
                request = await self._extract_skill_params(text)
                _log_step(
                    f"Step 2 OK: nome='{request.name}', "
                    f"caps={request.capabilities}, "
                    f"utterances={request.test_utterances}"
                )
                yield SkillResult(
                    True, "Richiesta conferma",
                    f"Vuoi che crei una nuova skill chiamata '{request.name}' per la seguente funzionalità: '{request.description}'? Rispondi sì o no per procedere."
                )
                self._pending_request = request
                self._pending_original_text = original_text
                return
            except Exception as e:
                _log_step(f"Step 2 FALLITO: {e}")
                # Fallback: usa il testo grezzo come descrizione
                request = SkillRequest(
                    name="NuovaSkill",
                    description=text,
                    test_utterances=[text],
                )
                yield SkillResult(
                    True, "Richiesta conferma",
                    f"Vuoi che crei una nuova skill chiamata '{request.name}' per la seguente funzionalità: '{request.description}'? Rispondi sì o no per procedere."
                )
                self._pending_request = request
                self._pending_original_text = original_text
                return

        # --- Step 3-5: Pipeline di generazione (con step_callback) ---
        _log_step("Step 3: Avvio pipeline SkillGeneratorPipeline")

        # Buffer per i messaggi della pipeline (non possiamo fare yield dall'interno di run_full_pipeline)
        pipeline_messages = []

        async def step_callback(msg: str):
            """Callback chiamato dalla pipeline ad ogni step."""
            _log_step(f"Pipeline: {msg}")
            pipeline_messages.append(msg)

        async def code_provider(prompt: str) -> str:
            """Chiama Gemini per generare il codice della skill."""
            try:
                response = await self.llm_service.generate(prompt)
                # Supporta sia .text che .response_text (v1/v2 API)
                if hasattr(response, 'text'):
                    return response.text
                elif hasattr(response, 'response_text'):
                    return response.response_text
                else:
                    return str(response)
            except Exception as e:
                raise RuntimeError(f"Errore generazione Gemini: {e}") from e

        # Emetti aggiornamento prima di avviare la pipeline lunga
        yield SkillResult(
            True, "Generazione in corso",
            f"Sto generando il codice per '{request.name}'. "
            f"Questa operazione richiede qualche secondo..."
        )

        try:
            result = await self.pipeline.run_full_pipeline(
                request=request,
                code_provider=code_provider,
                step_callback=step_callback,
            )
        except Exception as e:
            _log_step(f"Pipeline CRASH: {e}")
            self.pipeline.write_skill_creation_log(
                request=request,
                step_logs=step_logs,
                success=False,
                original_text=original_text,
            )
            yield SkillResult(
                False, "Errore interno",
                f"Si è verificato un errore durante la generazione: {str(e)}"
            )
            return

        # Emetti i messaggi accumulati dalla pipeline
        for msg in pipeline_messages[-3:]:  # Solo gli ultimi 3 per non sovraccaricaer
            if msg:
                yield SkillResult(True, "Aggiornamento pipeline", msg)
                await asyncio.sleep(0.1)  # Piccola pausa per non inondare il canale

        # --- Risultato Quality Gate ---
        if not result.success:
            _log_step(f"Quality Gate FALLITO dopo {result.iterations} iter: {result.failure_report}")
            log_file = self.pipeline.write_skill_creation_log(
                request=request,
                step_logs=step_logs,
                success=False,
                original_text=original_text,
            )
            error_summary = result.failure_report or "Errore sconosciuto"
            # Troncato per la risposta vocale
            if len(error_summary) > 200:
                error_summary = error_summary[:200] + "..."
            yield SkillResult(
                False, "Generazione fallita",
                f"Non sono riuscito a creare la skill '{request.name}' "
                f"dopo {result.iterations} tentativi. "
                f"Errori: {error_summary}. "
                f"Ho salvato il log in: SKILL_LOG_{request.snake_name}. "
                f"Puoi riformulare la richiesta con più dettagli?"
            )
            return

        # --- Step 6: Auto-approvazione e abilitazione ---
        _log_step("Step 6: Quality Gate superato — approvazione automatica")
        yield SkillResult(
            True, "Quality Gate superato",
            f"Ottimo! La skill '{request.name}' ha superato tutti i test. "
            f"Sto approvando e abilitando..."
        )

        try:
            manifest_entry = self.pipeline.approve_skill(request)
            if manifest_entry is None:
                raise RuntimeError("approve_skill ha restituito None (file staging non trovato?)")

            # Abilita immediatamente nel manifest
            enabled = self.pipeline.enable_skill(request.name)
            if not enabled:
                self._log("warning", f"enable_skill fallito per {request.name}")

            _log_step(f"Step 6 OK: skill approvata e abilitata (enabled={enabled})")

        except Exception as e:
            _log_step(f"Step 6 ERRORE: {e}")
            self._log("error", f"Errore approvazione skill: {e}")
            yield SkillResult(
                True, "Approvazione parziale",
                f"La skill è generata ma l'approvazione automatica ha avuto un problema: {e}. "
                f"Dovrai abilitarla manualmente nel manifest."
            )

        # --- Step 7: Hot-reload del SkillRegistry ---
        _log_step("Step 7: Hot-reload SkillRegistry")
        yield SkillResult(
            True, "Aggiornamento registry",
            f"Sto ricaricando il catalogo delle skill..."
        )

        try:
            from ..skill_registry import SkillRegistry
            registry = SkillRegistry()
            loaded = await asyncio.get_event_loop().run_in_executor(
                None, registry.reload_active
            )
            _log_step(f"Step 7 OK: {loaded} skill caricate/aggiornate nel registry")
        except Exception as e:
            _log_step(f"Step 7 ERRORE: {e}")
            self._log("warning", f"Hot-reload fallito: {e}")

        # --- Step 8: Aggiornamento ai_context.md ---
        _log_step("Step 8: Aggiornamento ai_context.md (RAK persistente)")
        try:
            ctx_ok = self.pipeline.update_ai_context_for_skill(request)
            rak_ok = self.pipeline.update_rak_for_skill(request)
            _log_step(f"Step 8 OK: ai_context={ctx_ok}, files_topic={rak_ok}")
        except Exception as e:
            _log_step(f"Step 8 ERRORE: {e}")

        # --- Step 9: Aggiornamento RAG runtime ---
        _log_step("Step 9: Aggiornamento RAG runtime (memoria semantica)")
        yield SkillResult(
            True, "Aggiornamento memoria",
            "Sto aggiornando la mia memoria per ricordare questa nuova capacità..."
        )

        try:
            rag_ok = await self.pipeline.add_skill_to_rag(request, self.memory_manager)
            _log_step(f"Step 9 OK: RAG runtime updated={rag_ok}")
        except Exception as e:
            _log_step(f"Step 9 ERRORE (non bloccante): {e}")

        # --- Scrittura log finale ---
        try:
            log_file = self.pipeline.write_skill_creation_log(
                request=request,
                step_logs=step_logs,
                success=True,
                original_text=original_text,
            )
            _log_step(f"Log creato: {log_file.name}")
        except Exception as e:
            self._log("warning", f"Errore scrittura log: {e}")

        # --- Messaggio finale di successo ---
        sample_utterances = (
            f"Prova a dirmi: \"{request.test_utterances[0]}\""
            if request.test_utterances
            else ""
        )
        yield SkillResult(
            True, "Skill creata con successo",
            f"✅ Perfetto! La skill '{request.name}' è ora attiva. "
            f"{sample_utterances} "
            f"Ci ho messo {result.iterations} iterazione/i di generazione."
        )

    # ------------------------------------------------------------------
    # Metodi privati
    # ------------------------------------------------------------------

    async def _extract_skill_params(self, text: str) -> SkillRequest:
        """
        Usa il LLM per estrarre strutturati parametri di SkillRequest dal testo libero.

        Se la chiamata LLM fallisce, lancia un'eccezione e il chiamante usa il fallback.
        """
        extraction_prompt = f"""Analizza questa richiesta di creazione skill per un robot ROS 2:
"{text}"

Rispondi SOLO con questo formato JSON (senza markdown, senza spiegazioni):
{{
  "name": "NomeInPascalCase",
  "description": "Descrizione chiara di cosa fa la skill in una frase",
  "capabilities": ["ha.write", "ha.read"],
  "test_utterances": ["frase di test 1", "frase di test 2"],
  "topics_pub": ["/ai/conversation/response"],
  "topics_sub": []
}}

Capability disponibili: ha.read, ha.write, nav.move, camera.read
Se non sai quali capability servono, usa una lista vuota.
Il nome DEVE essere in PascalCase e terminare con "Skill" (es: AccendiLuceSkill).
"""
        import json

        try:
            response = await self.llm_service.generate(extraction_prompt)
            raw = getattr(response, 'text', None) or getattr(response, 'response_text', None) or str(response)

            # Pulisci il JSON (rimuovi eventuali ```json ... ```)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip().rstrip("```").strip()

            data = json.loads(raw)

            return SkillRequest(
                name=data.get("name", "NuovaSkill"),
                description=data.get("description", text),
                capabilities=data.get("capabilities", []),
                topics_sub=data.get("topics_sub", []),
                topics_pub=data.get("topics_pub", ["/ai/conversation/response"]),
                test_utterances=data.get("test_utterances", [text]),
            )
        except Exception as e:
            logger.warning(f"Estrazione parametri LLM fallita: {e}. Uso fallback.")
            raise

    def to_function_declaration(self) -> Dict[str, Any]:
        """Dichiarazione Tool Calling per Gemini LLM Service."""
        return {
            "name": "crea_skill",
            "description": (
                "ATTENZIONE: USA QUESTO TOOL SOLO SE L'UTENTE HA ESPRESSAMENTE CHIESTO DI CREARE, GENERARE, SCRIVERE O IMPARARE UNA **NUOVA** SKILL / CODICE. "
                "NON usare per eseguire azioni comuni come 'avvia spotify', 'suona musica', 'accendi la luce'. Controlla sempre se esistono altre skill per quello. "
                "Questo tool avvia il processo di generazione codice per aggiungere capacità mancanti a Marcus."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Nome della skill in PascalCase terminante con 'Skill' "
                            "(es: AccendiLuceSkill, CercaSuInternetSkill)"
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Descrizione dettagliata di cosa deve fare la skill",
                    },
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Elenco delle capability richieste. "
                            "Valori possibili: ha.read, ha.write, nav.move, camera.read"
                        ),
                    },
                    "test_utterances": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-3 frasi di test che attivano la skill",
                    },
                    "extra_context": {
                        "type": "string",
                        "description": "Dettagli aggiuntivi per la generazione (topic specifici, API, ecc.)",
                    },
                },
                "required": ["name", "description"],
            },
        }
