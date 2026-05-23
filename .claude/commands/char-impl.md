# char-impl

신규 캐릭터 계산기 구현 및 검증 (Phase C + D).

`/char-parse` 완료 후, 유저 승인을 받고 실행한다.

$ARGUMENTS: 캐릭터 이름 (예: `/char-impl 아이언메이든`)

---

## 시작 전 준비

1. `context/CALCULATOR.md`를 읽는다 (모듈 구조·데이터 흐름 파악).
2. `context/IMPL-STATUS.md`(또는 `REFERENCE.md`)의 Step 1-6 체크리스트를 읽는다.

---

## Phase C — 계산기 코드 수정

`/char-parse` Phase B에서 확인한 구현 필요 항목을 대상으로 IMPL-STATUS.md Step 1-6을 필요한 항목만 골라 수행한다.

기존 캐릭터에 영향이 없는지 항상 확인한다:
- `_BUFFS_ZERO` 초기값이 0 또는 False이면 기존 캐릭터는 해당 key를 0으로 받으므로 안전하다.
- 타임라인 로직 변경은 회귀 테스트로 검증한다.

---

## Phase D — 테스트

### 스쿼드 구성

`parsed_nikke.json`에서 $ARGUMENTS의 버스트 단계를 확인하고, 아래 템플릿 중 적합한 구성을 **유저에게 먼저 제안한 뒤 컨펌받는다**:

```
B3 대상:         ["리틀 머메이드", "크라운", TARGET, "test_B3"]
B1 (쿨 20s):    [TARGET, "크라운", "신데렐라", "test_B3"]
B1 (쿨 40s):    [TARGET, "리틀 머메이드", "크라운", "신데렐라", "test_B3"]
B2 (쿨 20s):    ["리틀 머메이드", TARGET, "신데렐라", "test_B3"]
B2 (쿨 40s):    ["리틀 머메이드", TARGET, "크라운", "신데렐라", "test_B3"]
```

### test.py 실행

`context/test.py`를 확인하고 위 스쿼드 구성으로 수정한 뒤 실행한다 (`python -m context.test`).

시뮬 실행 후 버스트 사이클 간격이 **12.5초에서 5초 이상 벗어나면** 스쿼드 구성이 의도한 것인지 유저에게 확인한다.

### 체크리스트

- [ ] 모든 스킬 버프가 버프 스냅샷에 나타나는가
- [ ] 타이밍 트리거(hit_count, pellet_hit 등)가 예상 횟수만큼 발동하는가
- [ ] 히트 태그 분포가 캐릭터 메카닉과 일치하는가 (SG 펠릿 수, 버스트 중 변화 등)
- [ ] 기존 스쿼드 수치에 변화가 없는가

### 회귀 테스트

타임라인 로직을 수정했으면 반드시 실행한다. 실행·판정·FAIL 처리는 IMPL-STATUS.md `### 운영` 섹션 참고.

---

## 완료 후

체크리스트 통과 시 `context/GIT.md`를 참고해 커밋한다.
