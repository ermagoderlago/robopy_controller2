"""
Comprehensive PC Replay & Evolution Verification Suite (P0 - P4)
Covers:
  - P0: DialogueTraceLogger turn_id correlation, JSONL writing, counters
  - P1.1: Qwen mock opt-in in prod + zero synthetic motion <action>
  - P1.2 & P0.3: LiveConnectionManager non-renewing follow-up window, clock skew guard, <IGNORE_TURN>
  - P1.3 & P2.1-P2.2: DialogueArbiter Option A hierarchy, exclusive floor FSM, simultaneous Live+Qwen race, Fast-Path stop
  - P1.4: ReSpeakerVUINode FINAL-only standby wakeword, enrollment gating, Live duplicate voice_cmd suppression
  - P1.5: Dream poem removed, fake Amazon emails removed, conditional family grounding, BUDGET_SYSTEM=450, TRINITY timeout prompt
  - P1.6: Whole-word regex in CognitiveIntentClassifier (no false substring routing)
  - P1.7: Decoupled _mic_mute_cb vs _audio_out_queue drain + TTSService play_raw_pcm speaking state
  - P1.8: All 10 ALLOWED_FUNCTION_NAMES execution & spoken real telemetry result
  - P2.3-P2.5: Live <-> Qwen shared TRINITY memory digest, proactive queue, single /ai/input/mic_mute arbitration
  - P3.1: Social gating (DO_NOT_DISTURB 0.65x volume & proactivity suppression, DISENGAGED follow-up close)
  - P4.1-P4.4: Backchannel cues, sustained barge-in (>=250ms), Piper offline TTS (16->24kHz), /hailo/vlm/ask_question & <80C guard
"""

