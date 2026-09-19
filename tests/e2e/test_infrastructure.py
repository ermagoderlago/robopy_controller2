"""
==============================================================================
🧪 TEST INFRASTRUCTURE: Marcus Autonomous Perception & Navigation Stack
==============================================================================
Provides opaque-box contract adapters, deterministic test doubles, state
machine simulators, and evaluation fixtures for Tiers 1-4 of the E2E Test Suite.

Derived strictly from:
- ORIGINAL_REQUEST.md (R1-R5, TC1-TC8)
- PROJECT.md (Architecture, Interface Contracts, Milestones M1-M6)
- SPEC-00 to SPEC-07 & marcus_core_rules.md
==============================================================================
"""

import math
import time
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import numpy as np


# ----------------------------------------------------------------------------
# 1. Luminance Safety Gate Contract (TC1 / R2 / SPEC-03)
# ----------------------------------------------------------------------------
class LuminanceSafetyContract:
    """
    Evaluates frame luminance before visual mapping start.
    Thresholds:
      - < 25.0 / 255: REJECT / INHIBITED_DARK (voice warning)
      - > 30.0 / 255: AUTHORIZE visual mapping
      - 25.0 <= val <= 30.0: Transition / Hysteresis boundary (INHIBITED_DARK)
    """
    THRESHOLD_LOW = 25.0
    THRESHOLD_HIGH = 30.0

    @staticmethod
    def compute_mean_luminance(rgb_array: np.ndarray) -> float:
        """
        Computes standard perceptual luminance: Y = 0.299*R + 0.587*G + 0.114*B.
        Accepts (H, W, 3) or (H, W) array with values in [0, 255].
        """
        if rgb_array.ndim == 2:
            return float(np.mean(rgb_array))
        elif rgb_array.ndim == 3 and rgb_array.shape[2] >= 3:
            # RGB channels
            r = rgb_array[:, :, 0].astype(np.float32)
            g = rgb_array[:, :, 1].astype(np.float32)
            b = rgb_array[:, :, 2].astype(np.float32)
            luminance_map = 0.299 * r + 0.587 * g + 0.114 * b
            return float(np.mean(luminance_map))
        raise ValueError(f"Invalid image array shape: {rgb_array.shape}")

    @classmethod
    def evaluate(cls, mean_luminance: float) -> Dict[str, Any]:
        """
        Evaluates luminance and returns status dictionary.
        """
        if mean_luminance < cls.THRESHOLD_LOW:
            status = "INHIBITED_DARK"
            authorized = False
            warning = (
                f"Attenzione: illuminazione ambientale insufficiente per la mappatura visiva "
                f"({mean_luminance:.1f}/255). Mappatura inibita per sicurezza."
            )
        elif mean_luminance > cls.THRESHOLD_HIGH:
            status = "AUTHORIZED"
            authorized = True
            warning = None
        else:
            # Between 25.0 and 30.0 (marginal / transitional)
            status = "INHIBITED_DARK"
            authorized = False
            warning = (
                f"Attenzione: illuminazione al limite critico "
                f"({mean_luminance:.1f}/255). Richiesti almeno {cls.THRESHOLD_HIGH} per l'avvio."
            )

        return {
            "luminance": round(mean_luminance, 2),
            "status": status,
            "authorized": authorized,
            "voice_warning": warning,
            "threshold_low": cls.THRESHOLD_LOW,
            "threshold_high": cls.THRESHOLD_HIGH,
        }

    @classmethod
    def to_ros_topic_payload(cls, mean_luminance: float) -> str:
        """Returns the serialized JSON string matching /camera/luminance_status contract."""
        eval_result = cls.evaluate(mean_luminance)
        payload = {
            "luminance": eval_result["luminance"],
            "status": eval_result["status"],
            "threshold_low": eval_result["threshold_low"],
            "threshold_high": eval_result["threshold_high"],
        }
        return json.dumps(payload)

    @classmethod
    def trigger_service_check(cls, mean_luminance: float) -> Tuple[bool, str]:
        """Matches /mapping/check_luminance (std_srvs/srv/Trigger) contract."""
        res = cls.evaluate(mean_luminance)
        if res["authorized"]:
            return True, f"Luminance OK ({res['luminance']:.1f}/255). Mapping authorized."
        return False, res["voice_warning"]


