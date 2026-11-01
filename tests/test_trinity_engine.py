import sys
import os
import asyncio
import tempfile
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.trinity.trinity_engine import TrinityEngine


def test_trinity_engine_initialization_and_fast_prompt_augmentation():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".db") as f:
        tmp_db = f.name

    try:
        engine = TrinityEngine(db_path=tmp_db)
        assert engine.enabled is True

        async def _run():
            # Build augmented prompt for general conversational question
            prompt = await engine.build_augmented_prompt(
                user_text="Ciao Marcus, chi sei?",
                source="text",
                user_identity="Luca",
                target_model="qwen_local"
            )
            assert "Marcus" in prompt or "MARCUS" in prompt
            assert "chi sei?" in prompt
            assert len(prompt) > 50

            # Record interaction
            await engine.record_interaction(
                user_text="Ciao Marcus, chi sei?",
                robot_response="Sono Marcus, assistente robotico.",
                user_identity="Luca"
            )

            # Verify episode was stored in WAL database
            episodes = engine.mag_db.get_recent_episodes(limit=5)
            assert len(episodes) >= 1
            assert episodes[0]["user_input"] == "Ciao Marcus, chi sei?"

        asyncio.run(_run())
    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        if os.path.exists(tmp_db + "-wal"):
            os.remove(tmp_db + "-wal")
        if os.path.exists(tmp_db + "-shm"):
            os.remove(tmp_db + "-shm")
