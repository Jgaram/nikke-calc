# calculator/ 데이터 흐름

`simulate(squad, config, enemy)` 호출 → `SimResult` 반환까지 전체 흐름.

---

## 1. 진입점

```
app.py (Streamlit UI, run.bat으로 기동)
context/sim.py       (CLI 단발 시뮬)
context/snapshot.py  (회귀 하네스)
  └─ simulate(squad, config, enemy, seed)   ← timeline.py
```

`squad`은 캐릭터 인스턴스 dict 목록. 각 캐릭터는 `DEFAULT_CHAR`를 기반으로 `name`, `level`, `equipment`, `cube`, `console`, `collection_stage` 등을 포함.

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
  ├─ 장전컨 발동 시점?     → _apply_reload_control() → _start_reload()
  ├─ 재장전 중?           → 완료 시 _finish_reload()
  ├─ post_reload_delay 중? → 대기
  └─ fire_mode 분기
       ├─ "auto" / "auto_warmup"  → _tick_auto()
       └─ "charge"                → _tick_charge()
```

### 컨트롤 (톡톡이·장전컨)

`char["control"]`에서 읽어 `CharState.__init__`이 필드로 고정한다. 없으면 전부 꺼짐 —
컨트롤을 켜지 않은 시뮬 결과는 이 기능 도입 전과 완전히 동일하다.
**메커니즘·수치·설정 스키마의 정본은 `context/CONTROL.md`.** 여기는 코드 위치만 적는다.

| 컨트롤 | 필드 | 동작 위치 |
|---|---|---|
| 톡톡이 | `tap_fire` / `_tap_hold` / `_tap_charge` / `_tap_release` / `_tap_post` | `_tick_charge()`의 charging 분기 |
| 장전컨 | `reload_policy` / `reload_lead` / `reload_margin` | `_apply_reload_control()` |

- 톡톡이는 `_tap_hold`(누름) 동안 누르고 발사한 뒤 `_tap_release + _tap_post`를 기다린다.
  `_tap_charge >= _effective_charge_time()`이면 풀차지 샷, 아니면 논차지 샷 — 판정은
  **발사 시점**에 한다(차지속도 버프 반영). 발사 처리는 일반 차지와
  `_charge_fire(..., is_full)`을 공유한다.
- SR/RL 딜레이 0.38초 = 사격 전 0.22(누름 구간, 못 지움) + 사격 후 0.16(컨트롤로 지움).
  `_tap_hold = 0.22 + _tap_charge`이고 **사격 전 0.22초는 차지에 안 들어간다** — 그래서
  완벽한 0.22 간격 톡톡이는 `_tap_charge = 0`이라 배율이 언제나 100%다.
  네 값은 `__init__`에서 `rate` 하나로부터 역산한다 — 자세한 분해는 `CONTROL.md`.
- 캐릭터별 기본 컨트롤(`data/control_defaults.json`)은 **UI만 읽는다.** `simulate()`는
  넘겨받은 `char["control"]`만 보므로 기본값이 시뮬 결과를 소리 없이 바꾸지 않는다.
- 장전컨은 `BurstController`가 `state`에 공개하는 `full_burst_end_t`(진입 시 확정)와
  `next_fb_start_pred`(직전 사이클 주기로 예측)를 앵커로 쓴다. 앵커 값을 기억해 사이클당 1회만 건다.

### 발사 메카닉 값의 출처 (3계층)

`fire_rate` / `fire_rate_max` / `warmup_bullets` / `pellets` / `muzzles` / 딜레이는
`CharState.__init__`에서 `_pick()`으로 한 번 해석해 인스턴스 필드에 고정한다.
앞 계층이 이긴다:

| 계층 | 파일 | 성격 |
|---|---|---|
| ① | `weapon_delays.json` `_exceptions[캐릭터]` | 수동 실측 (스크래퍼가 안 건드림) |
| ② | `parsed_nikke.json[캐릭터]` | 스크래퍼가 CDN에서 수집 |
| ③ | `weapon_mechanics.json` `weapon_type_defaults` | 무기군 기본값 |

`_pick`은 `or`가 아니라 `is not None`으로 판정한다 — 0이 유효값이기 때문.
무기 변경은 ②가 비므로 `_weapon_change` 오버라이드 → `wc_eff` → 변경 무기군 기본값 순.
`_tick_weapon_change()`가 이 필드들을 임시 교체하고 원복한다.

### _tick_auto() 흐름

```
while t >= next_fire_time:
  ammo == 0?  → _start_reload(); break
  _current_fire_rate(bm, t)   ← bm.get_buffs()로 attack_speed_pct 읽기
  _fire(t, bm, enemy, cfg)    → HitEvent 목록
  next_fire_time += 1/fire_rate
  next_fire_time <= t?        → next_fire_time = t; break   ← 프레임당 1발 상한
