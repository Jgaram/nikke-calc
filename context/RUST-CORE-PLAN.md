# 러스트 코어 작업계획서 (nikke-core)

작성 2026-08-25. 파이썬 계산기(`calculator/`)를 러스트로 옮겨 **같은 결과를 20~50배 빨리** 내고,
같은 코어를 **WASM으로 브라우저**에도 싣는다. 파이썬 엔진은 정본·골든으로 남고, 러스트는
«마지막 자리까지 같은» 두 번째 엔진이다. 원본 저장소(Jgaram)는 건드리지 않는다.

새 세션은 이 문서와 `PERF-ENGINE.md` §7~§9만 읽고 시작하면 된다.

---

## 0. 한 장 요약

| | |
|---|---|
| 목표 | 덱 하나 3초 → 네이티브 0.05~0.15초, WASM 0.1~0.3초. 결과는 파이썬과 **비트 동일** |
| 하지 않는 것 | 파이썬 엔진 삭제·변경, 원본 저장소 푸시, 계산 규칙 개선(«더 맞게» 만들기 금지 — 같게만) |
| 저장소 | 새 저장소 `C:\claude\nikke-core` (별도 git). 파이썬 쪽은 포크 `nikke-calc`의 `feat/union-raid` 기준 |
| 완료 조건 | §6 «완료 정의» 전부 |
| 작업량 | **7~11 작업일**(AI 코딩+검수), 달력 **3~4주**(서버 병행 소킹 1~2주 포함) — §7 |
| 첫 행동 | 러스트 도구체인 설치(사용자 허락) → 하네스 골든 생성 |

---

## 1. 목표와 비목표

**목표**
1. 파이썬 `simulate()`와 같은 입력(스쿼드 성장 dict·config·enemy)으로 같은 출력(총딜·니케별·히트 목록·버스트 사이클)을 내는 러스트 라이브러리.
2. 세 가지 얼굴: `nikke-core`(라이브러리) · `nikke-cli`(JSON in → JSON out, 하네스용) · `nikke-py`(PyO3, 서버) · `nikke-wasm`(브라우저).
3. 파이썬 엔진과 **병행 실행 + 대조**가 언제든 가능한 구조.

**비목표**
- 규칙 개선. 파이썬이 틀렸어도 러스트는 똑같이 틀린다. 고칠 건 파이썬에 먼저 고치고 골든을 갱신한 뒤 러스트가 따라간다.
- 파서(`scraper/`)·프로필 변환(`profile_convert`)·스펙 조립(`context/spec.py`) 이식. 이건 파이썬에 남는다.
- 성능을 위해 계산 순서를 바꾸는 것. 부동소수점 합산 순서는 파이썬과 동일해야 한다.

---

## 2. 경계 (무엇을 받고 무엇을 내는가)

### 입력 — 지금 서버·워커가 만드는 그대로
`web/server.py:_sim_one` / `web/src/worker.js` PY 블록:
```
squad  = build_squad(names, over, profile)          # list[dict] — 성장 층
config = build_config(squad, {duration, rng_mode:"expected", weapon_coeff, no_burst_chars, …})
enemy  = {"code": …, "def": …, "core_px": …, "parts": …, …}   # 없으면 None
result = simulate(squad, config=config, enemy=enemy)
```
스쿼드 원소 키(실측): `name, level, breakthrough, core_enhancement, affinity, skill_levels,
equipment, equip_skills, collection_stage, favorite_stage, console, cube, control,
burst_regen_time, weapon_mode_swap`. 무기·스킬은 여기 없다 — 엔진이 데이터 파일에서 이름으로 찾는다.

### 데이터 파일 — 엔진이 직접 읽는다 (경로 `data/`)
`parsed_nikke.json` `parsed_skills.json` `weapon_mechanics.json` `weapon_delays.json`
`char_defaults.json` `cube.json` `collection.json` `affinity.json` `console.json`
`equipment_skills.json` `equipment_stats.json` `level_stats.json` (+ `base_stat_tables/`).
러스트도 **같은 파일을 그대로** 읽는다. 스키마 변환 없음 — 니케·스킬 추가가 데이터로 끝나야 한다.

