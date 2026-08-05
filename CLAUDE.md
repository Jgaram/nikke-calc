# NIKKE Damage Calculator

Building a 5-member squad DPS simulator for **승리의 여신: 니케 (NIKKE)**. Combines scraped skill data, hand-collected base stat tables, and the DealForm damage formula to produce per-hit damage events on a real-time combat timeline.

## Directory layout
- `data/` — all JSON data files: skill data, weapon mechanics, base stat tables
  - `parsed_nikke.json` — 캐릭터별 무기 스펙, 버스트 단계, 쿨다운
  - `parsed_skills.json` — 캐릭터별 스킬 효과 구조화 JSON
  - `weapon_mechanics.json` — 무기 종류별 발사 속도, 차징 시간, 펠릿 수
  - `base_stat_tables/` — 레벨·친밀도·콘솔·장비·큐브·컬렉션 스탯 테이블
- `calculator/` — Python modules
  - `base_stat.py` — 캐릭터 최종 ATK/DEF/HP 계산
  - `damage.py` — DealForm ①~⑦ 단타 데미지 공식
  - `buff_manager.py` — 버프 등록·활성화·만료·집계 (이벤트 기반)
  - `timeline.py` — 1/60초 프레임 단위 전투 시뮬레이터 메인 루프
  - `sim_result.py` — 결과 자료구조 및 분석 함수 (`HitEvent`, `SimResult`, `analyze_damage()` 등)
- `scraper/` — CDN 데이터 수집 (브라우저 미사용)
  - `cdn_fetch.py` — blablalink CDN 수집기(메인). `python scraper/cdn_fetch.py` / `--check` / `--ids`
  - `cdn_path.py` — 평문 경로 → 난독화 CDN URL 변환 (프론트엔드 `obfuscatedPath()` 재현)
  - `parse_nikke.py` — 수집 원시 데이터 → `parsed_nikke.json` 변환
  - `nikke_scraped.json` — 수집 원시 데이터. **파싱 입력의 유일한 정본** (`data/`에 사본을 두지 않는다)
- `context/` — working documents (read when relevant)
  - `context/sim.py` — 단발 시뮬 CLI (Claude 전용): 파일 수정 없이 임의 스쿼드 실행. `python -m context.sim "A,B,C" --view summary`. 컨트롤은 `--tap "이름:4.0"` / `--reload-ctrl "이름:into_fb"`
  - `context/snapshot.py` — 결정론적 회귀 하네스 (Claude 전용). `python -m context.snapshot`
  - `context/baseline/` — 하네스 golden 스냅샷 JSON. 손으로 편집하지 않는다
  - `context/test.py` — 대화형 셀 디버그 도구 (Claude 전용). `python -m context.test`
  - `context/doclint.py` — 문서 정합 린터 (Claude 전용). 문서가 코드·데이터를 재서술한 부분만 기계 검사 — A 미등록 키 · B 로스터 · C 구현상태↔코드 · D 선언된 사본↔정본 · E 문서가 지목한 파일·함수 실재. `python -m context.doclint` / `--usage`
  - `context/xlcalc.py` — 참조 엑셀 계산기 구동 CLI (Claude 전용). `python -m context.xlcalc --list` / `"딜러,서포터..."` / `--view cols|buff`
  - `context/xlcalc.xlsx` — 유저 손계산 엑셀의 계산 전용 정리본. 시뮬 교차 검증 기준선. **직접 편집하지 않는다**
- `ui/` — Streamlit UI 모듈 (진입점: `app.py`)
- `.claude/skills/<name>/` — 스킬(슬래시 커맨드). `SKILL.md`가 절차, 같은 폴더의 나머지 파일은 **그 스킬 전용** 문서·도구.
  유저가 `/이름`으로 부르기도 하지만, 보통은 에이전트가 다음 단계로 제안하고 유저가 승인해 실행된다
  - `report/` — `SKILL.md` · `REPORT.md`(스펙 정본) · `report.py`(러너) · `report_html.py`(렌더러)
  - `char-scrape/` — `SKILL.md` · `SCRAPER.md`
  - `char-add/` — `SKILL.md`(단계 게이트·진입점) · `SCENARIO.md`(단계 1·3) · `PARSE.md`(단계 2) · `IMPL.md`(단계 4)
  - `commit/` — `SKILL.md`만

