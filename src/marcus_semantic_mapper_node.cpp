#include "marcus_semantic_mapper_node.hpp"
#include <tf2_eigen/tf2_eigen.hpp>
#include <sensor_msgs/image_encodings.hpp>
#include <algorithm>
#include <cstring>
#include <vector>

MarcusSemanticMapperNode::MarcusSemanticMapperNode(const rclcpp::NodeOptions& options)
    : Node("marcus_semantic_mapper", options) {
    
    RCLCPP_INFO(this->get_logger(), "🔧 Configurazione marcus_semantic_mapper...");

    // Parameters setup
    config_.camera_frame = this->declare_parameter("camera_frame", config_.camera_frame);
    config_.base_frame   = this->declare_parameter("base_frame", config_.base_frame);
    config_.map_frame    = this->declare_parameter("map_frame", config_.map_frame);
    config_.odom_frame   = this->declare_parameter("odom_frame", config_.odom_frame);
    config_.min_depth_m  = this->declare_parameter("min_depth_m", config_.min_depth_m);
    config_.max_depth_m  = this->declare_parameter("max_depth_m", config_.max_depth_m);
    config_.depth_roi_margin_px = this->declare_parameter("depth_roi_margin_px", config_.depth_roi_margin_px);
    config_.max_objects_per_frame = this->declare_parameter("max_objects_per_frame", config_.max_objects_per_frame);
    config_.min_confidence = this->declare_parameter("min_confidence", config_.min_confidence);
    config_.depth_sample_grid = this->declare_parameter("depth_sample_grid", config_.depth_sample_grid);
    config_.attention_dynamic_weight = this->declare_parameter("attention_dynamic_weight", config_.attention_dynamic_weight);
    config_.attention_decay_rate = this->declare_parameter("attention_decay_rate", config_.attention_decay_rate);
    config_.publish_debug = this->declare_parameter("publish_debug", config_.publish_debug);
    config_.diag_period_sec = this->declare_parameter("diag_period_sec", config_.diag_period_sec);
    config_.max_queue_depth = this->declare_parameter("max_queue_depth", config_.max_queue_depth);

    // Initialize TF buffer and listener
    tf_buffer_ = std::make_shared<tf2_ros::Buffer>(this->get_clock());
    tf_listener_ = std::make_shared<tf2_ros::TransformListener>(*tf_buffer_);

    // Subscriptions
    camera_info_sub_ = this->create_subscription<sensor_msgs::msg::CameraInfo>(
        "/camera/camera_info", 10,
        std::bind(&MarcusSemanticMapperNode::cameraInfoCallback, this, std::placeholders::_1));

    // Message filters setup
    // Use BestEffort QoS profile for high frequency camera topics
    rmw_qos_profile_t qos_best_effort = rmw_qos_profile_sensor_data;
    qos_best_effort.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
    qos_best_effort.depth = config_.max_queue_depth;

    rgb_sub_.subscribe(this, "/rgb/image", qos_best_effort);
    depth_sub_.subscribe(this, "/camera/depth/image_raw", qos_best_effort);
    semantic_sub_.subscribe(this, "/hailo/vlm/semantic_objects", rclcpp::QoS(10).get_rmw_qos_profile());

    sync_ = std::make_shared<message_filters::Synchronizer<SyncPolicy>>(
        SyncPolicy(config_.max_queue_depth), rgb_sub_, depth_sub_, semantic_sub_);
    sync_->registerCallback(std::bind(&MarcusSemanticMapperNode::syncCallback, this,
        std::placeholders::_1, std::placeholders::_2, std::placeholders::_3));

    // Publishers
    user_data_pub_ = this->create_publisher<rtabmap_msgs::msg::UserData>("/rtabmap/user_data", 5);
    objects_3d_pub_ = this->create_publisher<robopy_controller::msg::SemanticObjectArray>("/semantic_mapper/objects_3d", 5);
    
    if (config_.publish_debug) {
        marker_pub_ = this->create_publisher<visualization_msgs::msg::MarkerArray>("/semantic_mapper/markers", 1);
    }
    diag_pub_ = this->create_publisher<diagnostic_msgs::msg::DiagnosticArray>("/semantic_mapper/diagnostics", 1);

    start_time_ = this->get_clock()->now();
    last_diag_time_ = start_time_;

    RCLCPP_INFO(this->get_logger(), "✅ Marcus Semantic Mapper inizializzato con successo!");
}

