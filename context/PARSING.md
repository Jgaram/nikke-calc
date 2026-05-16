# 스킬 파싱 인스트럭션

니케 캐릭터 1명씩 `scraper/nikke_scraped.json`에서 해당 캐릭터 항목을 조회하여 `data/parsed_skills.json`에 효과 단위로 파싱한다.

`nikke_scraped.json`은 파일이 크므로 Bash에서 아래 방식으로 읽는다:

```python
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
with open('scraper/nikke_scraped.json', encoding='utf-8') as f:
    data = json.load(f)
# 캐릭터명 키로 해당 항목만 출력
print(json.dumps(data['캐릭터명'], ensure_ascii=False, indent=2))
```

`sys.stdout.reconfigure(encoding='utf-8')` 없이 실행하면 터미널 인코딩(cp949)으로 한글이 깨진다.
파싱 주체는 **Claude Code**이다. 텍스트가 규칙의 패턴과 표현이 조금 달라도 의미가 명백하면 판단하여 직접 수행한다. 판단이 불명확한 경우 즉시 유저에게 질문하고 답을 받은 후 진행한다.

> **속도 원칙**: 인스트럭션에 정의된 규칙을 그대로 적용한다. 스스로 해석을 넓히거나 대안을 고민하지 않는다. 패턴이 매핑되면 즉시 적용, 불명확하면 고민 없이 즉시 유저에게 질문한다.

> **파싱 범위**: `"스킬"` 딕셔너리(스킬1~3)만 파싱한다. `"무기상세"` (무기유형, 장탄 수, 재장전 시간, 무기스킬)는 Python에서 별도 관리하므로 파싱하지 않는다.

> **인스트럭션 수정 원칙**: 파싱 중 규칙 추가·수정·삭제가 필요하다고 판단되면, 직접 변경하지 말고 유저에게 먼저 제안한 후 승인을 받고 수정한다.

---

## 1. 입력 데이터 구조

```json
"캐릭터명": {
  "id": 10,
  "레어도": "SR",
  "클래스": "화력형",
  "기업": "엘리시온",
  "버스트 단계": "3",
  "무기상세": {
    "무기유형": "AR",
    "최대 장탄 수": "60",
    "재장전 시간": "1.00s",
    "무기스킬": "■ 대상에게\n[공격력 13.65% 대미지]\n[코어 대미지 200%]"
  },
  "스킬": {
    "스킬이름": {
      "쿨타임": "20.0 s",
      "template": "■ 전투 시작 시 아군 전체에게[공격력 {0}% ▲] [10초 유지]■ ...",
      "values": {
        "1": ["23.5"],
        "2": ["26.1"],
        "3": ["28.7"], // 레벨 3~9도 동일하게 포함
        ...
        "10": ["47.0"]
      }
    }
  }
}
```

- `template`: `■`으로 clause 구분. 각 clause = `[대괄호 앞 텍스트][효과블록1][효과블록2]...`
- `values`: 스킬 레벨 1~10. 각 레벨의 값은 **문자열 배열**. `{0}` → index 0, `{1}` → index 1. JSON에 있는 모든 레벨을 출력에 포함한다.
- `쿨타임`: `"20.0 s"` 형식 또는 `null`
- `"버스트 단계"` 필드(1/2/3)는 팀 버스트 순서를 나타내며, source 결정에 사용하지 않음

---

## 2. 출력 스키마

```json
{
  "캐릭터명": [
    {
      "source": "스킬1",
      "type": "buff",
      "name": "포메이션 F.F",
      "trigger": { "timing": ["battle_start"], "condition": [] },
      "target": "all_allies",
      "stat": "atk_pct",
      "polarity": "beneficial",
      "max_stack": 1,
      "values": { "1": 23.5, "2": 26.1, ..., "10": 47.0 }, // 레벨 1~10 전체 포함 (예시는 생략)
      "duration": 10.0
    },
    {
      "source": "스킬3",
      "type": "damage",
      "name": "다탄두 미사일",
      "trigger": { "timing": ["full_burst_start"], "condition": [] },
      "target": "all_enemies",
      "stat": "burst_damage",
      "values": { "1": 23.0, "2": 25.0, "10": 47.0 }
    },
    {
      "source": "스킬2",
      "type": "instant",
      "name": "미사일",
      "trigger": { "timing": ["burst_cast"], "condition": [] },
      "target": "self",
      "stat": "burst_cooldown_reduce",
      "values": { "1": 2.0, "2": 2.5, "10": 5.0 }
    }
  ]
}
```

### 필드 설명

| 필드 | 필수 | 적용 type | 설명 |
|------|------|-----------|------|
| `source` | ✅ | 전체 | `"스킬1"`, `"스킬2"`, `"스킬3"` |
| `type` | ✅ | 전체 | `"buff"`, `"damage"`, `"instant"`, `"weapon_change"` |
| `name` | ✅ | 전체 | 스킬 내 효과 이름(있으면). 없으면 스킬 키 이름 사용 |
| `trigger` | ✅ | 전체 | `{ "timing": [...], "condition": [...] }` |
| `target` | ✅ | 전체 | 효과 대상 (5절 참고) |
| `stat` | ✅ | buff/damage/instant | 효과 종류 (6절 참고) |
| `polarity` | ✅ | buff만 | `"beneficial"` / `"harmful"` / `"neutral"` (Step 6 참고) |
| `values` | ✅* | buff/damage/instant | 스킬레벨 1~10별 수치 (float). `fixed_value`와 둘 중 하나 필수 |
| `fixed_value` | ✅* | buff/damage/instant | 레벨 무관 고정 수치. `values`와 둘 중 하나 필수. 둘 다 쓰지 않는다 |
| `duration` | buff: ✅ / damage·instant: 선택 | buff, periodic damage | 지속시간(초). **buff type은 언제나 필수**. duration 블록이 없으면 `"duration": null`로 기입 후 유저에게 질문. damage는 DoT 등 주기 대미지에서만 사용. instant는 사용하지 않는다. |
| `duration_bullets` | 선택 | buff, weapon_change | `[N발 유지]`인 경우 |
| `tick_interval` | 선택 | damage, instant | 주기적 발동 간격(초). DoT·주기 자동공격·주기 회복 등에 사용 |
| `max_stack` | 선택 | buff | 중첩 한도. 명시 없으면 1. 무한 중첩이면 `-1` |
| `max_trigger` | 선택 | 전체 | 전투 중 최대 발동 횟수. `[전투 중 N회 발동]` 블록 또는 buff/instant의 `[N회 발동]` 블록에서 추출. damage type의 `[N회 발동]`은 stat에 `:N` suffix로 표현하므로 `max_trigger` 사용 안 함 |
| `damage_formula` | 선택 | damage | `"skill"`(기본값) 또는 `"normal_attack"` |
| `weapon_type` | 선택 | damage, weapon_change | 해당 대미지/무기변경에 사용되는 무기 유형. `weapon_change`에서는 필수, `damage`에서는 `damage_formula: "normal_attack"` 항목에 명시. 미명시 시 유저에게 질문 |
| `damage_coeff` | ✅ | weapon_change | 변경 무기 공격 계수. 레벨별이면 `{"1": 65.95, ...}`, 고정이면 float |
| `max_ammo` | 선택 | weapon_change | 최대 장탄 수. 장탄 수 무한 또는 미명시 시 `-1` |
| `reload_time` | 선택 | weapon_change | 재장전 시간(초). 미명시 시 생략 |
| `core_dmg_mult` | 선택 | weapon_change | 코어 대미지. 미명시 시 생략 |
| `charge_time` | 선택 | weapon_change | 차지 시간(초). SR/RL 전용, 미명시 시 생략 |
| `full_charge_mult` | 선택 | weapon_change | 풀 차지 대미지. SR/RL 전용, 미명시 시 생략 |
| `scaling` | 선택 | damage, instant, buff | 특수 스케일링 기준. 단일 문자열 또는 복수 적용 시 배열. `"max_hp"`: 최대 체력 비례. `"stack_count"`: 지정 스택/게이지 수 비례 (실제값 = values[level] × 현재 스택 수). `"max_hp_additive"`: 최대 체력 N%를 공격력에 합산 후 대미지 계산 (`scaling_hp_pct` 필드에 N 기입). `"lost_hp_pct"`: 잃은 체력 % 비례 (실제값 = values[level] × 잃은 체력%). 복수 사용 예: `"scaling": ["max_hp_additive", "stack_count"]` |
| `scaling_ref` | 선택 | damage, instant, buff | `scaling: "stack_count"` 사용 시 기준이 되는 버프/스택/게이지의 `name`. 생략 시 해당 효과 자신의 스택 기준 |
| `scaling_hp_pct` | 선택 | damage, instant | `scaling: "max_hp_additive"` 사용 시 합산할 최대 체력 비율(%) |
| `target_effect` | 선택 | buff, instant | 효과가 작용할 대상 효과의 `name`. `effect_interval`·`remove_named_buff` stat에서 필수 |
| `trigger_values` | 선택 | 전체 | timing의 N이 레벨마다 다를 때 사용. `timing`에 `"hit_count:{0}"` 형태로 플레이스홀더 기입, `trigger_values: {"1": 65, "2": 62, ...}`로 레벨별 값 기입. `note` 필드로 상황 설명 추가 |
| `duration_values` | 선택 | buff | `values`/`fixed_value` 없이 duration만 레벨별로 다를 때 사용. `duration` 대신 `duration_values: {"1": 2.57, ..., "10": 5.0}` 기입 |

