"""Face detection + recognition with InsightFace's buffalo_l models (SCRFD-10G + ArcFace R50).

The official ONNX models are run directly through onnxruntime (GPU when available),
so no C++ build tools are needed on Windows.

Reference faces: for every People/<Person> folder we collect face embeddings,
keep the ones that agree with each other (the folder's "main" face), and use
those to tag who appears in every photo and video frame in the library.
"""
import logging
import urllib.request
import zipfile
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("index")

BUFFALO_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
DET_FILE, REC_FILE = "det_10g.onnx", "w600k_r50.onnx"

ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def ensure_models(models_dir: Path) -> tuple[Path, Path]:
    det, rec = models_dir / DET_FILE, models_dir / REC_FILE
    if det.exists() and rec.exists():
        return det, rec
    zip_path = models_dir / "buffalo_l.zip"
    log.info("Downloading InsightFace buffalo_l models (~280 MB, one time)...")
    urllib.request.urlretrieve(BUFFALO_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith((DET_FILE, REC_FILE)):
                (models_dir / Path(name).name).write_bytes(zf.read(name))
    zip_path.unlink(missing_ok=True)
    return det, rec


def _session(model_path: Path):
    try:
        import torch  # noqa: F401  (loads CUDA/cuDNN DLLs that onnxruntime-gpu can reuse)
    except Exception:
        pass
    import onnxruntime as ort
    try:
        ort.preload_dlls()
    except Exception:
        pass
    providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(str(model_path), sess_options=opts, providers=providers)


def _nms(dets: np.ndarray, thresh: float = 0.4) -> list[int]:
    x1, y1, x2, y2, scores = dets.T
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


class FaceEngine:
    def __init__(self, models_dir: Path, det_size: int = 640, det_threshold: float = 0.5, min_face_px: int = 36):
        det_path, rec_path = ensure_models(models_dir)
        self.det = _session(det_path)
        self.rec = _session(rec_path)
        self.device = "GPU" if "CUDAExecutionProvider" in self.det.get_providers() else "CPU"
        self.det_size = det_size
        self.det_threshold = det_threshold
        self.min_face_px = min_face_px
        self.det_input = self.det.get_inputs()[0].name
        self.rec_input = self.rec.get_inputs()[0].name
        self._anchor_cache: dict = {}

    # -------------------------------------------------------------- detection (SCRFD)
    def detect(self, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h, w = rgb.shape[:2]
        size = self.det_size
        if h / w > 1:
            nh, nw = size, int(size * w / h)
        else:
            nw, nh = size, int(size * h / w)
        scale = nh / h
        canvas = np.zeros((size, size, 3), dtype=np.uint8)
        canvas[:nh, :nw] = cv2.resize(rgb, (nw, nh))
        # model expects BGR normalised with mean 127.5 / std 128; canvas is RGB so no swap needed
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128, (size, size), (127.5, 127.5, 127.5), swapRB=False)
        outs = self.det.run(None, {self.det_input: blob})
        if outs[0].ndim == 3:
            outs = [o[0] for o in outs]

        scores_all, boxes_all, kps_all = [], [], []
        for idx, stride in enumerate((8, 16, 32)):
            scores = outs[idx].reshape(-1)
            bbox = outs[idx + 3] * stride
            kps = outs[idx + 6] * stride
            key = (size, stride)
            if key not in self._anchor_cache:
                fh = fw = size // stride
                centers = np.stack(np.mgrid[:fh, :fw][::-1], axis=-1).astype(np.float32)
                centers = (centers * stride).reshape(-1, 2)
                centers = np.stack([centers] * 2, axis=1).reshape(-1, 2)
                self._anchor_cache[key] = centers
            c = self._anchor_cache[key]
            pos = np.where(scores >= self.det_threshold)[0]
            if pos.size == 0:
                continue
            b = np.stack([c[:, 0] - bbox[:, 0], c[:, 1] - bbox[:, 1],
                          c[:, 0] + bbox[:, 2], c[:, 1] + bbox[:, 3]], axis=-1)
            k = np.stack([np.stack([c[:, 0] + kps[:, i], c[:, 1] + kps[:, i + 1]], axis=-1)
                          for i in range(0, 10, 2)], axis=1)
            scores_all.append(scores[pos])
            boxes_all.append(b[pos] / scale)
            kps_all.append(k[pos] / scale)
        if not scores_all:
            return np.zeros((0, 5), np.float32), np.zeros((0, 5, 2), np.float32)
        scores = np.concatenate(scores_all)
        boxes = np.concatenate(boxes_all)
        kps = np.concatenate(kps_all)
        dets = np.hstack([boxes, scores[:, None]]).astype(np.float32)
        keep = _nms(dets)
        return dets[keep], kps[keep]

    # -------------------------------------------------------------- recognition (ArcFace)
    def embed(self, rgb: np.ndarray, kps: np.ndarray) -> np.ndarray:
        M, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), ARCFACE_DST, method=cv2.LMEDS)
        crop = cv2.warpAffine(rgb, M, (112, 112), borderValue=0.0)
        blob = cv2.dnn.blobFromImage(crop, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=False)
        v = self.rec.run(None, {self.rec_input: blob})[0][0]
        return v / (np.linalg.norm(v) + 1e-9)

    def analyze(self, rgb: np.ndarray) -> list[dict]:
        """Faces in an image: normalized bbox, detection score, 512-d embedding."""
        h, w = rgb.shape[:2]
        dets, kps = self.detect(rgb)
        faces = []
        for d, k in zip(dets, kps):
            x1, y1, x2, y2, score = d
            if min(x2 - x1, y2 - y1) < self.min_face_px:
                continue
            faces.append({
                "bbox": [round(float(max(x1, 0) / w), 4), round(float(max(y1, 0) / h), 4),
                         round(float(min(x2, w) / w), 4), round(float(min(y2, h) / h), 4)],
                "score": round(float(score), 3),
                "emb": self.embed(rgb, k).astype(np.float16).tolist(),
            })
        faces.sort(key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]), reverse=True)
        return faces


