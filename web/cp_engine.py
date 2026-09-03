"""전투력(CP) 엔진 — 전투력 계산기(`/api/cp`) 전용.

**이 파일은 브라우저로 나가지 않는다.** 산식·계수를 가리는 것이 요구사항이라
`web/src/`(dist로 복사)나 `BUNDLE_FILES`(repo.zip)에 절대 넣지 말 것.
서버(`web/server.py`)만 import 한다.

산식·상수의 정본과 역산 근거: context/scenarios/전투력 산식.md (실측 2026-08-21~22).
검증: 라피 : 레드 후드 40렙 서버값 71,609 재현(±0), 실계정 153명 대조
121명 ±0.1% 이내.
"""
from __future__ import annotations

import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TDIR = os.path.join(_ROOT, "data", "base_stat_tables")


def _load(name: str) -> dict:
    with open(os.path.join(_TDIR, name), encoding="utf-8") as f:
        return json.load(f)


_LEVEL = _load("level_stats.json")
# 등급(SSR/SR/R) — 레벨 표 키가 `등급_클래스_무기유형`이라 이름으로 등급을 찾아야 한다.
# 요청이 `rare`를 주면 그것을 쓰고, 없으면 로스터, 그것도 없으면 SSR(옛 무접두 표 = SSR 곡선).
try:
    with open(os.path.join(_ROOT, "data", "parsed_nikke.json"), encoding="utf-8") as _f:
        _RARE = {k: (v.get("rare") or "SSR") for k, v in json.load(_f).items() if isinstance(v, dict)}
except (OSError, ValueError):
    _RARE = {}
_AFF = _load("affinity.json")
_EQUIP = _load("equipment_stats.json")
_CUBE = _load("cube.json")["_stats"]
_COLL = _load("collection.json")["_stat_table"]

# 캐릭터별 스탯 그룹 예외 — 겉보기 무기와 스탯 곡선이 다른 캐릭터 (하란: SR인데 AR 곡선,
# 렘: MG 곡선 등). 실측: 블라 디버그의 stat_enhance_id (51x 화력·52x 방어·53x 지원 /
# 끝자리 1 AR·2 SR·3 SMG·4 SG·5 RL·6 MG). 없으면 클래스_무기 그대로.
_GROUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cp_char_groups.json")
_GROUPS: dict = {}
if os.path.exists(_GROUP_FILE):
    with open(_GROUP_FILE, encoding="utf-8") as f:
        _GROUPS = json.load(f).get("groups", {})

# 큐브 계수 — 인덱스 = 큐브 레벨 0~15. powers = 92×(스킬1+스킬2+4 | 스킬1+1), 실측 배열.
_CUBE_POWERS = (0, 184, 184, 276, 276, 644, 644, 736, 736, 828, 920, 1012, 1012, 1104, 1104, 1196)
# 소장품 계수 — 인덱스 = 아이템 레벨 0~15. SR 실측, R은 69×(스킬1+6.33) 유도.
_FAV_POWERS = {
    "SR": (874, 874, 874, 874, 874, 1012, 1012, 1012, 1012, 1012, 1150, 1150, 1150, 1150, 1150, 1288),
    "R": tuple(round(69 * (s + 6.33)) for s in (1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4)),
}
# 돌파 고정치 — 스탯별로 다르다 (우리 계산기의 +20 일괄과 다름에 주의).
_GRADE_FIXED = {"hp": 3000, "atk": 20, "def": 100}
# 콘솔(재활용 연구실) 레벨당 스탯.
_CON_RATE = {"common": {"hp": 450}, "class": {"hp": 750, "def": 5}, "corp": {"atk": 25, "def": 5}}
# 오버로드 줄 battlepower = round(계수 × 단계). 우월코드만 82.8, 나머지 전부 69.
_BP_COEF = {"element_bonus": 82.8}
_BP_DEFAULT = 69.0
_OL_KEYS = {"atk_pct", "element_bonus", "max_ammo_pct", "crit_rate", "crit_dmg",
            "charge_speed_pct", "charge_dmg_pct", "accuracy_pct", "def_pct"}
_NO_ITEM = "없음"


# 중간 단계는 **반올림하지 않는다.** 블라블라링크는 부위·단계마다 반올림해 보여 주지만,
# 게임 실측(라피 : 레드 후드 823렙 스탯창)과는 소수를 끝까지 끌고 가는 쪽이 맞았다 —
# 블라 쪽이 그 자리에서 ±1씩 어긋난다. 돌파 단계 내림만 예외다.


def _equip_flat(cls: str, part: str, part_data: dict, corp: str = "") -> dict:
    """부위 하나의 플랫 스탯.

    **배율 = 1 + (제조사가 착용자 기업과 같으면 0.3) + 0.1×강화** — 블라 번들 실측
    (`l = 1 + c + n*s`). 딜 계산기의 장비 모델은 T1~T9의 강화·제조사를 버리지만
    전투력은 둘 다 쓴다. T10(우리 표의 「기업」)은 제조사가 없어 강화만 붙는다.

    받는 모양(`{"t": 단계, "lv": 강화, "corp": 제조사}`)은 프로필의 UI 전용 `_eq`와 같다.
    표의 +N강 칸 대신 0강 원값 × 배율로 계산한다 — 표는 정수 반올림이라 0.5가 유실되고,
    그 0.5가 전투력 ±1로 나타난다(실측).
    """
    t = int(part_data.get("t", 0) or 0)
    if t < 1:
        return {"atk": 0, "def": 0, "hp": 0}
    lv = max(0, min(5, int(part_data.get("lv", 0) or 0)))
    if t >= 10:                                   # T10 — 제조사 없음
        base = _EQUIP["기업"][cls][part]["0"]
        mult = 1 + 0.1 * lv
    else:
        base = _EQUIP["일반"][f"T{t}"][cls][part]
        mult = 1 + (0.3 if part_data.get("corp") and part_data["corp"] == corp else 0) + 0.1 * lv
    return {k: base[k] * mult for k in ("atk", "def", "hp")}