---

## 3. 파싱 절차

### Step 1: source 결정

`"스킬"` 딕셔너리의 **삽입 순서**(Python 3.7+ 보장)로 결정:
- 1번째 키 → `"스킬1"`, 2번째 키 → `"스킬2"`, 3번째 키 → `"스킬3"`
- 3번째 스킬이 해당 캐릭터의 버스트 스킬

### Step 2: clause 분리

`template`을 `■`으로 분리. 각 clause:
```
[대괄호 앞 텍스트] [효과블록1] [효과블록2] ...
```

### Step 3: trigger 결정

대괄호 앞 텍스트에서 timing과 condition을 추출 (4절 참고).

template에 timing 키워드가 없으면:
- **스킬3(버스트 스킬)**이면 → timing: `["burst_cast"]`
- **스킬1/스킬2**이면 → `쿨타임` 필드 확인:
  - 쿨타임 필드가 있으면 → timing: `["every:Ns"]` (N = 쿨타임 값, `"15.0 s"` → `15.0`)
  - 쿨타임 필드도 없으면 → timing 불명, 유저에게 질문

### Step 4: 각 대괄호 블록 분류

> 각 블록을 분류하기 전에 **Step 7의 name 결정 규칙을 먼저 확인**한다. `[상태명]` 단독 블록은 Rule 0(스킵)이 아닌 Step 7 규칙으로 처리한다.

| 블록 패턴 | 처리 방법 |
|-----------|----------|
| `N초 유지` | 해당 clause에서 직전에 생성된 효과 항목의 `duration`(초)으로 기록 |
| `N발 유지` | 해당 clause에서 직전에 생성된 효과 항목의 `duration_bullets`로 기록 |
| `지속` | 해당 clause에서 직전에 생성된 효과 항목의 `"duration": -1`로 기록 (종료 조건 없는 상시 지속) |
| `최대 장탄 재장전 완료 시 삭제` | 직전 효과에 `"duration": -1` 기록. 추가로 `event:full_reload` timing의 `remove_named_buff` instant 항목을 별도 생성 (target_effect = 직전 효과의 name) |
| `N초 간격` | 해당 clause 직전 효과 항목의 `tick_interval`로 기록 |
| `N중첩` | 해당 clause 직전 효과 항목의 `max_stack`으로 기록 |
| `N회 순차 공격` | 해당 clause 직전 효과 항목의 `stat`을 `"sequential_damage:N"` 형태로 갱신 |
| `[게이지명/스택명] 갯수만큼 공격` / `[게이지명/스택명] 수만큼 공격` | "순차 공격" 문구 없이 게이지/스택 수에 비례한 공격 횟수. 직전 damage 항목에 `"scaling": "stack_count"`, `"scaling_ref": "게이지명/스택명"` 추가. target은 `"enemies_random"` (무작위 배분) 또는 원문 그대로. |
| `N회 발동` | 해당 clause 직전 효과 항목이 damage type이면 stat을 `"stat_base:N"` 형태로 갱신 (예: `bonus_damage` → `bonus_damage:5`). damage 외 type이면 `max_trigger`로 기록 |
| `전투 중 N회 발동` | 해당 clause 직전 효과 항목의 `max_trigger`로 기록 |
| `[사용 횟수 별 효과]`, `[시작 횟수 별 효과]`, `[하위 효과 중복 적용]` | 7-3절 참고하여 flat expansion |
| 그 외 효과 블록 | type/stat/values 결정 후 항목 생성 |

### Step 5: value 추출

- 템플릿의 `{0}` → `float(values[level][0])` (레벨 1~10 각각)
- `{1}` → `float(values[level][1])`, 이하 동일
- `{N}` 없이 숫자가 고정된 블록 → `"fixed_value": 200.0` (레벨 무관)
- template의 `{N}` 개수와 values 배열 길이가 맞지 않으면 유저에게 질문

### Step 6: polarity 결정 (buff만)

`type: "buff"`인 항목에 `polarity` 필드를 기입한다.

아래 목록을 참고해 결정. 판단이 어려우면 `neutral`로 분류. `[해제 불가]` 블록이 있으면 값 뒤에 `_irremovable` suffix 추가 (예: `"beneficial_irremovable"`).

**harmful이 되는 케이스** (values 양수일 때 해로운 stat):

| stat | 설명 |
|------|------|
| `received_dmg_pct` | 받는 대미지 증가 |
| `skill_cooldown` | 스킬 쿨타임 증가 |
| `effect_interval` | 효과 발동 간격 증가 |
| `charge_time` | 차지 시간 증가 |
| `charge_time_caster_based` | 시전자 기준 차지 시간 증가 |

위 stat이라도 values 음수면 `"beneficial"`. 반대로 그 외 stat도 values 음수면 `"harmful"`.

**neutral이 되는 케이스** (이로움/해로움 분류가 맞지 않는 stat):

| stat | 이유 |
|------|------|
| `focus_fire` | 사격 집중 — 기능 변경, 이로움/해로움 단순 분류 불가 |
| `burst_stage_override:N` / `burst_stage_override:reenterN` | 버스트 단계 변경/재진입 — 기능 변경 |
| `heal_split` | 체력 회복 균등 분배 — 기능 변경 |
| `taunt` | 적 주목/도발 — 기능 변경 |

### Step 7: name 결정 및 출력 추가

- `name`: 스킬 내 효과 이름이 있으면 아래 세 형태 중 하나로 나타난다:
  - `[포메이션 AS : 공격력 {0}% ▲]` 형태 → 콜론 앞의 이름(`포메이션 AS`)을 사용
  - 효과 블록 뒤에 `[상태명]`(수치·stat 없음)이 단독으로 오는 형태 → 독립 항목 미생성, 직전 효과의 `name`으로 설정. 이어지는 `[N초 유지]` 등도 직전 효과에 귀속
  - clause의 **첫 번째 블록**이 `[상태명]`(수치·stat 없음)인 형태 → 해당 clause에서 생성되는 **모든** 효과 항목의 `name`으로 사용. 독립 항목 미생성
- 효과 이름이 없으면 `스킬 키 이름_stat` 형태로 조합한다 (예: 스킬 키 이름 `"미사일"`, stat `"atk_pct"` → `"미사일_atk_pct"`). 같은 조합이 또 겹치면 그 뒤에 1, 2, 3을 붙여 구분한다 (예: `"미사일_atk_pct 1"`, `"미사일_atk_pct 2"`).
- **캐릭터 전체 파싱 결과에서 `name`은 절대 중복되어서는 안 된다.** named 항목(이름이 명시된 효과)끼리 같은 이름이 생기는 경우, 첫 번째 항목은 원래 이름을 유지하고 두 번째부터 뒤에 2, 3을 붙여 구분한다 (예: `"터진 거품"`, `"터진 거품 2"`, `"터진 거품 3"`). calculator는 `target_effect` 등으로 이 이름을 참조할 때 첫 번째 항목을 기준으로 한다.
- 하나의 clause에서 여러 효과가 나올 수 있음. 효과마다 별도 항목, trigger는 동일하게 공유

---

## 4. Trigger 결정 규칙

> **동기화 규칙**: 이 절의 timing/condition 목록에 새 항목을 추가할 때는 **반드시 `MAINTENANCE.md`의 trigger/condition 마스터 테이블에도 동시에 추가**한다. 구현 상태(✅/⚠️/❌)와 처리 위치도 함께 기록한다.

### 4-1. timing 매핑

