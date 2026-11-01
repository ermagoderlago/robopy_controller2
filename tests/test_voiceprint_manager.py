import sys
import os
import tempfile
import json
import numpy as np
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.nodes.voiceprint_manager import VoicePrintManager


def test_voiceprint_manager_l2_normalization_and_match():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        # Pre-seed a known profile (e.g. Luca)
        vec = np.random.randn(128).astype(np.float32)
        vec /= np.linalg.norm(vec)
        json.dump({"luca": vec.tolist()}, f)
        tmp_path = f.name

    try:
        vpm = VoicePrintManager(storage_file=tmp_path, similarity_threshold=0.72)
        
        # Test 1: Identical embedding should match with score ~1.0
        name, score = vpm.identify_speaker(vec)
        assert name.lower() == "luca"
        assert score >= 0.99

        # Test 2: Highly similar embedding (cosine > 0.72)
        noise = np.random.randn(128).astype(np.float32) * 0.02
        noisy_vec = vec + noise
        noisy_vec /= np.linalg.norm(noisy_vec)
        name, score = vpm.identify_speaker(noisy_vec)
        assert name.lower() == "luca"
        assert score >= 0.72

        # Test 3: Orthogonal / random embedding should be classified as 'Sconosciuto'
        random_vec = np.random.randn(128).astype(np.float32)
        # Ensure orthogonal
        random_vec -= np.dot(random_vec, vec) * vec
        random_vec /= np.linalg.norm(random_vec)
        name, score = vpm.identify_speaker(random_vec)
        assert name == "Sconosciuto"
        assert score < 0.72

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_voiceprint_manager_empty_and_zero_vectors():
    vpm = VoicePrintManager(storage_file="/nonexistent.json")
    
    # Empty embedding
    name, score = vpm.identify_speaker(np.array([], dtype=np.float32))
    assert name == "Sconosciuto"
    assert score == 0.0

    # Zero vector
    zero_vec = np.zeros(128, dtype=np.float32)
    name, score = vpm.identify_speaker(zero_vec)
    assert name == "Sconosciuto"
    assert score == 0.0