### 출력 — `SimResult`(`calculator/sim_result.py`)
`hits: [HitEvent{t, caster, damage(int), is_crit, crit_frac, hit_tag}]`, `char_total{name: int}`,
`squad_total: int`, `duration`, `burst_casts`, `full_bursts`, `top_atk_picks`, `log`.
서버가 여기서 파생하는 것(`detail`·`dps_timeline`·`burst_cycles`·`top_atk`)은 §4-5에서 함께 옮기거나
파이썬에 남겨 러스트 히트 목록을 먹인다(1차는 후자).

### JSON 직렬화 규칙
- 입력: 파이썬 dict를 `json.dumps(ensure_ascii=False, sort_keys=False)`. 키 순서 보존(dict 순서 의미 있음).
- 출력: 히트는 `t`를 소수점 그대로(`repr`), `damage`는 정수. 비교는 문자열이 아니라 값으로.
- 러스트 쪽은 `serde_json` + `IndexMap`(순서 보존). `f64`는 그대로, 파이썬 `int`는 `i64`.

---

## 3. 저장소·크레이트 구조

```
nikke-core/
  Cargo.toml                 # workspace
  crates/core/               # 라이브러리 — 계산 전부. no_std 아님, 그러나 std만(외부 최소)
    src/data.rs              # 데이터 파일 로딩·인덱스 (parsed_nikke/skills/…)
    src/spec.rs              # 성장 dict → 캐릭터 인스턴스 (base_stat.py 대응 + growth 해석)
    src/base_stat.rs         # base_stat.py
    src/damage.rs            # damage.py
    src/buff/                # buff_manager.py — mod.rs, plan.rs, value.rs, cond.rs, target.rs, trigger.rs
    src/timeline/            # timeline.py — mod.rs, fire.rs, reload.rs, charge.rs, burst.rs, control.rs
    src/result.rs            # sim_result.py (SimResult·HitEvent)
    src/pyfloat.rs           # 파이썬 의미 재현: round(은행가), // 와 %, int(), math.inf 비교
  crates/cli/                # nikke-cli: stdin JSON → stdout JSON. 하네스가 부른다
  crates/py/                 # nikke-py: PyO3 — simulate_json(str) -> str
  crates/wasm/               # nikke-wasm: wasm-bindgen — simulate_json(str) -> str
  harness/                   # 파이썬: 골든 생성·비교 (nikke-calc를 sys.path로 참조)
    gen_golden.py            # 랜덤 덱 × 조건 → cases/*.in.json + *.gold.json
    compare.py               # 러스트 출력 vs 골든, 첫 불일치 지점 보고
    cases/                   # 생성물 (git에 올린다 — 재현성)
  docs/PARITY.md             # 파이썬 의미 대응표 + 발견한 함정 기록 (살아 있는 문서)
```
의존: `serde`, `serde_json`, `indexmap`, `pyo3`(py), `wasm-bindgen`(wasm). 난수는 `rng_mode:"expected"`만
1차 지원(서버·웹이 쓰는 모드). 확률 모드는 2차.

---

## 4. 단계별 작업

각 단계는 «작업 → 검수 → 대조 통과»가 한 묶음이다. 검수는 코드 리뷰 + 하네스 재실행.

### 4-0. 준비 (0.5일)
- [ ] rustup·cargo·wasm-pack 설치 — **다운로드라 허락 필요**. Windows MSVC 툴체인 또는 GNU.
- [ ] `nikke-core` 저장소 생성, workspace 뼈대, CI 없이 `cargo test` 로컬.
- [ ] `harness/gen_golden.py`: 파이썬 엔진으로 케이스 생성. 구성:
  - 덱: 파싱된 니케(`roster parsed=True`)에서 무작위 5인 **300개**. 중복 없음. 시드 고정.
  - 조건: 약점 코드 5종 × 시간 {60,120,180} × 컨트롤 {없음, 톡톡이, 홀드, 장전컨, 원클립, 버스트 금지, 선버} × 레이드 설정 {기본, 코어 있음, 파츠 보스, 계수 SG0.9}. 전부 곱하지 말고 **라틴 방격**으로 300개에 흩뿌린다.
  - 스펙: 고정 스펙 250개 + **실제 프로필** 50개(사용자 스펙 파일, git에 안 올림 — `cases/private/`).
  - 골든: 위 §2 출력 전체를 JSON으로.
