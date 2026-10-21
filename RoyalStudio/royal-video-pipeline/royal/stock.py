"""Stock footage from Pexels for concepts the royal library can't show (courts, hospitals, documents...).

1. find_concepts(): which segments mention a concept from config.yaml -> stock.concepts
2. fetch(): download a few HD clips per concept from Pexels (once; cached in Royal_Media_Library/Stock/<concept>/)
   with a _metadata.json so the licence + author travel with every clip
3. the normal indexer then indexes them (CLIP vectors), and the matcher puts one relevant stock shot
   into segments that talk about that concept (capped, so the video stays about the royals)

Pexels licence: free for commercial use, attribution appreciated but not required.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.pexels.com/videos/search"


def find_concepts(segs: list[dict], concepts: dict) -> dict[int, str]:
    """segment id -> concept name (first concept whose words appear in the segment)."""
    rx = {name: re.compile(r"\b(" + "|".join(re.escape(w) for w in c["words"]) + r")\b", re.I)
          for name, c in concepts.items()}
    out = {}
    for s in segs:
        for name, r in rx.items():
            if r.search(s["text"]):
                out[s["id"]] = name
                break
    return out


def _get(url: str, key: str, timeout=60):
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "RoyalVideoPipeline/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _best_file(video: dict) -> dict | None:
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
    hd = [f for f in files if 1280 <= f["width"] <= 1920 and f["width"] >= f.get("height", 0)]
    if not hd:
        return None
    return max(hd, key=lambda f: f["width"])


def fetch(concept: str, query: str, out_dir: Path, key: str, per_concept: int, log) -> int:
    """Download up to per_concept clips for one concept. Returns how many are available locally."""
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "_metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if len(meta) >= per_concept:
        return len(meta)
    url = API + "?" + urllib.parse.urlencode({"query": query, "per_page": 20, "orientation": "landscape",
                                              "size": "medium"})
    try:
        data = json.loads(_get(url, key))
    except Exception as e:
        log.warning("  Pexels search failed for '%s': %s", query, e)
        return len(meta)
    for v in data.get("videos", []):
        if len(meta) >= per_concept:
            break
        vid = f"pexels-{v['id']}"
        if vid in meta or not (4 <= v.get("duration", 0) <= 60):
            continue
        f = _best_file(v)
        if not f:
            continue
        name = f"{concept}_{v['id']}.mp4"
        try:
            (out_dir / name).write_bytes(_get(f["link"], key, timeout=300))
        except Exception as e:
            log.warning("  download failed %s: %s", vid, e)
            continue
        meta[vid] = {"file": name, "person": "", "source_page": v.get("url"),
                     "license": "Pexels License (free to use)", "author": (v.get("user") or {}).get("name", ""),
                     "description": f"Stock footage: {query}", "date": "", "width": f["width"], "height": f["height"]}
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("  stock: %s  (%s, %ds)", name, meta[vid]["author"], v.get("duration", 0))
        time.sleep(0.3)
    return len(meta)


def prepare_stock(segs: list[dict], cfg: dict, library: Path, log) -> dict[int, str]:
    """Detect concepts, make sure clips exist locally. Returns segment id -> concept."""
    sc = cfg.get("stock", {})
    if not sc.get("enabled", False):
        return {}
    concepts = sc.get("concepts", {})
    seg_concepts = find_concepts(segs, concepts)
    if not seg_concepts:
        return {}
    key = os.getenv("PEXELS_API_KEY", "").strip()
    used = sorted(set(seg_concepts.values()))
    log.info("Stock concepts in this script: %s", ", ".join(used))
    if not key:
        vid_ext = {e.lower() for e in cfg["index"]["video_extensions"]}
        have = [c for c in used if (library / "Stock" / c / "_metadata.json").exists()   # downloaded via the API
                or any(p.suffix.lower() in vid_ext for p in (library / "Stock" / c).glob("*"))]   # or added by hand
        log.warning("No PEXELS_API_KEY in .env - using only stock clips already downloaded (%s)", ", ".join(have) or "none")
        return {sid: c for sid, c in seg_concepts.items() if c in have}
    for c in used:
        fetch(c, concepts[c]["query"], library / "Stock" / c, key, concepts[c].get("count", sc.get("clips_per_concept", 5)), log)
    return seg_concepts
