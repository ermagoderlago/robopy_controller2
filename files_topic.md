# Files and Topics of robopy_controller

This file lists the ROS 2 nodes and their topics (auto-generated).

## mock_camera.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/rgb/image/compressed`

## robopy_controller\bluedot_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `bluedot_input`
  - `servo_angle`

## robopy_controller\camera_publisher.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/camera/camera_info`
  - `/camera/image_raw`
  - `/camera_info`
  - `/raw_image`

## robopy_controller\camera_publisher_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/camera/camera_info`
  - `/camera/image_raw`

## robopy_controller\depth_to_pointcloud_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/rgb/pointcloud`

## robopy_controller\gray_camera_publisher_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/rgb/camera_info`
  - `/rgb/image`

## robopy_controller\lite_depth_node.py
- **Subscribes to:**
  - `/camera/image_raw`
- **Transmits (Publishes) to:**
  - `/depth/image_raw`

## robopy_controller\midas_depth_node.py
- **Subscribes to:**
  - `/rgb/image`
- **Transmits (Publishes) to:**
  - `/rgb/depth`

## robopy_controller\midas_depth_node_NCNN.py
- **Subscribes to:**
  - `/rgb/image`
- **Transmits (Publishes) to:**
  - `/depth/image`

## robopy_controller\midas_lite_ONNX_node.py
- **Subscribes to:**
  - `/rgb/image`
- **Transmits (Publishes) to:**
  - `/rgb/depth`

## robopy_controller\motion_detector_node.py
- **Subscribes to:**
  - `/rgb/image`
- **Transmits (Publishes) to:**
  - `/motion_detected`

## robopy_controller\nodes\IMU_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `imu/data_raw`
  - `imu/mag`

## robopy_controller\nodes\camera_info_publisher.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `camera_info`

## robopy_controller\nodes\cpu_superpoint_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/cpu/superpoint/keypoints`
  - `/cpu/superpoint/overlay`
  - `/superpoint/user_data`

## robopy_controller\nodes\dynamic_camera_tf_node.py
- **Subscribes to:**
  - `/imu/calibrated`
  - `/imu/data`
- **Transmits (Publishes) to:** None

## robopy_controller\nodes\fastdepth_node.py
- **Subscribes to:**
  - `/rgb/image`
- **Transmits (Publishes) to:**
  - `/rgb/depth`

## robopy_controller\nodes\foxglove_nav2_bridge.py
- **Subscribes to:**
  - `/goal_pose`
- **Transmits (Publishes) to:** None

## robopy_controller\nodes\homeassistant_node.py
- **Subscribes to:**
  - `/robot/state`
  - `/sensors/temperature`
  - `/system/performance`
- **Transmits (Publishes) to:**
  - `/cmd_vel`
  - `ha/buttons`
  - `ha/lights`
  - `ha/switches`

## robopy_controller\nodes\image_compressor_node.py
- **Subscribes to:**
  - `/camera/image_raw`
  - `/depth/image_raw`
  - `/superpoint/debug_image`
- **Transmits (Publishes) to:**
  - `/camera/image_raw/compressed`
  - `/depth/image_raw/compressedDepth`
  - `/superpoint/debug_image/compressed`

## robopy_controller\nodes\madgwick_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/imu/linear`

## robopy_controller\nodes\map_manager_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:** None
- **Service Servers:**
  - `clear_map`
  - `load_map`
  - `save_map`

## robopy_controller\nodes\oak_d_lite_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/oak_d/camera_info`
  - `/oak_d/depth`
  - `/oak_d/rgb`

## robopy_controller\nodes\oak_driver_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/oak/diagnostics`
  - `/oak/sync_frame`

## robopy_controller\nodes\oak_superpoint_odometry_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/camera/camera_info`
  - `/camera/depth/image_raw`
  - `/camera/rgb/image_raw`
  - `/vo/debug/compressed`
  - `/vo/odom`
  - `/vo/quality`
  - `/yolo/detections`

