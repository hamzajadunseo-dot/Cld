"""Test the index: search the library by meaning and/or person, see results as a picture sheet.

    python search_library.py "sad moment at a funeral"
    python search_library.py "waving from a balcony" --person King_Charles
    python search_library.py --person Prince_William --top 40
    python search_library.py "crowd cheering" --type clip

Opens data/search_results.html in your browser.
"""
import argparse
import html
import os
import webbrowser
from pathlib import Path

import numpy as np

from royal.config import load_config, pretty_name
from royal.db import IndexDB
from royal.media import grab_frame, to_jpeg_bytes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", default="", help="what you're looking for, in plain English")
    ap.add_argument("--person", help="folder name, e.g. King_Charles")
    ap.add_argument("--type", choices=["photo", "clip"], help="only photos or only clips")
    ap.add_argument("--top", type=int, default=24)
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = ap.parse_args()

    cfg = load_config()
    data_dir = cfg.path("data_dir")
    library = cfg.path("library")
    db = IndexDB(data_dir / "index.db")

    where, params = "type IN ('photo','clip')", []
    if args.type:
        where, params = "type=?", [args.type]
    assets = db.assets(where, params)
    if args.person:
        assets = [a for a in assets if args.person in (a["people"] or [])]
    if not assets:
        print("No matching assets.")
        return

    if args.query:
        from royal.clip_embed import ClipEmbedder
        clip = ClipEmbedder(cfg.clip["model"], cfg.clip["pretrained"])
        q = clip.text([args.query])[0]
        for a in assets:
            a["_score"] = float(a["clip_vec"] @ q) if a["clip_vec"] is not None else -1.0
    else:
        for a in assets:
            a["_score"] = (a["quality"] or 0) + (a["people_scores"] or {}).get(args.person or "", 0)
    assets.sort(key=lambda a: -a["_score"])
    top = assets[: args.top]

    thumbs = data_dir / "thumbs"
    thumbs.mkdir(exist_ok=True)
    cards = []
    for rank, a in enumerate(top, 1):
        src = library / a["path"]
        if a["type"] == "clip":
            t = thumbs / f"{a['sha1']}_{a['shot_index']}.jpg"
            if not t.exists():
                frame = grab_frame(src, (a["start_sec"] + a["end_sec"]) / 2, 480)
                if frame is not None:
                    t.write_bytes(to_jpeg_bytes(frame, 480))
            img_url = t.as_uri()
            span = f"clip {a['start_sec']:.1f}-{a['end_sec']:.1f}s"
        else:
            img_url = src.as_uri()
            span = f"{a['width']}x{a['height']}"
        people = ", ".join(f"{pretty_name(p)} ({(a['people_scores'] or {}).get(p, 0):.2f})" for p in a["people"] or []) or "-"
        print(f"{rank:2d}. {a['_score']:.3f}  {a['path']}  [{people}]")
        cards.append(f"""
<div class="card"><img loading="lazy" src="{html.escape(img_url)}">
<div class="meta"><b>#{rank}</b> score {a['_score']:.3f} &middot; {html.escape(span)} &middot; {html.escape(a['shot_type'] or '')} &middot; {html.escape(a['mood'] or '')}
<div class="people">{html.escape(people)}</div>
<div>{html.escape(a['caption'] or '(no caption yet)')}</div>
<div class="small">{html.escape(a['event'] or '')} {a['year'] or ''} {html.escape(a['location'] or '')}</div>
<div class="small">{html.escape(a['license'] or 'NO LICENSE')} &middot; {html.escape((a['author'] or '')[:60])}</div>
<div class="small path">{html.escape(a['path'])}</div></div></div>""")

    title = html.escape(f"{args.query or 'best'}" + (f" / {args.person}" if args.person else ""))
    page = f"""<!doctype html><meta charset="utf-8"><title>Search: {title}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#111;color:#eee;margin:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}}
.card{{background:#1d1d1d;border-radius:8px;overflow:hidden}}
.card img{{width:100%;height:200px;object-fit:cover;display:block;background:#000}}
.meta{{padding:8px;font-size:13px;line-height:1.4}} .people{{color:#f5c542;font-weight:600}}
.small{{color:#999;font-size:11px}} .path{{word-break:break-all}}
</style><h2>Search: {title} &nbsp;<small>({len(assets)} candidates)</small></h2><div class="grid">{''.join(cards)}</div>"""
    out = data_dir / "search_results.html"
    out.write_text(page, encoding="utf-8")
    print(f"\nResults sheet: {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()
