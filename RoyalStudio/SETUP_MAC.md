# Royal Video Studio: Mac setup

The tool was Windows-only. It now also runs on macOS: Mac fonts, Finder instead of Explorer, the Stop button,
the Apple GPU for the CLIP search model, and `.command` launchers instead of `.bat` files.
Apple Silicon (M1–M4) runs it best. Intel Macs work too, just slower.

## 1. Get the files
On GitHub, open the repo, switch to the branch **`claude/eloquent-hopper-es808k`** → green **Code** button → **Download ZIP**.
Unzip it and move the `RoyalStudio` folder to somewhere simple, for example your home folder:
```
~/RoyalStudio/royal-video-pipeline/
~/RoyalStudio/Royal_Media_Library/
```
The two folders must stay **next to each other**.

## 2. Install Homebrew, Python 3.11 and FFmpeg (once)
Open **Terminal** (Cmd + Space → type "Terminal"). If you don't have Homebrew yet, install it first from https://brew.sh:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
When it finishes, it prints 2 "Next steps" commands. Run them. Then install Python and FFmpeg:
```bash
brew install python@3.11 ffmpeg
```

## 3. Run the one-time setup
Run these in Terminal. They download about 3 GB and take 10–20 minutes:
```bash
cd ~/RoyalStudio/royal-video-pipeline
xattr -dr com.apple.quarantine .            # lets macOS open the downloaded launchers
chmod +x *.command
./"SETUP - run once (Mac).command"
```
The setup is finished when you see `[OK] all packages work`.

## 4. Add your keys
```bash
cp .env.example .env
open -e .env
```
Then fill in:
```
ANTHROPIC_API_KEY=        (optional; leave blank if you don't have one)
PEXELS_API_KEY=your_key   (free at https://www.pexels.com/api/)
```
Save and close. Don't share `.env` with anyone.

## 5. Fill the media library
Put clips and photos in `Royal_Media_Library` (`People/King_Charles/`, `Events/Coronation_2023/`, `Clips/…`, `Stock/`).
See `royal-video-pipeline/README.md` for the full layout. Then index the library:
```bash
.venv/bin/python index_library.py
```
The first run downloads about 1.2 GB of AI models, one time only. Run it again whenever you add new media.

## 6. Start it
Double-click **`Royal Video Studio (Mac).command`** in Finder, or run `./"Royal Video Studio (Mac).command"`.
The studio opens at http://127.0.0.1:5077. Keep the Terminal window open while you use it.

## 7. Test it
Start with a short script and an mp3 of about 1 minute.

## Good to know
- **Speed:** voice transcription (Whisper) runs on the CPU on a Mac, because there is no NVIDIA GPU. A 10-minute
  voice-over takes a few minutes on an M-series Mac. The CLIP search model uses the Apple GPU.
- **"cannot be opened because it is from an unidentified developer":** run the `xattr` line from step 3 again,
  or right-click the file → **Open** → **Open**.
- **Commands in the README:** wherever it says `.venv\Scripts\python.exe something.py`, use `.venv/bin/python something.py` on Mac.
- `make_package.py` (copies everything to a USB drive) is written for Windows drive letters. You don't need it.
- If something fails, take a screenshot and paste it into Claude.
