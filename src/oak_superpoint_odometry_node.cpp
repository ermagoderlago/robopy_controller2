#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <sensor_msgs/msg/compressed_image.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <vision_msgs/msg/detection2_d_array.hpp>
#include <std_msgs/msg/float32.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/string.hpp>
#include <geometry_msgs/msg/transform_stamped.hpp>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2_ros/transform_broadcaster.h>
#include <tf2_geometry_msgs/tf2_geometry_msgs.hpp>
#include <cv_bridge/cv_bridge.hpp>
#include <image_transport/image_transport.hpp>

#include <depthai/depthai.hpp>
#include <opencv2/opencv.hpp>
#include <opencv2/calib3d.hpp>
#include <opencv2/features2d.hpp>

#include <chrono>
#include <thread>
#include <mutex>
#include <deque>
#include <atomic>
#include <condition_variable>

// Utility headers
#include "frame_synchronizer.hpp"
#include "camera_transform_handler.hpp"
#include "adaptive_covariance.hpp"

using namespace std::chrono_literals;

class OakSuperPointOdometry : public rclcpp::Node {
public:
    OakSuperPointOdometry() : Node("oak_superpoint_odometry") {
        RCLCPP_INFO(this->get_logger(), "🚀 OAK-D Hybrid VO: ORB (primary) + SuperPoint (relocalization)");

        // ============================================================================
        // PARAMETERS
        // ============================================================================
        declare_parameter("superpoint_blob_path", "");
        declare_parameter("yolo_blob_path", "");
        declare_parameter("enable_yolo", true);
        declare_parameter("yolo_frequency", 2.0);
        declare_parameter("yolo_conf_thresh", 0.5);
        declare_parameter("yolo_iou_thresh", 0.5);
        declare_parameter("publish_tf", false);
        declare_parameter("filter_alpha", 0.25);
        declare_parameter("min_features", 30);
        declare_parameter("min_inliers", 12);
        declare_parameter("min_depth", 0.3);
        declare_parameter("max_depth", 8.0);
        declare_parameter("enable_clahe", false);
        declare_parameter("use_bruteforce", false);
        declare_parameter("depth_fps", 30.0);
        declare_parameter("depth_resolution", "400p");
        declare_parameter("depth_pub_width", 320);
        declare_parameter("depth_pub_height", 200);
        
        // NEW PARAMETERS
        declare_parameter("vo_skip_frames", 1);
        declare_parameter("max_orb_features", 500);
        declare_parameter("use_orb_primary", true);  // ORB as primary VO
        declare_parameter("superpoint_relocalization", true);  // Use SP for relocalization
        declare_parameter("lost_tracking_threshold", 10);  // Consecutive frames with low inliers
        declare_parameter("relocalization_inliers", 30);  // Min inliers for relocalization success

        sp_blob_ = get_parameter("superpoint_blob_path").as_string();
        yolo_blob_ = get_parameter("yolo_blob_path").as_string();
        enable_yolo_ = get_parameter("enable_yolo").as_bool();
        yolo_freq_ = get_parameter("yolo_frequency").as_double();
        yolo_conf_thresh_ = get_parameter("yolo_conf_thresh").as_double();
        yolo_iou_thresh_ = get_parameter("yolo_iou_thresh").as_double();
        publish_tf_ = get_parameter("publish_tf").as_bool();
        filter_alpha_ = get_parameter("filter_alpha").as_double();
        min_features_ = get_parameter("min_features").as_int();
        min_inliers_ = get_parameter("min_inliers").as_int();
        min_depth_ = get_parameter("min_depth").as_double();
        max_depth_ = get_parameter("max_depth").as_double();
        enable_clahe_ = get_parameter("enable_clahe").as_bool();
        use_bruteforce_ = get_parameter("use_bruteforce").as_bool();
        depth_fps_ = get_parameter("depth_fps").as_double();
        depth_pub_w_ = get_parameter("depth_pub_width").as_int();
        depth_pub_h_ = get_parameter("depth_pub_height").as_int();
        
        vo_skip_frames_ = get_parameter("vo_skip_frames").as_int();
        max_orb_features_ = get_parameter("max_orb_features").as_int();
        use_orb_primary_ = get_parameter("use_orb_primary").as_bool();
        sp_relocalization_ = get_parameter("superpoint_relocalization").as_bool();
        lost_tracking_threshold_ = get_parameter("lost_tracking_threshold").as_int();
        relocalization_inliers_ = get_parameter("relocalization_inliers").as_int();

        // CLAHE
        if (enable_clahe_) {
            clahe_ = cv::createCLAHE(2.0, cv::Size(8, 8));
        }

        // ORB detector
        orb_ = cv::ORB::create(max_orb_features_, 1.2f, 8, 31, 0, 2, cv::ORB::HARRIS_SCORE, 31, 20);

        // Matcher
        if (use_bruteforce_) {
            matcher_ = cv::BFMatcher::create(cv::NORM_HAMMING, false);  // ORB uses HAMMING
            sp_matcher_ = cv::BFMatcher::create(cv::NORM_L2, false);    // SuperPoint uses L2
        } else {
            matcher_ = cv::BFMatcher::create(cv::NORM_HAMMING, false);  // BF better for ORB
            sp_matcher_ = cv::FlannBasedMatcher::create();
        }

        // ============================================================================
        // ROS PUBLISHERS
        // ============================================================================
        pub_odom_ = create_publisher<nav_msgs::msg::Odometry>("/vo/odom", rclcpp::QoS(10).reliable());
        pub_depth_ = create_publisher<sensor_msgs::msg::Image>("/camera/depth/image_raw", rclcpp::QoS(10).reliable());
        pub_debug_ = create_publisher<sensor_msgs::msg::CompressedImage>("/vo/debug/compressed", rclcpp::QoS(10).best_effort());
        pub_quality_ = create_publisher<std_msgs::msg::Float32>("/vo/quality", 10);
        pub_camera_info_ = create_publisher<sensor_msgs::msg::CameraInfo>("/camera/camera_info", 10);
        pub_rgb_ = create_publisher<sensor_msgs::msg::Image>("/rgb/image", rclcpp::QoS(10).reliable());
        pub_imu_ = create_publisher<sensor_msgs::msg::Imu>("/oak/imu/data", 50);
        pub_tracking_status_ = create_publisher<std_msgs::msg::Bool>("/vo/tracking_ok", 10);
        pub_diagnostics_ = create_publisher<std_msgs::msg::String>("/vo/diagnostics", 10);

        if (enable_yolo_) {
            pub_detections_ = create_publisher<vision_msgs::msg::Detection2DArray>("/yolo/detections", rclcpp::QoS(10).reliable());
        }

        tf_broadcaster_ = std::make_unique<tf2_ros::TransformBroadcaster>(*this);
        
        // Initialize synchronizer (20ms tolerance)
        frame_sync_ = std::make_unique<FrameSynchronizer<dai::ImgFrame, dai::ImgFrame>>(20, 10);
        
        // Configure adaptive covariance
        AdaptiveCovarianceEstimator::Params cov_params;
        cov_params.excellent_inliers = 50;
        cov_params.good_inliers = 30;
        cov_params.minimum_inliers = min_inliers_;
        covariance_estimator_ = AdaptiveCovarianceEstimator(cov_params);

        // Initialize Pose
        pose_matrix_ = cv::Mat::eye(4, 4, CV_64F);

        // ============================================================================
        // DEPTHAI INIT
        // ============================================================================
        if (!setup_pipeline()) {
            RCLCPP_ERROR(get_logger(), "Failed to setup DepthAI pipeline");
            throw std::runtime_error("DepthAI setup failed");
        }

        // Timers
        timer_odom_ = create_wall_timer(50ms, std::bind(&OakSuperPointOdometry::publish_odometry, this));

        // Start threads
        running_ = true;
        processing_thread_ = std::thread(&OakSuperPointOdometry::acquisition_loop, this);
        vo_thread_ = std::thread(&OakSuperPointOdometry::vo_processing_loop, this);
        depth_pub_thread_ = std::thread(&OakSuperPointOdometry::depth_publishing_loop, this);
        
        RCLCPP_INFO(get_logger(), "Mode: %s", use_orb_primary_ ? "ORB primary + SuperPoint relocalization" : "SuperPoint only");
    }

