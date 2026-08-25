#!/usr/bin/env python3
"""
Semantic Costmap Injector
=========================
ROS 2 Python node that transforms 3D semantic obstacles from Hailo VLM
and injects them as 2D grid cells for Nav2 Costmap.

Includes dynamic temporal decay to prevent ghost obstacles.

Version: 01.00.00
"""

import math
import time
import threading
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import Header
from sensor_msgs.msg import PointCloud2, PointField, Image
import sensor_msgs_py.point_cloud2 as pc2
from geometry_msgs.msg import Point, PointStamped
from visualization_msgs.msg import Marker, MarkerArray

# TF2 imports
import tf2_ros
from tf2_geometry_msgs import PointStamped as TFPointStamped

# Custom package messages
from robopy_controller.msg import SemanticObject, SemanticObjectArray


class SemanticCostmapInjector(Node):
    def __init__(self):
        super().__init__('semantic_costmap_injector')
        self.get_logger().info("Inizializzazione semantic_costmap_injector...")

        # Parameters
        self.declare_parameter('decay_time_sec', 5.0)
        self.declare_parameter('min_obstacle_confidence', 0.35)  # [FIX] Allineato a yolo_conf_thresh (0.35) per mostrare i punti su Foxglove
        self.declare_parameter('inflation_radius_m', 0.15)
        self.declare_parameter('costmap_frame', 'map')
        self.declare_parameter('grid_resolution', 0.05)  # 5 cm grid resolution
        self.declare_parameter('max_active_obstacles', 500)  # [FM-MEM-011] RAM cap su Pi 5 4GB

        # Negative Obstacle Detection Parameters (FM-NAV-009)
        self.declare_parameter('enable_negative_obstacles', True)
        self.declare_parameter('depth_topic', '/camera/depth/image_raw')
        self.declare_parameter('min_drop_height_m', 0.15)
        self.declare_parameter('max_floor_distance_m', 2.5)

        # Configurable obstacle classes whitelist [FM-SEM-001]
        self.declare_parameter('obstacle_classes', [
            'obstacle', 'person', 'furniture', 'sedia', 'tavolo', 'divano', 'letto',
            'chair', 'couch', 'bed', 'dining table', 'tv', 'refrigerator', 'toilet',
            'sink', 'bottiglia', 'tazza', 'laptop', 'cellulare', 'pianta', 'porta',
            'door', 'wall', 'muro', 'ostacolo', 'persona', 'mobile'
        ])

        self.decay_time = float(self.get_parameter('decay_time_sec').value)
        self.min_confidence = float(self.get_parameter('min_obstacle_confidence').value)
        self.inflation_radius = float(self.get_parameter('inflation_radius_m').value)
        self.costmap_frame = self.get_parameter('costmap_frame').value
        self.grid_res = float(self.get_parameter('grid_resolution').value)
        self.max_obstacles = int(self.get_parameter('max_active_obstacles').value)

        self.enable_negative_obstacles = bool(self.get_parameter('enable_negative_obstacles').value)
        self.depth_topic = self.get_parameter('depth_topic').value
        self.min_drop_height = float(self.get_parameter('min_drop_height_m').value)
        self.max_floor_dist = float(self.get_parameter('max_floor_distance_m').value)
        self.allowed_classes = set(c.lower() for c in self.get_parameter('obstacle_classes').value)

        # Depth cache for 2D bbox deprojection
        self.latest_depth_map = None
        self.latest_depth_frame_id = 'camera_optical_frame'
        self.depth_lock = threading.Lock()

        # TF2 Setup
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # QoS Settings
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5
        )
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Subscribers (Subscribe to both Hailo NPU C++ driver and VLM pipeline)
        self.sub_objects_hailo = self.create_subscription(
            SemanticObjectArray, '/hailo/semantic_objects', self.objects_callback, qos_reliable
        )
        self.sub_objects_vlm = self.create_subscription(
            SemanticObjectArray, '/hailo/vlm/semantic_objects', self.objects_callback, qos_reliable
        )

        from std_msgs.msg import Bool
        self.sub_clear = self.create_subscription(
            Bool, '/semantic_costmap/clear', self.clear_obstacles_callback, qos_reliable
        )

        # Depth image subscription (for negative obstacles and 3D deprojection)
        self.sub_depth = self.create_subscription(
            Image, self.depth_topic, self.depth_callback, qos_best_effort
        )
        if self.enable_negative_obstacles:
            self.get_logger().info(f"Rilevamento ostacoli negativi (FM-NAV-009) attivo sul topic {self.depth_topic}")

        # Publishers
        self.pub_point_cloud = self.create_publisher(
            PointCloud2, '/hailo_semantic_obstacles_pc', qos_reliable
        )
        self.pub_debug_markers = self.create_publisher(
            MarkerArray, '/semantic_costmap_injector/debug', qos_best_effort
        )

        # Obstacle Memory (key: label+str(x)+str(y), val: (transformed_point, timestamp, label))
        self.active_obstacles = {}
        self.lock = threading.Lock()

        # Timer to handle temporal decay and publishing at 5Hz
        self.create_timer(0.2, self.update_and_publish)

        self.get_logger().info(f"semantic_costmap_injector avviato (Cap: {self.max_obstacles} ostacoli, Decay: {self.decay_time}s).")

    def clear_obstacles_callback(self, msg):
        """Svuota immediatamente tutti gli ostacoli attivi in memoria (es. a seguito di ricalibrazione camera)"""
        with self.lock:
            count = len(self.active_obstacles)
            self.active_obstacles.clear()
            self.get_logger().info(f"Costmap Flush: rimossi {count} ostacoli attivi per ricalibrazione fotocamera.")

    def _is_allowed_class(self, sem_class: str, label: str) -> bool:
        """Verifica se la classe o la label dell'oggetto fa parte della whitelist ostacoli."""
        if 'all' in self.allowed_classes:
            return True
        c_low = sem_class.lower().strip() if sem_class else ''
        l_low = label.lower().strip() if label else ''
        return (c_low in self.allowed_classes or
                l_low in self.allowed_classes or
                c_low in ['obstacle', 'person', 'furniture'])

    def objects_callback(self, msg):
        """Callback per la ricezione degli oggetti rilevati da Hailo NPU / VLM"""
        for obj in msg.objects:
            if obj.confidence < self.min_confidence:
                continue

            if not self._is_allowed_class(obj.semantic_class, obj.label):
                continue

            try:
                frame_id = msg.header.frame_id if msg.header.frame_id else 'camera_optical_frame'
                centroid = obj.centroid_3d
                has_3d = (abs(centroid.x) > 1e-4 or abs(centroid.y) > 1e-4 or centroid.z > 0.05)

                pt_cam = PointStamped()
                pt_cam.header = msg.header
                pt_cam.header.frame_id = frame_id

                width_est = obj.estimated_width_m if obj.estimated_width_m > 0.05 else 0.30
                depth_est = obj.estimated_depth_m if obj.estimated_depth_m > 0.05 else 0.30

                if has_3d:
                    pt_cam.point = centroid
                else:
                    # Deproject 2D bbox center using latest depth map
                    with self.depth_lock:
                        if self.latest_depth_map is None:
                            continue
                        depth_map = self.latest_depth_map
                        depth_frame = self.latest_depth_frame_id

                    h_d, w_d = depth_map.shape
                    if len(obj.bbox_2d) >= 4:
                        xmin, ymin, xmax, ymax = obj.bbox_2d[0], obj.bbox_2d[1], obj.bbox_2d[2], obj.bbox_2d[3]
                        u_norm = float(np.clip((xmin + xmax) / 2.0, 0.0, 1.0))
                        v_norm = float(np.clip((ymin + ymax) / 2.0, 0.0, 1.0))
                    else:
                        u_norm, v_norm = 0.5, 0.5

                    u_px = int(u_norm * (w_d - 1))
                    v_px = int(v_norm * (h_d - 1))

                    patch = depth_map[max(0, v_px - 2):min(h_d, v_px + 3), max(0, u_px - 2):min(w_d, u_px + 3)]
                    valid_depths = patch[(patch > 0.2) & (patch < 6.0) & ~np.isnan(patch)]
                    if len(valid_depths) > 0:
                        z_val = float(np.median(valid_depths))
                    else:
                        z_val = float(depth_map[v_px, u_px])

                    if np.isnan(z_val) or z_val < 0.2 or z_val > 6.0:
                        continue

                    fx = w_d * 0.8
                    fy = w_d * 0.8
                    cx = w_d / 2.0
                    cy = h_d / 2.0

                    pt_cam.point.x = (u_px - cx) * z_val / fx
                    pt_cam.point.y = (v_px - cy) * z_val / fy
                    pt_cam.point.z = z_val
                    pt_cam.header.frame_id = depth_frame
                    frame_id = depth_frame

                    if len(obj.bbox_2d) >= 4:
                        width_est = max(0.20, float(abs(xmax - xmin) * z_val))
                        depth_est = max(0.20, float(width_est * 0.5))

                # Trasformiamo nel frame della mappa/costmap (solitamente 'map' o 'odom')
                if self.tf_buffer.can_transform(self.costmap_frame, frame_id, rclpy.time.Time()):
                    pt_map = self.tf_buffer.transform(pt_cam, self.costmap_frame)
                    
                    # Rimuoviamo la coordinata Z per la costmap 2D
                    pt_map.point.z = 0.0

                    # ID univoco per tracciare l'ostacolo
                    grid_x = round(pt_map.point.x / self.grid_res) * self.grid_res
                    grid_y = round(pt_map.point.y / self.grid_res) * self.grid_res
                    obs_key = f"{obj.label}_{grid_x:.2f}_{grid_y:.2f}"

                    with self.lock:
                        # Memory cap control (FM-MEM-011)
                        if len(self.active_obstacles) >= self.max_obstacles:
                            sorted_keys = sorted(
                                self.active_obstacles.keys(),
                                key=lambda k: self.active_obstacles[k]['timestamp']
                            )
                            to_evict = max(1, int(self.max_obstacles * 0.1))
                            for k in sorted_keys[:to_evict]:
                                self.active_obstacles.pop(k, None)

                        self.active_obstacles[obs_key] = {
                            'point': pt_map.point,
                            'timestamp': time.time(),
                            'label': obj.label,
                            'width': width_est,
                            'depth': depth_est
                        }
                else:
                    self.get_logger().warn(f"TF: Impossibile trasformare da {frame_id} a {self.costmap_frame}", throttle_duration_sec=5.0)
            except Exception as e:
                self.get_logger().error(f"Errore durante la trasformazione TF dell'ostacolo: {e}")

    def depth_callback(self, msg):
        """Callback per la matrice di profondità (Depth Image): aggiorna cache e raycasting ostacoli negativi."""
        try:
            height, width = msg.height, msg.width
            if msg.encoding in ['16UC1', 'mono16']:
                depth_map = np.frombuffer(msg.data, dtype=np.uint16).reshape((height, width)).astype(np.float32) / 1000.0
            elif msg.encoding in ['32FC1']:
                depth_map = np.frombuffer(msg.data, dtype=np.float32).reshape((height, width))
            else:
                return

            frame_id = msg.header.frame_id if msg.header.frame_id else 'oak_left_camera_optical_frame'

            # Aggiorna la cache depth per la deproiezione 3D dei bbox 2D
            with self.depth_lock:
                self.latest_depth_map = depth_map
                self.latest_depth_frame_id = frame_id

            if not self.enable_negative_obstacles:
                return

            # [CPU-OPT] Throttle raycasting ostacoli negativi a ~5 Hz (1 su 6 frame @ 30 Hz depth).
            self._depth_frame_counter = getattr(self, '_depth_frame_counter', 0) + 1
            if self._depth_frame_counter % 6 != 0:
                return

            if not self.tf_buffer.can_transform(self.costmap_frame, frame_id, rclpy.time.Time()):
                return

            # [CPU-OPT Pi 5] Single TF lookup and vectorized transformation (eliminates hundreds of TF calls)
            tf_stamped = self.tf_buffer.lookup_transform(self.costmap_frame, frame_id, rclpy.time.Time())
            q = tf_stamped.transform.rotation
            t = tf_stamped.transform.translation

            qx, qy, qz, qw = q.x, q.y, q.z, q.w
            rot_mat = np.array([
                [1.0 - 2.0 * (qy * qy + qz * qz), 2.0 * (qx * qy - qz * qw), 2.0 * (qx * qz + qy * qw)],
                [2.0 * (qx * qy + qz * qw), 1.0 - 2.0 * (qx * qx + qz * qz), 2.0 * (qy * qz - qx * qw)],
                [2.0 * (qx * qz - qy * qw), 2.0 * (qy * qz + qx * qw), 1.0 - 2.0 * (qx * qx + qy * qy)]
            ], dtype=np.float32)
            trans_vec = np.array([t.x, t.y, t.z], dtype=np.float32)

            fx = width * 0.8
            fy = width * 0.8
            cx = width / 2.0
            cy = height / 2.0

            step_x = 16
            step_y = 8
            start_y = height // 2
            now_sec = time.time()

            for u in range(0, width, step_x):
                last_valid_pt = None
                for v in range(height - 1, start_y, -step_y):
                    z_val = depth_map[v, u]

                    if np.isnan(z_val) or np.isinf(z_val) or z_val <= 0.2 or z_val > self.max_floor_dist:
                        if last_valid_pt is not None:
                            self._register_negative_obstacle(last_valid_pt, now_sec)
                            break
                        continue

                    x_cam = (u - cx) * z_val / fx
                    y_cam = (v - cy) * z_val / fy
                    z_cam = z_val

                    p_cam = np.array([x_cam, y_cam, z_cam], dtype=np.float32)
                    p_map = rot_mat @ p_cam + trans_vec

                    pt_map_point = Point()
                    pt_map_point.x = float(p_map[0])
                    pt_map_point.y = float(p_map[1])
                    pt_map_point.z = float(p_map[2])

                    if pt_map_point.z < -self.min_drop_height:
                        if last_valid_pt is not None:
                            self._register_negative_obstacle(last_valid_pt, now_sec)
                        else:
                            self._register_negative_obstacle(pt_map_point, now_sec)
                        break

                    last_valid_pt = pt_map_point

        except Exception as e:
            self.get_logger().error(f"Errore nel rilevamento ostacoli negativi depth: {e}", throttle_duration_sec=10.0)

    def _register_negative_obstacle(self, pt_map_point, timestamp):
        """Registra un bordo di dislivello come ostacolo negativo attivo"""
        grid_x = round(pt_map_point.x / self.grid_res) * self.grid_res
        grid_y = round(pt_map_point.y / self.grid_res) * self.grid_res
        obs_key = f"negative_obs_{grid_x:.2f}_{grid_y:.2f}"

        drop_pt = Point()
        drop_pt.x = pt_map_point.x
        drop_pt.y = pt_map_point.y
        drop_pt.z = 0.0

        with self.lock:
            # Memory cap control (FM-MEM-011)
            if len(self.active_obstacles) >= self.max_obstacles:
                sorted_keys = sorted(
                    self.active_obstacles.keys(),
                    key=lambda k: self.active_obstacles[k]['timestamp']
                )
                to_evict = max(1, int(self.max_obstacles * 0.1))
                for k in sorted_keys[:to_evict]:
                    self.active_obstacles.pop(k, None)

            self.active_obstacles[obs_key] = {
                'point': drop_pt,
                'timestamp': timestamp,
                'label': 'negative_obstacle',
                'width': 0.25,
                'depth': 0.25
            }

    def update_and_publish(self):
        """Pulisce gli ostacoli scaduti e pubblica i GridCells ed i marker debug"""
        now = time.time()
        expired_keys = []

        with self.lock:
            # Identifica gli ostacoli scaduti
            for key, obs in self.active_obstacles.items():
                if now - obs['timestamp'] > self.decay_time:
                    expired_keys.append(key)

            # Rimuove ostacoli scaduti
            for key in expired_keys:
                del self.active_obstacles[key]

            # Costruiamo la lista di punti PointCloud2
            pc_points = []
            
            # Per ogni ostacolo attivo, aggiungiamo il centro e i punti dell'ingombro
            for obs in self.active_obstacles.values():
                center_pt = obs['point']
                px, py = center_pt.x, center_pt.y
                # Aggiungi il centro a z = 0.1
                pc_points.append([px, py, 0.1])
                
                # Aggiungi i punti di delimitazione basati su larghezza e profondità (bounding box semplificata)
                dw = obs.get('width', 0.2) / 2.0
                dd = obs.get('depth', 0.2) / 2.0
                for dx in [-dd, dd]:
                    for dy in [-dw, dw]:
                        pc_points.append([px + dx, py + dy, 0.1])

            if pc_points:
                # Creiamo l'header per il PointCloud2
                header = Header()
                header.stamp = self.get_clock().now().to_msg()
                header.frame_id = self.costmap_frame
                
                # Pubblichiamo PointCloud2 per Nav2
                pc_msg = pc2.create_cloud_xyz32(header, pc_points)
                self.pub_point_cloud.publish(pc_msg)

            # Pubblica MarkerArray per visualizzazione in RViz (Foxglove Studio)
            self.publish_debug_markers()

    def publish_debug_markers(self):
        """Pubblica marker cilindrici per visualizzare gli ostacoli su Foxglove/RViz"""
        marker_array = MarkerArray()
        idx = 0
        
        for key, obs in self.active_obstacles.items():
            marker = Marker()
            marker.header.stamp = self.get_clock().now().to_msg()
            marker.header.frame_id = self.costmap_frame
            marker.ns = "semantic_obstacles"
            marker.id = idx
            marker.type = Marker.CYLINDER
            marker.action = Marker.ADD
            marker.pose.position = obs['point']
            # Altezza fittizia per visualizzazione 3D
            marker.pose.position.z = 0.5 
            marker.pose.orientation.w = 1.0
            
            # Dimensioni
            marker.scale.x = max(obs['width'], self.inflation_radius * 2.0)
            marker.scale.y = max(obs['depth'], self.inflation_radius * 2.0)
            marker.scale.z = 1.0 # Altezza 1m
            
            if obs.get('label') == 'negative_obstacle':
                marker.ns = "negative_obstacles"
                marker.color.r = 1.0
                marker.color.g = 0.5
                marker.color.b = 0.0
                marker.color.a = 0.8
            else:
                marker.color.r = 1.0
                marker.color.g = 0.0
                marker.color.b = 0.0
                marker.color.a = 0.6
            
            marker.lifetime = rclpy.duration.Duration(seconds=self.decay_time).to_msg()
            
            # Testo label sopra l'ostacolo
            text_marker = Marker()
            text_marker.header = marker.header
            text_marker.ns = "semantic_labels"
            text_marker.id = idx + 1000
            text_marker.type = Marker.TEXT_VIEW_FACING
            text_marker.action = Marker.ADD
            text_marker.pose.position = Point()
            text_marker.pose.position.x = obs['point'].x
            text_marker.pose.position.y = obs['point'].y
            text_marker.pose.position.z = 1.1 # Sopra il cilindro
            text_marker.scale.z = 0.2 # Altezza testo
            text_marker.color.r = 1.0
            text_marker.color.g = 1.0
            text_marker.color.b = 1.0
            text_marker.color.a = 1.0
            text_marker.text = f"{obs['label']}"
            text_marker.lifetime = marker.lifetime
            
            marker_array.markers.append(marker)
            marker_array.markers.append(text_marker)
            idx += 1

        self.pub_debug_markers.publish(marker_array)


def main(args=None):
    rclpy.init(args=args)
    node = SemanticCostmapInjector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
