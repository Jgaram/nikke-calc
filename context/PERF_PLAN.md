# 성능 개선 계획

작성일: 2026-05-22  
기반: profiler 실측 + 코드 분석

---

## 현황 (실측)

| 항목 | 수치 |
|------|------|
| 시뮬레이션 시간 (4인 스쿼드, 300s) | ~4.5s |
| get_buffs 호출 | 73,083회 — 캐시 히트 67.1%, 미스 24,076회 |
| `_runtime_condition_ok` 호출 | 839,614회 (미스 1회당 평균 35회) |
| `is_stunned` 호출 | 43,245회 (매 프레임 순회) |
| `_active` 평균 버프 수 | 45.9개 (최대 52) |
| 캐시 무효화 횟수 | 327회 |

**병목 분포** (profiler 기준, 총 18s):
- `get_buffs`: 8.8s (49%)
- `is_stunned`: 1.9s (11%)
- `_timing_match`/`notify`: 1.9s
- `consume_bullet_buffs`: 1.4s

---

## 스케일링 분석

비용 = `(캐시 미스 수) × (active 버프 수) × (조건 평가 비용)`

- **캐시 미스 수**: squad 발사 횟수로 결정 → DB 캐릭터 추가와 무관, 거의 고정
- **active 버프 수**: squad 4-5명 기준 천장 존재 (현재 46개). 새 캐릭터 추가해도 같은 squad라면 선형 증가 후 수렴
- **조건 평가 비용**: `self_state:`/`self_stack_above:` 조건이 `_active`를 **재순회** → O(N²) 요인

현재 효과 분포 (parsed_skills.json 기준):
- 전체 효과 529개 중 runtime condition 있음: 130개 (24.6%)
- `self_state:`/`self_stack_above:` (중첩 순회): 88개 (16.6%)

→ 지금도 O(N²) 실재. 이 조건이 많은 캐릭터가 squad에 들어오면 즉시 체감.

---

## 발견된 잠재 버그 (기존 캐시 코드)

`timeline.py`에서 `state["full_burst"]`와 `state["charging"]`을 직접 변경하는데,  
직후 `notify()`가 버프를 **하나도 활성화하지 않으면** `_invalidate_buffs_cache()`가 호출되지 않음.

→ `during_full_burst`, `not_during_full_burst`, `during_charge` 조건 가진 버프가  
state 전환 직후 stale 캐시를 반환할 수 있다.

현재 회귀 테스트가 통과하는 이유: 풀버스트/차지 전환 시 보통 버프도 함께 발동되어 간접 무효화됨.  
그러나 조건부 발동 스킬이 많아질수록 이 우연한 보장이 깨질 수 있음.

---

## 개선 방향

### 추천하지 않는 방법

**`_runtime_condition_ok` 결과를 (effect_id, cache_version)으로 캐싱**  
→ state 변화가 cache_version을 보장하지 않으므로 위의 잠재 버그를 악화시킴.  
→ 버프 적용 순서·타이밍 정확성을 해칠 위험이 있어 도입 불가.

---

### Phase 1 — 무효화 버그 수정 (선행 필수)

**`timeline.py`에서 state 변경 직후 캐시 명시 무효화**

```python
# full_burst 시작 (line 677 부근)
state["full_burst"] = True
bm._invalidate_buffs_cache()   # ← 추가
for n in self.squad_names:
    bm.notify("full_burst_start", t, n)

# full_burst 종료 (line 583 부근)
state["full_burst"] = False
bm._invalidate_buffs_cache()   # ← 추가
for n in self.squad_names:
    bm.notify("full_burst_end", t, n)
```

`state["charging"]` 변경 지점도 동일하게 처리.

**리스크**: 거의 없음. 기존에 우연히 맞았던 것을 명시적으로 보장하는 것.  
**검증**: 회귀 테스트 통과 확인.

---

### Phase 2 — `has_runtime_conditions` 플래그 (핵심)

**아이디어**: `ActiveBuff` 생성 시점에, 해당 효과의 condition 목록에  
runtime 재평가가 필요한 조건이 있는지 미리 판별해 플래그로 저장.  
`get_buffs` 내에서 플래그가 False인 버프는 `_runtime_condition_ok` 호출 자체를 건너뜀.

