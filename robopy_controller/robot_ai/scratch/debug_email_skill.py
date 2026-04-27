
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from robot_ai.skills.builtin.email_skill import EmailSkill
from robot_ai.skills.base_skill import SkillResult

async def debug_email_skill():
    print("--- Debugging EmailSkill ---")
    
    # Mock LLM Service
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = MagicMock(
        text=json.dumps({
            "summary": "Hai 2 email: una da Mario, una da Luca.",
            "reply_draft": None,
            "ha_actions": [],
            "priority": "normal",
        })
    )
    
    # Set dummy env vars
    os.environ["EMAIL_ADDRESS"] = "test@example.com"
    os.environ["EMAIL_PASSWORD"] = "password"
    
    skill = EmailSkill(llm_service=mock_llm)
    
    # Mock aioimaplib
    with patch("aioimaplib.IMAP4_SSL") as MockIMAP:
        mock_imap = MockIMAP.return_value
        mock_imap.wait_hello_from_server = AsyncMock(return_value=True)
        mock_imap.login = AsyncMock(return_value=("OK", [b"Logged in"]))
        mock_imap.select = AsyncMock(return_value=("OK", [b"7"]))
        
        # Test case 1: UNSEEN returns empty list but user has emails
        # What does search return? 
        # aioimaplib.IMAP4.search returns (status, lines)
        mock_imap.search = AsyncMock(return_value=("OK", [b""]))
        
        print("\nTesting 'leggi le email' when UNSEEN is empty...")
        results = []
        async for res in skill.execute("leggi le email"):
            results.append(res)
            print(f"Yielded: {res.message} (Speak: {res.speak})")
        
        if any("Non hai nuove email" in r.speak for r in results):
            print("Reproduced: Skill says no new emails when search returns empty.")
            
        # Test case 2: Check parsing if search returns IDs
        mock_imap.search = AsyncMock(return_value=("OK", [b"1 2 3"]))
        mock_imap.fetch = AsyncMock(return_value=("OK", [
            b'1 (RFC822 {100})', 
            b'From: mario@example.com\r\nSubject: Test\r\n\r\nHello', 
            b')'
        ]))
        
        print("\nTesting 'leggi le email' when UNSEEN returns '1 2 3'...")
        results = []
        async for res in skill.execute("leggi le email"):
            results.append(res)
        
        last_res = results[-1]
        print(f"Final result: {last_res.success}, Message: {last_res.message}")
        if last_res.success and "Hai 2 email" in last_res.message:
            print("Parsing OK when IDs are found.")
        else:
            print(f"Parsing FAILED or unexpected result: {last_res.message}")

if __name__ == "__main__":
    asyncio.run(debug_email_skill())
