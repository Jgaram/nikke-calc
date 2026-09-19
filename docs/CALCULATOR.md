# calculator/ 데이터 흐름

`simulate(squad, config, enemy)` 호출 → `SimResult` 반환까지 전체 흐름.

---

## 1. 진입점

```
runner/sim.py                     (CLI 단발 시뮬)
runner/snapshot.py                (회귀 하네스)
  └─ runner/spec.py   기본 육성 스펙 + 캐릭터별 레이어 → 캐릭터 dict
       └─ simulate(squad, config, enemy, seed)   ← timeline.py
```

딜량 보고서는 이 레포에 없다 — 별도 웹앱 레포가 같은 `spec.py`를 거쳐 `simulate()`를
부른다. 의존은 한 방향이라 이쪽에서 그쪽을 참조하지 않는다.

`squad`은 캐릭터 인스턴스 dict 목록. 각 캐릭터는 `name`, `level`, `equipment`, `equip_skills`,
`cube`, `console`, `collection_stage`, `control` 등을 포함한다. 이 dict를 만드는 건 러너 쪽
`runner/spec.py`이고(정본: `HARNESS.md §기본 스펙`), `timeline.DEFAULT_CHAR`는 호출자가
키를 빠뜨렸을 때의 **최소 폴백**일 뿐이다 — 장비 옵션·컨트롤 기본값은 거기 없다.

`equip_skills`의 값은 **스칼라(합산 퍼센트) 또는 줄별 퍼센트 리스트**다. 최대 장탄·차지 속도는
옵션 단계마다 따로 반올림되므로(`GAMEPLAY.md §무기 메카닉`) 단계가 섞인 장비는 리스트로 적어야
한다 — 같은 값(= 같은 레벨)끼리 묶여 한 그룹이 된다. 줄이 전부 같은 레벨이면 스칼라와 결과가
같으므로 기본 스펙은 스칼라 그대로다.

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

`level_stats.json`은 **`등급_클래스_무기유형`**으로 갈린다(예: `SSR_화력형_AR`). 등급이 키에
들어가는 이유는 SR·R 캐릭터의 곡선이 따로 있기 때문이다 — SR 라피의 레벨200 공격력은
18,983이지 SSR의 21,091이 아니다. 표는 손으로 고치지 않고 `python scraper/cdn_fetch.py`가
CDN roledata의 `character_level_{attack,defence,hp}_list`에서 다시 만든다(2026-08-28).
자기 조합의 곡선을 따르지 않는 캐릭터는 `_exceptions`에 실제로 따르는 조합 키로 적히며,
현재 등재는 하란 한 명이다(SR을 들었는데 방어력 곡선이 AR 쪽).

---

## 3. 메인 루프 (1/60초 스텝)

```
for t in 0, DT, 2·DT, ..., duration:
  boss.begin_frame(t, enemy)          ← 보스 패턴이 있을 때만. 전이 확정 → 적 상태 기록
  보스 효과 해제·부착                   ← 보스 패턴이 있을 때만. 닫히거나 해제된 패턴의 효과를 풀고 열린 보스 버프를 적에게
  state["enemy_count"]                ← summon 패턴이 있을 때만. 적 수(보스 1 + 산 쫄몹)를 프레임 맨 앞에 정한다
  bm.tick(t)                          ← 주기 대미지 → 만료 버프 제거 → every:Ns 쿨타임
  bm.sync_cover_hp(니케마다)            ← 보스 패턴이 있을 때만. 엄폐물 최대 체력 배율의 증감을 현재 체력에
  보스 이벤트 notify                   ← `part_break_interval`의 `event:part_destroy`와 같은 자리
  bm.drop_enemies(boss.gone)          ← 사라진 쫄몹을 적 효과의 대상에서 지운다. 사망 통지 뒤
  보스 지속 피해 틱 (_boss_dots)        ← 보스가 건 `dot` 디버프. 발보다 먼저
  보스 공격·디버프 (_boss_attack · _boss_debuff) ← 이 프레임의 attack·debuff 발(패턴 선언 순). 층·피격 이벤트·전투불능
  _dot_events 배출                     ← bm.tick이 낳은 damage 효과의 히트를 여기서 수확
  burst_ctrl.tick(t, bm, state)       ← 버스트 사이클 관리 (버스트 딜도 히트로 나온다)
  _pump_squad_seq · _arbitrate_control ← 스쿼드 시퀀스 → 조작자(카메라) 결정 (docs/CONTROL.md §판정 자리)
  _resolve_aims(t)                    ← 패턴 모드. 카메라가 정해진 뒤 니케마다 겨눌 곳 (§조준 · 좌표 모드면 조준점도)
  for each CharState:
    hits = cs.tick(t, bm, enemy, cfg) ← 발사/차지/재장전 처리
  (히트마다 _land(): [표적 히트(`target` — 좌표 모드의 착탄 · 좌표 off의 겨눈 발)면 _land_target: 보스 게이트 →
   hit_target → 파츠면 총딜 · 저지원이면 총딜 밖 + 흡혈] / [쫄몹이 있으면 boss.route로 적마다 나눔] →
   보스 몫: 보스 게이트 → result.hits 누적 + char_total 가산 + 흡혈 → [닿은 reach 파츠마다 파츠 히트도 같은 셋 ·
   닿은 reach 저지원은 총딜 밖 + 흡혈] /
   쫄몹 몫: boss.hit_add + 흡혈)
```

**한 프레임 안의 이 순서가 곧 명세다.** `bm.tick`이 만료 정리보다 주기 대미지를 먼저
처리하는 것, DoT 히트를 버스트·발사보다 앞에서 수확하는 것 모두 결과를 바꾼다 —
스냅샷 L3(순서)가 지키는 대상이 이것이다.

### 보스 패턴 (`enemy["patterns"]`)

보스를 스칼라 셋이 아니라 시간에 따라 이어지는 패턴들로 적는 자리다. **포맷·검사 규칙의
정본은 `calculator/boss_pattern.py` 모듈 docstring**이고, 여기는 루프에 끼는 자리만 적는다.

**보스 모드는 둘이고, 패턴 모드 안에 좌표 스위치가 있다**(유저 결정 2026-09-19 — 판정은 `boss_pattern.boss_mode()` 한 곳):