    ~OakSuperPointOdometry() {
        running_ = false;
        vo_cv_.notify_all();
        depth_cv_.notify_all();
        
        if (processing_thread_.joinable()) processing_thread_.join();
        if (vo_thread_.joinable()) vo_thread_.join();
        if (depth_pub_thread_.joinable()) depth_pub_thread_.join();
    }

private:
    // ============================================================================
    // PARAMETERS
    // ============================================================================
    std::string sp_blob_, yolo_blob_;
    bool enable_yolo_, publish_tf_, enable_clahe_, use_bruteforce_;
    bool use_orb_primary_, sp_relocalization_;
    double yolo_freq_, yolo_conf_thresh_, yolo_iou_thresh_;
    double filter_alpha_, min_depth_, max_depth_, depth_fps_;
    int min_features_, min_inliers_, depth_pub_w_, depth_pub_h_;
    int vo_skip_frames_, max_orb_features_, lost_tracking_threshold_, relocalization_inliers_;

    // ============================================================================
    // ROS
    // ============================================================================
    rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr pub_odom_;
    rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr pub_depth_, pub_rgb_;
    rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr pub_debug_;
    rclcpp::Publisher<std_msgs::msg::Float32>::SharedPtr pub_quality_;
    rclcpp::Publisher<sensor_msgs::msg::CameraInfo>::SharedPtr pub_camera_info_;
    rclcpp::Publisher<vision_msgs::msg::Detection2DArray>::SharedPtr pub_detections_;
    rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr pub_imu_;
    rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr pub_tracking_status_;
    std::unique_ptr<tf2_ros::TransformBroadcaster> tf_broadcaster_;
    rclcpp::TimerBase::SharedPtr timer_odom_;

    // ============================================================================
    // DEPTHAI PIPELINE
    // ============================================================================
    std::unique_ptr<dai::Device> device_;
    std::shared_ptr<dai::Pipeline> pipeline_;
    bool has_sp_ = false, has_yolo_ = false;
    double fx_ = 0, fy_ = 0, cx_ = 0, cy_ = 0;
    cv::Mat K_depth_, K_sp_;

    // ============================================================================
    // VO STATE
    // ============================================================================
    cv::Mat pose_matrix_; // 4x4
    std::mutex pose_mutex_;
    
    // ORB tracking
    cv::Mat last_orb_descs_;
    std::vector<cv::Point3f> last_orb_pts3d_;
    std::vector<cv::KeyPoint> last_orb_kpts_;
    std::mutex orb_mutex_;
    
    // SuperPoint tracking (for relocalization)
    cv::Mat last_sp_descs_;
    std::vector<cv::Point3f> last_sp_pts3d_;
    std::vector<cv::KeyPoint> last_sp_kpts_;
    std::mutex sp_mutex_;
    
    cv::Ptr<cv::CLAHE> clahe_;
    cv::Ptr<cv::ORB> orb_;
    cv::Ptr<cv::DescriptorMatcher> matcher_;      // For ORB (HAMMING)
    cv::Ptr<cv::DescriptorMatcher> sp_matcher_;   // For SuperPoint (L2)
    
    std::deque<int> inliers_history_;
    int consecutive_lost_frames_ = 0;
    bool tracking_lost_ = false;
    
    // Advanced utilities
    std::unique_ptr<FrameSynchronizer<dai::ImgFrame, dai::ImgFrame>> frame_sync_;
    std::unique_ptr<CameraTransformHandler> transform_handler_;
    AdaptiveCovarianceEstimator covariance_estimator_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_diagnostics_;

    // EMA Filter
    cv::Vec3f f_pos_ = {0,0,0};
    tf2::Quaternion f_quat_ = tf2::Quaternion::getIdentity();
    bool first_filter_ = true;

    // ============================================================================
    // MULTI-THREADING
    // ============================================================================
    std::thread processing_thread_, vo_thread_, depth_pub_thread_;
    std::atomic<bool> running_;
    std::atomic<int> frame_counter_{0};
    
    struct FrameData {
        rclcpp::Time ts;
        cv::Mat rectified;
        cv::Mat depth;
        std::shared_ptr<dai::NNData> sp;
        bool has_sp = false;
    };
    
    std::deque<FrameData> vo_queue_;
    std::deque<FrameData> depth_queue_;
    std::mutex vo_queue_mtx_, depth_queue_mtx_;
    std::condition_variable vo_cv_, depth_cv_;
    
    // ============================================================================
    // CONSTANTS
    // ============================================================================
    const int SP_W = 480;
    const int SP_H = 360;
    const int DEPTH_W = 640;
    const int DEPTH_H = 400;

