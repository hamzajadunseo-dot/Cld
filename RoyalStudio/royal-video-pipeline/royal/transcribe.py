"""Stage 2 - Voiceover -> word timestamps -> script-aligned segments.

1. faster-whisper gives every spoken word a start/end time (cached by audio hash).
2. The script's words are aligned to the spoken words (difflib), so segment text
   is YOUR script (correct names/spelling) but timings come from the voiceover.
3. The script is cut into segments of one sentence / visual idea, 3-8 seconds each.
"""
import difflib
import json
import os
import re
from pathlib import Path

from .media import sha1_of


# ------------------------------------------------------------------ script reading
def read_script(path: Path) -> str:
    return parse_script(path)[0]


def parse_script(path: Path) -> tuple[str, int]:
    """Spoken text of a script + number of words in its HOOK section (0 if none).

    Understands the Google-Doc layout: '## HOOK' heading, '-----' dividers,
    '✓check' research notes, **bold**/*italic* markup, and a 'Tab 2' / PACKAGE
    section at the end (titles, thumbnail, description) which is ignored.
    """
    if path.suffix.lower() == ".docx":
        import docx
        paras = []
        for p in docx.Document(str(path)).paragraphs:   # Word headings become markdown headings
            style = (p.style.name or "").lower() if p.style is not None else ""
            paras.append(("# " if style.startswith(("heading", "title")) else "") + p.text)
        text = "\n".join(paras)
    else:
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    text = re.sub(r"&#x?[0-9a-fA-F]+;|&nbsp;", " ", text).replace(" ", " ")   # stray HTML entities
    lines, hook_words, in_hook = [], 0, False
    total_words, new_section, section_starts = 0, False, []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^#*\s*(tab\s*2|package)\b", s, re.I):
            break                                            # everything after is the upload package
        if not s:
            continue
        if re.fullmatch(r"[-*_=—\s]{3,}", s):                # ----- divider = a new section starts
            in_hook = False
            new_section = bool(lines)
            continue
        if s.startswith("#"):
            heading = s.lstrip("#").strip()
            if re.fullmatch(r"hook\b.*", heading, re.I):
                in_hook = True
                continue
            if re.match(r"(chapter|part|section)\b", heading, re.I):
                in_hook = False                              # a chapter heading always ends the hook
                new_section = bool(lines)
            if (not s.startswith("##") or len(heading.split()) <= 8
                    or re.match(r"(chapter|part|section|close|outro|intro)\b", heading, re.I)):  # headings: not read aloud
                continue
            s = heading                                      # long "heading" is actually narration
        if (s[0], s[-1]) in (("[", "]"), ("(", ")")):
            continue
        if re.match(r"^(scene|section|part|chapter)\s*[:\-.]?\s*\d*\s*[:\-.]?", s, re.I) and len(s) < 60 \
                and len(s.split()) <= 8:                     # "Chapter:1", "Chapter 2 - The Folder" = new section
            in_hook = False
            new_section = bool(lines)
            continue
        s = re.sub(r"\s*✓\s*check\b.*?(?:\.(?=\s|$)|$)", "", s, flags=re.I)   # ✓check research notes
        s = re.sub(r"\[[^\]]*\]", "", s).replace("**", "").replace("*", "").strip()
        if not s:
            continue
        if new_section:
            section_starts.append(total_words)
            new_section = False
        lines.append(s)
        total_words += len(s.split())
        if in_hook:
            hook_words += len(s.split())
    parse_script.sections = section_starts                   # word index where each new section begins
    return "\n".join(lines), hook_words


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower().replace("’", "'"))


# ------------------------------------------------------------------ whisper
def _cuda_dlls():
    """ctranslate2 needs cuBLAS/cuDNN; reuse the copies that ship with PyTorch."""
    try:
        import torch
        lib = Path(torch.__file__).parent / "lib"
        if lib.exists():
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def transcribe(audio: Path, db, cfg_whisper: dict, log) -> dict:
    """{'duration': s, 'words': [{'w','start','end'}...]} cached by audio content."""
    key = f"{sha1_of(audio)}:{cfg_whisper['model']}"
    cached = db.cache_get_json("whisper", key)
    if cached:
        log.info("Transcript: cached (%d words)", len(cached["words"]))
        return cached

    _cuda_dlls()
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(cfg_whisper["model"], device="cuda", compute_type="float16")
        device = "GPU"
    except Exception as e:
        log.warning("Whisper on GPU failed (%s) - using CPU, this is slower", e)
        model = WhisperModel(cfg_whisper["model"], device="cpu", compute_type="int8")
        device = "CPU"
    log.info("Transcribing %s with %s on %s ...", audio.name, cfg_whisper["model"], device)
    segments, info = model.transcribe(str(audio), language=cfg_whisper.get("language") or None,
                                      word_timestamps=True, vad_filter=True, beam_size=5)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"w": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
    result = {"duration": round(info.duration, 3), "words": words}
    db.cache_put_json("whisper", key, result)
    db.commit()
    log.info("Transcript: %d words, %.1f s of audio", len(words), info.duration)
    return result


