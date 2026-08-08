# NIKKE Damage Calculator

승리의 여신: 니케 5인 스쿼드의 실시간 전투와 DPS를 계산한다.

## Repository contracts

- `scraper/nikke_scraped.json`은 수집 원시 데이터의 유일한 정본이다. `data/`에 사본을 만들지 않는다.
- 시뮬레이션용 character dict는 `context/spec.py`에서만 만든다. `calculator/`는 이를 import하지 않는다.
- `context/baseline/`의 golden snapshot은 손으로 편집하지 않는다.
- 공용 skill의 정본은 `.agent/skills/`다. `.claude/skills/`는 호환 진입점일 뿐이다.

## Context routing

필요한 문서와 절만 읽고, 현재 작업과 무관한 context는 다시 읽지 않는다.

| 상황 | 정본 |
|---|---|
| 캐릭터 이름 해석 | `context/ALIASES.md` |
| 스킬 파싱 규칙·현황·예외 | `context/PARSING.md`, `context/PARSING-CHARS.md` |
| stat/trigger/target 로스터와 구현 상태 | `context/IMPL-STATUS.md` |
| 컨트롤 메커니즘 | `context/CONTROL.md` |
| 인게임 검증값·추정값 | `context/DATA_VERIFY.md` |
| 기본 스펙·회귀 운영 | `context/HARNESS.md` |
| 게임 메커니즘 | `context/GAMEPLAY.md`의 관련 절만 |
| 캐릭터별 사이클·검증 또는 메커니즘 조사 | 해당 `context/scenarios/*.md`가 있을 때만 |

`GAMEPLAY.md`는 전체 통독하지 않는다. 편성은 `§스쿼드 구성`, 사이클은
`§버스트 쿨타임 감소`·`§풀버스트 사이클`, 파싱은 `§트리거 발동 의미`,
컨트롤은 요약만 읽고 상세는 `CONTROL.md`를 쓴다.

## Character names

캐릭터 이름이 나오면 작업 종류와 관계없이 먼저 `context/ALIASES.md`로 정식 명칭을 확인한다.
표에 없는 축약어는 추측하지 말고 묻는다. 코드·데이터·답변에는 정식 명칭만 쓴다.
신규 캐릭터 등록 중 아직 별칭이 없다면 입력된 정식 명칭을 그대로 쓴다.

## Simulation invariants

- 공통 기본 스펙과 캐릭터별 상시 차이는 `context/spec.py`·`data/char_defaults.json`에 두고, 특정 스쿼드만의 차이는 호출부에 둔다.
- 기본 layer에서 벗어난 설정으로 실행했다면 결과와 함께 이탈 목록을 그대로 보고한다.
- 계산기 코드를 수정하면 `python -m context.snapshot`과 `python -m context.doclint`를 실행한다.

## Skills

| 요청 | skill |
|---|---|
| 신규 캐릭터 추가 또는 기존 캐릭터 재구현 | `char-add` — 수집부터 시나리오·파싱·구현·검증까지 전부 담당 |
| 등록과 무관한 raw 게임 데이터 갱신만 | `char-scrape` |
| 조합·육성·운용 비교 보고서 | `report` |
| enikk.app 실사용 조합 대조 | `enikk-report` |
| 변경사항 커밋 | `commit` |

각 skill의 세부 절차와 gate는 해당 `SKILL.md`에서만 관리한다.

## Documentation

- 게임 명세·인게임 검증·시나리오는 문서가 정본이고, 구현 상태처럼 코드에서 판정 가능한 사실은 코드·데이터가 정본이다.
- 코드·데이터의 재서술은 가능한 한 쓰지 않는다. 불가피한 사본은 정본을 선언하고 `context/doclint.py`의 `MIRRORS`에 등록한다.
- 사용자 요청과 관련 context가 충돌하면 양쪽을 인용하고 어느 쪽을 따를지 묻는다.
- `context/*.md`를 바꾸기 전에는 해당 파일을 읽고 변경안을 제시해 확인받는다.