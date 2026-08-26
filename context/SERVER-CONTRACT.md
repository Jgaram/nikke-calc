# 서버 계약 박제 — `web/server.py`의 라우트·응답·오류 전수 (2차 이식 기준)

작성 2026-08-26, 기준 코드 `web/server.py`(커밋 c9dfc6b 시점, 2,168줄). **새 서버(사이트 속도 2차,
`SITE-SPEED-2-FRAMEWORK.md`)는 이 문서와 한 글자라도 다르면 버그다** — 회귀 스크립트가 이 표를 기준으로
파이썬 서버와 새 서버를 같은 요청으로 대조한다. 오류 문장은 웹이 그대로 사용자에게 보여 주므로(i18n 사전의
키이기도 하다) 띄어쓰기까지 계약이다.

## 0. 전송 계층 공통 (모든 응답)

- HTTP/1.1, keep-alive. `Server: DILDORO` (파이썬 판번호 미노출).
- **모든 응답**(정적 포함)에 헤더 셋:
  - `Content-Security-Policy`: `default-src 'self'; script-src 'self' 'wasm-unsafe-eval'
    https://cdn.jsdelivr.net; worker-src 'self' blob:; connect-src 'self' https://cdn.jsdelivr.net;
    img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
    font-src 'self' https://fonts.gstatic.com; object-src 'none'; base-uri 'none'; form-action 'none';
    frame-ancestors 'none'`
  - `X-Content-Type-Options: nosniff` · `Referrer-Policy: no-referrer`
  - `Cache-Control`: 경로가 `.webp/.png/.jpg/.jpeg/.svg/.woff2/.ico`로 끝나거나 `/i18n/*.js?v=…`이면
    `public, max-age=604800, immutable`, 그 밖은 전부 `no-store`.
- 오류 응답은 항상 JSON `{"error": "<문장>"}` + 상태코드. 예외 → 상태 매핑:
  대기열 거절(BusyError) → **429** · 입력 오류(ValueError·SystemExit — 저장소가 사용자 오류를 SystemExit으로
  낸다) → **400** · 설명 가능한 실패(RuntimeError·조회 실패) → **502** · 그 밖 → **500** + 고정 문장
  `서버 오류입니다 — 잠시 후 다시 시도하세요.` (내부 정보는 로그에만).
- 본문 상한 **8MB**. 초과면 읽지 않고 연결을 끊으며 400 `요청이 너무 큽니다 ({n:,}B > 8,388,608B)`.
  빈 본문은 400 `빈 요청`. **응답 전에 미독 본문을 소진**해야 한다(keep-alive에서 남은 바이트가 다음 요청으로
  파싱되는 사고 방지 — 새 서버는 프레임워크가 보장하는지 확인).
- 접근 로그는 `/api/*`만, **IP·주소 미기록**(운영 원칙: 방문자를 구분하지 않는다).

## 1. 상수

| 이름 | 값 | 쓰임 |
|---|---|---|
| MAX_BODY | 8MB | 본문 상한 |
| MAX_DECKS | 12 | `/api/sim`·공유의 덱 수 상한 |
| MAX_DURATION | 600.0 | 전투 시간 상한(초) |
| LV_MAX | 1400 | 덱별 레벨 상한 |
| SIM_SLOTS / SIM_QUEUE_MAX / SIM_WAIT_MAX | 1 / 12 / 30초 | 계산 입장 제한 |
| FETCH_QUEUE_MAX | 6 | 조회 큐(진행+대기) |
| JOB_TTL / SSE_MAX | 900초 / 1800초 | 작업 보존·스트림 상한 |
| RATE_WINDOW | 60초 | 아래 창당 상한의 창 |
| RATE_MAX_SIM/CP/SHARE/OCR/BOARD, boardpw | 12/600/6/60/6, 10 | 창당 상한(서버 전역 — 키가 IP가 아니라 `*`) |
| SHARE_TTL / SHARE_MAX_BODY / SHARE_MAX_CHARS | 86400초 / 32KB / 8 | 공유 |
| OCR_MAX_TILES / OCR_MAX_POWERS | 30 / 5 | 판독 |

## 2. 게이트 (요청 분류)

- **우리 페이지 판정** `from_our_page`: `Sec-Fetch-Site` 헤더가 있으면 `same-origin`이어야 참.
  없으면 `Origin`이 (X-Forwarded-)Host로 끝나는지로 판정, Origin도 없으면 거짓.
  → `/api/share`(POST)·`/api/fetch`에만 적용, 실패 시 403 (`bot_403` 집계).
