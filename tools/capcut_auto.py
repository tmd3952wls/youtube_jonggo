"""CapCut '활기찬 바리톤' 1.1배속 내레이션 완전 자동 생성 도구.

사용법:
  python tools/capcut_auto.py --job <작업명> [--voice vibrant_baritone]

동작 순서:
  1) CapCut 실행 중이면 먼저 안전 종료
  2) capcut_voice.make(job, voice) 호출하여 프로젝트 등록
  3) PC CapCut 실행 → 최대화 → 첫 번째 프로젝트(voice_<job>_<voice>) 열기
  4) 미리보기 일시정지(975, 616) → 타임라인 텍스트 전체 드래그 선택(1650,825 -> 195,760)
  5) '텍스트에서 음성으로'(1555, 54) → 검색창(1536, 92)에 '활기찬 바리톤' 입력
  6) 첫 카드 'Vibrant Baritone'(1383, 206) 선택(3.5초 대기) → '음성 생성'(1862, 670) 클릭
  7) draft_content.json 의 materials.audios 개수가 문장 수에 도달할 때까지 대기 후 CapCut 종료
  8) capcut_voice.harvest(job, voice) 실행하여 assets/voices/<voice>/ 에 1.1배속 wav 저장
"""
import argparse
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from tools import capcut_voice as CV  # noqa: E402

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32

WM_ACTIVATE = 0x0006
WM_SETFOCUS = 0x0007
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001


def _makelparam(lo, hi):
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)


def _resolve_capcut_exe() -> str:
    apps = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "Apps"
    cands = []
    if apps.exists():
        for d in apps.iterdir():
            if d.is_dir() and (d / "CapCut.exe").exists():
                parts = [int(x) for x in d.name.split(".") if x.isdigit()]
                cands.append((parts, str(d / "CapCut.exe")))
    if cands:
        cands.sort(reverse=True)
        return cands[0][1]
    return str(apps / "CapCut.exe")


def _find_windows():
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w > 40 and h > 30 and ("CapCut" in buf.value or "Qt6" in cls.value):
                found.append((hwnd, buf.value, cls.value, rect.left, rect.top, w, h))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def _target_for_pt(wins, sx, sy):
    main_win = max(wins, key=lambda x: x[5] * x[6])[0]
    cur = main_win
    pt = ctypes.wintypes.POINT(int(sx), int(sy))
    while True:
        cpt = ctypes.wintypes.POINT(pt.x, pt.y)
        user32.ScreenToClient(cur, ctypes.byref(cpt))
        child = user32.ChildWindowFromPointEx(cur, cpt, 0x0003)
        if not child or child == cur:
            return cur, cpt.x, cpt.y
        cur = child


def _click(wins, sx, sy, wait=0.8):
    hwnd, cx, cy = _target_for_pt(wins, sx, sy)
    user32.SetForegroundWindow(hwnd)
    user32.SendMessageW(hwnd, WM_ACTIVATE, 1, 0)
    user32.SendMessageW(hwnd, WM_SETFOCUS, 0, 0)
    lp = _makelparam(cx, cy)
    user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(0.06)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    time.sleep(wait)


def _drag(wins, x1, y1, x2, y2, steps=25, wait=0.8):
    hwnd, cx1, cy1 = _target_for_pt(wins, x1, y1)
    cpt2 = ctypes.wintypes.POINT(int(x2), int(y2))
    user32.ScreenToClient(hwnd, ctypes.byref(cpt2))
    cx2, cy2 = cpt2.x, cpt2.y
    user32.SetForegroundWindow(hwnd)
    user32.SendMessageW(hwnd, WM_ACTIVATE, 1, 0)
    user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, _makelparam(cx1, cy1))
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, _makelparam(cx1, cy1))
    time.sleep(0.05)
    for i in range(1, steps + 1):
        mx = int(cx1 + (cx2 - cx1) * i / steps)
        my = int(cy1 + (cy2 - cy1) * i / steps)
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, MK_LBUTTON, _makelparam(mx, my))
        time.sleep(0.03)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, _makelparam(cx2, cy2))
    time.sleep(wait)


