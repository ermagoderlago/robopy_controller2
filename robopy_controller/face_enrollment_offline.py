#!/usr/bin/env python3
"""
Face Enrollment Offline — Generazione placeholder embedding
==========================================================
Script STANDALONE (non richiede ROS 2 né Hailo).
Genera file embedding.npy in known_faces/<nome>/embedding.npy
a partire dalle immagini .jpg/.png presenti nelle sottocartelle.

ATTENZIONE: Gli embedding generati sono PLACEHOLDER basati su HOG/features
    OpenCV. NON sono embedding ArcFace reali e NON riconoscono correttamente
    i volti. Servono solo per verificare il flusso del sistema prima
    dell'enrollment reale su Marcus via NPU Hailo.

Enrollment REALE su Marcus (dopo deploy):
    ros2 topic pub --once /hailo/face/enroll std_msgs/msg/String "data: 'nome'"
    (tenere il volto della persona in camera per ~10 frame)

Uso:
    python3 face_enrollment_offline.py [--faces-dir /path/to/known_faces] [--dim 512]

Requisiti:
    pip install numpy opencv-python
"""

import os
import sys
import argparse
import numpy as np
import cv2


def extract_hog_embedding(image_bgr, target_dim=512):
    """
    Estrae un feature vector HOG dall'immagine e lo proietta a target_dim dim.
    Placeholder deterministico: stesso output per stessa immagine.
    """
    img = cv2.resize(image_bgr, (128, 128))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    hog = cv2.HOGDescriptor(
        _winSize=(128, 128),
        _blockSize=(16, 16),
        _blockStride=(8, 8),
        _cellSize=(8, 8),
        _nbins=9
    )
    hog_feat = hog.compute(gray).flatten().astype(np.float32)

    rng = np.random.default_rng(42)
    if hog_feat.shape[0] >= target_dim:
        indices = rng.choice(hog_feat.shape[0], size=target_dim, replace=False)
        indices.sort()
        emb = hog_feat[indices]
    else:
        proj = rng.standard_normal((hog_feat.shape[0], target_dim)).astype(np.float32)
        emb = hog_feat @ proj

    norm = np.linalg.norm(emb)
    if norm > 1e-6:
        emb /= norm
    return emb


def detect_and_crop_face(image_bgr):
    """
    Crop centrale adattivo: assume che il volto sia nel centro-alto dell'immagine.
    Per uno script placeholder non abbiamo bisogno di face detection esatta.
    """
    h, w = image_bgr.shape[:2]
    # Crop del quadrato centrale-alto (dove tipicamente si trova un volto in un selfie/ritratto)
    size = min(w, h)
    cx = w // 2
    cy = int(h * 0.40)  # Leggermente sopra il centro
    half = size // 2
    x1 = max(0, cx - half)
    y1 = max(0, cy - half)
    x2 = min(w, cx + half)
    y2 = min(h, cy + half)
    crop = image_bgr[y1:y2, x1:x2]
    return crop if crop.size > 0 else image_bgr

def process_person(person_dir, person_name, embedding_dim, force):
    """Genera embedding.npy per una persona. Ritorna True se completato."""
    output_path = os.path.join(person_dir, 'embedding.npy')

    if os.path.exists(output_path) and not force:
        print(f"  SKIP '{person_name}': embedding.npy gia' presente (usa --force per sovrascrivere)")
        return True

    image_files = [
        f for f in os.listdir(person_dir)
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp'))
    ]

    if not image_files:
        print(f"  WARN '{person_name}': nessuna immagine trovata in {person_dir}")
        return False

    print(f"  INFO '{person_name}': {len(image_files)} immagini trovate...")

    embeddings = []
    for img_file in image_files:
        img_path = os.path.join(person_dir, img_file)
        img = cv2.imread(img_path)
        if img is None:
            print(f"       FAIL impossibile leggere {img_file}")
            continue
        face_crop = detect_and_crop_face(img)
        emb = extract_hog_embedding(face_crop, target_dim=embedding_dim)
        embeddings.append(emb)
        print(f"       OK {img_file} (norm={np.linalg.norm(emb):.4f})")

    if not embeddings:
        print(f"  FAIL '{person_name}': nessun embedding valido")
        return False

    mean_emb = np.mean(np.stack(embeddings, axis=0), axis=0).astype(np.float32)
    norm = np.linalg.norm(mean_emb)
    if norm > 1e-6:
        mean_emb /= norm

    np.save(output_path, mean_emb)
    print(f"  SAVE {output_path} | shape={mean_emb.shape} | norm={np.linalg.norm(mean_emb):.6f}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Genera embedding placeholder per Face Recognition offline")
    parser.add_argument('--faces-dir', '-d',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'known_faces'),
        help='Percorso della directory known_faces')
    parser.add_argument('--dim', '-n', type=int, default=512,
        help='Dimensione embedding (default: 512)')
    parser.add_argument('--force', '-f', action='store_true',
        help='Sovrascrivi embedding.npy esistenti')
    parser.add_argument('--person', '-p', default=None,
        help='Elabora solo una persona specifica')

    args = parser.parse_args()
    faces_dir = os.path.abspath(args.faces_dir)

    print(f"\n{'='*60}")
    print("Face Enrollment Offline - Placeholder Generator")
    print(f"{'='*60}")
    print(f"Directory: {faces_dir}")
    print(f"Dimensione embedding: {args.dim}")
    print("NOTA: embedding PLACEHOLDER - non sono ArcFace reali!")
    print(f"{'='*60}\n")

    if not os.path.exists(faces_dir):
        print(f"ERRORE: Directory non trovata: {faces_dir}")
        sys.exit(1)

    if args.person:
        persons = [args.person]
    else:
        persons = [d for d in os.listdir(faces_dir) if os.path.isdir(os.path.join(faces_dir, d))]

    if not persons:
        print(f"ERRORE: Nessuna sottocartella trovata in {faces_dir}")
        sys.exit(1)

    print(f"Persone da elaborare: {persons}\n")
    success_count = 0
    for person_name in sorted(persons):
        person_dir = os.path.join(faces_dir, person_name)
        print(f"-> Elaborazione: {person_name}")
        ok = process_person(person_dir, person_name, args.dim, args.force)
        if ok:
            success_count += 1
        print()

    print(f"{'='*60}")
    print(f"Completato: {success_count}/{len(persons)} persone elaborate.")
    print(f"\nPer enrollment REALE su Marcus:")
    print(f"  ros2 topic pub --once /hailo/face/enroll std_msgs/msg/String \"data: '<nome>'\"")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