- **테일넷 전용**: `/admin`·`/admin.js`·`/api/board/admin`은 발신 IP가 `100.`으로 시작할 때만.
  아니면 404 `not found` (존재를 숨긴다). `/api/stats`는 `Tailscale-Funnel-Request` 헤더가 **있으면**
  404 `없는 라우트`.
- **로컬 직접 접속** `is_local_only`: `X-Forwarded-For`·`Tailscale-Funnel-Request`·`Tailscale-User-Login`
  세 헤더가 모두 없을 때만 참. `/api/health`의 `lab`·`union` 플래그가 이걸 쓴다(운영에서는 항상 거짓).
- **창당 상한** `rate_ok(ip="*", kind, limit)`: 60초 창의 요청 수. IP를 안 쓰므로 **서버 전역** 상한이다.

## 3. GET 라우트

| 경로 | 하는 일 | 실패 |
|---|---|---|
| `/api/health` | 기능 플래그 JSON (아래) | — |
| `/api/share?c=<code>` | 공유본 JSON | 400 `공유 코드가 아닙니다` · 404 `이 링크는 만료됐거나 지워졌습니다 (공유는 24시간 유지됩니다).` |
| `/api/board?before=<ts>&n=<개수>` | 피드백 목록(숨김 제외, 최신순, n 기본 30·상한 200) `{"items":[…]}` — 비공개 글은 body=""·reply=null·has_reply만 | 파라미터 못 읽으면 기본값으로 |
| `/api/stats` | 운영 지표(§8) | Funnel이면 404 |
| `/api/sim/events?id=` `/api/fetch/events?id=` | SSE — `data: {state,kind,pos[,error]}` 를 (state,pos) 변화 때만; done/error에서 닫음; fetch는 results 미포함; `Connection: close` | 404 `없는 작업입니다` |
| `/api/sim/result?id=` `/api/fetch/result?id=` | `{state,kind,pos}` + done이면 `results`, error면 `error` | 404 `없는 작업입니다` |
| `/admin` `/admin.js` | 내장 관리자 HTML/JS, `no-store, must-revalidate` | 테일넷 밖 404 |
| `/s` | `index.html` 서빙(공유 열람 진입) + 방문 집계 | — |
| `/s/` | 301 → `/s?<query>` | — |
| `/i18n/<en·ja·zh>.js` (+`Accept-Encoding: gzip`) | 옆의 `.gz`를 `Content-Encoding: gzip`·`Vary: Accept-Encoding`으로 | 없으면 일반 정적 경로로 |
| 그 밖 | `web/dist` 정적 서빙 — **없는 `/api/*` GET도 여기로 흘러 404 HTML이 된다**(JSON `없는 라우트`는 POST 전용) | 404 |

**`/api/health` 응답 키**: `sim:true` · `cp:true` · `ocr`(대조군 유무) · `power_ocr`(OpenCV+모델) ·
`share`(저장소 열림) · `lab`(로컬 직접 접속만) · `union`(로컬 직접 접속 또는 환경변수 `NIKKE_UNION=1`) ·
`share_ttl:86400` · `fetch`(프록시 켜짐 && 세션 쿠키 존재) · `max_decks:12` · `max_duration:600.0` ·
`jobs`(계산 스레드 수) · `slots:1` · `queue_max:12`.

**방문 집계 규칙**: API가 아니고 마지막 경로 조각에 `.`이 없는 GET만 `page`로 세고, `Referer`를
일자(KST)·도메인·주소(300자 상한)로 sqlite `ref` 표에 20건 묶음으로 기록. 자기 도메인
(`*.tetra-pantone.ts.net`)은 유입이 아님. Referer 없으면 `(직접·북마크)`.

## 4. POST `/api/sim` — 계산 (제일 중요한 계약)

요청: `{decks, duration, code, profile, enemy, config, controls, cubes, levels, codes, enemies, configs}`

**입력 검증** (전부 400, 문장 그대로):
- `decks`: 비면 `decks가 비었다` · 12 초과 `덱이 너무 많다 (N > 12)` · 각 덱은 1~5명 문자열 배열이고 빈 슬롯
  없어야 — `각 덱은 1~5명의 캐릭터 이름 배열이어야 한다 (빈 슬롯이 있으면 계산하지 않는다)`
- `duration`: 기본 180.0, 1~600 밖이면 `duration이 범위를 벗어났다 (1~600)`
- `profile`: 있으면 **계산 전에** 프로필 검증(코어 조립과 같은 규칙) — 실패 문장을 그대로 400으로.