```

**프레임당 1발 상한**: 게임이 60fps라 60발/초를 넘는 연사는 프레임에 갇힌다
(MG 표기 70/s → 실측 60/s). `next_fire_time`을 `t`로 당겨 밀린 빚을 남기지 않는다 —
빚을 남기면 나중에 연사가 떨어질 때 몰아 쏘는 보정이 생긴다.
근거·미확인 사항은 `DATA_VERIFY.md` §프레임 상한.

### _fire() 흐름

```
_fire()
  ├─ bm.notify("last_bullet_fire") if last bullet
  ├─ bm.notify("on_attack", t, name)
  ├─ buffs = bm.get_buffs(name, "__enemy__", t)   ← 핵심 버프 집계
  ├─ split = 펠릿 수 (buffs["pellet_count_fixed"] or self.pellets + buffs["pellet_count"])
  ├─ hit_count = split × self.muzzles             ← 총구 수만큼 묶음이 더 나간다
  ├─ 펠릿당 계수 = damage_coeff / split           ← 총구로는 나누지 않는다
  └─ for each hit:
       hit_type = default_hit_type(is_normal_atk=True, is_core=..., ...)
       result = calc_damage(base_atk, enemy_def, buffs, weapon, hit_type)
       bm.notify("hit_count" / "core_hit" / ...)
       → HitEvent 생성
```

---

## 5. buff_manager.py — 버프 생명주기

### notify(event, t, caster)
이벤트 발생 시 호출. `_notify_index`(사전 구축된 이벤트→효과 인덱스)로 후보 효과만 조회 후 `_activate()`로 버프 등록.

```
notify(event)
  → _timing_match(effect, event)  → bool
  → _condition_ok(effect, t)      → bool  (발동 시점 1회 평가)
  → _activate(effect, t, caster)
       ├─ target_chars = _resolve_target(...)
       ├─ buff type  → ActiveBuff 생성/갱신 → _active에 보관
       ├─ instant type → _dispatch_instant()
       └─ damage type  → _damage_handler 콜백 호출
                          (bonus_damage + burst_cast 조합은 timeline 측에서
                           _pending_burst_dmg에 보류 → 풀버스트 진입 후 발동)
```

### get_buffs(caster, target, t)
`calc_damage()` 직전 호출. `_active`에서 해당 target에게 적용되는 버프만 추려 `_BUFFS_ZERO` 기반 딕셔너리에 합산.

```
get_buffs(caster, target, t)
  ├─ lazy resolve: _LAZY_RESOLVE_PREFIXES 대상은 이 시점에 target 결정
  ├─ _runtime_condition_ok() 재평가 (ActiveBuff.has_runtime_conditions=True인 경우만)
  ├─ _STAT_TO_BUFF 매핑으로 stat → buffs 키 합산
  │     └─ crit_rate: 독립 확률 합성 (1 - ∏(1 - p_i))
  └─ 후처리: caster_based 환산, charge_time_fixed, immune 플래그 등
```

### tick(t)
매 프레임 호출. 만료 버프 제거 + `every:Ns` 스킬 쿨타임 처리 + DoT 타이머 발동.

### ref_count(caster, ref) — 게이지·스택 조회의 단일 창구

`scaling_ref`·`sequential_damage:이름`이 가리키는 이름은 **게이지일 수도 중첩 버프일 수도** 있다.
양쪽을 순서대로(게이지 → 버프 스택) 보는 곳은 이 함수 하나뿐이며, buff_manager·timeline의
모든 참조 지점이 이걸 부른다. 새로 참조가 필요하면 조회 로직을 다시 쓰지 말고 이 함수를 쓴다.

반환값 3가지를 구분해야 한다:

| 반환 | 의미 | 호출부가 할 일 |
|------|------|---------------|
| `0` | 게이지가 있고 값이 0 | 그대로 0으로 취급 (히트 0회, 배율 0) |
| `N` | 게이지값 또는 버프 스택 수 | 그대로 사용 |
| `None` | 그런 이름의 게이지도 버프도 없음 | 각자의 기본값(보통 1) 사용 |

**`0`과 `None`을 뭉뚱그리면 조용히 틀린다.** 게이지 0을 "없음"으로 보고 넘어가면
히트 수가 0회가 아니라 기본값 1회로 남는데, 에러도 안 나고 그럴듯한 숫자라 발견이 늦다.

### `scaling: "stack_count"` — 스택 수가 곱해지는 자리는 효과 종류가 정한다

같은 `scaling`·`scaling_ref`라도 곱해지는 대상이 셋으로 갈린다. **구조적 규칙이며
특정 캐릭터 예외가 아니다.**

| 효과 type | 스택 수가 곱해지는 곳 | 코드 위치 | 예 |
|-----------|---------------------|----------|-----|
| `damage` (일반) | **히트 수** | `timeline.simulate` hit_count 블록 | 미하라 `바디 컨텍 3`, 스노우 화이트 `오토 파이어 2` |
| `damage` (`dot_damage`) | **틱당 계수** (히트는 틱당 1회) | `timeline.simulate` 계수 블록 | 미하라 `사슬 감기` |
| `buff` | **버프 수치** | `BuffManager._get_value()` | 마스트 `취기`, 토브 `임시 개조`, 솔린 `티켓 효과` |

`dot_damage`는 hit_count 블록에서 **명시적으로 제외**되어 있다. 빼먹으면 계수와 히트 수
양쪽에 스택이 곱해져 스택² 배로 부풀거나, 게이지 0일 때 히트 0회가 되어 지속딜이 통째로 사라진다.

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

`simulate()`가 반환하는 `SimResult`에 모든 `HitEvent` 누적.

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

버그 수정 절차는 `/bug-fix` 스킬 사용.
