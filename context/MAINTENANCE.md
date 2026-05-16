# 신규 버프/스탯 추가 유지보수 가이드

---

## 신규 캐릭터 추가 전체 절차

신규 캐릭터를 시뮬레이터에 추가할 때 아래 순서대로 진행한다.

### Phase A — 스킬 파싱

1. `PARSING.md` 12절 목록에서 해당 캐릭터가 `예정` 상태인지 확인한다.
2. `scraper/nikke_scraped.json`에서 캐릭터 데이터를 읽는다.
3. `PARSING.md` 절차에 따라 스킬을 파싱하고 `data/parsed_skills.json`에 추가한다.
4. 파싱 중 **기존에 없는 stat**이 등장하면:
   - 즉시 유저에게 알리고, 적절한 stat 이름(snake_case)을 확정한다.
   - `PARSING.md` 6절 stat 목록에 추가한다.
   - `MAINTENANCE.md` stat 마스터 테이블에 추가한다 (구현 상태 ❌로 초기화).
5. 파싱 완료 후 `PARSING.md` 12절에서 해당 캐릭터를 `완료`(또는 `진행 중`)로 이동한다.

### Phase B — 구현 필요 항목 파악

파싱 결과의 stat 목록을 MAINTENANCE.md stat 마스터 테이블과 대조한다.

| 구현 상태 | 처리 |
|-----------|------|
| ✅ 완전 구현 | 추가 작업 없음 |
| ⚠️ 부분 구현 | DPS에 영향 없으면 스킵, 영향 있으면 아래 Phase C 진행 |
| ❌ 미구현 | Phase C 진행 |
| 🚫 보류 | 스킵 |

파싱 결과를 보면서 캐릭터의 핵심 메카닉(발동 조건, 모드 전환 등)이 기존 구현으로 표현 가능한지 판단한다. 불확실하면 `timeline.py`에서 관련 경로를 grep해 실제 처리 흐름을 확인한다.

특히 아래 stat은 겉으로는 `_STAT_TO_BUFF`에 있어도 타임라인 반영이 별도로 필요하므로 주의한다:
- **타임라인 전용** (`attack_speed_pct`, `pellet_count`, `pellet_count_fixed` 등): `buff_manager.py` 등록만으로 부족하고 `timeline.py`의 발사 루프에서 직접 읽어야 함
- **boolean 플래그** (`pierce_enabled` 등): `get_buffs()` 내 boolean 분기에 추가해야 `True`로 세팅됨
- **새 timing**: `_timing_match()`에 분기 없으면 트리거 자체가 발동하지 않음 — `notify()` 호출처도 함께 확인

### Phase C — 계산기 코드 수정

아래 Step 1~6(이 문서 하단 체크리스트)을 필요한 항목만 골라 수행한다.

기존 캐릭터에 영향이 없는지 항상 확인한다:
- `_BUFFS_ZERO` 초기값이 0 또는 False이면 기존 캐릭터는 해당 key를 0으로 받으므로 안전하다.
- 타임라인 로직 변경은 기존 캐릭터 팀으로 회귀 테스트를 실행한다.

### Phase D — 테스트

```python
# 해당 캐릭터가 포함된 팀으로 시뮬레이션
r = simulate(team, verbose=True)

# 1. 기본 동작 확인
print(r.summary())

# 2. 버프 스냅샷으로 스킬 발동 여부 확인
print(r.log.buff_summary(chars=[TARGET]))

# 3. 히트 태그 분포로 펠릿 수·모드 전환 확인 (SG 등)
from collections import Counter
tags = Counter(e.hit_tag for e in r.hits if e.caster == TARGET)

# 4. 기존 캐릭터 팀으로 회귀 테스트
r_old = simulate(old_team)
print(r_old.summary())  # 수치 변화 없어야 함
```

체크리스트:
- [ ] 모든 스킬 버프가 스냅샷에 나타나는가
- [ ] 타이밍 트리거(hit_count, pellet_hit 등)가 예상 횟수만큼 발동하는가
- [ ] 히트 태그 분포가 캐릭터 메카닉과 일치하는가 (SG 펠릿 수, 버스트 중 변화 등)
- [ ] 기존 팀 수치에 변화가 없는가

위 체크리스트를 모두 통과했으면 `context/GIT.md`를 참고해 커밋한다.

---

## 신규 stat/timing 추가 체크리스트

니케에 신규 캐릭터가 추가되거나 기존 스킬이 변경되어 새로운 stat 종류가 생기면,
아래 체크리스트를 **순서대로** 수행한다.

---

## 체크리스트

### Step 1 — `PARSING.md` 6절 stat 매핑 테이블에 추가

파일 위치: `PARSING.md` → **6절: stat 매핑**

- 새 stat 이름(snake_case)과 설명을 테이블에 추가한다.
- 어느 DealForm 항목(①~⑦)에 해당하는지, 또는 타임라인 전용인지 명시한다.
- 새 stat이 `buff` type인지, `damage` type인지, `instant` type인지 분류한다.

### Step 2 — `calculator/buff_manager.py` 두 곳 수정

**2-A. `_BUFFS_ZERO` 딕셔너리에 키 추가**

```python
_BUFFS_ZERO: dict[str, Any] = {
    ...
    "새_stat_키": 0.0,   # 또는 False (bool인 경우)
}
```

**2-B. `_STAT_TO_BUFF` 딕셔너리에 매핑 추가**

```python
_STAT_TO_BUFF: dict[str, str] = {
    ...
    "parsed_skills의_stat명": "buffs_딕셔너리_키",
}
```

- `parsed_skills.json`의 `stat` 문자열 → `get_buffs()`가 반환하는 `buffs` 키로 매핑한다.
- 타임라인 전용 stat(예: `charge_speed_pct`, `max_ammo_pct`)도 여기에 추가한다.
- `damage` / `instant` / `weapon_change` type 효과는 매핑하지 않는다 (타임라인이 직접 처리).

