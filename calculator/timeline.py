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
import random
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
    AmmoLogEntry,
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

_ACCURACY_DATA: dict = _MECHANICS.get("accuracy", {})
_MODEL_N: float      = float(_ACCURACY_DATA.get("_model_n", 2.55))

DT = 1 / 60  # 시뮬레이션 스텝 (초)

# ── 컨트롤 상수 (context/CONTROL.md) ───────────────────────────────────────
# SR/RL의 발사 딜레이 0.38초는 두 조각이다 — 사격 전 0.22초 + 사격 후 0.16초.
# 사격 전 0.22초는 누름(조준) 구간 그 자체라 지울 수 없고, 컨트롤로 지우는 건 사격 후 0.16초다.
# 얼마나 지우는지가 실력 요소이며, 그 실력은 `rate`(초당 발사) 하나로 표현한다 —
# rate가 낮다는 건 사격 후 딜레이를 덜 지웠다는 뜻이다.
_TAP_MIN_HOLD          = 0.22  # 사격 전 딜레이 = 최소 누름 시간(초). 더 짧게 누르면 발사 안 됨
_TAP_CUTTABLE_DELAY    = 0.16  # 사격 후 딜레이(초). 컨트롤로 지울 수 있는 몫
_TAP_RELEASE_DEFAULT   = 0.03  # 톡톡이 떼는 시간 기본값(초). 하드웨어 하한 0.02
_RELOAD_LEAD_DEFAULT   = 0.3   # 장전컨 A: 풀버스트 종료 몇 초 전에 재장전을 시작할지
_RELOAD_MARGIN_DEFAULT = 0.1   # 장전컨 B: 풀버스트 시작 몇 초 뒤에 재장전이 끝나게 할지

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
    "control": {},  # 컨트롤(톡톡이·장전컨). 스키마·의미는 context/CONTROL.md
}

DEFAULT_CONFIG: dict = {
    "duration":           180.0,  # 시뮬레이션 시간(초) — 실제 니케 전투 3분
    "burst_switch_delay":  0.1,   # 버스트 단계 전환 딜레이(초)
    "burst_reenter_delay": 0.5,   # reenter 딜레이(초)
    "max_burst_count":    None,   # 최대 풀버스트 횟수 (None = 무제한)
    "burst_sequence":     None,   # 풀버스트별 단계 사용 순서 list[dict[str, list[str]]] (None = 자동)
    "first_burst_time":    3.0,   # 첫 버스트 최소 시작 시간(초)
    "allow_unparsed":     False,  # True면 스킬 미파싱 캐릭터를 스킬 0개로 돌린다 (파싱 전 신캐 전용)
}

DEFAULT_ENEMY: dict = {
    "def":                  31784,
    "code":                 None,
    "core_px":              0,    # 코어 직경(px). 0이면 코어 없음, >0이면 코어히트율 확률 계산
    "has_parts":            False,# 파괴 가능 파츠 보유 보스. part_hit_count / part_dmg_pct의 전제
    "optimal_range_weapons": [],  # 적정거리 적용 무기군 목록 e.g. ["SG", "SMG"]
}


def _pick(key: str, *sources: dict | None, default=None):
    """발사 메카닉 값의 3계층 해석. 앞 소스가 이긴다.

    ① weapon_delays.json `_exceptions[캐릭터]` — 수동 실측 (스크래퍼가 안 건드림)
    ② parsed_nikke.json[캐릭터]              — 스크래퍼가 CDN에서 수집
    ③ weapon_mechanics.json 무기군 기본값

    `or`가 아니라 `is not None` 검사인 이유: 0을 유효값으로 살려야 한다.
    """
    for src in sources:
        if src is not None and src.get(key) is not None:
            return src[key]
    return default