# ----------------------------------------------------------------------------
# 2. HRI Voice Confirmation State Machine (TC2 / R2 / SPEC-04 / SPEC-05)
# ----------------------------------------------------------------------------
class HRISafetyStateMachine:
    """
    Coordinates safe HRI voice authorization prior to robot movement.
    Timers:
      - 120s: Reminder voice prompt on silence
      - 300s: Safe abort to standby on persistent silence
    Destructive actions:
      - 30s confirmation timeout
    """
    STATE_IDLE = "IDLE"
    STATE_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    STATE_REMINDER_SENT = "REMINDER_SENT"
    STATE_MAPPING_ACTIVE = "MAPPING_ACTIVE"
    STATE_STANDBY_ABORTED = "STANDBY_ABORTED"
    STATE_CANCELLED = "CANCELLED_BY_USER"

    REMINDER_TIMEOUT = 120.0
    STANDBY_TIMEOUT = 300.0
    DESTRUCTIVE_TIMEOUT = 30.0

    def __init__(self):
        self.state = self.STATE_IDLE
        self.target_environment: Optional[str] = None
        self.elapsed_time: float = 0.0
        self.voice_prompts_emitted: List[str] = []
        self.motors_allowed: bool = False
        self.reminder_dispatched: bool = False
        self.abort_dispatched: bool = False

        # Destructive command tracking
        self.destructive_pending: bool = False
        self.destructive_target: Optional[str] = None
        self.destructive_elapsed: float = 0.0
        self.destructive_confirmed: bool = False

    def request_mapping(self, environment_name: str) -> str:
        """Initiates HRI mapping request. Returns vocal challenge prompt."""
        self.target_environment = environment_name
        self.state = self.STATE_AWAITING_CONFIRMATION
        self.elapsed_time = 0.0
        self.reminder_dispatched = False
        self.abort_dispatched = False
        self.motors_allowed = False
        prompt = (
            f"Marcus: Richiesta di avvio mappatura autonoma per '{environment_name}'. "
            f"Per motivi di sicurezza, confermi con 'sì, procedi' per avviare i motori?"
        )
        self.voice_prompts_emitted.append(prompt)
        return prompt

    def request_destructive_action(self, target_name: str) -> str:
        """Requests confirmation for destructive action (e.g. map overwrite/delete)."""
        self.destructive_pending = True
        self.destructive_target = target_name
        self.destructive_elapsed = 0.0
        self.destructive_confirmed = False
        prompt = (
            f"Marcus: Attenzione! L'operazione sovrascriverà o cancellerà la mappa di '{target_name}'. "
            f"Per confermare l'azione irreversibile, rispondi 'sì, confermo' entro 30 secondi."
        )
        self.voice_prompts_emitted.append(prompt)
        return prompt

    def advance_time(self, delta_seconds: float) -> Optional[str]:
        """
        Advances simulated virtual clock and processes timer expirations.
        Returns newly emitted voice prompt if any.
        """
        new_prompt = None
        # Handle destructive timer
        if self.destructive_pending:
            self.destructive_elapsed += delta_seconds
            if self.destructive_elapsed >= self.DESTRUCTIVE_TIMEOUT:
                self.destructive_pending = False
                new_prompt = (
                    f"Marcus: Tempo scaduto per la conferma di sovrascrittura su '{self.destructive_target}'. "
                    f"Operazione annullata per sicurezza. La mappa originale resta integra."
                )
                self.voice_prompts_emitted.append(new_prompt)

        # Handle mapping FSM timers
        if self.state in (self.STATE_AWAITING_CONFIRMATION, self.STATE_REMINDER_SENT):
            self.elapsed_time += delta_seconds

            # 120s Reminder check
            if (
                self.elapsed_time >= self.REMINDER_TIMEOUT
                and not self.reminder_dispatched
                and self.state == self.STATE_AWAITING_CONFIRMATION
            ):
                self.reminder_dispatched = True
                self.state = self.STATE_REMINDER_SENT
                new_prompt = (
                    f"Marcus: Sollecito: sto ancora attendendo la tua conferma per mappare "
                    f"'{self.target_environment}'. Rispondi 'sì, procedi' per iniziare, "
                    f"altrimenti passerò in standby tra 3 minuti."
                )
                self.voice_prompts_emitted.append(new_prompt)

            # 300s Standby abort check
            if self.elapsed_time >= self.STANDBY_TIMEOUT and not self.abort_dispatched:
                self.abort_dispatched = True
                self.state = self.STATE_STANDBY_ABORTED
                self.motors_allowed = False
                new_prompt = (
                    f"Marcus: Nessuna conferma ricevuta dopo 300 secondi. "
                    f"Procedura di mappatura per '{self.target_environment}' annullata. "
                    f"I motori restano bloccati. Ritorno in standby."
                )
                self.voice_prompts_emitted.append(new_prompt)

        return new_prompt

    def process_voice_input(self, text: str) -> Tuple[str, bool]:
        """
        Processes voice response from user.
        Returns (response_message, action_successful).
        """
        cleaned = text.lower().strip()

        # Check destructive confirmation
        if self.destructive_pending:
            if "sì, confermo" in cleaned or "si, confermo" in cleaned or "confermo" in cleaned:
                self.destructive_pending = False
                self.destructive_confirmed = True
                msg = f"Marcus: Conferma registrata. Procedo con la modifica di '{self.destructive_target}'."
                self.voice_prompts_emitted.append(msg)
                return msg, True
            elif "no" in cleaned or "annulla" in cleaned:
                self.destructive_pending = False
                self.destructive_confirmed = False
                msg = f"Marcus: Operazione distruttiva annullata su richiesta dell'utente."
                self.voice_prompts_emitted.append(msg)
                return msg, False

        # Check mapping confirmation
        if self.state in (self.STATE_AWAITING_CONFIRMATION, self.STATE_REMINDER_SENT):
            affirmative = ["sì", "si", "confermo", "avvia", "procedi", "inizia", "ok"]
            negative = ["no", "annulla", "cancella", "fermati", "stop", "abort"]

            if any(word in cleaned for word in affirmative):
                self.state = self.STATE_MAPPING_ACTIVE
                self.motors_allowed = True
                msg = f"Marcus: Ricevuto. Avvio immediato della mappatura autonoma per '{self.target_environment}'."
                self.voice_prompts_emitted.append(msg)
                return msg, True

            if any(word in cleaned for word in negative):
                self.state = self.STATE_CANCELLED
                self.motors_allowed = False
                msg = f"Marcus: Mappatura annullata dall'utente. Motori disattivati."
                self.voice_prompts_emitted.append(msg)
                return msg, False

        return "Comando non riconosciuto nello stato corrente.", False


