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

크리 대미지는 `_CRIT_DMG_STATS`가 같은 역할을 한다 (`_PLAN_CDMG` 경로).

원문이 `[일반 공격 크리티컬 확률/대미지 ...]`처럼 **일반 공격 한정**이면
`_NORMAL_ATK_ONLY_CRIT_RATE_STATS` / `_NORMAL_ATK_ONLY_CRIT_DMG_STATS`에도 넣는다.
그러면 `get_buffs`가 그 기여를 뺀 합을 `crit_rate_skill` · `crit_dmg_skill`로 따로 내고,
스킬 딜 히트는 `damage.calc_avg_damage`에서 그쪽을 쓴다.

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
- **대상이 0기면 발동권을 쓰지 않는다** (유저 결정 2026-09-21). 종전에는 대상 해석보다 먼저
  카운터를 깎아서, 게이트가 condition이 아니라 **대상**에만 있는 효과가 아무에게도 닿지 못한
  첫 트리거에서 한 장뿐인 발동권을 태우고 영구히 죽었다 — 앤 : 미라클 페어리
  `파란 나비의 꿈 3`(「전투불능 상태 화력형 아군 무작위 1기에게 [부활] [전투 중 1회 발동]」)이
  그 첫 사례다. 지금은 `_resolve_target()`이 빈 목록을 주면 카운터를 그대로 두고 돌아간다.
  지연 resolve 대상(`_LAZY_RESOLVE_PREFIXES`)과 보스가 건 효과(`targets` 인자)는 여기서 대상을
  확정하지 않으므로 종전대로 즉시 소모한다. **로스터에서 `max_trigger`를 가진 효과는 20개이고
  그중 비어질 수 있는 대상은 셋뿐**이다(앤 하나 · 비스킷 `터그 놀이`·`터그 놀이 2`의
  `allies_class:방어형` 둘 — 방어형 아군이 없는 스쿼드에서만 갈린다). 회귀 33/33 불변.

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
보호막량·만료 시각도 갱신한다. 보호막은 **보스 공격 패턴(`enemy.patterns`의 `attack`)에만**
깎인다 — 패턴이 없으면 지속시간 만료 전까지 소모되지 않는다.

- 보호막을 받은 각 대상에게 `event:shield_applied`를 통지한다.
- `during_shield`는 해당 캐릭터에게 유효한 보호막이 하나 이상 있는지 판정한다.
- 보스 공격은 `bm.absorb_shield()`로 보호막을 깎는다. 보호막은 각자 따로 작동한다 — 비관통은
  **나중에 생긴 보호막 하나**만 맞고(순서는 잠정, `docs/DATA_VERIFY.md`) 그 보호막이 깨져도 남은
  피해가 넘어가지 않는다. 관통은 **살아 있는 보호막 전부**가 같은 피해를 받는다. 다 깎인 보호막마다
  그 대상에게 `event:shield_consumed`.
  층 규칙의 정본은 `docs/CALCULATOR.md` §보스 패턴.
- `shield_restore_pct`는 미구현이다(로스터 사용처 없음).
- `next_shield_hp_pct`(다음 보호막 체력 ▲)는 보호막이 적용되는 순간 대상별로 생성량을 키우고 소모된다.

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

> **재평가 대상은 「유한 수명이 없는 버프」다** (`_has_runtime_cond`). 가르는 값이 `expires_at`
> 하나여서 오래 구멍이 있었다 — `[N발 유지]`(`duration_bullets`)는 시간 만료가 없어
> `expires_at`이 `inf`로 남고, 그래서 `[N초 유지]`와 같은 유한 수명인데도 재평가에 끌려갔다.
> 2026-09-10에 `duration_bullets != -1`도 제외로 넣었다. 없을 때 생기는 일:
> **자기 상태 이름을 `not_self_state:`로 막는 재부여 게이트가 스스로를 꺼 버린다**
> (베스티 : 택티컬 업 `미사일 가이드` — 차지 속도 100%·차지 대미지 58.5가 실측 0이었다).
> 소급 영향은 팬텀 `괴도의 예고장`(`[1발 유지]` + `target_state:예고장`) 하나로,
> 예고장을 지우는 그 발이 이제 버프를 정상적으로 받는다.

