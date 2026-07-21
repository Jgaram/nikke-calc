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

스킬 텍스트만 바뀐 경우 `parsed_skills.json`은 자동 갱신되지 않는다(그건 Claude 손파싱,
`PARSING.md` 절차). `--check`로 변경된 캐릭터를 확인한 뒤, 해당 캐릭터만 재파싱한다.

---

## 데이터 흐름

```
cdn_fetch.py
  → CDN roledata/{resource_id}-v2-ko.json (캐릭터당 완결 JSON)
  → nikke_scraped.json (원시 데이터, 기존 스키마로 어댑트)
  → parse_nikke.py
    → data/parsed_nikke.json (무기 스펙, 버스트 단계, 쿨다운)

스킬 파싱 (Claude, PARSING.md 절차)
  → data/parsed_skills.json
```

`nikke_scraped.json`은 `parsed_nikke.json` 생성과 (Claude의) 스킬 파싱 입력에만 쓰임.
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
- 스킬 텍스트: `description_localkey`의 `{description_value_NN}` 플레이스홀더에 `description_value_list`의
  레벨별 값을 끼워 레벨 1~10 텍스트 생성 → `build_template()`으로 template/values 압축
- `<color>`·`<word_group>` 태그만 제거(설명문의 리터럴 `<Step N ...>` 텍스트는 보존)

**동명이인 처리:** 게임에 같은 이름 캐릭터가 존재한다(예: SSR 사쿠라 rid282 / SR 사쿠라 rid836).
이름을 키로 쓰므로 등급이 높은 쪽을 보존하고 나머지는 버린다(경고 출력).

**이미지 파일명:** Windows 금지 문자(`/ : * ? " < > |`)를 `_`로 치환. 기존 `image/` 규칙과 동일
(예: `D : 킬러 와이프` → `D _ 킬러 와이프.webp`).

**애장품(favorite item):** `favorite_rare_map.json`의 SSR 목록 17명만 애장품이 스킬을 바꾼다.
`favorite_{id}.json`(`/equip/{locale}/`, 비로그인 공개)을 받아 `icon_resource_id`의 `c###`로
캐릭터에 매핑한다. 캐릭터당 `"애장품"` 필드 추가(17명만):

- `favoriteitem_skill_group_data` = 애장품 1/2/3단계. **배열 순서 = 단계**, 각 항목의
  `skill_change_slot`(1/2/3)이 기존 skill1/skill2/ulti 중 무엇을 교체하는지 나타낸다(캐릭마다 다름).
- 각 단계 스킬 값은 `render_skill()`로 base와 동일하게 template/values 압축.
- `collection_skill_group_data`(소유 시 상시 버프)는 캐릭터 특성이 아니므로 수집하지 않는다.
- `favorite_rare_map`의 R/SR(1xxxxx)은 스탯 전용 인형이라 대상 아님.

애장품 스킬을 계산기에 반영하려면 base 스킬처럼 `parsed_skills.json`에 손파싱해야 한다
(`nikke_scraped.json`의 `애장품` 필드는 raw 소스일 뿐, `parsed_nikke.json`엔 반영 안 됨).

---

## 수동 관리 데이터

`post_fire_delay` / `post_reload_delay` 등 CDN에 없는 딜레이 값은 `data/weapon_delays.json`에서 관리.
`parse_nikke.py` / `parsed_nikke.json`과 무관. `calculator/timeline.py`가 직접 읽음.