## robopy_controller\nodes\oakd_camera_publisher_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/oak/detections`
  - `/oak/imu/data`
  - `/oak/rgb/camera_info`
  - `/oak/rgb/image_annotated`
  - `/oak/rgb/image_raw`
  - `/oak/stereo/image_raw`

## robopy_controller\nodes\oakd_camera_publisher_node_super.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/oak/detections`
  - `/oak/imu/data`
  - `/oak/rgb/camera_info`
  - `/oak/rgb/image_annotated`
  - `/oak/rgb/image_raw`
  - `/oak/stereo/camera_info`
  - `/oak/stereo/image_raw`
  - `/oak/superpoint/gray`
  - `/oak/superpoint/keypoints`
  - `/oak/superpoint/overlay`

## robopy_controller\nodes\oakd_camera_publisher_node_test.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/oak/detections`
  - `/oak/imu/data`
  - `/oak/rgb/camera_info`
  - `/oak/rgb/image_annotated`
  - `/oak/rgb/image_raw`
  - `/oak/stereo/image_raw`

## robopy_controller\nodes\oakd_camera_publisher_node_v2.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/oakd/depth/camera_info`
  - `/oakd/depth/image_raw`
  - `/oakd/rgb/camera_info`
  - `/oakd/rgb/image_raw`

## robopy_controller\nodes\object_3d_mapper.py
- **Subscribes to:**
  - `/oak/rgb/camera_info`
- **Transmits (Publishes) to:**
  - `/object_3d_markers`
  - `/rtabmap/info`

## robopy_controller\nodes\odometria_ibrida_node.py
- **Subscribes to:**
  - `/imu/data`
  - `/odom`
- **Transmits (Publishes) to:**
  - `/odometry/hybrid`

## robopy_controller\nodes\performance_monitor.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/system/performance`

## robopy_controller\nodes\respeaker_interface_node.py
- **Subscribes to:**
  - `/ai/tts/speaking`
  - `/respeaker/audio_control`
  - `/respeaker/led_command`
  - `/respeaker/speaker_audio`
- **Transmits (Publishes) to:**
  - `/ai/input/mic_mute`
  - `/audio/audio`
  - `/respeaker/audio_level`
  - `/respeaker/audio_stats`
  - `/respeaker/heartbeat`
  - `/respeaker/status`
  - `/respeaker/streaming`

## robopy_controller\nodes\respeaker_vui_node.py
- **Subscribes to:**
  - `/ai/input/mic_mute`
  - `/ai/tts/speaking`
  - `/respeaker/speaker_audio`
  - `/ai/music_playing`
  - `/ai/conversation/mood`
  - `/ai/conversation/interrupt`
- **Transmits (Publishes) to:**
  - `/ai/input/audio_chunk`
  - `/ai/input/mic_mute`
  - `/respeaker/led_command`
  - `/wake_word`
  - `/ai/barge_in`
  - `/ai/ambient_noise`

## robopy_controller\nodes\rtabmap_node.py
- **Subscribes to:**
  - `/camera/camera_info`
  - `/camera/image_raw`
  - `/odometry/filtered`
- **Transmits (Publishes) to:**
  - `/rtabmap/map`

## robopy_controller\nodes\servo_coda_node.py
- **Subscribes to:**
  - `/movement_detected`
  - `/oakdetections`
  - `/vo/tracking_status`
- **Transmits (Publishes) to:** None

## robopy_controller\nodes\servo_node.py
- **Subscribes to:**
  - `servo_angle`
- **Transmits (Publishes) to:** None

## robopy_controller\nodes\stereo_camera_info_converter.py
- **Subscribes to:**
  - `/oak/stereo/camera_info`
  - `/oak/stereo/image_raw`
- **Transmits (Publishes) to:**
  - `/scan`

## robopy_controller\nodes\superpoint_node copy.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/camera/camera_info`
  - `/camera/image_raw`
  - `/depth/image_normalized`
  - `/depth/image_raw`
  - `/flann/matches_viz`
  - `/imu/raw`
  - `/rtabmap/features`
  - `/superpoint/features`

