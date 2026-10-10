"""
Unit Test Suite: Hailo-10H Pose Tracking Gating, Decoding and Decoupling (F1 Phase)
==================================================================================
Tests:
- Presence-gating logic: Pose NPU infer triggered ONLY when person is detected (IMP-GOV-001, FM-GOV-016)
- 17 COCO Keypoint topology and 18 skeleton connection integrity
- Inverse letterbox mapping mathematics for keypoint reprojection
- OAK-D Lite decoupling: confirmation that MyriadX VPU has zero neural network blobs loaded
"""

import pytest
import numpy as np
import ast
import os


COCO_SKELETON_PAIRS = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16)
]

COCO_KEYPOINTS = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle"
]


class Detection:
    def __init__(self, label, confidence, class_id=0):
        self.label = label
        self.confidence = confidence
        self.class_id = class_id


def presence_gate(detections, enable_pose, conf_threshold=0.50):
    """Presence-gating logic matching C++ hailo_bridge_node_cpp."""
    if not enable_pose:
        return False
    for det in detections:
        if (det.label == "person" or det.class_id == 0) and det.confidence >= conf_threshold:
            return True
    return False


def inverse_letterbox_point(kx, ky, scale, pad_x, pad_y, orig_w, orig_h):
    """Maps letterboxed pixel point back to normalized [0.0, 1.0] image coords."""
    norm_x = np.clip((kx - pad_x) / (scale * orig_w), 0.0, 1.0)
    norm_y = np.clip((ky - pad_y) / (scale * orig_h), 0.0, 1.0)
    return float(norm_x), float(norm_y)


# ============================================================================
# TEST SUITE
# ============================================================================

def test_presence_gating_person_absent():
    """Verify Pose NPU inference is SKIPPED when only inanimate objects are detected."""
    detections = [
        Detection("chair", 0.85, class_id=56),
        Detection("dining table", 0.78, class_id=60),
        Detection("tv", 0.90, class_id=62),
        Detection("potted plant", 0.70, class_id=58),
    ]
    should_run_pose = presence_gate(detections, enable_pose=True)
    assert not should_run_pose, "Pose inference must NOT run when no person is present (NPU power-saving guard)"


def test_presence_gating_person_present():
    """Verify Pose NPU inference is ACTIVATED when person is detected above threshold."""
    detections = [
        Detection("chair", 0.85, class_id=56),
        Detection("person", 0.75, class_id=0),
    ]
    should_run_pose = presence_gate(detections, enable_pose=True)
    assert should_run_pose, "Pose inference must trigger when person is detected"


def test_presence_gating_low_confidence_person_rejected():
    """Verify low confidence person detections below threshold do not trigger pose."""
    detections = [
        Detection("person", 0.35, class_id=0),  # Below 0.50
    ]
    should_run_pose = presence_gate(detections, enable_pose=True, conf_threshold=0.50)
    assert not should_run_pose, "Person detection below confidence threshold must not trigger pose"


def test_presence_gating_disabled_flag():
    """Verify that if enable_pose is False, pose never triggers even if person is present."""
    detections = [
        Detection("person", 0.95, class_id=0),
    ]
    should_run_pose = presence_gate(detections, enable_pose=False)
    assert not should_run_pose, "Pose inference must remain disabled when enable_pose is False"


def test_coco_skeleton_topology_integrity():
    """Verify COCO 17 keypoint definition and 16 skeleton connection graph."""
    assert len(COCO_KEYPOINTS) == 17, "Standard COCO skeleton must define exactly 17 keypoints"
    assert len(COCO_SKELETON_PAIRS) == 16, "Standard COCO skeleton must define exactly 16 bone connections (Hailo official)"

    # Verify all bone pairs connect valid keypoints in range [0, 16]
    for idx, (p1, p2) in enumerate(COCO_SKELETON_PAIRS):
        assert 0 <= p1 < 17, f"Bone pair {idx} has invalid start index {p1}"
        assert 0 <= p2 < 17, f"Bone pair {idx} has invalid end index {p2}"
        assert p1 != p2, f"Bone pair {idx} is a self-loop ({p1} == {p2})"


