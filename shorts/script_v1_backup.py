"""2단계: 전사본 → 대본 plan.json.

모드
- claude-cli : Claude Code CLI 헤드리스(claude -p). Claude Max 구독 사용, 키 불필요 ← auto 의 1순위
- claude : ANTHROPIC_API_KEY 로 Claude API 호출
- openai : OPENAI_API_KEY 로 OpenAI API 호출
- manual : prompt.txt 만 만들어 둠 → ChatGPT/Claude 창에 붙여넣고 결과를 plan.json 으로 저장
- auto   : 키가 있는 API 를 쓰고, 없으면 manual
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .common import ROOT, read_json, write_json


def build_prompt(job: Path, context="", research_data=None):
    tr = read_json(job / "transcript.json")
    lines = "\n".join(f"{t['id']} [{t['start']:.1f}-{t['end']:.1f}] {t['text']}" for t in tr)
    tpl = (ROOT / "prompts" / "script_prompt.md").read_text(encoding="utf-8")
    from .sfxlib import category_help
    facts = (research_data or {}).get("facts") or []
    rtext = "\n".join(f"- {f['fact']} (출처: {f.get('source', '')})" for f in facts) or "(조사 결과 없음 — 후일담은 쓰지 말 것)"
    prompt = (tpl.replace("{{TRANSCRIPT}}", lines).replace("{{CONTEXT}}", context or "(없음)")
              .replace("{{SFX}}", category_help()).replace("{{RESEARCH}}", rtext))
    (job / "prompt.txt").write_text(prompt, encoding="utf-8")
    return prompt


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def call_claude(prompt, model):
    data = _post("https://api.anthropic.com/v1/messages",
                 {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                  "content-type": "application/json"},
                 {"model": model, "max_tokens": 8000,
                  "messages": [{"role": "user", "content": prompt}]})
    return "".join(b.get("text", "") for b in data["content"])


def find_claude_cli(cfg):
    p = cfg["script"].get("cli_path") or shutil.which("claude") or \
        str(Path.home() / ".local" / "bin" / "claude.exe")
    return p if Path(p).exists() or shutil.which(p) else None


def call_claude_cli(prompt, cfg):
    """Claude Code 헤드리스 실행 (Max 구독 사용, API 키 불필요). 도구·스킬·MCP 모두 끈 순수 텍스트 생성."""
    exe = find_claude_cli(cfg)
    cmd = [exe, "-p", "--output-format", "text", "--tools", "", "--disable-slash-commands",
           "--strict-mcp-config", "--no-session-persistence"]
    if cfg["script"].get("cli_model"):
        cmd += ["--model", cfg["script"]["cli_model"]]
    # 프로젝트 설정·스킬이 섞이지 않도록 빈 임시 폴더에서 실행
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=td, timeout=cfg["script"].get("cli_timeout", 600))
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"claude CLI 실패 (코드 {r.returncode}): {(r.stderr or r.stdout)[-800:]}")
    return r.stdout


RESEARCH_PROMPT = """아래는 미국 방송 전당포 사나이들(Pawn Stars) 한 장면의 영어 전사 일부와 메모다.
1) 이 장면에 나온 물건(정확한 이름·연도·제작자)과 손님을 파악하고
2) 웹 검색으로 '방송 이후' 이야기를 찾아라: 그 물건이 실제로 팔렸는지, 같은 물건(또는 같은 종류의 진품)이 이후 경매에서 얼마에 낙찰됐는지, 물건에 얽힌 실제 역사.
신뢰할 만한 출처의 사실만, 각 사실에 출처 URL. 못 찾으면 facts 를 비워라. 추측 금지.
JSON 하나만 출력: {"company": "물건 이름", "product": "물건 설명", "facts": [{"fact": "한국어 한 문장 (연도·숫자 포함)", "source": "URL"}]}