| 모드 | 적 dict | 보스가 하는 일 |
|---|---|---|
| 간단 모드 | `patterns` 없음 | 기본 스탯(`def`·`code`·`core_px`·`has_parts`·적정거리)이 전투 내내 고정. 파츠 파괴는 `part_break_interval` 주기로 흉내 낸다. 하네스 baseline 전부, 프리셋 이름만 준 `--boss` |
| 패턴 모드 · 좌표 off | `patterns` 있음 | 패턴이 적 상태를 시간에 따라 덮고 표적·공격·디버프·쫄몹을 연다. 무엇을 겨누는가는 조준 순서(아래 §조준), 곁에 무엇이 닿는가는 표적의 `reach`로 어림한다. 실제 보스 스크립트 |
| 패턴 모드 · 좌표 on | `patterns` + `coord` | 같은 패턴에서 「어디에 맞는가」만 화면 좌표·에임·탄 분포로 푼다(아래 §좌표 모드). 「좌표 모드」는 이 스위치가 켜진 상태의 이름이다 |

모드마다 제 칸이 있어 남의 칸을 적으면 즉시 실패한다 — 좌표(`coord`)는 패턴 모드의 스위치라 패턴이 없으면
다룰 표적·코어가 없고, 파츠 파괴 주기(`part_break_interval`)는 간단 모드의 칸이라 패턴과 같이 적을 수 없다.

- **러너 입력**: `runner/sim.py --boss`가 프리셋 이름이나 스크립트 파일을 받는다. 프리셋 참조
  (`preset`·`skill`·`part`) 전개는 `runner/boss.py`가 하고(정본은 그 모듈 docstring), calculator/는
  `data/boss_presets.json`을 읽지 않는다. **스크립트는 `data/boss_scripts/`에 둔다** — 그 보스의 자료
  출처·어림값·미확인은 `docs/bosses/<보스 이름>.md`가 맡고(스크립트가 수치의 정본이다), 만드는 절차는
  skill `boss-script`다.
- **간단 모드는 `BossScript`를 만들지 않는다.** 루프의 보스 자리가 전부 `boss is None`으로
  건너뛰어 이 기능 이전과 계산이 한 자리도 같다. 하네스 baseline이 전부 이 경로다.
- 패턴이 바꾸는 적 상태는 `def`·`core_px`·`has_parts`·`optimal_range_weapons`·`distance` 다섯뿐이다
  (`boss_pattern.OVERLAY_FIELDS`). 사격·조건 판정이 매번 같은 `enemy` dict를 다시 읽으므로
  dict를 갈아끼우지 않고 **값만 바꾼다.** `BurstController.enemy_def`가 조회 시점에 읽는
  property인 것도 그래서다 — 캐시하면 버스트 딜만 옛 방어력으로 계산된다.
- **t=0의 상태 확정은 `bm.battle_start()`보다 앞이다.** 전투 시작 효과도 기본 상태를 읽으면
  안 된다. 그때 나온 이벤트는 루프 첫 프레임의 통지 자리에서 나간다.
- **딜 게이트는 결과 자리에 있다**(`_land()` → `BossScript.gate()`). 사라짐은 평타
  (`sim_result._is_normal`)만 빼고, 속성보호막은 캐스터 단위로 로스터 코드 상성이거나
  `element_code_override` 버프로 우월할 때만 통과시킨다. **거른 뒤에 표적에 넣으므로** 막힌
  딜은 표적도 못 깎는다. 발사 시점에 이미 나간 트리거(`hit_count`·`core_hit` …)는 되돌리지 않는다.
- **사라짐은 평타 몫의 버스트 게이지만 뺀다.** 충전 창(`burst_gauge_charging`)은 건드리지 않고
  `CharState._weapon_gauge_lands()`가 무기 사격의 가산 자리에서만 거른다 — 스킬 게이지는
  사라진 동안에도 찬다(`docs/mechanics/버스트 게이지.md`).
- **속성보호막은 거꾸로 스킬 대미지 몫의 게이지만 뺀다.** 막힌 캐스터의 스킬 대미지 히트(무기 변경
  모드의 스킬 대미지 사격 포함)는 게이지를 안 채우고, 무기 사격과 게이지 충전 효과는 채운다
  (`BossScript.shield_blocks()` — 스킬 대미지 핸들러와 `_weapon_gauge_lands()`가 묻는다).
- 표적 파괴 이벤트(`emit_on_destroy`)는 `hit_target()`에서 바로 쏘지 않고 **다음 프레임 통지
  자리**에서 나간다. `_dot_events`를 다음 프레임 시작에 수거하는 것과 같은 1프레임 규약이다.
- **좌표 off의 파츠 다중 타격**(parts 표적의 `reach` — 단계 표와 규칙의 정본은 `calculator/boss_pattern.py`
  §파츠 다중 타격). 발을 만드는 세 자리(`CharState._fire`·`CharState._charge_fire`·스킬 대미지 `_handle_damage_eff`)가 `_reach_hit()`로 그
  발이 닿는 단계 상한(`hit_reach`)과 파츠 몫을 `HitEvent.reach`·`part_damage`에 싣는다. 파츠 몫은 같은 발을 코어
  없이 `is_part`로 다시 산정한 값이고, 크리는 본체 판정을 그대로 쓴다(hit_type `crit_override` — 난수를 안 먹는다).
  닿을 파츠가 있는지는 `_apply()`가 프레임 맨 앞에 적 dict에 적는 `_part_reach`(산 reach 파츠의 최저 단계)로 묻는다
  — 패턴이 바꾸는 적 상태 넷과 달리 파생값이고, 0이면 다시 산정하지 않는다. `_land_boss()`는 `gate()`를 지난
  발마다 `boss.part_hits()`가 돌려준 파츠마다 `HitEvent(part=파츠 이름)`를 `result.hits`·`char_total`에 더한다 —
  **총딜에 들어간다.** 그 발이 겨눈 표적(`HitEvent.aimed`, §조준)은 빠진다(한 발에 한 번). reach 파츠가 살아
  있는 동안 `hits_parts` 스킬의 본체 히트는 `is_part`를 내려놓는다 — 파츠 몫은 파츠 히트가 받는다.
