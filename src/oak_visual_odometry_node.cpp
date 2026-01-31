#include "oak_visual_odometry_node.hpp"
#include "camera_transform_handler.hpp"
#include "adaptive_covariance_estimator.hpp"

#include <cv_bridge/cv_bridge.hpp>
#include <opencv2/calib3d.hpp>
#include <opencv2/features2d.hpp>
#include <opencv2/imgproc.hpp>

#include <chrono>
#include <sstream>
#include <algorithm>

using namespace std::chrono_literals;

// ===================== FrameSynchronizer Implementation =====================

template<typename T1, typename T2>
class OakVisualOdometryNode::FrameSynchronizer {
public:
    struct SyncedPair {
        std::shared_ptr<T1> first;
        std::shared_ptr<T2> second;
        std::chrono::time_point<std::chrono::steady_clock> timestamp;
        bool valid = false;
    };

    struct Stats {
        size_t queue1_size;
        size_t queue2_size;
        size_t synced_count;
        size_t total_matched;
        size_t total_dropped;
    };

private:
    struct TimestampedFrame {
        std::shared_ptr<T1> frame1;
        std::shared_ptr<T2> frame2;
        std::chrono::time_point<std::chrono::steady_clock> timestamp;
    };

    mutable std::mutex mutex_;
    std::deque<TimestampedFrame> queue1_;
    std::deque<TimestampedFrame> queue2_;
    std::deque<SyncedPair> synced_pairs_;
    std::chrono::milliseconds max_time_diff_;
    size_t max_queue_size_;
    size_t total_matched_ = 0;
    size_t total_dropped_ = 0;

public:
    FrameSynchronizer(int max_time_diff_ms = 20, size_t max_queue_size = 10)
        : max_time_diff_(std::chrono::milliseconds(max_time_diff_ms))
        , max_queue_size_(max_queue_size) {}

    void addFirst(std::shared_ptr<T1> frame) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto ts = frame->getTimestamp();
        TimestampedFrame tf;
        tf.frame1 = frame;
        tf.timestamp = ts;
        queue1_.push_back(tf);
        
        if (queue1_.size() > max_queue_size_) {
            queue1_.pop_front();
            total_dropped_++;
        }
        tryMatch();
    }

    void addSecond(std::shared_ptr<T2> frame) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto ts = frame->getTimestamp();
        TimestampedFrame tf;
        tf.frame2 = frame;
        tf.timestamp = ts;
        queue2_.push_back(tf);
        
        if (queue2_.size() > max_queue_size_) {
            queue2_.pop_front();
            total_dropped_++;
        }
        tryMatch();
    }

    SyncedPair getNext() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (synced_pairs_.empty()) {
            return SyncedPair{nullptr, nullptr, {}, false};
        }
        auto pair = synced_pairs_.front();
        synced_pairs_.pop_front();
        return pair;
    }

    bool hasNext() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return !synced_pairs_.empty();
    }

    Stats getStats() const {
        std::lock_guard<std::mutex> lock(mutex_);
        Stats s;
        s.queue1_size = queue1_.size();
        s.queue2_size = queue2_.size();
        s.synced_count = synced_pairs_.size();
        s.total_matched = total_matched_;
        s.total_dropped = total_dropped_;
        return s;
    }

    void clear() {
        std::lock_guard<std::mutex> lock(mutex_);
        queue1_.clear();
        queue2_.clear();
        synced_pairs_.clear();
    }

private:
    void tryMatch() {
        auto it1 = queue1_.begin();
        auto it2 = queue2_.begin();

        while (it1 != queue1_.end() && it2 != queue2_.end()) {
            auto diff = it1->timestamp - it2->timestamp;
            auto abs_diff_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                (diff.count() >= 0) ? diff : -diff);
            auto max_diff_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(max_time_diff_);

            if (abs_diff_ns <= max_diff_ns) {
                auto ts = (diff.count() > 0) ? it2->timestamp : it1->timestamp;
                
                SyncedPair sp;
                sp.first = it1->frame1;
                sp.second = it2->frame2;
                sp.timestamp = ts;
                sp.valid = true;
                synced_pairs_.push_back(sp);
                
                total_matched_++;
                it1 = queue1_.erase(it1);
                it2 = queue2_.erase(it2);
            } else if (diff.count() < 0) {
                ++it1;
            } else {
                ++it2;
            }
        }
    }
};

// ===================== Constructor =====================

