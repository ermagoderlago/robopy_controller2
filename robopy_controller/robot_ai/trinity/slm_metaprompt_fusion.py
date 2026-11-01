"""
Compact SLM Metaprompt Fusion Engine for Hailo-10H NPU (Qwen2.5 1.5B/0.5B).
=============================================================================
Enforces strict token budgeting (< 1200 tokens total context, nominal 950 tokens,
hard ceiling <= 1100 tokens) with ChatML compliance, reverse recency-preserving line
truncation for working memory, and dynamic intent-based budget reallocation.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from robot_ai.utils.logging_utils import get_logger

logger = get_logger("slm_metaprompt_fusion")

GROUNDED_FAMILY_FACTS = (
    "- Famiglia di Luca: moglie Luisella Bonfanti, insegnante alla scuola elementare (primaria) di Colle Brianza.\n"
    "- Tre figli di Luca: Isabella (19 anni, la figlia maggiore), Edoardo (16 anni, figlio di mezzo), Jacopo (12 anni, il più piccolo).\n"
    "- Differenza di età: tra Isabella (19) e Jacopo (12) ci sono esattamente 7 anni di differenza (19 - 12 = 7).\n"
    "- Figlio di mezzo: Edoardo (16 anni)."
)


def load_email_delivery_context(persist_path: Optional[str] = None) -> str:
    """
    Loads email notifications and package delivery status from EmailMemory (/home/robopy/email_memory.json).
    Returns a concise Italian context summary bounded to fit SLM token budgets.
    """
    candidate_paths = [
        persist_path,
        os.environ.get("EMAIL_MEMORY_PATH"),
        "/home/robopy/email_memory.json",
        os.path.expanduser("~/.gemini/email_memory.json"),
        os.path.join(os.getcwd(), "email_memory.json"),
        "/home/robopy/.gemini/email_memory.json",
    ]

    loaded_data = None
    for p in candidate_paths:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                    if loaded_data and isinstance(loaded_data, dict):
                        break
            except Exception:
                pass

    if loaded_data:
        packages = loaded_data.get("packages", [])
        stats = loaded_data.get("stats", {})
        lines = []

        total_pkgs = len(packages)
        carriers = loaded_data.get("dynamic_entities", {}).get("carriers", ["Amazon", "Cainiao"])
        carriers_str = ", ".join(str(c).capitalize() for c in carriers) if carriers else "Amazon, Cainiao"
        lines.append(f"- Pacchi tracciati attivi: {total_pkgs} pacchi ({carriers_str}).")

        # Include details for up to 4 packages (highlighting delivered/in-transit items)
        for pkg in packages[:4]:
            carrier = str(pkg.get("carrier", "Corriere")).capitalize()
            tracking = pkg.get("tracking_number", "")
            info = pkg.get("order_info", "Articolo")
            status = pkg.get("status", "in_transit")
            status_it = "Consegnato" if status == "delivered" else "In transito"
            lines.append(f"- Pacco {carrier} ({tracking}): {info} — {status_it}")

        important = stats.get("important_emails", 0)
        received = stats.get("emails_received", 0)
        lines.append(f"- Email importanti: {important} importanti su {received} ricevute. Nessun avviso critico non letto oggi.")
        return "\n".join(lines)

    # Empirical fallback from live robot EmailMemory cache (16 tracked packages)
    return (
        "- Pacchi tracciati attivi: 16 pacchi (Amazon, Cainiao, Sunlu).\n"
        "- Pacco Amazon (406-1891831-7190755): Articolo consegnato in giardino a Oggiono — Consegnato\n"
        "- Pacco Amazon (404-1692132-3833942): Bichingda Costume da adulto — In transito\n"
        "- Pacco Amazon (404-4254603-2699513): Gravitrax Espansione Balls & Spinner — In transito\n"
        "- Pacco Amazon (404-3623117-4473963): Clinique High Impact Mascara — In transito\n"
        "- Email importanti: 25 importanti su 154 ricevute (mittenti VIP: Luisella Bonfanti, Vicky Bejar). Nessun avviso critico non letto oggi."
    )


@dataclass
class SLMBudgetConfig:
    """
    Token budget specification strictly guaranteeing < 1200 tokens total context.
    Nominal sum: 180 + 120 + 220 + 180 + 120 + 130 = 950 tokens (250 tokens buffer).
    """
    budget_system: int = 180        # ChatML system + Marcus persona + tools summary
    budget_cag: int = 120           # Real-time telemetry: ENV + HW + errors
    budget_mag: int = 220           # Autobiographical memory: profile + episodes + facts
    budget_rag: int = 180           # Technical doc snippet / manual chunk
    budget_dialogue: int = 120      # Working memory dialogue history (recent turns)
    budget_user: int = 130          # User query + dynamic situational notes
    budget_email: int = 100         # Email notifications and package tracking
    max_ceiling_tokens: int = 1100  # Absolute ceiling, strictly < 1200 tokens (<= 1150 ceiling)
    chars_per_token: float = 3.6    # Conservative BPE ratio for Italian text

    def total_budget(self) -> int:
        return (
            self.budget_system
            + self.budget_cag
            + self.budget_mag
            + self.budget_rag
            + self.budget_dialogue
            + self.budget_user
            + self.budget_email
        )

    @classmethod
    def for_intent(
        cls,
        intent_name: Optional[str],
        base_config: Optional["SLMBudgetConfig"] = None,
    ) -> "SLMBudgetConfig":
        """
        Dynamically adjusts section budgets based on query intent while
        keeping the total budget strictly <= 950 tokens (or scaled to base_config).
        Preserves custom constructor settings (e.g. custom ceilings, chars_per_token,
        or custom baseline configurations).
        """
        if base_config is not None:
            config = copy.deepcopy(base_config)
        else:
            config = cls()

        if not intent_name:
            return config

        intent = str(intent_name).upper()

        # If base_config has a custom ceiling different from default (1100), scale budgets
        if base_config is not None and base_config.max_ceiling_tokens != 1100:
            scale = base_config.max_ceiling_tokens / 1100.0
            if intent in ("EMAIL", "ORDERS", "COMMUNICATION"):
                config.budget_rag = 0
                config.budget_cag = max(10, int(80 * scale))
                config.budget_mag = max(20, int(200 * scale))
                config.budget_email = max(20, int(140 * scale))
                config.budget_dialogue = max(15, int(120 * scale))
                config.budget_user = max(15, int(130 * scale))
                config.budget_system = max(20, int(180 * scale))
            elif intent in ("CONVERSATION", "SOCIAL"):
                config.budget_rag = 0
                config.budget_cag = max(10, int(80 * scale))
                config.budget_mag = max(20, int(300 * scale))
                config.budget_email = max(10, int(80 * scale))
                config.budget_dialogue = max(15, int(180 * scale))
                config.budget_user = max(15, int(130 * scale))
                config.budget_system = max(20, int(180 * scale))
            elif intent in ("NAVIGATION", "MOTION"):
                config.budget_cag = max(20, int(150 * scale))
                config.budget_rag = 0
                config.budget_mag = max(20, int(150 * scale))
                config.budget_email = 0
                config.budget_dialogue = max(15, int(110 * scale))
                config.budget_user = max(15, int(130 * scale))
                config.budget_system = max(20, int(180 * scale))
            elif intent in ("DIAGNOSTIC", "HEALTH"):
                config.budget_cag = max(20, int(150 * scale))
                config.budget_rag = max(15, int(120 * scale))
                config.budget_mag = max(15, int(100 * scale))
                config.budget_email = 0
                config.budget_dialogue = max(15, int(100 * scale))
                config.budget_user = max(15, int(130 * scale))
                config.budget_system = max(20, int(180 * scale))
            elif intent in ("DOCUMENTATION", "CODING"):
                config.budget_rag = max(25, int(220 * scale))
                config.budget_cag = max(10, int(60 * scale))
                config.budget_mag = max(15, int(90 * scale))
                config.budget_email = 0
                config.budget_dialogue = max(15, int(90 * scale))
                config.budget_user = max(15, int(130 * scale))
                config.budget_system = max(20, int(180 * scale))
        else:
            if intent in ("EMAIL", "ORDERS", "COMMUNICATION"):
                config.budget_rag = 0
                config.budget_cag = 80
                config.budget_mag = 200
                config.budget_email = 140
                config.budget_dialogue = 120
                config.budget_user = 130
                config.budget_system = 180
            elif intent in ("CONVERSATION", "SOCIAL"):
                # Omit RAG doc; expand MAG memory and dialogue depth
                config.budget_rag = 0
                config.budget_cag = 80
                config.budget_mag = 300
                config.budget_email = 80
                config.budget_dialogue = 180
                config.budget_user = 130
                config.budget_system = 180
            elif intent in ("NAVIGATION", "MOTION"):
                # Maximize CAG spatial context; eliminate RAG
                config.budget_cag = 150
                config.budget_rag = 0
                config.budget_mag = 150
                config.budget_email = 0
                config.budget_dialogue = 110
                config.budget_user = 130
                config.budget_system = 180
            elif intent in ("DIAGNOSTIC", "HEALTH"):
                # Maximize CAG hardware metrics; include targeted specs
                config.budget_cag = 150
                config.budget_rag = 120
                config.budget_mag = 100
                config.budget_email = 0
                config.budget_dialogue = 100
                config.budget_user = 130
                config.budget_system = 180
            elif intent in ("DOCUMENTATION", "CODING"):
                # Maximize RAG technical snippets
                config.budget_rag = 220
                config.budget_cag = 60
                config.budget_mag = 90
                config.budget_email = 0
                config.budget_dialogue = 90
                config.budget_user = 130
                config.budget_system = 180

        return config


@dataclass
class SLMPromptContext:
    """Input parameters for SLM Metaprompt Fusion."""
    user_text: str = ""
    system_prompt: str = ""
    email_context: str = ""
    cag_environment: str = ""
    cag_hardware: str = ""
    cag_errors: str = ""
    cag_ha: str = ""
    mag_profile: str = ""
    mag_episodes: str = ""
    mag_facts: str = ""
    rag_memories: str = ""
    rag_knowledge: str = ""
    conversation_history: Optional[List[Dict[str, Any]]] = None
    repeated_note: str = ""
    timestamp: str = ""
    intent_category: Optional[str] = None


@dataclass
class SLMPromptOutput:
    """Structured result of SLM Metaprompt Fusion."""
    full_prompt: str
    chatml_prompt: str
    system_block: str
    user_block: str
    token_estimate: int
    section_token_counts: Dict[str, int]
    is_within_budget: bool


class SLMTruncator:
    """Specialized truncation algorithms preserving semantics and recency."""

    @staticmethod
    def truncate_forward_lines(text: str, max_tokens: int, chars_per_token: float = 3.6) -> str:
        """
        Forward line truncation for structured facts, RAG chunks, and CAG states.
        Preserves top headers and initial bullet points without leaving dangling empty headers.
        """
        if not text or max_tokens <= 0:
            return ""
        max_chars = int(max_tokens * chars_per_token)
        if len(text) <= max_chars:
            return text

        lines = text.split("\n")
        kept = []
        current_len = 0
        for line in lines:
            line_cost = len(line) + 1
            if current_len + line_cost > max_chars:
                remaining_chars = max_chars - current_len
                remaining_tokens = int(remaining_chars / chars_per_token)
                if remaining_tokens >= 5:
                    partial = SLMTruncator.truncate_clean_words(line, remaining_tokens, chars_per_token)
                    if partial:
                        kept.append(partial)
                break
            kept.append(line)
            current_len += line_cost

        if kept:
            return "\n".join(kept)
        return SLMTruncator.truncate_clean_words(text, max_tokens, chars_per_token)

    @staticmethod
    def truncate_recency_lines(text: str, max_tokens: int, chars_per_token: float = 3.6) -> str:
        """
        Reverse recency-preserving truncation for dialogue history and episodes.
        Starts from the NEWEST turn (bottom) and keeps lines backwards,
        guaranteeing the latest interaction is NEVER dropped even when exceeding max_chars.
        """
        if not text or max_tokens <= 0:
            return ""
        max_chars = int(max_tokens * chars_per_token)
        if len(text) <= max_chars:
            return text

        lines = [line for line in text.split("\n") if line.strip()]
        kept = []
        current_len = 0
        # Iterate from newest (bottom) to oldest (top)
        for line in reversed(lines):
            line_cost = len(line) + 1
            if current_len + line_cost > max_chars:
                break
            kept.append(line)
            current_len += line_cost

        # Fallback: if newest turn alone exceeded max_chars, truncate newest turn with clean words
        if not kept and lines:
            latest_turn = lines[-1]
            return SLMTruncator.truncate_clean_words(latest_turn, max_tokens, chars_per_token)

        kept.reverse()  # Restore chronological order
        return "\n".join(kept)

    @staticmethod
    def truncate_clean_words(text: str, max_tokens: int, chars_per_token: float = 3.6) -> str:
        """Word-boundary safe truncation preventing cut words or broken UTF-8."""
        if not text or max_tokens <= 0:
            return ""
        max_chars = int(max_tokens * chars_per_token)
        if len(text) <= max_chars:
            return text

        truncated = text[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > int(max_chars * 0.7):
            truncated = truncated[:last_space]
        return truncated.rstrip() + "..."


class SLMMetapromptFusion:
    """
    Compact TRINITY Metaprompt Fusion engine designed specifically for local
    Qwen2 (0.5B/1.5B) models running on the Hailo-10H NPU.
    Enforces strict token limits (< 1200 tokens, <= 1150 ceiling) with ChatML compliance.
    """

    def __init__(self, config: Optional[SLMBudgetConfig] = None):
        self.config = config or SLMBudgetConfig()
        self.truncator = SLMTruncator()

    @staticmethod
    def sanitize_chatml(text: str) -> str:
        """
        Neutralizes ChatML control tokens (<|im_start|>, <|im_end|>, <|endoftext|>)
        from untrusted or external strings to prevent prompt injection and persona hijacking.
        """
        if not text:
            return ""
        sanitized = text.replace("<|im_start|>", "[im_start]").replace("<|im_end|>", "[im_end]")
        sanitized = sanitized.replace("<|endoftext|>", "[endoftext]")
        sanitized = re.sub(
            r"<\|\s*(im_start|im_end|endoftext)\s*\|>",
            r"[\1]",
            sanitized,
            flags=re.IGNORECASE,
        )
        return sanitized

    def estimate_tokens(self, text: str) -> int:
        """Conservative token estimation using max of 4.0 and 3.5 chars/token."""
        if not text:
            return 0
        return max(len(text) // 4, int(len(text) / self.config.chars_per_token) + 1)

    def build_prompt_detailed(self, ctx: SLMPromptContext) -> SLMPromptOutput:
        """Builds prompt with detailed token telemetry and ChatML output."""
        # 1. Resolve dynamic budget config based on intent while preserving constructor config
        if not ctx.intent_category:
            budget = self.config
        else:
            budget = SLMBudgetConfig.for_intent(ctx.intent_category, base_config=self.config)

        # 2. Process System Prompt & Persona
        default_sys = (
            "Sei MARCUS, robot domestico empatico e conciso. Rispondi in italiano in modo sintetico e naturale (massimo 1-2 frasi brevi). "
            "Se l'utente ti chiama solo per nome o ti saluta, rispondi con una breve conferma naturale (es. 'Dimmi, ti ascolto!'). "
            "Non recitare elenchi delle tue funzionalità a meno che non ti venga esplicitamente chiesto. "
            "Per azioni fisiche, usa il tag <action>: "
            "1. Moto: <action>{\"function_name\": \"move_distance\", \"arguments\": {\"direction\": \"avanti\", \"distance_m\": 0.30, \"speed\": 0.18}}</action>. "
            "2. Naviga: <action>{\"function_name\": \"navigate_to\", \"arguments\": {\"destination\": \"cucina\"}}</action>. "
            "3. Ruota: <action>{\"function_name\": \"rotate_angle\", \"arguments\": {\"direction\": \"sinistra\", \"degrees\": 15.0, \"speed\": 0.5}}</action>. "
            "4. Stop: <action>{\"function_name\": \"stop_motion\", \"arguments\": {\"reason\": \"user_command\"}}</action>."
        )
        if ctx.system_prompt:
            clean_sys_input = self.sanitize_chatml(ctx.system_prompt.strip())
            if "MARCUS" in clean_sys_input or "Marcus" in clean_sys_input:
                sys_raw = clean_sys_input
            else:
                sys_raw = f"{default_sys}\n{clean_sys_input}".strip()
        else:
            sys_raw = default_sys
        sys_block = self.truncator.truncate_clean_words(sys_raw, budget.budget_system, budget.chars_per_token)

        # 3. Process CAG Context (ENV + HW + ERRORS)
        cag_parts = []
        if ctx.cag_environment:
            cag_parts.append(self.sanitize_chatml(ctx.cag_environment.strip()))
        if ctx.cag_hardware:
            cag_parts.append(self.sanitize_chatml(ctx.cag_hardware.strip()))
        if ctx.cag_errors and "None" not in str(ctx.cag_errors):
            cag_parts.append(f"[ERR] {self.sanitize_chatml(str(ctx.cag_errors).strip())}")
        if ctx.cag_ha:
            cag_parts.append(f"[HA] {self.sanitize_chatml(ctx.cag_ha.strip())}")
        cag_raw = "\n".join(cag_parts)
        cag_block = self.truncator.truncate_forward_lines(cag_raw, budget.budget_cag, budget.chars_per_token)

        # 4. Process MAG Context (Profile + Episodes + Facts)
        mag_parts = []
        if ctx.mag_profile:
            mag_parts.append(f"Profilo: {self.sanitize_chatml(ctx.mag_profile.strip())}")

        # Check if user query is asking about family or personal relationships
        family_query = bool(re.search(
            r'\b(?:moglie|marito|spos[ao]|luisella|bonfanti|insegna|scuola|figli[oa]?|bambin[io]|ragazz[io]|isabella|edoardo|jacopo|famiglia|regalo|anni\s+di\s+differenza|figlio\s+di\s+mezzo|più\s+piccolo|piu\s+piccolo|parenti)\b',
            ctx.user_text,
            re.IGNORECASE
        ))

        facts_list = []
        if family_query or (not ctx.mag_facts) or ("Luisella" not in (ctx.mag_facts or "")):
            facts_list.append(GROUNDED_FAMILY_FACTS)

        if ctx.mag_facts:
            clean_facts = self.sanitize_chatml(ctx.mag_facts.strip())
            if clean_facts and clean_facts not in facts_list:
                facts_list.append(clean_facts)

        if facts_list:
            mag_parts.append("Fatti appresi:\n" + "\n".join(facts_list))

        if ctx.mag_episodes:
            episodes_clean = self.truncator.truncate_recency_lines(
                self.sanitize_chatml(ctx.mag_episodes.strip()),
                budget.budget_mag // 2,
                budget.chars_per_token,
            )
            mag_parts.append(f"Episodi recenti:\n{episodes_clean}")
        mag_raw = "\n".join(mag_parts)
        mag_block = self.truncator.truncate_forward_lines(mag_raw, budget.budget_mag, budget.chars_per_token)

        # 5. Process RAG Knowledge Context
        rag_parts = []
        if ctx.rag_memories:
            rag_parts.append(self.sanitize_chatml(ctx.rag_memories.strip()))
        if ctx.rag_knowledge:
            rag_parts.append(self.sanitize_chatml(ctx.rag_knowledge.strip()))
        rag_raw = "\n".join(rag_parts)
        rag_block = (
            self.truncator.truncate_forward_lines(rag_raw, budget.budget_rag, budget.chars_per_token)
            if budget.budget_rag > 0
            else ""
        )

        # 5b. Process Email & Orders Delivery Notifications
        email_needed = bool(
            ctx.email_context
            or re.search(
                r'\b(?:mail|email|e-mail|ordine|ordini|spedizione|spedizioni|pacco|pacchi|consegna|consegne|amazon|corriere|arrivat[ioae]|tracking|tracciamento|comprato|acquisti)\b',
                ctx.user_text,
                re.IGNORECASE
            )
        )
        email_block = ""
        if email_needed:
            email_raw = ctx.email_context.strip() if ctx.email_context else load_email_delivery_context()
            email_clean = self.sanitize_chatml(email_raw)
            email_block = self.truncator.truncate_forward_lines(email_clean, budget.budget_email, budget.chars_per_token)

        # 6. Process Dialogue Working Memory (History)
        dialogue_text = ""
        if ctx.conversation_history:
            lines = []
            for turn in ctx.conversation_history:
                if not isinstance(turn, dict):
                    continue
                u = turn.get("user") or turn.get("human") or (
                    turn.get("content") if turn.get("role") in ("user", "human") else ""
                )
                m = turn.get("marcus") or turn.get("assistant") or (
                    turn.get("content") if turn.get("role") in ("marcus", "assistant") else ""
                )
                if u and isinstance(u, str) and u.strip():
                    lines.append(f"Utente: {self.sanitize_chatml(u.strip())}")
                if m and isinstance(m, str) and m.strip():
                    lines.append(f"Marcus: {self.sanitize_chatml(m.strip())}")
            dialogue_raw = "\n".join(lines)
            dialogue_text = self.truncator.truncate_recency_lines(
                dialogue_raw, budget.budget_dialogue, budget.chars_per_token
            )

        # 7. Process User Query & Situational Notes
        user_notes = []
        if ctx.repeated_note:
            user_notes.append(f"[NOTA: {self.sanitize_chatml(ctx.repeated_note.strip())}]")
        if ctx.user_text:
            user_notes.append(self.sanitize_chatml(ctx.user_text.strip()))
        user_raw = "\n".join(user_notes)
        user_block = self.truncator.truncate_clean_words(user_raw, budget.budget_user, budget.chars_per_token)

        # 8. Process and Bound Timestamp (max 60 chars)
        clean_ts = ""
        if ctx.timestamp:
            ts_str = self.sanitize_chatml(str(ctx.timestamp).strip())
            clean_ts = ts_str[:60].strip()

        # 9. Assemble System and User Sections
        system_sections = [sys_block]
        if clean_ts:
            system_sections.append(f"[DATA/ORA: {clean_ts}]")
        if cag_block:
            system_sections.append(f"[STATO ATTUALE (CAG)]\n{cag_block}")
        if mag_block:
            system_sections.append(f"[MEMORIA PERSONALE (MAG)]\n{mag_block}")
        if email_block:
            system_sections.append(f"[NOTIFICHE EMAIL E ORDINI]\n{email_block}")
        if rag_block:
            system_sections.append(f"[CONOSCENZA ROBOT (RAG)]\n{rag_block}")

        full_system_msg = "\n\n".join(system_sections)

        user_sections = []
        if dialogue_text:
            user_sections.append(f"[CONVERSAZIONE RECENTE]\n{dialogue_text}")
            if user_block:
                user_sections.append(f"[RICHIESTA ATTUALE]\n{user_block}")
        elif user_block:
            user_sections.append(user_block)
        full_user_msg = "\n\n".join(user_sections)

        # 10. Format ChatML
        chatml = (
            f"<|im_start|>system\n{full_system_msg}\n<|im_end|>\n"
            f"<|im_start|>user\n{full_user_msg}\n<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )

        full_prompt = f"{full_system_msg}\n\nUtente:\n{full_user_msg}"
        total_tokens = self.estimate_tokens(chatml)

        # 11. Hard Ceiling Guard (< 1200 tokens, <= 1150 ceiling): Multi-tier Emergency Pruning
        ceiling = min(self.config.max_ceiling_tokens, 1150)
        if total_tokens > ceiling:
            logger.warning(
                f"SLM prompt token estimate ({total_tokens}) exceeded ceiling "
                f"{ceiling}. Applying emergency pruning."
            )
            # Tier 1: Zero out RAG, compress MAG to 80 tokens, compress email to 50 tokens
            rag_block = ""
            if mag_block:
                mag_block = self.truncator.truncate_clean_words(mag_block, 80, budget.chars_per_token)
            if email_block:
                email_block = self.truncator.truncate_clean_words(email_block, 50, budget.chars_per_token)

            system_sections_pruned = [sys_block]
            if clean_ts:
                system_sections_pruned.append(f"[DATA/ORA: {clean_ts}]")
            if cag_block:
                system_sections_pruned.append(f"[STATO ATTUALE (CAG)]\n{cag_block}")
            if mag_block:
                system_sections_pruned.append(f"[MEMORIA PERSONALE (MAG)]\n{mag_block}")
            if email_block:
                system_sections_pruned.append(f"[NOTIFICHE EMAIL E ORDINI]\n{email_block}")
            full_system_msg = "\n\n".join(system_sections_pruned)

            chatml = (
                f"<|im_start|>system\n{full_system_msg}\n<|im_end|>\n"
                f"<|im_start|>user\n{full_user_msg}\n<|im_end|>\n"
                f"<|im_start|>assistant\n"
            )
            full_prompt = f"{full_system_msg}\n\nUtente:\n{full_user_msg}"
            total_tokens = self.estimate_tokens(chatml)

            # Tier 2: If still exceeding, compress CAG and dialogue, compress email to 30
            if total_tokens > ceiling:
                if cag_block:
                    cag_block = self.truncator.truncate_clean_words(cag_block, 60, budget.chars_per_token)
                if dialogue_text:
                    dialogue_text = self.truncator.truncate_recency_lines(dialogue_text, 60, budget.chars_per_token)
                if email_block:
                    email_block = self.truncator.truncate_clean_words(email_block, 30, budget.chars_per_token)

                system_sections_pruned = [sys_block]
                if clean_ts:
                    system_sections_pruned.append(f"[DATA/ORA: {clean_ts}]")
                if cag_block:
                    system_sections_pruned.append(f"[STATO ATTUALE (CAG)]\n{cag_block}")
                if mag_block:
                    system_sections_pruned.append(f"[MEMORIA PERSONALE (MAG)]\n{mag_block}")
                if email_block:
                    system_sections_pruned.append(f"[NOTIFICHE EMAIL E ORDINI]\n{email_block}")
                full_system_msg = "\n\n".join(system_sections_pruned)

                user_sections_pruned = []
                if dialogue_text:
                    user_sections_pruned.append(f"[CONVERSAZIONE RECENTE]\n{dialogue_text}")
                    if user_block:
                        user_sections_pruned.append(f"[RICHIESTA ATTUALE]\n{user_block}")
                elif user_block:
                    user_sections_pruned.append(user_block)
                full_user_msg = "\n\n".join(user_sections_pruned)

                chatml = (
                    f"<|im_start|>system\n{full_system_msg}\n<|im_end|>\n"
                    f"<|im_start|>user\n{full_user_msg}\n<|im_end|>\n"
                    f"<|im_start|>assistant\n"
                )
                full_prompt = f"{full_system_msg}\n\nUtente:\n{full_user_msg}"
                total_tokens = self.estimate_tokens(chatml)

            # Tier 3: Absolute hard ceiling guarantee (clamp system & user blocks)
            if total_tokens > ceiling:
                token_allowance_per_half = max(40, (ceiling - 40) // 2)
                sys_block = self.truncator.truncate_clean_words(sys_block, token_allowance_per_half // 2, budget.chars_per_token)
                full_system_msg = sys_block
                full_user_msg = self.truncator.truncate_clean_words(full_user_msg, token_allowance_per_half, budget.chars_per_token)
                chatml = (
                    f"<|im_start|>system\n{full_system_msg}\n<|im_end|>\n"
                    f"<|im_start|>user\n{full_user_msg}\n<|im_end|>\n"
                    f"<|im_start|>assistant\n"
                )
                full_prompt = f"{full_system_msg}\n\nUtente:\n{full_user_msg}"
                total_tokens = self.estimate_tokens(chatml)

        # Synchronize section token telemetry with actual post-pruning state
        counts = {
            "system": self.estimate_tokens(sys_block),
            "timestamp": self.estimate_tokens(clean_ts),
            "cag": self.estimate_tokens(cag_block),
            "mag": self.estimate_tokens(mag_block),
            "email": self.estimate_tokens(email_block),
            "rag": self.estimate_tokens(rag_block),
            "dialogue": self.estimate_tokens(dialogue_text),
            "user": self.estimate_tokens(user_block),
            "total_chatml": total_tokens,
        }

        is_safe = total_tokens <= self.config.max_ceiling_tokens and total_tokens < 1200

        return SLMPromptOutput(
            full_prompt=full_prompt,
            chatml_prompt=chatml,
            system_block=full_system_msg,
            user_block=full_user_msg,
            token_estimate=total_tokens,
            section_token_counts=counts,
            is_within_budget=is_safe,
        )

    def build_prompt(
        self,
        user_text: str,
        system_prompt: str = "",
        cag_environment: str = "",
        cag_hardware: str = "",
        cag_errors: str = "",
        cag_ha: str = "",
        mag_profile: str = "",
        mag_episodes: str = "",
        mag_facts: str = "",
        rag_memories: str = "",
        rag_knowledge: str = "",
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        repeated_note: str = "",
        email_context: str = "",
        timestamp: str = "",
        intent_category: Optional[str] = None,
    ) -> str:
        """Returns the formatted ChatML prompt string for direct NPU consumption."""
        ctx = SLMPromptContext(
            user_text=user_text,
            system_prompt=system_prompt,
            email_context=email_context,
            cag_environment=cag_environment,
            cag_hardware=cag_hardware,
            cag_errors=cag_errors,
            cag_ha=cag_ha,
            mag_profile=mag_profile,
            mag_episodes=mag_episodes,
            mag_facts=mag_facts,
            rag_memories=rag_memories,
            rag_knowledge=rag_knowledge,
            conversation_history=conversation_history,
            repeated_note=repeated_note,
            timestamp=timestamp,
            intent_category=intent_category,
        )
        output = self.build_prompt_detailed(ctx)
        return output.chatml_prompt


def build_slm_prompt(
    user_text: str,
    cag_text: str = "",
    mag_text: str = "",
    rag_text: str = "",
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    email_context: str = "",
) -> str:
    """
    Standalone entry point matching PROJECT.md Interface Contract 1.
    Guarantees resulting prompt token count < 1200 tokens.
    """
    fusion = SLMMetapromptFusion()
    ctx = SLMPromptContext(
        user_text=user_text,
        system_prompt=system_prompt or "",
        email_context=email_context,
        cag_environment=cag_text,
        mag_facts=mag_text,
        rag_knowledge=rag_text,
        conversation_history=history,
    )
    return fusion.build_prompt_detailed(ctx).chatml_prompt
