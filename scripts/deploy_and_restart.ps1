# Deploy automatico e riavvio per Marcus
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

Write-Host "🟢 Marcus ONLINE! Avvio trasferimento file aggiornati..."
scp -o StrictHostKeyChecking=no restart_hailo.sh "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/"
scp -o StrictHostKeyChecking=no robopy_controller/config/battery_params.yaml "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/config/"
scp -o StrictHostKeyChecking=no robopy_controller/nodes/battery_manager_node.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o StrictHostKeyChecking=no robopy_controller/nodes/nomad_reactive_pipeline_node.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o StrictHostKeyChecking=no robopy_controller/nodes/semantic_costmap_injector.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o StrictHostKeyChecking=no robopy_controller/nodes/respeaker_vui_node.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o StrictHostKeyChecking=no robopy_controller/robot_ai/services/llm_service.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/services/"
scp -o StrictHostKeyChecking=no scripts/marcus_voice_nav.py "robopy@${target_ip}:/mnt/ssd/robopy_controller_host/scripts/"

Write-Host "🔄 Aggiornamento install site-packages e dist-packages per esecuzione immediata..."
ssh -o StrictHostKeyChecking=no "robopy@${target_ip}" "cp -u /mnt/ssd/robopy_controller_host/robopy_controller/nodes/battery_manager_node.py /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/nodes/ 2>/dev/null; cp -u /mnt/ssd/robopy_controller_host/robopy_controller/nodes/nomad_reactive_pipeline_node.py /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/nodes/ 2>/dev/null; cp -u /mnt/ssd/robopy_controller_host/robopy_controller/nodes/semantic_costmap_injector.py /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/nodes/ 2>/dev/null; cp -u /mnt/ssd/robopy_controller_host/robopy_controller/nodes/respeaker_vui_node.py /mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/nodes/ 2>/dev/null; cp -u /mnt/ssd/robopy_controller_host/robopy_controller/config/battery_params.yaml /mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/ 2>/dev/null; chmod +x /mnt/ssd/robopy_controller_host/restart_hailo.sh /mnt/ssd/robopy_controller_host/scripts/marcus_voice_nav.py"

Write-Host "🚀 Riavvio stack su Marcus (modalità AMCL piano terra, Hailo abilitato)..."
ssh -o StrictHostKeyChecking=no "robopy@${target_ip}" "cd /mnt/ssd/robopy_controller_host && ./restart_hailo.sh --enable-hailo --amcl --map=/mnt/ssd/maps/piano_terra_opt.yaml"

Write-Host "✅ DEPLOY_AND_RESTART_COMPLETED"