- **저지원 다중 타격**(interrupt 표적의 `reach` 1~4 — 같은 절). `_reach_hit()`가 저지원 쪽 단계 상한과 몫을
  `HitEvent.interrupt_reach`·`interrupt_damage`에 따로 싣는다 — 단계 상한은 `hits_parts`를 빼고 재고(「파츠 포함」
  전체기는 저지원에 안 닿는다), 몫은 코어도 파츠 판정도 없이 다시 산정한다. 적 dict의 `_interrupt_reach`로 닿을
  저지원이 있는지 묻는다. `boss.interrupt_hits()`가 저지원 체력에 넣으면서 시전자별로 `interrupt_dealt`에 쌓고,
  `_land_boss()`는 흡혈만 붙인다 — **총딜 밖**이다(유저 결정 2026-09-19, 좌표 모드 저지원 히트와 같은 칸).
- `enemy["part_break_interval"]`은 간단 모드의 칸이다(2026-09-19까지는 config에 있었다 — 옛 자리에 적으면
  거절한다). **패턴과 같이 적으면 거절하고**(`boss_mode`), 패턴 모드의 `event:part_destroy`는 표적이 실제로
  깨질 때만 나간다.
- **보스 버프의 받는 대미지**(`buff.enemy.received_dmg_pct`)는 열린 동안 적에게 붙은 효과로 들어가
  니케 딜의 ⑥에 합산된다(`boss.enemy_effects` → `bm.apply_boss_effect`). 열린 `buff` 패턴 하나가 이로운
  효과 하나다 — 니케의 「적 이로운 효과 해제 N개」(`enemy_buff_cleanse`)가 나중에 두른 것부터 N개를 끄고
  (`BossScript.dispel`, `irremovable`은 건너뜀), 꺼진 패턴은 방어력 오버레이와 받는 대미지를 둘 다 잃는다.
  구간은 그대로 간다. 해제는 니케 스킬 발동 도중이라 **다음 프레임 맨 앞에** 반영한다 — 적 상태를 프레임
  안에서 바꾸지 않는다. 패턴이 닫히거나 해제된 효과는 `boss.released` → `bm.release_boss_effects`가 푼다.

#### 조준 (`boss.aim_target` → `_resolve_aims` → `boss.aim_of` · `state["aim"]`)

무엇을 겨누는가를 좌표 on/off가 **같은 규칙**으로 정한다(유저 결정 2026-09-19 — 손으로 적던 조준 비율 `share`를
대신한다. 스크립트에 적으면 거절한다). 정본은 `calculator/boss_pattern.py` §조준, 조작 쪽은 `docs/CONTROL.md` §에임이다.

- **순서**: 카메라 니케(레이어 2 — `config["aim_interrupt"]`)는 산 저지원 → 쫄몹 → 벌칙 파츠 → 본체, 나머지(레이어 1
  카메라 포함)는 쫄몹 → 본체다. 풀버스트·[사격 집중] 중이면 카메라 니케의 조준을 따르고, 좌표 모드의 손 에임이 가장
  앞이다. `_resolve_aims()`가 조율 **뒤** 니케마다 정해 `state["aim"]`(조준점, 겨눈 곳)과 `boss.aim_of`에 적는다 —
  겨눌 표적·쫄몹이 없는 스크립트는 건너뛴다. 겨눈 곳이 바뀌면 `aim_log`에 적는다(보고 [에임]).
- **벌칙 파츠**(`boss_pattern.penalty_parts`): 못 깨면 실패 분기가 오는 parts의 깰 수 있는 표적. 그 밖의 파츠는
  겨누지 않는다 — reach·관통·폭발·전체기로만 맞는다.
- **좌표 off 사격**: 표적을 겨눈 발은 `CharState._stage_pellet()`이 그 표적에 통째로 떨어뜨린다(`HitEvent.target` —
  코어 없음 · 파츠면 파츠 대미지 ▲). 관통·폭발 탄은 좌표 모드처럼 본체(코어 없이)와 곁의 reach 표적에도 닿고, 겨눈
  표적은 `extra` 히트다 — 본체 히트의 `HitEvent.aimed`로 다중 타격에서 뺀다(한 발에 한 번). 회계는 좌표 모드 표적
  히트와 같은 `_land_target()`이다(파츠 총딜, 저지원 총딜 밖).
- **스킬**은 쫄몹을 겨눈 니케면 그 쫄몹, 아니면 본체다 — 저지원·파츠를 겨눠도 스킬은 본체다.
- 쫄몹이 나온 프레임의 `enemy_spawn` 트리거는 조준을 정하기 전에 나간다. 그래서 쫄몹이 살아 있는데 조준이
  본체("")·미정이면 쫄몹으로 읽는다(`BossScript._aim_enemy`) — 쫄몹이 있는데 본체를 겨누는 순서는 없다.

#### 쫄몹 (`summon` 패턴 → `boss.route` · `bm.enemy_resolver`)

**좌표가 없는 모드다**(유저 결정 2026-09-16). 포맷과 대상 규칙표의 정본은 `calculator/boss_pattern.py` §쫄몹이고,
여기는 엔진에 끼는 자리만 적는다. summon 패턴이 없으면 아래 자리가 전부 건너뛰어 적은 보스 하나다.

- **적 id**: 보스는 센티널 `__enemy__`, 쫄몹은 `__enemy__:<패턴 id>#<번호>`다(`buff_manager._is_enemy`). 적인지
  묻는 자리(보호막·`stat_applied` 통지에서 적 빼기 · 적에게 건 도발 · `self_stack_above` · 보스 효과 부착)는 둘 다
  적으로 본다.
- **딜**: 딜은 지금처럼 **보스 기준으로 한 번 산정**하고, 히트에 효과의 대상 규칙을 싣는다(`HitEvent.rule` ·
  분할이면 `split` · 지속 대미지 틱이면 효과가 붙은 적 `to`). 쫄몹이 살아 있으면 `_land`가 `boss.route`로
  (적 id, 가중치)로 나누고, **보스 몫만** 보스 게이트(사라짐·속성보호막)·파츠 표적·총딜로 간다. 쫄몹 몫은
  `boss.hit_add`가 쫄몹 체력에 넣는다 — **총딜·캐릭터별 딜에 없고**(레이드 점수는 보스 딜, 유저 결정)
  `SimResult.add_char_total`로 따로 싣는다. 쫄몹 방어력·쫄몹에 붙은 효과는 쫄몹 몫에 안 들어간다(근사).
  체력을 넘친 딜과 이미 사라진 쫄몹에 간 딜은 버린다(`add_overkill`).
