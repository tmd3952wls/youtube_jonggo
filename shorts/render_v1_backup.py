"""4단계: plan.json + source.mp4 + TTS → 최종 쇼츠 mp4.

레이아웃(1080x1920)
  0 ~ title_h            : 검정 + 2줄 상단 제목
  title_h ~ +video_h     : 원본 영상(가운데 크롭) + 자막
  나머지                  : 검정 + 출처 표기
"""
import random
import re
import subprocess
from pathlib import Path

from .common import ASSETS, duration, ffmpeg, filter_path, read_json, run, write_json
from .align import align_all, chunk_times, proportional_times
from .sfx import ensure_sfx, sfx_path
from .align import ensure_words
from .faces import crop_box, face_in_shot
from .faces import load_cache as load_faces
from .faces import save_cache as save_faces
from .scenes import broll_shots, detect_cuts
from .sfxlib import pick as pick_sfx
from .subfx import PALETTE, circle_events, neon_events, open_state, parse_spans
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


def split_chunks(text, max_chars):
    """내레이션을 화면용 짧은 덩어리로 (어절 단위)."""
    words, chunks, cur = text.split(), [], ""
    for w in words:
        if cur and len(plain(cur)) + 1 + len(plain(w)) > max_chars:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [text]


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


def render_shots(src, shots, cfg, fcache, f, kind, audio_gain_db, tts=None, tts_gain=0.0):
    """샷 조각들을 얼굴 확대 크롭해 이어 붙여 세그먼트 파일 하나로.
    반환: [{src_a, src_b, out_a, out_b, face_out}]  (자막·동그라미 위치 계산용)"""
    L = cfg["layout"]
    W, VH = L["width"], L["video_h"]
    iw, ih = L["src_w"], L["src_h"]
    inputs, fc, mapping, t = [], [], [], 0.0
    cuts = cfg["_cuts"]
    alt = False
    shots = [s if len(s) == 3 else (s[0], s[1], "speech") for s in shots]
    prev_crop, face = None, None
    for k, (a, b, kind) in enumerate(shots):
        cut_at = any(abs(c - a) < 0.06 for c in cuts)
        prev = shots[k - 1] if k else None
        continuous = prev is not None and prev_crop is not None and not cut_at and \
            (kind == "ramp" or prev[2] == "ramp" or abs(a - prev[1]) < 0.02)
        if continuous:  # 같은 샷이 이어짐(빨리감기 구간 포함) → 같은 크롭 유지
            x, y, w, h = prev_crop
        else:
            face = face_in_shot(src, a, b, fcache) if cfg["face"]["enabled"] else None
            # 같은 원본 샷 안에서 긴 침묵을 잘라낸 점프컷이면 확대 배율을 번갈아 → '펀치인' 편집처럼
            jump = prev is not None and not any(prev[1] - 0.05 <= c <= a + 0.05 for c in cuts) and a - prev[1] < 5
            alt = (not alt) if jump else False
            x, y, w, h = crop_box(face, iw, ih, cfg, cfg["face"]["jump_zoom"] if alt else 1.0)
        prev_crop = (x, y, w, h)
        inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src)]
        if kind == "ramp":  # 침묵 구간 영상을 ramp_frames 프레임으로 빨리감기
            d = cfg["render"]["ramp_frames"] / 30
            fc.append(f"[{k}:v]crop={w}:{h}:{x}:{y},scale={W}:{VH},setsar=1,"
                      f"setpts=(PTS-STARTPTS)*{d / (b - a):.4f},fps=30,format=yuv420p,trim=duration={d:.3f}[v{k}]")
            fc.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={audio_gain_db:.2f}dB,"
                      f"atrim=duration={d:.3f},asetpts=PTS-STARTPTS[a{k}]")
        else:
            d = b - a
            fc.append(f"[{k}:v]crop={w}:{h}:{x}:{y},scale={W}:{VH},setsar=1,fps=30,format=yuv420p[v{k}]")
            fc.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={audio_gain_db:.2f}dB,"
                      f"afade=t=in:d=0.01,afade=t=out:st={max(0, d - 0.015):.3f}:d=0.015[a{k}]")
        fo = None
        if face:
            s = W / w
            fo = ((face[0] * iw - x) * s, (face[1] * ih - y) * s + L["title_h"],
                  face[2] * iw * s, face[3] * ih * s)
        mapping.append({"src_a": a, "src_b": b, "out_a": t, "out_b": t + d, "face_out": fo})
        t += d
    n = len(shots)
    fc.append("".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
    fc.append(f"[vc]{motion_filter(cfg, t, kind)}[v]")
    if tts:  # 내레이션: 원본 소리 대신 TTS
        inputs += ["-i", str(tts)]
        fc.append(f"[{n}:a]volume={tts_gain:.2f}dB,apad[tt];[ac]volume={cfg['render']['narration_original_volume']}[bg];"
                  f"[bg][tt]amix=inputs=2:normalize=0:duration=first[a]")
    else:
        fc.append("[ac]anull[a]")
    ffmpeg(*inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
           "-t", f"{t:.3f}", *ENC, f)
    return mapping


def build_segments(job: Path, plan, cfg):
    src = job / "source.mp4"
    src_dur = duration(src)
    tr = {t["id"]: t for t in read_json(job / "transcript.json")}
    words = ensure_words(job, cfg)
    iw, ih = (int(v) for v in run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                    "stream=width,height", "-of", "csv=p=0", src]).strip().split(",")[:2])
    cuts = detect_cuts(src)
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
            pieces, ws = speech_pieces(words, ids, tr, R)
            shots = pieces_to_shots(pieces, cuts, ws, R)
            # 원본 대사 음량을 TTS 와 같은 목표 음량으로 (말하는 구간만 측정)
            g = voice_gain(lufs_of(src, shots[0][0], shots[-1][1] - shots[0][0]), R)
            mapping = render_shots(src, shots, cfg, fcache, f, "clip", g)
            info = {"kind": "clip", "seg": s, "map": mapping, "gain_db": round(g, 1),
                    "words": {str(k): words.get(str(k), []) for k in ids}}
        else:
            wav = synth(plain(s["text"]), job / "tts", cfg)
            L = duration(wav) + R["narration_gap"]
            shots = broll_shots(cuts, tr[s["broll_id"]]["start"], L, src_dur,
                                R["broll_shot_min"], R["broll_shot_max"])
            g = voice_gain(lufs_of(wav), R)
            mapping = render_shots(src, shots, cfg, fcache, f, "narration", 0.0, tts=wav, tts_gain=g)
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
    if plan.get("credit"):  # 워터마크가 있으면 그 아래로
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
        if s.get("sfx"):
            if sg["kind"] == "narration":
                sfx_events.append((max(0.0, t + SFX_OFFSET.get(s["sfx"], 0.0)), s["sfx"]))
            else:
                pending_clip_sfx[0] = s["sfx"]
        if sg["kind"] == "narration":
            chunks = split_chunks(s["text"], S["narr_max_chars"])
            words = aligned.get(sg["tts"])
            if words:
                times, method = chunk_times(chunks, words, sg["dur"]), "whisperx"
            else:
                times, method = proportional_times(chunks, sg["tts_dur"]), "proportional"
            prop = proportional_times(chunks, sg["tts_dur"])  # 비교 기록용(검증)
            neon = (S["neon_hook"] and sg is first_narr) or S["neon_all"] or s.get("neon")
            kw_on = False
            accent = next_accent()
            for c, (a, b), (pa, _) in zip(chunks, times, prop):
                a, b = t + a, t + b
                if center:
                    spans = parse_spans(("[" if kw_on else "") + c, accent)
                    kw_on = open_state(c, kw_on)
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
            starts = [t + src_to_out(mp, w0(i)[0]) for i in ids]
            ends = [t + src_to_out(mp, w0(i)[1]) for i in ids]
            starts[0] = t  # 첫 자막은 세그먼트 시작과 함께
            # 빈 줄("")은 건너뛰고, 각 자막은 다음 자막 직전까지 유지
            shown = [k for k, txt in enumerate(s["subs"]) if txt.strip()]
            marked = False
            for n, k in enumerate(shown):
                i, txt = ids[k], s["subs"][k]
                a = max(t, starts[k] - 0.03)
                b = starts[shown[n + 1]] - 0.03 if n + 1 < len(shown) else t + sg["dur"]
                if pending_clip_sfx[0] and "[" in txt and a - t > 0.3:
                    sfx_events.append((a, pending_clip_sfx[0]))
                    pending_clip_sfx[0] = None
                if (not marked and mark_ok(s, txt, a)):
                    fo = face_at(mp, a - t)
                    if fo:
                        for ev_ in circle_events(fo, a, min(b, a + S_mark["hold_sec"]), cfg):
                            add(*ev_)
                        sfx_events.append((a, S_mark["sfx"]))
                        marks.append(a)
                        marked = True
                if center:
                    spans = parse_spans(wrap2(txt, S["dial_max_chars"]).replace(r"\N", "\n"),
                                        next_accent())
                    for ly, s0, s1, body in neon_events(spans, a, b, cfg, pos=sub_pos, an=sub_an):
                        add(s0, s1, "Dial", body, ly)
                    continue
                body, _ = keywordize(wrap2(txt, S["dial_max_chars"]), dial_base, dial_kw, False)
                add(a, b, "Dial", "{" + ENTER + "}" + body, 3)
            if s.get("tag") and S.get("show_tags", True):
                add(t, t + sg["dur"], "Tag", rf"{{\pos(60,{top + 40})}}" + pop + s["tag"], 2)
        pending_clip_sfx[0] = None  # 클립 안에서 못 쓴 효과음은 버림
        t += sg["dur"]
    (job / "subs.ass").write_text("\n".join(head + ev) + "\n", encoding="utf-8-sig")
    # 밀도 제한: 효과음끼리 min_gap 이상 (참고 영상 ≈ 10초에 2~3개)
    gap = cfg["render"]["sfx_min_gap"]
    kept = []
    for e in sorted(sfx_events):
        if not kept or e[0] - kept[-1][0] >= gap:
            kept.append(e)
    return kept


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

    up = plan.get("upload", {})
    (job / "upload.txt").write_text(
        f"제목: {up.get('title', '')}\n\n설명:\n{up.get('description', '')}\n\n태그: {', '.join(up.get('tags', []))}\n",
        encoding="utf-8")
    write_json(job / "timeline.json", [{"kind": s["kind"], "dur": round(s["dur"], 2)} for s in segs])
    return out, total
