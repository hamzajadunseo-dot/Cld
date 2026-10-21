"""Re-do the sound (SFX + music + voice) of a finished video WITHOUT re-rendering the picture.

Use it after you add new files to Royal_Media_Library/SFX/ or Music/, or change SFX levels in config.yaml:

    python remix_audio.py projects/catherine_new_role/catherine_new_role.mp4
    python remix_audio.py projects/x/x.mp4 --music "F:/.../Music/emotional/track.mp3"
    python remix_audio.py projects/x/x.mp4 --no-music

Takes ~1-2 minutes. The video file is replaced only after the new mix succeeded.
"""
import argparse
import json
from pathlib import Path

from royal.config import load_config
from royal.export import export_premiere_xml
from royal.logs import get_logger
from royal.render import mix_audio
from royal.sfx import build_track
from royal.style import build_sfx_events


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path, help="the finished .mp4 made by make_video.py")
    ap.add_argument("--music", type=Path)
    ap.add_argument("--no-music", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    log = get_logger("make_video", cfg.path("data_dir"))
    library = cfg.path("library")
    video = args.video.resolve()
    work = video.parent / f"{video.stem}_project"
    tl = json.loads((work / "timeline.json").read_text(encoding="utf-8"))
    segs = json.loads((work / "segments.json").read_text(encoding="utf-8"))
    callouts = json.loads((work / "callouts.json").read_text(encoding="utf-8")) if (work / "callouts.json").exists() else []
    items, duration = tl["items"], tl["duration"]
    vo = Path(tl["voiceover"])
    music = None if args.no_music else (args.music or (Path(tl["music"]) if tl.get("music") else None))

    # timelines made before section flags were stored: mark the first shot of each new section
    if not any(i.get("section_start") for i in items):
        sec_ids = {s["id"] for s in segs if s.get("section_start")}
        seen = set()
        for it in items:
            if it.get("seg") in sec_ids and it["seg"] not in seen:
                seen.add(it["seg"])
                it["section_start"] = True
    hook_end = max([s["end"] for s in segs if s.get("hook")] or [0.0])

    events = build_sfx_events(items, cfg, hook_end, callouts, seed=video.stem)
    stopdowns = [(e["t"], e["t"] + e["dur"]) for e in events if e["kind"] == "stopdown"]
    (work / "sfx.json").write_text(json.dumps(events, indent=0), encoding="utf-8")
    sfx = build_track(events, duration, cfg.get("style", {}).get("sfx", {}).get("levels_db", {}),
                      work / "_sfx.wav", library / "SFX", log)
    log.info("Remix: %d sound effects, %d stop-downs, music: %s", len(events), len(stopdowns),
             music.name if music else "none")

    tmp = video.with_name(video.stem + "_remix_tmp.mp4")
    mix_audio(video, vo, music, tmp, duration, cfg, sfx, hook_end, stopdowns)
    try:
        tmp.replace(video)
    except PermissionError:                        # the old file is open in a player: keep the new one beside it
        alt = video.with_name(video.stem + "_new_sound.mp4")
        tmp.replace(alt)
        log.warning("%s is open in another program - new version saved as %s", video.name, alt.name)
        video = alt

    for i, c in enumerate(callouts):                       # keep the Premiere timeline in sync
        png = work / "callouts" / f"callout_{i:03d}_{c['kind']}.png"
        if png.exists():
            c["png"] = str(png.resolve())
    export_premiere_xml(items, duration, library, vo, music, work / f"{video.stem}.xml", video.stem, cfg,
                        None, 0.0, callouts, sfx)
    log.info("Done: %s (new sound) + %s", video, work / f"{video.stem}.xml")


if __name__ == "__main__":
    main()
