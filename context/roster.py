"""파싱 현황 로스터 HTML 생성.

`data/parsed_skills.json`(파싱 정본)과 `data/parsed_nikke.json`(메타)을 조인해
버스트 단계·속성으로 분류한 초상화 보드를 프로젝트 루트의 `roster.html`로 출력한다.
문서가 아니라 데이터에서 파생되므로 캐릭터가 추가돼도 손으로 고칠 것이 없다.

사용:
  python -m context.roster            # roster.html 생성
  python -m context.roster --open     # 생성 후 브라우저로 열기
"""

from __future__ import annotations

import html
import json
import sys
import webbrowser
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "data" / "parsed_skills.json"
NIKKE = ROOT / "data" / "parsed_nikke.json"
IMAGE = ROOT / "image"
OUT = ROOT / "roster.html"

# 파싱 정본에 들어 있는 회귀 테스트용 더미
TEST_KEYS = ("test_B1", "test_B2", "test_B3")

ELEMENT_ICON = {
    "작열": "icn_element_fire.webp",
    "수냉": "icn_element_water.webp",
    "풍압": "icn_element_wind.webp",
    "전격": "icn_element_elect.webp",
    "철갑": "icn_element_iron.webp",
}
ELEMENT_ORDER = ["작열", "수냉", "풍압", "전격", "철갑"]
ELEMENT_COLOR = {
    "작열": "#ff6b4a",
    "수냉": "#4aa8ff",
    "풍압": "#4ad991",
    "전격": "#c77dff",
    "철갑": "#f2c14e",
}
CLASS_ICON = {
    "화력형": "icn_class_attacker.webp",
    "지원형": "icn_class_supporter.webp",
    "방어형": "icn_class_defender.webp",
}
BURST_ICON = {
    "1": "icn_burst_01.webp",
    "2": "icn_burst_02.webp",
    "3": "icn_burst_03.webp",
    "A": "icn_burst_all.webp",
}
BURST_ORDER = ["1", "2", "3", "A"]
BURST_LABEL = {"1": "1단", "2": "2단", "3": "3단", "A": "올라운더"}
# icn_corp_01~05는 `scraper/cdn_fetch.py`의 CORP_MAP 순서와 같다 (다이아·M·T·창·미로).
CORP_ICON = {
    "엘리시온": "icn_corp_01.webp",
    "미실리스": "icn_corp_02.webp",
    "테트라": "icn_corp_03.webp",
    "필그림": "icn_corp_04.webp",
    "어브노말": "icn_corp_05.webp",
}
CORP_ORDER = list(CORP_ICON)
# 무기군은 전용 아이콘이 없어 텍스트 칩으로 표시한다.
WEAPON_ORDER = ["AR", "SMG", "SG", "SR", "RL", "MG"]


def portrait(name: str) -> str | None:
    """캐릭명 → image/ 초상화 상대 경로 (없으면 None)."""
    stem = name.replace(":", "_")
    for cand in (f"{stem}.webp", f"{name}.webp"):
        if (IMAGE / cand).exists():
            return f"image/{cand}"
    return None


def collect() -> tuple[list[dict], list[dict]]:
    """(파싱됨, 미파싱) 캐릭터 레코드 목록."""
    skills = json.loads(SKILLS.read_text(encoding="utf-8"))
    nikke = json.loads(NIKKE.read_text(encoding="utf-8"))
    parsed_names = {k for k in skills if k not in TEST_KEYS}

    done, todo = [], []
    for name, meta in nikke.items():
        if name in TEST_KEYS:
            continue
        rec = {
            "name": name,
            "burst": meta.get("burst_stage", "?"),
            "element": meta.get("element_code", "?"),
            "cls": meta.get("class", "?"),
            "corp": meta.get("manufacturer", "?"),
            "weapon": meta.get("weapon_type", "?"),
            "squad": meta.get("squad_name", ""),
            "img": portrait(name),
            "skills": len(skills.get(name, [])),
        }
        (done if name in parsed_names else todo).append(rec)

    orphan = sorted(parsed_names - set(nikke))
    if orphan:
        print(f"경고: 메타 없는 파싱 캐릭터 {orphan}")
    return done, todo


