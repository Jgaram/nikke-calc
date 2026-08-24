# NIKKE 덱 랩 — 사이트를 어떻게 만들었나

`https://nikkedeck.tetra-pantone.ts.net`

블라블라링크에서 내 육성 상태를 가져와 브라우저에 저장하고, 솔로레이드 5덱을 짜서
덱별·합계 딜을 보는 사이트. 딜 계산은 이 저장소의 계산기(`calculator/`)를 그대로 쓴다 —
사이트는 그 위에 **수집·저장·편성 UI**를 얹은 것이다.

관련 문서: 데이터 취급은 [PRIVACY.md](PRIVACY.md), 계산기 자체는
[context/CALCULATOR.md](context/CALCULATOR.md), 조회 API 실측은
`context/scenarios/블라링크 프로필 조회.md`.

---

## 1. 전체 그림

```
                    ┌─ 북마클릿 ──── 방문자 자기 세션 (blablalink 탭에서 실행)
  육성 데이터 수집 ──┼─ URL 조회 ──── 서버가 운영자 세션으로 대신 조회
                    └─ 레츠도로 CSV ─ 로그인 없이

           ↓  (변환은 어느 경로든 같은 코드: scraper/profile_convert.py)

  localStorage  ←── 정본. 서버에 안 남는다

           ↓

  계산 ─┬─ 브라우저: Pyodide 워커가 이 저장소를 통째로 로드해 실행
        └─ 서버:     ProcessPoolExecutor로 덱 병렬 (약 3배 빠름)
```

설계의 뼈대는 두 가지다.

**남의 육성 데이터를 내 서버에 태우지 않는다.** 그래서 기본 경로가 브라우저 안이다.
변환기도 계산기도 순수 파이썬이라 Pyodide에서 그대로 돈다. 서버는 «가속 옵션»이지
필수가 아니다 — 서버가 죽어도 사이트는 동작한다.

**내 계정으로 남의 조회를 대신하지 않는 게 원칙**이되, 편의를 위해 URL 조회는 남겨
뒀다. 그건 공개로 설정된 프로필만 볼 수 있고, 비공개면 북마클릿을 쓰라고 안내한다.

---

## 2. 수집 세 갈래

세 갈래 모두 **같은 모양의 raw**를 만들고, 변환은 `build_profile()` 하나를 탄다.
그래서 「서버로 받은 것과 북마클릿으로 받은 것이 다르게 계산된다」가 구조적으로 불가능하다.

```python
# scraper/profile_convert.py
def build_profile(raw, maps, name) -> tuple[dict, list[dict]]
#   raw = {"characters", "details", "state_effects", "outpost"}   ← 수집 경로 무관
```

### 북마클릿 (`web/src/bookmarklet.js`)

방문자가 blablalink 탭에서 누르면 same-origin으로 자기 계정을 조회해 `nikke-raw-*.json`을
내려받는다. **세션 쿠키는 브라우저를 떠나지 않는다.** 조회 대상이 본인으로 고정돼 있어
(target openid 입력칸이 없다) 남의 계정을 긁는 데 쓸 수 없다.

공개 설정과 무관하게 동작한다 — 자기 세션으로 자기 데이터를 보는 것이기 때문이다.

### URL 조회 (`POST /api/fetch`)

브라우저는 CORS 때문에 블라 API를 직접 못 부른다(실측: `OPTIONS` → 405, ACAO 헤더 없음).
그래서 서버가 대신 부른다. 받은 URL에서 **숫자 openid만** 뽑아내고 나머지는 버린다:

```
https://www.blablalink.com/user?openid=MjkwODAtMTAz…   (base64 "29080-<숫자>")
29080-10346314715007941757
10346314715007941757                                    ← 셋 다 이걸로 정규화
```

`nikke_area_id`는 서버가 모르니 흔한 값부터 훑는다(83, 1, 261, …). 틀린 area는
`1303001 param invalid`로 떨어지고, 그 사이에 섞여 오는 **진짜 이유**를 붙잡아 둔다 —
`1301002 user not allow show nikkeinfo`면 「니케 정보 공개가 꺼져 있습니다」라고 알려 준다.
이걸 안 하면 비공개 계정과 없는 openid가 똑같은 문구를 받는다.

### 레츠도로 CSV (`scraper/profile_csv.py`)

