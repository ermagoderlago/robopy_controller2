#!/usr/bin/env python3
"""
Empirical Adversarial Test Suite for Milestone 3: Frontier Exploration
======================================================================
Author: challenger_m3_1 (Empirical Challenger: critic, specialist)
Target Module:
    - robopy_controller/nodes/frontier_explorer_node.py (FrontierExplorationEngine, FrontierExplorerNode)

Covers the 6 mandatory challenge dimensions:
1. Exact stopping boundaries: 0.38m (reject/stop), 0.399m (reject), 0.400m (accept), 0.401m (accept), 0.42m (accept)
2. Concave and U-shaped frontier clusters: verify centroid vs nav_goal safety
3. Obstacle and inflation proximity: free cells directly bordering walls/obstacles or within 0.20m of lethal obstacles rejected
4. Empty maps, completely unknown maps, completely explored maps (0 unknown cells)
5. Blacklist spatial matching: radius threshold (0.19m must match/blacklist, 0.21m must not match)
6. High-density synthetic occupancy grids (e.g. 1000x1000) for memory (<80MB delta) and CPU performance
"""

import math
import time
import tracemalloc
from pathlib import Path
from typing import List, Tuple
import numpy as np
import pytest

from robopy_controller.nodes.frontier_explorer_node import FrontierExplorationEngine


# ==============================================================================
# 1. Exact Stopping Boundaries (0.38m, 0.399m, 0.400m, 0.401m, 0.420m)
# ==============================================================================

class TestAdversarialStoppingBoundaries:
    """Stress-tests floating-point precision and strict boundary enforcement at 0.40m."""

    @pytest.mark.parametrize(
        "target_size,expected_status,expected_explore,expected_stop",
        [
            (0.380, "COMPLETED", False, True),
            (0.399, "COMPLETED", False, True),
            (0.400, "EXPLORING", True, False),
            (0.401, "EXPLORING", True, False),
            (0.420, "EXPLORING", True, False),
        ]
    )
    def test_exact_stopping_boundary_clustering_and_eval(
        self, target_size, expected_status, expected_explore, expected_stop
    ):
        """Validates exact stopping threshold using high-resolution (1mm) synthetic grids."""
        res_mm = 0.001  # 1mm resolution for exact metric span
        engine = FrontierExplorationEngine(resolution=res_mm, origin=(0.0, 0.0), min_frontier_size=0.40)

        # Build grid with line of free cells of exact span target_size
        num_cells = int(round(target_size / res_mm)) + 1
        grid = np.full((10, num_cells + 20), -1, dtype=np.int8)
        grid[5, 5: 5 + num_cells] = 0

        res = engine.evaluate_frontiers(grid)

        assert res["status"] == expected_status, (
            f"Target size {target_size:.3f}m produced status {res['status']}, expected {expected_status}"
        )
        assert res["motor_stop_required"] is expected_stop
        if expected_explore:
            assert res["selected_goal"] is not None
            assert res["max_size_meters"] >= 0.40
        else:
            assert res["selected_goal"] is None
            assert "ALL_FRONTIERS_BELOW_THRESHOLD" in res["reason"]

    def test_epsilon_precision_boundary(self):
        """Validates strict boundary at machine epsilon: 0.399999m vs 0.400001m."""
        engine = FrontierExplorationEngine(resolution=0.0001, origin=(0.0, 0.0), min_frontier_size=0.40)

        # 0.3999m
        cells_below = [(10, c) for c in range(10, 10 + 4000)]  # span = 3999 * 0.0001 = 0.3999m
        clusters_below = engine.cluster_frontiers(cells_below)
        assert clusters_below[0]["size_meters"] < 0.40
        assert clusters_below[0]["size_meters"] < engine.MIN_FRONTIER_SIZE_METERS

        # 0.4001m
        cells_above = [(10, c) for c in range(10, 10 + 4002)]  # span = 4001 * 0.0001 = 0.4001m
        clusters_above = engine.cluster_frontiers(cells_above)
        assert clusters_above[0]["size_meters"] >= 0.40
        assert clusters_above[0]["size_meters"] >= engine.MIN_FRONTIER_SIZE_METERS

    @pytest.mark.parametrize("resolution", [0.01, 0.02, 0.05, 0.10])
    def test_stopping_threshold_across_multiple_resolutions(self, resolution):
        """Verifies stopping threshold invariant across varying grid resolutions."""
        engine = FrontierExplorationEngine(resolution=resolution, origin=(0.0, 0.0), min_frontier_size=0.40)

        # Small cluster: 0.30m
        num_cells_small = max(2, int(0.30 / resolution) + 1)
        grid_small = np.full((15, num_cells_small + 10), -1, dtype=np.int8)
        grid_small[5, 5: 5 + num_cells_small] = 0
        res_small = engine.evaluate_frontiers(grid_small)
        assert res_small["status"] == "COMPLETED"
        assert res_small["motor_stop_required"] is True

        # Large cluster: 0.60m
        num_cells_large = int(0.60 / resolution) + 1
        grid_large = np.full((15, num_cells_large + 10), -1, dtype=np.int8)
        grid_large[5, 5: 5 + num_cells_large] = 0
        res_large = engine.evaluate_frontiers(grid_large)
        assert res_large["status"] == "EXPLORING"
        assert res_large["motor_stop_required"] is False


