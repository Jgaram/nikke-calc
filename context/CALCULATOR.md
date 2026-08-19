# calculator/ 데이터 흐름

`simulate(squad, config, enemy)` 호출 → `SimResult` 반환까지 전체 흐름.

---

## 1. 진입점

```
context/sim.py                     (CLI 단발 시뮬)
context/snapshot.py                (회귀 하네스)
.agent/skills/report-squad/scripts/report.py   (딜량 보고서)
  └─ context/spec.py   기본 육성 스펙 + 캐릭터별 레이어 → 캐릭터 dict
       └─ simulate(squad, config, enemy, seed)   ← timeline.py
```

`squad`은 캐릭터 인스턴스 dict 목록. 각 캐릭터는 `name`, `level`, `equipment`, `equip_skills`,
`cube`, `console`, `collection_stage`, `control` 등을 포함한다. 이 dict를 만드는 건 러너 쪽
`context/spec.py`이고(정본: `HARNESS.md §기본 스펙`), `timeline.DEFAULT_CHAR`는 호출자가
키를 빠뜨렸을 때의 **최소 폴백**일 뿐이다 — 장비 옵션·컨트롤 기본값은 거기 없다.

---

## 2. 초기화 단계 (simulate 진입 직후)

```
simulate()
  ├─ calc_base_stats(char)            ← base_stat.py  →  base_atk, base_def, base_hp
  ├─ BuffManager(squad, state)        ← buff_manager.py
  │     ├─ parsed_skills.json  (스킬 효과 목록)
  │     ├─ equipment_skills.json (장비 스킬)
  │     ├─ cube.json / collection.json (큐브·소장품 버프)
  │     └─ 모든 효과를 내부 effect 포맷으로 정규화해 _effects 에 보관 (_register_all)
  │        _effects는 여기서 확정되고 이후 불변이다 — every:Ns 목록·id 역참조 맵을
  │        이 시점에 한 번만 만든다
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
  bm.tick(t)                          ← 주기 대미지 → 만료 버프 제거 → every:Ns 쿨타임
  _dot_events 배출                     ← bm.tick이 낳은 damage 효과의 히트를 여기서 수확
  burst_ctrl.tick(t, bm, state)       ← 버스트 사이클 관리 (버스트 딜도 히트로 나온다)
  for each CharState:
    hits = cs.tick(t, bm, enemy, cfg) ← 발사/차지/재장전 처리
  (히트마다 result.hits 누적 + char_total 가산 + 흡혈 처리)
```

**한 프레임 안의 이 순서가 곧 명세다.** `bm.tick`이 만료 정리보다 주기 대미지를 먼저
처리하는 것, DoT 히트를 버스트·발사보다 앞에서 수확하는 것 모두 결과를 바꾼다 —
스냅샷 L3(순서)가 지키는 대상이 이것이다.

---

## 4. CharState.tick() — 발사 판단

```
cs.tick(t)
  ├─ weapon_change 활성?  → _tick_weapon_change()
  ├─ 컨트롤 액션 생산      → _pump_ctrl_seq() / _apply_cover_policy() → _enter_cover()
  ├─ 재장전 중?           → 완료 시 _finish_reload()  (클립 무기는 1/3만 차면 다음 클립으로)
  ├─ 엄폐 중?             → _tick_cover() → 사격·차징 불가
  ├─ post_reload_delay 중? → 대기
  └─ fire_mode 분기
       ├─ "auto" / "auto_warmup"  → _tick_auto()
       └─ "charge"                → _tick_charge()  (풀차지 후 _hold_ready() 게이트)
```

### 컨트롤 (톡톡이·장전컨·버스트 엄폐컨·홀드)

`char["control"]`에서 읽어 `CharState.__init__`이 필드로 고정한다. 없으면 전부 꺼짐 —
컨트롤을 켜지 않은 시뮬 결과는 이 기능 도입 전과 완전히 동일하다.
**메커니즘·수치·설정 스키마의 정본은 `context/CONTROL.md`.** 여기는 코드 위치만 적는다.

구조는 **2층**이다. 실행층은 조작 원시타입(click·cover 구간)만 알고, 정책과 명시 시퀀스는
그 구간을 만드는 생산자다. 실행층은 둘을 구분하지 않는다.

| 층 | 담당 | 코드 |
|---|---|---|
| 생산자 — 기본 전략 | 정책들이 엄폐 구간을 연다 (디스패처) | `_apply_cover_policy()` |
| 생산자 — 명시 시퀀스 | `control["sequence"]`를 시각순으로 꺼낸다 | `_pump_ctrl_seq()` |
| 실행층 — cover | 진입·만료·물리 배타 | `_enter_cover()` / `_tick_cover()` / `_exit_cover()` |
| 실행층 — click | 떼는 시점을 앞뒤로 민다 | `_tick_charge()`의 charging 분기 |

