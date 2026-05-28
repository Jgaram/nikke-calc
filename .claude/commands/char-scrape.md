# char-scrape

신규 캐릭터 스크래핑·nikke 데이터 갱신.

$ARGUMENTS: 캐릭터 이름 또는 ID (예: `/char-scrape 아이언메이든`)

작업 전 `context/SCRAPER.md` 읽는다.

---

## 작업 순서

### 1. 스크래퍼 실행

ID 알면 `rescrape.py`로 해당 ID만 수집. ID 불명이면 `nikke_scraper.py` 전체 재실행.

```bash
python scraper/rescrape.py   # TARGET_IDS에 ID 지정 후
# 또는
python scraper/nikke_scraper.py
```

완료 후 `parse_nikke.py` 자동 실행 → `data/parsed_nikke.json` 갱신.
자동 실행 안 된 경우 수동 실행:

```bash
python scraper/parse_nikke.py
```

### 2. weapon_delays.json 확인 (SR/RL 무기인 경우)

`parsed_nikke.json`에서 $ARGUMENTS 무기 유형 확인.

SR 또는 RL이면 유저에게 묻는다:

> "$ARGUMENTS 의 post_fire_delay 와 post_reload_delay 값을 알고 계신가요?
> 기본값은 post_fire_delay=0.215, post_reload_delay=0 입니다.
> 기본값과 다르면 알려주세요."

기본값과 다르면 `data/weapon_delays.json` `_exceptions`에 추가.

---

## 완료 후

작업 멈추고 유저에게 결과 보고. 다음 단계(`/char-scenario` 초안 모드 — 원본 스킬 텍스트로 메카닉 이해·검증 스쿼드 결정) 진행 여부 묻는다.
