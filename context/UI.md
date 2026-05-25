# UI — 시뮬레이터 디버깅 대시보드

**목적**: 인게임 동작↔시뮬레이터 출력 직접 비교.
**1차**: 메커니즘 검증 (버프 타이밍, 히트 이벤트) / **2차**: 조합 비교, DPS 최적화

---

## 실행

`run.bat` 더블클릭 또는 `streamlit run app.py`.
브라우저 `http://localhost:8501`. 코드 수정 후 **F5** (`Ctrl+Shift+R` 강제 새로고침).

`parsed_skills.json` / `parsed_nikke.json` 변경 시 `app.py`가 자동 감지해 관련 모듈 리로드.

---

## 화면 구성

**상단 expander — 스쿼드 구성** (`team_panel.py`)
결과 없으면 자동 펼침. 캐릭터 5명 + 개별 스탯(레벨·친밀도·콘솔·장비·큐브·컬렉션) + 시뮬 시간 + 버스트 설정. "▶ 시뮬 실행" 누르면 `simulate()` 호출 → `session_state["result"]`에 저장 후 `st.rerun()`.

**탭 1 — 개요** (`burst_panel.render_overview`)
- 대미지 분석: 캐릭터별 스택 막대 차트(스킬명 색상 구분), 버스트 사이클별 누적 딜량 차트

**탭 2 — 버스트별 히트 수** (`burst_panel.render_burst_hits` + `hit_panel.render_aggregate_only`)
- 버스트 타임라인: 풀버스트 단위 행 (시작/종료 시각, 참여 캐릭터 이미지, 구간 딜량), expander에 구간 히트 상세
- "히트 상세 캐릭터" 라디오 버튼이 버스트 expander와 하단 집계 표 공유 (`burst_char_radio` key)
- 하단 — 스킬별 집계 표: **전체 전투 시간** 동안의 총합 (버스트 구간 합산 아님)

**탭 3 — 버프 타임라인** (`buff_panel.py`)
- 대상 캐릭터(또는 "타겟 랩쳐") 선택 → Gantt 차트
- 풀버스트 시작/종료 시점 점선 표시
- 상시 적용 버프 표 (큐브·소장품·장비) — 차트 아래 표시

**탭 4 — 히트 추적** (`hit_panel.render_filter_only`)
- 캐릭터 선택 + 스킬·시간 범위 필터 → 히트 이벤트 목록만 표시 (집계 표 없음)

---

## 버프 타임라인 표시 규칙

레이블 형식: `[시전자] 버프명 (stat 값%)`
- stat·value는 `BuffEvent.stat`, `BuffEvent.value` 필드에서 읽음
- 장비·소장품·큐브 계열 버프명: `장비 옵션` / `소장품:공통` / `소장품:{무기군}` / `큐브:{큐브명}`
- stat suffix를 버프명에 포함하지 않음 (stat 필드로 분리)
- 장비·소장품 버프는 회색(`#666666`)으로 표시

---

## 이미지 관련 금지 사항

**이미지 문제 발생 시 Claude가 임의로 재다운로드·교체 절대 금지.**

이미지는 유저가 직접 관리. Claude가 할 일:
1. `ui/image_utils.py`의 캐릭터명 → 파일명 변환 규칙 확인 (` : ` → `_` 등)
2. `image/` 폴더에 해당 파일 존재 여부 확인
3. 위 두 가지를 유저에게 보고하고 판단 맡김

재다운로드 필요 시 유저가 `scraper/download_images.py` 직접 실행.

---

## 미구현 — 향후 작업

**버프 시간별 추이 차트** (Phase 2)
현재 버프 이벤트는 activate/expire만 기록. 시간축 누적 그래프 그리려면 `timeline.py`에 매 N프레임 `get_buffs()` 스냅샷 `buff_trace` 로그 필요. 구현 전 설계 확정 필요.

**조합 비교** (Phase 3)
A vs B 나란히, 레벨/스킬 레벨 변화 DPS 추이. 미착수.