**정제 규칙** (조용히 자르거나 버린다 — 오류 아님):
- `enemy`(공용)·`enemies[i]`(덱별): `code`→str[:8] · `def` 0~9,999,999(int) · `core_px` 0~400(int) ·
  `has_parts` bool · `optimal_range_weapons` ⊂ (AR,SMG,SG,SR,RL,MG) · `weapon_coeff` 무기군별 0.1~1.5(float),
  1.0은 제거. 결과가 비면 None.
- `config`(공용)·`configs[i]`(덱별): `first_burst_time` 0~60 · `burst_switch_delay` 0~3 ·
  `burst_reenter_delay` 0~5 · `part_break_interval` 0~180 · `max_burst_count` 1~60(int) ·
  `burst_regen_time` 0.5~30.
- `controls[i]`: 캐릭터별 dict — `control`은 {tap_fire,reload,cover,hold} 중 dict인 키만 ·
  `burst_pattern`은 카탈로그 문자열(≤40자; `안 씀`→None) 또는 `every:N`(1~99) 또는 int 목록(1~999, ≤40개,
  정렬·중복 제거) · `burst_first: true`만 통과. `no_burst: true`인 이름들(≤5명, 이름[:40])은 **덱 config의
  `no_burst_chars`로** 이동.
- `cubes[i]`: 캐릭터별 `{name≤40자, level 0~15}` → 오버라이드 `{cube:{name,level}}`로 컨트롤과 병합.
- `levels[i]`: 1~1400(int) — 덱 전원의 오버라이드 `level`로.
- `codes[i]`: str[:8] — 없으면 공용 `code`.

**job 조립** (덱마다): `{names, code: codes[i]|code, duration, profile_json, enemy: enemies[i]|enemy,
config_over: configs[i]|config (+no_burst_chars), control: 병합오버라이드|null}` → 코어 요청 배치
(`simcore.run_request_batch`) **한 번** 호출. 프로필은 요청당 한 번만 파싱. 코어의 덱 오류 문장은
`[i] ` 프리픽스를 떼고 400으로.

**입장 제한** (`_run_sim_now`): 슬롯 1, 대기 포함 12 초과 → 429 `계산 대기열이 가득 찼습니다 (진행·대기 N건).
잠시 후 다시 눌러 주세요.` · 30초 대기 초과 → 429 `계산 대기가 너무 길어졌습니다 — 잠시 후 다시 시도하세요.` ·
운영 스위치(`sim_busy_guard`, §7) 켜짐 + 실행 중 → 429 `서버가 다른 계산을 처리하고 있습니다 — 잠시 후 다시
시도하세요.` (이때만 창당 12건 상한도 함께).

성공: `{"results": [job별 dict]}` — 각 dict는 코어 요약 `{sec, total, chars, detail, top_atk, notes,
growth_flags, timeline, burst_cycles}` (모양의 정본은 코어 쪽 계약 — 서버는 손대지 않고 그대로 담는다).

## 5. POST 라우트 (나머지)

