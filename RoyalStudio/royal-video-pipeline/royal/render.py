"""Stage 5a - Render the timeline to a 1920x1080 / 30 fps H.264 MP4.

Frames are produced in Python (cv2) and piped into FFmpeg:
  photos     Ken Burns zoom + drift toward the wanted person's face; portrait
             photos sit on a blurred, darkened copy of themselves
  clips      decoded by FFmpeg, scaled to fill the frame (their audio is never used)
  text cards gold-on-navy title cards for numbers, dates, quotes
  crossfades between every shot; optional burned-in subtitles
Then the voiceover is mixed with background music that ducks under the voice.
"""
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from tqdm import tqdm

from . import fx
from .layouts import LayoutSource
from .media import FFMPEG, NO_WINDOW

FONT_CANDIDATES = ["C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/timesbd.ttf", "C:/Windows/Fonts/arialbd.ttf"]
SUB_FONT = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf"]


def _font(cands, size):
    for c in cands:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def ease(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(max(x, 0.0), 1.0))


# ------------------------------------------------------------------ sources
class PhotoSource:
    def __init__(self, path: Path, item: dict, W: int, H: int, zoom: float, flip: bool, cover: bool = False):
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            src = np.asarray(im)
        self.W, self.H = W, H
        self.t0, self.t1 = item["start"], item["end"]
        sh, sw = src.shape[:2]
        aspect = sw / sh
        self.zoom = (1.0, 1.0 + zoom) if not flip else (1.0 + zoom, 1.0)
        zmax = 1.0 + zoom
        fx, fy = item.get("focus") or [0.5, 0.4]
        if aspect >= 1.45 or cover:              # landscape (or a card box): fill the frame
            base = max(W / sw, H / sh)
            self.bg = np.zeros((H, W, 3), np.uint8)
        else:                                    # portrait/square: fit height on a blurred background
            base = H / sh
            cover = max(W / sw, H / sh)
            bg = cv2.resize(src, (round(sw * cover), round(sh * cover)), interpolation=cv2.INTER_AREA)
            y0, x0 = (bg.shape[0] - H) // 2, (bg.shape[1] - W) // 2
            bg = bg[y0:y0 + H, x0:x0 + W]
            bg = cv2.GaussianBlur(bg, (0, 0), 30)
            self.bg = (bg * 0.45).astype(np.uint8)
        pre = base * zmax                        # pre-scale once so per-frame warps only shrink slightly
        self.src = cv2.resize(src, (max(1, round(sw * pre)), max(1, round(sh * pre))), interpolation=cv2.INTER_AREA)
        self.base_rel = base / pre               # scale factor from pre-scaled image at zoom 1.0
        self.fit = aspect < 1.45 and not cover
        ph, pw = self.src.shape[:2]
        self.center = np.array([pw / 2, ph / 2])
        self.focus = np.array([fx * pw, fy * ph])

    def frame(self, t: float) -> np.ndarray:
        p = ease((t - self.t0) / max(self.t1 - self.t0, 1e-3))
        z = self.zoom[0] + (self.zoom[1] - self.zoom[0]) * p
        s = self.base_rel * z
        ph, pw = self.src.shape[:2]
        # drift from centre toward the face as we zoom in (40% of the way)
        c = self.center + (self.focus - self.center) * 0.4 * (z - 1) / max(self.zoom[1] - 1, self.zoom[0] - 1, 1e-6)
        if not self.fit:
            hw, hh = self.W / (2 * s), self.H / (2 * s)
            c[0] = min(max(c[0], hw), pw - hw)
            c[1] = min(max(c[1], hh), ph - hh)
        else:
            c = self.center + (c - self.center) * 0.3   # fitted portrait: gentle move only
        M = np.array([[s, 0, self.W / 2 - s * c[0]], [0, s, self.H / 2 - s * c[1]]], np.float32)
        out = self.bg.copy()
        cv2.warpAffine(self.src, M, (self.W, self.H), dst=out, flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_TRANSPARENT)
        return out