**주의**: `crit_rate` 계열은 `_CRIT_RATE_STATS` 집합에도 추가해야 독립 확률 합성이 적용된다.
```python
_CRIT_RATE_STATS = {"crit_rate", "normal_atk_crit_rate", ...}
```

### Step 2-C. 새 stat이 boolean 플래그인 경우

`charge_time_fixed`, `charge_speed_buff_immune`처럼 수치가 아닌 on/off 플래그 stat은 세 곳을 추가로 수정한다.

1. `_BUFFS_ZERO`에 `False`로 초기화
2. `get_buffs()` 루프 내 boolean 플래그 분기에 `buff_key` 추가:
   ```python
   if buff_key in ("charge_time_fixed", "charge_speed_buff_immune", ...):
       buffs[buff_key] = True
       continue
   ```
3. `get_buffs()` 후처리 블록에 해당 플래그가 미치는 효과 구현 (예: `charge_time_fixed=True`이면 `charge_speed_pct = 0`)

### Step 2-D. 새 stat이 `caster_based` 환산이 필요한 경우

`charge_speed_caster_based_pct`, `atk_caster_based_pct`처럼 시전자 스탯 기준으로 수치를 환산하는 stat은 `_get_value()` 내부에 환산 로직을 추가한다.

```python
if eff.get("stat") == "새_stat_caster_based_pct":
    caster_base = _NIKKE.get(ab.caster, {}).get("기준_필드")
    if caster_base is None:
        return None
    # 환산 공식 작성
    base = ...
```

- 환산 후 반환값의 단위가 기존 stat 키와 동일한지 확인한다.
- 대상 캐릭터에게 해당 무기/스탯이 없어 의미 없는 경우라도 수치는 반환하고, 실제 효과 미적용은 timeline/damage 쪽에 맡긴다.

### Step 3 — `calculator/damage.py` 수정

새 stat이 DealForm ①~⑦에 직접 영향을 주는 경우에만 수정한다.

| 영향 항목 | 수정 함수 |
|----------|----------|
| ① 계수 보정 | `_factor1()` |
| ② 공방 계산 | `_factor2()` |
| ③ 보너스 (크리·코어 등) | `_factor3()` |
| ④ 차지 배율 | `_factor4()` |
| ⑤ 유형별 버프 | `_factor5()`, `hit_type` 플래그 추가 |
| ⑥ 적 받는 대미지 | `_factor6()`, `hit_type` 플래그 추가 |
| ⑦ 우월 코드 | `_factor7()` |

타임라인 전용(charge_speed_pct, max_ammo_pct 등)은 `damage.py`를 수정하지 않는다.

`hit_type`에 새 플래그가 필요하면 `default_hit_type()` 함수에도 추가한다.

### Step 3-E. `hp_below_count:threshold:N` timing

`[사용 횟수 별 효과]` + `체력 N% 이하 도달 시` 패턴에서 각 단계를 구분할 때 사용한다.

- `"hp_below_count:20:1"` — `hp_below:20` 이벤트의 1번째 발생 시 발동
- `"hp_below_count:20:2"` — 2번째 발생 시 발동
- 각 단계에 `max_trigger:1`을 병기해 전투 중 1회 발동 제한
- `_timing_match()`에 이미 구현됨. 새 threshold가 생겨도 추가 구현 불필요

### Step 3-F. `max_trigger` 동작 방식

`max_trigger: N` 필드가 있는 효과는 전투 중 최대 N회만 발동한다. **추가 구현 불필요** — `BuffManager._activate()`에서 `_trigger_counts: dict[int(effect_id) → int]`로 추적하며 자동 차단한다.

- 모든 type(buff/instant/damage/weapon_change)에 동일하게 적용됨
- 버프가 만료된 후 재발동 시도도 차단됨 (전투 중 누적 횟수 기준)
- `reset()`시 `_trigger_counts`도 초기화됨

### Step 3-G. HP 모델

`state["hp"]` (현재 체력 절대값) + `state["hp_pct"]` (비율, 0~100) 두 값을 항상 동기화해서 관리한다. `state["hp_pct"]`는 읽기 전용으로 취급하고 직접 쓰지 않는다.

**`state["hp"]` 직접 변경 후 반드시 `bm.sync_hp(name)` 호출.**

| 상황 | 처리 |
|------|------|
| 현재 체력 증가 (힐) | `hp = min(hp + delta, bm.effective_max_hp(name))` → `sync_hp` |
| 현재 체력 감소 | `hp = max(hp - delta, 0)` → `sync_hp` |
| `max_hp_pct` 발동 | `hp += base_hp × val%` (최대치 cap 적용) → `sync_hp` — `_activate()` 후처리에서 자동 처리 |
| `max_hp_only_pct` 발동 | `hp` 변화 없음 → `sync_hp` (비율만 재계산) — `_activate()` 후처리에서 자동 처리 |

**`bm.effective_max_hp(name)`**: `base_hp × (1 + (max_hp_pct + max_hp_only_pct 버프 합계) / 100)`. 힐 cap 계산에 사용.

**`heal_received` 이벤트**: `heal_hp_pct` instant 핸들러에서만 발생. `max_hp_pct`는 힐이 아니므로 발생하지 않는다.

---

### Step 4 — 새 timing / condition 추가 시

새 캐릭터가 기존에 없던 timing(트리거 시점)이나 condition(발동 조건)을 사용하는 경우 `buff_manager.py`를 수정한다.

**새 timing 추가**

`_timing_match()` 메서드에 분기를 추가한다.

```python
# 예: "new_event:N" 형태
if timing.startswith("new_event:") and event == "new_event":
    raw = timing.split(":")[1]
    if not raw.lstrip("-").isdigit(): return False
    return count % int(raw) == 0
```

그 후 timeline에서 해당 이벤트 발생 시점에 `bm.notify("new_event", t, caster)` 호출을 추가한다.

**새 condition 추가**

조건이 활성화 시점에만 평가되면 `_condition_ok()`에 추가한다.
조건이 매 `get_buffs()` 호출 시마다 재평가되어야 하면(상태 의존) `_runtime_condition_ok()`에 추가한다.

