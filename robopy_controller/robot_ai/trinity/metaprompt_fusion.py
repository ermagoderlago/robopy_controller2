"""
Metaprompt fusion for the TRINITY system.
"""

from dataclasses import dataclass
from typing import List, Optional
from robot_ai.utils.logging_utils import get_logger

logger = get_logger(__name__)

@dataclass
class PromptSection:
    name: str
    content: str
    max_tokens: int
    priority: float  # 0.0-1.0, higher = keep more if budget tight

class MetapromptFusion:
    """Assembles the final structured prompt from all TRINITY sources."""
    
    # Token budget per section
    BUDGET_MAG = 600
    BUDGET_CAG = 400  
    BUDGET_RAG = 800
    BUDGET_SYSTEM = 200
    BUDGET_USER = 200
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate number of tokens in text using a simple 1 token = 4 chars approximation."""
        return len(text) // 4

    def _truncate_to_budget(self, text: str, max_tokens: int) -> str:
        """Intelligently truncate text to fit within token budget (keep first N lines)."""
        if not text:
            return ""
        
        current_tokens = self._estimate_tokens(text)
        if current_tokens <= max_tokens:
            return text
            
        lines = text.split('\n')
        kept_lines = []
        current_len = 0
        
        for line in lines:
            line_len = len(line) + 1  # +1 for newline character
            line_tokens = line_len // 4
            if current_len + line_tokens > max_tokens:
                break
            kept_lines.append(line)
            current_len += line_tokens
            
        return '\n'.join(kept_lines)

    def build_prompt(
        self,
        user_text: str,
        system_prompt: str,
        dopaminergic_override: str = "",
        mag_profile: str = "",
        mag_episodes: str = "",
        mag_facts: str = "",
        cag_hardware: str = "",
        cag_ros: str = "",
        cag_environment: str = "",
        cag_errors: str = "",
        cag_ha: str = "",
        rag_memories: str = "",
        rag_knowledge: str = "",
        repeated_note: str = "",
        email_context: str = "",
        timestamp: str = ""
    ) -> str:
        """Assemble the final metaprompt with token budget enforcement."""
        
        # Process System
        sys_block = f"{system_prompt} {dopaminergic_override}".strip()
        sys_block = self._truncate_to_budget(sys_block, self.BUDGET_SYSTEM)
        
        # Process MAG
        mag_parts = []
        if mag_profile:
            mag_parts.append(f"Profilo Utente: {mag_profile}")
        if mag_episodes:
            mag_parts.append(f"Episodi Rilevanti:\n{mag_episodes}")
        if mag_facts:
            mag_parts.append(f"Fatti Appresi:\n{mag_facts}")
        mag_block = "\n".join(mag_parts)
        mag_block = self._truncate_to_budget(mag_block, self.BUDGET_MAG)
        
        # Process CAG
        cag_parts = [p for p in [cag_hardware, cag_ros, cag_environment, cag_errors, cag_ha] if p]
        cag_block = "\n".join(cag_parts)
        cag_block = self._truncate_to_budget(cag_block, self.BUDGET_CAG)
        
        # Process RAG
        rag_parts = []
        if rag_memories:
            rag_parts.append(f"Memorie Conversazionali:\n{rag_memories}")
        if rag_knowledge:
            rag_parts.append(f"Documentazione Tecnica:\n{rag_knowledge}")
        rag_block = "\n".join(rag_parts)
        rag_block = self._truncate_to_budget(rag_block, self.BUDGET_RAG)
        
        # Process User
        user_block = self._truncate_to_budget(user_text, self.BUDGET_USER)
        
        # Assemble Prompt
        prompt_parts = []
        
        prompt_parts.append("[RUOLO DEL ROBOT]")
        prompt_parts.append("Sei MARCUS — Modular Autonomous Robotic Control Unit System.")
        if sys_block:
            prompt_parts.append(sys_block)
            
        if mag_block:
            prompt_parts.append("\n[MEMORIA STORICA (MAG)]")
            prompt_parts.append(mag_block)
            
        if cag_block:
            prompt_parts.append("\n[CONTESTO ATTUALE (CAG)]")
            prompt_parts.append(cag_block)
            
        if rag_block:
            prompt_parts.append("\n[CONOSCENZA RECUPERATA (RAG)]")
            prompt_parts.append(rag_block)
            
        # Timestamp and Context
        if timestamp or repeated_note or email_context:
            if timestamp:
                prompt_parts.append(f"\n[DATA LOCALE: {timestamp}]")
            else:
                prompt_parts.append(f"\n[CONTESTO AGGIUNTIVO]")
                
            if repeated_note:
                prompt_parts.append(repeated_note)
            if email_context:
                prompt_parts.append(email_context)
                
        prompt_parts.append(f"\nUtente: {user_block}")
        
        return "\n".join(prompt_parts)
