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
- `scraper/` — web scraping scripts
  - `nikke_scraper.py` — blablalink.com Playwright 크롤러
  - `parse_nikke.py` — 크롤 원시 데이터 → `parsed_nikke.json` 변환
  - `rescrape.py` — 특정 캐릭터 ID 재크롤
  - `extract_session.py` — 브라우저 로그인 세션(localStorage) 추출
  - `nikke_scraped.json` — 크롤 원시 데이터. **파싱 입력의 유일한 정본** (`data/`에 사본을 두지 않는다)
- `context/` — working documents (read when relevant)
  - `context/sim.py` — 단발 시뮬 CLI (Claude 전용): 파일 수정 없이 임의 스쿼드 실행. `python -m context.sim "A,B,C" --view summary`
  - `context/snapshot.py` — 결정론적 회귀 하네스 (Claude 전용). `python -m context.snapshot`
  - `context/baseline/` — 하네스 golden 스냅샷 JSON. 손으로 편집하지 않는다
  - `context/test.py` — 대화형 셀 디버그 도구 (Claude 전용). `python -m context.test`
- `ui/` — Streamlit UI 모듈 (진입점: `app.py`)

## Context files

| File | 내용 |
|------|------|
| `context/PARSING.md` | 스킬 파싱 규칙·스키마·진행 현황 |
| `context/IMPL-STATUS.md` | stat/trigger/target 마스터 테이블, 회귀 테스트 운영 기준 |
| `context/CALCULATOR.md` | calculator 모듈 구조·데이터 흐름 |
| `context/SCRAPER.md` | 스크래퍼 실행·데이터 갱신·수동 관리 필드 |
| `context/DATA_VERIFY.md` | 인게임 수치 검증·추정값 |
| `context/HARNESS.md` | 회귀 하네스 사용법·baseline 갱신 기준·diff 읽는 법 |
| `context/UI.md` | UI 화면 구성·표시 규칙·이미지 관리 |
| `context/scenarios/<name>.md` | 캐릭터별 버스트 사이클 시나리오·검증 체크리스트. `/char-impl`·`/bug-fix`가 참조 (있을 때만) |
| `context/GAMEPLAY.md` | 게임 메커니즘 기준. **필요한 절만** 읽는다 — 커맨드·문서가 특정 절을 지시하면 그 절을 읽고(예: 스쿼드 편성 → §스쿼드 구성, 파싱 → §트리거 발동 의미), 그 외에는 유저가 지시할 때만. 전체 통독은 하지 않는다 |

Do not proactively re-read context files unless the current task needs them.

## 워크플로우

**UI:** `run.bat` 더블클릭 또는 `streamlit run app.py` → http://localhost:8501. 구현의 최종 확인은 여기서 한다.

### 신규 캐릭터 추가

| 단계 | 슬래시 커맨드 | 내용 |
|------|--------------|------|
| 1 | `/char-scrape` | 스크래퍼 실행, `parsed_nikke.json` 갱신, SR/RL `weapon_delays.json` 처리 |
| 2 | `/char-scenario` | 원본 스킬 텍스트로 메카닉 이해·검증 스쿼드 결정 (초안 모드) |
| 3 | `/char-parse` | 스킬 파싱, 새 stat 확인 → 이후 `/char-scenario` 보강 모드 |
| 4 | `/char-impl` | 계산기 구현, 시나리오 체크리스트 검증 |

2단계부터 시작하는 경우가 많다. 각 단계 완료 후 다음 단계 진행 여부를 유저에게 묻는다.

### 기타 커맨드

| 슬래시 커맨드 | 내용 |
|--------------|------|
| `/bug-fix` | calculator 버그 수정 |
| `/docs-check` | 코드↔문서 불일치 확인 |
| `/commit` | 변경 사항 그룹핑 후 커밋 |

### 계산기 코드를 수정했다면

`python -m context.snapshot` 으로 회귀 확인. 상세는 `context/HARNESS.md`.

## Conflict resolution
If anything in the user's prompt — whether an instruction or factual information (e.g. file names, values, settings) — conflicts with the content of a relevant `context/*.md` file, stop and ask before proceeding. Quote both sides explicitly:
- **Prompt says:** `...`
- **context/xxx.md says:** `...`

Then ask which to follow, or whether the context file should be updated.

## Updating context files
`context/*.md` files may be updated either by Claude's judgment (when a decision warrants it) or by the user's request. In both cases: read the file first to understand its structure and existing content, then propose the change and ask for confirmation before applying it.
