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
OUT  = ROOT / "data" / "parsed_nikke.json"


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

    result["pellets"] = int(weapon.get("펠릿") or 1)
    result["muzzles"] = int(weapon.get("총구") or 1)
    return result


def run(skills_data: dict | None = None) -> None:
    """nikke_scraped.json 파싱 실행. skills_data를 넘기면 파일 재로드 없이 사용."""
    if skills_data is None:
        with open(SRC, encoding="utf-8") as f:
            skills_data = json.load(f)

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

        entry = {
            "element_code":  char.get("속성", ""),
            "class":         char.get("클래스", ""),
            "manufacturer":  char.get("기업", ""),
            "burst_stage":   char.get("버스트 단계", ""),
            "burst_cooldown": burst_cooldown,
            "weapon_type":   weapon_type,
            "max_ammo":      max_ammo,
            "reload_time":   reload_time,
            **parse_fire_mechanics(weapon),
            **skill_fields,
        }
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

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    print(f"[parse_nikke] {len(parsed)}명 (더미 B1/B2/B3 포함) → {OUT}")
    if warn_count:
        print(f"[parse_nikke] 경고: {warn_count}건 파싱 실패")


if __name__ == "__main__":
    run()
