#!/bin/bash
set -e

echo "===================================================================="
echo "        INSTALADOR AUTOMÁTICO DE ASISTENTE JUDICIAL (LINUX)"
echo "===================================================================="
echo "1. Instalando Tesseract OCR y dependencias del sistema..."

if command -v apt-get &> /dev/null; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv tesseract-ocr tesseract-ocr-spa libtesseract-dev
elif command -v dnf &> /dev/null; then
    sudo dnf install -y python3 python3-pip tesseract tesseract-langpack-spa
elif command -v pacman &> /dev/null; then
    sudo pacman -Sy --noconfirm python python-pip tesseract tesseract-data-spa
fi

echo "2. Creando entorno virtual de Python..."
cd "$(dirname "$0")"
python3 -m venv venv
source venv/bin/activate

echo "3. Instalando librerías de Python..."
pip install --upgrade pip
pip install -r requirements.txt

echo "4. Instalando navegador Chromium y librerías del sistema..."
playwright install chromium
playwright install-deps chromium || true

chmod +x *.sh

echo ""
echo "===================================================================="
echo "  🎉 ¡INSTALACIÓN COMPLETADA CON ÉXITO!"
echo "===================================================================="
echo "  Para iniciar el bot ahora mismo, ejecuta:"
echo "       ./iniciar_bot.sh"
echo "===================================================================="
