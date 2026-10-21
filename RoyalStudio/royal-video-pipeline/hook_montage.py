r"""Cold-open HOOK MONTAGE from several real clips (commentary / news / raw footage).

    .venv\Scripts\python.exe hook_montage.py --plan projects\x\hook_plan.json --out projects\x\hook_montage.mp4

The plan is a JSON list of segments, played in order:
  {"src": "clip2.mp4", "start": 3.2, "end": 11.0,            video (and audio) from this clip
   "audio_src": "clip2.mp4", "audio_start": 46.8,             optional: audio from another clip/time
   "crop": "X,Y,W,H",                                          cut away other channels' frames / panels
   "fill": "cover" | "blur",                                   blur = portrait clip on a blurred background
   "zoom": 0.06, "text": true, "credit": "CREDIT: TALKTV"}
  {"black": 0.6}                                               a beat of black at the end

Look = the locked intro style (dark cinematic grade, vignette, grain) + bold word-synced text
(white lead-in, big glowing gold key words) + a small navy/gold credit tag on every clip.
Sound = the clips' own audio over a synthesised suspense bed: low drone, heartbeat pulses,
a hit on every cut, a riser into the biggest line and a sub drop into the final black.
"""
import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intro_hook import LOOK, H, W, FPS, blit, build_items, probe_dur, run, tag_img, words_of  # noqa: E402
from royal.sfx import SR, _lowpass, _syn_impact, _syn_riser  # noqa: E402
from royal.fx import CardTemplate, background, place  # noqa: E402

CARD = None
FRAMED = True


CARD2 = None


def framed(frame: np.ndarray, t_global: float, t_seg: float, split: bool = False) -> np.ndarray:
    """Clip as a white-bordered card on the animated navy background (locked design) - not full screen.
    split: the left/right halves of the top of the frame become two cards side by side."""
    global CARD, CARD2
    if CARD is None:
        CARD = CardTemplate(1480, 832, border=8)
        CARD2 = CardTemplate(880, 495, border=7)
    bg = background("navy", W, H, t_global).copy()
    k = min(1.0, t_seg / 0.35)
    if split:
        for i, cx in enumerate((W * 0.265, W * 0.735)):
            kk = min(1.0, max(0.0, (t_seg - 0.12 * i) / 0.35))
            sc = 0.9 + 0.1 * (1 - (1 - kk) ** 3)
            rgb, alpha = CARD2.frame(frame[:540, i * 960:(i + 1) * 960])
            place(bg, rgb, alpha, cx, H * 0.40 - 5 * np.sin(t_global * 0.6 + i), scale=sc)
        return bg
    rgb, alpha = CARD.frame(frame)
    scale = 0.93 + 0.07 * (1 - (1 - k) ** 3)                    # card pops in at every cut
    place(bg, rgb, alpha, W / 2, H * 0.455 - 6 * np.sin(t_global * 0.6), scale=scale)
    return bg


