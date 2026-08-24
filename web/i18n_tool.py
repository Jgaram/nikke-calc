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

INLINE = {"b", "i", "em", "strong", "kbd", "code", "a", "span", "small", "br", "sup", "sub", "u", "s", "mark"}
VOID = {"br", "img", "input", "meta", "link", "hr", "wbr", "source"}


class _Html(HTMLParser):
    """i18n.js `apply()`와 같은 규칙.

    **단위**(innerHTML 통째): 자기 텍스트 노드에 한글이 있고, 자손이 전부 인라인 태그이며,
    자손 어디에도 id가 없는 요소. 그 안의 텍스트는 따로 내지 않고, 안쪽 단위도 바깥 단위에
    묻힌다. 주석은 키에서 뺀다. 나머지 한글 텍스트 노드는 하나씩 낸다."""

    def __init__(self, raw: str):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self._line_off = [0]
        for i, ch in enumerate(raw):
            if ch == chr(10):
                self._line_off.append(i + 1)
        self.attrs_found: list[tuple[str, str]] = []
        self.texts: list[tuple[str, int]] = []          # (원문, 위치)
        self.units: list[tuple[str, int, int, str]] = []  # (키, 시작, 끝, 태그)
        self.stack: list[dict] = []
        self._skip = 0

    def _abs(self):
        ln, col = self.getpos()
        return self._line_off[ln - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        for k, v in attrs:
            if k in ATTRS and v and KO.search(v):
                self.attrs_found.append((v, f"index.html {k}@{tag}"))
        has_id = any(k == "id" for k, _ in attrs)
        for anc in self.stack:
            anc["kids"].append(tag)
            if has_id:
                anc["deep_id"] = True
        if tag in VOID:
            return
        self.stack.append({"tag": tag, "start": self._abs() + len(self.get_starttag_text()),
                           "kids": [], "own": False, "deep_id": False})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1
        if tag in VOID or not self.stack:
            return
        while self.stack and self.stack[-1]["tag"] != tag:
            self._close(self.stack.pop(), self._abs())
        if self.stack:
            self._close(self.stack.pop(), self._abs())

    def _close(self, node, end):
        if self._skip or not node["own"] or not node["kids"] or node["deep_id"]:
            return
        if all(k in INLINE for k in node["kids"]):
            inner = re.sub(r"<!--.*?-->", "", self.raw[node["start"]:end], flags=re.S)
            # 브라우저 innerHTML 직렬화와 같은 모양으로: 태그 안 꼬리 공백 제거, 이름 엔티티는 글자로
            inner = re.sub(r"\s+>", ">", inner)
            for ent, ch in (("&mdash;", "—"), ("&ndash;", "–"), ("&hellip;", "…"), ("&middot;", "·"),
                            ("&laquo;", "«"), ("&raquo;", "»"), ("&times;", "×"), ("&rarr;", "→"),
                            ("&larr;", "←"), ("&quot;", '"')):
                inner = inner.replace(ent, ch)
            self.units.append((re.sub(r"\s+", " ", inner.strip()), node["start"], end, node["tag"]))

    def handle_data(self, data):
        if self._skip or not self.stack:
            return
        key = re.sub(r"\s+", " ", data.strip())
        if key and KO.search(key):
            self.stack[-1]["own"] = True
            self.texts.append((key, self._abs()))

    def handle_entityref(self, name):
        pass

    def handle_charref(self, name):
        pass

    @property
    def found(self) -> list[tuple[str, str]]:
        # 바깥 단위만 남긴다(안쪽 단위는 바깥에 묻힌다)
        outer = [u for u in self.units
                 if not any(o is not u and o[1] <= u[1] and u[2] <= o[2] for o in self.units)]
        out = [(k, f"index.html <{tag}>") for k, _, _, tag in outer]
        out += [(t, "index.html") for t, pos in self.texts
                if not any(a <= pos < b for _, a, b, _ in outer)]
        return out + self.attrs_found


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
        # `${…}`가 든 템플릿은 정적 부분에 한글이 없어 T()로 안 바뀐 것 — 안쪽 T()가 따로 잡힌다
        if KO.search(s) and "${" not in s:
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

    raw = (SRC / "index.html").read_text(encoding="utf-8")
    p = _Html(raw)
    p.feed(raw)
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
