"""결정론적 스냅샷 회귀 하네스 (Claude 전용).

계산기를 고친 뒤 기존 캐릭터가 조용히 틀어졌는지 잡는 도구다.
정확성 검증 도구가 아니라 **변화 감지** 도구다 — baseline을 찍는 순간
현재 코드의 기존 버그도 "정상"으로 고정된다. 정확성은 docs/scenarios/*.md 담당.

    python -m runner.snapshot                  # 전체 비교
    python -m runner.snapshot --squad S40_브리드라피   # 일부만
    python -m runner.snapshot --update         # baseline 갱신

## 스냅샷 4층 구조

절대 시각은 저장하지 않는다. 발사 타이밍이 1프레임(0.0167s) 밀리는 건 노이즈지만
"버프가 적용된 다음에 대미지가 계산되는가" 하는 **순서**는 신호이기 때문이다.

  L1 수치   — 캐릭터별 딜, 스킬별 딜·히트수, hit_tag 분포, 크리 수
  L2 발동   — 버프/인스턴트 이름별 발동 횟수와 대상 집합
  L3 순서   — 사이클별 이벤트 순서열 (시각 없음). 히트는 구간 집계로 압축
  L4 위상   — 사이클 간격(0.05초), 버프 발동 → 대상의 다음 히트까지 프레임 수 분포

자세한 사용법·diff 읽는 법은 docs/HARNESS.md 참고.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from calculator.buff_manager import _PARSED_SKILLS
from calculator.timeline import simulate
from runner import spec

ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = ROOT / "baseline"

DT = 1.0 / 60.0  # 시뮬레이터 프레임 간격 (timeline.py와 동일)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


# ── 스쿼드 정의 ────────────────────────────────────────────────────────────
# 캐릭터 dict는 이름만 주면 `runner/spec.py`가 채운다 —
# 기본 육성 스펙(장비 옵션은 오버로드 레벨 10의 우월코드 4줄·공격력 2줄·최대장탄 2줄,
# 컨트롤 없음 — 수치의 정본은 `spec.overload()`다)에
# 캐릭터별 기본 레이어(`data/char_defaults.json`)를 얹은 값이다.
# 아래 `chars`는 **그 스쿼드에서만** 다른 것을 적는 자리다.
#
# 새 스쿼드 추가 → 여기에 항목 추가 후 `--update --squad <이름>` 으로 baseline 생성.
#
# ## 이름 규칙
#
#   `S<시즌>_<대표>`  실제로 그 솔로레이드 시즌에서 쓰인 조합이다. 시즌 번호가
#                    적 코드까지 결정한다 (아래 표).
#   `커버_<대표>`      실전 기록이 없는 지정 편성. **실전 덱에 한 번도 안 나오는
#                    캐릭터를 덮으려고만** 만든다. 늘어나면 그만큼 하네스가
#                    실전에서 멀어지므로 최소로 둔다.
#
# ## 조합 출처
#
# 전부 enikk.app 솔로레이드 **시즌 36~40**의 Teams 데이터에서 가져왔다(2026-08-26 수집,
# 파스 10회 이상). 주석의 "N파스"가 그 시즌 그 조합의 실사용 횟수다.
# 멤버 순서도 enikk 표기 그대로다 — 임의로 재정렬하면 우리가 만든 조합이지
# 실사용 기록이 아니다. 순서를 바꿔야 할 이유가 생기면 그 이유를 주석에 적는다.
#
# 멤버 순서는 곧 버스트 사용 순서다 — BurstController가 스쿼드 입력 순서를
# 우선순위로 쓰므로, 같은 단계에 둘 이상이면 앞사람이 선점한다. 뒷사람도 버스트를
# 쓰게 하려면 `config["burst_pattern"]`으로 사이클을 나눈다 (`every:N` 또는 사이클 목록).
#
# ## 적 코드 = 보스 속성
#
# `enemy.code`에는 **보스 자신의 속성**을 넣는다. 적 코드에 우월한 캐릭터가 유리를 받으므로
# (`damage._CODE_ADVANTAGE`), "작열 약점 레이드"는 적 코드가 풍압이라는 뜻이다.
#
#   S36 Egovista          수냉   (약점 전격)
#   S37 Ultra             작열   (약점 수냉)
#   S38 Annihilio         철갑   (약점 풍압)
#   S39 Island Eater      전격   (약점 철갑)
#   S40 Luxurious Spider  풍압   (약점 작열)
#
# `커버_라플라스맥스웰`만 키가 없다 = **무속성 적**이다. `is_element_match`(DealForm ⑦)가
# 영구 거짓인 경로를 밟는 유일한 자리다 — 지우면 그 분기가 죽어도 하네스가 못 잡는다.
#
# ## 코어·파츠·적정거리는 시즌과 무관하다
#
# 시즌이 정하는 건 `code`뿐이다. 나머지 적 조건은 **그 경로를 밟을 baseline이 필요해서**
# 켠다 — 실제 그 시즌 보스가 그랬는지와는 별개이고, 켠 자리마다 이유를 주석에 적는다.
# 조건을 지우거나 옮기면 해당 경로가 죽어도 하네스가 못 잡으므로 함부로 옮기지 않는다.
#
#   `core_px`                코어히트율 확률 계산 + `core_dmg_pct`. 0이면 코어 없음.
#                            `S36_트리나홍련`(52) · `S40_토브드레이크`(52) · `S38_델타레이`(30)
#   `has_parts`              히트 이벤트가 `squad_body_hit` → `squad_part_hit`으로 바뀐다.
#                            `part_hit_count:N` 트리거와 `part_dmg_pct`의 전제다.
#   `part_break_interval`    (config) 주기마다 `event:part_destroy`를 쏜다.
#   `optimal_range_weapons`  적힌 무기군의 **일반 공격**에 ③ 고정 +30%. 스킬에는 안 붙는다.
#
# ## 컨트롤은 어디에 있나
#
# 컨트롤 전용 스쿼드는 없다. 캐릭터별 기본 레이어가 붙여 주거나(`data/char_defaults.json`),
# 그 조작이 실전인 조합의 `chars`에 직접 적는다. `CONTROL.md`의 설정 키가 전부 덮이도록
# 일부러 흩어 놨으므로, 아래 자리를 옮기면 그 키가 죽어도 하네스가 못 잡는다.
#
#   톡톡이 `tap_fire`          앨리스(`S40_앨리스모더니아`) · 아인(`S36_아인루주`) ·
#                             밀크 : 블루밍 바니(`S39_밀크도라`, `full_charge_interval`까지)
#   장전컨 A `before_fb_end`   `S38_델타레이` (`lead`·`duration`까지 한 자리에서) —
#                             상수 경로 전용 커버다. 실전 운용은 아래 C가 맡는다
#   장전컨 B `into_fb`         `S40_앨리스모더니아` (`margin`)
#   장전컨 C `finish_by_fb_end` `S39_나가라피` · `S37_브래디퀀시` · `S40_브리드라피`
#                             (셋 다 `if_dry`) — 진입 시각을 실제 재장전 시간에서 유도한다
#   탄충 취소 `cancel_on_full` 홍련 : 흑영(`S40_홍련흑영벨벳`, 레이어)
#   엄폐컨 `cover`             기본형 `S40_킬러와이프`(미란다 레이어) · `extend` `S40_브리드라피`
#   홀드컨 `own_full_burst`    `S36_아인루주` (아인+에이다, 에이다+미란다 두 규칙이 함께 걸린다)
#   홀드컨 `charge_hold_after_fb`  밀크 : 블루밍 바니(`S39_밀크도라`, 레이어)
#   명시 시퀀스 `sequence`      벨벳(`S38_마나`)

SQUADS: dict[str, dict] = {

    # ── S36 Egovista (적 수냉) ────────────────────────────────────────────
    "S36_이사벨아르카나": {
        # 커버: 이사벨, 아르카나, 신데렐라. S36 최다 사용 조합(745파스).
        "members": ["이사벨", "아니스 : 스타", "신데렐라", "아르카나", "크라운"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "수냉"},
        "seed": 42,
    },
    "S36_서머메이든": {
        # 커버: 아니스 : 스파클링 서머, 메이든 : 아이스 로즈, 에이드 : 에이전트 바니. 545파스.
        "members": ["아니스 : 스파클링 서머", "에이드 : 에이전트 바니", "목단",
                    "메이든 : 아이스 로즈", "프리바티"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "수냉"},
        "seed": 42,
    },
    "S36_네온리버": {
        # 커버: 네온 : 비전 아이, 리버렐리오. 495파스.
        "members": ["마스트 : 로망틱 메이드", "리틀 머메이드", "네온 : 비전 아이",
                    "리버렐리오", "앵커 : 이노센트 메이드"],
        "config": {
            "first_burst_time": 3.0,
            # 게이지 실누적 회귀 자리 — 네온 : 비전 아이의 버충속은 본인 대상 + 조건부 + 유한 지속이라, 영구 패시브인
            # 아니스·마나와 버프 수명이 다르다.
            "burst_gauge_mode": "accumulate",
        },
        "enemy": {"code": "수냉"},
        "seed": 42,
    },
    "S36_아인루주": {
        # 커버: 아인, 루주, 에이다, 타키나. 320파스.
        # 미란다가 있어 엄폐컨·홀드컨 레이어가 자동으로 붙는다(`spec._control_rules`).
        # 컨트롤 정책은 이렇게 미란다가 낀 스쿼드에서만 스냅샷에 들어온다 —
        # 정책 전용 스쿼드를 따로 두지 않는 이유다.
        "members": ["미란다", "타키나", "아인", "루주", "에이다"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "수냉"},
        "seed": 42,
    },
    "S36_질미하라": {
        # 커버: 질. 108파스.
        # 팀에 고정 B1이 없어 라피 : 레드 후드가 1버로 전환된다(`no_burst1_ally` →
        # `전투 보조`). 적 코드가 붙어 있으므로 라피 `부착형 유탄`의
        # `element_code_override`도 여기서 성립한다 — 두 경로를 한 스쿼드가 함께 밟는다.
        "members": ["라피 : 레드 후드", "나유타", "스노우 화이트 : 헤비암즈", "질",
                    "미하라 : 본딩 체인"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "수냉"},
        "seed": 42,
    },
    "S36_트리나홍련": {
        # 커버: 트리나, 홍련. 71파스.
        #
        # 홍련은 자해(`current_hp_reduce`)로 자기 체력을 깎아 `self_hp_below:60`(크리 대미지)·
        # `self_hp_below:50`(크리 확률)을 여는 유일한 캐릭터다. **체력 모델이 딜에 직접
        # 연결되는 자리**라, 자해가 현재 체력 비례가 아니게 되거나 회복이 엉뚱한 대상에게
        # 가면 여기가 먼저 운다.
        #
        # 트리나가 양방향으로 홍련의 체력을 움직인다 —
        #   ↓ `피스풀 트리`(`hp_only_caster_based_pct`)가 최대 체력만 올린다
        #   ↑ `네이처 그레이스 2·3`(`allies_lowest_hp:2`)이 최저 체력 아군을 회복
        # 후자는 instant target 해석이 시전자로 폴백하던 버그의 유일한 실사용 검출점이다.
        #
        # `core_px`는 실전 조합에서 온 값이 아니라 **여기서만 켜는 조건**이다. 코어가 없으면
        # 트리나의 `accuracy_pct` 버프가 딜에 반영되는 경로(명중률 → 코어히트율)와
        # `core_dmg_pct`가 어느 baseline에도 안 들어온다. 52px는
        # `docs/mechanics/명중률 탄착군.md`의 추정 코어 반경 26px에서 온 값이다.
        "members": ["라피 : 레드 후드", "스노우 화이트 : 헤비암즈", "트리나", "홍련", "프리바티"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "수냉", "core_px": 52},
        "seed": 42,
    },
    "S36_블랑": {
        # 커버: 블랑. 14파스.
        # 루주는 버스트를 쓰지 않는다 — B1이 미란다·루주 둘이라 그냥 두면 교대로 나가면서
        # 루주가 나가는 사이클에만 루주 버쿨감(`full_charge_count:8`)이 실려 사이클이
        # `12.25 ↔ 13.75`로 무한 교대한다. 미란다(`전담` 패턴)에게 B1을 맡겨 사이클을 고른다.
        # 루주 버스트는 `S36_아인루주`·`커버_라플라스맥스웰`이 덮는다.
        "members": ["미란다", "블랑", "스노우 화이트 : 헤비암즈", "루주", "디젤 : 윈터 스위츠"],
        "config": {"no_burst_char": "루주", "first_burst_time": 3.0},
        "enemy": {"code": "수냉"},
        "seed": 42,
    },

    # ── S37 Ultra (적 작열) ───────────────────────────────────────────────
    "S37_일레그루드밀라": {
        # 커버: 팬텀, 루드밀라 : 윈터 오너, 일레그 : 붐 앤 쇼크. 449파스.
        # 실전 조합은 이 자리가 헬름이었다. 헬름은 `S39_마르차나아쿠아`가 덮으므로,
        # 실전에 아예 안 나오는 팬텀(수냉 B3)으로 갈아 끼웠다 — 여기만의 변경이다.
        #
        # B3가 셋이라 그냥 두면 앞의 둘이 사이클을 번갈아 먹고 셋째가 0회가 된다.
        # 셋 다 이 스쿼드가 유일한 커버라 사이클을 3등분해 나눈다.
        "members": ["크라운", "리타", "팬텀", "루드밀라 : 윈터 오너", "일레그 : 붐 앤 쇼크"],
        "config": {
            "first_burst_time": 3.0,
            "burst_pattern": {
                "팬텀": [1, 4, 7, 10, 13, 16, 19],
                "루드밀라 : 윈터 오너": [2, 5, 8, 11, 14, 17, 20],
                "일레그 : 붐 앤 쇼크": [3, 6, 9, 12, 15, 18, 21],
            },
        },
        "enemy": {"code": "작열"},
        "seed": 42,
    },
    "S37_브래디퀀시": {
        # 커버: 브래디, 퀀시 : 이스케이프 퀸. 75파스.
        # 장전컨 `if_dry` 자리 — **남은 장탄으로 다음 풀버스트까지 못 버틸 때만** 엄폐한다.
        # 늘 엄폐하는 쪽은 `S40_브리드라피`가 덮으므로 둘이 대조 쌍이다.
        "members": ["마스트 : 로망틱 메이드", "퀀시 : 이스케이프 퀸", "앵커 : 이노센트 메이드",
                    "브래디", "라피 : 레드 후드"],
        "chars": {
            "라피 : 레드 후드": {
                "control": {"reload": {"policy": "finish_by_fb_end", "if_dry": True}},
            },
        },
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "작열"},
        "seed": 42,
    },

    # ── S38 Annihilio (적 철갑) ───────────────────────────────────────────
    "S38_델타레이": {
        # 커버: 델타 : 닌자 시프, 레이 (가칭), 아스카 : WILLE. 96파스.
        # 실전 조합은 이 자리가 헬름이고 크라운이 맨 뒤였다. 헬름은 다른 데서 덮으므로
        # 실전에 안 나오는 델타 : 닌자 시프로 바꾸고, 크라운을 1번 자리로 올렸다.
        # 그 결과 크라운이 B2를 독점하므로 델타에 격 사이클을 준다.
        #
        # 코어 30px — `S36_트리나홍련`·`S40_토브드레이크`의 52px보다 작은 코어다.
        # `_core_hit_prob`의 `(r_c/R)^n`이 직경에 비선형이라 한 값만으로는 곡선의 한 점만 밟는다.
        #
        # 장전컨 **하드코딩 커버 자리** — 정책 A(`before_fb_end`)와 그 상수 옵션
        # `lead`·`duration`이 여기 말고는 남지 않았다. 실전 운용으로는 정책 C가 낫지만
        # (라피 : 레드 후드 세 조합이 전부 C로 갔다), 상수를 쓰는 코드 경로가 죽어도
        # 하네스가 못 잡으면 안 되므로 한 자리는 남긴다. 성능이 아니라 **커버가 목적인
        # 설정**이라는 뜻이다 — 이 값을 실전 권장으로 읽지 말 것.
        "members": ["크라운", "라피 : 레드 후드", "아스카 : WILLE", "델타 : 닌자 시프",
                    "레이 (가칭)"],
        "chars": {
            "라피 : 레드 후드": {
                "control": {"reload": {"policy": "before_fb_end", "lead": 0.3, "duration": 1.0}},
            },
        },
        "config": {
            "first_burst_time": 3.0,
            "burst_pattern": {"델타 : 닌자 시프": "every:2"},
        },
        "enemy": {"code": "철갑", "core_px": 30},
        "seed": 42,
    },
    "S38_볼륨": {
        # 커버: 볼륨. 40파스.
        "members": ["볼륨", "민트", "스노우 화이트 : 헤비암즈", "신데렐라", "프리카"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "철갑"},
        "seed": 42,
    },
    "S38_누아르": {
        # 커버: 누아르. 37파스.
        # 다섯 중 넷이 SG인 샷건덱이라 **적정거리를 켜는 자리**로 골랐다 —
        # ③의 고정 +30%가 일반 공격에만 붙고 스킬에는 안 붙는 것을 여기서 지킨다.
        # 같은 골격의 `S40_토브드레이크`는 켜지 않는다(대조군).
        "members": ["토브", "아르카나 : 포츈 메이트", "도로시 : 세렌디피티", "누아르",
                    "솔린 : 프로스트 티켓"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "철갑", "optimal_range_weapons": ["SG"]},
        "seed": 42,
    },
    "S38_사쿠라로산나": {
        # 커버: 사쿠라 : 블룸 인 서머, 아크레인저 블랙, 로산나 : 시크 오션, 맥스웰. 23파스.
        # B3 셋(사쿠라·맥스웰·아크레인저 블랙)이라 `S37_일레그루드밀라`와 같은 이유로
        # 사이클을 3등분한다.
        #
        # **파츠 파괴 전용 자리다.** 사쿠라 : 블룸 인 서머·아크레인저 블랙·로산나 : 시크 오션
        # 셋이 전부 `event:part_destroy` 트리거를 갖고 있어, 한 스쿼드에서 세 명분이 함께 걸린다.
        # 30초 주기 = 180초에 6회. 45초(`S39_레이븐레드후드`)·60초(`S40_민트디젤`)와 달라
        # 사이클 대비 위상이 셋 다 다르게 어긋난다.
        "members": ["목단", "사쿠라 : 블룸 인 서머", "맥스웰", "아크레인저 블랙",
                    "로산나 : 시크 오션"],
        "config": {
            "first_burst_time": 3.0,
            "burst_pattern": {
                "사쿠라 : 블룸 인 서머": [1, 4, 7, 10, 13, 16, 19],
                "맥스웰": [2, 5, 8, 11, 14, 17, 20],
                "아크레인저 블랙": [3, 6, 9, 12, 15, 18, 21],
            },
            "part_break_interval": 30.0,
        },
        "enemy": {"code": "철갑", "has_parts": True},
        "seed": 42,
    },
    "S38_마나": {
        # 커버: 마나. 13파스.
        # **명시 시퀀스(`control.sequence`)를 쓰는 유일한 자리다.** 정책으로 표현되지 않는
        # 조작을 시각으로 직접 찍는 경로이고(`CONTROL.md §명시 시퀀스`), 시퀀스가 정책보다
        # 우선한다는 규칙도 여기서만 밟는다. 벨벳이 SR(차지형)이라 `hold`가 의미를 갖는다.
        # 시각이 절대값이라 **멤버를 바꾸면 사이클과 어긋난다** — 이 스쿼드는 편성을 건드리지 않는다.
        "members": ["마나", "리틀 머메이드", "벨벳", "프리바티", "크라운"],
        "chars": {
            "벨벳": {
                "control": {
                    "sequence": [
                        {"t": 45.0, "action": "cover", "duration": 1.5},
                        {"t": 60.0, "action": "hold", "until": 70.0},
                    ],
                },
            },
        },
        "config": {
            "first_burst_time": 3.0,
            # 게이지 실누적 회귀 자리 — 마나 `매터 시그마`는 **본인 대상**이라 곱연산으로 남는 유일한 회귀 자리다.
            # 리틀 머메이드의 「버스트 게이지 충전 37%」도 같이 밟는다.
            "burst_gauge_mode": "accumulate",
        },
        "enemy": {"code": "철갑"},
        "seed": 42,
    },

    # ── S39 Island Eater (적 전격) ────────────────────────────────────────
    "S39_나가라피": {
        # 커버: 나가, 라피 : 레드 후드, 아니스 : 스타, 크라운, 프리바티.
        # **최근 5시즌 통틀어 최다 사용 조합(1528파스).**
        # 크라운과 나가가 둘 다 B2라 크라운이 선점한다 — 나가는 여기가 유일한 커버라
        # 격 사이클을 준다.
        # 장전컨 정책 C 자리 — 진입 시각이 상수가 아니라 **그 시점의 실제 재장전 시간**에서
        # 나온다. 여기가 정책 C의 존재 이유를 가장 크게 보여 준다: 종전 설정(정책 A ·
        # `lead 0.3` · `duration 1.0`)은 풀버스트 종료 0.3초 전에 엄폐를 열고 고정 1.0초를
        # 버텨서 **버스트 게이지 충전 창을 0.7초 먹었다**. 60발/s MG라 그게 42발 = 게이지
        # 4.2%다. C로 바꾸면 fixed +8.1% · accumulate +9.5%(버충 4.29 → 3.68초).
        "members": ["크라운", "나가", "아니스 : 스타", "라피 : 레드 후드", "프리바티"],
        "chars": {
            "라피 : 레드 후드": {
                "control": {"reload": {"policy": "finish_by_fb_end", "if_dry": True}},
            },
        },
        "config": {
            "first_burst_time": 3.0,
            "burst_pattern": {"나가": "every:2"},
            # 게이지 실누적 회귀 자리 — 아군 가산 · 풀차지 래치 · 라피 유탄 예외 ·
            # 버충 컨트롤 · 카메라 유도가 한 스쿼드에서 전부 걸린다. 유저 실측
            # (180초 15버스트)이 붙어 있는 유일한 조합이기도 하다.
            "burst_gauge_mode": "accumulate",
        },
        "enemy": {"code": "전격"},
        "seed": 42,
    },
    "S39_크리스탈이브": {
        # 커버: 신데렐라 : 크리스탈 웨이브, 이브, 리틀 머메이드, 마스트 : 로망틱 메이드,
        #      앵커 : 이노센트 메이드. 1082파스.
        # 마스트 : 로망틱 메이드는 캐릭터별 기본 레이어의 버스트 패턴(`3의 배수`)이
        # 붙는다 — 여기에 다시 적지 않는다.
        #
        # **`part_dmg_pct`가 실제로 딜에 실리는 유일한 자리다.** 세 조건이 전부 맞아야 한다:
        #   ① `enemy.has_parts` — 없으면 `is_part`가 영원히 거짓
        #   ② `hits_parts: true`인 damage 효과 — 로스터 전체에서 신데렐라 : 크리스탈 웨이브
        #      `모드 스왑 2` 하나뿐이다 (레이븐·스노우 화이트 : 헤비암즈의 파츠 대미지 버프는
        #      짝이 되는 효과가 없어 무효다 — `IMPL-STATUS.md`)
        #   ③ 저격 모드 — `모드 스왑 2`의 `self_state:저격 모드` 조건. `weapon_mode_swap`으로 켠다
        # 셋 중 하나만 빠져도 이 경로가 통째로 죽는데 하네스는 아무것도 못 잡는다
        # (실제로 ①만 켰을 때 총딜이 1원도 안 움직였다).
        #
        # 파괴 주기는 주지 않는다 — 파괴 없이 파츠 히트만 계속되는 쪽도 밟아야 해서다.
        "members": ["마스트 : 로망틱 메이드", "신데렐라 : 크리스탈 웨이브",
                    "앵커 : 이노센트 메이드", "이브", "리틀 머메이드"],
        "chars": {
            "신데렐라 : 크리스탈 웨이브": {"weapon_mode_swap": True},
        },
        "config": {
            "first_burst_time": 3.0,
            # 게이지 실누적 회귀 자리 — 신데렐라 : 크리스탈 웨이브의 「충전 12%」는 `squad_ammo_consume:200` 트리거라
            # 헬름의 `full_charge_hit`과 발동 경로가 다르다.
            "burst_gauge_mode": "accumulate",
        },
        "enemy": {"code": "전격", "has_parts": True},
        "seed": 42,
    },
    "S39_마르차나아쿠아": {
        # 커버: 마르차나 : 마린 스터디, 헬름, 헬름 : 아쿠아마린, 미란다, 나유타. 711파스.
        # 나유타와 헬름 : 아쿠아마린이 둘 다 B2다 — 아쿠아마린이 버스트를 쓰는 유일한
        # 자리라 격 사이클로 나눈다.
        # 적정거리 SMG — 미란다·나유타 둘이 SMG다. SG만 켜는 `S38_누아르`와 달리
        # **다른 무기군**을 지정했을 때도 같은 경로가 도는지 지킨다.
        "members": ["미란다", "나유타", "마르차나 : 마린 스터디", "헬름", "헬름 : 아쿠아마린"],
        "config": {
            "first_burst_time": 3.0,
            "burst_pattern": {"헬름 : 아쿠아마린": "every:2"},
            # 게이지 실누적 회귀 자리 — 헬름 `진두지휘 3`의 「버스트 게이지 충전 14.31%」가
            # 도는 유일한 자리다(`handle_burst_charge_pct`). 히트당이 아니라 1회 가산이라
            # 아군 가산항이 안 붙는 경로이기도 하다.
            "burst_gauge_mode": "accumulate",
        },
        "enemy": {"code": "전격", "optimal_range_weapons": ["SMG"]},
        "seed": 42,
    },
    "S39_레이븐레드후드": {
        # 커버: 레이븐, 레드 후드. 671파스.
        # **여기에 파츠 조건을 켜지 않는 것이 의도다.** 레이븐이 `part_dmg_pct`(급소 공략)와
        # `event:part_destroy`(일점 공격·푸른 칼날)를 다 갖고 있어 후보였는데, 재 보니 둘 다
        # 무효였다 — 급소 공략은 짝이 되는 `hits_parts` 효과가 없고, 나머지 둘은 조건이
        # `not_self_state:A.N. 모드`인데 레이븐이 A.N. 모드로 사는 캐릭터다.
        # `has_parts` + `part_break_interval` 45초를 켜도 총딜이 1원도 안 움직인다.
        # 파괴 트리거는 `S38_사쿠라로산나`(30초)·`S40_민트디젤`(60초)이 덮는다.
        #
        # 사이클 2번째 칸이 `20.4`로 튀는 건 정상이다. 팀에 라피 : 레드 후드가 없어
        # 레드 후드(`burst_stage: "A"`)를 막을 사람이 없고, 2사이클에서 레드 후드가
        # **1단계와 3단계를 혼자 겸한다.** 그 사이클에 레이븐이 안 나가 다음 사이클이
        # 레이븐 쿨까지 밀린다 — `GAMEPLAY.md §버스트 사용 순서와 배치`가 말하는 그 상황이
        # 실전 덱에서 그대로 나온 경우다.
        "members": ["리타", "민트", "레이븐", "프리카", "레드 후드"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "전격"},
        "seed": 42,
    },
    "S39_소다": {
        # 커버: 소다 : 트윙클링 바니. 341파스.
        # 버쿨감 보유자가 없는 B3 3명 구성 — 사이클 20초가 정상이다.
        "members": ["토브", "나유타", "소다 : 트윙클링 바니", "도로시 : 세렌디피티", "드레이크"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "전격"},
        "seed": 42,
    },
    "S39_라플라스리코리코": {
        # 커버: 라플라스, 치사토. 254파스.
        # 방무(`armor_break_enabled` + `armor_break_dmg_pct`) 조합을 통째로 지키는 자리다:
        # 타키나가 아군 전체에 `armor_break_dmg_pct`를 뿌리고(스킬2, 15초 주기) 치사토가
        # 상시 방무 히트로 그걸 받아먹는다. 방무 경로가 깨지면 여기가 먼저 운다.
        # 타키나 스킬2는 사이클이 아니라 **15초 고정 주기**라 위상이 계속 어긋난다 —
        # 사이클과 무관한 `every:Ns` 트리거를 가진 유일한 하네스 스쿼드이기도 하다.
        # 상세는 docs/scenarios/타키나.md.
        #
        # 에이다는 B3 셋 중 맨 뒤라 버스트를 쓰지 않는다. 에이다 버스트는 `S36_아인루주`가 덮는다.
        #
        # 파츠 보유 — 라플라스가 `part_hit_count:1` 트리거다. 이 키가 없으면 히트 이벤트가
        # `squad_body_hit`으로 나가 라플라스 스킬이 통째로 안 걸린 채 baseline이 굳는다.
        "members": ["목단", "치사토", "라플라스", "에이다", "타키나"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "전격", "has_parts": True},
        "seed": 42,
    },
    "S39_밀크도라": {
        # 커버: 밀크 : 블루밍 바니, 도라. 125파스.
        # 실전 조합은 이 자리가 헬름 : 아쿠아마린이었다. 위 `S39_마르차나아쿠아`가 이미
        # 덮으므로, 실전에 안 나오는 도라(풍압 B2)로 갈아 끼웠다.
        # 나유타와 같은 B2라 격 사이클을 준다.
        "members": ["미란다", "마르차나 : 마린 스터디", "밀크 : 블루밍 바니", "나유타", "도라"],
        "config": {
            "first_burst_time": 3.0,
            "burst_pattern": {"도라": "every:2"},
        },
        "enemy": {"code": "전격"},
        "seed": 42,
    },
    "S39_맥스웰츠바이": {
        # 커버: 맥스웰, 스노우 화이트, 츠바이. 77파스.
        "members": ["나유타", "스노우 화이트", "맥스웰", "츠바이", "헬름 : 아쿠아마린"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "전격"},
        "seed": 42,
    },

    # ── S40 Luxurious Spider (적 풍압) ────────────────────────────────────
    "S40_토브드레이크": {
        # 커버: 토브, 아르카나 : 포츈 메이트, 도로시 : 세렌디피티, 드레이크,
        #      솔린 : 프로스트 티켓. **S40·S37 양쪽에서 1위**(1797파스 / 1281파스).
        # 실전 운용대로 솔린은 버스트를 쓰지 않는다(`no_burst_char`).
        # 코어 52px — SG 넷이라 명중률·코어히트율이 딜을 크게 좌우하는 조합이고,
        # S40 보스는 실제로 파괴 가능 파츠를 달고 있다. 적정거리는 켜지 않는다 —
        # 같은 골격의 `S38_누아르`가 그쪽을 맡는 대조 쌍이다.
        "members": ["토브", "아르카나 : 포츈 메이트", "도로시 : 세렌디피티", "드레이크",
                    "솔린 : 프로스트 티켓"],
        "config": {"no_burst_char": "솔린 : 프로스트 티켓", "first_burst_time": 3.0},
        "enemy": {"code": "풍압", "core_px": 52},
        "seed": 42,
    },
    "S40_퀸유키코": {
        # 커버: 퀸(마코토), 유키코. 1088파스.
        # 적정거리에 **두 무기군을 동시에** 지정하는 유일한 자리다 — 리틀 머메이드(SMG)와
        # 퀸(마코토)(SG)이 함께 있어, 한 스쿼드 안에서 붙는 쪽과 안 붙는 쪽이 갈린다.
        "members": ["리틀 머메이드", "크라운", "퀸(마코토)", "유키코", "마스트 : 로망틱 메이드"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압", "optimal_range_weapons": ["SG", "SMG"]},
        "seed": 42,
    },
    "S40_브리드라피": {
        # 커버: 브리드 : 사일런트 트랙, 미하라 : 본딩 체인. 834파스.
        # 라피 : 레드 후드 장전컨(정책 C) 자리 — 비버스트에 재장전이 걸리지 않도록
        # 풀버스트가 끝나기 전에 미리 채운다. `if_dry`는 `S37_브래디퀀시`가 같은 조합으로
        # 덮고, 하드코딩 쪽(정책 A · `lead` · `duration`)은 `S38_델타레이`가 덮는다.
        # 정책 B(`into_fb`)는 `S40_앨리스모더니아`.
        #
        # 미하라 : 본딩 체인 엄폐컨에는 `extend`를 준다 — 풀버스트가 끝난 뒤에도 0.5초 더
        # 엄폐한다. 미란다가 없어 레이어가 안 붙으므로 여기서 직접 켠다.
        # `extend` 없는 기본형은 `S40_킬러와이프`(미란다 동반, 레이어)가 덮는다.
        "members": ["브리드 : 사일런트 트랙", "라피 : 레드 후드", "아니스 : 스타",
                    "미하라 : 본딩 체인", "프리바티"],
        "chars": {
            "라피 : 레드 후드": {
                "control": {"reload": {"policy": "finish_by_fb_end", "if_dry": True}},
            },
            "미하라 : 본딩 체인": {
                "control": {"cover": {"policy": "own_full_burst", "extend": 0.5}},
            },
        },
        "config": {
            "first_burst_time": 3.0,
            # 게이지 실누적 회귀 자리 — 아니스 : 스타가 3번 자리가 아닌 두 번째 자리. 카메라 모드가 −2.0%였던 곳이라
            # 아군 가산이 카메라와 어떻게 겹치는지 신호가 S39_나가라피와 다르다.
            "burst_gauge_mode": "accumulate",
        },
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "S40_앨리스모더니아": {
        # 커버: 앨리스, 레이, 모더니아, 그레이브, 리타. 697파스.
        # 모더니아는 B3 셋 중 맨 뒤라 180초 동안 **버스트를 한 번도 쓰지 않는다** —
        # 실전 운용 그대로다(섬멸 모드는 무기계수를 낮추고 풀버스트를 5초 늘려 사이클
        # 간격을 밀어낸다). 그래서 `섬멸 모드` 자체는 어느 스쿼드도 커버하지 않는다.
        #
        # 앨리스 한 명만 조작한다 — 톡톡이 + 풀버스트 시작 전에 재장전을 시작해
        # 시작 시점에 70%쯤 진행된 상태로 만든다. margin은 "완료가 풀버스트 시작 후
        # 몇 초 뒤인가"이므로 남은 30%에 해당하는 실초를 준다: 이 스쿼드의 앨리스
        # 재장전 실측이 1.42초(기본 2.0 + 재장 큐브·버프)라 0.3 × 1.42 ≒ 0.43.
        "members": ["리타", "그레이브", "레이", "앨리스", "모더니아"],
        "chars": {
            # 톡톡이·차지속도 옵션은 기본 레이어가 준다. 이 스쿼드에서만 다른 건 장전컨이다.
            "앨리스": {
                "control": {"reload": {"policy": "into_fb", "margin": 0.43}},
            },
        },
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "S40_민트디젤": {
        # 커버: 민트, 프리카, 디젤 : 윈터 스위츠, 목단, 스노우 화이트 : 헤비암즈. 639파스.
        # 디젤 : 윈터 스위츠가 `event:part_destroy` 트리거다. 파괴 주기 60초(180초에 3회) —
        # 세 파괴 스쿼드 중 가장 드문 쪽이라, 발동이 잦을 때와 드물 때가 함께 남는다.
        "members": ["목단", "민트", "스노우 화이트 : 헤비암즈", "프리카", "디젤 : 윈터 스위츠"],
        "config": {"first_burst_time": 3.0, "part_break_interval": 60.0},
        "enemy": {"code": "풍압", "has_parts": True},
        "seed": 42,
    },
    "S40_플로라": {
        # 커버: 플로라. 427파스.
        "members": ["디젤 : 윈터 스위츠", "플로라", "브리드 : 사일런트 트랙",
                    "스노우 화이트 : 헤비암즈", "목단"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "S40_아스카": {
        # 커버: 아스카. 29파스.
        # 이 스쿼드가 지키는 경로는 **`lifesteal_pct` → `event:heal_received` → 자기 버프**다.
        # 아스카 `호승심 2`(공격력 96.98%)의 유일한 트리거가 본인 버스트가 준 라이프스틸이고,
        # 보스 sim은 아군 피격 모델이 없어 그 회복이 전부 오버힐이다 — `_apply_lifesteal()`이
        # HP를 최대치에서 잘라내고도 notify하기 때문에 성립한다(GAMEPLAY.md §트리거 발동 의미).
        # 힐 트리거를 자급하는 유일한 baseline이다.
        #
        # 다만 이 조합에는 보호막을 까는 아군이 없어 `during_shield` 게이트인
        # 아스카 `돌격 전술`은 열리지 않는다 — 종전 지그(크라운 동반)와 다른 점이다.
        "members": ["리타", "레이", "아스카", "모더니아", "그레이브"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "S40_홍련흑영벨벳": {
        # 커버: 홍련 : 흑영, 벨벳. 25파스.
        "members": ["리틀 머메이드", "나유타", "리버렐리오", "홍련 : 흑영", "벨벳"],
        "chars": {
            # 택티컬 베어 큐브(탄충) = 유일한 instant 큐브. 다른 큐브는 전부 battle_start
            # 상시 버프라, 이 자리가 빠지면 `_make_cube_effects`의 instant 경로를
            # 어느 baseline도 밟지 않는다. 홍련 : 흑영은 실전에서도 탄충을 낀다.
            "홍련 : 흑영": {"cube": {"name": "택티컬 베어 큐브", "level": 15}},
        },
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "S40_킬러와이프": {
        # 커버: D : 킬러 와이프. 14파스.
        # 파츠 보유 — D : 킬러 와이프가 `part_hit_count:1` 트리거다.
        # 미란다 + 미하라 : 본딩 체인 조합이라 엄폐컨 레이어도 함께 붙는다.
        "members": ["미란다", "나유타", "D : 킬러 와이프", "미하라 : 본딩 체인", "레이"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압", "has_parts": True},
        "seed": 42,
    },

    # ── 지정 편성 ─────────────────────────────────────────────────────────
    # 실전 덱에 안 나오는 캐릭터만 모은 자리다. 실사용 기록이 없으므로 딜 순위를
    # 실전과 견주는 근거로 쓰지 않는다 — 커버와 변화 감지 전용이다.

    "커버_레오나슈가": {
        # 커버: 레오나, 슈가.
        # `S40_토브드레이크`의 실전 골격에서 아르카나 : 포츈 메이트 → 레오나,
        # 드레이크 → 슈가로 갈아 끼운 편성이다.
        # 도로시 : 세렌디피티와 슈가가 B3 둘이라 격 사이클로 나뉜다.
        "members": ["토브", "레오나", "도로시 : 세렌디피티", "슈가", "솔린 : 프로스트 티켓"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "수냉"},
        "seed": 42,
    },
    "커버_라플라스맥스웰": {
        # 커버: 라플라스 : 얼티밋 히어로, 맥스웰 : 오디너리 미케닉.
        # **무속성 적을 쓰는 유일한 스쿼드다** — `is_element_match`(DealForm ⑦)가 영구
        # 거짓인 경로가 여기서만 스냅샷에 들어온다. 멤버가 전격 셋 + 풍압 이격 둘이라
        # 어느 코드를 붙여도 어중간한 것도 이유다.
        #
        # 트리나와 맥스웰 : 오디너리 미케닉이 둘 다 B2다. 그냥 두면 트리나가 독점하므로
        # 짝수 사이클을 맥스웰에게 준다 — 신데렐라가 버스트하는 사이클과 같은 짝이다.
        "members": ["라플라스 : 얼티밋 히어로", "루주", "신데렐라", "트리나",
                    "맥스웰 : 오디너리 미케닉"],
        "config": {
            "first_burst_time": 3.0,
            "burst_pattern": {"맥스웰 : 오디너리 미케닉": "every:2"},
        },
        "seed": 42,
    },
}


def build_squad(members: list[str], chars: dict[str, dict] | None = None) -> list[dict]:
    """이름 목록 → simulate()에 넘길 캐릭터 dict 목록.

    스펙 합성은 `runner/spec.py`가 한다 — 기본 스펙 → 캐릭터별 기본 레이어
    (`data/char_defaults.json`) → 여기의 `chars`. `chars`는 **그 스쿼드에서만** 다른 것을
    적는 자리다. 캐릭터를 어디서든 그렇게 굴린다면 `chars`가 아니라 레이어에 적는다.
    """
    return spec.build_squad(members, chars)


# ── 스냅샷 생성 ────────────────────────────────────────────────────────────

def _layer1(result) -> dict:
    """수치: 캐릭터별 딜·히트수·크리수·hit_tag 분포·스킬별 딜."""
    per_char: dict[str, dict] = {}
    for ev in result.hits:
        c = per_char.setdefault(ev.caster, {
            "hits": 0, "crits": 0, "hit_tags": Counter(), "skills": {},
        })
        c["hits"] += 1
        if ev.is_crit:
            c["crits"] += 1
        c["hit_tags"][ev.hit_tag] += 1
        s = c["skills"].setdefault(ev.skill_name, {"dmg": 0, "hits": 0})
        s["dmg"] += ev.damage
        s["hits"] += 1

    for c in per_char.values():
        c["hit_tags"] = dict(sorted(c["hit_tags"].items()))
        c["skills"] = dict(sorted(c["skills"].items()))

    fb_count = sum(
        1 for e in result.log.burst_log if e.event == "full_burst 시작"
    ) if result.log else 0

    return {
        "squad_total": result.squad_total,
        "char_total": dict(sorted(result.char_total.items())),
        "full_burst_count": fb_count,
        "per_char": dict(sorted(per_char.items())),
    }


def _layer2(log) -> dict:
    """발동 횟수: 버프/인스턴트 이름별 횟수와 대상 집합."""
    buffs: dict[str, dict] = {}
    for e in log.buff_events:
        if e.kind != "activate":
            continue
        b = buffs.setdefault(e.name, {"count": 0, "targets": set(), "stat": e.stat})
        b["count"] += 1
        b["targets"].add(e.target)

    instants: dict[str, dict] = {}
    for e in log.instant_events:
        i = instants.setdefault(e.name, {"count": 0, "targets": set(), "stat": e.stat})
        i["count"] += 1
        i["targets"].add(e.target)

    for d in (buffs, instants):
        for v in d.values():
            v["targets"] = sorted(v["targets"])

    return {
        "buffs": dict(sorted(buffs.items())),
        "instants": dict(sorted(instants.items())),
    }


# 같은 프레임에 서로 다른 로그 리스트의 이벤트가 있을 때의 정렬 우선순위.
# 실행 순서를 완벽히 복원하지는 못하지만 결정론적이며,
# 프레임이 다른 이벤트 간의 순서 변화(= 진짜 관심사)는 t로 정확히 잡힌다.
_KIND_PRIO = {"BURST": 0, "B+": 1, "I": 2, "B-": 3}


def _layer3(result, log) -> dict:
    """순서: 사이클별 이벤트 순서열. 시각은 저장하지 않는다.

    같은 (t, kind, name) 이벤트는 대상 집합으로 묶고,
    이벤트 사이 구간의 히트는 캐릭터별 집계 한 줄로 압축한다.
    """
    # (t, kind, name) → 대상 목록 으로 묶기
    grouped: dict[tuple[float, str, str], list[str]] = defaultdict(list)
    order: dict[tuple[float, str, str], int] = {}

    def add(t, kind, name, target, idx):
        key = (round(t, 6), kind, name)
        grouped[key].append(target)
        order.setdefault(key, idx)

    for i, e in enumerate(log.burst_log):
        add(e.t, "BURST", e.event, e.caster or "-", i)
    for i, e in enumerate(log.buff_events):
        add(e.t, "B+" if e.kind == "activate" else "B-", e.name, e.target, i)
    for i, e in enumerate(log.instant_events):
        add(e.t, "I", e.name, e.target, i)

    events = sorted(
        grouped.keys(),
        key=lambda k: (k[0], _KIND_PRIO.get(k[1], 9), k[2], order[k]),
    )

    # 사이클 경계 = 풀버스트 시작 시각
    fb_starts = [e.t for e in log.burst_log if e.event == "full_burst 시작"]
    bounds = [0.0] + fb_starts + [float("inf")]

    # 히트를 시각순으로 (이미 정렬돼 있음) — 구간 집계용 인덱스
    hit_ts = [h.t for h in result.hits]

    def hits_between(t0: float, t1: float) -> str | None:
        lo = bisect.bisect_left(hit_ts, t0)
        hi = bisect.bisect_left(hit_ts, t1)
        if lo >= hi:
            return None
        agg: dict[str, list[int]] = {}
        for h in result.hits[lo:hi]:
            a = agg.setdefault(h.caster, [0, 0, 0])
            a[0] += 1
            if h.is_crit:
                a[1] += 1
            if "core" in h.hit_tag:
                a[2] += 1
        parts = [
            f"{name}:{n} crit:{c} core:{co}"
            for name, (n, c, co) in sorted(agg.items())
        ]
        return "HITS " + " | ".join(parts)

    cycles: list[list[str]] = []
    for ci in range(len(bounds) - 1):
        c0, c1 = bounds[ci], bounds[ci + 1]
        seq: list[str] = []
        cyc_events = [k for k in events if c0 <= k[0] < c1]

        prev_t = c0
        for key in cyc_events:
            t, kind, name = key
            h = hits_between(prev_t, t)
            if h:
                seq.append(h)
            targets = sorted(set(grouped[key]))
            tgt = targets[0] if len(targets) == 1 else f"[{', '.join(targets)}]"
            seq.append(f"{kind} {name} → {tgt}")
            prev_t = t

        h = hits_between(prev_t, c1 if c1 != float("inf") else result.duration + 1)
        if h:
            seq.append(h)

        # 연속 동일 항목 런렝스 압축
        compressed: list[str] = []
        for item in seq:
            if compressed and compressed[-1].split(" ×")[0] == item:
                base = compressed[-1].split(" ×")
                n = int(base[1]) if len(base) > 1 else 1
                compressed[-1] = f"{item} ×{n + 1}"
            else:
                compressed.append(item)
        cycles.append(compressed)

    return {"cycles": cycles}


def _layer4(result, log) -> dict:
    """위상: 사이클 간격(0.05초)과 버프 발동 → 대상의 다음 히트까지 프레임 수 분포."""
    fb_starts = [e.t for e in log.burst_log if e.event == "full_burst 시작"]
    gaps = [
        round((fb_starts[i + 1] - fb_starts[i]) / 0.05) * 0.05
        for i in range(len(fb_starts) - 1)
    ]

    # 캐릭터별 히트 시각 (정렬됨) — bisect로 "다음 히트" 조회
    by_char: dict[str, list[float]] = defaultdict(list)
    for h in result.hits:
        by_char[h.caster].append(h.t)

    delays: dict[str, Counter] = defaultdict(Counter)
    for e in log.buff_events:
        if e.kind != "activate":
            continue
        ts = by_char.get(e.target)
        if not ts:
            continue
        idx = bisect.bisect_left(ts, e.t)
        if idx >= len(ts):
            continue
        frames = int(round((ts[idx] - e.t) / DT))
        delays[e.name][frames] += 1

    return {
        "cycle_gaps": [round(g, 2) for g in gaps],
        # 키는 JSON에서 어차피 문자열이 되므로 여기서 str로 통일한다.
        # (프레임 수 순서를 유지하려고 int로 정렬한 뒤 변환)
        "buff_to_hit_frames": {
            name: {str(k): v for k, v in sorted(c.items())}
            for name, c in sorted(delays.items())
        },
    }


def make_snapshot(squad_name: str, info: dict) -> dict:
    squad = build_squad(info["members"], info.get("chars"))
    config = spec.build_config(squad, info.get("config"))
    result = simulate(
        squad, config=config, enemy=info.get("enemy"),
        verbose=True, seed=info["seed"],
    )
    log = result.log
    snap = {
        "meta": {
            "squad": squad_name,
            "members": info["members"],
            "config": config,
            "enemy": info.get("enemy") or {},
            "seed": info["seed"],
            # 1층 이탈(레이어·오버라이드)을 스냅샷에 박아 둔다. 레이어가 조용히 바뀌면
            # 딜이 안 움직여도 여기서 FAIL이 난다 — 하네스 방식의 이탈 보고다.
            "spec_deviations": {
                nm: [f"{k}: {spec._fmt(b)} → {spec._fmt(c)} ({src})"
                     for k, b, c, src in items]
                for nm, items in spec.squad_deviations(squad).items()
            },
        },
        "L1_numbers": _layer1(result),
        "L2_activations": _layer2(log),
        "L3_order": _layer3(result, log),
        "L4_phase": _layer4(result, log),
    }
    # 저장된 baseline과 같은 표현으로 정규화한다.
    # JSON은 dict 키를 문자열로만 표현하고 tuple을 list로 바꾸므로,
    # 라운드트립을 거치지 않으면 타입 차이만으로 가짜 diff가 난다.
    return json.loads(json.dumps(snap, ensure_ascii=False))


# ── diff ──────────────────────────────────────────────────────────────────

def _fmt_delta(old: float, new: float) -> str:
    d = new - old
    pct = (d / old * 100) if old else float("inf")
    return f"{old:,} → {new:,}  ({d:+,}, {pct:+.2f}%)"


def _diff_l1(old: dict, new: dict, out: list[str]) -> None:
    if old["squad_total"] != new["squad_total"]:
        out.append(f"  스쿼드 총딜  {_fmt_delta(old['squad_total'], new['squad_total'])}")

    for name in sorted(set(old["char_total"]) | set(new["char_total"])):
        o, n = old["char_total"].get(name, 0), new["char_total"].get(name, 0)
        if o != n:
            out.append(f"  [{name}] 총딜  {_fmt_delta(o, n)}")

    if old["full_burst_count"] != new["full_burst_count"]:
        out.append(
            f"  풀버스트 횟수  {old['full_burst_count']} → {new['full_burst_count']}"
        )

    for name in sorted(set(old["per_char"]) | set(new["per_char"])):
        o = old["per_char"].get(name, {})
        n = new["per_char"].get(name, {})
        if o.get("hits") != n.get("hits"):
            out.append(f"  [{name}] 히트수  {o.get('hits')} → {n.get('hits')}")
        if o.get("crits") != n.get("crits"):
            out.append(f"  [{name}] 크리수  {o.get('crits')} → {n.get('crits')}")
        for tag in sorted(set(o.get("hit_tags", {})) | set(n.get("hit_tags", {}))):
            a, b = o.get("hit_tags", {}).get(tag, 0), n.get("hit_tags", {}).get(tag, 0)
            if a != b:
                out.append(f"  [{name}] hit_tag {tag}  {a} → {b}")
        for sk in sorted(set(o.get("skills", {})) | set(n.get("skills", {}))):
            a = o.get("skills", {}).get(sk, {"dmg": 0, "hits": 0})
            b = n.get("skills", {}).get(sk, {"dmg": 0, "hits": 0})
            if a != b:
                out.append(
                    f"  [{name}] 스킬 <{sk}>  딜 {a['dmg']:,} → {b['dmg']:,}"
                    f"  히트 {a['hits']} → {b['hits']}"
                )


def _diff_l2(old: dict, new: dict, out: list[str]) -> None:
    for group in ("buffs", "instants"):
        label = "버프" if group == "buffs" else "인스턴트"
        o, n = old[group], new[group]
        for name in sorted(set(o) | set(n)):
            a, b = o.get(name), n.get(name)
            if a == b:
                continue
            if a is None:
                out.append(f"  + {label} [{name}] 신규 발동 {b['count']}회 → {b['targets']}")
            elif b is None:
                out.append(f"  - {label} [{name}] 발동 사라짐 (기존 {a['count']}회)")
            else:
                if a["count"] != b["count"]:
                    out.append(
                        f"  ! {label} [{name}] 발동 {a['count']} → {b['count']}회"
                    )
                if a["targets"] != b["targets"]:
                    out.append(
                        f"  ! {label} [{name}] 대상 {a['targets']} → {b['targets']}"
                    )


def _diff_l3(old: dict, new: dict, out: list[str], max_lines: int = 12) -> None:
    oc, nc = old["cycles"], new["cycles"]
    if len(oc) != len(nc):
        out.append(f"  사이클 수 {len(oc)} → {len(nc)}")
    shown = 0
    for i in range(max(len(oc), len(nc))):
        a = oc[i] if i < len(oc) else []
        b = nc[i] if i < len(nc) else []
        if a == b:
            continue
        out.append(f"  ── 사이클 {i} 순서 변화 ──")
        import difflib
        for line in difflib.unified_diff(a, b, lineterm="", n=1):
            if line.startswith(("---", "+++", "@@")):
                continue
            out.append(f"    {line}")
            shown += 1
            if shown >= max_lines:
                out.append("    ... (이하 생략)")
                return


def _diff_l4(old: dict, new: dict, out: list[str]) -> None:
    if old["cycle_gaps"] != new["cycle_gaps"]:
        out.append(f"  사이클 간격  {old['cycle_gaps']}")
        out.append(f"           →  {new['cycle_gaps']}")
    o, n = old["buff_to_hit_frames"], new["buff_to_hit_frames"]
    for name in sorted(set(o) | set(n)):
        a, b = o.get(name, {}), n.get(name, {})
        if a == b:
            continue
        # 분포 전체를 찍으면 수백 개 키가 나와 읽을 수 없다. 달라진 프레임만 보인다.
        changed = [
            f"{f}프레임 {a.get(f, 0)}→{b.get(f, 0)}"
            for f in sorted(set(a) | set(b), key=lambda x: int(x))
            if a.get(f, 0) != b.get(f, 0)
        ]
        head = ", ".join(changed[:6])
        more = f" 외 {len(changed) - 6}건" if len(changed) > 6 else ""
        out.append(f"  버프 [{name}] 발동→다음히트  {head}{more}")


def _diff_spec(old: dict, new: dict, out: list[str]) -> None:
    """기본 스펙 이탈(레이어·오버라이드) 변화. 딜이 안 움직여도 이건 잡아야 한다."""
    o, n = old.get("spec_deviations", {}), new.get("spec_deviations", {})
    for name in sorted(set(o) | set(n)):
        a, b = set(o.get(name, [])), set(n.get(name, []))
        for line in sorted(b - a):
            out.append(f"  + [{name}] {line}")
        for line in sorted(a - b):
            out.append(f"  - [{name}] {line}  (사라짐)")


def diff_snapshot(old: dict, new: dict) -> list[str]:
    """층별 diff 라인 목록. 비어 있으면 완전 일치."""
    out: list[str] = []
    buf: list[str] = []
    _diff_spec(old.get("meta", {}), new.get("meta", {}), buf)
    if buf:
        out.append("\n  [기본 스펙 이탈 변화]")
        out.extend(buf)
    for layer, fn, label in (
        ("L1_numbers", _diff_l1, "L1 수치"),
        ("L2_activations", _diff_l2, "L2 발동 횟수"),
        ("L3_order", _diff_l3, "L3 순서"),
        ("L4_phase", _diff_l4, "L4 위상"),
    ):
        buf: list[str] = []
        fn(old[layer], new[layer], buf)
        if buf:
            out.append(f"\n  [{label}]")
            out.extend(buf)
    return out


# ── 실행 ──────────────────────────────────────────────────────────────────

def baseline_path(squad_name: str) -> Path:
    return BASELINE_DIR / f"{squad_name}.json"


def save(squad_name: str, snap: dict) -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path(squad_name).write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def coverage() -> tuple[int, int, list[str]]:
    """(파싱된 캐릭터 수, 커버된 수, 미커버 이름 목록).

    `HARNESS.md §스쿼드 커버리지`가 이 함수를 정본으로 가리킨다 — 문서에 명단을 옮겨
    적으면 캐릭터가 추가될 때마다 조용히 낡는다. `test_*`는 지그용 더미라 제외한다.
    """
    parsed = [c for c in _PARSED_SKILLS if not c.startswith("test_")]
    members = {m for info in SQUADS.values() for m in info["members"]}
    uncovered = sorted(set(parsed) - members)
    return len(parsed), len(parsed) - len(uncovered), uncovered


def _snapshot_job(name: str) -> tuple[str, dict]:
    """워커 프로세스 진입점. 프로세스 간에 오가는 건 스쿼드 이름과 스냅샷 dict뿐이다."""
    return name, make_snapshot(name, SQUADS[name])


def _snapshots(names: list[str], jobs: int):
    """(이름, 스냅샷)을 `names` 순서 그대로 내놓는다.

    스쿼드끼리 완전히 독립이고 시드가 고정이라(`HARNESS.md §왜 결정론적인가`) 어느
    순서로 돌리든, 몇 개를 동시에 돌리든 결과가 같다. `simulate()`가 건드리는 난수는
    프로세스별 전역 `random`이고 워커마다 자기 시드를 다시 심는다.

    출력 순서는 `ProcessPoolExecutor.map`이 보존하므로 순차 실행과 로그가 동일하다.
    """
    if jobs <= 1 or len(names) <= 1:
        for name in names:
            yield name, make_snapshot(name, SQUADS[name])
        return
    with ProcessPoolExecutor(max_workers=min(jobs, len(names))) as ex:
        yield from ex.map(_snapshot_job, names)


def run(names: list[str], update: bool, jobs: int) -> int:
    n_fail = 0
    for name in names:
        # 프리뷰(출시 전 카드 기준) 캐릭터가 낀 baseline은 출시 후 정식 등록에서 바뀔 수 있다
        if note := spec.preview_note(SQUADS[name]["members"]):
            print(f"⚠ [{name}] {note}")

    for name, snap in _snapshots(names, jobs):
        path = baseline_path(name)

        if update or not path.exists():
            save(name, snap)
            action = "갱신" if update else "신규 생성"
            print(f"[{action}] {name}  ({path.relative_to(ROOT)})")
            continue

        old = json.loads(path.read_text(encoding="utf-8"))
        lines = diff_snapshot(old, snap)
        if not lines:
            print(f"[{PASS}] {name}  총딜 {snap['L1_numbers']['squad_total']:,}")
        else:
            n_fail += 1
            print(f"[{FAIL}] {name}")
            print("\n".join(lines))
            print()
    return n_fail


def main() -> None:
    ap = argparse.ArgumentParser(description="결정론적 스냅샷 회귀 하네스")
    ap.add_argument("--squad", action="append", help="대상 스쿼드 (반복 지정 가능)")
    ap.add_argument("--update", action="store_true", help="baseline을 현재 결과로 갱신")
    ap.add_argument("--list", action="store_true", help="스쿼드 목록 출력")
    ap.add_argument(
        "--jobs", "-j", type=int, default=min(8, os.cpu_count() or 1),
        help="동시에 돌릴 스쿼드 수 (기본: CPU 수, 최대 8). 1이면 순차 — "
             "결과는 어느 쪽이든 같고, 디버깅할 때만 1로 둔다",
    )
    args = ap.parse_args()

    if args.list:
        for name, info in SQUADS.items():
            mark = "○" if baseline_path(name).exists() else "×"
            print(f"  {mark} {name}: {', '.join(info['members'])}")
            squad = build_squad(info["members"], info.get("chars"))
            for nm, items in spec.squad_deviations(squad).items():
                for k, b, c, src in items:
                    print(f"      · [{nm}] {k}: {spec._fmt(b)} → {spec._fmt(c)} ({src})")
        parsed, covered, uncovered = coverage()
        print(f"\n총 {len(SQUADS)}스쿼드 · 파싱된 {parsed}명 중 {covered}명 커버")
        print(f"\n미커버 {len(uncovered)}명 (새 스쿼드를 짤 때 우선 후보):")
        print("  " + " · ".join(uncovered))
        return

    names = args.squad or list(SQUADS)
    unknown = [n for n in names if n not in SQUADS]
    if unknown:
        print(f"알 수 없는 스쿼드: {unknown}\n사용 가능: {list(SQUADS)}")
        sys.exit(2)

    if args.update:
        print("=== baseline 갱신 ===\n")
    else:
        print("=== 스냅샷 회귀 검사 ===\n")

    n_fail = run(names, args.update, args.jobs)

    if args.update:
        print(f"\n{len(names)}개 스쿼드 baseline 저장 완료")
        return

    n_pass = len(names) - n_fail
    print(f"\n{n_pass}/{len(names)} 통과")
    if n_fail:
        print("\n변화가 의도된 것이면 `--update`로 baseline을 갱신한다.")
        print("의도치 않은 변화면 회귀다 — 원인을 찾을 때까지 갱신하지 않는다.")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
