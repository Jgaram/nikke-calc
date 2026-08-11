"""기존 딜량 보고서 캐시에서 솔로레이드 5스쿼드 최적 조합을 찾는다.

각 후보 스쿼드의 평균 총딜을 가중치로 보고, 캐릭터가 겹치지 않는 정확히 N개
스쿼드를 고르는 weighted set packing 문제를 분기 한정법으로 정확히 푼다.
새 시뮬레이션은 실행하지 않는다.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import functools
import heapq
import html
import io
import json
import math
import os
import statistics
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
REPORT_TOOL_DIR = Path(__file__).resolve().parent
if str(REPORT_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(REPORT_TOOL_DIR))

from report_html import _burst_pattern_text, _seq_text  # noqa: E402
from report_workspace import (  # noqa: E402
    WORK_DIR, data_path as work_data_path, output_path, preserve_spec,
    slug_from_spec, write_index, write_manifest,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _matches(actual: dict, required: dict) -> bool:
    return all(actual.get(key) == value for key, value in required.items())


def _stats(values: list[float]) -> dict[str, float | int]:
    mean = statistics.fmean(values) if values else 0.0
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "mean": mean,
        "std": std,
        "cv": std / mean * 100 if mean else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
        "n": len(values),
    }


@dataclass
class Candidate:
    id: str
    name: str
    squad: tuple[str, ...]
    damage: float
    total: dict[str, Any]
    burst_count: float
    config: dict[str, Any]
    enemy: dict[str, Any]
    runs: dict[int, float]
    source: str
    source_index: int
    signature: str
    mask: int = 0
    provenance: list[dict[str, Any]] = field(default_factory=list)


def _load_candidates(spec: dict, spec_path: Path) -> tuple[list[Candidate], dict, list[dict]]:
    target = spec["target"]
    required_enemy = target["enemy"]
    required_config = target.get("config", {})
    excluded_members = set(spec.get("exclude_members", []))
    sources: list[dict] = []
    candidates: list[Candidate] = []
    reference_defaults: dict | None = None

    for source_value in spec["sources"]:
        source_path = Path(source_value)
        if not source_path.is_absolute():
            source_path = spec_path.parent / source_path
        source_path = source_path.resolve()
        data = json.loads(source_path.read_text(encoding="utf-8"))
        report_spec = data["spec"]
        cases = data.get("cases", [])
        resolved_cases = report_spec.get("cases", [])
        if len(cases) != len(resolved_cases):
            raise ValueError(
                f"{source_path.name}: 결과 {len(cases)}개와 전개 케이스 "
                f"{len(resolved_cases)}개가 다릅니다."
            )

        defaults = report_spec.get("defaults", {})
        if reference_defaults is None:
            reference_defaults = defaults
        elif spec.get("require_same_defaults", True) and defaults != reference_defaults:
            raise ValueError(f"{source_path.name}: 첫 소스와 기본 육성 스펙이 다릅니다.")

        accepted = 0
        for index, (result, resolved) in enumerate(zip(cases, resolved_cases, strict=True)):
            enemy = result.get("enemy") or {}
            config = result.get("config") or {}
            if not _matches(enemy, required_enemy) or not _matches(config, required_config):
                continue
            squad = tuple(result["squad"])
            if excluded_members.intersection(squad):
                continue
            member_patterns = target.get("member_burst_patterns", {})
            burst_patterns = config.get("burst_pattern") or {}
            if any(
                member in squad and burst_patterns.get(member) != pattern
                for member, pattern in member_patterns.items()
            ):
                continue
            if len(squad) != 5 or len(set(squad)) != 5:
                continue
            mean = float(result.get("total", {}).get("mean", 0))
            if not math.isfinite(mean) or mean <= 0:
                continue

            # 이름·설명·출처는 빼고 실제 스펙과 운용만으로 중복을 판정한다.
            operation = {
                "squad": resolved.get("squad", []),
                "config": config,
                "enemy": enemy,
            }
            signature = _canonical(operation)
            run_map = {
                int(run["seed"]): float(run["squad_total"])
                for run in result.get("runs", [])
                if run.get("seed") is not None
            }
            candidates.append(Candidate(
                id=f"{source_path.stem}:{index}",
                name=result.get("name") or " / ".join(squad),
                squad=squad,
                damage=mean,
                total=result.get("total", {}),
                burst_count=float(result.get("burst_count", 0)),
                config=config,
                enemy=enemy,
                runs=run_map,
                source=str(source_path.relative_to(ROOT)),
                source_index=index,
                signature=signature,
                provenance=[{"source": str(source_path.relative_to(ROOT)), "index": index}],
            ))
            accepted += 1

        sources.append({
            "path": str(source_path.relative_to(ROOT)),
            "title": report_spec.get("title", source_path.stem),
            "loaded": len(cases),
            "accepted": accepted,
            "runs": report_spec.get("runs"),
            "seeds": data.get("seeds", []),
        })

    if not candidates:
        raise ValueError("지정한 조건에 맞는 5인 스쿼드 결과가 없습니다.")
    return candidates, reference_defaults or {}, sources


def _deduplicate(candidates: list[Candidate]) -> tuple[list[Candidate], int]:
    by_signature: dict[str, Candidate] = {}
    for candidate in candidates:
        previous = by_signature.get(candidate.signature)
        if previous is None:
            by_signature[candidate.signature] = candidate
            continue
        provenance = previous.provenance + candidate.provenance
        if candidate.damage > previous.damage:
            candidate.provenance = provenance
            by_signature[candidate.signature] = candidate
        else:
            previous.provenance = provenance
    unique = sorted(by_signature.values(), key=lambda item: (-item.damage, item.id))
    return unique, len(candidates) - len(unique)


def _assign_masks(candidates: list[Candidate]) -> dict[str, int]:
    names = sorted({name for candidate in candidates for name in candidate.squad})
    bits = {name: 1 << index for index, name in enumerate(names)}
    for candidate in candidates:
        candidate.mask = sum(bits[name] for name in candidate.squad)
    return bits


def solve_exact(candidates: list[Candidate], squad_count: int, top_k: int) -> list[tuple[float, tuple[int, ...]]]:
    """평균 총딜 기준 상위 K개 exact set-packing 해를 반환한다."""
    if squad_count <= 0 or top_k <= 0:
        raise ValueError("squad_count와 top_k는 양수여야 합니다.")

    heap: list[tuple[float, int, tuple[int, ...]]] = []
    serial = 0
    explored = 0

    def submit(total: float, chosen: tuple[int, ...]) -> None:
        nonlocal serial
        serial += 1
        item = (total, serial, chosen)
        if len(heap) < top_k:
            heapq.heappush(heap, item)
        elif total > heap[0][0]:
            heapq.heapreplace(heap, item)

    def visit(pool: tuple[int, ...], chosen: tuple[int, ...], used: int, total: float) -> None:
        nonlocal explored
        explored += 1
        need = squad_count - len(chosen)
        if need == 0:
            submit(total, chosen)
            return
        if len(pool) < need:
            return
        optimistic = total + sum(candidates[index].damage for index in pool[:need])
        if len(heap) == top_k and optimistic <= heap[0][0]:
            return

        last_start = len(pool) - need
        for position in range(last_start + 1):
            index = pool[position]
            candidate = candidates[index]
            if candidate.mask & used:
                continue
            tail = tuple(
                other for other in pool[position + 1:]
                if not (candidates[other].mask & (used | candidate.mask))
            )
            if len(tail) < need - 1:
                continue
            branch_upper = total + candidate.damage
            if need > 1:
                branch_upper += sum(candidates[other].damage for other in tail[:need - 1])
            if len(heap) == top_k and branch_upper <= heap[0][0]:
                continue
            visit(tail, chosen + (index,), used | candidate.mask, total + candidate.damage)

    visit(tuple(range(len(candidates))), (), 0, 0.0)
    solve_exact.explored = explored  # type: ignore[attr-defined]
    return [(total, chosen) for total, _, chosen in sorted(heap, reverse=True)]


def _candidate_json(candidate: Candidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "name": candidate.name,
        "squad": list(candidate.squad),
        "total": candidate.total,
        "burst_count": candidate.burst_count,
        "config": candidate.config,
        "source": candidate.source,
        "source_index": candidate.source_index,
        "provenance": candidate.provenance,
    }


def _solution_json(rank: int, chosen: tuple[int, ...], candidates: list[Candidate]) -> dict[str, Any]:
    squads = [candidates[index] for index in chosen]
    squads.sort(key=lambda item: item.damage, reverse=True)
    common_seeds = set.intersection(*(set(candidate.runs) for candidate in squads)) if squads else set()
    run_totals = [sum(candidate.runs[seed] for candidate in squads) for seed in sorted(common_seeds)]
    total = _stats(run_totals) if run_totals else _stats([sum(candidate.damage for candidate in squads)])
    # 목적함수는 각 후보의 저장된 평균 합이다. 공통 시드 합산 평균과 부동소수점 오차만 난다.
    total["objective"] = sum(candidate.damage for candidate in squads)
    return {
        "rank": rank,
        "total": total,
        "common_seeds": sorted(common_seeds),
        "squads": [_candidate_json(candidate) for candidate in squads],
    }


def _kor(value: float) -> str:
    return f"{value / 1e8:,.2f}억"


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


def _render_html(output: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value))

    def squad_card(squad: dict[str, Any]) -> str:
        portraits = []
        for name in squad["squad"]:
            image = _image_data(name)
            media = f'<img src="{image}" alt="">' if image else '<span class="missing">?</span>'
            portraits.append(f'<div class="char">{media}</div>')
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
        op_text = " · ".join(ops) if ops else "버스트순서 왼쪽부터"
        cv = float(squad.get("total", {}).get("cv", 0))
        return f'''<article class="squad">
          <div class="squad-head"><small>{esc(op_text)}</small>
          <div class="damage">{_kor(float(squad["total"]["mean"]))}<small>CV {cv:.2f}% · FB {float(squad["burst_count"]):.0f}</small></div></div>
          <div class="chars">{"".join(portraits)}</div>
        </article>'''

    variants = output.get("variants") or [output]

    def variant_panel(variant: dict[str, Any], index: int) -> str:
        solutions = []
        best = float(variant["solutions"][0]["total"]["objective"])
        for solution in variant["solutions"]:
            objective = float(solution["total"]["objective"])
            delta = objective - best
            delta_text = "최고" if solution["rank"] == 1 else f"{_kor(delta)}"
            solutions.append(f'''<section class="solution">
              <header><div><span class="rank">#{solution["rank"]}</span><b>{_kor(objective)}</b></div>
              <div class="delta">{delta_text} · CV {float(solution["total"]["cv"]):.2f}%</div></header>
              <div class="squads">{"".join(squad_card(s) for s in solution["squads"])}</div>
            </section>''')
        target = variant["target"]
        source_rows = "".join(
            f'<tr><td>{esc(src["title"])}</td><td>{src["loaded"]}</td><td>{src["accepted"]}</td></tr>'
            for src in variant["sources"]
        )
        excluded = variant.get("exclude_members", [])
        excluded_chip = f'<span class="chip">제외 {esc(" · ".join(excluded))}</span>' if excluded else '<span class="chip">캐릭터 제외 없음</span>'
        active = " active" if index == 0 else ""
        return f'''<section class="variant-panel{active}" data-panel="{index}">
          <section class="meta"><div class="chips"><span class="chip">적 코드 {esc(target["enemy"]["code"])}</span><span class="chip">비코어</span><span class="chip">노파츠</span><span class="chip">{target["config"]["duration"]:.0f}초</span>{excluded_chip}<span class="chip">후보 {variant["candidate_counts"]["unique"]}개</span><span class="chip">탐색 상태 {variant["search"]["explored_nodes"]:,}개</span></div>
          <table><thead><tr><th>후보 보고서</th><th>전체</th><th>채택</th></tr></thead><tbody>{source_rows}</tbody></table></section>
          {"".join(solutions)}</section>'''

    tabs = "".join(
        f'<button class="tab{" active" if index == 0 else ""}" data-tab="{index}" type="button">{esc(variant["name"])}</button>'
        for index, variant in enumerate(variants)
    )
    panels = "".join(variant_panel(variant, index) for index, variant in enumerate(variants))
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(output["title"])}</title>
<style>
:root{{--bg:#f6f6f3;--card:#fff;--ink:#151515;--muted:#6d6b65;--line:#deddd6;--blue:#2a78d6;--soft:#eaf2fc}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--card:#1c1c1b;--ink:#f5f5f3;--muted:#aaa79f;--line:#343431;--blue:#61a3ef;--soft:#152943}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,"Malgun Gothic",sans-serif}}
.wrap{{max-width:1180px;margin:auto;padding:34px 22px 80px}}h1{{font-size:25px;margin:0 0 6px}}.lead{{color:var(--muted);margin:0 0 18px}}
.tabs{{display:flex;gap:8px;margin:0 0 16px;border-bottom:1px solid var(--line)}}.tab{{appearance:none;border:0;border-bottom:3px solid transparent;background:transparent;color:var(--muted);font:inherit;font-weight:700;padding:10px 14px;cursor:pointer}}.tab.active{{color:var(--blue);border-bottom-color:var(--blue)}}.variant-panel{{display:none}}.variant-panel.active{{display:block}}
.meta{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:22px}}.chips{{display:flex;gap:7px;flex-wrap:wrap}}
.chip{{background:var(--soft);border-radius:999px;padding:4px 10px}}table{{width:100%;border-collapse:collapse;margin-top:12px;font-size:12px}}td,th{{padding:6px;border-top:1px solid var(--line);text-align:left}}th{{color:var(--muted)}}
.solution{{margin:24px 0 40px}}.solution>header{{display:flex;justify-content:space-between;align-items:end;margin-bottom:10px}}.solution>header b{{font-size:21px}}.rank{{color:var(--blue);font-weight:800;margin-right:9px}}.delta{{color:var(--muted)}}
.squads{{display:grid;gap:6px}}.squad{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 8px}}.squad-head{{display:flex;justify-content:space-between;gap:8px;align-items:start}}small{{display:block;color:var(--muted);font-weight:400}}.damage{{font-size:17px;font-weight:800;text-align:right;white-space:nowrap}}
.chars{{display:grid;grid-template-columns:repeat(5,48px);gap:6px;margin-top:4px}}.char{{display:grid;place-items:center}}.char img,.missing{{width:48px;height:48px;object-fit:cover;object-position:center 18%;border-radius:6px;background:var(--soft)}}.missing{{display:grid;place-items:center}}
@media(max-width:680px){{.chars{{grid-template-columns:repeat(5,minmax(0,1fr));gap:6px}}.char img,.missing{{width:100%;height:auto;aspect-ratio:1}}}}
</style></head><body><main class="wrap"><h1>{esc(output["title"])}</h1><p class="lead">기존 계산 결과 안에서 캐릭터 중복 없는 5스쿼드의 평균 총딜 합을 정확 최적화했다. 새 시뮬레이션은 실행하지 않았다.</p>
<nav class="tabs" aria-label="최적화 조건">{tabs}</nav>{panels}</main>
<script>document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{{document.querySelectorAll('.tab,.variant-panel').forEach(el=>el.classList.remove('active'));btn.classList.add('active');document.querySelector(`.variant-panel[data-panel="${{btn.dataset.tab}}"]`).classList.add('active')}}));</script></body></html>'''


def _optimize(spec: dict[str, Any], spec_path: Path, top_k_override: int | None = None) -> dict[str, Any]:
    candidates, defaults, sources = _load_candidates(spec, spec_path)
    loaded_count = len(candidates)
    candidates, duplicate_count = _deduplicate(candidates)
    char_bits = _assign_masks(candidates)
    top_k = top_k_override or int(spec.get("top_k", 10))
    squad_count = int(spec.get("squad_count", 5))
    solutions_raw = solve_exact(candidates, squad_count, top_k)
    if not solutions_raw:
        raise ValueError(f"캐릭터가 겹치지 않는 {squad_count}개 스쿼드를 만들 수 없습니다.")
    solutions = [
        _solution_json(rank, chosen, candidates)
        for rank, (_, chosen) in enumerate(solutions_raw, start=1)
    ]

    return {
        "target": spec["target"],
        "exclude_members": spec.get("exclude_members", []),
        "defaults": defaults,
        "sources": sources,
        "candidate_counts": {
            "loaded": loaded_count,
            "duplicates_removed": duplicate_count,
            "unique": len(candidates),
            "characters": len(char_bits),
        },
        "search": {
            "method": "exact branch-and-bound weighted set packing",
            "squad_count": squad_count,
            "top_k": top_k,
            "explored_nodes": getattr(solve_exact, "explored", 0),
        },
        "solutions": solutions,
    }


def run(spec_path: Path, top_k_override: int | None = None) -> tuple[Path, Path, dict[str, Any]]:
    slug = slug_from_spec(spec_path)
    preserve_spec(spec_path, slug)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    variant_specs = spec.get("variants") or [{"name": "전체"}]
    variants = []
    for variant in variant_specs:
        merged = {**spec, **{key: value for key, value in variant.items() if key != "name"}}
        result = _optimize(merged, spec_path, top_k_override)
        result["name"] = variant.get("name", "전체")
        variants.append(result)

    output = {
        "title": spec.get("title", spec_path.stem),
        "note": spec.get("note", ""),
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": spec["target"],
        "defaults": variants[0]["defaults"],
        "variants": variants,
        # 첫 탭을 기존 데이터 소비자의 호환 뷰로 유지한다.
        **{key: variants[0][key] for key in ("sources", "candidate_counts", "search", "solutions")},
    }

    data_path = work_data_path(slug)
    html_path = output_path(slug)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_html(output), encoding="utf-8")
    dependencies = []
    for value in spec.get("sources", []):
        source = (spec_path.parent / value).resolve()
        if source.name == "result.data.json" and source.parent.parent.resolve() == WORK_DIR.resolve():
            dependencies.append(source.parent.name)
    write_manifest(slug, kind="optimize", title=output["title"], dependencies=dependencies)
    write_index()
    return data_path, html_path, output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="최적화 스펙 JSON")
    parser.add_argument("--top", type=int, help="출력할 상위 해 개수")
    parser.add_argument("--open", action="store_true", help="완료 후 HTML 열기")
    args = parser.parse_args()
    spec_path = args.spec.resolve()
    data_path, html_path, output = run(spec_path, args.top)
    best = output["solutions"][0]
    print(f"후보 {output['candidate_counts']['unique']}개 / 탐색 {output['search']['explored_nodes']:,}상태")
    print(f"최적 총딜: {_kor(float(best['total']['objective']))}")
    for index, squad in enumerate(best["squads"], start=1):
        print(f"  {index}. {_kor(float(squad['total']['mean']))}  {' / '.join(squad['squad'])}")
    print(data_path)
    print(html_path)
    if args.open:
        webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
