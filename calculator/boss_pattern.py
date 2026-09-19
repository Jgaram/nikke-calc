"""
보스 패턴 — 시간에 따라 진행하는 보스 스크립트 (1단계)

보스를 「방어력·코어 크기·파츠 여부」 세 스칼라가 아니라 **서로를 잇는 패턴들**로 적는다.
종전 스칼라(`DEFAULT_ENEMY`의 `def`·`core_px`·`has_parts`·`optimal_range_weapons`)는 전투
내내 고정된 기본 상태로 남고, 열린 패턴이 그 위를 시간에 따라 덮어쓴다.
**`enemy["patterns"]`가 비면 스케줄러를 아예 만들지 않는다** — 이 기능 이전과 계산이 한 자리도
달라지면 안 되고, 회귀 baseline이 전부 그 기준이다.

**모드** (유저 결정 2026-09-19 — 판정은 `boss_mode()` 한 곳)
  | 모드                  | 적 dict                      | 보스가 하는 일 |
  |-----------------------|------------------------------|----------------|
  | 간단 모드             | `patterns` 없음              | 기본 스탯(`def`·`code`·`core_px`·`has_parts`·적정거리)이 전투 내내 고정. 파츠 파괴는 `part_break_interval` 주기로 흉내 낸다. BossScript를 만들지 않는다 — 하네스 baseline 전부 |
  | 패턴 모드 · 좌표 off  | `patterns` 있음, `coord` 없음 | 패턴이 적 상태를 시간에 따라 덮고 표적·공격·디버프·쫄몹을 연다. 「어디에 맞는가」는 표적의 `share`(조준 비율)·`reach`(위치 단계)로 어림한다 |
  | 패턴 모드 · 좌표 on   | `patterns` + `coord` 블록    | 같은 패턴에서 「어디에 맞는가」만 화면 좌표·에임·탄 분포로 푼다(§좌표 모드 — 「좌표 모드」는 이 스위치를 켠 상태의 이름이다) |
  좌표는 **패턴 모드의 스위치**다. 좌표가 다룰 표적·코어를 패턴이 열기 때문에 패턴 없이 `coord`를 적으면 거절한다.
  거꾸로 `part_break_interval`은 **간단 모드의 칸**이라 패턴과 같이 적으면 거절한다 — 패턴 모드의 파괴는 표적이
  실제로 깨질 때 나간다(`emit_on_destroy`).
  실제 보스를 넣고 돌리는 건 패턴 모드다. 프리셋 이름만 준 보스(`--boss "솔로 레이드 S40"`)는 스탯만 깔린 간단 모드다.

사용 (timeline.simulate가 부르는 자리):
  pats = validate(enemy["patterns"], weapon_types=...)   즉시 실패시키는 검사
  boss = BossScript(pats, enemy, superior)
  프레임마다   events = boss.begin_frame(t, enemy)       맨 앞 — 전이 → 적 상태 기록 → boss.attacks
               boss.released · boss.enemy_effects        begin_frame 직후 — 풀 효과(패턴 id)·적에게 붙일 효과
               boss.attacks                              이 프레임의 보스 공격·디버프 발 — timeline이 처리하고 비운다
               boss.route(ev)                            히트마다 — 쫄몹이 있으면 (적 id, 가중치)로 나눈다(§쫄몹)
               boss.admit(ev, t)                         보스 몫마다 — 게이트 통과면 흡수 후 True
               boss.part_hits(ev, t)                     admit을 통과한 발마다 — 닿은 reach 파츠 이름(§파츠 다중 타격)
               boss.interrupt_hits(ev, t)                admit을 통과한 발마다 — 닿은 reach 저지원 이름(총딜 밖, 같은 절)
               boss.gate(ev) · boss.hit_target(ev, t)    좌표 모드 표적 히트마다 — 게이트 뒤 그 표적 체력에(§좌표 모드)
               enemy[GEOM_KEY]                           좌표 모드의 산 표적·코어·자동 에임(`Geometry`) — 좌표 off는 없다
               boss.hit_add(ev, add_id, t)               쫄몹 몫마다 — 쫄몹 체력에 넣는다
               boss.gone                                 이번에 사라진 쫄몹 id — timeline이 적 효과에서 지우고 비운다
               boss.dispel(n, t)                         니케의 「적 이로운 효과 해제」 — 다음 프레임 맨 앞에 풀린다
  적 효과 대상  boss.resolve_enemies(target, has_state)   buff_manager가 적 대상 문자열을 풀 때(쫄몹이 있을 때만)
  루프 종료 뒤 boss.finish(duration)                     열린 패턴을 `end`로 닫는다

의존: damage(코드 상성 목록) · sim_result(평타 판정·로그 자료구조)뿐이라 순환이 없다.

─────────────────────────────────────────────────────────────────────────────
포맷 — 패턴 하나
─────────────────────────────────────────────────────────────────────────────
  {
    "id": "저지1",          고유 이름. 없으면 "<kind><그 kind 안의 순번>" (interrupt1 …)
    "kind": "interrupt",    아래 종류표
    "after": ["출현"],      시작 조건. 기본 ["start"] ("start" = 전투 시작, 예약어)
    "delay": 0.0,           after 충족 후 추가 지연(초)
    "until": {...},         종료 조건. 없으면 전투 끝까지
    "repeat": 1,            열릴 수 있는 횟수. 0이면 무제한
    "emit": [],             시작할 때 스쿼드 전원에게 쏠 이벤트 (BOSS_EVENTS)
    "emit_end": [],         끝날 때 (전투 종료로 닫힐 때는 쏘지 않는다)
    "note": "",             사람용 메모
    ... kind별 칸
  }

구간은 `[시작, 끝)` 반개구간이다 — 엔진의 다른 구간 판정과 같은 규약.

**after** — 항목은 `"패턴id"` 또는 `{"node": "패턴id", "outcome": "expired"}`.
  여럿이면 **OR**다(하나라도 새로 끝나면 열린다). AND는 두지 않는다 — 필요해지면 `after_all`을
  따로 만든다. `outcome`을 적으면 그 사유로 끝났을 때만 열린다(저지 실패 분기).
  판정은 「그 패턴이 **또** 끝났는가」다 — 열 때 항목마다 그때까지의 종료 횟수를 소비 기록에
  적고, 그보다 늘었을 때만 다시 연다. 이 한 줄이 `after: ["start", "마지막"]` + `repeat: 0`을
  사이클로 만든다. 열려 있는 동안 쌓인 종료도 소비 전이라, 끝나는 프레임에 곧바로 다시 열린다.

**until** — `{"time": 15.0, "targets_cleared": true, "after": [{"node": "저지1", "delay": 2.0}]}`
  먼저 오는 쪽(OR). 같은 프레임에 함께 성립하면 `targets_cleared` > `after` > `time` —
  제한시간이 끝나는 바로 그 프레임에 마지막 저지원을 깼다면 인게임에서는 성공이다.
  `until.after`는 **열린 뒤의** 종료만 센다.

**outcome**: cleared(표적 전멸) · expired(제한시간) · followed(다른 패턴을 따라) · end(전투 끝)

| kind | 칸 | 하는 일 |
|---|---|---|
| idle / groggy | — | 아무것도 안 함 (groggy는 이름만 다르다 — 「공격하지 않는 구간」을 스크립트에서 읽히게 적는 자리) |
| buff | enemy: {def_mult, def_add, received_dmg_pct} · irremovable | def ← def × def_mult + def_add (열린 순서대로 겹친다) · 보스가 받는 대미지 증감 % (아래 §보스 버프) |
| core | core_px (>0) | 코어를 연다 |
| parts | targets | 살아 있는 표적이 있으면 has_parts=True |
| interrupt | targets | 저지. has_parts는 안 건드린다 |
| shield | code | 그 코드에 우월한 캐스터의 딜만 들어간다. 막힌 스킬 대미지는 게이지도 안 채운다(무기 사격 몫은 채운다) |
| vanish | — | 평타 무효(평타 몫의 버스트 게이지 포함). 스킬 딜·스킬 게이지는 그대로 |
| move | weapons 또는 distance | 적정거리를 바꾼다 — 무기군 목록(`optimal_range_weapons`) 교체, 또는 보스 거리(`distance`) 교체. 한 스크립트에서 둘을 섞지 않는다 (아래 §적정거리) |
| attack | spec | 보스 → 니케 피해 (아래 §공격). `debuffs`를 적으면 체력 피해가 난 니케에게 디버프도 건다 |
| debuff | spec | 보스 → 니케 해로운 효과 (아래 §디버프) |
| summon | spec | 쫄몹 소환 (아래 §쫄몹). `until.targets_cleared` = 쫄몹이 전부 사라짐 |

예약 종류(`RESERVED_KINDS` — 지금은 없다)는 구간만 차지하고 효과를 안 내며 SimResult.boss_unmodeled에
실린다. 구간을 차지하는 이유: 저지 실패 뒤 공격 패턴이 다음 패턴을 미루는 게 실제 거동이라, 효과가 없다고
구간까지 없애면 뒤가 통째로 당겨진다.

**공격** (attack.spec — 모르는 칸은 거절한다)
  {"coeff": 150, "target": "random:1", "pierce": false, "hits": 3, "interval": 0.5, "atk": 200000}
  coeff    계수 %. 필수
  target   필수. all(전원) · random:N · top_atk:N(최종 공격력 순) · slot:1,3(스쿼드 자리, 1부터)
  pierce   관통 여부. 기본 false
  ignore_taunt  도발에 끌리지 않는 공격. 기본 false — 도발은 all을 뺀 모든 공격을 끈다(유저 확인)
  hits     발수. 기본 1. 열린 시각부터 interval초 간격으로 쏘고, 패턴이 먼저 닫히면 남은 발은 버린다
  interval 발 간격(초). 기본 0 — 모든 발이 같은 프레임
  atk      이 공격만의 보스 공격력. 없으면 `enemy["atk"]`
  debuffs  맞은 니케에게 걸 디버프 목록(아래 §디버프 항목). **체력에 피해가 들어간 발만 건다** — 보호막·
           엄폐물이 받았거나 무적이면 안 걸리고, 그 발로 쓰러졌어도 안 건다(유저 확인 2026-09-15)
  피해 산정·층 규칙(보호막·엄폐물·무적·도발·은신)·전투불능은 timeline이 한다 — 니케 상태가
  거기 있기 때문이다. 이 모듈은 「언제 몇 발이 누구 규칙으로 나가는가」까지만 안다.

**디버프** (debuff.spec)
  {"target": "all", "ignore_taunt": false, "hits": 1, "interval": 0, "debuffs": [...]}
  target·ignore_taunt·hits·interval은 공격과 같다 — **대상 규칙도 도발·은신까지 공격과 같다**(유저 확인).
  발마다 대상을 새로 고르고 목록의 디버프를 전부 건다.

  디버프 항목 (공격의 `debuffs`와 같은 모양)
  {"name": "부식", "stat": "atk_pct", "value": -20, "duration": 10, "max_stack": 1, "irremovable": false}
  stat      필수. `DEBUFF_STATS` — 수치 stat은 `value`(해로운 쪽 부호만), 상태(stun·cover_disabled)는
            값이 없고, 지속 피해 `dot`은 `coeff`(계수 %)와 `interval`(틱 초, 기본 1)을 적는다
  duration  초. **생략하면 이 패턴이 닫힐 때 풀린다**
  max_stack 다시 걸리면 쌓이는 중첩 상한. 기본 1(다시 걸리면 지속시간만 갱신)
  irremovable  해제 불가. 기본 false — 해로운 효과 해제·중첩 감소에 안 걸린다
  name      기본 "<패턴 id>·<stat>". `debuff_immune:[name]`이 이 이름을 본다
  효과는 buff_manager의 해로운 효과(`polarity: harmful`, 시전자 `__enemy__`)로 **니케마다 따로** 붙는다 —
  면역(`debuff_immune`)·해제·전투불능 소멸이 니케 스킬이 건 해로운 효과와 같은 경로를 탄다.
  `dot` 틱 피해 = max((보스 공격력 − 니케 최종 방어력) × coeff% × 중첩 × (100% + 받는 피해 증감%), 1).
  **체력만 받는다**(보호막·엄폐물 무시, 유저 확인). 무적·불굴·전투불능은 공격과 같고, 피격 이벤트는 안 쏜다
  (⬜ 인게임 미확인). 첫 틱은 걸린 뒤 interval초, 다시 걸리면 그때부터 다시 잰다. 공격에 딸린 `dot`은
  그 공격의 `atk`를 쓴다.

**보스 버프** (buff.enemy.received_dmg_pct · buff.irremovable)
  `received_dmg_pct`는 보스가 받는 대미지 증감 %다(음수 = 감소). 열려 있는 동안 적에게 붙은 효과로
  들어가 니케 딜의 ⑥에 합산된다. 열린 `buff` 패턴 하나가 **이로운 효과 하나**다 — 니케의 「적 이로운
  효과 해제 N개」(`enemy_buff_cleanse`)가 나중에 두른 것부터 N개를 끈다(⬜ 순서는 잠정). 꺼진 패턴은
  방어력 오버레이와 받는 대미지를 둘 다 잃고 구간은 그대로 간다. `irremovable`이면 안 꺼진다.

**표적** (parts·interrupt의 targets 항목 — 좌표 off. 좌표 모드는 아래 §좌표 모드)
  {"name": "저지원A", "hp": 2e8, "share": 1.0, "score": 1000000, "core_px": 0,
   "emit_on_destroy": ["event:part_destroy"], "reach": 3}
  hp 0 = 안 깨지는 표적. share = 스쿼드 딜 중 이 표적이 받는 비율(합이 1을 넘어도 된다).
  core_px > 0이면 살아 있는 동안 코어가 열린다. reach = 위치 단계 — parts는 1~5, interrupt는 1~4(§파츠 다중 타격).
  share 몫은 총딜에 그대로 남는다 — 체력 풀은 「언제 깨지는가」만 세는 카운터다. 총딜에 **더해지는** 것은
  reach로 닿은 파츠 다중 타격뿐이고, reach로 닿은 저지원 히트는 총딜 밖이다. 좌표 off에서 좌표 칸
  (x·y·r·w·h·rotation·z)을 적으면 거절한다.

**좌표 모드** (`enemy["coord"]` — 유저 결정 2026-09-18. 기하는 `calculator/aim.py`)
  적에 `coord` 블록이 있으면({} 포함) 표적을 **화면 좌표**로 적고, 「어디에 맞는가」를 share·reach 대신 에임과 탄
  분포로 푼다. 좌표는 CDN 탄착군·core_px와 같은 화면 px이고 x 오른쪽 · y 위, 원점은 보스 기준점이다.
    {"auto_aim": [0, 0], "explosion_scale": 0.2, "pierce_px": 25}
  auto_aim          자동 에임 위치 — 없으면 열린 코어 중심, 코어도 없으면 원점(유저: 대부분 코어 위치)
  explosion_scale   폭발 반지름(px) = CDN spot_explosion_range × 이 값 × (1 + 폭발 범위 ▲%). 기본 0.2 (⬜ 가정값)
  pierce_px         관통 반지름(px) = 이 값 × (1 + 관통 범위 ▲%). 기본 25 (⬜ 가정값)
  표적: {"name", "hp", "score", "emit_on_destroy"} + 원 {"x", "y", "r"} 또는 직사각형 {"x", "y", "w", "h", "rotation"}
  (rotation = 도, 반시계) + "z"(앞뒤 — 큰 쪽이 앞). share·reach·core_px는 거절한다 — 코어는 core 패턴에 x·y로 둔다.
  표적 이름은 스크립트 전체에서 하나다(히트와 에임이 이름으로 찾는다).
  - 앞뒤: 표적은 코어·본체보다 앞. 표적끼리는 z, 같으면 나중에 생긴 것, 같은 패턴 안에서는 뒤에 적힌 것이 앞
  - 한 발(펠릿)이 떨어지는 곳: 조준점 중심, 반지름 누적 확률 (ρ/R)^2.55(R = 탄착군 직경/2) — 코어 히트 모델 그대로
    · 보통 탄: 착탄점의 가장 앞 표적 → 그 표적 히트 / 없고 코어 안 → 코어 히트 / 아니면 본체
    · 관통 탄·폭발 탄: 본체 히트(착탄점이 코어 안이면 코어 — 앞 표적과 무관) + 착탄점에서 관통·폭발 반지름 안에
      닿는 표적마다 히트(`HitEvent.extra`). 둘을 겸하면 넓은 쪽
    · 기대값 모드는 확률로 나눈 히트, 난수 모드는 착탄점을 뽑는다
  - 파츠 히트: 코어 없음 · 파츠 대미지 ▲ · **총딜 포함**(초과분도). 저지원 히트: 파츠 판정 없음 · **총딜 밖**
    (`interrupt_dealt`, 유저 결정) — 저지원을 겨누는 만큼 점수를 잃는다. 둘 다 흡혈은 받는다
  - 관통·폭발로 따로 맞은 표적은 본체 히트의 크리 판정을 쓰고 트리거·게이지를 안 낸다. 보통 탄이 떨어진 표적은 그
    발의 명중이다 — 파츠면 「파츠 명중」, 본체(코어 아님)면 「본체 명중」 트리거
  - 스킬은 본체(종전)다. 「파츠 포함」 전체기는 산 파츠 전부(저지원 X), 관통 대미지·발사체 폭발 스킬은 시전자의
    조준점에서 관통·폭발 원(탄 분포 없이). 쫄몹은 좌표가 없어 share 그대로 — 본체 히트에서만 나눈다
  - 에임은 timeline이 프레임마다 정한다(카메라 · `control["aim"]` · 레이어 2 우선 타격 · 풀버스트 [사격 집중]). 이 모듈은
    산 표적·코어·자동 에임(`Geometry`)과 히트 회계(`gate` · `hit_target`)만 맡는다
  - **깨야 하는 표적**(`Geometry.must_break` — 레이어 2가 겨누는 것, 유저 결정 2026-09-19): 산 저지원 전부와 **벌칙
    파츠**의 산 표적(hp > 0). 벌칙 파츠 = `until.targets_cleared`가 있고, 그 패턴의 실패 종료(expired·followed)를
    `after`로 받는 패턴이 있는 parts(`penalty_parts`) — S40 알집(못 깨면 자폭 랩쳐). 실패 분기를 그 parts 패턴에
    직접 달아야 알아본다. 순서는 스크립트에 먼저 적힌 패턴 → 패턴 안에서 적힌 순서
  좌표를 하나도 안 준 좌표 모드(표적 없음 · 코어는 원점)는 같은 패턴의 좌표 off와 같은 적이다 — 조준점 중심 코어는
  종전 식과 같다.

**파츠 다중 타격** (좌표 off — parts 표적의 `reach`, 유저 결정 2026-09-18)
  관통·발사체 폭발·「파츠 포함」 전체기는 한 발로 본체와 파츠를 함께 맞힌다. 닿은 파츠마다 대미지가 따로 한 번
  더 들어가 총딜·캐릭터 딜에 더해지고, 파츠 잔여 체력을 넘어도 그 발의 몫이 통째로 들어간다(초과분도 입힌 피해).
  좌표가 없어서 「그 파츠에 무엇이 닿는가」를 파츠마다 위치 단계로 적는다 — **누적**이다:
    reach  이 파츠에 닿는 발
    1      모두 — 관통 · 발사체 폭발 · 「파츠 포함」 전체기
    2      관통은 관통 범위 ▲ 합 100% 이상일 때만 · 폭발 · 전체기
    3      발사체 폭발 · 전체기 (관통은 안 닿는다)
    4      폭발은 폭발 범위 ▲ 합 100% 이상일 때만 · 전체기
    5      「파츠 포함」 전체기(`hits_parts`)만
  발의 단계 상한 R(`hit_reach`): 관통 1 · 관통 + 관통 범위 100% 2 · 발사체 폭발 3 · 폭발 + 폭발 범위 100% 4 ·
  「파츠 포함」 전체기 5 — R ≥ reach면 닿는다. 관통 = `is_pierce_damage`(관통 사격·관통 대미지 스킬), 발사체 폭발 =
  `is_projectile_explosion`(RL 사격·발사체 폭발 대미지 스킬). 관통 없는 일반 사격은 파츠를 따로 맞히지 않는다 —
  어디를 겨누는지는 share가 맡는다.
  - 파츠 히트 대미지: 같은 발을 **코어 없이 파츠 대미지 ▲(`part_dmg_pct`)를 얹어** 다시 산정한다. 보스 방어력은
    본체와 같다. 크리는 본체 히트의 판정을 그대로 쓴다(`crit_override` — 난수를 안 먹는다, ⬜ 인게임 미확인)
  - 보스 게이트(사라짐·속성보호막)에 막힌 발은 파츠에도 안 들어간다
  - 닿은 파츠는 그 발의 share 몫을 받지 않는다 — 한 발에 한 번만 맞는다
  - 파츠 히트는 트리거·버스트 게이지를 따로 내지 않고 흡혈은 받는다(⬜ 둘 다 인게임 미확인)
  - reach 파츠가 살아 있는 동안 「파츠 포함」 전체기의 본체 히트는 파츠 판정(`is_part`)을 내려놓는다 — 파츠
    몫은 파츠 히트가 받는다
  - hp 0(안 깨지는 파츠)에 reach를 적으면 추가 딜이 전투 내내 들어간다
  timeline은 프레임 맨 앞의 `enemy[PART_REACH_KEY]`(산 reach 파츠 중 가장 낮은 단계, 없으면 0)를 보고 닿을
  파츠가 있을 때만 파츠 몫을 산정한다 — 보스 패턴이 없거나 reach 파츠가 없으면 계산이 한 자리도 안 달라진다.

  **저지원 다중 타격** (interrupt 표적의 `reach`, 유저 결정 2026-09-19 「저지원에 들어간 딜은 대미지로 인정하지 않음」)
  관통·발사체 폭발은 저지원에도 같은 단계 규칙으로 닿는다. 좌표 모드의 저지원 히트와 같은 규약이다:
  - 단계는 1~4뿐이다 — 「파츠 포함」 전체기는 저지원에 안 닿는다(5는 거절). 그래서 저지원에 대한 발의 단계 상한은
    `hits_parts`를 빼고 잰다 — 전체기이면서 발사체 폭발인 발은 저지원에 3(또는 4)으로 닿는다
  - 대미지: 같은 발을 **코어도 파츠 판정도 없이** 다시 산정(`interrupt_damage`) · 크리는 본체 판정 그대로
  - **총딜 밖** — 시전자별로 `interrupt_dealt`에 쌓는다(좌표 모드와 같은 칸). 흡혈은 받는다. 트리거·게이지 없음
  - 닿은 저지원은 그 발의 share 몫을 받지 않는다 — 한 발에 한 번
  timeline은 `enemy[INTERRUPT_REACH_KEY]`(산 reach 저지원 중 가장 낮은 단계, 없으면 0)를 파츠 쪽과 같이 본다.

적 상태 합성: 기본값에서 출발해 열린 패턴을 시작 시각 순(같으면 선언 순)으로 덮어쓴다.
`core_px`만 예외로 **살아 있는 것 중 가장 큰 값**(기본값 포함) — 코어가 둘이면 큰 쪽을 겨냥한다.

**적정거리** (`enemy["distance"]` · `move.distance` — 유저 결정 2026-09-18)
  보스 거리가 있으면 적정거리 판정이 무기군 목록(`optimal_range_weapons`) 대신 **니케마다** 자기 적정 구간(CDN
  bonusrange — `parsed_nikke`의 `optimal_range`, 적정 최대·최소 사거리 ▲ 반영)과 거리를 비교한다. 판정의 정본은
  `buff_manager.in_optimal_range`다. 거리는 보스에 하나다 — 실제로는 보스 부피 때문에 조준 위치마다 다르지만 일단
  하나로 둔다. 한 스크립트에서 무기군 목록과 거리를 섞지 않는다(적의 기본값·move 패턴 모두 — 섞으면 거절).

**쫄몹** (summon.spec — 모르는 칸은 거절한다. 좌표가 없는 모드다, 유저 결정 2026-09-16)
  {"name": "랩쳐", "count": 3, "hp": 5e6, "hit_hp": 20, "hit_hp_after": 3, "share": 0.5,
   "attack": {"coeff": 50, "target": "random:1", "atk": 20000}, "attack_at": 5, "self_destruct": true}
  name      표시 이름. 기본 패턴 id. 적 id는 `__enemy__:<패턴 id>#<번호>`
  count     마릿수. 기본 1
  hp        마리당 체력(>0). 쫄몹 스탯은 게임 데이터에 없어 손으로 적는다
  hit_hp    **타수 체력** — 딜이 얼마든 한 발에 1씩 깎이고 이 수를 채우면 죽는다(>0 정수).
            삼켜진 나머지 딜은 넘친 딜과 같은 자리로 간다. 한 발 = HitEvent 하나라 산탄 한 알·
            지속 대미지 한 틱도 1이다
  hit_hp_after  등장 뒤 몇 초에 체력에서 타수로 바뀌는가(>0). `hp`와 `hit_hp`를 같이 적을 때만 쓴다 —
            그 전에는 딜로 죽고 그 뒤로는 타수로 죽는다(마더웨일·사치스러운 거미의 소환수 방식).
            `hp` 없이 `hit_hp`만 적으면 **처음부터** 타수다. 둘 다 없으면 거절한다
  share     **조준 비율** — 조준으로 대상이 정해지는 딜(평타·「(조준선에) 가장 가까운 적」·`target` 스킬) 중
            이 무리가 받는 몫. 앞에서부터 한 마리씩 잡는다. 기본 0(조준하지 않음 — 광역·무작위 스킬로만 맞는다)
  attack    쫄몹 한 마리의 공격(§공격 칸). **등장 뒤 attack_at초에 산 쫄몹마다** 쏜다. atk는 attack.atk나
            spec.atk 중 하나에 필수(보스 공격력을 쓰지 않는다)
  attack_at 등장 뒤 첫 발까지 초. 기본 0
  self_destruct  마지막 발을 쏜 뒤 사라진다 — **사망으로 친다**(enemy_death 발동, 유저 확인)
  패턴이 닫히면 산 쫄몹은 퇴장한다 — 사망이 아니라 enemy_death를 쏘지 않는다(유저 확인).
  등장하면 쫄몹마다 event:enemy_spawn, 처치·자폭하면 쫄몹마다 enemy_death를 스쿼드 전원에게 쏜다
  (사망은 표적 파괴와 같이 다음 프레임 맨 앞). 적 수(`enemy_count_*`)는 보스 1 + 산 쫄몹이고 프레임 맨 앞에 정한다.

  딜 나누기 (`route` — 쫄몹이 없으면 부르지 않는다):
    조준        무기 사격 · target · target_body · same_target(:X) · enemy · enemies_nearest:N ·
                enemies_nearest_in_range — N이 1이면 가중치로 쪼갠다: 보스 1 − Σshare, 무리마다 첫 산 쫄몹이
                share(Σshare > 1이면 합이 1이 되게 줄인다). N ≥ 2면 가중치 순으로 N기가 온전히 맞는다
    전원        all_enemies · enemies_in_range (좌표가 없어 전원이 범위 안이라고 본다)
    무작위      enemies_random:N — 시드 난수(기대값 모드도 고정 시드)
    보스 먼저   enemies_top_hp:N · enemies_top_atk:N(쫄몹은 attack의 atk) · enemies_top_def:N
    쫄몹 먼저   enemies_lowest_hp:N(남은 체력 낮은 순) · enemies_lowest_def:N
    필터        enemies_with_buff:X(그 효과가 붙은 적) — 없으면 보스. enemies_code·enemies_lowest_hp_code는
                쫄몹 코드가 없어 보스
    모르는 적 대상은 조준으로 본다. 분할 대미지(`split`)는 맞은 적 수로 나눈다. 지속 대미지 틱은 효과가 붙은 적(`to`)만.
  **쫄몹 몫은 보스 기준으로 산정한 딜 그대로다**(쫄몹 방어력·쫄몹에 붙은 효과는 딜에 안 들어간다 — 근사).
  보스 게이트(사라짐·속성보호막)와 파츠 표적은 보스 몫에만 걸린다. 쫄몹 몫은 총딜에 없고
  `add_dealt`(시전자별)·`add_overkill`(넘친 딜·이미 사라진 쫄몹에 간 딜)로 따로 싣는다.
  적에게 거는 효과도 같은 규칙으로 적마다 붙는다(`resolve_enemies`) — 조준 규칙은 가중치가 가장 큰 1기(동률 보스).
  **좌표 모델 교체 지점은 `_aim_weights`와 `admit`의 표적 share 두 곳이다.**
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Callable

from .aim import Landing, Shape, landing
from .damage import DEFAULT_ENEMY_DEF, _CODE_ADVANTAGE
from .sim_result import BossLogEntry, HitEvent, _is_normal

# 시각 비교 여유. 루프가 `t += DT`로 시각을 쌓아 180초까지 가면 5e-13쯤 어긋나서, 그대로 `>=`로
# 재면 「20초 뒤」가 한 프레임 늦게(20.017초) 끝난다. 프레임(1.67e-2초)보다 한참 작아 한 프레임
# 일찍 터질 일은 없다.
_EPS = 1e-9

START = "start"

OUTCOMES = ("cleared", "expired", "followed", "end")

# 보스가 `emit`으로 스쿼드에 쏠 수 있는 이벤트 — **닫힌 집합이다.** 이름을 잘못 적으면 영영
# 무발동인데 딜은 그럴듯하게 나와 발견이 늦다. 피격 계열(received_hit·event:ally_down …)은 여기
# 없다 — `attack` 패턴이 실제로 때린 결과로만 나가야지 스크립트가 손으로 쏘면 안 된다.
BOSS_EVENTS = ("event:part_destroy", "event:target_spawn", "event:projectile_destroy",
               "enemy_death")

# 보스 모드(docstring §모드) — `boss_mode()`가 돌려주는 값과 사람용 이름
SIMPLE, PATTERN, COORD = "simple", "pattern", "coord"
MODE_LABELS = {SIMPLE: "간단 모드", PATTERN: "패턴 모드 · 좌표 off", COORD: "패턴 모드 · 좌표 on"}

# 적 상태 중 패턴이 바꿀 수 있는 칸. 여기 없는 키는 패턴으로 못 바꾼다.
OVERLAY_FIELDS = ("def", "core_px", "has_parts", "optimal_range_weapons", "distance")

_COMMON_FIELDS = frozenset({"id", "kind", "after", "delay", "until", "repeat",
                            "emit", "emit_end", "note"})
_KIND_FIELDS: dict[str, frozenset[str]] = {
    "idle":      frozenset(),
    "groggy":    frozenset(),
    "buff":      frozenset({"enemy", "irremovable"}),
    "core":      frozenset({"core_px", "x", "y"}),
    "parts":     frozenset({"targets"}),
    "interrupt": frozenset({"targets"}),
    "shield":    frozenset({"code"}),
    "vanish":    frozenset(),
    "move":      frozenset({"weapons", "distance"}),
    "attack":    frozenset({"spec"}),
    "summon":    frozenset({"spec"}),
    "debuff":    frozenset({"spec"}),
}
_REQUIRED: dict[str, tuple[str, ...]] = {
    "buff": ("enemy",), "core": ("core_px",), "parts": ("targets",),
    "interrupt": ("targets",), "shield": ("code",),
    "summon": ("spec",),
}
RESERVED_KINDS: frozenset[str] = frozenset()
_TARGET_RULE_FIELDS = frozenset({"target", "ignore_taunt", "hits", "interval"})
_SUMMON_FIELDS = frozenset({"name", "count", "hp", "hit_hp", "hit_hp_after", "share", "atk",
                            "attack", "attack_at", "self_destruct"})
_ATTACK_FIELDS = _TARGET_RULE_FIELDS | {"coeff", "pierce", "atk", "debuffs"}
_CAST_FIELDS = _TARGET_RULE_FIELDS | {"debuffs"}
# 보스 공격력 기본값 — 솔로 레이드 모의전 보스 Lv 400(`MonsterStatEnhanceTable` 그룹 230000).
# 모의전은 Lv 390으로 시작해 누적 피해가 문턱을 넘으면 400이 되고, 딜 계산은 대부분 400을 상대한다.
# 적 방어력 기본값(`timeline.DEFAULT_ENEMY["def"]`)도 같은 행이다. 근거는 docs/DATA_VERIFY.md §레이드 보스 스탯
DEFAULT_BOSS_ATK = 114344
_TARGET_KINDS = frozenset({"parts", "interrupt"})
_UNTIL_FIELDS = frozenset({"time", "targets_cleared", "after"})
_BUFF_FIELDS = frozenset({"def_mult", "def_add", "received_dmg_pct"})

# 보스 효과의 시전자·적 대상 센티널 — buff_manager가 적을 가리키는 이름과 같다.
ENEMY = "__enemy__"
# 쫄몹의 적 id 접두사 — `__enemy__:<패턴 id>#<번호>`. buff_manager `_is_enemy`가 같은 규약으로 알아본다
ADD_PREFIX = ENEMY + ":"

# 쫄몹이 있을 때 적 대상 문자열을 푸는 규칙 (docstring §쫄몹). 접두사까지만 본다
_AIM_TARGETS = frozenset({"enemy", "target", "target_body", "same_target", "enemies_nearest_in_range"})
_ALL_TARGETS = frozenset({"all_enemies", "enemies_in_range"})

# 보스가 니케에게 걸 수 있는 해로운 효과 — **닫힌 집합이다.** 엔진이 니케 쪽에서 실제로 읽는 stat만
# 둔다(각 stat이 어디서 읽히는지는 docs/CALCULATOR.md §보스 디버프). 값은 해로운 방향의 부호다 —
# -1은 감소가, +1은 증가가 해롭다. 반대 부호는 오타로 보고 거절한다(「디버프」가 이로우면 뜻이 없다).
# 0은 값이 없는 상태이고, `dot`은 계수를 따로 받는다.
DOT_STAT = "dot"
DEBUFF_STATS: dict[str, int] = {
    "atk_pct": -1, "def_pct": -1, "crit_rate": -1, "crit_dmg": -1, "accuracy_pct": -1,
    "attack_speed_pct": -1, "reload_speed_pct": -1, "charge_speed_pct": -1, "max_ammo_pct": -1,
    "atk_dmg_pct": -1, "heal_received_pct": -1,
    "received_dmg_pct": +1,
    "stun": 0, "cover_disabled": 0,
    DOT_STAT: 0,
}
_DEBUFF_FIELDS = frozenset({"name", "stat", "value", "coeff", "interval", "duration",
                            "max_stack", "irremovable"})
_TARGET_FIELDS = frozenset({"name", "hp", "share", "score", "core_px", "emit_on_destroy", "reach"})
# 좌표 모드 표적의 칸 — 모양(원 r · 직사각형 w·h·rotation)과 앞뒤(z). 좌표 off에서 적으면 거절한다
_COORD_TARGET_FIELDS = frozenset({"x", "y", "r", "w", "h", "rotation", "z"})
# 좌표 off의 칸 — 좌표 모드에서는 「어디에 맞는가」를 좌표가 풀므로 거절한다
_STAGE_ONLY_FIELDS = frozenset({"share", "reach", "core_px"})
# 좌표 모드 적 블록(`enemy["coord"]`)의 칸
COORD_FIELDS = frozenset({"auto_aim", "explosion_scale", "pierce_px"})
# 좌표 모드 가정값 (⬜ 둘 다 데이터 없음 — 유저 결정 2026-09-18 「엔진 가정값 + 스크립트가 덮기」)
#   폭발 반지름(px) = CDN spot_explosion_range × explosion_scale × (1 + 폭발 범위 ▲%)
#   관통 반지름(px) = pierce_px × (1 + 관통 범위 ▲%)
DEFAULT_EXPLOSION_SCALE = 0.2
DEFAULT_PIERCE_PX = 25.0
# 무기 값이 없는 폭발(RL이 아닌 니케의 발사체 폭발 스킬)의 기본 CDN 범위 — RL 대다수의 값(⬜)
DEFAULT_EXPLOSION_RANGE = 500
# 좌표 모드의 산 표적·코어 — 프레임 맨 앞에 `_apply()`가 적 dict에 싣는다(좌표 off는 None). timeline이 사격마다 읽는다
GEOM_KEY = "_geom"

# 파츠 위치 단계(`reach`) — 좌표 off의 파츠 다중 타격(docstring §파츠 다중 타격). **누적이다** — 높은 단계에
# 닿는 수단은 낮은 단계에도 닿는다(유저 결정 2026-09-18).
REACH_TIERS: dict[int, str] = {
    1: "모두 맞음",
    2: "관통은 관통 범위 +100%부터",
    3: "관통 X · 발사체 폭발부터",
    4: "폭발도 폭발 범위 +100%부터",
    5: "파츠 포함 전체기로만",
}
# 저지원에 적을 수 있는 단계 — 「파츠 포함」 전체기는 저지원에 안 닿는다(좌표 모드와 같은 규약)
INTERRUPT_REACH_MAX = 4
# 관통 범위·폭발 범위 ▲ 합이 이 % 이상이면 그 수단이 한 단계 더 닿는다
RANGE_BUFF_STEP = 100.0
# 프레임 맨 앞에 `_apply()`가 적 dict에 적는 값 — 산 reach 파츠 중 가장 낮은 단계(없으면 0). timeline이 발마다
# 「닿을 파츠가 있는가」를 싸게 묻는 자리다. 패턴이 바꾸는 적 상태(OVERLAY_FIELDS)는 아니다
PART_REACH_KEY = "_part_reach"
# 같은 자리의 저지원 쪽 — 산 reach 저지원 중 가장 낮은 단계(없으면 0)
INTERRUPT_REACH_KEY = "_interrupt_reach"


def hit_reach(*, parts_skill: bool = False, explosion: bool = False, pierce: bool = False,
              explosion_range: float = 0.0, pierce_range: float = 0.0) -> int:
    """한 발이 닿는 파츠 위치 단계의 상한. 0이면 파츠에 따로 안 들어간다 (docstring §파츠 다중 타격).

    `parts_skill` = 원문이 파츠를 명시한 전체기(`hits_parts`) · `explosion` = 발사체 폭발 · `pierce` = 관통.
    범위는 그 발을 쏜 캐릭터의 관통 범위·폭발 범위 ▲ 합(%)이다. 여러 수단을 겸하면 가장 멀리 닿는 쪽이다.
    """
    if parts_skill:
        return 5
    if explosion:
        return 4 if explosion_range >= RANGE_BUFF_STEP - _EPS else 3
    if pierce:
        return 2 if pierce_range >= RANGE_BUFF_STEP - _EPS else 1
    return 0
# 뒤 패턴의 `after` 필터가 요구하는 앞 패턴의 종료 조건. 없으면 그 분기는 영영 안 열린다.
_OUTCOME_NEEDS = {"cleared": "until.targets_cleared", "expired": "until.time",
                  "followed": "until.after"}
# 표적을 다 깨지 못하고 닫힌 종료 — 이걸 `after`로 받는 패턴이 실패(벌칙) 분기다
_FAIL_OUTCOMES = frozenset({"expired", "followed"})


# ── 정규화된 스크립트 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class TargetSpec:
    name: str
    hp: float
    share: float = 1.0
    score: int = 0
    core_px: int = 0
    emits: tuple[str, ...] = ()
    reach: int = 0      # 위치 단계(parts 1~5 · interrupt 1~4). 0 = 적지 않음 — 다중 타격 추가 히트 없음
    # ── 좌표 모드 ──
    shape: Shape | None = None      # 화면 모양. 좌표 off는 None
    z: float = 0.0                  # 앞뒤 — 큰 쪽이 앞

    @property
    def breakable(self) -> bool:
        return self.hp > 0


@dataclass(frozen=True)
class Pattern:
    idx: int                                        # 선언 순서 — 같은 시각에 열린 것끼리의 합성 순서
    id: str
    kind: str
    after: tuple[tuple[str, str | None], ...]       # (노드, outcome 필터)
    delay: float = 0.0
    until_time: float | None = None
    until_cleared: bool = False
    until_after: tuple[tuple[str, float], ...] = ()  # (노드, 지연)
    repeat: int = 1
    emit: tuple[str, ...] = ()
    emit_end: tuple[str, ...] = ()
    note: str = ""
    # kind별
    def_mult: float = 1
    def_add: float = 0
    irremovable: bool = False
    core_px: int = 0
    core_at: tuple[float, float] = (0.0, 0.0)   # core — 좌표 모드의 코어 중심
    code: str = ""
    weapons: tuple[str, ...] = ()
    distance: float | None = None       # move — 보스 거리(있으면 weapons 대신)
    targets: tuple[TargetSpec, ...] = ()
    attack: AttackSpec | None = None
    cast: DebuffCastSpec | None = None
    summon: SummonSpec | None = None
    # buff의 `received_dmg_pct` — 열릴 때 적에게 붙이는 효과 dict (없으면 None)
    enemy_effect: dict | None = field(default=None, compare=False)

    @property
    def shots(self) -> AttackSpec | DebuffCastSpec | None:
        """발 스케줄이 있는 spec (attack·debuff)."""
        return self.attack if self.attack is not None else self.cast


@dataclass(frozen=True)
class DebuffSpec:
    """니케에게 거는 해로운 효과 하나.

    `effect`는 buff_manager에 그대로 주입되는 효과 dict이고 **이 객체 하나당 하나다** — 엔진이 같은
    효과의 재발동(중첩·지속 갱신)을 dict 동일성으로 알아보므로, 패턴이 순환해도 같은 dict를 다시 건다.
    """
    name: str
    stat: str
    effect: dict = field(compare=False)
    duration: float | None = None       # None = 건 패턴이 닫힐 때 풀린다


@dataclass(frozen=True)
class AttackSpec:
    coeff: float
    rule: str                   # all · random · top_atk · slot
    n: int = 0                  # random·top_atk의 N
    slots: tuple[int, ...] = () # slot의 자리 (0부터)
    pierce: bool = False
    hits: int = 1
    interval: float = 0.0
    atk: float | None = None
    ignore_taunt: bool = False
    debuffs: tuple[DebuffSpec, ...] = ()


@dataclass(frozen=True)
class DebuffCastSpec:
    """`debuff` 패턴의 발 — 대상 규칙은 공격과 같은 칸 이름이라 timeline이 같은 함수로 고른다."""
    rule: str
    debuffs: tuple[DebuffSpec, ...]
    n: int = 0
    slots: tuple[int, ...] = ()
    hits: int = 1
    interval: float = 0.0
    ignore_taunt: bool = False


@dataclass(frozen=True)
class SummonSpec:
    """`summon` 패턴의 쫄몹 무리 (docstring §쫄몹)."""
    name: str
    hp: float | None = None             # 딜로 깎는 체력. None = 처음부터 타수 기믹
    hit_hp: int = 0                     # 타수 체력 — 한 발에 1씩. 0이면 안 쓴다
    hit_hp_after: float = 0.0           # 등장 후 몇 초에 타수 기믹으로 바뀌는가 (hp와 같이 적을 때만)
    count: int = 1
    share: float = 0.0
    atk: float = 0.0                    # 순위(`enemies_top_atk`)용 — 공격이 있으면 그 공격력
    attack: AttackSpec | None = None    # atk가 채워진 공격 — 보스 공격력으로 떨어지지 않는다
    attack_at: float = 0.0
    self_destruct: bool = False


@dataclass(frozen=True)
class AttackHit:
    """이 프레임에 나가는 보스 공격·디버프 한 발. timeline이 대상·피해를 정한다
    (`spec`이 `AttackSpec`이면 공격, `DebuffCastSpec`이면 디버프)."""
    pattern: str
    spec: AttackSpec | DebuffCastSpec
    index: int                  # 몇 번째 발인가 (0부터)
    source: str = ""            # 쫄몹이 쏜 발이면 그 쫄몹 이름 (보스는 "")


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _events(v, where: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(e, str) for e in v):
        raise ValueError(f"{where}는 이벤트 이름 list여야 한다: {v!r}")
    bad = [e for e in v if e not in BOSS_EVENTS]
    if bad:
        raise ValueError(f"{where}: 보스가 낼 수 없는 이벤트 {bad} — 쓸 수 있는 것: "
                         f"{' · '.join(BOSS_EVENTS)}")
    return tuple(v)


def _unknown(raw: dict, allowed: frozenset[str], where: str) -> None:
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ValueError(f"{where}: 모르는 칸 {extra} — 쓸 수 있는 칸: {sorted(allowed)}")


def _shape(raw: dict, where: str) -> Shape:
    """좌표 모드 표적의 모양 — 원 {x, y, r} 또는 직사각형 {x, y, w, h, rotation}. 둘 중 하나만."""
    for k in ("x", "y"):
        if k not in raw:
            raise ValueError(f"{where}: 좌표 모드 표적에는 {k}가 필요하다 (화면 px, 보스 기준점이 원점)")
        if not _is_num(raw[k]):
            raise ValueError(f"{where}: {k}는 수여야 한다: {raw[k]!r}")
    circle, rect = "r" in raw, ("w" in raw or "h" in raw)
    if circle == rect:
        raise ValueError(f"{where}: 모양은 원(r)이나 직사각형(w·h) 중 하나만 적는다")
    if circle:
        if "rotation" in raw:
            raise ValueError(f"{where}: 원에는 rotation이 뜻이 없다")
        r = raw["r"]
        if not _is_num(r) or r <= 0:
            raise ValueError(f"{where}: r은 0보다 큰 수여야 한다: {r!r}")
        return Shape(x=float(raw["x"]), y=float(raw["y"]), r=float(r))
    w, h, rot = raw.get("w"), raw.get("h"), raw.get("rotation", 0.0)
    if not _is_num(w) or w <= 0 or not _is_num(h) or h <= 0:
        raise ValueError(f"{where}: 직사각형은 w·h 둘 다 0보다 큰 수여야 한다: w={w!r} h={h!r}")
    if not _is_num(rot):
        raise ValueError(f"{where}: rotation은 수(도, 반시계)여야 한다: {rot!r}")
    return Shape(x=float(raw["x"]), y=float(raw["y"]), w=float(w), h=float(h), rot=float(rot))


def _target(raw, where: str, kind: str, coord: bool = False) -> TargetSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}는 dict여야 한다: {raw!r}")
    legacy = sorted(set(raw) & {"shape", "reachable_by"})
    if legacy:
        raise ValueError(f"{where}: {legacy}는 없는 칸이다 — 좌표 모드는 원(r)·직사각형(w·h)으로, 좌표 off는 "
                         f"reach(1~5)로 적는다")
    if coord:
        stage = sorted(set(raw) & _STAGE_ONLY_FIELDS)
        if stage:
            raise ValueError(f"{where}: {stage}는 좌표 off의 칸이다 — 좌표 모드에서는 어디에 맞는가를 좌표·에임이 "
                             f"푼다(코어는 core 패턴에 x·y로)")
        _unknown(raw, (_TARGET_FIELDS - _STAGE_ONLY_FIELDS) | _COORD_TARGET_FIELDS, where)
    else:
        pos = sorted(set(raw) & _COORD_TARGET_FIELDS)
        if pos:
            raise ValueError(f"{where}: {pos}는 좌표 모드의 칸이다 — 적에 coord 블록이 없으면 좌표 off라 "
                             f"파츠에 닿는 수단을 reach(1~5)로 적는다")
        _unknown(raw, _TARGET_FIELDS, where)
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{where}: name이 필요하다")
    where = f"{where} {name!r}"
    if "hp" not in raw:
        raise ValueError(f"{where}: hp가 필요하다 (안 깨지는 표적이면 0)")
    hp, share, score = raw["hp"], raw.get("share", 1.0), raw.get("score", 0)
    core_px = raw.get("core_px", 0)
    if not _is_num(hp) or hp < 0:
        raise ValueError(f"{where}: hp는 0 이상의 수여야 한다: {hp!r}")
    if not _is_num(share) or share < 0:
        raise ValueError(f"{where}: share는 0 이상의 수여야 한다: {share!r}")
    if not _is_int(score) or score < 0:
        raise ValueError(f"{where}: score는 0 이상의 정수여야 한다: {score!r}")
    if not _is_int(core_px) or core_px < 0:
        raise ValueError(f"{where}: core_px는 0 이상의 정수여야 한다: {core_px!r}")
    reach = raw.get("reach", 0)
    if "reach" in raw:
        top = INTERRUPT_REACH_MAX if kind == "interrupt" else max(REACH_TIERS)
        if not _is_int(reach) or not 1 <= reach <= top:
            why = " (「파츠 포함」 전체기는 저지원에 안 닿는다)" if kind == "interrupt" else ""
            raise ValueError(f"{where}: {kind} reach는 1~{top} 정수여야 한다{why}: {reach!r} — "
                             + " · ".join(f"{k} {v}" for k, v in REACH_TIERS.items() if k <= top))
    shape, z = None, 0.0
    if coord:
        shape = _shape(raw, where)
        z = raw.get("z", 0.0)
        if not _is_num(z):
            raise ValueError(f"{where}: z는 수여야 한다(큰 쪽이 앞): {z!r}")
    # 좌표 모드는 share로 딜을 나누지 않는다 — 표적은 거기 떨어진 히트만 받는다(0으로 둬 새지 않게)
    return TargetSpec(name=name, hp=hp, share=0.0 if coord else share, score=score, core_px=core_px,
                      emits=_events(raw.get("emit_on_destroy"), f"{where}.emit_on_destroy"),
                      reach=reach, shape=shape, z=float(z))


def _target_rule(raw: dict, where: str, squad_size: int | None) -> dict:
    """공격·디버프 spec이 함께 쓰는 대상 규칙과 발 스케줄 칸을 검사한다."""
    if "target" not in raw:
        raise ValueError(f"{where}: 'target'가 필요하다")
    target = raw["target"]
    if not isinstance(target, str):
        raise ValueError(f"{where}: target은 문자열이어야 한다: {target!r}")
    rule, _, arg = target.partition(":")
    n, slots = 0, ()
    if rule == "all" and not arg:
        pass
    elif rule in ("random", "top_atk"):
        if not arg.isdigit() or int(arg) < 1:
            raise ValueError(f"{where}: {rule}:N의 N은 1 이상의 정수여야 한다: {target!r}")
        n = int(arg)
    elif rule == "slot":
        parts = arg.split(",")
        if not arg or not all(p.strip().isdigit() and int(p) >= 1 for p in parts):
            raise ValueError(f"{where}: slot:i,j는 1부터 센 자리 번호여야 한다: {target!r}")
        slots = tuple(sorted({int(p) - 1 for p in parts}))
        if squad_size is not None and slots[-1] >= squad_size:
            raise ValueError(f"{where}: 스쿼드가 {squad_size}명인데 {slots[-1] + 1}번 자리를 노린다")
    else:
        raise ValueError(f"{where}: 모르는 target {target!r} — all · random:N · top_atk:N · slot:i,j")
    ignore_taunt = raw.get("ignore_taunt", False)
    if not isinstance(ignore_taunt, bool):
        raise ValueError(f"{where}: ignore_taunt는 bool이어야 한다: {ignore_taunt!r}")
    if ignore_taunt and rule == "all":
        # 전체 대상은 원래 도발과 무관하다 — 적어도 아무 일도 안 일어나는 칸은 거절한다
        raise ValueError(f"{where}: all 대상은 원래 도발에 안 끌린다 — ignore_taunt가 뜻이 없다")
    hits = raw.get("hits", 1)
    if not _is_int(hits) or hits < 1:
        raise ValueError(f"{where}: hits는 1 이상의 정수여야 한다: {hits!r}")
    interval = raw.get("interval", 0.0)
    if not _is_num(interval) or interval < 0:
        raise ValueError(f"{where}: interval은 0 이상의 수여야 한다: {interval!r}")
    return dict(rule=rule, n=n, slots=slots, ignore_taunt=ignore_taunt, hits=hits, interval=interval)


def _debuffs(raw, where: str, pid: str, atk: float | None) -> tuple[DebuffSpec, ...]:
    """디버프 항목 목록을 검사해 buff_manager에 주입할 효과 dict까지 만든다."""
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{where}: debuffs는 비어 있지 않은 list여야 한다: {raw!r}")
    out: list[DebuffSpec] = []
    for j, d in enumerate(raw):
        at = f"{where}[{j}]"
        if not isinstance(d, dict):
            raise ValueError(f"{at}는 dict여야 한다: {d!r}")
        _unknown(d, _DEBUFF_FIELDS, at)
        stat = d.get("stat")
        if stat not in DEBUFF_STATS:
            raise ValueError(f"{at}: 모르는 디버프 stat {stat!r} — {' · '.join(DEBUFF_STATS)}")
        sign = DEBUFF_STATS[stat]
        eff: dict = {"type": "buff", "stat": stat, "target": "self",
                     "trigger": {"timing": [], "condition": []}, "_boss_pattern": pid}
        value = d.get("value")
        if stat == DOT_STAT:
            coeff = d.get("coeff")
            if not _is_num(coeff) or coeff <= 0:
                raise ValueError(f"{at}: dot에는 양수 coeff(%)가 필요하다: {coeff!r}")
            interval = d.get("interval", 1.0)
            if not _is_num(interval) or interval <= 0:
                raise ValueError(f"{at}: interval은 0보다 커야 한다: {interval!r}")
            if value is not None:
                raise ValueError(f"{at}: dot은 value가 아니라 coeff로 적는다")
            eff.update(_boss_coeff=coeff, _boss_interval=interval, _boss_atk=atk)
        else:
            for k in ("coeff", "interval"):
                if k in d:
                    raise ValueError(f"{at}: {k}는 dot에만 쓴다")
            if sign == 0:
                if value is not None:
                    raise ValueError(f"{at}: {stat}은 값이 없는 상태다 — value를 적지 않는다")
            else:
                if not _is_num(value) or value == 0:
                    raise ValueError(f"{at}: {stat}에는 0이 아닌 value가 필요하다: {value!r}")
                if (value > 0) != (sign > 0):
                    raise ValueError(f"{at}: {stat} {value:+g}는 이로운 쪽이다 — 디버프는 "
                                     f"{'증가' if sign > 0 else '감소'}(부호 {'+' if sign > 0 else '-'})로 적는다")
                eff["fixed_value"] = value
        duration = d.get("duration")
        if duration is not None and (not _is_num(duration) or duration <= 0):
            raise ValueError(f"{at}: duration은 0보다 커야 한다 (패턴이 닫힐 때까지면 적지 않는다): {duration!r}")
        max_stack = d.get("max_stack", 1)
        if not _is_int(max_stack) or max_stack < 1:
            raise ValueError(f"{at}: max_stack은 1 이상의 정수여야 한다: {max_stack!r}")
        irremovable = d.get("irremovable", False)
        if not isinstance(irremovable, bool):
            raise ValueError(f"{at}: irremovable은 bool이어야 한다: {irremovable!r}")
        name = d.get("name", f"{pid}·{stat}")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{at}: name은 비어 있지 않은 문자열이어야 한다: {name!r}")
        eff.update(name=name, duration=-1 if duration is None else duration, max_stack=max_stack,
                   polarity="harmful_irremovable" if irremovable else "harmful",
                   _boss_bound=duration is None)
        out.append(DebuffSpec(name=name, stat=stat, effect=eff, duration=duration))
    return tuple(out)


def _attack(raw, where: str, squad_size: int | None, pid: str) -> AttackSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: attack에는 spec dict가 필요하다: {raw!r}")
    _unknown(raw, _ATTACK_FIELDS, f"{where}.spec")
    if "coeff" not in raw:
        raise ValueError(f"{where}.spec: 'coeff'가 필요하다")
    coeff = raw["coeff"]
    if not _is_num(coeff) or coeff <= 0:
        raise ValueError(f"{where}.spec: coeff는 양수(%)여야 한다: {coeff!r}")
    rule = _target_rule(raw, f"{where}.spec", squad_size)
    pierce = raw.get("pierce", False)
    if not isinstance(pierce, bool):
        raise ValueError(f"{where}.spec: pierce는 bool이어야 한다: {pierce!r}")
    atk = raw.get("atk")
    if atk is not None and (not _is_num(atk) or atk <= 0):
        raise ValueError(f"{where}.spec: atk는 양수여야 한다: {atk!r}")
    debuffs = (_debuffs(raw["debuffs"], f"{where}.spec.debuffs", pid, atk)
               if "debuffs" in raw else ())
    return AttackSpec(coeff=coeff, pierce=pierce, atk=atk, debuffs=debuffs, **rule)


def _cast(raw, where: str, squad_size: int | None, pid: str) -> DebuffCastSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: debuff에는 spec dict가 필요하다: {raw!r}")
    _unknown(raw, _CAST_FIELDS, f"{where}.spec")
    if "debuffs" not in raw:
        raise ValueError(f"{where}.spec: 'debuffs'가 필요하다")
    rule = _target_rule(raw, f"{where}.spec", squad_size)
    return DebuffCastSpec(debuffs=_debuffs(raw["debuffs"], f"{where}.spec.debuffs", pid, None), **rule)


def _summon(raw, where: str, squad_size: int | None, pid: str) -> SummonSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: summon에는 spec dict가 필요하다: {raw!r}")
    at = f"{where}.spec"
    _unknown(raw, _SUMMON_FIELDS, at)
    name = raw.get("name", pid)
    if not isinstance(name, str) or not name:
        raise ValueError(f"{at}: name은 비어 있지 않은 문자열이어야 한다: {name!r}")
    hp, hit_hp, hit_after = raw.get("hp"), raw.get("hit_hp"), raw.get("hit_hp_after")
    if hp is None and hit_hp is None:
        raise ValueError(f"{at}: hp(마리당 체력)나 hit_hp(타수 체력) 중 하나는 필요하다 — "
                         f"쫄몹 스탯은 데이터에 없어 손으로 적는다")
    if hp is not None and (not _is_num(hp) or hp <= 0):
        raise ValueError(f"{at}: hp는 양수여야 한다: {hp!r}")
    if hit_hp is not None and (not _is_int(hit_hp) or hit_hp < 1):
        raise ValueError(f"{at}: hit_hp(타수)는 1 이상의 정수여야 한다: {hit_hp!r}")
    if hit_after is not None and hit_hp is None:
        raise ValueError(f"{at}: hit_hp 없이 hit_hp_after — 바뀔 타수 기믹이 없다")
    if hit_hp is not None and hp is not None:
        # 「체력이었다가 타수로」는 전환 시각이 있어야 뜻이 있다. 0이면 hp가 조용히 죽는 칸이 된다
        if hit_after is None or not _is_num(hit_after) or hit_after <= 0:
            raise ValueError(f"{at}: hp와 hit_hp를 같이 적으면 hit_hp_after(전환 초)가 0보다 커야 한다: "
                             f"{hit_after!r}")
    elif hit_after is not None and hit_after != 0:
        raise ValueError(f"{at}: hp 없이 hit_hp_after {hit_after!r} — 처음부터 타수라 전환 시각이 없다")
    count = raw.get("count", 1)
    if not _is_int(count) or count < 1:
        raise ValueError(f"{at}: count는 1 이상의 정수여야 한다: {count!r}")
    share = raw.get("share", 0.0)
    if not _is_num(share) or not 0 <= share <= 1:
        raise ValueError(f"{at}: share(조준 비율)는 0~1이어야 한다: {share!r}")
    atk = raw.get("atk")
    if atk is not None and (not _is_num(atk) or atk <= 0):
        raise ValueError(f"{at}: atk는 양수여야 한다: {atk!r}")
    attack = None
    if "attack" in raw:
        a = raw["attack"]
        if isinstance(a, dict) and "atk" not in a:
            if atk is None:
                # 보스 공격력으로 떨어지면 쫄몹이 보스만큼 아프게 때린다 — 조용히 틀린 값이 된다
                raise ValueError(f"{at}.attack: 쫄몹 공격력이 없다 — attack.atk나 spec.atk를 적는다")
            a = {**a, "atk": atk}
        attack = _attack(a, f"{at}.attack", squad_size, pid)
    attack_at = raw.get("attack_at", 0.0)
    if not _is_num(attack_at) or attack_at < 0:
        raise ValueError(f"{at}: attack_at은 0 이상의 수여야 한다: {attack_at!r}")
    if "attack_at" in raw and attack is None:
        raise ValueError(f"{at}: attack 없이 attack_at — 쏠 공격이 없다")
    self_destruct = raw.get("self_destruct", False)
    if not isinstance(self_destruct, bool):
        raise ValueError(f"{at}: self_destruct는 bool이어야 한다: {self_destruct!r}")
    if self_destruct and attack is None:
        raise ValueError(f"{at}: attack 없이 self_destruct — 자폭은 공격을 쏜 뒤 사라지는 것이다")
    return SummonSpec(name=name, hp=hp, hit_hp=hit_hp or 0, hit_hp_after=float(hit_after or 0.0),
                      count=count, share=share,
                      atk=attack.atk if attack is not None else (atk or 0.0),
                      attack=attack, attack_at=attack_at, self_destruct=self_destruct)


def validate(patterns, *, weapon_types: frozenset[str] | None = None,
             squad_size: int | None = None, coord: bool = False) -> list[Pattern]:
    """스크립트를 검사해 정규화한다. **잘못 적힌 것은 전부 즉시 실패시킨다.**

    칸 이름을 잘못 적어 영영 무발동이 되는 쪽이 시뮬이 안 도는 것보다 훨씬 늦게 발견된다.
    그래서 조용히 무시될 수 있는 입력 — 모르는 칸·없는 참조·영영 안 열리는 분기 — 을 남기지
    않는다. `weapon_types`를 주면 `move.weapons`를 그 집합으로 검사한다(정본은 로스터 데이터라
    이 모듈이 목록을 따로 들지 않는다). `squad_size`를 주면 `slot:` 공격이 없는 자리를
    노리는지 본다. `coord`는 좌표 모드(적에 `coord` 블록이 있다) — 표적을 좌표로 받는다(§좌표 모드).
    """
    if not isinstance(patterns, list):
        raise ValueError(f"enemy.patterns는 list여야 한다: {type(patterns).__name__}")
    out: list[Pattern] = []
    seen_kind: dict[str, int] = {}
    for i, raw in enumerate(patterns):
        where = f"enemy.patterns[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}는 dict여야 한다: {raw!r}")
        kind = raw.get("kind")
        if kind not in _KIND_FIELDS:
            raise ValueError(f"{where}: 모르는 kind {kind!r} — {' · '.join(_KIND_FIELDS)}")
        seen_kind[kind] = seen_kind.get(kind, 0) + 1
        pid = raw.get("id", f"{kind}{seen_kind[kind]}")
        if not isinstance(pid, str) or not pid:
            raise ValueError(f"{where}: id는 비어 있지 않은 문자열이어야 한다: {pid!r}")
        if pid == START:
            raise ValueError(f"{where}: id {START!r}는 전투 시작을 가리키는 예약어다")
        where = f"패턴 {pid!r}"

        allowed = _COMMON_FIELDS | _KIND_FIELDS[kind]
        extra = sorted(set(raw) - allowed)
        if extra:
            elsewhere = [k for k in extra if any(k in f for f in _KIND_FIELDS.values())]
            if elsewhere:
                raise ValueError(f"{where}: kind {kind!r}에 없는 칸 {elsewhere} "
                                 f"(쓸 수 있는 kind별 칸: {sorted(_KIND_FIELDS[kind]) or '없음'})")
            raise ValueError(f"{where}: 모르는 칸 {extra}")
        for req in _REQUIRED.get(kind, ()):
            if req not in raw:
                raise ValueError(f"{where}: kind {kind!r}에는 {req!r}가 필요하다")

        # ── after ──
        after_raw = raw.get("after", [START])
        if not isinstance(after_raw, list) or not after_raw:
            raise ValueError(f"{where}: after는 비어 있지 않은 list여야 한다 "
                             f"(빈 목록은 영영 안 열린다): {after_raw!r}")
        after: list[tuple[str, str | None]] = []
        for a in after_raw:
            if isinstance(a, str):
                node, outcome = a, None
            elif isinstance(a, dict):
                _unknown(a, frozenset({"node", "outcome"}), f"{where}.after 항목")
                node, outcome = a.get("node"), a.get("outcome")
                if outcome is not None and outcome not in _OUTCOME_NEEDS:
                    raise ValueError(
                        f"{where}.after: outcome {outcome!r}로는 열 수 없다 — "
                        f"{' · '.join(_OUTCOME_NEEDS)} 중 하나 ('end'는 전투가 끝난 뒤라 영영 안 열린다)")
            else:
                raise ValueError(f"{where}.after 항목은 문자열이나 dict여야 한다: {a!r}")
            if not isinstance(node, str) or not node:
                raise ValueError(f"{where}.after: node가 필요하다: {a!r}")
            if node == START and outcome is not None:
                raise ValueError(f"{where}.after: {START!r}에는 outcome을 붙일 수 없다")
            after.append((node, outcome))

        delay = raw.get("delay", 0.0)
        if not _is_num(delay) or delay < 0:
            raise ValueError(f"{where}: delay는 0 이상의 수여야 한다: {delay!r}")
        repeat = raw.get("repeat", 1)
        if not _is_int(repeat) or repeat < 0:
            raise ValueError(f"{where}: repeat는 0 이상의 정수여야 한다 (0 = 무제한): {repeat!r}")
        note = raw.get("note", "")
        if not isinstance(note, str):
            raise ValueError(f"{where}: note는 문자열이어야 한다: {note!r}")

        # ── until ──
        until = raw.get("until") or {}
        if not isinstance(until, dict):
            raise ValueError(f"{where}: until은 dict여야 한다: {until!r}")
        _unknown(until, _UNTIL_FIELDS, f"{where}.until")
        until_time = until.get("time")
        if until_time is not None and (not _is_num(until_time) or until_time <= 0):
            # 0초 구간은 순환 스크립트에서 한 프레임에 무한히 돌 수 있고, 「켜졌다 같은 프레임에
            # 꺼지는 상태」는 뜻이 없다.
            raise ValueError(f"{where}: until.time은 0보다 커야 한다: {until_time!r}")
        until_cleared = until.get("targets_cleared", False)
        if not isinstance(until_cleared, bool):
            raise ValueError(f"{where}: until.targets_cleared는 bool이어야 한다: {until_cleared!r}")
        ua_raw = until.get("after", [])
        if not isinstance(ua_raw, list):
            raise ValueError(f"{where}: until.after는 list여야 한다: {ua_raw!r}")
        until_after: list[tuple[str, float]] = []
        for a in ua_raw:
            if not isinstance(a, dict):
                raise ValueError(f"{where}.until.after 항목은 {{'node', 'delay'}} dict여야 한다: {a!r}")
            _unknown(a, frozenset({"node", "delay"}), f"{where}.until.after 항목")
            node, d = a.get("node"), a.get("delay", 0.0)
            if not isinstance(node, str) or not node:
                raise ValueError(f"{where}.until.after: node가 필요하다: {a!r}")
            if node == pid:
                raise ValueError(f"{where}.until.after: 자기 자신을 따라 끝날 수는 없다")
            if node == START:
                raise ValueError(f"{where}.until.after: {START!r}는 열리기 전에 끝나 있어 "
                                 f"영영 따라 끝나지 않는다")
            if not _is_num(d) or d < 0:
                raise ValueError(f"{where}.until.after: delay는 0 이상의 수여야 한다: {d!r}")
            until_after.append((node, d))

        kw: dict = {}
        # ── kind별 ──
        if kind == "buff":
            en = raw["enemy"]
            if not isinstance(en, dict) or not en:
                raise ValueError(f"{where}: enemy는 def_mult·def_add·received_dmg_pct를 담은 dict여야 한다: {en!r}")
            _unknown(en, _BUFF_FIELDS, f"{where}.enemy")
            m, a = en.get("def_mult", 1), en.get("def_add", 0)
            if not _is_num(m) or m < 0:
                raise ValueError(f"{where}.enemy: def_mult는 0 이상의 수여야 한다: {m!r}")
            if not _is_num(a):
                raise ValueError(f"{where}.enemy: def_add는 수여야 한다: {a!r}")
            kw.update(def_mult=m, def_add=a)
            if "received_dmg_pct" in en:
                rd = en["received_dmg_pct"]
                if not _is_num(rd) or rd == 0:
                    raise ValueError(f"{where}.enemy: received_dmg_pct는 0이 아닌 수(%)여야 한다: {rd!r}")
                kw["enemy_effect"] = {
                    "name": f"{pid}·received_dmg_pct", "type": "buff", "stat": "received_dmg_pct",
                    "fixed_value": rd, "duration": -1, "max_stack": 1, "polarity": "beneficial",
                    "target": "self", "trigger": {"timing": [], "condition": []},
                    "_boss_pattern": pid, "_boss_bound": True}
            irremovable = raw.get("irremovable", False)
            if not isinstance(irremovable, bool):
                raise ValueError(f"{where}: irremovable은 bool이어야 한다: {irremovable!r}")
            kw["irremovable"] = irremovable
        elif kind == "core":
            cp = raw["core_px"]
            if not _is_int(cp) or cp <= 0:
                raise ValueError(f"{where}: core_px는 양의 정수여야 한다: {cp!r}")
            kw["core_px"] = cp
            if "x" in raw or "y" in raw:
                if not coord:
                    raise ValueError(f"{where}: 코어 위치(x·y)는 좌표 모드의 칸이다 — 좌표 off의 코어는 늘 조준점에 있다")
                cx, cy = raw.get("x", 0.0), raw.get("y", 0.0)
                if not _is_num(cx) or not _is_num(cy):
                    raise ValueError(f"{where}: 코어 x·y는 수여야 한다: {cx!r}, {cy!r}")
                kw["core_at"] = (float(cx), float(cy))
        elif kind == "shield":
            code = raw["code"]
            if code not in _CODE_ADVANTAGE:
                raise ValueError(f"{where}: 모르는 속성 코드 {code!r} — "
                                 f"{' · '.join(_CODE_ADVANTAGE)}")
            kw["code"] = code
        elif kind == "move":
            # 적정거리를 바꾼다 — 무기군 목록(`weapons`, 좌표 없는 근사)이나 보스 거리(`distance`) 중 **하나**
            if ("weapons" in raw) == ("distance" in raw):
                raise ValueError(f"{where}: move에는 weapons(적정거리 무기군 목록)나 distance(보스 거리) 중 "
                                 f"하나만 적는다")
            if "distance" in raw:
                dv = raw["distance"]
                if not _is_num(dv) or dv <= 0:
                    raise ValueError(f"{where}: distance는 0보다 큰 수여야 한다: {dv!r}")
                kw["distance"] = float(dv)
            else:
                ws = raw["weapons"]
                if not isinstance(ws, list) or not all(isinstance(w, str) for w in ws):
                    raise ValueError(f"{where}: weapons는 무기군 문자열 list여야 한다: {ws!r}")
                if weapon_types is not None:
                    bad = [w for w in ws if w not in weapon_types]
                    if bad:
                        raise ValueError(f"{where}: 모르는 무기군 {bad} — {' · '.join(sorted(weapon_types))}")
                kw["weapons"] = tuple(ws)
        elif kind == "attack":
            kw["attack"] = _attack(raw.get("spec"), where, squad_size, pid)
        elif kind == "debuff":
            kw["cast"] = _cast(raw.get("spec"), where, squad_size, pid)
        elif kind == "summon":
            kw["summon"] = _summon(raw.get("spec"), where, squad_size, pid)
        elif kind in RESERVED_KINDS:
            if "spec" in raw and not isinstance(raw["spec"], dict):
                raise ValueError(f"{where}: spec은 dict여야 한다: {raw['spec']!r}")
        if kind in _TARGET_KINDS:
            tr = raw["targets"]
            if not isinstance(tr, list) or not tr:
                raise ValueError(f"{where}: 표적 없는 {kind} — targets가 비었다")
            targets = [_target(x, f"{where}.targets[{j}]", kind, coord) for j, x in enumerate(tr)]
            names = [x.name for x in targets]
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                raise ValueError(f"{where}: 표적 이름 중복 {dup}")
            kw["targets"] = tuple(targets)
        if until_cleared:
            if kind not in _TARGET_KINDS and kind != "summon":
                raise ValueError(f"{where}: until.targets_cleared는 표적이 있는 kind"
                                 f"({' · '.join(sorted(_TARGET_KINDS | {'summon'}))})에만 쓴다")
            if kind in _TARGET_KINDS and not any(x.breakable for x in kw["targets"]):
                raise ValueError(f"{where}: 깰 수 있는 표적(hp > 0)이 없는데 until.targets_cleared — "
                                 f"영영 cleared로 안 끝난다")

        out.append(Pattern(
            idx=i, id=pid, kind=kind, after=tuple(after), delay=delay,
            until_time=until_time, until_cleared=until_cleared,
            until_after=tuple(until_after), repeat=repeat,
            emit=_events(raw.get("emit"), f"{where}.emit"),
            emit_end=_events(raw.get("emit_end"), f"{where}.emit_end"),
            note=note, **kw))

    # ── 서로 참조 ──
    by_id: dict[str, Pattern] = {}
    for p in out:
        if p.id in by_id:
            raise ValueError(f"패턴 id 중복: {p.id!r} (id를 안 적었다면 자동 이름끼리 부딪힌 것이다)")
        by_id[p.id] = p
    for p in out:
        for node, outcome in p.after:
            if node == START:
                continue
            if node not in by_id:
                raise ValueError(f"패턴 {p.id!r}.after: 없는 패턴 {node!r}")
            if outcome is not None:
                q = by_id[node]
                has = {"cleared": q.until_cleared, "expired": q.until_time is not None,
                       "followed": bool(q.until_after)}[outcome]
                if not has:
                    raise ValueError(
                        f"패턴 {p.id!r}.after: {node!r}는 {_OUTCOME_NEEDS[outcome]}이 없어 "
                        f"{outcome!r}로 끝날 수 없다 — 이 분기는 영영 안 열린다")
        for node, _ in p.until_after:
            if node not in by_id:
                raise ValueError(f"패턴 {p.id!r}.until.after: 없는 패턴 {node!r}")

    # 전투 시작에서 이어지지 않는 패턴은 무엇을 적었든 영영 안 열린다.
    reach = {START}
    grew = True
    while grew:
        grew = False
        for p in out:
            if p.id not in reach and any(node in reach for node, _ in p.after):
                reach.add(p.id)
                grew = True
    dead = [p.id for p in out if p.id not in reach]
    if dead:
        raise ValueError(f"전투 시작에서 이어지지 않아 영영 안 열리는 패턴: {dead}")
    # 적정거리는 무기군 목록이나 보스 거리 중 한 축으로만 본다 — 섞으면 거리 구간이 끝나는 순간 어느 쪽으로
    # 돌아가는지가 조용한 결과 차이가 된다
    moves = {p.distance is None for p in out if p.kind == "move"}
    if len(moves) > 1:
        raise ValueError("move 패턴이 weapons와 distance를 섞어 쓴다 — 적정거리를 무기군 목록으로 볼지 "
                         "보스 거리로 볼지 스크립트 하나에서는 하나로 정한다")
    if coord:
        # 좌표 모드의 히트·에임 컨트롤은 표적을 **이름으로** 가리킨다 — 스크립트 전체에서 하나여야 한다
        owner: dict[str, str] = {}
        for p in out:
            for x in p.targets:
                if x.name in owner and owner[x.name] != p.id:
                    raise ValueError(f"좌표 모드 표적 이름 {x.name!r}가 패턴 {owner[x.name]!r}·{p.id!r}에 겹친다 — "
                                     f"히트와 에임이 이름으로 표적을 찾으므로 스크립트 전체에서 하나여야 한다")
                owner[x.name] = p.id
    return out


@dataclass(frozen=True)
class CoordSpec:
    """좌표 모드 적 블록(`enemy["coord"]`). 정본: docstring §좌표 모드."""
    auto_aim: tuple[float, float] | None = None     # 자동 에임 위치. None = 열린 코어 중심, 없으면 (0, 0)
    explosion_scale: float = DEFAULT_EXPLOSION_SCALE
    pierce_px: float = DEFAULT_PIERCE_PX


def boss_mode(enemy: dict) -> str:
    """적 dict의 모드(docstring §모드) — SIMPLE · PATTERN(좌표 off) · COORD(좌표 on).

    모드마다 제 칸이 있어 남의 칸을 적으면 즉시 실패한다 — 켜 둔 채 조용히 무시되면 그 칸이 계산에 들어갔다고
    믿게 된다. 좌표(`coord`)는 패턴 모드의 스위치라 패턴이 없으면 다룰 표적·코어가 없고, 파츠 파괴 주기
    (`part_break_interval`)는 간단 모드의 칸이라 패턴 모드에서는 파괴가 표적이 실제로 깨질 때 나간다."""
    has_patterns = bool(enemy.get("patterns"))
    if has_patterns and enemy.get("part_break_interval"):
        raise ValueError("enemy.part_break_interval은 간단 모드의 칸이다 — 패턴 모드에서는 파츠 파괴가 표적이 "
                         "실제로 깨질 때 나간다(표적의 emit_on_destroy). 주기를 빼거나 패턴을 뺀다")
    if enemy.get("coord") is not None:
        if not has_patterns:
            raise ValueError("enemy.coord는 패턴 모드의 스위치다 — patterns가 없는 간단 모드에는 좌표가 다룰 "
                             "표적·코어가 없다. 표적·코어를 패턴으로 적고 켜거나 coord를 뺀다")
        return COORD
    return PATTERN if has_patterns else SIMPLE


def penalty_parts(patterns: list[Pattern]) -> frozenset[str]:
    """안 깨면 벌칙 분기가 오는 parts 패턴 id — `until.targets_cleared`가 있고(깨면 막을 수 있다), 그 패턴의 실패
    종료(expired·followed)를 `after`로 받는 패턴이 있다. 레이어 2가 저지원과 함께 겨눈다(docstring §좌표 모드)."""
    fails = {node for p in patterns for node, outcome in p.after if outcome in _FAIL_OUTCOMES}
    return frozenset(p.id for p in patterns if p.kind == "parts" and p.until_cleared and p.id in fails)


def coord_spec(raw) -> CoordSpec | None:
    """`enemy["coord"]` → CoordSpec. None이면 좌표 off. 모르는 칸·잘못된 값은 즉시 실패."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"enemy.coord는 dict여야 한다({{}}면 기본값으로 좌표 모드): {raw!r}")
    _unknown(raw, COORD_FIELDS, "enemy.coord")
    aa = raw.get("auto_aim")
    if aa is not None:
        if (not isinstance(aa, list) or len(aa) != 2 or not all(_is_num(v) for v in aa)):
            raise ValueError(f"enemy.coord.auto_aim은 [x, y]여야 한다: {aa!r}")
        aa = (float(aa[0]), float(aa[1]))
    es, pp = raw.get("explosion_scale", DEFAULT_EXPLOSION_SCALE), raw.get("pierce_px", DEFAULT_PIERCE_PX)
    if not _is_num(es) or es <= 0:
        raise ValueError(f"enemy.coord.explosion_scale은 0보다 큰 수(px / CDN 폭발 범위 단위)여야 한다: {es!r}")
    if not _is_num(pp) or pp <= 0:
        raise ValueError(f"enemy.coord.pierce_px는 0보다 큰 수(관통 원 반지름 px)여야 한다: {pp!r}")
    return CoordSpec(auto_aim=aa, explosion_scale=float(es), pierce_px=float(pp))


