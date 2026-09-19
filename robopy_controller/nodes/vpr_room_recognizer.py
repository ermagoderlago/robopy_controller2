#!/usr/bin/env python3
"""
VPR Room Recognizer Node (CosPlace 512D on Hailo-10H NPU / ONNX CPU Fallback)
============================================================================
Extracts 512-dimensional L2-normalized image embeddings, matches them against
MAGRoomRegistry exemplar clusters and centroids, strictly enforces the >0.840
cosine similarity threshold, provides ROS 2 topic/service interfaces, and
updates TRINITY CAG EnvironmentSnapshot.

Conforms to:
  - SPEC-03: Hailo-10H InferModel API, Float32 dequantization, strict L2 norm.
  - SPEC-05: MAGRoomRegistry integration, LRU cache <= 64.
  - TC6 / R3 Acceptance Criteria: latency < 50ms, similarity > 0.840, < 3s recognition.
"""

import os
import sys
import time
import json
import math
import threading
from collections import deque
from typing import Dict, Any, List, Optional, Tuple, Union

import numpy as np

# Safe ROS 2 import guard
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from sensor_msgs.msg import Image, CompressedImage
    from std_msgs.msg import String, Float32MultiArray
    from std_srvs.srv import Trigger
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    Node = object

# Safe OpenCV import
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Safe HailoRT import
try:
    from hailo_platform import VDevice, FormatType
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

# Safe ONNX Runtime import
try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ONNX_AVAILABLE = False

# Import TRINITY dependencies safely
try:
    from robot_ai.trinity.mag_room_registry import MAGRoomRegistry
    from robot_ai.trinity.cag_environment import EnvironmentSnapshot
except ImportError:
    try:
        from robopy_controller.robot_ai.trinity.mag_room_registry import MAGRoomRegistry
        from robopy_controller.robot_ai.trinity.cag_environment import EnvironmentSnapshot
    except ImportError:
        MAGRoomRegistry = None
        EnvironmentSnapshot = None