> **조건부 `passive`는 지속시간에 따라 수명 관리가 갈린다** (`_is_cond_finite_passive`,
> 2026-09-15). `passive`는 `battle_start`에 **한 번만** 등록되므로(`_timing_match`):
> - **무한 지속(`-1`)** — 조건과 무관하게 등록하고(`suppress_event`로 로그만 억제)
>   게이팅을 런타임 재평가에 전적으로 맡긴다. 조건이 곧 유효 구간이다.
> - **유한 지속(`[N초 유지]`)** — 위 재평가 대상이 **아니므로**(`expires_at != inf`) 같은 방식을
>   쓸 수 없다. 두 가지가 동시에 깨져 있었다: 조건이 거짓인데도 등록돼 **수치가 그대로 먹었고**,
>   한 번 만료되면 조건이 참이 되어도 **되살아날 경로가 없었다**. 그래서 이 부류는
>   ① 조건이 거짓이면 등록하지 않고 ② `tick()`이 조건이 참인 동안 만료 시각을 민다
>   ③ 거짓이 되면 그대로 두어 원문의 N초만큼 잔류시킨다. 원문 「… 일 때 … [N초 유지]」가
>   *조건이 유지되는 동안 계속 걸리고 조건이 깨진 뒤 N초 더 남는다*는 뜻이기 때문이다
>   (유저 결정 2026-09-15). 보유: 에이드 `청소를 시작하겠습니다, 주인님.`(딜 버프가 통째로
>   죽어 있었다 — 에이드 개인 딜 +4.88%) · 치사토 `사격 간파`(발동 1회 → 8회, `invincible`이라
>   **총딜은 불변**). → **파싱에서 `[N초 유지]`를 `-1`로 바꿔 우회하지 않는다.**

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
| `def_caster_based_pct` | `def_caster_based_pct` / `enemy_def_down_flat` | — / ② | ⚠️/✅ | **아군 대상**은 buffs에 집계되나 DPS 계산 미사용(⚠️ — `_effective_def()`의 `allies_below_def` 판정에만 쓴다). **적 대상**(마스트 `해풍`)은 `get_buffs`·`_plan_step`에서 `enemy_def_down_flat`으로 라우팅되어 factor②에서 **정액** 감소로 적용(✅, 2026-09-22). 감소량 = **시전자 기본**(버프 제외) 방어력 × N%이며 (`_caster_based_def_flat()` — 아군판 `_effective_def()`와 같은 환산), `_get_value()`가 중첩까지 곱한 뒤의 값이다. 비율판과 더하는 자리가 다르다: `eff_def = max(적방어력 × (1 + enemy_def_down_pct%) + enemy_def_down_flat, 0)` |
| `def_pct` | `def_pct` / `enemy_def_down_pct` | ② | ⚠️/✅ | **아군 대상**은 base_stat 재계산용으로 timeline 미반영(⚠️). **적 대상**(예: 마르차나 : 마린 스터디 고위험 대상)은 `get_buffs`에서 `enemy_def_down_pct`로 라우팅되어 factor②에서 적 방어력 감소 적용(✅). `eff_def = 적방어력 × (1 + enemy_def_down_pct%)` |
| `max_hp_pct` | `max_hp_pct` | — | ✅ | 최대+현재 체력 동반 증가. `state["hp"]` 동기화 |
| `max_hp_only_pct` | `max_hp_only_pct` | — | ✅ | 최대 체력만 증가. `state["hp"]` 유지 |
| `atk_caster_based_pct` | — / `enemy_atk_down_flat` | ② / — | ✅ | **아군 대상**은 `get_buffs()` 후처리에서 시전자 ATK × (val/100) → 수령자 `atk_flat`에 합산. `_STAT_TO_BUFF` 매핑 없음. **적 대상**(키리 `곁눈질` — 2026-09-23 구현, 첫 보유자)은 `bm.enemy_atk_down_flat(적 id, t)`로 **직접 조회**해 보스 공격력을 **정액**으로 깎는다: `eff_atk = max(보스 공격력 + enemy_atk_down_flat, 0)`. 감소량 = **시전자 기본**(버프 제외) 공격력 × N%이고(`_caster_based_atk_flat()` — 아군판과 환산이 같다) `_get_value()`가 중첩까지 곱한 뒤의 값이다. 짝인 `def_caster_based_pct` → `enemy_def_down_flat`과 달리 **`get_buffs()`의 버프 사전에 자리를 두지 않는다** — 소비처가 `timeline._boss_attack` 하나뿐이라서다(`cover_hp_pct`·`heal_received_mult`와 같은 자리). 적 공격력은 니케가 적을 때리는 식에 안 들어가므로 **딜 기여 0**이고 보스 공격 패턴이 있을 때의 생존만 바뀐다. ⬜ 쫄몹이 쏜 발은 깎지 않는다(`hit.source`가 표시 이름이라 적 id로 못 되돌린다 — `DATA_VERIFY.md` §보스 → 니케 피해) |
| `atk_from_hp_pct` | — | ② | ✅ | `get_buffs()` 후처리에서 `effective_max_hp(caster) × (val/100)` → `atk_flat`에 합산. `_STAT_TO_BUFF` 매핑 없음 |
| `max_hp_from_max_hp_pct` | — | — | ✅ | 최대+현재 체력 동반 증가, **시전자의 `effective_max_hp` × val%**. `hp_caster_based_pct`(시전자 **base_hp** 비례)와 기준이 다르다 — 시전자에게 걸린 최대 체력 버프가 값을 키운다. `shield_from_max_hp_pct`·`atk_from_hp_pct`와 같은 「시전자의 최종 최대 체력 비례」 계열이다. 가산분은 **부여 시점 스냅샷**으로 `ActiveBuff.hp_bonus_flat`에 싣고 `effective_max_hp()`가 그대로 더한다 — 조회 시점에 다시 재면 시전자가 자기 대상일 때 `effective_max_hp`가 자기를 다시 부르는 **재귀**가 된다(보호막의 `shield_per_target`과 같은 이유). 재발동은 직전 스냅샷을 먼저 걷어내고 다시 재 복리를 막는다. 만료 시 현재 체력 캡은 `hp_caster_based_pct`와 같은 자리에서 한다. `_STAT_TO_BUFF` 매핑 없음. 메어리 : 베이 갓데스 `고요한 수면 2` |
| `persona_state` | `persona_state` | — | ✅ | 페르소나 상태 마커 버프 (`values`/`fixed_value` 없음, boolean 플래그). 수치 기여 없이 상태 판정에만 쓴다 — `_has_persona_state()`가 이 stat 보유 여부로 `allies_burst3_persona_excl_self`를 판정. 퀸(마코토)·유키코·아이기스(B2라 그 대상에는 들지 않는다) |
| `crit_rate` | `crit_rate` | ③ | ✅ | 기본 15% + 버프 **합연산**, 100% 상한 (`_CRIT_RATE_STATS`) |
| `normal_atk_crit_rate` | `crit_rate` | ③ | ✅ | `crit_rate`(일반 공격용)에 합산하되, 이 기여를 뺀 합을 `crit_rate_skill`로 따로 낸다 — 스킬 딜 히트(`is_normal_atk=False`)는 그쪽을 쓴다 (`_NORMAL_ATK_ONLY_CRIT_RATE_STATS`) |
| `crit_dmg` | `crit_dmg` | ③ | ✅ | |
| `normal_atk_crit_dmg` | `crit_dmg` | ③ | ✅ | `crit_dmg`(일반 공격용)에 합산하되, 이 기여를 뺀 합을 `crit_dmg_skill`로 따로 낸다 — 스킬 딜 히트는 그쪽을 쓴다 (`_NORMAL_ATK_ONLY_CRIT_DMG_STATS`). 현재 이 stat을 쓰는 캐릭터는 없다 (선행 구현) |
| `core_dmg_pct` | `core_dmg_pct` | ③ | ✅ | `core_dmg_pct`로 합산 |
| `part_dmg_pct` | `part_dmg_pct` | ⑤ | ✅ | `is_part=True` 히트에만 가산. `is_part`가 서는 자리는 둘이다 — ① **원문이 파츠를 명시한 damage 효과(`hits_parts: true`)의 히트, `enemy["has_parts"]=True`일 때만** — 기본공격에는 붙지 않는다(유저 결정) ② 보스 패턴 좌표 off의 **파츠 히트** — 관통·발사체 폭발·「파츠 포함」 전체기가 위치 단계(`reach`)를 적은 파츠에 닿은 몫(`calculator/boss_pattern.py` §파츠 다중 타격). reach 파츠가 살아 있는 동안은 ①의 본체 히트가 판정을 내려놓고 파츠 히트가 받는다. ③ 보스 패턴 **좌표 모드의 파츠 히트** — 파츠에 떨어진 탄(일반 공격 포함)·관통·폭발 원에 닿은 파츠·「파츠 포함」 전체기(§좌표 모드). 좌표 모드에서도 ①의 본체 히트는 판정을 내려놓는다. 저지원 히트에는 안 붙는다(파츠가 아니다). `has_parts`는 `DEFAULT_ENEMY`(기본 `False`)·`runner/sim.py --has-parts`·보고서 스펙 `enemy`로 노출. `squad_part_hit`/`squad_body_hit` 이벤트 라우팅도 같은 키를 쓴다. `hits_parts` 효과: 레이븐 `템페스트` · 신데렐라 : 크리스탈 웨이브 `모드 스왑 2` · 베스티 : 택티컬 업 `미사일 컨테이너 온라인 3`. 보유: 레이븐 `급소 공략` · 신데렐라 : 크리스탈 웨이브 `디스트로이` · 스노우 화이트 : 헤비암즈 `어나더 화이트 파츠대미지`(①의 짝이 없어 ②에서만 실린다) · 아크레인저 블랙 · 로산나 : 시크 오션 |
| `intercept_dmg_pct` | — | — | 🚫 | 저지 부위 공격 대미지. **구현하지 않는다 — 발동 조건을 언제나 미달성으로 둔다**(유저 결정, 2026-08-11). 계산기 적 모델에 저지 부위가 없어 딜 기여가 영구히 0이다. 파싱은 정상 등록하고 시나리오에는 네거티브 항목으로 둔다. 보유: 누아르 `피날레 3`·`피날레 5` · 라피 : 레드 후드 `전황 파악 4` · 헬름 `포문 개방`(기본·애장품2 두 판본) · 아니스 : 스파클링 서머 `스파클링 미사일 2` · 앨리스 : 원더랜드 바니 `당근 파티` · 사쿠라 `앵화난만 3`. **`projectile_dmg_pct`(발사체에 가하는 대미지)와 다른 축이다** — 사쿠라가 둘 다 가졌다 |
| `atk_dmg_pct` | `atk_dmg_pct` | ⑤ | ✅ | |
| `burst_dmg_pct` | `burst_dmg_pct` | ⑤ | ✅ | `is_burst_damage=True` 히트에만 가산 |
| `pierce_dmg_pct` | `pierce_dmg_pct` | ⑤ | ✅ | `is_pierce_damage=True` 히트에만 가산 |
| `dot_dmg_pct` | `dot_dmg_pct` | ⑤ | ✅ | `is_dot=True` 히트에만 가산 |
| `split_dmg_pct` | `split_dmg_pct` | ⑥ | ✅ | `is_split=True` 히트에서 ⑥에 합산 |
| `charge_dmg_pct` | `charge_dmg_pct` | ④ | ✅ | 평문 「차지 대미지 N% ▲」. `full_charge_mult%`에 퍼센트포인트로 가산 |
| `charge_dmg_mag_pct` | `charge_dmg_mag_pct` | ④ | ✅ | 「차지 대미지 N% **배율** ▲」. **무기 기본 배율에만** 곱한다 — `full_charge_mult% × (1+Σmag%) + Σcharge_dmg%`. 배율끼리는 가산 (GAMEPLAY §차지 배율). 소장품 RL·SR이 이 층 |
| `sequential_dmg_pct` | `sequential_dmg_pct` | ⑤ | ✅ | `is_sequential=True` 히트에만 가산 |
| `optimal_range_dmg_pct` | — | ③ | ❌ | 적정거리 대미지 ▲. 미구현. ③의 고정 +30%와 별도 버프 항목 |
| `received_dmg_pct` | `received_dmg` | ⑥ | ✅ | 음수 저장 시 감소 효과 |
| `heal_received_pct` | — | — | ✅ | 받는 체력 회복량 ▲. 회복 경로(timeline `handle_heal_hp_pct`·`_apply_lifesteal`)가 회복량에 `bm.heal_received_mult()` = `1 + 합/100`을 곱한다 — 최대 체력 증가에 딸린 현재 체력 증가(`max_hp_pct`·`hp_caster_based_pct`)는 회복이 아니라 곱하지 않는다(⬜ `DATA_VERIFY.md` §보스 → 니케 피해). 버프가 없으면 배율이 정확히 1.0이다. 기본 경로에서는 체력이 거의 늘 가득 차 있어 결과가 안 바뀐다(하네스 무변동) — 보스 공격 패턴이 있을 때 의미가 생긴다. 크라운 `릴렉스`, 네온 : 비전 아이 `건강한 몸 3`, 앨리스 : 원더랜드 바니 `소중한 큰 당근 3` |
| `element_bonus_pct` | `element_bonus_pct` | ⑦ | ✅ | `is_element_match=True` 시 ⑦에 가산 |
| `normal_atk_dmg_pct` | `normal_atk_dmg_pct` | ① | ✅ | 「일반 공격 대미지 N% **배율** ▲」. `is_normal_atk=True`일 때 **무기 계수 자체에 곱한다** — `coeff × (1 + Σpct)`. 소장품 SG·SMG가 이 층 (GAMEPLAY §차지 배율의 RL·SR과 대칭) |
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
| `reload_ratio_pct` | `reload_ratio_pct` | — | ✅ | 타임라인 처리. 재장전 **1회가 채우는 탄창 비율**(CDN `reload_bullet` 유도값 `1/clip_count`)에 `(1 + val/100)`을 **곱한다** — `CharState._clip_refill()`. 비율이 1 미만이면 클립 장전으로 취급하므로 통짜 무기도 ▼가 걸리면 클립이 된다. 재장전 **속도**(`reload_speed_pct`)와 독립된 축이다: 50% ▼는 1회 시간을 그대로 두고 횟수를 두 배로 만든다(유저 확인 2026-08-28, `docs/DATA_VERIFY.md`). 그레이브 `방열`(버스트 종료 후 50% ▼ → 클립 4회). 상태 담체 수정 전에는 `event:state_end:미래 예지`가 발생하지 않아 이 효과 자체가 죽어 있었다 — `docs/scenarios/그레이브.md` §상태 담체 수정 |
| `attack_speed_pct` | `attack_speed_pct` | — | ✅ | 타임라인 처리. `_current_fire_rate()`에서 발사 속도에 반영 |
| `mg_warmup_speed_pct` | `mg_warmup_speed_pct` | — | ✅ | MG 예열 진행 속도 % (음수 = 감소). `_fire()`의 `warmup_shots` 증가량에 `(1 + val/100)` 배율 적용. -100이면 증가 0(예열 정지). 식음 속도는 영향 안 받음. **양수도 성립** — +100이면 예열 진행 2배(레이 (가칭) `정비 및 보급`). 같은 대상에 +100과 −100이 동시 활성이면 **단순 합산해 0(예열 정지)** 이 맞다(유저 확정) — 레이의 13초 예열 버프와 아스카 `긴급 수복 2`의 3초 감소가 겹치는 구간. 아스카 : WILLE, 레이 (가칭) |
| `accuracy_pct` | `accuracy_pct` | — | ⚠️ | DealForm 어느 항에도 안 들어간다. 단 `timeline.py`의 `_core_hit_prob()`가 탄착군 직경(`base_diameter - acc_slope × accuracy_pct`) 산출에 쓰므로 **코어 보유 적(`core_px > 0`)에서는 코어히트율을 통해 딜에 반영된다**. 기본 보스는 `core_px = 0`이라 무발동. 메카닉 조사 기록은 `docs/mechanics/명중률 탄착군.md` |
| `burst_charge_speed_pct` | `burst_charge_speed_pct` | — | ✅ | 수령자 구분 없는 시전자 기준식. 모든 활성 버프마다 `시전자 발당 기준 게이지×버프값×실제 히트 수`를 가산한다. 시전자의 일반 공격 첫 명중 전에는 CDN `(발당)`, 이후에는 `(대상)`을 참조하며 전환은 게이지 초기화 뒤에도 유지. 첫 일반 공격은 명중 상태를 먼저 갱신하고 그 공격의 게이지를 계산한다. 히트 없는 `burst_charge_pct`에는 기여 없음. `_route_burst_charge()`·`CharState._burst_gain()`. 마나·렐릭 퀀텀 큐브·아니스 : 스타·그레이브가 같은 stat을 쓴다 |
| `optimal_range_max` | — | — | ❌ | 최대 적정 사거리 증가. 미구현 |
| `optimal_range_max_pct` | `optimal_range_max_pct` | — | ✅ | 최대 적정 사거리 **% ▲**(`optimal_range_max`의 비율 표기판). **보스 거리(`enemy.distance`)가 있을 때만** 적정 구간의 최대를 ×(1 + N%) 한다 — 판정의 정본은 `buff_manager.in_optimal_range`. 거리가 없으면(기본 경로) 무기군 목록 판정이라 딜 기여 0이다(2026-08-17 「파싱만」 결정을 2026-09-18 거리 모델로 풀었다). 레오나 `우렁찬 포효` |
| `optimal_range_min` | `optimal_range_min_pct` | — | ✅ | 최소 적정 사거리 % ▲ — **최소 거리를 N% 줄여** 구간을 가까이까지 넓힌다(유저 결정 2026-09-18 — 에이드 : 에이전트 바니 4.44% × 10중첩 + 55.56% = 100%면 SR 45~100이 0~100). 보스 거리가 있을 때만(`in_optimal_range`). 에이드 : 에이전트 바니 `스파이 렌즈`·`최첨단 요원 장비` |
| `explosion_range` | `explosion_range` | — | ✅ | 폭발 범위 ▲(%). **대미지 식에는 안 들어간다** — `get_buffs`가 합산하고, 보스 패턴 좌표 off에서는 발사체 폭발이 닿는 파츠·저지원 위치 단계를 가르고(합 100% 이상이면 4단계 표적까지 — `boss_pattern.hit_reach`), **좌표 모드에서는 폭발 원의 반지름을 ×(1 + N%)** 한다(`CharState._area_radius` — CDN `spot_explosion_range` × `explosion_scale`). 네온 : 비전 아이 · 라플라스 · 라피 : 레드 후드 · 베스티 : 택티컬 업 · 아니스 : 스타 |
| `pierce_range` | `pierce_range` | — | ✅ | 관통 범위 ▲(%). `explosion_range`와 같은 자리 — 좌표 off는 관통이 닿는 단계를 가르고(합 100% 이상이면 2단계 파츠·저지원까지), 좌표 모드는 관통 원(착탄점 주변, 기본 `pierce_px` 25px ⬜)의 반지름을 ×(1 + N%) 한다. 도로시 : 세렌디피티 · 레드 후드 |
| `pierce_enabled` | `pierce_enabled` | — | ✅ | boolean 플래그. `get_buffs()` boolean 분기에서 `True` 세팅. `_fire()`/`_tick_charge()`에서 `is_pierce_damage`에 반영 |
| `fullburst_duration` | `fullburst_duration` | — | ✅ | 게임 내 동작은 instant이나, `switching→full_burst` 진입 시점에 값을 읽어야 하므로 buff로 등록해 보관. `BurstController.tick()`의 switching 단계에서 `bm._active`를 순회해 합산 후 `_full_burst_end_t` 결정. `burst_cast` 타이밍으로 등록된 버프는 해당 캐릭터가 이번 사이클의 3단계 발동자(`_fb_caster`)일 때만 반영 — 본인 버스트 때만 지속 시간을 바꾸는 캐릭터 지원. 모든 풀버스트에 적용되는 캐릭터는 `passive` 등 다른 타이밍을 사용하면 `_fb_caster` 조건 없이 항상 반영됨 |
| `effect_interval` | — | — | ✅ | `target_effect`가 가리키는 `every:Ns` 효과의 주기를 **초 단위로 가감**. `tick()`의 `every:Ns` 루프에서 `_active`를 탐색해 `stat=="effect_interval" and target_effect==eff["name"]`인 버프 값을 합산, `base_interval + flat`에 `skill_cooldown_pct` 배율을 곱한다. **런타임 조건을 재평가한다**(2026-09-10) — 조건부 영구 버프는 조건이 거짓이어도 `_active`에 등록되므로, 안 보면 꺼져 있어야 할 주기 단축이 그대로 먹는다(엠마 : 택티컬 업 `포메이션 LT 5~7`은 은화가 없으면 30초 주기를 유지해야 한다). `_STAT_TO_BUFF` 매핑 없음. `target_effect` 필수. 에이다 `섬광 수류탄 투척 발동 시간 조건`(조건 없음 — 이 변경의 영향 밖) |
| `dmg_scale_mag_pct` | — | — | ✅ | 특정 효과(`target_effect`)의 대미지 배율 N% ▲. `_handle_damage_eff`에서 `bm._active`를 탐색해 `stat=="dmg_scale_mag_pct" and target_effect==eff_name`인 버프를 찾아 `coeff *= (1 + mag/100)` 적용. `_STAT_TO_BUFF` 매핑 없음 (`buff` type으로 `_active`에 등록됨) |
| `atk_buff_mag_pct` | — | ② | ✅ | 특정 named buff(`target_effect`)의 `atk_caster_based_pct` 값 N% ▲. `get_buffs()` 후처리 `atk_caster_based_pct` 루프 안에서 `atk_buff_mag_pct` 버프를 탐색해 `coeff * (1 + N/100)` 배율 적용. `_STAT_TO_BUFF` 매핑 없음 |
| `received_dmg_buff_mag_pct` | — | ⑥ | ✅ | 특정 named buff(`target_effect`)의 `received_dmg_pct` 값 N% ▲. `atk_buff_mag_pct`와 같은 층이고 곱하는 대상만 다르다 — 적에게 붙은 「받는 대미지 ▲」 디버프의 수치를 `(1 + N/100)`배. 원문 「[효과명] 받는 대미지 증가 **배율**이 N% 증가 상태로 변경」. `target_effect` 필수, `fixed_value`에 N. `get_buffs()` 후처리에서 `_by_stat`으로 증폭 버프를 찾고 `_by_name(target_effect)`의 `received_dmg_pct` 버프 중 **같은 대상에게 걸린 것**만 골라 **증분(`base × N/100`)만** 더한다 — 기본값은 본 루프가 이미 더했으므로, 증폭이 없는 기존 로스터의 합산 순서·부동소수점 결과가 그대로 유지된다. `_STAT_TO_BUFF` 매핑 없음. 엠마 : 택티컬 업 `환경 조성 강화`(환경 조성 3.9% → 7.8%) |
| `lifesteal_pct` | `lifesteal_pct` | — | ✅ | 대미지 × lifesteal_pct% 만큼 시전자 HP 회복. `event:heal_received` 발생 |
| `armor_break_dmg_pct` | `armor_break_dmg_pct` | ⑤ | ✅ | `is_armor_break_damage=True` 히트에만 가산. ②에서 적 방어력 0 처리 |
| `projectile_dmg_pct` | — | — | ❌ | 「적 발사체 공격 시 해당 발사체에 가하는 대미지 N% ▲」. **미구현 유지**(유저 결정 2026-09-20) — 계산기 적 모델에 파괴 가능한 발사체가 없어(`all_projectiles`는 빈 리스트) 지금은 딜 기여가 0이지만, 발사체 모델이 들어올 여지를 남겨 🚫로 내리지 않는다. 파싱은 정상 등록하고 시나리오에는 네거티브 항목으로 둔다. **저지 부위(`intercept_dmg_pct`)와 다른 축이다** — 사쿠라가 한 캐릭터 안에 둘 다 가진 첫 사례. 보유: 사쿠라 `꽃잎 떨구기` · 일레그 `쇼트` · 클레이 `DON'T MISS!` |
| `projectile_attachment_dmg_pct` | `projectile_attachment_dmg` | ⑤ | ✅ | `is_projectile_attachment=True` 히트에만 가산 |
| `projectile_explosion_dmg_pct` | `projectile_explosion_dmg` | ⑤ | ✅ | `is_projectile_explosion=True` 히트에만 가산 |
| `burst_stage_override:N` / `burst_stage_override:reenterN` | — | — | ✅ | 타임라인 `_rebuild_burst_order()` / `_check_reenter()`에서 처리 |
| `element_code_override` | — | ⑦ | ✅ | 특정 코드 적에게 우월 코드 적용. 버프가 활성이고 `target_code`가 적 코드와 같으면 로스터 코드 상성과 **OR**로 합쳐 `is_element_match`를 세운다 (`BuffManager.element_override_match` → `CharState.element_match`). 버프라서 조회 시점에 평가하며 캐싱하지 않는다. **로스터의 `element_code`는 바뀌지 않으므로** `allies_code:` 같은 대상 판정에는 영향이 없다 (`docs/scenarios/센티.md`). `_STAT_TO_BUFF` 매핑 없음 |
| `trigger_count_reduce` | — | — | ✅ | `_dispatch_instant`에서 처리 |
| `shield_dmg_pct` | — | — | ❌ | 보호막 대미지 ▲. 미구현 |
| `cover_def_pct` | — | — | 🚫 | 엄폐물 방어력 ▲. 엄폐물은 방어력 없이 피해를 그대로 받는다. 첫 보유자 솔져 O.W. `오울 윈드`(2026-09-24 파싱) — 딜 0 |
| `cover_hp_pct` | — | — | ✅ | 엄폐물 최대 체력 ▲. `bm.cover_max_hp()` = 기본값(`config["cover_hp"]`, 임의값) × (1 + 비례 합/100) + Σ 시전자 최종 최대 체력 × N%. 원문 「시전자의 최대 체력 비례 엄폐물 최대 체력 N% ▲」는 `scaling: "max_hp"`(`cover_heal_pct`와 같은 표기) — 시전자 기준 항으로 간다. **기본값이 임의값이어도 배율은 얹는다**(유저 결정 2026-09-15). 보스 패턴이 있을 때 프레임마다 `bm.sync_cover_hp()`가 증감을 현재 체력에 옮긴다(늘면 같이 차고 줄면 잘린다, 부서진 엄폐물은 그대로). `get_buffs`에 자리가 없는 직접 조회 stat이다(`_DIRECT_READ_STATS`). 소장품 `마음의 버팀목` · 렐릭 커버 큐브 `커버 헬스 업 HC`(`scaling: max_hp`, `scraper/cdn_tables.py` `CUBE_SCALING`) · 티아 `카멜레온 은신술` |
| `cover_revive` | 타임라인 `handle_cover_revive` | — | ✅ | `엄폐물 체력 N%로 엄폐물 부활`(2026-09-20 구현) — **부서진 엄폐물을 되살린다.** 회복 계열은 부서진 엄폐물을 건드리지 않는 것이 규약이고(`cover_heal_pct` 행), 이 stat이 그 예외를 여는 유일한 경로다. `bm.revive_cover()`가 `break_cover()`의 역으로 `cover_hp`를 채우고 집계 캐시를 비운다 — `self_cover_alive`·`not_self_cover_alive` 판정이 같은 프레임에 뒤집혀야 하기 때문이다. 기준은 표기가 없으므로 **그 대상의 엄폐물 최대 체력**(`state["cover_max_hp"]`)의 N%이고 **살아 있는 엄폐물은 건너뛴다**. `event:cover_healed`는 보내지 않는다(원문이 「회복」이 아니라 「부활」 — ⬜ 인게임 미확인, 유일한 소비자는 티아 `파충류 애호가`). 엄폐물은 보스 공격 패턴이 있을 때만 부서지므로 기본 경로에서는 대상이 0기라 무발동이다. **같은 `burst_cast`에 `cover_hp_pct`가 함께 걸리면 순서가 결과를 바꾼다** — 원문 블록 순서대로 부활이 먼저 들어가 그 시점의 최대 체력 기준으로 채우고, 뒤이어 최대치가 오르면 `sync_cover_hp`가 늘어난 만큼 현재 체력에 얹는다(베이 실측: 26,000 → 다음 프레임 686,596). 비스킷 `산책 훈련`(`allies_broken_cover_random:2`) · 베이 `퍼스트 위너`(애장품 3, `self` + `not_self_cover_alive` + `max_trigger:1`) |
| `outgoing_heal_pct` | — | — | ❌ | 주는 회복량 ▲. 힐 모델 없음 |
| `shield_from_max_hp_pct` | — | timeline | ✅ | 시전자의 유효 최대 체력 N%만큼 대상별 보호막 생성. 지속시간 동안 `during_shield` 활성, 적용 대상에게 `event:shield_applied` 통지. **효과에 `end_on_shield_consumed: true`가 있으면 보호막이 다 깎일 때 담체 ActiveBuff도 끝난다** (`_end_shield_carrier` — `event:state_end:[이름]`까지 정상 발생). 기본은 off라 다 깎여도 `_active`에 남고 `self_state:[이름]`이 계속 참인데, 보호막이 곧 상태인 버프는 그러면 「보호막이 없을 때」 분기가 어느 모드에서도 안 열린다(킬로 `나노 코팅`). ⬜ 인게임에서는 모든 보호막이 이럴 가능성이 높으나 **확인된 것만 켠다** — 일괄로 켜면 이름이 상태로 참조되는 다른 보호막(폴리 `폴리스 뱃지`)의 발동 시점이 앞당겨진다. `during_shield`는 `has_shield()`가 잔량을 보므로 이 필드와 무관하게 정상이다 |
| `shared_shield_from_max_hp_pct` | — | timeline | ✅ | 아군 공용 보호막. 시전자의 유효 최대 체력 N%만큼 생성하되 **부여 대상은 시전자 1인**(텍스트에 대상 표기가 없어도 `all_allies`가 아니다). `_SHIELD_STATS`로 `shield_from_max_hp_pct`와 같은 경로를 타 `during_shield`·`event:shield_applied`도 동일하게 성립한다. 블랑 `럭키 가드` |
| `next_shield_hp_pct` | — | — | ✅ | 다음 보호막 체력 N% ▲. 보호막이 **대상에게 적용되는 순간**(`_activate()` 보호막 후처리) `bm.take_next_shield_amp()`가 그 대상의 이 버프를 꺼내 생성량에 `(1 + N/100)`을 곱하고 소모한다 — 누가 만든 보호막이든 받는 쪽 기준이고, 여럿이면 합산해 전부 소모한다(⬜ `DATA_VERIFY.md` §보스 → 니케 피해). 보호막량만 바뀌어 기본 경로의 딜은 무관하다. 델타 : 닌자 시프 `비기 : 닌자 오버드라이브 4` |
| `dmg_accum_dealt_atk_pct` | — | — | ✅ | **시전자가 가하는 대미지를 누적하고 상한은 시전자 최종 공격력의 N%.** 적에게 걸리는 **누적기의 담체**라 이 이름이 `not_target_state:` · `event:accum_full:[이름]` · `accum_split_damage`의 `target_effect` 참조 대상이 된다. 상한은 **부여 시점 스냅샷**이고(`ActiveBuff.accum_cap`, `_activate()` 후처리에서 `final_atk()`로 확정) 재평가하지 않는다 — 유저 결정 2026-09-22. 재평가로 두면 버스트의 공격력 ▲가 상한을 누적 가속과 같은 폭으로 밀어 올려 트로니가 영구 무발동이다(`docs/scenarios/트로니.md` §실측). 누적은 `timeline._land_boss()` → `bm.accumulate_damage()`, 상한 도달 시 `event:accum_full:[이름]`. 「대상이 **받는**」을 모으는 `dmg_accum_received_atk_pct`와 원천이 반대다. 트로니 `누적 폭발 스킬` |
| `dmg_accum_received_atk_pct` | — | — | ✅ | **대상이 받는 대미지를 누적**하고 상한은 시전자 최종 공격력의 N%. 시전자뿐 아니라 **스쿼드 전체**의 딜이 들어오는 것이 위 키와의 차이다(`accumulate_damage`가 `dealt` 쪽만 시전자를 거른다). 비율 블록이 따로 없으면 100%다(원문 「일괄 누적」 — `_accum_rate()`의 기본값). 상한에 닿아도 터지지 않고 **만료**로 방출한다 — 짝인 `accum_split_damage`가 `event:state_end:[이름]`에 걸린다. 델타 : 닌자 시프 `인법 IFAK`(`heal_overcharge_store_atk_pct`)의 대미지판이다. 도로시 `낙인` |
| `dmg_accum_rate_pct` | — | — | ✅ | 누적 비율 N%. `_accum_rate()`가 `target_effect` **없는** 항목을 기준 비율로(트로니 `누적 폭발 스킬 2` — 50%), `target_effect == 담체 이름`인 항목을 **가산 증분**으로 읽는다(트로니 `메가 T.Rony 2` — +62.83%p → 112.83%). 원문에 「배율」이 없으므로 곱하지 않는다. 실측: 버스트 밖 50.00 · 버스트 안 112.83. **트로니에게는 이 증분이 메카닉 전체의 게이트다** — 빼면 문턱에 한 번도 닿지 않아 본인 딜 −42.86%로 누적기를 통째로 뺀 것과 값이 같다 |
| `accumulate_max_scale_pct` | — | — | ❌ | 특정 효과의 최대 누적량 N% ▲. `target_effect` 필수. 미구현 |
| `heal_overcharge_store` | — | — | ❌ | 초과 회복 저장. 미구현 |
| `heal_overcharge_store_atk_pct` | — | — | ❌ | ATK N%까지 받는 회복량 저장. 힐 모델 없음 |
| `shield_restore_pct` | — | — | ❌ | 보호막 회복 ▲. 미구현 — **로스터에 쓰는 효과가 없다**(2026-09-14 확인). 보호막 소모 모델(보스 공격)은 있으므로 사용처가 생기면 `absorb_shield`가 깎은 양을 되돌리는 자리에 얹는다 |
| `buff_max_stack_add` | — | — | ✅ | 중첩 가능 이로운 효과의 **현재 중첩 N개 ▲**(2026-09-24 구현 — 유저 결정). **buff로 파싱돼 있지만(원문에 유지 블록이 없어 `duration: -1`) 동작은 즉발이다** — `_activate()`가 담체를 남기지 않고 `_apply_buff_max_stack_add()`로 그 자리에서 중첩만 올린다(로그는 instant 이벤트). 대상 아군에게 걸린 `type: buff` · `polarity: beneficial*` · `max_stack` ≠ 1인 효과가 대상이고 **누가 걸었든** 가리지 않는다. 상한은 그대로라 넘기지 못하며 이미 최대 중첩인 버프는 no-op(유저 확인 2026-09-21 — 니케에 최대 중첩 자체를 늘리는 효과는 없다). 중첩이 오르면 `buff_stack_add`처럼 지속시간을 갱신하고 `stack_reach`를 낸다. `stack_change_immune` 수령자는 뺀다. **근사 하나**: 한 ActiveBuff가 수령자와 비수령자를 함께 대상으로 잡고 있으면 건너뛴다 — 중첩이 ActiveBuff 하나에 공유돼 올리면 비수령자까지 오르기 때문이다(`bm.partial_skips`로 센다). 코드 한정 공급원이 아군 전체 스택 버프를 만날 때만 생긴다 — S40_플로라에서 `피튜니아 2`(전격 = 플로라·목단)가 `피튜니아`(인접 3인)를 만나는 것이 그 경우라 **총딜 불변**이다. 키 이름의 `max`는 2026-09-21 이전 오독(상한을 올린다)의 잔재다. 보유: 플로라 · 앨리스 : 원더랜드 바니 `당근과 토끼 파티 2` · 미카 : 스노우 버디 `응원의 축포` · 길티 `같이 놀자아….` · 디젤 `딸기향 이끌림 3`(애장품 3단계) · 페퍼 `페퍼 테라피 2` · 루피 `프라이즈` · 소다 `대청소 메이드 3` |
| `burst_dmg_single_pct` | `burst_dmg_single_pct` | ⑤ | ✅ | 단일 대상 버스트 대미지 ▲. **첫 보유자 자칼 `크레이지 자칼`**(2026-09-20, 같은 날 구현). `_factor5()`의 `is_burst_damage` 블록 **안**에서 `hit_type["is_single_burst"]`일 때만 가산 — AoE판과 **배타**이고, 구조적으로 `bonus_damage`가 탈 수 없다. 플래그는 `timeline.simulate` `_handle_damage_eff`가 `base_stat`이 버스트딜이고 `target`이 `enemies_`로 시작해 `:1`로 끝날 때 세운다. 원문은 「대상 설명이 `~ 적 1기에게`로 끝나는 버스트 스킬 대미지 ▲」라 `burst_dmg_aoe_pct`(「`적 전체에게`로 끝나는」)의 단일대상판이자 배타다 — 판정도 같은 층에서 `target` 문자열로 한다(`enemies_*:1` 계열만. `대상에게`(`target`)·`타겟에게`(`boss`)·`동일 적 대상에게`(`same_target`)는 문구가 달라 제외). AoE판과 같이 **같은 clause의 `bonus_damage`·`dot_damage`는 비대상** |
| `burst_dmg_aoe_pct` | `burst_dmg_aoe_pct` | ⑤ | ✅ | 전체 대상 버스트 대미지 ▲. `_factor5()`의 `is_burst_damage` 블록 **안**에서 `hit_type["is_aoe_burst"]`일 때만 가산 — 구조적으로 `bonus_damage`가 탈 수 없다. 플래그는 `timeline.simulate` `_handle_damage_eff`가 `base_stat=="burst_damage" and target=="all_enemies"`로 세운다. **AoE 판정 기준**: 버스트 스킬의 대상 설명이 `적 전체에게`로 끝나는 효과 — `적 전체에게(파츠 포함)`처럼 괄호 부연이 붙어도 포함한다(레이븐). **같은 clause의 `bonus_damage`·`dot_damage`는 제외** — "버스트 스킬 대미지"만 증폭한다(이사벨 `타겟 마킹 2·3` 추가 대미지는 비대상, 유저 확인). 트리나 `뻗은 뿌리`/`시든 뿌리` |
| `burst_cooldown` | `burst_cooldown` | — | ✅ | buff 상태로 지속. `BurstManager.tick()`의 `full_burst_start` 분기가 풀버스트 1회당 1회씩 `burst_ready_at`을 당긴다 (`_cd_applied_at_cast`로 cast 시 반영분 중복 방지) |
| `skill_cooldown` | — | — | ✅ | 개별 스킬 쿨타임 **초** 감소(`target_effect`로 대상 효과 지정, 음수 = 감소). `tick()`의 `every:Ns` 루프에서 `effect_interval`과 **같은 자리·같은 식**으로 합산된다(`base_interval + flat`) — 원문 문구만 다르고(「스킬 N 재사용 시간 N초 ▼」 ↔ 「[효과명] 발동 시간 조건 N초 ▼」) 연산이 같아 경로를 합쳤다(2026-09-22). 런타임 조건 재평가·잔여 쿨 비례 재조정·5% 하한이 그대로 적용된다. 도로시 `발현`이 첫 보유자(20s → 2s, 발현 10초 창에 초토화 5회) |
| `skill_cooldown_pct` | `skill_cooldown_pct` | — | ⚠️ | 스킬 쿨타임 % 감소. `tick()`의 `every:Ns` interval에 반영. `target_effect` 미지원 — target 캐릭터의 모든 `every:Ns` 스킬에 일괄 적용 |
| `stun` | — | — | ✅ | 기절. `bm.is_stunned(name)`: `_active`에서 `stat=="stun"` 버프 유무로 판별. 일반공격(`CharState.tick()`)·버스트 사용(`BurstController._try_use_stage()`) 차단. 기절 중 버스트 단계는 만료까지 매 프레임 재시도 |
| `invincible` | — | — | ✅ | 무적. 보스 공격(`timeline._boss_attack`)의 **체력 피해만** 0으로 한다 — 보호막·엄폐물은 그대로 깎이고 피격 이벤트도 나간다(⬜ `DATA_VERIFY.md` §보스 → 니케 피해). 판정은 `bm.has_live_stat()` |
| `shield_invincible` | — | — | ✅ | **「자신이 설치한 보호막 무적」**(2026-09-22 구현). 이 버프를 든 캐스터가 만든 보호막이 버프가 사는 동안 깎이지 않는다. 위 `invincible`과 **정반대 축**이다: 저쪽은 체력만 지키고 보호막은 깎이게 두는데, 이쪽은 보호막만 지킨다. 대상이 아니라 **시전자**로 가른다(`absorb_shield()`가 `ab.caster`를 `has_live_stat`로 본다) — 남이 걸어 준 보호막은 무적이 아니다. **막아 내되 잔량이 줄지 않는다** — 그 한 발은 거기서 끝나고 체력·엄폐물로 넘어가지 않는다(`invincible`이 자기 층에서 피해를 0으로 만드는 것과 같은 규약). 판정이 흡수 시점이라 같은 프레임에 무적과 보호막이 함께 걸릴 때의 **항목 순서와 무관**하다. 보호막은 보스 공격 패턴이 있을 때만 깎이므로 기본 경로 기여 0. 레이블 `망상 공유` |
| `undying` | — | — | ✅ | 불굴. 보스 공격(`timeline._boss_attack`)에서 체력이 0이 될 발을 **체력 1을 남기고** 받는다 — 쓰러지지 않았으므로 `hp_below:T` 임계 이벤트는 정상으로 나간다(⬜ `DATA_VERIFY.md` §보스 → 니케 피해). 무적 판정 뒤에 본다. 판정은 `bm.has_live_stat()` — 지연 resolve 대상(`allies_lowest_hp_excl:1`)도 `_live()`가 그 자리에서 확정한다. 나유타 `부동심`, 블랑 `쇼타임 2` |
| `stealth` | — | — | ✅ | 은신. 고르는 보스 공격(`random:N`·`top_atk:N`)의 후보에서 빠진다(`timeline._attack_targets`). 전원이 은신이면 은신을 무시한다. `[상태명 : 1인 공격 대상에서 제외 직접 피격 시 해제] [N초 유지]` 문형의 정본 표기다(`PARSING.md` §6) — 뒤쪽 해제 조건은 `received_hit_count:1` + `remove_named_buff` 즉발로 따로 적는다. 로산나 `은신`, 델타 : 닌자 시프 `인법 카모플라쥬 2` |
| `decoy` | — | — | ✅ | **분신 생성**(2026-09-21 구현). 「시전자의 **최종** 최대 체력 비례 N% 분신」 — 부여 시점에 `effective_max_hp(caster) × N%`를 `ActiveBuff.decoy_per_target`에 싣는다(보호막 `shield_per_target`과 같은 모양, 재발동하면 체력·상한이 함께 새로 잡힌다). **층은 보호막·엄폐물 다음, 체력 바로 앞**이다(유저 2026-09-21 — 신데렐라 계열의 인게임 거동). `timeline._land_squad`가 체력에 들어갈 몫이 남았고 **엄폐 중이 아닐 때만** `bm.absorb_decoy()`를 부른다 — 엄폐물이 *엄폐 중에* 대신 맞는 것의 짝으로 분신은 *사격 중에* 대신 맞으므로 **둘은 사실상 배타**다. 받았으면 체력에는 아무것도 안 남고 **관통도 가르지 않는다**(관통은 막을 뚫는 성질이고 분신은 다른 개체다). 체력이 0이면 **담체 버프가 끝난다** — 보호막의 `end_on_shield_consumed`와 달리 옵트인이 아니라 기본 동작이다(부서진 분신이 남으면 `self_state:디코이`가 계속 참이라 신데렐라 `아름다움`과 라이의 회복 둘이 없는 분신을 참조한다). ⬜ **인게임 미확인 셋**(`DATA_VERIFY.md` §보스 → 니케 피해): ① 층 순서 ② 「엄폐 중이 아닐 때만」 ③ 적이 분신을 *조준*하는지(도발처럼 대상 자체가 바뀜) 아니면 주인이 맞은 피해를 대신 받는지 — 후자(흡수 층)로 모델링했다. 분신은 **보스 공격 패턴이 있을 때만** 깎이므로 기본 경로 딜 기여 0. 보유: 신데렐라 · 신데렐라 : 크리스탈 웨이브(둘 다 `[지속]`) · **라이 `디코이`**(`[240초 유지]` — 유한 지속 첫 사례) |
| `infinite_ammo` | `infinite_ammo` | timeline | ✅ | boolean 플래그. 활성 중 일반 공격은 장탄을 줄이지 않고 `squad_ammo_consume`도 발생시키지 않으며, 장탄 0에서도 재장전 없이 발사한다. 활성 시 진행 중 재장전은 완료 이벤트 없이 취소하고 남은 장탄을 보존한다. 그레이브 `미래 예지`, 나유타 `고행 3` |
| `focus_fire` | — | — | ✅ | 사격 집중. **좌표 모드에서** 받은 니케가 카메라 니케의 조준점을 따른다(`timeline._resolve_aims`가 `bm.has_live_stat`으로 묻는다 — 풀버스트 동안은 버프 없이도 전원이 이 상태다, 유저 확인 2026-09-18). 좌표 모드가 아니면 조준점이 없어 무영향. 리틀 머메이드 `버블 오더`(`focusing` 조건) |
| `enemy_movement_disable` | — | — | ❌ | 적 이동 불가. 적 이동 모델 없음. **첫 보유자 유니 `BDG 2`**(2026-09-21, `[5초 유지]`) — 그전까지 로스터에 보유자가 0명이었다 |
| `force_move` | — | — | ❌ | `공격 범위 중심으로 강제 이동`. 적 이동 모델이 없어 `enemy_movement_disable`과 같은 자리다. **첫 보유자 얀 `일확천금 2`**(2026-09-21, `[2초 유지]`). **2026-09-21 이전에는 instant 테이블에 🚫 `_unparseable`로 있었다** — 보유자가 0명이라 드러나지 않았고, `[N초 유지]`가 붙은 원문이 실제로 나오면서 instant로는 담을 수 없음이 확인됐다(instant는 지속시간을 담지 못한다 — `targeting_exclude` → `stealth` 선례와 같은 이유). 「딜에 영향이 없어도 스킬 문구는 전부 파싱한다」(유저 지시 2026-09-05)에 따라 `_unparseable`도 걷었다 |
| `debuff_immune` | `debuff_immune` | — | ✅ | `_activate()`에서 harmful 효과 차단. 보스 디버프(`bm.apply_boss_effect`)도 같은 판정(`_harmful_blocked`)이다 — `harmful_irremovable`은 거르지 않는다 |
| `debuff_immune:[name]` | — | — | ✅ | `_activate()`에서 `debuff_immune:{eff_name}` 차단. `_has_immune()` 직접 탐색으로 `_STAT_TO_BUFF` 매핑 불필요 |
| `debuff_immune_count` | — | — | ✅ | **개수 제한 면역**. 원문 `[해로운 효과 면역 N개]`의 N을 `fixed_value`(레벨별이면 `values`)에 싣고, harmful 하나를 막을 때마다 하나를 소모한다 — 무제한인 `debuff_immune`과 다른 축이다. 개수는 `debuff_cleanse`와 같이 **대상 니케 1인당**이다(보스 디버프가 니케마다 따로 붙으므로). **잔량은 버프 이름 단위 풀**이라, 같은 이름을 여러 경로로 부여해도 합이 아니라 최대값 하나를 공유한다(`_consume_immune_charge`) — 에이드가 같은 `완벽한 메이드`를 스킬1(전투 시작)·스킬2(일반 공격 420회) 두 경로로 부여하고 `[1 중첩]`이라 인게임에서 총 1개이기 때문이다. **재부여가 소모량을 0으로 되돌린다**(`_immune_used`) — 그게 두 번째 블록의 역할이다. 소모는 `_harmful_blocked()`에서 일어나고 부르는 쪽이 `_activate` 대상 필터와 `apply_boss_effect` 둘뿐이라 이중 소모가 없다. ⬜ 잔량이 0이 된 버프는 `_active`에 남아 있기만 하고 막지 않는다 — 인게임처럼 아이콘이 사라지지는 않는다. 보스 공격 패턴이 없으면 막을 harmful이 없어 무발동. 에이드 `완벽한 메이드` |
| `stun_immune` | `stun_immune` | — | ✅ | `bm.is_stunned()`에서 `_has_immune(name, "stun_immune")` 체크로 기절 차단 |
| `charge_speed_buff_immune` | `charge_speed_buff_immune` | — | ✅ | `get_buffs()` 후처리에서 `_quant_parts["charge_speed_pct"]` 중 **양수 기여만** 제거. **스킬 버프만 면역**이고 `_source_tag`가 `equipment`(오버로드)·`cube`인 기여는 남긴다 (유저 확인, 2026-09-02 — `_CHARGE_IMMUNE_EXEMPT_SOURCES`). 소스를 가리지 않는 것은 `charge_time_fixed` 쪽이다 |
| `charge_speed_debuff_immune` | `charge_speed_debuff_immune` | — | ✅ | 위와 같되 **음수 기여만** 제거 |
| `charge_time_fixed` | `charge_time_fixed` | — | ✅ | 차지 시간을 `fixed_value`초로 **절대 고정**. 플래그는 `get_buffs()` 후처리에서 `charge_speed_pct = 0` — **소스를 가리지 않아 오버로드·큐브까지 무시한다**(효과 면역과의 차이), 실제 초는 `CharState._fixed_charge_time()`이 `bm._active`를 직접 읽는다. **무기 표기 차지 시간(base)을 후보에 넣지 않는다** — base보다 **짧게** 고정하는 경우가 있다(맥스웰 : 오디너리 미케닉 3.0초 모드 안에서 0.4초). 복수 활성이면 **최신값**(`activated_at`, `uid` 순)이 이긴다 — 고정값은 모드 진입/종료로 갈아끼워지는 형태가 정본(스노우 화이트 : 헤비암즈는 모드 종료 시 `event:state_end`로 원래 값을 재부여한다). `fixed_value` 없이 stat만 있으면 base 유지 = 차지 속도 버프만 무시(아니스 : 스타 `슈팅 스타2`) |
| `reload_time_fixed` | (타임라인 전용) | — | ✅ | **레벨별 `values`도 읽는다 (2026-08-16 수정).** `_fixed_reload_time()`이 `bm._get_value(ab.effect, ab)`를 쓰므로 `fixed_value`·`values` 양쪽이 후보가 된다 — 이전에는 `fixed_value`만 봐서 `values`만 있는 항목이 후보에서 빠지고 고정이 통째로 무시됐다(재장전이 기본 시간으로 복귀). **"고정"은 *다른 버프를 안 받는다*는 뜻이지 *레벨과 무관하다*는 뜻이 아니다**(유저 확인) — 질 `슈퍼 캅`은 `[재장전 속도 {0}% 증가 상태로 고정]`이라 Lv1 0.454s ~ Lv10 0.0004s로 레벨마다 다르다. **`charge_time_fixed`는 아직 `fixed_value`만 읽는다** — 같은 문형이 나오면 같은 수정이 필요하다. 재장전 시간을 그 값(초)으로 **절대 고정** — `reload_speed_pct`를 무시한다. `charge_time_fixed`와 같이 `fixed_value` 계열이라 `get_buffs()` 합산 경로를 타지 않고 `CharState._fixed_reload_time()`이 `bm._active`를 직접 읽는다(`_start_reload`에서 사용). **복수면 최대값** — `charge_time_fixed`와 달리 최신값이 아니다(이 stat은 갈아끼우는 사례가 아직 없어 기존 시맨틱 유지). `_STAT_TO_BUFF` 매핑 없음. 신데렐라 : 크리스탈 웨이브 `변경 준비` |
| `stack_change_immune` | `stack_change_immune` | — | ✅ | `_dispatch_instant()`에서 스택 변경 차단. `buff_stack_add`/`buff_stack_remove`는 **시전자 자신의 스택만** 건드리므로(`affected = [c for c in target_chars if c == caster]`) 이 면역은 남이 내 스택을 깎는 경로가 생길 때 비로소 갈린다. 나유타 `기억 흡수 2` |
| `atk_copy` | — | — | ❌ | **공격력 복제** — `copy_from`이 가리키는 아군의 공격력 N%를 수령자에게 더한다. 2026-09-20에 **첫 보유자 길티**(`빌려 갈게에….`)가 나오면서 `_unparseable` 표기를 걷고 정식 스키마로 옮겼다. `atk_caster_based_pct`와 같은 층(수령자 `atk_flat` 가산)이고 **읽는 원본만 다르다** — 저쪽은 시전자, 이쪽은 `copy_from`으로 푼 캐릭터다. **원본 선택은 최종 공격력, 복제하는 값은 버프 제외 기본 공격력**이다(원문에 `최종`이 없다 — `GAMEPLAY.md` §값 산정). 기본값을 읽으므로 원본이 자기 자신이어도 복리가 생기지 않는다. 미구현 |
| `hp_copy` | — | — | ❌ | **최대 체력 복제** — 위와 같은 축이고 스탯만 최대 체력이다. `copy_from` 아군의 **기본** 최대 체력 N%를 수령자의 최대 체력에 더한다(`hp_caster_based_pct`와 같은 층, `effective_max_hp()`의 `bonus_flat`). 첫 보유자 신 `센텐스 엔딩스`·퀀시 `새로운 루트`(2026-09-20). 미구현 |
| `received_dmg_split` | — | — | ❌ | 받는 대미지 **차등** 분배. **로스터 유일 보유자가 베이**다(2026-09-20 파싱 — 원문 전수 검색 결과 균등판 보유자는 아니스·폴리·율하·자칼 4명이고 차등판은 베이뿐). 균등판(`received_dmg_split_even`)과 달리 **분배 비율이 원문에 없다** — 「차등」이 무엇을 기준으로 갈리는지(방어력·최대 체력·시전자 고정 지분 등)를 정하는 근거가 레포에도 `REFERENCES.md`의 어느 출처에도 없다. **구현 규격 미정이라 ❌로 둔다**(유저 결정 2026-09-20 — 규격이 확인되면 그때 구현한다. 방어 효과라 시뮬 딜에는 영향이 0이다). 짝인 target `self_cover`도 같은 이유로 ❌로 함께 보류한다 — 그것만 구현하면 `치얼업 투게더`가 아무 일도 하지 않는 채로 활성화되기만 한다. 베이 `유 캔 두잇`(`all_allies`) · `치얼업 투게더`(`self_cover` — 같은 stat이 엄폐물에 걸리는 첫 사례) |
| `received_dmg_split_even` | — | — | ✅ | 받는 대미지 **균등** 분배(2026-09-20 구현). 보스 공격 한 발의 피해를 집단 머릿수로 나눈다 — `bm.split_group(name, t)`가 산 멤버를 풀고(`_live()`가 지연 resolve까지 확정), `timeline._boss_attack`이 **맞은 니케 기준으로 계산이 끝난 피해**를 `len(group)`으로 나눠 멤버마다 `_land_squad()`에 넣는다. 보호막·엄폐물·무적·불굴은 멤버마다 자기 것이 막고, **피격 이벤트와 공격 부착 디버프는 맞은 니케만** 받는다 (⬜ 인게임 미확인 2건: 멤버마다 자기 방어력으로 다시 계산하는지 · 나눠 진 쪽이 피격 트리거를 받는지). 보스 공격 패턴이 있을 때만 의미가 있고 기본 경로에서는 딜 0이다. 분배 집합은 부여 시점 고정 — `target_chars`가 활성화 때 굳으므로 주기 재부여가 집합을 갈아끼운다. 폴리 `도그 테라피 2`(주기 재부여로 집합이 바뀐다) · 율하 `위크 메이커 2`(`all_allies`) · 자칼 `치얼업 자칼`(전투 시작 1회라 집합 고정) |
| `heal_split` | — | — | ❌ | 회복 균등 분배. 정식 stat으로 파싱한다(`values` 없음 · `neutral`) — 나유타 `위선 3` · 렘 `렘에게 맡겨주세요! 2`·`렘이 치료하겠습니다! 2`. 회복을 집단에 나눠 주는 모델이 없고, 회복은 보스 공격 패턴이 있어야 의미가 생겨 **기본 경로 딜 기여 0** |
| `armor_break_enabled` | `armor_break_enabled` | ②⑤ | ✅ | 일반 공격을 방어력 무시 대미지로 치환(boolean 플래그). `timeline.py`가 `buffs.get("armor_break_enabled")` → `is_armor_break_damage`로 읽고, `damage.py`가 ② 적 방어력 0 처리 + ⑤ `armor_break_dmg_pct` 가산. 치사토 `방어 관통 사격` |
| `gauge_charge_enabled` | — | — | ✅ | buff로 등록. 게이지 충전 가능 상태 활성화. `gauge_id` 필수 |
| `gauge_max_add` | — | — | ✅ | `_dispatch_instant()`의 `gauge_charge`에서 cap 합산 |
| `taunt` | `taunt` | — | ✅ | 도발. **전체 공격(`all`)을 뺀 모든 보스 공격**(`random:N`·`top_atk:N`·`slot:`)의 자리를 도발 중인 니케가 먼저 가져가고 남은 자리를 원래 규칙으로 채운다(`bm.taunters()`, 유저 확인 2026-09-15). 도발에 안 끌리는 공격은 공격 spec `ignore_taunt: true`. **적에게 건 `taunt`는 시전자가 도발자다**(목단 `여긴 내가 맡는다!` — 대상이 `enemies_top_atk:3`) |
| `cover_disabled` | — | — | ✅ | `특이 사항 : 버스트 스킬 시전 중 엄폐 불가` — 무기 변경 모드 동안 엄폐가 막힌다(`values`/`fixed_value` 없음). **구현(유저 결정 2026-09-14, 2026-08-17 「파싱만」 결정을 뒤집음)**: `CharState.cover_blocked()` 한 곳을 정책(버스트 엄폐컨·장전컨)·명시 시퀀스·전체 엄폐가 모두 보고 엄폐 진입을 막는다. **이미 엄폐 중일 때 켜지면 그 프레임에 엄폐가 풀린다**(`CharState._drop_blocked_cover`, 유저 확인 2026-09-15 — 진행 중인 재장전은 끊지 않고, 재장전 로그에 `엄폐 해제(엄폐 불가)`). 켜진 동안의 재장전은 엄폐물 뒤가 아니라 보스 공격을 체력으로 받는다. 무시된 시퀀스 엄폐는 재장전 로그에 `엄폐 불가(시퀀스 무시)`로 남는다. 모드에 종속되므로 `passive` + `self_state:[모드명]` + `duration: -1`로 붙인다. 라플라스 `라플라스 버스터 5`(기본·애장품 2단계), 목단 `정정당당 승부다! 6`(기본만) |
| `lock_on` | `lock_on` | — | ❌ | **스노우 화이트 : 헤비암즈 전용**. 세븐스 드워프 공격 대상 지정 고유 메카닉. `values`/`fixed_value` 없음 |
| `possessed` | — | — | ❌ | **일레그 : 붐 앤 쇼크 전용** 적 마커. `target_state:빙의` 조건 게이팅용. **원본 일레그에는 필요 없다** — 그쪽은 방어력 디버프 자신이 `붐 인스톨`이라는 이름을 갖고 있어 담체가 이미 있다(`docs/scenarios/일레그.md`). `_STAT_TO_BUFF` 매핑 없음 — `_active`에만 등록되어 name 기반 condition 매칭. `values`/`fixed_value` 없음 |
| `effect_target_count_add` | — | — | ❌ | 특정 효과의 **타격 대상 수** N 증가 (`target_effect` 필수, `fixed_value`에 증가량). 텍스트: `[효과명] 적용 대상 N ▲` · `최대 [효과명] 대상 수 N ▲`. **적이 보스 하나면 항상 no-op** — 대상이 이미 1기로 수렴해 있다(`GAMEPLAY.md §condition`). 보스 패턴 `summon`(쫄몹)이 생겼지만 **대상 수를 늘리는 쪽은 구현하지 않았다**. 레이 (가칭) `섬멸 지원 4` (→ 아스카 : WILLE `섬멸 태세 추가 효과`), 스노우 화이트 : 헤비암즈 `세븐스 드워프 풀 액티브 5` (→ `록 온`) |
| `effect_range_pct` | — | — | ❌ | 특정 효과의 **공격 범위** % 증가 (`target_effect` 필수). 텍스트: `[효과명] 공격 범위 N% ▲`. 거리 모델이 없어 **항상 no-op**. 레이 (가칭) `섬멸 지원 5` |

