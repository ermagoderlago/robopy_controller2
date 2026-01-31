#include "fast_flow_vo_node.hpp"

#include <cv_bridge/cv_bridge.hpp>
#include <sensor_msgs/image_encodings.hpp>
#include <opencv2/calib3d.hpp>

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
    
    // FAST Detection
    config_.fast_threshold = declare_parameter<int>("fast_threshold", 15);
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
    
    // Performance
    config_.skip_frames = declare_parameter<int>("skip_frames", 1);
    
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
    odom_pub_ = create_publisher<nav_msgs::msg::Odometry>("/vo/odom", 10);
    diag_pub_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>("/diagnostics", 10);
    tracking_pub_ = create_publisher<std_msgs::msg::Bool>("/vo/tracking_ok", 10);
    
    // Image publishers for RTAB-Map
    rgb_pub_ = create_publisher<sensor_msgs::msg::Image>("/rgb/image", 10);
    depth_pub_ = create_publisher<sensor_msgs::msg::Image>("/camera/depth/image_raw", 10);
    camera_info_pub_ = create_publisher<sensor_msgs::msg::CameraInfo>("/camera/camera_info", 10);
    
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
        monoLeft->setFps(config_.depth_fps);
        
        monoRight->setBoardSocket(dai::CameraBoardSocket::CAM_C);
        monoRight->setResolution(dai::MonoCameraProperties::SensorResolution::THE_400_P);
        monoRight->setFps(config_.depth_fps);
        
        stereo->setDefaultProfilePreset(dai::node::StereoDepth::PresetMode::DEFAULT);
        stereo->setLeftRightCheck(true);
        stereo->setSubpixel(true);
        stereo->setDepthAlign(dai::CameraBoardSocket::CAM_B);
        
        monoLeft->out.link(stereo->left);
        monoRight->out.link(stereo->right);
        
        auto xoutRect = pipeline_->create<dai::node::XLinkOut>();
        xoutRect->setStreamName("rect_left");
        stereo->rectifiedLeft.link(xoutRect->input);
        
        auto xoutDepth = pipeline_->create<dai::node::XLinkOut>();
        xoutDepth->setStreamName("depth");
        stereo->depth.link(xoutDepth->input);
        
        auto config = stereo->initialConfig.get();
        config.postProcessing.thresholdFilter.minRange = static_cast<int>(config_.min_depth * 1000);
        config.postProcessing.thresholdFilter.maxRange = static_cast<int>(config_.max_depth * 1000);
        stereo->initialConfig.set(config);
        
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
    
    int frame_counter = 0;
    
    while (running_ && rclcpp::ok()) {
        try {
            auto rectFrame = qRect->tryGet<dai::ImgFrame>();
            auto depthFrame = qDepth->tryGet<dai::ImgFrame>();
            
            if (rectFrame && depthFrame) {
                frame_counter++;
                
                if (frame_counter % config_.skip_frames != 0) {
                    continue;
                }
                
                cv::Mat gray = rectFrame->getCvFrame();
                cv::Mat depth = depthFrame->getCvFrame();
                auto stamp = this->now();
                
                // Publish images for RTAB-Map
                publishImages(gray, depth, stamp);
                
                processFrame(gray, depth, stamp);
            }
            
            std::this_thread::sleep_for(std::chrono::microseconds(500));
            
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
                    tracking_ok = true;
                    consecutive_failures_ = 0;
                }
            }
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
        prev_frame_.gray = gray.clone();
        prev_frame_.depth = depth.clone();
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
    
    processed_frames_++;
}

// ===================== 1. FAST Detection =====================

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
        
        if (z < config_.min_depth || z > config_.max_depth) continue;
        
        // Backproject to 3D
        float X = (prev_pts[i].x - cx_) * z / fx_;
        float Y = (prev_pts[i].y - cy_) * z / fy_;
        
        obj_pts.emplace_back(X, Y, z);
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
        current_covariance_scale_ *= 2.0;
        return false;
    }
    
    // Check rotation magnitude
    if (result.rotation_norm > config_.max_rotation_per_frame) {
        RCLCPP_WARN(get_logger(), "Rejected: rotation too large (%.1f° > %.1f°)",
                    result.rotation_norm * 180.0 / CV_PI,
                    config_.max_rotation_per_frame * 180.0 / CV_PI);
        current_covariance_scale_ *= 2.0;
        return false;
    }
    
    // Check for noise floor
    if (result.translation_norm < config_.min_translation && 
        result.rotation_norm < 0.001) {
        // Robot is stationary, still valid but skip update
        return false;
    }
    
    // Update motion statistics
    motion_stats_.update(result.translation_norm, result.rotation_norm);
    
    // Reset covariance scale on good tracking
    current_covariance_scale_ = std::max(1.0, current_covariance_scale_ * 0.9);
    
    return true;
}