# ----------------------------------------------------------------------------
# 3. Frontier Exploration Engine (TC3 / R2 / SPEC-02)
# ----------------------------------------------------------------------------
class FrontierExplorationEngine:
    """
    Identifies occupancy grid boundary cells between free space (0) and unknown (-1),
    clusters them into contiguous frontiers, and evaluates stopping criteria (<0.40m).
    """
    FREE = 0
    OCCUPIED = 100
    UNKNOWN = -1
    MIN_FRONTIER_SIZE_METERS = 0.40

    def __init__(self, resolution: float = 0.05, origin: Tuple[float, float] = (-10.0, -10.0)):
        self.resolution = resolution  # meters per pixel
        self.origin = origin
        self.blacklisted_centroids: List[Tuple[float, float]] = []
        self.dispatched_goals: List[Tuple[float, float]] = []

    def detect_frontier_cells(self, grid: np.ndarray) -> List[Tuple[int, int]]:
        """
        Returns list of (row, col) coordinates that are FREE (0) and have at least
        one 8-connected neighbor that is UNKNOWN (-1).
        """
        rows, cols = grid.shape
        frontier_cells = []

        # 8-connected offsets
        neighbors = [(-1, -1), (-1, 0), (-1, 1),
                     (0, -1),           (0, 1),
                     (1, -1),  (1, 0),  (1, 1)]

        for r in range(1, rows - 1):
            for c in range(1, cols - 1):
                if grid[r, c] == self.FREE:
                    # Check if any neighbor is unknown
                    is_frontier = False
                    for dr, dc in neighbors:
                        if grid[r + dr, c + dc] == self.UNKNOWN:
                            is_frontier = True
                            break
                    if is_frontier:
                        frontier_cells.append((r, c))

        return frontier_cells

    def cluster_frontiers(self, frontier_cells: List[Tuple[int, int]]) -> List[Dict[str, Any]]:
        """
        Clusters contiguous frontier cells using connected component labeling (BFS).
        Returns a list of cluster metadata dictionaries.
        """
        if not frontier_cells:
            return []

        cell_set = set(frontier_cells)
        visited = set()
        clusters = []

        neighbors = [(-1, -1), (-1, 0), (-1, 1),
                     (0, -1),           (0, 1),
                     (1, -1),  (1, 0),  (1, 1)]

        for cell in frontier_cells:
            if cell in visited:
                continue

            # Start new cluster BFS
            cluster_cells = []
            queue = [cell]
            visited.add(cell)

            while queue:
                curr = queue.pop(0)
                cluster_cells.append(curr)
                cr, cc = curr

                for dr, dc in neighbors:
                    nbr = (cr + dr, cc + dc)
                    if nbr in cell_set and nbr not in visited:
                        visited.add(nbr)
                        queue.append(nbr)

            # Compute metric size and centroid
            rows = [c[0] for c in cluster_cells]
            cols = [c[1] for c in cluster_cells]

            min_r, max_r = min(rows), max(rows)
            min_c, max_c = min(cols), max(cols)

            # Metric dimensions
            delta_x = (max_c - min_c) * self.resolution
            delta_y = (max_r - min_r) * self.resolution
            spatial_size = math.sqrt(delta_x ** 2 + delta_y ** 2)

            # Metric centroid
            mean_r = float(np.mean(rows))
            mean_c = float(np.mean(cols))
            world_x = self.origin[0] + mean_c * self.resolution
            world_y = self.origin[1] + mean_r * self.resolution

            clusters.append({
                "cell_count": len(cluster_cells),
                "cells": cluster_cells,
                "size_meters": round(spatial_size, 4),
                "centroid": (round(world_x, 3), round(world_y, 3)),
            })

        return clusters

    def evaluate_frontiers(self, grid: np.ndarray) -> Dict[str, Any]:
        """
        Full evaluation step: detects, clusters, and decides whether exploration is complete.
        """
        frontier_cells = self.detect_frontier_cells(grid)
        clusters = self.cluster_frontiers(frontier_cells)

        # Filter out blacklisted centroids (unreachable)
        active_clusters = []
        for c in clusters:
            cx, cy = c["centroid"]
            is_blacklisted = any(
                math.hypot(cx - bx, cy - by) < 0.20 for bx, by in self.blacklisted_centroids
            )
            if not is_blacklisted:
                active_clusters.append(c)

        if not active_clusters:
            return {
                "status": "COMPLETED",
                "reason": "NO_FRONTIERS_REMAINING",
                "max_size_meters": 0.0,
                "cluster_count": 0,
                "selected_goal": None,
                "motor_stop_required": True,
            }

        max_size = max(c["size_meters"] for c in active_clusters)
        valid_frontiers = [c for c in active_clusters if c["size_meters"] >= self.MIN_FRONTIER_SIZE_METERS]

        if not valid_frontiers:
            return {
                "status": "COMPLETED",
                "reason": f"ALL_FRONTIERS_BELOW_THRESHOLD ({max_size:.2f}m < {self.MIN_FRONTIER_SIZE_METERS}m)",
                "max_size_meters": max_size,
                "cluster_count": len(active_clusters),
                "selected_goal": None,
                "motor_stop_required": True,
            }

        # Select largest cluster
        selected = max(valid_frontiers, key=lambda c: c["size_meters"])
        goal = selected["centroid"]
        self.dispatched_goals.append(goal)

        return {
            "status": "EXPLORING",
            "reason": f"FRONTIER_FOUND (size {selected['size_meters']:.2f}m >= {self.MIN_FRONTIER_SIZE_METERS}m)",
            "max_size_meters": max_size,
            "cluster_count": len(active_clusters),
            "selected_goal": goal,
            "motor_stop_required": False,
        }

    def blacklist_frontier(self, centroid: Tuple[float, float]):
        """Blacklists unreachable frontier."""
        self.blacklisted_centroids.append(centroid)


