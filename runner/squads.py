"""회귀 하네스 스쿼드 보드 HTML 생성.

`runner/snapshot.py`의 `SQUADS`(정본) · `baseline/*.json` · 캐릭터 메타를 조인해
스쿼드별 **편성 순서 · 조건 · 커버**를 초상화 보드로 낸다.
조합·조건·이름을 손보기 전에 "지금 무엇이 무엇을 덮고 있는가"를 한 화면에서 보는 도구다.

문서가 아니라 코드·baseline에서 파생되므로 스쿼드를 고치면 다시 돌리기만 하면 된다.
근거 주석도 `snapshot.py` 원본에서 읽어 오므로 여기에 다시 적지 않는다.

사용:
  python -m runner.squads          # squads.html 생성
  python -m runner.squads --open   # 생성 후 브라우저로 열기
"""

from __future__ import annotations

import html
import json
import re
import sys
import webbrowser
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from runner import roster, spec
from runner.snapshot import SQUADS, baseline_path, build_squad, coverage

ROOT = Path(__file__).resolve().parent.parent
NIKKE = ROOT / "data" / "parsed_nikke.json"
SRC = ROOT / "runner" / "snapshot.py"
OUT = ROOT / "squads.html"

# 이름 앞머리 = 계열. `HARNESS.md §스쿼드 커버리지`의 분류와 같은 축이다.
FAMILIES = [
    ("S36_", "S36 Egovista (수냉)"),
    ("S37_", "S37 Ultra (작열)"),
    ("S38_", "S38 Annihilio (철갑)"),
    ("S39_", "S39 Island Eater (전격)"),
    ("S40_", "S40 Luxurious Spider (풍압)"),
    ("커버_", "지정 편성"),
]


def family(name: str) -> tuple[str, str]:
    for prefix, label in FAMILIES:
        if name.startswith(prefix):
            return prefix, label
    return "기타", "기타"


# ── snapshot.py 주석 읽기 ──────────────────────────────────────────────────
# 스쿼드의 "왜 이 편성인가"는 전부 SQUADS 안의 주석에 있다. 여기에 옮겨 적으면
# 갈라지므로 원본을 파싱한다 (AGENTS.md §Documentation).

def parse_source() -> tuple[list[dict], dict[str, list[str]]]:
    """(항목 순서, 스쿼드별 주석). 항목은 구역 머리말(`# ── … ──`)과 스쿼드가 섞인 순서열."""
    lines = SRC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("SQUADS"))

    items: list[dict] = []
    notes: dict[str, list[str]] = {}
    block: list[str] = []   # 스쿼드 밖에서 모으는 주석 덩이
    cur: str | None = None
    depth = 0

    for line in lines[start:]:
        code, sep, cmt = line.partition("#")

        if sep and not code.strip():            # 주석 전용 줄
            # `# ` 한 칸만 걷어내고 나머지 들여쓰기는 남긴다 — 글머리표의 이어지는 줄인지
            # 새 문단인지를 그 들여쓰기로 가른다(`paragraphs()`).
            text = cmt[1:] if cmt.startswith(" ") else cmt
            (notes[cur] if cur else block).append(text.rstrip())
            continue

        # 구역 머리말은 `── … ──`로 시작하는 덩이다. 그 밖의 덩이는 다음 스쿼드의 주석.
        if cur is None and block and block[0].startswith("──"):
            items.append({"kind": "section", "lines": block})
            block = []

        if depth == 1 and cur is None:
            m = re.match(r'"([^"]+)":\s*\{', code.strip())
            if m:
                cur = m.group(1)
                notes[cur] = block
                block = []
                items.append({"kind": "squad", "name": cur})

        depth += code.count("{") + code.count("[") - code.count("}") - code.count("]")
        if cur and depth <= 1:
            cur = None
        if depth == 0 and items:
            break

    for key, val in list(notes.items()):
        notes[key] = _tidy(val)
    for it in items:
        if it["kind"] == "section":
            it["lines"] = _tidy(it["lines"])
    return items, notes


_BULLET = ("·", "①", "②", "③", "④", "⑤", "↓", "↑", "⚠", "-", "*")


