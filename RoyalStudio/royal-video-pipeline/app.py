r"""Royal Video Studio - web app for the royal video pipeline.

    Royal Video Studio.bat          -> only this PC     (http://127.0.0.1:5077)
    Royal Video Studio (Team).bat   -> team can connect (http://<this-pc>:5077, login required)

New video (script + voiceover + whose video) / replace the hook / add the locked intro clip /
upload + index library media / download the finished files.
Jobs run one after another from a queue (the GPU is shared). On this PC nobody has to log in;
every other device needs a team account (Team tab, only on this PC).
"""
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from royal.config import load_config  # noqa: E402

CFG = load_config(None)
LIBRARY = CFG.path("library")
PROJECTS = ROOT / "projects"
PROJECTS.mkdir(exist_ok=True)
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
USERS_FILE = DATA / "studio_users.json"
JOBS_FILE = DATA / "studio_jobs.json"
PY = sys.executable
PORT = 5077
TEAM_MODE = os.environ.get("STUDIO_TEAM") == "1"
OWNER = "Owner"
MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a"}

app = Flask(__name__, static_folder=None)
key_file = DATA / "studio_secret.key"
if not key_file.exists():
    key_file.write_text(secrets.token_hex(32), encoding="utf-8")
app.secret_key = key_file.read_text(encoding="utf-8").strip()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30))


# ------------------------------------------------------------------ users
def load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {}


def save_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, indent=1), encoding="utf-8")


def kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def open_local(target: Path):
    """Open a file or folder in Explorer / Finder."""
    if os.name == "nt":
        os.startfile(str(target))  # noqa: S606 - local desktop tool, owner only
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])


def is_local() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1")


def current_user() -> str | None:
    if is_local():
        return OWNER
    name = session.get("user")
    return name if name and name in load_users() else None


FAILS: dict = {}   # ip -> (count, first_fail_time)


@app.before_request
def guard():
    if request.path in ("/login", "/api/login") or current_user():
        return None
    if request.path.startswith(("/api/", "/files/")):
        return jsonify(error="Please log in."), 401
    return redirect("/login")


@app.get("/login")
def login_page():
    return send_from_directory(ROOT / "studio", "login.html")


@app.post("/api/login")
def api_login():
    ip = request.remote_addr
    n, t0 = FAILS.get(ip, (0, time.time()))
    if n >= 5 and time.time() - t0 < 600:
        return jsonify(error="Too many wrong passwords - try again in 10 minutes."), 429
    name, pw = request.form.get("name", "").strip(), request.form.get("password", "")
    u = load_users().get(name)
    if not u or not check_password_hash(u["hash"], pw):
        time.sleep(1)
        FAILS[ip] = (n + 1, t0 if n else time.time())
        return jsonify(error="Wrong name or password."), 401
    FAILS.pop(ip, None)
    session.permanent = True
    session["user"] = name
    return jsonify(ok=True)


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def api_me():
    return jsonify(user=current_user(), local=is_local(), team_mode=TEAM_MODE)


@app.get("/api/team")
def api_team():
    if not is_local():
        abort(403)
    return jsonify([{"name": k, "created": v.get("created", "")} for k, v in sorted(load_users().items())])


@app.post("/api/team/add")
def api_team_add():
    if not is_local():
        abort(403)
    name = re.sub(r"[^A-Za-z0-9 _-]", "", request.form.get("name", "")).strip()[:30]
    pw = request.form.get("password", "")
    if not name or name.lower() == OWNER.lower():
        return jsonify(error="Enter a name (letters / numbers)."), 400
    if len(pw) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400
    users = load_users()
    users[name] = {"hash": generate_password_hash(pw), "created": time.strftime("%d %b %Y")}
    save_users(users)
    return jsonify(ok=True)


@app.post("/api/team/remove")
def api_team_remove():
    if not is_local():
        abort(403)
    users = load_users()
    users.pop(request.form.get("name", ""), None)
    save_users(users)
    return jsonify(ok=True)


# ------------------------------------------------------------------ job queue
JOBS: list[dict] = []
QLOCK = threading.Lock()
WAKE = threading.Event()
RUNNING: dict = {}


def persist_jobs():
    keep = [{k: v for k, v in j.items() if k != "cmd"} for j in JOBS[-40:]]
    JOBS_FILE.write_text(json.dumps(keep, indent=0), encoding="utf-8")


def queue_job(kind: str, project: str, cmd: list[str]) -> dict:
    with QLOCK:
        job = {"id": secrets.token_hex(4), "kind": kind, "project": project, "user": current_user(),
               "cmd": cmd, "status": "queued", "created": time.time(), "started": None, "ended": None,
               "code": None,
               "log": str((PROJECTS / project / "studio_log.txt") if project else (DATA / "studio_log.txt"))}
        JOBS.append(job)
        persist_jobs()
    WAKE.set()
    return job