class ClipSource:
    def __init__(self, path: Path, item: dict, W: int, H: int, fps: int, pad: float,
                 clean: bool = False, edge_crop: float = 0.0):
        self.W, self.H, self.fps = W, H, fps
        self.t_first = item["start"] - pad
        ss = max(item["src_in"] - pad, 0.0)
        dur = (item["src_out"] - item["src_in"]) + 2 * pad
        pre = []
        if clean:     # 3-frame temporal median: removes film scratches/dust that change every frame
            pre.append("tmedian=radius=1")
        if edge_crop:  # trim darkened / burned film borders
            pre.append(f"crop=iw*{1 - 2 * edge_crop:.3f}:ih*{1 - 2 * edge_crop:.3f}")
        vf = ",".join(pre + [f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1"])
        cmd = [FFMPEG, "-v", "error", "-ss", f"{ss:.3f}", "-i", str(path), "-t", f"{dur:.3f}",
               "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self.idx = -1
        self.last = np.zeros((H, W, 3), np.uint8)

    def frame(self, t: float) -> np.ndarray:
        want = int(round((t - self.t_first) * self.fps))
        n = self.W * self.H * 3
        while self.idx < want:
            buf = self.proc.stdout.read(n)
            if len(buf) < n:            # clip ended early: hold last frame
                break
            self.last = np.frombuffer(buf, np.uint8).reshape(self.H, self.W, 3)
            self.idx += 1
        return self.last

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


def make_text_card(text: str, W: int, H: int) -> np.ndarray:
    yy = np.linspace(0, 1, H)[:, None]
    top, bot = np.array([14, 22, 48]), np.array([4, 6, 16])
    bg = (top * (1 - yy) + bot * yy)[:, None, :].repeat(W, 1).reshape(H, W, 3)
    im = Image.fromarray(bg.astype(np.uint8))
    d = ImageDraw.Draw(im)
    words, lines, size = text.split(), [], 120
    font = _font(FONT_CANDIDATES, size)
    while True:                                   # wrap to <= 80% width, shrink if more than 3 lines
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if d.textlength(trial, font=font) > W * 0.8 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
        if len(lines) <= 3 or size <= 60:
            break
        size -= 12
        font = _font(FONT_CANDIDATES, size)
    gold = (226, 185, 92)
    total_h = len(lines) * size * 1.2
    y = H / 2 - total_h / 2
    for ln in lines:
        w = d.textlength(ln, font=font)
        d.text(((W - w) / 2, y), ln, font=font, fill=gold)
        y += size * 1.2
    d.line([(W / 2 - 160, y + 30), (W / 2 + 160, y + 30)], fill=gold, width=4)
    return np.asarray(im)


class TextSource:
    def __init__(self, card: np.ndarray, item: dict):
        self.card, self.t0, self.t1 = card, item["start"], item["end"]
        self.H, self.W = card.shape[:2]

    def frame(self, t: float) -> np.ndarray:
        z = 1.0 + 0.04 * ease((t - self.t0) / max(self.t1 - self.t0, 1e-3))
        M = cv2.getRotationMatrix2D((self.W / 2, self.H / 2), 0, z)
        return cv2.warpAffine(self.card, M, (self.W, self.H), borderMode=cv2.BORDER_REFLECT)


# ------------------------------------------------------------------ subtitles
def subtitle_phrases(words: list[dict], max_words=6, max_sec=2.6) -> list[dict]:
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or w["end"] - cur[0]["start"] > max_sec or w["w"][-1:] in ".!?,;:":
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(x["w"] for x in cur)})
            cur = []
    if cur:
        out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(x["w"] for x in cur)})
    return out


