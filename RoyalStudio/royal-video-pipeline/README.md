# Royal Family Video Pipeline

Turns **script + voiceover → finished YouTube video** using your own Royal Family media library.
It also exports a Premiere Pro timeline so an editor can do the final polish.

| Stage | What it does | Status |
|---|---|---|
| 1. Media indexer | Scans the library and tags people (face recognition), captions, search vectors and clip shots | ✅ built |
| 2. Voiceover → timestamps | faster-whisper word timings, split into 3–8 s segments | next |
| 3. Script analysis | Claude works out who/what/when/mood for each segment | later |
| 4. Asset matching | Picks the best visual for each segment, with no repeats | later |
| 5. Assembly & render | Ken Burns effect, crossfades, music ducking, MP4, Premiere XML, credits.txt | later |

---

## One-time setup (Windows)

You need **Python 3.11** and **FFmpeg**. Both are already installed on this PC.

1. Open **PowerShell** in this folder. In Explorer, go to `F:\yt_channel_tracker\royal-video-pipeline`, then Shift + right-click → *Open PowerShell window here*.
2. Create the environment and install everything (about 3 GB, 10–20 min):
   ```
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. Add your Claude API key:
   - Copy `.env.example` and rename the copy to `.env`.
   - Open `.env` in Notepad and paste your key after `ANTHROPIC_API_KEY=`.
   - Get a key at https://console.anthropic.com → *API Keys*.
4. Check `config.yaml`. The `library:` line must point at your media folder.

The first run downloads the face-recognition models (~280 MB) and the CLIP model (~900 MB). They are downloaded only once.

---

## Your media library

```
Royal_Media_Library/
  People/King_Charles/        photos + _metadata.json (license, author, source)
  People/Prince_William/ ...
  Events/Coronation_2023/     put the year in the folder name
  Clips/<anything>/           your video clips (.mp4 .mov .mkv ...)
  Music/<mood>/               background tracks; the folder name is the mood (e.g. Music/sad/)
```

- `royal_downloader.py` (in `F:\yt_channel_tracker`) fills `People/` from Wikimedia Commons. Run it from `F:\yt_channel_tracker`.
- Files or folders that start with `_` are ignored.
- For your own clips, add a `_metadata.json` in the same format so the license stays attached. Anything without a license is listed in the report.

---

## Stage 1: build the index

```
.\.venv\Scripts\python.exe index_library.py
```

- **Incremental:** it only processes new or changed files, so re-run it any time you add media.
- A quick test on 20 files: `index_library.py --limit 20`
- Without the Claude API (free, no captions): `index_library.py --no-captions`
- Cost: captions use Claude Haiku 4.5, about **$2–3 per 1,000 photos/shots**. Each file is captioned once and cached forever, even if you rename or move it.

**Outputs** (in `data/`):

| File | What it is |
|---|---|
| `index.db` | The database the next stages use |
| `index.json` | The same data, human-readable |
| `index_report.txt` | People counts, **folder mismatches** (photos in the wrong person's folder), files missing a license, skipped files |
| `logs/index.log` | Full log of every run |

### Check the results visually

```
.\.venv\Scripts\python.exe search_library.py "sad moment at a funeral"
.\.venv\Scripts\python.exe search_library.py "waving from a balcony" --person King_Charles
.\.venv\Scripts\python.exe search_library.py --person Prince_Harry --top 40
.\.venv\Scripts\python.exe search_library.py "crowd cheering" --type clip
```

This opens a picture sheet in your browser. Each result shows the recognised people (with a confidence score), the caption, mood, shot type, year and license.

---

## How people are recognised

1. For each `People/<Person>` folder, the tool finds the face that appears consistently in that folder. That becomes the person's reference.
2. Every face in every photo and clip frame is compared with all the references.
3. So a photo of William inside Charles's folder gets tagged **William**, and it shows up under "folder mismatches" in the report.

The more people you download, the better it gets, because the tool can tell similar faces apart (for example William vs George).
If a photo has no recognisable face (back view, far away, a painting), it keeps the folder name as a low-confidence tag.

Tuning is in `config.yaml` → `faces:`:
- Raise `match_threshold` if you see wrong names.
- Lower it if people are missed.

---

## Copyright notes

- Every asset keeps its license, author and source page from `_metadata.json` through the whole pipeline. Stage 5 turns this into `credits.txt` for the YouTube description.
- Clips are limited to 5 seconds per shot (`rules:` in `config.yaml`).
