# CDN `shot_detail` 발사 데이터 (정본)

> blablalink CDN `roledata/{rid}-v2-ko.json`의 `shot_detail` 레코드가 무엇을 담고 있고,
> 그중 무엇을 계산기가 쓰는지를 적는다. **발사 딜레이·엄폐·톡톡이 가부의 정본이 이 문서다.**
> 수집 절차는 `.agent/skills/char-scrape/SCRAPER.md`, 값의 검증 상태는 `docs/DATA_VERIFY.md`.
> 조사·작성 2026-08-27 (전 캐릭터 199명 전수).

## 왜 이 문서가 생겼나

`data/weapon_delays.json`에는 인게임 눈대중으로 찍은 딜레이 예외가 쌓여 있었고 전부
미검증(⬜)이었다. 199명 전수 조사에서 **그 예외 목록이 CDN 필드와 그대로 겹친다는 것**이
확인됐다 — 우리가 손으로 발견한 규칙이 이미 데이터에 있었다. 그래서 딜레이를 하드코딩에서
CDN 유도로 옮겼고, 그 유도식의 근거가 여기다.

## 단위 규약

`shot_detail`은 두 가지 스케일을 섞어 쓴다. 필드 이름으로는 구별되지 않으니 이 표를 본다.

| 스케일 | 뜻 | 해당 필드 |
|---|---|---|
| **1/100초** | 100 = 1.00초 | `reload_time`, `charge_time`, `maintain_fire_stance`, `rate_of_fire_reset_time` |
| **1/100 %** | 20000 = 200.00% | `core_damage_rate`, `full_charge_damage`, `burst_energy_pershot` 계열 |
| **rpm** | 분당 발수 | `rate_of_fire`, `end_rate_of_fire`, `rate_of_fire_change_pershot` |
| **px** | 화면 픽셀 | `*_accuracy_circle_scale`, `accuracy_change_*`, `spot_*` |

## 우리가 쓰는 필드

### `input_type` — 유저가 잡았을 때 발사가 어떻게 결정되는가

**오토가 디폴트다.** 5명 전원이 가만히 둬도 알아서 싸우고, `input_type`은 그중
**유저가 조작할 때 무엇을 할 수 있는지**를 가른다 (`docs/CONTROL.md` §조작 원시타입).

| 값 | 인원 | 오토일 때 | 유저가 잡았을 때 |
|---|---|---|---|
| `DOWN` | 121 | 누르는 동안 연사 | 비차지 무기(AR/SMG/SG/MG) 전부 + RL 파스칼. 차지가 없어 끊기·잡기가 무의미 |
| `UP` | 72 | 알아서 풀차지로 쏜다 | **떼는 순간 발사** — 짧게 끊으면 톡톡이, 길게 잡으면 홀드 |
| `DOWN_Charge` | 6 | 알아서 풀차지로 쏜다 | **차지가 차는 순간 자동 발사** — 짧게도 길게도 못 잡는다. 아니스 : 스타, 네온 : 비전 아이, 베스티 : 택티컬 업, 라플라스 : 얼티밋 히어로, 리버렐리오, 신데렐라 |

`UP`을 "떼야만 발사된다"로 읽으면 안 된다 — 오토는 떼는 사람이 없어도 쏜다.
`UP`이 뜻하는 건 **떼는 시점을 유저가 고를 수 있다**는 것이고, 그게 톡톡이와 홀드의 전제다.

### `maintain_fire_stance` — 발사 후 사격 자세 유지 (1/100초)

**199명 중 3명만 0이 아니다.**

| 캐릭터 | 값 | 초 |
|---|---|---|
| 홍련 : 흑영 | 23 | 0.23 |
| 레이븐 | 83 | 0.83 |
| A2 (미등록) | 84 | 0.84 |

### `uptype_fire_timing` — 미해석

