"""Vmake 화질 업그레이드 자동화 (QUALITY 모델 4K → 4K 월 한도 초과 시 8K).

사용법:
  python tools/vmake_upscale.py --video jobs/<작업명>/final.mp4 --out jobs/<작업명>/final_4k.mp4
  (--res 8k 로 강제 8K, --dry 는 다운로드 버튼 누르기 직전에 멈춤)

- 로그인은 전용 크롬 프로필(.vmake_chrome)에 저장된다. 크롬은 평소 화면 밖에 떠서 백그라운드로 일하고,
  로그인이 필요할 때만 창이 화면에 나타난다 (--login 으로 직접 꺼낼 수도 있음).
- 결제/업그레이드 창이 뜨면 아무것도 누르지 않고 멈춘다.
"""
import argparse, json, re, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / ".vmake_profile"
CHROME_PROFILE = ROOT / ".vmake_chrome"  # 일반 크롬 프로필 (로그인 유지)
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9333
EDITOR = "https://vmake.ai/video-enhancer/editor"
PAY_WORDS = ["Upgrade to", "Subscribe", "Buy credits", "Purchase", "Payment", "결제", "구매"]
LIMIT_WORDS = ["limit", "Limit", "한도", "used up", "run out", "exceeded"]


HIDE_POS = (-32000, -32000)  # 화면 밖 (백그라운드 작업용)
SHOW_POS = (80, 40)


def launch(p, downloads=False, visible=False):
    """진짜 크롬(자동화 표시 없음)을 전용 프로필로 띄우고 원격 연결한다.
    구글 로그인은 자동화 크롬을 막기 때문에 이 방식이어야 로그인이 되고, 프로필에 계속 남는다.
    평소에는 창을 화면 밖에 띄워 백그라운드로 일하고, 로그인이 필요할 때만 show_window()로 꺼낸다."""
    url = f"http://127.0.0.1:{CDP_PORT}"
    try:
        br = p.chromium.connect_over_cdp(url, timeout=3000)
    except Exception:
        CHROME_PROFILE.mkdir(exist_ok=True)
        x, y = SHOW_POS if visible else HIDE_POS
        subprocess.Popen([CHROME, f"--user-data-dir={CHROME_PROFILE}", f"--remote-debugging-port={CDP_PORT}",
                          "--no-first-run", "--no-default-browser-check", "--window-size=1400,950",
                          f"--window-position={x},{y}",
                          # 화면 밖 창이라도 느려지지 않게 (가려진 창 절전 끄기)
                          "--disable-features=CalculateNativeWinOcclusion",
                          "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                          "--disable-background-timer-throttling",
                          "--lang=en-US", "--accept-lang=en-US,en",  # 버튼 글자를 영어로 고정 (스크립트가 영어 이름으로 찾음)
                          EDITOR])
        br = None
        for _ in range(30):
            time.sleep(1)
            try:
                br = p.chromium.connect_over_cdp(url, timeout=3000); break
            except Exception:
                pass
        if br is None:
            print("FAIL: 크롬 연결 실패"); sys.exit(7)
        # 방금 새로 켠 크롬만: '이전 세션 이어서 열기'로 되살아난 옛 탭이 쌓이지 않게 마지막 탭 하나만 남긴다.
        # (이미 켜져 있던 공용 크롬에 붙을 때는 다른 세션 탭일 수 있으니 건드리지 않음)
        time.sleep(2)
        for pg in br.contexts[0].pages[:-1]:
            try:
                pg.close(run_before_unload=False)
            except Exception:
                pass
    ctx = br.contexts[0]
    return ctx


def move_window(ctx, page, show=True):
    """크롬 창을 화면 안(로그인 부탁할 때) 또는 화면 밖(백그라운드)으로 옮긴다."""
    try:
        cdp = ctx.new_cdp_session(page)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]
        x, y = SHOW_POS if show else HIDE_POS
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"left": x, "top": y}})
        if show:
            page.bring_to_front()
    except Exception as e:
        print("창 이동 실패:", e)


def show_window(ctx, page):
    move_window(ctx, page, True)


def hide_window(ctx, page):
    move_window(ctx, page, False)


def save_state(ctx):
    return True


def probe_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def shot(page, dbg, name):
    try:
        page.screenshot(path=str(dbg / f"{name}.png"))
    except Exception:
        pass


