#!/bin/bash
# reprocess_and_generate_map.sh — Ottimizzazione globale offline del grafo SLAM ed esportazione mappa 2D Occupancy Grid
# Esegue rtabmap-reprocess con ICP 2D ad alta convergenza (-g2 SENZA -db per scrittura diretta del file .pgm)
set -e

INPUT_DB="${1:-/mnt/ssd/rtabmap_pianoterra2_raw.db}"
OUTPUT_DB="${2:-/mnt/ssd/maps/pianoterra2_final.db}"
OUTPUT_MAP_BASE="${OUTPUT_DB%.db}"

echo "=========================================================="
echo "🗺️ RTAB-MAP OFFLINE REPROCESSING & MAP GENERATION"
echo "Input DB:  $INPUT_DB"
echo "Output DB: $OUTPUT_DB"
echo "=========================================================="

if [ ! -f "$INPUT_DB" ]; then
    echo "❌ Errore: file $INPUT_DB non trovato!"
    exit 1
fi

mkdir -p /mnt/ssd/maps

# Esegue rtabmap-reprocess con opzioni ottimizzate per RPLIDAR C1 360° e chiusura loop geometrica
# NOTA: -g2 senza -db scrive direttamente il file /mnt/ssd/maps/pianoterra2_final_map.pgm
/usr/local/bin/rtabmap-reprocess \
    -g2 \
    --Reg/Strategy 1 \
    --Icp/Strategy 0 \
    --Icp/MaxCorrespondenceDistance 0.45 \
    --Icp/CorrespondenceRatio 0.15 \
    --Icp/OutlierRatio 0.70 \
    --Icp/VoxelSize 0.02 \
    --Icp/Iterations 40 \
    --Icp/Epsilon 0.0001 \
    --RGBD/ProximityBySpace true \
    --RGBD/LocalRadius 6.0 \
    --RGBD/ProximityAngle 360 \
    --RGBD/ProximityPathMaxNeighbors 15 \
    --RGBD/ProximityMaxGraphDepth 0 \
    --RGBD/NeighborLinkRefining true \
    --RGBD/OptimizeMaxError 0 \
    --Optimizer/Strategy 1 \
    --Optimizer/Slam2D true \
    --Optimizer/Iterations 50 \
    --Optimizer/Robust true \
    --Grid/Sensor 0 \
    --Grid/FromDepth false \
    --Grid/RangeMin 0.10 \
    --Grid/RangeMax 12.0 \
    --Grid/CellSize 0.05 \
    --Grid/RayTracing true \
    --Grid/3D false \
    --Grid/MinClusterSize 5 \
    "$INPUT_DB" \
    "$OUTPUT_DB"

echo "✅ Reprocessing e generazione mappa completati con successo!"
ls -lh "${OUTPUT_MAP_BASE}"* || true
