# Video Editing Pipelines for Faceless YouTube Channels

Step-by-step editing workflows matched to the niches ranked in [README.md](README.md). Every pipeline uses the same core stages and swaps out only the parts that differ by format. All the asset sources listed are royalty-free or properly licensed, so the videos stay safe to monetize.

---

## The Core Pipeline (Used by Every Format)

| Stage | What Happens | Tools (Free → Paid) | Output |
|-------|--------------|---------------------|--------|
| 1. Script | Write the script, mark visual cues as `[B-ROLL: …]` / `[SCREEN: …]` | Google Docs, Claude | Final script + shot list |
| 2. Voiceover | Record or generate narration, then clean it up | Audacity → Adobe Podcast Enhance, ElevenLabs | `vo_clean.wav` (−16 LUFS, no breaths) |
| 3. Asset pull | Gather footage, images, music and SFX from the shot list | Pexels, Pixabay → Storyblocks, Envato | `/assets` folder, named by scene |
| 4. Rough cut | Lay the VO on the timeline and cut the visuals to it | DaVinci Resolve (free), CapCut → Premiere Pro | Timeline with no gaps |
| 5. Polish | Add captions, zooms, lower-thirds, music ducking and SFX | Resolve Fusion, CapCut → After Effects | Fine cut |
| 6. Audio mix | VO at −16 LUFS, music at −28 to −32 LUFS under speech | Fairlight (in Resolve), Audition | Mixed master |
| 7. Export | H.264 or H.265, 1080p/4K, high bitrate | Resolve Deliver page | `final.mp4` |
| 8. QC & upload | Watch at 1.5×, check captions and claims, upload with chapters | YouTube Studio | Published video |

**Folder structure (copy for every video):**
```
/VIDEO_###_slug
  /01_script     script.docx, shotlist.txt
  /02_audio      vo_raw.wav, vo_clean.wav, music/, sfx/
  /03_assets     footage/, images/, screen/
  /04_project    project.drp
  /05_export     final.mp4, thumbnail.png, captions.srt
  LICENSES.txt   ← source + license for every third-party asset
```

> **Rule:** fill in `LICENSES.txt` while pulling assets, not afterwards. It is your evidence if a Content ID claim or a "reused content" review ever comes up.

---

## Pipeline A — Screen-Recording Tutorials
**Niches:** #1 B2B SaaS, #3 AI Tools for Professionals

1. **Record clean:** 1920×1080 or 2560×1440, browser at 125% zoom, bookmarks bar hidden, test data only (never real client data).
2. **Record the VO separately** from the screen. Re-recording a fluffed line is far easier than re-recording a screen take.
3. **Auto zoom and pan** on every click (Screen Studio, Cursorful, or Resolve keyframes at 120–150%).
4. **Cut dead time:** remove loading spinners and typing pauses over 1 second.
5. **Callouts:** box or arrow on the element being discussed, plus a lower-third for any keyboard shortcut.
6. **Chapters** for every feature covered; tutorial viewers jump straight to what they need.

⏱️ **Target:** 2–3 hours of editing per 10-minute video.

---

## Pipeline B — Stock-Footage Explainers
**Niches:** #2 Insurance, #4 Senior Health, #5 Legal, #7 Real Estate, #8 Personal Finance

1. **Shot list from the script:** one visual every 3–5 seconds. Write the search terms beside each line.
2. **Mix the footage types:** stock video (≈60%), animated text and charts (≈25%), images with a Ken Burns effect (≈15%). Pure stock footage reads as low effort and risks the "reused content" policy.
3. **Make data visual:** every number, statistic or comparison becomes an on-screen chart or text card (Canva, Flourish, or Resolve Text+).
4. **Show your sources on screen** for YMYL niches (finance, health, legal): put a small citation in the corner whenever a claim is made.
5. **Captions** burned in or uploaded as an SRT, with key terms highlighted in a brand color.
6. **Music:** low-key ambient or corporate bed, ducked under the VO; switch tracks at each section change.

⏱️ **Target:** 4–6 hours per 10-minute video.

---

## Pipeline C — Ambient & Sleep Audio
**Niche:** #6 Sleep Soundscapes

