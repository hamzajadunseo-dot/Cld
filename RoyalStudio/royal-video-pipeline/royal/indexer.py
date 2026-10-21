"""Stage 1 - Media indexer.

Phases
  A  find new / changed / deleted files (size + mtime, then sha1)
  B  per file: size, orientation, faces, CLIP vector, scenes (clips), license metadata
  C  build reference faces per person, then tag people in EVERY asset (cheap, from cache)
  D  Claude captions for assets that don't have one yet (cached by content hash)
  E  export data/index.json + data/index_report.txt
"""
import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from tqdm import tqdm

from .config import Config, pretty_name
from .db import IndexDB
from .logs import get_logger
from .media import (MetadataStore, discover, event_from_folder, grab_frame, load_image,
                    orientation_of, parse_year, probe, quality_score, sha1_of, sharpness,
                    to_jpeg_bytes)

PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}


class Indexer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.data_dir = cfg.path("data_dir")
        self.library = cfg.path("library")
        self.log = get_logger("index", self.data_dir)
        self.db = IndexDB(self.data_dir / "index.db")
        self.meta = MetadataStore()
        self._faces = None
        self._clip = None
        self.errors: list[tuple[str, str]] = []

    # ------------------------------------------------------------ lazy engines
    @property
    def faces(self):
        if self._faces is None and self.cfg.faces["enabled"]:
            from .faces import FaceEngine
            f = self.cfg.faces
            self._faces = FaceEngine(self.cfg.path("models_dir"), f["det_size"], f["det_threshold"], f["min_face_px"])
            self.log.info("Face engine ready (%s)", self._faces.device)
        return self._faces

    @property
    def clip(self):
        if self._clip is None and self.cfg.clip["enabled"]:
            from .clip_embed import ClipEmbedder
            self.log.info("Loading CLIP %s/%s ...", self.cfg.clip["model"], self.cfg.clip["pretrained"])
            self._clip = ClipEmbedder(self.cfg.clip["model"], self.cfg.clip["pretrained"])
            self.log.info("CLIP ready (%s)", self._clip.device.upper())
        return self._clip

    def clip_kind(self) -> str:
        return f"clip:{self.cfg.clip['model']}/{self.cfg.clip['pretrained']}"

    # ------------------------------------------------------------ main
    def run(self, limit: int | None = None, captions: bool = True) -> None:
        t0 = time.time()
        if not self.library.exists():
            self.log.error("Library folder not found: %s (check config.yaml)", self.library)
            return
        self.log.info("Library: %s", self.library)
        files = discover(self.library, self.cfg.index)
        self.log.info("Found %d media files", len(files))

        todo = self._phase_a(files)
        if limit is not None:
            todo = todo[:limit]
        if todo:
            self._phase_b(todo)
        self._phase_c()
        self._group_duplicates()
        if captions and self.cfg.captions["enabled"]:
            self._phase_d()
        else:
            self.log.info("Captions skipped")
        self._phase_e()
        self.log.info("Done in %.1f min", (time.time() - t0) / 60)

    # ------------------------------------------------------------ A: change detection
    def _phase_a(self, files):
        current = {f.rel for f in files}
        removed = self.db.all_file_paths() - current
        for rel in removed:
            self.db.remove_file(rel)
        # a different CLIP model means every vector must be recomputed (faces stay cached)
        version = self.clip_kind() if self.cfg.clip["enabled"] else "none"
        stored = self.db.cache_get("settings", "clip_model")
        if stored != version.encode() and self.db.all_file_paths():
            self.log.info("CLIP model changed -> refreshing all search vectors")
            self.db.conn.execute("UPDATE files SET status='stale'")
        self.db.cache_put("settings", "clip_model", version.encode())
        todo = []
        for f in files:
            st = f.path.stat()
            row = self.db.get_file(f.rel)
            if row and row["size"] == st.st_size and row["mtime"] == st.st_mtime and row["status"] == "ok":
                continue
            todo.append(f)
        self.db.commit()
        self.log.info("New/changed: %d   unchanged: %d   removed: %d",
                      len(todo), len(files) - len(todo), len(removed))
        return todo

    # ------------------------------------------------------------ B: per-file analysis
    def _phase_b(self, todo):
        _ = self.faces, self.clip  # load models before the progress bar starts
        for i, f in enumerate(tqdm(todo, desc="Analysing", unit="file")):
            st = f.path.stat()
            sha1 = None
            try:
                sha1 = sha1_of(f.path)
                if f.kind == "photo":
                    rows = [self._photo_row(f, sha1)]
                elif f.kind == "clip":
                    rows = self._clip_rows(f, sha1)
                else:
                    rows = [self._music_row(f, sha1)]
                self.db.replace_assets_for_path(f.rel, rows)
                self.db.upsert_file(f.rel, sha1, st.st_size, st.st_mtime, f.kind, "ok")
            except Exception as e:  # never crash on one bad file
                msg = f"{type(e).__name__}: {e}"
                self.errors.append((f.rel, msg))
                tqdm.write(f"  SKIPPED {f.rel}: {msg}")
                self.log.debug("skip %s: %s", f.rel, msg)
                self.db.upsert_file(f.rel, sha1, st.st_size, st.st_mtime, f.kind, "error", msg)
            if i % 20 == 0:
                self.db.commit()
        self.db.commit()

    def _base_row(self, f, sha1) -> dict:
        meta = self.meta.lookup(f.path)
        event = year = None
        folder_person = f.folder if f.category == "People" else None
        if f.category == "Events":
            event, year = event_from_folder(f.folder)
        elif f.category == "Clips" and f.folder:
            # Clips are organised like the rest: Clips/<Person>/ or Clips/<Event_Year>/
            if (self.library / "People" / f.folder).is_dir():
                folder_person = f.folder
            elif parse_year(f.folder):
                event, year = event_from_folder(f.folder)
        return {
            "asset_key": f.rel, "path": f.rel, "sha1": sha1,
            "category": f.category,
            "folder_person": folder_person,
            "event": event,
            "year": year or parse_year(meta.get("date")),
            "date": meta.get("date") or None,
            "license": meta.get("license") or None,
            "author": meta.get("author") or None,
            "source_page": meta.get("source_page") or None,
            "source_desc": meta.get("description") or None,
        }

    def _analyse_image(self, rgb, cache_key):
        """Faces (cached) + CLIP vector (cached) for one frame."""
        faces = self.db.cache_get_json("faces", cache_key)
        if faces is None:
            faces = self.faces.analyze(rgb) if self.faces else []
            self.db.cache_put_json("faces", cache_key, faces)
        vec = None
        if self.clip:
            vec = self.db.cache_get_vec(self.clip_kind(), cache_key)
            if vec is None:
                vec = self.clip.images([rgb])[0]
                self.db.cache_put_vec(self.clip_kind(), cache_key, vec)
        return faces, vec

    def _photo_row(self, f, sha1) -> dict:
        rgb, w, h = load_image(f.path, self.cfg.index["work_image_side"])
        faces, vec = self._analyse_image(rgb, f"{sha1}:0")
        row = self._base_row(f, sha1)
        row.update({
            "type": "photo", "shot_index": 0, "width": w, "height": h,
            "orientation": orientation_of(w, h),
            "faces": [{k: v for k, v in fc.items() if k != "emb"} for fc in faces],
            "face_count": len(faces),
            "quality": quality_score(w, h, sharpness(rgb), faces),
            "clip_vec": None if vec is None else np.asarray(vec, np.float16).tobytes(),
        })
        return row

    @staticmethod
    def _frame_key(sha1: str, shot: int, j: int) -> str:
        return f"{sha1}:s2:{shot}:{j}"  # s2 = single-pass scanner shot numbering

    def _clip_rows(self, f, sha1) -> list[dict]:
        info = probe(f.path)
        dur = info.get("duration", 0.0)
        if not dur or "width" not in info:
            raise ValueError("unreadable video (no duration/stream)")
        sc = self.cfg.scenes
        n_frames = max(1, self.cfg.faces["frames_per_shot"])
        scan_key = (f"{sha1}:{sc['sample_fps']}:{sc['scan_width']}:{sc['threshold']}:"
                    f"{sc['min_shot_sec']}:{sc['max_shot_sec']}:{n_frames}")
        shots = self.db.cache_get_json("scan", scan_key)
        complete = shots is not None and all(
            self.db.cache_get("faces", self._frame_key(sha1, i, j)) is not None
            and (not self.clip or self.db.cache_get(self.clip_kind(), self._frame_key(sha1, i, j)) is not None)
            for i in range(len(shots)) for j in range(n_frames))
        if not complete:
            from .scenes import scan_video
            shots = []
            for idx, (a, b, frames, sharp) in enumerate(scan_video(
                    f.path, info["width"], info["height"], sc["sample_fps"], sc["scan_width"], sc["threshold"],
                    sc["min_shot_sec"], sc["max_shot_sec"], n_frames)):
                for j, fr in enumerate(frames):
                    self._analyse_image(fr, self._frame_key(sha1, idx, j))
                shots.append([a, b, sharp])
                if idx % 25 == 0:
                    self.db.commit()
            self.db.cache_put_json("scan", scan_key, shots)
            tqdm.write(f"  {f.rel}: {len(shots)} shots from {dur / 60:.1f} min")

        rows = []
        for idx, (start, end, shot_sharp) in enumerate(shots):
            all_faces, vecs, sharp = [], [], [shot_sharp]
            for j in range(n_frames):
                t = start + (end - start) * (j + 1) / (n_frames + 1)
                key = self._frame_key(sha1, idx, j)
                faces = self.db.cache_get_json("faces", key) or []
                vec = self.db.cache_get_vec(self.clip_kind(), key) if self.clip else None
                all_faces += [{**fc, "t": round(t, 2)} for fc in faces]
                if vec is not None:
                    vecs.append(vec)
            vec = None
            if vecs:
                vec = np.mean(vecs, axis=0)
                vec = vec / (np.linalg.norm(vec) + 1e-9)
            row = self._base_row(f, sha1)
            row.update({
                "asset_key": f"{f.rel}#shot{idx:03d}", "type": "clip", "shot_index": idx,
                "start_sec": start, "end_sec": end, "duration": round(end - start, 3),
                "width": info["width"], "height": info["height"],
                "orientation": orientation_of(info["width"], info["height"]),
                "faces": [{k: v for k, v in fc.items() if k != "emb"} for fc in all_faces],
                "face_count": max((sum(1 for fc in all_faces if fc["t"] == t) for t in {fc["t"] for fc in all_faces}), default=0),
                "quality": quality_score(info["width"], info["height"], float(np.mean(sharp)) if sharp else 150.0, all_faces),
                "clip_vec": None if vec is None else np.asarray(vec, np.float16).tobytes(),
            })
            rows.append(row)
            self.db.commit()  # long videos: don't hold the database locked for the whole file
        return rows

    def _music_row(self, f, sha1) -> dict:
        info = probe(f.path)
        row = self._base_row(f, sha1)
        mood = f.folder.lower() if f.folder else f.path.stem.split("_")[0].lower()
        row.update({"type": "music", "shot_index": 0, "duration": info.get("duration"),
                    "mood": mood, "tags": [t.lower() for t in f.path.stem.replace("-", "_").split("_") if t]})
        return row

    # ------------------------------------------------------------ C: people tagging
    def _face_list(self, a) -> list[dict]:
        """Full face records (with embeddings) for an asset, from the cache."""
        if a["type"] == "photo":
            return self.db.cache_get_json("faces", f"{a['sha1']}:0") or []
        out = []
        for j in range(max(1, self.cfg.faces["frames_per_shot"])):
            out += self.db.cache_get_json("faces", self._frame_key(a["sha1"], a["shot_index"], j)) or []
        return out

    def _phase_c(self):
        from .faces import FaceMatcher, build_references
        if not self.cfg.faces["enabled"]:
            return
        visuals = self.db.assets("type IN ('photo','clip')")
        person_faces = defaultdict(list)
        for a in visuals:
            if a["category"] == "People" and a["type"] == "photo" and a["folder_person"]:
                person_faces[a["folder_person"]].append(self._face_list(a))
        refs = build_references(person_faces)
        self.ref_stats = {p: (len(person_faces[p]), len(refs.get(p, []))) for p in person_faces}
        for p, (n_photos, n_refs) in sorted(self.ref_stats.items()):
            self.log.info("Reference faces  %-22s %4d trusted from %4d photos", p, n_refs, n_photos)
        np.savez_compressed(self.data_dir / "face_refs.npz", **refs)

        f = self.cfg.faces
        matcher = FaceMatcher(refs, f["match_threshold"], f["match_margin"])
        for a in tqdm(visuals, desc="Tagging people", unit="asset"):
            faces_full = self._face_list(a)
            faces_out, scores = [], {}
            for fc, public in zip(faces_full, a["faces"] or []):
                name, s = matcher.identify(fc["emb"])
                public = {**public, "person": name, "sim": round(s, 3)}
                faces_out.append(public)
                if name:
                    scores[name] = max(scores.get(name, 0.0), round(s, 3))
            people = sorted(scores, key=lambda n: -scores[n])
            # folder tag is only a fallback when no face could be recognised at all
            if not people and a["folder_person"]:
                people, scores = [a["folder_person"]], {a["folder_person"]: 0.0}
            fields = {"people": people, "people_scores": scores, "faces": faces_out}
            if not a["caption"]:
                fields["shot_type"] = heuristic_shot(faces_out)
            self.db.update_asset(a["id"], **fields)
        self.db.commit()

    def _group_duplicates(self):
        """Cluster near-identical visuals (burst shots, crops) so Stage 4 treats them as one."""
        thr = self.cfg.index.get("duplicate_similarity", 0.93)
        assets = [a for a in self.db.assets("type IN ('photo','clip')") if a["clip_vec"] is not None]
        if not assets:
            return
        V = np.stack([a["clip_vec"] for a in assets])
        V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9
        parent = list(range(len(assets)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(0, len(V), 512):  # blockwise so big libraries fit in memory
            S = V[i:i + 512] @ V.T
            for r, c in zip(*np.where(S >= thr)):
                a, b = find(i + r), find(c)
                if a != b:
                    parent[max(a, b)] = min(a, b)
        roots = [find(i) for i in range(len(assets))]
        for a, root in zip(assets, roots):
            self.db.conn.execute("UPDATE assets SET dup_group=? WHERE id=?", (assets[root]["id"], a["id"]))
        self.db.commit()
        n_groups = len(set(roots))
        self.dup_stats = (len(assets), n_groups)
        self.log.info("Near-duplicates: %d visuals form %d distinct groups", len(assets), n_groups)

    # ------------------------------------------------------------ D: captions
    def _phase_d(self):
        from .captions import Captioner
        model = self.cfg.captions["model"]
        kind = f"caption:{model}"
        pending = self.db.assets("type IN ('photo','clip') AND caption IS NULL")
        uncached = []
        for a in pending:
            data = self.db.cache_get_json(kind, f"{a['sha1']}:{a['shot_index']}")
            if data:
                self._apply_caption(a, data)
            else:
                uncached.append(a)
        self.db.commit()
        if not uncached:
            self.log.info("Captions: all %d assets already captioned", len(pending))
            return
        try:
            cap = Captioner(model)
        except RuntimeError as e:
            self.log.warning("Captions skipped: %s", e)
            return

        self.log.info("Captioning %d assets with %s ...", len(uncached), model)
        tok_in = tok_out = done = 0

        def job(a):
            rgb = self._caption_image(a)
            if rgb is None:
                return a, None
            ctx = {
                "folder": f"{a['category']}/{pretty_name(a['folder_person'] or '')}".rstrip("/"),
                "file title": a["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0],
                "source description": a["source_desc"],
                "source date": a["date"],
                "event folder": a["event"] if a["category"] == "Events" else None,
                "asset": "still frame from a video clip" if a["type"] == "clip" else None,
            }
            return a, cap.describe(to_jpeg_bytes(rgb, self.cfg.captions["max_image_side"]), ctx)

        with ThreadPoolExecutor(self.cfg.captions["workers"]) as pool:
            futures = [pool.submit(job, a) for a in uncached]
            for fut in tqdm(as_completed(futures), total=len(futures), desc="Captioning", unit="asset"):
                try:
                    a, data = fut.result()
                except Exception as e:
                    if type(e).__name__ == "AuthenticationError":
                        self.log.error("Anthropic API key rejected - check .env. Stopping captions.")
                        pool.shutdown(cancel_futures=True)
                        break
                    self.log.warning("  caption failed: %s", e)
                    continue
                if not data:
                    continue
                usage = data.pop("_usage", {})
                tok_in += usage.get("in", 0)
                tok_out += usage.get("out", 0)
                self.db.cache_put_json(kind, f"{a['sha1']}:{a['shot_index']}", data)
                self._apply_caption(a, data)
                done += 1
                if done % 10 == 0:
                    self.db.commit()
        self.db.commit()
        p_in, p_out = PRICES.get(model, (1.0, 5.0))
        self.log.info("Captioned %d assets  (%d in / %d out tokens, ~$%.3f)",
                      done, tok_in, tok_out, tok_in / 1e6 * p_in + tok_out / 1e6 * p_out)

    def _caption_image(self, a):
        path = self.library / a["path"]
        side = self.cfg.captions["max_image_side"]
        if a["type"] == "photo":
            return load_image(path, side)[0]
        return grab_frame(path, (a["start_sec"] + a["end_sec"]) / 2, side)

    def _apply_caption(self, a, d):
        fields = {
            "caption": d.get("caption") or "",
            "location": d.get("location") or None,
            "mood": d.get("mood"),
            "shot_type": d.get("shot_type"),
            "image_kind": d.get("image_kind"),
            "tags": d.get("tags") or [],
        }
        if a["category"] != "Events" and d.get("event"):
            fields["event"] = d["event"]
        if a["category"] != "Events" and d.get("year_hint", 0) > 1850:
            fields["year"] = d["year_hint"]
        self.db.update_asset(a["id"], **fields)

    # ------------------------------------------------------------ E: export + report
    def _phase_e(self):
        assets = self.db.assets()
        export = []
        for a in assets:
            a.pop("clip_vec", None)
            export.append(a)
        (self.data_dir / "index.json").write_text(json.dumps(export, indent=1, ensure_ascii=False), encoding="utf-8")

        lines = [f"Royal Media Library index report - {time.strftime('%Y-%m-%d %H:%M')}", ""]
        by_type = Counter(a["type"] for a in assets)
        lines.append("Assets: " + ", ".join(f"{n} {t}s" for t, n in sorted(by_type.items())))
        lines.append(f"Captioned: {sum(1 for a in assets if a['caption'])}/{sum(by_type[t] for t in ('photo', 'clip'))}")
        if hasattr(self, "dup_stats"):
            lines.append(f"Distinct visuals after merging near-duplicates: {self.dup_stats[1]} of {self.dup_stats[0]}")
        lines.append("")

        lines.append("PEOPLE (assets where the person was recognised by face)")
        tagged = Counter()
        folder_only = Counter()
        for a in assets:
            for p in a["people"] or []:
                if (a["people_scores"] or {}).get(p, 0) > 0:
                    tagged[p] += 1
                else:
                    folder_only[p] += 1
        stats = getattr(self, "ref_stats", {})
        for p in sorted(set(tagged) | set(stats) | set(folder_only)):
            n_photos, n_refs = stats.get(p, (0, 0))
            lines.append(f"  {pretty_name(p):24s} recognised in {tagged[p]:4d} assets | folder-only {folder_only[p]:4d} "
                         f"| references {n_refs}/{n_photos}")
        lines.append("")

        mism = [a for a in assets if a["category"] == "People" and a["folder_person"]
                and a["people"] and a["folder_person"] not in a["people"]]
        lines.append(f"FOLDER MISMATCHES ({len(mism)}) - in a person's folder but only OTHER people recognised:")
        for a in mism:
            lines.append(f"  {a['path']}  ->  {', '.join(a['people'])}")
        lines.append("")

        nolic = [a for a in assets if not a["license"]]
        lines.append(f"MISSING LICENSE ({len(nolic)}) - add a _metadata.json entry before using these:")
        for a in nolic[:200]:
            lines.append(f"  {a['path']}")
        lines.append("")

        errs = self.db.conn.execute("SELECT path, error FROM files WHERE status='error'").fetchall()
        lines.append(f"ERRORS ({len(errs)}) - skipped files:")
        for path, err in errs:
            lines.append(f"  {path}: {err}")

        report = self.data_dir / "index_report.txt"
        report.write_text("\n".join(lines), encoding="utf-8")
        self.log.info("Wrote %s and %s", self.data_dir / "index.json", report)


def heuristic_shot(faces: list[dict]) -> str:
    if not faces:
        return "wide"
    per_frame = max(Counter(f.get("t") for f in faces).values())  # clips: faces come from several frames
    if per_frame >= 6:
        return "crowd"
    if per_frame >= 3:
        return "group"
    h = max(f["bbox"][3] - f["bbox"][1] for f in faces)
    return "close-up" if h > 0.28 else "medium" if h > 0.12 else "wide"