    // Optimized depth search pattern (spiral from center)
    const std::vector<cv::Point> DEPTH_OFFSETS = {
        {0,0}, {1,0}, {0,1}, {-1,0}, {0,-1},
        {1,1}, {-1,1}, {-1,-1}, {1,-1},
        {2,0}, {0,2}, {-2,0}, {0,-2}
    };

    // ============================================================================
    // IMU CALLBACK
    // ============================================================================
    void imuCallback(std::shared_ptr<dai::ADatatype> data) {
        if (!rclcpp::ok()) return;
        std::shared_ptr<dai::IMUData> imuData = std::dynamic_pointer_cast<dai::IMUData>(data);
        if (!imuData) return;
        
        auto current_time = this->now();
        
        for (const auto& packet : imuData->packets) {
            sensor_msgs::msg::Imu imu_msg;
            imu_msg.header.stamp = current_time;
            imu_msg.header.frame_id = "imu_link";
            
            auto accel = packet.acceleroMeter;
            imu_msg.linear_acceleration.x = accel.x;
            imu_msg.linear_acceleration.y = accel.y;
            imu_msg.linear_acceleration.z = accel.z;
            
            auto gyro = packet.gyroscope;
            imu_msg.angular_velocity.x = gyro.x;
            imu_msg.angular_velocity.y = gyro.y;
            imu_msg.angular_velocity.z = -gyro.z;  // Inverted for correct direction
            
            imu_msg.orientation.w = 1.0;
            imu_msg.orientation.x = 0.0;
            imu_msg.orientation.y = 0.0;
            imu_msg.orientation.z = 0.0;
            imu_msg.orientation_covariance[0] = -1.0;
            
            imu_msg.linear_acceleration_covariance = {0.01, 0, 0,  0, 0.01, 0,  0, 0, 0.01};
            imu_msg.angular_velocity_covariance = {0.001, 0, 0,  0, 0.001, 0,  0, 0, 0.001};
            
            pub_imu_->publish(imu_msg);
        }
    }

    // ============================================================================
    // PIPELINE SETUP
    // ============================================================================
    bool setup_pipeline() {
        pipeline_ = std::make_shared<dai::Pipeline>();

        auto monoLeft = pipeline_->create<dai::node::MonoCamera>();
        auto monoRight = pipeline_->create<dai::node::MonoCamera>();
        auto stereo = pipeline_->create<dai::node::StereoDepth>();

        auto res = dai::MonoCameraProperties::SensorResolution::THE_400_P;
        monoLeft->setResolution(res);
        monoLeft->setBoardSocket(dai::CameraBoardSocket::CAM_B);
        monoLeft->setFps(depth_fps_);
        monoRight->setResolution(res);
        monoRight->setBoardSocket(dai::CameraBoardSocket::CAM_C);
        monoRight->setFps(depth_fps_);

        stereo->setDefaultProfilePreset(dai::node::StereoDepth::PresetMode::HIGH_ACCURACY);
        stereo->setDepthAlign(dai::CameraBoardSocket::CAM_B);
        stereo->setLeftRightCheck(true);
        stereo->setSubpixel(true);
        
        auto config = stereo->initialConfig.get();
        config.postProcessing.median = dai::MedianFilter::KERNEL_5x5;
        config.postProcessing.spatialFilter.enable = true;
        config.postProcessing.spatialFilter.holeFillingRadius = 2;
        config.postProcessing.spatialFilter.numIterations = 1;
        config.postProcessing.temporalFilter.enable = true;
        config.postProcessing.temporalFilter.persistencyMode = 
            dai::RawStereoDepthConfig::PostProcessing::TemporalFilter::PersistencyMode::VALID_1_IN_LAST_2;
        config.postProcessing.thresholdFilter.minRange = (int)(min_depth_ * 1000);
        config.postProcessing.thresholdFilter.maxRange = (int)(max_depth_ * 1000);
        stereo->initialConfig.set(config);

        monoLeft->out.link(stereo->left);
        monoRight->out.link(stereo->right);

        // IMU
        auto imu = pipeline_->create<dai::node::IMU>();
        imu->enableIMUSensor({dai::IMUSensor::ACCELEROMETER_RAW, 
                              dai::IMUSensor::GYROSCOPE_RAW}, 100);
        imu->setBatchReportThreshold(1);
        imu->setMaxBatchReports(10);
        
        auto xoutImu = pipeline_->create<dai::node::XLinkOut>();
        xoutImu->setStreamName("imu");
        imu->out.link(xoutImu->input);

        // SuperPoint (optional - for relocalization)
        std::ifstream f(sp_blob_.c_str());
        if (f.good() && sp_relocalization_) {
            has_sp_ = true;
            auto manip = pipeline_->create<dai::node::ImageManip>();
            manip->initialConfig.setResize(SP_W, SP_H);
            manip->initialConfig.setFrameType(dai::RawImgFrame::Type::GRAY8);
            manip->setKeepAspectRatio(false);
            stereo->rectifiedLeft.link(manip->inputImage);

            auto nn = pipeline_->create<dai::node::NeuralNetwork>();
            nn->setBlobPath(sp_blob_);
            nn->setNumInferenceThreads(2);
            nn->input.setBlocking(false);
            manip->out.link(nn->input);

            auto xoutSp = pipeline_->create<dai::node::XLinkOut>();
            xoutSp->setStreamName("superpoint");
            nn->out.link(xoutSp->input);
            
            RCLCPP_INFO(get_logger(), "SuperPoint loaded for relocalization");
        }

        // YOLO (optional)
        std::ifstream fy(yolo_blob_.c_str());
        if (enable_yolo_ && fy.good()) {
            has_yolo_ = true;
            auto camRgb = pipeline_->create<dai::node::ColorCamera>();
            camRgb->setBoardSocket(dai::CameraBoardSocket::CAM_A);
            camRgb->setResolution(dai::ColorCameraProperties::SensorResolution::THE_1080_P);
            camRgb->setFps(depth_fps_);
            camRgb->setPreviewSize(320, 320);
            camRgb->setInterleaved(false);
            
            auto yolo = pipeline_->create<dai::node::YoloDetectionNetwork>();
            yolo->setBlobPath(yolo_blob_);
            yolo->setConfidenceThreshold(yolo_conf_thresh_);
            yolo->setNumClasses(80);
            yolo->setCoordinateSize(4);
            yolo->setAnchors({10,14, 23,27, 37,58, 81,82, 135,169, 344,319});
            yolo->setAnchorMasks({{"side26", {1,2,3}}, {"side13", {3,4,5}}});
            camRgb->preview.link(yolo->input);

            auto xoutY = pipeline_->create<dai::node::XLinkOut>();
            xoutY->setStreamName("yolo");
            yolo->out.link(xoutY->input);

            auto xoutRgb = pipeline_->create<dai::node::XLinkOut>();
            xoutRgb->setStreamName("rgb");
            camRgb->video.link(xoutRgb->input);
        }

        auto xoutRect = pipeline_->create<dai::node::XLinkOut>();
        xoutRect->setStreamName("rect");
        stereo->rectifiedLeft.link(xoutRect->input);

        auto xoutDepth = pipeline_->create<dai::node::XLinkOut>();
        xoutDepth->setStreamName("depth");
        stereo->depth.link(xoutDepth->input);

        // Start Device
        device_ = std::make_unique<dai::Device>(*pipeline_);
        
        // Calibration
        auto calib = device_->readCalibration();
        auto M = calib.getCameraIntrinsics(dai::CameraBoardSocket::CAM_B, DEPTH_W, DEPTH_H);
        fx_ = M[0][0]; fy_ = M[1][1]; cx_ = M[0][2]; cy_ = M[1][2];

        K_depth_ = cv::Mat::eye(3, 3, CV_64F);
        K_depth_.at<double>(0,0) = fx_; K_depth_.at<double>(1,1) = fy_;
        K_depth_.at<double>(0,2) = cx_; K_depth_.at<double>(1,2) = cy_;

        // K for SP (scaled)
        double sw = (double)SP_W / DEPTH_W;
        double sh = (double)SP_H / DEPTH_H;
        K_sp_ = K_depth_.clone();
        K_sp_.at<double>(0,0) *= sw; K_sp_.at<double>(1,1) *= sh;
        K_sp_.at<double>(0,2) *= sw; K_sp_.at<double>(1,2) *= sh;

        RCLCPP_INFO(get_logger(), "Camera: fx=%.1f, fy=%.1f, cx=%.1f, cy=%.1f", fx_, fy_, cx_, cy_);
        
        // Initialize transform handler (reads from TF tree)
        transform_handler_ = std::make_unique<CameraTransformHandler>(
            this, "base_link", "camera_link", "camera_optical_frame");
        
        return true;
    }

