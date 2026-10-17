#include <iostream>
#include <vector>
#include <map>
#include <cmath>
#include <algorithm>
#include <chrono>
#include <hailo/hailort.hpp>
#include <opencv2/opencv.hpp>

int main() {
    std::string hef_path = "/mnt/ssd/models/yolov8s_pose.hef";
    std::cout << "Loading HEF: " << hef_path << std::endl;

    auto vdevice_exp = hailort::VDevice::create();
    if (!vdevice_exp) {
        std::cerr << "Failed to create VDevice: " << vdevice_exp.status() << std::endl;
        return 1;
    }
    auto vdevice = vdevice_exp.release();

    auto infer_model_exp = vdevice->create_infer_model(hef_path);
    if (!infer_model_exp) {
        std::cerr << "Failed to create InferModel: " << infer_model_exp.status() << std::endl;
        return 1;
    }
    auto infer_model = infer_model_exp.release();
    infer_model->set_batch_size(1);

    std::cout << "=== INPUTS ===" << std::endl;
    for (const auto &inp : infer_model->inputs()) {
        auto s = inp.shape();
        std::cout << "Input: " << inp.name() << " shape=(" << s.height << "," << s.width << "," << s.features << ")" << std::endl;
    }

    std::cout << "=== OUTPUTS (Configuring Float32) ===" << std::endl;
    for (const auto &outp : infer_model->outputs()) {
        auto s = outp.shape();
        std::cout << "Output: " << outp.name() << " shape=(" << s.height << "," << s.width << "," << s.features << ")"
                  << " frame_size=" << outp.get_frame_size() << std::endl;
        auto out_stream = infer_model->output(outp.name());
        if (out_stream) {
            out_stream->set_format_type(HAILO_FORMAT_TYPE_FLOAT32);
        }
    }

    auto configured_exp = infer_model->configure();
    if (!configured_exp) {
        std::cerr << "Failed to configure InferModel: " << configured_exp.status() << std::endl;
        return 1;
    }
    auto configured = configured_exp.release();

    auto bindings_exp = configured.create_bindings();
    if (!bindings_exp) {
        std::cerr << "Failed to create bindings: " << bindings_exp.status() << std::endl;
        return 1;
    }
    auto bindings = bindings_exp.release();

    // Setup input buffer (mid-gray 114 default or load image)
    auto inp_name = infer_model->inputs()[0].name();
    size_t in_size = infer_model->inputs()[0].get_frame_size();
    std::vector<uint8_t> in_buf(in_size, 114);

    cv::Mat img = cv::imread("/home/robopy/latest_camera_frame.jpg");
    if (img.empty()) img = cv::imread("/home/robopy/test.jpg");
    if (!img.empty()) {
        std::cout << "Loaded real image: " << img.cols << "x" << img.rows << std::endl;
        cv::Mat letterbox(640, 640, CV_8UC3, in_buf.data());
        letterbox.setTo(cv::Scalar(114, 114, 114));
        float scale = std::min(640.0f / img.cols, 640.0f / img.rows);
        int nw = std::clamp(static_cast<int>(std::round(img.cols * scale)), 1, 640);
        int nh = std::clamp(static_cast<int>(std::round(img.rows * scale)), 1, 640);
        int px = (640 - nw) / 2;
        int py = (640 - nh) / 2;
        cv::Mat resized;
        cv::resize(img, resized, cv::Size(nw, nh));
        cv::Mat roi = letterbox(cv::Rect(px, py, nw, nh));
        cv::cvtColor(resized, roi, cv::COLOR_BGR2RGB);
        std::cout << "Letterboxed image: scale=" << scale << " pad=(" << px << "," << py << ")" << std::endl;
    } else {
        std::cout << "No image found, using dummy 114 frame" << std::endl;
    }

    bindings.input(inp_name)->set_buffer(hailort::MemoryView(in_buf.data(), in_buf.size()));

    // Setup output buffers
    std::map<std::string, std::vector<float>> out_buffers;
    for (const auto &outp : infer_model->outputs()) {
        std::string name = outp.name();
        size_t num_floats = outp.get_frame_size() / sizeof(float);
        out_buffers[name].resize(num_floats);
        bindings.output(name)->set_buffer(hailort::MemoryView(out_buffers[name].data(), num_floats * sizeof(float)));
    }

    std::cout << "Running inference..." << std::endl;
    hailo_status st = configured.run(bindings, std::chrono::milliseconds(1000));
    if (st != HAILO_SUCCESS) {
        std::cerr << "Inference failed: " << st << std::endl;
        return 1;
    }
    std::cout << "Inference SUCCESS! Results:" << std::endl;

    auto sigmoid = [](float v) {
        return 1.0f / (1.0f + std::exp(-std::clamp(v, -10.0f, 10.0f)));
    };

    struct TestScale {
        std::string name;
        int stride;
        int gw;
        int gh;
    };
    TestScale test_scales[] = {
        {"yolov8s_pose/conv45", 8, 80, 80},
        {"yolov8s_pose/conv59", 16, 40, 40},
        {"yolov8s_pose/conv72", 32, 20, 20}
    };

    for (const auto &ts : test_scales) {
        if (!out_buffers.count(ts.name)) continue;
        const auto &buf = out_buffers[ts.name];
        std::cout << "\n=== Analysis for " << ts.name << " (Stride " << ts.stride << ", " << ts.gw << "x" << ts.gh << ") ===" << std::endl;
        
        // Find cell with highest sum of visibility
        int best_cell = -1;
        float best_vis_sum = -1.0f;
        int best_vis_count = 0;
        for (int c = 0; c < ts.gw * ts.gh; ++c) {
            float sum_v = 0.0f;
            int count_v = 0;
            for (int k = 0; k < 17; ++k) {
                float conf = sigmoid(buf[c * 51 + k * 3 + 2]);
                if (conf >= 0.25f) count_v++;
                sum_v += conf;
            }
            if (sum_v > best_vis_sum) {
                best_vis_sum = sum_v;
                best_cell = c;
                best_vis_count = count_v;
            }
        }

        int bgx = best_cell % ts.gw;
        int bgy = best_cell / ts.gw;
        std::cout << "Best Cell (" << bgx << ", " << bgy << ") - sum_vis=" << best_vis_sum << " vis_count=" << best_vis_count << ":" << std::endl;
        for (int k = 0; k < 17; ++k) {
            float kx = buf[best_cell * 51 + k * 3 + 0];
            float ky = buf[best_cell * 51 + k * 3 + 1];
            float ks = buf[best_cell * 51 + k * 3 + 2];
            float conf = sigmoid(ks);
            float px_official = (kx * 2.0f + bgx) * ts.stride;
            float py_official = (ky * 2.0f + bgy) * ts.stride;
            float px_no2 = (kx + bgx) * ts.stride;
            float py_no2 = (ky + bgy) * ts.stride;
            std::cout << "  k=" << k << ": raw=(" << kx << ", " << ky << ", " << ks << ") conf=" << conf
                      << " | official_px=(" << px_official << ", " << py_official << ")"
                      << " | no2_px=(" << px_no2 << ", " << py_no2 << ")" << std::endl;
        }
    }

    return 0;
}
