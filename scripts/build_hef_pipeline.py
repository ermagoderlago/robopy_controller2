#!/usr/bin/env python3
"""
Pipeline di Compilazione HEF per NPU Hailo-10H (SPEC-03, FM-VIS-009).
Esegue la quantizzazione INT8 di YOLOv8s con dataset di calibrazione reale COCO
e produce sia il modello standalone (yolov8s_seg.hef) che il pacchetto unificato multi-stream
(joined_yolo_superpoint_netvlad.hef / marcus_unified.hef).
"""

import os
import sys
import time
import cv2
import numpy as np

def prepare_coco_calibration(coco_dir, output_npy, num_samples=256, target_size=(640, 640)):
    print(f"📸 [1/4] Preparazione del dataset di calibrazione da {coco_dir} ({num_samples} campioni)...")
    if not os.path.exists(coco_dir):
        raise FileNotFoundError(f"Directory COCO non trovata: {coco_dir}")
        
    files = [f for f in os.listdir(coco_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    files.sort()
    if len(files) < num_samples:
        print(f"⚠️ Trovate solo {len(files)} immagini, uso tutte quelle disponibili.")
        selected = files
    else:
        selected = files[:num_samples]
        
    images = []
    th, tw = target_size
    for fname in selected:
        fpath = os.path.join(coco_dir, fname)
        img = cv2.imread(fpath)
        if img is None:
            continue
        h, w = img.shape[:2]
        scale = min(th / h, tw / w)
        nh, nw = int(h * scale), int(w * scale)
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        
        # Letterbox 1:1 con padding 114 (standard YOLO)
        letterbox = np.full((th, tw, 3), 114, dtype=np.uint8)
        top = (th - nh) // 2
        left = (tw - nw) // 2
        letterbox[top:top+nh, left:left+nw] = resized
        
        # BGR -> RGB uint8
        rgb = cv2.cvtColor(letterbox, cv2.COLOR_BGR2RGB)
        images.append(rgb)
        
    calib_array = np.array(images, dtype=np.uint8)
    np.save(output_npy, calib_array)
    print(f"✅ Dataset di calibrazione salvato: {output_npy} (Shape: {calib_array.shape}, Dtype: {calib_array.dtype})")
    return calib_array

def compile_yolo_with_coco(har_input, calib_npy, alls_script, output_har, output_hef):
    print(f"\n🧠 [2/4] Ottimizzazione e quantizzazione INT8 di YOLOv8s con DFC ClientRunner...")
    from hailo_sdk_client import ClientRunner
    
    # 1. Caricamento del modello HAR nativo
    print(f"  Caricamento HAR nativo: {har_input}")
    runner = ClientRunner(har=har_input)
    
    # 2. Caricamento dello script di modello (.alls con normalizzazione [0, 255])
    print(f"  Caricamento script di modello: {alls_script}")
    runner.load_model_script(alls_script)
    
    # 3. Ottimizzazione INT8 con calibrazione reale COCO
    print(f"  Avvio runner.optimize() con {calib_npy}...")
    calib_data = np.load(calib_npy)
    start_opt = time.time()
    runner.optimize(calib_data)
    print(f"  ✅ Ottimizzazione completata in {time.time() - start_opt:.1f}s")
    
    # 4. Salvataggio HAR ottimizzato
    runner.save_har(output_har)
    print(f"  ✅ HAR ottimizzato salvato: {output_har}")
    
    # 5. Compilazione HEF
    print(f"\n⚙️ [3/4] Compilazione HEF per Hailo-10H...")
    start_comp = time.time()
    hef_bytes = runner.compile()
    with open(output_hef, "wb") as f:
        f.write(hef_bytes)
    print(f"  ✅ Compilazione HEF completata in {time.time() - start_comp:.1f}s ({len(hef_bytes)} bytes): {output_hef}")

def build_unified_hef(yolo_opt_har, sp_opt_har, nv_opt_har, output_unified_hef):
    print(f"\n🔗 [4/4] Fusione multi-network e compilazione marcus_unified.hef...")
    from hailo_sdk_client import ClientRunner
    
    # Step A: Unione YOLO + SuperPoint
    print(f"  Fondo {yolo_opt_har} + {sp_opt_har}...")
    runner_yolo = ClientRunner(har=yolo_opt_har)
    runner_sp = ClientRunner(har=sp_opt_har)
    runner_joint1 = runner_yolo.join(runner_sp, join_action="none")
    
    # Step B: Unione con NetVLAD
    print(f"  Fondo con {nv_opt_har}...")
    runner_nv = ClientRunner(har=nv_opt_har)
    runner_unified = runner_joint1.join(runner_nv, join_action="none")
    
    # Step C: Compilazione unificata
    print(f"  Compilazione target HEF unificato...")
    start_u = time.time()
    hef_bytes = runner_unified.compile()
    with open(output_unified_hef, "wb") as f:
        f.write(hef_bytes)
    print(f"  ✅ HEF unificato generato in {time.time() - start_u:.1f}s ({len(hef_bytes)} bytes): {output_unified_hef}")

def main():
    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    coco_dir = "/home/robopy/datasets/COCO/train2014"
    calib_npy = "/tmp/calib_set_coco.npy"
    alls_path = os.path.join(workspace, "yolo_spec.alls")
    
    yolo_har = os.path.join(workspace, "yolov8s_seg.har")
    yolo_opt_har = os.path.join(workspace, "yolov8s_seg_coco_optimized.har")
    yolo_hef = os.path.join(workspace, "yolov8s_seg.hef")
    
    sp_opt_har = os.path.join(workspace, "superpoint_128d_optimized.har")
    nv_opt_har = os.path.join(workspace, "netvlad_mobilenet_backbone_optimized.har")
    unified_hef = os.path.join(workspace, "joined_yolo_superpoint_netvlad.hef")
    marcus_hef = os.path.join(workspace, "marcus_unified.hef")
    
    # Scrittura script .alls di normalizzazione
    with open(alls_path, "w") as f:
        f.write("normalization1 = normalization([0.0, 0.0, 0.0], [255.0, 255.0, 255.0])\n")
    print(f"📄 Creato script di modello: {alls_path}")
    
    # Calibrazione COCO
    prepare_coco_calibration(coco_dir, calib_npy, num_samples=256)
    
    # Compilazione YOLOv8s Standalone
    compile_yolo_with_coco(yolo_har, calib_npy, alls_path, yolo_opt_har, yolo_hef)
    
    # Compilazione Unified Multi-Stream
    try:
        build_unified_hef(yolo_opt_har, sp_opt_har, nv_opt_har, unified_hef)
        # Copia di backup con nome marcus_unified.hef
        import shutil
        shutil.copy2(unified_hef, marcus_hef)
        print(f"  ✅ Copiato in {marcus_hef}")
    except Exception as e:
        print(f"⚠️ Fusione unificata via Python API ha generato: {e}")
        print("💡 Procedo con compilazione CLI hailo join se necessario...")

if __name__ == "__main__":
    main()
