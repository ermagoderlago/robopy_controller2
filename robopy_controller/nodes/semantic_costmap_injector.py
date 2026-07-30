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
        self.declare_parameter('min_obstacle_confidence', 0.6)
        self.declare_parameter('inflation_radius_m', 0.15)
        self.declare_parameter('costmap_frame', 'map')
        self.declare_parameter('grid_resolution', 0.05) # 5 cm grid resolution

        # Negative Obstacle Detection Parameters (FM-NAV-009)
        self.declare_parameter('enable_negative_obstacles', True)
        self.declare_parameter('depth_topic', '/camera/depth/image_raw')
        self.declare_parameter('min_drop_height_m', 0.15)
        self.declare_parameter('max_floor_distance_m', 2.5)

        self.decay_time = self.get_parameter('decay_time_sec').value
        self.min_confidence = self.get_parameter('min_obstacle_confidence').value
        self.inflation_radius = self.get_parameter('inflation_radius_m').value
        self.costmap_frame = self.get_parameter('costmap_frame').value
        self.grid_res = self.get_parameter('grid_resolution').value

        self.enable_negative_obstacles = self.get_parameter('enable_negative_obstacles').value
        self.depth_topic = self.get_parameter('depth_topic').value
        self.min_drop_height = self.get_parameter('min_drop_height_m').value
        self.max_floor_dist = self.get_parameter('max_floor_distance_m').value

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

        # Subscribers
        self.sub_objects = self.create_subscription(
            SemanticObjectArray, '/hailo/vlm/semantic_objects', self.objects_callback, qos_reliable
        )

        from std_msgs.msg import Bool
        self.sub_clear = self.create_subscription(
            Bool, '/semantic_costmap/clear', self.clear_obstacles_callback, qos_reliable
        )

        if self.enable_negative_obstacles:
            self.sub_depth = self.create_subscription(
                Image, self.depth_topic, self.depth_callback, qos_best_effort
            )
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

        self.get_logger().info("semantic_costmap_injector avviato con supporto al flush selettivo costmap.")

    def clear_obstacles_callback(self, msg):
        """Svuota immediatamente tutti gli ostacoli attivi in memoria (es. a seguito di ricalibrazione camera)"""
        with self.lock:
            count = len(self.active_obstacles)
            self.active_obstacles.clear()
            self.get_logger().info(f"Costmap Flush: rimossi {count} ostacoli attivi per ricalibrazione fotocamera.")

    def objects_callback(self, msg):
        """Callback per la ricezione degli oggetti rilevati da VLM"""
        # Filtra e trasforma i punti nel frame di destinazione
        for obj in msg.objects:
            if obj.confidence < self.min_confidence:
                continue

            # Consideriamo solo ostacoli adatti alla costmap
            if obj.semantic_class not in ["obstacle", "person", "furniture"]:
                continue

            try:
                # Creiamo PointStamped per TF2
                pt_cam = PointStamped()
                pt_cam.header = msg.header
                pt_cam.point = obj.centroid_3d

                # Trasformiamo nel frame della mappa/costmap (solitamente 'map' o 'odom')
                # Se la trasformata non è ancora pronta, saltiamo
                if self.tf_buffer.can_transform(self.costmap_frame, msg.header.frame_id, rclpy.time.Time()):
                    pt_map = self.tf_buffer.transform(pt_cam, self.costmap_frame)
                    
                    # Rimuoviamo la coordinata Z per la costmap 2D
                    pt_map.point.z = 0.0

                    # ID univoco per tracciare l'ostacolo
                    # Eseguiamo il discretizing sulla griglia per unire rilevamenti vicini dello stesso tipo
                    grid_x = round(pt_map.point.x / self.grid_res) * self.grid_res
                    grid_y = round(pt_map.point.y / self.grid_res) * self.grid_res
                    obs_key = f"{obj.label}_{grid_x:.2f}_{grid_y:.2f}"

                    with self.lock:
                        # Salviamo l'ostacolo con timestamp aggiornato
                        self.active_obstacles[obs_key] = {
                            'point': pt_map.point,
                            'timestamp': time.time(),
                            'label': obj.label,
                            'width': obj.estimated_width_m,
                            'depth': obj.estimated_depth_m
                        }
                else:
                    self.get_logger().warn(f"TF: Impossibile trasformare da {msg.header.frame_id} a {self.costmap_frame}", throttle_duration_sec=5.0)
            except Exception as e:
                self.get_logger().error(f"Errore durante la trasformazione TF dell'ostacolo: {e}")

    def depth_callback(self, msg):
        """Callback per la matrice di profondità (Depth Image): Raycasting ostacoli negativi (FM-NAV-009)"""
        if not self.enable_negative_obstacles:
            return

        try:
            height, width = msg.height, msg.width
            if msg.encoding in ['16UC1', 'mono16']:
                depth_map = np.frombuffer(msg.data, dtype=np.uint16).reshape((height, width)).astype(np.float32) / 1000.0
            elif msg.encoding in ['32FC1']:
                depth_map = np.frombuffer(msg.data, dtype=np.float32).reshape((height, width))
            else:
                return

            fx = width * 0.8
            fy = width * 0.8
            cx = width / 2.0
            cy = height / 2.0

            step_x = 16
            step_y = 8
            start_y = height // 2

            now_sec = time.time()
            frame_id = msg.header.frame_id if msg.header.frame_id else 'oak_left_camera_optical_frame'

            if not self.tf_buffer.can_transform(self.costmap_frame, frame_id, rclpy.time.Time()):
                return

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

                    pt_cam = PointStamped()
                    pt_cam.header = msg.header
                    pt_cam.header.frame_id = frame_id
                    pt_cam.point.x = x_cam
                    pt_cam.point.y = y_cam
                    pt_cam.point.z = z_cam

                    pt_map = self.tf_buffer.transform(pt_cam, self.costmap_frame)

                    if pt_map.point.z < -self.min_drop_height:
                        if last_valid_pt is not None:
                            self._register_negative_obstacle(last_valid_pt, now_sec)
                        else:
                            self._register_negative_obstacle(pt_map.point, now_sec)
                        break

                    last_valid_pt = pt_map.point

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
