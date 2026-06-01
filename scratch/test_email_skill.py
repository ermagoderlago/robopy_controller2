#!/usr/bin/env python3
import os
import sys
import asyncio
import logging
import json
import datetime
from typing import Dict, List, Any

# Setup python path to include robot_ai
sys.path.append('/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages')
sys.path.append('/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller')

# Set logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("TestEmailAgent")

# Load .env file
env_path = "/mnt/ssd/robopy_controller_host/.env"
if os.path.exists(env_path):
    logger.info(f"Loading env from {env_path}")
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                os.environ[k] = v
else:
    logger.error(".env file not found!")

# Import GenAI SDK and EmailSkill modules
try:
    from google import genai
    from google.genai import types
    logger.info("google-genai SDK imported successfully.")
except ImportError:
    logger.error("google-genai SDK not found! Make sure it is installed in the robot's python environment.")

from robot_ai.skills.builtin.email_skill import EmailSkill
from robot_ai.skills.builtin.email_memory import EmailMemory

# Define Mock LLMService to simulate dispatcher and synthesis responses
class MockLLMService:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("No GEMINI_API_KEY found, LLM classification will be mocked or skipped.")

    async def generate(self, prompt: str, max_tokens=2048, functions=None) -> Any:
        class ResponseWrapper:
            def __init__(self, text):
                self.text = text
                self.tokens_used = 150
                self.latency_ms = 450
        
        # If no client, return mocked data based on prompt contents
        if not self.client:
            if "dispatcher" in prompt.lower() or "dispatcher" in prompt:
                return ResponseWrapper(json.dumps({
                    "intent": "search",
                    "imap_search_criteria": 'FROM "AVIS"',
                    "post_filter_keyword": "AVIS",
                    "speak_before": "Certo Luca, vado subito a cercare le email da AVIS.",
                    "reason": "Test case dispatcher simulation"
                }))
            else:
                return ResponseWrapper(json.dumps({
                    "summary": "Simulazione riepilogo email AVIS.",
                    "reply_draft": None,
                    "ha_actions": [],
                    "priority": "normal",
                    "tracking": None,
                    "classifications": [{"from": "avis", "class": "important"}],
                    "packages": [],
                    "appointments": [],
                    "learnings": [{"type": "carrier", "value": "cainiao"}]
                }))
        
        # Run real Gemini call in thread executor
        def _call():
            resp = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.1
                )
            )
            return resp.text
        
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, _call)
        return ResponseWrapper(text)

async def main():
    logger.info("=== STARTING EMAIL AGENT ENGINE V4.0 VERIFICATION ===")
    
    # 1. Verify Dynamic Memory Learning
    logger.info("\n=== STEP 1: Verifying Dynamic AI Learning in EmailMemory ===")
    memory = EmailMemory()
    logger.info(f"Persistent memory file: {memory.persist_path}")
    
    # Verify dynamic learning insertions
    memory.learn_carrier("cainiao")
    memory.learn_vip("luisella.bonfanti@gmail.com")
    
    carriers = memory.get_learned_carriers()
    vips = memory.get_learned_vips()
    logger.info(f"Learned carriers in memory: {carriers}")
    logger.info(f"Learned VIP senders in memory: {vips}")
    
    if "cainiao" in carriers and "luisella.bonfanti@gmail.com" in vips:
        logger.info("Step 1: SUCCESS! Dynamic carrier and VIP learning confirmed.")
    else:
        logger.error("Step 1: FAILED! Dynamic learning check failed.")

    # 2. Verify Cognitive Agentic Dispatcher (Pre-Execution Call)
    logger.info("\n=== STEP 2: Verifying Cognitive Dispatcher ===")
    llm = MockLLMService()
    skill = EmailSkill(llm_service=llm, config={"min_interval_s": 0})
    
    user_query = "perfetto, dimmi cosa dice la AVIS"
    logger.info(f"Simulating user speech request: '{user_query}'")
    
    # Run email fetching and processing logic
    try:
        generator = skill.execute(text=user_query, context={"intent": "read"})
        async for step in generator:
            logger.info(f"Email Agent Yield Step: {step}")
            if step.speak and "AVIS" in step.speak:
                logger.info(f"-> Speech Feedback verified: '{step.speak}'")
    except Exception as e:
        logger.error(f"Failed during email agent task execution: {e}")

    # 3. Verify Dynamic Learning feedback loop from Synthesis Response
    logger.info("\n=== STEP 3: Verifying Dynamic Carrier Learning from LLM response ===")
    mock_synthesis_json = {
        "summary": "Hai ricevuto un aggiornamento da Cainiao per la tua spedizione AliExpress.",
        "reply_draft": None,
        "ha_actions": [],
        "priority": "normal",
        "tracking": "LP005678912345",
        "classifications": [{"from": "aliexpress", "class": "interesting"}],
        "packages": [{"tracking_number": "LP005678912345", "carrier": "aliexpress", "order_info": "Simulazione pacco Cainiao"}],
        "appointments": [],
        "learnings": [{"type": "carrier", "value": "cainiao-global"}]
    }
    
    # Process this mock synthesis payload like EmailSkill does at the end of execute()
    for l in mock_synthesis_json.get("learnings", []):
        l_type = l.get("type", "").strip().lower()
        l_val = l.get("value", "").strip()
        if l_type == "carrier" and l_val:
            memory.learn_carrier(l_val)
        elif l_type == "vip" and l_val:
            memory.learn_vip(l_val)
            
    # Verify the new carrier is learned
    updated_carriers = memory.get_learned_carriers()
    logger.info(f"Updated dynamic carriers list: {updated_carriers}")
    if "cainiao-global" in updated_carriers:
        logger.info("Step 3: SUCCESS! Cainiao-global dynamic learning confirmed.")
    else:
        logger.error("Step 3: FAILED! cainiao-global not found in dynamic carrier list.")

    logger.info("\n=== ALL TEST STEPS DEFINED. DIAGNOSTICS COMPLETED ===")

if __name__ == "__main__":
    asyncio.run(main())