### damage stat

`_STAT_TO_BUFF` 매핑 없음. 타임라인 `_handle_damage_eff()`에서 직접 처리.

| stat | hit_type 플래그 | 구현 상태 | 비고 |
|---|---|---|---|
| `damage` | `is_normal_atk=True` (일반공격) / `False` (스킬) | ✅ | |
| `auto_damage` | `is_normal_atk=True`, `damage_formula: "normal_attack"` | ✅ | |
| `burst_damage` | `is_burst_damage=True` | ✅ | |
| `dot_damage` | `is_dot=True` | ✅ | `tick_interval` 기반. **중첩 없는(`max_stack` 1) DoT는 이름이 곧 인스턴스다**(2026-09-24 — 유저 지시 「틱 복사는 잘못」): 같은 시전자의 같은 이름 DoT를 **다른 효과 항목**이 다시 걸면 새 인스턴스를 만들지 않고 돌고 있는 것을 갱신하며, 이번 부여의 대상은 그 인스턴스에 합친다(`_activate()`). 종전에는 인스턴스 키가 효과 객체라 같은 상태를 두 경로로 거는 효과가 한 적에게 따로 겹쳐 틱이 두 번 들어갔다 — 쿠루미 `해킹`(36명중 · 버스트, 표준 188틱 → 140틱) · 하란 `바이러스 전이`(격추 경로 — 쫄몹이 있을 때만). 재부여는 틱 위상을 새로 잡는다(다음 틱 = 재부여 +interval — 질 Q6). `[N 중첩]` DoT와 같은 분기의 주기 자동공격(`auto_damage` 등)에는 걸지 않는다 |
| `dot_damage` + `target: "same_target:[name]"` | `is_dot=True` | ✅ | **짝 공격이 한 발씩 중첩을 얹고, 얹는 즉시 그 중첩 수로 1틱을 때리는 DoT**. 램프 구간의 총 배율이 삼각수가 된다(N=10이면 1+2+…+10 = **55배**). `_same_target_ramp_hits()`가 짝 효과의 `stat` suffix로 N을 읽고, `_activate()`가 `_ramp_pending`에 **`ramp_interval`초 간격으로 예약**하며, `tick()`이 주기 틱 블록보다 **먼저** 소화한다. 주기 틱은 마지막 중첩 부여 +`tick_interval`부터 잇고 만료도 그 시점 기준으로 다시 잡는다(스택 부여는 지속시간을 갱신한다 — GAMEPLAY §버프 스택). **램프를 한 시점에 몰아 쏘면 안 된다** — 지속 대미지는 맞는 순간의 버프로 계산되므로(§값 산정) 램프 전체가 풀버스트 경계 밖으로 밀려 딜이 과소평가된다. 사쿠라 : 블룸 인 서머 `화양연화 2` |
| `split_damage` | `is_split=True` | ✅ | |
| `accum_split_damage` | `is_split=True` | ✅ | **누적기가 모은 양을 그대로 분배 대미지로 방출한다.** 계수가 원문에 없으므로 `values`·`fixed_value`를 쓰지 않고 `target_effect`로 누적기 이름만 가리킨다 — 델타 : 닌자 시프 `인법 IFAK 방출`(`heal_overcharge_discharge`)과 같은 규약이다. **DealForm을 타지 않는다**(유저 결정 2026-09-22) — 누적이 이미 방어력 적용 후 값이라 재적용하면 이중 경감이다. ⑥층 `split_dmg_pct`만 얹는다(실측 정확히 ×1.5184 — 트로니가 `효율 증가`로 자기 폭발을 키우는 경로, 본인 딜 +14.24%). `_handle_damage_eff()` 선두 분기 → `bm.accum_discharge()`. 트로니 `누적 폭발 스킬 3`(상한 도달) · 도로시 `낙인 2`(만료) |
| `dealt_fixed_damage` | — | ✅ | **「자신이 가한 피해량의 N% 만큼 고정 대미지」**(2026-09-24 구현) — 트리거한 탄이 준 대미지 × N%를 **DealForm을 다시 타지 않고** 그대로 넣는다(`accum_split_damage`가 누적값을 재경감하지 않는 것과 같은 이유 — 이미 방어력·버프·크리·코어가 적용된 값이다). `CharState._charge_fire()`가 `full_charge_hit`에 그 탄의 히트 합(÷ 총구)을 `dealt`로 싣고, `_handle_damage_eff()` 선두 분기가 `bm.notify_ctx("dealt")`로 읽는다 — 지금 `dealt`를 싣는 트리거는 `full_charge_hit`뿐이라 다른 트리거에 붙이면 0이다. 크리를 따로 굴리지 않고 자신은 `hit_count:[이름]`·버스트 게이지를 내지 않는다(⬜ 인게임 미확인). 착지(`_land`)는 다른 스킬 히트와 같아 흡혈·누적기에는 들어간다. `values`는 퍼센트다. 에밀리아 `대정령의 철퇴`(「본체에」 → `target_body`) |
| `bonus_damage` | — | ✅ | `timing: "burst_cast"` 시 **3버스트 캐릭터만** `_pending_burst_dmg`에 보류하고 `full_burst_start`에서 계산한다(유저 확인) — 풀버스트는 B3 발동 직후 시작하므로 B3의 추가 대미지만 풀버스트 버프를 받는다. B1/B2는 풀버스트보다 몇 초 앞서 발동하므로 `burst_cast` 시점 버프로 즉시 계산(헬름 : 아쿠아마린 `이지스 캐논 오버로드 2`). 보류된 B3 딜은 계산이 뒤로 밀려 원문 블록 순서가 깨지므로, `_later_burst_cast_buffs()`가 "이 딜보다 **뒤에** 서술된 같은 `burst_cast` buff" 이름을 모아 `get_buffs(exclude_names=...)`로 제외한다 (GAMEPLAY.md §효과 실행 순서. 로산나 `벤데타` ← `벤데타 2` 받는 대미지) |
| `armor_break_damage` | `is_armor_break_damage=True` | ✅ | ②에서 적 방어력 0 처리 |
| `armor_break_burst_damage` | `is_armor_break_damage=True` + `is_burst_damage=True` (+ `is_aoe_burst`) | ✅ | 「방어력 무시 **버스트 스킬** 대미지」 복합. 두 플래그를 함께 켜야 ②(적 방어력 0)·⑤(`armor_break_dmg_pct`)·⑧(`burst_dmg`, `all_enemies`면 `burst_dmg_aoe_pct`)이 모두 실린다. `_handle_damage_eff()`의 `base_stat` 비교 세 곳(`is_burst_damage`·`is_aoe_burst`·`is_armor_break_damage`)에 이 stat을 함께 넣는 것이 전부다. 베스티 : 택티컬 업 `미사일 컨테이너 온라인 3` |
| `first_damage_coeff` (weapon_change 필드) | — | ✅ | stat이 아니라 **`type: "weapon_change"` 항목의 필드**. 원문 `최초 대미지` / `일반 대미지` 2단 계수에서 **모드 진입 첫 발**에만 쓰는 계수(`damage_coeff`는 일반 대미지 쪽). `_tick_weapon_change()`가 레벨 환산해 `_wc_first_coeff`/`_wc_normal_coeff`에 싣고, 발사 직전 `_apply_wc_first_coeff()`가 `_wc_shots == 0`일 때만 첫 계수로 `self.weapon`을 갈아끼운다. **첫 발이 아닐 때 일반 계수로 되돌리는 게 필수** — 연사 24/s + dt 0.05s면 한 tick에 두 발이 나가므로, 되돌리지 않으면 같은 tick의 둘째 발까지 최초 대미지로 나간다. 필드가 없으면 `_wc_first_coeff`가 None이라 기존 동작 그대로. 보유: 라플라스 `라플라스 버스터`(1455.72 vs 22.2 @lv10) |
| `entry_reload` (weapon_change 필드) | — | ✅ | stat이 아니라 **`type: "weapon_change"` 항목의 필드**. `_tick_weapon_change()` 진입부가 `_wc_new_session`일 때 `_wc_entry_reload_until = t + _reload_duration()`을 잡고 그때까지 빈 리스트를 반환한다 — 재장전이 끝나는 프레임에 `_charge_phase`·`_charge_start_t`를 t로 초기화해 **차지가 재장전 뒤에 시작**하게 만든다(초기화를 빼면 재장전이 공짜가 된다). 필드가 없으면 `_wc_entry_reload_until`이 −1로 남아 기존 동작 그대로. 보유: 드레이크 : 그레이트 빌런 `오버 오버 드라이브` |
| `fire_rate` · `muzzles` (weapon_change 필드) | — | ✅ | stat이 아니라 **`type: "weapon_change"` 항목의 필드**. `_tick_weapon_change()`가 `_pick("fire_rate", wc_over, wc_eff, wc_mech, …)` · `_pick("muzzles", wc_over, wc_eff, default=1)`로 읽어 `CharState.fire_rate`·`muzzles`를 모드 동안 갈아끼운다. **`fire_rate`는 초당 발사 수**이고, 원문이 `공격 속도 : N% ▼`처럼 비율로 적어도 환산한 절대값을 넣는다 — buff `attack_speed_pct`로 적으면 다른 공속 버프와 가산이 되어 무기 속성의 곱연산과 어긋난다(`_current_fire_rate()`는 이 값을 base로 삼아 `attack_speed_pct`를 다시 곱한다). **`muzzles`의 기본값은 1이라 원래 무기의 총구를 물려받지 않는다** — 발당 히트 수는 `pellets × muzzles`이므로 총구 2인 캐릭터가 모드에 들어가면 히트가 절반이 된다. 둘 다 생략하면 무기군 기본값. 보유: K `정의로운 수단`(SMG 24/s의 90% ▼ = `fire_rate: 2.4`, `pellets: 10`) |
| `max_ammo_scaling_ref` (weapon_change 필드) | — | ✅ | stat이 아니라 **`type: "weapon_change"` 항목의 필드**. 원문 `최대 장탄 수 : N발 X [게이지명/스택명] 개수`처럼 **표기 장탄 자체가 카운터에 비례**할 때, `max_ammo`(=N)에 `ref_count(caster, 이 이름)`을 곱한 값이 모드의 실효 장탄이 된다. 최대 장탄 **버프**(`max_ammo_pct`·오버로드·큐브)와는 다른 층이다 — 그쪽은 `(사용 무기 변경 시 최대 장탄 수 효과 갱신)` 괄호구가 있는 모드만 받고(`max_ammo_buff_applies`), 이 필드는 괄호구와 **무관하게** 표기값을 정한다. 구현은 `timeline._full_ammo()`이고 `max_ammo_buff_applies` 분기보다 **앞**에 선다. 값을 읽는 시점은 다른 장탄과 같아야 하므로 `_wc_ammo_full` 캐시를 그대로 쓴다 — **모드 진입과 재장전 완료뿐**(GAMEPLAY.md §무기 메카닉). `ref_count`가 `None`(그런 이름 없음)이면 배수 1, `0`이면 **0발이 맞다** — `duration_bullets == max_ammo`(모든 탄환 발사 시 제거) 판정이 곱한 뒤 값을 쓰므로 모드가 첫 tick에 스스로 끝난다. 필드가 없으면 종전 동작 그대로. 보유: E.H. `인 투 더 헤븐`(1발 × 사제 탄창 1~4개) |
| `pierce_damage` | `is_pierce_damage=True` | ✅ | |
| `projectile_explosion_damage` | `is_projectile_explosion=True` | ✅ | RL 기본 공격에 자동 적용 |
| `projectile_attachment_damage` | `is_projectile_attachment=True` | ✅ | |
| `sequential_damage` | `is_sequential=True` | ✅ | `:N` suffix → hit_count |
| `<damage_stat>:[이름]` | 각 stat과 동일 | ✅ | `:N` suffix의 동적판 — 1트리거당 발사 횟수가 상수 N이 아니라 `ref_count(caster, 이름)`(게이지·버프 스택·소환체 수)다. 원래 `sequential_damage:이름`만 처리하던 분기를 모든 damage stat으로 일반화했다. 히트를 합치지 않고 수만큼 개별 발사해야 크리 판정·히트 수 집계가 맞는다. 아인 `armor_break_damage:니어 페더`, 메이든 : 아이스 로즈 `sequential_damage:MP` |
| `core_damage` | `is_core` + `is_core_damage` | ✅ | 코어 명중 판정 스킬 대미지(**확정 코어**, 확률 판정 없음). timeline이 `is_core=True`·`is_core_damage=True`를 세팅하고 `_factor3`이 `is_core and (is_normal_atk or is_core_damage)`로 코어 배율을 태운다 — 무기 `core_dmg_mult`(200%)와 `core_dmg_pct` 버프가 모두 실린다. 코어 유무 게이팅은 `core_hit` condition이 담당. 신데렐라 : 크리스탈 웨이브 `모드 스왑 3` |