- **타수 기믹**: `hit_hp`를 적은 무리는 딜이 얼마든 **한 발에 1**씩만 깎이고 그 수를 채우면 죽는다.
  `hp`와 같이 적으면 `hit_hp_after`초 뒤부터 바뀌고(마더웨일식 소환수), `hit_hp`만 적으면 등장부터다.
  한 발은 **HitEvent 하나**라 산탄 한 알·지속 대미지 한 틱도 1이고(⬜ 인게임 미확인), 삼켜진 나머지 딜은
  넘친 딜과 같은 자리로 간다. `enemies_lowest_hp`는 그 무리를 남은 타수로 줄 세운다(체력과 단위가 달라
  무리끼리 섞이면 뜻이 약하다).
- **조준**: 무기 사격과 조준 규칙 스킬(`target`·「(조준선에) 가장 가까운 적」)은 **시전자가 겨눈 적** 하나에 통째로
  간다(§조준) — 쫄몹을 겨눴으면 그때 먼저 나온 산 쫄몹, 표적·본체를 겨눴으면 보스. N ≥ 2면 겨눈 적 → 산 쫄몹 → 보스.
  「타겟에게」(`boss`)는 겨눈 적과 무관하게 보스다. 광역은 적마다 온전히, 분할 대미지는 맞은 적 수로 나눈다.
- **적 효과**: `bm.enemy_resolver`(= `boss.resolve_enemies`)가 적 대상 문자열을 적 id로 풀어 버프·디버프·지속
  대미지가 **적마다 따로** 붙는다(유저 결정). 조준 규칙은 시전자가 겨눈 적 1기다. `get_buffs` 캐시 키에 대상이
  들어가 보스·쫄몹이 받는 대미지를 따로 센다. 사라진 쫄몹은 사망 통지 **뒤에** `bm.drop_enemies`가 모든 효과의
  대상에서 지운다 — 그 통지의 「[상태] 적 사망 시」가 죽은 쫄몹의 상태를 아직 본다. `target_state:`는 조건에
  맞는 적 문맥이 없어 어느 적에게든 붙어 있으면 참이다(근사).
- **이벤트·적 수**: 등장하면 쫄몹마다 `event:enemy_spawn`(열린 프레임), 처치하면 쫄몹마다 `enemy_death`(표적
  파괴와 같이 다음 프레임), 자폭하면 쏜 프레임에 `enemy_death`, 패턴이 닫혀 퇴장하면 아무것도 안 쏜다(유저 확인).
  `enemy_count_*` 조건은 `state["enemy_count"]`(보스 1 + 산 쫄몹, 프레임 맨 앞)를 읽는다 — summon이 없으면 칸이
  없고 1로 본다.
- **쫄몹 공격**: 등장 뒤 `attack_at`초에 산 쫄몹마다 `boss.attacks`에 `source`(쫄몹 이름)를 달고 들어가 보스
  공격과 같은 `_boss_attack`을 탄다. 공격력은 spec에 필수다(보스 공격력으로 떨어지지 않는다). 결과는
  `squad_hits`의 `by`. 무작위 적 대상(`enemies_random:N`)은 보스 공격과 같은 난수열이다(기대값 모드 고정 시드).

#### 좌표 모드 (`enemy["coord"]` → `boss.geom` · `_resolve_aims` · `CharState._coord_pellet`)

패턴 모드에서 적에 `coord` 블록이 있으면(좌표 on) 표적을 **화면 좌표**로 적고, 「어디에 맞는가」를 reach 대신
조준점과 탄 분포로 푼다(유저 결정 2026-09-18). 포맷·규칙의 정본은 `calculator/boss_pattern.py` §좌표 모드, 기하(모양·확률·착탄점)는
`calculator/aim.py`, 조작 쪽은 `docs/CONTROL.md` §에임이다. 여기는 엔진에 끼는 자리만 적는다.

- **기하 스냅샷**: `BossScript._apply_geom()`이 프레임 맨 앞에 산 표적(앞 → 뒤)·코어·자동 에임을 `Geometry`로 묶어
  `enemy["_geom"]`(`GEOM_KEY`)에 싣는다. 산 집합이 그대로면 **같은 객체**를 둬 착탄 확률 캐시(조준점·탄착군·원
  반지름이 키)를 살린다. 좌표 off는 이 칸이 없고, 사격·스킬은 그걸로 경로를 가른다.
- **조준점**: `_resolve_aims()`(§조준)가 정한 겨눌 곳의 조준점을 `state["aim"]`에 싣는다 — 표적이면 그 중심, 쫄몹·
  본체면 자동 에임(쫄몹은 좌표가 없어 자동 에임 자리로 쏘고, 표적에 떨어지지 않은 히트만 쫄몹에 간다).
- **사격**: `_fire`·`_charge_fire`가 펠릿마다 `_coord_pellet()`을 부른다. 탄 분포는 코어 히트 모델을 2D로 편 것이다
  (조준점 중심, 반지름 누적 확률 (ρ/R)^2.55). 기대값 모드는 `Geometry.landing()`의 확률로 나눈 히트(본체·코어 혼합 1 +
  표적마다), 난수 모드는 착탄점을 뽑는다. 보통 탄은 가장 앞 표적에 막히고, 관통·폭발 탄은 본체를 늘 맞히며 원 안의
  표적마다 `extra` 히트를 더한다(본체의 크리 판정을 쓰고 트리거·게이지를 따로 안 낸다). 명중 트리거(파츠/본체 명중)는
  착탄점의 가장 앞 물체로 낸다. 폭발·관통 원의 반지름은 `CharState._area_radius()`다.
- **스킬**: 본체 히트는 종전 그대로이고, `_coord_skill_hits()`가 표적 몫을 더한다 — 「파츠 포함」 전체기는 산 파츠 전부,
  관통 대미지·발사체 폭발 스킬은 시전자 조준점에서 원 안의 표적(탄 분포 없이). `hits_parts`의 본체 히트는 `is_part`를
  내려놓는다.