OakVisualOdometryNode::OakVisualOdometryNode(const rclcpp::NodeOptions& options)
    : Node("oak_visual_odometry", options),
      last_diag_time_(this->now()),
      last_good_tracking_time_(this->now())
{
    // Load parameters - General
    config_.publish_tf = declare_parameter<bool>("publish_tf", false);
    config_.odom_frame = declare_parameter<std::string>("odom_frame", "odom");
    config_.base_frame = declare_parameter<std::string>("base_frame", "base_link");
    config_.camera_frame = declare_parameter<std::string>("camera_frame", "camera_optical_frame");
    
    // Algorithms
    config_.enable_orb = declare_parameter<bool>("use_orb_primary", true);
    config_.enable_superpoint = declare_parameter<bool>("superpoint_relocalization", false);
    config_.use_superpoint_for_relocalization = config_.enable_superpoint;
    config_.use_imu_rotation_prior = declare_parameter<bool>("use_imu_rotation_prior", true);
    config_.use_geometric_gating = declare_parameter<bool>("use_geometric_gating", true);
    config_.enable_yaw_only_fallback = declare_parameter<bool>("enable_yaw_only_fallback", true);
    
    // ORB Parameters
    config_.max_orb_features = declare_parameter<int>("max_orb_features", 500);
    config_.min_features = declare_parameter<int>("min_features", 30);
    config_.min_inliers = declare_parameter<int>("min_inliers", 12);
    config_.lost_tracking_threshold = declare_parameter<int>("lost_tracking_threshold", 10);
    
    // IMU Parameters
    config_.imu_buffer_duration = declare_parameter<double>("imu_buffer_duration", 0.2);
    config_.max_gyro_norm = declare_parameter<double>("max_gyro_norm", 5.0);
    
    // Geometric Gating
    config_.max_point_depth = declare_parameter<double>("max_point_depth", 10.0);
    config_.min_baseline_ratio = declare_parameter<double>("min_baseline_ratio", 0.01);
    config_.max_epipolar_error = declare_parameter<double>("max_epipolar_error", 2.0);
    
    // Yaw-only Mode
    config_.yaw_only_translation_threshold = declare_parameter<double>("yaw_only_translation_threshold", 0.02);
    config_.yaw_only_covariance_scale = declare_parameter<double>("yaw_only_covariance_scale", 10.0);
    
    // Depth Processing
    config_.min_depth = declare_parameter<double>("min_depth", 0.3);
    config_.max_depth = declare_parameter<double>("max_depth", 8.0);
    config_.depth_fps = declare_parameter<double>("depth_fps", 30.0);
    
    // Performance
    config_.vo_skip_frames = declare_parameter<int>("vo_skip_frames", 1);
    config_.enable_clahe = declare_parameter<bool>("enable_clahe", false);
    config_.filter_alpha = declare_parameter<double>("filter_alpha", 0.25);
    
    // RTAB-Map Integration
    config_.rtabmap_integration = declare_parameter<bool>("rtabmap_integration", true);
    config_.subscribe_initial_pose = declare_parameter<bool>("subscribe_initial_pose", true);
    
    // Initialize Frame Synchronizer
    frame_sync_ = std::make_unique<FrameSynchronizer<dai::ImgFrame, dai::ImgFrame>>(20, 10);
    
    // Initialize OpenCV components
    if (config_.enable_orb) {
        orb_detector_ = cv::ORB::create(config_.max_orb_features, 1.2f, 8, 31, 0, 2, 
                                        cv::ORB::HARRIS_SCORE, 31, 20);
        orb_matcher_ = cv::DescriptorMatcher::create("BruteForce-Hamming");
    }
    
    if (config_.enable_superpoint) {
        sp_matcher_ = cv::DescriptorMatcher::create(cv::DescriptorMatcher::FLANNBASED);
    }
    
    if (config_.enable_clahe) {
        clahe_ = cv::createCLAHE(3.0, cv::Size(8, 8));
    }
    
    // Initialize covariance estimator
    AdaptiveCovarianceEstimator::Params cov_params;
    cov_params.minimum_inliers = config_.min_inliers;
    covariance_estimator_ = std::make_unique<AdaptiveCovarianceEstimator>(cov_params);
    
    // Initialize DepthAI
    if (!initializeDepthAI()) {
        RCLCPP_ERROR(get_logger(), "Failed to initialize DepthAI");
        throw std::runtime_error("DepthAI initialization failed");
    }
    
    // Initialize transform handler
    transform_handler_ = std::make_unique<CameraTransformHandler>(
        this, config_.base_frame, "camera_link", config_.camera_frame);
    
    // ROS Publishers
    odom_pub_ = create_publisher<nav_msgs::msg::Odometry>("/vo/odom", 10);
    diag_pub_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>("/diagnostics", 10);
    tracking_pub_ = create_publisher<std_msgs::msg::Bool>("/vo/tracking_ok", 10);
    
    if (config_.publish_tf) {
        tf_broadcaster_ = std::make_unique<tf2_ros::TransformBroadcaster>(*this);
    }
    
    // IMU subscription
    if (config_.use_imu_rotation_prior) {
        imu_sub_ = create_subscription<sensor_msgs::msg::Imu>(
            "/oak/imu/data", 100,
            std::bind(&OakVisualOdometryNode::imuCallback, this, std::placeholders::_1));
        RCLCPP_INFO(get_logger(), "IMU pre-integration ENABLED");
    }
    
    // RTAB-Map subscriptions
    if (config_.rtabmap_integration && config_.subscribe_initial_pose) {
        initial_pose_sub_ = create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
            "/initialpose", 1,
            std::bind(&OakVisualOdometryNode::initialPoseCallback, this, std::placeholders::_1));
    }
    
    // TF buffer
    tf_buffer_ = std::make_shared<tf2_ros::Buffer>(get_clock());
    tf_listener_ = std::make_shared<tf2_ros::TransformListener>(*tf_buffer_);
    
    // Start processing thread
    running_ = true;
    processing_thread_ = std::thread(&OakVisualOdometryNode::processDepthAIStreams, this);
    
    RCLCPP_INFO(get_logger(), "OAK-D Visual Odometry Node started");
    RCLCPP_INFO(get_logger(), "Config: ORB=%s, IMU=%s, GeoGate=%s, YawFallback=%s",
                config_.enable_orb ? "ON" : "OFF",
                config_.use_imu_rotation_prior ? "ON" : "OFF",
                config_.use_geometric_gating ? "ON" : "OFF",
                config_.enable_yaw_only_fallback ? "ON" : "OFF");
}

