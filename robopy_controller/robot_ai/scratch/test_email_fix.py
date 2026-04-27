
import sys
from unittest.mock import MagicMock, AsyncMock

# Mock audioop to allow imports on Windows (Python 3.13+)
sys.modules['audioop'] = MagicMock()
# Mock rclpy if needed
sys.modules['rclpy'] = MagicMock()
sys.modules['rclpy.node'] = MagicMock()

import asyncio
import json
from robot_ai.skills.builtin.email_skill import EmailSkill
from robot_ai.skills.base_skill import SkillResult

async def test_email_logic_fix():
    print("--- Testing EmailSkill Logic Fixes ---")
    
    # 1. Setup Mock LLM
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = MagicMock(text=json.dumps({
        "summary": "Hai 1 email.",
        "reply_draft": None,
        "ha_actions": [],
        "priority": "normal"
    }))
    
    skill = EmailSkill(llm_service=mock_llm)
    skill.email_addr = "test@test.com"
    skill.email_pass = "pass"

    # 2. Mock aioimaplib
    with MagicMock() as mock_imap_class:
        import aioimaplib
        # We need to patch the class in the module where it's used
        with MagicMock() as mock_imap:
            # Setup mock search and fetch
            # Case A: SEARCH returns multiple lines of IDs
            mock_imap.wait_hello_from_server = AsyncMock()
            mock_imap.login = AsyncMock(return_value=("OK", []))
            mock_imap.select = AsyncMock(return_value=("OK", []))
            mock_imap.logout = AsyncMock()
            
            # This simulates a server returning IDs in multiple chunks
            mock_imap.search = AsyncMock(return_value=("OK", [b"1 2", b"3"]))
            
            # This simulates a very short email that was previously skipped (> 100 bytes rule)
            short_email_content = b"From: boss\r\nSubject: Hi\r\n\r\nShort msg"
            mock_imap.fetch = AsyncMock(return_value=("OK", [
                b"3 (RFC822 {36})", 
                short_email_content, 
                b")"
            ]))
            
            # Inject mock imap into skill execution
            with MagicMock() as MockSSL:
                MockSSL.return_value = mock_imap
                import robot_ai.skills.builtin.email_skill as email_module
                email_module.aioimaplib.IMAP4_SSL = MockSSL
                
                print("Executing skill...")
                results = []
                async for res in skill.execute("leggi le email"):
                    results.append(res)
                    print(f"Step: {res.message}")
                
                final = results[-1]
                print(f"Final success: {final.success}")
                print(f"Final data: {final.data}")
                
                # Assertions
                if final.success and final.data.get("emails_count") == 3:
                    print("SUCCESS: Correctly parsed IDs from multiple chunks.")
                else:
                    print(f"FAILURE: Expected 3 emails, got {final.data.get('emails_count')}")
                
                # Check if the short email was actually parsed
                # Since we mocked search to return 1 2 3, and fetch to always return the same
                # The count should be 3 if parsing is working.
                
    print("--- Test Completed ---")

if __name__ == "__main__":
    asyncio.run(test_email_logic_fix())
