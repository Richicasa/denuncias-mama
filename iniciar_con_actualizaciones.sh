#!/bin/bash
# Script para iniciar el bot con actualizaciones automáticas en Linux Mint
echo "===================================================================="
echo "    INICIANDO BOT CON ACTUALIZACIONES AUTOMÁTICAS (LINUX MINT)"
echo "===================================================================="

cd "$(dirname "$0")"

# Si existe un entorno virtual .venv, activarlo
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Ejecutar el supervisor de auto-actualización
python3 auto_runner.py