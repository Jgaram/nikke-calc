# char-scrape

신규 캐릭터 스크래핑·nikke 데이터 갱신.

$ARGUMENTS: 캐릭터 이름 또는 ID (예: `/char-scrape 아이언메이든`)

작업 전 `context/SCRAPER.md` 읽는다.

---

## 작업 순서

### 1. 수집기 실행

ID(resource_id) 알면 `--ids`로 해당 캐릭터만 수집(기존 파일에 병합). ID 불명이면 전량 수집.

```bash
python scraper/cdn_fetch.py --ids 601   # 특정 resource_id
# 또는
python scraper/cdn_fetch.py             # 전량 (수 초)
```

완료 후 `parse_nikke.py` 자동 실행 → `data/parsed_nikke.json` 갱신, 누락 이미지 자동 다운로드.
바뀐 캐릭터만 미리 확인하려면 `python scraper/cdn_fetch.py --check` (쓰기 없이 diff만).

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
