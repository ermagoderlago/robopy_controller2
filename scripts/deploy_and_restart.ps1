# Deploy automatico e riavvio per Marcus (Bio-Cognitive Evolution)
$target_ip = "192.168.1.11"
Write-Host "Verifica connettività verso Marcus ($target_ip)..."

$online = $false
for ($i = 0; $i -lt 10; $i++) {
    $ping = ping -n 1 -w 500 $target_ip
    if ($LASTEXITCODE -eq 0) {
        $online = $true
        break
    }
    Start-Sleep -Seconds 1
}

if (-not $online) {
    Write-Host "MARCUS_OFFLINE"
    exit 2
}

Write-Host "🟢 Marcus ONLINE! Arresto preventivo nodi e watchdog (marcus_core_rules.md)..."
ssh -o StrictHostKeyChecking=no "robopy@${target_ip}" "sudo systemctl stop marcus-watchdog 2>/dev/null; pkill -9 -f watchdog.sh 2>/dev/null; pkill -9 -f restart_hailo.sh 2>/dev/null; pkill -9 -f ros2 2>/dev/null; echo 'NODES_STOPPED'"

Write-Host "📦 Trasferimento sorgenti C++ e CMakeLists.txt..."
scp -o StrictHostKeyChecking=no CMakeLists.txt "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/"
scp -o StrictHostKeyChecking=no restart_hailo.sh "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/"
scp -o StrictHostKeyChecking=no src/hailo_bridge_node.cpp "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/src/"
scp -o StrictHostKeyChecking=no src/marcus_semantic_mapper_node.cpp "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/src/"
scp -o StrictHostKeyChecking=no src/marcus_semantic_mapper_node.hpp "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/src/"

Write-Host "📦 Trasferimento nodi e servizi Bio-Cognitivi Python..."
scp -o StrictHostKeyChecking=no robopy_controller/nodes/sensor_standby_manager.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o StrictHostKeyChecking=no robopy_controller/nodes/respeaker_vui_node.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o StrictHostKeyChecking=no robopy_controller/nodes/engagement_monitor.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/services/resource_governor.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/services/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/services/live_connection_manager.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/services/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/services/visual_gesture_pointing_engine.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/services/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/services/nightly_dream_service.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/services/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/cognitive/homeostasis_mal.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/cognitive/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/cognitive/theory_of_mind_engine.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/cognitive/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/cognitive/cognitive_core_node.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/cognitive/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/orchestration/cognitive_graph.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/orchestration/"

Write-Host "📦 Trasferimento Skills Biometriche e Sociali..."
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/skills/base_skill.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/skills/orchestrator.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/skills/builtin/__init__.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/builtin/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/skills/builtin/follow_person_skill.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/builtin/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/skills/builtin/visual_pointing_skill.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/builtin/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/skills/builtin/social_context_skill.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/builtin/"

Write-Host "📦 Trasferimento script di validazione e documentazione FMEA..."
scp -o StrictHostKeyChecking=no scripts/validate_marcus_biocognitive_field.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/scripts/"
scp -o StrictHostKeyChecking=no fmea/dfmea.yaml "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/fmea/"
scp -o StrictHostKeyChecking=no fmea/IMPROVEMENT_INDEX.yaml "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/fmea/"
scp -o StrictHostKeyChecking=no docs/improvements/IMP-BIO-*.md "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/docs/improvements/"

Write-Host "🔄 Aggiornamento install site-packages e build sequenziale C++ (marcus_core_rules.md)..."
ssh -o StrictHostKeyChecking=no "robopy@${target_ip}" @"
chmod +x /mnt/ssd/robopy_controller_host/restart_hailo.sh /mnt/ssd/robopy_controller_host/scripts/validate_marcus_biocognitive_field.py

# Hot-swap Python in install directory
cp -u -r /mnt/ssd/robopy_controller_host/robopy_controller/nodes/* /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/nodes/ 2>/dev/null
cp -u -r /mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/* /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/robot_ai/ 2>/dev/null

# Sequential C++ compilation with Pi 5 Cortex-A76 flags
cd /mnt/ssd/robopy_controller_host
source /home/robopy/ros2_jazzy/install/setup.bash
MAKEFLAGS="-j1" colcon build --packages-select robopy_controller --parallel-workers 1 --cmake-args -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-O3 -mcpu=cortex-a76+crypto" --executor sequential
"@

Write-Host "🚀 Riavvio stack su Marcus (modalità AMCL piano terra, Hailo abilitato)..."
ssh -o StrictHostKeyChecking=no "robopy@${target_ip}" "cd /mnt/ssd/robopy_controller_host && ./restart_hailo.sh --enable-hailo --amcl --map=/mnt/ssd/maps/piano_terra_opt.yaml"

Write-Host "✅ DEPLOY_AND_RESTART_COMPLETED"
