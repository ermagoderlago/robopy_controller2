"""
Robot AI Skills - Consult Documentation Skill
=============================================
Skill che permette a Marcus di consultare e spiegare l'intera documentazione tecnica del robot:
- DFMEA (fmea/dfmea.yaml)
- ECO storici e modifiche autonome (docs/ecos/*.md)
- Schede Tecniche e vincoli di Zona Rossa/Verde (docs/specs/SPEC-XX.md)
- Lezioni apprese (docs/lessons/*.md)
- Diario evolutivo e quote (docs/evolution/)
- Lettura sicura di file di configurazione
"""

import logging
from typing import Any, Dict, List, Optional
from ..base_skill import BaseSkill, SkillMetadata, SkillResult, Capability
from ...services.robot_documentation_service import RobotDocumentationService

logger = logging.getLogger("robot_ai.skills.consult_documentation")


class ConsultDocumentationSkill(BaseSkill):
    """
    Skill per l'interrogazione e la divulgazione naturale della documentazione tecnica di Marcus.
    """

    def __init__(self, doc_service: Optional[RobotDocumentationService] = None):
        super().__init__()
        self.doc_service = doc_service or RobotDocumentationService()

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="consult_documentation_skill",
            description="Consulta la documentazione tecnica del robot: DFMEA, ECO, Schede Tecniche (SPEC), Lesson Learned e file di sistema.",
            version="1.0.0",
            keywords=[
                "fmea", "dfmea", "failure mode", "guasto", "guasti",
                "eco", "modifiche", "scheda tecnica", "specifica", "spec",
                "zona rossa", "zona verde", "regole", "cosa hai imparato",
                "leggi file", "diario evolutivo", "quota token"
            ],
            priority=8,
            capabilities=[Capability.HA_READ]
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        text_lower = text.lower()
        triggers = [
            "cosa dice la fmea", "apri la fmea", "guasti nella fmea", "dfmea",
            "quali eco", "mostrami gli eco", "hai fatto degli eco",
            "cosa prevede la scheda tecnica", "scheda tecnica", "specifica tecnica",
            "quali sono le tue regole", "zona rossa", "zona verde", "vincoli fisici",
            "cosa hai imparato", "lezioni apprese", "lesson learned",
            "diario evolutivo", "quota token", "a che punto è la tua evoluzione",
            "leggi il file", "mostrami il file"
        ]
        if any(trig in text_lower for trig in triggers):
            return 0.95
        if any(w in text_lower for w in ["fmea", "dfmea", "eco-", "spec-"]):
            return 0.85
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        """Esegue l'interrogazione della documentazione tecnica."""
        context = context or {}
        query = context.get("query") or text
        category = context.get("category", "")

        logger.info(f"ConsultDocumentationSkill: query='{query}', category='{category}'")

        try:
            # Caso lettura esplicita di un file
            file_path = context.get("file_path")
            if not file_path and "leggi il file" in query.lower():
                parts = query.lower().split("leggi il file")
                if len(parts) > 1:
                    file_path = parts[1].strip().strip("'\"`")

            if file_path:
                ok, content = self.doc_service.read_workspace_file(file_path)
                if ok:
                    speak_text = f"Ho aperto il file {file_path}. Contiene la configurazione richiesta."
                    return SkillResult(
                        success=True,
                        message=content,
                        speak=speak_text,
                        data={"file_path": file_path, "content_preview": content[:400]}
                    )
                else:
                    return SkillResult(
                        success=False,
                        message=content,
                        speak=f"Non ho potuto leggere il file {file_path}: {content}"
                    )

            # Risoluzione generale query documentale
            answer = self.doc_service.answer_documentation_query(query)

            # Estrai una frase sintetica e fluida per il parlato vocale (senza markdown pesante)
            first_line = answer.strip().split("\n")[0].replace("*", "").replace("#", "").strip()
            speak_msg = f"{first_line}. Ho recuperato tutti i dettagli tecnici dai miei archivi."

            return SkillResult(
                success=True,
                message=answer,
                speak=speak_msg,
                data={"query": query, "full_answer": answer}
            )

        except Exception as e:
            logger.error(f"Errore ConsultDocumentationSkill: {e}", exc_info=True)
            return SkillResult(
                success=False,
                message=f"Errore durante la consultazione dei documenti: {str(e)}",
                speak="Mi dispiace, si è verificato un errore durante l'accesso ai file di documentazione."
            )

    def to_function_declaration(self) -> Dict[str, Any]:
        return {
            "name": "consult_robot_docs",
            "description": (
                "Consulta l'archivio della documentazione tecnica di Marcus: DFMEA (failure modes e RPN), "
                "ECO (modifiche hardware e software storiche e autonome), Schede Tecniche SPEC (vincoli di Zona Rossa/Verde/Gialla), "
                "Lesson Learned (gotchas architetturali audio, Hailo, Nav2) e Diario Evolutivo. "
                "Usa questo tool ogni volta che l'utente chiede chiarimenti sull'ingegneria del robot, sui guasti o sulle regole."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "La domanda o l'argomento tecnico da ricercare nella documentazione"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["dfmea", "eco", "specs", "lessons", "evolution", "file"],
                        "description": "Categoria documentale opzionale per affinare la ricerca"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Percorso relativo del file da leggere se richiesto espressamente (es. marcus_core_rules.md)"
                    }
                },
                "required": ["query"]
            }
        }