| 텍스트 패턴 | timing 값 |
|------------|-----------|
| `전투 시작 시` | `"battle_start"` |
| `풀 버스트 타임 시작 시` / `풀 버스트 타임 진입 시` | `"full_burst_start"` |
| `풀 버스트 타임 N회 시작 시` | `"full_burst_start_count:N"` |
| `풀 버스트 타임 종료 시` | `"full_burst_end"` |
| `풀 버스트 타임 N회 종료 시` | `"full_burst_end_count:N"` |
| `버스트 N단계 진입 시` | `"burst_enter:N"` |
| `버스트 스킬 사용 시` | `"burst_cast"` |
| `버스트 스킬 N회 사용 시` | `"burst_cast_count:N"` |
| `마지막 탄환 명중 시` | `"last_bullet"` |
| `일반 공격 N회 명중 시` | `"hit_count:N"` |
| `일반 공격 크리티컬 N회 명중 시` | `"crit_hit_count:N"` |
| `풀 차지 시` | `"full_charge"` |
| `풀 차지 공격 시` / `풀 차지 공격 명중 시` | `"full_charge_hit"` |
| `풀 차지 N회 공격 시` | `"full_charge_count:N"` |
| `코어 N회 명중 시` | `"core_hit_count:N"` |
| `파츠 N회 명중 시` | `"part_hit_count:N"` |
| `N회 피격 시` | `"received_hit_count:N"` (N 미명시 시 기본값 1, 즉 `"received_hit_count:1"`) |
| `피격 시` (횟수 없음) | `"received_hit_count:1"` |
| `적 처치 시` / `적 격추 시` | `"enemy_death"` |
| `N초 마다` / `N초마다` | `"every:Ns"` |
| `N 중첩 마다` | `"every_stack:N"` |
| `공격 시` | `"on_attack"` |
| `파츠 파괴 시` | `"event:part_destroy"` |
| `엄폐 시` | `"event:cover"` |
| `아군 전투불능 시` | `"event:ally_down"` |
| `자신을 포함한 아군 누군가의 체력이 N% 이하 도달 시` / `아군 누군가의 체력이 N% 이하 도달 시` | `"event:ally_hp_below:N"` |
| `자신이 전투불능 시` | `"event:self_down"` |
| `체력 N% 이하 도달 시` | `"hp_below:N"` |
| `[사용 횟수 별 효과]` + `체력 N% 이하 도달 시` (단계별) | `"hp_below_count:N:순서"` — N번째 도달 시에만 발동. 각 단계에 `max_trigger:1` 병기 |
| `자신이 생존해있을 때 한하여` | `"passive"` |
| `최초 발동 시` | `"first_trigger"` |
| `아군이 버스트 스킬 사용 시` | `"event:ally_burst_cast"` |
| `버스트 N 사용 시` (팀 버스트 단계) | `"team_burst_cast:N"` |
| `엄폐물 피격 시` | `"event:cover_hit"` |
| `N명 이상 동시 명중 시` | `"multi_hit:N"` |
| `코어 명중 시` (횟수 없음) | `"core_hit_count:1"` |
| `풀 차지 상태를 N초 이상 유지 시` | `"charge_hold:N"` |
| `마지막 탄환 공격 시` / `마지막 탄환 공격 후` | `"last_bullet_fire"` |
| `펠릿 N회 명중 시` | `"pellet_hit_count:N"` |
| `최대 장탄 재장전 완료 시` | `"event:full_reload"` |
| `파괴 가능한 발사체 파괴 시` | `"event:projectile_destroy"` |
| `적 등장 시` / `랩처 등장 시` | `"event:enemy_spawn"` |
| `타겟이 출현 시` | `"event:target_spawn"` |
| `회복 효과 적용 시` | `"event:heal_received"` |
| `보호막 적용 시` | `"event:shield_applied"` |
| `보호막 소모 시` | `"event:shield_consumed"` |
| `아군 탄환 N발 소비 시` | `"team_ammo_consume:N"` |
| `[상태명] 상태 종료 시` | `"event:state_end:[상태명]"` |
| `[상태명/스킬명] 상태 적용 후` / `[상태명/스킬명] 적용 시` | `"event:[상태명/스킬명]"` |
| template에 timing 없고 쿨타임 필드 있음 | `"every:Ns"` (N = 쿨타임 값) |
| `[무기명] 명중 시` (weapon_change 무기 명중) | `"weapon_hit:[name]"` (name = weapon_change 항목의 `name` 값) |

**`passive` 의미**: 전투 전반에 상시 활성. 조건 필드(`condition`)에 추가 제약이 있으면 그 조건 충족 시에만 유지.

복합 트리거 (`전투 시작 시와 풀 버스트 타임 종료 시` 등) → timing 배열에 둘 다 기입:
```json
"timing": ["battle_start", "full_burst_end"]
```

### 4-2. condition 매핑

| 텍스트 패턴 | condition 값 |
|------------|-------------|
| `풀 버스트 타임 중` / `풀 버스트 타임 지속 중` | `"during_full_burst"` |
| `N% 확률로` | `"prob:N"` |
| `자신의 체력이 N% 이상` | `"self_hp_above:N"` |
| `자신의 체력이 N% 이하` | `"self_hp_below:N"` |
| `자신이 [상태명] 상태라면` | `"self_state:상태명"` |
| `대상이 [상태명] 상태라면` | `"target_state:상태명"` |
| `동일 스쿼드 아군이 있다면` | `"squad_ally_exists"` |
| `코어가 아니라면` | `"not_core"` |
| `후열에 배치됐을 때` | `"back_row"` |
| `[스택명] N 중첩 이상이라면` | `"self_stack_above:스택명:N"` |
| `자신의 체력이 최대일 때` | `"self_hp_max"` |
| `아군의 체력이 N% 이하` | `"ally_hp_below:N"` |
| `아군의 체력이 최대일 때` | `"ally_hp_max"` |
| `차지 중` | `"during_charge"` |
| `보호막 지속 중` / `보호막 적용 상태라면` | `"during_shield"` |
| `재장전 중` | `"during_reload"` |
| `포커싱 상태` | `"focusing"` |
| `직전에 버스트 스킬을 사용한` | `"burst_casted"` |
| `직전에 버스트 스킬을 사용하지 않은` | `"burst_not_casted"` |
| `풀 버스트 타임이 아닐 때` / `풀 버스트 타임 외` | `"not_during_full_burst"` |
| `[스택명] 최대 중첩 상태라면` | `"self_stack_above:스택명:최대중첩수"` (max_stack 값으로 N 기입) |
| `자신이 [상태명] 상태가 아니라면` | `"not_self_state:상태명"` |
| `기본 버스트 단계가 Step 1인 아군이 없다면` | `"no_burst1_ally"` |
| `기본 버스트 단계가 Step 1인 아군이 있다면` | `"has_burst1_ally"` |
| `코어 명중 시` (timing이 아닌 condition으로 쓰일 때) | `"core_hit_count:1"` |
| `[게이지명] 보유 상태라면` / `[게이지명]이 1 이상이라면` | `"gauge_above:게이지명:1"` |
| `[게이지명]이 N이라면` / `[게이지명]이 N이상이라면` | `"gauge_eq:게이지명:N"` / `"gauge_above:게이지명:N"` |
| `[게이지명]이 N미만이면` | `"gauge_below:게이지명:N"` |

---

## 5. Target 결정 규칙

> **동기화 규칙**: 이 절의 target 목록에 새 항목을 추가할 때는 **반드시 `MAINTENANCE.md`의 target 마스터 테이블에도 동시에 추가**한다. lazy resolve 필요 여부와 구현 상태도 함께 기록한다.

대괄호 앞 텍스트의 끝부분에서 대상을 결정한다.

