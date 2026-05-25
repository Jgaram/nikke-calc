# char-parse

신규 캐릭터 스킬 파싱 (Phase A + B).

$ARGUMENTS: 캐릭터 이름 (예: `/char-parse 신데렐라`)

---

## 시작 전 확인

$ARGUMENTS 비어 있으면 즉시 멈추고 캐릭터 이름 요청. 이름 확인 후에만 진행.

---

## 시작 전 준비

1. `context/PARSING.md` 전체 읽는다.
2. `context/IMPL-STATUS.md` stat 마스터 테이블 읽는다.

---

## Phase A — 스킬 파싱

1. `PARSING.md` 12절에서 해당 캐릭터 `예정` 상태인지 확인.
2. `nikke_scraped.json`에서 해당 캐릭터 데이터 읽는다:
   ```python
   import json, sys
   sys.stdout.reconfigure(encoding='utf-8')
   with open('scraper/nikke_scraped.json', encoding='utf-8') as f:
       data = json.load(f)
   print(json.dumps(data['$ARGUMENTS'], ensure_ascii=False, indent=2))
   ```
3. `PARSING.md` 절차에 따라 스킬 파싱 → `data/parsed_skills.json`에 추가.
4. 파싱 중 **기존에 없는 stat** 등장 시:
   - 즉시 유저에게 알리고 stat 이름(snake_case) 확정.
   - `PARSING.md` 6절 stat 목록에 추가.
   - stat 마스터 테이블에 추가 (구현 상태 ❌로 초기화).
5. 파싱 완료 후 `PARSING.md` 12절에서 해당 캐릭터 `완료`(또는 `진행 중`)로 이동.

---

## Phase B — 구현 필요 항목 파악

파싱 결과 stat 목록을 stat 마스터 테이블과 대조:

| 구현 상태 | 처리 |
|-----------|------|
| ✅ 완전 구현 | 추가 작업 없음 |
| ⚠️ 부분 구현 | DPS 영향 없으면 스킵, 있으면 `/char-impl` 필요 |
| ❌ 미구현 | `/char-impl` 필요 |
| 🚫 보류 | 스킵 |

핵심 메카닉(발동 조건, 모드 전환 등)이 기존 구현으로 표현 가능한지 판단. 불확실하면 `timeline.py`에서 관련 경로 grep.

주의 stat:
- **타임라인 전용** (`attack_speed_pct`, `pellet_count` 등): `buff_manager.py` 등록만으로 부족
- **boolean 플래그** (`pierce_enabled` 등): `get_buffs()` 내 boolean 분기에 추가 필요
- **새 timing**: `_timing_match()`에 분기 없으면 트리거 발동 안 함

---

## Phase B 완료 후

구현 필요 항목 목록 유저에게 제시 후 멈춘다. 구현 진행 여부 묻는다. 임의로 `/char-impl` 시작 금지.
