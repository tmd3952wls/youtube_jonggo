"""4단계: plan.json + source.mp4 + TTS → 최종 쇼츠 mp4.

레이아웃(1080x1920)
  0 ~ title_h            : 검정 + 2줄 상단 제목
  title_h ~ +video_h     : 원본 영상(가운데 크롭) + 자막
  나머지                  : 검정 + 출처 표기
"""
import os
import random
import re
import subprocess
from pathlib import Path

from .common import ASSETS, ROOT, duration, ffmpeg, filter_path, read_json, run, write_json
from .align import align_all, chunk_times, proportional_times
from .sfx import ensure_sfx, sfx_path
from .align import ensure_words
from .faces import crop_box, face_in_shot
from .faces import load_cache as load_faces
from .faces import save_cache as save_faces
from .scenes import broll_shots, detect_cuts
from .sfxlib import pick as pick_sfx
from .subfx import PALETTE, circle_events, money_green, neon_events, open_state, parse_spans
from .tts import synth

COLORS = {  # RGB hex
    "white": "FFFFFF", "yellow": "FFE81A", "red": "FF2E2E", "cyan": "35E0FF",
    "orange": "FF9A1F", "green": "A6FF3C", "pink": "FF5FA2", "gray": "9A9A9A",
}
SFX_OFFSET = {"whoosh": -0.08}
LEGACY_SFX = {"boom": "impact"}  # 예전 대본의 효과음 이름 → 라이브러리 카테고리


def ass_color(name_or_hex):
    h = COLORS.get(name_or_hex, name_or_hex).lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


# 자막 등장: 흐릿하고 크게 → 0.11초 만에 또렷하게 (참고 영상의 블러 인)
ENTER = r"\blur7\fscx114\fscy114\alpha&H70&\t(0,110,\blur0.6\fscx100\fscy100\alpha&H00&)"


def plain(text):
    """강조 표시 [ ], [색:  ] 제거 (TTS·정렬·검증용)."""
    return re.sub(r"\[(\w+:)?|\]", "", text)


def keywordize(text, base, kw, on):
    """[강조어] → 강조 색. 덩어리 경계를 넘는 강조를 위해 on 상태를 이어받음."""
    out = [rf"{{\1c{kw}}}"] if on else []
    for ch in text:
        if ch == "[":
            on = True
            out.append(rf"{{\1c{kw}}}")
        elif ch == "]":
            on = False
            out.append(rf"{{\1c{base}}}")
        else:
            out.append(ch)
    return "".join(out), on


def vis_w(text):
    """자막 한 줄의 대략적인 가로 폭(em). 한글 1, 영문·숫자 0.6, 공백 0.3, 기호 0.45."""
    w = 0.0
    for ch in plain(text):
        if ch == " ":
            w += 0.3
        elif "가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ":
            w += 1.0
        elif ch.isascii() and ch.isalnum():
            w += 0.6
        else:
            w += 0.45
    return w


NUM_TOKEN = re.compile(r"[$]?[\d.,]+(억|천만|백만|만|천|백)*(년|월)?|약")  # "약 3억 5천만 원" 은 한 덩어리


def sub_tokens(text):
    """어절 목록. 숫자 덩어리(17만 5천 달러)와 괄호 안((약 3.5억))은 한 덩어리로 묶어 중간에서 끊기지 않게.
    괄호 앞에서는 끊을 수 있음: '25만 달러' / '(약 3.5억)를 원해요'."""
    out, depth = [], 0
    for w in text.split():
        last = plain(out[-1]).split()[-1] if out else ""
        if out and (depth > 0 or NUM_TOKEN.fullmatch(last)):
            out[-1] += " " + w
        else:
            out.append(w)
        depth = max(0, depth + plain(w).count("(") - plain(w).count(")"))
    return out


BOUND = re.compile(r"^(뒤|후|것|게|수|때|적|중|만큼|듯|전|채|대로|뿐|따름)(?![가-힣]{2})")


def split_chunks(text, max_em):
    """자막을 한 줄짜리 덩어리들로 (2줄 대신 순서대로 넘김).
    덩어리 수를 최소로, 그다음 폭이 고르게 (끝에 한 단어만 남는 일 없게) — 작은 DP."""
    toks = sub_tokens(text)
    if vis_w(text) <= max_em or len(toks) <= 1:
        return [text]
    n = len(toks)
    width = lambda i, j: vis_w(" ".join(toks[i:j]))
    INF = (10 ** 9, 0.0)
    best = [INF] * (n + 1)  # best[i] = (덩어리 수, 폭 편차 합) for toks[:i]
    prev = [0] * (n + 1)
    best[0] = (0, 0.0)
    for j in range(1, n + 1):
        for i in range(j):
            w = width(i, j)
            if w > max_em and j - i > 1:
                continue
            bad = 60.0 if j < n and BOUND.match(plain(toks[j])) else 0.0  # 다음 줄이 '뒤/것/수…' 로 시작하면 감점
            c = (best[i][0] + 1, best[i][1] + (max_em - w) ** 2 + bad)
            if c < best[j]:
                best[j], prev[j] = c, i
    chunks, j = [], n
    while j > 0:
        chunks.insert(0, " ".join(toks[prev[j]:j]))
        j = prev[j]
    return chunks


NL = chr(10)  # 자막 안 줄바꿈 (parse_spans 가 두 줄로 처리)


def merge_short(items, min_d):
    """[(a, b, text[, group])] 에서 너무 짧게 지나가는 자막(< min_d 초)을 옆 자막과 합쳐 두 줄로 보여줌 (깜빡임 방지).
    두 줄까지만, 그리고 group(화자)이 같을 때만 합친다."""
    out = []
    for it in items:
        a, b, txt = it[:3]
        g = it[3] if len(it) > 3 else None
        short = (b - a) < min_d or (out and (out[-1][1] - out[-1][0]) < min_d)
        same = out and (out[-1][3] if len(out[-1]) > 3 else None) == g
        if out and short and same and NL not in out[-1][2] and NL not in txt:
            out[-1] = (out[-1][0], b, out[-1][2] + NL + txt) + tuple(out[-1][3:])
        else:
            out.append(tuple(it))
    return out


def _round_rect(w, h, r):
    """(0,0)~(w,h) 둥근 사각형 ASS 드로잉 경로."""
    return (f"m {r:.0f} 0 l {w - r:.0f} 0 b {w:.0f} 0 {w:.0f} 0 {w:.0f} {r:.0f} "
            f"l {w:.0f} {h - r:.0f} b {w:.0f} {h:.0f} {w:.0f} {h:.0f} {w - r:.0f} {h:.0f} "
            f"l {r:.0f} {h:.0f} b 0 {h:.0f} 0 {h:.0f} 0 {h - r:.0f} l 0 {r:.0f} b 0 0 0 0 {r:.0f} 0")


