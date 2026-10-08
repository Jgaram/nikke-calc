# 시뮬 요청·결과 JSON — `runner/sim.py --request` · `--json` · `--batch`

다른 프로그램이 이 레포를 **평가기**로 쓸 때의 입출력 계약이다. 지금 쓰는 쪽은
`Jgaram/nikke-opt`(5덱 편성 최적화기)로, `python -m runner.sim`을 서브프로세스로 수천~수만 번
띄워 결과를 읽는다. 사람이 읽는 `--view` 텍스트 출력은 이 레포의 디버깅·문서용으로 그대로 남는다.

**입력은 요청 JSON 한 가지다.** 정본은 `runner/request.py`의 `SCHEMA`(JSON Schema 2020-12)이고, 이 문서는 그
설명이다. 출력(결과·오류 객체)은 이 문서가 정본이다. 칸을 바꾸면 `schema_version`을 올리고 이 문서를 같은 커밋에서
고친다(§버전). 계약 검사는 `tests/`다:

```bash
python -m unittest discover -s tests -v
python -m runner.request > request.schema.json      # 요청 스키마 — 호출하는 쪽이 검증·타입 생성에 쓴다
```

표준을 따른다 — 요청 모양은 **JSON Schema 2020-12**, 오류 위치는 **JSON Pointer(RFC 6901)**, 공통값 위에 요청을
얹는 병합은 **JSON Merge Patch(RFC 7386)**.

---

## 부르는 법

```bash
# 한 건 — 요청 JSON(문자열 또는 파일) → stdout에 결과 JSON 객체 하나 (한 줄)
python -m runner.sim --request '{"squad": ["크라운", "헬름"], "sim": {"expected": true}, "boss": "솔로 레이드 S40"}' --json
python -m runner.sim --request 요청.json --json

# 여러 건 — stdin JSON Lines(한 줄 = 요청 하나) → stdout JSON Lines
python -m runner.sim --batch < 요청.jsonl
```

사람이 쓰는 CLI 옵션(`"크라운,헬름" --expected --tap "앨리스:4.0"`…)도 그대로 된다. CLI 옵션은 요청의 **사람용
표기**라 `args_to_request()`가 요청으로 바꾼 뒤 같은 길을 탄다(§CLI 옵션과의 관계). `--request`와 CLI 옵션을 함께
주면 CLI 옵션이 그 위에 덮인다. `--view`·`--char`는 텍스트 표시 전용이라 `--json`·`--batch`와 같이 주면 오류다.

### 보장

| | |
|---|---|
| **총딜 동일** | 같은 요청이면 `total_damage`가 텍스트 출력의 `스쿼드 총 딜`과 **정확히** 같다. CLI 옵션과 그것을 편 요청도 같다. 전부 같은 `prepare()` → `execute()`를 거치고 출력만 다르다 |
| **stdout은 JSON뿐** | 경고·이탈 보고·진행 메시지는 JSON 안의 칸으로 들어간다. 시뮬 도중 다른 코드가 `print`를 해도 stderr로 돌린다 |
| **병렬 안전** | 결과를 어디에도 쓰지 않는다. 평가기 버전을 잴 때도 `git --no-optional-locks`라 `.git/index`를 건드리지 않는다. 프로세스를 동시에 여러 개 띄워도 된다 |
| **결정론** | `sim.expected: true`(기대값 모드)나 `sim.seed`를 주면 프로세스·순서와 무관하게 같은 값이다. 둘 다 없으면 매번 다르다(`warnings`에 실린다). 배치에서 앞 줄이 뒷줄 결과를 바꾸지 않는다 |

시뮬 한 번이 수 초(180초 5인 기준 5초 안팎)라 비용은 거의 시뮬 자체다. 배치는 파이썬 시작·import
몫(0.2초 남짓)을 아낀다. 많이 돌릴 땐 배치 프로세스 여러 개를 병렬로 띄운다.

---

## 요청

요청 하나 = 스쿼드 하나를 한 번 시뮬하는 데 필요한 전부. 모양은 계산기의 층을 그대로 따른다 — `squad`만 필수이고,
적지 않은 칸은 기본값(기본 스펙 · 기본 적 · 180초 · 랜덤 모드)이다.

