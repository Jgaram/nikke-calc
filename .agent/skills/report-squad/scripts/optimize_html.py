"""솔로레이드 N스쿼드 보고서(최적화·지정 편성)의 HTML 렌더러.

한 해(solution)는 초상화 N×5 블록 하나로 읽는다. 각 줄은 초상화 5개와 총딜만
두고, 운용·CV·FB 같은 부가 문구는 블록 아래 각주로 내린다 — 스쿼드 사이 간격이
벌어지면 25명을 한눈에 비교할 수 없기 때문이다.
"""

from __future__ import annotations

import base64
import functools
import html
import io
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from report_html import _burst_pattern_text, _seq_text  # noqa: E402


def _kor(value: float) -> str:
    return f"{value / 1e8:,.2f}억"


def _esc(value: Any) -> str:
    return html.escape(str(value))


@functools.lru_cache(maxsize=256)
def _image_data(name: str) -> str | None:
    normalized = name.replace(" ", "").replace(":", "").replace("_", "").lower()
    image_dir = ROOT / "image"
    if not image_dir.is_dir():
        return None
    for path in image_dir.iterdir():
        stem = path.stem.replace(" ", "").replace(":", "").replace("_", "").lower()
        if stem == normalized and path.suffix.lower() in {".webp", ".png", ".jpg", ".jpeg"}:
            from PIL import Image

            image = Image.open(path).convert("RGB")
            side = min(image.width, image.height)
            top = min(int(image.height * 0.18), image.height - side)
            image = image.crop((0, top, side, top + side)).resize((64, 64), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=78)
            return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode()
    return None


def _operation_text(squad: dict[str, Any]) -> str:
    """스쿼드의 버스트 운용을 한 줄로. 기본 운용이면 `버스트순서 왼쪽부터`."""
    cfg = squad.get("config", {})
    ops = []
    if cfg.get("no_burst_char"):
        ops.append(f'{cfg["no_burst_char"]} 버스트 미사용')
    for name, pattern in (cfg.get("burst_pattern") or {}).items():
        if pattern == "every:1":
            pattern_text = "매 사이클"
        elif pattern == [1]:
            pattern_text = "첫 사이클만"
        else:
            pattern_text = _burst_pattern_text(pattern)
        ops.append(f"{name} {pattern_text}")
    if sequence_text := _seq_text(squad["squad"], cfg.get("burst_sequence") or []):
        ops.append(sequence_text)
    return " · ".join(ops) if ops else "버스트순서 왼쪽부터"


def _squad_row(squad: dict[str, Any]) -> str:
    portraits = []
    for name in squad["squad"]:
        image = _image_data(name)
        if image:
            media = f'<img src="{image}" alt="{_esc(name)}" title="{_esc(name)}">'
        else:
            media = f'<span class="missing" title="{_esc(name)}">?</span>'
        portraits.append(media)
    stats = squad.get("total", {})
    # 범위는 관측 최소·최대가 아니라 평균 ± 표준편차다 — 시드 수가 적어 극단값이 튄다.
    mean, std = float(stats["mean"]), float(stats.get("std", 0))
    span = f'{(mean - std) / 1e8:,.2f}~{(mean + std) / 1e8:,.2f}억'
    return (f'<div class="row"><div class="chars">{"".join(portraits)}</div>'
            f'<div class="damage">{_kor(mean)}<span>{span}</span></div></div>')


def _squad_note(index: int, squad: dict[str, Any]) -> str:
    tail = f'FB {float(squad.get("burst_count", 0)):.0f}'
    if squad.get("simulated"):
        tail += " · 신규 시뮬"
    names = " / ".join(squad["squad"])
    operation = _operation_text(squad)
    # 각주 한 줄이 넘치면 잘라 둔다 — 25명 블록의 높이가 흔들리면 비교가 어려워진다.
    return (f'<li title="{_esc(f"{names} — {operation} · {tail}")}"><i>{index}</i>{_esc(names)}'
            f'<em>{_esc(operation)} · {tail}</em></li>')


