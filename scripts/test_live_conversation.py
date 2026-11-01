#!/usr/bin/env python3
import sys
import os
import asyncio
import traceback

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from robot_ai.orchestration.orchestrator import AIOrchestrator

async def main():
    rclpy.init()
    print(">>> Initializing AIOrchestrator...")
    orch = AIOrchestrator()
    print(">>> Waiting 3s for resources...")
    await asyncio.sleep(3.0)
    
    print(">>> Testing conversation_manager.process_input('Ciao Marcus, chi sei?', source='text')...")
    try:
        res = await orch.conversation_manager.process_input("Ciao Marcus, chi sei?", source="text")
        print(f">>> Result: {res}")
    except Exception as e:
        print(f"❌ Exception in process_input: {e}")
        traceback.print_exc()

    await orch.shutdown()
    rclpy.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
