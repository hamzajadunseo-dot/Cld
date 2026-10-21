"""Script + voiceover -> finished royal documentary video.

    python make_video.py --script script.txt --voiceover vo.mp3 --out output/my_video.mp4

Outputs (next to the MP4, in <name>_project/):
    my_video.mp4             final 1920x1080 30 fps H.264
    <name>_project/my_video.xml      Premiere Pro timeline (File > Import)
    <name>_project/credits.txt       image credits for the YouTube description
    <name>_project/missing_assets.txt  what to add to the library
    <name>_project/segments.json / analysis.json / timeline.json   intermediate steps

Options:
    --subtitles          burn captions into the video
    --music FILE         use this music track (default: picked from Music/ by mood)
    --no-music
    --intro FILE         a real clip (e.g. news headline, 5-8 s) played first with its own sound
    --stop-after STAGE   transcribe | analyze | match  (check a stage without rendering)
"""
import argparse
import collections
import json
import sys
import time
from pathlib import Path

from royal.config import load_config
from royal.db import IndexDB
from royal.logs import get_logger


def pick_music(assets, rows, log):
    tracks = [a for a in assets if a["type"] == "music"]
    if not tracks:
        log.info("No tracks in Music/ - video will have voiceover only")
        return None
    moods = collections.Counter(r.get("mood") for r in rows)
    sad = moods["sad"] + moods["solemn"] + moods["tense"]
    want = ["sad", "emotional", "somber", "dramatic", "documentary"] if sad > len(rows) * 0.3 \
        else ["documentary", "calm", "inspiring", "emotional", "neutral"]
    for w in want:
        for t in tracks:
            if w in (t["mood"] or "") or w in (t["tags"] or []):
                return t
    return tracks[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", type=Path, help=".txt or .docx (optional - without it the transcript is used)")
    ap.add_argument("--voiceover", type=Path, required=True, help=".mp3 / .wav")
    ap.add_argument("--out", type=Path, help="output .mp4 (default: output/<voiceover name>.mp4)")
    ap.add_argument("--subtitles", action="store_true")
    ap.add_argument("--music", type=Path)
    ap.add_argument("--intro", type=Path, help="real clip (e.g. news headline) played first, with its own sound")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--with-music", action="store_true", help="add background music (off by default, config audio.music)")
    ap.add_argument("--with-sfx", action="store_true", help="add transition sound effects (off by default, config audio.sfx)")
    ap.add_argument("--stop-after", choices=["transcribe", "analyze", "match"])
    ap.add_argument("--subject", help="who the video is about, e.g. Princess_Catherine (default: auto-detect)")
    ap.add_argument("--config")
    ap.add_argument("--reuse-timeline", action="store_true",
                    help="re-render from the saved timeline.json (after a hand edit) - no re-matching")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.subject:
        cfg["subject_override"] = [x.strip() for x in args.subject.split(",")]
    data_dir = cfg.path("data_dir")
    library = cfg.path("library")
    log = get_logger("make_video", data_dir)
    for p in (args.voiceover, args.script, args.intro):
        if p and not p.exists():
            log.error("File not found: %s", p)
            sys.exit(1)
    out = (args.out or Path("output") / (args.voiceover.stem + ".mp4")).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / f"{out.stem}_project"
    work.mkdir(exist_ok=True)
    t_start = time.time()
    db = IndexDB(data_dir / "index.db")

    # ---------------- Stage 2
    from royal.transcribe import run_stage2
    log.info("== Stage 2: voiceover -> timestamps")
    segs, words, duration = run_stage2(args.script, args.voiceover, db, cfg, log, work)
    from royal.callouts import build_callouts
    callouts = build_callouts(words, cfg, duration)
    (work / "callouts.json").write_text(json.dumps(callouts, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("On-screen text: %s", ", ".join(f"{n} {k}" for k, n in
             collections.Counter(c["kind"] for c in callouts).items()) or "none")
    if args.stop_after == "transcribe":
        return log.info("Stopped after transcribe. See %s", work / "segments.json")

    if args.reuse_timeline:
        tl = json.loads((work / "timeline.json").read_text(encoding="utf-8"))
        items = tl["items"]
        music = Path(tl["music"]) if tl.get("music") else None
        if not (cfg.get("audio", {}).get("music", False) or args.with_music or args.music) or args.no_music:
            music = None
        sfx_events = json.loads((work / "sfx.json").read_text(encoding="utf-8"))
        hook_end = max([sg["end"] for sg in segs if sg.get("hook")] or [0.0])
        stopdowns = [(e["t"], e["t"] + e["dur"]) for e in sfx_events if e["kind"] == "stopdown"]
        from royal.export import save_timeline, write_credits
        write_credits(items, work / "credits.txt")
        log.info("Re-rendering the saved timeline (%d shots)", len(items))
    else:
        # ---------------- Stage 3
        from royal.analyze import run_stage3
        log.info("== Stage 3: script analysis")
        assets = db.assets()
        people_ids = sorted({p for a in assets for p in (a["people"] or [])} |
                            {d.name for d in (library / "People").iterdir() if d.is_dir()})
        rows = run_stage3(segs, people_ids, cfg, db, log, work)
        if args.stop_after == "analyze":
            return log.info("Stopped after analyze. See %s", work / "analysis.json")

        # ---------------- Stage 4
        from royal.clip_embed import ClipEmbedder
        from royal.matcher import Matcher, write_missing_report
        log.info("== Stage 4: matching visuals")
        clip = ClipEmbedder(cfg.clip["model"], cfg.clip["pretrained"])
        from royal.stock import prepare_stock
        seg_concepts = prepare_stock(segs, cfg, library, log)
        if seg_concepts and any((library / "Stock").rglob("*.mp4")):
            from royal.indexer import Indexer          # index any newly downloaded stock clips
            Indexer(cfg).run(captions=False)
            assets = db.assets()
        matcher = Matcher(assets, clip, cfg, log, words)
        # footage used in earlier videos is down-ranked so each video looks fresh
        past = []
        for f in sorted(Path(__file__).parent.glob("projects/*/*_project/timeline.json")):
            if f.parent.resolve() != work.resolve():
                try:
                    past.append(json.loads(f.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
        log.info("Freshness: down-ranking footage already used in %d earlier videos", matcher.load_history(past))
        items = matcher.build(rows, seg_concepts)
        n_stock = sum(1 for it in items if str(it.get("path", "")).startswith("Stock/"))
        if seg_concepts:
            log.info("Stock footage: %d shots from %d concept moments", n_stock, len(seg_concepts))
        write_missing_report(work / "missing_assets.txt", matcher.missing)
        kinds = collections.Counter(i["kind"] for i in items)
        log.info("Timeline: %d shots (%s), %d weak/missing moments", len(items),
                 ", ".join(f"{n} {k}" for k, n in kinds.items()), len(matcher.missing))
        want_music = (cfg.get("audio", {}).get("music", False) or args.with_music or args.music) and not args.no_music
        music = (args.music or None) if want_music else None
        if want_music and not music:
            m = pick_music(assets, rows, log)
            music = library / m["path"] if m else None
        from royal.export import save_timeline, write_credits
        save_timeline(items, duration, work / "timeline.json",
                      {"voiceover": str(args.voiceover.resolve()), "music": str(music) if music else None})
        from royal.style import apply_style
        hook_end = max([sg["end"] for sg in segs if sg.get("hook")] or [0.0])
        sfx_events = apply_style(items, cfg, hook_end, callouts, seed=out.stem, segs=segs)
        stopdowns = [(e["t"], e["t"] + e["dur"]) for e in sfx_events if e["kind"] == "stopdown"]
        lay = collections.Counter(i.get("layout", "full") for i in items if i["kind"] != "text")
        trs = collections.Counter(i.get("transition") for i in items)
        log.info("Style: %s | transitions %s | %d sound effects",
                 ", ".join(f"{n} {k}" for k, n in lay.items()), dict(trs), len(sfx_events))
        (work / "sfx.json").write_text(json.dumps(sfx_events, indent=0), encoding="utf-8")
        write_credits(items, work / "credits.txt")
        del clip
        if args.stop_after == "match":
            return log.info("Stopped after match. See %s", work / "timeline.json")


    # ---------------- Stage 5
    from royal.export import export_premiere_xml
    from royal.render import mix_audio, render_video
    log.info("== Stage 5: rendering %s (%.1f min of video)", out.name, duration / 60)
    silent = work / "_video_only.mp4"
    from royal.sfx import build_track
    if not (cfg.get("audio", {}).get("sfx", False) or args.with_sfx):
        sfx_events, stopdowns = [], []          # user (2026-10-14): voiceover only - no SFX, no music dips
        log.info("Audio: voiceover%s, no sound effects", " + music" if music else " only")
    sfx_track = build_track(sfx_events, duration, cfg.get("style", {}).get("sfx", {}).get("levels_db", {}),
                            work / "_sfx.wav", library / "SFX", log)
    render_video(items, duration, library, silent, cfg, log, words=words, subtitles=args.subtitles,
                 hook_captions_until=hook_end if cfg.get("style", {}).get("hook_captions", True) else 0.0,
                 card_dir=work / "text_cards", callouts=callouts, callout_dir=work / "callouts")
    intro_dur = 0.0
    if args.intro:
        main = work / "_main.mp4"
        mix_audio(silent, args.voiceover, music, main, duration, cfg, sfx_track, hook_end, stopdowns)
        from royal.render import prepend_intro
        intro_dur = prepend_intro(args.intro.resolve(), main, out, cfg, work)
        main.unlink(missing_ok=True)
        log.info("Intro clip added (%.1f s) before the narration", intro_dur)
    else:
        mix_audio(silent, args.voiceover, music, out, duration, cfg, sfx_track, hook_end, stopdowns)
    silent.unlink(missing_ok=True)
    save_timeline(items, duration, work / "timeline.json",
                  {"voiceover": str(args.voiceover.resolve()), "music": str(music) if music else None})
    export_premiere_xml(items, duration, library, args.voiceover.resolve(), music, work / f"{out.stem}.xml",
                        out.stem, cfg, args.intro.resolve() if args.intro else None, intro_dur, callouts,
                        sfx_track)
    log.info("Done in %.1f min", (time.time() - t_start) / 60)
    log.info("  Video     : %s", out)
    log.info("  Premiere  : %s", work / f"{out.stem}.xml")
    log.info("  Credits   : %s", work / "credits.txt")
    log.info("  Missing   : %s", work / "missing_assets.txt")


if __name__ == "__main__":
    main()
