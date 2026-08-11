# 스크래퍼 운영 가이드


`scraper/` 파일 관계, 데이터 흐름, 난독화 경로 규칙.

브라우저를 쓰지 않는다. blablalink 프론트엔드가 참조하는 데이터는 전부 공개 CDN의
정적 JSON이고, 난독화된 URL이 평문 경로에서 결정론적으로 계산되므로 HTTP GET만으로 수집한다.

---

## 파일 역할

| 파일 | 역할 |
|------|------|
| `cdn_fetch.py` | CDN 수집기(메인). 캐릭터 목록 확정 → roledata 병렬 수집 → 어댑트 → 이미지 → `parse_nikke.py` |
| `cdn_path.py` | 평문 경로 → 난독화 CDN URL 변환. 프론트엔드 `obfuscatedPath()` 재현 |
| `parse_nikke.py` | `nikke_scraped.json` → `parsed_nikke.json` 변환. 단독 실행 가능 |
| `nikke_scraped.json` | 수집기 출력(원시 데이터). 파싱 입력 소스 |
| `preview_skills.json` | 출시 전 카드 이미지 전사본(수동). 같은 스키마, `values`는 레벨 10만 |
| `preview_diff.py` | 프리뷰 원문 ↔ 스크랩 원문 대조. char-add 단계 R에서 실행 |

---

## 사용법

```bash
python scraper/cdn_fetch.py            # 전량 수집 + 이미지 + parse_nikke
python scraper/cdn_fetch.py --check    # 수집 후 기존 파일과 diff만 출력 (쓰기 없음)
python scraper/cdn_fetch.py --ids 601,602   # 특정 resource_id(숫자)만 (기존 파일에 병합)
python scraper/cdn_fetch.py --force-images  # 이미지 전부 다시 받기
```

### 신캐 출시 / 기존 캐릭 스킬 업데이트 — 이게 정문이다

**이름·ID를 몰라도 된다.** 유저가 "신캐 나왔어" / "OO 스킬 바뀐 것 같아"라고만 해도:

```bash
python scraper/cdn_fetch.py --check   # ① 무엇이 신규/변경인지 먼저 확인 (쓰기 없음, 수 초)
python scraper/cdn_fetch.py           # ② 반영 (전량 재수집 + 누락 이미지 자동 채움)
```

`--check`는 `character_id_map.json`으로 현재 전체 캐릭터를 확정하고 각 roledata를 받아
기존 `nikke_scraped.json`과 비교해 **신규 / 변경(필드별) / 삭제**를 출력한다. 이름·ID
브루트포스가 필요 없다. 전량 수집이 수 초라 부분 수집을 고민할 이유가 거의 없다.

`--ids`는 **숫자 resource_id를 이미 알 때만** 쓰는 최적화다(이름을 넣으면 전량 수집을
안내하고 종료한다). 이름→id를 값싸게 조회할 인덱스가 CDN에 없기 때문 — 완전한 이름
소스는 roledata 전량뿐이고, 그건 곧 전량 수집이다.

스킬 텍스트만 바뀐 경우 `parsed_skills.json`은 자동 갱신되지 않는다(그건 에이전트의 파싱 단계,
`PARSING.md` 절차). `--check`로 변경된 캐릭터를 확인한 뒤 **파일 두 개로** 나눈다:

| `parsed_skills.json`에 키 | `preview_skills.json`에 키 | 판정 | 다음 |
|---|---|---|---|
| 있음 | 없음 | 정식 등록 완료 — 텍스트가 바뀌었으면 재검토 | 아래 ① |
| 있음 | **있음** | 프리뷰로 선행 등록 — 카드 기준 추정값이다 | 아래 ② (단계 R) |
| 없음 | 없음 | 미등록 신캐 | 아래 ③ |

프리뷰였는지 아닌지는 **오직 `preview_skills.json`에 키가 있는가**로 갈린다.
정식 등록이 끝나면 그 키가 사라지므로, 같은 캐릭터가 나중에 다시 바뀌어도 ①로 들어온다.

① **이미 등록된 캐릭터** — 변경이 아무리 사소해 보여도(공백·표기 통일·태그 추가 등)
  **재검토 대상이다**. 에이전트가 자체 판단으로 "영향 없음"이라 결론짓고 넘어가지 않는다.
  변경된 텍스트를 유저에게 그대로 보여주고, 재파싱 여부는 유저가 결정한다.
  사소해 보이는 문구가 실제로는 발동 조건·타이밍 변경인 경우가 있다
  (예: `명중 시` → `공격 시`, `사용 후` → `사용 시`).
