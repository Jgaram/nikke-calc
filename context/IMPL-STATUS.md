# 신규 버프/스탯 추가 유지보수 가이드

신규 캐릭터 추가: `/char-parse` (Phase A+B) 및 `/char-impl` (Phase C+D).

---

## 신규 stat/timing 추가 체크리스트

신규 stat 종류 생기면 아래 순서대로 수행.

---

## 체크리스트

### Step 1 — `PARSING.md` 6절 stat 매핑 테이블에 추가

`PARSING.md` → **6절: stat 매핑**

- 새 stat 이름(snake_case)과 설명 추가.
- DealForm 항목(①~⑦) 해당 여부 또는 타임라인 전용 명시.
- `buff` / `damage` / `instant` type 분류.

### Step 2 — `calculator/buff_manager.py` 두 곳 수정

**2-A. `_BUFFS_ZERO` 키 추가**

```python
_BUFFS_ZERO: dict[str, Any] = {
    ...
    "새_stat_키": 0.0,   # 또는 False (bool인 경우)
}
```

**2-B. `_STAT_TO_BUFF` 매핑 추가**

```python
_STAT_TO_BUFF: dict[str, str] = {
    ...
    "parsed_skills의_stat명": "buffs_딕셔너리_키",
}
```

- `parsed_skills.json`의 `stat` → `get_buffs()` 반환 `buffs` 키로 매핑.
- 타임라인 전용 stat(`charge_speed_pct`, `max_ammo_pct` 등)도 추가.
- `damage` / `instant` / `weapon_change` type은 매핑 안 함 (타임라인이 직접 처리).

**주의**: `crit_rate` 계열은 `_CRIT_RATE_STATS` 집합에도 추가해야 독립 확률 합성이 적용된다.
```python
_CRIT_RATE_STATS = {"crit_rate", "normal_atk_crit_rate", ...}
```

### Step 2-C. 새 stat이 boolean 플래그인 경우

`charge_time_fixed`, `charge_speed_buff_immune`처럼 on/off 플래그 stat — 세 곳 추가:

1. `_BUFFS_ZERO`에 `False`로 초기화
2. `get_buffs()` 루프 내 boolean 플래그 분기에 `buff_key` 추가:
   ```python
   if buff_key in ("charge_time_fixed", "charge_speed_buff_immune", ...):
       buffs[buff_key] = True
       continue
   ```
3. `get_buffs()` 후처리 블록에 플래그 효과 구현 (예: `charge_time_fixed=True`이면 `charge_speed_pct = 0`)

### Step 2-D. 새 stat이 `caster_based` 환산이 필요한 경우

`charge_speed_caster_based_pct`, `atk_caster_based_pct`처럼 시전자 스탯 기준 환산 stat — `_get_value()` 내부에 환산 로직 추가:

```python
if eff.get("stat") == "새_stat_caster_based_pct":
    caster_base = _NIKKE.get(ab.caster, {}).get("기준_필드")
    if caster_base is None:
        return None
    # 환산 공식 작성
    base = ...
```

- 환산 후 반환값 단위가 기존 stat 키와 동일한지 확인.
- 해당 무기/스탯이 없어 의미 없는 경우라도 수치는 반환. 실제 효과 미적용은 timeline/damage 쪽에 맡김.

### Step 3 — `calculator/damage.py` 수정

새 stat이 DealForm ①~⑦에 직접 영향을 주는 경우에만 수정.

| 영향 항목 | 수정 함수 |
|----------|----------|
| ① 계수 보정 | `_factor1()` |
| ② 공방 계산 | `_factor2()` |
| ③ 보너스 (크리·코어 등) | `_factor3()` |
| ④ 차지 배율 | `_factor4()` |
| ⑤ 유형별 버프 | `_factor5()`, `hit_type` 플래그 추가 |
| ⑥ 적 받는 대미지 | `_factor6()`, `hit_type` 플래그 추가 |
| ⑦ 우월 코드 | `_factor7()` |

