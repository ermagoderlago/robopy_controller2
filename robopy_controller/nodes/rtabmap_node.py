#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from sensor_msgs.msg import Image, CameraInfo
from nav_msgs.msg import Odometry
import threading
import time

class RTABMapNode(Node):
    def __init__(self):
        super().__init__('rtabmap_node')
        
        # Dichiarazione parametri
        self.declare_parameters(
            namespace='',
            parameters=[
                ('frame_id', 'camera_frame'),
                ('subscribe_depth', False),
                ('subscribe_imu', True),
                ('wait_imu_to_init', True),
                ('odom_frame_id', 'odom'),
                ('map_frame_id', 'map'),
                ('publish_tf_map', False),
                ('qos', 2),
                ('rate_limit', 15.0),
                ('debug', False)
            ]
        )
        
        # Ottieni parametri
        self.frame_id = self.get_parameter('frame_id').value
        self.subscribe_depth = self.get_parameter('subscribe_depth').value
        self.subscribe_imu = self.get_parameter('subscribe_imu').value
        self.wait_imu_to_init = self.get_parameter('wait_imu_to_init').value
        self.odom_frame_id = self.get_parameter('odom_frame_id').value
        self.map_frame_id = self.get_parameter('map_frame_id').value
        self.publish_tf_map = self.get_parameter('publish_tf_map').value
        qos_value = self.get_parameter('qos').value
        self.rate_limit = self.get_parameter('rate_limit').value
        self.debug = self.get_parameter('debug').value
        
        # Configura QoS
        if qos_value == 0:
            self.qos = QoSProfile(
                reliability=QoSReliabilityPolicy.SYSTEM_DEFAULT,
                history=QoSHistoryPolicy.SYSTEM_DEFAULT,
                depth=10
            )
        elif qos_value == 1:
            self.qos = QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=10
            )
        else:  # qos_value == 2
            self.qos = QoSProfile(
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=10
            )
        
        # Sincronizzazione
        self.lock = threading.Lock()
        self.last_image = None
        self.last_info = None
        self.last_odom = None
        self.initialized = False
        
        # Publisher
        self.map_pub = self.create_publisher(Odometry, '/rtabmap/map', 10)
        
        # Subscribers
        self.image_sub = self.create_subscription(
            Image,
            '/camera/image_raw',
            self.image_callback,
            self.qos
        )
        
        self.info_sub = self.create_subscription(
            CameraInfo,
            '/camera/camera_info',
            self.info_callback,
            self.qos
        )
        
        self.odom_sub = self.create_subscription(
            Odometry,
            '/odometry/filtered',
            self.odom_callback,
            self.qos
        )
        
        # Timer per elaborazione
        self.process_timer = self.create_timer(1.0 / self.rate_limit, self.process_data)
        
        self.get_logger().info("RTABMap Node initialized with parameters:")
        self.get_logger().info(f"  Frame ID: {self.frame_id}")
        self.get_logger().info(f"  Rate Limit: {self.rate_limit} Hz")
        self.get_logger().info(f"  QoS: {qos_value}")

    def image_callback(self, msg):
        with self.lock:
            self.last_image = msg

    def info_callback(self, msg):
        with self.lock:
            self.last_info = msg

    def odom_callback(self, msg):
        with self.lock:
            self.last_odom = msg
            if not self.initialized and self.wait_imu_to_init:
                self.initialized = True
                self.get_logger().info("IMU initialized, starting mapping")

    def process_data(self):
        if not self.initialized and self.wait_imu_to_init:
            return
            
        with self.lock:
            if self.last_image is None or self.last_info is None or self.last_odom is None:
                return
                
            # Simula elaborazione RTAB-Map
            # (Nella realtà qui ci sarebbe l'integrazione con la libreria RTAB-Map)
            if self.debug:
                self.get_logger().info(f"Processing data: {self.last_image.header.stamp}")
            
            # Crea messaggio mappa fittizio
            map_msg = Odometry()
            map_msg.header.stamp = self.get_clock().now().to_msg()
            map_msg.header.frame_id = self.map_frame_id
            map_msg.child_frame_id = self.odom_frame_id
            map_msg.pose.pose = self.last_odom.pose.pose
            
            # Pubblica la mappa
            self.map_pub.publish(map_msg)

def main(args=None):
    rclpy.init(args=args)
    node = RTABMapNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()