class CosPlaceModelWrapper:
    """
    Dual-runtime CosPlace 512D embedding extractor.
    Operates on Hailo-10H NPU via InferModel API with graceful fallback
    to ONNX CPU or deterministic mock feature extractor.
    """
    VECTOR_DIM: int = 512

    def __init__(
        self,
        hef_path: Optional[str] = None,
        onnx_path: Optional[str] = None,
        force_sim: bool = False,
        input_size: Tuple[int, int] = (224, 224)
    ):
        self.hef_path = hef_path
        self.onnx_path = onnx_path
        self.force_sim = force_sim
        self.input_size = input_size  # (W, H)
        self.backend = "MOCK"
        self._lock = threading.Lock()

        self.vdevice = None
        self.infer_model = None
        self.configured_model = None
        self.bindings = None
        self.npu_input_buf = None
        self.npu_output_buf = None
        self.input_name = None
        self.output_name = None

        self.onnx_session = None

        self._initialize_runtime()

    def _initialize_runtime(self):
        """Initializes Hailo-10H InferModel, ONNX session, or Mock fallback."""
        if self.force_sim:
            self.backend = "MOCK"
            return

        # 1. Attempt Hailo-10H NPU initialization
        if HAILO_AVAILABLE and self.hef_path and os.path.exists(self.hef_path):
            try:
                self.vdevice = VDevice()
                self.infer_model = self.vdevice.create_infer_model(self.hef_path)

                # Hardware dequantization to Float32 (SPEC-03 Red Zone constraint)
                for outp in self.infer_model.outputs:
                    outp.set_format_type(FormatType.FLOAT32)

                self.configured_model = self.infer_model.configure()
                self.bindings = self.configured_model.create_bindings()

                self.input_name = self.infer_model.inputs[0].name
                self.output_name = self.infer_model.outputs[0].name
                in_shape = self.infer_model.input(self.input_name).shape
                self.input_size = (in_shape[1], in_shape[0])

                # Pre-allocate zero-copy buffers
                self.npu_input_buf = np.zeros(in_shape, dtype=np.uint8)
                self.npu_output_buf = np.zeros(self.VECTOR_DIM, dtype=np.float32)

                self.bindings.input(self.input_name).set_buffer(self.npu_input_buf)
                self.bindings.output(self.output_name).set_buffer(self.npu_output_buf)

                self.backend = "HAILO_NPU"
                return
            except Exception:
                pass

        # 2. Attempt ONNX Runtime Fallback
        if ONNX_AVAILABLE and self.onnx_path and os.path.exists(self.onnx_path):
            try:
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                self.onnx_session = ort.InferenceSession(self.onnx_path, opts, providers=["CPUExecutionProvider"])
                self.backend = "ONNX_CPU"
                return
            except Exception:
                pass

        # 3. Fallback to MOCK
        self.backend = "MOCK"

    @staticmethod
    def normalize_l2(vector: np.ndarray) -> np.ndarray:
        """
        Enforces strict L2 normalization (||v||_2 = 1.0 +- 1e-5).
        Rejects zero, NaN, or infinite vectors.
        """
        flat = vector.reshape(-1).astype(np.float32)
        if len(flat) != 512:
            raise ValueError(f"Vector length must be 512, got {len(flat)}")
        norm = float(np.linalg.norm(flat))
        if norm == 0.0 or np.isnan(norm) or np.isinf(norm):
            raise ValueError("Zero or NaN vector cannot be normalized")
        norm_vec = flat / norm
        # Strict validation
        assert abs(float(np.linalg.norm(norm_vec)) - 1.0) <= 1e-5, "Normalized vector must have unit L2 norm"
        return norm_vec.astype(np.float32)

    def preprocess(self, bgr_image: np.ndarray) -> np.ndarray:
        """Resizes and converts image to RGB model input format."""
        w, h = self.input_size
        if HAS_CV2:
            resized = cv2.resize(bgr_image, (w, h), interpolation=cv2.INTER_LINEAR)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        else:
            # Fallback simple nearest neighbor crop/resize
            rgb = bgr_image[:h, :w, :3].copy()
        return rgb

    def extract(self, bgr_image: np.ndarray) -> Tuple[np.ndarray, float]:
        """
        Extracts 512D L2-normalized embedding.
        Returns: (normalized_vector_512d, elapsed_latency_ms).
        """
        t0 = time.perf_counter()

        if self.backend == "HAILO_NPU":
            rgb = self.preprocess(bgr_image)
            with self._lock:
                np.copyto(self.npu_input_buf, rgb)
                self.configured_model.run([self.bindings], timeout_ms=1000)
                raw_out = self.npu_output_buf.copy()
            norm_vec = self.normalize_l2(raw_out)

        elif self.backend == "ONNX_CPU":
            rgb = self.preprocess(bgr_image)
            tensor = rgb.astype(np.float32) / 255.0
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            tensor = (tensor - mean) / std
            tensor = np.transpose(tensor, (2, 0, 1))
            tensor = np.expand_dims(tensor, axis=0)

            input_name = self.onnx_session.get_inputs()[0].name
            outputs = self.onnx_session.run(None, {input_name: tensor})
            norm_vec = self.normalize_l2(outputs[0])

        else:
            # Deterministic MOCK extraction based on image content
            mean_val = float(np.mean(bgr_image))
            norm_vec, _ = self.extract_embedding_mock(seed_feature=mean_val, simulated_latency_ms=15.0)

        latency_ms = (time.perf_counter() - t0) * 1000.0
        return norm_vec, latency_ms

    def extract_embedding_mock(
        self,
        seed_feature: float,
        simulated_latency_ms: float = 18.5,
        add_noise: bool = False
    ) -> Tuple[np.ndarray, float]:
        """
        Simulates Hailo-10H NPU CosPlace 512D extraction deterministically.
        Directly conforms to test_infrastructure.py contract.
        """
        seed_int = int(abs(seed_feature * 1000)) % (2**31 - 1)
        rng = np.random.RandomState(seed_int)
        raw = rng.randn(self.VECTOR_DIM).astype(np.float32)
        if add_noise:
            raw += 0.05 * rng.randn(self.VECTOR_DIM).astype(np.float32)
        norm_vec = self.normalize_l2(raw)
        return norm_vec, simulated_latency_ms