def compute(p: dict) -> dict:
    """옵션 묶음 → {"cp", "hp", "atk", "def"}. 값이 어긋나면 ValueError.

    p 키:
      cls("화력형"…) · weapon("AR"…) · level(1~1000) · grade(0~3) · core(0~15)
      affinity(1~40) · s1·s2·ub(1~10) · cube_lv(0~15)
      coll_stage("없음"|"R0"~"R15"|"SR0"~"SR15")
      equipment({부위: {t: 단계 1~10, lv: 강화 0~5, corp: 제조사|null}})
      ol([[{o,l}|null ×3] ×4]) · corp(착용자 기업 — 제조사 일치 판정)
      console({common, class, corp})   ← 각 연구 레벨
    """
    name = p.get("name") or ""
    base_key = _GROUPS.get(name) or f"{p['cls']}_{p['weapon']}"
    rare = p.get("rare") or _RARE.get(name) or "SSR"
    key = f"{rare}_{base_key}"
    if key not in _LEVEL:
        key = base_key          # 등급 키가 없는 옛 표 — 종전 키로 (배포 순서 안전장치)
    if key not in _LEVEL:
        raise ValueError(f"레벨 표에 없는 조합: {key}")
    # 상한은 **표가 가진 최대 레벨**이다 — 게임이 레벨을 늘리면 표만 갱신하면 된다
    # (1400까지 실측 수집: scraper 쪽 stat_harvest).
    rows = _LEVEL[key]
    top = max(int(k) for k in rows if k.isdigit())
    level = max(1, min(top, int(p["level"])))
    row = rows.get(str(level))
    if row is None:
        raise ValueError(f"레벨 표에 없는 레벨: {level}")
    grade = max(0, min(3, int(p.get("grade", 0))))
    core = max(0, min(15, int(p.get("core", 0))))
    aff_lv = max(1, min(40, int(p.get("affinity", 1))))
    aff = _AFF[p["cls"]][str(aff_lv)]
    con = p.get("console") or {}
    # 콘솔 상한 1000 — 게임 데이터에 상한이 없고 증가치가 완전히 선형이라 계산에는
    # 영향이 없다. 오타 방어용이며 `web/src/app.js`의 CONSOLE_MAX_LV와 같아야 한다.
    con_lv = {k: max(0, min(1000, int(con.get(k, 0)))) for k in ("common", "class", "corp")}

    cube_lv = max(0, min(15, int(p.get("cube_lv", 0))))
    cube = _CUBE[str(cube_lv)] if cube_lv else {"atk": 0, "def": 0, "hp": 0}

    stage = p.get("coll_stage") or _NO_ITEM
    if stage == _NO_ITEM:
        coll = {"atk": 0, "def": 0, "hp": 0}
        fav_pow = 0
    else:
        coll = _COLL.get(stage)
        if coll is None:
            raise ValueError(f"모르는 소장품 단계: {stage!r}")
        fav_grade, fav_lv = stage[:-len(stage.lstrip("RS"))], int(stage.lstrip("RS"))
        fav_pow = _FAV_POWERS[fav_grade][fav_lv]

    equipment = p.get("equipment") or {}
    eq = {"atk": 0.0, "def": 0.0, "hp": 0.0}
    for part in ("머리", "몸통", "팔", "다리"):
        s = _equip_flat(p["cls"], part, equipment.get(part) or {"t": 0}, p.get("corp") or "")
        for k in eq:
            eq[k] += s[k]

    def stat(k: str) -> float:
        # 돌파 단계 내림 + 이후 무반올림 — 이 체인이 서버값을 ±0으로 재현한다.
        g = math.floor(row[k] * (1 + 0.02 * grade) + _GRADE_FIXED[k] * grade)
        base = (g + aff[k]
                + sum(_CON_RATE[t].get(k, 0) * con_lv[t] for t in _CON_RATE))
        return base * (1 + 0.02 * core) + eq[k] + cube[k] + coll[k]

    # **정수로 확정한 뒤** 전투력을 만든다 — 게임 스탯창에 뜨는 그 값이 입력이다.
    # 소수를 그대로 끌고 가면 전투력이 +1 어긋난다(실측: 라피 823렙 996,045 vs 996,044).
    hp, atk, dfn = round(stat("hp")), round(stat("atk")), round(stat("def"))

    bp = 0
    for part_lines in (p.get("ol") or []):
        for line in (part_lines or []):
            if not line or not line.get("o"):
                continue
            o, l = line["o"], max(1, min(15, int(line.get("l", 15))))
            if o not in _OL_KEYS:
                raise ValueError(f"모르는 오버로드 옵션: {o!r}")
            bp += round(_BP_COEF.get(o, _BP_DEFAULT) * l)

    coef = (1.3 + 0.01 * min(10, int(p.get("s1", 1))) + 0.01 * min(10, int(p.get("s2", 1)))
            + 0.02 * min(10, int(p.get("ub", 1)))
            + _CUBE_POWERS[cube_lv] / 1e4 + fav_pow / 1e4 + bp / 1e4)

    # 생존력·공격력치는 **각각 정수로 깎인 뒤** 계수와 곱해진다 (블라 디버그 원문의
    # survive_ability.value·attack_ability.value가 raw_value를 버림한 값이다).
    surv = math.floor(0.7 * (hp + 100 * dfn))
    atkp = math.floor(atk * 1.075 * 18)
    cp = round((surv + atkp) * coef * 0.01)
    return {"cp": cp, "hp": hp, "atk": atk, "def": dfn}
