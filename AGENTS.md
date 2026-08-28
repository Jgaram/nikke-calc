# NIKKE Damage Calculator

승리의 여신: 니케 5인 스쿼드의 실시간 전투와 DPS를 계산한다.

## Repository contracts

- `scraper/nikke_scraped.json`은 수집 원시 데이터의 유일한 정본이다. `data/`에 사본을 만들지 않는다.
- 출시 전 카드 이미지에서 옮겨 적은 스킬 원문은 `scraper/preview_skills.json`에만 둔다.
  스키마는 `nikke_scraped.json` 항목과 동일하되 `values`는 레벨 10만 갖는다.
  출시되면 스크랩 원문과 대조해 정식 등록하고 이 파일에서 제거한다 — `doclint.py`가 강제한다.
- 시뮬레이션용 character dict는 `runner/spec.py`에서만 만든다. `calculator/`는 이를 import하지 않는다.
- `profiles/`는 **개인 계정 육성 데이터**다. 통째로 gitignore이며 `scraper/.session_cookie`(계정
  접근권)와 함께 어떤 경우에도 커밋 대상에 올리지 않는다. 만드는 건 `profile-sync` skill뿐이다.
- **이 레포는 전투 계산까지다.** 「이 육성 상태면 스탯이 얼마이고 딜이 얼마인가」에
  답하고, 「거기까지 재화가 얼마 드는가·무엇을 더 키울까」에는 답하지 않는다. 후자는
  육성 의사결정이라 별도 웹앱 레포의 `cost/`·`overload/`가 맡는다.
  그쪽은 이 레포를 **읽기만** 하므로 의존은 한 방향이다 — 여기에 재화·투자 판단 코드를
  다시 들이지 않는다.
- `baseline/`의 golden snapshot은 손으로 편집하지 않는다.
- 공용 skill의 정본은 `.agent/skills/`다. `.claude/skills/`는 호환 진입점일 뿐이다.

## Landing changes

- **자기 작업에 PR을 열지 않는다.** 관리자가 유일한 작성자인 레포에서 자기 변경에 자기 PR을
  올리는 것은 형식만 남은 절차였다. `master`에 직접 push한다.
- **대신 push 전에 자체 검증을 통과시킨다. 이게 실질 게이트다.**

  ```bash
  python -m runner.doclint     # 문서·데이터 정합
  python -m runner.snapshot    # 딜 계산 회귀 (전체)
  ```

  둘 다 통과하지 못하면 push하지 않는다. CI는 push 후에도 같은 둘을 돌리지만, 그때는 이미
  들어간 뒤라 **막아 주는 게 아니라 알려 줄 뿐이다.**
- **CI가 붙이는 변동 표를 읽는다.** 통과 여부보다 그 표가 본체다 — 한 캐릭터를 고쳤는데
  모든 스쿼드가 흔들렸다면 초록불이어도 의도한 수정이 아니다.
- **보호 설정이 느슨한 것은 의도다.** `Require approvals`·`Require review from Code Owners`·
  `enforce_admins`·`Require a pull request before merging`은 전부 꺼 둔다 — 관리자가 유일한
  승인자라, 켜는 순간 그가 자리를 비운 동안 모든 변경이 멈춘다. `.github/CODEOWNERS`는 남이
  PR을 열었을 때 알림을 보내는 용도로만 남는다. GitHub UI가 권하더라도 조이지 않는다.

## Context routing

필요한 문서와 절만 읽고, 현재 작업과 무관한 context는 다시 읽지 않는다.

