"""단발 시뮬 CLI (Claude 전용).

파일을 수정하지 않고 임의 스쿼드를 돌린다.
(context/test.py는 셀 상수를 매번 고쳐야 해서 "이 스쿼드 돌려봐"를 시킬 때마다
 파일이 더러워진다. 탐색적 디버깅은 test.py, 단발 조회는 이쪽.)

    python -m context.sim "리틀 머메이드,크라운,라피 : 레드 후드,미하라,헬름"
    python -m context.sim "..." --view breakdown
    python -m context.sim "..." --no-burst "리틀 머메이드" --seed 42
    python -m context.sim "..." --view buff --char "라피 : 레드 후드"

캐릭터 이름에 콤마는 없지만 콜론·공백은 있다 (`라피 : 레드 후드`).
구분자는 콤마이며 앞뒤 공백은 자동으로 벗겨진다.

출력은 전부 기존 SimResult / SimLog 메서드를 그대로 부른다 — 신규 표시 로직 없음.
"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from calculator.sim_result import print_team_analysis
from calculator.timeline import simulate

VIEWS = ("summary", "breakdown", "analysis", "burst", "buff", "hits")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="단발 시뮬 실행 (파일 수정 불필요)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "--view 종류\n"
            "  summary    스쿼드 총딜 + 캐릭터별 딜·비율 (기본)\n"
            "  breakdown  버스트 사이클별 스킬 딜 집계\n"
            "  analysis   캐릭터별 유형·버스트구간 분석\n"
            "  burst      버스트 사이클 이벤트 전체\n"
            "  buff       풀버스트 진입 시점 버프 스냅샷\n"
            "  hits       히트 목록 (재장전·버스트 인터리브)\n"
        ),
    )
    ap.add_argument("squad", help="캐릭터 이름 콤마 구분 (1~5명)")
    ap.add_argument("--view", default="summary", choices=VIEWS, help="출력 형식")
    ap.add_argument("--char", action="append", help="특정 캐릭터만 표시 (반복 지정 가능)")
    ap.add_argument("--seed", type=int, help="난수 시드. 지정하면 결과가 재현된다")
    ap.add_argument("--no-burst", help="버스트를 쓰지 않을 캐릭터")
    ap.add_argument("--duration", type=float, help="시뮬 시간(초). 기본 180")
    ap.add_argument("--first-burst", type=float, default=3.0, help="첫 버스트 시각(초)")
    ap.add_argument("--enemy-def", type=int, help="적 방어력")
    ap.add_argument("--enemy-code", choices=["풍압", "수냉", "작열", "전격", "철갑"],
                    help="적 속성 코드. 우월 코드(DealForm ⑦)·target_code 조건에 반영")
    ap.add_argument("--core-px", type=float, help="코어 직경(px). 0이면 코어 없음")
    ap.add_argument("--has-parts", action="store_true", help="파괴 가능 파츠 보유 보스로 설정")
    ap.add_argument(
        "--part-break-interval", type=float, default=0.0,
        help="파츠 파괴 주기(초). 0이면 무발동(기본). `event:part_destroy`에 반응하는 "
             "캐릭터(아크레인저 블랙 배터리 충전)를 켜고 끄는 스위치",
    )
    ap.add_argument(
        "--mode-swap", action="append",
        help="수동 재장전으로 무기 변경 모드에 진입시킬 캐릭터 (반복 지정 가능). "
             "예: --mode-swap \"신데렐라 : 크리스탈 웨이브\" → 저격 모드 진입 후 유지",
    )
    args = ap.parse_args()

    members = [n.strip() for n in args.squad.split(",") if n.strip()]
    if not 1 <= len(members) <= 5:
        print(f"스쿼드는 1~5명이어야 한다 (입력 {len(members)}명: {members})")
        sys.exit(2)

    config: dict = {"first_burst_time": args.first_burst}
    if args.no_burst:
        config["no_burst_char"] = args.no_burst.strip()
    if args.duration:
        config["duration"] = args.duration
    if args.part_break_interval:
        config["part_break_interval"] = args.part_break_interval

    enemy: dict = {}
    if args.enemy_def is not None:
        enemy["def"] = args.enemy_def
    if args.enemy_code:
        enemy["code"] = args.enemy_code
    if args.core_px is not None:
        enemy["core_px"] = args.core_px
    if args.has_parts:
        enemy["has_parts"] = True

    swap = {c.strip() for c in (args.mode_swap or [])}
    unknown = swap - set(members)
    if unknown:
        print(f"--mode-swap 대상이 스쿼드에 없다: {sorted(unknown)}")
        sys.exit(2)

    squad = [
        {"name": n, "equip_skills": {}, "weapon_mode_swap": n in swap}
        for n in members
    ]

    # verbose=True: burst/buff/breakdown 뷰가 SimLog를 필요로 한다.
    result = simulate(
        squad, config=config, enemy=enemy or None, verbose=True, seed=args.seed
    )

    seed_note = f"  (seed={args.seed})" if args.seed is not None else "  (seed 미지정 — 매 실행 결과가 다름)"
    print(f"스쿼드: {', '.join(members)}{seed_note}\n")

    chars = [c.strip() for c in args.char] if args.char else None

    if args.view == "summary":
        print(result.summary(chars))
        print()
        print(result.dmg_breakdown(chars))
    elif args.view == "breakdown":
        print(result.skill_breakdown_by_cycle(chars))
    elif args.view == "analysis":
        print_team_analysis(result, chars)
    elif args.view == "burst":
        print(result.log.burst_summary(chars))
    elif args.view == "buff":
        print(result.log.buff_summary(chars))
    elif args.view == "hits":
        print(result.hit_summary(chars))


if __name__ == "__main__":
    main()
