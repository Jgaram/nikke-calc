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

**정식 명칭만 받는다.** 유저가 쓰는 별칭(`마스트`·`돌니스`)은 `context/ALIASES.md`로
먼저 변환한다. 변환을 빠뜨리면 스킬 미파싱 에러로 끊긴다 (조용히 틀리지 않는다).

출력은 전부 기존 SimResult / SimLog 메서드를 그대로 부른다 — 신규 표시 로직 없음.
"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")  # 한글 에러 메시지가 콘솔 코드페이지로 깨지지 않게

from calculator.sim_result import print_team_analysis
from calculator.timeline import simulate
from context import spec as char_spec

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
    ap.add_argument(
        "--allow-unparsed", action="store_true",
        help="스킬 미파싱 캐릭터를 스킬 0개로 돌린다. 파싱 전 신캐의 스탯·무기만 볼 때만 쓴다 "
             "(기본은 에러 — 별칭을 정식 명칭으로 못 바꾼 경우가 대부분이다)",
    )
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
    ap.add_argument(
        "--tap", action="append", metavar="이름[:rate[:release[:풀차지간격]]]",
        help="톡톡이를 시킬 차지형(SR/RL) 캐릭터. rate 기본 3.6발/s, release 기본 0.03초. "
             "풀차지간격(초)을 주면 그 간격마다 한 발은 풀차지로 쏜다 — `풀 차지 공격 시` "
             "버프 유지용(밀크 관통 특화 6초 → 5.5). "
             "예: --tap \"앨리스:4.0\" / --tap \"밀크 : 블루밍 바니:4.0:0.03:5.5\" "
             "(context/CONTROL.md §톡톡이)",
    )
    ap.add_argument(
        "--reload-ctrl", action="append", metavar="이름:정책[:값]",
        help="장전컨. 정책은 before_fb_end(값=lead, 기본 0.3) 또는 into_fb(값=margin, 기본 0.1). "
             "예: --reload-ctrl \"리버렐리오:into_fb\" (context/CONTROL.md §장전컨)",
    )
    ap.add_argument(
        "--cover-ctrl", action="append", metavar="이름:정책[:extend]",
        help="버스트 엄폐컨. 정책은 own_full_burst — 본인이 버스트를 쓴 사이클의 풀버스트 동안 "
             "엄폐해 한 발도 쏘지 않는다. extend(기본 0)는 풀버스트 종료 뒤 더 끄는 시간(초). "
             "예: --cover-ctrl \"미하라 : 본딩 체인:own_full_burst\" (context/CONTROL.md §버스트 엄폐컨)",
    )
    ap.add_argument(
        "--hold-ctrl", action="append", metavar="이름:정책[:lead]",
        help="홀드컨(차지형 전용). 정책은 own_full_burst — 본인 버스트 사이클의 풀버스트 동안 "
             "풀차지를 들고 있다가 종료 lead초 전(기본 0.5)에 뗀다. "
             "예: --hold-ctrl \"에이다:own_full_burst\" (context/CONTROL.md §홀드)",
    )
    ap.add_argument(
        "--auto", action="append", metavar="이름", nargs="?", const="__all__",
        help="캐릭터별 기본 레이어(data/char_defaults.json — 컨트롤·장비 옵션 차이분)를 "
             "통째로 건너뛴다. 이름 없이 주면 전원. 컨트롤 이득을 재는 대조군용. "
             "예: --auto \"앨리스\" / --auto",
    )
    ap.add_argument(
        "--burst-pattern", action="append", metavar="이름:패턴",
        help="버스트 운용 패턴을 바꾼다. 패턴 이름은 data/char_defaults.json의 "
             "`_burst_patterns`에 등록된 것, 또는 `없음`(패턴 해제). "
             "예: --burst-pattern \"마스트 : 로망틱 메이드:1,3,5,9,11,14\" (HARNESS §버스트 운용 패턴)",
    )
    args = ap.parse_args()

    members = [n.strip() for n in args.squad.split(",") if n.strip()]
    if not 1 <= len(members) <= 5:
        print(f"스쿼드는 1~5명이어야 한다 (입력 {len(members)}명: {members})")
        sys.exit(2)

    config: dict = {"first_burst_time": args.first_burst,
                    "allow_unparsed": args.allow_unparsed}
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

    # 컨트롤 (context/CONTROL.md). "이름[:값[:값]]" 형식을 char config의 control로 옮긴다
    controls: dict[str, dict] = {}

    def _split(spec: str) -> list[str]:
        """캐릭터 이름에 콜론이 들어가므로(`아니스 : 스타`) 스쿼드 이름으로 먼저 매칭한다."""
        for n in members:
            if spec == n:
                return [n]
            if spec.startswith(n + ":"):
                return [n] + spec[len(n) + 1:].split(":")
        print(f"컨트롤 대상이 스쿼드에 없다: {spec!r}")
        sys.exit(2)

    for spec in (args.tap or []):
        parts = _split(spec.strip())
        tap: dict = {"rate": float(parts[1]) if len(parts) > 1 else 3.6}
        if len(parts) > 2:
            tap["release"] = float(parts[2])
        if len(parts) > 3:
            tap["full_charge_interval"] = float(parts[3])
        controls.setdefault(parts[0], {})["tap_fire"] = tap

    for spec in (args.reload_ctrl or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--reload-ctrl 는 정책이 필요하다: {spec!r}")
            sys.exit(2)
        rl: dict = {"policy": parts[1]}
        if len(parts) > 2:
            rl["lead" if parts[1] == "before_fb_end" else "margin"] = float(parts[2])
        controls.setdefault(parts[0], {})["reload"] = rl

    for spec in (args.cover_ctrl or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--cover-ctrl 는 정책이 필요하다: {spec!r}")
            sys.exit(2)
        cv: dict = {"policy": parts[1]}
        if len(parts) > 2:
            cv["extend"] = float(parts[2])
        controls.setdefault(parts[0], {})["cover"] = cv

    for spec in (args.hold_ctrl or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--hold-ctrl 는 정책이 필요하다: {spec!r}")
            sys.exit(2)
        hd: dict = {"policy": parts[1]}
        if len(parts) > 2:
            hd["lead"] = float(parts[2])
        controls.setdefault(parts[0], {})["hold"] = hd

    # 스펙 합성은 context/spec.py — 기본 육성 스펙 → 캐릭터별 기본 레이어
    # (data/char_defaults.json: 앨리스 톡톡이 등) → 아래 CLI 인자.
    # `--tap` 등을 주면 그 캐릭터의 기본 컨트롤 위에 얹힌다.
    over = {n: {"weapon_mode_swap": n in swap} for n in members}

    # --auto: 그 캐릭터는 기본 레이어를 통째로 건너뛴다 (컨트롤도 옵션도 기본 스펙 그대로).
    auto = {a.strip() for a in (args.auto or [])}
    if "__all__" in auto:
        auto = set(members)
    if auto - set(members):
        print(f"--auto 대상이 스쿼드에 없다: {sorted(auto - set(members))}")
        sys.exit(2)

    for n, ctrl in controls.items():
        over[n]["control"] = ctrl

    for spec in (args.burst_pattern or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--burst-pattern 은 패턴 이름이 필요하다: {spec!r}")
            sys.exit(2)
        over[parts[0]]["burst_pattern"] = None if parts[1] == "없음" else ":".join(parts[1:])
    squad = char_spec.build_squad(members, over, no_layer=auto)
    config = char_spec.build_config(squad, config)

    # verbose=True: burst/buff/breakdown 뷰가 SimLog를 필요로 한다.
    try:
        result = simulate(
            squad, config=config, enemy=enemy or None, verbose=True, seed=args.seed
        )
    except ValueError as e:  # 이름 검증 실패 — 트레이스백은 도움이 안 된다
        print(e)
        sys.exit(2)

    seed_note = f"  (seed={args.seed})" if args.seed is not None else "  (seed 미지정 — 매 실행 결과가 다름)"
    print(f"스쿼드: {', '.join(members)}{seed_note}")
    # 1층 이탈은 언제나 출력에 싣는다 — 수치만 보고 기본 스펙 결과로 오해하지 않도록.
    print(char_spec.format_deviations(squad))
    print()

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
