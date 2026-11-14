"""사용자 효과음 폴더(600개) → 카테고리 색인.

파일명(한국어 의성어)으로 카테고리를 나누고, 각 파일의 길이·음량을 재서
렌더할 때 효과음 크기가 들쭉날쭉하지 않도록 보정값(gain)을 함께 저장한다.
  python -m shorts.sfxlib   → assets/sfx_library.json 생성
"""
import json
import random
import re
import subprocess
import sys
from pathlib import Path

from .common import ASSETS, duration, load_config

# 카테고리: (설명, 파일명 패턴들). 앞 카테고리가 우선. 대본 작성 LLM 에게도 이 목록을 보여준다.
CATEGORIES = {
    "whoosh":   ("장면 전환·빠른 등장", r"휘익|휙|쉭|스우쉬|Whoosh|화면전환|휘리릭|훅~|후욱|쉬이익|슉|후윽|후우우욱|화면전환효과음|넘기는소리\(장면효과\)|다음 장면 효과음|짧은 휙|휙하고 히트|쉭쉭 스우쉬|휘스우쉬|스르륵|샤스륵"),
    "pop":      ("자막/물건 톡 등장", r"뿅|팝|뽀옹|뾰옥|뽀욕|Poink|뾰잉|띡\(버튼|버튼 클릭|삡|똥\(버튼|띡\(버튼음\)|똥\(버튼음\)|떵_뿅_뼝|Cute Winking|어색한 물방울|바운스\d"),
    "cash":     ("희망가·돈 금액·가격표", r"Cash Register|철컥띵|캐셔|코인|띠룽"),
    "ding":     ("숫자·가격·제안가 공개", r"^띵$|띠링|^딩$|딩동|정답음|Ting|Correct|또잉|티딩|갑자기 띵|딩 다음 페이지|딩동댕동|^Correct"),
    "impact":   ("반전·충격 쾅", r"^쿵|두둥탁|둥\(큰북|슉쿵|쿠웅|푸우쿵|쿵우우|퉁우우|텅|^텅$|쿠웅_떨어지며|둥\(큰북|폭발 -b묵직|펑 효과음"),
    "surprise": ("놀람·충격 리액션", r"놀람|뜨헉|허얽|헉!!|Shocked|깜짝|허엉|What|Oh No|우와와|와와와우|놀람_반전|깜짝 놀라는 전자음|Shocked\d|마법봉"),
    "question": ("의문·황당·복선", r"물음표|띠용|띠요용|먼가 이상할때|띠우웅|미-밋|어이없음|띠유유유|띠요용"),
    "success":  ("성공·계약 성사", r"TaDaa|짜잔|타랴|빰빠|레벨업|축복|Good|Unlocked|레벨업2|Celebration|마술 2|Magical|Rising"),
    "fail":     ("실패·거절·결렬", r"오답|실패|Wrong|Wah Wah|망함|좌절|허무한|게임 오버|Fail|No no no|Windows Error"),
    "tension":  ("긴장·궁금증", r"심장|째깍|Drum Roll|미스테리 드럼|긴장|Suspense|Clock Ticking"),
    "caption":  ("자막 강조·말풍선·핑거스냅", r"캡션|타자기|Type Writer|핑거스냅|핑거 스냅|캡션 사운드 타탁|타당_타닥"),
    "camera":   ("연식·디테일 클로즈업 찰칵", r"찰칵|카메라|셔터|써터"),
    "swell":    ("분위기 고조 스르륵", r"스르륵|샤스륵"),
}
# 길게 끌거나 배경음악 성격인 파일은 제외
EXCLUDE = r"와$|웃음|Wow|What|헉|우와|배경|BGM|브금|음악|오케스트라|소리큼|싸이렌|사이렌|전주곡|진행중|분위기깔기"


def _lufs(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace").stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r)
    return (float(i[-1]) if i else -30.0), (float(p[-1]) if p else 0.0)


def build_index(cfg):
    lib = Path(cfg["sfx_library"]["dir"])
    S = cfg["sfx_library"]
    index = {k: [] for k in CATEGORIES}
    files = sorted(lib.glob("*.mp3")) + sorted(lib.glob("*.wav"))
    # 사람 목소리가 섞인 효과음(와우, What, 웃음 등)은 대사와 겹치면 어색 → 제외 (tools/sfx_voice_check.py 결과)
    vp = ASSETS / "sfx_voice.json"
    voice = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else {}
    for f in files:
        name = re.sub(r"^\d+_", "", f.stem)
        if re.search(EXCLUDE, name):
            continue
        if (voice.get(str(f)) or 0) >= S["voice_max"]:
            continue
        cat = next((k for k, (_, pat) in CATEGORIES.items() if re.search(pat, name)), None)
        if not cat:
            continue
        d = duration(f)
        if d > S.get("max_sec_by_cat", {}).get(cat, S["max_sec"]):
            continue
        lufs, peak = _lufs(f)
        # 참고 영상 분석: 효과음 피크 ≈ 목소리보다 약간 낮게. 짧은 소리는 LUFS 가 낮게 나오므로 피크 기준으로 맞춤
        gain = S["target_peak_db"] - peak
        index[cat].append({"file": str(f), "name": name, "dur": round(d, 2),
                           "gain_db": round(gain, 1)})
        print(f"  {cat:9s} {name}  ({d:.1f}s, {lufs:.0f} LUFS → {gain:+.1f}dB)")
    out = ASSETS / "sfx_library.json"
    out.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{sum(map(len, index.values()))}개 색인 → {out}")
    return index


_cache = None
# 카테고리가 비었을 때 대신 쓸 카테고리 (목소리 섞인 소리를 빼면서 비는 경우)
FALLBACK = {"impact": "whoosh", "tension": "ding", "surprise": "pop", "laugh": "pop", "cheer": "success",
            "question": "pop", "fail": "pop"}


def pick(category, seed):
    """카테고리에서 효과음 하나 선택 (같은 영상 안에서는 재현 가능하게 seed 사용)."""
    global _cache
    if _cache is None:
        p = ASSETS / "sfx_library.json"
        _cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        # tools/sfx_clap_check.py 결과: 실제 소리가 카테고리와 다른 파일은 빼고, 앞 무음 길이(lead)를 붙여 둠
        cp = ASSETS / "sfx_clap.json"
        clap = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {}
        _cache = {c: [{**it, "lead": clap.get(it["file"], {}).get("lead", 0.0)} for it in items
                      if clap.get(it["file"], {}).get("ok", True) and Path(it["file"]).exists()]
                  for c, items in _cache.items()}
    if "/" in category:  # "pop/카툰 팝" = 편집기에서 고른 특정 파일 고정
        cat, name = category.split("/", 1)
        hit = next((it for it in _cache.get(cat, []) if it["name"] == name), None)
        if hit:
            return {**hit, "pinned": True}
        category = cat
    items = _cache.get(category) or _cache.get(FALLBACK.get(category, ""), [])
    if not items:
        return None
    return random.Random(f"{seed}-{category}").choice(items)


def category_help():
    return "\n".join(f'- "{k}": {d}' for k, (d, _) in CATEGORIES.items())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    build_index(load_config())