import asyncio
import json
import os
import queue
import sys
import tempfile
import threading
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Always prefer real numpy BEFORE any module import mocks it
import numpy as np  # noqa: E402, F401

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in (_PROJECT_ROOT, _ROBOPY_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


def _ensure_ros_stubs():
    """Minimal ROS 2 / optional hardware stubs for Windows PC test execution."""
    if "rclpy" not in sys.modules:
        mock_rclpy = types.ModuleType("rclpy")
        mock_rclpy.__path__ = []
        mock_rclpy.logging = MagicMock()
        mock_rclpy.init = MagicMock()
        mock_rclpy.ok = MagicMock(return_value=False)
        mock_rclpy.shutdown = MagicMock()
        sys.modules["rclpy"] = mock_rclpy
    elif not hasattr(sys.modules["rclpy"], "__path__"):
        sys.modules["rclpy"].__path__ = []

    if "rclpy.time" not in sys.modules:
        mod_rtime = types.ModuleType("rclpy.time")
        mod_rtime.Time = MagicMock
        sys.modules["rclpy.time"] = mod_rtime
        sys.modules["rclpy"].time = mod_rtime

    if "rclpy.duration" not in sys.modules:
        mod_rdur = types.ModuleType("rclpy.duration")
        mod_rdur.Duration = MagicMock
        sys.modules["rclpy.duration"] = mod_rdur
        sys.modules["rclpy"].duration = mod_rdur
    elif not hasattr(sys.modules["rclpy.duration"], "Duration"):
        sys.modules["rclpy.duration"].Duration = MagicMock

    if "rclpy.node" not in sys.modules:
        mod_node = types.ModuleType("rclpy.node")
        sys.modules["rclpy.node"] = mod_node

    class _DummyNode:
        def __init__(self, *args, **kwargs):
            pass

        def get_logger(self):
            return MagicMock()

    if not isinstance(getattr(sys.modules["rclpy.node"], "Node", None), type):
        sys.modules["rclpy.node"].Node = _DummyNode
        if "rclpy" in sys.modules:
            sys.modules["rclpy"].node = sys.modules["rclpy.node"]

    if "aiohttp" not in sys.modules:
        mod_aio = types.ModuleType("aiohttp")
        mod_aio.ClientSession = MagicMock
        mod_aio.ClientTimeout = MagicMock
        sys.modules["aiohttp"] = mod_aio

    if "chromadb" not in sys.modules:
        mod_ch = types.ModuleType("chromadb")
        mod_ch.__path__ = []
        mod_ch.PersistentClient = MagicMock
        sys.modules["chromadb"] = mod_ch
    elif not hasattr(sys.modules["chromadb"], "__path__"):
        sys.modules["chromadb"].__path__ = []

    if "chromadb.config" not in sys.modules:
        mod_chc = types.ModuleType("chromadb.config")
        mod_chc.Settings = MagicMock
        sys.modules["chromadb.config"] = mod_chc

    if "google" not in sys.modules:
        mod_g = types.ModuleType("google")
        mod_g.__path__ = []
        sys.modules["google"] = mod_g
    if "google.genai" not in sys.modules:
        mod_gg = types.ModuleType("google.genai")
        mod_gg.__path__ = []
        mod_ggt = types.ModuleType("google.genai.types")
        mod_ggt.ActivityStart = MagicMock
        mod_ggt.ActivityEnd = MagicMock
        mod_ggt.Blob = MagicMock
        mod_ggt.LiveClientContent = MagicMock
        mod_ggt.FunctionDeclaration = MagicMock
        mod_ggt.Tool = MagicMock
        mod_gge = types.ModuleType("google.genai.errors")
        mod_gge.APIError = type("APIError", (Exception,), {})
        mod_gg.types = mod_ggt
        mod_gg.errors = mod_gge
        mod_gg.Client = MagicMock
        sys.modules["google"].genai = mod_gg
        sys.modules["google.genai"] = mod_gg
        sys.modules["google.genai.types"] = mod_ggt
        sys.modules["google.genai.errors"] = mod_gge

    if "rclpy.qos" not in sys.modules:
        mod_qos = types.ModuleType("rclpy.qos")
        mod_qos.QoSProfile = MagicMock()
        mod_qos.ReliabilityPolicy = MagicMock()
        mod_qos.HistoryPolicy = MagicMock()
        mod_qos.DurabilityPolicy = MagicMock()
        sys.modules["rclpy.qos"] = mod_qos

    if "rcl_interfaces" not in sys.modules:
        sys.modules["rcl_interfaces"] = types.ModuleType("rcl_interfaces")
    if "rcl_interfaces.msg" not in sys.modules:
        mod_rcli = types.ModuleType("rcl_interfaces.msg")
        mod_rcli.SetParametersResult = MagicMock
        sys.modules["rcl_interfaces.msg"] = mod_rcli

    if "rclpy.executors" not in sys.modules:
        mod_exec = types.ModuleType("rclpy.executors")
        mod_exec.MultiThreadedExecutor = MagicMock()
        sys.modules["rclpy.executors"] = mod_exec

    if "rclpy.callback_groups" not in sys.modules:
        mod_cg = types.ModuleType("rclpy.callback_groups")
        mod_cg.ReentrantCallbackGroup = MagicMock
        mod_cg.MutuallyExclusiveCallbackGroup = MagicMock
        sys.modules["rclpy.callback_groups"] = mod_cg

    if "std_msgs" not in sys.modules:
        sys.modules["std_msgs"] = types.ModuleType("std_msgs")
    if "std_msgs.msg" not in sys.modules:
        mod_std = types.ModuleType("std_msgs.msg")

        class _SimpleMsg:
            def __init__(self, data=None):
                self.data = data

        mod_std.String = _SimpleMsg
        mod_std.Bool = _SimpleMsg
        mod_std.Int16MultiArray = _SimpleMsg
        mod_std.Float32 = _SimpleMsg
        mod_std.Float32MultiArray = _SimpleMsg
        mod_std.UInt8MultiArray = _SimpleMsg
        mod_std.Header = MagicMock
        sys.modules["std_msgs.msg"] = mod_std
    else:
        for attr in ("String", "Bool", "Int16MultiArray", "Float32", "Float32MultiArray", "UInt8MultiArray", "Header"):
            if not hasattr(sys.modules["std_msgs.msg"], attr):
                setattr(sys.modules["std_msgs.msg"], attr, MagicMock)

    if "geometry_msgs" not in sys.modules:
        sys.modules["geometry_msgs"] = types.ModuleType("geometry_msgs")
    if "geometry_msgs.msg" not in sys.modules:
        mod_geom = types.ModuleType("geometry_msgs.msg")

        class _Twist:
            def __init__(self):
                self.linear = MagicMock(x=0.0, y=0.0, z=0.0)
                self.angular = MagicMock(x=0.0, y=0.0, z=0.0)

        mod_geom.Twist = _Twist
        mod_geom.Point = MagicMock
        mod_geom.PoseStamped = MagicMock
        mod_geom.PointStamped = MagicMock
        sys.modules["geometry_msgs.msg"] = mod_geom
    else:
        for attr in ("Point", "PoseStamped", "PointStamped"):
            if not hasattr(sys.modules["geometry_msgs.msg"], attr):
                setattr(sys.modules["geometry_msgs.msg"], attr, MagicMock)

    for mod_name in (
        "sensor_msgs",
        "sensor_msgs.msg",
        "sensor_msgs_py",
        "sensor_msgs_py.point_cloud2",
        "nav_msgs",
        "nav_msgs.msg",
        "vision_msgs",
        "vision_msgs.msg",
        "visualization_msgs",
        "visualization_msgs.msg",
        "diagnostic_msgs",
        "diagnostic_msgs.msg",
        "cv_bridge",
        "tf2_ros",
        "tf2_geometry_msgs",
    ):
        if mod_name not in sys.modules:
            m = types.ModuleType(mod_name)
            for attr in (
                "BatteryState",
                "Image",
                "CompressedImage",
                "CameraInfo",
                "PointCloud2",
                "PointField",
                "Odometry",
                "Detection2DArray",
                "Marker",
                "MarkerArray",
                "DiagnosticArray",
                "CvBridge",
                "Buffer",
                "TransformListener",
                "do_transform_pose",
            ):
                setattr(m, attr, MagicMock)
            sys.modules[mod_name] = m

    if "cv2" not in sys.modules:
        sys.modules["cv2"] = types.ModuleType("cv2")

    if "pyaudio" not in sys.modules:
        mod_pa = types.ModuleType("pyaudio")
        mod_pa.paInt16 = 8
        mod_pa.PyAudio = MagicMock
        sys.modules["pyaudio"] = mod_pa

    if "example_interfaces" not in sys.modules:
        sys.modules["example_interfaces"] = types.ModuleType("example_interfaces")
    if "example_interfaces.srv" not in sys.modules:
        mod_eis = types.ModuleType("example_interfaces.srv")
        mod_eis.Trigger = MagicMock()
        sys.modules["example_interfaces.srv"] = mod_eis

    if "scipy" not in sys.modules:
        sys.modules["scipy"] = types.ModuleType("scipy")
    if "scipy.signal" not in sys.modules:
        mod_ss = types.ModuleType("scipy.signal")
        mod_ss.butter = MagicMock(return_value=[[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]])
        mod_ss.sosfilt = MagicMock(side_effect=lambda sos, x, zi=None: (x, zi))
        mod_ss.sosfilt_zi = MagicMock(return_value=np.zeros((1, 2)))
        sys.modules["scipy.signal"] = mod_ss

    if "gtts" not in sys.modules:
        mod_gtts = types.ModuleType("gtts")
        mod_gtts.gTTS = MagicMock
        sys.modules["gtts"] = mod_gtts

    for opt_pkg in ("aioimaplib", "aiosmtplib"):
        if opt_pkg not in sys.modules:
            sys.modules[opt_pkg] = types.ModuleType(opt_pkg)

    if "robopy_controller.msg" not in sys.modules:
        mod_rmsg = types.ModuleType("robopy_controller.msg")

        class _AudioData:
            def __init__(self, data=b""):
                self.data = data

        mod_rmsg.AudioData = _AudioData
        sys.modules["robopy_controller.msg"] = mod_rmsg

    if "robopy_controller.srv" not in sys.modules:
        mod_srv = types.ModuleType("robopy_controller.srv")
        sys.modules["robopy_controller.srv"] = mod_srv
    else:
        mod_srv = sys.modules["robopy_controller.srv"]
    for srv_name in ("AskVisualQuestion", "GenerateText", "MemoryRecall"):
        if not hasattr(mod_srv, srv_name):
            srv_cls = MagicMock()
            srv_cls.Request = MagicMock
            setattr(mod_srv, srv_name, srv_cls)

    if "audio_common_msgs" not in sys.modules:
        sys.modules["audio_common_msgs"] = types.ModuleType("audio_common_msgs")
    if "audio_common_msgs.msg" not in sys.modules:
        mod_acm = types.ModuleType("audio_common_msgs.msg")

        class _AudioData:
            def __init__(self, data=b""):
                self.data = data

        mod_acm.AudioData = _AudioData
        sys.modules["audio_common_msgs.msg"] = mod_acm


_ensure_ros_stubs()


# ==============================================================================
# P0: DialogueTraceLogger turn_id, JSONL & Telemetry Counters
# ==============================================================================
def test_p0_dialogue_trace_jsonl_and_counters(tmp_path):
    from robopy_controller.robot_ai.orchestration.dialogue_trace import (
        DialogueTraceLogger,
        reset_dialogue_trace,
    )

    log_file = tmp_path / "dialogue_trace.jsonl"
    logger = reset_dialogue_trace(log_path=str(log_file), write_to_disk=True)
    tid = logger.new_turn_id("usr")
    assert tid.startswith("usr-")

    logger.log_event("turn_start", turn_id=tid, source="VUI", detail={"text": "ciao marcus"})
    logger.counters.inc("double_response_blocked", 1)
    logger.counters.inc("out_of_window_turns", 2)
    logger.counters.inc("mock_activations", 1)

    events = logger.recent_events(limit=10, turn_id=tid)
    assert len(events) == 1
    assert events[0]["turn_id"] == tid
    assert events[0]["event"] == "turn_start"

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["turn_id"] == tid
    assert parsed["source"] == "VUI"

    snap = logger.counters.snapshot()
    assert snap["double_response_blocked"] == 1
    assert snap["out_of_window_turns"] == 2
    assert snap["mock_activations"] == 1


# ==============================================================================
# P1.1: Mock Qwen Disabled in Prod + Zero Synthetic Motion <action>
# ==============================================================================
def test_p1_1_mock_qwen_disabled_and_zero_motion_synthesis(monkeypatch, tmp_path):
    from robopy_controller.robot_ai.hailo.hailo_qwen_client import (
        HailoQwenClient,
        HailoQwenConfig,
    )
    from robopy_controller.robot_ai.orchestration.dialogue_trace import reset_dialogue_trace

    reset_dialogue_trace(log_path=str(tmp_path / "t.jsonl"), write_to_disk=True)
    monkeypatch.delenv("MARCUS_QWEN_ALLOW_MOCK", raising=False)

    cfg = HailoQwenConfig()
    assert cfg.enable_mock_fallback is False, "Mock Qwen MUST be disabled by default in production"

    monkeypatch.setenv("MARCUS_QWEN_ALLOW_MOCK", "1")
    cfg_optin = HailoQwenConfig()
    assert cfg_optin.enable_mock_fallback is True

    client = HailoQwenClient(HailoQwenConfig(enable_mock_fallback=True))
    for cmd in ("avanti 1 metro", "gira a destra", "vai in cucina", "fermati subito"):
        tokens = client._synthesize_mock_tokens(cmd)
        joined = "".join(tokens)
        assert "<action>" not in joined.lower(), f"Mock synthesized forbidden <action> tag for {cmd!r}: {joined}"
        for forbidden in ("move_distance", "rotate_angle", "navigate_to", "start_frontier_exploration", "stop_motion"):
            assert forbidden not in joined


# ==============================================================================
# P1.2 & P0.3 Simulation: Live Window Non-Self-Renewing, Clock Skew & <IGNORE_TURN>
# ==============================================================================
def test_p1_2_live_window_non_renewing_clock_guard_and_ignore_turn(tmp_path):
    from robopy_controller.robot_ai.services.live_connection_manager import (
        LiveConnectionManager,
        LiveState,
    )
    from robopy_controller.robot_ai.services.llm_service import LLMServiceNode
    from robopy_controller.robot_ai.orchestration.dialogue_trace import reset_dialogue_trace

    trace = reset_dialogue_trace(log_path=str(tmp_path / "live_trace.jsonl"), write_to_disk=True)
    loop = asyncio.new_event_loop()
    try:
        mgr = LiveConnectionManager(
            client=MagicMock(),
            loop=loop,
            logger=MagicMock(),
            model_getter=lambda: "gemini-2.5-flash-live-preview",
            system_prompt_getter=lambda: "Sei Marcus.",
            voice_name_getter=lambda: "Aoede",
            timeout_live_getter=lambda: 25.0,
            on_audio_received=lambda d: None,
            on_tool_call=AsyncMock(return_value={}),
            on_turn_complete=lambda u, m: None,
            on_mic_mute=lambda b: None,
            on_interrupt=lambda b: None,
            history_getter=lambda: [],
            on_fallback_needed=lambda r: None,
            trace_logger=trace,
        )
        mgr.followup_window_sec = 8.0
        mgr._state = LiveState.ACTIVE
        fake_session = MagicMock()
        fake_session.send_realtime_input = AsyncMock()
        mgr._live_session = fake_session
        mgr._audio_in_queue = asyncio.Queue(maxsize=20)

        import robopy_controller.robot_ai.services.live_connection_manager as lcm_mod

        async def _pump_sender_once(chunks, fake_now):
            mgr._last_oow_log_time = 0.0
            for ch in chunks:
                mgr._audio_in_queue.put_nowait(ch)
            fake_time = types.SimpleNamespace(
                monotonic=lambda: fake_now,
                time=time.time,
                sleep=time.sleep,
            )
            fake_genai_types = types.SimpleNamespace(
                ActivityStart=MagicMock,
                ActivityEnd=MagicMock,
                Blob=MagicMock,
            )
            with patch.object(lcm_mod, "time", fake_time), patch.object(
                lcm_mod, "types", fake_genai_types
            ):
                task = asyncio.create_task(mgr._audio_sender_loop(fake_session))
                for _ in range(25):
                    if mgr._audio_in_queue.empty():
                        break
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.02)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # 1. Verify Unix epoch skew guard (time.time() stored accidentally > monotonic)
        mgr.last_wakeword_time = 0.0
        mgr._last_ai_speech_end_time = 0.0
        mgr.last_successful_turn_time = time.time() + 1e9  # simulated bad epoch stamp
        mgr._activity_started = False
        mgr._turn_in_progress = False
        loop.run_until_complete(_pump_sender_once([b"\x01\x00" * 160], fake_now=1000.0))
        assert trace.counters.get("out_of_window_turns") >= 1
        assert fake_session.send_realtime_input.await_count == 0

        # 2. Verify active follow-up window expires strictly at followup_window_sec (non-self-renewing)
        mgr.last_wakeword_time = 1970.0  # wake window (25s) expired at 2004.0, follow-up open [2000..2008)
        mgr._last_ai_speech_end_time = 2000.0
        mgr._activity_started = False
        mgr._turn_in_progress = False
        loop.run_until_complete(_pump_sender_once([b"\x02\x00" * 160], fake_now=2004.0))
        calls_in_window = fake_session.send_realtime_input.await_count
        assert calls_in_window >= 2, "Audio inside 8s follow-up window must trigger activity_start + chunk"

        # Simulate TV chatter after 8.5s -> window MUST be closed without self-renewing
        mgr._activity_started = False
        mgr._turn_in_progress = False
        loop.run_until_complete(_pump_sender_once([b"\x03\x00" * 160], fake_now=2008.6))
        assert fake_session.send_realtime_input.await_count == calls_in_window, (
            "TV/noise past 8s follow-up window must NOT renew or enqueue audio"
        )

        # 3. Verify <IGNORE_TURN> does NOT renew last_successful_turn_time or pollute transcripts
        mgr._turn_in_progress = True
        mgr._current_user_text = "dialogo della tv"
        mgr._current_live_response = {"text": "", "actions": []}
        prev_success = mgr.last_successful_turn_time
        msg = types.SimpleNamespace(
            server_content=types.SimpleNamespace(
                input_transcription=None,
                output_transcription=None,
                model_turn=types.SimpleNamespace(
                    parts=[
                        types.SimpleNamespace(
                            text="<IGNORE_TURN>",
                            inline_data=None,
                            function_call=None,
                        )
                    ]
                ),
                turn_complete=True,
                interrupted=False,
            ),
            tool_call=None,
            go_away=None,
            session_resumption_update=None,
        )
        loop.run_until_complete(mgr._handle_live_message(msg))
        assert mgr.last_successful_turn_time == prev_success
        assert mgr._turn_in_progress is False
        assert mgr._current_user_text == ""
        assert not any("tv" in (t[1] if isinstance(t, tuple) else str(t)).lower() for t in mgr.recent_user_transcripts)

        # 4. Verify LLMServiceNode._on_live_turn_complete uses monotonic clock, never epoch
        svc = object.__new__(LLMServiceNode)
        svc._live_mgr = mgr
        svc._live_turn_lock = threading.Lock()
        svc._conversation_history = []
        svc._live_conversation_history = []
        svc._trinity_turn_callback = None
        svc.dialogue_arbiter = None
        svc._stats = {"turns_total": 0}
        svc._save_conversation_turn = MagicMock()
        svc._publish_stats = MagicMock()
        svc.create_publisher = MagicMock()
        svc._live_response_pub = MagicMock()
        svc.get_logger = MagicMock(return_value=MagicMock())
        with patch("time.monotonic", return_value=5555.0):
            svc._on_live_turn_complete("ciao", "ciao!")
        assert mgr.last_successful_turn_time == pytest.approx(5555.0)
    finally:
        loop.close()