로그인이 필요 없는 대신 **호감도·큐브·닉네임·콘솔이 없다**(55개 컬럼 전수 확인). 애장품
단계는 있다 — 예전엔 이걸 못 읽고 전원 3단계로 넣고 있었다.

오버로드 옵션은 CSV가 표시용으로 반올림한 값이라, CDN 표에 **0.05%p 이내로 맞으면
표의 값을 쓴다**. 안 맞으면 그 줄을 통째로 버린다(레벨 없이 수치만 적용하지 않는다).
실측 837줄 중 836줄 정확히 일치, 1줄이 0.01%p 차이(명중 14.64 → 표의 14.63)였다.

---

## 3. 계산이 도는 두 곳

**같은 것을 계산해야 한다.** 한쪽만 고치면 「서버 켜고 끄는 것만으로 총딜이 달라지는」
사고가 난다. 두 진입점이 서로를 거울처럼 따라간다:

| | 브라우저 | 서버 |
|---|---|---|
| 코드 | `web/src/worker.js`의 `run_one()` | `web/server.py`의 `_sim_one()` |
| 실행 | Pyodide + `repo.zip`(빌드가 굽는다) | `ProcessPoolExecutor` |
| 속도 | 약 10.5초/덱 | 약 4.4초/덱 (실측) |

둘 다 `context.spec.build_squad(names, control, profile=prof)`를 부르고
`rng_mode="expected"`로 돌린다. 기대값 모드는 결정론적이라 **양쪽 총딜이 완전히 같아야
한다** — 다르면 프로필 전달 경로가 틀린 것이다.

### 결과 캐시와 지문

계산은 비싸서 결과를 localStorage에 캐시한다. 그래서 **무엇이 바뀌면 다시 계산해야
하는지**가 정확해야 한다. 지문에 안 들어간 값을 바꾸면 옛 결과가 그대로 보인다 —
이 프로젝트에서 가장 조용히 틀리는 종류의 버그다.

```js
const CALC_V = "c3";      // 계산 의미가 바뀌면 올린다 (캐시 전부 무효화)
JSON.stringify([d.names, CALC_V, code, duration, profSig(), battleSig(), ctrlSig(d)])
```

`CALC_V`는 스키마 토큰이다. 「약점 코드의 뜻이 바뀜」(w2), 「크리 기대값 추가」(c3)처럼
결과 의미가 달라질 때 올리면 옛 캐시가 한 번에 무효가 된다.

### 크리는 확률이 아니라 계수다

기대값 모드에서는 크리를 굴리지 않고 계수에 녹인다(`calculator/damage.py`). 그래서
`is_crit`은 언제나 False다 — 그대로 세면 「크리 0%」가 나온다. 실제 기대 크리율은
`crit_frac`에 있었는데 `HitEvent`에 실려 오지 않아서, 필드를 하나 더했다(가산이라
스냅샷 27/27 그대로).

### 약점 코드의 방향

인게임 「약점 코드」는 **내가 들고 갈 속성**을 뜻한다. UI 값을 그대로 `enemy.code`로
넘기면 방향이 반대가 된다. 매핑을 하나 둔다:

```js
const WEAK_TO_ENEMY = { 전격:"수냉", 수냉:"작열", 작열:"풍압", 풍압:"철갑", 철갑:"전격" };
```

---

## 4. 인게임 UI를 어떻게 베꼈나

「직접 그리지 말고 원본을 가져와라」가 원칙이었다. 블라블라링크 CDN은 경로를 난독화해
두는데, 프론트 청크를 읽어 규칙을 복원했다(`scraper/cdn_path.py`, 51줄):

```
디렉터리 = djb2 해시 토큰 · 파일명 = md5
ICONS_URL = ({path, name}) => getIngameResourceUrl(`/icon/${path}/${name}.webp`)
```

이걸로 뽑은 것(`scraper/cdn_icons.py`):

| 네임스페이스 | 내용 |
|---|---|
| `atlas_common_class` | 속성·역할군·버스트 아이콘 |
| `atlas_common_corp` | 기업 엠블럼 |
| `atlas_common_grade` | R/SR/SSR/애장품 등급 마크, 별, 코강 링 |
| `favoriteitem` | 애장품 아이콘 33종 (R6·SR6·SSR21) |

