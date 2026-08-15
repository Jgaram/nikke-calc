"""blablalink 로그인 세션으로 '내 계정의 실제 육성 스펙'을 받아 계산기 프리셋으로 변환한다.

브라우저 없음. `scraper/.session_cookie`(gitignore)의 쿠키만 읽어 순수 HTTP로 돈다.
쿠키 확보는 최초 1회 브라우저 로그인 → game_token 등 추출(SCRAPER.md §유저 수집).

사용법:
    python scraper/user_fetch.py                 # 쿠키의 game_openid = 내 계정
    python scraper/user_fetch.py --openid 1234…  # 특정 openid (타인, 상대 공개 시)
    python scraper/user_fetch.py --area 83       # nikke_area_id (기본: 자동 탐색)

출력(둘 다 gitignore):
    scraper/user_scraped_<openid>.json   원시 응답(캐릭터+상세)
    scraper/user_preset_<openid>.json    {우리캐릭명: 육성 오버라이드} — char_defaults와 같은 레이어

계산기 미제공 항목(계정 단위): console(공통/클래스/기업 콘솔), collection_stage(소장품).
→ 프리셋에 넣지 않는다. 러너의 DEFAULT_CHAR 기본값(콘솔 180/100/100, SR15)이 그대로 적용된다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cdn_path  # noqa: E402

API = "https://api.blablalink.com/api/game/proxy/"
COMMON = {"game_id": "29080", "area_id": "global", "source": "pc_web",
          "intl_game_id": "29080", "language": "ko", "env": "prod"}

# 오버로드 옵션 function_type(응답 state_effects) → 계산기 equip_skills 키.
# 값은 모두 |value|/100 = 퍼센트(차지시간 감소는 음수라 절대값). 미지의 타입은 경고.
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
PARTS = [("head", "머리"), ("torso", "몸통"), ("arm", "팔"), ("leg", "다리")]


# ── HTTP ──────────────────────────────────────────────────────────────────
def _load_cookie() -> str:
    p = os.path.join(HERE, ".session_cookie")
    if not os.path.exists(p):
        sys.exit("[!] scraper/.session_cookie 없음 — 최초 로그인으로 쿠키를 넣어라 (SCRAPER.md §유저 수집).")
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
        return {"code": e.code, "msg": "HTTP " + str(e.code), "_raw": e.read().decode("utf-8", "replace")[:300]}


def _check(resp: dict, what: str) -> dict:
    if resp.get("code") != 0:
        if resp.get("code") == 300001:
            sys.exit(f"[!] {what}: 로그인 세션 만료 (game not login). 쿠키를 다시 받아라.")
        sys.exit(f"[!] {what} 실패: {json.dumps(resp, ensure_ascii=False)[:300]}")
    return resp["data"]


# ── 매핑 로드 ─────────────────────────────────────────────────────────────
def _fetch_id_map() -> dict:
    """CDN character_id_map.json → {name_code: resource_id}."""
    url = cdn_path.url("/character/character_id_map.json")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    rows = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    m = {}
    for row in rows:
        m.setdefault(row["name_code"], row["resource_id"])
    return m


def _load_resource_name_map() -> dict:
    """nikke_scraped.json → {resource_id: 우리 캐릭명}."""
    d = json.load(open(os.path.join(HERE, "nikke_scraped.json"), encoding="utf-8"))
    return {v["id"]: name for name, v in d.items() if isinstance(v, dict) and "id" in v}


def _load_cube_name_map() -> dict:
    """cube.json → {cube id: 큐브명}."""
    d = json.load(open(os.path.join(ROOT, "data", "base_stat_tables", "cube.json"), encoding="utf-8"))
    return {v["id"]: name for name, v in d.items()
            if isinstance(v, dict) and "id" in v}


# ── 변환 ──────────────────────────────────────────────────────────────────
def _build_option_map(details_data: dict) -> dict:
    """state_effects 전체 → {option_id: (equip_skills 키, 퍼센트값)}. 미지 타입은 warn 목록에."""
    opt, unknown = {}, {}
    for se in details_data.get("state_effects", []):
        fd = se["function_details"][0]
        ftype = fd["function_type"]
        key = FUNC_TO_EQUIP.get(ftype)
        val = abs(fd["function_value"]) / 100.0
        if key is None:
            unknown[ftype] = se["id"]
            continue
        opt[str(se["id"])] = (key, val)
    return opt, unknown


def _to_preset(detail: dict, name: str, opt_map: dict, cube_names: dict, eff: dict) -> dict:
    """`eff` = GetUserCharacters 항목(유효 레벨·돌파·코강 = 동기화 반영값).
    상세의 lv는 개별 레벨이라 동기화 소대에 덮이지 않은 원값이므로 쓰지 않는다."""
    equip_skills = {k: 0.0 for k in EQUIP_KEYS}
    equipment = {}
    for api_p, ko_p in PARTS:
        tier = detail[f"{api_p}_equip_tier"]
        lv = detail[f"{api_p}_equip_lv"]
        if tier >= 10:
            equipment[ko_p] = {"level": lv}          # 기업 T10 (강화 0~5)
        elif tier >= 1:
            equipment[ko_p] = {"tier": f"T{tier}"}    # 일반 T1~T9 (강화 없음)
        else:
            equipment[ko_p] = {"level": 0}            # 빈 슬롯 → 계산기 바닥(기업 lv0)
        for i in (1, 2, 3):
            oid = str(detail[f"{api_p}_equip_option{i}_id"])
            if oid in opt_map:
                key, val = opt_map[oid]
                equip_skills[key] = round(equip_skills[key] + val, 4)
    cube_id = detail.get("harmony_cube_tid", 0)
    preset = {
        "level": eff["lv"],
        "breakthrough": eff["grade"],
        "core_enhancement": eff["core"],
        "affinity": max(1, detail["attractive_lv"]),   # 호감도 표는 1부터 (미투자 0 → 1로)
        "skill_levels": {"1": detail["skill1_lv"], "2": detail["skill2_lv"], "3": detail["ulti_skill_lv"]},
        "equipment": equipment,
        "equip_skills": equip_skills,
    }
    if cube_id and cube_id in cube_names:
        preset["cube"] = {"name": cube_names[cube_id], "level": detail.get("harmony_cube_lv", 0)}
    return preset


def _owned(c: dict) -> bool:
    # 보유 니케는 동기화 소대 레벨이 적용돼 lv>1. 미보유 placeholder는 lv==1.
    return c["lv"] > 1


# ── 메인 ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--openid", help="조회할 게임 openid (기본: 쿠키의 내 계정)")
    ap.add_argument("--area", type=int, help="nikke_area_id (기본: 자동 탐색)")
    args = ap.parse_args()

    cookie = _load_cookie()
    openid = args.openid or _openid_from_cookie(cookie)

    # nikke_area_id: 주면 그대로, 없으면 흔한 값들로 탐색
    areas = [args.area] if args.area else [83, 1, 261, 219, 145, 81, 82]
    chars = None
    for a in areas:
        resp = _post("Game/GetUserCharacters", {"intl_open_id": openid, "nikke_area_id": a}, cookie)
        if resp.get("code") == 0 and resp["data"].get("characters"):
            chars = resp["data"]["characters"]
            area = a
            break
    if chars is None:
        sys.exit(f"[!] openid {openid}: 캐릭터를 못 받았다. 세션 만료거나 비공개 계정.")
    print(f"[+] openid {openid} (area {area}): 캐릭터 {len(chars)}종")

    owned = [c for c in chars if _owned(c)]
    print(f"[+] 보유(육성됨) {len(owned)}종 상세 수집 중…")

    # 상세는 배치로 (60개씩)
    details = []
    all_data_for_optmap = {"state_effects": []}
    codes = [c["name_code"] for c in owned]
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        data = _check(_post("Game/GetUserCharacterDetails",
                            {"intl_open_id": openid, "nikke_area_id": area, "name_codes": batch}, cookie),
                      "GetUserCharacterDetails")
        details.extend(data["character_details"])
        all_data_for_optmap["state_effects"].extend(data.get("state_effects", []))

    # 매핑
    id_map = _fetch_id_map()                 # name_code -> resource_id
    res_name = _load_resource_name_map()     # resource_id -> 우리 캐릭명
    cube_names = _load_cube_name_map()
    opt_map, unknown = _build_option_map(all_data_for_optmap)
    if unknown:
        print("[!] 미매핑 오버로드 옵션 타입(무시됨):", unknown)

    # 원시 저장
    raw_path = os.path.join(HERE, f"user_scraped_{openid}.json")
    json.dump({"openid": openid, "area": area, "characters": chars, "details": details},
              open(raw_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[+] 원시 저장: {raw_path}")

    # 프리셋 변환
    eff_by_code = {c["name_code"]: c for c in owned}   # 유효 레벨·돌파·코강
    preset, skipped = {}, []
    for d in details:
        rid = id_map.get(d["name_code"])
        name = res_name.get(rid)
        if name is None:
            skipped.append(d["name_code"])
            continue
        preset[name] = _to_preset(d, name, opt_map, cube_names, eff_by_code[d["name_code"]])
    preset_path = os.path.join(HERE, f"user_preset_{openid}.json")
    json.dump(preset, open(preset_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[+] 프리셋 저장: {preset_path}  ({len(preset)}종 변환"
          + (f", {len(skipped)}종 이름매핑 실패" if skipped else "") + ")")
    if skipped:
        print("    이름매핑 실패 name_code(신캐·미수집 가능):", skipped[:20])


if __name__ == "__main__":
    main()
