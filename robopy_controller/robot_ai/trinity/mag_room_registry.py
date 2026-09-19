"""
MAG Room Registry: Semantic Room Memory, Spatial Geometry, and Multimodal Signatures.
Provides in-memory caching, thread-safe access, LRU caching for signatures (<=64 items),
and crash-resilient bidirectional synchronization with rooms_metadata.yaml.
"""

import os
import yaml
import json
import time
import math
import uuid
import struct
import threading
from typing import Dict, Any, List, Optional, Tuple, Sequence
from collections import OrderedDict
from dataclasses import dataclass, field

from robot_ai.utils.logging_utils import get_logger
from robot_ai.trinity.mag_database import MAGDatabase
from robot_ai.trinity.mag_room_geometry import (
    compute_bounding_box,
    compute_polygon_centroid,
    point_in_polygon
)

logger = get_logger("MAGRoomRegistry")


@dataclass
class RoomMetadata:
    """Semantic Room Metadata and spatial boundaries."""
    room_name: str
    display_name: str
    floor_id: str
    floor: str
    map_name: str
    polygon: List[Tuple[float, float]]
    centroid: Tuple[float, float]
    bounding_box: Tuple[float, float, float, float]
    nav_goal: Optional[Tuple[float, float, float]] = None
    created_at: float = 0.0
    updated_at: float = 0.0


