#!/bin/bash
# compile_waveshare_wsl.sh - Compiles Waveshare ESP32 firmware in a clean, space-free directory in WSL.
set -e
export PYTHONPATH="/home/robopy/esphome_venv/lib/python3.12/site-packages"
export IDF_MAINTAINER=1

echo "=================================================="
echo " 🛠️ WSL ESPHome Waveshare Firmware Compiler"
echo "=================================================="

BUILD_DIR="/home/robopy/waveshare_build"
SRC_DIR="/mnt/c/Users/lsuffia/OneDrive - BRUGOLA OEB INDUSTRIALE SPA/Documents/robopy/antigravity"

echo "📂 Creating clean build directory: ${BUILD_DIR}..."
mkdir -p "${BUILD_DIR}"

echo "📥 Copying source files..."
cp "${SRC_DIR}/robopy_controller/files_utili/waveshare_driver.yaml" "${BUILD_DIR}/waveshare_driver.yaml"
cp "${SRC_DIR}/robopy_controller/files_utili/waveshare_bridge.h" "${BUILD_DIR}/waveshare_bridge.h"

echo "⚙️ Compiling firmware via ESPHome..."
cd "${BUILD_DIR}"
/home/robopy/esphome_venv/bin/esphome compile waveshare_driver.yaml

echo "📦 Extracting factory binary..."
mkdir -p "${BUILD_DIR}/output"
cp .esphome/build/waveshare-motor-driver/.pioenvs/waveshare-motor-driver/firmware.factory.bin "${BUILD_DIR}/output/waveshare_driver.factory.bin"

echo "📤 Copying compiled firmware to Raspberry Pi..."
scp "${BUILD_DIR}/output/waveshare_driver.factory.bin" robopy@marcus:/tmp/waveshare_driver.factory.bin

echo "=================================================="
echo " 🎉 COMPILATION AND TRANSFER SUCCESSFUL!"
echo "   Binary is now on Pi: /tmp/waveshare_driver.factory.bin"
echo "=================================================="