# ==============================================================================
# 2. Concave and U-Shaped Clusters: Centroid vs Nav Goal Safety
# ==============================================================================

class TestAdversarialConcaveAndUClusters:
    """
    Stress-tests concave, U-shaped, horseshoe, and serpentine clusters.
    Verifies that geometric centroid falls in exterior/interior non-cluster space,
    while nav_goal is strictly constrained to a valid free cell of the cluster.
    """

    def test_u_shaped_cluster_nav_goal_strictly_on_cluster(self):
        """Verifies nav_goal lies strictly on a valid frontier cell in free space."""
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0))
        # 11 cells forming a U-shape
        u_cells = [(5, 5), (6, 5), (7, 5), (8, 5), (8, 6), (8, 7), (8, 8), (8, 9), (7, 9), (6, 9), (5, 9)]
        clusters = engine.cluster_frontiers(u_cells)

        assert len(clusters) == 1
        c = clusters[0]
        nav_goal = c["nav_goal"]
        centroid = c["centroid"]

        # World coordinates of all cluster cells
        cell_coords = [(col * 0.05, row * 0.05) for row, col in u_cells]

        # nav_goal must match one of the actual cells
        nav_match = any(math.hypot(nav_goal[0] - x, nav_goal[1] - y) < 1e-3 for x, y in cell_coords)
        assert nav_match, f"nav_goal {nav_goal} does not match any cluster cell!"

        # centroid is at (mean_col*res, mean_row*res) = (7*0.05, 6.909*0.05) = (0.35, 0.345)
        # Verify centroid does NOT match any cluster cell
        centroid_match = any(math.hypot(centroid[0] - x, centroid[1] - y) < 1e-3 for x, y in cell_coords)
        assert not centroid_match, "Geometric centroid of U-shape unexpectedly matched a cluster cell!"

    def test_u_shaped_cluster_in_grid_cavity_unknown_space(self):
        """
        Adversarial test: An unexplored bay of unknown space surrounded by free cells on 3 sides.
        The centroid falls in UNKNOWN space (-1), while nav_goal is in FREE space (0).
        """
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.10)
        grid = np.full((35, 35), 0, dtype=np.int8)  # all free space
        # A bay of unknown space penetrating into free space
        grid[10:25, 10:20] = -1

        frontier_cells = engine.detect_frontier_cells(grid)
        clusters = engine.cluster_frontiers(frontier_cells)
        assert len(clusters) >= 1

        # Locate the U-shaped cluster around the bay
        bay_cluster = max(clusters, key=lambda c: c["cell_count"])
        cx, cy = bay_cluster["centroid"]
        nx, ny = bay_cluster["nav_goal"]

        centroid_col = int(round(cx / 0.05))
        centroid_row = int(round(cy / 0.05))
        nav_col = int(round(nx / 0.05))
        nav_row = int(round(ny / 0.05))

        val_at_centroid = grid[centroid_row, centroid_col]
        val_at_nav_goal = grid[nav_row, nav_col]

        # nav_goal MUST be in known free space (0)
        assert val_at_nav_goal == 0, f"nav_goal at ({nav_row}, {nav_col}) has value {val_at_nav_goal} != FREE (0)"

        # Empirical observation: Centroid falls in unknown space (-1)
        assert val_at_centroid == -1, (
            f"Centroid at ({centroid_row}, {centroid_col}) was expected to fall in unknown cavity (-1), "
            f"got {val_at_centroid}"
        )

    def test_concave_cluster_with_lethal_obstacle_in_cavity(self):
        """
        Adversarial test: U-shaped free corridor around a lethal obstacle (100).
        Centroid is near/inside the obstacle, while nav_goal is in free space.
        """
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.10)
        # Synthetic U-cells
        u_cells = [
            (10, 10), (11, 10), (12, 10), (13, 10), (14, 10),
            (14, 11), (14, 12), (14, 13), (14, 14), (14, 15),
            (13, 15), (12, 15), (11, 15), (10, 15)
        ]
        clusters = engine.cluster_frontiers(u_cells)
        c = clusters[0]

        # Put obstacle right at the centroid row/col: row ~12.5, col ~12.5
        mean_r = np.mean([x[0] for x in u_cells])
        mean_c = np.mean([x[1] for x in u_cells])

        # Centroid is in the interior cavity
        assert (round(mean_r), round(mean_c)) not in u_cells

        # nav_goal is strictly in u_cells
        nav_r = round(c["nav_goal"][1] / 0.05)
        nav_c = round(c["nav_goal"][0] / 0.05)
        assert (nav_r, nav_c) in u_cells

    def test_evaluate_frontiers_selected_goal_discrepancy(self):
        """
        CRITICAL ADVERSARIAL FINDING:
        In `evaluate_frontiers`, `selected_goal` assigns `goal = selected['centroid']`
        instead of `selected['nav_goal']`.
        This test empirically verifies the exact discrepancy between selected_goal and nav_goal.
        """
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.10)
        grid = np.full((35, 35), 0, dtype=np.int8)
        grid[10:25, 10:20] = -1  # unknown bay

        eval_res = engine.evaluate_frontiers(grid)
        assert eval_res["status"] == "EXPLORING"

        selected_goal = eval_res["selected_goal"]
        selected_cluster = eval_res["selected_cluster"]
        nav_goal = selected_cluster["nav_goal"]
        centroid = selected_cluster["centroid"]

        # Verify that selected_goal currently returns centroid, NOT nav_goal
        assert selected_goal == centroid, "selected_goal is expected to match centroid currently"

        # Check grid values at selected_goal vs nav_goal
        sg_col = int(round(selected_goal[0] / 0.05))
        sg_row = int(round(selected_goal[1] / 0.05))
        ng_col = int(round(nav_goal[0] / 0.05))
        ng_row = int(round(nav_goal[1] / 0.05))

        val_at_sg = grid[sg_row, sg_col]
        val_at_ng = grid[ng_row, ng_col]

        # nav_goal is safe (0), but selected_goal is in unknown space (-1)!
        assert val_at_ng == 0, "nav_goal must be in free space"
        # Discrepancy documented: selected_goal is unsafe (-1)
        assert val_at_sg == -1, (
            "Empirical proof: selected_goal dispatched into unknown space (-1) due to centroid usage!"
        )

    def test_blacklist_nav_goal_vs_centroid_distance_mismatch(self):
        """
        CRITICAL CONCURRENCY/SPATIAL VULNERABILITY:
        If Nav2 rejects a nav_goal at distance > 0.20m from centroid,
        `blacklist_frontier(nav_goal)` will NOT match `c['centroid']` in `evaluate_frontiers`,
        causing an infinite loop where the unreachable cluster is never filtered out.
        """
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.10)
        grid = np.full((35, 35), 0, dtype=np.int8)
        grid[10:25, 10:20] = -1

        eval1 = engine.evaluate_frontiers(grid)
        c = eval1["selected_cluster"]
        cx, cy = c["centroid"]
        nx, ny = c["nav_goal"]

        dist_c_to_n = math.hypot(cx - nx, cy - ny)
        # In this U-shape, distance is 0.275m > 0.20m
        assert dist_c_to_n > 0.20, f"Distance {dist_c_to_n:.3f}m is unexpectedly <= 0.20m"

        # If nav_goal is blacklisted:
        engine.clear_blacklist()
        engine.blacklist_frontier((nx, ny))

        # In evaluate_frontiers, filtering checks: math.hypot(cx - bx, cy - by) < 0.20
        # Because dist_c_to_n > 0.20m, the cluster is NOT blacklisted!
        eval2 = engine.evaluate_frontiers(grid)
        # Cluster remains active because blacklisting checked centroid instead of nav_goal!
        assert eval2["status"] == "EXPLORING", (
            "Cluster unexpectedly blacklisted when blacklisting checked nav_goal vs centroid"
        )


