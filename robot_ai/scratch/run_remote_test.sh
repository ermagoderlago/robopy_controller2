#!/bin/bash
# Carica le chiavi e l'ambiente
source /mnt/ssd/robopy_controller_host/setup_keys.sh
source /home/robopy/ros2_venv/bin/activate
export PYTHONPATH=$PYTHONPATH:/mnt/ssd/robopy_controller_host

# Carica le variabili dal file .env se presente
if [ -f /mnt/ssd/robopy_controller_host/.env ]; then
    export $(grep -v '^#' /mnt/ssd/robopy_controller_host/.env | xargs)
fi

# Esegue il test
python3 /mnt/ssd/robopy_controller_host/robot_ai/scratch/remote_test_email.py
