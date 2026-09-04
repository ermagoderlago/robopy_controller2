import os
import asyncio
import threading
import datetime
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

from robot_ai.utils import get_logger
from .intent_router import IntentRouter, IntentCategory, RetrievalConfig
from .metaprompt_fusion import MetapromptFusion
from .cag_aggregator import ContextAggregator
from .rag_knowledge_query import KnowledgeQueryEngine
from .mag_database import MAGDatabase
from .mag_zettelkasten import SemanticFactStore
from .mag_user_profile import UserProfileEngine
from .mag_hybrid_search import HybridSearchEngine
from .mag_episodic import EpisodicMemoryEngine

logger = get_logger("trinity_engine")


class TrinityEngine:
    """
    The main orchestrator class for the TRINITY cognitive architecture.
    Provides a single unified interface for prompt augmentation and episodic memory recording.
    """

    def __init__(
        self,
        memory_store=None,
        embedding_service=None,
        world_model=None,
        ha_context_provider=None,
        config=None,
        node=None,
        db_path: str = "/home/robopy/mag_trinity.db"
    ):
        self.logger = logger
        self._lock = threading.RLock()

        self.memory_store = memory_store
        self.embedding_service = embedding_service
        self.world_model = world_model
        self.ha_context_provider = ha_context_provider
        self.config = config
        self.node = node

        # Check feature flag
        self.enabled = True
        if hasattr(self.config, 'trinity') and hasattr(self.config.trinity, 'enabled'):
            self.enabled = self.config.trinity.enabled

        # 1. Intent Router
        self.intent_router = IntentRouter()

        # 2. Metaprompt Fusion
        self.fusion_engine = MetapromptFusion()

        # 3. CAG Aggregator
        try:
            self.cag = ContextAggregator(ros_node=node, ttl_seconds=5.0)
        except Exception as e:
            self.logger.warning(f"CAG initialization partial error: {e}")
            self.cag = None

        # 4. RAG Knowledge Query Engine
        try:
            self.rag_knowledge = KnowledgeQueryEngine()
        except Exception as e:
            self.logger.warning(f"RAG Knowledge engine initialization error: {e}")
            self.rag_knowledge = None

        # 5. MAG Components
        try:
            # Fallback path if directory not writable (e.g., in testing or local simulation)
            if not os.path.exists(os.path.dirname(db_path)) and os.path.dirname(db_path):
                try:
                    os.makedirs(os.path.dirname(db_path), exist_ok=True)
                except OSError:
                    db_path = "./mag_trinity.db"

            self.mag_db = MAGDatabase(db_path=db_path)
            self.fact_store = SemanticFactStore(self.mag_db)
            self.user_profile = UserProfileEngine(self.mag_db)
            self.hybrid_search = HybridSearchEngine(self.mag_db)
            self.mag_episodic = EpisodicMemoryEngine(
                db=self.mag_db,
                fact_store=self.fact_store,
                user_profile=self.user_profile,
                search_engine=self.hybrid_search
            )
        except Exception as e:
            self.logger.warning(f"MAG initialization error: {e}")
            self.mag_episodic = None

        self.logger.info(f"🧠 TrinityEngine initialized successfully. Enabled: {self.enabled}")

    async def _retrieve_rag_conversational(self, clean_text: str, top_k: int = 3) -> str:
        """Retrieves conversational memories and learned facts from ChromaDB memory_store."""
        if not self.memory_store:
            return ""
        try:
            results = await self.memory_store.search(clean_text, top_k=top_k)
            memories = []
            for res in results:
                content = res.memory.content if hasattr(res, 'memory') else getattr(res, 'content', '')
                if not content:
                    continue
                # Filtra risposte evasive o generiche passate per non creare echo loop
                content_lower = content.lower()
                if "non ho visto molto di nuovo" in content_lower or "problema di connessione" in content_lower:
                    continue
                if hasattr(res, 'score') and res.score >= 0.35:
                    memories.append(f"- {content}")
                elif hasattr(res, 'content'):
                    memories.append(f"- {content}")

            # Se la query riguarda l'apprendimento o l'identità, recupera anche i fatti appresi recenti
            is_learning_query = any(k in clean_text.lower() for k in ["appreso", "imparato", "memoria", "ricordi", "ricordare", "acronimo", "significa"])
            if is_learning_query and hasattr(self.memory_store, 'get_recent'):
                try:
                    from ..rag.memory_store import MemoryType
                    facts = self.memory_store.get_recent(limit=4, memory_type=MemoryType.LEARNED_FACT)
                    for f in facts:
                        fact_line = f"- [FATTO APPRESO]: {f.content}"
                        if fact_line not in memories:
                            memories.append(fact_line)
                except Exception as e:
                    self.logger.debug(f"Learned facts lookup error: {e}")

            return "\n".join(memories)
        except Exception as e:
            self.logger.warning(f"RAG conversational search error: {e}")
            return ""

    async def _retrieve_rag_knowledge(self, clean_text: str, top_k: int = 4) -> str:
        """Retrieves technical documentation/code chunks from Knowledge Base."""
        if not self.rag_knowledge:
            return ""
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None, lambda: self.rag_knowledge.query_knowledge(clean_text, top_k=top_k, min_score=0.30)
            )
            return self.rag_knowledge.format_for_prompt(results, max_tokens=800)
        except Exception as e:
            self.logger.warning(f"RAG knowledge search error: {e}")
            return ""

    async def _retrieve_cag_context(self) -> Dict[str, str]:
        """Retrieves live CAG context snapshot."""
        if not self.cag:
            return {
                "cag_hardware": "",
                "cag_ros": "",
                "cag_environment": "",
                "cag_errors": "",
                "cag_ha": ""
            }
        try:
            snapshot = self.cag.get_snapshot()
            ha_text = ""
            if self.ha_context_provider:
                try:
                    ha_text = self.ha_context_provider() or ""
                except Exception as e:
                    self.logger.debug(f"HA context provider error: {e}")

            return {
                "cag_hardware": snapshot.get("hardware_text", ""),
                "cag_ros": snapshot.get("ros_text", ""),
                "cag_environment": snapshot.get("env_text", ""),
                "cag_errors": snapshot.get("error_text", ""),
                "cag_ha": ha_text
            }
        except Exception as e:
            self.logger.warning(f"CAG context error: {e}")
            return {}

    async def _retrieve_mag_memory(self, user_text: str, user_identity: Optional[str] = None) -> Dict[str, str]:
        """Retrieves autobiographical episodic memories, facts, and user profile."""
        if not self.mag_episodic:
            return {"mag_profile": "", "mag_episodes": "", "mag_facts": ""}
        try:
            loop = asyncio.get_running_loop()
            mem_data = await loop.run_in_executor(
                None, lambda: self.mag_episodic.retrieve_relevant_memory(user_text, user_id=user_identity, top_k_episodes=3, top_k_facts=4)
            )
            sections = self.mag_episodic.to_prompt_sections(mem_data, max_tokens=600)
            return {
                "mag_profile": sections.get("user_profile", ""),
                "mag_episodes": sections.get("episodes", ""),
                "mag_facts": sections.get("facts", "")
            }
        except Exception as e:
            self.logger.warning(f"MAG memory retrieval error: {e}")
            return {}

    async def build_augmented_prompt(
        self,
        user_text: str,
        source: str = "text",
        user_identity: Optional[str] = None,
        system_prompt: str = "",
        dopaminergic_override: str = "",
        repeated_note: str = "",
        email_context: str = "",
        extra_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Main entry point for prompt building.
        Runs RAG, CAG, and MAG retrievals in parallel and assembles the Metaprompt.
        """
        if not self.enabled:
            return user_text

        # 1. Intent Classification
        category = self.intent_router.classify(user_text)
        retrieval_config = self.intent_router.get_retrieval_config(category)
        self.logger.info(f"🎯 Query intent classified: {category.name}")

        # 2. Parallel retrieval tasks
        async def _empty_str():
            return ""

        rag_conv_task = self._retrieve_rag_conversational(user_text) if retrieval_config.rag_enabled else _empty_str()
        rag_know_task = self._retrieve_rag_knowledge(user_text) if retrieval_config.rag_knowledge_enabled else _empty_str()
        cag_task = self._retrieve_cag_context()
        mag_task = self._retrieve_mag_memory(user_text, user_identity)

        # Run with graceful fallback
        rag_conv_res, rag_know_res, cag_res, mag_res = await asyncio.gather(
            rag_conv_task, rag_know_task, cag_task, mag_task, return_exceptions=True
        )

        rag_memories = rag_conv_res if isinstance(rag_conv_res, str) else ""
        rag_knowledge = rag_know_res if isinstance(rag_know_res, str) else ""
        cag_data = cag_res if isinstance(cag_res, dict) else {}
        mag_data = mag_res if isinstance(mag_res, dict) else {}

        # 3. Timestamp formatting
        try:
            timestamp = datetime.datetime.now(ZoneInfo("Europe/Rome")).strftime("%A %d %B %Y, ore %H:%M")
        except Exception:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        # 4. Metaprompt Fusion
        final_prompt = self.fusion_engine.build_prompt(
            user_text=user_text,
            system_prompt=system_prompt,
            dopaminergic_override=dopaminergic_override,
            mag_profile=mag_data.get("mag_profile", ""),
            mag_episodes=mag_data.get("mag_episodes", ""),
            mag_facts=mag_data.get("mag_facts", ""),
            cag_hardware=cag_data.get("cag_hardware", ""),
            cag_ros=cag_data.get("cag_ros", ""),
            cag_environment=cag_data.get("cag_environment", ""),
            cag_errors=cag_data.get("cag_errors", ""),
            cag_ha=cag_data.get("cag_ha", ""),
            rag_memories=rag_memories,
            rag_knowledge=rag_knowledge,
            repeated_note=repeated_note,
            email_context=email_context,
            timestamp=timestamp
        )

        return final_prompt

    async def record_interaction(
        self,
        user_text: str,
        robot_response: str,
        actions_taken: Optional[List[str]] = None,
        was_successful: bool = True,
        user_identity: Optional[str] = None,
        summary: Optional[str] = None,
        extracted_facts: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        Post-interaction asynchronous hook.
        Persists episodic memory and extracted semantic facts to the MAG database.
        """
        if not self.enabled or not self.mag_episodic:
            return

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self.mag_episodic.record_episode(
                    user_input=user_text,
                    robot_response=robot_response,
                    actions=actions_taken or [],
                    was_successful=was_successful,
                    user_id=user_identity,
                    summary=summary,
                    extracted_facts=extracted_facts
                )
            )
            self.logger.debug("✅ Interaction stored successfully in MAG database.")
        except Exception as e:
            self.logger.error(f"Failed to record interaction in MAG: {e}")