- **회계**: 표적 히트(`HitEvent.target`)는 쫄몹으로 나누지 않고 `_land_target()`으로 간다 — 게이트(`boss.gate`)를 지나면
  `boss.hit_target()`이 표적 체력에 넣는다. **파츠 히트는 총딜**(초과분 포함), **저지원 히트는 총딜 밖**
  (`boss.interrupt_dealt` → `SimResult.interrupt_char_total`, 유저 결정)이고 흡혈은 둘 다 받는다. 본체 히트는 표적에
  안 들어간다.
- **등가**: 좌표를 하나도 안 준 좌표 모드(표적 없음, 코어는 원점)는 조준점 중심 코어만 남아 종전 식 그대로다 —
  같은 패턴의 좌표 off와 기대값·난수(같은 시드) 모두 원 단위로 같다. 착탄점을 뽑을 때 각도가 필요 없으면 난수를 하나만 먹는다.

#### 적정거리 (`enemy["distance"]` · `move.distance` → `buff_manager.in_optimal_range`)

보스 거리가 있으면 적정거리 판정이 무기군 목록(`optimal_range_weapons`) 대신 니케마다 적정 구간(CDN bonusrange —
`parsed_nikke` `optimal_range`, 적정 최대·최소 사거리 ▲ 반영)과 거리를 비교한다. 판정 자리 셋 — 평타 ③ +30%
(`_fire`·`_charge_fire`, 풀차지 트리거 뒤의 이 발 버프로)·스킬의 일반 공격 판정·조건 `optimal_range` — 이 모두 같은
함수를 본다. 거리가 없으면 종전 목록이라 기본 경로가 안 흔들린다. 거리와 목록을 같이 적으면(적 기본값·move 패턴
어디서든) 즉시 실패한다.

#### 보스 공격 (`attack` 패턴 → `timeline._boss_attack`)

`BossScript`는 「언제 몇 발이 어떤 대상 규칙으로 나가는가」까지만 알고(`boss.attacks`), 대상·피해·
층은 니케 상태가 있는 timeline이 정한다. 자리는 **통지 자리 바로 뒤**다 — 피격이 낳는 버프가 만료
정리 뒤에 붙어야 같은 프레임에 지워지지 않는다.

- **대상** (`_attack_targets`): `all`은 산 니케 전원이다. 그 밖의 공격은 **도발 중인 니케가 자리를
  먼저 가져가고**(`bm.taunters()`) 남은 자리를 원래 규칙으로 채운다 — `slot:`은 적힌 자리 순서로,
  `random:N`·`top_atk:N`은 은신이 아닌 산 니케에서(전원 은신이면 은신 무시). 공격 spec
  `ignore_taunt`면 도발을 보지 않는다. 무작위는 보스 전용 `random.Random(seed)`을 쓴다 — 전역 난수를
  같이 쓰면 공격 하나로 크리 판정 순서가 통째로 밀린다. 기대값 모드는 고정 시드다(§기대값 모드).
- **피해** = `max((보스 공격력 − 니케 최종 방어력) × 계수% × (100% + 받는 피해 증감%), 1)` — 니케가
  적을 때리는 식과 같은 모양이다(유저 결정). 크리 없음. 보스 공격력 `enemy["atk"]` 기본값은 솔로 레이드
  보스 Lv 400이고(`DEFAULT_BOSS_ATK`, `docs/DATA_VERIFY.md` §레이드 보스 스탯), 엄폐물 체력 기본값
  `config["cover_hp"]`은 **임의값**이다(엄폐물 레벨 기준 미확인). 엄폐물 최대 체력은 그 위에
  `cover_hp_pct`를 얹은 값이다(`bm.cover_max_hp()` — 비례 합과 시전자 최대 체력 비례 항, 늘면 현재
  체력도 같이 찬다).
- **층** (유저 확인):

  | | 보호막 | 엄폐물 (엄폐 중·엄폐물 생존) | 체력 |
  |---|---|---|---|
  | 비관통 | 있으면 이 층만 받는다 | 보호막이 없을 때만 받는다 | 앞 두 층이 없을 때만 |
  | 관통 | 같은 피해를 받는다 | 같은 피해를 받는다 | 같은 피해를 받는다 |

  **비관통은 앞 층이 깨져도 남은 피해가 넘어가지 않는다.** 보호막은 각자 따로 작동한다 — 비관통은
  나중에 생긴 하나만 맞고(순서는 잠정), 관통은 살아 있는 보호막 전부가 같은 피해를 받는다. 엄폐물이 부서지면 엄폐해도 막아 주지 않고 재생성되지 않는다 — 부서지는 순간
  `bm.break_cover()`가 집계 캐시를 비워 `self_cover_alive` 조건 버프가 같은 프레임부터 꺼진다.
  「엄폐 중」은 엄폐 구간이거나 재장전 중이다(`CharState.in_cover`) — `cover_disabled`가 켜져 있으면
  둘 다 아니고, 엄폐 구간 중에 켜지면 그 자리에서 자세가 풀린다(`CharState._drop_blocked_cover`). 무적은 체력 피해만 0으로 한다. 불굴(`undying`)은 무적 다음에 보며, 체력이 0이 될
  발을 체력 1을 남기고 받는다(쓰러지지 않았으니 임계 이벤트는 나간다).
- **다음 보호막 체력 ▲**(`next_shield_hp_pct`)은 보호막이 대상에게 적용되는 순간 소모되고,
  **받는 회복량 ▲**(`heal_received_pct`)은 힐 instant·흡혈의 회복량에 곱한다(`bm.heal_received_mult`).
- **이벤트 순서**: 체력 반영 → `sync_hp`(임계 이벤트) → `received_hit` → `event:cover_hit` →
  전투불능 판정. **체력이 0에 닿은 발은 임계 이벤트를 쏘지 않고** 곧바로 전투불능이다.