# ----------------------------------------------------------------------------
# 4. SLAM Optimization & SSD Map Exporter (TC4 / R2 / SPEC-02 / SPEC-07)
# ----------------------------------------------------------------------------
class SLAMOptimizationExporter:
    """
    Manages RTAB-Map global bundle adjustment and map export to /mnt/ssd/maps/.
    Verifies:
      - Memory RAM delta < 80MB (prevents OOM kill)
      - Destination directory strictly /mnt/ssd/maps/
      - Proper .yaml and .pgm formats
    """
    MAX_ALLOWED_RAM_DELTA_MB = 80.0
    MANDATORY_EXPORT_PREFIX = "/mnt/ssd/maps"

    def __init__(self, simulated_ssd_root: Optional[Path] = None, enforce_ssd_mount: bool = True):
        # Allow sandbox directory for host testing
        self.ssd_root = simulated_ssd_root or Path("/mnt/ssd/maps")
        self.enforce_ssd_mount = enforce_ssd_mount if simulated_ssd_root is None else False
        self.last_ram_delta_mb: float = 0.0
        self.optimization_called: bool = False

    def trigger_global_optimization(self, simulate_ram_usage_mb: float = 35.0) -> Tuple[bool, str]:
        """
        Simulates /rtabmap/global_optimization service call.
        Records RAM delta and verifies limit.
        """
        self.optimization_called = True
        self.last_ram_delta_mb = simulate_ram_usage_mb

        if simulate_ram_usage_mb >= self.MAX_ALLOWED_RAM_DELTA_MB:
            return False, (
                f"ALARM: RAM delta exceeded limit ({simulate_ram_usage_mb:.1f} MB >= "
                f"{self.MAX_ALLOWED_RAM_DELTA_MB} MB). Risk of Linux OOM Kill!"
            )

        return True, f"Global bundle adjustment converged. RAM delta: {simulate_ram_usage_mb:.1f} MB."

    def export_map_files(
        self,
        map_name: str,
        grid_data: np.ndarray,
        resolution: float = 0.05,
        target_override: Optional[Path] = None,
        force_readonly: bool = False,
        enforce_ssd_check: Optional[bool] = None
    ) -> Tuple[bool, str, Dict[str, Path]]:
        """
        Exports <map_name>.yaml and <map_name>.pgm.
        """
        should_enforce = self.enforce_ssd_mount if enforce_ssd_check is None else enforce_ssd_check
        target_dir = target_override or self.ssd_root
        target_path_str = str(target_dir).replace("\\", "/")

        # Check SSD NVMe rule
        if should_enforce and not target_path_str.startswith(self.MANDATORY_EXPORT_PREFIX):
            return False, f"VIOLATION FM-NAV-020: Target dir {target_dir} is not on SSD (/mnt/ssd/maps)!", {}

        if force_readonly:
            return False, "OSError: [Errno 30] Read-only file system on SSD", {}

        target_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = target_dir / f"{map_name}.yaml"
        pgm_path = target_dir / f"{map_name}.pgm"

        # Write YAML metadata
        yaml_content = (
            f"image: {map_name}.pgm\n"
            f"resolution: {resolution:.4f}\n"
            f"origin: [-10.000000, -10.000000, 0.000000]\n"
            f"negate: 0\n"
            f"occupied_thresh: 0.65\n"
            f"free_thresh: 0.25\n"
        )
        yaml_path.write_text(yaml_content, encoding="utf-8")

        # Write binary PGM
        h, w = grid_data.shape
        pgm_header = f"P5\n{w} {h}\n255\n".encode("ascii")
        # Map values: 0 -> 254 (free), 100 -> 0 (occupied), -1 -> 205 (unknown)
        pgm_img = np.full((h, w), 205, dtype=np.uint8)
        pgm_img[grid_data == 0] = 254
        pgm_img[grid_data == 100] = 0
        pgm_path.write_bytes(pgm_header + pgm_img.tobytes())

        return True, f"Map successfully exported to {yaml_path} and {pgm_path}", {
            "yaml": yaml_path,
            "pgm": pgm_path
        }