def boxcut_events(kind, txt, pos, S, color=None):
    """참고 채널(기억의순간) 자막 문법 — 2026-11-11 프레임 분석:
    - 등장·퇴장 애니메이션 없음: 다음 자막으로 한 프레임 만에 바로 교체 (확대·페이드·블러 없음)
    - 내레이션: 흰 글씨 + 반투명 검은 둥근 상자, 강조어만 노랑
    - 대사: 상자 없이 화자별 색 글씨(질문자 하늘색 / 대답자 연두 …) + 두꺼운 검은 테두리
    반환: [(layer, ass_text)]"""
    from .subfx import parse_spans, NEON
    x, y = pos
    lines = txt.split(NL)
    out = []
    if kind == "narr":
        fs = S["narr_size"]
        body = "".join(rf"{{\1c{ass_color(NEON.get(c.split('_')[0], c.split('_')[0]))}}}" + t_.replace(NL, r"\N")
                       for t_, c in parse_spans(txt, "yellow"))
        padx, pady = fs * 0.45, fs * 0.2
        w = max(vis_w(ln) for ln in lines) * fs * S.get("box_em_scale", 1.0) + 2 * padx
        h = len(lines) * fs * 1.2 + 2 * pady
        x0, y0 = x - w / 2, y + pady * 0.6 - h
        out.append((2, rf"{{\an7\pos({x0:.0f},{y0:.0f})\p1\bord0\shad0\blur0.6\1c&H000000&"
                       rf"\1a{S.get('box_alpha', '&H50&')}}}" + _round_rect(w, h, min(h / 2, fs * 0.32)) + r"{\p0}"))
        out.append((3, rf"{{\an2\pos({x},{y})\fs{fs}\b1\bord0\shad0\blur0.4}}" + body))
    else:
        fs = S["dial_size"]
        out.append((3, rf"{{\an2\pos({x},{y})\fs{fs}\b1\1c{ass_color(color or 'FFFFFF')}\3c&H000000&"
                       rf"\bord{S.get('dial_outline', 6)}\shad0\blur0.6}}" + plain(txt).replace(NL, r"\N")))
    return out


def carry_color(chunks):
    """덩어리 경계를 넘는 [색: ...] 강조를 다음 덩어리 앞에 다시 열어 줌."""
    out, open_tag = [], None
    for c in chunks:
        out.append((open_tag or "") + c)
        for m in re.finditer(r"\[(\w+:)?|\]", c):
            open_tag = m.group(0) if m.group(0) != "]" else None
    return out


def wrap2(text, max_chars):
    """대사 자막: 길면 가운데 근처 공백에서 2줄로."""
    if len(text) <= max_chars or " " not in text:
        return text
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut] + r"\N" + text[cut + 1:]


def video_filter(cfg):
    L = cfg["layout"]
    w, h = L["width"], L["video_h"]
    # 방송 하단 자막 배너(가격·이름 그래픽)가 들어가는 아래쪽 띠를 잘라냄:
    # 세로 keep 비율만 쓰고(top_y 부터), 그 안에서 패널 비율로 가운데 크롭
    keep, top_y = L.get("crop_keep_h", 1.0), L.get("crop_top", 0.0)
    return (f"crop=iw:ih*{keep}:0:ih*{top_y},"
            f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})',scale={w}:{h},setsar=1,fps=30,"
            "format=yuv420p")


def motion_filter(cfg, L, kind):
    """컷 전환 연출 (참고 영상 분석 기반).
    - 컷 직전 0.1초: 살짝 줌인(+punch) → 컷 직후 0.12초: 줌인 상태에서 원위치  ⇒ 이어지는 줌 펀치
    - 경계 2프레임 블러 (모션블러 느낌)
    - 내레이션(b-roll) 구간: 천천히 밀고 들어가는 줌(Ken Burns)
    """
    T = cfg["transition"]
    w, h = cfg["layout"]["width"], cfg["layout"]["video_h"]
    p, pin, pout = T["punch"], T["punch_in_sec"], T["punch_out_sec"]
    drift = T["narration_drift"] if kind == "narration" else T["clip_drift"]
    z = (f"1+{drift}*t/{L:.3f}+{p}*max(0\\,1-t/{pin})"
         f"+{p}*max(0\\,(t-{L - pout:.3f})/{pout})")
    bf = T["blur_frames"] / 30
    return (f"scale=w='trunc({w}*({z})/2)*2':h='trunc({h}*({z})/2)*2':eval=frame:flags=bicubic,"
            f"crop={w}:{h},boxblur={T['blur_radius']}:1:enable='lt(t\\,{bf:.3f})+gt(t\\,{L - bf:.3f})',"
            "setsar=1,format=yuv420p")


