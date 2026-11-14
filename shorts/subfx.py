"""네온 타자기 자막 (해외쇼쇼 스타일 분석 기반).

- 영상 패널 정중앙, 검은 외곽선 없음
- 어절별 색: 기본 흰색, [강조어] 는 이벤트마다 돌아가며 노랑/하늘/분홍/연두/주황
  [pink:강조어] 처럼 색을 직접 지정 가능. 줄 전체를 [ ] 로 감싸면 줄 전체가 색
- 글자가 한 글자씩 타이핑되며 나타나고, 같은 색 네온 빛이 크게 번졌다가 은은하게 남는다
"""
import re

PALETTE = ["yellow", "cyan", "pink", "green", "orange"]
NEON = {  # 네온용 밝은 색 (RGB)
    "white": "FFFFFF", "yellow": "FFE14D", "cyan": "4DF0FF", "pink": "FF6FC0",
    "green": "7CFF6B", "orange": "FFA640", "red": "FF4D5E", "blue": "5C8CFF", "purple": "C77DFF",
}


def bgr(name_or_hex):
    name_or_hex = re.sub(r"_(?:s|b)$", "", name_or_hex)  # "_s" = 작은 보조 줄, "_b" = 대박 강조 큰 글씨
    h = NEON.get(name_or_hex, name_or_hex).lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def parse_spans(text, accent):
    """'펌프로 [땅콩버터를]' → [(텍스트, 색이름)].
    - 문장 앞 '{big}' → 줄 전체 큰 강조 자막('_b')
    - '[big:yellow:대박]' 또는 '[big:대박]' → 해당 구간만 큰 강조 자막('_b')
    """
    line_big = False
    if "{big}" in text:
        line_big = True
        text = re.sub(r"\{big\}\s*", "", text)
    base_white = "white_b" if line_big else "white"
    spans, buf, color = [], "", base_white
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "[":
            if buf:
                spans.append((buf, color))
            buf = ""
            mb = re.match(r"\[big:(?:(\w+):)?", text[i:])
            if mb:
                cname = mb.group(1) if mb.group(1) and mb.group(1) in NEON else accent
                color = f"{cname}_b"
                i += len(mb.group(0))
                continue
            m = re.match(r"\[(\w+):", text[i:])
            if m and m.group(1) in NEON:
                color = f"{m.group(1)}_b" if line_big else m.group(1)
                i += len(m.group(0))
                continue
            color = f"{accent}_b" if line_big else accent
        elif ch == "]":
            if buf:
                spans.append((buf, color))
            buf, color = "", base_white
        else:
            buf += ch
        i += 1
    if buf:
        spans.append((buf, color))
    return spans


# 돈 금액: "약 100만 원", "2억 7천만 원", "16억~19억 원", "4만 원짜리"의 "4만 원" 부분
_NUM = r"\d[\d,.]*\s*(?:조|억|천만|백만|만|천)?(?:\s*\d[\d,.]*\s*(?:억|천만|백만|만|천))*"
MONEY_RE = re.compile(rf"(?:약\s*)?{_NUM}(?:\s*~\s*{_NUM})?\s*원")


def money_green(spans):
    """초록색은 돈 금액에만 (사용자 요청 2026-11-09).
    - 금액 부분은 원래 색과 상관없이 초록 ('_b' 큰 글씨 속성은 유지)
    - 금액이 아닌데 초록으로 적힌 말은 노랑으로 바꿈"""
    out = []
    for txt, col in spans:
        if col.endswith("_s"):  # 보조 줄(원화 환산 등)은 그대로
            out.append((txt, col))
            continue
        suf = "_b" if col.endswith("_b") else ""
        raw_col = col.removesuffix("_b")
        base = ("yellow" if raw_col == "green" else raw_col) + suf
        k = 0
        for m in MONEY_RE.finditer(txt):
            if m.start() > k:
                out.append((txt[k:m.start()], base))
            out.append((m.group(0), "green" + suf))
            k = m.end()
        if k < len(txt):
            out.append((txt[k:], base))
    return out


def open_state(text, was_open):
    """덩어리 끝에서 강조 괄호가 열려 있는지."""
    o = was_open
    for ch in text:
        if ch == "[":
            o = True
        elif ch == "]":
            o = False
    return o


def _ellipse(rx, ry):
    """(0,0)~(2rx,2ry) 박스에 꼭 맞는 타원 경로 (ASS 드로잉, 베지어 4개)."""
    k = 0.5523
    r = lambda v: f"{v:.1f}"
    return (f"m {r(rx)} 0 b {r(rx + k * rx)} 0 {r(2 * rx)} {r(ry - k * ry)} {r(2 * rx)} {r(ry)} "
            f"b {r(2 * rx)} {r(ry + k * ry)} {r(rx + k * rx)} {r(2 * ry)} {r(rx)} {r(2 * ry)} "
            f"b {r(rx - k * rx)} {r(2 * ry)} 0 {r(ry + k * ry)} 0 {r(ry)} "
            f"b 0 {r(ry - k * ry)} {r(rx - k * rx)} 0 {r(rx)} 0")


