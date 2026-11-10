"""EDL → CapCut 프로젝트 (편집 가능한 레이어로).

렌더러(edl.py)로 한 번 만든 뒤, 그 재료를 따로따로 CapCut 프로젝트에 넣는다.
  영상 트랙   : 장면(샷)마다 자른 클립 (크롭·확대·색 번쩍임·빨간 원은 클립 안에 이미 들어 있음)
  목소리 트랙 : 박자별 내레이션 / 원본 대사 소리
  효과음 트랙 : 렌더러가 고른 효과음 파일, 같은 위치·같은 크기
  자막·제목   : CapCut 텍스트 (글자·색 그대로 고칠 수 있음)

사용:  python -m shorts.capcut --job vulcan_rattrap --edl v5_edl.json [--preset 해외쇼쇼] [--name 이름]
       python -m shorts.capcut --register-only  (CapCut 이 켜져 있어서 목록 등록을 못 했을 때)

주의: CapCut 이 켜져 있으면 프로젝트 목록 파일(root_meta_info.json)을 CapCut 이 덮어쓰므로,
      목록 등록은 CapCut 을 끈 상태에서만 한다. 기존 CapCut 프로젝트는 건드리지 않는다.
"""
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .common import ASSETS, JOBS, load_config, read_json, write_json

TPL = Path(__file__).with_name("capcut_template.json")
FONT = ASSETS / "fonts" / "Cafe24Anemone.ttf"  # 글꼴 이름 'Cafe24 Ohsquare' (렌더러 자막과 같은 글꼴)
PENDING = JOBS / "_capcut_pending.json"  # 등록 못 한 프로젝트 (CapCut 이 켜져 있었을 때)
US = 1_000_000
CANVAS_W, CANVAS_H = 1080, 1920

# CapCut 글자 크기 1 = 화면(1080x1920) 몇 px 의 em 인지 (기존 프로젝트 표지 이미지로 맞춘 값)
PX_PER_SIZE = 5.2
LINE_H = 1.15  # 줄 높이 / em (위치 계산용 근사)

NEON = {"white": "FFFFFF", "yellow": "FFE14D", "cyan": "4DF0FF", "pink": "FF6FC0", "green": "7CFF6B",
        "orange": "FFA640", "red": "FF4D5E", "blue": "5C8CFF", "purple": "C77DFF"}
SMALL = 0.68
# 자막 외곽선·그림자: 사용자가 vulcan_rattrap_v5 에서 CapCut 으로 직접 맞춘 값 그대로 (2026-11-08)
SUB_STROKE = 0.05603644624352455
SUB_SHADOW_DIFFUSE = 0.028853455558419228
SUB_SHADOW_SMOOTHING = 0.5193622000515461


def us(t):
    return int(round(t * US))


def uid():
    return str(uuid.uuid4()).upper()


def rgb(name):
    h = NEON.get(name.removesuffix("_s"), name.removesuffix("_s")).lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def u16len(s):
    return len(s.encode("utf-16-le")) // 2


def probe_dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def ff(*args, cwd=None):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("ffmpeg 실패: " + r.stderr[-1500:])


def capcut_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe"], capture_output=True, text=True,
                             errors="replace").stdout
        return "CapCut.exe" in out
    except OSError:
        return False


def draft_root():
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    rm = base / "root_meta_info.json"
    if rm.exists():
        try:
            return Path(json.loads(rm.read_text(encoding="utf-8"))["root_path"])
        except (ValueError, KeyError):
            pass
    return base


def deep_merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def font_em_ratio():
    """libass 글자 크기 → em 비율 (= unitsPerEm / (winAscent+winDescent))."""
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(str(FONT))
        return f["head"].unitsPerEm / (f["OS/2"].usWinAscent + f["OS/2"].usWinDescent)
    except Exception:
        return 0.85


# ---------------------------------------------------------------- 재료 만들기