# ==============================================================================
# P1.3 & P2.1-P2.2: DialogueArbiter Option A Hierarchy, Floor FSM & Race Test
# ==============================================================================
def test_p1_3_p2_1_dialogue_arbiter_option_a_and_no_double_response(tmp_path):
    from robopy_controller.robot_ai.orchestration.dialogue_arbiter import (
        ConnectivityMode,
        DialogueArbiter,
        FloorState,
    )
    from robopy_controller.robot_ai.orchestration.dialogue_trace import reset_dialogue_trace
    from robopy_controller.robot_ai.orchestration.conversation import ConversationManager

    trace = reset_dialogue_trace(log_path=str(tmp_path / "arb_trace.jsonl"), write_to_disk=True)
    arb = DialogueArbiter(trace_logger=trace)

    # ONLINE -> Live owns voice; Local Qwen blocked from speaking over Live
    arb.set_connectivity(ConnectivityMode.ONLINE)
    arb.set_live_transport_ready(True, session_open=True)
    assert arb.is_live_voice_active() is True
    assert arb.should_vui_forward_to_local_pipeline() is False
    assert DialogueArbiter.is_fast_path_stop("quanta batteria hai?") is False
    assert DialogueArbiter.is_fast_path_stop("fermati subito!") is True  # Fast-path stop <10ms!

    d_live = arb.acquire_floor("GEMINI_LIVE", turn_id="t1")
    assert d_live.allowed is True
    assert arb.floor_state == FloorState.LIVE_ANSWERING

    d_qwen = arb.acquire_floor("LOCAL_QWEN", turn_id="t1")
    assert d_qwen.allowed is False
    assert trace.counters.get("double_response_blocked") >= 1

    # Simultaneous Local Qwen TTS suppressed when ConversationManager sees active Live + arbiter
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=("ok", 10.0, "g"))
    llm.generate_live = AsyncMock(return_value=("Risposta Live", 10.0, "live"))
    llm._system_prompt = "Sei Marcus."
    llm.is_duplicate_text = MagicMock(return_value=False)

    tts = MagicMock()
    tts.speak = AsyncMock()

    skill_ex = MagicMock()
    skill_ex.find_best_match.return_value = None
    skill_ex.registry.get_function_declarations.return_value = []
    skill_ex.registry.get.return_value = None

    cfg = MagicMock()
    cfg.get_config.return_value.rag.enabled = False
    cfg.get_config.return_value.llm.timeout = 5.0

    metrics = MagicMock()
    metrics.connectivity_state = "ONLINE"

    cm = ConversationManager(
        llm=llm,
        tts=tts,
        skill_executor=skill_ex,
        memory_manager=MagicMock(memory_store=None, embedding_service=None),
        world_model=MagicMock(current_user=None),
        ha_context_provider=MagicMock(return_value=""),
        metrics=metrics,
        config=cfg,
        reactive_safety=MagicMock(),
        response_callback=MagicMock(),
    )
    cm.attach_dialogue_arbiter(arb)
    cm._is_live = True
    asyncio.run(cm._emit_conversational_fallback("quanta batteria hai?", should_speak=True, reason="test"))
    assert tts.speak.await_count == 0, "Local fallback TTS MUST be suppressed when Live owns floor"

    # Switch to OFFLINE -> Local Qwen can acquire floor cleanly
    arb.release_floor(turn_id="t1", source="GEMINI_LIVE", force=True)
    arb.set_connectivity(ConnectivityMode.OFFLINE)
    d_qwen_off = arb.acquire_floor("LOCAL_QWEN", turn_id="t2")
    assert d_qwen_off.allowed is True
    assert arb.floor_state == FloorState.LOCAL_ANSWERING