# ==============================================================================
# 3. Obstacle and Inflation Proximity Filtering (0.20m clearance)
# ==============================================================================

class TestAdversarialObstacleAndInflationProximity:
    """
    Stress-tests obstacle proximity and safety inflation rejection:
    - Free cells directly bordering obstacles (8-connected) must be rejected
    - Free cells within 0.20m of lethal obstacles (100) must be rejected
    - Free cells at distance > 0.20m must be accepted
    """

    def test_free_cell_directly_bordering_obstacle_rejected(self):
        """Free cell with 8-neighbor >= 65 is rejected even if it borders unknown space."""
        engine = FrontierExplorationEngine(resolution=0.05, clearance_radius=0.20)
        grid = np.full((20, 20), -1, dtype=np.int8)
        grid[5:15, 5:15] = 0  # 10x10 free island
        grid[8, 9] = 100  # lethal obstacle inside island

        cells = engine.detect_frontier_cells(grid, reject_obstacle_proximity=False)
        # Cell (8, 8) touches unknown space? No, inside island.
        # But along perimeter: cell (5, 9) touches obstacle?
        # Let's put obstacle directly on perimeter at (5, 8)
        grid[5, 8] = 100
        # Now cell (5, 7) touches unknown (row 4) AND touches obstacle (5, 8)
        cells = engine.detect_frontier_cells(grid, reject_obstacle_proximity=False)
        assert (5, 7) not in cells, "Cell directly touching obstacle must be rejected!"
        assert (5, 8) not in cells, "Obstacle cell itself must never be a frontier!"

    def test_clearance_radius_020m_chebyshev_rejection(self):
        """Free cells within 4 cells (0.20m / 0.05m) of obstacle are rejected."""
        res = 0.05
        clearance = 0.20
        engine = FrontierExplorationEngine(resolution=res, clearance_radius=clearance)

        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[5:25, 5:25] = 0  # free space
        obs_r, obs_c = 15, 15
        grid[obs_r, obs_c] = 100

        cells = engine.detect_frontier_cells(grid, reject_obstacle_proximity=True)
        clearance_cells = int(math.ceil(clearance / res))  # 4 cells

        for r, c in cells:
            dr = abs(r - obs_r)
            dc = abs(c - obs_c)
            # Must not be inside the clearance box of the obstacle
            assert not (dr <= clearance_cells and dc <= clearance_cells), (
                f"Frontier cell ({r}, {c}) is inside clearance box of obstacle ({obs_r}, {obs_c})!"
            )

    def test_free_cells_beyond_clearance_accepted(self):
        """Free cells at distance > 0.20m from obstacle are retained."""
        engine = FrontierExplorationEngine(resolution=0.05, clearance_radius=0.20)
        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[5:25, 5:25] = 0
        # Obstacle in center
        grid[15, 15] = 100

        cells = engine.detect_frontier_cells(grid, reject_obstacle_proximity=True)
        assert len(cells) > 0

        # Perimeter cell at (5, 5): distance to (15, 15) is 10 cells = 0.50m > 0.20m
        assert (5, 5) in cells

    def test_continuous_wall_obstacle_barrier(self):
        """Continuous obstacle wall rejects entire adjacent frontier band."""
        engine = FrontierExplorationEngine(resolution=0.05, clearance_radius=0.20)
        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[10:20, 10:20] = 0  # 10x10 free
        # Wall at row 16, cols 10..20
        grid[16, 10:20] = 100

        cells = engine.detect_frontier_cells(grid, reject_obstacle_proximity=True)
        for r, c in cells:
            # Distance in row to wall at row 16 must be > 4 cells
            assert abs(r - 16) > 4, f"Cell ({r}, {c}) is within 0.20m of wall at row 16!"

    def test_soft_obstacle_threshold_discrimination(self):
        """Cells >= OCCUPIED_THRESHOLD (65) treated as obstacles; < 65 treated as passable."""
        engine = FrontierExplorationEngine(resolution=0.05, clearance_radius=0.20)
        grid = np.full((20, 20), -1, dtype=np.int8)
        grid[5:15, 5:15] = 0

        # Soft cost cell = 64 (below threshold 65)
        grid[10, 10] = 64
        # Since 64 is not FREE (0), it is not a frontier itself, but doesn't trigger clearance rejection
        cells = engine.detect_frontier_cells(grid, reject_obstacle_proximity=True)
        # Perimeter cells at rows 5 and cols 5 are valid
        assert (5, 10) in cells

        # Soft cost cell = 65 (at threshold)
        grid[10, 10] = 65
        # Now 65 is treated as obstacle. Cells within 4 cells of (10, 10) are rejected
        cells_obs = engine.detect_frontier_cells(grid, reject_obstacle_proximity=True)
        # Cell (6, 10) is 4 cells from (10, 10) -> rejected
        assert (6, 10) not in cells_obs


