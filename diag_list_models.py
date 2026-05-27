#!/usr/bin/env python3
import os
from google import genai

def list_models():
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("GEMINI_API_KEY not found in environment")
        return

    client = genai.Client(api_key=api_key)
    print("Listing models...")
    try:
        for model in client.models.list():
            print(f"Name: {model.name}, Display Name: {model.display_name}, Supported Actions: {model.supported_actions}")
    except Exception as e:
        print(f"Error listing models: {e}")

if __name__ == "__main__":
    list_models()
