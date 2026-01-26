std_msgs/Header header

# === DEPTH ===
# Può essere vuota (width=0) se valid_ratio < 0.20
sensor_msgs/Image depth
sensor_msgs/CameraInfo depth_info
geometry_msgs/Polygon depth_roi         # ROI coordinates (x1,y1,x2,y2) se cropped

# === VISUAL ODOMETRY ===
robopy_controller/KeypointsCompressed keypoints          # Spatial bucketed
robopy_controller/DescriptorsCompressed descriptors      # Delta-encoded int8

# === SEMANTIC ===
vision_msgs/Detection2DArray detections

# === IMU ===
sensor_msgs/Imu imu_data

# === HEALTH METRICS ===
float32 depth_valid_ratio      # 0.0-1.0
uint16 num_keypoints           # Post-bucketing
uint32 yolo_age_ms             # Staleness detection
float32 oak_temperature        # Celsius
string degradation_mode        # "normal" / "degraded" / "minimal"
float32 frame_loss_rate        # 0.0-1.0