def circle_events(face_out, t0, t1, cfg):
    """말하는 사람 얼굴에 빨간 동그라미 (캡컷 확대 인 애니메이션과 같은 0.2초 등장)."""
    M = cfg["marks"]
    cx, cy, fw, fh = face_out
    rx, ry = fw * M["size_x"] / 2, fh * M["size_y"] / 2
    zi = cfg["subtitle"]["zoom_in_ms"]
    col = bgr(M["color"])
    body = (rf"{{\an5\pos({cx:.0f},{cy:.0f})\p1\1a&HFF&\3c{col}\bord{M['width']}\shad0\blur0.6"
            rf"\fscx0\fscy0\t(0,{zi},0.6,\fscx100\fscy100)}}" + _ellipse(rx, ry) + r"{\p0}")
    return [(t0, t1, "Narr", body, 2)]


def money_icon_events(t0, t1, cx, cy, layer=9):
    """돈 금액 자막 아래 지폐 3장 아이콘 (2026-11-10 사용자 요청: 매출 자막 밑에 지폐 이모티콘 + 효과음).
    컬러 이모지는 libass 가 못 그려서 ASS 드로잉으로: 초록 지폐 3장이 부채꼴로 톡 튀어나옴."""
    W, H = 200, 100
    rect = lambda x0, y0, x1, y1: f"m {x0} {y0} l {x1} {y0} l {x1} {y1} l {x0} {y1}"
    # 드로잉 좌표는 0 부터 (빨간 원 _ellipse 와 같은 방식) → an5 가 상자 가운데를 move 점에 맞춤
    pop = r"\fscx0\fscy0\t(0,130,\fscx118\fscy118)\t(130,230,\fscx100\fscy100)"
    evs = []
    for k, (dx, rot, dy) in enumerate(((-70, 16, 10), (70, -16, 10), (0, 0, 0))):  # 가운데 장이 맨 위
        x, y = cx + dx, cy + dy
        mv = rf"\move({x},{y + 34},{x},{y},0,180)"
        body = rf"{{\an5\org({x},{y}){mv}\frz{rot}\p1\1c&H5ABE3D&\3c&H2A6B1A&\bord4\shad3\4c&H000000&\4a&H80&{pop}}}"
        # 지폐 본체 (an5: 드로잉 상자 가운데가 move 점)
        evs.append((layer + 2 * k, t0, t1, body + rect(0, 0, W, H) + r"{\p0}"))
        inner = rf"{{\an5\org({x},{y}){mv}\frz{rot}\p1\1a&HFF&\3c&HD6F5CF&\bord2\shad0{pop}}}"
        evs.append((layer + 2 * k + 1, t0, t1, inner + rect(0, 0, W - 22, H - 20) + r"{\p0}"))
        won = rf"{{\an5\org({x},{y}){mv}\frz{rot}\b1\fs72\1c&HEFFFEF&\3c&H2A6B1A&\bord4\shad0{pop}}}₩"
        evs.append((layer + 2 * k + 1, t0, t1, won))
    return evs


FRAME = 1 / 30
SMALL = 0.68  # 보조 줄(원화 환산) 글자 크기 비율


def _fs(col, size, big_size=None):
    if col.endswith("_s"):
        return rf"\fs{round(size * SMALL)}"
    if col.endswith("_b"):
        return rf"\fs{big_size if big_size else round(size * 1.29)}"
    return rf"\fs{size}"


def _colored(spans, alpha, size):
    out = []
    for txt, col in spans:
        out.append(rf"{{\1c{bgr(col)}\3c{bgr(col)}\alpha{alpha}{_fs(col, size)}}}" + txt.replace("\n", r"\N"))
    return "".join(out)


def _smear(spans, t0, t1, pos, layer, size, an=5):
    """등장 1프레임의 약한 가로 번짐.
    참고: 2jJoIbdciDY 는 1~2프레임 가로 번짐, QMJtjJz4M6w 는 일부 전환만 짧게 흐림, 0HbV-PW3ivw 는 대부분 즉시 교체.
    가독성 조정: 이동 폭 ±6~10px, 사본 3장, 약한 블러로 잔상이 남지 않게 (이전 ±42px·7장은 잔상처럼 보였음)."""
    x, y = pos
    evs = []
    for dx, a in ((-10, "&HC8&"), (0, "&H10&"), (10, "&HC8&")):
        evs.append((layer, t0, t1, rf"{{\an{an}\pos({x + dx},{y})\fscx104\bord1\blur1.5\shad0}}" + _colored(spans, a, size)))
    return evs


