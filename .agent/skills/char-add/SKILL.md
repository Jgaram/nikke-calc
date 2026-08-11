---
name: char-add
description: 신규 캐릭터를 계산기에 처음부터 끝까지 등록하거나 기존 캐릭터를 재파싱·재구현한다. 신캐 출시·추가 요청에는 데이터 수집부터 시나리오, 스킬 파싱, 계산기 구현, 검증까지 이 skill을 사용한다.
---

# char-add

캐릭터 등록의 전체 workflow를 맡는다.

**인자**: 캐릭터 이름. 없으면 이름을 요청한다.

## 단계

| # | 단계 | 절차 문서 | 산출물 |
|---|---|---|---|
| 0P | 프리뷰 전사 (출시 전) | `PREVIEW.md` | `scraper/preview_skills.json` 항목, 미확정 목록 |
| 0 | 게임 데이터 수집 | `../char-scrape/SCRAPER.md` | raw data, `parsed_nikke.json`, 이미지, 무기 delay 확인 |
| 1 | 시나리오 초안 | `SCENARIO.md` | `context/scenarios/<이름>.md` (모드: 초안) |
| 2 | 스킬 파싱 | `PARSE.md` | `parsed_skills.json` 항목과 구현 필요 목록 |
| 3 | 시나리오 보강 | `SCENARIO.md` | 같은 시나리오 파일 (모드: 보강) |
| 4 | 구현·검증 | `IMPL.md` | calculator 코드, 체크리스트와 회귀 통과 |
| R | 출시 후 정식 등록 | `PREVIEW.md` | 원문 diff, 프리뷰 항목 제거 또는 단계 2 재진입 |

절차 문서는 해당 단계에 진입할 때만 읽는다. 단계 1과 3 사이의 파싱을 생략하거나
단계 2에서 단계 4로 직행하지 않는다.

## 진입점 판별

현재 파일 상태와 요청 목적을 확인하고 시작 단계를 먼저 알린다.

| 상태 | 시작 |
|---|---|
| 카드 이미지가 주어졌고 `nikke_scraped.json`에 **없음** | 0P |
| 카드 이미지가 주어졌지만 `nikke_scraped.json`에 **있음** | 0 (이미 출시됨 — 카드를 쓰지 않는다) |
| `preview_skills.json`에 있는데 `nikke_scraped.json`에도 나타남(출시됨) | R |
| `nikke_scraped.json`에 캐릭터 없음 | 0 |
| 시나리오 없음 | 1 |
| 시나리오가 초안이고 `parsed_skills.json` 항목 없음 | 2 |
| 파싱 항목은 있고 시나리오가 초안 | 3 |
| 시나리오가 보강 모드이며 `## 해석 선언`이 채워짐 | 4 |

기존 캐릭터의 스킬 변경을 재반영하는 요청은 변경된 raw text를 확인한 뒤 단계 2부터
재검토한다. 시나리오 파일명은 콜론 `:`을 `_`로 바꾼다.

## 단계 0

`../char-scrape/SCRAPER.md`를 읽고 `cdn_fetch.py --check`로 변경을 확인한 뒤 수집을 반영한다.
대상이 SR/RL이면 `post_fire_delay`와 `post_reload_delay`의 실측값을 묻고,
기본값과 다를 때만 `data/weapon_delays.json`의 `_exceptions`에 기록한다.

## Gate

각 단계가 끝나면 결과를 보고하고 다음 단계 진행 여부를 묻는다. 자동 연쇄하지 않는다.
시나리오와 파싱 결과 또는 실제 동작이 어긋나면 즉시 멈추고 어느 쪽을 고칠지 사용자 판단을 받는다.