`UP` 중 위와 **같은 3명만** 비영이다: 홍련 : 흑영 1 / 레이븐 3200 / A2 4800.
숫자의 의미는 모른다(1과 3200이 같은 축일 리가 없다). 지금은 **비영 여부만** 쓴다 —
그 3명이 곧 "UP인데 풀차지 전용"인 집합이기 때문이다. 값 자체는 계산기까지 흘리지 않고
`parse_nikke.py`가 `full_charge_only` 불리언으로 봉한다.

### `rate_of_fire` — 발사 주기 하한

차지 무기에서 `UP`은 **전원 60rpm**이라 의미 없는 센티넬이고, `DOWN_Charge`만 실값을 갖는다.

| 캐릭터 | rpm | 초당 | 주기 하한 |
|---|---|---|---|
| 아니스 : 스타 | 120 | 2.0 | 0.500s |
| 신데렐라 | 180 | 3.0 | 0.333s |
| 베스티 : 택티컬 업 | 180 | 3.0 | 0.333s |
| 리버렐리오 | 200 | 3.33 | 0.300s |
| 네온 : 비전 아이 | 300 | 5.0 | 0.200s |
| 라플라스 : 얼티밋 히어로 | 300 | 5.0 | 0.200s |

비차지 무기에서의 해석(AR 720 → 12/s 등)은 `docs/DATA_VERIFY.md` §발사 속도.

### 탄착군 (`*_accuracy_circle_scale`, `accuracy_change_pershot`)

**2026-08-27부터 계산기 정본이다.** 종전에는 `weapon_mechanics.json`에 무기군별로 손으로
적은 커뮤니티 실험값을 썼고, 지금은 CDN 값이 캐릭터별로 `parsed_nikke.json`을 거쳐 들어온다.

무기군별로 값이 통일돼 있다. `start` → `end`로 지속 사격 중 수렴하며, 발당 변화량이
`accuracy_change_pershot`다.

| 무기 | start | end | 발당 | 예열 완료 | 종전 손 관리값 |
|---|---|---|---|---|---|
| AR | 75 | 75 | 0 | — | 76 |
| SMG | 110 | 110 | 0 | — | 110 (**일치했다**) |
| SG | 250 | 250 | 0 | — | 240 |
| MG | 250 | **10** | 7 | 34.3발 | 10 고정 가정 (예열 미모델) |
| RL · SR | 10 | 10 | 0 | — | 10 고정 가정 (**적중했다**) |

- 예외 1명 — **프리바티 : 언카인드 메이드**(SG) 250 → 75, 발당 18(9.7발).
  SG인데 지속 사격으로 탄착군이 좁아지는 유일한 케이스다. **미등록**이라 아직 안 돈다
- `auto_*` 접두 4개(`auto_start_accuracy_circle_scale` 등)는 비-auto판과 값이 **완전히 같다**.
  오토 조준용 별도 값으로 보이나 현재는 구별할 근거가 없다
- **`accuracy_change_speed`(MG 150px/s)는 쓰지 않는다** — 예열을 발수 선형으로 잡았기
  때문이다. 두 수치는 21.4발/s에서 만나는데 MG 연사는 1/s → 70/s로 오르므로 실제로는
  `speed`가 상한으로 걸리는 꺾인 곡선일 수도 있다. 미해석으로 남긴다

#### 명중률은 CDN에 없다

계산기가 쓰는 직경은 이렇다:

```
D(px) = spread(예열 보간) × (1 − 0.00908 × 명중%)
```

`0.00908`(명중 1%당 직경 −0.908%)은 **CDN에 없다.** 커뮤니티 실험값의 무기별 slope를
base로 나누면 AR 0.9079% · SMG 0.9091% · SG 0.9083%로 사실상 같아, 무기별 slope 3개가
아니라 곱셈 법칙 하나로 읽은 것이다. **추론이지 확인된 사실이 아니다** —
`docs/DATA_VERIFY.md` §명중률/탄착군에 ⬜로 남아 있다.

