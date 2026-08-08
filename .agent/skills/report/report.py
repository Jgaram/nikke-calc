"""딜량 보고서 러너.

JSON 케이스 스펙을 읽어 케이스마다 시뮬을 N회 돌리고, 결과를 자체완결 HTML로 낸다.

    python .agent/skills/report/report.py reports/specs/<이름>.json
    python .agent/skills/report/report.py <스펙> --runs 5 --jobs 4 --open
    python .agent/skills/report/report.py <스펙> --random   # 시드 고정 대신 매번 다른 난수

스펙 형식은 `.agent/skills/report/REPORT.md` 참조.

출력: `reports/out/<스펙파일명>.html` (이미지·CSS·JS 전부 인라인, 외부 의존 없음)

시드 정책
  기본은 고정 시드셋 [1..runs]을 **모든 케이스에 동일하게** 적용한다.
  같은 시드를 공유하므로 케이스 간 차이가 크리·코어 난수 노이즈에 묻히지 않고,
  보고서를 다시 뽑아도 수치가 재현된다. `--random`을 주면 seed=None으로 돌린다.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 이 파일은 `.agent/skills/report/` 안에 있다 (스킬 전용 도구). 저장소 루트는 3단계 위.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


from context import spec as char_spec  # noqa: E402  (sys.path 조정 뒤에 와야 한다)

# ── 기본 육성 스펙 ─────────────────────────────────────────────────────────
# 정본은 `context/spec.py`다 — 하네스(`context/snapshot.py`)·단발 CLI(`context/sim.py`)와
# 같은 스펙을 쓴다. 캐릭터별 차이분(앨리스 톡톡이 등)은 `data/char_defaults.json`.
REPORT_DEFAULT_CHAR: dict = char_spec.DEFAULT_CHAR

REPORT_DEFAULT_CONFIG: dict = {
    "duration": 180.0,
    "first_burst_time": 3.0,
    "burst_switch_delay": 0.1,
    "max_burst_count": 14,
}

DEFAULT_RUNS = 10


# ── 스펙 로드·정규화 ───────────────────────────────────────────────────────

def _deep_merge(base: dict, over: dict) -> dict:
    """dict를 재귀 병합한다 (over 우선). 리스트는 통째로 교체."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _expand_burst_sequence(cfg: dict, max_count: int | None) -> None:
    """`burst_sequence_cycle` 패턴을 풀버스트 횟수만큼 늘려 burst_sequence로 바꾼다.

    예: 2사이클 교대 패턴 × max_burst_count 14 → 14개 entry.
    `burst_sequence`가 직접 주어져 있으면 그대로 둔다.
    """
    pattern = cfg.pop("burst_sequence_cycle", None)
    if not pattern or cfg.get("burst_sequence"):
        return
    n = max_count or 20
    cfg["burst_sequence"] = [copy.deepcopy(pattern[i % len(pattern)]) for i in range(n)]