## robopy_controller\nodes\superpoint_node.py
- **Subscribes to:**
  - `/oak/sync_frame`
- **Transmits (Publishes) to:**
  - `/camera/camera_info`
  - `/camera/image_raw`
  - `/depth/image_normalized`
  - `/depth/image_raw`
  - `/depth/visualization`
  - `/flann/matches_viz`
  - `/imu/raw`
  - `/odom`
  - `/rtabmap/features`
  - `/superpoint/debug_image`
  - `/superpoint/features`
  - `/superpoint/keypoints_3d`
  - `/superpoint/markers`
  - `/superpoint/matches_3d`
  - `/superpoint/odometry`
  - `/yolo/detections`
  - `~/debug_image`
  - `~/debug_image/compressed`

## robopy_controller\nodes\system_monitor_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/diagnostics`

## robopy_controller\nodes\teleop_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `bluedot_input`

## robopy_controller\nodes\test_2d_static.py
- **Subscribes to:**
  - `/camera/image_raw`
- **Transmits (Publishes) to:**
  - `/test/static_matches`

## robopy_controller\nodes\v4l2_camera_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `camera/image_raw`

## robopy_controller\nodes\vision_safety_node.py
- **Subscribes to:**
  - `/cmd_vel`
  - `/rtabmap/info`
- **Transmits (Publishes) to:**
  - `/cmd_vel_safe`
  - `/vision/status`

## robopy_controller\nodes\wake_word_node.py
- **Subscribes to:**
  - `/audio/audio`
- **Transmits (Publishes) to:**
  - `/ai/input/mic_mute`

## robopy_controller\nodes\waveshare_motor_driver.py
- **Subscribes to:**
  - `/cmd_vel`
- **Transmits (Publishes) to:**
  - `/odom`
  - `/imu/esp32`
  - `/battery_state`
  - `/diagnostics`

## robopy_controller\nodes\web_video_stream_node.py
- **Subscribes to:**
  - `image_raw`
- **Transmits (Publishes) to:** None

## robopy_controller\object_detection_node copy.py
- **Subscribes to:**
  - `/raw/image`
  - `/rgb/image`
- **Transmits (Publishes) to:**
  - `camera/detections`
  - `detected_objects`

## robopy_controller\object_detection_node.py
- **Subscribes to:**
  - `/motion_detected`
  - `/rgb/image`
- **Transmits (Publishes) to:**
  - `camera/detections`
  - `detected_objects`

## robopy_controller\odometry_node.py
- **Subscribes to:**
  - `motor_speed`
- **Transmits (Publishes) to:**
  - `/odom`

## robopy_controller\robot_ai\integrations\navigation.py
- **Subscribes to:**
  - `/scan`
- **Transmits (Publishes) to:** None

## robopy_controller\robot_ai\orchestration\conversation.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/ai/tts/speaking`
- **Service Clients:**
  - `ask_visual_question`

## robopy_controller\robot_ai\orchestration\orchestrator.py
- **Subscribes to:**
  - `/ai/conversation/audio_chunk`
  - `/ai/input/audio_chunk`
  - `/ai/input/mic_mute`
  - `/ai/input/text`
  - `/ai/input/voice_test`
  - `/diagnostics`
  - `/rgb/image/compressed`
  - `/robopy/conversation_rx`
- **Transmits (Publishes) to:**
  - `/ai/conversation/response`
  - `/ai/conversation/status`
  - `/ai/input/mic_mute`
  - `/ai/tts/speaking`
  - `/bluedot_input`
  - `/respeaker/audio_control`
  - `/respeaker/led_command`
  - `/respeaker/speaker_audio`
  - `ai/input/voice_test`
- **Service Servers:**
  - `memory_search`

## robopy_controller\robot_ai\services\llm_service.py
- **Subscribes to:**
  - `/ai/input/audio_chunk`
  - `/wake_word`