구현은 `calculator/timeline.py` `CharState._current_spread()`, 상수는
`weapon_mechanics.json` `accuracy._slope_ratio`. 예열 진행도는 연사 예열과 **같은
`warmup_shots` 카운터**를 쓰되 분모가 달라 서로 다른 발수에서 끝난다(MG 34.3 vs 41.4).
분석·미해결은 `docs/mechanics/명중률 탄착군.md`.

### 버스트 게이지 (`burst_energy_pershot` 계열)

| 필드 | 뜻 | 우리가 쓰는가 |
|---|---|---|
| `burst_energy_pershot` | 히트당 원본 게이지 (1/100%) | ✅ `burst_energy_raw` = 값/10000. 아군 버충속 시전자가 일반 공격을 명중시키기 전 참조값 |
| `target_burst_energy_pershot` | 위의 **정확히 2배**(199/199) | ✅ `burst_energy` = 값/10000. 실제 공격 게이지와 시전자 첫 명중 후 참조값 |
| `full_charge_burst_energy` | 풀차지 배율. `/100 == full_charge_mult` **78/78 일치** | 값은 안 내린다 — `full_charge_mult`를 재사용하고 어긋나면 `[WARN]` |

2026-08-27 초판은 `target_`을 "대보스 배수로 **보인다**"고 적었다 — **실측으로
확정됐다.** 다만 원본 필드도 아군이 받는 「버스트 충전 속도」의 시전자 기준값으로 쓰인다.
`full_charge_burst_energy`를 "+2.5% 가산"으로 읽은 것은 틀렸고, 가산이 아니라
배율이다(1/100% 규약대로 25000 = 250.00%). 유도·실측은
`docs/mechanics/버스트 게이지.md`.

## 유도식 셋

```
full_charge_only   = input_type == "DOWN_Charge" or uptype_fire_timing != 0   (톡톡이 불가)
홀드 불가          = input_type == "DOWN_Charge"
post_fire_delay    = 0                                        (DOWN_Charge)
                   = 0.22 + max(0.16, maintain_fire_stance/100)   (UP)
cover_during_delay = input_type == "UP" and maintain_fire_stance == 0
발사 주기          = max(차지 시간 + post_fire_delay, 60/rate_of_fire)   (DOWN_Charge만 하한 적용)
```

**톡톡이 가부와 홀드 가부는 다른 축이다.** 홍련 : 흑영·레이븐은 풀차지 전용이라 끊어쏘기는
안 되지만 **홀딩은 된다**(유저 확인). 그래서 세 부류가 나온다:

| 부류 | 인원 | 톡톡이 | 홀드 | 쓸 수 있는 컨트롤 |
|---|---|---|---|---|
| `UP`, `uptype_fire_timing == 0` | 69 | ✅ | ✅ | 전부 |
| `UP`, `uptype_fire_timing != 0` | 3 (홍련 : 흑영 · 레이븐 · A2) | ❌ | ✅ | 홀드 · 엄폐 |
| `DOWN_Charge` | 6 | ❌ | ❌ | **엄폐뿐** |

게이트는 둘 다 `CharState.__init__`에서 **조립 시점에 즉시 실패**한다 — 조용히 무시하면
게임에 없는 조작으로 딜이 나온다.

`0.22`(사격 전) · `0.16`(사격 후)의 2분할은 이 문서가 아니라 `docs/CONTROL.md` §톡톡이가
정본이다. 구현은 `calculator/timeline.py` `CharState.__init__`의 차지 분기이며 상수는
`_TAP_MIN_HOLD` · `_TAP_CUTTABLE_DELAY`다.

### 근거 — 유저 인게임 확인 (2026-08-27)

- **`DOWN_Charge` 5명**(네온 : 비전 아이 · 라플라스 : 얼티밋 히어로 · 신데렐라 ·
  아니스 : 스타 · 리버렐리오) — 풀차지가 아니면 공격하지 않는다. **공격 사이에 잠깐의
  엄폐 자세를 취하지 않아 재장전 속도 100%를 넘겨도 탄이 충전되지 않는다.** 차지 중
  엄폐하면 공격이 취소된다