MarcusSemanticMapperNode::~MarcusSemanticMapperNode() {
    RCLCPP_INFO(this->get_logger(), "Chiusura marcus_semantic_mapper...");
}

void MarcusSemanticMapperNode::cameraInfoCallback(const sensor_msgs::msg::CameraInfo::SharedPtr msg) {
    if (camera_info_received_) {
        return;
    }
    std::lock_guard<std::mutex> lock(intrinsics_mutex_);
    fx_ = msg->k[0]; // K is row-major 3x3: [fx, 0, cx, 0, fy, cy, 0, 0, 1]
    fy_ = msg->k[4];
    cx_ = msg->k[2];
    cy_ = msg->k[5];
    camera_info_received_ = true;
    RCLCPP_INFO(this->get_logger(), "📷 Parametri intrinseci caricati: fx=%.2f, fy=%.2f, cx=%.2f, cy=%.2f",
                fx_, fy_, cx_, cy_);
}

bool MarcusSemanticMapperNode::lookupTransforms(const rclcpp::Time& stamp) {
    if (transform_initialized_) {
        return true;
    }
    try {
        auto tf_msg = tf_buffer_->lookupTransform(
            config_.base_frame,
            config_.camera_frame,
            stamp,
            tf2::durationFromSec(0.1));
        T_base_camera_ = tf2::transformToEigen(tf_msg);
        transform_initialized_ = true;
        RCLCPP_INFO(this->get_logger(), "📌 Trasformata Camera-to-Base agganciata con successo: %s -> %s",
            config_.base_frame.c_str(), config_.camera_frame.c_str());
        return true;
    } catch (const tf2::TransformException& ex) {
        RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 5000,
            "Impossibile agganciare la trasformata %s -> %s: %s",
            config_.base_frame.c_str(), config_.camera_frame.c_str(), ex.what());
        return false;
    }
}

bool MarcusSemanticMapperNode::isClassDynamic(const std::string& label) const {
    // Classi dinamiche COCO comuni o rilevate dal VLM
    return (label == "person" || label == "persona" ||
            label == "cat" || label == "gatto" ||
            label == "dog" || label == "cane" ||
            label == "human" || label == "uomo" || label == "donna");
}

double MarcusSemanticMapperNode::computeMedianDepth(const cv::Mat& depth_mat, int u_min, int v_min, int u_max, int v_max) {
    int margin = config_.depth_roi_margin_px;
    u_min = std::max(0, u_min + margin);
    v_min = std::max(0, v_min + margin);
    u_max = std::min(depth_mat.cols - 1, u_max - margin);
    v_max = std::min(depth_mat.rows - 1, v_max - margin);

    if (u_min >= u_max || v_min >= v_max) {
        return 0.0;
    }

    std::vector<double> valid_depths;
    valid_depths.reserve(config_.depth_sample_grid * config_.depth_sample_grid);

    int step_u = std::max(1, (u_max - u_min) / config_.depth_sample_grid);
    int step_v = std::max(1, (v_max - v_min) / config_.depth_sample_grid);

    for (int v = v_min; v < v_max; v += step_v) {
        for (int u = u_min; u < u_max; u += step_u) {
            uint16_t d_raw = depth_mat.at<uint16_t>(v, u);
            double d_m = d_raw * 0.001; // mm -> m
            if (d_m >= config_.min_depth_m && d_m <= config_.max_depth_m) {
                valid_depths.push_back(d_m);
            }
        }
    }

    if (valid_depths.empty()) {
        return 0.0;
    }

    std::sort(valid_depths.begin(), valid_depths.end());
    return valid_depths[valid_depths.size() / 2];
}