| 텍스트 패턴 | target 값 |
|------------|-----------|
| `자신에게` | `"self"` |
| `아군 전체에게` | `"all_allies"` |
| `자신을 제외한 아군 전체에게` | `"all_allies_excl_self"` |
| `아군 N기에게` | `"allies:N"` |
| `자신과 양 옆에 있는 아군 N기에게` | `"allies_adjacent:N"` |
| `최종 공격력이 가장 높은 아군 N기에게` | `"allies_top_atk:N"` |
| `자신을 제외한 최종 공격력이 가장 높은 아군 N기에게` | `"allies_top_atk_excl:N"` |
| `남은 체력이 가장 낮은 아군 N기에게` | `"allies_lowest_hp:N"` |
| `최종 방어력이 가장 높은 아군 N기에게` | `"allies_top_def:N"` |
| `최종 공격력이 가장 낮은 기본 버스트 단계가 Step 3인 아군 N기에게` | `"allies_lowest_atk_burst3:N"` |
| `무작위 아군 N기에게` | `"allies_random:N"` |
| `샷건 소지 아군 전체에게` | `"allies_weapon:SG"` |
| `자신을 제외한 샷건 소지 아군 전체에게` | `"allies_weapon_excl_self:SG"` |
| `스나이퍼 라이플 소지 아군 전체에게` | `"allies_weapon:SR"` |
| `화력형 아군 전체에게` | `"allies_class:공격"` |
| `방어형 아군 전체에게` | `"allies_class:방어"` |
| `지원형 아군 전체에게` | `"allies_class:지원"` |
| `수냉/작열/전격 코드 아군 전체에게` | `"allies_code:수냉"` 등 |
| `풍압/수냉/작열/전격 코드 적 전체에게` | `"enemies_code:풍압"` 등 |
| `남은 체력 수치가 가장 낮은 풍압/수냉 코드 적 N기에게` | `"enemies_lowest_hp_code:풍압:N"` 등 |
| `적 전체에게` | `"all_enemies"` |
| `최종 공격력이 가장 높은 적 N기에게` | `"enemies_top_atk:N"` |
| `최종 방어력이 가장 높은 적 N기에게` | `"enemies_top_def:N"` |
| `최종 방어력이 가장 낮은 적 N기에게` | `"enemies_lowest_def:N"` |
| `남은 체력 수치가 가장 낮은 적 N기에게` | `"enemies_lowest_hp:N"` |
| `남은 체력 비율이 가장 낮은 아군 N기에게` | `"allies_lowest_hp:N"` |
| `무작위 적 N기에게` | `"enemies_random:N"` |
| `가장 가까운 적 N기에게` | `"enemies_nearest:N"` |
| `조준선에 가장 가까운 적 N기에게` | `"enemies_nearest:N"` |
| `공격 범위 내 적들에게` | `"enemies_in_range"` |
| `조준선에 가장 가까운 공격 범위 내 적들에게` | `"enemies_nearest_in_range"` |
| `타겟에게` / `대상에게` | `"target"` |
| `대상 본체에게` | `"target_body"` |
| `동일 적 대상에게` | `"same_target"` — 연계 대상이 명시된 경우 `"same_target:[name]"` 형태로 기입. `[name]`은 연계 damage 항목의 `name` 값. calculator는 해당 항목이 명중한 대상마다 이 효과를 1회 적용한다. |
| `대상과 주변의 적 N기에게` | `"target_and_nearby:N"` |
| `자신의 엄폐물에게` | `"self_cover"` |
| `자신보다 최종 방어력이 낮은 아군 전체에게` | `"allies_below_def"` |
| `[버프명] 상태인 적 전체에게` | `"enemies_with_buff:버프명"` |
| `파괴 가능한 발사체 전체에게` | `"all_projectiles"` |

복합 대상 (`자신과 X에게` 등) → target 배열에 둘 다 기입:
```json
"target": ["self", "allies_below_def"]
```

대상이 명시되지 않거나 패턴에 맞지 않으면 유저에게 질문.

---

## 6. Stat 목록

> **동기화 규칙**: 이 절의 stat 목록에 새 항목을 추가할 때는 **반드시 `MAINTENANCE.md`의 stat 마스터 테이블에도 동시에 추가**한다. buffs 키, DealForm 항목, 구현 상태(✅/⚠️/❌/🚫)를 함께 기록한다.

> **buff stat 수치 방향**: stat은 방향 중립. 스킬 텍스트의 ▲ 또는 "증가" → `values` 양수, ▼ 또는 "감소" → `values` 음수로 저장. `instant` stat은 예외로 양수 = 효과 크기.
> 아래 ▲/▼ 표기는 해당 stat의 일반적 사용 방향 예시이며, 반대 부호로도 사용될 수 있다.

### 버프 stat

| stat | 의미 |
|------|------|
| `atk_pct` | 공격력 % ▲ |
| `hp_caster_based_pct` | 시전자 기준 최대 체력 % ▲ |
| `def_caster_based_pct` | 시전자 기준 방어력 % ▲ |
| `def_pct` | 방어력 % ▲ |
| `max_hp_pct` | 최대 체력 % ▲ (현재 체력도 동일 비율로 동반 증가. 텍스트: `최대 체력 N% ▲`) |
| `max_hp_only_pct` | 최대 체력만 % ▲ (현재 체력 유지. 텍스트에 "만"이 명시: `최대 체력만 N% ▲`) |
| `atk_caster_based_pct` | 시전자 기준 공격력 % ▲ |
| `atk_from_hp_pct` | **최종** 최대 체력 N%만큼 공격력 ▲ (버프 포함 최종 최대 체력 기준) |
| `crit_rate` | 크리티컬 확률 % ▲ |
| `normal_atk_crit_rate` | 일반 공격 크리티컬 확률 % ▲ |
| `crit_dmg` | 크리티컬 대미지 % ▲ |
| `normal_atk_crit_dmg` | 일반 공격 크리티컬 대미지 % ▲ |
| `core_dmg_pct` | 코어 대미지 % ▲ |
| `part_dmg_pct` | 파츠 대미지 % ▲ |
| `intercept_dmg_pct` | 저지 부위 공격 대미지 % ▲ |
| `atk_dmg_pct` | 공격 대미지 % ▲ |
| `burst_dmg_pct` | 버스트 스킬 대미지 % ▲ |
| `pierce_dmg_pct` | 관통 대미지 % ▲ |
| `dot_dmg_pct` | 지속 대미지 % ▲ |
| `split_dmg_pct` | 분배 대미지 % ▲ |
| `charge_dmg_pct` | 차지 대미지 % ▲ |
| `charge_dmg_mag_pct` | 차지 대미지 배율 % ▲ |
| `sequential_dmg_pct` | 순차 공격 대미지 % ▲ |
| `optimal_range_dmg_pct` | 적정 거리 대미지 % ▲ |
| `received_dmg_pct` | 받는 대미지 % ▲ |
| `heal_received_pct` | 받는 체력 회복량 % ▲ |
| `element_bonus_pct` | 우월 코드 공격 대미지 % ▲ |
| `normal_atk_dmg_pct` | 일반 공격 대미지 배율 % ▲ |
| `max_ammo_pct` | 최대 장탄 수 % ▲ |
| `max_ammo_flat` | 최대 장탄 수 N발 ▲ (고정값) |
| `pellet_count` | 펠릿 개수 N 증가 (고정값) |
| `pellet_count_fixed` | 펠릿 개수를 N개로 고정 (절대값 설정. 텍스트: `펠릿 개수 N개로 고정`) |
| `charge_speed_pct` | 차지 속도 % ▲ |
| `charge_speed_caster_based_pct` | 시전자 기준 차지 속도 % ▲ |
| `charge_time_caster_based` | (시전자 기준) 차지 시간 N초 ▼ (고정값, 초 단위) |
| `reload_speed_pct` | 재장전 속도 % ▲ |
| `attack_speed_pct` | 공격 속도 % ▲ |
| `accuracy_pct` | 명중률 % ▲ |
| `burst_charge_speed_pct` | 버스트 게이지 충전 속도 % ▲ |
| `optimal_range_max` | 최대 적정 사거리 N 증가 |
| `explosion_range` | 폭발 범위 N 증가 |
| `pierce_range` | 관통 범위 N 증가 |
| `pierce_enabled` | 관통 특화 (`values`/`fixed_value` 없음) |
| `fullburst_duration` | 풀버스트 타임 지속시간 N초 ▲ |
| `effect_interval` | 특정 효과의 발동 간격 N초 ▼ (`target_effect` 필수) |
| `lifesteal_pct` | 공격 대미지 비례 N% 체력 회복 |
| `armor_break_dmg_pct` | 방어력 무시 대미지 % ▲ |
| `projectile_dmg_pct` | 발사체에 가하는 대미지 % ▲ |
| `projectile_attachment_dmg_pct` | 발사체 부착 대미지 % ▲ |
| `projectile_explosion_dmg_pct` | 발사체 폭발 대미지 % ▲ |
| `burst_stage_override:N` | 자신의 버스트 단계를 N단계로 변경 (`values`/`fixed_value` 없음, `duration` 필수). 재진입이면 `burst_stage_override:reenterN` |
| `element_code_override` | 특정 코드 적에게 우월 코드 대미지 적용. `note`에 대상 코드 명시 (`values`/`fixed_value` 없음) |
| `trigger_count_reduce` | 특정 효과의 발동 횟수 조건 N회 ▼ (`target_effect` 필수, `fixed_value`에 감소량) |
| `shield_dmg_pct` | 보호막 대미지 % ▲ |
| `cover_def_pct` | 엄폐물 방어력 % ▲ |
| `cover_hp_pct` | 엄폐물 최대 체력 % ▲ |
| `outgoing_heal_pct` | 주는 체력 회복량 % ▲ |
| `shield_from_max_hp_pct` | 최대 체력 N%만큼 보호막 생성 |
| `heal_overcharge_store` | 시전자 기준 최대 체력 N%까지 초과 받는 체력 회복량 저장 |
| `shield_restore_pct` | 보호막 회복 % ▲ |
| `burst_dmg_single_pct` | 단일 대상 버스트 스킬 대미지 % ▲ |
| `burst_dmg_aoe_pct` | 전체 대상 버스트 스킬 대미지 % ▲ |
| `burst_cooldown` | 자신의 버스트 스킬 재사용 시간 N초 ▼ (buff 상태로 지속. named 상태 참조 가능) |
| `skill_cooldown` | 개별 스킬 쿨타임 N초 ▼ (`target_effect`로 대상 스킬 지정) |
| `skill_cooldown_pct` | 개별 스킬 쿨타임 N% ▼ (`target_effect`로 대상 스킬 지정. 음수 = 감소) |
| `stun` | 기절 (`values`/`fixed_value` 없음) |
| `invincible` | 무적 (`values`/`fixed_value` 없음, `duration` 필수) |
| `undying` | 불굴 (`values`/`fixed_value` 없음) |
| `stealth` | 은신 (`values`/`fixed_value` 없음) |
| `decoy` | 디코이 : 시전자의 최종 최대 체력 비례 {1}% 분신 |
| `infinite_ammo` | 장탄수 무한 (`values`/`fixed_value` 없음) |
| `focus_fire` | 사격 집중 (`values`/`fixed_value` 없음, `duration` 필수) |
| `enemy_movement_disable` | 적 이동 불가 (`values`/`fixed_value` 없음, `duration` 필수) |
| `debuff_immune` | 해로운 효과 면역 (`values`/`fixed_value` 없음) |
| `debuff_immune:[name]` | 특정 named debuff 면역. `[name]`에 debuff 이름 기입 (`values`/`fixed_value` 없음). 예: `debuff_immune:소음 공해` |
| `stun_immune` | 기절 면역 (`values`/`fixed_value` 없음) |
| `charge_speed_debuff_immune` | 차지 속도 감소 효과 면역 (`values`/`fixed_value` 없음) |
| `charge_speed_buff_immune` | 차지 속도 증가 효과 면역 (`values`/`fixed_value` 없음) |
| `stack_change_immune` | 중첩량 증감 효과 면역 (`values`/`fixed_value` 없음) |
| `charge_time_fixed` | 차지 시간 고정 |
| `atk_copy` | 공격력 복제 (복잡 메카닉, 파싱 불가 시 `_unparseable`) |
| `hp_copy` | 체력 복제 (복잡 메카닉, 파싱 불가 시 `_unparseable`) |
| `received_dmg_split` | 받는 대미지 차등 분배 (복잡 메카닉, 파싱 불가 시 `_unparseable`) |
| `heal_split` | 체력 회복 균등 분배 (복잡 메카닉, 파싱 불가 시 `_unparseable`) |
| `armor_break_enabled` | 일반 공격을 방어력 무시 대미지로 치환 (`values`/`fixed_value` 없음) |
| `gauge_charge_enabled` | 특정 게이지 충전 가능 상태 활성화 (`values`/`fixed_value` 없음, `gauge_id` 필수) |
| `gauge_max_add` | 게이지 최대값 N 일시 증가 (`gauge_id` 필수, `fixed_value`로 증가량, `duration`/`duration_bullets`로 유효기간) |
| `taunt` | 도발/주목. 대상을 시전자에게 강제 타겟 전환 (`values` 없음) |
| `lock_on` | 록 온 상태 부여 (`values`/`fixed_value` 없음). **스노우 화이트 : 헤비암즈 전용**. 세븐스 드워프의 공격 대상을 지정하는 고유 메카닉 |

