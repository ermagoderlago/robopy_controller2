import sys
try:
    import hailo_platform
    print("hailo_platform OK")
except Exception as e:
    print("hailo_platform error:", e)

try:
    import onnx
    m = onnx.load("/mnt/ssd/robopy_controller_host/yolov8s_pose.onnx")
    for o in m.graph.output:
        shape = [d.dim_value for d in o.type.tensor_type.shape.dim]
        print("ONNX Output:", o.name, shape)
except Exception as e:
    print("ONNX error:", e)