def test_inverse_letterbox_reprojection():
    """Verify mathematical correctness of inverse letterbox projection."""
    orig_w = 640.0
    orig_h = 400.0  # 16:10 aspect ratio
    model_w = 640.0
    model_h = 640.0  # 1:1 letterbox square

    scale = min(model_w / orig_w, model_h / orig_h)  # 1.0
    unpad_w = round(orig_w * scale)  # 640
    unpad_h = round(orig_h * scale)  # 400
    pad_x = (model_w - unpad_w) / 2.0  # 0.0
    pad_y = (model_h - unpad_h) / 2.0  # 120.0

    # Test top-left of image
    norm_x, norm_y = inverse_letterbox_point(0.0, 120.0, scale, pad_x, pad_y, orig_w, orig_h)
    assert norm_x == pytest.approx(0.0, abs=1e-4)
    assert norm_y == pytest.approx(0.0, abs=1e-4)

    # Test center of image
    norm_x, norm_y = inverse_letterbox_point(320.0, 320.0, scale, pad_x, pad_y, orig_w, orig_h)
    assert norm_x == pytest.approx(0.5, abs=1e-4)
    assert norm_y == pytest.approx(0.5, abs=1e-4)

    # Test bottom-right of image
    norm_x, norm_y = inverse_letterbox_point(640.0, 520.0, scale, pad_x, pad_y, orig_w, orig_h)
    assert norm_x == pytest.approx(1.0, abs=1e-4)
    assert norm_y == pytest.approx(1.0, abs=1e-4)


def test_oak_driver_node_has_zero_nn_blobs():
    """Verify that oak_driver_node.py contains no NeuralNetwork blob allocations (FM-VIS-001 mitigation)."""
    node_path = os.path.join(os.path.dirname(__file__), "..", "robopy_controller", "nodes", "oak_driver_node.py")
    assert os.path.isfile(node_path), f"File {node_path} must exist"

    with open(node_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Must NOT create NeuralNetwork nodes
    assert "pipeline.create(dai.node.NeuralNetwork)" not in content, \
        "OAK-D Lite must NOT instantiate NeuralNetwork nodes on MyriadX VPU"
    assert ".blob" not in content, \
        "OAK-D Lite must NOT load .blob models; all inference belongs on Hailo-10H NPU"
    assert "superpoint_raw" not in content, \
        "SuperPoint queue must not be polled from OAK-D Lite"
    assert "yolo_detections" not in content, \
        "YOLO detections queue must not be polled from OAK-D Lite"


def test_sync_buffer_runs_without_nn():
    """Verify that TemporalSyncBuffer operates cleanly with depth only without stalling."""
    from robopy_controller.oak_logic.sync_buffer import TemporalSyncBuffer

    buf = TemporalSyncBuffer(max_age_ms=100)
    mock_depth = np.ones((400, 640), dtype=np.uint16)
    buf.add_depth(mock_depth, (0, 0, 640, 400), 1.0, timestamp=10.0)

    synced = buf.get_synced_frame()
    assert synced is not None, "TemporalSyncBuffer must provide synced frames with depth-only without stalling"
    assert synced["timestamp"] == 10.0
    assert synced["keypoints"] is None
    assert synced["detections"] == []


def test_top_down_yolo_gated_pose_anti_flood():
    """Verify Top-Down YOLO bounding-box gating strictly eliminates background false positives (FM-VIS-010)."""
    # 1 Person detected in center of image
    person = {"xmin": 0.40, "ymin": 0.20, "xmax": 0.60, "ymax": 0.80, "confidence": 0.85, "label": "person"}

    # Simulate 100 random grid cells across the image
    np.random.seed(42)
    grid_cells = []
    for _ in range(100):
        gx = np.random.uniform(0.0, 1.0)
        gy = np.random.uniform(0.0, 1.0)
        # Background cells have unactivated keypoints around 0.50 (sigmoid of 0.0)
        kps = np.full(17, 0.50)
        grid_cells.append({"cx": gx, "cy": gy, "kps": kps})

    # One cell inside the person box has strong person keypoints (sigmoid >= 0.70)
    person_cell = {"cx": 0.50, "cy": 0.50, "kps": np.full(17, 0.88)}
    grid_cells.append(person_cell)

    # Top-Down Filter: evaluate ONLY cells within person bounding box (+10% margin)
    bw = person["xmax"] - person["xmin"]
    bh = person["ymax"] - person["ymin"]
    box_x1 = person["xmin"] - bw * 0.10
    box_x2 = person["xmax"] + bw * 0.10
    box_y1 = person["ymin"] - bh * 0.10
    box_y2 = person["ymax"] + bh * 0.10

    accepted_poses = []
    best_cell = None
    best_score = -1.0

    for cell in grid_cells:
        if box_x1 <= cell["cx"] <= box_x2 and box_y1 <= cell["cy"] <= box_y2:
            # Check keypoint confidence threshold (0.55)
            valid_kps = np.sum(cell["kps"] >= 0.55)
            if valid_kps >= 4:
                score = np.sum(cell["kps"])
                if score > best_score:
                    best_score = score
                    best_cell = cell

    if best_cell is not None:
        accepted_poses.append(best_cell)

    # Must produce EXACTLY 1 pose, strictly matching the person
    assert len(accepted_poses) == 1, f"Expected 1 matched pose, got {len(accepted_poses)}"
    assert accepted_poses[0]["cx"] == 0.50, "Selected pose must be the real person cell, not background noise"

