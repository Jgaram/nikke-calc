---
name: char-scrape
description: 신규 캐릭터 스크래핑·기존 캐릭 스킬 업데이트·nikke 게임 데이터 갱신. 유저가 '신캐 나왔어' / 'OO 스킬 바뀐 것 같아'처럼 게임 데이터 변경을 알릴 때도 사용.
---

# char-scrape

신규 캐릭터 스크래핑·기존 캐릭 업데이트·nikke 데이터 갱신.

**인자**: 캐릭터 이름 또는 숫자 resource_id. 비어 있어도 된다 (전량 수집이 기본).

작업 전 `.claude/skills/char-scrape/SCRAPER.md` 읽는다.

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

`parsed_nikke.json`에서 대상 캐릭터의 무기 유형 확인.

SR 또는 RL이면 유저에게 묻는다:

> "<캐릭터명>의 post_fire_delay 와 post_reload_delay 값을 알고 계신가요?
> 기본값은 post_fire_delay=0.215, post_reload_delay=0 입니다.
> 기본값과 다르면 알려주세요."

기본값과 다르면 `data/weapon_delays.json` `_exceptions`에 추가.

---

## 완료 후

작업 멈추고 유저에게 결과 보고.

스킬 텍스트가 신규·변경된 캐릭터가 있으면 **`char-add` 스킬(단계 1 — 시나리오 초안)** 진행
여부를 묻는다. 스크래핑만으로는 계산기에 반영되지 않는다 (`parsed_skills.json`은 자동 갱신 안 됨).