### 대미지 stat

> **`:N` suffix**: 대미지 stat에 `"bonus_damage:5"` 처럼 `:N`이 붙으면 해당 stat을 1트리거당 N회 발사함을 의미한다. `[N회 발동]` 블록이 있는 damage type 항목에 적용한다. calculator는 이 값을 hit_count로 파싱한다.

| stat | 의미 |
|------|------|
| `damage` | 일반 대미지 (`공격력 X% 대미지` 또는 `최종 공격력 X% 대미지`) |
| `auto_damage` | 주기 자동공격 대미지. `damage_formula: "normal_attack"` + `tick_interval` 함께 사용 |
| `burst_damage` | 버스트 스킬 대미지 (텍스트에 "버스트 스킬 대미지" 명시 시에만 사용; 그 외 스킬3 대미지는 `damage`) |
| `dot_damage` | 지속 대미지 (tick_interval 추가 필요, duration 추가 필요). **buff 필수 필드도 함께 작성**: `polarity`(항상 `"harmful"` 또는 `"harmful_irremovable"`), `max_stack`(명시 시), `duration`(필수). 인게임에서 DoT는 해로운 효과 판정이므로 debuff_cleanse로 제거 가능. `[해제 불가]` 블록이 있으면 `"harmful_irremovable"` 사용. |
| `split_damage` | 분배 대미지 |
| `bonus_damage` | 추가 대미지 |
| `armor_break_damage` | 방어력 무시 대미지 |
| `pierce_damage` | 관통 대미지 |
| `projectile_explosion_damage` | 발사체 폭발 대미지 |
| `projectile_attachment_damage` | 발사체 부착 대미지 |
| `sequential_damage` | 순차 공격 대미지. `[N회 순차 공격]` 블록이 있으면 `stat: "sequential_damage:N"` 형태로 N을 stat에 포함. target은 `"enemies_random"` (N 미명시) 또는 `"enemies_random:N"`. N이 스택/게이지 기반으로 동적인 경우 `stat: "sequential_damage:스택명"` 형태로 기입하고 `scaling_ref`는 사용하지 않는다. |

### 인스턴트 stat

| stat | 의미 |
|------|------|
| `burst_cooldown_reduce` | 버스트 스킬 재사용 시간 N초 ▼ |
| `ammo_charge_pct` | 탄환 충전 N% |
| `ammo_charge_flat` | 탄환 충전 N발 |
| `burst_charge_pct` | 버스트 게이지 충전 N% |
| `heal_hp_pct` | 체력 회복 (시전자 최대 체력 N%) |
| `buff_stack_add` | 중첩형 이로운 효과 중첩 N 증가. 특정 named buff의 스택을 올리는 경우에 사용 |
| `buff_stack_remove` | 중첩형 이로운 효과 중첩 N 감소. 특정 named buff의 스택을 내리는 경우에 사용 |
| `debuff_stack_add` | 중첩형 해로운 효과 중첩 N 증가. 스택이 쌓이는 debuff에만 사용 |
| `debuff_stack_remove` | 중첩형 해로운 효과 중첩 N 감소. 스택이 쌓이는 debuff의 중첩을 줄이는 경우에만 사용. 단순 해제(스택 무관)는 `debuff_cleanse` 사용 |
| `remove_named_buff` | 특정 이름의 버프 전체 제거 (`target_effect` 필수, `values` 없음) |
| `debuff_cleanse` | 자신 또는 아군의 해로운 효과 단순 해제 — 스택 수와 무관하게 제거. (`values` 없음). 스택형 debuff의 중첩 감소는 `debuff_stack_remove` 사용 |
| `enemy_buff_cleanse` | 적의 이로운 효과 해제 (`values` 없음) |
| `force_reload` | 강제 재장전 (`values` 없음) |
| `current_hp_reduce` | 현재 체력 N% 감소 |
| `cover_heal_pct` | 엄폐물 체력 회복 (시전자 기준 N%) |
| `burst_reentry` | 버스트 재진입 (`values`/`fixed_value` 없음) |
| `force_move` | 공격 범위 중심 강제 이동 (복잡 메카닉, 파싱 불가 시 `_unparseable`) |
| `revive` | 부활 (`values`/`fixed_value` 없음) |
| `gauge_charge` | 게이지 N 충전 (`gauge_id` 필수) |
| `gauge_consume` | 게이지 N 소모 (`gauge_id` 필수) |

---

## 7. 예외 케이스 처리

### 7-1. 고정값 블록 (플레이스홀더 없음)

`[코어 대미지 200%]`처럼 template에 `{N}` 없이 숫자가 고정된 블록:
- `values` 대신 `"fixed_value": 200.0` 사용
- 스킬 레벨과 무관하게 항상 동일

### 7-2. 하나의 clause에 복수 효과

```
■ 버스트 스킬 사용 시 최종 공격력이 가장 높은 적 1기에게
  [최종 공격력 {0}% 버스트 스킬 대미지]
  [공격력 {1}% ▲] [10초 유지]
```

