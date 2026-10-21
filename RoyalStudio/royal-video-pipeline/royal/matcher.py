"""Stage 4 - Pick the best visual for every moment of the voiceover.

Each segment is cut into shots of ~3-6 s. Every shot is scored against the whole
library:  right people (biggest weight) + event/year + CLIP similarity to the
segment's search query + mood + quality, minus a penalty for anything shown
recently. Rules from config.yaml (no repeat within 60 s, clips <= 5 s, ...) are
hard constraints. Near-duplicate photos (same burst) count as one visual.
When nothing good exists, a generic royal visual or text card is used and the
gap is written to missing_assets.txt.
"""
import math
import re
from collections import defaultdict

import numpy as np

SAD = {"sad", "solemn"}
HAPPY = {"celebratory", "joyful", "warm"}
# not a real photo of the person: down-rank (needs captions to know)
ODD_TAGS = {"scarecrow", "statue", "waxwork", "cartoon", "pub sign", "sign", "signage", "poster", "mural",
            "painting", "portrait painting", "banner", "billboard", "souvenir", "merchandise", "mask", "cardboard cutout"}


def _tokens(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower())} - {"the", "and", "royal", "of"}


class Matcher:
    def __init__(self, assets: list[dict], clip, cfg: dict, log, words: list[dict] | None = None):
        self.cfg = cfg
        self.rules = cfg["rules"]
        self.log = log
        self.clip = clip
        vis = [a for a in assets if a["type"] in ("photo", "clip") and a["clip_vec"] is not None]
        if not cfg["matching"].get("allow_unlicensed", True):
            vis = [a for a in vis if a["license"]]
        self.assets = vis
        self.V = np.stack([a["clip_vec"] for a in vis])
        self.V /= np.linalg.norm(self.V, axis=1, keepdims=True) + 1e-9
        self.is_clip = np.array([a["type"] == "clip" for a in vis])
        self.shot_len = np.array([(a["duration"] or 0) if a["type"] == "clip" else 1e9 for a in vis])
        self.quality = np.array([a["quality"] or 0.4 for a in vis])
        self.year = np.array([a["year"] or 0 for a in vis])
        self.group = np.array([a["dup_group"] or a["id"] for a in vis])
        self.odd_kind = np.array([(a["image_kind"] or "photo") not in ("photo",) or
                                  bool(ODD_TAGS & set(a["tags"] or []))
                                  for a in vis])
        self.portrait = np.array([a["orientation"] == "portrait" for a in vis])
        self.mood = [a["mood"] for a in vis]
        self.event_tok = [_tokens(f"{a['event']} {a['caption']}") for a in vis]
        # person -> confidence per asset (1 = face recognised, 0.5 = folder tag only)
        self.conf: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(len(vis)))
        for i, a in enumerate(vis):
            for p in a["people"] or []:
                self.conf[p][i] = 1.0 if (a["people_scores"] or {}).get(p, 0) > 0 else 0.5
        self.n_people = np.array([sum(1 for p in (a["people"] or []) if (a["people_scores"] or {}).get(p, 0) > 0)
                                  for a in vis])
        self.history = np.zeros(len(vis))     # how many earlier videos already showed this visual
        self.last_used: dict[int, float] = {}
        self.use_count: dict[int, int] = defaultdict(int)
        self.missing: list[dict] = []
        self.n_clip = self.n_shots = 0
        # stock clips by concept folder: Stock/<concept>/...
        self.stock_idx: dict[str, list[int]] = defaultdict(list)
        for i, a in enumerate(vis):
            if a["category"] == "Stock" and a["type"] == "clip":
                self.stock_idx[a["path"].split("/")[1]].append(i)
        self.n_stock = 0
        self.last_stock_t = -1e9
        self.pauses = self._pauses(words or [])

    # ------------------------------------------------------------------ planning
    def plan_slots(self, seg) -> list[tuple[float, float, str]]:
        """Split a segment into shots of 3-6 s. Returns (start, end, kind_hint)."""
        start, end = seg["start"], seg["end"]
        slots = []
        if seg.get("visual") == "text_card" and seg.get("text_card"):
            card_end = min(end, start + min(4.0, max(2.5, seg["duration"])))
            slots.append((start, card_end, "text"))
            start = card_end
        dur = end - start
        if dur < 0.05:
            return slots
        # Cuts land on the narrator's pauses (reference edit: 97% of cuts sit on a breath / comma /
        # sentence end), so shot lengths vary naturally around ~3.6 s instead of equal slices.
        lo, hi = self.rules["change_visual_every_sec"]
        # every shot short enough for a clip (clips max 5 s) so footage, not photos, can fill it
        max_clip = self.rules["max_clip_shot_sec"]
        hi = min(hi, max_clip - 0.5)
        target = self.cfg.get("pacing", {}).get("target_shot_sec", 3.6)
        min_shot = self.cfg.get("pacing", {}).get("min_shot_sec", 1.5)
        cands = [(t, w) for t, w in self.pauses if start + min_shot <= t <= end - min_shot]
        cur, bounds = start, []
        while end - cur > hi + 0.5:
            window = [(t, w) for t, w in cands if cur + min_shot <= t <= cur + min(hi + 1.0, max_clip)]
            if window:
                t = max(window, key=lambda p: p[1] - 0.35 * abs(p[0] - (cur + target)))[0]
            else:
                t = cur + target                   # no pause in reach: fall back to the target length
            bounds.append(t)
            cur = t
        edges = [start] + bounds + [end]
        return slots + [(a, b, "visual") for a, b in zip(edges, edges[1:])]

    def load_history(self, timelines) -> int:
        """Count, per visual, how many earlier videos used it, so every new video digs deeper into
        the library instead of reusing the same 'best' shots (user: same footage in every video)."""
        by_path: dict[str, list[int]] = defaultdict(list)
        for i, a in enumerate(self.assets):
            by_path[a["path"]].append(i)
        per_group: dict = defaultdict(int)
        n = 0
        for tl in timelines:
            items = tl["items"] if isinstance(tl, dict) else tl
            seen = set()
            for it in items:
                for i in by_path.get(it.get("path"), []):
                    a = self.assets[i]
                    if a["type"] == "clip":
                        t = it.get("src_in") or 0
                        if not ((a["start_sec"] or 0) - 0.5 <= t < (a["end_sec"] or 0)):
                            continue
                    seen.add(self.group[i])
            for g in seen:
                per_group[g] += 1
            n += 1
        self.history = np.array([per_group.get(g, 0) for g in self.group], dtype=float)
        return n

    @staticmethod
    def _pauses(words) -> list[tuple[float, float]]:
        """(time, strength) for every gap between spoken words; punctuation makes a pause stronger."""
        out = []
        for w0, w1 in zip(words, words[1:]):
            gap = max(w1["start"] - w0["end"], 0.0)
            punct = w0["w"].rstrip("\"'”’)")[-1:]
            strength = gap + (0.6 if punct in ".!?" else 0.3 if punct in ",;:—–-" else 0.0)
            if strength > 0.05:
                out.append(((w0["end"] + w1["start"]) / 2, strength))
        return out

    # ------------------------------------------------------------------ scoring
    def score(self, qvec, seg, t0, length, prev_path):
        r = self.rules
        sim = self.V @ qvec
        s = 1.6 * np.clip((sim - 0.15) / 0.15, 0, 1.5)

        people = seg.get("people") or []
        if people:
            pc = np.mean([self.conf[p] for p in people], axis=0)
            s += 3.0 * pc
            others = self.n_people - sum((self.conf[p] == 1.0).astype(int) for p in people)
            s -= 0.25 * np.clip(others, 0, 3)          # strangers / wrong royals in frame
            eligible = pc > 0
        else:
            eligible = np.ones(len(self.assets), bool)

        if seg.get("year"):
            dy = np.where(self.year > 0, np.abs(self.year - seg["year"]), 99)
            s += np.where(dy <= 1, 0.6, np.where(dy <= 4, 0.3, np.where((dy > 15) & (dy < 99), -0.5, 0)))
        ev = _tokens(seg.get("event"))
        if ev:
            s += 0.8 * np.array([len(ev & t) / len(ev) for t in self.event_tok])
        m = seg.get("mood")
        if m:
            mood = np.array([0.25 if am == m else
                             -0.4 if (m in SAD and am in HAPPY) or (m in HAPPY and am in SAD) else 0
                             for am in self.mood])
            s += mood
        s += 0.6 * self.quality
        s -= 0.4 * self.odd_kind
        s -= 0.1 * self.portrait
        # steer toward the target clip/photo mix: the further clips fall behind, the bigger their bonus
        target = self.cfg["matching"].get("clip_ratio", 0.5)
        deficit = target - self.n_clip / max(self.n_shots, 1)
        clip_bonus = float(np.clip(0.6 + 6 * deficit, -1.0, 3.0)) + (0.3 if seg.get("visual") == "clip" else 0)
        s += np.where(self.is_clip, clip_bonus, 0)

        # hard rules
        max_clip = r["max_clip_shot_sec"]
        clip_ok = (length <= max_clip) & (self.shot_len >= length)
        eligible &= ~self.is_clip | clip_ok
        recent = np.array([t0 - self.last_used.get(g, -1e9) < r["no_repeat_within_sec"] for g in self.group])
        eligible &= ~recent
        # the same shot at most N times per video (user: same clip kept coming back)
        cap = r.get("max_uses_per_video", 2)
        eligible &= np.array([self.use_count[g] < cap for g in self.group])
        # hook: moving footage only (user: the opening must be video clips, not photos)
        if seg.get("hook") and self.cfg.get("hook", {}).get("clips_only", True):
            # STRICT (user 2026-10-14): the hook never shows a photo - only video clips
            base = eligible & self.is_clip
            if people and self.cfg.get("hook", {}).get("subject_only", True):
                solo = base & (others <= 0)
                if solo.any():
                    base = solo
            if not base.any() and people:
                # every fresh clip used up: repeat a clip of this person rather than fall back to a photo
                relaxed = (pc > 0) & self.is_clip & clip_ok
                solo = relaxed & (others <= 0)
                base = solo if solo.any() else relaxed
            eligible = base        # may be empty -> pick() falls back to a generic clip, never a photo
        s -= 0.35 * np.array([self.use_count[g] for g in self.group])
        hp = self.cfg["matching"].get("history_penalty", 0.25)
        s -= np.minimum(hp * self.history, self.cfg["matching"].get("history_penalty_max", 2.0))
        if prev_path:
            s -= 0.5 * np.array([a["path"] == prev_path for a in self.assets])  # avoid back-to-back same video
        return s, eligible, sim

    def pick(self, qvec, seg, t0, length, prev_path):
        s, ok, sim = self.score(qvec, seg, t0, length, prev_path)
        self._last = (s, ok)
        min_score = self.cfg["matching"]["min_score"]
        if ok.any():
            i = int(np.argmax(np.where(ok, s, -1e9)))
            if s[i] >= min_score:
                return i, float(s[i]), None
        # fallback 1: right people but ignore relevance threshold
        if ok.any():
            i = int(np.argmax(np.where(ok, s, -1e9)))
            return i, float(s[i]), "weak match"
        # fallback 2: any unused royal visual that fits the meaning (generic)
        generic = {**seg, "people": []}
        s2, ok2, _ = self.score(qvec, generic, t0, length, prev_path)
        if ok2.any():
            i = int(np.argmax(np.where(ok2, s2, -1e9)))
            return i, float(s2[i]), "no visual of " + ", ".join(seg.get("people") or ["this"])
        return None, 0.0, "library exhausted"

    def alternates(self, i, seg, t0) -> list[dict]:
        """Extra photos for split screens / collages: other bursts of the same moment's people,
        plus (for two-person segments) the best photo of the second person on their own."""
        s, ok = getattr(self, "_last", (None, None))
        if s is None:
            return []
        people = seg.get("people") or []
        photo = ~self.is_clip & (self.group != self.group[i])
        recent = np.array([t0 - self.last_used.get(g, -1e9) < 30 for g in self.group])
        cand = photo & ~recent & (ok | (s > 0))
        out, used_groups = [], {self.group[i]}
        if len(people) >= 2:                      # comparison: second person alone
            p0, p1 = people[0], people[1]
            m = cand & (self.conf[p1] == 1.0) & (self.conf[p0] == 0)
            if m.any():
                j = int(np.argmax(np.where(m, s, -1e9)))
                out.append({**_asset_ref(self.assets[j], [p1]), "role": "second_person"})
                used_groups.add(self.group[j])
        order = np.argsort(-np.where(cand & ok, s, -1e9))
        for j in order[:40]:
            if len(out) >= 3 or s[j] < -1e8:
                break
            if self.group[j] in used_groups:
                continue
            used_groups.add(self.group[j])
            out.append({**_asset_ref(self.assets[j], people), "role": "same"})
        return out

    # ------------------------------------------------------------------ run
    def pick_stock(self, concept, qvec, t0, length):
        """Best stock clip for a concept (by meaning), respecting the repeat / share / gap rules."""
        sc = self.cfg.get("stock", {})
        idx = self.stock_idx.get(concept)
        if not idx:
            return None
        if (self.n_stock + 1) / max(self.n_shots + 1, 1) > sc.get("max_share", 0.15) and self.n_shots > 10:
            return None
        if t0 < sc.get("skip_first_sec", 0) and concept not in self.cfg.get("hook", {}).get("stock_concepts", []):
            return None                     # the start of the video is real royal footage, not stock
        if t0 - self.last_stock_t < sc.get("min_gap_sec", 8):
            return None
        best, best_s = None, -1e9
        for i in idx:
            if self.shot_len[i] < length or length > self.rules["max_clip_shot_sec"] + 1:
                continue
            if t0 - self.last_used.get(self.group[i], -1e9) < self.rules["no_repeat_within_sec"]:
                continue
            if self.use_count[self.group[i]] >= self.rules.get("max_uses_per_video", 2):
                continue
            sc_i = float(self.V[i] @ qvec) - 0.05 * self.use_count[self.group[i]]
            if sc_i > best_s:
                best, best_s = i, sc_i
        return best

    def build(self, segs, seg_concepts=None) -> list[dict]:
        seg_concepts = seg_concepts or {}
        queries = [s.get("search_query") or s["text"][:200] for s in segs]
        Q = np.concatenate([self.clip.text(queries[i:i + 64]) for i in range(0, len(queries), 64)])
        items, prev_path = [], None
        for seg, q in zip(segs, Q):
            stock_done = False
            extra_done = False
            for t0, t1, hint in self.plan_slots(seg):
                if hint == "text":
                    items.append({"kind": "text", "start": t0, "end": t1, "text": seg["text_card"], "seg": seg["id"]})
                    continue
                length = t1 - t0
                i = None
                hook_ok = not seg.get("hook") or seg_concepts.get(seg["id"]) in self.cfg.get("hook", {}).get("stock_concepts", [])
                if not stock_done and seg["id"] in seg_concepts and hook_ok:   # hook: only place stock (mansion...)
                    i = self.pick_stock(seg_concepts[seg["id"]], q, t0, length)
                    stock_done = True
                if i is not None:              # a stock shot that illustrates what is being said
                    sc, problem = 1.0, None
                    self._last = (None, None)
                    self.n_stock += 1
                    self.last_stock_t = t0
                else:
                    i = None
                    extra = [p for p in (seg.get("people") or []) if p not in self.cfg.get("_subject", [])]
                    if extra and not extra_done:
                        extra_done = True     # the named guest (Tyler Perry, Oprah...) gets the first shot
                        i, sc, problem = self.pick(q, {**seg, "people": extra[:1]}, t0, length, prev_path)
                        if problem or i is None or extra[0] not in (self.assets[i]["people"] or []):
                            i = None
                    if i is None:
                        i, sc, problem = self.pick(q, seg, t0, length, prev_path)
                if problem:
                    self.missing.append({"time": t0, "seg": seg, "problem": problem})
                if i is None:
                    items.append({"kind": "text", "start": t0, "end": t1, "seg": seg["id"],
                                  "text": short_title(seg)})
                    continue
                a = self.assets[i]
                g = self.group[i]
                alts = self.alternates(i, seg, t0)
                self.last_used[g] = t0
                self.use_count[g] += 1
                self.n_shots += 1
                self.n_clip += a["type"] == "clip"
                prev_path = a["path"] if a["type"] == "clip" else None
                item = {"kind": a["type"], "start": round(t0, 3), "end": round(t1, 3), "seg": seg["id"],
                        "asset_id": a["id"], "path": a["path"], "score": round(sc, 2),
                        "people": a["people"], "caption": a["caption"], "license": a["license"],
                        "author": a["author"], "source_page": a["source_page"],
                        "width": a["width"], "height": a["height"],
                        "focus": focus_point(a, seg.get("people") or []),
                        "transition": "cut" if seg.get("hook") else "fade",
                        "year": a["year"], "mood": seg.get("mood"), "seg_people": seg.get("people") or [],
                        "alts": alts}
                if a["type"] == "clip":
                    spare = a["duration"] - length
                    item["src_in"] = round(a["start_sec"] + max(spare, 0) / 2, 3)   # middle of the shot
                    item["src_out"] = round(item["src_in"] + length, 3)
                items.append(item)
        return items


