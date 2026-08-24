# blablalink 데이터 수집 기록

니케 정보 계열만 정리한다. 소셜(게시글/팔로우/좋아요) 계열은 목록만 남기고 파고들지 않았다.

## 1. 두 개의 데이터 경로

| 경로 | 용도 | 인증 |
| --- | --- | --- |
| `https://api.blablalink.com/api/...` | 계정·길드·유니온레이드 등 동적 데이터 | 쿠키(로그인) |
| `https://sg-tools-cdn.blablalink.com/...` | 캐릭터·스테이지·시즌 등 정적 테이블 | 불필요 |

### 1-1. API 호출 규약

```
POST https://api.blablalink.com/api/game/proxy/Game/<Method>
Content-Type: application/json
X-Channel-Type: 2
X-Language: ko
credentials: include        # 쿠키 인증
body: {...}                 # 파라미터 없으면 {}
```

응답은 항상 `{code, code_type, msg, data, seq}`. `code:0`이 성공,
`1303001`은 파라미터 누락, `999`는 서버 내부 오류(대개 파라미터 형식 불일치).

프런트 번들에서는 base 팩토리 + 메서드명 조합으로 만든다.
`h = a("/game/proxy/Game")` → `h("/GetGuildMembers")`.
그래서 URL 문자열을 통째로 grep하면 안 잡힌다.

### 1-2. CDN 경로 난독화

평문 경로만 알면 URL이 결정된다. `scraper/cdn_path.py`가 재현한다.

- 디렉토리 세그먼트 → `djb2(평문_전체_경로, LARGE_PRIMES[i])` 기반 `xx-99` 토큰
- 파일명 → `md5(평문_전체_경로)` + 원래 확장자

언어 토큰 치환 규칙(`getLFormatLangUrl`):

1. `{lang}` → `ko`
2. `{l_lang}` → `ko`
3. **locale이 `ko`면 경로 전체에서 `_ko`를 제거**

즉 `/character/{l_lang}/nikke_list_{lang}_v2.json` → `/character/ko/nikke_list_v2.json`.
이 3번 규칙을 몰라서 초기에 404가 났었다.

## 2. 니케 정보 API (`/api/game/proxy/Game/`)

| 메서드 | 파라미터 | 내용 |
| --- | --- | --- |
| `GetUserProfileBasicInfo` | - | 프로필 기본 정보 |
| `GetUserProfileOutpostInfo` | - | 전초기지 정보 |
| `GetUserCharacters` | - | 보유 니케 목록 |
| `GetUserCharacterDetails` | - | 니케 상세(레벨/돌파/스킬) |
| `GetUserDailyContentsProgress` | - | 일일 콘텐츠 진행도 |
| `GetRoleList` | - | 계정 캐릭터(서버) 목록 |
| `GetSavedRoleInfo` / `SaveRoleInfo` | - | 연동 캐릭터 저장 |
| `GetCampaignStageCharacterInfo` | - | 캠페인 스테이지 편성 |
| `GetMainQuestClearLineup` | - | 메인 퀘스트 클리어 편성 |
| `GetMyGuildInfo` | `{latest}` | 내 유니온 |
| `GetGuildDetail` | `{guild_id}` | 유니온 상세 |
| `GetGuildMembers` | `{guild_id, nikke_area_id}` | 유니온 멤버 |
| `QueryGuildCards` / `PublishGuildCard` / `SupportGuild` / `JoinGuild` / `SetGuildInfo` | - | 유니온 모집 |
| `GetUnionRaidData` | `{guild_id, nikke_area_id, intl_open_id}` | **진행 중** 시즌 참여 기록 |
| `GetUnionRaidLevelInfo` | `{guild_id, nikke_area_id, intl_open_id}` | **진행 중** 시즌 보스/HP |
| `GetUnionRaidDataOfGuildSeason` | `{area_id, guild_id, season_id}` | **지난** 시즌 참여 기록 |
| `GetUnionRaidLevelDataOfGuildSeason` | `{area_id, guild_id, season_id}` | **지난** 시즌 보스/HP |
| `ShiftypadBindGameMissionStatus` | `{}` | 연동 미션 상태 |
| `GetCdkRedemption` / `GetCdkRedemptionHistory` / `RecordCdkRedemption` | - | 쿠폰 |

`/api/game/proxy/Tools/` : `GetNikkesOrder`, `GetUserNikkesOrder`, `SaveNikkesOrder`, `GetUserSavedRoleInfo`
`/api/game/direct/Game/` : `GetCdkRedemption`, `QueryGuildCardList`, `QueryGuildCardsByTourist`, `QueryGuildCardSupportersByTourist` (비로그인 가능)

전체 209개 엔드포인트는 `api_endpoints.json`에 있다. `ugc/*/standalonesite`는 커뮤니티(소셜) 계열이다.

## 3. CDN 정적 테이블

