import numpy as np
from visualization_msgs.msg import Marker, MarkerArray
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import String

# ============================================================================
# CLASSE 3: OdometryDebugSystem
# ============================================================================
class OdometryDebugSystem:
    """Sistema completo di debug per odometria visiva"""
    
    def __init__(self, node):
        self.node = node
        self.enabled = True
        
        # Buffer per statistiche
        self.keypoints_history = []
        self.matches_history = []
        self.inlier_history = []
        self.max_history = 100
        
        # Timers per logging
        self.last_stats_log = 0
        self.stats_interval = 2.0
        
        # Publisher per debug
        self.setup_debug_publishers()
    
    def setup_debug_publishers(self):
        """Setup publisher per debug"""
        self.pub_perf_markers = self.node.create_publisher(
            MarkerArray, '/odometry/debug/performance', 10
        )
        
        self.pub_transform_debug = self.node.create_publisher(
            PoseStamped, '/odometry/debug/current_pose', 10
        )
        
        self.pub_status = self.node.create_publisher(
            String, '/odometry/debug/status', 10
        )
    
    def log_frame_stats(self, keypoints_count, matches_count, inlier_ratio, 
                       transform_valid, timestamp):
        """Registra statistiche del frame"""
        self.keypoints_history.append(keypoints_count)
        self.matches_history.append(matches_count)
        self.inlier_history.append(inlier_ratio)
        
        if len(self.keypoints_history) > self.max_history:
            self.keypoints_history.pop(0)
            self.matches_history.pop(0)
            self.inlier_history.pop(0)
        
        current_time = self.node.get_clock().now().nanoseconds / 1e9
        if current_time - self.last_stats_log >= self.stats_interval:
            self._publish_performance_stats(timestamp)
            self.last_stats_log = current_time
        
        if keypoints_count < 30:
            self.node.get_logger().warn(
                f"⚠️ FRAME {timestamp.nanoseconds}: "
                f"Keypoints={keypoints_count}, "
                f"Matches={matches_count}, "
                f"Inlier={inlier_ratio:.2f}, "
                f"Valid={'YES' if transform_valid else 'NO'}"
            )
    
    def _publish_performance_stats(self, timestamp):
        """Pubblica statistiche di performance"""
        if len(self.keypoints_history) == 0:
            return
        
        avg_keypoints = np.mean(self.keypoints_history)
        avg_matches = np.mean(self.matches_history)
        avg_inlier = np.mean(self.inlier_history)
        
        marker_array = MarkerArray()
        
        text_marker = Marker()
        if hasattr(timestamp, 'to_msg'):
             text_marker.header.stamp = timestamp.to_msg()
        else:
             text_marker.header.stamp = timestamp
             
        text_marker.header.frame_id = "odom"
        text_marker.ns = "performance_stats"
        text_marker.id = 0
        text_marker.type = Marker.TEXT_VIEW_FACING
        text_marker.action = Marker.ADD
        text_marker.pose.position.x = 0.0
        text_marker.pose.position.y = 0.0
        text_marker.pose.position.z = 2.0
        text_marker.scale.z = 0.15
        text_marker.color.a = 1.0
        text_marker.color.r = 0.0
        text_marker.color.g = 1.0
        text_marker.color.b = 0.0
        
        text_marker.text = (
            f"SuperPoint Odometry Debug\n"
            f"Avg Keypoints: {avg_keypoints:.1f}\n"
            f"Avg Matches: {avg_matches:.1f}\n"
            f"Avg Inlier Ratio: {avg_inlier:.2f}\n"
            f"Frames: {len(self.keypoints_history)}"
        )
        
        marker_array.markers.append(text_marker)
        
        bar_marker = Marker()
        bar_marker.header = text_marker.header
        bar_marker.ns = "keypoints_bar"
        bar_marker.id = 1
        bar_marker.type = Marker.CUBE
        bar_marker.action = Marker.ADD
        bar_marker.pose.position.x = -1.0
        bar_marker.pose.position.y = 0.0
        bar_marker.pose.position.z = 1.5
        bar_marker.scale.x = 0.1
        bar_marker.scale.y = 0.5
        bar_marker.scale.z = avg_keypoints / 100.0
        bar_marker.color.a = 0.7
        
        if avg_keypoints > 50:
            bar_marker.color.g = 1.0
            bar_marker.color.r = 0.0
        elif avg_keypoints > 30:
            bar_marker.color.g = 1.0
            bar_marker.color.r = 1.0
        else:
            bar_marker.color.g = 0.0
            bar_marker.color.r = 1.0
        
        marker_array.markers.append(bar_marker)
        
        self.pub_perf_markers.publish(marker_array)
        
        self.node.get_logger().info(
            f"📊 PERFORMANCE: "
            f"Keypoints={avg_keypoints:.1f}, "
            f"Matches={avg_matches:.1f}, "
            f"Inlier={avg_inlier:.2f}"
        )
    
    def publish_transform_debug(self, transform, timestamp):
        """Pubblica trasformazione corrente per debug"""
        if transform is None:
            return
        
        pose_msg = PoseStamped()
        pose_msg.header.stamp = self.node.get_clock().now().to_msg()
        pose_msg.header.frame_id = "odom"
        
        position = transform[:3, 3]
        pose_msg.pose.position.x = float(position[0])
        pose_msg.pose.position.y = float(position[1])
        pose_msg.pose.position.z = float(position[2])
        
        R = transform[:3, :3]
        qx, qy, qz, qw = self._matrix_to_quaternion(R)
        
        pose_msg.pose.orientation.x = qx
        pose_msg.pose.orientation.y = qy
        pose_msg.pose.orientation.z = qz
        pose_msg.pose.orientation.w = qw
        
        self.pub_transform_debug.publish(pose_msg)
    
    def publish_status(self, status_text, is_error=False, timestamp=None):
        """Pubblica stato del sistema"""
        if timestamp is None:
            timestamp = self.node.get_clock().now()
        
        status_msg = String()
        status_msg.data = f"[{timestamp.nanoseconds / 1e9:.1f}s] {status_text}"
        
        self.pub_status.publish(status_msg)
        
        if is_error:
            self.node.get_logger().error(status_text)
        else:
            self.node.get_logger().info(status_text)
    
    def _matrix_to_quaternion(self, R):
        """Converte matrice di rotazione in quaternione"""
        tr = np.trace(R)
        
        if tr > 0:
            S = np.sqrt(tr + 1.0) * 2
            qw = 0.25 * S
            qx = (R[2, 1] - R[1, 2]) / S
            qy = (R[0, 2] - R[2, 0]) / S
            qz = (R[1, 0] - R[0, 1]) / S
        elif (R[0, 0] > R[1, 1]) and (R[0, 0] > R[2, 2]):
            S = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
            qw = (R[2, 1] - R[1, 2]) / S
            qx = 0.25 * S
            qy = (R[0, 1] + R[1, 0]) / S
            qz = (R[0, 2] + R[2, 0]) / S
        elif R[1, 1] > R[2, 2]:
            S = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
            qw = (R[0, 2] - R[2, 0]) / S
            qx = (R[0, 1] + R[1, 0]) / S
            qy = 0.25 * S
            qz = (R[1, 2] + R[2, 1]) / S
        else:
            S = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
            qw = (R[1, 0] - R[0, 1]) / S
            qx = (R[0, 2] + R[2, 0]) / S
            qy = (R[1, 2] + R[2, 1]) / S
            qz = 0.25 * S
        
        return qx, qy, qz, qw
