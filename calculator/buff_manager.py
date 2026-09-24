"""
Phase 3-C: 버프 관리자

설계:
  - 효과 등록(parsed_skills, 장비, 큐브, 소장품) → 통일된 effect 포맷
  - notify(event, t, caster) → timing 매칭 시 ActiveBuff 생성/갱신
  - tick(t) → 만료 버프 제거, every:Ns 스킬 쿨타임 추적
  - get_buffs(caster, target, t) → condition 재평가 후 buffs 딕셔너리 반환

버프 합산 규칙:
  - 대부분 stat: 단순 합산
  - crit_rate: 기본 15% + 버프 합연산, 100% 상한
  - crit_rate_skill: 같은 합이되 `normal_atk_crit_rate`(일반 공격 한정) 기여만 뺀 값.
    스킬 딜 히트의 크리 판정은 이쪽을 쓴다 (damage.calc_avg_damage)
  - crit_dmg / crit_dmg_skill: 위와 같은 쌍. 뒤쪽은 `normal_atk_crit_dmg` 기여를 뺀 값
"""

from __future__ import annotations

import itertools
import json
import math
import os
import random
from dataclasses import dataclass, field
from typing import Any

from calculator.base_stat import NO_ITEM

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_TABLE_DIR = os.path.join(_DATA_DIR, "base_stat_tables")

# hp_pct 100% 도달 판정 허용 오차 (부동소수점 나눗셈 오차 흡수)
_HP_EPS = 1e-6


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _get_skill_lv(char: dict, eff: dict) -> str:
    """eff의 source(스킬1/2/3)에 맞는 스킬 레벨 반환. skill_levels 없으면 skill_level fallback."""
    levels = char.get("skill_levels")
    if levels:
        src = eff.get("source", "")
        if src == "스킬1":
            return str(levels.get("1", 10))
        if src == "스킬2":
            return str(levels.get("2", 10))
        if src == "스킬3":
            return str(levels.get("3", 10))
    return str(char.get("skill_level", 10))


_NIKKE = _load(os.path.join(_DATA_DIR, "parsed_nikke.json"))
_PARSED_SKILLS = _load(os.path.join(_DATA_DIR, "parsed_skills.json"))
_BURST_GAUGE   = _load(os.path.join(_DATA_DIR, "burst_gauge.json"))


def _type_optimal_ranges() -> dict[str, tuple[float, float]]:
    """무기군별 대표 적정거리 [최소, 최대] — 로스터에서 가장 흔한 값(하란 같은 캐릭터 예외가 표를 흔들지 않게)."""
    counts: dict[str, dict[tuple, int]] = {}
    for v in _NIKKE.values():
        if isinstance(v, dict) and v.get("weapon_type") and v.get("optimal_range"):
            per = counts.setdefault(v["weapon_type"], {})
            key = tuple(v["optimal_range"])
            per[key] = per.get(key, 0) + 1
    return {w: max(c, key=lambda k: (c[k], k)) for w, c in counts.items()}


_TYPE_OPTIMAL_RANGE = _type_optimal_ranges()


def optimal_range_of(name: str, weapon_type: str) -> tuple[float, float]:
    """이 니케가 지금 무기로 쏠 때의 기본 적정거리 [최소, 최대] — CDN `bonusrange_*`(`parsed_nikke` `optimal_range`).

    자기 무기군이면 캐릭터 값을 쓴다(하란은 SR인데 25~45). 무기 변경 모드로 무기군이 바뀐 사격은 그 무기군의
    대표값이다. RL은 0~0 — 거리가 있으면 언제나 적정거리 밖이다.
    """
    own = _NIKKE.get(name, {})
    if own.get("weapon_type") == weapon_type and own.get("optimal_range"):
        lo, hi = own["optimal_range"]
    else:
        lo, hi = _TYPE_OPTIMAL_RANGE.get(weapon_type, (0, 0))
    return float(lo), float(hi)


def in_optimal_range(enemy: dict, name: str, weapon_type: str, buffs: dict) -> bool:
    """적정거리 판정의 정본 — 평타 ③ +30%(timeline `_fire`·`_charge_fire`)·스킬의 일반 공격 판정·조건
    `optimal_range`가 모두 이 함수를 본다.

    **보스 거리(`enemy["distance"]`)가 없으면 종전 무기군 목록**(`optimal_range_weapons`)이다 — 기본 경로가
    이쪽이라 회귀가 안 흔들린다. 거리가 있으면 니케의 적정 구간과 비교한다(유저 결정 2026-09-18):
      최대 = 기본 최대 × (1 + 적정 최대 사거리 ▲%) · 최소 = 기본 최소 × (1 − 적정 최소 사거리 ▲%) (0 하한)
    「적정 최소 사거리 ▲」는 구간을 **가까이까지 넓힌다** — 에이드 : 에이전트 바니 4.44% × 10중첩 + 55.56% = 100%면
    SR 45~100이 0~100이 된다. 닫힌 구간으로 본다(⬜ 인게임 미확인).
    """
    d = enemy.get("distance")
    if d is None:
        return weapon_type in (enemy.get("optimal_range_weapons") or [])
    lo, hi = optimal_range_of(name, weapon_type)
    hi *= 1.0 + buffs.get("optimal_range_max_pct", 0.0) / 100.0
    lo = max(0.0, lo * (1.0 - buffs.get("optimal_range_min_pct", 0.0) / 100.0))
    return lo - 1e-9 <= float(d) <= hi + 1e-9

# {캐릭터: {스킬명: {"burst_energy": 히트당 %}}} — 무기값과 다른 버충 계수를 갖는 스킬.
BURST_GAUGE_EXCEPTIONS: dict = _BURST_GAUGE.get("_exceptions", {})

FAVORITE_MAX_STAGE = 3          # 애장품 단계는 0(미보유)~3


def char_effects(name: str, favorite_stage: int | None = None) -> list[dict]:
    """캐릭터의 활성 스킬 효과 목록. 애장품 단계에 맞는 슬롯 조합을 고른다.

    애장품은 단계마다 스킬 슬롯 **하나**를 통째로 갈아끼운다. 어느 단계가 어느 슬롯을
    바꾸는지는 캐릭터마다 다르고(`parsed_nikke.json`의 `favorite_slots`), 그래서
    `parsed_skills.json`에는 한 캐릭터의 슬롯마다 판본이 둘 있다 —
    `favorite: N`이 붙은 항목은 **애장품 N단계 판본**, 안 붙은 항목은 **기본(비애장품) 판본**.

    단계 S에서는 1~S단계가 교체한 슬롯은 애장품 판본을, 나머지 슬롯은 기본 판본을 쓴다.
    애장품이 없는 캐릭터는 단계와 무관하게 파싱된 항목 전부를 그대로 쓴다.

    필요한 판본이 아직 파싱돼 있지 않으면 **끊는다** — 그대로 두면 그 슬롯의 스킬이
    통째로 빠진 채 조용히 낮은 딜이 나온다.
    """
    effs = _PARSED_SKILLS.get(name, [])
    slots: list[int] = _NIKKE.get(name, {}).get("favorite_slots") or []
    if not slots or not effs:
        return effs

    stage = FAVORITE_MAX_STAGE if favorite_stage is None else int(favorite_stage)
    if not 0 <= stage <= FAVORITE_MAX_STAGE:
        raise ValueError(
            f"[{name}] 애장품 단계는 0~{FAVORITE_MAX_STAGE}여야 한다 (favorite_stage={favorite_stage})"
        )

    # 슬롯 → 그 슬롯에 실제로 쓸 판본. 애장품 판본이면 그 단계, 기본 판본이면 None.
    want: dict[int, int | None] = {
        slot: (i + 1 if i + 1 <= stage else None) for i, slot in enumerate(slots)
    }
    out = [eff for eff in effs
           if eff.get("favorite") == want[int(eff["source"].removeprefix("스킬"))]]

    missing = sorted(slot for slot in want
                     if not any(eff["source"] == f"스킬{slot}"
                                and eff.get("favorite") == want[slot] for eff in effs))
    if missing:
        kind = {slot: ("애장품 %d단계" % want[slot]) if want[slot] else "기본(비애장품)"
                for slot in missing}
        raise ValueError(
            f"[{name}] 애장품 {stage}단계로 돌리려면 필요한 스킬 판본이 "
            f"data/parsed_skills.json에 없다: "
            + ", ".join(f"스킬{slot}({k})" for slot, k in kind.items()) + "\n"
            f"  이대로 두면 그 슬롯의 효과가 통째로 빠져 딜이 조용히 낮게 나온다.\n"
            f"  ① 그 판본을 파싱한다 — char-add 단계 2 (`.agent/skills/char-add/PARSE.md`)\n"
            f"  ② 파싱 전이라면 애장품 3단계(`favorite_stage: 3`)로만 돌린다"
        )
    return out
_EQUIP_SKILLS = _load(os.path.join(_TABLE_DIR, "equipment_skills.json"))
_CUBE = _load(os.path.join(_TABLE_DIR, "cube.json"))
_COLLECTION = _load(os.path.join(_TABLE_DIR, "collection.json"))

# 게임 원문이 모드 이름 대신 쓰는 총칭 상태명. `self_state:` 판정에서 "아무 무기 변경
# 모드든 켜져 있는가"로 읽는다 (`_has_self_state`, `docs/PARSING.md` §상태의 담체).
WEAPON_CHANGE_STATE = "무기 변경"

# ── 빈 buffs 딕셔너리 템플릿 ──────────────────────────────────────────────

_BUFFS_ZERO: dict[str, Any] = {
    "atk_pct":          0.0,
    "atk_flat":         0.0,
    "def_ignore_pct":   0.0,
    "crit_rate":        0.0,   # 아래 _CRIT_RATE_STATS 경로에서 별도 합산
    "crit_rate_skill":  0.0,   # 같은 경로. 일반 공격 한정 크리율을 뺀 값 (스킬 딜용)
    "crit_dmg":         0.0,   # 아래 _CRIT_DMG_STATS 경로에서 별도 합산
    "crit_dmg_skill":   0.0,   # 같은 경로. 일반 공격 한정 크리뎀을 뺀 값 (스킬 딜용)
    "core_dmg_pct":     0.0,
    "atk_dmg_pct":                  0.0,
    "burst_dmg_pct":                0.0,
    "burst_dmg_aoe_pct":            0.0,   # 대상이 '적 전체'인 버스트 대미지에만 가산
    "burst_dmg_single_pct":         0.0,   # 대상 설명이 '~ 적 1기에게'인 버스트 대미지에만 가산
    "pierce_dmg_pct":               0.0,
    "dot_dmg_pct":                  0.0,
    "armor_break_dmg_pct":          0.0,
    "projectile_explosion_dmg":     0.0,
    "projectile_attachment_dmg":    0.0,
    "sequential_dmg_pct":           0.0,
    "charge_dmg_pct":   0.0,
    "charge_dmg_mag_pct": 0.0,
    "split_dmg_pct":    0.0,
    "part_dmg_pct":     0.0,
    # 관통 범위·폭발 범위 ▲(%) — 대미지 식에는 안 들어간다. 보스 패턴 좌표 off에서 이 발이 파츠에
    # 얼마나 멀리 닿는가(`boss_pattern.hit_reach` — 합 100% 이상이면 한 단계 더)에만 쓴다
    "pierce_range":     0.0,
    "explosion_range":  0.0,
    # 적정 최대·최소 사거리 ▲(%) — 대미지 식에는 안 들어간다. 보스 거리가 있을 때 적정거리 판정
    # (`in_optimal_range`)이 니케의 적정 구간을 넓히는 데만 쓴다
    "optimal_range_max_pct": 0.0,
    "optimal_range_min_pct": 0.0,
    "received_dmg":     0.0,
    "element_bonus_pct": 0.0,
    "is_element_match": False,
    "def_pct":          0.0,
    "enemy_def_down_pct": 0.0,  # 적 방어력 감소(②). 적 대상 def_pct 버프 합(음수)
    "enemy_def_down_flat": 0.0,  # 적 방어력 **정액** 감소(②). 적 대상 def_caster_based_pct를
                                 # 시전자 기본 방어력 × N%로 환산한 합(음수). 비율판과 더하는 자리가 다르다
    "charge_speed_pct": 0.0,
    "charge_time_flat": 0.0,  # 차지 시간 절대 가감(초). 감소는 음수
    "charge_time_fixed": False,
    "persona_state": False,   # 페르소나 상태 마커. 수치 기여 없이 대상 판정에만 쓴다
    "charge_speed_buff_immune": False,
    "charge_speed_debuff_immune": False,
    "debuff_immune": False,
    "stun_immune": False,
    "stack_change_immune": False,
    "max_ammo_pct":     0.0,
    "max_ammo_flat":    0.0,
    "accuracy_pct":     0.0,
    "normal_atk_dmg_pct": 0.0,
    "reload_speed_pct": 0.0,
    # 재장전 **1회가 채우는 탄창 비율**에 곱해지는 %. 재장전 속도와 다른 축이다
    # (그쪽은 1회에 걸리는 시간). 원문 「재장전 비율 N% ▼」 — 그레이브 `방열`.
    "reload_ratio_pct": 0.0,
    "burst_cooldown":   0.0,  # 버스트 쿨타임 감소 (buff 상태로 지속)
    "max_hp_pct":       0.0,  # 최대 체력 + 현재 체력 동반 증가
    "max_hp_only_pct":  0.0,  # 최대 체력만 증가 (현재 체력 유지)
    "lifesteal_pct":    0.0,
    "def_caster_based_pct": 0.0,
    "taunt":            False,
    "pierce_enabled":   False,
    "infinite_ammo":    False,
    "armor_break_enabled": False,  # 일반 공격을 방어력 무시 대미지로 치환
    "attack_speed_pct": 0.0,
    "pellet_count":     0.0,
    "pellet_count_fixed": 0.0,  # >0이면 펠릿 수를 이 값으로 고정 (절대값)
    "fullburst_duration": 0.0,  # 풀버스트 타임 지속 시간 증감 (초)
    "skill_cooldown_pct": 0.0,  # 스킬 쿨타임 % 감소 (음수 = 감소)
    "charge_speed_overflow_conversion_pct": 0.0,  # charge_speed 100% 초과분 × N% → charge_dmg_pct 추가
    "mg_warmup_speed_pct": 0.0,  # MG 예열 진행 속도 % (음수 = 감소). -100이면 warmup_shots 증가 정지
    # 「버스트 충전 속도」는 수령자와 무관하게 시전자의 발당 기준 게이지 × 버프값을
    # 매 히트에 가산한다. 정본: docs/mechanics/버스트 게이지.md.
    "burst_charge_speed_flat": 0.0,  # 모든 시전자가 주는 히트당 게이지 가산량(%p)
}

# parsed_skills stat → buffs 딕셔너리 키 매핑
# 매핑에 없는 stat은 damage/instant type이거나 타임라인 처리 대상
_STAT_TO_BUFF: dict[str, str] = {
    "atk_pct":              "atk_pct",
    "def_ignore_pct":       "def_ignore_pct",
    "crit_rate":            "crit_rate",
    "normal_atk_crit_rate": "crit_rate",
    "crit_dmg":             "crit_dmg",
    "normal_atk_crit_dmg":  "crit_dmg",
    "core_dmg_pct":         "core_dmg_pct",
    "atk_dmg_pct":                  "atk_dmg_pct",
    "burst_dmg_pct":                "burst_dmg_pct",
    "burst_dmg_aoe_pct":            "burst_dmg_aoe_pct",
    "burst_dmg_single_pct":         "burst_dmg_single_pct",
    "pierce_dmg_pct":               "pierce_dmg_pct",
    "dot_dmg_pct":                  "dot_dmg_pct",
    "armor_break_dmg_pct":          "armor_break_dmg_pct",
    "projectile_explosion_dmg":     "projectile_explosion_dmg",
    "projectile_explosion_dmg_pct": "projectile_explosion_dmg",
    "projectile_attachment_dmg":    "projectile_attachment_dmg",
    "projectile_attachment_dmg_pct": "projectile_attachment_dmg",
    "sequential_dmg_pct":           "sequential_dmg_pct",
    "charge_dmg_pct":       "charge_dmg_pct",
    "charge_dmg_mag_pct":   "charge_dmg_mag_pct",  # 차지 대미지 배율 ▲ (④ 승수)
    "split_dmg_pct":        "split_dmg_pct",        # 분배 대미지 ▲ (⑥에 합산)
    "part_dmg_pct":         "part_dmg_pct",         # 파츠 대미지 ▲ (⑤ 선택 합산)
    "pierce_range":         "pierce_range",         # 관통 범위 ▲ — 파츠 다중 타격 도달 단계에만
    "explosion_range":      "explosion_range",      # 폭발 범위 ▲ — 같은 자리
    "optimal_range_max_pct": "optimal_range_max_pct",  # 적정 최대 사거리 ▲ — 적정거리 판정에만
    "optimal_range_min":    "optimal_range_min_pct",   # 적정 최소 사거리 ▲ — 최소 거리를 N% 줄인다(유저 결정 2026-09-18)
    "received_dmg_pct":     "received_dmg",
    "element_bonus_pct":    "element_bonus_pct",
    "element_bonus":        "element_bonus_pct",  # 장비·큐브에서 사용하는 stat명 (동일 버프 키로 합산)
    "def_pct":              "def_pct",
    "charge_speed_pct":     "charge_speed_pct",
    "charge_speed_caster_based_pct": "charge_speed_pct",  # _get_value에서 시전자 charge_time 기준 환산
    "charge_time_flat":     "charge_time_flat",
    "charge_time_fixed":    "charge_time_fixed",
    "persona_state":        "persona_state",
    "charge_speed_buff_immune":  "charge_speed_buff_immune",
    "charge_speed_debuff_immune": "charge_speed_debuff_immune",
    "debuff_immune":        "debuff_immune",
    "stun_immune":          "stun_immune",
    "stack_change_immune":  "stack_change_immune",
    "max_ammo_pct":         "max_ammo_pct",
    "max_ammo_flat":        "max_ammo_flat",
    "accuracy_pct":         "accuracy_pct",
    "normal_atk_dmg_pct":   "normal_atk_dmg_pct",
    "reload_speed_pct":     "reload_speed_pct",
    "reload_ratio_pct":     "reload_ratio_pct",
    "burst_cooldown":       "burst_cooldown",
    "max_hp_pct":           "max_hp_pct",
    "max_hp_only_pct":      "max_hp_only_pct",
    "lifesteal_pct":        "lifesteal_pct",
    "def_caster_based_pct": "def_caster_based_pct",
    "taunt":                "taunt",
    "pierce_enabled":       "pierce_enabled",
    "infinite_ammo":        "infinite_ammo",
    "armor_break_enabled":  "armor_break_enabled",
    "attack_speed_pct":     "attack_speed_pct",
    "pellet_count":         "pellet_count",
    "pellet_count_fixed":   "pellet_count_fixed",
    "fullburst_duration":   "fullburst_duration",
    "skill_cooldown_pct":   "skill_cooldown_pct",
    "charge_speed_overflow_conversion_pct": "charge_speed_overflow_conversion_pct",
    "mg_warmup_speed_pct": "mg_warmup_speed_pct",
    # 2024-12-05에 `버스트 게이지 획득량` → `버스트 게이지 충전 속도`로 **표기만** 바뀌었다.
    # 별개 메커니즘이 아니므로 stat도 하나다 (docs/REFERENCES.md).
    # `_route_burst_charge()`가 시전자의 CDN 발당 기준값으로 환산한다.
    "burst_charge_speed_pct": "burst_charge_speed_flat",
}

# 방어·생존 stat 중 `get_buffs` 합산이 아니라 **엔진이 활성 버프를 직접 읽는** 것. 대미지 식에
# 들어가지 않아 buffs 딕셔너리에 자리가 없다(보스 → 니케 피해 쪽). `scraper/cdn_tables.py`가
# `_STAT_TO_BUFF`와 함께 보고 큐브 효과의 지원 여부를 가른다.
_DIRECT_READ_STATS = frozenset([
    "cover_hp_pct",          # cover_max_hp()
    "heal_received_pct",     # heal_received_mult()
    "next_shield_hp_pct",    # take_next_shield_amp()
    "invincible", "undying", "stealth", "cover_disabled",   # has_live_stat()
    "shield_invincible",     # has_live_stat() — absorb_shield()가 보호막의 시전자를 본다
    "received_dmg_split_even",   # split_group() — 보스 공격 한 발을 집단이 나눠 진다
])

# 크리확률로 합산되는 stat 집합 (백분율 → 확률 환산 후 기본 15%와 합연산)
_CRIT_RATE_STATS = {"crit_rate", "normal_atk_crit_rate"}

# 그중 **일반 공격에만** 실리는 stat. 스킬 딜(버스트·추가 대미지 등)의 크리 판정에서는
# 빠져야 한다 — 그래서 get_buffs는 `crit_rate`(전체 합)와 `crit_rate_skill`(이 집합 제외)
# 두 값을 낸다. 원문 표기가 `[일반 공격 크리티컬 확률 n% ▲]`인 것들이다 (헬름 진두지휘 등).
_NORMAL_ATK_ONLY_CRIT_RATE_STATS = {"normal_atk_crit_rate"}

# 크리 대미지도 같은 구조다. 합연산 자체는 평범하지만, 일반 공격 한정분을 빼야 해서
# `_PLAN_CDMG` 전용 경로로 뽑아 `crit_dmg` / `crit_dmg_skill` 두 합을 동시에 만든다.
_CRIT_DMG_STATS = {"crit_dmg", "normal_atk_crit_dmg"}
_NORMAL_ATK_ONLY_CRIT_DMG_STATS = {"normal_atk_crit_dmg"}

# **소스별로 따로 반올림되는** buff_key. 인게임은 이 둘을 합산 후 한 번 반올림하지 않고,
# 소스(장비 옵션 단계·큐브·소장품·스킬 버프 하나) 각각을 기본값에 곱해 눈금
# (장탄 1발 / 차지 0.01초)에 맞춰 반올림한 뒤 그 결과를 더한다
# (유저 인게임 확인, 2026-08-19 — GAMEPLAY.md §무기 메카닉).
# 그래서 get_buffs는 합계 말고 **그룹별 기여 목록**(`buffs["_quant_parts"]`)도 함께 낸다.
# 실제 반올림은 기본값을 아는 쪽(timeline `_full_ammo`·`_effective_charge_time`)이 한다.
_QUANT_BUFF_KEYS = frozenset(["max_ammo_pct", "charge_speed_pct"])
_QUANT_PARTS_KEY = "_quant_parts"


def _quant_group_key(ab) -> tuple:
    """소스별 반올림의 **그룹 식별자**. 같은 그룹은 합산한 뒤 딱 한 번 반올림한다.

    장비 옵션은 **종류·레벨이 모두 같으면 부위가 달라도 한 그룹**이다(유저 확인) —
    그래서 장비 효과는 `_quant_group` 태그를 달고 오고 태그가 같으면 합쳐진다.
    스킬 버프는 효과 하나가 한 그룹이라(같은 버프가 여러 번 걸리면 합산 후 1회 반올림)
    효과 객체 id를 그대로 쓴다. 시전자를 함께 넣어 서로 다른 캐릭터가 건 같은 효과가
    한 그룹으로 섞이지 않게 한다.
    """
    eff = ab.effect
    return (ab.caster, eff.get("_quant_group") or id(eff))


# 차지 속도 증감 **효과 면역**이 걸러 내지 못하는 소스. 면역은 스킬 버프만 막고
# 오버로드(장비 옵션)·큐브는 그대로 걸린다 (유저 인게임 확인, 2026-09-02).
# 소스를 가리지 않고 전부 무시하는 것은 `charge_time_fixed`(차지 시간 고정) 쪽이다.
_CHARGE_IMMUNE_EXEMPT_SOURCES = frozenset(["equipment", "cube"])


def _quant_source(ab) -> str | None:
    """`_source_tag`. 스킬 버프는 태그가 없어 None이다."""
    return ab.effect.get("_source_tag")


def _equip_option_groups(stat: str, val) -> list[float]:
    """`equip_skills` 항목 하나 → **그룹별 합산 퍼센트** 목록. 그룹당 효과 하나가 된다.

    스칼라는 그대로 한 그룹이다 — 오버로드 줄이 전부 같은 레벨이면 어차피 한 그룹으로
    합쳐지므로, 기본 스펙(레벨 10 2줄 = 129.64)은 이 표기 그대로 정확하다.
    **단계가 섞인 장비**만 줄별 퍼센트 리스트(`[64.82, 52.5]`)로 적고, 여기서 같은
    값(= 같은 레벨)끼리 묶어 그룹을 만든다. `scraper/profile_fetch.py`가 그렇게 낸다.

    소스별 반올림을 하지 않는 스탯(공격력 등)은 선형 합산이라 한 덩어리로 접는다 —
    쪼개도 결과는 같고 합산 순서만 흔들린다.
    """
    if not isinstance(val, (list, tuple)):
        return [float(val)]
    lines = [float(v) for v in val]
    if not lines:
        return []
    if (_EQUIP_SKILLS.get(stat) or {}).get("buff_type") not in _QUANT_BUFF_KEYS:
        return [sum(lines)]
    groups: dict[float, float] = {}
    for v in lines:
        groups[v] = groups.get(v, 0.0) + v
    return list(groups.values())


# 수치 없이 True만 세우는 boolean 플래그 buff_key
_BOOL_BUFF_KEYS = frozenset([
    "charge_time_fixed", "charge_speed_buff_immune", "charge_speed_debuff_immune",
    "debuff_immune", "stun_immune", "stack_change_immune", "taunt",
    "pierce_enabled", "infinite_ammo", "armor_break_enabled", "persona_state",
])

# get_buffs 실행 계획의 스텝 종류 (`BuffManager._build_plan` 참고)
_PLAN_ADD, _PLAN_CRIT, _PLAN_FLAG, _PLAN_LIVE, _PLAN_QUANT, _PLAN_CDMG = 0, 1, 2, 3, 4, 5

# 계획 캐시 감사 모드. `NIKKE_BUFF_AUDIT=1`이면 매 조회마다 계획을 다시 만들어 캐시와
# 대조하고, 다르면 즉시 예외를 던진다 (조용히 틀리는 대신 터진다).
#
# 계획 캐시의 전제는 **`_active`가 바뀌면 반드시 `_invalidate_buffs_cache()`를 거친다**는
# 것 하나다. 지금 코드의 모든 `_active` 변경 지점이 이를 지키지만, 앞으로 추가될 효과가
# 이 전제를 깰 수 있다. 새 캐릭터를 넣고 결과가 의심스러우면 이 모드로 회귀를 돌린다:
#
#     NIKKE_BUFF_AUDIT=1 python -m runner.snapshot --squad <스쿼드>
#
# 느리므로 평상시에는 끈다.
_BUFF_AUDIT = os.environ.get("NIKKE_BUFF_AUDIT") == "1"

# 대상별 보호막을 만드는 stat 집합. `shared_shield_from_max_hp_pct`(아군 공용 보호막)는
# 부여 대상이 시전자 1인이라는 점만 다르고 보호막 판정(during_shield ·
# event:shield_applied)은 동일하게 성립한다 — 대상 수는 target 값이 결정한다. (블랑)
_SHIELD_STATS = frozenset(["shield_from_max_hp_pct", "shared_shield_from_max_hp_pct"])

# 분신(디코이)을 만드는 stat 집합. 보호막과 같은 모양으로 대상별 체력을 들고 다니지만
# **층이 바깥**이다 — 분신은 니케 몸에 붙은 막이 아니라 별개 개체라 보호막·엄폐물보다 먼저 맞는다.
# (`timeline._land_squad` §층 규칙 · `docs/scenarios/라이.md` §해석 선언)
_DECOY_STATS = frozenset(["decoy"])

# get_buffs 시점에 재평가가 필요한 runtime condition 접두사 집합
# 이 집합에 포함된 조건이 하나라도 있으면 ActiveBuff.has_runtime_conditions = True
_RUNTIME_COND_PREFIXES = frozenset([
    "during_charge", "during_full_burst", "not_during_full_burst",
    "during_shield",
    "self_hp_above:", "self_hp_below:", "self_hp_max",
    "ally_hp_below:",
    "self_stack_above:", "self_state:", "not_self_state:",
    "target_state:", "not_target_state:",
    "gauge_above:", "gauge_below:",
    # 적 수 조건 — 쫄몹(보스 패턴 summon)이 없으면 적 1기라 상수 판정이지만 여기 등록해야 한다.
    # passive 버프는 조건 미충족이어도 _activate()로 등록되고(suppress_event만 다름)
    # 이후 게이팅을 전적으로 이 목록에 의존한다 — 빠지면 "적 N기 이상" 버프가
    # 보스전에서 그대로 적용된다 (맥스웰 `일렉트릭 샷` 크리 확률·크리 대미지).
    "enemy_count_above:", "enemy_count_below:",
    # 적 코드 조건 — 위 「적 수」와 같은 이유다(유저 결정 2026-09-22). 적 코드는 전투 중
    # 변하지 않으므로 기존 보유자 12명(전부 유한 지속이거나 이산 timing)의 값은 바뀌지
    # 않지만, **무한 지속 `passive`는 이 목록에만 게이팅을 의존**하므로 빠져 있으면 코드
    # 조건이 통째로 무시된다 — 레이블 `연애의 달콤함(상상) 4`가 풍압 보스에게도 전격 한정
    # 피해 감소를 그대로 받고 있었다(같은 캐릭터의 `battle_start` 판본은 정상이라 한
    # 캐릭터 안에서 두 판정이 갈렸다). 맥스웰 `일렉트릭 샷`과 같은 계통.
    "target_code:",
    # 엄폐물은 보스 공격 패턴이 있을 때만 부서진다 — 패턴이 없으면 늘 참이다
    # (슈가 `블랙 타이푼 4` 「자신의 엄폐물이 생존해 있을 때 한하여」).
    # 부정판(「자신의 엄폐물이 파괴된 상태라면」, 베이)도 같은 이유로 여기 있어야 한다 —
    # 부서지는 프레임에 켜지고 되살아나는(`cover_revive`) 프레임에 꺼져야 한다.
    "self_cover_alive", "not_self_cover_alive",
    # 「자신이 포커싱 상태일 때」 — 카메라를 잡고 있다(`state["camera"]`). 카메라는 조율이 프레임마다 옮긴다
    # (리틀 머메이드 `버블 오더` → 아군 전체 [사격 집중]).
    "focusing",
])


def _is_enemy(name: str) -> bool:
    """적을 가리키는 대상 이름인가 — 보스 센티널 `__enemy__`와 쫄몹 id `__enemy__:<패턴>#<번호>`
    (`boss_pattern.ADD_PREFIX`와 같은 규약). 쫄몹이 없으면 늘 센티널 하나다."""
    return name == "__enemy__" or name.startswith("__enemy__:")


def _is_cond_finite_passive(eff: dict) -> bool:
    """조건부 `passive` 중 **유한 지속**인 것인가.

    `passive`는 `battle_start`에 한 번만 등록된다(`_timing_match`). 지속이 `-1`이면 그걸로
    충분하다 — 게이팅을 런타임 재평가(`_RUNTIME_COND_PREFIXES`)에 맡기면 조건이 곧 유효
    구간이 된다. 그런데 **유한 지속이면 한 번 만료된 뒤 다시 켤 경로가 없다.** 조건이
    t=0에 거짓이면 등록되자마자 수명만 흘러 죽고, 조건이 참이 되어도 돌아오지 않는다.

    원문 「자신의 체력이 90% 이하일 때 … [5초 유지]」는 *조건이 유지되는 동안 계속 걸리고
    조건이 깨진 뒤 5초 더 남는다*는 뜻이다. 그래서 이 부류는 `tick()`이 따로 돌본다 —
    조건이 참인 동안 만료 시각을 밀고, 거짓이 되면 그대로 잔류시켜 만료시킨다.
    (에이드 `청소를 시작하겠습니다, 주인님.` · 치사토 `사격 간파`. 유저 결정 2026-09-15)

    `[N발 유지]`는 대상이 아니다 — 로스터에 이 조합이 없고, 발수 수명은 시간 축으로
    밀 수 없다.
    """
    if eff.get("type") != "buff":
        return False
    if "passive" not in eff["trigger"]["timing"]:
        return False
    if not eff["trigger"].get("condition"):
        return False
    if eff.get("duration_bullets", -1) != -1:
        return False
    duration = eff.get("duration")
    if duration is None and "duration_values" in eff:
        return True
    return duration is not None and duration != -1


def _has_runtime_cond(conditions: list, expires: float,
                      duration_bullets: int = -1) -> bool:
    """
    이 버프가 get_buffs 시점마다 조건을 재평가해야 하는지.

    스킬 텍스트 문법상 조건은 **발동 시점 게이트**이고 `[N초 유지]`는 버프 자체의
    지속시간이다 (예: "■ ... 시 소드 코인 상태라면 ... [10초 유지]" → 발동 순간
    소드 코인이면 그때부터 10초. 도중에 소드 코인이 풀려도 10초는 끝까지 간다).
    따라서 유한 duration 버프는 재평가 대상이 아니다.

    재평가는 duration -1 / null (지속·영구) 버프에만 적용한다. 그쪽은 만료 시각이
    없으므로 조건이 곧 유효 구간이다 (조건부 passive와 같은 기준 — tick()의
    `ab.expires_at < math.inf: continue` 참고).

    **`[N발 유지]`(`duration_bullets`)도 유한 지속이라 재평가하지 않는다.** 시간이
    아니라 발수로 끝날 뿐 "발동 시점 게이트 + 정해진 수명"이라는 구조는 `[N초 유지]`와
    같다 — 남은 한 발을 쏠 때까지는 그 발이 버프를 받아야 한다. 눈금이 초가 아니어서
    `expires_at`이 `inf`로 남는 탓에 위 게이트를 그냥 통과했고, 그 결과 **자기 상태
    이름을 `not_self_state:`로 막는 재부여 게이트가 스스로를 꺼 버렸다**
    (베스티 : 택티컬 업 `미사일 가이드` — 차지 속도 100%·차지 대미지 58.5가 실측 0).
    """
    if expires != math.inf or duration_bullets != -1:
        return False
    for c in conditions:
        for prefix in _RUNTIME_COND_PREFIXES:
            if c == prefix or c.startswith(prefix):
                return True
    return False


# 발사와 같은 프레임에 발동하는 트리거 타이밍.
# 이런 타이밍으로 활성화된 duration_bullets 버프는 활성화 직후 발사도 1발로 카운트한다
# (예: full_charge → 즉시 발사하는 SR/RL 풀차지. 그 발사가 곧 "1발" 자체).
#
# 판정 기준은 "같은 프레임"이 아니라 **그 발사의 calc_damage보다 앞서 발동하는가**다.
# 앞서면 그 발이 버프를 받으므로 1발로 세는 게 맞고, 뒤에 발동하면 받지도 못한 발에
# 소모만 당해 버프가 통째로 사라진다. **풀차지 사격 트리거 둘(`full_charge_fire`·
# `full_charge_hit`)은 발사 처리 **뒤**(timeline `_tick_charge()`가 calc_damage →
# notify(...) → consume_bullet_buffs 순)라 여기 넣으면 안 된다** — 짝인
# `full_charge`(차지 완료 = 발사 전)는 맞다. (아인 `페더 샷` — 넣어 두었을 때
# charge_dmg_pct 80%가 한 발에도 적용되지 않았다. 라플라스 : 얼티밋 히어로 딜은 불변)
_BULLET_BOUND_TIMINGS = frozenset([
    "full_charge",
    "on_attack", "hit_count", "pellet_hit", "core_hit", "crit_hit",
    "last_bullet", "last_bullet_fire",
    "event:full_reload", "squad_ammo_consume",
])


def _is_bullet_bound_trigger(eff: dict) -> bool:
    for timing in eff.get("trigger", {}).get("timing", []) or []:
        if timing in _BULLET_BOUND_TIMINGS:
            return True
    return False


# 활성화 시점이 아닌 get_buffs 시점에 타겟을 결정해야 하는 target 패턴
# (스탯 비교 기반 → 버프가 모두 반영된 후 순위가 정해져야 함)
_LAZY_RESOLVE_PREFIXES = (
    "allies_lowest_atk_burst3:",
    "allies_top_atk:",
    "allies_top_atk_excl:",
    "allies_weapon_top_atk:",
    "allies_lowest_hp:",
    "allies_lowest_hp_excl:",
    "allies_top_def:",
    "allies_below_def",
    "allies_random:",
)

# 주기 대미지 만료 경계 비교용 여유. 1프레임(1/60초)보다 훨씬 작아
# 정상 틱을 삼키지 않으면서 float 누적 오차만 흡수한다.
_TICK_EPS = 1e-6
# 만료 시각에 떨어지는 마지막 틱을 "살짝 당겨" 계산할 때 쓰는 폭.
# `get_buffs()`의 `t >= expires_at` 컷을 피할 만큼 크고, 1프레임보다는 훨씬 작다.
_TICK_NUDGE = 1e-4


