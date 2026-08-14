"""솔로레이드 N스쿼드 보고서.

두 가지 모드가 있다.

- **최적화**: 기존 딜량 보고서 캐시의 후보 중 캐릭터가 겹치지 않는 정확히 N개
  스쿼드를 고르는 weighted set packing을 분기 한정법으로 정확히 푼다. 새 시뮬은 없다.
- **지정 편성**(`pinned_squads`): 사용자가 N×5명을 직접 지정한다. 후보 캐시에 같은
  편성이 있으면 그 결과를 쓰고, 없으면 그 스쿼드만 새로 시뮬한다.

두 모드는 한 스펙 안에서 탭으로 섞을 수 있다 — 지정 편성 탭은 같은 보고서의
최적해 대비 차이를 함께 보여준다.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import heapq
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

from optimize_html import _kor, render_html  # noqa: E402
from report_workspace import (  # noqa: E402
    WORK_DIR, bundle_dir, data_path as work_data_path, output_path, preserve_spec,
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
    simulated: bool = False
    provenance: list[dict[str, Any]] = field(default_factory=list)


def _load_candidates(
    spec: dict, spec_path: Path, *, allow_empty: bool = False,
) -> tuple[list[Candidate], dict, list[dict]]:
    target = spec["target"]
    required_enemy = target["enemy"]
    required_config = target.get("config", {})
    excluded_members = set(spec.get("exclude_members", []))
    sources: list[dict] = []
    candidates: list[Candidate] = []
    reference_defaults: dict | None = None

    for source_value in spec.get("sources") or []:
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

    if not candidates and not allow_empty:
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
        "simulated": candidate.simulated,
        "provenance": candidate.provenance,
    }


def _solution_json(
    rank: int, chosen: tuple[int, ...], candidates: list[Candidate], *, sort: bool = True,
) -> dict[str, Any]:
    squads = [candidates[index] for index in chosen]
    if sort:
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


# ── 지정 편성 ──────────────────────────────────────────────────────────────

@dataclass
class SimContext:
    """지정 편성을 새로 시뮬할 때 쓰는 실행 조건."""
    runs: int
    seeds: list[int | None]
    random: bool
    defaults: dict
    config: dict
    enemy: dict


def _sim_context(spec: dict, spec_path: Path) -> SimContext:
    """첫 후보 원본의 시드·반복·전역 조건을 물려받는다.

    같은 시드셋으로 돌려야 캐시에서 가져온 스쿼드와 새로 시뮬한 스쿼드를 한 해 안에서
    합산할 수 있다. 후보 원본이 없는 지정 전용 보고서는 최적화 스펙 자신의 값을 쓴다.
    """
    runs = int(spec.get("runs", 10))
    seeds: list[int | None] = list(range(1, runs + 1))
    random = False
    defaults = copy.deepcopy(spec.get("defaults", {}))
    config = copy.deepcopy(spec.get("config", {}))
    enemy = copy.deepcopy(spec.get("enemy", {}))

    sources = spec.get("sources") or []
    if sources:
        source_path = Path(sources[0])
        if not source_path.is_absolute():
            source_path = spec_path.parent / source_path
        source_path = source_path.resolve()
        data = json.loads(source_path.read_text(encoding="utf-8"))
        seeds = list(data.get("seeds") or seeds)
        runs = len(seeds) or runs
        random = bool(data.get("random"))
        # 후보를 만든 보고서의 난수 모드를 그대로 물려받는다. 기대값 모드로 뽑은 후보와
        # 확률 판정으로 새로 돌린 지정 스쿼드를 한 해에 합산하면 기준이 어긋난다.
        src_mode = ((data.get("spec") or {}).get("config") or {}).get("rng_mode")
        if src_mode:
            config["rng_mode"] = src_mode
        # 원본의 **가공 전** defaults를 쓴다. 캐시에 남은 defaults는 기본 스펙과 병합된
        # 완성본이라 그대로 얹으면 캐릭터별 기본 레이어(data/char_defaults.json)를 덮는다.
        origin_spec = source_path.parent / "spec.json"
        if origin_spec.exists():
            raw = json.loads(origin_spec.read_text(encoding="utf-8"))
            defaults = _merge(copy.deepcopy(raw.get("defaults", {})), defaults)
            config = _merge(copy.deepcopy(raw.get("config", {})), config)
            enemy = _merge(copy.deepcopy(raw.get("enemy", {})), enemy)

    target = spec.get("target", {})
    config = _merge(config, copy.deepcopy(target.get("config", {})))
    enemy = _merge(enemy, copy.deepcopy(target.get("enemy", {})))
    return SimContext(runs=runs, seeds=seeds, random=random,
                      defaults=defaults, config=config, enemy=enemy)


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    out.update(over)
    return out


def _normalize_pinned(entries: list[Any]) -> list[dict[str, Any]]:
    """`pinned_squads` 항목을 이름·멤버·오버라이드로 편다.

    항목은 이름 배열이거나 `{"name", "members", "config", "chars", "defaults", "no_layer"}`
    dict다. **배열 순서가 버스트 우선순위**이므로 순서를 바꾸지 않는다.
    """
    squads: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for index, entry in enumerate(entries, start=1):
        if isinstance(entry, list):
            entry = {"members": entry}
        members = list(entry.get("members") or entry.get("squad") or [])
        if not 1 <= len(members) <= 5 or len(set(members)) != len(members):
            raise ValueError(
                f"{index}번째 지정 스쿼드는 서로 다른 1~5명이어야 합니다: {members}")
        for member in members:
            counts[member] = counts.get(member, 0) + 1
        squads.append({
            "index": index,
            "name": entry.get("name") or " / ".join(members),
            "members": members,
            "overrides": {key: copy.deepcopy(entry[key])
                          for key in ("config", "chars", "defaults", "no_layer")
                          if key in entry},
        })
    if repeated := sorted(name for name, count in counts.items() if count > 1):
        raise ValueError(f"지정 편성에 캐릭터가 겹칩니다: {' · '.join(repeated)}")
    return squads


def _simulate_pinned(
    squads: list[dict[str, Any]], context: SimContext, slug: str, jobs: int,
) -> list[Candidate]:
    """후보 캐시에 없는 지정 스쿼드만 새로 시뮬한다."""
    import report  # 지연 임포트 — 최적화만 할 때는 계산기를 올리지 않는다.

    built = report.build_spec({
        "title": f"{slug} 지정 편성",
        "runs": context.runs,
        "defaults": context.defaults,
        "config": context.config,
        "enemy": context.enemy,
        "cases": [{"name": squad["name"], "squad": squad["members"], **squad["overrides"]}
                  for squad in squads],
    }, slug)

    meta = report._cache_meta()
    cache_path = bundle_dir(slug) / "pinned.data.json"
    cache: dict[str, dict] = {}
    if cache_path.exists():
        try:
            stored = json.loads(cache_path.read_text(encoding="utf-8"))
            if stored.get("cache_meta") == meta and stored.get("seeds") == context.seeds:
                cache = stored.get("cases", {})
        except (OSError, ValueError):
            cache = {}

    keys = [report._case_key(case) for case in built["cases"]]
    pending = [case for case, key in zip(built["cases"], keys) if key not in cache]
    if pending:
        workers = jobs or min(os.cpu_count() or 1, max(1, context.runs * len(pending)), 8)
        print(f"[지정 편성] 새 시뮬 {len(pending)}스쿼드 × {context.runs}회 (병렬 {workers})",
              flush=True)
        results = report.run_report({**built, "cases": pending},
                                    context.runs, context.seeds, workers)
        for case, result in zip(pending, results, strict=True):
            cache[report._case_key(case)] = result
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"cache_meta": meta, "seeds": context.seeds, "cases": cache},
                       ensure_ascii=False),
            encoding="utf-8")

    candidates = []
    for squad, case, key in zip(squads, built["cases"], keys, strict=True):
        result = cache[key]
        run_map = {
            int(run["seed"]): float(run["squad_total"])
            for run in result.get("runs", []) if run.get("seed") is not None
        }
        candidates.append(Candidate(
            id=f"pinned:{squad['index']}",
            name=squad["name"],
            squad=tuple(result["squad"]),
            damage=float(result["total"]["mean"]),
            total=result["total"],
            burst_count=float(result.get("burst_count", 0)),
            config=result["config"],
            enemy=result.get("enemy") or {},
            runs=run_map,
            source=f".report-work/{slug}/pinned.data.json",
            source_index=squad["index"],
            signature=_canonical({"squad": case["squad"], "config": result["config"],
                                  "enemy": result.get("enemy")}),
            simulated=True,
        ))
    return candidates


def _evaluate_pinned(
    spec: dict[str, Any], spec_path: Path, slug: str, jobs: int,
) -> dict[str, Any]:
    """지정한 편성의 총딜을 낸다. 캐시에 같은 편성이 있으면 재사용하고 없으면 시뮬한다."""
    pinned = _normalize_pinned(spec["pinned_squads"])
    # 지정 편성은 사용자가 직접 고른 것이므로 후보 필터(제외 조건)를 적용하지 않는다.
    pool, defaults, sources = _load_candidates(
        {**spec, "exclude_members": []}, spec_path, allow_empty=True)

    best_by_members: dict[frozenset[str], Candidate] = {}
    for candidate in pool:
        key = frozenset(candidate.squad)
        previous = best_by_members.get(key)
        if previous is None or candidate.damage > previous.damage:
            best_by_members[key] = candidate

    resolved: list[Candidate | None] = []
    missing: list[dict[str, Any]] = []
    for squad in pinned:
        cached = None if squad["overrides"] else best_by_members.get(frozenset(squad["members"]))
        # 스쿼드 순서가 버스트 우선순위다. 지정 순서와 다른 후보는 다른 운용이므로 다시 돈다.
        if cached is not None and list(cached.squad) != squad["members"]:
            cached = None
        resolved.append(cached)
        if cached is None:
            missing.append(squad)

    if missing:
        fresh = iter(_simulate_pinned(missing, _sim_context(spec, spec_path), slug, jobs))
        resolved = [item if item is not None else next(fresh) for item in resolved]

    squads = [item for item in resolved if item is not None]
    solution = _solution_json(1, tuple(range(len(squads))), squads, sort=False)
    return {
        "pinned": True,
        "target": spec["target"],
        "exclude_members": [],
        "defaults": defaults,
        "sources": sources,
        "candidate_counts": {
            "loaded": len(pool),
            "duplicates_removed": 0,
            "unique": len(best_by_members),
            "characters": len({member for squad in pinned for member in squad["members"]}),
        },
        "search": {
            "method": "pinned squads",
            "squad_count": len(pinned),
            "top_k": 1,
            "explored_nodes": 0,
        },
        "pinned_meta": {"reused": len(pinned) - len(missing), "simulated": len(missing)},
        "solutions": [solution],
    }


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


def run(
    spec_path: Path, top_k_override: int | None = None, jobs: int = 0,
) -> tuple[Path, Path, dict[str, Any]]:
    slug = slug_from_spec(spec_path)
    preserve_spec(spec_path, slug)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    default_variant = {"name": "지정 편성"} if spec.get("pinned_squads") else {"name": "전체"}
    variant_specs = spec.get("variants") or [default_variant]
    variants = []
    for variant in variant_specs:
        merged = {**spec, **{key: value for key, value in variant.items() if key != "name"}}
        if merged.get("pinned_squads"):
            result = _evaluate_pinned(merged, spec_path, slug, jobs)
        else:
            result = _optimize(merged, spec_path, top_k_override)
        result["name"] = variant.get("name", default_variant["name"])
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
    html_path.write_text(render_html(output), encoding="utf-8")
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
    parser.add_argument("spec", type=Path, help="최적화·지정 편성 스펙 JSON")
    parser.add_argument("--top", type=int, help="출력할 상위 해 개수")
    parser.add_argument("--jobs", type=int, default=0,
                        help="지정 편성 신규 시뮬의 병렬 프로세스 수 (0=자동)")
    parser.add_argument("--open", action="store_true", help="완료 후 HTML 열기")
    args = parser.parse_args()
    spec_path = args.spec.resolve()
    data_path, html_path, output = run(spec_path, args.top, args.jobs)
    for variant in output["variants"]:
        best = variant["solutions"][0]
        if variant.get("pinned"):
            meta = variant["pinned_meta"]
            print(f"[{variant['name']}] 지정 {variant['search']['squad_count']}스쿼드 "
                  f"/ 재사용 {meta['reused']}개 · 신규 시뮬 {meta['simulated']}개")
            print(f"  총딜 합: {_kor(float(best['total']['objective']))}")
        else:
            print(f"[{variant['name']}] 후보 {variant['candidate_counts']['unique']}개 "
                  f"/ 탐색 {variant['search']['explored_nodes']:,}상태")
            print(f"  최적 총딜: {_kor(float(best['total']['objective']))}")
        for index, squad in enumerate(best["squads"], start=1):
            print(f"    {index}. {_kor(float(squad['total']['mean']))}  {' / '.join(squad['squad'])}")
    print(data_path)
    print(html_path)
    if args.open:
        webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