1. **Audio first:** build or layer the soundscape in Audacity or Reaper, crossfade loop points, and check that the loop is seamless at full volume.
2. **Visual:** one high-quality looping clip or still with subtle motion (rain on glass, fireplace, slow parallax). Viewers want calm, not cuts.
3. **Render efficiently:** render a 10–30 minute loop once, then concatenate to 3–10 hours with ffmpeg instead of rendering the whole length:
   ```bash
   # list.txt contains: file 'loop.mp4' (repeated N times)
   ffmpeg -f concat -safe 0 -i list.txt -c copy final_8h.mp4
   ```
4. **Optional dark-screen version:** fade to black after 10 minutes, and say so in the title.
5. **Loudness:** around −20 to −24 LUFS. Sleep audio should be quieter than speech content.

⏱️ **Target:** 1–2 hours per video once a template exists.

---

## Pipeline D — Story & Literary Analysis
**Niche:** #10 Literary & Story Analysis

1. **Script-led:** the narration carries the video, so spend your time on pacing in the VO rather than on visuals.
2. **Visuals:** book covers and short quotes (fair use applies only to *commentary*, so keep quoted text brief and always analyze it), AI-generated or commissioned scene art, and text animations for key quotes.
3. **Structure markers:** a title card for each act or theme, which become chapters.
4. **Music:** a cinematic bed that changes mood with the story beats.

⏱️ **Target:** 3–4 hours per 15-minute video.

---

## Pipeline E — Hands-On / Partial-Face
**Niches:** #7 Real Estate (on-site), #9 Homesteading

1. **Shoot multicam where you can:** wide shot plus a close-up on hands or the work, and a phone works fine as the second angle.
2. **Sync** on a clap, or use Resolve's auto-sync by waveform.
3. **Show progress:** use before/after splits and time-lapses for long tasks.
4. **Fix natural audio first:** run noise reduction before adding music, and keep some ambient sound for authenticity.

⏱️ **Target:** 6–10 hours per video (most of the time goes into footage review).

---

## Royalty-Free Asset Sources

| Type | Free | Paid (Better Quality / Clearer License) | Notes |
|------|------|-----------------------------------------|-------|
| Stock video | Pexels, Pixabay, Mixkit, Coverr | Storyblocks, Envato Elements, Artgrid | Read each license; some free clips forbid standalone resale but allow use in YouTube videos |
| Images | Unsplash, Pexels, Wikimedia Commons (check each license) | Adobe Stock, Shutterstock | Wikimedia often requires attribution |
| Music | YouTube Audio Library, Pixabay Music, Free Music Archive (CC-BY) | Epidemic Sound, Artlist, Musicbed | Register your channel with a paid service to clear Content ID claims |
| SFX | Freesound (check CC type), Pixabay SFX, Zapsplat | Epidemic Sound, Soundly | Freesound CC-BY needs credit in the description |
| Fonts | Google Fonts | Adobe Fonts (with Creative Cloud) | Google Fonts are free for commercial use |

**Biggest risk:** "royalty-free" does **not** mean "free" or "no conditions". Always check whether attribution is required and whether the license covers monetized use.

---

## Free Starter Stack vs. Scaled Stack

| | Starter ($0/mo) | Scaled (~$60–120/mo) |
|---|---|---|
| Editor | DaVinci Resolve (free) | Premiere Pro + After Effects, or Resolve Studio (one-time) |
| Voice | Your own mic + Audacity | ElevenLabs, or a hired voice actor |
| Footage | Pexels / Pixabay | Storyblocks or Envato Elements |
| Music | YouTube Audio Library | Epidemic Sound or Artlist |
| Captions | Resolve auto-captions / CapCut | Descript |
| Thumbnails | Canva free | Canva Pro / Photoshop |

**When to scale:** once the channel earns more than the paid stack costs, or once editing time is the bottleneck on how often you can upload.

---

## Pre-Upload QC Checklist

- [ ] No gaps, black frames or leftover placeholder text
- [ ] VO loudness around −16 LUFS (−20 to −24 for sleep content), no clipping
- [ ] Captions checked against the script (names, numbers, jargon)
- [ ] Every on-screen claim has a source (YMYL niches)
- [ ] `LICENSES.txt` complete, and attributions added to the description
- [ ] Chapters added (`0:00 Intro` is required for chapters to show)
- [ ] Thumbnail is readable at phone size
- [ ] End screen and cards set
