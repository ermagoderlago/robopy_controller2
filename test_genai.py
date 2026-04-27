import google.generativeai
print(f"google.generativeai path: {google.generativeai.__file__}")

try:
    from google import genai
    print(f"google.genai imported successfully from {genai.__file__}")
except ImportError as e:
    print(f"Failed to import google.genai: {e}")
