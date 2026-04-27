import sys
print(f"Python: {sys.version}")
try:
    import chromadb
    print("chromadb OK")
except ImportError as e:
    print(f"chromadb ERROR: {e}")

try:
    import llama_index
    print("llama_index OK")
except ImportError as e:
    print(f"llama_index ERROR: {e}")

try:
    from llama_index.core import VectorStoreIndex
    print("llama_index.core OK")
except Exception as e:
    print(f"llama_index.core ERROR: {type(e).__name__}: {e}")

try:
    from llama_index.vector_stores.chroma import ChromaVectorStore
    print("llama_index.vector_stores.chroma OK")
except Exception as e:
    print(f"llama_index.vector_stores.chroma ERROR: {type(e).__name__}: {e}")

try:
    from llama_index.llms.gemini import Gemini
    print("llama_index.llms.gemini OK")
except Exception as e:
    print(f"llama_index.llms.gemini ERROR: {type(e).__name__}: {e}")
