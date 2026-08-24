@echo off
title Servidor de Denuncias para Mama
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv run python run_server.py
pause
