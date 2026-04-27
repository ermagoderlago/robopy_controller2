#!/usr/bin/env python3
"""
Diagnostica modelli Gemini disponibili per questa API key.
Testa sia generateContent (Standard) che bidiGenerateContent (Live API).
"""
import os
import asyncio
from google import genai
from google.genai import types

def load_env():
    """Carica .env se presente."""
    env_file = ".env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))

load_env()

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("❌ GEMINI_API_KEY non trovata!")
    exit(1)

# Candidati da testare per Standard API
STANDARD_CANDIDATES = [
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-2.0-flash-lite",
]

# Candidati da testare per Live API (bidiGenerateContent)
LIVE_CANDIDATES = [
    "gemini-2.0-flash-live-001",
    "gemini-2.0-flash-exp",
    "gemini-2.5-flash-exp-native-audio-thinking-dialog",
    "gemini-2.0-flash-live",
    "gemini-live-2.5-flash-preview",
]

client = genai.Client(api_key=API_KEY)

print("=" * 60)
print("📋 LISTA TUTTI I MODELLI DISPONIBILI (v1beta)")
print("=" * 60)
try:
    for m in client.models.list():
        name = getattr(m, 'name', str(m))
        display = getattr(m, 'display_name', '')
        desc = getattr(m, 'description', '')[:60]
        print(f"  {name}  [{display}]")
except Exception as e:
    print(f"  Errore lista: {e}")

print()
print("=" * 60)
print("🧪 TEST STANDARD API (generateContent)")
print("=" * 60)
for model in STANDARD_CANDIDATES:
    try:
        resp = client.models.generate_content(
            model=model,
            contents="Rispondi con una sola parola: ok",
            config=types.GenerateContentConfig(max_output_tokens=5)
        )
        text = resp.text if resp.text else "(no text)"
        print(f"  ✅ {model}: '{text.strip()}'")
    except Exception as e:
        err = str(e)[:80]
        print(f"  ❌ {model}: {err}")

print()
print("=" * 60)
print("🎤 TEST LIVE API (bidiGenerateContent)")
print("=" * 60)

async def test_live(model_name):
    try:
        config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
        )
        async with client.aio.live.connect(model=model_name, config=config) as session:
            await session.send(input="ok", end_of_turn=True)
            async for msg in session:
                if getattr(msg, 'server_content', None):
                    sc = msg.server_content
                    if getattr(sc, 'turn_complete', False):
                        break
        return "✅ OK"
    except Exception as e:
        return f"❌ {str(e)[:80]}"

async def run_live_tests():
    for model in LIVE_CANDIDATES:
        result = await test_live(model)
        print(f"  {result} — {model}")

asyncio.run(run_live_tests())

print()
print("=" * 60)
print("✅ DIAGNOSI COMPLETATA")
print("=" * 60)
