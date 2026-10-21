"""File discovery, license metadata, image loading, video probing and frame grabs.

Everything goes through PIL / ffmpeg subprocesses so Unicode file names
(common on Wikimedia) work on Windows.
"""
import hashlib
import io
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 200_000_000
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class MediaFile:
    path: Path
    rel: str             # path relative to library root, forward slashes
    kind: str            # photo | clip | music
    category: str        # People | Events | Clips | Music | Other
    folder: str          # first sub-folder under the category, e.g. King_Charles


def discover(library: Path, cfg_index: dict) -> list[MediaFile]:
    img_ext = set(cfg_index["image_extensions"])
    vid_ext = set(cfg_index["video_extensions"])
    aud_ext = set(cfg_index["audio_extensions"])
    out = []
    for p in sorted(library.rglob("*")):
        if not p.is_file() or p.name.startswith(("_", ".")):
            continue
        ext = p.suffix.lower()
        if ext in img_ext:
            kind = "photo"
        elif ext in vid_ext:
            kind = "clip"
        elif ext in aud_ext:
            kind = "music"
        else:
            continue
        parts = p.relative_to(library).parts
        category = parts[0] if len(parts) > 1 else "Other"
        folder = parts[1] if len(parts) > 2 else ""
        out.append(MediaFile(p, "/".join(parts), kind, category, folder))
    return out


def sha1_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------ metadata
class MetadataStore:
    """Reads _metadata.json files (royal_downloader.py format), keyed by file name."""

    def __init__(self):
        self._dirs: dict[Path, dict] = {}

    def lookup(self, path: Path) -> dict:
        d = path.parent
        if d not in self._dirs:
            by_file = {}
            meta_path = d / "_metadata.json"
            if meta_path.exists():
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                    for title, entry in data.items():
                        name = entry.get("file") or title.replace("File:", "", 1)
                        by_file[name] = entry
                except Exception:
                    pass
            self._dirs[d] = by_file
        return self._dirs[d].get(path.name, {})


def parse_year(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(?<!\d)(18[5-9]\d|19\d\d|20[0-4]\d)(?!\d)", str(text))
    return int(m.group(1)) if m else None


def event_from_folder(folder: str) -> tuple[str | None, int | None]:
    """'Coronation_2023' -> ('Coronation 2023', 2023)."""
    if not folder:
        return None, None
    return folder.replace("_", " "), parse_year(folder)


# ------------------------------------------------------------------ images
def load_image(path: Path, max_side: int) -> tuple[np.ndarray, int, int]:
    """Returns (RGB uint8 array scaled to max_side, original width, original height)."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
        im = im.convert("RGB")
        if max(w, h) > max_side:
            s = max_side / max(w, h)
            im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
        return np.asarray(im), w, h


def orientation_of(w: int, h: int) -> str:
    r = w / max(h, 1)
    return "landscape" if r > 1.1 else "portrait" if r < 0.9 else "square"


def sharpness(rgb: np.ndarray) -> float:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if max(gray.shape) > 800:
        s = 800 / max(gray.shape)
        gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def quality_score(w: int, h: int, sharp: float, faces: list[dict]) -> float:
    """0..1 heuristic: resolution, focus, and a clearly visible face."""
    res = min(1.0, min(w, h) / 1080)
    focus = min(1.0, sharp / 300)
    face = 0.0
    if faces:
        biggest = max(f["bbox"][3] - f["bbox"][1] for f in faces)  # normalized height
        face = min(1.0, biggest / 0.25)
    return round(0.45 * res + 0.35 * focus + 0.20 * face, 3)


def to_jpeg_bytes(rgb: np.ndarray, max_side: int, quality: int = 85) -> bytes:
    im = Image.fromarray(rgb)
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


# ------------------------------------------------------------------ video / audio
def probe(path: Path) -> dict:
    """Duration, width, height, fps via ffprobe."""
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    res = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
    info = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
    out = {"duration": float(info.get("format", {}).get("duration") or 0)}
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and "width" not in out:
            out["width"], out["height"] = int(s["width"]), int(s["height"])
            num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
            out["fps"] = float(num) / float(den or 1) if float(den or 1) else 0.0
    return out


def grab_frame(path: Path, t: float, max_side: int) -> np.ndarray | None:
    """RGB frame at time t (seconds), scaled so the long side <= max_side."""
    vf = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    cmd = [FFMPEG, "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(path),
           "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"]
    res = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    if res.returncode != 0 or not res.stdout:
        return None
    with Image.open(io.BytesIO(res.stdout)) as im:
        return np.asarray(im.convert("RGB"))