메모: {context}
전사(앞부분):
{head}
"""


def research(job: Path, cfg, context=""):
    """방송 이후 후일담을 웹에서 조사 → research.json (Claude CLI + WebSearch). 실패해도 진행."""
    p = job / "research.json"
    if p.exists():
        return read_json(p)
    exe = find_claude_cli(cfg)
    if not exe or not cfg["script"].get("research", True):
        return {}
    tr = read_json(job / "transcript.json")
    head = "\n".join(t["text"] for t in tr[:60])
    prompt = RESEARCH_PROMPT.replace("{context}", context or "(없음)").replace("{head}", head)
    try:
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run([exe, "-p", "--output-format", "text", "--tools", "WebSearch", "WebFetch",
                                "--allowedTools", "WebSearch", "WebFetch", "--disable-slash-commands",
                                "--strict-mcp-config", "--no-session-persistence"],
                               input=prompt, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=td, timeout=300)
        data = parse_json(r.stdout)
    except Exception as e:
        print(f"      ⚠ 후일담 조사 실패: {str(e)[:200]}")
        data = {}
    write_json(p, data)
    return data


def call_openai(prompt, model):
    data = _post("https://api.openai.com/v1/chat/completions",
                 {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                  "Content-Type": "application/json"},
                 {"model": model, "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]})
    return data["choices"][0]["message"]["content"]


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("응답에서 JSON 을 찾지 못했습니다:\n" + text[:500])
    return json.loads(m.group(0))


def generate(job: Path, cfg, context=""):
    """plan.json 을 만들면 True, manual 모드라 사람이 채워야 하면 False."""
    print("      방송 이후 후일담 웹 조사...")
    rd = research(job, cfg, context)
    if rd.get("facts"):
        print(f"      → {rd.get('company', '')}: 사실 {len(rd['facts'])}개 확보")
    prompt = build_prompt(job, context, rd)
    mode = cfg["script"]["mode"]
    if mode == "auto":
        mode = "claude-cli" if find_claude_cli(cfg) else \
               "claude" if os.environ.get("ANTHROPIC_API_KEY") else \
               "openai" if os.environ.get("OPENAI_API_KEY") else "manual"
    if mode == "manual":
        return False
    transcript = read_json(job / "transcript.json")
    from .align import ensure_words
    words = ensure_words(job, cfg)  # 클립 실제 길이(침묵 제거 후) 추정용
    last_err, feedback, best = None, "", None
    for attempt in range(1, cfg["script"].get("retries", 2) + 2):
        try:
            print(f"      대본 생성: {mode} (시도 {attempt})")
            p = prompt + (f"\n\n## 이전 초안의 문제 — 반드시 고쳐서 다시 써라\n{feedback}" if feedback else "")
            text = call_claude_cli(p, cfg) if mode == "claude-cli" else \
                call_claude(p, cfg["script"]["claude_model"]) if mode == "claude" else \
                call_openai(p, cfg["script"]["openai_model"])
            (job / "llm_raw.txt").write_text(text, encoding="utf-8")
            plan = validate(parse_json(text), transcript)
            best = plan
            problems = check_structure(plan, transcript, cfg, words)
            if problems:
                feedback = "\n".join(f"- {x}" for x in problems)
                print("      ⚠ 구성 문제 → 다시 작성\n" + feedback)
                continue
            write_json(job / "plan.json", plan)
            return True
        except Exception as e:  # 형식 오류·일시 실패 → 재시도
            last_err = e
            print(f"      ⚠ {str(e)[:300]}")
    if best:  # 구성 경고가 남았어도 형식이 맞는 마지막 초안은 사용
        print("      ⚠ 구성 경고가 남은 초안을 사용합니다")
        write_json(job / "plan.json", best)
        return True
    print(f"      대본 자동 생성 실패 → 수동 모드로 전환 ({last_err})"[:400])
    return False


def validate(plan, transcript):
    """대본 형식 점검. 고칠 수 있는 건 고치고, 치명적이면 예외."""
    from .sfxlib import CATEGORIES
    ids = {t["id"] for t in transcript}
    assert len(plan.get("title_lines", [])) >= 1, "title_lines 가 비어 있음"
    for i, s in enumerate(plan["segments"]):
        if s.get("sfx") and s["sfx"] not in CATEGORIES and s["sfx"] != "boom":
            s["sfx"] = None  # 모르는 효과음 이름은 무시
        if s["type"] == "narration":
            assert s.get("text"), f"segment {i}: 내레이션 text 없음"
            if s.get("broll_id") not in ids:
                s["broll_id"] = min(ids)
        elif s["type"] == "clip":
            assert s["from_id"] in ids and s["to_id"] in ids and s["from_id"] <= s["to_id"], \
                f"segment {i}: clip id 범위 오류 {s['from_id']}~{s['to_id']}"
            n = s["to_id"] - s["from_id"] + 1
            subs = s.get("subs", [])
            s["subs"] = (subs + [""] * n)[:n]
        else:
            raise ValueError(f"segment {i}: 알 수 없는 type {s['type']}")
    return plan


DEAL_WORDS = re.compile(r"달러|지분|투자(?!자)|기업가치|가치를|매출|%|억|만 ?원|가격|원가|offer|deal", re.I)


def clip_speech_sec(ids, tr, words, gap=0.25):
    """클립이 실제로 차지할 시간(렌더에서 침묵을 빼므로 단어 시간 기준)."""
    ws = sorted((w for k in ids for w in words.get(str(k), [])), key=lambda w: w["s"])
    if not ws:
        return sum(tr[k]["end"] - tr[k]["start"] for k in ids) * 0.7
    tot, last = 0.1, None
    for w in ws:
        tot += w["e"] - w["s"]
        if last is not None:
            g = w["s"] - last
            tot += g if 0 < g <= gap else (0.07 if g > gap else 0)
        last = w["e"]
    return tot


def check_structure(plan, transcript, cfg, words=None):
    """참고 영상 구성 점검: 앞부분(intro_sec)은 제품 소개만, 가격·투자·매출은 그 뒤.
    (첫 세그먼트의 훅 티저는 예외). 문제 있으면 다시 쓰게 할 피드백 문구 반환."""
    tr = {t["id"]: t for t in transcript}
    T = cfg["script"]["structure"]
    t, problems, timeline = 0.0, [], []
    first_deal = None
    for i, s in enumerate(plan["segments"]):
        if s["type"] == "narration":
            txt = s["text"]
            d = len(re.sub(r"\[(\w+:)?|\]|\s", "", txt)) / T["chars_per_sec"]
        else:
            txt = " ".join(s["subs"])
            d = clip_speech_sec(range(s["from_id"], s["to_id"] + 1), tr, words or {})
        clean = re.sub(r"\[(\w+:)?|\]", "", txt)
        timeline.append(f"  {t:4.1f}s {'N' if s['type'] == 'narration' else 'C'} {clean[:40]}")
        if i > 0 and first_deal is None and DEAL_WORDS.search(txt):
            first_deal = (t, clean[:30])
        t += d
    if first_deal and first_deal[0] < T["intro_sec"]:
        need = T["intro_sec"] - first_deal[0]
        problems.append(
            f"가격/투자/매출 이야기('{first_deal[1]}')가 {first_deal[0]:.0f}초에 나옴 → {T['intro_sec']}초 이후로 미뤄라. "
            f"그 앞의 제품 소개를 약 {need:.0f}초({int(need * T['chars_per_sec'])}자) 늘려라: "
            "불편했던 상황 묘사, 작동 원리, 실제 사용·시연 장면 clip, 장점을 추가.")
    if t < T["min_total"] or t > T["max_total"]:
        problems.append(f"예상 길이 {t:.0f}초 → {T['min_total']}~{T['max_total']}초로 맞춰라.")
    if problems:
        problems.append("예상 타임라인(침묵 제거 후 실제 길이 기준):\n" + "\n".join(timeline))
    return problems
