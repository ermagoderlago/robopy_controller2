#!/usr/bin/env python3
# image_compressor_node.py
# Nodo ROS 2 per pubblicare immagini compresse (Foxglove-friendly)
# Ottimizzato per Raspberry Pi

import time
import cv2
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSProfile,
    ReliabilityPolicy,
    HistoryPolicy,
    DurabilityPolicy
)

from sensor_msgs.msg import Image, CompressedImage


class ImageCompressorNode(Node):

    def __init__(self):
        super().__init__("image_compressor")

        # ---------------------------------------------------------
        # QoS: Sensor Data (fondamentale per streaming video)
        # ---------------------------------------------------------
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            durability=DurabilityPolicy.VOLATILE
        )

        # ---------------------------------------------------------
        # Parameters
        # ---------------------------------------------------------
        self.declare_parameter("ui_fps", 10.0)
        self.declare_parameter("jpeg_quality", 50)
        self.declare_parameter("resize_factor", 1.0) # Ridimensiona le immagini prima della compressione, era 0.5

        self.ui_fps = float(self.get_parameter("ui_fps").value)
        self.jpeg_quality = int(self.get_parameter("jpeg_quality").value)
        self.resize_factor = float(self.get_parameter("resize_factor").value)

        self.min_interval = 1.0 / self.ui_fps if self.ui_fps > 0 else 0.0

        # Throttling per-topic (tempo ultimo frame pubblicato)
        self.last_pub_time = {
            "mono": 0.0,
            "debug": 0.0,
            "depth": 0.0
        }

        # ---------------------------------------------------------
        # Subscribers
        # ---------------------------------------------------------
        self.sub_mono = self.create_subscription(
            Image,
            "/camera/image_raw",
            self.cb_mono,
            qos
        )

        self.sub_debug = self.create_subscription(
            Image,
            "/superpoint/debug_image",
            self.cb_debug,
            qos
        )

        self.sub_depth = self.create_subscription(
            Image,
            "/depth/image_raw",
            self.cb_depth,
            qos
        )

        # ---------------------------------------------------------
        # Publishers
        # ---------------------------------------------------------
        self.pub_mono = self.create_publisher(
            CompressedImage,
            "/camera/image_raw/compressed",
            qos
        )

        self.pub_debug = self.create_publisher(
            CompressedImage,
            "/superpoint/debug_image/compressed",
            qos
        )

        self.pub_depth = self.create_publisher(
            CompressedImage,
            "/depth/image_raw/compressedDepth",
            qos
        )

        self.get_logger().info(
            f"🚀 ImageCompressor ready | "
            f"FPS: {self.ui_fps} | "
            f"Resize: {self.resize_factor}x | "
            f"JPEG quality: {self.jpeg_quality}"
        )

    # ---------------------------------------------------------
    # Utility
    # ---------------------------------------------------------
    def should_publish(self, key: str) -> bool:
        """Limita il frame-rate per ogni topic"""
        now = time.monotonic()
        if now - self.last_pub_time[key] < self.min_interval:
            return False
        self.last_pub_time[key] = now
        return True

    def compress_jpeg(self, frame: np.ndarray) -> bytes | None:
        """Resize + JPEG encode (velocissimo su RPi)"""
        if self.resize_factor != 1.0:
            h, w = frame.shape[:2]
            frame = cv2.resize(
                frame,
                (int(w * self.resize_factor), int(h * self.resize_factor)),
                interpolation=cv2.INTER_LINEAR
            )

        success, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        )

        return encoded.tobytes() if success else None

    def compress_depth_png(self, depth: np.ndarray) -> bytes | None:
        """PNG veloce per depth (lossless)"""
        if self.resize_factor != 1.0:
            h, w = depth.shape
            depth = cv2.resize(
                depth,
                (int(w * self.resize_factor), int(h * self.resize_factor)),
                interpolation=cv2.INTER_NEAREST
            )

        success, encoded = cv2.imencode(
            ".png",
            depth,
            [cv2.IMWRITE_PNG_COMPRESSION, 1]
        )

        return encoded.tobytes() if success else None

    # ---------------------------------------------------------
    # Callbacks
    # ---------------------------------------------------------
    def cb_mono(self, msg: Image):
        if not self.should_publish("mono"):
            return

        frame = np.frombuffer(msg.data, dtype=np.uint8).reshape(
            msg.height, msg.width
        )

        data = self.compress_jpeg(frame)
        if data is None:
            return

        out = CompressedImage()
        out.header = msg.header
        out.format = "jpeg"
        out.data = data
        self.pub_mono.publish(out)

    def cb_debug(self, msg: Image):
        if not self.should_publish("debug"):
            return

        frame = np.frombuffer(msg.data, dtype=np.uint8).reshape(
            msg.height, msg.width, -1
        )

        data = self.compress_jpeg(frame)
        if data is None:
            return

        out = CompressedImage()
        out.header = msg.header
        out.format = "jpeg"
        out.data = data
        self.pub_debug.publish(out)

    def cb_depth(self, msg: Image):
        if not self.should_publish("depth"):
            return

        depth = np.frombuffer(msg.data, dtype=np.uint16).reshape(
            msg.height, msg.width
        )

        data = self.compress_depth_png(depth)
        if data is None:
            return

        out = CompressedImage()
        out.header = msg.header
        out.format = "png; encoding=16UC1"
        out.data = data
        self.pub_depth.publish(out)


def main(args=None):
    rclpy.init(args=args)
    node = ImageCompressorNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