## Context files

`context/*.md`는 여러 작업에서 공유하는 문서다. **한 스킬에서만 쓰는 문서는 여기 두지 않고
해당 스킬 폴더에 둔다** (`REPORT.md` → `.claude/skills/report/`, `SCRAPER.md` → `.claude/skills/char-scrape/`).

| File | 내용 |
|------|------|
| `context/ALIASES.md` | 캐릭터 별칭 → 정식 명칭. **유저가 캐릭터를 이름으로 지목하면 무조건 먼저 읽는다** (아래 §캐릭터 이름) |
| `context/PARSING.md` | 스킬 파싱 규칙·스키마. **§1~8 파싱 시 통독, 나머지 필요한 절만** (약 890줄). 텍스트→키 매핑의 정본 |
| `context/PARSING-CHARS.md` | 캐릭터별 데이터 — `## 현황 목록`(완료/진행 중/예정)·`## 캐릭터별 예외`. PARSING.md에서 분리(캐릭터마다 증가하는 데이터) |
| `context/IMPL-STATUS.md` | stat/trigger/target 마스터 테이블(**키 로스터·구현상태의 정본**), 신규 stat 추가 체크리스트. **참조 테이블 — 필요한 절만** (약 580줄) |
| `context/CALCULATOR.md` | calculator 모듈 구조·데이터 흐름 |
| `context/CONTROL.md` | 컨트롤(톡톡이·장전컨) — 메커니즘 수치·계산기 모델·설정 스키마·적용 대상. **컨트롤의 정본** |
| `.claude/skills/char-scrape/SCRAPER.md` | 스크래퍼 실행·데이터 갱신·수동 관리 필드 |
| `context/DATA_VERIFY.md` | 인게임 수치 검증·추정값 |
| `context/HARNESS.md` | 회귀 하네스. 사용법·캐릭터 스펙·baseline 갱신 기준·diff 읽는 법·스쿼드 커버리지. **회귀 운영 기준의 정본** |
| `context/UI.md` | UI 화면 구성·표시 규칙·이미지 관리 |
| `.claude/skills/report/REPORT.md` | 딜량 보고서 — 케이스 스펙 형식·실행법·표시 규칙 |
| `context/XLCALC.md` | 참조 엑셀 계산기 — 시트 구성·계산 가정·우리 계산과의 차이·원본 대비 변경 이력. **교차 검증 기준선의 정본** |
| `context/scenarios/<name>.md` | 두 종류가 섞여 있다 — ① 캐릭터별 버스트 사이클 시나리오·검증 체크리스트(`char-add` 단계 4·버그 수정 시 참조, 있을 때만) ② 메커니즘 조사 기록(`MG 예열`·`명중률 탄착군`·`엄폐 자동재장전`). ②는 `DATA_VERIFY.md`가 참조한다 |
| `context/GAMEPLAY.md` | 게임 메커니즘 기준. **필요한 절만** 읽는다. 전체 통독은 하지 않는다 (아래 표 참조) |

`GAMEPLAY.md`는 절 단위로 찾아 읽는다. `## 스쿼드 구성`은 175줄이라 통째로 읽지 않는다.

| 하려는 일 | 읽을 절 |
|---|---|
| 스쿼드 편성·멤버 순서 | `§스쿼드 구성 §유효한 스쿼드의 조건` + `§버스트 사용 순서와 배치` |
| 버스트 주기·사이클 간격이 이상할 때 | `§버스트 쿨타임 감소`(수치·패턴·예외) + `§풀버스트 사이클 §사이클 주기의 구성` |
| 실전 조합 없는 캐릭터의 지그 | `§스쿼드 구성 §표준 테스트 스쿼드` |
| 스킬 파싱 | `§트리거 발동 의미` |
| 컨트롤(톡톡이·장전컨) | `§컨트롤` 요약만. 상세는 `context/CONTROL.md` |

