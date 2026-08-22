import torch
from LiteDepth.models import create_model
from LiteDepth.utils import download_model

# Carica il modello preaddestrato
model_name = "AdaBins"
model = create_model(model_name)
model_path = download_model(model_name)
checkpoint = torch.load(model_path, map_location=torch.device('cpu'))
model.load_state_dict(checkpoint['model'])
model.eval()

# Crea input di esempio (batch, channels, height, width)
dummy_input = torch.randn(1, 3, 192, 640)

# Esporta in ONNX
torch.onnx.export(
    model,
    dummy_input,
    "litedepth.onnx",
    export_params=True,
    opset_version=12,
    input_names=['input'],
    output_names=['output'],
    dynamic_axes={
        'input': {0: 'batch_size', 2: 'height', 3: 'width'},
        'output': {0: 'batch_size', 2: 'height', 3: 'width'}
    }
)
print("Modello ONNX esportato con successo!")
