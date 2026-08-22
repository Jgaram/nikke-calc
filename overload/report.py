"""오버로드 효율 보고 CLI. `python -m overload.report "이름1,이름2,..."`.

    python -m overload.report "리틀 머메이드,크라운,라피 : 레드 후드,미하라 : 본딩 체인,헬름" --code 철갑 --core 40
    python -m overload.report "..." --char "헬름" --lam 0.02 0.05 0.1
    python -m overload.report "..." --marginals-only          # 한계가치표만 (빠름)

두 가지를 낸다.

1. **한계가치표** — 옵션 한 줄이 이 덱의 총딜을 몇 % 올리나. 통설 순위(우코 > 공 > …)가
   *내 덱에서* 어떻게 되는지가 여기 나온다
2. **효율 곡선** — λ(모듈 1개를 딜 몇 %와 바꿀지)를 훑으며 기대 모듈·기대 딜·꼬리.
   최적점에서 λ는 곧 **그 정책이 실제로 얻는 교환비**이므로(포락선 정리), 표의
   `딜%/모듈` 열이 λ와 거의 같아야 정상이다

캐릭터 이름은 정식 명칭만 받는다 (`context/ALIASES.md`).
"""

from __future__ import annotations

import argparse
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from context import spec as char_spec

from .mechanics import OPTIONS
from .policy import BUCKETS, EMPTY, Overload, Values
from .rollout import rollout
from .value import DeckContext, default_build, marginals

DEFAULT_LAMBDAS = (0.01, 0.02, 0.05, 0.10, 0.20)


def print_marginals(marg, names: list[str]) -> None:
    print(f"\n[한계가치 — 레벨 {marg.ref_level} 한 줄이 스쿼드 총딜을 몇 % 올리나]")
    print(f"  기준 총딜 {marg.baseline['_총합']:,}  ({marg.runs}회 실행)")
    print("  " + f"{'':22}" + "".join(f"{o:>8}" for o in OPTIONS))
    for n in names:
        print("  " + f"{n:22}" + "".join(f"{marg.per_line[n][o]:>8.3f}" for o in OPTIONS))
    print()
    for n in names:
        top = [f"{o} {v:.2f}" for o, v in marg.ranking(n)[:4] if v > 0]
        print(f"  {n:22} {' > '.join(top) if top else '(딜에 닿는 옵션 없음)'}")


def print_curve(values: Values, lams, n: int, seed: int) -> None:
    print("\n[효율 곡선 — 부위 하나를 빈 상태에서 굴릴 때]")
    print(f"  {'λ(한계)':>8}  {'기대 모듈':>9}  {'기대 딜%':>9}  {'평균 딜%/모듈':>13}  "
          f"{'모듈 90%':>8}  {'순이득':>8}  {'DP 대조':>8}")
    print("  " + "-" * 76)
    rows = []
    for lam in sorted(lams):
        g = Overload(values, lam=lam)
        r = rollout(g, n=n, seed=seed)
        rate = r.mean_value / r.mean_modules if r.mean_modules else float("nan")
        rows.append((lam, r, g))
        print(f"  {lam:>8.3f}  {r.mean_modules:>9.2f}  {r.mean_value:>9.3f}  "
              f"{rate:>13.4f}  {r.quantile(0.9):>8.0f}  {r.net:>8.3f}  "
              f"{g.V(EMPTY):>8.3f}")

    print("\n  λ는 **한계** 교환비다 — 마지막 모듈 하나가 딜 λ%를 못 벌면 그만둔다는 뜻이고,")
    print("  그래서 λ가 커질수록 모듈도 딜도 함께 줄어든다. `평균 딜%/모듈`은 그보다 늘 크다")
    print("  (초반 모듈이 훨씬 싸게 벌어오기 때문이다). 가진 모듈로 무엇을 살 수 있는지는")
    print("  `기대 모듈` 열에서 거꾸로 읽는다.")
    if len(rows) > 1:
        print(f"\n  {'구간':>16}  {'Δ딜%/Δ모듈':>12}   (인접 λ 사이여야 정상)")
        for (l0, r0, _), (l1, r1, _) in zip(rows, rows[1:]):
            dm = r0.mean_modules - r1.mean_modules
            slope = (r0.mean_value - r1.mean_value) / dm if dm else float("nan")
            print(f"  {f'{l0:.3f}~{l1:.3f}':>16}  {slope:>12.4f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="오버로드 옵션 효율 보고")
    ap.add_argument("squad", help="캐릭터 이름 콤마 구분 (1~5명, 정식 명칭)")
    ap.add_argument("--char", help="효율 곡선을 낼 캐릭터. 기본은 한계가치 합이 가장 큰 사람")
    ap.add_argument("--code", choices=["풍압", "수냉", "작열", "전격", "철갑"],
                    help="적 속성 코드. 우코 줄의 가치를 좌우한다")
    ap.add_argument("--core", type=float, default=0.0, help="코어 직경(px). 0이면 코어 없음")
    ap.add_argument("--enemy-def", type=int, help="적 방어력")
    ap.add_argument("--duration", type=float, help="전투 시간(초). 기본 180")
    ap.add_argument("--profile", help="고정 스펙 대신 실제 계정 육성으로 (profiles/<이름>.json)")
    ap.add_argument("--lam", type=float, nargs="+", default=list(DEFAULT_LAMBDAS),
                    help="훑을 교환비 목록 (모듈 1개 = 딜 몇 %)")
    ap.add_argument("--buckets", choices=list(BUCKETS), default="기본",
                    help="레벨 버킷. 거침(빠름·−1.6%%) / 기본(−0.4%%) / 정확(근사 없음·7분)")
    ap.add_argument("--mode", choices=["auto", "fast", "exact"], default="auto",
                    help="한계가치 측정 방식. auto는 타이밍 옵션만 exact로 잰다")
    ap.add_argument("--n", type=int, default=40_000, help="검증 시뮬 판수")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--marginals-only", action="store_true", help="한계가치표만 내고 끝낸다")
    args = ap.parse_args()

    names = [s.strip() for s in args.squad.split(",") if s.strip()]
    enemy = {"core_px": args.core}
    if args.code:
        enemy["code"] = args.code
    if args.enemy_def is not None:
        enemy["def"] = args.enemy_def
    config = {"duration": args.duration} if args.duration else None
    profile = char_spec.load_profile(args.profile) if args.profile else None

    ctx = DeckContext(names=names, builds={n: default_build() for n in names},
                      enemy=enemy, config=config, profile=profile)

    print("=" * 72)
    print(f"  오버로드 효율 — {' / '.join(names)}")
    bits = [f"코어 {args.core:g}px"]
    if args.code:
        bits.append(f"적 {args.code}")
    if profile is not None:
        bits.append(f"프로필 {args.profile}")
    print(f"  {' · '.join(bits)}")
    print("=" * 72)

    t = time.time()
    marg = marginals(ctx, mode=args.mode)
    print_marginals(marg, names)
    print(f"\n  (측정 {time.time() - t:.0f}초)")
    if args.marginals_only:
        return 0

    who = args.char or max(names, key=lambda n: sum(max(v, 0) for v in marg.per_line[n].values()))
    if who not in marg.per_line:
        print(f"\n{who}는 이 덱에 없다: {names}", file=sys.stderr)
        return 2

    print(f"\n\n### {who} — 부위 하나의 효율 (레벨 버킷 {args.buckets})")
    values = Values.from_marginals(marg, who, buckets=BUCKETS[args.buckets])
    print_curve(values, args.lam, args.n, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
