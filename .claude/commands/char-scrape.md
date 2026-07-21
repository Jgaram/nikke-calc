# char-scrape

신규 캐릭터 스크래핑·기존 캐릭 업데이트·nikke 데이터 갱신.

$ARGUMENTS: 캐릭터 이름 또는 숫자 resource_id (예: `/char-scrape 아이언메이든`). 비어 있어도 됨.

작업 전 `context/SCRAPER.md` 읽는다.

---

## 작업 순서

### 1. 수집기 실행

**이름·ID를 몰라도 된다.** 전량 수집이 수 초이고, 이름→id 인덱스는 CDN에 없으므로
숫자 rid를 확실히 알 때만 `--ids`를 쓴다(이름을 넣으면 전량을 안내하고 종료).

```bash
python scraper/cdn_fetch.py --check     # ① 무엇이 신규/변경인지 확인 (쓰기 없음)
python scraper/cdn_fetch.py             # ② 반영 (전량, 누락 이미지 자동)
# 숫자 rid를 아는 경우만:
python scraper/cdn_fetch.py --ids 601   # 해당 캐릭터만 수집 후 기존 파일에 병합
```

②에서 `parse_nikke.py` 자동 실행 → `data/parsed_nikke.json` 갱신, 누락 이미지 자동 다운로드.

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
