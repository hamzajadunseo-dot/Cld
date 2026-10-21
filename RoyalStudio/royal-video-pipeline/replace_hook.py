r"""Swap a finished video's HOOK for a newly recorded one, then re-render.

    .venv\Scripts\python.exe replace_hook.py --project projects\harry_document --hook new_hook.mp3 --subject Prince_Harry

1. The old hook ends where the last hook word of segments.json ends (cut on the pause before the next word).
2. New voiceover = new hook + 0.4 s pause + old voiceover from that cut.
3. New script   = '## HOOK' + whisper transcript of the new hook + '---' + the old script after its hook.
4. The old vo / script / mp4 are kept as *_v<N>_oldhook.* and the full video is rendered again.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from royal.transcribe import _cuda_dlls  # noqa: E402


def transcribe(mp3: Path) -> str:
    _cuda_dlls()
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    except Exception:
        m = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(str(mp3), language="en")
    text = " ".join(s.text.strip() for s in segs)
    return re.sub(r'(?<=[.?!"”])\s+(?=[A-Z“"])', "\n", text)


def backup(p: Path) -> Path:
    n = 1
    while True:
        b = p.with_name(f"{p.stem}_v{n}_oldhook{p.suffix}")
        if not b.exists():
            p.rename(b)
            return b
        n += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, required=True, help="projects/<name> folder")
    ap.add_argument("--hook", type=Path, required=True, help="the new hook voiceover (mp3/wav)")
    ap.add_argument("--subject", default="", help="e.g. Prince_Harry or Prince_Harry,Meghan_Markle")
    a = ap.parse_args()

    proj = a.project.resolve()
    name = proj.name
    work = proj / f"{name}_project"
    segs = json.loads((work / "segments.json").read_text(encoding="utf-8"))
    words = json.loads((work / "words.json").read_text(encoding="utf-8"))
    hook_end = max((s["end"] for s in segs if s.get("hook")), default=0.0)
    if hook_end <= 0:
        sys.exit("This project has no hook section to replace.")
    last = max((w["end"] for w in words if w["start"] < hook_end - 0.05), default=hook_end)   # last hook word
    nxt = min((w["start"] for w in words if w["start"] >= hook_end - 0.05), default=hook_end)  # first chapter word
    cut = round((last + nxt) / 2, 3)                                                          # middle of the pause
    print(f"Old hook ends at {hook_end:.2f} s - cutting the old voiceover at {cut:.2f} s", flush=True)

    script = next((p for p in (proj / "script.txt", proj / "script.docx") if p.exists()), None)
    if script is None or script.suffix != ".txt":
        sys.exit("Need projects/<name>/script.txt (docx scripts: save the text as script.txt first).")
    old = script.read_text(encoding="utf-8")
    m = re.search(r"^\s*#+\s*hook\b.*$", old, re.I | re.M)
    body = old[m.end():] if m else old
    d = re.search(r"^\s*([-*_=—]{3,}|#+\s*(chapter|part|section)\b.*|(chapter|part)\s*[:\-.]?\s*\d+.*)\s*$",
                  body, re.I | re.M)
    if not d:
        sys.exit("Could not find where the old hook ends in script.txt (no ---- or CHAPTER line after it).")
    rest = body[d.start():].lstrip("\n")

    print("Transcribing the new hook ...", flush=True)
    hook_text = transcribe(a.hook)
    print("New hook:\n" + hook_text, flush=True)

    vo = proj / "vo.mp3"
    old_vo = backup(vo)
    backup(script)
    mp4 = proj / f"{name}.mp4"
    if mp4.exists():
        backup(mp4)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a.hook), "-ss", str(cut), "-i", str(old_vo),
                        "-filter_complex", "[0:a]aresample=44100,apad=pad_dur=0.4[a];[1:a]aresample=44100[b];"
                        "[a][b]concat=n=2:v=0:a=1", "-c:a", "libmp3lame", "-b:a", "192k", str(vo)],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("ffmpeg failed: " + r.stderr[-600:])
    if not re.match(r"\s*([-*_=—]{3,})", rest):
        rest = "---\n\n" + rest
    script.write_text("## HOOK\n\n" + hook_text + "\n\n" + rest, encoding="utf-8")
    print("New voiceover and script saved - rendering the full video ...", flush=True)

    cmd = [sys.executable, str(ROOT / "make_video.py"), "--script", str(script), "--voiceover", str(vo),
           "--out", str(mp4)]
    if a.subject:
        cmd += ["--subject", a.subject]
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
