#!/usr/bin/env python3
"""
Test Qwen Model Execution on Marcus (Hailo NPU & ROS 2)
=========================================================
"""

import sys
import json
import time
import urllib.request
import urllib.error

def test_hailo_ollama():
    print("\n--- [TEST 1] Testing hailo-ollama REST API ---")
    
    # 1. Test GET /api/tags
    url_tags = "http://localhost:11434/api/tags"
    try:
        req = urllib.request.Request(url_tags)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print(f"   --> Raw /api/tags response: {data}")
    except Exception as e:
        print(f"   ⚠️ Could not query /api/tags: {e}")

    # 2. Test POST /api/generate with qwen2.5-instruct:1.5b
    url_gen = "http://localhost:11434/api/generate"
    payload = {
        "model": "qwen2.5-instruct:1.5b",
        "prompt": "Ciao, sei operativo su Marcus?",
        "stream": False
    }
    
    headers = {"Content-Type": "application/json"}
    data_bytes = json.dumps(payload).encode('utf-8')
    
    start_time = time.time()
    try:
        print("   --> Sending prompt to qwen2.5-instruct:1.5b...")
        req = urllib.request.Request(url_gen, data=data_bytes, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            elapsed = time.time() - start_time
            res = json.loads(resp.read().decode('utf-8'))
            print(f"✅ Response received in {elapsed:.2f}s:")
            print(f"   --> {res}")
            return True
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP Error {e.code}: {e.read().decode('utf-8', errors='ignore')}")
    except Exception as e:
        print(f"❌ Error during generation: {e}")
    return False

def test_ros2_hailo_vlm():
    print("\n--- [TEST 2] Testing ROS 2 Hailo VLM Node ---")
    try:
        import rclpy
        from robopy_controller.srv import AskVisualQuestion
    except ImportError:
        print("⚠️ ROS 2 / robopy_controller not sourced.")
        return False

    rclpy.init()
    node = rclpy.create_node('qwen_test_node')
    client = node.create_client(AskVisualQuestion, '/hailo/vlm/ask_question')

    if not client.wait_for_service(timeout_sec=2.0):
        print("⚠️ Service /hailo/vlm/ask_question is not running.")
        node.destroy_node()
        rclpy.shutdown()
        return False

    req = AskVisualQuestion.Request()
    req.question = "Descrivi la scena attuale."
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)

    if future.result():
        res = future.result()
        print(f"✅ ROS 2 VLM Response: {res.answer} (Success={res.success})")
        success = res.success
    else:
        print("❌ Service call failed or timed out.")
        success = False

    node.destroy_node()
    rclpy.shutdown()
    return success

if __name__ == '__main__':
    print("=" * 60)
    print(" 🤖 MARCUS QWEN TEST SUITE")
    print("=" * 60)
    ok1 = test_hailo_ollama()
    ok2 = test_ros2_hailo_vlm()
    print("\n" + "=" * 60)
    print(f" RESULT: hailo-ollama: {'✅ PASS' if ok1 else '❌ FAIL'} | ROS 2 VLM: {'✅ PASS' if ok2 else '⚠️ INACTIVE'}")
    print("=" * 60)
