"""Stage 5b - Editable timeline for Premiere Pro (FCP7 XML) + credits.txt.

Premiere: File > Import > select the .xml. You get a sequence with
  V1  every photo / clip / text card at its time (photos scaled to fill the frame,
      with a slow zoom keyframed so it already moves like the render)
  A1  the voiceover        A2  the background music (if any)
"""
import html
import re
import json
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

from .media import probe


def _url(p: Path) -> str:
    return "file://localhost/" + quote(Path(p).resolve().as_posix(), safe="/:")


def _rate(fps):
    return f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"


def export_premiere_xml(items, duration, library: Path, vo: Path, music: Path | None,
                        out: Path, name: str, cfg: dict, intro: Path | None = None, intro_dur: float = 0.0,
                        callouts: list | None = None, sfx: Path | None = None) -> None:
    W, H, fps = cfg["output"]["width"], cfg["output"]["height"], cfg["output"]["fps"]
    zoom = cfg["render"]["ken_burns_zoom"]
    fr = lambda t: int(round(t * fps))  # noqa: E731
    off = fr(intro_dur) if intro else 0       # everything starts after the intro clip
    total = fr(duration) + off
    files: "OrderedDict[str, str]" = OrderedDict()
    probes: dict[str, dict] = {}

    def file_el(path: Path, kind: str, dur_frames: int, w=None, h=None, audio=False) -> str:
        key = str(path)
        if key in files:
            return f'<file id="{files[key]}"/>'
        fid = f"file-{len(files) + 1}"
        files[key] = fid
        media = (f"<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics>"
                 f"<channelcount>2</channelcount></audio>") if audio else (
            f"<video><samplecharacteristics>{_rate(fps)}<width>{w or W}</width><height>{h or H}</height>"
            f"<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>")
        return (f'<file id="{fid}"><name>{html.escape(path.name)}</name><pathurl>{_url(path)}</pathurl>'
                f"{_rate(fps)}<duration>{dur_frames}</duration><media>{media}</media></file>")

    clips = []
    for n, it in enumerate(items, 1):
        start, end = fr(it["start"]) + off, fr(it["end"]) + off
        if end <= start:
            continue
        motion = ""
        if it["kind"] == "clip":
            path = library / it["path"]
            if str(path) not in probes:
                probes[str(path)] = probe(path)
            pr = probes[str(path)]
            src_dur = fr(pr.get("duration", it["src_out"] + 10))
            fin, fout = fr(it["src_in"]), fr(it["src_in"]) + (end - start)
            fel = file_el(path, "clip", src_dur, pr.get("width"), pr.get("height"))
            cover = 100 * max(W / pr.get("width", W), H / pr.get("height", H))
            motion = _motion(cover, cover, end - start)
        else:
            path = library / it["path"] if it["kind"] == "photo" else Path(it.get("card_png", ""))
            if it["kind"] == "text" and not it.get("card_png"):
                continue
            src_dur = 10 * 60 * fps
            fin, fout = 0, end - start
            w, h = (it.get("width") or W, it.get("height") or H) if it["kind"] == "photo" else (W, H)
            fel = file_el(path, "still", src_dur, w, h)
            if it["kind"] == "photo":
                cover = 100 * max(W / w, H / h) if w / h >= 1.45 else 100 * H / h
                motion = _motion(cover, cover * (1 + zoom), end - start)
        clips.append(
            f'<clipitem id="clipitem-{n}"><name>{html.escape(path.name)}</name><enabled>TRUE</enabled>'
            f"<duration>{src_dur}</duration>{_rate(fps)}<start>{start}</start><end>{end}</end>"
            f"<in>{fin}</in><out>{fout}</out>{fel}{motion}</clipitem>")

    def audio_track(path: Path, dur_s: float, level_db: float | None = None, at: int = 0, length: float | None = None) -> str:
        fel = file_el(path, "audio", fr(dur_s), audio=True)
        n = fr(min(dur_s, length if length is not None else duration))
        vol = ""
        if level_db is not None:
            gain = 10 ** (level_db / 20)
            vol = ("<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid>"
                   "<effectcategory>audiolevels</effectcategory><effecttype>audiolevels</effecttype>"
                   "<mediatype>audio</mediatype><parameter><parameterid>level</parameterid><name>Level</name>"
                   f"<valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{gain:.4f}</value></parameter>"
                   "</effect></filter>")
        cid = re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)      # XML ids can't contain '&', spaces, ...
        return (f'<track><clipitem id="audio-{cid}"><name>{html.escape(path.name)}</name><enabled>TRUE</enabled>'
                f"<duration>{fr(dur_s)}</duration>{_rate(fps)}<start>{at}</start><end>{at + n}</end><in>0</in><out>{n}</out>"
                f"{fel}<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>{vol}"
                f"</clipitem></track>")

    audio = audio_track(vo, duration, at=off)
    if music:
        audio += audio_track(music, probe(music).get("duration", duration), cfg["music"]["volume_db"], at=off)
    if sfx and Path(sfx).exists():          # sound effects on their own track so the editor can tweak them
        audio += audio_track(Path(sfx), duration, None, at=off)
    if intro:
        pr = probe(intro)
        w, h = pr.get("width", W), pr.get("height", H)
        fit = 100 * min(W / w, H / h)
        clips.insert(0, f'<clipitem id="clipitem-intro"><name>{html.escape(intro.name)}</name><enabled>TRUE</enabled>'
                     f"<duration>{off}</duration>{_rate(fps)}<start>0</start><end>{off}</end><in>0</in><out>{off}</out>"
                     f"{file_el(intro, 'clip', off, w, h)}{_motion(fit, fit, off)}</clipitem>")
        audio += audio_track(intro, intro_dur, at=0, length=intro_dur)

    v2 = ""
    if callouts:            # names / dates / events as transparent PNGs on track V2
        items2 = []
        for k, c in enumerate(callouts):
            if not c.get("png"):
                continue
            a, b = fr(c["start"]) + off, fr(c["end"]) + off
            png = Path(c["png"])
            items2.append(f'<clipitem id="callout-{k}"><name>{html.escape(png.name)}</name><enabled>TRUE</enabled>'
                          f"<duration>{10 * 60 * fps}</duration>{_rate(fps)}<start>{a}</start><end>{b}</end>"
                          f"<in>0</in><out>{b - a}</out>{file_el(png, 'still', 10 * 60 * fps, W, H)}</clipitem>")
        v2 = f"<track>{''.join(items2)}</track>"

    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
           f'<sequence id="sequence-1"><name>{html.escape(name)}</name><duration>{total}</duration>{_rate(fps)}'
           f"<media><video><format><samplecharacteristics>{_rate(fps)}<width>{W}</width><height>{H}</height>"
           f"<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio>"
           f"<fielddominance>none</fielddominance></samplecharacteristics></format>"
           f"<track>{''.join(clips)}</track>{v2}</video><audio>{audio}</audio></media></sequence></xmeml>\n")
    out.write_text(xml, encoding="utf-8")


