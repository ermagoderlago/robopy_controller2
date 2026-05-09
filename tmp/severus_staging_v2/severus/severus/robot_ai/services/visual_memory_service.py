#!/usr/bin/env python3
"""
Robot AI Services - Visual Memory Service
=========================================
Analyzes camera stream periodically to build a semantic visual memory.
Triggers only on robot motion to avoid redundancy.
Projects 2D detections to 3D for mapping with O(1) dynamic spatial hashing deduplication.
Optimized for Raspberry Pi constraints.
"""

import time
import json
import asyncio
import math
import numpy as np
import cv2
import re
import uuid
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
from collections import deque

import rclpy
from rclpy.node import Node
from rclpy.time import Time
from rclpy.duration import Duration
from cv_bridge import CvBridge
from sensor_msgs.msg import Image, CameraInfo, PointCloud2, PointField
import sensor_msgs_py.point_cloud2 as pc2
from std_msgs.msg import Header
from visualization_msgs.msg import Marker, MarkerArray
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Point, PoseStamped, PointStamped
from tf2_ros import Buffer, TransformListener

# New: VQA Service from severus
from severus.srv import AskVisualQuestion

# Safe transforms: prefer pose, fallback to point if needed
try:
    from tf2_geometry_msgs import do_transform_pose
except ImportError:
    do_transform_pose = None

try:
    from tf2_geometry_msgs import do_transform_point
except ImportError:
    do_transform_point = None

from ..core.config_manager import ConfigManager
from ..utils.logging_utils import get_logger
from ..services.llm_service import LLMService
from ..services.embedding_service import EmbeddingService
from ..rag.memory_store import Memory, MemoryType
from ..rag.base_memory_store import BaseMemoryStore


