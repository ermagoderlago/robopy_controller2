#!/bin/bash
# =============================================================================
# SYNC FROM MARCUS - Script di back-sync: dal Robot al PC locale
# Versione: 2.0 - SSH ControlMaster (una sola autenticazione, senza multiplexing broken)
# =============================================================================

TARGET_HOST="marcus"
TARGET_DIR="/mnt/ssd/robopy_controller_host"

CTRL_SOCK="/tmp/ssh_ctrl_marcus_backsync"
SSH_OPTS="-o ControlMaster=auto -o ControlPath=${CTRL_SOCK} -o ControlPersist=60 -o ConnectTimeout=10"
SSH_CMD="ssh ${SSH_OPTS}"
RSYNC_SSH="ssh ${SSH_OPTS}"

echo "=============================================="
echo " 📥 BACK-SYNC: Robot -> PC"
echo "=============================================="

# Apertura connessione master
${SSH_CMD} -n ${TARGET_HOST} "echo '✅ Connessione SSH stabilita'" || {
    echo "❌ ERRORE: Impossibile connettersi a ${TARGET_HOST}"
    exit 1
}

# 1. Skills generate (active, staging, failed)
echo ""
echo "🔧 [1/5] Recupero skill (active, staging, failed)..."

rsync -avz --progress -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/robot_ai/skills/active/ \
    ./robopy_controller/robot_ai/skills/active/ 2>/dev/null || echo "  ⚠️  active/ skip"

rsync -avz --progress -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/robot_ai/skills/staging/ \
    ./robopy_controller/robot_ai/skills/staging/ 2>/dev/null || echo "  ⚠️  staging/ skip"

rsync -avz --progress -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/robot_ai/skills/failed/ \
    ./robopy_controller/robot_ai/skills/failed/ 2>/dev/null || echo "  ⚠️  failed/ skip"

# 2. Manifest skill
echo ""
echo "📋 [2/5] Recupero skills_manifest.json..."
rsync -avz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/robot_ai/skills/skills_manifest.json \
    ./robopy_controller/robot_ai/skills/skills_manifest.json 2>/dev/null || echo "  ⚠️  manifest non trovato, skip"

# 3. Logs di runtime
echo ""
echo "📝 [3/5] Recupero logs..."
rsync -avz --progress -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/robopy_controller/logs/ \
    ./robopy_controller/logs/ 2>/dev/null || echo "  ⚠️  logs/ skip"

# 4. WORKSPACE_STATE e files_topic (aggiornati automaticamente sul robot)
echo ""
echo "🗺️  [4/5] Recupero WORKSPACE_STATE e TOPIC_MAP..."
rsync -avz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/WORKSPACE_STATE.md \
    ./WORKSPACE_STATE.md 2>/dev/null || echo "  ⚠️  WORKSPACE_STATE.md non trovato"

rsync -avz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/files_topic.md \
    ./files_topic.md 2>/dev/null || echo "  ⚠️  files_topic.md non trovato"

# 5. Config aggiornate (lesson_learned, pesi RAG, ecc.)
echo ""
echo "🧠 [5/6] Recupero weights aggiornati..."
rsync -avz --progress -e "${RSYNC_SSH}" \
    --exclude='*.db' --exclude='*.bin' --exclude='ChromaDB/' \
    ${TARGET_HOST}:${TARGET_DIR}/weights/ \
    ./weights/ 2>/dev/null || echo "  ⚠️  weights/ skip"

# 6. File identità aggiornati dal Nightly Dream
echo ""
echo "🧬 [6/6] Recupero file identità Marcus (aggiornati dal Nightly Dream)..."
# MEMORY.md — aggiornato automaticamente da NightlyDreamService
rsync -avz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/MEMORY.md \
    ./MEMORY.md 2>/dev/null || echo "  ⚠️  MEMORY.md non trovato, skip"

# USER.md — può essere aggiornato dal Nightly Dream con osservazioni su Luca
rsync -avz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/USER.md \
    ./USER.md 2>/dev/null || echo "  ⚠️  USER.md non trovato, skip"

# SOUL.md — può evolversi tramite skill o dream (raramente)
rsync -avz -e "${RSYNC_SSH}" \
    ${TARGET_HOST}:${TARGET_DIR}/SOUL.md \
    ./SOUL.md 2>/dev/null || echo "  ⚠️  SOUL.md non trovato, skip"

# NOTA: AGENTS.md NON viene recuperato dal robot — è gestito solo dal dev (Antigravity/Luca)

# Chiudi tunnel
${SSH_CMD} -O exit ${TARGET_HOST} 2>/dev/null || true

echo ""
echo "=============================================="
echo " ✅ BACK-SYNC COMPLETATO!"
echo "   Il tuo workspace locale è aggiornato con le ultime"
echo "   modifiche del robot."
echo "   File identità recuperati: MEMORY.md, USER.md, SOUL.md"
echo "   Ora puoi fare forward-sync sicuro con: bash sync_marcus.sh"
echo "=============================================="