def lufs_of(path, start=None, dur=None):
    """구간 통합 음량(LUFS). 측정 불가(무음 등)면 None."""
    args = ["ffmpeg", "-hide_banner"]
    if start is not None:
        args += ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}"]
    r = subprocess.run(args + ["-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    v = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)
    x = float(v[-1]) if v else None
    return x if x is not None and x > -60 else None


def voice_gain(lufs, R):
    """목소리 구간을 voice_lufs 로 맞추는 보정(dB). 극단값은 제한."""
    if lufs is None:
        return 0.0
    return max(-R["voice_gain_max"], min(R["voice_gain_max"], R["voice_lufs"] - lufs))


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
       "-ar", "48000", "-ac", "2"]


def speech_pieces(words, ids, tr, R):
    """대사 클립 → 실제 말하는 구간들. 단어 사이가 max_inner_gap 보다 벌어지면 그 침묵은 잘라냄(점프컷)."""
    ws = [w for i in ids for w in words.get(str(i), [])]
    if not ws:  # 어절 정렬이 없으면 줄 단위
        ws = [{"s": tr[i]["start"], "e": tr[i]["end"]} for i in ids]
    ps = []
    for w in sorted(ws, key=lambda w: w["s"]):
        if ps and w["s"] - ps[-1][1] <= R["max_inner_gap"]:
            ps[-1][1] = max(ps[-1][1], w["e"])
        else:
            ps.append([w["s"], w["e"]])
    # 말은 절대 버리지 않는다. 조각 사이 침묵은:
    #  - ramp_max_gap 이하 → 그 구간 영상을 2프레임으로 빨리감기(스피드 램프): 소리는 안 비고 화면도 안 튐
    #  - 더 길면 → 점프컷(render_shots 에서 확대 배율을 번갈아 바꿔 의도된 편집처럼)
    pieces = []
    for k, (a, b) in enumerate(ps):
        a, b = max(0.0, a - R["word_pad_before"]), b + R["word_pad_after"]
        if pieces and pieces[-1][2] == "speech" and a - pieces[-1][1] <= R["ramp_max_gap"]:
            if a > pieces[-1][1]:
                pieces.append([pieces[-1][1], a, "ramp"])
            else:
                pieces[-1][1] = max(pieces[-1][1], b)
                continue
        pieces.append([a, b, "speech"])
    return pieces, ws


def pieces_to_shots(pieces, cuts, ws, R):
    """speech/ramp 조각 → 원본 컷 기준으로 쪼갠 (a, b, kind) 목록.
    침묵(ramp) 구간 안에 원본 컷이 있으면 그냥 하드컷으로 넘어가면 되므로 ramp 는 버림."""
    out = []
    for a, b, kind in pieces:
        if kind == "ramp":
            if not any(a < c < b for c in cuts):
                out.append((a, b, "ramp"))
            continue
        out += [(x, y, "speech") for x, y in split_shots(a, b, cuts, ws, R)]
    return out


def split_shots(a, b, cuts, ws, R):
    """[a,b] 를 원본 컷 기준 샷 조각으로. 앞/뒤 끝의 아주 짧은 조각에 말이 거의 없으면 버림(번쩍임 방지)."""
    bounds = [a] + [c for c in cuts if a < c < b] + [b]
    shots = list(zip(bounds, bounds[1:]))

    def talk(x, y):
        return sum(max(0.0, min(y, w["e"]) - max(x, w["s"])) for w in ws)
    while len(shots) > 1 and shots[0][1] - shots[0][0] < R["min_shot_sec"] and talk(*shots[0]) <= 0.2:
        shots.pop(0)
    while len(shots) > 1 and shots[-1][1] - shots[-1][0] < R["min_shot_sec"] and talk(*shots[-1]) <= 0.2:
        shots.pop()
    return shots


def render_shots(src, shots, cfg, fcache, f, kind, audio_gain_db, tts=None, tts_gain=0.0, fit_all=False, asrc=None):
    """샷 조각들을 얼굴 확대 크롭해 이어 붙여 세그먼트 파일 하나로.
    반환: [{src_a, src_b, out_a, out_b, face_out}]  (자막·동그라미 위치 계산용)"""
    L = cfg["layout"]
    W, VH = L["width"], L["video_h"]
    iw, ih = L["src_w"], L["src_h"]
    inputs, fc, mapping, t = [], [], [], 0.0
    cuts = cfg["_cuts"]
    alt = False
    seg_kind = kind
    shots = [s if len(s) == 3 else (s[0], s[1], "speech") for s in shots]
    # 영상과 소리는 항상 원본 그대로 함께 재생 (정지 화면·화면만 늘리기 없음 → 싱크 그대로).
    # 컷 감지 시각이 1~3프레임 어긋나 다음/이전 샷 프레임이 번쩍 끼는 것만 막기 위해,
    # 조각 끝이 컷 근처면 조각 자체(소리 포함)를 컷 안쪽으로 1프레임 맞춤.
    F1 = 1 / 30
    snapped = []
    for a_, b_, kk in shots:
        if kk != "ramp":
            for c in cuts:
                if a_ - 0.1 < c < a_ + 0.12:
                    a_ = c + F1
                if b_ - 0.12 < c < b_ + 0.1:
                    b_ = c - F1
        if b_ - a_ > 0.05:
            snapped.append((a_, b_, kk))
    shots = snapped or shots
    n = len(shots)
    if os.environ.get("SHOTS_DEBUG"):
        print(f"  [shots] {Path(f).name} {seg_kind}: " + " ".join(
            f"{a_:.2f}-{b_:.2f}({b_ - a_:.2f})" for a_, b_, kk in shots))
    prev_crop, face, prev_fit = None, None, False
    nin = 0  # ffmpeg 입력 번호
    for k, (a, b, kind) in enumerate(shots):
        cut_at = any(abs(c - a) < 0.06 for c in cuts)
        prev = shots[k - 1] if k else None
        continuous = prev is not None and prev_crop is not None and not cut_at and \
            (kind == "ramp" or prev[2] == "ramp" or abs(a - prev[1]) < 0.02)
        if continuous:  # 같은 샷이 이어짐 → 같은 크롭 유지
            x, y, w, h = prev_crop
            fit = prev_fit
        else:
            fit = False
            face = face_in_shot(src, a, b, fcache) if cfg["face"]["enabled"] and \
                (seg_kind != "narration" or cfg["face"].get("zoom_narration", False)) else None
            # 같은 원본 샷 안에서 침묵을 잘라낸 점프컷이면 확대 배율을 번갈아 → '펀치인' 편집처럼
            jump = prev is not None and not any(prev[1] - 0.05 <= c <= a + 0.05 for c in cuts) and a - prev[1] < 5
            alt = (not alt) if jump else False
            x, y, w, h = crop_box(face, iw, ih, cfg, cfg["face"]["jump_zoom"] if alt else 1.0)
            # 대화 장면인데 얼굴이 작게 잡힌 넓은 샷(두 사람이 양쪽 끝)이거나 얼굴을 못 찾으면:
            # 정사각형으로 자르면 사람이 잘리므로 원본 화면 전체를 가로폭에 맞춰 보여줌 (2026-11-11)
            # 훅(첫 장면) 등 제품 전체를 보여줘야 하는 내레이션은 대본의 "fit": true 로 원본 화면 전체
            fit = fit_all or seg_kind == "clip" and cfg["face"].get("fit_wide", True) and \
                (face is None or face[3] < cfg["face"].get("wide_face_h", 0.28))
        prev_crop, prev_fit = (x, y, w, h), fit
        inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src)]
        vk, nin = nin, nin + 1
        if asrc:  # 원본 대사 소리는 Demucs 로 분리한 목소리만 (원본 방송 배경음악 제거)
            inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(asrc)]
            ak, nin = nin, nin + 1
        else:
            ak = vk
        if kind == "ramp":  # 침묵 구간 영상을 ramp_frames 프레임으로 빨리감기
            d = cfg["render"]["ramp_frames"] / 30
            fc.append(f"[{vk}:v]crop={w}:{h}:{x}:{y},scale={W}:{VH},setsar=1,"
                      f"setpts=(PTS-STARTPTS)*{d / (b - a):.4f},fps=30,format=yuv420p,trim=duration={d:.3f}[v{k}]")
            fc.append(f"[{ak}:a]aresample=48000,aformat=channel_layouts=stereo,volume={audio_gain_db:.2f}dB,"
                      f"atrim=duration={d:.3f},asetpts=PTS-STARTPTS[a{k}]")
        else:
            d = b - a
            if fit:  # 원본 전체(16:9)를 가로폭에 맞추고, 위아래 빈 곳은 같은 화면을 흐리고 어둡게 깔아 채움
                fc.append(f"[{vk}:v]fps=30,trim=duration={d:.3f},setsar=1,split[f{k}a][f{k}b];"
                          f"[f{k}a]scale={W}:{VH}:force_original_aspect_ratio=increase,crop={W}:{VH},"
                          f"boxblur=20:2,colorchannelmixer=rr=0.5:gg=0.5:bb=0.5[f{k}g];"
                          f"[f{k}b]scale={W}:-2[f{k}f];[f{k}g][f{k}f]overlay=0:(H-h)/2,setsar=1,format=yuv420p[v{k}]")
            else:
                fc.append(f"[{vk}:v]crop={w}:{h}:{x}:{y},scale={W}:{VH},setsar=1,fps=30,"
                          f"trim=duration={d:.3f},format=yuv420p[v{k}]")
            fc.append(f"[{ak}:a]aresample=48000,aformat=channel_layouts=stereo,volume={audio_gain_db:.2f}dB,"
                      f"atrim=duration={d:.3f},afade=t=in:d=0.01,afade=t=out:st={max(0, d - 0.015):.3f}:d=0.015[a{k}]")
        fo = None
        if face:
            s = W / w
            fo = ((face[0] * iw - x) * s, (face[1] * ih - y) * s + L["title_h"],
                  face[2] * iw * s, face[3] * ih * s)
        mapping.append({"src_a": a, "src_b": b, "out_a": t, "out_b": t + d, "face_out": fo})
        t += d
    fc.append("".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
    dim = float(cfg["render"].get("narration_dim", 1.0))
    dim_f = f"colorchannelmixer=rr={dim}:gg={dim}:bb={dim}," if seg_kind == "narration" and dim < 0.999 else ""
    fc.append(f"[vc]{dim_f}{motion_filter(cfg, t, seg_kind)}[v]")
    if tts:  # 내레이션: 원본 소리 대신 TTS
        inputs += ["-i", str(tts)]
        fc.append(f"[{nin}:a]volume={tts_gain:.2f}dB,apad[tt];[ac]volume={cfg['render']['narration_original_volume']}[bg];"
                  f"[bg][tt]amix=inputs=2:normalize=0:duration=first[a]")
    else:
        fc.append("[ac]anull[a]")
    ffmpeg(*inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
           "-t", f"{t:.3f}", *ENC, f)
    return mapping


def smooth_clip_shots(shots, cuts, ws, src_dur, R):
    """대사 클립에서 0.x초짜리 샷·끊기는 점프컷이 생기지 않게 조각 경계를 고친다.
    영상과 소리를 항상 같이 옮기므로 입모양·자막 싱크는 그대로다 (정지 화면 없음)."""
    minv = R.get("min_visual_shot", 0.8)
    F1 = 1 / 30
    # t 에 딱 붙은 컷도 경계로 봄 (그 컷을 넘어 다음 샷까지 늘리지 않게)
    nxt = lambda t: next((c for c in cuts if c > t - 0.01), src_dur)
    prv = lambda t: max([0.0] + [c for c in cuts if c < t + 0.01])
    talk = lambda x, y: sum(max(0.0, min(y, w["e"]) - max(x, w["s"])) for w in ws)
    cut_between = lambda x, y: any(x - 0.03 <= c <= y + 0.03 for c in cuts)
    shots = [tuple(s) if len(s) == 3 else (s[0], s[1], "speech") for s in shots]
    # 1) 같은 원본 샷 안에서 침묵을 잘라낸 점프컷 → 쉼이 짧으면 원본 그대로 이어서 재생 (화면 튐 없음)
    merged = []
    for a, b, k in shots:
        if merged and k != "ramp" and merged[-1][2] != "ramp":
            pa, pb, pk = merged[-1]
            if a > pb + 0.02 and not cut_between(pb, a) and a - pb <= R.get("jump_merge_gap", 0.8):
                merged[-1] = (pa, b, pk)
                continue
            # 사이에 원본 컷이 있어도 쉼이 짧으면 원본 편집 그대로 이어서 (대화 주고받기가 원래 속도로)
            if a > pb + 0.02 and cut_between(pb, a) and a - pb <= R.get("cross_cut_gap", 0.8):
                c = next(c for c in cuts if pb - 0.03 <= c <= a + 0.03)
                merged[-1] = (pa, max(pb, c), pk)
                merged.append((min(a, c), b, k))
                continue
        merged.append((a, b, k))
    shots = merged
    # 2) 0.x초 조각 처리 (어디에 있든):
    #    같은 원본 샷의 앞/뒤 조각과 사이 쉼까지 이어 붙임 → 원본 그대로 끊김 없이 재생
    #    끝 조각인데 말이 거의 없으면 뺌, 그래도 짧으면 같은 샷 안에서 앞뒤를 더 보여줌
    changed = True
    while changed and len(shots) > 1:
        changed = False
        for i, (a, b, k) in enumerate(shots):
            if b - a >= minv or k == "ramp":
                continue
            if i > 0 and not cut_between(shots[i - 1][1], a) and a - shots[i - 1][1] <= 1.5:
                shots[i - 1:i + 1] = [(shots[i - 1][0], b, shots[i - 1][2])]
            elif i + 1 < len(shots) and not cut_between(b, shots[i + 1][0]) and shots[i + 1][0] - b <= 1.5:
                shots[i:i + 2] = [(a, shots[i + 1][1], k)]
            elif i in (0, len(shots) - 1) and talk(a, b) <= 0.15:
                shots.pop(i)
            else:
                continue
            changed = True
            break
    for i, (a, b, k) in enumerate(shots):  # 남은 짧은 조각: 같은 샷 안에서 늘림 (원본 소리 포함)
        if b - a >= minv or k == "ramp":
            continue
        lo = shots[i - 1][1] if i > 0 else prv(a) + F1
        hi = shots[i + 1][0] if i + 1 < len(shots) else nxt(b) - F1
        lo, hi = max(lo, prv(a) + F1), min(hi, nxt(b) - F1)
        nb = min(hi, b + (minv - (b - a)))
        na = max(lo, a - (minv - (nb - a)))
        shots[i] = (min(a, na), max(b, nb), k)
    # 3) 클립이 너무 짧으면(예: "140 grand") 같은 샷 안에서 뒤를 더, 모자라면 앞을 더 보여줌
    need = R.get("min_clip_sec", 1.3) - sum(b - a for a, b, _ in shots)
    if need > 0:
        a, b, k = shots[-1]
        nb = min(nxt(b) - F1, b + need)
        shots[-1] = (a, max(b, nb), k)
        need -= max(0.0, nb - b)
    if need > 0:
        a, b, k = shots[0]
        na = max(prv(a) + F1, a - need)
        shots[0] = (min(a, na), b, k)
    return shots


def ensure_vocals(job: Path, cfg):
    """원본 방송 배경음악 제거용: Demucs 로 대사(목소리)만 분리한 source_vocals.wav 를 만들어 둠 (없을 때만, 약 1~2분)."""
    out = job / "source_vocals.wav"
    if out.exists() or not cfg["render"].get("clip_vocals_only", True):
        return out if out.exists() else None
    py = ROOT / cfg.get("align", {}).get("python", ".venv-whisperx/Scripts/python.exe")
    if not py.exists():
        print("  ⚠ Demucs 환경 없음 → 원본 소리 그대로 사용 (배경음악이 섞일 수 있음)")
        return None
    tmp = job / "_source_44k.wav"
    print("  원본 배경음악 제거: Demucs 로 목소리만 분리 중...")
    ffmpeg("-i", job / "source.mp4", "-vn", "-ac", "2", "-ar", "44100", tmp)
    r = subprocess.run([str(py), "-I", str(ROOT / "tools" / "separate_vocals.py"), str(tmp), str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tmp.unlink(missing_ok=True)
    if r.returncode != 0 or not out.exists():
        print("  ⚠ 목소리 분리 실패 → 원본 소리 그대로 사용\n" + r.stderr[-800:])
        return None
    return out


def build_segments(job: Path, plan, cfg):
    src = job / "source.mp4"
    src_dur = duration(src)
    tr = {t["id"]: t for t in read_json(job / "transcript.json")}
    words = ensure_words(job, cfg)
    iw, ih = (int(v) for v in run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                    "stream=width,height", "-of", "csv=p=0", src]).strip().split(",")[:2])
    cuts = detect_cuts(src)
    ensure_vocals(job, cfg)
    cfg = {**cfg, "layout": {**cfg["layout"], "src_w": iw, "src_h": ih}, "_cuts": cuts}
    seg_dir = job / "segments"
    seg_dir.mkdir(exist_ok=True)
    R = cfg["render"]
    fcache = load_faces(job)
    out = []
    for i, s in enumerate(plan["segments"]):
        f = seg_dir / f"{i:02d}.mp4"
        if s["type"] == "clip":
            ids = list(range(s["from_id"], s["to_id"] + 1))
            wds = words
            if s.get("skip_words"):  # 첫 줄 앞 단어 몇 개(예: 같은 줄의 질문)는 빼고 시작
                wds = {**words, str(ids[0]): words.get(str(ids[0]), [])[s["skip_words"]:]}
            pieces, ws = speech_pieces(wds, ids, tr, R)
            shots = smooth_clip_shots(pieces_to_shots(pieces, cuts, ws, R), cuts, ws, src_dur, R)
            # 원본 대사 음량을 TTS 와 같은 목표 음량으로 (말하는 구간만 측정)
            vocals = job / "source_vocals.wav"
            asrc = vocals if R.get("clip_vocals_only", True) and vocals.exists() else None
            g = voice_gain(lufs_of(asrc or src, shots[0][0], shots[-1][1] - shots[0][0]), R)
            mapping = render_shots(src, shots, cfg, fcache, f, "clip", g, asrc=asrc)
            info = {"kind": "clip", "seg": s, "map": mapping, "gain_db": round(g, 1),
                    "words": {str(k): wds.get(str(k), []) for k in ids}}
        else:
            wav = synth(plain(s["text"]), job / "tts", cfg)
            L = duration(wav) + R["narration_gap"]
            # 내레이션 그림은 소리와 무관 → 0.x초 조각이 생기지 않는 자리를 원하는 장면 근처에서 찾음
            st0 = tr[s["broll_id"]]["start"]
            minv = R.get("min_visual_shot", 1.0)
            shots = None
            for off in (0, -0.5, 0.5, -1.0, 1.0, -1.5, 1.5, -2.0, 2.0, -3.0, 3.0, -4.0, 4.0):
                cand = broll_shots(cuts, max(0.0, st0 + off), L, src_dur, R["broll_shot_min"], R["broll_shot_max"])
                if all(b_ - a_ >= minv for a_, b_ in cand) or len(cand) == 1 and cand[0][1] - cand[0][0] >= L - 0.05:
                    shots = cand
                    break
            shots = shots or broll_shots(cuts, st0, L, src_dur, R["broll_shot_min"], R["broll_shot_max"])
            g = voice_gain(lufs_of(wav), R)
            mapping = render_shots(src, shots, cfg, fcache, f, "narration", 0.0, tts=wav, tts_gain=g,
                                   fit_all=bool(s.get("fit")))
            info = {"kind": "narration", "tts": str(wav), "tts_dur": duration(wav), "seg": s,
                    "map": mapping, "gain_db": round(g, 1)}
        info["file"] = f
        info["dur"] = duration(f)
        out.append(info)
    save_faces(job, fcache)
    return out, tr


def src_to_out(mapping, t):
    """원본 시각 → 세그먼트 안 출력 시각 (잘려나간 침묵 안이면 다음 조각 시작)."""
    for m in mapping:
        if t < m["src_a"]:
            return m["out_a"]
        if t <= m["src_b"]:
            k = (m["out_b"] - m["out_a"]) / max(1e-6, m["src_b"] - m["src_a"])  # 빨리감기 구간은 압축
            return m["out_a"] + (t - m["src_a"]) * k
    return mapping[-1]["out_b"]


def build_ass(job: Path, plan, segs, tr, cfg, total, aligned, timing_log):
    L, S = cfg["layout"], cfg["subtitle"]
    W, H, top, vh = L["width"], L["height"], L["title_h"], L["video_h"]
    font = S["font"]
    sub_y = top + vh - S["bottom_margin_in_video"]  # 자막 기준선(하단 정렬)
    B = -1 if S.get("font_bold", True) else 0  # 아네모네처럼 원래 굵은 폰트는 가짜 볼드 끔
    styles = [
        # Name, Fontname, Size, Primary, Outline colour, Bold, Outline, Shadow, Alignment
        ("Title", font, S["title_size"], "&H00FFFFFF", "&H00000000", B, S["title_outline"], 0, 5),
        ("Narr", font, S["narr_size"], ass_color(S["narr_color"]), "&H00000000", B, S["outline"], 1, 2),
        ("Dial", font, S["dial_size"], ass_color(S["dial_color"]), "&H00000000", B, S["outline"], 1, 2),
        ("Tag", font, S["tag_size"], ass_color(S["tag_color"]), "&H00000000", B, S["outline"], 0, 7),
        ("Credit", font, 30, ass_color("gray"), "&H00000000", 0, 0, 0, 5),
    ]
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for n, f, size, prim, outc, bold, ol, sh, al in styles:
        head.append(f"Style: {n},{f},{size},{prim},&H000000FF,{outc},&H96000000,{bold},0,0,0,"
                    f"100,100,0,0,1,{ol},{sh},{al},40,40,{H - sub_y},1")
    head += ["", "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ev = []
    pop = r"{\fscx80\fscy80\t(0,90,\fscx100\fscy100)}"

    def add(t0, t1, style, text, layer=0):
        if t1 - t0 > 0.05 and text.strip():
            ev.append(f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,,{text}")

    # 상단 제목 (전체 구간 고정)
    lines = plan["title_lines"]
    # 줄마다 가로 92%를 넘지 않도록 글자 크기 자동 조절 (한글 1자 ≈ 0.66em)
    sizes = [min(S["title_size"], int(W * 0.92 / (max(len(ln["text"]), 1) * S["title_char_em"])))
             for ln in lines]
    y = top - S["title_bottom_margin"]
    ys = []
    for fs in reversed(sizes):
        ys.insert(0, y - fs * 0.55)
        y -= fs * 1.1
    for ln, fs, yy in zip(lines, sizes, ys):
        col = ass_color(ln.get("color", "white"))
        # 같은 색 외곽선으로 획을 두껍게(가짜 볼드) + 아래쪽 은은한 그림자 → 두꺼운 썸네일 폰트 느낌
        add(0, total, "Title", rf"{{\pos({W // 2},{int(yy)})\fs{fs}\c{col}\3c{col}"
            rf"\bord{S.get('title_weight', 0)}\shad3\4c&H000000&\4a&H40&}}" + ln["text"], 1)
    wm = cfg.get("watermark", {})
    if wm.get("text"):  # 영상 패널 바로 아래 흐린 흰색 워터마크 (edl.py 와 같은 자리, 글자 위쪽 기준 an8)
        add(0, total, "Credit", rf"{{\an8\pos({W // 2},{top + vh + wm.get('gap', 22)})\fs{wm.get('size', 40)}"
            rf"\1c&HFFFFFF&\alpha{wm.get('alpha', '&HA8&')}\bord0\shad0\blur0.5}}" + wm["text"], 20)
    if plan.get("credit") and cfg["render"].get("show_credit", True):  # 워터마크가 있으면 그 아래로
        cy = top + vh + (wm.get("gap", 22) + wm.get("size", 40) + 30 if wm.get("text") else 45)
        add(0, total, "Credit", rf"{{\pos({W // 2},{cy})}}" + plan["credit"])

    narr_base, narr_kw = ass_color(S["narr_color"]), ass_color(S["narr_keyword_color"])
    dial_base, dial_kw = ass_color(S["dial_color"]), ass_color(S["dial_keyword_color"])
    first_narr = next((sg for sg in segs if sg["kind"] == "narration"), None)
    # 네온 중앙 자막 (해외쇼쇼 스타일): 영상 패널 정중앙, 강조색은 이벤트마다 순환
    center = S.get("style") == "neon_center"
    if S.get("sub_anchor") == "bottom":  # 영상 패널 하단: 자막 아래 끝 고정 (줄이 늘면 위로 자람)
        sub_pos, sub_an = (W // 2, int(top + vh - S.get("bottom_margin", 120))), 2
    else:
        sub_pos, sub_an = (W // 2, int(top + vh * S.get("center_ratio", 0.5))), 5
    acc_i = [0]
    # 참고 채널 문법 자막 (애니메이션 없음, 내레이션 상자 / 대사 화자색)
    boxcut = S.get("style") == "box_cut"
    subq = []  # [a, b, kind, text, color]
    spk_palette = S.get("speaker_colors", ["5CE1FF", "8CFF4F", "FFE14D", "FF8AD8"])
    spk_color, last_spk = {}, [None]

    def color_of(spk):
        if spk is None:
            spk = last_spk[0]
        last_spk[0] = spk
        fixed = S.get("speaker_fixed", {})
        if spk in fixed:  # 사장(릭) 하늘색, 손님 연두, 전문가 노랑 — 영상마다 같은 색
            return fixed[spk]
        if spk not in spk_color:
            spk_color[spk] = spk_palette[len(spk_color) % len(spk_palette)]
        return spk_color[spk]

    def next_accent():
        acc_i[0] += 1
        return PALETTE[(acc_i[0] - 1) % len(PALETTE)]

    # 빨간 동그라미: 강조어([ ])가 있는 대사 자막 / 대본에서 mark:true 인 클립, 일정 간격 이상, 최대 N개
    S_mark = cfg["marks"]
    marks = []
    pending_clip_sfx = [None]

    def mark_ok(seg, txt, at):
        if not S_mark["enabled"] or len(marks) >= S_mark["max_per_video"]:
            return False
        if marks and at - marks[-1] < S_mark["min_gap_sec"]:
            return False
        return bool(seg.get("mark")) or ("[" in txt and S_mark["auto_on_keyword"])

    def face_at(mp, rel):
        for m in mp:
            if m["out_a"] <= rel < m["out_b"]:
                return m["face_out"]
        return None

    sfx_events = []
    t = 0.0
    for sg in segs:
        s = sg["seg"]
        # 효과음 위치 (참고 영상 분석): 내레이션은 컷 지점, 원본 대사는 말 시작에 겹치지 않게
        # → 강조어 자막이 뜰 때로 옮기고, 없으면 생략
        REVEAL = ("ding", "surprise", "impact", "success", "question", "fail", "cash")
        if s.get("sfx"):
            if s["sfx"] not in REVEAL:  # 휙·팝 = 장면 전환 순간 (내레이션·대사 모두 구간 시작)
                sfx_events.append((max(0.0, t + SFX_OFFSET.get(s["sfx"], 0.0)), s["sfx"]))
            else:  # 띵·쿵·놀람 = 금액/강조어 자막이 뜨는 순간 (없으면 첫 자막)
                pending_clip_sfx[0] = s["sfx"]
        if sg["kind"] == "narration":
            chunks = split_chunks(s["text"], S.get("narr_max_em", 11.5))
            words = aligned.get(sg["tts"])
            if words:
                times, method = chunk_times(chunks, words, sg["dur"]), "whisperx"
            else:
                times, method = proportional_times(chunks, sg["tts_dur"]), "proportional"
            prop = proportional_times(chunks, sg["tts_dur"])  # 비교 기록용(검증)
            neon = (S["neon_hook"] and sg is first_narr) or S["neon_all"] or s.get("neon")
            kw_on = False
            accent = next_accent()
            merged = merge_short([(a_, b_, c_) for (a_, b_), c_ in zip(times, carry_color(chunks))],
                                 S.get("min_line_sec", 0.7))
            if pending_clip_sfx[0]:  # 내레이션의 '띵' 등은 강조어([ ])가 든 자막 덩어리가 뜰 때
                hit = next((a_ for a_, _, c_ in merged if "[" in c_), merged[0][0] if merged else 0.0)
                sfx_events.append((t + hit, pending_clip_sfx[0]))
                pending_clip_sfx[0] = None
            for (a, b, cc), (pa, _) in zip(merged, prop):
                c = plain(cc.replace(NL, " "))
                a, b = t + a, t + b
                if boxcut:
                    subq.append([a, b, "narr", cc, None])
                    timing_log.append({"text": plain(c), "start": round(a, 3), "end": round(b, 3),
                                       "method": method, "prop_start": round(t + pa, 3),
                                       "seg_start": round(t, 3), "seg_end": round(t + sg["dur"], 3)})
                    continue
                if center:
                    spans = money_green(parse_spans(cc, accent))
                    for ly, s0, s1, body in neon_events(spans, a, b, cfg, pos=sub_pos, an=sub_an):
                        add(s0, s1, "Narr", body, ly)
                    timing_log.append({"text": plain(c), "start": round(a, 3), "end": round(b, 3),
                                       "method": method, "prop_start": round(t + pa, 3),
                                       "seg_start": round(t, 3), "seg_end": round(t + sg["dur"], 3)})
                    continue
                txt, kw_on = keywordize(c, narr_base, narr_kw, kw_on)
                if neon:
                    glow = ass_color(S["neon_color"])
                    # ① 퍼지며 사라지는 빛 번짐  ② 은은하게 남는 후광  ③ 본문(얇은 네온 테두리)
                    add(a, min(b, a + 0.55), "Narr",
                        rf"{{\bord0\shad0\blur24\1c{glow}\alpha&H20&"
                        rf"\t(0,500,\fscx155\fscy190\alpha&HFF&)}}" + plain(c), 1)
                    add(a, b, "Narr", rf"{{\shad0\bord16\blur16\1c{glow}\3c{glow}\alpha&H30&"
                                      rf"\t(0,300,\bord22\blur24\alpha&H78&)}}" + plain(c), 2)
                    add(a, b, "Narr", "{" + ENTER + rf"\bord3\3c{glow}\shad0}}" + txt, 3)
                else:
                    add(a, b, "Narr", "{" + ENTER + "}" + txt, 3)
                timing_log.append({"text": plain(c), "start": round(a, 3), "end": round(b, 3),
                                   "method": method, "prop_start": round(t + pa, 3),
                                   "seg_start": round(t, 3), "seg_end": round(t + sg["dur"], 3)})
        else:
            ids = list(range(s["from_id"], s["to_id"] + 1))
            mp = sg["map"]

            def w0(i):
                ws = sg["words"].get(str(i)) or [{"s": tr[i]["start"], "e": tr[i]["end"]}]
                return ws[0]["s"], ws[-1]["e"]
            # 자막 이벤트: 줄마다 '//' 로 화자를 나누고(질문·대답 따로), 다시 한 줄 폭으로 나눔.
            # 시작 시각은 그 줄 단어들 사이에서 글자 폭 비율로 배분
            evs_, spk_of = [], {}
            for k, i in enumerate(ids):
                txt = s["subs"][k].strip()
                if not txt:
                    continue
                ws = sg["words"].get(str(i)) or [{"s": tr[i]["start"], "e": tr[i]["end"]}]
                # (덩어리, 화자 묶음 번호): 전사 줄이 바뀌거나 '//' 를 넘으면 다른 화자로 본다.
                # 화자 표시 '@릭 얼마 원하세요?' → 화자별 자막 색
                parts = []
                for q, p_ in enumerate(x.strip() for x in txt.split("//") if x.strip()):
                    m_ = re.match(r"@(\S+)\s*", p_)
                    spk_of[(k, q)] = m_.group(1) if m_ else None
                    p_ = p_[m_.end():] if m_ else p_
                    parts += [(c, (k, q)) for c in carry_color(split_chunks(p_, S.get("dial_max_em", 11.5)))]
                tot = sum(vis_w(c) for c, _ in parts) or 1.0
                acc = 0.0
                for c, grp in parts:
                    j = min(len(ws) - 1, int(round(acc / tot * len(ws))))
                    evs_.append((t + src_to_out(mp, ws[j]["s"]), c, grp))
                    acc += vis_w(c)
            if evs_:
                evs_[0] = (t,) + evs_[0][1:]  # 첫 자막은 세그먼트 시작과 함께
                ends_ = [x[0] for x in evs_[1:]] + [t + sg["dur"]]
                # 너무 짧은 자막은 같은 화자 안에서만 두 줄로 합침 (질문·대답은 절대 합치지 않음)
                evs_ = [(a_, txt_, g_) for a_, _, txt_, g_ in
                        merge_short([(a_, b_, x_, g_) for (a_, x_, g_), b_ in zip(evs_, ends_)],
                                    S.get("min_line_sec", 0.7))]
            marked = False
            for n, (st, txt, grp) in enumerate(evs_):
                a = max(t, st - 0.03)
                b = max(a + 0.4, evs_[n + 1][0] - 0.03) if n + 1 < len(evs_) else t + sg["dur"]
                if pending_clip_sfx[0] and pending_clip_sfx[0] != "fail" and "[" in txt:
                    sfx_events.append((a, pending_clip_sfx[0]))
                    pending_clip_sfx[0] = None
                # 동그라미는 클립의 마지막 자막(결정적 대답·판정)을 말하는 사람에게만
                if not marked and n == len(evs_) - 1 and mark_ok(s, txt, a):
                    fo = face_at(mp, a - t)
                    if fo:
                        for ev_ in circle_events(fo, a, min(b, a + S_mark["hold_sec"]), cfg):
                            add(*ev_)
                        sfx_events.append((a, S_mark["sfx"]))
                        marks.append(a)
                        marked = True
                if boxcut:
                    subq.append([a, b, "dial", txt, color_of(spk_of.get(grp))])
                    continue
                if center:
                    for ly, s0, s1, body in neon_events(money_green(parse_spans(txt, next_accent())), a, b, cfg,
                                                        pos=sub_pos, an=sub_an):
                        add(s0, s1, "Dial", body, ly)
                    continue
                body, _ = keywordize(txt, dial_base, dial_kw, False)
                add(a, b, "Dial", "{" + ENTER + "}" + body, 3)
            if s.get("tag") and S.get("show_tags", True):
                add(t, t + sg["dur"], "Tag", rf"{{\pos(60,{top + 40})}}" + pop + s["tag"], 2)
        if pending_clip_sfx[0] and sg["kind"] == "clip" and evs_:
            # 강조어 자막이 없는 클립: '실패'는 마지막 자막(거절하는 말), 나머지는 첫 자막에
            k_ = -1 if pending_clip_sfx[0] == "fail" else 0
            sfx_events.append((max(t, evs_[k_][0]), pending_clip_sfx[0]))
        pending_clip_sfx[0] = None
        t += sg["dur"]
    if boxcut:  # 자막 사이 빈틈(깜빡임) 없이 다음 자막으로 바로 교체
        subq.sort(key=lambda x: x[0])
        for k in range(len(subq) - 1):
            if subq[k + 1][0] - subq[k][1] < 0.2 or subq[k][1] > subq[k + 1][0]:
                subq[k][1] = subq[k + 1][0]
        for a, b, kind_, txt, col in subq:
            for ly, body in boxcut_events(kind_, txt, sub_pos, S, col):
                add(a, b, "Narr" if kind_ == "narr" else "Dial", body, ly)
    (job / "subs.ass").write_text("\n".join(head + ev) + "\n", encoding="utf-8-sig")
    # 밀도 제한: 효과음끼리 min_gap 이상 (참고 영상 ≈ 10초에 2~3개)
    # 너무 붙은 효과음은 하나만: 먼저 나온 것이 아니라 '중요한 것'을 남김 (쿵 > 실패 > 정답 > 놀람 > 띵 > 띠용 > 휙)
    gap = cfg["render"]["sfx_min_gap"]
    prio = {"impact": 0, "fail": 1, "success": 2, "surprise": 3, "ding": 4, "cash": 4, "question": 5, "whoosh": 6, "pop": 7}
    kept = []
    for e in sorted(sfx_events, key=lambda e: (prio.get(LEGACY_SFX.get(e[1], e[1]).split("/")[0], 8), e[0])):
        if all(abs(e[0] - k[0]) >= gap for k in kept):
            kept.append(e)
    return sorted(kept)


def pick_bgm(cfg):
    if cfg["render"].get("bgm_file"):
        return Path(cfg["render"]["bgm_file"])
    files = [p for p in (ASSETS / "bgm").glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")]
    return random.choice(files) if files else None


def render(job: Path, cfg):
    plan = read_json(job / "plan.json")
    ensure_sfx()
    segs, tr = build_segments(job, plan, cfg)

    lst = job / "segments" / "list.txt"
    lst.write_text("".join(f"file '{sg['file'].name}'\n" for sg in segs), encoding="utf-8")
    body = job / "body.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", body)
    total = duration(body)

    # 최종 내레이션 오디오(무음 제거된 TTS) 기준으로 어절 시간 정렬
    narr = [(sg["tts"], plain(sg["seg"]["text"])) for sg in segs if sg["kind"] == "narration"]
    aligned = align_all(narr, cfg)
    timing_log = []
    sfx_events = build_ass(job, plan, segs, tr, cfg, total, aligned, timing_log)
    write_json(job / "narration_timing.json", timing_log)
    n_ok = sum(1 for w in aligned.values() if w)
    print(f"  내레이션 자막 정렬: WhisperX {n_ok}/{len(narr)}문장")

    L, R = cfg["layout"], cfg["render"]
    inputs = ["-i", body]
    fc = [f"[0:v]pad={L['width']}:{L['height']}:0:{L['title_h']}:color=black,"
          f"ass=subs.ass:fontsdir='{filter_path(ASSETS / 'fonts')}'[v]"]
    mix = ["[voice]"]
    idx = 1
    bgm = pick_bgm(cfg)
    if bgm:
        inputs += ["-stream_loop", "-1", "-i", bgm]
        fc.append("[0:a]asplit[voice][sc]")
        fc.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={R['bgm_volume']},"
                  f"atrim=0:{total:.3f}[bgmraw]")
        fc.append("[bgmraw][sc]sidechaincompress=threshold=0.02:ratio=4:attack=15:release=350[bgm]")
        mix.append("[bgm]")
        idx += 1
    else:
        fc.append("[0:a]anull[voice]")
    used_sfx = []
    level_ev = []
    if R.get("sfx_engine", "level") == "level":
        # 깃허브 edl.py 와 같은 효과음 엔진(sfx_level): 참고 쇼츠 15편 팔레트에서 고르고,
        # 그 순간 목소리 크기에 맞춰 음량을 정하고, 소리의 가장 센 지점을 사건 시각에 맞춤 → 아래 ffmpeg 에선 목소리만
        level_ev = [(t, LEGACY_SFX.get(name, name)) for t, name in sfx_events]
        sfx_events = []
    for n, (t, name) in enumerate(sfx_events):
        cat = LEGACY_SFX.get(name, name)
        item = pick_sfx(cat, f"{job.name}-{n}")  # 사용자 효과음 라이브러리 우선
        if item:
            p, gain = Path(item["file"]), item["gain_db"]
            used_sfx.append({"t": round(t, 2), "category": cat, "name": item["name"]})
        else:
            p, gain = sfx_path(name), 0.0
        if not p:
            continue
        inputs += ["-i", p]
        ms = int(t * 1000)
        fc.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                  f"volume={gain:.1f}dB,volume={R['sfx_volume']},adelay={ms}:all=1[s{idx}]")
        mix.append(f"[s{idx}]")
        idx += 1
    write_json(job / "sfx_used.json", used_sfx)
    # 목소리는 구간별로 이미 같은 음량(voice_lufs)이라, 전체는 '선형 게인 + 리미터'로만 키움
    # (loudnorm 의 동적 보정이 TTS/원본 음량 균형을 다시 흐트러뜨리지 않도록)
    body_lufs = lufs_of(body) or R["voice_lufs"]
    master = R["loudness_lufs"] - body_lufs
    fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,"
              f"volume={master:.2f}dB,alimiter=limit=0.89:attack=3:release=60:level=0,"
              f"aresample=48000[a]")

    out = job / "final.mp4"
    ffmpeg(*inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}",
           out.name, cwd=job)
    if level_ev:
        from . import sfx_level
        tmp_out = job / "_lvl_final.mp4"
        _, _, mlog = sfx_level.render(job, body, out, tmp_out, level_ev, total, job / "sfx_level_tmp",
                                      cfg.get("sfx_mix", {}).get("master_lufs", -11.0))
        tmp_out.replace(out)
        (job / "sfx_level.log").write_text("\n".join(mlog), encoding="utf-8")
        print("\n".join("  " + x for x in mlog))

    up = plan.get("upload", {})
    (job / "upload.txt").write_text(
        f"제목: {up.get('title', '')}\n\n설명:\n{up.get('description', '')}\n\n태그: {', '.join(up.get('tags', []))}\n"
        f"\n고정댓글:\n{plan.get('pinned_comment', '')}\n",
        encoding="utf-8")
    write_json(job / "timeline.json", [{"kind": s["kind"], "dur": round(s["dur"], 2)} for s in segs])
    return out, total
