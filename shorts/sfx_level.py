"""효과음 믹스 — '귀에 들리는 크기'를 목소리에 맞춰 고르게 (2026-11-09 사용자 요청).

예전 opus 믹스(sfx_opus.py)는 효과음을 '한 주파수 대역에서만 겨우 들리게' 맞춰서,
어떤 소리는 크고 어떤 소리는 목소리·영상 소리에 묻혀 안 들렸다.

여기서는:
  1) 효과음마다 귀가 느끼는 크기(BS.1770 K-가중, 0.4초 순간 음량의 최대값)를 잰다
  2) 그 순간 앞뒤 0.5초 목소리 크기를 잰다 (조용한 곳이면 전체 목소리 크기)
  3) 효과음 크기 = 목소리 크기 + 종류별 차이(OFFSET) 가 되도록 음량을 정한다
  4) 소리의 가장 센 지점(2ms 포락선 최대)을 사건 프레임(컷·자막 등장)에 정확히 맞춘다
  5) 섞은 뒤 다시 재서 소리마다 '목소리 대비 몇 dB 로 들리는지' 표로 남긴다

소리 고르기(FAMILY)는 sfx_opus.py 의 2026-11-08 팔레트(참고 쇼츠 15편 분석)를 그대로 쓴다.
"""
import subprocess
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from .sfx_opus import BUILD, FAMILY, HERO, KIT, _exists, _sound

SR = 48000
# 목소리 대비 들리는 크기(dB). 0 = 목소리와 같은 크기로 들림
OFFSET = {"impact": -1.0, "success": -2.5, "ding": -3.0, "pop": -3.0, "question": -3.0, "surprise": -3.0,
          "camera": -3.0, "cash": -2.5, "whoosh": -3.5, "fail": -3.0, "caption": -4.5, "swell": -5.5, "tension": -6.5}
PRE_MAX, POST_MAX = 1.5, 2.2  # 가장 센 지점 앞뒤로 쓰는 최대 길이(초) — 여운/엔딩음이 중간에 잘리지 않게


def _read(path):
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(pcm, np.float32).reshape(-1, 2).copy()


def _kw(x):
    """BS.1770 K-가중 필터 (48kHz)."""
    y = lfilter([1.53512485958697, -2.69169618940638, 1.19839281085285], [1, -1.69065929318241, 0.73248077421585], x, axis=0)
    return lfilter([1.0, -2.0, 1.0], [1, -1.99004745483398, 0.99007225036621], y, axis=0)


def _momentary(x, hop=0.01):
    """0.4초 순간 음량(LUFS) 곡선, 10ms 간격. 반환 (시각 배열, 값 배열) — 시각은 창의 가운데."""
    z = (_kw(x) ** 2).sum(axis=1)
    w, h = int(0.4 * SR), int(hop * SR)
    if len(z) < w:
        z = np.pad(z, (0, w - len(z)))
    c = np.concatenate([[0.0], np.cumsum(z)])
    starts = np.arange(0, len(z) - w + 1, h)
    p = (c[starts + w] - c[starts]) / w
    return (starts + w / 2) / SR, -0.691 + 10 * np.log10(p + 1e-12)


def _integrated(x):
    """BS.1770 통합 음량 (게이트 -70 / 상대 -10)."""
    t, m = _momentary(x, hop=0.1)
    m = m[m > -70]
    if not len(m):
        return -70.0
    rel = 10 * np.log10(np.mean(10 ** (m / 10))) - 10
    m = m[m > rel]
    return float(10 * np.log10(np.mean(10 ** (m / 10))))


def _peak_idx(x):
    n = max(1, int(0.002 * SR))
    e = np.convolve((x ** 2).sum(axis=1), np.ones(n) / n, mode="same")
    return int(np.argmax(e))


def _file_of(sound):
    if Path(sound).is_file():
        return str(sound)
    typ, rest = sound.split("/", 1)
    return str(KIT / typ / f"{rest}.wav")


def _prep(sound):
    """소리 파일 → (가장 센 지점 앞뒤로 자른 신호, 자른 신호 안 피크 위치, 파일 앞에서 자른 초)."""
    x = _read(_file_of(sound))
    p = _peak_idx(x)
    a, b = max(0, p - int(PRE_MAX * SR)), min(len(x), p + int(POST_MAX * SR))
    y = x[a:b].copy()
    fi, fo = int(0.01 * SR), int(0.25 * SR)
    if a > 0:
        y[:fi] *= np.linspace(0, 1, min(fi, len(y)))[:, None]
    if b < len(x):
        y[-fo:] *= np.linspace(1, 0, min(fo, len(y)))[:, None]
    return y, p - a, a / SR