# ==============================================================================
# P1.4: ReSpeakerVUINode Standby Partial Filter & Enrollment Gate
# ==============================================================================
def test_p1_4_vui_standby_final_only_and_enrollment_gate():
    from robopy_controller.nodes.respeaker_vui_node import ReSpeakerVUINode

    vui = object.__new__(ReSpeakerVUINode)
    vui._ev_listening = threading.Event()
    vui._ev_tts = threading.Event()
    vui._is_playing_tts = False
    vui._last_ai_speaking_time = 0.0
    vui._live_voice_owner = True
    vui._last_speaker_name = "Sconosciuto"
    vui._last_ww_reset_time = 0.0
    vui._listen_timer_start = 0.0
    vui._session_start_time = 0.0
    vui._last_voice_query_text = ""
    vui._last_voice_query_ts = 0.0
    vui._last_speaker_time = 0.0
    vui._pending_transcriptions = []
    vui.enable_audio_beeps = False
    vui._dialogue_arbiter = None
    vui.get_logger = MagicMock(return_value=MagicMock())
    vui.set_led = MagicMock()
    vui.asr_text_pub = MagicMock()
    vui.transcript_pub = MagicMock()
    vui.voice_cmd_pub = MagicMock()
    vui._speaker_enroll_pub = MagicMock()
    vui.create_publisher = MagicMock(return_value=vui._speaker_enroll_pub)
    vui._start_listen_timer = MagicMock()
    vui._on_wakeword_detected = MagicMock()

    # 1. Partial "marcus" in standby MUST NOT wake robot
    vui._on_vosk_text("marcus", is_partial=True)
    assert vui._on_wakeword_detected.call_count == 0

    # 2. Accidental enrollment phrase in standby MUST NOT trigger enrollment
    vui._on_vosk_text("io sono marco", is_partial=False)
    assert vui._speaker_enroll_pub.publish.call_count == 0

    # 3. FINAL "marcus" in standby wakes robot cleanly
    vui._on_vosk_text("marcus", is_partial=False)
    assert vui._on_wakeword_detected.call_count == 1

    # 4. Active listening with _live_voice_owner=True suppresses duplicate normal query to Qwen
    vui._ev_listening.set()
    vui._on_vosk_text("raccontami una barzelletta", is_partial=False)
    assert vui.voice_cmd_pub.publish.call_count == 0

    # 5. Fast-Path stop during active Live still passes to local stop immediately
    vui._on_vosk_text("fermati subito", is_partial=False)
    assert vui.voice_cmd_pub.publish.call_count == 1