| 상황 | 정본 |
|---|---|
| 캐릭터 이름 해석 | `docs/ALIASES.md` |
| `calculator/` 데이터 흐름·기대값 모드 | `docs/CALCULATOR.md` |
| 스킬 파싱 규칙·현황·예외 | `docs/PARSING.md`, `docs/PARSING-CHARS.md` |
| stat/trigger/target 로스터와 구현 상태 | `docs/IMPL-STATUS.md` |
| 컨트롤 메커니즘 | `docs/CONTROL.md` |
| 인게임 검증값·추정값 | `docs/DATA_VERIFY.md` |
| 기본 스펙·회귀 운영 | `docs/HARNESS.md` |
| 게임 메커니즘 | `docs/GAMEPLAY.md`의 관련 절만 |
| 캐릭터별 사이클·검증 | 해당 `docs/scenarios/<정식 명칭>.md`가 있을 때만 |
| 무기·조작 등 캐릭터에 매이지 않는 메커니즘 조사 | 해당 `docs/mechanics/*.md`가 있을 때만 |
| 버스트 게이지·풀버스트 사이클 타이밍 | `docs/mechanics/버스트 게이지.md` |
| 외부 사이트에서 값을 캐 와야 할 때 | `docs/REFERENCES.md` |

`GAMEPLAY.md`는 전체 통독하지 않는다. 편성은 `§스쿼드 구성`, 사이클은
`§버스트 쿨타임 감소`·`§풀버스트 사이클`, 파싱은 `§트리거 발동 의미`,
컨트롤은 요약만 읽고 상세는 `CONTROL.md`를 쓴다.

## Character names

캐릭터 이름이 나오면 작업 종류와 관계없이 먼저 `docs/ALIASES.md`로 정식 명칭을 확인한다.
표에 없는 축약어는 추측하지 말고 묻는다. 코드·데이터·답변에는 정식 명칭만 쓴다.
신규 캐릭터 등록 중 아직 별칭이 없다면 입력된 정식 명칭을 그대로 쓴다.

## Simulation invariants

- 공통 기본 스펙과 캐릭터별 상시 차이는 `runner/spec.py`·`data/char_defaults.json`에 두고, 특정 스쿼드만의 차이는 호출부에 둔다.
- 기본 layer에서 벗어난 설정으로 실행했다면 결과와 함께 이탈 목록을 그대로 보고한다.
- `preview_skills.json`에 있는 캐릭터가 낀 시뮬·리포트 결과는 `[프리뷰 · 미검증]`을 함께 보고한다.
  스킬 레벨 10 외의 설정으로는 실행할 수 없다(값이 없어 조용히 0이 되는 대신 즉시 실패한다).
- 계산기 코드를 수정하면 `python -m runner.snapshot`과 `python -m runner.doclint`를 실행한다.

## Skills

| 요청 | skill |
|---|---|
| 신규 캐릭터 추가 또는 기존 캐릭터 재구현 | `char-add` — 수집부터 시나리오·파싱·구현·검증까지 전부 담당 |
| 출시 전 카드 이미지로 선행 등록 | `char-add` — 단계 0P로 진입 |
| 등록과 무관한 raw 게임 데이터 갱신만 | `char-scrape` |
| 조합·운용 비교, 육성 효율 등 **딜량 보고서** | 이 레포에 없다 — 별도 웹앱 레포가 맡는다 |
| **내 계정의 실제 육성 데이터를 받아오기** | `profile-sync` — 로그인 세션 필요, 산출물은 로컬 전용 |
| **내 실제 스펙으로 계산** | skill이 아니라 러너 옵션이다: `sim.py --profile <이름>` · 보고서 스펙의 `"profile"` 키 |
| 변경사항 커밋 | `commit` |

각 skill의 세부 절차와 gate는 해당 `SKILL.md`에서만 관리한다.

## Documentation

- 게임 명세·인게임 검증·시나리오는 문서가 정본이고, 구현 상태처럼 코드에서 판정 가능한 사실은 코드·데이터가 정본이다.
- 코드·데이터의 재서술은 가능한 한 쓰지 않는다. 불가피한 사본은 정본을 선언하고 `runner/doclint.py`의 `MIRRORS`에 등록한다.
- 사용자 요청과 관련 context가 충돌하면 양쪽을 인용하고 어느 쪽을 따를지 묻는다.
- `docs/*.md`를 바꾸기 전에는 해당 파일을 읽고 변경안을 제시해 확인받는다.
