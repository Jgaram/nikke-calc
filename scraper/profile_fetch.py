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

# 변환 로직·상수의 정본은 `profile_convert.py`다. 수집 경로가 셋(CLI·북마클릿·서버)이라
# 변환을 여기 두면 사본이 생긴다 — 이 파일은 **수집과 저장**만 담당한다.
from profile_convert import build_profile  # noqa: E402  (sys.path 주입 후에만 가능)


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


def fetch_union(openid: str, area: int, cookie: str) -> dict | None:
    """유니온(길드) 이름. **실패해도 조용히 넘어간다** — 유니온이 없거나 비공개인
    계정이 흔하고, 이름 하나 때문에 육성 수집 전체를 실패시킬 이유가 없다."""
    r = _post("Game/GetMyGuildInfo",
              {"target_intl_open_id": openid, "target_nikke_area_id": str(area)}, cookie)
    card = ((r.get("data") or {}).get("card") or {}) if r.get("code") == 0 else {}
    name = card.get("guild_name")
    if not name:
        return None
    return {"name": name, "id": card.get("guild_id"), "level": card.get("guild_level"),
            "members": card.get("guild_member_cnt")}


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
    """parsed_nikke.json → {우리 캐릭명: 기업}. T9 기업 장비 제조사 일치 확인용."""
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



def _load_maps() -> dict:
    """`profile_convert.build_profile()`이 받는 조회표 묶음. CDN 2종 + 저장소 파일 4종.

    웹은 같은 표를 빌드 때 `dist/profile_maps.json`으로 구워 쓴다 — 만드는 곳이 둘이지만
    **키 이름과 의미는 `profile_convert.py`의 MAP_KEYS 하나가 정한다.**
    """
    return {
        "id_map":      _fetch_id_map(),            # name_code -> resource_id (CDN)
        "res_name":    _load_resource_name_map(),  # resource_id -> 우리 캐릭명
        "fav_map":     _fetch_favorite_map(),      # 소장품·애장품 id -> (등급, 무기군) (CDN)
        "weapons":     _load_weapon_map(),
        "makers":      _load_manufacturer_map(),   # T9 기업 장비 제조사 일치 확인용
        "fav_chars":   _load_favorite_chars(),
        "cube_names":  _load_cube_name_map(),
        "skill_table": _load_equip_skill_table(),
    }



# ── 메인 ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="내 계정 육성 상태 → 육성 프로필")
    ap.add_argument("--name", default="me", help="프로필 이름 (기본 me → profiles/me.json)")
    ap.add_argument("--openid", help="조회할 게임 openid (기본: 쿠키의 내 계정)")
    ap.add_argument("--area", type=int, help="nikke_area_id (기본: 자동 탐색)")
    args = ap.parse_args()

    cookie = _load_cookie()
    openid = args.openid or _openid_from_cookie(cookie)

    # nikke_area_id: 주면 그대로, 없으면 아는 지역만 탐색.
    # 한섭(83)·일섭(81)·글로벌섭(84) 셋을 실측으로 확인했다(글로벌은 2026-08-23
    # 레벨 410짜리 계정으로 확인). 확인 안 된 지역까지 후보에 넣고 «모르면 글로벌»로
    # 뭉치면 엉뚱한 지역을 글로벌로 오인시킬 수 있어, 실제로 캐릭터가 나온 지역만
    # 찌른다(새 지역을 찾으면 여기 추가). **첫 지역에서 멈추지 않는다** — 계정 하나에
    # 여러 지역이 다 걸릴 수 있고, 첫 hit에서 멈추면 나머지 지역은 영영 못 본다(실측
    # 사례: 일섭이 메인인 계정이 한섭으로만 잡힘). 여러 지역이 잡히면 사람이 `--area`로
    # 고르게 한다 — 이 CLI는 한 번에 프로필 하나만 쓰므로 자동으로 나눠 저장하지 않는다
    # (여러 지역 동시 저장은 웹 쪽 `/api/fetch`에서 한다).
    AREA_LABEL = {83: "한섭", 81: "일섭", 84: "글로벌섭"}
    areas = [args.area] if args.area else list(AREA_LABEL)
    hits = []
    for a in areas:
        resp = _post("Game/GetUserCharacters", {"intl_open_id": openid, "nikke_area_id": a}, cookie)
        if resp.get("code") == 0 and resp["data"].get("characters"):
            hits.append((a, resp["data"]["characters"]))
    if not hits:
        sys.exit(f"[!] openid {openid}: 캐릭터를 못 받았다. 세션 만료거나 비공개 계정.")
    if len(hits) > 1:
        found = ", ".join(f"{AREA_LABEL.get(a, a)}(area {a}, {len(c)}종)" for a, c in hits)
        sys.exit(f"[!] 이 계정에 지역이 {len(hits)}개 걸려 있다 — {found}. "
                 f"--area 로 하나를 골라서 다시 실행하라.")
    area, chars = hits[0]
    print(f"[+] openid {openid} (area {area}): 캐릭터 {len(chars)}종")

    print(f"[+] 상세 수집 중… (전체 {len(chars)}종)")

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

    # 수집 결과 묶음. **세 수집 경로(CLI·북마클릿·서버)가 같은 모양을 만든다** —
    # 그래서 변환은 `profile_convert.build_profile()` 하나로 끝난다.
    raw = {"openid": openid, "area": area, "characters": chars,
           "details": details, "state_effects": state_effects,
           "outpost": outpost_info, "union": fetch_union(openid, area, cookie)}

    os.makedirs(PROFILE_DIR, exist_ok=True)
    raw_path = os.path.join(PROFILE_DIR, f"{args.name}.raw.json")
    json.dump(raw, open(raw_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[+] 원시 저장: {raw_path}")

    print("[+] CDN 매핑 수집 중…")
    maps = _load_maps()

    profile_path = os.path.join(PROFILE_DIR, f"{args.name}.json")
    old = {}
    if os.path.exists(profile_path):
        old = json.load(open(profile_path, encoding="utf-8"))

    profile, notices = build_profile(raw, maps, args.name, old=old)
    json.dump(profile, open(profile_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[+] 프로필 저장: {profile_path}")

    # 알림은 build_profile이 모아서 준다. **하나도 빠뜨리지 않고 낸다**
    # (.agent/skills/profile-sync/SKILL.md §절차 3).
    for n in notices:
        print(("[!] " if n["level"] == "warn" else "[+] ") + n["text"])
        names = n.get("names")
        if names:
            head = ", ".join(names[:12])
            more = f" … 외 {len(names) - 12}종" if len(names) > 12 else ""
            print(f"      {head}{more}")


if __name__ == "__main__":
    main()