- **전투불능** (`bm.knock_down` → `CharState.on_down` → 로그 → `bm.notify_down`): 그 니케가 **받은**
  버프는 영구 버프까지 전부 사라지고(`persist_on_revive` 제외) 준 버프는 남는다. 개인 게이지·스택과
  발동 횟수 카운터(`_event_counts` — 「N번째 버스트 시」 같은 회수별 효과)도 0으로 돌아간다. 스쿼드
  공용 카운터와 `max_trigger`는 그대로다. 쓰러진 동안은 사격·스킬 발동(`_notify` 게이트, `event:self_down`만
  예외)·버스트 후보·아군 대상 해석에서 전부 빠진다. `event:self_down`·`event:ally_down`은 **정리가
  끝난 뒤에** 나간다 — 같은 호출 안에서 부활이 나오면 뒤늦은 정리가 부활한 니케를 도로 멈춰 세운다.
- **부활** (`revive` → `bm.revive` → `CharState.on_revive`): 쓰러질 때 잃은 영구 버프 중 **패시브**
  (`passive`·`battle_start` 타이밍 — 장비·큐브·소장품·지속 패시브)를 다시 붙인다(`bm._reapply_passives`).
  다른 아군에게 아직 걸린 효과는 새로 발동하지 않고 대상에 되돌린다(재발동하면 나머지 아군에게 한 번 더
  걸린다). 부활 체력 %는 재적용 뒤의 최대 체력 기준이다. 만탄으로 바로 싸우고 버스트 쿨은 이어간다.
- 결과는 `SimResult.squad_hits`(발 단위 층별 피해)와 `boss_log`의 `down`·`revive`·`cover_break`.
  인게임 미확인 판단은 `docs/DATA_VERIFY.md` §보스 → 니케 피해.

#### 보스 디버프 (`debuff` 패턴 · attack `debuffs` → `timeline._boss_debuff` · `_boss_dots`)

포맷의 정본은 `calculator/boss_pattern.py` §디버프다. 여기는 엔진에 들어가는 자리만 적는다.

- **주입**: 보스 디버프는 buff_manager의 해로운 효과(`polarity: harmful`, 해제 불가면 `harmful_irremovable`,
  시전자 `__enemy__`)다. 초기화 때 확정되는 `_effects`에는 넣지 않는다 — 트리거가 아니라 보스 스크립트가
  시각·대상을 정해 거는 효과라 `bm.apply_boss_effect`가 `_activate(targets=...)`로 바로 넣는다. **니케마다
  따로 붙는다** — 다시 걸리면 그 니케의 것만 중첩·갱신된다. 효과 dict는 패턴마다 한 번 만들어 계속 같은
  객체를 넘긴다(`_activate`가 재발동을 dict 동일성으로 알아본다).
- 그래서 **니케 스킬이 건 해로운 효과와 같은 경로**를 탄다: 면역(`debuff_immune`·`debuff_immune:[이름]` —
  `harmful_irremovable`은 `_activate` 규약대로 거르지 않는다) · 해제(`debuff_cleanse`) · 중첩 감소
  (`debuff_stack_remove`) · 차지 속도 감소 효과 면역 · 전투불능 소멸. 부활은 되붙이지 않는다(패시브가 아니다).
- **대상**: `debuff` 패턴은 공격과 같은 `_attack_targets`로 고른다 — 도발·은신까지 같다(유저 확인). 공격에
  딸린 `debuffs`는 **체력에 피해가 들어간 발만** 건다(유저 확인) — 보호막·엄폐물이 받았거나 무적이면 안 걸리고,
  그 발로 쓰러졌으면 안 건다. 자리는 `sync_hp` 뒤 `received_hit` 앞이다.
- **수명**: `duration`(초)이 있으면 그만큼, 없으면 **건 패턴이 닫힐 때** 풀린다(`boss.released`). 전투가
  끝나서 닫힐 때는 풀지 않는다.
- **읽히는 자리** — `boss_pattern.DEBUFF_STATS`가 닫힌 집합이고, 해로운 쪽 부호만 받는다.

  | stat | 자리 |
  |---|---|
  | `atk_pct` · `atk_dmg_pct` · `accuracy_pct` | `get_buffs` (공격력은 `_effective_atk` 순위에도) |
  | `crit_rate` · `crit_dmg` | `get_buffs` — 크리 확률은 0, 크리 대미지 배율 `0.5 + crit_dmg%`는 0에서 자른다 |
  | `attack_speed_pct` · `reload_speed_pct` | 연사 속도 · 재장전 시간 |
  | `charge_speed_pct` · `max_ammo_pct` | 소스별 반올림 목록(`_quant_parts`) — 스킬 버프 소스라 차지 속도 감소 효과 면역이 거른다 |
  | `def_pct` · `received_dmg_pct` | 보스 공격·지속 피해의 피해 산정(`_effective_def` · `incoming_dmg_pct`) |
  | `heal_received_pct` | `heal_received_mult` — 0에서 자른다 |
  | `stun` · `cover_disabled` | `is_stunned` · `has_live_stat` |
  | `dot` | `_boss_dots` (아래) |

- **지속 피해** (`dot`): 틱 피해 = `max((보스 공격력 − 니케 최종 방어력) × coeff% × 중첩 × (100% + 받는 피해
  증감%), 1)` — 공격과 같은 식이고 **체력만 받는다**(보호막·엄폐물 무시, 유저 결정). 무적·불굴·전투불능은
  공격과 같다. 피격 이벤트는 쏘지 않는다(⬜). 첫 틱은 걸린 뒤 interval초, 다시 걸리면 그때부터 다시 재고,
  만료 시각에 떨어지는 틱까지 들어간다 — 니케 지속 대미지와 같은 규약이다. 틱 예약을 버프와 따로 드는
  이유는 만료 시각의 마지막 틱이 올 때 `bm.tick`이 버프를 이미 치웠기 때문이다. 버프가 만료 전에 사라지면
  (해제·전투불능·패턴 종료) 남은 틱을 버린다. **틱이 발보다 먼저**라 interval마다 다시 걸리는 지속 피해도
  틱을 잃지 않는다. 틱은 `squad_hits`에 `source`(디버프 이름)를 달고 들어간다.
- `debuff` 패턴의 발마다 `boss_log`에 `debuff`(붙은 대상·면역 대상)가 남고, 종료 로그에 붙은 건수가 실린다.

