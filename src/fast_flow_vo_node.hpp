#pragma once

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <std_msgs/msg/bool.hpp>

#include <depthai/depthai.hpp>
#include <opencv2/opencv.hpp>
#include <opencv2/video/tracking.hpp>
#include <Eigen/Core>
#include <Eigen/Geometry>

#include <mutex>
#include <atomic>
#include <deque>
#include <memory>
#include <thread>

/**
 * @brief FAST + Optical Flow Visual Odometry
 * 
 * Philosophy: "Preferiamo una stima mediocre ma continua
 *              a una stima eccellente che ogni tanto impazzisce."
 * 
 * NO SuperPoint, NO neural networks, NO descriptor matching
 * NO relocalization, NO keyframe database, NO loop closure
 */
class FastFlowVONode : public rclcpp::Node {
public:
    explicit FastFlowVONode(const rclcpp::NodeOptions& options);
    ~FastFlowVONode();

private:
    // ===================== Types =====================
    enum class TrackingState {
        UNINITIALIZED,
        TRACKING_GOOD,
        TRACKING_WEAK,
        TRACKING_LOST
    };

    struct FrameData {
        cv::Mat gray;
        cv::Mat depth;
        std::vector<cv::Point2f> points;
        rclcpp::Time timestamp;
    };

    struct TrackingResult {
        bool success = false;
        cv::Mat rvec;
        cv::Mat tvec;
        int inliers = 0;
        int tracked_points = 0;
        double translation_norm = 0.0;
        double rotation_norm = 0.0;
        double depth_valid_ratio = 1.0;
    };

    struct MotionStats {
        std::deque<double> translation_norms;
        std::deque<double> rotation_norms;
        size_t window_size = 20;
        
        void update(double t_norm, double r_norm);
        double getAvgTranslation() const;
        double getAvgRotation() const;
    };

    // ===================== Configuration =====================
    struct Config {
        // Frames
        std::string odom_frame = "odom";
        std::string base_frame = "base_link";
        std::string camera_frame = "oak_left_camera_optical_frame";
        
        // FAST Detection
        int fast_threshold = 15;
        int max_features = 400;
        int grid_rows = 6;
        int grid_cols = 8;
        
        // KLT Tracking
        int klt_win_size = 21;
        int klt_max_level = 3;
        float klt_max_error = 12.0f;
        float fb_threshold = 1.0f;  // Forward-backward check
        
        // Depth
        double min_depth = 0.3;
        double max_depth = 8.0;
        double depth_fps = 30.0;
        
        // PnP
        int min_points = 20;
        int min_inliers = 15;
        double reproj_error = 3.0;
        
        // Motion Validation
        double max_translation_per_frame = 0.5;  // meters
        double max_rotation_per_frame = 0.52;    // radians (~30°)
        double min_translation = 0.001;           // 1mm noise floor
        
        // EMA Filter
        double ema_alpha = 0.3;
        
        // State
        int lost_threshold = 5;  // consecutive failures
        int weak_inlier_threshold = 25;
        int good_inlier_threshold = 40;
        
        // Performance
        int skip_frames = 1;
    };
    
    // ===================== Pipeline Methods =====================
    
    // 1. FAST Detection with grid distribution
    void detectFAST(const cv::Mat& gray, std::vector<cv::Point2f>& points);
    
    // 2. KLT Optical Flow tracking
    bool trackKLT(const cv::Mat& prev_gray, const cv::Mat& curr_gray,
                  std::vector<cv::Point2f>& prev_pts, std::vector<cv::Point2f>& curr_pts);
    
    // 3. Depth association - backproject to 3D
    bool associateDepth(const cv::Mat& depth,
                       const std::vector<cv::Point2f>& prev_pts,
                       const std::vector<cv::Point2f>& curr_pts,
                       std::vector<cv::Point3f>& obj_pts,
                       std::vector<cv::Point2f>& img_pts);
    
    // 4. PnP motion estimation
    TrackingResult estimatePnP(const std::vector<cv::Point3f>& obj_pts,
                               const std::vector<cv::Point2f>& img_pts);
    
    // 5. Motion validation
    bool validateMotion(const TrackingResult& result);
    
    // 6. EMA filter on translation
    Eigen::Vector3d filterTranslation(const Eigen::Vector3d& raw_t);
    
    // ===================== Core Methods =====================
    bool initializeDepthAI();
    void processLoop();
    void processFrame(const cv::Mat& gray, const cv::Mat& depth, const rclcpp::Time& stamp);
    
    void updateState(const TrackingResult& result);
    void updatePose(const TrackingResult& result);
    void publishOdometry(const rclcpp::Time& stamp);
    void publishDiagnostics(const rclcpp::Time& stamp);
    void publishImages(const cv::Mat& gray, const cv::Mat& depth, const rclcpp::Time& stamp);
    
    // ===================== State =====================
    Config config_;
    
    // DepthAI
    std::shared_ptr<dai::Pipeline> pipeline_;
    std::unique_ptr<dai::Device> device_;
    
    // OpenCV
    cv::Ptr<cv::FastFeatureDetector> fast_detector_;
    
    // State (protected by mutex)
    mutable std::mutex state_mutex_;
    FrameData prev_frame_;
    bool has_prev_frame_ = false;
    
    Eigen::Isometry3d pose_ = Eigen::Isometry3d::Identity();
    Eigen::Vector3d filtered_translation_ = Eigen::Vector3d::Zero();
    bool filter_initialized_ = false;
    
    // Tracking State
    TrackingState tracking_state_ = TrackingState::UNINITIALIZED;
    int consecutive_failures_ = 0;
    rclcpp::Time last_good_tracking_time_;
    MotionStats motion_stats_;
    
    // Camera Transform (base_link -> camera)
    Eigen::Isometry3d T_base_camera_ = Eigen::Isometry3d::Identity();
    bool transform_initialized_ = false;
    
    // Covariance
    double current_covariance_scale_ = 1.0;
    
    // ROS Publishers
    rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
    rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diag_pub_;
    rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr tracking_pub_;
    
    // Image publishers for RTAB-Map
    rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr rgb_pub_;
    rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr depth_pub_;
    rclcpp::Publisher<sensor_msgs::msg::CameraInfo>::SharedPtr camera_info_pub_;
    
    // Threading
    std::thread processing_thread_;
    std::atomic<bool> running_{false};
    
    // Camera intrinsics
    cv::Mat camera_matrix_;
    double fx_ = 0.0, fy_ = 0.0, cx_ = 0.0, cy_ = 0.0;
    
    // Performance
    rclcpp::Time last_diag_time_;
    int processed_frames_ = 0;
    
    // Helper
    std::string stateToString(TrackingState state) const;
    void computeCameraTransform();
};