class VisualMemoryService:
    """Service for automatic visual memory analysis with fast spatial caching."""

    def __init__(
        self,
        node: Node,
        config_manager: ConfigManager,
        llm_service: LLMService,
        embedding_service: EmbeddingService,
        memory_store,
        on_visual_memory: Optional[Callable[[str], None]] = None,
    ):
        self.node = node
        self.logger = get_logger("visual_memory")
        self.config = config_manager
        self.llm_service = llm_service
        self.embedding_service = embedding_service
        self.memory_store = memory_store
        self._on_visual_memory = on_visual_memory

        self.bridge = CvBridge()
        self.tf_buffer = Buffer(cache_time=Duration(seconds=30.0))
        self.tf_listener = TransformListener(self.tf_buffer, self.node)
        
        # New: PointCloud2 publisher for Nav2 Semantic Costmap
        self.pc_pub = self.node.create_publisher(PointCloud2, "/visual_objects_pc", 10)

        # New: VQA ROS Service
        self.vqa_service = self.node.create_service(
            AskVisualQuestion,
            'ask_visual_question',
            self._handle_ask_vqa
        )

        # State
        self._last_analysis_time = 0.0
        self._last_odom: Optional[Odometry] = None
        self._is_moving = False
        self._startup_analysis_done = False
        self._processing_lock = asyncio.Lock()

        # Trigger
        self._force_next_analysis = False
        self._active_search_target: Optional[str] = None

        # Data buffers
        self._latest_rgb: Optional[np.ndarray] = None
        self._latest_depth: Optional[np.ndarray] = None
        self._latest_camera_info: Optional[CameraInfo] = None
        self._latest_rgb_ts: Optional[float] = None
        self._latest_depth_ts: Optional[float] = None
        self._depth_unit_scale = 0.001

        # Keep short buffer to reduce memory footprint on Pi
        self._depth_buffer: deque = deque(maxlen=10)  # tuple(ts, depth_img, unit_scale)

        self._marker_id_counter = 0

        # O(1) spatial hash: key=(label, frame, gx, gy, gz) -> dict with 'uuid', 'state', etc.
        self._spatial_hash_cache: Dict[tuple, Dict[str, Any]] = {}
        self._spatial_grid_size = 0.5  # 50cm cells for rough bucketing
        self._spatial_cache_ttl_s = 600.0  # Increased TTL for better persistence
        self._spatial_cache_max_items = 2000

        # Subscriptions
        self.node.create_subscription(Odometry, '/odom', self._odom_callback, 10)
        self.node.create_subscription(CameraInfo, '/camera/camera_info', self._cam_info_callback, 1)
        self.node.create_subscription(Image, '/oak/stereo/image_raw', self._depth_callback, 1)
        self.node.create_subscription(Image, '/oak/stereo/image_depth', self._depth_callback, 1)

        # Publishers
        self.markers_pub = self.node.create_publisher(MarkerArray, '/ai/visual_memory/markers', 10)

        try:
            from rtabmap_msgs.msg import UserData
            self.userdata_pub = self.node.create_publisher(UserData, '/rtabmap/user_data', 10)
            self._has_rtabmap_msgs = True
        except ImportError:
            self.logger.warning("rtabmap_msgs not found. UserData publication disabled.")
            self._has_rtabmap_msgs = False

        self.logger.info("Visual Memory Service initialized (Pi optimized)")

    def _odom_callback(self, msg: Odometry):
        self._last_odom = msg
        linear_vel = msg.twist.twist.linear.x
        angular_vel = msg.twist.twist.angular.z
        cfg = self.config.get_config().visual_memory

        is_moving = (
            abs(linear_vel) > cfg.min_motion_threshold
            or abs(angular_vel) > cfg.min_angular_threshold
        )
        if is_moving != self._is_moving:
            self._is_moving = is_moving

    def update_frame(
        self,
        rgb_frame: np.ndarray,
        depth_frame: Optional[np.ndarray] = None,
        rgb_timestamp: Optional[float] = None,
    ):
        self._latest_rgb = rgb_frame
        self._latest_rgb_ts = rgb_timestamp if rgb_timestamp is not None else time.time()
        if depth_frame is not None:
            self._latest_depth = depth_frame

    def _cam_info_callback(self, msg: CameraInfo):
        self._latest_camera_info = msg

    def _depth_callback(self, msg: Image):
        try:
            depth_img = self.bridge.imgmsg_to_cv2(msg, desired_encoding='passthrough')
            self._latest_depth = depth_img

            if msg.encoding in ('16UC1', '16SC1'):
                unit_scale = 0.001  # mm -> m
            elif msg.encoding == '32FC1':
                unit_scale = 1.0    # already m
            else:
                unit_scale = self._depth_unit_scale

            self._depth_unit_scale = unit_scale
            stamp = msg.header.stamp
            ts = float(stamp.sec) + float(stamp.nanosec) / 1e9 if stamp else time.time()
            self._latest_depth_ts = ts

            self._depth_buffer.append((ts, depth_img.copy(), unit_scale))
        except Exception as e:
            self.logger.debug(f"Depth frame conversion failed: {e}")

    def force_capture(self):
        self._force_next_analysis = True
        self.logger.info("External request to force visual capture received.")

    def active_search(self, target_object: str):
        self._active_search_target = target_object
        self._force_next_analysis = True
        self.logger.info(f"🎯 Active search initiated for: {target_object}")

    async def spin(self):
        cfg = self.config.get_config().visual_memory
        if not cfg.enabled or self._latest_rgb is None:
            return

        now = time.time()

        # Check stale frames before locking
        if self._latest_rgb_ts and (now - self._latest_rgb_ts) > 3.0:
            if self._force_next_analysis:
                self.logger.debug("Stale frame detected, waiting for fresh frame before forced capture.")
            return

        should_analyze = False

        if self._force_next_analysis:
            should_analyze = True
        else:
            analysis_interval = 5.0 if self._active_search_target else float(getattr(cfg, 'analysis_interval', 15.0))
            if (now - self._last_analysis_time) >= analysis_interval:
                if cfg.startup_analysis and not self._startup_analysis_done:
                    self.logger.info("Triggering startup visual analysis...")
                    self._startup_analysis_done = True
                    should_analyze = True
                elif self._is_moving:
                    should_analyze = True

        if not should_analyze or self._processing_lock.locked():
            return

        async with self._processing_lock:
            try:
                if self._force_next_analysis:
                    self._force_next_analysis = False
                    self.logger.debug("Forced capture trigger consumed.")

                self._last_analysis_time = time.time()
                await self._analyze_scene()
            except Exception as e:
                self.logger.error(f"Visual analysis failed: {e}")

    async def _analyze_scene(self):
        self.logger.info("📸 Capturing visual memory...")

        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        success, encoded_jpg = cv2.imencode('.jpg', self._latest_rgb, encode_param)
        if not success:
            return
        jpg_bytes = encoded_jpg.tobytes()

        target = self._active_search_target
        if target:
            base_instruction = (
                f"URGENT: Analyze the scene focusing primarily on finding '{target}'. "
                "Detect it and any other prominent objects. "
            )
        else:
            base_instruction = "Analyze the scene. Detect objects. "

        prompt = (
            base_instruction
            + "Output MUST be a valid JSON with keys: "
              "'description' (str), "
              "'objects' (list of {'label': str, 'state': str, 'box_2d': [xmin, ymin, xmax, ymax]}). "
              "The 'state' must describe the object's condition "
              "(e.g., 'open', 'closed', 'empty', 'full', 'on', 'off', 'unknown'). "
              "Ensure box_2d coordinates are normalized 0-1000. "
              "Return ONLY raw JSON. No markdown formatting."
        )

        try:
            response = await asyncio.wait_for(
                self.llm_service.generate(prompt, images=[jpg_bytes], max_tokens=800),
                timeout=20.0,
            )

            # consume target only on valid response
            if target and self._active_search_target == target:
                self._active_search_target = None

            text = response.text
            if not text:
                return

            description, objects = "", []
            try:
                start_idx = text.find("{")
                end_idx = text.rfind("}")
                if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
                    json_str = text[start_idx:end_idx + 1]
                    data = json.loads(json_str)
                    description = data.get("description", "")
                    objects = data.get("objects", [])
                else:
                    raise ValueError("Incomplete JSON.")
            except (json.JSONDecodeError, ValueError) as e:
                self.logger.warning(f"JSON parse failed ({e}), using regex fallback.")
                match = re.search(r'"description"\s*:\s*"([^"]*)', text)
                if match:
                    description = match.group(1).strip()
                    objects = []
                    self.logger.warning("Regex fallback used: object list unavailable.")
                else:
                    return

            if not description:
                return

            self.logger.info(f"👁️ Visual Memory: {description}")

            if self._on_visual_memory:
                try:
                    timestamp_str = time.strftime('%H:%M:%S')
                    self._on_visual_memory(f"[{timestamp_str}] {description}")
                except Exception as e:
                    self.logger.debug(f"Callback failed: {e}")

            should_save_to_db = False
            found_objects_3d: List[Dict[str, Any]] = []
            image_capture_time = self._latest_rgb_ts or time.time()

            if self._latest_depth is not None and self._latest_camera_info is not None and objects:
                found_objects_3d = self._process_and_publish_3d(objects, description, image_capture_time)

                now = time.time()
                # TTL prune
                keys_to_delete = [
                    k for k, v in self._spatial_hash_cache.items()
                    if (now - v['last_seen']) > self._spatial_cache_ttl_s
                ]
                for k in keys_to_delete:
                    self._spatial_hash_cache.pop(k, None)

                # Hard cap prune
                if len(self._spatial_hash_cache) > self._spatial_cache_max_items:
                    oldest = sorted(self._spatial_hash_cache.items(), key=lambda kv: kv[1]['last_seen'])
                    remove_count = len(self._spatial_hash_cache) - self._spatial_cache_max_items
                    for k, _ in oldest[:remove_count]:
                        self._spatial_hash_cache.pop(k, None)

                # O(1) hash + dynamic neighbor check for object permanence
                for obj in found_objects_3d:
                    label, state, frame = obj['label'], obj['state'], obj['frame']
                    x, y, z, depth_m = obj['x'], obj['y'], obj['z'], obj['depth_m']

                    gx = round(x / self._spatial_grid_size)
                    gy = round(y / self._spatial_grid_size)
                    gz = round(z / self._spatial_grid_size)

                    dynamic_tolerance = max(0.3, depth_m * 0.1)

                    matched_key = None
                    for key in self._iter_neighbor_keys(label, frame, gx, gy, gz):
                        if key in self._spatial_hash_cache:
                            cache_obj = self._spatial_hash_cache[key]
                            dist = math.sqrt(
                                (x - cache_obj['x']) ** 2
                                + (y - cache_obj['y']) ** 2
                                + (z - cache_obj['z']) ** 2
                            )
                            if dist <= dynamic_tolerance:
                                matched_key = key
                                break

                    if matched_key is not None:
                        cache_obj = self._spatial_hash_cache[matched_key]
                        obj['uuid'] = cache_obj['uuid']  # Propagate existing UUID
                        
                        if cache_obj['state'] != state:
                            self.logger.info(f"🔄 State change for {label} [{cache_obj['uuid']}]: {cache_obj['state']} -> {state}")
                            should_save_to_db = True
                            cache_obj['state'] = state

                        # Smooth centroid update
                        cache_obj['x'] = (cache_obj['x'] + x) / 2.0
                        cache_obj['y'] = (cache_obj['y'] + y) / 2.0
                        cache_obj['z'] = (cache_obj['z'] + z) / 2.0
                        cache_obj['last_seen'] = now
                    else:
                        # New object detected! Generate UUID
                        new_uuid = str(uuid.uuid4())[:8]
                        obj['uuid'] = new_uuid
                        should_save_to_db = True
                        
                        self.logger.info(f"✨ New object found: {label} [UUID: {new_uuid}] at ({x:.2f}, {y:.2f}, {z:.2f})")
                        
                        key = (label, frame, gx, gy, gz)
                        self._spatial_hash_cache[key] = {
                            'uuid': new_uuid,
                            'state': state,
                            'x': x,
                            'y': y,
                            'z': z,
                            'last_seen': now,
                        }
            else:
                should_save_to_db = True

            if target is not None:
                should_save_to_db = True

            if should_save_to_db or not self._spatial_hash_cache:
                embedding = None
                try:
                    embedding = await asyncio.wait_for(
                        self.embedding_service.embed(f"Visual Memory: {description}"),
                        timeout=15.0,
                    )
                except Exception as e:
                    self.logger.error(f"Embedding failed: {e}")

                # Prepare comprehensive spatial metadata
                primary_obj = found_objects_3d[0] if found_objects_3d else {}
                obj_metadata = [
                    {
                        "label": o['label'], 
                        "state": o.get('state', 'unknown'), 
                        "uuid": o.get('uuid', ''),
                        "x": o.get('x', 0.0),
                        "y": o.get('y', 0.0), 
                        "z": o.get('z', 0.0),
                        "frame": o.get('frame', 'map')
                    } 
                    for o in found_objects_3d
                ]

                mem = Memory(
                    id=primary_obj.get('uuid', ""),
                    content=f"Visual Observation: {description}",
                    memory_type=MemoryType.VISUAL_OBSERVATION,
                    embedding=embedding,
                    metadata={
                        "source": "visual_memory",
                        "objects": json.dumps(obj_metadata),
                        "timestamp": datetime.now().isoformat(),
                        "rgb_ts": image_capture_time,
                        "primary_uuid": primary_obj.get('uuid', ""),
                        "location": f"({primary_obj.get('x', 0):.2f}, {primary_obj.get('y', 0):.2f}, {primary_obj.get('z', 0):.2f}) in {primary_obj.get('frame', 'map')}"
                    },
                )
                
                # Use UUID-based upsert logic
                if mem.id and self.memory_store.get_sync(mem.id):
                    self.memory_store.update_sync(mem)
                    self.logger.info(f"📍 Memory UPDATED for {primary_obj.get('label')} [UUID: {mem.id}]")
                else:
                    self.memory_store.add_sync(mem)
                    self.logger.info(f"🆕 Memory SAVED for {primary_obj.get('label')} [UUID: {mem.id or 'N/A'}]")
            else:
                self.logger.info("♻️ Spatial deduplication: skipped Vector DB save.")

        except asyncio.TimeoutError:
            self.logger.error("LLM generation timed out. Aborting analysis for this frame.")
        except Exception as e:
            self.logger.error(f"Error in Gemini analysis: {e}")

    def _iter_neighbor_keys(self, label: str, frame: str, gx: int, gy: int, gz: int):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    yield (label, frame, gx + dx, gy + dy, gz + dz)

    def _find_closest_depth(self, target_ts: Optional[float]):
        if target_ts is None or not self._depth_buffer:
            return self._latest_depth, self._depth_unit_scale
        closest = min(self._depth_buffer, key=lambda x: abs(x[0] - target_ts))
        return closest[1], closest[2]

    def _sample_depth_m(
        self,
        depth_img: np.ndarray,
        u: int,
        v: int,
        unit_scale: float,
        radius: int = 5,
    ) -> Optional[float]:
        h, w = depth_img.shape
        y0, y1 = max(0, v - radius), min(h, v + radius + 1)
        x0, x1 = max(0, u - radius), min(w, u + radius + 1)
        patch = depth_img[y0:y1, x0:x1]
        valid = patch[np.isfinite(patch)]
        valid = valid[valid > 0]
        if valid.size == 0:
            return None
        return float(np.median(valid)) * unit_scale

    def _project_to_camera_frame(
        self,
        x_optical: float,
        y_optical: float,
        z_optical: float,
        cam_frame: str,
    ) -> Point:
        if 'optical' in (cam_frame or '').lower():
            return Point(x=float(x_optical), y=float(y_optical), z=float(z_optical))
        return Point(x=float(z_optical), y=float(-x_optical), z=float(-y_optical))

    def _process_and_publish_3d(
        self,
        objects: List[Dict],
        description: str,
        rgb_ts: Optional[float],
    ) -> List[Dict]:
        depth_img, depth_unit_scale = self._find_closest_depth(rgb_ts)

        # Guard clause for missing/incomplete intrinsics
        k = getattr(self._latest_camera_info, 'k', []) if self._latest_camera_info else []
        if depth_img is None or not k or len(k) < 9 or rgb_ts is None:
            return []

        fx, fy, cx, cy = k[0], k[4], k[2], k[5]
        if fx == 0 or fy == 0:
            self.logger.warning("Invalid camera intrinsics (fx or fy is 0). Skipping 3D projection.")
            return []

        sec = int(rgb_ts)
        nanosec = int((rgb_ts - sec) * 1e9)
        ros_time = Time(seconds=sec, nanoseconds=nanosec)
        ros_time_msg = ros_time.to_msg()

        height, width = depth_img.shape

        found_objects = []
        marker_array = MarkerArray()
        map_frame = 'map'
        cam_frame = self._latest_camera_info.header.frame_id or 'camera_link'

        tf_map_from_cam = None
        try:
            tf_map_from_cam = self.tf_buffer.lookup_transform(
                map_frame,
                cam_frame,
                ros_time,
                timeout=Duration(seconds=0.5),
            )
        except Exception as e:
            self.logger.debug(f"Could not transform at image time: {e}")

        for obj in objects:
            label = obj.get("label", "object")
            state = obj.get("state", "unknown")
            box = obj.get("box_2d")
            if not box or len(box) != 4:
                continue

            xmin, ymin, xmax, ymax = box
            u = int((xmin + xmax) / 2 / 1000 * width)
            v = int((ymin + ymax) / 2 / 1000 * height)
            u = max(0, min(width - 1, u))
            v = max(0, min(height - 1, v))

            depth_m = self._sample_depth_m(depth_img, u, v, depth_unit_scale, radius=5)
            if depth_m is None or depth_m <= 0.1 or depth_m > 10.0:
                continue

            x_opt = (u - cx) * depth_m / fx
            y_opt = (v - cy) * depth_m / fy
            z_opt = depth_m

            width_px = max(1, xmax - xmin) / 1000.0 * width
            height_px = max(1, ymax - ymin) / 1000.0 * height
            width_3d = (width_px * depth_m) / fx
            height_3d = (height_px * depth_m) / fy
            depth_3d = min(max(width_3d, 0.1), 0.5)

            frame_for_marker = cam_frame
            pos_x = pos_y = pos_z = 0.0
            qx = qy = qz = 0.0
            qw = 1.0

            if tf_map_from_cam is not None and do_transform_pose is not None:
                pose_in = PoseStamped()
                pose_in.header.frame_id = cam_frame
                pose_in.header.stamp = ros_time_msg
                pose_in.pose.position = self._project_to_camera_frame(x_opt, y_opt, z_opt, cam_frame)
                pose_in.pose.orientation.w = 1.0
                try:
                    pose_out = do_transform_pose(pose_in, tf_map_from_cam)
                    frame_for_marker = map_frame
                    pos_x = pose_out.pose.position.x
                    pos_y = pose_out.pose.position.y
                    pos_z = pose_out.pose.position.z
                    qx = pose_out.pose.orientation.x
                    qy = pose_out.pose.orientation.y
                    qz = pose_out.pose.orientation.z
                    qw = pose_out.pose.orientation.w
                except Exception as e:
                    self.logger.debug(f"Pose transform failed: {e}")
                    p = self._project_to_camera_frame(x_opt, y_opt, z_opt, cam_frame)
                    pos_x, pos_y, pos_z = p.x, p.y, p.z
            elif tf_map_from_cam is not None and do_transform_point is not None:
                pt_in = PointStamped()
                pt_in.header.frame_id = cam_frame
                pt_in.header.stamp = ros_time_msg
                pt_in.point = self._project_to_camera_frame(x_opt, y_opt, z_opt, cam_frame)
                try:
                    pt_out = do_transform_point(pt_in, tf_map_from_cam)
                    frame_for_marker = map_frame
                    pos_x, pos_y, pos_z = pt_out.point.x, pt_out.point.y, pt_out.point.z
                except Exception as e:
                    self.logger.debug(f"Point transform failed: {e}")
                    p = self._project_to_camera_frame(x_opt, y_opt, z_opt, cam_frame)
                    pos_x, pos_y, pos_z = p.x, p.y, p.z
            else:
                p = self._project_to_camera_frame(x_opt, y_opt, z_opt, cam_frame)
                pos_x, pos_y, pos_z = p.x, p.y, p.z

            found_objects.append({
                "label": label,
                "state": state,
                "depth_m": round(depth_m, 2),
                "x": round(pos_x, 3),
                "y": round(pos_y, 3),
                "z": round(pos_z, 3),
                "frame": frame_for_marker,
                "width_3d": round(width_3d, 3),
                "height_3d": round(height_3d, 3),
            })

            current_id = self._marker_id_counter
            self._marker_id_counter += 1

            # shape marker
            cube_marker = Marker()
            cube_marker.header.stamp = ros_time_msg
            cube_marker.header.frame_id = frame_for_marker
            cube_marker.ns = 'visual_objects_shapes'
            cube_marker.id = current_id
            cube_marker.type = Marker.CUBE
            cube_marker.action = Marker.ADD
            cube_marker.pose.position.x = pos_x
            cube_marker.pose.position.y = pos_y
            cube_marker.pose.position.z = pos_z
            cube_marker.pose.orientation.x = qx
            cube_marker.pose.orientation.y = qy
            cube_marker.pose.orientation.z = qz
            cube_marker.pose.orientation.w = qw

            if 'optical' in frame_for_marker.lower():
                cube_marker.scale.x = float(width_3d)
                cube_marker.scale.y = float(height_3d)
                cube_marker.scale.z = float(depth_3d)
            else:
                cube_marker.scale.x = float(depth_3d)
                cube_marker.scale.y = float(width_3d)
                cube_marker.scale.z = float(height_3d)

            cube_marker.color.r = 0.1
            cube_marker.color.g = 0.7
            cube_marker.color.b = 0.9
            cube_marker.color.a = 0.5
            cube_marker.lifetime.sec = 0
            marker_array.markers.append(cube_marker)

            # text marker
            text_marker = Marker()
            text_marker.header.stamp = ros_time_msg
            text_marker.header.frame_id = frame_for_marker
            text_marker.ns = 'visual_objects_labels'
            text_marker.id = current_id
            text_marker.type = Marker.TEXT_VIEW_FACING
            text_marker.action = Marker.ADD
            text_marker.pose.position.x = pos_x
            text_marker.pose.position.y = pos_y
            text_marker.pose.position.z = pos_z + (float(height_3d) / 2.0) + 0.15
            text_marker.pose.orientation.w = 1.0
            text_marker.scale.x = 0.0
            text_marker.scale.y = 0.0
            text_marker.scale.z = 0.15
            text_marker.color.r = 1.0
            text_marker.color.g = 1.0
            text_marker.color.b = 1.0
            text_marker.color.a = 1.0
            text_marker.text = f"{label} [{state}]"
            text_marker.lifetime.sec = 0
            marker_array.markers.append(text_marker)

        if marker_array.markers:
            self.markers_pub.publish(marker_array)

        # Publish semantic PointCloud2 for Nav2
        if found_objects:
            pc_points = []
            for obj in found_objects:
                px, py, pz = obj['x'], obj['y'], obj['z']
                # Add center point
                pc_points.append([px, py, pz])
                
                # Add 8 corner points based on 3D dimensions (simplified)
                # Note: This assumes axis-aligned for costmap projection
                # which is usually sufficient for 2D avoidance.
                dw = obj.get('width_3d', 0.2) / 2.0
                dh = obj.get('height_3d', 0.2) / 2.0
                dd = obj.get('depth_3d', 0.2) / 2.0
                
                for dx in [-dd, dd]:
                    for dy in [-dw, dw]:
                        for dz in [-dh, dh]:
                            pc_points.append([px + dx, py + dy, pz + dz])

            if pc_points:
                header = Header()
                header.stamp = ros_time_msg
                header.frame_id = frame_for_marker
                
                fields = [
                    PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
                    PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
                    PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
                ]
                
                pc_msg = pc2.create_cloud_xyz32(header, pc_points)
                self.pc_pub.publish(pc_msg)
                self.logger.debug(f"Published PointCloud2 with {len(pc_points)} points for Nav2")

        if found_objects and self._has_rtabmap_msgs:
            from rtabmap_msgs.msg import UserData
            user_data_msg = UserData()
            user_data_msg.header.stamp = ros_time_msg
            user_data_msg.header.frame_id = map_frame if tf_map_from_cam is not None else cam_frame
            payload = {
                "description": description,
                "objects": found_objects,
                "timestamp": rgb_ts,
            }
            json_str = json.dumps(payload)
            user_data_msg.data = list(json_str.encode('utf-8'))
            self.userdata_pub.publish(user_data_msg)

        return found_objects
    async def _handle_ask_vqa(self, request, response):
        """Handle synchronous VQA request for active search."""
        self.logger.info(f"🔍 Active Search Triggered: {request.question}")

        if self._latest_rgb is None:
            response.success = False
            response.answer = "Errore: Camera non disponibile."
            return response

        # Compress to JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        success, encoded_jpg = cv2.imencode('.jpg', self._latest_rgb, encode_param)
        if not success:
            response.success = False
            response.answer = "Errore: Compressione immagine fallita."
            return response

        jpg_bytes = encoded_jpg.tobytes()

        # Build Focused Prompt
        prompt = (
            f"FOCUSED ANALYSIS: {request.question}\n"
            "Analyze the image specifically for the question above. "
            "Be direct, concise, and provide structural details if requested. "
            "Avoid general descriptions unless needed."
        )

        try:
            # Synchronous-like wait for async LLM service
            llm_response = await asyncio.wait_for(
                self.llm_service.generate(prompt, images=[jpg_bytes], max_tokens=1000),
                timeout=30.0
            )
            response.answer = llm_response.text
            response.success = True
            self.logger.info(f"✅ VQA Result: {response.answer[:50]}...")
        except Exception as e:
            self.logger.error(f"❌ VQA Failed: {e}")
            response.success = False
            response.answer = f"Errore API/Timeout: {e}"

        return response
