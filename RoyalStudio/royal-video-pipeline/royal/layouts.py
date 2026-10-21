"""Frame layouts around the basic photo/clip sources.

  full      the media fills the frame (existing behaviour)
  card      media in a white-bordered (or rounded) card on an animated background;
            enters tilted + slightly small, settles straight, then drifts in slowly
  split     two cards side by side (e.g. "Samantha ... Meghan"), sliding in from the sides
  collage   three polaroid photos dropping in one after another over a blurred/particle background
"""
from pathlib import Path

import numpy as np

from . import fx


def _box_for(item, W, H, max_w=0.74, max_h=0.80):
    """Inset size that keeps the media's aspect ratio (landscape -> wide card, portrait -> tall card)."""
    w0, h0 = item.get("width") or 16, item.get("height") or 9
    if item.get("kind") == "clip":
        w0, h0 = 16, 9                                     # clips are scaled to 16:9 by the decoder
    a = w0 / h0
    if a >= 1.2:
        bw = int(W * max_w)
        bh = int(min(bw / a, H * max_h))
        bw = int(bh * a) if bh == int(H * max_h) else bw
    else:
        bh = int(H * max_h)
        bw = int(max(bh * a, W * 0.34))
    return bw - bw % 2, bh - bh % 2


class LayoutSource:
    def __init__(self, item: dict, library: Path, W: int, H: int, make_source):
        """make_source(item_like, w, h, cover) -> object with .frame(t) (and optional .close())."""
        self.it, self.W, self.H = item, W, H
        self.layout = item.get("layout", "full")
        self.bg = item.get("bg", "particles")
        self.t0, self.t1 = item["start"], item["end"]
        self.subs = []
        if self.layout == "full":
            self.main = make_source(item, W, H, False)
        elif self.layout == "card":
            bw, bh = _box_for(item, W, H)
            self.main = make_source(item, bw, bh, True)
            self.tpl = fx.CardTemplate(bw, bh, border=max(8, W // 190), rounded=item.get("rounded", False))
        elif self.layout == "split":
            bw, bh = int(W * 0.40) & ~1, int(H * 0.62) & ~1
            self.main = make_source(item, bw, bh, True)
            partner = {**item, **item["partner"], "kind": "photo", "start": item["start"], "end": item["end"]}
            self.other = make_source(partner, bw, bh, True)
            self.tpl = fx.CardTemplate(bw, bh, border=max(8, W // 190))
            self.subs.append(self.other)
        elif self.layout == "collage":
            bw, bh = int(W * 0.30) & ~1, int(H * 0.40) & ~1
            self.main = make_source(item, bw, bh, True)
            self.others = [make_source({**item, **p, "kind": "photo"}, bw, bh, True) for p in item["partners"][:2]]
            self.subs += self.others
            self.tpl = fx.CardTemplate(bw, bh, border=max(10, W // 140), polaroid=True)
        self.subs.append(self.main)

    def _media(self, src, t):
        m = src.frame(t)
        return fx.bw(m) if self.it.get("bw") else m

    def frame(self, t: float) -> np.ndarray:
        W, H = self.W, self.H
        p = t - self.t0
        dur = max(self.t1 - self.t0, 0.5)
        if self.layout == "full":
            return self._media(self.main, t)

        m = self._media(self.main, t)
        if self.layout == "card":
            out = fx.background(self.bg, W, H, t, media=m)
            k = fx.ease_out(p / 0.55)
            rot = self.it.get("rot0", 0) * (1 - k)
            scale = (0.9 + 0.1 * k) * (1 + 0.035 * min(max(p / dur, 0), 1))
            rgb, a = self.tpl.frame(m)
            return fx.place(out, rgb, a, W / 2, H / 2, scale, rot)

        if self.layout == "split":
            out = fx.background(self.bg, W, H, t)
            o = self._media(self.other, t)
            k = fx.ease_out(p / 0.5)
            drift = 1 + 0.02 * min(max(p / dur, 0), 1)
            for media, side, r in ((m, -1, -2.0), (o, 1, 2.0)):
                rgb, a = self.tpl.frame(media)
                cx = W / 2 + side * (W * 0.235 + (1 - k) * W * 0.35)
                fx.place(out, rgb, a, cx, H * 0.5, drift, r * (0.5 + 0.5 * (1 - k)))
            return out

        # collage
        out = fx.background("blur" if self.bg == "blur" else self.bg, W, H, t, media=m)
        spots = [(0.27, 0.37, -6), (0.73, 0.35, 5), (0.50, 0.68, -2)]
        medias = [m] + [self._media(s, t) for s in self.others]
        zoom = 1 + 0.04 * min(max(p / dur, 0), 1)
        for n, (media, (x, y, r)) in enumerate(zip(medias, spots)):
            k = fx.ease_out((p - 0.28 * n) / 0.45)
            if k <= 0:
                continue
            rgb, a = self.tpl.frame(media)
            fx.place(out, rgb, a, W * x, H * y + (1 - k) * H * 0.7, zoom, r + (1 - k) * 8)
        return out

    def close(self):
        for s in self.subs:
            if hasattr(s, "close"):
                s.close()
