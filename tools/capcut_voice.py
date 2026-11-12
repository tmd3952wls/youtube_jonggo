"""CapCut AI 목소리(텍스트 읽기)로 내레이션 만들기 (2026-11-12, 사용자 고정: 활기찬 바리톤 1.1배속).

CapCut 목소리는 외부 API 가 없어서 CapCut 화면에서 목소리를 입혀야 함:
  1) make    : plan.json 내레이션 문장만 텍스트로 깔린 CapCut 프로젝트 voice_<job>_<voice> 를 만듦
               (아직 목소리가 없는 문장만. 이미 만든 문장은 건너뜀)
  2) CapCut  : 그 프로젝트 열기 → 텍스트 전부 선택 → 텍스트 읽기 → '활기찬 바리톤' → 적용 → 닫기
               (Claude 가 컴퓨터 사용으로 직접 조작하거나 사용자가 직접)
  3) harvest : 생성된 음성을 문장별로 assets/voices/<voice>/<md5>.wav 로 가져옴.
               CapCut 에서 배속을 줬으면 그 값, 안 줬으면 config tts.capcut.speed(1.1) 를 적용
그 뒤 config tts.provider = "capcut" 로 렌더하면 그 음성을 씀.

  python tools/capcut_voice.py make --job vhs_trio
  python tools/capcut_voice.py harvest --job vhs_trio
  python tools/capcut_voice.py status --job vhs_trio
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from shorts import capcut as C  # noqa: E402
from shorts.common import ASSETS, JOBS, load_config, read_json, write_json  # noqa: E402
from shorts.render import plain  # noqa: E402


def key(text):
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()[:16]


def voice_cfg():
    return load_config()["tts"]["capcut"]


def lines_of(job):
    out = []
    for s in read_json(JOBS / job / "plan.json")["segments"]:
        if s.get("type", "narration") == "narration" and s.get("text"):
            t = plain(s["text"]).strip()
            if t and t not in out:
                out.append(t)
    return out


def missing(job, voice):
    d = ASSETS / "voices" / voice
    return [t for t in lines_of(job) if not (d / f"{key(t)}.wav").exists()]


def make(job, voice):
    lines = missing(job, voice)
    if not lines:
        print("모든 문장에 목소리가 이미 있음 — make 필요 없음")
        return
    name = f"voice_{job}_{voice}"
    folder = C.draft_root() / name
    if folder.exists():
        if C.capcut_running():
            sys.exit(f"CapCut 이 켜져 있어 기존 {name} 를 지울 수 없음 — CapCut 을 끄고 다시")
        shutil.rmtree(folder)  # 이 도구가 만든 임시 목소리 프로젝트만 (사용자 프로젝트 아님)
    t, spans = 0.0, []
    for s in lines:
        dur = max(2.0, len(s.replace(" ", "")) * 0.22 + 1.0)
        spans.append((s, t, dur))
        t += dur + 0.5
    d = C.Draft(t, name, folder)
    tr = d.track("text", "내레이션 문장")
    for s, a, dur in spans:
        d.add_text(tr, [(s, "white")], a, dur, 8.0, 960, shadow=False)
    meta = d.write()
    write_json(JOBS / job / f"capcut_voice_{voice}.json", {"draft": folder.as_posix(), "voice": voice, "lines": lines})
    if C.capcut_running():
        pend = read_json(C.PENDING) if C.PENDING.exists() else []
        write_json(C.PENDING, pend + [folder.as_posix()])
        print(f"만듦: {name} (문장 {len(lines)}개) — CapCut 이 켜져 있어 목록 등록 못 함: CapCut 끄고 "
              f"python -m shorts.capcut --register-only")
    else:
        print(f"만듦: {name} (문장 {len(lines)}개) —", C.register(folder, meta))
    for s in lines:
        print("  ·", s)


def harvest(job, voice):
    man = read_json(JOBS / job / f"capcut_voice_{voice}.json")
    folder = Path(man["draft"])
    d = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
    M = d["materials"]
    audios = {a["id"]: a for a in M.get("audios", [])}
    texts = {t["id"]: t for t in M.get("texts", [])}
    segs = [(s, tr["type"]) for tr in d["tracks"] for s in tr["segments"]]
    tsegs = [s for s, k in segs if k == "text" and s["material_id"] in texts]
    asegs = [s for s, k in segs if k == "audio" and s["material_id"] in audios]
    want_tone = voice_cfg().get("tone_type")
    default_speed = float(voice_cfg().get("speed", 1.0))
    out_dir = ASSETS / "voices" / voice
    out_dir.mkdir(parents=True, exist_ok=True)
    got = 0
    for ts in tsegs:
        tm = texts[ts["material_id"]]
        text = json.loads(tm["content"])["text"].strip()
        aid = next((i for i in tm.get("text_to_audio_ids") or [] if i in audios), None)
        aid = aid or next((k for k, a in audios.items() if a.get("text_id") == tm["id"]), None)
        if not aid:
            print("  ✗ 음성 없음:", text)
            continue
        a = audios[aid]
        if want_tone and a.get("tone_type") and a["tone_type"] != want_tone:
            print(f"  ✗ 다른 목소리({a['tone_type']}):", text)
            continue
        src = Path(re.sub(r"^##_draftpath_placeholder_[^#]+_##", folder.as_posix(), a["path"]))
        if not src.exists():
            print("  ✗ 파일 없음:", src)
            continue
        seg = next((s for s in asegs if s["material_id"] == aid), None)
        speed = float(seg.get("speed") or 1.0) if seg else 1.0
        if abs(speed - 1) < 1e-3:
            speed = default_speed  # CapCut 에서 배속을 안 줬으면 고정값 1.1
        dst = out_dir / f"{key(text)}.wav"
        af = ["-af", f"atempo={speed}"] if abs(speed - 1) > 1e-3 else []
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *af, "-ar", "48000", "-ac", "2", str(dst)],
                       check=True)
        print(f"  ✓ x{speed} {text}  ← {a.get('tone_type', '')}")
        got += 1
    print(f"가져옴 {got}/{len(man['lines'])} → {out_dir}")
    left = missing(job, voice)
    print("남은 문장 없음" if not left else f"아직 목소리 없는 문장 {len(left)}개: " + " / ".join(left))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "harvest", "status"])
    ap.add_argument("--job", required=True)
    ap.add_argument("--voice", default="")
    a = ap.parse_args()
    voice = a.voice or voice_cfg()["voice"]
    if a.cmd == "make":
        make(a.job, voice)
    elif a.cmd == "harvest":
        harvest(a.job, voice)
    else:
        left = missing(a.job, voice)
        print(f"문장 {len(lines_of(a.job))}개 중 목소리 없음 {len(left)}개")
        for s in left:
            print("  ·", s)


if __name__ == "__main__":
    main()
