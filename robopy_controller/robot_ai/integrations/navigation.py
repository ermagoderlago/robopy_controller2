"""
Robot AI Integrations - Navigation
===================================
Nav2 integration for robot navigation.
"""

import asyncio
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

try:
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
    from nav2_msgs.action import NavigateToPose
    from action_msgs.msg import GoalStatus
    HAS_ROS = True
except ImportError:
    HAS_ROS = False

from ..core.config_manager import ConfigManager
from ..core.exceptions import NavigationError
from ..core.event_bus import EventBus, EventType
from ..utils.logging_utils import get_logger


class NavigationStatus(Enum):
    """Navigation status."""
    IDLE = "idle"
    NAVIGATING = "navigating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class Waypoint:
    """A semantic waypoint."""
    name: str
    x: float
    y: float
    theta: float = 0.0
    frame_id: str = "map"
    aliases: List[str] = field(default_factory=list)
    
    def to_pose(self) -> 'PoseStamped':
        """Convert to ROS PoseStamped."""
        if not HAS_ROS:
            raise NavigationError("ROS 2 not available")
        
        pose = PoseStamped()
        pose.header.frame_id = self.frame_id
        pose.pose.position.x = self.x
        pose.pose.position.y = self.y
        pose.pose.position.z = 0.0
        
        # Convert theta to quaternion
        pose.pose.orientation.z = math.sin(self.theta / 2)
        pose.pose.orientation.w = math.cos(self.theta / 2)
        
        return pose