def _type_text(hwnd, text, wait=2.5):
    user32.SetForegroundWindow(hwnd)
    user32.SendMessageW(hwnd, WM_ACTIVATE, 1, 0)
    user32.SendMessageW(hwnd, WM_SETFOCUS, 0, 0)
    for ch in text:
        user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 1)
        time.sleep(0.03)
    time.sleep(0.1)
    user32.PostMessageW(hwnd, WM_KEYDOWN, 0x0D, 0x001C0001)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_KEYUP, 0x0D, 0xC01C0001)
    time.sleep(wait)


def run_auto(job: str, voice: str = "vibrant_baritone"):
    need = CV.missing(job, voice)
    if not need:
        print(f"모든 문장 음성이 이미 캐시되어 있습니다 ({job}, {voice}).")
        return

    subprocess.run(["taskkill", "/F", "/IM", "CapCut.exe"], capture_output=True)
    time.sleep(1.5)

    CV.make(job, voice)
    man_p = ROOT / "jobs" / job / f"capcut_voice_{voice}.json"
    man = json.loads(man_p.read_text(encoding="utf-8"))
    draft_json = Path(man["draft"]) / "draft_content.json"
    want_cnt = len(man["lines"])

    env = os.environ.copy()
    env["QSG_RHI_BACKEND"] = "opengl"
    env["QT_OPENGL"] = "software"
    proc = subprocess.Popen([_resolve_capcut_exe()], env=env)
    print(f"CapCut 실행됨 (PID={proc.pid}), 홈 화면 대기 중...")
    for _ in range(40):
        time.sleep(1.0)
        if any(w[5] > 400 and w[6] > 300 for w in _find_windows()):
            break
    time.sleep(3.5)

    try:
        wins = _find_windows()
        main_win = max(wins, key=lambda x: x[5] * x[6])[0]
        for w in wins:
            if w[0] != main_win:
                user32.PostMessageW(w[0], WM_CLOSE, 0, 0)
        user32.ShowWindow(main_win, 3)
        time.sleep(1.2)

        # 1) 첫 번째 프로젝트 열기 및 편집기 팝업창 닫기
        _click(_find_windows(), 308, 740, wait=7.5)
        wins = _find_windows()
        main_win = max(wins, key=lambda x: x[5] * x[6])[0]
        for w in wins:
            if w[0] != main_win:
                user32.PostMessageW(w[0], WM_CLOSE, 0, 0)
        time.sleep(1.0)
        user32.ShowWindow(main_win, 3)
        time.sleep(1.0)

        # 2) 타임라인 텍스트 전체 드래그 선택
        _drag(_find_windows(), 1650, 825, 195, 760, steps=25, wait=1.0)

        # 3) '텍스트에서 음성으로' 탭 → '활기찬 바리톤' 검색 → 카드 선택 → '음성 생성'
        _click(_find_windows(), 1555, 54, wait=1.8)
        _click(_find_windows(), 1536, 92, wait=0.6)
        _type_text(main_win, "활기찬 바리톤", wait=2.8)
        _click(_find_windows(), 1383, 206, wait=3.5)
        _click(_find_windows(), 1862, 670, wait=3.0)

        # 4) 생성 완료 폴링 (최대 45초)
        for _ in range(45):
            try:
                d = json.loads(draft_json.read_text(encoding="utf-8"))
                cnt = len(d.get("materials", {}).get("audios", []))
                if cnt >= want_cnt:
                    print(f"CapCut 음성 생성 완료: {cnt}/{want_cnt}개")
                    break
            except Exception:
                pass
            time.sleep(1.0)
        time.sleep(1.5)
    finally:
        for w in _find_windows():
            user32.PostMessageW(w[0], WM_CLOSE, 0, 0)
        time.sleep(1.5)
        proc.terminate()

    CV.harvest(job, voice)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--voice", default="vibrant_baritone")
    a = ap.parse_args()
    run_auto(a.job, a.voice)