타임라인 전용(`charge_speed_pct`, `max_ammo_pct` 등)은 `damage.py` 수정 불필요.

`hit_type`에 새 플래그 필요 시 `default_hit_type()`에도 추가.

### Step 3-E. `hp_below_count:threshold:N` timing

`[사용 횟수 별 효과]` + `체력 N% 이하 도달 시` 패턴에서 단계 구분 시 사용.

- `"hp_below_count:20:1"` — `hp_below:20` 이벤트 1번째 발생 시 발동
- `"hp_below_count:20:2"` — 2번째 발생 시 발동
- 각 단계에 `max_trigger:1` 병기 (전투 중 1회 제한)
- `_timing_match()`에 이미 구현됨. 새 threshold 추가 구현 불필요

### Step 3-F. `max_trigger` 동작 방식

`max_trigger: N` → 전투 중 최대 N회 발동. **추가 구현 불필요** — `BuffManager._activate()`에서 `_trigger_counts: dict[int(effect_id) → int]`로 추적·자동 차단.

- 모든 type(buff/instant/damage/weapon_change) 동일 적용
- 버프 만료 후 재발동 시도도 차단 (전투 중 누적 횟수 기준)
- `reset()` 시 `_trigger_counts`도 초기화

### Step 3-G. HP 모델

`state["hp"]` (현재 체력 절대값) + `state["hp_pct"]` (비율, 0~100) 항상 동기화. `state["hp_pct"]`는 읽기 전용, 직접 쓰지 않음.

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

새 timing/condition 사용 캐릭터 → `buff_manager.py` 수정.

**새 timing 추가**

`_timing_match()` 메서드에 분기 추가:

```python
# 예: "new_event:N" 형태
if timing.startswith("new_event:") and event == "new_event":
    raw = timing.split(":")[1]
    if not raw.lstrip("-").isdigit(): return False
    return count % int(raw) == 0
```

그 후 timeline에서 해당 이벤트 발생 시점에 `bm.notify("new_event", t, caster)` 호출 추가.

**새 condition 추가**

활성화 시점 1회 평가 → `_condition_ok()`에 추가.
매 `get_buffs()` 호출 시 재평가(상태 의존) → `_runtime_condition_ok()`에 추가.

| 평가 시점 | 추가 위치 |
|----------|----------|
| 버프 발동 시 1회 | `_condition_ok()` |
| 대미지 계산 시마다 | `_runtime_condition_ok()` |

### Step 5 — 새 target 유형 추가 시

새 target 패턴 사용 캐릭터 → `buff_manager.py` 수정.

**5-A. `_resolve_target()` 분기 추가**

```python
if target.startswith("새_패턴:"):
    n = int(target.split(":")[1])
    # 대상 목록 계산 후 반환
    return ...
```

**5-B. 스탯 비교 기반 target이면 `_LAZY_RESOLVE_PREFIXES`에 추가**

아군 스탯(공격력·체력·방어력 등) 비교로 대상을 정하는 target은 모든 버프 적용 후 순위 결정 필요. 이런 패턴은 반드시 `_LAZY_RESOLVE_PREFIXES` 튜플에 추가:

```python
_LAZY_RESOLVE_PREFIXES = (
    "allies_lowest_atk_burst3:",
    "allies_top_atk:",
    ...
    "새_스탯_비교_패턴:",   # ← 추가
)
```

- `_LAZY_RESOLVE_PREFIXES` 포함 target → `_activate()` 시점에 resolve 안 하고 `target_chars=None`으로 저장.
- `get_buffs()` 호출 시점에 `_resolve_target()` 실행 → 그 시점 버프 반영 스탯으로 순위 결정.
- 스쿼드 순서·위치·무기·클래스 등 고정 속성 기반 target은 lazy resolve 불필요.

**5-C. `_effective_atk()` 확장이 필요한 경우**

