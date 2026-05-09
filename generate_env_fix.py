import os

install_dir = "/home/robopy/ros2_jazzy/install"
subdirs = [os.path.join(install_dir, d) for d in os.listdir(install_dir) if os.path.isdir(os.path.join(install_dir, d))]

cmake_paths = []
ament_paths = []

for d in subdirs:
    cmake_paths.append(d)
    ament_paths.append(d)

print("export CMAKE_PREFIX_PATH=" + ":".join(cmake_paths) + ":$CMAKE_PREFIX_PATH")
print("export AMENT_PREFIX_PATH=" + ":".join(ament_paths) + ":$AMENT_PREFIX_PATH")
# Also need to add library paths to LD_LIBRARY_PATH potentially?
# For build time, CMAKE paths should be enough to find config.
# Runtime might need LD_LIBRARY_PATH.
# Let's add lib dirs to LD_LIBRARY_PATH just in case.
lib_paths = [os.path.join(d, "lib") for d in subdirs if os.path.exists(os.path.join(d, "lib"))]
print("export LD_LIBRARY_PATH=" + ":".join(lib_paths) + ":$LD_LIBRARY_PATH")
