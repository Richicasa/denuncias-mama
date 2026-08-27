#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(whoami)"

echo "Configurando servicio systemd para que el bot inicie automáticamente al encender Linux..."

SERVICE_FILE="/etc/systemd/system/denuncias-bot.service"

sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=Bot de Telegram Denuncias Judiciales
After=network.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python3 $DIR/telegram_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable denuncias-bot.service
sudo systemctl start denuncias-bot.service

echo ""
echo "===================================================================="
echo "  🎉 ¡SERVICIO 24/7 CONFIGURADO CON ÉXITO!"
echo "===================================================================="
echo "  El bot ahora se iniciará automáticamente cada vez que enciendas"
echo "  la computadora Linux, sin necesidad de abrir terminales."
echo "===================================================================="
