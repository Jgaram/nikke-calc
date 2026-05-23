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
  - `nikke_scraped.json` — 크롤 원시 데이터
- `context/` — working documents (read when relevant)
  - `context/test.py` — 단일 캐릭터 디버그 도구 (Claude 전용): 스쿼드 구성 → 시뮬 실행 → 버스트 사이클·버프 스냅샷 확인. `python -m context.test`
  - `context/regression_test.py` — 회귀 테스트 (Claude 전용): 5개 스쿼드 기준값 검증. `python -m context.regression_test`
- `ui/` — Streamlit UI 모듈 (진입점: `app.py`)

## Context files

| File | 내용 |
|------|------|
| `context/PARSING.md` | 스킬 파싱 규칙·스키마·진행 현황 |
| `context/IMPL-STATUS.md` | stat/trigger/target 마스터 테이블, 회귀 테스트 운영 기준 |
| `context/CALCULATOR.md` | calculator 모듈 구조·데이터 흐름 |
| `context/SCRAPER.md` | 스크래퍼 실행·데이터 갱신·수동 관리 필드 |
| `context/DATA_VERIFY.md` | 인게임 수치 검증·추정값 |
| `context/DOC_GAPS.md` | 문서화 누락 항목 |
| `context/GIT.md` | 커밋·버전 관리 |
| `context/UI.md` | UI 화면 구성·표시 규칙·이미지 관리 |
| `context/GAMEPLAY.md` | 유저가 명시적으로 읽으라고 할 때만 읽는다 |

Do not proactively re-read context files unless the current task needs them.

## Conflict resolution
If anything in the user's prompt — whether an instruction or factual information (e.g. file names, values, settings) — conflicts with the content of a relevant `context/*.md` file, stop and ask before proceeding. Quote both sides explicitly:
- **Prompt says:** `...`
- **context/xxx.md says:** `...`

Then ask which to follow, or whether the context file should be updated.

## Updating context files
`context/*.md` files may be updated either by Claude's judgment (when a decision warrants it) or by the user's request. In both cases: read the file first to understand its structure and existing content, then propose the change and ask for confirmation before applying it.
