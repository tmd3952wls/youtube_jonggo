---
name: pawn-stars-shorts
description: >-
  End-to-end zero-revision workflow for creating '전당포 사나이들 (Pawn Stars / 1분냠냠)'
  Korean YouTube Shorts in C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT.
  Activate whenever the user asks to create, edit, re-render, verify, or mail a Pawn Stars
  or youtube_jonggo short (e.g., "영상 하나 더 만들어", "전당포 쇼츠 만들어").
---

# 전당포 사나이들 쇼츠 무수정 원샷 제작 런북 (1분냠냠)

사용자가 별도로 세부 지시를 하지 않아도 **첫 렌더링에서 바로 수정 없이 완성본 품질**이 나오도록 아래 순서와 기준을 100% 준수한다.

## 0. 소통 및 사전 체크
- 모든 안내는 **쉬운 한국어**로만 작성한다 (영어 기술 용어 남발 금지).
- 사용자에게 선택지를 되묻지 말고, 조회수 높은 레전드 에피소드를 직접 선정해 처음부터 네이버 메일 발송·깃허브 푸시까지 끝낸다.
- 작업 전 `C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\AGENTS.md`의 **완료된 작업 목록**(`jobs/` 폴더)을 확인해 절대 주제가 겹치지 않게 한다.

## 1. 원본 확보 및 보컬·발음 정밀 전처리
작업 폴더 `60SEC_YOUTUBE_AUTO_SHORT/jobs/<영문작업명>/` 생성 후 아래 5단계를 순서대로 실행한다:
1. `yt-dlp --download-sections "*시작-끝" -f "bv*[height<=1080]+ba/b"`로 해당 아이템 구간만 받아 `shorts.source.fetch`로 30fps `source.mp4` 생성.
2. `shorts.source.transcribe` → `transcript.json` 생성.
3. `shorts.scenes.detect_cuts` → `cuts.json` 생성.
4. `shorts.align.ensure_words` → `transcript_words.json` 어절 타임스탬프 생성.
5. `shorts.render.ensure_vocals` → Demucs로 방송 배경음악을 제거한 `source_vocals.wav` 생성.
6. **필수 사전 점검**: 사용할 대사 줄(`id`)의 `source_vocals.wav` 음량(RMS)과 단어 간격을 먼저 확인한다.
   - 미국 방송 욕설 **삐처리(Bleep)** 구간은 Demucs에서 무음(`-40dB` 이하)이 되므로 `drop_end_words` / `skip_words`로 잘라낸다.
   - 출연자가 3~5초 침묵한 줄에서 어절 타임스탬프가 무음 구간에 잘못 찍힌 경우 실제 발화 구간(`-32dB` 이상)으로 보정한다.
   - B-roll(`broll_t`, `broll_id`)은 방송 정보 팝업창이 없는 깨끗한 클로즈업 샷을 고르고, 내레이션 길이 동안 카메라 컷이 튀지 않는 긴 샷 구간을 지정한다.

## 2. 대본(`plan.json`) 황금 규칙
- **총 영상 길이**: 반드시 **`45.0 ~ 57.0초`** (권장 타깃 **`50 ~ 53초`**).
- **찰진 구어체 티키타카**: 딱딱한 번역투 대신 `"14억 원 내놔요!"`, `"2,100만 원 쏴요!"`, `"어림도 없죠!"`, `"아 피 말리시네요!"`처럼 상황에 찰떡같이 붙는 입말로 맛깔나게 살린다.
- **큰 강조 자막(`{big}` / `[big:색상:...]`, `98px`) 1줄 절대 원칙**:
  - 강조 자막이 2줄로 꺾이면 안 되므로 화자 태그 제외 **공백 포함 8~9자 이내**로 짧고 강렬하게 쓴다 (예: `{big} @손님 [yellow:14억 원] 내놔요!`, `{big} @릭 [red:어림도 없죠!]`).
- **정확한 원화 표기**: 흥정 접전 구간의 금액 차이가 뭉개지지 않게 정확한 원화(`2,380만 원`, `2,240만 원`, `2,170만 원` 등)를 직접 표기한다.
- **효과음(`assets/sfx_pack/`)**:
  - 한 영상 안에서 **같은 효과음 파일 중복 사용 절대 금지** (전부 다른 파일 지정).
  - **마지막 마무리 내레이션 세그먼트는 반드시 `"sfx": null`**.
- **사실 기반 고정댓글(`pinned_comment`)**: 웹 검색으로 확인된 실제 방송 정보·경매 기록만 바탕으로 25~35줄 구성으로 풍성하게 작성한다 (`research.json` 함께 저장).

## 3. 캡컷 `'활기찬 바리톤'` 1.1배속 자동 생성
내레이션은 무조건 캡컷 `'활기찬 바리톤'` 1.1배속만 사용한다:
```powershell
$env:PYTHONUTF8 = "1"
python "C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\tools\capcut_auto.py" --job <작업명> --voice vibrant_baritone
```
실행 후 `남은 문장 없음` 출력을 확인한다.

## 4. 렌더링 및 8대 품질 게이트 + 시각 검수 (무결점 확인 전 발송 금지!)
```powershell
$env:PYTHONUTF8 = "1"
python "C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\make_short.py" render "C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\jobs\<작업명>"
python "C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\tools\verify_sync.py" "C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\jobs\<작업명>"
```
- `make_short.py render` 끝에 자동 실행되는 `tools/qa_check.py`에서 **`[자동 품질 검수 통과 8/8]`** 이 반드시 떠야 한다:
  1. 길이 `45~57초` 이내
  2. 자막 빈틈 `0.00초` (카드 간 공백 0건)
  3. `0.35초` 미만 찰나 자막 0건
  4. `{big}` 강조 자막 2줄 줄바꿈 0건 (전원 1줄)
  5. 마크업 태그 노출 0건
  6. 본편 중간 `0.40초` 이상 오디오 무음 공백 0건
  7. 효과음 중복 0건 & 마지막 내레이션 `"sfx": null`
  8. `source_vocals.wav` 분리 적용 완료
- 자동 생성된 `jobs/<작업명>/qa_big_cards.jpg` 및 `qa_transitions.jpg`를 `read_file`로 직접 눈으로 확인하여 화면 잘림·자막 겹침·전환 깜빡임이 없는지 최종 확인한다. 만약 단 1건이라도 경고가 뜨면 먼저 수정·재렌더링하여 `8/8 통과`를 만든 뒤 다음 단계로 간다.

## 5. 파일 저장 · 네이버 메일 자동 발송 · 깃허브 푸시
1. `C:\Users\tmd39\Downloads\chatgpt 사진\` 폴더에 `<한글이름>.mp4`, `<한글이름>_업로드정보(제목·설명·태그).txt`, `<한글이름>_고정댓글.txt` 저장 및 `jobs/<작업명>/<한글이름>.mp4` 복사.
2. 네이버 메일('내게쓰기') 발송:
   ```powershell
   python "C:\Users\tmd39\Desktop\youtube_jonggo\60SEC_YOUTUBE_AUTO_SHORT\tools\send_mail.py" --job <작업명> --video "<한글이름>.mp4" --name "<한글이름>"
   ```
3. `AGENTS.md` / `GEMINI.md`의 완료 주제 목록에 `<작업명>`을 추가하고, `https://github.com/tmd3952wls/youtube_jonggo.git` (`jonggo/main`)에 커밋 및 푸시한다 (`.env` 및 영상·음성 파일 제외).
