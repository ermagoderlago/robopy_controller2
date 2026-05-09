#!/bin/bash
# fix_raspotify.sh
# Configura raspotify per usare PulseAudio tramite il plugin ALSA-to-Pulse
# in modo che il device non sia in conflitto con VUI node (che usa pure Pulse)

set -e

CONF="/etc/raspotify/conf"

echo "=== Backup configurazione originale ==="
sudo cp "$CONF" "${CONF}.backup_$(date +%Y%m%d_%H%M%S)"

echo "=== Applicazione fix: backend pulse e device plug:pulse ==="

# Imposta backend su alsa (default) ma con device plug:pulse
# plug:pulse è il plugin ALSA che instrada su PulseAudio — così Pulse fa il mixing
sudo sed -i 's|^#LIBRESPOT_BACKEND=.*|LIBRESPOT_BACKEND=alsa|' "$CONF"
sudo sed -i 's|^#LIBRESPOT_DEVICE=.*|LIBRESPOT_DEVICE=plug:pulse|' "$CONF"

# Verifica che le righe siano state applicate
echo ""
echo "=== Configurazione risultante ==="
sudo grep -E "LIBRESPOT_BACKEND|LIBRESPOT_DEVICE|LIBRESPOT_MIXER" "$CONF" | grep -v '^#'

echo ""
echo "=== Riavvio raspotify ==="
sudo systemctl restart raspotify
sleep 5
systemctl is-active raspotify && echo "✅ raspotify ATTIVO" || echo "❌ raspotify FALLITO"

echo ""
echo "=== Ultimo log raspotify ==="
journalctl -u raspotify -n 10 --no-pager 2>/dev/null
