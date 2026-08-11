"""육성 효율 보고서 러너.

한 캐릭터의 육성 변수(스킬 레벨·장비 옵션·소장품 …)를 **기준점에서 한 축씩** 움직여
덱 총딜과 그 캐릭터 자신의 딜이 각각 얼마나 오르는지 잰다.

    python .agent/skills/growth/growth.py reports/specs/<이름>.json
    python .agent/skills/growth/growth.py <스펙> --runs 12 --jobs 8 --open
    python .agent/skills/growth/growth.py <스펙> --from-cache   # 시뮬 없이 HTML만 다시

스펙 형식은 `.agent/skills/growth/GROWTH.md` 참조.

딜량 보고서(`report`)와 다른 점은 셋이다.

1. **케이스를 손으로 쓰지 않는다.** 덱 × 축 × 단계로 전개하며, 기준 단계는 덱당 한 번만
   돌려 전 축이 공유한다 (축마다 다시 돌리면 그만큼 통째로 낭비다).
2. **페어드 델타로 잰다.** 시드별로 먼저 기준과의 차를 구하고 그 평균을 쓴다. 스킬 1레벨은
   보통 총딜 1% 안팎인데 시드 간 CV가 0.5~1.5%라, 케이스별 평균끼리 빼면 신호가 노이즈에
   묻힌다. 같은 시드셋을 공유하므로 짝지어 빼면 난수 성분이 대부분 상쇄된다.
3. **두 지표를 나눠 본다.** 덱 총딜 Δ와 대상 캐릭터 자신의 딜 Δ. 버퍼는 자기 딜이 안 늘어도
   덱 딜이 크게 오르고, 그 반대도 있다.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import statistics
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_REPORT = os.path.join(_ROOT, ".agent", "skills", "report")
for _p in (_ROOT, _REPORT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import report as report_tool  # noqa: E402  (sys.path 조정 뒤에 와야 한다)
from context import spec as char_spec  # noqa: E402

DEFAULT_RUNS = 10

# ── 모드 ───────────────────────────────────────────────────────────────────
# 스킬과 옵션을 한 보고서에 섞지 않는다. 둘은 기준이 서로 달라야 하기 때문이다 —
# 옵션 효율은 스킬이 만렙일 때의 값이고, 스킬 효율은 옵션이 기본값일 때의 값이다.
# 섞으면 어느 쪽 Δ도 "지금 내 계정에서의 값"이 아니게 된다.

SKILL_STEPS = [7, 8, 9, 10]        # 기준 7 + 8·9·10
OPTION_LINES = [0, 1, 2, 3, 4]     # 오버로드 줄 수. 전부 레벨 10

# 옵션 모드 기본 축. 라벨은 보고서에 그대로 나온다.
OPTION_AXES = [("공격력 옵션", "atk_pct"), ("최대장탄 옵션", "max_ammo_pct"),
               ("크리티컬 확률 옵션", "crit_rate"), ("크리티컬 대미지 옵션", "crit_dmg")]
# 차지형(RL·SR)에만 붙는 축. 다른 무기군에는 아무 효과가 없어 축을 넣어도 전부 0이 된다.
CHARGE_AXES = [("차지속도 옵션", "charge_speed_pct"), ("차지대미지 옵션", "charge_dmg_pct")]
CHARGE_WEAPONS = {"RL", "SR"}
# 우월코드는 **기준이 4줄**이다. 다른 축과 기준을 공유해야 효율 랭킹에 같이 올릴 수 있어서,
# 0~3줄은 음수 Δ로 나온다 ("4줄을 안 맞추면 이만큼 잃는다").
ELEMENT_AXIS = ("우월코드 옵션", "element_bonus")
ELEMENT_BASE_LINES = 4

# 랩쳐 코드 → 그 코드에 강한(=우월코드가 붙는) 속성.
CODE_WEAK = {"전격": "철갑", "수냉": "전격", "작열": "수냉", "풍압": "작열", "철갑": "풍압"}

# 케이스 이름 구분자. 케이스 이름은 `덱 ∥ 축 ∥ 단계`로 유일해야 한다
# (report의 `_ops()`가 케이스 이름으로 설정 예외를 가른다).
SEP = " ∥ "


# ── 스펙 전개 ──────────────────────────────────────────────────────────────

def _step_key(axis_name: str, label: str) -> str:
    return f"{axis_name}:{label}"


def _meta(name: str) -> dict:
    from calculator.timeline import _NIKKE  # noqa: PLC0415
    return _NIKKE.get(name, {})


def _line_label(lines: int, value: float) -> str:
    return "없음" if lines == 0 else f"{lines}줄 ({value:g}%)"


def _auto_axes(spec: dict, subject: str) -> tuple[dict, list[dict], list[str]]:
    """`mode`에 따라 기준 육성과 축을 만든다 → (baseline, axes, 알림 목록).

    스펙에 적힌 `baseline`·`axes`는 각각 위에 얹히고 뒤에 붙는다 — 자동 생성은 출발점이지
    잠금이 아니다. `mode`가 없으면 아무것도 만들지 않는다(예전 스펙이 그대로 돈다).
    """
    mode = spec.get("mode")
    if not mode:
        return {}, [], []
    if mode not in ("skill", "option"):
        raise SystemExit(f"`mode`는 \"skill\" 또는 \"option\"이어야 한다 ({mode!r}).")

    notes: list[str] = []
    lines_list = spec.get("option_lines") or OPTION_LINES

    if mode == "skill":
        # 옵션은 기본 스펙 그대로 두고 스킬만 움직인다.
        steps_lv = spec.get("skill_steps") or SKILL_STEPS
        base_lv = steps_lv[0]
        baseline = {"skill_levels": {k: base_lv for k in ("1", "2", "3")}}
        axes = [
            {"name": nm, "steps": [{"label": str(lv), **({"base": True} if lv == base_lv
                                                         else {"over": {"skill_levels": {k: lv}}})}
                                   for lv in steps_lv]}
            for k, nm in (("1", "1스킬 레벨"), ("2", "2스킬 레벨"), ("3", "버스트 레벨"))
        ]
        notes.append(f"스킬 조사 — 장비 옵션은 기본 스펙 그대로, 기준 스킬 레벨 {base_lv}")
        return baseline, axes, notes

    # mode == "option": 스킬은 만렙 고정, 옵션은 우월코드 4줄만 깔고 나머지를 0에서 올린다.
    keys = list(OPTION_AXES)
    weapon = _meta(subject).get("weapon_type", "")
    if weapon in CHARGE_WEAPONS or spec.get("charge_axes"):
        keys += CHARGE_AXES
        if weapon in CHARGE_WEAPONS:
            notes.append(f"{subject}는 {weapon} — 차지속도·차지대미지 축을 자동으로 넣었다")

    zero = {opt: 0 for _nm, opt in keys}
    zero["element_bonus"] = char_spec.overload("element_bonus", ELEMENT_BASE_LINES)
    baseline = {"skill_levels": {"1": 10, "2": 10, "3": 10}, "equip_skills": zero}

    axes = []
    for nm, opt in keys:
        steps = []
        for n in lines_list:
            v = char_spec.overload(opt, n)
            steps.append({"label": _line_label(n, v),
                          **({"base": True} if n == 0 else {"over": {"equip_skills": {opt: v}}})})
        axes.append({"name": nm, "steps": steps})

    if spec.get("include_element_bonus"):
        nm, opt = ELEMENT_AXIS
        steps = [{"label": _line_label(n, char_spec.overload(opt, n)),
                  **({"base": True} if n == ELEMENT_BASE_LINES
                     else {"over": {"equip_skills": {opt: char_spec.overload(opt, n)}}})}
                 for n in lines_list]
        if not any(s.get("base") for s in steps):
            raise SystemExit(f"우월코드 축의 기준은 {ELEMENT_BASE_LINES}줄인데 "
                             f"`option_lines`에 {ELEMENT_BASE_LINES}이 없다.")
        axes.append({"name": nm, "note": f"기준이 {ELEMENT_BASE_LINES}줄이라 그 아래는 음수로 나온다",
                     "steps": steps})

        code = (spec.get("enemy") or {}).get("code")
        if code and _meta(subject).get("element_code") != CODE_WEAK.get(code):
            notes.append(f"⚠ {subject}는 {code} 랩쳐의 약점 속성이 아니다 — "
                         f"우월코드 축은 전부 0으로 나온다")

    notes.append(f"옵션 조사 — 스킬 10/10/10 고정, 우월코드 {ELEMENT_BASE_LINES}줄 외 옵션 없음에서 시작"
                 f" (전부 레벨 {char_spec.OVERLOAD_LV})")
    return baseline, axes, notes


def expand(spec: dict) -> tuple[dict, dict]:
    """육성 효율 스펙 → (report 형식 스펙, 메타).

    메타는 케이스 이름으로 되짚어 볼 수 있는 구조 정보다 —
    어느 덱의 어느 축 어느 단계인지, 기준은 누구인지.
    """
    subject = spec.get("subject")
    if not subject:
        raise SystemExit("스펙에 `subject`(조사 대상 캐릭터)가 없다.")

    decks = spec.get("decks") or []
    if not decks:
        raise SystemExit("스펙에 `decks`가 없다. 덱을 1개 이상 적는다.")

    # `mode`가 만든 기준·축이 먼저 오고, 스펙이 직접 적은 것이 그 위에 얹히고 뒤에 붙는다.
    auto_base, auto_axes, mode_notes = _auto_axes(spec, subject)
    baseline = report_tool._deep_merge(auto_base, spec.get("baseline") or {})
    axes = auto_axes + (spec.get("axes") or [])
    if not axes:
        raise SystemExit("스펙에 `axes`가 없다. `mode`를 주거나 축을 직접 적는다.")

    # 축·단계 정규화. 축마다 기준 단계(`base: true`)가 정확히 하나 있어야 한다 —
    # 기준이 없으면 Δ를 어디서 재는지가 정해지지 않는다.
    norm_axes = []
    for ax in axes:
        name = ax.get("name") or "?"
        target = ax.get("target") or subject
        steps = ax.get("steps") or []
        bases = [s for s in steps if s.get("base")]
        if len(bases) != 1:
            raise SystemExit(f"[{name}] 축에는 `base: true` 단계가 정확히 하나 있어야 한다 "
                             f"(현재 {len(bases)}개).")
        if len(steps) < 2:
            raise SystemExit(f"[{name}] 축에 비교할 단계가 없다 (기준 하나뿐).")
        for s in steps:
            if s.get("base") and s.get("over"):
                raise SystemExit(f"[{name}] 기준 단계에는 `over`를 적지 않는다 — "
                                 f"기준 육성은 스펙의 `baseline`이 정본이다.")
        norm_axes.append({"name": name, "target": target, "note": ax.get("note", ""),
                          "steps": [{"label": s.get("label") or "?",
                                     "base": bool(s.get("base")),
                                     "over": s.get("over") or {}} for s in steps]})

    by_key = {_step_key(a["name"], s["label"]): (a, s)
              for a in norm_axes for s in a["steps"]}

    combos = []
    for cb in spec.get("combos") or []:
        refs = cb.get("of") or []
        missing = [r for r in refs if r not in by_key]
        if missing:
            raise SystemExit(f"[조합 {cb.get('label','?')}] 없는 단계를 가리킨다: {missing}\n"
                             f"형식은 `축이름:단계라벨`. 있는 단계: {sorted(by_key)}")
        if len(refs) < 2:
            raise SystemExit(f"[조합 {cb.get('label','?')}] `of`에 단계를 2개 이상 적는다.")
        if len({by_key[r][0]["name"] for r in refs}) != len(refs):
            raise SystemExit(f"[조합 {cb.get('label','?')}] 같은 축의 단계 둘을 겹칠 수 없다.")
        combos.append({"label": cb.get("label") or " + ".join(refs), "of": refs})

    cases: list[dict] = []
    meta_cases: dict[str, dict] = {}
    deck_meta: list[dict] = []
    # (덱 ∥ 축:단계) → 케이스 이름. 중복 제거로 케이스가 합쳐져도 단계는 여기서 되짚는다.
    lookup: dict[str, str] = {}

    for deck in decks:
        dname = deck.get("name") or " / ".join(deck["squad"])
        squad = deck.get("squad") or []
        targets = {a["target"] for a in norm_axes}
        outside = sorted(t for t in targets if t not in squad)
        if outside:
            raise SystemExit(f"[{dname}] 축의 대상이 스쿼드에 없다: {outside}")
        if subject not in squad:
            raise SystemExit(f"[{dname}] 대상 캐릭터 `{subject}`가 스쿼드에 없다.")

        def _case(name: str, chars_over: dict[str, dict]) -> dict:
            c = {"name": name, "group": dname, "squad": list(squad),
                 "chars": chars_over}
            for k in ("defaults", "config", "enemy", "no_layer"):
                if deck.get(k) is not None:
                    c[k] = copy.deepcopy(deck[k])
            return c

        def _chars(extra: dict[str, dict]) -> dict[str, dict]:
            """기준 육성 + 축 오버라이드. 기준은 대상 캐릭터에게만 얹는다."""
            out = {subject: copy.deepcopy(baseline)} if baseline else {}
            for nm, over in extra.items():
                out[nm] = report_tool._deep_merge(out.get(nm, {}), over)
            return out

        base_name = f"{dname}{SEP}기준"
        cases.append(_case(base_name, _chars({})))
        meta_cases[base_name] = {"deck": dname, "kind": "base"}

        # 같은 육성으로 두 번 돌리지 않는다 (축이 달라도 결과 dict가 같으면 한 케이스).
        # 기준 단계도 여기 들어 있어서, 기준과 같은 값을 쓴 단계는 자동으로 기준을 가리킨다.
        seen: dict[str, str] = {
            json.dumps(_chars({}), ensure_ascii=False, sort_keys=True): base_name}

        for ax in norm_axes:
            for st in ax["steps"]:
                key = _step_key(ax["name"], st["label"])
                if st["base"]:
                    lookup[f"{dname}{SEP}{key}"] = base_name
                    continue        # 기준 단계는 덱당 하나뿐인 기준 케이스가 대신한다
                chars = _chars({ax["target"]: st["over"]})
                sig = json.dumps(chars, ensure_ascii=False, sort_keys=True)
                cname = seen.get(sig)
                if cname is None:
                    cname = f"{dname}{SEP}{ax['name']}{SEP}{st['label']}"
                    cases.append(_case(cname, chars))
                    meta_cases[cname] = {"deck": dname, "kind": "step", "step_key": key}
                    seen[sig] = cname
                lookup[f"{dname}{SEP}{key}"] = cname

        for cb in combos:
            chars_extra: dict[str, dict] = {}
            for r in cb["of"]:
                ax, st = by_key[r]
                chars_extra[ax["target"]] = report_tool._deep_merge(
                    chars_extra.get(ax["target"], {}), st["over"])
            chars = _chars(chars_extra)
            cname = f"{dname}{SEP}조합{SEP}{cb['label']}"
            cases.append(_case(cname, chars))
            meta_cases[cname] = {"deck": dname, "kind": "combo", "combo": cb["label"]}

        deck_meta.append({"name": dname, "squad": list(squad), "note": deck.get("note", ""),
                          "base_case": base_name})

    report_spec = {k: v for k, v in spec.items()
                   if k in ("title", "note", "runs", "defaults", "config", "enemy", "no_layer")}
    report_spec["cases"] = cases

    meta = {
        "subject": subject,
        "mode": spec.get("mode", ""),
        "mode_notes": mode_notes,
        "baseline": baseline,
        "axes": norm_axes,
        "combos": combos,
        "decks": deck_meta,
        "cases": meta_cases,
        "lookup": lookup,
    }
    return report_spec, meta


# ── 페어드 델타 ────────────────────────────────────────────────────────────

def _paired(base_runs: list[dict], case_runs: list[dict],
            pick, base_mean: float) -> dict:
    """시드별 차이를 먼저 구하고 그 평균·표준편차를 낸다.

    `pick`은 회차 dict에서 볼 값을 꺼내는 함수 (덱 총딜 또는 대상 캐릭터 딜).
    `sig`는 평균이 표준오차의 2배를 넘는가 — 넘지 못하면 이 시드 수로는 방향조차
    말할 수 없다는 뜻이고, 보고서에서 중립색 + `판정 불가`로 표시된다.
    """
    by_seed = {r["seed"]: r for r in base_runs}
    diffs = [pick(r) - pick(by_seed[r["seed"]]) for r in case_runs if r["seed"] in by_seed]
    n = len(diffs)
    mean = statistics.fmean(diffs) if diffs else 0.0
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    return {
        "mean": mean,
        "std": sd,
        "se": se,
        "n": n,
        "pct": (mean / base_mean * 100) if base_mean else 0.0,
        "se_pct": (se / base_mean * 100) if base_mean else 0.0,
        "sig": bool(se) and abs(mean) > 2 * se,
    }


def analyze(meta: dict, cases: list[dict]) -> dict:
    """report 집계 결과 → 육성 효율 지표.

    반환 구조는 그대로 HTML로 넘어간다 — 렌더러는 계산하지 않는다.
    """
    subject = meta["subject"]
    by_name = {c["name"]: c for c in cases}

    def _self(c: dict) -> float:
        for ch in c["chars"]:
            if ch["name"] == subject:
                return ch["mean"]
        return 0.0

    decks = []
    for d in meta["decks"]:
        base = by_name[d["base_case"]]
        base_total = base["total"]["mean"]
        base_self = _self(base)

        def _delta(cname: str) -> dict | None:
            c = by_name.get(cname)
            if c is None or c["name"] == base["name"]:
                return None
            return {
                "deck": _paired(base["runs"], c["runs"],
                                lambda r: r["squad_total"], base_total),
                "self": _paired(base["runs"], c["runs"],
                                lambda r: r["chars"].get(subject, 0.0), base_self),
            }

        axes = []
        for ax in meta["axes"]:
            steps = []
            prev = None
            for st in ax["steps"]:
                cname = meta["lookup"].get(f"{d['name']}{SEP}{_step_key(ax['name'], st['label'])}")
                dl = _delta(cname) if cname else None
                cur = by_name.get(cname)
                row = {
                    "label": st["label"],
                    "base": st["base"],
                    "total": cur["total"]["mean"] if cur else base_total,
                    "self": _self(cur) if cur else base_self,
                    "delta": dl,
                    # 증분 — 바로 앞 단계 대비. 한계효용 체감이 여기 보인다.
                    "step_deck_pct": None,
                    "step_self_pct": None,
                }
                if prev is not None and base_total:
                    row["step_deck_pct"] = (row["total"] - prev["total"]) / base_total * 100
                    row["step_self_pct"] = ((row["self"] - prev["self"]) / base_self * 100
                                            if base_self else 0.0)
                steps.append(row)
                prev = row
            axes.append({"name": ax["name"], "target": ax["target"], "note": ax["note"],
                         "steps": steps})

        combos = []
        for cb in meta["combos"]:
            cname = f"{d['name']}{SEP}조합{SEP}{cb['label']}"
            dl = _delta(cname)
            if not dl:
                continue
            parts = []
            for r in cb["of"]:
                ax_name, label = r.split(":", 1)
                pc = meta["lookup"].get(f"{d['name']}{SEP}{r}")
                pd = _delta(pc) if pc else None
                parts.append({"ref": r, "axis": ax_name, "label": label,
                              "deck_pct": pd["deck"]["pct"] if pd else 0.0,
                              "self_pct": pd["self"]["pct"] if pd else 0.0})
            sum_deck = sum(p["deck_pct"] for p in parts)
            sum_self = sum(p["self_pct"] for p in parts)
            combos.append({
                "label": cb["label"], "parts": parts,
                "delta": dl, "sum_deck": sum_deck, "sum_self": sum_self,
                "gap_deck": dl["deck"]["pct"] - sum_deck,
                "gap_self": dl["self"]["pct"] - sum_self,
            })

        # 효율 랭킹 — 모든 축의 모든 비-기준 단계를 덱 총딜 Δ 내림차순으로.
        rank = [{"axis": a["name"], "target": a["target"], "label": s["label"], **s}
                for a in axes for s in a["steps"] if not s["base"] and s["delta"]]
        rank.sort(key=lambda r: -r["delta"]["deck"]["pct"])

        decks.append({
            "name": d["name"], "squad": d["squad"], "note": d["note"],
            "base_case": d["base_case"],
            "base_total": base_total, "base_self": base_self,
            "base_cv": base["total"]["cv"],
            "burst_count": base.get("burst_count", 0.0),
            "enemy": base.get("enemy"),
            "axes": axes, "combos": combos, "rank": rank,
        })

    return {"subject": subject, "baseline": meta["baseline"], "decks": decks,
            "mode": meta.get("mode", ""), "mode_notes": meta.get("mode_notes") or []}


# ── 실행 ──────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="육성 효율 보고서 생성 (HTML)")
    ap.add_argument("spec", help="육성 효율 스펙 JSON 경로")
    ap.add_argument("--runs", type=int, help="케이스당 반복 횟수 (기본: 스펙의 runs, 없으면 10)")
    ap.add_argument("--jobs", type=int, default=0, help="병렬 프로세스 수 (0=자동, 1=직렬)")
    ap.add_argument("--out", help="출력 HTML 경로 (기본 reports/out/<스펙명>.html)")
    ap.add_argument("--from-cache", action="store_true",
                    help="시뮬을 다시 돌리지 않고 직전 결과(.data.json)로 HTML만 다시 만든다")
    ap.add_argument("--dry-run", action="store_true",
                    help="케이스 전개만 하고 시뮬 횟수·목록을 보여준 뒤 끝낸다")
    ap.add_argument("--open", action="store_true", help="생성 후 브라우저로 연다")
    args = ap.parse_args()

    out = args.out or os.path.join(_ROOT, "reports", "out",
                                   os.path.splitext(os.path.basename(args.spec))[0] + ".html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cache_path = os.path.splitext(out)[0] + ".data.json"

    if args.from_cache:
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        spec, cases, meta, seeds = (cached["spec"], cached["cases"],
                                    cached["meta"], cached["seeds"])
        print(f"[육성 효율] 캐시 재렌더링: {cache_path}")
    else:
        with open(args.spec, encoding="utf-8") as f:
            raw = json.load(f)
        raw_spec, meta = expand(raw)
        spec = report_tool.build_spec(
            raw_spec, os.path.splitext(os.path.basename(args.spec))[0])
        runs = args.runs or int(raw.get("runs", DEFAULT_RUNS))
        seeds = list(range(1, runs + 1))  # 페어드 비교가 본체다 — 랜덤 시드는 제공하지 않는다
        total = len(spec["cases"]) * runs

        mode_txt = f" [{meta['mode']} 모드]" if meta.get("mode") else ""
        print(f"[육성 효율] {spec['title']}  대상 {meta['subject']}{mode_txt}")
        for n in meta.get("mode_notes") or []:
            print(f"  · {n}")
        print(f"  덱 {len(meta['decks'])} · 축 {len(meta['axes'])} · 조합 {len(meta['combos'])}"
              f"  →  케이스 {len(spec['cases'])}개 × {runs}회 = 시뮬 {total}회")
        if args.dry_run:
            for c in spec["cases"]:
                print(f"    - {c['name']}")
            return

        note = char_spec.preview_note(
            sorted({c.get("name", "") for case in spec["cases"] for c in case["squad"]}))
        if note:
            print(f"⚠ {note}")

        jobs = args.jobs or min(os.cpu_count() or 1, total, 8)
        cases = report_tool.run_report(spec, runs, seeds, jobs)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"spec": spec, "cases": cases, "meta": meta, "seeds": seeds},
                      f, ensure_ascii=False)

    result = analyze(meta, cases)

    from growth_html import render_html
    html = render_html(spec, cases, result, seeds=seeds)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n생성: {out}  ({os.path.getsize(out)/1024:.0f} KB)")

    for d in result["decks"]:
        print(f"\n  [{d['name']}] 기준 {d['base_total']/1e8:.2f}억 "
              f"(대상 {d['base_self']/1e8:.2f}억, CV {d['base_cv']:.2f}%)")
        for r in d["rank"]:
            mark = "" if r["delta"]["deck"]["sig"] else "  (판정 불가)"
            print(f"    {r['axis']} {r['label']:<14} 덱 {r['delta']['deck']['pct']:+6.2f}%"
                  f"  자기 {r['delta']['self']['pct']:+6.2f}%{mark}")

    if args.open:
        import webbrowser
        webbrowser.open("file:///" + out.replace("\\", "/"))


if __name__ == "__main__":
    main()
