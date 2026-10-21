r"""Pre-narration HOOK intro for royal videos - LOCKED STYLE (user approved 2026-10-10, Charles private message video).

    .venv\Scripts\python.exe intro_hook.py --clip hook.mp4 --main projects\x\x.mp4 --tag "KING CHARLES III  ·  2022"
        [--crop X,Y,W,H] [--start 0.25] [--end 6.4] [--out projects\x\x_final.mp4]

1. Montage: the clip is cut into 3 framings (wide slow push -> punch-in close-up -> slow push), the
   speech stays continuous. --crop removes another channel's burned-in text / logos / captions.
2. Cinematic dark filter: lower saturation, more contrast, cool shadows, vignette, light film grain,
   fade in from black and out to black. Audio loudness-normalised to -16 LUFS.
3. Kinetic quote text: every word pops in when it is spoken (whisper + speech-onset snap).
   White lead-in line + big glowing royal-gold key words, low on the frame; small navy/gold archive tag.
4. Joined in front of the main video (stream copy, no re-render).
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from royal.transcribe import _cuda_dlls  # noqa: E402

W, H, FPS = 1920, 1080, 30
F = "C:/Windows/Fonts/"
WHITE, GOLD = (255, 255, 255), (232, 192, 96)
LOOK = ("eq=contrast=1.12:saturation=0.78:brightness=-0.03:gamma=0.97,colorbalance=rs=-0.03:bs=0.04:rh=0.03,"
        "vignette=angle=0.55,noise=alls=7:allf=t,setsar=1,fps=30,format=yuv420p")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ffmpeg failed: " + r.stderr[-800:])
    return r


def probe_dur(p):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).stdout)


def make_montage(clip, out, start, end, crop):
    end = min(end or 1e9, probe_dur(clip))
    d = end - start
    a, b = start + d * 0.40, start + d * 0.70
    base = f"crop={crop}," if crop else ""
    fc = (f"[0:v]trim={start}:{a},setpts=PTS-STARTPTS,{base}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"zoompan=z='1+0.0006*on':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps=30,{LOOK}[a];"
          f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS,{base}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"crop=iw/1.25:ih/1.25:(iw-iw/1.25)/2:(ih-ih/1.25)/2*0.6,scale={W}:{H},{LOOK}[b];"
          f"[0:v]trim={b}:{end},setpts=PTS-STARTPTS,{base}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"zoompan=z='1.08+0.0012*on':x='iw/2-iw/zoom/2':y='ih/2.4-ih/zoom/2.4':d=1:s={W}x{H}:fps=30,{LOOK}[c];"
          f"[a][b][c]concat=n=3:v=1:a=0,fade=t=in:st=0:d=0.35,fade=t=out:st={d - 0.4:.2f}:d=0.4[v];"
          f"[0:a]atrim={start}:{end},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
          f"loudnorm=I=-16:TP=-1.5,afade=t=in:d=0.2,afade=t=out:st={d - 0.35:.2f}:d=0.35[au]")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-filter_complex", fc, "-map", "[v]", "-map", "[au]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac",
         "-b:a", "192k", "-ar", "48000", "-ac", "2", "-video_track_timescale", "15360", "-t", f"{d:.3f}", str(out)])


def font(name, size):
    return ImageFont.truetype(F + name, size)


def words_of(path):
    _cuda_dlls()
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    except Exception:
        m = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(str(path), language="en", word_timestamps=True)
    words = [{"w": w.word.strip(), "s": w.start, "e": w.end} for s in segs for w in s.words]
    # whisper sometimes starts a word inside a pause: snap it to the next real speech onset (50 ms energy)
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(pcm, np.int16).astype(np.float64)
    db = np.array([20 * np.log10(np.sqrt((a[i:i + 400] ** 2).mean()) + 1) for i in range(0, len(a) - 400, 400)])
    loud = db >= db.max() - 16
    for w in words:
        i = int(w["s"] / 0.05)
        while i < len(loud) and not loud[i]:
            i += 1
        w["s"] = max(w["s"], i * 0.05)
    for k in range(1, len(words)):
        words[k]["s"] = max(words[k]["s"], words[k - 1]["s"] + 0.06)
    return words


def phrases(words):
    out, cur = [], []
    for k, w in enumerate(words):
        cur.append(w)
        if w["w"].endswith((",", ".", "?", "!")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def layout(ph):
    """white lead-in line + gold key line (last 1-3 words)."""
    n = len(ph)
    k = 1 if n <= 4 else 3
    return ph[: n - k], ph[n - k:]


def draw_word(text, f, color, glow):
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w, h = int(d0.textlength(text, font=f)) + 60, int(f.size * 1.5)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((30, 10), text, font=f, fill=color + (255,), stroke_width=2, stroke_fill=(40, 28, 8, 255) if glow else (0, 0, 0, 255))
    g = im.filter(ImageFilter.GaussianBlur(glow)) if glow else None
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 255), (0, 0), im.split()[3])
    sh = sh.filter(ImageFilter.GaussianBlur(8))
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.alpha_composite(Image.eval(sh, lambda v: int(v * 0.85)))
    if g is not None:
        out.alpha_composite(g)
        out.alpha_composite(g)
    out.alpha_composite(im)
    return out, d0.textlength(text + " ", font=f) - d0.textlength(text, font=f)


def build_items(words):
    items = []                     # (rgba, x, y, t_in, t_out)
    phs = phrases(words)
    for p_i, ph in enumerate(phs):
        t_out = phs[p_i + 1][0]["s"] - 0.05 if p_i + 1 < len(phs) else 99
        lead, key = layout(ph)
        rows = [(lead, font("seguibl.ttf", 74), WHITE, 0, H * 0.70), (key, font("seguibl.ttf", 118), GOLD, 20, H * 0.80)]
        for ws, f, col, glow, y in rows:
            if not ws:
                continue
            if col == WHITE:
                ws = [{**w, "w": w["w"].strip(",.")} for w in ws]
            else:
                ws = [{**w, "w": w["w"].strip(",.").upper()} for w in ws]
            imgs = [draw_word(w["w"], f, col, glow) for w in ws]
            total = sum(im.width - 60 for im, _ in imgs) + sum(sp for _, sp in imgs[:-1])
            x = (W - total) / 2 - 30
            for w, (im, sp) in zip(ws, imgs):
                items.append((np.asarray(im), int(x), int(y - im.height / 2), w["s"], t_out))
                x += im.width - 60 + sp
    return items


def tag_img(TAG):
    f = font("segoeuib.ttf", 30)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(d0.textlength(TAG, font=f)) + 50
    im = Image.new("RGBA", (w, 56), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, 55], fill=(10, 20, 50, 170))
    d.rectangle([0, 0, 6, 55], fill=GOLD + (255,))
    d.text((24, 9), TAG, font=f, fill=(255, 255, 255, 255))
    return np.asarray(im)


def blit(frame, rgba, x, y, a_mul):
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0 or a_mul <= 0:
        return
    src = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = src[..., 3:4].astype(np.float32) / 255 * a_mul
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (roi * (1 - a) + src[..., :3] * a).astype(np.uint8)



def add_text(src, out, tag):
    words = words_of(src)
    print("words:", " ".join(f"{w['w']}@{w['s']:.2f}" for w in words))
    items, timg = build_items(words), tag_img(tag) if tag else None
    dur = probe_dur(src)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-i", str(src), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
                            "-video_track_timescale", "15360", "-shortest", str(out)], stdin=subprocess.PIPE)
    n, fi = W * H * 3, 0
    while True:
        buf = dec.stdout.read(n)
        if len(buf) < n:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
        t = fi / FPS
        fade_all = min(1.0, max(0.0, (dur - 0.15 - t) / 0.35))
        if timg is not None:
            blit(frame, timg, 60, 60, min(1.0, max(0.0, (t - 0.3) / 0.3)) * fade_all)
        for rgba, x, y, t_in, t_out in items:
            if not (t_in <= t < t_out):
                continue
            k = min(1.0, (t - t_in) / 0.14)
            img = rgba
            if k < 1.0:
                s = 0.8 + 0.2 * k
                im = Image.fromarray(rgba, "RGBA")
                img = np.asarray(im.resize((max(1, int(im.width * s)), max(1, int(im.height * s)))))
            dx, dy = (rgba.shape[1] - img.shape[1]) // 2, (rgba.shape[0] - img.shape[0]) // 2
            blit(frame, img, x + dx, y + dy, min(1.0, k * 1.6) * min(1.0, (t_out - t) / 0.12) * fade_all)
        enc.stdin.write(frame.tobytes())
        fi += 1
    enc.stdin.close()
    enc.wait()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip", type=Path, required=True)
    ap.add_argument("--main", type=Path, required=True, help="the rendered narration video")
    ap.add_argument("--tag", default="", help='archive tag, e.g. "KING CHARLES III  ·  2022" ("" = none)')
    ap.add_argument("--crop", default="", help="X,Y,W,H in clip pixels to cut away burned-in text/logos")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=0.0)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    crop = ""
    if a.crop:
        x, y, w, h = [int(v) for v in a.crop.split(",")]
        crop = f"{w}:{h}:{x}:{y}"
    work = a.main.resolve().parent
    mont, txt = work / "intro_montage.mp4", work / "intro_montage_text.mp4"
    make_montage(a.clip, mont, a.start, a.end or None, crop)
    add_text(mont, txt, a.tag)
    out = a.out or a.main.with_name(a.main.stem + "_final.mp4")
    lst = work / "_concat.txt"
    lst.write_text(f"file '{txt.resolve().as_posix()}'\nfile '{a.main.resolve().as_posix()}'\n", encoding="utf-8")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
         "-movflags", "+faststart", str(out)])
    lst.unlink()
    print(f"intro {probe_dur(txt):.1f}s + main -> {out}")


if __name__ == "__main__":
    main()
