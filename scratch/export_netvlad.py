#!/usr/bin/env python3
import os
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as models

class NetVLAD(nn.Module):
    def __init__(self, num_clusters=32, dim=128, alpha=100.0, normalize_input=True):
        super(NetVLAD, self).__init__()
        self.num_clusters = num_clusters
        self.dim = dim
        self.alpha = alpha
        self.normalize_input = normalize_input
        self.conv = nn.Conv2d(dim, num_clusters, kernel_size=1, bias=True)
        self.centroids = nn.Parameter(torch.rand(num_clusters, dim))

    def forward(self, x):
        N, C, H, W = x.size()
        if self.normalize_input:
            x = F.normalize(x, p=2, dim=1)
        
        # Soft-assignment
        # conv output: N x K x H x W
        soft_assign = self.conv(x).view(N, self.num_clusters, -1)
        soft_assign = F.softmax(soft_assign, dim=1) # N x K x HW
        
        x_flatten = x.view(N, C, -1) # N x C x HW
        
        # Vectorized NetVLAD core
        # Reshape to N x K x C x HW
        # soft_assign: N x K x HW -> N x K x 1 x HW
        # x_flatten: N x C x HW -> N x 1 x C x HW
        # centroids: K x C -> 1 x K x C x 1
        
        soft_assign_uns = soft_assign.unsqueeze(2) # N x K x 1 x HW
        x_flatten_uns = x_flatten.unsqueeze(1)     # N x 1 x C x HW
        centroids_uns = self.centroids.unsqueeze(0).unsqueeze(-1) # 1 x K x C x 1
        
        residual = x_flatten_uns - centroids_uns # N x K x C x HW
        vlad = torch.sum(residual * soft_assign_uns, dim=-1) # N x K x C
        
        # L2 normalize descriptor
        vlad = vlad.view(N, -1)
        vlad = F.normalize(vlad, p=2, dim=1)
        return vlad

class MobileNetV2NetVLAD(nn.Module):
    def __init__(self, num_clusters=32, dim=128, return_backbone_only=False):
        super(MobileNetV2NetVLAD, self).__init__()
        self.return_backbone_only = return_backbone_only
        
        # Load backbone robustly across torchvision versions
        backbone = None
        for weights_attr in ["MobileNet_V2_Weights", "MobileNetV2_Weights"]:
            if hasattr(models, weights_attr):
                try:
                    weights_class = getattr(models, weights_attr)
                    backbone = models.mobilenet_v2(weights=weights_class.DEFAULT)
                    print(f"Loaded mobilenet_v2 with {weights_attr}.DEFAULT")
                    break
                except Exception as e:
                    print(f"Failed loading with {weights_attr}: {e}")
        
        if backbone is None:
            print("Falling back to pretrained=True")
            backbone = models.mobilenet_v2(pretrained=True)
            
        self.features = backbone.features
        
        # Channel reduction: 1280 -> 128
        self.reducer = nn.Conv2d(1280, dim, kernel_size=1, bias=True)
        
        # NetVLAD layer
        self.netvlad = NetVLAD(num_clusters=num_clusters, dim=dim)

    def forward(self, x):
        x = self.features(x)
        x = self.reducer(x)
        if self.return_backbone_only:
            return x
        x = self.netvlad(x)
        return x

def export_model(model, dummy_input, onnx_path, output_name):
    print(f"=== Exporting to ONNX (dynamo=False): {onnx_path} ===")
    try:
        torch.onnx.export(
            model,
            dummy_input,
            onnx_path,
            input_names=["image"],
            output_names=[output_name],
            opset_version=15,
            do_constant_folding=True,
            export_params=True,
            dynamo=False
        )
        print(f"=== ONNX Export Complete: {onnx_path} ===")
    except Exception as e:
        print(f"Export failed with dynamo=False: {e}")
        raise e

def main():
    # Input size: 3 x 240 x 320 (standard VPR size)
    dummy_input = torch.randn(1, 3, 240, 320)
    
    workspace_dir = "/mnt/c/Users/lsuffia/OneDrive - BRUGOLA OEB INDUSTRIALE SPA/Documents/robopy/antigravity"
    
    print("\n=== Creating Full MobileNetV2-NetVLAD Model ===")
    model_full = MobileNetV2NetVLAD(num_clusters=32, dim=128, return_backbone_only=False)
    model_full.eval()
    onnx_path_full = os.path.join(workspace_dir, "netvlad_mobilenet_full.onnx")
    export_model(model_full, dummy_input, onnx_path_full, "descriptor")
    
    print("\n=== Creating Backbone-Only Model ===")
    model_backbone = MobileNetV2NetVLAD(num_clusters=32, dim=128, return_backbone_only=True)
    model_backbone.eval()
    onnx_path_backbone = os.path.join(workspace_dir, "netvlad_mobilenet_backbone.onnx")
    export_model(model_backbone, dummy_input, onnx_path_backbone, "features")

if __name__ == "__main__":
    main()