def card(rec: dict, dim: bool, idx: int = 0) -> str:
    name = html.escape(rec["name"])
    el, cls, burst, corp = rec["element"], rec["cls"], rec["burst"], rec["corp"]
    thumb = (
        f'<img class="portrait" src="{html.escape(rec["img"])}" alt="{name}" loading="lazy">'
        if rec["img"]
        else '<div class="portrait noimg">?</div>'
    )
    badges = []
    if burst in BURST_ICON:
        badges.append(f'<img class="badge" src="image/icon/{BURST_ICON[burst]}" alt="B{burst}">')
    else:
        badges.append(f'<span class="badge txt">{html.escape(burst)}</span>')
    if el in ELEMENT_ICON:
        badges.append(f'<img class="badge" src="image/icon/{ELEMENT_ICON[el]}" alt="{el}">')
    if cls in CLASS_ICON:
        badges.append(f'<img class="badge" src="image/icon/{CLASS_ICON[cls]}" alt="{cls}">')
    corp_badge = (
        f'<img class="badge corp" src="image/icon/{CORP_ICON[corp]}" alt="{corp}">'
        if corp in CORP_ICON
        else ""
    )
    meta = f'{BURST_LABEL.get(burst, burst)} · {el} · {cls} · {corp} · {rec["weapon"]}'
    if not dim:
        meta += f' · 스킬 {rec["skills"]}건'
    return (
        f'<figure class="card{" dim" if dim else ""}" data-i="{idx}" data-name="{name}" data-burst="{burst}" '
        f'data-element="{el}" data-class="{cls}" data-corp="{corp}" data-weapon="{rec["weapon"]}" '
        f'style="--el:{ELEMENT_COLOR.get(el, "#888")}" '
        f'title="{name}&#10;{html.escape(meta)}">'
        f'<div class="thumb">{thumb}<div class="badges">{"".join(badges)}</div>{corp_badge}'
        f'<span class="wchip">{html.escape(rec["weapon"])}</span></div>'
        f'<figcaption>{name}</figcaption></figure>'
    )


def sort_key(rec: dict):
    return (
        BURST_ORDER.index(rec["burst"]) if rec["burst"] in BURST_ORDER else 9,
        ELEMENT_ORDER.index(rec["element"]) if rec["element"] in ELEMENT_ORDER else 9,
        rec["name"],
    )