def load_spec(path: str) -> dict:
    """스펙 JSON을 읽어 케이스별 squad/config/enemy를 완전히 전개한 형태로 반환."""
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)

    from calculator.timeline import _NIKKE  # noqa: PLC0415  (임포트 비용 지연)
    known = set(_NIKKE)

    g_over = copy.deepcopy(spec.get("defaults", {}))
    g_config = _deep_merge(REPORT_DEFAULT_CONFIG, spec.get("config", {}))
    g_enemy = copy.deepcopy(spec.get("enemy", {}))

    # variants: 전 케이스에 공통으로 얹는 조건 축 (예: 코어 없음 / 코어 있음).
    # 케이스 × variant로 곱해지며, 보고서에서는 variant가 탭이 된다.
    variants = spec.get("variants") or [{"name": "", "defaults": {}, "config": {}, "enemy": {}}]

    cases = []
    for var in variants:
        for raw in spec["cases"]:
            names = raw["squad"]
            unknown = [n for n in names if n not in known]
            if unknown:
                raise SystemExit(
                    f"[{raw.get('name','?')}] parsed_nikke.json에 없는 이름: {unknown}\n"
                    f"별칭이 아니라 정식 명칭을 써야 한다 (context/ALIASES.md)."
                )
            if not 1 <= len(names) <= 5:
                raise SystemExit(f"[{raw.get('name','?')}] 스쿼드는 1~5명이어야 한다 ({len(names)}명)")

            # 육성 합성 순서: 기본 스펙 → 캐릭터별 기본 레이어(data/char_defaults.json)
            #   → 스펙 defaults → variant.defaults → case.defaults → case.chars[이름].
            # 레이어가 기본 스펙 바로 위에 있으므로 **스펙에 적은 값이 언제나 이긴다.**
            overlay = _deep_merge(_deep_merge(g_over, var.get("defaults", {})),
                                  raw.get("defaults", {}))

            # `no_layer`: 그 캐릭터는 레이어를 건너뛴다 (`true`면 전원). 재귀 병합이라
            # `"control": {}`로는 기본 컨트롤이 지워지지 않기 때문에 필요하다 —
            # "컨트롤 없음 vs 있음" 같은 대조군 variant를 만드는 스위치다.
            skip: set[str] = set()
            for src in (spec, var, raw):
                v = src.get("no_layer")
                if v is True:
                    skip |= set(names)
                elif v:
                    skip |= {x.strip() for x in v}
            if skip - set(names) and raw.get("no_layer") is not None:
                raise SystemExit(
                    f"[{raw.get('name','?')}] no_layer 대상이 스쿼드에 없다: "
                    f"{sorted(skip - set(names))}")

            per_char = {n: _deep_merge(overlay, raw.get("chars", {}).get(n, {}))
                        for n in names}
            # `members=names`를 반드시 넘긴다 — 조합 조건부 컨트롤(`_control_rules`)은
            # 스쿼드 전원을 봐야 판정된다. 빠뜨리면 미하라 엄폐컨·에이다 홀드컨이
            # 조용히 꺼진 채 돌아 그 조합만 딜이 낮게 나온다.
            squad = char_spec.resolve_patterns(
                [char_spec.build_char(n, per_char[n], no_layer=n in skip, members=names)
                 for n in names],
                explicit={n for n, v in per_char.items() if "burst_pattern" in v})

            config = _deep_merge(_deep_merge(g_config, var.get("config", {})),
                                 raw.get("config", {}))

            # 풀버스트 상한: 스펙이 명시하지 않았으면 스쿼드가 잘리지 않을 만큼 올린다.
            # 아르카나처럼 사이클이 빨라 기본 14회에 걸리는 캐릭터가 있고, 잘린 결과는
            # 조용히 낮게 나온다 (시뮬 자체는 상한이 없다 — timeline 기본 None).
            # **명시했으면 그대로 둔다** — 일부러 낮춰 자르는 것도 유효한 비교다.
            explicit = any("max_burst_count" in (s.get("config") or {})
                           for s in (spec, var, raw))
            floor = char_spec.max_burst_floor(names)
            if not explicit and floor:
                config["max_burst_count"] = max(config.get("max_burst_count") or 0, floor)

            _expand_burst_sequence(config, config.get("max_burst_count"))
            # 캐릭터별 버스트 패턴 → config["burst_pattern"] (burst_sequence가 있으면 무시)
            config = char_spec.build_config(squad, config)

            enemy = _deep_merge(_deep_merge(g_enemy, var.get("enemy", {})),
                                raw.get("enemy", {}))

            cases.append({
                "name": raw.get("name") or " / ".join(names),
                "variant": var.get("name", ""),
                "note": raw.get("note", ""),
                "squad": squad,
                "config": config,
                "enemy": enemy or None,
            })

    return {
        "title": spec.get("title") or os.path.splitext(os.path.basename(path))[0],
        "note": spec.get("note", ""),
        "runs": int(spec.get("runs", DEFAULT_RUNS)),
        # 보고서 하단 "설정" 표시용 전역 육성 스펙. 캐릭터별 기본 레이어는 여기 없고,
        # 캐릭터별 차이로 렌더러가 알아서 잡아낸다 (report_html: defaults와 다른 키만 표시).
        "defaults": _deep_merge(REPORT_DEFAULT_CHAR, g_over),
        "config": g_config,
        "enemy": g_enemy,
        "cases": cases,
    }


