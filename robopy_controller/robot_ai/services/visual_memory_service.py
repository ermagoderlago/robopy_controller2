#!/usr/bin/env python3
"""
Robot AI Services - Visual Memory Service
=========================================
Analyzes camera stream periodically to build a semantic visual memory.
Triggers only on robot motion to avoid redundancy.
Projects 2D detections to 3D for mapping.
"""

import time
import json
import math
import asyncio
import threading
import base64
import numpy as np
import cv2
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

import rclpy
from rclpy.node import Node
from rclpy.time import Time
from cv_bridge import CvBridge
from sensor_msgs.msg import Image, CameraInfo
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped, Point
from tf2_ros import Buffer, TransformListener
from tf2_geometry_msgs import do_transform_point

from ..core.config_manager import ConfigManager
from ..utils.logging_utils import get_logger
from ..services.llm_service import LLMService
from ..rag.memory_store import MemoryStore, Memory, MemoryType

class VisualMemoryService:
    """
    Service for automatic visual memory analysis.
    """

    def __init__(self, node: Node, config_manager: ConfigManager, llm_service: LLMService, memory_store: MemoryStore):
        self.node = node
        self.logger = get_logger("visual_memory")
        self.config = config_manager
        self.llm_service = llm_service
        self.memory_store = memory_store
        
        self.bridge = CvBridge()
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self.node)
        
        # State
        self._last_analysis_time = 0.0
        self._last_odom: Optional[Odometry] = None
        self._is_moving = False
        self._startup_analysis_done = False
        self._processing_lock = asyncio.Lock()
        
        # Data Buffers
        self._latest_rgb: Optional[np.ndarray] = None
        self._latest_depth: Optional[np.ndarray] = None
        self._latest_camera_info: Optional[CameraInfo] = None
        
        # Subscribers
        self.node.create_subscription(Odometry, '/odom', self._odom_callback, 10)
        self.node.create_subscription(CameraInfo, '/camera/camera_info', self._cam_info_callback, 1)
        
        # Publisher for RTAB-Map User Data (Async)
        # We publish JSON string to /rtabmap/user_data (UserData msg is defined in rtabmap_msgs, but we can use String if handled by a bridge node, 
        # but typically rtabmap_ros/UserData is used. However, simpler is to just store in MemoryStore for now 
        # and maybe publishing markers. 
        # The user requested: "inserire info nella mappa di rtabmap". 
        # RTAB-Map subscribes to /rtabmap/user_data (rtabmap_msgs/UserData).
        # We need to import rtabmap_msgs if available, or just skip publication if not.
        try:
            from rtabmap_msgs.msg import UserData
            self.userdata_pub = self.node.create_publisher(UserData, '/rtabmap/user_data', 10)
            self._has_rtabmap_msgs = True
        except ImportError:
            self.logger.warning("rtabmap_msgs not found. UserData publication disabled.")
            self._has_rtabmap_msgs = False

        self.logger.info("Visual Memory Service initialized")

    def _odom_callback(self, msg: Odometry):
        """Update motion state."""
        self._last_odom = msg
        linear_vel = msg.twist.twist.linear.x
        angular_vel = msg.twist.twist.angular.z
        
        cfg = self.config.get_config().visual_memory
        
        is_moving = (abs(linear_vel) > cfg.min_motion_threshold) or (abs(angular_vel) > cfg.min_angular_threshold)
        
        if is_moving != self._is_moving:
            self._is_moving = is_moving
            # self.logger.debug(f"Motion state changed: {is_moving}")

    def update_frame(self, rgb_frame: np.ndarray, depth_frame: Optional[np.ndarray] = None):
        """Update latest available frames from Main Node."""
        self._latest_rgb = rgb_frame
        self._latest_depth = depth_frame

    def _cam_info_callback(self, msg: CameraInfo):
        """Update camera intrinsics."""
        self._latest_camera_info = msg

    async def spin(self):
        """Main loop called by orchestrator."""
        cfg = self.config.get_config().visual_memory
        if not cfg.enabled:
            return

        now = time.time()
        
        # Check frequency
        if (now - self._last_analysis_time) < 15.0: # Hardcoded 15s for verification
            return

        # Check startup trigger
        should_analyze = False
        if cfg.startup_analysis and not self._startup_analysis_done:
            # Wait for image before triggering
            if self._latest_rgb is not None:
                self.logger.info("Triggering startup visual analysis...")
                self._startup_analysis_done = True
                should_analyze = True
        elif self._is_moving:
            should_analyze = True
        
        if not should_analyze:
            return

        # Check if we have data
        if self._latest_rgb is None:
            return

        # Acquire lock to prevent overlapping analysis
        if self._processing_lock.locked():
            return

        async with self._processing_lock:
            try:
                self._last_analysis_time = time.time()
                await self._analyze_scene()
            except Exception as e:
                self.logger.error(f"Visual analysis failed: {e}")

    async def _analyze_scene(self):
        """Perform Vision-LLM analysis."""
        self.logger.info("📸 Capturing visual memory...")
        
        # Prepare Image
        success, encoded_jpg = cv2.imencode('.jpg', self._latest_rgb)
        if not success:
            return
        
        jpg_bytes = encoded_jpg.tobytes()
        
        # Prepare Prompt
        prompt = (
            "Analyze the scene. Detect objects. "
            "Output must be valid JSON with keys: 'description', 'objects' (list of {'label': str, 'box_2d': [x1, y1, x2, y2]}). "
            "Ensure box_2d coordinates are normalized 0-1000. "
            "No markdown. No extra text."
        )
        
        # Call Gemini
        try:
            response = await self.llm_service.generate(
                prompt,
                images=[jpg_bytes], # Pass raw bytes, LLMService will handle it
            )
            
            # Parse Result
            text = response.text
            if not text:
                self.logger.warning("Gemini returned empty text response.")
                return
            
            # Cleanup JSON markdown
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].strip()
            
            # Attempt to find JSON start/end if still not clean
            if "{" in text:
                start = text.find("{")
                end = text.rfind("}") + 1
                text = text[start:end]
                
            try:
                data = json.loads(text)
                description = data.get("description", "")
                objects = data.get("objects", [])
                
                self.logger.info(f"👁️ Visual Memory: {description}")
            except json.JSONDecodeError as e:
                self.logger.error(f"Failed to parse JSON: {e}. Raw text: {text[:1000]}")
                return
            
            # Update Short-term History in RobotAI
            if hasattr(self.node, 'visual_memory_history'):
                timestamp = time.strftime('%H:%M:%S')
                entry = f"[{timestamp}] {description}"
                self.node.visual_memory_history.append(entry)
                # Keep last 5
                if len(self.node.visual_memory_history) > 5:
                    self.node.visual_memory_history.pop(0)
            
            # Save to Memory
            mem = Memory(
                id="",
                content=f"Visual Memory: {description}",
                memory_type=MemoryType.VISUAL_OBSERVATION,
                metadata={
                    "source": "visual_memory", 
                    "objects": [o['label'] for o in objects],
                    "timestamp": time.time()
                }
            )
            self.memory_store.add(mem)
            
            # Process Objects for Mapping (if Depth is available)
            if self._latest_depth is not None and self._latest_camera_info is not None:
                await self._process_objects_3d(objects, description)
                
        except Exception as e:
            self.logger.error(f"Error in Gemini analysis: {e}")

    async def _process_objects_3d(self, objects: List[Dict], description: str):
        """Project objects to 3D and publish to RTAB-Map."""
        if not self._has_rtabmap_msgs:
            return

        from rtabmap_msgs.msg import UserData
        
        height, width = self._latest_depth.shape
        
        found_objects = []
        
        for obj in objects:
            label = obj.get("label")
            box = obj.get("box_2d") # [ymin, xmin, ymax, xmax] 0-1000
            
            # Convert to [y, x, h, w] pixel coords
            ymin, xmin, ymax, xmax = box
            u = int((xmin + xmax) / 2 / 1000 * width)
            v = int((ymin + ymax) / 2 / 1000 * height)
            
            u = max(0, min(width-1, u))
            v = max(0, min(height-1, v))
            
            # Get Depth
            d = self._latest_depth[v, u] # depth in mm usually (uint16) or m (float)
            
            # Assuming depth is mm (uint16) from OAK-D
            depth_m = d / 1000.0
            
            if depth_m <= 0.1 or depth_m > 10.0:
                continue
                
            # Valid object in 3D
            # We don't need full 3D extraction here if we just pass UserData to RTAB-Map.
            # RTAB-Map UserData is just a byte array (usually typically JSON/text) attached to the current node.
            # We can send the semantic description + list of objects.
            
            found_objects.append(f"{label} at {depth_m:.1f}m")
            
        if found_objects:
            # Create UserData msg
            # UserData fields: header, match_id, user_data (bytes)
            # RTAB-Map will tag the current node with this data.
            user_data_msg = UserData()
            user_data_msg.header.stamp = self.node.get_clock().now().to_msg()
            user_data_msg.header.frame_id = "base_link" # or camera frame
            
            # Data payload
            payload = {
                "description": description,
                "objects": found_objects,
                "timestamp": time.time()
            }
            json_str = json.dumps(payload)
            user_data_msg.data = list(json_str.encode('utf-8')) # UserData.data is uint8[]
            
            self.userdata_pub.publish(user_data_msg)
            self.logger.debug(f"Published RTAB-Map UserData: {len(found_objects)} objects")

