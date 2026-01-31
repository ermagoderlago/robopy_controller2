#pragma once

#include <deque>
#include <algorithm>
#include <cmath>
#include <array>
#include <mutex>
#include <chrono>
#include <sstream>

/**
 * @brief Enhanced adaptive covariance estimator with direction consistency and yaw-only mode
 */
class AdaptiveCovarianceEstimator {
public:
    struct Params {
        // Base covariances
        double base_position_cov = 0.01;
        double base_orientation_cov = 0.05;
        
        // Quality thresholds
        int excellent_inliers = 50;
        int good_inliers = 30;
        int minimum_inliers = 15;
        
        // Motion thresholds
        double low_motion_threshold = 0.05;
        double high_motion_threshold = 0.5;
        
        // Scaling factors
        double low_inliers_penalty = 10.0;
        double motion_blur_penalty = 5.0;
        double lost_tracking_penalty = 50.0;
        double imu_assisted_boost = 0.5;
        double direction_inconsistency_penalty = 3.0;
        
        // Yaw-only mode
        double yaw_only_position_scale = 10.0;
        double yaw_only_orientation_scale = 2.0;
        
        // History
        size_t history_window = 10;
    };

    AdaptiveCovarianceEstimator() : params_() {}
    
    explicit AdaptiveCovarianceEstimator(const Params& params) 
        : params_(params) {}

    void update(int inliers, int total_matches, double translation_norm,
               double rotation_norm, double depth_valid_ratio = 1.0,
               bool tracking_lost = false, bool imu_assisted = false,
               double direction_penalty = 1.0) {
        
        std::lock_guard<std::mutex> lock(mutex_);
        
        (void)total_matches; // reserved
        
        // Store history
        inliers_history_.push_back(inliers);
        translation_history_.push_back(translation_norm);
        rotation_history_.push_back(rotation_norm);
        
        if (inliers_history_.size() > params_.history_window) {
            inliers_history_.pop_front();
            translation_history_.pop_front();
            rotation_history_.pop_front();
        }
        
        // Calculate quality factor
        double quality_factor = 1.0;
        
        // 1. Inlier-based quality
        if (tracking_lost) {
            quality_factor *= params_.lost_tracking_penalty;
        } else if (inliers < params_.minimum_inliers) {
            quality_factor *= params_.low_inliers_penalty;
        } else if (inliers < params_.good_inliers) {
            double ratio = static_cast<double>(inliers - params_.minimum_inliers) / 
                          (params_.good_inliers - params_.minimum_inliers);
            quality_factor *= (params_.low_inliers_penalty * (1.0 - ratio) + 1.0 * ratio);
        } else if (inliers < params_.excellent_inliers) {
            double ratio = static_cast<double>(inliers - params_.good_inliers) / 
                          (params_.excellent_inliers - params_.good_inliers);
            quality_factor *= (2.0 * (1.0 - ratio) + 1.0 * ratio);
        }
        
        // 2. Motion-based quality
        if (translation_norm > params_.high_motion_threshold) {
            quality_factor *= params_.motion_blur_penalty;
        } else if (translation_norm < params_.low_motion_threshold) {
            // Static scene - more confident
            quality_factor *= 0.3;
        }
        
        // 3. Rotation-only penalty
        if (translation_norm < 0.01 && rotation_norm > 0.1) {
            quality_factor *= 2.0;
        }
        
        // 4. Depth quality
        if (depth_valid_ratio < 0.3) {
            quality_factor *= 3.0;
        } else if (depth_valid_ratio < 0.6) {
            quality_factor *= 1.5;
        }
        
        // 5. IMU assistance bonus
        if (imu_assisted && quality_factor > 1.0) {
            quality_factor *= params_.imu_assisted_boost;
        }
        
        // 6. Direction inconsistency penalty
        quality_factor *= direction_penalty;
        
        // 7. Historical trend
        if (inliers_history_.size() >= 5) {
            int recent_avg = 0;
            for (size_t i = inliers_history_.size() - 5; i < inliers_history_.size(); i++) {
                recent_avg += inliers_history_[i];
            }
            recent_avg /= 5;
            
            if (recent_avg < params_.good_inliers) {
                quality_factor *= 1.5;
            }
        }
        
        // Check temporary increase
        auto now = std::chrono::steady_clock::now();
        if (temporary_factor_ > 1.0 && now < temporary_end_time_) {
            quality_factor *= temporary_factor_;
        } else {
            temporary_factor_ = 1.0;
        }
        
        current_quality_factor_ = quality_factor;
    }
    
