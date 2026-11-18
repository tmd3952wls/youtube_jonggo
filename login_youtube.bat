@echo off
chcp 65001 >nul
if not exist "C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\.yt_chrome" mkdir "C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\.yt_chrome"
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\.yt_chrome" --no-first-run --no-default-browser-check "https://studio.youtube.com"