# ── 실행 ──────────────────────────────────────────────────────────────────

@dataclass
class _Target:
    spec: TargetSpec
    dealt: float = 0.0
    destroyed: bool = False
    extra_hits: int = 0         # 다중 타격으로 따로 맞은 발 수 (reach 표적)
    extra_dealt: float = 0.0    # 그 발들이 넣은 딜 — 초과분 포함. 파츠면 총딜에, 저지원이면 총딜 밖에 들어간 값
    hits: int = 0               # 좌표 모드 — 이 표적에 떨어진 히트 수 (dealt가 그 딜, 초과분 포함)


@dataclass(frozen=True)
class GeomTarget:
    """좌표 모드의 산 표적 하나 — 히트가 이름으로 찾아온다."""
    name: str
    kind: str       # "parts" · "interrupt"
    shape: Shape


class Geometry:
    """좌표 모드 한 순간의 산 표적·코어·자동 에임. `BossScript._apply()`가 **산 집합이 바뀔 때만** 새로 만든다 —
    착탄 확률 캐시를 들고 있어서 같은 조준점·탄착군이면 적분을 다시 하지 않는다. 정본: docstring §좌표 모드."""

    def __init__(self, targets: tuple[GeomTarget, ...], must_break: tuple[str, ...], core: Shape | None,
                 auto_aim: tuple[float, float], spec: CoordSpec):
        self.targets = targets              # 앞 → 뒤
        self.shapes = tuple(g.shape for g in targets)
        self.must_break = must_break        # 깨야 하는 산 표적(저지원 · 벌칙 파츠) — 스크립트에 적힌 순서(레이어 2가 첫째를 겨눈다)
        self.core = core
        self.auto_aim = auto_aim
        self.explosion_scale = spec.explosion_scale
        self.pierce_px = spec.pierce_px
        self._by_name = {g.name: g for g in targets}
        self._cache: dict[tuple, Landing] = {}

    def center(self, name: str) -> tuple[float, float] | None:
        """산 표적(또는 "core" = 열린 코어)의 중심. 없으면 None."""
        if name == "core":
            return (self.core.x, self.core.y) if self.core is not None else None
        g = self._by_name.get(name)
        return (g.shape.x, g.shape.y) if g is not None else None

    def kind(self, name: str) -> str:
        """산 표적의 종류 — "parts" · "interrupt", 열린 코어면 "core". 없으면 ""."""
        if name == "core":
            return "core" if self.core is not None else ""
        g = self._by_name.get(name)
        return g.kind if g is not None else ""

    def landing(self, ax: float, ay: float, radius: float, grow: float = 0.0) -> Landing:
        key = (round(ax, 6), round(ay, 6), round(radius, 6), round(grow, 6))
        got = self._cache.get(key)
        if got is None:
            got = self._cache[key] = landing(ax, ay, radius, self.shapes, self.core, grow)
        return got