받은 응답이 200이어도 SPA 폴백 HTML일 수 있어서 **WEBP 매직 바이트를 검사**한 뒤에만
저장한다.

### 코스튬(스킨)은 **입고 있는 그대로** 그린다

프로필 응답이 캐릭터마다 장착 중인 코스튬 id를 준다 —
`characters[].costume_id` = `details[].costume_tid`, `0`이면 기본 코스튬이다.
그 id를 그림으로 바꾸는 표는 CDN roledata 안에 있다:

```
/roledata/{리소스id}-v2-ko.json → character_costume_list
  { "id": 10005, "costume_index": 2, "costume_name_locale": "클래식 바캉스", … }
```

`id`가 프로필이 주는 그 값이고, `costume_index`가 그림 번호다. 그림 경로는 기본
코스튬과 같은 틀에 번호만 바뀐다 — 초상화 `/character/mi/mi_c###_##_s.webp`,
전신 `/character/full/c###_##.webp`, 얼굴 `/character/si/si_c###_##_s.webp`.
`scraper/cdn_costume.py`가 표와 그림을 함께 받아 `data/costume_index.json`으로
남기고, `web/build.py`가 캐릭터별 `rec.costumes`로 구워 로스터에 싣는다.

세 가지를 지켰다:

- **계산에는 안 들어간다.** 프로필에 적히는 키는 `_costume`이고, `_` 접두 키는
  `spec.deep_merge`가 건너뛴다(`_cube`·`_ol`과 같은 취급). 외형뿐이다.
- **전신은 경계를 따로 잰다.** 2048² 안에서 캐릭터가 앉은 자리가 코스튬마다 달라,
  기본 코스튬의 알파 경계를 그대로 쓰면 스킨만 발이 잘리거나 붕 뜬다
  (`cdn_costume.py _add_bbox` → `rec.costumes[id].fbb`).
- **없으면 조용히 기본으로.** 표가 없어도, 그림이 없어도 빌드를 세우지 않는다 —
  기본 코스튬으로 그려질 뿐이다.

별 표기 규칙도 블라 프론트(`star-GXlUU28h.js`)에서 그대로 옮겼다:

```js
const RARE_STARS = { SSR: 3, SR: 2, R: 0 };
// 한계돌파 = 돌파 + 코강,  10 이상이면 "MAX",  아니면 (한계돌파 - 최대별)
```

---

## 5. 편성을 공유하는 길

계산 결과 화면을 남에게 보여 주고, 받은 사람이 **편성만** 자기 덱으로 가져가는 길이다.
이미지 내보내기와는 다른 물건이다 — 이미지는 보여 주기만 하고, 링크는 **가져올 수** 있다.

세 경로가 각자 다른 것을 담는다. 섞으면 «내보낸 걸 남에게 줬는데 닉네임이 딸려 갔다»가 된다.

| 경로 | 담는 것 | 용도 |
|---|---|---|
| 기록 «내보내기» JSON | 전부 (편성·수치·운용·스펙 지문·계정 이름·이탈 목록) | **내 백업** |
| 편성 프리셋 (`nikke.presets.v1`) | 편성 + 운용(`control`) + 약점 코드·전투 조건 | 내 편성 되살리기 |
| 공유 링크 (`/s?c=…`) | 편성 + 표시용 딜 수치 **뿐** | 남이 편성 복사 |

### 무엇을 올리고 무엇을 안 올리나

```json
{ "v": 1, "code": "풍압", "duration": 180, "total": 34012000000,
  "decks": [ { "names": [5], "total": 12040000000, "chars": {…} } ] }
```

5덱이 1.5KB 안쪽이다. **빠지는 것**: `profileName`(계정 이름) · `profileSig`(스펙 지문) ·
`engine` · `detail`(니케별 히트·크리) · `control`(운용) · 그리고 **`notes`**.

`notes`가 특히 중요하다 — `format_deviations()`가 만드는 그 문장에는
`[앨리스] equip_skills.charge_speed_pct: 0 → 9.26`처럼 **장비 실수치가 그대로** 들어 있다.
그대로 올리면 「편성만 공유한다」가 사실이 아니게 된다.

