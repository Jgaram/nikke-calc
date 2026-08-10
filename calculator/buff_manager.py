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
"""

from __future__ import annotations

import itertools
import json
import math
import os
import random
from dataclasses import dataclass, field
from typing import Any

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
_EQUIP_SKILLS = _load(os.path.join(_TABLE_DIR, "equipment_skills.json"))
_CUBE = _load(os.path.join(_TABLE_DIR, "cube.json"))
_COLLECTION = _load(os.path.join(_TABLE_DIR, "collection.json"))

# ── 빈 buffs 딕셔너리 템플릿 ──────────────────────────────────────────────

_BUFFS_ZERO: dict[str, Any] = {
    "atk_pct":          0.0,
    "atk_flat":         0.0,
    "def_ignore_pct":   0.0,
    "crit_rate":        0.0,   # 아래 _CRIT_RATE_STATS 경로에서 별도 합산
    "crit_dmg":         0.0,
    "core_dmg_pct":     0.0,
    "atk_dmg_pct":                  0.0,
    "burst_dmg_pct":                0.0,
    "burst_dmg_aoe_pct":            0.0,   # 대상이 '적 전체'인 버스트 대미지에만 가산
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
    "enemy_def_down_pct": 0.0,  # 적 방어력 감소(②). 적 대상 def_pct 버프 합(음수)
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
    "armor_break_enabled": False,  # 일반 공격을 방어력 무시 대미지로 치환
    "attack_speed_pct": 0.0,
    "pellet_count":     0.0,
    "pellet_count_fixed": 0.0,  # >0이면 펠릿 수를 이 값으로 고정 (절대값)
    "fullburst_duration": 0.0,  # 풀버스트 타임 지속 시간 증감 (초)
    "skill_cooldown_pct": 0.0,  # 스킬 쿨타임 % 감소 (음수 = 감소)
    "charge_speed_overflow_conversion_pct": 0.0,  # charge_speed 100% 초과분 × N% → charge_dmg_pct 추가
    "mg_warmup_speed_pct": 0.0,  # MG 예열 진행 속도 % (음수 = 감소). -100이면 warmup_shots 증가 정지
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
    "element_bonus":        "element_bonus_pct",  # 장비·큐브에서 사용하는 stat명 (동일 버프 키로 합산)
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
    "armor_break_enabled":  "armor_break_enabled",
    "attack_speed_pct":     "attack_speed_pct",
    "pellet_count":         "pellet_count",
    "pellet_count_fixed":   "pellet_count_fixed",
    "fullburst_duration":   "fullburst_duration",
    "skill_cooldown_pct":   "skill_cooldown_pct",
    "charge_speed_overflow_conversion_pct": "charge_speed_overflow_conversion_pct",
    "mg_warmup_speed_pct": "mg_warmup_speed_pct",
}

# 크리확률로 합산되는 stat 집합 (백분율 → 확률 환산 후 기본 15%와 합연산)
_CRIT_RATE_STATS = {"crit_rate", "normal_atk_crit_rate"}

# 대상별 보호막을 만드는 stat 집합. `shared_shield_from_max_hp_pct`(아군 공용 보호막)는
# 부여 대상이 시전자 1인이라는 점만 다르고 보호막 판정(during_shield ·
# event:shield_applied)은 동일하게 성립한다 — 대상 수는 target 값이 결정한다. (블랑)
_SHIELD_STATS = frozenset(["shield_from_max_hp_pct", "shared_shield_from_max_hp_pct"])

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
    # 적 수 조건은 단일 보스 sim에서 상수 판정이지만 여기 등록해야 한다.
    # passive 버프는 조건 미충족이어도 _activate()로 등록되고(suppress_event만 다름)
    # 이후 게이팅을 전적으로 이 목록에 의존한다 — 빠지면 "적 N기 이상" 버프가
    # 보스전에서 그대로 적용된다 (맥스웰 `일렉트릭 샷` 크리 확률·크리 대미지).
    "enemy_count_above:", "enemy_count_below:",
])


def _has_runtime_cond(conditions: list, expires: float) -> bool:
    """
    이 버프가 get_buffs 시점마다 조건을 재평가해야 하는지.

    스킬 텍스트 문법상 조건은 **발동 시점 게이트**이고 `[N초 유지]`는 버프 자체의
    지속시간이다 (예: "■ ... 시 소드 코인 상태라면 ... [10초 유지]" → 발동 순간
    소드 코인이면 그때부터 10초. 도중에 소드 코인이 풀려도 10초는 끝까지 간다).
    따라서 유한 duration 버프는 재평가 대상이 아니다.

    재평가는 duration -1 / null (지속·영구) 버프에만 적용한다. 그쪽은 만료 시각이
    없으므로 조건이 곧 유효 구간이다 (조건부 passive와 같은 기준 — tick()의
    `ab.expires_at < math.inf: continue` 참고).
    """
    if expires != math.inf:
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
# 소모만 당해 버프가 통째로 사라진다. `full_charge_hit`은 명중 **후**(timeline `_charge_fire`가
# calc_damage → notify("full_charge_hit") → consume_bullet_buffs 순)라 여기 넣으면 안 된다 —
# 짝인 `full_charge`(차지 완료 = 발사 전)는 맞다. (아인 `페더 샷` — 넣어 두었을 때
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

        # 활성 버프 목록
        self._active: list[ActiveBuff] = []

        # every:Ns 효과별 다음 발동 시각
        # id(effect) → (next_t, interval). interval을 같이 들고 있어야 쿨감이
        # 도중에 바뀐 걸 감지해 진행 중인 쿨타임의 잔여분을 재조정할 수 있다.
        self._next_fire: dict[int, tuple[float, float]] = {}

        # tick_interval damage 효과별 타이머: id(effect) → (caster, next_t, expires_at)
        self._dot_timers: dict[int, tuple[str, float, float]] = {}

        # tick_interval instant 효과별 타이머: id(effect) → (caster, next_t, expires_at)
        self._instant_timers: dict[int, tuple[str, float, float]] = {}
        # charge_hold:N 임계값 캐시 (캐스터별). `charge_hold_thresholds()` 참조
        self._charge_hold_cache: dict[str, list[tuple[float, str]]] = {}

        # 지연 resolve 대상 캐시: (caster, 활성화 시각, target 문자열) → 대상 목록.
        # 같은 시전자가 같은 시각에 같은 target으로 건 효과들이 대상을 공유한다.
        # `_resolve_lazy()` 참조 (블랑 `쇼타임` 불굴 ↔ 최대 체력)
        self._lazy_target_cache: dict[tuple[str, float, str], list[str]] = {}

        # 이벤트별 발동 횟수 (hit_count, burst_cast_count 등 추적용)
        self._event_counts: dict[str, dict[str, int]] = {}  # caster → {event_key: count}

        # max_trigger 추적: id(effect) → 발동 횟수 (buff/instant/damage/weapon_change 공통)
        self._trigger_counts: dict[int, int] = {}

        # 현재 시각. sync_hp()처럼 t를 받지 않는 지점에서 이벤트를 쏘기 위해 보관
        self._cur_t: float = 0.0
        # sync_hp → notify → _activate → sync_hp 재진입 방지
        self._in_hp_edge: bool = False

        # instant stat → 핸들러. 타임라인이 register_instant_handler()로 주입
        self._instant_handlers: dict[str, Any] = {}

        # instant 이벤트 로그 콜백. 타임라인이 register_instant_event_handler()로 주입
        self._instant_event_handler: Any = None

        # damage 효과 핸들러. 타임라인이 register_damage_handler()로 주입
        self._damage_handler: Any = None

        # 버프 활성/만료 이벤트 콜백. 타임라인이 register_buff_event_handler()로 주입
        # handler(kind, name, caster, target, t, expires_at)
        self._buff_event_handler: Any = None

        # get_buffs 캐시: (caster, t, _cache_version) → buffs dict
        self._buffs_cache: dict = {}
        self._cache_version: int = 0

        # is_stunned 캐시: char_name → bool (_invalidate_buffs_cache 시 함께 초기화)
        self._stunned_cache: dict = {}

        # notify 인덱스: event_key → [(eff, caster), ...]
        # caster별: _notify_index[caster][event_key] = [(eff, caster), ...]
        # squad_ammo_consume 전용: _squad_notify_index[event_key] = [(eff, caster), ...]
        # part_hit_count / body_hit_count 전용: _squad_hit_index[event_key] = [(eff, caster), ...]
        self._notify_index: dict[str, dict[str, list]] = {}
        self._squad_notify_index: dict[str, list] = {}
        self._squad_hit_index: dict[str, list] = {}

        # 조건부 passive 버프의 이전 틱 조건 충족 여부: id(ActiveBuff) → bool
        # tick()에서 False→True / True→False 전환 감지해 buff_event_handler 발생
        self._cond_passive_prev: dict[int, bool] = {}

        self._register_all()

    # ── 등록 ─────────────────────────────────────────────────────────────

    def _register_all(self):
        """스쿼드 전원의 모든 버프 소스를 효과 목록에 등록."""
        for char in self.squad:
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
        if timing.startswith("full_charge_count:"):
            return "full_charge_hit"
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
        # 나머지는 timing 자체가 event 키
        return timing

    def _build_notify_index(self):
        """_effects 로부터 notify 인덱스를 구축."""
        self._notify_index.clear()
        self._squad_notify_index.clear()
        self._squad_hit_index.clear()
        valid_types = ("buff", "instant", "weapon_change", "damage")

        for eff, eff_caster in self._effects:
            if eff.get("type") not in valid_types:
                continue
            for timing in eff["trigger"]["timing"]:
                key = self._timing_to_index_key(timing)
                if key is None:
                    continue
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
        }

    def _make_cube_effects(self, cube_name: str, cube_lv: int) -> list[dict]:
        """큐브 효과 목록.

        `공통`(우월 코드 대미지)은 소장품의 `공통`과 마찬가지로 **어떤 큐브를 끼든
        항상 붙는다**. 선택한 종류의 효과는 그 위에 추가된다 — 재장전 큐브를 끼면
        재장전 효과가 더해지는 것이지 우월 코드가 빠지는 게 아니다.
        """
        names = ["공통"]
        if cube_name != "공통":
            names.append(cube_name)

        effects = []
        for nm in names:
            entry = _CUBE.get(nm)
            if not entry or nm.startswith("_"):
                continue
            vals = entry.get("values", {}).get(str(cube_lv))
            if not vals:
                continue
            effects.append({
                "type": "buff",
                "name": f"큐브:{nm}",
                "trigger": {"timing": ["battle_start"], "condition": []},
                "target": "self",
                "stat": entry["stat"],
                "polarity": "beneficial",
                "fixed_value": float(vals[0]),
                "duration": None,
                "_source_tag": "cube",
            })
        return effects

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

    def register_instant_event_handler(self, handler):
        """타임라인이 instant 발동 로그 콜백을 등록한다.
        handler(name, caster, target, t, stat, value) 시그니처.
        """
        self._instant_event_handler = handler

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
                        has_runtime_conditions=_has_runtime_cond(target_eff["trigger"].get("condition", []), expires),
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
        """
        self._cur_t = t
        # squad_ammo_consume: 스쿼드 전체 탄환 소비 카운터 — caster와 무관하게 합산, 모든 스쿼드원 효과 순회
        if event == "squad_ammo_consume":
            team_counts = self._event_counts.setdefault("__squad__", {})
            team_counts[event] = team_counts.get(event, 0) + 1
            current_count = team_counts[event]
            for eff, eff_caster in self._squad_notify_index.get(event, []):
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
                    if is_passive:
                        conditions = eff["trigger"].get("condition", [])
                        cond_met = not conditions or self._condition_ok(conditions, caster, t, eff)
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
        for eff, eff_caster in self._squad_hit_index.get(event, []):
            for timing in eff["trigger"]["timing"]:
                if self._timing_match(timing, event, current_count, t, eff, eff_caster):
                    if self._condition_ok(eff["trigger"].get("condition", []), eff_caster, t, eff):
                        self._activate(eff, attacker, t)
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

        # full_charge_count:N  (trigger_count_reduce 버프로 N 감소 가능)
        if timing.startswith("full_charge_count:") and event == "full_charge_hit":
            raw = timing.split(":")[1]
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

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
            raw = timing.split(":")[1]
            if raw.startswith("{") and raw.endswith("}"):
                tv = eff.get("trigger_values", {})
                if tv:
                    char = self._char.get(caster, {})
                    skill_lv = _get_skill_lv(char, eff)
                    raw = str(tv.get(skill_lv, tv.get("10", raw)))
            if not raw.lstrip("-").isdigit(): return False
            n = int(raw)
            n = self._apply_trigger_count_reduce(n, eff, caster, t)
            return count % n == 0

        # burst_enter:N
        if timing.startswith("burst_enter:") and event.startswith("burst_enter:"):
            return timing == event

        # squad_burst_cast:N
        if timing.startswith("squad_burst_cast:") and event.startswith("squad_burst_cast:"):
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
            elif cond == "during_shield":
                if not self.has_shield(caster):
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
                # 단일 보스 sim: 적 1기. "랩쳐 N기 이하" → 1 <= N (N>=1이면 항상 참)
                n = int(cond.split(":")[1])
                if 1 > n:
                    return False
            elif cond.startswith("enemy_count_above:"):
                # 단일 보스 sim: 적 1기. "랩쳐 N기 이상" → 1 >= N (N>=2이면 항상 거짓 → 무발동)
                n = int(cond.split(":")[1])
                if 1 < n:
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
            # 나머지 condition은 get_buffs에서 재평가
        return True

    def _has_self_state(self, caster: str, state_name: str) -> bool:
        """self_state:/not_self_state: 판정의 단일 창구.

        상태는 두 곳에 있을 수 있다 — 일반 버프(_active)와 무기 변경 모드(state["weapon_change"]).
        weapon_change는 _active에 등록되지 않으므로 여기서 같이 봐야
        `self_state:저격 모드`처럼 모드 자체를 가리키는 조건이 성립한다.
        """
        if any(
            ab.effect.get("name") == state_name and caster in (ab.target_chars or [])
            for ab in self._active
        ):
            return True
        return self.weapon_change_name(caster) == state_name

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
                if timing.startswith("charge_hold:"):
                    raw = timing.split(":", 1)[1]
                    try:
                        found[raw] = float(raw)
                    except ValueError:
                        continue
        result = sorted(((v, raw) for raw, v in found.items()))
        self._charge_hold_cache[caster] = result
        return result

    def _has_target_state(self, state_name: str) -> bool:
        """target_state:/not_target_state: 판정의 단일 창구.

        단일 적 가정 — `"__enemy__"`가 target_chars에 있는 활성 효과로 확인한다.
        """
        return any(
            ab.effect.get("name") == state_name and "__enemy__" in (ab.target_chars or [])
            for ab in self._active
        )

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
        return base_hp * (1.0 + bonus_pct / 100.0) + bonus_flat

    def shield_amount(self, name: str) -> float:
        """name에게 현재 적용 중인 보호막 총량.

        아군 피격 모델이 생기기 전까지는 생성량 그대로 유지되며 ActiveBuff 만료와
        함께 사라진다. 여러 독립 보호막은 각각 보존하고 조회 시 합산한다.
        """
        return sum(
            ab.shield_per_target.get(name, 0.0)
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
                self._notify_adjacent_hp_below(name, prev_pct, new_pct)
            elif prev_pct < 100.0 - _HP_EPS <= new_pct:
                self._notify_adjacent_hp_max(name)

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

    def _activate(self, eff: dict, caster: str, t: float, suppress_event: bool = False):
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
                #
                # 첫 틱 위상은 두 유형이다 (GAMEPLAY.md §효과 실행 순서, 유저 조사):
                #   type 1 `tick_start: "immediate"` — 발동과 동시에 첫 틱 (미하라·아크레인저 블랙)
                #   type 2 (기본)                    — 발동 +interval부터        (디젤·밀크)
                # 회수는 양쪽 같다. 경계 처리는 tick()의 `limit` 참조.
                duration = eff.get("duration")
                expires = math.inf if duration is None or duration == -1 else t + duration
                first_t = t if eff.get("tick_start") == "immediate" else t + tick_interval
                self._dot_timers[id(eff)] = (caster, first_t, expires)
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
                        has_runtime_conditions=_has_runtime_cond(eff["trigger"].get("condition", []), expires),
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

        duration = eff.get("duration")
        if duration is None and "duration_values" in eff:
            char = self._char.get(caster, {})
            skill_lv = _get_skill_lv(char, eff)
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
                # 스쿼드 전체에 브로드캐스트: 다른 캐릭터가 이 이벤트를 트리거로 반응할 수 있음
                if name:
                    for _sq in self.squad_names:
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
                has_runtime_conditions=_has_runtime_cond(eff["trigger"].get("condition", []), expires),
                scaling_stack=self._capture_scaling_stack(eff, caster),
            ))
            name = eff.get("name", "")
            if name:
                # 스쿼드 전체에 브로드캐스트: 다른 캐릭터가 이 이벤트를 트리거로 반응할 수 있음
                for _sq in self.squad_names:
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

        # event:stat_applied:XXX — stat 유형별 버프 적용 시 해당 target_chars에게 notify
        stat = eff.get("stat", "")
        _STAT_APPLIED_EVENTS = {"dot_dmg_pct", "split_dmg_pct"}
        if stat in _STAT_APPLIED_EVENTS and targets:
            event_name = f"event:stat_applied:{stat}"
            for tgt in targets:
                if tgt != "__enemy__":
                    self.notify(event_name, t, tgt)

        # 보호막을 ActiveBuff 수명에 결합해 대상별 생성량을 기록한다. 보호막 상태를
        # 먼저 만든 뒤 적용 이벤트를 쏴야, 같은 프레임의 during_shield 판정이 참이다.
        if stat in _SHIELD_STATS and targets:
            ab_ref = next(
                (ab for ab in self._active if ab.effect is eff and ab.caster == caster),
                None,
            )
            if ab_ref is not None:
                val = self._get_value(eff, ab_ref, caster)
                amount = self.effective_max_hp(caster) * val / 100.0 if val is not None else 0.0
                ab_ref.shield_per_target = {
                    tgt: amount for tgt in (ab_ref.target_chars or []) if tgt != "__enemy__"
                }
                for tgt in ab_ref.shield_per_target:
                    self.notify("event:shield_applied", t, tgt)

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

        # ── 주기 대미지(tick_interval) — 만료 정리보다 **먼저** 처리한다 ──────
        #
        # type 2의 마지막 틱은 버프가 끝나는 바로 그 시각에 떨어진다. 만료를 먼저 치우면
        # 그 틱만 버프 없이 계산돼 딜이 몇 분의 일로 줄어든다 — 인게임에서는 마지막 틱도
        # 버프를 받는다(유저 확인). 순서를 앞에 두는 것으로 "틱 시각을 살짝 당기는" 효과를 낸다.
        if self._damage_handler and self._dot_timers:
            # id → eff 역참조 맵 (필요한 경우에만 구성)
            eff_by_id = {id(eff): eff for eff, _ in self._effects}
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
            if ab.effect.get("stat") in ("hp_caster_based_pct", "hp_only_caster_based_pct") and "hp" in self.state:
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
                # effect_interval 버프 반영: 이 효과(target_effect)의 발동 주기를 초 단위로 가감
                eff_name = eff.get("name", "")
                flat = 0.0
                if eff_name:
                    flat = sum(
                        (self._get_value(ab.effect, ab, caster) or 0.0)
                        for ab in self._active
                        if ab.effect.get("stat") == "effect_interval"
                        and ab.effect.get("target_effect") == eff_name
                        and (ab.target_chars is None or caster in (ab.target_chars or []))
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
                    if self._condition_ok(eff["trigger"].get("condition", []), caster, t, eff):
                        self._activate(eff, caster, t)
                    self._next_fire[eid] = (next_t + interval, interval)

        # tick_interval instant 처리
        if self._instant_timers:
            eff_by_id = {id(eff): eff for eff, _ in self._effects}
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
        cache_key = (caster, t, self._cache_version, exclude_names)
        cached = self._buffs_cache.get(cache_key)
        if cached is not None:
            return cached

        buffs = dict(_BUFFS_ZERO)
        crit_rate_parts: list[float] = [0.15]  # 기본 크리확률 15%

        for ab in self._active:
            if t >= ab.expires_at:
                continue

            eff = ab.effect
            if exclude_names and ab.caster == caster and eff.get("name") in exclude_names:
                continue
            stat = eff.get("stat", "")
            buff_key = _STAT_TO_BUFF.get(stat)
            if not buff_key:
                continue

            # runtime condition 재평가: 플래그가 없는 버프(정적 조건 전용)는 건너뜀
            if ab.has_runtime_conditions:
                conditions = eff["trigger"].get("condition", [])
                if not self._runtime_condition_ok(conditions, ab.caster, caster, target, t):
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

            # def_pct: 적(enemy)에게 부여되면 방어력 감소(②)로 라우팅.
            # 아군 대상 def_pct는 base_stat용 — 데미지엔 무관하므로 def_pct 키로 흘려보내 무시.
            if stat == "def_pct" and applies_to_target and not applies_to_caster:
                buff_key = "enemy_def_down_pct"

            # caster_based 환산을 위해 실제 버프 수령자를 특정
            actual_recipient = caster if applies_to_caster else target

            # boolean 플래그 스탯: 수치 없이 True만 세팅
            if buff_key in ("charge_time_fixed", "charge_speed_buff_immune", "charge_speed_debuff_immune",
                            "debuff_immune", "stun_immune", "stack_change_immune", "taunt",
                            "pierce_enabled", "armor_break_enabled"):
                buffs[buff_key] = True
                continue

            char_stack = ab.per_char_stacks.get(caster) if ab.per_char_stacks else None
            val = self._get_value(eff, ab, actual_recipient, stack_override=char_stack)
            if val is None:
                continue

            if stat in _CRIT_RATE_STATS:
                crit_rate_parts.append(val / 100)
            else:
                buffs[buff_key] = buffs.get(buff_key, 0.0) + val

        # 크리확률 합성: 단순 합연산 (유저 인게임 확인). 100%에서 자른다 —
        # 초과분은 게임에서도 버려지고, calc_avg_damage()의 기댓값 계산이 1을 넘으면 깨진다.
        buffs["crit_rate"] = min(1.0, sum(crit_rate_parts))

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
            if ab.has_runtime_conditions:
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
            if ab.has_runtime_conditions:
                conditions = ab.effect["trigger"].get("condition", [])
                if not self._runtime_condition_ok(conditions, ab.caster, caster, target, t):
                    continue
            val = self._get_value(ab.effect, ab, ab.caster)
            if val is None:
                continue
            caster_atk = self.state.get("base_stats", {}).get(ab.caster, {}).get("atk", 0.0)
            # atk_buff_mag_pct: 이 named buff를 target_effect로 참조하는 배율 적용
            mag_mult = 1.0
            buff_name = ab.effect.get("name", "")
            if buff_name:
                for mag_ab in self._active:
                    if mag_ab.expires_at <= t:
                        continue
                    if mag_ab.effect.get("stat") != "atk_buff_mag_pct":
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
        if buffs["charge_time_fixed"]:
            buffs["charge_speed_pct"] = 0.0
        else:
            # 면역 후처리: 양수(증가) 또는 음수(감소) 성분만 제거
            if buffs["charge_speed_buff_immune"] and buffs["charge_speed_pct"] > 0:
                buffs["charge_speed_pct"] = 0.0
            if buffs["charge_speed_debuff_immune"] and buffs["charge_speed_pct"] < 0:
                buffs["charge_speed_pct"] = 0.0

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
            elif cond.startswith("enemy_count_below:"):
                # 단일 보스 sim: 적 1기. "랩쳐 N기 이하" → 1 <= N (N>=1이면 항상 참)
                if 1 > int(cond.split(":")[1]):
                    return False
            elif cond.startswith("enemy_count_above:"):
                # 단일 보스 sim: 적 1기. "랩쳐 N기 이상" → 1 >= N (N>=2이면 항상 거짓)
                if 1 < int(cond.split(":")[1]):
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
        for ab in self._active:
            if ab.caster == caster and ab.effect.get("name") == ref:
                return ab.stack
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
        """target 문자열 → 캐릭터명 목록."""
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
        if target in ("enemy", "all_enemies", "target", "target_body", "same_target",
                      "enemies_in_range", "enemies_nearest_in_range"):
            # 적 대상: "__enemy__" 센티널 사용 (타임라인이 판단)
            return ["__enemy__"]

        if target.startswith("allies_lowest_atk_burst3:"):
            n = int(target.split(":")[1])
            burst3 = [name for name in self.squad_names
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
            pool = [x for x in self.squad_names if x != caster]
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
        if target.startswith("allies_weapon_excl_self:"):
            wtype = target.split(":")[1]
            return [n for n in self.squad_names
                    if _NIKKE[n]["weapon_type"] == wtype and n != caster]
        if target.startswith("allies_weapon:"):
            wtype = target.split(":")[1]
            return [n for n in self.squad_names
                    if _NIKKE[n]["weapon_type"] == wtype]
        # "[버프명] 상태인 아군 전체" — 부여 시점 스냅샷(비lazy).
        # 상태 판정은 self_state:와 같은 창구를 써서 weapon_change 모드도 함께 본다.
        if target.startswith("allies_with_buff:"):
            buff_name = target.split(":", 1)[1]
            return [n for n in self.squad_names if self._has_self_state(n, buff_name)]
        # "직전에 버스트 스킬을 사용한 [무기] 아군 전체" — burst_casted ∩ 무기유형.
        # burst_casted condition은 시전자 기준으로만 평가돼 대상 필터로 쓸 수 없어 target으로 둔다.
        if target.startswith("allies_burst_casted_weapon:"):
            wtype = target.split(":", 1)[1]
            casted = self.state.get("burst_casted", {})
            return [n for n in self.squad_names
                    if casted.get(n) and _NIKKE[n]["weapon_type"] == wtype]
        if target.startswith("allies_class:"):
            cls = target.split(":")[1]
            return [n for n in self.squad_names if _NIKKE[n]["class"] == cls]
        if target.startswith("allies_code:"):
            code = target.split(":")[1]
            return [n for n in self.squad_names if _NIKKE[n].get("element_code") == code]
        # 코드 + 무기유형 복합. leftmost는 스쿼드 입력 순서 기준 앞에서 N명
        # (고정 속성 기반이므로 lazy resolve 불필요)
        if target.startswith("allies_code_weapon_leftmost:"):
            _, code, wtype, n = target.split(":")
            return self._code_weapon(code, wtype)[:int(n)]
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
        # "직전에 버스트 스킬을 사용한 기본 버스트 단계 Step 3 아군" — burst_casted ∩ B3.
        # allies_burst_casted_weapon:과 같은 취지다 — burst_casted를 condition으로 두면
        # 시전자 기준으로만 평가돼 "누가 버스트를 썼나"를 대상 필터로 쓸 수 없다.
        if target == "allies_burst_casted_burst3":
            casted = self.state.get("burst_casted", {})
            burst_stages = self.state.get("burst_stages", {})
            return [n for n in self.squad_names
                    if casted.get(n) and burst_stages.get(n) == "3"]

        # 적 관련 (타임라인 처리)
        if target.startswith("enemies") or target in ("target", "target_body", "same_target"):
            return ["__enemy__"]

        # 커버, 발사체 등
        return []

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
        pool = [name for name in self.squad_names if name != exclude]
        if stat == "atk":
            pool.sort(key=self._effective_atk, reverse=True)
        else:
            base_stats = self.state.get("base_stats", {})
            pool.sort(key=lambda x: base_stats.get(x, {}).get(stat, 0), reverse=True)
        return pool[:n]

    def _lowest_hp(self, n: int, exclude: str | None = None) -> list[str]:
        hp_pct = self.state.get("hp_pct", {})
        names = [x for x in self.squad_names if x != exclude]
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
        self._instant_timers.clear()
        self._lazy_target_cache.clear()
        self._event_counts.clear()
        self._trigger_counts.clear()
        self._buffs_cache.clear()
        self._cache_version = 0
        self._cond_passive_prev.clear()

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
