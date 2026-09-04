from setuptools import setup, find_packages
import os
from glob import glob

package_name = "robopy_controller"


def safe_glob(pattern):
    """
    Glob sicuro per setup.py:
    - solo file reali
    - ignora directory
    - evita crash durante colcon --dry-run
    """
    files = []
    exclude_dirs = {"__pycache__", "build", "install", "log", ".git"}
    for f in glob(pattern, recursive=True):
        if os.path.isfile(f):
            path_parts = f.replace("\\", "/").split("/")
            if any(part in exclude_dirs for part in path_parts):
                continue
            files.append(f)
    return files


setup(
    name=package_name,
    version="0.01.0",
    packages=find_packages(exclude=["test", "__pycache__"]),

    data_files=[
        # Ament index
        (
            "share/ament_index/resource_index/packages",
            [os.path.join("resource", package_name)],
        ),

        # package.xml
        (
            "share/" + package_name,
            ["package.xml"],
        ),

        # Launch files
        (
            os.path.join("share", package_name, "launch"),
            safe_glob("launch/*.py"),
        ),

        # URDF
        (
            os.path.join("share", package_name, "urdf"),
            safe_glob("urdf/*"),
        ),

        # Config YAML
        (
            os.path.join("share", package_name, "config"),
            safe_glob("robopy_controller/config/**/*.yaml"),
        ),

        # Weights (NN)
        (
            os.path.join("share", package_name, "weights"),
            safe_glob("robopy_controller/weights/**/*"),
        ),

        # Networks
        (
            os.path.join("share", package_name, "networks"),
            safe_glob("robopy_controller/networks/*.py"),
        ),

                # OAK-D Models (blob files) - NUOVA SEZIONE
        (
            os.path.join("share", package_name, "models"),
            safe_glob("robopy_controller/models/**/*.*blob") + 
            safe_glob("robopy_controller/models/*.*blob") +
            safe_glob("robopy_controller/models/**/*.json") +
            safe_glob("robopy_controller/models/**/*.xml"),
        ),

        # Faces DB
        (
            os.path.join("share", package_name, "known_faces"),
            safe_glob("robopy_controller/known_faces/**/*"),
        ),
        (
            os.path.join("share", package_name, "unknown_faces"),
            safe_glob("robopy_controller/unknown_faces/*"),
        ),
        # Wake Word Config
        (
            os.path.join("share", package_name, "config", "wake_word"),
            safe_glob("robopy_controller/config/wake_word/*"),
        ),
    ],

    install_requires=["setuptools"],
    zip_safe=False,

    maintainer="luca suffia",
    maintainer_email="suffia.luca@gmail.com",
    description="RoboPY controller package",
    license="Apache-2.0",

    extras_require={
        "test": ["pytest"],
    },

    entry_points={
        "console_scripts": [
            "ultrasonic_sensor = robopy_controller.ultrasonic_sensor:main",
            "bluedot_node = robopy_controller.bluedot_node:main",
            "odometry_node = robopy_controller.odometry_node:main",
            "camera_publisher_node = robopy_controller.camera_publisher_node:main",
            "object_detection_node = robopy_controller.object_detection_node:main",
            "midas_depth_node = robopy_controller.midas_depth_node:main",
            "depth_to_pointcloud_node = robopy_controller.depth_to_pointcloud_node:main",
            "motion_detector_node = robopy_controller.motion_detector_node:main",
            "yuv_camera_publisher_node = robopy_controller.yuv_camera_publisher_node:main",
            "lite_mono_node = robopy_controller.lite_mono_node:main",
            "midas_lite_ONNX_node = robopy_controller.midas_lite_onnx_node:main",
            "lite_depth_node = robopy_controller.lite_depth_node:main",
            

            # nodes/
            "lite_mono_depth_node = robopy_controller.nodes.lite_mono_depth_node:main",
            "servo_node = robopy_controller.nodes.servo_node:main",
            "FastDepth_node = robopy_controller.nodes.FastDepth_node:main",
            "mobilenet_skipadd_node = robopy_controller.nodes.mobilenet_skipadd:main",
            "gray_camera_publisher_node = robopy_controller.gray_camera_publisher_node:main",
            "bno080_odom_node = robopy_controller.nodes.bno080_odom_node:main",
            "IMU_node = robopy_controller.nodes.IMU_node:main",
            "odometria_ibrida_node = robopy_controller.nodes.odometria_ibrida_node:main",
            "ekf_localization_node = robopy_controller.nodes.ekf_localization_node:main",
            "rtabmap_node = robopy_controller.nodes.rtabmap_node:main",
            "performance_monitor = robopy_controller.nodes.performance_monitor:main",
            "topic_checker_node = robopy_controller.nodes.topic_checker_node:main",
            "fastdepth_node = robopy_controller.nodes.fastdepth_node:main",
            "v4l2_camera_node = robopy_controller.nodes.v4l2_camera_node:main",
            "web_video_stream_node = robopy_controller.nodes.web_video_stream_node:main",
            "homeassistant_node = robopy_controller.nodes.homeassistant_node:main",
            "oak_d_lite_node = robopy_controller.nodes.oak_d_lite_node:main",
            "imu_bridge_node = robopy_controller.nodes.imu_bridge_node:main",
            "IMU_oakd_node = robopy_controller.nodes.IMU_oakd_node:main",
            "oakd_camera_publisher_node = robopy_controller.nodes.oakd_camera_publisher_node:main",
            #questo solo per test
            "oakd_camera_publisher_node_test = robopy_controller.nodes.oakd_camera_publisher_node_test:main",
            
            "map_manager_node = robopy_controller.nodes.map_manager_node:main",
            "imu_transformer_node = robopy_controller.nodes.imu_transformer_node:main",
            "madgwick_node = robopy_controller.nodes.madgwick_node:main",
            "teleop_node = robopy_controller.nodes.teleop_node:main",
            "dynamic_camera_tf_node = robopy_controller.nodes.dynamic_camera_tf_node:main",
            "object_3d_mapper = robopy_controller.nodes.object_3d_mapper:main",
            "stereo_camera_info_converter = robopy_controller.nodes.stereo_camera_info_converter:main",
            "oakd_camera_publisher_node_super = robopy_controller.nodes.oakd_camera_publisher_node_super:main",
            "oakd_camera_publisher_node_v2 = robopy_controller.nodes.oakd_camera_publisher_node_v2:main",
            "servo_coda_node = robopy_controller.nodes.servo_coda_node:main",
            "cpu_superpoint_node = robopy_controller.nodes.cpu_superpoint_node:main",
            "system_monitor_node = robopy_controller.nodes.system_monitor_node:main",
            "superpoint_node = robopy_controller.nodes.superpoint_node:main",
            "spectacular_vio_node = robopy_controller.nodes.spectacular_vio_node:main",
            "camera_info_publisher = robopy_controller.nodes.camera_info_publisher:main",
            "nav2_bridge_node = robopy_controller.nodes.nav2_bridge_node:main",
            "teleop_bridge_node = robopy_controller.nodes.teleop_bridge_node:main",
            "image_compressor_node = robopy_controller.nodes.image_compressor_node:main",
            "oak_driver_node = robopy_controller.nodes.oak_driver_node:main",
            "oak_superpoint_odometry_node = robopy_controller.nodes.oak_superpoint_odometry_node:main",
            "robot_ai_node = robopy_controller.nodes.robot_ai_node:main",
            "waveshare_motor_driver = robopy_controller.nodes.waveshare_motor_driver:main",
            "battery_manager_node = robopy_controller.nodes.battery_manager_node:main",
            "foxglove_nav2_bridge = robopy_controller.nodes.foxglove_nav2_bridge:main",
            "respeaker_vui_node = robopy_controller.nodes.respeaker_vui_node:main",
            "ai_chat = scripts.ai_chat:main",
            "hailo_bridge_node = robopy_controller.nodes.hailo_bridge_node:main",
            "hailo_vlm_node = robopy_controller.nodes.hailo_vlm_node:main",
            "hailo_kws_node = robopy_controller.nodes.hailo_kws_node:main",
            "semantic_costmap_injector = robopy_controller.nodes.semantic_costmap_injector:main",
            "engagement_monitor = robopy_controller.nodes.engagement_monitor:main",
            "cloud_watchdog_node = robopy_controller.nodes.cloud_watchdog_node:main",
            "speaker_id_node = robopy_controller.nodes.speaker_id_node:main",
            "localization_fuser_node = robopy_controller.nodes.localization_fuser_node:main",
            "robot_health_supervisor = robopy_controller.nodes.robot_health_supervisor:main",
            "room_mapping_scan_node = robopy_controller.nodes.room_mapping_scan_node:main",
            "attention_supervisor_node = robopy_controller.nodes.attention_supervisor_node:main",
            "system_lifecycle_coordinator_node = robopy_controller.nodes.system_lifecycle_coordinator_node:main",
            "live_connection_bridge_node = robopy_controller.robot_ai.services.live_connection_bridge_node:main",
            # === Cognitive Brain ===
            "chroma_synaptic_manager = robopy_controller.robot_ai.cognitive.chroma_synaptic_manager:main",
            "cognitive_amygdala = robopy_controller.robot_ai.cognitive.cognitive_amygdala:main",
            "cognitive_core_node = robopy_controller.robot_ai.cognitive.cognitive_core_node:main",
            "neuro_vegetative_bridge = robopy_controller.robot_ai.cognitive.neuro_vegetative_bridge:main",
        ],
    },
)
