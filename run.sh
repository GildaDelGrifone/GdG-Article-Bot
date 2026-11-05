#!/usr/bin/env bash
set -e

# Vai nella cartella del repository
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Usa il virtualenv del progetto
if [ -d ".venv" ]; then
    PYTHON_EXEC=".venv/bin/python"
elif [ -d "venv" ]; then
    PYTHON_EXEC="venv/bin/python"
else
    echo "Errore: virtualenv (.venv o venv) non trovato."
    echo "Crealo e installa le dipendenze con:"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/pip install -r requirements.txt"
    exit 1
fi

exec "$PYTHON_EXEC" main.py "$@"
