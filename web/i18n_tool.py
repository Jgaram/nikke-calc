"""다국어 사전 관리 — 원문 뽑기·빠진 번역 찾기.

사전(`web/src/i18n/<lang>.json`)은 「한국어 원문 → 현지어」의 납작한 표다.
원문은 코드에 흩어져 있으므로 여기서 긁어 모아(**카탈로그**) 사전과 대조한다.

    python web/i18n_tool.py extract          # 카탈로그 갱신 + 감싸지 않은 한글 리터럴 보고
    python web/i18n_tool.py check            # 언어별 빠진·남은 항목 수
    python web/i18n_tool.py scaffold         # 빠진 원문을 각 사전에 ""로 추가 (채우는 건 사람)
    python web/i18n_tool.py missing en       # 빠진 원문 목록 (번역 작업용)

원문을 긁는 곳:
  - index.html      텍스트 노드·title·placeholder·aria-label·alt 중 한글이 든 것
  - app.js          `T("…")` 첫 인자, `el(tag, cls, "…")` 셋째 인자, NOTICES·CHANGELOG 항목
  - server.py       `_err("…")` — 서버가 준 문장도 화면에 찍힐 때 사전을 지난다
  - worker.js       파이썬 블록의 오류 문장 몇 개

게임 데이터(니케·스킬 이름)는 `game.<lang>.json`(scraper/cdn_locale.py)이 따로 들고
있으므로 여기서는 세지 않는다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "src"
I18N = SRC / "i18n"
CATALOG = ROOT / "web" / "i18n_catalog.json"
LANGS = ("en", "ja", "zh")
KO = re.compile(r"[가-힣]")
ATTRS = ("title", "placeholder", "aria-label", "alt")


# ── 원문 긁기 ─────────────────────────────────────────────────────────────

class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found: list[tuple[str, str]] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        for k, v in attrs:
            if k in ATTRS and v and KO.search(v):
                self.found.append((v, f"index.html {k}@{tag}"))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        key = re.sub(r"\s+", " ", data.strip())
        if key and KO.search(key):
            self.found.append((key, "index.html"))


def _unescape_js(s: str) -> str:
    return (s.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")
             .replace("\\`", "`").replace("\\\\", "\\"))


def _js_literals(src: str, call: str) -> list[tuple[str, int]]:
    """`call("…")` / `call('…')` / `call(`…`)` 의 첫 인자. (원문, 줄번호)"""
    out = []
    pat = re.compile(call + r"\(\s*(?:\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`)", re.S)
    for m in pat.finditer(src):
        s = next(g for g in m.groups() if g is not None)
        s = _unescape_js(s)
        if KO.search(s):
            out.append((s, src.count("\n", 0, m.start()) + 1))
    return out


def _el_literals(src: str) -> list[tuple[str, int]]:
    """`el("tag", cls, "…")` 셋째 인자가 한글 리터럴인 것. 템플릿도 받는다(T와 같은 자리표시)."""
    out = []
    pat = re.compile(r"\bel\(\s*\"[a-z0-9]+\"\s*,\s*(?:\"[^\"]*\"|'[^']*'|`[^`]*`|null|[\w.?]+(?:\([^()]*\))?)\s*,\s*"
                     r"(?:\"((?:[^\"\\]|\\.)*)\"|`((?:[^`\\]|\\.)*)`)", re.S)
    for m in pat.finditer(src):
        s = m.group(1) if m.group(1) is not None else m.group(2)
        s = _unescape_js(s)
        if KO.search(s):
            out.append((s, src.count("\n", 0, m.start()) + 1))
    return out


def _js_array(src: str, name: str) -> list:
    """`const NAME = [ … ];` 블록을 node로 평가한다 — 문자열 이어붙이기(`"a" + "b"`)를
    정규식으로 흉내내다 틀리느니 JS에게 맡긴다."""
    m = re.search(rf"^const {name} = \[\n(.*?)^\];", src, re.S | re.M)
    if not m:
        return []
    js = f"const {name} = [\n{m.group(1)}];\nprocess.stdout.write(JSON.stringify({name}));"
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        print(f"[!] {name} 평가 실패: {r.stderr[:200]}", file=sys.stderr)
        return []
    return json.loads(r.stdout)


def extract() -> list[dict]:
    found: list[tuple[str, str]] = []

    p = _Html()
    p.feed((SRC / "index.html").read_text(encoding="utf-8"))
    found += p.found

    app = (SRC / "app.js").read_text(encoding="utf-8")
    found += [(s, f"app.js:{ln}") for s, ln in _js_literals(app, r"\bT")]
    found += [(s, f"app.js:{ln} el") for s, ln in _el_literals(app)]
    for sec in _js_array(app, "NOTICES"):
        for item in sec.get("items", []):
            if item:
                found.append((item, f"app.js NOTICES {sec.get('date')}"))
    for sec in _js_array(app, "CHANGELOG"):
        for item in sec.get("items", []):
            if item:
                found.append((item, f"app.js CHANGELOG {sec.get('v')}"))

    for extra in ("squadshot.js",):
        f = SRC / extra
        if f.exists():
            found += [(s, f"{extra}:{ln}") for s, ln in _js_literals(f.read_text(encoding="utf-8"), r"\bT")]

    srv = (ROOT / "web" / "server.py").read_text(encoding="utf-8")
    for m in re.finditer(r"_err\(\s*(?:f?)\"((?:[^\"\\]|\\.)*)\"", srv):
        s = m.group(1)
        if KO.search(s) and "{" not in s:
            found.append((s, f"server.py:{srv.count(chr(10), 0, m.start()) + 1}"))

    # 순서를 지키며 중복만 걷는다 — 사전 파일이 코드 순서를 따라야 찾아 고치기 쉽다
    seen: dict[str, str] = {}
    for s, where in found:
        seen.setdefault(s, where)
    return [{"key": k, "where": w} for k, w in seen.items()]


def report_unwrapped(app: str) -> None:
    """감싸지 않은 한글 큰따옴표 리터럴. 데이터 키(비교·조회 자리)는 뺀다."""
    data_ctx = re.compile(r"(===|!==|==|case |\.get\(|\.has\(|\.includes\(|\.indexOf\(|\[|: |\?\?|in )\s*$")
    hits = []
    for ln, line in enumerate(app.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        for m in re.finditer(r"\"((?:[^\"\\]|\\.)*[가-힣](?:[^\"\\]|\\.)*)\"", line):
            before = line[:m.start()]
            if re.search(r"\bT\(\s*$", before) or re.search(r"\bel\(\s*\"[a-z0-9]+\"\s*,[^,]*,\s*$", before):
                continue
            if data_ctx.search(before):
                continue
            hits.append((ln, m.group(1)[:50]))
    print(f"\n감싸지 않은 한글 리터럴(추정) {len(hits)}개 — app.js")
    for ln, s in hits[:400]:
        print(f"  {ln:5d}  {s}")


# ── 사전 대조 ─────────────────────────────────────────────────────────────

def load_dict(lang: str) -> dict:
    f = I18N / f"{lang}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_dict(lang: str, d: dict) -> None:
    I18N.mkdir(parents=True, exist_ok=True)
    (I18N / f"{lang}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")


def check(catalog: list[dict]) -> None:
    keys = [c["key"] for c in catalog]
    print(f"카탈로그 {len(keys)}개")
    for lang in LANGS:
        d = load_dict(lang)
        missing = [k for k in keys if not d.get(k)]
        stale = [k for k in d if k not in set(keys)]
        print(f"  {lang}: 번역 {len(d) - len(stale)} · 빠짐 {len(missing)} · 카탈로그 밖 {len(stale)}")


def scaffold(catalog: list[dict]) -> None:
    keys = [c["key"] for c in catalog]
    for lang in LANGS:
        old = load_dict(lang)
        new = {k: old.get(k, "") for k in keys}
        # 카탈로그 밖 항목도 버리지 않는다 — 서버 메시지처럼 긁히지 않는 원문이 있다
        for k, v in old.items():
            if k not in new and v:
                new[k] = v
        save_dict(lang, new)
        print(f"  {lang}.json: {len(new)}개 ({sum(1 for v in new.values() if not v)} 비어 있음)")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "extract":
        cat = extract()
        CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"카탈로그 {len(cat)}개 → {CATALOG.relative_to(ROOT)}")
        report_unwrapped((SRC / "app.js").read_text(encoding="utf-8"))
        return
    cat = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else extract()
    if cmd == "check":
        check(cat)
    elif cmd == "scaffold":
        scaffold(cat)
    elif cmd == "missing":
        lang = sys.argv[2]
        d = load_dict(lang)
        for c in cat:
            if not d.get(c["key"]):
                print(json.dumps(c["key"], ensure_ascii=False) + f"   // {c['where']}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
