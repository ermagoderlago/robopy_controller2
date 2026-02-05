#!/bin/bash
# tf_verify.sh - Script per verificare la struttura frame TF

echo "=================================="
echo "TF Frame Hierarchy Verification"
echo "=================================="
echo ""

# Richiedi all'utente se il nodo è in esecuzione
read -p "Sono i nodi ROS in esecuzione? (s/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Ss]$ ]]; then
    echo "Avvia i nodi e riprova:"
    echo "  ros2 launch robopy_controller test_odometry_launch.py"
    exit 1
fi

echo ""
echo "1️⃣  Visualizzazione Gerarchia Frame"
echo "======================================"
echo "Esecuzione: ros2 run tf2_tools view_frames"
echo ""
echo "Output atteso:"
echo "  odom"
echo "    └─ base_link"
echo "        ├─ camera_link"
echo "        │   └─ camera_optical_frame"
echo "        └─ imu_link"
echo ""
read -p "Premi INVIO per visualizzare (timeout 5s)..."
timeout 5 ros2 run tf2_tools view_frames || true
echo ""

echo ""
echo "2️⃣  Test Trasformazione: base_link → camera_optical_frame"
echo "=========================================================="
echo "Esecuzione: ros2 run tf2_ros tf2_echo base_link camera_optical_frame"
echo ""
echo "Output atteso: Translation e Rotation fissi"
echo ""
read -p "Premi INVIO per testare (timeout 3s)..."
timeout 3 ros2 run tf2_ros tf2_echo base_link camera_optical_frame || true
echo ""

echo ""
echo "3️⃣  Test Trasformazione: odom → base_link"
echo "=========================================="
echo "Esecuzione: ros2 run tf2_ros tf2_echo odom base_link"
echo ""
echo "Output atteso: Cambia nel tempo (odometria)"
echo ""
read -p "Premi INVIO per testare (timeout 3s)..."
timeout 3 ros2 run tf2_ros tf2_echo odom base_link || true
echo ""

echo ""
echo "4️⃣  Verifica Topics Pubblicati"
echo "=============================="
ros2 topic list | grep -E "camera|depth|imu|odom"
echo ""

echo ""
echo "5️⃣  Verifica Frame ID nei Messaggi"
echo "=================================="
echo "Sottoscrizione a /camera/image_raw (1 messaggio)..."
ros2 topic echo /camera/image_raw -n 1 --field header | head -3
echo ""

echo ""
echo "✅ Verifica completata!"
echo ""
echo "Se tutti i test passano, la struttura TF è corretta."
echo "Se ci sono problemi, controlla i messaggi di errore sopra."
