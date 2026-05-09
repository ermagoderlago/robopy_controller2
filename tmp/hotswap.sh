#!/bin/bash
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
BASE="/mnt/ssd/robopy_controller_host"
SITE="${BASE}/install/robopy_controller/lib/python${PYVER}/site-packages/robopy_controller"

echo "=== HOTSWAP COMPLETO ==="
echo "Python: ${PYVER} | Site: ${SITE}"

cp ${BASE}/robopy_controller/robot_ai/services/llm_service.py \
   ${SITE}/robot_ai/services/llm_service.py && echo "✅ llm_service.py"

cp ${BASE}/robopy_controller/robot_ai/orchestration/conversation.py \
   ${SITE}/robot_ai/orchestration/conversation.py && echo "✅ conversation.py"

cp ${BASE}/robopy_controller/robot_ai/orchestration/orchestrator.py \
   ${SITE}/robot_ai/orchestration/orchestrator.py && echo "✅ orchestrator.py"

echo "🔄 Hotswap completato. Riavvia il robot."