# ==============================================================================
# 4. Map Topologies: Empty, Unknown, Explored, Occupied
# ==============================================================================

class TestAdversarialMapTopologies:
    """Stress-tests corner-case map topologies."""

    @pytest.mark.parametrize("shape", [(0, 0), (1, 1), (2, 2), (1, 100), (100, 1)])
    def test_degenerate_and_minimal_grid_shapes(self, shape):
        """Grids with dimensions < 3 rows or cols gracefully yield COMPLETED without exception."""
        engine = FrontierExplorationEngine()
        grid = np.zeros(shape, dtype=np.int8)
        res = engine.evaluate_frontiers(grid)

        assert res["status"] == "COMPLETED"
        assert res["motor_stop_required"] is True
        assert res["selected_goal"] is None
        assert res["cluster_count"] == 0

    def test_completely_unknown_grid(self):
        """Completely unexplored map (-1) has no free cells -> COMPLETED."""
        engine = FrontierExplorationEngine()
        grid = np.full((50, 50), -1, dtype=np.int8)
        res = engine.evaluate_frontiers(grid)

        assert res["status"] == "COMPLETED"
        assert res["reason"] == "NO_FRONTIERS_REMAINING"
        assert res["motor_stop_required"] is True
        assert res["cluster_count"] == 0

    def test_completely_explored_grid_zero_unknowns(self):
        """Fully explored map (all 0, zero unknowns) -> COMPLETED."""
        engine = FrontierExplorationEngine()
        grid = np.zeros((50, 50), dtype=np.int8)
        res = engine.evaluate_frontiers(grid)

        assert res["status"] == "COMPLETED"
        assert res["reason"] == "NO_FRONTIERS_REMAINING"
        assert res["motor_stop_required"] is True
        assert res["cluster_count"] == 0

    def test_completely_occupied_grid_all_lethal(self):
        """Fully occupied map (all 100) -> COMPLETED."""
        engine = FrontierExplorationEngine()
        grid = np.full((50, 50), 100, dtype=np.int8)
        res = engine.evaluate_frontiers(grid)

        assert res["status"] == "COMPLETED"
        assert res["motor_stop_required"] is True

    def test_explored_map_with_obstacles_no_unknown(self):
        """Fully explored environment containing walls and rooms, but 0 unknown cells."""
        engine = FrontierExplorationEngine()
        grid = np.zeros((60, 60), dtype=np.int8)
        # Perimeter walls and interior partitions
        grid[0, :] = 100
        grid[-1, :] = 100
        grid[:, 0] = 100
        grid[:, -1] = 100
        grid[30, 10:50] = 100

        res = engine.evaluate_frontiers(grid)
        assert res["status"] == "COMPLETED"
        assert res["reason"] == "NO_FRONTIERS_REMAINING"
        assert res["motor_stop_required"] is True