OakVisualOdometryNode::~OakVisualOdometryNode() {
    running_ = false;
    if (processing_thread_.joinable()) {
        processing_thread_.join();
    }
}

// ===================== IMU Pre-integration =====================

void OakVisualOdometryNode::imuCallback(const sensor_msgs::msg::Imu::SharedPtr msg) {
    std::lock_guard<std::mutex> lock(imu_mutex_);
    imu_buffer_.push_back({*msg, msg->header.stamp});
    cleanupIMUBuffer();
}

void OakVisualOdometryNode::cleanupIMUBuffer() {
    auto now = this->now();
    while (!imu_buffer_.empty()) {
        auto age = (now - imu_buffer_.front().timestamp).seconds();
        if (age > config_.imu_buffer_duration) {
            imu_buffer_.pop_front();
        } else {
            break;
        }
    }
}

std::optional<Eigen::Quaterniond> OakVisualOdometryNode::computeIMURotationPrior(
    const rclcpp::Time& t_start, const rclcpp::Time& t_end) {
    
    if (!config_.use_imu_rotation_prior) {
        return std::nullopt;
    }
    
    std::lock_guard<std::mutex> lock(imu_mutex_);
    
    if (imu_buffer_.empty()) {
        return std::nullopt;
    }
    
    // Find IMU samples in the interval
    std::vector<IMUData> interval_samples;
    for (const auto& imu_data : imu_buffer_) {
        if (imu_data.timestamp >= t_start && imu_data.timestamp <= t_end) {
            interval_samples.push_back(imu_data);
        }
    }
    
    if (interval_samples.empty()) {
        return std::nullopt;
    }
    
    // Sort by timestamp
    std::sort(interval_samples.begin(), interval_samples.end(),
              [](const IMUData& a, const IMUData& b) {
                  return a.timestamp < b.timestamp;
              });
    
    // Pre-integrate rotation
    Eigen::Quaterniond delta_q = Eigen::Quaterniond::Identity();
    rclcpp::Time prev_time = t_start;
    
    for (size_t i = 0; i < interval_samples.size(); ++i) {
        const auto& imu = interval_samples[i];
        double dt = (imu.timestamp - prev_time).seconds();
        
        if (dt > 0) {
            const auto& gyro = imu.imu.angular_velocity;
            Eigen::Vector3d omega(gyro.x, gyro.y, gyro.z);
            
            double gyro_norm = omega.norm();
            if (gyro_norm > config_.max_gyro_norm) {
                RCLCPP_WARN_THROTTLE(get_logger(), *get_clock(), 1000,
                                   "Gyro norm too high: %.2f rad/s", gyro_norm);
                return std::nullopt;
            }
            
            Eigen::Quaterniond q_step = expSO3(omega, dt);
            delta_q = delta_q * q_step;
        }
        
        prev_time = imu.timestamp;
    }
    
    return delta_q;
}

Eigen::Quaterniond OakVisualOdometryNode::expSO3(const Eigen::Vector3d& omega, double dt) const {
    Eigen::Vector3d w = omega * dt;
    double theta = w.norm();
    
    if (theta < 1e-6) {
        return Eigen::Quaterniond::Identity();
    }
    
    Eigen::Vector3d axis = w.normalized();
    return Eigen::Quaterniond(Eigen::AngleAxisd(theta, axis));
}

// ===================== DepthAI Initialization =====================

bool OakVisualOdometryNode::initializeDepthAI() {
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
        
        RCLCPP_INFO(get_logger(), "Camera intrinsics: fx=%.1f, fy=%.1f, cx=%.1f, cy=%.1f",
                    fx_, fy_, cx_, cy_);
        
        return true;
        
    } catch (const std::exception& e) {
        RCLCPP_ERROR(get_logger(), "DepthAI initialization error: %s", e.what());
        return false;
    }
}

// ===================== Main Processing Loop =====================