### scaling (effect의 `scaling` 필드)

stat과 직교하는 **값 산정 기준**이다. `stat` 테이블에 없으므로 여기서 별도로 센다 —
파싱 규격(`docs/PARSING.md §scaling`)만 있고 계산기에 연결되지 않아도
**doclint가 잡지 못하는 자리**라 로스터를 둔다.

| scaling | 붙는 곳 | 구현 상태 | 처리 지점 |
|---|---|---|---|
| `stack_count` (+ `scaling_ref`) | buff · damage · instant | ✅ | `_get_value()`가 계수에 스택을 곱하고(`buff_manager.py`), damage stat이면 `_handle_damage_eff()`가 히트 수로도 읽는다 |
| `lost_hp_pct` | buff · instant | ✅ | `_get_value()` — 잃은 체력 % 비례 |
| `max_ammo_count` | buff | ✅ | 원문 「**최종 최대 장탄 수 1발 당** [stat] N% ▲」(2026-09-24 구현) — 실효값 = `values` × **수령자의 실효 최대 장탄**(`CharState._full_ammo()` — 오버로드·큐브·소장품·스킬 버프·반올림 반영). **조회 시점에 읽는다** — `get_buffs()` 라이브 루프가 `bm._scale_by_max_ammo()`로 곱하고, 최대 장탄은 타임라인이 들고 있어 `bm.max_ammo_provider` 콜백으로 묻는다. 그 콜백이 다시 `get_buffs()`를 부르므로 안쪽 조회에서는 이 버프를 건너뛴다(최대 장탄은 이 부류를 읽지 않는다). 에밀리아 `미정령의 축복 2`(`charge_dmg_pct`, 1발 유지) |
| `max_hp` | instant(`heal_hp_pct`·`cover_heal_pct`) · buff(`cover_hp_pct`) 전용 | ✅ | 원문 「**시전자의** 최종 최대 체력 비례」. `heal_hp_pct`: 힐 기준을 기본 체력 대신 **시전자** 최종 최대 체력으로 — 아군 전체 힐도 모두 같은 양이고, 받는 사람의 최대 체력은 상한일 뿐이다(`handle_heal_hp_pct`, 2026-09-15 수정 — 종전엔 받는 사람의 최대 체력이었다). `cover_heal_pct`: 엄폐물 회복 기준을 엄폐물 최대 체력 대신 **시전자** 최종 최대 체력으로 (`handle_cover_heal_pct`). `cover_hp_pct`: 엄폐물 기본값 비례 대신 **시전자** 최종 최대 체력 × N%를 더한다(`bm.cover_max_hp`, 큐브는 `cube.json`의 `scaling`). 대미지 경로와 접점이 없다 |
| `max_hp_additive` (+ `scaling_hp_pct`) | damage | ✅ | `_handle_damage_eff()`가 `bm.effective_max_hp(시전자) × pct/100`을 **buffs 사본의 `atk_flat`**에 더한다 — `atk_from_hp_pct`와 같은 자리(공격력 증가% **뒤**)다. **원문 「공격력으로 합산」과 「공격력으로 환산」이 같은 키를 쓴다**(유저 결정 2026-09-21) — 로스터에 합산은 메이든 : 아이스 로즈 `다이아몬드 더스트`, 환산은 킬로 `우선 순위 지정` 하나씩뿐이고 **둘을 함께 가진 캐릭터가 없어 차이를 확인할 자료가 없다**(⬜ 미확인). 메이든에서는 이 항이 공격력의 4배라 빠지면 버스트가 1/8이 되고, 킬로에서는 버스트가 1/3.3이 된다 |

### duration_scaling (effect의 `duration_scaling` 필드)

위 `scaling`이 **값**의 산정 기준이라면 이쪽은 **지속시간**의 산정 기준이다. 둘은 직교하고
한 효과에 같이 붙을 수 있다. `stat` 테이블에 없으므로 여기서 별도로 센다.

| duration_scaling | 붙는 곳 | 구현 상태 | 처리 지점 |
|---|---|---|---|
| `stack_count` (+ `duration_scaling_ref`) | buff | ✅ | 원문 `[N초 X [상태명] 횟수만큼 유지]`(2026-09-22 구현). 실효 지속 = `duration` × **부여 시점**의 참조 버프 중첩(`_expires_at()`이 `ref_count()`로 읽는다). 참조가 없거나 0중첩이면 0초라 사실상 무발동인데 그건 원문 그대로다 — 그래서 **이 문형의 컨테이너 담체는 배열 앞**에 둬야 한다(형제가 담체의 중첩을 읽으므로). `scaling: stack_count`가 `_get_value()`에서 값을 곱하는 것과 같은 자리를 지속시간에서 한다. 실측: 레이블 `망상 파괴 2`(기절) 지속이 파괴 1회차 1.02s → 2회차 2.02s → 3회차 2.00s(`max_stack: 2` 상한). 레이블 `상상 실연`·`망상 파괴 2` |

### instant stat

`_STAT_TO_BUFF` 매핑 없음. `_dispatch_instant()` 또는 타임라인 핸들러로 처리.

