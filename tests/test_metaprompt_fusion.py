import sys
import os
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.trinity.metaprompt_fusion import MetapromptFusion
from robopy_controller.robot_ai.trinity.slm_metaprompt_fusion import SLMMetapromptFusion, SLMBudgetConfig


def test_metaprompt_fusion_token_budget_red_zone():
    """SPEC-05 Red Zone: Metaprompt must strictly stay under 2500 tokens (nominal 2200)."""
    fusion = MetapromptFusion()

    # Create heavy synthetic context to stress-test token compression
    heavy_hardware = "CPU: 75% | RAM: 3.2GB | Bat: 12.1V (72%) | Core Temp: 58C\n" * 20
    heavy_episodes = "Episodio: Ieri abbiamo mappato il corridoio e trovato la base di ricarica.\n" * 30
    heavy_facts = "Fatto appreso: A Luca piace la pizza e la robotica autonoma con ROS 2.\n" * 30
    heavy_rag = "SPEC-04 Documentazione: La pipeline audio 16kHz PCM deve rispettare attenuazione 3.0x.\n" * 30

    prompt = fusion.build_prompt(
        user_text="Marcus, spiegami la mappatura 2.5D e controlla lo stato della batteria.",
        system_prompt="Sei Marcus, robot autonomo.",
        cag_hardware=heavy_hardware,
        mag_episodes=heavy_episodes,
        mag_facts=heavy_facts,
        rag_knowledge=heavy_rag
    )

    # Verify structural sections
    assert "[RUOLO DEL ROBOT]" in prompt
    assert "[MEMORIA STORICA (MAG)]" in prompt
    assert "[CONTESTO ATTUALE (CAG)]" in prompt

    # Verify token count ceiling (< 2500 tokens, approx 4 chars/token in Italian)
    estimated_tokens = len(prompt) / 3.5
    assert estimated_tokens < 2500, f"Token ceiling violated: estimated {estimated_tokens:.0f} tokens >= 2500"


def test_slm_metaprompt_fusion_under_1200_tokens():
    """SLM Metaprompt Fusion for Hailo Qwen must strictly stay under 1200 tokens (ceiling 1100)."""
    slm_fusion = SLMMetapromptFusion()

    prompt = slm_fusion.build_prompt(
        user_text="Dove ci troviamo e quanta batteria abbiamo?",
        cag_environment="Room: Salotto | Map: pianoterra2",
        cag_hardware="Bat: 12.4V (85%) | Temp: 45C",
        mag_profile="User: Luca | Role: Creator",
        mag_episodes="Episodio: Discussa la configurazione CycloneDDS.",
        intent_category="CONVERSATION"
    )

    estimated_tokens = len(prompt) / 3.6
    assert estimated_tokens < 1200, f"SLM token budget violated: {estimated_tokens:.0f} tokens >= 1200"
    assert "<|im_start|>system" in prompt
    assert "<|im_start|>user" in prompt
