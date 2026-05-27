#!/usr/bin/env python3
# object_3d_mapper.py

import rclpy
from rclpy.node import Node
from message_filters import ApproximateTimeSynchronizer, Subscriber
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
import numpy as np
import cv2
from cv_bridge import CvBridge
import tf2_ros
import tf2_geometry_msgs
from geometry_msgs.msg import PoseStamped, Point
from vision_msgs.msg import Detection2DArray, Detection2D, BoundingBox2D, ObjectHypothesisWithPose
from sensor_msgs.msg import Image, CameraInfo
from rtabmap_msgs.msg import Info, LandmarkDetection
from std_msgs.msg import ColorRGBA
from visualization_msgs.msg import Marker, MarkerArray
import math
import time
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy

class Object3DMapper(Node):
    def __init__(self):
        super().__init__('object_3d_mapper')
        
        # Parameters
        self.declare_parameter('min_confidence', 0.5)
        self.declare_parameter('max_distance', 5.0)  # metri
        self.declare_parameter('min_object_height', 0.05)  # metri (soglia per terra)
        self.declare_parameter('publish_markers', True)
        self.declare_parameter('debug', True)
        
        self.min_confidence = self.get_parameter('min_confidence').value
        self.max_distance = self.get_parameter('max_distance').value
        self.min_object_height = self.get_parameter('min_object_height').value
        self.publish_markers = self.get_parameter('publish_markers').value
        self.debug = self.get_parameter('debug').value
        
        # TF
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)
        
        # CV Bridge
        self.bridge = CvBridge()
        
        # Camera intrinsics (verranno aggiornate da camera_info)
        self.camera_matrix = None
        self.dist_coeffs = None
        self.camera_info_received = False
        
        # State
        self.objects_3d = {}  # ID -> oggetto 3D
        self.next_id = 0
        
        # QoS per sincronizzazione
        qos_profile = QoSProfile(
        reliability=ReliabilityPolicy.BEST_EFFORT,
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        durability=DurabilityPolicy.VOLATILE
        )
        
        # Subscribers sincronizzati
        self.detections_sub = Subscriber(self, Detection2DArray, '/oak/detections', qos_profile=qos_profile)
        self.depth_sub = Subscriber(self, Image, '/oak/stereo/image_raw', qos_profile=qos_profile)
        self.camera_info_sub = self.create_subscription(
            CameraInfo, '/oak/rgb/camera_info', self.camera_info_callback, 10
        )
        
        # Sincronizzatore (Depth + Detections)
        self.ts = ApproximateTimeSynchronizer(
            [self.detections_sub, self.depth_sub], 
            queue_size=10, 
            slop=0.1  # 0.1 secondi di tolleranza
        )
        self.ts.registerCallback(self.sync_callback)
        
        # Publishers
        self.info_pub = self.create_publisher(Info, '/rtabmap/info', 10)
        self.markers_pub = self.create_publisher(MarkerArray, '/object_3d_markers', 10)
        
        # Timer per pubblicazione periodica landmarks
        self.timer = self.create_timer(1.0, self.publish_landmarks)  # 1 Hz
        
        self.get_logger().info("Object 3D Mapper initialized")
        self.get_logger().info(f"Parameters: min_confidence={self.min_confidence}, "
                              f"max_distance={self.max_distance}m")
    
    def camera_info_callback(self, msg):
        """Salva i parametri intrinseci della camera"""
        if not self.camera_info_received:
            self.camera_matrix = np.array(msg.k).reshape(3, 3)
            self.dist_coeffs = np.array(msg.d) if msg.d else np.zeros(5)
            self.camera_info_received = True
            self.get_logger().info("Camera intrinsics loaded")
    
    def sync_callback(self, detections_msg, depth_msg):
        """Callback sincronizzato: depth + detections"""
        if not self.camera_info_received:
            self.get_logger().warn("Camera info not yet received")
            return
        
        try:
            # Converti depth image
            depth_image = self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding='32FC1')
            
            # Calcola posizione 3D per ogni detection
            for detection in detections_msg.detections:
                self.process_detection(detection, depth_image, depth_msg.header)
                
        except Exception as e:
            self.get_logger().error(f"Error in sync_callback: {e}")
    
    def process_detection(self, detection, depth_image, header):
        """Processa una singola detection e calcola posizione 3D"""
        try:
            # Estrai informazioni dalla detection
            if len(detection.results) == 0:
                return
            
            hypothesis = detection.results[0]
            class_id = int(hypothesis.hypothesis.class_id) if hypothesis.hypothesis.class_id.isdigit() else 0
            confidence = hypothesis.hypothesis.score
            class_name = f"class_{class_id}"  # Puoi mappare a nomi COCO
            
            if confidence < self.min_confidence:
                return
            
            # Bounding box
            bbox = detection.bbox
            center_x = bbox.center.position.x
            center_y = bbox.center.position.y
            width = bbox.size_x
            height = bbox.size_y
            
            # Punto di interesse: CENTRO DEL LATO INFERIORE della bbox
            # Questo dovrebbe essere il punto di appoggio dell'oggetto sul pavimento
            u = int(center_x)  # Pixel X
            v = int(center_y + height/2)  # Pixel Y (parte inferiore)
            
            # Controlla bounds
            h, w = depth_image.shape
            if u < 0 or u >= w or v < 0 or v >= h:
                return
            
            # Leggi profondità
            depth = depth_image[v, u]
            
            # Filtra valori non validi
            if np.isnan(depth) or depth <= 0 or depth > self.max_distance:
                return
            
            # Converti pixel 2D -> 3D (coordinate camera)
            point_3d = self.pixel_to_3d(u, v, depth)
            
            if point_3d is None:
                return
            
            # Trasforma da frame camera a frame map
            point_map = self.transform_to_map(point_3d, header)
            
            if point_map is None:
                return
            
            # Crea oggetto 3D
            obj_id = f"{class_name}_{self.next_id}"
            self.next_id += 1
            
            object_3d = {
                'id': obj_id,
                'class_id': class_id,
                'class_name': class_name,
                'confidence': confidence,
                'position': point_map,  # [x, y, z] in frame map
                'bbox': [center_x, center_y, width, height],
                'timestamp': time.time(),
                'age': 0
            }
            
            # Salva oggetto
            self.objects_3d[obj_id] = object_3d
            
            if self.debug:
                self.get_logger().info(
                    f"3D Object detected: {class_name} at [{point_map[0]:.2f}, "
                    f"{point_map[1]:.2f}, {point_map[2]:.2f}]m, "
                    f"depth: {depth:.2f}m, conf: {confidence:.2f}"
                )
                
        except Exception as e:
            self.get_logger().error(f"Error processing detection: {e}")
    
    def pixel_to_3d(self, u, v, depth):
        """Converte coordinate pixel + depth -> coordinate 3D (camera frame)"""
        if self.camera_matrix is None:
            return None
        
        # Matrice intrinseci
        fx = self.camera_matrix[0, 0]
        fy = self.camera_matrix[1, 1]
        cx = self.camera_matrix[0, 2]
        cy = self.camera_matrix[1, 2]
        
        # Conversione
        x = (u - cx) * depth / fx
        y = (v - cy) * depth / fy
        z = depth
        
        return [x, y, z]
    
    def transform_to_map(self, point_3d, header):
        """Trasforma punto da camera frame a map frame usando TF"""
        try:
            # Crea PoseStamped nel frame della camera
            pose_camera = PoseStamped()
            pose_camera.header = header
            pose_camera.pose.position = Point(x=point_3d[0], y=point_3d[1], z=point_3d[2])
            pose_camera.pose.orientation.w = 1.0
            
            # Cerca trasformata
            transform = self.tf_buffer.lookup_transform(
                'map',  # target frame
                header.frame_id,  # source frame (camera_optical_frame)
                rclpy.time.Time(),
                timeout=rclpy.duration.Duration(seconds=0.1)
            )
            
            # Applica trasformazione
            pose_map = tf2_geometry_msgs.do_transform_pose(pose_camera, transform)
            
            return [
                pose_map.pose.position.x,
                pose_map.pose.position.y,
                pose_map.pose.position.z
            ]
            
        except Exception as e:
            if self.debug:
                self.get_logger().warn(f"TF transform failed: {e}")
            return None
    
    def publish_landmarks(self):
        """Pubblica landmarks a RTAB-Map"""
        if not self.objects_3d:
            return
        
        try:
            # Crea messaggio Info con landmarks
            info_msg = Info()
            info_msg.header.stamp = self.get_clock().now().to_msg()
            info_msg.header.frame_id = 'map'
            
            # Pulisci oggetti troppo vecchi
            current_time = time.time()
            to_remove = []
            
            for obj_id, obj in self.objects_3d.items():
                obj_age = current_time - obj['timestamp']
                
                # Rimuovi oggetti più vecchi di 10 secondi
                if obj_age > 10.0:
                    to_remove.append(obj_id)
                    continue
                
                # Crea landmark
                landmark = LandmarkDetection()
                landmark.id = obj_id
                landmark.size = 0.1  # dimensione approssimativa (metri)
                
                # Posizione
                landmark.pose.position.x = obj['position'][0]
                landmark.pose.position.y = obj['position'][1]
                landmark.pose.position.z = obj['position'][2]
                landmark.pose.orientation.w = 1.0
                
                # Info aggiuntive (salvate come stringa)
                landmark.descriptor = f"class:{obj['class_name']},conf:{obj['confidence']:.2f}"
                
                info_msg.landmarks.append(landmark)
            
            # Rimuovi oggetti vecchi
            for obj_id in to_remove:
                del self.objects_3d[obj_id]
            
            # Pubblica su RTAB-Map
            if info_msg.landmarks:
                self.info_pub.publish(info_msg)
            
            # Pubblica markers per visualizzazione in Rviz
            if self.publish_markers:
                self.publish_markers_visualization()
                
        except Exception as e:
            self.get_logger().error(f"Error publishing landmarks: {e}")
    
    def publish_markers_visualization(self):
        """Pubblica MarkerArray per visualizzazione in Rviz"""
        marker_array = MarkerArray()
        
        for obj_id, obj in self.objects_3d.items():
            # Marker sfera per posizione
            sphere = Marker()
            sphere.header.frame_id = 'map'
            sphere.header.stamp = self.get_clock().now().to_msg()
            sphere.ns = 'objects_3d'
            sphere.id = hash(obj_id) % 10000
            sphere.type = Marker.SPHERE
            sphere.action = Marker.ADD
            
            # Posizione
            sphere.pose.position.x = obj['position'][0]
            sphere.pose.position.y = obj['position'][1]
            sphere.pose.position.z = obj['position'][2]
            sphere.pose.orientation.w = 1.0
            
            # Dimensione
            sphere.scale.x = 0.1
            sphere.scale.y = 0.1
            sphere.scale.z = 0.1
            
            # Colore in base alla classe
            color = self.get_color_for_class(obj['class_id'])
            sphere.color = color
            
            # Testo con nome classe
            text = Marker()
            text.header = sphere.header
            text.ns = 'object_labels'
            text.id = sphere.id + 10000
            text.type = Marker.TEXT_VIEW_FACING
            text.action = Marker.ADD
            text.pose.position = sphere.pose.position
            text.pose.position.z += 0.15  # Alza il testo sopra la sfera
            text.pose.orientation.w = 1.0
            text.scale.z = 0.1  # Altezza testo
            text.color.r = 1.0
            text.color.g = 1.0
            text.color.b = 1.0
            text.color.a = 1.0
            text.text = f"{obj['class_name']} ({obj['confidence']:.1f})"
            
            marker_array.markers.append(sphere)
            marker_array.markers.append(text)
        
        self.markers_pub.publish(marker_array)
    
    def get_color_for_class(self, class_id):
        """Restituisce colore per visualizzazione in base alla classe"""
        color = ColorRGBA()
        
        # Mappa colori per classi comuni
        color_map = {
            0: (1.0, 0.0, 0.0, 0.8),   # persona: rosso
            1: (0.0, 1.0, 0.0, 0.8),   # bicicletta: verde
            2: (0.0, 0.0, 1.0, 0.8),   # automobile: blu
            3: (1.0, 1.0, 0.0, 0.8),   # motocicletta: giallo
            56: (1.0, 0.0, 1.0, 0.8),  # sedia: viola
            62: (0.0, 1.0, 1.0, 0.8),  # tv: ciano
        }
        
        if class_id in color_map:
            r, g, b, a = color_map[class_id]
            color.r = r; color.g = g; color.b = b; color.a = a
        else:
            # Colore casuale basato su class_id
            import hashlib
            hash_obj = hashlib.md5(str(class_id).encode())
            hash_int = int(hash_obj.hexdigest(), 16)
            color.r = (hash_int % 100) / 100.0
            color.g = ((hash_int // 100) % 100) / 100.0
            color.b = ((hash_int // 10000) % 100) / 100.0
            color.a = 0.7
        
        return color

def main():
    rclpy.init()
    node = Object3DMapper()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()