| stat | 처리 위치 | 구현 상태 | 비고 |
|---|---|---|---|
| `burst_cooldown_reduce` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `skill_cooldown_reduce_pct` | `_dispatch_instant()` 내장 분기 | ✅ | **스킬 재사용 시간 N% ▼ (즉시 1회)** — 대상 캐릭터가 시전자인 `every:Ns` 효과의 **남은 시간**(`_next_fire[eid]`의 `next_t - t`)에 `(1 − N/100)`을 곱한다. `interval` 자체는 건드리지 않는다(다음 주기는 원래 길이로 복귀). `skill_cooldown_pct`(주기에 곱하는 buff)와 혼동 주의 — 이쪽은 잔여분만 깎는 instant다. `burst_cooldown_reduce`(초 단위 instant)의 % 스킬판. `target_effect` 미지원 — 시전자의 모든 `every:Ns`에 일괄 적용(`skill_cooldown_pct`와 같은 범위). 센티 `보수공사` |
| `ammo_charge_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | **최대 장탄의 비율**을 현재 장탄에 더한다(`max(0, min(ammo + max×val/100, max))`). 음수(`탄환 100% 제거`)면 반쯤 남은 탄창에서 0 아래로 갈 수 있어 하한이 필요하다 — 2026-08-28 이전에는 하한이 없어 음수 장탄이 나왔다(그레이브 `방열 2`가 처음 발동하면서 드러남). 음수 보유자: 그레이브 · 라플라스 : 얼티밋 히어로 · 밀크 : 블루밍 바니 · 질 · 벨벳(`탄환 가져오기 2` — `target: "all_enemies"`라 적 탄약 모델이 없어 무발동) |
| `ammo_charge_flat` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `burst_charge_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | 「버스트 게이지 충전 N%」를 그대로 가산. **`target: all_allies`여도 1회만** — 게이지가 스쿼드 공용 1개다 |
| `heal_hp_pct` | `_dispatch_instant()` → timeline 핸들러 | ✅ | `state["hp"]` 갱신 후 `hp_pct` 재동기화 |
| `buff_stack_add` | `_dispatch_instant()` | ✅ | 스택 +N과 함께 **대상 버프의 지속시간도 갱신**한다(유저 확정: 일반 동작 — 원문 `[스택명 : ...] [N 중첩] [M초 유지]`는 버프를 다시 붙이는 문장이다). `duration: -1`(영구, `expires_at == inf`)은 갱신 대상 아님. 스택이 증가하면 `stack_reach:버프명:N`도 notify한다(`_activate()`와 동일). notify는 `_active` 순회가 끝난 뒤 emit — 순회 중 emit하면 재진입으로 리스트가 바뀐다 |
| `buff_stack_remove` | `_dispatch_instant()` | ✅ | |
| `buff_stack_init` | `_dispatch_instant()` | ✅ | `target_effect` 버프가 없을 때만 N 스택으로 초기 생성. `_effects`에서 버프 정의 조회 후 `ActiveBuff` 직접 생성 |
| `debuff_stack_add` | `_dispatch_instant()` | ✅ | |
| `debuff_stack_remove` | `_dispatch_instant()` | ✅ | |
| `remove_named_buff` | `_dispatch_instant()` | ✅ | `target_effect` 필수. 기본은 이름이 같은 버프를 **대상·시전자와 무관하게 전부** 지운다. **`remove_scope: "target"`이면 `target`으로 풀린 캐릭터에게서만** 지운다(2026-09-18 구현) — 여럿에게 걸린 인스턴스는 그 캐릭터만 `target_chars`에서 빠지고, 남은 대상이 0명일 때 인스턴스가 사라지며 그때만 `event:state_end:`가 나간다(`debuff_cleanse`와 같은 모양). 같은 이름의 상태를 캐릭터마다 따로 드는 경우에 필요하다 — 전역 제거로는 짝의 모드까지 지워 동기화가 끊긴다(길티 : 마이티 바니 · 신 : 스위프트 바니 `바니 모드` 해제 8항목). **다른 캐릭터와 효과 이름만 겹칠 때도 쓴다** — 퀸(마코토) `철・권・제・재! 해제 2`가 전역 제거면 같은 스쿼드 아이기스의 영구 `라쿠카쟈`까지 지웠다(유저 결정 2026-09-24) |
| `debuff_cleanse` | `_dispatch_instant()` | ✅ | 대상의 `polarity: harmful` 버프를 제거한다(`harmful_irremovable`은 못 지운다). **개수는 대상 니케 1인당이다** — 원문 `[해로운 효과 해제 N개]`의 N을 `fixed_value`(레벨별이면 `values`)에 싣고 그만큼만 지운다(2026-09-15 유저 확정. 종전에는 개수를 무시하고 전부 지웠고, 보유자가 0명이라 드러나지 않았다). 보스 디버프가 니케마다 따로 붙으므로 「1개」를 스쿼드 전체 1개로 읽으면 5인 스쿼드에서 한 명만 풀린다. 제거 우선순위는 원문에 없어 **부여가 이른 것부터**(`_active` 배열 순서)로 정했다 — `docs/scenarios/미카 _ 스노우 버디.md §해석 선언`. **한 버프가 여러 니케에게 걸려 있으면 해제 대상만 `target_chars`에서 빼고**, 남은 대상이 없을 때 버프가 사라진다(통째로 지우면 대상이 아닌 아군의 디버프까지 풀린다). N을 안 적은 항목은 종전대로 전부 지운다. 보스 지속 피해는 `timeline._boss_dots`가 매 프레임 활성 버프를 다시 읽어 남은 틱을 버린다. 미카 : 스노우 버디 `설온제` · 코코아 `프로 종이접기 2`·`프로 메이드장` · 크러스트 `든든한 요리 2`·`든든한 요리 3` · 소라 `품 안의 비밀 2` · 클레어 `R+G+B 2` |
| `enemy_buff_cleanse` | timeline 핸들러 | ✅ | 적 이로운 효과 해제 N개(`values` = 레벨별 개수). 보스 패턴의 열린 `buff` 패턴 하나가 이로운 효과 하나다 — `BossScript.dispel`이 나중에 두른 것부터 N개를 끄고(`irremovable` 제외), 꺼진 패턴은 방어력 오버레이와 받는 대미지를 둘 다 잃는다. 다음 프레임 맨 앞에 반영(⬜ 순서·범위 `DATA_VERIFY.md` §보스 → 니케 피해). 핸들러는 보스 패턴이 있을 때만 등록된다 — 없으면 적에게 이로운 효과가 없어 종전과 같은 무발동. 로산나 `온 더 렘 2` |
| `force_reload` | timeline 핸들러 | ✅ | 시전자 `CharState.ammo = 0` 후 `_start_reload()` 강제 호출. 이미 재장전 중이면 스킵 |
| `targeting_exclude` | — | ❌ | 공격 대상 타겟팅 제외. 미구현(같은 뜻은 `stealth`가 맡는다). **2026-09-05 현재 사용처 없음** — 유일한 보유자였던 델타 : 닌자 시프 `인법 카모플라쥬 2`가 원문에 `[10초 유지]`가 붙어 있어 로산나 `은신`과 같은 `stealth` buff로 옮겨 갔다(instant는 지속시간을 담지 못한다). 키는 남긴다 |
| `heal_overcharge_discharge` | — | ❌ | 저장된 회복량 방출. `target_effect` 필수. 힐 모델 없음 |
| `current_hp_reduce` | `_dispatch_instant()` → timeline 핸들러 | ✅ | |
| `cover_heal_pct` | 타임라인 `handle_cover_heal_pct` | ✅ | 엄폐물 최대 체력(`config["cover_hp"]`, **임의값**)의 N% 회복. **`scaling: "max_hp"`면 기준이 시전자의 최종 최대 체력**이다 — 원문 「시전자의 최종 최대 체력 비례 엄폐물 체력 회복」(슈가 `블랙 타이푼 3`). 기준 표기 없는 「엄폐물 체력 회복 N%」(나가·츠바이·리타)는 엄폐물 기준. **부서진 엄폐물은 되살아나지 않는다**(유저 확인 — 재생성 없음). 엄폐물 체력은 보스 공격 패턴이 있을 때만 깎인다 |
| `decoy_heal_pct` | 타임라인 `handle_decoy_heal_pct` | ✅ | **디코이 체력 회복** N%(2026-09-21 구현). `cover_heal_pct`(엄폐물)·`shield_heal_pct`(보호막)와 같은 층이고 회복 대상만 분신이다 — `"scaling": "max_hp"`면 기준이 시전자의 최종 최대 체력이고, 기준 표기가 없는 판본은 아직 쓰이지 않는다. `bm.heal_decoy()`가 **깎인 만큼만** 되돌린다(상한은 부여 시점 스냅샷 `decoy_max_per_target`, 나중에 생긴 분신부터). **없는 분신을 새로 만들지 않는다** — 부서진 분신은 담체가 이미 끝나 후보에서 빠진다(`cover_heal_pct`가 부서진 엄폐물을 되살리지 않는 것과 같은 자리). 분신은 보스 공격 패턴이 있을 때만 깎이므로 기본 경로에서는 늘 만피라 **회복량 0**이다. 주기판(`tick_interval`)도 같은 핸들러가 받는다. 라이 `선배의 응원 2`(`on_attack_count:60`) · `선배의 모범 2`(`burst_cast`, 1초 간격 10초) |
| `shield_heal_pct` | 타임라인 `handle_shield_heal_pct` | ✅ | 보호막 **체력 회복** N%. `cover_heal_pct`의 보호막판이고 `scaling: "max_hp"`면 기준이 시전자의 최종 최대 체력이다 — 로스터의 세 보유자가 전부 「시전자의 최종 최대 체력 비례」라 기준 없는 판본은 아직 쓰이지 않는다. 생성 계열(`shield_from_max_hp_pct`)·증폭 계열(`next_shield_hp_pct`·`shield_restore_pct`)과 모두 다른 축이다 — **이미 있는 보호막이 깎인 만큼 되돌린다.** 보호막은 보스 공격 패턴이 있을 때만 깎이므로 기본 경로에서는 늘 만피라 딜 기여 0이다(`cover_heal_pct`와 같은 자리). 회복은 `bm.heal_shield()`가 `absorb_shield`와 같은 순서(나중에 생긴 것부터)로 **깎인 만큼만** 되돌린다 — 상한은 부여 시점 스냅샷 `ActiveBuff.shield_max_per_target`이고, 없는 보호막을 새로 만들지는 않는다(`cover_heal_pct`가 부서진 엄폐물을 되살리지 않는 것과 같은 자리). 회복 이벤트는 쏘지 않는다 — 「보호막 체력 회복 시」 트리거를 쓰는 효과가 로스터에 없다. 킬로 `자가 수복`, 라푼젤 : 퓨어 그레이스 `프레이 3`, 모리 `약자의 생존법` |
| `burst_reentry` | 타임라인 `handle_burst_reentry` | ✅ | `[버스트 재진입 N단계]` — 이번 버스트 1회의 재진입 사건. **`fixed_value`가 재진입 단계 N**이다(유저 결정 2026-09-14). 핸들러가 `state["pending_reentry"][시전자]`에 적어 두고, `_cast_burst` 직후 `BurstController._check_reenter()`가 꺼내 지운다 — 상태 buff `burst_stage_override:reenterN`(아니스 : 스타 `모두의 별`, `[… 재진입 N단계로 변경] [지속]`)과 같은 자리로 합류해 0.5s 대기·단계당 사이클 1회 규칙을 그대로 따른다. ⬜ 재진입은 지금 **사용한 그 단계**를 다시 연다 — N이 시전자 단계와 다른 사례(차임 `[버스트 재진입 2단계]`, 미파싱)가 들어오면 확인할 것. 티아 `도마뱀의 보호 3` · 앨리스 : 원더랜드 바니 `소중한 큰 당근` |
| `revive` | 타임라인 `handle_revive` | ✅ | `[체력 N%로 부활]` — values가 부활 직후 체력 %(대상 최대 체력 기준). 값이 없으면 즉시 실패한다. 부활은 만탄으로 바로 싸우고 버스트 쿨은 이어간다. 마나 `매터 감마 3` |
| `gauge_charge` | `_dispatch_instant()` | ✅ | `gauge_id` 필수 |
| `gauge_consume` | `_dispatch_instant()` | ✅ | `gauge_id` 필수 |
| `gauge_consume_as_ammo` | `_dispatch_instant()` | ✅ | `gauge_id` 필수. 소모량만큼 `squad_ammo_consume` notify 발생 |
| `squad_ammo_consume_as` | `_dispatch_instant()` | ✅ | "탄환 소모 N발" 표기 — 실제 장탄은 1발만 줄고 아군 탄 소비 총합 집계에서만 `fixed_value`발로 계상. **발사 자체가 이미 1발을 계상했으므로 핸들러는 `N-1`발만 추가 notify**한다(총 N발). `gauge_consume_as_ammo`(벨벳)와 달리 게이지 소모를 동반하지 않는다. 소비자인 `squad_ammo_consume:N`은 ✅ 구현이라 **스쿼드 DPS에 직결**(리틀 머메이드 `거품 난사` 등). 신데렐라 : 크리스탈 웨이브 `저격 모드 탄 소비 집계` |
| `named_buff_duration_extend` | `_dispatch_instant()` | ✅ | `target_effect` 필수. 해당 이름 및 `"이름 N"` 형태 부속 버프의 `expires_at += fixed_value`. 스쿼드 브로드캐스트 방식으로 발동. 적 대상 효과에도 걸린다 — `enemies_*`는 lazy가 아니라 즉시 `["__enemy__"]`로 풀리므로 연장 항목과 피연장 버프가 같은 센티널로 만난다. **DoT는 `_dot_timers`의 `expires_at`도 함께 늘린다** — 틱 스케줄이 `ActiveBuff`와 별도로 복사돼 있어 한쪽만 늘리면 표시만 길어지고 실제 틱은 원래 시각에서 끊긴다. 사쿠라 : 블룸 인 서머 `피어나다 3`(적측 `벚꽃잎` 연장). |
| `force_skill_use` | `_dispatch_instant()` | ✅ | `[스킬 N 강제 사용]`. `target_skill`이 가리키는 슬롯의 **활성 판본**(애장품 단계 반영) 효과 전체를 즉시 1회 발동한다. 특정 효과 하나가 아니라 슬롯 단위라 `target_effect`가 아닌 `target_skill`을 쓴다. 율리아 `크레센도 2`(애장품1 → 스킬1), 사쿠라 : 블룸 인 서머 `피어나다`(→ 스킬2, 현재는 스킬2 timing에 `battle_start`를 얹은 우회 표현 — 구현과 함께 전환) |
| `feather_refresh` | `_dispatch_instant()` | ✅ | **아인 전용**. 니어 페더 소환체를 슬롯 단위로 (재)소환한다. `feather_id`로 식별하고 `feather_slots`(슬롯별 지속시간 배열, `-1`=무제한) 길이만큼 소환하며, 이미 있던 슬롯도 **지속시간·공격 쿨을 초기화**한다(전투 시작 4기 / 버스트 6기 모두 같은 stat). 상태는 `state["feathers"][caster][feather_id]`. 주기 계산용 `feather_interval_base`·`feather_interval_mult`를 함께 싣는다. 소비자는 `feather_tick` timing과 `armor_break_damage:니어 페더`(`ref_count()`가 게이지와 같은 자리에서 생존 수를 돌려준다). 수치가 스킬 텍스트에 없는 추정치라 코드 상수가 아니라 JSON 필드로 둔다 — 정본: `docs/scenarios/아인.md §니어 페더 메커니즘` |

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
| `burst_enter_count:N:M` | ✅ | 「버스트 N단계 돌입 시 `[시작 횟수 별 효과]`」(2026-09-24 구현) — 스쿼드가 N단계에 **M번째 이상** 들어설 때마다(`>= M`, `burst_cast_count:M`과 같은 규약). `_timing_to_index_key`가 `burst_enter:N`으로 접고 `_timing_match`가 수신자별 `burst_enter:N` 누적(`_event_counts`)과 비교한다. 돌입은 스쿼드 판정이라 본인이 버스트를 안 쓴 사이클도 센다. 첫 보유자 네온 : 블루 오션 `워터 제트`(표준 스쿼드 1·2·3번째 항목 15·14·13회 — 3단계 돌입 15회) |
| `burst_cast` | ✅ | `bm.notify("burst_cast", ...)` |
| `burst_cast_count:N` | ✅ | `burst_cast` 이벤트가 **N번째에 도달한 뒤 매번**(`count >= N`, `buff_manager.py`). "N번째 한 번"이 아니다 — 이사벨 `타겟 마킹 1·2·3`이 버스트 8회에 **8/7/6회**로 계단이 되는 근거다 |
| `squad_burst_cast:N` | ✅ | `bm.notify("squad_burst_cast:N", ...)` |
| `hit_count:N` | ✅ | **명중** 트리거(`일반 공격 N회 명중 시`). `bm.notify("hit_count", ...)`가 `_fire()`·`_tick_charge()`에서 **총구 수만큼** 발생한다 — 탄 1발 소모에 공격 1회·명중 총구 수회다(유저 확인, 2026-09-04). 펠릿은 곱하지 않는다(`pellet_hit`이 따로 센다). `trigger_count_reduce` 버프로 N 감소 가능. `{0}` 자리표시자는 `_resolve_count_placeholder()`가 `trigger_values`에서 푼다 |
| `on_attack_count:N` | ✅ | **발사** 트리거(`일반 공격 N회 공격 시`·`N발 당`). `_timing_to_index_key()`가 `on_attack`으로 접어 같은 이벤트를 센다 — 총구·펠릿과 무관하게 발사 1회당 1이다. `trigger_count_reduce`·`{0}` 자리표시자는 `hit_count:N`과 같은 규약. **짝인 `hit_count:N`과 한 스킬에 나란히 오는 경우가 있다**(레이 `선두 제압`) |
| `hit_count:[스킬명]:N` | ✅ | named damage effect 명중 N회마다 발동. `_timing_match()`에 분기 추가. 타임라인 `_handle_damage_eff()` hit 루프 안에서 `bm.notify("hit_count:{eff_name}", t, caster)` 호출 |
| `crit_hit_count:N` | ✅ | `bm.notify("crit_hit", ...)`. `trigger_count_reduce` 버프로 N 감소 가능 |
| `full_charge` | ✅ | `bm.notify("full_charge", ...)` |
| `full_charge_hit` | ✅ | **명중**(`풀 차지 공격 명중 시`). `_tick_charge()`가 **총구 수만큼** notify — `hit_count`와 같은 규약이다. 짝인 발사는 `full_charge_fire` |
| `full_charge_fire` | ✅ | **발사**(`풀 차지 공격 시`). `_tick_charge()`가 풀차지 발사 1회당 1회 notify. 빗나가도 발동한다 |
| `full_charge_fire_count:N` | ✅ | `full_charge_fire` 이벤트 N회마다. `trigger_count_reduce` 버프로 N 감소 가능. **구 표기 `full_charge_count:N`은 이 키의 별칭으로 남겨 뒀다** — 데이터는 전부 옮겼지만 옛 표기가 들어와도 조용히 영구 무발동이 되지 않게 한다 |
| `full_charge_hit_count:N` | ✅ | `full_charge_hit` 이벤트 N회마다(`풀 차지 공격 N회 명중 시`). 브래디 `페이버릿 캔디` |
| `non_full_charge_fire_count:N` | ✅ | **논차지 발사** N회마다(`풀 차지 공격이 아닌 일반 공격 N회 공격 시`). `full_charge_fire`의 여집합 — `_charge_fire()`의 `is_full` 분기 `else`에서 `bm.notify("non_full_charge_fire", ...)`. `_timing_to_index_key()`가 `non_full_charge_fire`로 접고 `trigger_count_reduce`도 `full_charge_fire_count:N`과 같은 규약으로 받는다. **톡톡이(`click` 모드 `tap`) 없이는 차지 무기의 모든 발사가 풀차지라 영구 무발동**이고, 톡톡이 자체가 불가한 풀차지 전용 9명에게는 구조적으로 성립하지 않는다(`docs/CONTROL.md` §톡톡이). 크러스트 `마이야르`·`든든한 요리` |
| `charge_hold_count:N:M` | ✅ | `charge_hold:N` 판정이 **M회 누적될 때마다**(`풀 차지 상태 N초 이상 유지를 M회 실행 시`). 임계값 N은 `charge_hold:N`과 같은 규약으로 `BuffManager.charge_hold_thresholds(caster)`가 뽑고(이 키도 함께 훑는다), 카운터는 `_timing_to_index_key()`가 `charge_hold:N`으로 접어 센다. 한 차지에 1회만 판정하므로(`_charge_hold_fired`) M회를 채우려면 홀드-발사를 M번 반복해야 한다. **카운터는 사이클을 넘어 누적된다** — 사이클당 1회인 정책(`own_full_burst`)으로도 M사이클이면 닿고 이후 M회마다 재발동한다(크러스트 실측: 첫 발동 t=30.52s, 180초 16회). 홀드 조작이 없으면 영구 무발동(`charge_hold:N`과 같다). 크러스트 `블렌칭`·`든든한 요리 3` |
| `core_hit_count:1` | ✅ | `bm.notify("core_hit", ...)` (횟수 없는 형태, `timing == event`로 처리) |
| `core_hit_count:N` | ✅ | `bm.notify("core_hit", ...)`. `trigger_count_reduce` 버프로 N 감소 가능. **`_timing_match()`는 `core_hit:N`·`core_hit_count:N` 두 표기를 모두 받는다** — `_timing_to_index_key()`가 둘 다 `core_hit`로 접으므로 한쪽만 받으면 그 표기가 조용히 영구 미발동이 된다(2026-09-03 실제로 그랬다. 루드밀라 : 윈터 오너 `눈보라`) |
| `pellet_hit_count:N` | ✅ | `bm.notify("pellet_hit", ...)`. `trigger_count_reduce` 버프로 N 감소 가능 |
| `pellet_hit_in_shot:N` | ✅ | **한 발 안의** 펠릿 명중 수 문턱(`일반 공격 1회로 펠릿 N개 이상 명중 시`). 누적 카운터인 `pellet_hit_count:N`과 다른 축이다 — 발사 1회를 단위로 그 발의 명중 펠릿 수가 N 이상인지 본다. 임계값은 이 캐릭터가 실제로 쓰는 값만 본다(`BuffManager.pellet_in_shot_thresholds`, `charge_hold_thresholds`와 같은 모양)이고, 판정과 notify는 `CharState._fire()`의 펠릿 루프 **뒤**에서 그 발의 `hit_count`로 한다. **계산기에 빗나감 모델이 없어(`GAMEPLAY.md` §공격과 명중) 지금은 펠릿이 전부 명중하므로, N ≤ 무기 펠릿 수이면 항상 참 = 발사 1회당 1회다.** 그래도 `on_attack`으로 접지 않는 것은 미스 모델이 들어올 자리를 갈라 두기 위해서다(`on_attack_count:` ↔ `hit_count:`를 가른 것과 같은 이유). 프리바티 : 언카인드 메이드 `사랑 가득 메이드` |
| `last_bullet` | ✅ | **명중**(`마지막 탄환 명중 시`). `hit_count`와 같은 규약으로 **총구 수만큼** 발동한다. ⬜ 다만 카운터 없는 트리거라 총구 2개면 버프가 두 번 붙는데 **인게임 미검증**이다(유저 판단, 2026-09-04 — 일관성만으로 잡았다). **첫 실례는 크로우 `데어데블`(총구 2, 2026-09-24)** — 추가 대미지가 탄창당 2회 들어간다(표준 스쿼드 28회/탄창 14개). 1회였다면 본인 딜 −1.55%. 짝인 발사는 `last_bullet_fire` |
| `last_bullet_fire` | ✅ | `bm.notify("last_bullet_fire", ...)` |
| `enemy_death` | ✅ | 보스 패턴 `summon`의 쫄몹이 **처치**되면 다음 프레임에, **자폭**하면 그 프레임에 쫄몹마다 스쿼드 전원에게 notify한다. 패턴이 닫혀 퇴장한 쫄몹은 사망이 아니다(유저 확인 2026-09-16). 보스 패턴의 `emit`·`emit_on_destroy`로도 적을 수 있다(`calculator/boss_pattern.py` `BOSS_EVENTS`). **패턴이 없으면 무발동** — 보스는 죽지 않는다(2026-09-05 정정: 그때의 ✅ 표기는 호출처가 없어 근거가 없었다). 마르차나 : 마린 스터디 `펭군 긴급 출동 2`·`경계 대상 지정 2`·`경계 대상 2` |
| `received_hit_count:N` | ✅ | 보스 공격 한 발이 그 니케에게 들어갈 때마다 `timeline._boss_attack()`이 `received_hit`을 notify한다 — 보호막·엄폐물에 막혀도, 무적이어도 나간다. 패턴이 없으면 무발동. `_timing_match`는 `received_hit:N`·`received_hit_count:N` 두 표기를 받는다(2026-09-14 전에는 짧은 표기만 받아 정본 표기 10건이 영구 미발동일 뻔했다). `직접 피격 시 해제` 문형의 해제 트리거로 쓴다 — 로산나 `은신 해제 (직접 피격)`, 델타 : 닌자 시프 `인법 카모플라쥬 해제 (직접 피격)` |
| `event:full_reload` | ✅ | `bm.notify("event:full_reload", ...)` |
| `event:cover` | ✅ | `_enter_cover()`에서 `bm.notify("event:cover", ...)`. **엄폐는 컨트롤로만 발생한다** — `control`의 장전컨 정책이나 명시 시퀀스가 엄폐 구간을 열 때만 발동하고, 컨트롤이 꺼진 시뮬에서는 한 번도 발동하지 않는다 (자동 사격이 디폴트라 니케가 스스로 엄폐하지 않기 때문). 정본: `docs/CONTROL.md` |
| `event:ally_down` | ✅ | 보스 공격으로 누가 전투불능이 되면 **나머지 산 아군**에게 `bm.notify_down()`. 쓰러진 쪽의 정리(버프 소멸·조작 해제)가 끝난 뒤에 나간다 — 같은 호출 안에서 부활(마나 `매터 감마 3`)이 나올 수 있어서다 |
| `event:ally_hp_below:N` | ✅ | 누구든 체력이 N%를 위에서 아래로 넘으면 **스쿼드 전원**에게(자신 포함) — `bm._notify_hp_below()`(`sync_hp` 경로). 체력을 줄이는 모든 경로(보스 공격·`current_hp_reduce`·현재 체력 유지 최대 체력 증가)에서 나간다 |
| `event:adjacent_hp_below:N` | ✅ | 자신의 **양 옆 아군** 중 1기가 체력 N% 이하에 도달. `sync_hp()`가 등록된 임계값의 하향 전이를 감지하고, `allies_adjacent:2` 관찰자에게 notify. 플로라 |
| `event:adjacent_hp_max` | ✅ | 자신의 **양 옆 아군** 중 1기가 **최대 체력 도달**. `sync_hp()`가 hp_pct의 `<100 → 100` **전이(edge)** 를 감지해 `_notify_adjacent_hp_max()`로 발생시킨다(상시 만피는 전이가 없어 무발동). notify의 caster는 이웃이 아니라 **관찰자(효과 소유자)**. 시각은 `self._cur_t`(`tick()`·`notify()`에서 갱신), 재진입은 `_in_hp_edge`로 차단. 최대 체력만 증가 버프(`hp_only_caster_based_pct`·`max_hp_only_pct`)의 만료가 주 발생원. 플로라 |
| `event:self_down` | ✅ | 쓰러진 본인에게 `bm.notify_down()`. 전투불능인 니케의 스킬은 발동하지 않는데(`_notify` 게이트) 이 이벤트만 예외다 |
| `event:lethal_hit` | ✅ | 「전투불능에 이르는 공격에 피격 시」(2026-09-23 구현) — 보스 공격 한 발이 **무적·불굴에 막히지 않은 채** 그 니케의 체력을 0으로 만들 때, 그 발을 **받기 전에** 그 니케에게(받는 대미지 균등 분배로 나눠 받은 몫 포함 — ⬜ 인게임 미확인). 보스 지속 피해 틱은 피격이 아니라 제외한다(`_dot_tick`에 호출처 없음). `timeline._land_squad`가 기존 불굴 판정 뒤 `bm.notify("event:lethal_hit")`을 쏘고 **불굴을 한 번 더 본다** — 여기서 켜진 불굴이 같은 발을 체력 1로 받아야 트리거가 의미가 있다. 이미 불굴이면 치명타가 성립하지 않으므로 나가지 않는다. `_timing_match`는 `timing == event` 일반 분기를 탄다. 패턴이 없으면 무발동. 마키마 `발각된 모양이네`·`발각된 모양이네 2` |
| `event:part_destroy` | ⚠️ | 매칭 로직(`event:xxx`) 있음. **기본은 무발동**이고 발생원이 둘이다 — ① `enemy["part_break_interval"]`(초, 0/미지정이면 OFF)을 주면 `timeline.simulate`가 그 주기마다 스쿼드 전원에게 notify한다(있지도 않은 파괴를 반복한다). ② 보스 패턴의 표적이 실제로 깨지면 `emit_on_destroy`로 **1회**, 다음 프레임에 나간다(`calculator/boss_pattern.py`). ①은 간단 모드(보스 패턴 없음)의 칸이라 **패턴과 같이 적으면 거절한다**(유저 결정 2026-09-15 — 둘이 함께 켜져 이중으로 나가지 않는다 · 2026-09-19 적으로 옮김). 하네스는 ①로 baseline이 잡혀 있다. 아크레인저 블랙 `배터리 충전`, 사쿠라 : 블룸 인 서머 스킬1 전체 |
| `event:enemy_spawn` | ✅ | `battle_start()` 시점에 모든 스쿼드원에서 notify(보스 등장). 보스 패턴 `summon`이 쫄몹을 띄우면 **쫄몹마다** 한 번씩 더 나간다(⬜ 한꺼번에 나와도 마릿수만큼인지 인게임 미확인) |
| `event:target_spawn` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 기본은 호출처 없음 — 보스 패턴이 `emit`으로 적을 때만 발생(`calculator/boss_pattern.py` `BOSS_EVENTS`). **원문 「타겟 출현 시」를 이 키로 적지 않는다**(유저 결정 2026-09-20) — 기본 경로에서 영구 무발동이 되므로 `event:enemy_spawn` + `max_trigger: 1`로 적는다(`PARSING.md` §4-1, 일레그 `패스트 차지 2`). 현재 보유자 0명이고, 남겨 두는 것은 보스 스크립트가 `emit`할 자리이기 때문이다 |
| `event:heal_received` | ⚠️ | 매칭 로직(`event:xxx`) 있음. `heal_hp_pct` 핸들러에서만 notify 발생 |
| `event:shield_applied` | ✅ | `shield_from_max_hp_pct` 활성/갱신 시 보호막을 받은 각 대상에게 통지 |
| `event:shield_consumed` | ✅ | 보스 공격이 보호막을 다 깎은 순간 그 대상에게 — `bm.absorb_shield()` |
| `event:cover_hit` | ✅ | 보스 공격이 그 니케의 엄폐물을 깎았을 때 — `timeline._boss_attack()`. 패턴이 없으면 무발동(슈가 `블랙 타이푼`) |
| `event:cover_healed` | ✅ | 「엄폐물 체력 회복 시」 — **그 니케의 엄폐물**이 회복 효과를 받았을 때 그 니케에게. `timeline.handle_cover_heal_pct`가 대상마다 notify한다. **가득 찬 엄폐물에 들어간 회복도 발동한다**(유저 확인 2026-09-14 — `event:heal_received` 오버힐 규칙과 같다). 부서진 엄폐물은 회복되지 않으므로 무발동. 티아 `파충류 애호가`·`파충류 애호가 2` |
| `event:projectile_destroy` | ⚠️ | 매칭 로직(`event:xxx`) 있음. 기본은 호출처 없음 — 보스 패턴이 `emit`으로 적을 때만 발생(`calculator/boss_pattern.py` `BOSS_EVENTS`) |
| `event:ally_burst_cast` | ✅ | `timeline._cast_burst()`가 버스트 발동마다 **스쿼드 전원에게** 브로드캐스트한다 — `event:[버프명]`과 같은 규약으로 반응하는 캐릭터 본인을 caster로 넘겨 조건·대상을 자기 기준으로 평가하게 한다. **시전자 자신도 「아군」에 포함된다**(`all_allies`가 시전자 포함인 것과 같은 읽기, 2026-09-15). 재진입 버스트도 `_cast_burst`를 거치므로 한 사이클에 B1·재진입·B2·B3 네 번 발생한다. 루피 : 윈터 쇼퍼 `쇼핑` |
| `event:stat_applied:dot_dmg_pct` | ✅ | `_activate()` 후처리에서 `dot_dmg_pct` stat 버프 신규/갱신 등록 시 각 target_char에게 `notify("event:stat_applied:dot_dmg_pct", t, tgt)` 발생 |
| `event:stat_applied:split_dmg_pct` | ✅ | 동일. `split_dmg_pct` stat 버프 적용 시 발생 |
| `event:accum_full:[상태명]` | ✅ | 누적기(`dmg_accum_dealt_atk_pct` · `dmg_accum_received_atk_pct`)의 누적량이 **상한에 닿는 순간** `accumulate_damage()`가 발생시킨다(순회를 끝낸 뒤 쏜다 — notify가 방출·해제로 `_active`를 건드린다). 같은 프레임에 방출(`accum_split_damage`) → 해제(`remove_named_buff`) 순으로 처리한다 — 원문 「대미지를 준 후 해제」의 순서이고 파싱 배열 순서가 그것을 강제한다. **만료**로 방출하는 쪽은 이 이벤트를 쓰지 않고 `event:state_end:`를 쓴다(도로시 `낙인`). 트로니 `누적 폭발 스킬`이 첫 보유자(180초 13회) |
| `event:state_end:[상태명]` | ✅ | `tick()`에서 버프 만료 시 자동 발생. **명시적 제거에서도 나간다** — `remove_named_buff`·`target_effect` 부분 제거·`weapon_change` 모드 종료가 모두 같은 이벤트를 쏜다(`buff_manager` 1391·1419·3363·4604·4653행). 「만료만」이 아니다 — 폴리 애장품 3단계 지속 회복이 2단계의 `폴리스 뱃지 제거`로도 발동하는 근거 |
| `event:[상태명/스킬명]` | ✅ | `_activate()`에서 named buff 최초 등록 시 `notify(f"event:{name}", ...)` 자동 발생. 타임라인 별도 추가 불필요. 통지 대상은 **기본 스쿼드 전체 브로드캐스트**이며, 효과에 `event_scope: "recipients"`가 있으면 실제 수령자에게만 통지한다 (`_event_audience()`). 서로 다른 캐릭터가 같은 이름의 상태를 각자 갖는 경우(퀸(마코토)·유키코의 `1more`) 남의 상태 변화로 트리거가 열리는 것을 막는다 |
| `hp_below:N` | ✅ | 본인 체력이 N%를 위에서 아래로 넘을 때(직전 > N, 지금 ≤ N) `sync_hp` → `bm._notify_hp_below()`가 쏜다 — 아래 `hp_below_count:`와 같은 함수이고 인덱스 키가 timing 그대로다. 체력을 줄이는 모든 경로(보스 공격·`current_hp_reduce`·현재 체력 유지 최대 체력 증가)에서 나간다. **2026-09-24까지 「notify 호출처 없음 ⚠️」로 적혀 있었으나 낡은 서술이었다** — 보유자가 없어 드러나지 않았고, 첫 보유자 A2 `B 모드 해제`(자해 23번째 틱 t=26.217, 체력 39.2%)로 발동을 확인했다 |
| `hp_below_count:N:순서` | ✅ | 본인 체력이 N%를 위에서 아래로 넘을 때 `bm._notify_hp_below()`가 `hp_below:N`을 쏜다. **즉사한 발(체력 0)은 쏘지 않는다** — 쏘면 목단 `근성`(최대 체력 ▲·체력 동반 증가)이 0이 된 체력을 되살린다 |
| `every:Ns` | ✅ | `tick()`에서 내부 타이머로 처리. notify 경로 아님 |
| `every_stack:이름:N` | ✅ | **게이지 한정**. `gauge_charge` 핸들러가 충전 전후 값으로 `_emit_every_stack()`을 불러, 그 게이지를 보는 N들(`_every_stack_steps`, 인덱스 구축 시 수집)의 **배수 경계를 위로 넘을 때마다** `notify("every_stack:이름", stack_value=경계)`를 쏜다. `_timing_match`가 `경계 % N == 0`으로 가른다 — 한 번에 여러 경계를 넘으면 경계마다 따로. cap에 걸려 값이 안 오르면 발동하지 않는다. 소모 후 다시 넘으면 재발동. 중첩 **버프**의 문턱은 이 키가 아니라 `stack_reach:`다. 이름 없는 `every_stack:N` 형태는 쓰인 적이 없어 이름 포함 형태로 정정했다(2026-09-13). 길로틴 : 윈터 슬레이어 `용사 레벨 업`(`경험치` 게이지 10마다) |
| `stack_reach:버프명:N` | ✅ | 스택이 N에 도달하는 순간 `notify("stack_reach:버프명:N")` 발생. 발생처 **두 곳** — `_activate()`(일반 부여)와 `_dispatch_instant()`의 `buff_stack_add`. `_timing_match`에 분기 있음. 스택 리셋 후 재도달 시 재발동. **`[지속]`(`duration: -1`) 버프를 "최대 중첩 시" 조건으로 켤 때는 `self_stack_above` condition 대신 이 timing을 쓴다** — condition은 `_RUNTIME_COND_PREFIXES` 재평가로 중첩 해제와 함께 즉시 꺼진다(팬텀 `괴도의 시선 3`) |
| `on_attack` | ✅ | **발사**(`공격 시`·`일반 공격 시`). `_fire()`(자동사격: SG/AR/SMG/MG)와 `_tick_charge()`(풀차지 발사: SR/RL) 두 경로에서 발사 1회당 1회. **명중 계열(`hit_count`·`full_charge_hit`)보다 앞서 발생한다** — 쏘고 나서 맞는 순서이고, 같은 발의 「N회 공격 시」 버프가 「N회 명중 시」 딜에 실리는 근거다(레이 `선두 제압`). 둘 다 `calc_damage` **뒤**라 그 발의 대미지 자체는 어느 쪽도 못 바꾼다 |
| `first_trigger` | ❌ | 미구현이며 **쓰지 않는다**(유저 결정 2026-09-21). 원문 「최초 발동 시」는 **직전 clause의 timing + `max_trigger: 1`**로 적는다 — 이 키를 그대로 쓰면 `_timing_match`에 분기가 없어 영구 무발동이다(배치 [04] 일레그 `event:target_spawn` 건과 같은 계통). 매핑 정본은 `PARSING.md` §4-1. 보유자 0명이라 소급 영향 없음 |
| `multi_hit:N` | ✅ | `_timing_match`에 분기 있음. `bm.notify("multi_hit:N", ...)` — 타임라인에서 동시 명중 감지 필요 |
| `part_hit_count:N` | ✅ | `notify_team_hit("squad_part_hit", t, attacker)` 스쿼드 브로드캐스트. `_team_hit_index` 경로. `enemy.has_parts=True`일 때 비코어 히트마다 발생. **좌표 모드에서는 착탄점이 파츠(parts 표적)인 펠릿마다**(저지원·코어는 아니다 — `CharState._coord_pellet`). `_activate(eff, attacker, t)`로 target:"self"=발사 아군 |
| `body_hit_count:N` | ✅ | `notify_team_hit("squad_body_hit", t, attacker)` 스쿼드 브로드캐스트. `_team_hit_index` 경로. `enemy.has_parts=False`(기본값)일 때 비코어 히트마다 발생. **좌표 모드에서는 착탄점이 표적 없는 코어 밖 본체인 펠릿마다** |
| `charge_hold:N` | ✅ | `CharState._notify_charge_hold()`(`timeline.py`)가 `_charge_full_t`(풀차지 도달 래치)로 유지 시간을 재서 notify한다. 임계값은 `BuffManager.charge_hold_thresholds(caster)`가 그 캐릭터의 효과에서 뽑는다 — `_timing_match`가 문자열 완전 일치라 원문 표기(`0.5`)를 그대로 보낸다. **판정은 한 차지에 1회**(`_charge_hold_fired`, 차지 재시작 시 리셋) — 계속 들고 있어도 재판정하지 않는다. **홀드 조작이 없으면 풀차지 즉시 발사라 영구 무발동**이다: `control["sequence"]`의 `hold`, 정책 `own_full_burst`·`charge_hold_after_fb` 중 하나가 필요하다 — 정본: `docs/CONTROL.md §홀드`. **CDN `input_type`이 `DOWN_Charge`인 6명은 홀드 자체가 불가**(차지가 차면 자동 발사)라 구조적으로 발동할 수 없다 — 홀드를 지정하면 `CharState.__init__`이 즉시 실패시킨다. 밀크 : 블루밍 바니 `부끄러움` |
| `weapon_hit:[name]` | ✅ | `_timing_match`에 분기 있음. notify 호출처는 `_handle_damage_eff()` **한 곳뿐**으로, `[name]`은 **named damage 효과**(발사체 등)의 이름이다 — 그 효과가 명중할 때마다 발생한다(라피 : 레드 후드 `부착형 유탄 4`). **`weapon_change` 모드의 사격은 이 이벤트를 쏘지 않는다** (`_tick_weapon_change()`에 호출처 없음, 2026-08-13 확인) → 모드의 매 발마다 붙는 효과는 `hit_count:1` + `self_state:[모드명]`으로 센다 |
| `feather_tick` | ✅ | **아인 전용**. 니어 페더 소환체의 공격 주기 틱. `tick()`이 `state["feathers"]`를 돌며 `notify("feather_tick", ...)`. `_timing_match`는 `timing == event` 일반 분기를 그대로 탄다(별도 분기 불필요). 주기가 고정이 아니라 생존 수 n에 대해 `base × mult^(n-1)`이고, **다음 발사는 직전 예약 시각 기준**으로 잡는다(프레임 양자화 드리프트 방지). 만료로 수가 줄어도 예약된 시각은 바뀌지 않는다 — 재소환(`feather_refresh`)만 초기화한다. 정본: `docs/scenarios/아인.md §니어 페더 메커니즘` |
| `squad_ammo_consume:N` | ✅ | `_timing_match`에 분기 있음(`buff_manager.py`). `notify()`가 `__squad__` 누적 카운터로 집계해 스쿼드 전원의 효과를 순회(`_squad_notify_index`). 발생처는 `timeline.py` 자동사격·풀차지 발사 두 경로에서 **1발당 1회**, 그리고 `gauge_consume_as_ammo`(벨벳). 소비자: 리틀 머메이드 `거품 난사`(500발, `sequential_damage:10`)·`버블 오더 4`(400발), 일레그 : 붐 앤 쇼크 `고스트 버스터 2`(100발), 신데렐라 : 크리스탈 웨이브 `뷰티-풀 3`(200발) |

