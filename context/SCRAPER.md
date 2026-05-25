# 스크래퍼 운영 가이드

`scraper/` 파일 관계, 수동 관리 필드, 데이터 흐름.

---

## 파일 역할

| 파일 | 역할 |
|------|------|
| `nikke_scraper.py` | ID 0~870 전체 순회 크롤러. 완료 후 `parse_nikke.py` 자동 실행 |
| `rescrape.py` | `TARGET_IDS` 리스트에 지정한 ID만 재수집. 완료 후 `parse_nikke.py` 자동 실행 |
| `parse_nikke.py` | `nikke_scraped.json` → `parsed_nikke.json` 변환. 단독 실행 가능 |
| `extract_session.py` | 브라우저 창을 열어 로그인 후 세션 추출 → `ls_data.json` + `cookies.json` 생성 |
| `nikke_scraped.json` | 크롤 원시 데이터 (스크래퍼 출력). 파싱 입력 소스 |
| `collected_ids.json` | `KNOWN_IDS` 이후 새로 수집된 ID 누적 목록. `KNOWN_IDS`와 합산해 skip 처리 |

`nikke_scraper.py`는 비로그인 기본값. 로그인 필요 시 코드 내 주석 처리된 `inject_session` 줄 해제.

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

`nikke_scraped.json`은 `parsed_nikke.json` 생성에만 쓰임. 계산기는 참조하지 않음.

---

## 수동 관리 데이터

`post_fire_delay` / `post_reload_delay` 등 스크래핑으로 수집 불가한 딜레이 값은 `data/weapon_delays.json`에서 관리. `parse_nikke.py` / `parsed_nikke.json`과 무관. `calculator/timeline.py`가 직접 읽음.
