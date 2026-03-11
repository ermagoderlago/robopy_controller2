cmake_minimum_required(VERSION 3.8)
project(robopy_controller)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(vision_msgs REQUIRED)
find_package(vision_msgs REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(tf2 REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(tf2_geometry_msgs REQUIRED)
find_package(cv_bridge REQUIRED)
find_package(image_transport REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(OpenCV REQUIRED)
find_package(depthai CONFIG REQUIRED)

add_executable(oak_superpoint_odometry_cpp src/oak_superpoint_odometry_node.cpp)
ament_target_dependencies(oak_superpoint_odometry_cpp
  rclcpp
  std_msgs
  sensor_msgs
  geometry_msgs
  nav_msgs
  vision_msgs
  tf2
  tf2_ros
  tf2_geometry_msgs
  cv_bridge
  image_transport
)
# Link depthai and OpenCV manually if not handled by ament
target_link_libraries(oak_superpoint_odometry_cpp
  depthai::core
  depthai::opencv
  ${OpenCV_LIBRARIES}
)

install(TARGETS oak_superpoint_odometry_cpp
  DESTINATION lib/${PROJECT_NAME}
)

# NEW: Eigen-based Visual Odometry Node
find_package(Eigen3 REQUIRED)

add_executable(oak_visual_odometry_cpp src/oak_visual_odometry_node.cpp)
ament_target_dependencies(oak_visual_odometry_cpp
  rclcpp
  std_msgs
  sensor_msgs
  geometry_msgs
  nav_msgs
  diagnostic_msgs
  tf2
  tf2_ros
  tf2_geometry_msgs
  cv_bridge
)
target_include_directories(oak_visual_odometry_cpp PRIVATE ${EIGEN3_INCLUDE_DIR})
target_link_libraries(oak_visual_odometry_cpp
  depthai::core
  depthai::opencv
  ${OpenCV_LIBRARIES}
  Eigen3::Eigen
)

install(TARGETS oak_visual_odometry_cpp
  DESTINATION lib/${PROJECT_NAME}
)

# FAST + Optical Flow Visual Odometry Node (NO SuperPoint)
add_executable(fast_flow_vo_cpp src/fast_flow_vo_node.cpp)
ament_target_dependencies(fast_flow_vo_cpp
  rclcpp
  std_msgs
  sensor_msgs
  geometry_msgs
  nav_msgs
  diagnostic_msgs
  tf2
  tf2_ros
  tf2_geometry_msgs
  cv_bridge
)
target_include_directories(fast_flow_vo_cpp PRIVATE ${EIGEN3_INCLUDE_DIR})
target_link_libraries(fast_flow_vo_cpp
  depthai::core
  depthai::opencv
  ${OpenCV_LIBRARIES}
  Eigen3::Eigen
)

install(TARGETS fast_flow_vo_cpp
  DESTINATION lib/${PROJECT_NAME}
)


rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/AudioData.msg"
  "msg/KeypointsCompressed.msg"
  "msg/DescriptorsCompressed.msg"
  "msg/OAKSyncFrame.msg"
  "srv/AskVisualQuestion.srv"
  DEPENDENCIES std_msgs geometry_msgs sensor_msgs vision_msgs
)

# Install Python package (setup.py handles data_files and scripts)
# ament_python_install_package(${PROJECT_NAME})

find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(PYTHON_INSTALL_DIR "lib/python${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/site-packages")

# Manually install the python package (merges with rosidl generated one)
install(DIRECTORY robopy_controller
  DESTINATION ${PYTHON_INSTALL_DIR}
)

# Install entry point script
install(PROGRAMS 
  scripts/oak_driver_node
  scripts/superpoint_node
  scripts/madgwick_node
  scripts/bluedot_node
  scripts/motor_control_node
  scripts/image_compressor_node
  scripts/oak_superpoint_odometry_node
  scripts/teleop_node
  scripts/teleop_bridge_node
  scripts/robot_ai_node
  scripts/servo_coda_node
  scripts/homeassistant_node
  scripts/smart_buildhat_driver
  scripts/foxglove_nav2_bridge
  scripts/wake_word_node
  scripts/ultrasonic_sensor
  DESTINATION lib/${PROJECT_NAME}
)


# Install Data Files (launch, urdf) - IN ROOT
install(DIRECTORY launch urdf
  DESTINATION share/${PROJECT_NAME}
)

# Install Data Files (config, models) - IN SUBDIR
install(DIRECTORY robopy_controller/config robopy_controller/models
  DESTINATION share/${PROJECT_NAME}
)



# Register environment hook to add PYTHONPATH
ament_environment_hooks(
  "${CMAKE_CURRENT_SOURCE_DIR}/hooks/${PROJECT_NAME}.dsv.in"
)

# Register environment hook to add LD_LIBRARY_PATH
ament_environment_hooks(
  "${CMAKE_CURRENT_SOURCE_DIR}/hooks/${PROJECT_NAME}_lib.dsv.in"
)



ament_package()
