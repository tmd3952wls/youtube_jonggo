"""완성본의 대사 화면이 '소리와 같은 원본 시각'의 화면인지 실제 그림으로 확인한다 (입모양 싱크 실측).

사용법:  python tools/lipsync_check.py jobs/<작업명>

방법: render 가 남긴 sync_map.json 의 대사 조각마다
  - 조립된 구간 파일(segments/NN.mp4)의 프레임과
  - 원본(source.mp4)을 같은 영역으로 잘라 낸 프레임을, 소리 원본 시각 기준 ±0.5초(±15프레임) 밀어 가며 비교해
  그림이 가장 똑같은 시간 차이를 잰다. 차이가 ±1프레임(0.033초) 안이면 화면과 소리가 같은 시각 → 입모양이 맞는다.
(소리는 조립 때 같은 원본 시각에서 그대로 잘라 쓰므로, 화면 시각만 맞으면 된다.)
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
FPS = 30
SZ = (108, 105)  # 영상 칸(1080x1050) 축소 크기


def frames(path, t, d, crop=None):
    vf = (f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else "") + f"fps={FPS},scale={SZ[0]}:{SZ[1]},format=gray"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t):.3f}", "-t", f"{d:.3f}", "-i", str(path),
                          "-vf", vf, "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(raw) // (SZ[0] * SZ[1])
    return np.frombuffer(raw[:n * SZ[0] * SZ[1]], np.uint8).reshape(n, -1).astype(np.float32)


def main():
    job = Path(sys.argv[1]).resolve()
    sm = json.loads((job / "sync_map.json").read_text(encoding="utf-8"))
    M = 15
    # 대사 구간에는 조립 때 '천천히 확대'(transition.clip_drift)가 걸린다 → 원본 쪽도 같은 배율로 가운데를 잘라 비교
    # (안 그러면 구간 뒤쪽 조각일수록 그림이 달라져 시간차를 잘못 잼, 2026-11-24)
    cfg = json.loads((job.parents[1] / "config.json").read_text(encoding="utf-8"))
    drift = float(cfg.get("transition", {}).get("clip_drift", 0) or 0)
    seg_len = {}
    rows, bad, skipped = [], 0, 0
    for m in sm:
        d = m["src_b"] - m["src_a"]
        if d < 0.3 or not m.get("crop"):
            skipped += 1
            continue
        seg = job / "segments" / f"{m['seg']:02d}.mp4"
        o = frames(seg, m["out_a"], d)
        crop = m["crop"]
        if drift:
            if seg not in seg_len:
                seg_len[seg] = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                                     "-of", "csv=p=0", str(seg)], capture_output=True, text=True).stdout or 0)
            L = seg_len[seg] or 1.0
            z = 1 + drift * (m["out_a"] + min(d, 20 / FPS) / 2) / L
            x, y, w, h = crop
            w2, h2 = int(w / z) // 2 * 2, int(h / z) // 2 * 2
            crop = [x + (w - w2) // 2, y + (h - h2) // 2, w2, h2]
        s = frames(job / "source.mp4", m["src_a"] - M / FPS, d + 2 * M / FPS, crop=crop)
        n = min(len(o), 20)
        if n < 4 or len(s) < n + 2 * M - 2:
            skipped += 1
            continue
        errs = [float(np.abs(o[:n] - s[k:k + n]).mean()) for k in range(0, len(s) - n + 1)]
        k = int(np.argmin(errs))
        lag = (k - M) / FPS
        # 화면이 거의 안 움직이면 어느 시각이든 똑같이 보여 판단할 수 없다
        # (말하는 사람이 아닌 듣는 사람 얼굴처럼 정지에 가까운 화면: 시간차 0 위치의 오차가 최솟값과 거의 같다)
        flat = (sorted(errs)[min(3, len(errs) - 1)] - errs[k]) < 0.6 or (errs[M] - errs[k]) < 1.5
        ok = abs(lag) <= 1 / FPS + 1e-6
        # 화면 효과(천천히 밀기·확대)가 걸린 조각은 같은 시각이어도 그림이 조금 달라 최솟값 자체가 크다.
        # 이런 조각은 시간차를 믿을 수 없다 (다른 구간을 통째로 넣은 경우는 qa_check 의 [입모양 싱크] 항목이 잡는다).
        blurry = errs[k] > 12.0
        tag = "OK" if ok else ("판단 불가(화면 움직임 거의 없음)" if flat else ("판단 불가(화면 효과로 비교 어려움)" if blurry else "어긋남"))
        bad += tag == "어긋남"
        rows.append((m["seg"], m["out_a"], d, lag, errs[k], tag))
    for r in rows:
        print(f"  구간{r[0]:02d} +{r[1]:5.2f}s  길이 {r[2]:.2f}s  화면-소리 시간차 {r[3]:+.3f}s  (그림 오차 {r[4]:.1f})  {r[5]}")
    n_ok = sum(1 for r in rows if r[5] == "OK")
    print(f"LIPSYNC: 잰 대사 조각 {len(rows)}개 중 일치 {n_ok}개, 어긋남 {bad}개, 판단 불가 {len(rows) - n_ok - bad}개 (너무 짧아 건너뜀 {skipped}개)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
