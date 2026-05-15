# NIKKE Calculator

5인 팀 DPS 시뮬레이터 코어. `simulate(team, config, enemy)` 한 호출로 3분 전투 타임라인을 계산한다.

---

## 파일 구성

| 파일 | 역할 |
|------|------|
| `base_stat.py` | 레벨/돌파/코어/친밀도/장비/큐브/소장품 → `atk`, `def`, `hp` 계산 |
| `damage.py` | DealForm ①~⑦ 단일 히트 대미지 계산. 코드 상성 판별 포함 |
| `buff_manager.py` | 버프 등록·활성화·만료·합산. `notify` / `tick` / `get_buffs` |
| `timeline.py` | 발사 루프 + 버스트 흐름 시뮬레이션. `simulate()` 진입점 |

---

## 빠른 시작

```python
from calculator.timeline import simulate

def make_char(name):
    return {
        "name": name,
        "level": 200, "breakthrough": 3, "core_enhancement": 7,
        "affinity": 30, "skill_level": 10, "burst_regen_time": 2.0,
        "equipment": {p: {"level": 5, "skills": []} for p in ["머리","몸통","팔","다리"]},
        "cube": {"name": "재장", "level": 5},
        "console": {"common_level": 10, "class_level": 10, "company_level": 10},
        "collection_stage": "SR15",
    }

team = [make_char(n) for n in ["아니스 : 스타", "리틀 머메이드", "크라운", "라피 : 레드 후드", "리버렐리오"]]
result = simulate(team)
print(result.summary())
```

---

## 데이터 의존성

| 파일 | 위치 | 사용처 |
|------|------|--------|
| `parsed_nikke.json` | `data/` | 캐릭터별 무기 스펙, 버스트 단계, 코드, 버스트 쿨타임 |
| `parsed_skills.json` | `data/` | 캐릭터별 스킬 효과 목록 |
| `weapon_mechanics.json` | `data/` | 무기 유형별 발사 메카닉 (연사속도, 예열, 재장전 등) |
| `base_stat_tables/` | `data/` | 레벨별 기본 스탯 테이블, 장비·큐브·소장품 수치 |

`nikke_scraped.json`은 `parsed_nikke.json` 생성에만 쓰이며 계산 시엔 참조하지 않는다.

---

## 모듈별 상세

### `damage.py`

```
대미지 = ① 계수 × ② 공방차이 × ③ 보너스(크리/적정거리/코어) × ④ 차지 × ⑤ 유형별 버프 × ⑥ 받는 대미지 × ⑦ 우월 코드
```

#### 주요 함수

- `calc_damage(base_atk, buffs, weapon, hit_type, enemy_def)` → `{"damage": int, "is_crit": bool}`
- `calc_damage_avg(...)` → `float` (크리티컬 기댓값)
- `default_hit_type(**overrides)` — hit_type 딕셔너리 생성 헬퍼
- `is_element_match(char_code, enemy_code)` — 코드 상성 판별

최종 대미지가 0이면(공격력 < 방어력) 1을 반환한다.

#### hit_type 플래그

| 키 | 기본값 | 설명 |
|----|--------|------|
| `is_normal_atk` | `True` | 일반 공격 여부. `True`면 `normal_atk_dmg_pct`·적정거리·코어 적용 허용 |
| `is_full_burst` | `False` | 풀버스트 타임 (+50%, ③) |
| `is_optimal_range` | `False` | 적정거리 (+30%, ③, `is_normal_atk=True`일 때만) |
| `is_core` | `False` | 코어 히트 (`is_normal_atk=True`일 때만) |
| `is_full_charge` | `False` | SR/RL 풀 차지 (④ 적용) |
| `is_burst_damage` | `False` | `burst_damage` stat → `burst_dmg` 버프 가산 (⑤) |
| `is_pierce_damage` | `False` | `pierce_damage` stat → `pierce_dmg` 버프 가산 (⑤) |
| `is_armor_break_damage` | `False` | `armor_break_damage` stat → `armor_break_dmg_pct` 버프 가산 (⑤), ② 계산 시 적 방어력 0 |
| `is_dot` | `False` | `dot_damage` stat → `dot_dmg` 버프 가산 (⑤) |
| `is_projectile_explosion` | `False` | `projectile_explosion_damage` stat → `projectile_explosion_dmg` 버프 가산 (⑤). RL 기본 공격에 자동 적용 |
| `is_projectile_attachment` | `False` | `projectile_attachment_damage` stat → `projectile_attachment_dmg` 버프 가산 (⑤) |
| `is_sequential` | `False` | `sequential_damage` stat → `sequential_dmg` 버프 가산 (⑤) |
| `is_part` | `False` | 파츠 히트 → `part_dmg` 버프 가산 (⑤) |
| `is_split` | `False` | 분배 대미지 → `split_dmg` 버프 가산 (⑥) |
| `coeff` | `None` | 계수 override (None이면 `weapon["damage_coeff"]` 사용) |
| `is_final_atk` | `False` | 향후 구분용 보존 플래그 |