def paragraphs(lines: list[str]) -> list[str]:
    """주석 줄바꿈을 문단으로 되돌린다.

    소스의 줄바꿈은 79칸 폭 때문이지 문단 경계가 아니다. 빈 주석 줄과
    글머리표(`·`·`①`·`↓` …)에서만 끊고 나머지는 이어 붙인다.
    """
    out: list[str] = []
    cur: list[str] = []
    indent: int | None = None
    for line in lines:
        s = line.strip()
        if not s:
            if cur:
                out.append(" ".join(cur))
                cur, indent = [], None
            continue
        here = len(line) - len(line.lstrip())
        # 글머리표를 만났거나, 들여쓰기가 얕아졌으면(= 글머리표 블록에서 빠져나왔으면) 끊는다.
        if cur and (s[0] in _BULLET or s.startswith("커버")
                    or (indent is not None and here < indent)):
            out.append(" ".join(cur))
            cur = []
        if not cur:
            indent = here
        cur.append(s)
    if cur:
        out.append(" ".join(cur))
    return out


def rich(text: str) -> str:
    """주석의 마크다운 흉내(`코드`·**강조**)를 그대로 살린다."""
    t = esc(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    return t


def _tidy(block: list[str]) -> list[str]:
    """빈 주석 줄을 문단 경계로 남기고 앞뒤 공백 줄을 턴다."""
    out = [l.strip("─ ").strip() if "──" in l else l.rstrip() for l in block]
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


# ── baseline에서 읽는 사실 ────────────────────────────────────────────────

_BURST_USE = re.compile(r"^BURST stage:(\d) 사용 → (.+?)(?: ×(\d+))?$")


def baseline_facts(name: str) -> dict | None:
    """총딜·풀버스트 횟수·사이클 간격·멤버별 버스트 횟수. baseline이 없으면 None."""
    path = baseline_path(name)
    if not path.exists():
        return None
    snap = json.loads(path.read_text(encoding="utf-8"))
    bursts: Counter[str] = Counter()
    for cycle in snap["L3_order"]["cycles"]:
        for row in cycle:
            if m := _BURST_USE.match(row):
                bursts[m.group(2)] += int(m.group(3) or 1)
    return {
        "total": snap["L1_numbers"]["squad_total"],
        "fb": snap["L1_numbers"]["full_burst_count"],
        "gaps": snap["L4_phase"]["cycle_gaps"],
        "bursts": bursts,
        "char_total": snap["L1_numbers"]["char_total"],
    }


def gap_summary(gaps: list[float]) -> str:
    if not gaps:
        return "—"
    uniq = sorted(set(gaps))
    if len(uniq) == 1:
        return f"{uniq[0]:g} 균일 ({len(gaps)}칸)"
    counts = Counter(gaps)
    head = " · ".join(f"{g:g}×{counts[g]}" for g in uniq[:4])
    return head + (" …" if len(uniq) > 4 else "")


# ── 커버 ──────────────────────────────────────────────────────────────────

def cover_map() -> dict[str, list[str]]:
    """캐릭터 → 등장하는 스쿼드 목록."""
    out: dict[str, list[str]] = {}
    for sq, info in SQUADS.items():
        for m in info["members"]:
            out.setdefault(m, []).append(sq)
    return out


# ── 렌더 ──────────────────────────────────────────────────────────────────

def esc(v) -> str:
    return html.escape(str(v))


def icon(path: str, alt: str, cls: str = "ico") -> str:
    return f'<img class="{cls}" src="image/icon/{path}" alt="{esc(alt)}" title="{esc(alt)}">'


def member_card(name: str, idx: int, meta: dict, facts: dict | None,
                covers: list[str], devs: list[tuple], squad: str,
                no_burst: str | None = None) -> str:
    rec = meta.get(name, {})
    el, cls, burst = rec.get("element_code", "?"), rec.get("class", "?"), rec.get("burst_stage", "?")
    cd = rec.get("burst_cooldown")
    img = roster.portrait(name)
    thumb = (
        f'<img class="portrait" src="{esc(img)}" alt="{esc(name)}" loading="lazy">'
        if img else '<div class="portrait noimg">?</div>'
    )
    badges = []
    if burst in roster.BURST_ICON:
        badges.append(icon(roster.BURST_ICON[burst], f"버스트 {burst}단", "badge"))
    if el in roster.ELEMENT_ICON:
        badges.append(icon(roster.ELEMENT_ICON[el], el, "badge"))
    if cls in roster.CLASS_ICON:
        badges.append(icon(roster.CLASS_ICON[cls], cls, "badge"))

    # 버스트 0회는 회귀가 아니라 편성 사실이다 — 같은 단계에서 뒤로 밀렸거나(선점당함)
    # `no_burst_char`로 일부러 뺐거나. 둘을 구분해서 보여 준다.
    if not facts:
        burst_chip = ""
    elif name == no_burst:
        burst_chip = '<span class="chip dim" title="config.no_burst_char로 지정">버스트 미사용</span>'
    else:
        uses = facts["bursts"].get(name, 0)
        burst_chip = (
            f'<span class="chip">버스트 {uses}회</span>' if uses else
            '<span class="chip warn" title="같은 단계 앞사람에게 선점당해 한 번도 못 썼다. '
            '의도한 편성일 수도 있다 — 근거 주석을 본다">버스트 0회</span>'
        )

    only = len(covers) == 1
    star = '<span class="only" title="이 스쿼드가 유일한 커버">유일</span>' if only else ""
    others = [s for s in covers if s != squad]
    also = (f'<div class="also" title="{esc(" · ".join(others))}">그 밖 {len(others)}곳</div>'
            if others else "")

    dev_html = "".join(
        f'<li>{esc(k)}: {esc(spec._fmt(b))} → <b>{esc(spec._fmt(c))}</b> <span class="src">({esc(src)})</span></li>'
        for k, b, c, src in devs
    )
    dev_block = f'<ul class="dev">{dev_html}</ul>' if dev_html else ""

    dmg = ""
    if facts and (v := facts["char_total"].get(name)):
        share = v / facts["total"] * 100 if facts["total"] else 0
        dmg = f'<div class="dmg"><span style="--w:{share:.1f}%"></span>{share:.1f}%</div>'

    return (
        f'<figure class="mem{" only" if only else ""}" data-name="{esc(name)}" '
        f'style="--el:{roster.ELEMENT_COLOR.get(el, "#888")}">'
        f'<div class="thumb"><span class="ord">{idx}</span>{thumb}'
        f'<div class="badges">{"".join(badges)}</div>'
        f'<span class="wchip">{esc(rec.get("weapon_type", "?"))}</span></div>'
        f'<figcaption>{esc(name)}{star}</figcaption>'
        f'<div class="mline">{f"쿨 {cd:g}s" if cd else ""} {burst_chip}</div>'
        f'{dmg}{also}{dev_block}</figure>'
    )


def enemy_chip(enemy: dict) -> str:
    if not enemy:
        return '<span class="chip dim">적 무속성</span>'
    parts = []
    code = enemy.get("code")
    if code:
        ico = roster.ELEMENT_ICON.get(code, "")
        img = icon(ico, code, "ico") if ico else ""
        parts.append(f'<span class="chip el" style="--el:{roster.ELEMENT_COLOR.get(code, "#888")}">'
                     f'{img}적 {esc(code)}</span>')
    if px := enemy.get("core_px"):
        parts.append(f'<span class="chip">코어 {px}px</span>')
    return "".join(parts)


def config_chips(cfg: dict) -> str:
    out = []
    for k, v in cfg.items():
        if k == "first_burst_time":
            continue  # 전 스쿼드 공통(3.0)이라 칩으로 두면 소음이다
        if k == "burst_sequence":
            out.append(f'<span class="chip">burst_sequence {len(v)}엔트리</span>')
        else:
            out.append(f'<span class="chip">{esc(k)} = {esc(v)}</span>')
    return "".join(out)


def squad_card(name: str, info: dict, meta: dict, covers: dict[str, list[str]]) -> str:
    facts = baseline_facts(name)
    squad = build_squad(info["members"], info.get("chars"))
    devs = spec.squad_deviations(squad)
    prefix, fam_label = family(name)

    stages = Counter(meta.get(m, {}).get("burst_stage", "?") for m in info["members"])
    stage_txt = " · ".join(f"B{s} {n}" for s, n in sorted(stages.items()))
    only_n = sum(1 for m in info["members"] if len(covers[m]) == 1)

    no_burst = (info.get("config") or {}).get("no_burst_char")
    members = "".join(
        member_card(m, i + 1, meta, facts, covers[m], devs.get(m, []), name, no_burst)
        for i, m in enumerate(info["members"])
    )

    stat = []
    if facts:
        stat.append(f'<b>{facts["total"]:,}</b> 총딜')
        stat.append(f'풀버스트 {facts["fb"]}회')
        stat.append(f'사이클 {gap_summary(facts["gaps"])}')
    else:
        stat.append('<span class="warn">baseline 없음</span>')

    chars = info.get("chars")
    chars_html = (
        f'<details class="over"><summary>스쿼드 전용 오버라이드 <code>chars</code> '
        f'· {len(chars)}명</summary><pre>{esc(json.dumps(chars, ensure_ascii=False, indent=2))}</pre></details>'
        if chars else ""
    )

    body = "".join(f"<p>{rich(p)}</p>" for p in paragraphs(NOTES.get(name) or []))
    notes_html = (
        f'<aside class="note"><h4>근거 주석 <span class="src">snapshot.py</span></h4>{body}</aside>'
        if body else '<aside class="note empty"><h4>근거 주석 없음</h4></aside>'
    )

    return (
        f'<section class="squad" id="sq-{esc(name)}" data-fam="{esc(prefix)}" data-name="{esc(name)}" '
        f'data-enemy="{esc(info.get("enemy", {}).get("code") or "무속성")}" '
        f'data-members="{esc(" ".join(info["members"]))}">'
        f'<header><h2>{esc(name)}</h2>'
        f'<span class="fam">{esc(fam_label)}</span>'
        f'{enemy_chip(info.get("enemy") or {})}{config_chips(info.get("config") or {})}'
        f'<span class="chip dim">{esc(stage_txt)}</span>'
        f'<span class="chip dim">유일 커버 {only_n}명</span>'
        f'<span class="stat">{" · ".join(stat)}</span></header>'
        f'<div class="body"><div class="left"><div class="mems">{members}</div>{chars_html}</div>'
        f'{notes_html}</div></section>'
    )


CSS = """
:root{--bg:#f7f7f9;--fg:#1b1c1f;--sub:#6b6e76;--card:#fff;--line:#e2e3e8;--warn:#d24b3e;--acc:#3b6ef5}
@media (prefers-color-scheme:dark){:root{--bg:#15161a;--fg:#e9eaee;--sub:#9a9daa;--card:#1e2027;--line:#2c2f38;--warn:#ff7a6b;--acc:#7aa2ff}}
*{box-sizing:border-box}
body{margin:0;padding:22px 26px 70px;background:var(--bg);color:var(--fg);
 font:14px/1.55 "Pretendard","Malgun Gothic",system-ui,sans-serif}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--sub);font-size:13px;margin:0 0 16px}
code{font-family:ui-monospace,Consolas,monospace;font-size:.92em}
.bar{display:flex;flex-wrap:wrap;gap:7px;align-items:center;position:sticky;top:0;z-index:5;
 padding:10px 0;background:var(--bg);border-bottom:1px solid var(--line);margin-bottom:18px}
.bar b{font-size:12px;color:var(--sub);margin:0 2px}
button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;
 padding:5px 12px;font-size:12px;cursor:pointer}
button.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
input[type=search]{border:1px solid var(--line);background:var(--card);color:var(--fg);
 border-radius:999px;padding:5px 12px;font-size:12px;min-width:170px}
.sec{margin:26px 0 12px;padding:10px 14px;border-left:3px solid var(--acc);background:var(--card);
 border-radius:0 10px 10px 0}
.sec h3{margin:0 0 4px;font-size:13px}
.sec p{margin:0;color:var(--sub);font-size:12.5px}
.toc{margin:0 0 18px;display:flex;flex-direction:column;gap:6px}
.trow{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.trow b{font-size:11.5px;color:var(--sub);min-width:96px}
.trow b span{opacity:.6;font-weight:400}
.toc a{text-decoration:none;color:var(--fg)}
.toc a:hover{border-color:var(--fg)}
.squad{background:var(--card);border:1px solid var(--line);border-radius:14px;
 padding:14px 16px 12px;margin:0 0 14px;scroll-margin-top:64px}
.squad header{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:12px}
.squad h2{font-size:16px;margin:0 6px 0 0;letter-spacing:-.2px}
.fam{font-size:11px;color:var(--bg);background:var(--sub);border-radius:999px;padding:2px 8px}
.stat{margin-left:auto;font-size:12px;color:var(--sub);white-space:nowrap}
.chip{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:2px 8px;
 display:inline-flex;align-items:center;gap:4px}
.chip.dim{color:var(--sub)}
.chip.warn{color:var(--warn);border-color:var(--warn)}
.chip.el{border-color:var(--el);color:var(--el)}
.warn{color:var(--warn)}
.ico{width:13px;height:13px}
.body{display:grid;gap:16px;grid-template-columns:minmax(0,1fr) minmax(240px,330px);align-items:start}
@media (max-width:1080px){.body{grid-template-columns:minmax(0,1fr)}}
.left{min-width:0}
.mems{display:grid;gap:10px;grid-template-columns:repeat(5,minmax(0,1fr))}
@media (max-width:640px){.mems{grid-template-columns:repeat(3,minmax(0,1fr))}}
.mem{margin:0;text-align:center;min-width:0}
.thumb{position:relative;aspect-ratio:1;border-radius:10px;overflow:hidden;background:var(--bg);
 border:1px solid var(--line);border-bottom:3px solid var(--el)}
.portrait{width:100%;height:100%;object-fit:cover;object-position:center 18%;display:block}
.noimg{display:flex;align-items:center;justify-content:center;height:100%;color:var(--sub);font-size:22px}
.ord{position:absolute;left:0;top:0;z-index:2;background:rgba(0,0,0,.62);color:#fff;font-size:10px;
 font-weight:700;padding:2px 6px;border-radius:0 0 8px 0}
.badges{position:absolute;right:3px;top:3px;display:flex;flex-direction:column;gap:3px;
 padding:3px;border-radius:7px;background:rgba(0,0,0,.5)}
.badge{width:15px;height:15px;display:block}
.wchip{position:absolute;right:3px;bottom:3px;padding:1px 5px;border-radius:6px;
 background:rgba(0,0,0,.55);color:#fff;font-size:10px;font-weight:700}
figcaption{font-size:11.5px;margin-top:5px;line-height:1.3;word-break:keep-all}
.only>figcaption{font-weight:700}
.mem .only{margin-left:4px;font-size:9.5px;color:var(--bg);background:var(--acc);
 border-radius:4px;padding:1px 4px;vertical-align:1px}
.mline{font-size:10.5px;color:var(--sub);margin-top:3px;display:flex;gap:4px;
 justify-content:center;align-items:center;flex-wrap:wrap}
.dmg{position:relative;height:12px;margin-top:4px;font-size:9.5px;color:var(--sub);
 background:var(--bg);border-radius:3px;overflow:hidden;line-height:12px}
.dmg span{position:absolute;left:0;top:0;bottom:0;width:var(--w);background:var(--el);opacity:.28}
.also{font-size:10px;color:var(--sub);margin-top:3px}
ul.dev{list-style:none;margin:5px 0 0;padding:5px 6px;text-align:left;font-size:10px;
 background:var(--bg);border-radius:6px;color:var(--fg)}
ul.dev li{margin:0 0 2px;word-break:break-all}
ul.dev .src{color:var(--sub)}
details{margin-top:10px;font-size:12.5px}
summary{cursor:pointer;color:var(--sub);font-size:12px}
.note{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 12px;
 max-height:340px;overflow-y:auto}
.note h4{margin:0 0 4px;font-size:11.5px;color:var(--sub);font-weight:600}
.note h4 .src{font-weight:400;opacity:.7}
.note p{margin:5px 0 0;color:var(--fg);font-size:12px;line-height:1.55;word-break:keep-all}
.note p+p{margin-top:7px}
.note code{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:0 3px}
.note.empty{opacity:.45}
.sec p+p{margin-top:5px}
.over pre{margin:6px 0 0;padding:8px 10px;background:var(--bg);border-radius:8px;
 overflow-x:auto;font-size:11.5px}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));margin-top:10px}
.grid .mem{font-size:11px}
table{border-collapse:collapse;width:100%;font-size:12px;margin-top:10px}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{color:var(--sub);font-weight:600}
.hide{display:none}
.hit{outline:2px solid var(--acc);outline-offset:2px;border-radius:12px}
"""

JS = """
const state={fam:'all',enemy:'all',q:''};
function apply(){
  document.querySelectorAll('.squad').forEach(s=>{
    const q=state.q;
    const ok=(state.fam==='all'||s.dataset.fam===state.fam)
      &&(state.enemy==='all'||s.dataset.enemy===state.enemy)
      &&(!q||s.dataset.name.toLowerCase().includes(q)||s.dataset.members.toLowerCase().includes(q));
    s.classList.toggle('hide',!ok);
    s.querySelectorAll('.mem').forEach(m=>{
      m.classList.toggle('hit',!!q&&m.dataset.name.toLowerCase().includes(q));
    });
  });
  const n=document.querySelectorAll('.squad:not(.hide)').length;
  document.querySelector('#count').textContent=n;
}
document.querySelectorAll('button[data-key]').forEach(b=>b.onclick=()=>{
  state[b.dataset.key]=b.dataset.val;
  document.querySelectorAll(`button[data-key="${b.dataset.key}"]`)
    .forEach(o=>o.classList.toggle('on',o===b));
  apply();
});
document.querySelector('#q').oninput=e=>{state.q=e.target.value.trim().toLowerCase();apply();};
apply();
"""


def filter_bar(enemies: list[str]) -> str:
    def row(label, key, vals):
        btns = f'<button data-key="{key}" data-val="all" class="on">전체</button>'
        btns += "".join(
            f'<button data-key="{key}" data-val="{esc(v)}">{esc(t)}</button>' for v, t in vals
        )
        return f"<b>{label}</b>{btns}"

    return (
        '<div class="bar">'
        + row("계열", "fam", [(p, f"{l}") for p, l in FAMILIES])
        + row("적", "enemy", [(e, e) for e in enemies])
        + '<input type="search" id="q" placeholder="스쿼드·캐릭터 검색">'
        + '<span class="chip dim"><span id="count"></span>개 표시</span>'
        + "</div>"
    )


def toc() -> str:
    """이름만 모아 본 목차. 이름 규칙을 손볼 때 계열별로 나란히 보이는 게 핵심이다."""
    rows = []
    for prefix, label in FAMILIES:
        names = [n for n in SQUADS if family(n)[0] == prefix]
        if not names:
            continue
        chips = "".join(f'<a class="chip" href="#sq-{esc(n)}">{esc(n)}</a>' for n in names)
        rows.append(f'<div class="trow"><b>{esc(label)} <span>{len(names)}</span></b>{chips}</div>')
    return f'<div class="toc">{"".join(rows)}</div>'


def index_table(covers: dict[str, list[str]]) -> str:
    rows = "".join(
        f'<tr><td>{esc(name)}</td><td>{len(sqs)}</td><td>{esc(" · ".join(sqs))}</td></tr>'
        for name, sqs in sorted(covers.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )
    return (
        f'<details><summary>캐릭터 → 등장 스쿼드 (편성된 {len(covers)}명 · <code>test_</code> 더미 포함)</summary>'
        f'<table><tr><th>캐릭터</th><th>등장</th><th>스쿼드</th></tr>{rows}</table></details>'
    )


def uncovered_grid(names: list[str], meta: dict) -> str:
    cards = "".join(
        member_card(n, 0, meta, None, [], [], "")
        .replace('<span class="ord">0</span>', "")
        for n in names
    )
    return (
        f'<details><summary>미커버 {len(names)}명 — 새 스쿼드를 짤 때 우선 후보</summary>'
        f'<div class="grid">{cards}</div></details>'
    )


NOTES: dict[str, list[str]] = {}


def build() -> str:
    global NOTES
    items, NOTES = parse_source()
    meta = json.loads(NIKKE.read_text(encoding="utf-8"))
    covers = cover_map()
    parsed, covered, uncovered = coverage()

    body: list[str] = []
    for it in items:
        if it["kind"] == "section":
            head, *rest = it["lines"]
            para = "".join(f"<p>{rich(p)}</p>" for p in paragraphs(rest))
            body.append(f'<div class="sec"><h3>{esc(head)}</h3>{para}</div>')
        else:
            name = it["name"]
            body.append(squad_card(name, SQUADS[name], meta, covers))

    enemies = sorted({(info.get("enemy") or {}).get("code") or "무속성"
                      for info in SQUADS.values()})
    only_total = sum(1 for sqs in covers.values() if len(sqs) == 1)

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>회귀 하네스 스쿼드 보드</title><style>{CSS}</style></head><body>
<h1>회귀 하네스 스쿼드 보드</h1>
<p class="sub">{len(SQUADS)}스쿼드 · 파싱된 {parsed}명 중 <b>{covered}</b>명 커버
 · 그중 <b>{only_total}</b>명은 한 스쿼드에만 있다(편성을 바꾸면 커버가 사라지는 자리)
 · 정본 <code>runner/snapshot.py</code>의 <code>SQUADS</code>
 · <code>python -m runner.squads</code>로 재생성</p>
{filter_bar(enemies)}
{toc()}
{"".join(body)}
<div class="sec"><h3>부록</h3></div>
{uncovered_grid(uncovered, meta)}
{index_table(covers)}
<script>{JS}</script>
</body></html>
"""


def main() -> None:
    OUT.write_text(build(), encoding="utf-8")
    print(f"생성: {OUT}")
    if "--open" in sys.argv:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
