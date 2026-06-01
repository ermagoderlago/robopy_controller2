import asyncio
import os
import sys
import json
import logging
from unittest.mock import MagicMock

# Create a mock package structure
class MockPackage(MagicMock):
    __path__ = []

sys.modules['rclpy'] = MockPackage()
sys.modules['rclpy.node'] = MockPackage()
sys.modules['rclpy.callback_groups'] = MockPackage()
sys.modules['rclpy.executors'] = MockPackage()
sys.modules['rcl_interfaces'] = MockPackage()
sys.modules['rcl_interfaces.msg'] = MockPackage()
sys.modules['rcl_interfaces.srv'] = MockPackage()
sys.modules['std_msgs'] = MockPackage()
sys.modules['std_msgs.msg'] = MockPackage()
sys.modules['std_srvs'] = MockPackage()
sys.modules['std_srvs.srv'] = MockPackage()
sys.modules['robopy_controller.msg'] = MockPackage()
sys.modules['example_interfaces'] = MockPackage()
sys.modules['example_interfaces.srv'] = MagicMock()

# Mock out unnecessary internal services to bypass all cv2/other imports
sys.modules['robopy_controller.robot_ai.services.visual_memory_service'] = MockPackage()
sys.modules['robopy_controller.robot_ai.services.tts_service'] = MockPackage()
sys.modules['robopy_controller.robot_ai.services.asr_service'] = MockPackage()
sys.modules['aiohttp'] = MockPackage()

# Set up paths to import the packages correctly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from robopy_controller.robot_ai.services.nightly_dream_service import NightlyDreamService
from robopy_controller.robot_ai.rag.memory_store import Memory, MemoryType

# Set up basic logging
logging.basicConfig(level=logging.INFO)

# Mock classes to run isolated tests without ChromaDB or external API keys
class MockConfig:
    def get_config(self):
        class RobotConfig:
            name = "Marcus"
            full_name = "Marcus Autonomous Robot"
            model = "Pi5-Model"
            creator = "Luca"
            version = "15.0"
        class MasterConfig:
            robot = RobotConfig()
        return MasterConfig()

class MockMemoryStore:
    def get_recent(self, limit, memory_type):
        # Return mock conversation history showing a user dissatisfaction with a specific skill
        import time
        return [
            Memory(
                id="1",
                content="<USER_REQUEST>controlla le email, ma rispondi in modo più sintetico, vorrei visualizzare solo le ultime 2 righe</USER_REQUEST>",
                memory_type=MemoryType.CONVERSATION,
                embedding=[],
                created_at=time.time() - 3600
            )
        ]
    def add(self, memory):
        print(f"Memory added: {memory.content[:100]}...")

class MockResponse:
    def __init__(self, text):
        self.text = text

class MockLLMService:
    async def generate(self, prompt, max_tokens=1000):
        print("\n--- LLM RECEIVED PROMPT ---")
        # Check if the prompt is for gap analysis or general analysis
        if "gap_detected" in prompt:
            print("Processing Gap Analysis Request...")
            # Return JSON matching gap spec
            json_response = {
                "gap_detected": True,
                "skill_name": "email_skill.py",
                "reason": "L'utente vorrebbe risposte e-mail più sintetiche limitate alle ultime 2 righe.",
                "refactor_spec": "Modifica il comportamento di sintesi in email_skill.py.",
                "test_case_code": """# Mock verification test case for email skill
import sys
import os

def test_email_synthesis():
    print("Verifica sintesi e-mail...")
    assert True
    print("[TEST PASSED] Email synthesis check passed!")

if __name__ == '__main__':
    test_email_synthesis()
    sys.exit(0)
"""
            }
            return MockResponse(json.dumps(json_response))
        else:
            return MockResponse("## Analisi Notturna\n\nNessun errore riscontrato.")

class MockEmbeddingService:
    async def embed(self, text):
        return [0.1] * 768

async def main():
    print("==============================================")
    print(" [TEST] PROVA DI INTEGRAZIONE SOGNO NOTTURNO ")
    print("==============================================")
    
    config = MockConfig()
    mem_store = MockMemoryStore()
    llm = MockLLMService()
    embed = MockEmbeddingService()
    
    # Initialize the NightlyDreamService
    service = NightlyDreamService(config, mem_store, llm, embed)
    service.set_skills_summary("email_skill.py - Gestisce l'accesso e la lettura delle e-mail di Luca.")

    # Run the skill evolution loop
    result = await service.run_skill_evolution_loop()
    print("\n==============================================")
    print("FLAG RISULTATO DELLA PIPELINE:")
    print(json.dumps(result, indent=2))
    print("==============================================")

if __name__ == "__main__":
    asyncio.run(main())
