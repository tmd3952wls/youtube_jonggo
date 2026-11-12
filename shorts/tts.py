"""3단계: 내레이션 TTS.

provider
- fish     : Fish Audio API  (FISH_API_KEY, config tts.fish.reference_id)
- typecast : Typecast API    (TYPECAST_API_KEY, config tts.typecast.voice_id)
- capcut   : CapCut AI 목소리 (tools/capcut_voice.py 로 미리 만들어 둔 파일, config tts.capcut.voice)
- sapi     : Windows 기본 한국어 음성(Heami). 무료 테스트용, 품질 낮음.
결과는 앞뒤 무음을 잘라낸 48kHz 스테레오 wav. 같은 문장은 캐시 재사용.
"""
import hashlib
import json
import re
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .common import ffmpeg


def _post_audio(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"TTS API 오류 {e.code}: {e.read()[:500].decode('utf-8', 'replace')}")


def _fish(text, out, c):
    audio = _post_audio("https://api.fish.audio/v1/tts",
                        {"Authorization": f"Bearer {os.environ['FISH_API_KEY']}",
                         "Content-Type": "application/json", "model": c["model"]},
                        {"text": text, "reference_id": c["reference_id"] or None,
                         "format": "wav", "prosody": {"speed": c["speed"]}, "latency": "normal"})
    out.write_bytes(audio)


def typecast_voices(model, gender=None):
    """Typecast 보이스 목록 (GET /v3/voices)."""
    q = f"model={model}" + (f"&gender={gender}" if gender else "")
    req = urllib.request.Request(f"https://api.typecast.ai/v3/voices?{q}",
                                 headers={"X-API-KEY": os.environ["TYPECAST_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Typecast 오류 {e.code}: {e.read()[:500].decode('utf-8', 'replace')}")


def _typecast(text, out, c):
    if not c.get("voice_id"):
        raise RuntimeError("config.json 의 tts.typecast.voice_id 가 비어 있습니다 "
                           "(python make_short.py voices 로 찾기)")
    body = {"text": text, "model": c["model"], "voice_id": c["voice_id"], "language": "kor",
            "output": {"audio_format": "wav", "audio_tempo": c["speed"], "audio_pitch": c.get("pitch", 0)}}
    if c.get("emotion_preset"):
        body["prompt"] = {"emotion_type": "preset", "emotion_preset": c["emotion_preset"],
                          "emotion_intensity": c.get("emotion_intensity", 1.0)}
    elif c.get("smart_emotion", True):
        body["prompt"] = {"emotion_type": "smart"}
    audio = _post_audio("https://api.typecast.ai/v1/text-to-speech",
                        {"X-API-KEY": os.environ["TYPECAST_API_KEY"], "Content-Type": "application/json"},
                        body)
    out.write_bytes(audio)


def _sapi(text, out, c):
    t = text.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{c['voice']}'); $s.Rate={c['rate']}; "
          f"$s.SetOutputToWaveFile('{out}'); $s.Speak('{t}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


def _capcut(text, out: Path, c):
    """CapCut AI 목소리 (2026-11-12, 사용자 고정: 활기찬 바리톤 1.1배속). CapCut 은 외부 API 가 없어
    tools/capcut_voice.py make → CapCut 텍스트 읽기 → harvest 로 만든 assets/voices/<voice>/<md5>.wav 를 씀."""
    import shutil
    from .common import ASSETS
    f = ASSETS / "voices" / c["voice"] / (hashlib.md5(text.strip().encode("utf-8")).hexdigest()[:16] + ".wav")
    if not f.exists():
        raise RuntimeError(f"CapCut 목소리 파일 없음: {text} → python tools/capcut_voice.py make/harvest --job <job> 먼저")
    shutil.copy2(f, out)


PROVIDERS = {"fish": _fish, "typecast": _typecast, "sapi": _sapi, "capcut": _capcut}


def synth(text, tts_dir: Path, cfg):
    tcfg = cfg["tts"]
    prov = tcfg["provider"]
    pcfg = tcfg.get(prov, {})
    key = hashlib.md5(json.dumps([prov, pcfg, text], ensure_ascii=False).encode()).hexdigest()[:12]
    pause = tcfg.get("max_pause", 0.08)
    thr = int(tcfg.get("pause_threshold_db", -35))  # 2026-11-09: -35 로는 숨소리 섞인 0.3초 쉼이 안 줄어 '지루' → -30
    final = tts_dir / f"{key}_p{int(pause * 1000)}t{-thr}n.wav"  # n: 무음 판정 전에 음량을 맞춘 판 (2026-11-08)
    if final.exists():
        return final
    tts_dir.mkdir(parents=True, exist_ok=True)
    raw = tts_dir / f"{key}.raw.wav"  # API 원본은 보관 → 후처리만 바꿀 땐 다시 과금되지 않음
    if not raw.exists():
        PROVIDERS[prov](text, raw, pcfg)
    # 앞뒤 무음 제거 + 문장 사이 쉼을 max_pause 이하로 압축(말이 비지 않게) + 포맷 통일
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03"
    squeeze = (f"silenceremove=stop_periods=-1:stop_duration={pause}:stop_threshold={thr}dB:"
               f"stop_silence={pause}")
    # TTS 원본이 아주 작게 오는 경우(최고 -19dB)가 있어, 고정 -35dB 기준으로는 조용한 말끝("던지는데")까지
    # 무음으로 잘려 나갔음 → 먼저 최고 음량을 -1dB 로 맞춘 뒤 무음을 판정
    vd = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(raw), "-af", "volumedetect", "-f", "null", "-"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = re.search(r"max_volume: (-?[\d.]+) dB", vd)
    gain = -1.0 - float(m.group(1)) if m else 0.0
    af = f"volume={gain:.2f}dB,{trim},areverse,{trim},areverse,{squeeze}"
    extra = float(tcfg.get("extra_tempo", 1.0))
    if abs(extra - 1.0) > 0.01:
        af += f",atempo={extra}"
    ffmpeg("-i", raw, "-af", af, "-ar", "48000", "-ac", "2", final)
    return final
