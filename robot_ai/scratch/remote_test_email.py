
import asyncio
import os
import sys
import logging

# Aggiungi il path del controller per gli import
sys.path.append("/mnt/ssd/robopy_controller_host")

from robot_ai.skills.builtin.email_skill import EmailSkill
from robot_ai.services.llm_service import LLMService

async def run_test():
    logging.basicConfig(level=logging.INFO)
    
    # Mock di un nodo ROS minimo per LLMService
    class MockNode:
        def get_logger(self):
            return logging.getLogger("mock_node")
        def create_client(self, *args, **kwargs):
            return None
        def create_subscription(self, *args, **kwargs):
            return None
        def create_publisher(self, *args, **kwargs):
            return None

    node = MockNode()
    llm_service = LLMService(node=node)
    
    # La skill caricherà le credenziali dalle env vars caricate nello script bash
    skill = EmailSkill(llm_service=llm_service)
    
    print("\n--- AVVIO TEST EMAIL SKILL SU MARCUS ---")
    
    try:
        async for result in skill.execute("leggi le ultime email"):
            print(f"\n[STEP] Success: {result.success}")
            print(f"[STEP] Message: {result.message}")
            if result.speak:
                print(f"[STEP] Speak: {result.speak}")
            if result.data:
                print(f"[STEP] Data: {result.data}")
                
    except Exception as e:
        print(f"\n[ERRORE] Durante l'esecuzione: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_test())
