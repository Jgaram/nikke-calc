#!/usr/bin/env python3
"""
cdn_fetch.py
blablalink CDN에서 캐릭터 데이터 직접 수집 → nikke_scraped.json

브라우저를 쓰지 않는다. CDN 경로가 평문 경로에서 결정되므로(`cdn_path.py`)
전체 캐릭터를 수 초 만에 받는다.

캐릭터 외의 성장 테이블(소장품·장비·호감도)은 `cdn_tables.py`가 따로 만든다.
다만 **큐브는 신규 종류가 주기적으로 추가되므로** 여기서 같이 갱신한다.

Run:
  python scraper/cdn_fetch.py            # 전량 수집 + 이미지 + parse_nikke + 큐브 표
  python scraper/cdn_fetch.py --check    # 수집 후 기존 파일과 diff만 출력 (쓰기 없음)
  python scraper/cdn_fetch.py --ids 601,602
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

import cdn_path
from parse_nikke import run as parse_nikke

ROOT = Path(__file__).parent.parent
IMAGE_DIR = ROOT / "image"
JSON_PATH = Path(__file__).parent / "nikke_scraped.json"
LEVEL_STATS_PATH = ROOT / "data" / "base_stat_tables" / "level_stats.json"

# 표에 담는 레벨 상한. CDN은 1400레벨까지 주지만 계산기가 그 위를 조회할 일이 없어
# 잘라 담는다 (base_stat.py `_level_stat`은 표 밖 레벨을 양끝값으로 잡아준다).
LEVEL_STATS_MAX = 1000

LOCALE = "ko"
CONCURRENCY = 16

ID_MAP_PATH = "/character/character_id_map.json"
ROLEDATA_PATH = "/roledata/{rid}-v2-{locale}.json"
# 256x512 썸네일. 기존 image/ 규격과 동일하다.
PORTRAIT_PATH = "/character/mi/mi_c{rid:03d}_00_s.webp"

# 애장품(favorite item). SSR 17개만 스킬을 바꾼다.
FAVORITE_RARE_MAP_PATH = "/equip/favorite_rare_map.json"
FAVORITE_PATH = "/equip/{locale}/favorite_{fid}.json"
# icon_resource_id "si_favoriteitem_c072_00" → resource_id 72
ICON_RID_RE = re.compile(r"c(\d+)_")

ELEMENT_MAP = {
    "Fire": "작열", "Water": "수냉", "Wind": "풍압",
    "Electronic": "전격", "Iron": "철갑",
}
CLASS_MAP = {"Attacker": "화력형", "Supporter": "지원형", "Defender": "방어형"}
CORP_MAP = {
    "ELYSION": "엘리시온", "MISSILIS": "미실리스", "TETRA": "테트라",
    "PILGRIM": "필그림", "ABNORMAL": "어브노말",
}
BURST_MAP = {"Step1": "1", "Step2": "2", "Step3": "3", "AllStep": "A"}
RARITY_RANK = {"SSR": 3, "SR": 2, "R": 1}

# 사이트가 <span>으로 렌더링하는 마크업. innerText 기준으로는 사라진다.
# 설명문에 literal로 등장하는 `<Step 1 에서 사용 시 : ...>` 같은 텍스트는 건드리면 안 되므로
# 알려진 태그만 정확히 지운다.
TAG_RE = re.compile(r"</?(?:color|word_group)(?:=[^>]*)?>")


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text).replace("\xa0", " ")


def safe_filename(name: str) -> str:
    """캐릭터명 → 이미지 파일명(확장자 제외).

    Windows에서 금지된 문자를 기존 image/ 파일 규칙대로 '_'로 치환한다.
    예: 'D : 킬러 와이프' → 'D _ 킬러 와이프'
    """
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name


def js_number(value) -> str:
    """프론트엔드의 `String(Number(v)/100)`과 같은 문자열을 만든다."""
    x = value / 100
    return str(int(x)) if x == int(x) else f"{x:.10g}"


def build_template(levels: dict[str, str]) -> dict:
    """레벨별 텍스트에서 template + values 구조 생성.

    레벨 간 변하는 숫자만 {0}, {1}... 로 바뀌고 고정 숫자는 리터럴로 남는다.
    """
    texts = [levels[str(i)] for i in range(1, len(levels) + 1) if str(i) in levels]
    if not texts:
        return {"template": "", "values": {}}

    number_pattern = re.compile(r'\d+\.?\d*')
    all_numbers = [number_pattern.findall(t) for t in texts]

    if len(all_numbers) > 1:
        changing = [
            i for i in range(len(all_numbers[0]))
            if any(all_numbers[j][i] != all_numbers[0][i] for j in range(1, len(all_numbers))
                   if i < len(all_numbers[j]))
        ]
    else:
        changing = list(range(len(all_numbers[0]))) if all_numbers else []

    base_nums = all_numbers[0]
    changing_set = set(changing)
    ph_idx = 0
    result_parts = []
    search_start = 0
    template_src = texts[0]
    for num_pos, num in enumerate(base_nums):
        m = number_pattern.search(template_src, search_start)
        if m is None:
            break
        if num_pos in changing_set:
            result_parts.append(template_src[search_start:m.start()])
            result_parts.append(f"{{{ph_idx}}}")
            ph_idx += 1
        else:
            result_parts.append(template_src[search_start:m.end()])
        search_start = m.end()
    result_parts.append(template_src[search_start:])
    template = "".join(result_parts)

    values = {}
    for lv, text in levels.items():
        nums = number_pattern.findall(text)
        values[lv] = [nums[pos] for pos in changing if pos < len(nums)]

    return {"template": template, "values": values}


def render_skill(detail: dict) -> dict:
    """스킬 상세 → {쿨타임, template, values}.

    description_localkey의 {description_value_NN}에 레벨별 값을 끼워 넣어
    레벨 1~10 텍스트를 만든 뒤 template 구조로 압축한다.
    """
    desc = strip_tags(detail.get("description_localkey") or "")
    value_list = detail.get("description_value_list") or []

    level_values = []
    for entry in value_list:
        level_values.append((entry or {}).get("description_value") or [])
    level_count = max((len(v) for v in level_values), default=0) or 1

    levels = {}
    for lv in range(1, level_count + 1):
        text = desc
        for idx, values in enumerate(level_values, start=1):
            if not values:
                continue
            token = f"{{description_value_{idx:02d}}}"
            if token in text:
                text = text.replace(token, values[min(lv, len(values)) - 1])
        levels[str(lv)] = "\n".join(line.rstrip() for line in text.strip().split("\n"))

    cooltimes = detail.get("skill_cooltime_list") or []
    cooltime = f"{cooltimes[0] / 100:.1f} s" if cooltimes else None

    return {"쿨타임": cooltime, **build_template(levels)}


def render_weapon_skill(shot: dict) -> str:
    text = strip_tags(shot.get("description_localkey") or "")
    for key in re.findall(r"\{(\w+)\}", text):
        if key in shot:
            text = text.replace(f"{{{key}}}", js_number(shot[key]))
    return "\n".join(line.rstrip() for line in text.strip().split("\n"))


def adapt(role: dict) -> tuple[str, dict]:
    """roledata JSON → nikke_scraped.json 엔트리."""
    name = role["name_localkey"]
    shot = role.get("shot_detail") or {}
    weapon_desc = shot.get("description_localkey") or ""

    element = (role.get("element_details") or [{}])[0].get("element")
    for label, value, table in (
        ("속성", element, ELEMENT_MAP),
        ("클래스", role.get("class"), CLASS_MAP),
        ("기업", role.get("corporation"), CORP_MAP),
        ("버스트 단계", role.get("use_burst_skill"), BURST_MAP),
    ):
        if value not in table:
            print(f"  [WARN] {name}: 미지의 {label} 값 {value!r}", file=sys.stderr)

    skills = {}
    for key in ("skill1_detail", "skill2_detail", "ulti_skill_detail"):
        detail = role.get(key)
        if not detail:
            continue
        skills[detail.get("name_localkey", key)] = render_skill(detail)

    return name, {
        "id": role["resource_id"],
        "레어도": role.get("original_rare", ""),
        "속성": ELEMENT_MAP.get(element, element or ""),
        "클래스": CLASS_MAP.get(role.get("class"), role.get("class") or ""),
        "기업": CORP_MAP.get(role.get("corporation"), role.get("corporation") or ""),
        # 소속 스쿼드. `squad`(영문 코드)가 동일 스쿼드 판정의 정본이고,
        # `squad_name`은 표시용이라 `-`인 경우가 있다(예: 777 = 블랑·누아르).
        # 복각·의상 버전은 별도 스쿼드다(앵커=Counters, 앵커 : 이노센트 메이드=Aegis).
        "스쿼드": role.get("squad") or "",
        "스쿼드명": (role.get("squad_detail") or {}).get("squad_name") or "",
        "버스트 단계": BURST_MAP.get(role.get("use_burst_skill"), ""),
        "무기상세": {
            "무기유형": shot.get("weapon_type", ""),
            "최대 장탄 수": str(shot.get("max_ammo", 0)),
            "재장전 시간": f"{shot.get('reload_time', 0) / 100:.2f}s",
            "조작 타입": "차지형" if "{charge_time}" in weapon_desc else "일반형",
            # 발사 메카닉. CDN 원값 그대로 둔다(rpm·개수). 초당 발수 환산은 parse_nikke.py.
            # 펠릿·총구는 곱해서 1회 발사 히트 수가 된다(예: 츠바이 5펠릿 × 2총구 = 10).
            "연사(rpm)": shot.get("rate_of_fire", 0),
            "연사최대(rpm)": shot.get("end_rate_of_fire", 0),
            "연사증가(rpm/발)": shot.get("rate_of_fire_change_pershot", 0),
            "펠릿": shot.get("shot_count", 1),
            "총구": shot.get("muzzle_count", 1),
            # 발사 입력 방식과 발사 후 자세 유지. 딜레이·엄폐·톡톡이 가부가 전부 여기서
            # 유도된다 — 의미와 유도식은 `docs/mechanics/CDN 발사 데이터.md`가 정본이다.
            "조작 입력": shot.get("input_type", ""),
            "사격자세유지(cs)": shot.get("maintain_fire_stance", 0),
            "UP발사타이밍": shot.get("uptype_fire_timing", 0),
            # 탄착군(px). 지속사격 중 시작 → 끝으로 수렴한다(MG 예열). **수집만** 한다 —
            # 계산기는 여전히 weapon_mechanics.json의 커뮤니티 실험값을 쓴다.
            "탄착군 시작": shot.get("start_accuracy_circle_scale", 0),
            "탄착군 끝": shot.get("end_accuracy_circle_scale", 0),
            "탄착군 변화(발당)": shot.get("accuracy_change_pershot", 0),
            "탄착군 변화속도": shot.get("accuracy_change_speed", 0),
            # 샷당 버스트 게이지 충전량(1/100%). **수집만** 한다 — 현행 모델은 실제
            # 누적이 아니라 고정 충전 시간이다(GAMEPLAY.md §버스트 게이지).
            "버스트게이지(발당)": shot.get("burst_energy_pershot", 0),
            "버스트게이지(대상)": shot.get("target_burst_energy_pershot", 0),
            "버스트게이지(풀차지)": shot.get("full_charge_burst_energy", 0),
            # 재장전 1회가 채우는 비율(1/100%). 10000 = 탄창 전체, 3300 = 1/3(클립 무기).
            # 클립 판정의 정본이다 — parse_nikke.py가 `clip_refill`로 내리고
            # timeline.py가 그 값으로 클립 여부·1회 충전량을 유도한다.
            "재장전 채움(1/100%)": shot.get("reload_bullet", 10000),
            # ── 아래는 **의미 확정 전** 원값이다. 한글 라벨을 붙이면 해석을 이름에 박게
            # 되므로 CDN 원명을 그대로 쓴다. 계산기로는 내리지 않는다
            # (docs/mechanics/CDN 발사 데이터.md §수집만 하는 필드).
            #   spot_last_delay  199명 전원 20 — 값이 하나뿐이라 정보량이 없다
            #   spot_first_delay 197명 20 · 토브 33 · 네로 13
            #   bonusrange_*     거리 보너스 사거리(무기군별 고정). 계산기에 거리 개념이 없다
            #   spot_projectile_speed·fire_type  발사체 비행 속도와 탄도. RL만 비-0이다
            "spot_last_delay": shot.get("spot_last_delay", 0),
            "spot_first_delay": shot.get("spot_first_delay", 0),
            "bonusrange_min": role.get("bonusrange_min", 0),
            "bonusrange_max": role.get("bonusrange_max", 0),
            "spot_projectile_speed": shot.get("spot_projectile_speed", 0),
            "fire_type": shot.get("fire_type", ""),
            "무기스킬": render_weapon_skill(shot),
        },
        "스킬": skills,
    }


def level_curve(role: dict) -> dict:
    """roledata → 레벨 곡선 원값. `nikke_scraped.json`에는 담지 않는다.

    캐릭터 1명당 1400개짜리 배열이 셋이라 스크랩 원문에 넣으면 파일이 수십 배로
    부푼다. 대신 `build_level_stats()`가 곧바로 표로 접어 `level_stats.json`에 쓴다.
    """
    return {
        "레어도": role.get("original_rare", ""),
        "클래스": CLASS_MAP.get(role.get("class"), role.get("class") or ""),
        "무기유형": (role.get("shot_detail") or {}).get("weapon_type", ""),
        "atk": role.get("character_level_attack_list") or [],
        "def": role.get("character_level_defence_list") or [],
        "hp": role.get("character_level_hp_list") or [],
    }


def build_level_stats(results: dict, curves: dict[int, dict]) -> dict:
    """레벨 곡선 199명분 → `level_stats.json`.

    키는 `등급_클래스_무기유형`이다. **등급이 키에 들어간다** — atk·hp는 클래스와
    등급으로, def는 거기에 무기유형까지 얹혀 갈린다(SR 라피의 레벨1 공격력은
    SSR 화력형의 600이 아니라 540이다). 등급을 빼고 적으면 SR·R 캐릭터의 기본
    스탯이 통째로 SSR 값으로 부풀어 오른다.

    같은 조합 안에서 곡선이 갈리는 캐릭터는 `_curve_exceptions`에 **자기가 실제로
    따르는 조합 키**로 적는다(하란은 SR을 들었지만 방어력 곡선이 AR 쪽이다).
    어느 조합과도 안 맞는 곡선이 나오면 표로 접을 수 없다는 뜻이므로 죽는다 —
    조용히 남의 곡선을 씌우면 그 캐릭터만 계속 틀린 스탯으로 계산된다.
    """
    id_to_name = {entry["id"]: name for name, entry in results.items()}

    groups: dict[str, dict[tuple, list[int]]] = {}
    for rid, c in curves.items():
        if not (c["레어도"] and c["클래스"] and c["무기유형"]):
            continue
        key = f'{c["레어도"]}_{c["클래스"]}_{c["무기유형"]}'
        sig = (tuple(c["atk"][:LEVEL_STATS_MAX]),
               tuple(c["def"][:LEVEL_STATS_MAX]),
               tuple(c["hp"][:LEVEL_STATS_MAX]))
        groups.setdefault(key, {}).setdefault(sig, []).append(rid)

    # 조합의 대표 곡선 = 그 조합에서 가장 많은 캐릭터가 쓰는 곡선.
    # 동수면 resource_id가 작은 쪽(먼저 출시된 원본)이 대표다 — 재수집마다
    # 대표가 뒤바뀌면 표 전체가 diff로 뜬다.
    majority = {k: max(sigs, key=lambda s: (len(sigs[s]), -min(sigs[s])))
                for k, sigs in groups.items()}
    lookup: dict[tuple, str] = {}
    for key in sorted(majority):
        lookup.setdefault(majority[key], key)

    exceptions: dict[str, str] = {}
    for key in sorted(groups):
        for sig, rids in groups[key].items():
            if sig == majority[key]:
                continue
            owner = lookup.get(sig)
            for rid in sorted(rids):
                name = id_to_name.get(rid, f"id {rid}")
                if owner is None:
                    sys.exit(
                        f"[cdn_fetch] {name}의 레벨 곡선이 어느 조합({key} 포함)과도 "
                        f"맞지 않는다 — 등급_클래스_무기유형 표로는 담을 수 없다. "
                        f"level_stats.json 구조를 바꿔야 한다.")
                exceptions[name] = owner

    table = {
        key: {str(lv): {"atk": a, "def": d, "hp": h}
              for lv, (a, d, h) in enumerate(zip(*majority[key]), start=1)}
        for key in sorted(groups)
    }
    return {
        "_comment": ("레벨별 기본 스탯. 등급(SSR/SR/R)·클래스(화력형/지원형/방어형)·"
                     "무기유형 조합별로 구분. scraper/cdn_fetch.py가 CDN roledata의 "
                     "character_level_{attack,defence,hp}_list에서 생성한다 — 손으로 고치지 않는다."),
        "_structure": "{ 등급_클래스_무기유형: { 레벨: { atk, def, hp } } }",
        "_exceptions_comment": ("자기 조합의 대표 곡선과 다른 캐릭터 → 그 캐릭터가 실제로 "
                                "따르는 조합 키. base_stat.py `_level_stat`이 이름으로 먼저 본다."),
        "_exceptions": exceptions,
        **table,
    }


def write_level_stats(table: dict, check: bool = False) -> None:
    old = (json.loads(LEVEL_STATS_PATH.read_text(encoding="utf-8"))
           if LEVEL_STATS_PATH.exists() else {})
    combos = [k for k in table if not k.startswith("_")]
    added = [k for k in combos if k not in old]
    removed = [k for k in old if not k.startswith("_") and k not in table]
    changed = [k for k in combos if k in old and old[k] != table[k]]

    print(f"레벨 스탯: {len(combos)}조합"
          f" (신규 {len(added)} / 변경 {len(changed)} / 삭제 {len(removed)})")
    for label, keys in (("신규", added), ("변경", changed), ("삭제", removed)):
        if keys:
            print(f"  {label}: {', '.join(keys)}")
    if table.get("_exceptions"):
        print(f"  곡선 예외: {table['_exceptions']}")

    if check:
        return
    LEVEL_STATS_PATH.write_text(
        json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {LEVEL_STATS_PATH.name} 저장")


async def fetch_json(client: httpx.AsyncClient, path: str):
    r = await client.get(cdn_path.url(path))
    r.raise_for_status()
    return json.loads(r.content.decode("utf-8-sig"))


def _resolve_collision(a: dict, b: dict) -> tuple[dict, dict]:
    """동명이인 둘 → (맨이름을 갖는 쪽, 개명될 쪽).

    수집이 비동기라 도착 순서가 매번 다르다. 순서에 의존하면 재수집마다 키가 뒤바뀌므로
    **등급이 높은 쪽**(동률이면 resource_id가 작은 쪽 = 먼저 출시된 원본)이 맨이름을
    갖도록 고정한다.
    """
    rank = lambda e: (RARITY_RANK.get(e["레어도"], 0), -e["id"])
    return (a, b) if rank(a) >= rank(b) else (b, a)


def _alias_name(name: str, alias: dict, results: dict) -> str:
    """개명될 쪽의 새 키. `사쿠라 (SR)` — 등급까지 같으면 `사쿠라 (836)`.

    이름은 유저가 스쿼드에 직접 치는 식별자다(`docs/ALIASES.md`). 그래서 기계적인
    id보다 읽어서 구분되는 등급을 먼저 쓰고, 그걸로도 안 갈리는 경우에만 id로 떨어진다.
    """
    candidate = f"{name} ({alias['레어도']})"
    if candidate in results:
        candidate = f"{name} ({alias['id']})"
    return candidate


async def collect(ids: list[int] | None) -> tuple[dict, dict]:
    """(nikke_scraped 엔트리, resource_id → 레벨 곡선) 반환."""
    async with httpx.AsyncClient(timeout=30, http2=False) as client:
        if ids is None:
            id_map = await fetch_json(client, ID_MAP_PATH)
            ids = sorted({r["resource_id"] for r in id_map})
            print(f"캐릭터 후보 {len(ids)}개")

        limit = asyncio.Semaphore(CONCURRENCY)
        results: dict[str, dict] = {}
        curves: dict[int, dict] = {}
        missing: list[int] = []

        async def one(rid: int):
            async with limit:
                try:
                    role = await fetch_json(
                        client, ROLEDATA_PATH.format(rid=rid, locale=LOCALE)
                    )
                except httpx.HTTPStatusError:
                    missing.append(rid)
                    return
                curves[role["resource_id"]] = level_curve(role)
                name, entry = adapt(role)
                # 게임 내 동명이인(예: SSR 사쿠라 rid282 / SR 사쿠라 rid836)이 존재한다.
                # 이름을 키로 쓰므로 충돌하는 쪽을 개명해 **둘 다** 보존한다 — 버리면
                # 그 resource_id는 이름으로 되돌릴 길이 없어져 프로필 수집에서 통째로
                # 빠진다(profile_fetch의 `이름매핑 실패`).
                prev = results.get(name)
                if prev is not None:
                    keep, alias = _resolve_collision(prev, entry)
                    alias_name = _alias_name(name, alias, results)
                    print(f"  [WARN] 이름 충돌 {name!r}: "
                          f"id={keep['id']}({keep['레어도']}) 유지, "
                          f"id={alias['id']}({alias['레어도']}) → {alias_name!r} 개명",
                          file=sys.stderr)
                    results[name] = keep
                    results[alias_name] = alias
                    return
                results[name] = entry

        await asyncio.gather(*(one(rid) for rid in ids))

        # 애장품(17명만) 부착. results에 있는 캐릭터에만.
        favorites = await fetch_favorites(client)
        for entry in results.values():
            fav = favorites.get(entry["id"])
            if fav:
                entry["애장품"] = fav

    if missing:
        print(f"roledata 없음 {len(missing)}개: {sorted(missing)}")
    return dict(sorted(results.items(), key=lambda kv: kv[1]["id"])), curves


def adapt_favorite(fav: dict) -> tuple[int, dict] | None:
    """favorite_{id}.json → (resource_id, 애장품 엔트리).

    단계별로 3개 스킬 중 하나씩 교체된다(`skill_change_slot`). 배열 순서가
    곧 애장품 단계(1/2/3)다. 콜렉션 스킬은 캐릭터 특성이 아니라 제외한다.
    """
    m = ICON_RID_RE.search(fav.get("icon_resource_id", ""))
    if not m:
        return None
    rid = int(m.group(1))

    stages = []
    for i, item in enumerate(fav.get("favoriteitem_skill_group_data") or [], start=1):
        info = item.get("info", item)
        skill = render_skill(info)  # 쿨타임/template/values
        stages.append({
            "단계": i,
            "교체슬롯": item.get("skill_change_slot"),
            "스킬명": info.get("name_localkey", ""),
            "template": skill["template"],
            "values": skill["values"],
        })
    return rid, {"아이템명": fav.get("name_localkey", ""), "단계별": stages}


async def fetch_favorites(client: httpx.AsyncClient) -> dict[int, dict]:
    """SSR 애장품 전량 수집 → {resource_id: 애장품 엔트리}."""
    try:
        rare_map = await fetch_json(client, FAVORITE_RARE_MAP_PATH)
    except httpx.HTTPStatusError:
        print("애장품: favorite_rare_map 없음, 건너뜀", file=sys.stderr)
        return {}

    fav_ids = rare_map.get("SSR", [])
    result: dict[int, dict] = {}
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(fid: int):
        async with limit:
            try:
                fav = await fetch_json(
                    client, FAVORITE_PATH.format(locale=LOCALE, fid=fid)
                )
            except httpx.HTTPStatusError:
                return
            adapted = adapt_favorite(fav)
            if adapted:
                result[adapted[0]] = adapted[1]

    await asyncio.gather(*(one(fid) for fid in fav_ids))
    print(f"애장품: {len(result)}명 수집")
    return result


async def download_images(results: dict, force: bool = False) -> None:
    IMAGE_DIR.mkdir(exist_ok=True)
    targets = []
    for name, entry in results.items():
        path = IMAGE_DIR / f"{safe_filename(name)}.webp"
        if force or not path.exists():
            targets.append((entry["id"], path))
    if not targets:
        print("이미지: 모두 존재")
        return

    limit = asyncio.Semaphore(CONCURRENCY)
    saved, failed = 0, []

    async with httpx.AsyncClient(timeout=30) as client:
        async def one(rid: int, path: Path):
            nonlocal saved
            async with limit:
                r = await client.get(cdn_path.url(PORTRAIT_PATH.format(rid=rid)))
                if r.status_code != 200:
                    failed.append(path.stem)
                    return
                path.write_bytes(r.content)
                saved += 1

        await asyncio.gather(*(one(rid, path) for rid, path in targets))

    print(f"이미지: {saved}개 저장" + (f", 실패 {failed}" if failed else ""))


def report_diff(new: dict, old_path: Path, partial: bool = False) -> None:
    """수집 결과를 기존 파일과 비교해 신규/변경/삭제를 출력.

    partial=True(--ids 부분 수집)이면 가져온 캐릭터만 비교한다. 나머지는
    수집 대상이 아니므로 "삭제"로 오인하지 않는다.
    """
    if not old_path.exists():
        # 아래 print들은 cp949 콘솔로 나간다. em dash 같은 비-cp949 문자를 쓰면
        # UnicodeEncodeError로 죽으므로 ASCII 구분자만 쓴다.
        print("기존 nikke_scraped.json 없음 - 전량 신규")
        return
    old = json.loads(old_path.read_text(encoding="utf-8"))

    added = [n for n in new if n not in old]
    removed = [] if partial else [n for n in old if n not in new]
    changed = []
    for name in new:
        if name in old and new[name] != old[name]:
            fields = [k for k in set(new[name]) | set(old[name])
                      if new[name].get(k) != old[name].get(k)]
            changed.append((name, sorted(fields)))

    print(f"\n신규 {len(added)} / 변경 {len(changed)}"
          + ("" if partial else f" / 삭제 {len(removed)}"))
    if added:
        print("  신규:", ", ".join(added))
    if removed:
        print("  삭제:", ", ".join(removed))
    for name, fields in changed:
        print(f"  변경: {name} : {', '.join(fields)}")


def parse_ids(raw: str) -> list[int]:
    """--ids 인자 파싱. 숫자 resource_id만 받는다.

    이름→id를 값싸게 조회할 인덱스가 CDN에 없다(완전한 이름 소스는 roledata
    전량뿐). 그러니 이름을 넣었으면 크래시 대신, 이름·id 없이도 되는 전량 수집을
    안내한다.
    """
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    bad = [t for t in tokens if not t.isdigit()]
    if bad:
        sys.exit(
            f"[cdn_fetch] --ids 는 숫자 resource_id만 받는다 (받은 값: {', '.join(bad)}).\n"
            f"  이름만 안다면 인자 없이 전량 수집하라 (수 초):  python scraper/cdn_fetch.py\n"
            f"  무엇이 바뀌는지 먼저 보려면:                    python scraper/cdn_fetch.py --check"
        )
    return [int(t) for t in tokens]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="쓰지 않고 diff만 출력")
    ap.add_argument("--ids", help="쉼표 구분 resource_id(숫자)만 수집. 이름만 알면 인자 없이 전량")
    ap.add_argument("--force-images", action="store_true", help="이미지 전부 다시 받기")
    args = ap.parse_args()

    ids = parse_ids(args.ids) if args.ids else None
    results, curves = asyncio.run(collect(ids))
    print(f"수집 완료 {len(results)}명")

    partial = ids is not None

    # 레벨 스탯 표는 **전량 수집일 때만** 다시 만든다. --ids 부분 수집은 조합
    # 대부분이 비어 있어, 그대로 쓰면 표에서 통째로 사라진다.
    level_stats = None if partial else build_level_stats(results, curves)

    # 큐브 표 갱신. `cdn_tables`가 `cdn_fetch`를 import하므로 여기서 늦게 부른다.
    from cdn_tables import refresh as refresh_tables

    if args.check:
        report_diff(results, JSON_PATH, partial=partial)
        print()
        if level_stats is not None:
            write_level_stats(level_stats, check=True)
            print()
        refresh_tables(["cube"], check=True)
        print("\n--check 모드: 파일을 쓰지 않았다")
        return

    report_diff(results, JSON_PATH, partial=partial)
    if partial and JSON_PATH.exists():
        merged = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        merged.update(results)
        results = merged
    JSON_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{JSON_PATH.name} 저장")

    if level_stats is not None:
        write_level_stats(level_stats)
    asyncio.run(download_images(results, force=args.force_images))
    parse_nikke(results)
    print()
    refresh_tables(["cube"])


if __name__ == "__main__":
    main()