**PARSING ↔ IMPL-STATUS 정본 분리** (같은 규칙을 두 곳에 적지 않는다):

| 하려는 일 | 읽을 곳 |
|---|---|
| 신규 캐릭 스킬 파싱 | `PARSING.md §1~8` 통독 (`char-add` 단계 2가 지시) |
| timing/condition/target — 한국어 텍스트 → 키 | `PARSING.md §4`(트리거)·`§5`(타겟) 매핑 |
| stat — 텍스트 → 키가 헷갈릴 때 | `PARSING.md §6` 매핑 단서 |
| **키 로스터·구현상태**(어떤 키가 있나, 구현됐나) | `IMPL-STATUS.md` 마스터 테이블 (**정본**) |
| 파싱 예외 패턴(DoT·스택·게이지·무기변경 등) | `PARSING.md §7` |
| 캐릭터별 예외·파싱 현황 | `PARSING-CHARS.md` |

새 키는 IMPL-STATUS 마스터에 등록(정본), 텍스트 패턴이 새로우면 PARSING §4~6에 매핑 단서만 추가 — 양쪽 동시 편집 아님. 정합은 `python -m context.doclint`로 확인.

Do not proactively re-read context files unless the current task needs them.

## 캐릭터 이름 — 별칭은 항상 먼저 변환한다

위의 "필요할 때만 읽는다"에 대한 **예외**다. 유저는 거의 항상 별칭으로 말한다
(`마스트`·`돌니스`·`흑련`). 유저 입력에 캐릭터 이름이 하나라도 있으면 — 시뮬을 돌리든,
스킬을 설명하든, 그냥 질문에 답하든 — **먼저 `context/ALIASES.md`를 읽어 정식 명칭으로 바꾼다.**
`/report`뿐 아니라 모든 작업에 적용된다.

- 표에 없는 축약어는 **추측하지 말고 유저에게 묻는다.** Claude가 별칭을 지어내지 않는다.
- 코드·데이터·답변에는 정식 명칭만 쓴다. 별칭은 입력 해석 전용이다.
- **변환을 빠뜨리면 조용히 틀린다.** `마스트`·`앵커` 같은 별칭은 부제 없는 원본 캐릭터로도
  실재해서 `parsed_nikke.json`에는 있고 `parsed_skills.json`에는 없다 — 스탯·무기는 정상이고
  스킬만 0개인 니케로 계산된다. `simulate()`가 이 경우를 에러로 끊지만
  (`timeline._check_names`), 에러를 보고 나서 고치는 것보다 처음부터 변환하는 게 맞다.
- **`char-add`로 새로 등록하는 캐릭터는 아직 별칭이 없는 게 정상이다.** 이때는 정식 명칭을
  그대로 쓰면 된다. 유저가 나중에 별칭을 알려주면 그때 표에 추가한다(유저가 채우는 표다).
  파싱 전 신캐를 스탯만 돌려볼 때는 `--allow-unparsed`.

## 워크플로우

**UI:** `run.bat` 더블클릭 또는 `streamlit run app.py` → http://localhost:8501. 구현의 최종 확인은 여기서 한다.

### 신규 캐릭터 추가

스킬 **둘**로 나뉜다 — 데이터 수집은 캐릭터 등록과 무관하게도 돌아가므로 분리하고,
등록 절차는 시나리오 작성이 파싱 전후로 두 번 들어가는 하나의 흐름이라 합쳐 둔다.

| 스킬 | 내용 |
|------|------|
| `char-scrape` | 스크래퍼 실행, `parsed_nikke.json` 갱신, SR/RL `weapon_delays.json` 처리 |
| `char-add` | 단계 1 시나리오 초안 → 2 스킬 파싱 → 3 시나리오 보강 → 4 계산기 구현·검증 |