→ damage 항목 1개 + buff 항목 1개. trigger는 동일, values index만 다름.

### 7-3. 하위 효과 중복 적용 (단계 누적형)

`[하위 효과 중복 적용]` 블록이 있으면 각 단계를 독립 항목으로 flat expansion.

**예시 (버스트 사용 횟수별 누적):**
```
■ 버스트 스킬 사용 시 아군 전체에게
  [사용 횟수 별 효과] [하위 효과 중복 적용] :
  1회 : [최대 장탄 수 {0}% ▲] [5초 유지]
  2회 : [크리티컬 대미지 {1}% ▲] [5초 유지]
  3회 : [공격력 {2}% ▲] [5초 유지]
```

→ 3개의 독립 항목:
```json
{ "trigger": {"timing": ["burst_cast_count:1"]}, "stat": "max_ammo_pct", "duration": 5.0, ... },
{ "trigger": {"timing": ["burst_cast_count:2"]}, "stat": "crit_dmg", "duration": 5.0, ... },
{ "trigger": {"timing": ["burst_cast_count:3"]}, "stat": "atk_pct", "duration": 5.0, ... }
```

`중복 적용`이므로 N회 시점에 1~N번째 효과가 모두 활성 → 개별 항목이 각자 발동하면 자연히 누적됨.

`[하위 효과 중복 적용]` 없는 단계별 효과 (`[시작 횟수 별 효과]` 단독): 각 단계가 해당 횟수에만 발동하고 이전 단계는 비활성 → 동일하게 flat expansion.

**named state 조건 체인 (코인 예시):**
```
■ 전투 시작 시 후열 배치됐을 때 아군에게 [소드 코인 : 공격 대미지 {0}% ▲] [지속]
■ 풀 차지 30회 공격 시 자신이 소드 코인 상태라면 [실드 코인 : 받는 대미지 {1}% ▼] [지속]
```

→ 각 단계를 독립 항목으로, 이전 상태를 condition으로 참조:
```json
{ "trigger": {"timing":["battle_start"], "condition":["back_row"]},
  "name": "소드 코인", "stat": "atk_dmg", ... },
{ "trigger": {"timing":["full_charge_count:30"], "condition":["self_state:소드 코인"]},
  "name": "실드 코인", "stat": "received_dmg_pct", "polarity": "beneficial", ... }
```

### 7-4. DoT (지속 대미지)

type: `"damage"`, stat: `"dot_damage"`, `tick_interval` 추가.
tick_interval이 template에 명시되지 않은 경우 기본값 **1.0**.
duration이 template에 명시되지 않은 경우 `"duration": null`로 기입 후 유저에게 질문.

**DoT는 인게임에서 해로운 효과(debuff) 판정**이므로 buff 필수 필드도 반드시 작성한다:
- `polarity`: 항상 `"harmful"`. `[해제 불가]` 블록이 있으면 `"harmful_irremovable"`
- `max_stack`: 명시된 경우 기입
- `duration`: 필수 (미명시 시 `null` 기입 후 유저에게 질문)

```json
{ "type": "damage", "stat": "dot_damage", "tick_interval": 1.0, "duration": 5.0,
  "polarity": "harmful", "max_stack": 1, ... }
```

### 7-5. 주기 회복 (tick 기반 heal)

단일 트리거 이후 일정 간격으로 반복 회복하는 경우 `tick_interval`과 `duration`을 함께 사용한다.

```json
{ "type": "instant", "stat": "heal_hp_pct",
  "trigger": { "timing": ["burst_cast"], "condition": [] },
  "tick_interval": 1.0, "duration": 5.0, "values": { "1": 3.0, "10": 6.0 } }
```

단순히 스킬이 N초마다 발동하는 경우(쿨타임 또는 `N초마다` 텍스트)는 `every:Ns` timing을 사용하고 `tick_interval`은 불필요하다.

### 7-6. 특정 효과 발동 간격 단축

`[섬광 수류탄 투척 발동 시간 조건 1초 ▼]`처럼 특정 이름의 효과의 발동 간격을 단축하는 경우:

```json
{
  "type": "buff", "stat": "effect_interval",
  "target_effect": "섬광 수류탄 투척",
  "fixed_value": 1.0,
  "trigger": { "timing": ["burst_cast"], "condition": [] },
  "target": "self", "polarity": "beneficial", "duration": 10.0
}
```

### 7-7. 특정 버프 제거

특정 이름의 버프를 즉시 제거하는 경우. `values` 없음, `target_effect`에 제거 대상 버프의 `name`을 기입:

```json
{
  "type": "instant", "stat": "remove_named_buff",
  "target_effect": "소드 코인",
  "trigger": { "timing": ["burst_cast"], "condition": [] },
  "target": "self"
}
```

임의 이로운 효과 N중첩을 감소시키는 경우(대상 특정 없음)는 기존 `buff_stack_remove`를 사용한다.

### 7-8. Named buff (이름 있는 상태)

```
[포메이션 AS : 공격력 {0}% ▲]
```

효과 이름이 있으면 `name` 필드에 기록:
```json
{ "name": "포메이션 AS", "stat": "atk_pct", ... }
```

### 7-9. 스택 기반 효과

```
■ 자신에게 [공격력 {0}% ▲] [5중첩]
```

`max_stack: 5`. 스택당 수치 적용 방식은 기본적으로 **합산**으로 가정.

**단계 별 효과만 적용** (각 단계마다 다른 효과): 각 단계를 N중첩 이상 조건으로 분리하여 독립 항목으로 flat expansion:
```json
{ "condition": ["self_stack_above:스택명:1"], "stat": "atk_pct", ... },
{ "condition": ["self_stack_above:스택명:3"], "stat": "crit_rate", ... }
```
정확히 N중첩일 때만 발동하는 케이스(==N)는 현재 스키마로 표현 불가 → 유저에게 질문.

### 7-10. HP 비례 효과

`시전자의 최종 최대 체력 비례 N%` 형태:
- 버프면 stat: `atk_from_hp_pct` 등 별도 stat 사용
- 대미지면 stat: `damage`, 별도 `"scaling": "max_hp"` 필드 추가

```json
{ "type": "damage", "stat": "damage", "scaling": "max_hp", "values": {...} }
```

### 7-11. passive + HP 조건

`자신이 생존해있을 때 한하여` + `자신의 체력이 N% 이상` 조합:
timing: `"passive"`, condition: `["self_hp_above:N"]`.

### 7-12. 확률 기반 효과

`N% 확률로` → condition: `"prob:N"`. 타임라인에서 확률 판정.

### 7-13. 무기변경 스킬

`type: "weapon_change"` 사용. `stat` 없음. `damage_coeff`는 필수.

무기 스탯은 스킬 설명에 있는 값만 기입한다. 없으면 아래 기준에 따른다:

| 필드 | 미명시 시 처리 |
|------|--------------|
| `weapon_type` | 유저에게 질문 |
| `damage_coeff` | 필수 — 없으면 유저에게 질문 |
| `max_ammo` | `-1` (장탄 수 무한도 `-1`) |
| `reload_time` | 생략 |
| `core_dmg_mult` | 생략 |
| `charge_time` | 생략 (SR/RL 전용) |
| `full_charge_mult` | 생략 (SR/RL 전용) |

**지속시간 기반 (목단, 나유타 등):**
```json
{
  "source": "스킬3",
  "type": "weapon_change",
  "name": "무기변경",
  "trigger": { "timing": ["burst_cast"], "condition": [] },
  "target": "self",
  "weapon_type": "SR",
  "damage_coeff": { "1": 65.95, "10": 100.0 },
  "max_ammo": 6,
  "reload_time": 1.5,
  "core_dmg_mult": 200.0,
  "charge_time": 1.0,
  "full_charge_mult": 250.0,
  "duration": 10.0
}
```

**발수 기반 (츠바이, 은화:택티컬업 등):**
```json
{
  "type": "weapon_change",
  "weapon_type": "MG",
  "damage_coeff": { "1": 30.0, "10": 55.0 },
  "max_ammo": 20,
  "duration_bullets": 20
}
```

**장탄 수 무한 포함 (예시):**
```json
{
  "type": "weapon_change",
  "weapon_type": "???",
  "damage_coeff": { "1": 50.0, "10": 90.0 },
  "max_ammo": -1,
  "duration": 10.0
}
```

### 7-14. 주기 자동공격 (일반공격 판정 스킬)

무기변경 없이 스킬이 일정 시간 동안 일반공격 판정 대미지를 자동 발사하는 경우.

- 원래 무기는 유지됨 (타임라인 사격 루프 중단 없음)
- 각 타격은 일반공격 DealForm 적용 (`charge_dmg_pct` 등 차지 버프 미적용)
- `normal_atk_dmg_pct` 버프는 적용됨