class MAGRoomRegistry:
    """
    Registry for managing semantic room boundaries, spatial pose matching,
    multimodal signatures (VPR & LiDAR), and bidirectional synchronization with YAML.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        yaml_path: Optional[str] = None,
        db: Optional[MAGDatabase] = None
    ):
        self._db = db or MAGDatabase(db_path or "/home/robopy/mag_trinity.db")
        self._yaml_path = yaml_path
        self._lock = threading.RLock()
        self._rooms_cache: Dict[str, RoomMetadata] = {}
        # Enforce LRU cache max 64 items (SPEC-05 Red Zone constraint)
        self._signatures_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        
        self._hydrate_or_sync()

    def _hydrate_or_sync(self) -> None:
        """Initializes cache from SQLite WAL and/or synchronizes with YAML."""
        with self._lock:
            # 1. First, check if database already has rooms
            db_rooms = self._db.list_all_rooms()
            if db_rooms:
                for r in db_rooms:
                    self._populate_cache_from_dict(r)
            elif self._yaml_path and os.path.exists(self._yaml_path):
                # 2. Database empty, bootstrap from YAML file
                try:
                    self.import_from_yaml(self._yaml_path)
                except Exception as e:
                    logger.error(f"Error bootstrapping room registry from YAML {self._yaml_path}: {e}")

    def _populate_cache_from_dict(self, r: Dict[str, Any]) -> RoomMetadata:
        """Creates RoomMetadata and stores in _rooms_cache."""
        room_name = r["room_name"]
        poly = [(float(pt[0]), float(pt[1])) for pt in r.get("polygon", [])]
        
        # Bounding box
        if r.get("min_x") is not None and r.get("min_y") is not None:
            bbox = (float(r["min_x"]), float(r["min_y"]), float(r["max_x"]), float(r["max_y"]))
        elif r.get("bounding_box") is not None:
            bbox = tuple(float(x) for x in r["bounding_box"])
        else:
            bbox = compute_bounding_box(poly) if len(poly) >= 3 else (0.0, 0.0, 0.0, 0.0)
            
        # Centroid
        if r.get("centroid_x") is not None and r.get("centroid_y") is not None:
            centroid = (float(r["centroid_x"]), float(r["centroid_y"]))
        elif r.get("centroid") is not None:
            centroid = (float(r["centroid"][0]), float(r["centroid"][1]))
        else:
            centroid = compute_polygon_centroid(poly) if len(poly) >= 3 else (0.0, 0.0)

        nav_goal = None
        if r.get("nav_goal_x") is not None and r.get("nav_goal_y") is not None:
            nav_goal = (float(r["nav_goal_x"]), float(r["nav_goal_y"]), float(r.get("nav_goal_theta", 0.0)))
        elif r.get("navigation_goal") is not None:
            ng = r["navigation_goal"]
            if isinstance(ng, dict):
                nav_goal = (float(ng.get("x", centroid[0])), float(ng.get("y", centroid[1])), float(ng.get("theta_rad", 0.0)))
            elif isinstance(ng, (list, tuple)) and len(ng) >= 3:
                nav_goal = (float(ng[0]), float(ng[1]), float(ng[2]))

        floor_id = r.get("floor_id") or r.get("floor") or "floor_0"
        floor = r.get("floor") or r.get("floor_id") or "floor_0"
        display_name = r.get("display_name") or room_name
        map_name = r.get("map_name") or "default"

        meta = RoomMetadata(
            room_name=room_name,
            display_name=display_name,
            floor_id=floor_id,
            floor=floor,
            map_name=map_name,
            polygon=poly,
            centroid=centroid,
            bounding_box=bbox,
            nav_goal=nav_goal,
            created_at=float(r.get("created_at", 0.0)),
            updated_at=float(r.get("updated_at", 0.0))
        )
        self._rooms_cache[room_name] = meta
        return meta

    def match_room_by_pose(self, x: float, y: float, floor_id: Optional[str] = None) -> Optional[str]:
        """
        Point-in-polygon check returning room name if robot pose is within boundaries.
        Conforms to PROJECT.md Contract M1 <-> M2.
        Applies bounding box filter first (O(1)), then exact PIP ray casting.
        In case of overlapping threshold / shared boundary, selects closest centroid.
        """
        candidates: List[Tuple[str, float]] = []
        with self._lock:
            for room_name, meta in self._rooms_cache.items():
                if floor_id and meta.floor_id != floor_id and meta.floor != floor_id:
                    continue
                if point_in_polygon((x, y), meta.polygon, bounding_box=meta.bounding_box, include_boundary=True):
                    dist = math.hypot(x - meta.centroid[0], y - meta.centroid[1])
                    candidates.append((room_name, dist))

        if not candidates:
            return None
        # Tie-breaker: return room whose centroid is closest
        candidates.sort(key=lambda item: item[1])
        return candidates[0][0]

    def get_room_signatures(self, room_name: str) -> Dict[str, Any]:
        """
        Returns CosPlace 512D cluster centroids/embeddings and LiDAR 360° distance signatures.
        Conforms to PROJECT.md Contract M1 <-> M2.
        Maintains LRU cache of <= 64 items (SPEC-05 Red Zone).
        """
        with self._lock:
            # Check LRU cache
            if room_name in self._signatures_cache:
                self._signatures_cache.move_to_end(room_name)
                return self._signatures_cache[room_name]

            # Query database
            row = self._db.get_signatures_for_room(room_name)
            if not row:
                return {}

            vpr_embeddings = []
            vpr_cluster = None
            vpr_centroid = None
            vpr_blob = row.get("vpr_cluster_blob")
            vpr_count = int(row.get("vpr_count") or 0)
            vpr_dim = int(row.get("vpr_dim") or 512)
            vpr_dtype = row.get("vpr_dtype") or "float16"

            if vpr_blob and vpr_count > 0:
                raw_vpr = self._db.unpack_vector_array(vpr_blob, dtype=vpr_dtype)
                if raw_vpr is not None:
                    try:
                        import numpy as np
                        np_vpr = np.asarray(raw_vpr, dtype=np.float32).reshape((vpr_count, vpr_dim))
                        vpr_cluster = np_vpr
                        vpr_embeddings = [np_vpr[i].copy() for i in range(vpr_count)]
                        
                        # Compute L2-normalized mean centroid
                        mean_vec = np.mean(np_vpr, axis=0)
                        norm = float(np.linalg.norm(mean_vec))
                        vpr_centroid = (mean_vec / norm) if norm > 1e-6 else mean_vec
                    except Exception as e:
                        logger.error(f"Error unpacking VPR cluster for {room_name}: {e}")

            lidar_sig = None
            lidar_blob = row.get("lidar_signature_blob")
            lidar_dtype = row.get("lidar_dtype") or "float32"
            if lidar_blob:
                raw_lidar = self._db.unpack_vector_array(lidar_blob, dtype=lidar_dtype)
                if raw_lidar is not None:
                    try:
                        import numpy as np
                        lidar_sig = np.asarray(raw_lidar, dtype=np.float32)
                    except Exception:
                        lidar_sig = raw_lidar

            result: Dict[str, Any] = {
                "room_name": room_name,
                "vpr_embeddings": vpr_embeddings,
                "vpr_cluster": vpr_cluster,
                "vpr_centroid": vpr_centroid,
                "vpr_count": vpr_count,
                "vpr_dim": vpr_dim,
                "lidar_signature": lidar_sig,
                "lidar_type": row.get("lidar_type", "polar_360"),
                "lidar_bins": row.get("lidar_bins", 360),
                "updated_at": row.get("updated_at", 0.0)
            }

            # Enforce LRU cache constraint <= 64
            if len(self._signatures_cache) >= 64:
                self._signatures_cache.popitem(last=False)
            self._signatures_cache[room_name] = result

            return result

    def register_room_signatures(
        self,
        room_name: str,
        vpr_embeddings: List[Any],
        lidar_signature: Any
    ) -> bool:
        """
        Persists multimodal signatures to SQLite WAL and updates in-memory cache and YAML metadata.
        Conforms to PROJECT.md Contract M1 <-> M2.
        """
        vpr_blob = None
        vpr_count = 0
        vpr_dim = 512

        if vpr_embeddings:
            try:
                import numpy as np
                stacked = np.vstack([np.asarray(v, dtype=np.float32) for v in vpr_embeddings])
                vpr_count, vpr_dim = stacked.shape
                vpr_blob = self._db.pack_vector_array(stacked, dtype="float16")
            except Exception as e:
                logger.error(f"Error packing VPR embeddings for {room_name}: {e}")
                return False

        lidar_blob = None
        lidar_bins = 0
        if lidar_signature is not None:
            try:
                import numpy as np
                np_lidar = np.asarray(lidar_signature, dtype=np.float32)
                lidar_bins = len(np_lidar)
                lidar_blob = self._db.pack_vector_array(np_lidar, dtype="float32")
            except Exception as e:
                logger.error(f"Error packing LiDAR signature for {room_name}: {e}")
                return False

        with self._lock:
            success = self._db.upsert_signatures(
                room_name=room_name,
                vpr_blob=vpr_blob,
                vpr_count=vpr_count,
                vpr_dim=vpr_dim,
                lidar_blob=lidar_blob,
                lidar_type="polar_360",
                lidar_bins=lidar_bins
            )
            if not success:
                return False

            # Invalidate signature cache entry
            self._signatures_cache.pop(room_name, None)

            # Auto-export to YAML if path configured
            if self._yaml_path:
                try:
                    self.export_to_yaml(self._yaml_path)
                except Exception as e:
                    logger.warning(f"Could not auto-export to YAML after registering signatures: {e}")

            logger.info(f"Registered signatures for '{room_name}': {vpr_count} VPR, {lidar_bins} LiDAR bins.")
            return True

    def get_nearest_room(self, x: float, y: float, floor_id: Optional[str] = None) -> Optional[Tuple[str, float]]:
        """
        Calculates distance from (x, y) to the nearest room centroid.
        Returns (room_name, distance_in_meters) or None if no rooms registered.
        """
        with self._lock:
            if not self._rooms_cache:
                return None
            nearest_name = None
            min_dist = float("inf")
            for room_name, meta in self._rooms_cache.items():
                if floor_id and meta.floor_id != floor_id and meta.floor != floor_id:
                    continue
                dist = math.hypot(x - meta.centroid[0], y - meta.centroid[1])
                if dist < min_dist:
                    min_dist = dist
                    nearest_name = room_name

            if nearest_name is None:
                return None
            return (nearest_name, min_dist)

    def insert_room(
        self,
        room_name: str,
        polygon: List[Any],
        floor_id: str = "floor_0",
        floor: Optional[str] = None,
        map_name: str = "default",
        display_name: Optional[str] = None,
        nav_goal: Optional[Any] = None
    ) -> bool:
        """
        Registers or updates a room record in SQLite WAL and the in-memory cache.
        """
        if not room_name or not polygon or len(polygon) < 3:
            logger.error(f"Invalid room insertion arguments: name={room_name}, vertices={len(polygon) if polygon else 0}")
            return False

        poly = [(float(pt[0]), float(pt[1])) for pt in polygon]
        bbox = compute_bounding_box(poly)
        centroid = compute_polygon_centroid(poly)

        parsed_nav_goal = None
        if nav_goal is not None:
            if isinstance(nav_goal, dict):
                parsed_nav_goal = (
                    float(nav_goal.get("x", centroid[0])),
                    float(nav_goal.get("y", centroid[1])),
                    float(nav_goal.get("theta_rad", 0.0))
                )
            elif isinstance(nav_goal, (list, tuple)) and len(nav_goal) >= 3:
                parsed_nav_goal = (float(nav_goal[0]), float(nav_goal[1]), float(nav_goal[2]))
        else:
            parsed_nav_goal = (centroid[0], centroid[1], 0.0)

        flr = floor or floor_id
        disp_name = display_name or room_name

        with self._lock:
            try:
                self._db.insert_room(
                    room_name=room_name,
                    centroid_x=centroid[0],
                    centroid_y=centroid[1],
                    polygon=poly,
                    floor_id=flr,
                    floor=flr,
                    display_name=disp_name,
                    map_name=map_name,
                    min_x=bbox[0],
                    min_y=bbox[1],
                    max_x=bbox[2],
                    max_y=bbox[3],
                    nav_goal=parsed_nav_goal
                )

                now = time.time()
                meta = RoomMetadata(
                    room_name=room_name,
                    display_name=disp_name,
                    floor_id=flr,
                    floor=flr,
                    map_name=map_name,
                    polygon=poly,
                    centroid=centroid,
                    bounding_box=bbox,
                    nav_goal=parsed_nav_goal,
                    created_at=now,
                    updated_at=now
                )
                self._rooms_cache[room_name] = meta

                if self._yaml_path:
                    try:
                        self.export_to_yaml(self._yaml_path)
                    except Exception as e:
                        logger.warning(f"Could not auto-export to YAML after inserting room: {e}")

                return True
            except Exception as e:
                logger.error(f"Failed to insert room '{room_name}': {e}")
                return False

    def get_room(self, room_name: str) -> Optional[RoomMetadata]:
        """Retrieves RoomMetadata from cache or database."""
        with self._lock:
            if room_name in self._rooms_cache:
                return self._rooms_cache[room_name]
            
            row = self._db.get_room_by_name(room_name)
            if row:
                return self._populate_cache_from_dict(row)
            return None

    def list_rooms(self, floor_id: Optional[str] = None) -> List[RoomMetadata]:
        """Returns all registered rooms, optionally filtered by floor."""
        with self._lock:
            rooms = list(self._rooms_cache.values())
            if floor_id:
                rooms = [r for r in rooms if r.floor_id == floor_id or r.floor == floor_id]
            return rooms

    def delete_room(self, room_name: str) -> bool:
        """Deletes a room and removes it from cache and signatures."""
        with self._lock:
            deleted = self._db.delete_room(room_name)
            self._rooms_cache.pop(room_name, None)
            self._signatures_cache.pop(room_name, None)

            if deleted and self._yaml_path:
                try:
                    self.export_to_yaml(self._yaml_path)
                except Exception as e:
                    logger.warning(f"Could not auto-export to YAML after deleting room: {e}")
            return deleted

    def export_to_yaml(self, yaml_path: Optional[str] = None) -> bool:
        """
        Exports all rooms and multimodal signatures from SQLite WAL to YAML.
        Uses atomic file replacement (.tmp + os.fsync + os.replace) to prevent
        file corruption during sudden power losses.
        """
        target_path = yaml_path or self._yaml_path
        if not target_path:
            logger.error("No YAML path specified for export.")
            return False

        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        temp_path = target_path + ".tmp"

        with self._lock:
            rooms_list = self.list_rooms()
            rooms_data = []

            for room in rooms_list:
                sig_row = self._db.get_signatures_for_room(room.room_name)
                vpr_payload: Dict[str, Any] = {"format": "inline_floats", "vector_dim": 512, "clusters": []}
                lidar_payload: Dict[str, Any] = {"format": "polar_ranges", "num_bins": 360, "bins": []}

                if sig_row:
                    if sig_row.get("vpr_cluster_blob") and sig_row.get("vpr_count", 0) > 0:
                        vpr_blob = sig_row["vpr_cluster_blob"]
                        vpr_count = sig_row["vpr_count"]
                        vpr_dim = sig_row.get("vpr_dim", 512)
                        raw_vpr = self._db.unpack_vector_array(vpr_blob, dtype=sig_row.get("vpr_dtype", "float16"))
                        if raw_vpr is not None:
                            try:
                                import numpy as np
                                np_vpr = np.asarray(raw_vpr, dtype=np.float32).reshape((vpr_count, vpr_dim))
                                vpr_payload["clusters"] = np_vpr.tolist()
                            except Exception:
                                pass

                    if sig_row.get("lidar_signature_blob"):
                        lidar_blob = sig_row["lidar_signature_blob"]
                        raw_lidar = self._db.unpack_vector_array(lidar_blob, dtype=sig_row.get("lidar_dtype", "float32"))
                        if raw_lidar is not None:
                            try:
                                import numpy as np
                                lidar_payload["bins"] = [round(float(x), 4) for x in np.asarray(raw_lidar, dtype=np.float32)]
                                lidar_payload["num_bins"] = len(lidar_payload["bins"])
                            except Exception:
                                pass

                room_dict = {
                    "room_name": room.room_name,
                    "display_name": room.display_name,
                    "floor_id": room.floor_id,
                    "polygon": [[round(pt[0], 4), round(pt[1], 4)] for pt in room.polygon],
                    "centroid": [round(room.centroid[0], 4), round(room.centroid[1], 4)],
                    "bounding_box": [round(room.bounding_box[0], 4), round(room.bounding_box[1], 4),
                                     round(room.bounding_box[2], 4), round(room.bounding_box[3], 4)],
                    "navigation_goal": {
                        "x": round(room.nav_goal[0], 4) if room.nav_goal else round(room.centroid[0], 4),
                        "y": round(room.nav_goal[1], 4) if room.nav_goal else round(room.centroid[1], 4),
                        "theta_rad": round(room.nav_goal[2], 4) if room.nav_goal else 0.0
                    },
                    "vpr_cluster_centroids": vpr_payload,
                    "lidar_signature": lidar_payload,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(room.updated_at or time.time()))
                }
                rooms_data.append(room_dict)

            document = {
                "version": "1.0",
                "building": "headquarters",
                "floor_id": "floor_0",
                "map_name": rooms_list[0].map_name if rooms_list else "default",
                "last_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "rooms": rooms_data
            }

            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(document, f, sort_keys=False, indent=2, allow_unicode=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, target_path)
                return True
            except Exception as e:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                logger.error(f"Error exporting rooms to YAML {target_path}: {e}")
                raise

    def import_from_yaml(self, yaml_path: Optional[str] = None) -> int:
        """
        Loads room metadata from YAML, computes missing geometric fields,
        upserts records into SQLite WAL, and populates the in-memory cache.
        """
        path = yaml_path or self._yaml_path
        if not path or not os.path.exists(path):
            return 0

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"Invalid YAML content in {path}: root must be a mapping")

        map_name = data.get("map_name", "default")
        rooms = data.get("rooms", [])
        if not isinstance(rooms, list):
            raise ValueError(f"Invalid 'rooms' element in {path}: must be a list")

        loaded_count = 0
        with self._lock:
            for r in rooms:
                if not isinstance(r, dict):
                    continue
                room_name = r.get("room_name")
                raw_poly = r.get("polygon")
                if not room_name or not raw_poly or len(raw_poly) < 3:
                    logger.warning(f"Skipping invalid room definition in YAML: {r}")
                    continue

                poly = [(float(pt[0]), float(pt[1])) for pt in raw_poly]
                bbox = compute_bounding_box(poly)
                centroid = compute_polygon_centroid(poly)
                display_name = r.get("display_name", room_name)
                floor_id = r.get("floor_id", "floor_0")

                nav_goal = None
                raw_goal = r.get("navigation_goal")
                if isinstance(raw_goal, dict):
                    nav_goal = (
                        float(raw_goal.get("x", centroid[0])),
                        float(raw_goal.get("y", centroid[1])),
                        float(raw_goal.get("theta_rad", 0.0))
                    )
                elif isinstance(raw_goal, (list, tuple)) and len(raw_goal) >= 3:
                    nav_goal = (float(raw_goal[0]), float(raw_goal[1]), float(raw_goal[2]))
                else:
                    nav_goal = (centroid[0], centroid[1], 0.0)

                # Upsert room in database
                self._db.insert_room(
                    room_name=room_name,
                    centroid_x=centroid[0],
                    centroid_y=centroid[1],
                    polygon=poly,
                    floor_id=floor_id,
                    floor=floor_id,
                    display_name=display_name,
                    map_name=map_name,
                    min_x=bbox[0],
                    min_y=bbox[1],
                    max_x=bbox[2],
                    max_y=bbox[3],
                    nav_goal=nav_goal
                )

                # Populate cache
                meta = RoomMetadata(
                    room_name=room_name,
                    display_name=display_name,
                    floor_id=floor_id,
                    floor=floor_id,
                    map_name=map_name,
                    polygon=poly,
                    centroid=centroid,
                    bounding_box=bbox,
                    nav_goal=nav_goal,
                    created_at=time.time(),
                    updated_at=time.time()
                )
                self._rooms_cache[room_name] = meta

                # Check for signatures in YAML
                vpr_payload = r.get("vpr_cluster_centroids", {})
                clusters = vpr_payload.get("clusters", []) if isinstance(vpr_payload, dict) else []
                lidar_payload = r.get("lidar_signature", {})
                bins = lidar_payload.get("bins", []) if isinstance(lidar_payload, dict) else []

                if clusters or bins:
                    self.register_room_signatures(
                        room_name=room_name,
                        vpr_embeddings=clusters if clusters else [],
                        lidar_signature=bins if bins else None
                    )

                loaded_count += 1

        return loaded_count

    # Alias for load_and_sync_yaml
    load_and_sync_yaml = import_from_yaml
