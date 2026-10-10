/**
 * Hailo Bridge Node (C++ Implementation)
 * =====================================
 * High-performance C++ ROS 2 node managing Hailo NPU inference via HailoRT C++ API.
 * Replaces Python hailo_bridge_node.py to eliminate GIL and OpenCV Python CPU overhead.
 * 
 * Features:
 * - Direct HailoRT C++ VStream pipeline
 * - Zero-Copy image passing via cv_bridge
 * - Lazy Publishing: Skipping drawing & JPEG compression when subscription count == 0
 * - Multi-thread executor & explicit core pinning support
 * 
 * Version: 02.00.00 (ECO00004)
 */

#include <memory>
#include <string>
#include <vector>
#include <chrono>
#include <unordered_map>

#include <rclcpp/rclcpp.hpp>
#include <image_transport/image_transport.hpp>
#include <cv_bridge/cv_bridge.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/compressed_image.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/float32_multi_array.hpp>
#include <vision_msgs/msg/detection2_d_array.hpp>
#include <vision_msgs/msg/detection2_d.hpp>
#include <vision_msgs/msg/object_hypothesis_with_pose.hpp>
#include <visualization_msgs/msg/marker_array.hpp>
#include <visualization_msgs/msg/marker.hpp>
#include <geometry_msgs/msg/point.hpp>
#include <array>

#include <opencv2/opencv.hpp>

// Custom ROS 2 messages
#include "robopy_controller/msg/semantic_object.hpp"
#include "robopy_controller/msg/semantic_object_array.hpp"

// Check for HailoRT C++ headers
#if __has_include(<hailo/hailort.hpp>)
#include <hailo/hailort.hpp>
#include <hailo/vdevice.hpp>
#include <hailo/infer_model.hpp>
#define HAILO_CPP_AVAILABLE 1
#else
#define HAILO_CPP_AVAILABLE 0
#endif

#if defined(__linux__)
#include <pthread.h>
#include <sched.h>
#endif

using namespace std::chrono_literals;

// COCO 80 Class Labels
static const std::vector<std::string> COCO_CLASSES = {
    "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat",
    "traffic light","fire hydrant","stop sign","parking meter","bench","bird","cat",
    "dog","horse","sheep","cow","elephant","bear","zebra","giraffe","backpack",
    "umbrella","handbag","tie","suitcase","frisbee","skis","snowboard","sports ball",
    "kite","baseball bat","baseball glove","skateboard","surfboard","tennis racket",
    "bottle","wine glass","cup","fork","knife","spoon","bowl","banana","apple",
    "sandwich","orange","broccoli","carrot","hot dog","pizza","donut","cake","chair",
    "couch","potted plant","bed","dining table","toilet","tv","laptop","mouse",
    "remote","keyboard","cell phone","microwave","oven","toaster","sink","refrigerator",
    "book","clock","vase","scissors","teddy bear","hair drier","toothbrush"
};

// COCO to Italian translations for all 80 classes
static const std::unordered_map<std::string, std::string> COCO_TO_ITALIAN = {
    {"person", "persona"},
    {"bicycle", "bicicletta"},
    {"car", "auto"},
    {"motorcycle", "moto"},
    {"airplane", "aereo"},
    {"bus", "autobus"},
    {"train", "treno"},
    {"truck", "camion"},
    {"boat", "barca"},
    {"traffic light", "semaforo"},
    {"fire hydrant", "idrante"},
    {"stop sign", "segnale stop"},
    {"parking meter", "parchimetro"},
    {"bench", "panchina"},
    {"bird", "uccello"},
    {"cat", "gatto"},
    {"dog", "cane"},
    {"horse", "cavallo"},
    {"sheep", "pecora"},
    {"cow", "mucca"},
    {"elephant", "elefante"},
    {"bear", "orso"},
    {"zebra", "zebra"},
    {"giraffe", "giraffa"},
    {"backpack", "zaino"},
    {"umbrella", "ombrello"},
    {"handbag", "borsa"},
    {"tie", "cravatta"},
    {"suitcase", "valigia"},
    {"frisbee", "frisbee"},
    {"skis", "sci"},
    {"snowboard", "snowboard"},
    {"sports ball", "palla"},
    {"kite", "aquilone"},
    {"baseball bat", "mazza baseball"},
    {"baseball glove", "guantone baseball"},
    {"skateboard", "skateboard"},
    {"surfboard", "tavola surf"},
    {"tennis racket", "racchetta tennis"},
    {"bottle", "bottiglia"},
    {"wine glass", "bicchiere"},
    {"cup", "tazza"},
    {"fork", "forchetta"},
    {"knife", "coltello"},
    {"spoon", "cucchiaio"},
    {"bowl", "ciotola"},
    {"banana", "banana"},
    {"apple", "mela"},
    {"sandwich", "panino"},
    {"orange", "arancia"},
    {"broccoli", "broccoli"},
    {"carrot", "carota"},
    {"hot dog", "hot dog"},
    {"pizza", "pizza"},
    {"donut", "ciambella"},
    {"cake", "torta"},
    {"chair", "sedia"},
    {"couch", "divano"},
    {"potted plant", "pianta"},
    {"bed", "letto"},
    {"dining table", "tavolo"},
    {"toilet", "wc"},
    {"tv", "televisore"},
    {"laptop", "computer"},
    {"mouse", "mouse"},
    {"remote", "telecomando"},
    {"keyboard", "tastiera"},
    {"cell phone", "cellulare"},
    {"microwave", "microonde"},
    {"oven", "forno"},
    {"toaster", "tostapane"},
    {"sink", "lavandino"},
    {"refrigerator", "frigorifero"},
    {"book", "libro"},
    {"clock", "orologio"},
    {"vase", "vaso"},
    {"scissors", "forbici"},
    {"teddy bear", "peluche"},
    {"hair drier", "asciugacapelli"},
    {"toothbrush", "spazzolino"}
};

struct DetectionBBox {
    float xmin;
    float ymin;
    float xmax;
    float ymax;
    float confidence;
    int class_id;
    std::string label;
};

struct Keypoint2D {
    float x{0.0f};
    float y{0.0f};
    float confidence{0.0f};
};

// Standard 17 COCO Keypoint Skeleton Connections
static const std::vector<std::pair<int, int>> COCO_SKELETON_PAIRS = {
    {0, 1}, {0, 2}, {1, 3}, {2, 4}, {0, 5}, {0, 6},
    {5, 6}, {5, 7}, {7, 9}, {6, 8}, {8, 10},
    {5, 11}, {6, 12}, {11, 12},
    {11, 13}, {13, 15}, {12, 14}, {14, 16}
};

struct PersonPose {
    DetectionBBox bbox;
    std::array<Keypoint2D, 17> keypoints;
};

class HailoBridgeNodeCpp : public rclcpp::Node {
public:
    HailoBridgeNodeCpp() : Node("hailo_bridge_node_cpp"), num_frames_processed_(0) {
        // Declare parameters
        this->declare_parameter<std::string>("hef_path", "/mnt/ssd/models/marcus_unified.hef");
        this->declare_parameter<bool>("sim_mode", false);
        this->declare_parameter<std::string>("rgb_topic", "/rgb/image");
        this->declare_parameter<double>("vlm_rate_hz", 5.0);
        this->declare_parameter<double>("conf_threshold", 0.55);

        // Pose Tracking parameters (F1 Upgrade - Hailo-10H)
        this->declare_parameter<bool>("enable_pose", true);
        this->declare_parameter<std::string>("pose_hef_path", "/mnt/ssd/models/yolov8s_pose.hef");
        this->declare_parameter<double>("pose_conf_threshold", 0.30);

        hef_path_ = this->get_parameter("hef_path").as_string();
        sim_mode_ = this->get_parameter("sim_mode").as_bool();
        rgb_topic_ = this->get_parameter("rgb_topic").as_string();
        vlm_rate_hz_ = this->get_parameter("vlm_rate_hz").as_double();
        conf_threshold_ = static_cast<float>(this->get_parameter("conf_threshold").as_double());

        enable_pose_ = this->get_parameter("enable_pose").as_bool();
        pose_hef_path_ = this->get_parameter("pose_hef_path").as_string();
        pose_conf_threshold_ = static_cast<float>(this->get_parameter("pose_conf_threshold").as_double());

        RCLCPP_INFO(this->get_logger(), "🚀 Starting Hailo Bridge Node C++ (HEF: %s, Rate: %.1f Hz, Pose: %s)",
                    hef_path_.c_str(), vlm_rate_hz_, enable_pose_ ? "ENABLED" : "DISABLED");

        // Initialize Hailo NPU Device if available
        init_hailo_npu();
    }

