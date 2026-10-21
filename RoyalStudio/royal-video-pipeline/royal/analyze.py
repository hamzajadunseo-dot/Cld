"""Stage 3 - Script analysis: who / what / when / mood / ideal visual for every segment.

With an Anthropic API key, Claude reads the whole script (so "he", "the King",
"his son" resolve correctly) and returns structured JSON per segment.
Without a key, a keyword fallback tags names and carries them over to following
sentences - usable for a test, but far less precise.
"""
import hashlib
import json
import os
import re

import anthropic

MOODS = ["formal", "celebratory", "joyful", "warm", "solemn", "sad", "tense", "dramatic", "casual", "neutral"]
VISUALS = ["person", "group", "clip", "text_card", "generic"]

SYSTEM = """You are the picture editor for a faceless YouTube documentary channel about the British Royal Family.
The narrator reads a script; for every segment you decide what should be on screen.

The editor's media library is organised by these person IDs (use ONLY these IDs in "people"):
{people}

For each segment return:
- people: person IDs the segment is about or refers to - resolve pronouns and titles from context
  ("he", "the King", "his son", "the Princess of Wales" = whoever is meant at that point in the story).
  Pick the people that should be ON SCREEN. Empty list if nobody specific (e.g. general history).
- event: named event if any ("Coronation of Charles III", "Royal Wedding 2011", "Funeral of Elizabeth II"), else "".
- year: the year the moment happened, 0 if unclear.
- location: place if relevant, else "".
- mood: the emotional tone of the moment.
- visual: "person" (one person's photo), "group" (several people together), "clip" (movement/action
  fits better), "text_card" (ONLY for a striking number, date, or short direct quote), "generic"
  (palace, crowds, flags - nobody specific).
- text_card: if visual is text_card, the 2-8 words to show (e.g. "£86 MILLION", "8 SEPTEMBER 2022"), else "".
- search_query: a short visual description to search a photo library, e.g.
  "King Charles waving from Buckingham Palace balcony" or "sad crowd outside palace gates with flowers".
Use text cards sparingly (at most 1 in 10 segments)."""


def _schema(people_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {"segments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "people": {"type": "array", "items": {"type": "string", "enum": people_ids}},
                "event": {"type": "string"},
                "year": {"type": "integer"},
                "location": {"type": "string"},
                "mood": {"type": "string", "enum": MOODS},
                "visual": {"type": "string", "enum": VISUALS},
                "text_card": {"type": "string"},
                "search_query": {"type": "string"},
            },
            "required": ["id", "people", "event", "year", "location", "mood", "visual", "text_card", "search_query"],
            "additionalProperties": False,
        }}},
        "required": ["segments"],
        "additionalProperties": False,
    }


def analyze_with_claude(segs, people_ids, aliases, cfg_an, db, log) -> list[dict]:
    client = anthropic.Anthropic(max_retries=5)
    people_desc = "\n".join(f"- {p}: {', '.join(aliases.get(p, [p.replace('_', ' ')]))}" for p in people_ids)
    system = SYSTEM.format(people=people_desc)
    script = "\n".join(f"[{s['id']}] {s['text']}" for s in segs)
    schema = _schema(people_ids)
    model = cfg_an["model"]
    out: dict[int, dict] = {}
    chunk = cfg_an.get("chunk_segments", 40)
    for lo in range(0, len(segs), chunk):
        part = segs[lo:lo + chunk]
        key = json.dumps([model, system, script, part[0]["id"], part[-1]["id"]], ensure_ascii=False)
        ck = hashlib.sha1(key.encode()).hexdigest()
        cached = db.cache_get_json("analysis", ck)
        if cached:
            for r in cached:
                out[r["id"]] = r
            continue
        log.info("  Claude: analysing segments %d-%d ...", part[0]["id"], part[-1]["id"])
        content = [
            # the whole script first (cached across chunks) so pronouns resolve with full context
            {"type": "text", "text": f"FULL SCRIPT (segment ids in brackets):\n{script}",
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"Return the analysis for segments {part[0]['id']} to {part[-1]['id']} "
                                     f"(every id in that range, in order)."},
        ]
        params = dict(model=model, max_tokens=32000, system=system,
                      messages=[{"role": "user", "content": content}],
                      output_config={"effort": cfg_an.get("effort", "medium"),
                                     "format": {"type": "json_schema", "schema": schema}})
        try:
            # server-side fallback: if the model declines, the API reroutes automatically
            with client.beta.messages.stream(**params, betas=["server-side-fallback-2026-07-01"],
                                             extra_body={"fallbacks": "default"}) as stream:
                resp = stream.get_final_message()
        except anthropic.BadRequestError as e:
            if "fallback" not in str(e).lower():
                raise
            with client.messages.stream(**params) as stream:
                resp = stream.get_final_message()
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude declined to analyse this part of the script.")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Analysis was cut off (max_tokens) - lower chunk_segments in config.yaml.")
        text = next(b.text for b in resp.content if b.type == "text")
        rows = json.loads(text)["segments"]
        db.cache_put_json("analysis", ck, rows)
        db.commit()
        u = resp.usage
        log.info("    %d in (+%d cached) / %d out tokens", u.input_tokens,
                 getattr(u, "cache_read_input_tokens", 0) or 0, u.output_tokens)
        for r in rows:
            out[r["id"]] = r
    return [{**s, **out.get(s["id"], fallback_row(s))} for s in segs]


