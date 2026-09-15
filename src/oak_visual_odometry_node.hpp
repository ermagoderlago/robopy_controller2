#pragma once

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <std_msgs/msg/bool.hpp>
#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>

#include <tf2_ros/transform_broadcaster.h>
#include <tf2_ros/buffer.h>
#include <tf2_ros/transform_listener.h>

#include <depthai/depthai.hpp>
#include <opencv2/opencv.hpp>
#include <Eigen/Core>
#include <Eigen/Geometry>

#include <mutex>
#include <atomic>
#include <deque>
#include <memory>
#include <thread>
#include <optional>

// Forward declarations
class CameraTransformHandler;
class AdaptiveCovarianceEstimator;

/**
 * @brief Production visual odometry with IMU pre-integration and geometric gating
 */
class OakVisualOdometryNode : public rclcpp::Node {
public:
    explicit OakVisualOdometryNode(const rclcpp::NodeOptions& options);
    ~OakVisualOdometryNode();

private:
    // ===================== Types =====================
    enum class TrackingState {
        UNINITIALIZED,
        TRACKING_GOOD,
        TRACKING_WEAK,
        TRACKING_LOST,
        RELOCALIZING,
        YAW_ONLY_MODE  // New state for weak tracking
    };

    struct FrameState {
        cv::Mat gray;
        cv::Mat depth;
        std::vector<cv::KeyPoint> keypoints;
        cv::Mat descriptors;
        std::vector<cv::Point3f> points3d;
        rclcpp::Time timestamp;
        Eigen::Vector3d last_translation = Eigen::Vector3d::Zero();
    };

    struct TrackingResult {
        bool success = false;
        cv::Mat rvec;
        cv::Mat tvec;
        int inliers = 0;
        int total_matches = 0;
        double translation_norm = 0.0;
        double rotation_norm = 0.0;
        double depth_valid_ratio = 1.0;
        bool imu_assisted = false;
        rclcpp::Time timestamp;
    };

    struct IMUData {
        sensor_msgs::msg::Imu imu;
        rclcpp::Time timestamp;
    };

    struct MotionStats {
        std::deque<double> translation_norms;
        std::deque<double> rotation_norms;
        size_t window_size = 10;
        
        void update(double t_norm, double r_norm);
        double getAvgTranslation() const;
        double getAvgRotation() const;
    };

    // ===================== Configuration =====================
    struct Config {
        // General
        bool publish_tf = false;
        std::string odom_frame = "odom";
        std::string base_frame = "base_link";
        std::string camera_frame = "camera_optical_frame";
        
        // Algorithms
        bool enable_orb = true;
        bool enable_superpoint = false;
        bool use_superpoint_for_relocalization = true;
        bool use_imu_rotation_prior = true;
        bool use_geometric_gating = true;
        bool enable_yaw_only_fallback = true;
        
        // ORB Parameters
        int max_orb_features = 500;
        int min_features = 30;
        int min_inliers = 12;
        int lost_tracking_threshold = 10;
        
        // IMU Parameters
        double imu_buffer_duration = 0.2;  // seconds
        double max_gyro_norm = 5.0;  // rad/s
        
        // Geometric Gating
        double max_point_depth = 10.0;
        double min_baseline_ratio = 0.01;
        double max_epipolar_error = 2.0;
        
        // Yaw-only Mode
        double yaw_only_translation_threshold = 0.02;
        double yaw_only_covariance_scale = 10.0;
        
        // SuperPoint Parameters
        std::string superpoint_blob_path;
        int superpoint_relocalization_threshold = 30;
        
        // Depth Processing
        double min_depth = 0.3;
        double max_depth = 8.0;
        double depth_fps = 30.0;
        
        // Performance
        int vo_skip_frames = 1;
        bool enable_clahe = false;
        
        // Output
        double filter_alpha = 0.25;
        
        // RTAB-Map Integration
        bool rtabmap_integration = true;
        bool subscribe_initial_pose = true;
    };
    
    // ===================== Core Methods =====================
    bool initializeDepthAI();
    void processDepthAIStreams();
    void processSynchronizedFrame(
        std::shared_ptr<dai::ImgFrame> rect_frame,
        std::shared_ptr<dai::ImgFrame> depth_frame,
        std::chrono::time_point<std::chrono::steady_clock> sync_time);
    
    // IMU Handling with pre-integration
    void imuCallback(const sensor_msgs::msg::Imu::SharedPtr msg);
    std::optional<Eigen::Quaterniond> computeIMURotationPrior(
        const rclcpp::Time& t_start, const rclcpp::Time& t_end);
    void cleanupIMUBuffer();
    Eigen::Quaterniond expSO3(const Eigen::Vector3d& omega, double dt) const;
    
