# UI 계획 — 시뮬레이터 디버깅 대시보드

## 목적

현재 디버깅 방식(코드 수정 → 콘솔 출력 확인)은 Claude가 게임 메커니즘을 완전히 이해하지 못한 상태에서 수정하기 때문에 버그를 놓치거나 새로 만들기 쉽다. 유저가 직접 인게임 동작을 보면서 시뮬레이터 출력과 비교할 수 있는 UI가 필요하다.

**1차 목표: 메커니즘 확인 도구** (조합 비교, DPS 계산은 2차)

---

## 기술 스택 결정

**Python + Streamlit** 선택.

- `run.py`의 `simulate()` 호출을 그대로 재사용, 백엔드 코드 변경 없음
- 브라우저 기반 UI, 별도 빌드 없이 `streamlit run app.py`로 실행
- 테이블·차트·슬라이더 등 디버깅에 필요한 위젯 기본 제공
- 파이썬 생태계 내에서 완결 (JS 없음)

---

## 화면 구성

### 사이드바 — 팀 구성

| 항목 | 구현 |
|------|------|
| 캐릭터 5명 선택 | `parsed_skills.json` 키 목록 → selectbox × 5 (시뮬 가능한 캐릭터만) |
| 선택 즉시 이미지 표시 | 슬롯 아래 캐릭터 이미지 (base64 data URI) |
| 스킬 레벨 | 슬라이더 1~10, 기본 10 |
| 시뮬 시간 | 슬라이더 30~300초, 기본 180 |
| 실행 버튼 | `simulate(verbose=True)` 호출 → `SimResult` session_state 저장 |

### 탭 1 — 버스트 & 대미지 분석

**버스트 타임라인**
- 풀버스트 횟수 메트릭
- 풀버스트 단위 행: 시작 시각 / 종료 시각 / 버스트 사용 캐릭터 이미지 (최대 5칸)

**대미지 분석**
- 캐릭터별 총 대미지 막대 차트 + 차트 아래 이미지 행
- 스킬명 + hit_tag 기준 대미지/히트수/평균 분류 표
- 버스트 사이클별 누적 대미지 스택 막대 차트

### 탭 2 — 버프 & 히트 추적

**버프 스냅샷** (풀버스트 진입 시점 기준)
- 대상 캐릭터 선택 → 해당 캐릭터에 적용된 버프 Gantt 차트
- 시전자별 이미지 + 버프 목록 테이블
- 전체 스냅샷 원시 데이터 (시전자 이미지 포함)

**히트 이벤트 필터**
- 캐릭터 / 스킬명 / 시간 범위 필터
- 조건에 맞는 HitEvent 목록 테이블 (t / caster / skill_name / hit_tag / damage / crit)

---

## 구현 단계

### Phase 1 — 기초 연결 ✅ 완료

1. `app.py` + `ui/` 패키지 생성
2. 팀 구성 사이드바 (`team_panel.py`) — `parsed_skills.json` 목록, 캐릭터 이미지
3. 버스트 타임라인: 풀버스트 단위 행 + 사용 캐릭터 이미지 (`burst_panel.py`)
4. 대미지 분석: 막대 차트, 스킬별 집계 표, 사이클별 차트
5. 버프 스냅샷: Gantt 차트 + 시전자 이미지 (`buff_panel.py`)
6. 히트 이벤트 필터: 캐릭터 / 스킬명 / 시간 범위

**이미지 처리**: `image/` 폴더의 `.webp` 파일을 base64 data URI로 변환 (`image_utils.py`).
캐릭터명의 ` : ` → ` _ ` 변환으로 파일명 매핑.

### Phase 2 — 버프 시각화 강화 (미시작)

- 스택형 버프 꺾은선 그래프 — `BuffSnapshot`은 풀버스트 시점만 찍으므로, 별도 `buff_trace` 로그 추가 필요 (`timeline.py` 수정)
- stat 합산(atk_pct, crit_rate 등) 시간별 추이

### Phase 3 — 비교 기능 (2차 목표)

- 조합 A vs B 나란히 비교
- 레벨·스킬 레벨 변화에 따른 DPS 추이 그래프

---

## 버프 추적을 위한 백엔드 추가 사항

현재 `SimLog`에는 버프 스냅샷이 풀버스트 진입 시점에만 찍힌다. Phase 2 구현 시 다음이 필요:

- `timeline.py` → 매 N프레임(예: 60프레임 = 1초)마다 `BuffManager.get_buffs()`를 호출해 활성 버프와 stat 합산값을 기록하는 `buff_trace` 로그 추가
- `SimLog`에 `buff_trace: list[BuffTraceEntry]` 필드 추가
- `BuffTraceEntry`: `t`, `char`, `active_buff_names: list[str]`, `stat_totals: dict`

이 변경은 Phase 2 착수 시 설계 확정 후 진행.

---

## 파일 구조

```
Calc/
├── app.py                  ← Streamlit 진입점
├── ui/
│   ├── __init__.py
│   ├── image_utils.py      ← 이미지 base64 변환 유틸
│   ├── team_panel.py       ← 팀 구성 UI (사이드바)
│   ├── burst_panel.py      ← 버스트·대미지 분석
│   └── buff_panel.py       ← 버프 스냅샷·히트 추적
├── run.py                  ← 기존 CLI 진입점 (유지)
└── calculator/             ← 기존 그대로
```

---

## 실행 방법

```bash
python -m streamlit run app.py
```

브라우저에서 `http://localhost:8501` 접속.

최초 실행 시 `~/.streamlit/credentials.toml`에 `email = ""`이 있어야 이메일 프롬프트를 건너뜀.
