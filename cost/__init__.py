"""재화 소모량 — 육성 한 칸을 올리는 데 뭐가 얼마나 드는가.

여기서 나오는 값은 **소모량**이지 효율이 아니다. 효율은 이 소모량과 `report-growth`가
잰 딜량 Δ를 나눠서 나온다.

확정 재화(메뉴얼·장비 경험치)와 확률 시행 재화(모듈·관리키트)를 한 자리에서 다루되,
둘을 같은 단위로 합치지 않는다. 확률 시행 쪽은 기대값이고 그 사실을 `expected`로 표시한다.

    from cost import skill_manual, gear, collection, breakthrough, module, points_to_kits

키트의 **희소가치 가중치와 표준 배합은 둘 다 `tables.json`의 수급량에서 파생된다.**
수급처를 하나 더하면 두 개가 같이 움직인다 — 어느 쪽도 손으로 적어두지 않는다.

무거운 계산은 `collection`뿐이고 그것도 한 번에 수십 ms다. 웹앱은 이 패키지를 부르지
않고 `build.py`가 구워둔 `data/cost_expected.json`을 읽는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from calculator.base_stat import NO_ITEM  # 미장착 표현. 정본은 그쪽이라 복사하지 않는다

from . import kit

TABLES = json.loads((Path(__file__).parent / "tables.json").read_text(encoding="utf-8"))

_SUP = TABLES["키트_수급"]
_MIX = TABLES["표준_비율"]


def _box(name: str, count: float) -> dict[str, float]:
    """상자 `count`개의 기대 내용물."""
    per = _SUP["상자"][name]
    return {g: per[g] * count for g in kit.GRADES}


def _add(into: dict[str, float], part: dict[str, float]) -> None:
    for g in kit.GRADES:
        into[g] += part.get(g, 0.0)


def kit_supply_by_source() -> dict[str, dict[str, float]]:
    """수급처별 30일 관리키트 → {수급처: {등급: 개수}}. 전부 원시값에서 계산한다."""
    days = _SUP["기간_일"]
    out: dict[str, dict[str, float]] = {}

    # 파견 — 하루에 슬롯 수만큼 돌고, 각 슬롯이 항목표대로 하나를 준다.
    d = _SUP["파견"]
    per_run = {g: 0.0 for g in kit.GRADES}
    total_p = 0.0
    for item in d["항목"]:
        p, qty, what = item["확률"], item["수량"], item["품목"]
        total_p += p
        got = _box(what, qty) if what in _SUP["상자"] else {what: float(qty)}
        _add(per_run, {g: p * v for g, v in got.items()})
    # 항목표가 온전한지 — 키트 항목 + 마일리지가 1.0이 되어야 한다.
    if abs(total_p + d["마일리지_확률"] - 1.0) > 1e-9:
        raise ValueError(f"파견 항목 확률 합이 1이 아니다: {total_p} + {d['마일리지_확률']}")
    out["파견"] = {g: per_run[g] * d["슬롯"] * days for g in kit.GRADES}

    # 솔로레이드 — 시즌 보상 상자
    sr = {g: 0.0 for g in kit.GRADES}
    for name, n in _SUP["솔로레이드"].items():
        _add(sr, _box(name, n))
    out["솔로레이드"] = sr

    # 뮤지엄 — 6개월에 한 번 여러 번 받는 것을 월로 편다.
    m = _SUP["뮤지엄"]
    per_month = m["회수"] / m["주기_개월"]
    out["뮤지엄"] = {g: m["회당"][g] * per_month for g in kit.GRADES}

    # 고철상점 — 월 구매
    shop = {g: 0.0 for g in kit.GRADES}
    for name, n in _SUP["고철상점"].items():
        _add(shop, _box(name, n))
    out["고철상점"] = shop

    return out


def kit_supply() -> dict[str, float]:
    """30일 관리키트 수급 합계 → {등급: 개수}."""
    total = {g: 0.0 for g in kit.GRADES}
    for part in kit_supply_by_source().values():
        _add(total, part)
    return total


def kit_weights() -> dict[str, float]:
    """희소가치 = 초급키트 수급량 ÷ 그 등급 수급량. 초급키트 1개가 1점이다."""
    s = kit_supply()
    return {g: s["초급키트"] / s[g] for g in kit.GRADES}


def standard_mix(name: str = "수급비율") -> list[str]:
    """배합 한 주기. 원소 하나가 관리 1회(=키트 10개)다.

    `수급비율`은 수급에서 파생한다 — 중·상급 키트는 다른 쓸 곳이 없어 실제로 쓸 수
    있는 비율이 곧 수급 비율이다. 나머지는 `tables.json`에 개수가 박힌 고정 배합이다.
    """
    if name != "수급비율":
        return kit.spread(_MIX["_고정_배합"][name])
    return kit.spread(_largest_remainder(kit_supply(), _MIX["주기_길이"]))


def mix_for(grade: str) -> list[str]:
    """소장품 등급의 기본 배합. R은 초급만, SR은 수급 비율이다 (`tables.json`)."""
    return standard_mix(_MIX["_등급별_기본"][grade])


def _largest_remainder(supply: dict[str, float], slots: int) -> dict[str, int]:
    """수급 비율을 `slots`칸에 최대잉여법으로 나눈다. 어느 등급도 0칸이 되지 않는다."""
    total = sum(supply.values())
    exact = {g: supply[g] / total * slots for g in supply}
    counts = {g: max(1, int(exact[g])) for g in exact}
    order = sorted(exact, key=lambda g: exact[g] - int(exact[g]), reverse=True)
    i = 0
    while sum(counts.values()) < slots:
        counts[order[i % len(order)]] += 1
        i += 1
    return counts


def daily_points() -> float:
    """수급량을 점수로 환산해 하루치로 나눈 값. 점수를 '며칠치'로 읽을 때 쓴다."""
    w, s = kit_weights(), kit_supply()
    return sum(s[g] * w[g] for g in s) / _SUP["기간_일"]


def manual_cost() -> dict[int, float]:
    """레벨 → 그 레벨에 도달하는 데 드는 메뉴얼 장수 (가중치 적용).

    기본 가중치가 노랑만 1이라 사실상 노랑칩 장수다. 0인 등급도 표에는 남아 있어서
    병목이 바뀌면 `tables.json`의 가중치만 고치면 된다.
    """
    t = TABLES["스킬_메뉴얼"]
    w = t["가중치"]
    out = {}
    for lv, per in t["레벨도달비용"].items():
        n = sum(per[g] * w[g] for g in per)
        if n:
            out[int(lv)] = float(n)
    return out


def skill_manual(skill_key: str, frm: int, to: int) -> dict:
    """스킬 레벨 `frm` → `to`에 드는 메뉴얼. 확정 재화라 기대값이 아니다.

    `breakdown`에는 가중치 0인 등급까지 실제 장수가 들어간다 — 효율에는 안 쓰지만
    "실제로 몇 장 드는가"를 물었을 때 답이 없으면 곤란하다.
    """
    t = TABLES["스킬_메뉴얼"]
    kind = t["종류"][skill_key]
    levels = [lv for lv in range(frm + 1, to + 1) if str(lv) in t["레벨도달비용"]]
    breakdown = {g: float(sum(t["레벨도달비용"][str(lv)][g] for lv in levels))
                 for g in t["가중치"]}
    weighted = sum(breakdown[g] * t["가중치"][g] for g in breakdown)
    return {"cost": {kind: float(weighted)}, "expected": False,
            "breakdown": breakdown}


def gear(track: str, frm: int, to: int) -> dict:
    """장비 강화 `frm` → `to` 레벨에 드는 장비 경험치. `track`은 T9·오버로드.

    무엇을 갈아 넣든 경험치는 같아서 재화가 하나뿐이고, 확률 시행이 아니다.
    """
    t = TABLES["장비강화"]
    if track not in t["레벨도달비용"]:
        raise ValueError(f"강화 갈래는 {list(t['레벨도달비용'])} 중 하나여야 한다: {track!r}")
    steps = t["레벨도달비용"][track]
    exp = sum(steps[str(lv)] for lv in range(frm + 1, to + 1) if str(lv) in steps)
    return {"cost": {t["재화"]: float(exp)}, "expected": False,
            "days": exp / t["하루_수급"]}


def collection(grade: str, start: int, target: int,
               mix: str | list[str] | None = None) -> dict:
    """소장품 `start` → `target` 레벨에 드는 관리키트.

    시작은 0~14 아무 값이나 되고 (1→5, 8→10 같은 것도 그대로 나온다) 목표는 5·10·15만
    된다. 배합을 안 주면 등급별 기본을 쓴다 — **R은 초급키트만, SR은 셋을 섞는다.**

    `points`는 초급키트 1개를 1점으로 놓은 희소가치 환산값이다. 축끼리 순위를 매길 때
    쓰고, 사람에게 보고할 때는 `cost`의 숫자를 그대로 쓴다.
    """
    if mix is None:
        seq = mix_for(grade)
    else:
        seq = standard_mix(mix) if isinstance(mix, str) else list(mix)
    kits = kit.consume(grade, start, target, seq)
    pts = kit.score(kits, kit_weights())
    return {"cost": kits, "expected": True,
            "points": pts, "days": pts / daily_points()}


def breakthrough(frm: tuple[int, int], to: tuple[int, int]) -> dict:
    """돌파·코어강화 `(돌파, 코어강화)` → `(돌파, 코어강화)`에 드는 뽑기 횟수.

    둘 다 캐릭터 한 장을 먹으므로 한 칸을 1뽑기로 통일해서 센다. 캐릭터마다 뽑기
    확률이 다르지만 그 차이는 다루지 않는다 — 축끼리 순위를 매기는 데는 칸 수로 충분하다.

    코어강화는 돌파 3 이후에 해금되므로, 코어가 0보다 큰데 돌파가 3이 아니면 거부한다.
    """
    t = TABLES["돌파코강"]
    bt_max, ce_max = t["돌파_최대"], t["코어강화_최대"]

    def _check(pair: tuple[int, int], what: str) -> None:
        bt, ce = pair
        if not 0 <= bt <= bt_max or not 0 <= ce <= ce_max:
            raise ValueError(f"{what} 범위를 벗어났다 — 돌파 0~{bt_max}, 코어강화 0~{ce_max}: {pair}")
        if ce and bt != bt_max:
            raise ValueError(f"{what}: 코어강화는 돌파 {bt_max} 이후에 해금된다: {pair}")

    _check(frm, "시작")
    _check(to, "목표")
    steps = (to[0] - frm[0]) + (to[1] - frm[1])
    if steps < 0:
        raise ValueError(f"목표가 시작보다 낮다: {frm} → {to}")
    return {"cost": {t["재화"]: float(steps * t["칸당_뽑기"])}, "expected": False}


_STAGE = re.compile(r"^(SR|R)(\d{1,2})$")


def parse_stage(stage: str) -> tuple[str, int] | None:
    """`collection_stage` 문자열 → (등급, 레벨). 미장착이면 None.

    시뮬레이터가 쓰는 표현을 그대로 받는다 — `"R0"`~`"R15"` · `"SR0"`~`"SR15"` ·
    `"없음"`(미장착). 정본은 `calculator.base_stat.collection_stat`이다.
    """
    if stage == NO_ITEM:
        return None
    m = _STAGE.match(stage)
    if not m or int(m.group(2)) > 15:
        raise ValueError(f"알 수 없는 소장품 단계 {stage!r} — "
                         f"'R0'~'R15' · 'SR0'~'SR15' 또는 {NO_ITEM!r}")
    return m.group(1), int(m.group(2))


def collection_to(stage: str, target: int = 15) -> dict | None:
    """지금 상태(`collection_stage` 문자열)에서 `target`까지 드는 관리키트.

    프로필의 육성 상태를 그대로 넣는 자리다. 미장착이면 **None** — 소장품 아이템
    자체를 얻는 비용은 다루지 않기 때문이다 (`tables.json`의 `_안다룸`).
    이미 목표에 닿아 있으면 소모 0으로 나온다.
    """
    parsed = parse_stage(stage)
    if parsed is None:
        return None
    grade, level = parsed
    if level >= target:
        return {"cost": {}, "expected": True, "points": 0.0, "days": 0.0}
    return collection(grade, level, target)


def module(goal: str) -> dict:
    """커스텀 모듈 기대 소모량. 목표 문자열은 `tables.json`에 있는 것만 된다."""
    table = TABLES["오버로드_모듈"]
    if goal not in table:
        raise ValueError(f"모듈 목표는 {list(table)} 중 하나여야 한다: {goal!r}")
    return {"cost": {"커스텀 모듈": float(table[goal])}, "expected": True}


def points_to_kits(points: float, grade: str = "SR",
                   mix: str | list[str] | None = None) -> dict[str, float]:
    """점수 → 배합 비율의 키트 개수. 보고할 때 되돌리는 방향이다.

    등급을 주면 그 등급의 기본 배합으로 쪼갠다 — R이면 초급키트 한 줄로만 나온다.
    """
    if mix is None:
        seq = mix_for(grade)
    else:
        seq = standard_mix(mix) if isinstance(mix, str) else list(mix)
    return kit.unscore(points, seq, kit_weights())