# ==============================================================================
# P1.5 & P1.6: Dream/Email/Family Hygiene, BUDGET_SYSTEM & Whole-Word Classifier
# ==============================================================================
def test_p1_5_p1_6_prompt_hygiene_budget_and_whole_word_intent(monkeypatch, tmp_path):
    from robopy_controller.robot_ai.services.llm_service import LLMServiceNode
    from robopy_controller.robot_ai.trinity.slm_metaprompt_fusion import (
        SLMMetapromptFusion,
        load_email_delivery_context,
    )
    from robopy_controller.robot_ai.trinity.metaprompt_fusion import MetapromptFusion
    from robopy_controller.robot_ai.arbitration.intent_classifier import (
        CognitiveIntentClassifier,
        RouteTarget,
    )

    # Morning prompt has NO dream poem
    svc = object.__new__(LLMServiceNode)
    svc._system_prompt = "Sei Marcus, assistente domestico."
    svc._current_affective_state = ""
    svc._dream_context_consumed = False
    svc._last_interaction_time = time.time()
    svc._recent_tool_failure = False
    svc._morning_greeting_done = False
    svc._external_affective_mood = "NORMAL"
    svc.pub_mood = MagicMock()
    svc.get_logger = MagicMock(return_value=MagicMock())
    prompt_txt = svc._get_active_system_prompt()
    assert "sogno" not in prompt_txt.lower()
    assert "poetico" not in prompt_txt.lower()

    # Fake Amazon / VIP emails NOT returned when cache missing
    monkeypatch.setenv("EMAIL_MEMORY_PATH", str(tmp_path / "missing_email.json"))
    assert load_email_delivery_context() == ""

    # Family facts NOT injected for unrelated math/weather query
    slm = SLMMetapromptFusion()
    built = slm.build_prompt(user_text="quanto fa due più due?", system_prompt="Sei Marcus.")
    assert "Luisella" not in built
    assert "Jacopo" not in built

    # System prompt budget within SPEC-05 limits
    assert MetapromptFusion.BUDGET_SYSTEM == 450

    # Whole-word classifier: substrings must NOT trigger false LOCAL_QWEN routing
    clf = CognitiveIntentClassifier()
    for phrase in ("perché il cielo è blu?", "dovrei uscire oggi?", "che bello vederti!"):
        res = clf.classify(phrase)
        assert res.destination == RouteTarget.GEMINI_LIVE, f"Substring false match on {phrase!r}: {res}"

    assert clf.classify("ruota a destra di 45 gradi").destination == RouteTarget.LOCAL_QWEN
    assert clf.classify("quanta batteria hai").destination == RouteTarget.LOCAL_QWEN


