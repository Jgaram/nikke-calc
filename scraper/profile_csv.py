"""레츠도로 CSV(`니케정보_YYYY-MM-DD.csv`) → 육성 프로필.

**로그인이 필요 없는 세 번째 수집 경로다.** 블라링크 경로(CLI·북마클릿·서버)는 세션 쿠키를
요구하지만, 이쪽은 사용자가 레츠도로에서 내려받은 CSV를 그대로 놓으면 끝난다.

산출물은 `profile_convert.build_profile()`과 **같은 모양**이어야 한다 — 계산기·편집기·저장
구조가 출처를 구분하지 않기 때문이다. 그래서 프로필 조립은 여기서 하지 않고
`profile_convert.assemble()`에 넘긴다.

CSV가 주지 않는 것 (기본 스펙 값이 남는다 — 알림으로 반드시 보고한다):
    호감도 · 애장품 단계 · 큐브 · openid
"""
from __future__ import annotations

import csv
import io
import re

from profile_convert import (
    EQUIP_KEYS,
    NO_ITEM,
    PER_LINE_KEYS,
    _verify_option,
    assemble,
    opt_key,
)

# CSV 부위명 → 계산기 부위명. **`장갑`이 우리 `팔`이다** — 이름이 다르므로 매핑이 필요하다.
CSV_PARTS = [("머리", "머리"), ("몸통", "몸통"), ("장갑", "팔"), ("다리", "다리")]

# CSV 옵션 약칭 → equip_skills 키. 레츠도로 표기 그대로 받는다.
CSV_OPT = {
    "우코": "element_bonus",
    "공증": "atk_pct",
    "장탄": "max_ammo_pct",
    "크확": "crit_rate",
    "크댐": "crit_dmg",
    "명중": "accuracy_pct",
    "차댐": "charge_dmg_pct",
    "차속": "charge_speed_pct",
    "방어": "def_pct",
}

# CSV는 같은 값을 두 번 준다: 부위별 옵션 12칸과, 캐릭터당 합계 한 칸.
# 우리는 **부위칸만** 읽는다 — 단계를 역산해야 하고 장탄·차속은 줄별로 따로 써야
# 하기 때문이다. 그런데 부위칸이 비어 있고 합계칸에만 값이 있는 CSV가 있다(다른
# 계산기들도 같이 0으로 읽는다는 제보). 그 경우 조용히 0이 되면 딜이 낮게 나오는데
# 이유가 어디에도 안 보인다. 그래서 **대조해서 알린다.**
CSV_AGG = {
    "우코(%)": "element_bonus", "공증(%)": "atk_pct", "장탄(%)": "max_ammo_pct",
    "크확(%)": "crit_rate", "크댐(%)": "crit_dmg", "명중(%)": "accuracy_pct",
    "차댐(%)": "charge_dmg_pct", "차속(%)": "charge_speed_pct", "방어(%)": "def_pct",
}
AGG_TOL = 0.5               # 합계칸은 표시용 반올림이라 이 정도는 어긋나도 넘어간다

# 옵션 이름 별칭은 `profile_convert.OPT_ALIAS` 한 곳에 있다 — 두 벌로 두면 한쪽만
# 고쳐지는 사고가 난다. 여기서는 그걸 그대로 쓴다 (한국어 약어도 그 표에 있다).
CORP_TIER_CSV = 10          # `_티어` 10 = 기업 장비(강화 0~5)
REQUIRED_COLS = ("이름", "돌파", "코강", "스킬1", "스킬2", "버스트스킬")


def _num(v, default=0):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