    void init() {
#ifdef __linux__
        // Pinned to CPU Cores 2 and 3 (SPEC-03, SPEC-07, marcus_core_rules.md)
        cpu_set_t cpuset;
        CPU_ZERO(&cpuset);
        CPU_SET(2, &cpuset);
        CPU_SET(3, &cpuset);
        if (pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cpuset) == 0) {
            RCLCPP_INFO(this->get_logger(), "📌 Core pinning forzato sui core CPU 2 e 3.");
        } else {
            RCLCPP_WARN(this->get_logger(), "⚠️ Impossibile forzare il core pinning sui core 2-3.");
        }
#endif

        // Safe to call shared_from_this() after std::make_shared
        it_ = std::make_unique<image_transport::ImageTransport>(shared_from_this());
        sub_rgb_ = it_->subscribe(rgb_topic_, 1, std::bind(&HailoBridgeNodeCpp::rgb_callback, this, std::placeholders::_1));

        pub_annotated_ = it_->advertise("/hailo/annotated_image", 1);
        pub_annotated_compressed_ = this->create_publisher<sensor_msgs::msg::CompressedImage>("/hailo/annotated_image/compressed", 10);
        pub_detections_ = this->create_publisher<vision_msgs::msg::Detection2DArray>("/hailo/detections", 10);
        pub_semantic_objects_ = this->create_publisher<robopy_controller::msg::SemanticObjectArray>("/hailo/semantic_objects", 10);
        pub_vlad_ = this->create_publisher<std_msgs::msg::Float32MultiArray>("/hailo/vlad_descriptor", 10);

        if (enable_pose_) {
            pub_pose_markers_ = this->create_publisher<visualization_msgs::msg::MarkerArray>("/hailo/pose/skeletons", 10);
            pub_pose_detections_ = this->create_publisher<vision_msgs::msg::Detection2DArray>("/hailo/pose/detections", 10);
        }

        last_inference_time_ = std::chrono::steady_clock::now();
        RCLCPP_INFO(this->get_logger(), "✅ Hailo Bridge C++ Node initialized successfully (Pose: %s).",
                    enable_pose_ ? "ON" : "OFF");
    }

    ~HailoBridgeNodeCpp() override {
        RCLCPP_INFO(this->get_logger(), "🛑 Shutting down Hailo Bridge C++ Node.");
    }

