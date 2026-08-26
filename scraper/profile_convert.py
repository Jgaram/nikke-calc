"""blablalink 원시 응답 → 육성 프로필. **순수 변환만** 담는다 (HTTP·파일 IO 없음).

`profile_fetch.py`에서 분리해 나온 모듈이다. 분리한 이유는 수집 경로가 셋으로 늘었기 때문이다:

| 수집 경로 | 누구 세션 | 어디서 도는가 |
|---|---|---|
| `profile_fetch.py` (CLI) | `scraper/.session_cookie` | 로컬 파이썬 |
| 북마클릿 | 방문자 자기 세션 (**본인 계정만**) | 방문자 브라우저 |
| 서버 어댑터 | 운영자 세션 (타인 공개 프로필 조회) | 서버 |

세 경로가 만드는 `raw`는 **모양이 같다.** 그래서 변환은 한 곳에만 있으면 되고, 여기가 그 한
곳이다. 브라우저(Pyodide)도 이 모듈을 그대로 import한다 — JS로 다시 구현하지 않는다.

`maps`는 조회표 묶음이며 출처가 두 갈래다(CDN·저장소 파일). 만드는 건 호출자 몫이다:
CLI는 `profile_fetch._load_maps()`, 웹은 빌드 때 구운 `dist/profile_maps.json`.
**JSON을 거쳐 온 표는 정수 키가 문자열이 되므로** 조회는 전부 `_lookup()`을 지난다.
"""
from __future__ import annotations

import datetime
import re

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

# ── 옵션 이름 별칭 (두 수집 경로가 함께 쓴다) ─────────────────────────────
# 같은 옵션이 경로·언어설정에 따라 다른 문자열로 온다:
#   블라링크   게임 내부 이름      `IncElementDmg`
#   레츠도로   한국어 약어         `우코`
#   레츠도로   영문 (실측)         `elementalDamage`   ← 이 CSV는 옵션이 통째로 빠졌다
# 표기 흔들림(대소문자·구분자·공백)에 견디도록 **글자만 남겨** 맞춘 뒤 찾는다.
# 여기 없는 이름이 오면 두 경로 모두 «모르는 옵션»으로 경고하므로, 새 표기가 나오면
# 그 경고에 원문이 찍힌다 — 그걸 보고 이 표에 한 줄 더하면 된다.
_ALIAS_SRC = {
    "atk_pct": ["StatAtk", "attack", "attackPercent", "atk", "공증", "공격력", "공격력증가"],
    "element_bonus": ["IncElementDmg", "elementalDamage", "elementDamage", "elementBonus",
                      "elementalAdvantage", "우코", "우월코드"],
    "max_ammo_pct": ["StatAmmoLoad", "maxAmmo", "ammoCapacity", "ammo", "장탄", "장탄수",
                     "최대장탄수"],
    "crit_rate": ["StatCritical", "critRate", "criticalRate", "crit", "크확",
                  "크리티컬확률"],
    "crit_dmg": ["StatCriticalDamage", "critDamage", "criticalDamage", "크댐",
                 "크리티컬데미지"],
    "charge_speed_pct": ["StatChargeTime", "chargeSpeed", "chargeTime", "차속", "차지속도"],
    "charge_dmg_pct": ["StatChargeDamage", "chargeDamage", "chargeDmg", "차댐",
                       "차지데미지"],
    "accuracy_pct": ["StatAccuracyCircle", "accuracy", "hitRate", "hit", "명중", "명중률"],
    "def_pct": ["IncHurtDef", "StatDef", "defence", "defense", "def", "방어", "방어력"],
}


def _norm_opt(name: str) -> str:
    """옵션 이름을 맞춰 보기 좋게 — 구분자·공백·괄호를 떼고 ASCII만 소문자로."""
    return re.sub(r"[\s_\-·:()%\[\]/]+", "", str(name or "")).lower()


OPT_ALIAS = {_norm_opt(a): key for key, names in _ALIAS_SRC.items() for a in names}


def opt_key(name: str) -> str | None:
    """옵션 이름 → 우리 키. 모르는 이름이면 None (호출자가 경고한다)."""
    return OPT_ALIAS.get(_norm_opt(name))
PARTS = [("head", "머리"), ("torso", "몸통"), ("arm", "팔"), ("leg", "다리")]

