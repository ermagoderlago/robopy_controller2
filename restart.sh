#!/bin/bash
# restart.sh — Forwarder a restart_hailo.sh (Produzione Marcus AI)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/restart_hailo.sh" "$@"