# ----------------------------------------------------------------------------
# 5. Visual Place Recognition (VPR) on Hailo NPU (TC6 / R3 / SPEC-03)
# ----------------------------------------------------------------------------
class CosPlaceVPRMatcher:
    """
    CosPlace 512D Vector Extractor and Cosine Matcher.
    Constraints:
      - 512-dimensional float32 vector
      - L2 normalization: ||v||_2 == 1.0 +- 1e-5
      - Latency < 50ms per frame
      - Matching threshold > 0.84
      - Rapid room identification <= 3.0 seconds
    """
    VECTOR_DIM = 512
    COSINE_MATCH_THRESHOLD = 0.84
    MAX_LATENCY_MS = 50.0
    MAX_ROOM_ID_TIME_SEC = 3.0

    def __init__(self):
        self.registered_rooms: Dict[str, List[np.ndarray]] = {}

    @classmethod
    def normalize_l2(cls, vector: np.ndarray) -> np.ndarray:
        """Enforces rigorous L2 normalization."""
        norm = np.linalg.norm(vector)
        if norm == 0 or np.isnan(norm):
            raise ValueError("Zero or NaN vector cannot be normalized")
        return (vector / norm).astype(np.float32)

    def extract_embedding_mock(
        self,
        seed_feature: float,
        simulated_latency_ms: float = 18.5,
        add_noise: bool = False
    ) -> Tuple[np.ndarray, float]:
        """
        Simulates Hailo-10H NPU CosPlace 512D extraction.
        Returns (l2_vector, elapsed_ms).
        """
        np.random.seed(int(abs(seed_feature * 1000)) % (2**31 - 1))
        raw = np.random.randn(self.VECTOR_DIM).astype(np.float32)
        if add_noise:
            raw += 0.05 * np.random.randn(self.VECTOR_DIM).astype(np.float32)
        norm_vec = self.normalize_l2(raw)
        return norm_vec, simulated_latency_ms

    def register_room(self, room_name: str, embeddings: List[np.ndarray]):
        """Registers a set of CosPlace 512D descriptors for a room."""
        for v in embeddings:
            assert v.shape == (self.VECTOR_DIM,), f"Vector shape must be ({self.VECTOR_DIM},)"
            assert abs(np.linalg.norm(v) - 1.0) < 1e-4, "Vector must be L2 normalized"
        self.registered_rooms[room_name] = embeddings

    def match_room(self, query_vector: np.ndarray) -> Dict[str, Any]:
        """
        Matches query vector against all registered rooms via max cosine similarity.
        """
        best_room = None
        best_similarity = -1.0

        for room_name, room_vecs in self.registered_rooms.items():
            for ref_vec in room_vecs:
                # Cosine similarity for L2-normalized vectors is simply the dot product
                sim = float(np.dot(query_vector, ref_vec))
                if sim > best_similarity:
                    best_similarity = sim
                    best_room = room_name

        matched = (best_similarity - self.COSINE_MATCH_THRESHOLD) > 1e-5
        return {
            "matched": matched,
            "room_name": best_room if matched else None,
            "similarity": round(best_similarity, 4),
            "threshold": self.COSINE_MATCH_THRESHOLD,
        }