void OakVisualOdometryNode::processDepthAIStreams() {
    auto qRect = device_->getOutputQueue("rect_left", 8, false);
    auto qDepth = device_->getOutputQueue("depth", 8, false);
    
    int frame_counter = 0;
    auto last_stats_time = std::chrono::steady_clock::now();
    
    while (running_ && rclcpp::ok()) {
        try {
            auto rect = qRect->tryGet<dai::ImgFrame>();
            auto depth = qDepth->tryGet<dai::ImgFrame>();
            
            if (rect) {
                frame_sync_->addFirst(rect);
            }
            if (depth) {
                frame_sync_->addSecond(depth);
            }
            
            while (frame_sync_->hasNext()) {
                auto pair = frame_sync_->getNext();
                if (pair.valid) {
                    processSynchronizedFrame(pair.first, pair.second, pair.timestamp);
                    frame_counter++;
                }
            }
            
            auto now = std::chrono::steady_clock::now();
            if (std::chrono::duration_cast<std::chrono::seconds>(now - last_stats_time).count() >= 10) {
                auto stats = frame_sync_->getStats();
                RCLCPP_DEBUG(get_logger(), 
                    "Sync: Q1=%zu, Q2=%zu, Matched=%zu, Dropped=%zu, FPS=%d",
                    stats.queue1_size, stats.queue2_size, stats.total_matched,
                    stats.total_dropped, frame_counter / 10);
                
                frame_counter = 0;
                last_stats_time = now;
            }
            
            std::this_thread::sleep_for(std::chrono::microseconds(500));
            
        } catch (const std::exception& e) {
            RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 1000,
                                 "Processing error: %s", e.what());
        }
    }
}

void OakVisualOdometryNode::processSynchronizedFrame(
    std::shared_ptr<dai::ImgFrame> rect_frame,
    std::shared_ptr<dai::ImgFrame> depth_frame,
    std::chrono::time_point<std::chrono::steady_clock> sync_time) {
    
    (void)sync_time;
    auto stamp = this->now();
    
    cv::Mat gray = rect_frame->getCvFrame();
    cv::Mat depth = depth_frame->getCvFrame();
    
    if (config_.enable_clahe && clahe_) {
        clahe_->apply(gray, gray);
    }
    
    static int skip_counter = 0;
    skip_counter++;
    if (skip_counter % config_.vo_skip_frames != 0) {
        return;
    }
    
    TrackingResult result;
    bool tracking_ok = false;
    
    if (has_last_frame_) {
        auto snapshot = getFrameSnapshot();
        
        // Compute IMU prior if available
        std::optional<Eigen::Quaterniond> imu_prior = std::nullopt;
        if (config_.use_imu_rotation_prior) {
            imu_prior = computeIMURotationPrior(snapshot.timestamp, stamp);
        }
        
        // Track with geometric gating
        result = trackFrame(gray, depth, snapshot, imu_prior);
        result.timestamp = stamp;
        
        tracking_ok = result.success;
        
        if (tracking_ok) {
            updatePose(result);
            publishOdometry(stamp);
            consecutive_tracking_failures_ = 0;
        } else {
            consecutive_tracking_failures_++;
            if (consecutive_tracking_failures_ >= config_.lost_tracking_threshold) {
                attemptRecovery();
            }
        }
        
        updateTrackingState(result, snapshot);
    }
    
    // Update last frame if tracking was good or we have no previous frame
    if (tracking_ok || !has_last_frame_) {
        std::vector<cv::KeyPoint> keypoints;
        cv::Mat descriptors;
        
        if (config_.enable_orb) {
            orb_detector_->detectAndCompute(gray, cv::noArray(), keypoints, descriptors);
        }
        
        std::vector<cv::Point3f> points3d;
        std::vector<cv::KeyPoint> valid_keypoints;
        
        if (backprojectPoints(depth, keypoints, points3d, valid_keypoints)) {
            std::lock_guard<std::mutex> lock(state_mutex_);
            
            last_frame_.gray = gray.clone();
            last_frame_.depth = depth.clone();
            last_frame_.keypoints = valid_keypoints;
            last_frame_.descriptors = descriptors.clone();
            last_frame_.points3d = points3d;
            last_frame_.timestamp = stamp;
            
            has_last_frame_ = true;
        }
    }
    
    // Publish tracking status
    std_msgs::msg::Bool tracking_msg;
    tracking_msg.data = tracking_ok;
    tracking_pub_->publish(tracking_msg);
    
    // Publish diagnostics occasionally
    if ((stamp - last_diag_time_).seconds() > 2.0) {
        publishDiagnostics(stamp);
        last_diag_time_ = stamp;
    }
}

// ===================== Geometric Gating =====================

bool OakVisualOdometryNode::geometricGate(
    const std::vector<cv::Point3f>& object_points,
    const std::vector<cv::Point2f>& image_points,
    const std::vector<cv::DMatch>& matches,
    const FrameState& prev_state,
    std::vector<cv::Point3f>& filtered_object_points,
    std::vector<cv::Point2f>& filtered_image_points) {
    
    filtered_object_points.clear();
    filtered_image_points.clear();
    
    if (!config_.use_geometric_gating) {
        filtered_object_points = object_points;
        filtered_image_points = image_points;
        return true;
    }
    
    (void)matches;
    (void)prev_state;
    
    // Depth gating
    for (size_t i = 0; i < object_points.size(); ++i) {
        const auto& pt3d = object_points[i];
        
        // Check depth range
        if (pt3d.z < config_.min_depth || pt3d.z > config_.max_point_depth) {
            continue;
        }
        
        // Check baseline
        double baseline = cv::norm(pt3d);
        if (baseline < config_.min_baseline_ratio) {
            continue;
        }
        
        filtered_object_points.push_back(pt3d);
        filtered_image_points.push_back(image_points[i]);
    }
    
    return filtered_object_points.size() >= (size_t)config_.min_inliers;
}

// ===================== Robust PnP Solver =====================

