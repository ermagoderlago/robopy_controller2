#!/bin/bash
set -e
SITE_PKG="/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages"
SRC_DIR="/mnt/ssd/robopy_controller_host/robopy_controller"

ln -sfn "${SRC_DIR}/robot_ai" "${SITE_PKG}/robot_ai"
ln -sfn "${SRC_DIR}/robot_ai" "${SITE_PKG}/robopy_controller/robot_ai"
mkdir -p "${SITE_PKG}/robopy_controller/trinity" "${SITE_PKG}/robopy_controller/navigation"
cp -r "${SRC_DIR}/trinity/"* "${SITE_PKG}/robopy_controller/trinity/"
cp -r "${SRC_DIR}/navigation/"* "${SITE_PKG}/robopy_controller/navigation/"
ln -sfn "${SITE_PKG}/robopy_controller/trinity" "${SITE_PKG}/trinity"
ln -sfn "${SITE_PKG}/robopy_controller/navigation" "${SITE_PKG}/navigation"
echo "HOTSWAP_VERIFIED_OK"