class VPRMatcherEngine:
    """
    Cosine similarity matcher against MAGRoomRegistry cluster exemplars and centroids.
    Enforces strict threshold (>0.840) and temporal multi-frame consensus.
    """
    MATCH_THRESHOLD: float = 0.840

    def __init__(
        self,
        room_registry: Optional[Any] = None,
        consensus_window: int = 5
    ):
        self.registry = room_registry
        self.consensus_window = consensus_window
        self.recent_predictions = deque(maxlen=consensus_window)
        self._lock = threading.RLock()

        # In-memory storage for room embeddings
        self.registered_rooms: Dict[str, List[np.ndarray]] = {}
        self.room_exemplars: Dict[str, np.ndarray] = {}
        self.room_centroids: Dict[str, np.ndarray] = {}

        if self.registry is not None:
            self.refresh_database_signatures()

    def register_room(self, room_name: str, embeddings: List[np.ndarray]):
        """Registers a set of CosPlace 512D descriptors for a room."""
        with self._lock:
            validated = []
            for v in embeddings:
                arr = np.asarray(v, dtype=np.float32).reshape(-1)
                assert arr.shape == (512,), f"Vector shape must be (512,), got {arr.shape}"
                norm = float(np.linalg.norm(arr))
                assert abs(norm - 1.0) < 1e-4, f"Vector must be L2 normalized, got norm {norm}"
                validated.append(arr)

            self.registered_rooms[room_name] = validated
            self.room_exemplars[room_name] = np.vstack(validated)
            # Compute normalized centroid
            centroid = np.mean(self.room_exemplars[room_name], axis=0)
            c_norm = float(np.linalg.norm(centroid))
            if c_norm > 1e-6:
                centroid = centroid / c_norm
            self.room_centroids[room_name] = centroid.astype(np.float32)

    def refresh_database_signatures(self):
        """Loads and caches room signatures from MAGRoomRegistry."""
        if self.registry is None:
            return

        with self._lock:
            self.registered_rooms.clear()
            self.room_exemplars.clear()
            self.room_centroids.clear()

            try:
                rooms = self.registry.list_rooms()
            except Exception:
                rooms = []

            for r in rooms:
                sigs = self.registry.get_room_signatures(r.room_name)
                if not sigs:
                    continue

                cluster = sigs.get("vpr_cluster")
                if cluster is not None and len(cluster) > 0:
                    arr = np.asarray(cluster, dtype=np.float32)
                    self.room_exemplars[r.room_name] = arr
                    self.registered_rooms[r.room_name] = [row for row in arr]

                centroid = sigs.get("vpr_centroid")
                if centroid is not None:
                    self.room_centroids[r.room_name] = np.asarray(centroid, dtype=np.float32)

    def match_single_frame(self, query_vector: np.ndarray) -> Dict[str, Any]:
        """
        Computes max cosine similarity across all registered room exemplars and centroids.
        Strictly enforces (similarity - 0.840) > 1e-5.
        Rejects 0.839 and 0.840; accepts 0.841.
        """
        q = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if q.size == 0 or not np.all(np.isfinite(q)):
            return {
                "matched": False,
                "room_name": None,
                "similarity": -1.0,
                "confidence": -1.0,
                "threshold": self.MATCH_THRESHOLD
            }

        best_room = None
        best_similarity = -1.0

        with self._lock:
            # Check cluster exemplars matrix
            for room_name, exemplars in self.room_exemplars.items():
                sims = np.dot(exemplars, q)
                max_sim = float(np.max(sims))
                if max_sim > best_similarity:
                    best_similarity = max_sim
                    best_room = room_name

            # Fallback check registered_rooms list if exemplars was empty
            if best_similarity < 0.0 and self.registered_rooms:
                for room_name, room_vecs in self.registered_rooms.items():
                    for ref_vec in room_vecs:
                        sim = float(np.dot(q, ref_vec))
                        if sim > best_similarity:
                            best_similarity = sim
                            best_room = room_name

            # Centroid check fallback
            if best_similarity < 0.0 and self.room_centroids:
                for room_name, centroid in self.room_centroids.items():
                    sim = float(np.dot(q, centroid))
                    if sim > best_similarity:
                        best_similarity = sim
                        best_room = room_name

        matched = (best_similarity - self.MATCH_THRESHOLD) > 1e-5
        return {
            "matched": matched,
            "room_name": best_room if matched else None,
            "similarity": round(best_similarity, 4),
            "confidence": round(best_similarity, 4),
            "threshold": self.MATCH_THRESHOLD
        }

    def match_room(self, query_vector: np.ndarray) -> Dict[str, Any]:
        """Alias matching test_infrastructure.py contract."""
        return self.match_single_frame(query_vector)

    def evaluate_consensus(self, single_match: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates temporal consensus over sliding window.
        High confidence (>0.90) fast-tracks immediate return.
        """
        self.recent_predictions.append(single_match)

        if not single_match["matched"]:
            return single_match

        # Fast-track high similarity
        if single_match["similarity"] > 0.90:
            return single_match

        # Count agreements in sliding window
        target_room = single_match["room_name"]
        agreements = sum(
            1 for p in self.recent_predictions
            if p["matched"] and p["room_name"] == target_room
        )

        # Require >= 2 agreements if window has >= 2 elements
        if len(self.recent_predictions) >= 2 and agreements < 2:
            return {
                "matched": False,
                "room_name": None,
                "similarity": single_match["similarity"],
                "confidence": single_match["similarity"],
                "threshold": self.MATCH_THRESHOLD,
                "consensus_pending": True
            }

        return single_match


class VPRRoomRecognizerNode(Node):
    """ROS 2 Node encapsulating CosPlace 512D inference, matching, and TRINITY fusion."""

    def __init__(self, environment_snapshot: Optional[Any] = None, registry: Optional[Any] = None):
        if not HAS_ROS2:
            raise RuntimeError("ROS 2 (rclpy) is not available in the current environment.")
        super().__init__("vpr_room_recognizer")

        # Declare parameters
        self.declare_parameter("hef_path", "/opt/robopy/models/cosplace_512.hef")
        self.declare_parameter("onnx_path", "/opt/robopy/models/cosplace_512.onnx")
        self.declare_parameter("sim_mode", False)
        self.declare_parameter("similarity_threshold", 0.84)
        self.declare_parameter("consensus_window", 5)
        self.declare_parameter("db_path", "/home/robopy/mag_trinity.db")
        self.declare_parameter("yaml_path", "/opt/robopy/config/rooms_metadata.yaml")

        hef_path = self.get_parameter("hef_path").get_parameter_value().string_value
        onnx_path = self.get_parameter("onnx_path").get_parameter_value().string_value
        sim_mode = self.get_parameter("sim_mode").get_parameter_value().bool_value
        db_path = self.get_parameter("db_path").get_parameter_value().string_value
        yaml_path = self.get_parameter("yaml_path").get_parameter_value().string_value
        consensus_win = int(self.get_parameter("consensus_window").get_parameter_value().integer_value)

        self.latest_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()

        # Initialize MAG Room Registry & TRINITY
        if registry is not None:
            self.registry = registry
        elif MAGRoomRegistry is not None:
            self.registry = MAGRoomRegistry(db_path=db_path, yaml_path=yaml_path)
        else:
            self.registry = None

        if environment_snapshot is not None:
            self.env_snapshot = environment_snapshot
        elif EnvironmentSnapshot is not None:
            self.env_snapshot = EnvironmentSnapshot()
        else:
            self.env_snapshot = None

        # Initialize Model & Matcher
        self.extractor = CosPlaceModelWrapper(hef_path=hef_path, onnx_path=onnx_path, force_sim=sim_mode)
        self.matcher = VPRMatcherEngine(room_registry=self.registry, consensus_window=consensus_win)

        # QoS Profiles
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Subscriptions
        self.sub_image = self.create_subscription(
            Image, "/camera/color/image_raw", self._on_image_raw, sensor_qos
        )
        self.sub_compressed = self.create_subscription(
            CompressedImage, "/camera/color/image_raw/compressed", self._on_image_compressed, sensor_qos
        )

        # Publishers
        self.pub_room_id = self.create_publisher(String, "/perception/vpr_room_id", 10)
        self.pub_embedding = self.create_publisher(Float32MultiArray, "/perception/vpr/embedding", 10)

        # Services
        self.srv_recognize = self.create_service(
            Trigger, "/perception/recognize_room_vpr", self._handle_recognize_service
        )

        # Periodic processing timer (5 Hz)
        self.timer = self.create_timer(0.2, self._process_current_frame)
        self.last_recognized_room = None

        self.get_logger().info(f"VPR Room Recognizer Node initialized. Backend: {self.extractor.backend}")

    def _on_image_raw(self, msg: Image):
        try:
            arr = np.frombuffer(msg.data, dtype=np.uint8).reshape((msg.height, msg.width, -1))
            with self.frame_lock:
                self.latest_frame = arr
        except Exception as e:
            self.get_logger().error(f"Error decoding raw image: {e}")

    def _on_image_compressed(self, msg: CompressedImage):
        if self.latest_frame is None and HAS_CV2:
            try:
                np_arr = np.frombuffer(msg.data, np.uint8)
                cv_img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                with self.frame_lock:
                    self.latest_frame = cv_img
            except Exception as e:
                self.get_logger().error(f"Error decoding compressed image: {e}")

    def _process_current_frame(self):
        with self.frame_lock:
            if self.latest_frame is None:
                return
            frame = self.latest_frame.copy()

        try:
            vec, lat_ms = self.extractor.extract(frame)
            raw_match = self.matcher.match_single_frame(vec)
            match_res = self.matcher.evaluate_consensus(raw_match)

            # Publish raw embedding (Float32MultiArray)
            emb_msg = Float32MultiArray()
            emb_msg.data = vec.tolist()
            self.pub_embedding.publish(emb_msg)

            # Publish JSON topic payload
            payload = {
                "timestamp": time.time(),
                "matched": match_res["matched"],
                "room_name": match_res["room_name"],
                "similarity": match_res["similarity"],
                "threshold": match_res["threshold"],
                "latency_ms": round(lat_ms, 2),
                "backend": self.extractor.backend
            }
            str_msg = String()
            str_msg.data = json.dumps(payload)
            self.pub_room_id.publish(str_msg)

            # Update TRINITY if room identified and state changed
            if match_res["matched"] and match_res["room_name"]:
                curr_room = match_res["room_name"]
                if curr_room != self.last_recognized_room:
                    self.last_recognized_room = curr_room
                    loc = (0.0, 0.0)
                    map_name = "default"
                    if self.registry is not None:
                        room_meta = self.registry.get_room(curr_room)
                        if room_meta:
                            loc = getattr(room_meta, "centroid", (0.0, 0.0))
                            map_name = getattr(room_meta, "map_name", "default")
                    if self.env_snapshot is not None:
                        self.env_snapshot.update_location(
                            room_name=curr_room,
                            location=loc,
                            map_name=map_name
                        )
                    self.get_logger().info(
                        f"Room Identified via VPR: '{curr_room}' (Sim: {match_res['similarity']:.3f}, Lat: {lat_ms:.1f}ms)"
                    )
        except Exception as e:
            self.get_logger().error(f"Error in VPR processing cycle: {e}")

    def _handle_recognize_service(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        """Service callback for /perception/recognize_room_vpr."""
        with self.frame_lock:
            frame = self.latest_frame.copy() if self.latest_frame is not None else None

        if frame is None:
            response.success = False
            response.message = json.dumps({
                "matched": False,
                "error": "No camera frame available"
            })
            return response

        t0 = time.perf_counter()
        try:
            vec, lat_ms = self.extractor.extract(frame)
            match_res = self.matcher.match_single_frame(vec)
            elapsed_total_ms = (time.perf_counter() - t0) * 1000.0

            payload = {
                "matched": match_res["matched"],
                "room_name": match_res["room_name"],
                "confidence": match_res["similarity"],
                "similarity": match_res["similarity"],
                "threshold": match_res["threshold"],
                "latency_ms": round(lat_ms, 2),
                "total_time_ms": round(elapsed_total_ms, 2),
                "backend": self.extractor.backend
            }

            response.success = match_res["matched"]
            response.message = json.dumps(payload)
        except Exception as e:
            response.success = False
            response.message = json.dumps({"matched": False, "error": str(e)})

        return response


def main(args=None):
    if not HAS_ROS2:
        print("ROS 2 (rclpy) is not installed in current environment.")
        return
    rclpy.init(args=args)
    node = VPRRoomRecognizerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
