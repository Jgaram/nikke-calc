"""예산(쓸 모듈 개수) → λ → 부위별 계획. `python -m overload.budget "이름1,..."`.

    python -m overload.budget "리틀 머메이드,크라운,헬름" --char "헬름" --modules 300

`report.py`는 λ를 훑어 곡선을 보여주지만, 유저가 아는 것은 λ가 아니라 **가진 모듈
개수**다. 여기서 그 곡선을 뒤집는다.

## λ 하나로 부위 넷을 함께 푼다

각 부위는 자기 λ에서 독립적으로 "언제 그만둘까"를 푼다. 부위들을 묶는 것은 예산뿐이고,
같은 λ에서 멈추는 것이 곧 최적 배분이다 — 어느 부위가 마지막 모듈로 λ보다 더 벌면
그쪽으로 모듈을 옮기는 게 낫기 때문이다 (라그랑주 완화). 그래서

    총 기대 모듈(λ) = Σ_부위 기대 모듈(λ)

를 예산에 맞추는 λ 하나를 고르고, **순위도 전략도 그 λ에서** 읽는다.

## 보간하지 않는다 — 계단이라서

`(λ → 기대 모듈)`은 매끄러운 곡선이 아니다. 최적 정지 정책은 λ가 문턱을 넘을 때
**불연속으로** 바뀌고 그 사이에서는 거의 안 움직인다. 합성 가치표로 λ를 1.15배씩
올리며 잰 실측 (2026-08-23, 부위 0 = 우코10·공10이 이미 붙어 있는 경우):

    λ      0.0264  0.0304  0.0350 … 0.0704  0.0809
    부위 0   30.2    13.2    13.2  …  13.2     0.0

로그-로그로 보간해도 격자 사이에서 17~24% 빗나갔다. 그래서 **격자 위의 λ 중에서
고른다** — 기대 모듈이 예산을 넘지 않는 것 가운데 가장 공격적인(λ가 가장 작은) 점.
보고하는 기대 모듈은 보간값이 아니라 그 λ에서 실제로 잰 값이다.

계단이므로 **예산을 다 못 쓰는 구간이 있다.** 위 표에서 총 지출이 42.5에서 22.9로
건너뛰므로 예산 35개로는 22.9짜리 계획밖에 살 수 없다. 남는 몫은 다른 캐릭터나 다른
육성 축으로 보내야 하고, 계획표가 그 사실을 그대로 알린다.

λ가 맞추는 것은 **기대** 예산이지 상한이 아니다. 꼬리가 두꺼우므로 분위수를 함께 낸다.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from .policy import BUCKETS, DEFAULT_BUCKETS, Overload, Values
from .rollout import rollout, to_content
from .value import (NON_MULTIPLICATIVE, PIECES, DeckContext, Marginals, Piece,
                    marginals, piece_marginals)


def log_grid(lo: float = 0.002, hi: float = 0.512, ratio: float = 1.3) -> tuple[float, ...]:
    """로그 격자. 계단을 걸러내려면 촘촘해야 하고, 예산이 밖으로 안 나가려면 넓어야 한다."""
    out = [lo]
    while out[-1] * ratio <= hi:
        out.append(out[-1] * ratio)
    return tuple(out)


DEFAULT_LAMBDAS: tuple[float, ...] = log_grid()


def piece_values(ctx: DeckContext, char: str, marg: Marginals, buckets=DEFAULT_BUCKETS,
                 mode: str = "auto", log=None) -> list[Values]:
    """부위 넷의 가치표. 부위에 무엇이 붙었느냐로 만드는 방법이 갈린다.

    배율 옵션뿐이면 이미 잰 한계가치에서 되빼고(공짜), 명중·장탄이 끼면 그 부위를 뺀
    배경에서 다시 잰다 (`README.md` §굴릴 부위). 뺀 줄이 같으면 배경도 같으므로
    재측정 결과를 그 줄로 캐시한다 — 기본 구성처럼 같은 부위가 겹칠 때 절반이 준다.
    """
    cache: dict[tuple, Marginals] = {}
    return [values_for(ctx, char, k, marg, buckets, mode, cache, log)
            for k in range(PIECES)]


def values_for(ctx: DeckContext, char: str, k: int, marg: Marginals,
               buckets=DEFAULT_BUCKETS, mode: str = "auto", cache=None, log=None) -> Values:
    """부위 하나의 가치표. 갈림길은 `piece_values`의 설명과 같다."""
    piece = ctx.pieces_of(char)[k]
    if not ({o for o, _ in piece} & NON_MULTIPLICATIVE):
        return Values.from_marginals(marg, char, buckets=buckets, removed=piece)
    key = tuple(sorted(piece))
    pm = None if cache is None else cache.get(key)
    if pm is None:
        if log:
            lines = " ".join(f"{o}{lv}" for o, lv in piece)
            log(f"  부위 {k} ({lines}) — 되뺄 수 없는 옵션이 껴 있어 배경을 다시 잰다...")
        t = time.time()
        pm = piece_marginals(ctx, k, denom=marg.denom, only=char, mode=mode)
        if cache is not None:
            cache[key] = pm
        if log:
            log(f"    ...끝 ({pm.runs}회, {time.time() - t:.0f}초)")
    return Values.from_marginals(pm, char, buckets=buckets)


@dataclass
class Row:
    """한 부위를 한 λ에서 굴렸을 때. 전부 몬테카를로로 잰 값이다 — 보간이 없다."""

    lam: float
    modules: float          # 기대 소모 모듈
    value: float            # 기대 최종 가치 (스쿼드 총딜 %)
    base: float             # 안 굴리고 그만뒀을 때 손에 있는 값
    q90: float              # 모듈 90% 분위
    act: str                # 지금 구성에서의 최적 첫 행동

    @property
    def gain(self) -> float:
        """굴려서 **더** 버는 딜. 예산이 사는 것은 이것이다."""
        return self.value - self.base

    @property
    def rate(self) -> float:
        return self.gain / self.modules if self.modules else float("nan")


@dataclass
class PieceCurve:
    """부위 하나의 (λ → 기대 모듈·기대 딜) 격자."""

    piece: int
    current: Piece
    values: Values
    base: float
    rows: list[Row] = field(default_factory=list)


def curve(values: Values, current: Piece, lams=DEFAULT_LAMBDAS, n: int = 8_000,
          seed: int = 42, piece: int = 0) -> PieceCurve:
    """한 부위의 λ 격자를 굽는다. λ마다 정책 DP 한 번 + 몬테카를로 한 번.

    시작 상태는 **지금 붙어 있는 줄**이다. 빈 부위에서 시작하면 이미 손에 있는 값을
    다시 버는 것으로 세게 된다.
    """
    slots = list(current) + [None] * 3
    start = (slots[0], slots[1], slots[2])
    base = values.worth_levels(current)
    out = PieceCurve(piece, current, values, base)
    for lam in sorted(lams):
        g = Overload(values, lam=lam)
        r = rollout(g, n=n, seed=seed, start=start)
        act = g.action(to_content(values, start))
        out.rows.append(Row(lam, r.mean_modules, r.mean_value, base, r.quantile(0.9),
                            "그만두기" if act is None else act[0]))
    return out


def curves_for(vs: list[Values], pieces, lams=DEFAULT_LAMBDAS, n: int = 8_000,
               seed: int = 42) -> list[PieceCurve]:
    """부위 넷의 격자. 붙은 줄이 같으면 가치표도 격자도 같으므로 한 번만 굽는다."""
    done: dict[tuple, PieceCurve] = {}
    out = []
    for k, (v, p) in enumerate(zip(vs, pieces)):
        key = tuple(sorted(p))
        hit = done.get(key)
        if hit is None:
            hit = done[key] = curve(v, p, lams, n, seed, piece=k)
        out.append(PieceCurve(k, p, hit.values, hit.base, hit.rows))
    return out


def totals(curves: list[PieceCurve]) -> list[float]:
    """격자 점마다의 총 기대 모듈. λ 오름차순이므로 내림차순이어야 한다."""
    return [sum(c.rows[i].modules for c in curves) for i in range(len(curves[0].rows))]


def _unprobed(lam: float) -> Row:
    """아직 안 구운 격자점. 기대 모듈이 무한대라 `choose`가 저절로 건너뛴다."""
    return Row(lam, float("inf"), 0.0, 0.0, 0.0, "?")


def plan(vs: list[Values], pieces, budget: float, lams=DEFAULT_LAMBDAS, n: int = 8_000,
         seed: int = 42, log=None) -> tuple[list[PieceCurve], int, str]:
    """예산에 맞는 격자점을 **이분 탐색**으로 찾는다. 반환: `(curves, i, status)`.

    격자를 다 굽지 않는 이유는 λ 한 점이 정책 DP 한 번이라서다 — 거침 버킷이 네이티브
    7초고 Pyodide는 그 몇 배라, 22점을 다 굽는 것은 브라우저에서 무리다. 총 기대 모듈이
    λ에 대해 단조 감소이므로 "예산 이하"가 단조 술어이고, 5~6점만 구우면 다 구운 것과
    같은 점에 닿는다 (`test_overload.test_budget`이 전체 격자와 대조한다).

    **보간이 아니다.** 안 구운 점은 기대 모듈을 무한대로 둘 뿐이고, 고르는 것도 상태
    문구를 정하는 것도 `choose`가 그대로 한다 — 보고하는 값은 언제나 그 격자점에서
    실제로 잰 값이다 (§보간하지 않는다).

    `log(i, lam, total)`을 주면 격자점을 하나 구울 때마다 부른다 (진행률 표시용).
    """
    lams = tuple(sorted(lams))
    rows: list[dict[int, Row]] = [{} for _ in vs]

    def probe(i: int) -> float:
        if i in rows[0]:
            return sum(r[i].modules for r in rows)
        cs = curves_for(vs, pieces, (lams[i],), n, seed)
        for k, c in enumerate(cs):
            rows[k][i] = c.rows[0]
        total = sum(c.rows[0].modules for c in cs)
        if log:
            log(len(rows[0]), lams[i], total)
        return total

    lo, hi = 0, len(lams) - 1
    if probe(hi) <= budget:                 # 가장 아끼는 λ도 예산 안이면 더 공격적으로 간다
        while lo < hi:
            mid = (lo + hi) // 2
            if probe(mid) <= budget:
                hi = mid
            else:
                lo = mid + 1
        probe(lo)                           # 고른 점은 반드시 구워 둔다 (보고할 값이다)

    curves = [PieceCurve(k, pieces[k], vs[k], vs[k].worth_levels(pieces[k]),
                         [rows[k].get(i) or _unprobed(lams[i]) for i in range(len(lams))])
              for k in range(len(vs))]
    i, status = choose(curves, budget)
    return curves, i, status


def choose(curves: list[PieceCurve], budget: float) -> tuple[int, str]:
    """예산 → 격자 인덱스. 예산을 넘지 않는 것 중 가장 공격적인(λ가 작은) 점.

    두 번째 값은 그 선택이 예산과 어떤 관계인지다.

    - `"맞음"`     — 예산을 거의 다 쓴다
    - `"남는다"`   — 계단 때문에 다음 칸이 예산을 넘어 못 간다. 남는 몫은 다른 데 쓴다
    - `"격자끝"`   — 격자에서 가장 공격적인 λ로도 예산이 남는다. 더 퍼부을 데가 없다
    - `"모자란다"` — 가장 아끼는 λ조차 예산을 넘는다. 굴리지 않는 게 낫다
    """
    tot = totals(curves)
    for i, t in enumerate(tot):
        if t <= budget:
            slack = (budget - t) / budget if budget else 0.0
            if slack <= 0.10:
                return i, "맞음"
            return i, "격자끝" if i == 0 else "남는다"
    return len(tot) - 1, "모자란다"


# ── CLI ────────────────────────────────────────────────────────────────────
def print_plan(curves: list[PieceCurve], i: int, budget: float, status: str) -> None:
    rows = sorted(((c, c.rows[i]) for c in curves), key=lambda x: -x[1].gain)
    lam = curves[0].rows[i].lam
    tot_m = sum(r.modules for _, r in rows)
    tot_g = sum(r.gain for _, r in rows)
    tot_q = sum(r.q90 for _, r in rows)

    print(f"\n[계획 — 모듈 {budget:g}개를 쓴다면]")
    print(f"  교환비 λ = {lam:.4f}  (모듈 1개를 딜 {lam:.4f}%와 바꾼다)")
    if status == "남는다":
        print(f"  ⚠ 기대 지출은 {tot_m:.1f}개다. 정책이 계단이라 다음 칸은 예산을 넘어서,")
        print(f"    남는 {budget - tot_m:.0f}개는 이 캐릭터의 오버로드로는 쓸 데가 없다")
    elif status == "격자끝":
        print(f"  ⚠ 격자에서 가장 공격적인 λ인데도 기대 지출이 {tot_m:.1f}개에 그친다.")
        print(f"    남는 {budget - tot_m:.0f}개는 다른 캐릭터나 다른 육성 축으로 보내야 한다")
    elif status == "모자란다":
        print(f"  ⚠ 가장 아끼는 λ조차 기대 {tot_m:.1f}개를 쓴다 — 예산으로는 모자란다")
    print(f"  {'부위':>4}  {'지금 붙은 것':22}  {'첫 행동':>6}  {'기대 모듈':>9}  "
          f"{'기대 딜%':>8}  {'딜%/모듈':>9}  {'모듈 90%':>8}")
    print("  " + "-" * 84)
    for c, r in rows:
        cur = " ".join(f"{o}{lv}" for o, lv in c.current) or "(빈 부위)"
        rate = f"{r.rate:.4f}" if r.modules else "—"
        print(f"  {c.piece:>4}  {cur:22}  {r.act:>6}  {r.modules:>9.1f}  "
              f"{r.gain:>8.3f}  {rate:>9}  {r.q90:>8.0f}")
    print("  " + "-" * 84)
    rate = f"{tot_g / tot_m:.4f}" if tot_m else "—"
    print(f"  {'합계':>4}  {'':22}  {'':>6}  {tot_m:>9.1f}  {tot_g:>8.3f}  "
          f"{rate:>9}  {tot_q:>8.0f}")

    print("\n  기대 모듈은 **평균**이지 상한이 아니다. 부위별 90% 분위를 그냥 더한 마지막")
    print("  칸은 넷이 동시에 나쁠 때를 가정한 거친 상한이라 실제 합계의 90% 분위보다 크다.")
    print("  딜%/모듈이 큰 부위부터 하나씩 끝내고, 한 부위를 끝낼 때마다 나머지 부위의")
    print("  값이 달라지므로 다시 계산한다.")


def main() -> int:
    ap = argparse.ArgumentParser(description="오버로드 — 가진 모듈로 무엇을 할까")
    ap.add_argument("squad", help="캐릭터 이름 콤마 구분 (정식 명칭)")
    ap.add_argument("--char", required=True, help="계획을 낼 캐릭터")
    ap.add_argument("--modules", type=float, nargs="+", default=[100.0, 300.0, 1000.0],
                    help="쓸 모듈 개수. 여러 개 주면 각각 계획을 낸다")
    ap.add_argument("--code", choices=["풍압", "수냉", "작열", "전격", "철갑"])
    ap.add_argument("--core", type=float, default=0.0, help="코어 직경(px)")
    ap.add_argument("--enemy-def", type=int)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--profile", help="고정 스펙 대신 실제 계정 육성으로")
    ap.add_argument("--buckets", choices=list(BUCKETS), default="거침",
                    help="레벨 버킷. 격자를 λ마다 풀어야 해서 기본이 거침이다")
    ap.add_argument("--lam", type=float, nargs="+", default=list(DEFAULT_LAMBDAS),
                    help="λ 격자")
    ap.add_argument("--mode", choices=["auto", "fast", "exact"], default="auto")
    ap.add_argument("--n", type=int, default=8_000, help="격자 한 점당 몬테카를로 판수")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--show-grid", action="store_true", help="격자 전체를 찍는다")
    args = ap.parse_args()

    from context import spec as char_spec

    names = [s.strip() for s in args.squad.split(",") if s.strip()]
    if args.char not in names:
        print(f"{args.char}는 덱에 없다: {names}", file=sys.stderr)
        return 2
    enemy: dict = {"core_px": args.core}
    if args.code:
        enemy["code"] = args.code
    if args.enemy_def is not None:
        enemy["def"] = args.enemy_def
    config = {"duration": args.duration} if args.duration else None
    profile = char_spec.load_profile(args.profile) if args.profile else None
    ctx = DeckContext(names=names, enemy=enemy, config=config, profile=profile)

    print("=" * 84)
    print(f"  오버로드 예산 계획 — {args.char}  ({' / '.join(names)})")
    print(f"  코어 {args.core:g}px" + (f" · 적 {args.code}" if args.code else "")
          + (f" · 프로필 {args.profile}" if profile is not None else ""))
    print("=" * 84)

    t = time.time()
    print("\n한계가치 측정 중...", flush=True)
    marg = marginals(ctx, mode=args.mode)
    print(f"  ...끝 ({marg.runs}회, {time.time() - t:.0f}초)")
    vs = piece_values(ctx, args.char, marg, buckets=BUCKETS[args.buckets],
                      mode=args.mode, log=lambda s: print(s, flush=True))

    print(f"\nλ 격자 {len(args.lam)}점 × 부위 {PIECES}개를 굽는다 "
          f"(버킷 {args.buckets}, {args.n:,}판)...", flush=True)
    t = time.time()
    curves = curves_for(vs, ctx.pieces_of(args.char), tuple(sorted(args.lam)),
                        args.n, args.seed)
    print(f"  ...끝 ({time.time() - t:.0f}초)")

    if args.show_grid:
        print("\n[격자 — λ가 정하는 총 지출]")
        print(f"  {'λ':>8}  {'총 기대 모듈':>12}  {'총 기대 딜%':>11}")
        for i, tot in enumerate(totals(curves)):
            g = sum(c.rows[i].gain for c in curves)
            print(f"  {curves[0].rows[i].lam:>8.4f}  {tot:>12.1f}  {g:>11.3f}")

    for b in args.modules:
        i, status = choose(curves, b)
        print_plan(curves, i, b, status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
