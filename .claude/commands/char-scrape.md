# char-scrape

신규 캐릭터 스크래핑 및 nikke 데이터 갱신.

$ARGUMENTS: 캐릭터 이름 또는 ID (예: `/char-scrape 아이언메이든`)

작업 전 `context/SCRAPER.md`를 읽는다.

---

## 작업 순서

### 1. 스크래퍼 실행

신규 캐릭터 ID가 알려져 있으면 `rescrape.py`로 해당 ID만 수집한다.
ID 불명이면 `nikke_scraper.py` 전체 재실행.

```bash
python scraper/rescrape.py   # TARGET_IDS에 ID 지정 후
# 또는
python scraper/nikke_scraper.py
```

완료 후 `parse_nikke.py`가 자동 실행되어 `data/parsed_nikke.json`이 갱신된다.
자동 실행되지 않은 경우 수동으로 실행한다:

```bash
python scraper/parse_nikke.py
```

### 2. weapon_delays.json 확인 (SR/RL 무기인 경우)

`parsed_nikke.json`에서 $ARGUMENTS의 무기 유형을 확인한다.

SR 또는 RL이면 유저에게 묻는다:

> "$ARGUMENTS 의 post_fire_delay 와 post_reload_delay 값을 알고 계신가요?
> 기본값은 post_fire_delay=0.215, post_reload_delay=0 입니다.
> 기본값과 다르면 알려주세요."

답변을 받아 기본값과 다른 경우 `data/weapon_delays.json`의 `_exceptions`에 추가한다.

---

## 완료 후

여기서 작업을 멈추고 유저에게 결과를 보고한다.
다음 단계(`/char-parse`)로 진행할지 유저에게 묻는다.