# ── ActiveBuff ────────────────────────────────────────────────────────────

_AB_SEQ = itertools.count()  # ActiveBuff 고유 번호 발급기 (uid 필드 참고)


@dataclass
class ActiveBuff:
    effect: dict           # parsed effect 항목 원본
    caster: str            # 시전자 캐릭터명
    target_chars: list | None  # None = 지연 resolve (get_buffs 시점에 결정)
    activated_at: float
    expires_at: float      # math.inf = 영구
    stack: int = 1
    trigger_count: int = 1
    bullets_left: int = -1  # duration_bullets 기반 만료용. -1이면 미사용 (단일 caster 전용)
    bullets_per_target: dict = field(default_factory=dict)  # 캐릭터별 잔여 발사 횟수 (다중 target용)
    per_char_stacks: dict = field(default_factory=dict)     # 캐릭터별 독립 스택 (use_per_target + max_stack>1 전용)
    has_runtime_conditions: bool = False  # get_buffs 시점 재평가 필요 여부 (성능 최적화용)
    log_pending: bool = False  # 지연 resolve 대상이라 activate 로그를 아직 못 남긴 상태.
                               # _resolve_lazy()가 대상을 확정하는 순간 남긴다 — 활성화
                               # 시점에 미리 resolve해 찍으면 같은 프레임에 나중 발동하는
                               # 버프가 순위를 뒤집을 때 로그만 틀린 대상을 가리킨다.
    scaling_stack: int | None = None  # scaling:stack_count + scaling_ref 버프의 발동 시점 참조 중첩
                                      # (None = 미고정 → 조회 시점 값 사용). _capture_scaling_stack() 참고
    shield_per_target: dict[str, float] = field(default_factory=dict)
                                      # shield_from_max_hp_pct의 대상별 보호막량.
                                      # 수명은 ActiveBuff와 같아 별도 만료 상태를 두지 않는다.
    shield_max_per_target: dict[str, float] = field(default_factory=dict)
                                      # 부여 시점의 보호막량 스냅샷. `shield_heal_pct`가
                                      # 되돌릴 수 있는 상한이다 — 회복은 「깎인 만큼」이지
                                      # 「더 크게」가 아니다. `shield_per_target`과 같은
                                      # 자리에서 함께 갱신되므로 재발동하면 상한도 새 값이다.
    decoy_per_target: dict[str, float] = field(default_factory=dict)
                                      # `decoy`(분신)의 대상별 남은 체력. 보호막과 같은 모양이고
                                      # 층만 바깥이다 — 분신은 니케 몸 밖의 별개 개체다.
    decoy_max_per_target: dict[str, float] = field(default_factory=dict)
                                      # 부여 시점의 분신 체력 스냅샷. `decoy_heal_pct`가
                                      # 되돌릴 수 있는 상한이다(`shield_max_per_target`과 같은 자리).
    accum: float = 0.0                # 「누적 → 폭발」 누적기가 지금까지 모은 대미지.
                                      # `dmg_accum_dealt_atk_pct`(시전자가 가하는 딜) ·
                                      # `dmg_accum_received_atk_pct`(대상이 받는 딜) 전용.
    accum_cap: float = 0.0            # 누적 상한 = 시전자 최종 공격력 × values%.
                                      # **부여 시점 스냅샷이다**(유저 결정 2026-09-22) —
                                      # 매 프레임 재평가하면 버스트의 공격력 ▲가 상한을
                                      # 누적 가속과 같은 배율로 밀어 올려 트로니
                                      # `누적 폭발 스킬`이 영구 무발동이 된다
                                      # (`docs/scenarios/트로니.md` §실측).
    accum_done: bool = False          # 상한에 닿았거나 이미 방출했다 — 더 누적하지 않는다.
                                      # 방출 대미지 자신이 다시 누적되는 재귀를 막는 자리이기도 하다.
    hp_bonus_flat: float = 0.0        # max_hp_from_max_hp_pct가 부여 시점에 확정한 최대 체력
                                      # 가산분(절대값). 「시전자의 **최종** 최대 체력 비례」라
                                      # 조회 시점에 다시 재면 시전자 자신이 대상일 때
                                      # effective_max_hp가 자기를 다시 부르는 재귀가 된다 —
                                      # 보호막(`shield_per_target`)과 같이 부여 시점 스냅샷으로 둔다.

    uid: int = field(default_factory=lambda: next(_AB_SEQ))
    # 이 인스턴스의 고유 식별자.
    #
    # id(ab)를 키로 쓰면 안 된다. 만료된 ActiveBuff가 GC되면 CPython이 그 메모리
    # 주소를 새 객체에 재사용하므로, _cond_passive_prev 같이 수명이 더 긴 dict에
    # 남아 있던 옛 항목을 새 버프가 물려받는다. 그 결과 같은 시드로도 "앞서 무엇을
    # 실행했는가"에 따라 버프 발동 횟수가 달라졌다 (회귀 하네스가 검출).


# ── BuffManager ───────────────────────────────────────────────────────────

