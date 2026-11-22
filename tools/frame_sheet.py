import cv2, numpy as np, sys, json
job = sys.argv[1]; out = sys.argv[2]
cap = cv2.VideoCapture(f"jobs/{job}/final.mp4")
n = cap.get(cv2.CAP_PROP_FRAME_COUNT); fps = cap.get(cv2.CAP_PROP_FPS); dur = n / fps
# 구간 조각 시작 직후(0.25초 뒤) 프레임들: 모든 화면 조각을 한 장씩
tl = json.load(open(f"jobs/{job}/timeline.json", encoding="utf-8"))
sm = json.load(open(f"jobs/{job}/sync_map.json", encoding="utf-8"))
t0s, t = [], 0.0
for s in tl: t0s.append(t); t += s["dur"]
pts = sorted(set([round(t0s[m["seg"]] + m["out_a"] + min(0.3, (m["src_b"]-m["src_a"])/2), 2) for m in sm] +
                 [round(t0s[k] + 0.4, 2) for k, s in enumerate(tl) if s["kind"] == "narration"] +
                 [round(t0s[k] + s["dur"] - 0.4, 2) for k, s in enumerate(tl) if s["kind"] == "narration"]))
tw, th = 300, int(300 * 1050 / 1080)
cols = 8; rows = (len(pts) + cols - 1) // cols
sheet = np.zeros((rows * (th + 22), cols * tw, 3), np.uint8)
for i, p in enumerate(pts):
    cap.set(cv2.CAP_PROP_POS_MSEC, p * 1000); ok, fr = cap.read()
    if not ok: continue
    H, W = fr.shape[:2]
    panel = fr[int(H * 435 / 1920):int(H * 1485 / 1920)]
    sm_ = cv2.resize(panel, (tw, th), interpolation=cv2.INTER_AREA)
    r, c = divmod(i, cols)
    y = r * (th + 22)
    sheet[y + 22:y + 22 + th, c * tw:(c + 1) * tw] = sm_
    cv2.putText(sheet, f"{p:.1f}s", (c * tw + 6, y + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
cv2.imwrite(out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 85]); print(len(pts), sheet.shape)