#### 코드 상성

전격 → 수냉 → 작열 → 풍압 → 철갑 → 전격

---

### `buff_manager.py`

#### 핵심 메서드

| 메서드 | 설명 |
|--------|------|
| `notify(event, t, caster)` | 이벤트 발생 시 호출. `caster` 본인 효과 중 timing 매칭 → 활성화 |
| `tick(t)` | 매 프레임 호출. 만료 버프 제거, `every:Ns` 타이머, DoT 타이머 처리 |
| `get_buffs(caster, target, t)` | 현재 활성 버프를 condition 재평가 후 합산해 딕셔너리 반환 |
| `register_instant_handler(stat, handler)` | 타임라인이 instant stat 핸들러를 주입. `handler(eff, caster, t, val)` |
| `register_damage_handler(handler)` | 타임라인이 damage 효과 핸들러를 주입. `handler(eff, caster, t)` |

**timing은 OR, condition은 AND로 평가.**

#### 이벤트 흐름

```
notify("burst_cast", t, caster)
  └→ 해당 caster의 buff/instant/damage 효과 중 timing="burst_cast" 매칭 → _activate()
       ├→ type="instant"  → _dispatch_instant() → 핸들러 호출
       ├→ type="damage"
       │    ├→ tick_interval 있음 → _dot_timers에 (caster, next_t, expires_at) 등록
       │    └→ tick_interval 없음 → damage_handler(eff, caster, t) 즉시 호출
       └→ type="buff"    → ActiveBuff로 등록 또는 갱신
```

#### DoT (tick_interval이 있는 damage)

`bm.tick(t)`에서 `_dot_timers`를 순회하며 `next_t`에 도달하면 `damage_handler`를 호출하고, `next_t += tick_interval`로 갱신한다. `expires_at` 도달 시 자동 제거.

#### `every:Ns` 타이머

`timing: "every:Ns"` 효과는 `notify`에서 무시되고 `tick()`에서만 처리된다. 전투 시작 후 `N`초마다 condition 재평가 후 `_activate()` 호출.

#### stat → buffs 키 매핑 (`_STAT_TO_BUFF`)

`parsed_skills`의 `stat` 값이 `buffs` 딕셔너리의 어느 키에 합산되는지 정의한다. 매핑에 없는 stat은 `damage` / `instant` 타입이거나 타임라인이 직접 처리한다.

#### 버프 합산 규칙

- 대부분 stat: 단순 합산
- `crit_rate`: 독립 확률 합성 `1 - ∏(1 - pᵢ)`
- `crit_rate` 기본값(베이스 15%)은 `get_buffs` 반환 전 0.15를 더해 보정

#### 면역 처리

| stat | 적용 시점 | 동작 |
|------|-----------|------|
| `debuff_immune` | `_activate()` | `polarity: "harmful"` 효과를 대상에 등록하기 전 차단. lazy resolve 대상은 현재 미지원 |
| `stack_change_immune` | `_dispatch_instant()` | `buff_stack_add/remove`, `debuff_stack_add/remove` 발동 시 해당 대상의 스택 변경 차단 |
| `stun_immune` | — | `_BUFFS_ZERO`/`_STAT_TO_BUFF` 등록만 됨. 기절 모델 없으므로 실질 차단 로직 보류 |
| `charge_speed_buff_immune` | `get_buffs()` 후처리 | `charge_speed_pct > 0`이면 0으로 초기화 |
| `charge_speed_debuff_immune` | `get_buffs()` 후처리 | `charge_speed_pct < 0`이면 0으로 초기화 |
| `charge_time_fixed` | `get_buffs()` 후처리 | `charge_speed_pct`를 0으로 초기화 (증감 모두 무효) |