- **Transmits (Publishes) to:**
  - `/ai/conversation/audio_chunk`
  - `/ai/input/mic_mute`
  - `/ai/conversation/mood`
  - `/ai/conversation/interrupt`
  - `~/text_response`
- **Service Servers:**
  - `~/generate`
  - `~/generate_live`
  - `~/reconnect_live`

## robopy_controller\robot_ai\services\visual_memory_service.py
- **Subscribes to:**
  - `/camera/camera_info`
  - `/oak/stereo/image_depth`
  - `/oak/stereo/image_raw`
  - `/odom`
- **Transmits (Publishes) to:**
  - `/ai/visual_memory/markers`
  - `/rtabmap/user_data`
  - `/visual_objects_pc`
- **Service Servers:**
  - `ask_visual_question`

## robopy_controller\robot_ai\skills\builtin\calibration_skill.py
- **Subscribes to:**
  - `/vo/odom`
- **Transmits (Publishes) to:** None

## robopy_controller\sync_publisher_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/camera/depth/image_rect_raw`
  - `/camera/rgb/camera_info`
  - `/camera/rgb/image_rect_color`

## robopy_controller\ultrasonic_sensor.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `ultrasonic_range`

## robopy_controller\viz\debug.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/odometry/debug/current_pose`
  - `/odometry/debug/performance`
  - `/odometry/debug/status`

## robopy_controller\yuv_camera_publisher_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/rgb/camera_info`
  - `/rgb/image`

## test_dedup.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/robopy/conversation_rx`

## test_live_api_stability.py
- **Subscribes to:**
  - `/ai/conversation/response`
- **Transmits (Publishes) to:**
  - `/ai/input/text`

## test_obs.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/robopy/conversation_rx`

## test_robot_ai.py
- **Subscribes to:**
  - `/ai/conversation/response`
- **Transmits (Publishes) to:**
  - `ai/input/text`
  - `ai/input/voice_test`


## robopy_controller\robot_ai\skills\active\spotify_skill.py
- **Subscribes to:** None
- **Transmits (Publishes) to:** None

## robopy_controller\nodes\hailo_bridge_node.py
- **Subscribes to:**
  - `/rgb/image/compressed`
  - `/depth/image_raw`
  - `/ai/input/audio_chunk`
  - `/hailo/trigger/vlm`
  - `/hailo/trigger/face`
  - `/hailo/trigger/offline_mode`
- **Transmits (Publishes) to:**
  - `/hailo/vlm/semantic_objects`
  - `/hailo/face/detections`
  - `/hailo/face/embeddings`
  - `/hailo/face/emotions`
  - `/hailo/gaze/direction`
  - `/hailo/speaker/verified`
  - `/hailo/speaker/confidence`
  - `/hailo/health`
  - `/hailo/vlm/offline_intent`

## robopy_controller\nodes\semantic_costmap_injector.py
- **Subscribes to:**
  - `/hailo/vlm/semantic_objects`
  - `/tf`
- **Transmits (Publishes) to:**
  - `/semantic_obstacles`
  - `/semantic_costmap_injector/debug`

## robopy_controller\nodes\engagement_monitor.py
- **Subscribes to:**
  - `/hailo/face/detections`
  - `/hailo/gaze/direction`
  - `/superpoint/keypoints_3d`
- **Transmits (Publishes) to:**
  - `/engagement/status`
  - `/engagement/status_str`
  - `/engagement/cancel_goal`
  - `/engagement/interrupt`
  - `/engagement/proxemics_distance`

## robopy_controller\nodes\cloud_watchdog_node.py
- **Subscribes to:** None
- **Transmits (Publishes) to:**
  - `/cloud/status`
  - `/cloud/latency_ms`
  - `/hailo/trigger/offline_mode`

## robopy_controller\nodes\speaker_id_node.py
- **Subscribes to:**
  - `/ai/input/audio_chunk`
- **Transmits (Publishes) to:**
  - `/speaker/verified`
  - `/speaker/identity`
  - `/speaker/confidence`

