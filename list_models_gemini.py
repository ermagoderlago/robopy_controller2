import os
from dotenv import load_dotenv

load_dotenv(override=True)
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    try:
        with open('/home/robopy/robopy/robopi_controller/robopy_controller_host/setup_keys.sh', 'r') as f:
            for line in f:
                if line.startswith('export GEMINI_API_KEY='):
                    api_key = line.split('=', 1)[1].strip().strip('"').strip("'")
                    os.environ['GEMINI_API_KEY'] = api_key
                    break
    except Exception:
        pass

if not api_key:
    print("NO API KEY")
    exit(1)

try:
    from google import genai
    client = genai.Client(api_key=api_key)
except ImportError:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    for m in genai.list_models():
        name = m.name
        display = getattr(m, 'display_name', '')
        if "2.5" in name or "audio" in name.lower() or "dialog" in name.lower() or "flash" in name.lower() or "3" in name:
            print(f"Name: {name}, Display: {display}")
    exit(0)

# Google GenAI 1.0+ SDK mode
for m in client.models.list():
    name = m.name
    display = getattr(m, 'display_name', '')
    if "2.5" in name or "audio" in name.lower() or "dialog" in name.lower() or "flash" in name.lower() or "3" in name:
        print(f"Name: {name}, Display: {display}")
