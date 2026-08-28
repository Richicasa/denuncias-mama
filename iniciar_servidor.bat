@echo off
title Servidor de Denuncias Judiciales - Remoto y Local
chcp 65001 >nul
cls
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv run python iniciar_servidor_con_tunel.py
pause