| 평가 시점 | 추가 위치 |
|----------|----------|
| 버프 발동 시 1회 | `_condition_ok()` |
| 대미지 계산 시마다 | `_runtime_condition_ok()` |

### Step 5 — 새 target 유형 추가 시

새 캐릭터가 기존에 없던 target 패턴을 사용하는 경우 `buff_manager.py`를 수정한다.

**5-A. `_resolve_target()` 에 분기 추가**

```python
if target.startswith("새_패턴:"):
    n = int(target.split(":")[1])
    # 대상 목록 계산 후 반환
    return ...
```

**5-B. 스탯 비교 기반 target이면 `_LAZY_RESOLVE_PREFIXES`에 추가**

아군 스탯(공격력·체력·방어력 등)을 비교해 대상을 정하는 target은 모든 버프가 적용된 후에 순위를 결정해야 한다. 이런 패턴은 반드시 `_LAZY_RESOLVE_PREFIXES` 튜플에 추가한다.

```python
_LAZY_RESOLVE_PREFIXES = (
    "allies_lowest_atk_burst3:",
    "allies_top_atk:",
    ...
    "새_스탯_비교_패턴:",   # ← 추가
)
```

- `_LAZY_RESOLVE_PREFIXES`에 포함된 target은 `_activate()` 시점에 resolve하지 않고 `target_chars=None`으로 저장된다.
- `get_buffs()` 호출 시점에 `_resolve_target()`이 실행되어 그 시점의 버프가 반영된 스탯으로 순위를 결정한다.
- 반대로 팀 순서·위치·무기·클래스처럼 버프와 무관한 고정 속성 기반 target은 lazy resolve가 불필요하다.

**5-C. `_effective_atk()` 확장이 필요한 경우**

새 target이 공격력 기준 정렬을 사용하고 `atk_pct`/`atk_flat` 외 추가 버프 스탯이 공격력에 영향을 준다면 `_effective_atk()`의 stat 수집 범위를 확장한다.

### Step 6 — 검산

`damage.py` 하단 `__main__` 블록에 새 stat을 검증하는 케이스를 추가하고 실행한다.

```bash
python calculator/damage.py
```

새 timing/target을 추가한 경우 `simulate()` 실행 후 로그나 `SimResult.hits`로 발동 여부를 직접 확인한다.

---

## stat 마스터 테이블

`parsed_skills.json`에 등장하는 모든 stat의 구현 상태를 한 곳에서 관리한다.
**새 stat 파싱 시 반드시 이 테이블을 먼저 업데이트한 후 Step 1~4를 진행한다.**

구현 상태 범례:
- ✅ 완전 구현 (파싱 → 계산까지 반영됨)
- ⚠️ 부분 구현 (buffs에 집계되지만 계산에 미반영, 또는 조건부 미지원)
- ❌ 미구현 (buffs에도 없음. 파싱은 되나 계산 무효)
- 🚫 보류 (지원 계획 없음 — 해당 모델 자체가 없음)

### buff stat

