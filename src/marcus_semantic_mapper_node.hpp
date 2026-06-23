#pragma once

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <visualization_msgs/msg/marker_array.hpp>
#include <rtabmap_msgs/msg/user_data.hpp>
#include <robopy_controller/msg/semantic_object.hpp>
#include <robopy_controller/msg/semantic_object_array.hpp>

#include <message_filters/subscriber.h>
#include <message_filters/synchronizer.h>
#include <message_filters/sync_policies/approximate_time.h>

#include <tf2_ros/transform_listener.h>
#include <tf2_ros/buffer.h>
#include <cv_bridge/cv_bridge.hpp>
#include <opencv2/opencv.hpp>
#include <Eigen/Core>
#include <Eigen/Geometry>

#include <mutex>
#include <atomic>
#include <array>
#include <string>
#include <memory>
#include <thread>

/**
 * @brief ROS 2 C++17 Node for Fusing Hailo-10H Detections with Depth Maps.
 * 
 * Sincronizza i frame RGB/Depth/SemanticObjectArray e proietta i bounding box 
 * in 3D nello spazio camera ed in base_link (usando Eigen, senza dipendere da PCL).
 * Produce rtabmap_msgs/msg/UserData binari serializzati per RTAB-Map.
 */
class MarcusSemanticMapperNode : public rclcpp::Node {
public:
    explicit MarcusSemanticMapperNode(const rclcpp::NodeOptions& options = rclcpp::NodeOptions());
    ~MarcusSemanticMapperNode() override;

private:
    // ===================== Structs =====================
    struct Config {
        std::string camera_frame = "camera_optical_frame";
        std::string base_frame   = "base_link";
        std::string map_frame    = "map";
        std::string odom_frame   = "odom";
        
        double min_depth_m = 0.3;
        double max_depth_m = 6.0;
        int depth_roi_margin_px = 2;
        
        int max_objects_per_frame = 20;
        double min_confidence = 0.35;
        int depth_sample_grid = 5;
        
        double attention_dynamic_weight = 2.0;
        double attention_decay_rate = 0.95;
        
        bool publish_debug = false;
        double diag_period_sec = 2.0;
        int max_queue_depth = 10;
    };

    struct SemanticObject3D {
        Eigen::Vector3d centroid_cam;    // In camera optical frame (X=right, Y=down, Z=forward)
        Eigen::Vector3d centroid_base;   // In base_link frame (X=forward, Y=left, Z=up)
        std::array<float, 4> bbox_norm;  // [xmin, ymin, xmax, ymax] normalizzato
        float confidence;
        float depth_m;
        float width_m;
        float depth_extent_m;
        int class_id;
        char label[32];
        char semantic_class[16];
        float attention_score;
    };

    static constexpr size_t MAX_OBJECTS = 20;

    // ===================== Core Pipeline =====================
    void syncCallback(
        const sensor_msgs::msg::Image::ConstSharedPtr& rgb_msg,
        const sensor_msgs::msg::Image::ConstSharedPtr& depth_msg,
        const robopy_controller::msg::SemanticObjectArray::ConstSharedPtr& semantic_msg);

    void cameraInfoCallback(const sensor_msgs::msg::CameraInfo::SharedPtr msg);

    // ===================== Math & Helpers =====================
    bool isClassDynamic(const std::string& label) const;
    double computeMedianDepth(const cv::Mat& depth_mat, int u_min, int v_min, int u_max, int v_max);
    bool lookupTransforms(const rclcpp::Time& stamp);

    // ===================== Publishers =====================
    void publishUserData(const rclcpp::Time& stamp);
    void publishSemanticObjects(const rclcpp::Time& stamp);
    void publishMarkers(const rclcpp::Time& stamp);
    void publishDiagnostics(const rclcpp::Time& stamp);

    // ===================== State & Variables =====================
    Config config_;
    
    // Message synchronization
    message_filters::Subscriber<sensor_msgs::msg::Image> rgb_sub_;
    message_filters::Subscriber<sensor_msgs::msg::Image> depth_sub_;
    message_filters::Subscriber<robopy_controller::msg::SemanticObjectArray> semantic_sub_;
    rclcpp::Subscription<sensor_msgs::msg::CameraInfo>::SharedPtr camera_info_sub_;

    using SyncPolicy = message_filters::sync_policies::ApproximateTime<
        sensor_msgs::msg::Image,
        sensor_msgs::msg::Image,
        robopy_controller::msg::SemanticObjectArray>;
    std::shared_ptr<message_filters::Synchronizer<SyncPolicy>> sync_;

    // TF Listeners
    std::shared_ptr<tf2_ros::Buffer> tf_buffer_;
    std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
    Eigen::Isometry3d T_base_camera_ = Eigen::Isometry3d::Identity();
    std::atomic<bool> transform_initialized_{false};

    // Camera Intrinsics
    std::atomic<bool> camera_info_received_{false};
    double fx_ = 0.0, fy_ = 0.0, cx_ = 0.0, cy_ = 0.0;
    std::mutex intrinsics_mutex_;

    // Buffers (Zero allocation hot-path)
    std::mutex data_mutex_;
    std::array<SemanticObject3D, MAX_OBJECTS> object_buffer_{};
    size_t object_count_ = 0;

    // ROS 2 Publishers
    rclcpp::Publisher<rtabmap_msgs::msg::UserData>::SharedPtr user_data_pub_;
    rclcpp::Publisher<robopy_controller::msg::SemanticObjectArray>::SharedPtr objects_3d_pub_;
    rclcpp::Publisher<visualization_msgs::msg::MarkerArray>::SharedPtr marker_pub_;
    rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diag_pub_;

    // Diagnostics and Latency tracking
    std::atomic<uint64_t> processed_frames_{0};
    rclcpp::Time last_diag_time_;
    rclcpp::Time start_time_;
    std::atomic<double> last_processing_latency_ms_{0.0};
};