CSS = """
:root{--bg:#f7f7f9;--fg:#1b1c1f;--sub:#6b6e76;--card:#fff;--line:#e2e3e8}
@media (prefers-color-scheme:dark){:root{--bg:#15161a;--fg:#e9eaee;--sub:#9a9daa;--card:#1e2027;--line:#2c2f38}}
*{box-sizing:border-box}
body{margin:0;padding:24px 28px 60px;background:var(--bg);color:var(--fg);
 font:14px/1.5 "Pretendard","Malgun Gothic",system-ui,sans-serif}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--sub);font-size:13px;margin:0 0 18px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;position:sticky;top:0;z-index:5;
 padding:10px 0;background:var(--bg);border-bottom:1px solid var(--line);margin-bottom:18px}
.bar b{font-size:12px;color:var(--sub);margin-right:2px}
button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;
 padding:5px 12px;font-size:12px;cursor:pointer}
button.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
input[type=search]{border:1px solid var(--line);background:var(--card);color:var(--fg);
 border-radius:999px;padding:5px 12px;font-size:12px;min-width:150px}
.group{margin:0 0 26px}
.group h2{font-size:14px;margin:0 0 10px;color:var(--sub);font-weight:600}
.group h2 .n{font-weight:400;opacity:.7}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(92px,1fr))}
.card{margin:0;text-align:center}
.thumb{position:relative;aspect-ratio:1;border-radius:10px;overflow:hidden;background:var(--card);
 border:1px solid var(--line);border-bottom:3px solid var(--el)}
/* 크롭 위치: 보고서 썸네일과 동일 — 얼굴이 중심에 오게 */
.portrait{width:100%;height:100%;object-fit:cover;object-position:center 18%;display:block}
.noimg{display:flex;align-items:center;justify-content:center;height:100%;color:var(--sub);font-size:22px}
.badges{position:absolute;left:3px;top:3px;display:flex;flex-direction:column;gap:3px;
 padding:3px;border-radius:7px;background:rgba(0,0,0,.5);backdrop-filter:blur(2px)}
.badge{width:16px;height:16px;display:block}
.badge.txt{line-height:16px;font-size:10px;font-weight:700;color:#fff;text-align:center}
/* 기업은 좌측 배지단과 분리해 우상단에 — 배지 열이 4단이 되면 카드 높이를 넘는다 */
.badge.corp{position:absolute;right:3px;top:3px;padding:3px;width:22px;height:22px;
 border-radius:7px;background:rgba(0,0,0,.5);backdrop-filter:blur(2px)}
.wchip{position:absolute;right:3px;bottom:3px;padding:1px 5px;border-radius:6px;
 background:rgba(0,0,0,.55);color:#fff;font-size:10px;font-weight:700;letter-spacing:.3px}
figcaption{font-size:11px;margin-top:5px;line-height:1.3;word-break:keep-all;color:var(--fg)}
.card.dim .thumb{filter:grayscale(1);opacity:.42}
.card.dim figcaption{color:var(--sub)}
.card.hide,.group.hide{display:none}
details{margin-top:8px}
summary{cursor:pointer;color:var(--sub);font-size:13px;margin-bottom:12px}
"""

JS = """
const state={group:'burst',burst:'all',element:'all',cls:'all',corp:'all',weapon:'all',q:''};
const DATA={burst:'burst',element:'element',cls:'class',corp:'corp',weapon:'weapon'};

/* 그룹 기준이 바뀌면 카드 노드를 새 섹션으로 옮겨 다시 묶는다 (카드 자체는 재생성 안 함) */
function regroup(){
  for(const pool of ['done','todo']){
    const pane=document.getElementById(pool);
    if(!pane) continue;
    const cards=[...pane.querySelectorAll('.card')];
    const key=state.group, attr=DATA[key], buckets=new Map();
    for(const c of cards){
      const v=c.dataset[attr]||'?';
      if(!buckets.has(v)) buckets.set(v,[]);
      buckets.get(v).push(c);
    }
    for(const arr of buckets.values()) arr.sort((a,b)=>a.dataset.i-b.dataset.i);
    const known=(ORDER[key]||[]).filter(v=>buckets.has(v));
    const rest=[...buckets.keys()].filter(v=>!known.includes(v));
    pane.textContent='';
    for(const v of known.concat(rest)){
      const sec=document.createElement('section');
      sec.className='group';
      const h=document.createElement('h2');
      h.textContent=(pool==='todo'?'미파싱 · ':'')+((LABEL[key]||{})[v]||v)+' ';
      const n=document.createElement('span'); n.className='n'; n.textContent=buckets.get(v).length;
      h.appendChild(n); sec.appendChild(h);
      const grid=document.createElement('div'); grid.className='grid';
      buckets.get(v).forEach(c=>grid.appendChild(c));
      sec.appendChild(grid); pane.appendChild(sec);
    }
  }
  apply();
}
function apply(){
  document.querySelectorAll('.card').forEach(c=>{
    const ok=(state.burst==='all'||c.dataset.burst===state.burst)
      &&(state.element==='all'||c.dataset.element===state.element)
      &&(state.cls==='all'||c.dataset.class===state.cls)
      &&(state.corp==='all'||c.dataset.corp===state.corp)
      &&(state.weapon==='all'||c.dataset.weapon===state.weapon)
      &&(!state.q||c.dataset.name.toLowerCase().includes(state.q));
    c.classList.toggle('hide',!ok);
  });
  document.querySelectorAll('.group').forEach(g=>{
    const vis=g.querySelectorAll('.card:not(.hide)').length;
    g.classList.toggle('hide',vis===0);
    g.querySelector('.n').textContent=vis;
  });
}
document.querySelectorAll('button[data-key]').forEach(b=>b.onclick=()=>{
  const k=b.dataset.key;state[k]=b.dataset.val;
  document.querySelectorAll(`button[data-key="${k}"]`).forEach(o=>o.classList.toggle('on',o===b));
  k==='group'?regroup():apply();
});
document.querySelector('#q').oninput=e=>{state.q=e.target.value.trim().toLowerCase();apply();};
regroup();
"""


