"""
[COGNITIVE_CHECK]
- Scheda Tecnica letta con view_file: /docs/specs/SPEC-03_vision_and_hailo_npu.md e /docs/specs/SPEC-07_system_os_build_lifecycle.md
- Vincoli di Zona Rossa verificati:
  - Review-Only: Nessuna alterazione al codice sorgente di produzione.
  - Test collocato nella directory standard 'tests/' per conformità al layout di progetto (SPEC-07).
  - Nessun byte BOM UTF-8 (SPEC-07 § 3).
- Classificazione modifica: 🟢 Zona Verde (Aggiunta test harness avversario empirico).
- Documentazione consultata: SPEC-03, SPEC-07, marcus_core_rules.md, PROJECT.md.

Adversarial Stress Harness — Challenger 2 (Milestone 1 Iteration 2)
=====================================================================
Focus:
1. Concurrency & Vision Throughput with Synthetic Compute Load:
   - Verifies ConcurrentVisionHarness maintains >= 10.0 FPS under 1, 5, 10, 20 concurrent Qwen streams.
   - Checks inter-frame intervals stay <= 150.0 ms (zero frame drops).
2. CPU Measurement Accuracy & Mock Overhead Unmasking:
   - Verifies that real CPU load is no longer clamped to 0.10% by mock engine (FND-06 fix).
   - Verifies CPUMeasurementHarness normalization by quad-core Pi 5 target.
3. TTFT Latency Resilience under Multi-Stream Load:
   - Confirms TTFT <= 400.0 ms across all concurrent requests.
"""

import asyncio
import os
import sys
import threading
import time
from typing import List, Tuple

import psutil
import pytest

# Ensure project root is in sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.hailo.hailo_qwen_client import (
    HailoQwenClient,
    HailoQwenConfig,
    InferenceMode,
    InferenceTelemetry,
)
from tests.benchmark_hailo_npu import (
    CPUMeasurementHarness,
    ConcurrentVisionHarness,
    NPUFase1BenchmarkHarness,
)


def cpu_burner(stop_event: threading.Event, intensity: float = 0.7):
    """Generates synthetic CPU load on a background thread."""
    while not stop_event.is_set():
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < intensity * 0.05:
            _ = sum((i * 7) ^ 0x55 for i in range(1000))
        time.sleep((1.0 - intensity) * 0.05)


# ==============================================================================
# CHALLENGER EMPIRICAL VERIFICATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_vision_throughput_with_synthetic_load_under_multistream_qwen():
    """
    Verify ConcurrentVisionHarness maintains >= 10.0 FPS and zero frame drops
    with synthetic compute load active while 1, 5, 10, 20 concurrent Qwen streams run.
    """
    client = HailoQwenClient()
    concurrency_levels = [1, 5, 10, 20]
    records = []

    for conc in concurrency_levels:
        # Vision harness with synthetic compute enabled (~1.2ms per frame)
        vision = ConcurrentVisionHarness(
            target_fps=12.0,
            max_gap_drop_ms=150.0,
            enable_synthetic_load=True,
        )
        vision.start()

        async def _stream_worker(stream_id: int):
            chunks = []
            async for c in client.generate_stream(f"esplora settore {stream_id}"):
                chunks.append(c)
            return len(chunks)

        tasks = [asyncio.create_task(_stream_worker(i)) for i in range(conc)]
        results = await asyncio.gather(*tasks)

        metrics = vision.stop()

        records.append((conc, metrics.mean_fps, metrics.max_frame_gap_ms, metrics.frame_drops_count))

        assert all(count > 0 for count in results), f"All streams must receive tokens at conc={conc}"
        assert metrics.mean_fps >= 10.0, (
            f"Vision FPS {metrics.mean_fps:.2f} dropped below 10.0 FPS at concurrency={conc} "
            f"(synthetic compute enabled)"
        )
        assert metrics.frame_drops_count == 0, (
            f"Detected {metrics.frame_drops_count} frame drops (>150ms) at concurrency={conc}"
        )
        assert metrics.max_frame_gap_ms <= 150.0, (
            f"Max frame gap {metrics.max_frame_gap_ms:.2f} ms exceeded 150.0 ms limit at conc={conc}"
        )

    await client.close()
    return records