---

### `timeline.py`

#### 진입점

`simulate(team, config, enemy) → SimResult`

- `SimResult.summary()` — 콘솔 출력용 요약 문자열
- `SimResult.hits` — 전체 `HitEvent` 목록 (`t`, `caster`, `damage`, `is_crit`, `hit_tag`, `skill_name`)
- `SimResult.char_total` — 캐릭터별 누적 대미지

#### 설계 원칙

- `dt = 1/60`초 고정 스텝
- 버스트 스킬 사용 중에도 기본 발사 계속 (`CharState`와 `BurstController` 완전 독립)
- `bonus_damage` + `timing: "burst_cast"` 효과만 풀버스트 시점으로 pending (`_pending_burst_dmg`). 나머지 `damage` 타입은 이벤트 발생 시점에 즉시 계산
- 버스트 쿨타임은 캐릭터별 (`parsed_nikke.json`의 `burst_cool` 필드)

#### `CharState` (발사 루프)

무기 유형에 따라 두 가지 발사 모드를 가진다.

| 모드 | 대상 무기 | 메서드 |
|------|-----------|--------|
| `auto` / `auto_warmup` | AR, SMG, MG, SG, (일부 SMG) | `_tick_auto` |
| `charge` | SR, RL | `_tick_charge` |

- SG: `pellets` 수만큼 `calc_damage()` 독립 호출
- RL: 차지 발사 시 `is_projectile_explosion=True` 자동 설정
- `pierce_enabled` 버프 활성 시 → `is_pierce_damage=True`
- `armor_break_enabled` 버프 활성 시 → `is_armor_break_damage=True`

#### `BurstController` (버스트 흐름)

```
idle → stage:1 → [reenter:1] → stage:2 → [reenter:2] → stage:3 → switching → full_burst (10초) → idle
```

같은 단계에 복수 캐릭터가 있으면 팀 입력 순서가 우선순위. 쿨타임 중이면 다음 후보로 넘어감.

**버스트 단계 동적 관리:**

- `_default_burst_stage` — 캐릭터별 기본(고정) 버스트 단계. 변하지 않음.
- `state["burst_stages"]` — 현재 유효 버스트 단계. `burst_stage_override:N` 버프 활성 시 갱신되며 `has_burst1_ally` / `no_burst1_ally` 등 condition 평가에 사용.
- `_rebuild_burst_order()` — 매 tick마다 호출. 활성화된 `burst_stage_override:N` 버프를 반영해 `burst_order`를 재구성한다. 버프로 3버스트 캐릭터가 1버스트로 변경되는 경우도 처리됨.
- `_check_reenter()` — `bm._active`에서 `burst_stage_override:reenterN` 버프가 **실제로 활성화되어 있는지** 확인. duration / condition 있는 버프도 정확히 반영됨 (이전: `_PARSED_SKILLS` 직접 스캔으로 활성 여부 미확인).
- reenter 단계 진입 시 `burst_enter:N` notify 발생 — 해당 timing을 가진 스킬이 정상 발동.

#### `_handle_damage_eff` (damage 효과 처리)

`bm.register_damage_handler`로 등록된 콜백. `buff_manager._activate()`와 DoT tick에서 호출된다.

- `damage_formula: "normal_attack"` → `is_normal_atk=True`, 일반 공격 버프 전체 적용 (코어, 적정거리, `normal_atk_dmg_pct` 등)
- stat 이름으로 hit_type 플래그 자동 결정 (`sequential_damage:N`의 `:N` 파싱 포함)
- RL 무기 + `damage_formula: "normal_attack"` → `is_projectile_explosion=True` 추가 적용
- `bonus_damage` + `timing: "burst_cast"` → 즉시 계산하지 않고 `_pending_burst_dmg`에 추가

#### config 키

