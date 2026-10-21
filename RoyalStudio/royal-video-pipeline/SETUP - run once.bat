@echo off
title Royal Video Studio - one-time setup
cd /d "%~dp0"
echo.
echo  ============================================================
echo    Royal Video Studio - one-time setup  (needs internet, ~3 GB)
echo  ============================================================
echo.

rem ---- 1. Python 3.11
py -3.11 --version >nul 2>&1
if errorlevel 1 (
  echo  [X] Python 3.11 is not installed.
  echo      Open a terminal and run:   winget install -e --id Python.Python.3.11
  echo      then run this setup again.
  pause & exit /b 1
)
echo  [OK] Python 3.11 found

rem ---- 2. FFmpeg (video engine)
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo  [X] FFmpeg is not installed.
  echo      Open a terminal and run:   winget install -e --id Gyan.FFmpeg
  echo      close the terminal, then run this setup again.
  pause & exit /b 1
)
echo  [OK] FFmpeg found

rem ---- 3. Python environment
if not exist ".venv\Scripts\python.exe" (
  echo  ... creating the Python environment
  py -3.11 -m venv .venv || (echo  [X] could not create .venv & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul

rem ---- 4. PyTorch: NVIDIA GPU build if the laptop has one, otherwise CPU build
nvidia-smi >nul 2>&1
if errorlevel 1 (
  echo  ... no NVIDIA GPU found - installing the CPU version ^(works, renders slower^)
  ".venv\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || goto :fail
  ".venv\Scripts\python.exe" -m pip install onnxruntime || goto :fail
  findstr /v /i "onnxruntime-gpu" requirements.txt > "%TEMP%\royal_req.txt"
  ".venv\Scripts\python.exe" -m pip install -r "%TEMP%\royal_req.txt" || goto :fail
) else (
  echo  ... NVIDIA GPU found - installing the GPU version
  ".venv\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126 || goto :fail
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)

rem ---- 5. quick self-test
".venv\Scripts\python.exe" -c "import torch, open_clip, onnxruntime, faster_whisper, flask, cv2, docx; print('  [OK] all packages work - GPU:', torch.cuda.is_available())" || goto :fail
if not exist "..\Royal_Media_Library\People" echo  [!] Royal_Media_Library folder not found next to this folder - copy it from the USB.

rem ---- 6. desktop shortcut
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Royal Video Studio.lnk'); $s.TargetPath='%~dp0Royal Video Studio.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='C:\Windows\System32\imageres.dll,18'; $s.WindowStyle=7; $s.Save()"
echo  [OK] "Royal Video Studio" shortcut is on your desktop
echo.
echo  Setup finished. Double-click "Royal Video Studio" on the desktop to start.
pause
exit /b 0

:fail
echo.
echo  [X] Setup failed - check the internet connection and run it again.
echo      (Send a screenshot of this window if it keeps failing.)
pause
exit /b 1
