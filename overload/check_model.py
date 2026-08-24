"""부위 가치 모델을 계산기로 검산한다. `python -m overload.check_model "이름1,..."`.

    python -m overload.check_model "리틀 머메이드,크라운,라피 : 레드 후드,미하라 : 본딩 체인,헬름" \
        --char "리틀 머메이드" --code 철갑 --core 40

`test_overload.py`는 뽑기 수학만 잡고 계산기를 부르지 않는다. 여기는 반대로 **계산기가
정답**이고, 가치 모델이 그 정답을 얼마나 맞히는지 잰다. 느리다 (5인 덱 1회 ≈ 5초).

## 무엇을 대조하나

부위 하나를 굴릴 때의 값은 "그 부위를 뺀 덱"을 기준으로 재야 한다. 세 가지를 같은
잣대로 놓는다 — 모두 분모는 **실제 덱 총딜**이다.

| 이름 | 한계가치를 잰 배경 | 조립 |
|---|---|---|
| 정공법 | 그 캐릭터의 해당 부위를 뺀 구성 | `removed` 없음 |
| 지름길 | 실제 구성 그대로 (부위 포함) | `removed`로 배경에서 뺀다 |
| 옛 방식 | 실제 구성 그대로 | 보정 없음 — 부위 줄을 두 번 센다 |

정공법은 부위마다 한계가치를 다시 재야 해서 비싸고(부위마다 19회), 지름길은 한 번 잰
값으로 부위 넷을 다 다룬다. 옛 방식은 고치기 전 코드가 하던 것이라 오차 크기를 남겨
두려고 함께 잰다.

**결론은 부위에 무엇이 붙었느냐로 갈렸다** (`README.md` §굴릴 부위). 배율 옵션만
붙은 부위에서는 지름길이 정공법과 0.001%p 안에서 같았지만, 장탄이 붙은 부위에서는
0.389%p·부호까지 틀렸다. 그래서 `Values`는 명중·장탄이 낀 `removed`를 거절하고,
여기서만 `allow_approx=True`로 뚫어 오차를 잰다.
"""

from __future__ import annotations

import argparse
import random
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from .mechanics import OPTIONS
from .policy import Values
from .value import DeckContext, Piece, drop_lines, marginals

LEVELS = (1, 3, 5, 8, 10, 12, 15)


