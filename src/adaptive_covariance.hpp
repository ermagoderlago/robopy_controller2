#pragma once

#include <deque>
#include <algorithm>
#include <cmath>
#include <array>
#include <string>
#include <cstdio>

/**
 * @brief Adaptive covariance estimator for visual odometry
 * 
 * Dynamically adjusts odometry covariance based on:
 * - Number of inliers (feature tracking quality)
 * - Motion magnitude (motion blur risk)
 * - Recent tracking history (consecutive failures)
 * - Depth quality (percentage of valid depth pixels)
 * 
 * Usage with robot_localization EKF:
 * - Low covariance = "Trust me, I'm confident"
 * - High covariance = "Don't trust me, use wheel odometry instead"
 */
class AdaptiveCovarianceEstimator {
public:
    struct Params {
        // Base covariances (when everything is perfect)
        double base_position_cov = 0.01;      // meters²
        double base_orientation_cov = 0.05;   // radians²
        
        // Quality thresholds
        int excellent_inliers = 50;           // Above this: trust fully
        int good_inliers = 30;                // Above this: trust moderately
        int minimum_inliers = 15;             // Below this: distrust
        
        double low_motion_threshold = 0.05;   // m - below this is static
        double high_motion_threshold = 0.5;   // m - above this is motion blur
        
        // Scaling factors
        double low_inliers_penalty = 10.0;    // Multiply cov by this if inliers < minimum
        double motion_blur_penalty = 5.0;     // Multiply cov by this if high motion
        double lost_tracking_penalty = 50.0;  // Multiply cov by this if tracking lost
        
        // History
        size_t history_window = 10;           // Frames to consider for trend
    };

    AdaptiveCovarianceEstimator() : params_() {}
    
    AdaptiveCovarianceEstimator(const Params& params) 
        : params_(params) {}

    /**
     * @brief Update estimator with new measurement
     * 
     * @param inliers Number of RANSAC inliers
     * @param total_matches Total feature matches before RANSAC
     * @param translation_norm Magnitude of translation (meters)
     * @param depth_valid_ratio Ratio of valid depth pixels (0.0 to 1.0)
     * @param tracking_lost True if tracking is lost
     */
    void update(int inliers, int total_matches, double translation_norm,
               double depth_valid_ratio = 1.0, bool tracking_lost = false) {
        
        // Store history
        inliers_history_.push_back(inliers);
        motion_history_.push_back(translation_norm);
        
        if (inliers_history_.size() > params_.history_window) {
            inliers_history_.pop_front();
            motion_history_.pop_front();
        }
        
        // Calculate quality factors
        double quality_factor = 1.0;
        
        // 1. Inlier-based quality
        if (tracking_lost) {
            quality_factor *= params_.lost_tracking_penalty;
        } else if (inliers < params_.minimum_inliers) {
            quality_factor *= params_.low_inliers_penalty;
        } else if (inliers < params_.good_inliers) {
            // Linear interpolation between minimum and good
            double ratio = (double)(inliers - params_.minimum_inliers) / 
                          (params_.good_inliers - params_.minimum_inliers);
            quality_factor *= (params_.low_inliers_penalty * (1.0 - ratio) + 1.0 * ratio);
        } else if (inliers < params_.excellent_inliers) {
            // Linear interpolation between good and excellent
            double ratio = (double)(inliers - params_.good_inliers) / 
                          (params_.excellent_inliers - params_.good_inliers);
            quality_factor *= (2.0 * (1.0 - ratio) + 1.0 * ratio);
        }
        // else: excellent tracking, quality_factor stays at 1.0
        
        // 2. Motion-based quality
        if (translation_norm > params_.high_motion_threshold) {
            // High speed = motion blur risk
            quality_factor *= params_.motion_blur_penalty;
        } else if (translation_norm < params_.low_motion_threshold) {
            // Very slow motion = more confident (static scene)
            quality_factor *= 0.5;
        }
        
        // 3. Depth quality
        if (depth_valid_ratio < 0.3) {
            // Most depth invalid (e.g., glass, dark surfaces)
            quality_factor *= 5.0;
        } else if (depth_valid_ratio < 0.6) {
            quality_factor *= 2.0;
        }
        
        // 4. Historical trend
        if (inliers_history_.size() >= 5) {
            // Check for degrading tracking
            int recent_avg = 0;
            for (size_t i = inliers_history_.size() - 5; i < inliers_history_.size(); i++) {
                recent_avg += inliers_history_[i];
            }
            recent_avg /= 5;
            
            if (recent_avg < params_.good_inliers) {
                // Tracking is degrading
                quality_factor *= 1.5;
            }
        }
        
        // Store current quality
        current_quality_factor_ = quality_factor;
    }
    
    /**
     * @brief Get position covariance (x, y, z diagonal elements)
     */
    double getPositionCovariance() const {
        return params_.base_position_cov * current_quality_factor_;
    }
    
    /**
     * @brief Get orientation covariance (roll, pitch, yaw diagonal elements)
     */
    double getOrientationCovariance() const {
        return params_.base_orientation_cov * current_quality_factor_;
    }
    
    /**
     * @brief Fill nav_msgs::Odometry covariance array (6x6 = 36 elements)
     * 
     * ROS covariance matrix layout (row-major):
     * [x, y, z, rot_x, rot_y, rot_z]
     * Only diagonal elements are set (assuming independence)
     */
    void fillCovarianceMatrix(std::array<double, 36>& cov) const {
        // Clear all
        std::fill(cov.begin(), cov.end(), 0.0);
        
        double pos_cov = getPositionCovariance();
        double rot_cov = getOrientationCovariance();
        
        // Position (x, y, z)
        cov[0]  = pos_cov;  // x
        cov[7]  = pos_cov;  // y
        cov[14] = pos_cov * 2.0;  // z (less confident in vertical)
        
        // Orientation (roll, pitch, yaw)
        cov[21] = rot_cov * 1.5;  // roll
        cov[28] = rot_cov * 1.5;  // pitch
        cov[35] = rot_cov;        // yaw (most confident)
    }
    
    /**
     * @brief Get quality assessment (0.0 = worst, 1.0 = best)
     */
    double getQuality() const {
        return 1.0 / std::max(1.0, current_quality_factor_);
    }
    
    /**
     * @brief Get diagnostics string
     */
    std::string getDiagnostics() const {
        char buf[256];
        snprintf(buf, sizeof(buf), 
            "Quality: %.2f%% | Pos Cov: %.4f | Rot Cov: %.4f | Factor: %.2f",
            getQuality() * 100.0,
            getPositionCovariance(),
            getOrientationCovariance(),
            current_quality_factor_);
        return std::string(buf);
    }
    
    /**
     * @brief Reset history
     */
    void reset() {
        inliers_history_.clear();
        motion_history_.clear();
        current_quality_factor_ = 1.0;
    }

private:
    Params params_;
    std::deque<int> inliers_history_;
    std::deque<double> motion_history_;
    double current_quality_factor_ = 1.0;
};
