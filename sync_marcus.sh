#!/bin/bash
# =============================================================================
# SYNC MARCUS - Script di sincronizzazione bidirezionale (PC <-> Raspberry Pi)
# Versione: 3.0 - SSH ControlMaster (una sola autenticazione)
# =============================================================================
# PROBLEMA RISOLTO: Versioni precedenti aprivano 4-5 connessioni SSH separate,
# ognuna richiedeva la password. Ora si usa SSH ControlMaster per multiplexare
# tutte le connessioni su un unico tunnel autenticato.
# =============================================================================

set -e  # Esci subito in caso di errore

TARGET_HOST="robopy@marcus"
TARGET_DIR="/mnt/ssd/robopy_controller_host"

# --- Configurazione SSH ---
# In WSL o Linux puro, usiamo il native ssh di Linux che supporta ControlMaster.
# Usiamo ssh.exe solo se siamo in Windows Cmd/PowerShell (non-WSL).
if [ -n "$COMSPEC" ] && ! grep -qE "(Microsoft|WSL)" /proc/version 2>/dev/null; then
    echo "🪟 Ambiente Windows Cmd/PowerShell rilevato: uso ssh.exe (senza ControlMaster)"
    SSH_CMD="ssh.exe"
    RSYNC_SSH="ssh.exe"
else
    CTRL_SOCK="/tmp/ssh_ctrl_marcus_sync"
    SSH_OPTS="-o BatchMode=yes -o ControlMaster=auto -o ControlPath=${CTRL_SOCK} -o ControlPersist=60 -o ConnectTimeout=10"
    SSH_CMD="ssh ${SSH_OPTS}"
    RSYNC_SSH="ssh ${SSH_OPTS}"
fi

echo "=============================================="
echo " 🤖 SYNC MARCUS - Avvio sincronizzazione"
echo "=============================================="

# --- Pre-connessione: apre il master SSH (unica autenticazione) ---
echo "🔑 Apertura connessione SSH master (potrebbe richiedere la password)..."
${SSH_CMD} -n ${TARGET_HOST} "echo '✅ Connessione SSH stabilita con successo'" || {
    echo "❌ ERRORE: Impossibile connettersi a ${TARGET_HOST}"
    exit 1
}

# --- STEP 1: BACK-SYNC (ROBOT -> PC) ---
echo ""
echo "📥 [1/4] Back-Sync: Recupero aggiornamenti dal Robot..."

# Recupera skill generate/aggiornate sul robot
rsync -auvz --progress -e "${RSYNC_SSH}" \
    --exclude='__pycache__/' --exclude='*.pyc' \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/robot_ai/skills/active/ \
    ./robopy_controller/robot_ai/skills/active/ 2>/dev/null || echo "  ⚠️  Cartella active/ non trovata sul robot, skip."

rsync -auvz --progress -e "${RSYNC_SSH}" \
    --exclude='__pycache__/' --exclude='*.pyc' \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/robot_ai/skills/staging/ \
    ./robopy_controller/robot_ai/skills/staging/ 2>/dev/null || echo "  ⚠️  Cartella staging/ non trovata sul robot, skip."

rsync -auvz --progress -e "${RSYNC_SSH}" \
    --exclude='__pycache__/' --exclude='*.pyc' \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/robot_ai/skills/script/ \
    ./robopy_controller/robot_ai/skills/script/ 2>/dev/null || echo "  ⚠️  Cartella script/ non trovata sul robot, skip."

# Recupera logs aggiornati
rsync -auvz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/logs/ \
    ./robopy_controller/logs/ 2>/dev/null || echo "  ⚠️  Cartella logs/ non trovata sul robot, skip."

# Recupera WORKSPACE_STATE, files_topic e .env aggiornati dal robot (es. modelli Gemini auto-scoperti)
rsync -auvz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/WORKSPACE_STATE.md \
    ./WORKSPACE_STATE.md 2>/dev/null || echo "  ⚠️  WORKSPACE_STATE.md non trovato, skip."