def _asset_ref(a, people) -> dict:
    return {"asset_id": a["id"], "path": a["path"], "width": a["width"], "height": a["height"],
            "people": a["people"], "license": a["license"], "author": a["author"],
            "source_page": a["source_page"], "caption": a["caption"], "focus": focus_point(a, people)}


def focus_point(a, people) -> list[float]:
    """Normalized (x, y) the Ken Burns zoom drifts toward: the wanted person's face, else largest face."""
    faces = [f for f in (a["faces"] or []) if f.get("t") is None]
    pick = [f for f in faces if f.get("person") in people] or faces
    if not pick:
        return [0.5, 0.4]
    f = max(pick, key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]))
    return [round((f["bbox"][0] + f["bbox"][2]) / 2, 3), round((f["bbox"][1] + f["bbox"][3]) / 2, 3)]


def short_title(seg) -> str:
    if seg.get("people"):
        return " & ".join(p.replace("_", " ") for p in seg["people"][:2]).upper()
    words = seg["text"].split()
    return " ".join(words[:6]).upper()


def mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def write_missing_report(path, missing):
    lines = ["MISSING / WEAK ASSETS - add these to the library to improve future videos", ""]
    if not missing:
        lines.append("Nothing missing - every moment had a good match.")
    want = defaultdict(int)
    for m in missing:
        seg = m["seg"]
        who = ", ".join(seg.get("people") or []) or "-"
        lines.append(f"[{mmss(m['time'])}] {m['problem']}")
        lines.append(f"    script : {seg['text'][:140]}")
        lines.append(f"    wanted : people={who} | event={seg.get('event') or '-'} | year={seg.get('year') or '-'}")
        lines.append(f"    search : {seg.get('search_query', '')}")
        lines.append("")
        for p in seg.get("people") or []:
            want[(p, seg.get("event") or "")] += 1
    if want:
        lines.append("SUMMARY - most wanted:")
        for (p, ev), n in sorted(want.items(), key=lambda x: -x[1]):
            lines.append(f"  {n:3d} x  {p.replace('_', ' ')}" + (f"  @ {ev}" if ev else ""))
    path.write_text("\n".join(lines), encoding="utf-8")
