"""딜량 보고서 러너.

JSON 케이스 스펙을 읽어 케이스마다 시뮬을 N회 돌리고, 결과를 자체완결 HTML로 낸다.

    python .agent/skills/report-squad/scripts/report.py .report-work/<이름>/spec.json
    python .agent/skills/report-squad/scripts/report.py <스펙> --jobs 4 --open
    python .agent/skills/report-squad/scripts/report.py <스펙> --sampled --runs 5

스펙 형식은 `.agent/skills/report-squad/references/format.md` 참조.

출력: `reports/<스펙파일명>.html` (이미지·CSS·JS 전부 인라인, 외부 의존 없음)

난수 정책
  **기본은 기대값 모드**(`rng_mode: "expected"`)다 — 크리·코어히트를 확률 판정 대신
  기대값으로 태워 케이스당 1회로 끝낸다. 난수가 없으니 반복 평균이 필요 없고, 케이스 간
  차이가 난수 노이즈에 묻히지 않으며, 다시 뽑아도 같은 수치가 나온다.
  (하네스 회귀 `context/snapshot.py`는 종전대로 확률 판정 + 고정 시드다.)

  `--sampled`는 인게임과 같은 확률 판정으로 돌린다 — 분산·CV 자체를 보고 싶을 때만 쓴다.
  이때는 고정 시드셋 [1..runs]을 모든 케이스에 동일하게 적용하고, `--random`을 주면
  seed=None으로 돌린다.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 이 파일은 `.agent/skills/report-squad/scripts/` 안에 있다.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


from context import spec as char_spec  # noqa: E402  (sys.path 조정 뒤에 와야 한다)
from report_workspace import (  # noqa: E402
    data_path, output_path, prepare, preserve_spec, slug_from_spec, write_index, write_manifest,
)

# ── 기본 육성 스펙 ─────────────────────────────────────────────────────────
# 정본은 `context/spec.py`다 — 하네스(`context/snapshot.py`)·단발 CLI(`context/sim.py`)와
# 같은 스펙을 쓴다. 캐릭터별 차이분(앨리스 톡톡이 등)은 `data/char_defaults.json`.
REPORT_DEFAULT_CHAR: dict = char_spec.DEFAULT_CHAR

REPORT_DEFAULT_CONFIG: dict = {
    "duration": 180.0,
    "first_burst_time": 3.0,
    "burst_switch_delay": 0.1,
    "max_burst_count": 14,
    # 보고서 기본은 기대값 모드 — 케이스당 1회로 결정론적 기대딜이 나온다.
    # 스펙의 `config.rng_mode`나 `--sampled`/`--random`으로 확률 판정으로 되돌린다.
    "rng_mode": "expected",
}

DEFAULT_RUNS = 10
CACHE_SCHEMA_VERSION = 3  # 3: rng_mode 도입 (기대값 모드가 기본)


def _calculation_paths() -> list[Path]:
    root = Path(_ROOT)
    paths = [*root.glob("calculator/**/*.py"), root / "context" / "spec.py",
             *root.glob("data/**/*.json")]
    return sorted((p for p in paths if p.is_file()), key=lambda p: p.as_posix())


def _calculation_fingerprint() -> str:
    """Return a digest of code/data that can change simulation results.

    Presentation-only changes are deliberately excluded. A calculator, shared
    spec, or parsed-data change invalidates every cached case, while editing a
    title/group/note does not.
    """
    root = Path(_ROOT)
    digest = hashlib.sha256()
    for path in _calculation_paths():
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _cache_meta() -> dict:
    return {
        "schema": CACHE_SCHEMA_VERSION,
        "calculation_fingerprint": _calculation_fingerprint(),
    }


def _legacy_cache_compatible(cache_path: Path) -> bool:
    """Safely adopt a pre-fingerprint cache when dependencies predate it."""
    try:
        stamp = cache_path.stat().st_mtime_ns
        return all(path.stat().st_mtime_ns <= stamp for path in _calculation_paths())
    except OSError:
        return False


def _write_cache(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp_path, path)


def _case_key(case: dict) -> str:
    """Identity of inputs that affect one simulation result."""
    payload = {
        "squad": case["squad"],
        "config": case["config"],
        "enemy": case["enemy"],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _refresh_case_metadata(result: dict, case: dict) -> dict:
    """Reuse numeric output while adopting current display metadata."""
    out = copy.deepcopy(result)
    out.update({
        "name": case["name"],
        "group": case.get("group", ""),
        "variant": case.get("variant", ""),
        "note": case.get("note", ""),
        "squad": [c["name"] for c in case["squad"]],
        "config": copy.deepcopy(case["config"]),
        "enemy": copy.deepcopy(case["enemy"]),
    })
    return out


def _incremental_plan(spec: dict, cached: dict) -> tuple[list[dict | None], list[dict]]:
    """Return ordered result slots and only the cases that need simulation."""
    old_spec_cases = cached.get("spec", {}).get("cases") or []
    old_results = cached.get("cases") or []
    if len(old_spec_cases) != len(old_results):
        return [None] * len(spec["cases"]), list(spec["cases"])

    buckets: dict[str, list[dict]] = {}
    for old_case, old_result in zip(old_spec_cases, old_results):
        buckets.setdefault(_case_key(old_case), []).append(old_result)

    slots: list[dict | None] = []
    pending: list[dict] = []
    for case in spec["cases"]:
        matches = buckets.get(_case_key(case)) or []
        if matches:
            slots.append(_refresh_case_metadata(matches.pop(0), case))
        else:
            slots.append(None)
            pending.append(case)
    return slots, pending


def _combine_results(slots: list[dict | None], calculated: list[dict]) -> list[dict]:
    fresh = iter(calculated)
    return [next(fresh) if item is None else item for item in slots]


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
    return build_spec(spec, os.path.splitext(os.path.basename(path))[0])


def build_spec(spec: dict, title_fallback: str = "report") -> dict:
    """스펙 dict → 케이스별 squad/config/enemy를 완전히 전개한 형태.

    파일에서 읽은 스펙만이 아니라 **다른 도구가 메모리에서 만든 스펙**도 같은 경로로
    전개한다 (육성 효율 보고서가 축·단계를 케이스로 펼쳐 여기로 넘긴다).
    """
    from calculator.timeline import _NIKKE  # noqa: PLC0415  (임포트 비용 지연)
    known = set(_NIKKE)

    g_over = copy.deepcopy(spec.get("defaults", {}))
    g_config = _deep_merge(REPORT_DEFAULT_CONFIG, spec.get("config", {}))
    g_enemy = copy.deepcopy(spec.get("enemy", {}))

    # 육성 프로필(2.5층): 고정 스펙 대신 실제 계정의 육성으로 전체 보고서를 돌린다.
    # 보고서 단위 스위치다 — 케이스마다 다른 프로필을 섞으면 케이스 간 비교가 무의미해진다.
    profile_name = spec.get("profile")
    profile = (char_spec.load_profile(profile_name, bool(spec.get("allow_unowned")),
                                      spec.get("profile_level", "fixed"))
               if profile_name else None)

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
                [char_spec.build_char(n, per_char[n], no_layer=n in skip, members=names,
                                      profile=profile)
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
                "group": raw.get("group", ""),
                "variant": var.get("name", ""),
                "note": raw.get("note", ""),
                "squad": squad,
                "config": config,
                "enemy": enemy or None,
            })

    all_chars = [c for case in cases for c in case["squad"]]
    all_names = sorted({c["name"] for c in all_chars})
    return {
        "title": spec.get("title") or title_fallback,
        "note": spec.get("note", ""),
        "runs": int(spec.get("runs", DEFAULT_RUNS)),
        # 프로필은 이름만 싣는다 (캐시에 JSON으로 들어간다). 렌더러가 이름으로 다시 읽어
        # 이탈 보고의 기준선을 프로필로 맞춘다. 헤더·경고는 여기서 굳혀 둔다 — 나중에
        # 프로필을 다시 받아도 이 보고서가 무엇으로 계산됐는지는 바뀌면 안 되기 때문이다.
        "profile": profile_name,
        "profile_header": profile.header() if profile else "",
        "profile_notes": (profile.notes(all_names) + profile.cube_notes(all_chars)
                          if profile else []),
        # 보고서 하단 "설정" 표시용 전역 육성 스펙. 캐릭터별 기본 레이어는 여기 없고,
        # 캐릭터별 차이로 렌더러가 알아서 잡아낸다 (report_html: defaults와 다른 키만 표시).
        "defaults": _deep_merge(REPORT_DEFAULT_CHAR, g_over),
        "config": g_config,
        "enemy": g_enemy,
        "cases": cases,
    }


# ── 난수 모드 ──────────────────────────────────────────────────────────────
# 전개된 스펙에서는 **케이스의 config가 정본**이다 (variant·케이스가 덮어쓸 수 있다).
# 러너 셋(report·growth·optimize_solo_raid)이 이 두 함수로 같은 판정을 쓴다.

def spec_is_expected(spec: dict) -> bool:
    """전 케이스가 기대값 모드면 True. 하나라도 확률 판정이면 반복 평균이 필요하다."""
    cases = spec.get("cases") or []
    return bool(cases) and all(
        (c["config"].get("rng_mode") or "expected") == "expected" for c in cases)


def force_sampled_mode(spec: dict) -> None:
    """스펙 전체를 확률 판정 모드로 되돌린다 (CLI `--sampled`/`--random`)."""
    spec.setdefault("config", {})["rng_mode"] = "random"
    for case in spec.get("cases") or []:
        case["config"]["rng_mode"] = "random"


def sampling_plan(spec: dict, runs: int | None, random_seeds: bool) -> tuple[bool, int, list]:
    """(기대값 모드인가, 반복 횟수, 시드 목록).

    기대값 모드는 난수가 없어 몇 번을 돌려도 같은 값이라 1회로 고정한다.
    """
    if spec_is_expected(spec):
        return True, 1, [None]
    n = runs or int(spec.get("runs", DEFAULT_RUNS))
    return False, n, ([None] * n if random_seeds else list(range(1, n + 1)))


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
        "group": case.get("group", ""),
        "variant": case.get("variant", ""),
        "note": case["note"],
        "squad": order,
        "config": case["config"],
        "enemy": case["enemy"],
        "total": total,
        "dps": total["mean"] / duration if duration else 0.0,
        "duration": duration,
        "burst_count": statistics.fmean(r["burst_count"] for r in runs) if runs else 0,
        # 회차별 원자료. `chars`(시드별 캐릭터 딜)는 육성 효율 보고서의 **페어드 델타**가
        # 쓴다 — 케이스별 평균만 남기면 시드 노이즈가 상쇄되지 않아 1~2% 차이를 못 본다.
        "runs": [{"seed": r["seed"], "squad_total": r["squad_total"],
                  "chars": {n: v["total"] for n, v in r["chars"].items()}} for r in runs],
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
    ap.add_argument("--runs", type=int,
                    help="케이스당 반복 횟수 (--sampled일 때만 의미가 있다. 기본: 스펙의 runs, 없으면 10)")
    ap.add_argument("--sampled", action="store_true",
                    help="기대값 모드 대신 인게임과 같은 확률 판정으로 N회 돌려 평균낸다 "
                         "(분산·CV를 보고 싶을 때만)")
    ap.add_argument("--random", action="store_true",
                    help="확률 판정 + 고정 시드 대신 매번 다른 난수로 실행 (--sampled 포함)")
    ap.add_argument("--jobs", type=int, default=0, help="병렬 프로세스 수 (0=자동, 1=직렬)")
    ap.add_argument("--out", help="출력 HTML 경로 (기본 reports/<스펙명>.html)")
    ap.add_argument("--from-cache", action="store_true",
                    help="시뮬을 다시 돌리지 않고 직전 실행 결과(.data.json)로 HTML만 다시 만든다")
    ap.add_argument("--full", action="store_true",
                    help="호환되는 기존 케이스도 재사용하지 않고 전부 다시 계산한다")
    ap.add_argument("--open", action="store_true", help="생성 후 브라우저로 연다")
    args = ap.parse_args()
    if args.from_cache and args.full:
        ap.error("--from-cache와 --full은 함께 쓸 수 없다")

    slug = slug_from_spec(args.spec)
    prepare(slug)
    out = Path(args.out).resolve() if args.out else output_path(slug)
    out.parent.mkdir(parents=True, exist_ok=True)
    cache_path = data_path(slug)

    if args.from_cache:
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        if not cached.get("cache_meta") and _legacy_cache_compatible(cache_path):
            cached["cache_meta"] = _cache_meta()
            _write_cache(cache_path, cached)
            print(f"[보고서] 기존 캐시에 계산 지문 기록: {cache_path}")
        spec, cases = cached["spec"], cached["cases"]
        seeds = cached["seeds"]
        args.random = cached["random"]
        expected = cached.get("expected", spec_is_expected(spec))
        print(f"[보고서] 캐시 재렌더링: {cache_path}")
    else:
        preserve_spec(args.spec, slug)
        spec = load_spec(args.spec)
        if args.sampled or args.random:
            force_sampled_mode(spec)
        expected, runs, seeds = sampling_plan(spec, args.runs, args.random)
        if expected and args.runs and args.runs > 1:
            print("  · 기대값 모드라 --runs는 무시한다 (난수가 없어 1회로 확정된다). "
                  "분산을 보려면 --sampled")
        meta = _cache_meta()
        slots: list[dict | None] = [None] * len(spec["cases"])
        pending = list(spec["cases"])
        reuse_reason = "캐시 없음"

        if cache_path.exists() and not args.full:
            try:
                with open(cache_path, encoding="utf-8") as f:
                    cached = json.load(f)
                same_engine = (cached.get("cache_meta") == meta
                               or (not cached.get("cache_meta")
                                   and _legacy_cache_compatible(cache_path)))
                same_sampling = (cached.get("seeds") == seeds
                                 and bool(cached.get("random")) == args.random)
                if same_engine and same_sampling:
                    slots, pending = _incremental_plan(spec, cached)
                    reuse_reason = "호환 캐시"
                elif not same_engine:
                    reuse_reason = "계산 코드·데이터 변경"
                else:
                    reuse_reason = "시드·반복 조건 변경"
            except (OSError, ValueError, KeyError, TypeError) as exc:
                reuse_reason = f"캐시 읽기 실패: {exc}"
        elif args.full:
            reuse_reason = "--full 요청"

        reused = len(spec["cases"]) - len(pending)
        jobs = args.jobs or min(os.cpu_count() or 1, max(1, runs * len(pending)), 8)

        mode_txt = ("기대값 모드 — 난수 없음" if expected
                    else f"확률 판정 · 시드 {'랜덤' if args.random else f'1~{runs} 고정'}")
        print(f"[보고서] {spec['title']}  전체 {len(spec['cases'])}개 · "
              f"재사용 {reused}개 · 계산 {len(pending)}개 × {runs}회"
              f"  ({mode_txt}, 병렬 {jobs})")
        if not reused:
            print(f"  전체 계산 사유: {reuse_reason}")
        # 프리뷰(출시 전 카드 기준) 캐릭터가 끼면 결과가 미검증이라는 걸 명시한다
        note = char_spec.preview_note(
            sorted({c.get("name", "") for case in spec["cases"] for c in case["squad"]}))
        if note:
            print(f"⚠ {note}")
        # 프로필로 돌렸다는 사실은 언제나 실린다 — 고정 스펙 보고서와 총딜을 나란히
        # 놓으면 안 되기 때문이다.
        if spec.get("profile_header"):
            print(f"⚠ {spec['profile_header']}")
        for line in spec.get("profile_notes") or []:
            print(f"⚠ {line}")
        calculated = run_report({**spec, "cases": pending}, runs, seeds, jobs) if pending else []
        cases = _combine_results(slots, calculated)
        _write_cache(cache_path, {"spec": spec, "cases": cases, "seeds": seeds,
                                  "random": args.random, "expected": expected,
                                  "cache_meta": meta})

    from report_html import render_html
    html = render_html(spec, cases, seeds=seeds, random_seed=args.random,
                       expected=expected)

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    write_manifest(slug, kind="report-squad", title=spec.get("title", slug))
    write_index()
    print(f"\n생성: {out}  ({out.stat().st_size/1024:.0f} KB)")

    for c in cases:
        label = f"{c['name']} — {c['variant']}" if c.get("variant") else c["name"]
        spread = ("" if c["total"]["n"] <= 1
                  else f"  ±{c['total']['std']:>12,.0f}  (CV {c['total']['cv']:.2f}%)")
        print(f"  {label:<44} {c['total']['mean']:>16,.0f}{spread}")

    if args.open:
        import webbrowser
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
