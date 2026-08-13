# 신규 버프/스탯 추가 유지보수 가이드

신규 캐릭터 추가: `char-add` 스킬 — 단계 2 파싱(Phase A+B) · 단계 4 구현(Phase C+D).

---

## 신규 stat/timing 추가 체크리스트

신규 stat 종류 생기면 아래 순서대로 수행.

---

## 체크리스트

### Step 1 — 이 문서의 stat 마스터 테이블에 등록 (정본)

**stat 로스터·구현상태·코드 위치의 정본은 이 문서다.** 새 stat은 아래 stat 마스터 테이블에 먼저 등록:

- 새 stat 이름(snake_case)과 설명(비고).
- DealForm 항목(①~⑦) 해당 여부 또는 타임라인 전용 명시.
- `buff` / `damage` / `instant` type 분류(해당 하위 테이블에 기입).

그다음, 한국어 스킬 텍스트 → 이 stat 키 선택이 헷갈릴 만하면 `PARSING.md` §6에 **매핑 단서만** 추가(선택). 텍스트→키 매핑의 정본은 PARSING §4~6, 구현상태의 정본은 이 문서 — 역할이 다르므로 양쪽 동시 편집이 아니다.

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

**주의**: `crit_rate` 계열은 `_CRIT_RATE_STATS` 집합에도 추가해야 크리확률 합산 경로를 탄다
(백분율 → 확률 환산 + 기본 15% 합산 + 100% 상한).
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

**보호막 모델**: `shield_from_max_hp_pct`는 대상별 보호막 상태를 만들며, 보호막량은
`시전자의 effective_max_hp × val%`이다. 같은 효과가 재발동하면 기존 `ActiveBuff`와 함께
보호막량·만료 시각도 갱신한다. 현재 아군 피격 모델이 없으므로 보호막은 지속시간 만료 전까지
소모되지 않는다.

