import sys
import unittest.mock as mock

class _RealNode:
    def __init__(self, *args, **kwargs):
        pass

for mod in [
    'rclpy', 'rclpy.node', 'rclpy.qos', 'rclpy.parameter',
    'geometry_msgs', 'geometry_msgs.msg',
    'sensor_msgs', 'sensor_msgs.msg',
    'std_msgs', 'std_msgs.msg',
    'nav_msgs', 'nav_msgs.msg',
    'cv_bridge', 'torch'
]:
    if mod not in sys.modules:
        sys.modules[mod] = mock.MagicMock()

sys.modules['rclpy.node'].Node = _RealNode

import time
import pytest
from robopy_controller.nodes.nomad_reactive_pipeline_node import StallSlipDetector


def test_stall_detector_pivot_immunity():
    """Verifica che durante le rotazioni pure sul posto lo stallo lineare rimanga inibito."""
    detector = StallSlipDetector(
        stall_vel_cmd_thresh=0.08,
        stall_wheel_vel_thresh=0.02,
        stall_duration_sec=0.75
    )

    t0 = 100.0
    # Simulazione di rotazione sul posto a 0.35 rad/s per 1.5 secondi
    for i in range(15):
        t = t0 + i * 0.1
        # cmd_v = 0.02 (bassa velocità residua), cmd_w = 0.35 rad/s, wheel_v = 0.00 m/s, wheel_w = 0.33 rad/s
        triggered, reason = detector.evaluate(
            cmd_v=0.02,
            cmd_w=0.35,
            wheel_v=0.00,
            wheel_w=0.33,
            vio_v=0.01,
            now_mono=t
        )
        assert not triggered, f"Falso allarme scattato al passo {i}: {reason}"
        assert reason == "NONE"


def test_stall_detector_real_wall_block():
    """Verifica che contro un muro vero con moto lineare lo stallo scatti dopo 0.75s."""
    detector = StallSlipDetector(
        stall_vel_cmd_thresh=0.08,
        stall_wheel_vel_thresh=0.02,
        stall_duration_sec=0.75
    )

    t0 = 100.0
    triggered = False
    trigger_time = None

    # Avanzamento in avanti contro un ostacolo rigido (cmd_v=0.15, wheel_v=0.005)
    for i in range(12):
        t = t0 + i * 0.1
        trig, reason = detector.evaluate(
            cmd_v=0.15,
            cmd_w=0.0,
            wheel_v=0.005,
            wheel_w=0.0,
            vio_v=0.002,
            now_mono=t
        )
        if trig:
            triggered = True
            trigger_time = t
            assert reason == "WHEEL_STALL_PINNED"
            break

    assert triggered, "Lo stallo reale contro il muro doveva scattare!"
    assert (trigger_time - t0) >= 0.75, "Lo stallo non ha rispettato la durata minima di debounce!"


