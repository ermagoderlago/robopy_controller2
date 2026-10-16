#!/bin/bash
# reprocess_map.sh — Ottimizzazione avanzata del grafo RTAB-Map per eliminazione muri doppi
set -e

INPUT_DB="/mnt/ssd/rtabmap_pianoterra2_raw.db"
OUTPUT_DB="/mnt/ssd/rtabmap_pianoterra2_refined.db"

rm -f "$OUTPUT_DB" "$OUTPUT_DB-wal" "$OUTPUT_DB-shm"

echo "🔧 Avvio rtabmap-reprocess con parametri avanzati..."
echo "   Input: $INPUT_DB"
echo "   Output: $OUTPUT_DB"

/usr/local/bin/rtabmap-reprocess \
    --Icp/MaxCorrespondenceDistance 0.50 \
    --Icp/PointToPlane true \
    --Icp/Strategy 1 \
    --Icp/CorrespondenceRatio 0.15 \
    --RGBD/LocalRadius 8.0 \
    --RGBD/ProximityPathMaxNeighbors 30 \
    --Optimizer/Strategy 1 \
    --Optimizer/Iterations 60 \
    --RGBD/OptimizeMaxError 0.25 \
    --RGBD/LoopClosureRejectionWithGraph true \
    "$INPUT_DB" \
    "$OUTPUT_DB"

echo "✅ Reprocess completato con successo."
ls -lh "$OUTPUT_DB"