- [ ] `harness/compare.py`: 값 단위 비교. 실패 시 «몇 번째 히트, 어느 캐릭터, t, 파이썬 값, 러스트 값»과 그 직전 히트 3개를 같이 찍는다.
- **수락**: 골든 300건 생성, 파이썬 자기 자신과의 비교 통과(결정론 확인 — 두 번 돌려 같아야 한다).

### 4-1. 데이터 + 스펙 (0.5일)
- [ ] `data.rs`: 12개 JSON 로딩. 파싱 실패는 어느 파일 어느 키인지 말하고 죽는다.
- [ ] `spec.rs`/`base_stat.rs`: 성장 dict → 스탯(공·방·체), 장비·오버로드·큐브·소장품·애장품·콘솔 합성. `base_stat.py` 276줄 1:1.
- **수락**: 300케이스 × 5명의 «초기 스탯 스냅샷»(파이썬에서 `calc_base_stats` 결과를 골든에 추가)이 전부 같다.

### 4-2. 대미지 식 (0.5일)
- [ ] `damage.rs`: `damage.py` 476줄. 방어 계산·크리 기댓값·코어·파츠·속성·계수.
- **수락**: 파이썬에서 단위 벡터(버프 dict + 스탯 → 대미지) 2,000개를 뽑아 골든화, 전부 같다.

### 4-3. 버프 관리자 (2~4일) — 가장 크다
`buff_manager.py` 3,474줄. 어휘: stat 140종 · target 66종 · condition 33종.
- [ ] `ActiveBuff` 구조와 `_active` **순서 보존**(Vec, 삽입 위치 동일).
- [ ] 트리거 처리(`_notify`, 카운트 감소, pending burst dmg) — 실행 순서 규칙(GAMEPLAY.md §효과 실행 순서) 그대로.
- [ ] 대상 결정: 66종 + 지연 resolve(`_resolve_lazy`) — resolve **시점**과 부작용(로그·고정)까지 같게.
- [ ] 조건 33종(`_runtime_condition_ok`), `has_runtime_conditions` 판정.
- [ ] `_get_value`: 스킬 레벨 값 표, caster_based 환산, `lost_hp_pct`, `stack_count`+`scaling_ref`, 스택 곱.
- [ ] `get_buffs`: 계획(`_build_plan`)·시간 불변 접기·`_PLAN_*` 스텝 — 접는 방식과 순서를 **그대로**(합산 순서가 결과다). 같은 틱 캐시(`(caster,t,version,exclude)`)도 그대로(있으나 없으나 결과는 같아야 하지만, 부동소수점 재계산 경로가 달라지지 않게 그대로 둔다).
- [ ] 면역·기절·보호막·탄 소모 버프(`consume_bullet_buffs`)·무기 변경(`get_weapon_change`).
- **수락**: 골든에 «틱별 get_buffs 덤프»를 추가(300건 중 30건, 캐릭터별 전 틱)하여 dict 값이 전부 같다. 그 다음 히트 목록 비교로 넘어간다.

