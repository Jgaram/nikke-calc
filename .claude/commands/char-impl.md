# char-impl

신규 캐릭터 계산기 구현·검증 (Phase C + D).

`/char-parse` 완료 + 유저 승인 후 실행.

$ARGUMENTS: 캐릭터 이름 (예: `/char-impl 신데렐라`)

---

## 시작 전

1. `context/CALCULATOR.md` 읽는다.
2. `context/IMPL-STATUS.md` Step 1-6 체크리스트 읽는다.

---

## Phase C — 계산기 코드 수정

`/char-parse` Phase B 확인 항목 대상으로 IMPL-STATUS.md Step 1-6 필요 항목만 수행.

기존 캐릭터 영향 확인:
- `_BUFFS_ZERO` 초기값 0/False이면 기존 캐릭터는 해당 key를 0으로 받음 → 안전.
- 타임라인 로직 변경은 회귀 테스트로 검증.

---

## Phase D — 테스트

### 스쿼드 구성

`parsed_nikke.json`에서 $ARGUMENTS 버스트 단계 확인 후 템플릿 선택, **유저에게 먼저 제안·컨펌 받는다**:

```
B3:          ["리틀 머메이드", "크라운", TARGET, "test_B3"]
B1 (쿨 20s): [TARGET, "크라운", "신데렐라", "test_B3"]
B1 (쿨 40s): [TARGET, "리틀 머메이드", "크라운", "신데렐라", "test_B3"]
B2 (쿨 20s): ["리틀 머메이드", TARGET, "신데렐라", "test_B3"]
B2 (쿨 40s): ["리틀 머메이드", TARGET, "크라운", "신데렐라", "test_B3"]
```

### test.py 실행

`context/test.py` 스쿼드 수정 후 실행 (`python -m context.test`).

버스트 사이클 간격이 **12.5초에서 5초 이상 벗어나면** 유저에게 스쿼드 구성 확인.

### 체크리스트

- [ ] 모든 스킬 버프가 버프 스냅샷에 나타나는가
- [ ] 타이밍 트리거(hit_count, pellet_hit 등)가 예상 횟수만큼 발동하는가
- [ ] 히트 태그 분포가 캐릭터 메카닉과 일치하는가 (SG 펠릿 수, 버스트 중 변화 등)
- [ ] 기존 스쿼드 수치 변화 없는가

### 회귀 테스트

타임라인 로직 수정 시 필수. 실행·판정·FAIL 처리: `context/IMPL-STATUS.md` `### 운영` 섹션.