- 보호막을 받은 각 대상에게 `event:shield_applied`를 통지한다.
- `during_shield`는 해당 캐릭터에게 유효한 보호막이 하나 이상 있는지 판정한다.
- `event:shield_consumed`·`shield_restore_pct`는 아군 피격/소모 모델이 생길 때 구현한다.

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
| `def_pct` | `def_pct` / `enemy_def_down_pct` | ② | ⚠️/✅ | **아군 대상**은 base_stat 재계산용으로 timeline 미반영(⚠️). **적 대상**(예: 마르차나 : 마린 스터디 고위험 대상)은 `get_buffs`에서 `enemy_def_down_pct`로 라우팅되어 factor②에서 적 방어력 감소 적용(✅). `eff_def = 적방어력 × (1 + enemy_def_down_pct%)` |
| `max_hp_pct` | `max_hp_pct` | — | ✅ | 최대+현재 체력 동반 증가. `state["hp"]` 동기화 |
| `max_hp_only_pct` | `max_hp_only_pct` | — | ✅ | 최대 체력만 증가. `state["hp"]` 유지 |
| `atk_caster_based_pct` | — | ② | ✅ | `get_buffs()` 후처리에서 시전자 ATK × (val/100) → 수령자 `atk_flat`에 합산. `_STAT_TO_BUFF` 매핑 없음 |
| `atk_from_hp_pct` | — | ② | ✅ | `get_buffs()` 후처리에서 `effective_max_hp(caster) × (val/100)` → `atk_flat`에 합산. `_STAT_TO_BUFF` 매핑 없음 |
| `persona_state` | `persona_state` | — | ✅ | 페르소나 상태 마커 버프 (`values`/`fixed_value` 없음, boolean 플래그). 수치 기여 없이 상태 판정에만 쓴다 — `_has_persona_state()`가 이 stat 보유 여부로 `allies_burst3_persona_excl_self`를 판정. 퀸(마코토)·유키코 |
| `crit_rate` | `crit_rate` | ③ | ✅ | 기본 15% + 버프 **합연산**, 100% 상한 (`_CRIT_RATE_STATS`) |
| `normal_atk_crit_rate` | `crit_rate` | ③ | ✅ | `crit_rate`로 합산. `is_normal_atk=False` 시 분리 미지원 (근사) |
| `crit_dmg` | `crit_dmg` | ③ | ✅ | |
| `normal_atk_crit_dmg` | `crit_dmg` | ③ | ✅ | `crit_dmg`로 합산. `is_normal_atk=False` 시 분리 미지원 (근사) |
| `core_dmg_pct` | `core_dmg_pct` | ③ | ✅ | `core_dmg_pct`로 합산 |
| `part_dmg_pct` | `part_dmg_pct` | ⑤ | ✅ | `is_part=True` 히트에만 가산. **`is_part`는 원문이 파츠를 명시한 damage 효과(`hits_parts: true`)에만 붙고, `enemy["has_parts"]=True`일 때만 성립**한다 — 기본공격에는 붙지 않는다(유저 결정). `has_parts`는 `DEFAULT_ENEMY`(기본 `False`)·`context/sim.py --has-parts`·보고서 스펙 `enemy`로 노출. `squad_part_hit`/`squad_body_hit` 이벤트 라우팅도 같은 키를 쓴다. 영향: 신데렐라 : 크리스탈 웨이브 `디스트로이`→`모드 스왑 2`. 레이븐 `급소 공략`·스노우 화이트 : 헤비암즈 `어나더 화이트 파츠대미지`는 짝이 되는 `hits_parts` 효과가 없어 아직 무효 |
| `intercept_dmg_pct` | — | — | 🚫 | 저지 부위 공격 대미지. **구현하지 않는다 — 발동 조건을 언제나 미달성으로 둔다**(유저 결정, 2026-08-11). 계산기 적 모델에 저지 부위가 없어 딜 기여가 영구히 0이다. 파싱은 정상 등록하고 시나리오에는 네거티브 항목으로 둔다. 보유: 누아르 `피날레 3`·`피날레 5` |
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
| `charge_time_flat` | `charge_time_flat` | — | ✅ | 차지 시간 절대값 N초 가감(텍스트 `차지 시간 N초 ▼` → 음수). 시전자 기준 환산이 없는 **순수 절대값**이라 `charge_time_caster_based`와 별도 키다. 타임라인 처리 — `_effective_charge_time()`이 `charge_speed_pct`를 적용한 **뒤** 더하고 0에서 하한(`charge_time_fixed`가 있으면 그쪽이 먼저 이겨서 무시된다). 마나 `매터 시그마 4` |
| `charge_speed_overflow_conversion_pct` | `charge_speed_overflow_conversion_pct` | ④ | ✅ | 차지 속도 합산이 100% 초과 시, `overflow × N / 100` 만큼 `charge_dmg_pct`에 합산. `get_buffs()` 면역 처리 직후 후처리. 레드 후드 전용 |
| `reload_speed_pct` | `reload_speed_pct` | — | ✅ | 타임라인 처리. 재장전 시간에 반영 |
| `attack_speed_pct` | `attack_speed_pct` | — | ✅ | 타임라인 처리. `_current_fire_rate()`에서 발사 속도에 반영 |
| `mg_warmup_speed_pct` | `mg_warmup_speed_pct` | — | ✅ | MG 예열 진행 속도 % (음수 = 감소). `_fire()`의 `warmup_shots` 증가량에 `(1 + val/100)` 배율 적용. -100이면 증가 0(예열 정지). 식음 속도는 영향 안 받음. **양수도 성립** — +100이면 예열 진행 2배(레이 (가칭) `정비 및 보급`). 같은 대상에 +100과 −100이 동시 활성이면 **단순 합산해 0(예열 정지)** 이 맞다(유저 확정) — 레이의 13초 예열 버프와 아스카 `긴급 수복 2`의 3초 감소가 겹치는 구간. 아스카 : WILLE, 레이 (가칭) |
| `accuracy_pct` | `accuracy_pct` | — | ⚠️ | DealForm 어느 항에도 안 들어간다. 단 `timeline.py`의 `_core_hit_prob()`가 탄착군 직경(`base_diameter - acc_slope × accuracy_pct`) 산출에 쓰므로 **코어 보유 적(`core_px > 0`)에서는 코어히트율을 통해 딜에 반영된다**. 기본 보스는 `core_px = 0`이라 무발동. 메카닉 조사 기록은 `context/scenarios/명중률 탄착군.md` |
| `burst_charge_speed_pct` | — | — | 🚫 | 버스트 게이지 모델 단순화로 보류 |
| `optimal_range_max` | — | — | ❌ | 최대 적정 사거리 증가. 미구현 |
| `optimal_range_min` | — | — | ❌ | 최소 적정 사거리 % ▲. 미구현 |
| `explosion_range` | — | — | ❌ | 폭발 범위 증가. 미구현 |
| `pierce_range` | — | — | ❌ | 관통 범위 증가. 미구현 |
| `pierce_enabled` | `pierce_enabled` | — | ✅ | boolean 플래그. `get_buffs()` boolean 분기에서 `True` 세팅. `_fire()`/`_tick_charge()`에서 `is_pierce_damage`에 반영 |
| `fullburst_duration` | `fullburst_duration` | — | ✅ | 게임 내 동작은 instant이나, `switching→full_burst` 진입 시점에 값을 읽어야 하므로 buff로 등록해 보관. `BurstController.tick()`의 switching 단계에서 `bm._active`를 순회해 합산 후 `_full_burst_end_t` 결정. `burst_cast` 타이밍으로 등록된 버프는 해당 캐릭터가 이번 사이클의 3단계 발동자(`_fb_caster`)일 때만 반영 — 본인 버스트 때만 지속 시간을 바꾸는 캐릭터 지원. 모든 풀버스트에 적용되는 캐릭터는 `passive` 등 다른 타이밍을 사용하면 `_fb_caster` 조건 없이 항상 반영됨 |
| `effect_interval` | — | — | ✅ | `target_effect`가 가리키는 `every:Ns` 효과의 주기를 **초 단위로 가감**. `tick()`의 `every:Ns` 루프에서 `_active`를 탐색해 `stat=="effect_interval" and target_effect==eff["name"]`인 버프 값을 합산, `base_interval + flat`에 `skill_cooldown_pct` 배율을 곱한다. `_STAT_TO_BUFF` 매핑 없음. `target_effect` 필수. 에이다 `섬광 수류탄 투척 발동 시간 조건` |
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
| `shield_from_max_hp_pct` | — | timeline | ✅ | 시전자의 유효 최대 체력 N%만큼 대상별 보호막 생성. 지속시간 동안 `during_shield` 활성, 적용 대상에게 `event:shield_applied` 통지 |
| `shared_shield_from_max_hp_pct` | — | timeline | ✅ | 아군 공용 보호막. 시전자의 유효 최대 체력 N%만큼 생성하되 **부여 대상은 시전자 1인**(텍스트에 대상 표기가 없어도 `all_allies`가 아니다). `_SHIELD_STATS`로 `shield_from_max_hp_pct`와 같은 경로를 타 `during_shield`·`event:shield_applied`도 동일하게 성립한다. 블랑 `럭키 가드` |
| `next_shield_hp_pct` | — | — | ❌ | 다음 보호막 체력 N% ▲. 다음 1회 증폭·소모 경로 미구현 |
| `accumulate_max_scale_pct` | — | — | ❌ | 특정 효과의 최대 누적량 N% ▲. `target_effect` 필수. 미구현 |
| `heal_overcharge_store` | — | — | ❌ | 초과 회복 저장. 미구현 |
| `heal_overcharge_store_atk_pct` | — | — | ❌ | ATK N%까지 받는 회복량 저장. 힐 모델 없음 |
| `shield_restore_pct` | — | — | ❌ | 보호막 회복 ▲. 아군 피격·보호막 소모 모델 없음 |
| `buff_max_stack_add` | — | — | ❌ | 중첩 가능 이로운 효과의 **중첩 한도(`max_stack`) N개 ▲**. 대상 버프를 특정하지 않고 대상 아군의 스택형 이로운 효과 전반에 적용. `ActiveBuff`의 max_stack을 런타임에 올리는 경로 필요. 플로라 |
| `burst_dmg_single_pct` | — | — | ❌ | 단일 대상 버스트 대미지 ▲. 미구현 (`burst_dmg`로 통합 필요 또는 별도 처리) |
| `burst_dmg_aoe_pct` | `burst_dmg_aoe_pct` | ⑤ | ✅ | 전체 대상 버스트 대미지 ▲. `_factor5()`의 `is_burst_damage` 블록 **안**에서 `hit_type["is_aoe_burst"]`일 때만 가산 — 구조적으로 `bonus_damage`가 탈 수 없다. 플래그는 `timeline.simulate` `_handle_damage_eff`가 `base_stat=="burst_damage" and target=="all_enemies"`로 세운다. **AoE 판정 기준**: 버스트 스킬의 대상 설명이 `적 전체에게`로 끝나는 효과 — `적 전체에게(파츠 포함)`처럼 괄호 부연이 붙어도 포함한다(레이븐). **같은 clause의 `bonus_damage`·`dot_damage`는 제외** — "버스트 스킬 대미지"만 증폭한다(이사벨 `타겟 마킹 2·3` 추가 대미지는 비대상, 유저 확인). 트리나 `뻗은 뿌리`/`시든 뿌리` |
| `burst_cooldown` | `burst_cooldown` | — | ✅ | buff 상태로 지속. `BurstManager.tick()`의 `full_burst_start` 분기가 풀버스트 1회당 1회씩 `burst_ready_at`을 당긴다 (`_cd_applied_at_cast`로 cast 시 반영분 중복 방지) |
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
| `charge_time_fixed` | `charge_time_fixed` | — | ✅ | 차지 시간을 `fixed_value`초로 **절대 고정**. 플래그는 `get_buffs()` 후처리에서 `charge_speed_pct = 0`, 실제 초는 `CharState._fixed_charge_time()`이 `bm._active`를 직접 읽는다. **무기 표기 차지 시간(base)을 후보에 넣지 않는다** — base보다 **짧게** 고정하는 경우가 있다(맥스웰 : 오디너리 미케닉 3.0초 모드 안에서 0.4초). 복수 활성이면 **최신값**(`activated_at`, `uid` 순)이 이긴다 — 고정값은 모드 진입/종료로 갈아끼워지는 형태가 정본(스노우 화이트 : 헤비암즈는 모드 종료 시 `event:state_end`로 원래 값을 재부여한다). `fixed_value` 없이 stat만 있으면 base 유지 = 차지 속도 버프만 무시(아니스 : 스타 `슈팅 스타2`) |
| `reload_time_fixed` | (타임라인 전용) | — | ✅ | 재장전 시간을 `fixed_value`초로 **절대 고정** — `reload_speed_pct`를 무시한다. `charge_time_fixed`와 같이 `fixed_value` 계열이라 `get_buffs()` 합산 경로를 타지 않고 `CharState._fixed_reload_time()`이 `bm._active`를 직접 읽는다(`_start_reload`에서 사용). **복수면 최대값** — `charge_time_fixed`와 달리 최신값이 아니다(이 stat은 갈아끼우는 사례가 아직 없어 기존 시맨틱 유지). `_STAT_TO_BUFF` 매핑 없음. 신데렐라 : 크리스탈 웨이브 `변경 준비` |
| `stack_change_immune` | `stack_change_immune` | — | ✅ | `_dispatch_instant()`에서 스택 변경 차단 |
| `atk_copy` | — | — | ❌ | 공격력 복제. 복잡 메카닉, `_unparseable` |
| `hp_copy` | — | — | ❌ | 체력 복제. 복잡 메카닉, `_unparseable` |
| `received_dmg_split` | — | — | ❌ | 받는 대미지 차등 분배. `_unparseable` |
| `heal_split` | — | — | ❌ | 회복 균등 분배. `_unparseable` |
| `armor_break_enabled` | `armor_break_enabled` | ②⑤ | ✅ | 일반 공격을 방어력 무시 대미지로 치환(boolean 플래그). `timeline.py`가 `buffs.get("armor_break_enabled")` → `is_armor_break_damage`로 읽고, `damage.py`가 ② 적 방어력 0 처리 + ⑤ `armor_break_dmg_pct` 가산. 치사토 `방어 관통 사격` |
| `gauge_charge_enabled` | — | — | ✅ | buff로 등록. 게이지 충전 가능 상태 활성화. `gauge_id` 필수 |
| `gauge_max_add` | — | — | ✅ | `_dispatch_instant()`의 `gauge_charge`에서 cap 합산 |
| `taunt` | `taunt` | — | ⚠️ | buffs에 집계되나 타겟팅 모델 없음 |
| `lock_on` | `lock_on` | — | ❌ | **스노우 화이트 : 헤비암즈 전용**. 세븐스 드워프 공격 대상 지정 고유 메카닉. `values`/`fixed_value` 없음 |
| `possessed` | — | — | ❌ | **일레그 : 붐 앤 쇼크 전용** 적 마커. `target_state:빙의` 조건 게이팅용. `_STAT_TO_BUFF` 매핑 없음 — `_active`에만 등록되어 name 기반 condition 매칭. `values`/`fixed_value` 없음 |
| `effect_target_count_add` | — | — | ❌ | 특정 효과의 **타격 대상 수** N 증가 (`target_effect` 필수, `fixed_value`에 증가량). 텍스트: `[효과명] 적용 대상 N ▲`. **단일 보스 sim에서는 항상 no-op** — 대상이 이미 1기로 수렴해 있다(`GAMEPLAY.md §condition`). 다수 적 지원 전까지 구현하지 않는다. 레이 (가칭) `섬멸 지원 4` (→ 아스카 : WILLE `섬멸 태세 추가 효과`) |
| `effect_range_pct` | — | — | ❌ | 특정 효과의 **공격 범위** % 증가 (`target_effect` 필수). 텍스트: `[효과명] 공격 범위 N% ▲`. 거리 모델이 없어 **항상 no-op**. 레이 (가칭) `섬멸 지원 5` |

