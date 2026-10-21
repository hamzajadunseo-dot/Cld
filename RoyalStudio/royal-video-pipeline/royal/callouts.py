"""On-screen callouts timed to the exact spoken word.

  name   lower third when a person is mentioned   "PRINCE WILLIAM / Prince of Wales"
  date   top-left date stamp                      "JANUARY 2020"
  event  lower third for a named event / place    "THE OPRAH INTERVIEW"

Names, titles and events come from config.yaml (people_titles, callouts.events),
so they work without the Claude API. Rules stop them from cluttering the screen:
a person is named again only after `name_repeat_sec`, dates need a gap, and two
lower thirds never overlap.
"""
import re

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RX = re.compile(rf"\b((?:\d{{1,2}}\s+)?(?:{MONTHS})(?:\s+\d{{1,2}}(?:st|nd|rd|th)?)?,?\s+(?:18|19|20)\d\d)\b"
                     rf"|\b(?:in|since|by|until|of)\s+((?:18|19|20)\d\d)\b", re.I)


UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_U, _T, _X = "|".join(UNITS), "|".join(TEENS), "|".join(TENS)
# TTS scripts spell years out: "August twenty twenty-four", "in nineteen seventeen"
WORD_YEAR_RX = re.compile(
    rf"\b(?:(?P<month>{MONTHS})\s+|(?:in|since|by|until|of)\s+(?:early\s+|late\s+|mid-?\s*)?)"
    rf"(?P<cent>nineteen|twenty)[\s-]"
    rf"(?P<rest>(?:{_T})|(?:{_X})(?:[\s-](?:{_U}))?|(?:oh|o)[\s-](?:{_U}))\b", re.I)


def _word_year(cent: str, rest: str) -> int:
    c = 1900 if cent.lower() == "nineteen" else 2000
    parts = re.split(r"[\s-]+", rest.lower())
    if parts[0] in TEENS:
        return c + TEENS[parts[0]]
    if parts[0] in ("oh", "o"):
        return c + UNITS[parts[1]]
    return c + TENS[parts[0]] + (UNITS[parts[1]] if len(parts) > 1 else 0)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9' ]", " ", s.lower().replace("’", "'"))


def _phrase_index(tokens):
    """Joined lowercase text + map from character offset -> token index (for timing)."""
    text, starts = "", []
    for i, t in enumerate(tokens):
        if text:
            text += " "
        starts.append((len(text), i))
        text += t["w"]
    return text, starts


def _token_at(starts, pos):
    lo = 0
    for off, i in starts:
        if off > pos:
            break
        lo = i
    return lo


def build_callouts(tokens: list[dict], cfg: dict, duration: float) -> list[dict]:
    cc = cfg.get("callouts", {})
    if not cc.get("enabled", True) or not tokens:
        return []
    text, starts = _phrase_index(tokens)
    low = text.lower()
    found = []

    # ---- people (library folders + extra names), longest alias first so "Prince William" beats "William"
    titles = cfg.get("people_titles", {})
    aliases = cfg.get("people_aliases", {})
    names = []
    skip = {x.lower() for x in cc.get("skip_aliases", [])}   # titles whose holder depends on the year
    for pid, info in titles.items():
        for a in set(aliases.get(pid, []) + info.get("aliases", [])):
            if a.lower() not in skip:
                names.append((a, pid))
    names.sort(key=lambda x: -len(x[0]))
    taken = []
    for alias, pid in names:
        for m in re.finditer(rf"(?<![\w'])({re.escape(alias.lower())})(?:'s)?(?![\w])", low):
            if any(a <= m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append(("name", m.start(), pid))

    # ---- dates
    for m in DATE_RX.finditer(text):
        label = (m.group(1) or m.group(2)).replace(",", "").upper()
        label = re.sub(r"(\d)(ST|ND|RD|TH)\b", r"\1", label)
        found.append(("date", m.start(), label))

    for m in WORD_YEAR_RX.finditer(text):
        y = _word_year(m.group("cent"), m.group("rest"))
        label = f"{m.group('month').upper()} {y}" if m.group("month") else str(y)
        found.append(("date", m.start(), label))

    # ---- events / places
    ev_spans = []
    for phrase, label in sorted((cc.get("events") or {}).items(), key=lambda x: -len(x[0])):
        for m in re.finditer(rf"(?<![\w]){re.escape(phrase.lower())}(?![\w])", low):
            if any(x <= m.start() < y or x < m.end() <= y for x, y in ev_spans):
                continue                      # "meeting at Sandringham" already covers "Sandringham"
            ev_spans.append((m.start(), m.end()))
            found.append(("event", m.start(), label))

    found.sort(key=lambda x: x[1])
    out, last_name, last_date, last_event_label, last_year = [], {}, -1e9, {}, {}
    lower_busy_until = -1e9
    for kind, pos, val in found:
        t = tokens[_token_at(starts, pos)]["start"]
        if kind == "name":
            if t - last_name.get(val, -1e9) < cc.get("name_repeat_sec", 90):
                continue
            info = titles[val]
            dur = cc.get("name_sec", 3.5)
            item = {"kind": "name", "title": info["name"].upper(), "subtitle": info.get("title", "")}
        elif kind == "date":
            if t - last_date < cc.get("date_gap_sec", 12):
                continue
            if val.isdigit() and t - last_year.get(val, -1e9) < cc.get("year_repeat_sec", 300):
                continue                          # bare "2020" again so soon adds nothing
            dur = cc.get("date_sec", 3.0)
            item = {"kind": "date", "title": val, "subtitle": ""}
        else:
            if t - last_event_label.get(val, -1e9) < cc.get("event_repeat_sec", 120):
                continue
            dur = cc.get("event_sec", 3.5)
            item = {"kind": "event", "title": val.upper(), "subtitle": ""}
        if kind in ("name", "event"):           # lower thirds share the same spot: queue, never overlap
            start = max(t, lower_busy_until + 0.3)
            if start - t > 2.5:
                continue
            lower_busy_until = start + dur
        else:
            start = t
        start = min(start, max(duration - dur, 0))
        out.append({**item, "start": round(start, 3), "end": round(start + dur, 3)})
        if kind == "name":
            last_name[val] = t
        elif kind == "date":
            last_date = t
            last_year[val[-4:]] = t
        else:
            last_event_label[val] = t
    return out
