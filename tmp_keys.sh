#!/bin/bash
# Script per caricamento chiavi API da file .env
# [v3.0] Carica automaticamente tutte le variabili da .env

ENV_FILE="$(dirname "$BASH_SOURCE")/.env"

if [ -f "$ENV_FILE" ]; then
    echo "📄 Caricamento chiavi da $ENV_FILE..."
    # Esporta tutte le variabili definite nel file .env
    set -a
    source "$ENV_FILE"
    set +a
    echo "✅ Tutte le chiavi API e credenziali caricate correttamente."
else
    # Prova nel workspace path standard se non trovato localmente
    ENV_FILE="/mnt/ssd/robopy_controller_host/.env"
    if [ -f "$ENV_FILE" ]; then
        echo "📄 Caricamento chiavi da $ENV_FILE..."
        set -a
        source "$ENV_FILE"
        set +a
        echo "✅ Tutte le chiavi API e credenziali caricate correttamente."
    else
        echo "⚠️  ERRORE: File .env non trovato!"
        return 1 2>/dev/null || exit 1
    fi
fi