새 target이 공격력 기준 정렬 사용 + `atk_pct`/`atk_flat` 외 추가 버프 스탯이 공격력에 영향 → `_effective_atk()` stat 수집 범위 확장.

### Step 6 — 검산

`damage.py` 하단 `__main__` 블록에 새 stat 검증 케이스 추가 후 실행:

```bash
python calculator/damage.py
```

새 timing/target 추가 시 `simulate()` 실행 후 로그나 `SimResult.hits`로 발동 여부 직접 확인.

---

## stat 마스터 테이블

`parsed_skills.json` 모든 stat 구현 상태 단일 관리.
**새 stat 파싱 시 반드시 이 테이블 먼저 업데이트 후 Step 1~4 진행.**

구현 상태 범례:
- ✅ 완전 구현 (파싱 → 계산까지 반영)
- ⚠️ 부분 구현 (buffs에 집계되나 계산 미반영, 또는 조건부 미지원)
- ❌ 미구현 (buffs에도 없음. 파싱은 되나 계산 무효)
- 🚫 보류 (지원 계획 없음 — 해당 모델 자체 없음)

### buff stat

| stat (parsed_skills) | buffs 키 | DealForm | 구현 상태 | 비고 |
|---|---|---|---|---|
| `atk_pct` | `atk_pct` | ② | ✅ | |
| `hp_caster_based_pct` | — | — | ✅ | 최대+현재 체력 동반 증가 (시전자 base_hp × val%). `effective_max_hp()`에 flat 합산. 만료 시 현재 체력 캡 |
| `hp_only_caster_based_pct` | — | — | ✅ | 최대 체력만 증가, 현재 체력 유지 (시전자 base_hp × val%). `effective_max_hp()`에 flat 합산. 만료 시 현재 체력 캡 |
| `def_caster_based_pct` | `def_caster_based_pct` | — | ⚠️ | buffs에 집계되나 DPS 계산 미사용 |
| `def_pct` | `def_pct` | — | ⚠️ | base_stat 재계산용. 현재 timeline 미반영 |
| `max_hp_pct` | `max_hp_pct` | — | ✅ | 최대+현재 체력 동반 증가. `state["hp"]` 동기화 |
| `max_hp_only_pct` | `max_hp_only_pct` | — | ✅ | 최대 체력만 증가. `state["hp"]` 유지 |
| `atk_caster_based_pct` | — | ② | ✅ | `get_buffs()` 후처리에서 시전자 ATK × (val/100) → 수령자 `atk_flat`에 합산. `_STAT_TO_BUFF` 매핑 없음 |
| `atk_from_hp_pct` | — | ② | ✅ | `get_buffs()` 후처리에서 `effective_max_hp(caster) × (val/100)` → `atk_flat`에 합산. `_STAT_TO_BUFF` 매핑 없음 |
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
| `max_ammo_flat` | `max_ammo_flat` | — | ✅ | 타임라인 처리. `_finish_reload()`에서 `max_ammo_pct`와 함께 적용 |
| `pellet_count` | `pellet_count` | — | ✅ | 타임라인 처리. `_fire()`에서 기본 펠릿 수에 가산 |
| `pellet_count_fixed` | `pellet_count_fixed` | — | ✅ | 타임라인 처리. `>0`이면 `_fire()`에서 펠릿 수를 절대값으로 고정 |
| `charge_speed_pct` | `charge_speed_pct` | — | ✅ | 타임라인 처리. 차지 시간에 반영 |
| `charge_speed_caster_based_pct` | `charge_speed_pct` | — | ✅ | `_get_value()`에서 시전자 `charge_time` 기준 환산 후 `charge_speed_pct`로 합산 |
| `charge_time_caster_based` | — | — | ❌ | 차지 시간 절대값 감소. 미구현. `charge_speed_pct` 환산과 별도 |
| `charge_speed_overflow_conversion_pct` | `charge_speed_overflow_conversion_pct` | ④ | ✅ | 차지 속도 합산이 100% 초과 시, `overflow × N / 100` 만큼 `charge_dmg_pct`에 합산. `get_buffs()` 면역 처리 직후 후처리. 레드 후드 전용 |
| `reload_speed_pct` | `reload_speed_pct` | — | ✅ | 타임라인 처리. 재장전 시간에 반영 |
| `attack_speed_pct` | `attack_speed_pct` | — | ✅ | 타임라인 처리. `_current_fire_rate()`에서 발사 속도에 반영 |
| `accuracy_pct` | `accuracy_pct` | — | ⚠️ | buffs에 집계되나 DPS 계산 미사용 |
| `burst_charge_speed_pct` | — | — | 🚫 | 버스트 게이지 모델 단순화로 보류 |
| `optimal_range_max` | — | — | ❌ | 최대 적정 사거리 증가. 미구현 |
| `optimal_range_min` | — | — | ❌ | 최소 적정 사거리 % ▲. 미구현 |
| `explosion_range` | — | — | ❌ | 폭발 범위 증가. 미구현 |
| `pierce_range` | — | — | ❌ | 관통 범위 증가. 미구현 |
| `pierce_enabled` | `pierce_enabled` | — | ✅ | boolean 플래그. `get_buffs()` boolean 분기에서 `True` 세팅. `_fire()`/`_tick_charge()`에서 `is_pierce_damage`에 반영 |
| `fullburst_duration` | `fullburst_duration` | — | ✅ | 게임 내 동작은 instant이나, `switching→full_burst` 진입 시점에 값을 읽어야 하므로 buff로 등록해 보관. `BurstController.tick()`의 switching 단계에서 `bm._active`를 순회해 합산 후 `_full_burst_end_t` 결정. `burst_cast` 타이밍으로 등록된 버프는 해당 캐릭터가 이번 사이클의 3단계 발동자(`_fb_caster`)일 때만 반영 — 본인 버스트 때만 지속 시간을 바꾸는 캐릭터 지원. 모든 풀버스트에 적용되는 캐릭터는 `passive` 등 다른 타이밍을 사용하면 `_fb_caster` 조건 없이 항상 반영됨 |
| `effect_interval` | — | — | ✅ | `_dispatch_instant` 내부 처리. `target_effect` 필수 |
| `dmg_scale_mag_pct` | — | — | ✅ | 특정 효과(`target_effect`)의 대미지 배율 N% ▲. `_handle_damage_eff`에서 `bm._active`를 탐색해 `stat=="dmg_scale_mag_pct" and target_effect==eff_name`인 버프를 찾아 `coeff *= (1 + mag/100)` 적용. `_STAT_TO_BUFF` 매핑 없음 (`buff` type으로 `_active`에 등록됨) |
| `atk_buff_mag_pct` | — | ② | ✅ | 특정 named buff(`target_effect`)의 `atk_caster_based_pct` 값 N% ▲. `get_buffs()` 후처리 `atk_caster_based_pct` 루프 안에서 `atk_buff_mag_pct` 버프를 탐색해 `coeff * (1 + N/100)` 배율 적용. `_STAT_TO_BUFF` 매핑 없음 |
| `lifesteal_pct` | `lifesteal_pct` | — | ✅ | 대미지 × lifesteal_pct% 만큼 시전자 HP 회복. `event:heal_received` 발생 |
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
| `next_shield_hp_pct` | — | — | ❌ | 다음 보호막 체력 N% ▲. 보호막 모델 없음 |
| `accumulate_max_scale_pct` | — | — | ❌ | 특정 효과의 최대 누적량 N% ▲. `target_effect` 필수. 미구현 |
| `heal_overcharge_store` | — | — | ❌ | 초과 회복 저장. 미구현 |
| `heal_overcharge_store_atk_pct` | — | — | ❌ | ATK N%까지 받는 회복량 저장. 힐 모델 없음 |
| `shield_restore_pct` | — | — | ❌ | 보호막 회복 ▲. 보호막 모델 없음 |
| `burst_dmg_single_pct` | — | — | ❌ | 단일 대상 버스트 대미지 ▲. 미구현 (`burst_dmg`로 통합 필요 또는 별도 처리) |
| `burst_dmg_aoe_pct` | — | — | ❌ | 전체 대상 버스트 대미지 ▲. 미구현 |
| `burst_cooldown` | `burst_cooldown` | — | ✅ | buff 상태로 지속. 타임라인 `_effective_burst_cool()`에서 반영 |
| `skill_cooldown` | — | — | ❌ | 개별 스킬 쿨타임 초 감소. 미구현. `target_effect` 필요 |
| `skill_cooldown_pct` | `skill_cooldown_pct` | — | ⚠️ | 스킬 쿨타임 % 감소. `tick()`의 `every:Ns` interval에 반영. `target_effect` 미지원 — target 캐릭터의 모든 `every:Ns` 스킬에 일괄 적용 |
| `stun` | — | — | ✅ | 기절. `bm.is_stunned(name)`: `_active`에서 `stat=="stun"` 버프 유무로 판별. 일반공격(`CharState.tick()`)·버스트 사용(`BurstController._try_use_stage()`) 차단. 기절 중 버스트 단계는 만료까지 매 프레임 재시도 |
| `invincible` | — | — | ❌ | 무적. 피격 모델 없음 |
| `undying` | — | — | ❌ | 불굴. 피격 모델 없음 |
| `stealth` | — | — | ❌ | 은신. 타겟팅 모델 없음 |
| `decoy` | — | — | ❌ | 분신 생성. 미구현 |
| `infinite_ammo` | — | — | ❌ | 장탄 무한. 미구현 |
| `focus_fire` | — | — | ❌ | 사격 집중. 미구현 |
| `enemy_movement_disable` | — | — | ❌ | 적 이동 불가. 적 이동 모델 없음 |
| `debuff_immune` | `debuff_immune` | — | ✅ | `_activate()`에서 harmful 효과 차단 |
| `debuff_immune:[name]` | — | — | ✅ | `_activate()`에서 `debuff_immune:{eff_name}` 차단. `_has_immune()` 직접 탐색으로 `_STAT_TO_BUFF` 매핑 불필요 |
| `stun_immune` | `stun_immune` | — | ✅ | `bm.is_stunned()`에서 `_has_immune(name, "stun_immune")` 체크로 기절 차단 |
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

