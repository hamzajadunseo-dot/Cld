# Royal Video Studio: Windows setup checklist

The code has been checked: all 31 Python files parse cleanly. The setup scripts only install Python packages, and the
code only connects to Pexels, Anthropic (optional), the PyTorch/pip servers and the face-model download on GitHub.
The tool is **Windows-only** (it uses Windows fonts, `taskkill` and `.bat` launchers), so it has to be installed on your PC.

## 1. Put the files in place
Download this `RoyalStudio` folder from GitHub (or use the original zip) so you have:
```
D:\RoyalStudio\royal-video-pipeline\
D:\RoyalStudio\Royal_Media_Library\
```
Any drive works, as long as the two folders sit **next to each other**. `config.yaml` points at `../Royal_Media_Library`.

## 2. Install Python 3.11 and FFmpeg (once)
Open **PowerShell** and run:
```powershell
winget install -e --id Python.Python.3.11
winget install -e --id Gyan.FFmpeg
```
Then **close PowerShell and open a new one**, so Windows can find `ffmpeg`.

## 3. Run the one-time setup
Double-click **`SETUP - run once.bat`** in `royal-video-pipeline`. It downloads about 3 GB and takes 10–20 minutes.
- If you have an NVIDIA GPU, it installs the GPU version of torch automatically. Otherwise it installs the CPU version, which is slower.
- The setup is finished when you see `[OK] all packages work`. It also puts a desktop shortcut on your desktop.

## 4. Add your keys
In `royal-video-pipeline`, copy `.env.example`, rename the copy to `.env` and open it in Notepad:
```
ANTHROPIC_API_KEY=        (optional; leave blank if you don't have one)
PEXELS_API_KEY=your_key   (free at https://www.pexels.com/api/)
```
Don't share `.env` with anyone or upload it anywhere.

## 5. Fill the media library
```
Royal_Media_Library\
  People\King_Charles\      photos + clips of that person
  Events\Coronation_2023\   put the year in the folder name
  Clips\<anything>\         your video clips
  Stock\
```
Then index the library (PowerShell, inside `royal-video-pipeline`):
```powershell
.\.venv\Scripts\python.exe index_library.py
```
The first run downloads about 1.2 GB of AI models, one time only. Run it again whenever you add new media.

## 6. Start it
Double-click **Royal Video Studio** on your desktop, or `Royal Video Studio.bat`. The studio opens at http://127.0.0.1:5077.

## 7. Test it
Start with a short script and an mp3 of about 1 minute before you try a full video.

## If something fails
- `Python 3.11 is not installed` / `FFmpeg is not installed`: redo step 2, then open a **new** window.
- The setup fails partway: check your internet connection and run it again. It picks up where it stopped.
- Take a screenshot of the error and paste it into Claude.