---

## 4. CharState.tick() — 발사 판단

```
cs.tick(t)
  ├─ weapon_change 활성?  → _tick_weapon_change()
  ├─ 컨트롤 액션 생산      → _pump_ctrl_seq() / _apply_cover_policy() → _enter_cover()
  ├─ 유한 엄폐 종료?      → 탄이 있으면 재장전 취소, 0발이면 다음 클립 직후 취소 예약
  ├─ 재장전 중?           → 완료 시 _finish_reload()  (클립 무기는 일부만 차면 다음 클립으로)
  ├─ 엄폐 중?             → _tick_cover() → 사격·차징 불가
  ├─ post_reload_delay 중? → 대기
  └─ fire_mode 분기
      ├─ "auto" / "auto_warmup"  → _tick_auto()
      └─ "charge"                → _tick_charge()  (풀차지 후 _hold_ready() 게이트)
```

`infinite_ammo` boolean 버프가 활성인 동안에는 장탄 0에서도 재장전하지 않고 발사하며,
발사 시 장탄 감소와 `squad_ammo_consume`가 없다. 버프가 재장전 도중 켜지면 완료 이벤트 없이
재장전을 취소하고 당시 장탄을 보존한다.

### 컨트롤 (톡톡이·장전컨·버스트 엄폐컨·홀드)

`char["control"]`에서 읽어 `CharState.__init__`이 필드로 고정한다. 없으면 전부 꺼짐 —
컨트롤을 켜지 않은 시뮬 결과는 이 기능 도입 전과 완전히 동일하다.
**메커니즘·수치·설정 스키마의 정본은 `docs/CONTROL.md`.** 여기는 코드 위치만 적는다.

구조는 **2층**이다. 실행층은 조작 원시타입(click·cover 구간)만 알고, 정책과 명시 시퀀스는
그 구간을 만드는 생산자다. 실행층은 둘을 구분하지 않는다. 그 사이에 **조율**이 하나 있다 —
카메라는 하나뿐이라 같은 시각에 한 명만 조작할 수 있기 때문이다.

| 층 | 담당 | 코드 |
|---|---|---|
| 조건 검사 | 정책에 **부작용 없이** 묻는다 | `_wants_control()` → `_want_burst_cover()` · `_want_reload_cover()` · `_click_entry()` |
| 조율 | 이번 틱의 조작자(=카메라)를 정한다. **char tick 이전** | `_arbitrate_control()` → `state["ctrl_owner"]` · `state["camera"]` |
| 생산자 — 기본 전략 | 정책들이 엄폐 구간을 연다 (디스패처) | `_apply_cover_policy()` |
| 생산자 — 클릭 스케줄 | 창에 맞는 행위를 고른다 (누름 래치 · 떼기) | `_apply_click_schedule()` · `_click_entry()` |
| 생산자 — 명시 시퀀스 | `control["sequence"]`를 시각순으로 꺼낸다 | `_pump_ctrl_seq()` |
| 실행층 — cover | 진입·만료·물리 배타 | `_enter_cover()` / `_tick_cover()` / `_exit_cover()` |
| 실행층 — click | 떼는 시점을 앞뒤로 민다 | `_tick_charge()`의 charging 분기 |
| 조작자 관점 로그 | 구간을 기록해 점유를 잰다 | `_open_ctrl()` / `_close_ctrl()` → `SimLog.control_log` |

생산자는 전부 `_owns()`가 참일 때만 집행한다. 뺏기면 `_release_control()`이 엄폐를 풀고
들고 있던 풀차지를 발사시킨다 — 카메라를 떠나면 조작이 풀리기 때문이다(유저 확인).

| 컨트롤 | 필드 | 동작 위치 |
|---|---|---|
| 톡톡이 | `_click_sched`(mode `tap`) / `_tap_hold` / `_tap_charge` / `_tap_release` / `_tap_post` | 누름 래치 `_click_entry()` · 실행 `_tick_charge()`의 charging 분기 |
| 장전컨 | `reload_policy` / `reload_lead` / `reload_margin` / `reload_cover_dur` | `_apply_reload_cover()` |
| 버스트 엄폐컨 | `cover_policy` / `cover_extend` | `_apply_burst_cover()` |
| 홀드 | `_click_sched`(mode `hold`·`hold_judge`) / `_charge_full_t` / `_hold_release_t` | 생산자 `_apply_click_schedule()` · 실행 `_tick_charge()`의 charging 분기 |

> **톡톡이와 홀드는 같은 좌클릭에 실린 두 행위**라 하나의 스케줄(`_click_sched`)로 들어온다.
> 누름은 차지 시작 시점에 래치하고(`_click_entry()`) 떼기는 매 틱 평가한다 —
> 정본은 `docs/CONTROL.md` §체계.

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
  레이어를 얹는 건 러너 쪽(`runner/spec.py`)이고, `simulate()`는 넘겨받은
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
  ├─ bm.notify("last_bullet_fire") if last bullet   ← 유일하게 calc_damage 앞에 오는 발사 트리거
  ├─ ammo -= 1 · bm.notify("squad_ammo_consume")
  ├─ buffs = bm.get_buffs(name, "__enemy__", t)   ← 핵심 버프 집계
  ├─ split = 펠릿 수 (buffs["pellet_count_fixed"] or self.pellets + buffs["pellet_count"])
  ├─ hit_count = split × self.muzzles             ← 총구 수만큼 묶음이 더 나간다
  ├─ 펠릿당 계수 = damage_coeff / split           ← 총구로는 나누지 않는다
  ├─ for each hit:                                 ← 펠릿 × 총구
  │    hit_type = default_hit_type(is_normal_atk=True, is_core=..., ...)
  │    result = calc_damage(base_atk, enemy_def, buffs, weapon, hit_type)
  │    bm.notify("pellet_hit" / "crit_hit" / "core_hit") · notify_team_hit(body/part)
  │    → HitEvent 생성
  │    (좌표 모드면 이 자리가 `_coord_pellet()` — 착탄 판정 → 본체·코어·표적 히트, §좌표 모드)
  ├─ bm.notify("on_attack", t, name)               ← 발사: 발사 1회당 1회
  ├─ for 탄 in muzzles: bm.notify("hit_count", core_frac=그 탄의 코어 확률)  ← 명중: 탄 단위. `not_core`가 읽는다
  ├─ bm.consume_bullet_buffs(name, t)
  └─ for _ in range(muzzles): bm.notify("last_bullet") if last bullet
