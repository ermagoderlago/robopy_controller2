#!/usr/bin/env python3
"""
Multi-Turn Voice & Cognitive Benchmark for Marcus Robot
Tests R4 requirement: 6+ consecutive turns against live or local cognitive pipeline
Validates:
- 100% relevant Italian answers
- 0 empty responses
- 0 generic "Sono pronto" fallbacks
- Full TRINITY memory & telemetry recall (Identity, User, Memory, Battery, Room, Capabilities)
"""

import sys
import os
import time
import re
from typing import List, Dict, Any, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class VoiceCognitiveBenchmarkNode(Node):
    def __init__(self):
        super().__init__('voice_cognitive_benchmark_node')
        self.pub_text = self.create_publisher(String, '/robopy/conversation_rx', 10)
        self.pub_voice_test = self.create_publisher(String, 'ai/input/voice_test', 10)
        self.sub_response = self.create_subscription(
            String,
            '/ai/conversation/response',
            self._on_response,
            10
        )
        self.last_response: Optional[str] = None
        self.response_received_event = False
        self.t_send: float = 0.0
        self.response_latency: float = 0.0

    def _on_response(self, msg: String):
        self.response_latency = time.perf_counter() - self.t_send
        self.last_response = msg.data.strip()
        self.response_received_event = True
        self.get_logger().info(f"📥 Received Response ({self.response_latency*1000:.1f}ms): {self.last_response}")

    def send_turn(self, prompt: str, source: str = "text", timeout_sec: float = 12.0) -> Dict[str, Any]:
        self.last_response = None
        self.response_received_event = False
        self.response_latency = 0.0

        msg = String()
        msg.data = prompt
        self.t_send = time.perf_counter()

        if source == "audio":
            self.pub_voice_test.publish(msg)
            self.get_logger().info(f"📤 Sent [AUDIO/VOICE_TEST]: '{prompt}'")
        else:
            self.pub_text.publish(msg)
            self.get_logger().info(f"📤 Sent [CONVERSATION_RX]: '{prompt}'")

        start_wait = time.perf_counter()
        while not self.response_received_event and (time.perf_counter() - start_wait < timeout_sec):
            rclpy.spin_once(self, timeout_sec=0.05)

        elapsed = time.perf_counter() - start_wait
        success = self.response_received_event and bool(self.last_response)

        return {
            "prompt": prompt,
            "source": source,
            "success": success,
            "response": self.last_response or "",
            "latency_ms": self.response_latency * 1000.0 if self.response_received_event else elapsed * 1000.0,
            "timed_out": not self.response_received_event,
        }