@dataclass
class _Add:
    """쫄몹 한 마리. `id`가 buff_manager의 적 효과 대상 이름이다."""
    id: str
    name: str
    spec: SummonSpec
    order: int                          # 등장 순번 — 동률일 때 먼저 나온 쪽이 앞
    spawn_t: float = 0.0                # 등장 시각 — 타수 기믹 전환(`hit_hp_after`)을 여기서 잰다
    dealt: float = 0.0
    hits: int = 0                       # 타수 기믹으로 받은 발 수
    gone: str = ""                      # "" = 살아 있음 · "처치" · "자폭" · "퇴장"
    pending: list[float] = field(default_factory=list)   # 아직 안 나간 발의 예정 시각
    fired: int = 0

    @property
    def alive(self) -> bool:
        return not self.gone


@dataclass
class _Run:
    """패턴 하나의 실행 상태. 다시 열리면 표적·막은 딜이 새로 시작한다."""
    p: Pattern
    active: bool = False
    start_t: float = 0.0
    opens: int = 0
    consumed_after: list[int] = field(default_factory=list)
    consumed_until: list[int] = field(default_factory=list)
    targets: list[_Target] = field(default_factory=list)
    blocked: float = 0.0
    # attack·debuff — 아직 안 나간 발의 예정 시각 · 나간 발 수 · 니케 체력에 준 피해 · 붙은 디버프 수
    pending: list[float] = field(default_factory=list)
    fired: int = 0
    hp_dealt: float = 0.0
    debuffed: int = 0
    # buff — 니케가 해제했다(`dispel`). 이번에 열린 동안 효과를 잃는다
    dispelled: bool = False
    # summon — 이번에 열린 동안의 쫄몹
    adds: list[_Add] = field(default_factory=list)