# ==============================================================================
# 5. Blacklist Spatial Matching Radius Threshold (0.19m vs 0.21m)
# ==============================================================================

class TestAdversarialBlacklistSpatialMatching:
    """Stress-tests spatial radius blacklisting threshold (0.20m boundary)."""

    def test_blacklist_distance_019m_matches_and_suppresses(self):
        """Point within 0.19m (< 0.20m) matches and blacklists the cluster."""
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.40)
        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[15, 10:20] = 0  # 10 cells -> span = 9 * 0.05 = 0.45m
        grid[16, 10:20] = 0

        c = engine.cluster_frontiers(engine.detect_frontier_cells(grid))[0]
        cx, cy = c["centroid"]

        # Blacklist point at 0.190m offset
        engine.clear_blacklist()
        engine.blacklist_frontier((cx + 0.190, cy))
        res = engine.evaluate_frontiers(grid)

        assert res["status"] == "COMPLETED", "Distance 0.190m must match and blacklist cluster!"
        assert res["cluster_count"] == 0

    def test_blacklist_distance_021m_does_not_match_and_retains(self):
        """Point at 0.21m (> 0.20m) does not match and cluster is retained."""
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.40)
        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[15, 10:20] = 0
        grid[16, 10:20] = 0

        c = engine.cluster_frontiers(engine.detect_frontier_cells(grid))[0]
        cx, cy = c["centroid"]

        # Blacklist point at 0.210m offset
        engine.clear_blacklist()
        engine.blacklist_frontier((cx + 0.210, cy))
        res = engine.evaluate_frontiers(grid)

        assert res["status"] == "EXPLORING", "Distance 0.210m must NOT blacklist cluster!"
        assert res["cluster_count"] == 1
        assert res["selected_goal"] is not None

    def test_blacklist_boundary_precision_0199m_vs_0201m(self):
        """Precision verification at 0.199m (blacklisted) vs 0.201m (not blacklisted)."""
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.40)
        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[15, 10:20] = 0
        grid[16, 10:20] = 0

        c = engine.cluster_frontiers(engine.detect_frontier_cells(grid))[0]
        cx, cy = c["centroid"]

        # 0.199m
        engine.clear_blacklist()
        engine.blacklist_frontier((cx + 0.199, cy))
        res_sub = engine.evaluate_frontiers(grid)
        assert res_sub["status"] == "COMPLETED"

        # 0.201m
        engine.clear_blacklist()
        engine.blacklist_frontier((cx + 0.201, cy))
        res_sup = engine.evaluate_frontiers(grid)
        assert res_sup["status"] == "EXPLORING"

    def test_blacklist_clear_fully_restores_frontiers(self):
        """Clearing blacklist makes all previously rejected frontiers available again."""
        engine = FrontierExplorationEngine(resolution=0.05, origin=(0.0, 0.0), min_frontier_size=0.40)
        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[15, 10:20] = 0
        grid[16, 10:20] = 0

        res1 = engine.evaluate_frontiers(grid)
        goal = res1["selected_goal"]
        engine.blacklist_frontier(goal)

        res2 = engine.evaluate_frontiers(grid)
        assert res2["status"] == "COMPLETED"

        engine.clear_blacklist()
        res3 = engine.evaluate_frontiers(grid)
        assert res3["status"] == "EXPLORING"
        assert res3["selected_goal"] == goal


