# Royal Video Studio — notes for Claude

Faceless YouTube royal-documentary channel. This folder turns **script + voiceover → finished 1080p video**
(+ Premiere XML, credits.txt, missing_assets.txt) from the local media library `../Royal_Media_Library`.

## How to run
- Web app (easiest): `Royal Video Studio.bat` → http://127.0.0.1:5077 (New Video / Replace Hook / Intro Clip / Projects / Library).
- CLI: `.venv\Scripts\python.exe make_video.py --script projects\<name>\script.txt --voiceover projects\<name>\vo.mp3 --out projects\<name>\<name>.mp4 --subject Prince_Harry`
  - `--subject` = whose video it is (folder name in `People/`); two names for a couple video: `Prince_Harry,Meghan_Markle`.
  - `--stop-after match` plans the edit without rendering (fast check). `--reuse-timeline` re-renders a hand-edited `timeline.json`.
- New hook recorded: `replace_hook.py --project projects\<name> --hook new_hook.mp3 --subject <Person>`.
- Real speech/news clip before the narration (locked intro style): `intro_hook.py --clip c.mp4 --main projects\x\x.mp4 --tag "KING CHARLES III  ·  2022"`.
- Several real clips as a cold-open montage (bold captions + credits + suspense sound): `hook_montage.py --plan projects\<name>\hook_plan.json --out projects\<name>\hook_montage.mp4` (see the docstring for the plan format).
- New media in `People/<Person>/` → `index_library.py` (face recognition tags every shot).

## Script layout
`## HOOK` first, then `---` (or a `CHAPTER n` line) between sections. Chapter titles are not narrated.
The studio's "Check script" shows where the hook ends — the hook must end before chapter 1.

## House rules (locked by the channel owner — do not change unless asked)
- **Audio = voiceover only.** No background music, no sound effects (`audio.music/sfx: false` in config.yaml).
- **Hook = video clips only, never photos.** Hook shows only the video's subject (couple video: both).
  A non-royal guest named in a hook line (e.g. Tyler Perry) may appear on that line — only if they have clips.
  Place stock (mansion / Montecito / Beverly Hills) is allowed in the hook.
- Pacing: same pace in hook and body (median ~3.6 s per shot, cuts on the narrator's pauses), clips ≤ 5 s.
- About 85% video clips overall; photos only where no good clip exists. A shot is used at most twice per video,
  and footage used in earlier videos is down-ranked (fresh footage every video).
- Text: navy lower-third (navy box, white frame, gold bar, white title, light-blue subtitle) for names, dates, events.
  Navy card background. **Never add subtitles/captions.**
- Clean old-film scratches, crop clip edges 4%.
- Stock (Pexels) only for concepts (court, documents, hospital, mansion…), max ~6%, never in the first 2.5 minutes (opening = royal footage).
- Don't run test renders unless asked — use `--stop-after match` to check a plan.

## Content limits
Scripts are dramatized stories about real people. Keep them clearly framed as dramatization, and don't render
claims presented as fact that a real person's child has a different biological parent, or similar invented
accusations about real minors.

## Library tips
More footage of a person = more clips and less repetition. Harry, Meghan and Tyler Perry need the most.
Download only free-licensed media (Wikimedia CC/PD, Pexels); keep `_metadata.json` with licences.