| 컨트롤 | 필드 | 동작 위치 |
|---|---|---|
| 톡톡이 | `tap_fire` / `_tap_hold` / `_tap_charge` / `_tap_release` / `_tap_post` | `_tick_charge()`의 charging 분기 |
| 장전컨 | `reload_policy` / `reload_lead` / `reload_margin` / `reload_cover_dur` | `_apply_reload_cover()` |
| 버스트 엄폐컨 | `cover_policy` / `cover_extend` | `_apply_burst_cover()` |
| 홀드 | `hold_policy` / `hold_lead` / `_charge_full_t` / `_hold_release_t` | 생산자 `_apply_hold_policy()` · 실행 `_tick_charge()`의 charging 분기 |

정책이 둘이지만 만드는 구간은 cover 하나뿐이라 우선순위 판정이 거의 필요 없다 — 이미
엄폐 중이면 아무도 열지 않는다. 다만 디스패처가 **버스트 엄폐컨을 먼저** 본다(구간이 길고,
장전컨이 노리는 재장전은 그 구간 안에서 따라온다).

홀드는 정책(`own_full_burst`)과 시퀀스 액션 둘 다 있고 같은 `_hold_release_t`로 들어간다.
**정책을 먼저 굴리고 시퀀스가 덮어쓴다** — 순서만으로 "명시 시퀀스 우선"이 지켜진다.

- 톡톡이는 `_tap_hold`(누름) 동안 누르고 발사한 뒤 `_tap_release + _tap_post`를 기다린다.
  `_tap_charge >= _effective_charge_time()`이면 풀차지 샷, 아니면 논차지 샷 — 판정은
  **발사 시점**에 한다(차지속도 버프 반영). 발사 처리는 일반 차지와
  `_charge_fire(..., is_full)`을 공유한다.
- SR/RL 딜레이 0.38초 = 사격 전 0.22(누름 구간, 못 지움) + 사격 후 0.16(컨트롤로 지움).
  `_tap_hold = 0.22 + _tap_charge`이고 **사격 전 0.22초는 차지에 안 들어간다** — 그래서
  완벽한 0.22 간격 톡톡이는 `_tap_charge = 0`이라 배율이 언제나 100%다.
  네 값은 `__init__`에서 `rate` 하나로부터 역산한다 — 자세한 분해는 `CONTROL.md`.
- 캐릭터별 기본 컨트롤(`data/char_defaults.json`)은 **`calculator/`가 읽지 않는다.**
  레이어를 얹는 건 러너 쪽(`context/spec.py`)이고, `simulate()`는 넘겨받은
  `char["control"]`만 보므로 기본값이 시뮬 결과를 소리 없이 바꾸지 않는다.
- 장전컨은 `BurstController`가 `state`에 공개하는 `full_burst_end_t`(진입 시 확정)와
  `next_fb_start_pred`(직전 사이클 주기로 예측)를 앵커로 쓴다. 앵커 값을 기억해 사이클당 1회만 건다.
  버스트 엄폐컨도 `full_burst_end_t`를 앵커로 쓰되, 그 값까지의 **길이**를 구간으로 삼는다.
  진입 조건에 `state["burst_casted"][본인]`을 보므로 **본인이 버스트를 쓴 사이클에만** 걸린다.
- 풀차지 도달은 `_charge_full_t`로 래치한다. 래치가 없으면 홀드 중 차지속도 버프가 빠질 때
  `_charge_end_t`가 뒤로 밀려 이미 채운 차지가 풀려버린다.
- **엄폐를 연 틱은 거기서 끝난다**(자세 전환에 1프레임). 재장전이 0초인 구간에서 이 1프레임이
  결과를 가르므로 임의로 바꾸면 안 된다 — 장전컨 A가 노리는 구간이 정확히 거기다.

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
  ├─ 실행 계획 조회 (_plan_cache) — 없으면 _build_plan
  │     시간 불변 버프(_is_time_invariant)는 _cache_version당 1회만 평가해 스텝으로 접는다.
  │     _active 순서를 그대로 보존한다 — 합산 순서가 바뀌면 부동소수점 끝자리가 달라지고
  │     하네스는 완전 일치를 요구한다 (HARNESS.md §왜 결정론적인가)
  ├─ lazy resolve: _LAZY_RESOLVE_PREFIXES 대상은 이 시점에 target 결정 (_resolve_lazy)
  ├─ _runtime_condition_ok() 재평가 (ActiveBuff.has_runtime_conditions=True인 경우만)
  ├─ _STAT_TO_BUFF 매핑으로 stat → buffs 키 합산
  │     └─ crit_rate: 합연산 후 100% 상한 (기본 15% + 버프 합)
  └─ 후처리: caster_based 환산, charge_time_fixed, immune 플래그 등
