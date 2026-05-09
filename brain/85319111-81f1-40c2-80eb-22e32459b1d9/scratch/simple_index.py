import os
import json
from datetime import datetime
from pathlib import Path

def index_workspace():
    ws_root = Path.cwd()
    log_dir = ws_root / "robopy_controller" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_index_path = log_dir / "file_index.json"

    print(f"Indexing workspace files in: {ws_root}")
    file_map = {}
    exclude_dirs = {'.git', '__pycache__', 'build', 'install', 'log', '.venv', 'node_modules', '.gemini', 'brain'}
    
    for root, dirs, files in os.walk(ws_root):
        # Prune unwanted directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if file.endswith(('.py', '.md', '.txt', '.sh', '.yaml', '.yml', '.xml')):
                full_path = os.path.abspath(os.path.join(root, file))
                if file in file_map:
                    if isinstance(file_map[file], list):
                        file_map[file].append(full_path)
                    else:
                        file_map[file] = [file_map[file], full_path]
                else:
                    file_map[file] = full_path

    with open(file_index_path, "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": datetime.now().isoformat(),
            "total_files": len(file_map),
            "files": file_map
        }, f, indent=2)
    print(f"Workspace indexed: {len(file_map)} files found. Saved to {file_index_path}")

if __name__ == "__main__":
    index_workspace()