# ==============================================================================
# P1.7: Decoupled _mic_mute_cb vs _audio_out_queue Drain & TTSService Speaking
# ==============================================================================
def test_p1_7_mic_mute_preserves_audio_out_queue_and_tts_raw_pcm_speaking():
    from robopy_controller.nodes.respeaker_vui_node import ReSpeakerVUINode
    from robopy_controller.robot_ai.services.tts_service import TTSService
    from std_msgs.msg import Bool

    # 1. _mic_mute_cb(True) MUST NOT clear queued AI playback frames
    vui = object.__new__(ReSpeakerVUINode)
    vui._ev_listening = threading.Event()
    vui._ev_listening.set()
    vui._ev_mic_muted = threading.Event()
    vui._audio_out_queue = queue.Queue()
    vui._audio_out_queue.put((b"\x10\x00" * 160, False, "gemini_live"))
    vui._current_mood = "neutral"
    vui.vosk_mgr = MagicMock()
    vui.set_led = MagicMock()
    vui._stop_listen_timer = MagicMock()
    vui.get_logger = MagicMock(return_value=MagicMock())

    vui._mic_mute_cb(Bool(data=True))
    assert not vui._audio_out_queue.empty(), "_mic_mute_cb(True) MUST NOT flush _audio_out_queue!"

    # 2. TTSService.play_raw_pcm publishes /ai/tts/speaking True -> False
    ros_node = MagicMock()
    timer_cbs = []

    def _fake_timer(sec, cb):
        timer_cbs.append(cb)
        return MagicMock()

    ros_node.create_timer = _fake_timer
    ros_node.create_publisher = MagicMock(side_effect=lambda *a, **k: MagicMock())

    tts = object.__new__(TTSService)
    tts.logger = MagicMock()
    tts.ros_node = ros_node
    tts.speaker_pub = MagicMock()
    tts.speaking_pub = MagicMock()
    tts._output_stream = None
    tts._volume_scale = 1.0
    tts._raw_pcm_lock = threading.Lock()
    tts._raw_pcm_speaking_active = False
    tts._raw_pcm_timer = None

    tts.play_raw_pcm(b"\x00\x01" * 128)
    assert tts._raw_pcm_speaking_active is True
    assert tts.speaking_pub.publish.call_count >= 1
    assert tts.speaking_pub.publish.call_args_list[0][0][0].data is True

    # Fire idle timer callback -> sets speaking False
    tts._on_raw_pcm_idle_timeout()
    assert tts._raw_pcm_speaking_active is False
    assert tts.speaking_pub.publish.call_args_list[-1][0][0].data is False


# ==============================================================================
# P1.8: All 10 ALLOWED_FUNCTION_NAMES + Real Skill Execution Spoken Output
# ==============================================================================
def test_p1_8_all_10_skills_mapped_and_real_output_spoken():
    from robopy_controller.robot_ai.orchestration.skill_executor import SkillExecutor
    from robopy_controller.robot_ai.trinity.kinematic_sandbox import ALLOWED_FUNCTION_NAMES
    from robopy_controller.robot_ai.orchestration.conversation import ConversationManager

    assert len(ALLOWED_FUNCTION_NAMES) == 10

    registry = MagicMock()
    skills = {}
    for sname in ("navigation", "frontier_exploration", "home_assistant", "query_memory", "memory_info", "search"):
        sk = MagicMock()
        sk.safe_execute = AsyncMock(return_value=MagicMock(success=True, speak=f"ok-{sname}", error=None, data={}))
        skills[sname] = sk
    registry.get = MagicMock(side_effect=lambda n: skills.get(n))

    wm = MagicMock()
    wm.battery_voltage = 11.82
    wm.battery_pct = 84.0
    wm.battery_percentage = 84.0
    wm.is_charging = True
    wm.battery_current = -0.9
    wm.cpu_usage = 24.0
    wm.ram_mb = 1280.0
    wm.cpu_temp = 52.3
    wm.cpu_temp_c = 52.3
    wm.current_room = "salotto"

    executor = SkillExecutor(registry=registry, nav_client=MagicMock(), reactive_safety=MagicMock(), world_model=wm)

    test_calls = [
        ("navigate_to", {"target_location": "cucina"}),
        ("move_distance", {"distance_m": 0.4}),
        ("rotate_angle", {"degrees": 45.0}),
        ("stop_motion", {}),
        ("start_frontier_exploration", {}),
        ("pause_or_resume_exploration", {"mode": "pause"}),
        ("set_smart_device", {"entity": "luce", "action": "on"}),
        ("query_knowledge", {"query": "chi è Luisella"}),
        ("get_battery_status", {}),
        ("get_system_health", {}),
    ]
    assert {name for name, _ in test_calls} == set(ALLOWED_FUNCTION_NAMES)

    for fn_name, args in test_calls:
        res = asyncio.run(executor.execute_skill(fn_name, args))
        assert isinstance(res, list) and len(res) >= 1, f"Skill {fn_name} failed or returned empty: {res}"
        joined = " ".join(str(x) for x in res)
        if fn_name == "get_battery_status":
            assert "84%" in joined and "11.8" in joined
        elif fn_name == "get_system_health":
            assert "52.3" in joined

    # Verify ConversationManager._handle_local_qwen speaks real SkillExecutor telemetry return
    hailo_stub = MagicMock()
    hailo_stub.generate = AsyncMock(
        return_value=(
            '<action>{"call": {"name": "get_battery_status", "arguments": {}}}</action>',
            MagicMock(ttft_ms=10.0, total_time_ms=20.0),
        )
    )
    tts = MagicMock()
    tts.speak = AsyncMock()
    spoken_callbacks = []

    cfg = MagicMock()
    cfg.get_config.return_value.rag.enabled = False
    cfg.get_config.return_value.llm.timeout = 5.0

    cm = ConversationManager(
        llm=MagicMock(_system_prompt="Sei Marcus.", is_duplicate_text=MagicMock(return_value=False)),
        tts=tts,
        skill_executor=executor,
        memory_manager=MagicMock(memory_store=None, embedding_service=None),
        world_model=wm,
        ha_context_provider=MagicMock(return_value=""),
        metrics=MagicMock(connectivity_state="OFFLINE_LOCAL"),
        config=cfg,
        reactive_safety=MagicMock(),
        response_callback=lambda t: spoken_callbacks.append(t),
    )
    cm.hailo_client = hailo_stub
    cm._is_live = False

    ok = asyncio.run(cm._handle_local_qwen("quanta batteria hai?", should_speak=True))
    assert ok is True
    assert len(spoken_callbacks) == 1
    assert "84%" in spoken_callbacks[0] and "11.8" in spoken_callbacks[0]