    /**
     * @brief Temporary increase for loop closures
     */
    void temporaryIncrease(double factor, double duration_seconds) {
        if (temporary_factor_ > 1.0) {
            return;
        }
        temporary_factor_ = factor;
        temporary_end_time_ = std::chrono::steady_clock::now() + 
                             std::chrono::milliseconds(static_cast<int64_t>(duration_seconds * 1000));
    }
    
    void fillCovarianceMatrix(std::array<double, 36>& cov) const {
        std::lock_guard<std::mutex> lock(mutex_);
        std::fill(cov.begin(), cov.end(), 0.0);
        
        double pos_cov = getPositionCovarianceUnlocked();
        double rot_cov = getOrientationCovarianceUnlocked();
        
        // Position (x, y, z) - VO is good at this
        cov[0]  = pos_cov;        // x
        cov[7]  = pos_cov;        // y  
        cov[14] = pos_cov * 3.0;  // z (less confident)
        
        // Orientation - VO is weak at roll/pitch, okay at yaw
        cov[21] = rot_cov * 2.0;  // roll - let IMU handle
        cov[28] = rot_cov * 2.0;  // pitch - let IMU handle
        cov[35] = rot_cov * 0.5;  // yaw - VO can help
    }
    
    void fillYawOnlyCovariance(std::array<double, 36>& cov, double scale = 10.0) const {
        std::lock_guard<std::mutex> lock(mutex_);
        std::fill(cov.begin(), cov.end(), 0.0);
        
        double pos_cov = getPositionCovarianceUnlocked() * scale;
        double rot_cov = getOrientationCovarianceUnlocked() * (scale / 5.0);
        
        // High covariance for position (don't trust position updates)
        cov[0]  = pos_cov;        // x
        cov[7]  = pos_cov;        // y  
        cov[14] = pos_cov * 2.0;  // z
        
        // High covariance for roll/pitch, moderate for yaw
        cov[21] = rot_cov * 5.0;  // roll
        cov[28] = rot_cov * 5.0;  // pitch
        cov[35] = rot_cov;        // yaw (still somewhat confident)
    }
    
    double getPositionCovariance() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return getPositionCovarianceUnlocked();
    }
    
    double getOrientationCovariance() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return getOrientationCovarianceUnlocked();
    }
    
    double getQuality() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return 1.0 / std::max(1.0, current_quality_factor_);
    }
    
    std::string getDiagnostics() const {
        std::lock_guard<std::mutex> lock(mutex_);
        std::ostringstream oss;
        oss << "Quality: " << static_cast<int>(100.0 / std::max(1.0, current_quality_factor_)) 
            << "% | Pos Cov: " << getPositionCovarianceUnlocked()
            << " | Rot Cov: " << getOrientationCovarianceUnlocked()
            << " | Factor: " << current_quality_factor_;
        return oss.str();
    }
    
    void reset() {
        std::lock_guard<std::mutex> lock(mutex_);
        inliers_history_.clear();
        translation_history_.clear();
        rotation_history_.clear();
        current_quality_factor_ = 1.0;
        temporary_factor_ = 1.0;
    }

private:
    double getPositionCovarianceUnlocked() const {
        return params_.base_position_cov * current_quality_factor_;
    }
    
    double getOrientationCovarianceUnlocked() const {
        return params_.base_orientation_cov * current_quality_factor_;
    }

    mutable std::mutex mutex_;
    Params params_;
    std::deque<int> inliers_history_;
    std::deque<double> translation_history_;
    std::deque<double> rotation_history_;
    double current_quality_factor_ = 1.0;
    double temporary_factor_ = 1.0;
    std::chrono::steady_clock::time_point temporary_end_time_;
};
