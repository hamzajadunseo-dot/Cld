#!/bin/bash
# Royal Video Studio - opens http://127.0.0.1:5077 (this Mac only)
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# portable copy: AI models travel with the package
[ -d "../models_cache" ] && export HF_HOME="$(cd .. && pwd)/models_cache"
.venv/bin/python app.py
read -r -p "Press Enter to close..."
