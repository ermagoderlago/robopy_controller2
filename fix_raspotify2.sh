#!/bin/bash
# add_raspotify_cache.sh — Aggiunge LIBRESPOT_CACHE e LIBRESPOT_NAME alla config raspotify
CONF="/etc/raspotify/conf"

echo "=== Config attiva prima della modifica ==="
grep -v '^#' "$CONF" | grep -v '^$'

echo ""
echo "=== Aggiunta LIBRESPOT_CACHE e LIBRESPOT_NAME ==="

# Aggiungi cache path (per permettere autenticazione senza discovery)
if grep -q "^LIBRESPOT_CACHE=" "$CONF"; then
    echo "LIBRESPOT_CACHE già configurata"
else
    sed -i 's|^#LIBRESPOT_CACHE=.*|LIBRESPOT_CACHE=/home/robopy/.cache/librespot|' "$CONF"
    # Se la riga non c'era commentata, aggiungila in fondo
    if ! grep -q "^LIBRESPOT_CACHE=" "$CONF"; then
        echo "LIBRESPOT_CACHE=/home/robopy/.cache/librespot" >> "$CONF"
    fi
fi

# Aggiungi nome device (appare come "Marcus" in Spotify)
if grep -q "^LIBRESPOT_NAME=" "$CONF"; then
    echo "LIBRESPOT_NAME già configurata"
else
    sed -i 's|^#LIBRESPOT_NAME=.*|LIBRESPOT_NAME=Marcus|' "$CONF"
    if ! grep -q "^LIBRESPOT_NAME=" "$CONF"; then
        echo "LIBRESPOT_NAME=Marcus" >> "$CONF"
    fi
fi

echo ""
echo "=== Config attiva dopo la modifica ==="
grep -v '^#' "$CONF" | grep -v '^$'

echo ""
echo "=== Riavvio raspotify ==="
systemctl restart raspotify
sleep 8
systemctl is-active raspotify && echo "ACTIVE" || echo "FAILED"
echo ""
journalctl -u raspotify --since '-10s' --no-pager 2>/dev/null | grep -Ei 'authen|connect|error|warn|info' | head -10