문은 **두 겹**이다. 브라우저에서 `sharePayload()`가 화이트리스트로 짓고, 서버에서
`share_clean()`이 받은 것을 **다시 짓는다.** 웹을 고치다 실수로 필드를 하나 더 실어도
서버에 남지 않는다 — 약속을 문서가 아니라 코드가 지키게 하려는 것이다.

### 저장은 sqlite 한 파일

MySQL을 올리지 않았다. 담는 것이 «짧은 코드 → 1.5KB JSON, TTL 하루»이고, 이건 **캐시**지
정본이 아니다(정본은 여전히 방문자 localStorage다). 데몬·포트·계정·백업을 늘리는 대신
표준 라이브러리 `sqlite3`로 파일 하나를 쓴다. 나중에 관계형이 진짜 필요해지면 갈아 낄
면이 `share_put`·`share_get`·`share_del` 셋뿐이다.

```
share(code TEXT PRIMARY KEY, body BLOB, created REAL)   -- body는 zlib 압축 JSON
```

- 코드는 `secrets.token_urlsafe(6)` — 8자. **순번이면 남의 공유를 차례로 훑을 수 있다.**
- 만료는 **쓰기 때마다** `DELETE WHERE created < now-86400`. 쓰기가 드문 표라 타이머가 필요 없다.
- 만든 사람은 `POST /api/unshare`로 즉시 지운다 — **코드를 아는 것이 곧 권한이다.**
- 저장소를 열 수 없으면 `share_ok()`가 거짓이 되고 `/api/health`가 `share: false`를 준다.
  웹은 그걸 보고 버튼을 감춘다 — 눌러 놓고 실패를 알려 주는 버튼을 두지 않는다.

**쓰기 경로는 유닛이 딱 하나만 준다.** `ProtectSystem=strict`를 풀지 않고
`StateDirectory=nikke-decklab` 한 줄로 `/var/lib/nikke-decklab`만 열었고, 서버는 그 경로를
`STATE_DIRECTORY` 환경변수로 받는다(유닛 없이 띄우면 `web/.state/`). 저장소 안에 두면
**배포가 tar로 덮어써서 링크가 매번 죽는다** — `/var/lib`는 재배포·재시작을 넘어 남는다.

### 주소는 `/s?c=<코드>` — 경로형이 아니다

`index.html`의 자산 링크가 전부 상대경로(`app.js`·`style.css`, JS의 `image/${img}`)다.
`/s/<코드>`로 서빙하면 기준 경로가 `/s/`가 되어 브라우저가 `/s/app.js`를 찾고 **전부
404**가 된다. 질의문이면 기준이 `/`로 남아 아무것도 손대지 않아도 된다. 끝에 `/`가 붙은
`/s/`도 같은 이유로 301로 되돌려 보낸다.

링크 미리보기(`og:image`)는 **넣지 않았다.** 카톡·디스코드에서 작아서 잘 보이지도 않고,
넣으려면 캔버스 PNG를 같이 올려야 해서 저장량과 `robots.txt` 예외가 함께 따라온다.

### 받는 쪽 — 가져오는 건 편성뿐

공유 페이지는 **`recDetail()`을 그대로 쓴다.** 결과 탭·기록 탭과 같은 렌더러다 —
같은 것을 두 곳에서 그리면 어느 쪽이 맞는지 매번 확인해야 한다(§3과 같은 이유).
덱마다 «이 덱 가져오기»를 얹고, 전부 가져오기는 시트로 고르게 한다.

솔로레이드는 **덱 간 중복이 불가**하므로 가져오기는 늘 남의 덱을 건드린다. 규칙 넷:

1. **충돌은 «대상이 아닌 내 다른 덱»에 같은 니케가 있을 때다.** 대상 덱은 어차피
   덮어쓰므로 세지 않는다 — 세면 「내 2덱의 앨리스를 2덱으로 가져오는데 충돌」이라는
   거짓 경고가 뜬다.
2. 충돌 니케는 원래 덱에서 **비운다. 자리를 당기지 않는다** — 누가 비었는지 보여야 한다.
3. 내 스펙에 없는 니케는 빈 자리로 두고 몇 명인지 말한다(`haveChar()` — 기록·프리셋과 같은 판정).
4. 결과 캐시는 **따로 지우지 않는다.** 지문(`fingerprint`)에 `names`가 들어 있어서
   이름이 바뀐 덱은 자동으로 «계산 안 된 덱»이 된다.