def _fnum(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _collection(raw: str) -> tuple[str, int | None]:
    """소장품 칸 → (`collection_stage`, 애장품 단계).

    한 칸에 두 가지가 들어온다:

    - `"SR 5"` · `"R 0"` → 소장품(R·SR)과 그 레벨. `"SR5"`로 옮긴다.
    - `"애장품 ★★★"`     → **애장품 단계**다. 채운 별 수가 곧 1·2·3단계다.

    애장품은 소장품 슬롯을 공유하고 스탯이 SR15와 같으므로 `SR15`로 적고, **단계**만
    따로 돌려준다 — 단계는 스탯이 아니라 스킬 판본을 바꾸며 계산기가 `favorite_stage`로
    그대로 받는다(`calculator/buff_manager.py char_effects()`).
    """
    s = (raw or "").strip()
    if not s or s in (NO_ITEM, "-"):
        return NO_ITEM, None
    if s.startswith("애장품"):
        stage = s.count("★")
        # 별이 하나도 안 보이면(표기가 바뀌었다면) 단계를 지어내지 않고 비워 둔다
        return "SR15", (stage if 1 <= stage <= 3 else None)
    parts = s.replace(" ", " ").split()
    if len(parts) == 2 and parts[0] in ("R", "SR"):
        return f"{parts[0]}{_num(parts[1])}", 0
    return s.replace(" ", ""), 0


def _snap_option(key: str, val: float, table: dict) -> tuple[int | None, float]:
    """CSV 퍼센트 → (단계, **표의 정확한 값**). 어느 단계에도 안 맞으면 (None, val).

    레츠도로는 화면에 보이는 값을 소수 둘째 자리로 **반올림해서** 내려 준다. 그래서
    명중 15단계가 표에서는 14.63인데 CSV에는 14.64로 적힌다 — 그대로 비교하면 «표에
    없는 값»이 된다. 표의 어느 칸과 0.05%p 안으로 가까우면 그 단계로 붙이고, 계산에는
    **CSV 값이 아니라 표의 값**을 쓴다. 정본은 게임 CDN 표이지 CSV의 표시값이 아니다.
    """
    best_lv, best_gap, best_val = None, None, val
    for lv, v in enumerate(table.get(key, []), start=1):
        pct = v * 100
        gap = abs(pct - val)
        if best_gap is None or gap < best_gap:
            best_lv, best_gap, best_val = lv, gap, pct
    if best_gap is not None and best_gap <= 0.05:
        return best_lv, round(best_val, 4)
    return None, val


def _equipment_and_lines(row: dict, skill_table: dict, warn: list, name: str):
    """4부위 → (`equipment`, `equip_skills`, `_ol` 12줄).

    옵션은 퍼센트로만 오므로 단계를 `_verify_option`으로 역산한다. 표에 없는 수치는
    단계를 못 정하니 **버리지 않고 경고**한다 — 조용히 빼면 딜이 낮게 나오는데 이유가 안 보인다.
    """
    equipment: dict = {}
    lines: list = []
    agg: dict = {k: [] if k in PER_LINE_KEYS else 0.0 for k in EQUIP_KEYS}

    for csv_part, ko_part in CSV_PARTS:
        tier = _num(row.get(f"{csv_part}_티어"), 0)
        if tier >= CORP_TIER_CSV:
            equipment[ko_part] = {"level": _num(row.get(f"{csv_part}_레벨"), 0)}
        elif tier >= 1:
            equipment[ko_part] = {"tier": f"T{tier}"}
        else:
            equipment[ko_part] = {"tier": NO_ITEM}

        part_lines: list = []
        for i in (1, 2, 3):
            abbr = (row.get(f"{csv_part}_옵{i}") or "").strip()
            val = _fnum(row.get(f"{csv_part}_옵{i}값"))
            if not abbr or val is None:
                part_lines.append(None)
                continue
            key = opt_key(abbr)
            if key is None:
                warn.append(f"{name} {ko_part} {i}번째 줄: 모르는 옵션 '{abbr}' — 건너뜁니다")
                part_lines.append(None)
                continue
            lv, exact = _snap_option(key, val, skill_table)
            if lv is None:
                # **수치만 반영하지 않는다.** 단계 없는 줄을 합산에만 넣으면 그 줄은
                # UI에서 편집도 못 하는데 합계에는 살아 있어, 사용자가 아무 줄이나
                # 손대는 순간 `deriveEquipSkills`가 _ol만 보고 다시 더해 값이 어긋난다
                # (같은 수치가 두 번 들어가 29.26%가 되는 식). 줄째로 버리고 알린다.
                warn.append(f"{name} {ko_part} {i}번째 줄: {abbr} {val}%가 단계 표에 "
                            f"없습니다 — 이 줄은 계산에서 뺍니다")
                part_lines.append(None)
                continue
            part_lines.append({"o": key, "l": lv})
            if key in PER_LINE_KEYS:
                agg[key].append(round(exact, 4))
            else:
                agg[key] = round(agg[key] + exact, 4)
        lines.append(part_lines)

    for k in PER_LINE_KEYS:
        agg[k].sort(reverse=True)
    _check_against_agg(row, agg, warn, name)
    return equipment, agg, lines


def _check_against_agg(row: dict, agg: dict, warn: list, name: str) -> None:
    """부위칸에서 읽은 합과 CSV의 합계칸을 대조해 **어긋나면 알린다.**

    고치지는 않는다 — 합계칸에는 단계도 부위도 없어서 그대로 쓰면 UI에서 편집이
    안 되고, 장탄·차속은 줄별 목록이라 합계로 복원할 수도 없다. 대신 «무엇이 얼마나
    빠졌는지»를 말해 주면 유저가 레츠도로에서 다시 내보내거나 손으로 채울 수 있다.
    """
    for col, key in CSV_AGG.items():
        want = _fnum(row.get(col))
        if want is None or want <= 0:
            continue
        got = agg.get(key)
        have = round(sum(got), 4) if isinstance(got, list) else round(got or 0.0, 4)
        if abs(have - want) <= AGG_TOL:
            continue
        label = col.replace("(%)", "")
        if have == 0:
            warn.append(f"{name}: {label} {want}%가 CSV 합계칸에는 있는데 부위별 옵션칸이 "
                        f"비어 있습니다 — 계산에서 빠집니다. 레츠도로에서 다시 내보내 "
                        f"보시고, 그래도 비면 카드 톱니에서 손으로 넣으세요.")
        else:
            warn.append(f"{name}: {label} 합계칸 {want}% vs 부위칸 합 {have}% — "
                        f"차이 {round(want - have, 2)}%p만큼 계산에서 빠집니다.")


def build_profile_from_csv(text: str, maps: dict, name: str,
                           old: dict | None = None, fetched_at: str | None = None):
    """레츠도로 CSV 원문 → (육성 프로필, 알림 목록). `build_profile`과 같은 반환 형태."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise ValueError("CSV에 행이 없습니다.")
    missing = [c for c in REQUIRED_COLS if c not in rows[0]]
    if missing:
        raise ValueError(
            "레츠도로 CSV가 아닌 것 같습니다 — 필요한 열이 없습니다: "
            + ", ".join(missing))

    skill_table = maps["skill_table"]
    roster = maps["weapons"]        # {우리 캐릭명: 무기군} — 이름 검증용으로 쓴다
    notices: list = []
    warn: list = []
    entries: dict = {}
    unknown_names: list = []

    fav_chars = set(maps.get("fav_chars") or ())
    for row in rows:
        cname = (row.get("이름") or "").strip()
        if not cname:
            continue
        if cname not in roster:
            unknown_names.append(cname)
            continue
        equipment, agg, lines = _equipment_and_lines(row, skill_table, warn, cname)
        stage, fav_stage = _collection(row.get("소장품"))
        entries[cname] = {
            "breakthrough": max(0, min(3, _num(row.get("돌파"), 0))),
            "core_enhancement": max(0, min(7, _num(row.get("코강"), 0))),
            "skill_levels": {
                "1": max(1, min(10, _num(row.get("스킬1"), 1))),
                "2": max(1, min(10, _num(row.get("스킬2"), 1))),
                "3": max(1, min(10, _num(row.get("버스트스킬"), 1))),
            },
            "equipment": equipment,
            "equip_skills": agg,
            "collection_stage": stage,
            "_ol": lines,
            # UI 전용 — 인게임 전투력. 목록 정렬 기본값이라 API 경로와 같은 키로 담는다.
            "_combat": _num(row.get("전투력"), 0),
        }
        # 애장품이 **있는 캐릭터에만** 단계를 담는다. 없는 캐릭터에 0을 넣으면
        # API 경로(`profile_convert._to_profile`)와 프로필 모양이 달라진다.
        if fav_stage is not None and cname in fav_chars:
            entries[cname]["favorite_stage"] = fav_stage

    if not entries:
        raise ValueError("CSV의 이름을 로스터에서 하나도 못 찾았습니다 — 형식을 확인하세요.")

    notices.append({"level": "info",
                    "text": f"레츠도로 CSV에서 니케 {len(entries)}종을 읽었습니다."})
    if unknown_names:
        notices.append({"level": "warn",
                        "text": f"로스터에 없는 이름 {len(unknown_names)}개는 건너뜁니다.",
                        "names": unknown_names[:40]})
    notices.append({"level": "warn", "text":
                    "CSV에는 호감도와 큐브가 없습니다 — 둘은 기본 스펙 값"
                    "(호감도 30 · 렐릭 베어 큐브 Lv.15)으로 계산합니다. "
                    "실제와 다르면 카드의 톱니 버튼으로 고치세요."})
    for w in warn[:40]:
        notices.append({"level": "warn", "text": w})

    profile = assemble(entries, name, source="letsdoro CSV", openid=None, area=None,
                       console=None, synchro_level=None, cubes={},
                       console_warnings=[], old=old, fetched_at=fetched_at)
    return profile, notices
