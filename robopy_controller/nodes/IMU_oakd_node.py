#!/usr/bin/env python3
# oak_imu_node.py
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from sensor_msgs.msg import Imu
import depthai as dai
import numpy as np
from scipy.spatial.transform import Rotation
import threading
import time

class OakIMUNode(Node):
    def __init__(self):
        super().__init__('oak_imu_node')
        
        # Parametri
        self.declare_parameter('imu_fps', 100)
        self.declare_parameter('frame_id', 'imu_link')  # Frame IMU standard ROS
        self.declare_parameter('topic', '/oak/imu/data')
        self.declare_parameter('calibrate_gyro', True)
        
        # QoS per IMU (best effort, volatile)
        qos_profile = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE
        )
        
        self.publisher = self.create_publisher(
            Imu, 
            self.get_parameter('topic').value, 
            qos_profile
        )
        
        self.frame_id = self.get_parameter('frame_id').value
        self.calibrate_gyro = self.get_parameter('calibrate_gyro').value
        
        # Variabili per calibrazione giroscopio
        self.gyro_calib_samples = 100
        self.gyro_calib_data = []
        self.gyro_bias = np.zeros(3)
        self.is_calibrated = False
        
        # Thread per IMU
        self.imu_thread = threading.Thread(target=self.imu_loop)
        self.imu_thread.daemon = True
        self.running = True
        
        self.get_logger().info('Starting OAK-D-LITE IMU node...')
        self.imu_thread.start()
    
    def calibrate_gyroscope(self, gyro_data):
        """Calibrazione giroscopio per rimuovere bias"""
        self.gyro_calib_data.append(gyro_data)
        
        if len(self.gyro_calib_data) >= self.gyro_calib_samples:
            self.gyro_bias = np.mean(self.gyro_calib_data, axis=0)
            self.is_calibrated = True
            self.get_logger().info(f'Gyro calibration complete. Bias: {self.gyro_bias}')
            return True
        return False
    
    def imu_loop(self):
        """Loop principale per lettura IMU"""
        try:
            # Pipeline DepthAI per IMU
            pipeline = dai.Pipeline()
            
            # Configura IMU
            imu = pipeline.create(dai.node.IMU)
            
            # Abilita sensori IMU
            imu.enableIMUSensor([
                dai.IMUSensor.ACCELEROMETER,
                dai.IMUSensor.GYROSCOPE_CALIBRATED,
                dai.IMUSensor.ROTATION_VECTOR  # Aggiungi se disponibile
            ], self.get_parameter('imu_fps').value)
            
            # Configura report
            imu.setBatchReportThreshold(1)
            imu.setMaxBatchReports(10)
            
            # Output
            xlink_out = pipeline.create(dai.node.XLinkOut)
            xlink_out.setStreamName("imu")
            imu.out.link(xlink_out.input)
            
            # Connessione al dispositivo
            self.get_logger().info('Connecting to OAK-D-LITE device...')
            with dai.Device(pipeline) as device:
                self.get_logger().info('OAK-D-LITE IMU connected!')
                
                imu_queue = device.getOutputQueue(
                    name="imu", 
                    maxSize=50, 
                    blocking=False
                )
                
                # Filtro complementare per orientamento
                orientation_q = np.array([1.0, 0.0, 0.0, 0.0])  # Quaternione identità
                last_time = time.time()
                
                while self.running and rclpy.ok():
                    imu_data = imu_queue.tryGet()
                    
                    if imu_data is not None:
                        current_time = time.time()
                        dt = current_time - last_time
                        last_time = current_time
                        
                        for imu_packet in imu_data.packets:
                            msg = Imu()
                            msg.header.stamp = self.get_clock().now().to_msg()
                            msg.header.frame_id = self.frame_id
                            
                            # Accelerometro
                            accel = imu_packet.acceleroMeter
                            msg.linear_acceleration.x = accel.x
                            msg.linear_acceleration.y = accel.y
                            msg.linear_acceleration.z = accel.z
                            
                            # Giroscopio
                            gyro = imu_packet.gyroscope
                            gyro_data = np.array([gyro.x, gyro.y, gyro.z])
                            
                            # Calibrazione giroscopio
                            if self.calibrate_gyro and not self.is_calibrated:
                                if self.calibrate_gyroscope(gyro_data):
                                    self.get_logger().info('Gyroscope calibrated')
                            
                            # Applica correzione bias
                            if self.is_calibrated:
                                gyro_data -= self.gyro_bias
                            
                            msg.angular_velocity.x = gyro_data[0]
                            msg.angular_velocity.y = gyro_data[1]
                            msg.angular_velocity.z = gyro_data[2]
                            
                            # Tentativo di ottenere orientamento
                            # Prima controlla se c'è rotation vector
                            rotation_vectors = imu_packet.rotationVector
                            if rotation_vectors:
                                rot = rotation_vectors
                                msg.orientation.x = rot.i
                                msg.orientation.y = rot.j
                                msg.orientation.z = rot.k
                                msg.orientation.w = rot.real
                            else:
                                # Altrimenti integra giroscopio (semplice)
                                if dt > 0 and dt < 0.1:  # Filtra dt anomali
                                    # Integrazione approssimativa
                                    gyro_rad = gyro_data * (np.pi / 180.0)  # Converti a rad/s
                                    delta_angle = gyro_rad * dt
                                    
                                    # Aggiorna orientamento con quaternioni
                                    q_delta = Rotation.from_rotvec(delta_angle).as_quat()
                                    orientation_q = self.quaternion_multiply(orientation_q, q_delta)
                                    orientation_q /= np.linalg.norm(orientation_q)
                                    
                                    msg.orientation.x = orientation_q[0]
                                    msg.orientation.y = orientation_q[1]
                                    msg.orientation.z = orientation_q[2]
                                    msg.orientation.w = orientation_q[3]
                            
                            # Matrici di covarianza (da calibrare)
                            msg.linear_acceleration_covariance = [
                                0.01, 0.0, 0.0,
                                0.0, 0.01, 0.0,
                                0.0, 0.0, 0.01
                            ]
                            
                            msg.angular_velocity_covariance = [
                                0.01, 0.0, 0.0,
                                0.0, 0.01, 0.0,
                                0.0, 0.0, 0.01
                            ]
                            
                            msg.orientation_covariance = [
                                0.05, 0.0, 0.0,
                                0.0, 0.05, 0.0,
                                0.0, 0.0, 0.05
                            ]
                            
                            self.publisher.publish(msg)
                    
                    time.sleep(0.001)  # Small sleep per non saturare CPU
                    
        except Exception as e:
            self.get_logger().error(f'IMU loop error: {e}')
            self.running = False
    
    def quaternion_multiply(self, q1, q2):
        """Moltiplicazione di quaternioni"""
        w1, x1, y1, z1 = q1[3], q1[0], q1[1], q1[2]
        w2, x2, y2, z2 = q2[3], q2[0], q2[1], q2[2]
        
        w = w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
        x = w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2
        y = w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2
        z = w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2
        
        return np.array([x, y, z, w])
    
    def destroy_node(self):
        self.running = False
        if self.imu_thread.is_alive():
            self.imu_thread.join(timeout=2.0)
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = OakIMUNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()