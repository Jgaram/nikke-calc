"""좌표 모드 기하 — 탄이 화면 어디에 떨어지고 무엇에 닿는가 (보스 패턴 좌표 모드, 유저 결정 2026-09-18).

전투 규칙은 모른다. 모양·탄 분포·확률만 안다 — 규칙은 `boss_pattern` §좌표 모드, 쓰는 자리는 `timeline`.

좌표
  **화면 px**다 — CDN 탄착군 직경(`*_accuracy_circle_scale`)·`core_px`와 같은 px. x 오른쪽 · y 위,
  원점은 보스 스크립트의 기준점이다.

탄 분포
  조준점 A를 중심으로, 착탄점이 반지름 ρ 안에 들 누적 확률이 F(ρ) = min(1, (ρ/R)^k)이고 각도는 균일하다.
  R = 탄착군 직경 / 2, k = 2.55 — 코어 히트 모델 P(core) = min(1, (r/R)^k)(`timeline._core_hit_prob`)을
  2D로 편 것이다. 그래서 **조준점에 중심을 둔 원**의 확률은 종전 식과 정확히 같다.

확률 (기대값 모드)
  P(영역) = ∫₀¹ m(ρ(u)) / 2π du,  u = F(ρ).  m(ρ) = 조준점 중심 반지름 ρ 고리 중 영역 안에 드는 각도의 길이.
  m은 모양마다 호의 합집합으로 정확히 구한다(원은 arccos 하나, 직사각형은 반평면 넷의 교집합). 호의 구조가 바뀌는
  ρ(경계점)에서 끊어 구간마다 Gauss–Legendre로 적분한다 — 조준점 중심 원은 m이 계단이라 정확하다.
  조준점에서 R + grow보다 먼 모양은 닿을 수 없어 처음부터 뺀다.

모양 (`Shape`)
  원 {x, y, r} · 직사각형 {x, y, w, h, rot(도, 반시계)}. `grow`만큼 부푼 모양 — 모양까지의 거리가 grow 이하인
  점들(관통·폭발 원이 닿는 범위)도 같은 방식으로 푼다: 원은 반지름 + grow, 직사각형은 모서리가 둥근 직사각형
  (가로로 늘린 것 ∪ 세로로 늘린 것 ∪ 꼭짓점 원 넷).

판정 (`Landing`)
  front[i]   착탄점을 덮는 가장 앞 모양이 i일 확률 — 보통 탄이 맞히는 것
  core_open  착탄점이 어떤 표적에도 안 덮이고 코어 안일 확률 — 보통 탄의 코어 히트
  core_under 착탄점이 코어 안일 확률(앞 표적과 무관) — 관통·폭발 탄의 본체 히트는 착탄점 그대로 본체에 닿는다
  reach[i]   착탄점에서 grow 안에 모양 i가 있을 확률 — 관통·폭발 탄이 따로 맞히는 것 (grow > 0일 때만)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TAU = 2.0 * math.pi
MODEL_N = 2.55          # 코어 히트 모델의 지수 — 정본은 data/weapon_mechanics.json `accuracy._model_n`(timeline이 넘긴다)
_TOUCH = 1e-9           # 경계 판정 여유(px)


def _gauss_legendre(n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """구간 [-1, 1]의 Gauss–Legendre 절점·가중치 (Newton 반복)."""
    xs, ws = [], []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, x
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
            dp = n * (x * p1 - p0) / (x * x - 1.0)
            dx = p1 / dp
            x -= dx
            if abs(dx) < 1e-15:
                break
        xs.append(x)
        ws.append(2.0 / ((1.0 - x * x) * dp * dp))
    return tuple(xs), tuple(ws)


_GL_X, _GL_W = _gauss_legendre(10)


# ── 원 둘레 위의 각도 구간 — [0, 2π) 안의 서로 겹치지 않는 (시작, 끝) 목록, 시작 순 ──

def _arc(start: float, length: float) -> list[tuple[float, float]]:
    if length <= 0.0:
        return []
    if length >= TAU - 1e-12:
        return [(0.0, TAU)]
    a = start % TAU
    b = a + length
    if b <= TAU:
        return [(a, b)]
    return [(0.0, b - TAU), (a, TAU)]


def _union(a: list, b: list) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, e in sorted(a + b):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def _intersect(a: list, b: list) -> list[tuple[float, float]]:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if e > s:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _subtract(a: list, b: list) -> list[tuple[float, float]]:
    out = []
    for s, e in a:
        cur = s
        for c, d in b:
            if d <= cur or c >= e:
                continue
            if c > cur:
                out.append((cur, c))
            cur = max(cur, d)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def _measure(a: list) -> float:
    return sum(e - s for s, e in a)


def _shift(a: list, by: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, e in a:
        out = _union(out, _arc(s + by, e - s))
    return out


def _circle_arcs(cx: float, cy: float, r: float, rho: float) -> list[tuple[float, float]]:
    """원점 중심 반지름 rho 원 둘레 중, 중심 (cx, cy)·반지름 r 원 안에 드는 각도."""
    d = math.hypot(cx, cy)
    if rho <= 0.0:
        return [(0.0, TAU)] if d <= r else []
    if d + rho <= r:
        return [(0.0, TAU)]
    if rho >= d + r or rho <= d - r:
        return []
    c = (rho * rho + d * d - r * r) / (2.0 * rho * d)
    half = math.acos(max(-1.0, min(1.0, c)))
    return _arc(math.atan2(cy, cx) - half, 2.0 * half)


def _halfplane(beta: float, c: float, rho: float) -> list[tuple[float, float]]:
    """ρ·cos(θ − β) ≤ c 인 θ."""
    if rho <= 0.0:
        return [(0.0, TAU)] if c >= 0.0 else []
    s = c / rho
    if s >= 1.0:
        return [(0.0, TAU)]
    if s <= -1.0:
        return []
    a = math.acos(s)
    return _arc(beta + a, TAU - 2.0 * a)


def _rect_arcs(lx: float, ly: float, hw: float, hh: float, rho: float) -> list[tuple[float, float]]:
    """조준점이 직사각형(중심 원점, 반폭 hw·반높이 hh) 기준 (lx, ly)에 있을 때, 조준점 중심 rho 원 둘레 중
    직사각형 안에 드는 각도 (직사각형의 로컬 각도)."""
    out = _halfplane(0.0, hw - lx, rho)
    for beta, c in ((math.pi, hw + lx), (math.pi / 2, hh - ly), (-math.pi / 2, hh + ly)):
        if not out:
            return []
        out = _intersect(out, _halfplane(beta, c, rho))
    return out


@dataclass(frozen=True)
class Shape:
    """표적·코어의 화면 모양. 원이면 r > 0, 직사각형이면 w·h > 0 (rot = 도, 반시계)."""
    x: float
    y: float
    r: float = 0.0
    w: float = 0.0
    h: float = 0.0
    rot: float = 0.0

    @property
    def is_circle(self) -> bool:
        return self.r > 0.0

    def _local(self, px: float, py: float) -> tuple[float, float]:
        th = math.radians(self.rot)
        dx, dy = px - self.x, py - self.y
        return dx * math.cos(th) + dy * math.sin(th), -dx * math.sin(th) + dy * math.cos(th)

    def dist(self, px: float, py: float) -> float:
        """점에서 모양까지의 거리. 안이면 0."""
        if self.is_circle:
            return max(0.0, math.hypot(px - self.x, py - self.y) - self.r)
        lx, ly = self._local(px, py)
        return math.hypot(max(abs(lx) - self.w / 2, 0.0), max(abs(ly) - self.h / 2, 0.0))

    def contains(self, px: float, py: float, grow: float = 0.0) -> bool:
        return self.dist(px, py) <= grow + _TOUCH

    def arcs(self, ax: float, ay: float, rho: float, grow: float = 0.0) -> list[tuple[float, float]]:
        """조준점 (ax, ay) 중심 반지름 rho 원 둘레 중 이 모양(grow만큼 부푼)에 드는 화면 각도."""
        if self.is_circle:
            return _circle_arcs(self.x - ax, self.y - ay, self.r + grow, rho)
        lx, ly = self._local(ax, ay)
        hw, hh = self.w / 2, self.h / 2
        local = _rect_arcs(lx, ly, hw + grow, hh, rho)
        if grow > 0.0:
            local = _union(local, _rect_arcs(lx, ly, hw, hh + grow, rho))
            for cx, cy in ((hw, hh), (-hw, hh), (hw, -hh), (-hw, -hh)):
                local = _union(local, _circle_arcs(cx - lx, cy - ly, grow, rho))
        return _shift(local, math.radians(self.rot)) if self.rot else local

    def breaks(self, ax: float, ay: float, grow: float = 0.0) -> list[float]:
        """호의 구조가 바뀌는 반지름들 — 적분 구간을 여기서 끊는다."""
        if self.is_circle:
            d, r = math.hypot(self.x - ax, self.y - ay), self.r + grow
            return [abs(d - r), d + r]
        lx, ly = self._local(ax, ay)
        hw, hh = self.w / 2, self.h / 2
        out = []
        for ew, eh in ((hw + grow, hh), (hw, hh + grow)):
            out += [abs(ew - lx), abs(ew + lx), abs(eh - ly), abs(eh + ly)]
            out += [math.hypot(sx * ew - lx, sy * eh - ly) for sx in (1, -1) for sy in (1, -1)]
        if grow > 0.0:
            for sx in (1, -1):
                for sy in (1, -1):
                    d = math.hypot(sx * hw - lx, sy * hh - ly)
                    out += [abs(d - grow), d + grow]
        return out


@dataclass(frozen=True)
class Landing:
    """한 발(펠릿 하나)의 착탄 확률. 목록은 `landing()`에 준 표적 순서(앞 → 뒤)다."""
    front: tuple[float, ...]
    core_open: float
    core_under: float
    reach: tuple[float, ...]


def landing(ax: float, ay: float, radius: float, targets: tuple[Shape, ...] | list[Shape],
            core: Shape | None = None, grow: float = 0.0, k: float = MODEL_N) -> Landing:
    """조준점 (ax, ay), 탄 분포 반지름 `radius`(= 탄착군 직경/2)로 쏜 한 발의 착탄 확률 — 정확 적분.

    `targets`는 **앞 → 뒤** 순서다(앞 모양이 뒤를 가린다). `grow` > 0이면 관통·폭발 원의 반지름 — `reach`를 채운다.
    """
    R = max(radius, 1e-9)
    n = len(targets)
    near = [i for i, s in enumerate(targets) if s.dist(ax, ay) <= R + grow]
    use_core = core is not None and core.dist(ax, ay) <= R
    if not near and not use_core:
        return Landing(front=(0.0,) * n, core_open=0.0, core_under=0.0, reach=(0.0,) * n)
    if not near and core.is_circle and core.x == ax and core.y == ay:
        # 조준점 중심 코어뿐 — 종전 코어 히트 식 그대로(적분의 끝자리 오차 없이). 좌표 없는 좌표 모드가 좌표 off와
        # 원 단위까지 같은 근거다
        p = min(1.0, (core.r / R) ** k)
        return Landing(front=(0.0,) * n, core_open=p, core_under=p, reach=(0.0,) * n)
    cuts = {0.0, 1.0}
    for i in near:
        for b in targets[i].breaks(ax, ay):
            if 0.0 < b < R:
                cuts.add((b / R) ** k)
        if grow > 0.0:
            for b in targets[i].breaks(ax, ay, grow):
                if 0.0 < b < R:
                    cuts.add((b / R) ** k)
    if use_core:
        for b in core.breaks(ax, ay):
            if 0.0 < b < R:
                cuts.add((b / R) ** k)
    us = sorted(cuts)
    front = [0.0] * n
    reach = [0.0] * n
    c_open = c_under = 0.0
    for u0, u1 in zip(us, us[1:]):
        if u1 - u0 <= 1e-15:
            continue
        mid, half = (u0 + u1) / 2, (u1 - u0) / 2
        for gx, gw in zip(_GL_X, _GL_W):
            w = gw * half / TAU
            rho = R * (mid + half * gx) ** (1.0 / k)
            covered: list[tuple[float, float]] = []
            for i in near:
                arcs = targets[i].arcs(ax, ay, rho)
                if arcs:
                    front[i] += w * _measure(_subtract(arcs, covered))
                    covered = _union(covered, arcs)
                if grow > 0.0:
                    reach[i] += w * _measure(targets[i].arcs(ax, ay, rho, grow))
            if use_core:
                carcs = core.arcs(ax, ay, rho)
                if carcs:
                    c_under += w * _measure(carcs)
                    c_open += w * _measure(_subtract(carcs, covered))
    clip = lambda v: min(1.0, max(0.0, v))
    return Landing(front=tuple(clip(v) for v in front), core_open=clip(c_open),
                   core_under=clip(c_under), reach=tuple(clip(v) for v in reach))


def needs_angle(ax: float, ay: float, targets, core: Shape | None) -> bool:
    """착탄점을 뽑을 때 각도가 필요한가. 조준점에 중심을 둔 원 코어 하나뿐이면 반지름만으로 판정된다 —
    그때는 난수를 하나만 먹어 좌표 off의 코어 판정(`random() < P_core`)과 같은 난수열을 쓴다."""
    if targets:
        return True
    return core is not None and not (core.is_circle and core.x == ax and core.y == ay)


def sample(ax: float, ay: float, radius: float, rng, *, angle: bool = True,
           k: float = MODEL_N) -> tuple[float, float]:
    """착탄점 하나를 뽑는다 (난수 모드). 반지름은 역누적으로, 각도는 균일하게. `angle=False`면 난수 하나만 쓴다."""
    rho = max(radius, 1e-9) * rng.random() ** (1.0 / k)
    if not angle:
        return ax + rho, ay
    th = TAU * rng.random()
    return ax + rho * math.cos(th), ay + rho * math.sin(th)


# ── 자체검산 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import random
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    def mc(ax, ay, R, targets, core, grow, n=200_000, seed=1):
        rng = random.Random(seed)
        front = [0] * len(targets)
        reach = [0] * len(targets)
        c_open = c_under = 0
        for _ in range(n):
            px, py = sample(ax, ay, R, rng)
            hit = next((i for i, s in enumerate(targets) if s.contains(px, py)), None)
            if hit is not None:
                front[hit] += 1
            if core is not None and core.contains(px, py):
                c_under += 1
                if hit is None:
                    c_open += 1
            for i, s in enumerate(targets):
                if grow > 0 and s.contains(px, py, grow):
                    reach[i] += 1
        return [v / n for v in front], c_open / n, c_under / n, [v / n for v in reach]

    # ── 검산 1: 조준점 중심 원 = 종전 코어 식 (r/R)^k, 정확히
    for r, R in ((20, 37.5), (5, 5), (26, 125), (60, 55)):
        got = landing(0, 0, R, [], Shape(0, 0, r=r)).core_open
        want = min(1.0, (r / R) ** MODEL_N)
        assert abs(got - want) < 1e-12, (r, R, got, want)
    print("검산 1 — 조준점 중심 코어: (r/R)^2.55와 1e-12 안에서 같다 (4경우)")

    # ── 검산 2: 떨어진 원·회전 직사각형·겹침(앞이 가린다)·부푼 모양 — 몬테카를로와 대조
    cases = [
        ("떨어진 원", (10, 5), 60, [Shape(40, 10, r=25)], Shape(0, 0, r=26), 0.0),
        ("직사각형 둘 겹침", (0, 0), 125, [Shape(30, 0, w=60, h=40), Shape(50, 10, w=80, h=30, rot=30)],
         Shape(-10, 0, r=30), 0.0),
        ("관통 원 25px", (0, 0), 37.5, [Shape(60, 0, r=15), Shape(0, 40, w=20, h=10, rot=45)], None, 25.0),
        ("폭발 100px", (5, -5), 5, [Shape(80, 30, w=40, h=40), Shape(-150, 0, r=20)], Shape(0, 0, r=40), 100.0),
    ]
    for label, (ax, ay), R, tg, core, grow in cases:
        ex = landing(ax, ay, R, tg, core, grow)
        f, co, cu, rc = mc(ax, ay, R, tg, core, grow)
        tol = 4e-3
        for a, b in zip(ex.front, f):
            assert abs(a - b) < tol, (label, "front", ex.front, f)
        assert abs(ex.core_open - co) < tol and abs(ex.core_under - cu) < tol, (label, ex, co, cu)
        if grow:
            for a, b in zip(ex.reach, rc):
                assert abs(a - b) < tol, (label, "reach", ex.reach, rc)
        assert sum(ex.front) + ex.core_open <= 1.0 + 1e-9
        print(f"검산 2 — {label}: front {[round(v, 4) for v in ex.front]} · 코어 {ex.core_open:.4f}/"
              f"{ex.core_under:.4f}" + (f" · reach {[round(v, 4) for v in ex.reach]}" if grow else "")
              + " (몬테카를로 20만 발과 0.004 안)")

    # ── 검산 3: 닿을 수 없는 모양은 0 · 각도 필요 여부
    far = landing(0, 0, 10, [Shape(500, 0, r=10)], None, 50)
    assert far.front == (0.0,) and far.reach == (0.0,)
    assert not needs_angle(0, 0, [], Shape(0, 0, r=20)) and needs_angle(0, 0, [], Shape(5, 0, r=20))
    assert needs_angle(0, 0, [Shape(0, 0, r=5)], None)
    rng_a, rng_b = random.Random(7), random.Random(7)
    x, _ = sample(0, 0, 50, rng_a, angle=False)
    assert rng_b.random() ** (1 / MODEL_N) * 50 == x
    print("검산 3 — 먼 모양 0 · 중심 원 코어뿐이면 난수 하나(좌표 off와 같은 난수열)")

    print("\n모든 검산 통과.")