def prepare_media(job, exp, out_dir, make=True):
    """edl_tmp 의 렌더 결과에서 샷 클립·목소리·효과음 파일을 만든다. make=False: 이미 만든 파일을 다시 씀."""
    tmp = job / "edl_tmp"
    body = tmp / "body.mp4"
    top = exp["top"]
    pw, ph = exp["panel"]
    shots_dir, voice_dir, sfx_dir = out_dir / "shots", out_dir / "voice", out_dir / "sfx"
    for d in (shots_dir, voice_dir, sfx_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 샷 경계 (0 길이 샷 제외)
    shots = []
    for it in exp["items"]:
        for a, b, why, src in it["shots"]:
            if b - a > 1 / 60:
                shots.append({"a": a, "b": b, "why": why, "src": src, "item": it["n"]})
    cuts = [s["a"] for s in shots[1:]]

    # 빨간 원(ASS 그림)은 화면 전체 좌표 → 패널 높이로 늘렸다가 원을 그리고 다시 잘라냄
    vf = []
    if exp["circles"]:
        (out_dir / "circles.ass").write_text("\n".join(exp["ass_head"] + exp["circles"]) + "\n", encoding="utf-8-sig")
        vf = [f"pad={pw}:{CANVAS_H}:0:{top}:color=black", "ass=circles.ass", f"crop={pw}:{ph}:0:{top}"]
    times = ",".join(f"{max(0.0, t - 0.001):.4f}" for t in cuts)
    args = ["-i", body, "-an"]
    if vf:
        args += ["-vf", ",".join(vf)]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", "-g", "600"]
    if cuts:
        args += ["-force_key_frames", times, "-f", "segment", "-segment_times", times, "-reset_timestamps", "1",
                 "shots/s%03d.mp4"]
    else:
        args += ["shots/s000.mp4"]
    if make:
        ff(*args, cwd=out_dir)
    for k, s in enumerate(shots):
        s["file"] = shots_dir / f"s{k:03d}.mp4"
        if not s["file"].exists():
            raise RuntimeError(f"샷 파일이 없음: {s['file']}")
        s["file_dur"] = probe_dur(s["file"])

    # 목소리: 박자별로 잘라냄 (렌더러가 이미 크기를 맞춘 소리)
    voices = []
    for it in exp["items"]:
        f = voice_dir / f"i{it['n']:02d}_{it['type']}.wav"
        if make:
            ff("-i", body, "-ss", f"{it['t0']:.4f}", "-t", f"{it['dur']:.4f}", "-vn", "-ac", "2", "-ar", "48000",
               "-c:a", "pcm_s16le", f)
        voices.append({"a": it["t0"], "dur": it["dur"], "type": it["type"], "file": f, "n": it["n"]})

    # 효과음: 파일을 프로젝트 폴더로 복사 (원래 효과음 폴더를 옮겨도 프로젝트가 안 깨지게)
    sfx = []
    for k, e in enumerate(exp["sfx"]):
        src = Path(e["file"])
        dst = sfx_dir / f"{k:02d}_{src.name}"
        if make:
            shutil.copy2(src, dst)
        fd = probe_dur(dst)
        cap = e.get("max_dur", 1.7)  # 예전 효과음 폴더: 1.7초에서 끊음 / opus: 끝까지
        dur = max(0.05, (fd - e["lead"]) if cap is None else min(cap, fd - e["lead"]))
        sfx.append({**e, "file": dst, "file_dur": fd, "dur": dur})

    # BGM (2026-11-10): 덕킹·크기를 미리 입힌 wav 를 프로젝트 폴더로 복사
    if exp.get("bgm"):
        (out_dir / "bgm").mkdir(exist_ok=True)
        src = Path(exp["bgm"]["file"])
        dst = out_dir / "bgm" / src.name
        if make and src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        exp["bgm"]["file"] = str(dst)
    return shots, voices, sfx


# ---------------------------------------------------------------- CapCut JSON

class Draft:
    def __init__(self, total, name, folder):
        self.T = read_json(TPL)
        self.c = copy.deepcopy(self.T["content"])
        self.c["id"] = uid()
        self.c["duration"] = us(total)
        self.c["canvas_config"] = {"ratio": "9:16", "width": CANVAS_W, "height": CANVAS_H, "background": None}
        self.c["name"] = ""
        self.name, self.folder, self.total = name, folder, total
        self.media = []  # draft_meta_info 의 미디어 목록
        self.n_tracks = 0

    def mat(self, key, obj):
        self.c["materials"].setdefault(key, []).append(obj)
        return obj["id"]

    def track(self, kind, name):
        t = copy.deepcopy(self.T[{"video": "video", "audio": "audio", "text": "text"}[kind]]["track"])
        t.update({"id": uid(), "type": kind, "segments": [], "name": name, "is_default_name": False,
                  "flag": 0, "attribute": 0})
        self.c["tracks"].append(t)
        self.n_tracks += 1
        t["_idx"] = self.n_tracks - 1
        return t

    def _segment(self, kind, track, mat_id, start, dur, src_start=0.0, src_dur=None):
        b = self.T[kind]
        s = copy.deepcopy(b["segment"])
        s["id"] = uid()
        s["material_id"] = mat_id
        s["target_timerange"] = {"start": us(start), "duration": us(dur)}
        s["source_timerange"] = None if kind == "text" else {"start": us(src_start), "duration": us(src_dur or dur)}
        refs = []
        for key, obj in b["extras"]:
            o = copy.deepcopy(obj)
            o["id"] = uid()
            refs.append(self.mat(key, o))
        s["extra_material_refs"] = refs
        s["track_render_index"] = track["_idx"]
        track["segments"].append(s)
        return s

    def _media_entry(self, path, dur, kind, w=0, h=0):
        mid = str(uuid.uuid4())
        now = int(time.time())
        self.media.append({"ai_group_type": "", "create_time": now, "duration": us(dur), "enter_from": 0,
                           "extra_info": path.name, "file_Path": path.as_posix(), "height": h, "id": mid,
                           "import_time": now, "import_time_ms": int(time.time() * US), "item_source": 1,
                           "material_color_tag": "", "md5": "", "metetype": kind,
                           "roughcut_time_range": {"duration": us(dur), "start": 0},
                           "sub_time_range": {"duration": -1, "start": -1}, "type": 0, "width": w})
        return mid

    def add_video(self, track, path, start, dur, file_dur, w, h, y_px_center):
        m = copy.deepcopy(self.T["video"]["material"])
        m.update({"id": uid(), "path": path.as_posix(), "material_name": path.name, "duration": us(file_dur),
                  "width": w, "height": h, "has_audio": False, "category_name": "local",
                  "local_material_id": self._media_entry(path, file_dur, "video", w, h)})
        mid = self.mat("videos", m)
        s = self._segment("video", track, mid, start, dur, 0.0, min(dur, file_dur))
        s["volume"] = 0.0
        s["clip"]["transform"] = {"x": 0.0, "y": (CANVAS_H / 2 - y_px_center) / (CANVAS_H / 2)}
        s["render_index"] = 0
        return s

    def add_audio(self, track, path, start, dur, file_dur, src_start=0.0, volume=1.0):
        m = copy.deepcopy(self.T["audio"]["material"])
        m.update({"id": uid(), "path": path.as_posix(), "name": path.name, "duration": us(file_dur),
                  "category_name": "local", "music_id": str(uuid.uuid4()),
                  "local_material_id": self._media_entry(path, file_dur, "music")})
        mid = self.mat("audios", m)
        s = self._segment("audio", track, mid, start, dur, src_start, dur)
        s["volume"] = volume
        s["last_nonzero_volume"] = volume
        s["clip"] = None
        return s

    def add_text(self, track, spans, start, dur, size, y_px_center, shadow=True, render_index=14000):
        """spans = [(글자, 색이름)] — '_s' 로 끝나는 색은 작은 글씨."""
        text, styles = "", []
        for t, col in spans:
            if not t:
                continue
            a = u16len(text)
            text += t
            st = {"fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": rgb(col)}}},
                  "font": {"id": "", "path": FONT.as_posix()},
                  "range": [a, u16len(text)], "size": round(size * (SMALL if col.endswith("_s") else 1.0), 2),
                  "useLetterColor": True}
            if shadow:  # 자막: 사용자가 CapCut 에서 맞춘 값 (2026-11-08) — 검은 외곽선 + 흐림 낮춘 그림자
                st["strokes"] = [{"content": {"render_type": "solid", "solid": {"color": [0, 0, 0]}},
                                  "width": SUB_STROKE, "mode": 0}]
                st["shadows"] = [{"thickness_projection_angle": -45, "thickness_projection_enable": False,
                                  "diffuse": SUB_SHADOW_DIFFUSE, "alpha": 0.62, "distance": 0,
                                  "content": {"render_type": "solid", "solid": {"color": [0, 0, 0]}},
                                  "angle": -45, "thickness_projection_distance": 0}]
            styles.append(st)
        m = copy.deepcopy(self.T["text"]["material"])
        m.update({"id": uid(), "content": json.dumps({"styles": styles, "text": text}, ensure_ascii=False),
                  "font_path": FONT.as_posix(), "font_name": "Cafe24 Ohsquare", "font_title": "Cafe24 Ohsquare",
                  "font_size": float(size), "text_color": "#ffffff", "alignment": 1, "typesetting": 0,
                  "line_spacing": 0.02, "border_width": SUB_STROKE if shadow else 0.0, "border_alpha": 0.0,
                  "border_color": "#000000", "border_mode": 0,
                  "has_shadow": bool(shadow), "shadow_color": "#000000", "shadow_alpha": 0.62,
                  "shadow_smoothing": SUB_SHADOW_SMOOTHING, "shadow_distance": 0.0, "shadow_angle": -45.0,
                  "use_effect_default_color": False,
                  "is_rich_text": len(styles) > 1})
        mid = self.mat("texts", m)
        s = self._segment("text", track, mid, start, dur)
        s["clip"]["transform"] = {"x": 0.0, "y": (CANVAS_H / 2 - y_px_center) / (CANVAS_H / 2)}
        s["render_index"] = render_index
        return s

    def write(self):
        for t in self.c["tracks"]:
            t.pop("_idx", None)
        f = self.folder
        f.mkdir(parents=True)
        now_us = int(time.time() * US)
        write_json(f / "draft_content.json", self.c)
        meta = copy.deepcopy(self.T["meta"])
        meta.update({"draft_fold_path": f.as_posix(), "draft_id": uid(), "draft_name": self.name,
                     "draft_root_path": str(f.parent), "draft_cover": "", "tm_draft_create": now_us,
                     "tm_draft_modified": now_us, "tm_duration": us(self.total),
                     "draft_timeline_materials_size_": 0})
        meta["draft_materials"] = [{"type": t, "value": (self.media if t == 0 else [])} for t in (0, 1, 2, 3, 6, 7, 8)]
        write_json(f / "draft_meta_info.json", meta)
        return meta