`_STAT_TO_BUFF` 매핑 없음. 타임라인 `_handle_damage_eff()`에서 직접 처리.

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

`_STAT_TO_BUFF` 매핑 없음. `_dispatch_instant()` 또는 타임라인 핸들러로 처리.

| stat | 처리 위치 | 구현 상태 | 비고 |
|---|---|---|---|
| `burst_cooldown_reduce` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `ammo_charge_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `ammo_charge_flat` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `burst_charge_pct` | — | 🚫 | 버스트 게이지 모델 단순화로 보류 |
| `heal_hp_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | `state["hp"]` 갱신 후 `hp_pct` 재동기화 |
| `buff_stack_add` | `_dispatch_instant()` | ✅ | |
| `buff_stack_remove` | `_dispatch_instant()` | ✅ | |
| `buff_stack_init` | `_dispatch_instant()` | ✅ | `target_effect` 버프가 없을 때만 N 스택으로 초기 생성. `_effects`에서 버프 정의 조회 후 `ActiveBuff` 직접 생성 |
| `debuff_stack_add` | `_dispatch_instant()` | ✅ | |
| `debuff_stack_remove` | `_dispatch_instant()` | ✅ | |
| `remove_named_buff` | `_dispatch_instant()` | ✅ | `target_effect` 필수 |
| `debuff_cleanse` | `_dispatch_instant()` | ✅ | |
| `enemy_buff_cleanse` | — | 🚫 | 적 버프 모델 없음 |
| `force_reload` | — | ❌ | `CharState._start_reload()` 강제 호출 필요. 미구현 |
| `targeting_exclude` | — | ❌ | 공격 대상 타겟팅 제외. 타겟팅 모델 없음 |
| `heal_overcharge_discharge` | — | ❌ | 저장된 회복량 방출. `target_effect` 필수. 힐 모델 없음 |
| `current_hp_reduce` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `cover_heal_pct` | — | 🚫 | 엄폐 모델 없음 |
| `burst_reentry` | — | ❌ | `_check_reenter()` 경로와 별도. 미구현 |
| `revive` | — | 🚫 | 전투불능 모델 없음 |
| `gauge_charge` | `_dispatch_instant()` | ✅ | `gauge_id` 필수 |
| `gauge_consume` | `_dispatch_instant()` | ✅ | `gauge_id` 필수 |
| `gauge_consume_as_ammo` | `_dispatch_instant()` | ✅ | `gauge_id` 필수. 소모량만큼 `squad_ammo_consume` notify 발생 |
| `force_move` | — | 🚫 | 복잡 메카닉, `_unparseable` |

