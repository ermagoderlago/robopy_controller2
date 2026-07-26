#pragma once

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <sensor_msgs/msg/compressed_image.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <geometry_msgs/msg/transform_stamped.hpp>
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <std_msgs/msg/bool.hpp>
#include <tf2_ros/transform_broadcaster.h>

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
        std::vector<int> inlier_indices; // Indices of img_pts that are inliers
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
        std::string camera_frame = "camera_optical_frame";
        bool publish_tf = false;  // TF publishing (usually RTAB-Map handles this)
        
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
        double camera_fps = 30.0;  // Stereo camera FPS
        bool enable_depth_filter = true;  // Enable/disable depth range filtering
        
        // LaserScan Offset
        int scan_height = 3;
        int scan_y_offset = 0;
        
        // Floor Reflection Filter (with camera pitch compensation)
        bool enable_floor_filter = true;     // Enable/disable underground point rejection
        double camera_height = 0.08;         // Camera height from floor (meters)
        double camera_pitch = 0.0;           // Camera pitch angle (radians, positive = tilted up)
        double floor_z_threshold = 0.03;     // Punti sotto 3cm in base_link = scartati (era -0.02)
        
        // PnP
        int min_points = 20;
        int min_inliers = 15;
        double reproj_error = 3.0;
        
        // [CORREZIONE 4] Filtro parallasse minima (pixel)
        // 0.5px = elimina noise sub-pixel, ma mantiene abbastanza punti per PnP stabile
        float min_parallax_px = 0.5f;
        
        // Motion Validation
        double max_translation_per_frame = 0.5;  // meters
        double max_rotation_per_frame = 0.52;    // radians (~30°)
        double min_translation = 0.001;           // 1mm noise floor
        
        // EMA Filter
        double ema_alpha = 0.3;
        
        // State (Lowered thresholds after introducing min_parallax_px=0.5)
        int lost_threshold = 5;  // consecutive failures
        int weak_inlier_threshold = 15;
        int good_inlier_threshold = 25;
        int critical_inlier_threshold = 8;
        
        // Performance
        int skip_frames = 1;
        
        // Debug
        bool publish_debug = false;
        
        // YOLO
        bool enable_yolo = true;
        std::string yolo_blob_path = "";
        float yolo_conf_threshold = 0.5f;
        
        // Motion Gate (prevents drift when stationary)
        bool enable_motion_gate = true;
        double imu_gyro_threshold = 0.02;    // rad/s - below this = no rotation
        double imu_accel_threshold = 0.15;   // m/s^2 - deviation from gravity
        double cmd_vel_timeout = 0.5;        // seconds - cmd_vel considered stale after this
        
        // [OPT 1] Max dt tra rect e depth frame (ms) — skip se desincronizzati
        double max_frame_dt_ms = 33.0;
    };
    
    // TF Broadcaster (optional)
    std::unique_ptr<tf2_ros::TransformBroadcaster> tf_broadcaster_;
    
    // ===================== Pipeline Methods =====================
    
    // 1. FAST Detection with grid distribution
    void detectFAST(const cv::Mat& gray, std::vector<cv::Point2f>& points);
    
    // 2. KLT Optical Flow tracking (with pre-built pyramids)
    // [OPT 3] Accetta piramidi pre-calcolate per evitare doppia computazione
    bool trackKLT(const std::vector<cv::Mat>& prev_pyr,
                  const std::vector<cv::Mat>& curr_pyr,
                  std::vector<cv::Point2f>& prev_pts, std::vector<cv::Point2f>& curr_pts);
    
    // 3. Depth association - backproject to 3D
    bool associateDepth(const cv::Mat& depth,
                        const std::vector<cv::Point2f>& prev_pts,
                        const std::vector<cv::Point2f>& curr_pts,
                        std::vector<cv::Point3f>& obj_pts,
                        std::vector<cv::Point2f>& img_pts,
                        std::vector<cv::Point2f>& valid_prev_pts);
    
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
    // [CORREZIONE 3] updatePose riceve result per il check inlier nel motion gate
    void updatePose(const TrackingResult& result);
    void publishOdometry(const rclcpp::Time& stamp);
    void publishGuess(const rclcpp::Time& stamp);  // Guess for RTAB-Map
    void publishDiagnostics(const rclcpp::Time& stamp);
    void publishImages(const cv::Mat& gray, const cv::Mat& depth, const rclcpp::Time& stamp);
    void publishDebugView(const cv::Mat& gray, 
                         const std::vector<cv::Point2f>& prev_pts,
                         const std::vector<cv::Point2f>& curr_pts,
                         const std::vector<int>& inliers,
                         const rclcpp::Time& stamp);
    void publishDepthPreview(const cv::Mat& depth, const rclcpp::Time& stamp);
    
    // YOLO
    void processYolo(const std::shared_ptr<dai::ImgDetections>& detections, cv::Mat& display_frame);
    std::string getItalianLabel(int class_id);
    
    // ===================== State =====================
    Config config_;
    
    // DepthAI
    std::shared_ptr<dai::Pipeline> pipeline_;
    std::unique_ptr<dai::Device> device_;
    
    // OpenCV
    cv::Ptr<cv::FastFeatureDetector> fast_detector_;
    
    // State (protected by mutexes)
    mutable std::mutex state_mutex_;  // For pose and frame data
    mutable std::mutex time_mutex_;   // For rclcpp::Time variables
    FrameData prev_frame_;
    std::atomic<bool> has_prev_frame_{false};
    
    Eigen::Isometry3d pose_ = Eigen::Isometry3d::Identity();
    Eigen::Vector3d filtered_translation_ = Eigen::Vector3d::Zero();
    std::atomic<bool> filter_initialized_{false};
    
    // Tracking State (thread-safe)
    std::atomic<int> tracking_state_{static_cast<int>(TrackingState::UNINITIALIZED)};
    std::atomic<int> consecutive_failures_{0};
    rclcpp::Time last_good_tracking_time_;  // Protected by time_mutex_
    MotionStats motion_stats_;
    
    // Camera Transform (base_link -> camera)
    Eigen::Isometry3d T_base_camera_ = Eigen::Isometry3d::Identity();
    bool transform_initialized_ = false;
    
    // Covariance (thread-safe)
    std::atomic<double> current_covariance_scale_{1.0};
    
    // Velocity tracking (for twist-only odometry)
    rclcpp::Time last_velocity_time_;
    Eigen::Vector3d last_delta_translation_ = Eigen::Vector3d::Zero();
    double last_delta_yaw_ = 0.0;
    std::atomic<bool> velocity_initialized_{false};
    
    // ROS Publishers
    rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
    rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diag_pub_;
    rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr tracking_pub_;
    
    // Image publishers for RTAB-Map
    rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr rgb_pub_;
    rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr depth_pub_;
    rclcpp::Publisher<sensor_msgs::msg::CameraInfo>::SharedPtr camera_info_pub_;
    rclcpp::Publisher<sensor_msgs::msg::CameraInfo>::SharedPtr camera_info_scan_pub_;
    
    // Compressed publishers
    // Compressed publishers
    rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr rgb_compressed_pub_;
    rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr depth_compressed_pub_;
    
    // Debug Publishers
    rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr debug_view_pub_;
    rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr depth_preview_pub_;
    
    // IMU
    rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr imu_pub_;
    
    // Guess publisher for RTAB-Map (initial motion estimate)
    rclcpp::Publisher<geometry_msgs::msg::TransformStamped>::SharedPtr guess_pub_;
    
    // Threading
    std::thread processing_thread_;
    std::atomic<bool> running_{false};
    
    // Motion Gate State
    rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr cmd_vel_sub_;
    std::atomic<bool> motors_active_{false};
    std::atomic<bool> imu_motion_detected_{false};
    rclcpp::Time last_cmd_vel_time_;  // Protected by time_mutex_
    std::atomic<double> last_imu_gyro_norm_{0.0};
    std::atomic<double> last_imu_accel_deviation_{0.0};
    void cmdVelCallback(const geometry_msgs::msg::Twist::SharedPtr msg);
    bool isRobotMoving();
    void processIMU(const std::shared_ptr<dai::IMUData>& imuData);
    
    // IMU Pitch Calibration (auto-calibrates camera pitch from gravity vector at startup)
    std::atomic<bool> pitch_calibrated_{false};
    std::atomic<double> calibrated_pitch_{0.0};      // Calibrated camera pitch (radians)
    int calibration_sample_count_ = 0;               // Number of samples collected
    double accel_sum_x_ = 0.0;                       // Accumulated accelerometer X
    double accel_sum_y_ = 0.0;                       // Accumulated accelerometer Y  
    double accel_sum_z_ = 0.0;                       // Accumulated accelerometer Z
    static const int CALIBRATION_SAMPLES = 50;       // Samples needed for calibration
    // [OPT 4] Tempo di inizio calibrazione per timeout 30s
    rclcpp::Time calibration_start_time_;
    
    // [OPT 3] Piramide KLT del frame precedente per riuso
    std::vector<cv::Mat> prev_pyramid_;
    
    // Camera intrinsics
    cv::Mat camera_matrix_;
    double fx_ = 0.0, fy_ = 0.0, cx_ = 0.0, cy_ = 0.0;
    
    // Performance (thread-safe)
    rclcpp::Time last_diag_time_;  // Protected by time_mutex_
    rclcpp::Time start_time_;     // Session start time
    std::atomic<uint64_t> processed_frames_{0};
    
    // Helper
    std::string stateToString(TrackingState state) const;
    void computeCameraTransform();
};