# ------------------------------------------------------------------ alignment
def align(script_text: str, words: list[dict]) -> tuple[list[dict], float]:
    """Every script token gets start/end from the matching spoken word (interpolated if unmatched)."""
    tokens = [t for t in re.findall(r"\S+", script_text)]
    a = [norm(t) for t in tokens]
    b = [norm(w["w"]) for w in words]
    timed = [None] * len(tokens)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            w = words[blk.b + k]
            timed[blk.a + k] = (w["start"], w["end"])
    # fill gaps by spreading unmatched tokens evenly between their matched neighbours
    known = [i for i, t in enumerate(timed) if t]
    if not known:
        raise ValueError("Script and voiceover don't match at all - wrong files?")
    known_set = set(known)
    out = []
    for i, tok in enumerate(tokens):
        if timed[i] is None:
            prev = max((k for k in known if k < i), default=None)
            nxt = min((k for k in known if k > i), default=None)
            t0 = timed[prev][1] if prev is not None else 0.0
            t1 = timed[nxt][0] if nxt is not None else words[-1]["end"]
            lo, hi = (prev if prev is not None else -1), (nxt if nxt is not None else len(tokens))
            frac0 = (i - lo - 1) / max(hi - lo - 1, 1)
            frac1 = (i - lo) / max(hi - lo - 1, 1)
            timed[i] = (t0 + (t1 - t0) * frac0, t0 + (t1 - t0) * frac1)
        out.append({"w": tok, "start": round(timed[i][0], 3), "end": round(timed[i][1], 3),
                    "matched": i in known_set})
    match_rate = len(known) / len(tokens)
    return out, match_rate


# ------------------------------------------------------------------ segmentation
SPLIT_AFTER = re.compile(r"[.!?…]['\"”’)]*$")
SOFT_SPLIT = re.compile(r"[,;:—–]$")


def segment(tokens: list[dict], duration: float, min_sec: float = 3.0, max_sec: float = 8.0) -> list[dict]:
    # 1) sentences
    sentences, cur = [], []
    for t in tokens:
        cur.append(t)
        if SPLIT_AFTER.search(t["w"]):
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)

    # 2) split long sentences at commas / dashes (else evenly) so each piece <= max_sec
    pieces = []
    for s in sentences:
        stack = [s]
        while stack:
            p = stack.pop(0)
            dur = p[-1]["end"] - p[0]["start"]
            if dur <= max_sec or len(p) < 6:
                pieces.append(p)
                continue
            mid_t = p[0]["start"] + dur / 2
            cands = [i for i in range(2, len(p) - 2) if SOFT_SPLIT.search(p[i]["w"])]
            if cands:
                i = min(cands, key=lambda i: abs(p[i]["end"] - mid_t))
            else:
                i = min(range(2, len(p) - 2), key=lambda i: abs(p[i]["end"] - mid_t))
            stack[0:0] = [p[: i + 1], p[i + 1:]]

    # 3) merge pieces that are too short into a neighbour
    merged = []
    for p in pieces:
        if merged and (p[-1]["end"] - p[0]["start"] < min_sec * 0.7
                       or merged[-1][-1]["end"] - merged[-1][0]["start"] < min_sec * 0.7) \
                and (p[-1]["end"] - merged[-1][0]["start"]) <= max_sec * 1.25:
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)

    segs = []
    for i, p in enumerate(merged):
        start = 0.0 if i == 0 else p[0]["start"]
        segs.append({"id": i, "text": " ".join(t["w"] for t in p), "start": round(start, 3),
                     "speech_end": p[-1]["end"]})
    # each segment lasts until the next one begins, so visuals cover the whole timeline
    for i, s in enumerate(segs):
        s["end"] = round(segs[i + 1]["start"] if i + 1 < len(segs) else duration, 3)
        s["duration"] = round(s["end"] - s["start"], 3)
        s.pop("speech_end")
    return segs


def run_stage2(script_path: Path | None, vo_path: Path, db, cfg, log, work: Path) -> list[dict]:
    tr = transcribe(vo_path, db, cfg["whisper"], log)
    if not tr["words"]:
        raise ValueError("No speech found in the voiceover.")
    if script_path:
        text, hook_words = parse_script(script_path)
        tokens, rate = align(text, tr["words"])
        log.info("Script/voiceover alignment: %.0f%% of script words matched", rate * 100)
        if rate < 0.6:
            log.warning("Low match - is this the right script for this voiceover? Using it anyway.")
    else:
        tokens, hook_words = [{**w, "matched": True} for w in tr["words"]], 0
    seg_cfg = cfg["segments"]
    segs = segment(tokens, tr["duration"], seg_cfg["min_sec"], seg_cfg["max_sec"])
    # HOOK section -> fast jump cuts (Stage 4). No '## HOOK' heading: use the first N seconds.
    hook_end = tokens[min(hook_words, len(tokens)) - 1]["end"] if hook_words else cfg.get("hook", {}).get("default_sec", 0)
    for sg in segs:
        sg["hook"] = bool(sg["start"] < hook_end - 0.5)
    # sections ('-----' dividers / chapter headings): mark the segment where each one begins
    if script_path:
        for w_i in getattr(parse_script, "sections", []):
            if w_i < len(tokens):
                t_sec = tokens[w_i]["start"]
                sg = min(segs, key=lambda x: abs(x["start"] - t_sec))
                if abs(sg["start"] - t_sec) < 1.5 and sg["start"] > 3:
                    sg["section_start"] = True
    if hook_end:
        log.info("Hook: first %.0f s (same pace as the rest; main subject only)", hook_end)
    (work / "words.json").write_text(json.dumps(tokens, indent=0, ensure_ascii=False), encoding="utf-8")
    (work / "segments.json").write_text(json.dumps(segs, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("Stage 2: %d segments, avg %.1f s", len(segs), tr["duration"] / max(len(segs), 1))
    return segs, tokens, tr["duration"]
