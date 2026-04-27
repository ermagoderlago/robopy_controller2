#!/bin/bash

# =================================================================
# Marcus AI - GitHub Deploy Pipeline (Windows/Linux/Bash Compatible)
# =================================================================

echo "ðŸš€ Avvio della pipeline di deploy Marcus AI..."

# Funzione per trovare l'eseguibile Python corretto (compatibilitÃ  Windows/Linux)
get_python() {
    if command -v python &>/dev/null; then
        echo "python"
    elif command -v python3 &>/dev/null; then
        echo "python3"
    elif command -v py &>/dev/null; then
        echo "py"
    else
        return 1
    fi
}

PYTHON_CMD=$(get_python)

if [ $? -ne 0 ]; then
    echo "âŒ ERRORE: Python non trovato nel sistema. Assicurati che sia installato e nel PATH."
    exit 1
fi

echo "ðŸ” Esecuzione smoke test con $PYTHON_CMD..."
$PYTHON_CMD -m compileall -q -x "\.git" .
if [ $? -ne 0 ]; then
    echo "âŒ ERRORE: Sono stati trovati errori di sintassi nel codice."
    echo "Per favore, correggi gli errori segnalati sopra prima di procedere."
    exit 1
fi
echo "âœ… Smoke test superato."

# 2. Controllo Git
if [ ! -d ".git" ]; then
    echo "âš ï¸ Git non inizializzato. Inizializzazione in corso..."
    git init
    git branch -M main
fi

# 3. Staging
echo "ðŸ“¦ Staging dei file (rispettando .gitignore)..."
git add .

# 4. Commit (Conventional Commits)
DESC=${1:-"automated sync and syntax validation"}
COMMIT_MSG="feat: $DESC"

echo "ðŸ’¾ Esecuzione commit: \"$COMMIT_MSG\""
git commit -m "$COMMIT_MSG"

# 5. Push
if git remote | grep -q "origin"; then
    echo "ðŸ“¤ Push su origin/main..."
    git push origin main
else
    echo "â„¹ï¸  Nessun remote 'origin' trovato."
    echo "Per collegare la repo, usa: git remote add origin <URL_REPO>"
    echo "Poi esegui: git push -u origin main"
fi

echo "âœ¨ Operazione completata."
