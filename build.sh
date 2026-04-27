#!/bin/bash
# Script di build locale per Marcus (Windows/PowerShell)
# [v1.2] Orchestratore FAILPROOF: Sync SCP + Remote Build

echo "📦 1. Sincronizzazione file (Metodo Robust SCP)..."
bash scripts/sync_scp.sh

if [ $? -ne 0 ]; then
    echo "❌ Errore durante la sincronizzazione. Build annullata."
    exit 1
fi

echo "🚀 2. Avvio build remota su Marcus..."
# -t forza l'allocazione di un pseudo-terminale per migliorare il ritorno dei log
ssh -t robopy@marcus "bash /mnt/ssd/robopy_controller_host/scripts/remote_build.sh"

if [ $? -eq 0 ]; then
    echo "✨ Processo di build completato con successo!"
else
    echo "❌ Fallimento della build remota."
    exit 1
fi
