#include "fast_flow_vo_node.hpp"

#include <cv_bridge/cv_bridge.hpp>
#include <cv_bridge/cv_bridge.hpp>
#include <sensor_msgs/image_encodings.hpp>
#include <sensor_msgs/msg/compressed_image.hpp>
#include <opencv2/calib3d.hpp>
#include <opencv2/imgcodecs.hpp>

#include <chrono>
#include <sstream>

using namespace std::chrono_literals;

// ===================== Constructor =====================

FastFlowVONode::FastFlowVONode(const rclcpp::NodeOptions& options)
    : Node("fast_flow_vo", options),
      last_diag_time_(this->now()),
      last_good_tracking_time_(this->now())
{
    // Load parameters
    config_.odom_frame = declare_parameter<std::string>("odom_frame", "odom");
    config_.base_frame = declare_parameter<std::string>("base_frame", "base_link");
    config_.camera_frame = declare_parameter<std::string>("camera_frame", "oak_left_camera_optical_frame");
    config_.publish_tf = declare_parameter<bool>("publish_tf", false);
    
    // Initialize TF broadcaster if enabled
    if (config_.publish_tf) {
        tf_broadcaster_ = std::make_unique<tf2_ros::TransformBroadcaster>(this);
        RCLCPP_INFO(get_logger(), "TF broadcasting ENABLED (odom -> base_link)");
    }
    
    // FAST Detection
    config_.fast_threshold = declare_parameter<int>("fast_threshold", 10);
    config_.max_features = declare_parameter<int>("max_features", 400);
    config_.grid_rows = declare_parameter<int>("grid_rows", 6);
    config_.grid_cols = declare_parameter<int>("grid_cols", 8);
    
    // KLT Tracking
    config_.klt_win_size = declare_parameter<int>("klt_win_size", 21);
    config_.klt_max_level = declare_parameter<int>("klt_max_level", 3);
    config_.klt_max_error = declare_parameter<double>("klt_max_error", 12.0);
    config_.fb_threshold = declare_parameter<double>("fb_threshold", 1.0);
    
    // Depth
    config_.min_depth = declare_parameter<double>("min_depth", 0.3);
    config_.max_depth = declare_parameter<double>("max_depth", 8.0);
    config_.depth_fps = declare_parameter<double>("depth_fps", 30.0);
    config_.camera_fps = declare_parameter<double>("camera_fps", 30.0);  // Stereo camera FPS
    config_.enable_depth_filter = declare_parameter<bool>("enable_depth_filter", true);
    
    // Floor Reflection Filter (with camera pitch compensation)
    config_.enable_floor_filter = declare_parameter<bool>("enable_floor_filter", true);
    config_.camera_height = declare_parameter<double>("camera_height", 0.08);
    config_.camera_pitch = declare_parameter<double>("camera_pitch", 0.0);  // radians, positive = tilted up
    config_.floor_z_threshold = declare_parameter<double>("floor_z_threshold", -0.02);  // Z below this in base_link = rejected
    
    // PnP
    config_.min_points = declare_parameter<int>("min_points", 20);
    config_.min_inliers = declare_parameter<int>("min_inliers", 15);
    config_.reproj_error = declare_parameter<double>("reproj_error", 3.0);
    
    // Motion Validation
    config_.max_translation_per_frame = declare_parameter<double>("max_translation_per_frame", 0.5);
    config_.max_rotation_per_frame = declare_parameter<double>("max_rotation_per_frame", 0.52);
    config_.min_translation = declare_parameter<double>("min_translation", 0.001);
    
    // EMA Filter
    config_.ema_alpha = declare_parameter<double>("ema_alpha", 0.3);
    
    // State
    config_.lost_threshold = declare_parameter<int>("lost_threshold", 5);
    config_.weak_inlier_threshold = declare_parameter<int>("weak_inlier_threshold", 25);
    config_.good_inlier_threshold = declare_parameter<int>("good_inlier_threshold", 40);
    
    // Resilience: Halt motion if inliers are critically low
    config_.critical_inlier_threshold = declare_parameter<int>("critical_inlier_threshold", 10);
    
    // Performance
    config_.skip_frames = declare_parameter<int>("skip_frames", 1);
    
    // Debug
    config_.publish_debug = declare_parameter<bool>("publish_debug", false);
    
    // YOLO
    config_.enable_yolo = declare_parameter<bool>("enable_yolo", true);
    config_.yolo_blob_path = declare_parameter<std::string>("yolo_blob_path", "");
    config_.yolo_conf_threshold = declare_parameter<float>("yolo_conf_threshold", 0.5f);
    
    // Motion Gate (prevents drift when stationary)
    config_.enable_motion_gate = declare_parameter<bool>("enable_motion_gate", true);
    config_.imu_gyro_threshold = declare_parameter<double>("imu_gyro_threshold", 0.02);
    config_.imu_accel_threshold = declare_parameter<double>("imu_accel_threshold", 0.15);
    config_.cmd_vel_timeout = declare_parameter<double>("cmd_vel_timeout", 0.5);
    
    // Initialize motion gate time
    last_cmd_vel_time_ = this->now();
    
    // Initialize FAST detector
    fast_detector_ = cv::FastFeatureDetector::create(config_.fast_threshold, true);
    
    // Initialize DepthAI
    if (!initializeDepthAI()) {
        RCLCPP_ERROR(get_logger(), "Failed to initialize DepthAI");
        throw std::runtime_error("DepthAI initialization failed");
    }
    
    // Compute camera transform
    computeCameraTransform();
    
    // ROS Publishers
    odom_pub_ = create_publisher<nav_msgs::msg::Odometry>("/fast_flow/velocity", 10);
    diag_pub_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>("/diagnostics", 10);
    tracking_pub_ = create_publisher<std_msgs::msg::Bool>("/vo/tracking_ok", 10);
    
    // Image publishers for RTAB-Map
    // Image publishers for RTAB-Map
    rgb_pub_ = create_publisher<sensor_msgs::msg::Image>("/rgb/image", 10);
    depth_pub_ = create_publisher<sensor_msgs::msg::Image>("/camera/depth/image_raw", 10);
    camera_info_pub_ = create_publisher<sensor_msgs::msg::CameraInfo>("/camera/camera_info", 10);
    
    // Compressed publishers for Foxglove
    rgb_compressed_pub_ = create_publisher<sensor_msgs::msg::CompressedImage>("/rgb/image/compressed", 10);
    depth_compressed_pub_ = create_publisher<sensor_msgs::msg::CompressedImage>("/camera/depth/image_raw/compressed", 10);
    
    // Debug Publishers
    debug_view_pub_ = create_publisher<sensor_msgs::msg::CompressedImage>("/vo/debug_view/compressed", 10);
    depth_preview_pub_ = create_publisher<sensor_msgs::msg::CompressedImage>("/camera/depth/preview/compressed", 10);
    
    // IMU
    imu_pub_ = create_publisher<sensor_msgs::msg::Imu>("/oak/imu/data", 10);
    
    // Guess publisher for RTAB-Map (initial motion estimate)
    guess_pub_ = create_publisher<geometry_msgs::msg::TransformStamped>("/vo/guess", 10);
    
    // Motion Gate: Subscribe to cmd_vel to detect motor commands
    if (config_.enable_motion_gate) {
        cmd_vel_sub_ = create_subscription<geometry_msgs::msg::Twist>(
            "/cmd_vel", 10,
            std::bind(&FastFlowVONode::cmdVelCallback, this, std::placeholders::_1));
        RCLCPP_INFO(get_logger(), "Motion Gate ENABLED: VO updates only when motors/IMU active");
    }
    
    // Start processing thread
    running_ = true;
    processing_thread_ = std::thread(&FastFlowVONode::processLoop, this);
    
    RCLCPP_INFO(get_logger(), "FAST + Optical Flow VO Node started");
    RCLCPP_INFO(get_logger(), "Config: FAST=%d, MaxFeatures=%d, MinInliers=%d",
                config_.fast_threshold, config_.max_features, config_.min_inliers);
}

