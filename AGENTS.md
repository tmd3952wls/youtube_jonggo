# 전당포 사나이들 쇼츠 자동 제작 지침 (1분냠냠)

## 1. 소통 및 진행 원칙
- **무조건 쉬운 한국어로만** 소통한다 (영어 전문용어 남발 금지).
- 사용자에게 일일이 옵션을 묻지 말고, 에이전트가 최적의 선택지를 직접 추천·결정해서 **처음부터 끝까지 알아서 완성**한다.
- 완성본은 채팅방에 올리지 말고 **`60SEC_YOUTUBE_AUTO_SHORT/tools/send_mail.py`로 네이버 메일(내게쓰기)에 바로 발송**한다.

## 2. 내레이션 음성 (필수 고정)
- 내레이션 음성은 무조건 **캡컷(CapCut)의 `'활기찬 바리톤'` 1.1배속**(`config.json` → `"provider": "capcut"`, `"voice": "vibrant_baritone"`)만 사용한다. 다른 TTS로 임의 변경 금지.
- 음성 생성 절차:
  1. `python "60SEC_YOUTUBE_AUTO_SHORT/tools/capcut_voice.py" make --job <작업명> --voice vibrant_baritone` 실행 (캡컷이 꺼져 있을 때 실행해야 목록 맨 위에 바로 등록됨).
  2. 격리(샌드박스)가 꺼진 CLI 환경이라면 파이썬 화면 캡처(`PIL.ImageGrab`) 및 마우스·키보드 제어로 PC 캡컷(`C:\Users\tmd39\AppData\Local\CapCut\Apps\CapCut.exe`)을 직접 열어 `voice_<작업명>_vibrant_baritone` 프로젝트 진입 → 전체 선택(`Ctrl+A`) → `텍스트` → `텍스트 읽기` → `'활기찬 바리톤'` 적용 완료 대기 후 캡컷 종료까지 자동으로 수행한다.
  3. `python "60SEC_YOUTUBE_AUTO_SHORT/tools/capcut_voice.py" harvest --job <작업명> --voice vibrant_baritone` 실행하여 `assets/voices/vibrant_baritone/`에 6~8문장 `.wav` 수집 확인.

## 3. 대본·금액·효과음·마무리 규칙
- 작업 폴더: `60SEC_YOUTUBE_AUTO_SHORT/jobs/<작업명>/` (기존 제작 완료 주제인 `rolex_gmt`, `silver_hoard`, `mario_nes`, `vhs_trio`, `double_eagle`, `jordan_sneakers`, `pawn_auto`, `charizard_cards`, `apple1_computer`, `stradivarius_violin`, `faberge_spider`와 중복 금지).
- 총 영상 길이는 **45~57초** 사이로 맞춘다.
- **자막·음성 빈틈 제로 규칙**:
  - 처음(`0.00초`)부터 끝까지 **자막 사이 빈틈이 단 0.01초도 생기지 않게** 이전 자막 끝과 다음 자막 시작을 빈틈없이 이어 붙인다(화면에서 자막이 잠깐이라도 사라져 흐름이 끊기면 안 됨).
  - 원본 대사와 해설 음성 모두 **숨쉬는 구간·침묵 공백이 느껴지지 않도록** 단어 사이 쉼과 앞뒤 여백을 촘촘하게 잘라내어 속도감 있게 이어 붙인다.
  - 대사 번역은 `"14억 원 내놔요!"`, `"당장 14억 원 쏴요!"`처럼 **상황에 딱 맞는 찰지고 재치 있는 구어체**로 살려 보는 재미를 높인다.
- 영어 대사 배경음악 제거: 반드시 `tools/separate_vocals.py`로 `source_vocals.wav`를 먼저 뽑아 배경음악 충돌을 없앤다.
- 억 단위 흥정 금액 표기 주의: `krw_text()` 자동 환산은 1천만 원 단위로 반올림되어 미세한 흥정 차이가 사라지므로, 1억 이상 접전 구간은 자막에 정확한 원화(`1억 5,526만 원` 등)를 직접 적는다.
- 효과음(`assets/sfx_pack/`):
  - 한 영상 안에서 **같은 효과음 파일 중복 사용 금지**(모두 서로 다른 파일로 배치).
  - **마지막 마무리 내레이션 구간에는 유치한 퇴장음·코믹 효과음을 절대 넣지 않는다 (`"sfx": null`)**.
- 렌더링 후 `tools/verify_sync.py`로 자막 싱크를 검증하고, 결과 영상을 직접 확인한다.

## 4. 완료 후 네이버 메일 자동 발송
- `jobs/<작업명>/upload.txt`의 업로드 정보와 고정댓글을 `C:\Users\tmd39\Downloads\chatgpt 사진\<한글이름>_업로드정보(제목·설명·태그).txt` 및 `<한글이름>_고정댓글.txt`로 저장한 뒤,
- `python "60SEC_YOUTUBE_AUTO_SHORT/tools/send_mail.py" --job <작업명> --video <최종영상파일명.mp4> --name <한글이름>`으로 즉시 네이버 메일 발송까지 마친다.