def worker():
    while True:
        WAKE.wait(2)
        WAKE.clear()
        with QLOCK:
            job = next((j for j in JOBS if j["status"] == "queued"), None)
            if job:
                job.update(status="running", started=time.time())
                persist_jobs()
        if not job:
            continue
        log = Path(job["log"])
        log.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        with open(log, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(f"Started by {job['user']}\n")
            fh.flush()
            proc = subprocess.Popen(job["cmd"], cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, env=env,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                    start_new_session=os.name != "nt")   # own process group, so Stop kills ffmpeg too
            RUNNING.update(proc=proc, id=job["id"])
            code = proc.wait()
        RUNNING.clear()
        with QLOCK:
            job.update(code=code, ended=time.time(),
                       status="stopped" if job.get("stopping") else ("done" if code == 0 else "failed"))
            persist_jobs()


def clean_log(raw: str) -> tuple[list[str], int | None]:
    lines, progress = [], None
    for chunk in raw.replace("\r", "\n").split("\n"):
        s = chunk.strip()
        if not s or "HF_TOKEN" in s or "huggingface_hub" in s:
            continue
        m = re.search(r"^(Rendering|Tagging people|Scanning|Indexing)[^:]*:\s*(\d+)%", s)
        if m:
            if m.group(1) == "Rendering":
                progress = int(m.group(2))
            continue
        lines.append(re.sub(r"^\d\d:\d\d:\d\d\s+(INFO|WARNING|ERROR)\s+", "", s))
    return lines, progress


def public(j: dict) -> dict:
    return {k: j[k] for k in ("id", "kind", "project", "user", "status", "created", "started", "ended", "code")}


@app.get("/api/status")
def api_status():
    with QLOCK:
        running = next((j for j in JOBS if j["status"] == "running"), None)
        queued = [public(j) for j in JOBS if j["status"] == "queued"]
        recent = [public(j) for j in JOBS if j["status"] in ("done", "failed", "stopped")][-6:][::-1]
    out = {"queued": queued, "recent": recent, "running": None}
    focus = running or (JOBS[-1] if JOBS else None)
    if focus:
        try:
            raw = Path(focus["log"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        lines, progress = clean_log(raw)
        out["focus"] = {**public(focus), "log": lines[-60:], "progress": progress,
                        "elapsed": int((focus["ended"] or time.time()) - (focus["started"] or time.time()))}
        if running:
            out["running"] = focus["id"]
    return jsonify(out)


@app.post("/api/stop")
def api_stop():
    jid = request.form.get("id", "")
    with QLOCK:
        job = next((j for j in JOBS if j["id"] == jid), None)
        if not job:
            return jsonify(error="Job not found."), 404
        if current_user() not in (OWNER, job["user"]):
            return jsonify(error="Only the person who started it (or the owner) can stop it."), 403
        if job["status"] == "queued":
            job["status"] = "stopped"
            persist_jobs()
            return jsonify(ok=True)
        if job["status"] == "running" and RUNNING.get("id") == jid:
            job["stopping"] = True
            kill_tree(RUNNING["proc"])
            return jsonify(ok=True)
    return jsonify(error="That job is already finished."), 400


# ------------------------------------------------------------------ scripts / projects
def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s[:60] or f"video_{int(time.time())}"


def project_dir(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_]{1,60}", name or "") or not (PROJECTS / name).is_dir():
        abort(404)
    return PROJECTS / name


CHAPTER = re.compile(r"^\s*(#+\s*)?(chapter|part)\b", re.I)


def prep_script(text: str) -> str:
    """Pasted script -> pipeline layout: '## HOOK' first, '---' between sections.
    Hook = everything before the first CHAPTER line; without chapter lines, the first block
    (paragraph group) is the hook and every blank line starts a new section."""
    text = text.replace("\r", "")
    if re.search(r"^\s*#+\s*hook\b", text, re.I | re.M):
        return text
    lines = text.split("\n")
    is_chap = [bool(CHAPTER.match(l)) and len(l.split()) <= 14 for l in lines]
    out = ["## HOOK", ""]
    if any(is_chap):
        for l, c in zip(lines, is_chap):
            out += ["", "---", ""] if c else [l]
    else:
        blank = False
        for l in lines:
            if not l.strip():
                blank = True
                continue
            if blank and len(out) > 2:
                out += ["", "---", ""]
            blank = False
            out.append(l)
    return "\n".join(out).strip() + "\n"


def preview(path: Path) -> dict:
    from royal.transcribe import parse_script
    spoken, hook_words = parse_script(path)
    words = spoken.split()
    hook = words[:hook_words]
    return {"words": len(words), "minutes": round(len(words) / 2.6 / 60, 1),
            "hook_words": hook_words, "hook_seconds": round(hook_words / 2.6),
            "hook_start": " ".join(hook[:18]), "hook_end": " ".join(hook[-18:]),
            "sections": len(getattr(parse_script, "sections", [])) + 1}


def people() -> list[str]:
    d = LIBRARY / "People"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


def project_info(p: Path) -> dict:
    mp4 = p / f"{p.name}.mp4"
    work = p / f"{p.name}_project"
    meta = {}
    if (p / "project.json").exists():
        meta = json.loads((p / "project.json").read_text(encoding="utf-8"))
    return {"name": p.name, "subject": meta.get("subject", ""), "user": meta.get("user", ""),
            "video": mp4.exists(), "size_mb": round(mp4.stat().st_size / 1e6) if mp4.exists() else 0,
            "final": (p / f"{p.name}_final.mp4").exists(),
            "updated": time.strftime("%d %b %H:%M", time.localtime((mp4 if mp4.exists() else p).stat().st_mtime)),
            "xml": (work / f"{p.name}.xml").exists(), "credits": (work / "credits.txt").exists(),
            "missing": (work / "missing_assets.txt").exists()}


def save_upload(f, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    f.save(str(dest))
    return dest


def ext_of(f, allowed: set[str], default: str) -> str:
    e = Path(f.filename or "").suffix.lower()
    return e if e in allowed else default


@app.get("/")
def index():
    return send_from_directory(ROOT / "studio", "index.html")


@app.get("/api/people")
def api_people():
    return jsonify(people())


@app.get("/api/projects")
def api_projects():
    ps = [p for p in PROJECTS.iterdir() if p.is_dir() and re.fullmatch(r"[a-z0-9_]{1,60}", p.name)]
    ps.sort(key=lambda p: max((x.stat().st_mtime for x in p.iterdir()), default=0), reverse=True)
    return jsonify([project_info(p) for p in ps])


@app.post("/api/preview")
def api_preview():
    f = request.files.get("script_file")
    with tempfile.TemporaryDirectory() as td:
        if f and f.filename:
            path = Path(td) / ("script" + ext_of(f, {".txt", ".docx"}, ".txt"))
            f.save(str(path))
            if path.suffix == ".txt":
                path.write_text(prep_script(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        else:
            text = request.form.get("script_text", "")
            if not text.strip():
                return jsonify(error="Paste the script or choose a file first."), 400
            path = Path(td) / "script.txt"
            path.write_text(prep_script(text), encoding="utf-8")
        try:
            return jsonify(preview(path))
        except Exception as e:  # noqa: BLE001
            return jsonify(error=f"Could not read the script: {e}"), 400


@app.post("/api/new")
def api_new():
    name = slug(request.form.get("name", ""))
    subject = ",".join(p for p in request.form.get("subject", "").split(",") if p in people())
    vo = request.files.get("vo")
    if not vo or not vo.filename:
        return jsonify(error="Choose the voiceover file."), 400
    if not subject:
        return jsonify(error="Choose whose video it is."), 400
    proj = PROJECTS / name
    if proj.exists() and any(proj.iterdir()):
        return jsonify(error=f"A project called '{name}' already exists - pick another name."), 400
    proj.mkdir(parents=True, exist_ok=True)
    f = request.files.get("script_file")
    if f and f.filename:
        script = save_upload(f, proj / ("script" + ext_of(f, {".txt", ".docx"}, ".txt")))
        if script.suffix == ".txt":
            script.write_text(prep_script(script.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    else:
        text = request.form.get("script_text", "")
        if not text.strip():
            return jsonify(error="Paste the script or choose a script file."), 400
        script = proj / "script.txt"
        script.write_text(prep_script(text), encoding="utf-8")
    vo_path = save_upload(vo, proj / ("vo" + ext_of(vo, AUDIO_EXT, ".mp3")))
    (proj / "project.json").write_text(json.dumps({"subject": subject, "user": current_user()}), encoding="utf-8")
    cmd = [PY, "-u", "make_video.py", "--script", str(script), "--voiceover", str(vo_path),
           "--out", str(proj / f"{name}.mp4"), "--subject", subject]
    if request.form.get("no_music") == "1":
        cmd.append("--no-music")
    job = queue_job("New video", name, cmd)
    return jsonify(ok=True, project=name, id=job["id"])


@app.post("/api/replace_hook")
def api_replace_hook():
    proj = project_dir(request.form.get("project", ""))
    hook = request.files.get("hook")
    if not hook or not hook.filename:
        return jsonify(error="Choose the new hook voiceover."), 400
    subject = ",".join(p for p in (request.form.get("subject") or project_info(proj)["subject"]).split(",")
                       if p in people())
    path = save_upload(hook, proj / ("new_hook" + ext_of(hook, AUDIO_EXT, ".mp3")))
    cmd = [PY, "-u", "replace_hook.py", "--project", str(proj), "--hook", str(path)]
    if subject:
        cmd += ["--subject", subject]
    job = queue_job("Replace hook", proj.name, cmd)
    return jsonify(ok=True, id=job["id"])


@app.post("/api/intro")
def api_intro():
    proj = project_dir(request.form.get("project", ""))
    clip = request.files.get("clip")
    main = proj / f"{proj.name}.mp4"
    if not main.exists() or not clip or not clip.filename:
        return jsonify(error="Choose a finished project and the intro clip."), 400
    path = save_upload(clip, proj / ("intro_clip" + ext_of(clip, MEDIA_EXT, ".mp4")))
    cmd = [PY, "-u", "intro_hook.py", "--clip", str(path), "--main", str(main),
           "--tag", request.form.get("tag", "")[:80], "--out", str(proj / f"{proj.name}_final.mp4")]
    for k in ("crop", "start", "end"):
        v = request.form.get(k, "").strip()
        if v and re.fullmatch(r"[\d.,]+", v):
            cmd += [f"--{k}", v]
    job = queue_job("Intro clip", proj.name, cmd)
    return jsonify(ok=True, id=job["id"])


@app.post("/api/index")
def api_index():
    job = queue_job("Index library", "", [PY, "-u", "index_library.py"])
    return jsonify(ok=True, id=job["id"])


@app.post("/api/library/upload")
def api_library_upload():
    person = re.sub(r"[^A-Za-z0-9_]", "", request.form.get("person", "").strip().replace(" ", "_"))[:40]
    files = request.files.getlist("files")
    if not person or not files:
        return jsonify(error="Choose the person and the files."), 400
    dest = LIBRARY / "People" / person
    saved = 0
    for f in files:
        e = Path(f.filename or "").suffix.lower()
        if e not in MEDIA_EXT:
            continue
        base = secure_filename(Path(f.filename).stem) or f"upload_{int(time.time())}"
        target = dest / f"{base}{e}"
        n = 1
        while target.exists():
            target = dest / f"{base}_{n}{e}"
            n += 1
        save_upload(f, target)
        saved += 1
    if not saved:
        return jsonify(error="No video/photo files (mp4, mov, jpg, png...) in the selection."), 400
    if request.form.get("index") == "1":
        queue_job("Index library", "", [PY, "-u", "index_library.py"])
    return jsonify(ok=True, saved=saved, person=person)


@app.post("/api/open")
def api_open():
    if not is_local():
        return jsonify(error="Use the download links on other computers."), 403
    proj = project_dir(request.form.get("project", ""))
    what = request.form.get("what", "folder")
    target = {"video": proj / f"{proj.name}.mp4", "final": proj / f"{proj.name}_final.mp4",
              "missing": proj / f"{proj.name}_project" / "missing_assets.txt"}.get(what, proj)
    if not target.exists():
        return jsonify(error="File not found."), 404
    open_local(target)
    return jsonify(ok=True)


@app.post("/api/open_library")
def api_open_library():
    if not is_local():
        return jsonify(error="Only on the studio PC."), 403
    sub = request.form.get("sub", "")
    target = LIBRARY / sub if sub in ("People", "Music", "SFX", "Events", "Stock") else LIBRARY
    open_local(target)
    return jsonify(ok=True)


@app.get("/files/<name>/<kind>")
def files(name, kind):
    proj = project_dir(name)
    path = {"video": proj / f"{name}.mp4", "final": proj / f"{name}_final.mp4",
            "xml": proj / f"{name}_project" / f"{name}.xml",
            "credits": proj / f"{name}_project" / "credits.txt",
            "missing": proj / f"{name}_project" / "missing_assets.txt"}.get(kind)
    if not path or not path.exists():
        abort(404)
    return send_file(str(path), as_attachment=request.args.get("dl") == "1" or kind in ("xml", "credits"),
                     conditional=True)


if __name__ == "__main__":
    for j in json.loads(JOBS_FILE.read_text(encoding="utf-8")) if JOBS_FILE.exists() else []:
        if j.get("status") in ("done", "failed", "stopped"):
            JOBS.append({**j, "cmd": []})                 # history only; unfinished jobs are dropped
    threading.Thread(target=worker, daemon=True).start()
    host = "0.0.0.0" if TEAM_MODE else "127.0.0.1"
    if not os.environ.get("STUDIO_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    print(f"Royal Video Studio on http://127.0.0.1:{PORT}" +
          ("  - TEAM MODE: team members connect with their login" if TEAM_MODE else "  (this PC only)"))
    print(f"Team accounts: {', '.join(load_users()) or 'none yet - add them in the Team tab'}")
    print("Close this window to stop the studio.")
    app.run(host=host, port=PORT, debug=False, threaded=True)