def render_segment(seg: dict, base: Path, out: Path) -> float:
    src = base / seg["src"]
    d = seg["end"] - seg["start"]
    a_src = base / seg.get("audio_src", seg["src"])
    a_start = seg.get("audio_start", seg["start"])
    crop = ""
    if seg.get("crop"):
        x, y, w, h = [int(v) for v in seg["crop"].split(",")]
        crop = f"crop={w}:{h}:{x}:{y},"
    z = seg.get("zoom", 0.05)
    frames = max(int(d * FPS), 1)
    push = (f"zoompan=z='1+{z}*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2.3-ih/zoom/2.3':d=1:s={W}x{H}:fps={FPS}")
    if seg.get("split"):
        (x1, y1, w1, h1), (x2, y2, w2, h2) = [[int(v) for v in c.split(",")] for c in seg["split"]]
        half = "scale=960:540:force_original_aspect_ratio=increase,crop=960:540"
        v = (f"[0:v]trim={seg['start']}:{seg['end']},setpts=PTS-STARTPTS,split[p][q];"
             f"[p]crop={w1}:{h1}:{x1}:{y1},{half}[l];[q]crop={w2}:{h2}:{x2}:{y2},{half}[r];"
             f"[l][r]hstack,pad={W}:{H}:0:0:black,{LOOK}[v]")
    elif seg.get("fill") == "blur":
        v = (f"[0:v]trim={seg['start']}:{seg['end']},setpts=PTS-STARTPTS,{crop}split[s1][s2];"
             f"[s1]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:2,eq=brightness=-0.18[bg];"
             f"[s2]scale=-2:{H}[fg];[bg][fg]overlay=(W-w)/2:0,{push},{LOOK}[v]")
    else:
        v = (f"[0:v]trim={seg['start']}:{seg['end']},setpts=PTS-STARTPTS,{crop}"
             f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{push},{LOOK}[v]")
    a = (f"[1:a]atrim={a_start}:{a_start + d},asetpts=PTS-STARTPTS,aresample=48000,"
         f"aformat=channel_layouts=stereo,loudnorm=I=-15:TP=-1.5,afade=t=in:d=0.08,afade=t=out:st={d - 0.12:.2f}:d=0.12[a]")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(a_src), "-filter_complex", v + ";" + a,
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-c:a", "pcm_s16le", "-t", f"{d:.3f}", str(out)])
    return d


def overlay(src: Path, out: Path, words: list, credit: str, frame_on: bool = False, t0: float = 0.0,
            split: bool = False):
    items = build_items(words) if words else []
    timg = tag_img(credit) if credit else None
    dur = probe_dur(src)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-i", str(src), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest",
                            str(out)], stdin=subprocess.PIPE)
    n, fi = W * H * 3, 0
    from PIL import Image
    while True:
        buf = dec.stdout.read(n)
        if len(buf) < n:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
        t = fi / FPS
        if frame_on:
            frame = framed(frame, t0 + t, t, split)
        if timg is not None:                       # credit: small tag top-left, whole segment
            blit(frame, timg, 60, 56, min(1.0, t / 0.25))
        for rgba, x, y, t_in, t_out in items:
            t_out = min(t_out, dur - 0.05)
            if not (t_in <= t < t_out):
                continue
            k = min(1.0, (t - t_in) / 0.12)
            img = rgba
            if k < 1.0:                            # each word pops in (80% -> 100%)
                s = 0.8 + 0.2 * k
                im = Image.fromarray(rgba, "RGBA")
                img = np.asarray(im.resize((max(1, int(im.width * s)), max(1, int(im.height * s)))))
            dx, dy = (rgba.shape[1] - img.shape[1]) // 2, (rgba.shape[0] - img.shape[0]) // 2
            blit(frame, img, x + dx, y + dy, min(1.0, k * 1.6) * min(1.0, (t_out - t) / 0.1))
        enc.stdin.write(frame.tobytes())
        fi += 1
    enc.stdin.close()
    enc.wait()


def black(out: Path, d: float):
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={d}",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{d}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", str(out)])


def suspense_bed(total: float, cuts: list[float], big: float | None, end_black: float) -> np.ndarray:
    """Low drone + heartbeat + hits on cuts + riser into the big line + sub drop into the black."""
    n = int(SR * (total + 1))
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    lfo = 0.6 + 0.4 * np.sin(2 * np.pi * 0.11 * t)
    drone = (np.sin(2 * np.pi * 55 * t) + 0.6 * np.sin(2 * np.pi * 82.4 * t + 0.3 * np.sin(2 * np.pi * 0.07 * t))
             + 0.35 * np.sin(2 * np.pi * 110.5 * t)) * lfo
    air = _lowpass(rng.standard_normal(n), np.full(n, 420.0)) * 0.9
    bed = (drone * 0.22 + air * 0.25) * np.clip(t / 2.5, 0, 1)            # fades in over 2.5 s
    out = np.stack([bed, np.roll(bed, 90)], 1)

    def add(x, at, gain):
        i = int(at * SR)
        if i < 0 or i >= n:
            return
        m = min(len(x), n - i)
        out[i:i + m] += x[:m] * gain

    beat = _syn_impact(0.5, sub=True)[:, :] * np.linspace(1, 0.2, int(SR * 0.5))[:, None]
    hb = 1.6
    while hb < total - end_black - 0.5:                               # heartbeat: lub-dub every ~1.05 s
        add(beat, hb, 0.55)
        add(beat, hb + 0.24, 0.38)
        hb += 1.05
    for c in cuts:                                                    # a hit on every cut
        add(_syn_impact(1.4), c, 0.55)
    if big is not None:                                               # riser into the key line + big hit on it
        r = _syn_riser(2.2)
        add(r, big - 2.2, 0.32)
        add(_syn_impact(2.4, sub=True), big, 0.9)
        add(_syn_impact(1.6), big, 0.6)
    add(_syn_impact(2.6, sub=True), total - end_black, 1.0)           # drop into the black
    out[int((total - end_black + 0.05) * SR):] *= np.linspace(1, 0, n - int((total - end_black + 0.05) * SR))[:, None]
    out /= max(1e-6, np.abs(out).max()) / 0.9
    return out[: int(SR * total)]


