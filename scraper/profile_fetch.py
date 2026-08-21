"""blablalink 로그인 세션으로 '내 계정의 실제 육성 스펙'을 받아 **육성 프로필**로 변환한다.

CDN 수집기(`cdn_fetch.py`·`cdn_tables.py`)와 성격이 완전히 다르다. 저쪽은 모두가 공유하는
게임 마스터 데이터를 커밋 대상으로 갱신하고, 이쪽은 **한 계정의 사적인 육성 상태**를 로컬
전용으로 만든다. 절차·gate는 `.agent/skills/profile-sync/SKILL.md`.

브라우저 없음. `scraper/.session_cookie`(gitignore)의 쿠키만 읽어 순수 HTTP로 돈다.
쿠키 확보는 최초 1회 브라우저 로그인 → game_token 등 추출.

사용법:
    python scraper/profile_fetch.py                 # 쿠키의 game_openid = 내 계정 → profiles/me.json
    python scraper/profile_fetch.py --name 부계     # 프로필 이름 지정
    python scraper/profile_fetch.py --openid 1234…  # 특정 openid (타인, 세션은 여전히 내 것)
    python scraper/profile_fetch.py --area 83       # nikke_area_id (기본: 자동 탐색)

출력(gitignore):
    profiles/<이름>.json        육성 프로필 — 러너가 `--profile <이름>`으로 읽는다
    profiles/<이름>.raw.json    원시 응답(캐릭터+상세+옵션 사전). 재변환·감사용

**프로필에 넣지 않는 것**
- 큐브: API는 *장착 중인* 큐브만 준다. 큐브는 인게임에서 자유롭게 갈아끼우므로 육성 상태가
  아니라 **케이스가 정하는 축**이다. 관찰된 큐브는 `_account.cubes`에 보유 하한으로만 적는다.
- 콘솔(공통/클래스/기업): 이 두 엔드포인트가 주지 않는다. `_account.console`에 손으로 적는다
  (계정 단위라 한 곳이면 된다). 기존 값이 있으면 재수집해도 보존한다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")   # 한글 진단 메시지가 콘솔 코드페이지로 깨지지 않게

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROFILE_DIR = os.path.join(ROOT, "profiles")
sys.path.insert(0, HERE)
import cdn_path  # noqa: E402

API = "https://api.blablalink.com/api/game/proxy/"
COMMON = {"game_id": "29080", "area_id": "global", "source": "pc_web",
          "intl_game_id": "29080", "language": "ko", "env": "prod"}

# 오버로드 옵션 function_type(응답 state_effects) → 계산기 equip_skills 키.
# 값은 모두 |value|/100 = 퍼센트(차지시간 감소는 음수라 절대값). 미지의 타입은 경고.
# 매핑이 맞는지는 이름만으로 믿지 않고 `_verify_option`이 수치로 교차검증한다.
FUNC_TO_EQUIP = {
    "StatAtk": "atk_pct",
    "IncElementDmg": "element_bonus",
    "StatAmmoLoad": "max_ammo_pct",
    "StatCritical": "crit_rate",
    "StatCriticalDamage": "crit_dmg",
    "StatChargeTime": "charge_speed_pct",
    "StatChargeDamage": "charge_dmg_pct",
    "StatAccuracyCircle": "accuracy_pct",
    "IncHurtDef": "def_pct",
    "StatDef": "def_pct",
}
EQUIP_KEYS = ["atk_pct", "element_bonus", "max_ammo_pct", "crit_rate", "crit_dmg",
              "charge_speed_pct", "charge_dmg_pct", "accuracy_pct", "def_pct"]
# **줄 단위로 적어야 하는** 옵션. 최대 장탄·차지 속도는 인게임이 옵션 단계마다 따로
# 반올림해 더하므로(GAMEPLAY.md §무기 메카닉) 합산 스칼라로 뭉개면 단계가 섞인 장비에서
# 발수·차지 시간이 어긋난다. 그래서 이 둘만 줄별 퍼센트 리스트로 낸다 —
# 계산기(`buff_manager._equip_option_groups`)가 같은 값끼리 묶어 그룹을 만든다.
PER_LINE_KEYS = {"max_ammo_pct", "charge_speed_pct"}
PARTS = [("head", "머리"), ("torso", "몸통"), ("arm", "팔"), ("leg", "다리")]

NO_ITEM = "없음"          # calculator.base_stat.NO_ITEM — 미장착
OVERLOAD_TIER = 10        # equip_tier 10 = 오버로드 장비(강화 0~5), 1~9 = T1~T9 등급

# 장비 갈래 표식. `_track` 값은 `cost/tables.json` 장비강화.레벨도달비용의 키와 **같아야**
# 한다 — 웹앱 육성 탭이 이 값으로 강화 한 칸의 비용을 고른다(한 칸이 1.5배 갈린다).
#   tier 10                     → 오버로드 장비 (계산기의 `기업` 표가 이 갈래다)
#   tier 9 + corporation_type≠0 → T9 기업 장비 (역시 강화 0~5, 계산기에 표가 없다)
#   tier 1~9 + corporation_type 0 → 일반 장비 (강화 없음)
TRACK_OVERLOAD = "오버로드"
TRACK_CORP = "T9"

# `{부위}_equip_corporation_type` = 그 장비가 주는 **제조사 보너스**(blablalink 프론트의
# `equip_manufacturer_bonus`). 캐릭터 기업과 같을 때만 붙는다 — 실측 41부위 전부 일치했다.
# 값은 재활용 연구실 tid 순서와 같고(1201~1204), 어브노말만 7이다(사이트 번들 기준, 미관측).
# 글자는 `parsed_nikke.json`의 `manufacturer`와 같아야 한다.
GEAR_CORPS = {1: "엘리시온", 2: "미실리스", 3: "테트라", 4: "필그림", 7: "어브노말"}

# 재활용 연구실(= 계산기의 "콘솔") tid → 소속. 1001 공통 하나 · 11xx 역할군 셋 · 12xx 기업 다섯.
# 소속 순서는 인게임 재활용 연구실 표시 순서이며, `parsed_nikke.json`의 `class`·`manufacturer`
# 값과 글자까지 같아야 한다 (계산기가 그 문자열로 조회한다).
# 검산: API `GetUserProfileBasicInfo`의 corporation_character_counts가 기업별 보유 수를 주고,
# 그게 parsed_nikke의 manufacturer 분포와 맞아떨어진다.
CONSOLE_TIDS = {
    1001: ("common_level", ""),
    1101: ("class_level", "화력형"), 1102: ("class_level", "방어형"),
    1103: ("class_level", "지원형"),
    1201: ("company_level", "엘리시온"), 1202: ("company_level", "미실리스"),
    1203: ("company_level", "테트라"), 1204: ("company_level", "필그림"),
    1205: ("company_level", "어브노말"),
}


# ── HTTP ──────────────────────────────────────────────────────────────────
def _load_cookie() -> str:
    p = os.path.join(HERE, ".session_cookie")
    if not os.path.exists(p):
        sys.exit("[!] scraper/.session_cookie 없음 — 최초 로그인으로 쿠키를 넣어라 "
                 "(.agent/skills/profile-sync/SKILL.md §쿠키 확보).")
    c = open(p, encoding="utf-8").read().strip()
    if "game_token=" not in c:
        sys.exit("[!] .session_cookie 에 game_token 이 없다 — 로그인 세션 쿠키 전체를 넣어라.")
    return c


def _openid_from_cookie(cookie: str) -> str:
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("game_openid="):
            return part.split("=", 1)[1]
    sys.exit("[!] 쿠키에 game_openid 없음 — --openid 로 직접 지정하라.")


def _post(route: str, body: dict, cookie: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0 Safari/537.36",
        "Content-Type": "application/json", "Origin": "https://www.blablalink.com",
        "Referer": "https://www.blablalink.com/", "Accept": "application/json, text/plain, */*",
        "X-Channel-Type": "2", "X-Language": "ko",
        "X-Common-Params": json.dumps(COMMON), "Cookie": cookie,
    }
    req = urllib.request.Request(API + route, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": "HTTP " + str(e.code),
                "_raw": e.read().decode("utf-8", "replace")[:300]}


def _check(resp: dict, what: str) -> dict:
    if resp.get("code") != 0:
        if resp.get("code") == 300001:
            sys.exit(f"[!] {what}: 로그인 세션 만료 (game not login). 쿠키를 다시 받아라.")
        sys.exit(f"[!] {what} 실패: {json.dumps(resp, ensure_ascii=False)[:300]}")
    return resp["data"]


def _cdn_json(path: str):
    req = urllib.request.Request(cdn_path.url(path), headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))


# ── 매핑 로드 ─────────────────────────────────────────────────────────────
def _fetch_id_map() -> dict:
    """CDN character_id_map.json → {name_code: resource_id}."""
    m = {}
    for row in _cdn_json("/character/character_id_map.json"):
        m.setdefault(row["name_code"], row["resource_id"])
    return m


def _fetch_favorite_map() -> dict:
    """CDN favorite_rare_map + favorite_{id} → {아이템 id: (등급, 무기군)}.

    등급은 소장품 `R`/`SR`과 애장품 `SSR`. **SSR 애장품은 플랫 스탯도 소장품 스킬 레벨도
    SR15와 완전히 같으므로**(favorite_{id}.json: atk·hp·def 배열이 단계와 무관하게 SR15 값,
    `level1`=4) 계산기에는 `SR15`로 적는다. 애장품이 바꾸는 건 캐릭터 스킬 쪽이고, 그건
    단계를 `favorite_stage`로 따로 넘겨 계산기가 스킬 판본을 고르게 한다.
    """
    rare = _cdn_json("/equip/favorite_rare_map.json")
    out = {}
    for grade in ("R", "SR", "SSR"):
        for fid in rare.get(grade, []):
            d = _cdn_json(f"/equip/ko/favorite_{fid}.json")
            out[d["id"]] = (d["favorite_rare"], d["weapon_type"])
    return out


def _load_resource_name_map() -> dict:
    """nikke_scraped.json → {resource_id: 우리 캐릭명}."""
    d = json.load(open(os.path.join(HERE, "nikke_scraped.json"), encoding="utf-8"))
    return {v["id"]: name for name, v in d.items() if isinstance(v, dict) and "id" in v}


def _load_weapon_map() -> dict:
    """parsed_nikke.json → {우리 캐릭명: 무기군}. 소장품 무기군 대조용."""
    d = json.load(open(os.path.join(ROOT, "data", "parsed_nikke.json"), encoding="utf-8"))
    return {n: v.get("weapon_type") for n, v in d.items() if isinstance(v, dict)}


def _load_manufacturer_map() -> dict:
    """parsed_nikke.json → {우리 캐릭명: 기업}. T9 기업 장비 제조사 대조용."""
    d = json.load(open(os.path.join(ROOT, "data", "parsed_nikke.json"), encoding="utf-8"))
    return {n: v.get("manufacturer") for n, v in d.items() if isinstance(v, dict)}


def _load_favorite_chars() -> set:
    """parsed_nikke.json → 애장품이 **있는** 캐릭터 이름 집합.

    이 집합에만 `favorite_stage`를 적는다. 애장품이 없는 캐릭터에 0을 적으면 계산에는
    영향이 없으면서 이탈 보고만 지저분해진다.
    """
    d = json.load(open(os.path.join(ROOT, "data", "parsed_nikke.json"), encoding="utf-8"))
    return {n for n, v in d.items() if isinstance(v, dict) and v.get("favorite_slots")}


def _load_cube_name_map() -> dict:
    """cube.json → {cube id: 큐브명}."""
    d = json.load(open(os.path.join(ROOT, "data", "base_stat_tables", "cube.json"),
                       encoding="utf-8"))
    return {v["id"]: name for name, v in d.items() if isinstance(v, dict) and "id" in v}


def _load_equip_skill_table() -> dict:
    d = json.load(open(os.path.join(ROOT, "data", "base_stat_tables", "equipment_skills.json"),
                       encoding="utf-8"))
    return {k: v["values"] for k, v in d.items() if not k.startswith("_")}


# ── 변환 ──────────────────────────────────────────────────────────────────
def _verify_option(key: str, val: float, table: dict) -> int | None:
    """옵션 값이 그 스탯 표의 어느 레벨인지. 표에 없으면 None (= 매핑 의심).

    `FUNC_TO_EQUIP`은 게임 내부 이름 → 우리 키라 이름만으로는 틀렸는지 알 수 없다.
    옵션 수치는 반드시 `equipment_skills.json`의 15단계 중 하나이므로, 그걸로 교차검증한다.
    (여러 스탯이 같은 수열을 쓰므로 이 검사는 오탐이 아니라 누락을 잡는 용도다.)
    """
    for lv, v in enumerate(table.get(key, []), start=1):
        if abs(v * 100 - val) < 1e-6:
            return lv
    return None


def _build_option_map(state_effects: list, skill_table: dict) -> tuple[dict, dict, list]:
    """state_effects 전체 → {option_id: (equip_skills 키, 퍼센트값)}.

    반환: (옵션 사전, 미지 function_type, 표에 없는 수치 목록)
    """
    opt, unknown, off_table = {}, {}, []
    # 배치마다 겹쳐 오므로 옵션 id로 중복 제거한다 (같은 경고가 배치 수만큼 뜨지 않게).
    for se in {s["id"]: s for s in state_effects}.values():
        fd = se["function_details"][0]
        ftype = fd["function_type"]
        key = FUNC_TO_EQUIP.get(ftype)
        val = abs(fd["function_value"]) / 100.0
        if key is None:
            unknown[ftype] = se["id"]
            continue
        if _verify_option(key, val, skill_table) is None:
            off_table.append((ftype, key, val, se["id"]))
        opt[str(se["id"])] = (key, val)
    return opt, unknown, off_table


def _equipment(detail: dict) -> dict:
    """장비 4부위 → 계산기 `equipment` (+ 갈래 표식).

    빈 슬롯은 `tier: "없음"`이다. **기업 강화0으로 적으면 안 된다** — 그건 "가장 낮은 장착
    상태"라 부위당 수천 atk이 유령으로 붙는다(방어형 머리 기준 +4010).

    `_`로 시작하는 키는 **계산에 들어가지 않는다** — `spec.deep_merge()`가 걸러내고
    `GROWTH_KEYS` 검사도 통과한다. 웹앱 육성 탭이 강화 비용 갈래를 고르는 데만 쓴다.

    T9 기업 장비는 `corp`(장비 제조사)와 `level`(강화)을 함께 적는다 — 계산기가
    `기본값 × (1 + 0.3×기업일치 + 0.1×강화)`로 쓴다(`calculator.base_stat._equip_stat`).
    제조사가 캐릭터 기업과 다르면 보너스가 안 붙으므로 **장비 쪽 제조사를 그대로** 적는다.
    """
    out = {}
    for api_p, ko_p in PARTS:
        tier = detail[f"{api_p}_equip_tier"]
        corp = detail.get(f"{api_p}_equip_corporation_type", 0)
        lv = detail[f"{api_p}_equip_lv"]
        if tier >= OVERLOAD_TIER:
            out[ko_p] = {"level": lv, "_track": TRACK_OVERLOAD}  # 오버로드 (강화 0~5)
        elif tier >= 1 and corp:
            out[ko_p] = {"tier": f"T{tier}", "level": lv,        # T9 기업 (강화 0~5)
                         "corp": GEAR_CORPS.get(corp, f"?{corp}"),
                         "_track": TRACK_CORP}
        elif tier >= 1:
            out[ko_p] = {"tier": f"T{tier}"}                     # 일반 T1~T9 (강화 없음)
        else:
            out[ko_p] = {"tier": NO_ITEM}                        # 미장착
    return out


def _equip_skills(detail: dict, opt_map: dict) -> dict:
    """오버로드 12슬롯 → 계산기 `equip_skills`.

    `state_effects`는 옵션 id로 **중복 제거**되어(같은 옵션이 2부위면 1번만 등장) 합산에 못
    쓴다. 그래서 슬롯을 직접 순회하고 `state_effects`는 옵션id → 스탯 사전으로만 쓴다.

    `PER_LINE_KEYS`(최대 장탄·차지 속도)는 **줄별 퍼센트 리스트**로, 나머지는 합산
    스칼라로 낸다 — 앞의 둘만 단계별로 따로 반올림되기 때문이다.
    """
    out: dict = {k: [] if k in PER_LINE_KEYS else 0.0 for k in EQUIP_KEYS}
    for api_p, _ in PARTS:
        for i in (1, 2, 3):
            oid = str(detail[f"{api_p}_equip_option{i}_id"])
            if oid in opt_map:
                key, val = opt_map[oid]
                if key in PER_LINE_KEYS:
                    out[key].append(round(val, 4))
                else:
                    out[key] = round(out[key] + val, 4)
    # 줄별 리스트는 큰 단계부터 — 사람이 읽을 때 주력 옵션이 먼저 오게 한다.
    # 그룹은 값으로 묶이므로 순서는 결과에 영향을 주지 않는다.
    for k in PER_LINE_KEYS:
        out[k].sort(reverse=True)
    return out


def _collection(detail: dict, fav_map: dict, name: str, weapon: str | None,
                warn: list) -> tuple[str, int]:
    """소장품 슬롯 → (`collection_stage`, 애장품 단계).

    슬롯 하나를 소장품(R·SR)과 애장품(SSR)이 공유한다. 애장품은 SR15와 스탯이 같으므로
    `SR15`로 적고, **단계**만 따로 돌려준다 — 단계는 스탯이 아니라 스킬 판본을 바꾸며
    계산기가 `favorite_stage`로 그대로 받는다(`calculator/buff_manager.char_effects()`).
    소장품(R·SR)을 꼈거나 슬롯이 비었으면 애장품 0단계다.
    """
    tid = detail.get("favorite_item_tid", 0)
    lv = detail.get("favorite_item_lv", 0)
    if not tid:
        return NO_ITEM, 0
    info = fav_map.get(tid)
    if info is None:
        warn.append(f"{name}: 모르는 소장품 id {tid} — 미장착으로 처리")
        return NO_ITEM, 0
    grade, fav_weapon = info
    if grade == "SSR":
        return "SR15", lv + 1            # favorite_item_lv 0/1/2 = 단계 1/2/3
    if weapon and fav_weapon != weapon:
        warn.append(f"{name}: 소장품 무기군 불일치 ({fav_weapon} 장착, 캐릭터는 {weapon}) "
                    f"— 계산기는 캐릭터 무기군 효과로 계산한다")
    return f"{grade}{lv}", 0


def _to_profile(detail: dict, eff: dict, opt_map: dict, fav_map: dict,
                name: str, weapon: str | None, warn: list,
                has_favorite: bool = False) -> dict:
    """`eff` = GetUserCharacters 항목(유효 레벨·돌파·코강 = 동기화 반영값).
    상세의 lv는 개별 레벨이라 동기화 소대에 덮이지 않은 원값이므로 쓰지 않는다."""
    stage, fav_stage = _collection(detail, fav_map, name, weapon, warn)
    # **`level`은 담지 않는다.** 인게임 캐릭터 레벨은 동기화 소대에 넣었는지에 달려 있어
    # 육성 상태라기보다 편성 상태고, 솔로레이드는 레벨이 400으로 고정된다. 그래서 레벨은
    # 프로필이 아니라 **정책**으로 정한다 — 기본은 기본 스펙 레벨(400), `sync`면 동기화 소대
    # 레벨. 돌파·코강은 레벨과 달리 고정되지 않으므로 실제 값을 그대로 쓴다.
    out = {
        "breakthrough": eff["grade"],
        "core_enhancement": eff["core"],
        "affinity": max(1, detail["attractive_lv"]),   # 호감도 표는 1부터 (미투자 0 → 1로)
        "skill_levels": {"1": detail["skill1_lv"], "2": detail["skill2_lv"],
                         "3": detail["ulti_skill_lv"]},
        "equipment": _equipment(detail),
        "equip_skills": _equip_skills(detail, opt_map),
        "collection_stage": stage,
    }
    if has_favorite:
        # 애장품이 있는 캐릭터만. 단계가 스킬 판본을 정하므로 계산에 직접 들어간다.
        out["favorite_stage"] = fav_stage
    if _unsynced(eff):
        out["_unsynced"] = True          # 참고용. 레벨은 정책이 정하므로 계산에는 영향이 없다
    return out


def _unsynced(c: dict) -> bool:
    """동기화 소대 밖이라 인게임 레벨이 안 오른 항목인가 (`lv <= 1`).

    **보유 판정이 아니다.** 실측(2026-08-15): 로스터 192종 중 21종이 lv 1인데 그 수가 전초기지
    `synchro_nonempty_slot_count`(171)의 여집합과 정확히 일치하고, 유저 확인 결과 **전원 보유
    중**이다(소대에 안 넣었을 뿐). 그래서 로스터 전원을 프로필에 담고 이 플래그만 세운다.

    레벨은 프로필이 아니라 정책으로 정하므로(§`_to_profile`) 이 플래그는 계산에 영향이 없다.
    돌파·스킬 레벨이 낮은 이유를 설명하는 참고 정보로만 남는다.
    """
    return c["lv"] <= 1


def _console(researches: list, warn: list) -> dict | None:
    """재활용 연구실 목록 → 계산기 `console`. 못 읽으면 None.

    역할군·기업은 소속별 dict로 그대로 넘긴다(계산기가 캐릭터 소속으로 골라 쓴다).
    모르는 tid가 새로 생기면 조용히 빠뜨리지 않고 경고한다.
    """
    if not researches:
        return None
    out: dict = {}
    for r in researches:
        mapped = CONSOLE_TIDS.get(r["tid"])
        if mapped is None:
            warn.append(f"모르는 재활용 연구실 tid {r['tid']} (lv {r['lv']}) — 무시했다. "
                        f"신규 역할군·기업이면 profile_fetch.py의 CONSOLE_TIDS에 추가하라")
            continue
        key, bucket = mapped
        if bucket:
            out.setdefault(key, {})[bucket] = r["lv"]
        else:
            out[key] = r["lv"]
    missing = [k for k, _ in CONSOLE_TIDS.values() if k not in out]
    if missing:
        warn.append(f"재활용 연구실에서 못 받은 콘솔 항목 {sorted(set(missing))} — "
                    f"프로필의 기존 값을 그대로 둔다")
        return None
    return out


def _observed_cubes(details: list, cube_names: dict) -> dict:
    """장착 중인 큐브에서 관찰된 {큐브명: 최고 레벨}. 보유 큐브의 **하한**일 뿐이다."""
    out: dict[str, int] = {}
    for d in details:
        tid = d.get("harmony_cube_tid", 0)
        nm = cube_names.get(tid)
        if nm:
            out[nm] = max(out.get(nm, 0), d.get("harmony_cube_lv", 0))
    return dict(sorted(out.items()))


# ── 메인 ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="내 계정 육성 상태 → 육성 프로필")
    ap.add_argument("--name", default="me", help="프로필 이름 (기본 me → profiles/me.json)")
    ap.add_argument("--openid", help="조회할 게임 openid (기본: 쿠키의 내 계정)")
    ap.add_argument("--area", type=int, help="nikke_area_id (기본: 자동 탐색)")
    args = ap.parse_args()

    cookie = _load_cookie()
    openid = args.openid or _openid_from_cookie(cookie)

    # nikke_area_id: 주면 그대로, 없으면 흔한 값들로 탐색
    areas = [args.area] if args.area else [83, 1, 261, 219, 145, 81, 82]
    chars = area = None
    for a in areas:
        resp = _post("Game/GetUserCharacters", {"intl_open_id": openid, "nikke_area_id": a}, cookie)
        if resp.get("code") == 0 and resp["data"].get("characters"):
            chars, area = resp["data"]["characters"], a
            break
    if chars is None:
        sys.exit(f"[!] openid {openid}: 캐릭터를 못 받았다. 세션 만료거나 비공개 계정.")
    print(f"[+] openid {openid} (area {area}): 캐릭터 {len(chars)}종")

    unsynced = sum(1 for c in chars if _unsynced(c))
    print(f"[+] 상세 수집 중… (동기화 소대 {len(chars) - unsynced}종 · 소대 밖 {unsynced}종)")

    details, state_effects = [], []
    codes = [c["name_code"] for c in chars]
    for i in range(0, len(codes), 60):                    # 상세는 60개씩 배치
        data = _check(_post("Game/GetUserCharacterDetails",
                            {"intl_open_id": openid, "nikke_area_id": area,
                             "name_codes": codes[i:i + 60]}, cookie),
                      "GetUserCharacterDetails")
        details.extend(data["character_details"])
        state_effects.extend(data.get("state_effects", []))

    # 콘솔(재활용 연구실)은 캐릭터 API가 아니라 전초기지 쪽에 있다.
    outpost = _post("Game/GetUserProfileOutpostInfo",
                    {"intl_open_id": openid, "nikke_area_id": area}, cookie)
    outpost_info = (outpost.get("data") or {}).get("outpost_info") or {}
    researches = outpost_info.get("recycle_room_researches") or []
    synchro_level = outpost_info.get("synchro_level")

    print("[+] CDN 매핑 수집 중…")
    id_map = _fetch_id_map()                 # name_code -> resource_id
    res_name = _load_resource_name_map()     # resource_id -> 우리 캐릭명
    fav_map = _fetch_favorite_map()          # 소장품·애장품 id -> (등급, 무기군)
    weapons = _load_weapon_map()
    makers = _load_manufacturer_map()         # T9 기업 장비 제조사 일치 확인용
    fav_chars = _load_favorite_chars()       # 애장품이 있는 캐릭터 이름
    cube_names = _load_cube_name_map()
    skill_table = _load_equip_skill_table()
    opt_map, unknown, off_table = _build_option_map(state_effects, skill_table)
    if unknown:
        print("[!] 미매핑 오버로드 옵션 타입(무시됨) — FUNC_TO_EQUIP에 추가하라:", unknown)
    for ftype, key, val, sid in off_table:
        print(f"[!] 옵션 수치가 equipment_skills 표에 없다: {ftype}→{key} {val}% (id {sid}). "
              f"매핑이 틀렸거나 표가 낡았다")

    os.makedirs(PROFILE_DIR, exist_ok=True)
    raw_path = os.path.join(PROFILE_DIR, f"{args.name}.raw.json")
    json.dump({"openid": openid, "area": area, "characters": chars,
               "details": details, "state_effects": state_effects},
              open(raw_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[+] 원시 저장: {raw_path}")

    # 프로필 변환
    eff_by_code = {c["name_code"]: c for c in chars}
    profile_path = os.path.join(PROFILE_DIR, f"{args.name}.json")
    old = {}
    if os.path.exists(profile_path):
        old = json.load(open(profile_path, encoding="utf-8"))

    warn: list[str] = []
    entries, skipped = {}, []
    for d in details:
        name = res_name.get(id_map.get(d["name_code"]))
        if name is None:
            skipped.append(d["name_code"])
            continue
        entries[name] = _to_profile(d, eff_by_code[d["name_code"]], opt_map, fav_map,
                                    name, weapons.get(name), warn, name in fav_chars)

    # 콘솔은 전초기지에서 자동으로 온다. 못 받았으면 기존 손입력 값을 보존한다.
    console_warn: list[str] = []
    console = _console(researches, console_warn) or (old.get("_account") or {}).get("console")
    warn += console_warn
    account = {
        "synchro_level": synchro_level or (old.get("_account") or {}).get("synchro_level"),
        "_synchro_note": "동기화 소대 레벨. 러너의 레벨 정책이 `sync`일 때만 쓴다. 기본 정책은 "
                         "기본 스펙 레벨(400) 고정 — 솔로레이드가 그렇게 돌기 때문이다.",
        "console": console,
        # 계산 때마다 다시 알려야 하는 계정 단위 경고. 러너가 결과에 싣는다.
        "console_warnings": console_warn,
        "_console_note": "Game/GetUserProfileOutpostInfo의 recycle_room_researches(재활용 "
                         "연구실)에서 자동으로 온다. 역할군·기업은 소속별 레벨을 그대로 담고 "
                         "계산기가 캐릭터 소속으로 골라 쓴다. 비어 있으면 러너가 기본 스펙 값"
                         "(180/100/100)을 쓰고 그 사실을 보고한다.",
        "cubes": _observed_cubes(details, cube_names),
        "_cubes_note": "장착 중인 큐브에서 관찰된 **보유 하한**. API는 보유 큐브 전체를 주지 "
                       "않는다. 큐브는 자유롭게 갈아끼우므로 육성 상태가 아니라 케이스가 정하는 "
                       "축이며, 이 목록은 '그 큐브를 그 레벨로 갖고 있는가' 확인에만 쓴다.",
    }
    profile = {
        "_meta": {
            "name": args.name,
            "openid": openid,
            "area": area,
            "fetched_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "blablalink Game/GetUserCharacters + Game/GetUserCharacterDetails "
                      "+ Game/GetUserProfileOutpostInfo",
            "roster": len(entries),
            "synced": sum(1 for e in entries.values() if not e.get("_unsynced")),
        },
        "_account": account,
        "chars": dict(sorted(entries.items())),
    }
    json.dump(profile, open(profile_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"[+] 프로필 저장: {profile_path}  ({len(entries)}종"
          + (f", {len(skipped)}종 이름매핑 실패" if skipped else "") + ")")
    if skipped:
        print("    이름매핑 실패 name_code(신캐·미수집 가능):", skipped[:20])
    if account["console"] is None:
        print("[!] _account.console 이 비어 있다 — 인게임 콘솔 레벨을 손으로 채워라.")
    for w in warn:
        print("[!]", w)

    low = {n: e["favorite_stage"] for n, e in entries.items()
           if e.get("favorite_stage") is not None and e["favorite_stage"] < 3}
    if low:
        print(f"[!] 애장품 단계 3 미만 {len(low)}명 — 그 단계의 스킬 판본으로 계산된다. "
              f"판본이 아직 파싱 안 됐으면 시뮬이 그 사실을 알리며 끊는다: {low}")

    off = sorted(n for n, e in entries.items() if e.get("_unsynced"))
    if off:
        print(f"[+] 동기화 소대 밖 {len(off)}종 (참고) — 레벨은 정책이 정하므로 계산에는 영향이 "
              f"없다. 다만 이쪽은 대체로 스킬·장비 투자가 없어 딜이 낮게 나온다: {off}")

    # T9 기업 장비는 제조사가 캐릭터 기업과 같아야 +30%가 붙는다. 안 맞는 장비를 끼고 있으면
    # 인게임에서도 손해라 알린다 (실측 41부위는 전부 맞았다).
    corp = {n: {p: d for p, d in e["equipment"].items() if d.get("_track") == TRACK_CORP}
            for n, e in entries.items()}
    corp = {n: v for n, v in corp.items() if v}
    if corp:
        parts_n = sum(len(v) for v in corp.values())
        print(f"[+] T9 기업 장비 {parts_n}부위({len(corp)}명) — 제조사·강화가 계산에 들어간다 "
              f"(기본값×(1+0.3×기업일치+0.1×강화))")
        off = {n: {p: d["corp"] for p, d in v.items() if d["corp"] != makers.get(n)}
               for n, v in corp.items()}
        off = {n: v for n, v in off.items() if v}
        if off:
            print(f"[!] 캐릭터 기업과 다른 기업 장비를 낀 캐릭터 {len(off)}명 — 제조사 보너스"
                  f"(+30%)를 못 받는다(인게임도 마찬가지다): {dict(list(off.items())[:6])}")
    unknown_corp = sorted({d["corp"] for e in entries.values() for d in e["equipment"].values()
                           if str(d.get("corp", "")).startswith("?")})
    if unknown_corp:
        print(f"[!] 모르는 장비 제조사 코드 {unknown_corp} — profile_fetch.py의 GEAR_CORPS에 "
              f"추가하라 (parsed_nikke.json의 manufacturer와 글자가 같아야 한다)")

    no_item = sum(1 for e in entries.values() if e["collection_stage"] == NO_ITEM)
    empty = sum(1 for e in entries.values()
                if all(p.get("tier") == NO_ITEM for p in e["equipment"].values()))
    print(f"[+] 소장품 미장착 {no_item}종 · 장비 4부위 전부 미장착 {empty}종")


if __name__ == "__main__":
    main()