**예시 (아니스:스타 스킬3 — 10초간 0.25초마다 자동발사):**
```json
{
  "source": "스킬3", "type": "damage", "stat": "auto_damage",
  "damage_formula": "normal_attack",
  "trigger": { "timing": ["burst_cast"], "condition": [] },
  "target": "target", "weapon_type": "RL", "tick_interval": 0.25, "duration": 10.0,
  "values": { "1": 55.0, "10": 100.0 }
}
```

### 7-15. 수치가 X 방정식인 효과

```
[공격력 {0}% X {1}중첩 ▲]
[우월 코드 공격 대미지 {0}% X 중첩량 ▲]
```

`scaling: "stack_count"` + `scaling_ref: "스택이름"` 으로 표현한다. 실제값 = `values[level] × 현재 스택 수`.

```json
{
  "stat": "atk_pct",
  "values": { "1": 5.0, "10": 10.0 },
  "scaling": "stack_count",
  "scaling_ref": "스택이름"
}
```

- `{0}% X {1}중첩` 형태: `{0}` → `values`, `{1}` → `max_stack` (레벨별 값)
- `{0}% X 중첩량` 형태: `{0}` → `values`, `scaling_ref`에 기준 버프/스택 이름 기입
- `[상태명 중첩 복사]` 블록: 직전 효과 항목에 `"scaling": "stack_count"`, `"scaling_ref": "상태명"` 추가. 실제 대미지/수치 = `values[level] × 현재 상태명 스택 수`
- 기준 스택이 불명확하면 유저에게 질문.

### 7-16. 게이지형 메카닉

스택과 구조는 동일하지만, 인게임에서 "중첩" 대신 "충전/소모" 표현을 사용하는 수치형 게이지. 스택과 별도 stat으로 구분한다.

- `gauge_id`: 게이지 식별자. 모든 게이지 관련 항목에 필수.
- 충전: `stat: "gauge_charge"`, `fixed_value` 또는 `values`로 충전량 기입
- 소모: `stat: "gauge_consume"`, `fixed_value` 또는 `values`로 소모량 기입
- 최대값: 스킬 텍스트에 `[최대 N 축적]` 등 최대값이 명시된 경우, 해당 게이지를 처음 정의하는 `gauge_charge` 항목에 `gauge_max: N` 필드 추가
- 최대값 일시 증가: `stat: "gauge_max_add"` (buff), `gauge_id` 필수, `fixed_value`로 증가량 기입. `duration` 또는 `duration_bullets`로 유효 기간 지정. 만료 시 자동으로 cap에서 제외됨
- 전체 소모: `[모든 N 삭제/소모]` → `gauge_consume`, `fixed_value: -1` (전체 소모 표기)
- 충전 가능 상태: `stat: "gauge_charge_enabled"` (buff), `gauge_id` 필수, `values`/`fixed_value` 없음
- `{0}% X [게이지명] 충전량 ▲` 형태: `scaling: "stack_count"`, `scaling_ref: "게이지명"`

```json
{ "type": "instant", "stat": "gauge_charge", "gauge_id": "화력 게이지", "fixed_value": 100.0,
  "trigger": { "timing": ["battle_start"], "condition": [] }, "target": "self" }

{ "type": "buff", "stat": "gauge_charge_enabled", "gauge_id": "화력 게이지",
  "polarity": "beneficial", "duration": 10.0 }

{ "type": "instant", "stat": "gauge_consume", "gauge_id": "화력 게이지", "fixed_value": 100.0 }
```

---

## 8. 파싱 불가 마킹

구조적으로 표현 불가한 복잡 메카닉의 경우, clause 내 파싱된 항목이 하나도 없을 때만 항목에 `"_unparseable": true`와 `"_raw": "해당 clause 전체 원본 텍스트"` 추가 후 유저에게 질문한다. 일부 블록만 스킵한 경우에는 `_raw`를 기록하지 않고, 파싱 완료 후 스킵된 블록 목록을 유저에게 보고한다. 특정 패턴(스택 단계별 효과 등)에 대해서는 7절 각 항목 참고.

```json
{
  "source": "스킬2",
  "type": "buff",
  "name": "생존본능",
  "_unparseable": true,
  "_raw": "생존본능 단계 별 효과만 적용 ..."
}
```

---

## 9. 캐릭터별 예외 사항

파싱 중 발견된 캐릭터 고유의 특이 메카닉을 기록한다.

| 캐릭터 | 스킬 | 내용 |
|--------|------|------|
| 라피 : 레드 후드 | 스킬2 | `[전격 코드 적에게 우월 코드 대미지 적용]` — 전격 코드 속성 적에게 자신의 코드와 무관하게 우월 코드 대미지를 적용하는 고유 메카닉. `element_code_override` stat, `note: "전격 코드 적에게 우월 코드 대미지 적용"` |
| 라피 : 레드 후드 | 스킬1 | `[스쿼드 구성 별 효과] [해당되는 효과만 적용]` — 분기 구조 메타 블록. 블록 자체는 스킵하고 하위 조건(`no_burst1_ally` / `has_burst1_ally`)을 condition으로 추출 |
| 아르카나 | 스킬1, 스킬2 | `직전에 버스트 스킬을 사용한 기본 버스트 단계가 Step 3인 전격 코드 아군` — "Step 3 + 전격 코드 + 직전 버스트 사용" 복합 target은 기존 패턴 없음. 실제 해당 캐릭터가 이사벨 1명뿐이므로 `target: "이사벨"` 고정으로 처리. |
| 아르카나 | 스킬1 | `[마법사 카드 : 스킬2 재사용 시간 75%▼]` — % 기반 스킬 쿨타임 감소. 기존 `skill_cooldown`(초 단위)과 별도 stat `skill_cooldown_pct`로 신규 추가. `fixed_value: -75.0` |
| 아르카나 | 스킬2 | `[죽음 카드 : 버스트 스킬 재사용 시간 {1}초 ▼] [시전자 기준 공격력 {2}% ▲] [5초 유지]` — 하나의 named 효과(죽음 카드)에 두 stat이 묶인 예외 케이스. `burst_cooldown_reduce`는 name 규칙(`순환하는 운명_burst_cooldown_reduce`)으로 instant 처리, `atk_caster_based_pct`가 `죽음 카드` name을 가지는 buff로 처리. |
| 리버렐리오 | 스킬2 | `풀 차지 공격 명중 시 대상이 타겟이 아닌 랩쳐라면 자신에게` clause 전체 삭제. 계산기는 보스전(타겟 단독) 대상이므로 타겟이 아닌 랩쳐를 공격하는 경우가 없음. |
| 메이든 : 아이스 로즈 | 스킬3 | `[시전자의 최종 최대 체력의 10%를 공격력으로 합산한 {0}% 대미지]` — 공격력에 최대 체력의 10%를 더한 값을 기준으로 대미지 계산. `scaling: "max_hp_additive"`, `scaling_hp_pct: 10.0`으로 표기. 실제값 = (ATK + MaxHP × 0.10) × values[level] / 100 |
| 홍련 : 흑영 | 스킬1 | `[공격 횟수 별 효과] [단계별 효과만 적용]` — 파죽1→2→3 순환 구조. `full_charge_count:N` timing 대신 가상 게이지 `파죽`(max 3)으로 표현. 전투 시작 시 게이지 1로 초기화, 풀차지 3회마다 게이지 +1, 게이지가 1/2/3일 때 각각 파죽1/2/3 발동, 파죽3 발동 후 게이지 -3(리셋). 파죽1/2/3 모두 `full_charge_count:3` + `gauge_eq:파죽:N` condition 조합으로 표현. |
| 홍련 : 흑영 | 스킬3 | 만개 발동 시 풀차지 3회→1회 조건 단축 — `trigger_count_reduce` 1개로 통합, `target_effect: "파죽 게이지"`, `fixed_value: 2.0`. 게이지 충전 항목(`파죽 게이지`)의 `full_charge_count:3`을 2 줄여 1회마다 충전되도록 함. |
| 미하라 : 본딩 체인 | 스킬1 | `[포획 사슬 갯수만큼 공격] [공격 당 포획 사슬 1개 ▼]` — 포획 사슬 게이지 수만큼 공격하고 공격마다 1개 소모. 실질적으로 트리거 시점에 포획 사슬 게이지 전량 소모(`gauge_consume: fixed_value: -1`)와 동치로 파싱. 발사 횟수는 `바디 컨텍_damage`의 `scaling: "stack_count", scaling_ref: "포획 사슬"`로 표현. |
| 미하라 : 본딩 체인 | 스킬1 | `[개별 대상 사슬 감기 중첩 복사]` (스킬3 `사슬 당기기`) — 단일 적 가정이므로 "개별 대상" 구분 불필요. `scaling: "stack_count"`, `scaling_ref: "사슬 감기"`로 파싱. 복수 적 환경에서는 대상별 독립 스택 참조 로직 별도 구현 필요. |
| 디젤 : 윈터 스위츠 | 스킬1 | `[부활 시 유지]` 블록 스킵 — 인트로·클라이막스 buff 모두에 붙음. 부활 후에도 버프가 유지됨을 의미하나 시뮬레이터에 부활 모델 없으므로 무시. |
| 아르카나 : 포츈 메이트 | 스킬3 | `[추억 남기기]` — crit_rate, ammo_charge_flat, atk_dmg_pct 3개 효과를 하나의 named state로 묶음. name 분리(`추억 남기기`, `추억 남기기 2`, `추억 남기기 3`)로 처리. 스킬1 full_burst_end에서 `remove_named_buff` 5개 instant로 `추억 남기기`, `추억 남기기 3`, `행복한 기억`, `청춘의 기록`, `소중한 추억` 전부 제거. `self_state:추억 남기기` condition은 crit_rate buff(첫 번째 항목) 기준. |
| 아르카나 : 포츈 메이트 | 스킬2 | `[공격 횟수 별 효과]` + `[추억 남기기 해제 시 초기화]` — 추억 남기기 상태 내 로컬 공격 횟수 카운터. 가상 게이지 `공격 횟수`(gauge_max 없음)로 표현. `on_attack` + `self_state:추억 남기기` 시마다 게이지 +1. **6발 사이클**: 2nd/8th/14th→탄환충전 6발(`ammo_charge_flat`), 4th/10th/16th→`행복한 기억`(pellet_count +1, max_stack:3) + `청춘의 기록`(normal_atk_dmg_pct +10, max_stack:3), 6th/12th/18th→`소중한 추억`(atk_pct, max_stack:3). 각 단계는 `gauge_eq:공격 횟수:N` condition으로 발동하는 독립 항목 3개씩 열거. full_burst_end에서 `gauge_consume: fixed_value:-1`(전량 소모)로 리셋. |
| 아르카나 : 포츈 메이트 | 스킬1 | `[시전자 기준 공격력 {0}% X 소중한 추억 중첩 수 ▲]` — `atk_caster_based_pct` + `scaling: "stack_count"`, `scaling_ref: "소중한 추억"`. 두 stat의 조합: 시전자 ATK 기준 환산 후 소중한 추억 스택 수 곱셈. |