def pack(track_list, a, b, eps=1e-4):
    """겹치지 않게 들어갈 첫 트랙 번호 (효과음처럼 겹치는 소리용)."""
    for k, ends in enumerate(track_list):
        if a >= ends - eps:
            track_list[k] = b
            return k
    track_list.append(b)
    return len(track_list) - 1


def build_draft(exp, cfg, shots, voices, sfx, name, folder):
    S = cfg["subtitle"]
    top = exp["top"]
    pw, ph = exp["panel"]
    em = font_em_ratio()
    size_of = lambda ass_size: round(ass_size * em / PX_PER_SIZE, 2)
    master = 10 ** (exp["master_db"] / 20)
    d = Draft(exp["total"], name, folder)

    # 영상 (메인 트랙)
    vt = d.track("video", "영상")
    for s in shots:
        d.add_video(vt, s["file"], s["a"], s["b"] - s["a"], s["file_dur"], pw, ph, top + ph / 2)

    # 목소리: 내레이션 / 원본 대사 따로
    nt, ct = d.track("audio", "내레이션"), d.track("audio", "원본 대사")
    for v in voices:
        d.add_audio(nt if v["type"] == "narration" else ct, v["file"], v["a"], v["dur"], probe_dur(v["file"]),
                    volume=master)

    # 효과음: 겹치면 트랙을 나눔
    ends, sfx_tracks = [], []
    for e in sorted(sfx, key=lambda x: x["t"]):
        k = pack(ends, e["t"], e["t"] + e["dur"])
        while len(sfx_tracks) <= k:
            sfx_tracks.append(d.track("audio", f"효과음 {len(sfx_tracks) + 1}"))
        d.add_audio(sfx_tracks[k], e["file"], e["t"], e["dur"], e["file_dur"], src_start=e["lead"],
                    volume=10 ** (e["gain_db"] / 20) * master)

    # BGM: 한 트랙, 덕킹은 파일에 이미 들어 있음 → 목소리와 같은 마스터 크기만
    if exp.get("bgm"):
        bt = d.track("audio", "BGM")
        bf = Path(exp["bgm"]["file"])
        d.add_audio(bt, bf, 0.0, min(exp["total"], probe_dur(bf)), probe_dur(bf), volume=master)

    # 자막
    st = d.track("text", "자막")
    nsize = size_of(S["narr_size"])
    line_px = S["narr_size"] * em * LINE_H
    for k, x in enumerate(exp["subs"]):
        spans = [tuple(p) for p in x["spans"]]
        if x.get("krw"):
            spans += [("\n", "white"), (x["krw"], "green_s")]
        alone = x.get("note") and not "".join(t for t, _ in spans).strip()
        if alone:  # 안내만 따로 뜨는 자막 (2026-11-09: 마지막 대사 뒤에 따로) → 보통 크기
            spans = [(x["note"], "5CB8FF")]
        elif x.get("note"):  # 끝 자막 아래 작은 파란 안내 줄
            spans += [("\n", "white"), (x["note"], "5CB8FF_s")]
        text = "".join(t for t, _ in spans)
        lines = text.split("\n")
        h = sum(line_px * (SMALL if ln in (x.get("krw"), x.get("note")) and not alone else 1.0) for ln in lines)
        if exp["sub_an"] == 2:  # 아래 끝 고정
            cy = exp["sub_pos"][1] - h / 2
        else:
            cy = exp["sub_pos"][1]
        d.add_text(st, spans, x["a"], x["b"] - x["a"], nsize, cy, render_index=14000 + k)

    # 워터마크 (2026-11-10): 렌더러와 같은 자리 — 영상 패널 바로 아래 검은 띠에 흐린 흰색 워터마크(config.json → watermark.text)
    wm = cfg.get("watermark", {})
    if wm.get("text"):
        wt = d.track("text", "워터마크")
        wsize = wm.get("size", 40)
        y_top = top + ph + wm.get("gap", 22)  # 렌더러는 글자 위쪽 기준(an8)
        seg = d.add_text(wt, [(wm["text"], "white")], 0.0, exp["total"], size_of(wsize),
                         y_top + wsize * em * LINE_H / 2, shadow=False, render_index=13100)
        seg["clip"]["alpha"] = round(1 - int(wm.get("alpha", "&HA8&").strip("&H"), 16) / 255, 3)

    # 제목 2줄 (각 줄 따로 — 렌더러와 같은 위치)
    tsize = S["title_size"]
    for j, ln in enumerate(exp["title_lines"][:2]):
        tt = d.track("text", f"제목 {j + 1}")
        cy = top - 30 - tsize * (1.65 if j == 0 else 0.55)
        d.add_text(tt, [(ln["text"], ln["color"])], 0.0, exp["total"], size_of(tsize), cy, shadow=False,
                   render_index=13000 + j)
    return d


