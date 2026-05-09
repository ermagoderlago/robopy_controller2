import sys
import os

filepath = os.path.expanduser("~/severus_ws/src/severus/urdf/robopy.urdf")
try:
    with open(filepath, "r") as f:
        content = f.read()
    with open(filepath, "w") as f:
        f.write(content.replace("robopy_controller", "severus"))
    print("Fixed URDF")
except Exception as e:
    print(f"Error: {e}")
