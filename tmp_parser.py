import ast

path = r"c:\Users\lsuffia\OneDrive - BRUGOLA OEB INDUSTRIALE SPA\Documents\robopy\antigravity\launch\fast_flow_launch.py"
with open(path, 'r', encoding='utf-8') as f:
    tree = ast.parse(f.read())

for node in ast.walk(tree):
    if isinstance(node, ast.Tuple):
        print(f"FOUND TUPLE AT LINE {node.lineno}: {ast.unparse(node)}")
