#!/bin/bash
source ~/ros2_venv/bin/activate
cd /mnt/ssd/robopy_controller_host
source setup_keys.sh
python3 << 'PYEOF'
from google import genai
from google.genai import types
import os, asyncio

api_key = os.environ.get("GEMINI_API_KEY")

# Candidati Live (nomi esatti dalla lista)
LIVE_CANDIDATES = [
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-native-audio-preview-09-2025",
]

# Candidati standard con quota illimitata (evitare flash e flash-lite, quota esaurita)
STD_CANDIDATES = [
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash",  # conferma che funziona
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]

print("=== TEST STANDARD API ===")
c = genai.Client(api_key=api_key)
for model in STD_CANDIDATES:
    try:
        resp = c.models.generate_content(
            model=model,
            contents="Rispondi con una sola parola: ok",
            config=types.GenerateContentConfig(max_output_tokens=5)
        )
        text = (resp.text or "(no text)").strip()[:20]
        print(f"  ✅ {model}: '{text}'")
    except Exception as e:
        print(f"  ❌ {model}: {str(e)[:80]}")

async def test_live(client, model_name):
    try:
        config = types.LiveConnectConfig(response_modalities=["TEXT"])
        async with client.aio.live.connect(model=model_name, config=config) as session:
            await session.send(input="Rispondi solo: ok", end_of_turn=True)
            async for msg in session:
                sc = getattr(msg, 'server_content', None)
                if sc and getattr(sc, 'turn_complete', False):
                    return "✅ OK"
        return "✅ connesso"
    except Exception as e:
        return f"❌ {str(e)[:80]}"

async def main():
    print("\n=== TEST LIVE API (v1beta) ===")
    for m in LIVE_CANDIDATES:
        r = await test_live(c, m)
        print(f"  {r} — {m}")

asyncio.run(main())
PYEOF