| 경로 | 게이트 | 요청 → 응답 | 오류 문장 |
|---|---|---|---|
| `/api/cp` | cp 600/분 | 옵션 dict → `cp_engine.compute` 결과 + `cp40`(level 40 재계산, 실패 시 null) | 429 `요청이 너무 잦습니다 — 잠시 후 다시 시도하세요.` · compute의 ValueError 문장 |
| `/api/atk` | cp 600/분 | `{names(1~5), profile}` → `{"atk": {이름: {atk(반올림 int), atk_pct(float)}}}` | 400 `names는 1~5명이어야 한다` |
| `/api/squad/power` | ocr 60/분 | `{regions:[{w,h,rgb(b64)}]≤5}` → `{"powers": …}` | 429 `판독 요청이 너무 잦습니다 —…` · 503 `전투력 판독을 쓸 수 없습니다 (OpenCV 또는 학습 모델 없음).` · 400 `regions는 1~5개여야 한다 — 솔로레이드는 스쿼드가 다섯이다` |
| `/api/squad/align` | ocr | `{samples:[[b64×9]]1~8}` → `{"align_index","align"}` | 503 `판독 대조군이 없습니다 (python scraper/face_sig.py).` · 400 `samples는 1~8칸이어야 한다` · `칸마다 틀 9개가 와야 한다` · `{이름}가 base64가 아니다` |
| `/api/squad/read` | ocr | `{tiles:[{c12,c24,c32,badge(b64)}]1~30, locked:{i:이름}}` → `{"cells": …}` | 위와 같음 + `tiles는 1~30칸이어야 한다` |
| `/api/share` | 우리 페이지 + share 6/분 + 저장소 | 공유본(§6) → `{"code","expires","ttl":86400}` | 403 `이 사이트의 페이지에서만 공유할 수 있습니다.` · 429 `공유 요청이 너무 잦습니다 —…` · 503 `이 서버는 공유 저장소를 열 수 없습니다.` · 413 `공유 내용이 너무 큽니다 (N,B > 32,768B) — 편성과 딜 수치만 담깁니다.` |
| `/api/unshare` | share 6/분 + 저장소 | `{code}` → `{"deleted": bool}` | 400 `공유 코드가 아닙니다` |
| `/api/board` | board 6/분 | `{body 2~1000자, nick(기본 익명, [:12]), private, pw(4~32자), web(허니팟)}` → `{"ok":true,"id"}`; 허니팟이면 저장 없이 `{"ok":true}`; 같은 공개 본문 1시간 내 재전송이면 기존 id | 429 `피드백이 너무 잦습니다 — 잠시 후 다시 남겨 주세요.` · 400 `내용은 2~1000자로 적어 주세요` · `비공개 글은 4~32자 비밀번호가 필요합니다` |
| `/api/board/view` | boardpw 10/분 | `{id,pw}` → 글 전문(PBKDF2 20만회+글별 솔트, 상수시간 비교) | 429 `시도가 너무 잦습니다 — 잠시 후 다시 해 주세요.` · 403 `비밀번호가 맞지 않습니다` |
| `/api/board/admin` | 테일넷 | op=list/refs/settings/sim-guard/reply/hide/unhide/del | 400 `실패 — id·op 확인` |
| `/api/fetch` | 우리 페이지 + 프록시 켜짐 | `{openid|url, area?}` → **202** `{"job","queued"}` (큐 → SSE → result) | 403 `이 사이트의 페이지에서만 조회할 수 있습니다.` · 503 `이 서버는 조회 프록시를 끄고 실행되었습니다 (--no-fetch). 북마클릿을 사용하세요.` · 400 `openid 또는 프로필 URL이 필요하다` · `URL에 openid= 파라미터가 없다` · `openid를 해석할 수 없다: '…'` · 429 `조회 대기열이 가득 찼습니다 (진행·대기 N건). 잠시 후 다시 눌러 주세요.` |

## 6. 공유본 화이트리스트 (`share_clean` — 서버가 다시 짓는다)

`{v:1, code(≤8자|null), duration(1~600), total(0~1e18), decks:[{names(≤8개, 각 ≤40자|null),
total, chars:{이름:수}(≤8), weak?(≤8자)}](1~12), mode?("solo"|"union")}` — 그 밖 키(닉네임·스펙 지문·notes
등)는 **무엇이 오든 버린다**. 숫자는 범위 밖이면 자르지 않고 400(`숫자가 아닌 값이 있습니다`·`값이 범위를
벗어났습니다`). 저장은 zlib(9) 압축, 코드는 `secrets.token_urlsafe(6)`(중복 시 재추첨 8회), 쓰기 때마다
만료분 삭제. 코드 형식 `^[A-Za-z0-9_-]{4,16}$`.

## 7. 조회(`/api/fetch`) 흐름과 운영 스위치

- 큐(진행+대기 6) → **단일 워커**가 순차 처리(운영자 세션 보호 — 조회가 겹치지 않게) → 클라이언트는
  `/api/fetch/events?id=`(SSE)로 순번을 보다가 done이면 `/api/fetch/result?id=`로 결과 수령.
- 결과: `{"raws":[지역별 {openid, area, area_label(한섭/일섭/글로벌섭), characters, details, state_effects,
  outpost, union, _source:"블라링크", _collected_at}], "cached": false}` — **디스크 캐시 없음.**
- 실패 종류(운영 지표 구분용): `session` `운영자 세션이 만료됐다 (game not login). scraper/.session_cookie를
  갱신해야 한다.` · `private` (공개 설정 안내 문장 — 북마클릿 안내 포함) · `notfound` `캐릭터를 받지 못했다 —
  비공개 계정이거나 없는 openid다.` → SSE의 error로 전달(HTTP로는 502에 해당).
- 지역 후보 (83 한섭, 81 일섭, 84 글로벌) 전부 훑어 **전 지역** 반환; `area`를 주면 그 지역만(다시 싱크).
- 운영 스위치 `sim_busy_guard`(§5의 admin `sim-guard` op): `ops.json`에 원자적 저장, 재시작에도 유지.

