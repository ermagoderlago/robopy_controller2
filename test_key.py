import os
from dotenv import load_dotenv

load_dotenv()
key = os.environ.get('GEMINI_API_KEY', '')
print(f"KEY FOUND: {key[:5]}...{key[-5:]}" if key else "NO KEY")