# ------------------------------------------------------------------ keyword fallback
def fallback_row(seg, people=None) -> dict:
    return {"people": people or [], "event": "", "year": 0, "location": "", "mood": "neutral",
            "visual": "person" if people else "generic", "text_card": "", "search_query": seg["text"][:200]}


ROYALS = {"King_Charles", "Queen_Camilla", "Prince_William", "Princess_Catherine", "Prince_Harry", "Meghan_Markle",
          "Princess_Anne", "Princess_Diana", "Queen_Elizabeth_II", "Prince_Philip", "Prince_Edward", "Sophie",
          "Prince_Andrew", "Princess_Beatrice", "Princess_Eugenie", "Zara_Tindall", "Queen_Mother",
          "Prince_George", "Princess_Charlotte", "Prince_Louis", "Archie", "Lilibet"}

FEMALE = {"Queen_Camilla", "Princess_Catherine", "Meghan_Markle", "Queen_Elizabeth_II", "Princess_Anne",
          "Princess_Diana", "Princess_Charlotte", "Princess_Beatrice", "Princess_Eugenie", "Sophie", "Lilibet",
          "Oprah_Winfrey", "Samantha_Markle", "Mindy_Kaling", "Jamie_Kern_Lima", "Queen_Mother", "Zara_Tindall"}


def _patterns(people_ids, aliases):
    patterns = {}
    for p in people_ids:
        names = sorted(set(aliases.get(p, []) + [p.replace("_", " ")]), key=len, reverse=True)
        patterns[p] = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)
    return patterns


def main_subject(segs, people_ids, aliases, override=None) -> list[str]:
    """Who the video is about: the person (or two) mentioned in the most segments, unless overridden."""
    if override:
        return list(override)
    patterns = _patterns(people_ids, aliases)
    hook = [s for s in segs if s.get("hook")] or segs[: max(3, len(segs) // 10)]
    # names in the hook weigh 4x: the opening usually tells you who the video is about
    counts = {p: sum(1 for s in segs if rx.search(s["text"])) + 4 * sum(1 for s in hook if rx.search(s["text"]))
              for p, rx in patterns.items()}
    if not any(rx.search(s["text"]) for s in hook for rx in patterns.values()):
        # nobody named in the hook ("Her own voice..."): its pronouns tell us if the subject is a woman or a man
        text = " ".join(s["text"] for s in hook).lower()
        she = len(re.findall(r"\b(?:she|her|hers|herself)\b", text))
        he = len(re.findall(r"\b(?:he|him|his|himself)\b", text))
        if she > he:
            counts = {p: c for p, c in counts.items() if p in FEMALE} or counts
        elif he > she:
            counts = {p: c for p, c in counts.items() if p not in FEMALE} or counts
    ranked = sorted(((c, p) for p, c in counts.items() if c), reverse=True)
    if not ranked:
        return []
    top = [ranked[0][1]]
    if len(ranked) > 1 and ranked[1][0] >= 0.6 * ranked[0][0]:
        top.append(ranked[1][1])          # a genuine double act (e.g. Harry & Meghan)
    return top


def analyze_keywords(segs, people_ids, aliases, subject=None) -> list[dict]:
    patterns = _patterns(people_ids, aliases)
    subject = subject or []
    out, last = [], list(subject)       # before anyone is named, the story is about the main subject
    for s in segs:
        found = [p for p, rx in patterns.items() if rx.search(s["text"])]
        if s.get("hook"):
            people = found or subject   # hook: unnamed lines always show who the video is about
        else:
            people = found or last      # carry the subject over pronoun-only sentences
        if found:
            last = found
        row = fallback_row(s, people[:3])
        if len(people) > 1:
            row["visual"] = "group"
        y = re.search(r"\b(19[0-9]{2}|20[0-4][0-9])\b", s["text"])
        row["year"] = int(y.group(1)) if y else 0
        out.append({**s, **row})
    return out


def run_stage3(segs, people_ids, cfg, db, log, work) -> list[dict]:
    aliases = cfg.get("people_aliases", {})
    subject = main_subject(segs, people_ids, aliases, cfg.get("subject_override"))
    log.info("Main subject of this video: %s", ", ".join(subject) or "unknown")
    cfg["_subject"] = list(subject)
    if os.getenv("ANTHROPIC_API_KEY"):
        log.info("Stage 3: Claude (%s) script analysis", cfg["analysis"]["model"])
        rows = analyze_with_claude(segs, people_ids, aliases, cfg["analysis"], db, log)
    else:
        log.warning("Stage 3: no ANTHROPIC_API_KEY - using keyword matching (much less accurate)")
        rows = analyze_keywords(segs, people_ids, aliases, subject)
    # User rule: the hook shows ONLY the person the video is about, even when others are named
    if subject and cfg.get("hook", {}).get("subject_only", True):
        main = subject if cfg.get("subject_override") else subject[:1]   # --subject A,B = a couple video
        for r in rows:
            if r.get("hook"):
                # a named non-royal guest (Tyler Perry, Oprah...) is shown on the line that names them
                guests = [p for p in r.get("people") or [] if p not in main and p not in ROYALS]
                r["people"] = list(main) + guests[:1]
                r["visual"] = "person"
    (work / "analysis.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return rows