## 8. 부수 효과 (지표·저장)

- 카운터(메모리, 재시작 시 0): `page, sim_req, sim_deck, sim_err, sim_sec, fetch_req, fetch_ok, fetch_err,
  fetch_err_private/session/notfound/other, fetch_bad_input, busy_429, bot_403, cp_req, ocr_req,
  share_put/get/miss/del`. `/api/stats`가 업타임·큐 상태·loadavg(없으면 null)·`pool_jobs`·`fetch_on`·
  `sim_busy_guard`·유입 요약과 함께 낸다.
- sqlite (`STATE_DIRECTORY` 또는 `web/.state`의 `share.db`, WAL): `share(code, body BLOB, created)` ·
  `board(id, ts, kind, nick, body, reply, reply_ts, hidden, private, pw)` · `ref(day, host, url, n)`.
  연결 하나 + 락(쓰기 드묾). 저장소가 안 열리면 공유만 끈다(`share_ok` — health의 `share`).

## 9. 이식 노트 (2차)

- **사이드카 아키텍처(2026-08-26 확정)**: 아직 TS로 안 옮긴 라우트(`/api/cp` · `/api/atk` · `/api/squad/*` ·
  `/api/fetch` · 네 job events/result)는 **기존 파이썬 서버를 내부 포트(기본 127.0.0.1:8768)로 띄워 투명
  프록시**한다 — 응답이 코드 복제 없이 동일하고, 라우트를 하나씩 TS로 옮기면 프록시 목록에서 빼면 된다.
  규칙: 게이트 판정용 호스트는 `X-Forwarded-Host`로 전달(파이썬 `from_our_page`가 Origin 접미사를 이것과
  비교), SSE는 스트림 통과(실시간 검증됨), 프록시 라우트의 창당 상한·운영 카운터는 **사이드카 안에** 산다
  (새 서버 `/api/stats`에는 안 잡힌다 — 운영 수치 한정 차이). 사이드카가 죽으면 그 라우트만 502(고정 문장)
  이고 health의 cp/ocr/power_ocr/fetch가 꺼진다 — 계산·공유·정적은 계속 산다(파이썬 단일 서버에는 없던
  실패 모드). 남은 주의: Node 런타임의 undici는 SSE 유휴 300초에 본문을 끊을 수 있다(조회 SSE는 상태
  변화가 수 초 간격이라 실전에서는 안 걸린다; Bun이 배포 런타임이다).

- **계산은 코어 요청 배치 한 번** — 어댑터 계약은 `web/simcore.py`: `load_data(dir, threads)`(첫 호출 고정) ·
  요청 키 `{names, code, duration, profile, enemy, config_over, control}` · 오류 `[i] ` 프리픽스 제거.
  새 서버는 native 전용이다(파이썬 엔진 경로·프로세스 풀은 이식하지 않는다 — 운영 결정: 폴백 없음).
- **파이썬 사이드카로 남길 것**: `squad_ocr`(align/read) · `power_ocr`(read_regions) — OpenCV.
  `cp_engine.compute`(+cp40)와 `base_atk_of`(/api/atk — 조립+기본 스탯: 코어에 같은 면이 이미 있어 코어
  호출로 대체 가능)는 사이드카/코어/이식 중 실측으로 정한다. 판정 기준: 응답 모양이 이 문서와 같을 것.
- `_sim_worker`·`/api/sim/events`는 동기화 전환(1차) 뒤 **안 쓰이지만 라우트는 살아 있다**(사실상 fetch 전용).
  새 서버도 라우트는 유지한다(옛 클라이언트·북마크가 404를 받지 않게).
- OG 이미지는 서버 라우트가 아니라 빌드 산출물(`web/build.py`) — 이식 대상 아님.
- **문서화된 차이 하나**: 프로필이 잘못된 계산 요청을 파이썬은 사전 검증에서 끊어 `sim_req/sim_err`
  집계 전에 떨어뜨리지만, 새 서버는 코어 조립이 끊는다 — 응답(문장·상태 400)은 같고 운영 카운터만
  +1 차이다(스펙 검증 로직을 서버에 복제하지 않기 위한 선택).
- 검증: 두 서버를 다른 포트로 띄우고 같은 요청 모음(정상·오류·게이트·한도 초과)을 쏴 **상태코드·헤더 4종·
  본문(정규화 JSON)**을 대조하는 회귀 스크립트를 만든다. 문장 하나라도 다르면 실패.