# ------------------------------------------------------------------ references + matching
def build_references(person_faces: dict[str, list[list[dict]]], min_agree: float = 0.35) -> dict[str, np.ndarray]:
    """person -> matrix of trusted embeddings.

    person_faces[person] is a list (one per photo in that person's folder) of face lists.
    1. Seed with photos that contain exactly one face.
    2. Keep embeddings close to the seed centroid (drops wrong/odd photos).
    3. From group photos, add the single face that best matches the centroid.
    """
    refs = {}
    for person, photos in person_faces.items():
        singles = [np.array(f[0]["emb"], np.float32) for f in photos if len(f) == 1]
        if len(singles) < 3:  # tiny folder: fall back to the largest face per photo
            singles = [np.array(f[0]["emb"], np.float32) for f in photos if f]
        if not singles:
            continue
        E = np.stack(singles)
        centroid = _unit(E.mean(0))
        for _ in range(2):  # re-centre on agreeing faces
            sims = E @ centroid
            good = E[sims >= min_agree]
            if len(good) >= 2:
                centroid = _unit(good.mean(0))
        trusted = [e for e in E if e @ centroid >= min_agree]
        for faces in photos:
            if len(faces) > 1:
                embs = np.array([f["emb"] for f in faces], np.float32)
                sims = embs @ centroid
                i = int(sims.argmax())
                if sims[i] >= min_agree + 0.1:
                    trusted.append(embs[i])
        if trusted:
            refs[person] = np.stack(trusted)
    return refs


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


class FaceMatcher:
    def __init__(self, refs: dict[str, np.ndarray], threshold: float, margin: float):
        self.names = list(refs)
        self.refs = refs
        self.centroids = np.stack([_unit(refs[n].mean(0)) for n in self.names]) if refs else None
        self.threshold = threshold
        self.margin = margin

    def score(self, emb) -> np.ndarray:
        """Similarity to each person: mean of centroid sim and top-5 nearest reference sims."""
        e = _unit(np.asarray(emb, np.float32))
        cent = self.centroids @ e
        topk = np.array([np.sort(self.refs[n] @ e)[-5:].mean() for n in self.names])
        return 0.5 * cent + 0.5 * topk

    def identify(self, emb) -> tuple[str | None, float]:
        if self.centroids is None:
            return None, 0.0
        s = self.score(emb)
        order = s.argsort()[::-1]
        best = float(s[order[0]])
        second = float(s[order[1]]) if len(order) > 1 else -1.0
        if best >= self.threshold and best - second >= self.margin:
            return self.names[order[0]], best
        return None, best
