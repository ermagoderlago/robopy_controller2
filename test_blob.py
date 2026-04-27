import blobconverter
import os

try:
    print("Tentativo con name='yolov8n_seg_coco', shaves=8...")
    blob_path = blobconverter.from_zoo(
        name="yolov8n_seg_coco",
        shaves=8
    )
    print(f"✅ Blob scaricato in: {blob_path}")
    print(f"Dimensione file: {os.path.getsize(blob_path)} bytes")
except Exception as e:
    print(f"❌ Errore: {e}")
