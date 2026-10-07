"""
Skill for querying and searching autobiographical, episodic, and semantic memory.
Integrates with TRINITY MAG (SQLite WAL) and ChromaDB RAG.
"""

import time
import re
import datetime
from typing import Any, Dict, List, Optional
from ..base_skill import BaseSkill, Capability, SkillMetadata, SkillResult
from ...utils import get_logger
from ...trinity.mag_temporal_parser import MAGTemporalParser

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

        # Direct explicit administrative trigger phrases for raw memory inspection/dump
        direct_triggers = [
            "accedi ai dati della memoria",
            "accedi alla memoria",
            "interroga la memoria",
            "interroga il database della memoria",
            "fai una ricerca nella memoria",
            "stato dei database di memoria",
            "stato della memoria",
            "dump della memoria",
            "ispeziona memoria"
        ]
        if any(dt in text_lower for dt in direct_triggers):
            # Cap confidence below fast-path threshold (0.95) to ensure LLM/TRINITY orchestration
            return 0.85

        # Exclude purely technical documentation queries (handled by MemoryInfoSkill / ConsultDocumentationSkill)
        has_tech_doc = any(td in text_lower for td in [
            "documenti tecnici", "pdf caricati", "file caricati", "chunks rag"
        ])
        if has_tech_doc:
            return 0.0

        # Administrative intent: explicit memory reference + explicit database action
        has_admin_memory = any(m in text_lower for m in [
            "database memoria", "dati della memoria", "tabelle memoria", "indice vettoriale"
        ])
        has_action = any(a in text_lower for a in [
            "accedi", "mostra", "elenca", "interroga", "consultare", "ispeziona"
        ])

        if has_admin_memory and has_action:
            return 0.85

        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        ctx = context or {}
        raw_query = ctx.get("query") or ctx.get("text") or text or ""
        limit = int(ctx.get("limit") or 5)
        mem_type = (ctx.get("type") or ctx.get("memory_type") or "all").lower()

        logger.info(f"[QUERY_MEMORY] executing with query='{raw_query}', limit={limit}, type='{mem_type}'")

        clean_q = raw_query.strip()
        q_lower = clean_q.lower()

        # Check if user is asking if Marcus maps dates / timestamps
        is_date_mapping = bool(re.search(
            r'\b(mappa(?:re)?\s+le\s+date|date\s+dei\s+(?:tuoi\s+)?ricordi|salvi\s+le\s+date|registri\s+le\s+date|quando\s+(?:crei|salvi)\s+i\s+ricordi)\b',
            q_lower
        ))

        mag_db = self._get_mag_db()

        # Check if query is asking for frequency or statistics analysis
        is_stats_query = MAGTemporalParser.is_frequency_or_stats_query(clean_q)
        if is_stats_query and mag_db and hasattr(mag_db, 'get_episodes_frequency_stats'):
            stats = mag_db.get_episodes_frequency_stats(days=7)
            total_p = stats.get('total_episodes_period', 0)
            avg_p = stats.get('average_per_day', 0.0)
            speak_text = (
                f"Ho analizzato la frequenza dei miei ricordi: negli ultimi 7 giorni ho registrato "
                f"{total_p} episodi, con una media di {avg_p} interazioni al giorno."
            )
            md_lines = [
                "### 📊 Analisi Temporale e Frequenza Ricordi (MAG)",
                f"- **Periodo analizzato:** Ultimi {stats.get('days', 7)} giorni",
                f"- **Episodi totali registrati:** {total_p}",
                f"- **Media giornaliera:** {avg_p} interazioni/giorno",
                "\n**Distribuzione giornaliera:**"
            ]
            for day_str, count in stats.get('daily_distribution', {}).items():
                md_lines.append(f"- 📅 `{day_str}`: {count} episodi")
            if not stats.get('daily_distribution'):
                md_lines.append("- *(Nessun episodio registrato nel periodo selezionato)*")
            
            return SkillResult.success_result(
                message="\n".join(md_lines),
                speak=speak_text,
                data=stats
            )

        # Identify if query specifies a temporal range (e.g. 'ieri', '2 ottobre', 'oggi', 'questa settimana')
        temporal_info = MAGTemporalParser.parse_time_range(clean_q)

        # Identify if query is exploratory or requesting an overview of recent memories
        exploratory_phrases = [
            "accedi ai dati della memoria", "accedi alla memoria", "dati della memoria",
            "cosa c'è", "cosa ce", "cosa ricordi", "cosa ti ricordi", "quali ricordi",
            "memoria", "tutto", "recenti", "panoramica", "fatti appresi", "cosa sai",
            "cosa hai appreso", "stato memoria"
        ]
        is_exploratory = (
            not clean_q
            or is_date_mapping
            or any(ep in q_lower for ep in exploratory_phrases)
            or len(clean_q.split()) <= 2
        )

        mag_episodes = []
        mag_facts = []
        user_profiles = {}
        chroma_mems = []

        # 1. Query MAG Autobiographical Database (SQLite WAL)
        if mag_db:
            try:
                if temporal_info:
                    start_ts, end_ts, t_label = temporal_info
                    if mem_type in ("all", "episodic") and hasattr(mag_db, 'get_episodes_by_timerange'):
                        mag_episodes = mag_db.get_episodes_by_timerange(start_ts, end_ts, limit=limit)
                        clean_sub = MAGTemporalParser.clean_temporal_tokens(clean_q)
                        if clean_sub and len(clean_sub.split()) >= 1 and mag_episodes:
                            sub_tokens = set(clean_sub.lower().split())
                            def score_ep(ep):
                                text = f"{ep.get('user_input', '')} {ep.get('robot_response', '')}".lower()
                                return sum(1 for tok in sub_tokens if tok in text)
                            mag_episodes = sorted(mag_episodes, key=score_ep, reverse=True)

                    if mem_type in ("all", "facts", "semantic") and hasattr(mag_db, 'get_facts_by_timerange'):
                        mag_facts = mag_db.get_facts_by_timerange(start_ts, end_ts, limit=limit)
                else:
                    if mem_type in ("all", "facts", "semantic"):
                        if is_exploratory:
                            mag_facts = mag_db.get_all_facts()[:limit] if hasattr(mag_db, 'get_all_facts') else []
                        else:
                            mag_facts = mag_db.search_facts_fts(clean_q, limit=limit) if hasattr(mag_db, 'search_facts_fts') else []

                    if mem_type in ("all", "episodic"):
                        if is_exploratory:
                            mag_episodes = mag_db.get_recent_episodes(limit=limit) if hasattr(mag_db, 'get_recent_episodes') else []
                        else:
                            mag_episodes = mag_db.search_episodes_fts(clean_q, limit=limit) if hasattr(mag_db, 'search_episodes_fts') else []

                # Fetch user profiles if user-related
                if hasattr(mag_db, 'get_user_profile'):
                    user_profiles = mag_db.get_user_profile("Luca") or mag_db.get_user_profile("user") or {}
            except Exception as e:
                logger.warning(f"Error querying MAG database: {e}")

        # 2. Query ChromaDB Native Store (MemoryStore) if not temporal query
        if not temporal_info and self.memory_manager and hasattr(self.memory_manager, 'memory_store') and self.memory_manager.memory_store:
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
            except Exception as e:
                logger.warning(f"Error querying ChromaDB in QueryMemorySkill: {e}")

        # 3. Assemble Spoken Summary & Formatted Markdown
        found_elements = []

        # Summarize facts with dates
        facts_summary = []
        for f in mag_facts:
            f_text = f.get('fact_text') if isinstance(f, dict) else str(f)
            if f_text:
                created_at = f.get('created_at') if isinstance(f, dict) else None
                date_str = ""
                if created_at:
                    try:
                        date_str = datetime.datetime.fromtimestamp(float(created_at)).strftime("%d/%m/%Y")
                    except Exception:
                        pass
                f_display = f"[{date_str}] {f_text}" if date_str else f_text
                facts_summary.append(f_display)
                found_elements.append(f"fatto: {f_display}")

        # Summarize episodes with dates and times
        episodes_summary = []
        for ep in mag_episodes:
            if isinstance(ep, dict):
                u_in = ep.get('user_input', '').strip()
                r_out = ep.get('robot_response', '').strip()
                ts = ep.get('timestamp')
                dt_str = ""
                if ts:
                    try:
                        from zoneinfo import ZoneInfo
                        dt = datetime.datetime.fromtimestamp(float(ts), tz=ZoneInfo("Europe/Rome"))
                        dt_str = dt.strftime("%d/%m/%Y %H:%M")
                    except Exception:
                        try:
                            dt = datetime.datetime.fromtimestamp(float(ts))
                            dt_str = dt.strftime("%d/%m/%Y %H:%M")
                        except Exception:
                            pass
                if u_in:
                    date_prefix = f"[{dt_str}] " if dt_str else ""
                    ep_repr = f"{date_prefix}Utente: '{u_in}' -> Marcus: '{r_out[:60]}...'" if r_out else f"{date_prefix}'{u_in}'"
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
            if temporal_info:
                speak_text = f"Ho controllato la mia memoria per '{temporal_info[2]}', ma non ho trovato ricordi o eventi registrati in quell'intervallo."
                message_text = f"Nessun ricordo trovato nel database autobiografico per l'intervallo '{temporal_info[2]}'."
            else:
                speak_text = f"Ho consultato la mia memoria per '{clean_q}', ma non ho trovato informazioni specifiche salvate a riguardo."
                message_text = f"Ho interrogato i database di memoria (MAG e ChromaDB), ma non sono state trovate informazioni corrispondenti per '{clean_q}'."
        else:
            speak_parts = []
            if is_date_mapping:
                speak_parts.append(
                    "Certamente, la mia memoria mappa con precisione millimetrica la data e l'ora esatta di ogni ricordo, "
                    "interazione ed evento che registro nel database autobiografico."
                )

            if facts_summary:
                first_fact = facts_summary[0]
                speak_parts.append(f"Ricordo ad esempio: {first_fact}.")
                if len(facts_summary) > 1:
                    speak_parts.append(f"Inoltre ho registrato altri {len(facts_summary) - 1} fatti correlati.")

            if episodes_summary:
                first_ep = mag_episodes[0]
                ep_u = first_ep.get('user_input', '') if isinstance(first_ep, dict) else ""
                ep_ts = first_ep.get('timestamp') if isinstance(first_ep, dict) else None
                dt_verbal = ""
                if ep_ts:
                    try:
                        from zoneinfo import ZoneInfo
                        dt = datetime.datetime.fromtimestamp(float(ep_ts), tz=ZoneInfo("Europe/Rome"))
                        dt_verbal = dt.strftime("il %d/%m alle %H:%M")
                    except Exception:
                        pass

                if dt_verbal and ep_u:
                    speak_parts.append(f"Riguardo agli eventi registrati, {dt_verbal} abbiamo parlato di '{ep_u}'.")
                elif ep_u:
                    speak_parts.append(f"Riguardo alle nostre interazioni, ricordo la conversazione su '{ep_u}'.")
                else:
                    speak_parts.append(f"Ho trovato {len(episodes_summary)} interazioni salvate.")
            elif chroma_summary and not facts_summary:
                first_cm = chroma_summary[0][:80]
                speak_parts.append(f"Nella mia memoria ho trovato questo riferimento: {first_cm}.")

            speak_text = " ".join(speak_parts)

            # Build detailed Markdown message for display
            md_lines = [f"### 🧠 Risultati Memoria Marcus (Query: '{clean_q or 'panoramica'}')\n"]
            if temporal_info:
                md_lines.append(f"⏱️ **Filtro temporale:** `{temporal_info[2]}`\n")
            if facts_summary:
                md_lines.append("**Fatti Appresi (Zettelkasten):**")
                for f in facts_summary:
                    md_lines.append(f"- 📌 {f}")
                md_lines.append("")

            if episodes_summary:
                md_lines.append("**Interazioni ed Episodi Registrati (con data/ora):**")
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
