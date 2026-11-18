"""YouTube Studio '1분냠냠' 채널 비공개(Private) 자동 업로드 도구.

- 전용 크롬 프로필(.yt_chrome, 원격 포트 9336)을 사용하여 Vmake 및 다른 채널 세션과 분리 유지한다.
- 300MB+ 4K 영상도 전송 제한이 없도록 CDP DOM.setFileInputFiles 로 로컬 경로를 직접 지정한다.
- 채널이 여러 개일 경우 목표 채널명(기본: '1분냠냠')을 자동 선택하거나 확인한다.

사용법:
  python -X utf8 tools/youtube_upload.py --video "C:/Users/.../슈퍼맨슈트_4K.mp4" --name 슈퍼맨슈트 --privacy private
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TXT_DIR = Path.home() / "Downloads" / "chatgpt 사진"

STUDIO_HOME = "https://studio.youtube.com"
SWITCHER_URL = "https://www.youtube.com/channel_switcher"

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
YT_CHROME_PROFILE = ROOT / ".yt_chrome"
CDP_PORT = 9336
HIDE_POS = (-32000, -32000)
SHOW_POS = (80, 40)


def parse_upload_txt(txt_path: Path) -> tuple[str, str, list[str]]:
    raw = txt_path.read_text(encoding="utf-8").strip()
    title = ""
    desc_lines: list[str] = []
    tags: list[str] = []
    mode = None
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("제목:"):
            title = s.split(":", 1)[1].strip()
            mode = "title"
        elif s.startswith("설명:"):
            rest = s.split(":", 1)[1].strip()
            if rest:
                desc_lines.append(rest)
            mode = "desc"
        elif s.startswith("태그:"):
            t_raw = s.split(":", 1)[1].strip()
            tags = [x.strip() for x in t_raw.split(",") if x.strip()]
            mode = "tags"
        else:
            if mode == "desc" and s:
                desc_lines.append(s)
    return title[:100], "\n".join(desc_lines).strip(), tags


def launch_or_attach(p, visible: bool = False):
    url = f"http://127.0.0.1:{CDP_PORT}"
    try:
        br = p.chromium.connect_over_cdp(url, timeout=3000)
        return br, br.contexts[0], False
    except Exception:
        profile = YT_CHROME_PROFILE
        profile.mkdir(parents=True, exist_ok=True)
        pref_file = profile / "Default" / "Preferences"
        if pref_file.exists():
            try:
                prefs = json.loads(pref_file.read_text(encoding="utf-8"))
                prefs.setdefault("session", {})["restore_on_startup"] = 1
                pref_file.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        x, y = SHOW_POS if visible else HIDE_POS
        subprocess.Popen([
            CHROME,
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={CDP_PORT}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1400,950",
            f"--window-position={x},{y}",
            "--disable-features=CalculateNativeWinOcclusion",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "about:blank",
        ])
        for _ in range(30):
            time.sleep(1)
            try:
                br = p.chromium.connect_over_cdp(url, timeout=3000)
                return br, br.contexts[0], True
            except Exception:
                pass
        print("FAIL: 크롬 원격 디버깅 연결 실패", flush=True)
        sys.exit(7)


def shot(page, dbg: Path, name: str):
    try:
        page.screenshot(path=str(dbg / f"{name}.png"))
    except Exception:
        pass


def ensure_target_channel_and_upload(page, target_channel: str, dbg: Path) -> str:
    """로그인 확인 → target_channel 선택(필요 시 channel_switcher 경유) → 업로드 모달 열기."""
    page.goto(STUDIO_HOME, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    if "accounts.google.com" in page.url:
        shot(page, dbg, "00_login_needed")
        print("LOGIN_NEEDED: login_youtube.bat 으로 먼저 유튜브 스튜디오에 로그인해 주세요.", flush=True)
        sys.exit(6)

    # 현재 스튜디오 채널 이름 확인
    body_txt = ""
    try:
        body_txt = page.inner_text("body")
    except Exception:
        pass

    if target_channel and target_channel not in body_txt:
        # channel_switcher 에서 목표 채널로 전환 시도
        page.goto(SWITCHER_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        shot(page, dbg, "00_switcher")
        sw_txt = page.inner_text("body")
        if target_channel in sw_txt:
            page.get_by_text(target_channel, exact=True).first.click()
            page.wait_for_timeout(4000)
            page.goto(STUDIO_HOME, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            body_txt = page.inner_text("body")
        else:
            print(f"CHANNEL_MISMATCH: 로그인된 계정에 '{target_channel}' 채널이 보이지 않습니다.\n현재 목록:\n{sw_txt[:600]}", flush=True)
            sys.exit(8)

    m = re.search(r"studio\.youtube\.com/channel/([A-Za-z0-9_-]+)", page.url)
    ch_id = m.group(1) if m else ""
    if ch_id:
        page.goto(f"https://studio.youtube.com/channel/{ch_id}/videos/upload?d=ud", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

    if page.locator("input[type=file]").count() == 0:
        for sel in ["ytcp-button#create-icon", "#create-icon", "#upload-icon", "ytcp-icon-button#upload-icon"]:
            if page.locator(sel).count():
                try:
                    page.locator(sel).first.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    break
                except Exception:
                    pass
        for txt in ["동영상 업로드", "Upload videos", "Upload video"]:
            loc = page.get_by_text(txt, exact=False)
            if loc.count():
                try:
                    loc.first.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    break
                except Exception:
                    pass
    return ch_id


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="업로드할 mp4 경로")
    ap.add_argument("--name", required=True, help="업로드정보 txt 앞 한글 이름 (예: 슈퍼맨슈트)")
    ap.add_argument("--channel", default="1분냠냠", help="업로드할 목표 채널 이름")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--dry", action="store_true", help="저장 직전에 멈춤")
    ap.add_argument("--timeout-min", type=float, default=30)
    a = ap.parse_args()

    video = Path(a.video)
    if not video.is_absolute() and not video.exists():
        video = TXT_DIR / a.video
    video = video.resolve()
    if not video.exists():
        raise SystemExit(f"영상 파일이 없습니다: {video}")

    info_path = TXT_DIR / f"{a.name}_업로드정보(제목·설명·태그).txt"
    title, desc, tags = parse_upload_txt(info_path)
    if not title:
        raise SystemExit(f"제목을 찾지 못했습니다: {info_path}")

    dbg = ROOT / "jobs" / "_yt_upload_debug" / a.name
    dbg.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        br, ctx, spawned = launch_or_attach(p)
        page = ctx.new_page()
        try:
            ch_id = ensure_target_channel_and_upload(page, a.channel, dbg)
            page.locator("input[type=file]").first.wait_for(state="attached", timeout=30000)
            shot(page, dbg, "01_studio_ready")
            print(f"스튜디오 준비 완료: 채널='{a.channel}' (ID={ch_id})", flush=True)

            cdp = ctx.new_cdp_session(page)
            doc = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
            fnode = cdp.send("DOM.querySelector", {"nodeId": doc, "selector": "input[type=file]"})["nodeId"]
            cdp.send("DOM.setFileInputFiles", {"nodeId": fnode, "files": [str(video)]})
            print(f"파일 선택 완료: {video.name} ({video.stat().st_size / 1e6:.1f} MB)", flush=True)

            title_box = page.locator("ytcp-social-suggestions-textbox#title-textarea div#textbox").first
            title_box.wait_for(state="visible", timeout=60000)
            page.wait_for_timeout(2000)
            shot(page, dbg, "02_form_opened")

            # 1. Title
            title_box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.insert_text(title)
            page.wait_for_timeout(600)

            # 2. Description
            desc_box = page.locator("ytcp-social-suggestions-textbox#description-textarea div#textbox").first
            desc_box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.insert_text(desc)
            page.wait_for_timeout(600)

            # 3. Not made for kids
            mfk = page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').first
            mfk.scroll_into_view_if_needed()
            mfk.click()
            page.wait_for_timeout(500)

            # 4. Show more -> Altered content = No + Tags
            toggle = page.locator("ytcp-button#toggle-button").first
            if toggle.is_visible():
                toggle.scroll_into_view_if_needed()
                toggle.click()
                page.wait_for_timeout(1000)

            alt_no = page.locator('tp-yt-paper-radio-button[name="VIDEO_HAS_ALTERED_CONTENT_NO"]')
            if alt_no.count() and alt_no.first.is_visible():
                alt_no.first.scroll_into_view_if_needed()
                alt_no.first.click()
                page.wait_for_timeout(400)

            if tags:
                tag_input = page.locator("ytcp-form-input-container#tags-container input#text-input").first
                if tag_input.count():
                    tag_input.scroll_into_view_if_needed()
                    tag_input.click()
                    page.keyboard.insert_text(", ".join(tags) + ",")
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(600)

            shot(page, dbg, "03_details_filled")
            print(f"메타데이터 입력 완료: '{title}' (태그 {len(tags)}개)", flush=True)

            video_url = ""
            link_loc = page.locator('a[href*="youtu.be"], a[href*="youtube.com/shorts/"], a.style-scope.ytcp-video-info')
            if link_loc.count():
                video_url = link_loc.first.get_attribute("href") or ""

            # 5. Click Next x3 to reach Visibility step
            for _ in range(3):
                nxt = page.locator("ytcp-button#next-button").first
                nxt.wait_for(state="visible", timeout=15000)
                nxt.click()
                page.wait_for_timeout(1200)

            # 6. Select Privacy radio button
            priv_map = {"private": "PRIVATE", "unlisted": "UNLISTED", "public": "PUBLIC"}
            priv_radio = page.locator(f'tp-yt-paper-radio-button[name="{priv_map[a.privacy]}"]').first
            priv_radio.wait_for(state="visible", timeout=15000)
            priv_radio.click()
            page.wait_for_timeout(800)
            shot(page, dbg, "04_visibility_selected")

            if not video_url and link_loc.count():
                video_url = link_loc.first.get_attribute("href") or ""

            # 7. Wait until raw file upload finishes
            deadline = time.time() + a.timeout_min * 60
            tick = 0
            while time.time() < deadline:
                prog_txt = ""
                prog_loc = page.locator("ytcp-video-upload-progress")
                if prog_loc.count():
                    try:
                        prog_txt = prog_loc.first.inner_text()
                    except Exception:
                        pass
                still_uploading = bool(re.search(r"(\d+%\s*(uploaded|업로드)|(?:Uploading|업로드 중)\s*\d+%)", prog_txt, re.I))
                done_btn = page.locator("ytcp-button#done-button").first
                disabled = done_btn.get_attribute("disabled") is not None
                if not still_uploading and not disabled:
                    break
                tick += 1
                if tick % 3 == 0:
                    print(f"  ..유튜브 파일 전송 대기 ({prog_txt.strip() or '...'})", flush=True)
                    shot(page, dbg, f"up_{tick:03d}")
                page.wait_for_timeout(4000)
            else:
                shot(page, dbg, "timeout_upload")
                print("FAIL: 유튜브 파일 업로드 대기 시간 초과", flush=True)
                sys.exit(5)

            if not video_url and link_loc.count():
                video_url = link_loc.first.get_attribute("href") or ""

            if a.dry:
                print(f"DRY: 저장 직전에 멈춤 (URL={video_url})", flush=True)
                return

            page.locator("ytcp-button#done-button").first.click()
            # 혹시 저장 후에도 '동영상 업로드 중' 대화상자가 떠 있으면 완전히 끝날 때까지 대기
            for wait_k in range(90):
                page.wait_for_timeout(3000)
                body_now = ""
                try:
                    body_now = page.inner_text("body")
                except Exception:
                    pass
                if "동영상 업로드 중" not in body_now and not re.search(r"\d+%\s*업로드", body_now):
                    break
                if wait_k % 5 == 0:
                    print("  ..저장 후 마무리 전송 대기 중", flush=True)
            page.wait_for_timeout(3000)
            shot(page, dbg, "05_saved")

            for close_sel in ["ytcp-button#close-button", "ytcp-icon-button#close-button"]:
                if page.locator(close_sel).count() and page.locator(close_sel).first.is_visible():
                    try:
                        page.locator(close_sel).first.click(timeout=2000)
                        break
                    except Exception:
                        pass

            print(f"UPLOAD_DONE: [{a.privacy.upper()}] {title} -> {video_url}", flush=True)
        finally:
            if not a.dry:
                try:
                    page.close()
                except Exception:
                    pass
                if spawned:
                    try:
                        br.new_browser_cdp_session().send("Browser.close")
                    except Exception:
                        pass


if __name__ == "__main__":
    main()