# ── 단일 시뮬 실행 (워커) ──────────────────────────────────────────────────

def run_one(job: tuple[dict, int | None]) -> dict:
    """시뮬 1회 실행 후 집계만 반환한다.

    SimResult 자체(히트 수만 개)를 프로세스 경계 너머로 보내면 직렬화 비용이 커서,
    워커 안에서 집계까지 끝내고 작은 dict만 돌려준다.
    """
    case, seed = job
    from calculator.timeline import simulate
    from calculator.sim_result import analyze_damage

    result = simulate(
        copy.deepcopy(case["squad"]),
        config=copy.deepcopy(case["config"]),
        enemy=copy.deepcopy(case["enemy"]),
        verbose=True,
        seed=seed,
    )

    chars: dict[str, dict] = {}
    for name in (c["name"] for c in case["squad"]):
        bd = analyze_damage(result, name)
        skills = {"일반공격": [bd.normal_atk.damage, bd.normal_atk.hits]} if bd.normal_atk.hits else {}
        for sname, stat in bd._skill_detail.items():
            skills[sname] = [stat.damage, stat.hits]
        chars[name] = {
            "total": bd.total,
            "normal": bd.normal_atk.damage,
            "skill": bd.skill.damage,
            "fb_self": bd.fb_self.damage,
            "fb_other": bd.fb_other.damage,
            "non_fb": bd.non_fb.damage,
            "skills": skills,
        }

    burst_count = 0
    if result.log:
        burst_count = sum(1 for e in result.log.burst_log if e.event == "full_burst 시작")

    return {
        "seed": seed,
        "squad_total": result.squad_total,
        "duration": result.duration,
        "burst_count": burst_count,
        "chars": chars,
    }


# ── 케이스 집계 ────────────────────────────────────────────────────────────

def _stats(values: list[float]) -> dict:
    mean = statistics.fmean(values) if values else 0.0
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "mean": mean,
        "std": sd,
        "cv": (sd / mean * 100) if mean else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
        "n": len(values),
    }


def aggregate(case: dict, runs: list[dict]) -> dict:
    """run_one 결과 목록 → 케이스 1건의 보고서용 집계."""
    n = len(runs)
    order = [c["name"] for c in case["squad"]]

    chars = []
    for name in order:
        totals = [r["chars"][name]["total"] for r in runs]
        skills: dict[str, list[float]] = {}
        for r in runs:
            for sname, (dmg, hits) in r["chars"][name]["skills"].items():
                acc = skills.setdefault(sname, [0.0, 0.0])
                acc[0] += dmg
                acc[1] += hits
        st = _stats(totals)
        chars.append({
            "name": name,
            **st,
            "normal": statistics.fmean(r["chars"][name]["normal"] for r in runs),
            "skill": statistics.fmean(r["chars"][name]["skill"] for r in runs),
            "fb_self": statistics.fmean(r["chars"][name]["fb_self"] for r in runs),
            "fb_other": statistics.fmean(r["chars"][name]["fb_other"] for r in runs),
            "non_fb": statistics.fmean(r["chars"][name]["non_fb"] for r in runs),
            "skills": sorted(
                ({"name": s, "damage": v[0] / n, "hits": v[1] / n} for s, v in skills.items()),
                key=lambda x: -x["damage"],
            ),
        })

    total = _stats([r["squad_total"] for r in runs])
    duration = runs[0]["duration"] if runs else 0.0
    return {
        "name": case["name"],
        "variant": case.get("variant", ""),
        "note": case["note"],
        "squad": order,
        "config": case["config"],
        "enemy": case["enemy"],
        "total": total,
        "dps": total["mean"] / duration if duration else 0.0,
        "duration": duration,
        "burst_count": statistics.fmean(r["burst_count"] for r in runs) if runs else 0,
        "runs": [{"seed": r["seed"], "squad_total": r["squad_total"]} for r in runs],
        "chars": chars,
    }


