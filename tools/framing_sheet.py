"""구도 검토 사진: 화면 조각마다 '원본 전체 화면 + 지금 자르는 영역(빨간 상자)'을 보여 준다.

사용법:  python tools/framing_sheet.py jobs/<작업명>      → jobs/<작업명>/qa_framing_N.jpg

보는 법: 빨간 상자 안이 영상에 나오는 부분이다. 말하는 사람 얼굴이 상자 가운데(조금 위쪽)에 있어야 하고,
제품은 상자 안에 통째로 보여야 한다. 아니면 plan.json 에
  "framing": [{"from": 원본초, "to": 원본초, "cx": 0~1, "cy": 0~1, "h": 0~1}]
을 넣어 고친다 (cx, cy = 상자 중심의 가로·세로 위치, h = 원본 높이 대비 상자 높이. 눈금이 0.1 단위로 그려져 있다).
같은 원본 장면(같은 카메라 샷)의 조각들은 하나로 묶어 한 칸에 보여 준다.
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
TW, TH = 480, 270


def frame(src, t):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                          "-vf", f"scale={TW}:{TH}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    if len(raw) < TW * TH * 3:
        return np.zeros((TH, TW, 3), np.uint8)
    return np.frombuffer(raw[:TW * TH * 3], np.uint8).reshape(TH, TW, 3).copy()


def main():
    job = Path(sys.argv[1]).resolve()
    shots = json.loads((job / "shots_map.json").read_text(encoding="utf-8"))
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                         "-of", "csv=p=0", str(job / "source.mp4")], capture_output=True, text=True).stdout.strip().split(",")
    iw, ih = int(pr[0]), int(pr[1])
    # 같은 자르기 영역이 이어지는 조각은 한 칸으로
    groups = []
    for s in shots:
        if not s.get("crop"):
            continue
        g = groups[-1] if groups else None
        if g and g["crop"] == s["crop"] and abs(s["src_a"] - g["src_b"]) < 3.0 and g["kind"] == s["kind"]:
            g["src_b"], g["out_b"] = s["src_b"], s["out_b"]
        else:
            groups.append(dict(s))
    cols, per = 4, 16
    for page in range(0, len(groups), per):
        chunk = groups[page:page + per]
        rows = (len(chunk) + cols - 1) // cols
        sheet = np.zeros((rows * (TH + 26), cols * TW, 3), np.uint8)
        for i, g in enumerate(chunk):
            im = frame(job / "source.mp4", (g["src_a"] + g["src_b"]) / 2)
            for k in range(1, 10):  # 0.1 단위 눈금
                cv2.line(im, (TW * k // 10, 0), (TW * k // 10, TH), (90, 90, 90), 1)
                cv2.line(im, (0, TH * k // 10), (TW, TH * k // 10), (90, 90, 90), 1)
                cv2.putText(im, f".{k}", (TW * k // 10 + 2, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (200, 200, 200), 1)
                cv2.putText(im, f".{k}", (2, TH * k // 10 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (200, 200, 200), 1)
            x, y, w, h = g["crop"]
            sx, sy = TW / iw, TH / ih
            cv2.rectangle(im, (int(x * sx), int(y * sy)), (int((x + w) * sx), int((y + h) * sy)), (0, 0, 255), 2)
            r, c = divmod(i, cols)
            yy = r * (TH + 26)
            sheet[yy + 26:yy + 26 + TH, c * TW:(c + 1) * TW] = im
            lab = f"#{page + i} {'N' if g['kind'] == 'narration' else 'C'} out {g['out_a']:.1f}-{g['out_b']:.1f}s  src {g['src_a']:.2f}-{g['src_b']:.2f}"
            cv2.putText(sheet, lab, (c * TW + 4, yy + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        out = job / f"qa_framing_{page // per + 1}.jpg"
        cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
        print("저장:", out)
    print(f"FRAMING_SHEET: 화면 묶음 {len(groups)}개")


if __name__ == "__main__":
    main()