def _load_bgm(path, start, n):
    """BGM 을 start 초부터 n 샘플만큼 (짧으면 뒤를 무음으로)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(path), "-vn", "-ac", "2",
                          "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    y = np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)[:n]
    if len(y) < n:
        y = np.vstack([y, np.zeros((n - len(y), 2))])
    return y


def render(job, body, video, out, sfx_ev, total, work, master_lufs=-14.0, bgm=None):
    """목소리만 든 body + 효과음 (시각, 종류) [+ BGM] → 믹스 → video 그림에 붙여 out.
    bgm = {"file", "start"(곡 시작 지점 초), "rel_lu"(목소리 대비 음량, 기본 -7.0), "fade_out"(끝 페이드 초)}
    돌려줌: (CapCut 용 효과음 목록, 목소리 최종 음량 dB, 로그 줄들)"""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    voice = _read(body)[:int(round(total * SR))]  # 소리를 영상 길이에 딱 맞춤 (BGM 끝이 영상 끝과 같게)
    N = len(voice)
    vt, vm = _momentary(voice)
    v_int = _integrated(voice)
    used, events = {}, []
    for t, cat in sorted(sfx_ev, key=lambda e: e[0]):
        base = cat.split("/")[0]
        events.append((t, base, _sound(cat, used)))
        if base in HERO and t >= 1.0:  # 큰 한 방 앞: 역재생 심벌이 차오르다 그 프레임에서 터짐 (영상 맨 앞이면 차오를 자리가 없어 생략)
            events.append((t, "swell", BUILD if _exists(BUILD) else _sound("swell", used)))
    sfx = np.zeros_like(voice)
    log, capcut, rows = [f"효과음(고른 크기): 목소리 {v_int:.1f} LUFS 기준"], [], []
    for t, cat, sound in events:
        y, p, head_cut = _prep(sound)
        _, sm = _momentary(y)
        s_loud = float(sm.max())
        sel = (vt > t - 0.5) & (vt < t + 0.5) & (vm > v_int - 20)  # 그 순간 말하고 있으면 그 목소리 크기
        v_loc = float(np.percentile(vm[sel], 75)) if sel.any() else v_int
        g = v_loc + OFFSET.get(cat, -3.0) - s_loud
        start = int(round(t * SR)) - p  # 가장 센 지점이 정확히 t 에
        a0, a1 = max(0, start), min(N, start + len(y))
        if a1 > a0:
            sfx[a0:a1] += y[a0 - start:a1 - start] * 10 ** (g / 20)
        rows.append((t, cat, Path(sound).stem, g, v_loc, s_loud))
        capcut.append({"t": max(0.0, start / SR), "cat": cat, "name": Path(sound).stem, "file": _file_of(sound),
                       "lead": head_cut + max(0.0, -start / SR), "gain_db": float(g),
                       "max_dur": float((a1 - a0) / SR)})
    mix = voice + sfx
    if bgm and bgm.get("file") and Path(bgm["file"]).exists():
        # 사용자 CapCut 기준(coconut_cocotaps_v16, 2026-11-11): 곡을 영상 처음부터 일정한 크기로 깔고(덕킹 없음)
        # 목소리보다 7 LU 작게, 영상이 끝나는 지점에서 같이 끊김
        b = _load_bgm(bgm["file"], float(bgm.get("start", 0.0)), N)
        g = v_int + float(bgm.get("rel_lu", -7.0)) - _integrated(b)
        b *= 10 ** (g / 20)
        nf = int(float(bgm.get("fade_out", 0.05)) * SR)
        if nf > 0:
            b[-nf:] *= np.linspace(1.0, 0.0, nf)[:, None]
        mix = mix + b
        log.append(f"  BGM {Path(bgm['file']).stem[:30]} (곡 {float(bgm.get('start', 0)):.2f}초부터) "
                   f"목소리 대비 {float(bgm.get('rel_lu', -7.0)):+.1f} LU, 영상 끝({N / SR:.2f}초)에서 끊음")
    M = master_lufs - _integrated(mix)
    mix_path = work / "mix.wav"
    (mix * 10 ** (M / 20)).astype(np.float32).tofile(work / "mix.f32")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(work / "mix.f32"),
                    "-af", "alimiter=limit=0.89:attack=3:release=60:level=0", "-c:a", "pcm_s24le", str(mix_path)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(mix_path), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)],
                   check=True)
    # 검증: 섞인 결과에서 효과음만의 순간 최대 크기 - 그 순간 목소리 크기
    st, smm = _momentary(sfx)
    for (t, cat, name, g, v_loc, s_loud) in rows:
        w = (st > t - 0.25) & (st < t + 0.45)
        got = float(smm[w].max()) - v_loc if w.any() else float("nan")
        log.append(f"  {t:6.2f}s {cat:9s} {name[:34]:34s} 음량 {g:+6.1f}dB → 목소리 대비 {got:+5.1f}dB 로 들림")
    log.append(f"  마스터 {M:+.1f}dB → {master_lufs} LUFS")
    return capcut, float(M), log
