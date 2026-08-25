# NIKKE Damage Calculator

승리의 여신: 니케 5인 스쿼드의 실시간 전투와 DPS를 계산한다.

## 다국어 — 화면에 닿는 글자는 전부 사전을 지난다

웹(`web/`)은 한국어·영어·일본어·중국어(번체)를 지원한다. **새로 쓰는 UI 문구도
처음부터 다국어 기반으로 쓴다** — 한국어만 박아 두고 나중에 옮기는 일은 없다.

- 사용자에게 보이는 문자열은 `T("한국어 원문")`으로 감싼다. `el(tag, cls, text)`는
  text를 알아서 `T()`에 통과시키지만, `.textContent =`·`.title =`·템플릿 리터럴·
  캔버스 `fillText`는 직접 감싸야 한다. 자리표시는 `{name}`이고 `T("…{n}명", { n })`처럼
  넘긴다(문장 조각을 `+`로 잇지 말고 한 문장을 한 키로 둔다 — 어순이 다른 언어가 있다).
- 니케·스킬·속성·클래스·기업·큐브·랩처 이름 같은 **데이터 키는 한국어 그대로** 두고,
  화면에 찍을 때만 `T()`를 지나게 한다. 비교·조회·저장에 번역값을 쓰지 않는다.
- 문구를 추가·수정했으면 `python web/i18n_tool.py extract` → `scaffold` → 사전
  `web/src/i18n/{en,ja,zh}.json`에 세 언어를 **직접 채운다** → `check`가 «빠짐 0»이어야
  끝이다. 브라우저 콘솔 `I18N.audit()`로 화면에 남은 한글을 확인한다.
- 게임 데이터 이름은 사람이 옮기지 않는다 — `python scraper/cdn_locale.py`가 CDN에서
  받아 `game.<lang>.json`을 굽는다. 구조와 이유는 `SITE.md` §8.

## Repository contracts

- `scraper/nikke_scraped.json`은 수집 원시 데이터의 유일한 정본이다. `data/`에 사본을 만들지 않는다.
- 출시 전 카드 이미지에서 옮겨 적은 스킬 원문은 `scraper/preview_skills.json`에만 둔다.
  스키마는 `nikke_scraped.json` 항목과 동일하되 `values`는 레벨 10만 갖는다.
  출시되면 스크랩 원문과 대조해 정식 등록하고 이 파일에서 제거한다 — `doclint.py`가 강제한다.
- 시뮬레이션용 character dict는 `context/spec.py`에서만 만든다. `calculator/`는 이를 import하지 않는다.
- `profiles/`는 **개인 계정 육성 데이터**다. 통째로 gitignore이며 `scraper/.session_cookie`(계정
  접근권)와 함께 어떤 경우에도 커밋 대상에 올리지 않는다. 만드는 건 `profile-sync` skill뿐이다.
- 오버로드 옵션 뽑기의 정본은 `overload/`다. `cost/tables.json`에는 **목표 정의**만 두고
  기대 소모량은 `overload.reach`가 그 자리에서 계산한다 — 값을 옮겨 적지 않는다.
- 재화 소모량의 정본은 `cost/tables.json`이고, 거기에는 **원시값만** 둔다. 키트 수급
  합계·희소가치·표준 배합은 코드가 그 원시값에서 계산하므로 어디에도 따로 적지 않는다.
  `data/cost_expected.json`은 `python -m cost.build`가 굽는 생성물이라 손으로 고치지 않는다.
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
| 재화 소모량·키트 수급·안 다루기로 한 재화 | `cost/README.md` |
| 오버로드 옵션 뽑기·잠금 전략·옵션 한계가치 | `overload/README.md` |
| 기본 스펙·회귀 운영 | `context/HARNESS.md` |
| 게임 메커니즘 | `context/GAMEPLAY.md`의 관련 절만 |
| 캐릭터별 사이클·검증 또는 메커니즘 조사 | 해당 `context/scenarios/*.md`가 있을 때만 |
| 웹앱 확장 작업의 결정·진행 상태 | `webapp-roadmap.md` (루트) |
| 사이트 구조·배포·밟은 지뢰 | `SITE.md` (루트) |
| 계산기 감사 결과 (의심 지점·검증 항목) | `context/CALC-AUDIT.md` |
| 사이트가 데이터를 어떻게 다루는가 | `PRIVACY.md` (루트) |
| 인게임 전투력(CP) 산식·역산 근거 | `context/scenarios/전투력 산식.md` |

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
- `preview_skills.json`에 있는 캐릭터가 낀 시뮬·리포트 결과는 `[프리뷰 · 미검증]`을 함께 보고한다.
  스킬 레벨 10 외의 설정으로는 실행할 수 없다(값이 없어 조용히 0이 되는 대신 즉시 실패한다).
- 계산기 코드를 수정하면 `python -m context.snapshot`과 `python -m context.doclint`를 실행한다.
- 웹 UI·CSS·HTTP 서버·터널·배포 설정만 수정한 작업은 계산기 코드 변경이 아니다.
  이 경우 30개 계산 회귀(`context.snapshot`)와 계산 문서 검사(`context.doclint`)를 실행하지 않는다.

## Skills

| 요청 | skill |
|---|---|
| 신규 캐릭터 추가 또는 기존 캐릭터 재구현 | `char-add` — 수집부터 시나리오·파싱·구현·검증까지 전부 담당 |
| 출시 전 카드 이미지로 선행 등록 | `char-add` — 단계 0P로 진입 |
| 등록과 무관한 raw 게임 데이터 갱신만 | `char-scrape` |
| 조합·운용 비교, enikk 대조, 중복 없는 솔로레이드 N덱 최적화·지정 편성 계산 | `report-squad` |
| 한 캐릭터의 육성 효율 (덱 고정, 변수 한 축씩) | `report-growth` |
| **내 계정의 실제 육성 데이터를 받아오기** | `profile-sync` — 로그인 세션 필요, 산출물은 로컬 전용 |
| **내 실제 스펙으로 계산** | skill이 아니라 러너 옵션이다: `sim.py --profile <이름>` · 보고서 스펙의 `"profile"` 키 |
| 변경사항 커밋 | `commit` |

각 skill의 세부 절차와 gate는 해당 `SKILL.md`에서만 관리한다.

## Documentation

- 게임 명세·인게임 검증·시나리오는 문서가 정본이고, 구현 상태처럼 코드에서 판정 가능한 사실은 코드·데이터가 정본이다.
- 코드·데이터의 재서술은 가능한 한 쓰지 않는다. 불가피한 사본은 정본을 선언하고 `context/doclint.py`의 `MIRRORS`에 등록한다.
- 사용자 요청과 관련 context가 충돌하면 양쪽을 인용하고 어느 쪽을 따를지 묻는다.
- `context/*.md`를 바꾸기 전에는 해당 파일을 읽고 변경안을 제시해 확인받는다.
