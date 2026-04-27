
import os
import asyncio
import logging
from typing import Any, Dict, List, Optional, AsyncGenerator
from pathlib import Path

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability

logger = logging.getLogger("robot_ai.skills.technical_document_skill")

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.base_models import InputFormat
    HAS_DOCLING = True
except ImportError:
    HAS_DOCLING = False

class TechnicalDocumentSkill(BaseSkill):
    """
    Skill per la conversione di documenti tecnici (PDF) in formato Markdown
    utilizzando Docling.
    """

    def __init__(self, llm_service=None):
        super().__init__()
        self.llm_service = llm_service
        self._converter = None
        # Percorso predefinito per i documenti tecnici
        self.base_dir = Path("/home/robopy/severus_ws/istruzioni_tecniche")
        self.pdf_dir = self.base_dir / "pdf"
        self.md_dir = self.base_dir / "MD"

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="technical_document_processor",
            description="Converte documenti tecnici PDF in Markdown per l'analisi RAG.",
            version="1.0.0",
            keywords=["converti pdf", "elabora documenti", "istruzioni tecniche", "markdown"],
            priority=5,
            capabilities=[Capability.MEMORY_RW] # Richiede accesso al filesystem/memoria
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        text_lower = text.lower()
        patterns = [
            "converti i pdf",
            "elabora documenti tecnici",
            "genera markdown",
            "aggiorna istruzioni",
            "processa documenti"
        ]
        if any(p in text_lower for p in patterns):
            return 0.85
        return 0.0

    def _get_converter(self):
        if self._converter is None and HAS_DOCLING:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = False
            pipeline_options.do_table_structure = True
            self._converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        return self._converter

    async def execute(self, text: str, context: Dict[str, Any] = None) -> AsyncGenerator[SkillResult, None]:
        if not HAS_DOCLING:
            yield SkillResult.failure_result(
                "Docling non installato",
                error_code=SkillErrorCode.EXTERNAL_SERVICE_ERROR,
                speak="Non posso elaborare i documenti perché la libreria Docling non è installata nel sistema."
            )
            return

        self.md_dir.mkdir(parents=True, exist_ok=True)
        files_pdf = list(self.pdf_dir.glob("*.pdf"))

        if not files_pdf:
            yield SkillResult.success_result(
                message="Nessun PDF trovato",
                speak="Non ho trovato nuovi file PDF nella cartella delle istruzioni tecniche."
            )
            return

        yield SkillResult(
            success=True,
            message=f"Trovati {len(files_pdf)} documenti. Inizio conversione...",
            speak=f"Ho trovato {len(files_pdf)} documenti tecnici. Inizio la conversione in formato markdown."
        )

        converter = self._get_converter()
        success_count = 0
        
        for pdf_path in files_pdf:
            try:
                # Eseguiamo la conversione in un thread separato per non bloccare il loop ROS 2/Asyncio
                # dato che docling è CPU intensive.
                logger.info(f"Processando: {pdf_path.name}")
                
                # Feedback intermedio
                yield SkillResult(
                    success=True,
                    message=f"Elaborazione di {pdf_path.name}...",
                    speak=None # Non parliamo per ogni file se sono tanti
                )
                
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, converter.convert, pdf_path)
                markdown_content = result.document.export_to_markdown()
                
                output_md = self.md_dir / f"{pdf_path.stem}.md"
                with open(output_md, "w", encoding="utf-8") as f:
                    f.write(markdown_content)
                
                success_count += 1
                logger.info(f"✅ Convertito: {output_md.name}")
                
            except Exception as e:
                logger.error(f"Errore durante la conversione di {pdf_path.name}: {e}")
                yield SkillResult(
                    success=False,
                    message=f"Errore su {pdf_path.name}: {str(e)}",
                    speak=f"Ho avuto un problema con il file {pdf_path.name}."
                )

        yield SkillResult.success_result(
            message=f"Conversione completata. {success_count} file generati.",
            speak=f"Conversione terminata. Ho generato {success_count} file markdown pronti per la memoria."
        )

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["convert", "list"],
                    "description": "L'azione da eseguire: 'convert' per elaborare i PDF, 'list' per vedere i file presenti."
                }
            },
            "required": ["action"]
        }
