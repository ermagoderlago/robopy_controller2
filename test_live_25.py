import asyncio
import os
from dotenv import load_dotenv

load_dotenv(override=True)
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    try:
        with open('/mnt/ssd/robopy_controller_host/setup_keys.sh', 'r') as f:
            for line in f:
                if line.startswith('export GEMINI_API_KEY='):
                    api_key = line.split('=', 1)[1].strip().strip('"').strip("'")
                    os.environ['GEMINI_API_KEY'] = api_key
                    break
    except Exception:
        pass

from google import genai
from google.genai import types


client = genai.Client(api_key=api_key)

async def test_live():
    # Test 1: TEXT and AUDIO
    try:
        print("Testing TEXT, AUDIO...")
        async with client.aio.live.connect(
            model='gemini-2.5-flash-native-audio-latest',
            config=types.LiveConnectConfig(
                response_modalities=["TEXT", "AUDIO"],
                system_instruction="Sei un robot."
            )
        ) as session:
            print("SUCCESS with TEXT, AUDIO!")
            return
    except Exception as e:
        print(f"Error 1: {e}")

    # Test 2: AUDIO only
    try:
        print("Testing AUDIO only...")
        async with client.aio.live.connect(
            model='gemini-2.5-flash-native-audio-latest',
            config=types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction="Sei un robot."
            )
        ) as session:
            print("SUCCESS with AUDIO only!")
            return
    except Exception as e:
        print(f"Error 2: {e}")

    # Test 3: TEXT only
    try:
        print("Testing TEXT only...")
        async with client.aio.live.connect(
            model='gemini-2.5-flash-native-audio-latest',
            config=types.LiveConnectConfig(
                response_modalities=["TEXT"],
                system_instruction="Sei un robot."
            )
        ) as session:
            print("SUCCESS with TEXT only!")
            return
    except Exception as e:
        print(f"Error 3: {e}")

    # Test 4: no config
    try:
        print("Testing no config...")
        async with client.aio.live.connect(
            model='gemini-2.5-flash-native-audio-latest',
        ) as session:
            print("SUCCESS with no config!")
            return
    except Exception as e:
        print(f"Error 4: {e}")

asyncio.run(test_live())
