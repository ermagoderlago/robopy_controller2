# object_detection_node.py

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String, Bool
from cv_bridge import CvBridge
import cv2
import numpy as np
import json
import os
import face_recognition
from ament_index_python.packages import get_package_share_directory
from datetime import datetime
from collections import defaultdict

import os
os.environ['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'
os.environ['FASTRTPS_TRANSPORT_USE_SHM'] = '0'


CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat",
    "bottle", "bus", "car", "cat", "chair", "cow",
    "diningtable", "dog", "horse", "motorbike", "person",
    "pottedplant", "sheep", "sofa", "train", "tvmonitor"
]

MAX_UNKNOWN_FACES_PER_SESSION = 5

class ObjectRecognitionNode(Node):
    def __init__(self):
        super().__init__('object_recognition_node')
        self.bridge = CvBridge()
        self.subscription = self.create_subscription(Image, '/rgb/image', self.image_callback, 10)
        self.motion_subscription = self.create_subscription(Bool, '/motion_detected', self.motion_callback, 10)

        self.image_pub = self.create_publisher(Image, 'camera/detections', 10)
        self.data_pub = self.create_publisher(String, 'detected_objects', 10)

        self.detection_active = False
        self.unknown_faces_saved = defaultdict(int)

        package_dir = get_package_share_directory('robopy_controller')
        model_dir = os.path.join(package_dir, 'models')
        proto_path = os.path.join(model_dir, 'MobileNetSSD_deploy.prototxt')
        model_path = os.path.join(model_dir, 'MobileNetSSD_deploy.caffemodel')
        self.net = cv2.dnn.readNetFromCaffe(proto_path, model_path)

        self.known_faces_dir = os.path.join(package_dir, 'known_faces')
        self.unknown_faces_dir = os.path.join(package_dir, 'unknown_faces')
        os.makedirs(self.unknown_faces_dir, exist_ok=True)

        self.known_encodings = []
        self.known_names = []
        self.load_known_faces()

        self.get_logger().info("Object + Face Recognition node started")

    def load_known_faces(self):
        for person in os.listdir(self.known_faces_dir):
            person_dir = os.path.join(self.known_faces_dir, person)
            if not os.path.isdir(person_dir):
                continue
            for img_name in os.listdir(person_dir):
                img_path = os.path.join(person_dir, img_name)
                image = face_recognition.load_image_file(img_path)
                encs = face_recognition.face_encodings(image)
                if encs:
                    self.known_encodings.append(encs[0])
                    self.known_names.append(person)

    def motion_callback(self, msg: Bool):
        self.detection_active = msg.data
        if not self.detection_active:
            self.unknown_faces_saved.clear()

    def image_callback(self, msg):
        if not self.detection_active:
            return

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

                if label == "person":
                    face_crop = frame[y1:y2, x1:x2]
                    face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
                    face_locations = face_recognition.face_locations(face_rgb)
                    face_encodings = face_recognition.face_encodings(face_rgb, face_locations)

                    for (top, right, bottom, left), enc in zip(face_locations, face_encodings):
                        matches = face_recognition.compare_faces(self.known_encodings, enc)
                        name = "Sconosciuto"

                        if True in matches:
                            first_match_index = matches.index(True)
                            name = self.known_names[first_match_index]
                        else:
                            if self.unknown_faces_saved[name] < MAX_UNKNOWN_FACES_PER_SESSION:
                                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                save_path = os.path.join(self.unknown_faces_dir, f"{timestamp}.jpg")
                                cv2.imwrite(save_path, face_crop)
                                self.unknown_faces_saved[name] += 1

                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        y_text = y1 - 15 if y1 - 15 > 15 else y1 + 15
                        cv2.putText(frame, f"{name}", (x1, y_text),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        img_msg = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
        self.image_pub.publish(img_msg)

        if detected_data:
            json_msg = String()
            json_msg.data = json.dumps(detected_data)
            self.data_pub.publish(json_msg)
            self.get_logger().info(f"Oggetti rilevati: {json_msg.data}")

def main(args=None):
    rclpy.init(args=args)
    node = ObjectRecognitionNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