# ----------------------------------------------------------------------------
# 6. Pitch-Dark LiDAR Localization & AMCL Convergence (TC5 / R3 / SPEC-02)
# ----------------------------------------------------------------------------
class LiDARScanLocalizationSimulator:
    """
    Simulates RPLIDAR C1 360° ToF scanning and AMCL particle filter convergence in total darkness.
    Constraints:
      - Total darkness: lux = 0 -> Visual VPR disabled
      - Controlled 360° spin with scan matching
      - AMCL particle covariance trace < 0.08 within <= 2 full rotations
    """
    MAX_AMCL_COVARIANCE_TRACE = 0.08
    MAX_ALLOWED_ROTATIONS = 2.0

    def __init__(self, known_room_signatures: Optional[Dict[str, np.ndarray]] = None):
        # 360 distance beams representing polar room boundaries
        self.signatures = known_room_signatures or {}
        self.vision_active = True
        self.current_covariance_trace = 0.50  # initial high uncertainty
        self.rotations_completed = 0.0

    def set_ambient_lux(self, lux: float):
        """Disables vision when lux == 0."""
        if lux <= 0.0:
            self.vision_active = False
        else:
            self.vision_active = True

    def reset_maneuver(self):
        """Resets rotation counter and uncertainty for a new localization maneuver."""
        self.rotations_completed = 0.0
        self.current_covariance_trace = 0.50

    def simulate_rotation_step(
        self,
        actual_room: str,
        rotation_fraction: float = 0.5,
        scan_noise_std: float = 0.02
    ) -> Dict[str, Any]:
        """
        Executes a partial or full rotation sweep, matching ToF scan against known rooms.
        Reduces AMCL covariance trace.
        """
        self.rotations_completed += rotation_fraction

        if actual_room not in self.signatures:
            # Unknown room: covariance trace remains high
            self.current_covariance_trace = max(0.20, self.current_covariance_trace * 0.95)
            return {
                "rotations": round(self.rotations_completed, 2),
                "covariance_trace": round(self.current_covariance_trace, 4),
                "converged": False,
                "identified_room": None,
                "vision_active": self.vision_active,
            }

        # Decay covariance trace towards convergence
        decay_factor = math.exp(-1.8 * rotation_fraction)
        target_trace = 0.045 + scan_noise_std
        self.current_covariance_trace = max(
            target_trace,
            self.current_covariance_trace * decay_factor
        )

        converged = (
            self.current_covariance_trace < self.MAX_AMCL_COVARIANCE_TRACE
            and self.rotations_completed <= self.MAX_ALLOWED_ROTATIONS
        )

        return {
            "rotations": round(self.rotations_completed, 2),
            "covariance_trace": round(self.current_covariance_trace, 4),
            "converged": converged,
            "identified_room": actual_room if converged else None,
            "vision_active": self.vision_active,
        }


