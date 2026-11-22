"""유튜브 스튜디오에서 내 채널의 영상을 완전삭제한다 (되돌릴 수 없음 — 사용자가 지우라고 한 영상만).

사용법:  python tools/youtube_delete.py --ids <영상ID> [<영상ID> ...] --expect "제목에 들어 있어야 할 글자"

- youtube_upload.py 와 같은 1분냠냠 전용 크롬(.yt_chrome)을 쓴다.
- 영상 수정 화면의 제목에 --expect 글자가 없으면 지우지 않는다(엉뚱한 영상 보호).
- 단계마다 화면을 jobs/_yt_upload_debug/ 에 남긴다.
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from tools import youtube_upload as YU  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--expect", required=True)
    ap.add_argument("--channel", default="1분냠냠")
    ap.add_argument("--dry", action="store_true", help="삭제 확인 창까지만 열고 취소")
    a = ap.parse_args()
    dbg = ROOT / "jobs" / "_yt_upload_debug"
    dbg.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        br, ctx, _ = YU.launch_or_attach(p)
        page = ctx.new_page()
        page.goto(YU.STUDIO_HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        if "accounts.google.com" in page.url:
            print("LOGIN_NEEDED"); sys.exit(6)
        if a.channel not in page.inner_text("body"):
            print(f"CHANNEL_MISMATCH: 스튜디오에 '{a.channel}' 이 보이지 않음 → 중단"); sys.exit(8)
        for vid in a.ids:
            page.goto(f"https://studio.youtube.com/video/{vid}/edit", wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            try:
                title = page.locator("#title-textarea #textbox, ytcp-social-suggestions-textbox#title-textarea #textbox").first.inner_text(timeout=8000).strip()
            except Exception:
                title = ""
            if a.expect not in title:
                YU.shot(page, dbg, f"del_{vid}_title_mismatch")
                print(f"SKIP {vid}: 제목 '{title}' 에 '{a.expect}' 없음 → 지우지 않음"); continue
            # ⋮ 옵션 메뉴 → 삭제
            page.locator("ytcp-icon-button#overflow-menu-button, #overflow-menu-button").first.click(timeout=8000)
            page.wait_for_timeout(1200)
            item = page.locator("tp-yt-paper-item").filter(has_text="삭제")
            if not item.count():
                item = page.locator("tp-yt-paper-item").filter(has_text="Delete")
            item.first.click(timeout=8000)
            page.wait_for_timeout(2000)
            dlg = page.locator("ytcp-video-delete-dialog, ytcp-confirmation-dialog, tp-yt-paper-dialog:visible").first
            dlg.locator("#checkbox, ytcp-checkbox-lit, tp-yt-paper-checkbox").first.click(timeout=8000)
            page.wait_for_timeout(800)
            YU.shot(page, dbg, f"del_{vid}_confirm")
            if a.dry:
                page.keyboard.press("Escape"); print(f"DRY {vid}: '{title}' 확인 창까지 열림"); continue
            dlg.locator("#confirm-button, ytcp-button#confirm-button").first.click(timeout=8000)
            page.wait_for_timeout(5000)
            YU.shot(page, dbg, f"del_{vid}_after")
            # 확인: 수정 화면이 더 이상 열리지 않아야 함
            page.goto(f"https://studio.youtube.com/video/{vid}/edit", wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            gone = page.locator("#title-textarea #textbox").count() == 0 or f"/video/{vid}/edit" not in page.url
            YU.shot(page, dbg, f"del_{vid}_verify")
            print(f"{'DELETED' if gone else 'NOT_CONFIRMED'} {vid}: '{title}'", flush=True)
        page.close()


if __name__ == "__main__":
    main()