---

## 10. 유저에게 물어봐야 할 시점

다음 상황에서 진행을 멈추고 유저에게 질문한다:

0. **알 수 없는 블록**: Step 4 분류표와 4~6절 어디에도 매핑되지 않는 대괄호 블록이 등장하면 → 해당 **블록만** 스킵하고 나머지 블록은 계속 파싱. clause 내 파싱된 항목이 하나도 없을 때만 clause 전체를 `_unparseable` 마킹 후 즉시 질문. 일부 블록만 스킵한 경우는 파싱 완료 후 스킵된 블록 목록을 보고. 패턴 추론·유추 시도 금지.
1. **trigger 불명확**: 대괄호 앞 텍스트가 알려진 패턴에 맞지 않음
2. **target 불명확**: 대상 텍스트가 알려진 패턴에 맞지 않음
3. **스택 단계별 효과**: `단계 별 효과만 적용` 등 각 단계 수치가 다를 때
4. **복잡 메카닉**: 위 규칙으로 표현 불가한 고유 메카닉
5. **값 불일치**: template의 `{N}` 개수와 values 배열 길이가 맞지 않음
6. **timing 불명**: template에 알려진 timing 키워드가 없고 쿨타임 필드도 없음
7. **weapon_type 미명시**: 무기변경 스킬인데 변경 무기 유형이 스킬 설명에 없음
8. **polarity 판단 불명확**: 이로운/해로운 어느 쪽인지 결정 불가

---

## 11. 처리 순서

1. **12절 목록 확인**: `예정` 항목 중 첫 번째 캐릭터부터 순서대로 파싱한다. `완료` 및 `보류` 항목은 건너뛴다.
2. `parsed_skills.json` 파일이 있으면 읽어서 기존 데이터 유지. 없으면 빈 딕셔너리 `{}` 로 시작.
3. 캐릭터명 확인. 이미 `parsed_skills.json`에 해당 캐릭터가 있으면 유저에게 덮어쓸지 질문.
4. `스킬` 순서대로 (스킬1→스킬2→스킬3) 각 clause 파싱.
5. `_unparseable` 항목이 없으면 → 해당 캐릭터 항목 전체를 `parsed_skills.json`에 저장.
   `_unparseable` 항목이 하나라도 있으면 → 해당 캐릭터 항목 전체를 `unparsed_skills.json`에 저장. `parsed_skills.json`에는 넣지 않는다.
6. **12절 목록 갱신**: `_unparseable` 항목이 있으면 `진행 중`으로, 없으면 `완료`로 이동시킨 뒤 저장.
7. 다음 `예정` 캐릭터로 이동.

---

## 12. 니케 목록 및 현황

파싱 대상 캐릭터 목록. `예정` 항목만 파싱하며, 완료 시 해당 캐릭터를 `완료`로 이동시킨다. 파싱이 온전히 안 된 경우에는 `진행 중`으로 이동시킨다. `보류`는 파싱하지 않는다.

### 완료

그레이브
나가
나유타
드레이크
라피 : 레드 후드
마스트 : 로망틱 메이드
앵커 : 이노센트 메이드
크라운
홍련 : 흑영
신데렐라
리틀 머메이드
아니스 : 스타
리버렐리오
네온 : 비전 아이
메이든 : 아이스 로즈
스노우 화이트 : 헤비암즈
목단
미하라 : 본딩 체인
도로시 : 세렌디피티
토브
이사벨
아르카나
헬름
아르카나 : 포츈 메이트
디젤 : 윈터 스위츠
프리바티
솔린 : 프로스트 티켓
미란다
브리드 : 사일런트 트랙

### 진행 중

D : 킬러 와이프
E.H.

### 예정
누아르
델타 : 닌자 시프
도라
라플라스
레드 후드
레오나
레이
레이 (가칭)
레이븐
렘
로산나
로산나 : 시크 오션
루드밀라 : 윈터 오너 ✅
루주
루피 : 윈터 쇼퍼
리타
마나
마리
맥스웰
메어리 : 베이 갓데스
모더니아
밀크 : 블루밍 바니
바이퍼
베스티 : 택티컬 업
벨벳
볼륨
브래디
블랑
사쿠라 : 블룸 인 서머
소다 : 트윙클링 바니
스노우 화이트
아니스 : 스파클링 서머
아비스타
아스카
아스카 : WILLE
아인
앨리스
앨리스 : 원더랜드 바니
에이다
에이드 : 에이전트 바니
엑시아
엠마 : 택티컬 업
율리아
율하
은화 : 택티컬 업
이브
일레그 : 붐 앤 쇼크
질
차임
츠바이
치사토
퀀시 : 이스케이프 퀸
크러스트
타키나
트리나
티아
팬텀
프리바티 : 언카인드 메이드
헬름 : 아쿠아마린
홍련

### 보류

길로틴 : 윈터 슬레이어
노벨
도로시
라푼젤
에밀리아
센티
시그널
길티
2B
A2
플로라
밀크
프림
폴리
디젤
베이
얀
마르차나
메어리
키리
슈가
페퍼
루피
브리드
노아
D
루마니
은화
클레이
마스트
노이즈
베스티
메이든
소라
K
크로우
트로니
에이드
비스킷
에피넬
네로
루드밀라
로산다
라푼젤 : 퓨어 그레이스
유니
하란
스노우 화이트 : 이노센트 데이즈
코코아
앤 : 미라클 페어리
폴크방
레이블
모리
신
루피 : 위터 쇼퍼
길로틴
자칼
사쿠라
애드미
엠마
일레그
솔린
니힐리스타
아리아
네온 : 블루 오션
미카 : 스노우 버디
퀀시
소다
라이
킬로
백학
릴리
마키마
파워
iDoll 썬
iDoll 오션
iDoll 플라워
솔져 E.G.
솔져 F.A.
솔져 O.W.
프로덕트 08
프로덕트 12
프로덕트 23
N102
네베
네온
델타
라피
람
릴리
미사토
미카
미하라
벨로타
아니스
앵커
에테르
쿠루미
클레어
파스칼
히메노