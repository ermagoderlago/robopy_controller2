#!/bin/bash
# =============================================================================
# WATCHDOG DI SOPRAVVIVENZA COGNITIVA - MARCUS AI
# =============================================================================
# Questo script monitora robot_ai_node e respeaker_vui_node (VUI).
# Se robot_ai_node crasha 3 volte in 60 secondi, esegue rollback A/B.
# Se respeaker_vui_node muore, lo riavvia automaticamente (FIX-05).
# =============================================================================

CRASH_LIMIT=3
WINDOW_SEC=60
CRASH_TIMES=()

LOG_FILE="/home/robopy/logs/watchdog.log"
mkdir -p /home/robopy/logs

echo "$(date '+%Y-%m-%d %H:%M:%S') - Watchdog avviato. Attesa periodo di grazia (45s) per avvio hardware..." >> "$LOG_FILE"
sleep 45

while true; do
    # Se restart_hailo.sh è attualmente in esecuzione, attendi senza contare crash
    if pgrep -f "restart_hailo.sh" > /dev/null; then
        sleep 5
        continue
    fi

    # [FIX-05] Verifica se respeaker_vui_node è attivo (VUI audio pipeline)
    if ! pgrep -f "respeaker_vui_node" > /dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ATTENZIONE: respeaker_vui_node (VUI) non rilevato attivo! Riavvio..." >> "$LOG_FILE"
        # Riavvio leggero del solo nodo VUI senza riavviare lo stack completo
        source /home/robopy/ros2_jazzy/install/setup.bash
        source /mnt/ssd/robopy_controller_host/install/setup.bash 2>/dev/null
        export ROS_DOMAIN_ID=42
        export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
        export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
        export PA_ALSA_PLUGHW=1
        # Recupera gli argomenti VUI dall'ultimo avvio
        VUI_ARGS=""
        if [ -f "/mnt/ssd/last_vui_args.env" ]; then
            VUI_ARGS=$(cat /mnt/ssd/last_vui_args.env)
        fi
        nohup ros2 run robopy_controller respeaker_vui_node $VUI_ARGS >> /home/robopy/logs/vui_watchdog_restart.log 2>&1 &
        echo "$(date '+%Y-%m-%d %H:%M:%S') - respeaker_vui_node riavviato dal watchdog." >> "$LOG_FILE"
        sleep 10  # Attendi stabilizzazione prima del prossimo check
    fi

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
        
        # Recupera gli argomenti dell'ultimo avvio valido
        LAST_ARGS=""
        if [ -f "/mnt/ssd/last_run_args.env" ]; then
            LAST_ARGS=$(cat /mnt/ssd/last_run_args.env)
        fi

        if [ "$CRASH_COUNT" -ge "$CRASH_LIMIT" ]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - CRITICO: Limite di crash raggiunto (${CRASH_COUNT} in ${WINDOW_SEC}s)!" >> "$LOG_FILE"
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Avvio procedura di ripristino con argomenti: '$LAST_ARGS'..." >> "$LOG_FILE"
            
            # 1. Kill forzato di nodi zombie ed hardware lock
            pkill -9 -f robot_ai_node || true
            pkill -9 -f respeaker_vui_node || true
            
            # 2. Riproduzione TTS di avaria in background
            python3 -c "
import pyttsx3
engine = pyttsx3.init()
engine.say('Avaria sistema cognitivo, ripristino versione stabile.')
engine.runAndWait()
" 2>/dev/null || echo "Avaria sistema cognitivo, riavvio in corso"
            
            # 3. Riavvio stack completo preservando la configurazione
            FROM_WATCHDOG=1 bash /mnt/ssd/robopy_controller_host/restart_hailo.sh $LAST_ARGS >> "$LOG_FILE" 2>&1
            
            # Reset cronologia e attesa post-riavvio
            CRASH_TIMES=()
            sleep 60
        else
            # Riavvio semplice preservando la configurazione
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Eseguo riavvio semplice di Marcus con argomenti: '$LAST_ARGS'..." >> "$LOG_FILE"
            FROM_WATCHDOG=1 bash /mnt/ssd/robopy_controller_host/restart_hailo.sh $LAST_ARGS >> "$LOG_FILE" 2>&1
            sleep 60
        fi
    fi
    sleep 5
done
