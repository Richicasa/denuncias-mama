#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Configurando entorno por primera vez..."
    bash instalar_todo.sh
fi

source venv/bin/activate
export PYTHONIOENCODING="utf-8"

echo "===================================================================="
echo "          🤖 BOT DE TELEGRAM DE DENUNCIAS EN VIVO (LINUX)"
echo "===================================================================="
echo "El bot está activo y escuchando mensajes en Telegram."
echo "Tu mamá puede enviar mensajes a @denuncias_mama_bot"
echo "Presiona Ctrl+C para detener."
echo "===================================================================="

python3 telegram_bot.py