    // ============================================================================
    // ACQUISITION LOOP (Thread 1)
    // ============================================================================
    void acquisition_loop() {
        try {
            auto qRect = device_->getOutputQueue("rect", 8, false);
            auto qDepth = device_->getOutputQueue("depth", 8, false);
            auto qImu = device_->getOutputQueue("imu", 50, false);
            qImu->addCallback(std::bind(&OakSuperPointOdometry::imuCallback, this, std::placeholders::_1));
            
            std::shared_ptr<dai::DataOutputQueue> qSp, qYolo, qRgb;
            if (has_sp_) qSp = device_->getOutputQueue("superpoint", 8, false);
            if (has_yolo_) {
                qYolo = device_->getOutputQueue("yolo", 4, false);
                qRgb = device_->getOutputQueue("rgb", 4, false);
            }

            auto last_log = std::chrono::steady_clock::now();
            int frame_count = 0;
            auto last_sync_stats = std::chrono::steady_clock::now();

            while (running_ && rclcpp::ok()) {
                try {
                    // Feed frames to synchronizer instead of direct processing
                    auto rect = qRect->tryGet<dai::ImgFrame>();
                    auto depth = qDepth->tryGet<dai::ImgFrame>();
                    
                    if (rect) frame_sync_->addFirst(rect);
                    if (depth) frame_sync_->addSecond(depth);
                    
                    // Get synchronized pair
                    auto synced = frame_sync_->getNext();
                    if (!synced.valid) {
                        std::this_thread::sleep_for(1ms);
                        continue;
                    }

                    auto ts = this->now();
                    int fc = frame_counter_++;
                    frame_count++;

                    // Logging with sync stats
                    auto now = std::chrono::steady_clock::now();
                    if (std::chrono::duration_cast<std::chrono::seconds>(now - last_log).count() >= 5) {
                        auto stats = frame_sync_->getStats();
                        RCLCPP_INFO(get_logger(), 
                            "FPS: %.1f | Tracking: %s | Synced: %zu | Dropped: %zu", 
                            frame_count / 5.0,
                            tracking_lost_ ? "LOST" : "OK",
                            stats.total_matched,
                            stats.total_dropped);
                        last_log = now;
                        frame_count = 0;
                    }
                    
                    // Print sync diagnostics every 30s
                    if (std::chrono::duration_cast<std::chrono::seconds>(now - last_sync_stats).count() >= 30) {
                        auto stats = frame_sync_->getStats();
                        RCLCPP_INFO(get_logger(), 
                            "Sync Stats - Queue1: %zu, Queue2: %zu, Synced: %zu, Dropped: %zu",
                            stats.queue1_size, stats.queue2_size, 
                            stats.synced_count, stats.total_dropped);
                        last_sync_stats = now;
                    }

                    FrameData frame;
                    frame.ts = ts;
                    frame.rectified = synced.first->getCvFrame().clone();
                    frame.depth = synced.second->getCvFrame().clone();

                    // VO Queue (with frame skipping)
                    if (fc % vo_skip_frames_ == 0) {
                        if (qSp && tracking_lost_) {
                            auto sp = qSp->tryGet<dai::NNData>();
                            if (sp) {
                                frame.sp = sp;
                                frame.has_sp = true;
                            }
                        }
                        
                        std::lock_guard<std::mutex> lock(vo_queue_mtx_);
                        if (vo_queue_.size() > 2) vo_queue_.pop_front();
                        vo_queue_.push_back(frame);
                        vo_cv_.notify_one();
                    }

                    // Depth Publishing Queue (all frames)
                    {
                        std::lock_guard<std::mutex> lock(depth_queue_mtx_);
                        if (depth_queue_.size() > 5) depth_queue_.pop_front();
                        depth_queue_.push_back(frame);
                        depth_cv_.notify_one();
                    }

                    // YOLO (low frequency)
                    if (has_yolo_ && qYolo && qRgb && fc % 15 == 0) {
                        auto yolo = qYolo->tryGet<dai::ImgDetections>();
                        auto rgb = qRgb->tryGet<dai::ImgFrame>();
                        if (yolo && rgb) {
                            process_yolo(yolo, rgb->getCvFrame(), ts);
                        }
                    }

                } catch (const std::exception& e) {
                    RCLCPP_ERROR(get_logger(), "Acquisition error: %s", e.what());
                    std::this_thread::sleep_for(100ms);
                }
            }
            RCLCPP_INFO(get_logger(), "Acquisition loop exiting");
        } catch (const std::exception& e) {
            RCLCPP_FATAL(get_logger(), "Fatal acquisition error: %s", e.what());
        }
    }