---

---

## trigger/condition 마스터 테이블

**새 timing/condition 파싱 시 반드시 이 테이블 업데이트.**

구현 상태 범례:
- ✅ 완전 구현 (`_timing_match` / `_condition_ok` / `_runtime_condition_ok`에 분기 있음)
- ⚠️ 부분 구현 (매칭 로직은 있으나 notify 호출처 없음)
- ❌ 미구현 (분기 자체 없음)

### timing

| timing | 구현 상태 | 발생 위치 / 비고 |
|---|---|---|
| `battle_start` | ✅ | `bm.battle_start()` |
| `passive` | ✅ | `battle_start` 이벤트로 처리. 영구 지속, `_runtime_condition_ok`에서 매 프레임 재평가 |
| `full_burst_start` | ✅ | `bm.notify("full_burst_start", ...)` |
| `full_burst_start_count:N` | ✅ | `full_burst_start` 이벤트의 N번째 이상 매번 발동 (count >= N). 하위 효과 중복 적용 패턴 표준형 |
| `full_burst_start_exact:N` | ✅ | `full_burst_start` 이벤트의 정확히 N번째만 발동 (count == N). 예외적 1회성 패턴 전용 |
| `full_burst_end` | ✅ | `bm.notify("full_burst_end", ...)` |
| `full_burst_end_count:N` | ✅ | `full_burst_end` 이벤트의 N번째 이상 매번 발동 (count >= N) |
| `burst_enter:N` | ✅ | `bm.notify("burst_enter:N", ...)` |
| `burst_cast` | ✅ | `bm.notify("burst_cast", ...)` |
| `burst_cast_count:N` | ✅ | `burst_cast` 이벤트의 N번째 발생 시 |
| `squad_burst_cast:N` | ✅ | `bm.notify("squad_burst_cast:N", ...)` |
| `hit_count:N` | ✅ | `bm.notify("hit_count", ...)`. `trigger_count_reduce` 버프로 N 감소 가능 |
| `hit_count:[스킬명]:N` | ✅ | named damage effect 명중 N회마다 발동. `_timing_match()`에 분기 추가. 타임라인 `_handle_damage_eff()` hit 루프 안에서 `bm.notify("hit_count:{eff_name}", t, caster)` 호출 |
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
| `event:ally_hp_below:N` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 아군 HP 감소 모델 없어 notify 호출처 없음 |
| `event:self_down` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:part_destroy` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:enemy_spawn` | ✅ | `battle_start()` 시점에 모든 스쿼드원에서 notify. 단일 보스 가정 — 전투 시작 시 적 등장 처리 |
| `event:target_spawn` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:heal_received` | ⚠️ | 매칭 로직(`event:xxx`) 있음. `heal_hp_pct` 핸들러에서만 notify 발생 |
| `event:shield_applied` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 보호막 모델 없음 |
| `event:shield_consumed` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 보호막 모델 없음 |
| `event:cover_hit` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:projectile_destroy` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:ally_burst_cast` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:stat_applied:dot_dmg_pct` | ✅ | `_activate()` 후처리에서 `dot_dmg_pct` stat 버프 신규/갱신 등록 시 각 target_char에게 `notify("event:stat_applied:dot_dmg_pct", t, tgt)` 발생 |
| `event:stat_applied:split_dmg_pct` | ✅ | 동일. `split_dmg_pct` stat 버프 적용 시 발생 |
| `event:state_end:[상태명]` | ✅ | `tick()`에서 버프 만료 시 자동 발생 |
| `event:[상태명/스킬명]` | ✅ | `_activate()`에서 named buff 최초 등록 시 `notify(f"event:{name}", ...)` 자동 발생. 타임라인 별도 추가 불필요 |
| `hp_below:N` | ⚠️ | `_timing_match`에 분기 있음. 체력 변화 시 `bm.notify("hp_below:N", ...)` 호출처 없음 |
| `hp_below_count:N:순서` | ⚠️ | `_timing_match`에 분기 있음. `hp_below:N` 이벤트 발생처 없음 |
| `every:Ns` | ✅ | `tick()`에서 내부 타이머로 처리. notify 경로 아님 |
| `every_stack:N` | ❌ | 미구현. `_timing_match`에 분기 없음 |
| `stack_reach:버프명:N` | ✅ | `_activate()`에서 스택이 N에 도달하는 순간 `notify("stack_reach:버프명:N")` 발생. `_timing_match`에 분기 있음. 스택 리셋 후 재도달 시 재발동 |
| `on_attack` | ✅ | `bm.notify("on_attack", ...)` — `_fire()` (자동사격: SG/AR/SMG/MG) 및 `_tick_charge()` (풀차지 발사: SR/RL) 두 경로에서 모두 발생 |
| `first_trigger` | ❌ | 미구현. `max_trigger:1`로 대체 가능 |
| `multi_hit:N` | ✅ | `_timing_match`에 분기 있음. `bm.notify("multi_hit:N", ...)` — 타임라인에서 동시 명중 감지 필요 |
| `part_hit_count:N` | ✅ | `notify_team_hit("squad_part_hit", t, attacker)` 스쿼드 브로드캐스트. `_team_hit_index` 경로. `enemy.has_parts=True`일 때 비코어 히트마다 발생. `_activate(eff, attacker, t)`로 target:"self"=발사 아군 |
| `body_hit_count:N` | ✅ | `notify_team_hit("squad_body_hit", t, attacker)` 스쿼드 브로드캐스트. `_team_hit_index` 경로. `enemy.has_parts=False`(기본값)일 때 비코어 히트마다 발생 |
| `charge_hold:N` | ✅ | `_timing_match`에 분기 있음. `bm.notify("charge_hold:N", ...)` — 타임라인에서 차지 유지 감지 필요 |
| `weapon_hit:[name]` | ✅ | `_timing_match`에 분기 있음. `bm.notify("weapon_hit:[name]", ...)` — weapon_change 발사 시 타임라인이 notify |
| `squad_ammo_consume:N` | ❌ | 미구현. `_timing_match`에 분기 없음 |

