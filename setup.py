from setuptools import find_packages, setup
import os
from glob import glob

package_name = "robopy_controller"

# Helper function to exclude __pycache__ and directories
def filter_data_files(file_list):
    return [
        f for f in file_list 
        if '__pycache__' not in f and os.path.isfile(f)
    ]

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(where='.', exclude=['__pycache__']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), 
         filter_data_files(glob('launch/*.py'))),
        (os.path.join('share', package_name, 'urdf'), 
         filter_data_files(glob('urdf/*'))),
        
        # Corretto: include ricorsivamente tutti i file in weights
        (os.path.join('share', package_name, 'weights'), 
         filter_data_files(glob('robopy_controller/weights/**/*', recursive=True))),
        
        (os.path.join('share', package_name, 'networks'), 
         filter_data_files(glob('robopy_controller/networks/*.py'))),
        (os.path.join('share', package_name, 'known_faces'), 
         filter_data_files(glob('robopy_controller/known_faces/*'))),
        (os.path.join('share', package_name, 'unknown_faces'), 
         filter_data_files(glob('robopy_controller/unknown_faces/*'))),
        
        # Corretto: include tutti i file YAML ricorsivamente
        (os.path.join('share', package_name, 'config'), 
         filter_data_files(glob('robopy_controller/config/**/*.yaml', recursive=True)))
    ],
    install_requires=['setuptools'],
    zip_safe=False,
    maintainer="luca suffia",
    maintainer_email="suffia.luca@gmail.com",
    description="RoboPY controller package",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        'console_scripts': [
            'motor_control_node = robopy_controller.motor_control_node:main',
            'ultrasonic_sensor = robopy_controller.ultrasonic_sensor:main',
            'bluedot_node = robopy_controller.bluedot_node:main',
            'odometry_node = robopy_controller.odometry_node:main',
            'camera_publisher_node = robopy_controller.camera_publisher_node:main',
            'object_detection_node = robopy_controller.object_detection_node:main',
            'midas_depth_node = robopy_controller.midas_depth_node:main',
            'depth_to_pointcloud_node = robopy_controller.depth_to_pointcloud_node:main',
            'motion_detector_node = robopy_controller.motion_detector_node:main',
            'yuv_camera_publisher_node = robopy_controller.yuv_camera_publisher_node:main',
            'lite_mono_node = robopy_controller.lite_mono_node:main',
            'midas_lite_ONNX_node = robopy_controller.midas_lite_onnx_node:main',
            'lite_depth_node = robopy_controller.lite_depth_node:main',
            'lite_mono_depth_node = robopy_controller.nodes.lite_mono_depth_node:main',
            'servo_node = robopy_controller.nodes.servo_node:main',
            'sync_publisher_node = robopy_controller.sync_publisher_node:main',
            'FastDepth_node = robopy_controller.nodes.FastDepth_node:main',
            'mobilenet_skipadd_node = robopy_controller.nodes.mobilenet_skipadd:main',
            'gray_camera_publisher_node = robopy_controller.gray_camera_publisher_node:main',
            'bno080_odom_node = robopy_controller.nodes.bno080_odom_node:main',
            'IMU_node = robopy_controller.nodes.IMU_node:main',
            'odometria_ibrida_node = robopy_controller.nodes.odometria_ibrida_node:main',
            'ekf_localization_node = robopy_controller.nodes.ekf_localization_node:main',
            'rtabmap_node = robopy_controller.nodes.rtabmap_node:main',
            'performance_monitor = robopy_controller.nodes.performance_monitor:main',
            'topic_checker_node = robopy_controller.nodes.topic_checker_node:main',
            'fastdepth_node = robopy_controller.nodes.fastdepth_node:main',
            'v4l2_camera_node = robopy_controller.nodes.v4l2_camera_node:main',
            'web_video_stream_node = robopy_controller.nodes.web_video_stream_node:main',
            'homeassistant_node = robopy_controller.nodes.homeassistant_node:main',
        ],
    },
)