FastFlowVONode::~FastFlowVONode() {
    running_ = false;
    if (processing_thread_.joinable()) {
        processing_thread_.join();
    }
}

// ===================== DepthAI Initialization =====================

bool FastFlowVONode::initializeDepthAI() {
    try {
        pipeline_ = std::make_shared<dai::Pipeline>();
        
        auto monoLeft = pipeline_->create<dai::node::MonoCamera>();
        auto monoRight = pipeline_->create<dai::node::MonoCamera>();
        auto stereo = pipeline_->create<dai::node::StereoDepth>();
        
        monoLeft->setBoardSocket(dai::CameraBoardSocket::CAM_B);
        monoLeft->setResolution(dai::MonoCameraProperties::SensorResolution::THE_400_P);
        monoLeft->setFps(config_.camera_fps);  // Configurable FPS
        
        monoRight->setBoardSocket(dai::CameraBoardSocket::CAM_C);
        monoRight->setResolution(dai::MonoCameraProperties::SensorResolution::THE_400_P);
        monoRight->setFps(config_.camera_fps);  // Configurable FPS
        
        stereo->setDefaultProfilePreset(dai::node::StereoDepth::PresetMode::DEFAULT);
        stereo->setLeftRightCheck(true);
        stereo->setSubpixel(true);
        stereo->setDepthAlign(dai::CameraBoardSocket::CAM_B);
        
        monoLeft->out.link(stereo->left);
        monoRight->out.link(stereo->right);
        
        auto xoutRect = pipeline_->create<dai::node::XLinkOut>();
        xoutRect->setStreamName("rect_left");
        xoutRect->input.setBlocking(false);
        xoutRect->input.setQueueSize(1);
        stereo->rectifiedLeft.link(xoutRect->input);
        
        auto xoutDepth = pipeline_->create<dai::node::XLinkOut>();
        xoutDepth->setStreamName("depth");
        xoutDepth->input.setBlocking(false);
        xoutDepth->input.setQueueSize(1);
        stereo->depth.link(xoutDepth->input);
        
        auto config = stereo->initialConfig.get();
        config.postProcessing.thresholdFilter.minRange = static_cast<int>(config_.min_depth * 1000);
        config.postProcessing.thresholdFilter.maxRange = static_cast<int>(config_.max_depth * 1000);
        stereo->initialConfig.set(config);
        
        // --- IMU (Safe Mode) ---
        auto imu = pipeline_->create<dai::node::IMU>();
        imu->enableIMUSensor(dai::IMUSensor::ACCELEROMETER_RAW, 50); // 50Hz stable
        imu->enableIMUSensor(dai::IMUSensor::GYROSCOPE_RAW, 50);
        imu->setBatchReportThreshold(5);
        imu->setMaxBatchReports(20);

        auto xoutImu = pipeline_->create<dai::node::XLinkOut>();
        xoutImu->setStreamName("imu");
        xoutImu->input.setBlocking(false);
        xoutImu->input.setQueueSize(1);
        imu->out.link(xoutImu->input);
        
        // --- YOLO PIPELINE ---
        if (config_.enable_yolo && !config_.yolo_blob_path.empty()) {
            RCLCPP_INFO(get_logger(), "Enabling YOLO: %s", config_.yolo_blob_path.c_str());
            
            // Camera RGB - Must be 1080P for IMX378/214
            auto camRgb = pipeline_->create<dai::node::ColorCamera>();
            camRgb->setBoardSocket(dai::CameraBoardSocket::CAM_A);
            camRgb->setResolution(dai::ColorCameraProperties::SensorResolution::THE_1080_P); 
            camRgb->setFps(config_.camera_fps);  // Match Stereo FPS
            camRgb->setInterleaved(false);
            camRgb->setColorOrder(dai::ColorCameraProperties::ColorOrder::BGR);
            camRgb->setPreviewKeepAspectRatio(false);
            
            // Resize for YOLO (640x352 for YOLOv6 Nano)
            // Resize for YOLO (640x352 for YOLOv6 Nano)
            auto manip = pipeline_->create<dai::node::ImageManip>();
            // Using 640x352 for YOLOv6 (Standard Detection Model)
            manip->initialConfig.setResize(640, 352); 
            manip->initialConfig.setFrameType(dai::RawImgFrame::Type::BGR888p);
            manip->setKeepAspectRatio(true);  
            camRgb->preview.link(manip->inputImage);
            
            auto yoloNn = pipeline_->create<dai::node::YoloDetectionNetwork>();
            yoloNn->setBlobPath(config_.yolo_blob_path);
            yoloNn->setConfidenceThreshold(config_.yolo_conf_threshold);
            yoloNn->setNumClasses(80);
            yoloNn->setCoordinateSize(4);
            // YOLOv6 Nano Masks (Fix for runtime errors)
            yoloNn->setAnchorMasks({
                {"side80", {0,1,2}},
                {"side40", {0,1,2}},
                {"side20", {0,1,2}}
            });
            yoloNn->setIouThreshold(0.5f);
            manip->out.link(yoloNn->input);
            
            auto xoutYolo = pipeline_->create<dai::node::XLinkOut>();
            xoutYolo->setStreamName("yolo");
            xoutYolo->input.setBlocking(false);
            xoutYolo->input.setQueueSize(1);
            yoloNn->out.link(xoutYolo->input);
            
            // DISABLED TO SAVE BANDWIDTH (X_LINK_ERROR FIX)
            /*
            auto xoutRgb = pipeline_->create<dai::node::XLinkOut>();
            xoutRgb->setStreamName("color");
            xoutRgb->input.setBlocking(false);
            xoutRgb->input.setQueueSize(1);
            manip->out.link(xoutRgb->input); // Output the resized frame for preview
            */
        }
        
        device_ = std::make_unique<dai::Device>(*pipeline_);
        
        auto calib = device_->readCalibration();
        auto intrinsics = calib.getCameraIntrinsics(dai::CameraBoardSocket::CAM_B, 640, 400);
        
        fx_ = intrinsics[0][0];
        fy_ = intrinsics[1][1];
        cx_ = intrinsics[0][2];
        cy_ = intrinsics[1][2];
        
        camera_matrix_ = (cv::Mat_<double>(3, 3) << 
            fx_, 0, cx_,
            0, fy_, cy_,
            0, 0, 1);
        
        RCLCPP_INFO(get_logger(), "Camera: fx=%.1f, fy=%.1f, cx=%.1f, cy=%.1f",
                    fx_, fy_, cx_, cy_);
        
        return true;
        
    } catch (const std::exception& e) {
        RCLCPP_ERROR(get_logger(), "DepthAI error: %s", e.what());
        return false;
    }
}

void FastFlowVONode::computeCameraTransform() {
    // OAK-D Lite: camera is mounted facing forward
    // Camera optical frame: Z forward, X right, Y down
    // Base frame: X forward, Y left, Z up
    
    // Rotation: camera_optical -> base_link
    Eigen::Matrix3d R;
    R << 0, 0, 1,    // X_base = Z_cam (forward)
        -1, 0, 0,    // Y_base = -X_cam (left)
         0,-1, 0;    // Z_base = -Y_cam (up)
    
    T_base_camera_.linear() = R;
    T_base_camera_.translation() = Eigen::Vector3d(0.05, 0, 0.08);  // 5cm forward, 8cm up
    
    transform_initialized_ = true;
}

