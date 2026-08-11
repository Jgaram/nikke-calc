"""육성 효율 보고서 HTML 렌더러.

딜량 보고서(`report_html`)의 CSS 토큰·이미지 인라인·운용 조건 블록을 그대로 쓰고,
Δ를 보여주는 부분만 여기서 만든다. **계산은 하지 않는다** — `growth.analyze()`가
낸 값을 그리기만 한다.

시각화 규칙
  · Δ는 부호가 뜻을 가지므로 **발산형**이다 — 0을 가운데 두고 양·음이 서로 다른 색,
    유의하지 않은 값(|Δ| ≤ 2·표준오차)은 중립 회색 + `판정 불가` 라벨.
    색만으로 뜻을 전하지 않도록 숫자와 라벨을 언제나 같이 적는다.
  · 덱 총딜 Δ와 자기 딜 Δ는 **한 축에 겹치지 않는다.** 두 열로 나란히 놓는다
    (한 막대에 두 지표를 섞으면 발산 색과 계열 색이 충돌한다).
  · 오차 막대는 ±2·표준오차. 막대 끝에 얹는다.
"""

from __future__ import annotations

import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_REPORT = os.path.join(_ROOT, ".agent", "skills", "report")
for _p in (_ROOT, _REPORT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from report_html import (  # noqa: E402
    _CSS, _enemy_desc, _esc, _img_css, _kor, _ops, _squad_strip,
)

# 발산형 막대에 얹는 CSS. report의 토큰(--good/--bad/--muted/--grid)을 그대로 쓴다 —
# 라이트/다크 양쪽에서 이미 검증된 값이라 여기서 새 색을 만들지 않는다.
_EXTRA_CSS = """
.dgrid { display: grid; grid-template-columns: minmax(150px,1.3fr) 1fr 1fr; gap: 0 14px;
         align-items: center; font-variant-numeric: tabular-nums; }
.dgrid > .hd { font-size: 11.5px; color: var(--muted); padding: 0 0 6px;
               border-bottom: 1px solid var(--border); margin-bottom: 6px; }
.dgrid > .lb { font-size: 13px; padding: 5px 0; min-width: 0; }
.dgrid > .lb .sub2 { color: var(--muted); font-size: 11.5px; }
.dgrid > .cell { padding: 5px 0; }
/* 0을 가운데 둔 발산 트랙. 양수는 오른쪽, 음수는 왼쪽으로 자란다. */
.track { position: relative; height: 20px; background: var(--grid);
         border-radius: 4px; overflow: hidden; }
.track .zero { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px;
               background: var(--axis); }
.track .fill { position: absolute; top: 3px; bottom: 3px; border-radius: 4px; }
.track .fill.pos { background: var(--good); left: 50%; }
.track .fill.neg { background: var(--bad); }
.track .fill.nil { background: var(--muted); opacity: .5; }
/* ±2·표준오차. 막대 위에 얹히므로 표면색 테두리로 띄운다. */
.track .err { position: absolute; top: 5px; bottom: 5px;
              border-left: 1px solid var(--surface); border-right: 1px solid var(--surface);
              background: color-mix(in srgb, var(--ink) 22%, transparent); }
.dnum { font-size: 12.5px; margin-top: 3px; color: var(--ink2); }
.dnum b { color: var(--ink); font-size: 13px; }
.dnum .nil { color: var(--muted); }
.tag { display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 999px;
       font-size: 10.5px; background: var(--grid); color: var(--muted);
       border: 1px solid var(--border); }
.axisbox { border-top: 1px solid var(--border); padding: 14px 0 4px; }
.axisbox:first-child { border-top: none; padding-top: 2px; }
.axisname { font-weight: 640; font-size: 14px; margin-bottom: 8px; }
.axisname .who { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 8px; }
table.g { border-collapse: collapse; width: 100%; font-size: 12.5px;
          font-variant-numeric: tabular-nums; }
table.g th, table.g td { padding: 6px 10px; text-align: right;
                         border-bottom: 1px solid var(--border); }
table.g th:first-child, table.g td:first-child { text-align: left; }
table.g th { color: var(--muted); font-weight: 500; font-size: 11.5px; }
table.g td.pos { color: var(--good); }
table.g td.neg { color: var(--bad); }
table.g td.nil { color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--ink2);
          margin-bottom: 10px; }
.legend i { display: inline-block; width: 11px; height: 11px; border-radius: 3px;
            margin-right: 5px; vertical-align: -1px; }
.basecard { display: flex; flex-wrap: wrap; gap: 22px; align-items: center; }
.basecard .num { font-variant-numeric: tabular-nums; }
.basecard .num b { font-size: 20px; letter-spacing: -0.01em; }
.basecard .num span { display: block; color: var(--muted); font-size: 11.5px; }
"""


def _pct2(x: float) -> str:
    return f"{x:+.2f}%"


def _cls(d: dict) -> str:
    if not d["sig"]:
        return "nil"
    return "pos" if d["mean"] > 0 else "neg"


def _bar(d: dict, scale: float) -> str:
    """발산형 막대 하나. `scale`은 이 카드에서 100%에 해당하는 Δ% 절대값."""
    pct, se = d["pct"], d["se_pct"]
    w = min(abs(pct) / scale * 50, 50) if scale else 0.0
    kind = _cls(d)
    if kind == "nil":
        fill = (f'<div class="fill nil" style="left:{50 - w if pct < 0 else 50}%;'
                f'width:{w:.3f}%"></div>')
    elif pct >= 0:
        fill = f'<div class="fill pos" style="width:{w:.3f}%"></div>'
    else:
        fill = f'<div class="fill neg" style="left:{50 - w:.3f}%;width:{w:.3f}%"></div>'

    err = ""
    if se and scale:
        lo = (pct - 2 * se) / scale * 50 + 50
        hi = (pct + 2 * se) / scale * 50 + 50
        lo, hi = max(0.0, min(100.0, lo)), max(0.0, min(100.0, hi))
        err = f'<div class="err" style="left:{lo:.3f}%;width:{max(hi - lo, 0.4):.3f}%"></div>'

    tag = '' if d["sig"] else '<span class="tag">판정 불가</span>'
    num = (f'<div class="dnum"><b class="{kind}">{_esc(_pct2(pct))}</b> '
           f'<span class="{"nil" if kind == "nil" else ""}">± {2 * se:.2f}%p</span>{tag}</div>')
    return f'<div class="cell"><div class="track"><div class="zero"></div>{fill}{err}</div>{num}</div>'


def _legend(scale: tuple[float, float]) -> str:
    return ('<div class="legend">'
            '<span><i style="background:var(--good)"></i>기준보다 증가</span>'
            '<span><i style="background:var(--bad)"></i>기준보다 감소</span>'
            '<span><i style="background:var(--muted);opacity:.5"></i>판정 불가 '
            '(차이가 ±2·표준오차 안)</span>'
            '<span>가는 세로 띠 = ±2·표준오차</span>'
            f'<span>막대 길이는 <b>열 안에서만</b> 비교된다 — 왼쪽 열 끝 '
            f'{scale[0]:.1f}%, 오른쪽 열 끝 {scale[1]:.1f}%</span></div>')


def _axis_block(ax: dict, scale: tuple[float, float], subject: str) -> str:
    who = "" if ax["target"] == subject else f'<span class="who">대상 {_esc(ax["target"])}</span>'
    note = f'<div class="sub2" style="color:var(--muted);font-size:12px">{_esc(ax["note"])}</div>' \
        if ax["note"] else ""
    rows = ['<div class="hd">단계</div><div class="hd">덱 전체 딜 Δ</div>'
            '<div class="hd">해당 캐릭 딜 Δ</div>']
    for st in ax["steps"]:
        if st["base"]:
            rows.append(f'<div class="lb">{_esc(st["label"])}'
                        f'<span class="tag">기준</span>'
                        f'<div class="sub2">{_esc(_kor(st["total"]))} · '
                        f'본인 {_esc(_kor(st["self"]))}</div></div>'
                        f'<div class="cell"><div class="track"><div class="zero"></div></div>'
                        f'<div class="dnum"><b>0.00%</b></div></div>'
                        f'<div class="cell"><div class="track"><div class="zero"></div></div>'
                        f'<div class="dnum"><b>0.00%</b></div></div>')
            continue
        inc = ""
        if st["step_deck_pct"] is not None:
            inc = f'<div class="sub2">직전 단계 대비 덱 {st["step_deck_pct"]:+.2f}%</div>'
        rows.append(f'<div class="lb">{_esc(st["label"])}{inc}</div>'
                    f'{_bar(st["delta"]["deck"], scale[0])}'
                    f'{_bar(st["delta"]["self"], scale[1])}')
    return (f'<div class="axisbox"><div class="axisname">{_esc(ax["name"])}{who}</div>{note}'
            f'<div class="dgrid">{"".join(rows)}</div></div>')


def _rank_table(rank: list[dict], subject: str) -> str:
    rows = []
    for r in rank:
        dk, sf = r["delta"]["deck"], r["delta"]["self"]
        who = "" if r["target"] == subject else f' <span class="tag">{_esc(r["target"])}</span>'
        rows.append(
            f'<tr><td>{_esc(r["axis"])} → {_esc(r["label"])}{who}</td>'
            f'<td class="{_cls(dk)}">{_pct2(dk["pct"])}</td>'
            f'<td class="nil">± {2 * dk["se_pct"]:.2f}</td>'
            f'<td class="{_cls(sf)}">{_pct2(sf["pct"])}</td>'
            f'<td class="nil">± {2 * sf["se_pct"]:.2f}</td>'
            f'<td>{_esc(_kor(dk["mean"]))}</td></tr>')
    return (f'<table class="g"><thead><tr><th>투자</th><th>덱 딜 Δ</th><th>±2SE</th>'
            f'<th>본인 딜 Δ</th><th>±2SE</th><th>덱 딜 증가분</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def _combo_table(combos: list[dict]) -> str:
    if not combos:
        return ""
    rows = []
    for cb in combos:
        parts = " + ".join(f'{_esc(p["axis"])} {_esc(p["label"])} ({p["deck_pct"]:+.2f}%)'
                           for p in cb["parts"])
        gap = cb["gap_deck"]
        cls = "pos" if gap > 0.05 else ("neg" if gap < -0.05 else "nil")
        rows.append(
            f'<tr><td>{_esc(cb["label"])}<div class="sub2" style="color:var(--muted);'
            f'font-size:11.5px">{parts}</div></td>'
            f'<td class="{_cls(cb["delta"]["deck"])}">{_pct2(cb["delta"]["deck"]["pct"])}</td>'
            f'<td class="nil">{cb["sum_deck"]:+.2f}%</td>'
            f'<td class="{cls}">{gap:+.2f}%p</td>'
            f'<td class="{_cls(cb["delta"]["self"])}">{_pct2(cb["delta"]["self"]["pct"])}</td>'
            f'<td class="nil">{cb["sum_self"]:+.2f}%</td>'
            f'<td>{cb["gap_self"]:+.2f}%p</td></tr>')
    return (f'<h2>조합 검증 — 같이 올리면 합보다 큰가</h2><div class="card">'
            f'<p class="sub" style="margin:0 0 10px">실제로 둘 다 올려 돌린 값과, 각각 따로 올린 '
            f'Δ를 그냥 더한 값의 차이다. 차이가 +면 상승 효과가 서로 곱해진 것이고, '
            f'−면 한쪽이 다른 쪽에 먹혔다는 뜻이다 (버프 상한·오버킬).</p>'
            f'<table class="g"><thead><tr><th>조합</th><th>덱 실측 Δ</th><th>덱 단순 합</th>'
            f'<th>차이</th><th>본인 실측 Δ</th><th>본인 단순 합</th><th>차이</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _cross_deck(result: dict) -> str:
    """덱 간 대조 — 같은 투자가 덱에 따라 얼마나 다른가."""
    decks = result["decks"]
    if len(decks) < 2:
        return ""
    keys: list[tuple[str, str]] = []
    for d in decks:
        for r in d["rank"]:
            if (r["axis"], r["label"]) not in keys:
                keys.append((r["axis"], r["label"]))
    idx = [{f'{r["axis"]}|{r["label"]}': r for r in d["rank"]} for d in decks]

    head = "".join(f'<th>{_esc(d["name"])}</th>' for d in decks)
    rows = []
    for ax, lb in keys:
        cells = []
        for m in idx:
            r = m.get(f"{ax}|{lb}")
            if not r:
                cells.append('<td class="nil">—</td>')
                continue
            dk = r["delta"]["deck"]
            cells.append(f'<td class="{_cls(dk)}">{_pct2(dk["pct"])}</td>')
        rows.append(f'<tr><td>{_esc(ax)} → {_esc(lb)}</td>{"".join(cells)}</tr>')
    return (f'<h2>덱 간 대조 — 덱 전체 딜 Δ</h2><div class="card">'
            f'<p class="sub" style="margin:0 0 10px">같은 투자가 덱에 따라 얼마나 다르게 '
            f'돌아오는지. 여기서 크게 갈리면 "이 캐릭터를 어디에 쓰느냐"가 육성 순서보다 '
            f'먼저 정해져야 한다는 뜻이다.</p>'
            f'<table class="g"><thead><tr><th>투자</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _raw_table(cases: list[dict], seeds: list) -> str:
    head = "".join(f"<th>#{i + 1}</th>" for i in range(len(seeds)))
    rows = "".join(
        f'<tr><td>{_esc(c["name"])}</td>'
        + "".join(f'<td>{_esc(_kor(r["squad_total"]))}</td>' for r in c["runs"])
        + "</tr>"
        for c in cases)
    return (f'<details><summary>회차별 총딜 (케이스 {len(cases)}개)</summary>'
            f'<div class="detail-body"><table class="g"><thead><tr><th>케이스</th>{head}</tr>'
            f'</thead><tbody>{rows}</tbody></table></div></details>')


def render_html(spec: dict, cases: list[dict], result: dict, seeds: list) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    subject = result["subject"]
    decks = result["decks"]

    mode_label = {"skill": "스킬 조사", "option": "옵션 조사"}.get(result.get("mode", ""), "")
    chips = [f"대상 {subject}", mode_label, f"덱 {len(decks)}개", f"케이스 {len(cases)}개",
             f"케이스당 {len(seeds)}회", f"전투 {cases[0]['duration']:.0f}초" if cases else "",
             f"생성 {now}"]
    chip_html = "".join(f'<span class="chip">{_esc(t)}</span>' for t in chips if t)

    img_css = _img_css([nm for c in cases for nm in c["squad"]])

    by_deck: dict[str, list[dict]] = {}
    for c in cases:
        by_deck.setdefault(c.get("group", ""), []).append(c)

    # 막대 길이는 **전 덱 통틀어** 최대 |Δ%| 기준 — 탭을 바꿔도 길이가 서로 비교된다.
    # 단 덱 딜과 자기 딜은 **열마다 따로** 잰다. 버퍼는 자기 딜 Δ가 한 자릿수 %인데
    # 덱 딜 Δ는 1% 안팎이라, 한 배율로 묶으면 정작 중요한 덱 딜 막대가 실처럼 얇아진다.
    # 두 열의 배율이 다르다는 사실은 범례에 수치로 적는다.
    def _scale(metric: str) -> float:
        return max((abs(r["delta"][metric]["pct"]) for d in decks for r in d["rank"]),
                   default=1.0) or 1.0

    scale = (_scale("deck"), _scale("self"))

    tabs, panels = [], []
    for i, d in enumerate(decks):
        gcases = by_deck.get(d["name"], [])
        tabs.append(f'<button class="tab{" on" if i == 0 else ""}" '
                    f'data-panel="p{i}">{_esc(d["name"])}</button>')
        # 운용 조건은 **기준 케이스만** 넣어 만든다. 축이 바꾸는 값은 케이스마다 다른 게
        # 당연하고 아래 축별 상세가 정본이라, 여기 섞으면 "무엇이 기준인가"가 흐려진다.
        base_case = next((c for c in gcases if c["name"] == d["base_case"]), None)
        ops_top, _ = _ops(spec, [base_case] if base_case else gcases)
        base_note = f'<p class="sub">{_esc(d["note"])}</p>' if d["note"] else ""

        base_card = (
            f'<div class="card basecard">'
            f'<div style="flex:0 0 290px">{_squad_strip(d["squad"])}</div>'
            f'<div class="num"><b>{_esc(_kor(d["base_total"]))}</b>'
            f'<span>기준 덱 총딜 (CV {d["base_cv"]:.2f}%)</span></div>'
            f'<div class="num"><b>{_esc(_kor(d["base_self"]))}</b>'
            f'<span>{_esc(subject)} 본인 딜 · 덱의 '
            f'{d["base_self"] / d["base_total"] * 100 if d["base_total"] else 0:.1f}%</span></div>'
            f'<div class="num"><b>{d["burst_count"]:.1f}</b><span>풀버스트 횟수</span></div>'
            f'</div>')

        panels.append(f"""
<div class="panel" id="p{i}"{'' if i == 0 else ' hidden'}>
  <div class="boss"><b>랩쳐</b> {_esc(_enemy_desc(d["enemy"]))}</div>
  {ops_top}
  {base_note}

  <h2>기준</h2>
  {base_card}

  <h2>효율 랭킹 — 무엇부터 올릴까</h2>
  <div class="card">
    <p class="sub" style="margin:0 0 10px">기준에서 한 항목만 올렸을 때의 변화. 덱 전체 딜
    Δ 내림차순이다. ±2SE가 Δ보다 크면 이 회차 수로는 방향조차 말할 수 없다.</p>
    {_rank_table(d["rank"], subject)}
  </div>

  <h2>축별 상세</h2>
  <div class="card">
    {_legend(scale)}
    {''.join(_axis_block(ax, scale, subject) for ax in d["axes"])}
  </div>

  {_combo_table(d["combos"])}
</div>""")

    tab_html = f'<div class="tabs">{"".join(tabs)}</div>' if len(decks) > 1 else ""
    note = f'<p class="sub">{_esc(spec["note"])}</p>' if spec.get("note") else ""
    base_json = _esc(json.dumps(result["baseline"], ensure_ascii=False, indent=2))
    # 무엇을 고정하고 무엇을 움직였는지. 이 줄이 없으면 Δ가 무엇 대비인지 알 수 없다.
    mnotes = "".join(f"<li>{_esc(n)}</li>" for n in result.get("mode_notes") or [])
    mblock = f'<div class="ops"><b>조사 범위</b><ul>{mnotes}</ul></div>' if mnotes else ""

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(spec['title'])} — 육성 효율 보고서</title>
<style>{_CSS}
{_EXTRA_CSS}
{img_css}</style></head>
<body><div class="wrap">
  <h1>{_esc(spec['title'])}</h1>
  {note}
  <div class="chips">{chip_html}</div>
  {mblock}
  {tab_html}
  {''.join(panels)}

  {_cross_deck(result)}

  <h2>원자료 · 설정</h2>
  <div class="card" style="padding-top:0">
    {_raw_table(cases, seeds)}
    <details><summary>기준 육성 오버라이드 ({_esc(subject)})</summary>
      <div class="detail-body"><pre>{base_json}</pre></div></details>
  </div>

  <p class="foot">
    Δ는 <b>페어드 델타</b>다 — 시드별로 먼저 기준과의 차를 구하고 그 평균을 냈다. 케이스별
    평균끼리 빼는 것보다 난수 노이즈가 훨씬 작아, 총딜 CV(약 1%)보다 작은 차이도 잡힌다.
    ±는 그 차이의 2·표준오차이며, Δ가 이 안에 들면 <b>판정 불가</b>로 적었다.
    회차를 늘리면 ±가 √n에 반비례해 줄어든다.
  </p>
</div>
<script>
document.querySelectorAll('.tab').forEach(function (t) {{
  t.addEventListener('click', function () {{
    document.querySelectorAll('.tab').forEach(function (x) {{ x.classList.remove('on'); }});
    document.querySelectorAll('.panel').forEach(function (p) {{ p.hidden = true; }});
    t.classList.add('on');
    document.getElementById(t.dataset.panel).hidden = false;
  }});
}});
</script>
</body></html>"""
