#!/usr/bin/env bash
# ============================================================
#  flash_respeaker.sh — Flasha il firmware ESPHome sul
#  ReSpeaker Lite (XIAO ESP32S3) connesso via USB.
#
#  Uso:
#    ./scripts/flash_respeaker.sh [porta]
#
#  Default porta: /dev/ttyACM0
#  Per monitorare i log dopo il flash: aggiungi --monitor
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
YAML_FILE="$REPO_ROOT/robopy_controller/files_utili/respeaker.yaml"
SECRETS_FILE="$REPO_ROOT/robopy_controller/files_utili/secrets.yaml"
VENV_DIR="$HOME/esphome_venv"
PORT="${1:-/dev/ttyACM0}"
MONITOR="${2:-}"

# ── Colori ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[flash_respeaker]${RESET} $*"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
err()  { echo -e "${RED}[✗]${RESET} $*" >&2; exit 1; }

# ── Checks ──────────────────────────────────────────────────
log "=== ReSpeaker Lite — Flash Tool ==="
log "YAML   : $YAML_FILE"
log "Port   : $PORT"
log "Venv   : $VENV_DIR"

[[ -f "$YAML_FILE" ]] || err "File YAML non trovato: $YAML_FILE"
[[ -e "$PORT"      ]] || err "Porta non trovata: $PORT (controlla lsusb e ls /dev/ttyACM*)"

# ── Verifica che la porta sia l'ESP32S3 ─────────────────────
if ! lsusb | grep -qi "Espressif"; then
    warn "Nessun dispositivo Espressif trovato in lsusb. Continuo comunque..."
else
    ok "Dispositivo Espressif rilevato."
fi

# ── Permessi seriale ─────────────────────────────────────────
if ! groups "$USER" | grep -q dialout; then
    warn "L'utente $(whoami) non è nel gruppo 'dialout'. Aggiungo..."
    sudo usermod -aG dialout "$USER"
    warn "Potrebbe essere necessario un logout/login per applicare il gruppo."
fi

# ── Crea / aggiorna venv ESPHome ────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    log "Creazione venv ESPHome in $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
fi

log "Attivazione venv e installazione/aggiornamento ESPHome..."
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

pip install --upgrade pip --quiet
pip install esphome --quiet
ok "ESPHome $(esphome version) installato."

# ── Crea secrets.yaml se non esiste ─────────────────────────
if [[ ! -f "$SECRETS_FILE" ]]; then
    log "Creo $SECRETS_FILE ..."
    cat > "$SECRETS_FILE" << 'SECRETS_EOF'
# ESPHome secrets — non committare questo file in git!
wifi_ssid: "Vodafone-suffia"
wifi_password: "CJGHEhRbXGdRNreX"
SECRETS_EOF
    ok "secrets.yaml creato."
else
    ok "secrets.yaml esiste già."
fi

# Aggiorna il YAML per usare !secret correttamente se necessario
# (il YAML dell'utente ha già !secret, quindi è corretto)

# ── Compila il firmware ──────────────────────────────────────
log "Compilazione firmware ESPHome (potrebbe richiedere 5-10 minuti alla prima esecuzione)..."
cd "$(dirname "$YAML_FILE")"
esphome compile respeaker.yaml
ok "Compilazione completata."

# ── Flash sul dispositivo ────────────────────────────────────
log "Flash del firmware su $PORT ..."
log "(Assicurati che il dispositivo NON sia in uso da altri processi)"

# Se il dispositivo è in modalità normale, prova upload
esphome upload --device "$PORT" respeaker.yaml
ok "Flash completato con successo!"

# ── Istruzioni post-flash ────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════${RESET}"
echo -e "${GREEN}  ReSpeaker Lite flashato con successo! ${RESET}"
echo -e "${GREEN}════════════════════════════════════════${RESET}"
echo ""
echo "Per monitorare i log seriali:"
echo "  source $VENV_DIR/bin/activate && esphome logs --device $PORT $(dirname "$YAML_FILE")/respeaker.yaml"
echo ""
echo "Oppure con python3:"
echo "  python3 -m serial.tools.miniterm $PORT 115200"
echo ""

# ── Monitor opzionale ────────────────────────────────────────
if [[ "${MONITOR:-}" == "--monitor" ]]; then
    log "Avvio monitor seriale (Ctrl+C per uscire)..."
    esphome logs --device "$PORT" respeaker.yaml
fi