def run_benchmark():
    rclpy.init()
    node = VoiceCognitiveBenchmarkNode()

    # Wait for ROS 2 DDS discovery
    node.get_logger().info("⏳ Attesa scoperta publisher e subscriber ROS 2 DDS...")
    disc_start = time.time()
    while time.time() - disc_start < 10.0:
        rclpy.spin_once(node, timeout_sec=0.1)
        if node.pub_text.get_subscription_count() > 0 and node.sub_response.get_publisher_count() > 0:
            break
    node.get_logger().info(
        f"✅ DDS Discovery completata in {time.time() - disc_start:.2f}s "
        f"(subscribers: {node.pub_text.get_subscription_count()}, publishers: {node.sub_response.get_publisher_count()})"
    )
    time.sleep(1.0)

    turns = [
        {
            "id": 1,
            "name": "Robot Identity & Greeting",
            "prompt": "Ciao Marcus, chi sei?",
            "source": "text",
            "expected_keywords": ["marcus", "assistente", "robot"],
            "forbidden_phrases": ["non riesco a connettermi al cervello"],
        },
        {
            "id": 2,
            "name": "User Identity Recall",
            "prompt": "Chi sono io?",
            "source": "text",
            "expected_keywords": ["luca", "creatore", "sviluppatore", "utente"],
            "forbidden_phrases": ["non ti conosco", "non riesco a connettermi"],
        },
        {
            "id": 3,
            "name": "TRINITY Memory & Zettelkasten Recall",
            "prompt": "Cosa ti ricordi di me?",
            "source": "text",
            "expected_keywords": ["ricord", "pizza", "svilupp", "interazion", "memoria"],
            "forbidden_phrases": ["sono pronto, dimmi pure cosa posso fare"],
        },
        {
            "id": 4,
            "name": "Battery & Hardware Telemetry",
            "prompt": "Quanta batteria hai?",
            "source": "text",
            "expected_keywords": ["batteria", "volt", "%", "alimentazione", "carica"],
            "forbidden_phrases": ["non lo so", "errore"],
        },
        {
            "id": 5,
            "name": "Spatial Orientation & Location",
            "prompt": "Dove siamo?",
            "source": "text",
            "expected_keywords": ["troviamo", "stanza", "salotto", "mappa", "qui"],
            "forbidden_phrases": ["non riesco a connettermi"],
        },
        {
            "id": 6,
            "name": "Robot Capabilities Overview",
            "prompt": "Cosa sai fare?",
            "source": "text",
            "expected_keywords": ["muovermi", "navig", "lidar", "trinity", "esplorare", "mappar", "batteria"],
            "forbidden_phrases": ["non so fare nulla"],
        },
    ]

    print("\n" + "=" * 75)
    print("🤖 MARCUS AI - R4 MULTI-TURN VOICE & COGNITIVE BENCHMARK")
    print("=" * 75)

    results = []
    all_passed = True

    for turn in turns:
        t_id = turn["id"]
        t_name = turn["name"]
        prompt = turn["prompt"]
        source = turn["source"]
        exp_kw = turn["expected_keywords"]
        forbid = turn["forbidden_phrases"]

        print(f"\n--- [TURN {t_id}/6: {t_name}] ---")
        res = node.send_turn(prompt, source=source, timeout_sec=12.0)
        resp_text = res["response"]
        resp_lower = resp_text.lower()
        latency = res["latency_ms"]

        # Evaluation criteria
        passed = True
        reasons = []

        if res["timed_out"] or not resp_text:
            passed = False
            reasons.append("TIMED OUT or EMPTY RESPONSE")
        else:
            # Check for generic forbidden phrases
            for fb in forbid:
                if fb in resp_lower:
                    passed = False
                    reasons.append(f"Contains forbidden phrase: '{fb}'")

            # Check for generic fallback
            if "ciao! sono pronto, dimmi pure cosa posso fare per te" in resp_lower:
                passed = False
                reasons.append("Generic uncontextualized fallback emitted")

            # Check for keyword relevance
            kw_match = any(kw in resp_lower for kw in exp_kw)
            if not kw_match:
                passed = False
                reasons.append(f"Missing expected keywords {exp_kw}")

        status_str = "✅ PASSED" if passed else "❌ FAILED"
        if not passed:
            all_passed = False

        print(f"Result: {status_str} ({latency:.1f}ms)")
        print(f"Reply:  '{resp_text}'")
        if reasons:
            print(f"Errors: {', '.join(reasons)}")

        results.append({
            "id": t_id,
            "name": t_name,
            "passed": passed,
            "latency_ms": latency,
            "reply": resp_text,
            "errors": reasons
        })

        # Brief pause between dialogue turns to mirror natural conversational tempo
        time.sleep(1.0)

    print("\n" + "=" * 75)
    print("📊 BENCHMARK SUMMARY REPORT")
    print("=" * 75)
    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)
    avg_latency = sum(r["latency_ms"] for r in results) / total_count if total_count else 0.0

    print(f"Total Turns:       {total_count}")
    print(f"Passed Turns:      {passed_count}/{total_count} ({passed_count/total_count*100:.1f}%)")
    print(f"Average Latency:   {avg_latency:.1f}ms")
    print("-" * 75)
    for r in results:
        status_icon = "✅" if r["passed"] else "❌"
        print(f"{status_icon} Turn {r['id']} [{r['name']}]: {r['latency_ms']:.1f}ms - '{r['reply'][:60]}...'")

    node.destroy_node()
    rclpy.shutdown()

    if all_passed:
        print("\n🎉 [BENCHMARK SUCCESS] 100% of cognitive conversational turns PASSED!\n")
        return 0
    else:
        print(f"\n⚠️ [BENCHMARK INCOMPLETE] {total_count - passed_count} turns failed.\n")
        return 1


if __name__ == "__main__":
    sys.exit(run_benchmark())