def logged_in(page):
    try:
        loc = page.get_by_text("Log in", exact=True)
        return not any(loc.nth(i).is_visible() for i in range(loc.count()))
    except Exception:
        return False


def click_text(page, text, timeout=8000):
    loc = page.get_by_text(text, exact=True).last
    loc.wait_for(state="visible", timeout=timeout)
    loc.click()


def body_text(page):
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def close_promo(page):
    """광고 팝업(ant-modal)이 버튼을 가리면 닫는다. 결제·업그레이드 버튼은 누르지 않는다."""
    for _ in range(3):
        try:
            wrap = page.locator(".ant-modal-wrap:visible")
            if not wrap.count():
                return
            btn = page.locator(".ant-modal-wrap:visible .ant-modal-close, .ant-modal-wrap:visible [class*=close]").first
            if btn.count():
                btn.click(timeout=3000, force=True)
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="로그인 전용: 창에서 로그인 후 창을 닫으면 저장됨")
    ap.add_argument("--video")
    ap.add_argument("--out")
    ap.add_argument("--res", default="4k", choices=["4k", "8k"])
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--fetch-label", help="이미 변환한 카드(업로드 파일 이름)에서 다운로드만 다시 받기")
    ap.add_argument("--timeout-min", type=float, default=30)
    a = ap.parse_args()
    if a.login:
        with sync_playwright() as p:
            ctx = launch(p, visible=True)
            pgs = [pg for pg in ctx.pages if "vmake.ai" in pg.url]
            pg = pgs[0] if pgs else ctx.new_page()
            if not pgs:
                pg.goto(EDITOR)
            show_window(ctx, pg)
        print("LOGIN_SAVED: 창에서 로그인해 주세요 (다음 작업 때 창은 다시 화면 밖으로 숨겨짐)"); return
    video = Path(a.video).resolve()
    out = Path(a.out).resolve()
    dbg = out.parent / "vmake_debug"
    dbg.mkdir(exist_ok=True)

    with sync_playwright() as p:
        ctx = launch(p, downloads=True)
        page = ctx.new_page()
        page.goto(EDITOR, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        close_promo(page)
        # 쿠키: 꼭 필요한 것만
        try:
            if page.get_by_text("Manage Cookies").count():
                page.get_by_text("Manage Cookies").click()
                page.get_by_text("Save and close").click(timeout=5000)
        except Exception:
            pass

        if not logged_in(page):
            print("LOGIN_NEEDED: 열린 크롬 창에서 Vmake에 로그인해 주세요 (최대 10분 대기)", flush=True)
            show_window(ctx, page)
            t0 = time.time()
            while time.time() - t0 < 600:
                page.wait_for_timeout(3000)
                if logged_in(page):
                    break
            else:
                shot(page, dbg, "login_timeout"); print("FAIL: 로그인 대기 시간 초과"); sys.exit(2)
            hide_window(ctx, page)
            page.goto(EDITOR, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
        print("로그인 확인", flush=True)

        # 계정에 예전 작업 카드가 여러 개 쌓여 있으므로, 이번 업로드만 찾도록 고유한 파일 이름으로 올린다
        import shutil, tempfile
        if a.fetch_label:
            uname = a.fetch_label
        else:
            uname = f"{out.parent.name}_{time.strftime('%m%d%H%M%S')}.mp4"
            tmp = Path(tempfile.gettempdir()) / uname
            shutil.copyfile(video, tmp)
            page.locator("input[type=file]").first.set_input_files(str(tmp))
            print("업로드 시작:", uname, flush=True)
        label = page.locator(f"xpath=//*[normalize-space(text())='{uname}']").first
        label.wait_for(state="attached", timeout=120000)
        # 이 영상 카드 = 파일 이름을 감싸는 가장 가까운 task-card (다른 영상 카드로 번지지 않음)
        card = label.locator("xpath=ancestor::div[contains(concat(' ', @class), ' task-card--')][1]")
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(3000)
        close_promo(page)
        shot(page, dbg, "01_uploaded")

        res_label = "4K" if a.res == "4k" else "8K"
        if a.fetch_label:
            print("다운로드만 다시 받기:", uname, flush=True)
        else:
            card.get_by_text("Quality", exact=True).first.click(timeout=60000)
            page.wait_for_timeout(1500)
            res_label = "4K" if a.res == "4k" else "8K"
            try:
                card.get_by_text(res_label, exact=True).first.click(timeout=8000)
            except PWTimeout:
                shot(page, dbg, "02_no_res"); print(f"FAIL: {res_label} 버튼을 못 찾음"); sys.exit(3)
            page.wait_for_timeout(1500)
            shot(page, dbg, "02_settings")
            txt = body_text(page)
            print("설정 완료: Quality /", res_label, flush=True)
            if a.dry:
                print("DRY: 다운로드 직전에 멈춤"); page.wait_for_timeout(2000); page.close(); return

            apply_btn = card.get_by_text("Apply", exact=True).first
            try:
                info = apply_btn.locator("xpath=..").inner_text().replace("\n", " / ")
                print("버튼 안내:", info, flush=True)
            except Exception:
                info = ""
            if re.search(r"upgrade your vmake plan", info, re.I):
                # 무료 횟수를 다 썼다. Apply 를 누르면 30분을 기다려도 5초 미리보기만 나온다.
                shot(page, dbg, "paid_wall")
                print("PAID_WALL: 무료 횟수 소진 — 결제 없이는 변환 불가. 원본 화질(final.mp4)로 올리세요.", flush=True)
                sys.exit(9)
            m = re.search(r"remaining free uses:\s*(\d+)", info, re.I)
            if res_label == "4K" and m and int(m.group(1)) == 0:
                print("LIMIT_4K: 이번 달 4K 무료 횟수 0 → 8K로 전환", flush=True)
                card.get_by_text("8K", exact=True).first.click(timeout=8000)
                res_label = "8K"
                page.wait_for_timeout(1500)
                print("버튼 안내(8K):", apply_btn.locator("xpath=..").inner_text().replace("\n", " / "), flush=True)
            apply_btn.click()
            print("변환 시작 (Apply 클릭)", flush=True)
            page.wait_for_timeout(5000)
            shot(page, dbg, "03_after_apply")
            txt = body_text(page)
            if "Log in to enjoy" in txt:
                print("LOGIN_NEEDED: 로그인이 안 된 상태 → --login 으로 먼저 로그인"); show_window(ctx, page); sys.exit(6)
            if any(w in txt for w in PAY_WORDS + LIMIT_WORDS) and "Processing" not in txt and "%" not in txt:
                print("PAYWALL_OR_LIMIT: 결제/한도 화면으로 보여 멈춤 (03_after_apply.png 확인)")
                if res_label == "4K":
                    print("LIMIT_4K: --res 8k 로 다시 실행 가능")
                page.close(); sys.exit(4)

        deadline = time.time() + a.timeout_min * 60
        n = 0
        while time.time() < deadline:
            page.wait_for_timeout(10000)
            n += 1
            # 변환이 끝나면 나타나는 Download 버튼만 누른다 (Apply는 다시 누르지 않음)
            dl_btn = card.get_by_text("Download", exact=True)
            vis = [dl_btn.nth(i) for i in range(dl_btn.count()) if dl_btn.nth(i).is_visible()]
            if vis:
                try:
                    with page.expect_download(timeout=60000) as dl:
                        vis[-1].click()
                    dl.value.save_as(str(out))
                    save_state(ctx)
                    d_src, d_out = probe_dur(video), probe_dur(out)
                    if abs(d_src - d_out) > 0.3:
                        bad = out.with_suffix(".wrong.mp4"); out.replace(bad)
                        print(f"FAIL: 받은 영상 길이 {d_out:.2f}초 ≠ 원본 {d_src:.2f}초 (다른 영상) → {bad.name}")
                        page.close(); sys.exit(8)
                    print("DONE:", out, round(out.stat().st_size / 1024 / 1024, 1), "MB", flush=True)
                    page.close(); return
                except PWTimeout:
                    shot(page, dbg, f"dl_click_{n:03d}")
                    print("Download 눌렀지만 파일이 안 옴, 화면 확인 필요", flush=True)
            if n % 6 == 0:
                shot(page, dbg, f"wait_{n:03d}")
                print(f"..변환 대기 중 {n // 6}분", flush=True)
        shot(page, dbg, "timeout"); print("FAIL: 시간 초과"); page.close(); sys.exit(5)


if __name__ == "__main__":
    main()