bool OakVisualOdometryNode::solvePnPRobust(
    const std::vector<cv::Point3f>& object_points,
    const std::vector<cv::Point2f>& image_points,
    const std::optional<Eigen::Quaterniond>& imu_prior,
    cv::Mat& rvec, cv::Mat& tvec,
    std::vector<int>& inliers) const {
    
    bool use_extrinsic_guess = imu_prior.has_value();
    
    if (use_extrinsic_guess) {
        const auto& q = imu_prior.value();
        Eigen::Matrix3d R_imu = q.toRotationMatrix();
        
        cv::Mat R_imu_cv(3, 3, CV_64F);
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j) {
                R_imu_cv.at<double>(i, j) = R_imu(i, j);
            }
        }
        
        cv::Rodrigues(R_imu_cv, rvec);
        tvec = cv::Mat::zeros(3, 1, CV_64F);
    }
    
    bool success = cv::solvePnPRansac(
        object_points, image_points, camera_matrix_, cv::Mat(),
        rvec, tvec, use_extrinsic_guess, 100, 3.0, 0.99, inliers, cv::SOLVEPNP_EPNP);
    
    return success;
}

// ===================== Main Tracking =====================

OakVisualOdometryNode::TrackingResult OakVisualOdometryNode::trackFrame(
    const cv::Mat& curr_gray,
    const cv::Mat& curr_depth,
    FrameState& prev_state,
    const std::optional<Eigen::Quaterniond>& imu_prior) {
    
    TrackingResult result;
    result.timestamp = this->now();
    
    if (prev_state.keypoints.empty() || prev_state.points3d.empty()) {
        return result;
    }
    
    std::vector<cv::KeyPoint> curr_keypoints;
    cv::Mat curr_descriptors;
    orb_detector_->detectAndCompute(curr_gray, cv::noArray(), curr_keypoints, curr_descriptors);
    
    if (curr_keypoints.size() < (size_t)config_.min_features) {
        return result;
    }
    
    std::vector<cv::DMatch> matches;
    orb_matcher_->match(curr_descriptors, prev_state.descriptors, matches);
    
    if (matches.size() < (size_t)config_.min_features) {
        return result;
    }
    
    // Filter matches by distance
    std::vector<cv::DMatch> good_matches;
    double min_dist = 100.0;
    
    for (const auto& match : matches) {
        if (match.distance < min_dist) min_dist = match.distance;
    }
    
    for (const auto& match : matches) {
        if (match.distance <= std::max(2.0 * min_dist, 30.0)) {
            good_matches.push_back(match);
        }
    }
    
    if (good_matches.size() < (size_t)config_.min_features) {
        return result;
    }
    
    result.total_matches = good_matches.size();
    
    // Prepare correspondences
    std::vector<cv::Point3f> object_points;
    std::vector<cv::Point2f> image_points;
    
    for (const auto& match : good_matches) {
        size_t curr_idx = match.queryIdx;
        size_t prev_idx = match.trainIdx;
        
        if (prev_idx < prev_state.points3d.size() && curr_idx < curr_keypoints.size()) {
            object_points.push_back(prev_state.points3d[prev_idx]);
            image_points.push_back(curr_keypoints[curr_idx].pt);
        }
    }
    
    if (object_points.size() < (size_t)config_.min_inliers) {
        return result;
    }
    
    // Apply geometric gating
    std::vector<cv::Point3f> filtered_object_points;
    std::vector<cv::Point2f> filtered_image_points;
    
    if (!geometricGate(object_points, image_points, good_matches, prev_state,
                      filtered_object_points, filtered_image_points)) {
        return result;
    }
    
    // Solve PnP
    cv::Mat rvec, tvec;
    std::vector<int> inliers;
    
    bool success = solvePnPRobust(filtered_object_points, filtered_image_points,
                                 imu_prior, rvec, tvec, inliers);
    
    if (!success || inliers.size() < (size_t)config_.min_inliers) {
        return result;
    }
    
    result.success = true;
    result.rvec = rvec;
    result.tvec = tvec;
    result.inliers = inliers.size();
    result.imu_assisted = imu_prior.has_value();
    
    // Calculate norms
    double tx = tvec.at<double>(0);
    double ty = tvec.at<double>(1);
    double tz = tvec.at<double>(2);
    result.translation_norm = std::sqrt(tx*tx + ty*ty + tz*tz);
    
    double rx = rvec.at<double>(0);
    double ry = rvec.at<double>(1);
    double rz = rvec.at<double>(2);
    result.rotation_norm = std::sqrt(rx*rx + ry*ry + rz*rz);
    
    // Update last translation for direction consistency
    prev_state.last_translation = Eigen::Vector3d(tx, ty, tz);
    
    // Calculate depth valid ratio
    int sampled_pixels = 0;
    int valid_pixels = 0;
    
    for (int y = 0; y < curr_depth.rows; y += 10) {
        for (int x = 0; x < curr_depth.cols; x += 10) {
            sampled_pixels++;
            float depth_val = curr_depth.at<uint16_t>(y, x) / 1000.0f;
            if (depth_val > config_.min_depth && depth_val < config_.max_depth) {
                valid_pixels++;
            }
        }
    }
    
    if (sampled_pixels > 0) {
        result.depth_valid_ratio = static_cast<double>(valid_pixels) / sampled_pixels;
    }
    
    return result;
}

