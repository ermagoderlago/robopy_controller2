import asyncio
from robopy_controller.robot_ai.hailo.hailo_qwen_client import HailoQwenClient, HailoQwenConfig

async def main():
    client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True))
    
    # Test 1: Standard multi-turn ChatML prompt with multiple user/assistant blocks
    multi_turn_prompt = (
        "<|im_start|>system\nSei Marcus\n<|im_end|>\n"
        "<|im_start|>user\nCiao Marcus, chi sei?\n<|im_end|>\n"
        "<|im_start|>assistant\nSono Marcus...\n<|im_end|>\n"
        "<|im_start|>user\nDove siamo?\n<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    res_multi, _ = await client.generate(multi_turn_prompt)
    print("RESULT FOR multi-turn ChatML 'Dove siamo?':", repr(res_multi))
    assert "troviamo" in res_multi or "stanza" in res_multi or "salotto" in res_multi, f"Multi-turn failed! Got: {res_multi}"
    print("MULTI-TURN CHATML TEST PASSED!")

if __name__ == "__main__":
    asyncio.run(main())