def merge_short(subs, min_dur=0.8, line_chars=14):
    """너무 짧게 지나가는 자막(min_dur 미만)은 같은 항목 안의 다음(없으면 앞) 자막과 의미 단위로 합친다.
    합친 글자가 길면 두 줄(| = 줄바꿈)로. 참고 영상 자막은 대부분 1초 안팎 이상 머문다."""
    out = [dict(s) for s in subs]
    changed = True
    while changed:
        changed = False
        for j, s in enumerate(out):
            # solo = 감탄사(와우! 등): 문장에 붙이지 않고 혼자 뜸 (사용자 요청 2026-11-06)
            if s["b"] - s["a"] >= min_dur or s.get("solo"):
                continue
            ok = lambda i: 0 <= i < len(out) and out[i].get("item") == s.get("item") and not out[i].get("solo")
            k = j + 1 if ok(j + 1) else (j - 1 if ok(j - 1) else None)
            if k is None:
                continue
            first, second = (s, out[k]) if k > j else (out[k], s)
            n = sum(len(t.strip()) for t, _ in first["spans"]) + sum(len(t.strip()) for t, _ in second["spans"])
            sep = [("\n", "white")] if n > line_chars else [(" ", "white")]
            krw = " · ".join(x for x in (first.get("krw"), second.get("krw")) if x)
            merged = {**first, "krw": krw, "note": second.get("note") or first.get("note", ""), "b": second["b"], "spans": first["spans"] + sep + second["spans"],
                      "text": first.get("text", "") + (" | " if n > line_chars else " ") + second.get("text", "")}
            lo = min(j, k)
            out[lo:lo + 2] = [merged]
            changed = True
            break
    return out


def ref_sub_events(subs, cfg, pos, an=5):
    """자막 등장·교체 (참고 3편 비교 + 가독성 조정).
    - 등장: 약한 가로 번짐 1프레임 → 선명 (크기·위치·투명도 변화 없음, 강조어도 줄과 함께)
    - 퇴장: 다음 자막으로 즉시 교체 (번짐 없음)
    - 확대·바운스·페이드 없음."""
    F = FRAME
    evs = []
    sharp_cfg = {**cfg, "subtitle": {**cfg["subtitle"], "zoom_in_ms": 1, "zoom_from": 100}}
    for s in subs:
        a, b, sp = s["a"], s["b"], s["spans"]
        if s.get("krw"):  # 달러 금액 → 아래 줄에 작은 초록 글씨로 원화 환산
            sp = sp + [("\n", "white"), (s["krw"], "green_s")]
        if s.get("note") and not "".join(t for t, _ in sp).strip():  # 안내만 따로 (마지막 대사 뒤, 2026-11-09)
            sp = [(s["note"], "5CB8FF")]
        elif s.get("note"):  # 마지막 자막 아래 작은 파란 안내 줄 (참고: 셀 인사이트 끝 자막 "(자세한 내용은 고정댓글 참조)")
            sp = sp + [("\n", "white"), (s["note"], "5CB8FF_s")]
        evs += _smear(sp, a, a + F, pos, 5, cfg["subtitle"]["narr_size"], an)
        evs += neon_events(sp, a + F, b, sharp_cfg, pos=pos, an=an)
    return evs