def _core_hit_prob(weapon_type: str, accuracy_pct: float, core_px: float) -> float:
    """명중률·코어 크기로부터 코어히트 확률 반환 (power 모델 P = min(1, (r_c/R)^n)).

    D  = base_diameter - acc_slope * accuracy_pct  (탄착군 직경, px)
    R  = D / 2                                     (탄착군 반경)
    r_c = core_px / 2                              (코어 반경)
    """
    spec = _ACCURACY_DATA.get(weapon_type, {})
    D = max(spec.get("base_diameter", 10) - spec.get("acc_slope", 0) * accuracy_pct, 1.0)
    R = D / 2.0
    r_c = core_px / 2.0
    return min(1.0, (r_c / R) ** _MODEL_N)


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

        # MG 예열 (식는 속도가 있어 미사격 시 점진 냉각 — int 아닌 float)
        self.warmup_shots: float = 0.0
        self.last_fire_t: float = -999.0
        self._last_inter: float = 0.0  # 직전 발사가 예약한 간격 (_cool_warmup 판정 기준)

        # delay 값: weapon_delays.json 기준
        _delay_exc = _DELAYS["_exceptions"].get(self.name, {})
        _delay_wt  = _DELAYS["_defaults_by_weapon_type"].get(self.weapon_type, {})
        self.post_reload_delay: float = _delay_exc.get("post_reload_delay", _delay_wt.get("post_reload_delay", 0.0))
        # 엄폐 니케: 재장 ≥100%일 때 post_fire_delay 중 자동재장전 (장탄 유지)
        self.cover_during_delay: bool = _delay_exc.get("cover_during_delay", False)
        self._pending_auto_reload: bool = False

        # 발사 메카닉 3계층 해석 (_pick 참조). 무기군 기본값의 MG 곡선은 fire_rate_min
        # 키를 쓰므로, 캐릭터별 fire_rate가 없을 때만 거기서 시작 연사를 가져온다.
        self.fire_rate: float = float(_pick(
            "fire_rate", _delay_exc, weapon_data, mech,
            default=mech.get("fire_rate_min", 1.0)))
        self.fire_rate_max: float | None = _pick(
            "fire_rate_max", _delay_exc, weapon_data, mech)
        _fr_step = _pick("fire_rate_change_pershot", _delay_exc, weapon_data)
        if self.fire_rate_max is not None and _fr_step:
            # 캐릭터별 값이 있으면 예열 발수를 곡선에서 직접 유도한다
            self.warmup_bullets: float = (self.fire_rate_max - self.fire_rate) / _fr_step
        else:
            self.warmup_bullets = float(mech.get("warmup_bullets", 1.0))

        # 총구 수: 1회 발사에 동시에 나가는 탄 묶음 수. 실제 히트 수 = pellets × muzzles.
        # CDN damage(= 스킬 텍스트의 대미지 표기)는 총구당 값이라 총량이 총구 수만큼 늘어난다.
        self.muzzles: int = int(_pick("muzzles", _delay_exc, weapon_data, mech, default=1))

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

        # SG (계수를 나누는 단위. 히트 수는 self.muzzles를 곱한 값)
        self.pellets: int = int(_pick("pellets", _delay_exc, weapon_data, mech, default=1))

        # 클립 무기 여부 (재장전 속도가 명시된 수치의 3배인 SG/RL)
        _clip_chars = _MECHANICS.get("clip_characters", {}).get(self.weapon_type, [])
        self.is_clip: bool = self.name in _clip_chars

        self._in_weapon_change: bool = False
        # 이 재장전이 무기 변경 모드 안에서 시작됐는가 (모드 탄창 vs 원래 무기 탄창)
        self._reload_in_weapon_change: bool = False
        self._wc_shots: int = 0             # 현재 무기 변경 세션에서 실제 발사한 발수
        self._wc_new_session: bool = False  # 이번 tick이 세션 첫 진입인가

        # 모드 지정 플래그: 수동 재장전으로 진입하는 weapon_change 모드를 쓰는가.
        # 진입에 필요한 재장전만 삽입하고 진입 후에는 삽입하지 않아 모드를 유지한다.
        self.weapon_mode_swap: bool = bool(char.get("weapon_mode_swap", False))

        # ── 컨트롤 (유저 조작 재현). 정본: context/CONTROL.md ─────────────
        control = char.get("control") or {}

        # 톡톡이: 차지를 끝까지 하지 않고 짧게 눌렀다 떼기를 반복 (차지형 전용).
        # hold(누름) + release(뗌)로 주기를 만들고, hold가 유효 차지 시간 이상이면
        # 풀차지 샷이 된다 — 차지속도 버프로 차지가 짧아진 경우가 자동 처리된다.
        self.tap_fire: bool = False
        self._tap_hold: float = 0.0     # 누름 시간 = 사격 전 딜레이 + 차지
        self._tap_charge: float = 0.0   # 그중 실제로 차지되는 시간
        self._tap_release: float = 0.0
        self._tap_post: float = 0.0
        tap = control.get("tap_fire")
        if tap and self.fire_mode == "charge":
            rate = float(tap["rate"])
            self._tap_release = float(tap.get("release", _TAP_RELEASE_DEFAULT))
            # 목표 주기를 [사격 전 딜레이 + 차지 + 떼기 + 남은 사격 후 딜레이]로 분해한다.
            # 최소 구성(사격 전 0.22 + 떼기)보다 여유가 있으면, 그 여유는 **먼저 "덜 지운
            # 사격 후 딜레이"**로 간다 — rate를 낮게 잡는다는 게 곧 딜레이를 덜 지운다는 뜻이다.
            # 0.16초를 다 채우고도 남는 만큼만 실제로 차지된다(느린 톡톡이).
            slack = max(0.0, 1.0 / rate - _TAP_MIN_HOLD - self._tap_release)
            self._tap_post = min(_TAP_CUTTABLE_DELAY, slack)
            # 사격 전 딜레이 0.22초는 차지가 시작되기 전 구간이라 차지에 들어가지 않는다.
            # 그래서 완벽한 0.22 간격 톡톡이는 차지가 0 — 차지 배율은 언제나 100%다.
            self._tap_charge = max(0.0, slack - _TAP_CUTTABLE_DELAY)
            self._tap_hold = _TAP_MIN_HOLD + self._tap_charge
            self.tap_fire = True

        # 장전컨: 탄이 남았는데 일부러 재장전해 재장전을 유리한 구간에 밀어 넣는다.
        rl = control.get("reload") or {}
        self.reload_policy: str = rl.get("policy", "")
        self.reload_lead: float = float(rl.get("lead", _RELOAD_LEAD_DEFAULT))
        self.reload_margin: float = float(rl.get("margin", _RELOAD_MARGIN_DEFAULT))
        # 이미 처리한 앵커 시각 (사이클당 1회 보장)
        self._reload_ctrl_anchor: float = -1.0

    def tick(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        # 기절 중: 일반공격 불가
        if bm.is_stunned(self.name):
            return []

        # weapon_change 활성 시: 임시 무기 교체 후 해당 무기의 발사 루프로 처리
        wc_eff = bm.get_weapon_change(self.name)
        if wc_eff is not None:
            if not self._in_weapon_change:
                self._in_weapon_change = True
                self._wc_shots = 0
                self._wc_new_session = True
            # 자기 탄창을 관리하는 모드(지속형 + 유한 장탄)만 모드 안에서 재장전을 완료시킨다.
            # 처리하지 않으면 장탄 소진 후 재장전이 끝나지 않아 발사가 영원히 멈춘다.
            # 시한부 모드(duration 있음)나 무한 장탄 모드는 기존 동작을 유지한다 —
            # 그쪽의 재장전은 원래 무기의 것이고, 모드가 끝난 뒤 정상 경로에서 처리된다.
            if (self.reloading_until > 0 and self._reload_in_weapon_change
                    and wc_eff.get("max_ammo", -1) != -1
                    and wc_eff.get("duration") is None
                    and wc_eff.get("duration_bullets") is None):
                if t < self.reloading_until:
                    return []
                self._finish_reload(t, bm)
            return self._tick_weapon_change(t, bm, enemy, cfg, wc_eff)

        # weapon_change 만료 직후: next_fire_time 리셋으로 과거 발사 빚 방지
        if self._in_weapon_change:
            self._in_weapon_change = False
            self.next_fire_time = t

        # 모드 지정 플래그: 진입 조건이 충족된 순간 수동 재장전을 삽입해 모드로 들어간다.
        # (실전의 수동컨을 재현. 자연 재장전만으로는 진입 조건이 성립하지 않는 모드가 있다)
        if (self.weapon_mode_swap
                and self.reloading_until <= 0
                and self._post_reload_end_t <= 0
                and bm.manual_swap_ready(self.name, t)):
            self._start_reload(t, bm)
            return []

        # 장전컨: 탄이 남았어도 유리한 구간에 재장전을 밀어 넣는다
        if self._apply_reload_control(t, bm):
            return []

        # 재장전 완료 체크
        if self.reloading_until > 0:
            if t < self.reloading_until:
                return []
            self._finish_reload(t, bm)
            # 재장전 완료가 발생시킨 event:full_reload로 무기 변경 모드에 진입했을 수 있다.
            # 같은 프레임에 원래 무기로 한 발 쏘고 넘어가지 않도록 다시 확인한다.
            wc_eff = bm.get_weapon_change(self.name)
            if wc_eff is not None:
                self._in_weapon_change = True
                return self._tick_weapon_change(t, bm, enemy, cfg, wc_eff)

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
        if self.fire_mode == "auto_warmup":
            self._cool_warmup(t, bm)
        while t >= self.next_fire_time:
            if self.ammo <= 0:
                self._start_reload(t, bm)
                break
            fire_rate = self._current_fire_rate(bm, t)
            events.extend(self._fire(t, bm, enemy, cfg))
            inter = 1.0 / fire_rate
            self.next_fire_time += inter
            if self.fire_mode == "auto_warmup":
                self.last_fire_t = t
                self._last_inter = inter
            if self.next_fire_time <= t:
                # 프레임당 1발 상한. 게임이 60fps이므로 60발/초를 넘는 연사는
                # 프레임에 갇혀 실효 60/s가 된다 (MG 실측 60/s ← CDN 표기 70/s).
                # next_fire_time을 t로 당겨 밀린 빚을 남기지 않는다 — 빚을 남기면
                # 나중에 연사가 떨어질 때 몰아 쏘는 보정이 생긴다.
                self.next_fire_time = t
                break

        return events

    def _cool_warmup(self, t: float, bm: BuffManager):
        # MG 예열은 식는 속도가 있다. 재장전·기절 등으로 사격이 멈춘 구간만큼
        # 시간에 비례해 점진 냉각하고, 정상 연사의 inter-shot 간격은 냉각하지 않는다.
        if self.warmup_shots <= 0.0:
            return
        idle = t - self.last_fire_t
        if idle <= 0.0:
            return
        # 판정 기준은 **직전 발사가 실제로 예약한** 간격이다. 현재 연사 속도로 다시
        # 계산하면 안 된다 — 예열 중에는 매 발 속도가 올라 방금 지나온 정상 간격이
        # 항상 임계를 넘어버리고, 예열이 매 발 리셋돼 영원히 안 오른다.
        inter = self._last_inter or 1.0 / max(self._current_fire_rate(bm, t), 0.01)
        if idle <= inter * 1.5:  # 예약된 연사 대기 — 실제 정지가 아님
            return
        cool_rate = self.warmup_bullets / self.mech.get("cooldown_time", 1.0)
        self.warmup_shots = max(0.0, self.warmup_shots - cool_rate * idle)
        self.last_fire_t = t  # 다음 프레임 중복 차감 방지

    def _current_fire_rate(self, bm: BuffManager, t: float) -> float:
        if self.fire_mode == "auto_warmup":
            fr_min = self.fire_rate
            fr_max = self.fire_rate_max if self.fire_rate_max is not None else fr_min
            warmup = self.warmup_bullets
            base = fr_min + (fr_max - fr_min) * min(self.warmup_shots, warmup) / warmup
        else:
            base = self.fire_rate
        speed_pct = bm.get_buffs(self.name, "__enemy__", t).get("attack_speed_pct", 0.0)
        return base * max(0.01, 1.0 + speed_pct / 100.0)

    def _fire(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        events = []
        is_last = (self.ammo == 1)
        if is_last:
            bm.notify("last_bullet_fire", t, self.name)

        if self.fire_mode == "auto_warmup":
            if self.warmup_shots < self.warmup_bullets:
                wsp = bm.get_buffs(self.name, "__enemy__", t).get("mg_warmup_speed_pct", 0.0)
                incr = max(0.0, 1.0 + wsp / 100.0)
                self.warmup_shots = min(self.warmup_shots + incr, self.warmup_bullets)

        if self._in_weapon_change:
            # weapon_change의 duration_bullets 카운트. ammo 감소량으로 세면
            # `ammo_charge_pct` 같은 장탄 조작 효과에 오염되므로 발사 시점에 직접 센다.
            self._wc_shots += 1

        self.ammo -= 1
        if self._sim_log is not None:
            self._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=self.name, ammo=self.ammo))
        bm.notify("squad_ammo_consume", t, self.name)
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        buffs["is_element_match"] = self.is_element_match
        is_optimal = self.weapon_type in enemy.get("optimal_range_weapons", [])

        # 코어히트 확률: core_px>0이면 명중률·탄착군·코어 크기로 계산, 0이면 코어 없음
        if enemy.get("core_px", 0) > 0:
            P_core = _core_hit_prob(
                self.weapon_type,
                buffs.get("accuracy_pct", 0.0),
                enemy.get("core_px", 50),
            )
        else:
            P_core = 0.0

        is_full_burst = bm.state.get("full_burst", False)
        debug_char = cfg.get("_debug_char")
        in_debug_window = (
            debug_char == self.name
            and cfg.get("_debug_t0", -1.0) <= t <= cfg.get("_debug_t1", -1.0)
        )

        # 실효 펠릿 수: pellet_count_fixed > 0이면 절대값 고정, 아니면 기본값 + 증가량.
        # 펠릿은 **계수를 나누는 단위**이고, 총구 수는 그 묶음이 몇 벌 나가는지다.
        # 대미지 표기(damage_coeff)가 총구당 값이라 총구가 2개면 총량도 2배가 된다.
        # (버프는 "펠릿 개수"를 말하므로 총구가 아니라 펠릿 쪽에 더한다)
        pellet_fixed = buffs.get("pellet_count_fixed", 0.0)
        if pellet_fixed > 0:
            split = max(1, int(round(pellet_fixed)))
        else:
            split = max(1, self.pellets + int(round(buffs.get("pellet_count", 0.0))))
        hit_count = split * self.muzzles

        for i in range(hit_count):
            is_core = random.random() < P_core  # 히트마다 독립 샘플링 (SG: 10회, 기타: 1회)
            coeff = (self.weapon["damage_coeff"] / split) if split > 1 else None
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
            tag = (f"core:pellet:{i}" if is_core else f"pellet:{i}") if hit_count > 1 \
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

    def _effective_charge_time(self, bm: BuffManager, t: float) -> float:
        """현재 버프를 반영한 유효 차지 시간(초)."""
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        if buffs.get("charge_time_fixed"):
            return self._fixed_charge_time(bm)
        cs_pct = buffs.get("charge_speed_pct", 0.0) / 100.0
        return self.charge_time_base * max(0.0, 1.0 - cs_pct)

    def _tick_charge(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        events = []

        if self._charge_phase == "ready":
            if self.ammo <= 0:
                self._start_reload(t, bm)
                return events
            self._charge_start_t = t
            self._charge_phase = "charging"
            bm.state.setdefault("charging", {})[self.name] = True
            bm._invalidate_buffs_cache()
            if self.ammo == 1:
                bm.notify("last_bullet_fire", t, self.name)

        if self._charge_phase == "charging":
            if self.tap_fire:
                # 톡톡이: 누르는 시간이 고정이고, 그중 사격 전 딜레이를 뺀 만큼만 차지된다.
                # 차지속도 버프로 유효 차지 시간이 그 아래로 내려가면 풀차지 샷이 된다.
                self._charge_end_t = self._charge_start_t + self._tap_hold
                if t < self._charge_end_t:
                    return events
                is_full = self._tap_charge >= self._effective_charge_time(bm, t)
            else:
                self._charge_end_t = self._charge_start_t + self._effective_charge_time(bm, t)
                if t < self._charge_end_t:
                    return events
                is_full = True
            events.extend(self._charge_fire(t, bm, enemy, cfg, is_full))

        elif self._charge_phase == "post_delay" and t >= self._post_delay_end_t:
            if self._pending_auto_reload:
                self._pending_auto_reload = False
                self._auto_reload(t, bm)
            self._charge_phase = "ready"
            return self._tick_charge(t, bm, enemy, cfg)

        return events

    def _charge_fire(
        self, t: float, bm: BuffManager, enemy: dict, cfg: dict, is_full: bool
    ) -> list[HitEvent]:
        """차지 무기 1발 발사 처리. `is_full=False`면 논차지 샷(톡톡이)."""
        events = []
        is_optimal = self.weapon_type in enemy.get("optimal_range_weapons", [])
        if is_full:
            bm.notify("full_charge", t, self.name)
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        buffs["is_element_match"] = self.is_element_match
        if enemy.get("core_px", 0) > 0:
            P_core = _core_hit_prob(
                self.weapon_type,
                buffs.get("accuracy_pct", 0.0),
                enemy.get("core_px", 50),
            )
        else:
            P_core = 0.0
        is_core = random.random() < P_core

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
            is_full_charge=is_full,
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
        if is_full:
            tag = "core+full_charge_hit" if is_core else "full_charge_hit"
        else:
            # 논차지 샷은 일반 발사와 같은 취급 (차지 배율 없음)
            tag = "core" if is_core else "normal"
        events.append(HitEvent(t=t, caster=self.name, damage=res["damage"],
                               is_crit=res["is_crit"], hit_tag=tag))
        is_last = (self.ammo == 1)
        if self._in_weapon_change:
            # weapon_change의 duration_bullets 카운트 (_fire()와 동일 취지).
            # _tick_charge()는 _fire()를 거치지 않고 자체 발사 처리를 하므로 여기에도 필요하다.
            self._wc_shots += 1
        self.ammo -= 1
        if self._sim_log is not None:
            self._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=self.name, ammo=self.ammo))
        bm.notify("squad_ammo_consume", t, self.name)
        bm.notify("hit_count", t, self.name)
        if is_full:
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

        # 톡톡이는 **사격 후 딜레이를 줄이는 컨트롤이다** — 풀차지로 나갔든 아니든
        # 떼기 + 덜 지운 사격 후 딜레이만 기다린다. 그래서 차지속도 버프로 차지가 짧아진
        # 구간에서는 풀차지 샷을 초당 3~4발 낼 수 있다.
        if self.tap_fire:
            self._post_delay_end_t = t + self._tap_release + self._tap_post
        else:
            self._post_delay_end_t = t + self.post_fire_delay
            # 엄폐 니케 + 재장 ≥100%: 딜레이 중 자동재장전 예약 (장탄 유지)
            if self.cover_during_delay and buffs.get("reload_speed_pct", 0.0) >= 100.0:
                self._pending_auto_reload = True
        self._charge_phase = "post_delay"
        bm.state.setdefault("charging", {})[self.name] = False
        bm._invalidate_buffs_cache()
        return events

    # ── weapon_change ─────────────────────────────────────────────────────

    def _tick_weapon_change(
        self, t: float, bm: BuffManager, enemy: dict, cfg: dict, wc_eff: dict
    ) -> list[HitEvent]:
        """
        weapon_change 활성 중 발사 루프.

        변경 무기의 `weapon_type`으로 발사 방식(charge / auto / auto_warmup)을 정해
        `_tick_charge()` 또는 `_tick_auto()`에 위임한다. 기존 CharState 필드
        (weapon, weapon_type, mech, fire_mode, pellets, charge_time_base, post_fire_delay)
        를 임시 교체하고 처리 후 원복한다.

        `duration_bullets`가 있으면 **실제 발사 발수를 세어**(`_wc_shots`) 소진 시
        end_weapon_change().
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
        wc_fire_mode = wc_mech.get("type", "charge")
        wc_max_ammo = wc_eff.get("max_ammo", 1)
        wc_charge_time = wc_eff.get("charge_time", 1.0)
        wc_full_charge_mult = wc_eff.get("full_charge_mult", 100.0)
        wc_reload_time = wc_eff.get("reload_time", self.weapon.get("reload_time", 1.5))
        wc_core_dmg_mult = wc_eff.get("core_dmg_mult", self.weapon.get("core_dmg_mult", 200.0))
        wc_post_fire_delay = wc_eff.get("post_fire_delay", wc_mech.get("post_fire_delay", 0.0))

        # 변경 무기의 발사 메카닉. CDN에 변경 무기 레코드가 없어 캐릭터별 계층이 비므로
        # 수동 실측(weapon_delays `_weapon_change`) → 스킬 텍스트에 명시된 값(wc_eff)
        # → 변경 무기군 기본값 순으로 떨어진다.
        wc_over = _DELAYS.get("_weapon_change", {}).get(self.name, {}).get(wc_eff.get("name", ""), {})
        wc_fire_rate = float(_pick("fire_rate", wc_over, wc_eff, wc_mech,
                                   default=wc_mech.get("fire_rate_min", 1.0)))
        wc_fire_rate_max = _pick("fire_rate_max", wc_over, wc_eff, wc_mech)
        wc_warmup_bullets = float(_pick("warmup_bullets", wc_over, wc_eff, wc_mech, default=1.0))
        wc_pellets = int(_pick("pellets", wc_over, wc_eff, wc_mech, default=1))
        wc_muzzles = int(_pick("muzzles", wc_over, wc_eff, default=1))

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
        orig_weapon            = self.weapon
        orig_weapon_type       = self.weapon_type
        orig_mech              = self.mech
        orig_fire_mode         = self.fire_mode
        orig_pellets           = self.pellets
        orig_muzzles           = self.muzzles
        orig_fire_rate         = self.fire_rate
        orig_fire_rate_max     = self.fire_rate_max
        orig_warmup_bullets    = self.warmup_bullets
        orig_charge_time       = self.charge_time_base
        orig_post_delay        = self.post_fire_delay
        orig_cover_during_delay = self.cover_during_delay
        orig_ammo              = self.ammo if not was_ready else None

        self.weapon              = wc_weapon_dict
        self.weapon_type         = wc_weapon_type
        self.mech                = wc_mech or orig_mech
        self.fire_mode           = wc_fire_mode
        self.pellets             = wc_pellets
        self.muzzles             = wc_muzzles
        self.fire_rate           = wc_fire_rate
        self.fire_rate_max       = wc_fire_rate_max
        self.warmup_bullets      = wc_warmup_bullets
        self.charge_time_base    = wc_charge_time
        self.post_fire_delay     = wc_post_fire_delay
        self.cover_during_delay  = wc_eff.get("cover_during_delay", self.cover_during_delay)

        # 실효 최대 장탄. 스킬 텍스트에 `(사용 무기 변경 시 최대 장탄 수 효과 갱신)`이 있는
        # 무기 변경만 최대 장탄 수 버프를 받는다(`max_ammo_buff_applies`). 문구가 없으면 표기 고정.
        if wc_max_ammo == -1:
            wc_ammo_full = 999999
        elif wc_eff.get("max_ammo_buff_applies"):
            wc_ammo_full = self._full_ammo(bm, t)   # self.weapon이 변경 무기로 교체된 상태
        else:
            wc_ammo_full = wc_max_ammo

        if wc_fire_mode == "charge":
            if was_ready:
                self.ammo = wc_ammo_full
            elif self._wc_new_session:
                # 이전 무기의 차지가 진행 중인 채로 모드에 진입했다면 차지를 새로 시작한다.
                # 무기가 통째로 바뀌므로 앞 무기에 쌓인 차지 진행분을 물려받을 근거가 없다.
                #
                # 이어받게 두면 변경 무기의 차지가 **짧을수록** 손해가 되는 역설이 생긴다:
                # _charge_start_t + (짧은 차지)가 이미 과거라 진입과 동시에 발사돼
                # 풀버스트 진입(버스트 사용 +0.15초) 전에 쏘고 버프를 통째로 놓친다.
                # (맥스웰 : 오디너리 미케닉 — 과전류 5단계 0.4초가 4단계 1.5초보다
                #  대미지가 34% 낮았다)
                self._charge_start_t = t
        elif self._wc_new_session:
            # 연사 무기: 세션 진입 시 1회만 장탄을 채우고 발사 시계를 현재 시각에 맞춘다.
            # (차지 무기처럼 매 tick 리필하면 장탄이 줄지 않아 발사 흐름이 끊긴다)
            self.ammo = wc_ammo_full
            self.next_fire_time = t
            orig_ammo = None
        self._wc_new_session = False

        # 발수 카운트는 _fire()/_tick_charge()가 self._wc_shots에 직접 누적한다
        if wc_fire_mode in ("auto", "auto_warmup"):
            events = self._tick_auto(t, bm, enemy, cfg)
        else:
            events = self._tick_charge(t, bm, enemy, cfg)

        # 원복
        self.weapon              = orig_weapon
        self.weapon_type         = orig_weapon_type
        self.mech                = orig_mech
        self.fire_mode           = orig_fire_mode
        self.pellets             = orig_pellets
        self.muzzles             = orig_muzzles
        self.fire_rate           = orig_fire_rate
        self.fire_rate_max       = orig_fire_rate_max
        self.warmup_bullets      = orig_warmup_bullets
        self.charge_time_base    = orig_charge_time
        self.post_fire_delay     = orig_post_delay
        self.cover_during_delay  = orig_cover_during_delay
        if orig_ammo is not None and was_ready:
            # ready→charging 전환만 된 경우는 ammo 원복 불필요 (충전 중)
            pass

        # duration_bullets 기반: 지정 발수를 다 쏘면 weapon_change 종료
        duration_bullets = wc_eff.get("duration_bullets")
        if duration_bullets is not None:
            duration_bullets = int(duration_bullets)
            if wc_max_ammo != -1 and duration_bullets == wc_max_ammo:
                # "모든 탄환 발사 시 제거" 형태 — 장탄 버프로 장탄이 늘면 발수도 함께 늘어난다
                duration_bullets = wc_ammo_full
        if duration_bullets is not None and self._wc_shots >= duration_bullets:
            # 원래 무기로 돌아오면 charge_phase를 ready로 초기화
            self._charge_phase = "ready"
            if wc_fire_mode in ("auto", "auto_warmup"):
                # 마지막 발과 같은 tick에 잡힌 변경 무기 재장전 예약은 무효
                # (변경 무기는 재장전하지 않는다 — 장탄 소진이 곧 모드 종료)
                self.reloading_until = -1.0
                self.next_fire_time = t
            self.ammo = orig_ammo if orig_ammo is not None else self.weapon["max_ammo"]
            # 장탄 원복이 끝난 뒤에 종료 이벤트를 쏜다 — event:state_end로 발동하는
            # 장탄 조작 효과(라플라스 `탄환 100% 제거`)가 원복에 덮이지 않도록.
            bm.end_weapon_change(self.name, t)

        return events

    def _fixed_charge_time(self, bm: BuffManager) -> float:
        """charge_time_fixed 버프의 fixed_value(초). 복수이면 가장 나중에 부여된 값.

        fixed_value 없이 stat만 붙은 버프(아니스 : 스타 `슈팅 스타2`)는 "차지 속도 버프를
        무시하고 표기 시간으로 고정"이므로 후보가 없으면 charge_time_base를 그대로 쓴다.

        base를 후보에 넣지 않는다 — "N초로 고정"은 base보다 **짧게** 만드는 경우도 있다
        (맥스웰 : 오디너리 미케닉 — 무기 변경 「메티스 버스트 버스터」 3.0초 안에서
        과전류 5단계가 0.4초로 단축. base를 후보에 넣고 최대값을 취하면 영원히 3.0초).

        복수 활성 시 최대값이 아니라 **최신값**을 고른다 — 고정값은 모드 진입/종료로
        갈아끼워지는 형태가 정본이다 (스노우 화이트 : 헤비암즈 — 영구 1.2초 위에 모드
        3.2초가 얹히고, 모드 종료 시 `event:state_end`로 1.2초가 재부여된다. 그 재부여
        항목의 존재 자체가 최신값 우선을 전제한 데이터다).
        """
        best: float | None = None
        best_key: tuple[float, int] | None = None
        for ab in bm._active:
            if ab.caster != self.name:
                continue
            if ab.effect.get("stat") != "charge_time_fixed":
                continue
            val = ab.effect.get("fixed_value")
            if val is None:
                continue
            # uid는 단조 증가라 같은 프레임에 부여된 복수 항목은 parsed_skills 배열 순서상
            # 뒤쪽이 이긴다 (동률 판정을 결정론적으로 만든다).
            key = (ab.activated_at, ab.uid)
            if best_key is None or key > best_key:
                best, best_key = float(val), key
        return self.charge_time_base if best is None else best

    # ── 재장전 ────────────────────────────────────────────────────────────

    def _fixed_reload_time(self, bm: BuffManager) -> float | None:
        """reload_time_fixed 버프의 fixed_value(초). 복수이면 최대값. 없으면 None.

        charge_time_fixed와 같은 방식으로 _active를 직접 읽는다 (fixed_value 계열은
        get_buffs의 수치 합산 경로를 타지 않는다).
        """
        max_val: float | None = None
        for ab in bm._active:
            if ab.effect.get("stat") != "reload_time_fixed":
                continue
            if self.name not in (ab.target_chars or []):
                continue
            val = ab.effect.get("fixed_value")
            if val is not None:
                max_val = float(val) if max_val is None else max(max_val, float(val))
        return max_val

    def _apply_reload_control(self, t: float, bm: BuffManager) -> bool:
        """장전컨 — 탄이 남았는데 일부러 재장전. 걸었으면 True. 정본: context/CONTROL.md.

        A `before_fb_end` : 풀버스트 종료 `lead`초 전에 재장전. 종료 시각이 확정돼 있어
                            예측이 필요 없다. 재장 0초 구간을 놓치지 않는 용도.
        B `into_fb`       : 다음 풀버스트 시작 직후(`margin`초 뒤)에 재장전이 끝나도록
                            역산해서 시작. 시작 시각은 직전 사이클 주기로 예측한다.
                            완료가 시작보다 빠르면 최대장탄 증가 버프를 놓치므로 margin>0.
        """
        if not self.reload_policy:
            return False
        # 모드 탄창 로직을 흔들지 않도록 weapon_change 중에는 걸지 않는다
        if self._in_weapon_change or bm.get_weapon_change(self.name) is not None:
            return False
        if self.reloading_until > 0 or self._post_reload_end_t > 0:
            return False
        if self.ammo >= self._full_ammo(bm, t):
            return False

        if self.reload_policy == "before_fb_end":
            if not bm.state.get("full_burst", False):
                return False
            anchor = bm.state.get("full_burst_end_t", -1.0)
            if anchor <= 0 or t < anchor - self.reload_lead:
                return False
        elif self.reload_policy == "into_fb":
            anchor = bm.state.get("next_fb_start_pred", -1.0)
            if anchor <= 0:
                return False  # 관측 주기가 없는 첫 사이클
            if t < anchor - (self._reload_duration(bm, t) - self.reload_margin):
                return False
        else:
            return False

        if anchor == self._reload_ctrl_anchor:
            return False  # 이 사이클에서 이미 걸었다
        self._reload_ctrl_anchor = anchor
        self._start_reload(t, bm, label="재장전 시작(장전컨)")
        return True

    def _reload_duration(self, bm: BuffManager, t: float) -> float:
        """현재 버프를 반영한 재장전 소요 시간(초)."""
        fixed = self._fixed_reload_time(bm)
        if fixed is not None:
            # "재장전 시간 N초로 고정" — 절대 고정이라 reload_speed_pct를 타지 않는다
            return fixed
        speed_pct = bm.get_buffs(self.name, "__enemy__", t).get("reload_speed_pct", 0.0) / 100.0
        return self.weapon["reload_time"] * max(0.0, 1.0 - speed_pct)

    def _start_reload(self, t: float, bm: BuffManager, label: str = "재장전 시작"):
        self.reloading_until = t + self._reload_duration(bm, t)
        self._reload_in_weapon_change = bm.get_weapon_change(self.name) is not None
        # 차지 중에 재장전이 걸리면 차지는 무효다. 재장전 후에는 처음부터 다시 차지한다
        # (초기화하지 않으면 남아 있던 _charge_start_t로 재장전 직후 즉시 발사된다).
        if self.fire_mode == "charge":
            self._charge_phase = "ready"
        bm.state.setdefault("charging", {})[self.name] = False
        bm._invalidate_buffs_cache()
        # 예열은 재장전으로 리셋되지 않는다. 재장전 동안의 미사격은 _cool_warmup이 시간 비례로 냉각.
        if self._sim_log is not None:
            self._sim_log.reload_log.append(ReloadLogEntry(t=t, caster=self.name, event=label))

    def _full_ammo(self, bm: BuffManager, t: float) -> int:
        # 무기 변경 모드 중이면 그 모드의 장탄으로 채운다
        wc_eff = bm.get_weapon_change(self.name)
        if wc_eff is not None:
            wc_max = wc_eff.get("max_ammo", -1)
            if wc_max != -1:
                return int(wc_max)
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        ammo_pct = buffs.get("max_ammo_pct", 0.0) / 100.0
        ammo_flat = int(round(buffs.get("max_ammo_flat", 0.0)))
        return round(self.weapon["max_ammo"] * (1.0 + ammo_pct)) + ammo_flat

    def _finish_reload(self, t: float, bm: BuffManager):
        self.ammo = self._full_ammo(bm, t)
        self.reloading_until = -1.0
        self._reload_in_weapon_change = False
        bm.notify("event:full_reload", t, self.name)
        if self._sim_log is not None:
            self._sim_log.reload_log.append(ReloadLogEntry(t=t, caster=self.name, event="재장전 완료"))
            self._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=self.name, ammo=self.ammo))
        if self.post_reload_delay > 0.0:
            self._post_reload_end_t = t + self.post_reload_delay
        else:
            self.next_fire_time = t

    def _auto_reload(self, t: float, bm: BuffManager):
        """엄폐 니케의 딜레이 중 자동재장전. 장탄을 최대로 채우고 event:full_reload 발동.
        post_reload_delay는 적용하지 않음 (재장이 post_fire_delay 안에서 끝남)."""
        self.ammo = self._full_ammo(bm, t)
        bm.notify("event:full_reload", t, self.name)
        if self._sim_log is not None:
            self._sim_log.reload_log.append(ReloadLogEntry(t=t, caster=self.name, event="자동 재장전(엄폐)"))
            self._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=self.name, ammo=self.ammo))


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

        # 버스트 게이지 충전 완료 시각 — 첫 버스트는 burst_regen_time 무시, first_burst_time에 발동
        _first_burst_t = config.get("first_burst_time", 3.0)
        self.gauge_full_at: dict[str, float] = {
            c["name"]: _first_burst_t for c in squad
        }

        # 버스트 진행 상태
        # "idle" / "stage:N" / "reenter:N" / "switching" / "full_burst"
        self._phase: str = "idle"
        self._next_action_t: float = math.inf
        self._full_burst_end_t: float = -1.0
        # 직전 풀버스트 시작 시각 (장전컨 정책 B의 사이클 주기 관측용)
        self._last_fb_start_t: float = -1.0

        # 쿨타임 대기 중인 단계의 후보 목록 (대기가 아니면 None).
        # _next_action_t는 두 가지가 섞여 있다 — 의도된 딜레이(단계 전환 0.1s,
        # reenter 0.5s, 풀버스트 진입 0.05s)와 "전원 쿨이라 기다린다"는 예측.
        # 앞쪽은 지켜야 하고 뒤쪽은 쿨이 바뀌면 다시 계산해야 한다.
        # 이 목록이 채워져 있을 때만 재계산해서 둘을 구분한다.
        self._cd_wait_candidates: list[str] | None = None

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
            bm._invalidate_buffs_cache()
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

        # ── 쿨 대기 중 도착한 버스트 쿨감 반영 ─────────────────────────────
        # 대기에 들어갈 때 잡아둔 _next_action_t는 그 시점 쿨 기준의 예측이다.
        # 이후 burst_cooldown_reduce가 들어와 burst_ready_at이 당겨져도 예약 시각은
        # 그대로여서 헛대기가 생겼다 (루주 `카드 스로우` −7s에 3.42초 헛대기 실측).
        # 의도된 딜레이까지 무시하지 않도록 쿨 대기 중일 때만 다시 계산한다.
        if self._cd_wait_candidates:
            earliest = min(self.burst_ready_at.get(n, 0.0) for n in self._cd_wait_candidates)
            self._next_action_t = min(self._next_action_t, max(t, earliest))

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
            # 장전컨(context/CONTROL.md)이 쓰는 사이클 정보를 state에 공개한다.
            # 종료 시각은 여기서 확정 — 정책 A는 예측 없이 이 값을 그대로 쓴다.
            # 시작 시각은 반응형(게이지·쿨)이라 확정할 수 없어 직전 주기로 예측한다.
            state["full_burst_end_t"] = self._full_burst_end_t
            if self._last_fb_start_t >= 0.0:
                state["next_fb_start_pred"] = t + (t - self._last_fb_start_t)
            self._last_fb_start_t = t
            bm._invalidate_buffs_cache()
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
        # 쿨 대기 플래그는 매번 새로 판정한다 (아래 대기 분기에서만 다시 세운다)
        self._cd_wait_candidates = None

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

        # 전원 쿨타임 중 → 대기.
        # 여기서 잡은 시각은 "지금 쿨 기준의 예측"일 뿐이다. 대기 중에 버스트 쿨감이
        # 들어오면 tick()이 후보 목록을 보고 앞당긴다 (_cd_wait_candidates).
        earliest = min(self.burst_ready_at.get(n, 0.0) for n in candidates)
        self._next_action_t = max(self._next_action_t, earliest)
        self._cd_wait_candidates = list(candidates)
        return [], False, None

    def _fire_pending_burst_dmg(self, t: float, bm: BuffManager) -> list[HitEvent]:
        """풀버스트 진입 후 버프 적용 상태에서 미뤄둔 bonus_damage 발동."""
        events = []
        for name, eff, hit_count in self._pending_burst_dmg:
            cs = self.char_states[name]
            buffs = bm.get_buffs(
                name, "__enemy__", t,
                exclude_names=eff.get("_exclude_buffs", frozenset()),
            )
            buffs["is_element_match"] = cs.is_element_match

            coeff = eff["_coeff"]
            # scaling: "stack_count" → 참조 게이지/버프의 현재 수치만큼 계수 곱산
            if eff.get("scaling") == "stack_count":
                stack = bm.ref_count(name, eff.get("scaling_ref", ""))
                coeff *= stack if stack is not None else 0

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

        # 개별 버스트 쿨타임 갱신 (burst_cooldown buff 차감 반영)
        # burst_cast notify 전에 설정해야 burst_cooldown_reduce instant가
        # 새 쿨타임에 정확히 적용됨 (예: 라피 레드 후드 계승되는 힘 -20s)
        cd = self._burst_cd.get(name, 40.0)
        buffs = bm.get_buffs(name, "__enemy__", t)
        cd_buff = buffs.get("burst_cooldown", 0.0)
        self._cd_applied_at_cast[name] = cd_buff
        cd = max(0.0, cd - cd_buff)
        self.burst_ready_at[name] = t + cd

        bm.notify("burst_cast", t, name)
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


def _later_burst_cast_buffs(caster: str, eff: dict) -> frozenset[str]:
    """`eff`보다 **뒤에** 서술된 같은 `burst_cast` 트리거 buff들의 이름.

    parsed_skills.json의 배열 순서는 원문 `■` 블록 순서를 그대로 보존한다
    (GAMEPLAY.md §효과 실행 순서). 딜 블록보다 뒤에 적힌 버프는 그 딜에 실리지 않으므로,
    계산이 풀버스트로 밀리는 보류 딜에서 제외할 이름 집합을 만든다.
    """
    effs = _PARSED_SKILLS.get(caster, [])
    # 호출 경로에 따라 eff가 원본 dict의 사본일 수 있어 identity로 못 찾는다.
    # name + source + stat로 위치를 되짚는다 (name은 캐릭터 내 사실상 유일).
    key = (eff.get("name"), eff.get("source"), eff.get("stat"))
    for i, e in enumerate(effs):
        if e is eff or (e.get("name"), e.get("source"), e.get("stat")) == key:
            break
    else:
        return frozenset()
    later = set()
    for e in effs[i + 1:]:
        if e.get("type") != "buff":
            continue
        if "burst_cast" not in e.get("trigger", {}).get("timing", []):
            continue
        nm = e.get("name")
        if nm:
            later.add(nm)
    return frozenset(later)


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
        # 무기 변경 모드 중이면 그 모드의 장탄이 상한이다.
        # (모드 장탄 15발인데 원래 무기 328발 기준으로 채워지면 모드가 사실상 무한 탄창이 된다)
        wc_eff = bm.get_weapon_change(cs.name)
        if wc_eff is not None:
            wc_max = wc_eff.get("max_ammo", -1)
            if wc_max != -1:
                return int(wc_max)
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
            if cs._sim_log is not None:
                cs._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=name, ammo=cs.ammo))
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
            if cs._sim_log is not None:
                cs._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=name, ammo=cs.ammo))

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

    def handle_force_reload(eff, caster, t, val):
        target_names = _resolve_targets(eff, caster)
        for name in target_names:
            cs = char_states.get(name)
            if cs is None or cs.reloading_until > 0:
                continue
            cs.ammo = 0
            cs._start_reload(t, bm)

    bm.register_instant_handler("ammo_charge_pct", handle_ammo_charge_pct)
    bm.register_instant_handler("ammo_charge_flat", handle_ammo_charge_flat)
    bm.register_instant_handler("burst_cooldown_reduce", handle_burst_cooldown_reduce)
    bm.register_instant_handler("heal_hp_pct", handle_heal_hp_pct)
    bm.register_instant_handler("current_hp_reduce", handle_current_hp_reduce)
    bm.register_instant_handler("force_reload", handle_force_reload)


# ── simulate ──────────────────────────────────────────────────────────────

def _check_names(names: list[str], allow_unparsed: bool) -> None:
    """스쿼드 이름을 정본 JSON 두 곳과 대조한다.

    별칭(`마스트`)이나 부제 없는 원본은 `parsed_nikke.json`에는 있고
    `parsed_skills.json`에는 없다. 효과 조회가 `.get(name, [])`이라 그대로 두면
    스탯·무기만 정상이고 스킬이 0개인 니케로 조용히 돌아가 — 에러 없이 그럴듯한
    오답이 나온다. 여기서 끊는다 (context/ALIASES.md).
    """
    unknown = [n for n in names if n not in _NIKKE]
    if unknown:
        raise ValueError(
            f"parsed_nikke.json에 없는 캐릭터: {unknown}\n"
            f"  정식 명칭을 써야 한다. 별칭 표: context/ALIASES.md"
        )
    if allow_unparsed:
        return
    unparsed = [n for n in names if n not in _PARSED_SKILLS]
    if unparsed:
        raise ValueError(
            f"스킬이 파싱되지 않은 캐릭터: {unparsed}\n"
            f"  이대로 돌리면 스킬 0개로 계산되어 결과가 조용히 틀린다.\n"
            f"  ① 별칭을 쓴 것은 아닌지 확인 — `마스트` → `마스트 : 로망틱 메이드` (context/ALIASES.md)\n"
            f"  ② 파싱 전 신규 캐릭터를 의도적으로 돌리는 것이라면 "
            f"config['allow_unparsed']=True (CLI: --allow-unparsed)"
        )


def simulate(
    squad: list[dict],
    config: dict | None = None,
    enemy: dict | None = None,
    verbose: bool = False,
    seed: int | None = None,
) -> SimResult:
    """
    스쿼드 전투 시뮬레이션 (1~5인).

    Parameters
    ----------
    squad   : 캐릭터 인스턴스 목록 (base_stat.py 구조 + skill_level + burst_regen_time)
    config : 시뮬레이션 설정 (DEFAULT_CONFIG 기반 오버라이드)
    enemy  : 적 정보 (DEFAULT_ENEMY 기반 오버라이드)
    seed   : 난수 시드. None(기본)이면 시드를 건드리지 않아 매 실행 결과가 달라진다
             (UI의 기대딜은 여러 회 평균이 맞으므로 이쪽이 기본).
             정수를 주면 크리·코어히트·prob 조건·allies_random이 모두 재현되어
             결과가 완전히 결정론적이 된다. 회귀 하네스(context/snapshot.py)와
             CLI(context/sim.py)가 사용한다.
    """
    if seed is not None:
        random.seed(seed)

    cfg = {**DEFAULT_CONFIG, **(config or {})}
    enm = {**DEFAULT_ENEMY, **(enemy or {})}
    duration = cfg["duration"]

    squad = [{**DEFAULT_CHAR, **c} for c in squad]
    _check_names([c["name"] for c in squad], bool(cfg["allow_unparsed"]))

    base_stats: dict[str, dict] = {c["name"]: calc_base_stats(c) for c in squad}

    state: dict = {
        "full_burst":   False,
        # 장전컨(context/CONTROL.md)용 풀버스트 사이클 정보. BurstController가 갱신
        "full_burst_end_t":   -1.0,  # 현재 풀버스트 종료 시각 (진입 시 확정)
        "next_fb_start_pred": -1.0,  # 다음 풀버스트 시작 예측 (직전 사이클 주기 기준)
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
            # 자신의 _active 엔트리에 캡처된 stack 값을 먼저 확인
            # (scaling_ref 버프가 이미 제거됐을 경우 대비)
            scale = None
            eff_name = eff.get("name", "")
            for ab in bm._active:
                if ab.caster == caster and ab.effect.get("name") == eff_name:
                    scale = ab.stack
                    break
            if scale is None:
                # 자기 엔트리가 없을 때만 참조 게이지/버프를 본다
                scale = bm.ref_count(caster, ref)
            coeff *= scale if scale is not None else 0

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
        #
        # 단 **3버스트 캐릭터만** 보류한다 (유저 확인). 풀버스트는 3버스트 발동 직후 시작하므로
        # B3의 버스트 추가 대미지만 풀버스트 버프를 받는다. B1/B2는 풀버스트보다 몇 초 앞서
        # 발동하므로 그 시점 버프로 즉시 계산해야 한다.
        stat = eff.get("stat", "")
        timings = eff.get("trigger", {}).get("timing", [])
        target_field = eff.get("target", "")
        is_burst3 = str(_NIKKE.get(caster, {}).get("burst_stage", "")) == "3"
        if stat == "bonus_damage" and "burst_cast" in timings and is_burst3:
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
            # 원문 블록 순서 = 실행 순서: 이 딜보다 뒤에 서술된 같은 burst_cast 버프는
            # 계산이 풀버스트로 밀려도 실리면 안 된다 (GAMEPLAY.md §효과 실행 순서).
            eff_with_coeff["_exclude_buffs"] = _later_burst_cast_buffs(caster, eff)
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
            n = bm.ref_count(caster, stat_parts[1])
            if n is not None:
                hit_count = n
        elif eff.get("scaling") == "stack_count" and base_stat != "dot_damage":
            # damage stat + scaling:stack_count → scaling_ref 게이지/스택 수만큼 발사.
            # dot_damage는 제외 — 스택 배율이 위 계수 블록에서 이미 곱해지므로
            # 여기서 또 히트 수로 잡으면 스택이 두 번 곱해진다. 틱당 히트는 1회다.
            ref = eff.get("scaling_ref", "") or (stat_parts[1] if len(stat_parts) > 1 else "")
            n = bm.ref_count(caster, ref)
            if n is not None:
                hit_count = n
        weapon_type = cs.weapon.get("weapon_type", "")
        ht = default_hit_type(
            is_normal_atk=is_normal,
            is_full_burst=is_full_burst,
            # core_damage는 "코어 명중 대미지"가 명시된 확정 코어 히트 (core_hit condition이 코어 유무를 게이팅)
            is_core=(enm.get("core_px", 0) > 0 and is_normal) or base_stat == "core_damage",
            is_core_damage=(base_stat == "core_damage"),
            # 파츠 판정은 원문이 파츠를 명시한 스킬(hits_parts)에만 붙는다 — 파츠 보스일 때만
            is_part=(bool(eff.get("hits_parts")) and enm.get("has_parts", False)),
            is_optimal_range=(weapon_type in enm.get("optimal_range_weapons", []) and is_normal),
            is_burst_damage=(base_stat == "burst_damage"),
            # 대상 설명이 '적 전체에게'인 버스트 대미지 → burst_dmg_aoe_pct 수혜
            is_aoe_burst=(base_stat == "burst_damage" and target_field == "all_enemies"),
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

    # battle_start 버프 적용 후 장탄을 실제 max_ammo로 초기화
    for cs in char_states.values():
        cs.ammo = cs._full_ammo(bm, 0.0)
        if sim_log is not None:
            sim_log.ammo_log.append(AmmoLogEntry(t=0.0, caster=cs.name, ammo=cs.ammo))

    # 파츠 파괴 주기 (config["part_break_interval"], 초). 0/미지정이면 무발동.
    # `event:part_destroy`는 원래 notify 호출처가 없어 영구 무발동이었다 — 보스 sim에서
    # 파츠가 실제로 파괴되지 않기 때문. 파츠 파괴에 반응하는 캐릭터(아크레인저 블랙 배터리)를
    # 두 모드로 비교하기 위한 스위치다: 기본은 무발동, 주기를 주면 그 간격으로 발생.
    _part_break_interval = float(cfg.get("part_break_interval", 0) or 0)
    _next_part_break = _part_break_interval if _part_break_interval > 0 else math.inf

    t = 0.0
    while t <= duration:
        bm.tick(t)

        if t >= _next_part_break:
            for char in squad:
                bm.notify("event:part_destroy", t, char["name"])
            _next_part_break += _part_break_interval

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