// ===================== Main Processing Loop =====================

void FastFlowVONode::processLoop() {
    auto qRect = device_->getOutputQueue("rect_left", 4, false);
    auto qDepth = device_->getOutputQueue("depth", 4, false);
    auto qImu = device_->getOutputQueue("imu", 50, false); // IMU queue (Restored)
    
    // Optional YOLO queues
    std::shared_ptr<dai::DataOutputQueue> qYolo;
    if (config_.enable_yolo && !config_.yolo_blob_path.empty()) {
        qYolo = device_->getOutputQueue("yolo", 4, false);
        // qColor disabled to save bandwidth
    }
    
    int frame_counter = 0;
    
    while (running_ && rclcpp::ok()) {
        try {
            auto rectFrame = qRect->get<dai::ImgFrame>();
            auto depthFrame = qDepth->get<dai::ImgFrame>();
            
            if (!rectFrame || !depthFrame) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }
            
            // YOLO Processing (Using Mono Frame for Visualization to save bandwidth)
            if (qYolo) {
                auto yoloData = qYolo->tryGet<dai::ImgDetections>();
                
                if (yoloData) {
                    cv::Mat gray = rectFrame->getCvFrame();
                    cv::Mat display_bgr;
                    cv::cvtColor(gray, display_bgr, cv::COLOR_GRAY2BGR);
                    processYolo(yoloData, display_bgr);
                }
            }
            
            // IMU Processing (Process all available packets in the queue)
            while (auto imuData = qImu->tryGet<dai::IMUData>()) {
                auto packets = imuData->packets;
                for (const auto& packet : packets) {
                    sensor_msgs::msg::Imu imu_msg;
                    imu_msg.header.stamp = this->now(); 
                    imu_msg.header.frame_id = "imu_link";
                    
                    double ax_ros = -packet.acceleroMeter.z;
                    double ay_ros = -packet.acceleroMeter.x;
                    double az_ros = packet.acceleroMeter.y;
                    
                    imu_msg.linear_acceleration.x = ax_ros;
                    imu_msg.linear_acceleration.y = ay_ros;
                    imu_msg.linear_acceleration.z = az_ros;
                    
                    double gx_ros = packet.gyroscope.z;
                    double gy_ros = -packet.gyroscope.x;
                    double gz_ros = packet.gyroscope.y;
                    
                    const double GYRO_DEADBAND = 0.01;
                    if (std::abs(gx_ros) < GYRO_DEADBAND) gx_ros = 0.0;
                    if (std::abs(gy_ros) < GYRO_DEADBAND) gy_ros = 0.0;
                    if (std::abs(gz_ros) < GYRO_DEADBAND) gz_ros = 0.0;
                    
                    imu_msg.angular_velocity.x = gx_ros;
                    imu_msg.angular_velocity.y = gy_ros;
                    imu_msg.angular_velocity.z = gz_ros;
                    
                    imu_msg.orientation_covariance[0] = -1;
                    
                    if (!pitch_calibrated_.load(std::memory_order_acquire)) {
                        accel_sum_x_ += ax_ros;
                        accel_sum_y_ += ay_ros;
                        accel_sum_z_ += az_ros;
                        calibration_sample_count_++;
                        
                        if (calibration_sample_count_ >= CALIBRATION_SAMPLES) {
                            double avg_x = accel_sum_x_ / CALIBRATION_SAMPLES;
                            double avg_z = accel_sum_z_ / CALIBRATION_SAMPLES;
                            double pitch = std::atan2(avg_x, avg_z);
                            if (std::abs(pitch) > 0.78) pitch = 0.0;
                            
                            calibrated_pitch_.store(pitch, std::memory_order_release);
                            pitch_calibrated_.store(true, std::memory_order_release);
                            
                            RCLCPP_INFO(get_logger(), 
                                "📐 Camera pitch calibrated: %.1f° (ROS Accel: x=%.2f, z=%.2f)",
                                pitch * 180.0 / M_PI, avg_x, avg_z);
                        }
                    }
                    
                    if (config_.enable_motion_gate) {
                        double gyro_norm = std::sqrt(gx_ros*gx_ros + gy_ros*gy_ros + gz_ros*gz_ros);
                        double accel_magnitude = std::sqrt(ax_ros*ax_ros + ay_ros*ay_ros + az_ros*az_ros);
                        double accel_deviation = std::abs(accel_magnitude - 9.81);
                        
                        last_imu_gyro_norm_.store(gyro_norm, std::memory_order_release);
                        last_imu_accel_deviation_.store(accel_deviation, std::memory_order_release);
                        
                        bool motion = (gyro_norm > config_.imu_gyro_threshold) ||
                                     (accel_deviation > config_.imu_accel_threshold);
                        imu_motion_detected_.store(motion, std::memory_order_release);
                    }
                    
                    imu_pub_->publish(imu_msg);
                }
            }
            
            // Process Frames
            frame_counter++;
            if (config_.skip_frames <= 1 || frame_counter % config_.skip_frames == 0) {
                cv::Mat gray = rectFrame->getCvFrame();
                cv::Mat depth = depthFrame->getCvFrame();
                auto stamp = this->now();
                
                publishImages(gray, depth, stamp);
                processFrame(gray, depth, stamp);
            }
            
            // Minimal sleep to avoid absolute core hogging
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            
        } catch (const std::runtime_error& e) {
            // Catch DepthAI and other runtime errors
            std::string err_msg = e.what();
            if (err_msg.find("XLink") != std::string::npos || 
                err_msg.find("Device") != std::string::npos) {
                RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 1000,
                    "DepthAI error: %s", e.what());
            } else {
                RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 1000,
                    "Runtime error: %s", e.what());
            }
            // Don't crash, try to recover on next iteration
        } catch (const cv::Exception& e) {
            RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 1000,
                "OpenCV error [%s:%d]: %s", e.file.c_str(), e.line, e.what());
        } catch (const std::exception& e) {
            RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 1000,
                                 "Processing error: %s", e.what());
        }
    }
}

void FastFlowVONode::processFrame(const cv::Mat& gray, const cv::Mat& depth, 
                                   const rclcpp::Time& stamp) {
    
    TrackingResult result;
    bool tracking_ok = false;
    
    if (has_prev_frame_) {
        // 1. Track previous points with KLT
        std::vector<cv::Point2f> curr_pts;
        std::vector<cv::Point2f> prev_pts = prev_frame_.points;
        
        if (!prev_pts.empty() && trackKLT(prev_frame_.gray, gray, prev_pts, curr_pts)) {
            
            // 2. Associate with depth
            std::vector<cv::Point3f> obj_pts;
            std::vector<cv::Point2f> img_pts;
            
            if (associateDepth(prev_frame_.depth, prev_pts, curr_pts, obj_pts, img_pts)) {
                
                // 3. Estimate motion with PnP
                result = estimatePnP(obj_pts, img_pts);
                
                // 4. Validate motion
                if (result.success && validateMotion(result)) {
                    updatePose(result);
                    publishOdometry(stamp);
                    publishGuess(stamp);  // Provide motion hint to RTAB-Map
                    tracking_ok = true;
                    consecutive_failures_ = 0;
                }
            }
        }
        
        // Publish Debug View if enabled
        if (config_.publish_debug) {
            publishDebugView(gray, prev_pts, curr_pts, result.inlier_indices, stamp);
        }
        
        if (!tracking_ok) {
            consecutive_failures_++;
        }
        
        updateState(result);
    }
    
    // Update previous frame
    // Always detect new features for next frame
    std::vector<cv::Point2f> new_points;
    detectFAST(gray, new_points);
    
    if (new_points.size() >= (size_t)config_.min_points) {
        std::lock_guard<std::mutex> lock(state_mutex_);
        gray.copyTo(prev_frame_.gray);
        depth.copyTo(prev_frame_.depth);
        prev_frame_.points = new_points;
        prev_frame_.timestamp = stamp;
        has_prev_frame_ = true;
    }
    
    // Publish tracking status
    std_msgs::msg::Bool tracking_msg;
    tracking_msg.data = tracking_ok;
    tracking_pub_->publish(tracking_msg);
    
    // Diagnostics
    if ((stamp - last_diag_time_).seconds() > 2.0) {
        publishDiagnostics(stamp);
        last_diag_time_ = stamp;
    }
    
    processed_frames_.fetch_add(1, std::memory_order_relaxed);
}