### 4-4. 타임라인 (1.5~2.5일)
`timeline.py` 2,479줄. dt=1/60 고정 스텝.
- [ ] 캐릭터 상태기계: 사격(`_fire`, 펠릿·연사·탄퍼짐 계수), 재장전(시작/복귀 지연, 원클립, SG 규칙), 차지(`_tick_charge`, `_effective_charge_time`, 톡톡이·홀드), 엄폐(장전컨·버스트 엄폐컨), 무기 변경 모드.
- [ ] 버스트: 단계 진행·풀버스트 시작/종료·재진입 딜레이·상한·순서 규칙(`_rebuild_burst_order`, 패턴 랭크, 선버, 버스트 금지).
- [ ] 적: 코어·파츠·파츠 파괴 주기·방어력.
- [ ] `SimResult` 조립: 히트 목록(`t` 값은 파이썬 float 누적과 같은 연산으로 — `t += dt`인지 `i*dt`인지 그대로).
- **수락**: 300건 히트 목록·총딜·니케별·버스트 사이클 전부 같다. 실제 프로필 50건 포함.

### 4-5. 결과 파생 + 바인딩 (0.5~1일)
- [ ] `nikke-cli`(하네스용)는 4-1부터 이미 쓴다. 여기서는 `nikke-py`(PyO3, `simulate_json`)와 서버 연결.
- [ ] 서버: `_sim_one`에 **병행 모드** — 파이썬 결과를 그대로 쓰되 러스트도 돌려 차이를 `stats`에 카운트하고 첫 불일치 입력을 `/var/lib/nikke-decklab/parity/`에 저장. 환경변수 `NIKKE_RUST=shadow|off|primary`.
- [ ] `detail`·`dps_timeline`·`burst_cycles`·`top_atk`는 1차에선 파이썬 함수가 러스트 히트 목록을 받아 만든다(같은 입력이면 같은 출력).
- **수락**: 서버 shadow 모드에서 실제 요청 1주 차이 0.

### 4-6. WASM (0.5~1일)
- [ ] `nikke-wasm` 빌드(`wasm-pack build --target web`), 크기 확인(목표 < 1MB gz 전).
- [ ] `web/src/worker.js`에 두 번째 경로: 데이터 JSON 12개 fetch → `simulate_json`. Pyodide 경로는 남겨 두고 스위치로 고른다(사용자 설정 «브라우저 계산 엔진»).
- [ ] 브라우저에서도 하네스 30건을 돌려 서버(파이썬)와 같은지 확인하는 숨은 페이지(`/parity.html`, 로컬 전용).
- **수락**: 브라우저 결과 = 골든. 첫 로딩 1MB 이하, 덱 하나 0.3초 이하.

### 4-7. 전환 (소킹 뒤, 0.5일)
- [ ] `NIKKE_RUST=primary`: 러스트가 답하고 파이썬은 샘플링(요청의 5%)으로 계속 대조.
- [ ] 워커 3개 → 그대로(코어 부하가 사라져 의미 없음). `SIM_SLOTS`도 그대로.
- [ ] 브라우저 기본 엔진을 WASM으로.

---

## 5. 파이썬 의미 대응표 (PARITY.md 시작점)
러스트가 «자연스럽게» 다르게 하는 것들. 하나라도 놓치면 마지막 자리가 다르다.

| 파이썬 | 러스트에서 | 비고 |
|---|---|---|
| `round(x)` | 은행가 반올림 직접 구현 | `f64::round`는 half-away-from-zero |
| `int(x)` | `trunc` | 음수에서 갈린다 |
| `a // b`, `a % b` | 파이썬 부호 규칙 구현 | 음수 피연산자 |
| `x / y` (int/int) | 항상 f64 | |
| `sum(list)` | 순서대로 `+` | `iter().sum()`은 같지만 fold 순서 바꾸지 말 것 |
| `math.inf` 만료 | `f64::INFINITY` 비교 | `t >= expires_at` 그대로 |
| dict 순서 | `IndexMap` | `_active` Vec, `buffs` IndexMap |
| `dict.get(k, 0.0) + v` | 같은 순서로 누적 | 키별 누적 순서 = 계획 순서 |
| `max(1, base + …)` | 같은 캐스팅 시점 | `int(...)` 위치 |
| `float` 문자열 값(`"32.68"`) | `str::parse::<f64>` | 파이썬 `float()`과 같은 최근접 |
| `x * 100 / 100` 식 | 괄호·순서 그대로 | 결합 순서 바꾸면 끝자리 변함 |
| 스택 곱 `base * stack` | int→f64 변환 시점 동일 | |

