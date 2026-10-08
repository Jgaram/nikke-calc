"""단발 시뮬 CLI.

파일을 수정하지 않고 임의 스쿼드를 돌린다. 탐색적 디버깅도 `--view`로 여기서 한다.

    python -m runner.sim "리틀 머메이드,크라운,라피 : 레드 후드,미하라,헬름"
    python -m runner.sim "..." --view breakdown
    python -m runner.sim "..." --no-burst "리틀 머메이드" --seed 42
    python -m runner.sim "..." --expected          # 크리·코어히트를 기대값으로 (1회로 결정론적)
    python -m runner.sim "..." --view buff --char "라피 : 레드 후드"
    python -m runner.sim "..." --profile me        # 고정 스펙 대신 내 계정의 실제 육성으로
    python -m runner.sim "..." --boss 스크립트.json --view boss   # 보스 패턴 (runner/boss.py)
    python -m runner.sim --request 요청.json       # 요청 JSON 하나 (docs/SIM-JSON.md §요청)
    python -m runner.sim "..." --expected --json   # 다른 프로그램이 읽는 JSON 한 객체
    python -m runner.sim --batch < 요청.jsonl      # 요청 JSON Lines → 줄마다 결과 JSON

캐릭터 이름에 콤마는 없지만 콜론·공백은 있다 (`라피 : 레드 후드`).
구분자는 콤마이며 앞뒤 공백은 자동으로 벗겨진다.

**정식 명칭만 받는다.** 유저가 쓰는 별칭(`메스트`·`돌니스`)은 `docs/ALIASES.md`로
먼저 변환한다. 변환을 빠뜨리면 모르는 니케 오류로 끊긴다 (조용히 틀리지 않는다).

**입력의 정본은 요청 JSON 하나다**(`runner/request.py` `SCHEMA`). CLI 옵션은 그 요청을 사람이 적기 쉬운
표기일 뿐이라 `args_to_request()`가 요청으로 바꾼 뒤 같은 `prepare()` → `execute()`를 탄다 — `--tap
"프리카:4.0"`과 요청의 `chars.프리카.control.tap_fire`가 갈릴 자리가 없다.

텍스트 출력은 전부 기존 SimResult / SimLog 메서드를 그대로 부른다 — 신규 표시 로직 없음.
`--json`·`--batch`는 같은 시뮬 값을 JSON으로 옮길 뿐이다 — 총딜이 텍스트와 갈릴 자리가 없다.
형식의 정본은 docs/SIM-JSON.md다.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import functools
import json
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")  # 한글 에러 메시지가 콘솔 코드페이지로 깨지지 않게

from calculator.sim_result import print_team_analysis
from calculator.timeline import _ANCHORS, DEFAULT_CONFIG, DEFAULT_ENEMY, simulate
from runner import boss as boss_input
from runner import request as req_mod
from runner import spec as char_spec
from runner.request import RequestError

VIEWS = ("summary", "breakdown", "analysis", "burst", "buff", "hits", "gauge", "boss")


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(
        description="단발 시뮬 실행 (파일 수정 불필요)",
        allow_abbrev=False,     # `--jso`가 조용히 텍스트 모드로 도는 일이 없게
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "--view 종류\n"
            "  summary    스쿼드 총딜 + 캐릭터별 딜·비율 (기본)\n"
            "  breakdown  버스트 사이클별 스킬 딜 집계\n"
            "  analysis   캐릭터별 유형·버스트구간 분석\n"
            "  burst      버스트 사이클 이벤트 전체\n"
            "  buff       풀버스트 진입 시점 버프 스냅샷\n"
            "  hits       히트 목록 (재장전·버스트 인터리브)\n"
            "  gauge      버스트 게이지 충전 내역 · 사이클별 기여 (--burst-gauge-mode accumulate와 같이 쓴다)\n"
            "  boss       보스 패턴 흐름 · 니케 피격 (--boss와 같이 쓴다)\n"
        ),
    )
    ap.add_argument("squad", nargs="?", help="캐릭터 이름 콤마 구분 (1~5명). --batch면 주지 않는다")
    ap.add_argument(
        "--request", metavar="JSON|파일.json",
        help="요청 JSON 하나를 통째로 준다 (형식의 정본 runner/request.py — docs/SIM-JSON.md §요청). "
             "`{`로 시작하면 JSON 문자열, 아니면 파일 경로. 함께 준 다른 옵션은 그 위에 덮인다(RFC 7386). "
             "--batch와 같이 주면 모든 줄의 공통값이다",
    )
    ap.add_argument("--view", choices=VIEWS, help="출력 형식 (기본 summary). --json·--batch와는 같이 쓰지 않는다")
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
    ap.add_argument("--first-burst", type=float, help="첫 버스트 시각(초). 기본 3")
    ap.add_argument(
        "--allow-unparsed", action="store_true",
        help="스킬 미파싱 캐릭터를 스킬 0개로 돌린다. 파싱 전 신캐의 스탯·무기만 볼 때만 쓴다 "
             "(기본은 에러 — 별칭을 정식 명칭으로 못 바꾼 경우가 대부분이다)",
    )
    ap.add_argument(
        "--boss", metavar="프리셋|파일.json",
        help="보스를 바꾼다. `.json`으로 끝나면 보스 스크립트 파일(적 dict — patterns·atk·def·code, "
             "`preset`·`skill`·`part`로 프리셋 참조), 아니면 data/boss_presets.json의 프리셋 이름 "
             "(스탯·속성만, 패턴 없음). 아래 --enemy-def 등은 그 위에 덮는다. "
             "예: --boss \"솔로 레이드 S40\" / --boss 스크립트.json --view boss (runner/boss.py)",
    )
    ap.add_argument("--enemy-def", type=int, help="적 방어력")
    ap.add_argument("--enemy-code", choices=["풍압", "수냉", "작열", "전격", "철갑"],
                    help="적 속성 코드. 우월 코드(DealForm ⑦)·target_code 조건에 반영")
    ap.add_argument("--core-px", type=float, help="코어 직경(px). 0이면 코어 없음")
    ap.add_argument(
        "--distance", type=float,
        help="보스 거리. 주면 적정거리를 무기군 목록 대신 니케마다 적정 구간(CDN bonusrange — AR 25~45 · "
             "SR 45~100 · SMG 15~35 · SG 0~25 · MG 35~55 · RL 없음)과 비교한다. 적정 최대·최소 사거리 ▲ 반영 "
             "(calculator/boss_pattern.py §적정거리)",
    )
    ap.add_argument(
        "--aim", action="append", metavar="이름:표적[:키=값,...]",
        help="에임 컨트롤 — 좌표 모드 보스(enemy.coord)의 표적(또는 core)을 겨눈다. 카메라를 요구하는 조작이라 "
             "조율을 탄다. 키는 priority(기본 저지원·벌칙 파츠 high · 그 밖 mid) · window · anchor·offset·len. "
             "예: --aim \"목단:알집\" --aim \"앨리스:저지원:priority=high\" (docs/CONTROL.md §에임)",
    )
    ap.add_argument("--has-parts", action="store_true", help="파괴 가능 파츠 보유 보스로 설정")
    ap.add_argument(
        "--part-break-interval", type=float,
        help="파츠 파괴 주기(초) — 간단 모드 보스의 칸(enemy.part_break_interval). 0이면 무발동(기본). "
             "`event:part_destroy`에 반응하는 캐릭터(아크레인저 블랙 배터리 충전)를 켜고 끄는 스위치. "
             "패턴 모드 보스(--boss 스크립트)에는 못 준다 — 파괴는 표적이 실제로 깨질 때 나간다",
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
        "--cube", action="append", metavar="이름:큐브[:레벨]",
        help="니케가 낄 큐브. 레벨을 생략하면 아래 층 값(기본 15)이다. 모르는 이름·효과 모델이 없는 "
             "큐브는 오류다(data/base_stat_tables/cube.json). 예: --cube \"앨리스:렐릭 어설트 큐브\" / "
             "--cube \"홍련 : 흑영:택티컬 베어 큐브:15\"",
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
        "--profile", metavar="이름|JSON",
        help="고정 스펙 대신 **실제 계정의 육성 상태**로 돌린다 (profiles/<이름>.json, "
             "`python scraper/profile_fetch.py`가 만든다). 레벨·돌파·코강·호감도·스킬 레벨·"
             "장비·오버로드·소장품이 프로필 값으로 바뀌고, 컨트롤·버스트 패턴은 그대로다. "
             "`{`로 시작하면 **인라인 프로필**(파일 없이 육성을 직접 넘긴다 — 가상 육성용)로 "
             "읽는다. 형식은 docs/SIM-JSON.md §육성. "
             "결과에는 프로필을 썼다는 사실이 강제로 실린다 — 고정 스펙 결과와 총딜을 "
             "직접 비교하면 안 된다. 예: --profile me / "
             "--profile '{\"base\": \"default\", \"chars\": {\"크라운\": {\"skill_levels\": \"7/7/7\"}}}'",
    )
    ap.add_argument(
        "--profile-level", choices=char_spec.LEVEL_MODES,
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
    ap.add_argument(
        "--json", action="store_true",
        help="결과를 JSON 객체 하나로 stdout에 낸다 — 다른 프로그램이 읽는 용도. 경고·이탈 보고는 "
             "JSON 안의 칸으로 들어가고 stdout에는 그 밖의 아무것도 찍지 않는다. 오류면 "
             "{\"error\": {...}}를 내고 0이 아닌 코드로 끝난다 (docs/SIM-JSON.md)",
    )
    ap.add_argument(
        "--batch", action="store_true",
        help="stdin에서 JSON Lines(한 줄 = 요청 하나)를 읽어 한 줄씩 결과 JSON을 낸다. "
             "함께 준 다른 옵션·--request는 모든 줄의 공통값이고 줄이 그 위에 덮인다(RFC 7386). "
             "한 줄이 실패하면 그 줄만 error 객체다 (docs/SIM-JSON.md)",
    )
    return ap


class UsageError(ValueError):
    """입력이 잘못됐다 — 텍스트 모드는 메시지만 찍고 코드 2, JSON 모드는 error 객체가 된다."""


class _Parser(argparse.ArgumentParser):
    """JSON 모드에서는 인자 오류도 error 객체로 내야 하므로 종료 대신 예외를 던질 수 있게 한다."""

    raise_errors = False

    def error(self, message: str):
        if self.raise_errors:
            raise UsageError(message)
        super().error(message)


@dataclass
class Run:
    """`prepare()`가 조립을 끝낸 실행 한 건 — 텍스트·JSON 출력이 **같은** 이 값으로 시뮬한다."""
    members: list[str]
    squad: list[dict]
    config: dict
    enemy: dict
    boss_label: str | None
    profile: object | None
    auto: set[str]
    expected: bool
    seed: int | None


def _json_arg(value: str, what: str):
    """`{`로 시작하는 CLI 값은 JSON으로 읽는다(중복 키 거절). 아니면 그대로."""
    value = value.strip()
    return req_mod.loads(value, what) if value.startswith("{") else value


def _name_split(spec: str, maxsplit: int = -1) -> list[str]:
    """`이름:값:…` → [이름, 값, …]. 이름에 콜론이 있으므로(`아니스 : 스타`) 니케 정식 명칭으로 먼저 맞춘다.

    스쿼드가 아니라 **전체 명단**에서 가장 긴 이름부터 맞춘다 — `--batch`의 공통 옵션은 스쿼드를 모른 채
    요청으로 바뀌기 때문이다. 스쿼드에 있는 이름인지는 요청 검사(`request.check`)가 본다.
    """
    for n in _names_longest_first():
        if spec == n:
            return [n]
        if spec.startswith(n + ":"):
            return [n] + spec[len(n) + 1:].split(":", maxsplit)
    raise UsageError(f"컨트롤 대상을 니케 이름으로 읽지 못했다 (정식 명칭만 받는다): {spec!r}")


@functools.lru_cache(maxsize=1)
def _names_longest_first() -> tuple[str, ...]:
    return tuple(sorted(char_spec._nikke(), key=len, reverse=True))


def _num(text: str, what: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise UsageError(f"{what}: 숫자가 아니다 {text!r}") from None


def _gate(text: str) -> dict:
    stage, sep, user = text.partition("/")
    user = user.strip()
    if not sep or stage not in ("1", "2", "3") or not user:
        raise UsageError(f"gate는 `단계/정식 명칭` 형식이어야 한다: {text!r}")
    return {"burst_stage": stage, "burst_user": user}


def args_to_request(args: argparse.Namespace) -> dict:
    """CLI 인자 → 요청 dict (runner/request.py `SCHEMA`). **준 옵션만** 칸이 된다 — 그래야 `--request`·
    배치 줄 위에 덮을 때(RFC 7386) 주지 않은 옵션이 기본값으로 그쪽 값을 지우지 않는다.

    사람용 표기(`--tap "프리카:4.0"` 등)는 여기서 `chars.<니케>.control`로 펴진다. 같은 니케의 같은 축에
    여러 옵션을 주면 지금까지와 같이 한 dict로 합친다(`--reload-ctrl` + `--cancel-on-full`).
    """
    req: dict = {}

    def put(section: str, key: str, value) -> None:
        if value is not None:
            req.setdefault(section, {})[key] = value

    if args.squad is not None:
        req["squad"] = [n.strip() for n in args.squad.split(",") if n.strip()]

    put("sim", "duration", args.duration)
    put("sim", "seed", args.seed)
    put("sim", "first_burst", args.first_burst)
    put("sim", "burst_gauge_mode", args.burst_gauge_mode)
    if args.expected:
        put("sim", "expected", True)
    if args.allow_unparsed:
        put("sim", "allow_unparsed", True)

    if args.boss:
        req["boss"] = _json_arg(args.boss, "--boss 인라인 JSON")
    put("enemy", "def", args.enemy_def)
    put("enemy", "code", args.enemy_code)
    put("enemy", "core_px", args.core_px)
    put("enemy", "distance", args.distance)
    put("enemy", "part_break_interval", args.part_break_interval)
    if args.has_parts:
        put("enemy", "has_parts", True)

    put("operation", "camera", args.camera)
    put("operation", "camera_mode", args.camera_mode)
    put("operation", "control_mode", args.control_mode)
    if args.no_burst:
        put("operation", "no_burst", args.no_burst.strip())
    if args.auto:
        auto = [a.strip() for a in args.auto]
        put("operation", "auto", True if "__all__" in auto else auto)
    for spec_s in (args.tactic or []):
        tname, _, target = spec_s.strip().partition(":")
        t = {"name": tname.strip(), **({"target": target.strip()} if target.strip() else {})}
        req.setdefault("operation", {}).setdefault("tactics", []).append(t)

    if args.profile:
        req["profile"] = _json_arg(args.profile, "인라인 프로필")
    if args.profile_level is not None:
        req["profile_level"] = args.profile_level

    chars: dict[str, dict] = {}

    def char(n: str) -> dict:
        return chars.setdefault(n, {})

    def ctl(n: str) -> dict:
        return char(n).setdefault("control", {})

    for n in (args.mode_swap or []):
        char(_name_split(n.strip())[0])["weapon_mode_swap"] = True

    for spec in (args.tap or []):
        parts = _name_split(spec.strip())
        tap: dict = {"rate": _num(parts[1], "--tap rate") if len(parts) > 1 else 3.6}
        if len(parts) > 2:
            tap["release"] = _num(parts[2], "--tap release")
        if len(parts) > 3 and _num(parts[3], "--tap 풀차지간격"):
            tap["full_charge_interval"] = _num(parts[3], "--tap 풀차지간격")
        if len(parts) > 4:
            tap["window"] = parts[4]
        ctl(parts[0])["tap_fire"] = tap

    # 클릭 스케줄 — 같은 캐릭터에 여러 번 주면 준 순서대로 쌓인다(먼저 매치가 이긴다).
    for spec in (args.click or []):
        # 옵션 꼬리는 두 번만 나눈다. gate 사용자 정식 명칭에 콜론이 있어도 보존된다.
        parts = _name_split(spec.strip(), 2)
        if len(parts) < 3:
            raise UsageError(f"--click 은 창과 행위가 필요하다: {spec!r}")
        # 창 자리는 **상태 창 이름이거나 앵커 이름**이다 — 앵커면 offset·len을 키=값으로 준다.
        # 어느 쪽인지는 앵커 카탈로그가 가른다(정본 한 곳). docs/CONTROL.md §설정 스키마.
        slot = parts[1]
        entry: dict = {"anchor": slot} if slot in _ANCHORS else {"window": slot}
        entry["mode"] = parts[2]
        for kv in (parts[3].split(",") if len(parts) > 3 else []):
            k, _, v = kv.partition("=")
            k = k.strip()
            # 등급·동적 오프셋은 문자열이다 — 검증은 요청 검사·조립 시점(timeline)이 한다
            if k == "gate":
                entry[k] = _gate(v.strip())
            else:
                entry[k] = v.strip() if k in ("priority", "minus") else _num(v, f"--click {k}")
        ctl(parts[0]).setdefault("click", []).append(entry)

    for name in (args.cancel_on_full or []):
        ctl(_name_split(name.strip())[0]).setdefault("reload", {})["cancel_on_full"] = True

    for spec in (args.reload_ctrl or []):
        parts = _name_split(spec.strip())
        if len(parts) < 2:
            raise UsageError(f"--reload-ctrl 는 정책이 필요하다: {spec!r}")
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
                    rl[k] = v.strip() if k in ("priority", "minus", "buff") else _num(v, f"--reload-ctrl {k}")
            else:
                rl["lead" if parts[1] == "before_fb_end" else "margin"] = _num(extra, "--reload-ctrl")
        c = ctl(parts[0])
        c["reload"] = {**c.get("reload", {}), **rl}

    for opt, axis, num_key, items in (("--cover-ctrl", "cover", "extend", args.cover_ctrl),
                                      ("--hold-ctrl", "hold", "lead", args.hold_ctrl)):
        for spec in (items or []):
            parts = _name_split(spec.strip())
            if len(parts) < 2:
                raise UsageError(f"{opt} 는 정책이 필요하다: {spec!r}")
            d: dict = {"policy": parts[1]}
            for extra in parts[2:]:
                if extra.startswith("priority="):
                    d["priority"] = extra.partition("=")[2].strip()
                else:
                    d[num_key] = _num(extra, opt)
            ctl(parts[0])[axis] = d

    # 에임 — 좌표 모드 보스의 표적을 겨눈다. 같은 캐릭터에 여러 번 주면 준 순서대로(먼저 맞는 항목이 이긴다)
    for spec in (args.aim or []):
        parts = _name_split(spec.strip(), 2)
        if len(parts) < 2 or not parts[1].strip():
            raise UsageError(f"--aim 은 겨눌 표적이 필요하다: {spec!r}")
        entry = {"at": parts[1].strip()}
        for kv in (parts[2].split(",") if len(parts) > 2 else []):
            k, _, v = kv.partition("=")
            k = k.strip()
            entry[k] = _num(v, f"--aim {k}") if k in ("offset", "len") else v.strip()
        ctl(parts[0]).setdefault("aim", []).append(entry)

    for spec in (args.burst_pattern or []):
        parts = _name_split(spec.strip())
        if len(parts) < 2:
            raise UsageError(f"--burst-pattern 은 패턴 이름이 필요하다: {spec!r}")
        ctl(parts[0]).setdefault("burst", {})["pattern"] = False if parts[1] == "없음" else ":".join(parts[1:])

    for spec in (args.burst_delay or []):
        parts = _name_split(spec.strip())
        if len(parts) < 2:
            raise UsageError(f"--burst-delay 는 초가 필요하다: {spec!r}")
        ctl(parts[0]).setdefault("burst", {})["delay"] = _num(parts[1], "--burst-delay")

    for spec in (args.favorite or []):
        parts = _name_split(spec.strip())
        if len(parts) != 2 or not parts[1].isdigit():
            raise UsageError(f"--favorite 는 `이름:단계(0~3)` 형식이다: {spec!r}")
        char(parts[0])["favorite_stage"] = int(parts[1])

    for spec in (args.cube or []):
        parts = _name_split(spec.strip())
        if len(parts) not in (2, 3) or not parts[1].strip():
            raise UsageError(f"--cube 는 `이름:큐브[:레벨]` 형식이다: {spec!r}")
        cube: dict = {"name": parts[1].strip()}
        if len(parts) == 3:
            if not parts[2].strip().isdigit():
                raise UsageError(f"--cube 레벨은 정수다: {spec!r}")
            cube["level"] = int(parts[2])
        char(parts[0])["cube"] = cube

    if chars:
        req["chars"] = chars
    return req


def base_request(args: argparse.Namespace) -> dict:
    """`--request`(있으면) 위에 CLI 옵션을 덮은 요청. 검사는 하지 않는다 — `prepare()`가 한다."""
    base: dict = {}
    if args.request:
        raw = args.request.strip()
        if raw.startswith("{"):
            base = req_mod.loads(raw, "--request")
        else:
            path = Path(raw)
            if not path.is_file():
                raise UsageError(f"--request 파일이 없다: {raw}")
            base = req_mod.loads(path.read_text(encoding="utf-8"), f"--request {path.name}")
        if not isinstance(base, dict):
            raise RequestError("", f"요청은 JSON 객체다: {type(base).__name__}")
    return req_mod.merge_patch(base, args_to_request(args))


def _engine_char(entry: dict) -> dict:
    """요청의 `chars` 항목 하나 → 3층 오버라이드 dict.

    `false`(축 끄기)는 엔진이 읽는 `None`으로 — 아래 층 값을 None으로 덮어 그 축을 지운다. 버스트 패턴 끄기는
    지정 자리(`burst_pattern: None`)로 옮긴다 — `spec._fold_burst_pattern`이 레이어 패턴까지 접어 지운다.
    """
    out = copy.deepcopy(entry)
    if "cube" in out:
        out["cube"] = char_spec.cube_dict(out["cube"])
    if "control" in out:
        ctrl = {k: (None if v is False else v) for k, v in out["control"].items()}
        burst = ctrl.get("burst")
        if isinstance(burst, dict) and burst.get("pattern") is False:
            out["burst_pattern"] = None
            burst = {k: v for k, v in burst.items() if k != "pattern"}
            if burst:
                ctrl["burst"] = burst
            else:
                ctrl.pop("burst")
        out["control"] = ctrl
    return out


def prepare(raw) -> Run:
    """요청(runner/request.py `SCHEMA`) → 조립된 스쿼드·config·적. 잘못된 요청은 `RequestError`로 끊는다."""
    req = req_mod.validate(raw)
    members: list[str] = list(req["squad"])
    sim_o, op = req.get("sim") or {}, req.get("operation") or {}

    config: dict = {"first_burst_time": sim_o.get("first_burst", DEFAULT_CONFIG["first_burst_time"]),
                    "allow_unparsed": sim_o.get("allow_unparsed", False)}
    if sim_o.get("expected"):
        config["rng_mode"] = "expected"
    for key, cfg_key in (("burst_gauge_mode", "burst_gauge_mode"), ("duration", "duration")):
        if key in sim_o:
            config[cfg_key] = sim_o[key]
    for key, cfg_key in (("camera", "camera"), ("camera_mode", "camera_mode"),
                         ("control_mode", "control_mode"), ("no_burst", "no_burst_char")):
        if key in op:
            config[cfg_key] = op[key]

    enemy: dict = {}
    boss_label = None
    if "boss" in req:
        try:
            enemy, boss_label = boss_input.load_boss(req["boss"])
        except ValueError as e:
            raise RequestError("/boss", str(e)) from None
    enemy.update(copy.deepcopy(req.get("enemy") or {}))

    # 전원 오토 = 레이어 1 — 패턴 모드의 저지 우선 타격(레이어 2)도 끈다(에임을 안 옮긴다)
    auto_v = op.get("auto", False)
    auto = set(members) if auto_v is True else set(auto_v or [])
    if auto_v is True:
        config["aim_interrupt"] = False

    # 3층 = 택틱 전개 위에 니케별 운용(`chars`). 명시한 쪽이 택틱 다발을 이긴다 — 좌클릭은 한 덩어리(spec.merge_over)
    over: dict[str, dict] = {}
    for t in op.get("tactics") or []:
        for who, apply in char_spec.tactic_overrides(t["name"], members, t.get("target")).items():
            over[who] = char_spec.deep_merge(over.get(who, {}), apply)
    for n, entry in (req.get("chars") or {}).items():
        over[n] = char_spec.merge_over(over.get(n, {}), _engine_char(entry))

    profile = None
    if "profile" in req:
        try:
            profile = char_spec.load_profile(req["profile"], req.get("profile_level", "fixed"))
        except SystemExit as e:
            raise RequestError("/profile", str(e.code)) from None

    squad = char_spec.build_squad(members, over, no_layer=auto, profile=profile)
    config = char_spec.build_config(squad, config)

    return Run(members=members, squad=squad, config=config, enemy=enemy,
               boss_label=boss_label, profile=profile, auto=auto,
               expected=bool(sim_o.get("expected")), seed=sim_o.get("seed"))


def execute(run: Run):
    """조립된 실행 한 건을 시뮬한다. 텍스트·JSON이 같은 호출을 쓴다 — 총딜이 갈릴 자리가 없다."""
    # verbose=True: burst/buff/breakdown 뷰와 조작 요약이 SimLog를 필요로 한다 (딜과는 무관하다).
    return simulate(run.squad, config=run.config, enemy=run.enemy or None,
                    verbose=True, seed=run.seed)


def print_text(run: Run, result, args: argparse.Namespace) -> None:
    """사람이 읽는 출력 (`--view`). 이 레포의 디버깅·문서용이다."""
    if run.expected:
        seed_note = "  (기대값 모드 — 크리·코어히트 무작위 없음, 결정론적)"
    else:
        seed_note = f"  (seed={run.seed})" if run.seed is not None else "  (seed 미지정 — 매 실행 결과가 다름)"
    print(f"스쿼드: {', '.join(run.members)}{seed_note}")
    # 기준선 이탈은 언제나 출력에 싣는다 — 수치만 보고 기본 스펙 결과로 오해하지 않도록.
    print(char_spec.format_deviations(run.squad, profile=run.profile))
    # 보스도 기본 적이 아니면 같은 자리에 싣는다 — --enemy-def 등이 덮은 뒤의 최종값이다.
    if run.boss_label is not None:
        print(boss_input.describe(run.enemy, run.boss_label))
    # 조작자 관점 — 카메라는 하나뿐이라 겹친 조작은 그만큼 비현실적인 상한이다
    # (docs/CONTROL.md §조작자는 한 명). 이탈 보고와 같은 이유로 언제나 싣는다.
    if result.log is not None and result.log.control_log:
        print(result.log.control_summary())
    print()

    chars = [c.strip() for c in args.char] if args.char else None

    view = args.view or "summary"
    if view == "summary":
        print(result.summary(chars))
        print()
        print(result.dmg_breakdown(chars))
    elif view == "breakdown":
        print(result.skill_breakdown_by_cycle(chars))
    elif view == "analysis":
        print_team_analysis(result, chars)
    elif view == "burst":
        print(result.log.burst_summary(chars))
    elif view == "buff":
        print(result.log.buff_summary(chars))
    elif view == "hits":
        print(result.hit_summary(chars))
    elif view == "gauge":
        print(result.log.gauge_summary())
    elif view == "boss":
        print(result.boss_summary())


# ── 기계용 출력 (--json · --batch) ─────────────────────────────────────────
# 형식의 정본은 docs/SIM-JSON.md다. 칸을 바꾸면 SCHEMA_VERSION을 올리고 그 문서를 같이 고친다.

SCHEMA_VERSION = 2     # 입력(요청)·출력 계약 전체의 판 — runner/request.py `VERSION`과 같다
REPO = "Jgaram/nikke-calc"
_ROOT = Path(__file__).resolve().parent.parent

_SOURCE = {"레이어": "layer", "지정": "override"}


@functools.lru_cache(maxsize=1)
def evaluator() -> dict:
    """결과를 낸 평가기 버전 — 커밋 해시와 작업 트리 변경 여부. git이 없으면 null.

    `--no-optional-locks`: `git status`가 인덱스를 갱신해 쓰지 않게 한다 — 여러 프로세스를
    동시에 띄워도 공유 파일(.git/index)에 쓰는 일이 없어야 한다. 추적 중인 파일의 변경만
    본다(`-uno`) — 추적 밖 파일(profiles/ 등)은 평가기 코드가 아니다.
    """
    def git(*a: str) -> str | None:
        try:
            r = subprocess.run(["git", "--no-optional-locks", "-C", str(_ROOT), *a],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "-uno") if commit else None
    return {"repo": REPO, "commit": commit, "dirty": None if status is None else bool(status)}


def payload(run: Run, result) -> dict:
    """시뮬 결과 → JSON 객체 (docs/SIM-JSON.md §결과 객체)."""
    total = int(result.squad_total)
    members = [{"name": n, "damage": int(result.char_total.get(n, 0)),
                "share": (result.char_total.get(n, 0) / total) if total else 0.0}
               for n in run.members]

    dev = char_spec.squad_deviations(run.squad, run.profile)
    tacts, dropped = char_spec.applied_tactics(run.squad)
    preview = [n for n in run.members if char_spec.is_preview(n)]
    deviated = [n for n in run.members if n in dev]
    layered = [n for n in deviated if any(src == "레이어" for *_, src in dev[n])]
    label = "프로필(2.5층)" if run.profile is not None else "기본 스펙(1층)"

    warnings: list[str] = []
    if preview:
        warnings.append(char_spec.preview_note(run.members))
    if run.profile is not None:
        warnings.append(run.profile.header())
        warnings += run.profile.notes(run.members) + run.profile.cube_notes(run.squad)
    if dev:
        warnings.append(f"{label} 이탈 {len(dev)}명 — {', '.join(deviated)}")
    if dropped:
        warnings.append("택틱 없음 — 조건은 맞으나 붙지 않았다: " + " · ".join(
            f"{t}({', '.join(who)})" for t, who in dropped.items()))
    if not run.expected and run.seed is None:
        warnings.append("seed 미지정 — 매 실행 결과가 다름")
    control = None
    if result.log is not None and result.log.control_log:
        control = result.log.control_summary()
        if result.log.control_occupancy()["max"] >= 2:
            warnings.append(control)
    if result.boss_unmodeled:
        warnings.append(f"효과 모델이 없는 보스 패턴: {result.boss_unmodeled}")

    return {
        "schema_version": SCHEMA_VERSION,
        "total_damage": total,
        "members": members,
        "duration": result.duration,
        "expected": run.expected,
        "seed": run.seed,
        "boss": copy.deepcopy({**DEFAULT_ENEMY, **run.enemy}),
        "boss_label": run.boss_label,
        "spec": {
            "baseline": "profile" if run.profile is not None else "default",
            "at_baseline": not dev,
            "char_defaults": {"applied": layered, "skipped": [n for n in run.members if n in run.auto]},
            "deviated": deviated,
            "deviations": {n: [{"key": k, "baseline": b, "value": c, "source": _SOURCE.get(src, src)}
                               for k, b, c, src in dev[n]] for n in deviated},
            "tactics": tacts,
            "profile": (None if run.profile is None else
                        {"name": run.profile.name, "level_mode": run.profile.level_mode,
                         "source": run.profile.source, "base": run.profile.base,
                         "ungrown": [n for n in run.members if n in run.profile.ungrown]}),
            "preview": preview,
            "text": char_spec.format_deviations(run.squad, profile=run.profile),
        },
        "control": control,
        "control_detail": _control_detail(run, result),
        "warnings": warnings,
        "evaluator": evaluator(),
    }


def _control_detail(run: Run, result) -> dict:
    """조작자 관점(docs/CONTROL.md §두 관점)을 기계가 읽는 모양으로 — `control` 한 줄의 원본 값."""
    log = result.log
    occ = (log.control_occupancy() if log is not None
           else {"max": 0, "overlap_t": 0.0, "pairs": {}, "by_char": {}})
    return {
        "mode": run.config.get("control_mode") or DEFAULT_CONFIG["control_mode"],
        "camera_mode": run.config.get("camera_mode") or DEFAULT_CONFIG["camera_mode"],
        "by_char": {n: occ["by_char"][n] for n in run.members if n in occ["by_char"]},
        "max_concurrent": occ["max"],
        "overlap_t": occ["overlap_t"],
        "overlaps": [{"chars": list(pair), "t": t} for pair, t in occ["pairs"].items()],
        "preempted": dict(log.control_preempt) if log is not None else {},
    }


def _error(exc: BaseException) -> tuple[dict, int]:
    """예외 → (error 객체, 종료 코드). 입력 오류는 2, 그 밖(평가기 버그)은 1 + stderr 트레이스백.

    `path`는 요청 검사가 잡은 오류의 JSON Pointer(RFC 6901)다. 칸을 특정하지 못한 입력 오류는 null.
    """
    path = None
    if isinstance(exc, SystemExit):     # 라이브러리가 메시지로 끊은 경우 (spec 내부 검사 등)
        kind, msg, code = "invalid_input", str(exc.code), 2
    elif isinstance(exc, RequestError):
        kind, msg, code, path = "invalid_input", exc.detail, 2, exc.path
    elif isinstance(exc, ValueError):   # 조립·보스 검증이 끊은 입력 — 텍스트 모드와 같은 취급
        kind, msg, code = "invalid_input", str(exc), 2
        # 요청 검사를 지난 뒤의 ValueError라 평가기 쪽 판정일 수도 있다 — 추적은 남긴다
        traceback.print_exception(exc, file=sys.stderr)
    else:
        kind, msg, code = "internal_error", f"{type(exc).__name__}: {exc}", 1
        traceback.print_exception(exc, file=sys.stderr)
    return {"schema_version": SCHEMA_VERSION,
            "error": {"type": kind, "exception": type(exc).__name__, "message": msg, "path": path}}, code


def _dump(obj: dict, out) -> None:
    out.write(json.dumps(obj, ensure_ascii=False, allow_nan=False,
                         default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o)))
    out.write("\n")
    out.flush()


def _check_mode(args: argparse.Namespace) -> None:
    if args.view is not None or args.char:
        raise UsageError("--view·--char는 텍스트 출력 전용이다 — --json·--batch와 같이 쓰지 않는다")


def run_json(args: argparse.Namespace, out) -> int:
    try:
        _check_mode(args)
        run = prepare(base_request(args))
        obj, code = payload(run, execute(run)), 0
    except (Exception, SystemExit) as e:
        obj, code = _error(e)
    _dump(obj, out)
    return code


def run_batch(args: argparse.Namespace, out) -> int:
    """stdin JSON Lines → stdout JSON Lines. 줄마다 독립이고, 실패한 줄도 error 객체 한 줄을 낸다.

    줄 = 요청 하나. CLI 옵션·`--request`로 만든 공통 요청 위에 줄을 덮는다(RFC 7386 — 객체는 병합,
    배열·값은 교체, null은 그 칸을 지워 기본값으로).
    """
    if args.squad is not None:
        raise UsageError("--batch는 스쿼드를 stdin의 줄마다 받는다 — 위치 인자로 주지 않는다")
    _check_mode(args)
    base = base_request(args)
    for lineno, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        req = None
        try:
            req = req_mod.loads(line, f"{lineno}번 줄")
            if not isinstance(req, dict):
                raise RequestError("", f"배치 한 줄은 JSON 객체여야 한다: {type(req).__name__}")
            run = prepare(req_mod.merge_patch(base, req))
            obj = payload(run, execute(run))
        except (Exception, SystemExit) as e:
            obj, _ = _error(e)
        obj = {"line": lineno, **({"id": req["id"]} if isinstance(req, dict) and "id" in req else {}), **obj}
        _dump(obj, out)
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    machine = "--json" in argv or "--batch" in argv
    if not machine:
        args = ap.parse_args(argv)
        try:
            run = prepare(base_request(args))
            result = execute(run)
        except ValueError as e:     # 입력 오류 — 트레이스백은 도움이 안 된다
            print(e)
            sys.exit(2)
        print_text(run, result, args)
        return

    # 기계용: stdout에는 결과 JSON만 나간다. 그 밖의 print는 전부 stderr로 돌린다.
    out = sys.stdout
    ap.raise_errors = True
    with contextlib.redirect_stdout(sys.stderr):
        try:
            args = ap.parse_args(argv)
            code = run_batch(args, out) if args.batch else run_json(args, out)
        except SystemExit as e:
            if e.code in (0, None):     # --help
                raise
            obj, code = _error(e)
            _dump(obj, out)
        except Exception as e:
            obj, code = _error(e)
            _dump(obj, out)
    sys.exit(code)


if __name__ == "__main__":
    main()
