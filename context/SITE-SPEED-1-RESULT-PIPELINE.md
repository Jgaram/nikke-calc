# 사이트 속도 1차 — 결과 가공을 코어로 옮기고, 대기 지연을 없앤다

작성 2026-08-26. **계획만** — 사이트 코드는 아직 안 건드렸다. 코어(`nikke-core`) 쪽 절반(§2)은 코어 작업이라
먼저 진행한다. 실행은 사용자 결정 뒤.

## 0. 지금 어디에 시간이 가나 (서버 실측, 2026-08-26)

같은 덱(c0048 편성, 기본 스펙)을 서버 `_sim_one`으로 돌린 cProfile:

| 구간 | ms | 비고 |
|---|---|---|
| `nikke_py.simulate_json` (코어: 입력 JSON 파싱 + 계산 + 히트 1.6만 개 직렬화) | 55 | 코어 계산 자체는 ~45 |
| `json.loads` (히트 JSON 파싱) | 17 | 서버가 결과 JSON을 dict로 |
| `_decode_hits` (HitEvent 1.6만 개 생성) | 23 | 파이썬 객체 생성 비용 |
| `dps_timeline` | 17 | 히트 순회 |
| `detail` 집계(캐릭터별 normal/skill/crit 합) | 16 | 히트 5회 순회 |
| `build_squad`/`build_config`/deepcopy | 8 | 스펙 조립 |
| `format_deviations` (notes) | 4 | |
| **합계 `_sim_one`** | **112** | (cProfile 없이) |

HTTP로는 1덱 **0.28초**, 5덱 0.26~0.5초. 차이 ~0.15초는 계산이 아니라 **`_sim_events`의 0.25초 폴링**
(`time.sleep(0.25)`로 상태를 보고 SSE를 보낸다 — 평균 +125ms, 최악 +250ms)과 워커 풀 왕복이다.

즉 코어 45ms를 제외한 **~70ms가 파이썬 쪽 히트 가공**, **~150ms가 대기 지연**이다. 둘 다 코어와 무관하게
지금 구조 안에서 없앨 수 있다.

## 1. 목표

- 서버 `_sim_one`: 112 → **~60ms**(코어 45 + 스펙 조립 8 + notes 4 + 요약 dict 조립).
- HTTP 제출→완료: 1덱 0.28 → **≤ 0.10초**, 5덱(워커 3) 0.26~0.5 → **≤ 0.20초**.
- 결과 화면이 받는 JSON 모양·값은 **완전히 같다**(키 순서까지). 검증은 서버에서 py vs native 비교 스크립트로.

## 2. 코어 쪽 (nikke-core — 지금 진행)

1. `crates/core/src/derived.rs`: 파이썬 `sim_result.py`의 `_is_normal`·`dps_timeline`·`burst_cycles`·
   `analyze_top_atk`·`summarize_top_atk`와 서버 `_sim_one`의 `detail` 집계를 **1:1**로 옮긴다
   (합산은 파이썬 `+`/`sum()` 의미 그대로 — int/float 구분, Neumaier, `round(x, n)`은 정확 반올림).
2. CLI 출력에 `derived` 절을 넣는다(골든과 같은 모양). nikke-core 하네스의 대조 스크립트는 코어가 `derived`를
   주면 그것을 골든의 `derived`와 대조한다(전에는 파이썬 함수로 다시 계산해 붙였다) → **2,340건 비트 검증**.
3. PyO3에 `simulate_summary_json(case)` 추가 — 히트 없이 `{total, chars, detail, top_atk, timeline,
   burst_cycles}`만 돌려준다(서버가 받는 그 모양). 하네스에 `--engine py-summary`를 두어 골든의
   `squad_total`·`char_total`·`derived`와 대조한다.
4. 배치 API: `load_data(dir, threads=T)` + `simulate_summary_batch_json([case, …])` — 스레드 풀 병렬, 입력 순서.
   순차 결과와 문자열까지 같음을 `harness/batch_check.py`가 확인한다.