```jsonc
{
  "schema_version": 2,                          // 선택. 주면 2여야 한다
  "id": "s1",                                   // 선택. 배치 결과 줄에 그대로 돌려준다 (아무 JSON 값)
  "squad": ["앨리스", "프리카", "크라운", "헬름", "미하라"],      // 정식 명칭, 배치 순서 (1~5명)
  "sim":       {"duration": 180, "expected": true, "seed": 7, "first_burst": 3.0,
                "burst_gauge_mode": "fixed", "allow_unparsed": false},
  "boss":      "솔로 레이드 S40",               // 프리셋 이름 · .json 경로 · 인라인 스크립트 객체
  "enemy":     {"def": 31784, "code": "풍압"},  // boss 위에 덮는 적 스탯
  "operation": {"camera": "프리카", "control_mode": "solo", "no_burst": "헬름",
                "auto": ["미하라"], "tactics": [{"name": "버충", "target": "프리카"}]},
  "profile":   {"base": "default", "chars": {"크라운": {"skill_levels": "7/7/7"}}},   // §육성
  "profile_level": "fixed",
  "chars": {                                    // §니케별 운용 — 하네스 SQUADS의 chars와 같은 어휘
    "프리카": {"control": {"tap_fire": {"rate": 4.0, "window": "burst_charge"},
                           "reload": {"policy": "finish_by_fb_end"},
                           "burst": {"pattern": [1, 3, 5], "delay": 0.5}},
               "cube": {"name": "택티컬 베어 큐브", "level": 15}},
    "앨리스": {"control": {"tap_fire": false}}  // false = 그 축을 레이어 값까지 끈다
  }
}
```

### 칸

| 칸 | 형 | 뜻 · 기본값 |
|---|---|---|
| `squad` | 이름 목록 (1~5) | **필수.** 정식 명칭(`docs/ALIASES.md`). 같은 니케를 두 번 적어도 계산한다 — 판단은 요청하는 쪽 몫이다 |
| `sim.duration` | 수 > 0 | 시뮬 시간(초). 기본 180 |
| `sim.expected` | bool | 크리·코어히트를 기대값으로(결정론). 기본 false |
| `sim.seed` | 정수 | 난수 시드. 기대값 모드에선 결과에 영향이 없다 |
| `sim.first_burst` | 수 ≥ 0 | 첫 버스트 시각(초). 기본 3 |
| `sim.burst_gauge_mode` | `fixed` · `accumulate` | 버스트 사이클 판정(`docs/mechanics/버스트 게이지.md`). 기본 fixed |
| `sim.allow_unparsed` | bool | 스킬 미파싱 니케를 스킬 0개로 돌린다(파싱 전 신캐 전용) |
| `boss` | 문자열 \| 객체 | 문자열 = `data/boss_presets.json` 프리셋 이름, `.json`으로 끝나면 스크립트 파일 경로. 객체 = 인라인 보스 스크립트(형식 `runner/boss.py` 모듈 docstring) |
| `enemy` | 객체 | `boss` 위에 덮는 적 칸: `def` · `code`(풍압·수냉·작열·전격·철갑) · `core_px` · `distance` · `has_parts` · `part_break_interval` · `optimal_range_weapons`. 뜻은 `calculator/timeline.py` `DEFAULT_ENEMY` 주석 |
| `operation.camera` | 이름 \| 이름 목록 \| `""` | 카메라를 볼 니케(`docs/CONTROL.md §카메라`). `""` = 아무도 안 본다. 기본은 컨트롤·3번 자리에서 유도 |
| `operation.camera_mode` | `single` · `shared` | 기본 single |
| `operation.control_mode` | `solo` · `warn` · `strict` | 조작자 한 명 제약(`docs/CONTROL.md §조작자는 한 명`). 기본 solo |
| `operation.no_burst` | 이름 | 버스트를 쓰지 않을 니케 |
| `operation.auto` | `true` \| 이름 목록 | 캐릭터별 기본 레이어(`data/char_defaults.json`)를 통째로 건너뛸 니케. `true` = 전원(+ 패턴 모드 저지 우선 타격도 끈다) |
| `operation.tactics` | `[{name, target?}]` | 켤 택틱(`data/tactics.json`). `target`은 자동 선택 규칙을 덮는 담당 |
| `profile` | 문자열 \| 객체 | 육성(2.5층) — §육성 |
| `profile_level` | `fixed` · `sync` | `profile`의 레벨 정책(`docs/HARNESS.md §레벨 정책`). 기본 fixed. `profile` 없이 주면 오류 |
| `chars` | {이름: 객체} | 니케별 운용(3층) — §니케별 운용 |

이름을 가리키는 칸(`chars`의 키 · `no_burst` · `camera` · `auto` · `tactics[].target` · 컨트롤의 `gate.burst_user`)은
스쿼드에 있는 이름이어야 한다.

### null · false

