"""정책대로 실제로 굴려보는 몬테카를로 — DP 검산이자 유저에게 보여줄 분해.

DP가 내는 것은 순이득 하나(`가치 − λ·모듈`)뿐이라 "그래서 모듈 몇 개 드는데?"에
답하지 못한다. 여기서 같은 정책을 실제로 돌려 **기대 모듈·기대 가치·꼬리 분위수**로
쪼갠다. 그리고 그 순이득이 DP 값과 맞는지가 곧 DP 검산이다.

레벨은 버킷이 아니라 **진짜 1~15**로 뽑고 점수도 진짜 레벨로 매긴다. 버킷 대표값이
버킷 안 조건부 평균이라 기대값은 보존되므로, 정책이 버킷만 보고 판단해도 평균은
DP와 같아야 한다 — 안 맞으면 버킷이 아니라 구현이 틀린 것이다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .mechanics import LEVELS, LEVEL_P, P_SHOW, SLOTS, WEIGHTS, roll_cost
from .policy import Content, Overload, Values

# 진짜 레벨을 들고 있는 부위 상태. 자리마다 (옵션, 레벨 1~15) 또는 None.
RealContent = tuple[tuple[str, int] | None, ...]
EMPTY_REAL: RealContent = (None, None, None)

_LEVEL_LIST = list(LEVELS)
_LEVEL_W = [float(LEVEL_P[lv]) for lv in _LEVEL_LIST]


def bucket_of(values: Values, level: int) -> int:
    for i, b in enumerate(values.buckets):
        if level in b:
            return i
    raise ValueError(f"레벨 {level}이 어느 버킷에도 없다")


def to_content(values: Values, real: RealContent) -> Content:
    """진짜 레벨 → 정책이 보는 버킷 상태. 가치가 0인 옵션은 버킷이 하나뿐이다."""
    out = []
    for x in real:
        if x is None:
            out.append(None)
        else:
            o, lv = x
            out.append((o, 0 if len(values.grid[o]) == 1 else bucket_of(values, lv)))
    return (out[0], out[1], out[2])                                # type: ignore[return-value]


def real_worth(values: Values, real: RealContent) -> float:
    """진짜 레벨로 매긴 가치. 버킷을 안 거치므로 DP와의 차이가 곧 버킷 오차다."""
    return values.worth_levels(x for x in real if x is not None)


def _draw_option(excluded: set[str], rng: random.Random) -> str:
    pool = [(o, w) for o, w in WEIGHTS.items() if o not in excluded]
    r = rng.uniform(0, sum(w for _, w in pool))
    cum = 0.0
    for o, w in pool:
        cum += w
        if r <= cum:
            return o
    return pool[-1][0]


def _draw_level(rng: random.Random) -> int:
    return rng.choices(_LEVEL_LIST, weights=_LEVEL_W, k=1)[0]


def _effect_roll(real: RealContent, lock: Content, rng: random.Random) -> RealContent:
    """안 잠근 칸을 효과변경. 옵션과 레벨이 둘 다 새로 정해진다."""
    excluded = {real[i][0] for i in range(len(SLOTS))                # type: ignore[index]
                if lock[i] is not None and real[i] is not None}
    out: list[tuple[str, int] | None] = list(real)
    for i, slot in enumerate(SLOTS):
        if lock[i] is not None:
            continue
        if rng.random() >= float(P_SHOW[slot]):
            out[i] = None
            continue
        o = _draw_option(excluded, rng)
        excluded.add(o)
        out[i] = (o, _draw_level(rng))
    return (out[0], out[1], out[2])


def _polish_roll(real: RealContent, lock: Content, rng: random.Random) -> RealContent:
    """안 잠근 칸을 수치변경. 옵션은 그대로고 레벨만 새로 정해진다."""
    out: list[tuple[str, int] | None] = list(real)
    for i in range(len(SLOTS)):
        if lock[i] is not None or real[i] is None:
            continue
        out[i] = (real[i][0], _draw_level(rng))                     # type: ignore[index]
    return (out[0], out[1], out[2])


def play(game: Overload, rng: random.Random, start: RealContent = EMPTY_REAL,
         cap: int = 100_000) -> tuple[float, int]:
    """최적 정책대로 한 판. (최종 가치, 소모 모듈)."""
    real = start
    spent = 0
    for _ in range(cap):
        act = game.action(to_content(game.values, real))
        if act is None:
            return real_worth(game.values, real), spent
        kind, lock = act
        spent += roll_cost(sum(x is not None for x in lock))
        real = (_effect_roll if kind == "효과변경" else _polish_roll)(real, lock, rng)
    raise RuntimeError(f"{cap}판을 넘겨도 안 멈췄다 — 정책이나 λ가 이상하다")


@dataclass
class Rollout:
    """정책 실행 결과 요약. `순이득`이 DP 값과 맞아야 한다."""

    n: int
    lam: float
    values_sorted: list[float]
    modules_sorted: list[int]
    mean_value: float
    mean_modules: float

    @property
    def net(self) -> float:
        return self.mean_value - self.lam * self.mean_modules

    def quantile(self, q: float, which: str = "modules") -> float:
        seq = self.modules_sorted if which == "modules" else self.values_sorted
        return seq[min(int(q * self.n), self.n - 1)]

    def report(self) -> str:
        qs = (0.5, 0.75, 0.9, 0.95)
        mods = "  ".join(f"{int(q*100)}%={self.quantile(q):.0f}" for q in qs)
        return (f"{self.n:,}판  기대 모듈 {self.mean_modules:.2f}  "
                f"기대 가치 {self.mean_value:.3f}%  순이득 {self.net:.4f}\n"
                f"  모듈 분포: {mods}")


def rollout(game: Overload, n: int = 100_000, seed: int = 42,
            start: RealContent = EMPTY_REAL) -> Rollout:
    rng = random.Random(seed)
    rows = [play(game, rng, start) for _ in range(n)]
    vals = sorted(v for v, _ in rows)
    mods = sorted(m for _, m in rows)
    return Rollout(n, game.lam, vals, mods,
                   sum(vals) / n, sum(mods) / n)
