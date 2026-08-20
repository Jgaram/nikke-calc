"""가치 기반 최적 정지 DP — "지금 잠글까, 다시 굴릴까, 그만둘까".

`reach.py`는 *정해둔 목표*에 닿는 최소 비용을 낸다. 실제 유저는 목표에 닿기 전에
만족하기도 하고 목표보다 좋은 결과를 받기도 하므로, 여기서는 목표를 없애고
**교환비 λ** 하나로 바꾼다.

    λ = 커스텀 모듈 1개를 딜 몇 %와 바꿀 것인가

목적함수는 `기대(최종 부위 가치 − λ × 소모 모듈)`이고, 최적 정지 문제로 푼다.

    V(C) = max( 그만두기: U(C),
                굴리기:   max_A [ −λ(1+|A|) + E V(A ∪ 새로 나온 것) ] )

**이 한 식이 두 질문을 모두 답한다.** "목표 도달 비용"은 U를 목표 달성 여부의
계단함수로 둔 특수 케이스이고, "이 장비를 더 굴렸을 때의 기대 딜 상승"은 V(C) − U(C)다.

## 두 게임

효과변경은 안 잠근 칸의 **옵션까지** 날리므로 다음 상태가 잠금 집합에만 달려 있다.
수치변경은 옵션을 남기므로 안 잠근 칸의 옵션도 상태에 남는다. 그래서

1. `PolishGame` — 옵션 구성이 정해진 부위에서 수치변경만 쓰는 게임. 구성마다 따로 푼다
2. `EffectGame` — 효과변경 게임. **그만두기의 가치가 곧 `PolishGame`의 가치**다
   ("옵션은 이걸로 확정하고 이제 수치만 다듬는다")

옵션을 확정한 뒤 다시 효과변경으로 돌아가지 않는다는 정책 제약이 들어간다.
그 제약이 얼마나 손해인지는 `EffectGame.polish_regret()`이 잰다.

## 자기 참조를 대수로 닫는다

잠금 집합 A는 언제나 다음 상태에 그대로 남으므로 "같은 A로 또 굴린다"가 항상
선택지에 있다 — R(A)가 자기 자신을 참조한다. 그대로 값 반복을 돌리면 이 되먹임
때문에 수렴이 기어간다(실측 10분+). 대신 A를 뺀 나머지 선택지의 가치를 `rest`라 두고

    R = −비용 + Σ p·max(R, rest)

를 `_fixed_point()`가 정확히 푼다 — `reach.py`의 threshold 식과 같은 꼴이다.
바깥 Gauss-Seidel은 몇 바퀴 만에 멎는다.

## 레벨 버킷

레벨 15단계를 그대로 두면 뽑기 결과가 한 상태당 최대 173만 가지가 되어 못 돈다.
값이 비슷한 레벨끼리 묶고 대표값을 **버킷 안에서의 조건부 평균 가치**로 잡는다 —
기대 가치가 보존되므로 어긋나는 것은 경계 근처의 잠금 판정뿐이다. 딜에 아무 기여도
없는 옵션(방어 등)은 레벨이 값을 안 바꾸므로 버킷을 하나로 접는다(정확한 축소다).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .mechanics import LEVEL_P, LEVELS, OPTIONS, P_SHOW, SLOTS, WEIGHTS, roll_cost
from .value import line_pct

# 슬롯 3칸을 고정 길이 튜플로 표현한다. 자리마다 (옵션, 버킷) 또는 None(빈 칸).
Line = tuple[str, int]
Content = tuple[Line | None, Line | None, Line | None]
EMPTY: Content = (None, None, None)

# 레벨 버킷 프리셋. 균등 분할은 나쁘다 — 잠금 판정의 경계가 중·상위 레벨에 있어서,
# 흔한 1~5를 잘게 쪼개는 것은 상태만 늘리고 6~15를 뭉개는 것은 값을 깎는다.
# 실측(리틀 머메이드 / 철갑 코어 40px / λ=0.05, 기준 = 15버킷 4.83806):
#   균등5 −1.56% 19.5초 · 기본6 −0.38% 33초 · 상위7 −0.38% 59초 · 정확15 409초
BUCKETS: dict[str, tuple[tuple[int, ...], ...]] = {
    "거침": ((1, 2, 3, 4, 5), (6, 7, 8, 9, 10), (11, 12, 13, 14, 15)),
    "기본": ((1, 2, 3, 4, 5), (6, 7, 8), (9, 10), (11, 12), (13, 14), (15,)),
    "정확": tuple((lv,) for lv in LEVELS),
}
DEFAULT_BUCKETS: tuple[tuple[int, ...], ...] = BUCKETS["기본"]
NEG_INF = float("-inf")


def n_lines(c: Content) -> int:
    return sum(x is not None for x in c)


def options_of(c: Content) -> frozenset[str]:
    return frozenset(x[0] for x in c if x is not None)


def sublocks(c: Content) -> list[Content]:
    """`c`에서 잠글 수 있는 부분집합 전부. 3칸을 다 잠그는 것은 뺀다 — 굴려도 아무것도
    안 바뀌면서 모듈만 먹으므로 언제나 그만두기보다 나쁘다."""
    idx = [i for i, x in enumerate(c) if x is not None]
    full = len(idx) == len(SLOTS)
    out: list[Content] = []
    for mask in range(1 << len(idx)):
        if full and mask == (1 << len(idx)) - 1:
            continue
        cur: list[Line | None] = [None, None, None]
        for k, i in enumerate(idx):
            if mask >> k & 1:
                cur[i] = c[i]
        out.append((cur[0], cur[1], cur[2]))
    return out


def _fixed_point(cost: float, rows: list[tuple[float, float]]) -> float:
    """`R = −cost + Σ p·max(R, rest)`의 해. `rows`는 (확률, rest)이고 확률 합은 1.

    rest 내림차순으로 훑으며 "R보다 나은 결과"의 집합 S를 키운다. S가 정해지면

        R = (Σ_{i∈S} p_i·rest_i − cost) / Σ_{i∈S} p_i

    이고 `rest_k > R ≥ rest_{k+1}`에서 멎는다. 분모가 "이번 판에 R을 벗어날 확률"이라,
    계속 굴리게 되는 무한 반복이 이 식 하나에 들어가 있다.
    """
    rows = sorted(rows, key=lambda x: -x[1])
    acc_p = 0.0
    acc_pr = 0.0
    r = NEG_INF
    n = len(rows)
    for k in range(n):
        p, rest = rows[k]
        if rest == NEG_INF:
            break
        acc_p += p
        acc_pr += p * rest
        if acc_p <= 0.0:
            continue
        r = (acc_pr - cost) / acc_p
        if k + 1 == n or r >= rows[k + 1][1]:
            return r
    return r


# ── 가치표 ─────────────────────────────────────────────────────────────────
CRIT = ("크확", "크댐")


@dataclass
class Values:
    """옵션 구성 → 그 부위가 스쿼드 총딜을 몇 % 올리나. `value.Marginals`에서 만든다.

    **줄 값을 그냥 더하지 않는다.** 실측으로 잡은 구조는 이렇다
    (리틀 머메이드 / 철갑 코어 40px, 2026-08-20).

    - **같은 옵션을 쌓는 것은 정확히 선형.** 우코·공·크확 4줄까지 줄별 증가분이
      소수 셋째 자리까지 같았다 (3.598 × 4 / 2.260 × 4 / 0.604 × 4)
    - **레벨도 정확히 인게임 수치표 비례.** 레벨 1·5·10·15 실측이 표 비례 예측과 일치했다
    - **서로 다른 옵션은 곱해진다.** 그 캐릭터 자신의 딜 배율이 곱 구조라서다.
      우코 2줄을 얹으면 공의 한계가치가 2.2605 → 2.7205로 올랐고, 곱 모형 예측과
      소수 넷째 자리까지 맞았다 (공×크확도 2.3378 예측 / 2.3381 실측)
    - **크확·크댐만 곱으로 안 갈라진다.** 기대 크리 배율이 `1 + 크확 × (0.5 + 크댐)`
      꼴이라 쌍선형이다 — 크댐 2줄이 크확 한계가치를 0.604 → 0.978로 올렸다.
      곱 모형은 0.624를 예측해 크게 빗나간다. 그래서 이 둘만 한 채널로 묶고
      교차항 `crit_cross`를 따로 잰다

    그래서 값은 **캐릭터 자기 딜의 배율**로 조립하고 마지막에 덱 비중을 곱한다.

        worth = share × (Π_채널 (1 + G_채널) − 1) × 100

    가치가 어느 레벨에서도 0인 옵션(방어처럼 딜에 안 닿는 것)은 버킷을 하나로 접는다.
    레벨이 값을 안 바꾸므로 상태를 나눌 이유가 없다 — 근사가 아니라 정확한 축소다.
    """

    per_line: dict[str, float]          # 옵션 → 기준 레벨 줄 하나의 가치 (스쿼드 총딜 %)
    share: float = 1.0                  # 이 캐릭터가 스쿼드 총딜에서 차지하는 비중
    crit_cross: float = 0.0             # 크확·크댐 각 1줄일 때 더 붙는 스쿼드 총딜 %
    ref_level: int = 10
    buckets: tuple[tuple[int, ...], ...] = DEFAULT_BUCKETS
    grid: dict[str, tuple[tuple[int, float, float], ...]] = field(default_factory=dict, repr=False)
    _scale: dict[Line, float] = field(default_factory=dict, repr=False)
    _rate: dict[str, float] = field(default_factory=dict, repr=False)
    _cross: float = field(default=0.0, repr=False)
    _worth: dict[Content, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if sorted(sum(self.buckets, ())) != list(LEVELS):
            raise ValueError("버킷이 레벨 1~15를 정확히 한 번씩 덮지 않는다")
        if not 0.0 < self.share <= 1.0:
            raise ValueError(f"덱 비중은 0~1이어야 한다: {self.share}")
        self.per_line = {o: float(self.per_line.get(o, 0.0)) for o in OPTIONS}
        # 스쿼드 총딜 % → 그 캐릭터 자기 딜의 배율 증가분
        self._rate = {o: v / (self.share * 100.0) for o, v in self.per_line.items()}
        self._cross = self.crit_cross / (self.share * 100.0)

        for o in OPTIONS:
            dead = self.per_line[o] == 0.0 and not (o in CRIT and self.crit_cross)
            if dead:
                self.grid[o] = ((0, 1.0, 0.0),)
                self._scale[(o, 0)] = 0.0
                continue
            rows = []
            for i, b in enumerate(self.buckets):
                p = float(sum(LEVEL_P[lv] for lv in b))
                # 버킷 대표값은 **레벨 배수**의 조건부 평균이다. 값이 아니라 배수를 평균
                # 내는 이유는 조립이 곱셈이라 값의 평균이 보존 대상이 아니어서다.
                s = sum(float(LEVEL_P[lv]) * self.scale(o, lv) for lv in b) / p
                rows.append((i, p, s))
                self._scale[(o, i)] = s
            self.grid[o] = tuple(rows)

    def scale(self, option: str, level: int) -> float:
        """기준 레벨 대비 배수. 인게임 수치표 비례 — 실측으로 확인했다."""
        return line_pct(option, level) / line_pct(option, self.ref_level)

    def at(self, option: str, level: int) -> float:
        """그 옵션 **한 줄만** 있을 때의 가치. 순위표에 쓰는 값이다."""
        return self.per_line[option] * self.scale(option, level)

    def bucket_scale(self, option: str, b: int) -> float:
        return self._scale[(option, b)]

    def worth(self, c: Content) -> float:
        """이 구성이 스쿼드 총딜을 몇 % 올리나 = 그만뒀을 때 손에 남는 것."""
        hit = self._worth.get(c)
        if hit is not None:
            return hit
        n: dict[str, float] = {}
        for x in c:
            if x is not None:
                n[x[0]] = n.get(x[0], 0.0) + self._scale[x]
        out = self._worth[c] = self.assemble(n)
        return out

    def worth_levels(self, lines) -> float:
        """진짜 레벨(1~15)로 적힌 줄 목록의 가치. 버킷을 안 거치는 정확한 경로다."""
        n: dict[str, float] = {}
        for o, lv in lines:
            n[o] = n.get(o, 0.0) + self.scale(o, lv)
        return self.assemble(n)

    def assemble(self, n: dict[str, float]) -> float:
        """옵션별 줄 수(기준 레벨 환산) → 스쿼드 총딜 %. 채널끼리 곱한다."""
        factor = 1.0
        for o, cnt in n.items():
            if o in CRIT:
                continue
            factor *= 1.0 + self._rate[o] * cnt
        cr, cd = n.get("크확", 0.0), n.get("크댐", 0.0)
        if cr or cd:
            factor *= 1.0 + self._rate["크확"] * cr + self._rate["크댐"] * cd + self._cross * cr * cd
        return self.share * (factor - 1.0) * 100.0

    def lines(self) -> list[Line]:
        return [(o, b) for o in OPTIONS for b, _, _ in self.grid[o]]

    @classmethod
    def from_marginals(cls, marg, char: str, **kw) -> "Values":
        return cls(per_line=dict(marg.per_line[char]), ref_level=marg.ref_level,
                   share=marg.share.get(char, 1.0),
                   crit_cross=marg.crit_cross.get(char, 0.0), **kw)


# ── 공통 솔버 ──────────────────────────────────────────────────────────────
@dataclass
class _StoppingGame:
    """잠금 집합 A마다 R(A)를 푸는 최적 정지 게임. 두 게임이 공유하는 뼈대다.

    하위 클래스는 셋만 준다 — 잠금 상태 목록, 잠금별 뽑기 결과, 그만두기의 가치.
    """

    values: Values
    lam: float

    # 하위 클래스가 채운다
    def _lock_states(self) -> list[Content]:
        raise NotImplementedError

    def _outcomes(self, lock: Content) -> list[tuple[float, Content]]:
        raise NotImplementedError

    def stop_value(self, c: Content) -> float:
        raise NotImplementedError

    # ── 풀이 ───────────────────────────────────────────────────────────
    def solve(self, tol: float = 1e-11, max_iter: int = 200) -> None:
        states = sorted(self._lock_states(), key=lambda c: -n_lines(c))
        self._id = {c: i for i, c in enumerate(states)}
        self._R = [NEG_INF] * len(states)
        self._cost = [self.lam * roll_cost(n_lines(c)) for c in states]

        # 뽑기 결과를 미리 굽는다. 결과마다 (확률, 그만두기 가치, 이 잠금을 뺀 나머지
        # 잠금들의 id). 매 바퀴 다시 만들면 sublocks 호출만으로 시간이 다 간다.
        self._pre: list[list[tuple[float, float, tuple[int, ...]]]] = []
        for c in states:
            rows = []
            for p, nc in self._outcomes(c):
                ids = tuple(self._id[L] for L in sublocks(nc)
                            if L != c and L in self._id)
                rows.append((p, self.stop_value(nc), ids))
            self._pre.append(rows)

        R = self._R
        for _ in range(max_iter):
            delta = 0.0
            for i, rows in enumerate(self._pre):
                if not rows:
                    continue
                rest = [(p, max(sv, max((R[j] for j in ids), default=NEG_INF)))
                        for p, sv, ids in rows]
                new = _fixed_point(self._cost[i], rest)
                if new - R[i] > delta:
                    delta = new - R[i]
                R[i] = new
            if delta < tol:
                self._sweeps = _ + 1
                return
        raise RuntimeError(f"{type(self).__name__}이 수렴하지 않았다 — λ와 가치표를 확인해야 한다")

    # ── 조회 ───────────────────────────────────────────────────────────
    def R(self, lock: Content) -> float:
        i = self._id.get(lock)
        return NEG_INF if i is None else self._R[i]

    def V(self, c: Content) -> float:
        """이 상태의 가치 — 그만두기와 모든 잠금 선택지 중 최선."""
        best = self.stop_value(c)
        for lock in sublocks(c):
            r = self.R(lock)
            if r > best:
                best = r
        return best

    def best_lock(self, c: Content) -> tuple[float, Content | None]:
        """이 게임에서 가장 좋은 잠금 집합과 그 가치.

        **그만두기와 비교하지 않는다.** `stop_value()`에는 다른 게임으로 갈아타는
        선택지가 섞여 있어(`bail`) 여기서 비교하면 행동이 뭉개진다 — 세 후보
        (그만두기 · 효과변경 · 수치변경)를 나란히 놓는 것은 `Overload`가 한다.
        """
        best, arg = NEG_INF, None
        for lock in sublocks(c):
            r = self.R(lock)
            if r > best:
                best, arg = r, lock
        return best, arg


# ── 수치변경 게임 ──────────────────────────────────────────────────────────
@dataclass
class PolishGame(_StoppingGame):
    """옵션 구성이 정해진 부위에서 수치변경만 쓰는 게임.

    빈 칸은 계속 빈 칸이다 — 수치변경은 옵션을 만들어내지 않는다.
    """

    pattern: tuple[str | None, ...] = (None, None, None)
    bail: object | None = None   # 효과변경으로 갈아타는 선택지. `Overload`가 꽂는다

    def __post_init__(self) -> None:
        self.solve()

    def contents(self) -> list[Content]:
        out: list[Content] = []

        def rec(i: int, acc: list[Line | None]):
            if i == len(SLOTS):
                out.append((acc[0], acc[1], acc[2]))
                return
            opt = self.pattern[i]
            if opt is None:
                rec(i + 1, acc + [None])
            else:
                for b, _, _ in self.values.grid[opt]:
                    rec(i + 1, acc + [(opt, b)])

        rec(0, [])
        return out

    def _lock_states(self) -> list[Content]:
        seen = {lock for c in self.contents() for lock in sublocks(c)}
        # 다시 뽑을 칸이 하나도 없으면 굴릴 이유가 없다 (레벨이 있는 칸을 전부 잠근 경우)
        return [c for c in seen if len(self._outcomes(c)) > 1]

    def _outcomes(self, lock: Content) -> list[tuple[float, Content]]:
        outs: list[tuple[float, Content]] = [(1.0, lock)]
        for i, opt in enumerate(self.pattern):
            if opt is None or lock[i] is not None:
                continue
            outs = [(p * pb, (c[:i] + ((opt, b),) + c[i + 1:]))    # type: ignore[operator]
                    for p, c in outs for b, pb, _ in self.values.grid[opt]]
        return outs

    def stop_value(self, c: Content) -> float:
        """수치변경을 그만뒀을 때. `bail`이 있으면 효과변경으로 갈아타는 것도 후보다."""
        w = self.values.worth(c)
        if self.bail is None:
            return w
        b = self.bail(c)                                           # type: ignore[operator]
        return w if w > b else b


# ── 효과변경 게임 ──────────────────────────────────────────────────────────
@dataclass
class EffectGame(_StoppingGame):
    """효과변경으로 옵션 구성을 만드는 게임. 그만두면 수치변경 게임으로 넘어간다."""

    polish: bool = True
    bail: object | None = None   # 직전 바퀴의 효과변경 가치. `Overload`가 꽂는다
    _games: dict[tuple, PolishGame] = field(default_factory=dict, repr=False)
    _stop: dict[Content, float] = field(default_factory=dict, repr=False)
    _fresh_cache: dict[tuple, list] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.solve()

    # ── 그만두기 = 수치변경 단계로 ─────────────────────────────────────
    def polish_game(self, pattern: tuple) -> PolishGame:
        g = self._games.get(pattern)
        if g is None:
            g = self._games[pattern] = PolishGame(self.values, self.lam, pattern,
                                                  bail=self.bail)
        return g

    def stop_value(self, c: Content) -> float:
        if not self.polish:
            return self.values.worth(c)
        hit = self._stop.get(c)
        if hit is None:
            pattern = (c[0][0] if c[0] else None, c[1][0] if c[1] else None,
                       c[2][0] if c[2] else None)
            hit = self._stop[c] = self.polish_game(pattern).V(c)
        return hit

    # ── 뽑기 결과 ──────────────────────────────────────────────────────
    def _outcomes(self, lock: Content) -> list[tuple[float, Content]]:
        """`lock`을 남기고 나머지 칸을 효과변경으로 다시 뽑은 결과 전량.

        같은 부위에 같은 옵션이 두 줄 붙지 않으므로 잠긴 옵션과 앞 칸에서 뽑힌 옵션이
        풀에서 빠진다. 결과는 (빈 칸 위치, 제외 옵션)에만 달려 있어 그 키로 캐시한다.
        """
        free = tuple(i for i, x in enumerate(lock) if x is None)
        excl = options_of(lock)
        key = (free, excl)
        cached = self._fresh_cache.get(key)
        if cached is None:
            acc: list[tuple[float, tuple, frozenset[str]]] = [(1.0, EMPTY, excl)]
            for i in free:
                slot_p = float(P_SHOW[SLOTS[i]])
                nxt = []
                for p, c, ex in acc:
                    if slot_p < 1.0:
                        nxt.append((p * (1 - slot_p), c, ex))
                    pool = {o: w for o, w in WEIGHTS.items() if o not in ex}
                    tot = sum(pool.values())
                    for o, w in pool.items():
                        po = p * slot_p * w / tot
                        nx = ex | {o}
                        for b, pb, _ in self.values.grid[o]:
                            nxt.append((po * pb, c[:i] + ((o, b),) + c[i + 1:], nx))
                acc = nxt
            cached = self._fresh_cache[key] = [(p, c) for p, c, _ in acc]
        return [(p, (lock[0] or c[0], lock[1] or c[1], lock[2] or c[2]))
                for p, c in cached]

    def _lock_states(self) -> list[Content]:
        """R을 매길 잠금 집합 전부 — 0칸·1칸·2칸. 3칸을 다 잠근 상태는 굴릴 이유가 없다."""
        lines = self.values.lines()
        out: list[Content] = [EMPTY]
        for i in range(len(SLOTS)):
            for ln in lines:
                cur: list[Line | None] = [None, None, None]
                cur[i] = ln
                out.append((cur[0], cur[1], cur[2]))
        for i in range(len(SLOTS)):
            for j in range(i + 1, len(SLOTS)):
                for a in lines:
                    for b in lines:
                        if a[0] == b[0]:
                            continue
                        cur = [None, None, None]
                        cur[i], cur[j] = a, b
                        out.append((cur[0], cur[1], cur[2]))
        return out

    # ── 지표 ───────────────────────────────────────────────────────────
    def start(self) -> float:
        """빈 부위에서 시작할 때의 기대 순이득 (딜% − λ·모듈)."""
        return self.V(EMPTY)

    def roll_gain(self, c: Content) -> float:
        """지금 그만두는 대신 더 굴려서 얻는 기대 순이득. 0이면 그만둘 때다."""
        return self.V(c) - self.stop_value(c)

    def bail_value(self, c: Content) -> float:
        """이 구성에서 효과변경으로 갈아탔을 때의 가치. 다음 바퀴의 `bail`이 된다."""
        best = NEG_INF
        for lock in sublocks(c):
            r = self.R(lock)
            if r > best:
                best = r
        return best


# ── 결합 해 ────────────────────────────────────────────────────────────────
@dataclass
class Overload:
    """효과변경·수치변경을 **둘 다 언제든 쓸 수 있는** 진짜 게임의 해.

    두 게임이 서로의 값을 참조하므로(옵션이 나쁘면 다듬다 말고 효과변경으로 돌아간다)
    한 번에 풀 수 없다. 효과변경 쪽 R을 고정하고 수치변경을 풀고, 그 결과로 효과변경을
    다시 푸는 것을 값이 안 움직일 때까지 반복한다. 선택지가 늘기만 하므로 가치는
    단조 증가하고 위로 막혀 있어 수렴한다.
    """

    values: Values
    lam: float
    rounds: int = 12
    tol: float = 1e-9
    game: EffectGame = field(init=False, repr=False)
    used_rounds: int = field(init=False, default=0)
    gap: float = field(init=False, default=float("inf"))
    _act: dict[Content, tuple[str, Content] | None] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        prev: EffectGame | None = None
        for k in range(self.rounds):
            g = EffectGame(self.values, self.lam,
                           bail=None if prev is None else prev.bail_value)
            if prev is not None:
                self.gap = max((n - o for n, o in zip(g._R, prev._R)), default=0.0)
            prev = g
            self.used_rounds = k + 1
            if self.gap <= self.tol:
                break
        else:
            raise RuntimeError("Overload가 수렴하지 않았다 — rounds를 늘리거나 λ를 확인해야 한다")
        self.game = prev

    def V(self, c: Content = EMPTY) -> float:
        return self.game.V(c)

    def stop_value(self, c: Content) -> float:
        return self.game.stop_value(c)

    def roll_gain(self, c: Content = EMPTY) -> float:
        return self.game.roll_gain(c)

    def action(self, c: Content) -> tuple[str, Content] | None:
        """최적 행동. `None`이면 그만두기.

        효과변경과 수치변경 중 어느 쪽인지까지 답한다 — 잠금 집합이 같아도 두 행동은
        다른 결과를 준다. 몬테카를로가 판마다 부르므로 캐시한다.
        """
        if c in self._act:
            return self._act[c]
        out = self._act[c] = self._action(c)
        return out

    def _action(self, c: Content) -> tuple[str, Content] | None:
        stop = self.values.worth(c)
        ev, el = self.game.best_lock(c)
        pv, pl = self.polish_game(c).best_lock(c)
        if stop >= ev and stop >= pv:
            return None
        return ("효과변경", el) if ev >= pv else ("수치변경", pl)     # type: ignore[return-value]

    def polish_game(self, c: Content) -> PolishGame:
        return self.game.polish_game(
            (c[0][0] if c[0] else None, c[1][0] if c[1] else None, c[2][0] if c[2] else None))

    def best(self, c: Content = EMPTY) -> dict[str, float]:
        """세 후보의 가치를 나란히. 화면에 그대로 낼 수 있는 형태다."""
        return {
            "그만두기": self.values.worth(c),
            "효과변경": self.game.best_lock(c)[0],
            "수치변경": self.polish_game(c).best_lock(c)[0],
        }