| 평문 경로 | 내용 |
| --- | --- |
| `/character/ko/nikke_list_v2.json` | 니케 199종. 속성/무기/기업/코스튬 |
| `/character/character_id_map.json` | 캐릭터 ID 매핑 |
| `/character/character_skill_map.json` | 스킬 매핑 |
| `/character/character_avatar_map.json` | 아바타 매핑 |
| `/character/CharacterLevelTable.json` | 레벨별 스탯 |
| `/character/AttractiveLevelTable.json` | 호감도 |
| `/character/RecycleResearchStatTable.json` | 리사이클 연구 |
| `/character/scene_characeter_list_v2.json` | 스토리 등장 캐릭터 |
| `/character/ko/character_face_list.json` | 표정 |
| `/equip/ItemEquipTable-ko.json` | 장비 |
| `/equip/equip_option_table_v2-ko.json` | 장비 옵션 |
| `/equip/favorite_rare_map.json` | 애장품 |
| `/stage/stage_list.json` | 캠페인 |
| `/tower/tower_list.json` | 타워 |
| `/raid/raid_list.json` | **유니온 레이드 시즌 목록** |
| `/guild/guild_emblem.json` | 유니온 엠블럼 |
| `/archive/ko/archive_list.json` | 아카이브 |
| `/scene/ko/scene_list.json` | 스토리 |
| `/scene/ko/sudden_list.json` | 돌발 |

이미지 URL 빌더(같은 CDN, 평문 경로만 다름):

- `/character/full/c{resource_id:03}_{skin:02}.webp`
- `/character/mi/mi_c{resource_id:03}_{skin:02}_s.webp`
- `/character/si/si_c{resource_id:03}_{skin:02}_s.webp`
- `/icon/{path}/{name}.webp`
- `/background/{path}/{name}.webp`
- `/schedule/banner/{name}.webp`
- `/voice/{cv_lang}/{speech_id}.wav`

**보스(랩처) 이미지는 blablalink CDN에 없다.** 유니온 레이드 UI는 이름과 HP 바만 그린다.
응답의 `monster_model_id`(예: `252002`)와 `icon_id`(예: `ebg002`)에 대응하는 이미지 경로가
번들 어디에도 없다.

## 4. 유니온 레이드 구조 (실측)

`union_raid_seasons.json`에 시즌 S35~S43(=`season_id` 1000035~1000043) 정리.
원본 응답은 `api/unionraid_<season_id>_{raid,level}.json`.

### 4-1. 레벨·보스 구성

```
NORMAL  Lv1 ~ Lv10   각 레벨마다 보스 5기 (step 1~5)
HARD    Lv1 ~ Lv3    각 레벨마다 보스 5기 (step 1~5)
HARD    Lv4          보스 1기 (step 6) — max_hp = 0 (무한)
```

- 5기의 속성은 매 시즌 **5속성이 정확히 하나씩** 배정된다. 순서는 시즌마다 다르다.
- 같은 시즌 안에서는 레벨이 올라가도 **보스 라인업이 바뀌지 않는다.** HP만 오른다.
- `step 6`(HARD Lv4) 보스는 `step 5`와 동일한 몬스터다. 즉 **마지막 4단계는 그 시즌 5번째 속성 고정**.
- S35~S40은 유니온이 HARD Lv3까지만 갔고, S41부터 Lv4 기록이 있다.

### 4-2. 응답 필드

`GetUnionRaidDataOfGuildSeason.data.participate_data[]` — 공격 1회당 1건:

```jsonc
{
  "openid": "16520388401939496294",  // 유저 식별자
  "nickname": "...",
  "boss_id": "2520020144",
  "monster_model_id": "252002",
  "icon_id": "ebg002",
  "name_localvalues": {"ko": "...", "en": "...", "ja": "...", "zh-tw": "..."},
  "element_id": ["400001"],
  "difficulty": 1,                   // 1=NORMAL, 2=HARD
  "level": 10,
  "step": 5,                         // 보스 순번 1~5 (Lv4는 6)
  "day": 0,
  "is_final_hit": true,
  "total_damage": "1569579475",
  "squad": [                         // 그 판에 쓴 덱
    {"slot": 1, "tid": 201605, "lv": 673, "combat": 495638, "costume_id": 30050}
  ]
}
```

`GetUnionRaidLevelDataOfGuildSeason.data`는 `level_info[]`(현재 도달 레벨의 `boss_info` +
`current_hp`/`max_hp`)와 `manager_info`(시즌 일정, `monster_preset`)를 준다.
`max_hp: "0"`이면 무한 HP(=Lv4).

### 4-3. 속성 ID

`nikke_list_v2.json`의 `element_id.element`에서 확인.

| id | 영문 | 한글 |
| --- | --- | --- |
| 100001 | Fire | 작열 |
| 200001 | Water | 수냉 |
| 300001 | Wind | 풍압 |
| 400001 | Electronic | 전격 |
| 500001 | Iron | 철갑 |

보스 코드명이 속성을 그대로 가리킨다: `H.S.T.A.`=작열, `P.S.I.D.`=수냉,
`A.N.M.I.`=풍압, `Z.E.U.S.`=전격, `D.M.T.R.`=철갑.

## 5. 파일

| 파일 | 내용 |
| --- | --- |
| `crawl_api.py` | 프런트 번들 전체(910청크) 크롤 |
| `extract_endpoints.py` | base 팩토리 + 메서드 조합 복원 |
| `api_endpoints.json` | 엔드포인트 209개 |
| `api_inventory.json` | 원시 문자열 추출 결과 |
| `fetch_log.json`, `fetch_log2.json` | CDN 평문경로 → URL → 바이트수 |
| `json/` | 받아둔 CDN 정적 테이블 |
| `api/unionraid_*.json` | 유니온 레이드 시즌별 원본 응답 |
| `union_raid_seasons.json` | 시즌별 보스/속성/레벨 정리 |

## 6. 주의

- 호출은 천천히. 초당 여러 건씩 때리지 말 것.
- `openid`는 유저 식별자다. 공개 자료에 그대로 싣지 말 것.
- 여기 담긴 유니온 데이터는 로그인 계정이 속한 유니온(UID 6455) 것이다.
