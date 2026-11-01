import urllib.request
import json
import time

payload = {
    "model": "qwen2.5-instruct:1.5b",
    "messages": [
        {"role": "system", "content": "Sei MARCUS, assistente robotico. Conoscenza: La capitale della Francia è Parigi. Rispondi brevemente ed esattamente in italiano."},
        {"role": "user", "content": "qual è la capitale della Francia?"}
    ],
    "stream": False
}

t0 = time.time()
req = urllib.request.Request(
    "http://127.0.0.1:11434/api/chat",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req, timeout=20) as resp:
    data = json.loads(resp.read().decode())
    elapsed = (time.time() - t0) * 1000.0
    print(f"Elapsed: {elapsed:.1f}ms")
    print("Content:", data["message"]["content"])
