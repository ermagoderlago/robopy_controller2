#!/usr/bin/env python3
"""
Diagnostic & Auto-Discovery Script for Gemini API Models
==========================================================
Verifica la disponibilità dei modelli Gemini per la Multimodal Live API (WebSocket bidi-streaming con Native Audio).
Utilizzo:
  python3 scripts/test_gemini_models.py [--update-env]
"""

import sys
import os
import asyncio
import argparse
from typing import List, Tuple

# Fix Windows console UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Caricamento variabili d'ambiente
def load_env_file():
    possible_paths = [
        "/mnt/ssd/robopy_controller_host/.env",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
        ".env"
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ[k.strip()] = v.strip().strip("'").strip('"')
                print(f"📄 Caricate variabili d'ambiente da: {path}")
                return path
            except Exception as e:
                print(f"⚠️ Errore caricamento {path}: {e}")
    return None

async def test_live_connection(client, model_name: str) -> Tuple[bool, str]:
    """Testa la connessione WebSocket Live con audio nativo per un modello specifico."""
    from google.genai import types
    try:
        ws_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            )
        )
        # Tenta la connessione con timeout di 6 secondi
        async with asyncio.timeout(6.0):
            async with client.aio.live.connect(model=model_name, config=ws_config) as session:
                return True, "OK (Connesso con risposta AUDIO nativa)"
    except asyncio.TimeoutError:
        return False, "TIMEOUT (Nessuna risposta entro 6 secondi)"
    except Exception as e:
        err_msg = str(e)
        if "404" in err_msg or "not found" in err_msg.lower():
            return False, "NOT FOUND / DEPRECATED (404)"
        elif "1008" in err_msg or "policy" in err_msg.lower():
            return False, "UNSUPPORTED MODALITY / POLICY (1008)"
        else:
            return False, f"ERRORE: {err_msg[:80]}"

def fetch_web_models() -> List[str]:
    """Cerca dal sito web ai.google.dev eventuali nuove stringhe di modelli annunciate."""
    import urllib.request
    import re
    web_models = []
    urls = [
        "https://ai.google.dev/gemini-api/docs/models/gemini",
        "https://ai.google.dev/gemini-api/docs/multimodal-live"
    ]
    print("\n---------------------------------------------------------")
    print("🌐 Ricerca nuovi modelli sul sito ufficiale ai.google.dev...")
    print("---------------------------------------------------------")
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                matches = re.findall(r"gemini-[0-9.]+(?:-[a-z0-9]+)+", html, re.IGNORECASE)
                for m in matches:
                    clean_m = m.lower().strip()
                    if clean_m not in web_models and len(clean_m) > 8:
                        web_models.append(clean_m)
                        print(f"  🌐 [Trovato sul Web]: {clean_m}")
        except Exception as e:
            print(f"  ⚠️ Ricerca web ({url}): {e}")
    return web_models

async def main():
    parser = argparse.ArgumentParser(description="Diagnostica Modelli Gemini API per Marcus Robot")
    parser.add_argument("--update-env", action="store_true", help="Aggiorna automaticamente LIVE_MODEL_NAME nel file .env")
    args = parser.parse_args()

    env_path = load_env_file()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ ERRORE: GEMINI_API_KEY non trovata nelle variabili d'ambiente o nel file .env!")
        sys.exit(1)

    print("🔍 Inizializzazione SDK Google GenAI...")
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except ImportError:
        print("❌ ERRORE: Pacchetto 'google-genai' non installato. Installa con 'pip install google-genai'.")
        sys.exit(1)

    # 1. Elenco modelli disponibili via API
    print("\n---------------------------------------------------------")
    print("📋 [1/2] Interrogazione API client.models.list()...")
    print("---------------------------------------------------------")
    available_gemini_models = []
    try:
        models = client.models.list()
        for m in models:
            m_id = getattr(m, "name", str(m))
            if "gemini" in m_id.lower():
                clean_name = m_id.replace("models/", "")
                available_gemini_models.append(clean_name)
                print(f"  • {clean_name}")
    except Exception as e:
        print(f"⚠️ Impossibile recuperare la lista dinamica dei modelli: {e}")

    # 2. Test Live BidiStreaming (Priorità ai modelli -latest ed audio nativo)
    candidate_live_models = [
        "gemini-2.5-flash-native-audio-latest",
        "gemini-2.5-flash-native-audio-preview-12-2025",
        "gemini-3.1-flash-live-preview",
        "gemini-2.5-flash",
        "gemini-2.0-flash-exp"
    ]
    
    # Aggiungi eventuali modelli scoperti dall'API o dal Web (escludendo stringhe deprecate)
    web_models = fetch_web_models()
    for m in available_gemini_models + web_models:
        if m not in candidate_live_models and "deprecated" not in m:
            if "flash" in m or "live" in m or "exp" in m or "audio" in m:
                candidate_live_models.append(m)

    print("\n---------------------------------------------------------")
    print("🎙️ [2/2] Test Connessione Live WebSocket (response_modalities=['AUDIO'])...")
    print("---------------------------------------------------------")

    working_models = []
    results = {}

    for model_name in candidate_live_models:
        print(f"Testing '{model_name}'... ", end="", flush=True)
        success, msg = await test_live_connection(client, model_name)
        results[model_name] = (success, msg)
        if success:
            print(f"✅ {msg}")
            working_models.append(model_name)
        else:
            print(f"❌ {msg}")

    # Algoritmo di Ranking: premia 'latest' e modelli 'flash-native-audio' o 'live'
    def score_model(m: str) -> int:
        score = 0
        if "latest" in m:
            score += 100
        if "native-audio" in m or "live" in m:
            score += 50
        if "flash" in m:
            score += 20
        if "2.5" in m:
            score += 10
        return score

    best_working_model = None
    if working_models:
        working_models.sort(key=score_model, reverse=True)
        best_working_model = working_models[0]

    print("\n=========================================================")
    if best_working_model:
        print(f"🏆 RISULTATO: Il miglior modello resiliente per Gemini Live Native Audio è: '{best_working_model}'")
        
        if args.update_env and env_path and os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                
                updated = False
                new_lines = []
                for line in lines:
                    if line.startswith("LIVE_MODEL_NAME="):
                        new_lines.append(f"LIVE_MODEL_NAME={best_working_model}\n")
                        updated = True
                    else:
                        new_lines.append(line)
                
                if not updated:
                    new_lines.append(f"\nLIVE_MODEL_NAME={best_working_model}\n")
                
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                print(f"✅ Aggiornata variabile LIVE_MODEL_NAME={best_working_model} in {env_path}")
            except Exception as e:
                print(f"⚠️ Impossibile aggiornare .env: {e}")
    else:
        print("❌ NESSUN MODELLO LIVE AUDIO FUNZIONANTE! Verificare la connessione o l'API key.")
    print("=========================================================\n")

if __name__ == "__main__":
    asyncio.run(main())
