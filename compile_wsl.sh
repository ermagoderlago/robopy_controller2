#!/bin/bash
# compile_wsl.sh - Compiles ESPHome firmware in a clean, space-free directory in WSL.
set -e
export PYTHONPATH="/home/robopy/esphome_venv/lib/python3.12/site-packages"
export IDF_MAINTAINER=1


echo "=================================================="
echo " 🛠️ WSL ESPHome Firmware Compiler (Space-Free)"
echo "=================================================="

BUILD_DIR="/home/robopy/respeaker_build"
SRC_DIR="/mnt/c/Users/lsuffia/OneDrive - BRUGOLA OEB INDUSTRIALE SPA/Documents/robopy/antigravity"

echo "📂 Creating clean build directory: ${BUILD_DIR}..."
mkdir -p "${BUILD_DIR}"

echo "📥 Copying source files..."
cp "${SRC_DIR}/robopy_controller/files_utili/respeaker_lite_firmware_led_v14.yaml" "${BUILD_DIR}/respeaker.yaml"
cp "${SRC_DIR}/robopy_controller/files_utili/respeaker_helper.h" "${BUILD_DIR}/respeaker_helper.h"
cp "${SRC_DIR}/secrets.yaml" "${BUILD_DIR}/secrets.yaml"

echo "⚙️ Compiling firmware via ESPHome..."
cd "${BUILD_DIR}"
/home/robopy/esphome_venv/bin/esphome compile respeaker.yaml

echo "📦 Extracting factory binary..."
mkdir -p "${BUILD_DIR}/output"
cp .esphome/build/respeaker-lite/.pioenvs/respeaker-lite/firmware.factory.bin "${BUILD_DIR}/output/firmware_led_v14.factory.bin"

echo "📤 Copying compiled firmware to Raspberry Pi..."
scp "${BUILD_DIR}/output/firmware_led_v14.factory.bin" robopy@marcus:/tmp/firmware_led_v14.factory.bin

echo "=================================================="
echo " 🎉 COMPILATION AND TRANSFER SUCCESSFUL!"
echo "   Binary is now on Pi: /tmp/firmware_led_v14.factory.bin"
echo "=================================================="