def _motion(scale_from: float, scale_to: float, n_frames: int) -> str:
    keys = (f"<keyframe><when>0</when><value>{scale_from:.2f}</value></keyframe>"
            f"<keyframe><when>{n_frames}</when><value>{scale_to:.2f}</value></keyframe>") \
        if abs(scale_to - scale_from) > 0.01 else ""
    return ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
            "<effecttype>motion</effecttype><mediatype>video</mediatype>"
            f"<parameter><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin>"
            f"<valuemax>1000</valuemax><value>{scale_from:.2f}</value>{keys}</parameter></effect></filter>")


def write_credits(items, path: Path) -> None:
    used = OrderedDict()
    for it in items:
        if it["kind"] not in ("photo", "clip"):
            continue
        shown = [it] + ([it["partner"]] if it.get("layout") == "split" and it.get("partner") else []) \
            + (it.get("partners", [])[:2] if it.get("layout") == "collage" else [])
        for x in shown:                       # split screens / collages show extra photos too
            if x["path"] not in used:
                used[x["path"]] = x
    groups = {"attribution": [], "pd": [], "unknown": []}
    for it in used.values():
        lic = (it.get("license") or "").strip()
        if not lic:
            groups["unknown"].append(it)
        elif "public domain" in lic.lower() or lic.upper().startswith(("PD", "CC0")):
            groups["pd"].append(it)
        else:
            groups["attribution"].append(it)

    def line(it):
        title = Path(it["path"]).stem
        author = (it.get("author") or "Unknown author").replace("\n", " ").strip()
        src = it.get("source_page") or ""
        return f'- "{title}" by {author}, {it.get("license")}' + (f" - {src}" if src else "")

    out = ["IMAGE CREDITS", "(paste into the YouTube description)", ""]
    if groups["attribution"]:
        out += ["Licensed images (Creative Commons / Open Government Licence):"]
        out += [line(it) for it in groups["attribution"]] + [""]
    if groups["pd"]:
        out += ["Public domain images:"] + [line(it) for it in groups["pd"]] + [""]
    if groups["unknown"]:
        out += ["", "!! NOT FOR THE DESCRIPTION - CHECK RIGHTS BEFORE PUBLISHING !!",
                "These assets have no license info (no _metadata.json entry):"]
        out += [f"- {it['path']}" for it in groups["unknown"]]
    path.write_text("\n".join(out), encoding="utf-8")


def save_timeline(items, duration, path: Path, extra: dict) -> None:
    path.write_text(json.dumps({"duration": duration, **extra, "items": items}, indent=1, ensure_ascii=False),
                    encoding="utf-8")
