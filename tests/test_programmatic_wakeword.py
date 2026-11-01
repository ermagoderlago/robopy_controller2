import sys
import os
import re
import numpy as np
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.services.local_asr_vosk import VoskASRManager


class MockVoskWakeWordHarness(VoskASRManager):
    """Harness testing Vosk wake-word grammar, energy gating, and regex matching."""
    def __init__(self):
        super(VoskASRManager, self).__init__()
        import queue
        self._audio_queue = queue.Queue(maxsize=300)
        self._shutdown = False
        self._drop_count = 0
        self.energy_gating = True
        self._noise_floor_ema = 60.0
        self._hangover_chunks = 0
        self._max_hangover_chunks = 15
        self._min_rms_threshold = 80.0
        self._last_was_speech = False
        self.detected_wake_words = []

    def is_active(self):
        return True

    def simulate_transcription(self, recognized_text: str):
        text_lower = recognized_text.lower().strip()
        # SPEC-04 / FIX-12a Regex
        ww_detected = bool(re.search(r'\b(marcus|markus|marcos)\b', text_lower))
        if ww_detected:
            self.detected_wake_words.append(recognized_text)
        return ww_detected


def test_programmatic_wakeword_detection_variants():
    harness = MockVoskWakeWordHarness()

    valid_prompts = [
        "Marcus",
        "Ciao Marcus",
        "markus puoi sentirmi?",
        "ehi marcos",
    ]

    for p in valid_prompts:
        detected = harness.simulate_transcription(p)
        assert detected is True, f"Wake word was not detected for valid prompt: '{p}'"


def test_programmatic_wakeword_rejection_of_italian_name_marco():
    harness = MockVoskWakeWordHarness()

    # Utterances mentioning Marco must NOT wake up Marcus (FM-VUI-037 / FIX-12a)
    false_positive_prompts = [
        "Ciao Marco come stai?",
        "Marco ha detto che arriva tardi",
        "ho parlato con marco ieri sera",
        "marco",
    ]

    for p in false_positive_prompts:
        detected = harness.simulate_transcription(p)
        assert detected is False, f"False positive wake word triggered for: '{p}'!"


def test_energy_gating_and_noise_floor_clamping():
    """Verify that noise floor EMA is clamped between [30.0, 120.0] and does not drift deaf."""
    mgr = MockVoskWakeWordHarness()
    mgr.energy_gating = True

    # Feed loud transient noise bursts
    loud_noise = (np.random.normal(0, 1000, 960)).astype(np.int16).tobytes()
    for _ in range(100):
        mgr.process_audio(loud_noise)

    # Noise floor EMA must stay bounded below 120.0
    assert mgr._noise_floor_ema <= 120.0, f"Noise floor drifted too high: {mgr._noise_floor_ema} > 120.0"

    # Feed very low silence
    silence = (np.random.normal(0, 10, 960)).astype(np.int16).tobytes()
    for _ in range(100):
        mgr.process_audio(silence)

    assert mgr._noise_floor_ema >= 30.0, f"Noise floor drifted too low: {mgr._noise_floor_ema} < 30.0"


def test_production_source_code_wakeword_vocabulary_and_regex():
    """
    Adversarial verification against production source code:
    Ensures that production local_asr_vosk.py and respeaker_vui_node.py
    strictly accept 'marcus', 'markus', 'marcos' and reject 'marco'.
    """
    # 1. Inspect local_asr_vosk.py grammar directly
    vosk_path = os.path.join(_PROJECT_ROOT, "robopy_controller", "robot_ai", "services", "local_asr_vosk.py")
    assert os.path.exists(vosk_path), f"File {vosk_path} must exist"
    with open(vosk_path, "r", encoding="utf-8") as f:
        vosk_code = f.read()

    # Grammar list must contain targeted keywords and NOT marco
    import json
    grammar_match = re.search(r'self\.wake_grammar\s*=\s*json\.dumps\((\[[\s\S]*?\])\)', vosk_code)
    assert grammar_match is not None, "self.wake_grammar definition not found in local_asr_vosk.py"
    
    # Safely evaluate grammar literal
    import ast
    grammar_list = ast.literal_eval(grammar_match.group(1))
    assert "marcus" in grammar_list, "'marcus' must be in Vosk wake_grammar"
    assert "markus" in grammar_list, "'markus' must be in Vosk wake_grammar"
    assert "marcos" in grammar_list, "'marcos' must be in Vosk wake_grammar"
    assert "marco" not in grammar_list, "'marco' MUST NOT be in Vosk wake_grammar (prevents false positive activations)"

    # 2. Inspect respeaker_vui_node.py regex directly
    vui_path = os.path.join(_PROJECT_ROOT, "robopy_controller", "nodes", "respeaker_vui_node.py")
    assert os.path.exists(vui_path), f"File {vui_path} must exist"
    with open(vui_path, "r", encoding="utf-8") as f:
        vui_code = f.read()

    regex_match = re.search(r"ww_detected\s*=\s*bool\(re\.search\((r['\"].*?['\"]), text_lower\)\)", vui_code)
    assert regex_match is not None, "Wake word regex pattern not found in respeaker_vui_node.py"
    raw_pattern = ast.literal_eval(regex_match.group(1))
    
    compiled = re.compile(raw_pattern)
    assert compiled.search("marcus") is not None
    assert compiled.search("ehi markus") is not None
    assert compiled.search("ciao marcos") is not None
    assert compiled.search("marco") is None, "Production regex must NOT match 'marco'"
    assert compiled.search("ciao marco come stai?") is None, "Production regex must NOT match 'ciao marco come stai?'"