// ===================== YOLO & Italian Labels =====================

void FastFlowVONode::processYolo(const std::shared_ptr<dai::ImgDetections>& detections, cv::Mat& display_frame) {
    if (!detections) return;
    
    auto now = this->now();
    
    // Draw detections
    for (auto& det : detections->detections) {
        int x1 = det.xmin * display_frame.cols;
        int y1 = det.ymin * display_frame.rows;
        int x2 = det.xmax * display_frame.cols;
        int y2 = det.ymax * display_frame.rows;
        
        // Clamp
        x1 = std::max(0, std::min(x1, display_frame.cols - 1));
        y1 = std::max(0, std::min(y1, display_frame.rows - 1));
        x2 = std::max(0, std::min(x2, display_frame.cols - 1));
        y2 = std::max(0, std::min(y2, display_frame.rows - 1));
        
        cv::rectangle(display_frame, cv::Point(x1, y1), cv::Point(x2, y2), cv::Scalar(0, 255, 0), 2);
        
        std::string label = getItalianLabel(det.label);
        std::string conf = std::to_string((int)(det.confidence * 100)) + "%";
        std::string text = label + " " + conf;
        
        cv::putText(display_frame, text, cv::Point(x1, y1 - 10), 
                    cv::FONT_HERSHEY_SIMPLEX, 0.5, cv::Scalar(0, 255, 0), 2);
        
        // DEBUG LOGGING REQUESTED BY USER
        RCLCPP_INFO(get_logger(), "👀 YOLO DETECTED: %s (%.1f%%) at [%d, %d]", 
                    label.c_str(), det.confidence * 100, x1, y1);
    }
    
    // Publish Compressed
    std::vector<uchar> buf;
    cv::imencode(".jpg", display_frame, buf, {cv::IMWRITE_JPEG_QUALITY, 60});
    
    sensor_msgs::msg::CompressedImage msg;
    msg.header.stamp = now;
    msg.header.frame_id = "camera_color_optical_frame"; // YOLO uses color frame
    msg.format = "jpeg";
    msg.data = buf;
    
    rgb_compressed_pub_->publish(msg);
}

std::string FastFlowVONode::getItalianLabel(int class_id) {
    static const std::vector<std::string> labels = {
        "Persona", "Bicicletta", "Auto", "Moto", "Aereo", "Bus", "Treno", "Camion", "Barca", "Semaforo",
        "Idrante", "Segnale Stop", "Parchimetro", "Panchina", "Uccello", "Gatto", "Cane", "Cavallo", "Pecora", "Mucca",
        "Elefante", "Orso", "Zebra", "Giraffa", "Zaino", "Ombrello", "Borsa", "Cravatta", "Valigia", "Frisbee",
        "Sci", "Snowboard", "Pallone", "Aquilone", "Mazza da baseball", "Guantone", "Skateboard", "Surf", "Racchetta", "Bottiglia",
        "Bicchiere", "Tazza", "Forchetta", "Coltello", "Cucchiaio", "Ciotola", "Banana", "Mela", "Sandwich", "Arancia",
        "Broccoli", "Carota", "Hot dog", "Pizza", "Ciambella", "Torta", "Sedia", "Divano", "Pianta", "Letto",
        "Tavolo", "WC", "TV", "Laptop", "Mouse", "Tastiera", "Cellulare", "Microonde", "Forno", "Tostapane",
        "Lavandino", "Frigo", "Libro", "Orologio", "Vaso", "Forbici", "Teddy Bear", "Phon", "Spazzolino"
    };
    
    if (class_id >= 0 && class_id < (int)labels.size()) {
        return labels[class_id];
    }
    return "Ignoto";
}

void FastFlowVONode::detectFAST(const cv::Mat& gray, std::vector<cv::Point2f>& points) {
    points.clear();
    
    int cell_height = gray.rows / config_.grid_rows;
    int cell_width = gray.cols / config_.grid_cols;
    int max_per_cell = config_.max_features / (config_.grid_rows * config_.grid_cols);
    
    for (int row = 0; row < config_.grid_rows; row++) {
        for (int col = 0; col < config_.grid_cols; col++) {
            int x = col * cell_width;
            int y = row * cell_height;
            int w = (col == config_.grid_cols - 1) ? gray.cols - x : cell_width;
            int h = (row == config_.grid_rows - 1) ? gray.rows - y : cell_height;
            
            cv::Rect roi(x, y, w, h);
            cv::Mat cell = gray(roi);
            
            std::vector<cv::KeyPoint> keypoints;
            fast_detector_->detect(cell, keypoints);
            
            // Sort by response (strongest first)
            std::sort(keypoints.begin(), keypoints.end(),
                     [](const cv::KeyPoint& a, const cv::KeyPoint& b) {
                         return a.response > b.response;
                     });
            
            // Take top N from this cell
            int count = std::min((int)keypoints.size(), max_per_cell);
            for (int i = 0; i < count; i++) {
                points.emplace_back(keypoints[i].pt.x + x, keypoints[i].pt.y + y);
            }
        }
    }
    
    RCLCPP_DEBUG(get_logger(), "FAST detected %zu features (grid %dx%d)",
                points.size(), config_.grid_rows, config_.grid_cols);
}

// ===================== 2. KLT Tracking =====================

bool FastFlowVONode::trackKLT(const cv::Mat& prev_gray, const cv::Mat& curr_gray,
                               std::vector<cv::Point2f>& prev_pts, 
                               std::vector<cv::Point2f>& curr_pts) {
    
    if (prev_pts.empty()) {
        return false;
    }
    
    std::vector<uchar> status;
    std::vector<float> error;
    
    cv::Size win_size(config_.klt_win_size, config_.klt_win_size);
    cv::TermCriteria criteria(cv::TermCriteria::COUNT | cv::TermCriteria::EPS, 30, 0.01);
    
    // Forward tracking
    cv::calcOpticalFlowPyrLK(prev_gray, curr_gray, prev_pts, curr_pts,
                             status, error, win_size, config_.klt_max_level, criteria);
    
    // Forward-backward consistency check
    std::vector<cv::Point2f> back_pts;
    std::vector<uchar> back_status;
    std::vector<float> back_error;
    
    cv::calcOpticalFlowPyrLK(curr_gray, prev_gray, curr_pts, back_pts,
                             back_status, back_error, win_size, config_.klt_max_level, criteria);
    
    // Filter points
    std::vector<cv::Point2f> good_prev, good_curr;
    
    for (size_t i = 0; i < prev_pts.size(); i++) {
        if (status[i] == 0 || back_status[i] == 0) continue;
        if (error[i] > config_.klt_max_error) continue;
        
        // Forward-backward check
        float fb_dist = cv::norm(prev_pts[i] - back_pts[i]);
        if (fb_dist > config_.fb_threshold) continue;
        
        // Bounds check
        if (curr_pts[i].x < 0 || curr_pts[i].x >= curr_gray.cols) continue;
        if (curr_pts[i].y < 0 || curr_pts[i].y >= curr_gray.rows) continue;
        
        good_prev.push_back(prev_pts[i]);
        good_curr.push_back(curr_pts[i]);
    }
    
    prev_pts = good_prev;
    curr_pts = good_curr;
    
    RCLCPP_DEBUG(get_logger(), "KLT tracked %zu / %zu points (FB check)",
                curr_pts.size(), status.size());
    
    return curr_pts.size() >= (size_t)config_.min_points;
}