- 객체 안의 **`null`은 「적지 않음」**이다(RFC 7386). 배치 공통값이 준 칸을 줄에서 기본값으로 되돌릴 때 쓴다
  (`{"sim": {"seed": null}}`). 스키마는 null을 걷어 낸 뒤의 모양이다.
- 그래서 **끄는 값은 `false`**다 — 컨트롤 축(`chars.<니케>.control.tap_fire: false`)과 버스트 패턴
  (`control.burst.pattern: false`)은 레이어가 붙인 값까지 지운다.

### 검사

요청은 시뮬 전에 두 번 걸러진다. 어느 쪽에서 걸리든 오류 객체의 `path`가 잘못된 칸을 가리킨다(§오류 객체).

1. **스키마** — 형 · 어휘 · 범위 · 모르는 키. 숫자 범위와 이름 목록(장비 등급 · 소장품 단계 · 큐브 레벨 · 레벨 1~1000 ·
   오버로드 키 …)은 손으로 다시 적지 않고 계산기 표(`data/base_stat_tables/`)·러너 상수에서 만든다.
   `integer` 칸은 정수만 받는다(`7.0`은 거절).
2. **의미** — 모르는 니케 이름, 스쿼드에 없는 이름 참조, 컨트롤 어휘(`timeline.validate_control()`), 큐브 카탈로그
   (`spec.check_cube()` — 모르는 큐브·효과 모델이 없는 큐브는 오류), 버스트 패턴 카탈로그.

해석할 수 없는 입력은 끊고, 해석할 수 있으면 게임에서 드문 조합(같은 니케 둘 · 오버로드 줄 수)이어도 계산한다.
한 객체 안에 같은 키를 두 번 적은 JSON도 거절한다 — 뒤 값이 조용히 이기지 않게.

### CLI 옵션과의 관계

CLI 옵션은 아래 칸의 사람용 표기다. **준 옵션만** 칸이 된다(그래야 `--request`·배치 줄 위에 덮을 때 주지 않은 옵션이
그쪽 값을 지우지 않는다). `python -m runner.sim --help`에 각 표기가 있다.

| CLI | 요청 |
|---|---|
| 위치 인자 `"A,B,C"` | `squad` |
| `--duration` · `--expected` · `--seed` · `--first-burst` · `--burst-gauge-mode` · `--allow-unparsed` | `sim.*` |
| `--boss` | `boss` (`{`로 시작하면 인라인 JSON) |
| `--enemy-def` · `--enemy-code` · `--core-px` · `--distance` · `--has-parts` · `--part-break-interval` | `enemy.*` |
| `--camera` · `--camera-mode` · `--control-mode` · `--no-burst` · `--auto` | `operation.*` |
| `--tactic "버충:프리카"` | `operation.tactics[] = {"name": "버충", "target": "프리카"}` |
| `--profile` · `--profile-level` | `profile` (`{`로 시작하면 인라인 JSON) · `profile_level` |
| `--tap` · `--click` · `--reload-ctrl` · `--cancel-on-full` · `--cover-ctrl` · `--hold-ctrl` · `--aim` | `chars.<니케>.control.*` |
| `--burst-pattern` · `--burst-delay` | `chars.<니케>.control.burst.pattern` · `.delay` (`없음` = `false`) |
| `--cube "이름:큐브[:레벨]"` · `--favorite "이름:단계"` · `--mode-swap 이름` | `chars.<니케>.cube` · `.favorite_stage` · `.weapon_mode_swap` |

---

## 결과 객체

```jsonc
{
  "schema_version": 2,
  "total_damage": 3328221582,
  "members": [                                   // 배치 순서 그대로 (딜 순 정렬 아님)
    {"name": "리틀 머메이드", "damage": 633814499, "share": 0.19043638873921587},
    ...
  ],
  "duration": 180.0,
  "expected": true,
  "seed": null,
  "boss": {"def": 31784, "code": "풍압", "atk": 114344, "core_px": 0, ..., "patterns": []},
  "boss_label": "솔로 레이드 S40",
  "spec": { ... },                               // 아래 §spec
  "control": null,
  "control_detail": {"mode": "solo", "camera_mode": "single", "by_char": {}, "max_concurrent": 0,
                     "overlap_t": 0.0, "overlaps": [], "preempted": {}},
  "warnings": ["기본 스펙(1층) 이탈 1명 — 헬름"],
  "evaluator": {"repo": "Jgaram/nikke-calc", "commit": "e3017db…", "dirty": false}
}
```

