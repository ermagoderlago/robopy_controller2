# robopy_controller setup.py proxy/mirror
from setuptools import setup, find_packages
import os
from glob import glob

package_name = "robopy_controller"


def safe_glob(pattern):
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
            "luminance_safety_gate = robopy_controller.nodes.luminance_safety_gate:main",
            "vpr_room_recognizer = robopy_controller.nodes.vpr_room_recognizer:main",
            "lidar_room_recognizer = robopy_controller.nodes.lidar_room_recognizer:main",
            "kidnapped_robot_recovery = robopy_controller.nodes.kidnapped_robot_recovery:main",
            # === Milestone 3: Autonomous Mapping & Frontier Exploration ===
            "frontier_explorer_node = robopy_controller.nodes.frontier_explorer_node:main",
            "map_export_optimizer = robopy_controller.nodes.map_export_optimizer:main",
            "mapping_state_machine = robopy_controller.robot_ai.core.mapping_state_machine:main",
            # === Milestone 4: Conversational VUI, Situational Awareness & Safety Gates ===
            "vui_dialogue_node = robopy_controller.robot_ai.vui.vui_dialogue_engine:main",
            "destructive_confirmation_gate = robopy_controller.robot_ai.core.destructive_confirmation_gate:main",
            # === Milestone 5: Hierarchical Hybrid Navigation & Target Seeking ===
            "hybrid_target_seeker = robopy_controller.nodes.hybrid_target_seeker:main",
        ],
    },
)
