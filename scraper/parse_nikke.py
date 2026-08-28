#!/usr/bin/env python3
"""
parse_nikke.py
nikke_scraped.json → data/parsed_nikke.json

캐릭터별 속성/클래스/기업/버스트단계 + 무기상세 파싱.

Run: python scraper/parse_nikke.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC  = ROOT / "scraper" / "nikke_scraped.json"
PREVIEW = ROOT / "scraper" / "preview_skills.json"   # 출시 전 카드 전사본(수동)
OUT  = ROOT / "data" / "parsed_nikke.json"

# 인게임 실측을 마친 `reload_bullet` 값. 10000 = 통짜 재장전, 3300 = 클립 3회,
# 5000 = 클립 2회(그레이브 — 유저 확인 2026-08-28).
# (docs/GAMEPLAY.md §무기 메카닉 · 3300은 유저 확인 2026-08-19)
_VERIFIED_REFILL = {10000, 5000, 3300}


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


def parse_fire_mechanics(weapon: dict, name: str = "") -> dict:
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

    # 클립 무기 판정. CDN `reload_bullet`은 재장전 1회가 채우는 비율(1/100%)이라
    # 10000 = 탄창 전체, 3300 = 1/3이다. 탄창을 채우는 데 필요한 클립 수로 접어 내린다
    # (3300 → 3). 종전에는 weapon_mechanics.json에 캐릭터 이름을 손으로 적어 관리했는데,
    # 전수 대조에서 `3300`인 14명이 그 목록과 정확히 일치해 CDN 쪽을 정본으로 세웠다.
    refill = weapon.get("재장전 채움(1/100%)")
    if refill:
        clips = max(1, round(10000 / refill))
        if refill in _VERIFIED_REFILL:
            result["clip_count"] = clips
        else:
            # 10000·3300 말고 다른 값이 나왔다. 클립 수를 그대로 믿으면 재장전 실효
            # 시간이 그 배수만큼 통째로 달라지므로 **실측 전까지는 키를 만들지 않는다** —
            # 종전 동작(통짜 재장전)이 유지되고, 소리만 낸다.
            print(f"  [WARN] 실측 안 된 재장전 채움값 {refill} ({name}) — 클립 {clips}회로 "
                  f"보이지만 반영하지 않았다. docs/DATA_VERIFY.md 참조", file=sys.stderr)

    # 값이 없으면 키를 만들지 않는다. 여기서 1로 채우면 그 1이 3계층 해석의 ②층에
    # 실값으로 앉아 ③층(weapon_mechanics 무기군 기본값, 예: SG 펠릿 10)을 덮어버린다
    # — 정보가 없을 때 가야 할 곳은 무기군 기본값이지 1이 아니다.
    # CDN 수집분은 전원 펠릿·총구가 있으므로 이 분기는 프리뷰(출시 전) 캐릭터에만 걸린다.
    if weapon.get("펠릿"):
        result["pellets"] = int(weapon["펠릿"])
    if weapon.get("총구"):
        result["muzzles"] = int(weapon["총구"])

    # 발사 입력 방식. 딜레이·엄폐·톡톡이 가부를 여기서 유도한다 (timeline.py CharState).
    # `조작 입력`이 없는 프리뷰 캐릭터는 키 자체를 만들지 않아 종전 무기군 기본값으로 떨어진다.
    if weapon.get("조작 입력"):
        result["input_type"] = weapon["조작 입력"]
        # 사격 자세 유지 시간(초). CDN은 1/100초 단위다(reload_time·charge_time과 같은 규약).
        # **0도 유효값**이라 `if`로 거르지 않는다 — 0이 곧 "발사 후 엄폐 자세로 돌아간다"다.
        result["fire_stance_hold"] = weapon.get("사격자세유지(cs)", 0) / 100
        # 풀차지 전용(= 끊어쏘기 불가). 두 갈래를 하나로 봉한다:
        #   DOWN_Charge      — 차지가 차면 자동 발사. 애초에 끊을 지점이 없다
        #   uptype_fire_timing≠0 — UP 타입 중 홍련 : 흑영·레이븐·A2 3명 (유저 확인)
        # `uptype_fire_timing`의 숫자 의미는 미해석이라 비영 여부만 쓰고 값은 흘리지 않는다.
        result["full_charge_only"] = bool(
            weapon["조작 입력"] == "DOWN_Charge" or weapon.get("UP발사타이밍", 0)
        )

    # 히트당 버스트 게이지(%). CDN은 1/10000 % 단위다.
    # **`(발당)`이 아니라 `(대상)`을 내린다.** 이름만 보면 반대로 고르기 쉬운데, 유저
    # 인게임 실측이 전부 2배 쪽이다 — 크라운(MG) 1000발·목단(AR) 200발·루주(SR) 카메라
    # 없이 18발이 `(대상)/10000`으로만 맞는다. 전수 199명에서 `(대상)`이 정확히
    # `(발당)`의 2배라, 대보스 배수를 미리 곱해 둔 필드로 읽는다
    # (docs/mechanics/버스트 게이지.md).
    # 풀차지 배율은 여기서 새 필드를 만들지 않는다 — `버스트게이지(풀차지)/100`이
    # `full_charge_mult`와 78/78 일치하므로 그 값을 그대로 쓴다(검산은 run()에서).
    # **0도 유효값**이라 `if weapon.get(...)`으로 거르지 않는다. 대신 키 자체가 없는
    # 프리뷰 캐릭터는 키를 안 만들어 ③층(무기군 기본값)으로 떨어뜨린다.
    if "버스트게이지(대상)" in weapon:
        result["burst_energy"] = weapon["버스트게이지(대상)"] / 10000

    # 탄착군(px). 명중 0% 기준 직경과, 지속 사격으로 수렴하는 값·발당 변화량.
    # 계산기가 `_current_spread()`에서 명중률과 예열 진행도를 얹어 쓴다.
    # `탄착군 변화속도`(px/s)는 **내리지 않는다** — 예열을 발수 선형으로 잡아 안 쓴다.
    # 원값은 nikke_scraped.json에 남는다 (docs/mechanics/CDN 발사 데이터.md).
    if weapon.get("탄착군 시작"):
        result["spread_start"] = weapon["탄착군 시작"]
        result["spread_end"] = weapon.get("탄착군 끝", weapon["탄착군 시작"])
        result["spread_change_pershot"] = weapon.get("탄착군 변화(발당)", 0)
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


def run(skills_data: dict | None = None) -> None:
    """nikke_scraped.json 파싱 실행. skills_data를 넘기면 파일 재로드 없이 사용."""
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

        # 풀차지는 대미지 배율과 **같은 배율로** 버스트 게이지도 준다 —
        # `버스트게이지(풀차지)/100 == full_charge_mult`가 78/78 전수 일치한다.
        # 그래서 게이지용 필드를 따로 내리지 않고 full_charge_mult를 재사용하는데,
        # 게임이 언젠가 둘을 갈라놓으면 조용히 틀리게 된다. 그때 여기서 걸린다.
        fc_gauge = weapon.get("버스트게이지(풀차지)", 0)
        fc_dmg = skill_fields.get("full_charge_mult")
        if fc_dmg is not None and abs(fc_gauge / 100 - fc_dmg) > 1e-9:
            print(f"  [WARN] 풀차지 게이지 배율이 대미지 배율과 다르다: {name} "
                  f"게이지 {fc_gauge / 100} vs 대미지 {fc_dmg} "
                  f"— timeline.py가 full_charge_mult를 게이지에도 쓴다", file=sys.stderr)
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

        # 등급은 기본 스탯을 가른다 — SR 라피의 레벨1 공격력은 SSR 화력형의 600이 아니라
        # 540이다. base_stat.py가 `등급_클래스_무기유형`으로 level_stats.json을 조회한다.
        # 출시 전 프리뷰 카드에는 등급 표기가 없어 SSR로 둔다(신규 SSR이 아닌 적이 없다).
        rarity = char.get("레어도") or "SSR"

        entry = {
            "rarity":        rarity,
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
            **parse_fire_mechanics(weapon, name),
            **skill_fields,
            **parse_favorite(char),
        }
        if name in preview_only:
            entry["preview"] = True   # 출시 전 카드 기준. runner/spec.py가 레벨 10 외 실행을 막는다
        parsed[name] = entry

    _dummy_base = {
        "rarity": "SSR",
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

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    print(f"[parse_nikke] {len(parsed)}명 (더미 B1/B2/B3 포함) → {OUT}")
    if warn_count:
        print(f"[parse_nikke] 경고: {warn_count}건 파싱 실패")


if __name__ == "__main__":
    run()
