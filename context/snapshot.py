"""결정론적 스냅샷 회귀 하네스 (Claude 전용).

계산기를 고친 뒤 기존 캐릭터가 조용히 틀어졌는지 잡는 도구다.
정확성 검증 도구가 아니라 **변화 감지** 도구다 — baseline을 찍는 순간
현재 코드의 기존 버그도 "정상"으로 고정된다. 정확성은 context/scenarios/*.md 담당.

    python -m context.snapshot                  # 전체 비교
    python -m context.snapshot --squad 스쿼드1   # 일부만
    python -m context.snapshot --update         # baseline 갱신

## 스냅샷 4층 구조

절대 시각은 저장하지 않는다. 발사 타이밍이 1프레임(0.0167s) 밀리는 건 노이즈지만
"버프가 적용된 다음에 대미지가 계산되는가" 하는 **순서**는 신호이기 때문이다.

  L1 수치   — 캐릭터별 딜, 스킬별 딜·히트수, hit_tag 분포, 크리 수
  L2 발동   — 버프/인스턴트 이름별 발동 횟수와 대상 집합
  L3 순서   — 사이클별 이벤트 순서열 (시각 없음). 히트는 구간 집계로 압축
  L4 위상   — 사이클 간격(0.05초), 버프 발동 → 대상의 다음 히트까지 프레임 수 분포

자세한 사용법·diff 읽는 법은 context/HARNESS.md 참고.
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
from context import spec

ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = Path(__file__).resolve().parent / "baseline"

DT = 1.0 / 60.0  # 시뮬레이터 프레임 간격 (timeline.py와 동일)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


# ── 스쿼드 정의 ────────────────────────────────────────────────────────────
# 캐릭터 dict는 이름만 주면 `context/spec.py`가 채운다 —
# 기본 육성 스펙(장비 옵션은 오버로드 레벨 10의 우월코드 4줄·공격력 2줄·최대장탄 2줄,
# 컨트롤 없음 — 수치의 정본은 `spec.overload()`다)에
# 캐릭터별 기본 레이어(`data/char_defaults.json`)를 얹은 값이다.
# 아래 `chars`는 **그 스쿼드에서만** 다른 것을 적는 자리다.
#
# 새 스쿼드 추가 → 여기에 항목 추가 후 `--update --squad <이름>` 으로 baseline 생성.

SQUADS: dict[str, dict] = {
    "스쿼드1": {
        "members": ["리틀 머메이드", "크라운", "라피 : 레드 후드", "미하라 : 본딩 체인", "헬름"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "스쿼드2": {
        # 리틀 머메이드 버스트 미사용
        "members": ["츠바이", "나유타", "프리바티", "스노우 화이트 : 헤비암즈", "리틀 머메이드"],
        "config": {"no_burst_char": "리틀 머메이드", "first_burst_time": 3.0},
        "seed": 42,
    },
    "스쿼드3": {
        # = 실전 "작열샷건덱" (작열 약점 솔로레이드). 솔린 : 프로스트 티켓 버스트 미사용.
        # 작열 약점 적을 두는 유일한 초기 지그 — 아르카나 : 포츈 메이트·드레이크가 속성 유리를 받는다.
        "members": ["토브", "아르카나 : 포츈 메이트", "도로시 : 세렌디피티", "드레이크", "솔린 : 프로스트 티켓"],
        "config": {"no_burst_char": "솔린 : 프로스트 티켓", "first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "스쿼드4": {
        "members": ["목단", "마스트 : 로망틱 메이드", "홍련 : 흑영", "리버렐리오", "앵커 : 이노센트 메이드"],
        "chars": {
            # 택티컬 베어 큐브(탄충) = 유일한 instant 큐브. 다른 큐브는 전부 battle_start
            # 상시 버프라, 이 자리가 빠지면 `_make_cube_effects`의 instant 경로를
            # 어느 baseline도 밟지 않는다. 홍련 : 흑영은 실전에서도 탄충을 낀다.
            #
            # 이 스쿼드에서는 발동 32회가 잔탄 하한만 3 → 9로 올리고 **딜은 안 바뀐다** —
            # 홍련이 탄창을 비우는 일이 없어 재장전 시점이 그대로이기 때문이다.
            # 딜까지 움직이는 쪽은 `레이드_볼륨`이 덮는다(거기선 잔탄이 0을 찍는다).
            "홍련 : 흑영": {"cube": {"name": "택티컬 베어 큐브", "level": 15}},
        },
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "스쿼드5": {
        "members": ["아니스 : 스타", "아르카나", "이사벨", "신데렐라", "크라운"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },

    # ── 실전 레이드 조합 기반 (enikk.app 솔로레이드 시즌 30~39, 53,150 파스) ──
    # 미커버 31명을 덮기 위한 스쿼드. 조연은 임의 지그가 아니라 실제 상위 조합에서 가져왔다.
    #
    # 멤버 순서는 버스트 사용 순서다 — BurstController가 "스쿼드 입력 순서"를 우선순위로
    # 쓰므로, 커버 대상은 반드시 자기 단계(B1/B2/B3) 안에서 맨 앞에 둔다.
    # 뒤로 밀리면 같은 단계의 다른 캐릭터에게 선점당해 버스트를 아예 못 쓴다.
    #
    # 레드 후드(burst_stage "A")는 단계를 고정하지 않는다. 고정하면
    # 라피 : 레드 후드의 no_burst1_ally 조건이 깨져 라피가 1버로 전환되지 않는다.

    "레이드_미하라에이다": {
        # 커버: 에이다, D : 킬러 와이프, 그레이브, 미하라 : 본딩 체인, 미란다
        # D : 킬러 와이프는 버쿨감 용도로만 쓰이고 버스트는 미란다가 담당한다.
        "members": ["미란다", "그레이브", "에이다", "미하라 : 본딩 체인", "D : 킬러 와이프"],
        "config": {"no_burst_char": "D : 킬러 와이프", "first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_레드후드퀀시": {
        # 커버: 민트, 프리카, 퀀시 : 이스케이프 퀸, 레드 후드
        # 팀에 고정 1버가 없어 라피 : 레드 후드가 1버로 전환된다. 라피를 레드 후드보다
        # 앞에 둬야 첫 사이클을 레드 후드가 1·2·3단계 독식하지 않는다.
        "members": ["라피 : 레드 후드", "레드 후드", "프리카", "민트", "퀀시 : 이스케이프 퀸"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_아스카루드밀라": {
        # 커버: 아스카 : WILLE, 루드밀라 : 윈터 오너, 나가
        "members": ["리틀 머메이드", "나가", "크라운", "아스카 : WILLE", "루드밀라 : 윈터 오너"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_헬름아쿠아스노우": {
        # 커버: 헬름 : 아쿠아마린, 스노우 화이트, 에이드 : 에이전트 바니
        "members": ["미란다", "헬름 : 아쿠아마린", "에이드 : 에이전트 바니", "스노우 화이트", "에이다"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_앨리스브래디": {
        # 커버: 앨리스, 브래디
        "members": ["아니스 : 스타", "앵커 : 이노센트 메이드", "마스트 : 로망틱 메이드", "앨리스", "브래디"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_네온벨벳": {
        # 커버: 네온 : 비전 아이, 벨벳
        "members": ["리틀 머메이드", "벨벳", "나유타", "네온 : 비전 아이", "리버렐리오"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_이브레이븐": {
        # 커버: 이브, 레이븐
        "members": ["목단", "민트", "프리카", "이브", "레이븐"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_소다": {
        # 커버: 소다 : 트윙클링 바니
        # 버쿨감 보유자가 없는 B3 3명 구성 — 사이클 20초가 정상이다.
        "members": ["토브", "나유타", "소다 : 트윙클링 바니", "도로시 : 세렌디피티", "드레이크"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_일레그": {
        # 커버: 일레그 : 붐 앤 쇼크
        "members": ["아니스 : 스타", "크라운", "일레그 : 붐 앤 쇼크", "헬름", "루드밀라 : 윈터 오너"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_델타": {
        # 커버: 델타 : 닌자 시프
        "members": ["리틀 머메이드", "델타 : 닌자 시프", "크라운", "아스카 : WILLE", "라피 : 레드 후드"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_아니스서머메이든": {
        # 커버: 아니스 : 스파클링 서머, 메이든 : 아이스 로즈
        "members": ["목단", "에이드 : 에이전트 바니", "아니스 : 스파클링 서머", "메이든 : 아이스 로즈", "프리바티"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_루주": {
        # 커버: 루주
        "members": ["루주", "크라운", "마스트 : 로망틱 메이드", "신데렐라", "메이든 : 아이스 로즈"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    "레이드_브리드디젤": {
        # 커버: 브리드 : 사일런트 트랙, 디젤 : 윈터 스위츠
        # 작열 약점 솔로레이드 실전 운용 그대로 — 멤버 순서와 마스트 운용이 실전 기준이다.
        #   · 디젤은 스노우 화이트 : 헤비암즈보다 **뒤**에 둔다 (B3 두 자리를 격 사이클로 나눠 쓴다)
        #   · 마스트 : 로망틱 메이드는 **3의 배수 사이클에만** 버스트한다. 종전에는 20엔트리
        #     `burst_sequence`를 손으로 박아 넣었는데, 이제 캐릭터별 기본 레이어의
        #     버스트 패턴(`every:3`)이 같은 일을 한다 — 도입 시 이 스쿼드가 무변동임을
        #     확인했다(그게 곧 패턴 컴파일러의 검증이었다).
        "members": ["목단", "브리드 : 사일런트 트랙", "스노우 화이트 : 헤비암즈", "디젤 : 윈터 스위츠", "마스트 : 로망틱 메이드"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "레이드_볼륨": {
        # 커버: 볼륨
        "members": ["볼륨", "앵커 : 이노센트 메이드", "마스트 : 로망틱 메이드", "리버렐리오", "홍련 : 흑영"],
        "chars": {
            # 스쿼드4와 같은 이유 — 홍련 : 흑영은 탄충 큐브로 굴린다.
            "홍련 : 흑영": {"cube": {"name": "택티컬 베어 큐브", "level": 15}},
        },
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },
    # ── 작열 약점 솔로레이드 실전 조합 ──────────────────────────────────────
    # `enemy.code: "풍압"`이 작열 약점 보스다 (작열 → 풍압 우월, damage._CODE_ADVANTAGE).
    # 위 스쿼드들은 전부 무속성 적이라 is_element_match 경로가 스냅샷에 들어오지 않는다.
    # 같은 계열: `스쿼드3`(작열샷건덱) · `레이드_브리드디젤`(브브마디젤덱).
    # 풍압이 아닌 적은 `지그_라피1버전격` 하나뿐이다 — 로스터 코드가 아니라
    # `element_code_override`로 성립하는 우월 코드를 덮는 유일한 자리다.

    "레이드_라피앨리스": {
        # 커버: 앨리스(작열 SR) 속성 유리 · 라피 : 레드 후드
        #      + 컨트롤 두 종의 병행 — 앨리스 톡톡이 · 라피 장전컨(정책 A).
        # B3 셋 중 프리바티는 맨 뒤라 버스트를 쓰지 않는다 (`스쿼드2`가 커버).
        #
        # 실전 조작 순서는 "라피 장전컨이 우선, 아주 짧게 끝나므로 남은 시간은 앨리스 톡톡이"다.
        # 계산기는 동시 컨트롤 1명 제약을 검사하지 않으므로(CONTROL.md §미구현) 둘을 그냥 켠다 —
        # 라피를 조작하는 짧은 순간에 앨리스 톡톡이가 멈추는 손실만큼 낙관적인 상한이다.
        "members": ["리틀 머메이드", "크라운", "라피 : 레드 후드", "앨리스", "프리바티"],
        "chars": {
            # 앨리스의 톡톡이·차지속도 2줄(9.26%)은 캐릭터별 기본 레이어가 준다
            # (data/char_defaults.json) — 여기 다시 적지 않는다.
            #
            # 비버스트에 재장전이 걸리지 않도록 풀버스트가 끝나기 전에 미리 채운다.
            "라피 : 레드 후드": {
                "control": {"reload": {"policy": "before_fb_end", "lead": 0.3}},
            },
        },
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "레이드_작열짬": {
        # 커버: 레이(작열 MG)·모더니아(작열 MG) — 둘 다 유일한 커버 스쿼드다.
        # + 장전컨 정책 B(`into_fb`).
        # 모더니아는 B3 셋 중 맨 뒤라 180초 동안 **버스트를 한 번도 쓰지 않는다** —
        # 실전 운용 그대로다(섬멸 모드는 무기계수를 낮추고 풀버스트를 5초 늘려
        # 사이클 간격을 밀어낸다). 그래서 `섬멸 모드` 자체는 이 스쿼드가 커버하지 않는다.
        #
        # 앨리스 한 명만 조작한다 — 톡톡이 + 풀버스트 시작 전에 재장전을 시작해
        # 시작 시점에 70%쯤 진행된 상태로 만든다. margin은 "완료가 풀버스트 시작 후
        # 몇 초 뒤인가"이므로 남은 30%에 해당하는 실초를 준다: 이 스쿼드의 앨리스
        # 재장전 실측이 1.42초(기본 2.0 + 재장 큐브·버프)라 0.3 × 1.42 ≒ 0.43.
        # 실측 진행률 68.2% (margin 0.6이면 56.5%로 모자란다).
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

    "지그_리타": {
        # 커버: 리타 — 시즌 30~39 실전 조합에 한 번도 등장하지 않아 템플릿 지그를 쓴다.
        "members": ["리타", "크라운", "test_B3", "스노우 화이트 : 헤비암즈"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },

    "지그_리코리코": {
        # 커버: 타키나·치사토(CE008) — 둘 다 유일한 커버 스쿼드다. 나머지 셋은 기존 커버.
        # 방무(`armor_break_enabled` + `armor_break_dmg_pct`) 조합을 통째로 지키는 자리다:
        # 타키나가 아군 전체에 `armor_break_dmg_pct` +140.49를 뿌리고(스킬2, 15초 주기)
        # 치사토가 상시 방무 히트로 그걸 받아먹는다. 방무 경로가 깨지면 여기가 먼저 운다.
        #
        # 타키나 스킬2는 사이클(12.53s)이 아니라 **15초 고정 주기**라 위상이 계속 어긋난다
        # — 사이클과 무관한 `every:Ns` 트리거를 가진 유일한 하네스 스쿼드이기도 하다.
        # 상세는 context/scenarios/타키나.md.
        #
        # 에이다는 B3 셋 중 맨 뒤라 180초 동안 **버스트를 한 번도 쓰지 않는다**
        # (레이드_작열짬의 모더니아와 같은 패턴). 에이다 버스트는 레이드_미하라에이다·
        # 컨트롤_에이다미하라가 이미 덮는다.
        "members": ["목단", "타키나", "치사토", "스노우 화이트 : 헤비암즈", "에이다"],
        "config": {"first_burst_time": 3.0},
        "seed": 42,
    },

    "지그_라피1버전격": {
        # 커버 둘. 다른 스쿼드가 하나도 덮지 않는 경로다.
        #
        # ① `element_code_override` — 라피 : 레드 후드 `부착형 유탄`은 본인 코드(작열)와
        #    무관하게 **전격 적에게** 우월 코드를 성립시킨다. 아래 `enemy.code: "전격"`이
        #    그 유일한 스위치다. 코드 상성이 붙은 다른 스쿼드 6개는 전부 풍압이라,
        #    이 스쿼드가 빠지면 override 경로는 어느 baseline도 밟지 않는다.
        # ② **1버스트 라피** — 기본 B1 아군이 없어야 `no_burst1_ally` → `전투 보조`가
        #    걸린다. 스쿼드1·레이드_라피앨리스는 둘 다 리틀 머메이드가 있어 라피가 B3다.
        #    그래서 **B1 캐릭터를 넣으면 이 스쿼드의 의미가 사라진다.**
        #
        # 실전 조합이 아니라 GAMEPLAY.md §표준 테스트 스쿼드 템플릿이다 —
        # 철갑 약점(=전격 적) 시즌의 상위 조합 자료가 없어 지그로 둔다.
        # 라피 자신의 버쿨감(전황 파악 7.48 + 계승되는 힘 20)으로 40초 쿨이 매 사이클
        # 회복돼 12.533s 균일 사이클이 나온다 — 7.48 계열의 정상 간격열이다.
        # 크라운·test_B3(철갑)도 전격에 우월이라 ⑦가 넷 중 셋에 걸린다.
        "members": ["라피 : 레드 후드", "크라운", "test_B3", "스노우 화이트 : 헤비암즈"],
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "전격"},
        "seed": 42,
    },

    # ── 컨트롤 스쿼드 ─────────────────────────────────────────────────────
    # 캐릭터가 아니라 **컨트롤 정책**을 커버한다. 다른 스쿼드는 전부 컨트롤이 꺼져 있어
    # 정책 코드가 스냅샷에 전혀 들어오지 않는다.

    "컨트롤_미란다미하라": {
        # 커버: 버스트 엄폐컨 own_full_burst (context/CONTROL.md §버스트 엄폐컨)
        # 미하라가 미란다 제외 공격력 1위여야 `웨이크업! 4`(크리확률 1발)를 받는다.
        # 기본 스펙으로는 헬름이 1위라 엄폐가 헛돌므로 장비 공격력으로 순위를 뒤집는다.
        # 미하라는 B3 두 명 중 뒤라 격 사이클로 버스트한다 — 정책의 양쪽 분기
        # (본인 버스트 사이클엔 엄폐 / 아닌 사이클엔 사격)가 한 스냅샷에 들어온다.
        # 엄폐컨 자체는 미란다가 있으면 레이어가 붙인다(`_control_rules`) — 여기 안 적는다.
        "members": ["미란다", "브리드 : 사일런트 트랙", "헬름", "루주", "미하라 : 본딩 체인"],
        "chars": {
            "미하라 : 본딩 체인": {"equip_skills": {"atk_pct": 30.0}},
        },
        "config": {"first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
    "컨트롤_에이다미하라": {
        # 커버: 홀드컨 own_full_burst (context/CONTROL.md §홀드)
        # `레이드_미하라에이다`와 같은 조합·같은 컨트롤이고 우월 코드만 다르다. 에이다의
        # `은밀한 지원`이 직전에 버스트를 쓴 B3의 공격력을 올려 주므로 미란다
        # `웨이크업! 4`(크리확률 1발)가 사이클마다 에이다↔미하라로 번갈아 붙는다 —
        # 에이다는 홀드, 미하라는 엄폐로 아낀다. 둘 다 미란다가 있으면 레이어가 붙인다
        # (`_control_rules`) — 컨트롤을 끈 대조군은 `context/sim.py --auto`로 본다.
        "members": ["미란다", "그레이브", "에이다", "미하라 : 본딩 체인", "D : 킬러 와이프"],
        "config": {"no_burst_char": "D : 킬러 와이프", "first_burst_time": 3.0},
        "enemy": {"code": "풍압"},
        "seed": 42,
    },
}


def build_squad(members: list[str], chars: dict[str, dict] | None = None) -> list[dict]:
    """이름 목록 → simulate()에 넘길 캐릭터 dict 목록.

    스펙 합성은 `context/spec.py`가 한다 — 기본 스펙 → 캐릭터별 기본 레이어
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
