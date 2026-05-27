#pragma once

#include <depthai/depthai.hpp>
#include <chrono>
#include <deque>
#include <memory>
#include <mutex>

/**
 * @brief Synchronizes DepthAI frames by timestamp with tolerance
 * 
 * Solves the problem of RGB and Depth cameras running on different clocks.
 * Uses sequence numbers and timestamps to match frames correctly.
 */
template<typename T1, typename T2>
class FrameSynchronizer {
public:
    struct SyncedPair {
        std::shared_ptr<T1> first;
        std::shared_ptr<T2> second;
        std::chrono::time_point<std::chrono::steady_clock> timestamp;
        bool valid = false;
    };

    /**
     * @param max_time_diff_ms Maximum allowed time difference between frames (default 20ms)
     * @param max_queue_size Maximum frames to buffer before dropping old ones (default 10)
     */
    FrameSynchronizer(int max_time_diff_ms = 20, size_t max_queue_size = 10)
        : max_time_diff_(std::chrono::milliseconds(max_time_diff_ms))
        , max_queue_size_(max_queue_size) {}

    /**
     * @brief Add a frame from the first stream
     */
    void addFirst(std::shared_ptr<T1> frame) {
        std::lock_guard<std::mutex> lock(mutex_);
        
        auto ts = frame->getTimestamp();
        queue1_.push_back({frame, ts});
        
        // Limit queue size
        if (queue1_.size() > max_queue_size_) {
            queue1_.pop_front();
        }
        
        tryMatch();
    }

    /**
     * @brief Add a frame from the second stream
     */
    void addSecond(std::shared_ptr<T2> frame) {
        std::lock_guard<std::mutex> lock(mutex_);
        
        auto ts = frame->getTimestamp();
        queue2_.push_back({frame, ts});
        
        if (queue2_.size() > max_queue_size_) {
            queue2_.pop_front();
        }
        
        tryMatch();
    }

    /**
     * @brief Get the next synchronized pair if available
     * @return SyncedPair with valid=true if frames are available, valid=false otherwise
     */
    SyncedPair getNext() {
        std::lock_guard<std::mutex> lock(mutex_);
        
        if (synced_pairs_.empty()) {
            return SyncedPair{nullptr, nullptr, {}, false};
        }
        
        auto pair = synced_pairs_.front();
        synced_pairs_.pop_front();
        return pair;
    }

    /**
     * @brief Check if synchronized pairs are available
     */
    bool hasNext() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return !synced_pairs_.empty();
    }

    /**
     * @brief Get statistics
     */
    struct Stats {
        size_t queue1_size;
        size_t queue2_size;
        size_t synced_count;
        size_t total_matched;
        size_t total_dropped;
    };

    Stats getStats() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return {queue1_.size(), queue2_.size(), synced_pairs_.size(), 
                total_matched_, total_dropped_};
    }

    /**
     * @brief Clear all queues
     */
    void clear() {
        std::lock_guard<std::mutex> lock(mutex_);
        queue1_.clear();
        queue2_.clear();
        synced_pairs_.clear();
    }

private:
    struct TimestampedFrame {
        std::shared_ptr<dai::ADatatype> frame;
        std::chrono::time_point<std::chrono::steady_clock> timestamp;
    };

    void tryMatch() {
        // Try to find matching pairs within time tolerance
        auto it1 = queue1_.begin();
        auto it2 = queue2_.begin();

        while (it1 != queue1_.end() && it2 != queue2_.end()) {
            auto diff = it1->timestamp - it2->timestamp;
            auto abs_diff = std::chrono::abs(diff);

            if (abs_diff <= max_time_diff_) {
                // Found a match!
                auto ts = (diff.count() > 0) ? it2->timestamp : it1->timestamp; // Use earlier timestamp
                
                synced_pairs_.push_back({
                    std::dynamic_pointer_cast<T1>(it1->frame),
                    std::dynamic_pointer_cast<T2>(it2->frame),
                    ts,
                    true
                });
                
                total_matched_++;
                
                // Remove matched frames
                it1 = queue1_.erase(it1);
                it2 = queue2_.erase(it2);
            } else if (diff.count() < 0) {
                // Frame 1 is older, try to match with next frame 2
                ++it1;
            } else {
                // Frame 2 is older, try to match with next frame 1
                ++it2;
            }
        }

        // Remove old frames that can't be matched anymore
        cleanupOldFrames();
    }

    void cleanupOldFrames() {
        if (queue1_.empty() || queue2_.empty()) return;

        // Remove frames from queue1 that are too old to match anything in queue2
        auto oldest2 = queue2_.front().timestamp;
        while (!queue1_.empty()) {
            auto diff = oldest2 - queue1_.front().timestamp;
            if (diff > max_time_diff_) {
                queue1_.pop_front();
                total_dropped_++;
            } else {
                break;
            }
        }

        // Remove frames from queue2 that are too old to match anything in queue1
        auto oldest1 = queue1_.front().timestamp;
        while (!queue2_.empty()) {
            auto diff = oldest1 - queue2_.front().timestamp;
            if (diff > max_time_diff_) {
                queue2_.pop_front();
                total_dropped_++;
            } else {
                break;
            }
        }
    }

    mutable std::mutex mutex_;
    std::deque<TimestampedFrame> queue1_;
    std::deque<TimestampedFrame> queue2_;
    std::deque<SyncedPair> synced_pairs_;
    std::chrono::milliseconds max_time_diff_;
    size_t max_queue_size_;
    size_t total_matched_ = 0;
    size_t total_dropped_ = 0;
};