- **`mfs > 0` 2명**(홍련 : 흑영 · 레이븐) — 역시 풀차지 전용. **차지 중에는 엄폐가 되지만
  풀차지 이후 공격 중에는 엄폐가 안 된다.** 레이븐이 "공격 중 엄폐가 안 되는 니케"로
  알려진 것이 이 필드다
- 엄폐컨(유저 조작)은 두 부류 모두 언제나 가능하다 — 막히는 건 딜레이 중 **자동으로**
  엄폐 자세를 거치는 동작뿐이다

### 검산

| 캐릭터 | input | mfs | 유도 post_fire_delay | 대조 |
|---|---|---|---|---|
| 일반 RL/SR 69명 | UP | 0 | 0.38 | 종전 무기군 기본값과 같다 |
| 홍련 : 흑영 | UP | 23 | 0.45 | 종전 눈대중 실측 0.43 (−0.02) |
| 레이븐 | UP | 83 | 1.05 | 주기 1.0+1.05 = **2.05초** ≈ 유저 실측 "2초에 가깝다" |
| A2 (미등록) | UP | 84 | 1.06 | — |
| `DOWN_Charge` 6명 | DOWN_Charge | 0 | 0 | 종전 예외 4명과 같다 |

**신데렐라의 종전 하드코딩 `post_fire_delay: 0.33`은 값이 아니라 구멍 막이였다.** 그는
`무결한 유리 2`로 차지가 0초가 되는데 주기 하한이 없어 매 프레임 발사가 되기 때문이다.
0.33은 CDN `rate_of_fire` 180rpm(= 1/3초)을 딜레이 슬롯에 잘못 넣은 값이고, 지금은
제자리인 주기 하한 0.333초로 들어갔다. 라플라스 : 얼티밋 히어로(`예열` charge_speed)에도
같은 구멍이 있었다.

### `cover_during_delay` 전 캐릭터 분류

RL/SR 78명 중 **`false` 9명**, 나머지 69명 `true`.

| 판정 | 캐릭터 |
|---|---|
| `false` — `DOWN_Charge` | 아니스 : 스타, 네온 : 비전 아이, 라플라스 : 얼티밋 히어로, 리버렐리오, 신데렐라, 베스티 : 택티컬 업(미등록) |
| `false` — `mfs > 0` | 레이븐, 홍련 : 흑영, A2(미등록) |
| `true` | 나머지 69명 |

이 유도는 관측 22건에 전부 들어맞는다 — 종전에 손으로 `true`를 찍었던 15명(전원 `UP`+`mfs=0`)과
유저가 확인해 준 `false` 7명.

## 차지 무기(RL·SR) 한 장 정리

차지 무기 하나가 도는 데 관여하는 CDN 값을 전부 모은다. 위 절들이 필드별 설명이라면
여기는 **"한 발이 나가기까지 어느 값이 어디서 끼어드는가"**다.

### 차지형 판정

`shot_detail.description_localkey`에 **`{charge_time}` 플레이스홀더가 있으면 차지형**이다
(`cdn_fetch.py` `adapt()`의 `조작 타입`). 199명 전수에서 `charge_time > 0`과 **불일치 0건**이라
어느 쪽으로 판정해도 같다. 차지형은 **RL 41명 + SR 37명 = 78명**이고, RL 42명 중
**파스칼만 비차지**(`charge_time` 0, `input_type: DOWN`)다.

### 한 발의 생애

```
[차지 시작] ──charge_time── [풀차지] ──발사── [post_fire_delay] ──▶ 다음 차지
                                                       │
                     발사 주기 = max(charge_time + post_fire_delay, 60/rate_of_fire)
                                                        └ 하한은 DOWN_Charge에만 적용
```