// ===================== 3. Depth Association =====================

bool FastFlowVONode::associateDepth(const cv::Mat& depth,
                                     const std::vector<cv::Point2f>& prev_pts,
                                     const std::vector<cv::Point2f>& curr_pts,
                                     std::vector<cv::Point3f>& obj_pts,
                                     std::vector<cv::Point2f>& img_pts) {
    
    obj_pts.clear();
    img_pts.clear();
    
    int valid_count = 0;
    
    for (size_t i = 0; i < prev_pts.size(); i++) {
        int u = static_cast<int>(prev_pts[i].x);
        int v = static_cast<int>(prev_pts[i].y);
        
        if (u < 0 || u >= depth.cols || v < 0 || v >= depth.rows) continue;
        
        float z = depth.at<uint16_t>(v, u) / 1000.0f;
        
        // Depth range filtering (only if enabled)
        if (config_.enable_depth_filter && (z < config_.min_depth || z > config_.max_depth)) continue;
        // Always reject invalid depth
        if (z <= 0.01f) continue;
        
        // Backproject to 3D (camera optical frame: Z forward, X right, Y down)
        float X_cam = (prev_pts[i].x - cx_) * z / fx_;
        float Y_cam = (prev_pts[i].y - cy_) * z / fy_;
        float Z_cam = z;
        
        // Floor reflection filter: transform to base_link and check Z height
        // Camera optical frame: Z forward, X right, Y down
        // Base link frame: X forward, Y left, Z up
        // Uses calibrated pitch from IMU gravity vector
        if (config_.enable_floor_filter && pitch_calibrated_.load(std::memory_order_acquire)) {
            double pitch = calibrated_pitch_.load(std::memory_order_acquire);
            double cos_pitch = std::cos(pitch);
            double sin_pitch = std::sin(pitch);
            
            // Transform from camera optical to base_link (simplified for pitch only):
            // X_base = Z_cam * cos(pitch) + Y_cam * sin(pitch) (forward)
            // Y_base = -X_cam (left)
            // Z_base = -Y_cam * cos(pitch) + Z_cam * sin(pitch) + camera_height (up)
            double Z_base = -Y_cam * cos_pitch + Z_cam * sin_pitch + config_.camera_height;
            
            // Reject points below floor (Z_base < floor_z_threshold)
            if (Z_base < config_.floor_z_threshold) {
                // This point is below floor level - likely a reflection or underground object
                continue;
            }
        }
        
        obj_pts.emplace_back(X_cam, Y_cam, Z_cam);
        img_pts.push_back(curr_pts[i]);
        valid_count++;
    }
    
    RCLCPP_DEBUG(get_logger(), "Depth associated %d / %zu points",
                valid_count, prev_pts.size());
    
    return obj_pts.size() >= (size_t)config_.min_points;
}

// ===================== 4. PnP Motion Estimation =====================

FastFlowVONode::TrackingResult FastFlowVONode::estimatePnP(
    const std::vector<cv::Point3f>& obj_pts,
    const std::vector<cv::Point2f>& img_pts) {
    
    TrackingResult result;
    result.tracked_points = obj_pts.size();
    
    if (obj_pts.size() < (size_t)config_.min_points) {
        return result;
    }
    
    cv::Mat rvec, tvec;
    std::vector<int> inliers;
    
    bool success = cv::solvePnPRansac(
        obj_pts, img_pts, camera_matrix_, cv::Mat(),
        rvec, tvec, false, 100, config_.reproj_error, 0.99, inliers,
        cv::SOLVEPNP_ITERATIVE
    );
    
    if (!success || inliers.size() < (size_t)config_.min_inliers) {
        RCLCPP_DEBUG(get_logger(), "PnP failed: success=%d, inliers=%zu",
                    success, inliers.size());
        return result;
    }
    
    result.success = true;
    result.rvec = rvec;
    result.tvec = tvec;
    result.inliers = inliers.size();
    result.inlier_indices = inliers; // Store indices for debug view
    
    // Calculate norms
    double tx = tvec.at<double>(0);
    double ty = tvec.at<double>(1);
    double tz = tvec.at<double>(2);
    result.translation_norm = std::sqrt(tx*tx + ty*ty + tz*tz);
    
    double rx = rvec.at<double>(0);
    double ry = rvec.at<double>(1);
    double rz = rvec.at<double>(2);
    result.rotation_norm = std::sqrt(rx*rx + ry*ry + rz*rz);
    
    RCLCPP_DEBUG(get_logger(), "PnP: inliers=%zu, t=%.3fm, r=%.2f°",
                inliers.size(), result.translation_norm, 
                result.rotation_norm * 180.0 / CV_PI);
    
    return result;
}

// ===================== 5. Motion Validation =====================

bool FastFlowVONode::validateMotion(const TrackingResult& result) {
    if (!result.success) {
        return false;
    }
    
    // Check translation magnitude
    if (result.translation_norm > config_.max_translation_per_frame) {
        RCLCPP_WARN(get_logger(), "Rejected: translation too large (%.3fm > %.3fm)",
                    result.translation_norm, config_.max_translation_per_frame);
        double cov = current_covariance_scale_.load(std::memory_order_acquire);
        current_covariance_scale_.store(cov * 2.0, std::memory_order_release);
        return false;
    }
    
    // Check rotation magnitude
    if (result.rotation_norm > config_.max_rotation_per_frame) {
        RCLCPP_WARN(get_logger(), "Rejected: rotation too large (%.1f° > %.1f°)",
                    result.rotation_norm * 180.0 / CV_PI,
                    config_.max_rotation_per_frame * 180.0 / CV_PI);
        double cov = current_covariance_scale_.load(std::memory_order_acquire);
        current_covariance_scale_.store(cov * 2.0, std::memory_order_release);
        return false;
    }
    
    // Check for noise floor - Zero Velocity Update (ZUPT)
    // If motion is below thresholds, robot is stationary - skip pose update
    if (result.translation_norm < config_.min_translation && 
        result.rotation_norm < 0.01) {  // ~0.6 degrees
        // Robot is stationary, still valid but skip update to prevent drift
        return false;
    }
    
    // Update motion statistics
    motion_stats_.update(result.translation_norm, result.rotation_norm);
    
    // Reset covariance scale on good tracking
    double cov = current_covariance_scale_.load(std::memory_order_acquire);
    current_covariance_scale_.store(std::max(1.0, cov * 0.9), std::memory_order_release);
    
    return true;
}

// ===================== 6. EMA Translation Filter =====================

