#!/bin/bash
# Royal Video Studio - one-time setup for macOS (needs internet, ~3 GB)
cd "$(dirname "$0")" || exit 1
echo
echo "  ============================================================"
echo "    Royal Video Studio - one-time setup (Mac)  (~3 GB)"
echo "  ============================================================"
echo

fail() { echo; echo "  [X] $1"; echo; read -r -p "  Press Enter to close..."; exit 1; }

# Homebrew puts its tools here; Finder-launched windows don't always have them on PATH
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# ---- 1. Python 3.11
command -v python3.11 >/dev/null 2>&1 || fail "Python 3.11 is not installed. In Terminal run:   brew install python@3.11"
echo "  [OK] Python 3.11 found"

# ---- 2. FFmpeg (video engine)
command -v ffmpeg >/dev/null 2>&1 || fail "FFmpeg is not installed. In Terminal run:   brew install ffmpeg"
echo "  [OK] FFmpeg found"

# ---- 3. Python environment
if [ ! -x ".venv/bin/python" ]; then
  echo "  ... creating the Python environment"
  python3.11 -m venv .venv || fail "could not create .venv"
fi
PY=".venv/bin/python"
"$PY" -m pip install --upgrade pip >/dev/null

# ---- 4. Packages (Mac build of PyTorch uses the Apple GPU when it can; onnxruntime instead of onnxruntime-gpu)
echo "  ... installing packages (10-20 min)"
"$PY" -m pip install torch torchvision || fail "Setup failed - check the internet connection and run it again."
"$PY" -m pip install onnxruntime || fail "Setup failed - check the internet connection and run it again."
grep -v -i "onnxruntime-gpu" requirements.txt > "${TMPDIR:-/tmp}/royal_req.txt"
"$PY" -m pip install -r "${TMPDIR:-/tmp}/royal_req.txt" || fail "Setup failed - check the internet connection and run it again."
# Intel Macs top out at PyTorch 2.2, which needs NumPy 1.x
if [ "$(uname -m)" = "x86_64" ]; then
  "$PY" -m pip install "numpy<2" || fail "Setup failed - check the internet connection and run it again."
fi

# ---- 5. quick self-test
"$PY" -c "import torch, open_clip, onnxruntime, faster_whisper, flask, cv2, docx; print('  [OK] all packages work - Apple GPU:', torch.backends.mps.is_available())" \
  || fail "Setup failed - send a screenshot of this window."
[ -d "../Royal_Media_Library" ] || echo "  [!] Royal_Media_Library folder not found next to this folder - create it."

chmod +x ./*.command 2>/dev/null
echo
echo "  Setup finished. Double-click \"Royal Video Studio (Mac).command\" to start."
read -r -p "  Press Enter to close..."