    // Tracking with geometric gating
    FrameState getFrameSnapshot() const;
    TrackingResult trackFrame(const cv::Mat& curr_gray, const cv::Mat& curr_depth,
                             FrameState& prev_state, 
                             const std::optional<Eigen::Quaterniond>& imu_prior);
    TrackingResult trackWithORB(const cv::Mat& curr_gray, const cv::Mat& curr_depth,
                               const FrameState& prev_state);
    TrackingResult relocalizeWithSuperPoint(const cv::Mat& curr_gray);
    
    bool geometricGate(const std::vector<cv::Point3f>& object_points,
                      const std::vector<cv::Point2f>& image_points,
                      const std::vector<cv::DMatch>& matches,
                      const FrameState& prev_state,
                      std::vector<cv::Point3f>& filtered_object_points,
                      std::vector<cv::Point2f>& filtered_image_points);
    
    bool solvePnPRobust(const std::vector<cv::Point3f>& object_points,
                       const std::vector<cv::Point2f>& image_points,
                       const std::optional<Eigen::Quaterniond>& imu_prior,
                       cv::Mat& rvec, cv::Mat& tvec,
                       std::vector<int>& inliers) const;
    
    bool backprojectPoints(const cv::Mat& depth, const std::vector<cv::KeyPoint>& keypoints,
                          std::vector<cv::Point3f>& points3d,
                          std::vector<cv::KeyPoint>& valid_keypoints);
    
    // State Management
    bool validateMotion(const TrackingResult& result, const FrameState& prev_state);
    void updateTrackingState(const TrackingResult& result, const FrameState& prev_state);
    Eigen::Vector3d applyTranslationFilter(const Eigen::Vector3d& raw_translation);
    void updatePose(const TrackingResult& result);
    void publishOdometry(const rclcpp::Time& stamp);
    void publishDiagnostics(const rclcpp::Time& stamp);
    void attemptRecovery();
    double computeDirectionConsistency(const Eigen::Vector3d& prev_t, 
                                      const Eigen::Vector3d& curr_t) const;
    
    // RTAB-Map Integration
    void initialPoseCallback(const geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr msg);
    
    // ===================== State =====================
    Config config_;
    
    // DepthAI
    std::shared_ptr<dai::Pipeline> pipeline_;
    std::unique_ptr<dai::Device> device_;
    
    // Synchronization (forward declared, defined in cpp)
    template<typename T1, typename T2>
    class FrameSynchronizer;
    std::unique_ptr<FrameSynchronizer<dai::ImgFrame, dai::ImgFrame>> frame_sync_;
    
    // Transforms
    std::unique_ptr<CameraTransformHandler> transform_handler_;
    
    // State (protected by mutex)
    mutable std::mutex state_mutex_;
    FrameState last_frame_;
    bool has_last_frame_ = false;
    
    Eigen::Isometry3d pose_ = Eigen::Isometry3d::Identity();
    Eigen::Vector3d filtered_translation_ = Eigen::Vector3d::Zero();
    bool filter_initialized_ = false;
    
    // IMU Buffer for pre-integration
    mutable std::mutex imu_mutex_;
    std::deque<IMUData> imu_buffer_;
    
    // Direction history for consistency check
    std::deque<Eigen::Vector3d> translation_history_;
    
    // RTAB-Map Integration
    bool external_pose_initialized_ = false;
    Eigen::Isometry3d external_pose_offset_ = Eigen::Isometry3d::Identity();
    
    // Tracking State
    TrackingState tracking_state_ = TrackingState::UNINITIALIZED;
    int consecutive_tracking_failures_ = 0;
    rclcpp::Time last_good_tracking_time_;
    MotionStats motion_stats_;
    
    // Features
    cv::Ptr<cv::ORB> orb_detector_;
    cv::Ptr<cv::DescriptorMatcher> orb_matcher_;
    cv::Ptr<cv::DescriptorMatcher> sp_matcher_;
    cv::Ptr<cv::CLAHE> clahe_;
    
    // Covariance estimation
    std::unique_ptr<AdaptiveCovarianceEstimator> covariance_estimator_;
    
    // ROS
    rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
    rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diag_pub_;
    rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr tracking_pub_;
    
    // IMU subscription
    rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_sub_;
    
    // RTAB-Map subscriptions
    rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr initial_pose_sub_;
    
    std::unique_ptr<tf2_ros::TransformBroadcaster> tf_broadcaster_;
    std::shared_ptr<tf2_ros::Buffer> tf_buffer_;
    std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
    
    // Threading
    std::thread processing_thread_;
    std::atomic<bool> running_{false};
    
    // Camera intrinsics
    cv::Mat camera_matrix_;
    double fx_ = 0.0, fy_ = 0.0, cx_ = 0.0, cy_ = 0.0;
    
    // Performance monitoring
    rclcpp::Time last_diag_time_;
    int processed_frames_ = 0;
    
    // Helper
    std::string trackingStateToString(TrackingState state) const;
};
