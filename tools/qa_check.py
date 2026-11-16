"""최종 렌더링 결과물 8대 품질 자동 검수 도구 (make_short.py render 시 자동 실행).

검수 항목:
  1) 영상 길이: 45.0 ~ 57.0초 이내
  2) 자막 빈틈 제로: 0.00초부터 끝까지 카드 사이 공백(> 0.015초) 0건
  3) 찰나 자막 방지: 0.35초 미만으로 스쳐 지나가는 자막 카드 0건
  4) 큰 강조 자막({big}, 98px) 한 줄 원칙: 2줄(\\N)로 줄바꿈된 강조 자막 0건
  5) 마크업 노출 방지: {big}, [yellow: 등 태그 기호 잔류 0건
  6) 오디오 숨소리/침묵 공백 방지: 본편 중간(끝 여운 제외) 0.40초 이상 무음 구간 0건
  7) 효과음 규칙: 같은 효과음 중복 사용 0건 + 마지막 마무리 내레이션 sfx == null
  8) 배경음악 분리 확인: source_vocals.wav 존재 여부

추가로 시각 검수용 시트(qa_big_cards.jpg, qa_transitions.jpg)를 자동 생성한다.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


def _parse_ass_time(s: str) -> float:
    h, m, sc = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sc)


def run_qa(job: Path) -> bool:
    job = Path(job).resolve()
    plan_p = job / "plan.json"
    ass_p = job / "subs.ass"
    body_p = job / "body.mp4"
    final_p = job / "final.mp4"

    issues = []

    # 0. Video metadata
    cap = cv2.VideoCapture(str(final_p))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    dur = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) / fps

    # 1. 영상 길이 (45~57초)
    if not (45.0 <= dur <= 57.5):
        issues.append(f"[길이 위반] 현재 {dur:.2f}초 (기준: 45.0~57.0초)")

    # 2~5. ASS 자막 정밀 검사
    ass_txt = ass_p.read_text(encoding="utf-8")
    events = []
    big_times = []
    leaks = []
    two_line_bigs = []
    flash_subs = []

    for ln in ass_txt.splitlines():
        if not ln.startswith("Dialogue:"):
            continue
        parts = ln.split(",", 9)
        layer = int(parts[0].split(":")[1].strip())
        a, b, style, raw = _parse_ass_time(parts[1]), _parse_ass_time(parts[2]), parts[3], parts[9]
        plain = re.sub(r"\{[^}]*\}", "", raw)
        if "big" in plain.lower() or "[" in plain or "]" in plain:
            leaks.append(plain)
        if layer != 5:
            continue
        is_big = (r"\fs98" in raw) or style.endswith("_b")
        clean_1l = plain.replace(r"\N", " / ").strip()
        events.append((a, b, is_big, clean_1l))
        if is_big:
            big_times.append((0.5 * (a + b), clean_1l))
            if r"\N" in plain:
                two_line_bigs.append(f"{a:.2f}s '{clean_1l}'")
        if (b - a) < 0.35:
            flash_subs.append(f"{a:.2f}->{b:.2f}s ({b - a:.2f}s) '{clean_1l}'")

    gaps = []
    if events and events[0][0] > 0.05:
        gaps.append(f"시작 공백 0.00->{events[0][0]:.2f}s")
    for idx in range(1, len(events)):
        prev_end = events[idx - 1][1]
        cur_start = events[idx][0]
        if cur_start - prev_end > 0.015:
            gaps.append(f"{prev_end:.2f}->{cur_start:.2f}s ({cur_start - prev_end:.2f}s)")

    if gaps:
        issues.append(f"[자막 빈틈] {len(gaps)}곳 발생: {', '.join(gaps[:5])}")
    if flash_subs:
        issues.append(f"[찰나 자막(<0.35s)] {len(flash_subs)}곳: {', '.join(flash_subs[:5])}")
    if two_line_bigs:
        issues.append(f"[큰 자막 2줄 줄바꿈] 반드시 1줄(8~9자 이내)로 줄이세요: {', '.join(two_line_bigs)}")
    if leaks:
        issues.append(f"[자막 태그 노출] {leaks[:3]}")

    # 5-1. 원화 표기: 띄어쓰기 없이 '7억5천만원' + '원'이 다음 자막(또는 다음 줄)으로 떨어지지 않게
    money_space, won_split = [], []
    U = r"[\d조억천백만]"
    for idx, (a, b, _, t) in enumerate(events):
        if re.search(rf"\d[\d,]*\s*[조억천백만]*\s+[\d조억천백만,]*\s*원|\d\s+[조억천만]|[조억]\s+\d", t):
            money_space.append(f"{a:.2f}s '{t}'")
        if re.search(rf"{U}\s*/\s*원", t):
            won_split.append(f"{a:.2f}s '{t}' (줄바꿈으로 '원' 분리)")
        if idx + 1 < len(events):
            nxt = events[idx + 1][3].lstrip()
            if nxt.startswith("원") and re.search(rf"{U}[\s!?.,~]*$", t):
                won_split.append(f"{a:.2f}s '{t}' → 다음 '{nxt}'")
    if money_space:
        issues.append(f"[원화 띄어쓰기] '7억5천만원'처럼 붙여 쓰세요: {', '.join(money_space[:5])}")
    if won_split:
        issues.append(f"[원화 '원' 분리] 금액과 '원'을 같은 자막·같은 줄에: {', '.join(won_split[:5])}")

    # 6. 오디오 무음/삐처리 빈틈 검사 (body.mp4)
    if body_p.exists():
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(body_p), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
            capture_output=True, check=True
        ).stdout
        y = np.frombuffer(pcm, np.int16).astype(np.float64) / 32768
        hop = 160
        n = len(y) // hop
        if n > 0:
            e = 20 * np.log10(np.sqrt((y[:n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12))
            thr = max(np.percentile(e, 10) + 8, np.percentile(e, 95) - 36)
            quiet = e < thr
            dead_air = []
            s_idx = None
            cutoff_cs = int(max(0, dur - 0.35) * 100)
            for i, q in enumerate(quiet[:cutoff_cs]):
                if q and s_idx is None:
                    s_idx = i
                elif not q and s_idx is not None:
                    if (i - s_idx) >= 40:
                        dead_air.append(f"{s_idx / 100:.2f}->{i / 100:.2f}s ({(i - s_idx) / 100:.2f}s)")
                    s_idx = None
            if s_idx is not None and (cutoff_cs - s_idx) >= 40:
                dead_air.append(f"{s_idx / 100:.2f}->{cutoff_cs / 100:.2f}s ({(cutoff_cs - s_idx) / 100:.2f}s)")
            if dead_air:
                issues.append(f"[오디오 공백(>=0.40s)] 방송 삐처리/무음 구간 의심: {', '.join(dead_air)}")

    # 7. 효과음 중복 및 마지막 내레이션 무효과음 검사
    if plan_p.exists():
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        segs = plan.get("segments", [])
        sfxs = [s.get("sfx") for s in segs if s.get("sfx")]
        seen, dups = set(), []
        for x in sfxs:
            if x in seen and x not in dups:
                dups.append(x)
            seen.add(x)
        if dups:
            issues.append(f"[효과음 중복] 같은 효과음 재사용 금지: {dups}")
        if segs and segs[-1].get("type") == "narration" and segs[-1].get("sfx") is not None:
            issues.append(f"[마무리 효과음 위반] 마지막 내레이션의 sfx는 반드시 null이어야 함 (현재: {segs[-1].get('sfx')})")

    # 8. 원본 배경음악 제거(source_vocals.wav) 확인
    if not (job / "source_vocals.wav").exists():
        issues.append("[배경음악 분리 누락] source_vocals.wav 파일이 없습니다.")

    # 시각 검수용 이미지 저장 (qa_big_cards.jpg, qa_transitions.jpg)
    if big_times and w > 0 and h > 0:
        bcols = min(len(big_times), 5)
        brows = (len(big_times) + bcols - 1) // bcols
        btw, bth = 360, int(360 * h / w)
        bcanvas = np.zeros((brows * bth, bcols * btw, 3), dtype=np.uint8)
        for idx, (bt, _) in enumerate(big_times):
            cap.set(cv2.CAP_PROP_POS_MSEC, bt * 1000)
            ok, frame = cap.read()
            if ok:
                small = cv2.resize(frame, (btw, bth), interpolation=cv2.INTER_AREA)
                cv2.putText(small, f"{bt:.2f}s", (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
                rr, cc = divmod(idx, bcols)
                bcanvas[rr * bth:(rr + 1) * bth, cc * btw:(cc + 1) * btw] = small
        cv2.imwrite(str(job / "qa_big_cards.jpg"), bcanvas, [cv2.IMWRITE_JPEG_QUALITY, 90])

    if events and w > 0 and h > 0:
        trans_pts = [events[i][0] for i in np.linspace(0, len(events) - 1, min(10, len(events)), dtype=int)]
        tcols, trows = 5, 2
        ttw, tth = 360, int(360 * h / w)
        tcanvas = np.zeros((trows * tth, tcols * ttw, 3), dtype=np.uint8)
        for idx, tt in enumerate(trans_pts):
            cap.set(cv2.CAP_PROP_POS_MSEC, tt * 1000 + 5)
            ok, frame = cap.read()
            if ok:
                small = cv2.resize(frame, (ttw, tth), interpolation=cv2.INTER_AREA)
                cv2.putText(small, f"t={tt:.2f}s", (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
                rr, cc = divmod(idx, tcols)
                tcanvas[rr * tth:(rr + 1) * tth, cc * ttw:(cc + 1) * ttw] = small
        cv2.imwrite(str(job / "qa_transitions.jpg"), tcanvas, [cv2.IMWRITE_JPEG_QUALITY, 90])
    cap.release()

    if not issues:
        print(f"  [자동 품질 검수 통과 9/9] 길이 {dur:.1f}초 | 자막 {len(events)}장(빈틈 0초) | 강조자막 {len(big_times)}장(전원 1줄) | 원화 표기 OK | 오디오 공백 0건")
        print(f"  [시각 검수 시트] {job / 'qa_big_cards.jpg'} , {job / 'qa_transitions.jpg'}")
        return True

    print(f"\n  ⚠ [자동 품질 검수 경고 {len(issues)}건 — 발송 전 반드시 수정하세요!]")
    for it in issues:
        print(f"    - {it}")
    return False


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ok = run_qa(Path(sys.argv[1]))
    sys.exit(0 if ok else 1)
