# 아스카 : WILLE 동작 시나리오

> 모드: 보강

## 기본 정보
- 버스트 단계: B3
- 본인 쿨다운: 40s (섬멸 태세 스킬 쿨)
- 무기 유형: MG (300발, 재장전 2.33s, 일반공격 5.47% / 코어 200%, 풍압 코드, 화력형)
- 작성 사이클 수: 2 (본인 버스트 발동 사이클 2회분)
- 핵심 컨셉: **섬멸 태세(B3) → 9초 동안 일반공격이 적에게 [안티 AT 필드] 디버프 스택 누적(10발당 1, 30 cap) → 섬멸 태세 종료(`event:state_end:섬멸 태세`) 시 [섬멸]이 스택 수에 비례한 폭발딜(6.62% × N) → 스택 제거 + [긴급 수복] 유틸**

## 효과 요약 (효과 단위, parsed_skills.json 기준, Lv10 값)

| ID (name) | source | 트리거 (timing + condition) | stat / 값 | 대상 | 지속 | 비고 |
|---|---|---|---|---|---|---|
| 안티 AT 필드 강타 | 스킬1 | `hit_count:50` | `bonus_damage` 471.86 | `target` | 1회성 | 상시. 일반공격 50회 명중 누적 카운터(사이클·재장전 넘어 지속) |
| 섬멸 태세 추가 효과 | 스킬1 | `hit_count:10` + `self_state:섬멸 태세` | `damage` 15.62 (최종ATK) | `enemies_nearest:2` | 1회성 | 섬멸 태세 9초간만. "10발 당" → 10회 명중마다 |
| 안티 AT 필드 | 스킬1 | `hit_count:10` + `self_state:섬멸 태세` | `received_dmg_pct` +0.83 (harmful, max_stack 30) | `enemies_nearest:2` | 30s | 적 디버프. 스택 누적, 9초보다 빨리 30 cap 도달 예상 |
| 긴급 수복 | 스킬2 | `full_burst_start` + `self_state:섬멸 태세` | `atk_dmg_pct` +30.97 | `self` | 10s | 풀버스트 진입 시 1회 평가 |
| 긴급 수복 2 | 스킬2 | `event:state_end:섬멸 태세` | **`mg_warmup_speed_pct` -100** (harmful, 신규 stat ❌) | `self` | 3s | MG 예열 진행 100% ▼ |
| 긴급 수복 3 | 스킬2 | `event:state_end:섬멸 태세` | `force_reload` (instant, 미구현 ❌) | `self` | — | 탄환 100% 제거 |
| 긴급 수복 4 | 스킬2 | `event:state_end:섬멸 태세` | `heal_hp_pct` 3.77 (시전자 최대체력 비례) | `self` | 3s, tick 1s | 1초 간격 3회 회복 |
| 긴급 수복 5 | 스킬2 | `event:state_end:섬멸 태세` | `reload_speed_pct` +60 (fixed) | `self` | 1발 | 재장전 속도 고정 1회 |
| 섬멸 태세 | 스킬3 | `burst_cast` | `normal_atk_dmg_pct` -40 (harmful, fixed) | `self` | 9s | **상태 마커 buff** — self_state:섬멸 태세 게이트의 근거. 일반공격 ×0.6 |
| 섬멸 태세 2 | 스킬3 | `burst_cast` | `ammo_charge_pct` 21 (instant) | `self` | — | 발동 시 1회 |
| 섬멸 태세 3 | 스킬3 | `burst_cast` | `atk_caster_based_pct` +46.8 | `self` | 9s | **본인 한정 캐스터 ATK** — 모든 본인 딜 환산에 반영 |
| 섬멸 태세 4 | 스킬3 | `burst_cast` | `atk_dmg_pct` +36 | `self` | 9s | |
| 섬멸 | 스킬3 | `event:state_end:섬멸 태세` | `bonus_damage` 6.62, **scaling: stack_count, scaling_ref: "안티 AT 필드"** | `enemies_with_buff:안티 AT 필드` | 1회성 | 스택 수만큼 6.62%씩 히트 (총 6.62%×N) |
| 섬멸 2 | 스킬3 | `event:state_end:섬멸 태세` | `remove_named_buff`, target_effect: "안티 AT 필드" | `enemies_with_buff:안티 AT 필드` | — | 섬멸 이후 스택 제거 |

## 검증 스쿼드