가져온 덱의 컨트롤은 비운다 — 운용을 공유하지 않으므로 «전부 자동»에서 시작한다.

---

## 6. 파일 지도

```
web/
  build.py            dist 굽기 — 자산 해시(?v=…), repo.zip, 조회표, 로스터
  server.py    1267줄 정적 서빙 + /api/fetch + /api/sim(큐·SSE) + /api/share(sqlite)
  .state/             유닛 없이 띄웠을 때의 공유 저장소 (gitignore. 운영은 /var/lib)
  src/
    index.html   580줄
    app.js      4510줄 UI 전부 — 카드·필터·정렬·덱·결과·기록·프리셋·공유·이미지 내보내기
    style.css   2150줄
    worker.js    189줄 Pyodide 부트 + run_one/convert
    bookmarklet.js     본인 계정 전용 수집기
scraper/
  profile_fetch.py    CLI 조회 (운영자 세션)
  profile_convert.py  raw → 프로필 (수집 경로 무관, 525줄)
  profile_csv.py      레츠도로 CSV → 프로필
  cdn_path.py         CDN 경로 난독화 복원
  cdn_icons.py        인게임 아이콘 추출
  cdn_costume.py      코스튬 표·스킨 그림 (표 → data/costume_index.json)
context/
  spec.py             GrowthProfile · build_squad · profile_from_dict
deploy/
  nikke-decklab.service
```

`web/dist`는 6.8MB(아이콘 62개 포함)이고 gitignore다 — 빌드 산출물이라 저장소에 안 넣는다.

---

## 7. 배포

```bash
# 1) 로컬에서 굽고 회귀 확인
python web/build.py
python -m context.snapshot     # 27/27 통과해야 한다
python -m context.doclint      # OK

# 2) 서버로 (세션 쿠키·프로필·dist는 빼고 보낸다)
tar --exclude='.git' --exclude='web/dist' --exclude='__pycache__' \
    --exclude='profiles' --exclude='.session_cookie' -czf n.tgz .
scp n.tgz ubuntu@100.85.249.28:/tmp/
ssh ubuntu@100.85.249.28 'cd ~/nikke-calc && tar -xzf /tmp/n.tgz && python3 web/build.py'

# 3) 서버 전용 꾸러미 (없으면 그 기능만 조용히 꺼진다 — 아래 §7.1)
ssh ubuntu@100.85.249.28 'cd ~/nikke-calc && pip3 install --user \
    --break-system-packages -q -r deploy/requirements-server.txt'

# 4) 재시작 + 무엇이 켜졌는지 확인
ssh ubuntu@100.85.249.28 'sudo systemctl restart nikke-decklab && sleep 3 \
    && journalctl -u nikke-decklab -n 12 --no-pager | grep -E "기능:|\[!\]"'
```

`.shots/`(시험용 캡처)는 남의 계정 화면이라 tar에서 뺀다. `profiles/`·`.session_cookie`도
마찬가지다 — 위 `--exclude`에 이미 들어 있다.

`dist`는 **서버에서 다시 굽는다** — 자산 해시가 소스에서 나오므로 양쪽이 같은 값이 된다.

### 7.1 재기동하면 저절로 뜨는가

서버를 껐다 켜도 사람 손이 필요 없어야 한다. 물려 있는 것은 넷뿐이고, **전부 부팅
때 스스로 뜬다** — 아래가 그 근거다.

| 물건 | 어떻게 살아나나 | 확인 |
|---|---|---|
| 덱 랩 서버 | `WantedBy=multi-user.target` + `Restart=always` | `systemctl is-enabled nikke-decklab` → `enabled` |
| Tailscale | 배포판 유닛이 enabled | `systemctl is-enabled tailscaled` → `enabled` |
| Funnel(443 공개) | `tailscaled.state`에 남는다 — 명령을 다시 칠 필요가 없다 | `sudo tailscale funnel status` |
| 공유 링크 DB | `StateDirectory=`가 `/var/lib/nikke-decklab`을 다시 만든다 | `/api/health`의 `share` |

