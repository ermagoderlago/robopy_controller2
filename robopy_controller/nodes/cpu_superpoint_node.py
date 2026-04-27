#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from sensor_msgs.msg import Image
from std_msgs.msg import Float32MultiArray, MultiArrayDimension
from rtabmap_msgs.msg import UserData

from cv_bridge import CvBridge

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import cv2
import os
import time
import struct

# ------------------------------------------------------------------
# CPU OPTIMIZATION
# ------------------------------------------------------------------
torch.set_num_threads(1)

# ------------------------------------------------------------------
# SUPERPOINT MODEL (FULL: detector + descriptor)
# ------------------------------------------------------------------
class SuperPointNet(nn.Module):
    def __init__(self):
        super().__init__()

        self.relu = nn.ReLU(inplace=True)
        self.pool = nn.MaxPool2d(2, 2)

        c1, c2, c3, c4, c5 = 64, 64, 128, 128, 256

        self.conv1a = nn.Conv2d(1, c1, 3, 1, 1)
        self.conv1b = nn.Conv2d(c1, c1, 3, 1, 1)
        self.conv2a = nn.Conv2d(c1, c2, 3, 1, 1)
        self.conv2b = nn.Conv2d(c2, c2, 3, 1, 1)
        self.conv3a = nn.Conv2d(c2, c3, 3, 1, 1)
        self.conv3b = nn.Conv2d(c3, c3, 3, 1, 1)
        self.conv4a = nn.Conv2d(c3, c4, 3, 1, 1)
        self.conv4b = nn.Conv2d(c4, c4, 3, 1, 1)

        # Detector head
        self.convPa = nn.Conv2d(c4, c5, 3, 1, 1)
        self.convPb = nn.Conv2d(c5, 65, 1, 1, 0)

        # Descriptor head
        self.convDa = nn.Conv2d(c4, c5, 3, 1, 1)
        self.convDb = nn.Conv2d(c5, 256, 1, 1, 0)

    def forward(self, x):
        x = self.relu(self.conv1a(x))
        x = self.relu(self.conv1b(x))
        x = self.pool(x)

        x = self.relu(self.conv2a(x))
        x = self.relu(self.conv2b(x))
        x = self.pool(x)

        x = self.relu(self.conv3a(x))
        x = self.relu(self.conv3b(x))
        x = self.pool(x)

        x = self.relu(self.conv4a(x))
        x = self.relu(self.conv4b(x))

        # Detector
        cPa = self.relu(self.convPa(x))
        semi = self.convPb(cPa)

        # Descriptor
        cDa = self.relu(self.convDa(x))
        desc = self.convDb(cDa)
        desc = F.normalize(desc, p=2, dim=1)

        return semi, desc


# ------------------------------------------------------------------
# CPU SUPERPOINT NODE
# ------------------------------------------------------------------
class CpuSuperPointNode(Node):
    def __init__(self):
        super().__init__("cpu_superpoint_node")

        self.declare_parameter("weights_path", "")
        self.declare_parameter("input_topic", "/oak/rgb/image_raw")
        self.declare_parameter("conf_thresh", 0.015)
        self.declare_parameter("max_fps", 5.0)
        self.declare_parameter("w", 320)
        self.declare_parameter("h", 200)

        self.weights_path = self.get_parameter("weights_path").value
        self.input_topic = self.get_parameter("input_topic").value
        self.conf_thresh = self.get_parameter("conf_thresh").value
        self.max_fps = self.get_parameter("max_fps").value
        self.W = self.get_parameter("w").value
        self.H = self.get_parameter("h").value

        # Model
        self.net = SuperPointNet().cpu().eval()

        if not os.path.exists(self.weights_path):
            self.get_logger().fatal("SuperPoint weights not found")
            raise RuntimeError("Missing weights")

        self.net.load_state_dict(
            torch.load(self.weights_path, map_location="cpu")
        )

        self.bridge = CvBridge()

        self.last_time = 0.0
        self.min_dt = 1.0 / self.max_fps

        # ROS IO
        self.sub = self.create_subscription(
            Image, self.input_topic, self.image_cb, qos_profile_sensor_data
        )

        self.pub_overlay = self.create_publisher(
            Image, "/cpu/superpoint/overlay", 1
        )
        self.pub_kpts = self.create_publisher(
            Float32MultiArray, "/cpu/superpoint/keypoints", 1
        )
        self.pub_userdata = self.create_publisher(
            UserData, "/superpoint/user_data", 1
        )

        self.get_logger().info("✅ CPU SuperPoint + RTAB-Map READY")

    # --------------------------------------------------------------
    def image_cb(self, msg):
        now = time.time()
        if now - self.last_time < self.min_dt:
            return
        self.last_time = now

        img = self.bridge.imgmsg_to_cv2(msg, "mono8")
        img = cv2.resize(img, (self.W, self.H), cv2.INTER_NEAREST)

        inp = torch.from_numpy(img.astype(np.float32) / 255.0)
        inp = inp.view(1, 1, self.H, self.W)

        with torch.inference_mode():
            semi, desc = self.net(inp)

            dense = F.softmax(semi, dim=1)
            nodust = dense[:, :-1]
            heatmap = F.pixel_shuffle(nodust, 8)
            prob = heatmap.squeeze().numpy()

            desc_map = desc.squeeze().numpy()

        kpts, scores = self.nms(prob)

        if len(scores) == 0:
            return

        descriptors = self.sample_descriptors(kpts, desc_map)

        self.publish_userdata(kpts, descriptors, msg.header)
        self.publish_debug(img, kpts, scores, msg.header)

    # --------------------------------------------------------------
    def nms(self, prob):
        if prob.max() < self.conf_thresh:
            return np.empty((0, 2)), np.empty(0)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        mx = cv2.dilate(prob, kernel)
        mask = (prob == mx) & (prob > self.conf_thresh)
        pts = np.argwhere(mask)

        return pts, prob[mask]

    # --------------------------------------------------------------
    def sample_descriptors(self, kpts, desc_map):
        D = []
        for y, x in kpts:
            dx = int(x / 8)
            dy = int(y / 8)
            D.append(desc_map[:, dy, dx])
        return np.stack(D).astype(np.float32)

    # --------------------------------------------------------------
    def publish_userdata(self, kpts, desc, header):
        buf = bytearray()
        n = len(desc)
        buf += struct.pack("I", n)

        for y, x in kpts:
            buf += struct.pack("ff", float(x), float(y))

        for d in desc:
            buf += struct.pack("256f", *d)

        msg = UserData()
        msg.header = header
        msg.data = bytes(buf)
        self.pub_userdata.publish(msg)

    # --------------------------------------------------------------
    def publish_debug(self, img, kpts, scores, header):
        # keypoints
        kp = Float32MultiArray()
        data = np.zeros((len(scores), 3), np.float32)
        data[:, 0] = kpts[:, 1]
        data[:, 1] = kpts[:, 0]
        data[:, 2] = scores
        kp.data = data.flatten().tolist()
        kp.layout.dim = [
            MultiArrayDimension(label="pts", size=len(scores), stride=len(scores)*3)
        ]
        self.pub_kpts.publish(kp)

        if self.pub_overlay.get_subscription_count() > 0:
            vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            for y, x in kpts:
                cv2.circle(vis, (int(x), int(y)), 2, (0, 255, 0), -1)
            im = self.bridge.cv2_to_imgmsg(vis, "bgr8")
            im.header = header
            self.pub_overlay.publish(im)


# ------------------------------------------------------------------
def main():
    rclpy.init()
    node = CpuSuperPointNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    rclpy.shutdown()