def _solution_block(solution: dict[str, Any], best: float, *, pinned: bool) -> str:
    objective = float(solution["total"]["objective"])
    rows = "".join(_squad_row(squad) for squad in solution["squads"])
    notes = "".join(_squad_note(i, s) for i, s in enumerate(solution["squads"], start=1))
    if pinned:
        head = '<span class="rank">지정</span>'
        delta_text = "" if best <= 0 else f"최적해 대비 {_kor(objective - best)}"
    else:
        head = f'<span class="rank">#{solution["rank"]}</span>'
        delta_text = "최고" if solution["rank"] == 1 else _kor(objective - best)
    # 합계에는 평균만 둔다. 편차는 스쿼드별로 읽는 값이라 각 줄 옆에 붙어 있다.
    return f'''<section class="solution">
      <header><div>{head}<b>{_kor(objective)}</b></div>
      <div class="delta">{_esc(delta_text)}</div></header>
      <div class="rows">{rows}</div><ol class="notes">{notes}</ol>
    </section>'''


def _variant_panel(variant: dict[str, Any], index: int, reference: float) -> str:
    pinned = bool(variant.get("pinned"))
    best = float(variant["solutions"][0]["total"]["objective"])
    blocks = "".join(
        _solution_block(solution, reference if pinned else best, pinned=pinned)
        for solution in variant["solutions"]
    )

    target = variant.get("target", {})
    enemy = target.get("enemy", {})
    chips = [
        f'적 코드 {_esc(enemy.get("code", "미지정"))}',
        "코어" if enemy.get("core_px") else "비코어",
        "파츠" if enemy.get("has_parts") else "노파츠",
        f'{float(target.get("config", {}).get("duration", 0)):.0f}초',
    ]
    if pinned:
        meta = variant.get("pinned_meta", {})
        chips.append(f'지정 편성 {len(variant["solutions"][0]["squads"])}스쿼드')
        chips.append(f'캐시 재사용 {meta.get("reused", 0)}개 · 신규 시뮬 {meta.get("simulated", 0)}개')
    else:
        if excluded := variant.get("exclude_members", []):
            chips.append(f'제외 {" · ".join(excluded)}')
        else:
            chips.append("캐릭터 제외 없음")
        chips.append(f'후보 {variant["candidate_counts"]["unique"]}개')
        chips.append(f'탐색 상태 {variant["search"]["explored_nodes"]:,}개')
    chip_html = "".join(f'<span class="chip">{_esc(text)}</span>' for text in chips)

    source_rows = "".join(
        f'<tr><td>{_esc(src["title"])}</td><td>{src["loaded"]}</td><td>{src["accepted"]}</td></tr>'
        for src in variant.get("sources", [])
    )
    table = (f'<table><thead><tr><th>후보 보고서</th><th>전체</th><th>채택</th></tr></thead>'
             f'<tbody>{source_rows}</tbody></table>') if source_rows else ""
    active = " active" if index == 0 else ""
    return (f'<section class="variant-panel{active}" data-panel="{index}">'
            f'<section class="meta"><div class="chips">{chip_html}</div>{table}</section>'
            f'{blocks}</section>')


