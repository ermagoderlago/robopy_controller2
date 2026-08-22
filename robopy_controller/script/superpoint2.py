#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path

import torch
import torch.nn as nn

# =========================
# Utils
# =========================
def run(cmd, cwd=None):
    print("[CMD]", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd)


# =========================
# Download repo
# =========================
def download_repo(workdir: Path) -> Path:
    repo = workdir / "SuperPoint_nurenda"
    if repo.exists():
        print("[INFO] Repo già presente")
        return repo

    run([
        "git", "clone", "--depth", "1",
        "https://github.com/nurenda-technologies/SuperPoint.git",
        str(repo)
    ])
    return repo


# =========================
# Wrapper
# =========================
class SuperPointWrapper(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, x):
        semi, desc = self.model(x)
        return semi, desc


# =========================
# Export ONNX
# =========================
def export_onnx(repo: Path, ckpt: Path, h: int, w: int, out: Path):
    sys.path.insert(0, str(repo))

    try:
        from superpoint.superpoint import SuperPointNet
    except ImportError:
        sys.path.append(str(repo / "superpoint"))
        from superpoint.superpoint import SuperPointNet

    print("[INFO] Loading checkpoint:", ckpt)

    # ⚠️ IMPORTANTE: descriptor_dim = 128
    model = SuperPointNet(desc_dim=128)
    model.load_state_dict(torch.load(ckpt, map_location="cpu"))
    model.eval()

    model = SuperPointWrapper(model)

    dummy = torch.zeros(1, 1, h, w)

    print("[INFO] Exporting ONNX...")
    torch.onnx.export(
        model,
        dummy,
        out,
        input_names=["image"],
        output_names=["semi", "desc"],
        opset_version=11,
        do_constant_folding=True,
        dynamo=False,
    )

    print("[OK] ONNX exported:", out)


# =========================
# Build MyriadX BLOB
# =========================
def build_blob_from_onnx(onnx: Path, shaves: int, ov_version: str):
    import blobconverter

    print("[INFO] Building MyriadX blob...")

    optimizer_params = [
        "--scale_values=[255]",
        "--mean_values=[0]",
        "--input_shape=[1,1,200,320]",
        "--layout=NHWC"
    ]

    blob = blobconverter.from_onnx(
        model=str(onnx),
        data_type="FP16",
        shaves=shaves,
        version=ov_version,
        output_dir=str(onnx.parent),
        optimizer_params=optimizer_params
    )

    print("[OK] BLOB generated:", blob)


# =========================
# Main
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--height", type=int, default=200)
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--shaves", type=int, default=4)
    ap.add_argument("--openvino-version", default="2021.2")
    ap.add_argument("--workdir", default="./superpoint_fp16_320x200_desc128")
    args = ap.parse_args()

    workdir = Path(args.workdir).absolute()
    workdir.mkdir(parents=True, exist_ok=True)

    repo = download_repo(workdir)

    ckpt = repo / "superpoint_v1.pth"
    if not ckpt.exists():
        raise FileNotFoundError(f"Checkpoint non trovato: {ckpt}")

    onnx = workdir / "superpoint_desc128.onnx"

    export_onnx(repo, ckpt, args.height, args.width, onnx)
    build_blob_from_onnx(onnx, args.shaves, args.openvino_version)


if __name__ == "__main__":
    main()
