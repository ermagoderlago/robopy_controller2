# Deploy automatico e riavvio per Marcus (Bio-Cognitive Evolution)
$target_host = "robopy@marcus"
Write-Host "Verifica connettivita verso Marcus ($target_host)..."

$online = $false
for ($i = 0; $i -lt 5; $i++) {
    $test = echo "PING" | ssh -o BatchMode=yes -o ConnectTimeout=5 $target_host "cat -" 2>$null
    if ($test -eq "PING") {
        $online = $true
        break
    }
    Start-Sleep -Seconds 1
}

if (-not $online) {
    Write-Host "MARCUS_OFFLINE"
    exit 2
}

Write-Host " Marcus ONLINE! Arresto preventivo nodi e watchdog (marcus_core_rules.md)..."
ssh -o BatchMode=yes $target_host "sudo systemctl stop marcus-watchdog 2>/dev/null; pkill -9 -f watchdog.sh 2>/dev/null; pkill -9 -f restart_hailo.sh 2>/dev/null; pkill -9 -f ros2 2>/dev/null; echo 'NODES_STOPPED'"

Write-Host " Trasferimento file di configurazione, launch e avvio..."
scp -o BatchMode=yes restart_hailo.sh "${target_host}:/mnt/ssd/robopy_controller_host/"
scp -o BatchMode=yes launch/custom_nav2_launch.py "${target_host}:/mnt/ssd/robopy_controller_host/launch/"
scp -o BatchMode=yes robopy_controller/config/nav2_params_jazzy.yaml "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/config/"

Write-Host " Trasferimento nodi di navigazione, costmap e supervisione..."
scp -o BatchMode=yes robopy_controller/nodes/semantic_costmap_injector.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o BatchMode=yes robopy_controller/nodes/sensor_standby_manager.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o BatchMode=yes robopy_controller/nodes/resource_governor_node.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o BatchMode=yes robopy_controller/nodes/frontier_explorer_node.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"
scp -o BatchMode=yes robopy_controller/nodes/foxglove_nav2_bridge.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/nodes/"

Write-Host " Trasferimento servizi AI, cognitive, skills e sandbox..."
scp -o BatchMode=yes robopy_controller/robot_ai/services/resource_governor.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/services/"
scp -o BatchMode=yes robopy_controller/robot_ai/skills/builtin/navigation_skill.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/builtin/"
scp -o BatchMode=yes robopy_controller/robot_ai/integrations/navigation.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/integrations/"
scp -o BatchMode=yes robopy_controller/robot_ai/trinity/kinematic_sandbox.py "${target_host}:/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/trinity/"

Write-Host " Trasferimento documentazione FMEA, ECO, lezioni e guide aggiornate..."
scp -o BatchMode=yes fmea/dfmea.yaml "${target_host}:/mnt/ssd/robopy_controller_host/fmea/"
scp -o BatchMode=yes fmea/FMEA_EXECUTIVE_REPORT.md "${target_host}:/mnt/ssd/robopy_controller_host/fmea/"
scp -o BatchMode=yes fmea/IMPROVEMENT_INDEX.yaml "${target_host}:/mnt/ssd/robopy_controller_host/fmea/"
scp -o BatchMode=yes docs/improvements/IMP-NAV-041_motion_deadlock_resolution.md "${target_host}:/mnt/ssd/robopy_controller_host/docs/improvements/"
scp -o BatchMode=yes docs/lessons/nav2_slam_tuning.md "${target_host}:/mnt/ssd/robopy_controller_host/docs/lessons/"
scp -o BatchMode=yes docs/ecos/nav2_slam_ecos.md "${target_host}:/mnt/ssd/robopy_controller_host/docs/ecos/"
scp -o BatchMode=yes marcus_robot_guide.md "${target_host}:/mnt/ssd/robopy_controller_host/"
scp -o BatchMode=yes GAZEBO_WSL_GUIDE.md "${target_host}:/mnt/ssd/robopy_controller_host/"

Write-Host " Hot-Swap Python in install/ (SPEC-07: aggiornamento rapido senza OOM build)..."
$remote_hotswap = @'
TARGET_DIR="/mnt/ssd/robopy_controller_host"
SITE_PKGS="$TARGET_DIR/install/robopy_controller/lib/python3.11/site-packages"
LIB_BIN="$TARGET_DIR/install/robopy_controller/lib/robopy_controller"
SHARE_DIR="$TARGET_DIR/install/robopy_controller/share/robopy_controller"

# Copia i nodi e la libreria Python in site-packages
cp -u -r $TARGET_DIR/robopy_controller/nodes/* "$SITE_PKGS/robopy_controller/nodes/" 2>/dev/null || true
cp -u -r $TARGET_DIR/robopy_controller/robot_ai/* "$SITE_PKGS/robopy_controller/robot_ai/" 2>/dev/null || true

# Copia gli eseguibili in lib/robopy_controller/
cp -u $TARGET_DIR/robopy_controller/nodes/*.py "$LIB_BIN/" 2>/dev/null || true

# Copia launch e config in share
cp -u $TARGET_DIR/launch/* "$SHARE_DIR/launch/" 2>/dev/null || true
cp -u $TARGET_DIR/robopy_controller/config/* "$SHARE_DIR/config/" 2>/dev/null || true

# Assicura symlink canonici (SPEC-07 / FM-SYS-010)
ln -sfn "$TARGET_DIR/robopy_controller/robot_ai" "$SITE_PKGS/robopy_controller/robot_ai"
ln -sfn "$TARGET_DIR/robopy_controller/nodes" "$SITE_PKGS/robopy_controller/nodes"
ln -sfn "$SITE_PKGS/robopy_controller/robot_ai" "$SITE_PKGS/robot_ai"

# Permessi di esecuzione e rimozione caratteri CRLF (\r)
chmod +x $TARGET_DIR/*.sh $TARGET_DIR/scripts/* "$LIB_BIN"/*.py $TARGET_DIR/robopy_controller/nodes/*.py 2>/dev/null || true
cp -f $TARGET_DIR/restart_hailo.sh /home/robopy/restart_hailo.sh 2>/dev/null || true
chmod +x /home/robopy/restart_hailo.sh 2>/dev/null || true

find $TARGET_DIR -maxdepth 1 -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
find $TARGET_DIR/scripts -type f -exec sed -i 's/\r$//' {} + 2>/dev/null || true
sed -i 's/\r$//' /home/robopy/restart_hailo.sh 2>/dev/null || true

echo "HOTSWAP_COMPLETED"
'@

echo "$remote_hotswap" | ssh -o BatchMode=yes $target_host "tr -d '\r' | bash"

Write-Host " Sincronizzazione ed Hot-Swap completati con successo!"