| stat (parsed_skills) | buffs 키 | DealForm | 구현 상태 | 비고 |
|---|---|---|---|---|
| `atk_pct` | `atk_pct` | ② | ✅ | |
| `hp_caster_based_pct` | — | — | ❌ | 아군 HP 버프. DPS 미사용 |
| `def_caster_based_pct` | `def_caster_based_pct` | — | ⚠️ | buffs에 집계되나 DPS 계산 미사용 |
| `def_pct` | `def_pct` | — | ⚠️ | base_stat 재계산용. 현재 timeline 미반영 |
| `max_hp_pct` | `max_hp_pct` | — | ✅ | 최대+현재 체력 동반 증가. `state["hp"]` 동기화 |
| `max_hp_only_pct` | `max_hp_only_pct` | — | ✅ | 최대 체력만 증가. `state["hp"]` 유지 |
| `atk_caster_based_pct` | — | — | ❌ | 미구현. 시전자 ATK 기준 환산 필요 |
| `atk_from_hp_pct` | — | — | ❌ | 최종 최대 체력 N%만큼 ATK 가산. 미구현 |
| `crit_rate` | `crit_rate` | ③ | ✅ | 독립 확률 합성 (`_CRIT_RATE_STATS`) |
| `normal_atk_crit_rate` | `crit_rate` | ③ | ✅ | `crit_rate`로 합산. `is_normal_atk=False` 시 분리 미지원 (근사) |
| `crit_dmg` | `crit_dmg` | ③ | ✅ | |
| `normal_atk_crit_dmg` | `crit_dmg` | ③ | ✅ | `crit_dmg`로 합산. `is_normal_atk=False` 시 분리 미지원 (근사) |
| `core_dmg_pct` | `core_dmg_pct` | ③ | ✅ | `core_dmg_pct`로 합산 |
| `part_dmg_pct` | `part_dmg_pct` | ⑤ | ✅ | `is_part=True` 히트에만 가산 |
| `intercept_dmg_pct` | — | — | ❌ | 저지 부위 대미지. 미구현 |
| `atk_dmg_pct` | `atk_dmg_pct` | ⑤ | ✅ | |
| `burst_dmg_pct` | `burst_dmg_pct` | ⑤ | ✅ | `is_burst_damage=True` 히트에만 가산 |
| `pierce_dmg_pct` | `pierce_dmg_pct` | ⑤ | ✅ | `is_pierce_damage=True` 히트에만 가산 |
| `dot_dmg_pct` | `dot_dmg_pct` | ⑤ | ✅ | `is_dot=True` 히트에만 가산 |
| `split_dmg_pct` | `split_dmg_pct` | ⑥ | ✅ | `is_split=True` 히트에서 ⑥에 합산 |
| `charge_dmg_pct` | `charge_dmg_pct` | ④ | ✅ | |
| `charge_dmg_mag_pct` | `charge_dmg_mag_pct` | ④ | ✅ | ④ 승수. `(1+mag%) × full_charge_mult% × (1+charge_dmg%)` |
| `sequential_dmg_pct` | `sequential_dmg_pct` | ⑤ | ✅ | `is_sequential=True` 히트에만 가산 |
| `optimal_range_dmg_pct` | — | ③ | ❌ | 적정거리 대미지 ▲. 미구현. ③의 고정 +30%와 별도 버프 항목 |
| `received_dmg_pct` | `received_dmg` | ⑥ | ✅ | 음수 저장 시 감소 효과 |
| `heal_received_pct` | — | — | ❌ | 받는 회복량 ▲. 힐 모델 없음 |
| `element_bonus_pct` | `element_bonus_pct` | ⑦ | ✅ | `is_element_match=True` 시 ⑦에 가산 |
| `normal_atk_dmg_pct` | `normal_atk_dmg_pct` | ① | ✅ | `is_normal_atk=True`일 때 ① 계수에 가산 |
| `max_ammo_pct` | `max_ammo_pct` | — | ✅ | 타임라인 처리. `CharState` 장탄 계산 반영 |
| `max_ammo_flat` | — | — | ❌ | 고정값 장탄 증가. 미구현 (`max_ammo_pct`와 별도) |
| `pellet_count` | — | — | ❌ | 펠릿 수 증가. 미구현 |
| `pellet_count_fixed` | — | — | ❌ | 펠릿 개수 절대값 고정. 미구현 |
| `charge_speed_pct` | `charge_speed_pct` | — | ✅ | 타임라인 처리. 차지 시간에 반영 |
| `charge_speed_caster_based_pct` | `charge_speed_pct` | — | ✅ | `_get_value()`에서 시전자 `charge_time` 기준 환산 후 `charge_speed_pct`로 합산 |
| `charge_time_caster_based` | — | — | ❌ | 차지 시간 절대값 감소. 미구현. `charge_speed_pct` 환산과 별도 |
| `reload_speed_pct` | `reload_speed_pct` | — | ✅ | 타임라인 처리. 재장전 시간에 반영 |
| `attack_speed_pct` | — | — | ❌ | 공격 속도(연사속도) ▲. 미구현 |
| `accuracy_pct` | `accuracy_pct` | — | ⚠️ | buffs에 집계되나 DPS 계산 미사용 |
| `burst_charge_speed_pct` | — | — | 🚫 | 버스트 게이지 모델 단순화로 보류 |
| `optimal_range_max` | — | — | ❌ | 최대 적정 사거리 증가. 미구현 |
| `explosion_range` | — | — | ❌ | 폭발 범위 증가. 미구현 |
| `pierce_range` | — | — | ❌ | 관통 범위 증가. 미구현 |
| `pierce_enabled` | — | — | ❌ | 관통 특화 활성. 미구현 |
| `fullburst_duration` | `fullburst_duration` | — | ✅ | `BurstController.tick()`의 switching→full_burst 진입 시 `get_buffs`로 합산해 지속 시간 결정 |
| `effect_interval` | — | — | ✅ | `_dispatch_instant` 내부 처리. `target_effect` 필수 |
| `lifesteal_pct` | `lifesteal_pct` | — | ⚠️ | buffs에 집계되나 실제 체력 회복 처리 없음 |
| `armor_break_dmg_pct` | `armor_break_dmg_pct` | ⑤ | ✅ | `is_armor_break_damage=True` 히트에만 가산. ②에서 적 방어력 0 처리 |
| `projectile_dmg_pct` | — | — | ❌ | 발사체 대미지 ▲. 미구현 |
| `projectile_attachment_dmg_pct` | `projectile_attachment_dmg` | ⑤ | ✅ | `is_projectile_attachment=True` 히트에만 가산 |
| `projectile_explosion_dmg_pct` | `projectile_explosion_dmg` | ⑤ | ✅ | `is_projectile_explosion=True` 히트에만 가산 |
| `burst_stage_override:N` / `burst_stage_override:reenterN` | — | — | ✅ | 타임라인 `_rebuild_burst_order()` / `_check_reenter()`에서 처리 |
| `element_code_override` | — | — | ❌ | 특정 코드 적에게 우월 코드 적용. 미구현 |
| `trigger_count_reduce` | — | — | ✅ | `_dispatch_instant`에서 처리 |
| `shield_dmg_pct` | — | — | ❌ | 보호막 대미지 ▲. 미구현 |
| `cover_def_pct` | — | — | 🚫 | 엄폐물 방어력 ▲. 엄폐 모델 없음 |
| `cover_hp_pct` | — | — | 🚫 | 엄폐물 체력 ▲. 엄폐 모델 없음 |
| `outgoing_heal_pct` | — | — | ❌ | 주는 회복량 ▲. 힐 모델 없음 |
| `shield_from_max_hp_pct` | — | — | ❌ | 최대 체력 N%만큼 보호막 생성. 보호막 모델 없음 |
| `heal_overcharge_store` | — | — | ❌ | 초과 회복 저장. 미구현 |
| `shield_restore_pct` | — | — | ❌ | 보호막 회복 ▲. 보호막 모델 없음 |
| `burst_dmg_single_pct` | — | — | ❌ | 단일 대상 버스트 대미지 ▲. 미구현 (`burst_dmg`로 통합 필요 또는 별도 처리) |
| `burst_dmg_aoe_pct` | — | — | ❌ | 전체 대상 버스트 대미지 ▲. 미구현 |
| `burst_cooldown` | `burst_cooldown` | — | ✅ | buff 상태로 지속. 타임라인 `_effective_burst_cool()`에서 반영 |
| `skill_cooldown` | — | — | ❌ | 개별 스킬 쿨타임 초 감소. 미구현. `target_effect` 필요 |
| `skill_cooldown_pct` | `skill_cooldown_pct` | — | ⚠️ | 스킬 쿨타임 % 감소. `tick()`의 `every:Ns` interval에 반영. `target_effect` 미지원 — target 캐릭터의 모든 `every:Ns` 스킬에 일괄 적용 |
| `stun` | — | — | 🚫 | 기절. 기절 모델 없음 |
| `invincible` | — | — | ❌ | 무적. 피격 모델 없음 |
| `undying` | — | — | ❌ | 불굴. 피격 모델 없음 |
| `stealth` | — | — | ❌ | 은신. 타겟팅 모델 없음 |
| `decoy` | — | — | ❌ | 분신 생성. 미구현 |
| `infinite_ammo` | — | — | ❌ | 장탄 무한. 미구현 |
| `focus_fire` | — | — | ❌ | 사격 집중. 미구현 |
| `enemy_movement_disable` | — | — | ❌ | 적 이동 불가. 적 이동 모델 없음 |
| `debuff_immune` | `debuff_immune` | — | ✅ | `_activate()`에서 harmful 효과 차단 |
| `debuff_immune:[name]` | — | — | ✅ | `_activate()`에서 `debuff_immune:{eff_name}` 차단. `_has_immune()` 직접 탐색으로 `_STAT_TO_BUFF` 매핑 불필요 |
| `stun_immune` | `stun_immune` | — | ⚠️ | buffs에 집계되나 기절 모델 없어 실질 차단 없음 |
| `charge_speed_buff_immune` | `charge_speed_buff_immune` | — | ✅ | `get_buffs()` 후처리에서 `charge_speed_pct > 0`이면 0으로 초기화 |
| `charge_speed_debuff_immune` | `charge_speed_debuff_immune` | — | ✅ | `get_buffs()` 후처리에서 `charge_speed_pct < 0`이면 0으로 초기화 |
| `charge_time_fixed` | `charge_time_fixed` | — | ✅ | `get_buffs()` 후처리에서 `charge_speed_pct = 0` |
| `stack_change_immune` | `stack_change_immune` | — | ✅ | `_dispatch_instant()`에서 스택 변경 차단 |
| `atk_copy` | — | — | ❌ | 공격력 복제. 복잡 메카닉, `_unparseable` |
| `hp_copy` | — | — | ❌ | 체력 복제. 복잡 메카닉, `_unparseable` |
| `received_dmg_split` | — | — | ❌ | 받는 대미지 차등 분배. `_unparseable` |
| `heal_split` | — | — | ❌ | 회복 균등 분배. `_unparseable` |
| `armor_break_enabled` | — | — | ❌ | 일반 공격을 방어력 무시 대미지로 치환. 미구현 |
| `gauge_charge_enabled` | — | — | ✅ | buff로 등록. 게이지 충전 가능 상태 활성화. `gauge_id` 필수 |
| `gauge_max_add` | — | — | ✅ | `_dispatch_instant()`의 `gauge_charge`에서 cap 합산 |
| `taunt` | `taunt` | — | ⚠️ | buffs에 집계되나 타겟팅 모델 없음 |
| `lock_on` | `lock_on` | — | ❌ | **스노우 화이트 : 헤비암즈 전용**. 세븐스 드워프 공격 대상 지정 고유 메카닉. `values`/`fixed_value` 없음 |

