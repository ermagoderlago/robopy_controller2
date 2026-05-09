import os
import shutil
from pathlib import Path

BASE_DIR = Path(r"c:\Users\lsuffia\OneDrive - BRUGOLA OEB INDUSTRIALE SPA\Documents\robopy\antigravity")
STAGING_DIR = BASE_DIR / "tmp" / "severus_staging_v2"
SEVERUS_DIR = STAGING_DIR / "severus"

# Ensure clean staging
if STAGING_DIR.exists():
    shutil.rmtree(STAGING_DIR)

SEVERUS_DIR.mkdir(parents=True)

# Def functions
def copy_if_exists(src_rel, tgt_rel):
    src = BASE_DIR / src_rel
    tgt = SEVERUS_DIR / tgt_rel
    if src.exists():
        if src.is_dir():
            shutil.copytree(src, tgt, dirs_exist_ok=True)
        else:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tgt)

# 1. Copia file
# Python sources
nodes = [
    "robot_ai_node.py",
    "respeaker_vui_node.py",
    "respeaker_interface_node.py",
    "homeassistant_node.py",
    "servo_coda_node.py",
    "ultrasonic_sensor.py",
]
for node in nodes:
    copy_if_exists(f"robopy_controller/nodes/{node}", f"severus/nodes/{node}")

copy_if_exists("robopy_controller/robot_ai", "severus/robot_ai")
copy_if_exists("robopy_controller/utils", "severus/utils")

# Messaggistica
copy_if_exists("msg", "msg")
copy_if_exists("srv", "srv")

# Config, Launch, Asset
copy_if_exists("launch/robot_ia_launch.py", "launch/robot_ia_launch.py")
copy_if_exists("urdf/robopy.urdf", "urdf/robopy.urdf")
copy_if_exists("robopy_controller/config", "config")

# Environment
copy_if_exists(".env", "severus/.env")
copy_if_exists("requirements.txt", "requirements.txt")
copy_if_exists("requirements_ai.txt", "requirements_ai.txt")

# Dummy __init__ per i pacchetti python
(SEVERUS_DIR / "severus" / "__init__.py").touch(exist_ok=True)
(SEVERUS_DIR / "severus" / "nodes" / "__init__.py").touch(exist_ok=True)

# Risorsa ament_index
(SEVERUS_DIR / "resource").mkdir(exist_ok=True)
(SEVERUS_DIR / "resource" / "severus").touch()

# Scripts Python (entry point scripts for ament_cmake_python, handled typically by setup.py but we use Cmake)
# Nelle build standard ROS 2 (ament_cmake + python), gli script si possono piazzare in scripts/ senza estensione se necessario, o lasciar fare alla macro.

# 2. Rename strings
def replace_string_in_file(filepath, old, new):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        if old in content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content.replace(old, new))
    except Exception as e:
        print(f"Skipping {filepath} due to {e}")

for root, dirs, files in os.walk(SEVERUS_DIR):
    for f in files:
        if not f.endswith(('.py', '.yaml', '.txt', '.xml', '.srv', '.msg', '.env', '.md')):
            continue
        replace_string_in_file(os.path.join(root, f), "robopy_controller", "severus")

# 3. Create Package files
package_xml = """<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>severus</name>
  <version>0.1.0</version>
  <description>Robot AI structure for severus</description>
  <maintainer email="robopy@todo.todo">robopy</maintainer>
  <license>MIT</license>

  <buildtool_depend>ament_cmake</buildtool_depend>
  <buildtool_depend>ament_cmake_python</buildtool_depend>
  <buildtool_depend>rosidl_default_generators</buildtool_depend>

  <depend>geometry_msgs</depend>
  <depend>std_msgs</depend>
  <depend>rclpy</depend>
  <depend>sensor_msgs</depend>
  <depend>vision_msgs</depend>

  <exec_depend>rosidl_default_runtime</exec_depend>

  <export>
    <build_type>ament_cmake</build_type>
  </export>
  <member_of_group>rosidl_interface_packages</member_of_group>
</package>
"""
with open(SEVERUS_DIR / "package.xml", "w") as f:
    f.write(package_xml)

cmake_lists = """cmake_minimum_required(VERSION 3.8)
project(severus)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(vision_msgs REQUIRED)

file(GLOB msg_files "msg/*.msg")
file(GLOB srv_files "srv/*.srv")

rosidl_generate_interfaces(${PROJECT_NAME}
  ${msg_files}
  ${srv_files}
  DEPENDENCIES geometry_msgs std_msgs sensor_msgs vision_msgs
)

# Call python setup (if setup.py exists)
# In this approach with ament_cmake + ament_cmake_python, we just install the python code standardly.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(PYTHON_INSTALL_DIR "lib/python${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/site-packages")

# Install pure python code
install(DIRECTORY severus/
  DESTINATION ${PYTHON_INSTALL_DIR}/severus
  REGEX "/__pycache__/" EXCLUDE
)

# Install launcher directly as python scripts inside lib
# We create a script wrapper to run python code directly from ros2 run
# For simplicity, we just dump them into local scripts folder for now, or use python setup.

# Install Data Files
install(DIRECTORY launch urdf config
  DESTINATION share/${PROJECT_NAME}
)
install(FILES requirements.txt requirements_ai.txt
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
"""
with open(SEVERUS_DIR / "CMakeLists.txt", "w") as f:
    f.write(cmake_lists)

# Setup.py to register console scripts so `ros2 run severus robot_ai_node` works automatically via colcon.
# The `ament_python_install_package` is a mechanism but usually people just specify `setup.py`.
# Since it's an ament_cmake package, the easiest way to give node launchers in python is providing a `setup.py` alongside `CMakeLists.txt` and passing it over, or creating dummy scripts. I will create a `setup.py` configuration and invoke `ament_python_install_package`
cmake_lists2 = cmake_lists.replace("# Call python setup (if setup.py exists)", "ament_python_install_package(${PROJECT_NAME})\n")
with open(SEVERUS_DIR / "CMakeLists.txt", "w") as f:
    f.write(cmake_lists2)

# Scriviamo setup.py per generare gli eseguibili python
setup_py = """from setuptools import setup, find_packages

package_name = 'severus'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(),
    zip_safe=True,
    maintainer='robopy',
    maintainer_email='robopy@todo.todo',
    description='Severus AI Package',
    license='MIT',
    entry_points={
        'console_scripts': [
            'robot_ai_node = severus.nodes.robot_ai_node:main',
            'respeaker_vui_node = severus.nodes.respeaker_vui_node:main',
            'respeaker_interface_node = severus.nodes.respeaker_interface_node:main',
            'homeassistant_node = severus.nodes.homeassistant_node:main',
            'servo_coda_node = severus.nodes.servo_coda_node:main',
            'ultrasonic_sensor = severus.nodes.ultrasonic_sensor:main',
        ],
    },
)
"""
with open(SEVERUS_DIR / "setup.py", "w") as f:
    f.write(setup_py)

setup_cfg = """[develop]
script_dir=$base/lib/severus
[install]
install_scripts=$base/lib/severus
"""
with open(SEVERUS_DIR / "setup.cfg", "w") as f:
    f.write(setup_cfg)

print("Staging and rename logic executed!")