void MarcusSemanticMapperNode::syncCallback(
    const sensor_msgs::msg::Image::ConstSharedPtr& rgb_msg,
    const sensor_msgs::msg::Image::ConstSharedPtr& depth_msg,
    const robopy_controller::msg::SemanticObjectArray::ConstSharedPtr& semantic_msg) {
    
    auto t_start = this->get_clock()->now();

    if (!camera_info_received_) {
        RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 5000,
            "Sincronizzazione ignorata: camera_info non ancora ricevuto!");
        return;
    }

    rclcpp::Time stamp(rgb_msg->header.stamp);
    if (!lookupTransforms(stamp)) {
        return;
    }

    // Convert depth map
    cv_bridge::CvImageConstPtr cv_depth;
    try {
        cv_depth = cv_bridge::toCvShare(depth_msg, sensor_msgs::image_encodings::TYPE_16UC1);
    } catch (const cv_bridge::Exception& e) {
        RCLCPP_ERROR(this->get_logger(), "Errore cv_bridge su depth image: %s", e.what());
        return;
    }

    int depth_width = cv_depth->image.cols;
    int depth_height = cv_depth->image.rows;

    std::vector<SemanticObject3D> detected_objects;
    detected_objects.reserve(semantic_msg->objects.size());

    double local_fx, local_fy, local_cx, local_cy;
    {
        std::lock_guard<std::mutex> lock(intrinsics_mutex_);
        local_fx = fx_;
        local_fy = fy_;
        local_cx = cx_;
        local_cy = cy_;
    }

    for (const auto& obj : semantic_msg->objects) {
        if (obj.confidence < config_.min_confidence) {
            continue;
        }

        // Bounding box: convert normalized [xmin, ymin, xmax, ymax] to pixels
        int u_min = std::max(0, std::min(static_cast<int>(obj.bbox_2d[0] * depth_width), depth_width - 1));
        int v_min = std::max(0, std::min(static_cast<int>(obj.bbox_2d[1] * depth_height), depth_height - 1));
        int u_max = std::max(0, std::min(static_cast<int>(obj.bbox_2d[2] * depth_width), depth_width - 1));
        int v_max = std::max(0, std::min(static_cast<int>(obj.bbox_2d[3] * depth_height), depth_height - 1));

        if (u_min > u_max) std::swap(u_min, u_max);
        if (v_min > v_max) std::swap(v_min, v_max);

        // Robust depth estimation inside ROI
        double z_cam = computeMedianDepth(cv_depth->image, u_min, v_min, u_max, v_max);
        if (z_cam <= 0.0) {
            continue; // Invalid depth
        }

        // 3D back-projection (Camera optical frame)
        double u_c = (u_min + u_max) / 2.0;
        double v_c = (v_min + v_max) / 2.0;
        double x_cam = (u_c - local_cx) * z_cam / local_fx;
        double y_cam = (v_c - local_cy) * z_cam / local_fy;

        Eigen::Vector3d p_cam(x_cam, y_cam, z_cam);
        Eigen::Vector3d p_base = T_base_camera_ * p_cam;

        // Estimated physical width from pixel width
        double bbox_w_px = u_max - u_min;
        double width_m = (bbox_w_px / local_fx) * z_cam;

        // Attention score calculation
        bool dynamic = isClassDynamic(obj.label);
        double attention_weight = dynamic ? config_.attention_dynamic_weight : 1.0;
        double distance_factor = 1.0 / (1.0 + 0.3 * z_cam);
        double attention = obj.confidence * attention_weight * distance_factor;

        SemanticObject3D s_obj;
        s_obj.centroid_cam = p_cam;
        s_obj.centroid_base = p_base;
        s_obj.bbox_norm = {
            static_cast<float>(obj.bbox_2d[0]),
            static_cast<float>(obj.bbox_2d[1]),
            static_cast<float>(obj.bbox_2d[2]),
            static_cast<float>(obj.bbox_2d[3])
        };
        s_obj.confidence = obj.confidence;
        s_obj.depth_m = static_cast<float>(z_cam);
        s_obj.width_m = static_cast<float>(width_m);
        s_obj.depth_extent_m = 0.4f; // Default extent along camera Z axis
        s_obj.class_id = 0; // Standardize if needed
        
        std::strncpy(s_obj.label, obj.label.c_str(), sizeof(s_obj.label) - 1);
        s_obj.label[sizeof(s_obj.label) - 1] = '\0';
        
        std::strncpy(s_obj.semantic_class, obj.semantic_class.c_str(), sizeof(s_obj.semantic_class) - 1);
        s_obj.semantic_class[sizeof(s_obj.semantic_class) - 1] = '\0';
        if (s_obj.semantic_class[0] == '\0') {
            std::strncpy(s_obj.semantic_class, (dynamic ? "person" : "furniture"), sizeof(s_obj.semantic_class) - 1);
        }

        s_obj.attention_score = static_cast<float>(attention);

        detected_objects.push_back(s_obj);

        if (detected_objects.size() >= MAX_OBJECTS) {
            break;
        }
    }

    // Sort objects by attention score descending
    std::sort(detected_objects.begin(), detected_objects.end(), 
        [](const SemanticObject3D& a, const SemanticObject3D& b) {
            return a.attention_score > b.attention_score;
        });

    // Write to pre-allocated buffer with thread safety
    {
        std::lock_guard<std::mutex> lock(data_mutex_);
        object_count_ = std::min(detected_objects.size(), MAX_OBJECTS);
        for (size_t i = 0; i < object_count_; ++i) {
            object_buffer_[i] = detected_objects[i];
        }
    }

    processed_frames_++;

    // Publish all results
    publishUserData(stamp);
    publishSemanticObjects(stamp);
    if (config_.publish_debug) {
        publishMarkers(stamp);
    }

    // Calculate processing latency
    auto t_end = this->get_clock()->now();
    last_processing_latency_ms_ = (t_end - t_start).seconds() * 1000.0;

    if ((t_end - last_diag_time_).seconds() >= config_.diag_period_sec) {
        publishDiagnostics(t_end);
        last_diag_time_ = t_end;
    }
}

