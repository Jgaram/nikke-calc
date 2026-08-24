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
from .budget import choose, curves_for, log_grid, plan, totals
from .rollout import rollout
from .value import (NON_MULTIPLICATIVE, PIECES, SLOTS_PER_PIECE, check_pieces,
                    default_build, default_pieces, drop_lines, equip_skills, flatten)

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


# ── 4-b. 부위 분할과 배경 빼기 ─────────────────────────────────────────────
def test_pieces() -> None:
    """굴릴 부위의 줄은 배경에서 빠져야 한다.

    한계가치를 그 부위가 붙은 채로 쟀다면 부위의 줄이 배경에도 있고 조립에도 들어가
    두 번 세어진다. `Values.removed`가 그것을 뺀다 (`policy.Values` §굴릴 부위).
    """
    print("\n[부위 분할]")
    p = default_pieces()
    check("기본 분할이 게임 규칙에 맞는다", check_pieces(p) is p)
    check(f"부위 {PIECES}개 · 칸 {SLOTS_PER_PIECE}개 이하",
          len(p) == PIECES and all(len(x) <= SLOTS_PER_PIECE for x in p))
    check("평탄화하면 기본 구성", flatten(p) == tuple(sorted(default_build())))
    check("부위를 빼면 나머지만 남는다",
          drop_lines(default_build(), p[0]) == tuple(sorted(flatten(p[1:]))))
    try:
        check_pieces((p[0], p[1], p[2], (("우코", 10), ("우코", 5))))
        check("한 부위에 같은 옵션 두 줄은 거절", False)
    except ValueError:
        check("한 부위에 같은 옵션 두 줄은 거절", True)

    print("\n[배경 빼기 — 부위 줄을 두 번 세지 않는가]")
    share, cross = 0.35, 0.09
    rm = (("우코", 10), ("공", 10))
    kw = dict(per_line=dict(SYNTHETIC), share=share, crit_cross=cross, buckets=MEDIUM)
    v_raw = Values(**kw)                 # 보정 없음 = 고치기 전 방식
    v = Values(**kw, removed=rm)

    near("빈 부위는 여전히 0", v.worth((None, None, None)), 0.0, 1e-12)
    near("빈 줄 목록도 0", v.worth_levels([]), 0.0, 1e-12)

    # worth(c) = share × ( G(c − removed) − G(−removed) ) × 100
    def G(n):
        return 1.0 + v.assemble(n) / (share * 100.0)
    c = [("우코", 15), ("공", 10), ("크확", 10)]
    n_c: dict = {}
    for o, lv in c:
        n_c[o] = n_c.get(o, 0.0) + v.scale(o, lv)
    gone = {o: v.scale(o, lv) for o, lv in rm}
    want = share * (G({o: n_c.get(o, 0.0) - gone.get(o, 0.0) for o in set(n_c) | set(gone)})
                    - G({o: -x for o, x in gone.items()})) * 100.0
    near("조립 항등식", v.worth_levels(c), want, 1e-9)

    check("보정하면 값이 작아진다 (이중 계산이 빠진다)",
          v.worth_levels(c) < v_raw.worth_levels(c),
          f"{v.worth_levels(c):.4f} < {v_raw.worth_levels(c):.4f} "
          f"({(1 - v.worth_levels(c) / v_raw.worth_levels(c)) * 100:.1f}% 부풀어 있었다)")
    # 뺀 줄을 그대로 다시 붙이면 = 지금 부위가 실제로 벌고 있는 값
    near("뺀 줄을 그대로 다시 붙이면 지금 부위의 가치",
         v.worth_levels(rm), share * (1.0 - G({o: -x for o, x in gone.items()})) * 100.0, 1e-9)
    near("딜에 안 닿는 옵션만 나오면 0 (빈 부위와 같다)",
         v.worth_levels([("방어", 15)]), 0.0, 1e-12)
    check("그 결과는 지금 구성보다 손해",
          v.worth_levels([("방어", 15)]) < v.worth_levels(rm),
          f"0 < {v.worth_levels(rm):.4f}")

    # 명중·장탄은 포화·계단이라 배경이 바뀌면 한계가치가 비례해서 안 움직인다.
    # 실측에서 장탄은 부호까지 뒤집혔다 — 되빼기로 처리하면 조용히 틀린다.
    for opt in sorted(NON_MULTIPLICATIVE):
        try:
            Values(**kw, removed=((opt, 10),))
            check(f"{opt}을 되빼는 것은 거절", False)
        except ValueError:
            check(f"{opt}을 되빼는 것은 거절", True)
    ok = Values(**kw, removed=(("장탄", 10),), allow_approx=True)
    check("검산용 escape hatch(allow_approx)는 열려 있다",
          ok.worth_levels([("장탄", 10)]) > 0.0, f"{ok.worth_levels([('장탄', 10)]):.4f}")


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


