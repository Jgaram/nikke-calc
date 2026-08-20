"""소장품 관리키트 소모량 — 기대값.

**메커니즘** (종합.xlsx `소장품관리` 시트의 수식에서 복원, 유저 확인 완료)

    관리 1회 = 키트 10개. 등급별 획득 경험치는 초 200 / 중 500 / 상 1000이다.
    매 회 **대성공** 확률이 있고, 뜨면 그 자리에서 목표 레벨에 도달한다.
    대성공이 안 떠도 경험치는 쌓이며, 목표까지의 경험치를 다 채우면 확정 도달한다.
    대성공 확률은 (직전 회차를 마친 시점의 레벨, 키트 등급)으로 정해진다.

대성공이 목표 레벨까지 보내주므로 **목표는 5·10·15만** 의미가 있다. 시작 레벨은
0~14 아무 값이나 된다 (내 계정의 지금 상태가 그대로 들어온다).

대성공이 보내주는 곳은 **다음 구간의 끝**이지 최종 목표가 아니다. 0→15는 0→5·5→10·
10→15 세 번을 따로 돌려 더한다 — 레벨 0에서 대성공이 떠도 5까지만 간다.

키트를 어떤 순서로 넣느냐는 확률 시행이 아니라 사람이 정하는 것이라, 여기서는 최적
배분을 찾지 않는다. 이 모듈은 넘겨받은 배합을 반복할 뿐이고, 어떤 배합을 쓸지는
`cost.mix_for`가 정한다 (R은 초급만, SR은 수급 비율). 이유는 `README.md`를 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

_TABLES = json.loads((Path(__file__).parent / "tables.json").read_text(encoding="utf-8"))
_C = _TABLES["소장품"]

GRADES = ("초급키트", "중급키트", "상급키트")
TARGETS = (0, 5, 10, 15)          # 대성공이 목표로 보내주므로 중간 레벨은 목표가 못 된다
MAX_START = 14

# 확률이 이만큼 남으면 끊는다. 남은 확률 × 남은 회차는 그보다 훨씬 작아 표시 자리에
# 영향을 주지 않는다 (표준 비율 기준 15레벨 완주가 수백 회를 넘지 않는다).
_EPS = 1e-12


def spread(counts: dict[str, int]) -> list[str]:
    """등급별 개수 → 주기 전체에 고르게 흩은 순서.

    대성공이 뜨면 주기 도중에 끝나므로 순서가 결과를 바꾼다. 한 등급을 앞에 몰아두면
    그 등급이 실제 소모에서 과대 대표되고, 점수를 표준 비율로 되돌린 값과 어긋난다.
    희소한 등급부터 자리를 잡고 충돌하면 뒤로 밀어 채운다.
    """
    n = sum(counts.values())
    slots: list[str | None] = [None] * n
    for g, c in sorted(counts.items(), key=lambda x: x[1]):
        for i in range(c):
            p = round((i + 0.5) * n / c) % n
            while slots[p] is not None:
                p = (p + 1) % n
            slots[p] = g
    return [s for s in slots if s is not None]


def _great(grade: str, level: int, kit: str) -> float:
    """대성공 확률. 레벨은 표 범위(0~14)로 자른다."""
    row = _C["대성공"][grade][min(max(level, 0), MAX_START)]
    return row[GRADES.index(kit)]


def consume(grade: str, start: int, target: int, mix: list[str]) -> dict[str, float]:
    """`mix`를 반복해서 넣을 때의 기대 소모 키트 수 → {등급: 개수}.

    반환값은 **키트 개수**다 (회 수가 아니다). 목표에 이미 도달했으면 전부 0.
    구간(5·10·15)마다 따로 돌려 더한다 — 대성공은 구간 끝까지만 보내고, 배합
    주기도 구간마다 처음부터 다시 시작한다.
    """
    if grade not in _C["대성공"]:
        raise ValueError(f"소장품 등급은 R·SR만 있다: {grade!r}")
    if target not in TARGETS:
        raise ValueError(f"목표 레벨은 {TARGETS} 중 하나여야 한다: {target}")
    if not 0 <= start <= MAX_START:
        raise ValueError(f"시작 레벨은 0~{MAX_START}이어야 한다: {start}")
    if not mix:
        raise ValueError("빈 배합으로는 계산할 수 없다")

    out = {k: 0.0 for k in GRADES}
    cur = start
    for bound in (b for b in TARGETS if b):
        if bound <= cur or bound > target:
            continue
        for k, v in _segment(grade, cur, bound, mix).items():
            out[k] += v
        cur = bound
    return out


def _segment(grade: str, start: int, target: int, mix: list[str]) -> dict[str, float]:
    """한 구간(시작 → 그 다음 5의 배수) 안에서의 기대 소모 키트 수."""
    out = {k: 0.0 for k in GRADES}
    per_level = _C["레벨당_경험치"][grade]
    per_use = _C["회당_경험치"]
    kits_per_use = _C["회당_키트"]
    goal_exp = (target - start) * per_level

    cum_exp = 0            # 누적 경험치
    alive = 1.0            # 아직 목표에 도달하지 못했을 확률
    used = {k: 0.0 for k in GRADES}
    level = start          # 직전 회차를 마친 시점의 레벨

    i = 0
    while alive > _EPS:
        kit = mix[i % len(mix)]
        i += 1
        p = _great(grade, level, kit)

        cum_exp += per_use[kit]
        used[kit] += kits_per_use

        if cum_exp >= goal_exp:
            # 경험치만으로 목표를 채웠다 — 대성공 여부와 무관하게 이 회차에서 끝난다.
            for k in GRADES:
                out[k] += alive * used[k]
            return out

        for k in GRADES:
            out[k] += alive * p * used[k]
        alive *= 1.0 - p
        level = start + cum_exp // per_level

    return out


def table(grade: str, mix: list[str]) -> dict[tuple[int, int], dict[str, float]]:
    """(시작, 목표) → 소모량. 시작 0~14 × 목표 5·10·15 중 시작 < 목표인 것만."""
    return {(s, t): consume(grade, s, t, mix)
            for t in TARGETS if t
            for s in range(0, min(t, MAX_START + 1))}


def score(kits: dict[str, float], weights: dict[str, float]) -> float:
    """키트 개수 → 점수. 1점 = 초급키트 1개."""
    return sum(kits.get(k, 0.0) * weights[k] for k in GRADES)


def unscore(points: float, mix: list[str], weights: dict[str, float]) -> dict[str, float]:
    """점수 → `mix` 비율로 쪼갠 키트 개수. `score`의 역이고, 보고할 때 쓴다."""
    share = {k: float(mix.count(k)) * _C["회당_키트"] for k in GRADES}
    unit = sum(share[k] * weights[k] for k in GRADES)
    return {k: points * share[k] / unit for k in GRADES}
