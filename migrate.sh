#!/bin/bash
# Script di migrazione per Severus

SOURCE_DIR="/mnt/c/Users/lsuffia/OneDrive - BRUGOLA OEB INDUSTRIALE SPA/Documents/robopy/antigravity"
DEST_DIR="/home/robopy/severus"

echo "📂 Creazione cartella di destinazione: $DEST_DIR"
mkdir -p "$DEST_DIR"

echo "🚚 Copia dei file in corso..."
# Usa rsync per efficienza, escludendo cartelle pesanti o inutili
rsync -av --exclude 'build' --exclude 'install' --exclude 'log' --exclude '.git' --exclude '__pycache__' "$SOURCE_DIR/" "$DEST_DIR/"

echo "🔧 Impostazione permessi..."
chown -R robopy:robopy "$DEST_DIR"
chmod +x "$DEST_DIR/scripts/"*
chmod +x "$DEST_DIR/fix_env.sh"
chmod +x "$DEST_DIR/restart.sh"

echo "✅ Migrazione completata!"
echo "📍 I file si trovano ora in: $DEST_DIR"
