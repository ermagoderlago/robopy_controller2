import sys
print("sys.path before:", sys.path)
import robopy_controller
print("robopy_controller location:", robopy_controller.__file__)
try:
    from robopy_controller.msg import AudioData
    print("SUCCESS: Imported AudioData from robopy_controller.msg!", AudioData)
except Exception as e:
    print("FAILED:", e)