def filter_bar() -> str:
    def row(label: str, key: str, vals: list[tuple[str, str]]) -> str:
        btns = f'<button data-key="{key}" data-val="all" class="on">전체</button>'
        btns += "".join(
            f'<button data-key="{key}" data-val="{html.escape(v)}">{html.escape(t)}</button>'
            for v, t in vals
        )
        return f"<b>{label}</b>{btns}"

    group_btns = "".join(
        f'<button data-key="group" data-val="{k}"{" class=\'on\'" if k == "burst" else ""}>{t}</button>'
        for k, t in (("burst", "버스트"), ("element", "속성"), ("corp", "기업"), ("weapon", "무기군"), ("cls", "클래스"))
    )
    return (
        '<div class="bar">'
        + f"<b>묶기</b>{group_btns}"
        + '</div><div class="bar">'
        + row("버스트", "burst", [(b, BURST_LABEL[b]) for b in BURST_ORDER])
        + row("속성", "element", [(e, e) for e in ELEMENT_ORDER])
        + row("기업", "corp", [(c, c) for c in CORP_ORDER])
        + row("무기군", "weapon", [(w, w) for w in WEAPON_ORDER])
        + row("클래스", "cls", [(c, c) for c in CLASS_ICON])
        + '<input type="search" id="q" placeholder="이름 검색">'
        + "</div>"
    )


def build() -> str:
    done, todo = collect()
    done.sort(key=sort_key)
    todo.sort(key=sort_key)

    # 카드는 평평하게 심고, 섹션 묶기는 JS regroup()이 담당한다 (묶기 기준 전환용).
    done_html = f'<div id="done">{"".join(card(r, False, i) for i, r in enumerate(done))}</div>'
    todo_html = (
        f"<details><summary>미파싱 {len(todo)}명 보기</summary>"
        f'<div id="todo">{"".join(card(r, True, i) for i, r in enumerate(todo))}</div></details>'
    )
    order_js = json.dumps(
        {
            "burst": BURST_ORDER,
            "element": ELEMENT_ORDER,
            "corp": CORP_ORDER,
            "weapon": WEAPON_ORDER,
            "cls": list(CLASS_ICON),
        },
        ensure_ascii=False,
    )
    label_js = json.dumps({"burst": {b: f"버스트 {BURST_LABEL[b]}" for b in BURST_ORDER}}, ensure_ascii=False)

    total = len(done) + len(todo)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NIKKE 파싱 로스터</title><style>{CSS}</style></head><body>
<h1>파싱 로스터</h1>
<p class="sub">파싱 완료 <b>{len(done)}</b> / 전체 {total}명 · 정본 <code>data/parsed_skills.json</code>
 · <code>python -m context.roster</code>로 재생성</p>
{filter_bar()}
{done_html}
{todo_html}
<script>const ORDER={order_js};const LABEL={label_js};{JS}</script>
</body></html>
"""


def main() -> None:
    OUT.write_text(build(), encoding="utf-8")
    print(f"생성: {OUT}")
    if "--open" in sys.argv:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
