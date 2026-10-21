# Royal Video Studio: setup guide (paste this into Claude Code)

Hi Claude. My friend gave me this tool. Please set it up on my Windows PC and get it running.
Follow the steps below one at a time, and ask me before anything that needs my own keys or accounts.

## What this tool is
**Royal Video Studio** turns a **script + voice-over (mp3)** into a finished YouTube documentary video (1080p .mp4 + Premiere XML).
- `make_video.py`: the main pipeline (whisper transcription → matches each sentence to clips from the media library → render → loudness → QC).
- `app.py`: local web app (Flask) at `http://127.0.0.1:5077`. Started by `Royal Video Studio.bat`.
- `royal/`: pipeline modules. `studio/`: web pages. `config.yaml`: all settings.
- It needs a **media library** folder `Royal_Media_Library` next to `royal-video-pipeline` (path set in `config.yaml` → `paths.library`).

## Setup steps
1. Unzip so the folder looks like this:
   ```
   D:\RoyalStudio\royal-video-pipeline\      (this code)
   D:\RoyalStudio\Royal_Media_Library\       (create it: the clips/photos go here)
   ```
2. Install Python 3.11 and FFmpeg (once):
   ```
   winget install -e --id Python.Python.3.11
   winget install -e --id Gyan.FFmpeg
   ```
3. In `royal-video-pipeline`, run **`SETUP - run once.bat`** (creates `.venv`, installs requirements, ~3 GB, 10-20 min).
   If it fails, follow the "One-time setup" section in `README.md` manually.
   With an NVIDIA GPU, torch is installed with CUDA (whisper runs much faster); without one, it runs on CPU (slower).
4. Copy `.env.example` → `.env`. Add a free **Pexels API key** (pexels.com/api) for stock footage.
   (`ANTHROPIC_API_KEY` is optional.) Never share `.env`.
5. Media library: read `README.md` for the folder layout (`People/<Person_Name>/` with clips and photos, `Stock/`, etc.),
   then run `index_library.py` once after adding files.
6. Start the tool with **`Royal Video Studio.bat`** → open `http://127.0.0.1:5077`.
7. Test it with a short script + mp3 first.

## Notes
- Style rules for this channel are in `CLAUDE.md` and `config.yaml` (navy lower-thirds, no subtitles, voice-over only: no music, no SFX).
- `Royal Video Studio (Team).bat` is for sharing the studio with teammates over Tailscale (logins in the Team tab). Not needed for personal use.
- Only use footage you have the rights to use; Wikimedia/Pexels licences are written to `credits.txt` for every video.
