import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String
from cv_bridge import CvBridge
import cv2
import numpy as np
import json
import os


from ament_index_python.packages import get_package_share_directory


package_dir = get_package_share_directory('robopy_controller')
model_dir = os.path.join(package_dir, 'models')
proto_path = os.path.join(model_dir, 'MobileNetSSD_deploy.prototxt')
model_path = os.path.join(model_dir, 'MobileNetSSD_deploy.caffemodel')



CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat",
    "bottle", "bus", "car", "cat", "chair", "cow",
    "diningtable", "dog", "horse", "motorbike", "person",
    "pottedplant", "sheep", "sofa", "train", "tvmonitor"
]


class ObjectDetectionNode(Node):
    def __init__(self):
        super().__init__('object_detection_node')

        self.subscription = self.create_subscription(
            Image,
            '/rgb/image',
            self.image_callback,
            10)

        self.bridge = CvBridge()
        self.image_pub = self.create_publisher(Image, 'camera/detections', 10)
        self.data_pub = self.create_publisher(String, 'detected_objects', 10)

        # Ottieni il percorso del pacchetto e dei modelli
        package_dir = get_package_share_directory('robopy_controller')
        model_dir = os.path.join(package_dir, 'models')
        proto_path = os.path.join(model_dir, 'MobileNetSSD_deploy.prototxt')
        model_path = os.path.join(model_dir, 'MobileNetSSD_deploy.caffemodel')

        # Controlla che i file esistano
        if not os.path.exists(proto_path) or not os.path.exists(model_path):
            self.get_logger().error(f"Modello non trovato: {proto_path} o {model_path}")
            raise FileNotFoundError("File del modello non trovato.")

        # Carica il modello
        self.net = cv2.dnn.readNetFromCaffe(proto_path, model_path)

        self.get_logger().info("Object Detection node avviato")





        self.subscription = self.create_subscription(
            Image,
            '/raw/image',
            self.image_callback,
            10)

        self.bridge = CvBridge()
        self.image_pub = self.create_publisher(Image, 'camera/detections', 10)
        self.data_pub = self.create_publisher(String, 'detected_objects', 10)


        proto_path = os.path.join(model_dir, 'MobileNetSSD_deploy.prototxt')
        model_path = os.path.join(model_dir, 'MobileNetSSD_deploy.caffemodel')

        self.net = cv2.dnn.readNetFromCaffe(proto_path, model_path)

        self.get_logger().info("Object Detection node avviato")

    def image_callback(self, msg):
        try:
            frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except Exception as e:
            self.get_logger().error(f"Errore conversione immagine: {e}")
            return

        (h, w) = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
        self.net.setInput(blob)
        detections = self.net.forward()

        detected_data = []

        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence > 0.5:
                idx = int(detections[0, 0, i, 1])
                label = CLASSES[idx]

                box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                x1, y1, x2, y2 = box.astype("int")

                detected_data.append({
                    "label": label,
                    "confidence": float(confidence),
                    "bbox": [int(x1), int(y1), int(x2), int(y2)]
                })

                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                y = y1 - 15 if y1 - 15 > 15 else y1 + 15
                cv2.putText(frame, f"{label} {int(confidence*100)}%", (x1, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # Pubblica immagine annotata
        img_msg = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
        self.image_pub.publish(img_msg)

        # Pubblica dati rilevati come JSON
        if detected_data:
            json_msg = String()
            json_msg.data = json.dumps(detected_data)
            self.data_pub.publish(json_msg)
            self.get_logger().info(f"Oggetti rilevati: {json_msg.data}")

def main(args=None):
    rclpy.init(args=args)
    node = ObjectDetectionNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
