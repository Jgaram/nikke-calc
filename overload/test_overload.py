"""오버로드 뽑기 회귀. `python -m overload.test_overload`.

앵커는 셋이다.

1. **옛 DP 값** — `..\\module\\module_guide.md`가 내놓은 기대 소모량. 이식으로 값이
   흔들리지 않았음을 잡는다 (`cost/tables.json`의 `오버로드_모듈`도 이 값이다)
2. **시뮬레이션** — DP가 낸 정책을 실제로 돌린 평균이 DP 값과 맞는가
3. **버킷 단조성** — 레벨 버킷을 촘촘히 할수록 가치가 오르기만 하는가.
   버킷은 정책이 볼 수 있는 정보를 줄이는 것이라 언제나 **보수적**이어야 한다

계산기는 부르지 않는다. 가치표는 합성값을 쓴다 — 여기서 잡을 것은 뽑기 수학이지
딜 계산이 아니다.
"""

from __future__ import annotations

import random
import sys

from .mechanics import LEVEL_P, WEIGHTS
from .policy import BUCKETS, EMPTY, Overload, Values, _fixed_point
from .reach import Goal, expected_cost, quantiles
from .rollout import rollout
from .value import default_build, equip_skills

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# `..\module\module_guide.md` §@ 개수별 시작 기대 모듈
REACH_ANCHORS = [
    ("우코+공", Goal({"우코"}, {"공"}), 17.62),
    ("우코+공+@1", Goal({"우코", "공"}, {"장탄"}), 45.86),
    ("우코+공+@2", Goal({"우코", "공"}, {"크확", "크댐"}), 37.37),
    ("우코+공+@3", Goal({"우코", "공"}, {"크확", "크댐", "장탄"}), 30.62),
    ("우코+공+@4", Goal({"우코", "공"}, {"장탄", "크확", "크댐", "명중"}), 27.36),
]

# 합성 가치표. 통설 순위(우코 > 공 > 크확·크댐·장탄)를 대충 따르되 특정 덱을 흉내내지
# 않는다 — 실측값은 덱마다 달라서 회귀 앵커로 쓸 수 없다.
SYNTHETIC = {"우코": 3.5, "공": 2.2, "크확": 0.6, "크댐": 0.55, "장탄": 0.4,
             "차속": 0.2, "차댐": 0.0, "명중": 0.0, "방어": 0.0}

COARSE = ((1, 2, 3, 4, 5), (6, 7, 8, 9, 10), (11, 12, 13, 14, 15))
# COARSE의 **세분**이어야 한다. 겹치게 자르면 정보집합이 포개지지 않아 값이 오르리란
# 보장이 없다 — 촘촘함이 아니라 포개짐이 단조성의 조건이다.
MEDIUM = ((1, 2, 3), (4, 5), (6, 7, 8), (9, 10), (11, 12, 13), (14, 15))

_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        _fails.append(name)


def near(name: str, got: float, want: float, tol: float) -> None:
    check(name, abs(got - want) <= tol, f"{got:.4f} (기준 {want:.4f}, 허용 ±{tol})")


# ── 1. 규칙 ────────────────────────────────────────────────────────────────
def test_mechanics() -> None:
    print("\n[규칙]")
    check("옵션 가중치 합 100", sum(WEIGHTS.values()) == 100)
    check("레벨 분포 합 1", sum(LEVEL_P.values()) == 1)
    check("레벨 1~5가 각 12%", all(LEVEL_P[lv] == LEVEL_P[1] for lv in range(1, 6))
          and abs(float(LEVEL_P[1]) - 0.12) < 1e-12)
    check("레벨 11~15가 각 1%", abs(float(LEVEL_P[15]) - 0.01) < 1e-12)

    es = equip_skills(default_build())
    check("기본 스펙 재구성 = 우코 4 · 공 2 · 장탄 2",
          len(es["element_bonus"]) == 4 and len(es["atk_pct"]) == 2
          and len(es["max_ammo_pct"]) == 2)


# ── 2. 목표 도달 DP ────────────────────────────────────────────────────────
def test_reach() -> None:
    print("\n[목표 도달 DP — 이식 전 값과 일치하는가]")
    for name, goal, want in REACH_ANCHORS:
        near(name, expected_cost(goal), want, 0.005)

    print("\n[목표 도달 DP — 시뮬레이션 대조]")
    for name, goal, _ in REACH_ANCHORS[:2]:
        from .reach import ReachDP
        got = ReachDP(goal).simulate(50_000, seed=7)
        near(f"{name} 시뮬 평균", sum(got) / len(got), expected_cost(goal), 0.5)

    q = quantiles(REACH_ANCHORS[1][1], n=50_000)
    check("꼬리가 기대값보다 두껍다", q[0.9] > expected_cost(REACH_ANCHORS[1][1]),
          f"90%={q[0.9]} vs 기대 {expected_cost(REACH_ANCHORS[1][1]):.1f}")


# ── 3. 자기 참조 대수 ──────────────────────────────────────────────────────
def test_fixed_point() -> None:
    print("\n[자기 참조 — 대수 해 vs 무식한 반복]")
    rng = random.Random(1)
    worst = 0.0
    for _ in range(300):
        n = rng.randint(1, 12)
        ps = [rng.random() for _ in range(n)]
        s = sum(ps)
        rows = [(p / s, rng.uniform(-2, 8)) for p in ps]
        cost = rng.uniform(0.01, 3)
        exact = _fixed_point(cost, rows)
        r = -1e9                       # 아래에서 올라오는 값 반복
        for _ in range(20000):
            nxt = -cost + sum(p * max(r, rest) for p, rest in rows)
            if abs(nxt - r) < 1e-13:
                r = nxt
                break
            r = nxt
        worst = max(worst, abs(exact - r))
    check("300개 난수 사례에서 일치", worst < 1e-6, f"최대 오차 {worst:.2e}")