### damage stat

damage type은 `_STAT_TO_BUFF` 매핑 없음. 타임라인 `_handle_damage_eff()`에서 직접 처리.

| stat | hit_type 플래그 | 구현 상태 | 비고 |
|---|---|---|---|
| `damage` | `is_normal_atk=True` (일반공격) / `False` (스킬) | ✅ | |
| `auto_damage` | `is_normal_atk=True`, `damage_formula: "normal_attack"` | ✅ | |
| `burst_damage` | `is_burst_damage=True` | ✅ | |
| `dot_damage` | `is_dot=True` | ✅ | `tick_interval` 기반 |
| `split_damage` | `is_split=True` | ✅ | |
| `bonus_damage` | — | ✅ | `timing: "burst_cast"` 시 `_pending_burst_dmg`에 보류 |
| `armor_break_damage` | `is_armor_break_damage=True` | ✅ | ②에서 적 방어력 0 처리 |
| `pierce_damage` | `is_pierce_damage=True` | ✅ | |
| `projectile_explosion_damage` | `is_projectile_explosion=True` | ✅ | RL 기본 공격에 자동 적용 |
| `projectile_attachment_damage` | `is_projectile_attachment=True` | ✅ | |
| `sequential_damage` | `is_sequential=True` | ✅ | `:N` suffix → hit_count |

### instant stat

instant type은 `_STAT_TO_BUFF` 매핑 없음. `_dispatch_instant()` 또는 타임라인 핸들러로 처리.

| stat | 처리 위치 | 구현 상태 | 비고 |
|---|---|---|---|
| `burst_cooldown_reduce` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `ammo_charge_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `ammo_charge_flat` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `burst_charge_pct` | — | 🚫 | 버스트 게이지 모델 단순화로 보류 |
| `heal_hp_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | `state["hp"]` 갱신 후 `hp_pct` 재동기화 |
| `buff_stack_add` | `_dispatch_instant()` | ✅ | |
| `buff_stack_remove` | `_dispatch_instant()` | ✅ | |
| `debuff_stack_add` | `_dispatch_instant()` | ✅ | |
| `debuff_stack_remove` | `_dispatch_instant()` | ✅ | |
| `remove_named_buff` | `_dispatch_instant()` | ✅ | `target_effect` 필수 |
| `debuff_cleanse` | `_dispatch_instant()` | ✅ | |
| `enemy_buff_cleanse` | — | 🚫 | 적 버프 모델 없음 |
| `force_reload` | — | ❌ | `CharState._start_reload()` 강제 호출 필요. 미구현 |
| `current_hp_reduce` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `cover_heal_pct` | — | 🚫 | 엄폐 모델 없음 |
| `burst_reentry` | — | ❌ | `_check_reenter()` 경로와 별도. 미구현 |
| `revive` | — | 🚫 | 전투불능 모델 없음 |
| `gauge_charge` | `_dispatch_instant()` | ✅ | `gauge_id` 필수 |
| `gauge_consume` | `_dispatch_instant()` | ✅ | `gauge_id` 필수 |
| `force_move` | — | 🚫 | 복잡 메카닉, `_unparseable` |

---

---

