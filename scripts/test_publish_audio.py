import os
import sys
import time
import subprocess
import tempfile

# Force install path to the very front of sys.path
install_pkg = "/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages"
while "/mnt/ssd/robopy_controller_host" in sys.path:
    sys.path.remove("/mnt/ssd/robopy_controller_host")
if install_pkg not in sys.path:
    sys.path.insert(0, install_pkg)

import rclpy
from rclpy.node import Node
from robopy_controller.msg import AudioData
from gtts import gTTS

print("SUCCESS: Imported AudioData ->", AudioData)

text = sys.argv[1] if len(sys.argv) > 1 else "Ciao Luca! Questo è un test con il messaggio ROS 2 compilato corretto."
print(f"🎙️ Generazione TTS 24kHz per: '{text}'...")

mp3_path = "/tmp/test_robopy_voice.mp3"
raw_path = "/tmp/test_robopy_voice.raw"

tts = gTTS(text=text, lang='it')
tts.save(mp3_path)

cmd = [
    "ffmpeg", "-y", "-i", mp3_path,
    "-f", "s16le", "-acodec", "pcm_s16le",
    "-ar", "24000", "-ac", "1", raw_path
]
subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

with open(raw_path, "rb") as f:
    pcm_bytes = f.read()

print(f"📦 PCM generato: {len(pcm_bytes)} bytes a 24000 Hz.")

rclpy.init()
node = Node('test_robopy_audio_pub')
pub = node.create_publisher(AudioData, '/respeaker/speaker_audio', 10)

print("⏳ Attendo subscriber su /respeaker/speaker_audio...")
for i in range(50):
    rclpy.spin_once(node, timeout_sec=0.1)
    if pub.get_subscription_count() > 0:
        print(f"✅ Subscriber connesso! ({pub.get_subscription_count()} attivi)")
        break
    time.sleep(0.1)

if pub.get_subscription_count() == 0:
    print("❌ ERRORE: Nessun subscriber trovato su /respeaker/speaker_audio!")
    sys.exit(1)

chunk_size = 2048
delay = (chunk_size / (24000 * 2)) * 0.95
print(f"🔊 Streaming di {len(pcm_bytes)} bytes (delay={delay:.4f}s per chunk)...")

for i in range(0, len(pcm_bytes), chunk_size):
    chunk = pcm_bytes[i:i + chunk_size]
    msg = AudioData()
    msg.data = list(chunk)
    pub.publish(msg)
    rclpy.spin_once(node, timeout_sec=0.005)
    time.sleep(delay)

print("✅ Streaming completato! Attendo 2 secondi per svuotamento buffer DAC...")
time.sleep(2.0)
node.destroy_node()
rclpy.shutdown()
print("🎉 Test terminato.")
