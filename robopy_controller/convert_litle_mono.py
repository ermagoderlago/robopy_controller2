# convert_lite_mono_to_onnx.py
import torch
import torch.nn as nn
import torch.onnx
from collections import OrderedDict

# ===== Encoder: EfficientNet-Lite like (ResNet18 based in repo)
from torchvision.models import resnet18

class Encoder(nn.Module):
    def __init__(self):
        super().__init__()
        base = resnet18(pretrained=False)
        self.layer0 = nn.Sequential(base.conv1, base.bn1, base.relu)
        self.layer1 = nn.Sequential(base.maxpool, base.layer1)
        self.layer2 = base.layer2
        self.layer3 = base.layer3
        self.layer4 = base.layer4

    def forward(self, x):
        x0 = self.layer0(x)
        x1 = self.layer1(x0)
        x2 = self.layer2(x1)
        x3 = self.layer3(x2)
        x4 = self.layer4(x3)
        return [x0, x1, x2, x3, x4]

# ===== Decoder: from Lite-Mono repo
class DepthDecoder(nn.Module):
    def __init__(self, num_ch_enc):
        super().__init__()
        self.upconv5 = nn.ConvTranspose2d(num_ch_enc[4], 256, 3, 2, 1, output_padding=1)
        self.upconv4 = nn.ConvTranspose2d(256, 128, 3, 2, 1, output_padding=1)
        self.upconv3 = nn.ConvTranspose2d(128, 64, 3, 2, 1, output_padding=1)
        self.upconv2 = nn.ConvTranspose2d(64, 32, 3, 2, 1, output_padding=1)
        self.upconv1 = nn.ConvTranspose2d(32, 16, 3, 2, 1, output_padding=1)
        self.output_layer = nn.Conv2d(16, 1, 3, padding=1)

    def forward(self, features):
        x = self.upconv5(features[4])
        x = self.upconv4(x)
        x = self.upconv3(x)
        x = self.upconv2(x)
        x = self.upconv1(x)
        return self.output_layer(x)

# ===== Full Model
class LiteMonoModel(nn.Module):
    def __init__(self, encoder, decoder):
        super().__init__()
        self.encoder = encoder
        self.decoder = decoder

    def forward(self, x):
        features = self.encoder(x)
        out = self.decoder(features)
        return out

# ===== Carica i pesi
print("Caricamento pesi...")
encoder = Encoder()
encoder.load_state_dict(torch.load("encoder.pth", map_location="cpu"))
encoder.eval()

decoder = DepthDecoder([64, 64, 128, 256, 512])
decoder.load_state_dict(torch.load("depth.pth", map_location="cpu"))
decoder.eval()

# ===== Modello completo
model = LiteMonoModel(encoder, decoder)
model.eval()

# ===== Dummy input per export
dummy_input = torch.randn(1, 3, 192, 640)

# ===== Esportazione ONNX
print("Esportazione in ONNX...")
torch.onnx.export(
    model,
    dummy_input,
    "LiteMono.onnx",
    input_names=['input'],
    output_names=['depth'],
    export_params=True,
    opset_version=11,
    dynamic_axes={
        'input': {0: 'batch_size'},
        'depth': {0: 'batch_size'}
    }
)

print("✅ Conversione completata: LiteMono.onnx")
