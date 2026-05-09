import os
import sys
import asyncio
from dotenv import load_dotenv
sys.path.append('/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages')
from robopy_controller.robot_ai.skills.active.spotify_skill import SpotifySkill

async def test_play():
    load_dotenv('/mnt/ssd/robopy_controller_host/.env')
    skill = SpotifySkill()
    res = await skill.execute(text="", context={"action": "search_play", "query": "Metallica"})
    print(res)

if __name__ == "__main__":
    asyncio.run(test_play())
