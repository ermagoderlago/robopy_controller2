import os
import glob
import hashlib
import threading
from typing import List, Dict, Any, Optional
from datetime import datetime

from robot_ai.utils import get_logger

# Optional dependencies for parsing and embeddings
try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import fitz  # pymupdf
except ImportError:
    fitz = None

try:
    from robot_ai.rag.chroma_native_store import get_chroma_client
except ImportError:
    # Fallback to standard chromadb if not found
    import chromadb
    def get_chroma_client():
        return chromadb.PersistentClient(path="./chroma_db", settings=chromadb.config.Settings(anonymized_telemetry=False))

# For float16 embeddings
try:
    import torch
    from sentence_transformers import SentenceTransformer
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


logger = get_logger("trinity.rag_document_indexer")


class DocumentIndexer:
    """
    Indexes documents into the TRINITY knowledge base using thread-safe operations
    and optimized batch embeddings.
    """
    
    def __init__(self, embed_model_name: str = "all-MiniLM-L6-v2"):
        self._lock = threading.RLock()
        self.client = get_chroma_client()
        self.file_hashes: Dict[str, str] = {}
        
        # Initialize embedding model with float16 if possible
        self.embed_model = None
        if HAS_TORCH:
            try:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                self.embed_model = SentenceTransformer(embed_model_name, device=device)
                if device != "cpu":
                    self.embed_model.half() # Float16 quantization
                logger.info(f"Initialized embedding model {embed_model_name} on {device}")
            except Exception as e:
                logger.error(f"Failed to initialize embedding model: {e}")
        else:
            logger.warning("Torch/SentenceTransformer not found. Will rely on default Chroma embedding function.")

    def _get_file_hash(self, file_path: str) -> str:
        """Computes SHA-256 hash of a file for change detection."""
        hasher = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            logger.error(f"Error hashing file {file_path}: {e}")
            return ""

    def _generate_embeddings(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Generates float16 embeddings in batches."""
        if not self.embed_model or not texts:
            return None
        
        try:
            with self._lock:
                embeddings = self.embed_model.encode(texts, batch_size=32, convert_to_tensor=True)
                if embeddings.is_floating_point():
                    embeddings = embeddings.half()
                return embeddings.cpu().tolist()
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}")
            return None

    def _chunk_python_code(self, content: str) -> List[Dict[str, Any]]:
        """Chunks Python code by functions/classes or a sliding window."""
        lines = content.splitlines()
        chunks = []
        current_chunk = []
        start_line = 1
        window_size = 60
        overlap = 15

        for i in range(0, len(lines), window_size - overlap):
            chunk_lines = lines[i:i + window_size]
            if not chunk_lines:
                break
            chunk_text = "\n".join(chunk_lines)
            chunks.append({
                "content": chunk_text,
                "start_line": i + 1,
                "end_line": i + len(chunk_lines)
            })
            
        return chunks

    def _chunk_markdown(self, content: str) -> List[Dict[str, Any]]:
        """Chunks Markdown text by paragraphs or length."""
        # Simple window-based chunking for ~500 chars with overlap
        chunks = []
        window = 500
        overlap = 100
        
        i = 0
        while i < len(content):
            chunk_text = content[i:i+window]
            chunks.append({
                "content": chunk_text,
                "start_line": 0,
                "end_line": 0
            })
            i += (window - overlap)
            
        return chunks

    def _chunk_pdf(self, file_path: str) -> List[Dict[str, Any]]:
        """Extracts and chunks text from PDF if library is available."""
        text = ""
        if fitz:
            try:
                doc = fitz.open(file_path)
                for page in doc:
                    text += page.get_text() + "\n"
            except Exception as e:
                logger.error(f"PyMuPDF error on {file_path}: {e}")
        elif pypdf:
            try:
                with open(file_path, "rb") as f:
                    reader = pypdf.PdfReader(f)
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
            except Exception as e:
                logger.error(f"PyPDF error on {file_path}: {e}")
        else:
            logger.warning(f"Skipping PDF {file_path}, no PDF library installed.")
            return []
            
        if not text.strip():
            return []
            
        return self._chunk_markdown(text)

    def index_file(self, file_path: str, collection_name: str = "marcus_knowledge_base") -> int:
        """
        Indexes a single file into the given ChromaDB collection.
        Returns the number of chunks indexed.
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return 0

        file_hash = self._get_file_hash(file_path)
        
        with self._lock:
            if self.file_hashes.get(file_path) == file_hash:
                logger.debug(f"Skipping unmodified file: {file_path}")
                return 0

        ext = os.path.splitext(file_path)[1].lower()
        chunks = []

        try:
            if ext == '.pdf':
                chunks = self._chunk_pdf(file_path)
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                if ext == '.py':
                    chunks = self._chunk_python_code(content)
                else:
                    chunks = self._chunk_markdown(content)
        except Exception as e:
            logger.error(f"Failed to process {file_path}: {e}")
            return 0

        if not chunks:
            return 0

        # Store in DB
        with self._lock:
            try:
                collection = self.client.get_or_create_collection(name=collection_name)
                
                ids = []
                documents = []
                metadatas = []
                
                for idx, chunk in enumerate(chunks):
                    chunk_id = f"{file_path}_{file_hash}_{idx}"
                    ids.append(chunk_id)
                    documents.append(chunk["content"])
                    metadatas.append({
                        "file_path": file_path,
                        "extension": ext,
                        "start_line": chunk.get("start_line", 0),
                        "end_line": chunk.get("end_line", 0),
                        "hash": file_hash,
                        "indexed_at": datetime.now().isoformat()
                    })
                
                # Use custom embeddings if available
                embeddings = self._generate_embeddings(documents)
                
                if embeddings:
                    collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
                else:
                    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
                    
                self.file_hashes[file_path] = file_hash
                logger.info(f"Indexed {len(chunks)} chunks for {file_path}")
                return len(chunks)
            except Exception as e:
                logger.error(f"Error inserting chunks for {file_path} into Chroma: {e}")
                return 0

    def index_directory(self, dir_path: str, extensions: List[str] = ['.py', '.md', '.yaml', '.json', '.pdf'], collection_name: str = "marcus_knowledge_base") -> int:
        """
        Recursively indexes a directory.
        Returns the total number of chunks indexed.
        """
        total_chunks = 0
        if not os.path.exists(dir_path):
            logger.error(f"Directory not found: {dir_path}")
            return total_chunks

        logger.info(f"Indexing directory {dir_path} for extensions {extensions}")
        
        for root, _, files in os.walk(dir_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in extensions:
                    file_path = os.path.join(root, file)
                    total_chunks += self.index_file(file_path, collection_name)

        logger.info(f"Directory indexing complete. Total chunks: {total_chunks}")
        return total_chunks
