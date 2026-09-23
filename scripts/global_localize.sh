#!/usr/bin/env bash
# global_localize.sh
# ==================
# Forza AMCL a una localizzazione globale da zero (reinitialize_global_localization)
# e poi comanda Marcus a eseguire una lenta spin di 360° per convergere le particelle.
#
# USO (sul robot):
#   bash /mnt/ssd/robopy_controller_host/scripts/global_localize.sh
#
# PREREQUISITI:
#   - Marcus avviato in modalità --amcl
#   - Spazio libero intorno al robot
#   - SPEC-01: omega usato = 0.30 rad/s < 1.80 rad/s limite Zona Rossa

set -e

export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null

echo "======================================"
echo "  Marcus — Global Localization Spin"
echo "======================================"
echo ""
echo "[1/3] Resetto la localizzazione AMCL (particelle distribuite globalmente)..."
ros2 service call /reinitialize_global_localization std_srvs/srv/Empty "{}" 2>/dev/null \
  && echo "  ✅ AMCL reinizializzato globalmente" \
  || echo "  ⚠️  Service non risposto (AMCL potrebbe non essere attivo)"

sleep 1.0

echo ""
echo "[2/3] Avvio spin lenta 360° (omega=0.30 rad/s, durata ~21s)..."
echo "      Osserva il robot e la mappa su Foxglove: le particelle devono convergere."

# Calcolo: 2*pi / 0.30 = 20.9 secondi per un giro
# Pubblichiamo a 20Hz per 22 secondi (leggermente sopra per sicurezza)
python3 - << 'PYEOF'
import os, sys, time
for p in ["/home/robopy/ros2_venv/lib/python3.11/site-packages"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
import math, threading

rclpy.init()
node = Node("global_localize_spin")
pub  = node.create_publisher(Twist, "/cmd_vel", 10)

spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
spin_thread.start()

omega   = 0.30          # rad/s — lento, sotto Zona Rossa (max 1.80)
target  = 2.0 * math.pi  # 360°
duration = target / omega   # ~20.9s
dt      = 1.0 / 20          # 20 Hz

t = Twist()
t.angular.z = omega
t.linear.x  = 0.0

start = time.time()
while time.time() - start < duration:
    pub.publish(t)
    time.sleep(dt)

# Stop
for _ in range(10):
    pub.publish(Twist())
    time.sleep(0.05)

print(f"  ✅ Spin completata ({duration:.1f}s)")
rclpy.shutdown()
PYEOF

echo ""
echo "[3/3] Verifica convergenza AMCL..."
sleep 2.0
ros2 topic echo /amcl_pose --once 2>/dev/null | grep -E "(x:|y:|z:|w:)" | head -8 \
  || echo "  ⚠️  Nessuna posa AMCL ricevuta. Prova a muovere lentamente il robot."

echo ""
echo "======================================"
echo "  Se la mappa e il robot si sono allineati: OK!"
echo "  Se ancora disallineati:"
echo "    1. Eseguire di nuovo questo script"
echo "    2. Oppure usare Foxglove per impostare manualmente la posa iniziale"
echo "       (topic: /initialpose, tipo: geometry_msgs/PoseWithCovarianceStamped)"
echo "    3. Oppure calibrare wheel_separation:"
echo "       python3 scripts/calibrate_wheel_separation.py"
echo "======================================"
