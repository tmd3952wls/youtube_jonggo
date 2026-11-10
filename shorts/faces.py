"""말하는 인물 얼굴 찾기 → 컷(샷)마다 얼굴 쪽으로 확대 크롭 + 빨간 동그라미 위치 계산.

- 샷 하나에서 프레임 3장을 뽑아 OpenCV 얼굴 검출(정면+측면), 가장 큰 얼굴 = 화면의 주인공(말하는 사람)
- 크롭: 기본 영역(하단 배너 제외 영역) 안에서 얼굴이 위쪽 1/3 지점에 오도록 zoom 배 확대
- 얼굴이 없으면(제품 클로즈업 등) 확대하지 않음
"""
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

_front = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_prof = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")


def _frame(src, t, W=640):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                          "-vf", f"scale={W}:-2", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    if not raw:
        return None
    h = len(raw) // W
    return np.frombuffer(raw[:W * h], np.uint8).reshape(h, W)


def _faces(img):
    h, w = img.shape
    mins = (int(h * 0.12), int(h * 0.12))
    out = []
    for casc, flip in ((_front, False), (_prof, False), (_prof, True)):
        im = cv2.flip(img, 1) if flip else img
        # 오검출은 face_in_shot 에서 여러 프레임 일치로 거름
        for (x, y, fw, fh) in casc.detectMultiScale(im, 1.1, 6, minSize=mins):
            if flip:
                x = w - x - fw
            out.append((x / w, y / h, fw / w, fh / h))
    return out


def face_in_shot(src, a, b, cache):
    """샷 [a,b] 의 대표 얼굴 (cx, cy, fw, fh) 정규화 좌표, 없으면 None."""
    key = f"{a:.2f}-{b:.2f}"
    if key in cache:
        return cache[key]
    # 프레임 5장에서 잡힌 얼굴을 모두 모아 자리별로 묶음 → 여러 프레임에서 같은 자리에 잡힌 얼굴만 인정.
    # (벽 그림 같은 오검출은 한두 프레임에만 튀어서 걸러지고, 화면 가장자리에 선 사람도 놓치지 않게) 2026-11-11
    dets = []
    for fi, f in enumerate((0.1, 0.3, 0.5, 0.7, 0.9)):
        img = _frame(src, a + (b - a) * f)
        if img is None:
            continue
        for x, y, w, h in _faces(img):
            dets.append((fi, x + w / 2, y + h / 2, w, h))
    clusters = []
    for d in dets:
        for c in clusters:
            if abs(c[0][1] - d[1]) < 0.08 and abs(c[0][2] - d[2]) < 0.1:
                c.append(d)
                break
        else:
            clusters.append([d])
    good = [c for c in clusters if len({d[0] for d in c}) >= 2]
    res = None
    if good:
        # 가장 많은 프레임에서 잡힌 얼굴, 같으면 더 큰 얼굴
        c = max(good, key=lambda c: (len({d[0] for d in c}), np.median([d[4] for d in c])))
        res = tuple(float(np.median([d[i] for d in c])) for i in range(1, 5))
    cache[key] = res
    return res


def crop_box(face, iw, ih, cfg, extra_zoom=1.0):
    """원본 픽셀 기준 크롭 (x, y, w, h). 패널 비율 유지, 하단 배너 영역 제외."""
    L = cfg["layout"]
    A = L["width"] / L["video_h"]
    keep_h = ih * L.get("crop_keep_h", 1.0)
    base_h = min(keep_h, iw / A)
    base_w = base_h * A
    if face is None:
        z = 1.0
        cx, cy = iw / 2, base_h / 2
    else:
        F = cfg["face"]
        cx, cy, fw, fh = face[0] * iw, face[1] * ih, face[2] * iw, face[3] * ih
        # 얼굴 크기에 따라 확대: 얼굴이 이미 크면 덜 확대
        z = min(F["max_zoom"], max(1.0, F["target_face_h"] * base_h / max(fh, 1)))
    z *= extra_zoom  # 같은 샷 점프컷일 때 번갈아 더 확대 → 의도된 편집처럼 보이게
    h = base_h / z
    w = h * A
    if face is not None:
        x = cx - w / 2
        y = cy - h * cfg["face"]["face_y"]  # 얼굴을 화면 위쪽(face_y) 지점에
    else:
        x, y = (iw - w) / 2, 0.0
    x = max(0.0, min(iw - w, x))
    y = max(0.0, min(keep_h - h, y))
    return int(x) // 2 * 2, int(y) // 2 * 2, int(w) // 2 * 2, int(h) // 2 * 2


def load_cache(job):
    p = Path(job) / "faces.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_cache(job, cache):
    (Path(job) / "faces.json").write_text(json.dumps(cache))