def render_html(output: dict[str, Any]) -> str:
    variants = output.get("variants") or [output]
    # 지정 편성 패널의 기준선은 같은 보고서 안의 최적화 해다. 최적화가 없으면 비교하지 않는다.
    reference = next(
        (float(v["solutions"][0]["total"]["objective"]) for v in variants if not v.get("pinned")),
        0.0,
    )
    tabs = "".join(
        f'<button class="tab{" active" if index == 0 else ""}" data-tab="{index}" '
        f'type="button">{_esc(variant["name"])}</button>'
        for index, variant in enumerate(variants)
    )
    panels = "".join(_variant_panel(v, i, reference) for i, v in enumerate(variants))
    optimized = "기존 계산 결과 안에서 캐릭터 중복 없는 스쿼드 조합의 평균 총딜 합을 정확 최적화했다."
    if all(v.get("pinned") for v in variants):
        lead = "지정한 편성의 총딜을 계산했다. 후보 캐시에 없는 스쿼드만 새로 시뮬했다."
    elif any(v.get("pinned") for v in variants):
        lead = f"{optimized} 지정 편성 탭은 같은 조건에서 최적해와 나란히 비교한다."
    else:
        lead = optimized
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(output["title"])}</title>
<style>
:root{{--bg:#f6f6f3;--card:#fff;--ink:#151515;--muted:#6d6b65;--line:#deddd6;--blue:#2a78d6;--soft:#eaf2fc}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--card:#1c1c1b;--ink:#f5f5f3;--muted:#aaa79f;--line:#343431;--blue:#61a3ef;--soft:#152943}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,"Malgun Gothic",sans-serif}}
.wrap{{max-width:1180px;margin:auto;padding:34px 22px 80px}}h1{{font-size:25px;margin:0 0 6px}}.lead{{color:var(--muted);margin:0 0 18px}}
.tabs{{display:flex;gap:8px;margin:0 0 16px;border-bottom:1px solid var(--line)}}.tab{{appearance:none;border:0;border-bottom:3px solid transparent;background:transparent;color:var(--muted);font:inherit;font-weight:700;padding:10px 14px;cursor:pointer}}.tab.active{{color:var(--blue);border-bottom-color:var(--blue)}}.variant-panel{{display:none}}.variant-panel.active{{display:block}}
.meta{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:20px}}.chips{{display:flex;gap:7px;flex-wrap:wrap}}
.chip{{background:var(--soft);border-radius:999px;padding:4px 10px}}table{{width:100%;border-collapse:collapse;margin-top:12px;font-size:12px}}td,th{{padding:6px;border-top:1px solid var(--line);text-align:left}}th{{color:var(--muted)}}
.solution{{margin:0 0 26px}}.solution>header{{display:flex;justify-content:space-between;align-items:end;margin-bottom:6px}}.solution>header b{{font-size:21px}}.rank{{color:var(--blue);font-weight:800;margin-right:9px}}.delta{{color:var(--muted)}}
.rows{{display:grid;gap:2px;width:max-content;max-width:100%}}.row{{display:flex;align-items:center;gap:12px}}
.chars{{display:grid;grid-template-columns:repeat(5,56px);gap:2px}}.chars img,.missing{{display:block;width:56px;height:56px;object-fit:cover;object-position:center 18%;border-radius:3px;background:var(--soft)}}.missing{{display:grid;place-items:center}}
.damage{{font-size:16px;font-weight:800;white-space:nowrap}}.damage span{{margin-left:8px;font-size:11.5px;font-weight:400;color:var(--muted)}}
.notes{{list-style:none;margin:7px 0 0;padding:0;display:grid;gap:1px;color:var(--muted);font-size:11.5px;line-height:1.45}}
.notes li{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.notes i{{display:inline-block;min-width:14px;font-style:normal;font-weight:700;color:var(--ink)}}.notes em{{font-style:normal;margin-left:7px}}
@media(max-width:680px){{.rows{{width:auto}}.chars{{grid-template-columns:repeat(5,minmax(0,1fr));flex:1}}.chars img,.missing{{width:100%;height:auto;aspect-ratio:1}}}}
</style></head><body><main class="wrap"><h1>{_esc(output["title"])}</h1><p class="lead">{lead}</p>
<nav class="tabs" aria-label="조건">{tabs}</nav>{panels}</main>
<script>document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{{document.querySelectorAll('.tab,.variant-panel').forEach(el=>el.classList.remove('active'));btn.classList.add('active');document.querySelector(`.variant-panel[data-panel="${{btn.dataset.tab}}"]`).classList.add('active')}}));</script></body></html>'''