def subtitle_overlay(text: str, W: int, yellow: bool = False) -> tuple[np.ndarray, np.ndarray]:
    font = _font(SUB_FONT, 60 if yellow else 54)
    tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
    tw = int(tmp.textlength(text, font=font)) + 40
    im = Image.new("RGBA", (min(tw, W), 90), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((20, 10), text, font=font, fill=(255, 226, 40, 255) if yellow else (255, 255, 255, 255),
           stroke_width=5 if yellow else 4, stroke_fill=(0, 0, 0, 255))
    a = np.asarray(im)
    return a[..., :3].astype(np.float32), a[..., 3:].astype(np.float32) / 255


# ------------------------------------------------------------------ main render
def render_video(items, duration, library: Path, out_path: Path, cfg: dict, log,
                 words=None, subtitles=False, card_dir: Path | None = None,
                 callouts: list | None = None, callout_dir: Path | None = None,
                 hook_captions_until: float = 0.0) -> None:
    rc, oc = cfg["render"], cfg["output"]
    W, H, fps = oc["width"], oc["height"], oc["fps"]
    xf = rc["crossfade_sec"]
    n_frames = int(math.ceil(duration * fps))
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", rc["x264_preset"], "-crf", str(rc["crf"]),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=NO_WINDOW)

    subs = []
    if subtitles and words:
        for ph in subtitle_phrases(words):
            rgb, alpha = subtitle_overlay(ph["text"], W)
            subs.append((ph["start"], ph["end"], rgb, alpha))
    elif hook_captions_until and words:           # bold yellow 2-3 word captions during the hook only
        hook_words = [w for w in words if w["start"] < hook_captions_until]
        for ph in subtitle_phrases(hook_words, max_words=3, max_sec=1.4):
            rgb, alpha = subtitle_overlay(ph["text"].upper(), W, yellow=True)
            subs.append((ph["start"], ph["end"], rgb, alpha))

    open_src: dict[int, object] = {}
    layer = None
    if callouts:
        from .overlays import CalloutLayer
        layer = CalloutLayer(callouts, W, H, callout_dir)

    def pad_of(i):  # only crossfades need the next shot early; cut / flash / leak switch on the boundary
        nxt = items[i + 1].get("transition", "fade") if i + 1 < len(items) else "cut"
        return xf / 2 if nxt == "fade" else 0.0

    FLASH_IN, FLASH_OUT, LEAK_IN, LEAK_OUT = 0.07, 0.22, 0.30, 0.38

    def source(i):
        if i not in open_src:
            it = items[i]
            try:
                if it["kind"] in ("photo", "clip"):
                    def make(src_it, w, h, cover, _i=i):
                        if src_it["kind"] == "photo":
                            return PhotoSource(library / src_it["path"], src_it, w, h, rc["ken_burns_zoom"],
                                               flip=_i % 3 == 2, cover=cover)
                        return ClipSource(library / src_it["path"], src_it, w, h, fps, xf,
                                          rc.get("clean_film_scratches", False), rc.get("clip_edge_crop", 0.0))
                    open_src[i] = LayoutSource(it, library, W, H, make)
                else:
                    card = make_text_card(it["text"], W, H)
                    if card_dir is not None:
                        card_dir.mkdir(parents=True, exist_ok=True)
                        p = card_dir / f"card_{i:04d}.png"
                        Image.fromarray(card).save(p)
                        it["card_png"] = str(p)
                    open_src[i] = TextSource(card, it)
            except Exception as e:  # unreadable asset: show a title card instead of crashing
                log.warning("  could not load %s (%s) - using a title card", it.get("path"), e)
                open_src[i] = TextSource(make_text_card(" ", W, H), it)
        return open_src[i]

    cur, si = 0, 0
    try:
        for f in tqdm(range(n_frames), desc="Rendering", unit="frame", mininterval=2):
            t = f / fps
            while cur + 1 < len(items) and t >= items[cur]["end"] + pad_of(cur):
                s = open_src.pop(cur, None)
                if hasattr(s, "close"):
                    s.close()
                cur += 1
            frame = source(cur).frame(t)
            nxt = cur + 1
            if nxt < len(items):                       # transition OUT of this shot
                boundary = items[cur]["end"]
                tr = items[nxt].get("transition", "fade")
                if tr == "fade" and t >= boundary - xf / 2:
                    a = (t - (boundary - xf / 2)) / xf
                    other = source(nxt).frame(t)
                    frame = cv2.addWeighted(frame, 1 - a, other, a, 0)
                elif tr == "flash" and t >= boundary - FLASH_IN:
                    frame = fx.flash(frame, (t - (boundary - FLASH_IN)) / FLASH_IN)
                elif tr == "leak" and t >= boundary - LEAK_IN:
                    k = (t - (boundary - LEAK_IN)) / LEAK_IN
                    frame = fx.light_leak(frame, k, 0.5 * k)
            tr_in = items[cur].get("transition", "fade")    # transition INTO this shot
            since = t - items[cur]["start"]
            if tr_in == "flash" and 0 <= since < FLASH_OUT:
                frame = fx.flash(frame, 1 - since / FLASH_OUT)
            elif tr_in == "leak" and 0 <= since < LEAK_OUT:
                k = 1 - since / LEAK_OUT
                frame = fx.light_leak(frame, k, 0.5 + 0.5 * (1 - k))
            if subs:
                while si < len(subs) and subs[si][1] < t:
                    si += 1
                if si < len(subs) and subs[si][0] <= t:
                    _, _, rgb, alpha = subs[si]
                    h, w = rgb.shape[:2]
                    y0, x0 = H - 140, (W - w) // 2
                    roi = frame[y0:y0 + h, x0:x0 + w].astype(np.float32)
                    frame = frame.copy()
                    frame[y0:y0 + h, x0:x0 + w] = (roi * (1 - alpha) + rgb * alpha).astype(np.uint8)
            if layer is not None:
                frame = layer.apply(frame, t)
            enc.stdin.write(np.ascontiguousarray(frame).tobytes())
    finally:
        for s in open_src.values():
            if hasattr(s, "close"):
                s.close()
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError("FFmpeg video encode failed")


def mix_audio(video: Path, vo: Path, music: Path | None, out: Path, duration: float, cfg: dict,
              sfx: Path | None = None, hook_end: float = 0.0, stopdowns: list | None = None) -> None:
    mc = cfg["music"]
    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(video), "-i", str(vo)]
    chains, mix = ["[1:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[vo][key]"], ["[vo]"]
    n_in = 2
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fade_out = max(duration - 3, 0)
        boost = 10 ** (mc.get("hook_boost_db", 0) / 20)          # music sits higher under the hook, then settles
        # stop-downs: the music drops out for ~0.5 s before a new section (dramatic pause, like the reference)
        dips = ""
        if stopdowns:
            cond = "+".join(f"between(t,{x:.2f},{y:.2f})" for x, y in stopdowns[:60])
            dips = f"volume='if({cond},0.06,1)':eval=frame,"
        chains.append(f"[{n_in}:a]aresample=48000,volume={mc['volume_db']}dB,"
                      f"volume='if(lt(t,{hook_end:.2f}),{boost:.3f},1)':eval=frame,{dips}afade=t=in:d=2,"
                      f"afade=t=out:st={fade_out:.2f}:d=3[m]")
        chains.append("[m][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=500[duck]")
        mix.append("[duck]")
        n_in += 1
    else:
        chains[0] = "[1:a]aresample=48000,aformat=channel_layouts=stereo[vo]"
    if sfx:
        cmd += ["-i", str(sfx)]
        chains.append(f"[{n_in}:a]aresample=48000[fx]")
        mix.append("[fx]")
    if len(mix) == 1:
        chains[-1] = chains[-1].replace("[vo]", "[a]") if not music else chains[-1]
    else:
        chains.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,"
                      f"alimiter=limit=0.95:level=disabled[a]")   # SFX peaks never clip the voice
    cmd += ["-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[a]"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{duration:.3f}", "-video_track_timescale", "15360",
            "-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if res.returncode != 0:
        raise RuntimeError("FFmpeg audio mix failed: " + res.stderr.decode("utf-8", "replace")[-800:])


def prepend_intro(intro: Path, main: Path, out: Path, cfg: dict, work: Path) -> float:
    """Put a real clip (e.g. a news headline) before the video, with its own sound. Returns its length."""
    from .media import FFPROBE, probe
    oc, rc = cfg["output"], cfg["render"]
    W, H, fps = oc["width"], oc["height"], oc["fps"]
    info = probe(intro)
    dur = info.get("duration", 0.0)
    norm = work / "_intro.mp4"
    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1"
    has_audio = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                                "-show_entries", "stream=index", "-of", "csv=p=0", str(intro)],
                               capture_output=True, creationflags=NO_WINDOW).stdout.strip()
    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(intro)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a", "-t", f"{dur:.3f}", "-vf", vf,
            "-c:v", "libx264", "-preset", rc["x264_preset"], "-crf", str(rc["crf"]), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-video_track_timescale", "15360", str(norm)]
    if subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW).returncode != 0:
        raise RuntimeError(f"Could not convert intro clip {intro}")
    lst = work / "_concat.txt"
    lst.write_text(f"file '{norm.as_posix()}'\nfile '{main.as_posix()}'\n", encoding="utf-8")
    res = subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                          "-c", "copy", "-movflags", "+faststart", str(out)], capture_output=True, creationflags=NO_WINDOW)
    if res.returncode != 0:
        raise RuntimeError("Joining intro failed: " + res.stderr.decode("utf-8", "replace")[-500:])
    real = float(probe(norm).get("duration") or dur)
    norm.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    return real