```

**발사(`on_attack`) → 명중(`hit_count`) 순서이고, 둘 다 `calc_damage` 뒤다.** 순서는 쏘고 나서
맞는다는 실제 순서라 같은 발의 「N회 공격 시」 버프가 「N회 명중 시」 딜에 실리지만,
둘 다 계산 뒤에 있어 **그 발 자신의 대미지는 어느 쪽도 못 바꾼다**
(`buff_manager._BULLET_BOUND_TIMINGS` 주석과 같은 이야기).
빗나감 모델이 생기면 `hit_count` 루프가 그 게이트 자리다 — 정본은 `GAMEPLAY.md` §공격과 명중.

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

`_resolve_lazy()`는 `get_buffs`·`consume_bullet_buffs`·`_live()`(보스 공격이 무적·불굴·도발 등
니케 상태를 묻는 창구 — get_buffs가 읽지 않는 값 없는 stat도 여기서 대상이 정해진다)가 **같이 쓴다.** 지연 resolve
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

**보스 공격·디버프의 무작위 대상(`random:N`)은 고정 시드로 뽑는다**(`_EXPECTED_BOSS_SEED`, 유저 결정
2026-09-15). 「누구를 때리나」는 기대값으로 펼 수 없는 선택이라(전투불능이 비선형이다) 난수열 자체를
고정해, 기대값 모드가 시드와 무관하게 같은 결과를 낸다는 약속을 지킨다.

`simulate(config={"rng_mode": "expected"})`로 켠다. 시드·반복 평균 없이 1회 실행으로
기대딜이 나온다(CLI: `python -m runner.sim "..." --expected`).

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
                    + rule · split · to (쫄몹이 있을 때 `boss.route`가 읽는 대상 규칙. 없으면 아무도 안 읽는다)
                    + reach · part_damage · part (좌표 off 파츠 다중 타격. 닿을 파츠가 없으면 0·빈 값)
                    + interrupt_reach · interrupt_damage (좌표 off 저지원 다중 타격. 닿을 저지원이 없으면 0)
                    + target · extra (좌표 모드 표적 히트 — 맞힌 표적 이름 · 관통·폭발 원으로 따로 맞았는가)
SimLog            — verbose=True 시 버스트·버프스냅샷·재장전 이벤트 기록
SimResult
  ├─ hits: list[HitEvent]           (보스 몫 + 파츠 히트 — 파츠 히트는 `part`에 파츠 이름)
  ├─ char_total: dict[이름 → 딜]     (필드다. squad_total은 이것의 합)
  ├─ boss_log: list[BossLogEntry]   (보스 패턴 시작·종료·표적 파괴·쫄몹 처치/자폭. verbose와 무관하게 채운다)
  ├─ boss_score                     (표적 파괴 점수 합. squad_total에 들어가지 않는다)
  ├─ boss_unmodeled                 (구간만 차지하고 효과 모델이 없던 예약 패턴 id — 지금은 예약 종류가 없다)
  ├─ squad_hits: list[SquadHitEntry] (보스 공격 발·지속 피해 틱 × 대상 — 보호막·엄폐물·체력이 받은 양, 전투불능.
  │                                   틱이면 `source`에 디버프 이름, 쫄몹이 쏜 발이면 `by`에 쫄몹 이름)
  ├─ add_char_total · add_total     (쫄몹에 들어간 딜 — squad_total·char_total에 없다)
  ├─ add_overkill                   (쫄몹 체력을 넘친 딜·이미 사라진 쫄몹에 간 딜 — 버려진 몫)
  ├─ interrupt_char_total · interrupt_total (저지원에 들어간 딜 — 저지원에 떨어진 히트 · reach 히트.
  │                                   squad_total·char_total에 없다)
  ├─ aim_log · aim_spans()          (패턴 모드 — 니케마다 겨눈 곳이 바뀐 시각 / 표적·쫄몹별 조준 시간)
  ├─ summary()                      → 스쿼드 총딜 요약 출력
  ├─ boss_summary()                 → 보스 패턴 흐름 출력
  └─ hit_summary()                  → hit_tag별 히트 집계

모듈 함수 (SimResult의 메서드가 아니다)
  analyze_damage(result, name)      → DamageBreakdown (유형별·버스트구간별)
  analyze_team(result)              → 전원 DamageBreakdown
  print_team_analysis(result)       → 표 출력 (runner/sim.py --view analysis)
```

---

## 8. 모듈 간 의존 관계

```
timeline.py
  ├── aim.py            (좌표 모드일 때만 — 난수 모드의 착탄점 뽑기)
  ├── base_stat.py      (초기화 시 1회)
  ├── boss_pattern.py   (enemy["patterns"]가 있을 때만 — 매 프레임 begin_frame / 히트마다 gate, 쫄몹이 있으면 route)
  ├── buff_manager.py   (매 프레임 notify / get_buffs / tick)
  ├── damage.py         (매 발사마다 calc_damage)
  └── sim_result.py     (HitEvent 생성 및 SimResult 반환)

buff_manager.py
  └── data/             (parsed_skills, equipment_skills, cube, collection)

base_stat.py
  └── data/base_stat_tables/

boss_pattern.py
  ├── aim.py            (좌표 모드의 모양·착탄 확률)
  ├── damage.py         (코드 상성 목록)
  └── sim_result.py     (평타 판정 · BossLogEntry)

aim.py                 (외부 의존 없음 — 좌표 모드 기하. timeline도 착탄점 뽑기로 직접 쓴다)
damage.py              (외부 의존 없음 — 순수 계산)
sim_result.py          (외부 의존 없음 — 자료구조만)
```

버그 재현은 `python -m runner.sim`(파일 수정 없는 단발 시뮬), 수정 후 회귀는
`python -m runner.snapshot`. 사이클 간격 판정 기준은 `docs/HARNESS.md §편성 후 사이클 검증`.
캐릭터별 검증 체크리스트가 있으면 `docs/scenarios/<이름>.md`.
