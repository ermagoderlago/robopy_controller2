import sys
if sys.prefix == '/home/robopy/ros2_venv':
    sys.real_prefix = sys.prefix
    sys.prefix = sys.exec_prefix = '/home/robopy/robopy/robopi_controller/robopy_controller_host/install/robopy_controller'
