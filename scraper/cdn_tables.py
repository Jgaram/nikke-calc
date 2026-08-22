#!/usr/bin/env python3
"""
cdn_tables.py
blablalink CDN에서 캐릭터가 아닌 **성장 테이블**을 수집한다.

  cube       큐브    /equip/cube_rare_map.json + /equip/ko/cube_{id}.json
  collection 소장품  /equip/favorite_rare_map.json 의 R·SR + /equip/ko/favorite_{id}.json
  equipment  장비    /equip/ItemEquipTable-ko.json
  affinity   호감도  /character/AttractiveLevelTable.json

캐릭터 수집은 `cdn_fetch.py`가 담당한다. 이쪽은 `data/base_stat_tables/`의
cube.json · collection.json · equipment_stats.json · affinity.json을 다시 만든다.

큐브는 신규 종류가 주기적으로 추가되므로 `cdn_fetch.py`(= char-scrape)가 캐릭터를
수집할 때마다 같이 갱신한다. 나머지 셋은 게임 업데이트로 표가 바뀔 때만 손대면 된다.

Run:
  python scraper/cdn_tables.py                  # 4개 표 전부 수집 후 덮어쓰기
  python scraper/cdn_tables.py --check          # 수집 후 diff만 출력 (쓰기 없음)
  python scraper/cdn_tables.py --only cube      # 일부만 (쉼표 구분)

## 게임 텍스트 → 계산기 stat 매핑

CDN은 게임 설명문만 준다. 우리 stat 키로 바꾸는 건 의미 판단이라 아래 표로
손으로 관리한다(`context/PARSING.md §stat 로스터`가 어휘의 정본).
표에 없는 스킬이 새로 들어오면 조용히 넘어가지 않고 죽는다 — 신규 큐브가
추가됐다는 신호이므로 사람이 매핑을 정해야 한다.

`unsupported`는 계산기가 아직 그 stat을 처리하지 못한다는 표시다.
`calculator/buff_manager.py`의 `_STAT_TO_BUFF`를 직접 읽어 판정하므로,
엔진이 구현하면 다음 수집 때 자동으로 풀린다.

상시 버프가 아니라 트리거로 1회 발동하는 큐브(`CUBE_INSTANT`)는 이 판정에서 빠진다 —
그쪽은 `_STAT_TO_BUFF`가 아니라 타임라인의 instant 핸들러가 처리하기 때문이다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

import cdn_path
from cdn_fetch import build_template, strip_tags

ROOT = Path(__file__).resolve().parent.parent
TABLE_DIR = ROOT / "data" / "base_stat_tables"

sys.path.insert(0, str(ROOT))
from calculator.buff_manager import _STAT_TO_BUFF  # noqa: E402  (엔진 지원 여부 판정용)

LOCALE = "ko"
CONCURRENCY = 16

CUBE_MAP_PATH = "/equip/cube_rare_map.json"
CUBE_PATH = "/equip/{locale}/cube_{cid}.json"
FAVORITE_RARE_MAP_PATH = "/equip/favorite_rare_map.json"
FAVORITE_PATH = "/equip/{locale}/favorite_{fid}.json"
ITEM_EQUIP_PATH = "/equip/ItemEquipTable-{locale}.json"
ATTRACTIVE_PATH = "/character/AttractiveLevelTable.json"

TABLE_FILES = {
    "cube": "cube.json",
    "collection": "collection.json",
    "equipment": "equipment_stats.json",
    "affinity": "affinity.json",
}

# ── 큐브 ────────────────────────────────────────────────────────────────────
# 스킬명(name_localkey) → 우리 stat 키.
CUBE_STAT_MAP = {
    "안티 코드 HC":        "element_bonus",           # 모든 큐브가 공유 = `공통`
    "히트 업 HC":          "accuracy_pct",
    "차지 대미지 업 HC":   "charge_dmg_pct",
    "퀵 리로드 HC":        "reload_speed_pct",
    "리로드 업 HC":        "ammo_charge_flat",
    "차지 스피드 업 HC":   "charge_speed_pct",
    "매거진 업 HC":        "max_ammo_pct",
    "버스트 차지 업 HC":   "burst_charge_speed_pct",
    "헬스 업 HC":          "max_hp_pct",
    "디펜스 업 HC":        "def_pct",
    "힐 업 HC":            "outgoing_heal_pct",
    "대미지 리덕션 HC":    "received_dmg_pct",
    "핀치 헬스 업 HC":     "max_hp_pct",
    "핀치 파츠 히트 업 HC": "part_dmg_pct",
    "피어스 히트 업 HC":   "pierce_dmg_pct",
    "트루 대미지 업 HC":   "armor_break_dmg_pct",
    "커버 헬스 업 HC":     "cover_hp_pct",
    "디바이드 업 HC":      "split_dmg_pct",
}

# 상시 버프가 아니라 **트리거 시 1회 발동**하는 큐브 스킬. 스킬명 → 발동 타이밍.
# 여기 있는 스킬은 `type: instant`로 나가고 timing이 `battle_start`가 아니게 된다.
# instant 핸들러는 타임라인이 런타임에 등록하므로(`register_instant_handler`) 정적으로
# 지원 여부를 판정할 수 없다 — 여기 넣는 사람이 핸들러 존재를 확인한다.
CUBE_INSTANT = {
    "리로드 업 HC": "hit_count:10",   # 10발 사격 시 탄환 충전 N발 (상시 버프가 아니다)
}

# 전투 시작 시 상시가 아닌 스킬. stat은 엔진에 있어도 조건·지속시간을 이 스키마로
# 표현할 수 없으므로 버프로 등록하지 않는다.
CUBE_CONDITIONAL = {
    "핀치 헬스 업 HC": "체력 20% 이하일 때만 · 20초 지속 (조건부라 상시 버프로 등록하지 않는다)",
}

COMMON_CUBE_SKILL = "안티 코드 HC"

# ── 소장품 ──────────────────────────────────────────────────────────────────
# 소장품 스킬은 한 줄에 효과 2개가 붙어 있다. (스킬명, 값 순번) → (우리 키, buff_type).
# `공통`은 무기군과 무관하게 붙는 쪽, 무기군 키는 무기별로 갈리는 쪽이다.
COLLECTION_COMMON = {
    ("용기를 주는 시선", 1): ("def_pct", "def_pct"),
    ("마음의 버팀목", 0): ("received_dmg_pct", "received_dmg_pct"),
    ("마음의 버팀목", 1): ("cover_hp_pct", "cover_hp_pct"),
}
COLLECTION_WEAPON_SLOT = ("용기를 주는 시선", 0)

# 무기군 효과의 buff_type. 게임 텍스트가 아니라 계산기 취급 기준이다
# (SG·SMG의 `일반 공격 대미지 배율`, RL·SR의 `차지 대미지 배율`은 기존 결정을 유지).
COLLECTION_WEAPON_BUFF = {
    "AR":  "core_dmg_pct",
    "MG":  "max_ammo_pct",
    "RL":  "charge_dmg_pct",
    "SG":  "normal_atk_dmg_pct",
    "SMG": "normal_atk_dmg_pct",
    "SR":  "charge_dmg_pct",
}

# ── 장비 ────────────────────────────────────────────────────────────────────
EQUIP_CLASS_MAP = {"Attacker": "화력형", "Defender": "방어형", "Supporter": "지원형"}
# 호감도 표의 필드 접두사. 값은 %가 아니라 계산에 그대로 더하는 플랫 스탯이다.
AFFINITY_PREFIX = {"화력형": "attacker", "방어형": "defender", "지원형": "supporter"}
EQUIP_PART_MAP = {"Module_A": "머리", "Module_B": "몸통", "Module_C": "팔", "Module_D": "다리"}
# 인게임 `T9 기업 장비`의 내부 등급. 유일하게 강화(0~5)가 붙는 등급이다.
CORP_RARE = "T10"
CORP_MAX_LEVEL = 5
STAT_KEY_MAP = {"Atk": "atk", "Defence": "def", "Hp": "hp"}


# ── 수집 ────────────────────────────────────────────────────────────────────

async def fetch_json(client: httpx.AsyncClient, path: str):
    r = await client.get(cdn_path.url(path))
    r.raise_for_status()
    return json.loads(r.content.decode("utf-8-sig"))


async def gather_limited(client, paths):
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(path):
        async with limit:
            return await fetch_json(client, path)

    return await asyncio.gather(*(one(p) for p in paths))


async def collect(names: list[str]) -> dict[str, object]:
    """요청한 표의 CDN 원본만 수집. 반환 키는 `TABLE_FILES`의 이름."""
    raw: dict[str, object] = {}
    async with httpx.AsyncClient(timeout=30, http2=False) as client:
        if "cube" in names:
            cube_map = await fetch_json(client, CUBE_MAP_PATH)
            raw["cube"] = await gather_limited(client, [
                CUBE_PATH.format(locale=LOCALE, cid=c["id"]) for c in cube_map
            ])
            print(f"  큐브 {len(raw['cube'])}종")
        if "collection" in names:
            fav_map = await fetch_json(client, FAVORITE_RARE_MAP_PATH)
            coll_r, coll_sr = await asyncio.gather(
                gather_limited(client, [
                    FAVORITE_PATH.format(locale=LOCALE, fid=f) for f in fav_map["R"]
                ]),
                gather_limited(client, [
                    FAVORITE_PATH.format(locale=LOCALE, fid=f) for f in fav_map["SR"]
                ]),
            )
            raw["collection"] = (coll_r, coll_sr)
            print(f"  소장품 R {len(coll_r)}·SR {len(coll_sr)}종")
        if "equipment" in names:
            raw["equipment"] = await fetch_json(client, ITEM_EQUIP_PATH.format(locale=LOCALE))
            print(f"  장비 {len(raw['equipment']['records'])}레코드")
        if "affinity" in names:
            raw["affinity"] = await fetch_json(client, ATTRACTIVE_PATH)
            print(f"  호감도 {len(raw['affinity']['records'])}레벨")
    return raw


# ── 공통 유틸 ───────────────────────────────────────────────────────────────

def one_line(text: str) -> str:
    """게임 설명문 → 한 줄. 태그와 장식 머리표(■)를 걷어낸다.

    효과를 감싸는 대괄호는 남긴다 — 한 줄에 효과가 둘 이상인 소장품에서
    효과 경계를 찾는 유일한 단서다. 최종 template에서는 `clean_template`이 지운다.
    """
    text = strip_tags(text).replace("■", " ")
    return " ".join(text.split())


def clean_template(text: str) -> str:
    """표시용 template 문구. 대괄호를 지우고 공백을 정리한다."""
    return " ".join(text.replace("[", " ").replace("]", " ").split())


def slot_template(template: str, slot: int) -> str:
    """효과가 여럿 붙은 한 줄에서 slot번째 효과 문구만 뽑는다.

    `[받는 대미지 {0}% ▼] [엄폐물 최대 체력 {1}% ▲]` → slot 1 → `엄폐물 최대 체력 {0}% ▲`
    """
    groups = re.findall(r"\[([^\]]*)\]", template)
    hit = [g for g in groups if f"{{{slot}}}" in g]
    if len(hit) != 1:
        sys.exit(f"[cdn_tables] 효과 {slot}번을 한 덩어리로 못 집었다: {template}")
    return clean_template(hit[0]).replace(f"{{{slot}}}", "{0}")


def render_levels(info: dict) -> dict[str, str]:
    """스킬 상세 → {스킬레벨: 텍스트}. `cdn_fetch.render_skill`의 큐브·소장품판."""
    desc = one_line(info.get("description_localkey") or "")
    value_list = [(v or {}).get("description_value") or []
                  for v in (info.get("description_value_list") or [])]
    level_count = max((len(v) for v in value_list), default=0) or 1

    levels = {}
    for lv in range(1, level_count + 1):
        text = desc
        for idx, values in enumerate(value_list, start=1):
            if not values:
                continue
            token = f"{{description_value_{idx:02d}}}"
            if token in text:
                text = text.replace(token, values[min(lv, len(values)) - 1])
        levels[str(lv)] = text
    return levels


def single_value_template(info: dict, label: str) -> tuple[str, dict[str, list[str]]]:
    """스킬 상세 → (template, {스킬레벨: [값]}). 변하는 숫자가 1개가 아니면 죽는다."""
    built = build_template(render_levels(info))
    template, values = built["template"], built["values"]
    widths = {len(v) for v in values.values()}
    if widths != {1}:
        sys.exit(f"[cdn_tables] {label}: 레벨별로 변하는 숫자가 1개가 아니다 "
                 f"({sorted(widths)}). 매핑을 사람이 정해야 한다:\n  {template}")
    return clean_template(template), values


def unsupported_reason(skill_name: str, stat: str) -> str | None:
    """엔진이 이 효과를 못 받는 이유. 받을 수 있으면 None."""
    if skill_name in CUBE_CONDITIONAL:
        return CUBE_CONDITIONAL[skill_name]
    if skill_name in CUBE_INSTANT:
        return None   # instant는 _STAT_TO_BUFF가 아니라 타임라인 핸들러가 처리한다
    if stat not in _STAT_TO_BUFF:
        return f"계산기 미구현 stat ({stat}) — 버프로 등록하지 않는다"
    return None


# ── 큐브 테이블 ─────────────────────────────────────────────────────────────

def build_cube_table(cubes: list[dict]) -> dict:
    stats = {}
    for lv in range(1, len(cubes[0]["atk"]) + 1):
        stats[str(lv)] = {
            "atk": cubes[0]["atk"][lv - 1],
            "def": cubes[0]["def"][lv - 1],
            "hp": cubes[0]["hp"][lv - 1],
        }
    for c in cubes[1:]:
        if [c["atk"], c["def"], c["hp"]] != [cubes[0]["atk"], cubes[0]["def"], cubes[0]["hp"]]:
            sys.exit(f"[cdn_tables] {c['name_localkey']}: 큐브 플랫 스탯이 다른 큐브와 다르다. "
                     f"_stats 공유 전제가 깨졌다")

    out = {
        "_comment": "큐브 레벨별 공통 스탯(_stats) + 큐브별 스킬. 스탯은 모든 큐브가 동일",
        "_source": "blablalink CDN /equip/cube_rare_map.json · /equip/ko/cube_{id}.json "
                   "→ python scraper/cdn_tables.py (손으로 고치지 않는다)",
        "_skill_note": "`공통`(우월 코드 공격 대미지 = 안티 코드 HC)은 종류가 아니라 "
                       "어떤 큐브를 끼든 항상 붙는 두 번째 스킬이다. 큐브 이름 키는 "
                       "첫 번째 스킬이며 공통 위에 더해진다 — collection.json의 common과 같은 구조",
        "_level_note": "values의 키는 큐브 레벨(1~15)이다. 스킬 레벨이 0인 구간은 키가 없다 "
                       "(레벨 1~4에서는 공통 효과가 붙지 않는다)",
        "_unsupported_note": "`unsupported`가 있는 항목은 계산기가 버프로 등록하지 않는다",
        "_instant_note": "`type: instant`인 항목은 상시 버프가 아니라 `timing`에 적힌 트리거로 "
                         "그때그때 1회 발동한다 (없으면 battle_start 상시 버프)",
        "_stats": stats,
    }

    common_entry = None
    for cube in cubes:
        name = cube["name_localkey"]
        skills = [s for s in (cube.get("harmonycube_skill_group") or []) if s]
        by_name = {s["name_localkey"]: s for s in skills}
        if COMMON_CUBE_SKILL not in by_name:
            sys.exit(f"[cdn_tables] {name}: 공통 스킬({COMMON_CUBE_SKILL})이 없다")

        own = [s for s in skills if s["name_localkey"] != COMMON_CUBE_SKILL]
        if len(own) != 1:
            sys.exit(f"[cdn_tables] {name}: 고유 스킬이 {len(own)}개다 (1개 전제)")

        for info, level_key, target in (
            (by_name[COMMON_CUBE_SKILL], "level2", "common"),
            (own[0], "level1", "own"),
        ):
            skill_name = info["name_localkey"]
            if skill_name not in CUBE_STAT_MAP:
                sys.exit(f"[cdn_tables] 매핑 없는 큐브 스킬 {skill_name!r} ({name}). "
                         f"CUBE_STAT_MAP에 stat을 추가하라:\n"
                         f"  {one_line(info.get('description_localkey') or '')}")
            stat = CUBE_STAT_MAP[skill_name]
            template, by_skill_lv = single_value_template(info, f"{name} / {skill_name}")

            # 큐브 레벨 → 스킬 레벨 (level1/level2 배열의 인덱스 = 큐브 레벨 - 1)
            values = {}
            for cube_lv, skill_lv in enumerate(cube[level_key], start=1):
                if skill_lv <= 0:
                    continue
                values[str(cube_lv)] = by_skill_lv[str(skill_lv)]

            entry = {"stat": stat, "스킬명": skill_name, "template": template, "values": values}
            if skill_name in CUBE_INSTANT:
                entry["type"] = "instant"
                entry["timing"] = CUBE_INSTANT[skill_name]
            reason = unsupported_reason(skill_name, stat)
            if reason:
                entry["unsupported"] = reason

            if target == "common":
                if common_entry is None:
                    common_entry = entry
                elif common_entry != entry:
                    sys.exit(f"[cdn_tables] {name}: 공통 스킬이 다른 큐브와 다르다")
            else:
                out[name] = {**entry, "id": cube["id"], "resource_id": cube["resource_id"]}

    # `공통`이 큐브들보다 앞에 오도록 다시 조립한다
    cube_entries = {k: v for k, v in out.items() if not k.startswith("_")}
    for k in cube_entries:
        del out[k]
    out["공통"] = common_entry
    out.update(cube_entries)
    return out


# ── 소장품 테이블 ───────────────────────────────────────────────────────────

def build_collection_table(coll_r: list[dict], coll_sr: list[dict]) -> dict:
    stat_table = {}
    for prefix, items in (("R", coll_r), ("SR", coll_sr)):
        head = items[0]
        for other in items[1:]:
            if [other["atk"], other["def"], other["hp"]] != [head["atk"], head["def"], head["hp"]]:
                sys.exit(f"[cdn_tables] {other['name_localkey']}: 소장품 플랫 스탯이 "
                         f"같은 등급의 다른 소장품과 다르다")
        for lv in range(len(head["atk"])):
            stat_table[f"{prefix}{lv}"] = {
                "skill_lv": head["level1"][lv],
                "hp": head["hp"][lv],
                "atk": head["atk"][lv],
                "def": head["def"][lv],
            }

    common: dict[str, dict] = {}
    weapons: dict[str, dict] = {}

    for prefix, items in (("R", coll_r), ("SR", coll_sr)):
        for item in items:
            name = item["name_localkey"]
            weapon = item["weapon_type"]
            for group in item.get("collection_skill_group_data") or []:
                info = group.get("info", group)
                skill_name = info["name_localkey"]
                levels = render_levels(info)
                built = build_template(levels)
                # 한 줄에 효과가 여럿이라 template의 {N}과 값 순번이 그대로 대응한다
                for slot in range(len(next(iter(built["values"].values()), []))):
                    key = (skill_name, slot)
                    series = [float(built["values"][str(lv)][slot])
                              for lv in range(1, len(built["values"]) + 1)]
                    if key in COLLECTION_COMMON:
                        our_key, buff_type = COLLECTION_COMMON[key]
                        entry = common.setdefault(our_key, {
                            "template": slot_template(built["template"], slot),
                            "buff_type": buff_type,
                        })
                        _set_series(entry, prefix, series, f"{name} / {our_key}")
                    elif key == COLLECTION_WEAPON_SLOT:
                        entry = weapons.setdefault(weapon, {
                            "template": slot_template(built["template"], slot),
                            "buff_type": COLLECTION_WEAPON_BUFF[weapon],
                        })
                        _set_series(entry, prefix, series, f"{name} / {weapon}")
                    else:
                        sys.exit(f"[cdn_tables] 매핑 없는 소장품 효과 {key} ({name}). "
                                 f"COLLECTION_COMMON / COLLECTION_WEAPON_SLOT를 확인하라:\n"
                                 f"  {built['template']}")

    return {
        "_comment": "무기군별 소장품 스킬. skill_lv는 _stat_table 참조 "
                    "(collection_lv 0~4=1, 5~9=2, 10~14=3, 15=4 · R/SR 공통)",
        "_source": "blablalink CDN /equip/favorite_rare_map.json의 R·SR + "
                   "/equip/ko/favorite_{id}.json → python scraper/cdn_tables.py "
                   "(손으로 고치지 않는다)",
        "_grade_effects": "R등급: common×1(def_pct) + 무기군×1 / SR등급: common×3 + 무기군×1",
        "_grade_note": "common 키에 'R' 없으면 SR 전용 효과. 배열 인덱스 = skill_lv - 1",
        "_stat_table_note": "소장품 레벨별 누적 플랫 스탯 참조용",
        "_stat_table": stat_table,
        "common": common,
        **weapons,
    }


def _set_series(entry: dict, prefix: str, series: list[float], label: str) -> None:
    if prefix in entry and entry[prefix] != series:
        sys.exit(f"[cdn_tables] {label}: 같은 등급의 다른 소장품과 수치가 다르다 "
                 f"({entry[prefix]} vs {series})")
    entry[prefix] = series


# ── 장비 테이블 ─────────────────────────────────────────────────────────────

def build_equipment_table(equip: dict, old: dict | None) -> dict:
    """CDN 장비 테이블 → equipment_stats.json.

    CDN은 **강화 0단계**만 준다. 기업 장비의 1~5단계는 인게임 관측값(0·5단계)에서
    선형 보간한 기존 표를 그대로 잇는다 — 단계당 정확히 +10%가 아닌 부위가 있어
    (지원형 다리) 공식으로 다시 만들면 관측값과 어긋난다. 0단계는 CDN과 대조한다.
    """
    corp: dict[str, dict] = {}
    plain: dict[str, dict] = {}
    old_corp = (old or {}).get("기업") or {
        cls: parts for cls, parts in (old or {}).items() if not cls.startswith("_")
    }

    for rec in equip["records"]:
        cls = EQUIP_CLASS_MAP.get(rec["class"])
        part = EQUIP_PART_MAP.get(rec["item_sub_type"])
        if cls is None or part is None:      # class "All" = TEST용 더미
            continue
        base = {"atk": 0, "def": 0, "hp": 0}
        for s in rec["stat"]:
            key = STAT_KEY_MAP.get(s["stat_type"])
            if key:
                base[key] = s["stat_value"]

        if rec["item_rare"] != CORP_RARE:
            plain.setdefault(rec["item_rare"], {}).setdefault(cls, {})[part] = base
            continue

        prev = (old_corp.get(cls) or {}).get(part)
        if prev is None:
            print(f"  [WARN] 기업 {cls} {part}: 기존 강화 표가 없다. "
                  f"base×(1+0.1×강화)로 채운다 — 인게임 값으로 검증하라", file=sys.stderr)
            levels = {str(lv): {k: round(v * (1 + 0.1 * lv)) for k, v in base.items()}
                      for lv in range(CORP_MAX_LEVEL + 1)}
        else:
            if prev["0"] != base:
                print(f"  [WARN] 기업 {cls} {part}: 0단계가 CDN과 다르다 "
                      f"(기존 {prev['0']} / CDN {base}). CDN 값으로 덮고 1~5단계는 "
                      f"기존 값을 유지한다 — 인게임 재확인 필요", file=sys.stderr)
            levels = {**prev, "0": base}
        corp.setdefault(cls, {})[part] = levels

    return {
        "_comment": "장비 부위별·클래스별 플랫 스탯",
        "_source": "blablalink CDN /equip/ItemEquipTable-ko.json "
                   "→ python scraper/cdn_tables.py (손으로 고치지 않는다)",
        "_corp_note": "`기업`은 인게임 T9 기업 장비(CDN 내부 등급 T10)다. 강화 0~5단계가 "
                      "붙는 유일한 장비다. CDN이 주는 건 0단계뿐이라 1~5단계는 "
                      "인게임 관측(0·5단계) 선형 보간값을 잇는다",
        "_plain_note": "`일반`은 강화가 없는 T1~T9 장비다. 부위별 1세트뿐이라 레벨 키가 없다",
        "기업": {cls: corp[cls] for cls in sorted(corp)},
        "일반": {tier: plain[tier] for tier in sorted(plain, key=lambda t: int(t[1:]))},
    }


# ── 호감도 테이블 ───────────────────────────────────────────────────────────

def build_affinity_table(attractive: dict) -> dict:
    """호감도 레벨별 보너스. CDN의 `*_rate`는 %가 아니라 플랫 스탯이라 그대로 쓴다."""
    out = {
        "_comment": "호감도 레벨별 스탯 보너스. 클래스별로 구분",
        "_source": "blablalink CDN /character/AttractiveLevelTable.json "
                   "→ python scraper/cdn_tables.py (손으로 고치지 않는다)",
        "_note": "CDN 필드명은 `*_rate`지만 값은 비율이 아니라 더해지는 플랫 스탯이다. "
                 "속성 저항(energy·metal·bio)은 계산기가 쓰지 않아 옮기지 않는다",
    }
    for cls, prefix in AFFINITY_PREFIX.items():
        levels = {}
        for rec in attractive["records"]:
            levels[str(rec["attractive_level"])] = {
                "hp": rec[f"{prefix}_hp_rate"],
                "atk": rec[f"{prefix}_attack_rate"],
                "def": rec[f"{prefix}_defence_rate"],
            }
        out[cls] = {k: levels[k] for k in sorted(levels, key=int)}
    return out


# ── 표 조립 ─────────────────────────────────────────────────────────────────

def build(name: str, raw: dict[str, object]) -> dict:
    """수집 원본 → 저장할 표."""
    if name == "cube":
        return build_cube_table(raw["cube"])
    if name == "collection":
        return build_collection_table(*raw["collection"])
    if name == "equipment":
        path = TABLE_DIR / TABLE_FILES["equipment"]
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return build_equipment_table(raw["equipment"], old)
    if name == "affinity":
        return build_affinity_table(raw["affinity"])
    raise KeyError(name)


def refresh(names: list[str], check: bool = False) -> None:
    """표를 수집해 `data/base_stat_tables/`에 쓴다. check면 diff만 출력."""
    print(f"CDN 성장 테이블 수집: {', '.join(names)}")
    raw = asyncio.run(collect(names))
    tables = {TABLE_DIR / TABLE_FILES[name]: build(name, raw) for name in names}

    for path, table in tables.items():
        report_diff(table, path)
    if check:
        print("--check 모드: 파일을 쓰지 않았다")
        return
    for path, table in tables.items():
        write(table, path)


# ── 출력 ────────────────────────────────────────────────────────────────────

def report_diff(new: dict, path: Path) -> None:
    """기존 파일과 비교해 최상위 키 단위로 신규/변경/삭제를 출력."""
    if not path.exists():
        print(f"  {path.name}: 기존 파일 없음 - 전량 신규")
        return
    old = json.loads(path.read_text(encoding="utf-8"))
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and new[k] != old[k]]
    print(f"  {path.name}: 신규 {len(added)} / 변경 {len(changed)} / 삭제 {len(removed)}")
    for label, keys in (("신규", added), ("변경", changed), ("삭제", removed)):
        if keys:
            print(f"    {label}: {', '.join(keys)}")


def write(new: dict, path: Path) -> None:
    path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  {path.name} 저장")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="쓰지 않고 diff만 출력")
    ap.add_argument("--only", help=f"쉼표 구분 표 이름 ({', '.join(TABLE_FILES)}). 기본은 전부")
    args = ap.parse_args()

    names = [n.strip() for n in args.only.split(",")] if args.only else list(TABLE_FILES)
    bad = [n for n in names if n not in TABLE_FILES]
    if bad:
        sys.exit(f"[cdn_tables] 모르는 표 이름: {', '.join(bad)} "
                 f"(가능: {', '.join(TABLE_FILES)})")

    refresh(names, check=args.check)


if __name__ == "__main__":
    main()
