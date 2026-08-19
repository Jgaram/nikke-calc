# 단계 2 — 스킬 파싱


> char-add 워크플로우의 절차 문서. 진입점·게이트 규칙은 `SKILL.md`.

스킬 텍스트를 `parsed_skills.json` 구조로 옮기고(Phase A) 구현 필요 항목을 뽑는다(Phase B).

---

## 시작 전 준비

1. `context/PARSING.md` **§1~8 읽는다** (§9 예외·§12 현황은 `PARSING-CHARS.md`로 분리 — 필요 시 참조).
2. `context/IMPL-STATUS.md` stat 마스터 테이블 읽는다 (**키 로스터·구현상태 정본**).
3. `context/GAMEPLAY.md` §스쿼드 구성 + §트리거 발동 의미 읽는다.
4. **`context/scenarios/<이름>.md` 초안 시나리오 읽는다 (필수).** 없으면 즉시 멈추고 단계 1(`SCENARIO.md` 초안 모드)로 되돌아간다. 시나리오 없이 파싱하면 메카닉 이해 부족으로 condition·target 매핑 오류 위험.

---

## Phase A — 스킬 파싱

1. `context/PARSING-CHARS.md` `## 현황 목록`에서 해당 캐릭터 `예정` 상태인지 확인.
2. `nikke_scraped.json`에서 해당 캐릭터 데이터 읽는다:
   ```python
   import json, sys
   sys.stdout.reconfigure(encoding='utf-8')
   with open('scraper/nikke_scraped.json', encoding='utf-8') as f:
       data = json.load(f)
   print(json.dumps(data['<캐릭터명>'], ensure_ascii=False, indent=2))
   ```
3. 애장품 보유 캐릭터(`nikke_scraped.json`에 `"애장품"` 키)는 **6개를 파싱한다** — 기본 템플릿 3개 + 단계별 템플릿 3개. 단계별 항목에서 나온 효과에는 `"favorite": <단계>`를 붙인다(`PARSING.md` §애장품). 판본끼리 효과 `name`과 배열 순서를 맞춘다 — 이름은 버프 조회 키(`target_effect`·`scaling_ref`·`same_target:`)고 순서는 원문 `■` 블록의 실행 순서다. 파싱 후 `python -m context.doclint` 검사 H로 판본 완비를 확인한다.
   - 애장품이 **줄만 추가한** 슬롯은 살아남는 줄의 문구와 `{N}` 자리가 그대로다 — 이때 기본 판본은 추가된 줄에서 나온 항목을 빼고 `values`만 기본 원문의 같은 자리 값으로 갈아끼우면 된다(값이 자리마다 다르므로 반드시 다시 읽는다). 트리거·대상·지속·중첩 이름이 바뀐 슬롯은 이 방법을 쓰지 않고 원문대로 새로 파싱한다.
4. `PARSING.md` 절차에 따라 스킬 파싱 → `data/parsed_skills.json`에 추가. **파싱 결과가 시나리오 초안의 메카닉 묘사와 어긋나면**(예: 시나리오는 모드 전환인데 파싱은 단순 buff로 나옴) 즉시 유저에게 보고하고 모호 점 해소 후 진행.
5. 파싱 중 **기존에 없는 stat** 등장 시:
   - 즉시 유저에게 알리고 stat 이름(snake_case) 확정.
   - `IMPL-STATUS.md` stat 마스터 테이블에 추가 (구현 상태 ❌로 초기화) — **로스터·구현상태 정본**.
   - 텍스트→키 매핑이 헷갈릴 만하면 `PARSING.md` §6에 매핑 단서만 추가(선택). 양쪽 동시 편집 아님.
6. 파싱 완료 후 `context/PARSING-CHARS.md` `## 현황 목록`에서 해당 캐릭터 `완료`(또는 `진행 중`)로 이동.
7. `python -m context.roster` 실행 — 파싱 현황 로스터(루트 `roster.html`) 재생성. 데이터에서 파생되므로 HTML은 손대지 않는다.

---

## Phase B — 구현 필요 항목 파악

파싱 결과 stat 목록을 stat 마스터 테이블과 대조:

| 구현 상태 | 처리 |
|-----------|------|
| ✅ 완전 구현 | 추가 작업 없음 |
| ⚠️ 부분 구현 | DPS 영향 없으면 스킵, 있으면 단계 4 필요 |
| ❌ 미구현 | 단계 4 필요 |
| 🚫 보류 | 스킵 |

핵심 메카닉(발동 조건, 모드 전환 등)이 기존 구현으로 표현 가능한지 판단. **시나리오 초안의 메카닉을 기준으로 점검** — 시나리오가 명시한 동작이 `timeline.py`·`buff_manager.py` 기존 경로로 표현 가능한지 grep으로 확인. 모호하면 유저 질문.

주의 stat:
- **타임라인 전용** (`attack_speed_pct`, `pellet_count` 등): `buff_manager.py` 등록만으로 부족
- **boolean 플래그** (`pierce_enabled` 등): `get_buffs()` 내 boolean 분기에 추가 필요
- **새 timing**: `_timing_match()`에 분기 없으면 트리거 발동 안 함

---

## 단계 종료

구현 필요 항목 목록 유저에게 제시 후 멈춘다. **다음은 단계 3 — 시나리오 보강**(`SCENARIO.md` 보강 모드) — 파싱 결과 반영해 효과 요약 표·타임라인·체크리스트를 stat 단위로 정밀화. **단계 4 직행 금지.** 보강 없이 구현하면 stat 단위 검증 기준이 없어 깊은 버그가 잠복한다.
