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
find_package(tf2_eigen REQUIRED)
find_package(cv_bridge REQUIRED)
find_package(image_transport REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(OpenCV REQUIRED)
find_package(depthai CONFIG REQUIRED)
find_package(message_filters REQUIRED)
find_package(rtabmap_msgs REQUIRED)
find_package(visualization_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/AudioData.msg"
  "msg/KeypointsCompressed.msg"
  "msg/DescriptorsCompressed.msg"
  "msg/OAKSyncFrame.msg"
  "msg/SemanticObject.msg"
  "msg/SemanticObjectArray.msg"
  "msg/EngagementStatus.msg"
  "srv/AskVisualQuestion.srv"
  "srv/MemoryRecall.srv"
  DEPENDENCIES std_msgs geometry_msgs sensor_msgs vision_msgs
)

# Legacy odometry targets disabled to conserve RAM and compilation time
# add_executable(oak_superpoint_odometry_cpp src/oak_superpoint_odometry_node.cpp)
# find_package(Eigen3 REQUIRED)
# add_executable(oak_visual_odometry_cpp src/oak_visual_odometry_node.cpp)

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

# NEW: Marcus Semantic Mapper Node (fuses Hailo NPU detections and Depth)
add_executable(marcus_semantic_mapper_cpp
  src/marcus_semantic_mapper_node.cpp
)
ament_target_dependencies(marcus_semantic_mapper_cpp
  rclcpp
  std_msgs
  geometry_msgs
  sensor_msgs
  diagnostic_msgs
  visualization_msgs
  rtabmap_msgs
  message_filters
  tf2
  tf2_ros
  tf2_geometry_msgs
  cv_bridge
  tf2_eigen
)
target_include_directories(marcus_semantic_mapper_cpp PRIVATE ${EIGEN3_INCLUDE_DIR})
target_link_libraries(marcus_semantic_mapper_cpp
  ${OpenCV_LIBRARIES}
  Eigen3::Eigen
)

install(TARGETS marcus_semantic_mapper_cpp
  DESTINATION lib/${PROJECT_NAME}
)


# Link visual fusion node to generated interfaces
rosidl_get_typesupport_target(typesupport_target "${PROJECT_NAME}" "rosidl_typesupport_cpp")
target_link_libraries(marcus_semantic_mapper_cpp "${typesupport_target}")
add_dependencies(marcus_semantic_mapper_cpp ${PROJECT_NAME}__rosidl_generator_cpp)

# Workaround: copy visibility control header during build to prevent Ninja deletion bug
add_custom_target(copy_visibility_control_header ALL
  COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/rosidl_generator_cpp/${PROJECT_NAME}/msg"
  COMMAND ${CMAKE_COMMAND} -E copy_if_different
    "${CMAKE_CURRENT_SOURCE_DIR}/msg/rosidl_generator_cpp__visibility_control.hpp"
    "${CMAKE_CURRENT_BINARY_DIR}/rosidl_generator_cpp/${PROJECT_NAME}/msg/rosidl_generator_cpp__visibility_control.hpp"
  COMMENT "Copying C++ visibility control header to prevent Ninja deletion"
)
add_dependencies(copy_visibility_control_header ${PROJECT_NAME}__cpp)
add_dependencies(marcus_semantic_mapper_cpp copy_visibility_control_header)

# =============================================================================
# C++ Native Hailo Bridge Node
# =============================================================================
find_package(rcl_interfaces REQUIRED)
find_package(rosidl_runtime_cpp REQUIRED)

add_executable(hailo_bridge_node_cpp src/hailo_bridge_node.cpp)
ament_target_dependencies(hailo_bridge_node_cpp
  rclcpp
  rcl_interfaces
  rosidl_runtime_cpp
  std_msgs
  sensor_msgs
  geometry_msgs
  vision_msgs
  cv_bridge
  image_transport
)
target_include_directories(hailo_bridge_node_cpp PRIVATE /usr/include/hailo)
target_link_libraries(hailo_bridge_node_cpp
  ${OpenCV_LIBRARIES}
  "${typesupport_target}"
  hailort
)
add_dependencies(hailo_bridge_node_cpp ${PROJECT_NAME}__rosidl_generator_cpp copy_visibility_control_header)

install(TARGETS hailo_bridge_node_cpp
  DESTINATION lib/${PROJECT_NAME}
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
  scripts/image_compressor_node
  scripts/oak_superpoint_odometry_node
  scripts/teleop_node
  scripts/teleop_bridge_node
  scripts/robot_ai_node
  scripts/servo_coda_node
  scripts/homeassistant_node
  scripts/waveshare_motor_driver
  scripts/foxglove_nav2_bridge
  scripts/wake_word_node
  scripts/ultrasonic_sensor
  scripts/respeaker_interface_node
  scripts/respeaker_vui_node
  scripts/hailo_bridge_node
  scripts/semantic_costmap_injector
  scripts/engagement_monitor
  scripts/cloud_watchdog_node
  scripts/speaker_id_node
  scripts/localization_fuser_node
  scripts/robot_health_supervisor
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
