---
name: char-scrape
description: 캐릭터 등록 없이 NIKKE raw 게임 데이터만 갱신한다. scraper 상태 확인, 기존 데이터 refresh, 특정 resource_id 재수집에 사용한다. 신규 캐릭터 추가·계산기 반영은 char-add를 사용한다.
---

# char-scrape

등록 workflow와 무관한 데이터 갱신만 수행한다.

1. `SCRAPER.md`를 읽는다.
2. `python scraper/cdn_fetch.py --check`로 변경을 확인한다.
3. 사용자 요청 범위에 맞게 전량 또는 확정된 숫자 resource_id만 반영한다.
4. 변경 결과를 보고하고 멈춘다.

신규 캐릭터 등록이나 기존 캐릭터 재파싱·재구현이 필요하면 `char-add`를 제안한다.