`char-add`는 진입점을 스스로 판별한다 (시나리오·`parsed_skills.json` 상태로 판단) —
이미 스크래핑된 캐릭터면 단계 1부터, 파싱까지 끝났으면 단계 3부터 시작한다.
**각 단계 완료 후 멈추고 다음 단계 진행 여부를 유저에게 묻는다.**

### 데이터 갱신 (신캐 출시·기존 캐릭 스킬 업데이트)

유저가 "신캐 나왔어" / "OO 스킬 바뀐 것 같아"처럼 **게임 데이터가 바뀌었다**고 하면,
이름·ID를 몰라도 다음으로 반영한다 (상세는 `.claude/skills/char-scrape/SCRAPER.md` `§신캐 출시 / 기존 캐릭 스킬 업데이트`):

```bash
python scraper/cdn_fetch.py --check   # 무엇이 신규/변경인지 확인 (쓰기 없음, 수 초)
python scraper/cdn_fetch.py           # 반영 (전량 재수집 + 누락 이미지)
```

스킬 텍스트가 바뀐 캐릭터는 이후 `char-add`로 재파싱·재구현해야 계산기에 반영된다
(`parsed_skills.json`은 자동 갱신되지 않음).

### 기타 스킬

| 스킬 | 내용 |
|------|------|
| `/report` | 조합·육성·버스트 운용 비교 HTML 딜량 보고서 생성 |
| `/commit` | 변경 사항 그룹핑 후 커밋 |

### 계산기 코드를 수정했다면

`python -m context.snapshot` 으로 회귀 확인. 상세는 `context/HARNESS.md`.
`python -m context.doclint` 로 문서 정합 확인 (IMPL-STATUS 구현상태가 코드와 어긋나면 잡힌다).

### 문서를 고칠 때 — 이중 진실을 만들지 않는다

문서는 성격에 따라 취급이 다르다.

| 성격 | 예 | 원본 | 검증 |
|---|---|---|---|
| **명세** — 게임 경험이 원천, 코드가 하류 | GAMEPLAY · DATA_VERIFY · CONTROL · scenarios | 문서가 원본 | 인게임 확인 · scenarios 체크리스트 · 하네스 |
| **결정·이력** — 코드 어디에도 없는 판단 | HARNESS 운영 규칙·이력 · PARSING 매핑 규칙 · XLCALC 차이 | 문서가 유일본 | 대조 대상 없음 |
| **재서술** — 코드·데이터를 보면 답이 나오는 것 | IMPL-STATUS 구현상태·키 로스터 | 코드/데이터가 원본 | **doclint 필수** |

**재서술은 부채다.** 새로 쓰기 전에 지울 수 있는지 먼저 본다. 지울 수 없으면
`context/doclint.py`가 검사할 수 있는 형태로 만든다 — 사람이 눈으로 대조하는 문서 규칙은 만들지 않는다.

같은 내용을 두 문서에 두어야 한다면(콜드 세션이 한쪽만 읽고도 판정해야 할 때) 사본 쪽에
`> 이 표는 **사본**이다. 정본은 X` 선언을 붙이고 `doclint.py`의 `MIRRORS`에 등록한다.
선언만 하고 등록하지 않으면 그때부터 갈라진다.

## Conflict resolution
If anything in the user's prompt — whether an instruction or factual information (e.g. file names, values, settings) — conflicts with the content of a relevant `context/*.md` file, stop and ask before proceeding. Quote both sides explicitly:
- **Prompt says:** `...`
- **context/xxx.md says:** `...`

Then ask which to follow, or whether the context file should be updated.

## Updating context files
`context/*.md` files may be updated either by Claude's judgment (when a decision warrants it) or by the user's request. In both cases: read the file first to understand its structure and existing content, then propose the change and ask for confirmation before applying it.