class BossScript:
    """스케줄러 + 적 상태 오버레이 + 딜 게이트 + 표적 체력 풀.

    `superior(caster, code)` — 그 캐스터의 딜이 `code` 속성보호막을 통과하는가. 로스터 코드
    상성이거나 `element_code_override` 버프로 우월해졌거나다(인게임이 후자도 인정한다). 후자가
    버프라 호출 시점에 봐야 해서 timeline이 콜백으로 넘긴다.
    """

    def __init__(self, patterns: list[Pattern], enemy: dict,
                 superior: Callable[[str, str], bool], rng: random.Random | None = None):
        self._superior = superior
        # 쫄몹이 있을 때 무작위 적 대상(`enemies_random:N`)을 고르는 난수열 — timeline이 보스 공격과 같은 것을 준다
        self._rng = rng if rng is not None else random.Random(0)
        self._enemy = enemy     # 순위 대상이 보스 공격력·방어력을 읽는다(같은 dict 객체)
        self._runs = [_Run(p) for p in patterns]
        # 좌표 모드 — 적에 coord 블록이 있다. 표적은 이름으로 히트를 받고 에임·탄 분포가 어디에 맞는지 푼다
        self.coord = coord_spec(enemy.get("coord"))
        if self.coord is not None and any(x.shape is None for p in patterns for x in p.targets):
            raise ValueError("좌표 모드(enemy.coord)인데 표적에 좌표가 없다 — validate(coord=True)로 검사한 패턴을 준다")
        if self.coord is None and any(x.shape is not None for p in patterns for x in p.targets):
            raise ValueError("표적에 좌표가 있는데 적에 coord 블록이 없다")
        # 표적 이름 → 종류(좌표 모드의 히트 회계 — 파츠는 총딜, 저지원은 총딜 밖)
        self.target_kinds: dict[str, str] = {x.name: p.kind for p in patterns for x in p.targets}
        # 안 깨면 벌칙 분기가 오는 parts 패턴 — 좌표 모드의 레이어 2가 저지원과 함께 겨눈다
        self._penalty_parts = penalty_parts(patterns)
        self.geom: Geometry | None = None
        self._geom_sig: tuple | None = None
        # 저지원에 들어간 딜(시전자별) — 좌표 모드의 저지원 히트와 좌표 off의 reach 저지원 히트. **총딜에 없다**
        # (유저 결정 2026-09-18 · 2026-09-19)
        self.interrupt_dealt: dict[str, float] = {}
        # 기본 상태 — 패턴이 없을 때의 적. 매 프레임 여기서 출발해 덮어쓴다.
        self._base_def = enemy.get("def", DEFAULT_ENEMY_DEF)
        self._base_core = enemy.get("core_px", 0)
        self._base_parts = enemy.get("has_parts", False)
        self._base_weapons = enemy.get("optimal_range_weapons", [])
        self._base_distance = enemy.get("distance")
        moves = [r.p for r in self._runs if r.p.kind == "move"]
        if moves and moves[0].distance is not None and self._base_weapons:
            raise ValueError("move 패턴은 보스 거리(distance)로 적었는데 적의 optimal_range_weapons가 있다 — "
                             "적정거리를 한 축으로만 적는다")
        if moves and moves[0].distance is None and self._base_distance is not None:
            raise ValueError("적에 보스 거리(distance)가 있는데 move 패턴이 무기군 목록(weapons)으로 적혀 있다 — "
                             "거리가 있으면 move도 distance로 적는다")
        # 노드별 종료 기록 (시각, outcome). START는 첫 프레임에 한 번 끝난다.
        self._ends: dict[str, list[tuple[float, str]]] = {p.id: [] for p in patterns}
        self._ends[START] = []
        # 흡수 자리에서 깨진 표적의 이벤트는 다음 프레임 맨 앞에서 걷는다(최대 한 프레임 지연).
        # `_dot_events`를 다음 프레임 시작에 수거하는 것과 같은 규약이다.
        self._carry: list[str] = []
        # 이번 프레임의 게이트·흡수 대상 — `_apply()`가 채운다
        self._vanish: list[_Run] = []
        self._shields: list[_Run] = []
        self._absorbers: list[_Run] = []
        self._reach_runs: list[_Run] = []     # 산 reach 파츠가 있는 parts 패턴 — `part_hits()`가 훑는다
        self._ireach_runs: list[_Run] = []    # 산 reach 저지원이 있는 interrupt 패턴 — `interrupt_hits()`가 훑는다

        # 보스가 사라졌는가. 딜 게이트는 `admit()`이 직접 하고, timeline은 이 값을 state에 실어
        # 무기 사격의 버스트 게이지를 거른다(평타가 빗나가면 그 게이지도 안 찬다).
        self.vanished = False
        # 이번 프레임에 나가는 보스 공격·디버프 발(패턴 선언 순). `begin_frame()`이 채우고 timeline이 비운다.
        self.attacks: list[AttackHit] = []
        # buff_manager에 주입한 효과의 수명 — `begin_frame()`이 채우고 timeline이 **곧바로** 처리해 비운다.
        #   released       이 프레임에 효과를 풀 패턴 id (닫힘·해제). 「패턴이 닫힐 때 풀리는」 디버프와 보스 버프
        #   enemy_effects  이 프레임에 열린 buff 패턴이 적에게 붙일 효과 dict (`received_dmg_pct`)
        # 보스 상태를 확정하는 자리(프레임 맨 앞)라 bm.tick보다 먼저다 — 이 프레임의 딜이 전부 같은 값을 읽는다.
        self.released: list[str] = []
        self.enemy_effects: list[dict] = []
        # 해제는 니케의 스킬 발동 도중에 일어난다 — 적 상태는 프레임 안에서 안 바꾸고 다음 프레임 맨 앞에 반영한다
        self._dispel_carry: list[str] = []
        self._run_by_id = {r.p.id: r for r in self._runs}
        self.log: list[BossLogEntry] = []
        self.score = 0
        self.unmodeled: list[str] = []

        # ── 쫄몹 ──
        self.has_summons = any(p.kind == "summon" for p in patterns)
        # 이 프레임 맨 앞의 적 수(보스 1 + 산 쫄몹). 프레임 안에서 쫄몹이 죽어도 다음 프레임에 반영한다
        self.enemy_count = 1
        # 사라진 쫄몹 id — timeline이 스쿼드 통지 뒤에 적 효과 대상에서 지우고 비운다
        self.gone: list[str] = []
        # 쫄몹에 들어간 딜(시전자별) · 넘친 딜과 이미 사라진 쫄몹에 간 딜 — 총딜에 없다
        self.add_dealt: dict[str, float] = {}
        self.add_overkill = 0.0
        self._add_seq = 0

    # ── 프레임 맨 앞 ──

    def begin_frame(self, t: float, enemy: dict) -> list[str]:
        """전이를 확정하고 적 상태를 `enemy`에 기록한다. 스쿼드에 쏠 이벤트를 돌려준다.

        **이 프레임의 누구도 적 상태를 읽기 전에** 불러야 한다 — 코어 직경·방어력·적정거리가
        프레임 안에서 어긋나면 안 된다. 같은 시각에 두 번 불러도 결과가 같다(전이가 이미 끝나
        있으면 아무것도 안 한다).
        """
        events, self._carry = self._carry, []
        self.released.extend(self._dispel_carry)
        self._dispel_carry = []
        if not self._ends[START]:
            self._ends[START].append((t, START))
        self._close_due(t, events)
        # 여는 건 한 바퀴면 된다. 열린 패턴은 같은 프레임에 끝날 수 없다 — 표적은 새로 차고,
        # until.time은 0보다 크고, until.after는 열린 뒤의 종료만 센다. 그래서 열기가 또 다른
        # 종료를 낳지 않고 순환 스크립트도 한 프레임에 갇히지 않는다.
        for run in self._runs:
            if not run.active:
                trig = self._ready(run, t)
                if trig is not None:
                    self._open(run, t, trig, events)
        # 공격은 열고 난 뒤에 걷는다 — 이 프레임에 열린 공격의 첫 발도 이 프레임에 나간다.
        for run in self._runs:
            while run.active and run.pending and t >= run.pending[0] - _EPS:
                run.pending.pop(0)
                self.attacks.append(AttackHit(pattern=run.p.id, spec=run.p.shots, index=run.fired))
                run.fired += 1
            if run.active and run.p.summon is not None and run.p.summon.attack is not None:
                self._add_attacks(run, t, events)
        self._apply(enemy)
        return events

    def _add_attacks(self, run: _Run, t: float, events: list[str]) -> None:
        """산 쫄몹마다 이 프레임에 나갈 발을 걷는다. 자폭이면 마지막 발을 쏜 프레임에 사라진다(사망)."""
        s = run.p.summon
        for add in run.adds:
            while add.alive and add.pending and t >= add.pending[0] - _EPS:
                add.pending.pop(0)
                self.attacks.append(AttackHit(pattern=run.p.id, spec=s.attack, index=add.fired,
                                              source=add.name))
                add.fired += 1
                run.fired += 1
            if add.alive and s.self_destruct and not add.pending and add.fired:
                self._retire(run, add, "자폭", t, events)

    def note_attack(self, pattern: str, hp_dealt: float) -> None:
        """timeline이 한 발을 처리한 뒤 니케 체력에 들어간 피해를 돌려준다(종료 로그용)."""
        self._run_by_id[pattern].hp_dealt += hp_dealt

    def note_debuff(self, pattern: str, n: int) -> None:
        """timeline이 이 패턴의 발로 니케에게 붙인 디버프 수를 돌려준다(종료 로그용)."""
        self._run_by_id[pattern].debuffed += n

    def dispel(self, n: int, t: float) -> list[str]:
        """니케의 「적 이로운 효과 해제 N개」. 끈 buff 패턴 id를 돌려준다.

        열린 `buff` 패턴 하나가 이로운 효과 하나다. **나중에 두른 것부터** 끈다(⬜ 인게임 미확인 — 보호막
        비관통 순서와 같은 잠정). `irremovable`은 건너뛴다. 꺼진 패턴은 이번에 열린 동안 방어력 오버레이와
        받는 대미지를 둘 다 잃고, 구간(종료 조건)은 그대로 간다. 효과는 **다음 프레임 맨 앞에** 풀린다 —
        니케 스킬 발동 도중이라 적 상태를 프레임 안에서 바꾸지 않는다."""
        live = [r for r in self._runs if r.active and r.p.kind == "buff"
                and not r.p.irremovable and not r.dispelled]
        live.sort(key=lambda r: (r.start_t, r.p.idx), reverse=True)
        out = []
        for r in live[:max(n, 0)]:
            r.dispelled = True
            out.append(r.p.id)
            self._dispel_carry.append(r.p.id)
            self.log.append(BossLogEntry(t=t, pattern=r.p.id, kind=r.p.kind, event="dispel",
                                         detail="니케가 이로운 효과 해제"))
        return out

    def _matching(self, node: str, outcome: str | None) -> list[float]:
        return [te for te, oc in self._ends[node] if outcome is None or oc == outcome]

    def _ready(self, run: _Run, t: float) -> str | None:
        p = run.p
        if p.repeat and run.opens >= p.repeat:
            return None
        for i, (node, outcome) in enumerate(p.after):
            ends = self._matching(node, outcome)
            k = run.consumed_after[i] if run.opens else 0
            # 소비하지 않은 종료 중 가장 이른 것부터 지연을 잰다
            if len(ends) > k and t >= ends[k] + p.delay - _EPS:
                return node if outcome is None else f"{node}({outcome})"
        return None

    def _close_due(self, t: float, events: list[str]) -> None:
        """이 프레임에 끝나는 패턴을 사유와 함께 확정하고 닫는다.

        cleared·expired는 서로와 무관하게 정해지지만 followed는 **같은 프레임의 다른 종료**에
        기대므로 퍼뜨려서 구한다. 같은 프레임에 둘 다 성립하면 followed가 expired를 이긴다.
        """
        due: dict[str, str] = {}
        for run in self._runs:
            if not run.active:
                continue
            p = run.p
            if p.until_cleared and (all(not a.alive for a in run.adds) if p.summon is not None
                                    else all(x.destroyed for x in run.targets if x.spec.breakable)):
                due[p.id] = "cleared"
            elif p.until_time is not None and t >= run.start_t + p.until_time - _EPS:
                due[p.id] = "expired"
        grew = True
        while grew:
            grew = False
            for run in self._runs:
                p = run.p
                if not run.active or due.get(p.id) in ("cleared", "followed"):
                    continue
                for i, (node, delay) in enumerate(p.until_after):
                    ends, k = self._ends[node], run.consumed_until[i]
                    if len(ends) > k:
                        te = ends[k][0]
                    elif node in due:
                        te = t          # 이 프레임에 끝난다
                    else:
                        continue
                    if t >= te + delay - _EPS:
                        due[p.id] = "followed"
                        grew = True
                        break
        for run in self._runs:
            if run.p.id in due:
                self._close(run, t, due[run.p.id], events)

    def _open(self, run: _Run, t: float, trigger: str, events: list[str]) -> None:
        p = run.p
        run.active = True
        run.start_t = t
        run.opens += 1
        run.consumed_after = [len(self._matching(n, o)) for n, o in p.after]
        run.consumed_until = [len(self._ends[n]) for n, _ in p.until_after]
        run.targets = [_Target(s) for s in p.targets]
        run.blocked = 0.0
        run.dispelled = False
        if p.shots is not None:
            run.pending = [t + i * p.shots.interval for i in range(p.shots.hits)]
            run.fired = 0
            run.hp_dealt = 0.0
            run.debuffed = 0
        if p.enemy_effect is not None:
            self.enemy_effects.append(p.enemy_effect)
        if p.kind in RESERVED_KINDS and p.id not in self.unmodeled:
            self.unmodeled.append(p.id)
        detail = f"after {trigger}" + (f" +{p.delay:g}s" if p.delay else "")
        if p.kind in RESERVED_KINDS:
            detail += " · 효과 모델 없음"
        tiers = [f"{s.name} {s.reach}" for s in p.targets if s.reach]
        if tiers:
            detail += f" · 위치 단계 {' · '.join(tiers)}"
        if p.summon is not None:
            s = p.summon
            run.adds = []
            run.fired = 0
            run.hp_dealt = 0.0
            run.debuffed = 0
            for k in range(s.count):
                self._add_seq += 1
                first = t + s.attack_at
                pending = ([first + i * s.attack.interval for i in range(s.attack.hits)]
                           if s.attack is not None else [])
                name = f"{s.name}#{k + 1}"
                run.adds.append(_Add(id=f"{ADD_PREFIX}{p.id}#{k + 1}", name=name, spec=s,
                                     order=self._add_seq, spawn_t=t, pending=pending))
            # 등장은 쫄몹마다 「적 등장」이다 (⬜ 한꺼번에 나와도 마릿수만큼 발동하는지 인게임 미확인)
            events.extend(["event:enemy_spawn"] * s.count)
            body = f"체력 {s.hp:,.0f}" if s.hp is not None else ""
            if s.hit_hp:
                body += f" → {s.hit_hp_after:g}초 뒤 타수 {s.hit_hp}" if body else f"타수 {s.hit_hp}"
            detail += f" · {s.name} {s.count}기 등장({body}"
            detail += f" · 조준 {s.share:g})" if s.share else ")"
        self.log.append(BossLogEntry(t=t, pattern=p.id, kind=p.kind, event="start",
                                     detail=detail))
        events.extend(p.emit)

    def _close(self, run: _Run, t: float, outcome: str, events: list[str]) -> None:
        p = run.p
        run.active = False
        self._ends[p.id].append((t, outcome))
        bits = []
        breakable = [x for x in run.targets if x.spec.breakable]
        if breakable:
            bits.append(f"표적 {sum(x.destroyed for x in breakable)}/{len(breakable)} 파괴")
        extra = sum(x.extra_hits for x in run.targets)
        if extra:
            bits.append(f"다중 타격 {extra}발 · 딜 {round(sum(x.extra_dealt for x in run.targets)):,}"
                        + (" (총딜 밖)" if p.kind == "interrupt" else ""))
        landed = sum(x.hits for x in run.targets)
        if landed:
            bits.append(f"명중 {landed}발 · 딜 {round(sum(x.dealt for x in run.targets)):,}"
                        + (" (총딜 밖)" if p.kind == "interrupt" else ""))
        if p.kind in ("shield", "vanish"):
            bits.append(f"막은 딜 {round(run.blocked):,}")
        if p.summon is not None:
            # 닫힐 때 산 쫄몹은 퇴장 — 사망이 아니라 enemy_death를 안 쏜다(유저 확인 2026-09-16)
            for add in run.adds:
                if add.alive:
                    self._retire(run, add, "퇴장", t, events, log=False)
                add.pending = []
            n = {why: sum(a.gone == why for a in run.adds) for why in ("처치", "자폭", "퇴장")}
            bits.append(" · ".join(f"{why} {k}" for why, k in n.items() if k) + f" / {len(run.adds)}기")
            took = sum(a.dealt if a.spec.hp is None else min(a.dealt, a.spec.hp) for a in run.adds)
            bits.append(f"받은 딜 {round(took):,}")
            if p.summon.hit_hp:
                bits.append(f"타수 {sum(a.hits for a in run.adds)}발")
            if p.summon.attack is not None:
                bits.append(f"{run.fired}발 · 니케 체력 피해 {round(run.hp_dealt):,}")
                if p.summon.attack.debuffs:
                    bits.append(f"디버프 {run.debuffed}건")
        if p.attack is not None:
            # 패턴이 먼저 닫혀 못 나간 발은 버린다 — 사유를 보이게 남긴다
            bits.append(f"{run.fired}/{p.attack.hits}발 · 니케 체력 피해 {round(run.hp_dealt):,}")
        elif p.cast is not None:
            bits.append(f"{run.fired}/{p.cast.hits}회")
        if p.shots is not None:
            if p.shots.debuffs:
                bits.append(f"디버프 {run.debuffed}건")
            run.pending = []
        if run.dispelled:
            bits.append("해제됨")
        self.log.append(BossLogEntry(t=t, pattern=p.id, kind=p.kind, event="end",
                                     outcome=outcome, detail=" · ".join(bits)))
        if outcome != "end":
            events.extend(p.emit_end)
            # 전투가 끝나서 닫히는 게 아니면, 이 패턴이 건 「닫힐 때 풀리는」 효과를 푼다
            self.released.append(p.id)

    def _apply(self, enemy: dict) -> None:
        """열린 패턴을 시작 시각 순(같으면 선언 순)으로 기본 상태 위에 덮어쓴다."""
        live = sorted((r for r in self._runs if r.active), key=lambda r: (r.start_t, r.p.idx))
        d, core = self._base_def, self._base_core
        parts, weapons, dist = self._base_parts, self._base_weapons, self._base_distance
        vanish, shields, absorbers = [], [], []
        for r in live:
            p = r.p
            if p.kind == "buff":
                if not r.dispelled:
                    d = d * p.def_mult + p.def_add
            elif p.kind == "core":
                core = max(core, p.core_px)
            elif p.kind == "move":
                if p.distance is not None:
                    dist = p.distance
                else:
                    weapons = list(p.weapons)
            elif p.kind == "shield":
                shields.append(r)
            elif p.kind == "vanish":
                vanish.append(r)
            elif p.kind in _TARGET_KINDS:
                alive = [x for x in r.targets if not x.destroyed]
                if alive:
                    if p.kind == "parts":
                        parts = True
                    core = max(core, max(x.spec.core_px for x in alive))
                if any(x.spec.breakable for x in alive):
                    absorbers.append(r)
        enemy["def"] = d
        enemy["core_px"] = core
        enemy["has_parts"] = parts
        enemy["optimal_range_weapons"] = weapons
        enemy["distance"] = dist
        self._vanish, self._shields, self._absorbers = vanish, shields, absorbers
        for kind, key, attr in (("parts", PART_REACH_KEY, "_reach_runs"),
                                ("interrupt", INTERRUPT_REACH_KEY, "_ireach_runs")):
            reach = {id(r): [x.spec.reach for x in r.targets if x.spec.reach and not x.destroyed]
                     for r in live if r.p.kind == kind}
            setattr(self, attr, [r for r in live if reach.get(id(r))])
            enemy[key] = min((k for ks in reach.values() for k in ks), default=0)
        self.vanished = bool(vanish)
        self.enemy_count = 1 + len(self._alive_adds())
        if self.coord is not None:
            self._apply_geom(live, enemy)

    def _apply_geom(self, live: list[_Run], enemy: dict) -> None:
        """좌표 모드 — 산 표적(앞 → 뒤)·코어·자동 에임을 `enemy[GEOM_KEY]`에 싣는다. 산 집합이 그대로면 같은 객체를
        둔다(착탄 확률 캐시를 살린다).

        앞뒤: z 큰 쪽이 앞, 같으면 **나중에 생긴 것**이 앞(새로 뜬 저지원이 파츠 위에 그려진다), 같은 패턴 안에서는
        뒤에 적힌 것이 앞. 코어: 열린 core 패턴과 적 기본값 중 가장 큰 것(같으면 나중에 열린 것) — 기본값은 원점.
        자동 에임: coord.auto_aim, 없으면 코어 중심, 코어도 없으면 원점(유저: 「대부분 코어가 있으면 그 위치」).
        깨야 하는 표적: 산 저지원 전부 + 벌칙 파츠의 깰 수 있는 산 표적, 패턴 선언 순 → 패턴 안 적힌 순."""
        rows = []
        for r in live:
            if r.p.kind in _TARGET_KINDS:
                for j, x in enumerate(r.targets):
                    if not x.destroyed:
                        rows.append(((x.spec.z, r.start_t, r.p.idx, j),
                                     GeomTarget(x.spec.name, r.p.kind, x.spec.shape)))
        rows.sort(key=lambda kv: kv[0], reverse=True)
        targets = tuple(g for _, g in rows)
        must_break = tuple(x.spec.name for r in sorted(live, key=lambda r: r.p.idx)
                           if r.p.kind == "interrupt" or r.p.id in self._penalty_parts
                           for x in r.targets
                           if not x.destroyed and (r.p.kind == "interrupt" or x.spec.breakable))
        core_px, core_at = self._base_core, (0.0, 0.0)
        for r in live:
            if r.p.kind == "core" and r.p.core_px >= core_px:
                core_px, core_at = r.p.core_px, r.p.core_at
        core = Shape(x=core_at[0], y=core_at[1], r=core_px / 2.0) if core_px > 0 else None
        auto = self.coord.auto_aim or (core_at if core is not None else (0.0, 0.0))
        sig = (tuple(g.name for g in targets), must_break, core, auto)
        if sig != self._geom_sig:
            self.geom = Geometry(targets, must_break, core, auto, self.coord)
            self._geom_sig = sig
        enemy[GEOM_KEY] = self.geom

    # ── 쫄몹 ──

    def _alive_adds(self) -> list[_Add]:
        """산 쫄몹(등장 순)."""
        return sorted((a for r in self._runs if r.active for a in r.adds if a.alive),
                      key=lambda a: a.order)

    @property
    def has_adds(self) -> bool:
        return any(a.alive for r in self._runs if r.active for a in r.adds)

    def _retire(self, run: _Run, add: _Add, why: str, t: float, events: list[str],
                log: bool = True) -> None:
        """쫄몹 하나를 없앤다. 처치·자폭은 사망이라 enemy_death를 쏘고, 퇴장은 안 쏜다."""
        add.gone = why
        add.pending = []
        self.gone.append(add.id)
        if why != "퇴장":
            events.append("enemy_death")
        if log:
            self.log.append(BossLogEntry(t=t, pattern=run.p.id, kind=run.p.kind, event="destroy",
                                         detail=f"{add.name} {why}"))

    def _aim_weights(self) -> list[tuple[str, float]]:
        """조준으로 대상이 정해지는 딜의 몫 — (적 id, 가중치), 가중치 순(동률은 보스 먼저, 등장 순).

        보스는 1 − Σshare, 무리마다 **첫 산 쫄몹**이 share를 받는다(앞에서부터 한 마리씩 잡는다).
        Σshare > 1이면 합이 1이 되게 줄인다. 조준하지 않는 무리(share 0)의 쫄몹은 가중치 0으로 뒤에 붙는다.
        **좌표 모델 교체 지점** — 조준·에임 모델이 생기면 손으로 적은 share 대신 여기서 유도한다."""
        alive = self._alive_adds()
        heads: dict[str, _Add] = {}
        for a in alive:
            if a.spec.share > 0:
                heads.setdefault(a.id.rsplit("#", 1)[0], a)
        total = sum(a.spec.share for a in heads.values())
        scale = 1.0 / total if total > 1 else 1.0
        picked = {a.id: a.spec.share * scale for a in heads.values()}
        rows = [(ENEMY, max(0.0, 1.0 - total * scale), -1)]
        rows += [(a.id, picked.get(a.id, 0.0), a.order) for a in alive]
        rows.sort(key=lambda r: (-r[1], r[2]))
        return [(i, w) for i, w, _ in rows]

    def _ranked(self, target: str, has_state: Callable[[str, str], bool] | None) -> tuple[list[str], int] | None:
        """적 대상 문자열 → (후보 적 id를 규칙 순서로, 고를 수). 조준 N=1이면 None(가중치로 쪼갠다).

        정본: docstring §쫄몹. 쫄몹이 있을 때만 부른다."""
        alive = self._alive_adds()
        ids = [a.id for a in alive]
        rule, _, arg = target.partition(":")
        n = int(arg) if arg.isdigit() else 0
        if rule in _ALL_TARGETS and not arg:
            return [ENEMY] + ids, 1 + len(ids)
        if rule == "enemies_random":
            pool = [ENEMY] + ids
            k = min(max(n, 1), len(pool))
            picked = set(self._rng.sample(pool, k))
            return [x for x in pool if x in picked], k
        if rule in ("enemies_top_hp", "enemies_top_atk", "enemies_top_def"):
            # 보스 먼저 — 체력은 보스가 가장 크고, 쫄몹 방어력은 모델이 없다. 공격력만 실제 값으로 줄 세운다
            if rule == "enemies_top_atk":
                boss_atk = float(self._enemy.get("atk", DEFAULT_BOSS_ATK))
                rows = [(ENEMY, boss_atk, -1)] + [(a.id, a.spec.atk, a.order) for a in alive]
                order = [i for i, _, _ in sorted(rows, key=lambda r: (-r[1], r[2]))]
            elif rule == "enemies_top_hp":
                order = [ENEMY] + [a.id for a in sorted(alive, key=lambda a: (-a.spec.hp, a.order))]
            else:
                order = [ENEMY] + ids
            return order, max(n, 1)
        if rule in ("enemies_lowest_hp", "enemies_lowest_def"):
            if rule == "enemies_lowest_hp":
                # 타수 기믹 쫄몹은 남은 타수로 줄 세운다 — 체력과 단위가 달라 무리끼리 섞이면 뜻이 약하다(⬜)
                def _left(a: _Add) -> float:
                    return (a.spec.hp - a.dealt) if a.spec.hp is not None else float(a.spec.hit_hp - a.hits)
                order = [a.id for a in sorted(alive, key=lambda a: (_left(a), a.order))]
            else:
                order = list(ids)
            return order + [ENEMY], max(n, 1)
        if rule == "enemies_with_buff":
            hit = [x for x in [ENEMY] + ids if has_state is not None and has_state(x, arg)]
            return (hit, len(hit)) if hit else ([ENEMY], 1)
        if rule in ("enemies_code", "enemies_lowest_hp_code"):
            return [ENEMY], 1       # 쫄몹 코드가 없다 — 단일 보스 때처럼 필터를 안 건다
        # 조준 — target · same_target(:X) · enemies_nearest(:N) · enemies_nearest_in_range · 모르는 적 대상
        if rule == "enemies_nearest" and n >= 2:
            return [i for i, _ in self._aim_weights()], n
        return None

    def resolve_enemies(self, target: str, has_state: Callable[[str, str], bool] | None = None) -> list[str]:
        """적에게 거는 효과의 대상 적 id (buff_manager `_resolve_target`이 쫄몹이 있을 때 부른다).

        딜과 같은 규칙이고, 조준 규칙은 가중치가 가장 큰 1기다(동률 보스) — 효과는 쪼갤 수 없다."""
        ranked = self._ranked(target, has_state)
        if ranked is None:
            return [self._aim_weights()[0][0]]
        order, k = ranked
        return order[:k]

    def route(self, ev: HitEvent, has_state: Callable[[str, str], bool] | None = None) -> list[tuple[str, float]]:
        """히트 하나를 (적 id, 가중치)로 나눈다. 쫄몹이 있을 때만 부른다.

        대상이 정해진 히트(`to` — 지속 대미지 틱)는 그 적 중 남은 것만, 나머지는 `rule`대로.
        분할 대미지는 맞은 적 수로 나눈다(조준 N=1은 한 발이 한 적이라 안 나눈다)."""
        if ev.to is not None:
            live = {ENEMY} | {a.id for a in self._alive_adds()}
            dead = [x for x in ev.to if x not in live]
            if dead:
                self.add_overkill += ev.damage * len(dead)
            return [(x, 1.0) for x in ev.to if x in live]
        ranked = self._ranked(ev.rule, has_state) if ev.rule else None
        if ranked is None:
            return [(i, w) for i, w in self._aim_weights() if w > 0]
        order, k = ranked
        hit = order[:k]
        w = 1.0 / len(hit) if ev.split and hit else 1.0
        return [(x, w) for x in hit]

    def hit_add(self, ev: HitEvent, add_id: str, t: float) -> bool:
        """쫄몹 몫 하나를 그 쫄몹 체력에 넣는다. 이미 사라졌으면 버리고 False.

        **타수 기믹**(`hit_hp`)이 켜져 있으면 딜이 얼마든 한 발에 1만 들어가고, 삼켜진 나머지는
        넘친 딜과 같은 자리로 간다(`add_overkill`). 그 밖에는 체력을 깎는다. 체력을 넘친 딜도 버린다.
        체력이 다하거나 타수를 채우면 처치 — enemy_death는 표적 파괴와 같이 다음 프레임 맨 앞에서 나간다."""
        for run in self._runs:
            if not run.active:
                continue
            for add in run.adds:
                if add.id != add_id:
                    continue
                if not add.alive:
                    self.add_overkill += ev.damage
                    return False
                s = add.spec
                counting = bool(s.hit_hp) and (s.hp is None
                                               or t >= add.spawn_t + s.hit_hp_after - _EPS)
                if counting:
                    add.hits += 1
                    took = min(float(ev.damage), 1.0)       # 한 발에 1 — 딜 크기는 뜻이 없다
                    killed = add.hits >= s.hit_hp
                else:
                    took = min(float(ev.damage), s.hp - add.dealt)
                    killed = add.dealt + took >= s.hp
                add.dealt += took
                self.add_dealt[ev.caster] = self.add_dealt.get(ev.caster, 0.0) + took
                self.add_overkill += ev.damage - took
                if killed:
                    self._retire(run, add, "처치", t, self._carry)
                return True
        self.add_overkill += ev.damage
        return False

    # ── 히트마다 ──

    def shield_blocks(self, caster: str) -> bool:
        """지금 열린 속성보호막이 이 캐스터의 딜을 막는가(여럿이면 하나라도 못 이기면 막힌다).

        timeline이 **스킬 대미지 몫의 버스트 게이지**를 거를 때 쓴다 — 막힌 스킬 대미지 히트는
        게이지를 안 채우고, 무기 사격 게이지와 게이지 충전 효과는 그대로 채운다(유저 확인 2026-09-15).
        딜 게이트는 `admit()`이 따로 한다."""
        return any(not self._superior(caster, r.p.code) for r in self._shields)

    def gate(self, ev: HitEvent) -> bool:
        """보스 게이트(사라짐·속성보호막)를 지나는가. 막힌 딜은 그 패턴의 「막은 딜」로 센다.

        게이트는 결과 이벤트 자리에 있다. 사라짐은 평타만 빼고, 발사로 파생된 스킬과 이미 걸린
        지속 대미지는 보스가 화면에 없어도 들어간다 — 트리거는 이미 처리된 뒤다.
        """
        if self._vanish and _is_normal(ev):
            self._vanish[0].blocked += ev.damage
            return False
        for r in self._shields:
            # 보호막이 여럿 겹치면 전부 이겨야 한다
            if not self._superior(ev.caster, r.p.code):
                r.blocked += ev.damage
                return False
        return True

    def admit(self, ev: HitEvent, t: float) -> bool:
        """본체 히트가 들어가는가(`gate`). 들어가면 좌표 off는 표적에 share만큼 흡수하고 True.

        **거른 뒤에 흡수한다.** 안 들어간 딜로 저지원이 깨지면 안 된다 — 그래야 「속성보호막을
        두르고 저지를 띄운다」는 연계가 제대로 어려워진다.

        **좌표 모드는 흡수하지 않는다** — 표적은 거기 떨어진 히트(`hit_target`)만 받는다.
        """
        if not self.gate(ev):
            return False
        if self.coord is not None:
            return True
        for r in self._absorbers:
            direct, tier = ((ev.part_damage, ev.reach) if r.p.kind == "parts"
                            else (ev.interrupt_damage, ev.interrupt_reach))
            for x in r.targets:
                if x.destroyed or not x.spec.breakable:
                    continue
                if direct and x.spec.reach and tier >= x.spec.reach:
                    continue    # 이 발은 이 표적을 직접 맞힌다 — `part_hits()`·`interrupt_hits()`가 한 발 몫을 넣는다(한 발에 한 번)
                # 교체 지점: 조준·좌표 모델이 생기면 손으로 적은 share를 좌표에서 유도한 값으로
                x.dealt += ev.damage * x.spec.share
                if x.dealt >= x.spec.hp:
                    self._destroy(r, x, t)
        return True

    def hit_target(self, ev: HitEvent, t: float) -> str:
        """좌표 모드 — 표적 `ev.target`에 떨어진 히트를 그 표적 체력에 넣고 표적 종류("parts"·"interrupt")를
        돌려준다. 게이트(`gate`)는 부르는 쪽이 먼저 본다.

        회계는 종류로 갈린다(유저 결정 2026-09-18): **파츠 히트는 총딜**(초과분 포함 — 좌표 off 다중 타격과 같은
        규약), **저지원 히트는 총딜 밖**이다 — 여기서 시전자별로 `interrupt_dealt`에 쌓는다. 같은 프레임에 먼저 깨진
        표적처럼 이미 없는 표적에 온 히트는 체력에 안 넣고 종류만 돌려준다(회계는 그대로)."""
        kind = self.target_kinds[ev.target]
        if kind == "interrupt":
            self.interrupt_dealt[ev.caster] = self.interrupt_dealt.get(ev.caster, 0.0) + ev.damage
        for r in self._runs:
            if not r.active or r.p.kind not in _TARGET_KINDS:
                continue
            for x in r.targets:
                if x.spec.name != ev.target or x.destroyed:
                    continue
                x.hits += 1
                x.dealt += ev.damage
                if x.spec.breakable and x.dealt >= x.spec.hp:
                    self._destroy(r, x, t)
                return kind
        return kind

    def _destroy(self, r: _Run, x: _Target, t: float) -> None:
        x.destroyed = True
        self.score += x.spec.score
        self.log.append(BossLogEntry(
            t=t, pattern=r.p.id, kind=r.p.kind, event="destroy",
            detail=x.spec.name + (f" · 점수 {x.spec.score:,}" if x.spec.score else "")))
        self._carry.extend(x.spec.emits)

    def part_hits(self, ev: HitEvent, t: float) -> list[str]:
        """좌표 off의 파츠 다중 타격 — `admit()`을 통과한 발이 닿는 산 reach 파츠에 그 발의 파츠 몫
        (`ev.part_damage`)을 통째로 넣고, 맞은 파츠 이름을 돌려준다. timeline이 파츠마다 히트 하나씩을
        총딜에 더한다. 잔여 체력을 넘어도 몫이 그대로 들어간다 — 초과분도 입힌 피해다(유저 확인 2026-09-18).

        **교체 지점**: 좌표 모드가 생기면 「닿는가」를 reach 대신 파츠 좌표·에임·관통·폭발 범위로 푼다.
        """
        if not ev.reach or not ev.part_damage:
            return []
        hit = []
        for r in self._reach_runs:
            for x in r.targets:
                if x.destroyed or not x.spec.reach or ev.reach < x.spec.reach:
                    continue
                x.extra_hits += 1
                x.extra_dealt += ev.part_damage
                hit.append(x.spec.name)
                if x.spec.breakable:
                    x.dealt += ev.part_damage
                    if x.dealt >= x.spec.hp:
                        self._destroy(r, x, t)
        return hit

    def interrupt_hits(self, ev: HitEvent, t: float) -> list[str]:
        """좌표 off의 저지원 다중 타격 — `admit()`을 통과한 발이 닿는 산 reach 저지원에 그 발의 저지원 몫
        (`ev.interrupt_damage`)을 넣고, 맞은 저지원 이름을 돌려준다. 딜은 **총딜 밖**이라 여기서 시전자별로
        `interrupt_dealt`에 쌓는다(좌표 모드 `hit_target`과 같은 칸). timeline은 흡혈만 붙인다."""
        if not ev.interrupt_reach or not ev.interrupt_damage:
            return []
        hit = []
        for r in self._ireach_runs:
            for x in r.targets:
                if x.destroyed or not x.spec.reach or ev.interrupt_reach < x.spec.reach:
                    continue
                x.extra_hits += 1
                x.extra_dealt += ev.interrupt_damage
                self.interrupt_dealt[ev.caster] = self.interrupt_dealt.get(ev.caster, 0.0) + ev.interrupt_damage
                hit.append(x.spec.name)
                if x.spec.breakable:
                    x.dealt += ev.interrupt_damage
                    if x.dealt >= x.spec.hp:
                        self._destroy(r, x, t)
        return hit

    def log_squad(self, t: float, pattern: str, event: str, detail: str) -> None:
        """니케 쪽 사건(전투불능·부활)을 흐름 로그에 끼운다. 원인이 된 공격 패턴 이름으로 적는다."""
        kind = self._run_by_id[pattern].p.kind if pattern in self._run_by_id else ""
        self.log.append(BossLogEntry(t=t, pattern=pattern, kind=kind, event=event, detail=detail))

    # ── 루프 종료 뒤 ──

    def finish(self, t: float) -> None:
        for run in self._runs:
            if run.active:
                self._close(run, t, "end", [])