// ===================== 6. EMA Translation Filter =====================

Eigen::Vector3d FastFlowVONode::filterTranslation(const Eigen::Vector3d& raw_t) {
    if (!filter_initialized_) {
        filtered_translation_ = raw_t;
        filter_initialized_ = true;
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
        if (consecutive_failures_ >= config_.lost_threshold) {
            tracking_state_ = TrackingState::TRACKING_LOST;
        } else {
            tracking_state_ = TrackingState::TRACKING_WEAK;
        }
        current_covariance_scale_ *= 1.5;
        return;
    }
    
    if (result.inliers >= config_.good_inlier_threshold) {
        tracking_state_ = TrackingState::TRACKING_GOOD;
        last_good_tracking_time_ = this->now();
    } else if (result.inliers >= config_.weak_inlier_threshold) {
        tracking_state_ = TrackingState::TRACKING_WEAK;
    } else {
        tracking_state_ = TrackingState::TRACKING_WEAK;
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
    
    // Update global pose
    std::lock_guard<std::mutex> lock(state_mutex_);
    pose_ = pose_ * delta;
}

// ===================== Publishing =====================

void FastFlowVONode::publishOdometry(const rclcpp::Time& stamp) {
    nav_msgs::msg::Odometry odom_msg;
    odom_msg.header.stamp = stamp;
    odom_msg.header.frame_id = config_.odom_frame;
    odom_msg.child_frame_id = config_.base_frame;
    
    Eigen::Vector3d position;
    Eigen::Quaterniond orientation;
    
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        position = pose_.translation();
        orientation = Eigen::Quaterniond(pose_.rotation());
    }
    
    odom_msg.pose.pose.position.x = position.x();
    odom_msg.pose.pose.position.y = position.y();
    odom_msg.pose.pose.position.z = position.z();
    
    odom_msg.pose.pose.orientation.x = orientation.x();
    odom_msg.pose.pose.orientation.y = orientation.y();
    odom_msg.pose.pose.orientation.z = orientation.z();
    odom_msg.pose.pose.orientation.w = orientation.w();
    
    // Adaptive covariance
    double base_pos_cov = 0.01 * current_covariance_scale_;
    double base_rot_cov = 0.05 * current_covariance_scale_;
    
    // State-based scaling
    if (tracking_state_ == TrackingState::TRACKING_WEAK) {
        base_pos_cov *= 3.0;
        base_rot_cov *= 2.0;
    } else if (tracking_state_ == TrackingState::TRACKING_LOST) {
        base_pos_cov *= 10.0;
        base_rot_cov *= 5.0;
    }
    
    // Fill covariance matrix
    std::fill(odom_msg.pose.covariance.begin(), odom_msg.pose.covariance.end(), 0.0);
    odom_msg.pose.covariance[0]  = base_pos_cov;      // x
    odom_msg.pose.covariance[7]  = base_pos_cov;      // y
    odom_msg.pose.covariance[14] = base_pos_cov * 3;  // z
    odom_msg.pose.covariance[21] = base_rot_cov * 2;  // roll
    odom_msg.pose.covariance[28] = base_rot_cov * 2;  // pitch
    odom_msg.pose.covariance[35] = base_rot_cov;      // yaw
    
    // Twist covariance (not estimated)
    std::fill(odom_msg.twist.covariance.begin(), odom_msg.twist.covariance.end(), -1.0);
    
    odom_pub_->publish(odom_msg);
}

void FastFlowVONode::publishDiagnostics(const rclcpp::Time& stamp) {
    diagnostic_msgs::msg::DiagnosticArray diag_msg;
    diag_msg.header.stamp = stamp;
    
    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "Visual Odometry (FAST+Flow)";
    status.hardware_id = "OAK-D";
    
    switch (tracking_state_) {
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
    kv.value = stateToString(tracking_state_);
    status.values.push_back(kv);
    
    kv.key = "Covariance_Scale";
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(2) << current_covariance_scale_;
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