def sample_pieces(n: int, seed: int = 0) -> list[Piece]:
    """무작위 부위 구성 n개. 칸 수 1~3, 같은 옵션은 한 부위에 두 줄 안 온다."""
    rng = random.Random(seed)
    out: list[Piece] = []
    seen: set[Piece] = set()
    while len(out) < n:
        k = rng.choice((1, 2, 3, 3))
        opts = rng.sample(OPTIONS, k)
        p: Piece = tuple(sorted((o, rng.choice(LEVELS)) for o in opts))
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("names", help="쉼표로 구분한 정식 명칭 5명")
    ap.add_argument("--char", help="검산할 캐릭터 (기본: 첫 번째)")
    ap.add_argument("--piece", type=int, default=0, help="검산할 부위 번호 (기본 0)")
    ap.add_argument("--code", help="상대 코드")
    ap.add_argument("--core", type=int, help="코어 크기(px)")
    ap.add_argument("-n", type=int, default=12, help="무작위 구성 수 (기본 12)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    names = [s.strip() for s in args.names.split(",") if s.strip()]
    who = args.char or names[0]
    if who not in names:
        raise SystemExit(f"{who}는 덱에 없다: {names}")
    enemy = None
    if args.code or args.core:
        enemy = {k: v for k, v in (("code", args.code), ("core_px", args.core)) if v}

    ctx = DeckContext(names=names, enemy=enemy)
    piece = ctx.pieces_of(who)[args.piece]
    t0 = time.time()

    print("=" * 78)
    print("  부위 가치 모델 검산 — 계산기가 정답")
    print("=" * 78)
    print(f"  덱      {', '.join(names)}")
    print(f"  상대    {enemy or '기본'}")
    print(f"  대상    {who} / 부위 {args.piece} = {piece or '(빈 부위)'}")

    # ── 기준선 ────────────────────────────────────────────────────────────
    full = ctx.run()
    denom = full["_총합"]
    red = ctx.without({who: piece})          # 그 캐릭터의 해당 부위만 뺀 덱
    base_red = red.run()
    print(f"\n  실제 덱 총딜 {denom:,}  /  부위를 뺀 총딜 {base_red['_총합']:,}"
          f"  (부위가 {(denom - base_red['_총합']) / denom * 100:.3f}%)")

    # ── 세 모델 ───────────────────────────────────────────────────────────
    print("\n  한계가치 측정 중...", flush=True)
    m_red = marginals(red, denom=denom)                    # 정공법
    m_full = marginals(ctx, denom=denom)                   # 지름길·옛 방식이 함께 쓴다
    v_direct = Values.from_marginals(m_red, who)
    # 되빼기가 안 통하는 옵션이 껴 있어도 여기서는 일부러 만든다 — 얼마나 틀리는지가
    # 이 스크립트가 재려는 것이다. 다른 자리에서는 `Values`가 이 조합을 거절한다.
    v_short = Values.from_marginals(m_full, who, removed=piece, allow_approx=True)
    v_old = Values.from_marginals(m_full, who)
    print(f"  ...끝 ({m_red.runs + m_full.runs}회, {time.time() - t0:.0f}초)")

    print("\n[한계가치 — 배경에 따라 얼마나 달라지나]")
    print("  " + f"{'':10}" + "".join(f"{o:>8}" for o in OPTIONS))
    print("  " + f"{'부위 뺌':10}" + "".join(f"{m_red.per_line[who][o]:>8.3f}" for o in OPTIONS))
    print("  " + f"{'부위 있음':10}" + "".join(f"{m_full.per_line[who][o]:>8.3f}" for o in OPTIONS))

    # ── 계산기 실측 대조 ──────────────────────────────────────────────────
    print(f"\n[구성별 대조 — 부위를 뺀 자리에 이 줄을 붙였을 때 스쿼드 총딜 %]")
    print(f"  {'구성':38} {'계산기':>8} {'정공법':>8} {'지름길':>8} {'옛방식':>8}")
    print("  " + "-" * 74)
    rows = []
    base_build = drop_lines(ctx.build_of(who), piece)
    for p in sample_pieces(args.n, args.seed):
        got = red.run({who: tuple(sorted(base_build + p))})
        truth = (got["_총합"] - base_red["_총합"]) / denom * 100.0
        direct = v_direct.worth_levels(p)
        short = v_short.worth_levels(p)
        old = v_old.worth_levels(p)
        rows.append((p, truth, direct, short, old))
        label = " ".join(f"{o}{lv}" for o, lv in p)
        print(f"  {label:38} {truth:>8.3f} {direct:>8.3f} {short:>8.3f} {old:>8.3f}")

    def worst(i: int) -> tuple[float, Piece]:
        r = max(rows, key=lambda r: abs(r[i] - r[1]))
        return abs(r[i] - r[1]), r[0]

    print("\n[최대 오차 (%p)]")
    for i, name in ((2, "정공법"), (3, "지름길"), (4, "옛 방식")):
        e, p = worst(i)
        print(f"  {name:8} {e:7.3f}   최악 구성 {' '.join(f'{o}{lv}' for o, lv in p)}")
    mean = {i: sum(abs(r[i] - r[1]) for r in rows) / len(rows) for i in (2, 3, 4)}
    print(f"  평균     정공법 {mean[2]:.3f} / 지름길 {mean[3]:.3f} / 옛 방식 {mean[4]:.3f}")
    print(f"\n  총 {time.time() - t0:.0f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