| 구간 | CDN 필드 | 우리 필드 | 비고 |
|---|---|---|---|
| 차지 시간 | `charge_time` (1/100초) | `charge_time` | **직접 안 읽는다** — 아래 텍스트 경유 참조 |
| 발사 여부 | `input_type` | `input_type` | `UP` = 손 떼서 / `DOWN_Charge` = 자동 |
| 끊어쏘기 가부 | `input_type` + `uptype_fire_timing` | `full_charge_only` | 풀차지 전용 9명은 톡톡이 불가 |
| 홀딩 가부 | `input_type` | (직접 판정) | `DOWN_Charge` 6명만 불가. 홍련 : 흑영·레이븐은 **가능** |
| 발사 후 딜레이 | `maintain_fire_stance` (1/100초) | `post_fire_delay` | `0.22 + max(0.16, mfs/100)`. `DOWN_Charge`는 0 |
| 딜레이 중 엄폐 | `input_type` + `maintain_fire_stance` | `cover_during_delay` | `UP` and `mfs == 0` |
| 주기 하한 | `rate_of_fire` (rpm) | `_min_fire_cycle` | `DOWN_Charge`만. `UP`은 전원 60rpm 센티넬 |
| 풀차지 배율 | `full_charge_damage` (1/100%) | `full_charge_mult` | 텍스트 경유 |
| 풀차지 게이지 | `full_charge_burst_energy` (1/100%) | — | 수집만 |
| 장탄·재장전 | `max_ammo` · `reload_time` (1/100초) | `max_ammo` · `reload_time` | 무기군 공통 |

### 텍스트를 경유하는 값들 (주의)

`charge_time` · `full_charge_damage` · `core_damage_rate` · `damage`는 **필드에서 직접
읽지 않는다.** `cdn_fetch.py` `render_weapon_skill()`이 설명문의 `{charge_time}` 같은
플레이스홀더에 `js_number()`(= 값/100)로 치환해 `무기스킬` 문자열을 만들고,
`parse_nikke.py` `parse_weapon_skill()`이 그 문장을 정규식으로 **되읽는다**.

```
CDN charge_time: 100  →  "차지 시간: 1초"  →  parsed_nikke charge_time: 1.0
```

프론트엔드가 화면에 찍는 문장과 같은 경로라 표기와 어긋날 일이 없다는 게 장점이고,
**설명문 문구가 바뀌면 정규식이 깨진다**는 게 대가다(`parse_weapon_skill`이 `[WARN]`을 낸다).
반대로 `input_type` · `maintain_fire_stance` · `rate_of_fire`는 설명문에 안 나오므로
필드에서 직접 읽는다 — 이번에 추가한 값들이 전부 그쪽이다.

### `charge_time` 값 분포

**표준은 1.00초**(RL 35명 · SR 34명)이고 벗어나는 건 9명뿐이다.

| 무기 | charge_time | input_type | 풀차지 배율 | 캐릭터 |
|---|---|---|---|---|
| RL | **0.30s** | UP | 150% | 홍련 : 흑영 |
| RL | 1.00s | UP / DOWN_Charge | 대개 250% | 표준 35명 |
| RL | **1.50s** | UP | 350% | N102, 벨로타, 얀, 유니 (전원 미등록) |
| RL | **2.00s** | DOWN_Charge | 200% | 베스티 : 택티컬 업 (미등록) |
| SR | 1.00s | UP | 대개 250% | 표준 34명 |
| SR | **1.20s** | UP | 250% | 스노우 화이트 : 헤비암즈 |
| SR | **1.50s** | UP | 350% | 앨리스 |
| SR | **1.50s** | DOWN_Charge | 250% | 리버렐리오 |

차지가 길수록 풀차지 배율이 높은 경향이 뚜렷하다(1.5초 = 350%). 홍련 : 흑영은 반대쪽
극단으로 0.3초에 150%다.

### 차지 시간을 줄이는 것은 CDN 밖이다