# ---------------------------------------------------------------- CapCut 목록 등록

def register(folder, meta):
    root = folder.parent
    rm = root / "root_meta_info.json"
    if not rm.exists():
        return "목록 파일이 없어 등록을 건너뜀 (CapCut 을 켜면 폴더를 찾아 보여 줄 수 있음)"
    data = json.loads(rm.read_text(encoding="utf-8"))
    store = data.setdefault("all_draft_store", [])
    if any(e.get("draft_fold_path") == folder.as_posix() for e in store):
        return "이미 등록됨"
    bak = rm.with_name(f"root_meta_info.json.bak-shorts-{int(time.time())}")
    shutil.copy2(rm, bak)
    tpl = copy.deepcopy(store[0]) if store else {}
    tpl.update({"draft_cover": "", "draft_fold_path": folder.as_posix(), "draft_id": meta["draft_id"],
                "draft_json_file": (folder / "draft_content.json").as_posix(), "draft_name": meta["draft_name"],
                "draft_root_path": str(root), "draft_timeline_materials_size": 0,
                "tm_draft_create": meta["tm_draft_create"], "tm_draft_modified": meta["tm_draft_modified"],
                "tm_duration": meta["tm_duration"], "tm_draft_removed": 0, "draft_is_invisible": False})
    store.insert(0, tpl)
    if "draft_ids" in data and isinstance(data["draft_ids"], int):
        data["draft_ids"] += 1
    rm.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return f"CapCut 목록에 등록함 (백업: {bak.name})"


