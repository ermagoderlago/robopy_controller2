#!/usr/bin/env python3
"""Test rapido Live API con v1alpha."""
import os, asyncio
from google import genai
from google.genai import types

def load_env():
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))

load_env()
API_KEY = os.environ.get("GEMINI_API_KEY")

LIVE_MODELS_V1ALPHA = [
    "gemini-2.0-flash-exp",
    "gemini-2.0-flash-live-001",
    "gemini-2.5-flash-exp-native-audio-thinking-dialog",
]

async def test_live(client, model_name):
    try:
        config = types.LiveConnectConfig(response_modalities=["TEXT"])
        async with client.aio.live.connect(model=model_name, config=config) as session:
            await session.send(input="Rispondi solo: ok", end_of_turn=True)
            async for msg in session:
                sc = getattr(msg, 'server_content', None)
                if sc and getattr(sc, 'turn_complete', False):
                    txt = ""
                    if sc.model_turn:
                        for p in sc.model_turn.parts:
                            if hasattr(p, 'text') and p.text:
                                txt += p.text
                    return f"✅ OK (risposta: '{txt.strip()[:30]}')"
        return "✅ connesso (no text turn)"
    except Exception as e:
        return f"❌ {str(e)[:100]}"

async def main():
    # Test con v1alpha
    client_alpha = genai.Client(api_key=API_KEY, http_options={'api_version': 'v1alpha'})
    print("\n🎤 TEST LIVE API con v1alpha:")
    for m in LIVE_MODELS_V1ALPHA:
        result = await test_live(client_alpha, m)
        print(f"  {result} — {m}")

asyncio.run(main())
