#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <sensor_msgs/msg/compressed_image.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <vision_msgs/msg/detection2_d_array.hpp>
#include <std_msgs/msg/float32.hpp>
#include <geometry_msgs/msg/transform_stamped.hpp>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2_ros/transform_broadcaster.h>
#include <tf2_geometry_msgs/tf2_geometry_msgs.hpp>
#include <cv_bridge/cv_bridge.hpp>
#include <image_transport/image_transport.hpp>

#include <depthai/depthai.hpp>
#include <opencv2/opencv.hpp>
#include <opencv2/calib3d.hpp>

#include <chrono>
#include <thread>
#include <mutex>
#include <deque>
#include <atomic>

using namespace std::chrono_literals;

class OakSuperPointOdometry : public rclcpp::Node {
public:
    OakSuperPointOdometry() : Node("oak_superpoint_odometry") {
        RCLCPP_INFO(this->get_logger(), "🚀 OAK-D Hybrid: SuperPoint VO + Optional YOLO (C++ Native)");

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
        declare_parameter("min_features", 20);
        declare_parameter("min_inliers", 8);
        declare_parameter("min_depth", 0.3);
        declare_parameter("max_depth", 8.0);
        declare_parameter("enable_clahe", true);
        declare_parameter("use_bruteforce", true);
        declare_parameter("depth_fps", 20.0);
        declare_parameter("depth_resolution", "400p");
        declare_parameter("depth_pub_width", 320);
        declare_parameter("depth_pub_height", 200);

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

        // CLAHE
        if (enable_clahe_) {
            clahe_ = cv::createCLAHE(2.0, cv::Size(8, 8));
        }

        // Matcher
        if (use_bruteforce_) {
            matcher_ = cv::BFMatcher::create(cv::NORM_L2, false); // Cross check false for kNN
        } else {
            matcher_ = cv::FlannBasedMatcher::create(); // Default FLANN
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
        pub_imu_ = create_publisher<sensor_msgs::msg::Imu>("/oak/imu/data", 50);  // IMU at high rate

        if (enable_yolo_) {
            pub_detections_ = create_publisher<vision_msgs::msg::Detection2DArray>("/yolo/detections", rclcpp::QoS(10).reliable());
        }

        tf_broadcaster_ = std::make_unique<tf2_ros::TransformBroadcaster>(*this);

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
        // CameraInfo now published with depth frames for synchronization

        // Start thread
        running_ = true;
        processing_thread_ = std::thread(&OakSuperPointOdometry::process_frames, this);
    }

    ~OakSuperPointOdometry() {
        running_ = false;
        if (processing_thread_.joinable()) {
            processing_thread_.join();
        }
    }

private:
    // Params
    std::string sp_blob_, yolo_blob_;
    bool enable_yolo_, publish_tf_, enable_clahe_, use_bruteforce_;
    double yolo_freq_, yolo_conf_thresh_, yolo_iou_thresh_;
    double filter_alpha_, min_depth_, max_depth_, depth_fps_;
    int min_features_, min_inliers_, depth_pub_w_, depth_pub_h_;

    // ROS
    rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr pub_odom_;
    rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr pub_depth_, pub_rgb_;
    rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr pub_debug_;
    rclcpp::Publisher<std_msgs::msg::Float32>::SharedPtr pub_quality_;
    rclcpp::Publisher<sensor_msgs::msg::CameraInfo>::SharedPtr pub_camera_info_;
    rclcpp::Publisher<vision_msgs::msg::Detection2DArray>::SharedPtr pub_detections_;
    rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr pub_imu_;  // IMU publisher
    std::unique_ptr<tf2_ros::TransformBroadcaster> tf_broadcaster_;
    rclcpp::TimerBase::SharedPtr timer_odom_, timer_info_;

    // Pipeline
    std::unique_ptr<dai::Device> device_;
    std::shared_ptr<dai::Pipeline> pipeline_;
    bool has_sp_ = false, has_yolo_ = false;
    double fx_ = 0, fy_ = 0, cx_ = 0, cy_ = 0;
    cv::Mat K_depth_, K_sp_;

    // Odometry State
    cv::Mat pose_matrix_; // 4x4
    std::mutex pose_mutex_;
    cv::Mat last_descs_;
    std::vector<cv::Point3f> last_pts3d_;
    std::vector<cv::KeyPoint> last_kpts_;
    std::mutex vo_mutex_;
    
    cv::Ptr<cv::CLAHE> clahe_;
    cv::Ptr<cv::DescriptorMatcher> matcher_;
    std::deque<int> inliers_history_;

    // EMA Filter
    cv::Vec3f f_pos_ = {0,0,0};
    tf2::Quaternion f_quat_ = tf2::Quaternion::getIdentity();
    bool first_filter_ = true;

    // Threading
    std::thread processing_thread_;
    std::atomic<bool> running_;
    
    // Constants
    const int SP_W = 480;
    const int SP_H = 360;
    const int DEPTH_W = 640;
    const int DEPTH_H = 400;

    void imuCallback(std::shared_ptr<dai::ADatatype> data) {
        if (!rclcpp::ok()) return;
        std::shared_ptr<dai::IMUData> imuData = std::dynamic_pointer_cast<dai::IMUData>(data);
        if (!imuData) return;
        
        auto current_time = this->now();
        // Calculate ~timestamp for batch if needed, or just use current_time
        // For simplicity and to avoid drift, we use current_time but knowing it's a batch
        // A better approach would be to interpolate based on rate, but for now this fixes the 20Hz issue
        
        for (const auto& packet : imuData->packets) {
            sensor_msgs::msg::Imu imu_msg;
            imu_msg.header.stamp = current_time; // Ideally: packet.acceleroMeter.timestamp.get() -> ROS time
            imu_msg.header.frame_id = "imu_link";
            
            // Accelerometer (raw)
            auto accel = packet.acceleroMeter;
            imu_msg.linear_acceleration.x = accel.x;
            imu_msg.linear_acceleration.y = accel.y;
            imu_msg.linear_acceleration.z = accel.z;
            
            // Gyroscope (raw)
            auto gyro = packet.gyroscope;
            imu_msg.angular_velocity.x = gyro.x;
            imu_msg.angular_velocity.y = gyro.y;
            imu_msg.angular_velocity.z = gyro.z;
            
            // Orientation (unknown)
            imu_msg.orientation.w = 1.0;
            imu_msg.orientation.x = 0.0;
            imu_msg.orientation.y = 0.0;
            imu_msg.orientation.z = 0.0;
            imu_msg.orientation_covariance[0] = -1.0;
            
            // Covariances
            imu_msg.linear_acceleration_covariance = {0.01, 0, 0,  0, 0.01, 0,  0, 0, 0.01};
            imu_msg.angular_velocity_covariance = {0.001, 0, 0,  0, 0.001, 0,  0, 0, 0.001};
            
            pub_imu_->publish(imu_msg);
        }
    }

    bool setup_pipeline() {
        pipeline_ = std::make_shared<dai::Pipeline>();

        auto monoLeft = pipeline_->create<dai::node::MonoCamera>();
        auto monoRight = pipeline_->create<dai::node::MonoCamera>();
        auto stereo = pipeline_->create<dai::node::StereoDepth>();

        // Config
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
        stereo->setSubpixel(false);
        auto config = stereo->initialConfig.get();
        config.postProcessing.thresholdFilter.minRange = (int)(min_depth_ * 1000);
        config.postProcessing.thresholdFilter.maxRange = (int)(max_depth_ * 1000);
        stereo->initialConfig.set(config);

        monoLeft->out.link(stereo->left);
        monoRight->out.link(stereo->right);

        // IMU Setup
        auto imu = pipeline_->create<dai::node::IMU>();
        imu->enableIMUSensor({dai::IMUSensor::ACCELEROMETER_RAW, 
                              dai::IMUSensor::GYROSCOPE_RAW}, 100);  // 100 Hz - BMI270 only supports RAW
        imu->setBatchReportThreshold(1);
        imu->setMaxBatchReports(10);
        
        auto xoutImu = pipeline_->create<dai::node::XLinkOut>();
        xoutImu->setStreamName("imu");
        imu->out.link(xoutImu->input);

        // IMU Callback (threaded)
        // We use a separate thread/callback to ensure 100Hz rate even if VO is slow
        // Note: We need to bind the callback AFTER device starts, or use addCallback later.
        // But depthai-core allows adding callback on OutputQueue which is created after device start.
        // So here we just link. Callback is added in process_frames (or better: device start)
        // See process_frames for callback registration.

        // SuperPoint
        std::ifstream f(sp_blob_.c_str());
        if (f.good()) {
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
        }

        // YOLO
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

        // K_sp (scaled)
        double sw = (double)SP_W / DEPTH_W;
        double sh = (double)SP_H / DEPTH_H;
        K_sp_ = K_depth_.clone();
        K_sp_.at<double>(0,0) *= sw; K_sp_.at<double>(1,1) *= sh;
        K_sp_.at<double>(0,2) *= sw; K_sp_.at<double>(1,2) *= sh;

        RCLCPP_INFO(get_logger(), "Calib: fx=%.1f, fy=%.1f", fx_, fy_);
        return true;
    }

    void process_frames() {
        try {
            auto qRect = device_->getOutputQueue("rect", 4, false);
            auto qDepth = device_->getOutputQueue("depth", 4, false);
            auto qImu = device_->getOutputQueue("imu", 50, false);
            qImu->addCallback(std::bind(&OakSuperPointOdometry::imuCallback, this, std::placeholders::_1));
            std::shared_ptr<dai::DataOutputQueue> qSp, qYolo, qRgb;
            
            if (has_sp_) qSp = device_->getOutputQueue("superpoint", 4, false);
            if (has_yolo_) {
                qYolo = device_->getOutputQueue("yolo", 4, false);
                qRgb = device_->getOutputQueue("rgb", 4, false);
            }

            int frame_count = 0;
            auto last_log = std::chrono::steady_clock::now();

            while (running_ && rclcpp::ok()) {
                try {
                    auto rect = qRect->tryGet<dai::ImgFrame>();
                    auto depth = qDepth->tryGet<dai::ImgFrame>();
                    
                    if (!rect || !depth) {
                        std::this_thread::sleep_for(1ms);
                        continue;
                    }

                    auto ts = this->now();
                    frame_count++;

                    // IMU is now handled via callback!

                    // Log stats every 100 frames
                    auto now = std::chrono::steady_clock::now();
                    if (std::chrono::duration_cast<std::chrono::seconds>(now - last_log).count() >= 10) {
                        RCLCPP_INFO(get_logger(), "Frames: %d, RGB subs: %zu, Depth subs: %zu", 
                            frame_count, pub_rgb_->get_subscription_count(), pub_depth_->get_subscription_count());
                        last_log = now;
                        frame_count = 0;
                    }

                    // Process VO
                    if (qSp) {
                        auto sp = qSp->tryGet<dai::NNData>();
                        if (sp) {
                            process_vo(sp, rect->getCvFrame(), depth->getCvFrame(), ts);
                        }
                    }

                    // Publish Depth and CameraInfo (synchronized)
                    try {
                        cv::Mat frame = depth->getCvFrame(); // CV_16UC1
                        if (frame.cols == DEPTH_W && frame.rows == DEPTH_H) {
                            if (depth_pub_w_ != DEPTH_W) {
                                cv::resize(frame, frame, cv::Size(depth_pub_w_, depth_pub_h_), 0, 0, cv::INTER_NEAREST);
                            }
                            std_msgs::msg::Header header;
                            header.stamp = ts;
                            header.frame_id = "left_optical_frame";
                            auto msg = cv_bridge::CvImage(header, "mono16", frame).toImageMsg();
                            pub_depth_->publish(*msg);

                            // Publish CameraInfo with same timestamp and scaled intrinsics
                            sensor_msgs::msg::CameraInfo info;
                            info.header = header; // Same timestamp and frame_id
                            info.width = depth_pub_w_;  // Published image dimensions
                            info.height = depth_pub_h_;
                            info.distortion_model = "plumb_bob";
                            info.d = {0.0, 0.0, 0.0, 0.0, 0.0};
                            
                            // Scale intrinsics to match published resolution
                            double scale_x = (double)depth_pub_w_ / DEPTH_W;
                            double scale_y = (double)depth_pub_h_ / DEPTH_H;
                            double fx_pub = fx_ * scale_x;
                            double fy_pub = fy_ * scale_y;
                            double cx_pub = cx_ * scale_x;
                            double cy_pub = cy_ * scale_y;
                            
                            // K matrix (3x3)
                            info.k[0] = fx_pub; info.k[1] = 0.0;    info.k[2] = cx_pub;
                            info.k[3] = 0.0;    info.k[4] = fy_pub; info.k[5] = cy_pub;
                            info.k[6] = 0.0;    info.k[7] = 0.0;    info.k[8] = 1.0;
                            
                            // P matrix (3x4) - complete
                            info.p[0] = fx_pub; info.p[1] = 0.0;    info.p[2] = cx_pub; info.p[3] = 0.0;
                            info.p[4] = 0.0;    info.p[5] = fy_pub; info.p[6] = cy_pub; info.p[7] = 0.0;
                            info.p[8] = 0.0;    info.p[9] = 0.0;    info.p[10] = 1.0;   info.p[11] = 0.0;
                            
                            // R matrix (identity for rectified)
                            info.r[0] = 1.0; info.r[1] = 0.0; info.r[2] = 0.0;
                            info.r[3] = 0.0; info.r[4] = 1.0; info.r[5] = 0.0;
                            info.r[6] = 0.0; info.r[7] = 0.0; info.r[8] = 1.0;
                            
                            pub_camera_info_->publish(info);
                        }
                    } catch (const std::exception& e) {
                        RCLCPP_ERROR(get_logger(), "Depth publish error: %s", e.what());
                    }

                    // RGB or Mono-as-RGB
                    try {
                        if (has_yolo_ && qYolo && qRgb) {
                            auto yolo = qYolo->tryGet<dai::ImgDetections>();
                            auto rgb = qRgb->tryGet<dai::ImgFrame>();
                            if (yolo && rgb) {
                                process_yolo(yolo, rgb->getCvFrame(), ts);
                            }
                        } else if (!has_yolo_) {
                            // Publish Mono as RGB (resized)
                            cv::Mat gray = rect->getCvFrame();
                            cv::Mat bgr;
                            cv::cvtColor(gray, bgr, cv::COLOR_GRAY2BGR);
                            cv::resize(bgr, bgr, cv::Size(depth_pub_w_, depth_pub_h_));
                            std_msgs::msg::Header header;
                            header.stamp = ts;
                            header.frame_id = "left_optical_frame";
                            auto msg = cv_bridge::CvImage(header, "bgr8", bgr).toImageMsg();
                            pub_rgb_->publish(*msg);
                        }
                    } catch (const std::exception& e) {
                        RCLCPP_ERROR(get_logger(), "RGB publish error: %s", e.what());
                    }
                } catch (const std::exception& e) {
                    RCLCPP_ERROR(get_logger(), "Frame processing error: %s", e.what());
                    std::this_thread::sleep_for(100ms);
                }
            }
            RCLCPP_INFO(get_logger(), "Process frames thread exiting");
        } catch (const std::exception& e) {
            RCLCPP_FATAL(get_logger(), "Fatal error in process_frames: %s", e.what());
        }
    }

    void process_vo(std::shared_ptr<dai::NNData> sp, cv::Mat gray, cv::Mat depth, rclcpp::Time ts) {
        std::vector<cv::KeyPoint> kpts;
        cv::Mat descs;
        
        if (!decode_superpoint(sp, kpts, descs)) return;

        // CLAHE
        if (enable_clahe_) clahe_->apply(gray, gray);

        // Backproject
        std::vector<cv::Point3f> pts3d;
        std::vector<int> valid_idx;
        
        // Scale factors SuperPoint -> Depth
        float sx = (float)DEPTH_W / SP_W;
        float sy = (float)DEPTH_H / SP_H;

        for(size_t i=0; i<kpts.size(); i++) {
            int u = kpts[i].pt.x * sx;
            int v = kpts[i].pt.y * sy;
            if (u >=0 && u < DEPTH_W && v >=0 && v < DEPTH_H) {
                float z = (float)depth.at<uint16_t>(v, u) / 1000.0f;
                if (z > min_depth_ && z < max_depth_) {
                    float X = (u - cx_) * z / fx_;
                    float Y = (v - cy_) * z / fy_;
                    pts3d.emplace_back(X, Y, z);
                    valid_idx.push_back(i);
                }
            }
        }

        int n_matches = 0;
        int n_inliers = 0;

        {
            std::lock_guard<std::mutex> lock(vo_mutex_);
            if (!last_pts3d_.empty() && pts3d.size() >= (size_t)min_features_) {
                // Match
                std::vector<cv::Mat> current_descs_vec;
                // Filter descs by valid_idx
                cv::Mat valid_descs;
                if (!valid_idx.empty()) {
                   std::vector<cv::Mat> rows;
                   for(int idx : valid_idx) rows.push_back(descs.row(idx));
                   cv::vconcat(rows, valid_descs);
                }

                if (!valid_descs.empty() && !last_descs_.empty()) {
                    std::vector<std::vector<cv::DMatch>> knn_matches;
                    matcher_->knnMatch(valid_descs, last_descs_, knn_matches, 2);

                    std::vector<cv::Point3f> obj_pts;
                    std::vector<cv::Point2f> img_pts;
                    
                    for(auto &m : knn_matches) {
                        if(m.size() == 2 && m[0].distance < 0.75f * m[1].distance) {
                            obj_pts.push_back(last_pts3d_[m[0].trainIdx]);
                            img_pts.push_back(kpts[valid_idx[m[0].queryIdx]].pt);
                            n_matches++;
                        }
                    }

                    if (n_matches >= min_inliers_) {
                        cv::Mat rvec, tvec;
                        std::vector<int> inliers;
                        bool success = cv::solvePnPRansac(obj_pts, img_pts, K_sp_, cv::Mat(), rvec, tvec, 
                            false, 100, 3.0f, 0.99, inliers, cv::SOLVEPNP_EPNP);
                        
                        if (success && inliers.size() >= (size_t)min_inliers_) {
                            n_inliers = inliers.size();
                            update_pose(rvec, tvec);
                            publish_odometry();
                            inliers_history_.push_back(n_inliers);
                            if (inliers_history_.size() > 10) inliers_history_.pop_front();
                            
                            // if (n_inliers < 15) RCLCPP_WARN(get_logger(), "Low inliers: %d", n_inliers);
                        } else {
                            RCLCPP_WARN(get_logger(), "PnP Failed! Success: %d, Inliers: %zu", success, inliers.size());
                        }

                    }
                }
            }

            // Update History
            last_pts3d_ = pts3d;
            
            // Build valid_descs again for next frame
            cv::Mat valid_copy;
             if (!valid_idx.empty()) {
                   std::vector<cv::Mat> rows;
                   for(int idx : valid_idx) rows.push_back(descs.row(idx));
                   cv::vconcat(rows, valid_copy);
            }
            last_descs_ = valid_copy; 
            last_kpts_.clear();
            for(int idx : valid_idx) last_kpts_.push_back(kpts[idx]);
        }

        // Publish Quality
        float quality = (n_matches > 0) ? (float)n_inliers / n_matches : 0.0f;
        std_msgs::msg::Float32 q_msg;
        q_msg.data = quality;
        pub_quality_->publish(q_msg);

        // Debug Image
        if (pub_debug_->get_subscription_count() > 0) {
            cv::Mat debug_img;
            cv::cvtColor(gray, debug_img, cv::COLOR_GRAY2BGR);
            cv::drawKeypoints(debug_img, kpts, debug_img, cv::Scalar(0,255,0), cv::DrawMatchesFlags::DEFAULT);
            std_msgs::msg::Header h;
            h.stamp = ts;
            h.frame_id = "left_optical_frame";
            sensor_msgs::msg::CompressedImage cmsg;
            cmsg.header = h;
            cmsg.format = "jpeg";
            cv::imencode(".jpg", debug_img, cmsg.data);
            pub_debug_->publish(cmsg);
        }
    }

    bool decode_superpoint(std::shared_ptr<dai::NNData> sp, std::vector<cv::KeyPoint>& kpts, cv::Mat& descs) {
        // Output layers: heatmap(1x65x45x60), desc(1x256x45x60)
        // Check size heuristic
        std::vector<float> data_hm, data_desc;
        
        // This relies on the blob output names or size heuristic
        // Let's use size heuristic from Python code to be safe
        for(auto& layer : sp->getAllLayerNames()) {
            auto vec = sp->getLayerFp16(layer);
            if (vec.size() > 500000) { // Descriptors: 256*45*60 ~ 690k
                data_desc = vec; // Copy :(
            } else if (vec.size() > 100000) { // Heatmap: 65*45*60 ~ 175k
                data_hm = vec;
            }
        }
        
        if (data_hm.empty() || data_desc.empty()) return false;

        const int H_grid = SP_H / 8; // 45
        const int W_grid = SP_W / 8; // 60
        
        // Pixel Shuffle & Threshold
        // Input: 65 channels. 64 are 8x8 blocks. Last is dustbin.
        
        // We do NMS on the fly or dense map?
        // Python does: reshape -> 65x45x60. Remove dustbin. Pixel shuffle.
        // Simplified Logic: 
        
        cv::Mat prob = cv::Mat::zeros(SP_H, SP_W, CV_32F);
        float* prob_ptr = (float*)prob.data;

        // Optimized Shuffle
        for (int h=0; h<H_grid; h++) {
            for (int w=0; w<W_grid; w++) {
                int px_base = h * 8 * SP_W + w * 8;
                // For each channel 0..63
                for (int c=0; c<64; c++) {
                    int dy = c / 8;
                    int dx = c % 8;
                    // value index in planar buffer: c * H_grid * W_grid + h * W_grid + w
                    // Note: DepthAI usually planar
                    int idx = c * H_grid * W_grid + h * W_grid + w;
                    float val = data_hm[idx];
                    prob_ptr[px_base + dy * SP_W + dx] = val;
                }
            }
        }
        
        // Threshold
        cv::Mat mask;
        cv::compare(prob, 0.015, mask, cv::CMP_GT); // Threshold 0.015

        // NMS (Simple 3x3 max)
        cv::Mat dilated;
        cv::dilate(prob, dilated, cv::Mat());
        cv::Mat peaks;
        cv::compare(prob, dilated, peaks, cv::CMP_EQ);
        
        cv::bitwise_and(peaks, mask, peaks); // peaks & mask
        
        // Extract Keypoints
        std::vector<cv::Point> pts;
        cv::findNonZero(peaks, pts);
        
        if (pts.empty()) return false;
        
        // Extract Descriptors form 256x45x60
        // Coarse coordinates
        cv::Mat desc_mat(pts.size(), 256, CV_32F);
        
        for(size_t i=0; i<pts.size(); i++) {
            kpts.emplace_back((float)pts[i].x, (float)pts[i].y, prob.at<float>(pts[i]));
            
            int gx = std::min(pts[i].x / 8, W_grid-1);
            int gy = std::min(pts[i].y / 8, H_grid-1);
            
            // Bilinear logic omitted for speed, using nearest neighbor from grid
            // Descriptor index: c * H * W + gy * W + gx
            for(int c=0; c<256; c++) {
                desc_mat.at<float>(i, c) = data_desc[c * H_grid * W_grid + gy * W_grid + gx];
            }
        }
        
        // Normalize L2
        for(int i=0; i<desc_mat.rows; i++) {
            cv::normalize(desc_mat.row(i), desc_mat.row(i));
        }
        
        descs = desc_mat;
        return true;
    }

    void update_pose(cv::Mat rvec, cv::Mat tvec) {
        std::lock_guard<std::mutex> lock(pose_mutex_);
        cv::Mat R;
        cv::Rodrigues(rvec, R);
        
        // T_delta (Cam(t-1) -> Cam(t))
        cv::Mat T_delta = cv::Mat::eye(4, 4, CV_64F);
        R.copyTo(T_delta(cv::Rect(0,0,3,3)));
        tvec.copyTo(T_delta(cv::Rect(3,0,1,3))); // col 3, row 0..2
        
        T_delta = T_delta.inv(); // Camera motion
        
        // Convert Optical -> Base
        // R_opt (z->x, -x->y, -y->z)
        cv::Mat R_opt = (cv::Mat_<double>(3,3) << 0,0,1, -1,0,0, 0,-1,0); 
        cv::Mat T_robot = cv::Mat::eye(4, 4, CV_64F);
        
        // R_rob = R_opt * R_cam * R_opt.t()
        // t_rob = R_opt * t_cam
        cv::Mat R_cam = T_delta(cv::Rect(0,0,3,3));
        cv::Mat t_cam = T_delta(cv::Rect(3,0,1,3));
        
        cv::Mat R_rob = R_opt * R_cam * R_opt.t();
        cv::Mat t_rob = R_opt * t_cam;
        
        R_rob.copyTo(T_robot(cv::Rect(0,0,3,3)));
        t_rob.copyTo(T_robot(cv::Rect(3,0,1,3)));
        
        pose_matrix_ = pose_matrix_ * T_robot;
    }

    void process_yolo(std::shared_ptr<dai::ImgDetections> detections, cv::Mat rgb, rclcpp::Time ts) {
        vision_msgs::msg::Detection2DArray msg;
        msg.header.stamp = ts;
        msg.header.frame_id = "camera_color_optical_frame";
        
        for(auto& det : detections->detections) {
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
    
    void publish_odometry() {
        cv::Mat pose;
        {
            std::lock_guard<std::mutex> lock(pose_mutex_);
            pose = pose_matrix_.clone();
        }
        
        // Extract Pos
        double x = pose.at<double>(0,3);
        double y = pose.at<double>(1,3);
        double z = pose.at<double>(2,3);
        
        // Extract Rot
        cv::Mat R = pose(cv::Rect(0,0,3,3));
        tf2::Matrix3x3 tfR(
            R.at<double>(0,0), R.at<double>(0,1), R.at<double>(0,2),
            R.at<double>(1,0), R.at<double>(1,1), R.at<double>(1,2),
            R.at<double>(2,0), R.at<double>(2,1), R.at<double>(2,2)
        );
        tf2::Quaternion q; 
        tfR.getRotation(q);
        
        // EMA
        if (first_filter_) {
            f_pos_ = { (float)x, (float)y, (float)z };
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
        
        // Covariance
        double scale = 1.0;
        if (!inliers_history_.empty()) {
             double avg = 0; 
             for(int n : inliers_history_) avg += n;
             avg /= inliers_history_.size();
             scale = 1.0 / std::max(1.0, avg);
        }
        odom.pose.covariance[0] = 0.01 + scale * 0.1;
        odom.pose.covariance[7] = 0.01 + scale * 0.1;
        odom.pose.covariance[35] = 0.05 + scale * 0.2;
        
        pub_odom_->publish(odom);
        
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

    // Removed - now published inline with depth frames for synchronization
};

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    try {
        rclcpp::spin(std::make_shared<OakSuperPointOdometry>());
    } catch(const std::exception& e) {
        std::cerr << "Node Exception: " << e.what() << std::endl;
    }
    rclcpp::shutdown();
    return 0;
}