def register_pending():
    if not PENDING.exists():
        print("등록할 프로젝트가 없습니다.")
        return
    if capcut_running():
        print("CapCut 이 켜져 있습니다. CapCut 을 완전히 끈 뒤 다시 실행해 주세요.")
        return
    for p in read_json(PENDING):
        folder = Path(p)
        meta = read_json(folder / "draft_meta_info.json")
        print(folder.name, "→", register(folder, meta))
    PENDING.unlink()


# ---------------------------------------------------------------- 실행

def export(job_name, edl_name, preset="", name=""):
    job = JOBS / job_name
    cfg = load_config()
    if preset:
        p = Path(__file__).resolve().parent.parent / "editor" / "presets" / f"{preset}.json"
        deep_merge(cfg, read_json(p).get("config", {}))
    stem = Path(edl_name).stem.removesuffix("_edl")
    base = name or f"{job_name}_{stem}"
    root = draft_root()
    folder, k = root / base, 2
    while folder.exists():
        folder, k = root / f"{base}_{k}", k + 1

    out_name, k = f"capcut_{stem}.mp4", 2
    while (job / out_name).exists():
        out_name, k = f"capcut_{stem}_{k}.mp4", k + 1
    print(f"1/3 렌더링 (재료 만들기): {job_name}/{edl_name} → {out_name}", flush=True)
    from .edl import render_edl
    _, total, log = render_edl(job, job / edl_name, cfg, out_name=out_name)
    (job / out_name.replace(".mp4", "_log.txt")).write_text("\n".join(log), encoding="utf-8")
    exp = read_json(job / "edl_tmp" / "export.json")

    media_dir = job / "capcut" / folder.name
    if media_dir.exists():
        shutil.rmtree(media_dir)
    media_dir.mkdir(parents=True)
    print(f"2/3 샷·목소리·효과음 파일 만들기 → {media_dir}", flush=True)
    write_json(media_dir / "export.json", exp)
    shots, voices, sfx = prepare_media(job, exp, media_dir)

    print(f"3/3 CapCut 프로젝트 쓰기 → {folder}", flush=True)
    d = build_draft(exp, cfg, shots, voices, sfx, folder.name, folder)
    meta = d.write()
    if capcut_running():
        pend = read_json(PENDING) if PENDING.exists() else []
        pend.append(folder.as_posix())
        write_json(PENDING, pend)
        msg = ("CapCut 이 켜져 있어 목록 등록은 미뤘습니다. CapCut 을 끈 뒤 "
               "`python -m shorts.capcut --register-only` 를 실행하세요.")
    else:
        msg = register(folder, meta)
    print(f"완료: '{folder.name}' ({total:.2f}초, 샷 {len(shots)}개, 자막 {len(exp['subs'])}개, "
          f"효과음 {len(sfx)}개{', BGM ' + exp['bgm']['name'] if exp.get('bgm') else ''}) — {msg}")
    return folder


