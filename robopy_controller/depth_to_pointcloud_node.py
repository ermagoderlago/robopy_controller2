import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo, PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header
from cv_bridge import CvBridge
import numpy as np
from sensor_msgs.msg import PointField
from message_filters import Subscriber, ApproximateTimeSynchronizer

class DepthToPointCloudNode(Node):
    def __init__(self):
        super().__init__('depth_to_pointcloud_node')
        self.bridge = CvBridge()

        self.rgb_sub = Subscriber(self, Image, '/rgb/image')
        self.depth_sub = Subscriber(self, Image, '/rgb/depth')
        self.info_sub = Subscriber(self, CameraInfo, '/rgb/camera_info')

        self.ts = ApproximateTimeSynchronizer(
            [self.rgb_sub, self.depth_sub, self.info_sub], queue_size=10, slop=0.1)
        self.ts.registerCallback(self.synced_callback)

        self.pc_pub = self.create_publisher(PointCloud2, '/rgb/pointcloud', 10)

        # Internal storage
        self.color = None
        self.depth = None
        self.cam_info = None

    def synced_callback(self, rgb_msg, depth_msg, info_msg):
        try:
            self.color = self.bridge.imgmsg_to_cv2(rgb_msg, desired_encoding='bgr8')
            self.depth = self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding='32FC1')
            self.cam_info = info_msg
            self.publish_pointcloud(rgb_msg.header)
        except Exception as e:
            self.get_logger().error(f"Errore nella callback sincronizzata: {e}")

    def publish_pointcloud(self, header):
        if self.color is None or self.depth is None or self.cam_info is None:
            return

        fx = self.cam_info.k[0]
        fy = self.cam_info.k[4]
        cx = self.cam_info.k[2]
        cy = self.cam_info.k[5]

        height, width = self.depth.shape

        # Crea meshgrid di coordinate pixel
        u, v = np.meshgrid(np.arange(width), np.arange(height))
        z = self.depth
        valid = (z > 0) & (z < 10) & (~np.isnan(z))

        u = u[valid]
        v = v[valid]
        z = z[valid]
        color = self.color[valid]

        x = (u - cx) * z / fx
        y = (v - cy) * z / fy

        r = color[:, 2]
        g = color[:, 1]
        b = color[:, 0]
        rgb = (r.astype(np.uint32) << 16) | (g.astype(np.uint32) << 8) | b.astype(np.uint32)
        rgb = rgb.view(np.float32)

        points = np.stack([x, y, z, rgb], axis=-1)

        cloud_header = Header()
        cloud_header.stamp = header.stamp
        cloud_header.frame_id = "camera_frame"

        pc2_msg = point_cloud2.create_cloud(
            cloud_header,
            fields=[
                PointField(name='x', offset=0,  datatype=PointField.FLOAT32, count=1),
                PointField(name='y', offset=4,  datatype=PointField.FLOAT32, count=1),
                PointField(name='z', offset=8,  datatype=PointField.FLOAT32, count=1),
                PointField(name='rgb', offset=12, datatype=PointField.FLOAT32, count=1),
            ],
            points=points
        )
        self.pc_pub.publish(pc2_msg)

        self.color = None
        self.depth = None

        self.get_logger().info(f"PointCloud pubblicata con {points.shape[0]} punti")

def main(args=None):
    rclpy.init(args=args)
    node = DepthToPointCloudNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
