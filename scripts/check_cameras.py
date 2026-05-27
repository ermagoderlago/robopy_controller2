import depthai as dai

with dai.Device() as device:
    print(f"Connected to {device.getMxId()}")
    print("Connected Cameras:")
    for cam in device.getConnectedCameras():
        print(f" - {cam.name}")
    print("Camera Features:")
    for feat in device.getConnectedCameraFeatures():
        print(f" - Socket: {feat.socket.name}, Name: {feat.name}, Type: {feat.type}, Max Size: {feat.width}x{feat.height}")
