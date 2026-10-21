"""Visual building blocks for the "newsroom documentary" style (all generated in code, no stock assets).

Backgrounds   blur (from the shot itself), particles (dark teal data streaks), grid (light grey
              diamond pattern), navy (stars + red stripe)
Cards         media inside a white border with a soft shadow; enters tilted and settles
Polaroids     photos with a thick bottom border for collages
Light leak    orange/red flare that sweeps across a cut
"""
import math

import cv2
import numpy as np

_RNG = np.random.default_rng(7)
_cache: dict = {}


def ease_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


# ------------------------------------------------------------------ backgrounds
def _particles(W, H):
    key = ("particles", W, H)
    if key in _cache:
        return _cache[key]
    w, h = int(W * 1.6), int(H * 1.6)
    yy = np.linspace(0, 1, h)[:, None, None]
    img = ((np.array([10, 24, 30]) * (1 - yy) + np.array([0, 4, 8]) * yy) * np.ones((1, w, 1))).astype(np.float32)
    layer = np.zeros((h, w, 3), np.float32)
    ang = math.radians(-35)
    dx, dy = math.cos(ang), math.sin(ang)
    for _ in range(2600):                                   # light streaks along one diagonal
        x, y = _RNG.uniform(0, w), _RNG.uniform(0, h)
        ln = _RNG.uniform(8, 140)
        b = _RNG.uniform(0.15, 1.0)
        col = (np.array([60, 200, 215]) * b).tolist()
        cv2.line(layer, (int(x), int(y)), (int(x + dx * ln), int(y + dy * ln)), col, 1, cv2.LINE_AA)
    for _ in range(2500):                                   # dust dots
        x, y = int(_RNG.uniform(0, w)), int(_RNG.uniform(0, h))
        cv2.circle(layer, (x, y), 1, (np.array([90, 220, 230]) * _RNG.uniform(0.2, 1)).tolist(), -1, cv2.LINE_AA)
    layer = cv2.GaussianBlur(layer, (0, 0), 0.8) + 0.6 * cv2.GaussianBlur(layer, (0, 0), 4)
    out = np.clip(img + layer, 0, 255).astype(np.uint8)
    _cache[key] = out
    return out