NO_ITEM = "없음"          # calculator.base_stat.NO_ITEM — 미장착
OVERLOAD_TIER = 10        # equip_tier 10 = 오버로드 장비(강화 0~5), 1~9 = T1~T9 등급

# 장비 갈래 표식. `_track` 값은 `cost/tables.json` 장비강화.레벨도달비용의 키와 **같아야**
# 한다 — `.agent/skills/report-growth`가 이 값으로 강화 한 칸의 비용을 고른다.
#   tier 10                     → 오버로드 장비 (계산기의 `기업` 표가 이 갈래다)
#   tier 9 + corporation_type≠0 → T9 기업 장비 (역시 강화 0~5, 계산기에 표가 없다)
#   tier 1~9 + corporation_type 0 → 일반 장비 (강화 없음)
TRACK_OVERLOAD = "오버로드"
TRACK_CORP = "T9"

# 재활용 연구실(= 계산기의 "콘솔") tid → 소속. 1001 공통 하나 · 11xx 역할군 셋 · 12xx 기업 다섯.
# 소속 순서는 인게임 재활용 연구실 표시 순서이며, `parsed_nikke.json`의 `class`·`manufacturer`
# 값과 글자까지 같아야 한다 (계산기가 그 문자열로 조회한다).
CONSOLE_TIDS = {
    1001: ("common_level", ""),
    1101: ("class_level", "화력형"), 1102: ("class_level", "방어형"),
    1103: ("class_level", "지원형"),
    1201: ("company_level", "엘리시온"), 1202: ("company_level", "미실리스"),
    1203: ("company_level", "테트라"), 1204: ("company_level", "필그림"),
    1205: ("company_level", "어브노말"),
}

# `maps`에 있어야 하는 표. 빠지면 조용히 빈 결과가 나오므로 `build_profile`이 먼저 검사한다.
MAP_KEYS = ("id_map", "res_name", "fav_map", "weapons", "fav_chars",
            "cube_names", "skill_table")


def _lookup(table, key, default=None):
    """정수 키 표를 JSON 왕복 후에도 같은 코드로 조회한다.

    CDN·파이썬에서 만든 표는 키가 int인데, 빌드 때 JSON으로 구우면 문자열이 된다.
    호출부를 둘로 갈라 쓰면 한쪽이 조용히 전부 미스가 되므로 조회를 여기로 모은다.
    """
    if key in table:
        return table[key]
    return table.get(str(key), default)


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


def build_option_map(state_effects: list, skill_table: dict) -> tuple[dict, dict, list]:
    """state_effects 전체 → {option_id: (equip_skills 키, 퍼센트값, 레벨|None)}.

    반환: (옵션 사전, 미지 function_type, 표에 없는 수치 목록)

    레벨을 함께 담는 이유: 계산에는 퍼센트만 쓰지만, **UI는 줄마다 "우월 코드 15단계"처럼
    단계를 보여 주고 고쳐야 한다.** 합산 퍼센트만 남기면 어느 줄이 몇 단계였는지 복원할 수
    없다 — `_verify_option`이 이미 단계를 알아내므로 그 값을 버리지 않고 같이 낸다.
    """
    opt, unknown, off_table = {}, {}, []
    # 배치마다 겹쳐 오므로 옵션 id로 중복 제거한다 (같은 경고가 배치 수만큼 뜨지 않게).
    for se in {s["id"]: s for s in state_effects}.values():
        fd = se["function_details"][0]
        ftype = fd["function_type"]
        key = FUNC_TO_EQUIP.get(ftype) or opt_key(ftype)
        val = abs(fd["function_value"]) / 100.0
        if key is None:
            unknown[ftype] = se["id"]
            continue
        lv = _verify_option(key, val, skill_table)
        if lv is None:
            off_table.append((ftype, key, val, se["id"]))
        opt[str(se["id"])] = (key, val, lv)
    return opt, unknown, off_table


