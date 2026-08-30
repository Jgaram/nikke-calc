"""단발 시뮬 CLI (Claude 전용).

파일을 수정하지 않고 임의 스쿼드를 돌린다. 탐색적 디버깅도 `--view`로 여기서 한다.

    python -m runner.sim "리틀 머메이드,크라운,라피 : 레드 후드,미하라,헬름"
    python -m runner.sim "..." --view breakdown
    python -m runner.sim "..." --no-burst "리틀 머메이드" --seed 42
    python -m runner.sim "..." --expected          # 크리·코어히트를 기대값으로 (1회로 결정론적)
    python -m runner.sim "..." --view buff --char "라피 : 레드 후드"
    python -m runner.sim "..." --profile me        # 고정 스펙 대신 내 계정의 실제 육성으로

캐릭터 이름에 콤마는 없지만 콜론·공백은 있다 (`라피 : 레드 후드`).
구분자는 콤마이며 앞뒤 공백은 자동으로 벗겨진다.

**정식 명칭만 받는다.** 유저가 쓰는 별칭(`마스트`·`돌니스`)은 `docs/ALIASES.md`로
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
from calculator.timeline import _ANCHORS, simulate
from runner import spec as char_spec

VIEWS = ("summary", "breakdown", "analysis", "burst", "buff", "hits", "gauge")


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
    ap.add_argument(
        "--burst-gauge-mode", choices=["fixed", "accumulate"],
        help="버스트 사이클을 무엇으로 판정할지. fixed(기본) = 캐릭터별 `burst_regen_time`의 "
             "고정 시각. accumulate = 실누적 게이지가 100%%에 닿는 시각 "
             "(docs/mechanics/버스트 게이지.md). `--view gauge`와 같이 쓴다",
    )
    ap.add_argument(
        "--camera", metavar="이름",
        help="카메라를 볼 니케. 풀차지 게이지 배율(SR·RL)이 이 니케에게만 붙는다. "
             "빈 문자열(`--camera \"\"`)이면 아무도 안 보는 것으로 친다. 미지정이면 "
             "컨트롤·3번 자리에서 유도한다 (docs/CONTROL.md §카메라)",
    )
    ap.add_argument(
        "--expected", action="store_true",
        help="크리·코어히트를 확률 판정 대신 기대값으로 계산한다. 난수가 사라져 1회 실행으로 "
             "결정론적 기대딜이 나온다(시드·반복 평균 불필요). 대신 히트 목록의 '크리'·'코어' "
             "표시와 코어 hit_tag는 사라진다 — 배율이 히트마다 확률로 녹아 있어서다",
    )
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
        "--tap", action="append", metavar="이름[:rate[:release[:풀차지간격[:창]]]]",
        help="톡톡이를 시킬 차지형(SR/RL) 캐릭터. rate 기본 3.6발/s, release 기본 0.03초. "
             "풀차지간격(초)을 주면 그 간격마다 한 발은 풀차지로 쏜다 — `풀 차지 공격 시` "
             "버프 유지용(밀크 관통 특화 6초 → 5.5). 창은 always(기본)·burst_charge — "
             "burst_charge가 버충 컨트롤이다. "
             "예: --tap \"앨리스:4.0\" / --tap \"프리카:4.0:0.03:0:burst_charge\" "
             "(docs/CONTROL.md §톡톡이 · §버충 컨트롤)",
    )
    ap.add_argument(
        "--click", action="append", metavar="이름:창|앵커:행위[:키=값,...]",
        help="클릭 스케줄을 직접 적는다. 같은 캐릭터에 여러 번 주면 **준 순서대로** 쌓이고 "
             "먼저 매치되는 항목이 이긴다. 셋째 칸은 상태 창(always·burst_charge·"
             "burst_chain·own_full_burst)이거나 앵커(combat_start·fb_end·own_fb_end·"
             "next_fb_start·own_buff_end)이고, 앵커면 offset=·len=으로 구간을 적는다. "
             "행위는 tap·hold·hold_until_close·hold_judge·auto. "
             "priority=high·mid·low로 조작 등급을 덮어쓴다. gate=단계/사용자는 해당 "
             "사이클의 B단계 사용자가 일치할 때만 연다. "
             "예: --click \"아인:own_full_burst:hold:lead=0.5\" "
             "--click \"프리카:own_fb_end:tap:offset=-6,len=6,rate=4.0\" "
             "--click \"루주:burst_charge:tap:rate=4.0,gate=3/헬름\" "
             "--click \"헬름:burst_chain:hold_until_close:priority=mid\" "
             "(docs/CONTROL.md §설정 스키마)",
    )
    ap.add_argument(
        "--control-mode", choices=["solo", "warn", "strict"],
        help="조작자가 한 명이라는 제약을 어떻게 다룰지. solo(기본)는 겹치면 등급이 급한 쪽이 "
             "카메라를 가져가고(같은 등급이면 후입 우선) 뺏긴 쪽은 조작이 풀린다. warn은 전원 "
             "실행하고 겹침을 경고로만 싣는다(비현실적 상한). strict는 겹치는 순간 실패 "
             "(docs/CONTROL.md §조작자는 한 명)",
    )
    ap.add_argument(
        "--camera-mode", choices=["single", "shared"],
        help="카메라를 몇 명이 나눠 가질 수 있는가. single(기본)은 1명, shared는 컨트롤을 "
             "켠 전원 — 상한이지 실전값이 아니다 (docs/CONTROL.md §카메라)",
    )
    ap.add_argument(
        "--tactic", action="append", metavar="택틱[:담당]",
        help="택틱(목적 하나로 묶인 컨트롤 다발)을 켠다. data/tactics.json에 등록된 이름을 쓰고, "
             "담당을 주면 자동 선택 규칙을 덮어쓴다. 예: --tactic 버충 / --tactic \"버충:프리카\" "
             "(docs/CONTROL.md §택틱)",
    )
    ap.add_argument(
        "--cancel-on-full", action="append", metavar="이름",
        help="탄충 취소. 재장전 중 탄환 충전으로 탄창이 꽉 차면 재장전을 끊고 즉시 사격한다. "
             "장전컨 정책 없이 단독으로 켤 수 있다 (docs/CONTROL.md §탄충 취소)",
    )
    ap.add_argument(
        "--reload-ctrl", action="append", metavar="이름:정책|앵커[:값][:if_dry][:키=값]",
        help="장전컨. 종전 정책은 before_fb_end(값=lead, 기본 0.3) · into_fb(값=margin, 기본 0.1) · "
             "finish_by_fb_end(값=margin)이고, finish_by_own_buff_end는 buff=이름인 본인 발동 "
             "버프가 끝나기 전에 완료한다. 앵커(fb_end·own_fb_end·next_fb_start·combat_start·"
             "own_buff_end)를 직접 적고 offset=·minus=reload_total을 줘도 된다. "
             "끝에 if_dry를 붙이면 비버스트에 탄이 마를 때만 건다. "
             "priority=high·mid·low로 조작 등급을 덮어쓴다(기본은 C=상, D=중, A·B=하). "
             "gate=단계/사용자는 해당 사이클의 B단계 사용자가 일치할 때만 연다. "
             "예: --reload-ctrl \"리버렐리오:into_fb\" / "
             "--reload-ctrl \"프리카:finish_by_fb_end:0.1:if_dry\" / "
             "--reload-ctrl \"프리카:fb_end:offset=-0.1:minus=reload_total\" "
             "(docs/CONTROL.md §장전컨)",
    )
    ap.add_argument(
        "--cover-ctrl", action="append", metavar="이름:정책[:extend][:priority=등급]",
        help="버스트 엄폐컨. 정책은 own_full_burst — 본인이 버스트를 쓴 사이클의 풀버스트 동안 "
             "엄폐해 한 발도 쏘지 않는다. extend(기본 0)는 풀버스트 종료 뒤 더 끄는 시간(초). "
             "priority=high·mid·low로 조작 등급을 덮어쓴다(기본 중). "
             "예: --cover-ctrl \"미하라 : 본딩 체인:own_full_burst\" (docs/CONTROL.md §버스트 엄폐컨)",
    )
    ap.add_argument(
        "--hold-ctrl", action="append", metavar="이름:정책[:lead][:priority=등급]",
        help="홀드컨(차지형 전용). 정책은 own_full_burst — 본인 버스트 사이클의 풀버스트 동안 "
             "풀차지를 들고 있다가 종료 lead초 전(기본 0.5)에 뗀다. "
             "priority=high·mid·low로 조작 등급을 덮어쓴다(기본 중). "
             "예: --hold-ctrl \"에이다:own_full_burst\" (docs/CONTROL.md §홀드)",
    )
    ap.add_argument(
        "--auto", action="append", metavar="이름", nargs="?", const="__all__",
        help="캐릭터별 기본 레이어(data/char_defaults.json — 컨트롤·장비 옵션 차이분)를 "
             "통째로 건너뛴다. 이름 없이 주면 전원. 컨트롤 이득을 재는 대조군용. "
             "예: --auto \"앨리스\" / --auto",
    )
    ap.add_argument(
        "--favorite", action="append", metavar="이름:단계",
        help="애장품 단계를 바꾼다. 단계는 0(미보유)~3, 기본 스펙은 3단계다. 애장품은 단계마다 "
             "스킬 슬롯 하나를 애장품 판본으로 갈아끼운다 — 낮은 단계로 돌리려면 그 슬롯의 "
             "기본(비애장품) 판본이 파싱돼 있어야 한다(없으면 시뮬이 끊는다). "
             "예: --favorite \"드레이크:0\" (docs/PARSING.md §애장품)",
    )
    ap.add_argument(
        "--profile", metavar="이름",
        help="고정 스펙 대신 **실제 계정의 육성 상태**로 돌린다 (profiles/<이름>.json, "
             "`python scraper/profile_fetch.py`가 만든다). 레벨·돌파·코강·호감도·스킬 레벨·"
             "장비·오버로드·소장품이 프로필 값으로 바뀌고, 컨트롤·버스트 패턴은 그대로다. "
             "결과에는 프로필을 썼다는 사실이 강제로 실린다 — 고정 스펙 결과와 총딜을 "
             "직접 비교하면 안 된다. 예: --profile me",
    )
    ap.add_argument(
        "--profile-level", choices=char_spec.LEVEL_MODES, default="fixed",
        help="--profile 을 쓸 때 캐릭터 레벨을 무엇으로 볼지. fixed(기본) = 기본 스펙 레벨 400 "
             "고정 — 솔로레이드가 그렇게 돌기 때문이다. sync = 동기화 소대 레벨. "
             "인게임 개별 레벨은 쓰지 않는다 (소대에 넣었는지에 달린 편성 상태일 뿐이다)",
    )
    ap.add_argument(
        "--burst-pattern", action="append", metavar="이름:패턴",
        help="버스트 운용 패턴을 바꾼다 — **어느 사이클**에 누를지. 패턴 이름은 "
             "data/char_defaults.json의 `_burst_patterns`에 등록된 것, 또는 `없음`(패턴 해제). "
             "예: --burst-pattern \"마스트 : 로망틱 메이드:1,3,5,9,11,14\" (HARNESS §버스트 운용 패턴)",
    )
    ap.add_argument(
        "--burst-delay", action="append", metavar="이름:초",
        help="딜레이 버스트 — **사이클 안에서 언제** 누를지. 차례가 온 뒤 몇 초를 기다렸다 "
             "누른다(기본 0 = 즉시). 조작자가 한 명이라 그 단계 전체가 밀리고, 이후 사이클도 "
             "따라 밀린다. 카메라를 요구하지 않아 조율 대상이 아니다. "
             "예: --burst-delay \"프리카:2.0\" (docs/CONTROL.md §L0)",
    )
    args = ap.parse_args()

    members = [n.strip() for n in args.squad.split(",") if n.strip()]
    if not 1 <= len(members) <= 5:
        print(f"스쿼드는 1~5명이어야 한다 (입력 {len(members)}명: {members})")
        sys.exit(2)

    config: dict = {"first_burst_time": args.first_burst,
                    "allow_unparsed": args.allow_unparsed}
    if args.expected:
        config["rng_mode"] = "expected"
    if args.burst_gauge_mode:
        config["burst_gauge_mode"] = args.burst_gauge_mode
    if args.camera is not None:
        config["camera"] = args.camera
    if args.camera_mode:
        config["camera_mode"] = args.camera_mode
    if args.control_mode:
        config["control_mode"] = args.control_mode
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

    # 컨트롤 (docs/CONTROL.md). "이름[:값[:값]]" 형식을 char config의 control로 옮긴다
    controls: dict[str, dict] = {}

    def _split(spec: str, maxsplit: int = -1) -> list[str]:
        """캐릭터 이름에 콜론이 들어가므로(`아니스 : 스타`) 스쿼드 이름으로 먼저 매칭한다."""
        for n in members:
            if spec == n:
                return [n]
            if spec.startswith(n + ":"):
                return [n] + spec[len(n) + 1:].split(":", maxsplit)
        print(f"컨트롤 대상이 스쿼드에 없다: {spec!r}")
        sys.exit(2)

    def _gate(text: str) -> dict:
        stage, sep, user = text.partition("/")
        user = user.strip()
        if not sep or stage not in ("1", "2", "3") or not user:
            print(f"gate는 `단계/정식 명칭` 형식이어야 한다: {text!r}")
            sys.exit(2)
        if user not in members:
            print(f"gate의 버스트 사용자가 스쿼드에 없다: {user!r}")
            sys.exit(2)
        return {"burst_stage": stage, "burst_user": user}

    for spec in (args.tap or []):
        parts = _split(spec.strip())
        tap: dict = {"rate": float(parts[1]) if len(parts) > 1 else 3.6}
        if len(parts) > 2:
            tap["release"] = float(parts[2])
        if len(parts) > 3 and float(parts[3]):
            tap["full_charge_interval"] = float(parts[3])
        if len(parts) > 4:
            tap["window"] = parts[4]
        controls.setdefault(parts[0], {})["tap_fire"] = tap

    # 클릭 스케줄 — 같은 캐릭터에 여러 번 주면 준 순서대로 쌓인다(먼저 매치가 이긴다).
    for spec in (args.click or []):
        # 옵션 꼬리는 두 번만 나눈다. gate 사용자 정식 명칭에 콜론이 있어도 보존된다.
        parts = _split(spec.strip(), 2)
        if len(parts) < 3:
            print(f"--click 은 창과 행위가 필요하다: {spec!r}")
            sys.exit(2)
        # 창 자리는 **상태 창 이름이거나 앵커 이름**이다 — 앵커면 offset·len을 키=값으로 준다.
        # 어느 쪽인지는 앵커 카탈로그가 가른다(정본 한 곳). docs/CONTROL.md §설정 스키마.
        slot = parts[1]
        entry: dict = {"anchor": slot} if slot in _ANCHORS else {"window": slot}
        entry["mode"] = parts[2]
        for kv in (parts[3].split(",") if len(parts) > 3 else []):
            k, _, v = kv.partition("=")
            k = k.strip()
            # 등급·동적 오프셋은 문자열이다 — 검증은 조립 시점(timeline)이 한다
            if k == "gate":
                entry[k] = _gate(v.strip())
            else:
                entry[k] = v.strip() if k in ("priority", "minus") else float(v)
        controls.setdefault(parts[0], {}).setdefault("click", []).append(entry)

    # 택틱 → 캐릭터별 오버라이드 전개. 컨트롤은 아래 `controls`에 합류하고, 그 밖의 키
    # (버스트 패턴 등)는 `over`가 만들어진 뒤 얹는다 — 여기서는 `over`가 아직 없다.
    tactic_extra: dict[str, dict] = {}
    for spec_s in (args.tactic or []):
        tname, _, target = spec_s.strip().partition(":")
        for who, ap in char_spec.tactic_overrides(
                tname, members, target.strip() or None).items():
            ap = dict(ap)
            if ctrl := ap.pop("control", None):
                controls.setdefault(who, {}).update(ctrl)
            if ap:
                tactic_extra[who] = char_spec.deep_merge(tactic_extra.get(who, {}), ap)

    for name in (args.cancel_on_full or []):
        parts = _split(name.strip())
        controls.setdefault(parts[0], {}).setdefault("reload", {})["cancel_on_full"] = True

    for spec in (args.reload_ctrl or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--reload-ctrl 는 정책이 필요하다: {spec!r}")
            sys.exit(2)
        # 정책 자리도 **정책 이름이거나 앵커 이름**이다 — 앵커면 offset·minus를 키=값으로 준다.
        rl: dict = {"anchor": parts[1]} if parts[1] in _ANCHORS else {"policy": parts[1]}
        extras = parts[2:]
        # gate 값의 정식 명칭에 콜론이 있을 수 있으므로 gate= 이후는 다시 한 덩어리로 묶는다.
        gate_i = next((i for i, x in enumerate(extras) if x.startswith("gate=")), None)
        if gate_i is not None:
            extras = extras[:gate_i] + [":".join(extras[gate_i:])]
        for extra in extras:
            if extra == "if_dry":
                rl["if_dry"] = True
            elif "=" in extra:
                k, _, v = extra.partition("=")
                k = k.strip()
                if k == "gate":
                    rl[k] = _gate(v.strip())
                else:
                    rl[k] = v.strip() if k in ("priority", "minus", "buff") else float(v)
            else:
                rl["lead" if parts[1] == "before_fb_end" else "margin"] = float(extra)
        controls.setdefault(parts[0], {}).update(
            {"reload": {**controls.get(parts[0], {}).get("reload", {}), **rl}})

    for spec in (args.cover_ctrl or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--cover-ctrl 는 정책이 필요하다: {spec!r}")
            sys.exit(2)
        cv: dict = {"policy": parts[1]}
        for extra in parts[2:]:
            if extra.startswith("priority="):
                cv["priority"] = extra.partition("=")[2].strip()
            else:
                cv["extend"] = float(extra)
        controls.setdefault(parts[0], {})["cover"] = cv

    for spec in (args.hold_ctrl or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--hold-ctrl 는 정책이 필요하다: {spec!r}")
            sys.exit(2)
        hd: dict = {"policy": parts[1]}
        for extra in parts[2:]:
            if extra.startswith("priority="):
                hd["priority"] = extra.partition("=")[2].strip()
            else:
                hd["lead"] = float(extra)
        controls.setdefault(parts[0], {})["hold"] = hd

    # 스펙 합성은 runner/spec.py — 기본 육성 스펙 → 캐릭터별 기본 레이어
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

    for n, extra in tactic_extra.items():
        over[n] = char_spec.deep_merge(over[n], extra)

    for n, ctrl in controls.items():
        over[n]["control"] = ctrl

    for spec in (args.burst_pattern or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--burst-pattern 은 패턴 이름이 필요하다: {spec!r}")
            sys.exit(2)
        over[parts[0]]["burst_pattern"] = None if parts[1] == "없음" else ":".join(parts[1:])

    for spec in (args.burst_delay or []):
        parts = _split(spec.strip())
        if len(parts) < 2:
            print(f"--burst-delay 는 초가 필요하다: {spec!r}")
            sys.exit(2)
        over[parts[0]].setdefault("control", {}).setdefault("burst", {})["delay"] = float(parts[1])

    for spec in (args.favorite or []):
        parts = _split(spec.strip())
        if len(parts) != 2 or not parts[1].isdigit() or not 0 <= int(parts[1]) <= 3:
            print(f"--favorite 는 `이름:단계(0~3)` 형식이다: {spec!r}")
            sys.exit(2)
        over[parts[0]]["favorite_stage"] = int(parts[1])

    if not args.profile and args.profile_level != "fixed":
        print("--profile-level 은 --profile 과 함께만 의미가 있다")
        sys.exit(2)
    profile = (char_spec.load_profile(args.profile, args.profile_level)
               if args.profile else None)

    squad = char_spec.build_squad(members, over, no_layer=auto, profile=profile)
    config = char_spec.build_config(squad, config)

    # verbose=True: burst/buff/breakdown 뷰가 SimLog를 필요로 한다.
    try:
        result = simulate(
            squad, config=config, enemy=enemy or None, verbose=True, seed=args.seed
        )
    except ValueError as e:  # 이름 검증 실패 — 트레이스백은 도움이 안 된다
        print(e)
        sys.exit(2)

    if args.expected:
        seed_note = "  (기대값 모드 — 크리·코어히트 무작위 없음, 결정론적)"
    else:
        seed_note = f"  (seed={args.seed})" if args.seed is not None else "  (seed 미지정 — 매 실행 결과가 다름)"
    print(f"스쿼드: {', '.join(members)}{seed_note}")
    # 기준선 이탈은 언제나 출력에 싣는다 — 수치만 보고 기본 스펙 결과로 오해하지 않도록.
    print(char_spec.format_deviations(squad, profile=profile))
    # 조작자 관점 — 카메라는 하나뿐이라 겹친 조작은 그만큼 비현실적인 상한이다
    # (docs/CONTROL.md §조작자는 한 명). 이탈 보고와 같은 이유로 언제나 싣는다.
    if result.log is not None and result.log.control_log:
        print(result.log.control_summary())
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
    elif args.view == "gauge":
        print(result.log.gauge_summary())


if __name__ == "__main__":
    main()