# ── 4. 가치표 ──────────────────────────────────────────────────────────────
def test_values() -> None:
    print("\n[가치표]")
    share, cross = 0.35, 0.09
    v = Values(per_line=dict(SYNTHETIC), share=share, crit_cross=cross, buckets=MEDIUM)
    for o, base in SYNTHETIC.items():
        if base == 0.0:
            continue
        want = sum(float(LEVEL_P[lv]) * v.scale(o, lv) for lv in range(1, 16))
        got = sum(p * s for _, p, s in v.grid[o])
        near(f"{o} 버킷이 기대 레벨배수를 보존", got, want, 1e-12)
    check("가치 0이고 크리도 아닌 옵션은 버킷이 하나", len(v.grid["방어"]) == 1)
    near("기준 레벨에서 표값 그대로", v.at("우코", 10), SYNTHETIC["우코"], 1e-12)

    print("\n[가치 조립 — 채널끼리 곱하는가]")
    near("한 줄만 있으면 한계가치 그대로",
         v.worth((("우코", 3), None, None)),
         SYNTHETIC["우코"] * v.bucket_scale("우코", 3), 1e-9)
    r_u = SYNTHETIC["우코"] / (share * 100)
    r_g = SYNTHETIC["공"] / (share * 100)
    near("우코+공은 곱", v.worth_levels([("우코", 10), ("공", 10)]),
         share * ((1 + r_u) * (1 + r_g) - 1) * 100, 1e-9)
    near("우코 2줄은 채널 안에서 선형", v.worth_levels([("우코", 10), ("우코", 10)]),
         share * (1 + 2 * r_u - 1) * 100, 1e-9)
    both = v.worth_levels([("크확", 10), ("크댐", 10)])
    near("크확+크댐은 교차항만큼 더 붙는다", both,
         SYNTHETIC["크확"] + SYNTHETIC["크댐"] + cross, 1e-9)
    check("교차항이 있으면 단순 합보다 크다", both > SYNTHETIC["크확"] + SYNTHETIC["크댐"],
          f"{both:.4f} > {SYNTHETIC['크확'] + SYNTHETIC['크댐']:.4f}")
    near("빈 부위는 0", v.worth((None, None, None)), 0.0, 1e-12)


# ── 5. 정책 DP ─────────────────────────────────────────────────────────────
def test_policy() -> None:
    print("\n[정책 DP — 시뮬레이션 대조]")
    lam = 0.05
    g = Overload(Values(per_line=dict(SYNTHETIC), share=0.35, crit_cross=0.09,
                        buckets=COARSE), lam=lam)
    r = rollout(g, n=40_000, seed=11)
    near("빈 부위 순이득", r.net, g.V(EMPTY), 0.02)
    print(f"     {r.report()}")

    print("\n[정책 DP — 버킷을 세분하면 값이 오르는가]")
    coarse = g.V(EMPTY)
    fine = Overload(Values(per_line=dict(SYNTHETIC), share=0.35, crit_cross=0.09,
                           buckets=MEDIUM), lam=lam).V(EMPTY)
    check("세분하면 값이 오른다 (버킷은 보수적)", fine >= coarse - 1e-9,
          f"{len(COARSE)}버킷 {coarse:.4f} → {len(MEDIUM)}버킷 {fine:.4f}")
    check("프리셋이 서로 포개진다",
          all(any(set(f) <= set(c) for c in BUCKETS["거침"]) for f in BUCKETS["기본"]))

    print("\n[정책 DP — λ에 대한 단조성]")
    def synth(lam):
        return Overload(Values(per_line=dict(SYNTHETIC), share=0.35, crit_cross=0.09,
                               buckets=COARSE), lam=lam)
    lo, hi = synth(0.02), synth(0.20)
    check("λ가 크면 순이득이 작다", hi.V(EMPTY) < lo.V(EMPTY),
          f"λ=0.02 → {lo.V(EMPTY):.3f} / λ=0.20 → {hi.V(EMPTY):.3f}")
    r_lo = rollout(lo, n=20_000, seed=3)
    r_hi = rollout(hi, n=20_000, seed=3)
    check("λ가 크면 모듈을 덜 쓴다", r_hi.mean_modules < r_lo.mean_modules,
          f"{r_lo.mean_modules:.1f} → {r_hi.mean_modules:.1f}")

    print("\n[정책 DP — 가치가 없으면 굴리지 않는다]")
    dead = Overload(Values(per_line={o: 0.0 for o in WEIGHTS}, buckets=COARSE), lam=lam)
    near("전 옵션 0인 덱의 순이득", dead.V(EMPTY), 0.0, 1e-12)
    check("행동이 그만두기", dead.action(EMPTY) is None)


def main() -> int:
    print("=" * 66)
    print("  오버로드 뽑기 회귀")
    print("=" * 66)
    test_mechanics()
    test_reach()
    test_fixed_point()
    test_values()
    test_policy()
    print("\n" + "=" * 66)
    if _fails:
        print(f"  ❌ 실패 {len(_fails)}건: {_fails}")
        return 1
    print("  ✅ 전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