| 칸 | 형 | 뜻 |
|---|---|---|
| `schema_version` | int | 이 형식의 판. 지금 `2` (§버전) |
| `total_damage` | int | 스쿼드 총딜 = 보스(파츠 포함)에 들어간 딜. 텍스트 `스쿼드 총 딜`과 같은 값 |
| `members` | list | **배치 순서대로** `{name, damage, share}`. `damage`는 int, `share`는 `damage / total_damage`(0~1 비율, 퍼센트 아님). 총딜이 0이면 0.0 |
| `duration` | float | 실제로 돈 시뮬 시간(초) |
| `expected` | bool | 기대값 모드(`sim.expected`) 여부 |
| `seed` | int \| null | 넘긴 시드. 기대값 모드에선 결과에 영향이 없다 |
| `boss` | dict | **실제로 적용된 적 dict** — 기본 적(`timeline.DEFAULT_ENEMY`) 위에 프리셋·스크립트(`boss`)와 `enemy` 칸을 얹고 `skill`·`part` 참조까지 전개한 최종값 |
| `boss_label` | str \| null | 보스를 바꿨을 때 그 이름(프리셋·파일·`인라인 스크립트 (프리셋 …)`). 기본 적이면 null |
| `spec` | dict | 적용된 스펙 레이어 요약 — §spec |
| `control` | str \| null | 조작 요약 한 줄(텍스트 출력의 `조작 구간: …`). 조작이 없으면 null |
| `control_detail` | dict | 같은 조작 요약을 기계가 읽는 모양으로 — §니케별 운용 §결과에서. 조작이 없어도 언제나 있다 |
| `warnings` | list[str] | 결과를 읽을 때 알아야 할 것 — §warnings |
| `evaluator` | dict | 결과를 낸 평가기 버전 — `repo`, `commit`(HEAD 해시), `dirty`(추적 중인 파일에 커밋 안 된 변경이 있나). git을 못 부르면 `commit`·`dirty`가 null |

쫄몹·저지원에 들어간 딜은 `total_damage`·`damage`에 없다 — 텍스트 출력과 같다(`calculator/sim_result.py`
`SimResult` 칸 주석).

### spec

이탈 보고(`docs/HARNESS.md §이탈 보고`)를 기계가 읽는 모양으로 옮긴 것이다.

| 칸 | 형 | 뜻 |
|---|---|---|
| `baseline` | `"default"` \| `"profile"` | 이탈의 기준선. `default` = 기본 스펙(1층), `profile` = 1층+레이어+육성 프로필 |
| `at_baseline` | bool | 기준선 그대로인가(이탈 0명) |
| `char_defaults.applied` | list[str] | 캐릭터별 기본 레이어(`data/char_defaults.json`)가 실제로 값을 바꾼 니케 |
| `char_defaults.skipped` | list[str] | `--auto`로 레이어를 건너뛴 니케 |
| `deviated` | list[str] | 기준선을 벗어난 니케 (배치 순서) |
| `deviations` | dict | 니케 → `[{key, baseline, value, source}]`. `source`는 `layer`(기본 레이어) \| `override`(호출자 지정) |
| `tactics` | dict | 붙은 자동 택틱 → 니케 목록 |
| `profile` | dict \| null | `--profile`을 썼으면 `{name, level_mode, source, base, ungrown}` — `source`는 `file` \| `inline`, `base`는 프로필에 없는 니케를 무엇으로 계산했나(§육성), `ungrown`은 미육성으로 계산한 멤버(배치 순서) |
| `preview` | list[str] | 출시 전 카드 기준(`[프리뷰 · 미검증]`) 니케 |
| `text` | str | 텍스트 출력에 찍히는 이탈 블록 그대로 (`spec.format_deviations()`) |

### warnings

다음이 해당할 때 문자열 한 줄씩 실린다. 문구는 사람용이라 **바뀔 수 있다** — 프로그램은 판단을
`spec`의 구조화된 칸으로 하고, 이 목록은 결과와 함께 그대로 보여 주는 데 쓴다.

- 프리뷰 니케가 있다
- 육성 프로필을 썼다 (그 머리줄과 프로필 경고 전부)
- 기준선 이탈이 있다 — `기본 스펙(1층) 이탈 N명 — 이름, …`
- 조건은 맞았는데 붙지 않은 택틱이 있다
- 기대값 모드도 시드도 아니다 — `seed 미지정 — 매 실행 결과가 다름`
- 동시 조작이 2명 이상이다(비현실적 상한) — `control`과 같은 줄
- 효과 모델이 없는 보스 패턴이 있다

---

## 오류 객체