Eigen::Vector3d FastFlowVONode::filterTranslation(const Eigen::Vector3d& raw_t) {
    if (!filter_initialized_.load(std::memory_order_acquire)) {
        filtered_translation_ = raw_t;
        filter_initialized_.store(true, std::memory_order_release);
        return raw_t;
    }
    
    filtered_translation_ = config_.ema_alpha * raw_t + 
                           (1.0 - config_.ema_alpha) * filtered_translation_;
    
    return filtered_translation_;
}

// ===================== State Management =====================

void FastFlowVONode::MotionStats::update(double t_norm, double r_norm) {
    translation_norms.push_back(t_norm);
    rotation_norms.push_back(r_norm);
    
    if (translation_norms.size() > window_size) {
        translation_norms.pop_front();
        rotation_norms.pop_front();
    }
}

double FastFlowVONode::MotionStats::getAvgTranslation() const {
    if (translation_norms.empty()) return 0.0;
    double sum = 0.0;
    for (auto v : translation_norms) sum += v;
    return sum / translation_norms.size();
}

double FastFlowVONode::MotionStats::getAvgRotation() const {
    if (rotation_norms.empty()) return 0.0;
    double sum = 0.0;
    for (auto v : rotation_norms) sum += v;
    return sum / rotation_norms.size();
}

void FastFlowVONode::updateState(const TrackingResult& result) {
    if (!result.success) {
        int failures = consecutive_failures_.fetch_add(1, std::memory_order_relaxed);
        
        if (failures >= config_.lost_threshold) {
            tracking_state_.store(static_cast<int>(TrackingState::TRACKING_LOST), std::memory_order_release);
        } else {
            tracking_state_.store(static_cast<int>(TrackingState::TRACKING_WEAK), std::memory_order_release);
        }
        
        double current = current_covariance_scale_.load(std::memory_order_acquire);
        current_covariance_scale_.store(current * 1.5, std::memory_order_release);
        return;
    }
    
    consecutive_failures_.store(0, std::memory_order_release);
    
    if (result.inliers >= config_.good_inlier_threshold) {
        tracking_state_.store(static_cast<int>(TrackingState::TRACKING_GOOD), std::memory_order_release);
        {
            std::lock_guard<std::mutex> lock(time_mutex_);
            last_good_tracking_time_ = this->now();
        }
    } else if (result.inliers >= config_.weak_inlier_threshold) {
        tracking_state_.store(static_cast<int>(TrackingState::TRACKING_WEAK), std::memory_order_release);
    } else if (result.inliers >= config_.critical_inlier_threshold) {
        tracking_state_.store(static_cast<int>(TrackingState::TRACKING_WEAK), std::memory_order_release);
    } else {
        tracking_state_.store(static_cast<int>(TrackingState::TRACKING_LOST), std::memory_order_release);
        current_covariance_scale_.store(1000.0, std::memory_order_release);
    }
}

void FastFlowVONode::updatePose(const TrackingResult& result) {
    // Convert cv::Mat to Eigen
    cv::Mat R_cam;
    cv::Rodrigues(result.rvec, R_cam);
    
    // Camera motion is inverse of object motion from solvePnP
    cv::Mat R_cam_inv = R_cam.t();
    cv::Mat t_cam_inv = -R_cam_inv * result.tvec;
    
    // Convert to Eigen
    Eigen::Matrix3d R;
    Eigen::Vector3d t;
    
    for (int i = 0; i < 3; ++i) {
        for (int j = 0; j < 3; ++j) {
            R(i, j) = R_cam_inv.at<double>(i, j);
        }
        t(i) = t_cam_inv.at<double>(i);
    }
    
    // Transform to base frame
    if (transform_initialized_) {
        Eigen::Matrix3d R_base_cam = T_base_camera_.linear();
        R = R_base_cam * R * R_base_cam.transpose();
        t = R_base_cam * t;
    }
    
    // Apply EMA filter to translation (rotation stays raw)
    Eigen::Vector3d t_filtered = filterTranslation(t);
    
    // Create delta transformation
    Eigen::Isometry3d delta = Eigen::Isometry3d::Identity();
    delta.linear() = R;
    delta.translation() = t_filtered;
    
    // Resilience: If tracking is LOST or critical, do not update pose
    TrackingState ts = static_cast<TrackingState>(tracking_state_.load(std::memory_order_acquire));
    if (ts == TrackingState::TRACKING_LOST) {
        return;
    }
    
    // Motion Gate: Block pose updates if robot is stationary
    // This prevents drift in front of white walls when the robot is not moving
    if (config_.enable_motion_gate && !isRobotMoving()) {
        // Robot is stationary - don't update pose to prevent phantom drift
        RCLCPP_DEBUG_THROTTLE(get_logger(), *get_clock(), 2000,
            "Motion Gate: Blocking VO update (motors_active=%d, imu_motion=%d)",
            motors_active_.load(), imu_motion_detected_.load());
        return;
    }

    // Update global pose
    std::lock_guard<std::mutex> lock(state_mutex_);
    pose_ = pose_ * delta;
    
    // Save delta for velocity calculation
    last_delta_translation_ = t_filtered;
    // Extract yaw from rotation matrix
    double yaw = atan2(R(1, 0), R(0, 0));
    last_delta_yaw_ = yaw;
}

// ===================== Publishing =====================