OakVisualOdometryNode::TrackingResult OakVisualOdometryNode::trackWithORB(
    const cv::Mat& curr_gray,
    const cv::Mat& curr_depth,
    const FrameState& prev_state) {
    
    FrameState mutable_state = prev_state;
    return trackFrame(curr_gray, curr_depth, mutable_state, std::nullopt);
}

OakVisualOdometryNode::TrackingResult OakVisualOdometryNode::relocalizeWithSuperPoint(
    const cv::Mat& curr_gray) {
    
    (void)curr_gray;
    TrackingResult result;
    // TODO: Implement SuperPoint relocalization
    return result;
}

OakVisualOdometryNode::FrameState OakVisualOdometryNode::getFrameSnapshot() const {
    std::lock_guard<std::mutex> lock(state_mutex_);
    FrameState snapshot;
    snapshot.gray = last_frame_.gray.clone();
    snapshot.keypoints = last_frame_.keypoints;
    snapshot.descriptors = last_frame_.descriptors.clone();
    snapshot.points3d = last_frame_.points3d;
    snapshot.timestamp = last_frame_.timestamp;
    snapshot.last_translation = last_frame_.last_translation;
    return snapshot;
}

bool OakVisualOdometryNode::backprojectPoints(
    const cv::Mat& depth,
    const std::vector<cv::KeyPoint>& keypoints,
    std::vector<cv::Point3f>& points3d,
    std::vector<cv::KeyPoint>& valid_keypoints) {
    
    points3d.clear();
    valid_keypoints.clear();
    
    for (const auto& kp : keypoints) {
        int u = static_cast<int>(kp.pt.x);
        int v = static_cast<int>(kp.pt.y);
        
        if (u >= 0 && u < depth.cols && v >= 0 && v < depth.rows) {
            float depth_val = depth.at<uint16_t>(v, u) / 1000.0f;
            
            if (depth_val > config_.min_depth && depth_val < config_.max_depth) {
                float X = (u - cx_) * depth_val / fx_;
                float Y = (v - cy_) * depth_val / fy_;
                float Z = depth_val;
                
                points3d.emplace_back(X, Y, Z);
                valid_keypoints.push_back(kp);
            }
        }
    }
    
    return points3d.size() >= (size_t)config_.min_features;
}

// ===================== State Management =====================

void OakVisualOdometryNode::MotionStats::update(double t_norm, double r_norm) {
    translation_norms.push_back(t_norm);
    rotation_norms.push_back(r_norm);
    
    if (translation_norms.size() > window_size) {
        translation_norms.pop_front();
        rotation_norms.pop_front();
    }
}

double OakVisualOdometryNode::MotionStats::getAvgTranslation() const {
    if (translation_norms.empty()) return 0.0;
    double sum = 0.0;
    for (auto v : translation_norms) sum += v;
    return sum / translation_norms.size();
}

double OakVisualOdometryNode::MotionStats::getAvgRotation() const {
    if (rotation_norms.empty()) return 0.0;
    double sum = 0.0;
    for (auto v : rotation_norms) sum += v;
    return sum / rotation_norms.size();
}

double OakVisualOdometryNode::computeDirectionConsistency(
    const Eigen::Vector3d& prev_t, const Eigen::Vector3d& curr_t) const {
    
    if (prev_t.norm() < 1e-6 || curr_t.norm() < 1e-6) {
        return 1.0;
    }
    
    double dot = prev_t.normalized().dot(curr_t.normalized());
    return (dot + 1.0) / 2.0;
}

bool OakVisualOdometryNode::validateMotion(const TrackingResult& result, 
                                          const FrameState& prev_state) {
    if (!result.success) {
        return false;
    }
    
    if (result.translation_norm < 0.001) {
        return false;
    }
    
    double rotation_deg = result.rotation_norm * 180.0 / CV_PI;
    if (rotation_deg > 30.0) {
        RCLCPP_WARN(get_logger(), "Rejected: rotation too large (%.1f deg)", rotation_deg);
        return false;
    }
    
    // Direction consistency
    if (!prev_state.last_translation.isZero()) {
        Eigen::Vector3d current_t(result.tvec.at<double>(0),
                                  result.tvec.at<double>(1),
                                  result.tvec.at<double>(2));
        
        double consistency = computeDirectionConsistency(prev_state.last_translation, current_t);
        
        if (consistency < 0.3) {
            RCLCPP_WARN(get_logger(), "Direction inconsistency: %.2f", consistency);
            return false;
        }
    }
    
    return true;
}

