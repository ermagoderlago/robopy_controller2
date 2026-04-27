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
    for f in glob(pattern, recursive=True):
        if os.path.isfile(f) and "__pycache__" not in f:
            files.append(f)
    return files


setup(
    name=package_name,
    version="0.1.2",
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
    tests_require=["pytest"],

    entry_points={
        "console_scripts": [
            "fastdepth_node = robopy_controller.nodes.fastdepth_node:main",
            "lite_mono_node = robopy_controller.nodes.lite_mono_node:main",
            "lite_depth_node = robopy_controller.nodes.lite_depth_node:main",
            "lite_mono_depth_node = robopy_controller.nodes.lite_mono_depth_node:main",
            "sync_publisher_node = robopy_controller.nodes.sync_publisher_node:main",
            "depth_to_pointcloud_node = robopy_controller.nodes.depth_to_pointcloud_node:main",
            "madgwick_node = robopy_controller.nodes.madgwick_node:main",
            "smart_buildhat_driver = robopy_controller.nodes.smart_buildhat_driver:main",
            "homeassistant_node = robopy_controller.nodes.homeassistant_node:main",
            "robot_ai_node = robopy_controller.nodes.robot_ai_node:main",
            "llm_service_node = robopy_controller.robot_ai.services.llm_service:main",
            "servo_coda_node = robopy_controller.nodes.servo_coda_node:main",
            "foxglove_nav2_bridge = robopy_controller.nodes.foxglove_nav2_bridge:main",
            "wake_word_node = robopy_controller.nodes.wake_word_node:main",
            "respeaker_interface_node = robopy_controller.nodes.respeaker_interface_node:main",
            "respeaker_vui_node = robopy_controller.nodes.respeaker_vui_node:main",
            "ultrasonic_sensor = robopy_controller.nodes.ultrasonic_sensor:main",
        ],
    },
)