void MarcusSemanticMapperNode::publishUserData(const rclcpp::Time& stamp) {
    rtabmap_msgs::msg::UserData ud_msg;
    ud_msg.header.stamp = stamp;
    ud_msg.header.frame_id = config_.base_frame;

    std::vector<uint8_t> buffer;
    
    // Header (4 bytes)
    buffer.push_back('S');
    buffer.push_back('E');
    buffer.push_back('M');
    buffer.push_back('\0');
    
    // Version (1 byte)
    buffer.push_back(0x01);
    
    // Object count (1 byte)
    size_t active_count = 0;
    {
        std::lock_guard<std::mutex> lock(data_mutex_);
        active_count = object_count_;
    }
    uint8_t count = static_cast<uint8_t>(std::min(active_count, static_cast<size_t>(255)));
    buffer.push_back(count);

    // Objects serialization (76 bytes each)
    {
        std::lock_guard<std::mutex> lock(data_mutex_);
        for (size_t i = 0; i < count; ++i) {
            const auto& obj = object_buffer_[i];
            
            // label (32 bytes)
            uint8_t label_buf[32] = {0};
            std::memcpy(label_buf, obj.label, std::min(std::strlen(obj.label), sizeof(label_buf) - 1));
            buffer.insert(buffer.end(), label_buf, label_buf + 32);
            
            // confidence (4 bytes float)
            float conf = obj.confidence;
            uint8_t* conf_bytes = reinterpret_cast<uint8_t*>(&conf);
            buffer.insert(buffer.end(), conf_bytes, conf_bytes + 4);
            
            // centroid_base x, y, z (12 bytes floats)
            float x = static_cast<float>(obj.centroid_base.x());
            float y = static_cast<float>(obj.centroid_base.y());
            float z = static_cast<float>(obj.centroid_base.z());
            uint8_t* x_bytes = reinterpret_cast<uint8_t*>(&x);
            uint8_t* y_bytes = reinterpret_cast<uint8_t*>(&y);
            uint8_t* z_bytes = reinterpret_cast<uint8_t*>(&z);
            buffer.insert(buffer.end(), x_bytes, x_bytes + 4);
            buffer.insert(buffer.end(), y_bytes, y_bytes + 4);
            buffer.insert(buffer.end(), z_bytes, z_bytes + 4);
            
            // width_m, depth_m (8 bytes floats)
            float w = obj.width_m;
            float d = obj.depth_extent_m;
            uint8_t* w_bytes = reinterpret_cast<uint8_t*>(&w);
            uint8_t* d_bytes = reinterpret_cast<uint8_t*>(&d);
            buffer.insert(buffer.end(), w_bytes, w_bytes + 4);
            buffer.insert(buffer.end(), d_bytes, d_bytes + 4);
            
            // semantic_class (16 bytes)
            uint8_t sem_buf[16] = {0};
            std::memcpy(sem_buf, obj.semantic_class, std::min(std::strlen(obj.semantic_class), sizeof(sem_buf) - 1));
            buffer.insert(buffer.end(), sem_buf, sem_buf + 16);
            
            // attention_score (4 bytes float)
            float att = obj.attention_score;
            uint8_t* att_bytes = reinterpret_cast<uint8_t*>(&att);
            buffer.insert(buffer.end(), att_bytes, att_bytes + 4);
        }
    }

    ud_msg.data = buffer;
    user_data_pub_->publish(ud_msg);
}