② **프리뷰로 선행 등록된 캐릭터** — `--check`가 "신규"로 보고하지만 `parsed_skills.json`에는
  이미 키가 있다. 신규도 변경도 아닌 **정식 등록 대상**이다.
  `char-add` 단계 R(`../char-add/PREVIEW.md`)로 간다. 카드 표기가 CDN 키와 달라(콜론 간격·부제)
  프리뷰 이름이 신규 목록에 없을 수 있으므로, 프리뷰 항목이 남아 있으면 신규 목록과 대조한다.
  스스로 판정하지 않아도 된다 — `python -m context.doclint` 검사 G가 대상과 다음 명령을 알려준다.

③ **미등록 캐릭터** — `char-add`에서 호출했다면 단계 1(시나리오 초안)로 진행한다. 독립 raw 갱신 요청이면 여기서 끝낸다.

---

## 데이터 흐름

```
cdn_fetch.py
  → CDN roledata/{resource_id}-v2-ko.json (캐릭터당 완결 JSON)
  → nikke_scraped.json (원시 데이터, 기존 스키마로 어댑트)
  → parse_nikke.py
    → data/parsed_nikke.json (무기 스펙, 버스트 단계, 쿨다운)

스킬 파싱 (char-add 단계 2, PARSING.md 절차)
  → data/parsed_skills.json
```

`nikke_scraped.json`은 `parsed_nikke.json` 생성과 char-add의 스킬 파싱 입력에만 쓰임.
계산기는 참조하지 않음.

---

## 난독화 경로 규칙 (`cdn_path.py`)

프론트엔드 `index-*.js`의 `obfuscatedPath()`와 동일:

- **디렉토리 세그먼트** → djb2 해시(고정 소수 `LARGE_PRIMES`) 기반 `xx-99` 토큰
- **파일명** → `md5(평문 전체 경로)` + 원래 확장자
- CDN 베이스: `https://sg-tools-cdn.blablalink.com`

주요 평문 경로:

| 평문 경로 | 내용 |
|-----------|------|
| `/character/character_id_map.json` | 전체 캐릭터 resource_id 목록 |
| `/roledata/{rid}-v2-ko.json` | 캐릭터 1명 완결 데이터(무기·스킬·스탯) |
| `/character/mi/mi_c{rid:03d}_00_s.webp` | 256×512 썸네일 |

**리스크:** 사이트가 난독화 상수(소수·djb2·locale)를 바꾸면 URL이 깨진다.
그때는 전량 404로 즉시 드러나므로, JS 번들에서 `LARGE_PRIMES`·`generateTwoLetterHash`·
`createNormalObfuscatedPath`를 다시 추출해 `cdn_path.py`를 맞춘다.

---

## 어댑터 매핑 (`cdn_fetch.py`)

roledata(영문 enum) → 기존 `nikke_scraped.json` 한국어 스키마:

- `element` → 속성(`Water`→수냉 등), `class` → 클래스, `corporation` → 기업, `use_burst_skill` → 버스트 단계
- **스쿼드**: `squad`(영문 코드) → `스쿼드`, `squad_detail.squad_name` → `스쿼드명`.
  `parse_nikke.py`가 `squad` / `squad_name`으로 넘긴다. **판정의 정본은 코드**다 —
  표시명은 `-`인 경우가 있어(777 = 블랑·누아르) 그때는 코드로 대체해 넣는다.
  의상·복각 버전도 원본과 같은 스쿼드일 수 있다(라피 : 레드 후드 = `Counters`).
  전 캐릭터가 스쿼드를 가지며(빈 값 없음) 현재 62종. 더미 `test_B*`에는 필드가 없다.
- **발사 메카닉**: `shot_detail`의 `rate_of_fire` / `end_rate_of_fire` /
  `rate_of_fire_change_pershot` / `shot_count` / `muzzle_count`를 CDN 원값(rpm·개수)
  그대로 `무기상세`에 담고, `parse_nikke.py`가 `/60` 해서 `fire_rate`(초당 발수) ·
  `pellets` · `muzzles`로 변환한다. `fire_rate_max` / `fire_rate_change_pershot`은
  시작값과 다를 때만 기록되므로 실질 MG 전용이다.
  **총구 수는 히트 수 배수다** — 1회 발사 히트 수 = `pellets × muzzles`
  (예: 츠바이 5 × 2 = 10). 상세는 `DATA_VERIFY.md` §총구 수
