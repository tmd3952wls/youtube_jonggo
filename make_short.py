"""쇼츠 자동 생성 CLI.

  python make_short.py new <유튜브URL|파일> [--start 초] [--end 초] [--name 이름] [--context "메모"]
      → 다운로드/자르기 → 영어 전사 → 대본(API 있으면 자동) → TTS → 렌더
  python make_short.py render <jobs/이름>
      → plan.json 을 고친 뒤 다시 렌더 (TTS 는 캐시 재사용)
"""
import argparse
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from shorts.common import JOBS, load_config  # noqa: E402
from shorts import render as R, script as S, source  # noqa: E402


def cmd_new(a, cfg):
    name = a.name or time.strftime("%Y%m%d_%H%M%S")
    job = JOBS / name
    job.mkdir(parents=True, exist_ok=True)
    print(f"[1/4] 원본 준비 → {job}")
    video = source.fetch(a.src, job, a.start, a.end)
    print(f"[2/4] 영어 전사 (whisper {cfg['whisper_model']})")
    tr = source.transcribe(video, job, cfg["whisper_model"])
    print(f"      {len(tr)}줄 전사 완료")
    print("[3/4] 대본 생성")
    if not S.generate(job, cfg, a.context):
        print(f"\n  API 키가 없어 수동 모드입니다.\n"
              f"  1) {job / 'prompt.txt'} 내용을 ChatGPT 또는 Claude 에 붙여넣기\n"
              f"  2) 받은 JSON 을 {job / 'plan.json'} 로 저장\n"
              f"  3) python make_short.py render {job}")
        return
    do_render(job, cfg)


def do_render(job, cfg):
    from shorts.common import read_json
    plan = S.validate(read_json(job / "plan.json"), read_json(job / "transcript.json"),
                      cfg["script"].get("krw_rate", S.KRW_RATE))
    from shorts.common import write_json
    write_json(job / "plan.json", plan)  # 원화 병기 등 자동 수정 반영
    print(f"[4/4] TTS({cfg['tts']['provider']}) + 렌더")
    out, total = R.render(job, cfg)
    warn = "  ⚠ 60초 초과 — plan.json 에서 clip/내레이션을 줄이세요" if total > 59.5 else ""
    print(f"\n완료: {out}  ({total:.1f}초){warn}\n업로드 정보: {job / 'upload.txt'}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("src")
    n.add_argument("--start", type=float)
    n.add_argument("--end", type=float)
    n.add_argument("--name")
    n.add_argument("--context", default="", help="프로그램명/상황 메모 (대본 품질 향상)")
    r = sub.add_parser("render")
    r.add_argument("job")
    rm = sub.add_parser("remake", help="다운로드·전사는 그대로 두고 대본만 새로 써서 렌더")
    rm.add_argument("job")
    rm.add_argument("--context", default="")
    v = sub.add_parser("voices", help="Typecast 보이스 목록")
    v.add_argument("--gender", choices=["male", "female"])
    v.add_argument("--find", default="", help="이름/용도 검색어")
    t = sub.add_parser("tts-test", help="보이스 미리듣기 wav 생성")
    t.add_argument("voice_ids", nargs="+")
    t.add_argument("--text", default="평범해 보이는 치킨으로 무려 100만 달러를 제안받은 이들이 있습니다. "
                                      "하지만 이들은 이 제안을 거절하는데요")
    a = p.parse_args()
    cfg = load_config()
    if a.cmd == "new":
        cmd_new(a, cfg)
    elif a.cmd == "render":
        do_render(Path(a.job).resolve(), cfg)
    elif a.cmd == "remake":
        job = Path(a.job).resolve()
        print("[3/4] 대본 다시 생성")
        if S.generate(job, cfg, a.context):
            do_render(job, cfg)
    elif a.cmd == "voices":
        cmd_voices(a, cfg)
    else:
        cmd_tts_test(a, cfg)


def cmd_voices(a, cfg):
    from shorts.tts import typecast_voices
    voices = typecast_voices(cfg["tts"]["typecast"]["model"], a.gender)
    n = 0
    for vo in voices:
        name = vo.get("voice_name")
        name = name.get("kor") or name.get("eng") if isinstance(name, dict) else name
        line = (f"{vo['voice_id']}  {name}  [{vo.get('gender', '')}/{vo.get('age', '')}]  "
                f"{', '.join(vo.get('use_cases') or [])}")
        if a.find.lower() in line.lower():
            print(line)
            n += 1
    print(f"\n{n}개 (모델 {cfg['tts']['typecast']['model']})")


def cmd_tts_test(a, cfg):
    import copy
    from shorts.common import ROOT
    from shorts.tts import synth
    out_dir = ROOT / "jobs" / "_voice_test"
    for vid in a.voice_ids:
        c = copy.deepcopy(cfg)
        c["tts"]["provider"] = "typecast"
        c["tts"]["typecast"]["voice_id"] = vid
        wav = synth(a.text, out_dir, c)
        dst = out_dir / f"{vid}.wav"
        dst.write_bytes(wav.read_bytes())
        print(dst)


if __name__ == "__main__":
    main()