### damage stat

`_STAT_TO_BUFF` 매핑 없음. 타임라인 `_handle_damage_eff()`에서 직접 처리.

| stat | hit_type 플래그 | 구현 상태 | 비고 |
|---|---|---|---|
| `damage` | `is_normal_atk=True` (일반공격) / `False` (스킬) | ✅ | |
| `auto_damage` | `is_normal_atk=True`, `damage_formula: "normal_attack"` | ✅ | |
| `burst_damage` | `is_burst_damage=True` | ✅ | |
| `dot_damage` | `is_dot=True` | ✅ | `tick_interval` 기반 |
| `dot_damage` + `target: "same_target:[name]"` | `is_dot=True` | ✅ | **짝 공격이 한 발씩 중첩을 얹고, 얹는 즉시 그 중첩 수로 1틱을 때리는 DoT**. 램프 구간의 총 배율이 삼각수가 된다(N=10이면 1+2+…+10 = **55배**). `_same_target_ramp_hits()`가 짝 효과의 `stat` suffix로 N을 읽고, `_activate()`가 `_ramp_pending`에 **`ramp_interval`초 간격으로 예약**하며, `tick()`이 주기 틱 블록보다 **먼저** 소화한다. 주기 틱은 마지막 중첩 부여 +`tick_interval`부터 잇고 만료도 그 시점 기준으로 다시 잡는다(스택 부여는 지속시간을 갱신한다 — GAMEPLAY §버프 스택). **램프를 한 시점에 몰아 쏘면 안 된다** — 지속 대미지는 맞는 순간의 버프로 계산되므로(§값 산정) 램프 전체가 풀버스트 경계 밖으로 밀려 딜이 과소평가된다. 사쿠라 : 블룸 인 서머 `화양연화 2` |
| `split_damage` | `is_split=True` | ✅ | |
| `bonus_damage` | — | ✅ | `timing: "burst_cast"` 시 **3버스트 캐릭터만** `_pending_burst_dmg`에 보류하고 `full_burst_start`에서 계산한다(유저 확인) — 풀버스트는 B3 발동 직후 시작하므로 B3의 추가 대미지만 풀버스트 버프를 받는다. B1/B2는 풀버스트보다 몇 초 앞서 발동하므로 `burst_cast` 시점 버프로 즉시 계산(헬름 : 아쿠아마린 `이지스 캐논 오버로드 2`). 보류된 B3 딜은 계산이 뒤로 밀려 원문 블록 순서가 깨지므로, `_later_burst_cast_buffs()`가 "이 딜보다 **뒤에** 서술된 같은 `burst_cast` buff" 이름을 모아 `get_buffs(exclude_names=...)`로 제외한다 (GAMEPLAY.md §효과 실행 순서. 로산나 `벤데타` ← `벤데타 2` 받는 대미지) |
| `armor_break_damage` | `is_armor_break_damage=True` | ✅ | ②에서 적 방어력 0 처리 |
| `first_damage_coeff` (weapon_change 필드) | — | ✅ | stat이 아니라 **`type: "weapon_change"` 항목의 필드**. 원문 `최초 대미지` / `일반 대미지` 2단 계수에서 **모드 진입 첫 발**에만 쓰는 계수(`damage_coeff`는 일반 대미지 쪽). `_tick_weapon_change()`가 레벨 환산해 `_wc_first_coeff`/`_wc_normal_coeff`에 싣고, 발사 직전 `_apply_wc_first_coeff()`가 `_wc_shots == 0`일 때만 첫 계수로 `self.weapon`을 갈아끼운다. **첫 발이 아닐 때 일반 계수로 되돌리는 게 필수** — 연사 24/s + dt 0.05s면 한 tick에 두 발이 나가므로, 되돌리지 않으면 같은 tick의 둘째 발까지 최초 대미지로 나간다. 필드가 없으면 `_wc_first_coeff`가 None이라 기존 동작 그대로. 보유: 라플라스 `라플라스 버스터`(1455.72 vs 22.2 @lv10) |
| `pierce_damage` | `is_pierce_damage=True` | ✅ | |
| `projectile_explosion_damage` | `is_projectile_explosion=True` | ✅ | RL 기본 공격에 자동 적용 |
| `projectile_attachment_damage` | `is_projectile_attachment=True` | ✅ | |
| `sequential_damage` | `is_sequential=True` | ✅ | `:N` suffix → hit_count |
| `<damage_stat>:[이름]` | 각 stat과 동일 | ✅ | `:N` suffix의 동적판 — 1트리거당 발사 횟수가 상수 N이 아니라 `ref_count(caster, 이름)`(게이지·버프 스택·소환체 수)다. 원래 `sequential_damage:이름`만 처리하던 분기를 모든 damage stat으로 일반화했다. 히트를 합치지 않고 수만큼 개별 발사해야 크리 판정·히트 수 집계가 맞는다. 아인 `armor_break_damage:니어 페더`, 메이든 : 아이스 로즈 `sequential_damage:MP` |
| `core_damage` | `is_core` + `is_core_damage` | ✅ | 코어 명중 판정 스킬 대미지(**확정 코어**, 확률 판정 없음). timeline이 `is_core=True`·`is_core_damage=True`를 세팅하고 `_factor3`이 `is_core and (is_normal_atk or is_core_damage)`로 코어 배율을 태운다 — 무기 `core_dmg_mult`(200%)와 `core_dmg_pct` 버프가 모두 실린다. 코어 유무 게이팅은 `core_hit` condition이 담당. 신데렐라 : 크리스탈 웨이브 `모드 스왑 3` |

