"""Designs for on-screen callouts (rendered once as RGBA images, animated per frame).

  name / event   lower third, bottom-left: gold bar + white name + small gold title,
                 on a soft dark panel; slides in from the left, fades out
  date           top-left stamp: big white date with a gold underline
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BOLD = ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]
REG = ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"]
SERIF = ["C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/timesbd.ttf"]
GOLD = (226, 185, 92, 255)
WHITE = (255, 255, 255, 255)


def _font(cands, size):
    for c in cands:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def _shadowed(panel: Image.Image, blur=10, alpha=150) -> Image.Image:
    """Adds a soft drop shadow so text reads on any background."""
    pad = blur * 2
    out = Image.new("RGBA", (panel.width + pad * 2, panel.height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    a = panel.split()[3].point(lambda v: min(alpha, v))
    shadow.paste((0, 0, 0, 255), (pad, pad), a)
    out = Image.alpha_composite(out, shadow.filter(ImageFilter.GaussianBlur(blur)))
    out.alpha_composite(panel, (pad, pad))
    return out


def lower_third(title: str, subtitle: str = "", tag: str = "") -> np.ndarray:
    # LOCKED DESIGN (user approved 2026-10-08): solid navy box, thin white frame, gold left bar,
    # small gold tag (EVENT), white bold title, light-blue subtitle. Do not restyle without asking.
    f_title, f_sub, f_tag = _font(BOLD, 54), _font(REG, 30), _font(BOLD, 22)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(max(d0.textlength(title, font=f_title), d0.textlength(subtitle, font=f_sub) if subtitle else 0,
                d0.textlength(tag, font=f_tag) if tag else 0)) + 70
    h = 20 + (34 if tag else 0) + 64 + (44 if subtitle else 0) + 16
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # solid navy news box with a thin white frame (newsroom style)
    d.rectangle([0, 0, w - 1, h - 1], fill=(16, 34, 92, 238), outline=(235, 238, 245, 255), width=2)
    d.rectangle([0, 0, 8, h], fill=GOLD)                       # gold accent bar
    y = 18
    if tag:
        d.text((32, y), tag.upper(), font=f_tag, fill=GOLD)
        y += 34
    d.text((32, y - 6), title, font=f_title, fill=WHITE)
    y += 64
    if subtitle:
        d.text((34, y - 6), subtitle, font=f_sub, fill=(200, 212, 240, 255))
    return np.asarray(_shadowed(im, blur=8, alpha=90))


def date_stamp(text: str) -> np.ndarray:
    f = _font(SERIF, 64)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(d0.textlength(text, font=f)) + 20
    im = Image.new("RGBA", (w, 100), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((10, 6), text, font=f, fill=WHITE, stroke_width=2, stroke_fill=(0, 0, 0, 160))
    d.rectangle([12, 86, min(w - 10, 12 + 180), 92], fill=GOLD)
    return np.asarray(_shadowed(im, blur=10, alpha=170))


def render_callout(c: dict) -> np.ndarray:
    if c["kind"] == "date":
        return date_stamp(c["title"])
    tag = "EVENT" if c["kind"] == "event" else ""
    return lower_third(c["title"], c.get("subtitle", ""), tag)


class CalloutLayer:
    """Composites callouts onto frames with a 0.35 s slide/fade in and fade out."""

    def __init__(self, callouts: list[dict], W: int, H: int, save_dir: Path | None = None):
        self.items = []
        for i, c in enumerate(callouts):
            rgba = render_callout(c)
            if save_dir is not None:          # PNGs with transparency for the Premiere timeline
                save_dir.mkdir(parents=True, exist_ok=True)
                p = save_dir / f"callout_{i:03d}_{c['kind']}.png"
                canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                x, y = self._pos(c, rgba, W, H)
                canvas.alpha_composite(Image.fromarray(rgba, "RGBA"), (max(x, 0), max(y, 0)))
                canvas.save(p)
                c["png"] = str(p)
            self.items.append((c, rgba[..., :3].astype(np.float32), rgba[..., 3:].astype(np.float32) / 255))
        self.W, self.H = W, H

    @staticmethod
    def _pos(c, rgba, W, H):
        h = rgba.shape[0]
        if c["kind"] == "date":
            return 60, 50
        return 70, H - h - 120

    def apply(self, frame: np.ndarray, t: float) -> np.ndarray:
        out = None
        for c, rgb, alpha in self.items:
            if not (c["start"] <= t < c["end"]):
                continue
            k_in = min(1.0, (t - c["start"]) / 0.35)
            k_out = min(1.0, (c["end"] - t) / 0.35)
            k = max(0.0, min(k_in, k_out))
            ease = 1 - (1 - k_in) ** 3
            x, y = self._pos(c, rgb, self.W, self.H)
            x += int(-60 * (1 - ease)) if c["kind"] != "date" else 0
            h, w = rgb.shape[:2]
            x0, y0 = max(x, 0), max(y, 0)
            x1, y1 = min(x + w, self.W), min(y + h, self.H)
            if x1 <= x0 or y1 <= y0:
                continue
            if out is None:
                out = frame.copy()
            sx, sy = x0 - x, y0 - y
            a = alpha[sy:sy + (y1 - y0), sx:sx + (x1 - x0)] * k
            roi = out[y0:y1, x0:x1].astype(np.float32)
            out[y0:y1, x0:x1] = (roi * (1 - a) + rgb[sy:sy + (y1 - y0), sx:sx + (x1 - x0)] * a).astype(np.uint8)
        return frame if out is None else out