```

계획 캐시·`_by_stat`/`_by_name` 인덱스는 전부 **`_active`가 그대로인 동안** 유효한 파생물이라
`_invalidate_buffs_cache()`가 한꺼번에 비운다. 전제가 깨졌는지 확인하는 감사 모드는
`HARNESS.md §버프 집계 캐시 감사`.

`_resolve_lazy()`는 `get_buffs`와 `consume_bullet_buffs` **양쪽이 같이 쓴다.** 지연 resolve
대상에 `duration_bullets`가 붙어 있으면 타겟 확정과 동시에 발수 카운터를
`bullets_left` → `bullets_per_target`으로 옮겨야 한다 — 옮기지 않으면 소모가
"시전자 본인 발사" 분기로 새서 **대상이 아니라 시전자의 발사**가 버프를 먹는다.
(미란다 `웨이크업! 4`가 이 조건에 걸리는 유일한 효과다.)

### tick(t)
매 프레임 호출. 만료 버프 제거 + `every:Ns` 스킬 쿨타임 처리 + DoT 타이머 발동.

### ActiveBuff.uid — 버프를 dict 키로 잡을 때

**`ActiveBuff.uid`(모듈 전역 `itertools.count()`)를 쓴다. `id(ab)`를 쓰지 않는다.**
만료된 ActiveBuff가 GC되면 CPython이 그 메모리 주소를 새 객체에 재사용하므로,
버프보다 오래 사는 dict(`_cond_passive_prev`처럼 `reset()`에서만 비우는 것)에 항목이
남아 있으면 **새 버프가 옛 버프의 상태를 물려받는다.**

증상이 딜 수치가 아니라 **비결정성**으로 나온다 — 같은 시드인데 "직전에 어떤 스쿼드를
돌렸는가"에 따라 버프 발동 횟수가 달라진다. 일반화하면 수명이 다른 dict의 키로 `id()`를
쓰지 않는다는 규칙이고, 새 코드에도 그대로 적용된다.

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

`calc_damage(base_atk, enemy_def, buffs, weapon, hit_type)` → `{"damage": int, "is_crit": bool, "crit_frac": float}`

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

### 기대값 모드 (`rng_mode: "expected"`)

시뮬의 딜 계산 난수원은 두 개다 — **크리 판정**과 **코어히트 판정**. 둘 다 ③에서
*가산* 항으로 들어가므로, 확률 판정 대신 각 항에 확률을 곱하면 그 자체가 기댓값이다.
`calc_damage(..., expected=True)`가 그 경로다:

- 크리: `min(crit_rate, 1) × (0.5 + crit_dmg%)`를 더한다. `is_crit`은 항상 False,
  `crit_frac`에 확률이 담긴다.
- 코어: `hit_type["core_prob"]`(= `_core_hit_prob()`)를 코어 가산에 곱한다.
  timeline이 기대값 모드에서만 채우고, `is_core`는 False로 둔다.

**확률 조건(`prob:`)도 기대값 모드에서는 난수를 굴리지 않는다** — 확률을 (효과, 캐스터)별로
누적해 1.0을 넘길 때마다 발동시킨다(`buff_manager._condition_ok`, `state["rng_acc"]`,
판정 플래그는 `state["rng_expected"]`). 기대 발동 횟수는 확률 판정과 같고 위상만 규칙적으로
퍼진다 — 크리·코어의 `_notify_frac`과 같은 규약이다. 보유: 토브 `급조 탄환`(기본 판본),
슈가, 홍련. 이 처리가 없으면 그 셋만 기대값 모드에서 시드에 의존한다.

`simulate(config={"rng_mode": "expected"})`로 켠다. 시드·반복 평균 없이 1회 실행으로
기대딜이 나온다(CLI: `python -m context.sim "..." --expected`).

트레이드오프 — 히트마다 크리·코어가 확률로 섞이므로 **개별 히트의 크리/코어 구분이 없다.**
`is_crit`은 늘 False고 `hit_tag`에 `core:`가 붙지 않는다(코어히트율이 정확히 100%인
캐릭터는 판정할 게 없으므로 코어 태그를 그대로 유지한다). 크리·코어 횟수를 세는 트리거
(`crit_hit_count:N` 이브, `core_hit_count:N` 루드밀라 : 윈터 오너)와 `squad_body_hit`은
확률을 캐릭터별로 누적해 1.0을 넘길 때마다 발화한다(`timeline._notify_frac`) — 기대
발동 **횟수**는 확률 판정과 같지만 발동 **시점**은 규칙적으로 고르게 퍼진다.

---

## 7. sim_result.py — 결과 수집

`simulate()`가 반환하는 `SimResult`에 모든 `HitEvent` 누적.

```
HitEvent          — t, caster, damage, is_crit, skill_name, hit_tag
SimLog            — verbose=True 시 버스트·버프스냅샷·재장전 이벤트 기록
SimResult
  ├─ hits: list[HitEvent]
  ├─ char_total: dict[이름 → 딜]     (필드다. squad_total은 이것의 합)
  ├─ summary()                      → 스쿼드 총딜 요약 출력
  └─ hit_summary()                  → hit_tag별 히트 집계

모듈 함수 (SimResult의 메서드가 아니다)
  analyze_damage(result, name)      → DamageBreakdown (유형별·버스트구간별)
  analyze_team(result)              → 전원 DamageBreakdown
  print_team_analysis(result)       → 표 출력 (context/sim.py --view analysis)
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

버그 재현은 `python -m context.sim`(파일 수정 없는 단발 시뮬), 수정 후 회귀는
`python -m context.snapshot`. 사이클 간격 판정 기준은 `context/HARNESS.md §편성 후 사이클 검증`.
캐릭터별 검증 체크리스트가 있으면 `context/scenarios/<이름>.md`.
