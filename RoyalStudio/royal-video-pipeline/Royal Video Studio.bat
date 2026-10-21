@echo off
title Royal Video Studio
cd /d "%~dp0"
rem portable copy: AI models travel with the package
if exist "%~dp0..\models_cache" set HF_HOME=%~dp0..\models_cache
".venv\Scripts\python.exe" app.py
pause