- **표준 (B3, 쿨 40s)**: `["리틀 머메이드", "크라운", "아스카 : WILLE", "test_B3"]` — 기본 사이클 동작 검증
  - 조건부 효과 스캔 (parsed_skills 기준): `self_state:섬멸 태세` 외 다른 condition·target 의존 없음. `no_burstN_ally`·`back_row`·`allies_weapon/class/code`·트리거 캐릭터 의존 모두 부재 → **표준 1개로 충분**.
  - 단일 보스 환경: `enemies_nearest:2`, `enemies_with_buff:안티 AT 필드` 모두 보스 1기로 수렴.

## 사이클 타임라인

> 본인 쿨 40s, 풀버스트 사이클 ~12.5s → 약 3사이클당 1회 본인 발동(중간 사이클은 test_B3가 B3 슬롯 채움). 작성 사이클 수 = 2 → **본인 발동 사이클 2회분**(예: 발동#1, 발동#2). 사이에 끼는 미발동 사이클은 일반공격만 발생하며 별도 표로 정리.

### 본인 버스트 발동 사이클 (1회분, 발동 시각 = T)

| t (예상) | 이벤트 | 발동 효과 (parsed_skills name) | 대상/지속/스택 | 비고 |
|---|---|---|---|---|
| 사이클 내내 | 일반공격 50회 명중 누적 | 안티 AT 필드 강타 (`bonus_damage` 471.86) | target / 1회성 | 50회 도달 시 1회 발사. 카운터는 사이클·재장전 넘어 지속 |
| T (B3 발동, `burst_cast`) | 섬멸 태세 진입 | 섬멸 태세 (`normal_atk_dmg_pct` -40, 9s) **상태 마커** | self / 9s | 이 buff의 활성 == `self_state:섬멸 태세` 판정의 근거 |
| T | 〃 | 섬멸 태세 2 (`ammo_charge_pct` 21) | self / instant | 최대 장탄 21% 즉시 충전 |
| T | 〃 | 섬멸 태세 3 (`atk_caster_based_pct` +46.8, 9s) | self / 9s | 캐스터 ATK 합산. 일반딜·섬멸딜 모두 환산에 반영 |
| T | 〃 | 섬멸 태세 4 (`atk_dmg_pct` +36, 9s) | self / 9s | |
| T+0.1 | 풀버스트 진입 (`full_burst_start`) | 긴급 수복 (`atk_dmg_pct` +30.97, 10s) **`self_state:섬멸 태세` 통과** | self / 10s | 진입 시 1회 평가. 섬멸 태세 활성이므로 조건 충족 |
| T+0~T+9 | 일반공격 (×0.6 normal_atk_dmg_pct로 인해) | 섬멸 태세 추가 효과 (`damage` 15.62) — 10발마다 | enemies_nearest:2 / 1회성 | `hit_count:10`이 9초 동안 다수 발동 |
| 〃 | 〃 | 안티 AT 필드 (`received_dmg_pct` +0.83) — 10발마다 스택 부여 | 적 / 누적, max 30, 30s | 9초보다 빨리 30 cap 도달 예상. 만료는 마지막 부여 후 30s |
| T+9 | 섬멸 태세 만료 (`event:state_end:섬멸 태세`) | 섬멸 (`bonus_damage` 6.62, scaling: stack_count×안티 AT 필드) | enemies_with_buff:안티 AT 필드 / 1회성 | 스택 N개일 때 6.62%×N 합산 (timeline은 N번 hit으로 분기) |
| T+9 | 〃 | 섬멸 2 (`remove_named_buff`: "안티 AT 필드") | 적 / — | 섬멸 직후 스택 전량 제거 |
| T+9 | 〃 | 긴급 수복 2 (`mg_warmup_speed_pct` -100, 3s) | self / 3s | MG 예열 진행 0 |
| T+9 | 〃 | 긴급 수복 3 (`force_reload`) | self / — | 현재 탄환 0 → 강제 재장전 |
| T+9 | 〃 | 긴급 수복 4 (`heal_hp_pct` 3.77, tick 1s, 3s) | self / 3s | t=T+9, T+10, T+11 회복 (1초 간격) |
| T+9 | 〃 | 긴급 수복 5 (`reload_speed_pct` +60, 1발 유지) | self / 1발 | 재장전 1회분 가속 |
| T+9~T+12 | 〃 (3초 유지) | 긴급 수복 2~4 활성 (5는 1발 후 만료) | | 예열 0 + 재장전 중 |
| ~T+10 | 풀버스트 종료 (`full_burst_end`) | 섬멸 태세 1·3·4 이미 만료(9s) / 긴급 수복(10s) 잔존 ~T+10.1까지 | | 버프 만료 타이밍 어긋남 |

### 본인 버스트 미발동 사이클 (사이에 끼는 사이클)

| t | 이벤트 | 발동 효과 | 비고 |
|---|---|---|---|
| 사이클 내내 | 일반공격만 | 안티 AT 필드 강타(50회 명중 도달 시) **만** 발동 | `self_state:섬멸 태세` 거짓 → 섬멸 태세 추가 효과 / 안티 AT 필드 / 긴급 수복(블록1) 모두 발동 안 함 |
| 풀버스트 진입 | 긴급 수복 조건 평가 | `self_state:섬멸 태세` 거짓 → 미발동 | |
| 풀버스트 종료 | 본인 버스트 미사용 | 섬멸 태세 / 섬멸 / 긴급 수복 블록2 전부 미발동 | |

## 스킬 간 상호작용

- **[섬멸]의 대미지는 [안티 AT 필드] 스택 수에 직접 비례 (스택 생성 → 소비 패턴)**
  - 경로: 섬멸 태세 발동 → `self_state:섬멸 태세` 활성 → `hit_count:10` 트리거가 안티 AT 필드 스택을 누적(`received_dmg_pct` +0.83, max 30) → 9초 후 섬멸 태세 만료 → `event:state_end:섬멸 태세`로 섬멸(`scaling: stack_count, scaling_ref: "안티 AT 필드"`) 발사 → 같은 timing의 섬멸 2(`remove_named_buff`)가 스택 제거
  - 시점: 스택 누적 종료 == 섬멸 발사 == 스택 제거 모두 T+9에 같은 이벤트 dispatch에서 처리. **dispatch 순서**: parsed_skills 배열 순서에 따라 섬멸(인덱스 12) → 섬멸 2(인덱스 13). 섬멸이 스택 수 읽은 후 섬멸 2가 제거 → 정상.
  - 위험: 섬멸 2가 섬멸보다 먼저 처리되거나 스택이 다른 경로로 미리 사라지면 폭발딜이 0.

- **모든 조건부 효과는 [섬멸 태세] 상태 마커 buff(첫 번째 효과)에 게이트됨**
  - 경로: 섬멸 태세(스킬3 effect1, `normal_atk_dmg_pct` -40, 9s) 활성 → `self_state:섬멸 태세` 통과 → 안티 AT 필드 스택 누적 / 섬멸 태세 추가 효과 / 긴급 수복(블록1) 발동
  - 시점: `_active`에 name=="섬멸 태세" buff 존재 여부로 판정. 9초 만료 시 동시에 게이트 닫힘.
  - 위험: 섬멸 태세 buff의 duration이 9가 아닌 다른 값이거나 일찍 만료되면 후속 효과 전부 무력화.

- **본인 한정 캐스터 ATK (target: self + atk_caster_based_pct)**
  - 경로: 섬멸 태세 3(`atk_caster_based_pct` +46.8, target self) → 본인 ATK에만 합산 → 일반공격·섬멸 태세 추가 효과·섬멸 모두 이 ATK 기준으로 계산
  - 시점: 9초간 유지. 풀버스트 중에만 적용.
  - 위험: target이 self 외(예: all_allies)로 잘못 적용되면 다른 아군에도 캐스터 ATK가 더해져 과대평가.

- **풀버스트 진입 1회 평가 (full_burst_start + self_state)**
  - 경로: 긴급 수복(블록1)이 `full_burst_start` + `self_state:섬멸 태세` 결합 → 풀버스트 진입 순간 1회 조건 평가
  - 시점: T+0.1 (B3 발동 → 0.1초 switching → full_burst_start). 섬멸 태세는 T에 발동되어 활성이므로 조건 충족.
  - 위험: switching 0.1초 동안 섬멸 태세 buff가 활성 처리 안 되어 있으면 미발동. (버스트 발동과 동시에 _activate 처리되므로 정상이어야 함.)

## 유저 확인 사항 (초안 모드 답변 보존)

- **Q1**: 안티 AT 필드 강타(50회 명중) 카운터는 사이클·재장전을 넘어 누적되는가?
  - A: **누적된다.** GAMEPLAY 일반 규칙 후보(기존 캐릭터 구현 검증은 추후 별도). 보강 모드에서는 `hit_count:50` 그대로 사용.
- **Q2**: 섬멸 태세 효과2 "탄환 충전 21%"는 즉시 1회성?
  - A: **맞음.** `ammo_charge_pct` instant로 구현됨 (✅).
- **Q3**: "일반 공격 대미지 40% 배율 ▼" 의미?
  - A: **×0.6 (40% 감소).** `normal_atk_dmg_pct` -40 (fixed) 9s. DealForm ① 계수에 가산되어 자동 처리.
- **Q4**: "10발 당" 발동?
  - A: 10발 발사마다 1스택. 9초 동안 30 cap 도달 예상. `hit_count:10`으로 매핑(단일 보스·고명중 가정).
- **Q5**: 섬멸 6.62%가 스택당?
  - A: **6.62% × 스택수.** `scaling: stack_count, scaling_ref: "안티 AT 필드"` (미하라 사슬 당기기 선례와 동일 구조).
- **Q6**: 긴급 수복 블록2 4효과 처리?
  - A: **셋 모두(예열▼/탄환제거/회복) 구현 필요.** 4번째(재장전 속도 +60)는 기존 `reload_speed_pct`로 근사. "고정" 어휘는 단순 합산으로 처리(다른 reload 버프 무력화 의미라면 Phase C 추가 검토).
- **Q7**: 본인 발동 주기?
  - A: 본인 쿨 40s, 사이클 ~12.5s → 약 3사이클당 1회 발동 (simulate 결과로 확정). 작성 사이클 수 = 2는 본인 발동 사이클 2회분 의미.

## impl/bug-fix용 검증 체크리스트 (보강)

- [ ] **본인 발동 사이클**: `섬멸 태세`(스킬3 effect1) 9s 활성 — `_active`에 name=="섬멸 태세" 존재 확인
- [ ] **풀버스트 진입(T+0.1)**: `긴급 수복`(atk_dmg_pct +30.97, 10s) 활성. 조건 `self_state:섬멸 태세` 통과
- [ ] **섬멸 태세 중 hit_count:10 발동**: `섬멸 태세 추가 효과`(damage 15.62, enemies_nearest:2) 9초간 다수회 발동
- [ ] **안티 AT 필드 스택 누적**: `_active` 내 name=="안티 AT 필드"의 `stack`이 T+9 시점에 ≤30, cap에 도달했는지(MG fire rate 기준 ~3~4초 내 30 cap 도달 예상)
- [ ] **섬멸 발동(T+9)**: `섬멸`이 `event:state_end:섬멸 태세`로 1회 발사. hit_count = 안티 AT 필드 스택 수
- [ ] **섬멸 직후 스택 제거**: `섬멸 2`(`remove_named_buff` target_effect="안티 AT 필드") 처리 후 `_active`에서 해당 디버프 사라짐
- [ ] **자버프**: `섬멸 태세 3`(atk_caster_based_pct +46.8) 본인에게만, 다른 아군 ATK에 영향 없음
- [ ] **일반공격 감쇠**: `섬멸 태세`(normal_atk_dmg_pct -40)로 9s간 일반공격 계수 ×0.6 적용 (DealForm ①)
- [ ] **탄환 충전**: `섬멸 태세 2`(ammo_charge_pct 21) T 시점 1회, 현재 탄환 +최대장탄×21%
- [ ] **긴급 수복 블록2 (T+9~T+12)**:
  - [ ] `mg_warmup_speed_pct` -100 (신규 stat 구현 필요) — 3초간 MG 예열 진행 0
  - [ ] `force_reload` (미구현 stat 구현 필요) — 현재 탄환 0 + 강제 재장전 트리거
  - [ ] `heal_hp_pct` 3.77 — t=T+9, T+10, T+11 (1초 간격 3회)
  - [ ] `reload_speed_pct` +60 — 1발 재장전 동안만
- [ ] **미발동 사이클**: 본인 버스트 미사용 시 `self_state:섬멸 태세` 거짓 → `안티 AT 필드`·`섬멸 태세 추가 효과`·`긴급 수복`(블록1)·`섬멸`·`긴급 수복 2~5` 전부 미발동
- [ ] **사이클 간격**: 본인 섬멸 태세 발동은 ~40s 간격(약 3 풀버스트 사이클당 1회). simulate에서 본인 burst_cast 이벤트가 180s 내 4~5회
- [ ] **상시 발동**: `안티 AT 필드 강타`(hit_count:50, bonus_damage 471.86)는 섬멸 태세 무관 상시. 카운터는 사이클·재장전 넘어 누적
- [ ] **이름 매칭**: `scaling_ref: "안티 AT 필드"`·`target_effect: "안티 AT 필드"`·`enemies_with_buff:안티 AT 필드` 모두 디버프(buff type, name "안티 AT 필드")를 정확히 참조. **2026-08-04 이름 재정리**: clause1 damage는 `안티 AT 필드 강타`로 개명해 이름 충돌 자체를 제거했다(레이 (가칭)이 `target_state:안티 AT 필드`·`debuff_stack_add`로 이 디버프를 참조하기 때문)
- [ ] **이벤트 순서 (T+9)**: parsed_skills 배열 순서에 따라 섬멸 → 섬멸 2 dispatch. 섬멸이 스택 읽은 후 섬멸 2가 제거하는지 확인
