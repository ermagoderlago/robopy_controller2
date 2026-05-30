import asyncio
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
from ..base_skill import BaseSkill, Capability, SkillMetadata, SkillResult
from ...utils import get_logger

logger = get_logger("memory_info_skill")

class MemoryInfoSkill(BaseSkill):
    """
    Skill for providing information about the system's memory and loaded documents.
    """

    def __init__(self, memory_manager):
        super().__init__()
        self.memory_manager = memory_manager

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="memory_info",
            description="Fornisce informazioni sui documenti caricati in memoria, il conteggio dei dati e lo stato del database RAG.",
            version="1.0.0",
            keywords=["quanti documenti", "quali documenti", "cosa hai in memoria", "lista documenti", "stato memoria", "database", "rag stats"],
            priority=5,
            requires_internet=False,
            capabilities=[Capability.MEMORY_RW]
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        text_lower = text.lower()
        # Parole chiave fondamentali
        has_base_kw = any(kw in text_lower for kw in ["documenti", "memoria", "database", "rag"])
        # Intenzioni (lista, elenco, quanti, ecc.)
        has_intent = any(q in text_lower for q in ["quanti", "quali", "cosa", "stato", "elenco", "lista", "riepilogo", "fammi un documento"])
        
        if has_base_kw and has_intent:
            return 0.98
        elif has_base_kw:
            return 0.4
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> AsyncGenerator[SkillResult, None]:
        yield SkillResult(success=True, message="Interrogazione del database di memoria...", speak="Certamente, controllo subito cosa ho in memoria.")

        try:
            stats = await self.memory_manager.get_stats()
            docs = await self.memory_manager.list_loaded_documents()
            
            total_chunks = stats.get("total_chunks", 0)
            doc_count = len(docs)
            
            if doc_count == 0:
                if total_chunks > 0:
                    msg = f"Ho **{total_chunks}** frammenti di conoscenza in memoria, ma non riesco a recuperare i nomi dei documenti originali (metadati mancanti o non riconosciuti)."
                    speak = f"Ho circa {total_chunks} frammenti di informazione in memoria, ma non riesco a risalire ai nomi dei file originali."
                else:
                    msg = "Al momento non ho documenti tecnici caricati nel mio database RAG."
                    speak = "Al momento non ho documenti tecnici caricati nella mia memoria a lungo termine."
                yield SkillResult.success_result(message=msg, speak=speak)
            else:
                doc_list_str = "\n".join([f"- {d}" for d in docs])
                msg = f"Ho caricato **{doc_count}** documenti tecnici (per un totale di {total_chunks} frammenti di conoscenza):\n\n{doc_list_str}"
                
                # Creiamo una versione Markdown più bella per il documento
                markdown = f"# Archivio Documentale Severus\n\n"
                markdown += f"Stato della memoria RAG al: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                markdown += f"| Documento | Stato | Note |\n"
                markdown += f"| :--- | :--- | :--- |\n"
                for d in docs:
                    markdown += f"| {d} | ✅ Caricato | Pronto per interrogazione |\n"
                markdown += f"\n**Statistiche di Sistema:**\n"
                markdown += f"- **Totale frammenti (chunks):** {total_chunks}\n"
                markdown += f"- **Status Database:** {stats.get('status', 'unknown')}\n"
                
                speak = f"Ho caricato {doc_count} documenti tecnici nel mio database."
                
                yield SkillResult.success_result(
                    message=msg,
                    speak=speak,
                    data={"stats": stats, "documents": docs},
                    formatted_document=markdown
                )
        except Exception as e:
            logger.error(f"Error in MemoryInfoSkill: {e}")
            yield SkillResult.failure_result("Errore durante il recupero delle informazioni di memoria.", speak="Mi dispiace, ho avuto un problema tecnico nel consultare il mio indice di memoria.")

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query_type": {"type": "string", "enum": ["count", "list", "all"], "default": "all"}
            }
        }