void MarcusSemanticMapperNode::publishSemanticObjects(const rclcpp::Time& stamp) {
    robopy_controller::msg::SemanticObjectArray array_msg;
    array_msg.header.stamp = stamp;
    array_msg.header.frame_id = config_.camera_frame;

    Eigen::Isometry3d T_map_camera = Eigen::Isometry3d::Identity();
    bool transform_found = false;

    // Prova ad agganciare la posa in map frame (o odom come fallback) per centroid_2d
    try {
        auto tf_msg = tf_buffer_->lookupTransform(
            config_.map_frame, config_.camera_frame, stamp, tf2::durationFromSec(0.02));
        T_map_camera = tf2::transformToEigen(tf_msg);
        transform_found = true;
    } catch (const tf2::TransformException&) {
        try {
            auto tf_msg = tf_buffer_->lookupTransform(
                config_.odom_frame, config_.camera_frame, stamp, tf2::durationFromSec(0.02));
            T_map_camera = tf2::transformToEigen(tf_msg);
            transform_found = true;
        } catch (const tf2::TransformException&) {
            // Usa base_link transform se proprio non c'è odometria/map
            T_map_camera = T_base_camera_;
        }
    }

    std::vector<SemanticObject3D> local_objects;
    {
        std::lock_guard<std::mutex> lock(data_mutex_);
        local_objects.assign(object_buffer_.begin(), object_buffer_.begin() + object_count_);
    }

    for (size_t i = 0; i < local_objects.size(); ++i) {
        const auto& s_obj = local_objects[i];

        robopy_controller::msg::SemanticObject msg_obj;
        msg_obj.header.stamp = stamp;
        msg_obj.header.frame_id = config_.camera_frame;
        msg_obj.label = s_obj.label;
        msg_obj.confidence = s_obj.confidence;
        
        msg_obj.centroid_3d.x = s_obj.centroid_cam.x();
        msg_obj.centroid_3d.y = s_obj.centroid_cam.y();
        msg_obj.centroid_3d.z = s_obj.centroid_cam.z();

        // Proiezione su piano di terra Z=0 nel frame globale
        Eigen::Vector3d p_map = T_map_camera * s_obj.centroid_cam;
        msg_obj.centroid_2d.x = p_map.x();
        msg_obj.centroid_2d.y = p_map.y();
        msg_obj.centroid_2d.z = 0.0;

        msg_obj.bbox_2d = s_obj.bbox_norm;
        msg_obj.estimated_width_m = s_obj.width_m;
        msg_obj.estimated_depth_m = s_obj.depth_extent_m;
        msg_obj.semantic_class = s_obj.semantic_class;

        array_msg.objects.push_back(msg_obj);
    }

    objects_3d_pub_->publish(array_msg);
}