def _equipment(detail: dict) -> dict:
    """장비 4부위 → 계산기 `equipment` (+ 갈래 표식).

    빈 슬롯은 `tier: "없음"`이다. **오버로드 강화0으로 적으면 안 된다** — 그건 "가장 낮은
    장착 상태"라 부위당 수천 atk이 유령으로 붙는다(방어형 머리 기준 +4010).

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
                         "corp": EQUIP_CORP.get(corp, f"?{corp}"),
                         "_track": TRACK_CORP}
        elif tier >= 1:
            out[ko_p] = {"tier": f"T{tier}"}                     # 일반 T1~T9 (강화 없음)
        else:
            out[ko_p] = {"tier": NO_ITEM}                        # 미장착
    return out


# 장비 제조사 코드 → 기업. `*_equip_corporation_type` 실측(2026-08-22).
EQUIP_CORP = {1: "엘리시온", 2: "미실리스", 3: "테트라", 4: "필그림", 7: "어브노말"}


def _equipment_raw(detail: dict) -> dict:
    """장비 4부위의 **원본 그대로** — 단계·강화·제조사. UI 전용(`_eq`).

    위 `_equipment`는 딜 계산기의 모델에 맞춰 **T1~T9의 강화 단계와 제조사를 버린다**
    (그쪽은 일반 장비에 강화가 없다고 본다). 그런데 인게임 전투력은 둘 다 쓴다 —
    배율 = 1 + (제조사가 본인 기업과 같으면 0.3) + 0.1×강화. 버린 값을 여기 남겨
    전투력 계산기가 실제 전투력을 낼 수 있게 한다. `_` 접두사라 시뮬에는 넘어가지 않는다.
    """
    out = {}
    for api_p, ko_p in PARTS:
        tier = detail[f"{api_p}_equip_tier"]
        if tier < 1:
            out[ko_p] = {"t": 0}
            continue
        out[ko_p] = {"t": tier, "lv": detail[f"{api_p}_equip_lv"],
                     "corp": EQUIP_CORP.get(detail.get(f"{api_p}_equip_corporation_type") or 0)}
    return out


def _equip_skills(detail: dict, opt_map: dict) -> tuple[dict, list]:
    """오버로드 12슬롯 → (계산기 `equip_skills`, 줄 목록).

    `state_effects`는 옵션 id로 **중복 제거**되어(같은 옵션이 2부위면 1번만 등장) 합산에 못
    쓴다. 그래서 슬롯을 직접 순회하고 `state_effects`는 옵션id → 스탯 사전으로만 쓴다.

    `PER_LINE_KEYS`(최대 장탄·차지 속도)는 **줄별 퍼센트 리스트**로, 나머지는 합산
    스칼라로 낸다 — 앞의 둘만 단계별로 따로 반올림되기 때문이다.

    두 번째 반환값은 **UI 전용 줄 목록**이다: `[[{"o": 키, "l": 단계}, …3], …4부위]`.
    계산은 이걸 쓰지 않는다(프로필에 `_ol`로 담기고 `_` 접두사라 시뮬에 넘어가지 않는다).
    편집기가 "우월 코드 15단계"를 보여 주고 고치려면 합산 전 상태가 필요해서 남긴다.
    """
    out: dict = {k: [] if k in PER_LINE_KEYS else 0.0 for k in EQUIP_KEYS}
    lines: list = []
    for api_p, _ in PARTS:
        part_lines: list = []
        for i in (1, 2, 3):
            oid = str(detail[f"{api_p}_equip_option{i}_id"])
            if oid not in opt_map:
                part_lines.append(None)
                continue
            key, val, lv = opt_map[oid]
            part_lines.append({"o": key, "l": lv})
            if key in PER_LINE_KEYS:
                out[key].append(round(val, 4))
            else:
                out[key] = round(out[key] + val, 4)
        lines.append(part_lines)
    # 줄별 리스트는 큰 단계부터 — 사람이 읽을 때 주력 옵션이 먼저 오게 한다.
    # 그룹은 값으로 묶이므로 순서는 결과에 영향을 주지 않는다.
    for k in PER_LINE_KEYS:
        out[k].sort(reverse=True)
    return out, lines


def _collection(detail: dict, fav_map: dict, name: str, weapon: str | None,
                warn: list) -> tuple[str, int]:
    """소장품 슬롯 → (`collection_stage`, 애장품 단계).

    슬롯 하나를 소장품(R·SR)과 애장품(SSR)이 공유한다. 애장품은 SR15와 스탯이 같으므로
    `SR15`로 적고, **단계**만 따로 돌려준다 — 단계는 스탯이 아니라 스킬 판본을 바꾸며
    계산기가 `favorite_stage`로 그대로 받는다(`calculator/buff_manager.py char_effects()`).
    소장품(R·SR)을 꼈거나 슬롯이 비었으면 애장품 0단계다.
    """
    tid = detail.get("favorite_item_tid", 0)
    lv = detail.get("favorite_item_lv", 0)
    if not tid:
        return NO_ITEM, 0
    info = _lookup(fav_map, tid)
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
                has_favorite: bool = False, cube_names: dict | None = None,
                cube_field_seen: bool = False) -> dict:
    """`eff` = GetUserCharacters 항목(유효 레벨·돌파·코강 = 동기화 반영값).
    상세의 lv는 개별 레벨이라 동기화 소대에 덮이지 않은 원값이므로 쓰지 않는다."""
    stage, fav_stage = _collection(detail, fav_map, name, weapon, warn)
    equip_skills, ol_lines = _equip_skills(detail, opt_map)
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
        "equip_skills": equip_skills,
        "collection_stage": stage,
        # UI 전용 — 합산 전 오버로드 줄. `_` 접두사라 시뮬에 넘어가지 않는다.
        "_ol": ol_lines,
        # UI 전용 — 장비 원본(단계·강화·제조사). 전투력 계산에만 쓴다.
        "_eq": _equipment_raw(detail),
        # UI 전용 — 인게임 전투력. 딜과 순위가 다르지만 «내가 아는 숫자»라
        # 목록 정렬 기본값으로 쓴다. 계산에는 들어가지 않는다.
        "_combat": int(detail.get("combat") or eff.get("combat") or 0),
    }
    # UI 전용 — 이 캐릭터가 **장착 중인** 큐브. `detail`(캐릭터 하나의 상세 응답) 안에
    # `harmony_cube_tid`·`harmony_cube_lv`가 그대로 있는데, 예전엔 이걸 캐릭터별로
    # 저장하지 않고 `_observed_cubes()`가 계정 전체로 뭉개 버려서 「모든 캐릭터가
    # 같은 큐브로 나온다」는 결과를 냈다(실측: blablalink 사이트는 캐릭터마다 정확한
    # 큐브를 보여 주는데 이쪽만 뭉개져 있었다 — API가 안 준 게 아니라 우리가 버렸다).
    # **미장착도 명시로 남긴다(level 0).** 예전에는 안 낀 캐릭터의 `cube` 키를 통째로
    # 빼서, 러너 기본값(렐릭 베어 Lv15)이 대신 씌워졌다. 큐브 공통 스킬 `안티 코드 HC`가
    # 우월 코드 대미지를 최대 +19.09% 주고 플랫 공격력도 붙으므로, 실제로 안 낀 니케의
    # 딜이 조용히 부풀었다(2026-08-24 실측: 드레이크 +17%p 과대평가).
    # 큐브는 **`_cube`(UI 전용)로 적는다 — 시뮬에는 넘기지 않는다.**
    # `_` 접두 키는 `spec.deep_merge`가 건너뛰므로 편성 계산에는 러너 기본값
    # (렐릭 베어 Lv15)이 일괄로 쓰인다. 이렇게 나눈 이유:
    #   · 큐브는 육성 상태가 아니라 자유롭게 갈아끼우는 자원이다. 지금 안 끼고 있어도
    #     실제로 그 니케로 돌릴 때는 끼우므로, 장착 상태를 그대로 편성 계산에 쓰면
    #     실전보다 딜이 낮게 나온다(2026-08-24: 안 낀 상태를 0으로 적었더니 그랬다).
    #   · 반대로 «내 전투력»은 지금 실제 상태를 보여야 한다 — 안 꼈으면 안 낀 값이다.
    # 그래서 사실(장착 중인 큐브·미장착 0)은 `_cube`에 그대로 남기고, 전투력 계산기가
    # 그걸 읽는다. 편성에서 특정 니케만 다른 큐브를 쓰고 싶으면 카드 톱니에서 고치면
    # 되고, 그건 수정 층의 `cube`(밑줄 없음)라 시뮬에 반영된다.
    #
    # 블라는 큐브를 안 낀 캐릭터에게 `harmony_cube_lv` 필드를 아예 주지 않는다(실측:
    # 199명 중 33명만 보유 = 그 33명이 실제 장착분). 그래서 **응답 전체에 필드가 하나도
    # 없으면** 출처가 큐브를 안 주는 것이므로 «미장착»으로 단정하지 않고 비워 둔다.
    if cube_names is not None:
        cube_nm = _lookup(cube_names, detail.get("harmony_cube_tid", 0))
        cube_lv = detail.get("harmony_cube_lv", 0)
        if cube_nm and cube_lv:
            out["_cube"] = {"name": cube_nm, "level": cube_lv}
        elif cube_field_seen:
            out["_cube"] = {"name": cube_nm or "", "level": 0}
    # UI 전용 — 장착 중인 코스튬(스킨) id. 외형뿐이라 계산에는 일절 안 들어간다.
    # **id만 적는다.** 이름·그림 번호는 CDN 표(`data/costume_index.json`)가 정본이라,
    # 프로필에 베껴 두면 스킨 이름이 바뀌었을 때 프로필 쪽만 낡는다.
    # 0(기본 코스튬)은 키 자체를 안 만든다 — 없음이 곧 기본이다.
    if detail.get("costume_tid"):
        out["_costume"] = int(detail["costume_tid"])
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


def _MASKED(r: dict) -> bool:
    """블라링크가 «안 알려 준다»고 표시한 항목인가.

    전초기지 정보를 비공개로 두면 재활용 연구실이 빈 목록이 아니라 음수 가림값으로
    온다(실측 `tid -9999, lv -9999`). 어느 음수를 쓸지는 저쪽 사정이므로 **-9999만**
    보지 않고 «음수면 가림»으로 넓게 잡는다 — 레벨도 tid도 음수일 수 없다.
    """
    try:
        return int(r.get("tid", 0)) < 0 or int(r.get("lv", 0)) < 0
    except (TypeError, ValueError):
        return True                      # 숫자가 아니면 읽을 수 없는 값이다


def _console(researches: list, warn: list) -> dict | None:
    """재활용 연구실 목록 → 계산기 `console`. 못 읽으면 None.

    역할군·기업은 소속별 dict로 그대로 넘긴다(계산기가 캐릭터 소속으로 골라 쓴다).
    모르는 tid가 새로 생기면 조용히 빠뜨리지 않고 경고한다 — 다만 **가림값은 다르다**
    (아래 `_MASKED`).
    """
    if not researches:
        return None
    out: dict = {}
    for r in researches:
        # 전초기지를 «비공개»로 두면 항목이 빠지는 게 아니라 **가림값**으로 온다
        # (실측: tid -9999, lv -9999가 아홉 줄). 그건 «모르는 항목»이 아니라 «안 알려 준
        # 항목»이다 — 개발자에게 하는 말(CONSOLE_TIDS에 추가하라)을 아홉 번 띄우면
        # 정작 사람이 할 일(공개로 바꾸기)을 적은 한 줄이 그 밑에 묻힌다.
        if _MASKED(r):
            continue
        mapped = CONSOLE_TIDS.get(r["tid"])
        if mapped is None:
            warn.append(f"모르는 재활용 연구실 항목(tid {r['tid']}, lv {r['lv']})을 건너뜁니다. "
                        f"신규 역할군·기업이면 profile_convert.py의 CONSOLE_TIDS에 추가해야 합니다")
            continue
        key, bucket = mapped
        if bucket:
            out.setdefault(key, {})[bucket] = r["lv"]
        else:
            out[key] = r["lv"]
    missing = [k for k, _ in CONSOLE_TIDS.values() if k not in out]
    if missing:
        # 하나도 못 받았으면 «일부가 빠졌다»가 아니라 통째로 못 받은 것이고, 그 이유는
        # 호출자가 한 줄로 설명한다(`_to_profile`). 여기서 항목 이름까지 늘어놓으면
        # 같은 말을 두 번 하는 셈이다.
        if out:
            warn.append("재활용 연구실에서 콘솔 항목을 다 못 받았습니다 ("
                        + ", ".join(sorted(set(missing))) + ") — 기존 값을 그대로 둡니다")
        return None
    return out


def _observed_cubes(details: list, cube_names: dict) -> dict:
    """장착 중인 큐브에서 관찰된 {큐브명: 최고 레벨}. 보유 큐브의 **하한**일 뿐이다."""
    out: dict[str, int] = {}
    for d in details:
        tid = d.get("harmony_cube_tid", 0)
        nm = _lookup(cube_names, tid)
        if nm:
            out[nm] = max(out.get(nm, 0), d.get("harmony_cube_lv", 0))
    return dict(sorted(out.items()))


# ── 공개 진입점 ───────────────────────────────────────────────────────────
def assemble(entries: dict, name: str, *, source: str,
             openid=None, area=None, console=None, synchro_level=None,
             cubes: dict | None = None, console_warnings: list | None = None,
             union: dict | None = None,
             old: dict | None = None, fetched_at: str | None = None) -> dict:
    """캐릭터 항목 묶음 → 프로필 dict. **모든 수집 경로가 이 한 곳에서 조립한다.**

    출처가 셋(블라링크 API·레츠도로 CSV·불러온 파일)이라 조립을 각자 하면 모양이 갈린다.
    계산기·편집기·저장 구조는 출처를 구분하지 않으므로, 갈리는 순간 조용히 틀린다.

    `old`가 있으면 이번 출처가 주지 않는 계정 값(콘솔·동기화레벨)을 **덮지 않고 보존한다.**
    """
    old = old or {}
    old_acct = old.get("_account") or {}
    account = {
        "synchro_level": synchro_level or old_acct.get("synchro_level"),
        "_synchro_note": "동기화 소대 레벨. 러너의 레벨 정책이 `sync`일 때만 쓴다. 기본 정책은 "
                         "기본 스펙 레벨(400) 고정 — 솔로레이드가 그렇게 돌기 때문이다.",
        "console": console or old_acct.get("console"),
        # 계산 때마다 다시 알려야 하는 계정 단위 경고. 러너가 결과에 싣는다.
        "console_warnings": console_warnings or [],
        "_console_note": "블라링크 경로는 전초기지의 recycle_room_researches에서 자동으로 온다. "
                         "역할군·기업은 소속별 레벨을 그대로 담고 계산기가 캐릭터 소속으로 골라 "
                         "쓴다. 비어 있으면 러너가 기본 스펙 값(180/100/100)을 쓰고 그 사실을 "
                         "보고한다. CSV 경로는 이 값을 주지 않는다.",
        # 유니온(길드). 유니온 레이드 화면이 이름을 띄우는 데만 쓴다 — 계산에는 안 들어간다.
        # 안 준 출처(CSV 등)면 기존 값을 보존한다.
        "union": union or old_acct.get("union"),
        "_union_note": "블라링크 Game/GetMyGuildInfo. 이름 표시용이고 계산과 무관하다.",
        "cubes": cubes if cubes is not None else (old_acct.get("cubes") or {}),
        "_cubes_note": "장착 중인 큐브에서 관찰된 **보유 하한**. 보유 큐브 전체를 주는 출처는 "
                       "없다. 큐브는 자유롭게 갈아끼우므로 육성 상태가 아니라 케이스가 정하는 "
                       "축이며, 이 목록은 '그 큐브를 그 레벨로 갖고 있는가' 확인에만 쓴다.",
    }
    return {
        "_meta": {
            "name": name,
            "openid": openid,
            "area": area,
            "fetched_at": fetched_at or datetime.datetime.now().astimezone().isoformat(
                timespec="seconds"),
            "source": source,
            "roster": len(entries),
            "synced": sum(1 for e in entries.values() if not e.get("_unsynced")),
        },
        "_account": account,
        "chars": dict(sorted(entries.items())),
    }


def build_profile(raw: dict, maps: dict, name: str, old: dict | None = None,
                  fetched_at: str | None = None) -> tuple[dict, list[dict]]:
    """원시 응답 묶음 → (육성 프로필, 알림 목록).

    Parameters
    ----------
    raw : {"openid", "area", "characters", "details", "state_effects", "outpost"}
          수집 경로가 무엇이든 이 모양이어야 한다. `outpost`는 없거나 None일 수 있다
          (그때 콘솔·동기화레벨은 `old`의 값을 보존한다).
    maps : `MAP_KEYS`의 조회표 묶음. CLI는 `profile_fetch._load_maps()`가 만들고,
           웹은 빌드 때 구운 `dist/profile_maps.json`을 쓴다.
    old  : 기존 프로필. 콘솔·동기화레벨을 API가 안 줄 때 **덮어쓰지 않고 보존**하는 데 쓴다.

    Returns
    -------
    (profile, notices)
        notices 항목은 `{"level": "warn"|"info", "text": str}`. 수집 경로가 셋이라
        경고를 print로 흘리면 웹에서 사라진다 — 그래서 반환값으로 올린다.
        `profile-sync` SKILL.md가 "실행 로그의 경고를 하나도 빠뜨리지 말고 유저에게
        옮긴다"고 요구하는 대상이 이 목록이다.
    """
    missing_maps = [k for k in MAP_KEYS if k not in maps]
    if missing_maps:
        raise ValueError(
            f"maps에 {missing_maps}가 없다. 빠진 표는 조용히 전부 미스가 되어 "
            f"장비·소장품이 통째로 빠진 프로필이 나온다 — 만들지 않고 끊는다.")

    id_map = maps["id_map"]
    res_name = maps["res_name"]
    fav_map = maps["fav_map"]
    weapons = maps["weapons"]
    fav_chars = maps["fav_chars"]
    cube_names = maps["cube_names"]
    skill_table = maps["skill_table"]

    old = old or {}
    notices: list[dict] = []

    def note(level: str, text: str, names: list | None = None):
        """알림 하나. **`text`에 파이썬 repr을 박지 않는다** —
        `{'로산나': 1}`·`['D', 'E.H.', …]`가 그대로 화면으로 새어 나가기 때문이다.
        이름은 `names`에 담아, 보여 주는 쪽이 접거나 줄여서 쓰게 한다."""
        item = {"level": level, "text": text}
        if names:
            item["names"] = [str(x) for x in names]
        notices.append(item)

    def warn(text: str, names: list | None = None):
        note("warn", text, names)

    def info(text: str, names: list | None = None):
        note("info", text, names)

    characters = raw["characters"]
    details = raw["details"]
    outpost = raw.get("outpost") or {}

    opt_map, unknown, off_table = build_option_map(raw.get("state_effects") or [], skill_table)
    if unknown:
        warn(f"모르는 오버로드 옵션 {len(unknown)}종이 있어 계산에서 빠집니다. "
             f"신규 옵션이면 profile_convert.py의 FUNC_TO_EQUIP에 추가해야 합니다.",
             sorted(unknown))
    for ftype, key, val, sid in off_table:
        warn(f"옵션 수치가 equipment_skills 표에 없다: {ftype}→{key} {val}% (id {sid}). "
             f"매핑이 틀렸거나 표가 낡았다")

    eff_by_code = {c["name_code"]: c for c in characters}
    char_warn: list[str] = []
    entries, skipped = {}, []
    # 이 응답이 큐브 정보를 주는 출처인가 — 한 명이라도 필드가 있으면 준다는 뜻이고,
    # 그러면 필드가 없는 캐릭터는 «모른다»가 아니라 «지금 안 끼고 있다»다(_to_profile 주석).
    cube_field_seen = any("harmony_cube_lv" in d for d in details)
    for d in details:
        cname = _lookup(res_name, _lookup(id_map, d["name_code"]))
        if cname is None:
            skipped.append(d["name_code"])
            continue
        entries[cname] = _to_profile(d, eff_by_code[d["name_code"]], opt_map, fav_map,
                                     cname, _lookup(weapons, cname), char_warn,
                                     cname in fav_chars, cube_names, cube_field_seen)

    # 콘솔은 전초기지에서 자동으로 온다. 못 받았으면 기존 손입력 값을 보존한다.
    console_warn: list[str] = []
    researches = outpost.get("recycle_room_researches") or []
    console = _console(researches, console_warn) or (old.get("_account") or {}).get("console")
    synchro_level = outpost.get("synchro_level") or (old.get("_account") or {}).get("synchro_level")

    profile = assemble(
        entries, name,
        source="blablalink Game/GetUserCharacters + Game/GetUserCharacterDetails "
               "+ Game/GetUserProfileOutpostInfo",
        openid=raw.get("openid"), area=raw.get("area"),
        console=console, synchro_level=synchro_level,
        cubes=_observed_cubes(details, cube_names),
        console_warnings=console_warn, union=raw.get("union"),
        old=old, fetched_at=fetched_at,
    )

    # ── 알림 조립 (CLI·웹이 같은 문구를 낸다) ────────────────────────────
    info(f"니케 {len(entries)}종을 읽었습니다."
         + (f" ({len(skipped)}개는 이름을 못 맞춰 빠졌습니다)" if skipped else ""))
    if skipped:
        warn(f"이름을 못 맞춘 항목 {len(skipped)}개 — 아직 등록되지 않은 신규 캐릭터일 수 "
             f"있습니다. 그 항목은 프로필에서 빠집니다.", skipped[:40])
    if console is None:
        # 전초기지 정보를 비공개로 두면 `recycle_room_researches`가 통째로 비어서 온다
        # (`outpost.is_hide`). 그때는 «못 받았다»가 아니라 **왜** 못 받았는지를 말해야
        # 유저가 고칠 수 있다.
        hidden = bool((raw.get("outpost") or {}).get("is_hide"))
        warn("재활용 연구실(콘솔) 레벨을 못 받았습니다 — 기본 스펙 값"
             "(공통 180 / 역할군 100 / 기업 100)으로 계산합니다."
             + (" 블라블라링크에서 전초기지 정보를 «공개»로 바꾸면 자동으로 들어옵니다."
                if hidden else "")
             + " 지금 바로 넣으려면 스펙 고르개 옆 톱니(계정 공통 설정)에서 고치세요.")
    for w in char_warn + console_warn:
        warn(w)

    low = {n: e["favorite_stage"] for n, e in entries.items()
           if e.get("favorite_stage") is not None and e["favorite_stage"] < 3}
    if low:
        warn(f"애장품 단계가 3 미만인 캐릭터 {len(low)}명 — 실제 단계의 스킬 판본으로 "
             f"계산합니다. 기본 스펙(3단계)보다 딜이 낮게 나오는 게 정상입니다.",
             [f"{n} {v}단계" for n, v in sorted(low.items())])

    off = sorted(n for n, e in entries.items() if e.get("_unsynced"))
    if off:
        info(f"동기화 소대 밖 {len(off)}종 — 미보유가 아니라 소대에 넣지 않아 인게임 레벨이 "
             f"1인 것입니다. 레벨은 정책이 정하므로 계산에는 영향이 없지만, 이쪽은 대체로 "
             f"스킬·장비 투자가 없어 딜이 낮게 나옵니다.", off)

    no_item = sum(1 for e in entries.values() if e["collection_stage"] == NO_ITEM)
    empty = sum(1 for e in entries.values()
                if all(p.get("tier") == NO_ITEM for p in e["equipment"].values()))
    info(f"소장품·애장품이 없는 캐릭터 {no_item}종, 장비 4부위가 모두 빈 캐릭터 {empty}종. "
         f"이들을 덱에 넣으면 딜이 낮게 나오는 게 정상입니다.")

    # T9 기업 장비는 제조사가 캐릭터 기업과 같아야 +30%가 붙는다. `makers`가 없으면
    # (오래된 profile_maps.json 등) 조용히 건너뛴다 — 계산 자체는 장비 쪽 `corp`만으로
    # 이미 맞게 되고, 이 알림은 "손해 보고 있다"를 알려 주는 보너스일 뿐이다.
    makers = maps.get("makers") or {}
    if makers:
        corp_parts = {n: {p: d for p, d in e["equipment"].items() if d.get("_track") == TRACK_CORP}
                      for n, e in entries.items()}
        corp_parts = {n: v for n, v in corp_parts.items() if v}
        if corp_parts:
            parts_n = sum(len(v) for v in corp_parts.values())
            info(f"T9 기업 장비 {parts_n}부위({len(corp_parts)}명) — 제조사·강화가 계산에 "
                 f"들어갑니다(기본값×(1+0.3×기업일치+0.1×강화)).")
            off_corp = {n: [p for p, d in v.items() if d["corp"] != _lookup(makers, n)]
                       for n, v in corp_parts.items()}
            off_corp = {n: v for n, v in off_corp.items() if v}
            if off_corp:
                warn(f"캐릭터 기업과 다른 기업 장비를 낀 캐릭터 {len(off_corp)}명 — 제조사 "
                     f"보너스(+30%)를 못 받습니다(인게임도 마찬가지입니다).",
                     [f"{n}({','.join(v)})" for n, v in sorted(off_corp.items())])
    unknown_corp = sorted({d["corp"] for e in entries.values() for d in e["equipment"].values()
                          if str(d.get("corp", "")).startswith("?")})
    if unknown_corp:
        warn(f"모르는 장비 제조사 코드 {unknown_corp} — profile_convert.py의 EQUIP_CORP에 "
             f"추가해야 합니다 (parsed_nikke.json의 manufacturer와 글자가 같아야 한다).")

    return profile, notices
