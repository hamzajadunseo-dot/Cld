"""Sound effects: professional files from Royal_Media_Library/SFX/<kind>/ when available,
otherwise built-in synthesised sounds. Everything is mixed into one stereo track.

Kinds (folder names)
  whoosh    big transitions (flash / light leak)        - its loudest moment lands just AFTER the cut
  impact    layered under big whooshes, end of the hook - lands ON the cut
  sub_drop  section changes                             - lands ON the cut
  riser     2 s build into the end of the hook          - ENDS on the cut
  swish     cards / split screens sliding in            - peak on the cut
  shutter   polaroid photos landing
  paper     polaroids / documents sliding in
  pop       date stamps / name cards appearing

Every file is level-matched by its loudest 50 ms (so a quiet pack and a loud pack sound the same),
and the same file is never used twice in a row.
"""
import random
import subprocess
import wave
from pathlib import Path

import numpy as np

from .media import FFMPEG, NO_WINDOW

SR = 48000
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".aif", ".aiff", ".m4a"}
_rng = np.random.default_rng(11)

# where the loudest point of each kind should sit relative to the event time
ANCHOR = {"whoosh": "peak", "swish": "peak", "impact": "start", "sub_drop": "start", "riser": "end",
          "shutter": "start", "paper": "peak", "pop": "start"}


# ------------------------------------------------------------------ synthesised fallbacks
def _lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = a[i] * acc + (1 - a[i]) * x[i]
        y[i] = acc
    return y


def _st(mono, pan=None):
    if pan is None:
        return np.stack([mono, mono], 1)
    return np.stack([mono * np.cos(pan * np.pi / 2), mono * np.sin(pan * np.pi / 2)], 1)


def _syn_whoosh(dur=0.9, soft=False):
    n = int(SR * dur)
    t = np.linspace(0, 1, n)
    noise = _rng.standard_normal(n)
    peak = 0.6
    cut = np.where(t < peak, 400 + 11000 * (t / peak) ** 2, 11400 - 9500 * ((t - peak) / (1 - peak)))
    body = _lowpass(noise, cut)
    air = noise - _lowpass(noise, np.full(n, 5000.0))                 # bright "air" layer pro whooshes have
    env = np.where(t < peak, (t / peak) ** 2.5, np.exp(-((t - peak) / (1 - peak)) * 4.5))
    mono = (body + 0.5 * air * env ** 1.5) * env
    return _st(mono, np.clip(t * 1.3 - 0.15, 0, 1)) * (0.5 if soft else 1)


def _syn_impact(dur=1.8, sub=False):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = (30 if sub else 42) + (70 if sub else 60) * np.exp(-t * (4 if sub else 9))
    boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * (1.8 if sub else 2.6))
    click = 0 if sub else _rng.standard_normal(n) * np.exp(-t * 80) * 0.4
    tail = _lowpass(_rng.standard_normal(n), np.full(n, 700.0)) * np.exp(-t * 3) * 0.6
    return _st(boom + click + tail)


def _syn_riser(dur=2.0):
    n = int(SR * dur)
    t = np.linspace(0, 1, n)
    noise = _lowpass(_rng.standard_normal(n), 300 + 9000 * t ** 2)
    tone = np.sin(2 * np.pi * np.cumsum(180 + 1400 * t ** 2) / SR) * 0.35
    return _st((noise + tone) * t ** 2.5)


def _click(m_sec, amp, ring_hz, decay):
    """One mechanical click: a sharp noise transient + a short metallic ring + a little body."""
    m = int(SR * m_sec)
    t = np.arange(m) / SR
    noise = np.diff(np.concatenate([[0], _rng.standard_normal(m)])) * np.exp(-t / 0.0018)
    ring = (np.sin(2 * np.pi * ring_hz * t) + 0.5 * np.sin(2 * np.pi * ring_hz * 1.87 * t)) * np.exp(-t / decay)
    body = np.sin(2 * np.pi * 420 * t) * np.exp(-t / 0.006)
    return (noise * 0.9 + ring * 0.35 + body * 0.4) * amp


def _syn_shutter():
    """DSLR-style shutter: mirror up (click) -> ~70 ms -> shutter close (click), with a faint mechanism rattle."""
    n = int(SR * 0.22)
    out = np.zeros(n)
    c1 = _click(0.05, 1.0, 3300, 0.010)
    c2 = _click(0.06, 0.8, 2700, 0.014)
    out[:len(c1)] += c1
    s2 = int(SR * 0.072)
    out[s2:s2 + len(c2)] += c2
    rattle = _rng.standard_normal(int(SR * 0.06)) * np.exp(-np.arange(int(SR * 0.06)) / (SR * 0.02)) * 0.08
    out[int(SR * 0.012):int(SR * 0.012) + len(rattle)] += _lowpass(rattle, np.full(len(rattle), 3000.0))
    w = np.stack([out, np.roll(out, 24)], 1)                 # tiny stereo width
    return w


