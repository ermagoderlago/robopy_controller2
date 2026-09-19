# 🧪 TEST_INFRA: Marcus Autonomous Perception & Navigation E2E Test Infrastructure

## 1. Executive Summary & Architecture Overview
This document defines the comprehensive opaque-box End-to-End (E2E) Test Architecture for Marcus's Autonomous Perception and Navigation Stack. Derived strictly from the authoritative requirements (**R1–R5**, **TC1–TC8**), the system specifications (**SPEC-00** to **SPEC-07**), and physical hardware constraints (Raspberry Pi 5 4GB RAM, RPLIDAR C1 360° ToF, OAK-D Lite RGB-D, Hailo-10H NPU, ReSpeaker USB AEC).

The test harness provides contract-level deterministic execution across development workstations (Windows/Linux) and the target robot host, operating without brittle external hardware dependencies while enforcing physical thresholds.

```
+-----------------------------------------------------------------------------------------+
|                                    E2E Test Runner                                      |
|                      (tests/e2e/run_e2e_tests.py / pytest tests/e2e/)                   |
+-----------------------------------------------------------------------------------------+
       |                                |                             |
       v                                v                             v
+------------------+          +-------------------+         +--------------------+
| Tier 1: Feature  |          | Tier 2: Boundary  |         | Tier 3: Pairwise   |
| Coverage Suite   |          | & Corner Suite    |         | Interactions Suite |
| (TC1-TC8, R1-R5) |          | (Exact Thresholds)|         | (Cross-Subsystem)  |
+------------------+          +-------------------+         +--------------------+
       |                                |                             |
       +--------------------------------+-----------------------------+
                                        |
                                        v
                            +-----------------------+
                            | Tier 4: Real-World    |
                            | Application Missions  |
                            | (Full End-to-End)     |
                            +-----------------------+
                                        |
                                        v
+-----------------------------------------------------------------------------------------+
|                           Opaque-Box Test Infrastructure Core                           |
|                       (tests/e2e/test_infrastructure.py)                                |
|                                                                                         |
|  * Luminance Safety Gate Contract     * HRI Voice Confirmation State Machine Clock     |
|  * Occupancy Frontier Synthesizer      * SLAM Optimization & SSD NVMe Exporter Spy       |
|  * CosPlace 512D NPU / CPU Matcher    * RPLIDAR C1 360 Scan & AMCL Covariance Evaluator |
|  * TRINITY/CAG Situational Dialogues  * Hierarchical Hybrid Nav (Nav2->NOMAD->YOLO)     |
|  * SQLite WAL Spatial Room DB Model   * Audio Resampling & 0.1x Barge-in Verifier       |
+-----------------------------------------------------------------------------------------+
```

---

## 2. 4-Tier Systematic Testing Methodology

### Tier 1: Feature Coverage (TC1–TC8 / R1–R5)
Validates the nominal, boundary, and error-handling behavior of each core feature with at least 5 isolated, self-contained test cases per feature:
- **TC1 / R2 (Luminance Safety Gate)**: Image luminance evaluation (<25 reject, >30 authorize), voice warning dispatch, JSON topic payload, Trigger service interface.
- **TC2 / R2 (HRI Safety State Machine)**: Voice confirmation challenge before movement, 120s reminder on silence, 300s safe abort to standby, affirmative immediate dispatch, explicit negative cancellation.
- **TC3 / R2 (Autonomous Frontier Exploration)**: Occupancy grid free-to-unknown boundary clustering, waypoint goal dispatching, termination when remaining frontiers < 0.40m, motor stop command.
- **TC4 / R2 (SLAM Graph Optimization & SSD Export)**: Bundle adjustment service trigger, `/mnt/ssd/maps/` export (`.yaml` + `.pgm`), RAM delta budget (< 80MB), error handling on storage issues.
- **TC5 / R3 (Pitch-Dark LiDAR Localization)**: 0 lux total darkness, visual VPR disabling, 360° spin with RPLIDAR C1 ToF scan matching, AMCL particle convergence (trace < 0.08) within <= 2 rotations.
- **TC6 / R3 (Visual Place Recognition su Hailo NPU)**: CosPlace 512D L2-normalized vector extraction, latency < 50ms, cosine similarity > 0.84 room matching, 3-second rapid identification.
- **TC7 / R4 (VUI Dialogue & Intent Management)**: Situational queries ("Dove ti trovi?", "In quale mappa stai navigando?", "Cosa vedi?"), destructive command confirmation gate ("sì, confermo", 30s timeout), ReSpeaker 16kHz->48kHz resampling and 0.1x TTS gain attenuation.
- **TC8 / R5 (Hierarchical Hybrid Navigation)**: Nav2 macro-navigation to room polygon/centroid, seamless handover to NOMAD visual reactive search for blind spots, Hailo YOLO target detection (>0.55 conf) and servoing (<0.30m stop).