rsync -auvz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/files_topic.md \
    ./files_topic.md 2>/dev/null || echo "  ⚠️  files_topic.md non trovato, skip."

rsync -auvz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/.env \
    ./.env 2>/dev/null || echo "  ⚠️  .env non trovato sul robot, skip."

# --- STEP 2: FORWARD-SYNC (PC -> ROBOT) - Workspace completo ---
echo ""
echo "📤 [2/4] Forward-Sync: Aggiornamento workspace completo sul Robot..."

# Sync esplicito del .env (contiene LIVE_MODEL_NAME e tutte le API key aggiornate)
rsync -avz -e "${RSYNC_SSH}" \
    ./.env ${TARGET_HOST}:${TARGET_DIR}/.env
echo "   ✅ .env sincronizzato."

rsync -avz --progress -e "${RSYNC_SSH}" \
    --exclude='.git/' \
    --exclude='.agent/' \
    --exclude='.cache/' \
    --exclude='build/' \
    --exclude='install/' \
    --exclude='log/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.log' \
    --exclude='*.tar.gz' \
    --exclude='marcus_sync*.gz' \
    --exclude='*.har' \
    --exclude='*.hef' \
    ./ ${TARGET_HOST}:${TARGET_DIR}/

# --- STEP 3: HOT-SWAP install/ (aggiornamento Python a caldo senza ricompilare) ---
echo ""
echo "🔄 [3/4] Hot-Swap: Aggiornamento codice Python in install/ (senza rebuild)..."

# Recupera la versione Python del robot in modo sicuro (riusa il tunnel già aperto)
PYTHON_VER=$(${SSH_CMD} ${TARGET_HOST} "python3 -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")'")
SITE_PKGS="install/robopy_controller/lib/python${PYTHON_VER}/site-packages"

echo "   -> Target: ${TARGET_DIR}/${SITE_PKGS}/robopy_controller/"

rsync -avz -e "${RSYNC_SSH}" \
    --include='nodes/' \
    --include='nodes/**' \
    --include='robot_ai/' \
    --include='robot_ai/**' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*' \
    robopy_controller/ ${TARGET_HOST}:${TARGET_DIR}/${SITE_PKGS}/robopy_controller/

rsync -avz -e "${RSYNC_SSH}" \
    --exclude='__pycache__/' --exclude='*.pyc' \
    robopy_controller/nodes/ ${TARGET_HOST}:${TARGET_DIR}/install/robopy_controller/lib/robopy_controller/

rsync -avz -e "${RSYNC_SSH}" \
    --exclude='__pycache__/' --exclude='*.pyc' \
    launch/ ${TARGET_HOST}:${TARGET_DIR}/install/robopy_controller/share/robopy_controller/launch/

# --- STEP 4: PERMESSI REMOTI (eseguiti in una sola chiamata SSH) ---
echo ""
echo "🔑 [4/4] Impostazione permessi remoti..."
${SSH_CMD} ${TARGET_HOST} "
    chmod +x ${TARGET_DIR}/scripts/* ${TARGET_DIR}/*.sh ${TARGET_DIR}/robopy_controller/nodes/*.py 2>/dev/null || true
    # Fix line endings su tutti gli script shell e wrapper in scripts/ (per sicurezza)
    find ${TARGET_DIR} -name '*.sh' -exec sed -i 's/\r$//' {} \; 2>/dev/null || true
    find ${TARGET_DIR}/scripts -type f -exec sed -i 's/\r$//' {} \; 2>/dev/null || true
    echo '✅ Permessi impostati.'
"

# --- Fine: chiudi il master SSH esplicitamente ---
echo ""
echo "🔒 Chiusura connessione SSH master..."
${SSH_CMD} -O exit ${TARGET_HOST} 2>/dev/null || true

echo ""
echo "=============================================="
echo " ✅ SYNC COMPLETATO CON SUCCESSO!"
echo "   PC -> Robot: codice aggiornato"
echo "   Robot -> PC: skills/logs/workspace recuperati"
echo "=============================================="
echo ""
echo "💡 Prossimo step: ssh robopy@marcus e lancia il robot con restart.sh"