| 키 | 기본값 | 설명 |
|----|--------|------|
| `duration` | 180.0 | 시뮬레이션 시간(초) |
| `burst_switch_delay` | 0.1 | 버스트 단계 전환 딜레이(초) |
| `burst_reenter_delay` | 0.5 | reenter 딜레이(초) |
| `is_optimal_range` | True | 적정거리 여부 |

#### enemy 키

| 키 | 기본값 | 설명 |
|----|--------|------|
| `def` | 31784 | 적 방어력 |
| `code` | None | 적 코드 (None이면 상성 미적용) |
| `has_core` | True | 코어 히트 여부 |

---

## 새 스탯 추가 방법

`MAINTENANCE.md` 참고.

---

## instant stat 처리 현황

`type: "instant"` 효과는 timeline이 직접 처리한다. `type: "damage"`는 stat 종류만 파악해 `damage.py`로 넘기면 된다.

### 처리됨

| stat | 처리 위치 | 비고 |
|------|-----------|------|
| `burst_cooldown_reduce` | `BuffManager._dispatch_instant()` → timeline 핸들러 | 모든 timing. `target: "all_allies"` 지원 |
| `ammo_charge_pct` | `BuffManager._dispatch_instant()` → timeline 핸들러 | 모든 timing |
| `ammo_charge_flat` | `BuffManager._dispatch_instant()` → timeline 핸들러 | 모든 timing |
| `buff_stack_add` | `BuffManager._dispatch_instant()` | 모든 timing |
| `buff_stack_remove` | `BuffManager._dispatch_instant()` | 모든 timing |
| `debuff_stack_add` | `BuffManager._dispatch_instant()` | 모든 timing |
| `debuff_stack_remove` | `BuffManager._dispatch_instant()` | 모든 timing |
| `debuff_cleanse` | `BuffManager._dispatch_instant()` | 모든 timing |
| `remove_named_buff` | `BuffManager._dispatch_instant()` | 모든 timing |
| `gauge_charge` | `BuffManager._dispatch_instant()` | 모든 timing |
| `gauge_consume` | `BuffManager._dispatch_instant()` | 모든 timing |
| `heal_hp_pct` | `BuffManager._dispatch_instant()` → timeline 핸들러 | `state["hp"]` 갱신 후 `hp_pct` 재동기화. effective_max_hp 초과 불가 |
| `current_hp_reduce` | `BuffManager._dispatch_instant()` → timeline 핸들러 | `state["hp"]` 감소 후 `hp_pct` 재동기화. 0 미만 불가 |
| `max_hp_pct` | `BuffManager._activate()` 후처리 | 최대 체력+현재 체력 동반 증가. `state["hp"]` 가산 후 `hp_pct` 재동기화 |
| `max_hp_only_pct` | `BuffManager._activate()` 후처리 | 최대 체력만 증가. `state["hp"]` 유지, `hp_pct` 재동기화 (비율 감소) |

### 미구현 — 등록은 되나 DPS에 미반영

| stat | 상태 |
|------|------|
| `lifesteal_pct` | `get_buffs`에 집계되나 실제 체력 회복 처리 없음 |
| `def_caster_based_pct` | `get_buffs`에 집계되나 아군 방어력은 DPS 계산에 미사용 |
| `taunt` | `get_buffs`에 집계되나 타겟팅 모델 없음 |

### 미구현 — DPS 간접/조건부 영향

| stat | 필요 처리 |
|------|-----------|
| `force_reload` | `CharState._start_reload()` 강제 호출 |

### 미구현 — 현재 모델 미지원 (보류)

| stat / timing | 이유 |
|---------------|------|
| `burst_charge_pct` | 버스트 게이지 모델 단순화 (`gauge_full_at` 고정) — 추후 검토 |
| `cover_heal_pct` | 엄폐물 체력 모델 없음 |
| `enemy_buff_cleanse` | 적 버프 모델 없음 |
| `revive` | 전투불능 상태 모델 없음 |
| `force_move` | 복잡 메카닉, `_unparseable` 대상 |
| timing `received_hit:N` | 보스 공격 모델 없음 — `_timing_match`에 매칭 로직은 있으나 `bm.notify("received_hit", ...)` 호출처 없음 |
| timing `part_hit:N` | 파츠 모델 없음 — 동일하게 매칭 로직만 존재 |
