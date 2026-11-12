"""네이버 메일 '내게쓰기' 자동 발송 (2026-11-11).

.env 에 NAVER_SMTP_USER(아이디, @naver.com 빼고) / NAVER_SMTP_PASS(애플리케이션 비밀번호) 필요.
받는 사람 = 보내는 사람(본인). 비밀번호는 화면에 절대 출력하지 않음.

python tools/send_mail.py --job cardboard --video full_v9.mp4 --name 페이퍼박스
  - 본문: Downloads\\chatgpt 사진\\<이름>_업로드정보(제목·설명·태그).txt + <이름>_고정댓글.txt 내용
  - 첨부: 영상 파일만 (txt 는 첨부하지 않음)
"""
import argparse
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TXT_DIR = Path.home() / "Downloads" / "chatgpt 사진"


def env(key):
    s = (ROOT / ".env").read_text(encoding="utf-8")
    m = re.search(rf"^{key}=(.*)$", s, re.M)
    v = m.group(1).strip().strip('"').strip("'") if m else ""
    if not v:
        raise SystemExit(f".env 에 {key} 가 비어 있어요")
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--name", required=True, help="txt 파일 앞 한글 이름 (예: 페이퍼박스)")
    ap.add_argument("--prefix", default="[1분냠냠]", help="메일 제목 앞머리")
    a = ap.parse_args()

    user = env("NAVER_SMTP_USER").split("@")[0]
    pw = env("NAVER_SMTP_PASS")
    addr = f"{user}@naver.com"

    info = (TXT_DIR / f"{a.name}_업로드정보(제목·설명·태그).txt").read_text(encoding="utf-8").strip()
    pinned = (TXT_DIR / f"{a.name}_고정댓글.txt").read_text(encoding="utf-8").strip()
    title = next((l.split(":", 1)[1].strip() for l in info.splitlines() if l.startswith("제목:")), a.name)

    video = ROOT / "jobs" / a.job / a.video
    msg = EmailMessage()
    msg["From"] = addr
    msg["To"] = addr
    msg["Subject"] = f"{a.prefix} {title}"
    msg.set_content(
        "■ 업로드 정보 (제목·설명·태그)\n" + "─" * 30 + "\n" + info
        + "\n\n\n■ 고정댓글\n" + "─" * 30 + "\n" + pinned + "\n"
    )
    msg.add_attachment(video.read_bytes(), maintype="video", subtype="mp4", filename=video.name)

    with smtplib.SMTP_SSL("smtp.naver.com", 465, context=ssl.create_default_context(), timeout=300) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"보냄: {addr} ← '{msg['Subject']}' (첨부 {video.name}, {video.stat().st_size / 1e6:.1f}MB)")


if __name__ == "__main__":
    main()