### condition

평가 위치:
- `_condition_ok()`: 버프 발동 시점 1회 (notify 시)
- `_runtime_condition_ok()`: `get_buffs()` 호출마다 (상태 의존 조건)

| condition | 평가 위치 | 구현 상태 | 비고 |
|---|---|---|---|
| `during_full_burst` | 양쪽 모두 | ✅ | `state["full_burst"]` |
| `not_during_full_burst` | 양쪽 모두 | ✅ | `state["full_burst"]` |
| `prob:N` | `_condition_ok` 전용 | ✅ | `get_buffs`에서 재판정 안 함. `prob:{0}` 자리표시자면 timing의 `hit_count:{0}`과 같은 규약으로 `trigger_values[스킬레벨]`에서 확률을 꺼낸다 — 확률이 레벨마다 다른 슬롯용(토브 `급조 탄환` 기본 판본). `trigger_values`가 없으면 발동하지 않는다. **기대값 모드(`rng_mode: "expected"`)에서는 난수 대신 확률을 (효과, 캐스터)별로 누적해 1.0을 넘길 때 발동**한다 — 기대 횟수는 같고 위상만 규칙적이다(`state["rng_expected"]`·`state["rng_acc"]`) |
| `self_hp_above:N` | 양쪽 모두 | ✅ | `state["hp_pct"]` |
| `self_hp_below:N` | 양쪽 모두 | ✅ | `state["hp_pct"]` |
| `self_hp_max` | 양쪽 모두 | ✅ | `hp_pct >= 100.0` |
| `ally_hp_below:N` | 양쪽 모두 | ✅ | `_condition_ok`는 target resolve 전이라 **스쿼드 최저 체력**이 N% 이하인지로 판정, `_runtime_condition_ok`가 `state["hp_pct"][query_target]`로 대상별 재판정. 활성화 시점 분기가 없으면 instant 효과가 조건을 통째로 무시한다 |
| `ally_hp_max` | — | ❌ | 미구현. 분기 없음 |
| `during_charge` | 양쪽 모두 | ✅ | `state["charging"][caster]` |
| `during_shield` | 양쪽 모두 | ✅ | 조건 평가 대상에게 만료 전 `shield_from_max_hp_pct` 보호막이 하나 이상 있으면 참 |
| `self_cover_alive` | 양쪽 모두 | ✅ | 자신의 엄폐물이 살아 있는가 — `bm.cover_alive()`(`state["cover_hp"][caster] > 0`). `_RUNTIME_COND_PREFIXES` 등록 — 엄폐물은 보스 공격(`enemy.patterns`의 `attack`)에만 부서지고, 부서지는 순간 `bm.break_cover()`가 집계 캐시를 비워 같은 프레임부터 꺼진다. 패턴이 없으면 늘 참이다. 슈가 `블랙 타이푼 4` |
| `not_self_cover_alive` | 양쪽 모두 | ✅ | 위의 부정 — 「자신의 엄폐물이 **파괴된** 상태라면」(2026-09-20 구현). `not_` 접두사는 일반 처리가 없어 분기를 따로 달았다(`not_self_state:`·`not_during_full_burst`와 같은 방식). 짝과 함께 `_RUNTIME_COND_PREFIXES`에 등록돼 있다 — 엄폐물이 부서지는 프레임에 켜지고 `cover_revive`로 되살아나는 프레임에 꺼진다. **기본 경로에서는 늘 거짓**이고, 구현 전에는 `_condition_ok`가 모르는 조건을 참으로 흘려보내 베이의 애장품 2·3단계가 엄폐물이 멀쩡한데도 발동했다(15회·1회 → 0회·0회). **포지티브를 보려면 엄폐컨이 필요하다** — 엄폐물은 「엄폐 중일 때」만 깎이고 기본 컨트롤에서 엄폐는 재장전뿐이라(`CharState.in_cover`), 재장전이 드문 캐릭터는 세기만 올리면 엄폐물보다 체력이 먼저 바닥난다. 버스트 엄폐컨(`own_full_burst`)을 걸면 자기 풀버스트 동안 피해가 엄폐물로 들어가 파괴·부활이 성립한다(`docs/scenarios/베이.md` §검증 스쿼드 변형 2). 베이 `치얼업 투게더 3`(애장품 2) · `퍼스트 위너`(애장품 3) |
| `no_broken_cover_ally` | `_condition_ok` 전용 | ✅ | 「엄폐물이 파괴된 아군이 없다면」(2026-09-24 구현 — 첫 보유자 릴리 `최고의 엔지니어! 3`). **시전자 포함** 산 아군(`_alive()`) 전원의 엄폐물이 살아 있으면(`bm.cover_alive()`) 참 — 같은 스킬 앞 clause의 대상 `allies_broken_cover_random:N`(시전자를 빼지 않는다)의 후보가 0기인 것과 정확히 같은 판정이라 두 clause가 배타 분기가 된다. 발동 시점 판정이다(`[10초 유지]` 버프의 게이트 — `_RUNTIME_COND_PREFIXES`에 넣지 않는다). 엄폐물은 보스 공격 패턴이 있을 때만 부서지므로 **기본 경로에서는 늘 참**이다 — 구현 전에도 `_condition_ok`가 모르는 조건을 참으로 흘려보내 기본 경로 결과는 같았고, 엄폐물이 부서진 보스 패턴에서만 40% 분기가 잘못 켜졌다. **배열 순서가 판정의 일부다** — 같은 `burst_cast`의 부활(`cover_revive`)이 먼저 돌면 되살아난 엄폐물 때문에 판정이 뒤집히므로 부활을 맨 뒤에 둔다(PARSING §4-2) |
| `during_reload` | — | ❌ | 미구현. `state["reloading"]` 연동 필요 |
| `burst_casted` | `_condition_ok` 전용 | ✅ | `state["burst_casted"][caster]` |
| `burst_not_casted` | `_condition_ok` 전용 | ✅ | `state["burst_casted"][caster]` |
| `back_row` | `_condition_ok` 전용 | ✅ | 스쿼드 인덱스 1 또는 3 = 후열 (포지션 2번, 4번) |
| `squad_ally_exists` | `_condition_ok` 전용 | ✅ | 소속 스쿼드(`parsed_nikke["squad"]`, 카운터스·이지스 등)가 같은 아군이 자신 외에 편성돼야 True. 의상 버전도 원본과 같은 스쿼드일 수 있다(라피 : 레드 후드 = `Counters`). 스쿼드가 없는 더미(`test_B*`)는 False |
| `focusing` | 양쪽 모두 | ✅ | 「자신이 포커싱 상태일 때」 = 시전자가 **카메라를 잡고 있다**(`state["camera"]` — 조율이 프레임마다 옮기고, 조작이 없으면 정적 유도값). `_RUNTIME_COND_PREFIXES`에 있어 카메라가 옮으면 켜졌다 꺼진다(2026-09-18 전에는 재평가 대상이 아니라 늘 켜져 있었다 — 효과 `focus_fire`가 무영향이라 딜 불변). 리틀 머메이드 `버블 오더` |
| `not_core` | `_condition_ok` 전용 | ✅ | **트리거를 일으킨 그 탄**이 비코어인가. `_fire()`·`_tick_charge()`가 `hit_count` notify에 ctx `core_frac`(그 탄의 코어 확률 — 펠릿 판정을 총구 단위로 평균, `timeline._bullet_core_fracs`)을 싣는다. 확률 판정 모드는 0/1이라 그대로, 기대값 모드는 `prob:`와 같은 규약으로 `(1 − core_frac)`을 (효과, 캐스터)별로 누적해 1.0을 넘길 때 발동한다. ctx가 없는 경로(명중 외 트리거)는 비코어로 본다. 길로틴 : 윈터 슬레이어 `경험치 2`(`hit_count:6`) |
| `core_hit_count:1` | — | ❌ | 미구현. timing이 아닌 condition으로 쓰일 때 |
| `self_state:상태명` | 양쪽 모두 | ✅ | `_has_self_state()` 단일 창구. `_active` 버프 **+ `state["weapon_change"]` 무기 변경 모드명**을 함께 본다 — 모드는 `_active`에 등록되지 않으므로 이걸 빼면 `self_state:저격 모드`류가 영구 거짓이 된다. 나유타 `위선 5/6`(`self_state:기억 연소`), 신데렐라 : 크리스탈 웨이브 `모드 스왑 2`. **상태명이 총칭 `무기 변경`(`WEAPON_CHANGE_STATE`)이면 모드명 대조가 아니라 "아무 모드든 켜져 있는가"로 읽는다** — 원문이 모드 이름 대신 「자신이 무기 변경 상태라면」이라고만 쓰는 경우다(목단 `다 덤벼! 2`. 2026-08-28 이전에는 모드명으로만 대조해 영구 거짓이었고, 고친 뒤 목단 개인 딜 +64%). **상태 이름은 반드시 지속 효과에 붙어야 한다** — instant에만 붙으면 조용히 영구 거짓이 된다(`docs/PARSING.md` §상태의 담체, `doclint` 검사 K).<br>**참조되는 버프 자신의 발동 조건은 보지 않는다 — 의도다.** `_by_name`으로 `_active` 멤버십과 `target_chars`만 보고 그 버프의 `trigger.condition`은 읽지 않는다. 조건은 *부여 게이트*이고 `self_state:`는 *마커가 있는가*를 묻는 것이라 층이 다르다. 재평가를 넣으면 ① 밀크 : 블루밍 바니 `부끄러움`은 자기 condition이 `not_self_state:부끄러움`이라 **자기 이름을 봐서** 재귀가 종료하지 않고, ② 프리카 `무대 파트 : 보컬`(민트에게 거는 `duration: -1` 마커, 조건 `self_state:퍼포먼스`)은 `퍼포먼스`가 25초짜리라 원문 「해제 불가」와 반대로 25초 뒤 꺼지며, ③ 소다 : 트윙클링 바니 `시간 연장 I·II`(조건 `self_stack_above:골든 칩:10/20`, `duration: -1`)는 스택이 빠지는 순간 `fullburst_duration` 연장이 도중에 풀린다. **마커의 유효 구간을 좁히려면 조건이 아니라 그 마커의 `duration`을 고칠 것.** 영향 범위(조건부 영구 마커를 참조하는 10건 열거)와 미수정 근거는 `docs/PARSING-CHARS.md` 엠마 : 택티컬 업 스킬1·2 |
| `not_self_state:상태명` | 양쪽 모두 | ✅ | 위와 같은 창구의 부정. 신데렐라 : 크리스탈 웨이브 `모드 스왑 3` |
| `target_state:상태명` | 양쪽 모두 | ✅ | 적(`"__enemy__"` 또는 쫄몹 id)이 target_chars에 있는 활성 효과로 확인. 조건에는 「지금 맞는 적」 문맥이 없어 쫄몹이 있으면 **어느 적에게든** 붙어 있으면 참이다(⬜ 근사). **게이지에 쓰지 않는다** — 게이지는 `state["gauges"][caster]`에 살아 `_has_target_state()`에 절대 안 걸린다. `[게이지명] 보유 상태라면`은 `gauge_above:게이지명:1`이다(`PARSING.md` 4-2). 솔린 : 프로스트 티켓 `열차 탑승 도와줄게!`가 `target_state:티켓`이라 영구 거짓이었다(2026-09-05 수정) |
| `not_target_state:상태명` | 양쪽 모두 | ✅ | `target_state:`의 부정형. `_has_target_state()` 단일 창구를 공유한다. **미구현 시 조용히 항상 통과**하므로(조건 미매칭은 `return True`로 빠진다) 부여 조건으로 쓰면 매 히트 재부여되어 루프가 폭주한다 — 팬텀 구현 전 실측 딜 비중 77%. 팬텀 `예고장`·`괴도의 단검` |
| `target_stunned` | `_condition_ok` 전용 | ✅ | 대상이 기절 상태인지. `is_stunned("__enemy__")` — 버프 *이름*이 아니라 `stat == "stun"` 유무를 보므로 기절을 건 효과의 이름·주체와 무관하다. 기절은 이름 있는 상태가 아니므로 `target_state:`를 쓰지 않는다(프리바티 `LD 어설트 3` 기본 판본). `_RUNTIME_COND_PREFIXES`에 넣지 않는다 — 발동 시점 게이트다 |
| `self_stun_immune` | `_condition_ok` 전용 | ✅ | 자신이 **기절 면역 상태**인지. `_has_immune(caster, "stun_immune")` — `target_stunned`와 같은 규약으로 **버프 이름이 아니라 stat 유무**를 보므로 남이 건 기절 면역도 참이다(`self_state:`를 쓰지 않는 이유). `_RUNTIME_COND_PREFIXES`에 넣지 않는다 — 발동 시점 게이트다. D `처단 3`(기절 면역일 때만 팀 풀버스트 +5.04초) |
| `self_undying` | `_condition_ok` 전용 | ✅ | 「자신이 **불굴 상태**라면」(2026-09-23 구현) — `self_stun_immune`의 불굴판. **버프 이름이 아니라 stat(`undying`) 유무**를 보므로 남이 건 불굴(블랑 `쇼타임 2`)도 참이다. 판정은 보스 공격이 쓰는 것과 같은 `has_live_stat(caster, "undying", t)`. 발동 시점 게이트라 `_RUNTIME_COND_PREFIXES`에 넣지 않는다. **구현 전에는 `_condition_ok`가 모르는 조건을 참으로 흘려보내 매 버스트 발동했다**(표준 15회 → 0회, `not_self_caused_heal` 선례) — 딜 0축(받는 회복량)이라 총딜은 한 자리도 안 움직였다. 마키마 `조용히 해주겠니? 3` |
| `target_code:[코드]` | `_condition_ok` + `_runtime_condition_ok` | ✅ | 대상(적)의 속성 코드 확인. `self.state["enemy"]["code"]`와 비교. 코드 미설정(빈 문자열)이면 항상 통과. **`_RUNTIME_COND_PREFIXES`에 있다**(2026-09-22, 유저 결정) — 적 코드는 전투 중 변하지 않아 이산 timing 보유자에게는 값이 안 바뀌지만, **무한 지속 `passive`는 게이팅을 이 목록에만 의존**하므로 빠져 있으면 조건이 통째로 무시된다. 레이블 `연애의 달콤함(상상) 4`(`passive` + `self_state:망상` + `target_code:전격`)가 풍압 보스에게도 전격 한정 피해 감소를 그대로 받고 있었고, **같은 캐릭터의 `battle_start` 판본은 정상이라 한 캐릭터 안에서 두 판정이 갈렸다**. 맥스웰 `일렉트릭 샷`(`enemy_count_above:`)과 같은 계통. 회귀 33/33 불변 |
| `target_is_boss` | `_condition_ok` 전용 | ✅ | 「대상이 타겟이라면」(명중·공격 트리거) — 트리거를 낸 한 발의 대상, 곧 **시전자가 겨눈 적**(`calculator/boss_pattern.py` §조준)이 보스(타겟)인가 — `_resolve_enemies("target", caster)`의 첫 적이 `__enemy__`인가. 쫄몹이 없으면 항상 참. 저지원·파츠를 겨눴어도 보스다. 발동 시점 게이트라 `_RUNTIME_COND_PREFIXES`에 넣지 않는다 — 리버렐리오 `격류`는 `[지속]`이고 반대 분기의 `격류 해제`가 끈다. 리버렐리오 `격류`·`완만류 해제` · 네온 : 비전 아이 `화력 폭발 1·2` · 로산나 `광기`(애장품 1)·`벤데타 2`(애장품 2) · 팬텀 `괴도의 단검 2`(애장품 1) |
| `target_is_add` | `_condition_ok` 전용 | ✅ | 「대상이 타겟이 아닌 랩쳐라면」 — 위의 부정(시전자가 겨눈 적이 쫄몹). 쫄몹이 없으면 항상 거짓. 리버렐리오 `완만류`·`격류 해제` |
| `self_stack_above:스택명:N` | 양쪽 모두 | ✅ | `_active`에서 스택 수 확인 |
| `target_stack_above:스택명:N` | `_condition_ok` 전용 | ✅ | 「**대상이** [스택명] 최대 중첩 상태라면」(2026-09-24 구현) — `self_stack_above:`의 대상(적)판. 적(`__enemy__`·쫄몹 id)에게 걸린 그 이름 효과의 중첩 수(여럿이면 최대, `_target_stack()`)를 `target_state:`와 같은 근사(어느 적에게든)로 비교한다. 발동 시점 1회 판정 — 보유 효과(`일어남`)가 유한 지속이라 런타임 재평가 대상이 아니다. 첫 보유자 프림 `일어남`(애장품 1단계) |
| `self_stat_above:stat키:N` | `_condition_ok` 전용 | ✅ | 자신에게 적용 중인 해당 stat의 **합이 N보다 클 때** 참. `self_state:`(버프 *이름* 판정)와 달리 **stat 값**을 본다 — 누가 준 버프인지 무관. `_STAT_TO_BUFF`로 buffs 키를 찾아 `get_buffs()` 값을 읽으므로 스택·scaling이 이미 반영된 값이 기준이다. 모더니아 `대도약 2`(`self_stat_above:accuracy_pct:0` = "자신이 명중률 증가 상태라면") |
| `gauge_above:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `gauge_below:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `gauge_eq:게이지명:N` | 양쪽 모두 | ✅ | `state["gauges"][caster][gauge_id]` |
| `has_burst1_ally` | `_condition_ok` 전용 | ✅ | `state["burst_stages"]` |
| `no_defender_ally` | `_condition_ok` 전용 | ✅ | 자신 제외 스쿼드에 `parsed_nikke["class"] == "방어형"`인 아군이 **없어야** True. `has_defender_ally`와 한 분기에서 함께 판정한다 |
| `has_defender_ally` | `_condition_ok` 전용 | ✅ | 위의 부정. **둘은 같은 원문의 배타 분기라 한쪽만 구현하면 양쪽이 동시에 성립한다** — 2026-09-02 이전이 그 상태였고, 델타 : 닌자 시프 `인법 카모플라쥬`(보호막·주목)와 `인법 카모플라쥬 2`·`인법 인젝션`이 전투 시작에 전부 걸렸다. 스쿼드 구성은 전투 중 불변이므로 `_RUNTIME_COND_PREFIXES` 대상이 아니다 |
| `no_burst1_ally` | `_condition_ok` 전용 | ✅ | `state["burst_stages"]` |
| `enemy_count_below:N` | 양쪽 모두 | ✅ | 랩쳐/적 N기 이하. 적 수 = 보스 1 + 산 쫄몹(`state["enemy_count"]`, 프레임 맨 앞). 쫄몹이 없으면 1 → 1<=N 항상 True. 마르차나 : 마린 스터디 |
| `enemy_count_above:N` | 양쪽 모두 | ✅ | 랩쳐/적 N기 이상. 적 수는 위와 같다. 쫄몹이 없으면 1 → N>=2면 False, 무발동. **`_RUNTIME_COND_PREFIXES`에도 등록**(2026-08-08) — `passive` 버프는 조건 미충족이어도 등록된 뒤 게이팅을 runtime 재평가에만 의존하므로, 여기 없으면 보스전에서 그대로 적용된다(맥스웰 `일렉트릭 샷`). 마르차나 : 마린 스터디, 맥스웰 |
| `optimal_range` | `_condition_ok` 전용 | ✅ | 적정 사거리 여부. 판정의 정본은 **`buff_manager.in_optimal_range`**로, ③ 고정 +30%를 태우는 `timeline`의 `is_optimal_range`와 같은 함수다 — 보스 거리(`enemy.distance`)가 없으면 **적 스펙 `optimal_range_weapons`**에 시전자 무기군이 있는가, 있으면 시전자의 적정 구간(CDN bonusrange · 적정 최대·최소 사거리 ▲ 반영)에 거리가 드는가. 시전자 무기군은 로스터 값(`parsed_nikke["weapon_type"]`)을 보므로 무기 변경 모드는 반영하지 않는다 — 현재 사용처(에이드 : 에이전트 바니 `요원의 시선`·`요원의 움직임`)에 모드 전환이 없다. **기본값이 빈 목록·거리 없음이라 스쿼드 스펙이 둘 다 안 적으면 무발동**이다 |
| `core_hit` | `_condition_ok` 전용 | ✅ | 대상이 코어 보유 적일 때. **`enemy["core_px"] >= 1` 기준**(0이면 코어 없음). 기본공격의 코어히트는 명중률·탄착군 확률이지만 이 condition이 붙은 효과는 "코어가 활성화된 적" 대상의 **확정 발동**이다 — 확률 판정을 걸지 않는다. 기본값 `core_px = 0`이므로 코어 없는 보스에서는 정상적으로 무발동. 리버렐리오 `차분한 수심 2`, 신데렐라 : 크리스탈 웨이브 `모드 스왑 3` |
| `gauge_mod:게이지명:mod:나머지` | `_condition_ok` 전용 | ✅ | 게이지값 `% mod == 나머지`일 때 발동. 민트, 아르카나 : 포츈 메이트 |
| `not_self_caused_heal` | `_condition_ok` 전용 | ✅ | 「**자신이 사용한 회복 효과가 아니라면**」(2026-09-21 구현) — `event:heal_received`와 짝으로만 쓴다. 회복 핸들러 두 곳이 `notify(..., heal_source=<건 쪽>)`을 싣고(`timeline.handle_heal_hp_pct`는 효과의 시전자, `_apply_lifesteal`은 **때린 본인** — 합산 버프라 어느 아군의 몫인지 가를 수 없다, ⬜ 인게임 미확인) 조건이 `_notify_ctx`에서 그것을 읽는다. `hit_crit`·`core_frac`과 같은 자리다. `heal_source`가 없는 경로는 출처 불명이라 종전대로 통과시킨다. **구현 전에는 `_condition_ok`가 모르는 조건을 참으로 흘려보내 자기 회복에도 발동했다** — 백학이 자기 회복만으로 3중첩을 채워 t=8.00에 `서약 해지`가 켜지고 팀 최대 체력 버프가 172초 동안 죽었다. 백학 `서약 위반 증거` |
| `trigger_hit_crit` | `_condition_ok` 전용 | ✅ | 트리거를 발생시킨 히트가 **실제 크리티컬 롤에 성공**했는가. named damage 명중(`hit_count:[이름]:N`)과 짝으로 쓴다. `prob:` 확률 근사가 아니라 그 히트의 롤 결과를 그대로 읽는다 — 근사로 대체하면 원래 딜과 상관관계가 끊긴다(유저 결정, 2026-08-17). 율리아 `마르카토 2` |