def neon_events(spans, t0, t1, cfg, layer_base=3, pos=None, an=5):
    """한 자막 덩어리 → [(layer, start, end, text)].

    참고 영상 프레임 분석 결과를 그대로 옮김:
    - 긴 줄: 글자가 한 글자씩(약 1프레임 간격) 회색→흰색으로 빠르게 페이드 인 (타자기)
    - 짧은 감탄(≤ pop_max_chars): 작게 → 살짝 크게(오버슈트) → 제자리로 튀어나오는 팝
    - 빛: 크게 번지지 않는 '글자색 네온' + 가독성용 '어두운 그림자'. 등장 후엔 고정
    """
    S = cfg["subtitle"]
    if S.get("look") == "capcut_v16":
        return capcut_events(spans, t0, t1, S, layer_base, pos, an)
    head = (rf"\an{an}\pos({pos[0]},{pos[1]})" if pos else "")
    # 캡컷 [애니메이션 > 인 > 확대]: 0% → 100% 로 커지며 등장 (zoom_in_ms, 끝으로 갈수록 감속)
    zi = S["zoom_in_ms"]
    head += rf"\fscx{S['zoom_from']}\fscy{S['zoom_from']}\t(0,{zi},{S['zoom_accel']},\fscx100\fscy100)"

    def typed(alpha_to, color_tag):
        out = []
        for txt, col in spans:
            a_to = alpha_to(col) if callable(alpha_to) else alpha_to
            out.append("{" + color_tag(col) + _fs(col, S["narr_size"]) + rf"\alpha&HFF&\t(0,{max(1, zi // 2)},\alpha{a_to})}}")
            out.append(txt.replace("\n", r"\N"))
        return "".join(out)

    # ① 어두운 그림자 (밝은 배경에서도 읽히게)
    shadow = ("{" + head + rf"\shad0\bord{S['shadow_bord']}\blur{S['shadow_blur']}\3c&H000000&\1c&H000000&}}"
              + typed(S["shadow_alpha"], lambda c: ""))
    # ② 글자색 네온 빛 (좁고 선명하게)
    glow = ("{" + head + rf"\shad0\bord{S['glow_bord']}\blur{S['glow_blur']}}}"
            + typed(lambda c: S["glow_alpha_white"] if c == "white" else S["glow_alpha"],
                    lambda c: rf"\1c{bgr(c)}\3c{bgr(c)}"))
    # ③ 글자 본체. text_outline 이 있으면 글자 외곽선을 검은색으로 (네온 위 가독성, 2026-11-10 사용자 요청)
    ol = S.get("text_outline")
    text = ("{" + head + rf"\shad0\bord{ol if ol else S['text_bord']}\blur{S['text_blur']}}}"
            + typed("&H00&", (lambda c: rf"\1c{bgr(c)}\3c&H000000&") if ol else (lambda c: rf"\1c{bgr(c)}\3c{bgr(c)}")))
    return [(layer_base, t0, t1, shadow), (layer_base + 1, t0, t1, glow),
            (layer_base + 2, t0, t1, text)]


# 사용자가 CapCut coconut_cocotaps_v16 에서 직접 맞춘 자막 스타일 (2026-11-11, 이후 고정)
#  - 글씨체 배달의민족 주아(BM JUA), 크기 13 (CapCut) = em 67.6px → libass 74
#  - 외곽선 없음. 그림자: 검정 90%, 거리 5, 각도 -45°(오른쪽 아래), 흐림 0.45
#  - 애니메이션 [인 > 블러 입장] 0.133초. 색: 노랑 #FFDC00, 돈 초록 #2CFF23 (사용자 프로젝트 값)
CAPCUT_COLORS = {"yellow": "FFE11A", "green": "2CFF23", "red": "FF3B4E", "cyan": "3DF2FF", "orange": "FFA62B"}


def capcut_events(spans, t0, t1, S, layer_base=3, pos=None, an=5):
    size = S.get("look_size", 76)
    big_size = S.get("look_big_size", round(size * 1.29))
    ol = S.get("look_outline", 3.0)
    ms = min(int(S.get("look_in_ms", 65)), 70)
    blur_in = min(float(S.get("look_blur_from", 6)), 6.0)
    x, y = pos if pos else (540, 960)
    has_big = any(col.endswith("_b") for _, col in spans)
    # 신기하거나 대단하거나 대박인 자막('_b'): 더 큰 글자 크기 + 탄력 있게 튀어나오는 팝 애니메이션
    pop_anim = r"\fscx86\fscy86\t(0,115,\fscx106\fscy106)\t(115,200,\fscx100\fscy100)" if has_big else ""

    def line(color_of, alpha_start, alpha_end, dx, dy, blur_end, bord, out_col=""):
        head = (rf"{{\an{an}\pos({x + dx},{y + dy})\fn{S.get('look_font', 'BM JUA')}\b0\bord{bord}{out_col}\shad0"
                rf"{pop_anim}\alpha{alpha_start}\blur{blur_in}\t(0,{ms},\alpha{alpha_end}\blur{blur_end})}}")
        out = []
        for txt, col in spans:
            out.append("{" + color_of(col) + _fs(col, size, big_size) + "}" + txt.replace("\n", r"\N"))
        return head + "".join(out)

    shadow = line(lambda c: r"\1c&H000000&", "&H50&", S.get("look_shadow_alpha", "&H1A&"),
                  S.get("look_shadow_dx", 4), S.get("look_shadow_dy", 4), S.get("look_shadow_blur", 5), ol, r"\3c&H000000&")
    text = line(lambda c: rf"\1c{bgr(CAPCUT_COLORS.get(re.sub(r'_(?:s|b)$', '', c), c))}", "&H28&", "&H00&", 0, 0, 0.4,
                ol, r"\3c&H000000&")
    return [(layer_base, t0, t1, shadow), (layer_base + 2, t0, t1, text)]
