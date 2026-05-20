# calculator/ 데이터 흐름

`simulate(squad, config, enemy)` 호출 → `SimResult` 반환까지 전체 흐름을 추적한다.

---

## 1. 진입점

```
run.py / app.py
  └─ simulate(squad, config, enemy)   ← timeline.py
```

`squad`은 캐릭터 인스턴스 dict 목록. 각 캐릭터는 `DEFAULT_CHAR`를 기반으로 `name`, `level`, `equipment`, `cube`, `console`, `collection_stage` 등을 포함한다.

---

## 2. 초기화 단계 (simulate 진입 직후)

```
simulate()
  ├─ calc_base_stats(char)            ← base_stat.py  →  base_atk, base_def, base_hp
  ├─ BuffManager(squad, skills, ...)   ← buff_manager.py
  │     ├─ parsed_skills.json  (스킬 효과 목록)
  │     ├─ equipment_skills.json (장비 스킬)
  │     ├─ cube.json / collection.json (큐브·소장품 버프)
  │     └─ 모든 효과를 내부 effect 포맷으로 정규화해 _effects 에 보관
  ├─ CharState(char, base_atk, ...)   ← timeline.py 내부 클래스
  │     └─ 캐릭터 1명당 1개. 발사 타이머·장탄·차지 상태 관리
  └─ bm.battle_start()               → timing=="battle_start" / "passive" 효과 발동
```

### base_stat.py 흐름

```
level_stats.json  ──┐
affinity.json     ──┼─ _level_stat() + 보정
console.json      ──┤
equipment_stats   ──┤  → base atk/def/hp
cube.json         ──┤
collection.json   ──┘
```

공식: `(레벨스탯 + 돌파보정 + 친밀도스탯 + 콘솔스탯) × (1 + 0.02×코강수) + 장비스탯 + 큐브스탯 + 소장품스탯`

---

## 3. 메인 루프 (1/60초 스텝)

```
for t in 0, DT, 2·DT, ..., duration:
  bm.tick(t)                          ← 만료 버프 제거, every:Ns 쿨타임 처리
  BurstController.tick(t, bm)         ← 버스트 사이클 관리
  for each CharState:
    hits = cs.tick(t, bm, enemy, cfg) ← 발사/차지/재장전 처리
    result.hits.extend(hits)
```

---

## 4. CharState.tick() — 발사 판단

```
cs.tick(t)
  ├─ weapon_change 활성?  → _tick_weapon_change()
  ├─ 재장전 중?           → 완료 시 _finish_reload()
  ├─ post_reload_delay 중? → 대기
  └─ fire_mode 분기
       ├─ "auto" / "auto_warmup"  → _tick_auto()
       └─ "charge"                → _tick_charge()
```

### _tick_auto() 흐름

```
while t >= next_fire_time:
  ammo == 0?  → _start_reload(); break
  _current_fire_rate(bm, t)   ← bm.get_buffs()로 attack_speed_pct 읽기
  _fire(t, bm, enemy, cfg)    → HitEvent 목록
  next_fire_time += 1/fire_rate
```

### _fire() 흐름

```
_fire()
  ├─ bm.notify("last_bullet_fire") if last bullet
  ├─ bm.notify("on_attack", t, name)
  ├─ buffs = bm.get_buffs(name, "__enemy__", t)   ← 핵심 버프 집계
  ├─ pellet_count 결정 (buffs["pellet_count_fixed"] or base + buffs["pellet_count"])
  └─ for each pellet:
       hit_type = default_hit_type(is_normal_atk=True, is_core=..., ...)
       result = calc_damage(base_atk, enemy_def, buffs, weapon, hit_type)
       bm.notify("hit_count" / "core_hit" / ...)
       → HitEvent 생성
```

---

## 5. buff_manager.py — 버프 생명주기

### notify(event, t, caster)
이벤트 발생 시 호출. `_effects`를 순회하며 `_timing_match()`로 조건 확인 후 `_activate()`로 버프 등록.

```
notify(event)
  → _timing_match(effect, event)  → bool
  → _condition_ok(effect, t)      → bool  (발동 시점 1회 평가)
  → _activate(effect, t, caster)
       ├─ target_chars = _resolve_target(...)
       ├─ buff type  → ActiveBuff 생성/갱신 → _active에 보관
       ├─ instant type → _dispatch_instant()
       └─ damage type  → _pending_damage에 추가
```

### get_buffs(caster, target, t)
`calc_damage()` 직전에 호출. `_active`에서 해당 target에게 적용되는 버프만 추려 `_BUFFS_ZERO` 기반 딕셔너리에 합산.

```
get_buffs(caster, target, t)
  ├─ lazy resolve: _LAZY_RESOLVE_PREFIXES 대상은 이 시점에 target 결정
  ├─ _runtime_condition_ok() 재평가 (상태 의존 조건)
  ├─ _STAT_TO_BUFF 매핑으로 stat → buffs 키 합산
  │     └─ crit_rate: 독립 확률 합성 (1 - ∏(1 - p_i))
  └─ 후처리: caster_based 환산, charge_time_fixed, immune 플래그 등
```

### tick(t)
매 프레임 호출. 만료 버프 제거 + `every:Ns` 스킬 쿨타임 처리.

---

## 6. damage.py — DealForm 공식

`calc_damage(base_atk, enemy_def, buffs, weapon, hit_type)` → `{"damage": int, "is_crit": bool}`

```
① _factor1()  — 계수 (weapon.damage_coeff × normal_atk_dmg_pct 등)
② _factor2()  — 공방차이 (base_atk × atk배율 vs enemy_def × def배율)
③ _factor3()  — 보너스 (크리·코어·적정거리, 풀버스트 +50%)
④ _factor4()  — 차지 배율 (SR/RL full_charge)
⑤ _factor5()  — 유형별 버프 (atk_dmg / burst_dmg / pierce_dmg / ...)
⑥ _factor6()  — 적 받는 대미지 (received_dmg, split_dmg)
⑦ _factor7()  — 우월 코드 (element_bonus_pct)

damage = ① × ② × ③ × ④ × ⑤ × ⑥ × ⑦
```

---

## 7. sim_result.py — 결과 수집

`simulate()`가 반환하는 `SimResult`에 모든 `HitEvent`가 누적된다.

```
HitEvent          — t, caster, damage, is_crit, skill_name, hit_tag
SimLog            — verbose=True 시 버스트·버프스냅샷·재장전 이벤트 기록
SimResult
  ├─ hits: list[HitEvent]
  ├─ summary()                      → 스쿼드 총딜 요약 출력
  ├─ char_total(name)               → 캐릭터 단독 딜 합산
  └─ analyze_damage(result, name)   → DamageBreakdown (유형별·버스트구간별)
```

---

## 8. 모듈 간 의존 관계

```
timeline.py
  ├── base_stat.py      (초기화 시 1회)
  ├── buff_manager.py   (매 프레임 notify / get_buffs / tick)
  ├── damage.py         (매 발사마다 calc_damage)
  └── sim_result.py     (HitEvent 생성 및 SimResult 반환)

buff_manager.py
  └── data/             (parsed_skills, equipment_skills, cube, collection)

base_stat.py
  └── data/base_stat_tables/

damage.py              (외부 의존 없음 — 순수 계산)
sim_result.py          (외부 의존 없음 — 자료구조만)
```