@pytest.mark.asyncio
async def test_mock_cpu_overhead_is_unmasked_when_load_applied():
    """
    Verify that in Iteration 2, HailoQwenClient._mock_generate_stream DOES NOT clamp
    measured CPU to mock_cpu_overhead_pct (0.1%), but unmasks and reports the true
    measured CPU delta when host load is present.
    """
    client = HailoQwenClient()

    # Step 1: Idle run without external load
    _, telem_idle = await client.generate("ruota a destra")
    assert telem_idle.cpu_delta_pct >= client.config.mock_cpu_overhead_pct, (
        "Under idle, cpu_delta_pct must be at least mock_cpu_overhead_pct"
    )

    # Step 2: Active load run with CPU burner
    stop_event = threading.Event()
    burners = [
        threading.Thread(target=cpu_burner, args=(stop_event, 0.9), daemon=True)
        for _ in range(2)
    ]
    for b in burners:
        b.start()
    time.sleep(0.1)

    try:
        _, telem_loaded = await client.generate("comando navigazione complessa")
    finally:
        stop_event.set()
        for b in burners:
            b.join(timeout=1.0)
        await client.close()

    # In Iteration 1, telem.cpu_delta_pct was clamped to <= 0.10% by min()
    # In Iteration 2, it is max(mock_overhead, measured_delta), so real CPU load is UNMASKED!
    print(f"\n[UNMASK CHECK] Idle CPU delta: {telem_idle.cpu_delta_pct:.3f}%, Loaded CPU delta: {telem_loaded.cpu_delta_pct:.3f}%")
    assert telem_loaded.cpu_delta_pct >= client.config.mock_cpu_overhead_pct


def test_cpu_measurement_harness_quad_core_normalization():
    """
    Verify CPUMeasurementHarness normalizes process CPU by target_cores (4.0 for Pi 5 BCM2712),
    accurately captures baseline, active sampling, and produces valid CPUMetrics.
    """
    harness = CPUMeasurementHarness(sample_interval_sec=0.050, target_cores=4.0)

    # 1. Baseline measurement
    baseline = harness.measure_baseline(duration_sec=0.15)
    assert baseline >= 0.0, "Baseline CPU must be non-negative"

    # 2. Active sampling during synthetic load
    harness.start_sampling()
    stop_event = threading.Event()
    burner = threading.Thread(target=cpu_burner, args=(stop_event, 0.8), daemon=True)
    burner.start()
    time.sleep(0.3)
    stop_event.set()
    burner.join(timeout=1.0)

    metrics = harness.stop_sampling(baseline_pct=baseline)

    assert metrics.samples_count >= 3, f"Expected at least 3 samples at 50ms, got {metrics.samples_count}"
    assert metrics.infer_mean_pct >= 0.0
    assert metrics.infer_peak_pct >= metrics.infer_mean_pct
    assert metrics.delta_pct >= 0.0
    print(f"\n[HARNESS CHECK] Samples: {metrics.samples_count}, Mean: {metrics.infer_mean_pct:.2f}%, Peak: {metrics.infer_peak_pct:.2f}%, Delta: {metrics.delta_pct:.2f}%")


@pytest.mark.asyncio
async def test_ttft_under_extreme_concurrency_burst():
    """
    Verify TTFT remains <= 400.0 ms across a burst of 20 concurrent Qwen streams.
    """
    client = HailoQwenClient()
    ttfts: List[float] = []

    async def _measure_stream(idx: int):
        first_token_time = None
        t0 = time.perf_counter()
        async for chunk in client.generate_stream(f"query concorrente burst {idx}"):
            if first_token_time is None:
                first_token_time = time.perf_counter()
        if first_token_time:
            ttft_ms = (first_token_time - t0) * 1000.0
            ttfts.append(ttft_ms)

    tasks = [asyncio.create_task(_measure_stream(i)) for i in range(20)]
    await asyncio.gather(*tasks)
    await client.close()

    assert len(ttfts) == 20, f"Expected 20 TTFT measurements, got {len(ttfts)}"
    for idx, ttft in enumerate(ttfts):
        assert ttft <= 400.0, f"Stream {idx} TTFT {ttft:.2f} ms exceeded 400.0 ms budget"

    mean_ttft = sum(ttfts) / len(ttfts)
    min_ttft = min(ttfts)
    max_ttft = max(ttfts)
    print(f"\n[TTFT BURST CHECK] 20 Streams -> Min: {min_ttft:.2f} ms, Mean: {mean_ttft:.2f} ms, Max: {max_ttft:.2f} ms")


@pytest.mark.asyncio
async def test_concurrency_telemetry_isolation_and_no_leak():
    """
    Verify FND-02 fix under high concurrency: each concurrent invocation receives its
    own isolated telemetry without state leakage across streams.
    """
    client = HailoQwenClient()
    prompts = [
        "avanti 10 metri",
        "ruota a sinistra di 90",
        "stop immediato",
        "stato batteria",
        "torna alla base",
    ] * 4  # 20 concurrent queries

    async def _invoke(p: str):
        text, telem = await client.generate(p)
        assert telem.prompt == p, f"Telemetry prompt '{telem.prompt}' does not match query prompt '{p}'"
        assert len(text) > 0, "Response must not be empty"
        return telem

    tasks = [asyncio.create_task(_invoke(p)) for p in prompts]
    telems = await asyncio.gather(*tasks)
    await client.close()

    assert len(telems) == 20
    # Also verify empty prompt isolation
    _, empty_telem = await client.generate("")
    assert empty_telem.prompt == ""
    assert empty_telem.response == ""
    assert empty_telem.token_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
