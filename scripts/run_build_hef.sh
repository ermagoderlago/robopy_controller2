#!/usr/bin/env bash
set -e
source /home/robopy/hailo_env/bin/activate
export CUDA_VISIBLE_DEVICES=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "${SCRIPT_DIR}/build_hef_pipeline.py"