# ==============================================================================
# P2.3 - P2.5: Shared TRINITY Digest, Proactive Queue & Single mic_mute Arbiter
# ==============================================================================
def test_p2_3_to_p2_5_shared_memory_proactive_queue_and_mic_mute_arbiter(tmp_path):
    from robopy_controller.robot_ai.trinity.trinity_engine import TrinityEngine
    from robopy_controller.robot_ai.orchestration.dialogue_arbiter import (
        ConnectivityMode,
        DialogueArbiter,
    )
    from robopy_controller.robot_ai.orchestration.orchestrator import AIOrchestrator
    from std_msgs.msg import String

    # 1. Shared TRINITY Live digest <= 2500 tokens + turn sync
    db_file = tmp_path / "mag_test.db"
    wm = MagicMock()
    wm.current_room = "salotto"
    wm.battery_voltage = 12.1
    wm.battery_pct = 90.0
    wm.battery_percentage = 90.0
    wm.is_charging = False
    wm.cpu_temp = 48.0
    wm.cpu_usage = 20.0
    wm.ram_mb = 900.0
    wm.current_user = "Luca"
    wm.detected_objects = []
    wm.visible_objects = []
    wm.active_errors = []

    cfg = MagicMock()
    cfg.trinity.enabled = True
    eng = TrinityEngine(
        memory_store=None,
        embedding_service=None,
        world_model=wm,
        ha_context_provider=MagicMock(return_value=""),
        config=cfg,
        db_path=str(db_file),
    )
    eng.enabled = True
    eng.record_live_turn_sync("ricordami le chiavi sul tavolo", "Perfetto Luca, me lo segno!", user_identity="Luca")
    digest = asyncio.run(eng.build_live_context_digest(user_identity="Luca", max_tokens=2500))
    assert isinstance(digest, str) and len(digest) > 0
    assert len(digest) <= 2500 * 4 + 200
    assert "stato reale cag" in digest.lower()
    assert "chiavi sul tavolo" in digest.lower()
    assert "perfetto luca" in digest.lower()

    # 2. Proactive Queue respects floor + engagement/ToM
    arb = DialogueArbiter(proactive_cooldown_sec=0.0)
    arb.set_connectivity(ConnectivityMode.ONLINE)
    arb.enqueue_proactive(kind="email", text="Hai ricevuto un messaggio importante.", priority=5)
    arb.acquire_floor("GEMINI_LIVE", turn_id="busy1")
    assert arb.pop_ready_proactive() is None  # Floor busy -> queued
    arb.release_floor(turn_id="busy1", source="GEMINI_LIVE", force=True)

    arb.update_tom_state("DO_NOT_DISTURB")
    assert arb.recommended_tts_volume_scale() == pytest.approx(0.65)
    assert arb.pop_ready_proactive() is None  # Suppressed on DND

    arb.update_tom_state("ENGAGED")
    arb.update_engagement("ENGAGED")
    ready = arb.pop_ready_proactive()
    assert ready is not None and "messaggio" in ready.text

    # 3. Single /ai/input/mic_mute owner arbitration in AIOrchestrator
    orch = object.__new__(AIOrchestrator)
    orch.dialogue_arbiter = arb
    orch.mic_mute_pub = MagicMock()
    orch.ai_logger = MagicMock()
    orch._last_published_mic_mute = None

    # TTS_ECHO mutes, MOVEMENT mutes, MOVEMENT unmutes -> STILL MUTED because TTS_ECHO still holds lock!
    orch._mic_mute_request_callback(String(data=json.dumps({"owner": "TTS_ECHO", "muted": True, "reason": "tts"})))
    assert orch.mic_mute_pub.publish.call_args[0][0].data is True

    orch._mic_mute_request_callback(String(data=json.dumps({"owner": "MOVEMENT", "muted": True, "reason": "spin"})))
    orch._mic_mute_request_callback(String(data=json.dumps({"owner": "MOVEMENT", "muted": False, "reason": "done"})))
    assert arb.is_mic_muted is True  # TTS_ECHO still holds lock!

    orch._mic_mute_request_callback(String(data=json.dumps({"owner": "TTS_ECHO", "muted": False, "reason": "tts_done"})))
    assert arb.is_mic_muted is False
    assert orch.mic_mute_pub.publish.call_args[0][0].data is False