---

## target 마스터 테이블

**새 target 파싱 시 반드시 이 테이블 업데이트.**

구현 상태 범례:
- ✅ 완전 구현 (`_resolve_target()`에 분기 있음)
- ❌ 미구현 (분기 없음 — 빈 리스트 반환)

**전투불능 아군은 모든 아군 대상에서 빠진다**(`_resolve_target()` 래퍼). 순위로 N명을 고르는 대상
(`allies_top_atk:` 등)은 거르고 나서 자르므로 산 사람으로 N명이 찬다(`_alive()`). 전투불능은 보스
공격 패턴이 있을 때만 생기므로 패턴이 없으면 결과가 종전과 같다.

lazy resolve: 버프 반영 스탯 기준 정렬 필요 target → `_activate()` 아닌 `get_buffs()` 시점에 resolve → `_LAZY_RESOLVE_PREFIXES`에 등록 필요.

> **instant 경로도 같은 해석기를 쓴다** (2026-08-16 홍련 등록 중 수정).
> `timeline._register_instant_handlers()`의 `_resolve_targets()`는 예전에 `self` · `all_allies` · 리스트만
> 처리하고 **나머지를 전부 시전자 자신으로 조용히 폴백**했다 — `heal_hp_pct`·`ammo_charge_*`·
> `burst_cooldown_reduce`·`current_hp_reduce`·`force_reload`에 다른 target이 붙으면 대상이 틀렸다.
> 지금은 `bm._resolve_target()`에 위임하고 적 센티널·스쿼드 밖 이름만 걸러낸다(매칭 0명이면 무발동).
> instant는 지속시간이 없어 지연 resolve가 의미 없으므로 발동 시점 상태로 즉시 판정한다.
> 영향받던 항목은 4건(전부 `heal_hp_pct`) — 나가 `우정의 서포트 2`, 트리나 `네이처 그레이스 2·3`
> (`allies_lowest_hp:2`), 플로라 `마음의 평화`(`allies_adjacent:2`). 회복은 보스 sim 딜에 직접 기여하지 않아
> 오래 드러나지 않았고, **체력이 조건인 캐릭터(홍련)에서 처음 딜 차이로 드러났다**
> (트리나 조합 홍련 hp_pct 수렴 12.7~19.1% → 28.6~39.5%).



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
| `"allies_top_hp:N"` | ❌ | ❌ | **최대 체력이 가장 높은 아군 N기**(2026-09-20 신설). `enemies_top_hp:N`의 아군판이고, 지금은 `target`이 아니라 **`copy_from`의 값으로만** 쓰인다 — `hp_copy` 보유자(신 `센텐스 엔딩스` · 퀀시 `새로운 루트`)의 버프 대상은 `self`이고 이 키는 *어느 아군의 체력을 읽을지*만 정한다. 정렬 기준은 **버프 제외 기본 최대 체력**(`_top_by`의 비-atk 분기와 같다)이라 lazy resolve가 필요 없고, 동률이면 스쿼드 입력 순서다 — 기본 스펙에서 최대 체력은 클래스만으로 정해져 동률이 흔하다. 시전자 포함(원문에 제외 표기 없음). 미구현 |
| `"allies_lowest_atk_burst3:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 3버스트 아군 중 공격력 최저 N명 |
| `"allies_random:N"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 자신 제외 무작위 |
| `"allies_weapon:무기유형"` | ❌ | ✅ | `parsed_nikke["weapon_type"]` 기준 |
| `"allies_weapon_excl_self:SG"` | ❌ | ✅ | 자신 제외 샷건 소지 아군 전체. `_resolve_target()`에 `allies_weapon_excl_self:` 분기 추가. `allies_weapon:SG`와 별도 |
| `"allies_weapon_top_atk:무기유형:N"` | ✅ | ✅ | 해당 무기 소지 아군 중 **최종 공격력 최고 N기**. `allies_weapon:X` ∩ `allies_top_atk:N`. 공격력 정렬이므로 `_LAZY_RESOLVE_PREFIXES` 등록 필수. 시전자 포함(자신 제외 표기 없음). 매칭 아군이 N보다 적으면 있는 만큼. 레오나 `용기있는 시선 2`(`SG:2`) |
| `"allies_class:클래스"` | ❌ | ✅ | `parsed_nikke["class"]`와 **정확히 일치**하는 아군 전체. 값은 `화력형`·`방어형`·`지원형`이며 「형」을 떼지 않는다 — `_resolve_target()`이 `== cls`로 비교한다. **첫 보유자는 비스킷**(2026-09-20). 그 전까지 보유자가 0명이라 `PARSING.md` §5의 매핑이 `공격`·`방어`·`지원`으로 잘못 적혀 있어도 드러나지 않았다(2026-09-20 정정) |
| `"allies_class:클래스:N"` | ❌ | ✅ | 위 「전체」판의 **인원수 제한형**(`방어형 아군 2기에게`, 2026-09-23 구현). 원문에 정렬 기준이 없으므로 `allies:N`과 같은 **스쿼드 입력 순서 앞 N명**이다 — 고정 속성 기반이라 lazy resolve 대상이 아니다. 두 칸(`allies_class:클래스`)이면 종전대로 전체다. **구현 전에는 `allies_class:` 분기가 `split(":")[1]`만 읽어 세 번째 칸을 조용히 무시했다** — 「방어형 전체」로 떨어졌고 doclint 검사 A도 `prefix()`가 첫 콜론까지만 보므로 못 잡았다(방어형 3명 스쿼드에서 키리 `훑어보기`가 셋 다에게 걸렸다). 첫 보유자는 키리 `훑어보기`·`곁눈질 2` |
| `"allies_code:코드"` | ❌ | ✅ | `parsed_nikke["element_code"]` 기준 |
| `"allies_code_excl_self:코드"` | ❌ | ✅ | 자신 제외 해당 코드 아군 전체. `allies_code:`와 별도 분기다 — 메이든 : 아이스 로즈 `블레스 유`·`블레스 유 2`는 아군판/자기판이 배타 분기라 시전자를 빼지 않으면 한쪽이 양쪽을 다 받는다 |
| `"allies_code_weapon:코드:무기유형"` | ❌ | ✅ | 코드+무기 복합 조건 아군 전체. `_code_weapon()` 헬퍼가 `element_code`·`weapon_type` 동시 필터. 트리나(`전격:AR`) |
| `"allies_code_weapon_leftmost:코드:무기유형:N"` | ❌ | ✅ | 위 조건을 만족하는 아군 중 **스쿼드 입력 순서 앞 N명**. 고정 속성 기반이라 lazy resolve 불필요. 매칭 0명이면 빈 리스트. 트리나(`전격:AR:1`) |
| `"allies_squad"` | ❌ | ✅ | **동일 스쿼드**(`parsed_nikke["squad"]` — 앱솔루트·카운터스·이지스 등) 아군 전체. **시전자 포함**이고, 스쿼드가 없는 더미(`test_B*`)는 제외된다 — condition `squad_ally_exists`와 같은 기준의 대상판. 엠마 : 택티컬 업 `포메이션 LT` · 은화 : 택티컬 업 `포메이션 AS`(둘이 서로의 `self_state:` 게이팅을 여는 자리라 대상이 좁아지면 추가 효과 6종이 통째로 죽는다) |
| `"allies_below_def"` | ✅ | ✅ | `_LAZY_RESOLVE_PREFIXES` 등록됨. 시전자보다 방어력 낮은 아군 전체 |
| `"allies_burst3"` | ❌ | ✅ | 기본 버스트 단계가 Step 3인 아군 전체. `burst_stages` 기준 |
| `"allies_top_base_charge_time:N"` | ❌ | ✅ | 기본(버프 제외) 차지 시간이 가장 긴 아군 N기. `parsed_nikke["charge_time"]` 기준 고정 속성이라 lazy resolve 불필요. 차지 무기 아군이 없으면 빈 리스트, 동률이면 스쿼드 입력 순서. 마나 `매터 시그마 4` |
| `"allies_down_top_atk_excl:N"` | ❌ | ✅ | 자신을 제외한 전투불능 아군 중 최종 공격력 최고 N기. **`_resolve_target()`이 전투불능 아군을 빼는 규칙의 유일한 예외**(`allies_down_` 접두사). 마나 `매터 감마 3` |
| `"allies_down_class_random:클래스:N"` | ❌ | ✅ | **전투불능 상태 + 클래스 필터 + 무작위 N기**(2026-09-21 구현). 위와 같은 `allies_down_` 접두사라 전투불능 제외 규칙에서 함께 빠진다. 클래스는 `parsed_nikke["class"]`(`화력형`/`방어형`/`지원형`) 기준이고, 후보가 N보다 적으면 있는 만큼·0기면 무발동이다(`allies_broken_cover_random:N`과 같은 규약). **시전자를 빼지 않는다** — 원문에 「자신을 제외한」이 없다(빼는 쪽은 `allies_down_top_atk_excl:N`). 무작위라 지연 resolve 대상이 아니다. 아군은 보스 공격 패턴이 있을 때만 쓰러지므로 기본 경로에서는 늘 0기. 앤 : 미라클 페어리 `파란 나비의 꿈 3` |
| `"allies_without_buff:버프명"` | ❌ | ✅ | 해당 이름의 버프가 **활성이 아닌** 아군 전체. `allies_with_buff:`의 여집합이고 판정도 같은 `_has_self_state()`를 쓴다. 원문 「[상태명] 상태가 아닌 아군 전체에게」 — 재부여를 막는 대상 필터라, **같은 clause 안에서 그 상태를 부여하는 항목보다 다른 항목을 앞에 두어야 한다**(부여가 먼저 끝나면 뒤 항목의 대상이 0명이 된다). 크러스트 `든든한 요리` |
| `"allies_random_with_debuff:N"` | ❌ | ✅ | **해로운 효과를 실제로 보유한 아군** 중 무작위 N기(`_has_harmful()`). `allies_random:N`(자신 제외 무작위)과 달리 시전자를 빼지 않고, `polarity`가 `harmful`·`harmful_irremovable`인 활성 버프 보유를 먼저 거른다. **지연 resolve 대상이 아니다** — 「지금 디버프를 가진 사람」이 곧 부여 시점 판정이다. 보유자가 N보다 적으면 있는 만큼, 0명이면 무발동. 보스 공격 패턴이 없으면 아군에게 걸리는 harmful이 드물어 대체로 무발동이다. 코코아 `프로 종이접기 2` |
| `"allies_with_buff:버프명"` | ❌ | ✅ | 해당 이름의 버프가 활성인 아군 전체. `enemies_with_buff:`의 아군판(그쪽은 쫄몹이 없으면 `__enemy__` 센티널이라 실질 필터가 없다). **부여 시점 스냅샷(비lazy)으로 확정** — "부여 순간 조건을 만족한 아군에게 준다"는 게임 시맨틱에 가깝다. 판정은 `_has_self_state()`를 재사용해 weapon_change 모드도 상태로 인정. 레이 (가칭) `섬멸 지원 4~6` |
| `"allies_burst3_persona_excl_self"` | ❌ | ✅ | 자신을 제외한 · 기본 버스트 단계 Step 3 · `persona_state` 보유 아군 전체. `allies_burst3` ∩ `persona_state` 보유 − 자신. 판정은 `allies_with_buff:`와 같은 부여 시점 스냅샷. 퀸(마코토) `배턴 터치`, 유키코 `추격` |
| `"allies_burst_casted_burst3"` | ❌ | ✅ | 직전에 버스트를 사용한 아군 중 기본 버스트 단계 Step 3. `all_allies_burst_casted` ∩ `allies_burst3`. 아래 무기판과 같은 취지 — `burst_casted`를 condition으로 두면 시전자 기준이라 대상 필터가 안 된다. 에이다 `은밀한 지원 1~3` |
| `"allies_burst_casted_weapon:무기유형"` | ❌ | ✅ | 직전에 버스트를 사용한 아군 중 해당 무기 소지자 전체. `all_allies_burst_casted`(`state["burst_casted"]`)와 `allies_weapon:X`(`parsed_nikke["weapon_type"]`)의 AND. 고정 속성 + 사이클 단위 플래그라 lazy resolve 불필요. 레이 (가칭) `정비 및 보급` |
| `"target"` / `"target_body"` / `"same_target"` | ❌ | ✅ | `__enemy__` 센티널 반환. 타임라인이 실제 처리. **아래 적 대상 전부 — 보스 패턴 `summon`의 쫄몹이 살아 있으면 `bm.enemy_resolver`가 적 id로 푼다**(정본 `calculator/boss_pattern.py` §쫄몹). 이 셋은 조준 — 딜도 효과도 시전자가 겨눈 적 1기(`boss_pattern` §조준 — 쫄몹을 겨눴으면 먼저 나온 산 쫄몹, 아니면 보스) |
| `"all_enemies"` / `"enemies_in_range"` / `"enemies_nearest_in_range"` | ❌ | ✅ | `__enemy__` 센티널 반환. 쫄몹이 있으면 `all_enemies`·`enemies_in_range`는 전원(범위는 좌표가 없어 전원), `enemies_nearest_in_range`는 조준 |
| `"enemies_random:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 쫄몹이 있으면 산 적 중 무작위 N기(보스 공격 난수열 — 기대값 모드 고정 시드) |
| `"enemies_nearest:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 쫄몹이 있으면 조준 — N ≥ 2면 겨눈 적 → 산 쫄몹(등장 순) → 보스 순 N기 |
| `"enemies_top_atk:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 쫄몹이 있으면 공격력 순(보스 `enemy["atk"]` · 쫄몹은 공격의 atk) |
| `"enemies_top_def:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 쫄몹이 있으면 보스 먼저(쫄몹 방어력 모델 없음) |
| `"enemies_lowest_def:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 쫄몹이 있으면 쫄몹 먼저 |
| `"enemies_lowest_hp:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 쫄몹이 있으면 남은 체력 낮은 쫄몹 순, 그다음 보스 |
| `"enemies_top_hp:N"` | ❌ | ✅ | 최종 최대 체력 최고 적 N기. `_resolve_target()` 일반 `enemies` prefix 처리로 `__enemy__` 센티널 반환. 쫄몹이 있으면 보스 먼저. 마르차나 : 마린 스터디 |
| `"enemies_highest_hp:N"` | ❌ | ✅ | **남은 체력 수치가 가장 높은 적 N기**(2026-09-24 신설·구현). `enemies_lowest_hp:N`의 반대쪽이고, 기준이 최대 체력인 `enemies_top_hp:N`과 다른 축이다. `_resolve_target()` 일반 `enemies` prefix 처리로 `__enemy__` 센티널 반환. 쫄몹이 있으면 **보스 먼저**(고정 시간 sim이라 보스 체력은 줄지 않는다), 그다음 쫄몹을 남은 체력 높은 순(타수 기믹은 남은 타수 — `enemies_lowest_hp`와 같은 `_left`) — `boss_pattern._ranked()`, 검산 15. 2B `연속 공격 2` · 네로 `크고 난폭한 고양이`(미등록) |
| `"target_and_nearby:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 첫 보유자는 일레그 `쇼트 2`(2026-09-20) — 적이 보스 하나면 **1기에게 1회**라 「주변의 적 2기」가 딜을 배로 만들지 않는다 |
| `"boss"` | ❌ | ✅ | 보스(타겟) 1기 — 「타겟에게」와 적 전체 공격 뒤 「대상이 타겟이라면 동일 적 대상에게」. 쫄몹이 있어도 보스다. 쫄몹이 없으면 `__enemy__` 센티널이라 `target`과 같다. 디젤 : 윈터 스위츠 `노래할게요! 3`·`라라라♬ 3` · 나유타 `위선 6` · 팬텀 `비기 괴도 난무 2`(애장품 3) |
| `"enemies_with_buff:버프명"` | ❌ | ✅ | `__enemy__` 센티널 반환. 쫄몹이 있으면 그 효과가 붙은 적(`bm.enemy_has_state`), 없으면 보스 |
| `"enemies_code:코드"` | ❌ | ✅ | `__enemy__` 센티널 반환. 코드 필터 무시 — 쫄몹 코드가 없어 쫄몹이 있어도 보스 |
| `"enemies_lowest_hp_code:코드:N"` | ❌ | ✅ | `__enemy__` 센티널 반환. 코드 필터 무시 — 쫄몹이 있어도 보스 |
| `"all_projectiles"` | ❌ | ❌ | 발사체 모델 없음. 빈 리스트 반환 |
| `"self_cover"` | ❌ | ❌ | 미구현. 빈 리스트 반환. **첫 보유자는 베이 `치얼업 투게더`**(2026-09-20 파싱) — 「자신의 엄폐물에게」. 담체가 니케가 아니라 엄폐물이라 `_resolve_target()`이 캐릭터 이름을 돌려주는 규약 밖이다. 짝이 되는 stat(`received_dmg_split`)이 규격 미정이라 **둘을 같이 보류한다**(유저 결정 2026-09-20) — 지금은 빈 리스트를 돌려주므로 `치얼업 투게더`가 한 번도 활성화되지 않는다 |
| `"allies_broken_cover_random:N"` | ❌ | ✅ | **엄폐물이 파괴된** 아군 중 무작위 N기(2026-09-20 구현). `allies_random:N`(자신 제외 무작위)과 달리 **시전자를 빼지 않고**, `bm.cover_alive()`가 거짓인 아군만 후보로 둔다 — `allies_lowest_cover_hp:N`이 부서진 엄폐물을 후보에서 빼는 것과 정확히 반대다. 지연 resolve 대상이 아니다(「지금 부서져 있는가」가 곧 부여 시점 판정). 후보가 N보다 적으면 있는 만큼, 0기면 무발동. 엄폐물은 보스 공격 패턴이 있을 때만 부서지므로 기본 경로에서는 늘 0기다. 비스킷 `산책 훈련` |
| `"allies_lowest_cover_hp:N"` | ❌ | ✅ | 엄폐물 체력 **남은 비율** 오름차순(동률은 스쿼드 순서). 부서진 엄폐물은 되살아나지 않으므로 후보에서 뺀다. 리타 `볼트 부스트` |
| `"same_target:[name]"` | ❌ | ❌ | 연계 대상 명시 형태. 미구현 |
| `"allies_lowest_atk_burst3:N"` 형 확장 | ✅ | — | 새 스탯 비교 기반 target 추가 시 `_LAZY_RESOLVE_PREFIXES`에 등록 필수 |

---

## 빠른 참조: stat 분류 (신규 추가 시 판단용)

| 분류 | stat 예시 | buff_manager | damage.py |
|------|----------|-------------|-----------|
| DealForm ①에 영향 | `normal_atk_dmg_pct` | ✅ 추가 | ✅ `_factor1` |
| DealForm ②에 영향 | `atk_pct`, `atk_flat`, `def_ignore_pct`, `enemy_def_down_pct`, `enemy_def_down_flat` | ✅ 추가 | ✅ `_factor2` |
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

> **`target`이 배열이면 lazy resolve가 걸리지 않는다.** `_apply_buff`의 판정이
> `isinstance(raw_target, str) and raw_target.startswith(_LAZY_RESOLVE_PREFIXES)`라서
> 배열은 `_resolve_target()`이 그 자리에서 평탄화한다 — 위 표에서 ✅인 대상도 배열 안에
> 들어가면 **부여 시점 확정**이다. 복합 대상(`자신과 X에게`)은 규칙상 배열이므로
> (`PARSING.md` §5) 이 조합이 실제로 생긴다: 아니스 : 스타 `스타더스트 3`
> (`["self", "allies_below_def"]`), 소다 : 트윙클링 바니 `럭키 골든 칩 2`
> (`["self", "allies_top_atk_excl:1"]`). 수령자가 갈리는지는 스냅샷 L2의 `targets`로 본다.

---


## 회귀 테스트

계산기 로직을 고친 뒤에는 `python -m runner.snapshot`으로 회귀를 확인한다.

**운영 기준·스쿼드 스펙·diff 읽는 법은 전부 `docs/HARNESS.md`에 있다.**
이 문서에는 회귀 관련 규칙을 중복해서 적지 않는다.