**runtime condition으로 분류되는 조건들:**
```
during_charge, during_full_burst, not_during_full_burst,
self_hp_above:, self_hp_below:, self_hp_max,
ally_hp_below:,
self_stack_above:, self_state:,
gauge_above:, gauge_below:
```

**구현 위치:**

`ActiveBuff` (또는 `_activate` 내부):
```python
# _activate() 에서 ActiveBuff 생성 시
_RUNTIME_COND_PREFIXES = frozenset([
    "during_charge", "during_full_burst", "not_during_full_burst",
    "self_hp_above:", "self_hp_below:", "self_hp_max", "ally_hp_below:",
    "self_stack_above:", "self_state:", "gauge_above:", "gauge_below:",
])

def _has_runtime_cond(conditions: list) -> bool:
    for c in conditions:
        for prefix in _RUNTIME_COND_PREFIXES:
            if c == prefix or c.startswith(prefix):
                return True
    return False

# ActiveBuff 생성 시
has_rc = _has_runtime_cond(eff["trigger"].get("condition", []))
ab = ActiveBuff(..., has_runtime_conditions=has_rc)
```

`get_buffs` 내 루프:
```python
for ab in self._active:
    ...
    conditions = eff["trigger"].get("condition", [])
    if ab.has_runtime_conditions:
        if not self._runtime_condition_ok(conditions, ab.caster, caster, target, t):
            continue
    # else: 정적 조건만 있으므로 _condition_ok에서 이미 검증됨 → 통과
```

**리스크 평가:**
- `_active` 리스트 순서 변경 없음
- 버프 활성화/만료 로직 변경 없음
- 정적 조건만 있는 버프(약 75%)에 대해 `_runtime_condition_ok` 호출 제거
- 결과 정확성: 정적 조건 버프는 `_condition_ok`에서 이미 검증됐으므로 항상 True가 보장됨 → 동일 결과

**기대 효과:**  
`_runtime_condition_ok` 호출 839k → 약 207k (75% 감소)  
중첩 _active 순회 횟수도 비례 감소.

---

### Phase 3 — `is_stunned` 캐싱

```python
def _invalidate_buffs_cache(self):
    self._cache_version += 1
    self._buffs_cache.clear()
    self._stunned_cache.clear()   # ← 추가

def is_stunned(self, char_name: str) -> bool:
    cached = self._stunned_cache.get(char_name)
    if cached is not None:
        return cached
    result = self._compute_is_stunned(char_name)
    self._stunned_cache[char_name] = result
    return result
```

(기존 is_stunned 로직을 `_compute_is_stunned`로 이름 변경)

**리스크**: 매우 낮음. stun 상태는 버프 활성화/만료 시 이미 캐시 무효화됨.  
**기대 효과**: ~1.9s 절감.

---

## 예상 효과 요약

| 개선 | 절감 |
|------|------|
| Phase 1 (버그 수정) | 속도 영향 미미, 정확성 보장 |
| Phase 2 (runtime_conditions 플래그) | get_buffs 비용 40~50% 감소 |
| Phase 3 (is_stunned 캐싱) | ~1.9s → 거의 0 |
| **합산 예상** | 4.5s → 약 2~2.5s |

---

## 스케일링 전망

Phase 2 적용 후:

- active 버프 수 증가: **여전히 선형** (O(N × K), K = runtime 조건 버프 수)
- `self_state:`/`self_stack_above:` 조건 증가: **K × N**으로 증가하므로 이 조건을 많이 쓰는 캐릭터 주의
- 메커니즘 추가 자체는 `_active` 천장이 있으므로 장기적으로 수렴

---

## 작업 순서

1. Phase 1 먼저 — 버그 수정이 선행되어야 Phase 2/3의 정확성이 보장됨
2. Phase 1 후 회귀 테스트 통과 확인
3. Phase 2 구현
4. Phase 3 구현
5. 최종 회귀 테스트 + 속도 재측정
