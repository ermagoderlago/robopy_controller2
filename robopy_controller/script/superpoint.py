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
# Export ONNX (LEGACY)
# =========================
def export_onnx(repo: Path, ckpt: Path, h: int, w: int, out: Path):
    sys.path.insert(0, str(repo))
    # Importa la classe originale dal repo scaricato
    try:
        from superpoint.superpoint import SuperPointNet
    except ImportError:
        # Fallback nel caso la struttura delle cartelle sia diversa
        sys.path.append(str(repo / "superpoint"))
        from superpoint.superpoint import SuperPointNet

    print("[INFO] Loading checkpoint", ckpt)
    model = SuperPointNet()
    model.load_state_dict(torch.load(ckpt, map_location="cpu"))
    model.eval()

    # Avvolgiamo il modello per gestire input/output puliti
    model = SuperPointWrapper(model)

    # Input dummy 1 canale (Grayscale)
    dummy = torch.zeros(1, 1, h, w)

    print("[INFO] Export ONNX (legacy)...")
    torch.onnx.export(
        model,
        dummy,
        out,
        input_names=["image"],
        output_names=["semi", "desc"],
        opset_version=11,
        do_constant_folding=True,
        dynamo=False,   # Necessario per versioni recenti di PyTorch
    )

    print("[OK] ONNX:", out)


# =========================
# Build Myriad BLOB
# =========================
def build_blob_from_onnx(onnx: Path, shaves: int, ov_version: str):
    import blobconverter

    print("[INFO] Building MyriadX blob...")

    # --- CORREZIONE CRITICA ---
    # Definiamo i parametri per il Model Optimizer.
    # SuperPoint si aspetta float [0,1]. La camera dà uint8 [0,255].
    # --scale_values=[255] divide l'input per 255.
    # --mean_values=[0] specifica che c'è 1 solo canale e non sottraiamo nulla.
    # Questo sovrascrive il default RGB [127.5, 127.5, 127.5] che causava il crash.
    optimizer_params = [
        "--scale_values=[255]",
        "--mean_values=[0]"
    ]

    blob = blobconverter.from_onnx(
        model=str(onnx),
        data_type="FP16",
        shaves=shaves,
        version=ov_version,
        output_dir=str(onnx.parent),
        optimizer_params=optimizer_params  # Passiamo i parametri corretti
    )

    print("[OK] BLOB:", blob)


# =========================
# Main
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--height", type=int, default=200)
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--shaves", type=int, default=6)
    ap.add_argument("--openvino-version", default="2021.4")
    ap.add_argument("--workdir", default="./superpoint_fp16_320x200")
    args = ap.parse_args()

    workdir = Path(args.workdir).absolute()
    workdir.mkdir(parents=True, exist_ok=True)

    repo = download_repo(workdir)

    ckpt = repo / "superpoint_v1.pth"
    if not ckpt.exists():
        raise FileNotFoundError(f"Checkpoint non trovato: {ckpt}")

    onnx = workdir / "superpoint.onnx"

    # 1. Esporta in ONNX
    export_onnx(repo, ckpt, args.height, args.width, onnx)
    
    # 2. Converti in BLOB (con fix canali)
    build_blob_from_onnx(onnx, args.shaves, args.openvino_version)


if __name__ == "__main__":
    main()