"""
Archive Images Pose Estimation & Biometric Face Recognition Verification Suite
==============================================================================
Verifica:
1. Integrita' delle immagini d'archivio in `robopy_controller/known_faces/<persona>/*.jpg`.
2. Normalizzazione L2 rigorosa (`||v||_2 = 1.0 +- 1e-5`, SPEC-03 Zona Rossa) e
   separabilita' inter-classe degli embedding ArcFace 512-dim per tutte le 7 persone.
3. Geometria della trasformazione di similitudine a 3 punti (`compute_similarity_3pt`)
   dai keypoint facciali `yolov8s_pose` (`left_eye`, `right_eye`, `nose`) al template
   canonico 112x112 di `arcface_mobilefacenet`.
4. Validazione anatomica dei 17 keypoint COCO e riconoscimento identita' top-1 (100%)
   sulle 12 foto d'archivio (in esecuzione diretta su NPU Hailo-10H quando disponibile).
"""

import os
import glob
import numpy as np
import cv2
import pytest

from robopy_controller.face_enrollment_offline import (
    ARCFACE_SRC_3PT,
    compute_similarity_3pt,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
KNOWN_FACES_DIR = os.path.join(REPO_ROOT, "robopy_controller", "known_faces")
EXPECTED_PERSONS = ["edoardo", "filippo", "isabella", "jacopo", "luca", "luisella", "rosaria"]


def get_clean_archive_images():
    """Restituisce la lista di tuple (person_name, img_path) per le foto d'archivio."""
    items = []
    for person in EXPECTED_PERSONS:
        pdir = os.path.join(KNOWN_FACES_DIR, person)
        if not os.path.isdir(pdir):
            continue
        for fname in sorted(os.listdir(pdir)):
            if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            if fname.startswith("face_174"):
                continue
            items.append((person, os.path.join(pdir, fname)))
    return items


def test_archive_images_inventory_and_readability():
    """Verifica che tutte le 12 foto d'archivio delle 7 persone siano presenti e leggibili."""
    items = get_clean_archive_images()
    assert len(items) >= 12, f"Attese almeno 12 immagini d'archivio pulite, trovate {len(items)}"

    persons_found = set()
    for person, img_path in items:
        img = cv2.imread(img_path)
        assert img is not None, f"Immagine d'archivio illeggibile: {img_path}"
        h, w = img.shape[:2]
        assert h >= 200 and w >= 200, f"Risoluzione anomala ({w}x{h}) per {img_path}"
        persons_found.add(person)

    assert persons_found == set(EXPECTED_PERSONS), (
        f"Persone mancanti nell'archivio: {set(EXPECTED_PERSONS) - persons_found}"
    )


def test_known_faces_arcface_embeddings_l2_normalized_and_discriminative():
    """
    Verifica SPEC-03 Zona Rossa:
    - Ogni persona in known_faces ha un embedding.npy (512,) float32
    - Norma L2 unitaria ||v||_2 = 1.0 +- 1e-5
    - Separabilita' coseno inter-persona (nessuna coppia di persone diverse supera 0.45)
    """
    embeddings = {}
    for person in EXPECTED_PERSONS:
        emb_path = os.path.join(KNOWN_FACES_DIR, person, "embedding.npy")
        assert os.path.isfile(emb_path), f"embedding.npy mancante per {person}: {emb_path}"
        vec = np.load(emb_path)
        assert vec.shape == (512,), f"Shape errata per {person}: {vec.shape}"
        assert vec.dtype == np.float32, f"Dtype errato per {person}: {vec.dtype}"
        norm = float(np.linalg.norm(vec))
        assert abs(norm - 1.0) <= 1e-5, f"Violazione SPEC-03 L2 norm per {person}: {norm}"
        embeddings[person] = vec

    # Verifica separabilita' inter-persona (nessuna collisione biometrica)
    names = sorted(embeddings.keys())
    max_inter_sim = -1.0
    max_pair = ("", "")
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            sim = float(np.dot(embeddings[names[i]], embeddings[names[j]]))
            if sim > max_inter_sim:
                max_inter_sim = sim
                max_pair = (names[i], names[j])

    assert max_inter_sim < 0.45, (
        f"Embedding biometrici troppo simili tra {max_pair[0]} e {max_pair[1]}: "
        f"cosine={max_inter_sim:.3f} (soglia max 0.45)"
    )


def test_similarity_3pt_alignment_geometry():
    """Verifica che compute_similarity_3pt allinei esattamente occhi e naso su ARCFACE_SRC_3PT."""
    # Simula keypoint facciali ruotati di 12 gradi e scalati di 2.4x
    theta = np.deg2rad(12.0)
    scale = 2.4
    R = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]], dtype=np.float32)
    t = np.array([310.0, 185.0], dtype=np.float32)

    # Punti sorgente nel frame camera a partire dal template canonico
    src_pts = (ARCFACE_SRC_3PT @ R.T) * scale + t

    M = compute_similarity_3pt(src_pts, ARCFACE_SRC_3PT)
    assert M.shape == (2, 3), f"Matrice affine attesa (2, 3), ottenuta {M.shape}"

    # Applica M ai punti sorgente e verifica che cadano esattamente su ARCFACE_SRC_3PT
    src_h = np.hstack([src_pts, np.ones((3, 1), dtype=np.float32)])
    mapped = src_h @ M.T
    err = float(np.max(np.abs(mapped - ARCFACE_SRC_3PT)))
    assert err < 1e-3, f"Errore di riallineamento 3-point similarity troppo elevato: {err}"