입력이 잘못됐거나 평가기가 실패하면 stdout에 이것 하나를 내고 0이 아닌 코드로 끝난다.
트레이스백은 stderr로만 간다.

```json
{"schema_version": 2, "error": {"type": "invalid_input", "exception": "RequestError", "message": "모르는 니케 '없는 니케' — 정식 명칭만 받는다 (docs/ALIASES.md)", "path": "/squad/0"}}
```

| `type` | 종료 코드 | 언제 |
|---|---|---|
| `invalid_input` | 2 | 요청 검사(§요청 §검사)에 걸린 칸 · 스킬 미파싱 · 잘못된 보스 스크립트 · 없는 프로필 파일 등. 텍스트 모드가 메시지만 찍고 코드 2로 끝나던 경우와 같다 |
| `internal_error` | 1 | 그 밖의 예외 — 평가기 버그다. stderr에 트레이스백 |

`exception`은 파이썬 예외 이름이고 `message`는 사람용 문구다. 판단은 `type`으로 한다. **`path`**는 잘못된 칸의
JSON Pointer(RFC 6901 — `/chars/프리카/control/reload/margin`, 요청 자체면 `""`)이고, 칸을 특정하지 못한 오류(조립 중
끊긴 경우)는 `null`이다.

---

## 배치 모드 — `--batch`

stdin을 한 줄씩 읽어 줄마다 결과(또는 오류) 객체 한 줄을 stdout에 낸다. 줄마다 flush하므로
파이프로 흘려 읽어도 된다. 빈 줄은 건너뛴다. **한 줄 = 요청 하나**(§요청)다.

```jsonl
{"id": "s1", "squad": ["리틀 머메이드","크라운","라피 : 레드 후드","미하라","헬름"], "boss": "솔로 레이드 S40"}
{"id": "s2", "squad": ["크라운","헬름"], "sim": {"seed": 7, "duration": 60}, "operation": {"no_burst": "크라운"}}
```

- `--batch`와 함께 준 CLI 옵션·`--request`는 **모든 줄의 공통값**이다. 줄은 그 위에 RFC 7386으로 덮인다 — 객체는
  재귀 병합, 배열·값은 교체, `null`은 그 칸을 지워 기본값으로. 예: `--batch --expected`면 모든 줄이 기대값 모드이고,
  줄의 `{"sim": {"expected": null}}`이 그 줄만 되돌린다.
- **`id`**(아무 JSON 값)는 결과에 그대로 돌려준다. 모든 출력 줄에는 입력 줄 번호 **`line`**(1부터, 빈 줄 포함해 센다)이 붙는다.
- 검사를 못 지난 줄, JSON이 아닌 줄, 객체가 아닌 줄은 **그 줄만** 오류 객체가 되고 다음 줄로 넘어간다.
- 모든 줄을 처리하면 종료 코드 0이다(실패한 줄이 있어도). 0이 아닌 코드는 배치 자체를 시작하지 못했을 때뿐이다 — 위치 인자로 스쿼드를 줬거나 `--view`를 같이 준 경우 등. 그때도 stdout에 오류 객체 한 줄을 낸다.

---

## 육성 — `profile`

아무것도 주지 않으면 전원 기본 스펙이다(`docs/HARNESS.md §기본 스펙`). 육성을 바꾸려면 `profile`을 준다.

| 값 | 뜻 |
|---|---|
| 문자열 | `profiles/<이름>.json` — `profile-sync`가 만든 **내 계정** 프로필. 이 레포의 로컬 파일이다 |
| 객체 (CLI `--profile`은 `{`로 시작하는 JSON 문자열) | **인라인 프로필** — 파일 없이 요청에 육성을 싣는다. **다른 프로그램은 이쪽을 쓴다** |

다른 프로그램은 `profiles/`에 파일을 쓰지 않는다. 그 폴더는 `profile-sync`만 만드는 개인 데이터이고(`AGENTS.md`),
인라인은 육성이 요청 줄에 그대로 들어 있어 호출자의 캐시 키가 내용을 따라간다 — 이름은 같은데 내용이 바뀐 파일을
옛 결과로 읽는 사고가 없다.

```jsonc
{"squad": ["라피 : 레드 후드", "크라운", "헬름"], "sim": {"expected": true}, "profile": {
  "base": "default",                    // 적지 않은 니케 = 기본 스펙. 생략하면 "ungrown"(미육성)
  "chars": {
    "라피 : 레드 후드": {"skill_levels": "7/7/7", "overload": {"우월 코드 대미지": 2, "공격력": 1}},
    "크라운": {"skill_levels": 4, "overload": {"우월코드": 0, "공격력": 0}, "collection_stage": "없음"},
    "헬름": {}                            // 빈 항목 = 아래 층 그대로 (여기선 기본 스펙)
  }
}}
```