def legacy_to_patterns(enemy: dict) -> dict:
    """종전 스칼라를 「전투 시작에 열려 끝까지 가는 패턴」으로 옮긴 적을 돌려준다.

    **검산 전용이다. 엔진은 안 쓴다** — 스칼라를 그대로 두는 편이 회귀에 안전하다. 두 적이 같은
    전투를 내면 오버레이 배선이 기본 경로와 어긋나지 않았다는 증거가 된다.
    """
    if enemy.get("patterns"):
        raise ValueError("이미 패턴이 있는 적은 등가 변환의 대상이 아니다")
    out = {k: v for k, v in enemy.items() if k not in OVERLAY_FIELDS and k != "patterns"}
    out.update(core_px=0, has_parts=False, optimal_range_weapons=[], distance=None)
    pats: list[dict] = [{"id": "기본 방어력", "kind": "buff",
                         "enemy": {"def_mult": 0, "def_add": enemy.get("def", DEFAULT_ENEMY_DEF)}}]
    if enemy.get("core_px", 0) > 0:
        pats.append({"id": "기본 코어", "kind": "core", "core_px": enemy["core_px"]})
    if enemy.get("has_parts"):
        pats.append({"id": "기본 파츠", "kind": "parts", "targets": [{"name": "파츠", "hp": 0}]})
    if enemy.get("optimal_range_weapons"):
        pats.append({"id": "기본 적정거리", "kind": "move",
                     "weapons": list(enemy["optimal_range_weapons"])})
    if enemy.get("distance") is not None:
        pats.append({"id": "기본 거리", "kind": "move", "distance": enemy["distance"]})
    out["patterns"] = pats
    return out