def _syn_pop():
    n = int(SR * 0.12)
    t = np.arange(n) / SR
    return _st(np.sin(2 * np.pi * (900 - 2500 * t) * t) * np.exp(-t * 45))


SYNTH = {"whoosh": lambda: _syn_whoosh(), "swish": lambda: _syn_whoosh(0.45, soft=True),
         "impact": lambda: _syn_impact(), "sub_drop": lambda: _syn_impact(2.4, sub=True),
         "riser": lambda: _syn_riser(), "shutter": _syn_shutter, "paper": lambda: _syn_whoosh(0.35, soft=True),
         "pop": _syn_pop}


# ------------------------------------------------------------------ file library
def _decode(path: Path) -> np.ndarray | None:
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    res = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if res.returncode != 0 or not res.stdout:
        return None
    a = np.frombuffer(res.stdout, np.float32).reshape(-1, 2).copy()
    # trim leading/trailing near-silence so timing is exact
    env = np.abs(a).max(1)
    on = np.nonzero(env > env.max() * 0.01)[0]
    return a[on[0]:on[-1] + 1] if len(on) else None


def _peak_rms(a: np.ndarray) -> tuple[float, int]:
    """Loudest 50 ms window (RMS) and its sample position."""
    w = int(SR * 0.05)
    p = (a ** 2).mean(1)
    if len(p) < w:
        return float(np.sqrt(p.mean() + 1e-12)), len(p) // 2
    c = np.convolve(p, np.ones(w) / w, mode="valid")
    i = int(np.argmax(c))
    return float(np.sqrt(c[i] + 1e-12)), i + w // 2


class SfxBank:
    def __init__(self, sfx_dir: Path | None, log=None):
        self.files: dict[str, list[Path]] = {}
        if sfx_dir and sfx_dir.exists():
            for d in sfx_dir.iterdir():
                if d.is_dir():
                    fs = sorted(f for f in d.iterdir() if f.suffix.lower() in AUDIO_EXT)
                    if fs:
                        self.files[d.name.lower()] = fs
        self._cache: dict[str, tuple] = {}
        self._last: dict[str, list] = {}
        self.rand = random.Random(5)
        if log:
            have = ", ".join(f"{k} ({len(v)})" for k, v in sorted(self.files.items())) or "none - using built-in sounds"
            log.info("SFX library: %s", have)

    def get(self, kind: str) -> tuple[np.ndarray, int]:
        """(normalised stereo samples, anchor sample index)."""
        files = self.files.get(kind, [])
        if files:
            recent = self._last.setdefault(kind, [])
            choices = [f for f in files if f not in recent] or files
            f = self.rand.choice(choices)
            recent.append(f)
            del recent[:-min(2, len(files) - 1 or 1)]
            key = str(f)
            if key not in self._cache:
                a = _decode(f)
                self._cache[key] = a if a is not None else SYNTH[kind]().astype(np.float32)
            a = self._cache[key]
        else:
            key = "synth:" + kind
            if key not in self._cache:
                self._cache[key] = SYNTH[kind]().astype(np.float32)
            a = self._cache[key]
        lvl, pk = _peak_rms(a)
        a = a / (lvl + 1e-9)                                  # loudest 50 ms -> RMS 1.0 (0 dBFS-RMS)
        anchor = {"peak": pk, "start": 0, "end": len(a)}[ANCHOR.get(kind, "start")]
        return a, anchor


def build_track(events: list[dict], duration: float, levels_db: dict, out_path: Path,
                sfx_dir: Path | None = None, log=None) -> Path | None:
    """events: [{'t': seconds, 'kind': 'whoosh'}] -> stereo 48 kHz WAV. t = where the anchor lands."""
    if not events:
        return None
    bank = SfxBank(sfx_dir, log)
    total = np.zeros((int(SR * (duration + 4)), 2), np.float32)
    for e in events:
        k = e["kind"]
        if k not in SYNTH:
            continue
        a, anchor = bank.get(k)
        gain = 10 ** (levels_db.get(k, -20) / 20) * e.get("gain", 1.0)
        start = int(e["t"] * SR) - anchor
        s0 = max(start, 0)
        seg = a[s0 - start:]
        j = min(s0 + len(seg), len(total))
        if j > s0:
            total[s0:j] += seg[: j - s0] * gain
    total = total[: int(SR * duration)]
    peak = np.abs(total).max()
    if peak > 0.98:                                           # soft safety: never clip
        total *= 0.98 / peak
    pcm = (total * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out_path