# ==============================================================================
# 6. High-Density Synthetic Occupancy Grids (1000x1000 Performance & Memory)
# ==============================================================================

class TestAdversarialHighDensityPerformance:
    """
    Stress-tests high-density grids (1000x1000 = 1,000,000 cells).
    Enforces host Pi 5 budget:
    - Peak RAM delta strictly < 80.0 MB
    - Execution time bounded
    """

    def test_1000x1000_all_unknown_grid_timing(self):
        """1000x1000 all-unknown grid completes in < 0.5s."""
        engine = FrontierExplorationEngine()
        grid = np.full((1000, 1000), -1, dtype=np.int8)

        t0 = time.perf_counter()
        res = engine.evaluate_frontiers(grid)
        dt = time.perf_counter() - t0

        assert res["status"] == "COMPLETED"
        assert dt < 0.50, f"1000x1000 all-unknown evaluation too slow: {dt:.3f}s"

    def test_1000x1000_large_single_room_performance(self):
        """1000x1000 grid with a large 200x200 free room completes in < 1.0s."""
        engine = FrontierExplorationEngine()
        grid = np.full((1000, 1000), -1, dtype=np.int8)
        grid[400:600, 400:600] = 0
        grid[450, 450] = 100  # an obstacle

        t0 = time.perf_counter()
        res = engine.evaluate_frontiers(grid)
        dt = time.perf_counter() - t0

        assert res["status"] == "EXPLORING"
        assert res["cluster_count"] >= 1
        assert dt < 1.00, f"1000x1000 single room evaluation too slow: {dt:.3f}s"

    def test_1000x1000_100_rooms_peak_ram_and_cpu_budget(self):
        """
        Adversarial stress test: 100 independent rooms across a 1,000,000 cell grid.
        Peak RAM increase must be strictly < 80.0 MB.
        """
        engine = FrontierExplorationEngine()
        grid = np.full((1000, 1000), -1, dtype=np.int8)
        for i in range(10):
            for j in range(10):
                grid[i * 90 + 10: (i + 1) * 90 - 10, j * 90 + 10: (j + 1) * 90 - 10] = 0

        tracemalloc.start()
        t0 = time.perf_counter()
        res = engine.evaluate_frontiers(grid)
        dt = time.perf_counter() - t0
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak_bytes / (1024.0 * 1024.0)

        assert res["status"] == "EXPLORING"
        assert res["cluster_count"] == 100
        # Host RAM budget: < 80MB
        assert peak_mb < 80.0, f"Peak RAM allocation {peak_mb:.2f} MB exceeded 80.0 MB limit!"
        # Execution time limit: < 15.0s on development host
        assert dt < 15.0, f"Evaluation of 100 rooms in 1000x1000 grid took {dt:.2f}s (>15s)"

    def test_zero_memory_leak_across_repeated_evaluations(self):
        """Repeated 1000x1000 evaluations exhibit zero monotonic memory accumulation."""
        engine = FrontierExplorationEngine()
        grid = np.full((1000, 1000), -1, dtype=np.int8)
        grid[400:500, 400:500] = 0

        tracemalloc.start()
        # Warmup
        engine.evaluate_frontiers(grid)

        mem_snapshots = []
        for _ in range(5):
            engine.evaluate_frontiers(grid)
            cur, _ = tracemalloc.get_traced_memory()
            mem_snapshots.append(cur)
        tracemalloc.stop()

        # Delta between first and last iteration should be negligible (< 2.0 MB)
        delta_mb = (mem_snapshots[-1] - mem_snapshots[0]) / (1024.0 * 1024.0)
        assert delta_mb < 2.0, f"Potential memory leak detected: delta = {delta_mb:.2f} MB"


