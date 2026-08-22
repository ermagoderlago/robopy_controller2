import torch
import types
import sys
import io
import pickle

# Dummy classi
class MobileNetSkipAdd(torch.nn.Module):
    def __init__(self):
        super().__init__()

class Result:
    def __init__(self):
        pass

# Mock dei moduli "models" e "metrics"
models_module = types.ModuleType("models")
metrics_module = types.ModuleType("metrics")
models_module.MobileNetSkipAdd = MobileNetSkipAdd
metrics_module.Result = Result
sys.modules["models"] = models_module
sys.modules["metrics"] = metrics_module

# Percorsi file
old_path = "mobilenet-nnconv5dw-skipadd-pruned.pth.tar"
new_path = "mobilenet_fastdepth_state_dict.pth"

# Custom Unpickler con safe globals
class SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "models" and name == "MobileNetSkipAdd":
            return MobileNetSkipAdd
        if module == "metrics" and name == "Result":
            return Result
        return super().find_class(module, name)

# Leggi e carica con Unpickler sicuro
with open(old_path, "rb") as f:
    buffer = io.BytesIO(f.read())
    checkpoint = SafeUnpickler(buffer).load()

# Estrai lo state_dict
state_dict = checkpoint["state_dict"] if "state_dict" in checkpoint else checkpoint

# Salva solo lo state_dict
torch.save(state_dict, new_path)
print(f"? State dict salvato in {new_path}")

