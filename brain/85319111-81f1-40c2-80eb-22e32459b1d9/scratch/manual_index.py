import os
import sys
from pathlib import Path

# Aggiungi il path del progetto per importare i moduli
sys.path.append(str(Path.cwd()))

from robopy_controller.robot_ai.services.nightly_dream_service import NightlyDreamService
from robopy_controller.robot_ai.core.config_manager import ConfigManager

# Mock dependencies non necessarie per l'indicizzazione
class Mock:
    def __init__(self, *args, **kwargs): pass

config = ConfigManager()
service = NightlyDreamService(
    config_manager=config,
    memory_store=Mock(),
    llm_service=Mock(),
    embedding_service=Mock()
)

# Forza l'indicizzazione nel percorso OneDrive locale
base_path = str(Path.cwd() / "robopy_controller")
service.log_path = os.path.join(base_path, "logs", "continuous_improvements.md")
service.file_index_path = os.path.join(base_path, "logs", "file_index.json")

print(f"Avvio indicizzazione forzata in: {service.file_index_path}")
service._index_workspace()
print("Indicizzazione completata.")