    // ============================================================================
    // VO PROCESSING LOOP (Thread 2)
    // ============================================================================
    void vo_processing_loop() {
        while (running_ && rclcpp::ok()) {
            FrameData frame;
            {
                std::unique_lock<std::mutex> lock(vo_queue_mtx_);
                if (!vo_cv_.wait_for(lock, 50ms, [this]() { return !vo_queue_.empty(); })) {
                    continue;
                }
                frame = vo_queue_.back();
                vo_queue_.clear();  // Process only latest frame
            }

            // PRIMARY: ORB tracking
            if (use_orb_primary_ && !tracking_lost_) {
                process_orb_vo(frame.rectified, frame.depth, frame.ts);
            }
            
            // FALLBACK: SuperPoint relocalization when lost
            if (tracking_lost_ && frame.has_sp) {
                RCLCPP_WARN(get_logger(), "Attempting SuperPoint relocalization...");
                process_superpoint_relocalization(frame.sp, frame.rectified, frame.depth, frame.ts);
            }
        }
        RCLCPP_INFO(get_logger(), "VO loop exiting");
    }

    // ============================================================================
    // DEPTH PUBLISHING LOOP (Thread 3)
    // ============================================================================
    void depth_publishing_loop() {
        while (running_ && rclcpp::ok()) {
            FrameData frame;
            {
                std::unique_lock<std::mutex> lock(depth_queue_mtx_);
                if (!depth_cv_.wait_for(lock, 20ms, [this]() { return !depth_queue_.empty(); })) {
                    continue;
                }
                frame = depth_queue_.front();
                depth_queue_.pop_front();
            }

            // Publish depth only if subscribers exist
            if (pub_depth_->get_subscription_count() > 0 || 
                pub_camera_info_->get_subscription_count() > 0) {
                publish_depth_and_info(frame.depth, frame.ts);
            }

            // Publish RGB/Mono
            if (pub_rgb_->get_subscription_count() > 0) {
                if (!has_yolo_) {
                    publish_mono_as_rgb(frame.rectified, frame.ts);
                }
            }
        }
        RCLCPP_INFO(get_logger(), "Depth publishing loop exiting");
    }

