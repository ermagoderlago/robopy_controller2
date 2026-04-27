import os
import re
from pathlib import Path
from datetime import datetime

# Configurations
WORKSPACE_ROOT = Path('.')
WORKSPACE_STATE_FILE = 'WORKSPACE_STATE.md'
FILES_TOPIC_FILE = 'files_topic.md'
EXCLUDE_DIRS = {'install', 'build', 'log', '.git', '__pycache__', 'ros2_venv', '.venv', 'venv'}

# Regex patterns for ROS 2 (Python)
PUB_PATTERN = re.compile(r'create_publisher\s*\((?:[^,]+),\s*([\'"])([^\'"]+)\1')
SUB_PATTERN = re.compile(r'create_subscription\s*\((?:[^,]+),\s*([\'"])([^\'"]+)\1')
SRV_PATTERN = re.compile(r'create_service\s*\((?:[^,]+),\s*([\'"])([^\'"]+)\1')
CLI_PATTERN = re.compile(r'create_client\s*\((?:[^,]+),\s*([\'"])([^\'"]+)\1')

def extract_topics(file_path):
    topics = {
        'subs': set(),
        'pubs': set(),
        'srv_servers': set(),
        'srv_clients': set()
    }
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            topics['subs'].update(m[1] for m in SUB_PATTERN.findall(content))
            topics['pubs'].update(m[1] for m in PUB_PATTERN.findall(content))
            topics['srv_servers'].update(m[1] for m in SRV_PATTERN.findall(content))
            topics['srv_clients'].update(m[1] for m in CLI_PATTERN.findall(content))
    except Exception:
        pass
        
    return {k: sorted(list(v)) for k, v in topics.items()}

def scan_workspace():
    files_map = []
    file_topics = {}
    global_topics = {"pubs": set(), "subs": set()}
    
    for root, dirs, files in os.walk(WORKSPACE_ROOT):
        # Filter excluded directories
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('.')]
        
        for file in files:
            filepath = Path(root) / file
            rel_path = str(filepath.relative_to(WORKSPACE_ROOT))
            
            # File list for WORKSPACE_STATE.md
            if file.endswith(('.py', '.xml', '.yaml', '.msg', '.srv')):
                files_map.append(rel_path)
            
            # Extraction for files_topic.md
            if file.endswith('.py'):
                topics = extract_topics(filepath)
                if any(topics.values()):
                    file_topics[rel_path] = topics
                    global_topics["pubs"].update(topics["pubs"])
                    global_topics["subs"].update(topics["subs"])
                        
    return sorted(files_map), file_topics, global_topics

def generate_workspace_state(files_map, global_topics):
    md = f"# Stato del Workspace (Auto-Generato)\n"
    md += f"*Ultimo aggiornamento: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n"
    
    md += "## 📂 Albero dei File Rilevanti\n```text\n"
    for f in files_map:
        md += f"- {f}\n"
    md += "```\n\n"
    
    md += "## 📡 ROS2 Topics Rilevati (Analisi Statica)\n"
    md += "### 📤 Publishers\n"
    for pub in sorted(global_topics["pubs"]):
        md += f"- `{pub}`\n"
        
    md += "\n### 📥 Subscribers\n"
    for sub in sorted(global_topics["subs"]):
        md += f"- `{sub}`\n"
        
    return md

def generate_files_topic(file_topics):
    md = "# Files and Topics of robopy_controller\n\n"
    md += "This file lists the ROS 2 nodes and their topics (auto-generated).\n\n"
    
    for path, topics in sorted(file_topics.items()):
        md += f"## {path}\n"
        if topics['subs']:
            md += "- **Subscribes to:**\n"
            for s in topics['subs']: md += f"  - `{s}`\n"
        else:
            md += "- **Subscribes to:** None\n"
            
        if topics['pubs']:
            md += "- **Transmits (Publishes) to:**\n"
            for p in topics['pubs']: md += f"  - `{p}`\n"
        else:
            md += "- **Transmits (Publishes) to:** None\n"

        if topics['srv_servers']:
            md += "- **Service Servers:**\n"
            for s in topics['srv_servers']: md += f"  - `{s}`\n"
        
        if topics['srv_clients']:
            md += "- **Service Clients:**\n"
            for c in topics['srv_clients']: md += f"  - `{c}`\n"
        md += "\n"
        
    return md

if __name__ == '__main__':
    print("Scansione del workspace in corso...")
    files, file_topics, global_topics = scan_workspace()
    
    print(f"Aggiornamento {WORKSPACE_STATE_FILE}...")
    with open(WORKSPACE_STATE_FILE, 'w', encoding='utf-8') as f:
        f.write(generate_workspace_state(files, global_topics))
        
    print(f"Aggiornamento {FILES_TOPIC_FILE}...")
    with open(FILES_TOPIC_FILE, 'w', encoding='utf-8') as f:
        f.write(generate_files_topic(file_topics))
        
    print(f"Fatto! File aggiornati con successo.")