# ── 실행 ──────────────────────────────────────────────────────────────────

def run_report(spec: dict, runs: int, seeds: list[int | None], jobs: int) -> list[dict]:
    jobs_list: list[tuple[dict, int | None]] = [
        (case, seed) for case in spec["cases"] for seed in seeds
    ]

    t0 = time.time()
    done = 0
    total_jobs = len(jobs_list)

    def _tick(label: str) -> None:
        nonlocal done
        done += 1
        el = time.time() - t0
        print(f"  [{done}/{total_jobs}] {label}  ({el:.1f}s)", flush=True)

    outputs: list[dict] = []
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            for job, res in zip(jobs_list, ex.map(run_one, jobs_list, chunksize=1)):
                outputs.append(res)
                _tick(f"{job[0]['name']}  seed={job[1]}")
    else:
        for job in jobs_list:
            outputs.append(run_one(job))
            _tick(f"{job[0]['name']}  seed={job[1]}")

    per_case: list[dict] = []
    k = len(seeds)
    for i, case in enumerate(spec["cases"]):
        per_case.append(aggregate(case, outputs[i * k:(i + 1) * k]))
    return per_case


def main() -> None:
    ap = argparse.ArgumentParser(description="딜량 보고서 생성 (HTML)")
    ap.add_argument("spec", help="케이스 스펙 JSON 경로")
    ap.add_argument("--runs", type=int, help="케이스당 반복 횟수 (기본: 스펙의 runs, 없으면 10)")
    ap.add_argument("--random", action="store_true", help="고정 시드 대신 매번 다른 난수로 실행")
    ap.add_argument("--jobs", type=int, default=0, help="병렬 프로세스 수 (0=자동, 1=직렬)")
    ap.add_argument("--out", help="출력 HTML 경로 (기본 reports/out/<스펙명>.html)")
    ap.add_argument("--from-cache", action="store_true",
                    help="시뮬을 다시 돌리지 않고 직전 실행 결과(.data.json)로 HTML만 다시 만든다")
    ap.add_argument("--open", action="store_true", help="생성 후 브라우저로 연다")
    args = ap.parse_args()

    out = args.out or os.path.join(_ROOT, "reports", "out",
                                   os.path.splitext(os.path.basename(args.spec))[0] + ".html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cache_path = os.path.splitext(out)[0] + ".data.json"

    if args.from_cache:
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        spec, cases = cached["spec"], cached["cases"]
        seeds = cached["seeds"]
        args.random = cached["random"]
        print(f"[보고서] 캐시 재렌더링: {cache_path}")
    else:
        spec = load_spec(args.spec)
        runs = args.runs or spec["runs"]
        seeds = [None] * runs if args.random else list(range(1, runs + 1))
        jobs = args.jobs or min(os.cpu_count() or 1, runs * len(spec["cases"]), 8)

        print(f"[보고서] {spec['title']}  케이스 {len(spec['cases'])}개 × {runs}회"
              f"  (시드: {'랜덤' if args.random else f'1~{runs} 고정'}, 병렬 {jobs})")
        cases = run_report(spec, runs, seeds, jobs)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"spec": spec, "cases": cases, "seeds": seeds,
                       "random": args.random}, f, ensure_ascii=False)

    from report_html import render_html
    html = render_html(spec, cases, seeds=seeds, random_seed=args.random)

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n생성: {out}  ({os.path.getsize(out)/1024:.0f} KB)")

    for c in cases:
        label = f"{c['name']} — {c['variant']}" if c.get("variant") else c["name"]
        print(f"  {label:<44} {c['total']['mean']:>16,.0f}  ±{c['total']['std']:>12,.0f}"
              f"  (CV {c['total']['cv']:.2f}%)")

    if args.open:
        import webbrowser
        webbrowser.open("file:///" + out.replace("\\", "/"))


if __name__ == "__main__":
    main()
