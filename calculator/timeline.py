"""
Phase 5: 전투 타임라인 시뮬레이터

simulate(squad, config, enemy) → SimResult

설계:
  - dt = 1/60초 (16.67ms) 고정 스텝
  - 발사: while current_time >= next_fire_time 루프로 누적 오차 없음
  - SG: 펠릿마다 calc_damage() 독립 호출, hit_count notify 펠릿 수만큼 발생
  - 버스트 사용 중에도 기본 발사는 계속 진행 (bursting 플래그 없음)
  - weapon_change 타입 스킬: 활성 시 임시 무기 교체 후 차지 사격 1발 발사
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from .base_stat import calc_base_stats
from .buff_manager import BuffManager, _get_skill_lv
from .damage import calc_damage, default_hit_type, is_element_match
from .sim_result import (
    HitEvent,
    BurstLogEntry,
    BuffEntry,
    BuffEvent,
    BuffSnapshot,
    InstantEvent,
    ReloadLogEntry,
    SimLog,
    SimResult,
)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_NIKKE        = _load(os.path.join(_DATA_DIR, "parsed_nikke.json"))
_MECHANICS    = _load(os.path.join(_DATA_DIR, "weapon_mechanics.json"))
_PARSED_SKILLS = _load(os.path.join(_DATA_DIR, "parsed_skills.json"))
_DELAYS       = _load(os.path.join(_DATA_DIR, "weapon_delays.json"))

DT = 1 / 60  # 시뮬레이션 스텝 (초)

# ── 기본 config / enemy ────────────────────────────────────────────────────

DEFAULT_CHAR: dict = {
    "level": 400,
    "breakthrough": 3,
    "core_enhancement": 0,
    "affinity": 30,
    "skill_levels": {"1": 10, "2": 10, "3": 10},
    "burst_regen_time": 2.0,
    "equipment": {p: {"level": 5, "skills": []} for p in ["머리", "몸통", "팔", "다리"]},
    "cube": {"name": "재장", "level": 15},
    "console": {"common_level": 180, "class_level": 100, "company_level": 100},
    "collection_stage": "SR15",
}

DEFAULT_CONFIG: dict = {
    "duration":           180.0,  # 시뮬레이션 시간(초) — 실제 니케 전투 3분
    "burst_switch_delay":  0.1,   # 버스트 단계 전환 딜레이(초)
    "burst_reenter_delay": 0.5,   # reenter 딜레이(초)
    "max_burst_count":    None,   # 최대 풀버스트 횟수 (None = 무제한)
    "burst_sequence":     None,   # 풀버스트별 단계 사용 순서 list[dict[str, list[str]]] (None = 자동)
}

DEFAULT_ENEMY: dict = {
    "def":                  31784,
    "code":                 None,
    "has_core":             False,
    "optimal_range_weapons": [],  # 적정거리 적용 무기군 목록 e.g. ["SG", "SMG"]
}




# ── CharState (캐릭터별 발사 상태) ────────────────────────────────────────

class CharState:
    """캐릭터 1명의 발사 루프 상태 관리. 버스트 사용 중에도 발사 계속."""

    def __init__(self, char: dict, base_atk: float, is_element_match: bool):
        self.char = char
        self.name = char["name"]
        self.base_atk = base_atk
        self.is_element_match = is_element_match

        weapon_data = _NIKKE[self.name]
        self.burst_stage: str = weapon_data["burst_stage"]
        self.weapon = weapon_data
        self.weapon_type = weapon_data["weapon_type"]

        mech = _MECHANICS["weapon_type_defaults"][self.weapon_type]
        self.mech = mech
        self.fire_mode: str = mech["type"]  # "auto" / "auto_warmup" / "charge"

        self.ammo: int = weapon_data["max_ammo"]
        self.reloading_until: float = -1.0
        self._post_reload_end_t: float = -1.0
        self.next_fire_time: float = 0.0
        self._sim_log: SimLog | None = None

        # MG 예열
        self.warmup_shots: int = 0
        self.last_fire_t: float = -999.0

        # delay 값: weapon_delays.json 기준
        _delay_exc = _DELAYS["_exceptions"].get(self.name, {})
        _delay_wt  = _DELAYS["_defaults_by_weapon_type"].get(self.weapon_type, {})
        self.post_reload_delay: float = _delay_exc.get("post_reload_delay", _delay_wt.get("post_reload_delay", 0.0))

        # charge (SR/RL)
        if self.fire_mode == "charge":
            charge_time_raw = char.get("charge_time_frames")
            if charge_time_raw is not None:
                self.charge_time_base: float = charge_time_raw / 60.0
            else:
                self.charge_time_base = weapon_data["charge_time"]
            self.post_fire_delay: float = _delay_exc.get("post_fire_delay", _delay_wt.get("post_fire_delay", mech.get("post_fire_delay", 0.0)))
        else:
            self.charge_time_base = 0.0
            self.post_fire_delay = 0.0
        self._charge_phase: str = "ready"
        self._charge_start_t: float = 0.0
        self._charge_end_t: float = 0.0
        self._post_delay_end_t: float = 0.0

        # SG
        self.pellets: int = mech.get("pellets", 1)

        self._in_weapon_change: bool = False

    def tick(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        # 기절 중: 일반공격 불가
        if bm.is_stunned(self.name):
            return []

        # weapon_change 활성 시: 임시 무기 교체 후 차지 사격 처리
        wc_eff = bm.get_weapon_change(self.name)
        if wc_eff is not None:
            self._in_weapon_change = True
            return self._tick_weapon_change(t, bm, enemy, cfg, wc_eff)

        # weapon_change 만료 직후: next_fire_time 리셋으로 과거 발사 빚 방지
        if self._in_weapon_change:
            self._in_weapon_change = False
            self.next_fire_time = t

        # 재장전 완료 체크
        if self.reloading_until > 0:
            if t < self.reloading_until:
                return []
            self._finish_reload(t, bm)

        # post_reload_delay 대기 (재장전 완료 후 발사 전 고정 딜레이)
        if self._post_reload_end_t > 0:
            if t < self._post_reload_end_t:
                return []
            self._post_reload_end_t = -1.0
            self.next_fire_time = t

        if self.fire_mode in ("auto", "auto_warmup"):
            return self._tick_auto(t, bm, enemy, cfg)
        else:
            return self._tick_charge(t, bm, enemy, cfg)

    # ── auto / auto_warmup ────────────────────────────────────────────────

    def _tick_auto(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        events = []
        while t >= self.next_fire_time:
            if self.ammo <= 0:
                self._start_reload(t, bm)
                break
            fire_rate = self._current_fire_rate(bm, t)
            events.extend(self._fire(t, bm, enemy, cfg))
            self.next_fire_time += 1.0 / fire_rate
            if self.fire_mode == "auto_warmup":
                self.last_fire_t = t

        if self.fire_mode == "auto_warmup":
            if t - self.last_fire_t >= self.mech.get("cooldown_time", 1.1):
                self.warmup_shots = 0

        return events

    def _current_fire_rate(self, bm: BuffManager, t: float) -> float:
        if self.fire_mode == "auto_warmup":
            fr_min = self.mech["fire_rate_min"]
            fr_max = self.mech["fire_rate_max"]
            warmup = self.mech["warmup_bullets"]
            base = fr_min + (fr_max - fr_min) * min(self.warmup_shots, warmup) / warmup
        else:
            base = self.mech["fire_rate"]
        speed_pct = bm.get_buffs(self.name, "__enemy__", t).get("attack_speed_pct", 0.0)
        return base * max(0.01, 1.0 + speed_pct / 100.0)

    def _fire(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        events = []
        is_last = (self.ammo == 1)
        if is_last:
            bm.notify("last_bullet_fire", t, self.name)

        if self.fire_mode == "auto_warmup":
            if self.warmup_shots < self.mech["warmup_bullets"]:
                self.warmup_shots += 1

        self.ammo -= 1
        bm.notify("squad_ammo_consume", t, self.name)
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        buffs["is_element_match"] = self.is_element_match
        is_core = enemy.get("has_core", False)
        is_optimal = self.weapon_type in enemy.get("optimal_range_weapons", [])

        is_full_burst = bm.state.get("full_burst", False)
        debug_char = cfg.get("_debug_char")
        in_debug_window = (
            debug_char == self.name
            and cfg.get("_debug_t0", -1.0) <= t <= cfg.get("_debug_t1", -1.0)
        )

        # 실효 펠릿 수: pellet_count_fixed > 0이면 절대값 고정, 아니면 기본값 + 증가량
        pellet_fixed = buffs.get("pellet_count_fixed", 0.0)
        if pellet_fixed > 0:
            effective_pellets = max(1, int(round(pellet_fixed)))
        else:
            effective_pellets = max(1, self.pellets + int(round(buffs.get("pellet_count", 0.0))))

        for i in range(effective_pellets):
            coeff = (self.weapon["damage_coeff"] / effective_pellets) if effective_pellets > 1 else None
            ht = default_hit_type(
                is_core=is_core,
                is_full_burst=is_full_burst,
                is_optimal_range=is_optimal,
                is_normal_atk=True,
                is_pierce_damage=bool(buffs.get("pierce_enabled")),
                is_armor_break_damage=bool(buffs.get("armor_break_enabled")),
                coeff=coeff,
                _debug_factors=in_debug_window,
            )
            if in_debug_window and i == 0:
                print(f"t={t:.3f}s  base_atk={self.base_atk:,}  enemy_def={enemy.get('def', 31784):,}")
            res = calc_damage(
                base_atk=self.base_atk, buffs=buffs, weapon=self.weapon,
                hit_type=ht, enemy_def=enemy.get("def", 31784),
            )
            if in_debug_window and i == 0:
                print()
            tag = (f"core:pellet:{i}" if is_core else f"pellet:{i}") if effective_pellets > 1 \
                  else ("core" if is_core else "normal")
            events.append(HitEvent(t=t, caster=self.name, damage=res["damage"],
                                   is_crit=res["is_crit"], hit_tag=tag))
            bm.notify("pellet_hit", t, self.name)
            if not is_core:
                ev = "squad_part_hit" if enemy.get("has_parts", False) else "squad_body_hit"
                bm.notify_team_hit(ev, t, self.name)
            if res["is_crit"]:
                bm.notify("crit_hit", t, self.name)
            if is_core:
                bm.notify("core_hit", t, self.name)

        # hit_count: 발사 1회당 1회 (펠릿 수와 무관). pellet_hit은 루프 내 펠릿마다 발생
        bm.notify("hit_count", t, self.name)
        bm.notify("on_attack", t, self.name)
        bm.consume_bullet_buffs(self.name, t)
        if is_last:
            bm.notify("last_bullet", t, self.name)

        return events

    # ── charge (SR/RL) ────────────────────────────────────────────────────

    def _tick_charge(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        events = []

        if self._charge_phase == "ready":
            if self.ammo <= 0:
                self._start_reload(t, bm)
                return events
            self._charge_start_t = t
            self._charge_phase = "charging"
            bm.state.setdefault("charging", {})[self.name] = True
            if self.ammo == 1:
                bm.notify("last_bullet_fire", t, self.name)

        if self._charge_phase == "charging":
            buffs = bm.get_buffs(self.name, "__enemy__", t)
            if buffs.get("charge_time_fixed"):
                charge_time = self._fixed_charge_time(bm)
            else:
                cs_pct = buffs.get("charge_speed_pct", 0.0) / 100.0
                charge_time = self.charge_time_base * max(0.0, 1.0 - cs_pct)
            self._charge_end_t = self._charge_start_t + charge_time
            if t < self._charge_end_t:
                return events
            is_core = enemy.get("has_core", False)
            is_optimal = self.weapon_type in enemy.get("optimal_range_weapons", [])
            bm.notify("full_charge", t, self.name)
            buffs = bm.get_buffs(self.name, "__enemy__", t)
            buffs["is_element_match"] = self.is_element_match

            debug_char = cfg.get("_debug_char")
            in_debug_window = (
                debug_char == self.name
                and cfg.get("_debug_t0", -1.0) <= t <= cfg.get("_debug_t1", -1.0)
            )

            is_full_burst = bm.state.get("full_burst", False)
            ht = default_hit_type(
                is_core=is_core,
                is_full_burst=is_full_burst,
                is_optimal_range=is_optimal,
                is_normal_atk=True,
                is_full_charge=True,
                is_pierce_damage=bool(buffs.get("pierce_enabled")),
                is_armor_break_damage=bool(buffs.get("armor_break_enabled")),
                is_projectile_explosion=(self.weapon.get("weapon_type") == "RL"),
                _debug_factors=in_debug_window,
            )
            if in_debug_window:
                print(f"t={t:.3f}s  base_atk={self.base_atk:,}  enemy_def={enemy.get('def', 31784):,}")
            res = calc_damage(
                base_atk=self.base_atk, buffs=buffs, weapon=self.weapon,
                hit_type=ht, enemy_def=enemy.get("def", 31784),
            )
            if in_debug_window:
                print()
            tag = "core+full_charge_hit" if is_core else "full_charge_hit"
            events.append(HitEvent(t=t, caster=self.name, damage=res["damage"],
                                   is_crit=res["is_crit"], hit_tag=tag))
            is_last = (self.ammo == 1)
            self.ammo -= 1
            bm.notify("squad_ammo_consume", t, self.name)
            bm.notify("hit_count", t, self.name)
            bm.notify("full_charge_hit", t, self.name)
            if not is_core:
                ev = "squad_part_hit" if enemy.get("has_parts", False) else "squad_body_hit"
                bm.notify_team_hit(ev, t, self.name)
            bm.notify("on_attack", t, self.name)
            bm.consume_bullet_buffs(self.name, t)
            if res["is_crit"]:
                bm.notify("crit_hit", t, self.name)
            if is_core:
                bm.notify("core_hit", t, self.name)
            if is_last:
                bm.notify("last_bullet", t, self.name)

            self._post_delay_end_t = t + self.post_fire_delay
            self._charge_phase = "post_delay"
            bm.state.setdefault("charging", {})[self.name] = False

        elif self._charge_phase == "post_delay" and t >= self._post_delay_end_t:
            self._charge_phase = "ready"
            return self._tick_charge(t, bm, enemy, cfg)

        return events

    # ── weapon_change ─────────────────────────────────────────────────────

    def _tick_weapon_change(
        self, t: float, bm: BuffManager, enemy: dict, cfg: dict, wc_eff: dict
    ) -> list[HitEvent]:
        """
        weapon_change 활성 중 발사 루프.
        SR/RL 계열 차지 무기만 지원. 발사 완료 후 duration_bullets 소진이면 end_weapon_change().
        기존 CharState 필드(weapon, weapon_type, fire_mode, charge_time_base, post_fire_delay)
        를 임시 교체 후 _tick_charge()에 위임하고, 발사 완료 시 원복한다.
        """
        # weapon_change effect의 스킬 레벨별 damage_coeff 결정
        skill_lv = _get_skill_lv(self.char, wc_eff)
        dc = wc_eff.get("damage_coeff", {})
        if isinstance(dc, dict):
            coeff = float(dc.get(skill_lv, dc.get("10", 0.0)))
        else:
            coeff = float(dc)

        wc_weapon_type = wc_eff.get("weapon_type", "SR")
        wc_mech = _MECHANICS["weapon_type_defaults"].get(wc_weapon_type, {})
        wc_max_ammo = wc_eff.get("max_ammo", 1)
        wc_charge_time = wc_eff.get("charge_time", 1.0)
        wc_full_charge_mult = wc_eff.get("full_charge_mult", 100.0)
        wc_reload_time = wc_eff.get("reload_time", self.weapon.get("reload_time", 1.5))
        wc_core_dmg_mult = wc_eff.get("core_dmg_mult", self.weapon.get("core_dmg_mult", 200.0))
        wc_post_fire_delay = wc_eff.get("post_fire_delay", wc_mech.get("post_fire_delay", 0.0))

        # 임시 무기 dict 구성 (calc_damage가 weapon["full_charge_mult"] 등을 참조)
        wc_weapon_dict = {
            **self.weapon,
            "weapon_type": wc_weapon_type,
            "damage_coeff": coeff,
            "max_ammo": wc_max_ammo if wc_max_ammo != -1 else 999999,
            "charge_time": wc_charge_time,
            "full_charge_mult": wc_full_charge_mult,
            "reload_time": wc_reload_time,
            "core_dmg_mult": wc_core_dmg_mult,
        }

        # 발사 전 charge_phase가 ready인 경우 ammo를 weapon_change 장탄으로 세팅
        # (이미 charging 중이거나 post_delay 중이면 그대로 진행)
        was_ready = (self._charge_phase == "ready")

        # CharState 필드 임시 교체
        orig_weapon       = self.weapon
        orig_weapon_type  = self.weapon_type
        orig_fire_mode    = self.fire_mode
        orig_charge_time  = self.charge_time_base
        orig_post_delay   = self.post_fire_delay
        orig_ammo         = self.ammo if not was_ready else None

        self.weapon           = wc_weapon_dict
        self.weapon_type      = wc_weapon_type
        self.fire_mode        = "charge"
        self.charge_time_base = wc_charge_time
        self.post_fire_delay  = wc_post_fire_delay
        if was_ready:
            self.ammo = wc_max_ammo if wc_max_ammo != -1 else 999999

        events = self._tick_charge(t, bm, enemy, cfg)

        # 원복
        self.weapon           = orig_weapon
        self.weapon_type      = orig_weapon_type
        self.fire_mode        = orig_fire_mode
        self.charge_time_base = orig_charge_time
        self.post_fire_delay  = orig_post_delay
        if orig_ammo is not None and was_ready:
            # ready→charging 전환만 된 경우는 ammo 원복 불필요 (충전 중)
            pass

        # duration_bullets 기반: 발사 완료(post_delay 진입) 시 weapon_change 종료
        if events and wc_eff.get("duration_bullets") is not None:
            bm.end_weapon_change(self.name)
            # 발사 후 원래 무기로 돌아오면 charge_phase를 ready로 초기화
            self._charge_phase = "ready"
            self.ammo = orig_ammo if orig_ammo is not None else self.weapon["max_ammo"]

        return events

    def _fixed_charge_time(self, bm: BuffManager) -> float:
        """charge_time_fixed 버프의 fixed_value(초)를 반환. 복수이면 최대값. 없으면 charge_time_base."""
        max_val = self.charge_time_base
        for ab in bm._active:
            if ab.caster != self.name:
                continue
            if ab.effect.get("stat") != "charge_time_fixed":
                continue
            val = ab.effect.get("fixed_value")
            if val is not None:
                max_val = max(max_val, float(val))
        return max_val

    # ── 재장전 ────────────────────────────────────────────────────────────

    def _start_reload(self, t: float, bm: BuffManager):
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        speed_pct = buffs.get("reload_speed_pct", 0.0) / 100.0
        reload_time = self.weapon["reload_time"] * max(0.0, 1.0 - speed_pct)
        self.reloading_until = t + reload_time
        bm.state.setdefault("charging", {})[self.name] = False
        if self.fire_mode == "auto_warmup":
            self.warmup_shots = 0
        if self._sim_log is not None:
            self._sim_log.reload_log.append(ReloadLogEntry(t=t, caster=self.name, event="재장전 시작"))

    def _finish_reload(self, t: float, bm: BuffManager):
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        ammo_pct = buffs.get("max_ammo_pct", 0.0) / 100.0
        ammo_flat = int(round(buffs.get("max_ammo_flat", 0.0)))
        self.ammo = round(self.weapon["max_ammo"] * (1.0 + ammo_pct)) + ammo_flat
        self.reloading_until = -1.0
        bm.notify("event:full_reload", t, self.name)
        if self._sim_log is not None:
            self._sim_log.reload_log.append(ReloadLogEntry(t=t, caster=self.name, event="재장전 완료"))
        if self.post_reload_delay > 0.0:
            self._post_reload_end_t = t + self.post_reload_delay
        else:
            self.next_fire_time = t


# ── BurstController ───────────────────────────────────────────────────────

class BurstController:
    """
    스쿼드 버스트 흐름 관리. 발사 루프와 완전 독립.

    버스트 쿨타임: 캐릭터별로 parsed_nikke.json 스킬3 쿨타임 필드에서 읽음.
    같은 단계에 N명 있어도 1명만 사용하면 다음 단계 진입.
    우선순위: 스쿼드 입력 순서, 쿨타임 불가 시 다음 순위.
    reenter: 같은 단계 재사용, 0.5초 딜레이, 단계 전환 없음.
    """

    def __init__(
        self,
        squad: list[dict],
        config: dict,
        char_states: dict[str, CharState],
        enemy: dict,
    ):
        self.config = config
        self.char_states = char_states
        self.enemy_def: int = enemy.get("def", 31784)
        self.squad_names = [c["name"] for c in squad]

        # 캐릭터별 기본(고정) 버스트 단계 — 변하지 않음
        # 스쿼드 config에 "burst_stage" 필드가 있으면 parsed_nikke 값보다 우선 적용 ("A" 캐릭터 슬롯 지정용)
        self._default_burst_stage: dict[str, str] = {
            c["name"]: c.get("burst_stage") or _NIKKE[c["name"]]["burst_stage"] for c in squad
        }

        # 최대 풀버스트 횟수 / 사이클별 단계 사용 순서 / 버스트 미사용 캐릭터
        # (_rebuild_burst_order에서 참조하므로 burst_order 초기화 전에 설정)
        self._max_burst_count: int | None = config.get("max_burst_count")
        self._burst_sequence: list[dict] | None = config.get("burst_sequence")
        self._burst_count: int = 0
        self._no_burst_char: str | None = config.get("no_burst_char")

        # 단계별 우선순위 목록 (입력 순서) — tick마다 _rebuild_burst_order()로 갱신
        self.burst_order: dict[str, list[str]] = {"1": [], "2": [], "3": []}
        self._rebuild_burst_order({})

        # 캐릭터별 버스트 쿨타임 (parsed_nikke.json burst_cooldown 필드)
        self._burst_cd: dict[str, float] = {
            c["name"]: _NIKKE[c["name"]].get("burst_cooldown", 40.0) for c in squad
        }

        # 캐릭터별 버스트 사용 가능 시각
        self.burst_ready_at: dict[str, float] = {n: 0.0 for n in self.squad_names}

        # burst_cast 시 반영된 burst_cooldown 추적 (full_burst_start 소급 보정용)
        self._cd_applied_at_cast: dict[str, float] = {n: 0.0 for n in self.squad_names}

        # 버스트 게이지 충전 완료 시각
        self.gauge_full_at: dict[str, float] = {
            c["name"]: c.get("burst_regen_time", 2.0) for c in squad
        }

        # 버스트 진행 상태
        # "idle" / "stage:N" / "reenter:N" / "switching" / "full_burst"
        self._phase: str = "idle"
        self._next_action_t: float = math.inf
        self._full_burst_end_t: float = -1.0

        # reenter 대기 중인 단계
        self._reenter_stage: str = ""

        # 풀버스트 진입 시 발동할 버스트 대미지 (버프 적용 후 계산)
        self._pending_burst_dmg: list[tuple[str, dict, int]] = []  # (caster, eff, hit_count)

        # 현재 풀버스트 사이클의 3단계 버스트 발동자 (fullburst_duration 귀속용)
        self._fb_caster: str = ""

        # verbose 로그 (simulate에서 주입)
        self._log: SimLog | None = None

    def tick(self, t: float, bm: BuffManager, state: dict) -> list[HitEvent]:
        events: list[HitEvent] = []

        # ── 유효 버스트 단계 갱신 ─────────────────────────────────────────
        # burst_stage_override:N 버프 활성 여부를 매 tick 반영
        active_stages: dict[str, str] = {}
        for ab in bm._active:
            stat = ab.effect.get("stat", "")
            if stat.startswith("burst_stage_override:") and not "reenter" in stat:
                n = stat.split(":")[1]
                active_stages[ab.caster] = n
        self._rebuild_burst_order(active_stages)
        # state["burst_stages"]는 condition 평가에 쓰이므로 현재 유효 단계로 동기화
        for name in self.squad_names:
            state["burst_stages"][name] = (
                active_stages.get(name) or self._default_burst_stage.get(name, "")
            )

        # ── 풀버스트 종료 ──────────────────────────────────────────────────
        if self._phase == "full_burst" and t >= self._full_burst_end_t - 1e-9:
            self._phase = "idle"
            state["full_burst"] = False
            # burst_casted 리셋은 notify 이후: full_burst_end 트리거 조건에서 burst_casted를 참조하는 경우 대비
            for n in self.squad_names:
                bm.notify("full_burst_end", t, n)
            for n in self.squad_names:
                state["burst_casted"][n] = False
            if self._log is not None:
                self._log.burst_log.append(BurstLogEntry(t=t, event="full_burst 종료", caster=""))
            for name in self.squad_names:
                regen = self.char_states[name].char.get("burst_regen_time", 2.0)
                self.gauge_full_at[name] = t + regen
            self._burst_count += 1

        # ── idle → 게이지 충전 완료 시 1단계 진입 ─────────────────────────
        _at_max = (self._max_burst_count is not None and self._burst_count >= self._max_burst_count)
        if self._phase == "idle" and not _at_max:
            if all(t >= self.gauge_full_at[n] - 1e-9 for n in self.squad_names):
                self._phase = "stage:1"
                self._next_action_t = t
                for n in self.squad_names:
                    bm.notify("burst_enter:1", t, n)

        # ── 단계 스킬 사용 ─────────────────────────────────────────────────
        if self._phase.startswith("stage:") and t >= self._next_action_t - 1e-9:
            stage = self._phase.split(":")[1]
            ev, advanced, reenter_info = self._try_use_stage(stage, t, bm, state)
            events.extend(ev)

            if reenter_info:
                # reenter: 같은 단계 재진입 대기 (사용자는 딜레이 후 재선출)
                _, r_stage = reenter_info
                self._reenter_stage = r_stage
                self._phase = f"reenter:{r_stage}"
                self._next_action_t = t + self.config.get("burst_reenter_delay", 0.5)
            elif advanced:
                if stage == "3":
                    self._phase = "switching"
                    self._next_action_t = t + 0.05
                else:
                    next_stage = str(int(stage) + 1)
                    self._phase = f"stage:{next_stage}"
                    self._next_action_t = t + self.config.get("burst_switch_delay", 0.1)
                    for n in self.squad_names:
                        bm.notify(f"burst_enter:{next_stage}", t, n)

        # ── reenter 딜레이 완료 → 재진입 ──────────────────────────────────
        if self._phase.startswith("reenter:") and t >= self._next_action_t - 1e-9:
            r_stage = self._reenter_stage
            # 재진입 단계 진입 이벤트 발생 (burst_enter:N 조건 트리거용)
            for n in self.squad_names:
                bm.notify(f"burst_enter:{r_stage}", t, n)
            # 해당 단계 후보 중 쿨타임이 풀린 캐릭터를 재선출 (reenter 발동자는 이미 쿨)
            ev, advanced, _ = self._try_use_stage(r_stage, t, bm, state)
            events.extend(ev)
            if not advanced:
                # 전원 쿨타임 중이면 대기 (이미 _next_action_t가 갱신됨)
                pass
            elif r_stage == "3":
                self._phase = "switching"
                self._next_action_t = t + 0.05
            else:
                next_stage = str(int(r_stage) + 1)
                self._phase = f"stage:{next_stage}"
                self._next_action_t = t + self.config.get("burst_switch_delay", 0.1)
                for n in self.squad_names:
                    bm.notify(f"burst_enter:{next_stage}", t, n)

        # ── 전환 딜레이 → 풀버스트 진입 ───────────────────────────────────
        if self._phase == "switching" and t >= self._next_action_t - 1e-9:
            self._phase = "full_burst"
            # fullburst_duration 버프(초) 합산.
            # 동일 caster의 버프가 all_allies target으로 여러 캐릭터에 등록되어도
            # 풀버스트 지속 시간 기여는 caster당 1회만 집계한다.
            # _fb_caster(3단계 발동자)의 버프는 본인이 직접 풀버스트를 발동할 때만 적용.
            seen_casters: set[str] = set()
            fb_ext = 0.0
            for ab in bm._active:
                if ab.effect.get("stat") != "fullburst_duration":
                    continue
                if ab.caster in seen_casters:
                    continue
                # burst_cast 타이밍으로 등록된 fullburst_duration은
                # 해당 caster가 이번 풀버스트의 3단계 발동자일 때만 반영
                timings = ab.effect.get("trigger", {}).get("timing", [])
                if "burst_cast" in timings and ab.caster != self._fb_caster:
                    continue
                val = ab.effect.get("fixed_value")
                if val is None:
                    lv = _get_skill_lv(self.char_states[ab.caster].char, ab.effect)
                    vals = ab.effect.get("values", {})
                    val = float(vals.get(lv, vals.get("10", 0.0)))
                fb_ext += float(val)
                seen_casters.add(ab.caster)
            self._full_burst_end_t = t + max(1.0, 10.0 + fb_ext)
            state["full_burst"] = True
            for n in self.squad_names:
                bm.notify("full_burst_start", t, n)
            # full_burst_start마다 burst_cooldown 버프를 burst_ready_at에 반영.
            # 쿨 감소는 풀버스트 1회당 1회 적용: 40초 캐릭터가 격사이클로 버스트하면
            # 2회의 full_burst_start에서 각각 감소를 받아 실효 쿨 = 40 - 7.48×2 = 25.04초.
            # _cd_applied_at_cast는 이번 사이클 cast에서 이미 반영한 값을 추적 (중복 방지).
            # dict.fromkeys: 동명 캐릭터 중복 보정 방지
            for n in dict.fromkeys(self.squad_names):
                cd_now = bm.get_buffs(n, "__enemy__", t).get("burst_cooldown", 0.0)
                if self.burst_ready_at[n] > t:
                    extra = cd_now - self._cd_applied_at_cast.get(n, 0.0)
                    if extra > 0.0:
                        self.burst_ready_at[n] = max(t, self.burst_ready_at[n] - extra)
                # 다음 full_burst_start에서 재적용 가능하도록 초기화
                self._cd_applied_at_cast[n] = 0
            # 버스트 스킬 대미지: full_burst_start 버프 적용 후 계산
            events.extend(self._fire_pending_burst_dmg(t, bm))
            if self._log is not None:
                self._log.burst_log.append(BurstLogEntry(t=t, event="full_burst 시작", caster=""))
                snap = BuffSnapshot(t=t, buffs_by_char={})
                for n in self.squad_names:
                    entries = []
                    for ab in bm._active:
                        resolved = (
                            bm._resolve_target(ab.effect.get("target", "self"), ab.caster)
                            if ab.target_chars is None
                            else ab.target_chars
                        )
                        if n in resolved:
                            entries.append(BuffEntry(
                                name=ab.effect.get("name", ab.effect.get("stat", "?")),
                                caster=ab.caster,
                                expires_at=ab.expires_at,
                            ))
                    snap.buffs_by_char[n] = entries
                self._log.buff_snapshots.append(snap)

        return events

    def _try_use_stage(
        self, stage: str, t: float, bm: BuffManager, state: dict
    ) -> tuple[list[HitEvent], bool, tuple | None]:
        """
        반환: (events, advanced, reenter_info)
        reenter_info: (caster, stage) or None
        """
        if (
            self._burst_sequence is not None
            and self._burst_count < len(self._burst_sequence)
        ):
            candidates = self._burst_sequence[self._burst_count].get(stage, [])
        else:
            candidates = self.burst_order.get(stage, [])
        if not candidates:
            # 해당 단계 캐릭터가 없으면 이 단계에서 버스트 진행 불가 (영구 블록)
            # 실제 게임: 1단계 캐릭터 없으면 버스트 발동 자체 안 됨
            self._next_action_t = math.inf
            return [], False, None

        for name in candidates:
            if t < self.burst_ready_at.get(name, 0.0) - 1e-9:
                continue
            if bm.is_stunned(name):
                continue
            events = self._cast_burst(name, stage, t, bm, state)

            # burst_stage_override:reenterN 버프 활성 여부 확인
            reenter = self._check_reenter(name, bm)
            if reenter:
                return events, False, (name, reenter)
            return events, True, None

        # 전원 쿨타임 중 → 대기
        earliest = min(self.burst_ready_at.get(n, 0.0) for n in candidates)
        self._next_action_t = max(self._next_action_t, earliest)
        return [], False, None

    def _fire_pending_burst_dmg(self, t: float, bm: BuffManager) -> list[HitEvent]:
        """풀버스트 진입 후 버프 적용 상태에서 미뤄둔 bonus_damage 발동."""
        events = []
        for name, eff, hit_count in self._pending_burst_dmg:
            cs = self.char_states[name]
            buffs = bm.get_buffs(name, "__enemy__", t)
            buffs["is_element_match"] = cs.is_element_match

            coeff = eff["_coeff"]
            # scaling: "stack_count" → 해당 버프의 현재 스택 수만큼 계수 곱산
            if eff.get("scaling") == "stack_count":
                ref = eff.get("scaling_ref", "")
                stack = 0
                for ab in bm._active:
                    if ab.caster == name and ab.effect.get("name") == ref:
                        stack = ab.stack
                        break
                coeff *= stack

            if coeff == 0.0:
                continue

            debug_char = self.config.get("_debug_char")
            in_debug_window = (
                debug_char == name
                and self.config.get("_debug_t0", -1.0) <= t <= self.config.get("_debug_t1", -1.0)
            )
            ht = default_hit_type(
                is_normal_atk=False,
                is_full_burst=True,
                coeff=coeff,
                is_final_atk=True,
                _debug_factors=in_debug_window,
            )
            for _ in range(hit_count):
                if in_debug_window:
                    print(f"t={t:.3f}s  [{eff.get('name', '버스트 스킬')}]  base_atk={cs.base_atk:,}  enemy_def={self.enemy_def:,}")
                res = calc_damage(
                    base_atk=cs.base_atk, buffs=buffs, weapon=cs.weapon,
                    hit_type=ht, enemy_def=self.enemy_def,
                )
                if in_debug_window:
                    print()
                events.append(HitEvent(
                    t=t, caster=name, damage=res["damage"],
                    is_crit=res["is_crit"], hit_tag="bonus_damage",
                    skill_name=eff.get("name", "버스트 스킬"),
                ))
        self._pending_burst_dmg.clear()
        return events

    def _rebuild_burst_order(self, bm_active_stages: dict[str, str]):
        """
        bm_active_stages: 캐릭터명 → 현재 활성 burst_stage_override:N 값 (없으면 기본값).
        burst_order를 현재 유효 버스트 단계 기준으로 재구성한다.
        """
        order: dict[str, list[str]] = {"1": [], "2": [], "3": []}
        for name in self.squad_names:
            if name == self._no_burst_char and self._burst_sequence is None:
                continue
            stage = bm_active_stages.get(name) or self._default_burst_stage.get(name, "")
            if stage == "A":
                for s in ("1", "2", "3"):
                    order[s].append(name)
            elif stage in order:
                order[stage].append(name)
        self.burst_order = order

    def _check_reenter(self, name: str, bm: BuffManager) -> str | None:
        """버스트 사용 후 활성화된 burst_stage_override:reenterN 버프가 있으면 대상 단계 반환."""
        for ab in bm._active:
            if ab.caster != name:
                continue
            stat = ab.effect.get("stat", "")
            if stat.startswith("burst_stage_override:reenter"):
                return stat.split("reenter")[1]
        return None

    def _cast_burst(
        self, name: str, stage: str, t: float, bm: BuffManager, state: dict
    ) -> list[HitEvent]:
        """버스트 스킬 사용. buff notify + instant 처리 + damage 계산."""
        events: list[HitEvent] = []
        state.setdefault("burst_casted", {})[name] = True
        bm.notify("burst_cast", t, name)

        # 개별 버스트 쿨타임 갱신 (burst_cooldown buff 차감 반영)
        cd = self._burst_cd.get(name, 40.0)
        buffs = bm.get_buffs(name, "__enemy__", t)
        cd_buff = buffs.get("burst_cooldown", 0.0)
        self._cd_applied_at_cast[name] = cd_buff
        cd = max(0.0, cd - cd_buff)
        self.burst_ready_at[name] = t + cd

        bm.notify(f"squad_burst_cast:{stage}", t, name)

        is_reenter = self._phase.startswith("reenter:")
        event_label = f"reenter:{stage} 사용" if is_reenter else f"stage:{stage} 사용"
        if self._log is not None:
            self._log.burst_log.append(BurstLogEntry(t=t, event=event_label, caster=name))

        # 3단계 버스트 발동자를 기록 (fullburst_duration 귀속용)
        if stage == "3":
            self._fb_caster = name

        cs = self.char_states[name]

        for eff in _PARSED_SKILLS.get(name, []):
            if eff.get("source") != "스킬3":
                continue

            # instant/damage 타입 모두 bm.notify("burst_cast") 경로에서 처리됨

        return events


# ── instant 핸들러 등록 ────────────────────────────────────────────────────

def _register_instant_handlers(bm, char_states: dict[str, "CharState"], burst_ctrl: "BurstController"):
    """BuffManager에 타임라인 전용 instant stat 핸들러를 등록한다."""

    def _resolve_targets(eff: dict, caster: str) -> list[str]:
        """target 필드를 캐릭터명 목록으로 변환 (아군 only)."""
        target = eff.get("target", "self")
        if target == "self":
            return [caster]
        if target in ("all_allies",):
            return list(char_states.keys())
        if isinstance(target, list):
            result = []
            for t in target:
                result.extend(_resolve_targets({"target": t}, caster))
            return result
        return [caster]

    def _effective_max_ammo(cs: "CharState", t: float) -> int:
        buffs = bm.get_buffs(cs.name, "__enemy__", t)
        ammo_pct = buffs.get("max_ammo_pct", 0.0) / 100.0
        return round(cs.weapon["max_ammo"] * (1.0 + ammo_pct))

    def handle_ammo_charge_pct(eff, caster, t, val):
        target_names = _resolve_targets(eff, caster)
        for name in target_names:
            cs = char_states.get(name)
            if cs is None:
                continue
            max_ammo = _effective_max_ammo(cs, t)
            charge = round(max_ammo * (val / 100.0))
            cs.ammo = min(cs.ammo + charge, max_ammo)
        # 이 instant 효과 발동을 이벤트로 전파 (예: 급조 탄환 → 임시 개조 트리거)
        eff_name = eff.get("name", "")
        if eff_name:
            bm.notify(f"event:{eff_name}", t, caster)

    def handle_ammo_charge_flat(eff, caster, t, val):
        target_names = _resolve_targets(eff, caster)
        for name in target_names:
            cs = char_states.get(name)
            if cs is None:
                continue
            max_ammo = _effective_max_ammo(cs, t)
            cs.ammo = min(cs.ammo + int(val), max_ammo)

    def handle_burst_cooldown_reduce(eff, caster, t, val):
        target_names = _resolve_targets(eff, caster)
        for name in target_names:
            burst_ctrl.burst_ready_at[name] = max(t, burst_ctrl.burst_ready_at.get(name, 0.0) - val)

    def handle_heal_hp_pct(eff, caster, t, val):
        target_names = _resolve_targets(eff, caster)
        hp = bm.state["hp"]
        for name in target_names:
            base_hp = bm.state["base_stats"].get(name, {}).get("hp", 0.0)
            max_hp = bm.effective_max_hp(name)
            heal_base = max_hp if eff.get("scaling") == "max_hp" else base_hp
            hp[name] = min(hp.get(name, base_hp) + heal_base * val / 100.0, max_hp)
            bm.sync_hp(name)
            bm.notify("event:heal_received", t, name)

    def handle_current_hp_reduce(eff, caster, t, val):
        target_names = _resolve_targets(eff, caster)
        hp = bm.state["hp"]
        for name in target_names:
            base_hp = bm.state["base_stats"].get(name, {}).get("hp", 0.0)
            hp[name] = max(hp.get(name, base_hp) - base_hp * val / 100.0, 0.0)
            bm.sync_hp(name)

    bm.register_instant_handler("ammo_charge_pct", handle_ammo_charge_pct)
    bm.register_instant_handler("ammo_charge_flat", handle_ammo_charge_flat)
    bm.register_instant_handler("burst_cooldown_reduce", handle_burst_cooldown_reduce)
    bm.register_instant_handler("heal_hp_pct", handle_heal_hp_pct)
    bm.register_instant_handler("current_hp_reduce", handle_current_hp_reduce)


# ── simulate ──────────────────────────────────────────────────────────────

def simulate(
    squad: list[dict],
    config: dict | None = None,
    enemy: dict | None = None,
    verbose: bool = False,
) -> SimResult:
    """
    스쿼드 전투 시뮬레이션 (1~5인).

    Parameters
    ----------
    squad   : 캐릭터 인스턴스 목록 (base_stat.py 구조 + skill_level + burst_regen_time)
    config : 시뮬레이션 설정 (DEFAULT_CONFIG 기반 오버라이드)
    enemy  : 적 정보 (DEFAULT_ENEMY 기반 오버라이드)
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    enm = {**DEFAULT_ENEMY, **(enemy or {})}
    duration = cfg["duration"]

    squad = [{**DEFAULT_CHAR, **c} for c in squad]

    base_stats: dict[str, dict] = {c["name"]: calc_base_stats(c) for c in squad}

    state: dict = {
        "full_burst":   False,
        "burst_casted": {c["name"]: False for c in squad},
        "hp_pct":       {c["name"]: 100.0 for c in squad},
        "hp":           {c["name"]: float(base_stats[c["name"]]["hp"]) for c in squad},
        "base_stats":   base_stats,
        "stacks":       {c["name"]: {} for c in squad},
        "gauges":       {c["name"]: {} for c in squad},
        "burst_stages": {c["name"]: _NIKKE[c["name"]]["burst_stage"] for c in squad},
        "enemy":        enm,
    }

    enemy_code = enm.get("code", "")
    element_match: dict[str, bool] = {
        c["name"]: is_element_match(_NIKKE[c["name"]].get("element_code", ""), enemy_code)
        for c in squad
    }

    char_states: dict[str, CharState] = {
        c["name"]: CharState(c, float(base_stats[c["name"]]["atk"]), element_match[c["name"]])
        for c in squad
    }

    bm = BuffManager(squad, state)
    burst_ctrl = BurstController(squad, cfg, char_states, enm)
    _register_instant_handlers(bm, char_states, burst_ctrl)

    sim_log = SimLog() if verbose else None
    burst_ctrl._log = sim_log
    for cs in char_states.values():
        cs._sim_log = sim_log

    result = SimResult(duration=duration, log=sim_log)
    result.char_total = {c["name"]: 0 for c in squad}

    # damage 핸들러: bm.tick()/_activate()에서 호출되는 damage 효과를 처리
    _dot_events: list[HitEvent] = []

    def _handle_damage_eff(eff: dict, caster: str, t: float):
        if eff.get("target") == "all_projectiles":
            return
        cs = char_states.get(caster)
        if cs is None:
            return
        skill_lv = _get_skill_lv(cs.char, eff)
        if "values" in eff:
            vals = eff["values"]
            coeff = float(vals.get(skill_lv, vals.get("10", 0.0)))
        elif "fixed_value" in eff:
            coeff = float(eff["fixed_value"])
        else:
            coeff = 0.0

        # scaling:stack_count + dot_damage → 틱당 계수에 현재 스택 수를 곱함
        # (hit_count 방식으로 처리하는 일반 damage는 아래 hit_count 블록에서 별도 처리)
        if eff.get("scaling") == "stack_count" and eff.get("stat", "").startswith("dot_damage"):
            ref = eff.get("scaling_ref", "")
            scale = 0
            # 자신의 _active 엔트리에 캡처된 stack 값을 먼저 확인
            # (scaling_ref 버프가 이미 제거됐을 경우 대비)
            eff_name = eff.get("name", "")
            for ab in bm._active:
                if ab.caster == caster and ab.effect.get("name") == eff_name:
                    scale = ab.stack
                    break
            if not scale and ref:
                # 자신의 stack이 기본값(1)이 아닐 수도 있으므로, 참조 버프가 살아있으면 그걸 씀
                gauge_val = bm.state.get("gauges", {}).get(caster, {}).get(ref, 0.0)
                if gauge_val:
                    scale = int(gauge_val)
                else:
                    for ab in bm._active:
                        if ab.caster == caster and ab.effect.get("name") == ref:
                            scale = ab.stack
                            break
            coeff *= scale

        if coeff == 0.0:
            return

        # dmg_scale_mag_pct: target_effect가 이 효과를 참조하는 버프의 배율 적용
        eff_name = eff.get("name", "")
        if eff_name:
            for ab in bm._active:
                if (ab.effect.get("stat") == "dmg_scale_mag_pct"
                        and ab.effect.get("target_effect") == eff_name
                        and ab.caster == caster
                        and t < ab.expires_at):
                    mag = bm._get_value(ab.effect, ab, caster)
                    if mag is not None:
                        coeff *= (1.0 + mag / 100.0)

        eff_with_coeff = {**eff, "_coeff": coeff}

        # bonus_damage + burst_cast → 풀버스트 시점으로 pending
        # same_target:X 여부와 무관하게 모두 pending (풀버스트 버프 적용 후 계산)
        stat = eff.get("stat", "")
        timings = eff.get("trigger", {}).get("timing", [])
        target_field = eff.get("target", "")
        if stat == "bonus_damage" and "burst_cast" in timings:
            # same_target:X → 짝이 되는 sequential 효과의 hit_count만큼 반복 발동
            hit_count = 1
            if isinstance(target_field, str) and target_field.startswith("same_target:"):
                ref_name = target_field[len("same_target:"):]
                for ref_eff in _PARSED_SKILLS.get(caster, []):
                    if ref_eff.get("name") != ref_name:
                        continue
                    ref_stat = ref_eff.get("stat", "")
                    ref_parts = ref_stat.split(":")
                    if len(ref_parts) > 1 and ref_parts[1].lstrip("-").isdigit():
                        hit_count = int(ref_parts[1])
                    break
            burst_ctrl._pending_burst_dmg.append((caster, eff_with_coeff, hit_count))
            return

        # damage_formula: "normal_attack" → is_normal_atk=True で일반 공격 버프 적용
        is_normal = eff.get("damage_formula") == "normal_attack"
        buffs = bm.get_buffs(caster, "__enemy__", t)
        buffs["is_element_match"] = cs.is_element_match
        is_full_burst = bm.state.get("full_burst", False)
        stat = eff.get("stat", "damage")
        stat_parts = stat.split(":")
        base_stat = stat_parts[0]
        # hit_count 결정
        # - "damage" + hit_count_gauge_ref → 게이지 값만큼 히트
        # - "sequential_damage:N" → N회 (순차 공격)
        # - "sequential_damage:이름" → 게이지/스택 수만큼 히트 (scaling 값 무관)
        # - "<any_damage_stat>:N" (N이 정수) → 1트리거당 N회 발사 (예: bonus_damage:5)
        # - "damage" + scaling=stack_count → scaling_ref 게이지/스택 수만큼 히트
        hit_count = 1
        gauge_ref = eff.get("hit_count_gauge_ref")
        if gauge_ref:
            hit_count = int(bm.state.get("gauges", {}).get(caster, {}).get(gauge_ref, 0))
        elif len(stat_parts) > 1 and stat_parts[1].lstrip("-").isdigit():
            hit_count = int(stat_parts[1])
        elif len(stat_parts) > 1 and base_stat == "sequential_damage":
            # sequential_damage:이름 형태 — scaling 값 무관하게 게이지/스택 수 읽기
            raw = stat_parts[1]
            gauge_val = bm.state.get("gauges", {}).get(caster, {}).get(raw, 0)
            if gauge_val:
                hit_count = int(gauge_val)
            else:
                for ab in bm._active:
                    if ab.caster == caster and ab.effect.get("name") == raw:
                        hit_count = ab.stack
                        break
        elif eff.get("scaling") == "stack_count":
            # damage stat + scaling:stack_count → scaling_ref 게이지/스택 수만큼 발사
            ref = eff.get("scaling_ref", "")
            if ref:
                gauge_val = bm.state.get("gauges", {}).get(caster, {}).get(ref, 0)
                if gauge_val:
                    hit_count = int(gauge_val)
                else:
                    for ab in bm._active:
                        if ab.caster == caster and ab.effect.get("name") == ref:
                            hit_count = ab.stack
                            break
            elif len(stat_parts) > 1:
                raw = stat_parts[1]
                gauge_val = bm.state.get("gauges", {}).get(caster, {}).get(raw, 0)
                if gauge_val:
                    hit_count = int(gauge_val)
                else:
                    for ab in bm._active:
                        if ab.caster == caster and ab.effect.get("name") == raw:
                            hit_count = ab.stack
                            break
        weapon_type = cs.weapon.get("weapon_type", "")
        ht = default_hit_type(
            is_normal_atk=is_normal,
            is_full_burst=is_full_burst,
            is_core=(enm.get("has_core", False) and is_normal),
            is_optimal_range=(weapon_type in enm.get("optimal_range_weapons", []) and is_normal),
            is_burst_damage=(base_stat == "burst_damage"),
            is_pierce_damage=(base_stat == "pierce_damage"),
            is_armor_break_damage=(base_stat == "armor_break_damage"),
            is_dot=(base_stat == "dot_damage"),
            is_projectile_explosion=(base_stat == "projectile_explosion_damage" or (is_normal and weapon_type == "RL")),
            is_projectile_attachment=(base_stat == "projectile_attachment_damage"),
            is_sequential=(base_stat == "sequential_damage"),
            is_split=(base_stat == "split_damage"),
            coeff=eff_with_coeff["_coeff"],
            is_final_atk=True,
        )
        debug_char = cfg.get("_debug_char")
        in_debug_window = (
            debug_char == caster
            and cfg.get("_debug_t0", -1.0) <= t <= cfg.get("_debug_t1", -1.0)
        )
        ht["_debug_factors"] = in_debug_window

        for _ in range(hit_count):
            if in_debug_window:
                print(f"t={t:.3f}s  [{eff.get('name', stat)}]  base_atk={cs.base_atk:,}  enemy_def={enm.get('def', 31784):,}")
            res = calc_damage(
                base_atk=cs.base_atk, buffs=buffs, weapon=cs.weapon,
                hit_type=ht, enemy_def=enm.get("def", 31784),
            )
            if in_debug_window:
                print()
            hit_tag = "normal_skill" if is_normal else base_stat
            _dot_events.append(HitEvent(
                t=t, caster=caster, damage=res["damage"],
                is_crit=res["is_crit"], hit_tag=hit_tag,
                skill_name=eff.get("name", stat),
            ))
            # hit_count:[스킬명] 이벤트 — named damage effect 명중마다 발생
            if eff_name:
                bm.notify(f"hit_count:{eff_name}", t, caster)

        # weapon_hit:name 이벤트 발생 (hit_count:N 트리거로 발사된 발사체 명중 시)
        if eff_name:
            bm.notify(f"weapon_hit:{eff_name}", t, caster)

    bm.register_damage_handler(_handle_damage_eff)

    if sim_log is not None:
        def _buff_event_cb(kind: str, name: str, caster: str, target: str, t: float, expires_at: float, value: float | None = None, stat: str | None = None):
            sim_log.buff_events.append(BuffEvent(
                t=t, kind=kind, name=name, caster=caster, target=target, expires_at=expires_at, value=value, stat=stat,
            ))
        bm.register_buff_event_handler(_buff_event_cb)

        def _instant_event_cb(name: str, caster: str, target: str, t: float, stat: str, value: float | None):
            sim_log.instant_events.append(InstantEvent(
                t=t, name=name, caster=caster, target=target, stat=stat, value=value,
            ))
        bm.register_instant_event_handler(_instant_event_cb)

    def _apply_lifesteal(ev: HitEvent, bm: BuffManager, base_stats: dict, t: float):
        buffs = bm.get_buffs(ev.caster, "__enemy__", t)
        ls = buffs.get("lifesteal_pct", 0.0)
        if ls <= 0.0:
            return
        heal = ev.damage * ls / 100.0
        hp = bm.state["hp"]
        bs = base_stats.get(ev.caster, {})
        base_hp = float(bs.get("hp", 0.0))
        max_hp = bm.effective_max_hp(ev.caster)
        hp[ev.caster] = min(hp.get(ev.caster, base_hp) + heal, max_hp)
        bm.sync_hp(ev.caster)
        bm.notify("event:heal_received", t, ev.caster)

    bm.battle_start(0.0)

    t = 0.0
    while t <= duration:
        bm.tick(t)

        for ev in _dot_events:
            result.hits.append(ev)
            result.char_total[ev.caster] += ev.damage
            _apply_lifesteal(ev, bm, base_stats, t)
        _dot_events.clear()

        for ev in burst_ctrl.tick(t, bm, state):
            result.hits.append(ev)
            result.char_total[ev.caster] += ev.damage
            _apply_lifesteal(ev, bm, base_stats, t)

        for char in squad:
            name = char["name"]
            for ev in char_states[name].tick(t, bm, enm, cfg):
                result.hits.append(ev)
                result.char_total[name] += ev.damage
                _apply_lifesteal(ev, bm, base_stats, t)

        t += DT

    result.squad_total = sum(result.char_total.values())
    result.hits.sort(key=lambda e: e.t)

    return result


# ── 빠른 테스트 ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    def make_char(name):
        return {
            "name": name,
            "level": 200, "breakthrough": 3, "core_enhancement": 7,
            "affinity": 30, "skill_levels": {"1": 10, "2": 10, "3": 10}, "burst_regen_time": 2.0,
            "equipment": {p: {"level": 5, "skills": []} for p in ["머리","몸통","팔","다리"]},
            "cube": {"name": "재장", "level": 5},
            "console": {"common_level": 10, "class_level": 10, "company_level": 10},
            "collection_stage": "SR15",
        }

    squad = [make_char(n) for n in
            ["아니스 : 스타", "리틀 머메이드", "크라운", "라피 : 레드 후드", "리버렐리오"]]

    result = simulate(squad, verbose=True)
    print(result.summary())
    print(f"\n히트 수: {len(result.hits)}")
    print()
    print(result.hit_summary())
    print()
    if result.log:
        print(result.log.burst_summary())
        print()
        print(result.log.buff_summary())