def test_battery_params_thresholds():
    """Verifica che le soglie di battery_params siano armonizzate col cutoff BMS a 9.74V."""
    import yaml
    with open('robopy_controller/config/battery_params.yaml', 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    
    params = cfg['battery_manager_node']['ros__parameters']
    assert params['shutdown_voltage'] == 9.80, "shutdown_voltage deve essere 9.80V"
    assert params['docking_voltage'] == 10.15, "docking_voltage deve essere 10.15V"
    assert params['eco_voltage'] == 10.40, "eco_voltage deve essere 10.40V"
    assert params['shutdown_voltage'] > 9.74, "La soglia di shutdown deve anticipare il cutoff fisico BMS (9.74V)"


def test_voice_speak_cooldown_and_dedup():
    """Verifica che la logica di speak() filtri duplicati e rispetti il cooldown."""
    class DummyVoiceNav:
        def __init__(self):
            import queue
            self._speech_queue = queue.Queue()
            self.last_speech_time = 0.0
            self.last_spoken_phrase = ""
            self.speech_cooldown_sec = 4.0
            self.dedup_window_sec = 10.0
            self.is_speaking = False

        def get_logger(self):
            class DummyLogger:
                def info(self, msg): pass
            return DummyLogger()

    # Importiamo speak da marcus_voice_nav
    from scripts.marcus_voice_nav import MarcusVoiceNavigator
    node = DummyVoiceNav()

    # Prima frase: deve essere accodata
    MarcusVoiceNavigator.speak(node, "Ciao Luca")
    assert node._speech_queue.qsize() == 1
    assert node._speech_queue.get() == "Ciao Luca"

    # Seconda frase identica entro 10s: deve essere scartata
    MarcusVoiceNavigator.speak(node, "Ciao Luca")
    assert node._speech_queue.qsize() == 0

    # Terza frase diversa ma entro cooldown (4s): deve essere scartata
    MarcusVoiceNavigator.speak(node, "Ostacolo rilevato")
    assert node._speech_queue.qsize() == 0

    # Frase forzata con bypass (force=True): deve essere accodata
    MarcusVoiceNavigator.speak(node, "Allarme critico", force=True)
    assert node._speech_queue.qsize() == 1
    assert node._speech_queue.get() == "Allarme critico"


def test_vui_noise_transcription_filter():
    """[FM-VUI-033] Verifica che i tag di rumore ASR vengano classificati correttamente."""
    from robopy_controller.robot_ai.services.live_connection_manager import LiveConnectionManager

    # Tag di rumore puro -> True (deve essere scartato)
    assert LiveConnectionManager._is_noise_transcription("<noise>") is True
    assert LiveConnectionManager._is_noise_transcription("<laughter>") is True
    assert LiveConnectionManager._is_noise_transcription("<cough>") is True
    assert LiveConnectionManager._is_noise_transcription("<sigh>") is True
    assert LiveConnectionManager._is_noise_transcription("<noise> <laughter>") is True
    assert LiveConnectionManager._is_noise_transcription("[noise]") is True
    assert LiveConnectionManager._is_noise_transcription("") is True
    assert LiveConnectionManager._is_noise_transcription("   ") is True
    assert LiveConnectionManager._is_noise_transcription("...") is True
    assert LiveConnectionManager._is_noise_transcription("a") is True

    # Frasi reali con parole -> False (devono essere processate)
    assert LiveConnectionManager._is_noise_transcription("ciao marcus") is False
    assert LiveConnectionManager._is_noise_transcription("esplora la stanza") is False
    assert LiveConnectionManager._is_noise_transcription("ok") is False
    assert LiveConnectionManager._is_noise_transcription("<noise> ciao") is False


def test_nomad_exploration_skill_schema_and_action():
    """[FM-NOM-011] Verifica schema e priorità della skill NomadExploration."""
    import asyncio
    from robopy_controller.robot_ai.skills.builtin.nomad_exploration_skill import NomadExplorationSkill

    skill = NomadExplorationSkill(ros_node=None)
    meta = skill.get_metadata()
    assert meta.name == "nomad_exploration"
    assert meta.priority >= 25, "La skill nomad_exploration deve avere priorità >= 25"
    assert "esplora" in meta.keywords

    schema = skill.get_parameters_schema()
    assert schema["type"] == "object"
    assert "action" in schema["properties"]
    assert "start" in schema["properties"]["action"]["enum"]
    assert "stop" in schema["properties"]["action"]["enum"]

    # Match test
    assert skill.match("esplora la stanza con nomad") >= 0.95
    assert skill.match("ferma l'esplorazione") >= 0.95

    # Execution test: start
    loop = asyncio.new_event_loop()
    res_start = loop.run_until_complete(skill.execute("", context={"action": "start"}))
    assert res_start.success is True
    assert skill.is_exploring is True
    assert "NOMAD" in res_start.speak

    # Execution test: stop
    res_stop = loop.run_until_complete(skill.execute("", context={"action": "stop"}))
    assert res_stop.success is True
    assert skill.is_exploring is False
    loop.close()


def test_navigation_skill_explore_delegation():
    """[FM-NOM-011] Verifica che navigation_skill deleghi l'esplorazione a NOMAD."""
    import asyncio
    from robopy_controller.robot_ai.skills.builtin.navigation_skill import NavigationSkill

    nav = NavigationSkill(nav_client=None)
    loop = asyncio.new_event_loop()
    res = loop.run_until_complete(nav.execute("esplora la stanza"))
    assert res.success is True
    assert any(a.get("action_type") == "nomad_exploration" for a in res.actions)
    loop.close()


if __name__ == '__main__':
    pytest.main(['-v', __file__])
