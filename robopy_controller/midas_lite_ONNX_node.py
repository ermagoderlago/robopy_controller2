#!/usr/bin/env python3
import os
os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'
os.environ['FASTRTPS_TRANSPORT_USE_SHM'] = '0'
os.environ['OPENCV_SKIP_PYTHON_LOAD_EXTRA_MODULES'] = '1'

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import numpy as np
import time
import onnxruntime as ort
from rclpy.qos import qos_profile_sensor_data
import cv2

class MidasONNXNode(Node):
    def __init__(self):
        super().__init__('midas_onnx_node')
        self.sub = self.create_subscription(
            Image,
            '/rgb/image',
            self.image_callback,
            qos_profile=qos_profile_sensor_data
        )
        self.pub = self.create_publisher(Image, '/rgb/depth', 10)

        # Percorso modello ONNX (deve essere esportato per 640x480!)
        onnx_path = '/host_home/robopy/robopi_controller/robopy_controller/weights/midas_small_640x480.onnx'
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            onnx_path, 
            options,
            providers=['CPUExecutionProvider']
        )
        self.input_name = self.session.get_inputs()[0].name
        self.input_shape = self.session.get_inputs()[0].shape
        self.get_logger().info(f"MiDaS_small ONNX caricato. Forma input: {self.input_shape}")

    def preprocess(self, np_image):
        # Nessun resize, solo conversione e normalizzazione
        img = cv2.cvtColor(np_image, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))  # HWC -> CHW
        return np.expand_dims(img, axis=0)  # [1, 3, H, W]

    def image_callback(self, msg):
        try:
            start_time = time.time()
            np_image = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, -1)
            if np_image.shape[2] != 3:
                self.get_logger().error(f"Immagine input non ha 3 canali ma {np_image.shape[2]}")
                return
            input_tensor = self.preprocess(np_image)
            outputs = self.session.run(None, {self.input_name: input_tensor})
            depth = outputs[0][0, 0]  # [H, W] float32

            # Assicurati che il depth sia float32 e shape [H, W, 1]
            depth = depth.astype(np.float32)
            if len(depth.shape) == 2:
                depth = np.expand_dims(depth, axis=2)  # [H, W, 1]

            # Crea il messaggio Image ROS
            depth_msg = Image()
            depth_msg.header.stamp = msg.header.stamp
            depth_msg.header.frame_id = "camera_frame"
            depth_msg.height = depth.shape[0]
            depth_msg.width = depth.shape[1]
            depth_msg.encoding = "32FC1"
            depth_msg.is_bigendian = False
            depth_msg.step = depth.shape[1] * 4  # 4 bytes per float32
            depth_msg.data = depth.tobytes()
            self.pub.publish(depth_msg)

            self.get_logger().info(f"Pubblicato depth {depth.shape} in {time.time() - start_time:.3f}s")
        except Exception as e:
            self.get_logger().error(f"Errore: {str(e)}", throttle_duration_sec=5)

def main(args=None):
    rclpy.init(args=args)
    node = MidasONNXNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()