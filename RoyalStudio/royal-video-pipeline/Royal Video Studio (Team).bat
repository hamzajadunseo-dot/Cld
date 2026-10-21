@echo off
title Royal Video Studio - TEAM MODE
cd /d "%~dp0"
rem portable copy: AI models travel with the package
if exist "%~dp0..\models_cache" set HF_HOME=%~dp0..\models_cache
rem Team mode: other computers (e.g. over Tailscale) can connect and must log in.
set STUDIO_TEAM=1
".venv\Scripts\python.exe" app.py
pause
