#include <iostream>
#include <vector>
#include <map>
#include <cmath>
#include <algorithm>
#include <hailo/hailort.hpp>
#include <opencv2/opencv.hpp>

float decode_dfl(const float *reg) {
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
    return val;
}

float sigmoid(float v) {
    return 1.0f / (1.0f + std::exp(-std::clamp(v, -10.0f, 10.0f)));
}

float compute_iou(float ax1, float ay1, float ax2, float ay2,
                  float bx1, float by1, float bx2, float by2) {
    float ix1 = std::max(ax1, bx1);
    float iy1 = std::max(ay1, by1);
    float ix2 = std::min(ax2, bx2);
    float iy2 = std::min(ay2, by2);
    float iw = std::max(0.0f, ix2 - ix1);
    float ih = std::max(0.0f, iy2 - iy1);
    float inter = iw * ih;
    float area_a = (ax2 - ax1) * (ay2 - ay1);
    float area_b = (bx2 - bx1) * (by2 - by1);
    float union_area = area_a + area_b - inter;
    return (union_area > 0.0f) ? (inter / union_area) : 0.0f;
}

struct Keypoint {
    float x, y, conf;
};

int main(int argc, char** argv) {
    std::string img_path = (argc > 1) ? argv[1] : "/home/robopy/latest_camera_frame.jpg";
    cv::Mat img = cv::imread(img_path);
    if (img.empty()) {
        std::cerr << "Cannot open " << img_path << std::endl;
        return 1;
    }

    float scale = std::min(640.0f / img.cols, 640.0f / img.rows);
    int nw = std::round(img.cols * scale);
    int nh = std::round(img.rows * scale);
    int pad_x = (640 - nw) / 2;
    int pad_y = (640 - nh) / 2;
    cv::Mat letterbox(640, 640, CV_8UC3, cv::Scalar(114, 114, 114));
    cv::Mat resized;
    cv::resize(img, resized, cv::Size(nw, nh));
    cv::Mat roi = letterbox(cv::Rect(pad_x, pad_y, nw, nh));
    cv::cvtColor(resized, roi, cv::COLOR_BGR2RGB);

    auto vdevice = hailort::VDevice::create().release();
    auto model = vdevice->create_infer_model("/mnt/ssd/models/yolov8s_pose.hef").release();
    for (const auto &outp : model->outputs()) {
        model->output(outp.name())->set_format_type(HAILO_FORMAT_TYPE_FLOAT32);
    }
    auto conf = model->configure().release();
    auto bindings = conf.create_bindings().release();

    std::string in_name = model->inputs()[0].name();
    bindings.input(in_name)->set_buffer(hailort::MemoryView(letterbox.data, 640*640*3));

    std::map<std::string, std::vector<float>> out_bufs;
    for (const auto &outp : model->outputs()) {
        size_t n = outp.get_frame_size() / sizeof(float);
        out_bufs[outp.name()].resize(n);
        bindings.output(outp.name())->set_buffer(hailort::MemoryView(out_bufs[outp.name()].data(), n * sizeof(float)));
    }

    conf.run(bindings, std::chrono::milliseconds(1000));

    // Target Person Box (in original image coords [0, 1])
    // From our detection on latest_camera_frame:
    // Box in model: [480, 420, 620, 620]
    // Normalized in image:
    float xmin = (480.0f - pad_x) / (scale * img.cols);
    float ymin = (420.0f - pad_y) / (scale * img.rows);
    float xmax = (620.0f - pad_x) / (scale * img.cols);
    float ymax = (620.0f - pad_y) / (scale * img.rows);

    float p_x1 = xmin * img.cols * scale + pad_x;
    float p_y1 = ymin * img.rows * scale + pad_y;
    float p_x2 = xmax * img.cols * scale + pad_x;
    float p_y2 = ymax * img.rows * scale + pad_y;
    float p_cx = (p_x1 + p_x2) * 0.5f;
    float p_cy = (p_y1 + p_y2) * 0.5f;
    float p_w = p_x2 - p_x1;
    float p_h = p_y2 - p_y1;

    struct ScaleInfo {
        int stride;
        int gw, gh;
        const float *bbox{nullptr};
        const float *kpt{nullptr};
    };
    std::vector<ScaleInfo> scales = {
        {8, 80, 80, nullptr, nullptr},
        {16, 40, 40, nullptr, nullptr},
        {32, 20, 20, nullptr, nullptr}
    };

    for (auto &sc : scales) {
        size_t cells = sc.gw * sc.gh;
        for (const auto &kv : out_bufs) {
            if (kv.second.size() == cells * 64) sc.bbox = kv.second.data();
            else if (kv.second.size() == cells * 51) sc.kpt = kv.second.data();
        }
    }

    // Best winner search
    float best_score = -1.0f;
    int best_stride = -1, best_gx = -1, best_gy = -1;
    std::vector<Keypoint> best_kpts(17);
    float best_iou = 0.0f;

    for (const auto &sc : scales) {
        if (!sc.bbox || !sc.kpt) continue;
        int c_gx = std::clamp(static_cast<int>(std::round(p_cx / sc.stride)), 0, sc.gw - 1);
        int c_gy = std::clamp(static_cast<int>(std::round(p_cy / sc.stride)), 0, sc.gh - 1);

        int r_gx = std::max(2, static_cast<int>(std::ceil(p_w * 0.35f / sc.stride)));
        int r_gy = std::max(2, static_cast<int>(std::ceil(p_h * 0.35f / sc.stride)));

        int min_gx = std::max(0, c_gx - r_gx);
        int max_gx = std::min(sc.gw - 1, c_gx + r_gx);
        int min_gy = std::max(0, c_gy - r_gy);
        int max_gy = std::min(sc.gh - 1, c_gy + r_gy);

        for (int gy = min_gy; gy <= max_gy; ++gy) {
            // Reject padding cells
            float cell_y_px = (gy + 0.5f) * sc.stride;
            if (cell_y_px < pad_y || cell_y_px > 640 - pad_y) continue;

            for (int gx = min_gx; gx <= max_gx; ++gx) {
                int cell_idx = gy * sc.gw + gx;
                const float *bcell = sc.bbox + (cell_idx * 64);
                const float *kcell = sc.kpt + (cell_idx * 51);

                float dfl[4];
                for (int b = 0; b < 4; ++b) dfl[b] = decode_dfl(bcell + b * 16);
                float bx1 = (gx + 0.5f - dfl[0]) * sc.stride;
                float by1 = (gy + 0.5f - dfl[1]) * sc.stride;
                float bx2 = (gx + 0.5f + dfl[2]) * sc.stride;
                float by2 = (gy + 0.5f + dfl[3]) * sc.stride;

                float iou = compute_iou(p_x1, p_y1, p_x2, p_y2, bx1, by1, bx2, by2);
                if (iou < 0.25f) continue;

                int vis = 0;
                float sum_conf = 0.0f;
                std::vector<Keypoint> candidate_kpts(17);
                for (int k = 0; k < 17; ++k) {
                    float conf = sigmoid(kcell[k * 3 + 2]);
                    float kx = (kcell[k * 3 + 0] * 2.0f + gx) * sc.stride;
                    float ky = (kcell[k * 3 + 1] * 2.0f + gy) * sc.stride;
                    float norm_x = std::clamp((kx - pad_x) / (scale * img.cols), 0.0f, 1.0f);
                    float norm_y = std::clamp((ky - pad_y) / (scale * img.rows), 0.0f, 1.0f);
                    candidate_kpts[k] = {norm_x, norm_y, conf};
                    if (conf >= 0.30f) {
                        vis++;
                        sum_conf += conf;
                    }
                }

                if (vis < 4) continue;

                float torso_conf = (candidate_kpts[5].conf + candidate_kpts[6].conf +
                                    candidate_kpts[11].conf + candidate_kpts[12].conf) * 0.25f;

                float score = iou * 15.0f + sum_conf * 1.0f + vis * 0.5f + torso_conf * 3.0f;
                if (score > best_score) {
                    best_score = score;
                    best_stride = sc.stride;
                    best_gx = gx;
                    best_gy = gy;
                    best_iou = iou;
                    best_kpts = candidate_kpts;
                }
            }
        }
    }

    std::cout << "WINNER: Stride=" << best_stride << " Cell=(" << best_gx << "," << best_gy
              << ") IoU=" << best_iou << " Score=" << best_score << std::endl;
    static const char* KP_NAMES[] = {
        "Nose", "LEye", "REye", "LEar", "REar",
        "LShoulder", "RShoulder", "LElbow", "RElbow", "LWrist", "RWrist",
        "LHip", "RHip", "LKnee", "RKnee", "LAnkle", "RAnkle"
    };
    for (int k = 0; k < 17; ++k) {
        std::cout << "  " << KP_NAMES[k] << ": (" << best_kpts[k].x << ", " << best_kpts[k].y
                  << ") conf=" << best_kpts[k].conf << std::endl;
    }
    return 0;
}
