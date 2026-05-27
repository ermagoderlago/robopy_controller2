#pragma once

#include <rclcpp/rclcpp.hpp>
#include <tf2_ros/buffer.h>
#include <tf2_ros/transform_listener.h>
#include <geometry_msgs/msg/transform_stamped.hpp>
#include <Eigen/Dense>
#include <opencv2/opencv.hpp>

#include <thread>
#include <chrono>

/**
 * @brief Handles dynamic camera-to-base frame transformations using TF2
 */
class CameraTransformHandler {
public:
    CameraTransformHandler(rclcpp::Node* node, 
                          const std::string& base_frame = "base_link",
                          const std::string& camera_frame = "camera_link",
                          const std::string& camera_optical_frame = "camera_optical_frame")
        : node_(node)
        , base_frame_(base_frame)
        , camera_frame_(camera_frame)
        , camera_optical_frame_(camera_optical_frame)
        , tf_buffer_(std::make_shared<tf2_ros::Buffer>(node->get_clock()))
        , tf_listener_(std::make_shared<tf2_ros::TransformListener>(*tf_buffer_))
    {
        waitForTransform();
    }
    
    /**
     * @brief Convert camera motion to base frame
     */
    bool cameraMotionToBase(const cv::Mat& rvec, const cv::Mat& tvec,
                           cv::Mat& R_out, cv::Mat& t_out) const {
        if (!transform_available_) {
            // Fallback: identity transform
            cv::Mat R_cam;
            cv::Rodrigues(rvec, R_cam);
            R_out = R_cam.t();
            t_out = -R_out * tvec;
            return true;
        }
        
        cv::Mat R_cam;
        cv::Rodrigues(rvec, R_cam);
        
        // Camera motion is inverse of object motion from solvePnP
        cv::Mat R_cam_inv = R_cam.t();
        cv::Mat t_cam_inv = -R_cam_inv * tvec;
        
        // Transform to base frame
        cv::Mat R_base = R_cam_to_base_ * R_cam_inv * R_cam_to_base_.t();
        cv::Mat t_base = R_cam_to_base_ * t_cam_inv;
        
        R_out = R_base;
        t_out = t_base;
        return true;
    }
    
    /**
     * @brief Convert 4x4 transformation matrix
     */
    cv::Mat cameraMatrixToBase(const cv::Mat& T_cam) const {
        cv::Mat R_cam = T_cam(cv::Rect(0, 0, 3, 3));
        cv::Mat t_cam = T_cam(cv::Rect(3, 0, 1, 3));
        
        cv::Mat R_base = R_cam_to_base_ * R_cam * R_cam_to_base_.t();
        cv::Mat t_base = R_cam_to_base_ * t_cam;
        
        cv::Mat T_base = cv::Mat::eye(4, 4, CV_64F);
        R_base.copyTo(T_base(cv::Rect(0, 0, 3, 3)));
        t_base.copyTo(T_base(cv::Rect(3, 0, 1, 3)));
        
        return T_base;
    }
    
    /**
     * @brief Get frame names
     */
    std::string getBaseFrame() const { return base_frame_; }
    std::string getCameraFrame() const { return camera_frame_; }
    std::string getOpticalFrame() const { return camera_optical_frame_; }
    
    /**
     * @brief Check if transform is available
     */
    bool isAvailable() const { return transform_available_; }

private:
    void waitForTransform() {
        RCLCPP_INFO(node_->get_logger(), "Waiting for transform %s -> %s...", 
            base_frame_.c_str(), camera_optical_frame_.c_str());
        
        for (int i = 0; i < 50; i++) {
            try {
                auto transform = tf_buffer_->lookupTransform(
                    base_frame_, camera_optical_frame_, 
                    tf2::TimePointZero, std::chrono::milliseconds(100));
                
                updateMatrices(transform);
                transform_available_ = true;
                
                RCLCPP_INFO(node_->get_logger(), "✓ Transform loaded");
                printTransform();
                return;
                
            } catch (const tf2::TransformException& ex) {
                if (i % 10 == 0) {
                    RCLCPP_WARN(node_->get_logger(), "Transform not ready: %s", ex.what());
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
        
        RCLCPP_ERROR(node_->get_logger(), 
            "Failed to get transform, using identity");
        
        // Fallback to identity
        R_cam_to_base_ = cv::Mat::eye(3, 3, CV_64F);
        t_cam_to_base_ = cv::Mat::zeros(3, 1, CV_64F);
        transform_available_ = false;
    }
    
    void updateMatrices(const geometry_msgs::msg::TransformStamped& tf) {
        auto& translation = tf.transform.translation;
        auto& rotation = tf.transform.rotation;
        
        // Translation
        t_cam_to_base_ = (cv::Mat_<double>(3, 1) << 
            translation.x, translation.y, translation.z);
        
        // Rotation (quaternion to matrix)
        Eigen::Quaterniond q(rotation.w, rotation.x, rotation.y, rotation.z);
        Eigen::Matrix3d R_eigen = q.toRotationMatrix();
        
        R_cam_to_base_ = (cv::Mat_<double>(3, 3) << 
            R_eigen(0,0), R_eigen(0,1), R_eigen(0,2),
            R_eigen(1,0), R_eigen(1,1), R_eigen(1,2),
            R_eigen(2,0), R_eigen(2,1), R_eigen(2,2));
    }
    
    void printTransform() const {
        if (!transform_available_) return;
        
        RCLCPP_INFO(node_->get_logger(), "  Translation: [%.3f, %.3f, %.3f]",
            t_cam_to_base_.at<double>(0), 
            t_cam_to_base_.at<double>(1), 
            t_cam_to_base_.at<double>(2));
    }

    rclcpp::Node* node_;
    std::string base_frame_;
    std::string camera_frame_;
    std::string camera_optical_frame_;
    
    std::shared_ptr<tf2_ros::Buffer> tf_buffer_;
    std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
    
    cv::Mat R_cam_to_base_;
    cv::Mat t_cam_to_base_;
    bool transform_available_ = false;
};
