#!/bin/bash
# =============================================================================
# WATCHDOG DI SOPRAVVIVENZA COGNITIVA - MARCUS AI
# =============================================================================
# Questo script monitora il nodo robot_ai_node. Se crasha 3 volte in 60 secondi,
# esegue un rollback di emergenza A/B ripristinando la versione precedente stabile.
# =============================================================================

CRASH_LIMIT=3
WINDOW_SEC=60
CRASH_TIMES=()

LOG_FILE="/home/robopy/logs/watchdog.log"
mkdir -p /home/robopy/logs

echo "$(date '+%Y-%m-%d %H:%M:%S') - Watchdog avviato. Monitoraggio robot_ai_node..." >> "$LOG_FILE"

while true; do
    # Verifica se robot_ai_node è attivo
    if ! pgrep -f "robot_ai_node" > /dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ATTENZIONE: robot_ai_node non rilevato attivo!" >> "$LOG_FILE"
        CURRENT_TIME=$(date +%s)
        CRASH_TIMES+=("$CURRENT_TIME")
        
        # Filtra i crash al di fuori della finestra temporale di 60s
        MIN_TIME=$((CURRENT_TIME - WINDOW_SEC))
        FILTERED_TIMES=()
        for t in "${CRASH_TIMES[@]}"; do
            if [ "$t" -ge "$MIN_TIME" ]; then
                FILTERED_TIMES+=("$t")
            fi
        done
        CRASH_TIMES=("${FILTERED_TIMES[@]}")
        
        CRASH_COUNT=${#CRASH_TIMES[@]}
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Rilevato crash. Crash negli ultimi ${WINDOW_SEC}s: ${CRASH_COUNT}/${CRASH_LIMIT}" >> "$LOG_FILE"
        
        if [ "$CRASH_COUNT" -ge "$CRASH_LIMIT" ]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - CRITICO: Limite di crash raggiunto (${CRASH_COUNT} in ${WINDOW_SEC}s)!" >> "$LOG_FILE"
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Avvio procedura di ROLLBACK DI EMERGENZA A/B..." >> "$LOG_FILE"
            
            # 1. Kill forzato di nodi zombie ed hardware lock
            pkill -9 -f robot_ai_node || true
            pkill -9 -f respeaker_vui_node || true
            
            # 2. Ripristino del symlink di produzione
            if [ -L "/home/robopy/robopy/install" ] || [ -d "/home/robopy/robopy/install" ]; then
                echo "$(date '+%Y-%m-%d %H:%M:%S') - Eseguo swap symlink install -> install_v15" >> "$LOG_FILE"
                ln -sfn /home/robopy/robopy/install_v15 /home/robopy/robopy/install
            fi
            
            # 3. Riproduzione TTS di avaria in background
            python3 -c "
import pyttsx3
engine = pyttsx3.init()
engine.say('Avaria sistema cognitivo, ripristino versione precedente stabile.')
engine.runAndWait()
" 2>/dev/null || echo "Avaria sistema cognitivo, ripristino versione precedente"
            
            # 4. Riavvio stack completo
            FROM_WATCHDOG=1 bash /mnt/ssd/robopy_controller_host/restart_hailo.sh >> "$LOG_FILE" 2>&1
            
            # Reset cronologia per evitare loop infiniti di rollback
            CRASH_TIMES=()
        else
            # Riavvio semplice
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Eseguo riavvio semplice di Marcus..." >> "$LOG_FILE"
            FROM_WATCHDOG=1 bash /mnt/ssd/robopy_controller_host/restart_hailo.sh >> "$LOG_FILE" 2>&1
        fi
    fi
    sleep 5
done
