#!/bin/bash
source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash
# Aggiungiamo il path della cartella robopy_controller_host per trovare il pacchetto robot_ai
export PYTHONPATH=$PYTHONPATH:/mnt/ssd/robopy_controller_host

echo "--- Diagnostica avvio LLM Service ---"
# Proviamo un import diretto per vedere se ci sono errori circolari o mancanti
python3 -c "
try:
    print('1. Importando robot_ai.services.llm_service...')
    from robot_ai.services.llm_service import LLMServiceNode
    print('✅ Import LLMServiceNode OK')
    
    print('2. Verificando dipendenze interne...')
    from robot_ai.services.llm_live_api import LiveAPIMixin
    print('✅ Import LiveAPIMixin OK')
    
    from robot_ai.services.llm_circuit_breaker import CircuitBreaker
    print('✅ Import CircuitBreaker OK')
    
    print('Tutti gli import critici sono a posto.')
except Exception as e:
    print(f'❌ ERRORE RISCONTRATO: {e}')
    import traceback
    traceback.print_exc()
"
