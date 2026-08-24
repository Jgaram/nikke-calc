"""오버로드 장비 옵션 뽑기의 게임 규칙 — 이 패키지에서 규칙의 정본이다.

수치 근거는 `README.md` §규칙. 여기에는 규칙만 두고, 옵션의 **가치**(딜 기여)는
`value.py`가, 뽑기 **정책**은 `reach.py`·`policy.py`가 맡는다.

옵션 이름은 인게임 축약어(`우코`·`공`···)를 쓴다. 계산기 쪽 stat 키와의 대응은
`STAT_KEY`에 두고, 옵션별 레벨 수치는 여기서 다시 적지 않는다 —
정본은 `data/base_stat_tables/equipment_skills.json`이고 `value.py`가 읽는다.
"""

from __future__ import annotations

from fractions import Fraction

# ── 옵션 ───────────────────────────────────────────────────────────────────
# 뽑기 가중치(합 100). 인게임 표기 그대로 %로 읽힌다.
WEIGHTS: dict[str, int] = {
    "우코": 10, "공": 10, "크댐": 10, "방어": 10,
    "크확": 12, "명중": 12, "차속": 12, "장탄": 12, "차댐": 12,
}
OPTIONS: tuple[str, ...] = tuple(WEIGHTS)

# 옵션 → `equip_skills`(계산기 입력) 키. 이름 대응의 정본.
STAT_KEY: dict[str, str] = {
    "우코": "element_bonus",
    "공": "atk_pct",
    "크댐": "crit_dmg",
    "방어": "def_pct",
    "크확": "crit_rate",
    "명중": "accuracy_pct",
    "차속": "charge_speed_pct",
    "장탄": "max_ammo_pct",
    "차댐": "charge_dmg_pct",
}

# ── 슬롯 ───────────────────────────────────────────────────────────────────
# 한 부위에 슬롯 3칸. 뽑기마다 각 칸에 옵션이 **등장할** 확률이 다르다.
SLOTS: tuple[str, ...] = ("A", "B", "C")
P_SHOW: dict[str, Fraction] = {
    "A": Fraction(1),
    "B": Fraction(1, 2),
    "C": Fraction(3, 10),
}

# ── 레벨 ───────────────────────────────────────────────────────────────────
# 옵션이 등장하면 레벨 1~15가 함께 랜덤으로 정해진다. 낮을수록 흔하다.
# 1~5 각 12% / 6~10 각 7% / 11~15 각 1% (합 100%).
LEVELS: tuple[int, ...] = tuple(range(1, 16))
LEVEL_P: dict[int, Fraction] = {
    **{lv: Fraction(3, 25) for lv in range(1, 6)},
    **{lv: Fraction(7, 100) for lv in range(6, 11)},
    **{lv: Fraction(1, 100) for lv in range(11, 16)},
}

assert sum(LEVEL_P.values()) == 1, "레벨 분포 합이 1이 아니다"
assert sum(WEIGHTS.values()) == 100, "옵션 가중치 합이 100이 아니다"


# ── 뽑기 ───────────────────────────────────────────────────────────────────
def roll_cost(n_locked: int) -> int:
    """잠긴 칸이 `n_locked`개일 때 뽑기 1회 비용(커스텀 모듈 개수).

    효과 변경·수치 변경 둘 다 같은 식이다.
    """
    return 1 + n_locked


def draw_dist(excluded: frozenset[str]) -> dict[str, Fraction]:
    """이미 나온 옵션을 뺀 뒤 각 옵션의 조건부 확률.

    한 부위에 같은 옵션이 두 줄 붙지 않으므로, 앞 슬롯에서 뽑힌 옵션과 잠긴
    옵션이 뒤 슬롯의 풀에서 빠진다.
    """
    pool = {o: w for o, w in WEIGHTS.items() if o not in excluded}
    total = sum(pool.values())
    return {o: Fraction(w, total) for o, w in pool.items()}