```bash
python -m runner.sim "크라운,헬름" --expected --json --profile '{"base": "default", "chars": {"크라운": {"skill_levels": "7/7/7"}}}'
```

### 최상위

| 키 | 값 | 뜻 |
|---|---|---|
| `chars` | dict | 니케 정식 명칭 → 항목(아래). 모르는 이름은 오류다 — 오타 난 니케가 조용히 `base` 상태로 계산되지 않게 |
| `base` | `"ungrown"`(기본) \| `"default"` | 프로필에 **없는** 니케를 무엇으로 계산하나. `ungrown` = 미육성(정본 `spec.py`의 `UNGROWN` — 돌파 0 · 스킬 1/1/1 · 장비 미장착 · 소장품 없음). 실제 계정에서 없다는 건 미보유라서 파일 프로필의 뜻과 같다. `default` = 기본 스펙(+ 캐릭터별 레이어) — 「기본 스펙에서 몇 명만 다르게」인 가상 육성용 |
| `_account` | dict | 계정 단위 값. `console`(콘솔 레벨 — `common_level`·`class_level`·`company_level`, 형식은 `calculator/base_stat.py` 모듈 docstring. 적지 않은 칸은 기본 스펙 값), `synchro_level`(`profile_level: "sync"`일 때만), `cubes`(보유 큐브 레벨 하한 — 넘는 큐브를 요구하면 경고) |
| `_meta` | dict | `name`을 주면 결과의 `spec.profile.name`에 실린다(기본 `인라인`) |

그 밖의 최상위 키는 오류다.

### 니케 항목

항목은 **아래 층 위에 병합된다**(dict는 재귀 병합, 값은 교체). 아래 층은 `base`가 `default`면 기본 스펙 + 레이어,
`ungrown`이면 미육성이다. 적지 않은 키·옵션은 아래 층 값이 남는다.

| 키 | 받는 표기 | 범위 |
|---|---|---|
| `skill_levels` | `"7/7/7"` · `[7, 7, 7]` · `7`(셋 다) · `{"1": 7, "2": 7, "3": 7}`(일부만 적어도 된다) | 1~10 |
| `overload` | `{옵션: 줄 수}` 또는 `{옵션: [줄별 레벨, ...]}`. 줄 수만 주면 레벨 10(기본 스펙과 같은 레벨). `0`·`[]`은 그 옵션 없음 | 레벨 1~15 |
| `breakthrough` | 정수 | 0~3 |
| `core_enhancement` | 정수 | 0~7 |
| `affinity` | 정수 | 1~40 |
| `collection_stage` | `"R0"`~`"R15"` · `"SR0"`~`"SR15"` · `"없음"`(미장착). 애장품은 `"SR15"` | |
| `favorite_stage` | 애장품 단계. 애장품이 없는 니케에는 영향이 없다 | 0~3 |
| `equip_skills` | 오버로드 옵션을 계산기 표기(합산 퍼센트, 또는 줄별 퍼센트 리스트)로 직접. 키는 아래 표의 영문 키뿐. `overload`와 같은 옵션을 함께 적으면 오류 | |
| `equipment` | 장비 4부위(`머리`·`몸통`·`팔`·`다리`) → `{tier, corp, level, skills}` — 형식은 `calculator/base_stat.py` 모듈 docstring. `tier`는 `T1`~`T9` · `기업` · `없음`(미장착), 생략 = 오버로드 장비 | 강화 0~5 |
| `level` | 정수. 보통은 적지 않고 `profile_level` 정책에 맡긴다(`docs/HARNESS.md §레벨 정책`) | 1~1000 |
| `console` | 그 니케만의 콘솔 레벨(`_account.console`과 같은 형식) | |
| `cube` | 큐브 — §니케별 운용 `cube`와 같은 두 표기·검사 | |

컨트롤·버스트 패턴처럼 **육성이 아닌 키는 오류다** — 운용은 요청의 `chars`·`operation`으로 준다.

**`overload` 옵션 이름**은 `equip_skills` 키나 인게임 이름이다. 인게임 이름의 정본은
`data/base_stat_tables/equipment_skills.json`의 `template` 문구다.