void FastFlowVONode::publishOdometry(const rclcpp::Time& stamp) {
    nav_msgs::msg::Odometry odom_msg;
    odom_msg.header.stamp = stamp;
    odom_msg.header.frame_id = config_.odom_frame;
    odom_msg.child_frame_id = config_.base_frame;
    
    // ===================== VELOCITY CALCULATION =====================
    // This VO is used as a VELOCITY SENSOR, not a pose estimator!
    // Position comes from RTAB-Map, orientation from IMU
    
    Eigen::Vector3d delta_t;
    double delta_yaw;
    Eigen::Vector3d position;
    Eigen::Quaterniond orientation;
    
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        delta_t = last_delta_translation_;
        delta_yaw = last_delta_yaw_;
        position = pose_.translation();
        orientation = Eigen::Quaterniond(pose_.rotation());
    }
    
    // Calculate dt
    double dt = 0.0;
    {
        std::lock_guard<std::mutex> lock(time_mutex_);
        if (velocity_initialized_.load()) {
            dt = (stamp - last_velocity_time_).seconds();
        } else {
            velocity_initialized_.store(true);
        }
        last_velocity_time_ = stamp;
    }
    
    // Compute velocity from delta motion (velocity = delta / dt)
    double vx = 0.0, vy = 0.0, vyaw = 0.0;
    if (dt > 0.001 && dt < 0.5) {  // Reasonable dt range
        vx = delta_t.x() / dt;
        vy = delta_t.y() / dt;
        vyaw = delta_yaw / dt;
    }
    
    // ===================== POSE: SET TO ZERO =====================
    // NON vogliamo che l'EKF usi la nostra pose!
    // Pose è a zero con covariance infinita = "non usarmi"
    odom_msg.pose.pose.position.x = 0.0;
    odom_msg.pose.pose.position.y = 0.0;
    odom_msg.pose.pose.position.z = 0.0;
    
    odom_msg.pose.pose.orientation.x = 0.0;
    odom_msg.pose.pose.orientation.y = 0.0;
    odom_msg.pose.pose.orientation.z = 0.0;
    odom_msg.pose.pose.orientation.w = 1.0;  // Identity quaternion
    
    // ===================== POSE COVARIANCE: INFINITE =====================
    // Tell EKF: "Don't trust my position at all!"
    std::fill(odom_msg.pose.covariance.begin(), odom_msg.pose.covariance.end(), 0.0);
    for (int i = 0; i < 6; ++i) {
        odom_msg.pose.covariance[i * 6 + i] = 9999.0;  // Diagonal = inf
    }
    
    // ===================== TWIST (VELOCITY) =====================
    odom_msg.twist.twist.linear.x = vx;
    odom_msg.twist.twist.linear.y = vy;
    odom_msg.twist.twist.linear.z = 0.0;  // 2D
    odom_msg.twist.twist.angular.x = 0.0;
    odom_msg.twist.twist.angular.y = 0.0;
    odom_msg.twist.twist.angular.z = vyaw;
    
    // ===================== TWIST COVARIANCE: Adaptive =====================
    // Base covariance for velocity
    double base_vel_cov = 0.01;
    
    TrackingState current_state = static_cast<TrackingState>(
        tracking_state_.load(std::memory_order_acquire));
    
    if (current_state == TrackingState::TRACKING_WEAK) {
        base_vel_cov *= 3.0;  // Less confident
    } else if (current_state == TrackingState::TRACKING_LOST) {
        base_vel_cov = 9999.0;  // Can't trust velocity at all
    }
    
    // ZUPT: If motion is very small, we're confident it's zero
    double motion_magnitude = sqrt(vx*vx + vy*vy);
    if (motion_magnitude < 0.01 && fabs(vyaw) < 0.01) {
        // Robot is stationary - very confident in zero velocity
        vx = 0.0;
        vy = 0.0;
        vyaw = 0.0;
        odom_msg.twist.twist.linear.x = 0.0;
        odom_msg.twist.twist.linear.y = 0.0;
        odom_msg.twist.twist.angular.z = 0.0;
        base_vel_cov = 0.001;  // Very confident!
    }
    
    std::fill(odom_msg.twist.covariance.begin(), odom_msg.twist.covariance.end(), 0.0);
    odom_msg.twist.covariance[0]  = base_vel_cov;      // vx
    odom_msg.twist.covariance[7]  = base_vel_cov;      // vy
    odom_msg.twist.covariance[14] = base_vel_cov * 2;  // vz (less confident)
    odom_msg.twist.covariance[21] = base_vel_cov * 2;  // wx
    odom_msg.twist.covariance[28] = base_vel_cov * 2;  // wy
    odom_msg.twist.covariance[35] = base_vel_cov;      // wz (yaw rate)
    
    odom_pub_->publish(odom_msg);
    
    // TF publishing disabled - RTAB-Map handles odom->base_link
    // The pose we have isn't accurate enough for TF
}

void FastFlowVONode::publishGuess(const rclcpp::Time& stamp) {
    // Only publish guess if tracking is good
    TrackingState ts = static_cast<TrackingState>(tracking_state_.load(std::memory_order_acquire));
    if (ts != TrackingState::TRACKING_GOOD && ts != TrackingState::TRACKING_WEAK) {
        return;  // Don't provide guess if tracking is lost
    }
    
    Eigen::Vector3d delta_t;
    double delta_yaw;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        delta_t = last_delta_translation_;
        delta_yaw = last_delta_yaw_;
    }
    
    // Skip if motion is negligible
    if (delta_t.norm() < 0.0001 && std::abs(delta_yaw) < 0.0001) {
        return;
    }
    
    geometry_msgs::msg::TransformStamped msg;
    msg.header.stamp = stamp;
    msg.header.frame_id = config_.base_frame;
    msg.child_frame_id = config_.base_frame;
    
    // Delta translation (motion since last frame)
    msg.transform.translation.x = delta_t.x();
    msg.transform.translation.y = delta_t.y();
    msg.transform.translation.z = 0.0;  // 2D
    
    // Delta rotation as quaternion
    double half_yaw = delta_yaw * 0.5;
    msg.transform.rotation.x = 0.0;
    msg.transform.rotation.y = 0.0;
    msg.transform.rotation.z = std::sin(half_yaw);
    msg.transform.rotation.w = std::cos(half_yaw);
    
    guess_pub_->publish(msg);
    
    RCLCPP_DEBUG_THROTTLE(get_logger(), *get_clock(), 2000,
        "Guess published: dx=%.4f, dy=%.4f, dyaw=%.4f",
        delta_t.x(), delta_t.y(), delta_yaw);
}

void FastFlowVONode::publishDiagnostics(const rclcpp::Time& stamp) {
    diagnostic_msgs::msg::DiagnosticArray diag_msg;
    diag_msg.header.stamp = stamp;
    
    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "Visual Odometry (FAST+Flow)";
    status.hardware_id = "OAK-D";
    
    // Thread-safe read of tracking state
    TrackingState diag_state = static_cast<TrackingState>(
        tracking_state_.load(std::memory_order_acquire));
    
    switch (diag_state) {
        case TrackingState::UNINITIALIZED:
            status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
            status.message = "Initializing";
            break;
        case TrackingState::TRACKING_GOOD:
            status.level = diagnostic_msgs::msg::DiagnosticStatus::OK;
            status.message = "Tracking Good";
            break;
        case TrackingState::TRACKING_WEAK:
            status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
            status.message = "Tracking Weak";
            break;
        case TrackingState::TRACKING_LOST:
            status.level = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
            status.message = "Tracking Lost";
            break;
    }
    
    diagnostic_msgs::msg::KeyValue kv;
    
    kv.key = "State";
    kv.value = stateToString(diag_state);
    status.values.push_back(kv);
    
    kv.key = "Covariance_Scale";
    std::ostringstream oss;
    double cov_diag = current_covariance_scale_.load(std::memory_order_acquire);
    oss << std::fixed << std::setprecision(2) << cov_diag;
    kv.value = oss.str();
    status.values.push_back(kv);
    
    kv.key = "Avg_Translation";
    oss.str("");
    oss << std::fixed << std::setprecision(4) << motion_stats_.getAvgTranslation() << "m";
    kv.value = oss.str();
    status.values.push_back(kv);
    
    diag_msg.status.push_back(status);
    diag_pub_->publish(diag_msg);
}

std::string FastFlowVONode::stateToString(TrackingState state) const {
    switch (state) {
        case TrackingState::UNINITIALIZED: return "UNINITIALIZED";
        case TrackingState::TRACKING_GOOD: return "TRACKING_GOOD";
        case TrackingState::TRACKING_WEAK: return "TRACKING_WEAK";
        case TrackingState::TRACKING_LOST: return "TRACKING_LOST";
        default: return "UNKNOWN";
    }
}

// ===================== Image Publishing for RTAB-Map =====================