`charge_speed_pct`(차지 속도 ▲) · `charge_time_fixed` · `charge_time_flat`은 전부 **스킬·장비
효과**이지 `shot_detail`에 없다. 계산기의 `_effective_charge_time()`이 `charge_time_base`에
그것들을 얹어 매 발 계산한다. 그래서 **차지가 0초가 되는 상황이 실제로 나오고**, 그때
무한 연사를 막는 것이 `rate_of_fire` 하한이다 — 신데렐라 `무결한 유리 2`(charge_speed +100),
라플라스 : 얼티밋 히어로 `예열`, 앨리스 버스트 + 차지속도 오버로드 2줄이 그 사례다.

> `UP` 무기는 하한이 없다(60rpm 센티넬을 무시하므로). 앨리스가 차지 0초에서
> 톡톡이로 초당 4발을 내는 공략이 성립하는 것이 그 때문이고, 이는 의도된 것이다 —
> 상세는 `docs/CONTROL.md` §톡톡이.

### `reload_bullet` — 클립 무기 판정 (1/100 %)

재장전 **1회**가 채우는 탄창 비율이다. `10000` = 통짜 재장전, `3300` = 1/3.
`parse_nikke.py`가 `clip_count = round(10000 / reload_bullet)`으로 접어 내리고,
`timeline.py`가 `clip_count > 1`을 클립 무기로 읽는다(`_clip_gain`이 그 수로 나눈다).

| 값 | 인원 | 뜻 |
|---|---|---|
| 10000 | 184 | 한 번에 탄창 전체 |
| **3300** | 14 | 클립 3회. SG 9명 + RL 5명 — `weapon_mechanics.json` `clip_characters`와 **명단이 정확히 일치**한다 |
| **5000** | 1 | 클립 2회. 그레이브(AR) — 유저 인게임 확인 (2026-08-28) |

손으로 관리하던 `clip_characters` 목록이 이 필드로 대체됐다(2026-08-28). 목록은 CDN 값이
없는 프리뷰 캐릭터용 폴백으로만 남는다. 실측을 마치지 않은 새 값이 나오면 `parse_nikke.py`가
`[WARN]`을 내고 **키를 만들지 않는다** — 종전 동작이 유지되고, 조용히 재장전 시간이
배수로 바뀌는 일이 없다.

> **이 값은 상수가 아니라 버프가 곱해지는 값이다.** 원문 「재장전 비율 N% ▼」가 정확히
> 이 필드를 깎는다 — 그레이브 `방열`이 걸리면 50% → 25%가 되어 재장전이 4회로 늘어난다
> (유저 확인). 계산기는 아직 그 효과를 재장전 **속도**로 잘못 취급한다:
> `docs/DATA_VERIFY.md` §`재장전 비율 N% ▼`.

## 수집만 하는 필드

계산기가 아직 쓰지 않지만 `scraper/nikke_scraped.json`에 원값으로 보관한다.
**`data/parsed_nikke.json`에는 내리지 않는다** — 계산기 입력에 안 쓰는 값을 올리지 않는다.
탄착군은 2026-08-27에 이쪽에서 §우리가 쓰는 필드로 옮겨 갔다(`accuracy_change_speed`만 남는다).
버스트 게이지 3필드도 2026-08-28에 같은 곳으로 옮겨 갔다.

2026-08-28에 아래 넷이 이쪽으로 들어왔다. **의미가 확정되지 않은 값이라 한글 라벨을 붙이지
않고 CDN 원명 그대로** `무기상세`에 담는다 — 이름을 붙이는 순간 해석이 굳는다.

| 필드 | 값 분포 | 왜 담아두나 |
|---|---|---|
| `spot_last_delay` | **20×199 (예외 없음)** | 값이 하나뿐이라 지금은 정보량이 0이다. 재장전 앞 딜레이 후보 |
| `spot_first_delay` | 20×197 · **토브 33** · **네로 13** | 재장전 뒤 딜레이 후보. 예외 둘이 폭발 필드가 전부 0인 `Instant` 무기(AR·SMG)라 "폭발 판정 창"으로는 설명되지 않는다 |
| `bonusrange_min` / `bonusrange_max` | 무기군별 고정 (AR 25\~45 · SR 45\~100 · MG 35\~55 · SMG 15\~35 · SG 0\~25 · **RL 0\~0**) | 거리 보너스 사거리. 계산기에 거리 개념 자체가 없다. **`shot_detail`이 아니라 roledata 최상위 필드**다 |
| `spot_projectile_speed` · `fire_type` | RL만 비-0 (유도 100 · 직선 300/400 · 곡사 1500) | 발사체 비행 속도와 탄도. 발사 시각과 명중 시각 사이 지연을 여기서 유도할 수 있다 |

