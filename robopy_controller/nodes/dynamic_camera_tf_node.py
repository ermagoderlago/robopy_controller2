#!/usr/bin/env python3
# dynamic_camera_tf_node.py
import rclpy
from rclpy.node import Node
from tf2_ros import TransformBroadcaster
from geometry_msgs.msg import TransformStamped
from sensor_msgs.msg import Imu
import math

class DynamicCameraTF(Node):
    def __init__(self):
        super().__init__('dynamic_camera_tf')
        self.declare_parameter('imu_topic', '/imu/data')
        self.declare_parameter('camera_position_x', 0.10)
        self.declare_parameter('camera_position_y', 0.0)
        self.declare_parameter('camera_position_z', 0.15)
        self.declare_parameter('camera_pitch_offset', -0.5236)  # physical URDF offset (rad)
        self.declare_parameter('compensation_factor', 1.0)
        self.declare_parameter('lowpass_alpha', 0.3)
        self.declare_parameter('wait_for_calib', True)

        self.imu_topic = self.get_parameter('imu_topic').value
        self.cam_x = self.get_parameter('camera_position_x').value
        self.cam_y = self.get_parameter('camera_position_y').value
        self.cam_z = self.get_parameter('camera_position_z').value
        self.camera_offset = self.get_parameter('camera_pitch_offset').value
        self.compensation_factor = self.get_parameter('compensation_factor').value
        self.alpha = self.get_parameter('lowpass_alpha').value
        self.wait_for_calib = self.get_parameter('wait_for_calib').value

        self.tf_broadcaster = TransformBroadcaster(self)
        self.filtered_pitch = 0.0
        self.filtered_roll = 0.0
        self.calibrated = False
        self.extrinsic_pitch_correction = 0.0

        # subscribe to calibration flag, IMU, and extrinsic pitch auto-calibration
        self.create_subscription(Imu, '/imu/data', self.imu_cb, 50)
        from std_msgs.msg import Bool, Float32
        self.create_subscription(Bool, '/imu/calibrated', lambda m: self._on_calib(m), 1)
        self.create_subscription(Float32, '/camera/extrinsic_pitch_correction', self._on_extrinsic_corr, 10)

        self.get_logger().info("dynamic_camera_tf node started")

    def _on_extrinsic_corr(self, msg: Float32):
        self.extrinsic_pitch_correction = float(msg.data)
        self.get_logger().info(f"Updated dynamic camera pitch correction: {math.degrees(self.extrinsic_pitch_correction):+.2f} deg")

    def _on_calib(self, msg):
        # calibration completed
        if isinstance(msg, bool):
            self.calibrated = msg
        else:
            try:
                self.calibrated = getattr(msg, 'data', True)
            except Exception:
                self.calibrated = True
        if self.calibrated:
            self.get_logger().info("IMU calibration flag received. Enabling dynamic TF.")

    def imu_cb(self, msg: Imu):
        if self.wait_for_calib and not self.calibrated:
            return

        # compute pitch, roll from quaternion
        q = msg.orientation
        x, y, z, w = q.x, q.y, q.z, q.w

        # conversion to roll/pitch/yaw
        # roll (x-axis rotation)
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp)
        # pitch (y-axis rotation)
        sinp = 2.0 * (w * y - z * x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi/2, sinp)
        else:
            pitch = math.asin(sinp)

        # lowpass
        self.filtered_pitch = self.filtered_pitch*(1.0-self.alpha) + pitch*self.alpha
        self.filtered_roll = self.filtered_roll*(1.0-self.alpha) + roll*self.alpha

        # compensation - invert sign if needed + extrinsic sag correction
        compensation = -self.filtered_pitch * self.compensation_factor
        total_pitch = self.camera_offset + self.extrinsic_pitch_correction + compensation

        self._publish_tf(total_pitch)

    def _publish_tf(self, camera_pitch):
        now = self.get_clock().now().to_msg()
        t = TransformStamped()
        t.header.stamp = now
        t.header.frame_id = 'base_link'
        t.child_frame_id = 'camera_link_stabilized'
        t.transform.translation.x = float(self.cam_x)
        t.transform.translation.y = float(self.cam_y)
        t.transform.translation.z = float(self.cam_z)
        cy = math.cos(camera_pitch*0.5)
        sy = math.sin(camera_pitch*0.5)
        # quaternion for pitch rotation (x=0,y=sin/2,z=0,w=cos/2)
        t.transform.rotation.x = 0.0
        t.transform.rotation.y = sy
        t.transform.rotation.z = 0.0
        t.transform.rotation.w = cy
        # optical frame transform (REP-103)
        t_opt = TransformStamped()
        t_opt.header.stamp = now
        t_opt.header.frame_id = 'camera_link_stabilized'
        t_opt.child_frame_id = 'camera_optical_frame_stabilized'
        t_opt.transform.translation.x = 0.0
        t_opt.transform.translation.y = 0.0
        t_opt.transform.translation.z = 0.0
        # REP-103 rotation: roll=-pi/2, pitch=0, yaw=-pi/2
        # precompute quaternion for (-pi/2,0,-pi/2)
        cr = math.cos(-1.5708/2); sr = math.sin(-1.5708/2)
        cp = 1.0; sp = 0.0
        cy = math.cos(-1.5708/2); sy = math.sin(-1.5708/2)
        t_opt.transform.rotation.x = sr*cp*cy - cr*sp*sy
        t_opt.transform.rotation.y = cr*sp*cy + sr*cp*sy
        t_opt.transform.rotation.z = cr*cp*sy - sr*sp*cy
        t_opt.transform.rotation.w = cr*cp*cy + sr*sp*sy

        self.tf_broadcaster.sendTransform(t)
        self.tf_broadcaster.sendTransform(t_opt)

def main(args=None):
    rclpy.init(args=args)
    node = DynamicCameraTF()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__=='__main__':
    main()
