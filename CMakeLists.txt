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
find_package(diagnostic_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/KeypointsCompressed.msg"
  "msg/DescriptorsCompressed.msg"
  "msg/OAKSyncFrame.msg"
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