### Tier 2: Boundary & Corner Cases (Exact Thresholds)
Rigorously checks numerical limits and transitional states:
- **Luminance Thresholds**:
  - `24 / 255`: Strictly dark, reject mapping, trigger voice alert.
  - `25 / 255`: Low boundary limit, reject mapping.
  - `28 / 255`: Intermediate hysteresis zone, preserve safe state.
  - `30 / 255`: High boundary limit, transition to authorized.
  - `31 / 255`: Sufficient illumination, authorize visual mapping immediately.
- **Timer Thresholds**:
  - `119.0s`: Silence timer active, no reminder dispatched yet.
  - `120.0s`: Reminder threshold hit, vocal reminder dispatched.
  - `299.0s`: Approaching timeout, still awaiting response.
  - `300.0s`: Hard timeout elapsed, safe abort to standby.
  - Destructive confirmation: `29.0s` (pending) vs `30.0s` (timed out/cancelled).
- **Frontier Size Thresholds**:
  - `0.38m`: Below 0.40m limit -> exploration declares completion.
  - `0.40m`: Exact stopping boundary -> trigger completion.
  - `0.42m`: Above limit -> continue exploration loop.
- **AMCL Covariance Trace Thresholds**:
  - `0.079`: Strictly below 0.08 -> localization converged & confident.
  - `0.080`: Exact boundary limit.
  - `0.081`: Strictly above 0.08 -> unconverged, continue scan matching.
- **CosPlace 512D Cosine Similarity**:
  - `0.839`: Strictly below 0.84 -> reject room match (unknown room / false positive).
  - `0.840`: Exact boundary limit.
  - `0.841`: Strictly above 0.84 -> accept room match.
- **RAM Delta Limits**:
  - `79.0 MB`: Within allowable < 80MB budget during graph optimization.
  - `80.0 MB`: Exact boundary limit.
  - `81.0 MB`: Limit exceeded -> trigger memory pressure alarm.
- **Extreme Corner Conditions**:
  - `0.0 lux`: Complete darkness, optical black.
  - `255.0 lux`: Extreme optical saturation / blinding flare.
  - Single-pixel frontier island, disconnected floor partitions, empty particle distributions.

### Tier 3: Cross-Feature Interactions (Pairwise Combinations)
Tests concurrent subsystem interactions under stress:
1. *Frontier Mapping + Mid-Flight Luminance Drop*: Robot begins mapping in bright daylight, room illumination drops (<25) mid-mission.
2. *Kidnapped Robot in Pitch Darkness (0 Lux)*: Blind relocation into unfamiliar room, relying strictly on 360° LiDAR ToF scan matching.
3. *Nav2 Macro-Nav -> NOMAD Local Handover + Hailo YOLO Target Detection*: Complex handover pipeline without race conditions or velocity spikes.
4. *Frontier Exploration + Battery Sag Gating*: Transient voltage drop during mapping triggers safe map export and orderly return-to-dock.
5. *VUI Destructive Command Gate during Active Navigation*: Voice request to wipe map while navigating executes safety challenge without aborting ongoing motion unsafely.
6. *VPR CosPlace Visual Ambiguity + LiDAR Geometric Disambiguation*: Symmetrical room ambiguity resolved via LiDAR 360° Fourier/distance signatures.
7. *Barge-in VUI under Heavy NPU Load*: Ensuring 16kHz->48kHz audio streaming and 0.1x TTS gain attenuation function while Hailo NPU executes heavy inference.
8. *Dual-Mode REP-105 TF Authority Shift*: Switching between SLAM (`publish_tf:=true`) and AMCL Navigation (`publish_tf:=false` on RTAB-Map).