void OakVisualOdometryNode::updateTrackingState(const TrackingResult& result,
                                               const FrameState& prev_state) {
    const int GOOD_THRESHOLD = config_.min_inliers * 3;
    const int WEAK_THRESHOLD = config_.min_inliers;
    
    if (!result.success) {
        tracking_state_ = TrackingState::TRACKING_LOST;
        consecutive_tracking_failures_++;
        return;
    }
    
    // Yaw-only mode check
    bool should_use_yaw_only = config_.enable_yaw_only_fallback &&
                               result.translation_norm < config_.yaw_only_translation_threshold &&
                               result.inliers >= WEAK_THRESHOLD &&
                               result.inliers < GOOD_THRESHOLD;
    
    if (should_use_yaw_only) {
        tracking_state_ = TrackingState::YAW_ONLY_MODE;
        RCLCPP_DEBUG(get_logger(), "Entering YAW_ONLY_MODE (translation: %.3f m)", 
                    result.translation_norm);
    } else if (result.inliers >= GOOD_THRESHOLD) {
        tracking_state_ = TrackingState::TRACKING_GOOD;
        last_good_tracking_time_ = this->now();
        consecutive_tracking_failures_ = 0;
    } else if (result.inliers >= WEAK_THRESHOLD) {
        tracking_state_ = TrackingState::TRACKING_WEAK;
        consecutive_tracking_failures_ = 0;
    } else {
        tracking_state_ = TrackingState::TRACKING_LOST;
        consecutive_tracking_failures_++;
    }
    
    // Store translation history
    if (result.success) {
        translation_history_.push_back(Eigen::Vector3d(
            result.tvec.at<double>(0),
            result.tvec.at<double>(1),
            result.tvec.at<double>(2)));
        
        if (translation_history_.size() > 10) {
            translation_history_.pop_front();
        }
    }
    
    (void)prev_state;
}

Eigen::Vector3d OakVisualOdometryNode::applyTranslationFilter(const Eigen::Vector3d& raw_translation) {
    if (!filter_initialized_) {
        filtered_translation_ = raw_translation;
        filter_initialized_ = true;
        return raw_translation;
    }
    
    filtered_translation_ = config_.filter_alpha * raw_translation + 
                           (1.0 - config_.filter_alpha) * filtered_translation_;
    
    return filtered_translation_;
}

void OakVisualOdometryNode::updatePose(const TrackingResult& result) {
    FrameState snapshot = getFrameSnapshot();
    
    if (!validateMotion(result, snapshot)) {
        return;
    }
    
    cv::Mat R_camera, t_camera;
    transform_handler_->cameraMotionToBase(result.rvec, result.tvec, R_camera, t_camera);
    
    Eigen::Matrix3d R;
    Eigen::Vector3d t;
    
    for (int i = 0; i < 3; ++i) {
        for (int j = 0; j < 3; ++j) {
            R(i, j) = R_camera.at<double>(i, j);
        }
        t(i) = t_camera.at<double>(i);
    }
    
    // Yaw-only mode
    if (tracking_state_ == TrackingState::YAW_ONLY_MODE) {
        double yaw = std::atan2(R(1, 0), R(0, 0));
        
        Eigen::Matrix3d R_yaw_only;
        R_yaw_only << std::cos(yaw), -std::sin(yaw), 0,
                      std::sin(yaw),  std::cos(yaw), 0,
                      0,             0,             1;
        
        Eigen::Vector3d filtered_t = applyTranslationFilter(Eigen::Vector3d::Zero());
        
        Eigen::Isometry3d delta = Eigen::Isometry3d::Identity();
        delta.linear() = R_yaw_only;
        delta.translation() = filtered_t;
        
        std::lock_guard<std::mutex> lock(state_mutex_);
        pose_ = pose_ * delta;
    } else {
        Eigen::Vector3d filtered_t = applyTranslationFilter(t);
        
        Eigen::Isometry3d delta = Eigen::Isometry3d::Identity();
        delta.linear() = R;
        delta.translation() = filtered_t;
        
        std::lock_guard<std::mutex> lock(state_mutex_);
        pose_ = pose_ * delta;
    }
    
    // Direction consistency penalty for covariance
    double direction_penalty = 1.0;
    if (translation_history_.size() >= 2) {
        double consistency = computeDirectionConsistency(
            translation_history_[translation_history_.size() - 2],
            translation_history_.back());
        
        if (consistency < 0.5) {
            direction_penalty = 2.0 / consistency;
        }
    }
    
    covariance_estimator_->update(
        result.inliers,
        result.total_matches,
        result.translation_norm,
        result.rotation_norm,
        result.depth_valid_ratio,
        tracking_state_ == TrackingState::TRACKING_LOST,
        result.imu_assisted,
        direction_penalty
    );
}

// ===================== Publishing =====================

