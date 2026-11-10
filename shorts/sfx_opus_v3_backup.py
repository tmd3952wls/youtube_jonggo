"""효과음 믹스 — opus-sound-layer (실제 녹음 CC0 1,139개) 로.

2026-11-08 사용자 결정: 예전 효과음 폴더(sfxlib) 소리가 어색해서, full_v6 방식(한 계열의 실제 녹음 +
목소리 대비 소리별 음량 + 공유 공간 울림 + -14 LUFS 마스터)을 기본으로.

렌더러(edl.py)가 정한 효과음 '시점·종류'는 그대로 쓰고, 소리만 아래 FAMILY 에서 차례로 고른다.
  카테고리 이름: whoosh, pop, ding, question, impact, success, surprise, fail, tension, caption, camera
  "whoosh/fs-30240-swoop1-flac-1" 처럼 라이브러리 id 를 직접 적으면 그 소리를 그대로 씀.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

from .common import ROOT

OPUS = ROOT / "ext" / "opus-sound-layer"
KIT = OPUS / "audio" / "kit"
SR = 48000

# 2026-11-08 v2: 참고 쇼츠 15개(사용자 지정) 분석 결과로 바꿈 (예전 판: sfx_opus_v1_backup.py)
#  - 목소리를 뺀 음악+효과음 트랙을 earshot timeline 으로 들어 보니, 화면 컷에 붙는 소리는
#    '띵'(글로켄슈필·종, 컷 50ms 안 30% — 우연 8%) > 휙(24%)·역재생 심벌 스웰(19%) > 낮은 쿵(38%) 순.
#  - 예전 판(거품 팝·나무 톡·천 스치는 소리 같은 실제 녹음)은 참고 영상과 결이 달라 사용자가 '별로'라고 함.
#  - 소리 파일은 earshot rank(설명 문장 검색, z>=3.8 'strong')로 사용자 효과음 폴더 + opus 키트에서 고름.
PACK = Path(os.environ.get("SHORTS_SFX_PACK", r"C:\Users\tmd39\Desktop\효과음\효과음600개"))


def _p(name):
    return str(PACK / name)


FAMILY = {
    "ding": [_p("001_띵.mp3"), _p("185_Ting.mp3"), _p("265_딩 다음 페이지 넘기기.mp3"), _p("400_전화 메시지 소리3.mp3"),
             _p("526_화면전환효과음_01.mp3"), "chime/fs-159011-ding2-wav-1", _p("042_마법봉.mp3")],
    "whoosh": [_p("573_Whoosh(Transition).mp3"), "whoosh/fs-449988-whoosh-med-high-1", _p("190_훅.mp3"),
               "whoosh/fs-60004-fast-sword-swing-sound-1", "whoosh/fs-449992-whoosh-short-low-wav-1",
               "whoosh/fs-60011-whoosh-24-2"],
    "swell": ["riser/fs-503802-reverse-fx-4-wav-1", "riser/fs-503799-reverse-fx-9-wav-1", _p("087_스르륵 효과음.mp3")],
    "impact": ["boom/fs-81145-sub-oceano-a-wav-1", "boom/fs-236956-boom-mp3-1"],
    "pop": [_p("002_뿅.mp3")],
    "question": [_p("015_띠동띠동(물음표).mp3")],
    "success": [_p("468_또잉(정답소리).mp3"), _p("379_Correct.mp3")],
    "surprise": [_p("042_마법봉.mp3"), "riser/fs-503799-reverse-fx-9-wav-1"],
    "fail": ["boom/fs-81149-sub-oceano-g-sharp-wav-1"],
    "tension": ["tick/fs-403001-clock-tick-tik-tak-2", "tick/fs-130388-clock-ticking-1"],
    "caption": [_p("267_캡션 사운드 타탁.mp3"), "key/fs-194795-vintage-keyboard-1-1"],
    "camera": [_p("502_카메라 플래시 셔터.mp3"), "click/kenney-ui-audio-switch16"],
    # 돈 금액 + 지폐 아이콘 (2026-11-10): Freesound CC0 금전등록기 — sfx.py search 로 찾아 add
    "cash": ["chime/own-ov-deafdf72", "chime/own-ov-62dd2f8f"],
}
HERO = {"impact"}  # 한 영상의 큰 한 방: 직전 정적 + 짧게 차오르는 소리
BUILD = "riser/fs-503802-reverse-fx-4-wav-1"  # 큰 한 방 직전: 역재생 심벌 스웰 (참고 영상에 많음)


# 효과음 폴더(PACK)·opus 키트가 없는 PC 에서 쓰는 저장소 안 기본 소리 (2026-11-10)
_A, _S = ROOT / "assets" / "sfx", ROOT / "sfx"
LOCAL = {
    "whoosh": [str(_S / "punchy_whoosh_02_Swish hit (13lrs).mp3"), str(_A / "whoosh.wav"),
               str(_S / "punchy_whoosh_03_Crunch whoosh with end punch reverb.mp3")],
    "swell": [str(_A / "whoosh.wav")],
    "impact": [str(_A / "boom.wav")], "fail": [str(_A / "boom.wav")],
    "ding": [str(_A / "ding.wav")], "success": [str(_A / "ding.wav")], "tension": [str(_A / "ding.wav")],
    "pop": [str(_A / "pop.wav")], "question": [str(_A / "pop.wav")], "surprise": [str(_A / "pop.wav")],
    "caption": [str(_A / "pop.wav")], "camera": [str(_A / "pop.wav")],
    # 금전등록기 (Freesound CC0: MaoDin204 #721774, clubmydia+ #584168) — 키트 없는 PC 에서도 지폐 아이콘 소리
    "cash": [str(_A / "cash_register_1.wav"), str(_A / "cash_register_2.wav")],
}


# 효과음 폴더(PACK)가 없을 때: opus 키트(Freesound CC0)에서 종류별로 여러 개 → 같은 소리 반복 방지 (2026-11-10 사용자 요청)
KIT_FAMILY = {
    "ding": ["chime/fs-615949-ding-effect-wav-1", "chime/fs-404104-glass-ding-3", "chime/fs-127149-ding-wav-1",
             "chime/fs-571513-soft-notifications-bell-ding-2", "chime/fs-611113-bell-ding-1-wav-1",
             "chime/fs-404104-glass-ding-1", "chime/fs-159011-ding2-wav-1"],
    "success": ["shimmer/fs-389789-chimes-3", "chime/fs-243701-correct-1", "shimmer/fs-462095-sparkling-star-01-wav-2"],
    "pop": ["pop/fs-188381-3-popping-pops-1", "pop/fs-206152-bottlepop44-wav-1",
            "pop/fs-503162-mouth-pops-magic-bottle-cork-3", "pop/fs-796298-bubble-pop-1"],
    "question": ["chime/fs-570243-point-in-space-ogg-1", "shimmer/fs-376746-fantasy-ui-button-2-wav-2"],
    "surprise": ["shimmer/fs-452371-small-bell-2", "riser/fs-503799-reverse-fx-9-wav-1"],
    "whoosh": ["transition/fs-444623-swoosh-10-wav-1", "whoosh/fs-30240-swoop1-flac-1",
               "transition/fs-432294-swoosh-2-wav-2", "whoosh/fs-449992-whoosh-short-low-wav-1",
               "transition/fs-234533-wet-transition-1"],
    "swell": ["riser/fs-503802-reverse-fx-4-wav-1", "riser/fs-275512-reversed-riser-wav-1"],
    "impact": ["boom/fs-636624-deep-hit-2", "boom/fs-535615-low-bit-boom-1"],
    "fail": ["thump/fs-405537-thud-2-wav-1", "boom/fs-535615-low-bit-boom-1"],
}


def _exists(s):
    if os.path.isfile(s):
        return True
    if "/" in s:
        typ, rest = s.split("/", 1)
        return (KIT / typ / f"{rest}.wav").exists()
    return False


def available():
    return (KIT / "catalog.json").exists() and shutil.which("uv") is not None


def _sound(cat, used):
    if os.path.isfile(cat):  # 파일 경로를 직접 적은 경우
        return cat
    if "/" in cat:
        typ, rest = cat.split("/", 1)
        if (KIT / typ / f"{rest}.wav").exists():
            return cat
        cat = typ  # 예전 효과음 폴더의 고정 이름("pop/카툰 팝") → 같은 종류에서 고름
    opts = [o for o in (FAMILY.get(cat) or FAMILY["pop"]) if _exists(o)]
    if not PACK.exists():  # 사용자 효과음 폴더가 없는 PC: 키트에서 종류별 여러 개를 더해 돌려 씀
        opts += [o for o in KIT_FAMILY.get(cat, []) if _exists(o) and o not in opts]
    opts = opts or [o for o in LOCAL.get(cat, LOCAL["pop"]) if os.path.isfile(o)]
    k = used.get(cat, 0)
    used[cat] = k + 1
    return opts[k % len(opts)]


def _read(path):
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(pcm, np.float32)


def _peak_offset(x):  # mix.py 와 같은 계산 (2ms 포락선 최대)
    n = max(1, int(0.002 * SR))
    env = np.sqrt(np.convolve(x * x, np.ones(n) / n, mode="same"))
    return int(np.argmax(env))


def _cues(sfx_ev):
    used, cues = {}, []
    for k, (t, cat) in enumerate(sorted(sfx_ev, key=lambda e: e[0])):
        base = cat.split("/")[0]
        c = {"id": f"{k:02d}-{base}", "t": round(max(0.0, t), 4), "sound": _sound(cat, used), "align": "peak",
             "weight": "hero" if base in HERO else "support", "_cat": base}
        cues.append(c)
        if base in HERO:
            c.update({"stop_before": True, "build": True})
            # 같은 시점의 보조음은 히어로 '뒤'에 둬야 mix.py 가 보조음을 0.6배로 줄여 히어로 몸통을 가리지 않음
            cues.append({"id": f"{k:02d}-build", "t": c["t"], "sound": BUILD, "align": "peak", "weight": "support"})
    return cues


def _mix(work, total, cues):
    sheet = {"fps": 30, "duration_s": round(total, 3), "energy": "punchy", "kit": KIT.as_posix(),
             "room": {"decay_s": 0.4, "wet_db": -26}, "voice": [{"file": "vo.wav", "start_s": 0}], "duck_db": 0,
             "silence_ok": [[max(0.0, total - 0.8), total]],
             "cues": [{k: v for k, v in c.items() if not k.startswith("_")} for c in cues]}
    (work / "cues.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=1), encoding="utf-8")
    r = subprocess.run(["uv", "run", "--project", str(OPUS), str(OPUS / "scripts" / "mix.py"), "cues.json"],
                       cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("opus mix.py 실패: " + r.stderr[-1500:])
    return json.loads((work / "audio" / "mix-report.json").read_text(encoding="utf-8"))


def qa(video, work):
    """opus qa.py — FAIL/warn 줄만 돌려줌."""
    r = subprocess.run(["uv", "run", "--project", str(OPUS), str(OPUS / "scripts" / "qa.py"), str(video)],
                       cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    return [l.strip() for l in out.splitlines() if l.startswith(("FAIL", "warn"))]


def render(job, body, video, out, sfx_ev, total, work, rounds=2):
    """목소리만 든 body + 효과음 시점 → 믹스 → video 그림에 붙여 out 으로 → qa.py.
    qa 에서 '안 들림'이 나온 효과음은 같은 종류의 다음 후보로 바꿔 최대 rounds 번 다시 믹스 (PROMPT.md 규칙).
    돌려줌: (CapCut 용 효과음 목록, 목소리 음량 dB, 로그 줄들)"""
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(body), "-vn", "-ac", "2", "-ar", str(SR),
                    "-c:a", "pcm_s24le", str(work / "vo.wav")], check=True)
    cues = _cues(sfx_ev)
    log, swap = [], {}
    for rnd in range(rounds + 1):
        rep = _mix(work, total, cues)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(work / "audio" / "mix.wav"),
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)], check=True)
        bad = qa(out, work)
        fails = [l for l in bad if l.startswith("FAIL")]
        log.append(f"  opus qa {rnd + 1}회차: " + ("; ".join(bad) if bad else "모두 통과"))
        changed = False
        for l in fails:
            parts = l.split()
            if len(parts) >= 3 and parts[1] == "audible":
                c = next((c for c in cues if c["id"] == parts[2]), None)
                opts = FAMILY.get(c.get("_cat", "")) if c else None
                if opts and len(opts) > 1:
                    swap[c["id"]] = swap.get(c["id"], 0) + 1
                    taken = {x["sound"] for x in cues}
                    k0 = opts.index(c["sound"]) if c["sound"] in opts else -1
                    order = [(k0 + j) % len(opts) for j in range(1, len(opts) + 1)]
                    i = next((j for j in order if opts[j] not in taken), order[0])  # 영상 안에서 안 쓴 소리 먼저
                    log.append(f"    → {c['id']}: {c['sound']} 대신 {opts[i]}")
                    c["sound"] = opts[i]
                    changed = True
        if not changed or rnd == rounds:
            break
    M = rep["master_gain_db"]
    # 목소리 최종 음량 = 원래 목소리 대비 stems/voice.wav (마스터 적용) 비율
    v0, v1 = _read(work / "vo.wav"), _read(work / "audio" / "stems" / "voice.wav")
    n = min(len(v0), len(v1))
    voice_db = float(20 * np.log10(np.sqrt((v1[:n] ** 2).mean()) / (np.sqrt((v0[:n] ** 2).mean()) + 1e-12)))
    head = [f"효과음(opus): {rep['master_lufs']} LUFS, TP {rep['master_true_peak_db']} dBTP, 이벤트 {len(rep['events'])}개"]
    capcut = []
    for ev in rep["events"]:
        head.append(f"  {ev['peak_s']:.2f}s {ev['sound']} ({ev['gain_db']:+.1f}dB, {ev['limited_by']})")
        for i, f in enumerate(ev["files"]):
            x = _read(f)
            start = ev["peak_s"] - _peak_offset(x) / SR
            total_db = ev["gain_db"] + (0 if i == 0 else -4) + M  # mix.py: 둘째 층부터 -4dB
            capcut.append({"t": max(0.0, start), "cat": ev["id"], "name": Path(f).stem, "file": str(Path(f)),
                           "lead": max(0.0, -start), "gain_db": total_db - voice_db, "max_dur": None})
    return capcut, voice_db, head + log
