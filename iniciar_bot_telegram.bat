@echo off
title Bot de Telegram - Denuncias Judiciales
chcp 65001 >nul
cls
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv run python telegram_bot.py
pause