# ----------------------------------------------------------------------------
# 7. VUI Situational Dialogue & Audio Conditioning (TC7 / R4 / SPEC-04 / SPEC-05)
# ----------------------------------------------------------------------------
class VUIDialogueEngine:
    """
    Situational awareness queries, destructive confirmation gates,
    audio streaming rate validation (16k in -> 48k out), and 0.1x barge-in gain.
    """
    def __init__(self):
        self.cag_snapshot = {
            "room_name": "sconosciuta",
            "map_name": "nessuna",
            "location": (0.0, 0.0),
            "nearest_room": "corridoio",
            "nearest_dist": 1.2,
            "amcl_covariance_trace": 0.055,
            "last_visual_detections": ["tavolo", "sedia"],
        }
        self.audio_in_rate = 16000
        self.audio_out_hw_rate = 48000
        self.stt_gain = 1.0

    def update_cag_location(
        self,
        room_name: str,
        location: Tuple[float, float],
        map_name: str,
        covariance_trace: float,
        visual_detections: Optional[List[str]] = None
    ):
        """Updates CAG real-time snapshot."""
        self.cag_snapshot["room_name"] = room_name
        self.cag_snapshot["location"] = location
        self.cag_snapshot["map_name"] = map_name
        self.cag_snapshot["amcl_covariance_trace"] = covariance_trace
        if visual_detections is not None:
            self.cag_snapshot["last_visual_detections"] = visual_detections

    def handle_query(self, user_text: str) -> str:
        """Processes natural language situational queries."""
        cleaned = user_text.lower().strip()

        if "dove ti trovi" in cleaned:
            room = self.cag_snapshot["room_name"]
            map_name = self.cag_snapshot["map_name"]
            x, y = self.cag_snapshot["location"]
            return (
                f"Mi trovo in {room} (coordinate: x={x:.1f}, y={y:.1f}), "
                f"navigando sulla mappa attiva '{map_name}'."
            )

        if "in quale mappa" in cleaned or "in che mappa" in cleaned:
            map_name = self.cag_snapshot["map_name"]
            trace = self.cag_snapshot["amcl_covariance_trace"]
            acc = "eccellente" if trace < 0.08 else "media"
            return (
                f"Sto navigando sulla mappa '{map_name}'. "
                f"Accuratezza localizzazione {acc} (traccia covarianza: {trace:.3f})."
            )

        if "cosa vedi" in cleaned:
            detections = self.cag_snapshot["last_visual_detections"]
            if not detections:
                return "Non rilevo oggetti specifici nel campo visivo attuale."
            items = ", ".join(detections)
            return f"Nel mio campo visivo riconosco: {items}."

        return "Non ho compreso la domanda. Puoi chiedermi dove mi trovo, in che mappa navigo o cosa vedo."

    def set_tts_active(self, is_speaking: bool):
        """Sets STT gain attenuation during TTS playback for barge-in."""
        if is_speaking:
            self.stt_gain = 0.1
        else:
            self.stt_gain = 1.0

    def verify_audio_resampling(self, input_rate: int, output_hw_rate: int) -> bool:
        """Checks ReSpeaker 16k -> 48k DAC resampling rule."""
        return input_rate == 16000 and output_hw_rate == 48000


# ----------------------------------------------------------------------------
# 8. Hierarchical Hybrid Navigation (TC8 / R5 / SPEC-02 / SPEC-03)
# ----------------------------------------------------------------------------
class HybridSearchCoordinator:
    """
    3-Phase Hierarchical Search Pipeline:
      Phase 1: Nav2 Macro-Navigation to target room centroid
      Phase 2: Handover to NOMAD visual reactive blind-spot perusal
      Phase 3: Hailo YOLO target detection (conf > 0.55) & visual servoing (<0.30m stop)
    """
    PHASE_IDLE = "IDLE"
    PHASE_1_NAV2_MACRO = "NAV2_MACRO"
    PHASE_2_NOMAD_REACTIVE = "NOMAD_REACTIVE"
    PHASE_3_VISUAL_SERVOING = "VISUAL_SERVOING"
    PHASE_COMPLETED = "COMPLETED"
    PHASE_FAILED = "FAILED"

    YOLO_MIN_CONFIDENCE = 0.55
    TARGET_PROXIMITY_METERS = 0.30

    def __init__(self):
        self.current_phase = self.PHASE_IDLE
        self.target_room: Optional[str] = None
        self.target_class: Optional[str] = None
        self.nav2_goal_reached = False
        self.nomad_active = False
        self.detected_target: Optional[Dict[str, Any]] = None
        self.final_distance_to_target: float = 999.0
        self.chime_played = False
        self.completion_announced = False

    def start_mission(self, room_name: str, target_class: str) -> str:
        """Starts hybrid search mission."""
        self.target_room = room_name
        self.target_class = target_class
        self.current_phase = self.PHASE_1_NAV2_MACRO
        self.nav2_goal_reached = False
        self.nomad_active = False
        self.detected_target = None
        self.final_distance_to_target = 999.0
        self.chime_played = False
        self.completion_announced = False
        return f"Missione avviata: ricerca '{target_class}' in '{room_name}' via Nav2 macro-navigazione."

    def notify_nav2_arrival(self):
        """Triggered upon Nav2 arrival at room centroid."""
        if self.current_phase == self.PHASE_1_NAV2_MACRO:
            self.nav2_goal_reached = True
            self.current_phase = self.PHASE_2_NOMAD_REACTIVE
            self.nomad_active = True

    def process_hailo_yolo_detections(self, detections: List[Dict[str, Any]]) -> Optional[str]:
        """
        Evaluates YOLO detections during NOMAD exploration.
        Each detection: {"class": str, "confidence": float, "distance_m": float}
        """
        if self.current_phase != self.PHASE_2_NOMAD_REACTIVE:
            return None

        # Look for target class with confidence > 0.55
        target_hits = [
            d for d in detections
            if d.get("class") == self.target_class and d.get("confidence", 0.0) >= self.YOLO_MIN_CONFIDENCE
        ]

        if target_hits:
            best_hit = max(target_hits, key=lambda d: d["confidence"])
            self.detected_target = best_hit
            self.current_phase = self.PHASE_3_VISUAL_SERVOING
            self.nomad_active = False
            return (
                f"Target '{self.target_class}' avvistato con confidenza {best_hit['confidence']:.2f}. "
                f"Avvio avvicinamento visual servoing."
            )
        return None

    def execute_visual_servoing_step(self, current_distance_m: float) -> Tuple[str, bool]:
        """
        Executes proximity approach towards detected target until distance < 0.30m.
        """
        if self.current_phase != self.PHASE_3_VISUAL_SERVOING:
            return "Visual servoing non attivo", False

        self.final_distance_to_target = current_distance_m
        if current_distance_m <= self.TARGET_PROXIMITY_METERS:
            self.current_phase = self.PHASE_COMPLETED
            self.chime_played = True
            self.completion_announced = True
            msg = (
                f"Target '{self.target_class}' raggiunto con successo a {current_distance_m:.2f}m. "
                f"Motori arrestati e notifica vocale emessa."
            )
            return msg, True

        return f"Avvicinamento in corso: distanza attuale {current_distance_m:.2f}m", False