void MarcusSemanticMapperNode::publishMarkers(const rclcpp::Time& stamp) {
    if (!marker_pub_) {
        return;
    }

    visualization_msgs::msg::MarkerArray marker_arr;
    std::vector<SemanticObject3D> local_objects;
    {
        std::lock_guard<std::mutex> lock(data_mutex_);
        local_objects.assign(object_buffer_.begin(), object_buffer_.begin() + object_count_);
    }

    for (size_t i = 0; i < local_objects.size(); ++i) {
        const auto& s_obj = local_objects[i];

        // Marker Box
        visualization_msgs::msg::Marker box;
        box.header.stamp = stamp;
        box.header.frame_id = config_.base_frame;
        box.ns = "semantic_objects_3d";
        box.id = static_cast<int>(i);
        box.type = visualization_msgs::msg::Marker::CUBE;
        box.action = visualization_msgs::msg::Marker::ADD;

        box.pose.position.x = s_obj.centroid_base.x();
        box.pose.position.y = s_obj.centroid_base.y();
        box.pose.position.z = s_obj.centroid_base.z();
        box.pose.orientation.w = 1.0;

        // Size: estimated width, depth_extent, height = 0.3
        box.scale.x = s_obj.depth_extent_m;
        box.scale.y = s_obj.width_m;
        box.scale.z = 0.3;

        // Color based on class
        box.color.a = 0.7f;
        if (std::strcmp(s_obj.semantic_class, "person") == 0) {
            box.color.r = 1.0f; box.color.g = 0.0f; box.color.b = 0.0f; // Red
        } else if (std::strcmp(s_obj.semantic_class, "obstacle") == 0) {
            box.color.r = 1.0f; box.color.g = 0.5f; box.color.b = 0.0f; // Orange
        } else {
            box.color.r = 0.0f; box.color.g = 1.0f; box.color.b = 0.0f; // Green
        }

        // Label Marker
        visualization_msgs::msg::Marker label_text;
        label_text.header = box.header;
        label_text.ns = "semantic_labels_3d";
        label_text.id = static_cast<int>(i + MAX_OBJECTS);
        label_text.type = visualization_msgs::msg::Marker::TEXT_VIEW_FACING;
        label_text.action = visualization_msgs::msg::Marker::ADD;
        
        label_text.pose = box.pose;
        label_text.pose.position.z += 0.25; // Sospeso sopra il cubo

        label_text.scale.z = 0.15; // text height (m)
        label_text.color.r = 1.0f; label_text.color.g = 1.0f; label_text.color.b = 1.0f; label_text.color.a = 1.0f;
        
        label_text.text = std::string(s_obj.label) + " (" + std::to_string(static_cast<int>(s_obj.confidence * 100)) + "%)";

        marker_arr.markers.push_back(box);
        marker_arr.markers.push_back(label_text);
    }

    marker_pub_->publish(marker_arr);
}

void MarcusSemanticMapperNode::publishDiagnostics(const rclcpp::Time& stamp) {
    diagnostic_msgs::msg::DiagnosticArray diag_array;
    diag_array.header.stamp = stamp;

    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "Marcus Semantic Mapper";
    status.level = diagnostic_msgs::msg::DiagnosticStatus::OK;
    status.message = "Semantic Fusion active and processing";
    status.hardware_id = "NPU Hailo-10H + OAK-D Lite";

    diagnostic_msgs::msg::KeyValue kv_fps;
    kv_fps.key = "Processing Rate (Hz)";
    double elapsed = (stamp - start_time_).seconds();
    kv_fps.value = std::to_string(elapsed > 0 ? processed_frames_ / elapsed : 0.0);
    status.values.push_back(kv_fps);

    diagnostic_msgs::msg::KeyValue kv_latency;
    kv_latency.key = "Last Processing Latency (ms)";
    kv_latency.value = std::to_string(last_processing_latency_ms_.load());
    status.values.push_back(kv_latency);

    diagnostic_msgs::msg::KeyValue kv_objects;
    kv_objects.key = "Enriched 3D Object Count";
    size_t count = 0;
    {
        std::lock_guard<std::mutex> lock(data_mutex_);
        count = object_count_;
    }
    kv_objects.value = std::to_string(count);
    status.values.push_back(kv_objects);

    diag_array.status.push_back(status);
    diag_pub_->publish(diag_array);
}

// Entrypoint for compiling as a standalone executable
int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    auto node = std::make_shared<MarcusSemanticMapperNode>();
    rclcpp::spin(node);
    rclcpp::shutdown();
    return 0;
}