| 키 | 인게임 이름 |
|---|---|
| `atk_pct` | 공격력 |
| `element_bonus` | 우월 코드 대미지 |
| `max_ammo_pct` | 최대 장탄 수 |
| `crit_rate` · `crit_dmg` | 크리티컬 확률 · 크리티컬 대미지 |
| `charge_speed_pct` · `charge_dmg_pct` | 차지 속도 · 차지 대미지 |
| `accuracy_pct` · `def_pct` | 명중률 · 방어력 |

- 공백은 무시한다. 앞부분만 적어도 한 옵션으로 정해지면 받는다(`우월코드`·`최대장탄`). 여럿과 맞으면(`크리티컬`) 오류다.
- 줄 레벨이 섞이면(`[15, 15, 10]`) 줄별 퍼센트 리스트로 펴진다 — 최대 장탄·차지 속도는 레벨 그룹마다 따로
  반올림되기 때문이다(`runner/spec.py` §오버로드 장비 옵션).
- `base: "default"`에서 `overload`에 적지 않은 옵션은 기본 스펙 값이 남는다(최대 장탄 수 2줄 등, 캐릭터별
  레이어가 정한 값 포함). 없애려면 `0`을 적는다.

### 검사 — 인라인은 파일보다 엄하다

인라인 프로필은 요청의 일부라 요청 스키마를 지난다 — 모르는 키 · 모르는 니케 이름 · 육성이 아닌 키 · 읽을 수 없는
표기 · 범위 밖 값(장비 등급·오버로드 키·콘솔 칸 포함)은 모두 `path`가 붙은 `invalid_input` 오류 객체가 된다. 파일
프로필도 표기(스킬 레벨 1~10 · `overload`)는 같이 펴고 검사하지만, 돌파·소장품 같은 값 범위는 보지 않는다
(`profile_fetch.py`가 API에서 옮긴 값이다).

### 결과에서

- 프로필을 쓰면 `spec.baseline`이 `"profile"`이 되어 이탈 기준선이 「1층 + 레이어 + 프로필」로 바뀐다. 그래서
  `base: "default"` + 빈 `chars`는 **총딜은 기본 스펙과 같지만** 레이어 이탈(헬름의 장탄 옵션 등)이 `deviated`에서 빠진다.
- `warnings` 첫머리에 프로필 머리줄이 실린다. `base: "ungrown"`이면 미육성으로 계산한 멤버가 `spec.profile.ungrown`과
  `warnings`에 함께 실린다.

---

## 니케별 운용 — `chars`

모두 **운용**이라 육성 프로필이 아니라 호출자 오버라이드(3층)로 들어간다 — 그 스쿼드에서만 다르게 굴리는 값이다
(`docs/HARNESS.md §3층`). 어휘는 회귀 하네스 스쿼드(`runner/snapshot.py` `SQUADS`)의 `chars`와 같다. 아무것도 주지
않으면 니케별 기본 레이어(`data/char_defaults.json`)의 컨트롤만 켜져 있고, 큐브는 전원 기본 스펙 큐브다.

| 키 | 값 |
|---|---|
| `control` | control dict — 형식의 정본은 `docs/CONTROL.md §설정 스키마`. 축(`click`·`tap_fire`·`hold`·`reload`·`cover`·`burst`·`aim`·`sequence`·`priority`) 하나를 `false`로 주면 그 축을 끈다 |
| `cube` | 큐브 이름, 또는 `{"name", "level": 1~15}`. 레벨 생략 = 아래 층 값(기본 스펙 15) |
| `weapon_mode_swap` | bool — 수동 재장전으로 무기 변경 모드에 진입시킨다(CLI `--mode-swap`) |
| `favorite_stage` | 0~3 — 애장품 단계(CLI `--favorite`). 육성 프로필의 같은 키보다 위 층이다 |

### `control`

- **기본 레이어 위에 병합된다**(dict는 재귀 병합, 리스트·값은 교체). 문자열 옵션(`--tap`·`--reload-ctrl`…)이 만드는
  dict와 **같은 것**이라, 같은 내용이면 총딜도 같다.
- **좌클릭은 한 덩어리다.** `click`과 종전 키(`tap_fire`·`hold`)는 같은 버튼의 두 표기라, 한쪽 표기로 좌클릭을 주면
  레이어·조건부 규칙이 붙인 다른 표기는 버린다(`spec.merge_over()`). 엄폐·장전컨 같은 다른 축은 그대로 남는다.
- **택틱(`operation.tactics`)보다 위다.** 택틱은 컨트롤 다발의 기본값이라, 같은 니케에 `control`을 주면 그 위에 병합돼
  명시한 값이 이긴다.