# ── 6. 예산 역산 ───────────────────────────────────────────────────────────
def test_budget() -> None:
    """쓸 모듈 개수 → λ. 격자에서 고르는 방식이 약속한 성질을 지키는가.

    보간하지 않는 이유는 정책이 계단이라서다 (`budget.py` §보간하지 않는다).
    그래서 여기서 잡을 것은 보간 오차가 아니라 **선택 규칙**이다 — 예산을 넘지 않는
    것 중 가장 공격적인 점을 고르는가. 계산기는 안 부른다.
    """
    print("\n[예산 역산 — 격자에서 고르는 규칙]")
    v = Values(per_line=dict(SYNTHETIC), share=0.35, crit_cross=0.09, buckets=COARSE)
    lams = log_grid(0.02, 0.35, 1.25)
    cur = (("우코", 10), ("공", 10))
    curves = curves_for([v, v], [cur, ()], lams, n=3_000, seed=5)

    tot = totals(curves)
    check("총 기대 모듈이 λ에 대해 단조 감소",
          all(a >= b for a, b in zip(tot, tot[1:])),
          f"{tot[0]:.0f} → {tot[-1]:.0f} ({len(tot)}점)")
    twins = curves_for([v, v, v], [cur, (), cur], lams, n=500, seed=5)
    check("붙은 줄이 같은 부위는 격자를 한 번만 굽는다",
          twins[0].rows is twins[2].rows and twins[0].rows is not twins[1].rows,
          "부위 0과 2가 같은 격자를 쓴다")

    for frac in (0.2, 0.5, 0.9):
        budget = tot[-1] + (tot[0] - tot[-1]) * frac
        i, status = choose(curves, budget)
        spent = tot[i]
        check(f"예산 {budget:.0f}을 넘지 않는다", spent <= budget + 1e-9,
              f"λ {curves[0].rows[i].lam:.4f} → 기대 {spent:.1f}  [{status}]")
        if i > 0:
            check(f"예산 {budget:.0f}에서 한 칸 더 공격적인 점은 예산을 넘는다",
                  tot[i - 1] > budget, f"{tot[i - 1]:.1f} > {budget:.1f}")

    i, status = choose(curves, tot[-1] / 10)
    check("어떤 λ로도 못 맞추는 작은 예산은 모자란다고 알린다", status == "모자란다",
          f"{status}, λ={curves[0].rows[i].lam:.4f}")
    i, status = choose(curves, tot[0] * 10)
    check("예산이 남으면 가장 공격적인 점을 고르고 격자끝이라 알린다",
          i == 0 and status == "격자끝", f"{status}, λ={curves[0].rows[i].lam:.4f}")

    # 이분 탐색(`plan`)은 격자를 다 굽는 대신 5~6점만 굽는다. 웹앱이 그 길로 도므로
    # **전체 격자와 같은 점·같은 상태**에 닿는지가 곧 그 화면의 정확성이다.
    probes = []
    for frac in (0.05, 0.3, 0.6, 1.0, 3.0):
        budget = tot[-1] + (tot[0] - tot[-1]) * frac
        want_i, want_st = choose(curves, budget)
        got, i, status = plan([v, v], [cur, ()], budget, lams, n=3_000, seed=5,
                              log=lambda *a: probes.append(a))
        check(f"이분 탐색이 예산 {budget:.0f}에서 전체 격자와 같은 점을 고른다",
              i == want_i and status == want_st,
              f"λ={got[0].rows[i].lam:.4f} [{status}] / 전체 "
              f"λ={curves[0].rows[want_i].lam:.4f} [{want_st}]")
    check("격자를 다 굽지 않는다", 0 < len(probes) < len(lams) * 5,
          f"예산 5개에 격자점 {len(probes)}회 (전체 격자면 {len(lams) * 5}회)")


def main() -> int:
    print("=" * 66)
    print("  오버로드 뽑기 회귀")
    print("=" * 66)
    test_mechanics()
    test_reach()
    test_fixed_point()
    test_values()
    test_pieces()
    test_policy()
    test_budget()
    print("\n" + "=" * 66)
    if _fails:
        print(f"  ❌ 실패 {len(_fails)}건: {_fails}")
        return 1
    print("  ✅ 전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
