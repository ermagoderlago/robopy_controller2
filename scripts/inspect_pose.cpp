#include <iostream>
#include <vector>
#include <map>
#include <cmath>
#include <algorithm>
#include <chrono>
#include <hailo/hailort.hpp>

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

    // Setup input buffer (mid-gray 114)
    auto inp_name = infer_model->inputs()[0].name();
    size_t in_size = infer_model->inputs()[0].get_frame_size();
    std::vector<uint8_t> in_buf(in_size, 114);
    bindings.input(inp_name)->set_buffer(hailort::MemoryView(in_buf.data(), in_buf.size()));

    // Setup output buffers
    std::map<std::string, std::vector<float>> out_buffers;
    for (const auto &outp : infer_model->outputs()) {
        std::string name = outp.name();
        size_t num_floats = outp.get_frame_size() / sizeof(float);
        out_buffers[name].resize(num_floats);
        bindings.output(name)->set_buffer(hailort::MemoryView(out_buffers[name].data(), num_floats * sizeof(float)));
    }

    std::cout << "Running inference with dummy 114 frame..." << std::endl;
    hailo_status st = configured.run(bindings, std::chrono::milliseconds(1000));
    if (st != HAILO_SUCCESS) {
        std::cerr << "Inference failed: " << st << std::endl;
        return 1;
    }
    std::cout << "Inference SUCCESS! Results:" << std::endl;

    for (const auto &kv : out_buffers) {
        const auto &name = kv.first;
        const auto &buf = kv.second;
        float min_v = 1e9, max_v = -1e9, sum_v = 0;
        int non_zero = 0;
        int count_gt_02 = 0;
        int count_gt_05 = 0;
        for (float v : buf) {
            min_v = std::min(min_v, v);
            max_v = std::max(max_v, v);
            sum_v += v;
            if (std::abs(v) > 1e-5) non_zero++;
            if (v >= 0.2f) count_gt_02++;
            if (v >= 0.5f) count_gt_05++;
        }
        float mean_v = sum_v / buf.size();
        std::cout << "Layer " << name << ": size=" << buf.size()
                  << " min=" << min_v << " max=" << max_v << " mean=" << mean_v
                  << " non_zero=" << non_zero << " >=0.2: " << count_gt_02 << " >=0.5: " << count_gt_05 << std::endl;
        std::cout << "  First 5 values: ";
        for (size_t i = 0; i < std::min((size_t)5, buf.size()); ++i) {
            std::cout << buf[i] << " ";
        }
        std::cout << std::endl;
    }

    return 0;
}