void OakVisualOdometryNode::publishOdometry(const rclcpp::Time& stamp) {
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
    
    // Apply external offset if available
    if (external_pose_initialized_) {
        Eigen::Isometry3d global_pose = external_pose_offset_ * pose_;
        
        odom_msg.pose.pose.position.x = global_pose.translation().x();
        odom_msg.pose.pose.position.y = global_pose.translation().y();
        odom_msg.pose.pose.position.z = global_pose.translation().z();
        
        Eigen::Quaterniond global_q(global_pose.rotation());
        odom_msg.pose.pose.orientation.x = global_q.x();
        odom_msg.pose.pose.orientation.y = global_q.y();
        odom_msg.pose.pose.orientation.z = global_q.z();
        odom_msg.pose.pose.orientation.w = global_q.w();
    } else {
        odom_msg.pose.pose.position.x = position.x();
        odom_msg.pose.pose.position.y = position.y();
        odom_msg.pose.pose.position.z = position.z();
        
        odom_msg.pose.pose.orientation.x = orientation.x();
        odom_msg.pose.pose.orientation.y = orientation.y();
        odom_msg.pose.pose.orientation.z = orientation.z();
        odom_msg.pose.pose.orientation.w = orientation.w();
    }
    
    // Adaptive covariance with yaw-only mode
    std::array<double, 36> cov;
    
    if (tracking_state_ == TrackingState::YAW_ONLY_MODE) {
        covariance_estimator_->fillYawOnlyCovariance(cov, config_.yaw_only_covariance_scale);
    } else {
        covariance_estimator_->fillCovarianceMatrix(cov);
    }
    
    std::copy(cov.begin(), cov.end(), odom_msg.pose.covariance.begin());
    
    for (size_t i = 0; i < 36; ++i) {
        odom_msg.twist.covariance[i] = -1;
    }
    
    odom_pub_->publish(odom_msg);
    
    if (config_.publish_tf && tf_broadcaster_) {
        geometry_msgs::msg::TransformStamped tf_msg;
        tf_msg.header = odom_msg.header;
        tf_msg.child_frame_id = config_.base_frame;
        
        tf_msg.transform.translation.x = odom_msg.pose.pose.position.x;
        tf_msg.transform.translation.y = odom_msg.pose.pose.position.y;
        tf_msg.transform.translation.z = odom_msg.pose.pose.position.z;
        
        tf_msg.transform.rotation = odom_msg.pose.pose.orientation;
        
        tf_broadcaster_->sendTransform(tf_msg);
    }
}

void OakVisualOdometryNode::publishDiagnostics(const rclcpp::Time& stamp) {
    diagnostic_msgs::msg::DiagnosticArray diag_msg;
    diag_msg.header.stamp = stamp;
    
    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "Visual Odometry";
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
        case TrackingState::YAW_ONLY_MODE:
            status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
            status.message = "Yaw-Only Mode";
            break;
        case TrackingState::TRACKING_LOST:
            status.level = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
            status.message = "Tracking Lost";
            break;
        case TrackingState::RELOCALIZING:
            status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
            status.message = "Relocalizing";
            break;
    }
    
    diagnostic_msgs::msg::KeyValue kv;
    
    kv.key = "State";
    kv.value = trackingStateToString(tracking_state_);
    status.values.push_back(kv);
    
    kv.key = "Quality";
    std::ostringstream oss;
    oss << static_cast<int>(covariance_estimator_->getQuality() * 100) << "%";
    kv.value = oss.str();
    status.values.push_back(kv);
    
    kv.key = "IMU_Enabled";
    kv.value = config_.use_imu_rotation_prior ? "true" : "false";
    status.values.push_back(kv);
    
    kv.key = "Geo_Gating";
    kv.value = config_.use_geometric_gating ? "true" : "false";
    status.values.push_back(kv);
    
    diag_msg.status.push_back(status);
    diag_pub_->publish(diag_msg);
}

void OakVisualOdometryNode::attemptRecovery() {
    RCLCPP_WARN(get_logger(), "Attempting recovery after tracking loss");
    
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        has_last_frame_ = false;
    }
    
    tracking_state_ = TrackingState::RELOCALIZING;
    consecutive_tracking_failures_ = 0;
    
    covariance_estimator_->temporaryIncrease(10.0, 3.0);
}

void OakVisualOdometryNode::initialPoseCallback(
    const geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr msg) {
    
    if (msg->header.frame_id != "map") {
        RCLCPP_WARN(get_logger(), "Initial pose not in map frame, ignoring");
        return;
    }
    
    Eigen::Vector3d position(
        msg->pose.pose.position.x,
        msg->pose.pose.position.y,
        msg->pose.pose.position.z
    );
    
    Eigen::Quaterniond orientation(
        msg->pose.pose.orientation.w,
        msg->pose.pose.orientation.x,
        msg->pose.pose.orientation.y,
        msg->pose.pose.orientation.z
    );
    
    Eigen::Isometry3d map_to_base = Eigen::Isometry3d::Identity();
    map_to_base.translation() = position;
    map_to_base.linear() = orientation.toRotationMatrix();
    
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        external_pose_offset_ = map_to_base * pose_.inverse();
        external_pose_initialized_ = true;
    }
    
    RCLCPP_INFO(get_logger(), "Initial pose set from RTAB-Map");
}

std::string OakVisualOdometryNode::trackingStateToString(TrackingState state) const {
    switch (state) {
        case TrackingState::UNINITIALIZED: return "UNINITIALIZED";
        case TrackingState::TRACKING_GOOD: return "TRACKING_GOOD";
        case TrackingState::TRACKING_WEAK: return "TRACKING_WEAK";
        case TrackingState::YAW_ONLY_MODE: return "YAW_ONLY_MODE";
        case TrackingState::TRACKING_LOST: return "TRACKING_LOST";
        case TrackingState::RELOCALIZING: return "RELOCALIZING";
        default: return "UNKNOWN";
    }
}

// ===================== Main Function =====================

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    
    rclcpp::NodeOptions options;
    auto node = std::make_shared<OakVisualOdometryNode>(options);
    
    rclcpp::spin(node);
    rclcpp::shutdown();
    
    return 0;
}