void FastFlowVONode::publishImages(const cv::Mat& gray, const cv::Mat& depth, 
                                    const rclcpp::Time& stamp) {
    // Publish grayscale as RGB (RTAB-Map expects /rgb/image)
    // Convert mono8 to bgr8 for compatibility
    cv::Mat rgb;
    cv::cvtColor(gray, rgb, cv::COLOR_GRAY2BGR);
    
    auto rgb_msg = cv_bridge::CvImage(std_msgs::msg::Header(), "bgr8", rgb).toImageMsg();
    rgb_msg->header.stamp = stamp;
    rgb_msg->header.frame_id = config_.camera_frame;
    rgb_pub_->publish(*rgb_msg);
    
    // Publish depth as 16UC1 (millimeters)
    auto depth_msg = cv_bridge::CvImage(std_msgs::msg::Header(), 
                                        sensor_msgs::image_encodings::TYPE_16UC1, 
                                        depth).toImageMsg();
    depth_msg->header.stamp = stamp;
    depth_msg->header.frame_id = config_.camera_frame;
    depth_pub_->publish(*depth_msg);
    
    // Publish camera info
    sensor_msgs::msg::CameraInfo camera_info_msg;
    camera_info_msg.header.stamp = stamp;
    camera_info_msg.header.frame_id = config_.camera_frame;
    camera_info_msg.width = gray.cols;
    camera_info_msg.height = gray.rows;
    camera_info_msg.distortion_model = "plumb_bob";
    
    // K matrix (3x3 intrinsics)
    camera_info_msg.k = {fx_, 0.0, cx_, 
                        0.0, fy_, cy_, 
                        0.0, 0.0, 1.0};
    
    // P matrix (3x4 projection) - for rectified images, same as K with column of zeros
    camera_info_msg.p = {fx_, 0.0, cx_, 0.0,
                        0.0, fy_, cy_, 0.0,
                        0.0, 0.0, 1.0, 0.0};
    
    // R matrix (3x3 rectification) - identity for rectified
    camera_info_msg.r = {1.0, 0.0, 0.0,
                        0.0, 1.0, 0.0,
                        0.0, 0.0, 1.0};
    
    // D vector (distortion coefficients) - empty for rectified
    camera_info_msg.d = {0.0, 0.0, 0.0, 0.0, 0.0};
    
    camera_info_pub_->publish(camera_info_msg);
    
    /* --- Manual Compression DISABLED to save CPU ---
    // RGB -> JPG (Only if YOLO is disabled, otherwise YOLO publishes the colored/labeled one)
    if (!config_.enable_yolo || config_.yolo_blob_path.empty()) {
        std::vector<uchar> buf_rgb;
        cv::imencode(".jpg", rgb, buf_rgb, {cv::IMWRITE_JPEG_QUALITY, 50}); 
        
        sensor_msgs::msg::CompressedImage rgb_comp;
        rgb_comp.header = rgb_msg->header;
        rgb_comp.format = "jpeg";
        rgb_comp.data = buf_rgb;
        rgb_compressed_pub_->publish(rgb_comp);
    }
    
    // Depth -> PNG (Lossless) - kept for strict data adherence
    std::vector<uchar> buf_depth;
    cv::imencode(".png", depth, buf_depth); 
    
    sensor_msgs::msg::CompressedImage depth_comp;
    depth_comp.header = depth_msg->header;
    depth_comp.format = "png";
    depth_comp.data = buf_depth;
    depth_compressed_pub_->publish(depth_comp);
    */
    
    if (config_.publish_debug) {
        publishDepthPreview(depth, stamp);
    }
}

void FastFlowVONode::publishDebugView(const cv::Mat& gray, 
                                     const std::vector<cv::Point2f>& prev_pts,
                                     const std::vector<cv::Point2f>& curr_pts,
                                     const std::vector<int>& inliers,
                                     const rclcpp::Time& stamp) {
    
    if (curr_pts.empty()) return;

    cv::Mat debug_img;
    cv::cvtColor(gray, debug_img, cv::COLOR_GRAY2BGR);
    
    // Create a set of inlier indices for O(1) lookup
    std::vector<bool> is_inlier(curr_pts.size(), false);
    for (int idx : inliers) {
        if (idx >= 0 && idx < (int)curr_pts.size()) {
            is_inlier[idx] = true;
        }
    }
    
    // Draw tracks
    for (size_t i = 0; i < curr_pts.size(); i++) {
        cv::Scalar color = is_inlier[i] ? cv::Scalar(0, 255, 0) : cv::Scalar(0, 0, 255); // Green vs Red
        
        cv::line(debug_img, prev_pts[i], curr_pts[i], color, 1);
        cv::circle(debug_img, curr_pts[i], 2, color, -1);
    }
    
    // Draw status
    TrackingState debug_state = static_cast<TrackingState>(
        tracking_state_.load(std::memory_order_acquire));
    std::string status_text = stateToString(debug_state);
    cv::putText(debug_img, status_text, cv::Point(10, 30), 
                cv::FONT_HERSHEY_SIMPLEX, 1.0, cv::Scalar(0, 255, 255), 2);
                
    // Compress and Publish
    std::vector<uchar> buf;
    cv::imencode(".jpg", debug_img, buf, {cv::IMWRITE_JPEG_QUALITY, 60});
    
    sensor_msgs::msg::CompressedImage msg;
    msg.header.stamp = stamp;
    msg.header.frame_id = config_.camera_frame;
    msg.format = "jpeg";
    msg.data = buf;
    
    debug_view_pub_->publish(msg);
}

void FastFlowVONode::publishDepthPreview(const cv::Mat& depth, const rclcpp::Time& stamp) {
    // Convert 16UC1 to 8UC1 with Jet Colormap
    cv::Mat adjMap;
    
    // Normalize: 0.3m (300mm) -> 255, 5.0m (5000mm) -> 0
    // We clip at 5m for better contrast in near range
    double min_val = config_.min_depth * 1000.0;
    double max_val = 5000.0; // Fixed visual range for consistency
    
    depth.convertTo(adjMap, CV_8UC1, 255.0 / (max_val - min_val), -min_val * 255.0 / (max_val - min_val));
    
    cv::Mat color_depth;
    cv::applyColorMap(adjMap, color_depth, cv::COLORMAP_JET);
    
    // Compress
    std::vector<uchar> buf;
    cv::imencode(".jpg", color_depth, buf, {cv::IMWRITE_JPEG_QUALITY, 60});
    
    sensor_msgs::msg::CompressedImage msg;
    msg.header.stamp = stamp;
    msg.header.frame_id = config_.camera_frame;
    msg.format = "jpeg";
    msg.data = buf;
    
    depth_preview_pub_->publish(msg);
}

// ===================== Motion Gate Functions =====================

void FastFlowVONode::cmdVelCallback(const geometry_msgs::msg::Twist::SharedPtr msg) {
    // Check if any velocity command is non-zero
    bool has_motion = (std::abs(msg->linear.x) > 0.001) ||
                      (std::abs(msg->linear.y) > 0.001) ||
                      (std::abs(msg->angular.z) > 0.001);
    
    motors_active_.store(has_motion, std::memory_order_release);
    
    {
        std::lock_guard<std::mutex> lock(time_mutex_);
        last_cmd_vel_time_ = this->now();
    }
    
    RCLCPP_DEBUG_THROTTLE(get_logger(), *get_clock(), 2000,
        "cmd_vel received: linear=%.3f, angular=%.3f, motors_active=%d",
        msg->linear.x, msg->angular.z, has_motion);
}

bool FastFlowVONode::isRobotMoving() {
    // Get last cmd_vel time with lock
    rclcpp::Time cmd_time;
    {
        std::lock_guard<std::mutex> lock(time_mutex_);
        cmd_time = last_cmd_vel_time_;
    }
    
    // Check if cmd_vel is recent and active
    bool cmd_vel_active = false;
    double time_since_cmd = (this->now() - cmd_time).seconds();
    
    if (time_since_cmd < config_.cmd_vel_timeout) {
        cmd_vel_active = motors_active_.load(std::memory_order_acquire);
    }
    
    // Robot is moving if motors are active OR IMU detects motion
    return cmd_vel_active || imu_motion_detected_.load(std::memory_order_acquire);
}

// ===================== Main Function =====================

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    
    rclcpp::NodeOptions options;
    auto node = std::make_shared<FastFlowVONode>(options);
    
    rclcpp::spin(node);
    rclcpp::shutdown();
    
    return 0;
}