**2026-08-26 상태: 1차 전부 반영·배포 완료.** 코어(§2 1~4 + 스펙 조립 `assemble.rs`: 원 요청 → 조립·계산·요약을
코어가 한 번에)와 사이트(§3: `/api/sim` 동기 응답, native 모드는 `simcore.run_request_batch` 한 번 호출, 클라이언트는
응답의 results를 바로 씀). 서버 실측: 덱당 2.4초 → **0.036초**(결과 동일, `deploy/check_engine.py`), HTTP 1덱 0.08~0.14초 ·
5덱 0.08~0.11초. 웹 서버에는 정적 서빙·입장 제한·입력 검사·notes 외 계산 논리가 남지 않아 2차(프레임워크 교체)는
«코어 호출 한 줄 + 라우트 이식»이면 된다.

## 3. 사이트 쪽 (nikke-calc — 사용자 결정 뒤 실행)

1. `web/simcore.py`에 `run_summary(squad, config, enemy) -> dict` 추가(3의 함수 호출, JSON 한 번 파싱).
2. `web/server.py` `_sim_one`: `SIM_ENGINE == "native"`면 `run_summary` 결과로 `total/chars/detail/top_atk/
   timeline/burst_cycles`를 채우고 **히트 복원·파이썬 집계를 생략**한다. `py` 모드는 지금 경로 그대로
   (거울 규약 — 두 경로가 같은 dict를 내야 한다). `notes`·`growth_flags`는 파이썬 그대로(스펙 쪽).
3. 대기 지연: (결정 2026-08-26) SSE 폴링을 고치는 대신 **계산 API를 동기 응답으로** 바꾼다 — `/api/sim`이
   결과를 그 응답에 담아 준다. 큐는 입장 제한(동시 `SIM_SLOTS`·대기 `SIM_QUEUE_MAX`·최대 대기 초, 운영자
   스위치면 즉시 거절)으로만 남긴다. 조회(`/api/fetch`)의 SSE는 그대로.
4. 클라이언트(`app.js`): 계산 호출부가 이벤트 스트림 대신 POST 응답의 `results`를 바로 쓴다.
5. 프로필 파싱 캐시: 워커마다 `profile_json`의 해시 → 파싱된 프로필 1건 캐시. 5덱 요청이면
   `profile_from_dict` 5회 → 1회.
6. **한 번에 20덱**(옵션 비교 등 — 사용자 요구): 프로세스 풀(워커 3, 덱마다 프로세스 왕복·프로필 재파싱)
   대신 코어의 **배치 API**를 쓴다 — `nikke_py.simulate_summary_batch_json([case, …])`가 한 호출에 덱 N개를
   코어 스레드 풀(스레드마다 데이터 캐시, `load_data(dir, threads=T)`)로 병렬 계산해 요약 목록을 돌려준다.
   서버 쪽은 `_sim_one`을 «스펙 조립 → 케이스 JSON» 부분과 «요약 → 응답 dict» 부분으로 나눠, 요청의 덱
   전부를 한 번에 조립해 배치로 넘기고 결과를 나눠 담는다(`notes`·`growth_flags`는 덱별 파이썬 그대로).
   기대치(서버 4코어, T=3): 20덱 ≈ 20 × 45ms / 3 ≈ **0.3초**(지금 구조로는 20 × 112 / 3 ≈ 0.75초 + 큐).
   `MAX_DECKS`(지금 12)를 20 이상으로 올리고, 요청 JSON 크기(프로필은 요청당 한 번)와 429 한도를 같이 본다.
   SSE는 덱별 완료가 아니라 배치 완료 한 번이면 된다(0.3초).
7. (선택) 스레드 수 T: 서버에 다른 서비스가 같이 돌므로 T는 사용자 결정(PERF-ENGINE §「워커 늘리기 — 제외」).
   T=3이면 지금 워커 수와 같은 부하다. 프로세스 풀은 `py` 모드용으로 남긴다.

## 4. 검증·측정·되돌리기

- 서버에서 `python3 deploy/check_engine.py`(지금 `/tmp/server_e2e.py`를 저장소로 옮긴 것):
  같은 요청을 `py`와 `native`로 돌려 결과 dict(파생값 포함)가 같은지 + HTTP 1덱·5덱 시간을 찍는다.
- 배포는 `SITE.md` §7 그대로. 되돌리기는 코드 되돌림(모드 스위치는 그대로 `native`).
- 기대치: `_sim_one` 60ms, HTTP 1덱 0.09초, 5덱 0.2초. 실측으로 갱신한다.

## 5. 하지 않는 것

- 파이썬 폴백·대조 표본(운영 결정). 응답에 코어 관련 키 추가(중립 이름 원칙).
- 히트 목록을 클라이언트로 보내는 것(지금도 안 보낸다).
