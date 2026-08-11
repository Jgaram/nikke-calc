"""육성 효율 보고서 러너.

한 캐릭터의 육성 변수(스킬 레벨·장비 옵션·소장품 …)를 **기준점에서 한 축씩** 움직여
덱 총딜과 그 캐릭터 자신의 딜이 각각 얼마나 오르는지 잰다.

    python .agent/skills/report-growth/scripts/growth.py .report-work/<이름>/spec.json
    python .agent/skills/report-growth/scripts/growth.py <스펙> --runs 12 --jobs 8 --open
    python .agent/skills/report-growth/scripts/growth.py <스펙> --from-cache

스펙 형식은 `.agent/skills/report-growth/references/format.md` 참조.

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
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
_REPORT = os.path.join(_ROOT, ".agent", "skills", "report-squad", "scripts")
for _p in (_ROOT, _REPORT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import report as report_tool  # noqa: E402  (sys.path 조정 뒤에 와야 한다)
from context import spec as char_spec  # noqa: E402
from report_workspace import (  # noqa: E402
    data_path, output_path, prepare, preserve_spec, slug_from_spec, spec_path,
    write_index, write_manifest,
)

DEFAULT_RUNS = 10

# ── 모드 ───────────────────────────────────────────────────────────────────
# 스킬과 옵션을 한 보고서에 섞지 않는다. 둘은 기준이 서로 달라야 하기 때문이다 —
# 옵션 효율은 스킬이 만렙일 때의 값이고, 스킬 효율은 옵션이 기본값일 때의 값이다.
# 섞으면 어느 쪽 Δ도 "지금 내 계정에서의 값"이 아니게 된다.

SKILL_STEPS = [7, 8, 9, 10]        # 기준 7 + 8·9·10
OPTION_LINES = [0, 1, 2, 3, 4]     # 오버로드 줄 수. 전부 레벨 10

# ── 스킬 메뉴얼 비용 ───────────────────────────────────────────────────────
# 그 레벨에 **도달하는 데** 드는 메뉴얼 수. 7레벨까지는 사실상 무제한 수급이라 0으로 본다.
MANUAL_COST = {8: 90, 9: 105, 10: 120}
# 1·2스킬은 스킬 메뉴얼, 버스트(3)는 버스트 메뉴얼만 먹는다 — 서로 대체되지 않는다.
MANUAL_KIND = {"1": "스킬 메뉴얼", "2": "스킬 메뉴얼", "3": "버스트 메뉴얼"}
# 비용은 **각 메뉴얼의 실제 장수 그대로** 적고 한 단위로 환산하지 않는다. 스킬 메뉴얼
# 수급량이 버스트 메뉴얼의 두 배라는 사실은 보고서에 적지 않는다 — 계정마다 다른 사정이라
# 숫자로 굳히지 않고, 종류가 다른 줄을 나란히 옮길 때 사람이 덧붙인다 (`SKILL.md` Step 5).

# 옵션 모드 기본 축. 라벨은 보고서에 그대로 나온다.
OPTION_AXES = [("공격력 옵션", "atk_pct"), ("최대장탄 옵션", "max_ammo_pct"),
               ("크리티컬 확률 옵션", "crit_rate"), ("크리티컬 대미지 옵션", "crit_dmg")]
# 차지형(RL·SR)에만 붙는 축. 다른 무기군에는 아무 효과가 없어 축을 넣어도 전부 0이 된다.
CHARGE_AXES = [("차지속도 옵션", "charge_speed_pct"), ("차지대미지 옵션", "charge_dmg_pct")]
CHARGE_WEAPONS = {"RL", "SR"}
# 우월코드는 **기준이 4줄**이다. 다른 축과 기준을 공유해야 나란히 비교할 수 있어서,
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


def _cost_cfg(spec: dict) -> dict:
    """메뉴얼 비용 모델. `"cost": {"enabled": false}`로 끌 수 있다.

    비용은 `skill_key`가 붙은 축(= `mode: "skill"`이 만든 축)에만 적용된다.
    손으로 적은 축은 무엇을 먹는지 알 수 없으므로 비용 없이 Δ만 나온다.
    """
    c = spec.get("cost") or {}
    if c.get("enabled") is False:
        return {}
    return {
        "level_cost": {int(k): float(v)
                       for k, v in (c.get("level_cost") or MANUAL_COST).items()},
        "kind": {**MANUAL_KIND, **(c.get("kind") or {})},
    }


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
        # `skill_key`·`level`은 메뉴얼 비용을 되짚기 위한 꼬리표다 (`_cost_cfg`).
        axes = [
            {"name": nm, "skill_key": k,
             "steps": [{"label": str(lv), "level": lv,
                        **({"base": True} if lv == base_lv
                           else {"over": {"skill_levels": {k: lv}}})}
                       for lv in steps_lv]}
            for k, nm in (("1", "1스킬 레벨"), ("2", "2스킬 레벨"), ("3", "버스트 레벨"))
        ]
        notes.append(f"스킬 조사 — 장비 옵션은 기본 스펙 그대로, 기준 스킬 레벨 {base_lv}")
        if _cost_cfg(spec):
            notes.append(f"재화 효율은 메뉴얼 장수 그대로 — {base_lv}레벨까지는 무료로 본다")
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
                          "skill_key": ax.get("skill_key") or "",
                          "steps": [{"label": s.get("label") or "?",
                                     "base": bool(s.get("base")),
                                     "level": s.get("level"),
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
        "cost": _cost_cfg(spec),
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


# ── 재화 비용 ──────────────────────────────────────────────────────────────

def _axis_cost(ax: dict, cost: dict) -> dict | None:
    """축 하나의 레벨별 메뉴얼 비용 → {kind, weight, base_level, step, cum} 또는 None.

    `cum[lv]`는 기준 레벨에서 `lv`까지의 총액, `step[lv]`는 **축에 있는 직전 단계**에서
    한 칸 올리는 값이다. 단계를 건너뛴 스펙(7→10)도 그래서 맞게 나온다.
    """
    key = ax.get("skill_key")
    if not cost or not key:
        return None
    base = next((s.get("level") for s in ax["steps"] if s["base"]), None)
    kind = cost["kind"].get(key)
    if base is None or kind is None:
        return None
    lc = cost["level_cost"]
    step, cum, prev_cum = {}, {}, 0.0
    for lv in sorted(s["level"] for s in ax["steps"]
                     if s.get("level") is not None and s["level"] > base):
        if any(x not in lc for x in range(base + 1, lv + 1)):
            return None       # 비용표에 없는 레벨이 끼면 이 축은 비용 없이 Δ만 낸다
        cum[lv] = sum(lc[x] for x in range(base + 1, lv + 1))
        step[lv] = cum[lv] - prev_cum
        prev_cum = cum[lv]
    if not cum:
        return None
    return {"kind": kind, "base_level": base, "step": step, "cum": cum}


def _step_pct(row: dict, metric: str) -> float:
    """직전 단계 대비 증분 Δ%. 페어드 값이 있으면 그것을 쓴다."""
    sd = row.get("step_delta")
    if sd:
        return sd[metric]["pct"]
    return (row["step_deck_pct"] if metric == "deck" else row["step_self_pct"]) or 0.0


def _step_cost(levels: dict, lv: int, row: dict) -> dict:
    """한 단계(직전 단계 → 이 레벨)의 메뉴얼 장수와 장당 효율.

    효율은 **100장당 Δ%** 다. 1장당으로 적으면 소수점 넷째 자리라 표에서 읽히지 않는다.
    """
    step_raw = levels["step"][lv]

    def _per100(pct: float) -> float:
        return pct / step_raw * 100 if step_raw else 0.0

    return {
        "kind": levels["kind"],
        "cost": step_raw,
        "per100": {m: _per100(_step_pct(row, m)) for m in ("deck", "self")},
        "sig": bool(row.get("step_delta") and row["step_delta"]["deck"]["sig"]),
    }


def _cost_rows(axes: list[dict]) -> list[dict]:
    """재화 효율 표의 줄 — **축 순서·레벨 순서 그대로**.

    효율순으로 재정렬하지 않는다. 스킬창과 같은 순서로 읽히는 편이, 정렬된 순위표보다
    "지금 내 캐릭터의 이 칸이 얼마짜리인가"를 찾기 쉽다. 순위는 `100장당` 열로 읽는다.
    """
    rows = []
    for a in axes:
        for s in a["steps"]:
            if s["base"] or not s["cost"]:
                continue
            c = s["cost"]
            rows.append({"axis": a["name"], "kind": c["kind"],
                         "from": s["prev_label"], "to": s["label"], "cost": c["cost"],
                         "deck_pct": _step_pct(s, "deck"), "self_pct": _step_pct(s, "self"),
                         "per100": c["per100"]["deck"], "per100_self": c["per100"]["self"],
                         "sig": c["sig"]})
    return rows


def _cost_total(rows: list[dict]) -> list[dict]:
    """만렙까지의 메뉴얼 총액. 종류가 다른 재화는 **합치지 않고** 따로 센다."""
    out: dict[str, float] = {}
    for r in rows:
        out[r["kind"]] = out.get(r["kind"], 0.0) + r["cost"]
    return [{"kind": k, "cost": v} for k, v in out.items()]


def analyze(meta: dict, cases: list[dict]) -> dict:
    """report 집계 결과 → 육성 효율 지표.

    반환 구조는 그대로 HTML로 넘어간다 — 렌더러는 계산하지 않는다.
    """
    subject = meta["subject"]
    by_name = {c["name"]: c for c in cases}
    cost = meta.get("cost") or {}

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
            levels = _axis_cost(ax, cost)
            steps = []
            prev = None
            for st in ax["steps"]:
                cname = meta["lookup"].get(f"{d['name']}{SEP}{_step_key(ax['name'], st['label'])}")
                dl = _delta(cname) if cname else None
                cur = by_name.get(cname)
                row = {
                    "label": st["label"],
                    "base": st["base"],
                    "level": st.get("level"),
                    "case": (cur or base)["name"],
                    "total": cur["total"]["mean"] if cur else base_total,
                    "self": _self(cur) if cur else base_self,
                    "delta": dl,
                    # 증분 — 바로 앞 단계 대비. 한계효용 체감이 여기 보인다.
                    "prev_label": prev["label"] if prev else None,
                    "step_delta": None,
                    "step_deck_pct": None,
                    "step_self_pct": None,
                    "cost": None,
                }
                if prev is not None and base_total:
                    row["step_deck_pct"] = (row["total"] - prev["total"]) / base_total * 100
                    row["step_self_pct"] = ((row["self"] - prev["self"]) / base_self * 100
                                            if base_self else 0.0)
                    # 증분도 페어드로 잰다 — 평균끼리 빼도 값은 같지만, 이렇게 해야
                    # 이 증분이 노이즈보다 큰지(±2SE)를 말할 수 있다.
                    pc = by_name.get(prev["case"])
                    if pc is not None and pc["name"] != row["case"]:
                        row["step_delta"] = {
                            "deck": _paired(pc["runs"], by_name[row["case"]]["runs"],
                                            lambda r: r["squad_total"], base_total),
                            "self": _paired(pc["runs"], by_name[row["case"]]["runs"],
                                            lambda r: r["chars"].get(subject, 0.0), base_self),
                        }
                if levels and not st["base"] and st.get("level") in levels["cum"]:
                    row["cost"] = _step_cost(levels, st["level"], row)
                steps.append(row)
                prev = row
            axes.append({"name": ax["name"], "target": ax["target"], "note": ax["note"],
                         "skill_key": ax.get("skill_key", ""),
                         "kind": levels["kind"] if levels else "",
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

        # 모든 축의 모든 비-기준 단계를 덱 총딜 Δ 내림차순으로. 보고서에 표로 나가지는
        # 않는다(축별 상세와 겹친다) — 덱 간 대조·막대 배율·콘솔 요약이 이걸 쓴다.
        rank = [{"axis": a["name"], "target": a["target"], "label": s["label"], **s}
                for a in axes for s in a["steps"] if not s["base"] and s["delta"]]
        rank.sort(key=lambda r: -r["delta"]["deck"]["pct"])

        cost_rows = _cost_rows(axes)

        decks.append({
            "name": d["name"], "squad": d["squad"], "note": d["note"],
            "base_case": d["base_case"],
            "base_total": base_total, "base_self": base_self,
            "base_cv": base["total"]["cv"],
            "burst_count": base.get("burst_count", 0.0),
            "enemy": base.get("enemy"),
            "axes": axes, "combos": combos, "rank": rank,
            "cost_rows": cost_rows, "cost_total": _cost_total(cost_rows),
        })

    return {"subject": subject, "baseline": meta["baseline"], "decks": decks,
            "mode": meta.get("mode", ""), "mode_notes": meta.get("mode_notes") or [],
            "cost": cost}


# ── 실행 ──────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="육성 효율 보고서 생성 (HTML)")
    ap.add_argument("spec", help="육성 효율 스펙 JSON 경로")
    ap.add_argument("--runs", type=int, help="케이스당 반복 횟수 (기본: 스펙의 runs, 없으면 10)")
    ap.add_argument("--jobs", type=int, default=0, help="병렬 프로세스 수 (0=자동, 1=직렬)")
    ap.add_argument("--out", help="출력 HTML 경로 (기본 reports/<스펙명>.html)")
    ap.add_argument("--from-cache", action="store_true",
                    help="시뮬을 다시 돌리지 않고 직전 결과(.data.json)로 HTML만 다시 만든다")
    ap.add_argument("--dry-run", action="store_true",
                    help="케이스 전개만 하고 시뮬 횟수·목록을 보여준 뒤 끝낸다")
    ap.add_argument("--open", action="store_true", help="생성 후 브라우저로 연다")
    args = ap.parse_args()

    slug = slug_from_spec(args.spec)
    prepare(slug)
    out = Path(args.out).resolve() if args.out else output_path(slug)
    out.parent.mkdir(parents=True, exist_ok=True)
    cache_path = data_path(slug)

    if args.from_cache:
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        spec, cases, meta, seeds = (cached["spec"], cached["cases"],
                                    cached["meta"], cached["seeds"])
        print(f"[육성 효율] 캐시 재렌더링: {cache_path}")
        # 메타는 스펙에서 다시 만든다 — 지표가 늘어난 뒤에도 옛 캐시가 그대로 살아나도록.
        # 케이스 이름이 하나라도 어긋나면 스펙이 바뀐 것이므로 캐시 쪽을 그대로 둔다.
        try:
            fresh_path = Path(args.spec) if Path(args.spec).exists() else spec_path(slug)
            with open(fresh_path, encoding="utf-8") as f:
                fresh_spec, fresh_meta = expand(json.load(f))
            if ({c["name"] for c in fresh_spec["cases"]} == {c["name"] for c in cases}):
                meta = fresh_meta
            else:
                print("  ⚠ 스펙의 케이스가 캐시와 다르다 — 캐시에 저장된 메타로 그린다. "
                      "새 지표가 필요하면 시뮬을 다시 돌린다")
        except (OSError, ValueError, SystemExit) as e:
            print(f"  ⚠ 스펙을 다시 읽지 못해 캐시 메타로 그린다 ({e})")
    else:
        preserve_spec(args.spec, slug)
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
    write_manifest(slug, kind="report-growth", title=spec.get("title", slug))
    write_index()
    print(f"\n생성: {out}  ({out.stat().st_size/1024:.0f} KB)")

    for d in result["decks"]:
        print(f"\n  [{d['name']}] 기준 {d['base_total']/1e8:.2f}억 "
              f"(대상 {d['base_self']/1e8:.2f}억, CV {d['base_cv']:.2f}%)")
        for r in d["rank"]:
            mark = "" if r["delta"]["deck"]["sig"] else "  (판정 불가)"
            print(f"    {r['axis']} {r['label']:<14} 덱 {r['delta']['deck']['pct']:+6.2f}%"
                  f"  자기 {r['delta']['self']['pct']:+6.2f}%{mark}")

        if d["cost_rows"]:
            print("    ─ 재화 효율 (100장당 덱 딜 Δ)")
            for r in d["cost_rows"]:
                mark = "" if r["sig"] else "  (판정 불가)"
                print(f"      {r['axis']} {r['from']}→{r['to']:<3} "
                      f"{r['cost']:>4.0f}장 {r['kind']} · 덱 {r['deck_pct']:+.2f}% · "
                      f"100장당 {r['per100']:+.3f}%p{mark}")
            print("      총 " + " + ".join(f"{t['kind']} {t['cost']:.0f}장"
                                           for t in d["cost_total"]))

    if args.open:
        import webbrowser
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
