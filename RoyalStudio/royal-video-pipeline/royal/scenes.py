"""Split videos into usable shots in ONE decoding pass.

FFmpeg decodes the video once at a few frames per second; cuts are detected
from the colour change between consecutive samples, and the frames needed for
face tagging + CLIP are taken from the same stream. Much faster than seeking
into the file three times per shot.
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .media import FFMPEG, NO_WINDOW


def scan_video(path: Path, src_w: int, src_h: int, sample_fps: float, width: int, threshold: float,
               min_sec: float, max_sec: float, frames_per_shot: int, trim: float = 0.25):
    """Yields (start, end, frames, sharpness) per usable shot. frames = RGB arrays at width x ?"""
    w = min(width, src_w)
    h = int(round(src_h * w / src_w / 2) * 2)
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-an", "-sn",
           "-vf", f"fps={sample_fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    n = w * h * 3
    prev_small = None
    buf: list[tuple[float, np.ndarray]] = []
    shot_start = 0.0
    idx = 0

    def finish(start, end, frames):
        a, b = start + (trim if start > 0 else 0), end - trim
        if b - a < min_sec or not frames:
            return None
        picks = []
        for j in range(frames_per_shot):
            t = a + (b - a) * (j + 1) / (frames_per_shot + 1)
            picks.append(min(frames, key=lambda f: abs(f[0] - t))[1])
        gray = cv2.cvtColor(picks[len(picks) // 2], cv2.COLOR_RGB2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return round(a, 3), round(b, 3), picks, sharp

    try:
        while True:
            raw = proc.stdout.read(n)
            if len(raw) < n:
                break
            t = idx / sample_fps
            idx += 1
            frame = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
            small = cv2.cvtColor(cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2HSV)
            small = small.astype(np.int16)
            cut = False
            if prev_small is not None:
                score = float(np.abs(small - prev_small).mean())
                cut = score > threshold and t - shot_start >= 0.75
            prev_small = small
            if cut or (t - shot_start) >= max_sec:
                res = finish(shot_start, t, buf)
                if res:
                    yield res
                buf, shot_start = [], t
            buf.append((t, frame))
        end = idx / sample_fps
        res = finish(shot_start, end, buf)
        if res:
            yield res
    finally:
        proc.kill()