def _grid(W, H):
    key = ("grid", W, H)
    if key in _cache:
        return _cache[key]
    w, h = int(W * 1.2), int(H * 1.2)
    img = np.full((h, w, 3), 226, np.uint8)
    s = 260
    for i, cx in enumerate(range(-s, w + s, s)):
        for j, cy in enumerate(range(-s, h + s, s // 2)):
            ox = cx + (s // 2 if j % 2 else 0)
            pts = np.array([[ox, cy - s // 2], [ox + s // 2, cy], [ox, cy + s // 2], [ox - s // 2, cy]], np.int32)
            shade = 236 if (i + j) % 3 == 0 else 218 if (i + j) % 3 == 1 else 228
            cv2.fillConvexPoly(img, pts, (shade, shade, shade + 4))
            cv2.polylines(img, [pts], True, (245, 245, 248), 2, cv2.LINE_AA)
    for _ in range(8):                                      # a few thin diagonal "scratches"
        x = int(_RNG.uniform(0, w))
        cv2.line(img, (x, 0), (x - int(h * 0.6), h), (250, 250, 252), 1, cv2.LINE_AA)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    _cache[key] = img
    return img


def _navy(W, H):
    # LOCKED DESIGN (user approved 2026-10-08): the only background for cards, splits and collages.
    # Navy gradient, small light crosses in side columns, red stripe with white inner stripe bottom-right.
    key = ("navy", W, H)
    if key in _cache:
        return _cache[key]
    w, h = int(W * 1.15), int(H * 1.15)
    yy = np.linspace(0, 1, h)[:, None, None]
    img = ((np.array([18, 30, 78]) * (1 - yy) + np.array([6, 12, 38]) * yy) * np.ones((1, w, 1))).astype(np.uint8)
    for x in list(range(30, int(w * 0.22), 46)) + list(range(int(w * 0.78), w - 20, 46)):   # star columns
        for y in range(30, h - 20, 46):
            c = (x + (23 if (y // 46) % 2 else 0), y)
            cv2.drawMarker(img, c, (150, 160, 200), cv2.MARKER_TILTED_CROSS, 9, 1, cv2.LINE_AA)
    pts = np.array([[int(w * 0.55), h], [w, int(h * 0.72)], [w, int(h * 0.80)], [int(w * 0.68), h]], np.int32)
    cv2.fillConvexPoly(img, pts, (200, 25, 45))             # red stripe, bottom right
    pts2 = np.array([[int(w * 0.62), h], [w, int(h * 0.80)], [w, int(h * 0.83)], [int(w * 0.67), h]], np.int32)
    cv2.fillConvexPoly(img, pts2, (240, 240, 245))          # thin white stripe inside it
    _cache[key] = img
    return img


def background(kind: str, W: int, H: int, t: float, media: np.ndarray | None = None) -> np.ndarray:
    """Full-frame background at time t. 'blur' uses the current media frame."""
    if kind == "blur" and media is not None:
        small = cv2.resize(media, (192, int(192 * media.shape[0] / media.shape[1]) or 108), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 6)
        h0, w0 = small.shape[:2]
        sc = max(W / w0, H / h0) * 1.15
        big = cv2.resize(small, (int(w0 * sc) + 1, int(h0 * sc) + 1), interpolation=cv2.INTER_LINEAR)
        y0, x0 = (big.shape[0] - H) // 2, (big.shape[1] - W) // 2
        return cv2.convertScaleAbs(big[y0:y0 + H, x0:x0 + W], alpha=0.55)
    tex = {"particles": _particles, "grid": _grid, "navy": _navy}.get(kind, _particles)(W, H)
    th, tw = tex.shape[:2]
    speed = 26 if kind == "particles" else 8            # px per second drift
    off = (t * speed) % max(tw - W, 1)
    x0 = int(off)
    y0 = int((t * speed * 0.6) % max(th - H, 1)) if kind == "particles" else (th - H) // 2
    return tex[y0:y0 + H, x0:x0 + W].copy()


# ------------------------------------------------------------------ cards
class CardTemplate:
    """White-bordered (or rounded) card with a baked soft shadow, as RGB + alpha, size known up front."""

    def __init__(self, w: int, h: int, border: int = 10, rounded: bool = False, polaroid: bool = False):
        self.w, self.h = w, h
        bt = border
        bb = border * 4 if polaroid else border
        if rounded:
            bt = bb = 0
        self.bt, self.bb = bt, bb
        pad = 40
        self.pad = pad
        cw, ch = w + 2 * bt, h + bt + bb
        H2, W2 = ch + 2 * pad, cw + 2 * pad
        alpha = np.zeros((H2, W2), np.float32)
        card_mask = np.zeros((H2, W2), np.uint8)
        if rounded:
            r = int(min(w, h) * 0.08)
            x0, y0, x1, y1 = pad, pad, pad + cw - 1, pad + ch - 1
            cv2.rectangle(card_mask, (x0 + r, y0), (x1 - r, y1), 255, -1)
            cv2.rectangle(card_mask, (x0, y0 + r), (x1, y1 - r), 255, -1)
            for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
                cv2.circle(card_mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
        else:
            card_mask[pad:pad + ch, pad:pad + cw] = 255
        shadow = cv2.GaussianBlur(np.roll(np.roll(card_mask, 10, 0), 6, 1).astype(np.float32) / 255, (0, 0), 14) * 0.55
        cm = card_mask.astype(np.float32) / 255
        alpha = np.maximum(cm, shadow)
        rgb = np.zeros((H2, W2, 3), np.float32)
        rgb[cm > 0] = 255                                       # white border
        # where only shadow shows, colour is black; premultiply-friendly: keep rgb, alpha separately
        self.rgb = rgb.astype(np.uint8)                         # uint8: cheap per-frame copies
        self.alpha = alpha
        self.card_mask = cm
        self.inside = cm > 0.5
        self.media_box = (pad + bt, pad + bt, w, h)             # x, y, w, h inside the template

    def frame(self, media: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rgb = self.rgb.copy()
        x, y, w, h = self.media_box
        m = media if media.shape[:2] == (h, w) else cv2.resize(media, (w, h), interpolation=cv2.INTER_AREA)
        rgb[y:y + h, x:x + w] = m              # rounded corners come from the alpha mask, no per-pixel work
        return rgb, self.alpha


def place(dst: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, cx: float, cy: float,
          scale: float = 1.0, rot: float = 0.0) -> np.ndarray:
    """Composite an RGBA sprite onto dst, centred at (cx, cy), scaled and rotated (degrees)."""
    H, W = dst.shape[:2]
    h, w = alpha.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rot, scale)
    M[0, 2] += cx - w / 2
    M[1, 2] += cy - h / 2
    # only warp the screen area the sprite can cover (much faster than a full-frame warp)
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float32) @ M.T
    x0, y0 = max(int(corners[:, 0].min()) - 1, 0), max(int(corners[:, 1].min()) - 1, 0)
    x1, y1 = min(int(corners[:, 0].max()) + 2, W), min(int(corners[:, 1].max()) + 2, H)
    if x1 <= x0 or y1 <= y0:
        return dst
    M2 = M.copy()
    M2[0, 2] -= x0
    M2[1, 2] -= y0
    a = cv2.warpAffine(alpha, M2, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderValue=0)
    c = cv2.warpAffine(rgb, M2, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderValue=0)
    roi = dst[y0:y1, x0:x1]
    dst[y0:y1, x0:x1] = cv2.blendLinear(c, roi, a, 1.0 - a)
    return dst


# ------------------------------------------------------------------ grading + transitions
def bw(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    g = cv2.convertScaleAbs(g, alpha=1.12, beta=-10)              # a little more contrast
    return cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)


def flash(img: np.ndarray, k: float) -> np.ndarray:
    """Blend toward white. k in 0..1."""
    if k <= 0:
        return img
    return cv2.addWeighted(img, 1 - k, np.full_like(img, 255), k, 0)


def _leak_tex(W, H):
    key = ("leak", W, H)
    if key in _cache:
        return _cache[key]
    w, h = W // 2, H // 2
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    tex = np.zeros((h, w * 3, 3), np.float32)
    blobs = [((0.35, 0.45), 0.55, (255, 175, 50)), ((0.9, 0.3), 0.45, (255, 90, 35)),
             ((1.5, 0.6), 0.6, (255, 220, 120)), ((2.1, 0.4), 0.5, (255, 60, 40)), ((2.6, 0.55), 0.55, (255, 190, 80))]
    yy3, xx3 = np.mgrid[0:h, 0:w * 3].astype(np.float32)
    for (bx, by), r, col in blobs:
        d = ((xx3 - bx * w) ** 2 + (yy3 - by * h) ** 2) / (r * h) ** 2
        tex += np.exp(-d)[..., None] * np.array(col, np.float32)
    tex = np.clip(tex, 0, 255).astype(np.uint8)
    _cache[key] = tex
    return tex


def light_leak(img: np.ndarray, k: float, phase: float) -> np.ndarray:
    """Screen-blend a moving orange/red flare. k = strength 0..1, phase 0..1 = sweep position."""
    if k <= 0:
        return img
    H, W = img.shape[:2]
    tex = _leak_tex(W, H)
    tw = tex.shape[1]
    x0 = int(phase * (tw - W // 2))
    leak = cv2.resize(tex[:, x0:x0 + W // 2], (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) * k
    out = 255 - (255 - img.astype(np.float32)) * (255 - leak) / 255
    return out.astype(np.uint8)