- `burst.pattern`은 패턴 이름(`data/char_defaults.json`의 그 니케 `_burst_patterns`) 또는 **사이클 목록**(`[1, 3, 5]`,
  1부터 — 카탈로그 없이 직접)이다. `false` = 패턴 없음. `burst.delay`는 차례가 온 뒤 기다릴 초.
- 레이어 컨트롤을 통째로 지우려면 그 니케를 `operation.auto`에 넣는다 — 그러면 `control`만 남는다.

### `cube`

- 이름의 정본은 `data/base_stat_tables/cube.json`이다. **모르는 이름과 효과 모델이 없는 큐브(`unsupported`)는 오류다** —
  계산기는 그런 큐브의 고유 효과를 조용히 건너뛰므로 입구에서 끊는다(`spec.check_cube()`). 인라인 육성 프로필의
  `cube` 키로 들어와도 같은 두 표기를 받고 같은 검사를 지난다.

### 결과에서 — `control_detail`

`control` 한 줄의 원본 값이다(`SimLog.control_occupancy()`). 판단은 이 칸으로 한다.

| 칸 | 형 | 뜻 |
|---|---|---|
| `mode` · `camera_mode` | str | 적용된 조작 모드(`solo`·`warn`·`strict`)와 카메라 모드(`single`·`shared`) |
| `by_char` | dict | 니케 → 조작한 총 시간(초). 배치 순서, 조작한 니케만 |
| `max_concurrent` | int | 같은 시각에 조작한 최대 인원. 2 이상이면 카메라가 하나라는 제약을 어긴 **비현실적 상한**이다 — 기본 `solo`는 겹치면 급한 쪽만 남기므로 1을 넘지 않고, `warn`에서 나온다(`docs/CONTROL.md §조작자는 한 명`) |
| `overlap_t` | float | 2명 이상이 동시에 조작한 총 시간(초) |
| `overlaps` | list | `[{chars: [A, B], t}]` — 짝별 겹친 시간 |
| `preempted` | dict | 니케 → 조작을 빼앗긴 횟수(`solo` 모드의 등급 조율 — 더 급한 조작에 카메라를 넘겼다) |

---

## 버전

`schema_version`은 **요청·결과 계약 전체**의 판이다(`runner/sim.py` `SCHEMA_VERSION` = `runner/request.py` `VERSION`).

- 칸을 **더하는 것**은 판을 올리지 않는다. 읽는 쪽은 모르는 결과 칸을 무시한다. 요청 칸이 늘어도 옛 요청은 그대로 통한다.
- 칸의 이름·형·뜻을 **바꾸거나 빼면** 판을 올리고 이 문서와 `tests/`를 같이 고친다.
- `warnings`의 문구와 `spec.text`는 사람용이라 판과 무관하게 바뀔 수 있다.
- 딜 수치 자체가 바뀌는 것(계산기 수정)은 형식 변경이 아니다 — 그건 `evaluator.commit`이 가른다.

### 1 → 2

요청 v1은 배치 줄의 키가 CLI 옵션 이름이었다(`"expected"`·`"no-burst"`·`"tap": ["프리카:4.0"]`). v2는 그 자리를 §요청의
구조로 옮겼다 — 사람용 문자열 표기(`"이름:값:값"`)는 요청에 더 이상 없다. v1 키를 주면 오류 메시지가 새 자리를 알려
준다(`runner/request.py` `_V1_KEYS`).

| v1 배치 키 | v2 |
|---|---|
| `expected` · `seed` · `duration` · `first-burst` · `burst-gauge-mode` · `allow-unparsed` | `sim.*` (snake_case) |
| `enemy-def` · `enemy-code` · `core-px` · `distance` · `has-parts` · `part-break-interval` | `enemy.def` · `.code` · … |
| `camera` · `camera-mode` · `control-mode` · `no-burst` · `auto` · `tactic` | `operation.*` · `operation.tactics[]` |
| `controls: {니케: control}` · `tap` · `click` · `reload-ctrl` · … · `burst-pattern` · `burst-delay` | `chars.<니케>.control` |
| `cube: {니케: 큐브}` · `favorite` · `mode-swap` | `chars.<니케>.cube` · `.favorite_stage` · `.weapon_mode_swap` |
| `profile-level` | `profile_level` |
| `squad: "A,B"` (콤마 문자열) | `squad: ["A", "B"]` (목록만) |

결과 쪽은 오류 객체에 `path`가 더해진 것 말고 같다. CLI 옵션 `--controls`는 빠졌다 — `--request '{"chars": …}'`로 준다.