발견하는 대로 이 표에 보태고, 항목마다 하네스 케이스를 하나씩 남긴다.

---

## 6. 완료 정의
1. 골든 300건(+실스펙 50건) **비트 동일**: 히트 `t`·`damage`·`caster`·`hit_tag`, `char_total`, `squad_total`, `burst_casts`, `full_bursts`.
2. `python -m context.snapshot` 30건을 러스트로도 통과(같은 러너에 `--engine rust` 옵션).
3. 서버 shadow 모드 실제 트래픽 **1주 이상 차이 0**.
4. WASM 브라우저 결과 = 골든, 번들 < 1MB.
5. `docs/PARITY.md`에 발견한 함정 전부 기록, 각 항목에 케이스 있음.
6. 새 니케 추가 리허설: 파이썬에 스킬 JSON만 추가하고 러스트 코드 무변경으로 같은 결과 — 어휘 밖 메카닉이면 두 엔진에 같이 넣고 골든 갱신하는 절차가 문서에 있음.

---

## 7. 소요 시간 (AI가 코딩·검수)

| 단계 | 작업일 |
|---|---|
| 4-0 준비·하네스 | 0.5 |
| 4-1 데이터·스펙 | 0.5 |
| 4-2 대미지 | 0.5 |
| 4-3 버프 관리자 | 2~4 |
| 4-4 타임라인 | 1.5~2.5 |
| 4-5 바인딩·서버 병행 | 0.5~1 |
| 4-6 WASM | 0.5~1 |
| 4-7 전환 | 0.5 |
| 검수(코드 리뷰·재실행·실스펙) | 1 |
| **합계** | **7~11 작업일** |

달력으로는 **3~4주**: 코딩 뒤 서버 병행 소킹 1~2주가 기다리는 시간이다. 폭이 있는 곳은 4-3·4-4 —
«다르다»가 몇 번 나오고 그 원인이 §5 표에 있는 것인지 새 함정인지에 달렸다. 4-0 하네스가 끝나면
4-3부터는 «케이스 몇 건 남았나»로 남은 시간이 보이므로 그때 재견적한다.

---

## 8. 위험과 대응
- **조용한 불일치**(에러 없이 값만 다름) → 골든 300건 + 틱별 버프 덤프 + shadow 소킹. 세 겹.
- **파이썬 쪽 비결정성**(있다면) → 4-0에서 파이썬 두 번 돌려 자기 일치부터 확인. `uid`·`id()` 문제는 이미 고쳐져 있음(`ActiveBuff.uid`).
- **새 메카닉이 소킹 중 파이썬에 들어옴** → 골든 갱신 절차: 파이썬 변경 커밋 → `gen_golden` 재실행 → 러스트 따라가기 → 비교 통과 → 둘 다 태그.
- **WASM 크기** → 데이터는 번들 밖(fetch), 코드만 wasm. `opt-level="z"`, `wasm-opt`.
- **PyO3 빌드가 서버(aarch64)에서** → 서버에 러스트 툴체인 없이 가려면 CI/로컬 크로스 빌드로 `.so`를 만들어 배포에 싣는다. 1차는 서버에 `rustup` 설치가 단순하다(허락 필요).

---

## 9. 새 세션 첫 프롬프트 (그대로 붙여 넣기)

> `C:\claude\nikke-calc\context\RUST-CORE-PLAN.md`와 `PERF-ENGINE.md` §7~§9를 읽고, 4-0부터 시작해.
> 새 저장소는 `C:\claude\nikke-core`. 파이썬 엔진은 `C:\claude\nikke-calc`(브랜치 feat/union-raid)를
> 참조만 하고 수정하지 마. 원본(Jgaram) 저장소는 건드리지 마. 러스트 도구 설치는 하기 전에 물어봐.
> 단계마다 하네스 통과 결과를 보고하고, «다르다»가 나오면 원인을 PARITY.md에 적어.