### Tier 4: Real-World Application Scenarios (End-to-End Missions)
Full multi-step operational missions:
- **Scenario 1**: Full Autonomous Room Commissioning (Spoken request -> HRI gate -> Frontier exploration -> Bundle adjustment -> SSD export -> Multimodal fingerprinting -> Voice announcement).
- **Scenario 2**: Nighttime Patrol & Dark Awakening (Awakening in pitch dark 0 lux -> Visual disabled -> 360° LiDAR spin -> Covariance trace < 0.08 in <2 spins -> Room localized -> CAG updated).
- **Scenario 3**: Conversational Lost Item Retrieval ("Marcus, cerca i miei occhiali in camera da letto" -> Nav2 macro-nav -> NOMAD blind-spot search -> YOLO sighting at conf > 0.55 -> Visual servoing to <0.30m -> Vocal report).
- **Scenario 4**: Rearranged Furniture & Destructive Map Overwrite (User requests map replacement -> Voice challenge -> 30s timeout test -> Confirmed overwrite test -> Clean SSD update).
- **Scenario 5**: Multi-Room Continuous Survey Across Lighting Conditions (Daylight living room -> Dim hallway [26 lux] -> Pitch-dark storage [0 lux] -> Continuous 2D map with semantic rooms).

---

## 3. Test Infrastructure Component Architecture

The infrastructure modules located in `tests/e2e/test_infrastructure.py` implement:
1. `LuminanceSafetyContract`: Computes luminance ($Y = 0.299R + 0.587G + 0.114B$), evaluates status against low threshold 25 and high threshold 30, and constructs `/camera/luminance_status` JSON payloads.
2. `HRISafetyStateMachine`: Simulates the confirmation state machine with simulated virtual time: awaiting confirmation, 120s prompt reminder, 300s standby abort, positive confirmation, and cancellation.
3. `FrontierExplorationEngine`: Detects boundaries between free (0) and unknown (-1) cells on occupancy grids, clusters connected frontiers, calculates cluster metric sizes, and triggers completion when max frontier size < 0.40m.
4. `SLAMOptimizationExporter`: Emulates RTAB-Map global bundle adjustment, tracks simulated RAM delta (< 80MB requirement), validates export paths (`/mnt/ssd/maps/*.yaml` and `*.pgm`), and verifies map metadata.
5. `CosPlaceVPRMatcher`: Implements 512D L2-normalized embedding extraction simulation, computes cosine similarity, validates the 0.84 acceptance threshold, and tracks inference latency (< 50ms).
6. `LiDARScanLocalizationSimulator`: Emulates RPLIDAR C1 360° polar distance scans, computes AMCL particle distribution covariance trace, and simulates 360° rotation convergence (< 0.08 within 2 spins).
7. `VUIDialogueEngine`: Implements situational awareness query handlers ("Dove ti trovi?", "In quale mappa stai navigando?", "Cosa vedi?"), destructive action confirmation state with 30s timeout, ReSpeaker 16kHz->48kHz resampling validation, and 0.1x TTS gain attenuation.
8. `HybridSearchCoordinator`: Orchestrates the 3-phase hybrid search: Phase 1 Nav2 macro-navigation, Phase 2 NOMAD reactive blind-spot perusal, Phase 3 YOLO target detection (conf > 0.55) and visual servoing (<0.30m stop).
9. `SemanticRoomRegistryModel`: Manages SQLite WAL and YAML room registries (`rooms_metadata.yaml`), storing polygonal boundaries, centroids, and multimodal fingerprints.

---

## 4. Test Execution & Reporting

The test suite can be run via pytest or the standalone runner:
```bash
# Run complete test suite via standalone runner
python tests/e2e/run_e2e_tests.py --all

# Run specific tier
python tests/e2e/run_e2e_tests.py --tier 1
python tests/e2e/run_e2e_tests.py --tier 2
python tests/e2e/run_e2e_tests.py --tier 3
python tests/e2e/run_e2e_tests.py --tier 4

# Run via pytest
pytest tests/e2e/ -v
```

All test scripts return exit code `0` on 100% pass and `1` on any failure, producing clear diagnostic summaries.