# ----------------------------------------------------------------------------
# 9. Semantic Room Registry & SQLite WAL Model (TC1 / R1 / SPEC-05)
# ----------------------------------------------------------------------------
class SemanticRoomRegistryModel:
    """
    In-memory and SQLite WAL storage model for continuous 2D metric map rooms.
    Maintains polygonal boundaries, centroids, CosPlace fingerprints, and LiDAR signatures.
    """
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path(tempfile.gettempdir()) / f"test_mag_rooms_{time.time_ns()}.db"
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Returns SQLite connection with WAL and synchronous=NORMAL applied."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self):
        """Initializes SQLite tables with WAL mode."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                name TEXT PRIMARY KEY,
                centroid_x REAL,
                centroid_y REAL,
                polygon_json TEXT,
                vpr_fingerprint_blob BLOB,
                lidar_signature_blob BLOB
            );
        """)
        conn.commit()
        conn.close()

    def register_room(
        self,
        name: str,
        centroid: Tuple[float, float],
        polygon: List[Tuple[float, float]],
        vpr_fingerprints: Optional[List[np.ndarray]] = None,
        lidar_signature: Optional[np.ndarray] = None
    ):
        """Persists room into SQLite WAL database."""
        conn = self.get_connection()
        cur = conn.cursor()

        poly_json = json.dumps(polygon)
        vpr_blob = (
            np.array(vpr_fingerprints, dtype=np.float32).tobytes()
            if vpr_fingerprints is not None else None
        )
        lidar_blob = (
            lidar_signature.astype(np.float32).tobytes()
            if lidar_signature is not None else None
        )

        cur.execute("""
            INSERT OR REPLACE INTO rooms (name, centroid_x, centroid_y, polygon_json, vpr_fingerprint_blob, lidar_signature_blob)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (name, centroid[0], centroid[1], poly_json, vpr_blob, lidar_blob))
        conn.commit()
        conn.close()

    def get_room(self, name: str) -> Optional[Dict[str, Any]]:
        """Retrieves room record."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT name, centroid_x, centroid_y, polygon_json FROM rooms WHERE name = ?;", (name,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "name": row[0],
            "centroid": (row[1], row[2]),
            "polygon": json.loads(row[3]),
        }

    def match_room_by_pose(self, x: float, y: float) -> Optional[str]:
        """Point-in-polygon matching across all registered rooms."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT name, polygon_json FROM rooms;")
        rows = cur.fetchall()
        conn.close()

        for name, poly_json in rows:
            polygon = json.loads(poly_json)
            if self._point_in_polygon(x, y, polygon):
                return name
        return None

    @staticmethod
    def _point_in_polygon(x: float, y: float, poly: List[List[float]]) -> bool:
        """Ray-casting algorithm for point-in-polygon test."""
        n = len(poly)
        inside = False
        p1x, p1y = poly[0]
        for i in range(n + 1):
            p2x, p2y = poly[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside
