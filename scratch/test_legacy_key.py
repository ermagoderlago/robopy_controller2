import os
import sys

# Try loading env from parent directory setup_keys.sh or .env
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    # Try reading setup_keys.sh
    try:
        with open('/mnt/ssd/robopy_controller_host/setup_keys.sh', 'r') as f:
            for line in f:
                if line.startswith('export GEMINI_API_KEY='):
                    api_key = line.split('=', 1)[1].strip().strip('"').strip("'")
                    os.environ['GEMINI_API_KEY'] = api_key
                    break
    except Exception as e:
        print(f"Error reading setup_keys.sh: {e}")

if not api_key:
    # Try reading .env
    try:
        with open('/mnt/ssd/robopy_controller_host/.env', 'r') as f:
            for line in f:
                if line.startswith('GEMINI_API_KEY='):
                    api_key = line.split('=', 1)[1].strip().strip('"').strip("'")
                    os.environ['GEMINI_API_KEY'] = api_key
                    break
    except Exception as e:
        print(f"Error reading .env: {e}")

print(f"API KEY: {api_key[:10]}...{api_key[-10:]}" if api_key else "NO KEY FOUND")

try:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    print("Attempting to get models/gemini-pro...")
    model = genai.get_model("models/gemini-pro")
    print(f"Success: {model.name}")
except Exception as e:
    print(f"Failed legacy check: {type(e).__name__}: {e}")