`spot_*_delay` 둘을 재장전 앞뒤 딜레이로 읽어 엔진에 배선한 적이 있다(PR #7). **되돌아왔다** —
필드 해석이 틀려서가 아니라 재장전 속도 **100% 초과** 구간의 실제 거동이 미실측이기
때문이다(PR #8). 그 실측이 끝나기 전에는 수집만 한다.

## 우리가 안 쓰는 나머지 필드

전수 조사 결과 값이 하나뿐이거나(=정보 없음) 우리 모델에 대응물이 없는 것들이다.

| 필드 | 값 분포 | 비고 |
|---|---|---|
| `spot_explosion_range` / `_radius` / `_radius_object` | 무기별 상이 | 범위 공격 연출. 우리는 단일 보스라 무의미 |
| `penetration` | 0×199 | 관통. 전원 0 — 관통은 스킬로만 붙는다 |
| `center_shot_count` · `multi_aim_range` · `multi_target_count` | 0×199 | 다중 타겟팅. 미사용 |
| `shot_timing` | `Concurrence`×199 | |
| `hurt_function_id_list` · `use_function_id_list` | `[0]`×199 | |
| `reload_start_ammo` | 전원 `max_ammo − 1` | 파생값, 정보 없음 |
| ~~`rate_of_fire_reset_time`~~ | MG 26명만 100(=1초) | **여기 있으면 안 된다 — 이미 쓰고 있다.** `weapon_mechanics.json` MG `cooldown_time` 1.0이 이 값이고 `_cool_warmup()`이 미사격 냉각에 쓴다. 2026-08-27 초판의 오기 |
| `is_targeting` · `prefer_target` · `prefer_target_condition` · `homing_script` | 상이 | 조준 대상 선택 로직. 단일 보스에서 무의미 |
| `auto_accuracy_change_*` · `auto_*_accuracy_circle_scale` | 수동 탄착군과 같은 분포 | 오토 사격용 탄착군. 수동값과 같은 값인지 미확인 |
| `ShakeType` · `ShakeWeight` · `shake_id` · `camera_work` · `zoom_rate` · `aim_prefab` | — | 순수 연출 |
| `attack_type` · `counter_enermy` | `Metal` 146 / `Energy` 34 / `Bio` 19 | **속성(`element_details`)과 1:1이 아니다** — 같은 전격 안에서 Metal 28 · Energy 5 · Bio 5로 갈린다. 별개 축이며 의미 미해석 (2026-08-28 정정: 초판은 "중복"이라 적었다) |
| `core_damage_rate` · `full_charge_damage` · `damage` | 캐릭별 | `무기스킬` 텍스트에서 이미 파싱한다 |

## 재수집 시 주의

- 이 필드들은 `scraper/cdn_fetch.py` `adapt()`가 `무기상세`에 원값 그대로 담고,
  `scraper/parse_nikke.py` `parse_fire_mechanics()`가 계산기가 쓰는 것만 변환해 내린다
- **`input_type`이 없는 캐릭터**(출시 전 `preview_skills.json` 항목)는 `fire_stance_hold`
  키 자체가 없어 `weapon_delays.json` `_defaults_by_weapon_type`(RL/SR 0.38)로 폴백한다
- 새 캐릭터가 `maintain_fire_stance` 비영이나 새로운 `input_type`을 들고 나오면
  이 문서의 인원 수·목록을 함께 갱신한다