### instant stat

`_STAT_TO_BUFF` 매핑 없음. `_dispatch_instant()` 또는 타임라인 핸들러로 처리.

| stat | 처리 위치 | 구현 상태 | 비고 |
|---|---|---|---|
| `burst_cooldown_reduce` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `skill_cooldown_reduce_pct` | `_dispatch_instant()` 내장 분기 | ✅ | **스킬 재사용 시간 N% ▼ (즉시 1회)** — 대상 캐릭터가 시전자인 `every:Ns` 효과의 **남은 시간**(`_next_fire[eid]`의 `next_t - t`)에 `(1 − N/100)`을 곱한다. `interval` 자체는 건드리지 않는다(다음 주기는 원래 길이로 복귀). `skill_cooldown_pct`(주기에 곱하는 buff)와 혼동 주의 — 이쪽은 잔여분만 깎는 instant다. `burst_cooldown_reduce`(초 단위 instant)의 % 스킬판. `target_effect` 미지원 — 시전자의 모든 `every:Ns`에 일괄 적용(`skill_cooldown_pct`와 같은 범위). 센티 `보수공사` |
| `ammo_charge_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `ammo_charge_flat` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `burst_charge_pct` | — | 🚫 | 버스트 게이지 모델 단순화로 보류 |
| `heal_hp_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | `state["hp"]` 갱신 후 `hp_pct` 재동기화 |
| `buff_stack_add` | `_dispatch_instant()` | ✅ | 스택 +N과 함께 **대상 버프의 지속시간도 갱신**한다(유저 확정: 일반 동작 — 원문 `[스택명 : ...] [N 중첩] [M초 유지]`는 버프를 다시 붙이는 문장이다). `duration: -1`(영구, `expires_at == inf`)은 갱신 대상 아님. 스택이 증가하면 `stack_reach:버프명:N`도 notify한다(`_activate()`와 동일). notify는 `_active` 순회가 끝난 뒤 emit — 순회 중 emit하면 재진입으로 리스트가 바뀐다 |
| `buff_stack_remove` | `_dispatch_instant()` | ✅ | |
| `buff_stack_init` | `_dispatch_instant()` | ✅ | `target_effect` 버프가 없을 때만 N 스택으로 초기 생성. `_effects`에서 버프 정의 조회 후 `ActiveBuff` 직접 생성 |
| `debuff_stack_add` | `_dispatch_instant()` | ✅ | |
| `debuff_stack_remove` | `_dispatch_instant()` | ✅ | |
| `remove_named_buff` | `_dispatch_instant()` | ✅ | `target_effect` 필수 |
| `debuff_cleanse` | `_dispatch_instant()` | ✅ | |
| `enemy_buff_cleanse` | — | 🚫 | 적 버프 모델 없음 |
| `force_reload` | timeline 핸들러 | ✅ | 시전자 `CharState.ammo = 0` 후 `_start_reload()` 강제 호출. 이미 재장전 중이면 스킵 |
| `targeting_exclude` | — | ❌ | 공격 대상 타겟팅 제외. 타겟팅 모델 없음 |
| `heal_overcharge_discharge` | — | ❌ | 저장된 회복량 방출. `target_effect` 필수. 힐 모델 없음 |
| `current_hp_reduce` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `cover_heal_pct` | — | 🚫 | 엄폐 모델 없음 |
| `burst_reentry` | — | ❌ | `_check_reenter()` 경로와 별도. 미구현 |
| `revive` | — | 🚫 | 전투불능 모델 없음 |
| `gauge_charge` | `_dispatch_instant()` | ✅ | `gauge_id` 필수 |
| `gauge_consume` | `_dispatch_instant()` | ✅ | `gauge_id` 필수 |
| `gauge_consume_as_ammo` | `_dispatch_instant()` | ✅ | `gauge_id` 필수. 소모량만큼 `squad_ammo_consume` notify 발생 |
| `squad_ammo_consume_as` | `_dispatch_instant()` | ✅ | "탄환 소모 N발" 표기 — 실제 장탄은 1발만 줄고 아군 탄 소비 총합 집계에서만 `fixed_value`발로 계상. **발사 자체가 이미 1발을 계상했으므로 핸들러는 `N-1`발만 추가 notify**한다(총 N발). `gauge_consume_as_ammo`(벨벳)와 달리 게이지 소모를 동반하지 않는다. 소비자인 `squad_ammo_consume:N`은 ✅ 구현이라 **스쿼드 DPS에 직결**(리틀 머메이드 `거품 난사` 등). 신데렐라 : 크리스탈 웨이브 `저격 모드 탄 소비 집계` |
| `named_buff_duration_extend` | `_dispatch_instant()` | ✅ | `target_effect` 필수. 해당 이름 및 `"이름 N"` 형태 부속 버프의 `expires_at += fixed_value`. 스쿼드 브로드캐스트 방식으로 발동. 적 대상 효과에도 걸린다 — `enemies_*`는 lazy가 아니라 즉시 `["__enemy__"]`로 풀리므로 연장 항목과 피연장 버프가 같은 센티널로 만난다. **DoT는 `_dot_timers`의 `expires_at`도 함께 늘린다** — 틱 스케줄이 `ActiveBuff`와 별도로 복사돼 있어 한쪽만 늘리면 표시만 길어지고 실제 틱은 원래 시각에서 끊긴다. 사쿠라 : 블룸 인 서머 `피어나다 3`(적측 `벚꽃잎` 연장). |
| `force_move` | — | 🚫 | 복잡 메카닉, `_unparseable` |
| `feather_refresh` | `_dispatch_instant()` | ✅ | **아인 전용**. 니어 페더 소환체를 슬롯 단위로 (재)소환한다. `feather_id`로 식별하고 `feather_slots`(슬롯별 지속시간 배열, `-1`=무제한) 길이만큼 소환하며, 이미 있던 슬롯도 **지속시간·공격 쿨을 초기화**한다(전투 시작 4기 / 버스트 6기 모두 같은 stat). 상태는 `state["feathers"][caster][feather_id]`. 주기 계산용 `feather_interval_base`·`feather_interval_mult`를 함께 싣는다. 소비자는 `feather_tick` timing과 `armor_break_damage:니어 페더`(`ref_count()`가 게이지와 같은 자리에서 생존 수를 돌려준다). 수치가 스킬 텍스트에 없는 추정치라 코드 상수가 아니라 JSON 필드로 둔다 — 정본: `context/scenarios/아인.md §니어 페더 메커니즘` |

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
| `event:cover` | ✅ | `_enter_cover()`에서 `bm.notify("event:cover", ...)`. **엄폐는 컨트롤로만 발생한다** — `control`의 장전컨 정책이나 명시 시퀀스가 엄폐 구간을 열 때만 발동하고, 컨트롤이 꺼진 시뮬에서는 한 번도 발동하지 않는다 (자동 사격이 디폴트라 니케가 스스로 엄폐하지 않기 때문). 정본: `context/CONTROL.md` |
| `event:ally_down` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:ally_hp_below:N` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 아군 HP 감소 모델 없어 notify 호출처 없음 |
| `event:adjacent_hp_below:N` | ✅ | 자신의 **양 옆 아군** 중 1기가 체력 N% 이하에 도달. `sync_hp()`가 등록된 임계값의 하향 전이를 감지하고, `allies_adjacent:2` 관찰자에게 notify. 플로라 |
| `event:adjacent_hp_max` | ✅ | 자신의 **양 옆 아군** 중 1기가 **최대 체력 도달**. `sync_hp()`가 hp_pct의 `<100 → 100` **전이(edge)** 를 감지해 `_notify_adjacent_hp_max()`로 발생시킨다(상시 만피는 전이가 없어 무발동). notify의 caster는 이웃이 아니라 **관찰자(효과 소유자)**. 시각은 `self._cur_t`(`tick()`·`notify()`에서 갱신), 재진입은 `_in_hp_edge`로 차단. 최대 체력만 증가 버프(`hp_only_caster_based_pct`·`max_hp_only_pct`)의 만료가 주 발생원. 플로라 |
| `event:self_down` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:part_destroy` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 보스 sim에서 파츠는 실제로 파괴되지 않으므로 **기본은 무발동**이고, `config["part_break_interval"]`(초, 0/미지정이면 OFF)을 주면 `timeline.simulate`가 그 주기마다 스쿼드 전원에게 notify한다. 아크레인저 블랙 `배터리 충전`, 사쿠라 : 블룸 인 서머 스킬1 전체 |
| `event:enemy_spawn` | ✅ | `battle_start()` 시점에 모든 스쿼드원에서 notify. 단일 보스 가정 — 전투 시작 시 적 등장 처리 |
| `event:target_spawn` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:heal_received` | ⚠️ | 매칭 로직(`event:xxx`) 있음. `heal_hp_pct` 핸들러에서만 notify 발생 |
| `event:shield_applied` | ✅ | `shield_from_max_hp_pct` 활성/갱신 시 보호막을 받은 각 대상에게 통지 |
| `event:shield_consumed` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 아군 피격·보호막 소모 호출처 없음 |
| `event:cover_hit` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:projectile_destroy` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:ally_burst_cast` | ⚠️ | 매칭 로직(`event:xxx`) 있음. notify 호출처 없음 |
| `event:stat_applied:dot_dmg_pct` | ✅ | `_activate()` 후처리에서 `dot_dmg_pct` stat 버프 신규/갱신 등록 시 각 target_char에게 `notify("event:stat_applied:dot_dmg_pct", t, tgt)` 발생 |
| `event:stat_applied:split_dmg_pct` | ✅ | 동일. `split_dmg_pct` stat 버프 적용 시 발생 |
| `event:state_end:[상태명]` | ✅ | `tick()`에서 버프 만료 시 자동 발생 |
| `event:[상태명/스킬명]` | ✅ | `_activate()`에서 named buff 최초 등록 시 `notify(f"event:{name}", ...)` 자동 발생. 타임라인 별도 추가 불필요. 통지 대상은 **기본 스쿼드 전체 브로드캐스트**이며, 효과에 `event_scope: "recipients"`가 있으면 실제 수령자에게만 통지한다 (`_event_audience()`). 서로 다른 캐릭터가 같은 이름의 상태를 각자 갖는 경우(퀸(마코토)·유키코의 `1more`) 남의 상태 변화로 트리거가 열리는 것을 막는다 |
| `hp_below:N` | ⚠️ | `_timing_match`에 분기 있음. 체력 변화 시 `bm.notify("hp_below:N", ...)` 호출처 없음 |
| `hp_below_count:N:순서` | ⚠️ | `_timing_match`에 분기 있음. `hp_below:N` 이벤트 발생처 없음 |
| `every:Ns` | ✅ | `tick()`에서 내부 타이머로 처리. notify 경로 아님 |
| `every_stack:N` | ❌ | 미구현. `_timing_match`에 분기 없음 |
| `stack_reach:버프명:N` | ✅ | 스택이 N에 도달하는 순간 `notify("stack_reach:버프명:N")` 발생. 발생처 **두 곳** — `_activate()`(일반 부여)와 `_dispatch_instant()`의 `buff_stack_add`. `_timing_match`에 분기 있음. 스택 리셋 후 재도달 시 재발동. **`[지속]`(`duration: -1`) 버프를 "최대 중첩 시" 조건으로 켤 때는 `self_stack_above` condition 대신 이 timing을 쓴다** — condition은 `_RUNTIME_COND_PREFIXES` 재평가로 중첩 해제와 함께 즉시 꺼진다(팬텀 `괴도의 시선 3`) |
| `on_attack` | ✅ | `bm.notify("on_attack", ...)` — `_fire()` (자동사격: SG/AR/SMG/MG) 및 `_tick_charge()` (풀차지 발사: SR/RL) 두 경로에서 모두 발생 |
| `first_trigger` | ❌ | 미구현. `max_trigger:1`로 대체 가능 |
| `multi_hit:N` | ✅ | `_timing_match`에 분기 있음. `bm.notify("multi_hit:N", ...)` — 타임라인에서 동시 명중 감지 필요 |
| `part_hit_count:N` | ✅ | `notify_team_hit("squad_part_hit", t, attacker)` 스쿼드 브로드캐스트. `_team_hit_index` 경로. `enemy.has_parts=True`일 때 비코어 히트마다 발생. `_activate(eff, attacker, t)`로 target:"self"=발사 아군 |
| `body_hit_count:N` | ✅ | `notify_team_hit("squad_body_hit", t, attacker)` 스쿼드 브로드캐스트. `_team_hit_index` 경로. `enemy.has_parts=False`(기본값)일 때 비코어 히트마다 발생 |
| `charge_hold:N` | ✅ | `CharState._notify_charge_hold()`(`timeline.py`)가 `_charge_full_t`(풀차지 도달 래치)로 유지 시간을 재서 notify한다. 임계값은 `BuffManager.charge_hold_thresholds(caster)`가 그 캐릭터의 효과에서 뽑는다 — `_timing_match`가 문자열 완전 일치라 원문 표기(`0.5`)를 그대로 보낸다. **판정은 한 차지에 1회**(`_charge_hold_fired`, 차지 재시작 시 리셋) — 계속 들고 있어도 재판정하지 않는다. **홀드 조작이 없으면 풀차지 즉시 발사라 영구 무발동**이다: `control["sequence"]`의 `hold`, 정책 `own_full_burst`·`charge_hold_after_fb` 중 하나가 필요하다 — 정본: `context/CONTROL.md §홀드`. 밀크 : 블루밍 바니 `부끄러움` |
| `weapon_hit:[name]` | ✅ | `_timing_match`에 분기 있음. notify 호출처는 `_handle_damage_eff()` **한 곳뿐**으로, `[name]`은 **named damage 효과**(발사체 등)의 이름이다 — 그 효과가 명중할 때마다 발생한다(라피 : 레드 후드 `부착형 유탄 4`). **`weapon_change` 모드의 사격은 이 이벤트를 쏘지 않는다** (`_tick_weapon_change()`에 호출처 없음, 2026-08-13 확인) → 모드의 매 발마다 붙는 효과는 `hit_count:1` + `self_state:[모드명]`으로 센다 |
| `feather_tick` | ✅ | **아인 전용**. 니어 페더 소환체의 공격 주기 틱. `tick()`이 `state["feathers"]`를 돌며 `notify("feather_tick", ...)`. `_timing_match`는 `timing == event` 일반 분기를 그대로 탄다(별도 분기 불필요). 주기가 고정이 아니라 생존 수 n에 대해 `base × mult^(n-1)`이고, **다음 발사는 직전 예약 시각 기준**으로 잡는다(프레임 양자화 드리프트 방지). 만료로 수가 줄어도 예약된 시각은 바뀌지 않는다 — 재소환(`feather_refresh`)만 초기화한다. 정본: `context/scenarios/아인.md §니어 페더 메커니즘` |
| `squad_ammo_consume:N` | ✅ | `_timing_match`에 분기 있음(`buff_manager.py`). `notify()`가 `__squad__` 누적 카운터로 집계해 스쿼드 전원의 효과를 순회(`_squad_notify_index`). 발생처는 `timeline.py` 자동사격·풀차지 발사 두 경로에서 **1발당 1회**, 그리고 `gauge_consume_as_ammo`(벨벳). 소비자: 리틀 머메이드 `거품 난사`(500발, `sequential_damage:10`)·`버블 오더 4`(400발), 일레그 : 붐 앤 쇼크 `고스트 버스터 2`(100발), 신데렐라 : 크리스탈 웨이브 `뷰티-풀 3`(200발) |

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
| `ally_hp_below:N` | 양쪽 모두 | ✅ | `_condition_ok`는 target resolve 전이라 **스쿼드 최저 체력**이 N% 이하인지로 판정, `_runtime_condition_ok`가 `state["hp_pct"][query_target]`로 대상별 재판정. 활성화 시점 분기가 없으면 instant 효과가 조건을 통째로 무시한다 |
| `ally_hp_max` | — | ❌ | 미구현. 분기 없음 |
| `during_charge` | 양쪽 모두 | ✅ | `state["charging"][caster]` |
| `during_shield` | 양쪽 모두 | ✅ | 조건 평가 대상에게 만료 전 `shield_from_max_hp_pct` 보호막이 하나 이상 있으면 참 |
| `during_reload` | — | ❌ | 미구현. `state["reloading"]` 연동 필요 |
| `burst_casted` | `_condition_ok` 전용 | ✅ | `state["burst_casted"][caster]` |
| `burst_not_casted` | `_condition_ok` 전용 | ✅ | `state["burst_casted"][caster]` |
| `back_row` | `_condition_ok` 전용 | ✅ | 스쿼드 인덱스 1 또는 3 = 후열 (포지션 2번, 4번) |
| `squad_ally_exists` | `_condition_ok` 전용 | ✅ | 소속 스쿼드(`parsed_nikke["squad"]`, 카운터스·이지스 등)가 같은 아군이 자신 외에 편성돼야 True. 의상 버전도 원본과 같은 스쿼드일 수 있다(라피 : 레드 후드 = `Counters`). 스쿼드가 없는 더미(`test_B*`)는 False |
| `focusing` | — | ❌ | 미구현. `focus_fire` stat과 연동 필요 |
| `not_core` | — | ❌ | 미구현. hit_type 연동 필요 |
| `core_hit_count:1` | — | ❌ | 미구현. timing이 아닌 condition으로 쓰일 때 |
| `self_state:상태명` | 양쪽 모두 | ✅ | `_has_self_state()` 단일 창구. `_active` 버프 **+ `state["weapon_change"]` 무기 변경 모드명**을 함께 본다 — 모드는 `_active`에 등록되지 않으므로 이걸 빼면 `self_state:저격 모드`류가 영구 거짓이 된다. 나유타 `위선 5/6`(`self_state:기억 연소`), 신데렐라 : 크리스탈 웨이브 `모드 스왑 2` |
| `not_self_state:상태명` | 양쪽 모두 | ✅ | 위와 같은 창구의 부정. 신데렐라 : 크리스탈 웨이브 `모드 스왑 3` |
| `target_state:상태명` | 양쪽 모두 | ✅ | 단일 적 가정: `"__enemy__"`가 target_chars에 있는 활성 효과로 확인 |
| `not_target_state:상태명` | 양쪽 모두 | ✅ | `target_state:`의 부정형. `_has_target_state()` 단일 창구를 공유한다. **미구현 시 조용히 항상 통과**하므로(조건 미매칭은 `return True`로 빠진다) 부여 조건으로 쓰면 매 히트 재부여되어 루프가 폭주한다 — 팬텀 구현 전 실측 딜 비중 77%. 팬텀 `예고장`·`괴도의 단검` |
| `target_code:[코드]` | `_condition_ok` 전용 | ✅ | 대상(적)의 속성 코드 확인. `self.state["enemy"]["code"]`와 비교. 코드 미설정(빈 문자열)이면 항상 통과 |
| `self_stack_above:스택명:N` | 양쪽 모두 | ✅ | `_active`에서 스택 수 확인 |
| `self_stat_above:stat키:N` | `_condition_ok` 전용 | ✅ | 자신에게 적용 중인 해당 stat의 **합이 N보다 클 때** 참. `self_state:`(버프 *이름* 판정)와 달리 **stat 값**을 본다 — 누가 준 버프인지 무관. `_STAT_TO_BUFF`로 buffs 키를 찾아 `get_buffs()` 값을 읽으므로 스택·scaling이 이미 반영된 값이 기준이다. 모더니아 `대도약 2`(`self_stat_above:accuracy_pct:0` = "자신이 명중률 증가 상태라면") |
| `gauge_above:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `gauge_below:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `gauge_eq:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `has_burst1_ally` | `_condition_ok` 전용 | ✅ | `state["burst_stages"]` |
| `no_defender_ally` | `_condition_ok` 전용 | ❌ | 미구현. 분기 없음 |
| `has_defender_ally` | `_condition_ok` 전용 | ❌ | 미구현. 분기 없음 |
| `no_burst1_ally` | `_condition_ok` 전용 | ✅ | `state["burst_stages"]` |
| `enemy_count_below:N` | 양쪽 모두 | ✅ | 랩쳐/적 N기 이하. 단일 보스 count=1 → 1<=N 항상 True. 마르차나 : 마린 스터디 |
| `enemy_count_above:N` | 양쪽 모두 | ✅ | 랩쳐/적 N기 이상. 단일 보스 count=1 → N>=2면 False, 무발동. **`_RUNTIME_COND_PREFIXES`에도 등록**(2026-08-08) — `passive` 버프는 조건 미충족이어도 등록된 뒤 게이팅을 runtime 재평가에만 의존하므로, 여기 없으면 보스전에서 그대로 적용된다(맥스웰 `일렉트릭 샷`). 마르차나 : 마린 스터디, 맥스웰 |
| `core_hit` | `_condition_ok` 전용 | ✅ | 대상이 코어 보유 적일 때. **`enemy["core_px"] >= 1` 기준**(0이면 코어 없음). 기본공격의 코어히트는 명중률·탄착군 확률이지만 이 condition이 붙은 효과는 "코어가 활성화된 적" 대상의 **확정 발동**이다 — 확률 판정을 걸지 않는다. 기본값 `core_px = 0`이므로 코어 없는 보스에서는 정상적으로 무발동. 리버렐리오 `차분한 수심 2`, 신데렐라 : 크리스탈 웨이브 `모드 스왑 3` |
| `gauge_mod:게이지명:mod:나머지` | `_condition_ok` 전용 | ✅ | 게이지값 `% mod == 나머지`일 때 발동. 민트, 아르카나 : 포츈 메이트 |

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
| `"all_allies_burst_casted"` | ❌ | ✅ | 직전에 버스트 사용한 아군 전체. `state["burst_casted"]`. 크라운 |
| `"all_allies_burst_not_casted"` | ❌ | ✅ | 직전에 버스트 미사용 아군 전체. 크라운 |
| `"[캐릭터명]"` (하드코딩) | ❌ | ✅ | target 값이 스쿼드 캐릭터 이름 리터럴이면 그 캐릭터 지정 (`target in squad_names`). 이사벨(아르카나 예외)·민트(프리카). **특정 캐릭 전용 — 코드 일반화는 범위 밖(memo)** |
| `"allies:N"` | ❌ | ✅ | 스쿼드 입력 순서 앞 N명 |
| `"allies_adjacent:N"` | ❌ | ✅ | 양 옆 아군. 자신 포함 최대 N+1명 |
| `"allies_top_atk:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨 |
| `"allies_top_atk_excl:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 자신 제외 |
| `"allies_lowest_hp:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨 |
| `"allies_lowest_hp_excl:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. `allies_lowest_hp:N`의 자신 제외 변형(`_lowest_hp(n, exclude=caster)`). 블랑 `쇼타임` |
| `"allies_top_def:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨 |
| `"allies_lowest_atk_burst3:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 3버스트 아군 중 공격력 최저 N명 |
| `"allies_random:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 자신 제외 무작위 |
| `"allies_weapon:무기유형"` | ❌ | ✅ | `parsed_nikke["weapon_type"]` 기준 |
| `"allies_weapon_excl_self:SG"` | ❌ | ✅ | 자신 제외 샷건 소지 아군 전체. `_resolve_target()`에 `allies_weapon_excl_self:` 분기 추가. `allies_weapon:SG`와 별도 |
| `"allies_class:클래스"` | ❌ | ✅ | `parsed_nikke["class"]` 기준 |
| `"allies_code:코드"` | ❌ | ✅ | `parsed_nikke["element_code"]` 기준 |
| `"allies_code_weapon:코드:무기유형"` | ❌ | ✅ | 코드+무기 복합 조건 아군 전체. `_code_weapon()` 헬퍼가 `element_code`·`weapon_type` 동시 필터. 트리나(`전격:AR`) |
| `"allies_code_weapon_leftmost:코드:무기유형:N"` | ❌ | ✅ | 위 조건을 만족하는 아군 중 **스쿼드 입력 순서 앞 N명**. 고정 속성 기반이라 lazy resolve 불필요. 매칭 0명이면 빈 리스트. 트리나(`전격:AR:1`) |
| `"allies_below_def"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 시전자보다 방어력 낮은 아군 전체 |
| `"allies_burst3"` | ❌ | ✅ | 기본 버스트 단계가 Step 3인 아군 전체. `burst_stages` 기준 |
| `"allies_top_base_charge_time:N"` | ❌ | ✅ | 기본(버프 제외) 차지 시간이 가장 긴 아군 N기. `parsed_nikke["charge_time"]` 기준 고정 속성이라 lazy resolve 불필요. 차지 무기 아군이 없으면 빈 리스트, 동률이면 스쿼드 입력 순서. 마나 `매터 시그마 4` |
| `"allies_down_top_atk_excl:N"` | ❌ | ❌ | 자신을 제외한 전투불능 아군 중 최종 공격력 최고 N기. **전투불능 모델이 없어 분기를 두지 않는다** — 기본 경로가 빈 리스트를 돌려주고, 짝인 `revive`(🚫)·`event:ally_down`(⚠️)과 같은 클래스다. 마나 `매터 감마 3` |
| `"allies_with_buff:버프명"` | ❌ | ✅ | 해당 이름의 버프가 활성인 아군 전체. `enemies_with_buff:`의 아군판(그쪽은 `__enemy__` 센티널이라 실질 필터가 없다). **부여 시점 스냅샷(비lazy)으로 확정** — "부여 순간 조건을 만족한 아군에게 준다"는 게임 시맨틱에 가깝다. 판정은 `_has_self_state()`를 재사용해 weapon_change 모드도 상태로 인정. 레이 (가칭) `섬멸 지원 4~6` |
| `"allies_burst3_persona_excl_self"` | ❌ | ✅ | 자신을 제외한 · 기본 버스트 단계 Step 3 · `persona_state` 보유 아군 전체. `allies_burst3` ∩ `persona_state` 보유 − 자신. 판정은 `allies_with_buff:`와 같은 부여 시점 스냅샷. 퀸(마코토) `배턴 터치`, 유키코 `추격` |
| `"allies_burst_casted_burst3"` | ❌ | ✅ | 직전에 버스트를 사용한 아군 중 기본 버스트 단계 Step 3. `all_allies_burst_casted` ∩ `allies_burst3`. 아래 무기판과 같은 취지 — `burst_casted`를 condition으로 두면 시전자 기준이라 대상 필터가 안 된다. 에이다 `은밀한 지원 1~3` |
| `"allies_burst_casted_weapon:무기유형"` | ❌ | ✅ | 직전에 버스트를 사용한 아군 중 해당 무기 소지자 전체. `all_allies_burst_casted`(`state["burst_casted"]`)와 `allies_weapon:X`(`parsed_nikke["weapon_type"]`)의 AND. 고정 속성 + 사이클 단위 플래그라 lazy resolve 불필요. 레이 (가칭) `정비 및 보급` |
| `"target"` / `"target_body"` / `"same_target"` | ❌ | ✅ | `__enemy__` 센티널 반환. 타임라인이 실제 처리 |
| `"all_enemies"` / `"enemies_in_range"` / `"enemies_nearest_in_range"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_random:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_nearest:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_top_atk:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_top_def:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_lowest_def:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_lowest_hp:N"` | ❌ | ✅ | `__enemy__` 센티널 반환 |
| `"enemies_top_hp:N"` | ❌ | ✅ | 최종 최대 체력 최고 적 N기. `_resolve_target()` 일반 `enemies` prefix 처리로 `__enemy__` 센티널 반환(단일 보스). 별도 분기 불필요. 마르차나 : 마린 스터디 |
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
| DealForm ②에 영향 | `atk_pct`, `atk_flat`, `def_ignore_pct`, `enemy_def_down_pct` | ✅ 추가 | ✅ `_factor2` |
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


## 회귀 테스트

계산기 로직을 고친 뒤에는 `python -m context.snapshot`으로 회귀를 확인한다.

**운영 기준·스쿼드 스펙·diff 읽는 법은 전부 `context/HARNESS.md`에 있다.**
이 문서에는 회귀 관련 규칙을 중복해서 적지 않는다.
