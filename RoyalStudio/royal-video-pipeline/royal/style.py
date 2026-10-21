"""The "editor": decides how every shot is presented, how it enters, and which sound effect goes with it.

Per shot (item):
  layout       full | card | split | collage
  bg           blur | particles | grid | navy           (card / split / collage backgrounds)
  rot0         entrance tilt in degrees (cards settle to 0)
  rounded      rounded corners instead of a white border
  bw           black & white grade (old photos, some sad moments)
  transition   cut | flash | leak | fade                (how this shot enters)
Plus a list of SFX events for the whole video.

All proportions live in config.yaml -> style.
"""
import random


def _pick(rng, weights: dict) -> str:
    keys, w = zip(*weights.items())
    return rng.choices(keys, weights=w, k=1)[0]


def apply_style(items: list[dict], cfg: dict, hook_end: float, callouts: list[dict], seed: str = "",
                segs: list[dict] | None = None) -> list[dict]:
    st = cfg.get("style", {})
    if not st.get("enabled", True):
        return []
    rng = random.Random(seed or "royal")
    lay_w = st.get("layouts", {"full": 0.5, "card": 0.35, "split": 0.08, "collage": 0.07})
    bg_w = st.get("card_backgrounds", {"blur": 0.45, "particles": 0.25, "grid": 0.15, "navy": 0.15})
    tr_w = st.get("transitions", {"cut": 0.55, "flash": 0.2, "leak": 0.2, "fade": 0.05})
    tr_hook = st.get("hook_transitions", {"cut": 0.7, "flash": 0.15, "leak": 0.15})
    bw_year = st.get("bw_before_year", 1995)
    sfx_on = st.get("sfx", {}).get("enabled", True)
    events = []
    segs_by_id = {sg["id"]: sg for sg in segs or []}
    sections_done = set()
    recent_layouts = []
    last_tr = "cut"

    for n, it in enumerate(items):
        dur = it["end"] - it["start"]
        in_hook = it["start"] < hook_end
        it.setdefault("layout", "full")
        # ---------------------------------------------------------------- layout
        if it["kind"] in ("photo", "clip"):
            alts = it.get("alts") or []
            second = [a for a in alts if a.get("role") == "second_person"]
            same = [a for a in alts if a.get("role") == "same"]
            options = dict(lay_w)
            if not (second and dur >= 2.4):
                options.pop("split", None)
            if not (it["kind"] == "photo" and len(same) >= 2 and dur >= 3.2):
                options.pop("collage", None)
            if second and dur >= 2.4:
                options["split"] = options.get("split", 0) + 0.35      # two people named -> favour comparison
            if in_hook:                                                # hook = moving footage: no photo collages / splits
                options.pop("collage", None)
                options.pop("split", None)
            if len(recent_layouts) >= 2 and all(l != "full" for l in recent_layouts[-2:]):
                options = {"full": 1.0}                                # breathe: never 3 framed shots in a row
            layout = _pick(rng, options)
            it["layout"] = layout
            if layout == "split":
                it["partner"] = second[0]
            elif layout == "collage":
                it["partners"] = same[:2]
            if layout in ("card", "split", "collage"):
                bgs = dict(bg_w)
                if layout != "card":
                    bgs.pop("blur", None)
                it["bg"] = _pick(rng, bgs)
                it["rot0"] = rng.choice([-5, -4, -3, 3, 4, 5]) if layout == "card" else 0
                it["rounded"] = layout == "card" and rng.random() < st.get("rounded_share", 0.25)
            yr = it.get("year") or 0
            it["bw"] = it["kind"] == "photo" and ((0 < yr <= bw_year) or
                                                (it.get("mood") in ("sad", "solemn") and rng.random() < 0.3))
            recent_layouts.append(layout)
        else:
            recent_layouts.append("full")
        # ---------------------------------------------------------------- transition in
        if n == 0:
            tr = "cut"
        elif it["kind"] == "text":
            tr = "flash"
        else:
            tr = _pick(rng, tr_hook if in_hook else tr_w)
            if tr in ("flash", "leak") and last_tr in ("flash", "leak"):
                tr = "cut"                                             # never two effects back to back
            if tr in ("flash", "leak") and dur < 1.0:
                tr = "cut"
        # first shot of a new script section ('-----'): always an effect transition + stop-down
        if n > 0 and segs_by_id.get(it.get("seg"), {}).get("section_start") and it.get("seg") not in sections_done:
            sections_done.add(it.get("seg"))
            it["section_start"] = True
            if it["kind"] != "text" and tr == "cut":
                tr = "leak"
        it["transition"] = tr
        last_tr = tr

    return build_sfx_events(items, cfg, hook_end, callouts, seed)


def build_sfx_events(items: list[dict], cfg: dict, hook_end: float, callouts: list[dict], seed: str = "") -> list[dict]:
    """Sound effects for an already-styled timeline (also used to re-do the SFX of a finished video).

    The user's choice (2026-10-08): transitions are CAMERA SHUTTER clicks with only a soft whoosh
    layered underneath (loud whooshes sounded odd). Section changes add a stop-down + sub drop.
    """
    st = cfg.get("style", {})
    if not st.get("sfx", {}).get("enabled", True):
        return []
    rng = random.Random((seed or "royal") + ":sfx")
    events = []
    for n, it in enumerate(items):
        if n == 0:
            continue
        tr = it.get("transition")
        if it.get("section_start"):
            events.append({"t": it["start"] - 0.55, "kind": "stopdown", "dur": 0.5})
            events.append({"t": it["start"], "kind": "sub_drop"})
            events.append({"t": it["start"], "kind": "shutter"})
            events.append({"t": it["start"] + 0.15, "kind": "whoosh", "gain": 0.4})
        elif tr == "flash":                               # white flash = camera flash -> shutter click
            events.append({"t": it["start"] - 0.03, "kind": "shutter"})
            events.append({"t": it["start"] + 0.15, "kind": "whoosh", "gain": 0.3})
        elif tr == "leak":                                # film burn -> shutter + soft air
            events.append({"t": it["start"], "kind": "shutter", "gain": 0.9})
            events.append({"t": it["start"] + 0.15, "kind": "whoosh", "gain": 0.5})
            if rng.random() < 0.35:
                events.append({"t": it["start"], "kind": "impact", "gain": 0.5})
        elif it.get("layout") in ("card", "split") and rng.random() < 0.6:
            events.append({"t": it["start"] + 0.02, "kind": "shutter", "gain": 0.55})
        if it.get("layout") == "collage":
            events.append({"t": it["start"] + 0.1, "kind": "paper"})
            for k in range(3):
                events.append({"t": it["start"] + 0.18 + k * 0.28, "kind": "shutter"})
    if hook_end > 4:                                                    # build into the end of the hook
        events.append({"t": hook_end, "kind": "riser"})                 # riser ENDS on the hook's last word
        events.append({"t": hook_end, "kind": "impact"})
    for c in callouts or []:
        if c["kind"] == "date":
            events.append({"t": c["start"], "kind": "pop"})
        elif st.get("sfx", {}).get("callout_pop", True):
            events.append({"t": c["start"], "kind": "pop", "gain": 0.6})
    events.sort(key=lambda e: e["t"])
    return events
