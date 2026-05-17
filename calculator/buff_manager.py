"""
Phase 3-C: 버프 관리자

설계:
  - 효과 등록(parsed_skills, 장비, 큐브, 소장품) → 통일된 effect 포맷
  - notify(event, t, caster) → timing 매칭 시 ActiveBuff 생성/갱신
  - tick(t) → 만료 버프 제거, every:Ns 스킬 쿨타임 추적
  - get_buffs(caster, target, t) → condition 재평가 후 buffs 딕셔너리 반환

버프 합산 규칙:
  - 대부분 stat: 단순 합산
  - crit_rate: 독립 확률 합성 (1 - ∏(1 - p_i))
"""

from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass, field
from typing import Any

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_TABLE_DIR = os.path.join(_DATA_DIR, "base_stat_tables")


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_NIKKE = _load(os.path.join(_DATA_DIR, "parsed_nikke.json"))
_PARSED_SKILLS = _load(os.path.join(_DATA_DIR, "parsed_skills.json"))
_EQUIP_SKILLS = _load(os.path.join(_TABLE_DIR, "equipment_skills.json"))
_CUBE = _load(os.path.join(_TABLE_DIR, "cube.json"))
_COLLECTION = _load(os.path.join(_TABLE_DIR, "collection.json"))

# ── 빈 buffs 딕셔너리 템플릿 ──────────────────────────────────────────────

_BUFFS_ZERO: dict[str, Any] = {
    "atk_pct":          0.0,
    "atk_flat":         0.0,
    "def_ignore_pct":   0.0,
    "crit_rate":        0.0,   # 독립 확률 합성으로 처리
    "crit_dmg":         0.0,
    "core_dmg_pct":     0.0,
    "atk_dmg_pct":                  0.0,
    "burst_dmg_pct":                0.0,
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
    "received_dmg":     0.0,
    "element_bonus_pct": 0.0,
    "is_element_match": False,
    "def_pct":          0.0,
    "charge_speed_pct": 0.0,
    "charge_time_fixed": False,
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
    "burst_cooldown":   0.0,  # 버스트 쿨타임 감소 (buff 상태로 지속)
    "max_hp_pct":       0.0,  # 최대 체력 + 현재 체력 동반 증가
    "max_hp_only_pct":  0.0,  # 최대 체력만 증가 (현재 체력 유지)
    "lifesteal_pct":    0.0,
    "def_caster_based_pct": 0.0,
    "taunt":            False,
    "pierce_enabled":   False,
    "attack_speed_pct": 0.0,
    "pellet_count":     0.0,
    "pellet_count_fixed": 0.0,  # >0이면 펠릿 수를 이 값으로 고정 (절대값)
    "fullburst_duration": 0.0,  # 풀버스트 타임 지속 시간 증감 (초)
    "skill_cooldown_pct": 0.0,  # 스킬 쿨타임 % 감소 (음수 = 감소)
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
    "received_dmg_pct":     "received_dmg",
    "element_bonus_pct":    "element_bonus_pct",
    "def_pct":              "def_pct",
    "charge_speed_pct":     "charge_speed_pct",
    "charge_speed_caster_based_pct": "charge_speed_pct",  # _get_value에서 시전자 charge_time 기준 환산
    "charge_time_fixed":    "charge_time_fixed",
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
    "burst_cooldown":       "burst_cooldown",
    "max_hp_pct":           "max_hp_pct",
    "max_hp_only_pct":      "max_hp_only_pct",
    "lifesteal_pct":        "lifesteal_pct",
    "def_caster_based_pct": "def_caster_based_pct",
    "taunt":                "taunt",
    "pierce_enabled":       "pierce_enabled",
    "attack_speed_pct":     "attack_speed_pct",
    "pellet_count":         "pellet_count",
    "pellet_count_fixed":   "pellet_count_fixed",
    "fullburst_duration":   "fullburst_duration",
    "skill_cooldown_pct":   "skill_cooldown_pct",
}

# crit_rate 합성에 독립 확률 처리가 필요한 stat 집합
_CRIT_RATE_STATS = {"crit_rate", "normal_atk_crit_rate"}

# 활성화 시점이 아닌 get_buffs 시점에 타겟을 결정해야 하는 target 패턴
# (스탯 비교 기반 → 버프가 모두 반영된 후 순위가 정해져야 함)
_LAZY_RESOLVE_PREFIXES = (
    "allies_lowest_atk_burst3:",
    "allies_top_atk:",
    "allies_top_atk_excl:",
    "allies_lowest_hp:",
    "allies_top_def:",
    "allies_below_def",
    "allies_random:",
)


# ── ActiveBuff ────────────────────────────────────────────────────────────

@dataclass
class ActiveBuff:
    effect: dict           # parsed effect 항목 원본
    caster: str            # 시전자 캐릭터명
    target_chars: list | None  # None = 지연 resolve (get_buffs 시점에 결정)
    activated_at: float
    expires_at: float      # math.inf = 영구
    stack: int = 1
    trigger_count: int = 1
    bullets_left: int = -1  # duration_bullets 기반 만료용. -1이면 미사용


# ── BuffManager ───────────────────────────────────────────────────────────

