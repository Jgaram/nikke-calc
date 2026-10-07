# 시뮬 결과 JSON — `runner/sim.py --json` · `--batch`

다른 프로그램이 이 레포를 **평가기**로 쓸 때의 입출력 계약이다. 지금 쓰는 쪽은
`Jgaram/nikke-opt`(5덱 편성 최적화기)로, `python -m runner.sim`을 서브프로세스로 수천~수만 번
띄워 결과를 읽는다. 사람이 읽는 `--view` 텍스트 출력은 이 레포의 디버깅·문서용으로 그대로 남는다.

**이 문서가 형식의 정본이다.** 칸을 바꾸면 `schema_version`을 올리고 이 문서를 같은 커밋에서 고친다
(§버전). 계약 검사는 `tests/test_sim_json.py`다:

```bash
python -m unittest discover -s tests -v
```

---

## 부르는 법

```bash
# 한 건 — stdout에 JSON 객체 하나 (한 줄)
python -m runner.sim "리틀 머메이드,크라운,라피 : 레드 후드,미하라,헬름" --expected --boss "솔로 레이드 S40" --json

# 여러 건 — stdin JSON Lines → stdout JSON Lines
python -m runner.sim --batch < 요청.jsonl
```

`--json`은 텍스트 모드의 옵션을 전부 그대로 받는다(`--boss`·`--tap`·`--profile`…). 받지 않는 건
`--view`·`--char` 둘뿐이다 — 텍스트 표시 전용이라, 주면 오류다.

`--boss`는 텍스트 모드와 같이 프리셋 이름이나 `.json` 파일 경로를 받고, 덧붙여 **`{`로 시작하는
문자열은 인라인 보스 스크립트**로 읽는다(형식은 파일과 같다 — `runner/boss.py` 모듈 docstring).

### 보장

| | |
|---|---|
| **총딜 동일** | 같은 입력이면 `total_damage`가 텍스트 출력의 `스쿼드 총 딜`과 **정확히** 같다. 둘은 같은 `prepare()` → `execute()`를 거치고 출력만 다르다 |
| **stdout은 JSON뿐** | 경고·이탈 보고·진행 메시지는 JSON 안의 칸으로 들어간다. 시뮬 도중 다른 코드가 `print`를 해도 stderr로 돌린다 |
| **병렬 안전** | 결과를 어디에도 쓰지 않는다. 평가기 버전을 잴 때도 `git --no-optional-locks`라 `.git/index`를 건드리지 않는다. 프로세스를 동시에 여러 개 띄워도 된다 |
| **결정론** | `"expected": true`(기대값 모드)나 `seed`를 주면 프로세스·순서와 무관하게 같은 값이다. 둘 다 없으면 매번 다르다(`warnings`에 실린다). 배치에서 앞 줄이 뒷줄 결과를 바꾸지 않는다 |

시뮬 한 번이 수 초(180초 5인 기준 5초 안팎)라 비용은 거의 시뮬 자체다. 배치는 파이썬 시작·import
몫(0.2초 남짓)을 아낀다. 많이 돌릴 땐 배치 프로세스 여러 개를 병렬로 띄운다.

---

## 결과 객체

```jsonc
{
  "schema_version": 1,
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
  "warnings": ["기본 스펙(1층) 이탈 1명 — 헬름"],
  "evaluator": {"repo": "Jgaram/nikke-calc", "commit": "e3017db…", "dirty": false}
}
```