## trigger/condition 마스터 테이블

**새 timing/condition 파싱 시 반드시 이 테이블을 업데이트한다.**

구현 상태 범례:
- ✅ 완전 구현 (`_timing_match` / `_condition_ok` / `_runtime_condition_ok`에 분기 있음)
- ⚠️ 부분 구현 (매칭 로직은 있으나 이벤트 발생처 없음 — notify 호출 없음)
- ❌ 미구현 (분기 자체 없음)

### timing

| timing | 구현 상태 | 발생 위치 / 비고 |
|---|---|---|
| `battle_start` | ✅ | `bm.battle_start()` |
| `passive` | ✅ | `battle_start` 이벤트로 처리. 영구 지속, `_runtime_condition_ok`에서 매 프레임 재평가 |
| `full_burst_start` | ✅ | `bm.notify("full_burst_start", ...)` |
| `full_burst_start_count:N` | ✅ | `full_burst_start` 이벤트의 N번째 발생 시 |
| `full_burst_end` | ✅ | `bm.notify("full_burst_end", ...)` |
| `full_burst_end_count:N` | ✅ | `full_burst_end` 이벤트의 N번째 발생 시 |
| `burst_enter:N` | ✅ | `bm.notify("burst_enter:N", ...)` |
| `burst_cast` | ✅ | `bm.notify("burst_cast", ...)` |
| `burst_cast_count:N` | ✅ | `burst_cast` 이벤트의 N번째 발생 시 |
| `team_burst_cast:N` | ✅ | `bm.notify("team_burst_cast:N", ...)` |
| `hit_count:N` | ✅ | `bm.notify("hit_count", ...)`. `trigger_count_reduce` 버프로 N 감소 가능 |
| `crit_hit_count:N` | ✅ | `bm.notify("crit_hit", ...)`. `trigger_count_reduce` 버프로 N 감소 가능 |
| `full_charge` | ✅ | `bm.notify("full_charge", ...)` |
| `full_charge_hit` | ✅ | `bm.notify("full_charge_hit", ...)` |
| `full_charge_count:N` | ✅ | `full_charge_hit` 이벤트의 N번째 발생 시. `trigger_count_reduce` 버프로 N 감소 가능 |
| `core_hit_count:1` | ✅ | `bm.notify("core_hit", ...)` (횟수 없는 형태, `timing == event`로 처리) |
| `core_hit_count:N` | ✅ | `bm.notify("core_hit", ...)`. `trigger_count_reduce` 버프로 N 감소 가능 |
| `pellet_hit_count:N` | ✅ | `bm.notify("pellet_hit", ...)`. `trigger_count_reduce` 버프로 N 감소 가능 |
| `last_bullet` | ✅ | `bm.notify("last_bullet", ...)` |
| `last_bullet_fire` | ✅ | `bm.notify("last_bullet_fire", ...)` |
| `enemy_death` | ✅ | `bm.notify("enemy_death", ...)` |
| `received_hit_count:N` | ⚠️ | `_timing_match`에 분기 있음. `bm.notify("received_hit", ...)` 호출처 없음 (보스 공격 모델 없음) |
| `event:full_reload` | ✅ | `bm.notify("event:full_reload", ...)` |
| `event:cover` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:ally_down` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:self_down` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:part_destroy` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:enemy_spawn` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:target_spawn` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:heal_received` | ⚠️ | 매칭 로직(`event:xxx`) 있음. `heal_hp_pct` 핸들러에서만 notify 발생 |
| `event:shield_applied` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 보호막 모델 없음 |
| `event:shield_consumed` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 보호막 모델 없음 |
| `event:cover_hit` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:projectile_destroy` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:ally_burst_cast` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:state_end:[상태명]` | ✅ | `tick()`에서 버프 만료 시 자동 발생 |
| `event:[상태명/스킬명]` | ✅ | `_activate()`에서 named buff 최초 등록 시 `notify(f"event:{name}", ...)` 자동 발생. 타임라인 별도 추가 불필요 |
| `hp_below:N` | ⚠️ | `_timing_match`에 분기 있음. 체력 변화 시 `bm.notify("hp_below:N", ...)` 호출처 없음 |
| `hp_below_count:N:순서` | ⚠️ | `_timing_match`에 분기 있음. `hp_below:N` 이벤트 발생처 없음 |
| `every:Ns` | ✅ | `tick()`에서 내부 타이머로 처리. notify 경로 아님 |
| `every_stack:N` | ❌ | 미구현. `_timing_match`에 분기 없음 |
| `on_attack` | ✅ | `bm.notify("on_attack", ...)` — `_fire()` (자동사격: SG/AR/SMG/MG) 및 `_tick_charge()` (풀차지 발사: SR/RL) 두 경로에서 모두 발생 |
| `first_trigger` | ❌ | 미구현. `max_trigger:1`로 대체 가능 |
| `multi_hit:N` | ✅ | `_timing_match`에 분기 있음. `bm.notify("multi_hit:N", ...)` — 타임라인에서 동시 명중 감지 필요 |
| `part_hit_count:N` | ⚠️ | `_timing_match`에 분기 없음 (파츠 모델 없음). 매칭 로직 미구현 |
| `charge_hold:N` | ✅ | `_timing_match`에 분기 있음. `bm.notify("charge_hold:N", ...)` — 타임라인에서 차지 유지 감지 필요 |
| `weapon_hit:[name]` | ✅ | `_timing_match`에 분기 있음. `bm.notify("weapon_hit:[name]", ...)` — weapon_change 발사 시 타임라인이 notify |
| `team_ammo_consume:N` | ❌ | 미구현. `_timing_match`에 분기 없음 |

### condition

condition은 두 위치에서 평가된다.
- `_condition_ok()`: 버프 발동 시점 1회 평가 (notify 시)
- `_runtime_condition_ok()`: `get_buffs()` 호출마다 재평가 (상태 의존 조건)

| condition | 평가 위치 | 구현 상태 | 비고 |
|---|---|---|---|
| `during_full_burst` | 양쪽 모두 | ✅ | `state["full_burst"]` |
| `not_during_full_burst` | 양쪽 모두 | ✅ | `state["full_burst"]` |
| `prob:N` | `_condition_ok` 전용 | ✅ | `get_buffs`에서 재판정 안 함 |
| `self_hp_above:N` | 양쪽 모두 | ✅ | `state["hp_pct"]` |
| `self_hp_below:N` | 양쪽 모두 | ✅ | `state["hp_pct"]` |
| `self_hp_max` | 양쪽 모두 | ✅ | `hp_pct >= 100.0` |
| `ally_hp_below:N` | `_runtime_condition_ok` 전용 | ✅ | `state["hp_pct"][query_target]` |
| `ally_hp_max` | — | ❌ | 미구현. 분기 없음 |
| `during_charge` | 양쪽 모두 | ✅ | `state["charging"][caster]` |
| `during_shield` | — | ❌ | 미구현. 보호막 모델 없음 |
| `during_reload` | — | ❌ | 미구현. `state["reloading"]` 연동 필요 |
| `burst_casted` | `_condition_ok` 전용 | ✅ | `state["burst_casted"][caster]` |
| `burst_not_casted` | `_condition_ok` 전용 | ✅ | `state["burst_casted"][caster]` |
| `back_row` | `_condition_ok` 전용 | ✅ | 팀 인덱스 2 이상 = 후열 |
| `squad_ally_exists` | `_condition_ok` 전용 | ✅ | 5인 팀에서 항상 True (스킵 처리) |
| `focusing` | — | ❌ | 미구현. `focus_fire` stat과 연동 필요 |
| `not_core` | — | ❌ | 미구현. hit_type 연동 필요 |
| `core_hit_count:1` | — | ❌ | 미구현. timing이 아닌 condition으로 쓰일 때 |
| `self_state:상태명` | 양쪽 모두 | ✅ | `_active`에서 해당 name 버프 존재 여부 확인 |
| `not_self_state:상태명` | 양쪽 모두 | ✅ | `_active`에서 해당 name 버프 부재 여부 확인 |
| `target_state:상태명` | 양쪽 모두 | ✅ | 단일 적 가정: `"__enemy__"`가 target_chars에 있는 활성 효과로 확인 |
| `self_stack_above:스택명:N` | 양쪽 모두 | ✅ | `_active`에서 스택 수 확인 |
| `gauge_above:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `gauge_below:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `gauge_eq:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `has_burst1_ally` | `_condition_ok` 전용 | ✅ | `state["burst_stages"]` |
| `no_burst1_ally` | `_condition_ok` 전용 | ✅ | `state["burst_stages"]` |

