r"""Copy the whole Royal Video Studio to a USB drive / folder for a teammate.

    .venv\Scripts\python.exe make_package.py --target E:\

Creates E:\RoyalStudio\ with
    royal-video-pipeline\   the tool (no .venv, no finished projects, no API keys, no team accounts)
    Royal_Media_Library\    all clips / photos / stock / music (+ the index, so nothing is re-scanned)
    models_cache\           whisper + CLIP models (so they are not downloaded again)
    START HERE.txt          setup steps for the teammate
Re-running only copies what changed (robocopy), so updating a USB later is quick.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from royal.config import load_config  # noqa: E402

HF_MODELS = ["models--mobiuslabsgmbh--faster-whisper-large-v3-turbo", "models--timm--vit_large_patch14_clip_224.openai"]
TOOL_SKIP_DIRS = [".venv", "projects", "__pycache__", ".claude", "logs", "callouts", "text_cards"]
TOOL_SKIP_FILES = [".env", "studio_users.json", "studio_jobs.json", "studio_secret.key", "studio_log.txt",
                   "*.jpg", "*.png", "download_log.txt"]

GUIDE = r"""ROYAL VIDEO STUDIO - setup for a new laptop
===========================================

1. Copy this whole "RoyalStudio" folder from the USB to the laptop, e.g. to  D:\RoyalStudio
   (keep the 3 folders together: royal-video-pipeline, Royal_Media_Library, models_cache)

2. Install Python 3.11 and FFmpeg once - open "Terminal" (or PowerShell) and run:
       winget install -e --id Python.Python.3.11
       winget install -e --id Gyan.FFmpeg
   then CLOSE the terminal.

3. Open the folder  royal-video-pipeline  and double-click   SETUP - run once.bat
   (needs internet, downloads ~3 GB, takes 10-20 minutes).

4. Optional - stock footage (court, documents, mansion...): get a FREE key at
   https://www.pexels.com/api/  then in royal-video-pipeline make a file named  .env  with one line:
       PEXELS_API_KEY=your_key_here
   (without it the videos simply use no new stock clips)

5. Double-click "Royal Video Studio" on the desktop -> the studio opens in the browser.
   New Video tab: paste script -> Check script -> voiceover mp3 -> whose video -> Make Video.

No NVIDIA graphics card? It still works, just slower (a 25-min voiceover can take 1-2 hours).
Using Claude Code on this laptop? Open the royal-video-pipeline folder in it - CLAUDE.md tells
Claude all the house rules.
"""


def robocopy(src: Path, dst: Path, xd=(), xf=()):
    cmd = ["robocopy", str(src), str(dst), "/E", "/MT:8", "/R:1", "/W:1", "/NFL", "/NDL", "/NP", "/NJH"]
    if xd:
        cmd += ["/XD", *xd]
    if xf:
        cmd += ["/XF", *xf]
    print(f"\n>> {src}  ->  {dst}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    print("\n".join(l for l in r.stdout.splitlines() if l.strip())[-600:], flush=True)
    if r.returncode >= 8:
        sys.exit(f"robocopy failed ({r.returncode}) for {src}")


def size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path, required=True, help="USB drive or folder, e.g. E:\\")
    a = ap.parse_args()
    cfg = load_config(None)
    library = cfg.path("library")
    hf = Path.home() / ".cache" / "huggingface" / "hub"
    out = a.target / "RoyalStudio"

    need = size(library) + sum(size(hf / m) for m in HF_MODELS) + size(ROOT / "data")
    free = shutil.disk_usage(a.target).free
    print(f"Package size about {need / 1e9:.1f} GB - free on {a.target}: {free / 1e9:.1f} GB", flush=True)
    already = size(out)
    if need - already > free:
        sys.exit("Not enough space on the target drive.")

    robocopy(ROOT, out / "royal-video-pipeline", xd=TOOL_SKIP_DIRS, xf=TOOL_SKIP_FILES)
    robocopy(library, out / "Royal_Media_Library")
    for m in HF_MODELS:
        if (hf / m).exists():
            robocopy(hf / m, out / "models_cache" / "hub" / m)
        else:
            print(f"   (model {m} not found - it will download on first use)")
    (out / "START HERE.txt").write_text(GUIDE, encoding="utf-8")
    print(f"\nDone: {out}  ({size(out) / 1e9:.1f} GB). Give the teammate the USB and 'START HERE.txt'.")


if __name__ == "__main__":
    main()