- 스킬 텍스트: `description_localkey`의 `{description_value_NN}` 플레이스홀더에 `description_value_list`의
  레벨별 값을 끼워 레벨 1~10 텍스트 생성 → `build_template()`으로 template/values 압축
- `<color>`·`<word_group>` 태그만 제거(설명문의 리터럴 `<Step N ...>` 텍스트는 보존)

**동명이인 처리:** 게임에 같은 이름 캐릭터가 존재한다(예: SSR 사쿠라 rid282 / SR 사쿠라 rid836).
이름을 키로 쓰므로 등급이 높은 쪽을 보존하고 나머지는 버린다(경고 출력).

**이미지 파일명:** Windows 금지 문자(`/ : * ? " < > |`)를 `_`로 치환. 기존 `image/` 규칙과 동일
(예: `D : 킬러 와이프` → `D _ 킬러 와이프.webp`).

**애장품(favorite item):** `favorite_rare_map.json`의 SSR 목록에 오른 캐릭터만 애장품이
스킬을 바꾼다. `favorite_{id}.json`(`/equip/{locale}/`, 비로그인 공개)을 받아
`icon_resource_id`의 `c###`로 캐릭터에 매핑한다. 해당 캐릭터에만 `"애장품"` 필드 추가
(대상은 신규 애장품 출시마다 늘어난다 — 현재 수는 실행 로그 `애장품: N명 수집`으로 확인):

- `favoriteitem_skill_group_data` = 애장품 1/2/3단계. **배열 순서 = 단계**, 각 항목의
  `skill_change_slot`(1/2/3)이 기존 skill1/skill2/ulti 중 무엇을 교체하는지 나타낸다(캐릭마다 다름).
- 각 단계 스킬 값은 `render_skill()`로 base와 동일하게 template/values 압축.
- `collection_skill_group_data`(소유 시 상시 버프)는 캐릭터 특성이 아니므로 수집하지 않는다.
- `favorite_rare_map`의 R/SR(1xxxxx)은 스탯 전용 인형이라 대상 아님.

애장품 스킬을 계산기에 반영하려면 base 스킬처럼 `parsed_skills.json`에 손파싱해야 한다
(`nikke_scraped.json`의 `애장품` 필드는 raw 소스일 뿐, `parsed_nikke.json`엔 반영 안 됨).

---

## 수동 관리 데이터

`scraper/preview_skills.json`은 출시 전 카드 이미지를 손으로 옮겨 적은 파일이고,
**스크래퍼는 이 파일을 절대 건드리지 않는다** — 수집을 아무리 돌려도 덮어써지지 않는다.
`parse_nikke.py`는 이 파일도 읽어 `parsed_nikke.json` 항목을 만들되 `"preview": true`를 붙이며,
같은 이름이 `nikke_scraped.json`에도 있으면 **스크랩 쪽이 이긴다**(출시 후 자동으로 정본으로 넘어감).
프리뷰 항목의 수명 관리는 `context/doclint.py` 검사 G가 강제한다.

`data/weapon_delays.json`에서 관리. `calculator/timeline.py`가 직접 읽고,
**스크래퍼는 이 파일을 절대 건드리지 않는다** — 여기 적은 값은 수집을 아무리 자주 돌려도
덮어써지지 않는다.

발사 메카닉 값의 해석 순서(3계층, `timeline.py` `_pick`):

| 계층 | 파일 | 성격 |
|---|---|---|
| ① | `weapon_delays.json` `_exceptions[캐릭터]` | 수동 실측 (최우선) |
| ② | `parsed_nikke.json[캐릭터]` | 스크래퍼가 CDN에서 수집 |
| ③ | `weapon_mechanics.json` `weapon_type_defaults` | 무기군 기본값 |

- CDN에 없는 딜레이(`post_fire_delay` / `post_reload_delay` / `cover_during_delay`)는
  ②가 아예 없으므로 ①→③으로 떨어진다.
- **무기 변경 무기**는 CDN에 레코드 자체가 없어 ②가 빈다. 실측 연사속도는
  `weapon_delays.json`의 `_weapon_change[캐릭터][효과이름]`에 적는다 — 무기군 기본값에
  얹어두면 그 기본값이 바뀔 때 소리 없이 함께 바뀐다
  (라플라스 : 얼티밋 히어로 20발/초가 이 경우).