private:
    struct ScaleInfo {
        int stride;
        int grid_h;
        int grid_w;
        std::string bbox_layer;
        std::string cls_layer;
    };

    void init_hailo_npu() {
#if HAILO_CPP_AVAILABLE
        if (sim_mode_) {
            RCLCPP_WARN(this->get_logger(), "⚠️ Simulation mode active: Skipping Hailo NPU hardware init.");
            hailo_ready_ = false;
            return;
        }

        try {
            auto vdevice_expected = hailort::VDevice::create();
            if (!vdevice_expected) {
                RCLCPP_ERROR(this->get_logger(), "❌ Failed to create Hailo VDevice: %d", vdevice_expected.status());
                hailo_ready_ = false;
                return;
            }
            vdevice_ = vdevice_expected.release();

            // Load model using InferModel API (Hailo-10H Standard - SPEC-03)
            auto infer_model_expected = vdevice_->create_infer_model(hef_path_);
            if (!infer_model_expected) {
                RCLCPP_ERROR(this->get_logger(), "❌ Failed to create InferModel from %s: %d",
                             hef_path_.c_str(), infer_model_expected.status());
                hailo_ready_ = false;
                return;
            }
            infer_model_ = infer_model_expected.release();

            // Find input stream
            const auto &inputs = infer_model_->inputs();
            if (inputs.empty()) {
                RCLCPP_ERROR(this->get_logger(), "❌ InferModel has no input streams!");
                hailo_ready_ = false;
                return;
            }

            yolo_input_name_ = inputs[0].name();
            for (const auto &inp : inputs) {
                if (inp.name().find("yolo") != std::string::npos) {
                    yolo_input_name_ = inp.name();
                    break;
                }
            }

            auto in_shape = infer_model_->input(yolo_input_name_)->shape();
            yolo_input_h_ = in_shape.height;
            yolo_input_w_ = in_shape.width;
            RCLCPP_INFO(this->get_logger(), "📦 YOLO Input Stream: %s (%dx%dx%d)",
                        yolo_input_name_.c_str(), yolo_input_w_, yolo_input_h_, in_shape.features);

            // Configure all outputs to FLOAT32 for automatic hardware dequantization
            for (const auto &outp : infer_model_->outputs()) {
                auto out_stream_exp = infer_model_->output(outp.name());
                if (out_stream_exp) {
                    out_stream_exp->set_format_type(HAILO_FORMAT_TYPE_FLOAT32);
                }
            }

            // Configure the InferModel
            auto configured_expected = infer_model_->configure();
            if (!configured_expected) {
                RCLCPP_ERROR(this->get_logger(), "❌ Failed to configure InferModel: %d", configured_expected.status());
                hailo_ready_ = false;
                return;
            }
            configured_infer_model_ = std::make_unique<hailort::ConfiguredInferModel>(configured_expected.release());

            // Create Bindings
            auto bindings_expected = configured_infer_model_->create_bindings();
            if (!bindings_expected) {
                RCLCPP_ERROR(this->get_logger(), "❌ Failed to create bindings: %d", bindings_expected.status());
                hailo_ready_ = false;
                return;
            }
            bindings_ = std::make_unique<hailort::ConfiguredInferModel::Bindings>(bindings_expected.release());

            // Pre-allocate and bind all input buffers (handles multi-network joined HEF)
            for (const auto &inp : infer_model_->inputs()) {
                std::string name = inp.name();
                size_t frame_size_bytes = inp.get_frame_size();
                input_buffers_[name].resize(frame_size_bytes, 0);
                auto in_stream = bindings_->input(name);
                if (in_stream) {
                    in_stream->set_buffer(hailort::MemoryView(
                        input_buffers_[name].data(), input_buffers_[name].size()
                    ));
                }
            }

            // Pre-allocate output buffers
            for (const auto &outp : infer_model_->outputs()) {
                std::string name = outp.name();
                size_t frame_size_bytes = outp.get_frame_size();
                output_buffers_[name].resize(frame_size_bytes / sizeof(float));
                auto out_stream = bindings_->output(name);
                if (out_stream) {
                    out_stream->set_buffer(hailort::MemoryView(
                        output_buffers_[name].data(), output_buffers_[name].size() * sizeof(float)
                    ));
                }
            }

            // Dynamically detect YOLO layer prefix (e.g. "yolo/" or "yolov8s_seg/")
            std::string yolo_prefix = "yolo/";
            for (const auto &outp : infer_model_->outputs()) {
                std::string n = outp.name();
                size_t p = n.find("conv44");
                if (p != std::string::npos) {
                    yolo_prefix = n.substr(0, p);
                    break;
                }
            }

            // Define YOLOv8 Multi-scale heads with resolved prefix
            scales_ = {
                {8,  80, 80, yolo_prefix + "conv44", yolo_prefix + "conv45"},
                {16, 40, 40, yolo_prefix + "conv60", yolo_prefix + "conv61"},
                {32, 20, 20, yolo_prefix + "conv73", yolo_prefix + "conv74"}
            };
            RCLCPP_INFO(this->get_logger(), "🔍 YOLO Head Layers: %s (stride 8), %s (stride 16), %s (stride 32)",
                        scales_[0].cls_layer.c_str(), scales_[1].cls_layer.c_str(), scales_[2].cls_layer.c_str());

            // Initialize Pose InferModel on Shared VDevice (SPEC-03 F1 Upgrade)
            if (enable_pose_) {
                auto infer_model_pose_expected = vdevice_->create_infer_model(pose_hef_path_);
                if (!infer_model_pose_expected) {
                    RCLCPP_WARN(this->get_logger(), "⚠️ Failed to create Pose InferModel from %s: %d (Pose disabled)",
                                pose_hef_path_.c_str(), infer_model_pose_expected.status());
                    hailo_pose_ready_ = false;
                } else {
                    infer_model_pose_ = infer_model_pose_expected.release();
                    infer_model_pose_->set_batch_size(1);

                    const auto &pose_inputs = infer_model_pose_->inputs();
                    if (!pose_inputs.empty()) {
                        pose_input_name_ = pose_inputs[0].name();
                        auto in_shape = infer_model_pose_->input(pose_input_name_)->shape();
                        pose_input_h_ = in_shape.height;
                        pose_input_w_ = in_shape.width;
                        RCLCPP_INFO(this->get_logger(), "📦 Pose Input Stream: %s (%dx%dx%d)",
                                    pose_input_name_.c_str(), pose_input_w_, pose_input_h_, in_shape.features);
                    }

                    for (const auto &outp : infer_model_pose_->outputs()) {
                        auto out_stream_exp = infer_model_pose_->output(outp.name());
                        if (out_stream_exp) {
                            out_stream_exp->set_format_type(HAILO_FORMAT_TYPE_FLOAT32);
                        }
                    }

                    auto configured_pose_expected = infer_model_pose_->configure();
                    if (configured_pose_expected) {
                        configured_infer_model_pose_ = std::make_unique<hailort::ConfiguredInferModel>(configured_pose_expected.release());
                        auto bindings_pose_expected = configured_infer_model_pose_->create_bindings();
                        if (bindings_pose_expected) {
                            bindings_pose_ = std::make_unique<hailort::ConfiguredInferModel::Bindings>(bindings_pose_expected.release());

                            for (const auto &inp : infer_model_pose_->inputs()) {
                                std::string name = inp.name();
                                size_t frame_size_bytes = inp.get_frame_size();
                                pose_input_buffers_[name].resize(frame_size_bytes, 0);
                                auto in_stream = bindings_pose_->input(name);
                                if (in_stream) {
                                    in_stream->set_buffer(hailort::MemoryView(
                                        pose_input_buffers_[name].data(), pose_input_buffers_[name].size()
                                    ));
                                }
                            }

                            for (const auto &outp : infer_model_pose_->outputs()) {
                                std::string name = outp.name();
                                size_t frame_size_bytes = outp.get_frame_size();
                                pose_output_buffers_[name].resize(frame_size_bytes / sizeof(float));
                                auto out_stream = bindings_pose_->output(name);
                                if (out_stream) {
                                    out_stream->set_buffer(hailort::MemoryView(
                                        pose_output_buffers_[name].data(), pose_output_buffers_[name].size() * sizeof(float)
                                    ));
                                }
                                RCLCPP_INFO(this->get_logger(), "📦 Pose Output Stream: %s, size=%zu floats (frame_bytes=%zu)",
                                            name.c_str(), pose_output_buffers_[name].size(), frame_size_bytes);
                            }
                            hailo_pose_ready_ = true;
                            RCLCPP_INFO(this->get_logger(), "🕺 Hailo-10H Pose InferModel configured successfully on shared VDevice!");
                        }
                    }
                }
            }

            RCLCPP_INFO(this->get_logger(), "🧠 Hailo-10H NPU Hardware & InferModel configured successfully C++!");
            hailo_ready_ = true;
        } catch (const std::exception &e) {
            RCLCPP_ERROR(this->get_logger(), "❌ Exception during Hailo NPU init: %s", e.what());
            hailo_ready_ = false;
        }
#else
        RCLCPP_WARN(this->get_logger(), "⚠️ HailoRT C++ SDK headers not compiled in. Operating in lightweight stub mode.");
        hailo_ready_ = false;
#endif
    }

    void rgb_callback(const sensor_msgs::msg::Image::ConstSharedPtr &msg) {
        auto now = std::chrono::steady_clock::now();
        double elapsed_sec = std::chrono::duration<double>(now - last_inference_time_).count();

        // Enforce VLM / Detection rate throttle
        if (vlm_rate_hz_ > 0.0 && elapsed_sec < (1.0 / vlm_rate_hz_)) {
            return;
        }
        last_inference_time_ = now;
        num_frames_processed_++;

        // Convert ROS Image to OpenCV Mat (Zero-copy)
        cv_bridge::CvImageConstPtr cv_ptr;
        try {
            cv_ptr = cv_bridge::toCvShare(msg, sensor_msgs::image_encodings::BGR8);
        } catch (const cv_bridge::Exception &e) {
            RCLCPP_ERROR(this->get_logger(), "cv_bridge exception: %s", e.what());
            return;
        }

        const cv::Mat &frame = cv_ptr->image;
        if (frame.empty()) return;

        // Perform Inference / Object Detection
        std::vector<DetectionBBox> detections;
        if (hailo_ready_) {
#if HAILO_CPP_AVAILABLE
            // Pre-process: Letterbox 1:1 aspect ratio preserving resize with padding 114 (FM-VIS-009)
            float scale = std::min(static_cast<float>(yolo_input_w_) / frame.cols,
                                   static_cast<float>(yolo_input_h_) / frame.rows);
            int new_unpad_w = std::clamp(static_cast<int>(std::round(frame.cols * scale)), 1, yolo_input_w_);
            int new_unpad_h = std::clamp(static_cast<int>(std::round(frame.rows * scale)), 1, yolo_input_h_);
            int pad_x = (yolo_input_w_ - new_unpad_w) / 2;
            int pad_y = (yolo_input_h_ - new_unpad_h) / 2;

            last_scale_ = scale;
            last_pad_x_ = pad_x;
            last_pad_y_ = pad_y;
            last_img_w_ = frame.cols;
            last_img_h_ = frame.rows;

            cv::Mat letterbox_rgb(yolo_input_h_, yolo_input_w_, CV_8UC3, input_buffers_[yolo_input_name_].data());
            letterbox_rgb.setTo(cv::Scalar(114, 114, 114)); // YOLO standard letterbox fill
            cv::Mat resized;
            cv::resize(frame, resized, cv::Size(new_unpad_w, new_unpad_h));
            cv::Mat roi = letterbox_rgb(cv::Rect(pad_x, pad_y, new_unpad_w, new_unpad_h));
            cv::cvtColor(resized, roi, cv::COLOR_BGR2RGB);

            // Execute Synchronous NPU Inference
            hailo_status status = configured_infer_model_->run(*bindings_, std::chrono::milliseconds(1000));
            if (status == HAILO_SUCCESS) {
                detections = decode_yolo_outputs();
                if (!detections.empty()) {
                    std::string summary = "";
                    for (size_t i = 0; i < std::min(detections.size(), (size_t)3); ++i) {
                        summary += detections[i].label + "(" + cv::format("%.2f", detections[i].confidence) + ") ";
                    }
                    RCLCPP_INFO_THROTTLE(this->get_logger(), *this->get_clock(), 2000,
                        "🎯 [HAILO-YOLO] Rilevati %zu oggetti: %s", detections.size(), summary.c_str());
                } else {
                    RCLCPP_INFO_THROTTLE(this->get_logger(), *this->get_clock(), 5000,
                        "🔍 [HAILO-YOLO] Frame elaborato a 5Hz: 0 oggetti sopra soglia %.2f", conf_threshold_);
                }
            } else {
                RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 5000,
                                     "Hailo InferModel::run failed with status %d", status);
            }
#endif
        } else if (sim_mode_) {
            // Simulated / Mock detection fallback
            detections.push_back({0.36f, 0.45f, 0.62f, 0.88f, 0.88f, 56, "chair"});
            detections.push_back({0.61f, 0.48f, 0.88f, 0.95f, 0.85f, 56, "chair"});
            detections.push_back({0.34f, 0.28f, 0.95f, 0.88f, 0.91f, 60, "dining table"});
            detections.push_back({0.19f, 0.58f, 0.32f, 0.68f, 0.82f, 62, "tv"});
            detections.push_back({0.01f, 0.48f, 0.12f, 0.57f, 0.79f, 58, "potted plant"});
            if (enable_pose_) {
                detections.push_back({0.35f, 0.12f, 0.65f, 0.88f, 0.93f, 0, "person"});
            }
        }

        // 🧍 PRESENCE-GATED POSE ESTIMATION (IMP-GOV-001, FM-GOV-016)
        std::vector<PersonPose> poses;
        bool has_person = false;
        for (const auto &det : detections) {
            if (det.label == "person" || det.class_id == 0) {
                has_person = true;
                break;
            }
        }

        if (enable_pose_ && (has_person || sim_mode_)) {
            if (hailo_pose_ready_) {
#if HAILO_CPP_AVAILABLE
                if (!pose_input_name_.empty() && pose_input_buffers_.count(pose_input_name_)) {
                    if (pose_input_w_ == yolo_input_w_ && pose_input_h_ == yolo_input_h_) {
                        std::memcpy(pose_input_buffers_[pose_input_name_].data(),
                                    input_buffers_[yolo_input_name_].data(),
                                    pose_input_buffers_[pose_input_name_].size());
                    } else {
                        float p_scale = std::min(static_cast<float>(pose_input_w_) / frame.cols,
                                                static_cast<float>(pose_input_h_) / frame.rows);
                        int p_unpad_w = std::clamp(static_cast<int>(std::round(frame.cols * p_scale)), 1, pose_input_w_);
                        int p_unpad_h = std::clamp(static_cast<int>(std::round(frame.rows * p_scale)), 1, pose_input_h_);
                        int p_pad_x = (pose_input_w_ - p_unpad_w) / 2;
                        int p_pad_y = (pose_input_h_ - p_unpad_h) / 2;

                        cv::Mat p_letterbox(pose_input_h_, pose_input_w_, CV_8UC3, pose_input_buffers_[pose_input_name_].data());
                        p_letterbox.setTo(cv::Scalar(114, 114, 114));
                        cv::Mat p_resized;
                        cv::resize(frame, p_resized, cv::Size(p_unpad_w, p_unpad_h));
                        cv::Mat p_roi = p_letterbox(cv::Rect(p_pad_x, p_pad_y, p_unpad_w, p_unpad_h));
                        cv::cvtColor(p_resized, p_roi, cv::COLOR_BGR2RGB);
                    }

                    hailo_status p_status = configured_infer_model_pose_->run(*bindings_pose_, std::chrono::milliseconds(1000));
                    if (p_status == HAILO_SUCCESS) {
                        poses = decode_pose_outputs();
                        if (!poses.empty()) {
                            RCLCPP_INFO_THROTTLE(this->get_logger(), *this->get_clock(), 2000,
                                "🕺 [HAILO-POSE] Tracciate %zu persone con skeleton keypoints", poses.size());
                        }
                    } else {
                        RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 5000,
                            "Hailo Pose InferModel::run failed with status %d", p_status);
                    }
                }
#endif
            } else if (sim_mode_) {
                poses = generate_sim_poses(detections);
            }

            if (!poses.empty()) {
                publish_poses(msg->header, poses, frame.cols, frame.rows);
            }
        }

        // Publish Detection Messages to ROS 2 topics
        publish_detections(msg->header, detections, frame.cols, frame.rows);

        // 🚀 CRITICAL LAZY PUBLISHING OPTIMIZATION (SPEC-03):
        // Skip drawing bboxes, text rendering, and JPEG compression entirely if no subscriber!
        bool has_image_subscribers = (pub_annotated_.getNumSubscribers() > 0) ||
                                     (pub_annotated_compressed_->get_subscription_count() > 0);

        if (!has_image_subscribers) {
            return; // ⚡ SAVES 90%+ CPU on Pi 5!
        }

        // Render annotations ONLY when someone is listening (e.g., Foxglove Studio)
        cv::Mat annotated_frame = frame.clone();
        for (const auto &det : detections) {
            cv::Rect bbox(
                static_cast<int>(det.xmin * frame.cols),
                static_cast<int>(det.ymin * frame.rows),
                static_cast<int>((det.xmax - det.xmin) * frame.cols),
                static_cast<int>((det.ymax - det.ymin) * frame.rows)
            );
            cv::rectangle(annotated_frame, bbox, cv::Scalar(0, 255, 0), 2);

            auto it_it = COCO_TO_ITALIAN.find(det.label);
            std::string italian_label = (it_it != COCO_TO_ITALIAN.end()) ? it_it->second : det.label;
            std::string label_str = italian_label + " " + cv::format("%.2f", det.confidence);

            int base_line = 0;
            cv::Size text_size = cv::getTextSize(label_str, cv::FONT_HERSHEY_SIMPLEX, 0.5, 1, &base_line);
            int text_x = std::max(0, std::min(bbox.x, frame.cols - text_size.width - 6));
            int text_y = std::max(text_size.height + 4, bbox.y);
            cv::rectangle(annotated_frame, cv::Point(text_x, text_y - text_size.height - 6),
                          cv::Point(text_x + text_size.width + 4, text_y),
                          cv::Scalar(0, 255, 0), cv::FILLED);
            cv::putText(annotated_frame, label_str, cv::Point(text_x + 2, text_y - 4),
                        cv::FONT_HERSHEY_SIMPLEX, 0.5, cv::Scalar(0, 0, 0), 1);
        }

        // Render Pose Skeletons if available
        if (enable_pose_ && !poses.empty()) {
            for (const auto &p : poses) {
                // Skeleton bone links
                for (const auto &pair : COCO_SKELETON_PAIRS) {
                    const auto &kp1 = p.keypoints[pair.first];
                    const auto &kp2 = p.keypoints[pair.second];
                    if (kp1.confidence >= pose_conf_threshold_ && kp2.confidence >= pose_conf_threshold_) {
                        cv::Point pt1(static_cast<int>(kp1.x * frame.cols), static_cast<int>(kp1.y * frame.rows));
                        cv::Point pt2(static_cast<int>(kp2.x * frame.cols), static_cast<int>(kp2.y * frame.rows));
                        cv::line(annotated_frame, pt1, pt2, cv::Scalar(0, 215, 255), 2, cv::LINE_AA);
                    }
                }
                // Joint keypoints
                for (const auto &kp : p.keypoints) {
                    if (kp.confidence >= pose_conf_threshold_) {
                        cv::Point pt(static_cast<int>(kp.x * frame.cols), static_cast<int>(kp.y * frame.rows));
                        cv::circle(annotated_frame, pt, 4, cv::Scalar(0, 255, 255), -1, cv::LINE_AA);
                        cv::circle(annotated_frame, pt, 5, cv::Scalar(0, 0, 0), 1, cv::LINE_AA);
                    }
                }
            }
        }

        // Publish Annotated Raw Image if subscribed
        if (pub_annotated_.getNumSubscribers() > 0) {
            sensor_msgs::msg::Image::SharedPtr ann_msg =
                cv_bridge::CvImage(msg->header, "bgr8", annotated_frame).toImageMsg();
            pub_annotated_.publish(ann_msg);
        }

        // Publish Annotated Compressed JPEG Image if subscribed
        if (pub_annotated_compressed_->get_subscription_count() > 0) {
            sensor_msgs::msg::CompressedImage comp_msg;
            comp_msg.header = msg->header;
            comp_msg.format = "jpeg";
            std::vector<uchar> buffer;
            std::vector<int> params = {cv::IMWRITE_JPEG_QUALITY, 80};
            cv::imencode(".jpg", annotated_frame, buffer, params);
            comp_msg.data = buffer;
            pub_annotated_compressed_->publish(comp_msg);
        }
    }

    std::vector<DetectionBBox> decode_yolo_outputs() {
        std::vector<DetectionBBox> candidates;
        const float conf_thresh = conf_threshold_;

        for (const auto &scale : scales_) {
            auto bbox_it = output_buffers_.find(scale.bbox_layer);
            auto cls_it = output_buffers_.find(scale.cls_layer);
            if (bbox_it == output_buffers_.end() || cls_it == output_buffers_.end()) {
                continue;
            }

            const float *bbox_data = bbox_it->second.data();
            const float *cls_data = cls_it->second.data();
            int grid_h = scale.grid_h;
            int grid_w = scale.grid_w;
            int stride = scale.stride;

            for (int gy = 0; gy < grid_h; ++gy) {
                for (int gx = 0; gx < grid_w; ++gx) {
                    int cell_idx = gy * grid_w + gx;
                    const float *cls_cell = cls_data + (cell_idx * 80);

                    // Find best class
                    float best_score = -1.0f;
                    int best_cls = -1;
                    for (int c = 0; c < 80; ++c) {
                        float logit = cls_cell[c];
                        float score = 1.0f / (1.0f + std::exp(-std::clamp(logit, -10.0f, 10.0f)));
                        if (score > best_score) {
                            best_score = score;
                            best_cls = c;
                        }
                    }

                    if (best_score < conf_thresh) {
                        continue;
                    }

                    // DFL Decode for 4 coordinates (left, top, right, bottom)
                    const float *bbox_cell = bbox_data + (cell_idx * 64);
                    float dfl[4];
                    for (int b = 0; b < 4; ++b) {
                        const float *reg = bbox_cell + (b * 16);
                        float max_val = -1e9f;
                        for (int i = 0; i < 16; ++i) {
                            max_val = std::max(max_val, reg[i]);
                        }
                        float sum_exp = 0.0f;
                        float exp_v[16];
                        for (int i = 0; i < 16; ++i) {
                            exp_v[i] = std::exp(reg[i] - max_val);
                            sum_exp += exp_v[i];
                        }
                        float val = 0.0f;
                        for (int i = 0; i < 16; ++i) {
                            val += (exp_v[i] / sum_exp) * i;
                        }
                        dfl[b] = val;
                    }

                    float x1 = (gx + 0.5f - dfl[0]) * stride;
                    float y1 = (gy + 0.5f - dfl[1]) * stride;
                    float x2 = (gx + 0.5f + dfl[2]) * stride;
                    float y2 = (gy + 0.5f + dfl[3]) * stride;

                    float orig_w = (last_scale_ > 0.0f && last_img_w_ > 0) ? static_cast<float>(last_img_w_) : 640.0f;
                    float orig_h = (last_scale_ > 0.0f && last_img_h_ > 0) ? static_cast<float>(last_img_h_) : 480.0f;

                    // Inverse letterbox mapping to original image coordinate frame [0.0, 1.0]
                    float xmin = std::clamp((x1 - last_pad_x_) / (last_scale_ * orig_w), 0.0f, 1.0f);
                    float ymin = std::clamp((y1 - last_pad_y_) / (last_scale_ * orig_h), 0.0f, 1.0f);
                    float xmax = std::clamp((x2 - last_pad_x_) / (last_scale_ * orig_w), 0.0f, 1.0f);
                    float ymax = std::clamp((y2 - last_pad_y_) / (last_scale_ * orig_h), 0.0f, 1.0f);

                    if (xmax > xmin && ymax > ymin) {
                        std::string label = (best_cls >= 0 && best_cls < static_cast<int>(COCO_CLASSES.size()))
                                            ? COCO_CLASSES[best_cls] : "obstacle";

                        // Filter out spurious horizontal person detections on wide furniture (FM-VIS-009)
                        if (label == "person") {
                            float box_w = (xmax - xmin) * orig_w;
                            float box_h = (ymax - ymin) * orig_h;
                            if (box_w > 1.8f * box_h) {
                                continue; // Reject wide horizontal person false positives
                            }
                        }

                        candidates.push_back({xmin, ymin, xmax, ymax, best_score, best_cls, label});
                    }
                }
            }
        }

        // Fast NMS (IoU Threshold: 0.45)
        std::sort(candidates.begin(), candidates.end(), [](const DetectionBBox &a, const DetectionBBox &b) {
            return a.confidence > b.confidence;
        });

        std::vector<DetectionBBox> nms_results;
        const float iou_threshold = 0.45f;

        for (const auto &box : candidates) {
            bool keep = true;
            for (const auto &selected : nms_results) {
                float inter_x1 = std::max(box.xmin, selected.xmin);
                float inter_y1 = std::max(box.ymin, selected.ymin);
                float inter_x2 = std::min(box.xmax, selected.xmax);
                float inter_y2 = std::min(box.ymax, selected.ymax);

                float inter_w = std::max(0.0f, inter_x2 - inter_x1);
                float inter_h = std::max(0.0f, inter_y2 - inter_y1);
                float inter_area = inter_w * inter_h;

                float area_box = (box.xmax - box.xmin) * (box.ymax - box.ymin);
                float area_selected = (selected.xmax - selected.xmin) * (selected.ymax - selected.ymin);
                float union_area = area_box + area_selected - inter_area;

                float iou = (union_area > 0.0f) ? (inter_area / union_area) : 0.0f;
                if (iou > iou_threshold) {
                    keep = false;
                    break;
                }
            }
            if (keep) {
                nms_results.push_back(box);
            }
        }

        return nms_results;
    }

    void publish_detections(const std_msgs::msg::Header &header,
                            const std::vector<DetectionBBox> &detections,
                            int img_width, int img_height) {
        vision_msgs::msg::Detection2DArray det_array_msg;
        det_array_msg.header = header;

        robopy_controller::msg::SemanticObjectArray sem_array_msg;
        sem_array_msg.header = header;

        for (const auto &det : detections) {
            // vision_msgs/Detection2D
            vision_msgs::msg::Detection2D det_msg;
            det_msg.header = header;
            det_msg.bbox.center.position.x = (det.xmin + det.xmax) / 2.0 * img_width;
            det_msg.bbox.center.position.y = (det.ymin + det.ymax) / 2.0 * img_height;
            det_msg.bbox.size_x = (det.xmax - det.xmin) * img_width;
            det_msg.bbox.size_y = (det.ymax - det.ymin) * img_height;

            vision_msgs::msg::ObjectHypothesisWithPose hyp;
            hyp.hypothesis.class_id = det.label;
            hyp.hypothesis.score = det.confidence;
            det_msg.results.push_back(hyp);
            det_array_msg.detections.push_back(det_msg);

            // robopy_controller/SemanticObject
            robopy_controller::msg::SemanticObject sem_obj;
            sem_obj.header = header;
            auto it_it = COCO_TO_ITALIAN.find(det.label);
            sem_obj.label = (it_it != COCO_TO_ITALIAN.end()) ? it_it->second : det.label;
            sem_obj.confidence = det.confidence;
            sem_obj.bbox_2d[0] = det.xmin;
            sem_obj.bbox_2d[1] = det.ymin;
            sem_obj.bbox_2d[2] = det.xmax;
            sem_obj.bbox_2d[3] = det.ymax;
            sem_obj.semantic_class = "obstacle";
            sem_array_msg.objects.push_back(sem_obj);
        }

        pub_detections_->publish(det_array_msg);
        pub_semantic_objects_->publish(sem_array_msg);
    }

    std::vector<PersonPose> decode_pose_outputs() {
        std::vector<PersonPose> candidates;
        const float conf_thresh = pose_conf_threshold_;
        float orig_w = (last_scale_ > 0.0f && last_img_w_ > 0) ? static_cast<float>(last_img_w_) : 640.0f;
        float orig_h = (last_scale_ > 0.0f && last_img_h_ > 0) ? static_cast<float>(last_img_h_) : 480.0f;

        static auto last_pose_log_time = std::chrono::steady_clock::now();
        auto now_log = std::chrono::steady_clock::now();
        bool should_log = false;
        if (std::chrono::duration<double>(now_log - last_pose_log_time).count() >= 1.0) {
            should_log = true;
            last_pose_log_time = now_log;
        }

        // 1. Single concatenated output tensor format: (8400, 56)
        for (const auto &kv : pose_output_buffers_) {
            const auto &buf = kv.second;
            if (buf.size() == 8400 * 56) {
                for (int i = 0; i < 8400; ++i) {
                    const float *row = buf.data() + (i * 56);
                    float score = row[4];
                    if (score < conf_thresh) continue;

                    float cx = row[0];
                    float cy = row[1];
                    float w = row[2];
                    float h = row[3];

                    float x1 = cx - w * 0.5f;
                    float y1 = cy - h * 0.5f;
                    float x2 = cx + w * 0.5f;
                    float y2 = cy + h * 0.5f;

                    float xmin = std::clamp((x1 - last_pad_x_) / (last_scale_ * orig_w), 0.0f, 1.0f);
                    float ymin = std::clamp((y1 - last_pad_y_) / (last_scale_ * orig_h), 0.0f, 1.0f);
                    float xmax = std::clamp((x2 - last_pad_x_) / (last_scale_ * orig_w), 0.0f, 1.0f);
                    float ymax = std::clamp((y2 - last_pad_y_) / (last_scale_ * orig_h), 0.0f, 1.0f);

                    if (xmax <= xmin || ymax <= ymin) continue;

                    PersonPose pose;
                    pose.bbox = {xmin, ymin, xmax, ymax, score, 0, "person"};
                    const float *kpt_raw = row + 5;
                    for (int k = 0; k < 17; ++k) {
                        float kx = kpt_raw[k * 3 + 0];
                        float ky = kpt_raw[k * 3 + 1];
                        float ks = kpt_raw[k * 3 + 2];
                        float norm_x = std::clamp((kx - last_pad_x_) / (last_scale_ * orig_w), 0.0f, 1.0f);
                        float norm_y = std::clamp((ky - last_pad_y_) / (last_scale_ * orig_h), 0.0f, 1.0f);
                        pose.keypoints[k] = {norm_x, norm_y, ks};
                    }
                    candidates.push_back(pose);
                }
                break;
            }
        }

        // 2. Multi-scale separate heads format (strides 8, 16, 32)
        // 2. Multi-scale separate heads format (strides 8, 16, 32)
        if (candidates.empty()) {
            struct PoseHeadDef {
                int stride;
                int gh;
                int gw;
                std::string bbox_pattern;
                std::string cls_pattern;
                std::string kpt_pattern;
            };

            std::vector<PoseHeadDef> scale_defs = {
                {8,  80, 80, "conv43", "conv44", "conv45"},
                {16, 40, 40, "conv57", "conv58", "conv59"},
                {32, 20, 20, "conv70", "conv71", "conv72"}
            };

            if (should_log) {
                // Inspect all 9 pose output buffer stats
                for (const auto &kv : pose_output_buffers_) {
                    float b_min = 1e9f, b_max = -1e9f;
                    size_t non_zero = 0;
                    for (float v : kv.second) {
                        if (v < b_min) b_min = v;
                        if (v > b_max) b_max = v;
                        if (std::abs(v) > 1e-6f) non_zero++;
                    }
                    RCLCPP_INFO(this->get_logger(),
                        "📊 [POSE-TENSOR] %s: size=%zu, min=%.4f, max=%.4f, non_zero=%zu/%zu",
                        kv.first.c_str(), kv.second.size(), b_min, b_max, non_zero, kv.second.size());
                }
            }

            for (const auto &sc : scale_defs) {
                int stride = sc.stride;
                int gh = sc.gh;
                int gw = sc.gw;
                int num_cells = gh * gw;

                const float *cls_ptr = nullptr;
                const float *bbox_ptr = nullptr;
                const float *kpt_ptr = nullptr;
                bool is_dfl = true; // YOLOv8 pose uses 64-dim DFL bbox heads

                for (const auto &kv : pose_output_buffers_) {
                    const std::string &name = kv.first;
                    const auto &buf = kv.second;

                    if (name.find(sc.bbox_pattern) != std::string::npos || (sc.stride == 8 && name.find("output_layer1") != std::string::npos) || (sc.stride == 16 && name.find("output_layer4") != std::string::npos) || (sc.stride == 32 && name.find("output_layer7") != std::string::npos)) {
                        bbox_ptr = buf.data();
                        is_dfl = (buf.size() == static_cast<size_t>(num_cells * 64));
                    } else if (name.find(sc.cls_pattern) != std::string::npos || (sc.stride == 8 && name.find("output_layer2") != std::string::npos) || (sc.stride == 16 && name.find("output_layer5") != std::string::npos) || (sc.stride == 32 && name.find("output_layer8") != std::string::npos)) {
                        cls_ptr = buf.data();
                    } else if (name.find(sc.kpt_pattern) != std::string::npos || (sc.stride == 8 && name.find("output_layer3") != std::string::npos) || (sc.stride == 16 && name.find("output_layer6") != std::string::npos) || (sc.stride == 32 && name.find("output_layer9") != std::string::npos)) {
                        kpt_ptr = buf.data();
                    }
                }

                if (!kpt_ptr || !bbox_ptr || !cls_ptr) {
                    RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 2000,
                        "⚠️ [POSE-STRIDE %d] MISSING PTR! cls=%p bbox=%p kpt=%p",
                        stride, (void*)cls_ptr, (void*)bbox_ptr, (void*)kpt_ptr);
                    continue;
                }

                float stride_max_raw = -999.0f;
                float stride_max_score = -999.0f;
                float stride_max_kpt = -999.0f;
                int stride_candidates = 0;

                for (int gy = 0; gy < gh; ++gy) {
                    for (int gx = 0; gx < gw; ++gx) {
                        int cell_idx = gy * gw + gx;
                        float raw_val = cls_ptr[cell_idx];
                        if (raw_val > stride_max_raw) stride_max_raw = raw_val;
                        float score = (raw_val >= 0.0f && raw_val <= 1.0f)
                                      ? raw_val
                                      : (1.0f / (1.0f + std::exp(-std::clamp(raw_val, -10.0f, 10.0f))));
                        if (score > stride_max_score) stride_max_score = score;

                        // Inspect keypoints in this cell
                        const float *kpt_cell = kpt_ptr + (cell_idx * 51);
                        float max_kp_score = 0.0f;
                        float sum_kp_score = 0.0f;
                        int valid_kps = 0;
                        for (int k = 0; k < 17; ++k) {
                            float kscore_raw = kpt_cell[k * 3 + 2];
                            float kp_score = 1.0f / (1.0f + std::exp(-std::clamp(kscore_raw, -10.0f, 10.0f)));
                            if (kp_score > max_kp_score) max_kp_score = kp_score;
                            sum_kp_score += kp_score;
                            if (kp_score >= 0.25f) valid_kps++;
                        }
                        float avg_kp_score = sum_kp_score / 17.0f;
                        if (max_kp_score > stride_max_kpt) stride_max_kpt = max_kp_score;

                        // Dual gating: either classification head score >= thresh OR strong keypoints detected
                        float effective_score = std::max(score, avg_kp_score);
                        if (effective_score < conf_thresh && valid_kps < 4) continue;

                        float x1, y1, x2, y2;
                        if (is_dfl) {
                            const float *bbox_cell = bbox_ptr + (cell_idx * 64);
                            float dfl[4];
                            for (int b = 0; b < 4; ++b) {
                                const float *reg = bbox_cell + (b * 16);
                                float max_val = -1e9f;
                                for (int i = 0; i < 16; ++i) max_val = std::max(max_val, reg[i]);
                                float sum_exp = 0.0f;
                                float exp_v[16];
                                for (int i = 0; i < 16; ++i) {
                                    exp_v[i] = std::exp(reg[i] - max_val);
                                    sum_exp += exp_v[i];
                                }
                                float val = 0.0f;
                                for (int i = 0; i < 16; ++i) val += (exp_v[i] / sum_exp) * i;
                                dfl[b] = val;
                            }
                            x1 = (gx + 0.5f - dfl[0]) * stride;
                            y1 = (gy + 0.5f - dfl[1]) * stride;
                            x2 = (gx + 0.5f + dfl[2]) * stride;
                            y2 = (gy + 0.5f + dfl[3]) * stride;
                        } else {
                            const float *bbox_cell = bbox_ptr + (cell_idx * 4);
                            x1 = (gx + 0.5f - bbox_cell[0]) * stride;
                            y1 = (gy + 0.5f - bbox_cell[1]) * stride;
                            x2 = (gx + 0.5f + bbox_cell[2]) * stride;
                            y2 = (gy + 0.5f + bbox_cell[3]) * stride;
                        }

                        float xmin = std::clamp((x1 - last_pad_x_) / (last_scale_ * orig_w), 0.0f, 1.0f);
                        float ymin = std::clamp((y1 - last_pad_y_) / (last_scale_ * orig_h), 0.0f, 1.0f);
                        float xmax = std::clamp((x2 - last_pad_x_) / (last_scale_ * orig_w), 0.0f, 1.0f);
                        float ymax = std::clamp((y2 - last_pad_y_) / (last_scale_ * orig_h), 0.0f, 1.0f);

                        if (xmax <= xmin || ymax <= ymin) continue;

                        // Sanity check: humans are vertical, reject extreme horizontal boxes (FM-VIS-009)
                        float box_w = (xmax - xmin) * orig_w;
                        float box_h = (ymax - ymin) * orig_h;
                        if (box_w > 1.8f * box_h) continue;

                        PersonPose pose;
                        pose.bbox = {xmin, ymin, xmax, ymax, std::max(effective_score, 0.50f), 0, "person"};

                        for (int k = 0; k < 17; ++k) {
                            float kx = kpt_cell[k * 3 + 0];
                            float ky = kpt_cell[k * 3 + 1];
                            float kscore_raw = kpt_cell[k * 3 + 2];
                            float kp_score = 1.0f / (1.0f + std::exp(-std::clamp(kscore_raw, -10.0f, 10.0f)));

                            float px = (kx * 2.0f + gx) * stride;
                            float py = (ky * 2.0f + gy) * stride;
                            float norm_x = std::clamp((px - last_pad_x_) / (last_scale_ * orig_w), 0.0f, 1.0f);
                            float norm_y = std::clamp((py - last_pad_y_) / (last_scale_ * orig_h), 0.0f, 1.0f);
                            pose.keypoints[k] = {norm_x, norm_y, kp_score};
                        }
                        candidates.push_back(pose);
                        stride_candidates++;
                    }
                }
                if (should_log) {
                    RCLCPP_INFO(this->get_logger(),
                        "🔍 [POSE-STRIDE %d] max_raw=%.3f, max_cls=%.3f, max_kpt=%.3f (thresh=%.2f), candidates=%d, ptrs: cls=%p bbox=%p kpt=%p",
                        stride, stride_max_raw, stride_max_score, stride_max_kpt, conf_thresh, stride_candidates,
                        (void*)cls_ptr, (void*)bbox_ptr, (void*)kpt_ptr);
                }
            }
            if (should_log) {
                RCLCPP_INFO(this->get_logger(),
                    "🔍 [POSE-SUMMARY] Total raw candidates: %zu", candidates.size());
            }
        }

        // NMS on candidates (IoU 0.45)
        std::sort(candidates.begin(), candidates.end(), [](const PersonPose &a, const PersonPose &b) {
            return a.bbox.confidence > b.bbox.confidence;
        });

        std::vector<PersonPose> nms_results;
        for (const auto &cand : candidates) {
            bool keep = true;
            for (const auto &selected : nms_results) {
                float ix1 = std::max(cand.bbox.xmin, selected.bbox.xmin);
                float iy1 = std::max(cand.bbox.ymin, selected.bbox.ymin);
                float ix2 = std::min(cand.bbox.xmax, selected.bbox.xmax);
                float iy2 = std::min(cand.bbox.ymax, selected.bbox.ymax);
                float iw = std::max(0.0f, ix2 - ix1);
                float ih = std::max(0.0f, iy2 - iy1);
                float inter_area = iw * ih;
                float area_a = (cand.bbox.xmax - cand.bbox.xmin) * (cand.bbox.ymax - cand.bbox.ymin);
                float area_b = (selected.bbox.xmax - selected.bbox.xmin) * (selected.bbox.ymax - selected.bbox.ymin);
                float union_area = area_a + area_b - inter_area;
                float iou = (union_area > 0.0f) ? (inter_area / union_area) : 0.0f;
                if (iou > 0.45f) {
                    keep = false;
                    break;
                }
            }
            if (keep) nms_results.push_back(cand);
        }

        if (should_log) {
            RCLCPP_INFO(this->get_logger(),
                "🔍 [POSE-SUMMARY] Total raw candidates: %zu, After NMS: %zu",
                candidates.size(), nms_results.size());
        }

        return nms_results;
    }

    std::vector<PersonPose> generate_sim_poses(const std::vector<DetectionBBox> &detections) {
        std::vector<PersonPose> sim_poses;
        for (const auto &det : detections) {
            if (det.label == "person" || det.class_id == 0) {
                PersonPose pose;
                pose.bbox = det;
                float cx = (det.xmin + det.xmax) * 0.5f;
                float bw = (det.xmax - det.xmin);
                float bh = (det.ymax - det.ymin);

                // Anatomically plausible 17 COCO keypoints
                pose.keypoints[0]  = {cx, det.ymin + bh * 0.10f, 0.95f}; // nose
                pose.keypoints[1]  = {cx - bw * 0.08f, det.ymin + bh * 0.08f, 0.92f}; // left_eye
                pose.keypoints[2]  = {cx + bw * 0.08f, det.ymin + bh * 0.08f, 0.92f}; // right_eye
                pose.keypoints[3]  = {cx - bw * 0.16f, det.ymin + bh * 0.10f, 0.88f}; // left_ear
                pose.keypoints[4]  = {cx + bw * 0.16f, det.ymin + bh * 0.10f, 0.88f}; // right_ear
                pose.keypoints[5]  = {cx - bw * 0.28f, det.ymin + bh * 0.22f, 0.94f}; // left_shoulder
                pose.keypoints[6]  = {cx + bw * 0.28f, det.ymin + bh * 0.22f, 0.94f}; // right_shoulder
                pose.keypoints[7]  = {cx - bw * 0.35f, det.ymin + bh * 0.40f, 0.90f}; // left_elbow
                pose.keypoints[8]  = {cx + bw * 0.35f, det.ymin + bh * 0.40f, 0.90f}; // right_elbow
                pose.keypoints[9]  = {cx - bw * 0.38f, det.ymin + bh * 0.58f, 0.89f}; // left_wrist
                pose.keypoints[10] = {cx + bw * 0.38f, det.ymin + bh * 0.58f, 0.89f}; // right_wrist
                pose.keypoints[11] = {cx - bw * 0.20f, det.ymin + bh * 0.55f, 0.92f}; // left_hip
                pose.keypoints[12] = {cx + bw * 0.20f, det.ymin + bh * 0.55f, 0.92f}; // right_hip
                pose.keypoints[13] = {cx - bw * 0.22f, det.ymin + bh * 0.75f, 0.91f}; // left_knee
                pose.keypoints[14] = {cx + bw * 0.22f, det.ymin + bh * 0.75f, 0.91f}; // right_knee
                pose.keypoints[15] = {cx - bw * 0.24f, det.ymin + bh * 0.95f, 0.87f}; // left_ankle
                pose.keypoints[16] = {cx + bw * 0.24f, det.ymin + bh * 0.95f, 0.87f}; // right_ankle

                sim_poses.push_back(pose);
            }
        }
        return sim_poses;
    }

    void publish_poses(const std_msgs::msg::Header &header,
                       const std::vector<PersonPose> &poses,
                       int img_width, int img_height) {
        if (!enable_pose_ || poses.empty()) return;

        // 1. Detection2DArray for Pose
        if (pub_pose_detections_ && pub_pose_detections_->get_subscription_count() > 0) {
            vision_msgs::msg::Detection2DArray det_array;
            det_array.header = header;
            for (const auto &p : poses) {
                vision_msgs::msg::Detection2D det;
                det.header = header;
                det.bbox.center.position.x = (p.bbox.xmin + p.bbox.xmax) * 0.5 * img_width;
                det.bbox.center.position.y = (p.bbox.ymin + p.bbox.ymax) * 0.5 * img_height;
                det.bbox.size_x = (p.bbox.xmax - p.bbox.xmin) * img_width;
                det.bbox.size_y = (p.bbox.ymax - p.bbox.ymin) * img_height;

                vision_msgs::msg::ObjectHypothesisWithPose hyp;
                hyp.hypothesis.class_id = "person_pose";
                hyp.hypothesis.score = p.bbox.confidence;
                det.results.push_back(hyp);
                det_array.detections.push_back(det);
            }
            pub_pose_detections_->publish(det_array);
        }

        // 2. Visualization Markers for Skeletons (RViz / Foxglove 3D & 2D)
        if (pub_pose_markers_ && pub_pose_markers_->get_subscription_count() > 0) {
            visualization_msgs::msg::MarkerArray marker_array;
            
            for (size_t i = 0; i < poses.size(); ++i) {
                const auto &p = poses[i];

                // Spheres for Joints
                visualization_msgs::msg::Marker joints_marker;
                joints_marker.header = header;
                joints_marker.header.frame_id = "camera_optical_frame";
                joints_marker.ns = "skeleton_joints";
                joints_marker.id = static_cast<int>(i * 2);
                joints_marker.type = visualization_msgs::msg::Marker::SPHERE_LIST;
                joints_marker.action = visualization_msgs::msg::Marker::ADD;
                joints_marker.scale.x = 0.04;
                joints_marker.scale.y = 0.04;
                joints_marker.scale.z = 0.04;
                joints_marker.color.r = 0.0f;
                joints_marker.color.g = 1.0f;
                joints_marker.color.b = 0.8f;
                joints_marker.color.a = 0.9f;
                joints_marker.lifetime = rclcpp::Duration::from_seconds(0.5);

                for (const auto &kp : p.keypoints) {
                    if (kp.confidence >= pose_conf_threshold_) {
                        geometry_msgs::msg::Point pt;
                        pt.x = (kp.x - 0.5f) * 1.5f;
                        pt.y = (kp.y - 0.5f) * 1.5f;
                        pt.z = 1.5f;
                        joints_marker.points.push_back(pt);
                    }
                }
                marker_array.markers.push_back(joints_marker);

                // Lines for Bones
                visualization_msgs::msg::Marker bones_marker;
                bones_marker.header = header;
                bones_marker.header.frame_id = "camera_optical_frame";
                bones_marker.ns = "skeleton_bones";
                bones_marker.id = static_cast<int>(i * 2 + 1);
                bones_marker.type = visualization_msgs::msg::Marker::LINE_LIST;
                bones_marker.action = visualization_msgs::msg::Marker::ADD;
                bones_marker.scale.x = 0.02; // Bone line width
                bones_marker.color.r = 1.0f;
                bones_marker.color.g = 0.85f;
                bones_marker.color.b = 0.0f;
                bones_marker.color.a = 0.85f;
                bones_marker.lifetime = rclcpp::Duration::from_seconds(0.5);

                for (const auto &pair : COCO_SKELETON_PAIRS) {
                    const auto &kp1 = p.keypoints[pair.first];
                    const auto &kp2 = p.keypoints[pair.second];
                    if (kp1.confidence >= pose_conf_threshold_ && kp2.confidence >= pose_conf_threshold_) {
                        geometry_msgs::msg::Point pt1, pt2;
                        pt1.x = (kp1.x - 0.5f) * 1.5f;
                        pt1.y = (kp1.y - 0.5f) * 1.5f;
                        pt1.z = 1.5f;
                        pt2.x = (kp2.x - 0.5f) * 1.5f;
                        pt2.y = (kp2.y - 0.5f) * 1.5f;
                        pt2.z = 1.5f;
                        bones_marker.points.push_back(pt1);
                        bones_marker.points.push_back(pt2);
                    }
                }
                marker_array.markers.push_back(bones_marker);
            }

            pub_pose_markers_->publish(marker_array);
        }
    }

    // Parameters
    std::string hef_path_;
    bool sim_mode_;
    std::string rgb_topic_;
    double vlm_rate_hz_;
    float conf_threshold_{0.55f};
    bool hailo_ready_{false};
    uint64_t num_frames_processed_{0};

    float last_scale_{1.0f};
    int last_pad_x_{0};
    int last_pad_y_{0};
    int last_img_w_{640};
    int last_img_h_{400};

    std::string yolo_input_name_;
    int yolo_input_h_{640};
    int yolo_input_w_{640};
    std::unordered_map<std::string, std::vector<uint8_t>> input_buffers_;
    std::unordered_map<std::string, std::vector<float>> output_buffers_;
    std::vector<ScaleInfo> scales_;

    std::chrono::steady_clock::time_point last_inference_time_;

    // ROS 2 Interfaces
    std::unique_ptr<image_transport::ImageTransport> it_;
    image_transport::Subscriber sub_rgb_;
    image_transport::Publisher pub_annotated_;
    rclcpp::Publisher<sensor_msgs::msg::CompressedImage>::SharedPtr pub_annotated_compressed_;
    rclcpp::Publisher<vision_msgs::msg::Detection2DArray>::SharedPtr pub_detections_;
    rclcpp::Publisher<robopy_controller::msg::SemanticObjectArray>::SharedPtr pub_semantic_objects_;
    rclcpp::Publisher<std_msgs::msg::Float32MultiArray>::SharedPtr pub_vlad_;

    // Pose Tracking on Shared VDevice (SPEC-03 F1 Upgrade)
    bool enable_pose_{false};
    std::string pose_hef_path_;
    float pose_conf_threshold_{0.50f};
    bool hailo_pose_ready_{false};

    std::string pose_input_name_;
    int pose_input_h_{640};
    int pose_input_w_{640};
    std::unordered_map<std::string, std::vector<uint8_t>> pose_input_buffers_;
    std::unordered_map<std::string, std::vector<float>> pose_output_buffers_;

    rclcpp::Publisher<visualization_msgs::msg::MarkerArray>::SharedPtr pub_pose_markers_;
    rclcpp::Publisher<vision_msgs::msg::Detection2DArray>::SharedPtr pub_pose_detections_;

#if HAILO_CPP_AVAILABLE
    std::unique_ptr<hailort::VDevice> vdevice_;
    std::shared_ptr<hailort::InferModel> infer_model_;
    std::unique_ptr<hailort::ConfiguredInferModel> configured_infer_model_;
    std::unique_ptr<hailort::ConfiguredInferModel::Bindings> bindings_;

    std::shared_ptr<hailort::InferModel> infer_model_pose_;
    std::unique_ptr<hailort::ConfiguredInferModel> configured_infer_model_pose_;
    std::unique_ptr<hailort::ConfiguredInferModel::Bindings> bindings_pose_;
#endif
};

int main(int argc, char **argv) {
    rclcpp::init(argc, argv);
    auto node = std::make_shared<HailoBridgeNodeCpp>();
    node->init();
    rclcpp::spin(node);
    rclcpp::shutdown();
    return 0;
}
