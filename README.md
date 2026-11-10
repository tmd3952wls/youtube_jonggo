# YOUTUBE_AUTO — 해외 예능 요약 쇼츠 자동 생성

```
원본(URL/파일) → 영어 전사(Whisper) → 대본 plan.json(LLM) → 내레이션 TTS → 렌더(1080x1920)
```

## 사용법
```bash
python make_short.py new "https://www.youtube.com/watch?v=..." --name 이름 --context "샤크탱크, 13살 창업자"
```
- `--start 120 --end 400` : 긴 에피소드에서 해당 구간만 사용 (전사 시간 단축)
- 결과: `jobs/이름/final.mp4`, 업로드용 제목·설명·태그는 `jobs/이름/upload.txt`

API 키가 없으면 **수동 모드**로 멈춥니다.
1. `jobs/이름/prompt.txt` 내용을 ChatGPT 또는 Claude 창에 붙여넣기
2. 받은 JSON을 `jobs/이름/plan.json` 으로 저장
3. `python make_short.py render jobs/이름`

대본(plan.json)을 고친 뒤에는 `render` 만 다시 실행하면 됩니다. TTS는 캐시를 재사용합니다.

## 설정
- `.env.example` 을 `.env` 로 복사한 뒤 키 입력
- `config.json`
  - `tts.provider`: `sapi`(윈도우 기본 음성, 테스트용) → `fish` 또는 `typecast` 로 변경
    - fish: `tts.fish.reference_id` 에 Fish Audio 보이스 모델 ID
    - typecast: `tts.typecast.voice_id` 에 `tc_...` 보이스 ID
  - `subtitle.font`: 굵은 폰트(G마켓 산스 Bold, Black Han Sans 등)를 `assets/fonts` 에 넣고 폰트 이름을 적기
  - `subtitle.dial_color`, `narr_color`: white / yellow / green / cyan / red / orange
  - `render.bgm_volume`, `sfx_volume`, `narration_original_volume`(내레이션 중 원본 소리, 0 = 음소거)
- `assets/bgm/` : BGM 파일(mp3/wav)을 넣으면 매번 무작위로 깔림 (내레이션·대사 나올 때 자동 덕킹)
- `assets/sfx/` : `whoosh` `pop` `ding` `boom` (.wav/.mp3). 같은 이름으로 넣으면 기본 합성음을 교체
- `prompts/script_prompt.md` : 대본 작성 규칙. 채널 톤을 바꾸려면 여기를 수정

## EDL 편집 규칙 (2026-11-10, jobs/polygons 기준)
- 렌더: `python editor/render_worker.py --job 이름 --edl v1_edl.json --out full_v1.mp4 --preset 해외쇼쇼`
- 자막 글자 외곽선: `config.json` → `subtitle.text_outline` (검은색, 3)
- 워터마크: `config.json` → `watermark` (영상 바로 아래 검은 띠에 흐린 흰색 워터마크 "1분냠냠")
- 제품 매출 금액 자막에 `"icon": "money"` → `assets/money_icon.png` 지폐 그림 + 금전등록기 소리. 영상당 한 번만
- `audio_end` 는 다음 대사 첫 단어 전까지만. 숫자로 끝나는 대사는 정렬기가 짧게 잡으니 실제 목소리 끝까지 늘리기
- 끝에 무음 `tail` 대신 원본 소리를 이어서(`audio_end`)
