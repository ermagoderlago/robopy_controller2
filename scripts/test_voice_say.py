#!/usr/bin/env python3
"""
test_voice_say.py - Direct Voice Test via ReSpeaker VUI AudioData topic
"""
import os
import sys
import time
import subprocess
import tempfile

for p in [
    "/home/robopy/ros2_venv/lib/python3.11/site-packages",
    "/mnt/ssd/robopy_controller_host",
    "/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages"
]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
try:
    from robopy_controller.msg import AudioData
    print("DEBUG: Successfully imported robopy_controller.msg.AudioData")
except Exception as e:
    print(f"DEBUG: Failed importing robopy_controller.msg.AudioData: {e}")
    from audio_common_msgs.msg import AudioData
    print("DEBUG: Fallback to audio_common_msgs.msg.AudioData")
from gtts import gTTS

def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "Ciao! Sono Marcus. Questo è un test della mia voce."
    print(f"🎙️ Generazione voce per: '{text}'...")

    mp3_path = "/tmp/test_voice.mp3"
    raw_path = "/tmp/test_voice.raw"

    tts = gTTS(text=text, lang='it')
    tts.save(mp3_path)

    cmd = [
        "ffmpeg", "-y", "-i", mp3_path,
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1", raw_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    with open(raw_path, "rb") as f:
        pcm_bytes = f.read()

    print(f"📦 Generati {len(pcm_bytes)} bytes PCM 16kHz.")

    rclpy.init()
    node = Node('test_voice_node')
    pub = node.create_publisher(AudioData, '/respeaker/speaker_audio', 10)

    print("⏳ Attendo subscriber su /respeaker/speaker_audio...")
    for _ in range(50):
        rclpy.spin_once(node, timeout_sec=0.1)
        if pub.get_subscription_count() > 0:
            print(f"✅ Subscriber connesso! ({pub.get_subscription_count()} attivi)")
            break
        time.sleep(0.1)

    chunk_size = 2048
    print("🔊 Invio streaming audio a respeaker_vui_node...")
    for i in range(0, len(pcm_bytes), chunk_size):
        chunk = pcm_bytes[i:i + chunk_size]
        msg = AudioData()
        msg.data = list(chunk)
        pub.publish(msg)
        rclpy.spin_once(node, timeout_sec=0.01)
        time.sleep(chunk_size / 32000.0 * 0.95)

    print("✅ Riproduzione completata. Attendo svuotamento buffer...")
    for _ in range(20):
        rclpy.spin_once(node, timeout_sec=0.1)
        time.sleep(0.1)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
