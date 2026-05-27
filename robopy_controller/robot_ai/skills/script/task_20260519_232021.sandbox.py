import sys
try:
    import resource
    # Limiti generosi per consentire l'esecuzione di analisi e caricamento moduli standard
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
except Exception:
    pass
with open('/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/script/task_20260519_232021.py', 'r', encoding='utf-8') as f:
    exec(f.read())