class BuffManager:
    """
    5인 팀 버프/디버프 관리자.

    Parameters
    ----------
    team : list[dict]
        캐릭터 인스턴스 목록 (base_stat.py와 동일 구조, skill_level 추가)
    state : dict
        타임라인 공유 상태. 최소 키: "full_burst", "burst_casted"
        필요에 따라 타임라인이 채워준다.
    """

    def __init__(self, team: list[dict], state: dict | None = None):
        self.team = team
        self.team_names = [c["name"] for c in team]
        self.state = state or {}

        # 캐릭터명 → 인스턴스 빠른 접근
        self._char = {c["name"]: c for c in team}

        # 등록된 효과 목록: (effect, caster_name)
        self._effects: list[tuple[dict, str]] = []

        # 활성 버프 목록
        self._active: list[ActiveBuff] = []

        # every:Ns 효과별 다음 발동 시각
        self._next_fire: dict[int, float] = {}  # id(effect) → next_t

        # tick_interval damage 효과별 타이머: id(effect) → (caster, next_t, expires_at)
        self._dot_timers: dict[int, tuple[str, float, float]] = {}

        # 이벤트별 발동 횟수 (hit_count, burst_cast_count 등 추적용)
        self._event_counts: dict[str, dict[str, int]] = {}  # caster → {event_key: count}

        # max_trigger 추적: id(effect) → 발동 횟수 (buff/instant/damage/weapon_change 공통)
        self._trigger_counts: dict[int, int] = {}

        # instant stat → 핸들러. 타임라인이 register_instant_handler()로 주입
        self._instant_handlers: dict[str, Any] = {}

        # damage 효과 핸들러. 타임라인이 register_damage_handler()로 주입
        self._damage_handler: Any = None

        # 버프 활성/만료 이벤트 콜백. 타임라인이 register_buff_event_handler()로 주입
        # handler(kind, name, caster, target, t, expires_at)
        self._buff_event_handler: Any = None

        # get_buffs 캐시: (caster, t, _cache_version) → buffs dict
        self._buffs_cache: dict = {}
        self._cache_version: int = 0

        # notify 인덱스: event_key → [(eff, caster), ...]
        # caster별: _notify_index[caster][event_key] = [(eff, caster), ...]
        # team_ammo_consume 전용: _team_notify_index[event_key] = [(eff, caster), ...]
        self._notify_index: dict[str, dict[str, list]] = {}
        self._team_notify_index: dict[str, list] = {}

        self._register_all()

    # ── 등록 ─────────────────────────────────────────────────────────────

    def _register_all(self):
        """팀 전원의 모든 버프 소스를 효과 목록에 등록."""
        for char in self.team:
            name = char["name"]
            # parsed_skills
            for eff in _PARSED_SKILLS.get(name, []):
                self._effects.append((eff, name))
            # 장비 스킬 (부위별 개별 옵션)
            for part_data in char["equipment"].values():
                for sk in part_data.get("skills", []):
                    eff = self._make_equip_effect(sk["id"], sk["lv"])
                    if eff:
                        self._effects.append((eff, name))
            # 장비 옵션 합산값 (equip_skills)
            for stat, val in char.get("equip_skills", {}).items():
                eff = self._make_equip_effect(stat, None, fixed_val=float(val))
                if eff:
                    eff = {**eff, "name": "장비 옵션"}
                    self._effects.append((eff, name))
            # 큐브 고유 스킬
            cube_name = char["cube"]["name"]
            cube_lv = char["cube"]["level"]
            eff = self._make_cube_effect(cube_name, cube_lv)
            if eff:
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
        if timing.startswith("full_burst_start_count:"):
            return "full_burst_start"
        if timing.startswith("full_burst_end_count:"):
            return "full_burst_end"
        if timing.startswith("full_charge_count:"):
            return "full_charge_hit"
        if timing.startswith("hit_count:"):
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
        if timing.startswith("team_ammo_consume:"):
            return "team_ammo_consume"
        # 나머지는 timing 자체가 event 키
        return timing

    def _build_notify_index(self):
        """_effects 로부터 notify 인덱스를 구축."""
        self._notify_index.clear()
        self._team_notify_index.clear()
        valid_types = ("buff", "instant", "weapon_change", "damage")

        for eff, eff_caster in self._effects:
            if eff.get("type") not in valid_types:
                continue
            for timing in eff["trigger"]["timing"]:
                key = self._timing_to_index_key(timing)
                if key is None:
                    continue
                if timing.startswith("team_ammo_consume:"):
                    bucket = self._team_notify_index.setdefault(key, [])
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
        }

    def _make_cube_effect(self, cube_name: str, cube_lv: int) -> dict | None:
        entry = _CUBE.get(cube_name)
        if not entry or cube_name.startswith("_"):
            return None
        val = float(entry["values"][str(cube_lv)][0])
        return {
            "type": "buff",
            "name": f"큐브:{cube_name}",
            "trigger": {"timing": ["battle_start"], "condition": []},
            "target": "self",
            "stat": entry["stat"],
            "polarity": "beneficial",
            "fixed_value": val,
            "duration": None,
            "_source_tag": "cube",
        }

    def _make_collection_effects(self, char: dict) -> list[dict]:
        stage = char["collection_stage"]
        entry = _COLLECTION["_stat_table"][stage]
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

    def _dispatch_instant(self, eff: dict, caster: str, t: float):
        """instant 효과를 핸들러로 라우팅하거나 내장 로직으로 처리."""
        stat = eff.get("stat", "")
        char = self._char.get(caster, {})
        skill_lv = str(char.get("skill_level", 10))

        val: float | None
        if "fixed_value" in eff:
            val = float(eff["fixed_value"])
        elif "values" in eff:
            vals = eff["values"]
            val = float(vals.get(skill_lv, vals.get("10", 0.0)))
        else:
            val = None

        # ── 내장 처리 ──────────────────────────────────────────────────────

        # buff_stack_add / buff_stack_remove
        if stat in ("buff_stack_add", "buff_stack_remove"):
            target_name = eff.get("target_effect", "")
            delta = int(val or 1) if stat == "buff_stack_add" else -int(val or 1)
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
                ab.stack = max(1, min(ab.stack + delta, cap))
            return

        # debuff_stack_add / debuff_stack_remove
        if stat in ("debuff_stack_add", "debuff_stack_remove"):
            target_name = eff.get("target_effect", "")
            # scaling:stack_count + scaling_ref → 참조 게이지/스택 값을 delta로 사용
            raw_delta = int(val or 1)
            if eff.get("scaling") == "stack_count":
                ref = eff.get("scaling_ref", "")
                if ref:
                    gauge_val = self.state.get("gauges", {}).get(caster, {}).get(ref, 0.0)
                    if gauge_val:
                        raw_delta = int(gauge_val)
                    else:
                        for _ab in self._active:
                            if _ab.caster == caster and _ab.effect.get("name") == ref:
                                raw_delta = _ab.stack
                                break
            delta = raw_delta if stat == "debuff_stack_add" else -raw_delta
            target_chars = self._resolve_target(eff.get("target", "self"), caster)
            for ab in self._active:
                if ab.effect.get("name") != target_name:
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
                ab.stack = max(0, min(ab.stack + delta, cap))
            return

        # debuff_cleanse: 대상의 harmful 버프 제거 (harmful_irremovable은 제거 불가)
        if stat == "debuff_cleanse":
            target_chars = self._resolve_target(eff.get("target", "self"), caster)
            self._invalidate_buffs_cache()
            self._active = [
                ab for ab in self._active
                if not (
                    ab.effect.get("polarity") == "harmful"
                    and any(tc in (ab.target_chars or []) for tc in target_chars)
                )
            ]
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
            if self._buff_event_handler:
                for ab in to_remove:
                    if ab.effect.get("name"):
                        for tgt in (ab.target_chars or []):
                            self._buff_event_handler("expire", ab.effect["name"], ab.caster, tgt, t, t)
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
                    to_remove.append(id(ab))
            if to_remove:
                self._invalidate_buffs_cache()
            self._active = [ab for ab in self._active if id(ab) not in to_remove]
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
            else:  # gauge_consume / gauge_consume_as_ammo
                if val == -1.0:  # fixed_value: -1 = 전체 소모
                    consumed = current
                    gauges[gauge_id] = 0.0
                else:
                    consumed = min(val, current)
                    gauges[gauge_id] = max(0.0, current - val)
                # gauge_consume_as_ammo: 실제 소모량만큼 team_ammo_consume 이벤트 발생
                if stat == "gauge_consume_as_ammo" and consumed > 0:
                    for _ in range(int(consumed)):
                        self.notify("team_ammo_consume", t, caster)
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
        """
        # team_ammo_consume: 팀 전체 탄환 소비 카운터 — caster와 무관하게 합산, 모든 팀원 효과 순회
        if event == "team_ammo_consume":
            team_counts = self._event_counts.setdefault("__team__", {})
            team_counts[event] = team_counts.get(event, 0) + 1
            current_count = team_counts[event]
            for eff, eff_caster in self._team_notify_index.get(event, []):
                for timing in eff["trigger"]["timing"]:
                    if self._timing_match(timing, event, current_count, t, eff, eff_caster):
                        if self._condition_ok(eff["trigger"].get("condition", []), eff_caster, t, eff):
                            self._activate(eff, eff_caster, t)
                        break
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
                    if is_passive or self._condition_ok(eff["trigger"].get("condition", []), caster, t, eff):
                        self._activate(eff, caster, t)
                    break

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

        # burst_cast_count:N
        if timing.startswith("burst_cast_count:") and event == "burst_cast":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count == int(raw)

        # full_burst_start_count:N
        if timing.startswith("full_burst_start_count:") and event == "full_burst_start":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count == int(raw)

        # full_burst_end_count:N
        if timing.startswith("full_burst_end_count:") and event == "full_burst_end":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count == int(raw)

        # full_charge_count:N  (trigger_count_reduce 버프로 N 감소 가능)
        if timing.startswith("full_charge_count:") and event == "full_charge_hit":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

        # hit_count:N  (trigger_count_reduce 버프로 N 감소 가능)
        # hit_count:{0} 형태면 trigger_values에서 현재 스킬 레벨 기준 N을 꺼냄
        if timing.startswith("hit_count:") and event == "hit_count":
            raw = timing.split(":")[1]
            if raw.startswith("{") and raw.endswith("}"):
                tv = eff.get("trigger_values", {})
                if tv:
                    char = self._char.get(caster, {})
                    skill_lv = str(char.get("skill_level", 10))
                    raw = str(tv.get(skill_lv, tv.get("10", raw)))
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

        # burst_enter:N
        if timing.startswith("burst_enter:") and event.startswith("burst_enter:"):
            return timing == event

        # team_burst_cast:N
        if timing.startswith("team_burst_cast:") and event.startswith("team_burst_cast:"):
            return timing == event

        # core_hit:N  (trigger_count_reduce 버프로 N 감소 가능)
        if timing.startswith("core_hit:") and event == "core_hit":
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

        # received_hit:N
        if timing.startswith("received_hit:") and event == "received_hit":
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

        # team_ammo_consume:N — 팀 전체 탄환 소비 누적 N발마다 발동
        if timing.startswith("team_ammo_consume:") and event == "team_ammo_consume":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            return count % int(raw) == 0

        return False

    def _condition_ok(self, conditions: list, caster: str, t: float, eff: dict | None = None) -> bool:
        """발동 시점 조건 평가. False이면 발동 안 함."""
        # burst_casted 계열 조건 평가 기준 캐릭터:
        # effect의 target이 단일 캐릭터 이름(팀원)이면 그 캐릭터 기준, 아니면 caster 기준
        raw_target = eff.get("target", "") if eff else ""
        burst_check_char = (
            raw_target if isinstance(raw_target, str) and raw_target in self.team_names
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
            elif cond == "burst_casted":
                if not self.state.get("burst_casted", {}).get(burst_check_char):
                    return False
            elif cond == "burst_not_casted":
                if self.state.get("burst_casted", {}).get(burst_check_char):
                    return False
            elif cond.startswith("prob:"):
                p = float(cond.split(":")[1]) / 100
                if random.random() >= p:
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
            elif cond == "back_row":
                idx = self.team_names.index(caster)
                if idx < 2:  # 앞 두 자리 = 전열
                    return False
            elif cond == "squad_ally_exists":
                # 같은 스쿼드 아군이 있으면 True (5인 팀에서 항상 True)
                pass
            elif cond == "has_burst1_ally":
                # 자신 제외 팀에 1버스트 캐릭터가 있어야 함
                burst_stages = self.state.get("burst_stages", {})
                has = any(burst_stages.get(n) == "1" for n in self.team_names if n != caster)
                if not has:
                    return False
            elif cond == "no_burst1_ally":
                # 자신 제외 팀에 1버스트 캐릭터가 없어야 함
                burst_stages = self.state.get("burst_stages", {})
                has = any(burst_stages.get(n) == "1" for n in self.team_names if n != caster)
                if has:
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
            elif cond.startswith("self_state:"):
                state_name = cond[len("self_state:"):]
                has_state = any(
                    ab.effect.get("name") == state_name and caster in (ab.target_chars or [])
                    for ab in self._active
                )
                if not has_state:
                    return False
            elif cond.startswith("not_self_state:"):
                state_name = cond[len("not_self_state:"):]
                has_state = any(
                    ab.effect.get("name") == state_name and caster in (ab.target_chars or [])
                    for ab in self._active
                )
                if has_state:
                    return False
            elif cond.startswith("target_state:"):
                state_name = cond[len("target_state:"):]
                # 단일 적 가정: "__enemy__"가 target_chars에 있는 활성 효과를 확인
                has_state = any(
                    ab.effect.get("name") == state_name and "__enemy__" in (ab.target_chars or [])
                    for ab in self._active
                )
                if not has_state:
                    return False
            elif cond.startswith("self_stack_above:"):
                parts = cond.split(":")
                stack_name, threshold = parts[1], int(parts[2])
                current = next(
                    (ab.stack for ab in self._active
                     if ab.effect.get("name") == stack_name
                     and ab.caster == caster
                     and (caster in (ab.target_chars or []) or "__enemy__" in (ab.target_chars or []))),
                    0,
                )
                if current < threshold:
                    return False
            elif cond == "core_hit":
                if not self.state.get("enemy", {}).get("has_core", False):
                    return False
            # 나머지 condition은 get_buffs에서 재평가
        return True

    def effective_max_hp(self, name: str) -> float:
        """현재 활성 max_hp_pct / max_hp_only_pct 버프를 반영한 최대 체력 절대값.
        get_buffs() 재귀 없이 _active를 직접 순회한다."""
        base_hp = self.state.get("base_stats", {}).get(name, {}).get("hp", 0.0)
        bonus_pct = 0.0
        for ab in self._active:
            stat = ab.effect.get("stat", "")
            if stat not in ("max_hp_pct", "max_hp_only_pct"):
                continue
            if name not in (ab.target_chars or []):
                continue
            val = self._get_value(ab.effect, ab, name)
            if val is not None:
                bonus_pct += val
        return base_hp * (1.0 + bonus_pct / 100.0)

    def sync_hp(self, name: str):
        """state['hp']를 기준으로 state['hp_pct']를 재계산한다."""
        hp = self.state.get("hp", {}).get(name)
        if hp is None:
            return
        max_hp = self.effective_max_hp(name)
        if max_hp <= 0:
            return
        self.state["hp_pct"][name] = hp / max_hp * 100.0

    def _has_immune(self, char_name: str, immune_stat: str) -> bool:
        """char_name이 현재 immune_stat 버프를 가지고 있는지 확인."""
        buff_key = _STAT_TO_BUFF.get(immune_stat, immune_stat)
        for ab in self._active:
            if ab.effect.get("stat") != immune_stat:
                continue
            if char_name in (ab.target_chars or []):
                return True
        return False

    def _activate(self, eff: dict, caster: str, t: float):
        """효과를 ActiveBuff로 변환해 활성 목록에 추가하거나 갱신."""
        # max_trigger: 전투 중 최대 발동 횟수 제한
        max_trigger = eff.get("max_trigger")
        if max_trigger is not None:
            eid = id(eff)
            if self._trigger_counts.get(eid, 0) >= max_trigger:
                return
            self._trigger_counts[eid] = self._trigger_counts.get(eid, 0) + 1

        if eff.get("type") == "instant":
            self._dispatch_instant(eff, caster, t)
            return

        if eff.get("type") == "damage":
            tick_interval = eff.get("tick_interval")
            if tick_interval and self._damage_handler:
                # tick_interval이 있으면 DoT 타이머 등록 (이미 활성이면 갱신)
                duration = eff.get("duration")
                expires = math.inf if duration is None or duration == -1 else t + duration
                self._dot_timers[id(eff)] = (caster, t + tick_interval, expires)
                # DoT는 _active에도 등록해야 target_state/debuff_cleanse/remove_named_buff
                # 등이 name·polarity 기준으로 조회할 수 있다.
                raw_target = eff.get("target", "self")
                lazy = isinstance(raw_target, str) and raw_target.startswith(_LAZY_RESOLVE_PREFIXES)
                targets = None if lazy else self._resolve_target(raw_target, caster)
                max_stack = eff.get("max_stack", 1)
                existing = next(
                    (ab for ab in self._active if ab.effect is eff and ab.caster == caster), None
                )
                # scaling_ref가 있는 DoT는 등록 시점 참조 스택/게이지 값을 초기 stack으로 캡처
                # (틱 발동 시 참조 버프가 이미 제거됐을 수 있으므로)
                scaling_ref = eff.get("scaling_ref", "")
                if scaling_ref and eff.get("scaling") == "stack_count":
                    ref_val = int(self.state.get("gauges", {}).get(caster, {}).get(scaling_ref, 0) or 0)
                    if not ref_val:
                        for _ab in self._active:
                            if _ab.caster == caster and _ab.effect.get("name") == scaling_ref:
                                ref_val = _ab.stack
                                break
                    init_stack = ref_val if ref_val else 1
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
                    ))
                    if self._buff_event_handler and eff.get("name") and targets:
                        for tgt in targets:
                            self._buff_event_handler("activate", eff["name"], caster, tgt, t, expires, None, eff.get("stat"))
            elif self._damage_handler:
                # tick_interval 없으면 즉시 1회 발동
                self._damage_handler(eff, caster, t)
            return

        if eff.get("type") == "weapon_change":
            # 발사 루프 교체는 타임라인이 처리하지만, 활성 여부는 state에 기록
            duration = eff.get("duration")
            expires = math.inf if duration is None or duration == -1 else t + duration
            wc = self.state.setdefault("weapon_change", {})
            wc[caster] = {
                "effect": eff,
                "activated_at": t,
                "expires_at": expires,
            }
            return

        duration = eff.get("duration")
        if duration is None and "duration_values" in eff:
            char = self._char.get(caster, {})
            skill_lv = str(char.get("skill_level", 10))
            dv = eff["duration_values"]
            duration = float(dv.get(skill_lv, dv.get("10", 0.0)))
        expires = math.inf if duration is None or duration == -1 else t + duration

        raw_target = eff.get("target", "self")
        lazy = isinstance(raw_target, str) and raw_target.startswith(_LAZY_RESOLVE_PREFIXES)
        targets = None if lazy else self._resolve_target(raw_target, caster)

        # harmful 효과: debuff_immune 또는 named debuff immunity인 대상 제거
        if eff.get("polarity") == "harmful" and targets is not None:
            eff_name = eff.get("name", "")
            named_immune = f"debuff_immune:{eff_name}" if eff_name else None
            targets = [
                c for c in targets
                if not self._has_immune(c, "debuff_immune")
                and (named_immune is None or not self._has_immune(c, named_immune))
            ]
            if not targets:
                return

        max_stack = eff.get("max_stack", 1)

        # 동일 효과(name + caster + target) 기존 버프 탐색
        # target이 동적(lazy 아님)이면 target_chars까지 일치해야 같은 버프로 간주
        name = eff.get("name", "")
        existing = None
        for ab in self._active:
            if ab.effect is eff and ab.caster == caster:
                if lazy or ab.target_chars == targets:
                    existing = ab
                    break

        duration_bullets = eff.get("duration_bullets", -1)

        if existing:
            if max_stack == 1:
                existing.activated_at = t
                existing.expires_at = expires
                existing.trigger_count += 1
                if duration_bullets != -1:
                    existing.bullets_left = duration_bullets
            else:
                cap = max_stack if max_stack != -1 else existing.stack + 1
                prev_stack = existing.stack
                existing.stack = min(existing.stack + 1, cap)
                existing.activated_at = t
                existing.expires_at = expires
                existing.trigger_count += 1
                if duration_bullets != -1:
                    existing.bullets_left = duration_bullets
                # 스택이 새 값에 도달했으면 stack_reach 이벤트 발생
                if existing.stack != prev_stack and name:
                    self.notify(f"stack_reach:{name}:{existing.stack}", t, caster)
                # 스택 갱신 시에도 event:{name} notify (의존 버프 갱신용)
                if name:
                    self.notify(f"event:{name}", t, caster)
            # 갱신 이벤트: 만료 시각이 바뀌었으므로 activate로 재기록
            if self._buff_event_handler and name:
                _val = self._get_value(eff, existing, caster)
                _stat = eff.get("stat")
                for tgt in (existing.target_chars or []):
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
                bullets_left=duration_bullets,
            ))
            name = eff.get("name", "")
            if name:
                self.notify(f"event:{name}", t, caster)
                # 스택 1로 처음 등록 시도 stack_reach:버프명:1
                self.notify(f"stack_reach:{name}:1", t, caster)
            # 신규 등록 이벤트
            if self._buff_event_handler and name and targets:
                ab_new = next((ab for ab in self._active if ab.effect is eff and ab.caster == caster), None)
                _val = self._get_value(eff, ab_new, caster) if ab_new else None
                _stat = eff.get("stat")
                for tgt in targets:
                    self._buff_event_handler("activate", name, caster, tgt, t, expires, _val, _stat)

        # event:stat_applied:XXX — stat 유형별 버프 적용 시 해당 target_chars에게 notify
        stat = eff.get("stat", "")
        _STAT_APPLIED_EVENTS = {"dot_dmg_pct", "split_dmg_pct"}
        if stat in _STAT_APPLIED_EVENTS and targets:
            event_name = f"event:stat_applied:{stat}"
            for tgt in targets:
                if tgt != "__enemy__":
                    self.notify(event_name, t, tgt)

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
                    for tgt in (ab.target_chars or []):
                        self._buff_event_handler("expire", name, ab.caster, tgt, t, t)

        # weapon_change 만료 정리
        wc = self.state.get("weapon_change", {})
        expired = [name for name, info in wc.items() if t >= info["expires_at"]]
        for name in expired:
            del wc[name]

        # every:Ns 처리
        for eff, caster in self._effects:
            for timing in eff["trigger"]["timing"]:
                if not timing.startswith("every:"):
                    continue
                eid = id(eff)
                base_interval = float(timing[6:-1])  # "every:20s" → 20.0
                # skill_cooldown_pct 버프 반영: 음수 = 감소 (예: -75% → interval × 0.25)
                cool_pct = sum(
                    (self._get_value(ab.effect, ab, caster) or 0.0)
                    for ab in self._active
                    if ab.effect.get("stat") == "skill_cooldown_pct"
                    and (ab.target_chars is None or caster in (ab.target_chars or []))
                )
                interval = base_interval * max(0.0, 1.0 + cool_pct / 100.0)
                interval = max(interval, base_interval * 0.05)  # 최소 5% cap
                if eid not in self._next_fire:
                    # 전투 시작 후 interval초 후 첫 발동
                    self._next_fire[eid] = interval
                if t >= self._next_fire[eid]:
                    if self._condition_ok(eff["trigger"].get("condition", []), caster, t, eff):
                        self._activate(eff, caster, t)
                    self._next_fire[eid] += interval

        # tick_interval damage (DoT) 처리
        if self._damage_handler:
            # id → eff 역참조 맵 (필요한 경우에만 구성)
            eff_by_id = {id(eff): eff for eff, _ in self._effects}
            expired_dots = []
            for eid, (caster, next_t, expires_at) in self._dot_timers.items():
                if t >= expires_at:
                    expired_dots.append(eid)
                    continue
                if t >= next_t:
                    eff = eff_by_id.get(eid)
                    if eff is None:
                        expired_dots.append(eid)
                        continue
                    # DoT는 _activate 시점에 조건 통과 후 등록된 것이므로
                    # 틱마다 재검사 없이 무조건 발동한다.
                    self._damage_handler(eff, caster, t)
                    interval = eff.get("tick_interval", 1.0)
                    self._dot_timers[eid] = (caster, next_t + interval, expires_at)
            for eid in expired_dots:
                del self._dot_timers[eid]

    # ── 버프 집계 ─────────────────────────────────────────────────────────

    def _invalidate_buffs_cache(self):
        self._cache_version += 1
        self._buffs_cache.clear()

    def get_buffs(self, caster: str, target: str, t: float) -> dict:
        """
        현재 시각 t에서 caster가 target을 공격할 때 적용되는 buffs 딕셔너리 반환.

        caster에게 적용된 버프(공격력 등)와
        target에게 적용된 버프(received_dmg 등)를 모두 포함.
        """
        cache_key = (caster, t, self._cache_version)
        cached = self._buffs_cache.get(cache_key)
        if cached is not None:
            return cached

        buffs = dict(_BUFFS_ZERO)
        crit_rate_parts: list[float] = [0.15]  # 기본 크리확률 15%

        for ab in self._active:
            if t >= ab.expires_at:
                continue

            eff = ab.effect
            stat = eff.get("stat", "")
            buff_key = _STAT_TO_BUFF.get(stat)
            if not buff_key:
                continue

            # condition 재평가 (get_buffs 호출 시마다)
            conditions = eff["trigger"].get("condition", [])
            if not self._runtime_condition_ok(conditions, ab.caster, caster, target, t):
                continue

            # 지연 resolve: get_buffs 시점에 스탯 비교 기반 타겟 결정
            target_chars = (
                self._resolve_target(ab.effect.get("target", "self"), ab.caster)
                if ab.target_chars is None
                else ab.target_chars
            )

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

            # boolean 플래그 스탯: 수치 없이 True만 세팅
            if buff_key in ("charge_time_fixed", "charge_speed_buff_immune", "charge_speed_debuff_immune",
                            "debuff_immune", "stun_immune", "stack_change_immune", "taunt",
                            "pierce_enabled"):
                buffs[buff_key] = True
                continue

            val = self._get_value(eff, ab, actual_recipient)
            if val is None:
                continue

            if stat in _CRIT_RATE_STATS:
                crit_rate_parts.append(val / 100)
            else:
                buffs[buff_key] = buffs.get(buff_key, 0.0) + val

        # 크리확률 독립 합성: 1 - ∏(1 - p_i)
        buffs["crit_rate"] = 1.0 - math.prod(1.0 - p for p in crit_rate_parts)

        # atk_from_hp_pct: 최종 최대 HP × (val/100) → atk_flat에 합산
        for ab in self._active:
            if t >= ab.expires_at:
                continue
            if ab.effect.get("stat") != "atk_from_hp_pct":
                continue
            target_chars = (
                self._resolve_target(ab.effect.get("target", "self"), ab.caster)
                if ab.target_chars is None
                else ab.target_chars
            )
            if caster not in target_chars:
                continue
            conditions = ab.effect["trigger"].get("condition", [])
            if not self._runtime_condition_ok(conditions, ab.caster, caster, target, t):
                continue
            val = self._get_value(ab.effect, ab, caster)
            if val is None:
                continue
            final_hp = self.effective_max_hp(caster)
            buffs["atk_flat"] = buffs.get("atk_flat", 0.0) + final_hp * (val / 100.0)

        # atk_caster_based_pct: 시전자 공격력 × (val/100) → 수령자 atk_flat에 합산
        for ab in self._active:
            if t >= ab.expires_at:
                continue
            if ab.effect.get("stat") != "atk_caster_based_pct":
                continue
            target_chars = (
                self._resolve_target(ab.effect.get("target", "self"), ab.caster)
                if ab.target_chars is None
                else ab.target_chars
            )
            if caster not in target_chars:
                continue
            conditions = ab.effect["trigger"].get("condition", [])
            if not self._runtime_condition_ok(conditions, ab.caster, caster, target, t):
                continue
            val = self._get_value(ab.effect, ab, ab.caster)
            if val is None:
                continue
            caster_atk = self.state.get("base_stats", {}).get(ab.caster, {}).get("atk", 0.0)
            buffs["atk_flat"] = buffs.get("atk_flat", 0.0) + caster_atk * (val / 100.0)

        # charge_time_fixed가 있으면 차지속도 관련 버프/디버프 모두 0
        if buffs["charge_time_fixed"]:
            buffs["charge_speed_pct"] = 0.0
        else:
            # 면역 후처리: 양수(증가) 또는 음수(감소) 성분만 제거
            if buffs["charge_speed_buff_immune"] and buffs["charge_speed_pct"] > 0:
                buffs["charge_speed_pct"] = 0.0
            if buffs["charge_speed_debuff_immune"] and buffs["charge_speed_pct"] < 0:
                buffs["charge_speed_pct"] = 0.0

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
                    (ab.stack for ab in self._active
                     if ab.effect.get("name") == stack_name
                     and ab.caster == buff_caster
                     and (buff_caster in (ab.target_chars or []) or "__enemy__" in (ab.target_chars or []))),
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
            elif cond.startswith("gauge_eq:"):
                parts = cond.split(":")
                gauge_id, threshold = parts[1], float(parts[2])
                current = self.state.get("gauges", {}).get(buff_caster, {}).get(gauge_id, 0.0)
                if current != threshold:
                    return False
            elif cond.startswith("self_state:"):
                state_name = cond[len("self_state:"):]
                has_state = any(
                    ab.effect.get("name") == state_name and buff_caster in (ab.target_chars or [])
                    for ab in self._active
                )
                if not has_state:
                    return False
            elif cond.startswith("not_self_state:"):
                state_name = cond[len("not_self_state:"):]
                has_state = any(
                    ab.effect.get("name") == state_name and buff_caster in (ab.target_chars or [])
                    for ab in self._active
                )
                if has_state:
                    return False
            elif cond.startswith("target_state:"):
                state_name = cond[len("target_state:"):]
                has_state = any(
                    ab.effect.get("name") == state_name and "__enemy__" in (ab.target_chars or [])
                    for ab in self._active
                )
                if not has_state:
                    return False
            # prob:N은 notify 시점에만 평가 (get_buffs에서 재판정하지 않음)
        return True

    def _get_value(self, eff: dict, ab: ActiveBuff, query_caster: str | None = None) -> float | None:
        """효과 항목에서 현재 스킬 레벨 + 스택 기준 수치 반환. %값 그대로 반환."""
        if "fixed_value" in eff:
            base = float(eff["fixed_value"])
        elif "values" in eff:
            char = self._char.get(ab.caster, {})
            skill_lv = str(char.get("skill_level", 10))
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

        # 스택 합산
        if scaling == "stack_count":
            ref = eff.get("scaling_ref")
            if ref:
                # scaling_ref가 있으면 해당 이름의 다른 버프 스택 수를 참조
                stack = 0
                for other in self._active:
                    if other.caster == ab.caster and other.effect.get("name") == ref:
                        stack = other.stack
                        break
                base *= stack
            else:
                base *= ab.stack
            return base

        return base * ab.stack if eff.get("max_stack", 1) != 1 else base

    # ── 타겟 resolve ──────────────────────────────────────────────────────

    def _resolve_target(self, target: Any, caster: str) -> list[str]:
        """target 문자열 → 캐릭터명 목록."""
        if isinstance(target, list):
            result = []
            for t in target:
                result.extend(self._resolve_target(t, caster))
            return result

        if target == "self":
            return [caster]
        # 캐릭터 이름 직접 지정 (예: "이사벨" — 아르카나 마법사 카드 예외)
        if target in self.team_names:
            return [target]
        if target == "all_allies":
            return list(self.team_names)
        if target == "all_allies_burst_casted":
            return [n for n in self.team_names if self.state.get("burst_casted", {}).get(n)]
        if target == "all_allies_burst_not_casted":
            return [n for n in self.team_names if not self.state.get("burst_casted", {}).get(n)]
        if target == "all_allies_excl_self":
            return [n for n in self.team_names if n != caster]
        if target in ("enemy", "all_enemies", "target", "target_body", "same_target",
                      "enemies_in_range", "enemies_nearest_in_range"):
            # 적 대상: "__enemy__" 센티널 사용 (타임라인이 판단)
            return ["__enemy__"]

        if target.startswith("allies_lowest_atk_burst3:"):
            n = int(target.split(":")[1])
            burst3 = [name for name in self.team_names
                      if _NIKKE.get(name, {}).get("burst_stage") == "3"]
            burst3.sort(key=self._effective_atk)
            return burst3[:n]

        if target.startswith("allies:"):
            n = int(target.split(":")[1])
            return self.team_names[:n]
        if target.startswith("allies_top_atk:"):
            n = int(target.split(":")[1])
            return self._top_by("atk", n)
        if target.startswith("allies_top_atk_excl:"):
            n = int(target.split(":")[1])
            return self._top_by("atk", n, exclude=caster)
        if target.startswith("allies_lowest_hp:"):
            n = int(target.split(":")[1])
            return self._lowest_hp(n)
        if target.startswith("allies_top_def:"):
            n = int(target.split(":")[1])
            return self._top_by("def", n)
        if target.startswith("allies_random:"):
            n = int(target.split(":")[1])
            pool = [x for x in self.team_names if x != caster]
            return random.sample(pool, min(n, len(pool)))
        if target.startswith("allies_adjacent:"):
            n = int(target.split(":")[1])
            idx = self.team_names.index(caster)
            adj = []
            if idx > 0:
                adj.append(self.team_names[idx - 1])
            if idx < len(self.team_names) - 1:
                adj.append(self.team_names[idx + 1])
            return [caster] + adj[:n]
        if target.startswith("allies_weapon_excl_self:"):
            wtype = target.split(":")[1]
            return [n for n in self.team_names
                    if _NIKKE[n]["weapon_type"] == wtype and n != caster]
        if target.startswith("allies_weapon:"):
            wtype = target.split(":")[1]
            return [n for n in self.team_names
                    if _NIKKE[n]["weapon_type"] == wtype]
        if target.startswith("allies_class:"):
            cls = target.split(":")[1]
            return [n for n in self.team_names if _NIKKE[n]["class"] == cls]
        if target.startswith("allies_code:"):
            code = target.split(":")[1]
            return [n for n in self.team_names if _NIKKE[n].get("element_code") == code]
        if target.startswith("allies_below_def"):
            caster_def = self.state.get("base_stats", {}).get(caster, {}).get("def", 0)
            return [n for n in self.team_names
                    if self.state.get("base_stats", {}).get(n, {}).get("def", 0) < caster_def]
        if target == "allies_burst3":
            burst_stages = self.state.get("burst_stages", {})
            return [n for n in self.team_names if burst_stages.get(n) == "3"]

        # 적 관련 (타임라인 처리)
        if target.startswith("enemies") or target in ("target", "target_body", "same_target"):
            return ["__enemy__"]

        # 커버, 발사체 등
        return []

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

    def _top_by(self, stat: str, n: int, exclude: str | None = None) -> list[str]:
        pool = [name for name in self.team_names if name != exclude]
        if stat == "atk":
            pool.sort(key=self._effective_atk, reverse=True)
        else:
            base_stats = self.state.get("base_stats", {})
            pool.sort(key=lambda x: base_stats.get(x, {}).get(stat, 0), reverse=True)
        return pool[:n]

    def _lowest_hp(self, n: int) -> list[str]:
        hp_pct = self.state.get("hp_pct", {})
        # 동률이면 team_names 순서(앞쪽 우선)로 결정
        pool = sorted(self.team_names,
                      key=lambda x: (hp_pct.get(x, 100.0), self.team_names.index(x)))
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

    def end_weapon_change(self, caster: str):
        """
        duration_bullets 소진 등 타임라인이 무기 변경을 강제 종료할 때 호출.
        """
        self.state.get("weapon_change", {}).pop(caster, None)

    def consume_bullet_buffs(self, caster: str, t: float = 0.0):
        """발사 1회 소모 시 duration_bullets 기반 버프 카운트를 차감하고 소진된 버프를 제거."""
        to_remove = []
        for ab in self._active:
            if ab.caster != caster or ab.bullets_left == -1:
                continue
            # 이번 발사 중 막 활성화된 버프는 소모하지 않음 (첫 발사도 효과에 포함)
            if ab.activated_at == t:
                continue
            ab.bullets_left -= 1
            if ab.bullets_left <= 0:
                to_remove.append(id(ab))
        removed_ids = set(to_remove)
        removed_buffs = [ab for ab in self._active if id(ab) in removed_ids]
        if removed_ids:
            self._invalidate_buffs_cache()
        self._active = [ab for ab in self._active if id(ab) not in removed_ids]
        for ab in removed_buffs:
            name = ab.effect.get("name", "")
            if name:
                self.notify(f"event:state_end:{name}", t, ab.caster)
                if self._buff_event_handler:
                    for tgt in (ab.target_chars or []):
                        self._buff_event_handler("expire", name, ab.caster, tgt, t, t)

    def battle_start(self, t: float = 0.0):
        """전투 시작 시 모든 캐릭터에 대해 battle_start 이벤트 발생."""
        for name in self.team_names:
            self.notify("battle_start", t, name)
        # 단일 보스 가정: 전투 시작 시 적 등장 이벤트 발생
        for name in self.team_names:
            self.notify("event:enemy_spawn", t, name)

    def reset(self):
        """전투 초기화."""
        self._active.clear()
        self._next_fire.clear()
        self._dot_timers.clear()
        self._event_counts.clear()
        self._trigger_counts.clear()
        self._buffs_cache.clear()
        self._cache_version = 0

        self.state.pop("weapon_change", None)


# ── 간단 테스트 ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    team = [
        {
            "name": "크라운",
            "level": 200, "breakthrough": 3, "core_enhancement": 7,
            "affinity": 30, "skill_level": 10,
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

    bm = BuffManager(team, state)
    bm.battle_start(0.0)
    bm.tick(0.0)

    buffs = bm.get_buffs("크라운", "크라운", 0.0)
    print("크라운 self 버프:")
    for k, v in buffs.items():
        if v:
            print(f"  {k}: {v}")
