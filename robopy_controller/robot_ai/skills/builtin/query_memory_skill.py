"""
Skill for querying and searching autobiographical, episodic, and semantic memory.
Integrates with TRINITY MAG (SQLite WAL) and ChromaDB RAG.
"""

import time
import re
from typing import Any, Dict, List, Optional
from ..base_skill import BaseSkill, Capability, SkillMetadata, SkillResult
from ...utils import get_logger

logger = get_logger("query_memory_skill")


class QueryMemorySkill(BaseSkill):
    """
    Skill for searching and exploring Marcus's memories, learned facts,
    user preferences, and past conversational episodes.
    """

    def __init__(self, memory_manager=None, trinity_engine=None):
        super().__init__()
        self.memory_manager = memory_manager
        self._trinity_engine = trinity_engine
        self._mag_db = None

    def set_trinity_engine(self, trinity_engine: Any) -> None:
        """Sets or updates the TRINITY engine instance."""
        self._trinity_engine = trinity_engine

    def _get_mag_db(self) -> Optional[Any]:
        """Resolves the MAG database instance lazily."""
        if self._mag_db is not None:
            return self._mag_db

        if callable(self._trinity_engine) and not hasattr(self._trinity_engine, 'mag_db') and not hasattr(self._trinity_engine, 'mag_episodic'):
            try:
                engine = self._trinity_engine()
            except Exception:
                engine = self._trinity_engine
        else:
            engine = self._trinity_engine

        if engine and hasattr(engine, 'mag_db') and engine.mag_db:
            self._mag_db = engine.mag_db
            return self._mag_db
        if engine and hasattr(engine, 'mag_episodic') and engine.mag_episodic and hasattr(engine.mag_episodic, '_db'):
            self._mag_db = engine.mag_episodic._db
            return self._mag_db

        try:
            from ...trinity.mag_database import MAGDatabase
            self._mag_db = MAGDatabase()
            return self._mag_db
        except Exception as e:
            logger.debug(f"Could not initialize MAGDatabase directly: {e}")
            return None

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="query_memory",
            description="Cerca nella memoria a lungo termine del robot fatti, interazioni, preferenze utente o eventi passati.",
            version="1.0.0",
            keywords=[
                "cerca nella memoria", "interroga memoria", "accedi alla memoria",
                "accedi ai dati della memoria", "dati della memoria", "cosa ti ricordi",
                "cosa c'è nella memoria", "ricerche nella memoria", "ricordi",
                "memoria", "cosa sai di me", "fatti appresi", "cosa abbiamo fatto"
            ],
            priority=6,
            requires_internet=False,
            capabilities=[Capability.MEMORY_RW]
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        text_lower = text.lower()

        # Direct explicit trigger phrases for memory queries
        direct_triggers = [
            "accedi ai dati della memoria",
            "accedi alla memoria",
            "cerca nella memoria",
            "interroga la memoria",
            "fai una ricerca nella memoria",
            "cosa c'è nella memoria",
            "cosa ce nella memoria",
            "dati della memoria",
            "cosa ti ricordi",
            "cosa ricordi",
            "quali ricordi hai",
            "cosa sai di me",
            "cosa hai imparato",
            "cosa hai appreso",
            "fatti appresi",
            "chi sono io",
            "chi sono",
            "come mi chiamo",
            "ricordi cosa",
            "ti ricordi cosa",
            "ti ricordi di",
            "cosa abbiamo fatto",
            "cosa abbiamo detto"
        ]
        if any(dt in text_lower for dt in direct_triggers):
            return 0.98

        # Intent combination: memory reference + action/query
        has_memory = any(m in text_lower for m in [
            "memoria", "ricordi", "ricordare", "ricordo", "imparato",
            "appreso", "salvato", "preferenze"
        ])
        has_action = any(a in text_lower for a in [
            "accedi", "cerca", "trova", "mostra", "leggi", "dimmi",
            "elenca", "quali", "cosa", "interroga", "consultare"
        ])

        # Exclude purely technical documentation queries (handled by MemoryInfoSkill / ConsultDocumentationSkill)
        has_tech_doc = any(td in text_lower for td in [
            "documenti tecnici", "pdf caricati", "file caricati", "chunks rag"
        ])
        if has_tech_doc:
            return 0.0

        if has_memory and has_action:
            return 0.92

        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        ctx = context or {}
        raw_query = ctx.get("query") or ctx.get("text") or text or ""
        limit = int(ctx.get("limit") or 5)
        mem_type = (ctx.get("type") or ctx.get("memory_type") or "all").lower()

        logger.info(f"[QUERY_MEMORY] executing with query='{raw_query}', limit={limit}, type='{mem_type}'")

        clean_q = raw_query.strip()
        q_lower = clean_q.lower()

        # Identify if query is exploratory or requesting an overview of recent memories
        exploratory_phrases = [
            "accedi ai dati della memoria", "accedi alla memoria", "dati della memoria",
            "cosa c'è", "cosa ce", "cosa ricordi", "cosa ti ricordi", "quali ricordi",
            "memoria", "tutto", "recenti", "panoramica", "fatti appresi", "cosa sai",
            "cosa hai appreso", "stato memoria"
        ]
        is_exploratory = (
            not clean_q
            or any(ep in q_lower for ep in exploratory_phrases)
            or len(clean_q.split()) <= 2
        )

        mag_episodes = []
        mag_facts = []
        user_profiles = {}
        chroma_mems = []

        # 1. Query MAG Autobiographical Database (SQLite WAL)
        mag_db = self._get_mag_db()
        if mag_db:
            try:
                if mem_type in ("all", "facts", "semantic"):
                    if is_exploratory:
                        mag_facts = mag_db.get_all_facts()[:limit] if hasattr(mag_db, 'get_all_facts') else []
                    else:
                        mag_facts = mag_db.search_facts_fts(clean_q, limit=limit) if hasattr(mag_db, 'search_facts_fts') else []
                        if not mag_facts and hasattr(mag_db, 'get_all_facts'):
                            mag_facts = mag_db.get_all_facts()[:limit]

                if mem_type in ("all", "episodic"):
                    if is_exploratory:
                        mag_episodes = mag_db.get_recent_episodes(limit=limit) if hasattr(mag_db, 'get_recent_episodes') else []
                    else:
                        mag_episodes = mag_db.search_episodes_fts(clean_q, limit=limit) if hasattr(mag_db, 'search_episodes_fts') else []
                        if not mag_episodes and hasattr(mag_db, 'get_recent_episodes'):
                            mag_episodes = mag_db.get_recent_episodes(limit=limit)

                # Fetch user profiles if user-related
                if hasattr(mag_db, 'get_user_profile'):
                    # Check common users or default
                    user_profiles = mag_db.get_user_profile("Luca") or mag_db.get_user_profile("user") or {}
            except Exception as e:
                logger.warning(f"Error querying MAG database: {e}")

        # 2. Query ChromaDB Native Store (MemoryStore)
        if self.memory_manager and hasattr(self.memory_manager, 'memory_store') and self.memory_manager.memory_store:
            try:
                mem_store = self.memory_manager.memory_store
                if is_exploratory:
                    if hasattr(mem_store, 'get_recent'):
                        recent = mem_store.get_recent(limit=limit)
                        for m in recent:
                            c = getattr(m, 'content', '')
                            if c and c not in chroma_mems:
                                chroma_mems.append(c)
                else:
                    if hasattr(mem_store, 'search'):
                        res = await mem_store.search(clean_q, top_k=limit)
                        for r in res:
                            c = r.memory.content if hasattr(r, 'memory') else getattr(r, 'content', '')
                            if c and c not in chroma_mems:
                                chroma_mems.append(c)
                    if not chroma_mems and hasattr(mem_store, 'get_recent'):
                        recent = mem_store.get_recent(limit=limit)
                        for m in recent:
                            c = getattr(m, 'content', '')
                            if c and c not in chroma_mems:
                                chroma_mems.append(c)
            except Exception as e:
                logger.warning(f"Error querying ChromaDB in QueryMemorySkill: {e}")

        # 3. Assemble Spoken Summary & Formatted Markdown
        found_elements = []

        # Summarize facts
        facts_summary = []
        for f in mag_facts:
            f_text = f.get('fact_text') if isinstance(f, dict) else str(f)
            if f_text:
                facts_summary.append(f_text)
                found_elements.append(f"fatto: {f_text}")

        # Summarize episodes
        episodes_summary = []
        for ep in mag_episodes:
            if isinstance(ep, dict):
                u_in = ep.get('user_input', '').strip()
                r_out = ep.get('robot_response', '').strip()
                if u_in:
                    ep_repr = f"Utente: '{u_in}' -> Marcus: '{r_out[:60]}...'" if r_out else f"'{u_in}'"
                    episodes_summary.append(ep_repr)
                    found_elements.append(f"conversazione su '{u_in}'")

        # Summarize Chroma memories
        chroma_summary = []
        for cm in chroma_mems:
            clean_cm = re.sub(r'\s+', ' ', cm).strip()
            if clean_cm and clean_cm not in episodes_summary:
                chroma_summary.append(clean_cm)
                found_elements.append(clean_cm[:60])

        total_found = len(facts_summary) + len(episodes_summary) + len(chroma_summary)

        # Build Spoken Response (Natural Italian)
        if total_found == 0:
            speak_text = "Ho consultato la mia memoria, ma non ho trovato informazioni specifiche salvate a riguardo."
            message_text = "Ho interrogato i database di memoria (MAG e ChromaDB), ma non sono state trovate informazioni corrispondenti."
        else:
            speak_parts = []
            if facts_summary:
                first_fact = facts_summary[0]
                speak_parts.append(f"Ho consultato la mia memoria: ricordo ad esempio che {first_fact}.")
            elif episodes_summary:
                first_ep = mag_episodes[0].get('user_input', '') if mag_episodes else ""
                if first_ep:
                    speak_parts.append(f"Ho consultato la mia memoria: ricordo la nostra interazione su '{first_ep}'.")
                else:
                    speak_parts.append("Ho trovato delle interazioni recenti salvate nella mia memoria.")
            elif chroma_summary:
                first_cm = chroma_summary[0][:80]
                speak_parts.append(f"Nella mia memoria ho trovato questo riferimento: {first_cm}.")

            if len(facts_summary) > 1:
                speak_parts.append(f"Inoltre ho registrato altri {len(facts_summary) - 1} fatti appresi.")
            elif len(episodes_summary) > 1 and not facts_summary:
                speak_parts.append(f"Ci sono anche altre {len(episodes_summary) - 1} interazioni recenti memorizzate.")

            speak_text = " ".join(speak_parts)

            # Build detailed Markdown message for display
            md_lines = [f"### 🧠 Risultati Memoria Marcus (Query: '{clean_q or 'panoramica'}')\n"]
            if facts_summary:
                md_lines.append("**Fatti Appresi (Zettelkasten):**")
                for f in facts_summary:
                    md_lines.append(f"- 📌 {f}")
                md_lines.append("")

            if episodes_summary:
                md_lines.append("**Interazioni ed Episodi Recenti:**")
                for ep_str in episodes_summary[:5]:
                    md_lines.append(f"- 💬 {ep_str}")
                md_lines.append("")

            if chroma_summary and not episodes_summary:
                md_lines.append("**Memorie Semantiche RAG:**")
                for cm_str in chroma_summary[:5]:
                    md_lines.append(f"- 📄 {cm_str}")
                md_lines.append("")

            if user_profiles:
                md_lines.append("**Preferenze Utente:**")
                for k, v in user_profiles.items():
                    md_lines.append(f"- **{k}**: {v}")
                md_lines.append("")

            message_text = "\n".join(md_lines)

        return SkillResult.success_result(
            message=message_text,
            speak=speak_text,
            data={
                "query": clean_q,
                "total_found": total_found,
                "facts": facts_summary,
                "episodes": mag_episodes,
                "chroma_memories": chroma_summary,
                "user_profiles": user_profiles
            }
        )

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "La query di ricerca, argomento, persona, o 'tutto'/'recenti' per accedere ai dati della memoria.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Numero massimo di ricordi o fatti da recuperare (default 5).",
                    "default": 5,
                },
                "type": {
                    "type": "string",
                    "enum": ["all", "episodic", "semantic", "facts"],
                    "description": "Filtro per tipo di memoria: 'all', 'episodic' (interazioni passate), 'semantic' o 'facts' (fatti appresi).",
                    "default": "all"
                }
            },
            "required": ["query"]
        }