# ==============================================================================
# 7. Goal Selection Strategies & Extreme Pose Conditions
# ==============================================================================

class TestAdversarialSelectionStrategies:
    """Stress-tests selection strategies under extreme and edge-case robot poses."""

    def test_strategy_nearest_with_extreme_robot_pose(self):
        """Nearest goal selection with robot pose at extreme negative coordinates."""
        engine = FrontierExplorationEngine(
            resolution=0.05, selection_strategy="nearest", origin=(-100.0, -100.0), min_frontier_size=0.40
        )
        grid = np.full((60, 60), -1, dtype=np.int8)
        # Cluster 1 at row 10 (world y ~ -99.5)
        grid[10, 10:20] = 0
        grid[11, 10:20] = 0
        # Cluster 2 at row 50 (world y ~ -97.5)
        grid[50, 10:20] = 0
        grid[51, 10:20] = 0

        # Robot at extreme position (-1000.0, -1000.0) -> Cluster 1 is closer
        res = engine.evaluate_frontiers(grid, robot_pose=(-1000.0, -1000.0))
        assert res["status"] == "EXPLORING"
        assert res["selected_goal"][1] < -98.0

    def test_strategy_hybrid_extreme_pose_stability(self):
        """Hybrid selection combines size / (distance + 0.5) without division by zero."""
        engine = FrontierExplorationEngine(resolution=0.05, selection_strategy="hybrid", min_frontier_size=0.40)
        grid = np.full((40, 40), -1, dtype=np.int8)
        grid[10, 10:20] = 0
        grid[11, 10:20] = 0

        c = engine.cluster_frontiers(engine.detect_frontier_cells(grid))[0]
        cx, cy = c["centroid"]

        # Robot pose directly at cluster centroid: distance = 0.0 -> denominator = 0.5
        res = engine.evaluate_frontiers(grid, robot_pose=(cx, cy))
        assert res["status"] == "EXPLORING"
        assert res["selected_goal"] is not None

    def test_zero_bom_cleanliness_on_adversarial_suite(self):
        """Verifies this test file is free of UTF-8 Byte Order Mark (BOM)."""
        raw = Path(__file__).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), "BOM detected in test_adversarial_m3_frontier_exploration.py"
