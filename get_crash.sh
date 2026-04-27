#!/bin/bash
LATEST_DIR=$(ls -td /home/robopy/.ros/log/*/ | head -n 1)
echo "Searching in LATEST_DIR: $LATEST_DIR"
for file in "${LATEST_DIR}"*.log; do
    echo "--- Checking $file ---"
    grep -A 30 -B 5 -i -E 'traceback|exception|error' "$file"
done