**한 번 밟은 지뢰.** `~/.local`에 깐 파이썬 꾸러미는 재부팅을 넘어 살아남지만
**배포 tar에는 안 들어간다.** 서버에 OpenCV가 없어 `power_ocr`이 꺼진 채로 돌고 있었고,
`server.py`가 import 실패를 조용히 삼켜서 아무도 몰랐다. 지금은 기동할 때
`기능: … 총딜판독 꺼짐` 한 줄과 `[!] power_ocr 꺼짐 — ModuleNotFoundError: …`를 찍는다.

```bash
# 재기동 뒤 한 번에 확인
ssh ubuntu@100.85.249.28 'systemctl is-active nikke-decklab; \
    curl -s http://127.0.0.1:8766/api/health; sudo tailscale funnel status | head -3'
```

`/api/health`가 `sim·cp·ocr·power_ocr·share·fetch` 전부 `true`면 정상이다
(`lab`은 운영에서 일부러 꺼 둔다).

유닛(`deploy/nikke-decklab.service`)을 바꿨다면 복사 + `daemon-reload`가 먼저다.
`restart`만 하면 옛 유닛으로 뜨고, `StateDirectory`가 없어 **공유만 조용히 꺼진다**
(`/api/health`의 `share: false`로 알 수 있다).

```bash
ssh ubuntu@100.85.249.28 'sudo cp ~/nikke-calc/deploy/nikke-decklab.service \
    /etc/systemd/system/ && sudo systemctl daemon-reload'
```

### 주소

Tailscale Funnel이 443 → `127.0.0.1:8766`으로 넘긴다. 무료이고 **고정**이다 —
`nikkedeck`(머신 이름) + `tetra-pantone`(tailnet 이름)에서 만들어지므로 재시작·재부팅·IP
변경으로 바뀌지 않는다. 클라우드플레어 **임시** 터널(`trycloudflare`)은 뜰 때마다 이름이
새로 붙어서 못 쓴다.

```bash
sudo tailscale funnel --bg 8766       # 설정은 tailscaled 상태에 남아 재부팅을 넘긴다
sudo tailscale funnel status
```

### 상시 구동

`deploy/nikke-decklab.service` — 부팅 자동시작, 크래시 시 3초 뒤 재시작, 파일시스템
읽기 전용, 권한 없음, `PrivateTmp`. 이 서비스는 파일을 안 쓰기 때문에 쓰기 경로를
하나도 열지 않았다(`PYTHONDONTWRITEBYTECODE=1`로 `__pycache__`도 막는다).

---

## 8. 보안

자세한 것은 [PRIVACY.md](PRIVACY.md). 요점만:

- 서버는 **127.0.0.1에만 바인딩**한다. 밖에서 오는 길은 Funnel 하나뿐이고, Funnel은
  serve 설정에 적힌 `/ → 8766` 한 줄만 프록시한다. SSH·다른 포트로 가는 길이 없다.
- `/api/fetch`는 URL을 받는 것처럼 보이지만 **숫자 openid만** 뽑아 하드코딩된 주소로
  간다 — SSRF로 내부 포트를 찌를 수 없다.
- CSP·`nosniff`·`no-referrer`·`frame-ancestors 'none'`. 인라인 script/style이 없어서
  좁게 잡을 수 있었다.
- 예상 못 한 예외는 경로를 흘릴 수 있어 로그에만 남기고 밖으로는 일반 문구만 보낸다.
- **방문자를 구분하지도 기록하지도 않는다.** IP 기반 제한을 쓰지 않고, 접근 로그에도
  주소를 남기지 않는다. 대신 서버가 자기 호출을 조인다 — 블라링크 호출 사이 1.5초
  간격(`API_GAP`). 조이는 자리가 «조회 1건»이 아니라 **실제 요청 하나하나**인 게
  핵심이다: 조회 한 번이 요청 6건이라, 조회 단위로만 조이면 그 6건이 그대로 몰아친다.
- `/api/fetch`는 이 사이트 페이지에서 온 요청만 받는다(`Sec-Fetch-Site`). `robots.txt`와
  `<meta name="robots">`로 검색·AI 수집을 거부한다.

---

## 9. 밟은 지뢰 (다시 밟지 않으려고 적어 둔다)

**`not enough values to unpack (expected 6, got 4)`** — 워커는 디스크에서 새로
임포트되는데 부모는 메모리에 남은 옛 코드로 작업을 만든다. 필드를 하나 늘리는 순간
「코드는 맞는데 서버만 고장 난」 상태가 된다. 작업을 **dict로** 넘기게 바꿨다(없는 키는
None이 될 뿐이다) + 옛 튜플 호환 처리.

