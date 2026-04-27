#!/bin/bash
# Script di sincronizzazione selettivo via SCP/Tar
# [v1.3] Sincronizzazione "Lightweight": Solo sorgenti e config essenziali.

TARGET_HOST="marcus"
TARGET_DIR="/mnt/ssd/robopy_controller_host"
TARBALL_PATH="../marcus_sync_minimal.tar.gz"

echo "📦 Compressione selettiva workspace (Sorgenti + Config)..."
# Escludiamo tutto ciò che è pesante o non necessario per la build
tar --exclude='.git' \
    --exclude='.agent' \
    --exclude='.cache' \
    --exclude='.esphome' \
    --exclude='.depthai_cached_models' \
    --exclude='build' \
    --exclude='install' \
    --exclude='log' \
    --exclude='__pycache__' \
    --exclude='*.log' \
    --exclude='*.tar.gz' \
    -czf "$TARBALL_PATH" .

if [ $? -ne 0 ]; then
    echo "❌ Errore durante la compressione."
    exit 1
fi

echo "🚀 Trasferimento via SCP..."
scp "$TARBALL_PATH" robopy@$TARGET_HOST:/tmp/

if [ $? -ne 0 ]; then
    echo "❌ Errore durante il trasferimento via SCP."
    rm -f "$TARBALL_PATH"
    exit 1
fi

echo "📡 Decompressione remota via SSH..."
ssh robopy@$TARGET_HOST "mkdir -p $TARGET_DIR && tar -xzf /tmp/marcus_sync_minimal.tar.gz -C $TARGET_DIR && rm /tmp/marcus_sync_minimal.tar.gz"

if [ $? -ne 0 ]; then
    echo "❌ Errore durante la decompressione remota."
    rm -f "$TARBALL_PATH"
    exit 1
fi

echo "🔑 Impostazione permessi..."
ssh robopy@$TARGET_HOST "chmod +x $TARGET_DIR/scripts/* $TARGET_DIR/*.sh 2>/dev/null || true"

rm -f "$TARBALL_PATH"
echo "✅ Sincronizzazione Light completata!"