def rebuild(job_name, media_name, preset="", name=""):
    """렌더 없이, 이미 만든 재료(jobs/<작업>/capcut/<이름>)로 CapCut 프로젝트만 다시 씀."""
    job = JOBS / job_name
    media_dir = job / "capcut" / media_name
    cfg = load_config()
    if preset:
        p = Path(__file__).resolve().parent.parent / "editor" / "presets" / f"{preset}.json"
        deep_merge(cfg, read_json(p).get("config", {}))
    exp = read_json(media_dir / "export.json")
    shots, voices, sfx = prepare_media(job, exp, media_dir, make=False)
    root = draft_root()
    base = name or media_name
    folder, k = root / base, 2
    while folder.exists():
        folder, k = root / f"{base}_{k}", k + 1
    d = build_draft(exp, cfg, shots, voices, sfx, folder.name, folder)
    meta = d.write()
    if capcut_running():  # export 와 같이 대기 목록에 넣어 --register-only 로 나중에 등록
        pend = read_json(PENDING) if PENDING.exists() else []
        pend.append(folder.as_posix())
        write_json(PENDING, pend)
        msg = "CapCut 이 켜져 있어 목록 등록은 미룸 (끈 뒤 --register-only)"
    else:
        msg = register(folder, meta)
    print(f"완료: '{folder.name}' — " + msg)
    return folder


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser(description="EDL → CapCut 프로젝트")
    ap.add_argument("--job")
    ap.add_argument("--edl")
    ap.add_argument("--preset", default="해외쇼쇼")
    ap.add_argument("--name", default="")
    ap.add_argument("--register-only", action="store_true")
    ap.add_argument("--reuse", default="", help="렌더 없이 jobs/<작업>/capcut/<이름> 재료로 프로젝트만 다시 씀")
    a = ap.parse_args()
    if a.register_only:
        register_pending()
        return
    if a.reuse:
        rebuild(a.job, a.reuse, a.preset, a.name)
        return
    if not (a.job and a.edl):
        ap.error("--job 과 --edl 이 필요합니다")
    export(a.job, a.edl, a.preset, a.name)


if __name__ == "__main__":
    main()
