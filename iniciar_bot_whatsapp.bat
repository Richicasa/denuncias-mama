@echo off
title Bot de WhatsApp - Denuncias Judiciales
chcp 65001 >nul
cls
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv run python whatsapp_bot.py
pause
