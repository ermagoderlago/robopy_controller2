cmake_minimum_required(VERSION 3.8)
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

ament_python_install_package(${PROJECT_NAME})

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
