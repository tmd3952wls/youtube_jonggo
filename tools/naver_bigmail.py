"""네이버 메일 (받는 사람 = 나) + 대용량 첨부 자동 발송 (4K 영상처럼 SMTP로 못 보내는 큰 파일용).

Vmake 와 같은 전용 크롬(.vmake_chrome, 원격 포트 9333)에 네이버 로그인이 유지된 상태에서 동작한다.
비밀번호는 절대 입력하지 않는다. 로그인이 풀려 있으면 LOGIN_NEEDED 를 출력하고 멈춘다.

python tools/naver_bigmail.py --video "C:/Users/.../타지마할보물_4K.mp4" --name 타지마할보물
  - 본문/제목: send_mail.py 와 같음 (Downloads\\chatgpt 사진\\<이름>_업로드정보 / _고정댓글 txt)
  - --dry : 보내기 버튼 누르기 직전에 멈춤
"""
import argparse, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vmake_upscale as v  # launch() 재사용 (같은 크롬 프로필)

ROOT = Path(__file__).resolve().parent.parent
TXT_DIR = Path.home() / "Downloads" / "chatgpt 사진"
WRITE = "https://mail.naver.com/v2/new"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--prefix", default="[1분냠냠]")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--timeout-min", type=float, default=30)
    a = ap.parse_args()

    video = Path(a.video)
    if not video.is_absolute() and not video.exists():
        video = TXT_DIR / a.video
    video = video.resolve()
    info = (TXT_DIR / f"{a.name}_업로드정보(제목·설명·태그).txt").read_text(encoding="utf-8").strip()
    pinned = (TXT_DIR / f"{a.name}_고정댓글.txt").read_text(encoding="utf-8").strip()
    title = next((l.split(":", 1)[1].strip() for l in info.splitlines() if l.startswith("제목:")), a.name)
    subject = f"{a.prefix} {title}"
    body = ("■ 업로드 정보 (제목·설명·태그)\n" + "─" * 30 + "\n" + info
            + "\n\n\n■ 고정댓글\n" + "─" * 30 + "\n" + pinned + "\n")
    dbg = ROOT / "jobs" / "_mail_debug"
    dbg.mkdir(exist_ok=True)

    with sync_playwright() as p:
        ctx = v.launch(p)
        # 다른 세션이 같은 크롬을 함께 쓸 수 있으므로 남의 탭은 건드리지 않고 내 탭만 새로 연다
        pg = ctx.new_page()
        pg.goto(WRITE, wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        if "nid.naver.com" in pg.url:
            print("LOGIN_NEEDED: 화면에 뜬 크롬 창에서 네이버에 로그인해 주세요 (최대 10분 대기)", flush=True)
            v.show_window(ctx, pg)
            t0 = time.time()
            while "nid.naver.com" in pg.url and time.time() - t0 < 600:
                pg.wait_for_timeout(3000)
            if "nid.naver.com" in pg.url:
                print("FAIL: 로그인 대기 시간 초과"); sys.exit(6)
            v.hide_window(ctx, pg)
            pg.goto(WRITE, wait_until="domcontentloaded"); pg.wait_for_timeout(5000)

        # 받는 사람 = 나 (send_mail.py 처럼 받은메일함으로 오게, 주소는 .env 의 아이디)
        import send_mail
        me = send_mail.env("NAVER_SMTP_USER").split("@")[0] + "@naver.com"
        rc = pg.locator("#recipient_input_element")
        rc.click(); rc.fill(me); rc.press("Enter")
        pg.wait_for_timeout(800)
        pg.locator("#subject_title").fill(subject)

        # 본문: 에디터(프레임 안 편집 영역)에 그대로 입력
        ed = None
        for fr in pg.frames:
            loc = fr.locator("div.workseditor-content[contenteditable=true]")
            if loc.count():
                ed = loc.first; break
        ed.click()
        pg.keyboard.insert_text(body)
        pg.wait_for_timeout(500)
        print("제목·본문 입력:", subject, flush=True)

        # 첨부 (10MB 넘으면 네이버가 자동으로 대용량 첨부로 올림)
        # 50MB 넘는 파일은 playwright 전송이 막혀서, 크롬에 로컬 경로를 직접 지정 (같은 PC라 가능)
        cdp = ctx.new_cdp_session(pg)
        root = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
        node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": "#ATTACH_LOCAL_FILE_ELEMENT_ID"})["nodeId"]
        cdp.send("DOM.setFileInputFiles", {"nodeId": node, "files": [str(video)]})
        print(f"첨부 업로드 시작: {video.name} ({video.stat().st_size / 1e6:.0f}MB)", flush=True)
        pg.wait_for_timeout(3000)
        for _ in range(3):  # 대용량 전환 안내창이 뜨면 확인
            try:
                pg.get_by_role("button", name="확인").filter(visible=True).first.click(timeout=2000)
                pg.wait_for_timeout(1500)
            except Exception:
                break
        pg.screenshot(path=str(dbg / "01_attach.png"))

        size_txt = f"{video.stat().st_size / 1048576:.2f}MB"  # 네이버 표시 형식 (예: 325.30MB)
        deadline = time.time() + a.timeout_min * 60
        n = 0
        while time.time() < deadline:
            pg.wait_for_timeout(5000); n += 1
            # 첨부 영역 글자만 본다 (한글 파일명은 화면에서 자모가 분리돼 비교가 안 되므로 용량으로 확인)
            txt = pg.inner_text("body")
            area = txt.split("파일첨부", 1)[-1].split("굴림", 1)[0]
            busy = "%" in area or any(w in area for w in ["업로드 중", "업로드중", "전송 중", "남은 시간"])
            if size_txt in area and area.count(size_txt) >= 2 and not busy:
                break
            if n % 12 == 0:
                print(f"..업로드 중 {n // 12}분", flush=True); pg.screenshot(path=str(dbg / f"up_{n:03d}.png"))
        else:
            pg.screenshot(path=str(dbg / "timeout.png")); print("FAIL: 업로드 시간 초과"); sys.exit(5)
        pg.wait_for_timeout(3000)
        pg.screenshot(path=str(dbg / "02_ready.png"))
        print("첨부 업로드 완료", flush=True)
        if a.dry:
            print("DRY: 보내기 직전에 멈춤 (창은 그대로 둠)"); return

        pg.locator("button.button_write_task", has_text="보내기").first.click()
        # 보내기 클릭 후 '첨부파일 업로드' 모달 진행(100%) → 발송 완료 화면까지 폴링 대기
        send_deadline = time.time() + a.timeout_min * 60
        k = 0
        while time.time() < send_deadline:
            pg.wait_for_timeout(3000)
            k += 1
            txt = pg.inner_text("body")
            if "보냈습니다" in txt or pg.url.endswith("/done"):
                shot_p = dbg / "03_sent.png"
                pg.screenshot(path=str(shot_p))
                print(f"SENT: {me} ← '{subject}' (대용량 첨부 {video.name})")
                pg.close(); return
            if k % 5 == 0:
                pg.screenshot(path=str(dbg / f"sending_{k:03d}.png"))
                print(f"..대용량 전송 중 ({k * 3}초 경과)", flush=True)
        pg.screenshot(path=str(dbg / "03_sent.png"))
        print("CHECK: 보낸 결과를 확인 못 함 → jobs/_mail_debug/03_sent.png 확인"); sys.exit(9)


if __name__ == "__main__":
    main()
