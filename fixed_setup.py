from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'robopy_controller'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'urdf'), glob('urdf/*')),
        (os.path.join('share', package_name, 'models'), [f for f in glob('robopy_controller/models/*') if os.path.isfile(f)]),
        (os.path.join('share', package_name, 'config'), [f for f in glob('config/*') if os.path.isfile(f)]),
        (os.path.join('share', package_name, 'config/wake_word'), [f for f in glob('config/wake_word/*') if os.path.isfile(f)]),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='robopy',
    maintainer_email='luca.suffia@gmail.com',
    description='TODO: Package description',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'respeaker_interface_node = robopy_controller.nodes.respeaker_interface_node:main',
            'robot_ai_node = robopy_controller.nodes.robot_ai_orchestrator:main',
            'respeaker_vui_node = robopy_controller.nodes.respeaker_vui_node:main',
            'audio_player_node = robopy_controller.audio_player_node:main',
            'homeassistant_node = robopy_controller.homeassistant_node:main',
            'foxglove_bridge = foxglove_bridge:main',
            'servo_coda_node = robopy_controller.servo_coda_node:main',
            'ultrasonic_sensor = robopy_controller.ultrasonic_sensor:main',
            'camera_node = robopy_controller.camera_node:main',
            'face_detector_node = robopy_controller.face_detector_node:main',
            'object_detector_node = robopy_controller.object_detector_node:main',
            'person_detector_node = robopy_controller.person_detector_node:main',
            'visual_servoing_node = robopy_controller.visual_servoing_node:main',
            'midas_depth_node = robopy_controller.midas_depth_node:main',
            'depth_to_pointcloud_node = robopy_controller.depth_to_pointcloud_node:main',
            'motion_detector_node = robopy_controller.motion_detector_node:main',
            'yuv_camera_publisher_node = robopy_controller.yuv_camera_publisher_node:main',
            'lite_mono_node = robopy_controller.lite_mono_node:main',
            'midas_lite_ONNX_node = robopy_controller.midas_lite_onnx_node:main',
            'lite_depth_node = robopy_controller.lite_depth_node:main',
            'lite_mono_depth_node = robopy_controller.nodes.lite_mono_depth_node:main',
            'servo_controller = robopy_controller.nodes.servo_node:main',
            'sync_publisher_node = robopy_controller.sync_publisher_node:main',
            'FastDepth_node = robopy_controller.nodes.FastDepth_node:main',
            'mobilenet_skipadd_node = robopy_controller.nodes.mobilenet_skipadd:main',
        ],
    },
)
