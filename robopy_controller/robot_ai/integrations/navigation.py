"""
Robot AI Integrations - Navigation
===================================
Nav2 integration for robot navigation.
"""

import asyncio
import math
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

try:
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
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
    
    def __init__(self, node: 'Node' = None, config_manager: ConfigManager = None):
        self.logger = get_logger("nav_client")
        self.config = config_manager or ConfigManager()
        self.ai_config = self.config.get_config()
        self.event_bus = EventBus()
        
        # ROS node
        self._node = node
        self._action_client = None
        
        # State
        self._status = NavigationStatus.IDLE
        self._current_goal = None
        self._current_goal_handle = None
        
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
        if not self._action_client.wait_for_server(timeout_sec=5.0):
            raise NavigationError("Navigate action server not available")
        
        # Create goal
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = frame_id
        goal.pose.header.stamp = self._node.get_clock().now().to_msg()
        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        goal.pose.pose.position.z = 0.0
        goal.pose.pose.orientation.z = math.sin(theta / 2)
        goal.pose.pose.orientation.w = math.cos(theta / 2)
        
        self._current_goal = (x, y, theta)
        self._status = NavigationStatus.NAVIGATING
        
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