# ── 자체검산 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    from .damage import is_element_match

    DT = 1 / 60
    BASE = {"def": 31784, "code": "수냉", "core_px": 0, "has_parts": False,
            "optimal_range_weapons": []}
    ROSTER = {"전격캐": "전격", "작열캐": "작열"}

    def superior(caster: str, code: str) -> bool:
        return is_element_match(ROSTER.get(caster, ""), code)

    def run(patterns, until_t, hits=None, base=None):
        """timeline 루프를 흉내 낸다: 프레임 맨 앞 전이 → 히트 흡수. 프레임별 적 상태도 남긴다."""
        enemy = dict(base or BASE)
        boss = BossScript(validate(patterns, weapon_types=frozenset({"SG", "SMG", "SR"})),
                          enemy, superior)
        frames, admitted, fired = [], [], []
        t = 0.0
        while t <= until_t:
            fired += [(t, e) for e in boss.begin_frame(t, enemy)]
            frames.append((t, dict(enemy), boss.vanished))
            for ev in (hits(t) if hits else []):
                if boss.admit(ev, t):
                    admitted.append(ev)
            t += DT
        boss.finish(until_t)
        return boss, frames, admitted, fired

    def starts(boss, pid):
        return [e.t for e in boss.log if e.pattern == pid and e.event == "start"]

    def ends(boss, pid):
        return [(e.t, e.outcome) for e in boss.log if e.pattern == pid and e.event == "end"]

    def near(a, b):
        return abs(a - b) < DT / 2

    def state_at(frames, t):
        return next(s for ft, s, _ in frames if near(ft, t) or ft > t)

    normal = lambda c, d: HitEvent(t=0, caster=c, damage=d, is_crit=False, hit_tag="normal")
    skill = lambda c, d: HitEvent(t=0, caster=c, damage=d, is_crit=False, hit_tag="dot_damage",
                                  skill_name="지속딜")

    # ── 검산 1: 순차 — A(5초) → B(3초) → C가 5.0s·8.0s에 갈린다
    b, *_ = run([{"id": "A", "kind": "idle", "until": {"time": 5}},
                 {"id": "B", "kind": "idle", "after": ["A"], "until": {"time": 3}},
                 {"id": "C", "kind": "idle", "after": ["B"]}], 10)
    assert near(starts(b, "B")[0], 5.0) and near(starts(b, "C")[0], 8.0), b.log
    assert ends(b, "C") == [(10, "end")], ends(b, "C")
    print(f"검산 1 — 순차: B {starts(b, 'B')[0]:.3f}s · C {starts(b, 'C')[0]:.3f}s")

    # ── 검산 2: 동시 — 같은 after를 나눠 가진 둘이 함께 열린다 / 시각 여유 (20초가 20.017로 밀리지 않는다)
    b, *_ = run([{"id": "A", "kind": "idle", "until": {"time": 20}},
                 {"id": "B1", "kind": "idle", "after": ["A"]},
                 {"id": "B2", "kind": "groggy", "after": ["A"], "delay": 1.5}], 25)
    assert starts(b, "B1") == [ends(b, "A")[0][0]] and near(starts(b, "B1")[0], 20.0)
    assert near(starts(b, "B2")[0], 21.5)
    print(f"검산 2 — 동시·지연: B1 {starts(b, 'B1')[0]:.3f}s · B2 {starts(b, 'B2')[0]:.3f}s")

    # ── 검산 3·4: 저지 성공 / 실패
    def interrupt_script():
        return [
            {"id": "저지", "kind": "interrupt", "after": [{"node": "대기"}],
             "until": {"time": 15, "targets_cleared": True},
             "emit": ["event:target_spawn"],
             "targets": [{"name": "저지원A", "hp": 1000, "score": 500,
                          "emit_on_destroy": ["enemy_death"]},
                         {"name": "저지원B", "hp": 1000, "share": 0.5},
                         {"name": "본체", "hp": 0}]},
            {"id": "대기", "kind": "idle", "until": {"time": 2}},
            {"id": "성공", "kind": "groggy", "after": [{"node": "저지", "outcome": "cleared"}],
             "until": {"time": 5}},
            {"id": "광역기", "kind": "attack", "after": [{"node": "저지", "outcome": "expired"}],
             "until": {"time": 5}, "spec": {"coeff": 300, "target": "all"}},
        ]
    # 2초부터 초당 60프레임 × 10 = 600딜 → A는 1000에서 약 3.67초, B(절반)는 약 5.33초에 깨진다
    b, _, _, fired = run(interrupt_script(), 30, hits=lambda t: [normal("전격캐", 10)])
    (t_end, oc), = ends(b, "저지")
    destroys = [e for e in b.log if e.event == "destroy"]
    assert oc == "cleared" and near(t_end, destroys[-1].t + DT), (t_end, destroys)
    assert starts(b, "성공") == [t_end] and not starts(b, "광역기")
    assert b.score == 500 and not b.unmodeled
    assert (2.0, "event:target_spawn") in [(round(ft, 6), e) for ft, e in fired]
    t_death = next(ft for ft, e in fired if e == "enemy_death")
    assert near(t_death, destroys[0].t + DT), "파괴 이벤트는 다음 프레임 맨 앞에서 나간다"
    print(f"검산 3 — 저지 성공: {t_end:.3f}s cleared · 점수 {b.score} · 실패 분기 무발동")

    b, *_ = run(interrupt_script(), 30)
    (t_end, oc), = ends(b, "저지")
    assert oc == "expired" and near(t_end, 17.0) and not starts(b, "성공")
    assert near(starts(b, "광역기")[0], 17.0) and not b.unmodeled
    print(f"검산 4 — 저지 실패: {t_end:.3f}s expired → 광역기 분기 열림")

    # ── 우선순위: 제한시간이 끝나는 바로 그 프레임에 마지막 저지원이 깨졌다면 성공이다
    lim = 1.0
    b, *_ = run([{"id": "저지", "kind": "interrupt", "until": {"time": lim, "targets_cleared": True},
                  "targets": [{"name": "X", "hp": 100}]}], 3,
                hits=lambda t: [normal("전격캐", 100)] if lim - DT - DT / 2 < t < lim - DT / 2 else [])
    assert ends(b, "저지")[0][1] == "cleared", ends(b, "저지")
    print(f"검산 5 — 우선순위: 마감 프레임에 전멸 → {ends(b, '저지')[0][1]}")

    # ── 검산 6: 속성보호막 — 우월 코드만 통과하고, 막힌 딜은 표적도 못 깎는다
    b, frames, admitted, _ = run(
        [{"id": "보호막", "kind": "shield", "code": "수냉", "until": {"time": 5}},
         {"id": "파츠", "kind": "parts", "targets": [{"name": "팔", "hp": 10 ** 9}]}],
        10, hits=lambda t: [normal("전격캐", 10), skill("작열캐", 30)])
    n_fire = sum(1 for e in admitted if e.caster == "작열캐")
    n_elec = sum(1 for e in admitted if e.caster == "전격캐")
    assert n_elec == len(frames) and n_fire == len(frames) - 300, (n_fire, len(frames))
    팔 = b._runs[1].targets[0]
    assert 팔.dealt == 10 * n_elec + 30 * n_fire, "막힌 딜이 표적을 깎았다"
    shield_end = next(e for e in b.log if e.pattern == "보호막" and e.event == "end")
    assert shield_end.detail == "막은 딜 9,000", shield_end
    # 스킬 게이지 게이트가 묻는 자리 — 열린 동안만, 우월하지 않은 캐스터만 막힌다
    enemy = dict(BASE)
    sb = BossScript(validate([{"id": "보호막", "kind": "shield", "code": "수냉", "until": {"time": 1}}]),
                    enemy, superior)
    sb.begin_frame(0.0, enemy)
    assert sb.shield_blocks("작열캐") and not sb.shield_blocks("전격캐")
    sb.begin_frame(1.0, enemy)
    assert not sb.shield_blocks("작열캐")
    print(f"검산 6 — 속성보호막: 작열 300프레임 차단 · 팔이 받은 딜 {팔.dealt:,.0f} ({shield_end.detail}) · "
          f"게이지 게이트는 열린 동안 작열만")

    # ── 검산 7: 사라짐 — 평타만 빠지고 스킬·지속딜은 들어간다 / 사라진 구간이 [1, 3)이다
    # (평타 게이지를 거르는 자리는 timeline `CharState._weapon_gauge_lands()` — 스킬 게이지는 그대로 찬다)
    b, frames, admitted, _ = run(
        [{"id": "출현", "kind": "idle", "until": {"time": 1}},
         {"id": "사라짐", "kind": "vanish", "after": ["출현"], "until": {"time": 2}}],
        4, hits=lambda t: [normal("전격캐", 1), skill("전격캐", 1)])
    n_normal = sum(1 for e in admitted if e.hit_tag == "normal")
    n_skill = sum(1 for e in admitted if e.hit_tag == "dot_damage")
    gone = [ft for ft, _, v in frames if v]
    assert n_skill == len(frames) and n_normal == len(frames) - 120, (n_normal, n_skill)
    assert near(gone[0], 1.0) and near(gone[-1], 3.0 - DT) and len(gone) == 120
    print(f"검산 7 — 사라짐: 평타 {len(frames) - n_normal}프레임 빠짐 · 스킬 전부 들어감 · "
          f"사라진 구간 {gone[0]:.3f}~{gone[-1]:.3f}s")

    # ── 검산 8: 코어 파츠 — 표적이 깨질 때 코어·파츠도 함께 닫힌다. 합성은 큰 코어
    b, frames, _, _ = run(
        [{"id": "코어", "kind": "core", "core_px": 30},
         {"id": "약점", "kind": "parts", "targets": [{"name": "코어 파츠", "hp": 600, "core_px": 45}]}],
        3, hits=lambda t: [skill("전격캐", 10)] if t >= 1 - DT / 2 else [])
    dt_ = next(e.t for e in b.log if e.event == "destroy")
    before, after = state_at(frames, dt_), state_at(frames, dt_ + DT)
    assert before["core_px"] == 45 and before["has_parts"] is True, before
    assert after["core_px"] == 30 and after["has_parts"] is False, after
    assert near(dt_, 2.0 - DT)
    print(f"검산 8 — 코어 파츠: {dt_:.3f}s 파괴 → 다음 프레임 core {before['core_px']}→{after['core_px']} · "
          f"has_parts {before['has_parts']}→{after['has_parts']}")

    # ── 검산 9: 사이클 — after 순환 + repeat 0 / 오버레이(방어력·적정거리)가 구간을 따른다
    b, frames, _, _ = run(
        [{"id": "강화", "kind": "buff", "after": ["start", "돌진"], "repeat": 0,
          "until": {"time": 2}, "enemy": {"def_mult": 2, "def_add": 100}},
         {"id": "후퇴", "kind": "move", "after": ["강화"], "repeat": 0, "until": {"time": 3},
          "weapons": ["SR"]},
         {"id": "돌진", "kind": "move", "after": ["후퇴"], "repeat": 0, "until": {"time": 1},
          "weapons": ["SG", "SMG"]}], 20)
    st = starts(b, "강화")
    assert len(st) == 4 and all(near(x, 6.0 * i) for i, x in enumerate(st)), st
    assert state_at(frames, 1.0)["def"] == 31784 * 2 + 100
    assert state_at(frames, 3.0)["def"] == 31784 and state_at(frames, 3.0)["optimal_range_weapons"] == ["SR"]
    assert state_at(frames, 5.5)["optimal_range_weapons"] == ["SG", "SMG"]
    assert state_at(frames, 6.5)["optimal_range_weapons"] == []
    print(f"검산 9 — 사이클: 강화 {' · '.join(f'{x:.3f}' for x in st)}s")

    # ── 검산 9b: 보스 거리 — move.distance가 구간 동안 거리를 바꾸고 닫히면 기본 거리로 돌아온다
    b, frames, _, _ = run(
        [{"id": "후퇴", "kind": "move", "until": {"time": 2}, "distance": 70},
         {"id": "돌진", "kind": "move", "after": ["후퇴"], "until": {"time": 1}, "distance": 20}], 5,
        base={**BASE, "distance": 40})
    assert [state_at(frames, x)["distance"] for x in (0.5, 2.5, 3.5)] == [70, 20, 40]
    for label, base_, pats in (
            ("거리 move + 무기군 목록 적", {**BASE, "optimal_range_weapons": ["SR"]},
             [{"kind": "move", "distance": 30}]),
            ("거리 있는 적 + 목록 move", {**BASE, "distance": 30}, [{"kind": "move", "weapons": ["SR"]}])):
        try:
            BossScript(validate(pats), dict(base_), superior)
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: {label}")
    print("검산 9b — 보스 거리: 70 → 20 → 기본 40 · 목록과 거리를 섞은 적 2종 거절")

    # ── 검산 10: 등가 변환 — 종전 스칼라와 같은 적 상태가 나온다
    for legacy in ({"def": 31784, "code": "수냉"},
                   {"def": 50000, "code": "작열", "core_px": 52, "has_parts": True,
                    "optimal_range_weapons": ["SG", "SMG"]},
                   {"def": 40000, "code": "풍압", "distance": 35}):
        full = {**BASE, "distance": None, **legacy}
        conv = legacy_to_patterns(full)
        enemy = {**BASE, **{k: v for k, v in conv.items() if k != "patterns"}}
        boss = BossScript(validate(conv["patterns"]), enemy, superior)
        boss.begin_frame(0.0, enemy)
        for k in OVERLAY_FIELDS:
            assert enemy[k] == full[k], (k, enemy[k], full[k])
    print("검산 10 — 등가 변환: 스칼라 다섯(거리 포함)이 같다")

    # ── 검산 12: 공격 발 스케줄 — 열린 프레임에 첫 발, interval 간격, 패턴이 먼저 닫히면 남은 발은 버린다
    def attack_frames(patterns, until_t):
        enemy = dict(BASE)
        boss = BossScript(validate(patterns, squad_size=5), enemy, superior)
        shots, t = [], 0.0
        while t <= until_t:
            boss.begin_frame(t, enemy)
            shots += [(t, a.pattern, a.index) for a in boss.attacks]
            boss.attacks.clear()
            t += DT
        boss.finish(until_t)
        return boss, shots
    b, shots = attack_frames(
        [{"id": "대기", "kind": "idle", "until": {"time": 1}},
         {"id": "난사", "kind": "attack", "after": ["대기"], "until": {"time": 1.2},
          "spec": {"coeff": 50, "target": "random:2", "hits": 4, "interval": 0.5}},
         {"id": "일격", "kind": "attack", "after": ["난사"],
          "spec": {"coeff": 300, "target": "slot:1", "pierce": True, "hits": 2,
                   "ignore_taunt": True}}], 5)
    assert b._run_by_id["일격"].p.attack.ignore_taunt and not b._run_by_id["난사"].p.attack.ignore_taunt
    nansa = [ft for ft, pid, _ in shots if pid == "난사"]
    assert len(nansa) == 3 and all(near(x, 1.0 + 0.5 * i) for i, x in enumerate(nansa)), nansa
    ilgyeok = [(ft, i) for ft, pid, i in shots if pid == "일격"]
    assert [i for _, i in ilgyeok] == [0, 1] and near(ilgyeok[0][0], 2.2) and ilgyeok[0][0] == ilgyeok[1][0]
    end = next(e for e in b.log if e.pattern == "난사" and e.event == "end")
    assert end.detail.startswith("3/4발"), end.detail
    print(f"검산 12 — 공격 스케줄: 난사 {' · '.join(f'{x:.3f}' for x in nansa)}s (4발 중 3발) · "
          f"일격 {ilgyeok[0][0]:.3f}s 2발 동시")

    # ── 검산 13: 디버프 — 발 스케줄은 공격과 같고 선언 순으로 섞인다 · 효과 dict는 순환해도 같은 객체 ·
    #    「닫힐 때 풀리는」 효과는 닫힌 프레임에 released로 나온다(전투 끝은 제외)
    enemy = dict(BASE)
    pats = validate([
        {"id": "저주", "kind": "debuff", "after": ["start", "휴식"], "repeat": 0, "until": {"time": 2},
         "spec": {"target": "random:2", "hits": 2, "interval": 1.5,
                  "debuffs": [{"name": "부식", "stat": "atk_pct", "value": -20},
                              {"stat": "dot", "coeff": 30, "duration": 5, "max_stack": 3}]}},
        {"id": "휴식", "kind": "idle", "after": ["저주"], "repeat": 0, "until": {"time": 1}},
        {"id": "베기", "kind": "attack", "spec": {"coeff": 100, "target": "all", "atk": 90000,
                                                 "debuffs": [{"stat": "stun", "duration": 1}]}},
    ], squad_size=5)
    cast = pats[0].cast
    assert cast.rule == "random" and cast.n == 2 and cast.hits == 2
    부식, 틱 = cast.debuffs
    assert 부식.effect["_boss_bound"] and 부식.effect["duration"] == -1 and 부식.effect["polarity"] == "harmful"
    assert 틱.name == "저주·dot" and 틱.effect["_boss_coeff"] == 30 and 틱.effect["_boss_interval"] == 1.0
    assert not 틱.effect["_boss_bound"] and 틱.effect["duration"] == 5 and 틱.effect["_boss_atk"] is None
    stun = pats[2].attack.debuffs[0]
    assert "fixed_value" not in stun.effect and stun.effect["duration"] == 1
    boss = BossScript(pats, enemy, superior)
    order, released, t = [], [], 0.0
    while t <= 7:
        boss.begin_frame(t, enemy)
        released += [(t, pid) for pid in boss.released]
        boss.released.clear()
        order += [(t, a.pattern, type(a.spec).__name__) for a in boss.attacks]
        boss.attacks.clear()
        t += DT
    boss.finish(7)
    assert [x[1:] for x in order[:2]] == [("저주", "DebuffCastSpec"), ("베기", "AttackSpec")], order[:2]
    curse = [ft for ft, pid, _ in order if pid == "저주"]
    assert all(near(a, b) for a, b in zip(curse, [0.0, 1.5, 3.0, 4.5, 6.0])), curse
    assert [pid for _, pid in released[:2]] == ["저주", "휴식"] and near(released[0][0], 2.0), released
    assert pats[0].cast.debuffs[0].effect is 부식.effect
    end = next(e for e in boss.log if e.pattern == "저주" and e.event == "end")
    assert end.detail == "2/2회 · 디버프 0건", end.detail   # 붙은 수는 timeline이 note_debuff로 돌려준다
    print(f"검산 13 — 디버프: 저주 {' · '.join(f'{x:.3f}' for x in curse)}s · 베기와 선언 순 · "
          f"닫힘 해제 {released[0][0]:.3f}s")

    # ── 검산 14: 보스 버프 받는 대미지 · 해제 — 나중에 두른 것부터, irremovable은 건너뛰고, 효과는 다음 프레임에 풀린다
    enemy = dict(BASE)
    boss = BossScript(validate([
        {"id": "갑주", "kind": "buff", "enemy": {"def_mult": 2, "received_dmg_pct": -30}},
        {"id": "결의", "kind": "buff", "irremovable": True, "after": ["start"], "delay": 1,
         "enemy": {"received_dmg_pct": -10}},
        {"id": "분노", "kind": "buff", "after": ["start"], "delay": 2, "enemy": {"def_add": 500}},
    ]), enemy, superior)
    boss.begin_frame(0.0, enemy)
    assert [e["fixed_value"] for e in boss.enemy_effects] == [-30] and enemy["def"] == 31784 * 2
    assert boss.enemy_effects[0]["polarity"] == "beneficial" and boss.enemy_effects[0]["_boss_bound"]
    boss.enemy_effects.clear()
    boss.begin_frame(1.0, enemy); boss.enemy_effects.clear()
    boss.begin_frame(2.0, enemy)
    assert enemy["def"] == 31784 * 2 + 500
    assert boss.dispel(1, 2.0) == ["분노"] and enemy["def"] == 31784 * 2 + 500, "해제는 다음 프레임에 반영"
    assert boss.dispel(5, 2.0) == ["갑주"], "irremovable·이미 해제된 것은 건너뛴다"
    boss.begin_frame(2.0 + DT, enemy)
    assert boss.released == ["분노", "갑주"] and enemy["def"] == 31784, (boss.released, enemy["def"])
    boss.finish(3.0)
    assert next(e for e in boss.log if e.pattern == "갑주" and e.event == "end").detail == "해제됨"
    print(f"검산 14 — 보스 버프 해제: 분노 → 갑주 순 · 결의(irremovable) 유지 · 다음 프레임 def {enemy['def']}")

    # ── 검산 15: 쫄몹 — 등장·조준 몫·집중 처치·사망 이벤트(다음 프레임)·적 수·cleared / 자폭 = 사망 / 퇴장은 이벤트 없음
    enemy = dict(BASE)
    boss = BossScript(validate([
        {"id": "무리", "kind": "summon", "until": {"targets_cleared": True, "time": 10},
         "spec": {"name": "랩쳐", "count": 2, "hp": 100, "share": 0.6}},
        {"id": "알", "kind": "summon", "after": ["start"], "delay": 1, "until": {"time": 3},
         "spec": {"count": 3, "hp": 50, "attack": {"coeff": 100, "target": "random:1", "hits": 2,
                                                   "interval": 0.5}, "atk": 9000, "attack_at": 1,
                  "self_destruct": True}},
        {"id": "후속", "kind": "idle", "after": [{"node": "무리", "outcome": "cleared"}]},
    ], squad_size=5), enemy, superior, rng=random.Random(1))
    fired = boss.begin_frame(0.0, enemy)
    assert fired == ["event:enemy_spawn"] * 2 and boss.enemy_count == 3, (fired, boss.enemy_count)
    assert boss._aim_weights() == [("__enemy__:무리#1", 0.6), (ENEMY, 0.4), ("__enemy__:무리#2", 0.0)]
    ev = HitEvent(t=0, caster="전격캐", damage=100, is_crit=False, hit_tag="normal")
    assert boss.route(ev) == [("__enemy__:무리#1", 0.6), (ENEMY, 0.4)]
    assert boss.route(replace(ev, rule="all_enemies", split=True)) == [
        (ENEMY, 1 / 3), ("__enemy__:무리#1", 1 / 3), ("__enemy__:무리#2", 1 / 3)]
    assert [x for x, _ in boss.route(replace(ev, rule="enemies_lowest_hp:2"))] == [
        "__enemy__:무리#1", "__enemy__:무리#2"]
    assert [x for x, _ in boss.route(replace(ev, rule="enemies_top_hp:1"))] == [ENEMY]
    assert [x for x, _ in boss.route(replace(ev, rule="enemies_nearest:2"))] == ["__enemy__:무리#1", ENEMY]
    assert boss.resolve_enemies("enemies_nearest:1") == ["__enemy__:무리#1"], "효과는 가중치 최대 1기"
    assert boss.resolve_enemies("enemies_with_buff:표식", lambda i, s: i == "__enemy__:무리#2") == [
        "__enemy__:무리#2"]
    assert boss.resolve_enemies("enemies_with_buff:표식", lambda i, s: False) == [ENEMY]
    # 무리#1을 60 + 60으로 잡는다 — 넘친 20은 버리고, 사망 이벤트는 다음 프레임 맨 앞
    assert boss.hit_add(replace(ev, damage=60), "__enemy__:무리#1", 0.0)
    assert boss.hit_add(replace(ev, damage=60), "__enemy__:무리#1", 0.0)
    assert boss.gone == ["__enemy__:무리#1"] and boss.enemy_count == 3, "적 수는 프레임 맨 앞에 정한다"
    assert not boss.hit_add(replace(ev, damage=5), "__enemy__:무리#1", 0.0), "죽은 쫄몹에 간 딜은 버린다"
    assert boss.add_dealt == {"전격캐": 100} and boss.add_overkill == 25
    assert boss._aim_weights()[0] == ("__enemy__:무리#2", 0.6), "다음 마리로 조준이 옮는다"
    boss.gone.clear()
    assert boss.begin_frame(DT, enemy) == ["enemy_death"] and boss.enemy_count == 2
    boss.hit_add(replace(ev, damage=100), "__enemy__:무리#2", DT)
    boss.gone.clear()
    t, attacks, deaths = 2 * DT, [], []
    while t <= 5:
        evs = boss.begin_frame(t, enemy)
        deaths += [(t, e) for e in evs if e == "enemy_death"]
        attacks += [(t, a.source) for a in boss.attacks]
        boss.attacks.clear()
        t += DT
    boss.finish(5)
    assert near(ends(boss, "무리")[0][0], 2 * DT) and ends(boss, "무리")[0][1] == "cleared"
    assert near(starts(boss, "후속")[0], 2 * DT)
    # 알: 1초 등장 → 2초·2.5초 발 × 3마리 → 2.5초에 셋 다 자폭(사망 셋) · 등장 이벤트 셋
    assert len(attacks) == 6 and {s for _, s in attacks} == {"알#1", "알#2", "알#3"}
    assert all(near(a, 2.0) for a, _ in attacks[:3]) and all(near(a, 2.5) for a, _ in attacks[3:])
    assert len(deaths) == 4 and all(near(a, 2.5) for a, _ in deaths[1:]), deaths
    egg_end = next(e for e in boss.log if e.pattern == "알" and e.event == "end")
    assert egg_end.outcome == "expired" and egg_end.detail.startswith("자폭 3 / 3기"), egg_end.detail
    # 퇴장 — 자폭 전에 닫히면 사망 이벤트 없이 사라진다
    enemy = dict(BASE)
    boss = BossScript(validate([{"id": "잠깐", "kind": "summon", "until": {"time": 1},
                                 "spec": {"count": 2, "hp": 10}}]), enemy, superior)
    boss.begin_frame(0.0, enemy)
    assert boss.begin_frame(1.0, enemy) == [] and boss.enemy_count == 1 and len(boss.gone) == 2
    assert next(e for e in boss.log if e.event == "end").detail.startswith("퇴장 2 / 2기")
    print(f"검산 15 — 쫄몹: 조준 0.6/0.4 · 집중 처치 → 다음 프레임 enemy_death · cleared {2 * DT:.3f}s → 후속 · "
          f"자폭 3기 = 사망 3 · 퇴장은 이벤트 없음")

    # ── 검산 16: 타수 기믹 쫄몹 — 전환 전에는 딜로, 전환 뒤에는 한 발에 1씩. 삼킨 딜은 넘친 딜로 간다
    enemy = dict(BASE)
    boss = BossScript(validate([
        {"id": "타수", "kind": "summon", "until": {"targets_cleared": True, "time": 20},
         "spec": {"name": "새끼", "count": 2, "hp": 1000, "hit_hp": 3, "hit_hp_after": 2, "share": 1.0}},
        {"id": "처음부터", "kind": "summon", "after": [{"node": "타수", "outcome": "cleared"}],
         "spec": {"name": "알", "hit_hp": 2}},
    ], squad_size=5), enemy, superior)
    boss.begin_frame(0.0, enemy)
    ev = HitEvent(t=0, caster="작열캐", damage=400, is_crit=False, hit_tag="normal")
    boss.hit_add(ev, "__enemy__:타수#1", 0.0)
    boss.hit_add(ev, "__enemy__:타수#1", 0.0)
    assert boss.add_dealt == {"작열캐": 800.0} and not boss.gone, "전환 전에는 딜로 깎는다"
    boss.begin_frame(2.0, enemy)
    for _ in range(3):
        boss.hit_add(replace(ev, damage=1_000_000), "__enemy__:타수#1", 2.0)
    assert boss.gone == ["__enemy__:타수#1"], "전환 뒤에는 딜이 얼마든 3발에 죽는다"
    assert boss.add_dealt == {"작열캐": 803.0} and boss.add_overkill == 3 * (1_000_000 - 1)
    boss.gone.clear()
    boss.begin_frame(2.0 + DT, enemy)
    for _ in range(3):
        boss.hit_add(replace(ev, damage=5), "__enemy__:타수#2", 2.0 + DT)
    boss.gone.clear()
    boss.begin_frame(2.0 + 2 * DT, enemy)
    assert near(ends(boss, "타수")[0][0], 2.0 + 2 * DT) and ends(boss, "타수")[0][1] == "cleared"
    assert near(starts(boss, "처음부터")[0], 2.0 + 2 * DT)
    # hp 없이 hit_hp만 — 등장부터 타수다
    boss.hit_add(replace(ev, damage=7), "__enemy__:처음부터#1", 2.0 + 2 * DT)
    assert not boss.gone
    boss.hit_add(replace(ev, damage=7), "__enemy__:처음부터#1", 2.0 + 2 * DT)
    assert boss.gone == ["__enemy__:처음부터#1"], "hp 없이 hit_hp면 처음부터 타수"
    start = next(e for e in boss.log if e.pattern == "타수" and e.event == "start")
    assert "체력 1,000 → 2초 뒤 타수 3" in start.detail, start.detail
    end = next(e for e in boss.log if e.pattern == "타수" and e.event == "end")
    assert "타수 6발" in end.detail, end.detail
    print("검산 16 — 타수 기믹: 전환 전 딜 800 · 전환 뒤 3발에 처치 · 삼킨 딜 넘친 딜로 · "
          "hp 없이 hit_hp면 등장부터 타수")

    # ── 검산 17: 좌표 off 파츠 다중 타격 —단계는 누적 · 닿은 파츠는 그 발의 몫을 통째로(초과분 포함) ·
    #    닿은 파츠는 share 몫을 안 받는다 · 깨진 파츠의 이벤트는 다음 프레임 · 산 reach 파츠의 최저 단계를 적에 적는다
    assert [hit_reach(pierce=True), hit_reach(pierce=True, pierce_range=100.0),
            hit_reach(explosion=True), hit_reach(explosion=True, explosion_range=100.0),
            hit_reach(parts_skill=True), hit_reach()] == [1, 2, 3, 4, 5, 0]
    assert hit_reach(pierce=True, pierce_range=99.9) == 1, "관통 범위 100% 미만이면 한 단계 덜 닿는다"
    assert hit_reach(pierce=True, explosion=True, pierce_range=300.0) == 3, "여러 수단이면 가장 멀리 닿는 쪽"
    enemy = dict(BASE)
    boss = BossScript(validate([
        {"id": "파츠", "kind": "parts",
         "targets": [{"name": f"단계{k}", "hp": 10 ** 9, "share": 0.0, "reach": k} for k in range(1, 6)]
                    + [{"name": "조준", "hp": 10 ** 9, "share": 0.5},
                       {"name": "겨눈 단계1", "hp": 10 ** 9, "share": 1.0, "reach": 1}]},
        {"id": "알집", "kind": "parts", "until": {"targets_cleared": True},
         "targets": [{"name": "알", "hp": 250, "share": 0.0, "reach": 3,
                      "emit_on_destroy": ["event:part_destroy"]}]},
    ]), enemy, superior)
    boss.begin_frame(0.0, enemy)
    assert enemy[PART_REACH_KEY] == 1
    shot = lambda r, pd: HitEvent(t=0, caster="전격캐", damage=100, is_crit=False, hit_tag="normal",
                                  reach=r, part_damage=pd)
    for R in range(6):
        ev = shot(R, 10 if R else 0)
        assert boss.admit(ev, 0.0)
        got = boss.part_hits(ev, 0.0)
        assert [n for n in got if n.startswith("단계")] == [f"단계{k}" for k in range(1, R + 1)], (R, got)
        assert ("알" in got) == (R >= 3), (R, got)
    assert boss.part_hits(shot(5, 0), 0.0) == [], "파츠 몫이 없는 발은 파츠를 안 때린다"
    tg = {x.spec.name: x for r in boss._runs for x in r.targets}
    assert tg["조준"].dealt == 6 * 100 * 0.5 and tg["조준"].extra_hits == 0, "단계 없는 파츠는 조준 몫만"
    assert tg["겨눈 단계1"].dealt == 100 + 5 * 10, "닿은 발은 share 몫 대신 파츠 몫만 — 한 발에 한 번"
    assert tg["단계1"].extra_hits == 5 and tg["단계5"].extra_hits == 1
    big = shot(3, 1000)
    assert boss.admit(big, 0.5) and "알" in boss.part_hits(big, 0.5)
    assert tg["알"].destroyed and tg["알"].extra_dealt == 3 * 10 + 1000, "잔여 체력을 넘어도 한 발 몫이 통째로"
    assert "알" not in boss.part_hits(shot(5, 10), 0.5), "깨진 파츠는 더 안 맞는다"
    assert boss.begin_frame(0.5 + DT, enemy) == ["event:part_destroy"], "파괴 이벤트는 다음 프레임 맨 앞"
    end = next(e for e in boss.log if e.pattern == "알집" and e.event == "end")
    assert end.outcome == "cleared" and "다중 타격 4발 · 딜 1,030" in end.detail, end.detail
    start = next(e for e in boss.log if e.pattern == "알집" and e.event == "start")
    assert "위치 단계 알 3" in start.detail, start.detail
    enemy2 = dict(BASE)
    plain = BossScript(validate([{"kind": "parts", "targets": [{"name": "X", "hp": 1}]}]), enemy2, superior)
    plain.begin_frame(0.0, enemy2)
    assert enemy2[PART_REACH_KEY] == 0, "reach 파츠가 없으면 0 — timeline이 파츠 몫을 산정하지 않는다"
    assert enemy2[INTERRUPT_REACH_KEY] == 0
    print("검산 17 — 파츠 다중 타격: 단계 누적 1~5 · 닿은 파츠는 share 대신 파츠 몫 · 초과분 포함 1,030 · "
          "파괴 이벤트 다음 프레임 · 최저 단계 기록")

    # ── 검산 20: 좌표 off 저지원 다중 타격 —단계 누적 1~4 · 닿은 저지원은 share 대신 저지원 몫 · 딜은 총딜 밖
    #    (`interrupt_dealt`) · 파츠 쪽 칸과 섞이지 않는다 · 깨지면 cleared
    enemy = dict(BASE)
    boss = BossScript(validate([
        {"id": "알집", "kind": "parts", "targets": [{"name": "알", "hp": 10 ** 9, "share": 0.0, "reach": 1}]},
        {"id": "저지", "kind": "interrupt", "until": {"time": 10, "targets_cleared": True},
         "targets": [{"name": f"저지원{k}", "hp": 10 ** 9, "share": 0.0, "reach": k} for k in range(1, 5)]
                    + [{"name": "조준 저지원", "hp": 10 ** 9, "share": 1.0},
                       {"name": "깨질 저지원", "hp": 1000, "share": 1.0, "reach": 3}]},
    ]), enemy, superior)
    boss.begin_frame(0.0, enemy)
    assert enemy[PART_REACH_KEY] == 1 and enemy[INTERRUPT_REACH_KEY] == 1
    ishot = lambda r, d: HitEvent(t=0, caster="전격캐", damage=100, is_crit=False, hit_tag="normal",
                                  interrupt_reach=r, interrupt_damage=d)
    for R in range(5):
        ev = ishot(R, 10 if R else 0)
        assert boss.admit(ev, 0.0)
        assert boss.part_hits(ev, 0.0) == [], "저지원 칸만 있는 발은 파츠를 안 때린다"
        got = boss.interrupt_hits(ev, 0.0)
        assert [n for n in got if n.startswith("저지원")] == [f"저지원{k}" for k in range(1, R + 1)], (R, got)
        assert ("깨질 저지원" in got) == (R >= 3), (R, got)
    itg = {x.spec.name: x for r in boss._runs for x in r.targets}
    assert itg["조준 저지원"].dealt == 5 * 100 and itg["조준 저지원"].extra_hits == 0, "단계 없는 저지원은 share 몫만"
    assert itg["깨질 저지원"].dealt == 3 * 100 + 2 * 10, "닿은 발은 share 몫 대신 저지원 몫만 — 한 발에 한 번"
    assert boss.interrupt_dealt == {"전격캐": 10.0 * (1 + 2 + 3 + 4 + 2)}, boss.interrupt_dealt
    assert itg["알"].extra_hits == 0 and boss.part_hits(replace(ishot(4, 10), reach=1, part_damage=7), 0.0) == ["알"]
    assert boss.interrupt_dealt["전격캐"] == 120.0, "파츠 몫은 저지원 딜에 안 섞인다"
    big = ishot(4, 1000)
    assert boss.admit(big, 0.5) and "깨질 저지원" in boss.interrupt_hits(big, 0.5)
    assert itg["깨질 저지원"].destroyed and itg["깨질 저지원"].extra_dealt == 2 * 10 + 1000
    boss.begin_frame(0.5 + DT, enemy)
    assert enemy[INTERRUPT_REACH_KEY] == 1, "깨진 저지원은 빠지고 나머지 최저 단계"
    assert not any(e.pattern == "저지" and e.event == "end" for e in boss.log), "산 저지원이 남아 cleared가 아니다"
    boss.finish(1.0)
    iend = next(e for e in boss.log if e.pattern == "저지" and e.event == "end")
    assert boss.interrupt_dealt == {"전격캐": 5120.0}, boss.interrupt_dealt
    assert "다중 타격 17발 · 딜 5,120 (총딜 밖)" in iend.detail, iend.detail
    istart = next(e for e in boss.log if e.pattern == "저지" and e.event == "start")
    assert "위치 단계 저지원1 1 · 저지원2 2 · 저지원3 3 · 저지원4 4 · 깨질 저지원 3" in istart.detail, istart.detail
    print("검산 20 — 저지원 다중 타격: 단계 누적 1~4 · 닿은 저지원은 share 대신 저지원 몫 · 딜은 총딜 밖 "
          "interrupt_dealt · 파츠 칸과 따로 · 깨진 저지원은 최저 단계에서 빠짐")

    # ── 검산 18: 좌표 모드 — 앞뒤 순서 · 자동 에임 · 표적은 떨어진 히트만 받는다(share 흡수 없음) ·
    #    파츠/저지원 회계 · 산 집합이 그대로면 같은 기하 객체(확률 캐시 유지)
    cpats = validate([
        {"id": "코어", "kind": "core", "core_px": 40, "x": 10, "y": -5},
        {"id": "다리", "kind": "parts", "targets": [
            {"name": "왼다리", "hp": 0, "x": -80, "y": -60, "w": 40, "h": 90},
            {"name": "오른다리", "hp": 300, "x": 80, "y": -60, "w": 40, "h": 90, "rotation": 15, "z": 1}]},
        {"id": "저지", "kind": "interrupt", "delay": 1.0, "until": {"time": 5, "targets_cleared": True},
         "targets": [{"name": "저지원", "hp": 200, "x": 0, "y": 40, "r": 30}]},
    ], coord=True)
    cen = {**BASE, "coord": {}}
    cboss = BossScript(cpats, cen, superior)
    cboss.begin_frame(0.0, cen)
    g0 = cen[GEOM_KEY]
    assert [x.name for x in g0.targets] == ["오른다리", "왼다리"], "z 큰 쪽이 앞"
    assert g0.core == Shape(10, -5, r=20) and g0.auto_aim == (10.0, -5.0), "자동 에임 = 코어 중심"
    assert g0.must_break == () and cen["core_px"] == 40, "실패 분기 없는 파츠는 깨야 하는 표적이 아니다"
    cboss.begin_frame(DT, cen)
    assert cen[GEOM_KEY] is g0, "산 집합이 그대로면 같은 객체"
    tt = 1.0
    cboss.begin_frame(tt, cen)
    g1 = cen[GEOM_KEY]
    assert g1 is not g0 and [x.name for x in g1.targets] == ["오른다리", "저지원", "왼다리"], \
        "나중에 생긴 저지원이 z 0끼리에서 앞(오른다리는 z 1)"
    assert g1.must_break == ("저지원",) and g1.center("저지원") == (0.0, 40.0) and g1.center("core") == (10.0, -5.0)
    body = HitEvent(t=tt, caster="전격캐", damage=500, is_crit=False, hit_tag="normal")
    assert cboss.admit(body, tt)
    ctg = {x.spec.name: x for r in cboss._runs for x in r.targets}
    assert ctg["저지원"].dealt == 0 and ctg["오른다리"].dealt == 0, "좌표 모드는 share로 흡수하지 않는다"
    hit = lambda name, d: HitEvent(t=tt, caster="전격캐", damage=d, is_crit=False, hit_tag="normal", target=name)
    assert cboss.gate(hit("저지원", 150)) and cboss.hit_target(hit("저지원", 150), tt) == "interrupt"
    assert cboss.hit_target(hit("오른다리", 120), tt) == "parts" and not ctg["오른다리"].destroyed
    assert cboss.hit_target(hit("저지원", 150), tt) == "interrupt" and ctg["저지원"].destroyed
    assert cboss.interrupt_dealt == {"전격캐": 300.0}, cboss.interrupt_dealt
    assert cboss.hit_target(hit("저지원", 99), tt) == "interrupt" and ctg["저지원"].hits == 2, \
        "깨진 표적에 온 히트는 체력에 안 넣고 회계만"
    cboss.begin_frame(tt + DT, cen)
    assert cen[GEOM_KEY].must_break == () and [x.name for x in cen[GEOM_KEY].targets] == ["오른다리", "왼다리"]
    cend = next(e for e in cboss.log if e.pattern == "저지" and e.event == "end")
    assert cend.outcome == "cleared" and "명중 2발 · 딜 300 (총딜 밖)" in cend.detail, cend.detail
    # 좌표 없는 좌표 모드 — 코어가 원점이면 자동 에임도 원점, 표적이 없으면 산 표적 없음
    e0 = {**BASE, "core_px": 52, "coord": {}}
    b0 = BossScript(validate([{"kind": "idle"}], coord=True), e0, superior)
    b0.begin_frame(0.0, e0)
    assert e0[GEOM_KEY].core == Shape(0, 0, r=26) and e0[GEOM_KEY].auto_aim == (0.0, 0.0)
    assert e0[GEOM_KEY].landing(0, 0, 37.5).core_open == min(1.0, (26 / 37.5) ** 2.55)
    # 벌칙 파츠 — 실패 분기(expired·followed)가 달린 parts의 깰 수 있는 표적은 저지원과 함께 깨야 하는 표적이다.
    # 성공 분기만 달린 parts · 깰 수 없는 표적(hp 0)은 아니다. 순서는 패턴 선언 순
    ppats = validate([
        {"id": "알집", "kind": "parts", "until": {"time": 5, "targets_cleared": True},
         "targets": [{"name": "알집", "hp": 100, "x": 0, "y": -50, "r": 20},
                     {"name": "껍질", "hp": 0, "x": 0, "y": -80, "r": 10}]},
        {"id": "부화", "kind": "idle", "after": [{"node": "알집", "outcome": "expired"}]},
        {"id": "뿔", "kind": "parts", "until": {"time": 5, "targets_cleared": True},
         "targets": [{"name": "뿔", "hp": 100, "x": 60, "y": 60, "r": 20}]},
        {"id": "보상", "kind": "idle", "after": [{"node": "뿔", "outcome": "cleared"}]},
        {"id": "타이머", "kind": "idle", "until": {"time": 5}},
        {"id": "촉수", "kind": "parts", "until": {"targets_cleared": True, "after": [{"node": "타이머"}]},
         "targets": [{"name": "촉수", "hp": 100, "x": -60, "y": 60, "r": 20}]},
        {"id": "휘두르기", "kind": "idle", "after": [{"node": "촉수", "outcome": "followed"}]},
        {"id": "저지", "kind": "interrupt", "until": {"time": 5, "targets_cleared": True},
         "targets": [{"name": "저지원", "hp": 100, "x": 0, "y": 40, "r": 30}]},
    ], coord=True)
    assert penalty_parts(ppats) == {"알집", "촉수"}, penalty_parts(ppats)
    pen = {**BASE, "coord": {}}
    pb = BossScript(ppats, pen, superior)
    pb.begin_frame(0.0, pen)
    assert pen[GEOM_KEY].must_break == ("알집", "촉수", "저지원"), pen[GEOM_KEY].must_break
    pb.hit_target(HitEvent(t=0.0, caster="전격캐", damage=100, is_crit=False, hit_tag="normal", target="알집"), 0.0)
    pb.begin_frame(DT, pen)
    assert pen[GEOM_KEY].must_break == ("촉수", "저지원"), "깬 벌칙 파츠는 빠진다"
    print("검산 18 — 좌표 모드: 앞뒤(z·나중에 생긴 것) · 자동 에임 = 코어 중심 · share 흡수 없음 · 파츠 총딜/저지원 "
          "총딜 밖 300 · 같은 산 집합이면 같은 기하 · 좌표 없는 좌표 모드 코어 = 종전 식 · 깨야 하는 표적 = 저지원 + "
          "벌칙 파츠(expired·followed 분기, hp 0 제외)")

    # ── 검산 19: 좌표 모드의 잘못된 스크립트·적 블록
    circ = {"name": "X", "hp": 10, "x": 0, "y": 0, "r": 5}
    coord_bad = {
        "좌표 표적에 x 없음":     [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "y": 0, "r": 5}]}],
        "원·직사각형 둘 다":      [{"kind": "parts", "targets": [{**circ, "w": 3, "h": 3}]}],
        "모양 없음":             [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "x": 0, "y": 0}]}],
        "r 0":                  [{"kind": "parts", "targets": [{**circ, "r": 0}]}],
        "직사각형 w만":          [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "x": 0, "y": 0, "w": 3}]}],
        "원에 rotation":        [{"kind": "parts", "targets": [{**circ, "rotation": 10}]}],
        "좌표 모드에 share":     [{"kind": "parts", "targets": [{**circ, "share": 0.5}]}],
        "좌표 모드에 reach":     [{"kind": "parts", "targets": [{**circ, "reach": 1}]}],
        "좌표 모드에 core_px":   [{"kind": "parts", "targets": [{**circ, "core_px": 10}]}],
        "z 문자열":             [{"kind": "parts", "targets": [{**circ, "z": "앞"}]}],
        "shape 칸":            [{"kind": "parts", "targets": [{**circ, "shape": "circle"}]}],
        "패턴 사이 표적 이름 겹침": [{"id": "A", "kind": "parts", "targets": [circ]},
                                 {"id": "B", "kind": "interrupt", "targets": [circ]}],
        "코어 위치가 수 아님":    [{"kind": "core", "core_px": 10, "x": "가운데"}],
    }
    for label, pats in coord_bad.items():
        try:
            validate(pats, coord=True)
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: {label}")
    stage_bad = {
        "좌표 off에 좌표":      [{"kind": "parts", "targets": [circ]}],
        "좌표 off 코어 위치":    [{"kind": "core", "core_px": 10, "x": 5}],
    }
    for label, pats in stage_bad.items():
        try:
            validate(pats)
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: {label}")
    block_bad = {"모르는 칸": {"aim": [0, 0]}, "auto_aim 모양": {"auto_aim": [1]},
                 "explosion_scale 0": {"explosion_scale": 0}, "pierce_px 음수": {"pierce_px": -1},
                 "dict 아님": [0, 0]}
    for label, raw in block_bad.items():
        try:
            coord_spec(raw)
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: coord {label}")
    assert coord_spec(None) is None and coord_spec({}) == CoordSpec()
    # 모드 — 좌표는 패턴 모드의 스위치다. 패턴 없이 켜면 거절(빈 목록도 패턴 없음)
    assert boss_mode({}) == SIMPLE and boss_mode({"patterns": [], "coord": None}) == SIMPLE
    assert boss_mode({"patterns": [{"kind": "idle"}]}) == PATTERN
    assert boss_mode({"patterns": [{"kind": "idle"}], "coord": {}}) == COORD
    assert boss_mode({"part_break_interval": 30.0}) == SIMPLE
    assert boss_mode({"patterns": [{"kind": "idle"}], "part_break_interval": 0.0}) == PATTERN
    mode_bad = {"패턴 없이 좌표": {"coord": {}}, "빈 패턴에 좌표": {"patterns": [], "coord": {}},
                "패턴에 파츠 파괴 주기": {"patterns": [{"kind": "idle"}], "part_break_interval": 30.0}}
    for label, e in mode_bad.items():
        try:
            boss_mode(e)
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: {label}")
    print(f"검산 19 — 좌표 모드 거절: 표적 {len(coord_bad)}종 · 좌표 off의 좌표 {len(stage_bad)}종 · "
          f"coord 블록 {len(block_bad)}종 · 모드 판정 5종 + 남의 모드 칸 {len(mode_bad)}종")

    # ── 검산 11: 잘못된 스크립트는 전부 거절한다
    idle = {"id": "A", "kind": "idle"}
    tg = [{"name": "X", "hp": 10}]
    bad_cases = {
        "모르는 kind":            [{"kind": "roar"}],
        "모르는 칸":              [{"kind": "idle", "untill": {"time": 1}}],
        "kind에 없는 칸":         [{"kind": "core", "core_px": 10, "targets": tg}],
        "id 중복":               [idle, dict(idle)],
        "자동 id 충돌":           [{"kind": "idle"}, {"id": "idle1", "kind": "idle"}],
        "예약어 id":             [{"id": "start", "kind": "idle"}],
        "없는 패턴 참조":          [{"kind": "idle", "after": ["B"]}],
        "until.after 자기 자신":   [{"id": "A", "kind": "idle", "until": {"after": [{"node": "A"}]}}],
        "until.after 없는 패턴":   [{"kind": "idle", "until": {"after": [{"node": "Z"}]}}],
        "until.time 0":          [{"kind": "idle", "until": {"time": 0}}],
        "until.time 음수":        [{"kind": "idle", "until": {"time": -1}}],
        "없는 속성 코드":          [{"kind": "shield", "code": "빛"}],
        "core_px 0":             [{"kind": "core", "core_px": 0}],
        "표적 없는 parts":         [{"kind": "parts", "targets": []}],
        "표적 없는 interrupt":     [{"kind": "interrupt"}],
        "표적 이름 중복":          [{"kind": "parts", "targets": tg + tg}],
        "음수 hp":               [{"kind": "parts", "targets": [{"name": "X", "hp": -1}]}],
        "음수 share":            [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "share": -0.1}]}],
        "hp 없는 표적":           [{"kind": "parts", "targets": [{"name": "X"}]}],
        "표적의 모르는 칸":        [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "hpp": 2}]}],
        "reach 0":               [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "reach": 0}]}],
        "reach 6":               [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "reach": 6}]}],
        "reach 소수":             [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "reach": 2.5}]}],
        "reach 문자열":           [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "reach": "all"}]}],
        "reach bool":            [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "reach": True}]}],
        "interrupt에 reach 5":    [{"kind": "interrupt", "targets": [{"name": "X", "hp": 1, "reach": 5}]}],
        "좌표 칸(x)":             [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "x": 0}]}],
        "좌표 칸(reachable_by)":  [{"kind": "parts", "targets": [{"name": "X", "hp": 1,
                                                               "reachable_by": ["pierce"]}]}],
        "깰 표적 없이 cleared":    [{"kind": "interrupt", "until": {"targets_cleared": True},
                                  "targets": [{"name": "X", "hp": 0}]}],
        "표적 kind 아닌 cleared":  [{"kind": "idle", "until": {"targets_cleared": True}}],
        "빈 after":              [{"kind": "idle", "after": []}],
        "outcome end":           [idle, {"kind": "idle", "after": [{"node": "A", "outcome": "end"}]}],
        "불가능한 outcome":        [idle, {"kind": "idle", "after": [{"node": "A", "outcome": "expired"}]}],
        "start에 outcome":        [{"kind": "idle", "after": [{"node": "start", "outcome": "expired"}]}],
        "시작에서 끊긴 패턴":       [{"id": "P", "kind": "idle", "after": ["Q"], "until": {"time": 1}},
                                 {"id": "Q", "kind": "idle", "after": ["P"], "until": {"time": 1}}],
        "모르는 이벤트":           [{"kind": "idle", "emit": ["event:part_destory"]}],
        "모르는 무기군":           [{"kind": "move", "weapons": ["LMG"]}],
        "move 목록·거리 둘 다":     [{"kind": "move", "weapons": ["SR"], "distance": 30}],
        "move 목록·거리 둘 다 없음": [{"kind": "move"}],
        "move 거리 0":             [{"kind": "move", "distance": 0}],
        "move 목록·거리 섞음":      [{"id": "A", "kind": "move", "weapons": ["SR"], "until": {"time": 1}},
                                 {"id": "B", "kind": "move", "after": ["A"], "distance": 30}],
        "예약 아닌 kind의 spec":   [{"kind": "idle", "spec": {}}],
        "spec 없는 attack":       [{"kind": "attack"}],
        "attack의 모르는 칸":      [{"kind": "attack", "spec": {"coeff": 1, "target": "all", "dmg": 1}}],
        "attack coeff 없음":      [{"kind": "attack", "spec": {"target": "all"}}],
        "attack target 없음":     [{"kind": "attack", "spec": {"coeff": 100}}],
        "attack 모르는 target":   [{"kind": "attack", "spec": {"coeff": 100, "target": "lowest_hp:1"}}],
        "attack random:0":        [{"kind": "attack", "spec": {"coeff": 100, "target": "random:0"}}],
        "attack 없는 자리":        [{"kind": "attack", "spec": {"coeff": 100, "target": "slot:6"}}],
        "attack slot 0":          [{"kind": "attack", "spec": {"coeff": 100, "target": "slot:0"}}],
        "attack hits 0":          [{"kind": "attack", "spec": {"coeff": 100, "target": "all", "hits": 0}}],
        "attack pierce 문자열":    [{"kind": "attack", "spec": {"coeff": 100, "target": "all", "pierce": "yes"}}],
        "attack atk 음수":         [{"kind": "attack", "spec": {"coeff": 100, "target": "all", "atk": -1}}],
        "attack ignore_taunt 문자열": [{"kind": "attack", "spec": {"coeff": 100, "target": "slot:1",
                                                                "ignore_taunt": "yes"}}],
        "all 공격의 ignore_taunt":  [{"kind": "attack", "spec": {"coeff": 100, "target": "all",
                                                              "ignore_taunt": True}}],
        "없앤 칸(게이지 토글)":     [{"kind": "vanish", "blocks_burst_gauge": False}],
        "모르는 buff 칸":          [{"kind": "buff", "enemy": {"atk_mult": 2}}],
        "buff received_dmg_pct 0":  [{"kind": "buff", "enemy": {"received_dmg_pct": 0}}],
        "buff irremovable 문자열":  [{"kind": "buff", "irremovable": "yes", "enemy": {"def_add": 1}}],
        "spec 없는 debuff":        [{"kind": "debuff"}],
        "debuff debuffs 없음":     [{"kind": "debuff", "spec": {"target": "all"}}],
        "debuff 빈 debuffs":       [{"kind": "debuff", "spec": {"target": "all", "debuffs": []}}],
        "debuff target 없음":      [{"kind": "debuff", "spec": {"debuffs": [{"stat": "stun"}]}}],
        "debuff의 모르는 칸":       [{"kind": "debuff", "spec": {"target": "all", "coeff": 1,
                                                             "debuffs": [{"stat": "stun"}]}}],
        "debuff all의 ignore_taunt": [{"kind": "debuff", "spec": {"target": "all", "ignore_taunt": True,
                                                               "debuffs": [{"stat": "stun"}]}}],
        "모르는 디버프 stat":       [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "silence"}]}}],
        "디버프 항목의 모르는 칸":   [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "stun", "durtion": 1}]}}],
        "이로운 쪽 부호":           [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "atk_pct", "value": 20}]}}],
        "이로운 쪽 부호(받는 피해)": [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "received_dmg_pct", "value": -5}]}}],
        "수치 stat에 value 없음":   [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "reload_speed_pct"}]}}],
        "상태에 value":            [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "stun", "value": 1}]}}],
        "dot coeff 없음":          [{"kind": "debuff", "spec": {"target": "all", "debuffs": [{"stat": "dot"}]}}],
        "dot에 value":             [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "dot", "coeff": 5, "value": 5}]}}],
        "dot interval 0":          [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "dot", "coeff": 5, "interval": 0}]}}],
        "dot 아닌데 coeff":         [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "stun", "coeff": 5}]}}],
        "디버프 duration 0":        [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "stun", "duration": 0}]}}],
        "디버프 max_stack 0":       [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "stun", "max_stack": 0}]}}],
        "디버프 irremovable 문자열": [{"kind": "debuff", "spec": {"target": "all",
                                                             "debuffs": [{"stat": "stun", "irremovable": 1}]}}],
        "attack debuffs 빈 목록":   [{"kind": "attack", "spec": {"coeff": 100, "target": "all", "debuffs": []}}],
        "attack 디버프 모르는 stat": [{"kind": "attack", "spec": {"coeff": 100, "target": "all",
                                                              "debuffs": [{"stat": "atk"}]}}],
        "repeat 음수":            [{"kind": "idle", "repeat": -1}],
        "spec 없는 summon":       [{"kind": "summon"}],
        "summon hp·hit_hp 둘 다 없음": [{"kind": "summon", "spec": {"count": 2}}],
        "summon hp 0":           [{"kind": "summon", "spec": {"hp": 0}}],
        "summon hit_hp 0":       [{"kind": "summon", "spec": {"hit_hp": 0}}],
        "summon hit_hp 소수":     [{"kind": "summon", "spec": {"hit_hp": 2.5}}],
        "summon hit_hp 없이 전환": [{"kind": "summon", "spec": {"hp": 10, "hit_hp_after": 2}}],
        "summon 전환 시각 없는 타수": [{"kind": "summon", "spec": {"hp": 10, "hit_hp": 3}}],
        "summon 전환 시각 0":      [{"kind": "summon", "spec": {"hp": 10, "hit_hp": 3, "hit_hp_after": 0}}],
        "summon hp 없이 전환 시각": [{"kind": "summon", "spec": {"hit_hp": 3, "hit_hp_after": 2}}],
        "summon count 0":        [{"kind": "summon", "spec": {"hp": 1, "count": 0}}],
        "summon share 1 초과":    [{"kind": "summon", "spec": {"hp": 1, "share": 1.5}}],
        "summon 모르는 칸":        [{"kind": "summon", "spec": {"hp": 1, "def": 100}}],
        "summon 공격력 없는 공격":  [{"kind": "summon", "spec": {"hp": 1, "attack": {"coeff": 10, "target": "all"}}}],
        "summon 공격 없이 자폭":    [{"kind": "summon", "spec": {"hp": 1, "self_destruct": True}}],
        "summon 공격 없이 attack_at": [{"kind": "summon", "spec": {"hp": 1, "attack_at": 3}}],
        "summon 공격의 모르는 칸":  [{"kind": "summon", "spec": {"hp": 1, "atk": 5,
                                                              "attack": {"coeff": 10, "target": "all", "at": 1}}}],
        "list 아님":             {"kind": "idle"},
    }
    for label, pats in bad_cases.items():
        try:
            validate(pats, weapon_types=frozenset({"SG", "SMG", "SR"}), squad_size=5)
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: {label}")
    print(f"검산 11 — 잘못된 스크립트 {len(bad_cases)}종 전부 거절")

    print("\n모든 검산 통과.")
