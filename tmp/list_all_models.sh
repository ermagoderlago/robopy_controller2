#!/bin/bash
source ~/ros2_venv/bin/activate
cd /mnt/ssd/robopy_controller_host
source setup_keys.sh
python3 << 'PYEOF'
from google import genai
import os

api_key = os.environ.get("GEMINI_API_KEY")

# Lista v1beta
print("=== MODELLI v1beta ===")
c = genai.Client(api_key=api_key)
for m in c.models.list():
    print(f"  {m.name} | {getattr(m, 'display_name', '')}")

# Lista v1alpha
print("\n=== MODELLI v1alpha ===")
c2 = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
try:
    for m in c2.models.list():
        print(f"  {m.name} | {getattr(m, 'display_name', '')}")
except Exception as e:
    print(f"Errore: {e}")
PYEOF