class BuffManager:
    """
    5인 스쿼드 버프/디버프 관리자.

    Parameters
    ----------
    squad : list[dict]
        캐릭터 인스턴스 목록 (base_stat.py와 동일 구조, skill_level 추가)
    state : dict
        타임라인 공유 상태. 최소 키: "full_burst", "burst_casted"
        필요에 따라 타임라인이 채워준다.
    """

    def __init__(self, squad: list[dict], state: dict | None = None):
        self.squad = squad
        self.squad_names = [c["name"] for c in squad]
        self.state = state or {}

        # 캐릭터명 → 인스턴스 빠른 접근
        self._char = {c["name"]: c for c in squad}

        # 등록된 효과 목록: (effect, caster_name)
        self._effects: list[tuple[dict, str]] = []

        # 캐릭터명 → 애장품 단계까지 반영한 스킬 효과 목록 (`char_effects()`)
        self._char_effects_cache: dict[str, list[dict]] = {}

        # 활성 버프 목록
        self._active: list[ActiveBuff] = []

        # every:Ns 효과별 다음 발동 시각
        # id(effect) → (next_t, interval). interval을 같이 들고 있어야 쿨감이
        # 도중에 바뀐 걸 감지해 진행 중인 쿨타임의 잔여분을 재조정할 수 있다.
        self._next_fire: dict[int, tuple[float, float]] = {}

        # tick_interval damage 효과별 타이머: id(effect) → (caster, next_t, expires_at)
        self._dot_timers: dict[int, tuple[str, float, float]] = {}

        # 「누적 → 폭발」 누적기의 마지막 누적량: 상태 이름 → 값.
        # 방출(`accum_split_damage`)이 `event:state_end:`에 걸리는 형태(도로시 `낙인`)에서는
        # 방출 시점에 담체 ActiveBuff가 이미 `_active`에서 빠져 있어 그쪽을 읽을 수 없다 —
        # 누적할 때마다 여기에도 남겨 두고, 방출은 활성 담체가 없으면 이 값을 쓴다.
        self._accum_last: dict[str, float] = {}

        # `same_target:[이름]` DoT의 중첩 램프 예약: [(fire_t, effect, caster, stack)]
        # 짝 공격이 한 발씩 중첩을 얹는 구조라 **시간에 펼쳐야** 한다 — 한 시점에
        # 몰아 쏘면 램프 전체가 풀버스트 경계 밖으로 밀린다(사쿠라 : 블룸 인 서머).
        self._ramp_pending: list[tuple[float, dict, str, int]] = []

        # tick_interval instant 효과별 타이머: id(effect) → (caster, next_t, expires_at)
        self._instant_timers: dict[int, tuple[str, float, float]] = {}
        # charge_hold:N 임계값 캐시 (캐스터별). `charge_hold_thresholds()` 참조
        self._charge_hold_cache: dict[str, list[tuple[float, str]]] = {}

        # pellet_hit_in_shot:N 임계값 캐시 (캐스터별). `pellet_in_shot_thresholds()` 참조
        self._pellet_in_shot_cache: dict[str, list[tuple[int, str]]] = {}

        # 지연 resolve 대상 캐시: (caster, 활성화 시각, target 문자열) → 대상 목록.
        # 같은 시전자가 같은 시각에 같은 target으로 건 효과들이 대상을 공유한다.
        # `_resolve_lazy()` 참조 (블랑 `쇼타임` 불굴 ↔ 최대 체력)
        self._lazy_target_cache: dict[tuple[str, float, str], list[str]] = {}

        # 이벤트별 발동 횟수 (hit_count, burst_cast_count 등 추적용)
        self._event_counts: dict[str, dict[str, int]] = {}  # caster → {event_key: count}
        # `buff_max_stack_add`가 수령자·비수령자 공유 ActiveBuff를 건너뛴 횟수(근사 계측용)
        self.partial_skips: int = 0

        # 전투불능 때 잃은 영구 버프: 니케 → [(effect, 시전자)]. 부활 때 패시브만 골라 다시 붙인다
        # (`knock_down` · `_reapply_passives`)
        self._down_lost: dict[str, list[tuple[dict, str]]] = {}

        # max_trigger 추적: id(effect) → 발동 횟수 (buff/instant/damage/weapon_change 공통)
        self._trigger_counts: dict[int, int] = {}

        # 현재 시각. sync_hp()처럼 t를 받지 않는 지점에서 이벤트를 쏘기 위해 보관
        self._cur_t: float = 0.0
        # 지금 처리 중인 notify의 추가 컨텍스트 (hit_crit 등). _condition_ok가 읽는다
        self._notify_ctx: dict = {}
        # sync_hp → notify → _activate → sync_hp 재진입 방지
        self._in_hp_edge: bool = False

        # instant stat → 핸들러. 타임라인이 register_instant_handler()로 주입
        self._instant_handlers: dict[str, Any] = {}

        # instant 이벤트 로그 콜백. 타임라인이 register_instant_event_handler()로 주입
        self._instant_event_handler: Any = None
        self._gauge_event_handler: Any = None

        # damage 효과 핸들러. 타임라인이 register_damage_handler()로 주입
        self._damage_handler: Any = None

        # 쫄몹이 살아 있을 때 적 대상 문자열을 적 id 목록으로 푸는 콜백 — 타임라인이 보스 패턴에 summon이
        # 있을 때만 넣는다(`BossScript.resolve_enemies`). 쫄몹이 없으면 None을 돌려주고 종전 센티널로 간다
        self.enemy_resolver: Any = None

        # 니케의 실효 최대 장탄을 묻는 콜백 `(이름, t) → 발수` — 타임라인이 `CharState._full_ammo()`로 넣는다.
        # `scaling: "max_ammo_count"`(「최종 최대 장탄 수 1발 당」)가 읽는다(`_scale_by_max_ammo`).
        # 최대 장탄은 타임라인이 들고 있어 여기서 직접 셀 수 없다
        self.max_ammo_provider: Any = None
        # 위 콜백이 다시 `get_buffs`를 부르는 동안의 수령자 — 재귀 차단용(`_scale_by_max_ammo`)
        self._ammo_query: set[str] = set()

        # 버프 활성/만료 이벤트 콜백. 타임라인이 register_buff_event_handler()로 주입
        # handler(kind, name, caster, target, t, expires_at)
        self._buff_event_handler: Any = None

        # get_buffs 캐시: (caster, t, _cache_version) → buffs dict
        self._buffs_cache: dict = {}
        self._cache_version: int = 0

        # get_buffs 실행 계획 캐시: (caster, target, exclude_names) → (plan, hp_abs, cb_abs)
        # `_active`가 그대로인 동안(= 같은 _cache_version) 기여가 변하지 않는 버프를
        # 미리 평가해 둔다. 자세한 근거는 get_buffs / _plan_step 참고.
        self._plan_cache: dict = {}

        # `_active`를 stat/name으로 되짚는 인덱스. 셋 다 _invalidate_buffs_cache에서 함께 비운다
        self._stat_index: dict[str, list] = {}
        self._name_index_cache: dict[str, list] = {}

        # id(eff) → eff 역참조. _effects는 __init__ 이후 불변이라 1회만 만든다
        self._eff_by_id: dict[int, dict] = {}

        # is_stunned 캐시: char_name → bool (_invalidate_buffs_cache 시 함께 초기화)
        self._stunned_cache: dict = {}

        # notify 인덱스: event_key → [(eff, caster), ...]
        # caster별: _notify_index[caster][event_key] = [(eff, caster), ...]
        # squad_ammo_consume 전용: _squad_notify_index[event_key] = [(eff, caster), ...]
        # part_hit_count / body_hit_count 전용: _squad_hit_index[event_key] = [(eff, caster), ...]
        self._notify_index: dict[str, dict[str, list]] = {}
        self._squad_notify_index: dict[str, list] = {}
        self._squad_hit_index: dict[str, list] = {}
        # every_stack:이름:N 전용: (caster, 게이지명) → 그 게이지를 보는 N 목록.
        # 게이지 충전이 이 N들의 배수 경계를 넘을 때만 이벤트를 쏜다
        self._every_stack_steps: dict[tuple[str, str], list[int]] = {}

        # 조건부 passive 버프의 이전 틱 조건 충족 여부: id(ActiveBuff) → bool
        # tick()에서 False→True / True→False 전환 감지해 buff_event_handler 발생
        self._cond_passive_prev: dict[int, bool] = {}

        # `debuff_immune_count` 소모량: (니케, 버프 이름) → 쓴 개수.
        # 재부여 시 0으로 되돌린다 (`_consume_immune_charge` 참조)
        self._immune_used: dict[tuple[str, str], float] = {}

        self._register_all()

        # `_effects`는 여기서 확정되고 이후 변하지 않는다 — 프레임마다 다시 훑던 두 가지를
        # 이 시점에 한 번만 만든다. `tick()`의 every:Ns 블록과 tick_interval 블록이 쓴다.
        self._eff_by_id = {id(eff): eff for eff, _ in self._effects}
        self._every_effects: list[tuple[dict, str, str]] = [
            (eff, caster, timing)
            for eff, caster in self._effects
            for timing in eff["trigger"]["timing"]
            if timing.startswith("every:")
        ]
        # 조건부 passive 중 유한 지속인 것 — tick()이 조건이 참인 동안 만료를 민다
        # (`_is_cond_finite_passive` 참조). 로스터 전체에서 두 항목뿐이라 비용은 없다.
        self._cond_finite_passives: list[tuple[dict, str]] = [
            (eff, caster)
            for eff, caster in self._effects
            if _is_cond_finite_passive(eff)
        ]

    # ── 등록 ─────────────────────────────────────────────────────────────

    def char_effects(self, name: str) -> list[dict]:
        """스쿼드 멤버의 활성 스킬 효과 목록 (그 캐릭터의 애장품 단계 기준).

        모듈 함수 `char_effects()`와 달리 단계를 캐릭터 dict에서 읽는다. 효과 목록을
        순서대로 되짚는 타임라인 쪽 코드도 `_PARSED_SKILLS` 대신 이걸 써야 한다 —
        원본에는 안 쓰는 판본이 섞여 있어 서술 순서가 실제 실행 순서와 어긋난다.
        """
        if name not in self._char_effects_cache:
            char = self._char.get(name) or {}
            self._char_effects_cache[name] = char_effects(name, char.get("favorite_stage"))
        return self._char_effects_cache[name]

    def _register_all(self):
        """스쿼드 전원의 모든 버프 소스를 효과 목록에 등록."""
        for char in self.squad:
            name = char["name"]
            # parsed_skills (애장품 단계에 맞는 슬롯 판본만)
            for eff in self.char_effects(name):
                self._effects.append((eff, name))
            # 장비 스킬 (부위별 개별 옵션)
            for part_data in char["equipment"].values():
                for sk in part_data.get("skills", []):
                    eff = self._make_equip_effect(sk["id"], sk["lv"])
                    if eff:
                        self._effects.append((eff, name))
            # 장비 옵션 (equip_skills) — 스칼라는 한 그룹, 리스트는 줄별 값
            for stat, val in char.get("equip_skills", {}).items():
                for gval in _equip_option_groups(stat, val):
                    eff = self._make_equip_effect(stat, None, fixed_val=gval)
                    if eff:
                        eff = {**eff, "name": "장비 옵션"}
                        self._effects.append((eff, name))
            # 큐브 스킬 (공통 + 종류별)
            cube_name = char["cube"]["name"]
            cube_lv = char["cube"]["level"]
            for eff in self._make_cube_effects(cube_name, cube_lv):
                self._effects.append((eff, name))
            # 소장품 무기군 스킬
            for eff in self._make_collection_effects(char):
                self._effects.append((eff, name))

        self._build_notify_index()

    def _timing_to_index_key(self, timing: str) -> str | None:
        """timing 문자열 → notify 인덱스 조회 키. every:* 는 None 반환 (틱 전용)."""
        if timing == "passive":
            return "battle_start"
        if timing.startswith("every:"):
            return None
        if timing.startswith("burst_cast_count:"):
            return "burst_cast"
        if timing.startswith("full_burst_start_count:") or timing.startswith("full_burst_start_exact:"):
            return "full_burst_start"
        if timing.startswith("full_burst_end_count:"):
            return "full_burst_end"
        # `burst_enter_count:N:M` — 「버스트 N단계 돌입 시 [시작 횟수 별 효과]」. 이벤트는
        # `burst_enter:N` 그대로이고 횟수 판정만 `_timing_match`가 한다 (네온 : 블루 오션 `워터 제트`)
        if timing.startswith("burst_enter_count:"):
            return "burst_enter:" + timing.split(":")[1]
        # 풀차지는 발사(`풀 차지 공격 시`)와 명중(`풀 차지 공격 명중 시`)이 별개 이벤트다.
        # `full_charge_count:N`은 분리 전 표기라 **발사**의 별칭으로 남긴다 — 데이터는
        # 전부 `full_charge_fire_count:N`으로 옮겼지만, 옛 표기가 들어와도 조용히
        # 영구 무발동이 되지 않게 한다(루드밀라 `눈보라` 사고와 같은 종류의 예방).
        if (timing.startswith("full_charge_fire_count:")
                or timing.startswith("full_charge_count:")):
            return "full_charge_fire"
        if timing.startswith("full_charge_hit_count:"):
            return "full_charge_hit"
        # `풀 차지 공격이 아닌 일반 공격 N회 공격 시` — 풀차지 발사의 여집합.
        # 톡톡이(논차지 샷)가 없으면 차지 무기의 모든 발사가 풀차지라 영구 무발동이다.
        if timing.startswith("non_full_charge_fire_count:"):
            return "non_full_charge_fire"
        # `풀 차지 상태 N초 이상 유지를 M회 실행 시` — charge_hold:N 이벤트를 M회 센다.
        # 이벤트 표기가 원문 그대로라(`charge_hold:0.5` ≠ `charge_hold:0.50`) N을 그대로 붙인다.
        if timing.startswith("charge_hold_count:"):
            parts = timing.split(":")
            return f"charge_hold:{parts[1]}" if len(parts) >= 3 else None
        # `일반 공격 N회 공격 시` — 원문이 「공격」이라 명중이 아니라 발사에 붙는다
        if timing.startswith("on_attack_count:"):
            return "on_attack"
        if timing.startswith("hit_count:"):
            parts = timing.split(":", 2)
            if len(parts) == 3 and not parts[1].lstrip("-").isdigit():
                return f"hit_count:{parts[1]}"
            return "hit_count"
        if timing.startswith("core_hit_count:") or timing.startswith("core_hit:"):
            return "core_hit"
        if timing.startswith("crit_hit_count:"):
            return "crit_hit"
        if timing.startswith("received_hit_count:") or timing.startswith("received_hit:"):
            return "received_hit"
        if timing.startswith("pellet_hit_count:") or timing.startswith("pellet_hit:"):
            return "pellet_hit"
        if timing.startswith("hp_below_count:"):
            # "hp_below_count:T:N" → hp_below:T 이벤트에 반응
            parts = timing.split(":")
            return f"hp_below:{parts[1]}" if len(parts) >= 2 else "hp_below:"
        if timing.startswith("squad_ammo_consume:"):
            return "squad_ammo_consume"
        if timing.startswith("part_hit_count:"):
            return "squad_part_hit"
        if timing.startswith("body_hit_count:"):
            return "squad_body_hit"
        # every_stack:이름:N → every_stack:이름 (N은 _timing_match가 경계값으로 가른다)
        if timing.startswith("every_stack:"):
            return timing.rsplit(":", 1)[0]
        # 나머지는 timing 자체가 event 키
        return timing

    def _build_notify_index(self):
        """_effects 로부터 notify 인덱스를 구축."""
        self._notify_index.clear()
        self._squad_notify_index.clear()
        self._squad_hit_index.clear()
        self._every_stack_steps.clear()
        valid_types = ("buff", "instant", "weapon_change", "damage")

        for eff, eff_caster in self._effects:
            if eff.get("type") not in valid_types:
                continue
            for timing in eff["trigger"]["timing"]:
                key = self._timing_to_index_key(timing)
                if key is None:
                    continue
                if timing.startswith("every_stack:"):
                    ref, raw_n = timing[len("every_stack:"):].rsplit(":", 1)
                    steps = self._every_stack_steps.setdefault((eff_caster, ref), [])
                    if raw_n.isdigit() and int(raw_n) > 0 and int(raw_n) not in steps:
                        steps.append(int(raw_n))
                if timing.startswith("squad_ammo_consume:"):
                    bucket = self._squad_notify_index.setdefault(key, [])
                    bucket.append((eff, eff_caster))
                elif timing.startswith("part_hit_count:") or timing.startswith("body_hit_count:"):
                    bucket = self._squad_hit_index.setdefault(key, [])
                    bucket.append((eff, eff_caster))
                else:
                    caster_idx = self._notify_index.setdefault(eff_caster, {})
                    bucket = caster_idx.setdefault(key, [])
                    bucket.append((eff, eff_caster))

    def _make_equip_effect(self, skill_id: str, lv: int | None, fixed_val: float | None = None) -> dict | None:
        entry = _EQUIP_SKILLS.get(skill_id)
        if not entry or skill_id.startswith("_"):
            return None
        if fixed_val is not None:
            val = fixed_val
        else:
            val = entry["values"][lv - 1] * 100  # 소수 → %
        return {
            "type": "buff",
            "name": f"장비:{skill_id}",
            "trigger": {"timing": ["passive"], "condition": []},
            "target": "self",
            "stat": entry["buff_type"],
            "polarity": "beneficial",
            "fixed_value": val,
            "duration": None,
            "_source_tag": "equipment",
            # 소스별 반올림의 그룹 태그(`_quant_group_key`). 종류·수치(=레벨)가 같으면
            # 부위가 달라도 같은 태그가 되어 합산 후 한 번만 반올림된다.
            "_quant_group": f"equip:{skill_id}:{val!r}",
        }

    def _make_cube_effects(self, cube_name: str, cube_lv: int) -> list[dict]:
        """큐브 효과 목록.

        `공통`(우월 코드 공격 대미지)은 소장품의 `공통`과 마찬가지로 **어떤 큐브를 끼든
        항상 붙는다** — 모든 큐브의 두 번째 스킬이 같기 때문이다. 큐브 이름으로 고른
        효과는 그 위에 추가된다.

        `unsupported`가 달린 항목(계산기 미구현 stat·조건부 발동)은 등록하지 않는다.
        `cube.json`이 데이터는 다 갖고 있되 엔진이 못 다루는 것을 명시한 표시다.

        엔트리의 `type`·`timing`을 그대로 따른다. 없으면 `battle_start` 상시 버프다 —
        대부분의 큐브가 그렇지만, 택티컬 베어 큐브(10발 사격 시 탄환 충전)처럼
        `type: instant` + 트리거 타이밍으로 오는 것도 있다. instant는 duration이 없다
        (`parsed_skills.json`의 instant와 같은 모양이어야 타임라인 핸들러가 받는다).
        """
        names = ["공통"]
        if cube_name != "공통":
            names.append(cube_name)

        effects = []
        for nm in names:
            entry = _CUBE.get(nm)
            if not entry or nm.startswith("_") or entry.get("unsupported"):
                continue
            vals = entry.get("values", {}).get(str(cube_lv))
            if not vals:
                continue
            val = float(vals[0])
            # 받는 대미지 감소(이로운) → 음수로 저장 (소장품과 같은 규약)
            if entry["stat"] == "received_dmg_pct":
                val = -val
            eff = {
                "type": entry.get("type", "buff"),
                "name": f"큐브:{nm}",
                "trigger": {
                    "timing": [entry.get("timing", "battle_start")],
                    "condition": [],
                },
                "target": "self",
                "stat": entry["stat"],
                "fixed_value": val,
                "_source_tag": "cube",
            }
            # 「시전자의 최대 체력 비례 …」 — 기준 표기는 스킬 효과와 같은 `scaling` 칸으로 온다(커버 헬스 업)
            if entry.get("scaling"):
                eff["scaling"] = entry["scaling"]
            if eff["type"] == "buff":
                eff["polarity"] = "beneficial"
                eff["duration"] = None
            effects.append(eff)
        return effects

    def _make_collection_effects(self, char: dict) -> list[dict]:
        stage = char["collection_stage"]
        if stage == NO_ITEM:        # 미장착 — 플랫 스탯도 스킬도 없다
            return []
        entry = _COLLECTION["_stat_table"].get(stage)
        if entry is None:
            raise KeyError(
                f"[{char['name']}] 알 수 없는 소장품 단계 {stage!r} — "
                "'R0'~'R15' · 'SR0'~'SR15' 또는 '없음'(미장착)")
        skill_lv = entry["skill_lv"]
        idx = skill_lv - 1
        rarity_prefix = "SR" if stage.startswith("SR") else "R"
        weapon = _NIKKE[char["name"]]["weapon_type"]
        effects = []

        # common 스킬들
        for skill_name, skill_data in _COLLECTION["common"].items():
            if rarity_prefix not in skill_data:
                continue
            val = skill_data[rarity_prefix][idx]
            # received_dmg_pct는 감소(이로운) → 음수로 저장
            if skill_data["buff_type"] == "received_dmg_pct":
                val = -val
            effects.append({
                "type": "buff",
                "name": "소장품:공통",
                "trigger": {"timing": ["passive"], "condition": []},
                "target": "self",
                "stat": skill_data["buff_type"],
                "polarity": "beneficial",
                "fixed_value": float(val),
                "duration": None,
                "_source_tag": "collection",
            })

        # 무기군 스킬
        weapon_data = _COLLECTION.get(weapon)
        if weapon_data and rarity_prefix in weapon_data:
            val = weapon_data[rarity_prefix][idx]
            effects.append({
                "type": "buff",
                "name": f"소장품:{weapon}",
                "trigger": {"timing": ["passive"], "condition": []},
                "target": "self",
                "stat": weapon_data["buff_type"],
                "polarity": "beneficial",
                "fixed_value": float(val),
                "duration": None,
                "_source_tag": "collection",
            })

        return effects

    # ── instant 콜백 등록 ─────────────────────────────────────────────────

    def register_damage_handler(self, handler):
        """
        타임라인이 damage 효과 핸들러를 등록한다.
        handler(eff, caster, t) 시그니처.
        tick_interval이 있는 damage는 tick()에서 주기적으로 호출되고,
        없는 damage는 _activate() 시점에 즉시 호출된다.
        """
        self._damage_handler = handler

    def register_buff_event_handler(self, handler):
        """타임라인이 버프 활성/만료 이벤트 콜백을 등록한다.
        handler(kind, name, caster, target, t, expires_at) 시그니처.
        kind: "activate" | "expire"
        """
        self._buff_event_handler = handler

    def register_instant_handler(self, stat: str, handler):
        """
        타임라인이 instant stat 핸들러를 등록한다.
        handler(eff, caster, t, val) 시그니처.
        val: fixed_value 또는 현재 스킬 레벨 수치 (없으면 None).
        """
        self._instant_handlers[stat] = handler

    def register_instant_event_handler(self, handler):
        """타임라인이 instant 발동 로그 콜백을 등록한다.
        handler(name, caster, target, t, stat, value) 시그니처.
        """
        self._instant_event_handler = handler

    def register_gauge_event_handler(self, handler):
        """타임라인이 버스트 게이지 가산 로그 콜백을 등록한다.
        handler(t, caster, source, amount, gauge) 시그니처.
        """
        self._gauge_event_handler = handler

    def _dispatch_instant(self, eff: dict, caster: str, t: float, from_tick: bool = False):
        """instant 효과를 핸들러로 라우팅하거나 내장 로직으로 처리.

        `from_tick=True`는 주기 instant의 매 틱 재발동 — 로그와 타이머 등록을 건너뛰고
        효과만 적용한다. 이 경로가 없으면 `_instant_handlers`에 등록된 stat(heal 등)만
        틱이 돌고, 내장 분기로 처리되는 stat(게이지 계열)은 조용히 무발동이 된다.
        """
        stat = eff.get("stat", "")
        char = self._char.get(caster, {})
        skill_lv = _get_skill_lv(char, eff)

        val: float | None
        if "fixed_value" in eff:
            val = float(eff["fixed_value"])
        elif "values" in eff:
            vals = eff["values"]
            val = float(vals.get(skill_lv, vals.get("10", 0.0)))
        else:
            val = None

        # ── instant 이벤트 로그 (처리 전 먼저 기록) ────────────────────────
        if not from_tick and self._instant_event_handler and eff.get("name"):
            raw_target = eff.get("target", "self")
            if raw_target == "self":
                _log_targets = [caster]
            elif raw_target in ("all", "squad"):
                _log_targets = list(self._char.keys())
            else:
                _log_targets = [raw_target] if raw_target in self._char else [caster]
            for _tgt in _log_targets:
                self._instant_event_handler(eff["name"], caster, _tgt, t, stat, val)

        # ── 주기 instant(tick_interval) 타이머 등록 ────────────────────────
        #
        # 첫 발동은 **등록 시점이 아니라 t + tick_interval**이다. 여기서 즉시 1회
        # 발동시키면 같은 프레임 뒤쪽 항목이 읽는 값이 이미 한 틱 진행돼 있어 조건이 깨진다
        # (아크레인저 블랙 — 배터리 드레인이 즉시 1% 깎으면 뒤따르는 `gauge_eq:배터리:100`
        #  긴급 충전 −50%가 발동하지 못해 변신이 10초가 아니라 20초가 된다).
        #
        # 주기 **대미지**의 `tick_start: "immediate"`(type 1)는 여기 적용하지 않는다 —
        # 유저 확인 결과 두 유형 구분은 주기 대미지에만 해당한다
        # (GAMEPLAY.md §효과 실행 순서).
        #
        # 등록은 stat 종류와 무관하게 여기서 한 번만 한다. 아래 내장 처리 분기들이
        # 각자 early return하므로 개별 분기에 두면 게이지 계열이 조용히 누락된다.
        tick_interval = eff.get("tick_interval")
        if tick_interval and not from_tick:
            duration = eff.get("duration")
            expires = math.inf if duration is None or duration == -1 else t + float(duration)
            self._instant_timers[id(eff)] = (caster, t + tick_interval, expires)
            return

        # ── 내장 처리 ──────────────────────────────────────────────────────

        # force_skill_use — `[스킬 N 강제 사용]`
        #
        # `target_skill` 슬롯의 **활성 판본**(그 캐릭터의 애장품 단계 기준) 효과를 전부
        # 즉시 1회 발동한다. 슬롯 단위인 이유는 원문이 효과가 아니라 스킬을 지목하기
        # 때문이고, 애장품 판본이 슬롯마다 갈리는 캐릭터에서는 "대상 슬롯 항목들의
        # timing에 battle_start를 얹는" 우회가 단계 조합과 어긋난다 (율리아 애장품 1단계
        # — 강제 사용은 슬롯2 판본에 적혀 있는데 대상 슬롯1은 아직 기본 판본).
        #
        # `every:Ns` 타이머는 건드리지 않는다. 강제 사용은 주기 격자를 리셋하는 게
        # 아니라 그 격자와 별개로 한 번 더 도는 것이다(사쿠라 : 블룸 인 서머의 기존
        # `["battle_start", "every:30.0s"]` 표현과 같은 동작).
        if stat == "force_skill_use":
            slot = eff.get("target_skill")
            if not slot:
                raise ValueError(f"[{caster}] force_skill_use에 target_skill이 없다: {eff.get('name')}")
            for other in self.char_effects(caster):
                if other.get("source") != slot or other is eff:
                    continue
                if self._condition_ok(other["trigger"].get("condition", []), caster, t, other):
                    self._activate(other, caster, t)
            return

        # feather_refresh — 소환체를 슬롯 단위로 (재)소환 (아인 니어 페더)
        #
        # 소환과 공격 쿨 초기화를 **한 항목이 함께** 한다. 둘을 나누면 재소환 프레임에
        # 옛 예약이 살아남아 볼리가 한 번 더 샌다. 만료로 수가 줄어드는 것은 예약된
        # next_t를 건드리지 않는다 — 유저 확인 규칙("버스트 재소환만 진행 중 쿨에 영향").
        if stat == "feather_refresh":
            fid = eff.get("feather_id")
            slots = eff.get("feather_slots") or []
            if not fid or not slots:
                return
            base = float(eff.get("feather_interval_base", 8.0))
            mult = float(eff.get("feather_interval_mult", 1.0))
            st = self.state.setdefault("feathers", {}).setdefault(caster, {})
            st[fid] = {
                "expiry": [math.inf if float(d) < 0 else t + float(d) for d in slots],
                "next_t": t + base * mult ** (len(slots) - 1),
                "base": base,
                "mult": mult,
            }
            return

        # skill_cooldown_reduce_pct — 스킬 재사용 시간 N% ▼ (즉시 1회)
        #
        # 대상 캐릭터가 시전자인 `every:Ns` 효과의 **남은 시간에만** (1 - N/100)을 곱한다.
        # `interval` 자체는 건드리지 않으므로 다음 주기는 원래 길이로 복귀한다 —
        # 원문에 `[N초 유지]`·`[N 중첩]`이 없는 % 쿨감은 버프가 아니라 그 순간의 잔여 쿨을
        # 깎는 1회성 사건이기 때문이다 (GAMEPLAY.md §값 산정). 주기 자체를 줄이는 쪽은
        # 버프인 `skill_cooldown_pct`가 담당한다.
        #
        # `target_effect`는 지원하지 않는다 — `skill_cooldown_pct`와 같은 범위(대상의
        # 모든 every:Ns)다. 센티 `보수공사`.
        if stat == "skill_cooldown_reduce_pct":
            if not val:
                return
            factor = max(0.0, 1.0 - float(val) / 100.0)
            target_chars = set(self._resolve_target(eff.get("target", "self"), caster))
            for _eff, _caster in self._effects:
                if _caster not in target_chars:
                    continue
                if not any(tm.startswith("every:") for tm in _eff["trigger"]["timing"]):
                    continue
                entry = self._next_fire.get(id(_eff))
                if entry is None:
                    continue
                next_t, interval = entry
                self._next_fire[id(_eff)] = (t + max(0.0, next_t - t) * factor, interval)
            return

        # buff_stack_add / buff_stack_remove
        if stat in ("buff_stack_add", "buff_stack_remove"):
            target_name = eff.get("target_effect", "")
            delta = int(val or 1) if stat == "buff_stack_add" else -int(val or 1)
            # notify는 _active를 다시 건드릴 수 있으므로 루프를 다 돈 뒤에 emit한다
            reached: list[tuple[str, int, str]] = []
            for ab in self._active:
                if ab.effect.get("name") != target_name:
                    continue
                affected = [c for c in (ab.target_chars or []) if c == caster]
                if not affected:
                    continue
                # stack_change_immune인 대상은 건너뜀
                if any(self._has_immune(c, "stack_change_immune") for c in affected):
                    continue
                max_s = ab.effect.get("max_stack", 1)
                cap = max_s if max_s != -1 else ab.stack + delta
                prev_stack = ab.stack
                ab.stack = max(1, min(ab.stack + delta, cap))
                # 스택 부여는 "버프를 다시 붙이는" 동작이라 지속시간도 갱신한다
                # (원문: `[스택명 : ...] [N 중첩] [M초 유지]`). _activate()와 같은 규칙.
                # duration -1/null(영구, expires_at == inf)은 갱신 대상 아님.
                if delta > 0 and ab.expires_at != math.inf:
                    duration = ab.effect.get("duration")
                    if duration is not None and duration > 0:
                        self._invalidate_buffs_cache()
                        ab.activated_at = t
                        ab.expires_at = t + duration
                if ab.stack != prev_stack:
                    self._invalidate_buffs_cache()
                    if delta > 0 and ab.effect.get("name"):
                        reached.append((ab.effect["name"], ab.stack, ab.caster))
                if self._buff_event_handler and ab.effect.get("name"):
                    new_val = self._get_value(ab.effect, ab)
                    for tgt in affected:
                        self._buff_event_handler(
                            "activate", ab.effect["name"], ab.caster, tgt,
                            t, ab.expires_at, new_val, ab.effect.get("stat"),
                        )
            # 스택이 새 값에 도달했으면 stack_reach 이벤트 발생 (_activate()와 동일)
            for name, stack, ab_caster in reached:
                self.notify(f"stack_reach:{name}:{stack}", t, ab_caster)
            return

        # buff_stack_init: 대상 버프를 N 스택으로 초기 생성 (없을 때만)
        if stat == "buff_stack_init":
            target_name = eff.get("target_effect", "")
            init_count = int(val or 1)
            already = any(
                ab.effect.get("name") == target_name and caster in (ab.target_chars or [])
                for ab in self._active
            )
            if not already and init_count > 0 and target_name:
                target_eff = next(
                    (e for e, ec in self._effects
                     if e.get("name") == target_name and ec == caster and e.get("type") == "buff"),
                    None,
                )
                if target_eff is not None:
                    raw_target = target_eff.get("target", "self")
                    lazy = isinstance(raw_target, str) and raw_target.startswith(_LAZY_RESOLVE_PREFIXES)
                    targets = None if lazy else self._resolve_target(raw_target, caster)
                    max_s = target_eff.get("max_stack", 1)
                    init_stack = min(init_count, max_s if max_s != -1 else init_count)
                    duration = target_eff.get("duration")
                    expires = math.inf if duration is None or duration == -1 else t + duration
                    self._invalidate_buffs_cache()
                    ab_new = ActiveBuff(
                        effect=target_eff,
                        caster=caster,
                        target_chars=targets,
                        activated_at=t,
                        expires_at=expires,
                        stack=init_stack,
                        has_runtime_conditions=_has_runtime_cond(
                            target_eff["trigger"].get("condition", []), expires,
                            target_eff.get("duration_bullets", -1)),
                        scaling_stack=self._capture_scaling_stack(target_eff, caster),
                    )
                    self._active.append(ab_new)
                    if self._buff_event_handler and target_name and targets:
                        new_val = self._get_value(target_eff, ab_new, caster)
                        for tgt in targets:
                            self._buff_event_handler(
                                "activate", target_name, caster, tgt,
                                t, expires, new_val, target_eff.get("stat"),
                            )
            return

        # debuff_stack_add / debuff_stack_remove
        if stat in ("debuff_stack_add", "debuff_stack_remove"):
            target_name = eff.get("target_effect", "")
            # scaling:stack_count + scaling_ref → 참조 게이지/스택 값을 delta로 사용
            raw_delta = int(val or 1)
            if eff.get("scaling") == "stack_count":
                ref_val = self.ref_count(caster, eff.get("scaling_ref", ""))
                if ref_val is not None:
                    raw_delta = ref_val
            delta = raw_delta if stat == "debuff_stack_add" else -raw_delta
            target_chars = self._resolve_target(eff.get("target", "self"), caster)
            for ab in self._active:
                if target_name:
                    # 특정 버프명 지정: 이름 일치 여부로 필터
                    if ab.effect.get("name") != target_name:
                        continue
                else:
                    # target_effect 미지정: 중첩 가능한(max_stack > 1) harmful 버프 전체에 적용
                    if ab.effect.get("polarity") != "harmful":
                        continue
                    if ab.effect.get("max_stack", 1) <= 1:
                        continue
                affected = [tc for tc in target_chars if tc in (ab.target_chars or [])]
                if not affected:
                    continue
                # stack_change_immune인 대상은 건너뜀
                affected = [c for c in affected if not self._has_immune(c, "stack_change_immune")]
                if not affected:
                    continue
                max_s = ab.effect.get("max_stack", 1)
                cap = max_s if max_s != -1 else ab.stack + delta
                if target_name:
                    ab.stack = max(0, min(ab.stack + delta, cap))
                else:
                    # 중첩 가능 해로운 효과 범용 감소: 완전 제거 불가, 최소 1스택 유지
                    ab.stack = max(1, min(ab.stack + delta, cap))
                # 스택 변화를 buff_event_handler에 알려 UI 타임라인 갱신
                if self._buff_event_handler and ab.effect.get("name"):
                    new_val = self._get_value(ab.effect, ab)
                    for tgt in affected:
                        self._buff_event_handler(
                            "activate", ab.effect["name"], ab.caster, tgt,
                            t, ab.expires_at, new_val, ab.effect.get("stat"),
                        )
            return

        # debuff_cleanse: 대상의 harmful 버프 제거 (harmful_irremovable은 제거 불가)
        #
        # **개수는 대상 니케 1인당이다** — 원문 `[해로운 효과 해제 N개]`의 N을 `fixed_value`
        # (레벨별이면 `values`)에 싣는다(2026-09-15 유저 확정). 보스 디버프가 니케마다 따로
        # 붙으므로(CALCULATOR.md §보스 디버프) 「1개」를 스쿼드 전체 1개로 읽으면 5인 스쿼드에서
        # 한 명만 풀린다. 제거 우선순위는 원문에 없어 **부여가 이른 것부터**로 정했다 —
        # `_active`가 부여 순서를 유지하는 유일한 결정론적 순서다.
        # N을 안 적은 항목(구 표기)은 종전대로 전부 지운다.
        #
        # 한 버프가 여러 니케에게 걸려 있으면(아군 전체 디메리트) **해제 대상 니케만** 빼고
        # 남은 대상이 없을 때 버프 자체가 사라진다. 통째로 지우면 해제 대상이 아닌 아군의
        # 디버프까지 같이 풀린다.
        if stat == "debuff_cleanse":
            target_chars = self._resolve_target(eff.get("target", "self"), caster)
            if not target_chars:
                return
            limit = int(val) if val is not None and val > 0 else None
            # 키는 `uid`다 — `id(ab)`는 GC 뒤 재사용돼 엉뚱한 버프를 가리킬 수 있다.
            strip: dict[int, set[str]] = {}
            for tc in target_chars:
                taken = 0
                for ab in self._active:
                    if ab.effect.get("polarity") != "harmful":
                        continue
                    if tc not in (ab.target_chars or []):
                        continue
                    strip.setdefault(ab.uid, set()).add(tc)
                    taken += 1
                    if limit is not None and taken >= limit:
                        break
            if not strip:
                return
            self._invalidate_buffs_cache()
            kept = []
            for ab in self._active:
                gone = strip.get(ab.uid)
                if gone:
                    ab.target_chars = [n for n in (ab.target_chars or []) if n not in gone]
                    if not ab.target_chars:
                        continue
                kept.append(ab)
            self._active = kept
            return

        # `remove_scope: "target"` — `target`으로 풀린 캐릭터에게서만 지운다(PARSING.md §2).
        # 여럿에게 걸린 인스턴스는 그 캐릭터만 빠지고, 남은 대상이 없을 때 인스턴스가 사라진다
        # (`debuff_cleanse`와 같은 모양). 같은 이름의 상태를 캐릭터마다 따로 들고 있는데 한쪽만
        # 바뀌어야 할 때 쓴다 — 아래 전역 제거로는 짝의 모드까지 지워 동기화가 끊긴다
        # (길티 : 마이티 바니 · 신 : 스위프트 바니 `바니 모드`).
        if stat == "remove_named_buff" and eff.get("remove_scope") == "target":
            target_name = eff.get("target_effect", "")
            scope = set(self._resolve_target(eff.get("target", "self"), caster) or [])
            hit = [ab for ab in self._by_name(target_name)
                   if scope & set(ab.target_chars or [])]
            if not hit:
                return
            ended = []
            for ab in hit:
                gone = [c for c in ab.target_chars if c in scope]
                if self._buff_event_handler:
                    for tgt in gone:
                        self._buff_event_handler("expire", target_name, ab.caster, tgt, t, t)
                ab.target_chars = [c for c in ab.target_chars if c not in scope]
                if not ab.target_chars:
                    ended.append(ab)
            if ended:
                ended_uids = {ab.uid for ab in ended}
                self._active = [ab for ab in self._active if ab.uid not in ended_uids]
                live = {id(ab.effect) for ab in self._active}
                for ab in ended:
                    if id(ab.effect) not in live:
                        self._dot_timers.pop(id(ab.effect), None)
                        self._instant_timers.pop(id(ab.effect), None)
            self._invalidate_buffs_cache()   # `target_chars`만 줄어든 인스턴스도 집계가 바뀐다
            # 전역 제거와 같이 순회가 끝난 뒤 emit한다 — 재진입으로 `_active`가 바뀐다.
            for ab in ended:
                self.notify(f"event:state_end:{target_name}", t, ab.caster)
            return

        # remove_named_buff: 특정 name의 버프 즉시 제거 (_active + _dot_timers 모두)
        if stat == "remove_named_buff":
            target_name = eff.get("target_effect", "")
            to_remove = [ab for ab in self._active if ab.effect.get("name") == target_name]
            removed_ids = {id(ab.effect) for ab in to_remove}
            self._invalidate_buffs_cache()
            self._active = [
                ab for ab in self._active
                if ab.effect.get("name") != target_name
            ]
            for eid in removed_ids:
                self._dot_timers.pop(eid, None)
                self._instant_timers.pop(eid, None)
            if self._buff_event_handler:
                for ab in to_remove:
                    if ab.effect.get("name"):
                        for tgt in (ab.target_chars or []):
                            self._buff_event_handler("expire", ab.effect["name"], ab.caster, tgt, t, t)
            # 이름 있는 버프가 제거되면 그 상태는 끝난 것이다 — 만료 경로(tick)와 동일하게
            # state_end를 발생시켜야 상태에 종속된 효과를 풀 수 있다.
            # notify는 순회가 끝난 뒤 emit — 순회 중 emit하면 재진입으로 `_active`가 바뀐다.
            # (아크레인저 블랙 — 배터리 0에 `변신`이 제거되면 코레더 DoT가 함께 풀려야 한다)
            for _ab in to_remove:
                _n = _ab.effect.get("name")
                if _n:
                    self.notify(f"event:state_end:{_n}", t, _ab.caster)
            return

        # trigger_count_reduce: target_effect 버프의 스택을 fixed_value만큼 감소, 0이 되면 제거
        if stat == "trigger_count_reduce":
            target_name = eff.get("target_effect", "")
            reduce = int(val or 1)
            to_remove = []
            for ab in self._active:
                if ab.effect.get("name") != target_name:
                    continue
                if caster not in (ab.target_chars or []):
                    continue
                ab.stack = max(0, ab.stack - reduce)
                if ab.stack <= 0:
                    to_remove.append(ab.uid)
            if to_remove:
                self._invalidate_buffs_cache()
            self._active = [ab for ab in self._active if ab.uid not in to_remove]
            return

        # gauge_charge / gauge_consume / gauge_consume_as_ammo
        if stat in ("gauge_charge", "gauge_consume", "gauge_consume_as_ammo"):
            gauge_id = eff.get("gauge_id", "")
            if not gauge_id or val is None:
                return
            gauges = self.state.setdefault("gauges", {}).setdefault(caster, {})
            gauge_max_key = f"_gauge_max:{gauge_id}"

            # gauge_max가 처음 선언된 항목에서 기본 최대값 등록
            if "gauge_max" in eff:
                self.state["gauges"][caster][gauge_max_key] = float(eff["gauge_max"])

            current = gauges.get(gauge_id, 0.0)
            if stat == "gauge_charge":
                new_val = current + val
                base_cap = gauges.get(gauge_max_key, math.inf)
                # 활성 gauge_max_add buff 합산
                add_cap = sum(
                    ab.effect.get("fixed_value", 0.0)
                    for ab in self._active
                    if ab.caster == caster
                    and ab.effect.get("stat") == "gauge_max_add"
                    and ab.effect.get("gauge_id") == gauge_id
                )
                cap = base_cap + add_cap
                gauges[gauge_id] = min(new_val, cap)
                self._emit_every_stack(gauge_id, current, gauges[gauge_id], caster, t)
            else:  # gauge_consume / gauge_consume_as_ammo
                if val == -1.0:  # fixed_value: -1 = 전체 소모
                    consumed = current
                    gauges[gauge_id] = 0.0
                else:
                    consumed = min(val, current)
                    gauges[gauge_id] = max(0.0, current - val)
                # gauge_consume_as_ammo: 실제 소모량만큼 squad_ammo_consume 이벤트 발생
                if stat == "gauge_consume_as_ammo" and consumed > 0:
                    for _ in range(int(consumed)):
                        self.notify("squad_ammo_consume", t, caster)
            return

        # squad_ammo_consume_as: "탄환 소모 N발" 표기 — 실제 장탄은 1발만 줄고,
        # 아군 탄 소비 총합 카운터에만 N발로 계상된다.
        # 발사 자체가 이미 1발을 계상했으므로 여기서는 N-1발만 추가한다.
        if stat == "squad_ammo_consume_as":
            extra = int(val or 0) - 1
            for _ in range(max(0, extra)):
                self.notify("squad_ammo_consume", t, caster)
            return

        # named_buff_duration_extend: target_effect 이름의 활성 버프 _end_t += fixed_value
        # "퍼포먼스"를 지정하면 "퍼포먼스", "퍼포먼스 2", "퍼포먼스 3" 등 동일 스킬 부속 버프 모두 연장
        if stat == "named_buff_duration_extend":
            target_name = eff.get("target_effect", "")
            if target_name and val is not None:
                extend_targets = set(self._resolve_target(eff.get("target", "self"), caster))
                prefix = target_name + " "
                for ab in self._active:
                    ab_name = ab.effect.get("name", "")
                    if ab_name != target_name and not ab_name.startswith(prefix):
                        continue
                    if ab.expires_at == math.inf:
                        continue
                    affected = extend_targets.intersection(set(ab.target_chars or []))
                    if not affected:
                        continue
                    ab.expires_at += val
                    # DoT는 틱 스케줄이 _dot_timers에 별도로 복사돼 있다. ActiveBuff만
                    # 늘리면 표시만 길어지고 실제 틱은 원래 시각에서 끊긴다.
                    # (사쿠라 : 블룸 인 서머 `피어나다 3` — 적측 `벚꽃잎` 유지 시간 ▲)
                    dot = self._dot_timers.get(id(ab.effect))
                    if dot is not None:
                        d_caster, d_next, _ = dot
                        self._dot_timers[id(ab.effect)] = (d_caster, d_next, ab.expires_at)
                    if self._buff_event_handler and ab.effect.get("name"):
                        new_val = self._get_value(ab.effect, ab)
                        for tgt in affected:
                            self._buff_event_handler(
                                "activate", ab.effect["name"], ab.caster, tgt,
                                t, ab.expires_at, new_val, ab.effect.get("stat"),
                            )
            return

        # ── 외부 핸들러 ────────────────────────────────────────────────────
        handler = self._instant_handlers.get(stat)
        if handler:
            handler(eff, caster, t, val)

    # ── 이벤트 통지 ───────────────────────────────────────────────────────

    def notify(self, event: str, t: float, caster: str, **ctx):
        """
        타임라인이 이벤트 발생 시 호출.

        Parameters
        ----------
        event : str
            "battle_start", "full_burst_start", "hit_count", "burst_cast",
            "full_charge_hit", "enemy_death", ... (timing 값과 동일 형식)
        t : float  현재 시각(초)
        caster : str  이벤트 주체 캐릭터명
        ctx : 추가 컨텍스트
            count (int): 누적 횟수 (hit_count, burst_cast_count 등)
            hit_crit (bool): 트리거를 발생시킨 히트의 크리 여부 (`trigger_hit_crit` 조건용)
            core_frac (float): 트리거를 발생시킨 탄의 코어 확률 (`not_core` 조건용)
            stack_value (int): 넘은 배수 경계 (`every_stack:이름:N` timing용)
            heal_source (str): 회복을 **건** 캐릭터 (`event:heal_received` 전용,
                `not_self_caused_heal` 조건용). 받는 쪽은 `caster` 인자다
            dealt (float): 트리거한 탄이 준 대미지 (`full_charge_hit` 전용,
                `dealt_fixed_damage`가 `notify_ctx()`로 읽는다)

        ctx는 `_notify_ctx`에 실어 `_condition_ok`가 읽는다. 발동 중 다시 notify가
        걸리는 경로가 있으므로(damage 핸들러 → named damage 명중 → notify) 반드시
        이전 ctx를 되돌린다 — 안 되돌리면 바깥 트리거의 조건이 안쪽 히트의 결과를 본다.
        """
        prev_ctx = self._notify_ctx
        self._notify_ctx = ctx
        try:
            self._notify(event, t, caster)
        finally:
            self._notify_ctx = prev_ctx

    def notify_ctx(self, key: str, default: Any = None) -> Any:
        """지금 처리 중인 notify의 컨텍스트 값. 타임라인의 damage 핸들러가 **트리거한 히트**의
        정보를 읽는 창구다 — `dealt`(그 탄이 준 대미지, `dealt_fixed_damage`)."""
        return self._notify_ctx.get(key, default)

    def _notify(self, event: str, t: float, caster: str):
        self._cur_t = t
        down = self.state.get("down")
        # squad_ammo_consume: 스쿼드 전체 탄환 소비 카운터 — caster와 무관하게 합산, 모든 스쿼드원 효과 순회
        if event == "squad_ammo_consume":
            team_counts = self._event_counts.setdefault("__squad__", {})
            team_counts[event] = team_counts.get(event, 0) + 1
            current_count = team_counts[event]
            for eff, eff_caster in self._squad_notify_index.get(event, []):
                if down and eff_caster in down:
                    continue
                for timing in eff["trigger"]["timing"]:
                    if self._timing_match(timing, event, current_count, t, eff, eff_caster):
                        if self._condition_ok(eff["trigger"].get("condition", []), eff_caster, t, eff):
                            self._activate(eff, eff_caster, t)
                        break
            return

        # **전투불능인 니케의 스킬은 발동하지 않는다.** 예외는 자기 전투불능 이벤트 하나 —
        # 「자신이 전투불능 시」 효과는 쓰러진 순간에 나가야 한다(미하라 : 본딩 체인 `타이트 2`).
        if down and caster in down and event != "event:self_down":
            return

        counts = self._event_counts.setdefault(caster, {})
        counts[event] = counts.get(event, 0) + 1
        current_count = counts[event]

        caster_idx = self._notify_index.get(caster, {})
        candidates = caster_idx.get(event, [])

        for eff, eff_caster in candidates:
            for timing in eff["trigger"]["timing"]:
                if self._timing_match(timing, event, current_count, t, eff, caster):
                    is_passive = (timing == "passive")
                    if is_passive:
                        conditions = eff["trigger"].get("condition", [])
                        cond_met = not conditions or self._condition_ok(conditions, caster, t, eff)
                        if _is_cond_finite_passive(eff):
                            # 유한 지속은 조건이 거짓이면 아예 걸지 않는다 — 걸어 두면
                            # 런타임 재평가 대상이 아니라서(`_has_runtime_cond`) 조건이
                            # 거짓인 동안에도 수치가 그대로 먹는다. 조건이 참이 되는 시점은
                            # tick()의 조건부 유한 passive 블록이 잡는다.
                            if cond_met:
                                self._activate(eff, caster, t)
                        else:
                            self._activate(eff, caster, t, suppress_event=not cond_met)
                    elif self._condition_ok(eff["trigger"].get("condition", []), caster, t, eff):
                        self._activate(eff, caster, t)
                    break

    def notify_team_hit(self, event: str, t: float, attacker: str):
        """part_hit / body_hit 스쿼드 브로드캐스트.

        어느 아군(attacker)이 hit하더라도 모든 캐릭터의 part_hit_count / body_hit_count
        효과를 체크한다. _activate() 시 caster=attacker 로 호출하므로
        target:"self"가 발사한 아군(attacker)을 가리킨다.
        조건 평가(condition)는 효과 소유자(eff_caster) 기준으로 수행한다.
        """
        team_counts = self._event_counts.setdefault("__squad__", {})
        team_counts[event] = team_counts.get(event, 0) + 1
        current_count = team_counts[event]
        down = self.state.get("down")
        for eff, eff_caster in self._squad_hit_index.get(event, []):
            if down and eff_caster in down:
                continue
            for timing in eff["trigger"]["timing"]:
                if self._timing_match(timing, event, current_count, t, eff, eff_caster):
                    if self._condition_ok(eff["trigger"].get("condition", []), eff_caster, t, eff):
                        self._activate(eff, attacker, t)
                    break

    def _emit_every_stack(self, ref: str, old: float, new: float, caster: str, t: float) -> None:
        """게이지가 old → new로 오르며 넘은 배수 경계마다 `every_stack:ref` 1회.

        경계값을 ctx `stack_value`로 실어 보내고, 어느 N의 배수인지는 `_timing_match`가
        가른다 — N이 서로 다른 효과가 같은 게이지를 봐도 이벤트 하나로 끝난다.
        한 번에 여러 경계를 넘으면(큰 충전량) 넘은 경계마다 따로 쏜다.
        cap에 걸려 값이 안 오르면 경계를 넘지 않으므로 발동하지 않는다
        (길로틴 : 윈터 슬레이어 — 경험치 100 이후 레벨 업이 멈추는 근거).
        """
        steps = self._every_stack_steps.get((caster, ref))
        if not steps or new <= old:
            return
        bounds = sorted({
            k * n
            for n in steps
            for k in range(math.floor(old / n) + 1, math.floor(new / n) + 1)
        })
        for b in bounds:
            self.notify(f"every_stack:{ref}", t, caster, stack_value=b)

    def _apply_trigger_count_reduce(self, n: int, eff: dict, caster: str, t: float) -> int:
        """활성화된 trigger_count_reduce 버프가 eff를 대상으로 하면 n을 감소시킨다. 최솟값 1.

        target_effect는 같은 timing 그룹의 대표 effect name을 가리킨다.
        eff 자신의 name이 일치하거나, eff와 같은 timing을 공유하는 effect 중
        target_effect name을 가진 것이 있으면 적용한다.
        """
        if not caster:
            return n
        eff_timings = set(eff.get("trigger", {}).get("timing", []))
        reduce = 0.0
        for ab in self._active:
            if ab.caster != caster:
                continue
            if ab.effect.get("stat") != "trigger_count_reduce":
                continue
            if not (ab.expires_at == math.inf or ab.expires_at > t):
                continue
            target_name = ab.effect.get("target_effect", "")
            if not target_name:
                continue
            # eff 자신이 target이거나, 같은 timing 그룹의 다른 effect가 target인 경우
            if eff.get("name") == target_name:
                reduce += ab.effect.get("fixed_value", 0.0)
            else:
                for reg_eff, reg_caster in self._effects:
                    if reg_caster != caster:
                        continue
                    if reg_eff.get("name") != target_name:
                        continue
                    reg_timings = set(reg_eff.get("trigger", {}).get("timing", []))
                    if reg_timings & eff_timings:
                        reduce += ab.effect.get("fixed_value", 0.0)
                        break
        return max(1, n - int(reduce))

    def _resolve_count_placeholder(self, raw: str, eff: dict, caster: str) -> str:
        """timing의 `{0}` 자리표시자를 `trigger_values`의 현재 스킬 레벨 값으로 바꾼다.

        `hit_count:{0}` · `on_attack_count:{0}` 공용 — 트리거 횟수가 레벨마다 다른
        슬롯용이다(크라운 `로얄 에타이어`, 토브 애장품 `급조 탄환`).
        """
        if not (raw.startswith("{") and raw.endswith("}")):
            return raw
        tv = eff.get("trigger_values", {})
        if not tv:
            return raw
        char = self._char.get(caster, {})
        skill_lv = _get_skill_lv(char, eff)
        return str(tv.get(skill_lv, tv.get("10", raw)))

    def _timing_match(
        self, timing: str, event: str, count: int, t: float, eff: dict, caster: str = ""
    ) -> bool:
        """timing 문자열과 현재 이벤트가 매칭되는지 확인."""

        # passive: battle_start에 한 번 등록 (영구 지속)
        if timing == "passive":
            return event == "battle_start"

        # on_attack: auto(_fire)와 charge(_tick_charge) 양쪽에서 직접 notify
        if timing == "on_attack" and event == "on_attack":
            return True

        # battle_start, full_burst_start, full_burst_end, ...
        if timing == event:
            return True

        # every:Ns: 내부 타이머로 관리 (tick에서 처리), notify에서는 무시
        if timing.startswith("every:"):
            return False

        # burst_cast_count:N — N번째 이후 버스트마다 누적 발동 (count >= N)
        if timing.startswith("burst_cast_count:") and event == "burst_cast":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count >= int(raw)

        # full_burst_start_count:N — N번째 이상 매번 발동 (>= N)
        if timing.startswith("full_burst_start_count:") and event == "full_burst_start":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count >= int(raw)

        # full_burst_start_exact:N — 정확히 N번째만 발동 (== N)
        if timing.startswith("full_burst_start_exact:") and event == "full_burst_start":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count == int(raw)

        # full_burst_end_count:N — N번째 이상 매번 발동 (>= N)
        if timing.startswith("full_burst_end_count:") and event == "full_burst_end":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count >= int(raw)

        # on_attack_count:N — `일반 공격 N회 공격 시`. 발사 카운터라 총구·펠릿과 무관하게
        # 발사 1회당 1씩 오른다. 짝인 `hit_count:N`(명중)은 탄 단위라 총구만큼 오른다.
        # `{0}` 자리표시자 규약은 `hit_count:{0}`과 같다(크라운·토브 애장품).
        if timing.startswith("on_attack_count:") and event == "on_attack":
            raw = self._resolve_count_placeholder(timing.split(":")[1], eff, caster)
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

        # full_charge_fire_count:N / full_charge_hit_count:N
        # (구 표기 `full_charge_count:N`은 발사로 읽는다 — _timing_to_index_key 참고)
        # trigger_count_reduce 버프로 N 감소 가능
        _fc_evt = {"full_charge_fire_count:": "full_charge_fire",
                   "full_charge_count:": "full_charge_fire",
                   "full_charge_hit_count:": "full_charge_hit"}
        for _pref, _evt in _fc_evt.items():
            if timing.startswith(_pref) and event == _evt:
                raw = timing.split(":")[1]
                if not raw.lstrip("-").isdigit(): return False
                n = int(raw)
                n = self._apply_trigger_count_reduce(n, eff, caster, t)
                return count % n == 0

        # non_full_charge_fire_count:N — 논차지(톡톡이) 발사 N회마다.
        # `full_charge_fire_count:N`과 같은 규약이고 세는 이벤트만 여집합이다.
        if (timing.startswith("non_full_charge_fire_count:")
                and event == "non_full_charge_fire"):
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            n = self._apply_trigger_count_reduce(int(raw), eff, caster, t)
            return count % n == 0

        # charge_hold_count:N:M — `charge_hold:N` 판정이 M회 누적될 때마다.
        # 판정은 한 차지에 1회뿐이라(`CharState._charge_hold_fired`) M회를 채우려면
        # 홀드-발사를 M번 반복해야 한다 — 사이클당 1회인 홀드 정책만으로는 못 닿는다.
        if timing.startswith("charge_hold_count:") and event.startswith("charge_hold:"):
            parts = timing.split(":")
            if len(parts) != 3: return False
            if event != f"charge_hold:{parts[1]}": return False
            raw_m = parts[2]
            if not raw_m.isdigit() or int(raw_m) <= 0: return False
            return count % int(raw_m) == 0

        # hit_count:[스킬명]:N — named damage effect 명중 N회마다
        if timing.startswith("hit_count:") and event.startswith("hit_count:") and event != "hit_count":
            parts = timing.split(":", 2)
            if len(parts) == 3 and f"hit_count:{parts[1]}" == event:
                raw = parts[2]
                if not raw.lstrip("-").isdigit(): return False
                n = int(raw)
                n = self._apply_trigger_count_reduce(n, eff, caster, t)
                return count % n == 0
            return False

        # hit_count:N  (trigger_count_reduce 버프로 N 감소 가능)
        # hit_count:{0} 형태면 trigger_values에서 현재 스킬 레벨 기준 N을 꺼냄
        if timing.startswith("hit_count:") and event == "hit_count":
            raw = self._resolve_count_placeholder(timing.split(":")[1], eff, caster)
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

        # burst_enter:N
        if timing.startswith("burst_enter:") and event.startswith("burst_enter:"):
            return timing == event

        # burst_enter_count:N:M — N단계 돌입이 M번째에 도달한 뒤 매번 (count >= M).
        # `burst_cast_count:M`과 같은 규약이고, 카운터는 수신자별 `burst_enter:N` 누적이다 —
        # 돌입은 스쿼드 판정이라 본인이 버스트를 안 쓴 사이클도 센다(GAMEPLAY §timing)
        if timing.startswith("burst_enter_count:") and event.startswith("burst_enter:"):
            _, stage, raw = timing.split(":")
            if event != f"burst_enter:{stage}" or not raw.isdigit():
                return False
            return count >= int(raw)

        # squad_burst_cast:N
        if timing.startswith("squad_burst_cast:") and event.startswith("squad_burst_cast:"):
            return timing == event

        # core_hit:N · core_hit_count:N  (trigger_count_reduce 버프로 N 감소 가능)
        # `_timing_to_index_key()`가 둘 다 "core_hit"로 접는다. 파싱 정본 표기는
        # `core_hit_count:N`이므로(`docs/PARSING.md`) 여기서 둘 다 받아야 한다 —
        # 한쪽만 있으면 그 표기를 쓰는 효과가 조용히 영구 미발동이 된다
        if (timing.startswith("core_hit:") or timing.startswith("core_hit_count:")) and event == "core_hit":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

        # crit_hit_count:N  (trigger_count_reduce 버프로 N 감소 가능)
        if timing.startswith("crit_hit_count:") and event == "crit_hit":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

        # received_hit_count:N · received_hit:N — 피격 N회마다. 파싱 정본 표기는
        # `received_hit_count:N`인데(`docs/PARSING.md`) 종전에는 짧은 표기만 받아, 피격 모델이
        # 들어와도 그 표기를 쓰는 효과(홍련 `검신합일` 등 10건)가 영구 미발동일 뻔했다.
        if ((timing.startswith("received_hit:") or timing.startswith("received_hit_count:"))
                and event == "received_hit"):
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count % int(raw) == 0

        # pellet_hit_count:N 또는 pellet_hit:N  (trigger_count_reduce 버프로 N 감소 가능)
        if (timing.startswith("pellet_hit_count:") or timing.startswith("pellet_hit:")) and event == "pellet_hit":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

        # hp_below:N → 타임라인이 체력 변화 시 "hp_below:N" 이벤트 발생
        if timing.startswith("hp_below:") and event.startswith("hp_below:"):
            return timing == event

        # hp_below_count:threshold:N → "hp_below:threshold" 이벤트의 N번째 발생 시
        if timing.startswith("hp_below_count:") and event.startswith("hp_below:"):
            parts = timing.split(":")
            if len(parts) == 3 and event == f"hp_below:{parts[1]}":
                return count == int(parts[2])

        # stack_reach:버프명:N — 해당 버프 스택이 N에 도달하는 순간 발동
        if timing.startswith("stack_reach:") and event.startswith("stack_reach:"):
            return timing == event

        # every_stack:이름:N — 게이지가 N의 배수 경계를 위로 넘을 때마다 (_emit_every_stack)
        if timing.startswith("every_stack:") and event.startswith("every_stack:"):
            ref_key, raw = timing.rsplit(":", 1)
            if ref_key != event or not raw.isdigit() or int(raw) <= 0:
                return False
            b = self._notify_ctx.get("stack_value")
            return b is not None and b % int(raw) == 0

        # event:xxx
        if timing.startswith("event:") and event == timing:
            return True

        # weapon_hit:name
        if timing.startswith("weapon_hit:") and event.startswith("weapon_hit:"):
            return timing == event

        # charge_hold:N
        if timing.startswith("charge_hold:") and event.startswith("charge_hold:"):
            return timing == event

        # multi_hit:N
        if timing.startswith("multi_hit:") and event.startswith("multi_hit:"):
            return timing == event

        # squad_ammo_consume:N — 스쿼드 전체 탄환 소비 누적 N발마다 발동
        if timing.startswith("squad_ammo_consume:") and event == "squad_ammo_consume":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count % int(raw) == 0

        # part_hit_count:N — 스쿼드 내 아군이 파츠 명중할 때마다 (squad_part_hit 이벤트)
        if timing.startswith("part_hit_count:") and event == "squad_part_hit":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count % int(raw) == 0

        # body_hit_count:N — 스쿼드 내 아군이 본체 명중할 때마다 (squad_body_hit 이벤트)
        if timing.startswith("body_hit_count:") and event == "squad_body_hit":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count % int(raw) == 0

        return False

    def _condition_ok(self, conditions: list, caster: str, t: float, eff: dict | None = None) -> bool:
        """발동 시점 조건 평가. False이면 발동 안 함."""
        # burst_casted 계열 조건 평가 기준 캐릭터:
        # effect의 target이 단일 캐릭터 이름(스쿼드원)이면 그 캐릭터 기준, 아니면 caster 기준
        raw_target = eff.get("target", "") if eff else ""
        burst_check_char = (
            raw_target if isinstance(raw_target, str) and raw_target in self.squad_names
            else caster
        )
        for cond in conditions:
            if cond == "during_charge":
                if not self.state.get("charging", {}).get(caster):
                    return False
            elif cond == "during_full_burst":
                if not self.state.get("full_burst"):
                    return False
            elif cond == "not_during_full_burst":
                if self.state.get("full_burst"):
                    return False
            elif cond == "not_self_caused_heal":
                # 「자신이 사용한 회복 효과가 아니라면」 — `event:heal_received`와 짝으로만 쓴다.
                # 회복 핸들러가 ctx에 실어 준 `heal_source`(회복을 **건** 쪽)가 이 효과의 주인과
                # 같으면 막는다. 이 조건이 없으면 자기 회복이 자기 트리거를 켜므로
                # 「자기 지속 회복 → 자기 스택 누적」 순환이 성립한다 (백학 `서약 위반 증거`).
                # ctx에 `heal_source`가 없는 경로는 출처를 알 수 없으므로 종전대로 통과시킨다.
                src = self._notify_ctx.get("heal_source")
                if src is not None and src == caster:
                    return False
            elif cond == "trigger_hit_crit":
                # 트리거를 발생시킨 그 히트가 크리티컬이었는가 — notify의 ctx로 전달된다.
                # 확률 근사가 아니라 실제 롤 결과를 읽는다 (율리아 `마르카토 2`).
                if not self._notify_ctx.get("hit_crit"):
                    return False
            elif cond == "not_core":
                # 트리거를 일으킨 그 탄이 코어가 아니었는가 — timeline이 명중 notify에
                # `core_frac`(그 탄의 코어 확률)을 싣는다. 실리지 않은 경로는 코어 판정이
                # 없는 명중이라 비코어로 본다. 기대값 모드에서는 `prob:`와 같은 규약으로
                # (1 − core_frac)을 누적해 1.0을 넘길 때 발동한다 (길로틴 : 윈터 슬레이어 `경험치 2`).
                p = 1.0 - float(self._notify_ctx.get("core_frac", 0.0))
                if self.state.get("rng_expected"):
                    acc = self.state.setdefault("rng_acc", {})
                    key = ("not_core", id(eff), caster)
                    acc[key] = acc.get(key, 0.0) + p
                    if acc[key] < 1.0:
                        return False
                    acc[key] -= 1.0
                elif p <= 0.0 or (p < 1.0 and random.random() >= p):
                    return False
            elif cond == "burst_casted":
                if not self.state.get("burst_casted", {}).get(burst_check_char):
                    return False
            elif cond == "burst_not_casted":
                if self.state.get("burst_casted", {}).get(burst_check_char):
                    return False
            elif cond.startswith("prob:"):
                # prob:{0} 형태면 trigger_values에서 현재 스킬 레벨 기준 확률을 꺼낸다
                # (timing의 hit_count:{0}과 같은 규약 — 토브 `급조 탄환` 기본 판본)
                raw = cond.split(":", 1)[1]
                if raw.startswith("{") and raw.endswith("}"):
                    tv = (eff or {}).get("trigger_values", {})
                    if not tv:
                        return False
                    char = self._char.get(caster, {})
                    raw = str(tv.get(_get_skill_lv(char, eff), tv.get("10")))
                p = float(raw) / 100
                # 기대값 모드에서는 난수를 굴리지 않고 확률을 (효과, 캐스터)별로 누적해
                # 1.0을 넘길 때마다 발동시킨다 — 크리·코어히트의 `_notify_frac`과 같은
                # 규약이다(기대 발동 횟수는 같고 위상만 규칙적으로 퍼진다). 이게 없으면
                # `prob:` 보유 캐릭터(토브·슈가·홍련)만 기대값 모드에서 시드에 의존한다.
                if self.state.get("rng_expected"):
                    acc = self.state.setdefault("rng_acc", {})
                    key = ("prob", id(eff), caster)
                    acc[key] = acc.get(key, 0.0) + p
                    if acc[key] < 1.0:
                        return False
                    acc[key] -= 1.0
                elif random.random() >= p:
                    return False
            elif cond in ("target_is_boss", "target_is_add"):
                # 「대상이 타겟이라면」·「대상이 타겟이 아닌 랩쳐라면」 — 트리거를 낸 한 발의 대상은 시전자가
                # 겨눈 적이다(boss_pattern.py §조준). 쫄몹이 없으면 늘 보스(타겟)라 앞은 참, 뒤는 거짓이다.
                # 발동 시점 게이트 — `_RUNTIME_COND_PREFIXES`에 넣지 않는다(리버렐리오 `격류`는 [지속]이고
                # 반대 분기의 해제 항목이 끈다)
                on_add = self._resolve_enemies("target", caster)[0] != "__enemy__"
                if on_add != (cond == "target_is_add"):
                    return False
            elif cond == "target_stunned":
                # 기절은 이름 있는 상태가 아니므로 target_state:로 잡지 않는다.
                # 누가 걸었든 stat이 stun이면 참 (프리바티 `LD 어설트 3` 기본 판본)
                if not self.is_stunned("__enemy__"):
                    return False
            elif cond == "self_stun_immune":
                # 「자신이 기절 면역 상태라면」 — 위와 같은 규약으로 **버프 이름이 아니라 stat**을 본다.
                # 남이 건 기절 면역도 참이어야 하므로 self_state:를 쓰지 않는다 (D `처단 3`).
                if not self._has_immune(caster, "stun_immune"):
                    return False
            elif cond == "self_undying":
                # 「자신이 불굴 상태라면」 — 위와 같은 규약으로 **버프 이름이 아니라 stat**을 본다.
                # 불굴은 이름이 아니라 상태라(나유타의 불굴은 이름이 `부동심`) `self_state:`로는 못 잡고,
                # 남이 건 불굴(블랑 `쇼타임 2`)도 참이어야 한다. 보스 공격이 불굴을 보는 창구와 같다
                # (마키마 `조용히 해주겠니? 3`).
                if not self.has_live_stat(caster, "undying", t):
                    return False
            elif cond.startswith("self_hp_above:"):
                n = float(cond.split(":")[1])
                hp_pct = self.state.get("hp_pct", {}).get(caster, 100.0)
                if hp_pct < n:
                    return False
            elif cond.startswith("self_hp_below:"):
                n = float(cond.split(":")[1])
                hp_pct = self.state.get("hp_pct", {}).get(caster, 100.0)
                if hp_pct > n:
                    return False
            elif cond == "self_hp_max":
                hp_pct = self.state.get("hp_pct", {}).get(caster, 100.0)
                if hp_pct < 100.0:
                    return False
            elif cond == "during_shield":
                if not self.has_shield(caster):
                    return False
            elif cond == "self_cover_alive":
                if not self.cover_alive(caster):
                    return False
            elif cond == "not_self_cover_alive":
                # 「자신의 엄폐물이 파괴된 상태라면」 — 위의 부정. 엄폐물은 보스 공격
                # 패턴에만 부서지므로 기본 경로에서는 늘 거짓이다 (베이 애장품 2·3단계).
                if self.cover_alive(caster):
                    return False
            elif cond == "no_broken_cover_ally":
                # 「엄폐물이 파괴된 아군이 없다면」 — 시전자 포함 산 아군 전원의 엄폐물이 살아 있어야 참.
                # 같은 스킬 앞 clause의 대상 `allies_broken_cover_random:N`(시전자를 빼지 않는다)의
                # 후보가 0기인 것과 정확히 같은 판정이라 두 clause가 배타 분기가 된다. 발동 시점
                # 판정이다(`[10초 유지]` 버프의 게이트). 엄폐물은 보스 공격 패턴에만 부서지므로
                # 기본 경로에서는 늘 참이다 (릴리 `최고의 엔지니어! 3`).
                if any(not self.cover_alive(x) for x in self._alive()):
                    return False
            elif cond.startswith("ally_hp_below:"):
                # 발동 시점에는 target이 아직 resolve되기 전이라 개별 대상을 볼 수 없다.
                # "체력 N% 이하인 아군이 하나라도 있는가"로 판정하고,
                # 대상별 판정은 get_buffs의 _runtime_condition_ok()가 이어받는다.
                n = float(cond.split(":")[1])
                hp_map = self.state.get("hp_pct", {})
                if min((hp_map.get(x, 100.0) for x in self.squad_names), default=100.0) > n:
                    return False
            elif cond == "back_row":
                idx = self.squad_names.index(caster)
                if idx not in (1, 3):  # 후열 = 포지션 2(idx 1) 또는 4(idx 3)
                    return False
            elif cond == "squad_ally_exists":
                # 소속 스쿼드(카운터스·이지스 등, parsed_nikke["squad"])가 같은 아군이
                # 자신 외에 편성돼 있어야 True. 의상 버전도 원본과 같은 스쿼드일 수 있다
                # (라피 : 레드 후드 = Counters). 스쿼드가 없는 더미 캐릭터는 False.
                my_squad = _NIKKE.get(caster, {}).get("squad")
                if not my_squad or not any(
                    _NIKKE.get(n, {}).get("squad") == my_squad
                    for n in self.squad_names if n != caster
                ):
                    return False
            elif cond == "has_burst1_ally":
                # 자신 제외 스쿼드에 1버스트 캐릭터가 있어야 함
                burst_stages = self.state.get("burst_stages", {})
                has = any(burst_stages.get(n) == "1" for n in self.squad_names if n != caster)
                if not has:
                    return False
            elif cond == "no_burst1_ally":
                # 자신 제외 스쿼드에 1버스트 캐릭터가 없어야 함
                burst_stages = self.state.get("burst_stages", {})
                has = any(burst_stages.get(n) == "1" for n in self.squad_names if n != caster)
                if has:
                    return False
            elif cond in ("has_defender_ally", "no_defender_ally"):
                # 자신 제외 스쿼드에 방어형 아군이 있는가. `parsed_nikke["class"]`로 판정한다
                # (스쿼드 구성은 전투 중 안 바뀌므로 _RUNTIME_COND_PREFIXES 대상이 아니다).
                # 두 조건은 같은 원문의 배타 분기다 — 한쪽만 구현하면 양쪽이 동시에 성립한다.
                has = any(
                    _NIKKE.get(n, {}).get("class") == "방어형"
                    for n in self.squad_names if n != caster
                )
                if has != (cond == "has_defender_ally"):
                    return False
            elif cond.startswith("gauge_above:"):
                parts = cond.split(":")
                gauge_id, threshold = parts[1], float(parts[2])
                current = self.state.get("gauges", {}).get(caster, {}).get(gauge_id, 0.0)
                if current < threshold:
                    return False
            elif cond.startswith("gauge_below:"):
                parts = cond.split(":")
                gauge_id, threshold = parts[1], float(parts[2])
                current = self.state.get("gauges", {}).get(caster, {}).get(gauge_id, 0.0)
                if current >= threshold:
                    return False
            elif cond.startswith("gauge_eq:"):
                parts = cond.split(":")
                gauge_id, threshold = parts[1], float(parts[2])
                current = self.state.get("gauges", {}).get(caster, {}).get(gauge_id, 0.0)
                if current != threshold:
                    return False
            elif cond.startswith("gauge_mod:"):
                parts = cond.split(":")
                gauge_id, mod, rem = parts[1], int(parts[2]), int(parts[3])
                current = int(self.state.get("gauges", {}).get(caster, {}).get(gauge_id, 0))
                if current % mod != rem:
                    return False
            elif cond.startswith("self_state:"):
                state_name = cond[len("self_state:"):]
                if not self._has_self_state(caster, state_name):
                    return False
            elif cond.startswith("not_self_state:"):
                state_name = cond[len("not_self_state:"):]
                if self._has_self_state(caster, state_name):
                    return False
            elif cond.startswith("target_state:"):
                state_name = cond[len("target_state:"):]
                if not self._has_target_state(state_name):
                    return False
            elif cond.startswith("not_target_state:"):
                state_name = cond[len("not_target_state:"):]
                if self._has_target_state(state_name):
                    return False
            elif cond.startswith("target_code:"):
                code = cond[len("target_code:"):]
                enemy_code = self.state.get("enemy", {}).get("code", "")
                if enemy_code and enemy_code != code:
                    return False
            elif cond.startswith("enemy_count_below:"):
                # 적 수 = 보스 1 + 산 쫄몹(`state["enemy_count"]`, 프레임 맨 앞에 정한다). 쫄몹이 없으면 1 —
                # "랩쳐 N기 이하" → 1 <= N (N>=1이면 항상 참)
                n = int(cond.split(":")[1])
                if self.state.get("enemy_count", 1) > n:
                    return False
            elif cond.startswith("enemy_count_above:"):
                # 쫄몹이 없으면 적 1기 — "랩쳐 N기 이상" → 1 >= N (N>=2이면 항상 거짓 → 무발동)
                n = int(cond.split(":")[1])
                if self.state.get("enemy_count", 1) < n:
                    return False
            elif cond.startswith("self_stack_above:"):
                parts = cond.split(":")
                stack_name, threshold = parts[1], int(parts[2])
                current = next(
                    (ab.stack for ab in self._active
                     if ab.effect.get("name") == stack_name
                     and ab.caster == caster
                     and (caster in (ab.target_chars or [])
                          or any(_is_enemy(x) for x in (ab.target_chars or [])))),
                    0,
                )
                if current < threshold:
                    return False
            elif cond.startswith("target_stack_above:"):
                # 「대상이 [스택명] 최대 중첩 상태라면」 — `self_stack_above:`의 대상(적)판.
                # 존재만 보는 `target_state:`와 같은 창구 규약(어느 적에게든 — 쫄몹이 없으면 보스)에
                # 중첩 수 비교를 얹는다. 발동 시점 1회 판정이다 (프림 `일어남` 애장품 1단계)
                stack_name, _, threshold = cond[len("target_stack_above:"):].rpartition(":")
                if self._target_stack(stack_name) < int(threshold):
                    return False
            elif cond.startswith("self_stat_above:"):
                # "자신이 [stat] 증가 상태라면" — 버프 *이름*이 아니라 **stat 값**으로 판정한다.
                # 누가 건 버프인지 무관하게 caster에게 적용 중인 해당 stat의 합이 N보다 크면 참.
                # (모더니아 `대도약 2` — "자신이 명중률 증가 상태라면")
                #
                # get_buffs를 그대로 쓴다: 스택·scaling·runtime condition이 이미 반영된 값이라
                # _active를 직접 훑어 합산하면 그 로직을 중복 구현하게 된다. get_buffs는 읽기
                # 전용이고 _condition_ok를 다시 부르지 않으므로 재진입 위험도 없다.
                parts = cond.split(":")
                stat_key, threshold = parts[1], float(parts[2])
                buff_key = _STAT_TO_BUFF.get(stat_key, stat_key)
                if self.get_buffs(caster, "__enemy__", t).get(buff_key, 0.0) <= threshold:
                    return False
            elif cond == "core_hit":
                # 코어 유무는 enemy["core_px"]가 정본 (>=1이면 코어 있음, 0이면 없음).
                # 기본공격의 코어히트는 명중률·탄착군 확률이지만, 이 condition이 붙은 스킬은
                # "코어가 활성화된 적"을 대상으로 하는 확정 발동이다.
                if float(self.state.get("enemy", {}).get("core_px", 0) or 0) < 1:
                    return False
            elif cond == "optimal_range":
                # 적정 사거리 판정의 정본은 `in_optimal_range`다 — ③ 고정 +30%를 태우는 timeline
                # 판정과 같은 함수. 보스 거리가 없으면 적 스펙 `optimal_range_weapons`(기본 빈 목록이라
                # 스쿼드 스펙이 무기군을 명시하지 않으면 무발동), 있으면 시전자의 적정 구간이다.
                # 무기 유형은 로스터 값을 본다(무기 변경 모드는 반영하지 않는다 —
                # 지금 이 조건을 쓰는 캐릭터에 모드 전환이 없다). get_buffs는 거리가 있을 때만 부른다 —
                # 사거리 ▲가 구간을 넓히는 데만 쓰이고, 읽기 전용이라 재진입 위험은 `self_stat_above:`와 같다.
                enemy = self.state.get("enemy", {})
                wt = _NIKKE.get(caster, {}).get("weapon_type")
                buffs = (self.get_buffs(caster, "__enemy__", t)
                         if enemy.get("distance") is not None else {})
                if not in_optimal_range(enemy, caster, wt, buffs):
                    return False
            # 나머지 condition은 get_buffs에서 재평가
        return True

    def _has_harmful(self, name: str) -> bool:
        """이 니케에게 지금 해로운 효과가 하나라도 걸려 있는가.

        `debuff_cleanse`가 지우는 대상(`harmful`)과 못 지우는 것(`harmful_irremovable`)을
        **둘 다** 센다 — 원문은 「해로운 효과 소지 아군」이지 「해제 가능한 효과 소지」가 아니다.
        보스 공격 패턴이 없으면 아군에게 걸리는 harmful이 드물어 대체로 거짓이다.
        """
        return any(
            str(ab.effect.get("polarity", "")).startswith("harmful")
            and name in (ab.target_chars or [])
            for ab in self._active
        )

    def _has_self_state(self, caster: str, state_name: str) -> bool:
        """self_state:/not_self_state: 판정의 단일 창구.

        상태는 두 곳에 있을 수 있다 — 일반 버프(_active)와 무기 변경 모드(state["weapon_change"]).
        weapon_change는 _active에 등록되지 않으므로 여기서 같이 봐야
        `self_state:저격 모드`처럼 모드 자체를 가리키는 조건이 성립한다.
        """
        if any(caster in (ab.target_chars or []) for ab in self._by_name(state_name)):
            return True
        if state_name == WEAPON_CHANGE_STATE:
            # 원문이 모드 이름이 아니라 「무기 변경 상태」라고만 쓴 경우 — 그 캐릭터가
            # 무슨 모드든 들고 있으면 성립한다. 모드명으로만 대조하면 영구 거짓이 된다
            # (목단 `다 덤벼!` — "자신이 무기 변경 상태라면 일반 공격 5회 명중 시").
            #
            # `is not None`으로 보면 안 된다 — `weapon_change_name()`은 모드가 없을 때
            # None이 아니라 **빈 문자열**을 준다. 영구 거짓이 이번엔 영구 참으로 뒤집혀
            # 모드 밖 일반 공격에도 조건이 통과한다.
            return bool(self.weapon_change_name(caster))
        return self.weapon_change_name(caster) == state_name

    def element_override_match(self, name: str, enemy_code: str) -> bool:
        """`element_code_override` 버프로 이 적에게 우월 코드가 성립하는가.

        본인 코드 상성(`damage.is_element_match`)과 **별개의 경로**다. 로스터 코드
        자체는 바뀌지 않으므로 `allies_code:` 같은 대상 판정에는 영향이 없다
        (`scenarios/센티.md §해석 선언`).

        대상 코드는 `note` 원문이 아니라 `target_code` 필드에서 읽는다.
        """
        if not enemy_code:
            return False
        return any(
            ab.effect.get("target_code") == enemy_code
            and name in (ab.target_chars or [])
            for ab in self._by_stat("element_code_override")
        )

    def _has_persona_state(self, name: str) -> bool:
        """`persona_state` 마커 버프 보유 여부 — `allies_burst3_persona_excl_self` 판정용."""
        return any(
            ab.effect.get("stat") == "persona_state" and name in (ab.target_chars or [])
            for ab in self._active
        )

    def _event_audience(self, eff: dict, targets, caster: str) -> list[str]:
        """`event:{name}` 통지 대상.

        기본은 스쿼드 전체 브로드캐스트다 — 다른 캐릭터가 남의 상태 변화를 트리거로
        반응하는 기존 캐릭터들이 이 동작에 의존한다.
        `event_scope: "recipients"`인 효과만 **실제로 버프를 받은 대상**에게만 통지한다.
        자기 자신에게만 붙는 상태(퀸(마코토)·유키코의 `1more`)가 이름이 같아
        서로의 트리거를 잘못 여는 것을 막는 용도다.
        """
        if eff.get("event_scope") != "recipients":
            return list(self.squad_names)
        # 대상이 **확정됐는데 0명**이면 아무도 받지 않았다 — 시전자에게 떨어뜨리지 않는다.
        # 떨어뜨리면 받지도 않은 상태의 「적용 시」 트리거가 시전자에게서 한 번 더 돈다
        # (길티 : 마이티 바니 · 신 : 스위프트 바니 동기화 — `allies_with_buff:` 대상 0명).
        # `None`은 지연 resolve라 아직 모르는 것이므로 종전대로 시전자다.
        if targets is None:
            return [caster] if caster in self.squad_names else []
        return [c for c in targets if c in self.squad_names]

    def pellet_in_shot_thresholds(self, caster: str) -> list[tuple[int, str]]:
        """이 캐스터의 효과가 쓰는 `pellet_hit_in_shot:N` 임계값 목록 — `(값, 원문 표기)`.

        「일반 공격 1회로 펠릿 N개 이상 명중 시」는 **한 발 안의** 명중 펠릿 수를 보므로
        누적 카운터인 `pellet_hit_count:N`과 다른 축이다. 타임라인이 발사마다 그 발의
        펠릿 명중 수를 알고 있으니, 판정은 거기서 하고 여기서는 임계값만 모아 준다
        (`charge_hold_thresholds`와 같은 모양). 프리바티 : 언카인드 메이드 `사랑 가득 메이드`
        """
        cached = self._pellet_in_shot_cache.get(caster)
        if cached is not None:
            return cached
        found: dict[str, int] = {}
        for eff, eff_caster in self._effects:
            if eff_caster != caster:
                continue
            for timing in eff["trigger"]["timing"]:
                if not timing.startswith("pellet_hit_in_shot:"):
                    continue
                raw = timing.split(":", 1)[1]
                try:
                    found[raw] = int(raw)
                except ValueError:
                    continue
        result = sorted(((v, raw) for raw, v in found.items()))
        self._pellet_in_shot_cache[caster] = result
        return result

    def charge_hold_thresholds(self, caster: str) -> list[tuple[float, str]]:
        """이 캐스터의 효과가 쓰는 `charge_hold:N` 임계값 목록 — `(값, 원문 표기)`.

        `_timing_match`가 문자열 완전 일치라 notify도 원문 표기 그대로 보내야 한다
        (`charge_hold:0.5` ≠ `charge_hold:0.50`). 타임라인이 매 프레임 호출하므로 캐싱한다.
        """
        cached = self._charge_hold_cache.get(caster)
        if cached is not None:
            return cached
        found: dict[str, float] = {}
        for eff, eff_caster in self._effects:
            if eff_caster != caster:
                continue
            for timing in eff["trigger"]["timing"]:
                # `charge_hold:N`과 `charge_hold_count:N:M`이 같은 임계값을 쓴다 —
                # 후자는 전자의 판정을 M회 세는 것뿐이라 notify 표기도 `charge_hold:N`이다.
                if timing.startswith("charge_hold_count:"):
                    parts = timing.split(":")
                    raw = parts[1] if len(parts) == 3 else None
                elif timing.startswith("charge_hold:"):
                    raw = timing.split(":", 1)[1]
                else:
                    continue
                if raw is None:
                    continue
                try:
                    found[raw] = float(raw)
                except ValueError:
                    continue
        result = sorted(((v, raw) for raw, v in found.items()))
        self._charge_hold_cache[caster] = result
        return result

    def _has_target_state(self, state_name: str) -> bool:
        """target_state:/not_target_state: 판정의 단일 창구.

        적(`__enemy__` 또는 쫄몹 id)이 target_chars에 있는 활성 효과로 확인한다. 조건에는 「지금 맞는 적」
        문맥이 없어서 **어느 적에게든** 붙어 있으면 참이다(쫄몹이 없으면 보스 하나라 종전과 같다 — 근사).
        """
        return any(
            any(_is_enemy(x) for x in (ab.target_chars or [])) for ab in self._by_name(state_name)
        )

    def _target_stack(self, state_name: str) -> int:
        """`target_stack_above:`의 판정값 — 적에게 걸린 그 이름 효과의 중첩 수(여럿이면 최대).
        `_has_target_state()`와 같은 근사로 어느 적에게든 붙어 있으면 센다."""
        return max(
            (ab.stack for ab in self._by_name(state_name)
             if any(_is_enemy(x) for x in (ab.target_chars or []))),
            default=0,
        )

    def enemy_has_state(self, enemy_id: str, state_name: str) -> bool:
        """이 적에게 그 이름의 효과가 붙어 있는가 — `enemies_with_buff:X`를 쫄몹이 있을 때 푸는 창구."""
        return any(enemy_id in (ab.target_chars or []) for ab in self._by_name(state_name))

    def drop_enemies(self, ids: list[str], t: float) -> None:
        """사라진 쫄몹을 모든 활성 효과의 대상에서 지운다. 대상이 비면 그 효과는 누구에게도 안 걸린다
        (지속 대미지 틱도 맞을 적이 없어 버려진다)."""
        gone = set(ids)
        touched = False
        for ab in self._active:
            if ab.target_chars and gone.intersection(ab.target_chars):
                if self._buff_event_handler and ab.effect.get("name"):
                    for tgt in gone.intersection(ab.target_chars):
                        self._buff_event_handler("expire", ab.effect["name"], ab.caster, tgt, t, t)
                ab.target_chars = [x for x in ab.target_chars if x not in gone]
                touched = True
        if touched:
            self._invalidate_buffs_cache()

    def weapon_change_name(self, caster: str) -> str:
        """현재 활성 weapon_change 효과의 이름. 없으면 빈 문자열."""
        info = self.state.get("weapon_change", {}).get(caster)
        if not info:
            return ""
        return info["effect"].get("name", "")

    def manual_swap_ready(self, caster: str, t: float) -> bool:
        """수동 재장전으로 지금 진입 가능한 weapon_change가 있는가.

        타임라인의 "모드 지정 플래그"(char["weapon_mode_swap"])가 쓰는 판정.
        `event:full_reload` + 조건부인 weapon_change만 대상이며(조건 없는 무기 변경은
        자연 재장전으로 이미 걸리므로 제외), 이미 모드 중이면 False —
        진입만 삽입하고 토글 해제는 하지 않는다.
        """
        if caster in self.state.get("weapon_change", {}):
            return False
        for eff, eff_caster in self._notify_index.get(caster, {}).get("event:full_reload", []):
            if eff.get("type") != "weapon_change":
                continue
            conds = eff["trigger"].get("condition", [])
            if conds and self._condition_ok(conds, eff_caster, t, eff):
                return True
        return False

    def effective_max_hp(self, name: str) -> float:
        """현재 활성 max_hp_pct / max_hp_only_pct / hp_caster_based_pct / hp_only_caster_based_pct
        버프를 반영한 최대 체력 절대값. get_buffs() 재귀 없이 _active를 직접 순회한다."""
        base_hp = self.state.get("base_stats", {}).get(name, {}).get("hp", 0.0)
        bonus_pct = 0.0
        bonus_flat = 0.0
        for ab in self._active:
            stat = ab.effect.get("stat", "")
            if name not in (ab.target_chars or []):
                continue
            if stat in ("max_hp_pct", "max_hp_only_pct"):
                val = self._get_value(ab.effect, ab, name)
                if val is not None:
                    bonus_pct += val
            elif stat in ("hp_caster_based_pct", "hp_only_caster_based_pct"):
                caster_base_hp = self.state.get("base_stats", {}).get(ab.caster, {}).get("hp", 0.0)
                val = self._get_value(ab.effect, ab, name)
                if val is not None:
                    bonus_flat += caster_base_hp * val / 100.0
            elif stat == "max_hp_from_max_hp_pct":
                # 부여 시점에 확정한 절대값을 그대로 쓴다 (`ActiveBuff.hp_bonus_flat`).
                # 여기서 effective_max_hp(ab.caster)를 다시 부르면 시전자가 자기 대상일 때 재귀다.
                bonus_flat += ab.hp_bonus_flat
        return base_hp * (1.0 + bonus_pct / 100.0) + bonus_flat

    # ── 「누적 → 폭발」 누적기 ────────────────────────────────────────────
    #
    # 두 캐릭터가 같은 메커니즘을 반대 방향으로 쓴다:
    #   · 트로니 `누적 폭발 스킬` — **시전자가 가하는** 딜을 모으고, 상한에 닿는 순간
    #     `event:accum_full:[이름]`으로 방출한다. 그래서 방출 1회가 늘 상한값이다.
    #   · 도로시 `낙인`     — **대상이 받는** 딜(스쿼드 전체분)을 모으고, 상한은 절삭만
    #     하며 **만료**(`event:state_end:[이름]`)로 방출한다.
    # 상한은 부여 시점 스냅샷이고(ActiveBuff.accum_cap), 누적·방출 모두 방어력이 적용된
    # **실피해** 단위다 — 방출은 DealForm을 다시 타지 않는다(유저 결정 2026-09-22).

    _ACCUM_STATS = ("dmg_accum_dealt_atk_pct", "dmg_accum_received_atk_pct")

    def final_atk(self, name: str, t: float) -> float:
        """name의 **최종 공격력** = 기본 공격력 × (1 + atk_pct%) + atk_flat.

        `damage._factor2()`의 공격 항과 같은 식이다 — 누적 상한이 「시전자 최종 공격력의
        N%」라 같은 자로 재야 한다. 「시전자 기준」(`atk_caster_based_pct`)과 달리
        **버프를 포함한** 값이다(`GAMEPLAY.md` §값 산정).
        """
        base_atk = self.state.get("base_stats", {}).get(name, {}).get("atk", 0.0)
        b = self.get_buffs(name, "__enemy__", t)
        return base_atk * (1.0 + b.get("atk_pct", 0.0) / 100.0) + b.get("atk_flat", 0.0)

    def _accum_rate(self, ab: "ActiveBuff") -> float:
        """이 누적기의 실효 누적 비율(%).

        기준 비율은 `target_effect` 없는 `dmg_accum_rate_pct`(트로니 `누적 폭발 스킬 2` 50%)이고,
        같은 시전자에게 걸린 `target_effect == 담체 이름`짜리가 **가산**된다
        (트로니 `메가 T.Rony 2` +62.83%p — 원문에 「배율」이 없으므로 곱하지 않는다).
        기준 항목이 아예 없으면 100%다 — 도로시 `낙인`의 「**일괄** 누적」이 그 경우로,
        원문에 비율 블록이 따로 없다.
        """
        name = ab.effect.get("name", "")
        base = None
        add = 0.0
        for other in self._by_stat("dmg_accum_rate_pct"):
            if other.caster != ab.caster:
                continue
            ref = other.effect.get("target_effect")
            val = self._get_value(other.effect, other, other.caster)
            if val is None:
                continue
            if ref is None:
                base = (base or 0.0) + val
            elif ref == name:
                add += val
        return (100.0 if base is None else base) + add

    def accumulate_damage(self, caster: str, damage: float, t: float) -> None:
        """보스에게 들어간 히트 하나를 살아 있는 누적기들에 반영한다.

        `timeline._land_boss()`가 딜을 총합에 더한 **직후** 부른다 — 쫄몹 몫은 보스가 받은
        딜이 아니라서 그쪽 경로에는 붙이지 않는다.
        """
        if damage <= 0.0:
            return
        # 보유자가 없는 스쿼드에서 히트마다 `_active`를 훑지 않도록 stat 색인을 쓴다
        # (`_by_stat`은 캐시되고 없으면 빈 리스트다)
        live = (self._by_stat("dmg_accum_dealt_atk_pct")
                + self._by_stat("dmg_accum_received_atk_pct"))
        if not live:
            return
        full: list[tuple[str, str]] = []
        for ab in live:
            stat = ab.effect.get("stat", "")
            if ab.accum_done or ab.accum_cap <= 0.0:
                continue
            if stat == "dmg_accum_dealt_atk_pct" and ab.caster != caster:
                continue    # 「시전자가 가하는」 — 남의 딜은 안 센다
            ab.accum = min(ab.accum + damage * self._accum_rate(ab) / 100.0, ab.accum_cap)
            name = ab.effect.get("name", "")
            if name:
                self._accum_last[name] = ab.accum
            if ab.accum >= ab.accum_cap - 1e-9:
                ab.accum_done = True      # 상한 도달 — 더 모으지 않는다
                if name:
                    full.append((name, ab.caster))
        # notify가 방출·해제를 부르며 `_active`를 건드리므로 순회를 끝낸 뒤에 쏜다
        for name, ab_caster in full:
            self.notify(f"event:accum_full:{name}", t, ab_caster)

    def accum_discharge(self, name: str, t: float) -> float:
        """`name` 누적기가 모은 양을 방출한다 — 값을 반환하고 누적기를 비운다.

        담체가 아직 살아 있으면(트로니 — 상한 도달 방출) 그 자리에서 비우고 `accum_done`을
        세워 **방출 대미지 자신이 다시 누적되는 재귀**를 막는다. 담체가 이미 사라졌으면
        (도로시 — 만료 방출) `_accum_last`에 남겨 둔 마지막 값을 쓴다.
        """
        val = 0.0
        for ab in self._active:
            if ab.effect.get("name") == name and ab.effect.get("stat", "") in self._ACCUM_STATS:
                val = ab.accum
                ab.accum = 0.0
                ab.accum_done = True
                break
        else:
            val = self._accum_last.get(name, 0.0)
        self._accum_last[name] = 0.0
        return val

    def shield_amount(self, name: str) -> float:
        """name에게 현재 적용 중인 보호막 총량.

        보스 공격(`absorb_shield`)에 깎이지 않으면 생성량 그대로 유지되며 ActiveBuff 만료와
        함께 사라진다. 여러 독립 보호막은 각각 보존하고 조회 시 합산한다.
        """
        return sum(
            ab.shield_per_target.get(name, 0.0)
            for ab in self._active
            if ab.effect.get("stat") in _SHIELD_STATS
        )

    def shield_capacity(self, name: str) -> float:
        """name의 보호막 **최대치** 총합 — 부여 시점 값(`shield_max_per_target`)의 합.

        `shield_amount()`가 지금 남은 양이라면 이쪽은 되돌릴 수 있는 상한이다.
        기준 표기가 없는 `[보호막 체력 회복 N%]`의 분모로 쓴다(지금 로스터에 보유자는 없다 —
        셋 다 「시전자의 최종 최대 체력 비례」다).
        """
        return sum(
            ab.shield_max_per_target.get(name, 0.0)
            for ab in self._active
            if ab.effect.get("stat") in _SHIELD_STATS
        )

    def has_shield(self, name: str) -> bool:
        """name에게 양수 보호막이 하나 이상 활성화돼 있는지 반환."""
        return any(
            ab.shield_per_target.get(name, 0.0) > 0.0
            for ab in self._active
            if ab.effect.get("stat") in _SHIELD_STATS
        )

    # ── 보스 → 니케 피해 (보스 패턴 `attack`) ─────────────────────────────
    #
    # 피해 산정과 층 순서는 timeline `_boss_attack()`이 정한다. 여기는 니케 쪽 상태를 묻고
    # 바꾸는 창구만 둔다 — 버프 목록(`_active`)을 만지는 일은 이 클래스 밖으로 새면 안 된다.

    def _live(self, ab: ActiveBuff, name: str, t: float) -> bool:
        """이 버프가 지금 name에게 살아 있는가 — 지속 버프의 런타임 조건까지 본다
        (목단 `정정당당 승부다! 6`처럼 `self_state:`로 켜지는 영구 `cover_disabled`).

        지연 resolve 대상(`_LAZY_RESOLVE_PREFIXES`)은 **여기서 확정한다.** 안 하면 get_buffs가
        읽지 않는 stat(값 없는 불굴 등)은 대상이 영영 None이라 아무에게도 안 걸린다 — 블랑
        `쇼타임 2`(`allies_lowest_hp_excl:1`). 같은 블록의 `쇼타임 3`(최대 체력)과는
        `_lazy_target_cache`로 대상을 공유하므로 조회가 늦어도 같은 아군을 고른다."""
        if t >= ab.expires_at:
            return False
        if ab.target_chars is None:
            self._resolve_lazy(ab)
            self._invalidate_buffs_cache()
        if name not in ab.target_chars:
            return False
        if not ab.has_runtime_conditions:
            return True
        return self._runtime_condition_ok(
            ab.effect["trigger"].get("condition", []), ab.caster, name, name, t)

    def has_live_stat(self, name: str, stat: str, t: float) -> bool:
        return any(self._live(ab, name, t) for ab in self._by_stat(stat))

    def split_group(self, name: str, t: float) -> list[str]:
        """`받는 대미지 균등 분배` — name과 한 발을 나눠 지는 산 니케 목록(자신 포함).

        분배가 안 걸렸거나 혼자 남았으면 **빈 목록**이다 — 호출부가 기존 단일 경로를 그대로 탄다.
        분배가 둘 이상 겹치면 합집합이다(⬜ 인게임 미확인 — 지금 로스터엔 겹칠 조합이 없다).
        전투불능인 멤버는 빠진다: 쓰러진 니케는 피해를 안 받는다.
        `_live()`가 지연 resolve까지 확정하므로 `["self", "allies_lowest_hp_excl:2"]` 같은
        복합 대상도 여기서 풀린다(블랑 `쇼타임 2`와 같은 경로).
        """
        if self.is_down(name):
            return []
        out: list[str] = [name]
        for ab in self._by_stat("received_dmg_split_even"):
            if not self._live(ab, name, t):
                continue
            for c in (ab.target_chars or []):
                if c not in out and not self.is_down(c) and self._live(ab, c, t):
                    out.append(c)
        return out if len(out) > 1 else []

    def taunters(self, t: float) -> list[str]:
        """지금 도발 중인 산 니케(스쿼드 순서). 자기에게 건 `taunt`와, 적에게 걸어 자신을
        노리게 한 `taunt`(목단 `여긴 내가 맡는다!` — 대상이 적이라 시전자가 도발자다) 둘 다."""
        out: list[str] = []
        for ab in self._by_stat("taunt"):
            if t >= ab.expires_at:
                continue
            chars = ab.target_chars or []
            who = ab.caster if any(_is_enemy(x) for x in chars) else next(
                (c for c in chars if self._live(ab, c, t)), None)
            if who is not None and who not in out and not self.is_down(who):
                out.append(who)
        return [n for n in self.squad_names if n in out]

    def _live_sum(self, name: str, stat: str, t: float) -> float:
        """name에게 지금 살아 있는 `stat` 버프 값의 합."""
        total = 0.0
        for ab in self._by_stat(stat):
            if self._live(ab, name, t):
                total += self._get_value(ab.effect, ab, name) or 0.0
        return total

    def incoming_dmg_pct(self, name: str, t: float) -> float:
        """name이 받는 피해 증감 % 합. 소장품·큐브의 감소는 음수로 저장돼 있다."""
        return self._live_sum(name, "received_dmg_pct", t)

    def heal_received_mult(self, name: str, t: float) -> float:
        """name이 받는 체력 회복량 배율 — `1 + heal_received_pct 합 / 100`, 0 아래로는 안 내려간다
        (보스 디버프 「받는 회복량 ▼」가 100%를 넘어도 회복이 체력을 깎지 않는다).

        회복 경로(힐 instant·흡혈)가 회복량에 곱한다. 버프가 없으면 정확히 1.0이라 곱해도
        부동소수점이 안 흔들린다."""
        return max(0.0, 1.0 + self._live_sum(name, "heal_received_pct", t) / 100.0)

    # ── 보스가 건 효과 (보스 패턴 `debuff` · attack `debuffs` · buff `received_dmg_pct`) ──
    #
    # `_effects`는 초기화 때 확정되고 트리거(`_notify`)로만 발동한다. 보스 효과는 트리거가 아니라 보스
    # 스크립트가 시각·대상을 정해 직접 거는 것이라 **`_activate`로 바로 들어간다.** 시전자는 `__enemy__`다.
    # 효과 dict는 `boss_pattern`이 패턴마다 한 번 만들어 계속 같은 객체를 넘긴다 — 재발동(중첩·지속 갱신)을
    # dict 동일성으로 알아보는 `_activate`의 규약 그대로다.

    def _harmful_blocked(self, name: str, eff: dict) -> bool:
        """name이 이 해로운 효과에 면역인가 — `debuff_immune` · `debuff_immune:[효과 이름]` ·
        `debuff_immune_count`(개수 제한).

        **개수 제한 면역은 여기서 한 개를 소모한다.** 부르는 쪽이 둘뿐이고
        (`_activate`의 대상 필터 · `apply_boss_effect`) 둘 다 「지금 이 대상에게 붙이려다
        막혔다」는 지점이라 소모 시점이 정확히 한 번이다. `apply_boss_effect`는 막히면
        `_activate`를 부르지 않으므로 이중 소모가 없다.
        """
        eff_name = eff.get("name", "")
        if (self._has_immune(name, "debuff_immune")
                or bool(eff_name) and self._has_immune(name, f"debuff_immune:{eff_name}")):
            return True
        return self._consume_immune_charge(name)

    def _consume_immune_charge(self, name: str) -> bool:
        """`debuff_immune_count` 잔량이 있으면 하나 쓰고 True.

        **잔량은 버프 이름 단위 풀이다.** 원문이 같은 상태(`[완벽한 메이드 : 해로운 효과 면역
        1개] [1 중첩]`)를 여러 경로로 부여하면 인게임에서는 **총 N개**이므로, 같은 이름의
        항목들은 합이 아니라 최대값 하나를 공유한다. 재부여(`_activate` 후처리)가 그 이름의
        소모량을 0으로 되돌린다 — 그게 「소모된 뒤 다시 채워 주는」 두 번째 블록의 역할이다.
        (에이드 `완벽한 메이드` — 스킬1 전투 시작 · 스킬2 일반 공격 420회)

        개수는 `debuff_cleanse`와 같이 **대상 니케 1인당**이다(보스 디버프가 니케마다 따로
        붙는다 — `IMPL-STATUS.md` `debuff_cleanse` 행, 2026-09-15).
        """
        cap: dict[str, float] = {}
        for ab in self._active:
            if ab.effect.get("stat") != "debuff_immune_count":
                continue
            if name not in (ab.target_chars or []):
                continue
            val = self._get_value(ab.effect, ab, name)
            if val is None:
                continue
            key = ab.effect.get("name", "")
            cap[key] = max(cap.get(key, 0.0), float(val))
        for key, total in cap.items():
            used = self._immune_used.get((name, key), 0.0)
            if used < total:
                self._immune_used[(name, key)] = used + 1.0
                return True
        return False

    def apply_boss_effect(self, eff: dict, target: str, t: float) -> bool:
        """보스가 건 효과 하나를 target(니케 이름 또는 `__enemy__`)에게 붙인다. 붙었으면 True.

        니케에게는 **한 명씩 따로** 붙인다 — 같은 효과가 다시 걸리면 그 니케의 것만 중첩·갱신되고,
        해제(`debuff_cleanse`)·전투불능 소멸도 그 니케의 것만 지운다. 쓰러졌거나 면역이면 안 붙는다."""
        if not _is_enemy(target):
            # 면역 판정은 `_activate`와 같은 식이다 — `harmful_irremovable`은 면역이 거르지 않는다(기존 규약)
            if self.is_down(target) or (eff.get("polarity") == "harmful"
                                        and self._harmful_blocked(target, eff)):
                return False
        self._activate(eff, "__enemy__", t, targets=[target])
        return True

    def release_boss_effects(self, pattern: str, t: float) -> None:
        """이 보스 패턴이 건 효과 중 「패턴이 닫힐 때 풀리는」 것(`_boss_bound`)을 지운다."""
        gone = [ab for ab in self._active
                if ab.caster == "__enemy__" and ab.effect.get("_boss_pattern") == pattern
                and ab.effect.get("_boss_bound")]
        if not gone:
            return
        drop = {ab.uid for ab in gone}
        self._active = [ab for ab in self._active if ab.uid not in drop]
        self._invalidate_buffs_cache()
        if self._buff_event_handler:
            for ab in gone:
                for tgt in (ab.target_chars or []):
                    self._buff_event_handler("expire", ab.effect["name"], ab.caster, tgt, t, t)

    def cover_alive(self, name: str) -> bool:
        """name의 엄폐물이 살아 있는가. 엄폐물 상태가 없는 실행(단독 BuffManager)은 산 것으로 본다."""
        return self.state.get("cover_hp", {}).get(name, 1.0) > 0.0

    def break_cover(self, name: str) -> None:
        """name의 엄폐물이 부서졌다. `self_cover_alive` 판정이 바뀌므로 집계 캐시를 비운다
        — 같은 프레임에 이미 집계한 버프가 부서지기 전 값으로 남지 않게."""
        self.state["cover_hp"][name] = 0.0
        self._invalidate_buffs_cache()

    def revive_cover(self, name: str, hp: float) -> None:
        """부서진 name의 엄폐물을 체력 `hp`로 되살린다 — `break_cover`의 역이다.

        `cover_revive` stat 전용이고, **회복(`cover_heal_pct`)은 이 경로를 쓰지 않는다**
        (회복은 부서진 엄폐물을 되살리지 않는다는 규약이 그대로다). 같은 이유로
        `self_cover_alive`·`not_self_cover_alive` 판정이 뒤집히므로 캐시를 비운다."""
        self.state["cover_hp"][name] = max(0.0, hp)
        self._invalidate_buffs_cache()

    def cover_max_hp(self, name: str, t: float) -> float:
        """name의 엄폐물 최대 체력 — 기본값(`state["cover_base_hp"]`, 임의값) 위에 `cover_hp_pct`를 얹는다.

        원문이 둘이다.
          「엄폐물 최대 체력 N% ▲」(소장품 `마음의 버팀목`)          → 기본값 × N%, 합연산
          「시전자의 최대 체력 비례 엄폐물 최대 체력 N% ▲」(`scaling: max_hp` — 렐릭 커버 큐브,
            티아 `카멜레온 은신술`)                                  → 시전자 최종 최대 체력 × N%
        기본값이 임의값이어도 배율은 얹는다(유저 결정 2026-09-15)."""
        base = self.state.get("cover_base_hp", {}).get(name, 0.0)
        pct = flat = 0.0
        for ab in self._by_stat("cover_hp_pct"):
            if not self._live(ab, name, t):
                continue
            val = self._get_value(ab.effect, ab, name) or 0.0
            if ab.effect.get("scaling") == "max_hp":
                flat += self.effective_max_hp(ab.caster) * val / 100.0
            else:
                pct += val
        return base * (1.0 + pct / 100.0) + flat

    def sync_cover_hp(self, name: str, t: float) -> None:
        """엄폐물 최대 체력의 변화를 현재 체력에 옮긴다. 늘면 늘어난 만큼 함께 차고, 줄면 넘친 만큼
        잘린다 — 니케 `max_hp_pct`(최대 체력 + 현재 체력 동반 증가)와 같은 규약이다. 부서진 엄폐물은
        되살아나지 않는다. timeline이 보스 패턴이 있을 때만 프레임마다 부른다."""
        cur, mx = self.state["cover_hp"], self.state["cover_max_hp"]
        new = self.cover_max_hp(name, t)
        prev = mx[name]
        if new == prev:
            return
        if cur[name] > 0.0:
            cur[name] = min(cur[name] + max(new - prev, 0.0), new)
        mx[name] = new

    def take_next_shield_amp(self, name: str, t: float) -> float:
        """name에게 걸린 「다음 보호막 체력 N% ▲」(`next_shield_hp_pct`)를 꺼내 쓰고 N 합을 돌려준다.

        보호막이 **name에게 적용되는 순간** 한 번 소모된다 — 누가 만든 보호막이든 받는 쪽 기준이다
        (델타 : 닌자 시프 `비기 : 닌자 오버드라이브 4`). 여럿이면 합산하고 전부 소모한다
        (둘 다 유저 확인 2026-09-15)."""
        used = [ab for ab in self._by_stat("next_shield_hp_pct") if self._live(ab, name, t)]
        if not used:
            return 0.0
        amp = sum(self._get_value(ab.effect, ab, name) or 0.0 for ab in used)
        for ab in used:
            ab.target_chars = [c for c in ab.target_chars if c != name]
            if self._buff_event_handler and ab.effect.get("name"):
                self._buff_event_handler("expire", ab.effect["name"], ab.caster, name, t, t)
        drop = {ab.uid for ab in used if not ab.target_chars}
        if drop:
            self._active = [ab for ab in self._active if ab.uid not in drop]
        self._invalidate_buffs_cache()
        return amp

    def absorb_shield(self, name: str, dmg: float, t: float, pierce: bool = False) -> float:
        """보호막이 이 피해를 받는다. 보호막들이 받은 양의 합(0이면 보호막 없음)을 돌려준다.

        보호막은 **각자 따로 작동한다**(유저 확인 2026-09-15).
          비관통 — **나중에 생긴 보호막 하나만** 맞는다. 남은 피해는 넘어가지 않는다(유저 확인) —
                   그 보호막이 깨지더라도 한 발은 거기서 끝난다. ⬜ 순서는 인게임 미확인, 잠정.
          관통   — 살아 있는 보호막 **전부가 같은 피해를 각각** 받는다.
        다 깎인 보호막마다 `event:shield_consumed`.
        """
        live = [ab for ab in self._active
                if ab.effect.get("stat") in _SHIELD_STATS
                and ab.shield_per_target.get(name, 0.0) > 0.0 and t < ab.expires_at]
        if not live:
            return 0.0
        # 나중에 생긴 것부터 — 같은 시각이면 목록 뒤(나중에 붙은) 쪽. 재발동은 activated_at이 갱신된다
        order = sorted(range(len(live)), key=lambda i: (live[i].activated_at, i), reverse=True)
        total = 0.0
        ended: list[ActiveBuff] = []
        for i in (order if pierce else order[:1]):
            ab = live[i]
            # 「자신이 설치한 보호막 무적」(`shield_invincible`) — **그 보호막을 만든
            # 시전자**가 무적을 들고 있으면 막아 내되 잔량이 줄지 않는다. 대상이 아니라
            # 시전자로 가르므로 남이 걸어 준 보호막은 그대로 깎인다. 판정을 흡수 시점에
            # 두었기 때문에 같은 프레임에 무적과 보호막이 함께 걸릴 때의 **항목 순서와
            # 무관**하다. `invincible`이 체력 피해만 0으로 하는 것의 짝이다 — 층이 다를 뿐
            # 「그 층에서 피해가 멈춘다」는 같다. (레이블 `망상 공유`)
            if self.has_live_stat(ab.caster, "shield_invincible", t):
                total += dmg
                continue
            left = ab.shield_per_target[name]
            taken = min(left, dmg)
            ab.shield_per_target[name] = left - taken
            total += taken
            if ab.shield_per_target[name] <= 0.0:
                ab.shield_per_target[name] = 0.0
                # `during_shield` 판정이 바뀌므로 집계 캐시를 비운다
                self._invalidate_buffs_cache()
                if ab.effect.get("end_on_shield_consumed") and not any(
                    v > 0.0 for v in ab.shield_per_target.values()
                ):
                    ended.append(ab)
                self.notify("event:shield_consumed", t, name)
        for ab in ended:
            self._end_shield_carrier(ab, t)
        return total

    def decoy_capacity(self, name: str) -> float:
        """name이 가진 분신의 **최대치** 총합 — 부여 시점 값(`decoy_max_per_target`)의 합."""
        return sum(
            ab.decoy_max_per_target.get(name, 0.0)
            for ab in self._active
            if ab.effect.get("stat") in _DECOY_STATS
        )

    def has_decoy(self, name: str) -> bool:
        """name이 살아 있는 분신을 가지고 있는지."""
        return any(
            ab.decoy_per_target.get(name, 0.0) > 0.0
            for ab in self._active
            if ab.effect.get("stat") in _DECOY_STATS
        )

    def absorb_decoy(self, name: str, dmg: float, t: float) -> float:
        """분신이 이 피해를 받는다. 받은 양을 돌려준다(0이면 분신 없음).

        **보호막·엄폐물보다 안쪽, 체력 바로 앞 층이다**(유저 2026-09-21 — 신데렐라 계열의
        인게임 거동). 호출은 `timeline._land_squad`가 하며, **엄폐 중이 아닐 때만** 부른다:
        엄폐물이 *엄폐 중에* 대신 맞는 것의 짝으로 분신은 니케가 나와서 *사격 중일 때*
        대신 맞는다. 그래서 엄폐물과 분신은 사실상 배타다.

        보호막과 같이 **나중에 생긴 것 하나만** 맞고 남은 피해는 넘어가지 않는다.
        관통(`pierce`)은 가르지 않는다 — 관통은 막을 뚫는 성질이고 분신은 다른 개체다.

        체력이 0이 되면 분신은 **사라진다**(담체 버프를 끝낸다). 보호막의
        `end_on_shield_consumed`처럼 옵트인이 아니라 기본 동작이다 — 부서진 분신이 `_active`에
        남으면 `self_state:디코이`가 계속 참이라 그 상태를 읽는 효과(신데렐라 `아름다움`,
        라이의 디코이 회복 둘)가 없는 분신을 계속 회복·참조한다.

        ⬜ **인게임 미확인 둘**(`docs/DATA_VERIFY.md` §보스 → 니케 피해):
        ① 「사격 중일 때 대신 맞는다」는 유저의 기억이고 확정이 아니다.
        ② 적이 분신을 실제로 *조준*하는지(= 도발처럼 공격 대상 자체가 바뀌는지), 아니면
           주인이 맞은 피해를 분신이 대신 받는지. 여기서는 후자(흡수 층)로 모델링한다 —
           조준을 바꾸려면 `_attack_targets` 전체를 건드려야 하고, 분신은 공격 대상 목록에
           없는 개체라 「누가 몇 발을 맞는가」가 통째로 달라진다.
        **보스 공격 패턴이 없는 기본 경로에서는 어느 쪽이든 딜 기여가 0이다.**
        """
        live = [ab for ab in self._active
                if ab.effect.get("stat") in _DECOY_STATS
                and ab.decoy_per_target.get(name, 0.0) > 0.0 and t < ab.expires_at]
        if not live:
            return 0.0
        ab = max(live, key=lambda x: (x.activated_at, x.uid))
        taken = min(ab.decoy_per_target[name], dmg)
        ab.decoy_per_target[name] = ab.decoy_per_target[name] - taken
        if ab.decoy_per_target[name] <= 0.0:
            ab.decoy_per_target[name] = 0.0
            self._invalidate_buffs_cache()
            if not any(v > 0.0 for v in ab.decoy_per_target.values()):
                self._end_shield_carrier(ab, t)
        return taken

    def heal_decoy(self, name: str, amount: float, t: float) -> float:
        """name의 분신을 amount만큼 되돌린다 — 실제로 되돌린 양을 반환한다.

        **깎인 만큼만 채운다.** 상한은 부여 시점 값(`decoy_max_per_target`)이고, 없는 분신을
        새로 만들지는 않는다 — `heal_shield`·`cover_heal_pct`와 같은 규약이다(생성은 `decoy`).
        부서진 분신은 담체가 이미 끝나 `_active`에 없으므로 후보에서 빠진다.
        """
        if amount <= 0.0:
            return 0.0
        live = [ab for ab in self._active
                if ab.effect.get("stat") in _DECOY_STATS
                and name in ab.decoy_max_per_target and t < ab.expires_at]
        if not live:
            return 0.0
        live.sort(key=lambda ab: ab.activated_at, reverse=True)
        left, total = amount, 0.0
        for ab in live:
            room = ab.decoy_max_per_target[name] - ab.decoy_per_target.get(name, 0.0)
            if room <= 0.0:
                continue
            put = min(room, left)
            ab.decoy_per_target[name] = ab.decoy_per_target.get(name, 0.0) + put
            total += put
            left -= put
            if left <= 0.0:
                break
        if total > 0.0:
            self._invalidate_buffs_cache()
        return total

    def _end_shield_carrier(self, ab: "ActiveBuff", t: float) -> None:
        """다 깎인 보호막의 담체 버프를 끝낸다 — `end_on_shield_consumed` 전용.

        **부서진 분신(`absorb_decoy`)도 같은 자리를 쓴다.** 그쪽은 옵트인이 아니라 기본
        동작이다 — 분신은 막이 아니라 개체라 체력이 0이면 그냥 없어진다.

        보호막이 곧 상태인 버프는 보호막이 없어지면 상태도 없어져야 한다. 그러지 않으면
        `shield_per_target`만 0이 되고 `_active`에는 남아 **`self_state:[이름]`이 계속 참**이라
        「보호막이 없을 때」 분기가 어느 모드에서도 열리지 않는다(킬로 `나노 코팅` — 스킬
        셋 중 절반이 그 분기에 있다). `during_shield`는 `has_shield()`가 잔량을 보므로 이미
        정상적으로 꺼진다 — 어긋나 있던 것은 이름 상태 쪽뿐이다.

        **효과 단위 옵트인이다.** ⬜ 인게임에서는 모든 보호막이 이럴 가능성이 높지만 확인된
        것만 켠다 — 일괄로 켜면 이름이 상태로 참조되는 다른 보호막의 발동 시점이 앞당겨진다
        (폴리 `폴리스 뱃지` → `event:state_end:폴리스 뱃지` → `도그 테라피 3` 지속 회복).
        제거 절차는 `remove_named_buff`와 같은 자리를 쓴다.
        """
        name = ab.effect.get("name") or ""
        if ab not in self._active:
            return
        self._active = [x for x in self._active if x.uid != ab.uid]
        if not any(x.effect is ab.effect for x in self._active):
            self._dot_timers.pop(id(ab.effect), None)
            self._instant_timers.pop(id(ab.effect), None)
        self._invalidate_buffs_cache()
        if self._buff_event_handler and name:
            for tgt in (ab.target_chars or []):
                self._buff_event_handler("expire", name, ab.caster, tgt, t, t)
        if name:
            self.notify(f"event:state_end:{name}", t, ab.caster)

    def heal_shield(self, name: str, amount: float, t: float) -> float:
        """name의 보호막을 amount만큼 되돌린다 — 실제로 되돌린 양을 반환한다.

        **깎인 만큼만 채운다.** 상한은 각 보호막의 부여 시점 값(`shield_max_per_target`)이고,
        `absorb_shield`와 같은 순서(나중에 생긴 것부터)로 채운다 — 그쪽이 먼저 깎이는 층이라
        같은 층을 되돌리는 것이 자연스럽다. ⬜ 층 순서는 흡수 쪽과 같이 잠정이다.
        **없는 보호막을 새로 만들지는 않는다** — `cover_heal_pct`가 부서진 엄폐물을 되살리지
        않는 것과 같은 자리다(그쪽의 예외는 `cover_revive`가 따로 연다). 담체가 이미
        `end_on_shield_consumed`로 끝났다면 `_active`에 없으므로 후보에서 빠진다.
        """
        if amount <= 0.0:
            return 0.0
        live = [ab for ab in self._active
                if ab.effect.get("stat") in _SHIELD_STATS
                and name in ab.shield_max_per_target and t < ab.expires_at]
        if not live:
            return 0.0
        live.sort(key=lambda ab: ab.activated_at, reverse=True)
        left, total = amount, 0.0
        for ab in live:
            room = ab.shield_max_per_target[name] - ab.shield_per_target.get(name, 0.0)
            if room <= 0.0:
                continue
            put = min(room, left)
            ab.shield_per_target[name] = ab.shield_per_target.get(name, 0.0) + put
            total += put
            left -= put
            if left <= 0.0:
                break
        if total > 0.0:
            # 0이던 보호막이 되살아나면 `during_shield`가 다시 참이 된다
            self._invalidate_buffs_cache()
        return total

    def knock_down(self, name: str, t: float) -> None:
        """name을 전투불능으로 만든다.

        **받은 버프는 전부 사라지고 준 버프는 남는다**(유저 확인 2026-09-15) — 영구 버프(장비·큐브·
        소장품·지속 패시브)도 사라지고, 부활할 때 패시브만 다시 붙는다(`_reapply_passives`).
        `[부활 시 유지]`(`persist_on_revive`)는 남는다.

        **게이지·스택·발동 횟수도 초기화된다**(유저 확인 2026-09-15) — 개인 게이지(`state["gauges"]`)와
        「N번째 버스트 시」 같은 회수별 효과의 카운터(`_event_counts`, 리타 스킬1 `burst_cast_count:N`)가
        0부터 다시 센다. 스쿼드 공용 카운터(`__squad__`)와 `max_trigger`(전투 중 N회)는 그대로다.
        """
        down = self.state.setdefault("down", set())
        if name in down:
            return
        down.add(name)
        self.state["hp"][name] = 0.0
        self.state["hp_pct"][name] = 0.0
        lost: list[tuple[dict, str]] = []
        kept: list[ActiveBuff] = []
        for ab in self._active:
            chars = ab.target_chars
            if chars and name in chars and not ab.effect.get("persist_on_revive"):
                if self._buff_event_handler and ab.effect.get("name"):
                    self._buff_event_handler("expire", ab.effect["name"], ab.caster, name, t, t)
                if ab.expires_at == math.inf:
                    lost.append((ab.effect, ab.caster))
                rest = [c for c in chars if c != name]
                if not rest:
                    continue
                ab.target_chars = rest
                ab.bullets_per_target.pop(name, None)
                ab.per_char_stacks.pop(name, None)
                ab.shield_per_target.pop(name, None)
            kept.append(ab)
        self._active = kept
        self._down_lost[name] = lost
        self._event_counts.pop(name, None)
        gauges = self.state.get("gauges", {}).get(name)
        if gauges:
            for key in gauges:
                if not key.startswith("_gauge_max:"):   # 최대치 선언은 게이지 값이 아니다
                    gauges[key] = 0.0
        stacks = self.state.get("stacks", {}).get(name)
        if stacks:
            stacks.clear()
        if name in self.state.get("weapon_change", {}):
            self.end_weapon_change(name, t)
        self._invalidate_buffs_cache()

    def notify_down(self, name: str, t: float) -> None:
        """전투불능 이벤트. **`knock_down()`과 부르는 쪽의 정리가 다 끝난 뒤에** 쏜다 —
        `event:ally_down`이 같은 호출 안에서 부활(마나 `매터 감마 3`)을 낳으므로, 통지가 먼저
        나가면 부활한 니케를 뒤늦은 정리가 도로 멈춰 세운다."""
        self.notify("event:self_down", t, name)
        for other in self._alive():
            self.notify("event:ally_down", t, other)

    def revive(self, name: str, t: float, hp_pct: float) -> None:
        down = self.state.get("down")
        if not down or name not in down:
            return
        down.discard(name)
        # 패시브를 먼저 붙인다 — 부활 체력 %는 패시브(최대 체력 ▲ 등)가 반영된 최대 체력 기준이다.
        # 그동안 체력 전이 이벤트가 나가지 않게 비율을 비워 둔다.
        self.state["hp_pct"][name] = None
        self._reapply_passives(name, t)
        self.state["hp"][name] = self.effective_max_hp(name) * hp_pct / 100.0
        self.state["hp_pct"][name] = None      # 전이 이벤트 없이 다시 잰다
        self.sync_hp(name)
        self._invalidate_buffs_cache()

    def _reapply_passives(self, name: str, t: float) -> None:
        """부활 — 전투불능 때 잃은 영구 버프 중 **패시브**를 다시 붙인다(유저 확인 2026-09-15).

        패시브 = 전투 시작에 붙는 상시 버프(`passive`·`battle_start` 타이밍 — 장비·큐브·소장품·
        지속 패시브). 그 밖의 영구 버프(「N번째 풀버스트 시 [지속]」 등)는 계기가 다시 와야 붙는다.

        다른 아군에게 아직 걸려 있는 효과는 새로 발동하지 않고 name을 대상에 되돌린다 — 새로 발동하면
        나머지 아군에게 한 번 더 걸려 스택형은 중첩이 오른다. 순위로 고른 대상(지연 resolve)은 그때
        고른 결과라 되돌리지 않는다. 시전자가 아직 쓰러져 있으면 새로 발동하지 않는다(쓰러진 니케의
        스킬은 발동하지 않는다)."""
        down = self.state.get("down") or ()
        for eff, caster in self._down_lost.pop(name, []):
            if eff.get("type") != "buff" or eff.get("duration_bullets", -1) != -1:
                continue
            timings = eff["trigger"]["timing"]
            if "passive" not in timings and "battle_start" not in timings:
                continue
            ab = next((a for a in self._active if a.effect is eff and a.caster == caster), None)
            if ab is not None:
                raw = eff.get("target", "self")
                if (ab.target_chars is None or name in ab.target_chars
                        or (isinstance(raw, str) and raw.startswith(_LAZY_RESOLVE_PREFIXES))):
                    continue
                ab.target_chars = ab.target_chars + [name]
                self._invalidate_buffs_cache()
                if self._buff_event_handler and eff.get("name"):
                    self._buff_event_handler("activate", eff["name"], caster, name, t, ab.expires_at,
                                             self._get_value(eff, ab, name), eff.get("stat"))
                continue
            if caster in down:
                continue
            conds = eff["trigger"].get("condition", [])
            ok = not conds or self._condition_ok(conds, caster, t, eff)
            if "passive" in timings:
                if _is_cond_finite_passive(eff):
                    # 유한 지속은 조건이 참일 때만 건다 (`_notify`와 같은 이유)
                    if ok:
                        self._activate(eff, caster, t)
                else:
                    # 조건부 passive는 `_notify`와 같이 조건과 무관하게 등록한다 — 게이팅은 런타임 조건이 한다
                    self._activate(eff, caster, t, suppress_event=not ok)
            elif ok:
                self._activate(eff, caster, t)

    def add_burst_gauge(self, amount: float, t: float,
                        caster: str = "", source: str = "") -> float:
        """공용 버스트 게이지에 가산하고 실제로 들어간 양을 돌려준다.

        **충전 창·상한·폐기 규칙을 여기 한 곳에 가둔다.** 그래서 부르는 쪽(발사·차지·
        스킬 대미지·instant 핸들러)은 "얼마를 만들었나"만 알면 되고 언제 충전되는지는
        몰라도 된다.

        - 게이지는 스쿼드 공용 1개다 (캐릭터별이 아니다).
        - **풀버스트가 끝나기 전까지는 충전되지 않는다** (유저 인게임 확인). 그 조건이
          `BurstController._phase == "idle"`과 정확히 같아서, 컨트롤러가 매 tick
          `state["burst_gauge_charging"]`에 그 값을 실어 준다.
        - 만충 100 초과분은 버려진다 (유저 인게임 확인) — 그래서 min()이지 이월이 아니다.

        정본: docs/mechanics/버스트 게이지.md
        """
        if amount <= 0.0 or not self.state.get("burst_gauge_charging", False):
            return 0.0
        cur = self.state.get("burst_gauge", 0.0)
        new = min(100.0, cur + amount)
        self.state["burst_gauge"] = new
        added = new - cur
        if self._gauge_event_handler is not None and added > 0.0:
            self._gauge_event_handler(t, caster, source, added, new)
        return added

    def sync_hp(self, name: str):
        """state['hp']를 기준으로 state['hp_pct']를 재계산한다.

        등록된 `event:adjacent_hp_below:N` 임계값을 위에서 아래로 통과하거나,
        100% 미만 → 100% 복귀 전이에서 `event:adjacent_hp_max`를 발생시킨다
        (양 옆 아군을 관찰하는 캐릭터에게만). 상시 만피 상태에서는 전이가
        없으므로 반복 발동하지 않는다 — 플로라 아이리스의 발동 경로.
        """
        hp = self.state.get("hp", {}).get(name)
        if hp is None:
            return
        max_hp = self.effective_max_hp(name)
        if max_hp <= 0:
            return
        prev_pct = self.state["hp_pct"].get(name)
        new_pct = hp / max_hp * 100.0
        self.state["hp_pct"][name] = new_pct

        if prev_pct is not None:
            if new_pct < prev_pct:
                self._notify_hp_below(name, prev_pct, new_pct)
                self._notify_adjacent_hp_below(name, prev_pct, new_pct)
            elif prev_pct < 100.0 - _HP_EPS <= new_pct:
                self._notify_adjacent_hp_max(name)

    def _notify_hp_below(self, changed: str, prev_pct: float, new_pct: float):
        """체력 임계값 하향 통과 — 본인의 `hp_below:T`(`hp_below_count:T:N`의 이벤트)와
        스쿼드 전원의 `event:ally_hp_below:N`(「자신을 포함한 아군 누군가의 체력이 N% 이하 도달 시」).
        판정은 인접판과 같다: 직전 > T 이고 지금 ≤ T."""
        if self._in_hp_edge:
            return
        self._in_hp_edge = True
        try:
            crossed = lambda thr: prev_pct > thr + _HP_EPS and new_pct <= thr + _HP_EPS
            for observer in self.squad_names:
                for event in self._notify_index.get(observer, {}):
                    if observer == changed and event.startswith("hp_below:"):
                        raw = event[len("hp_below:"):]
                    elif event.startswith("event:ally_hp_below:"):
                        raw = event[len("event:ally_hp_below:"):]
                    else:
                        continue
                    try:
                        thr = float(raw)
                    except ValueError:
                        continue
                    if crossed(thr):
                        self.notify(event, self._cur_t, observer)
        finally:
            self._in_hp_edge = False

    def _notify_adjacent_hp_below(self, changed: str, prev_pct: float, new_pct: float):
        """changed가 관찰자의 인접 HP 임계값을 하향 통과한 이벤트를 알린다."""
        if self._in_hp_edge:
            return
        self._in_hp_edge = True
        try:
            for observer in self.squad_names:
                if observer == changed:
                    continue
                if changed not in self._resolve_target("allies_adjacent:2", observer):
                    continue
                event_keys = self._notify_index.get(observer, {})
                for event in event_keys:
                    prefix = "event:adjacent_hp_below:"
                    if not event.startswith(prefix):
                        continue
                    try:
                        threshold = float(event[len(prefix):])
                    except ValueError:
                        continue
                    if prev_pct > threshold + _HP_EPS and new_pct <= threshold + _HP_EPS:
                        self.notify(event, self._cur_t, observer)
        finally:
            self._in_hp_edge = False

    def _notify_adjacent_hp_max(self, changed: str):
        """changed가 최대 체력에 도달했음을 '양 옆에 changed를 둔' 아군에게 알린다.

        `event:adjacent_hp_max` timing은 관찰자 본인이 아니라 **이웃**의 도달을
        본다. 따라서 notify의 caster는 관찰자(효과 소유자)로 넘긴다.
        """
        if self._in_hp_edge:
            return
        self._in_hp_edge = True
        try:
            for observer in self.squad_names:
                if observer == changed:
                    continue
                if changed in self._resolve_target("allies_adjacent:2", observer):
                    self.notify("event:adjacent_hp_max", self._cur_t, observer)
        finally:
            self._in_hp_edge = False

    def _has_immune(self, char_name: str, immune_stat: str) -> bool:
        """char_name이 현재 immune_stat 버프를 가지고 있는지 확인."""
        buff_key = _STAT_TO_BUFF.get(immune_stat, immune_stat)
        for ab in self._active:
            if ab.effect.get("stat") != immune_stat:
                continue
            if char_name in (ab.target_chars or []):
                return True
        return False

    def is_stunned(self, char_name: str) -> bool:
        """char_name이 현재 기절(stun) 상태이면 True.

        stun_immune 버프가 있으면 기절 상태로 간주하지 않는다.
        결과는 _stunned_cache에 캐싱되며 _invalidate_buffs_cache 시 함께 초기화된다.
        """
        cached = self._stunned_cache.get(char_name)
        if cached is not None:
            return cached
        result = self._compute_is_stunned(char_name)
        self._stunned_cache[char_name] = result
        return result

    def _compute_is_stunned(self, char_name: str) -> bool:
        if self._has_immune(char_name, "stun_immune"):
            return False
        for ab in self._active:
            if ab.effect.get("stat") == "stun" and char_name in (ab.target_chars or []):
                return True
        return False

    def _expires_at(self, eff: dict, caster: str, t: float) -> float:
        """이 효과를 지금 걸면 언제 만료되는가. 종료 조건이 없으면 `inf`.

        `duration_scaling: "stack_count"`가 붙으면 **지속시간이 다른 상태의 중첩 수에
        비례**한다(원문 `[N초 X [상태명] 횟수만큼 유지]`). `duration`은 1중첩 분량이고
        기준은 `duration_scaling_ref`가 가리키는 이름이다 — `scaling: stack_count`가
        *값*에 하는 일을 지속시간에서 한다. 참조가 없거나 0중첩이면 0초이므로 사실상
        무발동인데, 그건 원문 그대로다(중첩이 0이면 「0초 유지」다). 그래서 이 문형의
        컨테이너 담체는 **배열 앞**에 둬야 한다 — 형제가 담체의 중첩을 읽기 때문이다
        (레이블 `상상 실연`·`망상 파괴 2`, `PARSING-CHARS.md` §레이블).
        """
        duration = eff.get("duration")
        if duration is None and "duration_values" in eff:
            char = self._char.get(caster, {})
            skill_lv = _get_skill_lv(char, eff)
            dv = eff["duration_values"]
            duration = float(dv.get(skill_lv, dv.get("10", 0.0)))
        if eff.get("duration_scaling") == "stack_count" and duration not in (None, -1):
            n = self.ref_count(caster, eff.get("duration_scaling_ref", ""))
            duration = float(duration) * (n if n is not None else 0)
        return math.inf if duration is None or duration == -1 else t + duration

    def _apply_buff_max_stack_add(self, eff: dict, caster: str, t: float) -> None:
        """「중첩 가능 이로운 효과 중첩량 N개 ▲」 — 대상 아군에게 걸린 **스택형 이로운 효과**의
        현재 중첩을 N 올린다(IMPL-STATUS `buff_max_stack_add`).

        - 상한(`max_stack`)은 그대로이고 넘기지 못한다 — 이미 최대 중첩인 버프는 no-op(유저 확인
          2026-09-21). 누가 건 버프든 가리지 않고, 해로운 효과·`max_stack` 1짜리는 건드리지 않는다.
        - 중첩이 실제로 오르면 지속시간도 갱신하고(`buff_stack_add`와 같은 일반 규칙 —
          GAMEPLAY §버프 스택) `stack_reach` 이벤트를 낸다.
        - `stack_change_immune`인 수령자는 뺀다.
        - **한 ActiveBuff가 수령자와 비수령자를 함께 대상으로 잡고 있으면 건너뛴다.** 중첩은
          ActiveBuff 하나에 공유되므로 올리면 비수령자까지 오른다. 코드 한정 공급원(`allies_code:`)이
          아군 전체 스택 버프를 만날 때만 생기는 근사다(`partial_skips`로 센다).
        """
        char = self._char.get(caster, {})
        if "fixed_value" in eff:
            n = int(eff["fixed_value"])
        else:
            vals = eff.get("values") or {}
            n = int(float(vals.get(_get_skill_lv(char, eff), vals.get("10", 1))))
        recipients = {
            c for c in (self._resolve_target(eff.get("target", "self"), caster) or [])
            if not _is_enemy(c) and not self._has_immune(c, "stack_change_immune")
        }
        if not recipients or n <= 0:
            return
        if self._instant_event_handler and eff.get("name"):
            for tgt in recipients:
                self._instant_event_handler(eff["name"], caster, tgt, t, "buff_max_stack_add", float(n))
        reached: list[tuple[str, int, str]] = []
        for ab in self._active:
            e = ab.effect
            if e.get("type") != "buff" or not str(e.get("polarity", "")).startswith("beneficial"):
                continue
            max_s = e.get("max_stack", 1)
            if max_s == 1:
                continue
            tgts = ab.target_chars
            if tgts is None:
                tgts = self._resolve_target(e.get("target", "self"), ab.caster) or []
            hit = [c for c in tgts if c in recipients]
            if not hit:
                continue
            if any(c not in recipients for c in tgts):
                self.partial_skips += 1
                continue
            cap = max_s if max_s != -1 else ab.stack + n
            prev = ab.stack
            ab.stack = min(ab.stack + n, cap)
            if ab.per_char_stacks:
                ab.per_char_stacks = {
                    c: (min(v + n, max_s) if max_s != -1 else v + n)
                    for c, v in ab.per_char_stacks.items()
                }
            if ab.stack == prev:
                continue
            self._invalidate_buffs_cache()
            duration = e.get("duration")
            if ab.expires_at != math.inf and duration is not None and duration > 0:
                ab.activated_at = t
                ab.expires_at = t + duration
            if e.get("name"):
                reached.append((e["name"], ab.stack, ab.caster))
                if self._buff_event_handler:
                    new_val = self._get_value(e, ab)
                    for tgt in hit:
                        self._buff_event_handler(
                            "activate", e["name"], ab.caster, tgt,
                            t, ab.expires_at, new_val, e.get("stat"),
                        )
        for name, stack, ab_caster in reached:
            self.notify(f"stack_reach:{name}:{stack}", t, ab_caster)

    def _activate(self, eff: dict, caster: str, t: float, suppress_event: bool = False,
                  targets: list[str] | None = None):
        """효과를 ActiveBuff로 변환해 활성 목록에 추가하거나 갱신.

        `targets`를 주면 효과의 `target` 문자열을 해석하지 않고 그 대상에게 건다 — 보스가 건 효과
        (`apply_boss_effect`)만 쓴다. buff 타입만 받는다."""
        # max_trigger: 전투 중 최대 발동 횟수 제한
        #
        # **대상이 0기면 발동권을 쓰지 않는다** (유저 결정 2026-09-21). 카운터를 대상 해석보다
        # 먼저 깎으면, 게이트가 condition이 아니라 **대상**에만 있는 효과는 아무에게도 닿지
        # 못한 첫 트리거에서 한 장뿐인 발동권을 태우고 영구히 죽는다 — 앤 : 미라클 페어리
        # `파란 나비의 꿈 3`(「전투불능 상태 화력형 아군 무작위 1기에게 [부활] [전투 중 1회 발동]」)이
        # 그 첫 사례다. 조건절이 없어 매 버스트에 트리거가 서고, 쓰러진 아군이 없는 첫 버스트에
        # 카운터가 소모돼 보스 패턴에서도 발동하지 않았다.
        #
        # 지연 resolve 대상(`_LAZY_RESOLVE_PREFIXES`)과 보스가 건 효과(`targets` 지정)는
        # 여기서 대상을 확정하지 않으므로 종전대로 즉시 소모한다.
        max_trigger = eff.get("max_trigger")
        if max_trigger is not None:
            eid = id(eff)
            if self._trigger_counts.get(eid, 0) >= max_trigger:
                return
            raw_target = eff.get("target", "self")
            if (targets is None and isinstance(raw_target, str)
                    and not raw_target.startswith(_LAZY_RESOLVE_PREFIXES)
                    and not self._resolve_target(raw_target, caster)):
                return
            self._trigger_counts[eid] = self._trigger_counts.get(eid, 0) + 1

        if eff.get("type") == "instant":
            self._dispatch_instant(eff, caster, t)
            return

        # 「중첩 가능 이로운 효과 중첩량 N개 ▲」는 buff로 파싱돼 있지만(원문에 유지 블록이 없어
        # `duration: -1`) **즉발**이다 — 담체를 남기지 않고 그 자리에서 중첩만 올린다
        if eff.get("stat") == "buff_max_stack_add":
            self._apply_buff_max_stack_add(eff, caster, t)
            return

        if eff.get("type") == "damage":
            tick_interval = eff.get("tick_interval")
            if tick_interval and self._damage_handler:
                # tick_interval이 있으면 DoT 타이머 등록 (이미 활성이면 갱신)
                #
                # 첫 틱 위상은 두 유형이다 (GAMEPLAY.md §효과 실행 순서, 유저 조사):
                #   type 1 `tick_start: "immediate"` — 발동과 동시에 첫 틱 (미하라·아크레인저 블랙)
                #   type 2 (기본)                    — 발동 +interval부터        (디젤·밀크)
                # 회수는 양쪽 같다. 경계 처리는 tick()의 `limit` 참조.
                duration = eff.get("duration")
                expires = math.inf if duration is None or duration == -1 else t + duration
                max_stack = eff.get("max_stack", 1)
                # **중첩 없는 지속 대미지는 이름이 곧 인스턴스다** (GAMEPLAY §버프 스택 — `[N 중첩]` 없는
                # DoT는 재부여 시 병존하지 않고 갱신된다). 인스턴스 키는 효과 객체라, 같은 상태를 두 경로로
                # 부여하는 효과(쿠루미 `해킹` — 36명중 · 버스트, 하란 `바이러스 전이`)는 이게 없으면 한 적에게
                # 따로 겹쳐 틱이 두 번 들어갔다(유저 지시 2026-09-24 — 틱 복사는 잘못이다). 같은 시전자의
                # 같은 이름 DoT가 이미 돌고 있으면 그 인스턴스를 갱신하고, 이번 부여의 대상은 그 인스턴스에
                # 합친다(쫄몹이 있을 때만 갈린다). 주기 자동공격(`auto_damage`·소환체 `damage:N`)은 같은
                # 분기를 타지만 지속 대미지가 아니라 걸지 않는다.
                if eff.get("stat") == "dot_damage" and max_stack == 1 and eff.get("name"):
                    running = next((
                        ab for ab in self._active
                        if ab.caster == caster and ab.effect is not eff
                        and ab.effect.get("name") == eff["name"]
                        and ab.effect.get("stat") == "dot_damage"
                        and ab.effect.get("max_stack", 1) == 1
                        and id(ab.effect) in self._dot_timers
                    ), None)
                    if running is not None:
                        own_raw = eff.get("target", "self")
                        if (running.target_chars is not None and isinstance(own_raw, str)
                                and not own_raw.startswith(_LAZY_RESOLVE_PREFIXES)):
                            for tgt in self._resolve_target(own_raw, caster):
                                if tgt not in running.target_chars:
                                    running.target_chars.append(tgt)
                        eff = running.effect
                # **돌고 있는 지속 대미지를 다시 걸면 틱 박자는 그대로 잇고 만료만 갱신한다**
                # (유저 결정 2026-09-24 — 인게임은 재부여와 무관하게 초당 1틱씩 들어간다). 종전에는
                # 재부여마다 다음 틱을 「재부여 +interval」로 새로 잡아, 질 `산성탄 2`(재장전마다) ·
                # 쿠루미 `해킹`(36명중마다) · 레이븐 `쇼크웨이브`(풀차지마다 중첩 추가)처럼 박자보다
                # 촘촘히 다시 걸리는 DoT는 틱 간격이 벌어져 틱을 잃었다. 중첩 추가도 같은 재부여다.
                # 첫 부여(만료 뒤 다시 거는 것 포함)의 첫 틱 위상은 종전대로
                # type 1/2다(질 Q6 — type 2). 주기 자동공격(`auto_damage` 등)은 지속 대미지가
                # 아니라 종전대로 새로 잡는다.
                running_timer = self._dot_timers.get(id(eff))
                if eff.get("stat") == "dot_damage" and running_timer is not None:
                    first_t = running_timer[1]
                else:
                    first_t = t if eff.get("tick_start") == "immediate" else t + tick_interval
                self._dot_timers[id(eff)] = (caster, first_t, expires)
                # DoT는 _active에도 등록해야 target_state/debuff_cleanse/remove_named_buff
                # 등이 name·polarity 기준으로 조회할 수 있다.
                raw_target = eff.get("target", "self")
                lazy = isinstance(raw_target, str) and raw_target.startswith(_LAZY_RESOLVE_PREFIXES)
                targets = None if lazy else self._resolve_target(raw_target, caster)
                existing = next(
                    (ab for ab in self._active if ab.effect is eff and ab.caster == caster), None
                )
                # scaling_ref가 있는 DoT는 등록 시점 참조 스택/게이지 값을 초기 stack으로 캡처
                # (틱 발동 시 참조 버프가 이미 제거됐을 수 있으므로)
                scaling_ref = eff.get("scaling_ref", "")
                if scaling_ref and eff.get("scaling") == "stack_count":
                    ref_val = self.ref_count(caster, scaling_ref)
                    init_stack = ref_val if ref_val is not None else 1
                else:
                    init_stack = 1

                if existing:
                    # 재발동: 타이머 갱신은 위에서 됐으므로 스택/만료만 갱신
                    if max_stack == 1:
                        existing.expires_at = expires
                    elif scaling_ref and eff.get("scaling") == "stack_count":
                        # scaling_ref 기반 DoT: 재발동 시에도 참조값으로 스택을 재초기화
                        existing.stack = init_stack
                        existing.expires_at = expires
                    else:
                        cap = max_stack if max_stack != -1 else existing.stack + 1
                        existing.stack = min(existing.stack + 1, cap)
                        existing.expires_at = expires
                    if self._buff_event_handler and eff.get("name"):
                        for tgt in (existing.target_chars or []):
                            self._buff_event_handler("activate", eff["name"], caster, tgt, t, existing.expires_at, None, eff.get("stat"))
                else:
                    self._invalidate_buffs_cache()
                    self._active.append(ActiveBuff(
                        effect=eff, caster=caster, target_chars=targets,
                        activated_at=t, expires_at=expires, stack=init_stack,
                        has_runtime_conditions=_has_runtime_cond(
                            eff["trigger"].get("condition", []), expires,
                            eff.get("duration_bullets", -1)),
                    ))
                    if self._buff_event_handler and eff.get("name") and targets:
                        for tgt in targets:
                            self._buff_event_handler("activate", eff["name"], caster, tgt, t, expires, None, eff.get("stat"))

                # target이 `same_target:[이름]`인 DoT는 짝 효과가 **히트마다 한 중첩씩**
                # 얹고, 얹는 즉시 그 중첩 수로 1틱을 때린다 (사쿠라 : 블룸 인 서머
                # `화양연화 2` — 순차 10타 → 중첩 1→10, 배율 합이 삼각수 55).
                # 계산기는 순차 히트를 같은 t에 몰아 쏘므로 여기서 램프를 펼친다.
                ramp_n = self._same_target_ramp_hits(eff, caster)
                if ramp_n:
                    ab = next(
                        (a for a in self._active if a.effect is eff and a.caster == caster), None
                    )
                    if ab is not None:
                        # 램프는 짝 공격의 타간 간격(`ramp_interval`, 초)에 맞춰 펼친다.
                        # 지속 대미지는 **맞는 순간의 버프로 계산**되므로(유저 확인),
                        # 이 위상이 곧 각 틱이 풀버스트를 받느냐를 정한다.
                        gap = float(eff.get("ramp_interval", 0.22))
                        cap = max_stack if max_stack != -1 else ramp_n
                        self._ramp_pending = [
                            p for p in self._ramp_pending if not (p[1] is eff and p[2] == caster)
                        ]
                        for i in range(1, ramp_n + 1):
                            self._ramp_pending.append((t + i * gap, eff, caster, min(i, cap)))
                        # 지속시간은 **마지막 중첩 부여 기준**으로 다시 잡는다
                        # (GAMEPLAY §버프 스택 — 스택 부여는 지속시간도 갱신한다).
                        last_t = t + ramp_n * gap
                        duration = eff.get("duration")
                        expires = (math.inf if duration is None or duration == -1
                                   else last_t + duration)
                        ab.expires_at = expires
                        ab.stack = 0
                        # 주기 틱은 램프가 끝난 뒤 +interval부터 잇는다.
                        self._dot_timers[id(eff)] = (caster, last_t + tick_interval, expires)
            elif self._damage_handler:
                # tick_interval 없으면 즉시 1회 발동
                self._damage_handler(eff, caster, t)
            return

        if eff.get("type") == "weapon_change":
            # 발사 루프 교체는 타임라인이 처리하지만, 활성 여부는 state에 기록
            wc = self.state.setdefault("weapon_change", {})
            name = eff.get("name", "")
            # toggle: 진입과 해제가 같은 조건인 모드 — 이미 활성이면 이번 발동은 해제다
            # (신데렐라 : 크리스탈 웨이브 `저격 모드`)
            if eff.get("toggle") and name and self.weapon_change_name(caster) == name:
                self.end_weapon_change(caster, t)
                return
            duration = eff.get("duration")
            expires = math.inf if duration is None or duration == -1 else t + duration
            wc[caster] = {
                "effect": eff,
                "activated_at": t,
                "expires_at": expires,
            }
            self._invalidate_buffs_cache()
            # 모드 진입도 상태 변화다 — 일반 버프와 동일하게 event:{name}을 스쿼드에 브로드캐스트
            if name:
                for _sq in self.squad_names:
                    self.notify(f"event:{name}", t, _sq)
            return

        expires = self._expires_at(eff, caster, t)

        raw_target = eff.get("target", "self")
        if targets is not None:
            lazy = False
            targets = list(targets)
        else:
            lazy = isinstance(raw_target, str) and raw_target.startswith(_LAZY_RESOLVE_PREFIXES)
            targets = None if lazy else self._resolve_target(raw_target, caster)

        # harmful 효과: debuff_immune 또는 named debuff immunity인 대상 제거
        if eff.get("polarity") == "harmful" and targets is not None:
            targets = [c for c in targets if not self._harmful_blocked(c, eff)]
            if not targets:
                return

        max_stack = eff.get("max_stack", 1)

        duration_bullets = eff.get("duration_bullets", -1)
        # duration_bullets 버프: target이 확정된 경우 캐릭터별 독립 카운터 사용.
        # 미확정(lazy) target은 기존 bullets_left 방식 유지.
        use_per_target = duration_bullets != -1 and targets is not None

        # 동일 효과(name + caster + target) 기존 버프 탐색
        # target이 동적(lazy 아님)이면 target_chars까지 일치해야 같은 버프로 간주
        # 단, use_per_target(duration_bullets 다중 대상) 버프는 consume 시 target_chars가 줄어들므로
        # target_chars 비교 없이 effect + caster만으로 식별
        name = eff.get("name", "")
        existing = None
        for ab in self._active:
            if ab.effect is eff and ab.caster == caster:
                if lazy or use_per_target or ab.target_chars == targets:
                    existing = ab
                    break

        # **같은 이름의 버프는 한 상태다** (GAMEPLAY §버프 스택). 위 탐색의 키는 효과 객체라, 같은 상태를
        # 두 경로로 거는 효과는 이게 없으면 한 대상에게 따로 겹쳐 값이 두 배가 됐다 — 레이븐 `급소 공략`
        # (전투 시작 · 풀버스트 시작, 5초)이 첫 풀버스트 3.25~5.02초에 42.24%였다. 위 지속 대미지 분기
        # (쿠루미 `해킹`)와 같은 규칙의 버프판이다. 같은 시전자가 같은 이름·같은 stat을 **같은 대상에게**
        # 다시 걸면 살아 있는 인스턴스를 재발동과 똑같이 갱신한다(중첩형이면 중첩이 하나 오른다). 값은
        # 먼저 건 항목의 것을 쓰므로 항목끼리 값이 같아야 한다(`runner/doclint.py` 검사 M).
        # 대상이 다르면 따로 둔다(바니 모드 — 자신 · 짝). 조건을 런타임에 재평가하는 버프와 조건부 유한
        # passive는 조건이 곧 유효 구간이라 항목마다 따로 둔다 — 합치면 한쪽 조건이 다른 쪽을 켜고 끈다.
        # 발수 수명(`duration_bullets`)은 대상별 카운터라 합치지 않는다. 보스가 건 효과는 패턴이 닫힐 때
        # 그 패턴 것만 풀려야 해서(`release_boss_effects`) 합치지 않는다.
        if (existing is None and name and duration_bullets == -1 and not _is_enemy(caster)
                and not _has_runtime_cond(eff["trigger"].get("condition", []), expires)
                and not _is_cond_finite_passive(eff)):
            stat_key = eff.get("stat")
            for ab in self._active:
                other = ab.effect
                if (ab.caster == caster and other is not eff and t < ab.expires_at
                        and other.get("type") == "buff" and other.get("name") == name
                        and other.get("stat") == stat_key
                        and other.get("max_stack", 1) == max_stack
                        and other.get("duration_bullets", -1) == -1
                        and not ab.has_runtime_conditions
                        and not _is_cond_finite_passive(other)
                        and (other.get("target", "self") == raw_target if lazy
                             else ab.target_chars == targets)):
                    existing = ab
                    eff = other
                    break

        if existing:
            if max_stack == 1:
                existing.activated_at = t
                existing.expires_at = expires
                existing.trigger_count += 1
                if duration_bullets != -1:
                    if use_per_target:
                        # target_chars를 새로 resolve한 targets으로 복원 (이전 소모로 제거된 캐릭터 재추가)
                        existing.target_chars = list(targets)
                        existing.bullets_per_target = {c: duration_bullets for c in targets}
                    else:
                        existing.bullets_left = duration_bullets
            else:
                cap = max_stack if max_stack != -1 else existing.stack + 1
                prev_stack = existing.stack
                existing.stack = min(existing.stack + 1, cap)
                existing.activated_at = t
                existing.expires_at = expires
                existing.trigger_count += 1
                if duration_bullets != -1:
                    if use_per_target:
                        # 캐릭터별 독립 스택 갱신:
                        # - bullets_per_target에 남아있는 캐릭터(아직 발사 안 함): 기존 스택+1
                        # - 이미 발사해서 만료된 캐릭터: 스택 1로 초기화
                        new_per_char: dict[str, int] = {}
                        for c in targets:
                            if c in existing.bullets_per_target:
                                cur = existing.per_char_stacks.get(c, existing.stack) if existing.per_char_stacks else existing.stack
                                cap_c = max_stack if max_stack != -1 else cur + 1
                                new_per_char[c] = min(cur + 1, cap_c)
                            else:
                                new_per_char[c] = 1
                        existing.per_char_stacks = new_per_char
                        existing.target_chars = list(targets)
                        existing.bullets_per_target = {c: duration_bullets for c in targets}
                    else:
                        existing.bullets_left = duration_bullets
                # 스택이 새 값에 도달했으면 stack_reach 이벤트 발생
                if existing.stack != prev_stack and name:
                    self.notify(f"stack_reach:{name}:{existing.stack}", t, caster)
                # 스택 갱신 시에도 event:{name} notify (의존 버프 갱신용)
                # 기본은 스쿼드 전체 브로드캐스트, event_scope: "recipients"면 수령자 한정
                if name:
                    for _sq in self._event_audience(eff, existing.target_chars, caster):
                        self.notify(f"event:{name}", t, _sq)
            # 재발동이므로 참조 중첩도 이 시점 값으로 다시 고정
            existing.scaling_stack = self._capture_scaling_stack(eff, caster)

            # 갱신 이벤트: 만료 시각이 바뀌었으므로 activate로 재기록
            if self._buff_event_handler and name and existing.target_chars is None:
                existing.log_pending = True   # 위와 같은 이유로 resolve 시점까지 미룬다
            elif self._buff_event_handler and name:
                _stat = eff.get("stat")
                log_chars = existing.target_chars
                for tgt in (log_chars or []):
                    tgt_stack = existing.per_char_stacks.get(tgt) if existing.per_char_stacks else None
                    _val = self._get_value(eff, existing, caster, stack_override=tgt_stack)
                    self._buff_event_handler("activate", name, caster, tgt, t, existing.expires_at, _val, _stat)
        else:
            self._invalidate_buffs_cache()
            self._active.append(ActiveBuff(
                effect=eff,
                caster=caster,
                target_chars=targets,
                activated_at=t,
                expires_at=expires,
                stack=1,
                trigger_count=1,
                bullets_left=-1 if use_per_target else duration_bullets,
                bullets_per_target={c: duration_bullets for c in (targets or [])} if use_per_target else {},
                per_char_stacks={c: 1 for c in (targets or [])} if (use_per_target and max_stack != 1) else {},
                has_runtime_conditions=_has_runtime_cond(
                    eff["trigger"].get("condition", []), expires, duration_bullets),
                scaling_stack=self._capture_scaling_stack(eff, caster),
            ))
            name = eff.get("name", "")
            if name:
                # 기본은 스쿼드 전체 브로드캐스트, event_scope: "recipients"면 수령자 한정
                for _sq in self._event_audience(eff, targets, caster):
                    self.notify(f"event:{name}", t, _sq)
                # 스택 1로 처음 등록 시도 stack_reach:버프명:1
                self.notify(f"stack_reach:{name}:1", t, caster)
            # 신규 등록 이벤트 (suppress_event=True이면 억제 — 조건부 passive 미충족 시)
            if self._buff_event_handler and name and not suppress_event:
                if targets is None:
                    # 지연 resolve: 대상이 아직 없다. _resolve_lazy()가 확정하는 순간 남긴다
                    self._active[-1].log_pending = True
                elif targets:
                    ab_new = next((ab for ab in self._active if ab.effect is eff and ab.caster == caster), None)
                    _val = self._get_value(eff, ab_new, caster) if ab_new else None
                    _stat = eff.get("stat")
                    for tgt in targets:
                        self._buff_event_handler("activate", name, caster, tgt, t, expires, _val, _stat)

        # 「누적 → 폭발」 누적기: 부여 시점에 상한을 스냅샷하고 누적을 0에서 다시 시작한다.
        # **재평가가 아니라 스냅샷인 것이 이 메카닉의 작동 조건이다**(유저 결정 2026-09-22) —
        # 매 프레임 재면 버스트의 공격력 ▲(트로니 +101.37% → 최종 공격력 2.20배)가 상한을
        # 누적 가속(2.26배)과 같은 폭으로 밀어 올려 문턱이 영영 안 열린다.
        if eff.get("stat", "") in self._ACCUM_STATS:
            _ab = next((ab for ab in self._active
                        if ab.effect is eff and ab.caster == caster), None)
            if _ab is not None:
                _pct = self._get_value(eff, _ab, caster) or 0.0
                _ab.accum = 0.0
                _ab.accum_done = False
                _ab.accum_cap = self.final_atk(caster, t) * _pct / 100.0

        # event:stat_applied:XXX — stat 유형별 버프 적용 시 해당 target_chars에게 notify
        stat = eff.get("stat", "")
        _STAT_APPLIED_EVENTS = {"dot_dmg_pct", "split_dmg_pct"}
        if stat in _STAT_APPLIED_EVENTS and targets:
            event_name = f"event:stat_applied:{stat}"
            for tgt in targets:
                if not _is_enemy(tgt):
                    self.notify(event_name, t, tgt)

        # 보호막을 ActiveBuff 수명에 결합해 대상별 생성량을 기록한다. 보호막 상태를
        # 먼저 만든 뒤 적용 이벤트를 쏴야, 같은 프레임의 during_shield 판정이 참이다.
        # 분신(`decoy`)도 같은 모양으로 대상별 체력을 잡는다 — 원문이 「시전자의 **최종** 최대 체력
        # 비례 N% 분신」이라 기준·환산이 보호막과 같다. 재발동하면 체력과 상한이 함께 새로 잡힌다
        # (신데렐라 계열은 버스트마다 다시 세운다).
        if stat in _DECOY_STATS and targets:
            ab_ref = next(
                (ab for ab in self._active if ab.effect is eff and ab.caster == caster),
                None,
            )
            if ab_ref is not None:
                val = self._get_value(eff, ab_ref, caster)
                amount = self.effective_max_hp(caster) * val / 100.0 if val is not None else 0.0
                ab_ref.decoy_per_target = {
                    tgt: amount for tgt in (ab_ref.target_chars or []) if not _is_enemy(tgt)
                }
                ab_ref.decoy_max_per_target = dict(ab_ref.decoy_per_target)

        if stat in _SHIELD_STATS and targets:
            ab_ref = next(
                (ab for ab in self._active if ab.effect is eff and ab.caster == caster),
                None,
            )
            if ab_ref is not None:
                val = self._get_value(eff, ab_ref, caster)
                amount = self.effective_max_hp(caster) * val / 100.0 if val is not None else 0.0
                # 「다음 보호막 체력 ▲」는 받는 대상마다 그 순간 소모된다. 없으면 0이라 곱해도 같은 값이다
                ab_ref.shield_per_target = {
                    tgt: amount * (1.0 + self.take_next_shield_amp(tgt, t) / 100.0)
                    for tgt in (ab_ref.target_chars or []) if not _is_enemy(tgt)
                }
                # 회복 상한(`shield_heal_pct`)은 부여 시점 값이다 — 재발동하면 같이 새로 잡힌다
                ab_ref.shield_max_per_target = dict(ab_ref.shield_per_target)
                for tgt in ab_ref.shield_per_target:
                    self.notify("event:shield_applied", t, tgt)

        # debuff_immune_count 재부여 — 그 이름의 소모량을 되돌린다(잔량 재충전).
        if stat == "debuff_immune_count" and targets:
            key = eff.get("name", "")
            for tgt in targets:
                self._immune_used.pop((tgt, key), None)

        # max_hp_from_max_hp_pct 발동 후처리 — 「시전자의 최종 최대 체력 비례 최대 체력 N% ▲」.
        # 「최대 체력만」이 아니므로 현재 체력도 같이 오른다(`max_hp_pct`와 같은 쪽).
        # 가산분은 **부여 시점의 시전자 effective_max_hp** 기준으로 확정해 버프에 싣는다.
        if stat == "max_hp_from_max_hp_pct" and "hp" in self.state:
            ab_ref = next((ab for ab in self._active if ab.effect is eff and ab.caster == caster), None)
            if ab_ref is not None and targets:
                full_val = self._get_value(eff, ab_ref, caster)  # 현재 스택 기준 전체값
                if full_val is not None:
                    # 재발동이면 **직전 스냅샷을 먼저 걷어내고** 잰다. 시전자가 자기 대상이면
                    # 자기 증가분 위에 다시 N%가 얹혀 갱신할 때마다 복리로 불어난다.
                    prev = ab_ref.hp_bonus_flat
                    ab_ref.hp_bonus_flat = 0.0
                    ab_ref.hp_bonus_flat = self.effective_max_hp(caster) * full_val / 100.0
                    delta = ab_ref.hp_bonus_flat - prev
                    for tgt in targets:
                        if tgt in self.state["hp"]:
                            if delta > 0:
                                self.state["hp"][tgt] = min(
                                    self.state["hp"][tgt] + delta,
                                    self.effective_max_hp(tgt),
                                )
                            self.sync_hp(tgt)

        # hp_caster_based_pct / hp_only_caster_based_pct 발동 후처리
        if stat in ("hp_caster_based_pct", "hp_only_caster_based_pct") and "hp" in self.state:
            ab_ref = next((ab for ab in self._active if ab.effect is eff and ab.caster == caster), None)
            if ab_ref is not None and targets:
                if stat == "hp_caster_based_pct":
                    caster_base_hp = self.state.get("base_stats", {}).get(caster, {}).get("hp", 0.0)
                    full_val = self._get_value(eff, ab_ref, caster)
                    unit_val = (full_val / ab_ref.stack) if (full_val is not None and ab_ref.stack > 0) else None
                    if unit_val is not None and caster_base_hp > 0:
                        for tgt in targets:
                            if tgt in self.state["hp"]:
                                self.state["hp"][tgt] = min(
                                    self.state["hp"][tgt] + caster_base_hp * unit_val / 100.0,
                                    self.effective_max_hp(tgt),
                                )
                                self.sync_hp(tgt)
                else:  # hp_only_caster_based_pct: 최대 체력만 증가, 현재 체력 유지
                    for tgt in targets:
                        if tgt in self.state["hp"]:
                            self.sync_hp(tgt)

        # max_hp_pct / max_hp_only_pct 발동 후처리
        if stat in ("max_hp_pct", "max_hp_only_pct") and "hp" in self.state:
            ab_ref = next((ab for ab in self._active if ab.effect is eff and ab.caster == caster), None)
            if ab_ref is not None and targets:
                if stat == "max_hp_pct":
                    # 현재 체력 동반 증가: 이번 스택 1회분 단위값만큼 hp 가산
                    base_hp = self.state.get("base_stats", {}).get(caster, {}).get("hp", 0.0)
                    full_val = self._get_value(eff, ab_ref, caster)  # 현재 스택 기준 전체값
                    unit_val = (full_val / ab_ref.stack) if (full_val is not None and ab_ref.stack > 0) else None
                    if unit_val is not None and base_hp > 0:
                        for tgt in targets:
                            if tgt in self.state["hp"]:
                                self.state["hp"][tgt] = min(
                                    self.state["hp"][tgt] + base_hp * unit_val / 100.0,
                                    self.effective_max_hp(tgt),
                                )
                                self.sync_hp(tgt)
                else:
                    # 최대 체력만 증가: hp 절대값 변화 없음, hp_pct만 재동기화
                    for tgt in targets:
                        if tgt in self.state["hp"]:
                            self.sync_hp(tgt)

    # ── 틱 (every:Ns 처리 + 만료 정리) ──────────────────────────────────

    def tick(self, t: float):
        """
        매 프레임(또는 적절한 간격)마다 호출.
        - 만료 버프 제거
        - every:Ns 효과 발동 체크
        """
        self._cur_t = t

        # ── `same_target:[이름]` DoT 중첩 램프 ────────────────────────────
        #
        # 짝 공격이 한 발 맞을 때마다 중첩이 하나 붙고, 붙는 즉시 그 중첩 수로 1틱이
        # 들어간다. 아래 주기 틱보다 **먼저** 처리해야 같은 프레임에서 중첩이 앞서 반영된다.
        if self._damage_handler and self._ramp_pending:
            due = [p for p in self._ramp_pending if t >= p[0]]
            if due:
                self._ramp_pending = [p for p in self._ramp_pending if t < p[0]]
                for _, eff, caster, stack in sorted(due, key=lambda p: p[0]):
                    ab = next(
                        (a for a in self._active if a.effect is eff and a.caster == caster), None
                    )
                    if ab is None:
                        continue
                    ab.stack = stack
                    self._damage_handler(eff, caster, t)

        # ── 주기 대미지(tick_interval) — 만료 정리보다 **먼저** 처리한다 ──────
        #
        # type 2의 마지막 틱은 버프가 끝나는 바로 그 시각에 떨어진다. 만료를 먼저 치우면
        # 그 틱만 버프 없이 계산돼 딜이 몇 분의 일로 줄어든다 — 인게임에서는 마지막 틱도
        # 버프를 받는다(유저 확인). 순서를 앞에 두는 것으로 "틱 시각을 살짝 당기는" 효과를 낸다.
        if self._damage_handler and self._dot_timers:
            eff_by_id = self._eff_by_id  # __init__에서 만든 id → eff 역참조 맵
            expired_dots = []
            for eid, (caster, next_t, expires_at) in self._dot_timers.items():
                eff = eff_by_id.get(eid)
                if eff is None:
                    expired_dots.append(eid)
                    continue
                # 만료 경계는 첫 틱 위상과 짝을 이룬다 — 양쪽 유형의 틱 회수를 같게 만든다.
                #   type 1 (즉시 첫 틱, 0·2·4·6·8) → 만료 시각의 틱은 발동하지 않는다
                #   type 2 (+interval, 2·4·6·8·10) → 만료 시각의 틱까지 발동한다
                if eff.get("tick_start") == "immediate":
                    limit = expires_at - _TICK_EPS
                else:
                    limit = expires_at + _TICK_EPS
                if next_t > limit:
                    expired_dots.append(eid)
                    continue
                if t >= next_t:
                    # DoT는 _activate 시점에 조건 통과 후 등록된 것이므로
                    # 틱마다 재검사 없이 무조건 발동한다.
                    #
                    # 만료 시각에 떨어지는 type 2의 마지막 틱은 **만료 직전 시각으로 당겨서**
                    # 계산한다. `get_buffs()`가 `t >= expires_at`인 버프를 빼기 때문에,
                    # 시각을 그대로 두면 그 틱만 버프 없이 계산된다(인게임은 버프를 받는다).
                    dmg_t = t
                    if t >= expires_at:
                        dmg_t = expires_at - _TICK_NUDGE
                    self._damage_handler(eff, caster, dmg_t)
                    interval = eff.get("tick_interval", 1.0)
                    self._dot_timers[eid] = (caster, next_t + interval, expires_at)
            for eid in expired_dots:
                del self._dot_timers[eid]

        # ── 소환체 주기 공격(feather_tick) ────────────────────────────────
        #
        # DoT와 달리 주기가 고정이 아니다 — 생존 수 n에 대해 base × mult^(n-1)이고,
        # 다음 발사는 **직전 예약 시각 기준**으로 잡는다(프레임 양자화 드리프트 방지).
        # 히트 수는 timeline이 발사 시점에 `ref_count()`로 다시 읽는다.
        feathers = self.state.get("feathers")
        if feathers:
            for f_caster, by_id in feathers.items():
                for st in by_id.values():
                    nxt = st.get("next_t")
                    if nxt is None or t < nxt:
                        continue
                    n = sum(1 for e in st["expiry"] if e > t)
                    if n == 0:
                        st["next_t"] = None      # 전멸 — 재소환 전까지 정지
                        continue
                    self.notify("feather_tick", t, f_caster)
                    st["next_t"] = nxt + st["base"] * st["mult"] ** (n - 1)

        # 만료 버프 제거 + state_end 이벤트 발생
        expired_buffs = [ab for ab in self._active if t >= ab.expires_at]
        if expired_buffs:
            self._invalidate_buffs_cache()
        self._active = [ab for ab in self._active if t < ab.expires_at]
        for ab in expired_buffs:
            name = ab.effect.get("name", "")
            if name:
                self.notify(f"event:state_end:{name}", t, ab.caster)
                if self._buff_event_handler:
                    # 한 번도 조회되지 않고 만료된 지연 resolve 버프는 여기서 확정한다 —
                    # _resolve_lazy()가 밀려 있던 activate를 먼저 남기므로 expire만 뜨지 않는다
                    log_chars = self._resolve_lazy(ab)
                    for tgt in (log_chars or []):
                        self._buff_event_handler("expire", name, ab.caster, tgt, t, t)
            # hp_caster_based_pct / hp_only_caster_based_pct 만료 시 현재 체력 캡
            if ab.effect.get("stat") in ("hp_caster_based_pct", "hp_only_caster_based_pct",
                                         "max_hp_from_max_hp_pct") and "hp" in self.state:
                for tgt in (ab.target_chars or []):
                    if tgt in self.state["hp"]:
                        new_max = self.effective_max_hp(tgt)
                        if self.state["hp"][tgt] > new_max:
                            self.state["hp"][tgt] = new_max
                        self.sync_hp(tgt)

        # weapon_change 만료 정리 (state_end 이벤트 포함)
        wc = self.state.get("weapon_change", {})
        expired = [name for name, info in wc.items() if t >= info["expires_at"]]
        for name in expired:
            self.end_weapon_change(name, t)

        # 조건부 passive + 유한 지속: 조건이 참인 동안 만료를 민다.
        #
        # `passive`는 battle_start에 한 번만 등록되므로, 유한 지속 항목은 한 번 만료되면
        # 다시 켤 경로가 없었다 — 조건이 t=0에 거짓이면 그대로 죽었다. 원문
        # 「… 일 때 … [N초 유지]」는 *조건이 유지되는 동안 계속 걸리고 조건이 깨진 뒤
        # N초 더 남는다*는 뜻이라, 참인 동안 만료 시각을 밀고 거짓이 되면 그대로 둔다.
        # 무한 지속(`-1`) 조건부 passive는 아래 블록이 종전대로 돌본다.
        # (`_is_cond_finite_passive`. 유저 결정 2026-09-15)
        if self._cond_finite_passives:
            down = self.state.get("down")
            for eff, caster in self._cond_finite_passives:
                if down and caster in down:
                    continue
                if not self._condition_ok(eff["trigger"].get("condition", []), caster, t, eff):
                    continue
                ab = next((a for a in self._active
                           if a.effect is eff and a.caster == caster), None)
                if ab is None:
                    self._activate(eff, caster, t)
                else:
                    # 갱신은 조용히 한다 — 조건이 참인 내내 activate 로그가 쌓이지 않도록.
                    # `get_buffs` 캐시 키에 t가 들어가므로 이 프레임 값은 바뀌지 않는다.
                    ab.expires_at = max(ab.expires_at, self._expires_at(eff, caster, t))

        # 조건부 passive 버프: 조건 충족 여부 변화 감지 → buff_event_handler 발생
        if self._buff_event_handler:
            for ab in self._active:
                if ab.expires_at < math.inf:
                    continue  # 영구 passive만 대상
                conditions = ab.effect["trigger"].get("condition", [])
                if not conditions:
                    continue  # 무조건 passive는 이미 t=0에 등록됨
                bid = ab.uid
                now_met = self._runtime_condition_ok(conditions, ab.caster, ab.caster, ab.caster, t)
                prev_met = self._cond_passive_prev.get(bid)
                if prev_met is None:
                    # 첫 틱: 현재 상태만 기록, 이미 suppress_event로 처리됨
                    self._cond_passive_prev[bid] = now_met
                elif now_met and not prev_met:
                    # False → True: 조건 충족 시작 → activate 이벤트
                    self._cond_passive_prev[bid] = True
                    tgt_chars = (
                        self._resolve_target(ab.effect.get("target", "self"), ab.caster)
                        if ab.target_chars is None
                        else ab.target_chars
                    )
                    _val = self._get_value(ab.effect, ab, ab.caster)
                    _stat = ab.effect.get("stat")
                    for tgt in tgt_chars:
                        self._buff_event_handler("activate", ab.effect.get("name", ""), ab.caster, tgt, t, math.inf, _val, _stat)
                elif not now_met and prev_met:
                    # True → False: 조건 해제 → expire 이벤트
                    self._cond_passive_prev[bid] = False
                    tgt_chars = (
                        self._resolve_target(ab.effect.get("target", "self"), ab.caster)
                        if ab.target_chars is None
                        else ab.target_chars
                    )
                    for tgt in tgt_chars:
                        self._buff_event_handler("expire", ab.effect.get("name", ""), ab.caster, tgt, t, t)

        # every:Ns 처리 (`_every_effects`는 __init__에서 한 번만 추린다)
        for eff, caster, timing in self._every_effects:
            eid = id(eff)
            base_interval = float(timing[6:-1])  # "every:20s" → 20.0
            # skill_cooldown_pct 버프 반영: 음수 = 감소 (예: -75% → interval × 0.25)
            cool_pct = sum(
                (self._get_value(ab.effect, ab, caster) or 0.0)
                for ab in self._by_stat("skill_cooldown_pct")
                if ab.target_chars is None or caster in (ab.target_chars or [])
            )
            # effect_interval 버프 반영: 이 효과(target_effect)의 발동 주기를 초 단위로 가감
            eff_name = eff.get("name", "")
            flat = 0.0
            if eff_name:
                # 조건부 영구 버프는 `_active`에 등록만 되고 게이팅을 런타임 재평가에
                # 맡긴다(`_RUNTIME_COND_PREFIXES`). 여기서 조건을 안 보면 조건이 거짓인
                # 주기 단축이 그대로 먹는다 — 엠마 : 택티컬 업 `포메이션 LT 5~7`은
                # 은화가 없으면 꺼져야 하는데 30초 주기가 10초로 줄어 버린다.
                #
                # `skill_cooldown`(「스킬 N 재사용 시간 N초 ▼」)도 같은 자리다 — 원문 문구가
                # 다를 뿐 연산이 같다(`target_effect`가 가리키는 주기를 초 단위로 가감).
                # 도로시 `발현`이 첫 보유자이고, 값이 음수라 주기가 줄어든다(20s → 2s).
                flat = sum(
                    (self._get_value(ab.effect, ab, caster) or 0.0)
                    for ab in (self._by_stat("effect_interval")
                               + self._by_stat("skill_cooldown"))
                    if ab.effect.get("target_effect") == eff_name
                    and (ab.target_chars is None or caster in (ab.target_chars or []))
                    and (
                        not ab.has_runtime_conditions
                        or self._runtime_condition_ok(
                            ab.effect["trigger"].get("condition", []),
                            ab.caster, caster, caster, t,
                        )
                    )
                )
            interval = max(0.0, base_interval + flat) * max(0.0, 1.0 + cool_pct / 100.0)
            interval = max(interval, base_interval * 0.05)  # 최소 5% cap
            if eid not in self._next_fire:
                # 전투 시작 후 interval초 후 첫 발동
                self._next_fire[eid] = (interval, interval)
            next_t, prev_interval = self._next_fire[eid]
            if interval != prev_interval:
                # 쿨감이 도중에 켜지거나 꺼졌다 — 이미 예약된 절대 시각을 그대로 두면
                # 진행 중인 쿨타임에는 효과가 안 먹고 다음 주기부터 적용된다(위상 밀림).
                # 남은 시간을 새 배율로 비례 재조정한다.
                #   예) 20s 쿨 중 10s 경과 후 −75% → 잔여 10s × (5/20) = 2.5s
                next_t = t + max(0.0, next_t - t) * (interval / prev_interval)
            self._next_fire[eid] = (next_t, interval)
            if t >= next_t:
                # 전투불능 동안에도 주기는 흐른다 — 발동만 거른다
                down = self.state.get("down")
                if (not (down and caster in down)
                        and self._condition_ok(eff["trigger"].get("condition", []), caster, t, eff)):
                    self._activate(eff, caster, t)
                self._next_fire[eid] = (next_t + interval, interval)

        # tick_interval instant 처리
        if self._instant_timers:
            eff_by_id = self._eff_by_id
            expired_instants = []
            for eid, (caster, next_t, expires_at) in list(self._instant_timers.items()):
                if t >= expires_at:
                    expired_instants.append(eid)
                    continue
                if t < next_t:
                    continue
                eff = eff_by_id.get(eid)
                if eff is None:
                    expired_instants.append(eid)
                    continue
                # 영구(duration -1) 주기 instant는 런타임 조건을 매 틱 재평가한다.
                # `_has_runtime_cond`와 같은 기준 — 유한 duration은 발동 시점 래치이므로 제외.
                # 래치 조건(gauge_eq 등)은 `_runtime_condition_ok`가 보지 않으므로 영향 없다.
                # (아크레인저 블랙 `변신 2` 배터리 드레인 — 변신이 끝나면 멈춰야 한다)
                if eff.get("duration") == -1:
                    conds = eff["trigger"].get("condition", [])
                    if conds and not self._runtime_condition_ok(conds, caster, caster, caster, t):
                        self._instant_timers[eid] = (caster, next_t + eff.get("tick_interval", 1.0), expires_at)
                        continue
                self._dispatch_instant(eff, caster, t, from_tick=True)
                interval = eff.get("tick_interval", 1.0)
                self._instant_timers[eid] = (caster, next_t + interval, expires_at)
            for eid in expired_instants:
                del self._instant_timers[eid]

    # ── 버프 집계 ─────────────────────────────────────────────────────────

    def _invalidate_buffs_cache(self):
        self._cache_version += 1
        self._buffs_cache.clear()
        self._stunned_cache.clear()
        # 아래 셋은 전부 "`_active`가 그대로인 동안" 유효한 파생물이다. `_active`의
        # 추가·제거는 반드시 이 함수를 거치므로 여기서 한꺼번에 비우면 수명이 맞는다.
        self._plan_cache.clear()
        self._stat_index.clear()
        self._name_index_cache.clear()

    # ── `_active` 인덱스 ──────────────────────────────────────────────────
    #
    # stat·name으로 활성 버프를 찾는 일이 매 프레임 여러 번 일어난다. 그때마다 _active
    # 전체(스쿼드 1개 기준 ~100개)를 훑으면 프레임당 수천 번의 헛돈다.
    # 목록은 _active 순서를 그대로 보존하므로 합산 순서(= 부동소수점 결과)가 변하지 않는다.

    def _by_stat(self, stat: str) -> list:
        """`stat`이 일치하는 활성 버프 목록 (_active 순서 유지)."""
        out = self._stat_index.get(stat)
        if out is None:
            out = self._stat_index[stat] = [
                ab for ab in self._active if ab.effect.get("stat") == stat
            ]
        return out

    def _by_name(self, name: str) -> list:
        """효과 이름이 일치하는 활성 버프 목록 (_active 순서 유지).

        `target_chars`는 지연 resolve로 나중에 채워질 수 있으므로 **여기서 거르지 않는다** —
        대상 판정은 호출부가 조회 시점에 한다.
        """
        out = self._name_index_cache.get(name)
        if out is None:
            out = self._name_index_cache[name] = [
                ab for ab in self._active if ab.effect.get("name") == name
            ]
        return out

    def own_buff_expires_at(self, caster: str, name: str, t: float) -> float | None:
        """`caster`가 직접 발동한 유한 버프 `name`의 현재 만료 시각.

        대상은 일부러 조회하지 않는다. 지연 resolve 버프의 대상을 미리 확정하면 컨트롤의
        관찰만으로 전투 결과가 바뀔 수 있기 때문이다. 따라서 이 값은 "본인에게 걸렸나"가
        아니라 **본인이 발동한 이름 있는 효과가 지금 살아 있나**만 답한다.
        """
        expires = [
            ab.expires_at
            for ab in self._by_name(name)
            if ab.caster == caster and t < ab.expires_at < math.inf
        ]
        return max(expires) if expires else None

    @staticmethod
    def _is_time_invariant(ab: ActiveBuff) -> bool:
        """이 버프의 기여가 `_active`가 그대로인 동안 절대 변하지 않는가.

        참이면 값을 한 번만 구해 `_build_plan`에 박아 둘 수 있다. **보수적으로 판정한다** —
        애매하면 거짓을 돌려 매 조회 재평가시키는 쪽이 언제나 안전하다. 거짓 하나가
        늘어봐야 원래 하던 일을 그대로 할 뿐이고, 참을 잘못 주면 조용히 틀린다.

        각 조건이 막는 것:
          - `expires_at`가 유한  → 만료 판정이 t에 달렸다
          - runtime condition    → 풀버스트·차지·HP 등 상태에 달렸다
          - 지연 resolve 미완료   → 조회 시점에 대상이 결정되며 부작용도 있다
          - per_char_stacks      → 조회하는 캐릭터마다 값이 다르다
          - `scaling`            → lost_hp_pct·stack_count가 실시간 상태를 읽는다
          - `max_stack` != 1     → `_get_value`가 ab.stack을 곱한다
          - duration_bullets     → 발사에 따라 대상·잔량이 줄어든다
        """
        if ab.expires_at != math.inf:
            return False
        if ab.has_runtime_conditions:
            return False
        if ab.target_chars is None:
            return False
        if ab.per_char_stacks:
            return False
        eff = ab.effect
        if eff.get("scaling"):
            return False
        if eff.get("max_stack", 1) != 1:
            return False
        if ab.bullets_left != -1 or ab.bullets_per_target:
            return False
        if eff.get("stat") == "burst_charge_speed_pct":
            # 그 시전자가 일반 공격을 명중시켰는지에 따라 참조값이 바뀐다.
            # 계획에 접으면 전투 시작 시점의 값이 그대로 굳는다.
            return False
        return True

    def _route_burst_charge(self, ab: ActiveBuff,
                            buff_key: str, val: float) -> tuple[str, float]:
        """같은 버충속 값을 시전자 기준 게이지로 환산한다.

        버프 수령자가 시전자 본인인지 아군인지는 가르지 않는다. 시전자가 일반 공격을
        한 번도 명중시키지 않았으면 CDN `(발당)`, 한 번이라도 명중시켰으면 `(대상)`을
        참조해 히트당 고정 가산량으로 바꾼다. 전환 상태는 게이지 초기화와 무관하게
        전투 끝까지 유지된다. 정본: docs/mechanics/버스트 게이지.md.
        """
        if buff_key != "burst_charge_speed_flat":
            return buff_key, val
        weapon = _NIKKE.get(ab.caster, {})
        if ab.caster in self.state.get("normal_attack_landed", ()):
            reference = weapon.get("burst_energy", 0.0)
        else:
            reference = weapon.get("burst_energy_raw", weapon.get("burst_energy", 0.0) / 2.0)
        return buff_key, reference * val / 100.0

    def mark_normal_attack_landed(self, caster: str) -> None:
        """일반 공격 첫 명중을 기록하고 버충속 집계 캐시를 갱신한다.

        충전 창 밖(풀버스트 중) 명중과 구조물 명중도 전환을 일으킨다는 인게임 확인에
        따라 게이지 가산 성공 여부는 보지 않는다. 현재 시뮬에는 구조물 대상이 없으므로
        일반 공격 히트 경로가 적과 구조물을 대표한다.
        """
        landed = self.state.setdefault("normal_attack_landed", set())
        if caster not in landed:
            landed.add(caster)
            self._invalidate_buffs_cache()

    def _plan_step(self, ab: ActiveBuff, caster: str, target: str,
                   exclude_names: frozenset[str]) -> tuple | None:
        """시간 불변 버프 1개를 미리 평가해 스텝으로 축약. 기여가 없으면 None.

        분기 순서는 `get_buffs`의 조회 시점 경로와 **한 줄씩 대응한다.** 둘이 갈라지면
        정적/동적 버프가 서로 다른 규칙으로 집계되므로, 한쪽을 고치면 반드시 다른 쪽도 고친다.
        """
        eff = ab.effect
        if exclude_names and ab.caster == caster and eff.get("name") in exclude_names:
            return None
        stat = eff.get("stat", "")
        buff_key = _STAT_TO_BUFF.get(stat)
        if not buff_key:
            return None

        target_chars = ab.target_chars or []
        applies_to_caster = caster in target_chars
        applies_to_target = target in target_chars
        if buff_key == "received_dmg":
            if not applies_to_target:
                return None
        elif buff_key == "split_dmg_pct":
            if not applies_to_caster:
                return None
        elif not (applies_to_caster or applies_to_target):
            return None

        if stat == "def_pct" and applies_to_target and not applies_to_caster:
            buff_key = "enemy_def_down_pct"
        elif stat == "def_caster_based_pct" and applies_to_target and not applies_to_caster:
            buff_key = "enemy_def_down_flat"
        actual_recipient = caster if applies_to_caster else target

        if buff_key in _BOOL_BUFF_KEYS:
            return (_PLAN_FLAG, buff_key, None)

        val = self._get_value(eff, ab, actual_recipient, stack_override=None)
        if val is None:
            return None
        if buff_key == "enemy_def_down_flat":
            val = self._caster_based_def_flat(ab, val)
        buff_key, val = self._route_burst_charge(ab, buff_key, val)
        if stat in _CRIT_RATE_STATS:
            # key 자리에 "일반 공격 한정인가"를 싣는다 — 스킬 딜용 합에서 뺄 기여를 가린다
            return (_PLAN_CRIT, stat in _NORMAL_ATK_ONLY_CRIT_RATE_STATS, val / 100)
        if stat in _CRIT_DMG_STATS:
            return (_PLAN_CDMG, stat in _NORMAL_ATK_ONLY_CRIT_DMG_STATS, val)
        if buff_key in _QUANT_BUFF_KEYS:
            return (_PLAN_QUANT, (buff_key, _quant_source(ab), _quant_group_key(ab)), val)
        return (_PLAN_ADD, buff_key, val)

    def _build_plan(self, caster: str, target: str, exclude_names: frozenset[str]) -> list:
        """`_active`를 훑는 순서를 그대로 보존한 get_buffs 실행 계획.

        `_active`의 대부분은 장비·큐브·소장품·영구 패시브라 매 프레임 같은 값을 낸다
        (실측 79~88%). 그것들을 미리 스텝으로 접어 두고, 나머지만 조회 시점에 평가한다.

        **순서를 보존하는 이유**: 합산이 부동소수점이라 순서가 바뀌면 마지막 자리가
        달라지고, 회귀 하네스는 완전 일치를 요구한다(`HARNESS.md §왜 결정론적인가`).
        그래서 "정적인 것만 앞으로 모아 더하기"는 하지 않는다.

        계획은 `_cache_version`이 오르면 `_invalidate_buffs_cache`가 통째로 버린다.
        """
        plan = [
            self._plan_step(ab, caster, target, exclude_names)
            if self._is_time_invariant(ab) else (_PLAN_LIVE, ab, None)
            for ab in self._active
        ]
        plan = [step for step in plan if step is not None]
        self._plan_cache[(caster, target, exclude_names)] = plan
        return plan

    def get_buffs(
        self, caster: str, target: str, t: float,
        exclude_names: frozenset[str] = frozenset(),
    ) -> dict:
        """
        현재 시각 t에서 caster가 target을 공격할 때 적용되는 buffs 딕셔너리 반환.

        caster에게 적용된 버프(공격력 등)와
        target에게 적용된 버프(received_dmg 등)를 모두 포함.

        `exclude_names`: caster 본인이 건 버프 중 이 이름들은 집계에서 뺀다.
        원문 `■` 블록 순서가 실행 순서라는 규칙(GAMEPLAY.md §효과 실행 순서) 때문에,
        딜 블록보다 **뒤에** 서술된 같은 트리거의 버프는 그 딜에 실리면 안 된다.
        보류 발동(`_pending_burst_dmg`)은 계산 시점이 뒤로 밀려 이 순서가 깨지므로
        해당 이름을 여기서 제외한다.
        """
        # target도 키에 넣는다 — 딜 경로는 늘 적 센티널이지만, 같은 프레임에 다른 대상(아군·쫄몹)으로
        # 부른 결과를 돌려주면 대상에게 붙은 받는 대미지 계열이 섞인다
        cache_key = (caster, target, t, self._cache_version, exclude_names)
        cached = self._buffs_cache.get(cache_key)
        if cached is not None:
            return cached

        plan = self._plan_cache.get((caster, target, exclude_names))
        if plan is None:
            plan = self._build_plan(caster, target, exclude_names)
        elif _BUFF_AUDIT:
            fresh = self._build_plan(caster, target, exclude_names)
            if fresh != plan:
                raise AssertionError(
                    f"get_buffs 계획 캐시가 낡았다 (caster={caster}, t={t}). "
                    f"`_active`를 바꾸고 _invalidate_buffs_cache()를 부르지 않은 경로가 있다."
                )
            plan = fresh

        buffs = dict(_BUFFS_ZERO)
        crit_rate_parts: list[float] = [0.15]  # 기본 크리확률 15%
        # 스킬 딜용 합. 일반 공격 한정 기여(`normal_atk_crit_rate`)만 빠진다.
        # 별도 리스트로 두는 이유는 합산 **순서**를 보존하기 위해서다 — 나중에 빼는 방식은
        # 부동소수점 마지막 자리가 달라져 회귀 하네스의 완전 일치가 깨진다.
        crit_rate_skill_parts: list[float] = [0.15]
        # 크리 대미지도 같은 이유로 순서 보존 리스트. 시작값 0.0은 `_BUFFS_ZERO["crit_dmg"]`와
        # 같아서, 종전의 `buffs[key] += val` 누산과 부동소수점까지 동일한 결과를 낸다.
        crit_dmg_parts: list[float] = [0.0]
        crit_dmg_skill_parts: list[float] = [0.0]
        # 소스별 반올림 스탯의 그룹별 기여. {(buff_key, 그룹키): 합} — 삽입 순서가
        # 곧 `_active` 순서라 부동소수점 합산 순서가 결정론적으로 유지된다.
        quant_parts: dict[tuple, float] = {}

        for kind, key, pre in plan:
            # 미리 접어 둔 스텝 — 시간 불변 버프의 기여 (`_build_plan`)
            if kind == _PLAN_ADD:
                buffs[key] = buffs.get(key, 0.0) + pre
                continue
            if kind == _PLAN_CRIT:
                crit_rate_parts.append(pre)
                if not key:  # key = 일반 공격 한정 여부
                    crit_rate_skill_parts.append(pre)
                continue
            if kind == _PLAN_CDMG:
                crit_dmg_parts.append(pre)
                if not key:  # key = 일반 공격 한정 여부
                    crit_dmg_skill_parts.append(pre)
                continue
            if kind == _PLAN_QUANT:
                quant_parts[key] = quant_parts.get(key, 0.0) + pre
                continue
            if kind == _PLAN_FLAG:
                buffs[key] = True
                continue

            # _PLAN_LIVE — 시간·상태에 따라 기여가 변하는 버프는 매번 평가한다
            ab = key
            if t >= ab.expires_at:
                continue

            eff = ab.effect
            if exclude_names and ab.caster == caster and eff.get("name") in exclude_names:
                continue
            stat = eff.get("stat", "")
            buff_key = _STAT_TO_BUFF.get(stat)
            if not buff_key:
                continue

            # 지연 resolve: 활성화 시점 직후 첫 조회 때 1회 결정하고 캐싱.
            # (같은 프레임에 simultaneous 발동된 다른 버프들이 정착된 후 순위 평가.
            #  이후엔 고정 — 대상의 ATK/HP 등이 변해도 타겟이 바뀌지 않음.)
            target_chars = ab.target_chars if ab.target_chars is not None else self._resolve_lazy(ab)

            # 대상 확인: 버프가 caster 또는 target에게 적용되는지
            applies_to_caster = caster in target_chars
            applies_to_target = target in target_chars

            # received_dmg 계열: 적(target)에게 부여된 것만 ⑥에 반영
            # split_dmg 계열: 아군(caster)에게 부여된 것만 ⑥에 반영
            if buff_key == "received_dmg":
                if not applies_to_target:
                    continue
            elif buff_key == "split_dmg_pct":
                if not applies_to_caster:
                    continue
            elif not (applies_to_caster or applies_to_target):
                continue

            # caster_based 환산을 위해 실제 버프 수령자를 특정
            actual_recipient = caster if applies_to_caster else target

            # runtime condition 재평가: 플래그가 없는 버프(정적 조건 전용)는 건너뜀.
            # `query_target`엔 공격 대상(딜 계산에서는 대개 적 센티널)이 아니라 **실제
            # 버프 수령자**를 넘긴다 — `ally_hp_below:` 같은 조건은 "버프 받는 아군의
            # 체력"을 봐야 하는데, target을 그대로 넘기면 딜 계산 경로에서는 늘 적
            # 센티널이 되어 체력이 항상 100%로 읽혀 조건이 영구히 거짓이 된다.
            if ab.has_runtime_conditions:
                conditions = eff["trigger"].get("condition", [])
                if not self._runtime_condition_ok(conditions, ab.caster, caster, actual_recipient, t):
                    continue

            # def_pct: 적(enemy)에게 부여되면 방어력 감소(②)로 라우팅.
            # 아군 대상 def_pct는 base_stat용 — 데미지엔 무관하므로 def_pct 키로 흘려보내 무시.
            if stat == "def_pct" and applies_to_target and not applies_to_caster:
                buff_key = "enemy_def_down_pct"
            # def_caster_based_pct: 적에게 부여되면 **정액** 방어력 감소(②)로 라우팅.
            # 아군 대상은 종전대로 base_stat용(`_effective_def`)이라 딜 경로에서 무시된다.
            elif stat == "def_caster_based_pct" and applies_to_target and not applies_to_caster:
                buff_key = "enemy_def_down_flat"

            # boolean 플래그 스탯: 수치 없이 True만 세팅
            if buff_key in _BOOL_BUFF_KEYS:
                buffs[buff_key] = True
                continue

            char_stack = ab.per_char_stacks.get(caster) if ab.per_char_stacks else None
            val = self._get_value(eff, ab, actual_recipient, stack_override=char_stack)
            if val is not None and eff.get("scaling") == "max_ammo_count":
                val = self._scale_by_max_ammo(val, actual_recipient, t)
            if val is None:
                continue
            if buff_key == "enemy_def_down_flat":
                val = self._caster_based_def_flat(ab, val)
            buff_key, val = self._route_burst_charge(ab, buff_key, val)

            if stat in _CRIT_RATE_STATS:
                crit_rate_parts.append(val / 100)
                if stat not in _NORMAL_ATK_ONLY_CRIT_RATE_STATS:
                    crit_rate_skill_parts.append(val / 100)
            elif stat in _CRIT_DMG_STATS:
                crit_dmg_parts.append(val)
                if stat not in _NORMAL_ATK_ONLY_CRIT_DMG_STATS:
                    crit_dmg_skill_parts.append(val)
            elif buff_key in _QUANT_BUFF_KEYS:
                gk = (buff_key, _quant_source(ab), _quant_group_key(ab))
                quant_parts[gk] = quant_parts.get(gk, 0.0) + val
            else:
                buffs[buff_key] = buffs.get(buff_key, 0.0) + val

        # 크리확률 합성: 단순 합연산 (유저 인게임 확인). 100%에서 자른다 —
        # 초과분은 게임에서도 버려지고, calc_avg_damage()의 기댓값 계산이 1을 넘으면 깨진다.
        # 0 아래도 자른다 — 보스 디버프 「크리티컬 확률 ▼」가 기본 15%를 넘으면 기댓값이 음수가 된다.
        buffs["crit_rate"] = max(0.0, min(1.0, sum(crit_rate_parts)))
        buffs["crit_rate_skill"] = max(0.0, min(1.0, sum(crit_rate_skill_parts)))

        # 크리 대미지는 상한이 없다 — 합만 낸다 (③ 가산 항 `0.5 + crit_dmg%`).
        buffs["crit_dmg"] = sum(crit_dmg_parts)
        buffs["crit_dmg_skill"] = sum(crit_dmg_skill_parts)

        # 소스별 반올림 스탯: 그룹별 목록과 합계를 함께 싣는다. 합계는 표시·후처리
        # (면역·초과분 환산)용이고, 실제 반올림은 기본값을 아는 timeline이 목록으로 한다.
        parts_by_key: dict[str, list[float]] = {k: [] for k in _QUANT_BUFF_KEYS}
        # 면역 후처리가 소스를 봐야 하므로 목록과 같은 순서로 소스 태그도 싣는다.
        parts_src_by_key: dict[str, list[str | None]] = {k: [] for k in _QUANT_BUFF_KEYS}
        for (bk, src, _group), v in quant_parts.items():
            parts_by_key[bk].append(v)
            parts_src_by_key[bk].append(src)
            buffs[bk] = buffs.get(bk, 0.0) + v
        buffs[_QUANT_PARTS_KEY] = parts_by_key

        # received_dmg_buff_mag_pct: 특정 named buff(`target_effect`)의 「받는 대미지 ▲」
        # 수치를 (1 + N/100)배. `atk_buff_mag_pct`와 같은 층이고 증폭 대상만 다르다.
        # 기본값은 위 루프가 이미 더했으므로 여기서는 **증분만** 얹는다 — 그래야 증폭
        # 버프가 없는 기존 로스터의 합산 순서·부동소수점 결과가 그대로 유지된다.
        # (엠마 : 택티컬 업 `환경 조성 강화` — 원문 「환경 조성 받는 대미지 증가 배율이
        #  100% 증가 상태로 변경」. 증폭도, 증폭 대상도 적에게 붙는다)
        for mag_ab in self._by_stat("received_dmg_buff_mag_pct"):
            if t >= mag_ab.expires_at:
                continue
            ref = mag_ab.effect.get("target_effect")
            if not ref:
                continue
            mag_tgt = (
                self._resolve_target(mag_ab.effect.get("target", "self"), mag_ab.caster)
                if mag_ab.target_chars is None else mag_ab.target_chars
            )
            if target not in mag_tgt:
                continue
            if mag_ab.has_runtime_conditions:
                mag_conds = mag_ab.effect["trigger"].get("condition", [])
                if not self._runtime_condition_ok(mag_conds, mag_ab.caster, caster, target, t):
                    continue
            mag_val = self._get_value(mag_ab.effect, mag_ab, mag_ab.caster)
            if mag_val is None:
                continue
            for ab in self._by_name(ref):
                if t >= ab.expires_at or ab.effect.get("stat") != "received_dmg_pct":
                    continue
                base_tgt = ab.target_chars if ab.target_chars is not None else self._resolve_lazy(ab)
                if target not in base_tgt:
                    continue
                if ab.has_runtime_conditions:
                    conds = ab.effect["trigger"].get("condition", [])
                    if not self._runtime_condition_ok(conds, ab.caster, caster, target, t):
                        continue
                base_val = self._get_value(ab.effect, ab, target)
                if base_val is None:
                    continue
                buffs["received_dmg"] = buffs.get("received_dmg", 0.0) + base_val * (mag_val / 100.0)

        # atk_from_hp_pct: 최종 최대 HP × (val/100) → atk_flat에 합산
        for ab in self._by_stat("atk_from_hp_pct"):
            if t >= ab.expires_at:
                continue
            target_chars = (
                self._resolve_target(ab.effect.get("target", "self"), ab.caster)
                if ab.target_chars is None
                else ab.target_chars
            )
            if caster not in target_chars:
                continue
            if ab.has_runtime_conditions:
                conditions = ab.effect["trigger"].get("condition", [])
                # 위 `caster not in target_chars` 검사를 지났으므로 수령자는 caster다.
                if not self._runtime_condition_ok(conditions, ab.caster, caster, caster, t):
                    continue
            val = self._get_value(ab.effect, ab, caster)
            if val is None:
                continue
            final_hp = self.effective_max_hp(caster)
            buffs["atk_flat"] = buffs.get("atk_flat", 0.0) + final_hp * (val / 100.0)

        # atk_caster_based_pct: 시전자 공격력 × (val/100) → 수령자 atk_flat에 합산
        for ab in self._by_stat("atk_caster_based_pct"):
            if t >= ab.expires_at:
                continue
            target_chars = (
                self._resolve_target(ab.effect.get("target", "self"), ab.caster)
                if ab.target_chars is None
                else ab.target_chars
            )
            if caster not in target_chars:
                continue
            if ab.has_runtime_conditions:
                conditions = ab.effect["trigger"].get("condition", [])
                # 위 `caster not in target_chars` 검사를 지났으므로 수령자는 caster다.
                if not self._runtime_condition_ok(conditions, ab.caster, caster, caster, t):
                    continue
            val = self._get_value(ab.effect, ab, ab.caster)
            if val is None:
                continue
            caster_atk = self.state.get("base_stats", {}).get(ab.caster, {}).get("atk", 0.0)
            # atk_buff_mag_pct: 이 named buff를 target_effect로 참조하는 배율 적용
            mag_mult = 1.0
            buff_name = ab.effect.get("name", "")
            if buff_name:
                for mag_ab in self._by_stat("atk_buff_mag_pct"):
                    if mag_ab.expires_at <= t:
                        continue
                    if mag_ab.effect.get("target_effect") != buff_name:
                        continue
                    mag_tgt = (
                        self._resolve_target(mag_ab.effect.get("target", "self"), mag_ab.caster)
                        if mag_ab.target_chars is None
                        else mag_ab.target_chars
                    )
                    if caster not in mag_tgt:
                        continue
                    mag_val = self._get_value(mag_ab.effect, mag_ab, mag_ab.caster)
                    if mag_val is not None:
                        mag_mult += mag_val / 100.0
            buffs["atk_flat"] = buffs.get("atk_flat", 0.0) + caster_atk * (val / 100.0) * mag_mult

        # charge_time_fixed가 있으면 차지속도 관련 버프/디버프 모두 0
        # (합계를 0으로 만들 때는 그룹별 목록도 함께 비운다 — 반올림은 목록으로 하므로
        #  한쪽만 지우면 후처리가 통째로 무시된다)
        if buffs["charge_time_fixed"]:
            buffs["charge_speed_pct"] = 0.0
            parts_by_key["charge_speed_pct"] = []
        elif buffs["charge_speed_buff_immune"] or buffs["charge_speed_debuff_immune"]:
            # 면역 후처리: **스킬 버프**의 양수(증가)·음수(감소) 성분만 제거한다.
            # 오버로드(장비 옵션)·큐브의 차지 속도는 면역이 막지 못한다
            # (유저 인게임 확인, 2026-09-02 — 리버렐리오. 본인 스킬도 면역 대상이다).
            # 소스를 가리지 않고 전부 무시하는 것은 위의 `charge_time_fixed`다.
            kept = [
                v
                for v, src in zip(parts_by_key["charge_speed_pct"],
                                  parts_src_by_key["charge_speed_pct"])
                if src in _CHARGE_IMMUNE_EXEMPT_SOURCES
                or not ((v > 0 and buffs["charge_speed_buff_immune"])
                        or (v < 0 and buffs["charge_speed_debuff_immune"]))
            ]
            # 남는 것이 그대로면 손대지 않는다 — 재합산은 부동소수점 마지막 자리를 흔든다.
            if len(kept) != len(parts_by_key["charge_speed_pct"]):
                parts_by_key["charge_speed_pct"] = kept
                buffs["charge_speed_pct"] = sum(kept, 0.0)

        # charge_speed 100% 초과분을 charge_dmg_pct로 환산 (레드 후드)
        conv = buffs["charge_speed_overflow_conversion_pct"]
        if conv > 0.0:
            overflow = max(0.0, buffs["charge_speed_pct"] - 100.0)
            if overflow > 0.0:
                buffs["charge_dmg_pct"] += overflow * conv / 100.0

        self._buffs_cache[cache_key] = buffs
        return buffs

    def _runtime_condition_ok(
        self,
        conditions: list,
        buff_caster: str,
        query_caster: str,
        query_target: str,
        t: float,
    ) -> bool:
        """get_buffs 호출 시마다 재평가하는 상태 의존 condition."""
        for cond in conditions:
            if cond == "during_charge":
                if not self.state.get("charging", {}).get(buff_caster):
                    return False
            elif cond == "during_full_burst":
                if not self.state.get("full_burst"):
                    return False
            elif cond == "not_during_full_burst":
                if self.state.get("full_burst"):
                    return False
            elif cond.startswith("self_hp_above:"):
                n = float(cond.split(":")[1])
                hp_pct = self.state.get("hp_pct", {}).get(buff_caster, 100.0)
                if hp_pct < n:
                    return False
            elif cond.startswith("self_hp_below:"):
                n = float(cond.split(":")[1])
                hp_pct = self.state.get("hp_pct", {}).get(buff_caster, 100.0)
                if hp_pct > n:
                    return False
            elif cond == "self_hp_max":
                hp_pct = self.state.get("hp_pct", {}).get(buff_caster, 100.0)
                if hp_pct < 100.0:
                    return False
            elif cond == "during_shield":
                if not self.has_shield(buff_caster):
                    return False
            elif cond == "self_cover_alive":
                if not self.cover_alive(buff_caster):
                    return False
            elif cond == "not_self_cover_alive":
                if self.cover_alive(buff_caster):
                    return False
            elif cond == "focusing":
                # 포커싱 = 카메라를 잡고 있다. 카메라는 조작 주인을 따라가고, 없으면 정적 유도값이다
                if buff_caster not in self.state.get("camera", ()):
                    return False
            elif cond.startswith("ally_hp_below:"):
                n = float(cond.split(":")[1])
                # 대상이 아군이고 체력이 N% 이하인지
                hp_pct = self.state.get("hp_pct", {}).get(query_target, 100.0)
                if hp_pct > n:
                    return False
            elif cond.startswith("self_stack_above:"):
                parts = cond.split(":")
                stack_name, threshold = parts[1], int(parts[2])
                current = next(
                    (ab.stack for ab in self._by_name(stack_name)
                     if ab.caster == buff_caster
                     and (buff_caster in (ab.target_chars or [])
                          or any(_is_enemy(x) for x in (ab.target_chars or [])))),
                    0,
                )
                if current < threshold:
                    return False
            elif cond.startswith("gauge_above:"):
                parts = cond.split(":")
                gauge_id, threshold = parts[1], float(parts[2])
                current = self.state.get("gauges", {}).get(buff_caster, {}).get(gauge_id, 0.0)
                if current < threshold:
                    return False
            elif cond.startswith("gauge_below:"):
                parts = cond.split(":")
                gauge_id, threshold = parts[1], float(parts[2])
                current = self.state.get("gauges", {}).get(buff_caster, {}).get(gauge_id, 0.0)
                if current >= threshold:
                    return False
            elif cond.startswith("self_state:"):
                state_name = cond[len("self_state:"):]
                has_state = self._has_self_state(buff_caster, state_name)
                if not has_state:
                    return False
            elif cond.startswith("not_self_state:"):
                state_name = cond[len("not_self_state:"):]
                has_state = self._has_self_state(buff_caster, state_name)
                if has_state:
                    return False
            elif cond.startswith("target_state:"):
                state_name = cond[len("target_state:"):]
                if not self._has_target_state(state_name):
                    return False
            elif cond.startswith("not_target_state:"):
                state_name = cond[len("not_target_state:"):]
                if self._has_target_state(state_name):
                    return False
            elif cond.startswith("target_code:"):
                # `_condition_ok`와 같은 규약 — 코드 미지정 적은 통과시킨다
                code = cond[len("target_code:"):]
                enemy_code = self.state.get("enemy", {}).get("code", "")
                if enemy_code and enemy_code != code:
                    return False
            elif cond.startswith("enemy_count_below:"):
                # 적 수 = 보스 1 + 산 쫄몹. 쫄몹이 없으면 1 — "랩쳐 N기 이하" → 1 <= N (N>=1이면 항상 참)
                if self.state.get("enemy_count", 1) > int(cond.split(":")[1]):
                    return False
            elif cond.startswith("enemy_count_above:"):
                # 쫄몹이 없으면 1 — "랩쳐 N기 이상" → 1 >= N (N>=2이면 항상 거짓)
                if self.state.get("enemy_count", 1) < int(cond.split(":")[1]):
                    return False
            # prob:N은 notify 시점에만 평가 (get_buffs에서 재판정하지 않음)
        return True

    def ref_count(self, caster: str, ref: str) -> int | None:
        """`scaling_ref` 등이 가리키는 이름의 현재 수치.

        같은 이름이 게이지일 수도, 중첩 버프일 수도 있어 양쪽을 순서대로 본다.
        게이지로 등록된 이름이면 값이 0이어도 그 0을 그대로 돌려준다
        (0을 "없음"으로 보고 버프 스택으로 넘어가면, 히트 수가 0이 아니라
         1로 남는 식으로 조용히 틀린 값이 나온다).

        게이지도 버프도 아니면 None — 호출부가 각자의 기본값을 쓰도록 둔다.
        """
        if not ref:
            return None
        gauges = self.state.get("gauges", {}).get(caster, {})
        if ref in gauges:
            return int(gauges[ref])
        # 소환체(feather_id) — 게이지와 같은 자리에서 본다. 값은 현재 생존 수
        feathers = self.state.get("feathers", {}).get(caster, {})
        if ref in feathers:
            return sum(1 for e in feathers[ref]["expiry"] if e > self._cur_t)
        for ab in self._by_name(ref):
            if ab.caster == caster:
                return ab.stack
        return None

    def _same_target_ramp_hits(self, eff: dict, caster: str) -> int | None:
        """`target: "same_target:[이름]"` DoT가 한 트리거에 몇 번 얹히는가.

        원문 `동일 적 대상에게`가 **앞 블록의 다중 히트 공격에 딸린** 경우, 중첩은
        그 공격의 히트마다 하나씩 붙는다. 몇 번인지는 짝 효과의 `stat` suffix가
        정한다 — `sequential_damage:10`이면 10, `sequential_damage:MP`처럼 이름이면
        `ref_count()`로 게이지·스택 수를 읽는다.

        짝을 못 찾으면 None. 그러면 호출부는 램프 없이 평범한 DoT로 둔다.
        """
        target = eff.get("target", "")
        if not isinstance(target, str) or not target.startswith("same_target:"):
            return None
        ref_name = target[len("same_target:"):]
        for other, other_caster in self._effects:
            if other_caster != caster or other.get("name") != ref_name:
                continue
            parts = other.get("stat", "").split(":")
            if len(parts) < 2:
                return 1
            if parts[1].lstrip("-").isdigit():
                return int(parts[1])
            n = self.ref_count(caster, parts[1])
            return n if n is not None else 1
        return None

    def _get_value(self, eff: dict, ab: ActiveBuff, query_caster: str | None = None, stack_override: int | None = None) -> float | None:
        """효과 항목에서 현재 스킬 레벨 + 스택 기준 수치 반환. %값 그대로 반환."""
        if "fixed_value" in eff:
            base = float(eff["fixed_value"])
        elif "values" in eff:
            char = self._char.get(ab.caster, {})
            skill_lv = _get_skill_lv(char, eff)
            vals = eff["values"]
            base = float(vals.get(skill_lv, vals.get("10", 0.0)))
        else:
            return None

        # charge_speed_caster_based_pct: 시전자 charge_time 기준으로 환산
        # 단축량(초) = caster_charge_time × base% → 대상 관점의 charge_speed_pct로 변환
        if eff.get("stat") == "charge_speed_caster_based_pct":
            caster_nikke = _NIKKE.get(ab.caster, {})
            caster_charge_time = caster_nikke.get("charge_time")
            if caster_charge_time is None:
                return None
            target_name = query_caster  # get_buffs의 caster(=실제 버프 수령자)
            target_nikke = _NIKKE.get(target_name, {}) if target_name else {}
            target_charge_time = target_nikke.get("charge_time") or caster_charge_time
            reduction_sec = caster_charge_time * base / 100.0
            base = reduction_sec / target_charge_time * 100.0

        # lost_hp_pct: 잃은 체력 % 비례 (실제값 = base × 잃은 체력%)
        scaling = eff.get("scaling")
        if scaling == "lost_hp_pct":
            hp_pct = self.state.get("hp_pct", {}).get(ab.caster, 100.0)
            lost = max(0.0, 100.0 - hp_pct)
            return base * lost

        # 스택 합산 (per_char_stacks 오버라이드 우선 적용)
        eff_stack = stack_override if stack_override is not None else ab.stack
        if scaling == "stack_count":
            ref = eff.get("scaling_ref")
            if ref:
                # 발동 시점에 고정한 값이 있으면 그것을 쓴다 (_capture_scaling_stack 참고).
                # 없으면(지속 버프 등) scaling_ref가 가리키는 게이지/스택을 실시간 조회.
                captured = getattr(ab, "scaling_stack", None)
                stack = captured if captured is not None else self.ref_count(ab.caster, ref)
                base *= stack if stack is not None else 0
            else:
                base *= eff_stack
            return base

        return base * eff_stack if eff.get("max_stack", 1) != 1 else base

    def _scale_by_max_ammo(self, val: float, recipient: str, t: float) -> float | None:
        """`scaling: "max_ammo_count"` — 원문 「**최종** 최대 장탄 수 1발 당」. 값에 수령자의 실효 최대 장탄을
        곱한다(오버로드·큐브·소장품·스킬 버프·반올림을 다 거친 `CharState._full_ammo()`). **조회 시점에 읽는다**
        — 1발 유지 버프면 부여한 발이 아니라 받는 발을 쏘는 순간의 장탄이다(에밀리아 `미정령의 축복 2`).

        최대 장탄 계산이 다시 `get_buffs`를 부르므로 그 안쪽 조회에서는 None(= 이 버프를 건너뜀)을 돌려준다.
        최대 장탄은 차지 대미지 같은 이 부류의 stat을 읽지 않아 결과가 같다. 안쪽 결과가 `_buffs_cache`에
        먼저 들어가도 같은 키로 바깥 조회가 끝나며 덮어쓴다."""
        if self.max_ammo_provider is None or recipient in self._ammo_query:
            return None
        self._ammo_query.add(recipient)
        try:
            n = self.max_ammo_provider(recipient, t)
        finally:
            self._ammo_query.discard(recipient)
        if n is None:
            return None
        return val * n

    # ── 타겟 resolve ──────────────────────────────────────────────────────

    def _resolve_lazy(self, ab: ActiveBuff) -> list[str]:
        """지연 resolve 버프(`_LAZY_RESOLVE_PREFIXES`)의 대상을 1회 결정하고 캐싱한다.

        `duration_bullets`가 붙어 있으면 **캐릭터별 카운터로 함께 옮긴다.** 옮기지 않으면
        남은 `bullets_left`가 consume의 "시전자 본인 발사로 소모" 분기에 걸려, 대상이 아니라
        **시전자의 발사**가 버프를 먹는다 (미란다 `웨이크업! 4`: 최고 공격력 아군 1기에게
        크리확률 1발 유지 → 미란다 본인 SMG 첫 발이 1프레임 만에 소모).

        두 호출자(get_buffs · consume_bullet_buffs)가 같은 헬퍼를 쓰므로 어느 쪽이 먼저
        resolve하든 결과가 같다.

        **같은 시전자가 같은 시각에 같은 target 문자열로 건 버프는 대상을 공유한다**
        (`_lazy_target_cache`). 원문 한 블록이 여러 효과를 한 대상에게 주는 경우
        (블랑 `쇼타임`의 불굴 + 최대 체력), 먼저 resolve된 쪽이 순위 기준 스탯을 바꾸면
        나중 항목이 다른 아군을 고른다 — `max_hp_pct`는 활성화 프레임에 resolve되지만
        값 없는 불굴 쪽은 `get_buffs`가 읽지 않아 한참 뒤에야 resolve되기 때문이다.
        캐시로 묶지 않으면 같은 블록의 두 효과가 서로 다른 아군에게 붙는다.
        """
        if ab.target_chars is None:
            raw_target = ab.effect.get("target", "self")
            key = (ab.caster, ab.activated_at, str(raw_target))
            shared = self._lazy_target_cache.get(key)
            if shared is None:
                shared = self._resolve_target(raw_target, ab.caster)
                self._lazy_target_cache[key] = shared
            ab.target_chars = list(shared)
            if ab.bullets_left != -1:
                ab.bullets_per_target = {c: ab.bullets_left for c in ab.target_chars}
                ab.bullets_left = -1
            if ab.log_pending:
                ab.log_pending = False
                name = ab.effect.get("name", "")
                if self._buff_event_handler and name:
                    val = self._get_value(ab.effect, ab, ab.caster)
                    stat = ab.effect.get("stat")
                    for tgt in ab.target_chars:
                        self._buff_event_handler("activate", name, ab.caster, tgt,
                                                 ab.activated_at, ab.expires_at, val, stat)
        return ab.target_chars

    def _resolve_target(self, target: Any, caster: str) -> list[str]:
        """target 문자열 → 캐릭터명 목록. **전투불능 아군은 빠진다** — 쓰러진 니케에게는 버프가
        안 붙는다. `allies_down_*`만 거꾸로 쓰러진 아군에서 고른다.

        순위로 N명을 고르는 대상(`allies_top_atk:` 등)은 거르고 나서 자르므로 산 사람으로
        N명이 찬다(`_alive()`). 전투불능이 없으면 결과가 이 기능 이전과 같다.
        """
        res = self._resolve_target_raw(target, caster)
        down = self.state.get("down")
        if not down or (isinstance(target, str) and target.startswith("allies_down_")):
            return res
        return [n for n in res if n not in down]

    def _alive(self, names: list[str] | None = None) -> list[str]:
        """전투불능이 아닌 스쿼드원(순서 유지)."""
        names = self.squad_names if names is None else names
        down = self.state.get("down")
        return [n for n in names if n not in down] if down else list(names)

    def is_down(self, name: str) -> bool:
        down = self.state.get("down")
        return bool(down) and name in down

    def _resolve_target_raw(self, target: Any, caster: str) -> list[str]:
        if isinstance(target, list):
            result = []
            for t in target:
                result.extend(self._resolve_target(t, caster))
            return result

        if target == "self":
            return [caster]
        # 캐릭터 이름 직접 지정 (예: "이사벨" — 아르카나 마법사 카드 예외)
        if target in self.squad_names:
            return [target]
        if target == "all_allies":
            return list(self.squad_names)
        if target == "all_allies_burst_casted":
            return [n for n in self.squad_names if self.state.get("burst_casted", {}).get(n)]
        if target == "all_allies_burst_not_casted":
            return [n for n in self.squad_names if not self.state.get("burst_casted", {}).get(n)]
        if target == "all_allies_excl_self":
            return [n for n in self.squad_names if n != caster]
        if target in ("enemy", "all_enemies", "target", "target_body", "same_target", "boss",
                      "enemies_in_range", "enemies_nearest_in_range"):
            # 적 대상: "__enemy__" 센티널 사용 (타임라인이 판단). 쫄몹이 살아 있으면 적마다 푼다
            return self._resolve_enemies(target, caster)

        # "자신을 제외한 전투불능 상태 최종 공격력이 가장 높은 아군 N기" (마나 `매터 감마 3` 부활)
        if target.startswith("allies_down_top_atk_excl:"):
            n = int(target.split(":")[1])
            down = self.state.get("down") or ()
            pool = [x for x in self.squad_names if x in down and x != caster]
            pool.sort(key=self._effective_atk, reverse=True)
            return pool[:n]
        # "엄폐물 체력이 가장 낮은 아군 N기" — 남은 비율 기준, 동률은 스쿼드 순서.
        # 부서진 엄폐물은 회복으로 되살아나지 않으므로(재생성 없음) 후보에서 뺀다.
        if target.startswith("allies_lowest_cover_hp:"):
            n = int(target.split(":")[1])
            cur, mx = self.state.get("cover_hp", {}), self.state.get("cover_max_hp", {})
            pool = [x for x in self._alive() if cur.get(x, 0.0) > 0.0 and mx.get(x, 0.0) > 0.0]
            pool.sort(key=lambda x: (cur[x] / mx[x], self.squad_names.index(x)))
            return pool[:n]
        # "엄폐물이 파괴된 아군 무작위 N기" — 위 키와 정확히 반대 필터다.
        # `allies_random:N`(자신 제외)과 달리 **시전자를 빼지 않는다**(원문에 제외 표기가 없다).
        # 지금 부서져 있는가가 곧 부여 시점 판정이라 지연 resolve 대상이 아니다.
        # 엄폐물은 보스 공격 패턴에만 부서지므로 기본 경로에서는 늘 0기 = 무발동이다.
        # 비스킷 `산책 훈련` (`cover_revive`의 대상)
        if target.startswith("allies_broken_cover_random:"):
            n = int(target.split(":")[1])
            pool = [x for x in self._alive() if not self.cover_alive(x)]
            return random.sample(pool, min(n, len(pool)))

        # "전투불능 상태 [클래스] 아군 무작위 N기" — `allies_down_` 접두사라
        # `_resolve_target()`의 전투불능 제외 규칙에서 함께 빠진다(마나 `매터 감마 3`과 같은 자리).
        # **원문에 「자신을 제외한」이 없으므로 시전자를 빼지 않는다** — 빼는 쪽은
        # `allies_down_top_atk_excl:N`이다. 무작위라 지연 resolve 대상이 아니고,
        # 후보가 N보다 적으면 있는 만큼·0기면 빈 목록이다.
        # 아군은 보스 공격 패턴이 있을 때만 쓰러지므로 기본 경로에서는 늘 0기다.
        # (앤 : 미라클 페어리 `파란 나비의 꿈 3` — `revive`의 대상)
        if target.startswith("allies_down_class_random:"):
            _, cls, n_raw = target.split(":")
            down = self.state.get("down") or ()
            pool = [x for x in self.squad_names
                    if x in down and _NIKKE[x]["class"] == cls]
            return random.sample(pool, min(int(n_raw), len(pool)))

        if target.startswith("allies_lowest_atk_burst3:"):
            n = int(target.split(":")[1])
            burst3 = [name for name in self._alive()
                      if _NIKKE.get(name, {}).get("burst_stage") == "3"]
            burst3.sort(key=self._effective_atk)
            return burst3[:n]

        if target.startswith("allies:"):
            n = int(target.split(":")[1])
            return self.squad_names[:n]
        if target.startswith("allies_top_atk:"):
            n = int(target.split(":")[1])
            return self._top_by("atk", n)
        if target.startswith("allies_top_atk_excl:"):
            n = int(target.split(":")[1])
            return self._top_by("atk", n, exclude=caster)
        if target.startswith("allies_lowest_hp:"):
            n = int(target.split(":")[1])
            return self._lowest_hp(n)
        if target.startswith("allies_lowest_hp_excl:"):
            n = int(target.split(":")[1])
            return self._lowest_hp(n, exclude=caster)
        if target.startswith("allies_top_def:"):
            n = int(target.split(":")[1])
            return self._top_by("def", n)
        if target.startswith("allies_random:"):
            n = int(target.split(":")[1])
            pool = [x for x in self._alive() if x != caster]
            return random.sample(pool, min(n, len(pool)))
        if target.startswith("allies_adjacent:"):
            n = int(target.split(":")[1])
            idx = self.squad_names.index(caster)
            adj = []
            if idx > 0:
                adj.append(self.squad_names[idx - 1])
            if idx < len(self.squad_names) - 1:
                adj.append(self.squad_names[idx + 1])
            return [caster] + adj[:n]
        # "최종 공격력이 가장 높은 [무기] 소지 아군 N기" — 무기 필터 ∩ 공격력 top N.
        # 시전자 포함(원문에 자신 제외 표기 없음). 매칭 아군이 N보다 적으면 있는 만큼.
        # 공격력 정렬이라 _LAZY_RESOLVE_PREFIXES 등록 필수. 레오나 `용기있는 시선 2`
        if target.startswith("allies_weapon_top_atk:"):
            _, wtype, cnt = target.split(":")
            pool = [c for c in self._alive()
                    if _NIKKE[c]["weapon_type"] == wtype]
            pool.sort(key=self._effective_atk, reverse=True)
            return pool[:int(cnt)]
        if target.startswith("allies_weapon_excl_self:"):
            wtype = target.split(":")[1]
            return [n for n in self.squad_names
                    if _NIKKE[n]["weapon_type"] == wtype and n != caster]
        if target.startswith("allies_weapon:"):
            wtype = target.split(":")[1]
            return [n for n in self.squad_names
                    if _NIKKE[n]["weapon_type"] == wtype]
        # "기본 차지 시간이 가장 긴 아군 N기" — 버프를 뺀 무기 표기 차지 시간 기준.
        # 고정 속성이라 lazy resolve가 필요 없다. 차지 무기 아군이 없으면 빈 리스트고,
        # 동률이면 스쿼드 입력 순서가 앞선 쪽이 이긴다(정렬 안정성). 마나 `매터 시그마 4`
        if target.startswith("allies_top_base_charge_time:"):
            n = int(target.split(":")[1])
            charged = [c for c in self.squad_names if (_NIKKE[c].get("charge_time") or 0.0) > 0]
            charged.sort(key=lambda c: -_NIKKE[c]["charge_time"])
            return charged[:n]
        # "[버프명] 상태인 아군 전체" — 부여 시점 스냅샷(비lazy).
        # 상태 판정은 self_state:와 같은 창구를 써서 weapon_change 모드도 함께 본다.
        if target.startswith("allies_with_buff:"):
            buff_name = target.split(":", 1)[1]
            return [n for n in self.squad_names if self._has_self_state(n, buff_name)]
        # "[버프명] 상태가 아닌 아군 전체" — 위의 여집합이고 판정 창구도 같다.
        # **재부여를 막는 대상 필터**라 같은 clause에서 그 상태를 부여하는 항목보다
        # 다른 항목을 앞에 두어야 한다 (크러스트 `든든한 요리` — PARSING.md Step 7 §담체).
        if target.startswith("allies_without_buff:"):
            buff_name = target.split(":", 1)[1]
            return [n for n in self.squad_names if not self._has_self_state(n, buff_name)]
        # "해로운 효과 소지 아군 중 무작위 N기" — 보유자만 거른 뒤 무작위.
        # `allies_random:N`과 달리 **시전자를 제외하지 않는다**(원문에 제외 표기가 없다).
        # 보유 판정 시점이 곧 부여 시점이라 지연 resolve 대상이 아니다. 코코아 `프로 종이접기 2`
        if target.startswith("allies_random_with_debuff:"):
            n = int(target.split(":")[1])
            pool = [x for x in self._alive() if self._has_harmful(x)]
            return random.sample(pool, min(n, len(pool)))
        # "직전에 버스트 스킬을 사용한 [무기] 아군 전체" — burst_casted ∩ 무기유형.
        # burst_casted condition은 시전자 기준으로만 평가돼 대상 필터로 쓸 수 없어 target으로 둔다.
        if target.startswith("allies_burst_casted_weapon:"):
            wtype = target.split(":", 1)[1]
            casted = self.state.get("burst_casted", {})
            return [n for n in self.squad_names
                    if casted.get(n) and _NIKKE[n]["weapon_type"] == wtype]
        # `allies_class:클래스` (전체) · `allies_class:클래스:N` (인원수 제한).
        # N이 붙으면 **스쿼드 입력 순서로 앞 N명**이다 — 원문 「방어형 아군 2기에게」에는
        # 정렬 기준(`가장 ~한`)이 없고, 같은 모양인 `아군 N기에게`(`allies:N`)가
        # `squad_names[:n]`이라 같은 규약으로 읽는다. 고정 속성 기반이라 지연 resolve가 아니다.
        # **세 칸짜리를 두 칸으로 읽던 동안에는 인원수가 조용히 무시돼 「전체」가 됐다**
        # (키리 `훑어보기`·`곁눈질 2` — 방어형 3명 스쿼드에서 셋 다 받았다, 2026-09-23 수정).
        if target.startswith("allies_class:"):
            parts = target.split(":")
            cls = parts[1]
            pool = [n for n in self.squad_names if _NIKKE[n]["class"] == cls]
            if len(parts) > 2 and parts[2].isdigit():
                return pool[:int(parts[2])]
            return pool
        # "동일 스쿼드 아군 전체" — 소속 스쿼드(`parsed_nikke["squad"]`, 앱솔루트·카운터스
        # ·이지스 등)가 시전자와 같은 아군. **시전자 포함**이고, 스쿼드가 없는 더미
        # (`test_B*`)는 빠진다 — condition `squad_ally_exists`와 같은 기준의 대상판이다.
        # 소속이 없으면 자기 자신만 남는다(빈 리스트가 아니다 — 원문의 "동일 스쿼드"에는
        # 언제나 자신이 들어간다).
        if target == "allies_squad":
            my_squad = _NIKKE.get(caster, {}).get("squad")
            if not my_squad:
                return [caster]
            return [n for n in self.squad_names
                    if n == caster or _NIKKE.get(n, {}).get("squad") == my_squad]
        # "자신을 제외한 [코드] 아군 전체" — 시전자 포함판(`allies_code:`)과 원문이 갈린다.
        # 메이든 : 아이스 로즈 `블레스 유`는 아군판과 자기판이 배타 분기라, 시전자를 빼지
        # 않으면 MP≥1 사이클에 자기가 양쪽을 다 받는다 (`docs/scenarios/메이든 _ 아이스 로즈.md`)
        if target.startswith("allies_code_excl_self:"):
            code = target.split(":")[1]
            return [n for n in self.squad_names
                    if _NIKKE[n].get("element_code") == code and n != caster]
        if target.startswith("allies_code:"):
            code = target.split(":")[1]
            return [n for n in self.squad_names if _NIKKE[n].get("element_code") == code]
        # 코드 + 무기유형 복합. leftmost는 스쿼드 입력 순서 기준 앞에서 N명
        # (고정 속성 기반이므로 lazy resolve 불필요)
        if target.startswith("allies_code_weapon_leftmost:"):
            _, code, wtype, n = target.split(":")
            return self._alive(self._code_weapon(code, wtype))[:int(n)]
        if target.startswith("allies_code_weapon:"):
            _, code, wtype = target.split(":")
            return self._code_weapon(code, wtype)
        if target.startswith("allies_below_def"):
            # 원문이 "자신보다 **최종** 방어력이 낮은 아군" → 버프 반영 후 방어력으로 비교.
            # 기본 방어력으로 비교하면 같은 클래스·무기 아군(예: 방어형 RL 딜러)이
            # 시전자와 값이 같아 탈락한다.
            caster_def = self._effective_def(caster)
            return [n for n in self.squad_names if self._effective_def(n) < caster_def]
        if target == "allies_burst3":
            burst_stages = self.state.get("burst_stages", {})
            return [n for n in self.squad_names if burst_stages.get(n) == "3"]
        # "자신을 제외한 기본 버스트 단계 Step3인 페르소나 상태 아군 전체".
        # 페르소나 상태 = persona_state 마커 버프 보유. allies_with_buff:와 달리
        # 버프 이름이 캐릭터마다 다르므로(요한나/코노하나사쿠야) stat으로 판정한다.
        if target == "allies_burst3_persona_excl_self":
            burst_stages = self.state.get("burst_stages", {})
            return [n for n in self.squad_names
                    if n != caster and burst_stages.get(n) == "3" and self._has_persona_state(n)]
        # "직전에 버스트 스킬을 사용한 기본 버스트 단계 Step 3 아군" — burst_casted ∩ B3.
        # allies_burst_casted_weapon:과 같은 취지다 — burst_casted를 condition으로 두면
        # 시전자 기준으로만 평가돼 "누가 버스트를 썼나"를 대상 필터로 쓸 수 없다.
        if target == "allies_burst_casted_burst3":
            casted = self.state.get("burst_casted", {})
            burst_stages = self.state.get("burst_stages", {})
            return [n for n in self.squad_names
                    if casted.get(n) and burst_stages.get(n) == "3"]

        # 적 관련 (타임라인 처리)
        # `same_target:[이름]`도 같은 적을 가리킨다 — 접두사까지 봐야 []로 새지 않는다.
        if (target.startswith("enemies") or target.startswith("same_target:")
                or target in ("target", "target_body", "same_target")):
            return self._resolve_enemies(target, caster)

        # 커버, 발사체 등
        return []

    def _resolve_enemies(self, target: str, caster: str = "") -> list[str]:
        """적 대상 문자열 → 적 id 목록. 쫄몹이 없으면 늘 `["__enemy__"]`(단일 보스 센티널)이고, 살아 있으면
        보스 패턴이 규칙대로 고른다 — 조준 규칙은 시전자가 겨눈 적이다(정본: boss_pattern.py §쫄몹·§조준)."""
        if self.enemy_resolver is not None:
            got = self.enemy_resolver(target, caster)
            if got is not None:
                return got
        return ["__enemy__"]

    def _code_weapon(self, code: str, wtype: str) -> list[str]:
        """코드·무기유형 둘 다 일치하는 아군을 스쿼드 입력 순서대로 반환."""
        return [n for n in self.squad_names
                if _NIKKE[n].get("element_code") == code
                and _NIKKE[n].get("weapon_type") == wtype]

    def _effective_atk(self, name: str) -> float:
        """활성 버프(atk_pct, atk_flat)를 반영한 최종 공격력. 타겟 정렬용."""
        base = self.state.get("base_stats", {}).get(name, {}).get("atk", 0.0)
        atk_pct = 0.0
        atk_flat = 0.0
        for ab in self._active:
            if name not in (ab.target_chars or []):
                continue
            stat = ab.effect.get("stat", "")
            if stat == "atk_pct":
                v = self._get_value(ab.effect, ab, name)
                if v is not None:
                    atk_pct += v
            elif stat == "atk_caster_based_pct":
                v = self._get_value(ab.effect, ab, ab.caster)
                if v is not None:
                    caster_atk = self.state.get("base_stats", {}).get(ab.caster, {}).get("atk", 0.0)
                    atk_flat += caster_atk * (v / 100.0)
            elif stat == "atk_flat":
                v = self._get_value(ab.effect, ab, name)
                if v is not None:
                    atk_flat += v
        return base * (1 + atk_pct / 100) + atk_flat

    def _capture_scaling_stack(self, eff: dict, caster: str) -> int | None:
        """`scaling: stack_count` + `scaling_ref` 버프의 참조 중첩을 발동 시점 값으로 고정.

        인게임에서는 "[분배 대미지 X% X 취기 중첩 수 ▲] [10초 유지]"처럼 **거는 순간의**
        중첩 수로 값이 정해지고, 이후 참조 중첩이 깎여도 이미 걸린 버프는 그대로 간다.
        조회 시점에 실시간으로 읽으면, 앵커 : 이노센트 메이드가 취기(해로운 효과)를
        1개 벗기는 순간 마스트 : 로망틱 메이드가 3중첩으로 걸어둔 버프까지 같이 내려앉는다.

        지속(duration -1) 버프는 게이지를 실시간 추종하는 쪽이 맞으므로 제외한다
        (솔린 : 프로스트 티켓 [티켓 효과] 등 — 한 번 등록되고 갱신되지 않아
         고정하면 초기값에 얼어붙는다).
        """
        if eff.get("scaling") != "stack_count" or not eff.get("scaling_ref"):
            return None
        duration = eff.get("duration")
        if duration is None or duration == -1:
            return None
        return self.ref_count(caster, eff["scaling_ref"])

    def _caster_based_def_flat(self, ab: "ActiveBuff", pct: float) -> float:
        """`시전자 기준 방어력 N%`를 **정액** 방어력으로 환산한다 (적 대상판).

        기준은 시전자의 **기본**(버프 제외) 방어력이다 — `시전자 기준` 문형의 공통 규약이고
        (`atk_caster_based_pct`·`hp_only_caster_based_pct`와 같다), 아군판
        `_effective_def()`의 `def_caster_based_pct` 분기도 같은 값을 쓴다.
        `pct`는 `_get_value()`가 이미 중첩까지 곱해 넘긴 값이라 여기서는 곱하지 않는다.
        """
        caster_def = self.state.get("base_stats", {}).get(ab.caster, {}).get("def", 0.0)
        return caster_def * (pct / 100.0)

    def _caster_based_atk_flat(self, ab: "ActiveBuff", pct: float) -> float:
        """`시전자 기준 공격력 N%`를 **정액** 공격력으로 환산한다 (적 대상판).

        위 `_caster_based_def_flat`의 공격력판이고 규약이 같다 — 기준은 시전자의
        **기본**(버프 제외) 공격력이고, `pct`는 `_get_value()`가 이미 중첩까지 곱해 넘긴 값이다.
        아군판(`get_buffs()` 후처리의 `atk_caster_based_pct` 루프)도 같은 `base_stats` ATK를 읽는다.
        """
        caster_atk = self.state.get("base_stats", {}).get(ab.caster, {}).get("atk", 0.0)
        return caster_atk * (pct / 100.0)

    def enemy_atk_down_flat(self, enemy_id: str, t: float) -> float:
        """적에게 걸린 `atk_caster_based_pct`의 **정액** 공격력 증감 합(감소면 음수).

        적 대상 `def_caster_based_pct` → `enemy_def_down_flat`의 공격력판이다(키리 `곁눈질`).
        다만 소비처가 하나뿐이라 — 보스 → 니케 피해(`timeline._boss_attack`) — `get_buffs()`의
        버프 사전에 자리를 두지 않고 **직접 조회**한다(`cover_hp_pct`·`heal_received_mult`와 같은 자리).
        딜 계산에는 닿지 않는다: 적 공격력은 니케가 적을 때리는 식에 들어가지 않는다.

        아군 대상 `atk_caster_based_pct`는 여기 오지 않는다 — `enemy_id`가 `__enemy__` 센티널
        (또는 쫄몹 id)이라 아군에게만 걸린 버프는 `target_chars`에 그 id가 없다.
        """
        total = 0.0
        for ab in self._by_stat("atk_caster_based_pct"):
            if t >= ab.expires_at:
                continue
            tgts = ab.target_chars if ab.target_chars is not None else self._resolve_lazy(ab)
            if enemy_id not in (tgts or ()):
                continue
            if ab.has_runtime_conditions:
                conditions = ab.effect["trigger"].get("condition", [])
                if not self._runtime_condition_ok(conditions, ab.caster, ab.caster, enemy_id, t):
                    continue
            val = self._get_value(ab.effect, ab, ab.caster)
            if val is None:
                continue
            total += self._caster_based_atk_flat(ab, val)
        return total

    def _effective_def(self, name: str) -> float:
        """활성 버프(def_pct, def_caster_based_pct)를 반영한 최종 방어력.
        allies_below_def 판정용 — 원문 기준이 "최종 방어력"이다."""
        base = self.state.get("base_stats", {}).get(name, {}).get("def", 0.0)
        def_pct = 0.0
        def_flat = 0.0
        for ab in self._active:
            if name not in (ab.target_chars or []):
                continue
            stat = ab.effect.get("stat", "")
            if stat == "def_pct":
                v = self._get_value(ab.effect, ab, name)
                if v is not None:
                    def_pct += v
            elif stat == "def_caster_based_pct":
                v = self._get_value(ab.effect, ab, ab.caster)
                if v is not None:
                    caster_def = self.state.get("base_stats", {}).get(ab.caster, {}).get("def", 0.0)
                    def_flat += caster_def * (v / 100.0)
        return base * (1 + def_pct / 100) + def_flat

    def _top_by(self, stat: str, n: int, exclude: str | None = None) -> list[str]:
        pool = [name for name in self._alive() if name != exclude]
        if stat == "atk":
            pool.sort(key=self._effective_atk, reverse=True)
        else:
            base_stats = self.state.get("base_stats", {})
            pool.sort(key=lambda x: base_stats.get(x, {}).get(stat, 0), reverse=True)
        return pool[:n]

    def _lowest_hp(self, n: int, exclude: str | None = None) -> list[str]:
        hp_pct = self.state.get("hp_pct", {})
        names = [x for x in self._alive() if x != exclude]
        # 동률이면 squad_names 순서(앞쪽 우선)로 결정
        pool = sorted(names,
                      key=lambda x: (hp_pct.get(x, 100.0), self.squad_names.index(x)))
        return pool[:n]

    # ── 편의 메서드 ───────────────────────────────────────────────────────

    def is_weapon_changed(self, caster: str) -> bool:
        """캐릭터가 현재 무기 변경 상태인지 반환."""
        return caster in self.state.get("weapon_change", {})

    def get_weapon_change(self, caster: str) -> dict | None:
        """
        현재 활성 weapon_change effect 반환. 없으면 None.
        타임라인이 발사 루프 교체 및 weapon_hit notify에 사용.
        """
        info = self.state.get("weapon_change", {}).get(caster)
        return info["effect"] if info else None

    def end_weapon_change(self, caster: str, t: float | None = None):
        """
        duration_bullets 소진·토글 해제 등으로 무기 변경을 종료할 때 호출.
        t를 주면 event:state_end:{모드명}을 발생시킨다 (모드 종료에 반응하는 효과용).
        """
        info = self.state.get("weapon_change", {}).pop(caster, None)
        if info is None:
            return
        self._invalidate_buffs_cache()
        name = info["effect"].get("name", "")
        if t is not None and name:
            self.notify(f"event:state_end:{name}", t, caster)

    def consume_bullet_buffs(self, caster: str, t: float = 0.0):
        """발사 1회 소모 시 duration_bullets 기반 버프 카운트를 차감하고 소진된 버프를 제거."""
        to_remove = []
        for ab in self._active:
            # 이번 발사 중 막 활성화된 버프는 소모하지 않음 (첫 발사도 효과에 포함).
            # 단, 트리거가 발사와 같은 프레임에 발동하는 종류(full_charge 등)이면
            # 그 발사가 곧 "1발" 자체이므로 카운트해야 함 (예: 효과 +X%/1bul).
            if ab.activated_at == t and not _is_bullet_bound_trigger(ab.effect):
                continue

            # lazy-target duration_bullets: 타겟을 여기서 확정하고 per-target 카운터로 옮긴다.
            # get_buffs가 먼저 조회했다면 이미 옮겨져 있다 — 어느 쪽이 먼저든 결과가 같아야
            # 하므로 같은 헬퍼를 쓴다 (아래 "시전자 본인 발사" 분기로 새는 것을 막는다).
            if ab.target_chars is None and ab.bullets_left != -1:
                self._resolve_lazy(ab)
                self._invalidate_buffs_cache()

            # 캐릭터별 독립 카운터 (다중 target duration_bullets 버프)
            if caster in ab.bullets_per_target:
                ab.bullets_per_target[caster] -= 1
                if ab.bullets_per_target[caster] <= 0:
                    del ab.bullets_per_target[caster]
                    if ab.target_chars is not None:
                        ab.target_chars = [c for c in ab.target_chars if c != caster]
                    self._invalidate_buffs_cache()
                    eff_name = ab.effect.get("name", "")
                    if self._buff_event_handler and eff_name:
                        self._buff_event_handler("expire", eff_name, ab.caster, caster, t, t)
                    if not ab.bullets_per_target:  # 모든 대상 소진 → 버프 전체 제거
                        to_remove.append(ab.uid)
                continue

            # 시전자 본인 발사로 소모 (self-target 포함 비lazy 단일 카운터)
            if ab.caster != caster or ab.bullets_left == -1:
                continue
            ab.bullets_left -= 1
            if ab.bullets_left <= 0:
                to_remove.append(ab.uid)

        removed_ids = set(to_remove)
        removed_buffs = [ab for ab in self._active if ab.uid in removed_ids]
        if removed_ids:
            self._invalidate_buffs_cache()
        self._active = [ab for ab in self._active if ab.uid not in removed_ids]
        for ab in removed_buffs:
            name = ab.effect.get("name", "")
            if name:
                self.notify(f"event:state_end:{name}", t, ab.caster)
                if self._buff_event_handler:
                    for tgt in (ab.target_chars or []):
                        self._buff_event_handler("expire", name, ab.caster, tgt, t, t)

    def battle_start(self, t: float = 0.0):
        """전투 시작 시 모든 캐릭터에 대해 battle_start 이벤트 발생."""
        for name in self.squad_names:
            self.notify("battle_start", t, name)
        # 단일 보스 가정: 전투 시작 시 적 등장 이벤트 발생
        for name in self.squad_names:
            self.notify("event:enemy_spawn", t, name)

    def reset(self):
        """전투 초기화."""
        self._active.clear()
        self._next_fire.clear()
        self._dot_timers.clear()
        self._ramp_pending.clear()
        self._instant_timers.clear()
        self._accum_last.clear()
        self._lazy_target_cache.clear()
        self._event_counts.clear()
        self.partial_skips = 0
        self._down_lost.clear()
        self._trigger_counts.clear()
        self._buffs_cache.clear()
        self._plan_cache.clear()
        self._stat_index.clear()
        self._name_index_cache.clear()
        self._cache_version = 0
        self._cond_passive_prev.clear()
        self._immune_used.clear()

        self.state.pop("weapon_change", None)
        self.state.pop("feathers", None)


# ── 간단 테스트 ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    squad = [
        {
            "name": "크라운",
            "level": 200, "breakthrough": 3, "core_enhancement": 7,
            "affinity": 30, "skill_levels": {"1": 10, "2": 10, "3": 10},
            "equipment": {
                "머리": {"level": 5, "skills": [{"id": "atk_pct", "lv": 15}]},
                "몸통": {"level": 5, "skills": [{"id": "atk_pct", "lv": 15}]},
                "팔":   {"level": 5, "skills": [{"id": "crit_rate", "lv": 15}]},
                "다리": {"level": 5, "skills": []},
            },
            "cube": {"name": "공통", "level": 15},
            "console": {"common_level": 10, "class_level": 10, "company_level": 10},
            "collection_stage": "SR15",
        },
    ]

    state = {"full_burst": False, "hp_pct": {"크라운": 100.0}, "hp": {"크라운": 0.0}, "base_stats": {}}

    bm = BuffManager(squad, state)
    bm.battle_start(0.0)
    bm.tick(0.0)

    buffs = bm.get_buffs("크라운", "크라운", 0.0)
    print("크라운 self 버프:")
    for k, v in buffs.items():
        if v:
            print(f"  {k}: {v}")
