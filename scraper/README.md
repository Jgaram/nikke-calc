# NIKKE 스킬 스크래퍼

## 파일 구성

### 실행 파일
| 파일 | 설명 |
|------|------|
| `nikke_scraper.py` | 메인 스크래퍼. ID 0~870 전체 순회하며 캐릭터 데이터 수집. 완료 후 `parse_nikke.py` 자동 실행 |
| `rescrape.py` | 특정 ID만 재수집할 때 사용. `TARGET_IDS` 리스트에 번호 입력 후 실행. 완료 후 `parse_nikke.py` 자동 실행 |
| `parse_nikke.py` | `nikke_scraped.json`의 무기상세 파싱 → `parsed_nikke.json` 생성. 단독 실행 가능 |

### 데이터 파일
| 파일 | 위치 | 설명 |
|------|------|------|
| `nikke_scraped.json` | `scraper/` | 수집된 전체 캐릭터 데이터 (스크래퍼 출력) |
| `parsed_nikke.json` | `../data/` | 캐릭터별 무기 스펙 파싱 결과 (`parse_nikke.py` 출력) |
| `collected_ids.json` | `scraper/` | 코드 내 `KNOWN_IDS` 이후 새로 수집된 ID 누적 목록. `KNOWN_IDS`와 합산하여 skip |

### 로그인 관련 파일 (로그인 시에만 존재)
| 파일 | 설명 |
|------|------|
| `ls_data.json` | 로그인 localStorage 데이터 (`extract_session.py`로 추출) |
| `cookies.json` | 로그인 쿠키 데이터 (`extract_session.py`로 추출) |

### 로그 파일 (자동 생성)
| 파일 | 설명 |
|------|------|
| `scraper_log.txt` | nikke_scraper.py 실행 로그 |
| `rescrape_log.txt` | rescrape.py 실행 로그 |

### 로그인 관련 파일
| 파일 | 설명 |
|------|------|
| `extract_session.py` | 브라우저 창을 열어 로그인 후 세션 자동 추출. `ls_data.json` + `cookies.json` 생성 |

---

## 실행 방법

### 처음 설치
```bash
pip install playwright
playwright install chromium
```

### 전체 수집 (첫 실행 또는 신규 캐릭터 확인)
```bash
python nikke_scraper.py > scraper_log.txt 2>&1
```
- 코드 내 `KNOWN_IDS` + `collected_ids.json` 합산 목록에 없는 ID만 탐색
- 캐릭터 발견 시 즉시 `nikke_scraped.json`에 저장 (중단 후 재실행 가능)
- 완료 후 `parsed_nikke.json` 자동 생성

### 특정 ID 재수집 (로그인 데이터 반영 등)
1. `rescrape.py` 열어서 `TARGET_IDS` 리스트에 원하는 ID 입력
2. `python rescrape.py` 실행 — **로그인 상태로 실행** (기본값)
3. 기존 데이터 덮어쓰기
4. 완료 후 `parsed_nikke.json` 자동 갱신 (전체 캐릭터 기준)

### 무기 파싱만 단독 실행
```bash
python parse_nikke.py
```
`nikke_scraped.json`이 이미 있을 때 `parsed_nikke.json`만 재생성.

> `nikke_scraper.py`는 비로그인이 기본값. 로그인이 필요하면 코드 내 주석 처리된 `inject_session` 줄을 해제.

---

## 로그인 세션 갱신 방법
```bash
python extract_session.py
```
1. 브라우저 창이 열리면 blablalink.com에 로그인
2. 터미널로 돌아와 Enter
3. `ls_data.json`(localStorage) + `cookies.json`(쿠키) 자동 저장

세션 만료 시 위 과정 반복.

---

## JSON 데이터 구조

```json
"캐릭터명": {
  "id": 10,
  "레어도": "SSR",
  "스쿼드": "...",
  "속성": "작열",
  "클래스": "화력형",
  "기업": "엘리시온",
  "버스트 단계": "1",
  "무기상세": {
    "무기유형": "AR",
    "최대 장탄 수": "180",
    "재장전 시간": "1.5초",
    "조작 타입": "자동",
    "무기스킬": "..."
  },
  "스킬": {
    "스킬명": {
      "쿨타임": null,
      "template": "공격력 {0}% 증가",
      "values": {
        "1": ["10"],
        "2": ["11"],
        ...
      }
    }
  },
  "post_fire_delay": 0,
  "post_reload_delay": 0
}
```

---

## 수동 관리 필드

스크래핑으로는 수집할 수 없는 타이밍 값은 `nikke_scraped.json`을 직접 수정하여 관리한다.
`parse_nikke.py`는 해당 필드가 존재할 경우 `parsed_nikke.json`에 그대로 전달한다.

### `post_fire_delay` (발사 후 딜레이, 단위: 초)

SR/RL 차지형 무기에서 발사 직후부터 다음 차지 시작까지의 딜레이.
기본값은 `weapon_mechanics.json`의 `weapon_type_defaults`에 정의되어 있으며,
캐릭터에 이 필드가 있으면 기본값을 대신한다.

스크래핑 시 기본값 `0`으로 자동 생성됨 — 기본값과 다른 캐릭터는 수동으로 수정할 것.

| 캐릭터 | 값 |
|--------|-----|
| 아니스 : 스타 | 0.0 |
| 리버렐리오 | 0.0 |
| 네온 : 비전 아이 | 0.0 |
| 신데렐라 | 0.33 |
| 홍련 : 흑영 | 0.43 |

### `post_reload_delay` (재장전 후 딜레이, 단위: 초)

재장전 완료 후 첫 발사까지의 딜레이.
스크래핑 시 기본값 `0`으로 자동 생성됨 — 실측값이 있는 캐릭터는 수동으로 수정할 것.