### condition

평가 위치:
- `_condition_ok()`: 버프 발동 시점 1회 (notify 시)
- `_runtime_condition_ok()`: `get_buffs()` 호출마다 (상태 의존 조건)

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
| `back_row` | `_condition_ok` 전용 | ✅ | 스쿼드 인덱스 1 또는 3 = 후열 (포지션 2번, 4번) |
| `squad_ally_exists` | `_condition_ok` 전용 | ✅ | 5인 스쿼드에서 항상 True (스킵 처리) |
| `focusing` | — | ❌ | 미구현. `focus_fire` stat과 연동 필요 |
| `not_core` | — | ❌ | 미구현. hit_type 연동 필요 |
| `core_hit_count:1` | — | ❌ | 미구현. timing이 아닌 condition으로 쓰일 때 |
| `self_state:상태명` | 양쪽 모두 | ✅ | `_active`에서 해당 name 버프 존재 여부 확인 |
| `not_self_state:상태명` | 양쪽 모두 | ✅ | `_active`에서 해당 name 버프 부재 여부 확인 |
| `target_state:상태명` | 양쪽 모두 | ✅ | 단일 적 가정: `"__enemy__"`가 target_chars에 있는 활성 효과로 확인 |
| `target_code:[코드]` | `_condition_ok` 전용 | ✅ | 대상(적)의 속성 코드 확인. `self.state["enemy"]["code"]`와 비교. 코드 미설정(빈 문자열)이면 항상 통과 |
| `self_stack_above:스택명:N` | 양쪽 모두 | ✅ | `_active`에서 스택 수 확인 |
| `gauge_above:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `gauge_below:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `gauge_eq:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `has_burst1_ally` | `_condition_ok` 전용 | ✅ | `state["burst_stages"]` |
| `no_defender_ally` | `_condition_ok` 전용 | ❌ | 미구현. 분기 없음 |
| `has_defender_ally` | `_condition_ok` 전용 | ❌ | 미구현. 분기 없음 |
| `no_burst1_ally` | `_condition_ok` 전용 | ✅ | `state["burst_stages"]` |

---

## target 마스터 테이블

**새 target 파싱 시 반드시 이 테이블 업데이트.**

구현 상태 범례:
- ✅ 완전 구현 (`_resolve_target()`에 분기 있음)
- ❌ 미구현 (분기 없음 — 빈 리스트 반환)

lazy resolve: 버프 반영 스탯 기준 정렬 필요 target → `_activate()` 아닌 `get_buffs()` 시점에 resolve → `_LAZY_RESOLVE_PREFIXES`에 등록 필요.

| target | lazy resolve | 구현 상태 | 비고 |
|---|:---:|---|---|
| `"self"` | ❌ | ✅ | |
| `"all_allies"` | ❌ | ✅ | |
| `"all_allies_excl_self"` | ❌ | ✅ | |
| `"allies:N"` | ❌ | ✅ | 스쿼드 입력 순서 앞 N명 |
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
| `"allies_burst3"` | ❌ | ✅ | 기본 버스트 단계가 Step 3인 아군 전체. `burst_stages` 기준 |
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
| `"enemies_code:코드"` | ❌ | ✅ | `__enemy__` 센티널 반환. 단일 적 시뮬레이터에서는 코드 필터 무시 |
| `"enemies_lowest_hp_code:코드:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 단일 적 시뮬레이터에서는 코드 필터 무시 |
| `"all_projectiles"` | ❌ | ❌ | 발사체 모델 없음. 빈 리스트 반환 |
| `"self_cover"` | ❌ | ❌ | 엄폐 모델 없음. 빈 리스트 반환 |
| `"allies_lowest_cover_hp:N"` | ❌ | ❌ | 엄폐물 체력 수치 기준 정렬. 엄폐 모델 없음. 빈 리스트 반환 |
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


## 회귀 테스트 운영 방침

계산기 로직 수정 후 기존 수치 변화 없는지 확인.

### 스쿼드 스펙 (`make_char` 기본값)

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

### 운영

- 실행: `python -m context.regression_test`
- 판정: 스쿼드별 단발 1회 시행, ±3σ 범위 내이면 PASS
- 3σ 초과: **재시도 없이 FAIL 처리**. 실제 딜 수치 보고 후 작업 중단. 유저 판단 후 지시 대기.
- 기준값(총 딜 평균 ±3σ, 캐릭터별 평균) 단일 출처: `context/regression_test.py`의 `SQUADS`. 수치 변경 시 해당 파일만 수정.
- 기준값 갱신: 의도한 변경 후 **30회 재측정** → `regression_test.py`만 업데이트