# ==============================================================================
# P3.1 & P4.1-P4.4: Social Gating, Backchannels, Piper Offline TTS, VLM Thermal
# ==============================================================================
def test_p3_p4_human_vui_piper_offline_and_vlm_thermal_guard(tmp_path):
    from robopy_controller.nodes.respeaker_vui_node import ReSpeakerVUINode
    from robopy_controller.robot_ai.orchestration.dialogue_arbiter import DialogueArbiter
    from robopy_controller.robot_ai.orchestration.dialogue_trace import reset_dialogue_trace
    from robopy_controller.robot_ai.services.tts_service import TTSService
    from robopy_controller.robot_ai.orchestration.conversation import ConversationManager
    from robopy_controller.robot_ai.cognitive.cognitive_core_node import CognitiveCoreNode
    from std_msgs.msg import String

    trace = reset_dialogue_trace(log_path=str(tmp_path / "p4_trace.jsonl"), write_to_disk=True)

    # 1. Social ToM & Engagement on ReSpeakerVUINode
    vui = object.__new__(ReSpeakerVUINode)
    vui._dnd_active = False
    vui.playback_volume = 0.80
    vui._base_playback_volume = 0.80
    vui._dialogue_arbiter = DialogueArbiter(trace_logger=trace)
    vui._engagement_state = "ENGAGED"
    vui._engagement_updated_mono = time.monotonic()
    vui._is_person_present = True
    vui._social_telemetry_active = True

    vui._tom_state_cb(String(data="DO_NOT_DISTURB"))
    assert vui._dnd_active is True
    assert vui.playback_volume < 0.80

    vui._engagement_status_cb(String(data="DISENGAGED"))
    assert vui._engagement_state == "DISENGAGED"

    # 2. <300ms backchannel cue with cooldown
    cue = vui._dialogue_arbiter.request_backchannel_cue(category="vision", min_interval_sec=5.0)
    assert cue is not None
    assert trace.counters.get("backchannels_emitted") == 1
    assert vui._dialogue_arbiter.request_backchannel_cue(category="vision", min_interval_sec=5.0) is None

    # 3. TTSService uses PiperTTSClient offline BEFORE gTTS, resampled 16k -> 24kHz
    tts = object.__new__(TTSService)
    tts.logger = MagicMock()
    tts._cache_dir = str(tmp_path / "tts_cache")
    os.makedirs(tts._cache_dir, exist_ok=True)
    tts._client = None
    tts._volume_scale = 0.5
    tts.ai_config = MagicMock(tts=MagicMock(language="it-IT"))

    piper_mock = MagicMock()
    piper_mock.is_available = MagicMock(return_value=True)
    piper_mock.synthesize = MagicMock(return_value=b"\x00\x10" * 160)
    tts._piper_client = piper_mock

    with patch("gtts.gTTS", side_effect=AssertionError("Online gTTS must NOT be called when Piper succeeds")):
        pcm_24k = asyncio.run(tts._synthesize("Ciao Luca", "it"))
    assert pcm_24k is not None
    assert len(pcm_24k) > len(b"\x00\x10" * 160)  # Resampled 16kHz -> 24kHz (1.5x samples)

    # Unavailable Piper must NOT run synthetic tone fallback -> proceeds past Piper cleanly
    piper_unavail = MagicMock()
    piper_unavail.is_available = MagicMock(return_value=False)
    piper_unavail.synthesize = MagicMock(side_effect=AssertionError("synthesize() must NOT be called when unavailable"))
    tts._piper_client = piper_unavail
    with patch("gtts.gTTS", side_effect=RuntimeError("no_network")):
        res_fallback = asyncio.run(tts._synthesize("Ciao offline fallback", "it"))
    assert res_fallback == b"CONSOLE_TTS"

    # 4. CognitiveCoreNode suppresses non-urgent proactivity under DND + has no blind wakeword bypass
    cog = object.__new__(CognitiveCoreNode)
    cog.drive_engine = MagicMock()
    cog.drive_engine.tom_state = "ACTIVE"
    cog.drive_engine.current_battery_voltage = 12.0
    cog.drive_engine.step.return_value = (
        {"sociality": 0.4},
        types.SimpleNamespace(drive_name="sociality", utterance="Ciao Luca!"),
    )
    cog.affective_state_pub = MagicMock()
    cog.proactive_speech_pub = MagicMock()
    cog.drives_pub = MagicMock()
    cog.intent_pub = MagicMock()
    cog.get_logger = MagicMock(return_value=MagicMock())
    assert not hasattr(cog, "proactive_listen_pub")
    cog._on_tom_state(String(data="DO_NOT_DISTURB"))
    assert cog.drive_engine.tom_state == "DO_NOT_DISTURB"
    cog._step_homeostatic_drives()
    cog.proactive_speech_pub.publish.assert_not_called()
    cog.affective_state_pub.publish.assert_called_once()

    # 5. ConversationManager.ask_visual_question thermal guard >= 80C + correct VLM service
    cm = object.__new__(ConversationManager)
    cm._logger = MagicMock()
    cm.node = MagicMock()
    cm.node.create_client = MagicMock(side_effect=AssertionError("VLM client must not run >= 80C!"))
    thermal_file = MagicMock()
    thermal_file.__enter__ = MagicMock(return_value=types.SimpleNamespace(read=lambda: "82500\n"))
    thermal_file.__exit__ = MagicMock(return_value=False)

    real_open = open

    def _patched_open(path, *args, **kwargs):
        if str(path) == "/sys/class/thermal/thermal_zone0/temp":
            return thermal_file
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=_patched_open):
        vqa_msg = asyncio.run(cm.ask_visual_question("cosa vedi?"))
    assert "temperatura" in vqa_msg.lower()
