"""
Phase 2: 기본 스탯 계산기

공식:
  (레벨스탯 + (레벨스탯×0.02+20) × 돌파수 + 호감도스탯 + 콘솔스탯)
  × (1 + 0.02×코강수)
  + 장비스탯 + 큐브스탯 + 소장품스탯

캐릭터 인스턴스 구조:
  {
    "name": "라피",
    "level": 200,
    "breakthrough": 3,          # 0~3
    "core_enhancement": 7,      # 0~7 (돌파 3 이후 해금)
    "affinity": 30,             # 1~40
    "equipment": {
      "머리": { "level": 5, "skills": [{"id": "atk_pct", "lv": 10}, ...] },
      "몸통": { "level": 5, "skills": [...] },
      "팔":   { "level": 5, "skills": [...] },
      "다리": { "level": 5, "skills": [...] }
    },
    "cube": { "name": "재장", "level": 5 },
    "console": { "common_level": 10, "class_level": 10, "company_level": 10 },
    "collection_stage": "SR15"
  }
"""
import json
import os

_DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
_TABLE_DIR = os.path.join(_DATA_DIR, "base_stat_tables")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 테이블 (모듈 임포트 시 1회 로드) ─────────────────────────────────────
_NIKKE       = _load(os.path.join(_DATA_DIR, "parsed_nikke.json"))
_LEVEL_STATS = _load(os.path.join(_TABLE_DIR, "level_stats.json"))
_AFFINITY    = _load(os.path.join(_TABLE_DIR, "affinity.json"))
_CONSOLE     = _load(os.path.join(_TABLE_DIR, "console.json"))
_EQUIP_STATS = _load(os.path.join(_TABLE_DIR, "equipment_stats.json"))
_CUBE        = _load(os.path.join(_TABLE_DIR, "cube.json"))
_COLLECTION  = _load(os.path.join(_TABLE_DIR, "collection.json"))


# ── 내부 유틸 ─────────────────────────────────────────────────────────────

def _zero():
    return {"atk": 0.0, "def": 0.0, "hp": 0.0}


def _add(a, b):
    return {"atk": a["atk"] + b["atk"],
            "def": a["def"] + b["def"],
            "hp":  a["hp"]  + b["hp"]}


def _scale(s, k):
    return {"atk": s["atk"] * k, "def": s["def"] * k, "hp": s["hp"] * k}


def _level_stat(cls: str, weapon: str, level: int) -> dict:
    """level_stats.json 조회. 키 없는 레벨은 인접 두 키로 선형 보간."""
    table = _LEVEL_STATS[f"{cls}_{weapon}"]
    key = str(level)
    if key in table:
        return dict(table[key])

    keys   = sorted(table.keys(), key=int)
    levels = [int(k) for k in keys]
    if level <= levels[0]:
        return dict(table[keys[0]])
    if level >= levels[-1]:
        return dict(table[keys[-1]])

    for i in range(len(levels) - 1):
        lo, hi = levels[i], levels[i + 1]
        if lo < level < hi:
            t = (level - lo) / (hi - lo)
            lo_s, hi_s = table[str(lo)], table[str(hi)]
            return {
                "atk": lo_s["atk"] + t * (hi_s["atk"] - lo_s["atk"]),
                "def": lo_s["def"] + t * (hi_s["def"] - lo_s["def"]),
                "hp":  lo_s["hp"]  + t * (hi_s["hp"]  - lo_s["hp"]),
            }


def _core_formula(lv_val: float, bt: int) -> float:
    """레벨스탯 단일 값에 DealForm ② b 공식 적용."""
    return lv_val + (lv_val * 0.02 + 20) * bt


# ── 메인 계산 함수 ────────────────────────────────────────────────────────

def calc_base_stats(char: dict) -> dict:
    """
    캐릭터 인스턴스 → 기본 ATK / DEF / HP 반환.
    반환: {"atk": int, "def": int, "hp": int}
    """
    name       = char["name"]
    level      = char["level"]
    bt         = char["breakthrough"]
    core_enh   = char["core_enhancement"]
    affinity   = char["affinity"]
    equip_inst = char["equipment"]
    cube_inst  = char["cube"]
    console    = char["console"]
    coll_stage = char["collection_stage"]

    # 캐릭터 메타
    meta   = _NIKKE[name]
    cls    = meta["class"]
    weapon = meta["weapon_type"]

    # 레벨스탯
    lv_s = _level_stat(cls, weapon, level)

    # 코어공식 (atk/def/hp 각각)
    core = {
        "atk": _core_formula(lv_s["atk"], bt),
        "def": _core_formula(lv_s["def"], bt),
        "hp":  _core_formula(lv_s["hp"],  bt),
    }

    # 호감도 스탯
    aff_s = _AFFINITY[cls][str(affinity)]

    # 콘솔 스탯 (공통 + 클래스 + 기업)
    con_s = _zero()
    for con_type, level_key in (
        ("공통",   "common_level"),
        ("클래스", "class_level"),
        ("기업",   "company_level"),
    ):
        per = _CONSOLE[con_type]["per_level"]
        con_s = _add(con_s, _scale(per, console[level_key]))

    # 코강 적용 전 합계 → 코강 반영
    pre_scaled = _scale(
        _add(_add(core, aff_s), con_s),
        1 + 0.02 * core_enh,
    )

    # 장비 플랫 스탯 (4부위 합산)
    equip_s = _zero()
    for part, part_data in equip_inst.items():
        equip_s = _add(equip_s, _EQUIP_STATS[cls][part][str(part_data["level"])])

    # 큐브 플랫 스탯
    cube_s = _CUBE["_stats"][str(cube_inst["level"])]

    # 소장품 플랫 스탯
    coll_entry = _COLLECTION["_stat_table"][coll_stage]
    coll_s = {"atk": coll_entry["atk"], "def": coll_entry["def"], "hp": coll_entry["hp"]}

    # 최종 합산
    total = _add(_add(_add(pre_scaled, equip_s), cube_s), coll_s)
    return {"atk": round(total["atk"]),
            "def": round(total["def"]),
            "hp":  round(total["hp"])}


def hp_to_atk(hp: float, ratio: float) -> float:
    """
    HP → ATK 전환. 스킬 텍스트 '최대 HP의 N%를 공격력으로' 대응.
    ratio: 소수 (5% → 0.05)
    """
    return hp * ratio


# ── 빠른 테스트 ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    sample = {
        "name": "라피",
        "level": 200,
        "breakthrough": 0,
        "core_enhancement": 0,
        "affinity": 1,
        "equipment": {
            "머리": {"level": 0, "skills": []},
            "몸통": {"level": 0, "skills": []},
            "팔":   {"level": 0, "skills": []},
            "다리": {"level": 0, "skills": []},
        },
        "cube": {"name": "재장", "level": 1},
        "console": {"common_level": 0, "class_level": 0, "company_level": 0},
        "collection_stage": "R0",
    }

    result = calc_base_stats(sample)
    print("라피 lv200 bt0 최소 조건:", result)
