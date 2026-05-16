# 스크래퍼 운영 가이드

`scraper/` 디렉토리의 파일 관계, 수동 관리 필드, 데이터 흐름을 정리한다.

---

## 파일 역할

| 파일 | 역할 |
|------|------|
| `nikke_scraper.py` | ID 0~870 전체 순회 크롤러. 완료 후 `parse_nikke.py` 자동 실행 |
| `rescrape.py` | `TARGET_IDS` 리스트에 지정한 ID만 재수집. 완료 후 `parse_nikke.py` 자동 실행 |
| `parse_nikke.py` | `nikke_scraped.json` → `parsed_nikke.json` 변환. 단독 실행 가능 |
| `extract_session.py` | 브라우저 창을 열어 로그인 후 세션 추출 → `ls_data.json` + `cookies.json` 생성 |
| `nikke_scraped.json` | 크롤 원시 데이터 (스크래퍼 출력). 파싱 입력 소스 |
| `collected_ids.json` | `KNOWN_IDS` 이후 새로 수집된 ID 누적 목록. `KNOWN_IDS`와 합산하여 skip 처리 |

`nikke_scraper.py`는 비로그인이 기본값. 로그인 필요 시 코드 내 주석 처리된 `inject_session` 줄을 해제.

---

## 데이터 흐름

```
nikke_scraper.py / rescrape.py
  → nikke_scraped.json (원시 데이터)
  → parse_nikke.py
    → data/parsed_nikke.json (무기 스펙, 버스트 단계, 쿨다운)

스킬 파싱 (Claude, PARSING.md 절차)
  → data/parsed_skills.json
```

`nikke_scraped.json`은 `parsed_nikke.json` 생성에만 쓰인다. 계산기는 참조하지 않는다.

---

## 수동 관리 필드

스크래핑으로 수집할 수 없는 타이밍 값은 `nikke_scraped.json`을 직접 수정해 관리한다.
`parse_nikke.py`는 해당 필드가 존재하면 `parsed_nikke.json`에 그대로 전달한다.
스크래핑 시 기본값 `0`으로 자동 생성되므로, 기본값과 다른 캐릭터만 수동으로 수정한다.

### `post_fire_delay` (발사 후 딜레이, 단위: 초)

SR/RL 차지형 무기에서 발사 직후부터 다음 차지 시작까지의 딜레이.
기본값은 `weapon_mechanics.json`의 `weapon_type_defaults`에 정의되어 있으며, 캐릭터에 이 필드가 있으면 기본값을 대신한다.

| 캐릭터 | 값 |
|--------|-----|
| 아니스 : 스타 | 0.0 |
| 리버렐리오 | 0.0 |
| 네온 : 비전 아이 | 0.0 |
| 신데렐라 | 0.33 |
| 홍련 : 흑영 | 0.43 |

### `post_reload_delay` (재장전 후 딜레이, 단위: 초)

재장전 완료 후 첫 발사까지의 딜레이. 실측값이 있는 캐릭터만 수동으로 수정한다.
