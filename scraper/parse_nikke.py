#!/usr/bin/env python3
"""
parse_nikke.py
nikke_scraped.json → scraper/parsed_nikke.generated.json (전량 산출물, 커밋하지 않음)
                   → data/parsed_nikke.json (정본 — **필요한 항목만 병합**)

캐릭터별 속성/클래스/기업/버스트단계 + 무기상세 파싱.

정본에는 이 파서가 만들지 않는 손수 관리 키(`burst_energy`·`rare`·`clip_fill` …)가 있다.
그래서 정본을 통째로 덮어쓰지 않고 `merge_into`로 항목·키 단위 병합만 한다.

Run: python scraper/parse_nikke.py              # 전원 병합(파서 로직이 바뀌었을 때)
     python scraper/parse_nikke.py --check      # 정본에 무엇이 바뀔지 보기만
     python scraper/parse_nikke.py --names 라피,네온   # 이 캐릭터들만 병합
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC  = ROOT / "scraper" / "nikke_scraped.json"
PREVIEW = ROOT / "scraper" / "preview_skills.json"   # 출시 전 카드 전사본(수동)
OUT  = ROOT / "data" / "parsed_nikke.json"
GENERATED = ROOT / "scraper" / "parsed_nikke.generated.json"   # 전량 산출물 (.gitignore)

# 이 파서가 만드는 키 전부. 병합 때 정본에서 갈아 끼우는 대상이며, 새 산출물에 없으면 정본에서도 지운다
# (`preview`는 출시되는 순간 사라져야 한다: char-add PREVIEW.md 단계 R Step 6 · `charge_time`은 무기가
# 바뀌면 없어진다). 여기 없는 키(`burst_energy`·`rare`·`clip_fill` …)는 손수 관리 키로 보고 건드리지 않는다.
# 파서에 키를 새로 만들면 여기에도 적는다 — 실행분에서 나온 키는 자동으로 합쳐지지만, 어떤 캐릭터도
# 안 만드는 회차에는 목록만이 그 키를 파서 몫으로 알아본다.
PARSER_KEYS = (
    "element_code", "class", "manufacturer", "squad", "squad_name",
    "burst_stage", "burst_cooldown", "weapon_type", "max_ammo", "reload_time",
    "damage_coeff", "core_dmg_mult", "charge_time", "full_charge_mult",
    "fire_rate", "fire_rate_max", "fire_rate_change_pershot", "pellets", "muzzles",
    "reload_start_delay", "post_reload_delay",
    "favorite_item", "favorite_slots",
    "preview",
)


def merge_into(out_path: Path, parsed: dict, only: list[str] | None = None, check: bool = False) -> list[str]:
    """산출물 `parsed`를 정본 `out_path`에 **항목 단위로** 병합한다. 바뀐 이름 목록을 돌려준다.

    - `only`에 든 이름만(없으면 산출물 전부) 건드린다 — 수집기는 신규·변경 캐릭터만 넘긴다.
    - 캐릭터 안에서는 파서가 만드는 키만 갈아 끼우고, 그 밖의 키(손수 관리)는 그대로 둔다.
    - 파서가 더는 만들지 않는 키(출시로 사라진 `preview`, 무기가 바뀌어 없어진 `charge_time` 등)는 지운다.
    - 정본에만 있는 캐릭터는 지우지 않는다(삭제는 사람이 한다).
    - 파일 형식(들여쓰기 칸수·CRLF·끝 개행·키 순서)을 그대로 보존한다.
    - `check=True`면 바뀔 항목만 출력하고 쓰지 않는다.
    """
    if not out_path.exists():
        text = json.dumps(parsed, ensure_ascii=False, indent=1)
        if not check:
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        print(f"[parse_nikke] {out_path.name} 없음 - 전량 신규 {len(parsed)}명")
        return list(parsed)

    with open(out_path, encoding="utf-8", newline="") as f:
        raw = f.read()
    crlf = "\r\n" in raw
    lines = raw.split("\n")
    indent = (len(lines[1]) - len(lines[1].lstrip())) if len(lines) > 1 else 1
    trailing_nl = raw.endswith("\n")
    existing = json.loads(raw)

    gen_keys: set[str] = set(PARSER_KEYS)
    for entry in parsed.values():
        gen_keys |= set(entry)

    names = [n for n in (only if only is not None else parsed) if n in parsed]
    changed: list[tuple[str, list[str]]] = []
    for name in names:
        new = parsed[name]
        if name not in existing:
            existing[name] = dict(new)
            changed.append((name, ["신규"]))
            continue
        cur = existing[name]
        fields: list[str] = []
        for k in list(cur):
            if k in gen_keys and k not in new:
                del cur[k]
                fields.append("-" + k)
        for k, v in new.items():
            if k not in cur:
                fields.append("+" + k)
            elif cur[k] != v:
                fields.append(k)
            cur[k] = v            # 있던 키는 자리 그대로, 새 키는 뒤에 붙는다
        if fields:
            changed.append((name, fields))

    for name, fields in changed:
        print(f"  {name}: {', '.join(fields)}")
    if not changed:
        print(f"[parse_nikke] {out_path.name} 변화 없음 ({len(names)}명 대조)")
        return []
    if check:
        print(f"[parse_nikke] --check: {len(changed)}명 바뀔 것, 쓰지 않았다")
        return [n for n, _ in changed]

    text = json.dumps(existing, ensure_ascii=False, indent=indent)
    if crlf:
        text = text.replace("\n", "\r\n")
    if trailing_nl:
        text += "\r\n" if crlf else "\n"
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"[parse_nikke] {out_path.name}: {len(changed)}명 병합 (손수 관리 키 보존)")
    return [n for n, _ in changed]


def load_preview() -> dict:
    """preview_skills.json의 캐릭터 항목. 없으면 빈 dict.

    스키마가 nikke_scraped.json과 같으므로 그대로 같은 파서에 태운다.
    `_`로 시작하는 키(`_comment`)는 주석이라 제외한다.
    """
    if not PREVIEW.exists():
        return {}
    with open(PREVIEW, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def parse_weapon_skill(text: str, is_charge: bool) -> dict:
    result = {}

    m = re.search(r'\[공격력 ([\d.]+)% 대미지\]', text)
    if m:
        result["damage_coeff"] = float(m.group(1))
    else:
        print(f"  [WARN] damage_coeff 파싱 실패: {text!r}", file=sys.stderr)

    m = re.search(r'\[코어 대미지 ([\d.]+)%\]', text)
    if m:
        result["core_dmg_mult"] = float(m.group(1))
    else:
        print(f"  [WARN] core_dmg_mult 파싱 실패: {text!r}", file=sys.stderr)

    if is_charge:
        m = re.search(r'차지 시간:\s*([\d.]+)초', text)
        if m:
            result["charge_time"] = float(m.group(1))
        else:
            print(f"  [WARN] charge_time 파싱 실패: {text!r}", file=sys.stderr)

        m = re.search(r'풀 차지 대미지:\s*([\d.]+)% 대미지', text)
        if m:
            result["full_charge_mult"] = float(m.group(1))
        else:
            print(f"  [WARN] full_charge_mult 파싱 실패: {text!r}", file=sys.stderr)

    return result


def parse_fire_mechanics(weapon: dict) -> dict:
    """무기상세의 CDN 원값 → 발사 메카닉 필드.

    `연사(rpm)`은 분당 발수다(AR 720 → 12/s, SG 90 → 1.5/s로 기존 값과 일치).
    `연사최대`·`연사증가`는 예열이 있는 MG에서만 시작값과 달라지므로 그때만 기록한다.
    """
    result = {}

    rpm = weapon.get("연사(rpm)") or 0
    if rpm:
        result["fire_rate"] = round(rpm / 60, 4)

        rpm_max = weapon.get("연사최대(rpm)") or 0
        rpm_step = weapon.get("연사증가(rpm/발)") or 0
        if rpm_max and rpm_max != rpm and rpm_step:
            result["fire_rate_max"] = round(rpm_max / 60, 4)
            result["fire_rate_change_pershot"] = round(rpm_step / 60, 4)

    # 값이 없으면 키를 만들지 않는다. 여기서 1로 채우면 그 1이 3계층 해석의 ②층에
    # 실값으로 앉아 ③층(weapon_mechanics 무기군 기본값, 예: SG 펠릿 10)을 덮어버린다
    # — 정보가 없을 때 가야 할 곳은 무기군 기본값이지 1이 아니다.
    # CDN 수집분은 전원 펠릿·총구가 있으므로 이 분기는 프리뷰(출시 전) 캐릭터에만 걸린다.
    if weapon.get("펠릿"):
        result["pellets"] = int(weapon["펠릿"])
    if weapon.get("총구"):
        result["muzzles"] = int(weapon["총구"])

    # spot_*_delay는 CDN에서 1/100초 단위다. 값이 없는 프리뷰 캐릭터는 키를
    # 만들지 않아 timeline의 무기군 폴백으로 내려가게 한다.
    for source, target in (
        ("spot_last_delay", "reload_start_delay"),
        ("spot_first_delay", "post_reload_delay"),
    ):
        if weapon.get(source) is not None:
            result[target] = round(float(weapon[source]) / 100, 4)
    return result


def parse_favorite(char: dict) -> dict:
    """애장품 보유 캐릭터의 단계↔교체슬롯 매핑.

    `favorite_slots[i]` = **애장품 (i+1)단계가 교체하는 스킬 슬롯 번호**다. 교체 순서는
    캐릭터마다 다르다(드레이크 1·2·3, 미란다 3·2·1). 계산기가 단계별로 어느 슬롯을
    애장품 판본으로 갈아끼울지 정하는 유일한 근거이므로 스크랩 원문에서 그대로 옮긴다.
    애장품이 없으면 빈 dict — 키 자체가 "애장품 보유" 판정이다.
    """
    fav = char.get("애장품")
    if not fav:
        return {}
    slots = [int(st["교체슬롯"]) for st in fav.get("단계별", [])]
    if sorted(slots) != [1, 2, 3]:
        print(f"  [WARN] 애장품 교체슬롯이 1·2·3 한 번씩이 아니다: {slots}", file=sys.stderr)
        return {}
    return {"favorite_item": fav.get("아이템명", ""), "favorite_slots": slots}


def run(skills_data: dict | None = None, only: list[str] | None = None, check: bool = False) -> list[str]:
    """nikke_scraped.json 파싱 실행. skills_data를 넘기면 파일 재로드 없이 사용.

    전량 산출물은 `GENERATED`에 쓰고, 정본 `OUT`에는 `only`(없으면 전원)만 `merge_into`로 병합한다.
    정본에서 바뀐 캐릭터 이름 목록을 돌려준다.
    """
    if skills_data is None:
        with open(SRC, encoding="utf-8") as f:
            skills_data = json.load(f)

    # 프리뷰(출시 전 카드 전사본)를 같이 태운다. 같은 이름이 양쪽에 있으면 **스크랩이 이긴다** —
    # 출시되는 순간 정본으로 자동 전환된다(프리뷰 항목 제거는 char-add 단계 R의 몫).
    preview = load_preview()
    preview_only = {k: v for k, v in preview.items() if k not in skills_data}
    if preview_only:
        print(f"[parse_nikke] 프리뷰 {len(preview_only)}명 포함: {', '.join(preview_only)}")
    skills_data = {**preview_only, **skills_data}

    parsed: dict = {}
    warn_count = 0

    for name, char in skills_data.items():
        weapon = char.get("무기상세", {})
        weapon_type = weapon.get("무기유형", "")
        is_charge = weapon.get("조작 타입") == "차지형"
        weapon_skill_text = weapon.get("무기스킬", "")

        max_ammo_raw = weapon.get("최대 장탄 수", "0")
        reload_raw   = weapon.get("재장전 시간", "0s")

        try:
            max_ammo = int(max_ammo_raw)
        except ValueError:
            max_ammo = 0

        try:
            reload_time = float(re.sub(r'[^\d.]', '', reload_raw))
        except ValueError:
            reload_time = 0.0

        skill_fields = parse_weapon_skill(weapon_skill_text, is_charge)
        if any(k not in skill_fields for k in ("damage_coeff", "core_dmg_mult")):
            warn_count += 1

        skills = char.get("스킬", {})
        skill3 = list(skills.values())[2] if len(skills) >= 3 else {}
        burst_cool_raw = skill3.get("쿨타임")
        try:
            burst_cooldown = float(str(burst_cool_raw).replace("s", "").strip())
        except (ValueError, TypeError):
            burst_cooldown = 40.0
            print(f"  [WARN] burst_cooldown 파싱 실패: {name} {burst_cool_raw!r}", file=sys.stderr)

        # 스쿼드는 코드가 정본. 표시명이 없는 스쿼드(`-`)는 코드로 대체한다.
        squad = char.get("스쿼드", "")
        squad_name = char.get("스쿼드명", "")
        if squad_name in ("", "-"):
            squad_name = squad

        entry = {
            "element_code":  char.get("속성", ""),
            "class":         char.get("클래스", ""),
            "manufacturer":  char.get("기업", ""),
            "squad":         squad,
            "squad_name":    squad_name,
            "burst_stage":   char.get("버스트 단계", ""),
            "burst_cooldown": burst_cooldown,
            "weapon_type":   weapon_type,
            "max_ammo":      max_ammo,
            "reload_time":   reload_time,
            **parse_fire_mechanics(weapon),
            **skill_fields,
            **parse_favorite(char),
        }
        if name in preview_only:
            entry["preview"] = True   # 출시 전 카드 기준. context/spec.py가 레벨 10 외 실행을 막는다
        parsed[name] = entry

    _dummy_base = {
        "element_code": "철갑",
        "class": "화력형",
        "manufacturer": "어브노말",
        "weapon_type": "AR",
        "max_ammo": 60,
        "reload_time": 1.0,
        "damage_coeff": 13.65,
        "core_dmg_mult": 200.0,
    }
    parsed["test_B1"] = {**_dummy_base, "burst_stage": "1", "burst_cooldown": 20.0}
    parsed["test_B2"] = {**_dummy_base, "burst_stage": "2", "burst_cooldown": 20.0}
    parsed["test_B3"] = {**_dummy_base, "burst_stage": "3", "burst_cooldown": 40.0}

    with open(GENERATED, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    print(f"[parse_nikke] {len(parsed)}명 (더미 B1/B2/B3 포함) → {GENERATED.name} (전량 산출물)")
    if warn_count:
        print(f"[parse_nikke] 경고: {warn_count}건 파싱 실패")
    return merge_into(OUT, parsed, only=only, check=check)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="nikke_scraped.json → parsed_nikke.json (필요한 항목만 병합)")
    ap.add_argument("--names", help="쉼표 구분 캐릭터 이름 — 이들만 정본에 병합 (없으면 전원)")
    ap.add_argument("--check", action="store_true", help="정본을 쓰지 않고 바뀔 항목만 출력")
    args = ap.parse_args()
    run(only=[n.strip() for n in args.names.split(",") if n.strip()] if args.names else None, check=args.check)