    // ============================================================================
    // ORB VISUAL ODOMETRY (Primary Tracking)
    // ============================================================================
    void process_orb_vo(cv::Mat gray, cv::Mat depth, rclcpp::Time ts) {
        auto start = std::chrono::steady_clock::now();
        
        // CLAHE
        if (enable_clahe_) {
            clahe_->apply(gray, gray);
        }

        // Detect ORB features
        std::vector<cv::KeyPoint> kpts;
        cv::Mat descs;
        orb_->detectAndCompute(gray, cv::Mat(), kpts, descs);

        if (kpts.empty()) {
            RCLCPP_WARN(get_logger(), "No ORB features detected");
            return;
        }

        // Backproject to 3D
        std::vector<cv::Point3f> pts3d;
        std::vector<int> valid_idx;
        backproject_keypoints(kpts, depth, DEPTH_W, DEPTH_H, pts3d, valid_idx);

        int n_matches = 0;
        int n_inliers = 0;
        bool vo_success = false;

        {
            std::lock_guard<std::mutex> lock(orb_mutex_);
            
            if (!last_orb_pts3d_.empty() && pts3d.size() >= (size_t)min_features_) {
                // Build valid descriptors
                cv::Mat valid_descs;
                if (!valid_idx.empty()) {
                    valid_descs.create(valid_idx.size(), descs.cols, descs.type());
                    for (size_t i = 0; i < valid_idx.size(); i++) {
                        descs.row(valid_idx[i]).copyTo(valid_descs.row(i));
                    }
                }

                if (!valid_descs.empty() && !last_orb_descs_.empty()) {
                    // Match using Lowe's ratio test
                    std::vector<std::vector<cv::DMatch>> knn_matches;
                    matcher_->knnMatch(valid_descs, last_orb_descs_, knn_matches, 2);

                    std::vector<cv::Point3f> obj_pts;
                    std::vector<cv::Point2f> img_pts;
                    
                    for (auto &m : knn_matches) {
                        if (m.size() == 2 && m[0].distance < 0.7f * m[1].distance) {
                            obj_pts.push_back(last_orb_pts3d_[m[0].trainIdx]);
                            img_pts.push_back(kpts[valid_idx[m[0].queryIdx]].pt);
                            n_matches++;
                        }
                    }

                    if (n_matches >= min_inliers_) {
                        cv::Mat rvec, tvec;
                        std::vector<int> inliers;
                        bool success = cv::solvePnPRansac(obj_pts, img_pts, K_depth_, cv::Mat(), 
                            rvec, tvec, false, 100, 2.0f, 0.99, inliers, cv::SOLVEPNP_EPNP);
                        
                        if (success && inliers.size() >= (size_t)min_inliers_) {
                            n_inliers = inliers.size();
                            
                            // Use dynamic transform instead of hardcoded matrix
                            cv::Mat R_base, t_base;
                            if (transform_handler_->cameraMotionToBase(rvec, tvec, R_base, t_base)) {
                                // Update pose in base frame
                                cv::Mat T_base = cv::Mat::eye(4, 4, CV_64F);
                                R_base.copyTo(T_base(cv::Rect(0,0,3,3)));
                                t_base.copyTo(T_base(cv::Rect(3,0,1,3)));
                                
                                {
                                    std::lock_guard<std::mutex> pose_lock(pose_mutex_);
                                    pose_matrix_ = pose_matrix_ * T_base;
                                }
                                
                                vo_success = true;
                                
                                // Update adaptive covariance
                                double translation_norm = cv::norm(t_base);
                                double depth_valid_ratio = (double)pts3d.size() / kpts.size();
                                covariance_estimator_.update(n_inliers, n_matches, translation_norm,
                                                            depth_valid_ratio, false);
                            } else {
                                RCLCPP_WARN(get_logger(), "Transform failed");
                                consecutive_lost_frames_++;
                            }
                            
                            inliers_history_.push_back(n_inliers);
                            if (inliers_history_.size() > 10) inliers_history_.pop_front();
                            
                            consecutive_lost_frames_ = 0;
                            
                            if (n_inliers < 20) {
                                RCLCPP_WARN(get_logger(), "ORB: Low inliers %d", n_inliers);
                            }
                        } else {
                            RCLCPP_WARN(get_logger(), "ORB PnP failed");
                            consecutive_lost_frames_++;
                        }
                    } else {
                        consecutive_lost_frames_++;
                    }
                }
            }

            // Update history
            last_orb_pts3d_ = pts3d;
            if (!valid_idx.empty()) {
                last_orb_descs_.create(valid_idx.size(), descs.cols, descs.type());
                for (size_t i = 0; i < valid_idx.size(); i++) {
                    descs.row(valid_idx[i]).copyTo(last_orb_descs_.row(i));
                }
                last_orb_kpts_.clear();
                for (int idx : valid_idx) {
                    last_orb_kpts_.push_back(kpts[idx]);
                }
            }
        }

        // Check if tracking is lost
        if (consecutive_lost_frames_ >= lost_tracking_threshold_) {
            if (!tracking_lost_) {
                RCLCPP_ERROR(get_logger(), "🔴 TRACKING LOST! Will attempt SuperPoint relocalization");
                tracking_lost_ = true;
                std_msgs::msg::Bool status;
                status.data = false;
                pub_tracking_status_->publish(status);
            }
        }

        // Publish quality
        float quality = (n_matches > 0) ? (float)n_inliers / n_matches : 0.0f;
        std_msgs::msg::Float32 q_msg;
        q_msg.data = quality;
        pub_quality_->publish(q_msg);

        // Debug visualization
        if (pub_debug_->get_subscription_count() > 0 && vo_success) {
            cv::Mat debug_img;
            cv::cvtColor(gray, debug_img, cv::COLOR_GRAY2BGR);
            
            for (size_t i = 0; i < kpts.size(); i++) {
                cv::Scalar color = (std::find(valid_idx.begin(), valid_idx.end(), i) != valid_idx.end()) ? 
                    cv::Scalar(0, 255, 0) : cv::Scalar(0, 0, 255);
                cv::circle(debug_img, kpts[i].pt, 3, color, -1);
            }
            
            cv::putText(debug_img, "ORB: " + std::to_string(n_inliers) + "/" + std::to_string(n_matches), 
                cv::Point(10, 30), cv::FONT_HERSHEY_SIMPLEX, 0.7, cv::Scalar(0, 255, 0), 2);
            
            std_msgs::msg::Header h;
            h.stamp = ts;
            h.frame_id = "left_optical_frame";
            sensor_msgs::msg::CompressedImage cmsg;
            cmsg.header = h;
            cmsg.format = "jpeg";
            cv::imencode(".jpg", debug_img, cmsg.data, {cv::IMWRITE_JPEG_QUALITY, 80});
            pub_debug_->publish(cmsg);
        }

        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start).count();
        if (elapsed > 40) {
            RCLCPP_WARN(get_logger(), "ORB VO slow: %ld ms", elapsed);
        }
    }

    // ============================================================================
    // SUPERPOINT RELOCALIZATION (Fallback when tracking is lost)
    // ============================================================================
    void process_superpoint_relocalization(std::shared_ptr<dai::NNData> sp, 
                                          cv::Mat gray, cv::Mat depth, rclcpp::Time ts) {
        (void)gray; // Silence unused warning
        (void)ts;   // Silence unused warning

        std::vector<cv::KeyPoint> kpts;
        cv::Mat descs;
        
        if (!decode_superpoint_fast(sp, kpts, descs)) {
            RCLCPP_WARN(get_logger(), "SuperPoint decode failed");
            return;
        }

        // Backproject
        std::vector<cv::Point3f> pts3d;
        std::vector<int> valid_idx;
        backproject_keypoints(kpts, depth, SP_W, SP_H, pts3d, valid_idx);

        int n_matches = 0;
        int n_inliers = 0;

        {
            std::lock_guard<std::mutex> lock(sp_mutex_);
            
            if (!last_sp_pts3d_.empty() && pts3d.size() >= (size_t)min_features_) {
                cv::Mat valid_descs;
                if (!valid_idx.empty()) {
                    valid_descs.create(valid_idx.size(), descs.cols, descs.type());
                    for (size_t i = 0; i < valid_idx.size(); i++) {
                        descs.row(valid_idx[i]).copyTo(valid_descs.row(i));
                    }
                }

                if (!valid_descs.empty() && !last_sp_descs_.empty()) {
                    std::vector<std::vector<cv::DMatch>> knn_matches;
                    sp_matcher_->knnMatch(valid_descs, last_sp_descs_, knn_matches, 2);

                    std::vector<cv::Point3f> obj_pts;
                    std::vector<cv::Point2f> img_pts;
                    
                    for (auto &m : knn_matches) {
                        if (m.size() == 2 && m[0].distance < 0.75f * m[1].distance) {
                            obj_pts.push_back(last_sp_pts3d_[m[0].trainIdx]);
                            img_pts.push_back(kpts[valid_idx[m[0].queryIdx]].pt);
                            n_matches++;
                        }
                    }

                    if (n_matches >= relocalization_inliers_) {
                        cv::Mat rvec, tvec;
                        std::vector<int> inliers;
                        bool success = cv::solvePnPRansac(obj_pts, img_pts, K_sp_, cv::Mat(), 
                            rvec, tvec, false, 200, 2.0f, 0.99, inliers, cv::SOLVEPNP_EPNP);
                        
                        if (success && inliers.size() >= (size_t)relocalization_inliers_) {
                            n_inliers = inliers.size();
                            update_pose(rvec, tvec);
                            
                            RCLCPP_INFO(get_logger(), "🟢 RELOCALIZATION SUCCESS! Inliers: %d", n_inliers);
                            tracking_lost_ = false;
                            consecutive_lost_frames_ = 0;
                            
                            std_msgs::msg::Bool status;
                            status.data = true;
                            pub_tracking_status_->publish(status);
                            
                            // Transfer to ORB tracking
                            {
                                std::lock_guard<std::mutex> orb_lock(orb_mutex_);
                                last_orb_pts3d_.clear();
                                last_orb_descs_ = cv::Mat();
                                last_orb_kpts_.clear();
                            }
                        }
                    }
                }
            }

            // Always update SuperPoint history for future relocalization
            last_sp_pts3d_ = pts3d;
            if (!valid_idx.empty()) {
                last_sp_descs_.create(valid_idx.size(), descs.cols, descs.type());
                for (size_t i = 0; i < valid_idx.size(); i++) {
                    descs.row(valid_idx[i]).copyTo(last_sp_descs_.row(i));
                }
            }
        }

        RCLCPP_INFO(get_logger(), "SuperPoint: %d matches, %d inliers", n_matches, n_inliers);
    }

    // ============================================================================
    // SUPERPOINT DECODER (Optimized)
    // ============================================================================
    bool decode_superpoint_fast(std::shared_ptr<dai::NNData> sp, 
                               std::vector<cv::KeyPoint>& kpts, 
                               cv::Mat& descs) {
        auto layers = sp->getAllLayerNames();
        if (layers.size() < 2) return false;
        
        std::vector<float> heatmap, descriptors;
        for (auto& name : layers) {
            auto data = sp->getLayerFp16(name);
            if (data.size() > 500000) descriptors = std::move(data);
            else if (data.size() > 100000) heatmap = std::move(data);
        }
        
        if (heatmap.empty() || descriptors.empty()) return false;
        
        const int H = SP_H / 8;
        const int W = SP_W / 8;
        
        // Find top-K peaks directly (no full pixel shuffle)
        using ScoredPoint = std::tuple<float, int, int, int>;
        std::vector<ScoredPoint> candidates;
        candidates.reserve(H * W);
        
        for (int h = 0; h < H; h++) {
            for (int w = 0; w < W; w++) {
                float max_score = 0;
                int best_c = -1;
                
                for (int c = 0; c < 64; c++) {
                    int idx = c * H * W + h * W + w;
                    if (heatmap[idx] > max_score) {
                        max_score = heatmap[idx];
                        best_c = c;
                    }
                }
                
                if (max_score > 0.015f) {
                    candidates.emplace_back(max_score, h, w, best_c);
                }
            }
        }
        
        // Top-K sorting
        const int MAX_KPT = std::min(300, (int)candidates.size());
        std::partial_sort(candidates.begin(), 
                         candidates.begin() + MAX_KPT,
                         candidates.end(),
                         [](const ScoredPoint& a, const ScoredPoint& b) { 
                             return std::get<0>(a) > std::get<0>(b); 
                         });
        
        candidates.resize(MAX_KPT);
        
        // Convert to keypoints and descriptors
        kpts.clear();
        kpts.reserve(MAX_KPT);
        descs.create(MAX_KPT, 256, CV_32F);
        
        for (size_t i = 0; i < candidates.size(); i++) {
            // Unpack tuple manually (C++14 compatible)
            float score = std::get<0>(candidates[i]);
            int h = std::get<1>(candidates[i]);
            int w = std::get<2>(candidates[i]);
            int c = std::get<3>(candidates[i]);
            
            int dy = c / 8;
            int dx = c % 8;
            float px = w * 8 + dx;
            float py = h * 8 + dy;
            
            kpts.emplace_back(px, py, 8.0f, -1, score);
            
            // Descriptor (nearest neighbor)
            float* desc_row = descs.ptr<float>(i);
            for (int d = 0; d < 256; d++) {
                desc_row[d] = descriptors[d * H * W + h * W + w];
            }
            cv::normalize(descs.row(i), descs.row(i));
        }
        
        return !kpts.empty();
    }

    // ============================================================================
    // BACKPROJECT KEYPOINTS TO 3D (Optimized)
    // ============================================================================
    void backproject_keypoints(const std::vector<cv::KeyPoint>& kpts, 
                               const cv::Mat& depth,
                               int img_w, int img_h,
                               std::vector<cv::Point3f>& pts3d,
                               std::vector<int>& valid_idx) {
        pts3d.clear();
        valid_idx.clear();
        pts3d.reserve(kpts.size());
        valid_idx.reserve(kpts.size());
        
        float sx = (float)DEPTH_W / img_w;
        float sy = (float)DEPTH_H / img_h;
        
        for (size_t i = 0; i < kpts.size(); i++) {
            int u = kpts[i].pt.x * sx;
            int v = kpts[i].pt.y * sy;
            
            float z = 0;
            for (const auto& off : DEPTH_OFFSETS) {
                int uu = u + off.x;
                int vv = v + off.y;
                if (uu >= 0 && uu < DEPTH_W && vv >= 0 && vv < DEPTH_H) {
                    float z_val = depth.at<uint16_t>(vv, uu) / 1000.0f;
                    if (z_val > min_depth_ && z_val < max_depth_) {
                        z = z_val;
                        break;
                    }
                }
            }
            
            if (z > 0) {
                float X = (u - cx_) * z / fx_;
                float Y = (v - cy_) * z / fy_;
                pts3d.emplace_back(X, Y, z);
                valid_idx.push_back(i);
            }
        }
    }

    // ============================================================================
    // UPDATE POSE
    // ============================================================================
    void update_pose(cv::Mat rvec, cv::Mat tvec) {
        std::lock_guard<std::mutex> lock(pose_mutex_);
        
        cv::Mat R_base, t_base;
        if (transform_handler_->cameraMotionToBase(rvec, tvec, R_base, t_base)) {
            cv::Mat T_robot = cv::Mat::eye(4, 4, CV_64F);
            R_base.copyTo(T_robot(cv::Rect(0,0,3,3)));
            t_base.copyTo(T_robot(cv::Rect(3,0,1,3)));

            pose_matrix_ = pose_matrix_ * T_robot;
        } else {
             RCLCPP_WARN(get_logger(), "SP Transform failed");
        }
    }

    // ============================================================================
    // PUBLISH ODOMETRY
    // ============================================================================
    void publish_odometry() {
        cv::Mat pose;
        {
            std::lock_guard<std::mutex> lock(pose_mutex_);
            pose = pose_matrix_.clone();
        }
        
        double x = pose.at<double>(0,3);
        double y = pose.at<double>(1,3);
        double z = pose.at<double>(2,3);
        
        cv::Mat R = pose(cv::Rect(0,0,3,3));
        tf2::Matrix3x3 tfR(
            R.at<double>(0,0), R.at<double>(0,1), R.at<double>(0,2),
            R.at<double>(1,0), R.at<double>(1,1), R.at<double>(1,2),
            R.at<double>(2,0), R.at<double>(2,1), R.at<double>(2,2)
        );
        tf2::Quaternion q;
        tfR.getRotation(q);
        
        // EMA filter
        if (first_filter_) {
            f_pos_ = {(float)x, (float)y, (float)z};
            f_quat_ = q;
            first_filter_ = false;
        } else {
            f_pos_[0] = f_pos_[0] * (1-filter_alpha_) + x * filter_alpha_;
            f_pos_[1] = f_pos_[1] * (1-filter_alpha_) + y * filter_alpha_;
            f_pos_[2] = f_pos_[2] * (1-filter_alpha_) + z * filter_alpha_;
            f_quat_ = f_quat_.slerp(q, filter_alpha_);
        }
        
        auto current_time = this->now();
        
        nav_msgs::msg::Odometry odom;
        odom.header.stamp = current_time;
        odom.header.frame_id = "odom";
        odom.child_frame_id = "base_link";
        odom.pose.pose.position.x = f_pos_[0];
        odom.pose.pose.position.y = f_pos_[1];
        odom.pose.pose.position.z = f_pos_[2];
        odom.pose.pose.orientation = tf2::toMsg(f_quat_);
        
        // Use adaptive covariance instead of fixed values
        covariance_estimator_.fillCovarianceMatrix(odom.pose.covariance);
        
        pub_odom_->publish(odom);
        
        // Publish diagnostics
        if (pub_diagnostics_->get_subscription_count() > 0) {
            std_msgs::msg::String diag_msg;
            diag_msg.data = covariance_estimator_.getDiagnostics();
            pub_diagnostics_->publish(diag_msg);
        }
        
        if (publish_tf_) {
            geometry_msgs::msg::TransformStamped t;
            t.header = odom.header;
            t.child_frame_id = "base_link";
            t.transform.translation.x = odom.pose.pose.position.x;
            t.transform.translation.y = odom.pose.pose.position.y;
            t.transform.translation.z = odom.pose.pose.position.z;
            t.transform.rotation = odom.pose.pose.orientation;
            tf_broadcaster_->sendTransform(t);
        }
    }

    // ============================================================================
    // PUBLISH DEPTH AND CAMERA INFO
    // ============================================================================
    void publish_depth_and_info(cv::Mat depth, rclcpp::Time ts) {
        try {
            if (depth.cols != DEPTH_W || depth.rows != DEPTH_H) return;
            
            if (depth_pub_w_ != DEPTH_W) {
                cv::resize(depth, depth, cv::Size(depth_pub_w_, depth_pub_h_), 0, 0, cv::INTER_NEAREST);
            }
            
            std_msgs::msg::Header header;
            header.stamp = ts;
            header.frame_id = "left_optical_frame";
            auto msg = cv_bridge::CvImage(header, "mono16", depth).toImageMsg();
            pub_depth_->publish(*msg);

            // Camera Info
            sensor_msgs::msg::CameraInfo info;
            info.header = header;
            info.width = depth_pub_w_;
            info.height = depth_pub_h_;
            info.distortion_model = "plumb_bob";
            info.d = {0.0, 0.0, 0.0, 0.0, 0.0};
            
            double scale_x = (double)depth_pub_w_ / DEPTH_W;
            double scale_y = (double)depth_pub_h_ / DEPTH_H;
            double fx_pub = fx_ * scale_x;
            double fy_pub = fy_ * scale_y;
            double cx_pub = cx_ * scale_x;
            double cy_pub = cy_ * scale_y;
            
            info.k[0] = fx_pub; info.k[1] = 0.0;    info.k[2] = cx_pub;
            info.k[3] = 0.0;    info.k[4] = fy_pub; info.k[5] = cy_pub;
            info.k[6] = 0.0;    info.k[7] = 0.0;    info.k[8] = 1.0;
            
            info.p[0] = fx_pub; info.p[1] = 0.0;    info.p[2] = cx_pub; info.p[3] = 0.0;
            info.p[4] = 0.0;    info.p[5] = fy_pub; info.p[6] = cy_pub; info.p[7] = 0.0;
            info.p[8] = 0.0;    info.p[9] = 0.0;    info.p[10] = 1.0;   info.p[11] = 0.0;
            
            info.r[0] = 1.0; info.r[1] = 0.0; info.r[2] = 0.0;
            info.r[3] = 0.0; info.r[4] = 1.0; info.r[5] = 0.0;
            info.r[6] = 0.0; info.r[7] = 0.0; info.r[8] = 1.0;
            
            pub_camera_info_->publish(info);
        } catch (const std::exception& e) {
            RCLCPP_ERROR(get_logger(), "Depth publish error: %s", e.what());
        }
    }

    // ============================================================================
    // PUBLISH MONO AS RGB
    // ============================================================================
    void publish_mono_as_rgb(cv::Mat gray, rclcpp::Time ts) {
        try {
            cv::Mat bgr;
            cv::cvtColor(gray, bgr, cv::COLOR_GRAY2BGR);
            cv::resize(bgr, bgr, cv::Size(depth_pub_w_, depth_pub_h_));
            std_msgs::msg::Header header;
            header.stamp = ts;
            header.frame_id = "left_optical_frame";
            auto msg = cv_bridge::CvImage(header, "bgr8", bgr).toImageMsg();
            pub_rgb_->publish(*msg);
        } catch (const std::exception& e) {
            RCLCPP_ERROR(get_logger(), "RGB publish error: %s", e.what());
        }
    }

    // ============================================================================
    // PROCESS YOLO
    // ============================================================================
    void process_yolo(std::shared_ptr<dai::ImgDetections> detections, cv::Mat rgb, rclcpp::Time ts) {
        vision_msgs::msg::Detection2DArray msg;
        msg.header.stamp = ts;
        msg.header.frame_id = "camera_color_optical_frame";
        
        for (auto& det : detections->detections) {
            vision_msgs::msg::Detection2D d;
            d.bbox.center.position.x = (det.xmin + det.xmax) * rgb.cols / 2.0;
            d.bbox.center.position.y = (det.ymin + det.ymax) * rgb.rows / 2.0;
            d.bbox.size_x = (det.xmax - det.xmin) * rgb.cols;
            d.bbox.size_y = (det.ymax - det.ymin) * rgb.rows;
            
            vision_msgs::msg::ObjectHypothesisWithPose hyp;
            hyp.hypothesis.class_id = std::to_string(det.label);
            hyp.hypothesis.score = det.confidence;
            d.results.push_back(hyp);
            msg.detections.push_back(d);
        }
        pub_detections_->publish(msg);
        
        // Publish RGB
        std_msgs::msg::Header h;
        h.stamp = ts;
        h.frame_id = "camera_color_optical_frame";
        auto img_msg = cv_bridge::CvImage(h, "bgr8", rgb).toImageMsg();
        pub_rgb_->publish(*img_msg);
    }
};

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    try {
        rclcpp::spin(std::make_shared<OakSuperPointOdometry>());
    } catch (const std::exception& e) {
        std::cerr << "Node Exception: " << e.what() << std::endl;
    }
    rclcpp::shutdown();
    return 0;
}
