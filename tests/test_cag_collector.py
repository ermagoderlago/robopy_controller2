"""
Unit test suite for TRINITY CAG HardwareStateCollector & Telemetry Grounding.
Validates Milestone 2 requirements:
1. Ingestion of live /battery_state (voltage, percentage, current, power_supply_status, is_charging).
2. Discharge autonomy estimation in hours and minutes across charging and discharging states.
3. Host system metrics: RAM used in MB and % out of 4GB (Pi 5), Swap in MB, NVMe SSD (/mnt/ssd), MicroSD (/), CPU temp, and uptime.
4. Nightly Dream status reporting (timestamp and natural summary).
5. Unambiguous, positive natural Italian formatting in to_text() adhering to PROJECT.md § Interface Contracts.
6. Integration with TrinityEngine subscription and prompt augmentation.
"""

import os
import sys
import time
import tempfile
import asyncio
from unittest.mock import MagicMock, patch
import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROBOPY_DIR = os.path.join(_PROJECT_ROOT, "robopy_controller")
for p in [_PROJECT_ROOT, _ROBOPY_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from robot_ai.trinity.cag_hardware_collector import HardwareStateCollector
from robot_ai.trinity.trinity_engine import TrinityEngine


# ==============================================================================
# 1. Battery Telemetry & State Ingestion Tests
# ==============================================================================

def test_hardware_collector_initialization_defaults():
    """Collector starts with safe nominal battery state and host metrics."""
    hw = HardwareStateCollector()
    assert hw.battery_voltage >= 12.0
    assert hw.battery_percentage == 100.0
    assert hw.battery_capacity == 6.80
    assert hw.is_charging is False


def test_battery_ingestion_ratio_and_percentage():
    """Handles both ROS 2 ratio [0.0, 1.0] and percentage [0.0, 100.0]."""
    hw = HardwareStateCollector()

    # Ratio from ROS 2 BatteryState (e.g. 0.85 -> 85.0%)
    hw.update_battery(voltage=12.15, percentage=0.85)
    assert abs(hw.battery_percentage - 85.0) < 0.01
    assert abs(hw.battery_voltage - 12.15) < 0.01

    # Percentage direct (e.g. 75.0%)
    hw.update_battery(voltage=11.90, percentage=75.0)
    assert abs(hw.battery_percentage - 75.0) < 0.01


def test_battery_charging_state_detection():
    """Detects charging vs discharging state via voltage (SPEC-06 >= 12.70V) and status flags."""
    hw = HardwareStateCollector()

    # 1. Discharging on battery (V = 12.61V < 12.70V)
    hw.update_battery(voltage=12.61, percentage=100.0, power_supply_status=2)
    assert hw.is_charging is False
    assert hw.power_supply_status == 2

    # 2. Charging on external power (V = 12.80V >= 12.70V)
    hw.update_battery(voltage=12.80, percentage=100.0)
    assert hw.is_charging is True
    assert hw.power_supply_status == 1

    # 3. Explicit is_charging flag override
    hw.update_battery(voltage=12.20, percentage=90.0, is_charging=True)
    assert hw.is_charging is True


def test_battery_state_dict_and_ros_msg_ingestion():
    """Ingests battery state from dictionary or object with attributes."""
    hw = HardwareStateCollector()

    # Dictionary input
    hw.update_battery_state({
        "voltage": 12.45,
        "percentage": 0.92,
        "current": 1.15,
        "power_supply_status": 2
    })
    assert abs(hw.battery_voltage - 12.45) < 0.01
    assert abs(hw.battery_percentage - 92.0) < 0.01
    assert abs(hw.battery_current - 1.15) < 0.01
    assert hw.is_charging is False

    # Object with attributes (mocking sensor_msgs/msg/BatteryState)
    class MockBatteryState:
        voltage = 12.75
        percentage = 1.0
        current = -1.50
        power_supply_status = 1
        capacity = 6.80

    hw.update_battery_state(MockBatteryState())
    assert abs(hw.battery_voltage - 12.75) < 0.01
    assert hw.battery_percentage == 100.0
    assert hw.is_charging is True


# ==============================================================================
# 2. Autonomy Estimation Tests
# ==============================================================================

def test_autonomy_estimation_full_battery():
    """At 12.61V (100%), remaining discharge autonomy is ~4.5 hours above docking cliff."""
    hw = HardwareStateCollector()
    hw.update_battery(voltage=12.61, percentage=100.0, is_charging=False)

    autonomy = hw.calculate_autonomy()
    assert autonomy["is_charging"] is False
    assert autonomy["hours"] >= 4
    assert "ore" in autonomy["text"]
    assert "~4.5h" in autonomy["text"] or "~4." in autonomy["text"]


def test_autonomy_estimation_charging():
    """When connected to external power, autonomy is unlimited/charging."""
    hw = HardwareStateCollector()
    hw.update_battery(voltage=12.80, percentage=100.0, is_charging=True)

    autonomy = hw.calculate_autonomy()
    assert autonomy["is_charging"] is True
    assert "in carica" in autonomy["text"] or "illimitata" in autonomy["text"]


def test_autonomy_estimation_near_docking():
    """Near docking threshold (9.90V), autonomy is low (< 30 minutes)."""
    hw = HardwareStateCollector()
    hw.update_battery(voltage=9.95, percentage=8.0, is_charging=False)

    autonomy = hw.calculate_autonomy()
    assert autonomy["hours"] == 0
    assert autonomy["minutes"] <= 30


# ==============================================================================
# 3. Host System Metrics Collection Tests
# ==============================================================================

def test_ram_metrics_collection():
    """RAM metrics report total MB (out of 4GB / 4096MB), used MB, and percentage."""
    hw = HardwareStateCollector()
    ram = hw._get_ram_info()

    assert ram["total_mb"] > 0
    assert ram["used_mb"] > 0
    assert 0.0 <= ram["percent"] <= 100.0
    assert ram["total_mb"] >= 3000
    assert ram["used_mb"] <= ram["total_mb"]


def test_swap_metrics_collection():
    """Swap metrics report used MB."""
    hw = HardwareStateCollector()
    swap = hw._get_swap_info()

    assert "used_mb" in swap
    assert "total_mb" in swap
    assert swap["used_mb"] >= 0


def test_disk_metrics_nvme_and_root():
    """Storage metrics include NVMe SSD (/mnt/ssd) and MicroSD root (/)."""
    hw = HardwareStateCollector()
    disks = hw._get_disk_info()

    assert "root" in disks
    assert "ssd" in disks

    ssd = disks["ssd"]
    assert ssd["total_gb"] > 0
    assert ssd["used_gb"] > 0
    assert ssd["free_gb"] > 0
    assert 0.0 <= ssd["percent"] <= 100.0

    root = disks["root"]
    assert root["total_gb"] > 0
    assert root["used_gb"] > 0


def test_uptime_formatting():
    """Uptime returns non-negative seconds and formatted Italian string."""
    hw = HardwareStateCollector()
    uptime = hw._get_uptime_info()

    assert uptime["uptime_seconds"] >= 0
    assert len(uptime["uptime_str"]) > 0
    assert any(w in uptime["uptime_str"] for w in ["minuti", "ore", "giorni", "s"])


def test_nightly_dream_status_parsing():
    """Parses last Nightly Dream run and produces natural summary."""
    hw = HardwareStateCollector()
    dream = hw._get_nightly_dream_status()

    assert "timestamp" in dream
    assert "summary" in dream
    assert len(dream["timestamp"]) >= 10
    assert len(dream["summary"]) > 10


# ==============================================================================
# 4. to_text() Prompt Formatting & Natural Italian Assertions
# ==============================================================================

def test_to_text_conforms_to_interface_contracts():
    """to_text() must strictly match PROJECT.md § Interface Contracts."""
    hw = HardwareStateCollector()
    hw.update_battery(voltage=12.61, percentage=100.0, is_charging=False)

    text = hw.to_text()

    # Section header
    assert "[TELEMETRIA DI BORDO]" in text

    # Line 1: Battery
    assert "- Batteria: 12.61V (100%), Stato: non in carica / alimentazione a batteria" in text
    assert "Autonomia stimata:" in text
    # Backwards compatibility tag for existing test suites
    assert "[Batt: 100.0%]" in text

    # Line 2: Pi 5 host resources
    assert "- Risorse Pi 5: RAM usata" in text
    assert "Swap:" in text
    assert "CPU:" in text
    assert "Uptime:" in text

    # Line 3: Disks
    assert "- Dischi: SSD NVMe (/mnt/ssd)" in text
    assert "MicroSD (/)" in text

    # Line 4: Nightly Dream
    assert "- Nightly Dream: Ultimo completato" in text


def test_to_text_charging_status_clarity():
    """When charging, to_text() explicitly states 'in carica' to prevent LLM hallucination."""
    hw = HardwareStateCollector()
    hw.update_battery(voltage=12.80, percentage=100.0, is_charging=True)

    text = hw.to_text()
    assert "Stato: in carica (collegato all'alimentatore di rete)" in text
    assert "Autonomia stimata: illimitata (in carica da rete)" in text


def test_to_text_token_budget_compliance():
    """to_text() length must fit cleanly within the 150 token budget for SLM."""
    hw = HardwareStateCollector()
    text = hw.to_text()

    # Approximate tokens: len(text) / 3.6
    token_estimate = len(text) / 3.6
    assert token_estimate < 160, f"Hardware text too long: {token_estimate} tokens ({len(text)} chars)"


# ==============================================================================
# 5. TrinityEngine Integration & Prompt Feeding
# ==============================================================================

def test_trinity_engine_integrates_cag_hardware():
    """TrinityEngine prompt augmentation feeds CAG telemetry into generated prompt."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".db") as f:
        tmp_db = f.name

    try:
        engine = TrinityEngine(db_path=tmp_db)

        # Update battery on engine's CAG hardware collector
        if engine.cag and hasattr(engine.cag, "hardware"):
            engine.cag.hardware.update_battery(voltage=12.61, percentage=100.0, is_charging=False)

        async def _run():
            # Prompt for battery state
            prompt = await engine.build_augmented_prompt(
                user_text="Qual è il livello della batteria?",
                target_model="qwen_local"
            )

            assert "12.61V" in prompt or "100%" in prompt or "Batteria" in prompt
            assert "Autonomia" in prompt or "autonomia" in prompt

        asyncio.run(_run())
    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        for ext in ["-wal", "-shm"]:
            if os.path.exists(tmp_db + ext):
                os.remove(tmp_db + ext)


def test_trinity_engine_ros_node_battery_callback():
    """TrinityEngine _on_battery_state updates CAG hardware state."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".db") as f:
        tmp_db = f.name

    try:
        mock_node = MagicMock()
        engine = TrinityEngine(node=mock_node, db_path=tmp_db)

        class MockBatteryMsg:
            voltage = 12.35
            percentage = 0.88
            current = 1.20
            power_supply_status = 2
            capacity = 6.80

        # Simulate ROS callback
        engine._on_battery_state(MockBatteryMsg())

        assert engine.cag is not None
        assert abs(engine.cag.hardware.battery_voltage - 12.35) < 0.01
        assert abs(engine.cag.hardware.battery_percentage - 88.0) < 0.01
        assert engine.cag.hardware.is_charging is False
    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        for ext in ["-wal", "-shm"]:
            if os.path.exists(tmp_db + ext):
                os.remove(tmp_db + ext)
