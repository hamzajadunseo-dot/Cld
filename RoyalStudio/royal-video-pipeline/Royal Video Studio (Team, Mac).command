#!/bin/bash
# Royal Video Studio - TEAM MODE: other computers (e.g. over Tailscale) can connect and must log in.
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
[ -d "../models_cache" ] && export HF_HOME="$(cd .. && pwd)/models_cache"
export STUDIO_TEAM=1
.venv/bin/python app.py
read -r -p "Press Enter to close..."