class NavigationClient:
    """
    Nav2 integration client.
    
    Features:
    - Semantic waypoint navigation
    - Pose-based navigation
    - Navigation status tracking
    - Goal cancellation
    - Feedback callbacks
    
    Usage:
        nav = NavigationClient(ros_node)
        
        # Navigate to waypoint
        await nav.navigate_to_waypoint("cucina")
        
        # Navigate to pose
        await nav.navigate_to_pose(2.5, 1.0, 0.0)
        
        # Cancel
        await nav.cancel_navigation()
    """
    
    def __init__(self, node: 'Node' = None, config_manager: ConfigManager = None, cmd_vel_pub=None):
        self.logger = get_logger("nav_client")
        self.config = config_manager or ConfigManager()
        self.ai_config = self.config.get_config()
        self.event_bus = EventBus()
        
        # ROS node
        self._node = node
        self._action_client = None
        self._cmd_vel_pub = cmd_vel_pub  # For bootstrap mapping
        
        # State
        self._status = NavigationStatus.IDLE
        self._current_goal = None
        self._current_goal_handle = None
        self._exploration_task = None
        self._is_exploring = False
        
        # Stasi (Stuck) Detection
        self._stasi_detected = False
        self._last_stasi_pos = None
        self._stasi_start_time = None
        self._stasi_threshold_s = 10.0
        self._stasi_dist_threshold = 0.05
        
        # Waypoints
        self._waypoints: Dict[str, Waypoint] = {}
        self._load_default_waypoints()
        
        # Callbacks
        self._feedback_callbacks: List[Callable] = []
        self._completion_callbacks: List[Callable] = []
        
        if HAS_ROS and node:
            self._setup_ros()
        
        self.logger.info("Navigation client initialized")
    
    def _setup_ros(self) -> None:
        """Set up ROS action client."""
        self._action_client = ActionClient(
            self._node,
            NavigateToPose,
            'navigate_to_pose'
        )
    
    def _load_default_waypoints(self) -> None:
        """Load default semantic waypoints."""
        default_waypoints = [
            Waypoint("cucina", 2.5, 1.0, aliases=["kitchen"]),
            Waypoint("soggiorno", 0.0, 0.0, aliases=["salotto", "living"]),
            Waypoint("camera", 4.0, 3.0, aliases=["bedroom", "letto"]),
            Waypoint("bagno", 3.0, 2.5, aliases=["bathroom"]),
            Waypoint("studio", 1.5, 3.5, aliases=["ufficio", "office"]),
            Waypoint("ingresso", 0.0, 2.0, aliases=["entrata", "entrance"]),
            Waypoint("base", 0.0, 0.0, aliases=["home", "ricarica", "dock"]),
        ]
        
        for wp in default_waypoints:
            self._waypoints[wp.name] = wp
    
    @property
    def status(self) -> NavigationStatus:
        """Get current navigation status."""
        return self._status
    
    @property
    def is_stuck(self) -> bool:
        """Check if robot is stuck (stasi)."""
        return self._stasi_detected

    @property
    def is_navigating(self) -> bool:
        """Check if currently navigating."""
        return self._status == NavigationStatus.NAVIGATING
    
    def get_waypoint(self, name: str) -> Optional[Waypoint]:
        """
        Get waypoint by name or alias.
        
        Args:
            name: Waypoint name or alias
            
        Returns:
            Waypoint or None
        """
        name_lower = name.lower()
        
        # Check direct name match
        if name_lower in self._waypoints:
            return self._waypoints[name_lower]
        
        # Check aliases
        for wp in self._waypoints.values():
            if name_lower in [a.lower() for a in wp.aliases]:
                return wp
        
        return None
    
    def add_waypoint(self, waypoint: Waypoint) -> None:
        """Add a new waypoint."""
        self._waypoints[waypoint.name.lower()] = waypoint
        self.logger.info(f"Added waypoint: {waypoint.name}")
    
    def get_all_waypoints(self) -> List[Waypoint]:
        """Get all waypoints."""
        return list(self._waypoints.values())
    
    async def navigate_to_waypoint(self, name: str) -> bool:
        """
        Navigate to a semantic waypoint.
        
        Args:
            name: Waypoint name or alias
            
        Returns:
            True if navigation succeeded
        """
        waypoint = self.get_waypoint(name)
        if not waypoint:
            raise NavigationError(f"Unknown waypoint: {name}")
        
        self.logger.info(f"Navigating to waypoint: {name}")
        return await self.navigate_to_pose(
            waypoint.x, 
            waypoint.y, 
            waypoint.theta,
            waypoint.frame_id
        )
    
    async def navigate_to_pose(
        self,
        x: float,
        y: float,
        theta: float = 0.0,
        frame_id: str = "map"
    ) -> bool:
        """
        Navigate to a specific pose.
        
        Args:
            x: X position
            y: Y position
            theta: Orientation (radians)
            frame_id: Reference frame
            
        Returns:
            True if navigation succeeded
        """
        if not HAS_ROS or not self._action_client:
            self.logger.warning("ROS not available, simulating navigation")
            self._status = NavigationStatus.NAVIGATING
            
            # Publish event
            self.event_bus.publish(EventType.NAVIGATION_STARTED, {
                "x": x, "y": y, "theta": theta
            })
            
            # Simulate navigation time
            await asyncio.sleep(2.0)
            
            self._status = NavigationStatus.SUCCEEDED
            self.event_bus.publish(EventType.NAVIGATION_COMPLETED, {
                "x": x, "y": y, "success": True
            })
            
            return True
        
        # Wait for action server
        if not self._action_client.wait_for_server(timeout_sec=15.0):
            raise NavigationError("Navigate action server not available")
        
        # Create goal
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = frame_id
        # Set goal stamp to exactly CURRENT time
        goal.pose.header.stamp = self._node.get_clock().now().to_msg()
        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        goal.pose.pose.position.z = 0.0
        goal.pose.pose.orientation.z = math.sin(theta / 2)
        goal.pose.pose.orientation.w = math.cos(theta / 2)
        
        self._current_goal = (x, y, theta)
        self._status = NavigationStatus.NAVIGATING
        self._stasi_detected = False
        self._last_stasi_pos = None
        self._stasi_start_time = None
        
        # Publish event
        self.event_bus.publish(EventType.NAVIGATION_STARTED, {
            "x": x, "y": y, "theta": theta
        })
        
        self.logger.info(f"Sending navigation goal: ({x}, {y}, {theta})")
        
        # Send goal
        send_goal_future = self._action_client.send_goal_async(
            goal,
            feedback_callback=self._feedback_callback
        )
        
        goal_handle = await send_goal_future
        
        if not goal_handle.accepted:
            self._status = NavigationStatus.FAILED
            self.event_bus.publish(EventType.NAVIGATION_FAILED, {
                "reason": "Goal rejected"
            })
            raise NavigationError("Navigation goal rejected")
        
        self._current_goal_handle = goal_handle
        
        # Wait for result
        result_future = goal_handle.get_result_async()
        result = await result_future
        
        if result.status == GoalStatus.STATUS_SUCCEEDED:
            self._status = NavigationStatus.SUCCEEDED
            self.event_bus.publish(EventType.NAVIGATION_COMPLETED, {
                "x": x, "y": y, "success": True
            })
            self.logger.info("Navigation succeeded")
            return True
        else:
            self._status = NavigationStatus.FAILED
            self.event_bus.publish(EventType.NAVIGATION_FAILED, {
                "status": result.status
            })
            self.logger.warning(f"Navigation failed with status: {result.status}")
            return False
    
    async def cancel_navigation(self) -> bool:
        """
        Cancel current navigation.
        
        Returns:
            True if cancelled successfully
        """
        if not self.is_navigating:
            return False
        
        if self._current_goal_handle and HAS_ROS:
            cancel_future = self._current_goal_handle.cancel_goal_async()
            await cancel_future
        
        self._status = NavigationStatus.CANCELED
        self._current_goal = None
        self._current_goal_handle = None
        
        self.logger.info("Navigation cancelled")
        return True
    
    async def _bootstrap_mapping(self) -> None:
        """
        Bootstrap mapping: move forward ~30cm then rotate 360° via direct cmd_vel.
        
        This builds a minimal occupancy map in RTAB-Map before invoking Nav2,
        preventing planner_server SIGSEGV on empty costmaps.
        """
        if not self._cmd_vel_pub:
            self.logger.warning("No cmd_vel publisher, skipping bootstrap mapping")
            await asyncio.sleep(3.0)  # Fallback: just wait
            return
        
        self.logger.info("🗺️ Bootstrap mapping: forward 30cm...")
        # Phase 1: Move forward ~30cm (0.15 m/s × 2s = 0.30m)
        await self._cmd_vel_send(linear_x=0.15, angular_z=0.0, duration=2.0)
        
        # Pause to let RTAB-Map process frames
        await asyncio.sleep(1.0)
        
        self.logger.info("🗺️ Bootstrap mapping: 360° rotation...")
        # Phase 2: Rotate 360° (0.5 rad/s × 12.6s ≈ 2π rad)
        await self._cmd_vel_send(linear_x=0.0, angular_z=0.5, duration=12.6)
        
        # Stop and let costmaps settle
        await self._cmd_vel_send(linear_x=0.0, angular_z=0.0, duration=0.3)
        self.logger.info("🗺️ Bootstrap mapping complete! Waiting 3s for costmaps...")
        await asyncio.sleep(3.0)
    
    async def _cmd_vel_send(self, linear_x: float, angular_z: float, duration: float) -> None:
        """Publish cmd_vel at 10Hz for the given duration."""
        if not HAS_ROS or not self._cmd_vel_pub:
            await asyncio.sleep(duration)
            return
        
        twist = Twist()
        twist.linear.x = linear_x
        twist.angular.z = angular_z
        
        t_end = time.monotonic() + duration
        while time.monotonic() < t_end and self._is_exploring:
            self._cmd_vel_pub.publish(twist)
            await asyncio.sleep(0.1)  # 10Hz
        
        # Stop
        self._cmd_vel_pub.publish(Twist())
    
    async def start_exploration(self, radius: float = 2.0, max_points: int = 15) -> bool:
        """
        Start autonomous random walk exploration.
        
        Args:
            radius: Maximum meters away from starting point
            max_points: Max points to visit
        """
        if self._is_exploring:
            self.logger.warning("Exploration already active")
            return False
            
        self._is_exploring = True
        
        self.logger.info("Starting autonomous exploration...")
                
        # Start background loop
        self._exploration_task = asyncio.create_task(self._explore_loop(radius, max_points))
        return True
        
    async def stop_exploration(self) -> None:
        """Stop the autonomous exploration."""
        if self._is_exploring:
            self._is_exploring = False
            if self._exploration_task:
                self._exploration_task.cancel()
                self._exploration_task = None
            await self.cancel_navigation()
            self.logger.info("Exploration stopped")

    async def _explore_loop(self, radius: float, max_points: int) -> None:
        """Background loop to generate random Nav2 goals for exploration."""
        import random
        
        self.logger.info(f"Started Nav2 autonomous exploration (max {max_points} points)")
        visited_points = 0
        failure_count = 0
        
        while self._is_exploring and visited_points < max_points and failure_count < 10:
            self.logger.info(f"Exploration Phase {visited_points + 1}/{max_points}")
            
            # Generate random goal within radius (assuming start is near 0,0)
            # Min radius 0.5 to avoid points too close
            dist = random.uniform(0.5, radius)
            angle = random.uniform(-math.pi, math.pi)
            
            x = dist * math.cos(angle)
            y = dist * math.sin(angle)
            theta = random.uniform(-math.pi, math.pi)
            
            self.logger.info(f"-> Navigating to random pose: x={x:.2f}, y={y:.2f}, theta={theta:.2f}")
            
            # Send the goal to Nav2
            success = await self.navigate_to_pose(x, y, theta)
            
            if success:
                visited_points += 1
                failure_count = 0  # Reset on success
                self.logger.info("-> Reached exploration point successfully.")
            else:
                failure_count += 1
                self.logger.warning(f"-> Failed to reach point. Failure {failure_count}/10")
            
            if not self._is_exploring:
                break
                
            # Wait for costmap/map to update
            self.logger.info("-> Updating map...")
            await asyncio.sleep(2.0)
                
        self._is_exploring = False
        self.logger.info(f"Exploration task finished ({visited_points} phases completed)")
    
    def _feedback_callback(self, feedback_msg) -> None:
        """Handle navigation feedback."""
        feedback = feedback_msg.feedback
        
        # Current pose
        current_pose = feedback.current_pose.pose
        
        # Distance remaining
        distance = feedback.distance_remaining if hasattr(feedback, 'distance_remaining') else 0
        
        # Notify callbacks
        feedback_data = {
            "x": current_pose.position.x,
            "y": current_pose.position.y,
            "distance_remaining": distance
        }
        
        for callback in self._feedback_callbacks:
            try:
                callback(feedback_data)
            except Exception as e:
                self.logger.error(f"Error in feedback callback: {e}")

        # Stasi Detection Logic
        if self.is_navigating:
            now = asyncio.get_event_loop().time()
            curr_pos = (feedback_data["x"], feedback_data["y"])
            
            if self._last_stasi_pos is None:
                self._last_stasi_pos = curr_pos
                self._stasi_start_time = now
            else:
                dist_moved = math.hypot(curr_pos[0] - self._last_stasi_pos[0], 
                                        curr_pos[1] - self._last_stasi_pos[1])
                
                if dist_moved > self._stasi_dist_threshold:
                    # Robot is moving
                    self._last_stasi_pos = curr_pos
                    self._stasi_start_time = now
                    if self._stasi_detected:
                        self.logger.info("Robot resumed movement, stasi cleared")
                        self._stasi_detected = False
                else:
                    # Robot is stationary or oscillating slightly
                    if not self._stasi_detected and (now - self._stasi_start_time) > self._stasi_threshold_s:
                        self._stasi_detected = True
                        self.logger.warning(f"STASI DETECTED: Robot stuck at {curr_pos} for >{self._stasi_threshold_s}s")
                        self.event_bus.publish(EventType.NAVIGATION_FAILED, {
                            "reason": "stasi_detected",
                            "pos": curr_pos
                        })
    
    def on_feedback(self, callback: Callable) -> None:
        """Register feedback callback."""
        self._feedback_callbacks.append(callback)
    
    def on_completion(self, callback: Callable) -> None:
        """Register completion callback."""
        self._completion_callbacks.append(callback)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get navigation statistics."""
        return {
            "status": self._status.value,
            "is_navigating": self.is_navigating,
            "current_goal": self._current_goal,
            "waypoints_count": len(self._waypoints),
            "has_ros": HAS_ROS and self._action_client is not None
        }
