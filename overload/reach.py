"""목표 도달 DP — "이 옵션 조합을 맞추는 데 모듈이 몇 개 드나".

`..\\module\\module_3opt.py`에서 옮겨온 계산이다. 달라진 것은 목표를 전역 상수가
아니라 `Goal`로 받는 것뿐이고, 기대값은 그대로다(`test_overload.py`가 옛 값으로 잡는다).

**레벨을 보지 않는다.** 옵션 종류만 맞추면 완료로 친다. 수치까지 따지는 계산은
`policy.py`가 맡는다 — 이쪽은 `cost/`가 쓰는 "목표 도달 기대 소모량"의 정본이다.

    from overload.reach import Goal, expected_cost
    expected_cost(Goal(mandatory={"우코", "공"}, optional={"장탄"}))   # → 45.86

## 계산 방법

상태는 **무엇이 어느 칸에 잠겼는가**뿐이다. E(상태) = 완료까지의 기대 모듈이고,
"잠금 vs 재뽑기" 중 E를 최소화하는 쪽을 고른다.

그 선택이 E(현재)에 의존해 순환하므로 threshold 식으로 푼다. 잠금 후 기대값을
오름차순으로 놓고

    E*_k = (뽑기비용 + Σ_{i<k} p_i·e_i) / (p_완료 + Σ_{i<k} p_i)

를 훑다가 `E*_k ≤ e_k`가 되는 지점에서 멈춘다. 분모는 "이번 판에 현재 상태를
벗어날 확률"이라, 유효 옵션이 하나도 안 나와 강제로 다시 굴리는 경우가 이 식 하나에
들어가 있다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from fractions import Fraction

from .mechanics import P_SHOW, SLOTS, WEIGHTS, draw_dist, roll_cost

# 뽑기 결과: {슬롯: 옵션 or None}
Result = dict[str, str | None]
# 잠금 상태: 정렬된 ((슬롯, 옵션), ...)
Locked = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Goal:
    """원하는 옵션 조합. `mandatory`는 전부, `optional`은 `need` 개 이상."""

    mandatory: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()
    need: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "mandatory", frozenset(self.mandatory))
        object.__setattr__(self, "optional", frozenset(self.optional))
        unknown = (self.mandatory | self.optional) - set(WEIGHTS)
        if unknown:
            raise ValueError(f"모르는 옵션: {sorted(unknown)}")
        if self.mandatory & self.optional:
            raise ValueError(f"필수와 선택이 겹친다: {sorted(self.mandatory & self.optional)}")
        if len(self.optional) < self.need:
            raise ValueError(f"선택 후보({len(self.optional)})가 필요 개수({self.need})보다 적다")
        if len(self.mandatory) + self.need > len(SLOTS):
            raise ValueError(f"칸이 {len(SLOTS)}개뿐이라 목표를 담을 수 없다")

    @property
    def valid(self) -> frozenset[str]:
        """잠글 가치가 있는 옵션 전체."""
        return self.mandatory | self.optional

    def done(self, opts: frozenset[str]) -> bool:
        return self.mandatory <= opts and len(self.optional & opts) >= self.need

    def lockable(self, locked_opts: frozenset[str], result: Result) -> list[tuple[str, str]]:
        """이번 결과에서 **잠글 이유가 있는** (슬롯, 옵션) 목록.

        이미 필요한 만큼 확보한 선택 옵션은 더 잠가봐야 칸과 비용만 먹는다.
        """
        enough = len(self.optional & locked_opts) >= self.need
        return [(s, o) for s, o in result.items()
                if o is not None and o in self.valid
                and not (o in self.optional and enough)]


def _roll_outcomes(unlocked: frozenset[str], locked_opts: frozenset[str]
                   ) -> list[tuple[Fraction, Result]]:
    """잠기지 않은 칸을 A→B→C 순으로 굴린 (확률, 결과) 전량."""
    states: list[tuple[Fraction, Result, frozenset[str]]] = [(Fraction(1), {}, locked_opts)]
    for slot in (s for s in SLOTS if s in unlocked):
        p = P_SHOW[slot]
        nxt = []
        for prob, res, excl in states:
            if p < 1:
                nxt.append((prob * (1 - p), {**res, slot: None}, excl))
            for opt, op in draw_dist(excl).items():
                nxt.append((prob * p * op, {**res, slot: opt}, excl | {opt}))
        states = nxt
    return [(p, r) for p, r, _ in states]


@dataclass
class ReachDP:
    """한 목표에 대한 DP. 캐시를 물고 있으므로 목표마다 하나씩 만든다."""

    goal: Goal
    _cache: dict[Locked, Fraction] = field(default_factory=dict, repr=False)

    def E(self, locked: Locked = ()) -> Fraction:
        """`locked` 상태에서 목표 달성까지의 기대 모듈 소모량 (정확한 유리수)."""
        hit = self._cache.get(locked)
        if hit is not None:
            return hit

        locked_opts = frozenset(o for _, o in locked)
        if self.goal.done(locked_opts):
            self._cache[locked] = Fraction(0)
            return Fraction(0)

        cost = Fraction(roll_cost(len(locked)))
        locked_slots = frozenset(s for s, _ in locked)
        unlocked = frozenset(s for s in SLOTS if s not in locked_slots)

        p_complete = Fraction(0)
        branches: list[tuple[Fraction, Fraction]] = []   # (확률, 잠금 후 최선 E)

        for prob, result in _roll_outcomes(unlocked, locked_opts):
            showing = frozenset(v for v in result.values() if v)
            if self.goal.done(locked_opts | showing):
                p_complete += prob
                continue
            options = self.goal.lockable(locked_opts, result)
            if options:
                best = min(self.E(tuple(sorted(locked + (so,)))) for so in options)
                branches.append((prob, best))
            # 잠글 것이 없으면 강제 재뽑기 — threshold 식의 분모가 처리한다

        branches.sort(key=lambda x: x[1])

        sum_p = Fraction(0)
        sum_pe = Fraction(0)
        value = None
        for p_i, e_i in branches:
            denom = p_complete + sum_p
            if denom > 0 and (cost + sum_pe) / denom <= e_i:
                value = (cost + sum_pe) / denom
                break
            sum_p += p_i
            sum_pe += p_i * e_i
        if value is None:
            value = (cost + sum_pe) / (p_complete + sum_p)

        self._cache[locked] = value
        return value

    def action(self, locked: Locked, result: Result) -> str | tuple[str, str] | None:
        """이번 결과에 대한 최적 행동. `"완료"` / `(슬롯, 옵션)` 잠금 / `None` 재뽑기."""
        locked_opts = frozenset(o for _, o in locked)
        showing = frozenset(v for v in result.values() if v)
        if self.goal.done(locked_opts | showing):
            return "완료"
        options = self.goal.lockable(locked_opts, result)
        if not options:
            return None
        best = min(options, key=lambda so: self.E(tuple(sorted(locked + (so,)))))
        return best if self.E(tuple(sorted(locked + (best,)))) < self.E(locked) else None

    # ── 검증용 시뮬레이션 ──────────────────────────────────────────────
    def _play(self, rng: random.Random) -> int:
        locked: Locked = ()
        spent = 0
        while True:
            locked_opts = frozenset(o for _, o in locked)
            locked_slots = frozenset(s for s, _ in locked)
            spent += roll_cost(len(locked))

            result: Result = {}
            excl = set(locked_opts)
            for slot in SLOTS:
                if slot in locked_slots:
                    continue
                if rng.random() < float(P_SHOW[slot]):
                    opt = _sample(excl, rng)
                    result[slot] = opt
                    excl.add(opt)
                else:
                    result[slot] = None

            act = self.action(locked, result)
            if act == "완료":
                return spent
            if act is not None:
                locked = tuple(sorted(locked + (act,)))  # type: ignore[arg-type]

    def simulate(self, n: int = 200_000, seed: int = 42) -> list[int]:
        """최적 정책대로 `n`판. 정렬된 소모량 목록을 준다."""
        rng = random.Random(seed)
        return sorted(self._play(rng) for _ in range(n))


def _sample(excluded: set[str], rng: random.Random) -> str:
    pool = [(o, w) for o, w in WEIGHTS.items() if o not in excluded]
    r = rng.uniform(0, sum(w for _, w in pool))
    cum = 0.0
    for o, w in pool:
        cum += w
        if r <= cum:
            return o
    return pool[-1][0]


def expected_cost(goal: Goal) -> float:
    """목표 달성까지의 기대 모듈 소모량."""
    return float(ReachDP(goal).E())


def quantiles(goal: Goal, qs=(0.5, 0.75, 0.9, 0.95), n: int = 200_000,
              seed: int = 42) -> dict[float, int]:
    """소모량 분포의 분위수. 기대값만으로는 꼬리가 안 보여서 같이 낸다."""
    samples = ReachDP(goal).simulate(n, seed)
    return {q: samples[min(int(q * n), n - 1)] for q in qs}


def geometric_quantile(p_success: float, q: float, cost_per_roll: int) -> int:
    """성공 확률 `p`인 마지막 한 칸의 분위수 소모량 (기하분포 정확값)."""
    return math.ceil(math.log(1 - q) / math.log(1 - p_success)) * cost_per_roll
