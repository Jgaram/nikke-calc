# 외부 자료 진입점 (정본)

우리가 실제로 값을 캐 온 곳과, **다음 사람이 같은 곳에 다시 들어가는 방법**을 적어 둔다.
사이트마다 진입 장벽이 다르고(SPA·리다이렉트·필수 헤더) 그걸 매번 다시 알아내는 게
조사 비용의 절반이었다.

**이 문서는 진입 방법과 주의사항만 담는다.** 값 자체는 각 정본 문서에 있다.

## 표

| 자료 | 무엇을 주나 | 진입 방법 |
|---|---|---|
| **blablalink CDN** | 캐릭터·무기·성장 테이블 원값 (**우리 정본**) | `scraper/cdn_path.py` + `scraper/cdn_fetch.py`. **인증 불필요.** 표 재생성은 `python scraper/cdn_tables.py`(`--check`로 diff만, `--only <표>`로 한 개만) |
| **nikke-kr.com 공지** | 메커니즘 변경 이력. 라이선스 깨끗한 1차 사료 | `POST .../InformationFeedsSvr/GetFeeds` → `GetContentInfoById`. **헤더 필수**: `x-areaid: na` · `x-source: pc_web` · `x-language: ko` · `x-gameid: 16` · `Content-Type: application/json;charset=utf-8` · `Referer: https://nikke-kr.com/`. 웹 상세 페이지는 `newsdetail.html`이다(`news_detail.html`은 404) |
| **nikke.top** | 아레나 프레임 단위 타임라인. **채점표로만 쓴다** | SPA다. `/about` 직접 접근은 404 — `/`를 띄우고 모달을 닫은 뒤 링크를 클릭한다. 데이터는 `assets/HomePage-*.js`의 `JSON.parse(...)` 2블록(백틱·홑따옴표 둘 다 있다). 캐릭터별 전용 계산기는 `Cr.getCalculator`의 switch문 |
| **nikke-deck.com** | 아레나 버스트 게이지 계산기 | 사이드바 `정보`. `/ko/information` 직접 접근은 홈으로 리다이렉트된다 |
| **KosMiu / Gatrix 스프레드시트** | 위 둘의 원 데이터 | xlsx export를 받아 `sharedStrings` 파싱 |
| **NGA 원글** | 측정 방법론의 출발점 | `ngabbs.com/read.php?tid=36406961` |

수집한 원자료는 `archive/research/`에 있다(**로컬 전용** — `.gitignore`가 `archive/*`를
통째로 무시한다). 파일별 출처표는 그 폴더의 `README.md`.

## 주의 — 아레나 값을 그대로 쓰면 안 된다

1. **아레나 수치는 PvE와 스케일이 다르다.** 버스트 게이지에서 저쪽은 ① 1배값
   (`burst_energy_pershot`)을 쓰고 ② 풀차지 배율을 안 곱한다. SR 1발이 아레나 2.8% vs
   우리 14.5% — **5.2배** 차이다. 정본은 `docs/mechanics/버스트 게이지.md`.
2. **`chargeMultiplier`는 아레나 기하학이다.** RL ×4, 관통 ×2, AoE ×5 같은 배수는
   "니케 5명이 줄지어 선 아레나"에서 나온 값이라 단일 보스에서는 **1로 접힌다.**
   접는 걸 빠뜨리면 히트 수가 부풀려진다(헤비암즈에서 실제로 그랬다).
3. **저쪽 값은 이식이 아니라 채점에 쓴다.** 전용 계산기 21개를 분해하면 새 상수가 하나도
   없고 전부 (무기값 × 히트 수)나 스킬 텍스트 %로 환원된다. 가져올 것은 "어느 스킬이
   히트를 몇 개 만드나"라는 **사실**뿐이고, 그건 우리 파싱을 검산하는 데 쓴다.
4. **2024-03-07 이전 커뮤니티 측정값에는 RL 차지속도 버그가 섞여 있다.**
5. **2024-12-05에 `버스트 게이지 획득량` → `버스트 게이지 충전 속도`로 표기만 바뀌었다.**
   별개 메커니즘이 아니다 — 파싱에서 두 개로 가르지 말 것(우리 stat은
   `burst_charge_speed_pct` 하나다).

## 어떤 사이트를 더 넣을지

**이 문서는 "실제로 써서 값을 얻은 곳"만 담는다.** 안 써 본 사이트를 미리 등재하지
않는다 — 진입 방법을 검증하지 못한 항목은 다음 사람에게 도움이 아니라 함정이다.
새로 캐 온 곳이 생기면 그때 한 줄 추가한다.