| 칸 | 형 | 뜻 |
|---|---|---|
| `schema_version` | int | 이 형식의 판. 지금 `1` |
| `total_damage` | int | 스쿼드 총딜 = 보스(파츠 포함)에 들어간 딜. 텍스트 `스쿼드 총 딜`과 같은 값 |
| `members` | list | **배치 순서대로** `{name, damage, share}`. `damage`는 int, `share`는 `damage / total_damage`(0~1 비율, 퍼센트 아님). 총딜이 0이면 0.0 |
| `duration` | float | 실제로 돈 시뮬 시간(초) |
| `expected` | bool | 기대값 모드(`--expected`) 여부 |
| `seed` | int \| null | 넘긴 시드. 기대값 모드에선 결과에 영향이 없다 |
| `boss` | dict | **실제로 적용된 적 dict** — 기본 적(`timeline.DEFAULT_ENEMY`) 위에 프리셋·스크립트·`--enemy-def` 등을 얹고 `skill`·`part` 참조까지 전개한 최종값 |
| `boss_label` | str \| null | 보스를 바꿨을 때 그 이름(프리셋·파일·`인라인 스크립트 (프리셋 …)`). 기본 적이면 null |
| `spec` | dict | 적용된 스펙 레이어 요약 — §spec |
| `control` | str \| null | 조작 요약 한 줄(텍스트 출력의 `조작 구간: …`). 조작이 없으면 null |
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
| `profile` | dict \| null | `--profile`을 썼으면 `{name, level_mode}` |
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
{"schema_version": 1, "error": {"type": "invalid_input", "exception": "ValueError", "message": "parsed_nikke.json에 없는 캐릭터: ['없는 니케'] …"}}
```

| `type` | 종료 코드 | 언제 |
|---|---|---|
| `invalid_input` | 2 | 모르는 니케 이름·스킬 미파싱·잘못된 보스·잘못된 옵션 값·없는 프로필 등. 텍스트 모드가 메시지만 찍고 코드 2로 끝나던 경우와 같다 |
| `internal_error` | 1 | 그 밖의 예외 — 평가기 버그다. stderr에 트레이스백 |

`exception`은 파이썬 예외 이름이고 `message`는 사람용 문구다. 판단은 `type`으로 한다.

---

## 배치 모드 — `--batch`

stdin을 한 줄씩 읽어 줄마다 결과(또는 오류) 객체 한 줄을 stdout에 낸다. 줄마다 flush하므로
파이프로 흘려 읽어도 된다. 빈 줄은 건너뛴다.

```jsonl
{"id": "s1", "squad": ["리틀 머메이드","크라운","라피 : 레드 후드","미하라","헬름"], "expected": true, "boss": {"preset": "솔로 레이드 S40"}}
{"squad": "크라운,헬름", "seed": 7, "duration": 60, "no-burst": "크라운", "tap": ["앨리스:4.0"]}
```

**키는 CLI 옵션 이름이다** — `no-burst`·`no_burst` 둘 다 받는다. 값의 형:

| 옵션 종류 | JSON 값 | 예 |
|---|---|---|
| `squad` | 이름 목록 또는 콤마 문자열 | `["크라운","헬름"]` · `"크라운,헬름"` |
| `boss` | 프리셋 이름·`.json` 경로 문자열, 또는 **인라인 스크립트 dict** | `{"preset": "솔로 레이드 S40", "patterns": [...]}` |
| 스위치(`expected`·`has-parts`·`allow-unparsed`) | bool | `true` |
| 숫자(`seed`·`duration`·`enemy-def`…) | 수 (문자열 `"30"`은 거절) | `60` |
| 반복 옵션(`tap`·`click`·`reload-ctrl`·`tactic`…) | 문자열 하나 또는 목록 — CLI에 준 문자열 그대로 | `["프리카:4.0:0.03:0:burst_charge"]` |
| `auto` | `true`(전원) · 이름 · 이름 목록 | `true` |
| 그 밖의 문자열 옵션 | 문자열 | `"enemy-code": "철갑"` |
| 아무 옵션이나 | `null` = 기본값으로 되돌린다 | |

- **`id`**(아무 JSON 값)는 결과에 그대로 돌려준다. 모든 출력 줄에는 입력 줄 번호 **`line`**(1부터, 빈 줄 포함해 센다)이 붙는다.
- `--batch`와 함께 준 다른 CLI 옵션은 **모든 줄의 기본값**이다. 줄에 같은 키가 있으면 줄이 이긴다(반복 옵션도 합치지 않고 바꾼다).
- 모르는 키, 형이 맞지 않는 값, JSON이 아닌 줄, 객체가 아닌 줄은 **그 줄만** 오류 객체가 되고 다음 줄로 넘어간다.
- 모든 줄을 처리하면 종료 코드 0이다(실패한 줄이 있어도). 0이 아닌 코드는 배치 자체를 시작하지 못했을 때뿐이다 — 위치 인자로 스쿼드를 줬거나 `--view`를 같이 준 경우 등. 그때도 stdout에 오류 객체 한 줄을 낸다.

---

## 버전

- 칸을 **더하는 것**은 판을 올리지 않는다. 읽는 쪽은 모르는 칸을 무시한다.
- 칸의 이름·형·뜻을 **바꾸거나 빼면** `schema_version`(`runner/sim.py`의 `SCHEMA_VERSION`)을 올리고
  이 문서와 `tests/test_sim_json.py`를 같이 고친다.
- `warnings`의 문구와 `spec.text`는 사람용이라 판과 무관하게 바뀔 수 있다.
- 딜 수치 자체가 바뀌는 것(계산기 수정)은 형식 변경이 아니다 — 그건 `evaluator.commit`이 가른다.