def write_wav(path: Path, x: np.ndarray):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--bed_db", type=float, default=-15.0, help="suspense bed level under the clips")
    ap.add_argument("--full", action="store_true", help="clips full screen (default: framed card on navy)")
    ap.add_argument("--accent", default="gold", help="key-word colour: gold (default) or green, or R,G,B")
    a = ap.parse_args()
    global FRAMED
    FRAMED = not a.full
    import intro_hook
    intro_hook.GOLD = {"gold": (232, 192, 96), "green": (70, 235, 95)}.get(
        a.accent, tuple(int(v) for v in a.accent.split(",")) if "," in a.accent else (232, 192, 96))
    plan = json.loads(a.plan.read_text(encoding="utf-8"))
    base = a.plan.resolve().parent
    tmp = base / "_hook_tmp"
    tmp.mkdir(exist_ok=True)
    parts, t, cuts, big, end_black = [], 0.0, [], None, 0.0
    for i, seg in enumerate(plan):
        if "black" in seg:
            p = tmp / f"seg{i}.mkv"
            black(p, seg["black"])
            parts.append(p)
            end_black = seg["black"]
            t += seg["black"]
            continue
        raw, txt = tmp / f"seg{i}_raw.mkv", tmp / f"seg{i}.mkv"
        d = render_segment(seg, base, raw)
        if seg.get("lines"):                                           # hand-written captions [text, from, to]
            seg["max_words"] = 99                                      # lines are already broken by hand
            words = []
            for text, a0, a1 in seg["lines"]:
                ws = text.split()
                for k, w in enumerate(ws):
                    words.append({"w": w, "s": a0 + (a1 - a0) * k / max(len(ws), 1), "e": a1})
        elif seg.get("caption"):                                       # fixed on-screen text, e.g. "WHAT TICKETS?"
            cw = seg["caption"].split()
            words = [{"w": w, "s": seg.get("caption_at", 0.3) + 0.25 * k, "e": d} for k, w in enumerate(cw)]
            words[-1]["w"] = words[-1]["w"].rstrip(".") + ("" if words[-1]["w"].endswith(("?", "!")) else ".")
        else:
            words = words_of(raw) if seg.get("text", True) else []
        fixes = {"Megan": "Meghan", "megan": "Meghan", **seg.get("fix", {})}   # whisper spelling slips
        for w in words:
            core = w["w"].strip(",.?!")
            if core in fixes:
                w["w"] = w["w"].replace(core, fixes[core])
        run_len = 0                                                    # max 5 words per caption: no overflow
        if seg.get("breaks"):                                          # hand-placed caption breaks win
            for w in words:
                if w["w"].strip(",.?!") in seg["breaks"] and not w["w"].endswith((",", ".", "?", "!")):
                    w["w"] += ","
            seg["max_words"] = 99
        for k, w in enumerate(words):
            run_len += 1
            if w["w"].endswith((",", ".", "?", "!")):
                run_len = 0
            elif run_len >= seg.get("max_words", 5) and k + 1 < len(words):
                w["w"] += ","
                run_len = 0
        print(f"seg{i}: {d:.1f}s  " + " ".join(w["w"] for w in words), flush=True)
        if seg.get("big"):                                            # the hit lands on this word
            hit = next((w["s"] for w in words if seg["big"].lower() in w["w"].lower()), None)
            if hit is not None:
                big = t + hit
        overlay(raw, txt, words, seg.get("credit", ""), frame_on=seg.get("framed", FRAMED), t0=t,
                split=bool(seg.get("split")))
        parts.append(txt)
        if t > 0:
            cuts.append(t)
        t += d
    total = t
    lst = "".join(f"[{k}:v][{k}:a]" for k in range(len(parts)))
    joined = tmp / "joined.mkv"
    run(["ffmpeg", "-v", "error", "-y", *sum((["-i", str(p)] for p in parts), []), "-filter_complex",
         f"{lst}concat=n={len(parts)}:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(joined)])
    bed = tmp / "bed.wav"
    write_wav(bed, suspense_bed(total, cuts, big, end_black))
    g = 10 ** (a.bed_db / 20)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(joined), "-i", str(bed), "-filter_complex",
         f"[1:a]volume={g:.3f}[b];[0:a][b]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", str(a.out)])
    print(f"hook montage {total:.1f}s -> {a.out}  (cuts at {', '.join(f'{c:.1f}' for c in cuts)}; "
          f"big hit at {big:.1f}s)" if big else f"hook montage {total:.1f}s -> {a.out}")


if __name__ == "__main__":
    main()
