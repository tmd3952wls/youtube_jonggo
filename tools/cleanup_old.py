"""만든 지 N일(기본 3일) 지난 영상·영상 소재를 지운다 (디스크 정리. 유튜브에 올린 영상은 건드리지 않는다).

사용법:
  python tools/cleanup_old.py            # 무엇을 지울지 목록만 보여 줌 (지우지 않음)
  python tools/cleanup_old.py --apply    # 실제로 지움 (되돌릴 수 없음)

지우는 것 (작업 폴더가 N일 넘게 손대지 않은 경우만):
  - jobs/<작업명>/ 안의 영상·소리·그림과 중간 파일: *.mp4 *.wav *.mp3 *.m4a *.webm *.mkv *.jpg *.png,
    segments/ ext/ edl_tmp/ sfx_level_tmp/ vmake_debug/ tts/ 안의 같은 종류 파일
  - Downloads\\chatgpt 사진\\ 의 <한글이름>.mp4 / _4K.mp4 / _업로드정보 / _고정댓글 (제목이 이 저장소 작업의 제목과 같은 것만)
  - Downloads\\<작업명>_<숫자10자리>.mp4 (Vmake 가 내려받아 남긴 4K 사본)
남기는 것: plan.json, transcript*.json, research.json 등 글자 파일(깃에 올라가 있고 중복 주제 확인에 쓰임), 효과음·글꼴·목소리 캐시.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
DL = Path.home() / "Downloads"
DL_PIC = DL / "chatgpt 사진"
MEDIA = {".mp4", ".wav", ".mp3", ".m4a", ".webm", ".mkv", ".mov", ".jpg", ".jpeg", ".png"}


def newest_mtime(d: Path) -> float:
    ts = [p.stat().st_mtime for p in d.rglob("*") if p.is_file()]
    return max(ts) if ts else d.stat().st_mtime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cutoff = time.time() - a.days * 86400
    targets, titles, old_jobs = [], set(), []
    for job in sorted(p for p in JOBS.iterdir() if p.is_dir() and not p.name.startswith("_")):  # _로 시작하는 폴더(참고 자료·점검 기록)는 건드리지 않음
        if newest_mtime(job) > cutoff:
            continue
        old_jobs.append(job.name)
        pj = job / "plan.json"
        if pj.exists():
            try:
                t = json.loads(pj.read_text(encoding="utf-8-sig")).get("upload", {}).get("title")
                if t:
                    titles.add(t.strip())
            except Exception:
                pass
        targets += [p for p in job.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA]
    # Downloads\chatgpt 사진: 제목이 오래된 작업과 같은 묶음만
    if DL_PIC.exists():
        for info in DL_PIC.glob("*_업로드정보(제목·설명·태그).txt"):
            try:
                txt = info.read_text(encoding="utf-8-sig")
            except Exception:
                continue
            title = next((l.split(":", 1)[1].strip() for l in txt.splitlines() if l.startswith("제목:")), "")
            if title not in titles:
                continue
            name = info.name.replace("_업로드정보(제목·설명·태그).txt", "")
            for p in [info, DL_PIC / f"{name}_고정댓글.txt", DL_PIC / f"{name}.mp4", DL_PIC / f"{name}_4K.mp4"]:
                if p.exists() and p.stat().st_mtime <= cutoff:
                    targets.append(p)
    # Vmake 가 남긴 4K 사본 (오래된 것만, 이 저장소 작업 이름으로 시작하는 것만)
    job_names = {p.name for p in JOBS.iterdir() if p.is_dir()}
    for p in DL.glob("*.mp4"):
        m = re.fullmatch(r"(.+)_\d{10}(?: \(\d+\))?\.mp4", p.name)
        if m and m.group(1) in job_names and p.stat().st_mtime <= cutoff:
            targets.append(p)
    total = sum(p.stat().st_size for p in targets)
    by = {}
    for p in targets:
        key = p.relative_to(JOBS).parts[0] if JOBS in p.parents else str(p.parent)
        by.setdefault(key, [0, 0])
        by[key][0] += 1
        by[key][1] += p.stat().st_size
    print(f"기준: {a.days:g}일 넘게 손대지 않은 작업 {len(old_jobs)}개")
    for k, (n, sz) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print(f"  {k}: 파일 {n}개, {sz / 1e6:,.0f} MB")
    print(f"합계: 파일 {len(targets)}개, {total / 1e9:.2f} GB")
    if not a.apply:
        print("DRY: 지우지 않았습니다 (--apply 를 붙이면 실제로 지움)")
        return
    done = 0
    for p in targets:
        try:
            p.unlink()
            done += 1
        except Exception as e:
            print(f"  못 지움: {p} ({e})")
    for job in JOBS.iterdir():  # 비어 버린 하위 폴더 정리
        if job.is_dir() and job.name in old_jobs:
            for d in sorted((x for x in job.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
                try:
                    d.rmdir()
                except OSError:
                    pass
    print(f"CLEANED: 파일 {done}개 삭제, 약 {total / 1e9:.2f} GB 확보")


if __name__ == "__main__":
    main()