def test_cpp_hailo_bridge_pose_and_arcface_integration():
    """Verifica che hailo_bridge_node.cpp implementi il decoder Pose con cls_ptr e ArcFace 3-pt."""
    cpp_path = os.path.join(REPO_ROOT, "src", "hailo_bridge_node.cpp")
    assert os.path.isfile(cpp_path)
    with open(cpp_path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "has_active_cls_head" in code, "hailo_bridge_node.cpp deve usare la testa cls attiva del modello yolov8s_pose"
    assert "compute_similarity_3pt" in code, "hailo_bridge_node.cpp deve includere l'allineamento facciale a 3 punti"
    assert "recognize_faces" in code, "hailo_bridge_node.cpp deve eseguire recognize_faces sul VDevice condiviso"
    assert "infer_model_arcface_" in code, "hailo_bridge_node.cpp deve caricare arcface_mobilefacenet.hef sul VDevice"


@pytest.mark.skipif(
    not (
        os.path.exists("/dev/hailo0")
        and os.path.isfile("/mnt/ssd/models/yolov8s_pose.hef")
        and os.path.isfile("/mnt/ssd/models/arcface_mobilefacenet.hef")
    ),
    reason="Richiede NPU Hailo-10H fisica (/dev/hailo0) e modelli HEF v5.1.0 su Marcus",
)
def test_live_hailo_npu_archive_images_pose_and_face_id():
    """
    Test hardware end-to-end su NPU Hailo-10H con le 12 immagini d'archivio:
    - Usa rigorosamente l'API InferModel (SPEC-03 Zona Rossa, FM-VIS-005)
    - Verifica che yolov8s_pose.hef rilevi la posa con cls >= 0.75 su tutte le 12 foto
    - Verifica la coerenza anatomica dei keypoint (occhi, naso, orecchie, spalle)
    - Verifica il riconoscimento biometrico ArcFace 12/12 (100% top-1 accuracy)
    """
    from hailo_platform import VDevice, FormatType

    known_db = {}
    for person in EXPECTED_PERSONS:
        known_db[person] = np.load(os.path.join(KNOWN_FACES_DIR, person, "embedding.npy"))

    items = get_clean_archive_images()

    with VDevice() as target:
        pose_model = target.create_infer_model("/mnt/ssd/models/yolov8s_pose.hef")
        for outp in pose_model.outputs:
            outp.set_format_type(FormatType.FLOAT32)

        arc_model = target.create_infer_model("/mnt/ssd/models/arcface_mobilefacenet.hef")
        for outp in arc_model.outputs:
            outp.set_format_type(FormatType.FLOAT32)
        arc_out_name = arc_model.output_names[0]

        with pose_model.configure() as p_cfg, arc_model.configure() as a_cfg:
            for gt_person, img_path in items:
                bgr = cv2.imread(img_path)
                orig_h, orig_w = bgr.shape[:2]
                scale = min(640.0 / orig_w, 640.0 / orig_h)
                nw, nh = int(round(orig_w * scale)), int(round(orig_h * scale))
                px, py = (640 - nw) // 2, (640 - nh) // 2
                lb = np.full((640, 640, 3), 114, dtype=np.uint8)
                lb[py:py + nh, px:px + nw] = cv2.cvtColor(cv2.resize(bgr, (nw, nh)), cv2.COLOR_BGR2RGB)

                p_b = p_cfg.create_bindings()
                p_b.input().set_buffer(lb)
                res = {}
                for oname in pose_model.output_names:
                    shape = pose_model.output(oname).shape
                    buf = np.empty(shape, dtype=np.float32)
                    p_b.output(oname).set_buffer(buf)
                    res[oname] = buf
                p_cfg.run([p_b], 1000)

                # Find peak classification cell across strides 8, 16, 32
                best_cls = -1.0
                best_kpts = None
                for stride in (8, 16, 32):
                    gh, gw = 640 // stride, 640 // stride
                    cls_t = next(v for v in res.values() if v.shape == (gh, gw, 1))
                    kpt_t = next(v for v in res.values() if v.shape == (gh, gw, 51))
                    idx = int(np.argmax(cls_t))
                    gy, gx = divmod(idx, gw)
                    cval = float(cls_t[gy, gx, 0])
                    if cval > best_cls:
                        best_cls = cval
                        kc = kpt_t[gy, gx].reshape(17, 3)
                        kps = np.zeros((17, 3), dtype=np.float32)
                        for k in range(17):
                            kx = (kc[k, 0] * 2.0 + gx) * stride
                            ky = (kc[k, 1] * 2.0 + gy) * stride
                            kc_s = 1.0 / (1.0 + np.exp(-np.clip(kc[k, 2], -10.0, 10.0)))
                            kps[k] = [
                                np.clip((kx - px) / (scale * orig_w), 0.0, 1.0),
                                np.clip((ky - py) / (scale * orig_h), 0.0, 1.0),
                                kc_s,
                            ]
                        best_kpts = kps

                assert best_cls >= 0.75, f"Confidenza posa insufficiente ({best_cls:.2f}) su {img_path}"
                # Coerenza anatomica: naso ed occhi ad alta confidenza e ordinati
                nose, leye, reye = best_kpts[0], best_kpts[1], best_kpts[2]
                assert nose[2] >= 0.80 and leye[2] >= 0.80 and reye[2] >= 0.80
                assert reye[0] < leye[0], f"Inversione orizzontale occhi su {img_path}: REye.x={reye[0]} >= LEye.x={leye[0]}"
                assert reye[0] - 0.02 <= nose[0] <= leye[0] + 0.02, f"Naso fuori dall'asse inter-oculare su {img_path}"

                # Allineamento facciale 3-pt ed estrazione ArcFace
                p_nose = np.array([nose[0] * orig_w, nose[1] * orig_h], dtype=np.float32)
                p_l = np.array([leye[0] * orig_w, leye[1] * orig_h], dtype=np.float32)
                p_r = np.array([reye[0] * orig_w, reye[1] * orig_h], dtype=np.float32)
                img_left = p_r if p_r[0] < p_l[0] else p_l
                img_right = p_l if p_r[0] < p_l[0] else p_r
                M = compute_similarity_3pt([img_left, img_right, p_nose])
                aligned_rgb = np.ascontiguousarray(
                    cv2.cvtColor(cv2.warpAffine(bgr, M, (112, 112)), cv2.COLOR_BGR2RGB)
                )

                a_b = a_cfg.create_bindings()
                a_b.input().set_buffer(aligned_rgb)
                raw_emb = np.empty(arc_model.output(arc_out_name).shape, dtype=np.float32)
                a_b.output(arc_out_name).set_buffer(raw_emb)
                a_cfg.run([a_b], 1000)

                emb = raw_emb.flatten().astype(np.float32)
                emb /= np.linalg.norm(emb)

                best_name = max(known_db.keys(), key=lambda n: float(np.dot(emb, known_db[n])))
                best_sim = float(np.dot(emb, known_db[best_name]))
                assert best_name == gt_person, (
                    f"Errore riconoscimento su {os.path.basename(img_path)}: atteso {gt_person}, ottenuto {best_name} ({best_sim:.3f})"
                )
                assert best_sim >= 0.72, f"Similarita' ArcFace sotto soglia ({best_sim:.3f}) per {gt_person}"