**Python 문서 문자열의 백틱** — `worker.js`의 파이썬 코드는 JS 템플릿 리터럴 안에 있다.
독스트링에 백틱을 쓰면 리터럴이 끊겨 `Unexpected identifier`가 난다.

**HTTP/1.0이 기본** — `SimpleHTTPRequestHandler`는 응답마다 연결을 끊는다. 아이콘을
받는 첫 방문이 눈에 띄게 느렸다. `protocol_version = "HTTP/1.1"`로 올렸는데, 그러면
**본문을 안 읽고 일찍 응답하는 경로**(429 등)의 남은 바이트가 다음 요청으로 파싱돼
엉뚱한 501이 난다. 안 읽은 본문을 버리는 처리가 같이 필요하다.

**터널 뒤의 클라이언트 IP** — `client_address`는 언제나 루프백이라 레이트리밋이 전역이
된다. `X-Forwarded-For`의 **마지막** 항목을 쓴다(앞쪽은 클라이언트가 지어낼 수 있다).
소켓 상대가 루프백일 때만 믿는다.

**그리고 그걸 tailnet 안에서 시험하면 틀린 답이 나온다** — 이 PC가 tailnet에 있으면
공개 주소로 불러도 Tailscale이 지름길로 붙여서, 서버에는 **내 기기의 100.x 주소**와
`Tailscale-User-Login`(계정 이메일)이 도착한다. 그걸 보고 「Funnel은 방문자 IP를 안
넘겨준다」고 결론지었는데 틀렸다. **tailnet 밖에서** 부르면
`Tailscale-Funnel-Request: ?1`과 함께 진짜 공인 IP가 온다. 공개 경로를 검증할 때는
반드시 tailnet 밖(다른 회선·외부 fetch 서비스)에서 재야 한다.

**`pkill -f "web/server.py"`가 자기를 죽인다** — ssh로 실행하는 명령줄 자체에 그
문자열이 들어 있어 패턴에 걸린다. pid를 먼저 뽑아 죽여야 한다.

**`systemctl restart tailscaled`는 내 SSH를 끊는다** — Tailscale로 붙어 있으니 당연하다.

**임시 터널 pid를 잘못 짚으면 주소가 영영 사라진다** — `trycloudflare` 주소는 재발급이
안 된다. 애초에 고정 주소(Funnel)로 갔어야 했다.

**정렬 방향의 뜻** — 숫자 정렬 비교기가 이미 내림차순인데 그 위에 `asc`를 또 적용하면
▼인데 작은 값이 위로 온다. 비교기는 **언제나 오름차순**으로 두고 방향은 `asc` 하나가
정하게 한다. 「큰 값부터가 자연스럽다」는 정렬을 **고르는 자리**에서 기본값으로 준다.

**라이트 토큰이 다크 존에 남는다** — 계정 탭을 다크로 옮길 때 `--color-roster-ink-2`
(L 0.52)가 몇 군데 남았다. 밝은 바탕에서 「조용한 회색」이던 값이 L 0.26 바탕에서는
배경과 본문 사이에 끼어 안 보인다. 게다가 `.stage-solo .prof-notice`가 뒤에 와서
`.prof-notice.warn`의 주황을 덮어 **경고가 회색이 됐다.**

---

## 10. 확인 명령

```bash
# 계산이 안 바뀌었나 (프로필은 하네스에 절대 안 들어간다)
python -m context.snapshot          # 27/27

# 문서가 지목한 파일·함수가 실재하나
python -m context.doclint

# 브라우저 = 서버 일치 (기대값 모드는 결정론적이라 완전히 같아야 한다)
python -m context.sim "<5인>" --profile me --expected

# 공유 저장소가 열렸나 (거짓이면 StateDirectory가 안 잡힌 것)
curl -s https://nikkedeck.tetra-pantone.ts.net/api/health | grep -o '"share": [a-z]*'

# 서비스
ssh ubuntu@100.85.249.28 'systemctl status nikke-decklab'
ssh ubuntu@100.85.249.28 'sudo tailscale funnel status'
curl https://nikkedeck.tetra-pantone.ts.net/api/health
```