---

## target 마스터 테이블

**새 target 파싱 시 반드시 이 테이블을 업데이트한다.**

구현 상태 범례:
- ✅ 완전 구현 (`_resolve_target()`에 분기 있음)
- ❌ 미구현 (분기 없음 — 빈 리스트 반환)

lazy resolve 여부: 버프 반영 스탯 기준 정렬이 필요한 target은 `_activate()` 시점이 아닌 `get_buffs()` 시점에 resolve 됨 → `_LAZY_RESOLVE_PREFIXES`에 등록 필요.

| target | lazy resolve | 구현 상태 | 비고 |
|---|:---:|---|---|
| `"self"` | ❌ | ✅ | |
| `"all_allies"` | ❌ | ✅ | |
| `"all_allies_excl_self"` | ❌ | ✅ | |
| `"allies:N"` | ❌ | ✅ | 팀 입력 순서 앞 N명 |
| `"allies_adjacent:N"` | ❌ | ✅ | 양 옆 아군. 자신 포함 최대 N+1명 |
| `"allies_top_atk:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨 |
| `"allies_top_atk_excl:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 자신 제외 |
| `"allies_lowest_hp:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨 |
| `"allies_top_def:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨 |
| `"allies_lowest_atk_burst3:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 3버스트 아군 중 공격력 최저 N명 |
| `"allies_random:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 자신 제외 무작위 |
| `"allies_weapon:무기유형"` | ❌ | ✅ | `parsed_nikke["weapon_type"]` 기준 |
| `"allies_weapon_excl_self:SG"` | ❌ | ✅ | 자신 제외 샷건 소지 아군 전체. `_resolve_target()`에 `allies_weapon_excl_self:` 분기 추가. `allies_weapon:SG`와 별도 |
| `"allies_class:클래스"` | ❌ | ✅ | `parsed_nikke["class"]` 기준 |
| `"allies_code:코드"` | ❌ | ✅ | `parsed_nikke["element_code"]` 기준 |
| `"allies_below_def"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 시전자보다 방어력 낮은 아군 전체 |
| `"target"` / `"target_body"` / `"same_target"` | ❌ | ✅ | `__enemy__` 센티널 반환. 타임라인이 실제 처리 |
| `"all_enemies"` / `"enemies_in_range"` / `"enemies_nearest_in_range"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_random:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_nearest:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_top_atk:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_top_def:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_lowest_def:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_lowest_hp:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"target_and_nearby:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_with_buff:버프명"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"all_projectiles"` | ❌ | ❌ | 발사체 모델 없음. 빈 리스트 반환 |
| `"self_cover"` | ❌ | ❌ | 엄폐 모델 없음. 빈 리스트 반환 |
| `"same_target:[name]"` | ❌ | ❌ | 연계 대상 명시 형태. 미구현 |
| `"allies_lowest_atk_burst3:N"` 형 확장 | ✅ | — | 새 스탯 비교 기반 target 추가 시 `_LAZY_RESOLVE_PREFIXES`에 등록 필수 |

---

## 빠른 참조: stat 분류 (신규 추가 시 판단용)

| 분류 | stat 예시 | buff_manager | damage.py |
|------|----------|-------------|-----------|
| DealForm ①에 영향 | `normal_atk_dmg_pct` | ✅ 추가 | ✅ `_factor1` |
| DealForm ②에 영향 | `atk_pct`, `atk_flat`, `def_ignore_pct` | ✅ 추가 | ✅ `_factor2` |
| DealForm ③에 영향 | `crit_rate`, `crit_dmg`, `core_dmg` | ✅ 추가 | ✅ `_factor3` |
| DealForm ④에 영향 | `charge_dmg_pct`, `charge_dmg_mag_pct` | ✅ 추가 | ✅ `_factor4` |
| DealForm ⑤에 영향 | `atk_dmg_pct`, `burst_dmg`, `pierce_dmg_pct`, `dot_dmg_pct`, `part_dmg_pct` | ✅ 추가 | ✅ `_factor5` + `hit_type` 플래그 |
| DealForm ⑥에 영향 | `received_dmg_pct`, `split_dmg_pct` | ✅ 추가 | ✅ `_factor6` + `hit_type` 플래그 |
| DealForm ⑦에 영향 | `element_bonus_pct` | ✅ 추가 | ✅ `_factor7` |
| 타임라인 전용 | `charge_speed_pct`, `max_ammo_pct`, `reload_speed_pct` | ✅ 추가 | ❌ 수정 불필요 |
| boolean 플래그 | `charge_time_fixed`, `charge_speed_buff_immune` | ✅ Step 2-C | ❌ 수정 불필요 |
| caster_based 환산 | `charge_speed_caster_based_pct`, `atk_caster_based_pct` | ✅ Step 2-D | 환산 후 기존 키 사용 |
| 타임라인 직접 처리 | `damage`, `instant`, `weapon_change` type | ❌ 매핑 불필요 | ❌ 수정 불필요 |

## 빠른 참조: target 분류

| target 패턴 예시 | lazy resolve 필요 | 이유 |
|----------------|:-----------------:|------|
| `"self"`, `"all_allies"`, `"allies:N"` | ❌ | 고정 위치 기반 |
| `"allies_weapon:SR"`, `"allies_class:지원"` | ❌ | 고정 속성 기반 |
| `"allies_top_atk:N"`, `"allies_lowest_atk_burst3:N"` | ✅ | 버프 반영 공격력 기준 정렬 |
| `"allies_lowest_hp:N"` | ✅ | 런타임 체력 상태 기준 정렬 |
| `"allies_top_def:N"`, `"allies_below_def"` | ✅ | 방어력 기준 정렬 |
| `"allies_random:N"` | ✅ | 매 호출마다 재추첨이 자연스러움 |

---

## `nikke_scraped.json` 갱신 (신규 캐릭터 추가)

1. `scraper/nikke_scraper.py` 재실행 (또는 `rescrape.py`로 신규 ID만 수집)
2. `scraper/parse_nikke.py` 재실행 → `data/parsed_nikke.json` 갱신
3. 신규 캐릭터 스킬 파싱 → `data/parsed_skills.json`에 추가 (PARSING.md 절차)
4. 파싱 결과에 새 stat이 있으면 위 Step 1~4 수행

---

## 회귀 테스트 기준점 (2026-05-07)

계산기 로직 수정 후 기존 수치 변화가 없는지 확인하는 기준값.
수치가 바뀌었다면 의도한 변경인지 반드시 검토할 것.

### 팀 스펙 (context/regression_test.py `make_char` 기본값)

| 항목 | 값 |
|---|---|
| 레벨 | 400 |
| 돌파 | 3 |
| 코어 강화 | 0 |
| 친밀도 | 30 |
| 스킬 레벨 | 10 |
| 버스트 리젠 시간 | 2.0s |
| 장비 등급 | 전 부위 T5 (옵션 없음) |
| 장비 옵션 합산 | atk_pct 20%, max_ammo_pct 120% |
| 큐브 | 재장 Lv.15 |
| 콘솔 | 공용 180 / 클래스 100 / 회사 100 |
| 컬렉션 단계 | SR15 |

### 팀 구성

FIXED = `["아니스 : 스타", "크라운"]`  
CANDIDATES (아래 표) + `"B3"` (4번 슬롯)

### CANDIDATES 기준 수치

대미지 집계 대상: 해당 CANDIDATE 단독 (`char_total` 기준)  
10회 반복 실행 평균값. `context/regression_test.py` 허용 오차는 표준편차 기준으로 설정.

| CANDIDATE | 평균 딜 | 표준편차 | 편차% |
|---|---:|---:|---:|
| 라피 : 레드 후드 | 701,118,380 | 8,477,194 | 1.21% |
| 스노우 화이트 : 헤비암즈 | 1,020,857,738 | 4,014,939 | 0.39% |
| 신데렐라 | 1,113,789,055 | 7,527,600 | 0.68% |
| 리버렐리오 | 963,212,249 | 15,248,613 | 1.58% |
| 홍련 : 흑영 | 852,730,260 | 6,741,361 | 0.79% |
| 네온 : 비전 아이 | 966,974,513 | 8,642,904 | 0.89% |
| 미하라 : 본딩 체인 | 831,709,432 | 5,444,193 | 0.65% |
| 도로시 : 세렌디피티 | 1,273,809,328 | 7,806,765 | 0.61% |
| 디젤 : 윈터 스위츠 | 926,941,262 | 10,910,845 | 1.18% |

> **디젤 : 윈터 스위츠 팀 순서 예외**: `["아니스 : 스타", "크라운", "B3", "디젤 : 윈터 스위츠"]` (4번 슬롯 배치)

### 회귀 테스트 운영 방침

- 실행: 프로젝트 루트에서 `python -m context.regression_test`
- 판정: 단발 1회 시행, ±3σ 범위 내이면 PASS
- 3σ 초과 시: **재시도 없이 FAIL 처리**. 출력된 σ 수치를 보고 의도한 변경인지 판단
  - 3~4σ: 통계적 false alarm 가능성 있음 (약 0.3%). 코드 변경 없으면 다시 돌려볼 것
  - 5σ↑: 코드 변경이 원인일 가능성 높음. 반드시 검토
- 기준값 갱신: 의도한 변경 후에는 10회 재측정해서 MAINTENANCE.md와 regression_test.py 양쪽 모두 업데이트
