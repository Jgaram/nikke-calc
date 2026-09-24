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
from dataclasses import replace
from typing import Any

from .aim import needs_angle, sample as sample_landing
from .base_stat import calc_base_stats
from .boss_pattern import (
    AIM_ADDS, DEFAULT_BOSS_ATK, DEFAULT_EXPLOSION_RANGE, DOT_STAT, ENEMY, GEOM_KEY, INTERRUPT_REACH_KEY,
    PART_REACH_KEY, SIMPLE, COORD, AttackHit, AttackSpec, BossScript, boss_mode, hit_reach,
    validate as validate_boss_patterns,
)
from .buff_manager import (
    BuffManager, _QUANT_PARTS_KEY, _get_skill_lv, _is_enemy,
    BURST_GAUGE_EXCEPTIONS, in_optimal_range,
)
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
    GaugeLogEntry,
    ControlLogEntry,
    SimLog,
    SimResult,
    SquadHitEntry,
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
# 명중률 1%당 탄착군 직경이 줄어드는 비율. CDN에 slope가 없어 커뮤니티 실험값에서 유도했다 —
# 세 무기의 slope/base가 0.9079·0.9091·0.9083%로 사실상 같아 곱셈 법칙으로 읽은 것이고,
# **확인된 사실이 아니다**(docs/DATA_VERIFY.md §명중률/탄착군에 ⬜).
_ACC_SLOPE_RATIO: float = float(_ACCURACY_DATA.get("_slope_ratio", 0.00908))
# CDN 미수집(출시 전 프리뷰) 캐릭터용 탄착군 직경 폴백.
_FALLBACK_SPREAD: float = float(_ACCURACY_DATA.get("_fallback_spread", 10))

DT = 1 / 60  # 시뮬레이션 스텝 (초)


# ── 소스별 반올림 (장탄 · 차지 시간) ───────────────────────────────────────
# 최대 장탄과 차지 시간의 % 버프는 **합산 후 한 번**이 아니라 **소스마다 따로** 기본값에
# 곱해 눈금에 맞춰 반올림한 뒤 그 결과를 더한다 (유저 인게임 확인, 2026-08-19 —
# GAMEPLAY.md §무기 메카닉). 그룹을 나누는 규칙은 `buff_manager._quant_group_key`.
#
#   최대 장탄 = 기본장탄 + Σ 반올림(기본장탄 × 그룹%, 1발) + flat   (하한 1발)
#   차지 시간 = 기본차지 − Σ 반올림(기본차지 × 그룹%, 0.01초) + flat (하한 0초)
#
# 0.5는 올린다(유저 지정). 음수 쪽도 같은 방향(+∞)이라 −2.5는 −2가 된다.

def _round_half_up(x: float) -> float:
    return math.floor(x + 0.5)


def _quantize(x: float, step: float) -> float:
    """`x`를 `step` 눈금에 맞춰 반올림. 0.01초 눈금은 부동소수점 오차를 피해 정수로 센다."""
    return _round_half_up(x / step) * step


def _quant_sum(base: float, buffs: dict, buff_key: str, step: float) -> float:
    """`base`에 걸린 그룹별 % 기여를 각각 반올림해 더한 총량.

    `buffs`에 그룹 목록(`_quant_parts`)이 없으면 합계 하나를 한 그룹으로 본다 —
    BuffManager를 거치지 않고 만든 buffs dict(테스트·damage.py 템플릿)도 돌아야 한다.
    """
    parts = (buffs.get(_QUANT_PARTS_KEY) or {}).get(buff_key)
    if parts is None:
        total = buffs.get(buff_key, 0.0)
        parts = [total] if total else []
    return sum(_quantize(base * (p / 100.0), step) for p in parts)

# ── 컨트롤 상수 (docs/CONTROL.md) ───────────────────────────────────────
# SR/RL의 발사 딜레이 0.38초는 두 조각이다 — 사격 전 0.22초 + 사격 후 0.16초.
# 사격 전 0.22초는 누름(조준) 구간 그 자체라 지울 수 없고, 컨트롤로 지우는 건 사격 후 0.16초다.
# 얼마나 지우는지가 실력 요소이며, 그 실력은 `rate`(초당 발사) 하나로 표현한다 —
# rate가 낮다는 건 사격 후 딜레이를 덜 지웠다는 뜻이다.
_TAP_MIN_HOLD          = 0.22  # 사격 전 딜레이 = 최소 누름 시간(초). 더 짧게 누르면 발사 안 됨
_TAP_CUTTABLE_DELAY    = 0.16  # 사격 후 딜레이(초). 컨트롤로 지울 수 있는 몫
_TAP_RELEASE_DEFAULT   = 0.03  # 톡톡이 떼는 시간 기본값(초). 하드웨어 하한 0.02
_RELOAD_LEAD_DEFAULT   = 0.3   # 장전컨 A: 풀버스트 종료 몇 초 전에 재장전을 시작할지
_RELOAD_MARGIN_DEFAULT = 0.1   # 장전컨 B: 풀버스트 시작 몇 초 뒤에 재장전이 끝나게 할지
_HOLD_LEAD_DEFAULT     = 0.5   # 홀드컨: 풀버스트 종료 몇 초 전에 들고 있던 풀차지를 뗄지
_CTRL_FRAME            = 1.0 / 60.0  # 한 프레임(초). 판정 직후를 가리킬 때 쓰는 최소 여유

# 클릭 스케줄 어휘. 정본: docs/CONTROL.md §체계.
#   **언제**(아래 두 표기) / 행위(mode) — 무엇을
#
# **상태 창** — 전투 상태 그 자체다. 앵커+오프셋으로 환산할 수 없어서 남는다.
# `burst_charge`가 대표인데, 판정이 `state["burst_gauge_charging"]`이고 이건
# `BuffManager.add_burst_gauge()`가 충전 여부를 판정하는 **바로 그 값**이다. 톡톡이 구간과
# 충전 구간이 구조적으로 어긋날 수 없는 이유가 이것이라, 오프셋으로 바꾸면 그 보장이 사라진다.
# `after_own_fb`는 **구간이 아니라 역산 전용 슬롯**이다 (`hold_judge`와만 짝짓는다).
_CLICK_WINDOWS = ("always", "burst_charge", "burst_chain", "own_full_burst", "after_own_fb")
_CLICK_MODES   = ("tap", "hold", "hold_until_close", "hold_judge", "auto")
# 스케줄을 두 관심사로 나눠 묻는다 — `CharState._click_entry()` 참조.
_CLICK_PRESS_MODES = ("tap", "hold", "hold_until_close", "auto")  # 누름: 차지 시작 시점에 래치
_CLICK_HOLD_MODES  = ("hold", "hold_until_close", "hold_judge")   # 떼기: 매 틱 평가

# **앵커 카탈로그 — 닫힌 집합이다.** 표현력이 커지는 만큼 "조용히 무시되는 입력"이 생기기
# 쉬운데 그건 §체계 불변식 ②가 금지한다. `spec.WHEN_KEYS`·`APPLY_KEYS`를 닫고 doclint
# 검사 L로 지키는 것과 같은 선례를 따른다. 정본: docs/CONTROL.md §설정 스키마.
#
# **앵커는 자기 게이트를 함께 든다.** 정책 A·C가 `full_burst`를 요구하고 B가 요구하지
# 않는 차이가 앵커 정의 안으로 들어가서, 종전 정책이 글자 그대로 같은 판정식으로 접힌다.
# `full_burst_end_t`는 풀버스트가 끝나도 남는 값이라 게이트 없이 쓰면 결과가 달라진다.
#
#   combat_start   0.0                              게이트 없음            확정
#   fb_end         state["full_burst_end_t"]        full_burst             확정
#   own_fb_end     같음                             + burst_casted[본인]   확정
#   next_fb_start  state["next_fb_start_pred"]      값 > 0                 예측(관측+쿨타임)
#   own_buff_end   본인이 발동한 이름 있는 버프 만료  버프 활성              확정(대상 미조회)
_ANCHORS = ("combat_start", "fb_end", "own_fb_end", "next_fb_start", "own_buff_end")
# 오프셋이 상수가 아니라 **런타임 함수**인 자리. 정책 C의 진입 시각이 그 시점의 실제
# 재장전 시간에서 나오기 때문에 필요하다 — 상수 offset으로는 표현되지 않는다.
_ANCHOR_MINUS = ("reload_total",)
# "언제"를 적는 키. 상태 창(`window`)과 앵커(`anchor`+…)는 **정확히 하나만** 쓴다.
# `gate`는 이미 확정된 이번 사이클의 버스트 사용자를 보는 닫힌 런타임 조건이다.
_GATE_KEYS = ("burst_stage", "burst_user")
_WHEN_KEYS = ("window", "anchor", "offset", "len", "minus", "gate", "buff")
# 항목이 쓸 수 있는 키 — **닫혀 있다.** 모르는 키가 살아남으면 오타가 조용히
# "아무 일도 안 함"이 된다(§체계 불변식 ②). `_prio`·`_timing`은 검증 뒤에 붙는 내부 키다.
_CLICK_ENTRY_KEYS = _WHEN_KEYS + (
    "mode", "priority", "rate", "release", "full_charge_interval", "lead")
_RELOAD_KEYS = ("anchor", "offset", "minus", "gate", "buff") + (
    "policy", "lead", "margin", "if_dry", "duration", "cancel_on_full", "priority")
# 버스트 버튼 — **다섯째 원시 입력**이다. 정본: docs/CONTROL.md §L0.
#   pattern  어느 사이클에 쓰는가 (러너가 `config["burst_pattern"]`으로 편다)
#   delay    사이클 안에서 언제 누르는가 — 차례가 온 뒤 몇 초를 기다리나 (기본 0 = 즉시)
# **조율(③) 대상이 아니다.** 배타 자원인 카메라를 이 입력만 요구하지 않기 때문이다 —
# 조작자 배타의 예외이자 그 모델을 완성하는 조각이다(`_wants_control()`이 이걸 반환하지
# 않는 것이 그 집행이다).
_BURST_KEYS = ("pattern", "delay")
# 에임 — **여섯째 원시 입력**(마우스로 조준점을 옮긴다). 좌표 모드 보스(`enemy["coord"]`)에서만 뜻이 있다.
# 정본: docs/CONTROL.md §에임.
#   at        겨눌 곳 — 보스 스크립트의 표적 이름, 또는 "core"(열린 코어)
#   언제       클릭과 같은 window·anchor 어휘. 안 적으면 그 표적이 살아 있는 동안 내내
#   priority  카메라 경합 등급. 기본은 저지원·벌칙 파츠 상(놓치면 보스가 실패 분기로 간다) · 그 밖 중
# 손 에임은 **카메라를 잡은 니케에게만** 걸린다 — 조율(③) 대상이다. 그 밖의 니케는 자동 에임이고,
# 풀버스트 동안은 [사격 집중]으로 카메라 니케의 조준점을 따른다(`simulate` `_resolve_aims`).
_AIM_ENTRY_KEYS = _WHEN_KEYS + ("at", "priority")
_AIM_WINDOWS = ("always", "burst_charge", "burst_chain", "own_full_burst")
# `control` 자체가 쓸 수 있는 키. 여기도 닫는다 — 오타 난 축은 조용히 아무것도 안 한다.
_CONTROL_KEYS = ("click", "tap_fire", "hold", "reload", "cover", "burst",
                 "sequence", "priority", "aim")

# 조작 모드 — 카메라가 하나뿐이라는 제약을 어떻게 다룰지. 정본: docs/CONTROL.md §조작자는 한 명.
_CTRL_MODES = ("solo", "warn", "strict")

# 조작 등급 — 카메라 경합의 승자는 "나중에 요청해서"가 아니라 **"이게 더 급해서"**로 갈린다.
# 등급은 **요청 단위**다: 기본값이 요청 종류에서 나오고, 부착 규칙(캐릭터 레이어·택틱·
# 호출부)이 요소마다 덮어쓴다. 정본: docs/CONTROL.md §조작자는 한 명.
#
#   상 30  놓치면 사이클이 밀린다 (되돌릴 수 없다)      — 버충 톡톡이 · 장전컨 C
#   중 20  놓치면 그 순간부터 버프가 샌다                — 엄폐컨 · 홀드컨
#   하 10  언제든 끊고 다시 재개할 수 있다                — 상시 톡톡이 · 장전컨 A·B
_PRIO_HIGH, _PRIO_MID, _PRIO_LOW = 30, 20, 10
_PRIO_ALIAS = {"high": _PRIO_HIGH, "mid": _PRIO_MID, "low": _PRIO_LOW}
_PRIO_SEQ = 99      # 명시 시퀀스 — 유저가 시각을 콕 집었다. 등급 밖의 최우선

# 장전컨 정책 이름 → 앵커 표기. **서로 다른 것은 앵커가 무엇이고 `lead`·`margin`의
# 부호가 어느 쪽이냐뿐이다** — 판정식은 전부 `t >= anchor + offset − minus` 한 줄이다.
# 정본: docs/CONTROL.md §장전컨.
#
#   (앵커, 읽는 키, 부호, minus, 기본 등급)
#
# 등급이 여기 붙는 이유: 정책 C는 상(놓치면 사이클이 밀림), D는 중(버프가 샘)이다.
# 앵커 표기를 직접 쓰면 그 이름이 없으므로 기본 하이고, 급하면 `priority`를 적는다 —
# docs/CONTROL.md §택틱 "등급은 기본값과 같아도 명시한다"와 같은 취지다.
_RELOAD_POLICIES: dict[str, tuple[str, str, float, str | None, int]] = {
    "before_fb_end":    ("fb_end",        "lead",   -1.0, None,           _PRIO_LOW),
    "into_fb":          ("next_fb_start", "margin", +1.0, "reload_total", _PRIO_LOW),
    "finish_by_fb_end": ("fb_end",        "margin", -1.0, "reload_total", _PRIO_HIGH),
    "finish_by_own_buff_end":
                         ("own_buff_end",  "margin", -1.0, "reload_total", _PRIO_MID),
}


def _norm_gate(e: dict, who: str) -> None:
    """런타임 게이트를 제자리 정규화한다. 현재는 `B단계 사용자` 한 어휘만 받는다."""
    raw = e.get("gate")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError(
            f"{who}: `gate`는 객체여야 한다 (받은 것 {type(raw).__name__}). "
            f"docs/CONTROL.md §런타임 게이트")
    if extra := set(raw) - set(_GATE_KEYS):
        raise ValueError(
            f"{who}: `gate`에 모르는 키 {sorted(extra)}. "
            f"쓸 수 있는 것: {list(_GATE_KEYS)}. docs/CONTROL.md §런타임 게이트")
    if set(raw) != set(_GATE_KEYS):
        raise ValueError(
            f"{who}: `gate`에는 `burst_stage`와 `burst_user`가 모두 필요하다. "
            f"docs/CONTROL.md §런타임 게이트")
    stage = str(raw["burst_stage"])
    if stage not in ("1", "2", "3"):
        raise ValueError(f"{who}: gate.burst_stage는 1·2·3 중 하나여야 한다 (받은 값 {stage!r}).")
    user = str(raw["burst_user"]).strip()
    if not user:
        raise ValueError(f"{who}: gate.burst_user는 빈 이름일 수 없다.")
    e["gate"] = {"burst_stage": stage, "burst_user": user}


def _norm_when(e: dict, who: str, *, span: bool) -> None:
    """항목의 **언제** 부분을 제자리에서 정규화·검증한다. 정본: docs/CONTROL.md §설정 스키마.

    `window`(상태 창)와 `anchor`(앵커 구간)는 **정확히 하나만** 쓴다. 둘 다 주거나
    둘 다 안 주면 어느 쪽으로 읽히는지가 조용한 결과 차이가 되므로 조립 시점에 끊는다
    (§체계 불변식 ②).

    span : 이 축이 **구간**을 쓰는가. 클릭은 구간이라 `len`이 필수이고, 엄폐는
           "이 시각이 되면 연다"는 **진입 트리거**라 `len`을 받지 않는다.
    """
    has_w, has_a = e.get("window") is not None, e.get("anchor") is not None
    if has_w == has_a:
        raise ValueError(
            f"{who}: 언제인지를 `window`(상태 창)나 `anchor`(앵커 구간) 중 "
            f"**정확히 하나로** 적는다 "
            f"({'둘 다 적혔다' if has_w else '둘 다 없다'}). docs/CONTROL.md §설정 스키마")
    if has_w:
        for k in ("anchor", "offset", "len", "minus", "buff"):
            if k in e:
                raise ValueError(
                    f"{who}: 상태 창(`window`)에는 {k!r}를 줄 수 없다 — 앵커 구간 전용이다. "
                    f"docs/CONTROL.md §설정 스키마")
        _norm_gate(e, who)
        return
    anchor = str(e["anchor"])
    if anchor not in _ANCHORS:
        raise ValueError(
            f"{who}: 모르는 앵커 {anchor!r}. {' · '.join(_ANCHORS)} 중 하나여야 한다. "
            f"docs/CONTROL.md §설정 스키마")
    e["anchor"] = anchor
    if anchor == "own_buff_end":
        buff = str(e.get("buff", "")).strip()
        if not buff:
            raise ValueError(
                f"{who}: `own_buff_end` 앵커에는 `buff` 이름이 필요하다. "
                f"docs/CONTROL.md §설정 스키마")
        e["buff"] = buff
    elif "buff" in e:
        raise ValueError(
            f"{who}: `buff`는 `own_buff_end` 앵커에서만 쓸 수 있다 "
            f"(받은 앵커 {anchor!r}). docs/CONTROL.md §설정 스키마")
    e["offset"] = float(e.get("offset", 0.0))
    if (minus := e.get("minus")) is not None:
        if minus not in _ANCHOR_MINUS:
            raise ValueError(
                f"{who}: 모르는 동적 오프셋 {minus!r}. "
                f"{' · '.join(_ANCHOR_MINUS)} 중 하나여야 한다. docs/CONTROL.md §설정 스키마")
    if span:
        # 길이 없는 구간은 **한 프레임도 열리지 않는다.** 조용히 아무 일도 안 하느니
        # 조립에서 끊는다.
        if e.get("len") is None:
            raise ValueError(
                f"{who}: 앵커 구간에는 `len`(구간 길이, 초)이 필요하다. "
                f"docs/CONTROL.md §설정 스키마")
        if (ln := float(e["len"])) <= 0:
            raise ValueError(f"{who}: `len`은 0보다 커야 한다 (받은 값 {ln}).")
        e["len"] = ln
    elif "len" in e:
        raise ValueError(
            f"{who}: 엄폐는 구간이 아니라 **진입 트리거**라 `len`을 받지 않는다 "
            f"(엄폐 지속 시간은 `duration`이다). docs/CONTROL.md §설정 스키마")
    _norm_gate(e, who)


def _build_reload_when(rl: dict, who: str) -> tuple[dict | None, int]:
    """장전컨의 **언제**를 앵커 표기로 정규화한다. `(앵커 스펙|None, 기본 등급)`.

    정책 A·B·C·D가 전부 `t >= anchor + offset − minus` 한 줄로 접힌다 —
    달랐던 것은 앵커가 무엇이고 `lead`·`margin`의 부호가 어느 쪽이냐뿐이다
    (`_RELOAD_POLICIES`). 정본: docs/CONTROL.md §장전컨.
    """
    if extra := set(rl) - set(_RELOAD_KEYS):
        raise ValueError(
            f"{who}: `reload`에 모르는 키 {sorted(extra)}. "
            f"쓸 수 있는 것: {list(_RELOAD_KEYS)}. docs/CONTROL.md §설정 스키마")
    policy, has_anchor = rl.get("policy", ""), rl.get("anchor") is not None
    if policy and has_anchor:
        raise ValueError(
            f"{who}: `reload.policy`와 `reload.anchor`를 같이 줄 수 없다 — "
            f"한쪽으로 적는다. docs/CONTROL.md §장전컨")
    if has_anchor:
        when = {k: rl[k] for k in ("anchor", "offset", "minus", "gate", "buff") if k in rl}
        _norm_when(when, f"{who}: reload", span=False)
        prio = _PRIO_LOW
    elif not policy:
        if rl.get("if_dry") or rl.get("gate") or rl.get("buff"):
            raise ValueError(
                f"{who}: `reload.if_dry`·`reload.gate`는 엄폐 정책이 있을 때만 의미가 있다. "
                f"docs/CONTROL.md §장전컨")
        return None, _PRIO_LOW
    else:
        # 오타가 조용히 "정책 없음"으로 떨어지면 컨트롤을 켠 줄 알고 결과를 읽게 된다.
        if policy not in _RELOAD_POLICIES:
            raise ValueError(
                f"{who}: 모르는 reload.policy: {policy!r}. "
                f"{' · '.join(_RELOAD_POLICIES)} 중 하나여야 한다. docs/CONTROL.md §장전컨")
        anchor, key, sign, minus, prio = _RELOAD_POLICIES[policy]
        # 정책이 안 읽는 키를 주면 조용히 무시된다 — 지정한 줄 알고 결과를 읽게 되므로 끊는다.
        if (other := {"lead": "margin", "margin": "lead"}[key]) in rl:
            raise ValueError(
                f"{who}: 정책 {policy!r}는 {other!r}를 읽지 않는다 — {key!r}를 쓴다. "
                f"docs/CONTROL.md §설정 스키마")
        dflt = _RELOAD_LEAD_DEFAULT if key == "lead" else _RELOAD_MARGIN_DEFAULT
        when = {"anchor": anchor, "offset": sign * float(rl.get(key, dflt)), "minus": minus}
        if "buff" in rl:
            when["buff"] = rl["buff"]
        if "gate" in rl:
            when["gate"] = rl["gate"]
        _norm_when(when, f"{who}: reload", span=False)
    # `if_dry`는 이번 풀버스트 종료 시각을 기준으로 "다음 풀버스트까지 버티나"를 잰다.
    # `next_fb_start`·`combat_start` 앵커에는 그 기준이 없어 판정이 성립하지 않는다.
    if rl.get("if_dry") and when["anchor"] not in ("fb_end", "own_fb_end"):
        raise ValueError(
            f"{who}: `if_dry`는 `fb_end`·`own_fb_end` 앵커에서만 쓸 수 있다 "
            f"(받은 앵커 {when['anchor']!r}). docs/CONTROL.md §장전컨")
    return when, prio


def _norm_click_entry(raw_e: dict, who: str) -> dict:
    """클릭 스케줄 항목 하나를 정규화·검증한다 (사본을 돌려준다).

    무기 종류를 보지 않으므로 **캐릭터 없이도 부를 수 있다** — `runner/doclint.py`의
    검사 L이 아무도 안 돌린 규칙을 미리 훑는 데 쓴다(`validate_control()`).
    """
    e = dict(raw_e)
    if extra := set(e) - set(_CLICK_ENTRY_KEYS):
        # 모르는 키가 조용히 살아남으면 오타가 "아무 일도 안 함"이 된다 —
        # 앵커 문법이 들어오면서 오타 지면이 넓어졌으므로 여기서 닫는다.
        raise ValueError(
            f"{who}: 클릭 항목에 모르는 키 {sorted(extra)}. "
            f"쓸 수 있는 것: {list(_CLICK_ENTRY_KEYS)}. docs/CONTROL.md §설정 스키마")
    # 둘 다 없으면 종전 기본값(전투 내내)이다 — 종전 표기를 그대로 받는 자리.
    if e.get("window") is None and e.get("anchor") is None:
        e["window"] = "always"
    mode = str(e.get("mode", "auto"))
    if mode not in _CLICK_MODES:
        raise ValueError(
            f"{who}: 모르는 클릭 행위 {mode!r}. "
            f"{' · '.join(_CLICK_MODES)} 중 하나여야 한다. docs/CONTROL.md §체계")
    e["mode"] = mode
    if (window := e.get("window")) is not None:
        window = str(window)
        if window not in _CLICK_WINDOWS:
            raise ValueError(
                f"{who}: 모르는 클릭 구간 {window!r}. "
                f"{' · '.join(_CLICK_WINDOWS)} 중 하나여야 한다. docs/CONTROL.md §체계")
        e["window"] = window
    _norm_when(e, f"{who}: click", span=True)
    # `after_own_fb`는 구간이 아니라 **역산 전용 슬롯**이다. 다른 행위와 짝지으면
    # 창이 열린 것처럼 보이지만 실제로 하는 일이 없다 — 조립에서 끊는다.
    if (window == "after_own_fb") != (mode == "hold_judge"):
        raise ValueError(
            f"{who}: `after_own_fb`와 `hold_judge`는 서로만 짝짓는다 "
            f"(구간이 아니라 시각을 역산하는 슬롯이다). "
            f"받은 것: window={window!r} mode={mode!r}. docs/CONTROL.md §체계")
    # 버스트 버튼 사슬은 끝 시각을 미리 알 수 없는 동적 창이다. 창이 닫히는 틱에 떼는
    # 전용 행위와만 짝지어, `always` 같은 창에 무한 홀드가 걸리는 입력을 막는다.
    if (window == "burst_chain") != (mode == "hold_until_close"):
        raise ValueError(
            f"{who}: `burst_chain`과 `hold_until_close`는 서로만 짝짓는다 "
            f"(받은 것: window={window!r} mode={mode!r}). docs/CONTROL.md §체계")
    if mode == "hold_until_close" and "lead" in e:
        raise ValueError(
            f"{who}: `hold_until_close`는 창이 닫히는 즉시 떼므로 `lead`를 받지 않는다. "
            f"docs/CONTROL.md §홀드")
    return e


def _norm_aim_entry(raw_e: dict, who: str) -> dict:
    """에임 항목 하나를 정규화·검증한다 (사본). 표적 이름이 스크립트에 있는지는 `simulate()`가 보스를 만든 뒤
    대조한다 — 캐릭터만으로는 보스를 모른다. 정본: docs/CONTROL.md §에임."""
    if not isinstance(raw_e, dict):
        raise ValueError(f"{who}: 에임 항목은 객체여야 한다 (받은 것 {type(raw_e).__name__}).")
    e = dict(raw_e)
    if extra := set(e) - set(_AIM_ENTRY_KEYS):
        raise ValueError(
            f"{who}: 에임 항목에 모르는 키 {sorted(extra)}. "
            f"쓸 수 있는 것: {list(_AIM_ENTRY_KEYS)}. docs/CONTROL.md §에임")
    at = e.get("at")
    if not isinstance(at, str) or not at.strip():
        raise ValueError(f"{who}: 에임 항목에는 겨눌 곳 `at`(표적 이름 또는 \"core\")이 필요하다.")
    e["at"] = at.strip()
    if e.get("window") is None and e.get("anchor") is None:
        e["window"] = "always"      # 표적이 살아 있는 동안 내내
    if (window := e.get("window")) is not None:
        if window not in _AIM_WINDOWS:
            raise ValueError(
                f"{who}: 모르는 에임 구간 {window!r}. {' · '.join(_AIM_WINDOWS)} 중 하나여야 한다. "
                f"docs/CONTROL.md §에임")
    _norm_when(e, f"{who}: aim", span=True)
    return e


def validate_control(control: dict, who: str) -> None:
    """컨트롤 스키마의 **어휘**를 캐릭터 없이 검사한다. 어긋나면 `ValueError`.

    조립 시점 검사(`CharState.__init__`)의 부분집합이다 — 무기 종류를 봐야 하는 것
    (풀차지 전용 톡톡이 금지 · `DOWN_Charge` 홀드 금지)은 여기서 판정할 수 없다.

    있는 이유는 **아무도 안 돌린 규칙**이다. `data/char_defaults.json`·`data/tactics.json`의
    조건부 규칙은 조건이 맞는 스쿼드를 돌려야만 조립까지 가므로, 그때까지 오타가 발견되지
    않는다 — `runner/doclint.py`의 검사 L이 이 함수로 미리 훑는다. 어휘의 정본이 한 곳에
    남게 검사 쪽에서 다시 적지 않고 여기를 부른다.
    """
    if extra := set(control) - set(_CONTROL_KEYS):
        raise ValueError(
            f"{who}: `control`에 모르는 키 {sorted(extra)}. "
            f"쓸 수 있는 것: {list(_CONTROL_KEYS)}. docs/CONTROL.md §설정 스키마")
    if extra := set(control.get("burst") or {}) - set(_BURST_KEYS):
        raise ValueError(
            f"{who}: `control.burst`에 모르는 키 {sorted(extra)}. "
            f"쓸 수 있는 것: {list(_BURST_KEYS)}. docs/CONTROL.md §L0")
    if (d := (control.get("burst") or {}).get("delay")) is not None and float(d) < 0:
        raise ValueError(f"{who}: `control.burst.delay`는 0 이상이어야 한다 (받은 값 {d}).")
    raw = control.get("click")
    legacy = [k for k in ("tap_fire", "hold") if control.get(k)]
    if raw is not None and legacy:
        raise ValueError(
            f"{who}: `click`과 종전 키({' · '.join(legacy)})를 같이 줄 수 없다 — "
            f"한쪽으로 적는다. docs/CONTROL.md §체계")
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError(f"{who}: `click`은 항목 리스트여야 한다 (받은 것 {type(raw).__name__}).")
        for e in raw:
            _norm_click_entry(e, who)
    else:
        # 종전 키도 같은 어휘를 쓴다 — `CharState._desugar_click()`이 옮겨 준다.
        if (w := (control.get("tap_fire") or {}).get("window")) is not None:
            if w not in _CLICK_WINDOWS:
                raise ValueError(
                    f"{who}: 모르는 tap_fire.window {w!r}. "
                    f"{' · '.join(_CLICK_WINDOWS)} 중 하나여야 한다. docs/CONTROL.md §체계")
        if p := (control.get("hold") or {}).get("policy", ""):
            if p not in ("own_full_burst", "charge_hold_after_fb"):
                raise ValueError(
                    f"{who}: 모르는 hold.policy: {p!r}. "
                    f'"own_full_burst" 또는 "charge_hold_after_fb"여야 한다. docs/CONTROL.md §홀드')
    if (p := (control.get("cover") or {}).get("policy", "")) and p != "own_full_burst":
        raise ValueError(
            f'{who}: 모르는 cover.policy: {p!r}. 현재 "own_full_burst" 하나다. '
            f"docs/CONTROL.md §버스트 엄폐컨")
    if (aim := control.get("aim")) is not None:
        if not isinstance(aim, list):
            raise ValueError(f"{who}: `aim`은 항목 리스트여야 한다 (받은 것 {type(aim).__name__}).")
        for e in aim:
            _norm_aim_entry(e, who)
    _build_reload_when(control.get("reload") or {}, who)


def _when_label(e: dict) -> str:
    """항목의 **언제**를 사람이 읽는 한 조각으로. 조작 구간 로그·이탈 보고가 함께 쓴다.

    상태 창은 이름 그대로, 앵커 구간은 `앵커@오프셋+길이` (러너 CLI가 받는 표기와 같다).
    """
    if (w := e.get("window")) is not None:
        s = str(w)
    else:
        s = f"{e['anchor']}@{e.get('offset', 0.0):+g}"
        if (minus := e.get("minus")):
            s += f"-{minus}"
        if (ln := e.get("len")) is not None:
            s += f"+{ln:g}"
    if gate := e.get("gate"):
        s += f"[B{gate['burst_stage']}={gate['burst_user']}]"
    return s


def _parse_prio(val, default: int, who: str) -> int:
    """등급 값을 정수로. `"high"`·`"mid"`·`"low"` 별칭과 정수를 함께 받는다.

    오타가 조용히 기본 등급으로 떨어지면 지정한 줄 알고 결과를 읽게 된다 —
    조립 시점에 끊는다(docs/CONTROL.md §체계 불변식 ②).
    """
    if val is None:
        return default
    if isinstance(val, str):
        if val not in _PRIO_ALIAS:
            raise ValueError(
                f"{who}: 모르는 컨트롤 등급 {val!r}. "
                f"{' · '.join(_PRIO_ALIAS)} 또는 정수여야 한다. docs/CONTROL.md §조작자는 한 명")
        return _PRIO_ALIAS[val]
    return int(val)

# ── 기본 config / enemy ────────────────────────────────────────────────────

DEFAULT_CHAR: dict = {
    "level": 400,
    "breakthrough": 3,
    "core_enhancement": 0,
    "affinity": 30,
    "skill_levels": {"1": 10, "2": 10, "3": 10},
    "burst_regen_time": 2.0,
    "equipment": {p: {"level": 5, "skills": []} for p in ["머리", "몸통", "팔", "다리"]},
    "cube": {"name": "렐릭 베어 큐브", "level": 15},
    "console": {"common_level": 180, "class_level": 100, "company_level": 100},
    "collection_stage": "SR15",
    "control": {},  # 컨트롤(톡톡이·장전컨). 스키마·의미는 docs/CONTROL.md
}

DEFAULT_CONFIG: dict = {
    "duration":           180.0,  # 시뮬레이션 시간(초) — 실제 니케 전투 3분
    "burst_switch_delay":  0.1,   # 버스트 단계 전환 딜레이(초)
    "burst_reenter_delay": 0.5,   # reenter 딜레이(초)
    "max_burst_count":    None,   # 최대 풀버스트 횟수 (None = 무제한)
    "burst_sequence":     None,   # 풀버스트별 단계 사용 순서 list[dict[str, list[str]]] (None = 자동)
    "first_burst_time":    3.0,   # 첫 버스트 최소 시작 시간(초) — "fixed" 모드 전용
    # 버스트 게이지 사이클 판정 방식. 정본: docs/mechanics/버스트 게이지.md
    #   "fixed"      — 종전 모델. 풀버스트 종료 후 burst_regen_time(기본 2.0초) 뒤 1단계,
    #                  첫 버스트는 first_burst_time. 게이지는 계산되어 로그에 남지만
    #                  사이클을 판정하지는 않는다(두 모델 비교용).
    #   "accumulate" — 실누적. 게이지가 100%에 닿아야 1단계가 나간다.
    #                  burst_regen_time·first_burst_time을 **둘 다 무시한다**(유저 결정).
    "burst_gauge_mode":   "fixed",
    # 카메라가 보고 있는 니케. 풀차지 게이지 배율은 **카메라를 받은 니케에게만** 붙는다
    # (2024-04-25 패치). None이면 컨트롤에서 유도한다 — _resolve_cameras().
    # str 하나 · 이름 list · ""(아무도 안 봄) 를 받는다.
    "camera":             None,
    # 카메라를 몇 명이 나눠 가질 수 있는가. 정본: docs/CONTROL.md §카메라.
    #   "single" — 정확히 1명(기본). 실제 게임의 제약이다.
    #   "shared" — 컨트롤을 켠 전원이 받는다. 컨트롤 정책이 이미 "여러 명 동시 조작"을
    #              비현실적 상한으로 허용하고 있어(docs/CONTROL.md), 그 상한에 카메라만
    #              혼자 1명으로 남아 있으면 조작과 카메라가 따로 논다. 같은 태도로 맞춘다.
    # **버충 컨트롤은 모드와 무관하게 언제나 단독이다** — 아래 _resolve_cameras().
    "camera_mode":        "single",
    # 레이어 2 「저지 우선 타격」 — 카메라를 가진 니케가 산 저지원 → 쫄몹 → 안 깨면 벌칙 분기가 오는 파츠 → 본체 순으로
    # 겨눈다(유저 결정 2026-09-18 · 09-19, 좌표 on/off 공통 — `boss_pattern` §조준). 레이어 1은 카메라 니케도 쫄몹 → 본체.
    # 엔진 기본은 레이어 1(오토 — 저지 때도 에임을 안 옮긴다)이고, 러너가 레이어 2로 켠다(`spec.build_config`).
    # 패턴 모드 보스가 아니면 읽지 않는다. 정본: docs/CONTROL.md §에임
    "aim_interrupt":      False,
    # 조작자는 한 명이라는 제약을 어떻게 다룰지. 정본: docs/CONTROL.md §조작자는 한 명.
    #   "solo"   — 카메라 한 대(기본). 겹치면 **등급이 급한 쪽**이 가져가고(같은 등급이면
    #              후입 우선) 뺏긴 쪽은 조작이 풀린다(엄폐 해제·홀드 발사). 실제 조작에
    #              가장 가깝다.
    #   "warn"   — 전원 실행하고 겹침을 결과에 경고로 싣는다. 비현실적 상한이다.
    #   "strict" — 겹치는 순간 실패. 유저가 시각을 갈라 적는다.
    "control_mode":       "solo",
    # 스쿼드 시퀀스 — **조작자 관점의 탈출구.** 캐릭터 시퀀스(`control["sequence"]`)가 한 니케의
    # 조작을 시각으로 찍는다면, 이쪽은 카메라 이동과 전체 엄폐를 찍는다.
    #   [{"t": 12.0, "action": "focus",     "target": "프리카"},
    #    {"t": 30.0, "action": "cover_all", "duration": 1.0}]
    # `focus`의 target이 빈 문자열이면 자동(조율)으로 돌려준다. 정본: docs/CONTROL.md §스쿼드 시퀀스.
    "sequence":           None,
    "allow_unparsed":     False,  # True면 스킬 미파싱 캐릭터를 스킬 0개로 돌린다 (파싱 전 신캐 전용)
    # 난수(크리·코어히트) 처리 방식.
    #   "random"   — 히트마다 확률 판정(기본, 인게임과 동일한 분산)
    #   "expected" — 확률 대신 기대값을 태워 결과를 결정론적으로 만든다.
    #                시드·반복 평균 없이 1회 실행으로 기대딜이 나온다.
    "rng_mode":           "random",
    # 엄폐물 체력 기본값 — **임의값이다.** CDN roledata·테이블에 엄폐물 체력이 없다(2026-09-14 확인).
    # 보스 공격 패턴(`enemy["patterns"]`의 attack)이 있을 때만 쓰인다. 큐브·소장품·스킬의
    # 엄폐물 최대 체력 ▲(`cover_hp_pct`)는 이 위에 얹는다(`BuffManager.cover_max_hp`).
    "cover_hp":           2000000.0,
}

# 기대값 모드의 보스 공격 대상 난수 시드. 모드의 약속(시드와 무관하게 같은 결과)을 지키려고 고정한다.
_EXPECTED_BOSS_SEED = 0

DEFAULT_ENEMY: dict = {
    "def":                  31784,  # 솔로 레이드 모의전 보스 Lv 400 방어력 (DATA_VERIFY §레이드 보스 스탯)
    "code":                 None,
    "core_px":              0,    # 코어 직경(px). 0이면 코어 없음, >0이면 코어히트율 확률 계산
    "has_parts":            False,# 파괴 가능 파츠 보유 보스. part_hit_count / part_dmg_pct의 전제
    # 파츠 파괴 주기(초) — **간단 모드의 칸**. >0이면 그 주기마다 `event:part_destroy`를 쏜다(있지도 않은 파괴를
    # 반복한다). 0이면 무발동. 패턴 모드에서는 파괴가 표적이 실제로 깨질 때 나가므로 적으면 거절한다(`boss_mode`)
    "part_break_interval":  0.0,
    "optimal_range_weapons": [],  # 적정거리 적용 무기군 목록 e.g. ["SG", "SMG"]
    # 보스 거리 — 있으면 무기군 목록 대신 니케마다 적정 구간(CDN bonusrange)과 비교한다
    # (`buff_manager.in_optimal_range`). 없으면(None) 종전 목록이라 기본 경로가 안 흔들린다
    "distance":             None,
    # 좌표 모드 — 패턴 모드의 스위치(패턴 없이 켜면 거절). 있으면({} 포함) 표적을 화면 좌표로 적고 에임·탄 분포·
    # 관통·폭발 원으로 「어디에 맞는가」를 푼다. 정본은 `calculator/boss_pattern.py` §모드·§좌표 모드. 없으면(None) 좌표 off
    "coord":                None,
    # 보스 공격력 — attack 패턴의 피해 산정에만 쓴다. 솔로 레이드 보스 Lv 400(boss_pattern.DEFAULT_BOSS_ATK)
    "atk":                  DEFAULT_BOSS_ATK,
    # 보스 패턴 — 위 넷을 시간에 따라 덮어쓰고 딜 게이트·표적을 연다. 포맷의 정본은
    # `calculator/boss_pattern.py`. **비어 있으면 스케줄러를 만들지 않아** 종전과 한 자리도 같다.
    "patterns":             [],
}

# `move` 패턴이 받는 무기군. 정본은 로스터 데이터라 목록을 따로 적지 않는다.
_WEAPON_TYPES: frozenset[str] = frozenset(
    v["weapon_type"] for v in _NIKKE.values() if isinstance(v, dict) and v.get("weapon_type"))


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


def _core_hit_prob(spread_px: float, core_px: float) -> float:
    """탄착군 직경·코어 크기로부터 코어히트 확률 반환 (power 모델 P = min(1, (r_c/R)^n)).

    직경 자체는 `CharState._current_spread()`가 만든다 — 캐릭터별 CDN 값에 예열
    진행도와 명중률을 얹은 값이다.

    R  = spread_px / 2   (탄착군 반경)
    r_c = core_px / 2    (코어 반경)
    """
    R = max(spread_px, 1.0) / 2.0
    r_c = core_px / 2.0
    return min(1.0, (r_c / R) ** _MODEL_N)


def _reach_hit(enemy: dict, ht: dict, res: dict, buffs: dict, *, parts_skill: bool,
               base_atk: float, weapon: dict, expected: bool) -> dict:
    """좌표 off의 다중 타격 — 이 발이 닿는 단계 상한과 표적 하나에 넣을 몫. 파츠 쪽(`reach`·`part_damage`)과
    저지원 쪽(`interrupt_reach`·`interrupt_damage`)을 따로 낸다. `HitEvent`에 그대로 펼쳐 넣는다.
    정본: boss_pattern.py §파츠 다중 타격.

    **닿을 표적이 없으면 빈 dict다** — 대미지를 다시 산정하지 않고 난수도 안 먹는다. 보스 패턴이 없거나
    reach 표적이 없는 전투는 여기서 곧바로 빠져 계산이 한 자리도 안 달라진다.
    """
    need_p = enemy.get(PART_REACH_KEY, 0)
    need_i = enemy.get(INTERRUPT_REACH_KEY, 0)
    if not need_p and not need_i:
        return {}
    ranges = dict(explosion=ht["is_projectile_explosion"], pierce=ht["is_pierce_damage"],
                  explosion_range=buffs.get("explosion_range", 0.0),
                  pierce_range=buffs.get("pierce_range", 0.0))
    out: dict = {}

    def again(is_part: bool) -> int:
        # 같은 발을 표적에 — 코어는 없다. 크리는 본체 판정을 그대로 쓴다(난수 안 먹음)
        pht = dict(ht, is_part=is_part, is_core=False, core_prob=None, is_core_damage=False,
                   crit_override=None if expected else res["is_crit"], _debug_factors=False)
        return calc_damage(base_atk=base_atk, buffs=buffs, weapon=weapon, hit_type=pht,
                           enemy_def=enemy.get("def", 31784), expected=expected)["damage"]

    if need_p:
        reach = hit_reach(parts_skill=parts_skill, **ranges)
        if reach >= need_p:
            out.update(reach=reach, part_damage=again(True))        # 파츠 대미지 ▲가 붙는다
    if need_i:
        # 「파츠 포함」 전체기는 저지원에 안 닿는다 — 단계 상한을 그것 없이 잰다
        ireach = hit_reach(**ranges)
        if ireach >= need_i:
            out.update(interrupt_reach=ireach, interrupt_damage=again(False))
    return out


def _notify_frac(bm, key: str, name: str, frac: float, fire) -> None:
    """확률적으로 일어나는 히트 이벤트를 소수 누적으로 발화한다.

    확률 판정 모드에서는 frac이 0/1이라 그대로 0회 또는 1회 발화한다.
    기대값 모드에서는 히트마다 확률(0~1)이 쌓이므로 (key, 캐릭터)별로 누적해
    1.0을 넘길 때마다 발화한다 — 횟수를 세는 트리거
    (`crit_hit_count:N` 이브, `core_hit_count:N` 루드밀라 : 윈터 오너)가
    난수 없이 **같은 장기 빈도**로 발동하게 하는 결정론적 대응이다.
    개별 발동 시점은 확률 판정과 달라지지만 기대 발동 횟수는 같다.
    """
    if frac >= 1.0:
        fire()
        return
    if frac <= 0.0:
        return
    acc = bm.state["rng_acc"]
    k = (key, name)
    acc[k] = acc.get(k, 0.0) + frac
    while acc[k] >= 1.0:
        acc[k] -= 1.0
        fire()


def _bullet_core_fracs(core_fracs: list[float], muzzles: int) -> list[float]:
    """펠릿 단위 코어 확률을 **탄(총구) 단위**로 접는다 — `hit_count` 1회당 1값.

    `not_core` 조건(「명중 시 코어가 아니라면」)이 트리거를 일으킨 그 탄의 코어 여부를
    읽는데, 명중은 탄 단위이고 코어 판정은 펠릿 단위라 묶음 평균을 넘긴다.
    펠릿 1이면 히트 하나의 값 그대로다.
    """
    per = max(1, len(core_fracs) // max(1, muzzles))
    out = []
    for m in range(muzzles):
        chunk = core_fracs[m * per:(m + 1) * per]
        out.append(sum(chunk) / len(chunk) if chunk else 0.0)
    return out


# ── CharState (캐릭터별 발사 상태) ────────────────────────────────────────

class CharState:
    """캐릭터 1명의 발사 루프 상태 관리. 버스트 사용 중에도 발사 계속."""

    def __init__(self, char: dict, base_atk: float, enemy_code: str):
        self.char = char
        self.name = char["name"]
        self.base_atk = base_atk

        weapon_data = _NIKKE[self.name]

        # 로스터 코드 상성은 전투 내내 고정이지만, `element_code_override`는 버프라
        # 활성 여부를 조회 시점에 봐야 한다 → element_match()가 둘을 합친다.
        self.enemy_code = enemy_code
        self.base_element_match = is_element_match(
            weapon_data.get("element_code", ""), enemy_code)

        self.burst_stage: str = weapon_data["burst_stage"]
        self.weapon = weapon_data
        self.weapon_type = weapon_data["weapon_type"]
        # 무기 변경 중에도 안 바뀌는 원래 무기 타입. 「투사체 폭발 대미지 ▲」처럼
        # **기본 무기**로 판정하는 항이 쓴다 (유저 확인, 2026-08-25).
        self.base_weapon_type = self.weapon_type
        # CDN 발사 입력 방식. `UP`(손 떼서 발사) / `DOWN_Charge`(풀차지 자동발사) /
        # `DOWN`(비차지). 프리뷰 캐릭터는 CDN 레코드가 없어 빈 문자열이다.
        self.input_type: str = weapon_data.get("input_type", "")
        # 풀차지 전용 = 끊어쏘기(톡톡이) 불가. 유도는 parse_nikke.py.
        self.full_charge_only: bool = bool(weapon_data.get("full_charge_only", False))

        mech = _MECHANICS["weapon_type_defaults"][self.weapon_type]
        self.mech = mech
        # 발사 방식. "auto" / "auto_warmup" / "charge".
        # **차지 여부는 무기 유형과 독립된 축이다** — SR/RL이라고 차지인 게 아니고 반대도
        # 마찬가지다. 정본은 CDN `조작 타입`에서 온 `is_charge`이고(parse_nikke.py),
        # 무기군 기본값의 `type`은 그 키가 없는 프리뷰 캐릭터용 폴백일 뿐이다.
        #   RL 파스칼 = 비차지(`[차지 공격이 불가능한 무기]`) — 종전에는 RL이라는 이유로
        #   charge로 잡혀 `charge_time`이 없어 조립부터 터졌다.
        # 무기 변경 모드의 차지 여부는 여기가 아니라 `_tick_weapon_change()`가 정한다.
        _is_charge = weapon_data.get("is_charge")
        if _is_charge is None:
            self.fire_mode: str = mech["type"]
        elif _is_charge:
            self.fire_mode = "charge"
        else:
            self.fire_mode = "auto" if mech["type"] == "charge" else mech["type"]

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
        # post_fire_delay·cover_during_delay는 CDN에서 유도한다 — 아래 차지 분기 참조.
        self.cover_during_delay: bool = False
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

        # 탄착군(px). CDN `start/end_accuracy_circle_scale` + `accuracy_change_pershot`.
        # 지속 사격으로 start → end로 좁혀지며(MG 예열·프리바티 : 언카인드 메이드),
        # 그 위에 명중률이 곱해진다 — `_current_spread()`.
        self.spread_start: float = float(
            weapon_data.get("spread_start", _FALLBACK_SPREAD))
        self.spread_end: float = float(
            weapon_data.get("spread_end", self.spread_start))
        _sp_step = float(weapon_data.get("spread_change_pershot", 0) or 0)
        # 예열 완료까지의 발수. **발수에 선형이라는 건 우리 가정이다** —
        # CDN은 발당·초당 두 수치만 주고 곡선 모양은 주지 않는다(DATA_VERIFY ⬜).
        self._spread_shots_needed: float = (
            abs(self.spread_start - self.spread_end) / _sp_step if _sp_step else 0.0)

        # 총구 수: 1회 발사에 동시에 나가는 탄 묶음 수. 실제 히트 수 = pellets × muzzles.
        # CDN damage(= 스킬 텍스트의 대미지 표기)는 총구당 값이라 총량이 총구 수만큼 늘어난다.
        self.muzzles: int = int(_pick("muzzles", _delay_exc, weapon_data, mech, default=1))

        # 히트당 버스트 게이지(%). 한 발이 만드는 게이지 = burst_energy × pellets × muzzles.
        # CDN `target_burst_energy_pershot`을 parse_nikke가 /10000해 내려 준 값이고,
        # 해석 계층은 pellets·muzzles와 같다. 정본: docs/mechanics/버스트 게이지.md
        self.burst_energy: float = float(
            _pick("burst_energy", _delay_exc, weapon_data, mech, default=0.0))

        # charge — 무기 유형이 아니라 `fire_mode`가 정한다(위 `is_charge` 참조)
        if self.fire_mode == "charge":
            charge_time_raw = char.get("charge_time_frames")
            if charge_time_raw is not None:
                self.charge_time_base: float = charge_time_raw / 60.0
            elif "charge_time" in weapon_data:
                self.charge_time_base = weapon_data["charge_time"]
            else:
                raise ValueError(
                    f"{self.name}: 차지 무기인데 charge_time이 없다 — 무기스킬 원문의 "
                    f"`차지 시간: N초` 파싱이 실패했다(scraper/parse_nikke.py)")
            # 발사 후 딜레이·엄폐 여부를 CDN `input_type`·`maintain_fire_stance`에서
            # 유도한다. 유도식과 근거는 `docs/mechanics/CDN 발사 데이터.md`가 정본이다.
            #   DOWN_Charge — 풀차지가 차면 자동 발사. 딜레이가 없고, 발사 사이에
            #                 엄폐 자세를 거치지 않는다(유저 확인 2026-08-27)
            #   UP          — 사격 전 0.22 + 사격 후 max(0.16, 자세 유지)
            #                 (2분할의 정본은 docs/CONTROL.md §톡톡이)
            _hold = weapon_data.get("fire_stance_hold")   # None = CDN 미수집(프리뷰)
            if _hold is None:
                _derived_delay = _delay_wt.get("post_fire_delay", mech.get("post_fire_delay", 0.0))
                _derived_cover = False
            elif self.input_type == "DOWN_Charge":
                _derived_delay, _derived_cover = 0.0, False
            else:
                _derived_delay = _TAP_MIN_HOLD + max(_TAP_CUTTABLE_DELAY, _hold)
                _derived_cover = (_hold == 0.0)
            self.post_fire_delay: float = _delay_exc.get("post_fire_delay", _derived_delay)
            # 엄폐 니케: 재장 ≥100%일 때 post_fire_delay 중 자동재장전 (장탄 유지)
            self.cover_during_delay = _delay_exc.get("cover_during_delay", _derived_cover)
            # DOWN_Charge 전용 발사 주기 하한. 차지속도 100%로 차지가 0초가 되어도
            # 무한 연사가 되지 않게 잡는다 — 신데렐라 `무결한 유리 2`가 그 경우다.
            # UP의 rate_of_fire는 전원 60rpm인 센티넬이라 쓰지 않는다.
            self._min_fire_cycle: float = (
                1.0 / self.fire_rate
                if self.input_type == "DOWN_Charge" and self.fire_rate else 0.0)
        else:
            self.charge_time_base = 0.0
            self.post_fire_delay = 0.0
            self._min_fire_cycle = 0.0
        self._charge_phase: str = "ready"
        self._charge_start_t: float = 0.0
        self._charge_end_t: float = 0.0
        self._post_delay_end_t: float = 0.0
        # 무기 변경 모드 진입 재장전이 끝나는 시각 (-1 = 진행 중 아님)
        self._wc_entry_reload_until: float = -1.0

        # SG (계수를 나누는 단위. 히트 수는 self.muzzles를 곱한 값)
        self.pellets: int = int(_pick("pellets", _delay_exc, weapon_data, mech, default=1))

        # 클립 무기 여부 (일부 SG/RL). `reload_time`에 적힌 짧은 값은 **클립 1회** 시간이고,
        # 한 번에 채우는 건 탄창의 1/3뿐이다. 오토는 이 클립 장전을 3연속으로 굴려 탄창을
        # 채우므로 빈 탄창에서의 실효 재장전 시간은 `reload_time × 3` — 일반 무기와 비슷해진다
        # (유저 확인, 2026-08-19). 처리는 _finish_reload()·_reload_total_duration().
        # 클립 수는 CDN `reload_bullet`에서 유도한 `clip_count`가 정본이다(3300 → 3회).
        # weapon_mechanics.json의 `clip_characters` 목록은 프리뷰처럼 CDN 값이 없는
        # 캐릭터를 위한 폴백으로만 남는다 — 전수 대조에서 두 출처는 14명 그대로 일치했다.
        _clip_chars = _MECHANICS.get("clip_characters", {}).get(self.weapon_type, [])
        self.clip_count: int = int(_pick(
            "clip_count", _delay_exc, weapon_data,
            default=3 if self.name in _clip_chars else 1))
        self.is_clip: bool = self.clip_count > 1

        self._in_weapon_change: bool = False
        # 이 재장전이 무기 변경 모드 안에서 시작됐는가 (모드 탄창 vs 원래 무기 탄창)
        self._reload_in_weapon_change: bool = False
        self._wc_shots: int = 0             # 현재 무기 변경 세션에서 실제 발사한 발수
        self._wc_new_session: bool = False  # 이번 tick이 세션 첫 진입인가
        # `first_damage_coeff`(원문 `최초 대미지`)의 레벨 환산값. 세션 첫 발에만 쓴다.
        # 없으면 None. _tick_weapon_change()가 매 tick 세팅한다.
        self._wc_first_coeff: float | None = None
        self._wc_normal_coeff: float | None = None  # 같은 세션의 `일반 대미지` 계수
        # 연사 무기 모드는 진입 시 self.ammo를 모드 장탄으로 덮어쓴다(원래 장탄은 버린다).
        # 모드가 끝날 때 되돌려 놓아야 그 값이 원래 무기로 새어 나가지 않는다.
        self._wc_ammo_borrowed: bool = False
        # 이번 모드 종료에서 장탄 만탄 복구를 이미 했는가. 종료 경로가 둘이라
        # (발수 소진 = `_tick_weapon_change` / 지속시간 만료·토글 해제 = `tick`)
        # 플래그 없이 양쪽에서 채우면 **두 번 채워진다** — 발수 소진으로 끝난 모드의
        # `event:state_end` 장탄 조작(라플라스 : 얼티밋 히어로 `탄환 100% 제거`)이
        # 다음 tick의 복구에 덮여 사라지고, 그만큼 재장전이 통째로 없어진다.
        self._wc_ammo_restored: bool = False
        # `max_ammo_buff_applies` 모드의 실효 최대 장탄. **장탄을 채우는 사건에만 다시 잰다** —
        # 모드 진입과 재장전 완료 두 가지뿐이고, 원문 괄호구가 각 캐릭터에게 의미 있는 쪽을
        # 지목한다(라플라스 : 얼티밋 히어로 `사용 무기 변경 시` — 모드 안에 재장전이 없다 /
        # 신데렐라 : 크리스탈 웨이브 `재장전 완료 시` — 모드 안에서 재장전한다). 매 tick 다시
        # 재면 모드 도중 장탄 버프가 붙고 끊길 때마다 종료 조건(`모든 탄환 발사 시`)만 흔들려
        # **탄이 마른 채 끝나지 않는 모드**가 생긴다.
        self._wc_ammo_full: int | None = None

        # 모드 지정 플래그: 수동 재장전으로 진입하는 weapon_change 모드를 쓰는가.
        # 진입에 필요한 재장전만 삽입하고 진입 후에는 삽입하지 않아 모드를 유지한다.
        self.weapon_mode_swap: bool = bool(char.get("weapon_mode_swap", False))

        # ── 컨트롤 (유저 조작 재현). 정본: docs/CONTROL.md ─────────────
        control = char.get("control") or {}
        # 어휘 검사는 **한 곳에서** 한다 — doclint 검사 L이 부르는 그 함수다. 여기서만
        # 하면 아무도 안 돌린 규칙의 오타가 안 잡히고, 저기서만 하면 호출자가 손으로 준
        # 컨트롤이 안 잡힌다. 무기 종류를 봐야 하는 검사는 아래에 따로 남는다.
        validate_control(control, self.name)

        # 캐릭터 단위 등급 — 요소가 따로 적지 않았을 때의 기본값이다. 없으면(None) 요청
        # 종류별 기본 등급을 쓴다(`_prio_of()`). 정본: docs/CONTROL.md §조작자는 한 명.
        self._ctrl_priority: int | None = (
            None if control.get("priority") is None
            else _parse_prio(control["priority"], 0, self.name))

        # 클릭 스케줄 — **"언제 무엇을"을 입력으로 받는다.** 정본: docs/CONTROL.md §체계.
        # 좌클릭 하나에 세 행위가 실려 있다: 짧게 끊기(`tap`) · 들고 있기(`hold`) ·
        # 차면 즉발(`auto`). 어느 구간에서 무엇을 할지는 **유저가 적고 코드는 판정하지
        # 않는다** — 엄폐가 `_enter_cover()` 한 입구로 모여 정책 간 우선순위가 사라진 것과
        # 같은 취지다. 종전 키(`tap_fire`·`hold`)도 같은 리스트로 정규화한다.
        self._click_sched: list[dict] = self._build_click_schedule(control)
        # 에임 스케줄 — 좌표 모드에서 카메라를 잡았을 때 어디를 겨누는가(먼저 맞는 항목이 이긴다).
        # 표적 이름은 simulate()가 보스 스크립트와 대조한다. 정본: docs/CONTROL.md §에임
        self._aim_sched: list[dict] = [_norm_aim_entry(e, self.name) for e in (control.get("aim") or [])]
        # 발사체 폭발 범위(CDN 원값, RL만) — 좌표 모드의 폭발 반지름 기준. 없으면 RL 기본값
        self._explosion_cdn: float | None = _NIKKE.get(self.name, {}).get("spot_explosion_range")
        self.tap_fire: bool = any(e["mode"] == "tap" for e in self._click_sched)
        # 이번 차지에 고른 항목의 값 (차지 시작 시점에 래치 — `_tick_charge()`)
        self._tap_hold: float = 0.0     # 누름 시간 = 사격 전 딜레이 + 차지
        self._tap_charge: float = 0.0   # 그중 실제로 차지되는 시간
        self._tap_release: float = 0.0
        self._tap_post: float = 0.0
        self._tap_this_shot: bool = False
        # 톡톡이 중 주기적으로 풀차지 한 발을 섞는다 — `풀 차지 공격 시` 버프를 유지하려고
        # 하는 조작이다. 논차지 샷은 `full_charge_hit`를 발동시키지 않으므로, 톡톡이만
        # 켜면 그 버프가 통째로 죽는다 (밀크 : 블루밍 바니 `관통 특화` 6초).
        self.tap_full_charge_interval: float = 0.0
        self._last_full_charge_t: float = -1e9
        self._force_full_charge: bool = False
        self._wc_skill_damage: bool = False
        self._wc_name: str = ""

        # 장전컨: 엄폐로 재장전을 유리한 구간에 밀어 넣는다. 정책은 **엄폐 구간의 생산자**이지
        # 재장전을 직접 거는 게 아니다 — 실행층은 아래 §컨트롤 실행층 참조.
        rl = control.get("reload") or {}
        # **언제 엄폐를 여는가**는 앵커 표기 하나로 적는다 — 종전 정책 이름도 여기서
        # 같은 표기로 desugar된다(`_RELOAD_POLICIES`). 정본: docs/CONTROL.md §장전컨.
        self.reload_when: dict | None
        self.reload_when, _rl_prio = _build_reload_when(rl, self.name)
        # 비버스트에 탄이 마를 때만 건다 (`fb_end`·`own_fb_end` 앵커 전용 — 다음 풀버스트까지
        # 버티는지를 재려면 이번 풀버스트 종료 시각이 있어야 한다). 남은 장탄으로 풀버스트
        # 잔여 구간 + 다음 비버스트 구간을 버틸 수 있으면 엄폐하지 않는다.
        self.reload_if_dry: bool = bool(rl.get("if_dry", False))
        # 등급 — 정책 C는 상이다. 목적이 "만탄으로 버스트 게이지 충전 창을 여는 것"이라
        # 놓치면 그 사이클의 버충이 통째로 날아간다(유저 확인 2026-08-29). A·B는 재장전을
        # 유리한 구간에 밀어 넣는 것이라 놓쳐도 다음에 다시 하면 된다. D는 버프 만료를
        # 놓치면 그 순간부터 버프가 새므로 중이다.
        self.reload_priority: int = self._prio_of(rl, _rl_prio)
        # 엄폐 지속 시간(초). None이면 재장전이 끝나는 순간까지만 엄폐한다
        self.reload_cover_dur: float | None = (
            None if rl.get("duration") is None else float(rl["duration"]))
        # 이미 처리한 앵커 시각 (사이클당 1회 보장)
        self._reload_ctrl_anchor: float = -1.0
        # 탄충 취소: 재장전 중에 탄환 충전이 들어와 탄창이 꽉 차면 재장전을 끊고 즉시 사격한다.
        # 오토는 이걸 하지 않는다 (유저 확인) — 그래서 기본 동작이 아니라 컨트롤이다.
        self.reload_cancel_on_full: bool = bool(rl.get("cancel_on_full", False))

        # 버스트 엄폐컨: 본인이 버스트를 쓴 사이클의 풀버스트 동안 **한 발도 쏘지 않는다.**
        # 장전컨과 같은 원시타입(cover)을 쓰지만 목적이 다르다 — 재장전을 유리한 구간에
        # 밀어 넣는 게 아니라, 발수로 소모되는 버프(duration_bullets)를 쓰지 않고 스킬
        # 대미지 구간까지 끌고 가는 컨트롤이다. 재장전은 그 구간에서 따라오는 부산물이다.
        cv = control.get("cover") or {}
        self.cover_policy: str = cv.get("policy", "")
        self.cover_extend: float = float(cv.get("extend", 0.0))
        # 등급 중 — 자리를 비우면 자동 사격이 재개돼 **그 순간부터 발수 소모 버프가 샌다.**
        self.cover_priority: int = self._prio_of(cv, _PRIO_MID)
        self._cover_ctrl_anchor: float = -1.0
        # 같은 창을 엄폐와 홀드가 함께 노리면 **엄폐가 이기고 홀드는 소리 없이 죽는다**
        # (`_enter_cover()`가 `_hold_release_t`를 지운다 — 엄폐 중에는 클릭이 불가능하므로
        # 그 자체는 옳다). 둘을 같이 켠 건 의도 충돌이라 조립 시점에 끊는다 — 목적이 같고
        # 수단만 다른 두 컨트롤이라(docs/CONTROL.md §버스트 엄폐컨) 하나만 골라야 한다.
        # 앵커 구간(`own_fb_end` + 오프셋)으로 적은 홀드가 풀버스트와 겹치는 경우는
        # 조립 시점에 판정할 수 없다(오프셋·길이가 런타임 값과 만나야 정해진다).
        # 상태 창으로 적은 것만 잡고, 그 한계는 docs/CONTROL.md §버스트 엄폐컨에 적어 둔다.
        if self.cover_policy == "own_full_burst" and any(
                e.get("window") == "own_full_burst" for e in self._click_sched):
            raise ValueError(
                f"{self.name}: 같은 창(own_full_burst)에 엄폐컨과 홀드를 같이 걸 수 없다 — "
                f"엄폐 중에는 클릭이 불가능해 홀드가 무시된다. 하나만 고른다. "
                f"docs/CONTROL.md §버스트 엄폐컨")

        # 홀드(차지 유지): 풀차지가 끝나도 떼지 않고 지정 시각까지 들고 있는다 (차지형 전용).
        # 시퀀스로 시각을 직접 찍거나, 아래 홀드컨 정책이 사이클마다 시각을 계산해 준다.
        self._charge_full_t: float = -1.0   # 풀차지 도달 시각(래치). <0이면 아직 차지 중
        self._hold_release_t: float = -1.0  # 떼기 시각. <0이면 홀드 안 함
        # 끝 시각을 미리 모르는 상태 창 홀드. 지금은 `burst_chain` 하나이며, 창이 닫힌 틱에
        # `_apply_click_schedule()` 또는 solo 조율의 `_release_control()`이 즉시 푼다.
        self._hold_until_close_entry: dict | None = None

        # 홀드컨의 떼기 시각은 클릭 스케줄이 매 틱 계산한다 — `_apply_click_schedule()`.
        # **버스트 엄폐컨과 목적이 같고 수단만 다르다**: 둘 다 발수로 소모되는 버프를 일반
        # 공격에 흘리지 않는 컨트롤이고, 차지형은 엄폐 대신 홀드를 쓴다(들고 있는 동안
        # 차지 배율까지 챙기므로 더 이득이다).
        self._hold_ctrl_anchor: float = -1.0

        # `charge_hold:N` 판정용 상태 (밀크 : 블루밍 바니 부끄러움).
        # 풀차지 도달 후 N초를 넘긴 순간 1회만 발동한다 — 계속 들고 있어도 재판정하지 않는다.
        self._charge_hold_fired: set[str] = set()
        # `charge_hold_after_fb` 정책이 이번 사이클에 잡아 둔 시각.
        # 차지를 **이 시각에 시작**해야 판정이 원하는 곳(`_ch_judge_t`)에 떨어진다.
        self._ch_charge_start_t: float = -1.0
        self._ch_judge_t: float = -1.0

        # ── 컨트롤 실행층 ────────────────────────────────────────────────
        # 조작은 구간이다. **엄폐 중에는 사격도 차징도 물리적으로 불가능**하므로 두 컨트롤은
        # 애초에 충돌할 수 없다 — 정책 간 우선순위 판단이 필요 없는 이유다. 액션을 만드는
        # 생산자가 정책(기본 전략)이든 명시 시퀀스든 실행층은 구분하지 않는다.
        # 지금 열려 있는 조작 구간 (조작자 관점 로그 — docs/CONTROL.md §두 관점)
        self._ctrl_open: dict | None = None
        self._cover_all: bool = False   # 지금 엄폐가 space(전체 엄폐)로 걸린 것인가
        # 조작자 배타(카메라 한 대) — docs/CONTROL.md §조작자는 한 명.
        # 같은 틱에 여럿이 열려 하면 조율 단계가 주인을 정하고, 뺏긴 쪽은 조작이 풀린다.
        self._ctrl_want_prev: bool = False   # 직전 틱에도 원했나 (새 요청 = 후입 판정)
        self._ctrl_anchor_kind: str = ""     # 지금 연 구간이 쓴 앵커 종류
        self._ctrl_anchor_val: float = -1.0
        self._reentry_used: set = set()      # 되돌린 앵커 (사이클당 재진입 1회)
        # 지금 열려 있는 조작(엄폐·홀드)이 어느 등급으로 열렸나. **유지 요청은 연 정책의
        # 등급을 물려받는다** — 열 때는 상이던 조작이 유지 중에 떨어지면 그대로 뺏긴다.
        self._ctrl_open_prio: int = 0
        self._cover_until: float = -1.0         # >0이면 엄폐 중 (해제 예정 시각)
        self._cover_until_reload: bool = False  # 재장전이 끝날 때까지 엄폐 (duration 미지정)
        # 유한 엄폐가 탄창 0인 채 끝나면 다음 클립 1회가 채워진 직후 재장전을 끊는다.
        # 0발 상태에서 즉시 취소하면 자동 재장전이 곧바로 다시 걸려 조작이 표현되지 않는다.
        self._reload_cancel_after_clip: bool = False
        # 명시 시퀀스 — 정책으로 표현 못 하는 조작을 시각으로 직접 적는 통로.
        #   [{"t": 45.0, "action": "cover", "duration": 1.5},
        #    {"t": 60.0, "action": "hold",  "until": 62.5}]
        self._ctrl_seq: list[dict] = sorted(
            control.get("sequence") or [], key=lambda a: float(a.get("t", 0.0)))
        self._ctrl_seq_i: int = 0

        # 풀차지 홀딩은 **떼는 시점을 유저가 고르는** 조작이라 `DOWN_Charge`에는 없다 —
        # 차지가 차는 순간 자동으로 나가 버리기 때문이다(유저 확인 2026-08-27).
        # 톡톡이 게이트와 달리 `full_charge_only`를 보지 않는다: 홍련 : 흑영·레이븐은
        # 풀차지 전용이면서 **홀딩은 된다** — 두 조작은 다른 축이다.
        # 정책(--hold-ctrl · char_defaults)과 명시 시퀀스 양쪽을 조립 시점에 막는다.
        if self.input_type == "DOWN_Charge" and (
                any(e["mode"] in ("hold", "hold_judge") for e in self._click_sched)
                or any(a.get("action") == "hold" for a in self._ctrl_seq)):
            raise ValueError(
                f"{self.name}: DOWN_Charge 무기는 풀차지 홀딩이 안 된다 "
                f"(차지가 차면 자동 발사). docs/CONTROL.md §홀드")

    # ── 조작자 배타 (카메라 한 대) ────────────────────────────────────────

    def _prio_of(self, el: dict, default: int) -> int:
        """컨트롤 요소 하나의 등급. **요소 지정 → 캐릭터 단위 지정 → 종류 기본값** 순이다.

        요소마다 따로 적을 수 있어야 하는 이유는 같은 니케가 급한 조작과 안 급한 조작을
        함께 들기 때문이다 — 버충 톡톡이(상)와 상시 재장전(하)을 한 캐릭터에 걸면
        캐릭터 단위 등급 하나로는 표현되지 않는다. 정본: docs/CONTROL.md §조작자는 한 명.
        """
        val = el.get("priority")
        if val is None:
            val = self._ctrl_priority
        return _parse_prio(val, default, self.name)

    def _owns(self, bm: BuffManager) -> bool:
        """지금 이 니케를 조작할 수 있는가 = 카메라를 잡고 있는가.

        `warn`·`strict`는 언제나 True다 — 전원을 동시에 조작하는 비현실적 상한이고,
        겹침은 경고나 실패로만 다룬다. 정본: docs/CONTROL.md §조작자는 한 명.
        """
        if bm.state.get("ctrl_mode") != "solo":
            return True
        return bm.state.get("ctrl_owner") == self.name

    def _aim_entry(self, t: float, bm: BuffManager, geom) -> dict | None:
        """지금 걸린 에임 항목 — 창이 열려 있고 겨눌 표적이 살아 있는 첫 항목. 좌표 모드가 아니면 None.
        **부작용이 없다** — 조율(`_wants_control`)과 조준점 결정(`simulate` `_resolve_aims`)이 함께 묻는다."""
        if geom is None or not self._aim_sched:
            return None
        for e in self._aim_sched:
            if geom.center(e["at"]) is not None and self._when_open(e, t, bm):
                return e
        return None

    def _wants_control(self, t: float, bm: BuffManager) -> tuple[str, int] | None:
        """지금 이 니케를 조작하고 싶은가 — **부작용 없이** 묻는다. 조율 단계 전용.
        `(요청 종류, 등급)` 또는 None.

        손 에임(`control["aim"]`)도 카메라를 요구하는 요청이다. 같은 니케의 다른 요청과 겹치면 **등급이 높은
        쪽**이 이 니케의 요청이 된다(같으면 에임 아닌 쪽) — 에임 항목이 없으면 종전과 한 자리도 같다.
        """
        req = self._wants_control_base(t, bm)
        geom = bm.state.get("enemy", {}).get(GEOM_KEY)
        e = self._aim_entry(t, bm, geom)
        if e is None:
            return req
        aim = (f"에임:{e['at']}",
               self._prio_of(e, _PRIO_HIGH if e["at"] in geom.must_break else _PRIO_MID))
        return aim if req is None or aim[1] > req[1] else req

    def _wants_control_base(self, t: float, bm: BuffManager) -> tuple[str, int] | None:
        """에임을 뺀 요청 — 종전 `_wants_control`.

        이미 열려 있는 조작(엄폐·홀드)은 계속 잡고 있어야 하므로 "유지"도 요청으로 센다 —
        카메라를 떠나는 순간 풀려 버리기 때문이다(유저 확인). 유지의 등급은 그 조작을 연
        정책에서 물려받는다(`_ctrl_open_prio`).
        """
        seq = self._ctrl_seq
        if self._ctrl_seq_i < len(seq) and t >= float(seq[self._ctrl_seq_i].get("t", 0.0)):
            return "시퀀스", _PRIO_SEQ   # 유저가 시각을 찍은 조작 — 등급 밖의 최우선
        if (self._cover_until_reload or self._cover_until > 0) and not self._cover_all:
            # `cover_all`은 카메라를 잡지 않는다 (버튼 하나로 전원)
            return "엄폐 유지", self._ctrl_open_prio
        if (self._hold_release_t > t
                and (self._hold_until_close_entry is None
                     or self._when_open(self._hold_until_close_entry, t, bm))):
            return "홀드 유지", self._ctrl_open_prio
        if not (self._in_weapon_change or bm.get_weapon_change(self.name) is not None):
            if self._want_burst_cover(t, bm) is not None:
                return "버스트 엄폐컨", self.cover_priority
            if self._want_reload_cover(t, bm) is not None:
                return "장전컨", self.reload_priority
        e = self._click_entry(t, bm, _CLICK_PRESS_MODES + _CLICK_HOLD_MODES)
        if e is not None and e["mode"] != "auto":
            return f"클릭:{e['mode']}", e["_prio"]
        return None

    def _release_control(self, t: float, bm: BuffManager) -> None:
        """카메라를 뺏겼다 — 걸어 둔 조작이 **풀린다**.

        엄폐는 자세가 풀려 자동 사격으로 돌아가고, 들고 있던 풀차지는 그 자리에서
        발사된다 (유저 확인 2026-08-29 — `docs/DATA_VERIFY.md §컨트롤`). 되돌릴 상태가
        없으므로 복귀는 정책 재평가로 한다 — 이번 사이클에 이미 쓴 앵커를 한 번만 되돌린다.
        """
        if self._cover_until_reload or self._cover_until > 0:
            self._exit_cover(t)
            self._revert_ctrl_anchor()
        if self._hold_release_t > t:
            self._hold_release_t = -1.0      # 들고 있던 풀차지가 나간다
            self._hold_until_close_entry = None
            self._revert_ctrl_anchor()
        self._close_ctrl(t)

    def _revert_ctrl_anchor(self) -> None:
        """선점으로 끊긴 정책이 다시 걸릴 수 있게 앵커를 되돌린다. **앵커당 1회만.**

        되돌리지 않으면 "이 사이클에 이미 했다"로 남아 복귀가 불가능하고, 무제한으로
        되돌리면 복귀 → 재선점이 매 틱 반복된다(채터링).
        """
        kind, val = self._ctrl_anchor_kind, self._ctrl_anchor_val
        if not kind or (kind, val) in self._reentry_used:
            return
        self._reentry_used.add((kind, val))
        if kind == "cover":
            self._cover_ctrl_anchor = -1.0
        elif kind == "reload":
            self._reload_ctrl_anchor = -1.0
        elif kind == "hold":
            self._hold_ctrl_anchor = -1.0
        self._ctrl_anchor_kind = ""

    # ── 조작 구간 로그 (조작자 관점) ──────────────────────────────────────

    def _open_ctrl(self, t: float, input_: str, mode: str, producer: str) -> None:
        """조작 구간을 연다. **같은 행위가 이어지면 열린 구간을 그대로 둔다** —
        톡톡이를 발마다 적으면 로그가 폭발한다. 정본: docs/CONTROL.md §두 관점.
        """
        if self._sim_log is None:
            return
        cur = self._ctrl_open
        if cur is not None:
            if (cur["input"], cur["mode"], cur["producer"]) == (input_, mode, producer):
                return
            self._close_ctrl(t)
        self._ctrl_open = {"t0": t, "input": input_, "mode": mode, "producer": producer}

    def _close_ctrl(self, t: float) -> None:
        """열려 있는 조작 구간을 닫는다. 길이가 0이면 버린다(같은 틱에 열고 닫힌 경우)."""
        if self._sim_log is None or self._ctrl_open is None:
            return
        o, self._ctrl_open = self._ctrl_open, None
        if t > o["t0"]:
            self._sim_log.control_log.append(ControlLogEntry(
                t0=o["t0"], t1=t, caster=self.name,
                input=o["input"], mode=o["mode"], producer=o["producer"]))

    # ── "언제" 조립 — 상태 창과 앵커 구간 ────────────────────────────────

    @staticmethod
    def _gate_open(spec: dict, bm: BuffManager) -> bool:
        """확정된 현재/직전 풀버스트 사이클의 단계별 사용자가 게이트와 맞는가."""
        gate = spec.get("gate")
        if gate is None:
            return True
        users = bm.state.get("burst_cycle_users", {}).get(gate["burst_stage"], set())
        return gate["burst_user"] in users

    def _anchor_at(self, spec: dict, t: float, bm: BuffManager) -> tuple[float, float] | None:
        """앵커 스펙 → `(앵커 기준값, 오프셋까지 적용한 시각)`. 게이트가 닫혔으면 None.

        **기준값을 따로 돌려준다.** 사이클당 1회 가드가 판정하는 건 기준값이기 때문이다 —
        `minus`가 붙은 시각은 매 틱 재장전 시간을 따라 움직여서 "같은 사이클인가"를
        물을 수 없다.

        앵커가 자기 게이트를 함께 든다 — 위 `_ANCHORS` 주석 참조.
        """
        if not self._gate_open(spec, bm):
            return None
        anchor = spec["anchor"]
        if anchor == "combat_start":
            base = 0.0
        elif anchor in ("fb_end", "own_fb_end"):
            if not bm.state.get("full_burst", False):
                return None
            if anchor == "own_fb_end" and not bm.state.get("burst_casted", {}).get(self.name):
                return None
            base = float(bm.state.get("full_burst_end_t", -1.0))
            if base <= 0:
                return None
        elif anchor == "own_buff_end":
            expiry = bm.own_buff_expires_at(self.name, spec["buff"], t)
            if expiry is None:
                return None
            base = expiry
        else:   # next_fb_start — 관측 기반 예측이라 첫 사이클에는 값이 없다
            base = float(bm.state.get("next_fb_start_pred", -1.0))
            if base <= 0:
                return None
        at = base + spec["offset"]
        if spec.get("minus") == "reload_total":
            at -= self._reload_total_duration(bm, t)
        return base, at

    # ── 클릭 스케줄 조립 ──────────────────────────────────────────────────

    def _build_click_schedule(self, control: dict) -> list[dict]:
        """`control`을 클릭 스케줄로 정규화한다. 정본: docs/CONTROL.md §체계.

        새 키(`click`)와 종전 키(`tap_fire`·`hold`)를 함께 주면 **조립 시점에 실패시킨다** —
        둘이 겹치면 어느 쪽이 이기는지가 조용한 결과 차이가 되기 때문이다.
        차지형이 아니면 클릭에 실을 행위가 없어(auto뿐) 빈 스케줄을 준다.
        """
        raw = control.get("click")
        legacy = [k for k in ("tap_fire", "hold") if control.get(k)]
        if raw is not None and legacy:
            raise ValueError(
                f"{self.name}: `click`과 종전 키({' · '.join(legacy)})를 같이 줄 수 없다 — "
                f"한쪽으로 적는다. docs/CONTROL.md §체계")
        sched = list(raw) if raw is not None else self._desugar_click(control)
        if self.fire_mode != "charge":
            return []   # 차지형 전용 (종전과 같다 — 비차지에 주면 무시된다)

        out: list[dict] = []
        for raw_e in sched:
            e = _norm_click_entry(raw_e, self.name)
            window, mode = e.get("window"), e["mode"]
            # 등급 — **같은 톡톡이라도 목적이 다르면 등급이 다르다.** 충전 창 한정 톡톡이는
            # 놓치면 사이클이 밀리는 조작이고(버충), 상시 톡톡이는 언제든 끊고 다시 하면
            # 된다. 홀드는 엄폐컨과 목적이 같아 같은 등급이다.
            e["_prio"] = self._prio_of(
                e, _PRIO_MID if mode in ("hold", "hold_until_close", "hold_judge")
                else _PRIO_HIGH if (mode == "tap" and window == "burst_charge")
                else _PRIO_LOW if mode == "tap" else 0)
            if mode == "tap":
                # 풀차지 전용 무기(DOWN_Charge + 홍련 : 흑영·레이븐·A2)는 끊어쏘기가
                # 물리적으로 안 된다 — 조용히 무시하면 있지도 않은 조작으로 딜이 나온다.
                if self.full_charge_only:
                    raise ValueError(
                        f"{self.name}: 풀차지 전용 무기라 톡톡이를 걸 수 없다 "
                        f"(input_type={self.input_type!r}). docs/CONTROL.md §톡톡이")
                e["_timing"] = self._tap_timing(e)
            out.append(e)
        return out

    def _desugar_click(self, control: dict) -> list[dict]:
        """종전 키(`tap_fire`·`hold`)를 클릭 스케줄로 옮긴다.

        **hold를 tap 앞에 놓는다.** 같은 좌클릭에 실린 두 행위라 동시에 할 수 없고, 유저
        운용이 "본인 버스트 동안엔 들고 있다가 밖에서는 끊어친다"이기 때문이다(아인·에이다).
        종전 실행층은 톡톡이 분기에서 홀드를 보지 않아 이 조합이 통째로 무시됐다.
        """
        out: list[dict] = []
        hd = control.get("hold") or {}
        policy = hd.get("policy", "")
        if policy in ("own_full_burst", "charge_hold_after_fb"):
            e = {
                "window": "own_full_burst" if policy == "own_full_burst" else "after_own_fb",
                "mode": "hold" if policy == "own_full_burst" else "hold_judge",
                "lead": float(hd.get("lead", _HOLD_LEAD_DEFAULT)),
            }
            if "priority" in hd:
                e["priority"] = hd["priority"]
            out.append(e)
        elif policy:
            raise ValueError(
                f"{self.name}: 모르는 hold.policy: {policy!r}. "
                f'"own_full_burst" 또는 "charge_hold_after_fb"여야 한다. docs/CONTROL.md §홀드')
        tap = control.get("tap_fire")
        if tap:
            e = {"window": str(tap.get("window", "always")), "mode": "tap",
                 "rate": tap["rate"]}
            for k in ("release", "full_charge_interval", "priority"):
                if k in tap:
                    e[k] = tap[k]
            out.append(e)
        return out

    def _tap_timing(self, e: dict) -> dict:
        """톡톡이 한 발의 주기를 조각으로 분해한다. 정본: docs/CONTROL.md §톡톡이.

        목표 주기를 [사격 전 딜레이 + 차지 + 떼기 + 남은 사격 후 딜레이]로 나눈다. 최소
        구성(사격 전 0.22 + 떼기)보다 여유가 있으면 그 여유는 **먼저 "덜 지운 사격 후
        딜레이"**로 간다 — rate를 낮게 잡는다는 게 곧 딜레이를 덜 지운다는 뜻이다.
        0.16초를 다 채우고도 남는 만큼만 실제로 차지된다(느린 톡톡이). 사격 전 0.22초는
        차지가 시작되기 전 구간이라 차지에 들어가지 않아, 완벽한 0.22 간격 톡톡이는 차지가
        0이고 차지 배율이 언제나 100%다.
        """
        release = float(e.get("release", _TAP_RELEASE_DEFAULT))
        slack = max(0.0, 1.0 / float(e["rate"]) - _TAP_MIN_HOLD - release)
        charge = max(0.0, slack - _TAP_CUTTABLE_DELAY)
        return {
            "release": release,
            "post": min(_TAP_CUTTABLE_DELAY, slack),
            "charge": charge,
            "hold": _TAP_MIN_HOLD + charge,
            "full_charge_interval": float(e.get("full_charge_interval", 0.0)),
        }

    def element_match(self, bm: BuffManager) -> bool:
        """이 히트에 우월 코드(DealForm ⑦)가 붙는가.

        두 경로가 OR로 합쳐진다 — 로스터 코드 상성(고정)과 `element_code_override`
        버프(라피 : 레드 후드 `부착형 유탄`: 전격 적에게도 우월). 후자는 버프라
        조회 시점에 봐야 하므로 값을 캐싱하지 않는다.
        """
        return self.base_element_match or bm.element_override_match(
            self.name, self.enemy_code)

    def tick(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        # 전투불능: 아무것도 못 한다 (보스 공격 패턴이 있을 때만 생긴다)
        if bm.is_down(self.name):
            return []
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
                self._wc_ammo_full = None   # 진입 시점에 다시 잰다
                self._wc_ammo_restored = False
            # ── 컨트롤 실행층 (모드 중) ────────────────────────────────
            # **무기 변경 중에도 엄폐는 된다** (유저 확인, 2026-09-02). 종전에는 이 분기가
            # 컨트롤층보다 위에서 return해 모드가 켜진 동안 조작이 통째로 멈췄다. 시각을
            # 지정한 명시 시퀀스는 버려지지도 않고 **모드 종료 프레임으로 밀렸다** —
            # `_pump_ctrl_seq`가 지나간 항목을 그대로 들고 있다가 한꺼번에 소비하기 때문이다
            # (`S38_마나` 벨벳: t=45.0 엄폐 지정 → 실제 50.917에 발동).
            # 순서는 모드 밖 경로와 같게 둔다: 클릭 스케줄 → 명시 시퀀스 → 엄폐 만료 →
            # (모드 재장전) → 엄폐 중 사격 금지.
            # **정책 엄폐(`_apply_cover_policy`)는 여기서 부르지 않는다** — 그쪽은
            # 「모드 탄창 로직을 흔들지 않도록」 스스로 모드 중을 막고 있고, 그 판단은 그대로 둔다.
            self._apply_click_schedule(t, bm)
            if self._owns(bm) and self._pump_ctrl_seq(t, bm):
                return []
            self._drop_blocked_cover(t, bm)
            self._expire_timed_cover(t, bm)

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
            # 엄폐 중이면 모드 사격도 멈춘다 — 컨트롤의 물리 배타는 모드 안팎이 같다.
            if self._tick_cover(t):
                return []
            return self._tick_weapon_change(t, bm, enemy, cfg, wc_eff)

        # weapon_change 만료 직후: next_fire_time 리셋으로 과거 발사 빚 방지
        if self._in_weapon_change:
            self._in_weapon_change = False
            self.next_fire_time = t
            self._wc_entry_reload_until = -1.0
            # **원래 무기로 돌아오면 차지는 처음부터 다시 한다** (유저 확인, 2026-09-02).
            # 초기화하지 않으면 모드 진입 전에 잡아 둔 `_charge_start_t`가 10초 내내 얼어
            # 있다가 복귀 프레임에 **공짜 풀차지 한 발**로 터진다 — 차지바가 MG를 들고 있는
            # 동안 가득 찬 채 남아 있는 셈이다. `_start_reload`·`_enter_cover`가 같은 이유로
            # 하는 처리이고, 발수 소진 종료 경로(`_tick_weapon_change`)에는 이미 있었다.
            # 지속시간 만료·토글 해제 경로만 비어 있었다.
            # 영향: 벨벳 `깔끔한 마무리`(SR→MG) · 타키나 `제압 개시`(SR→SG) ·
            #       라플라스 `라플라스 버스터`(RL→SMG).
            if self.fire_mode == "charge":
                self._charge_phase = "ready"
            self._charge_full_t = -1.0
            self._hold_release_t = -1.0
            bm.state.setdefault("charging", {})[self.name] = False
            # 시한부 모드가 duration으로 끝났거나 토글이 풀렸다. **모드가 끝나면 원래 무기는
            # 만탄으로 돌아온다** (유저 확인 2026-08-08·2026-09-09) — 발사 발수도 지속시간도
            # 진입 전 잔탄도 보지 않는다. 모드 종료 = 재장전 완료 상태로 본다.
            # 진입 시 덮어쓴 모드 장탄(무한 장탄이면 센티널 999999)이 그대로 남아 원래 무기의
            # 탄창으로 새어 나가면 모드가 끝난 뒤에도 재장전이 사라진다. 모더니아 `섬멸 모드`.
            #
            # 종전에는 `_wc_ammo_borrowed`(= 연사 모드)일 때만 채웠다 — **차지 모드는 이 경로에서
            # 잔탄을 그대로 들고 나왔다.** 여기서는 이미 모드가 만료돼(`wc_eff is None`)
            # `_full_ammo`가 원래 무기 기준을 주므로 그대로 쓴다.
            #
            # **발수 소진으로 끝난 모드는 `_tick_weapon_change`가 이미 채웠다** —
            # 여기서 또 채우면 그때 발생한 `event:state_end`의 장탄 조작을 덮어쓴다.
            if not self._wc_ammo_restored:
                self.ammo = self._full_ammo(bm, t)
                self._wc_ammo_full = None
                if self.reloading_until > 0 and self._reload_in_weapon_change:
                    # 모드 안에서 잡힌 재장전은 만탄 복귀로 의미가 없어진다.
                    self.reloading_until = -1.0
                    self._reload_in_weapon_change = False
            self._wc_ammo_borrowed = False
            self._wc_ammo_restored = False

        # 장탄 수 무한이 켜지면 진행 중 재장전은 완료 이벤트 없이 즉시 끊는다.
        # 남은 장탄은 보존하고, 활성 중에는 0발이어도 발사할 수 있다.
        if self._has_infinite_ammo(bm, t) and self.reloading_until > 0:
            self._cancel_reload(t, bm, "재장전 취소(무한 장탄)")
            self.next_fire_time = max(self.next_fire_time, t)

        # 최대 장탄 증가 버프가 만료되면 초과 잔탄은 잘린다 (유저 확인, GAMEPLAY §무기 메카닉).
        # 잔탄은 발사로만 줄어들기 때문에, 여기서 재평가하지 않으면 `[N초 유지]` 장탄 버프가
        # 끝난 뒤에도 초과분을 계속 쏜다. 재장전 중에는 _finish_reload가 어차피 다시 채운다.
        if self.reloading_until <= 0:
            _cap = self._full_ammo(bm, t)
            if self.ammo > _cap:
                self.ammo = _cap
                if self._sim_log is not None:
                    self._sim_log.ammo_log.append(
                        AmmoLogEntry(t=t, caster=self.name, ammo=self.ammo))

        # 모드 지정 플래그: 진입 조건이 충족된 순간 수동 재장전을 삽입해 모드로 들어간다.
        # (실전의 수동컨을 재현. 자연 재장전만으로는 진입 조건이 성립하지 않는 모드가 있다)
        if (self.weapon_mode_swap
                and self.reloading_until <= 0
                and self._post_reload_end_t <= 0
                and bm.manual_swap_ready(self.name, t)):
            self._start_reload(t, bm)
            return []

        # ── 컨트롤 실행층 ────────────────────────────────────────────────
        # 액션 생산자 둘을 같은 입구(_enter_cover / _hold_release_t)로 흘린다.
        # 클릭 스케줄을 먼저 굴린다 — 뒤이은 시퀀스가 같은 틱에 덮어쓸 수 있게 해서
        # **명시 시퀀스가 정책보다 우선**한다는 규칙을 순서만으로 지킨다.
        # 엄폐를 연 틱은 거기서 끝난다: 자세 전환에 최소 1프레임이 든다. 재장전이 0초인
        # 구간(정책 A가 노리는 바로 그 구간)에서 이 1프레임이 결과를 가른다.
        self._apply_click_schedule(t, bm)
        if (self._owns(bm) and self._pump_ctrl_seq(t, bm)) or self._apply_cover_policy(t, bm):
            return []

        # duration이 있는 엄폐는 지정 시각에 끝난다. 탄이 일부라도 있으면 진행 중인
        # 재장전을 그 자리에서 끊고, 0발이면 다음 클립 하나가 들어온 직후 끊는다.
        # 그보다 먼저, 엄폐 불가가 켜졌으면 자세부터 풀린다.
        self._drop_blocked_cover(t, bm)
        self._expire_timed_cover(t, bm)

        # 재장전 완료 체크 (엄폐 중에도 재장전은 그대로 굴러간다)
        if self.reloading_until > 0:
            if t < self.reloading_until:
                return []
            self._finish_reload(t, bm)
            if self.reloading_until > 0:
                return []  # 클립 무기 — 탄창이 덜 찼고 다음 클립이 이어졌다
            # 재장전 완료가 발생시킨 event:full_reload로 무기 변경 모드에 진입했을 수 있다.
            # 같은 프레임에 원래 무기로 한 발 쏘고 넘어가지 않도록 다시 확인한다.
            wc_eff = bm.get_weapon_change(self.name)
            if wc_eff is not None:
                self._in_weapon_change = True
                return self._tick_weapon_change(t, bm, enemy, cfg, wc_eff)

        # 엄폐 중이면 사격도 차징도 불가 — 컨트롤의 물리 배타는 여기 한 곳에서만 강제된다
        if self._tick_cover(t):
            return []

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
            if self.ammo <= 0 and not self._has_infinite_ammo(bm, t):
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

    def _current_spread(self, buffs: dict) -> float:
        """현재 탄착군 직경(px). 예열 진행도와 명중률을 얹은 값.

            D = spread(예열 보간) × (1 − _ACC_SLOPE_RATIO × 명중%)

        예열은 `warmup_shots`(지속 사격 누적 발수)에 **선형**으로 보간한다 — 연사 예열과
        같은 카운터를 쓰되 분모가 다르다. 선형이라는 것과 비율 상수 둘 다 우리 가정이며
        `docs/DATA_VERIFY.md` §명중률/탄착군에 ⬜로 남아 있다.
        """
        base = self.spread_start
        if self._spread_shots_needed > 0:
            prog = min(self.warmup_shots, self._spread_shots_needed) / self._spread_shots_needed
            base = self.spread_start + (self.spread_end - self.spread_start) * prog
        acc = buffs.get("accuracy_pct", 0.0)
        return max(base * (1.0 - _ACC_SLOPE_RATIO * acc), 1.0)

    # ── 좌표 모드 (보스 패턴 enemy.coord) ─────────────────────────────────
    # 정본: calculator/boss_pattern.py §좌표 모드 · 기하는 calculator/aim.py

    def _area_radius(self, geom, buffs: dict, pierce: bool, explosion: bool) -> float:
        """관통·폭발 원의 반지름(px). 관통 = pierce_px × (1 + 관통 범위 ▲%), 폭발 = CDN 폭발 범위 × explosion_scale ×
        (1 + 폭발 범위 ▲%) — 무기 값이 없으면(RL이 아닌 니케의 발사체 폭발 스킬) RL 기본값. 둘을 겸하면 넓은 쪽."""
        r = 0.0
        if pierce:
            r = geom.pierce_px * (1.0 + buffs.get("pierce_range", 0.0) / 100.0)
        if explosion:
            base = self._explosion_cdn or DEFAULT_EXPLOSION_RANGE
            r = max(r, base * geom.explosion_scale * (1.0 + buffs.get("explosion_range", 0.0) / 100.0))
        return r

    def _aim_point(self, bm: BuffManager, geom) -> tuple[float, float]:
        """이번 프레임의 조준점 — `simulate` `_resolve_aims`가 조율 뒤에 정한 값. 아직 없으면 자동 에임."""
        got = bm.state.get("aim", {}).get(self.name)
        return got[0] if got else geom.auto_aim

    def _stage_aim(self, bm: BuffManager) -> tuple[str, str] | None:
        """좌표 off — 이번 프레임에 겨눈 표적 `(이름, 종류)`. 본체·쫄몹을 겨눴으면 None. 정본: boss_pattern.py §조준."""
        got = bm.state.get("aim", {}).get(self.name)
        if not got or not got[1]:
            return None
        kind = bm.state.get("target_kinds", {}).get(got[1])
        return (got[1], kind) if kind else None

    def _stage_pellet(self, stage: tuple[str, str], t: float, enemy: dict, buffs: dict, ht: dict,
                      expected: bool, tag_of, more: dict) -> tuple[list[HitEvent], float, float, float, float]:
        """좌표 off 펠릿 하나 — 겨눈 표적에 통째로 떨어진다. `_coord_pellet`과 같은 모양을 돌려준다.

        코어 없음 · 파츠면 파츠 대미지 ▲. 관통·폭발 탄은 좌표 모드처럼 본체에도(코어 없이) 맞고 곁의 reach 표적에도
        닿는다 — 그때 본체 히트가 그 발의 크리를 정하고 겨눈 표적은 관통·폭발로 따로 맞은 히트(`extra`)다. 겨눈 표적은
        다중 타격에서 빠진다(`aimed`, 한 발에 한 번). 명중 트리거는 겨눈 곳 — 파츠면 파츠 명중, 저지원이면 없음."""
        name, kind = stage
        is_part = kind == "parts"

        def calc(**over) -> dict:
            return calc_damage(base_atk=self.base_atk, buffs=buffs, weapon=self.weapon,
                               hit_type={**ht, "is_core": False, "core_prob": None, "is_core_damage": False, **over},
                               enemy_def=enemy.get("def", 31784), expected=expected)

        part_f = 1.0 if is_part else 0.0
        if ht["is_pierce_damage"] or ht["is_projectile_explosion"]:
            body = calc()
            bev = HitEvent(t=t, caster=self.name, damage=body["damage"], is_crit=body["is_crit"],
                           hit_tag=tag_of(False), aimed=name, **more,
                           **_reach_hit(enemy, {**ht, "is_core": False, "core_prob": None}, body, buffs,
                                        parts_skill=False, base_atk=self.base_atk, weapon=self.weapon,
                                        expected=expected))
            r = calc(is_part=is_part, crit_override=None if expected else body["is_crit"])
            tev = HitEvent(t=t, caster=self.name, damage=r["damage"], is_crit=r["is_crit"], hit_tag=tag_of(False),
                           target=name, extra=True, **more)
            return [bev, tev], part_f, 0.0, 0.0, body["crit_frac"]
        r = calc(is_part=is_part)
        return ([HitEvent(t=t, caster=self.name, damage=r["damage"], is_crit=r["is_crit"], hit_tag=tag_of(False),
                          target=name, **more)], part_f, 0.0, 0.0, r["crit_frac"])

    def _coord_pellet(self, geom, t: float, bm: BuffManager, enemy: dict, buffs: dict, ht: dict,
                      expected: bool, tag_of, extra: dict) -> tuple[list[HitEvent], float, float, float, float]:
        """좌표 모드 펠릿 하나 — 착탄 판정에서 히트를 만든다. `(히트, 파츠 명중 몫, 본체 명중 몫, 코어 몫, 크리 몫)`.

        `ht`는 이 펠릿의 hit_type(코어·파츠 칸은 여기서 덮는다), `tag_of(is_core)`는 히트 태그, `extra`는 히트에 더 실을
        칸(무기 변경 모드의 skill_name). 명중 트리거 몫은 **착탄점의 가장 앞 물체**로 정한다 — 파츠면 파츠 명중, 표적 없이
        코어 밖 본체면 본체 명중. 관통·폭발 탄의 코어 몫은 본체 히트의 코어 판정(착탄점이 코어 안)이다.
        """
        ax, ay = self._aim_point(bm, geom)
        R = self._current_spread(buffs) / 2.0
        grow = self._area_radius(geom, buffs, ht["is_pierce_damage"], ht["is_projectile_explosion"])
        tg = geom.targets

        def calc(**over) -> dict:
            return calc_damage(base_atk=self.base_atk, buffs=buffs, weapon=self.weapon, hit_type={**ht, **over},
                               enemy_def=enemy.get("def", 31784), expected=expected)

        def hit(dmg: int, is_crit: bool, is_core: bool, target: str = "", area: bool = False) -> HitEvent:
            return HitEvent(t=t, caster=self.name, damage=dmg, is_crit=is_crit, hit_tag=tag_of(is_core),
                            target=target, extra=area, **extra)

        events: list[HitEvent] = []
        if expected:
            L = geom.landing(ax, ay, R, grow)
            part_f = sum(p for p, g in zip(L.front, tg) if g.kind == "parts")
            body_f = max(0.0, 1.0 - sum(L.front) - L.core_open)
            if grow > 0.0:
                body = calc(is_core=L.core_under >= 1.0, core_prob=L.core_under)
                events.append(hit(body["damage"], body["is_crit"], L.core_under >= 1.0))
                for g, q in zip(tg, L.reach):
                    if q > 0.0:
                        r = calc(is_core=False, core_prob=None, is_core_damage=False, is_part=g.kind == "parts")
                        events.append(hit(round(r["damage"] * q), False, False, g.name, True))
                return events, part_f, body_f, L.core_under, body["crit_frac"]
            wb = max(0.0, 1.0 - sum(L.front))
            pcb = L.core_open / wb if wb > 1e-12 else 0.0
            body = calc(is_core=pcb >= 1.0, core_prob=pcb)
            if wb > 0.0:
                events.append(hit(round(body["damage"] * wb), body["is_crit"], pcb >= 1.0))
            for g, p in zip(tg, L.front):
                if p > 0.0:
                    r = calc(is_core=False, core_prob=None, is_core_damage=False, is_part=g.kind == "parts")
                    events.append(hit(round(r["damage"] * p), False, False, g.name))
            return events, part_f, body_f, L.core_open, body["crit_frac"]

        # 난수 모드 — 착탄점을 뽑는다. 조준점 중심 원 코어뿐이면 난수를 하나만 먹는다(좌표 off와 같은 난수열)
        px, py = sample_landing(ax, ay, R, random, angle=needs_angle(ax, ay, tg, geom.core))
        front = next((g for g in tg if g.shape.contains(px, py)), None)
        in_core = geom.core is not None and geom.core.contains(px, py)
        part_f = 1.0 if front is not None and front.kind == "parts" else 0.0
        if grow > 0.0:
            body = calc(is_core=in_core, core_prob=None)
            events.append(hit(body["damage"], body["is_crit"], in_core))
            for g in tg:
                if g.shape.contains(px, py, grow):
                    r = calc(is_core=False, core_prob=None, is_core_damage=False, is_part=g.kind == "parts",
                             crit_override=body["is_crit"])
                    events.append(hit(r["damage"], r["is_crit"], False, g.name, True))
            return (events, part_f, 1.0 if front is None and not in_core else 0.0,
                    1.0 if in_core else 0.0, body["crit_frac"])
        if front is not None:
            r = calc(is_core=False, core_prob=None, is_core_damage=False, is_part=front.kind == "parts")
            events.append(hit(r["damage"], r["is_crit"], False, front.name))
            return events, part_f, 0.0, 0.0, r["crit_frac"]
        body = calc(is_core=in_core, core_prob=None)
        events.append(hit(body["damage"], body["is_crit"], in_core))
        return events, 0.0, 0.0 if in_core else 1.0, 1.0 if in_core else 0.0, body["crit_frac"]

    def _fire(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        events = []
        self._apply_wc_first_coeff()
        infinite_ammo = self._has_infinite_ammo(bm, t)
        is_last = (self.ammo == 1 and not infinite_ammo)
        if is_last:
            bm.notify("last_bullet_fire", t, self.name)

        # 지속 사격 누적 발수. 연사 예열(MG)과 탄착군 예열이 **같은 카운터를 공유**하되
        # 각자 자기 분모로 나눈다 — 둘은 서로 다른 발수에서 끝난다(MG 41.4 vs 34.3).
        # 그래서 상한은 둘 중 긴 쪽이다. 탄착군 예열만 있는 무기(프리바티 : 언카인드
        # 메이드, SG)도 세야 하므로 auto_warmup 조건을 넓혔다.
        _shot_cap = max(self.warmup_bullets, self._spread_shots_needed)
        if self.fire_mode == "auto_warmup" or self._spread_shots_needed > 0:
            if self.warmup_shots < _shot_cap:
                wsp = bm.get_buffs(self.name, "__enemy__", t).get("mg_warmup_speed_pct", 0.0)
                incr = max(0.0, 1.0 + wsp / 100.0)
                self.warmup_shots = min(self.warmup_shots + incr, _shot_cap)

        if self._in_weapon_change:
            # weapon_change의 duration_bullets 카운트. ammo 감소량으로 세면
            # `ammo_charge_pct` 같은 장탄 조작 효과에 오염되므로 발사 시점에 직접 센다.
            self._wc_shots += 1

        if not infinite_ammo:
            self.ammo -= 1
            if self._sim_log is not None:
                self._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=self.name, ammo=self.ammo))
            bm.notify("squad_ammo_consume", t, self.name)
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        buffs["is_element_match"] = self.element_match(bm)
        is_optimal = in_optimal_range(enemy, self.name, self.weapon_type, buffs)

        # 코어히트 확률: core_px>0이면 명중률·탄착군·코어 크기로 계산, 0이면 코어 없음
        if enemy.get("core_px", 0) > 0:
            P_core = _core_hit_prob(
                self._current_spread(buffs),
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

        expected = cfg.get("rng_mode") == "expected"
        # 좌표 모드면 착탄점이 코어·파츠·저지원·본체를 가른다(`_coord_pellet`). 좌표 off는 None
        geom = enemy.get(GEOM_KEY)
        # 좌표 off에서 표적을 겨눴으면 그 표적에 떨어진다(`_stage_pellet`, boss_pattern.py §조준)
        stage = self._stage_aim(bm) if geom is None else None
        core_fracs: list[float] = []
        for i in range(hit_count):
            # 히트마다 독립 샘플링 (SG: 10회, 기타: 1회). 기대값 모드는 판정 대신 확률을 넘긴다
            # (P_core가 1이면 판정할 게 없으므로 기대값 모드에서도 코어 히트로 남긴다).
            # 좌표 모드는 여기서 뽑지 않는다 — 착탄점을 `_coord_pellet`이 뽑는다(같은 자리의 난수).
            # 표적을 겨눈 좌표 off 발은 코어가 없다
            aimed = geom is not None or stage is not None
            is_core = False if aimed else (
                (P_core >= 1.0) if expected else (random.random() < P_core))
            coeff = (self.weapon["damage_coeff"] / split) if split > 1 else None
            ht = default_hit_type(
                is_core=is_core,
                core_prob=(P_core if expected and not aimed else None),
                is_full_burst=is_full_burst,
                is_optimal_range=is_optimal,
                is_normal_atk=not self._wc_is_skill_damage(),
                is_weapon_mode_skill=self._wc_is_skill_damage(),
                is_pierce_damage=bool(buffs.get("pierce_enabled")),
                is_armor_break_damage=bool(buffs.get("armor_break_enabled")),
                coeff=coeff,
                _debug_factors=in_debug_window,
            )
            if in_debug_window and i == 0:
                print(f"t={t:.3f}s  base_atk={self.base_atk:,}  enemy_def={enemy.get('def', 31784):,}")
            if aimed:
                tag_of = (lambda c, i=i: ((f"core:pellet:{i}" if c else f"pellet:{i}") if hit_count > 1
                                          else ("core" if c else "normal")))
                more = {"skill_name": self._wc_name} if self._wc_is_skill_damage() else {}
                evs, part_f, body_f, core_frac, crit_frac = (
                    self._coord_pellet(geom, t, bm, enemy, buffs, ht, expected, tag_of, more) if geom is not None
                    else self._stage_pellet(stage, t, enemy, buffs, ht, expected, tag_of, more))
                events.extend(evs)
                bm.notify("pellet_hit", t, self.name)
                core_fracs.append(core_frac)
                _notify_frac(bm, "squad_part_hit", self.name, part_f,
                             lambda: bm.notify_team_hit("squad_part_hit", t, self.name))
                _notify_frac(bm, "squad_body_hit", self.name, body_f,
                             lambda: bm.notify_team_hit("squad_body_hit", t, self.name))
                _notify_frac(bm, "crit_hit", self.name, crit_frac,
                             lambda: bm.notify("crit_hit", t, self.name))
                _notify_frac(bm, "core_hit", self.name, core_frac,
                             lambda: bm.notify("core_hit", t, self.name))
                continue
            res = calc_damage(
                base_atk=self.base_atk, buffs=buffs, weapon=self.weapon,
                hit_type=ht, enemy_def=enemy.get("def", 31784),
                expected=expected,
            )
            if in_debug_window and i == 0:
                print()
            # 기대값 모드에서는 한 히트에 코어/비코어가 섞여 있어 태그를 코어로 가르지 않는다
            # (코어 배율은 이미 이 히트의 damage에 확률로 반영돼 있다)
            tag = (f"core:pellet:{i}" if is_core else f"pellet:{i}") if hit_count > 1 \
                  else ("core" if is_core else "normal")
            events.append(HitEvent(t=t, caster=self.name, damage=res["damage"],
                                   is_crit=res["is_crit"], hit_tag=tag,
                                   **({"skill_name": self._wc_name}
                                      if self._wc_is_skill_damage() else {}),
                                   **_reach_hit(enemy, ht, res, buffs, parts_skill=False,
                                                base_atk=self.base_atk, weapon=self.weapon,
                                                expected=expected)))
            bm.notify("pellet_hit", t, self.name)
            body_ev = "squad_part_hit" if enemy.get("has_parts", False) else "squad_body_hit"
            core_frac = P_core if expected else (1.0 if is_core else 0.0)
            core_fracs.append(core_frac)
            _notify_frac(bm, body_ev, self.name, 1.0 - core_frac,
                         lambda: bm.notify_team_hit(body_ev, t, self.name))
            _notify_frac(bm, "crit_hit", self.name, res["crit_frac"],
                         lambda: bm.notify("crit_hit", t, self.name))
            _notify_frac(bm, "core_hit", self.name, core_frac,
                         lambda: bm.notify("core_hit", t, self.name))

        # 「일반 공격 1회로 펠릿 N개 이상 명중 시」 — 이 **한 발**의 명중 펠릿 수로 판정한다.
        # 누적 카운터(`pellet_hit_count:N`)와 다른 축이라 별도 이벤트다. 계산기에 빗나감
        # 모델이 없어(GAMEPLAY.md §공격과 명중) 지금은 쏜 펠릿이 전부 명중하므로
        # N ≤ 펠릿 수이면 매 발 참이다 — 분리해 둔 것은 미스 모델이 들어올 자리다.
        for _need, _raw in bm.pellet_in_shot_thresholds(self.name):
            if hit_count >= _need:
                bm.notify(f"pellet_hit_in_shot:{_raw}", t, self.name)

        # 일반 공격 명중은 충전 창 밖에서도 시전자 기준 버충값을 `(발당)`→`(대상)`으로
        # 전환한다. 구조물 명중도 같은 방아쇠지만 현재 시뮬에는 구조물 대상이 없다.
        if not self._wc_is_skill_damage():
            bm.mark_normal_attack_landed(self.name)

        # 버스트 게이지: 히트 수만큼. 오토 무기라 풀차지 배율이 걸릴 자리가 없다.
        if self._weapon_gauge_lands(bm):
            gauge_buffs = bm.get_buffs(self.name, "__enemy__", t)
            bm.add_burst_gauge(self._burst_gain(gauge_buffs, hit_count), t, self.name, "weapon")

        # 발사(`on_attack`) → 명중(`hit_count`) 순서다. 쏘고 나서 맞는다는 실제 순서이고,
        # 같은 발에 걸린 「N회 공격 시」 버프가 「N회 명중 시」 딜에 실리는 근거다
        # (레이 `선두 제압` — 공격 100회 버프 + 명중 100회 딜이 한 스킬에 나란히 있다).
        # 둘 다 calc_damage **뒤**라, 이 순서는 종전 `hit_count → on_attack`과 마찬가지로
        # 이 발의 대미지 자체는 건드리지 않는다.
        #
        # 「공격 시」는 **발사 단위**라 총구·펠릿과 무관하게 발사 1회당 1회다.
        bm.notify("on_attack", t, self.name)
        # 「명중 시」는 **탄 단위**라 총구 수만큼 발생한다 — 총구 2개는 탄 1발 소모에
        # 공격 1회·명중 2회다(유저 확인). 펠릿은 한 탄을 나눈 것이라 여기 곱하지
        # 않는다(`pellet_hit`이 루프 안에서 따로 센다).
        # 빗나간 탄은 이 루프에서 빠지고 `on_attack`만 남는 것이 분리의 목적이다 —
        # 지금은 미스 모델이 없어 총구 전부가 명중한다. 여기가 그 게이트 자리다.
        # `core_frac`은 그 탄의 코어 확률 — `not_core` 조건이 읽는다.
        for bullet_core in _bullet_core_fracs(core_fracs, self.muzzles):
            bm.notify("hit_count", t, self.name, core_frac=bullet_core)
        if not self._wc_is_skill_damage():
            bm.consume_bullet_buffs(self.name, t)
        if is_last:
            # `마지막 탄환 명중 시`도 명중이라 총구 수만큼 발동한다.
            # 짝인 `last_bullet_fire`(마지막 탄환 **공격** 시)는 발사라 1회다.
            # **실례가 없어 미검증이다** — 총구 2개인 등록 캐릭(츠바이·퀀시 : 이스케이프
            # 퀸)에 이 트리거가 없다. 카운터가 없는 트리거라 2회면 버프가 두 번 붙는데,
            # 그게 맞는지는 확인된 바 없고 명중 규칙의 일관성만으로 잡았다
            # (유저 판단, 2026-09-04. `docs/DATA_VERIFY.md` §총구 수).
            for _ in range(self.muzzles):
                bm.notify("last_bullet", t, self.name)

        return events

    # ── charge (SR/RL) ────────────────────────────────────────────────────

    def _effective_charge_time(self, bm: BuffManager, t: float) -> float:
        """현재 버프를 반영한 유효 차지 시간(초)."""
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        if buffs.get("charge_time_fixed"):
            return self._fixed_charge_time(bm)
        # 차지 속도 % 버프도 장탄과 같다 — 소스마다 **기본 차지 시간** 기준으로 단축량을
        # 구해 0.01초 눈금에 반올림한 뒤 더한다 (유저 인게임 확인, 2026-08-19).
        cut = _quant_sum(self.charge_time_base, buffs, "charge_speed_pct", 0.01)
        # charge_time_flat(초)은 차지 속도 % 를 적용한 뒤 더한다 — "차지 시간 N초 ▼"는
        # 속도 배율이 아니라 결과 시간에서 그만큼 빼는 표기다 (마나 `매터 시그마 4`).
        # 단축량이 기본 차지 시간을 넘으면 차지 시간은 실제로 0초가 된다 (유저 확인).
        return max(0.0, max(0.0, self.charge_time_base - cut)
                   + buffs.get("charge_time_flat", 0.0))

    def _when_open(self, e: dict, t: float, bm: BuffManager) -> bool:
        """이 항목의 **언제**가 지금 열려 있는가. 정본: docs/CONTROL.md §설정 스키마.

        적는 법이 둘이고, 어휘는 클릭과 엄폐가 공유한다 — 축마다 다른 이름을 쓰면 같은
        뜻을 두 번 배워야 한다.

        **상태 창** — 전투 상태 그 자체라 앵커+오프셋으로 환산할 수 없다.
        `burst_charge`는 `state["burst_gauge_charging"]`(= `BurstController._phase == "idle"`)
        한 곳에서만 정의되고 게이지 가산이 쓰는 것과 **같은 값**이라, 톡톡이 구간과 충전 구간이
        구조적으로 어긋날 수 없다. 전투 시작부터 첫 버스트까지도 충전 창이다.

        **앵커 구간** — `[앵커+오프셋, +len)`. 앵커가 자기 게이트를 함께 드므로
        (`_anchor_at()`), 게이트가 닫혀 있으면 구간도 닫힌다.
        """
        if not self._gate_open(e, bm):
            return False
        window = e.get("window")
        if window is None:
            at = self._anchor_at(e, t, bm)
            return at is not None and at[1] <= t < at[1] + e["len"]
        if window == "always":
            return True
        if window == "burst_charge":
            return bool(bm.state.get("burst_gauge_charging", False))
        if window == "burst_chain":
            # 게이지가 실제로 충족돼 1단계에 진입한 뒤부터 switching까지. 미래 게이지를
            # 예측하지 않으며, full_burst가 켜진 바로 그 틱에 닫힌다.
            return bool(not bm.state.get("burst_gauge_charging", False)
                        and not bm.state.get("full_burst", False))
        if window in ("own_full_burst", "after_own_fb"):
            # `after_own_fb`는 시각을 역산하는 항목이라 창 자체는 본인 풀버스트에서 연다
            # (역산은 `_apply_click_schedule()`).
            return bool(bm.state.get("full_burst", False)
                        and bm.state.get("burst_casted", {}).get(self.name))
        return False

    def _click_entry(self, t: float, bm: BuffManager,
                     modes: tuple[str, ...]) -> dict | None:
        """지금 이 니케의 좌클릭에서 `modes` 중 어떤 항목이 걸리는가.
        **먼저 매치되는 항목이 이긴다.** None이면 해당 없음(`auto` = 차면 즉발).

        `modes`로 관심사를 나눠 묻는다 — 스케줄 한 줄이 **누름**과 **떼기** 양쪽을 정하지
        않기 때문이다:

        - 누름(`_CLICK_PRESS_MODES`) — 차지 시작 시점에 래치한다.
        - 떼기(`_CLICK_HOLD_MODES`) — 매 틱 평가한다.

        `hold_judge`는 **누름 선택에 참여하지 않는다.** 그건 `charge_hold:N` 판정이 원하는
        곳에 떨어지도록 시각을 역산하는 항목이라, 창이 열려 있는 내내 누름을 바꾸는 게
        아니라 **그 한 발만** 풀차지로 들게 만든다(`_force_full_charge`). 참여시키면 밀크 :
        블루밍 바니가 본인 버스트 내내 톡톡이를 멈춘다.

        코드가 톡톡이·홀드의 우선순위를 판정하지 않는다 — 어느 구간에서 무엇을 할지는
        입력이 정한다.
        """
        for e in self._click_sched:
            if e["mode"] in modes and self._when_open(e, t, bm):
                return e
        return None

    def _tick_charge(self, t: float, bm: BuffManager, enemy: dict, cfg: dict) -> list[HitEvent]:
        events = []

        if self._charge_phase == "ready":
            if self.ammo <= 0 and not self._has_infinite_ammo(bm, t):
                self._start_reload(t, bm)
                return events
            # `charge_hold_after_fb`: 정책이 잡은 차지 시작 시각을 기다린다. 다만 **한 발
            # 사이클보다 멀면 기다리지 않는다** — 실제 조작도 그때까지는 평소대로 쏘다가,
            # 마지막 한 발이 어차피 안 들어가는 시점부터 손을 뗀다.
            if self._ch_charge_start_t > 0 and t < self._ch_charge_start_t:
                if self._ch_charge_start_t - t <= self._effective_charge_time(bm, t) + 0.4:
                    return events
            self._charge_start_t = t
            self._charge_phase = "charging"
            self._charge_hold_fired.clear()
            # **누름을 어떻게 할지는 차지 시작 시점에 한 번만 정한다(래치).** 매 프레임
            # 다시 보면 창 경계에서 한 발이 반쯤 톡톡이인 채로 갈라진다. 반대로 **떼는**
            # 시점을 고르는 홀드는 매 틱 평가한다 — `_apply_click_schedule()`.
            _entry = self._click_entry(t, bm, _CLICK_PRESS_MODES) if self._owns(bm) else None
            self._tap_this_shot = bool(_entry is not None and _entry["mode"] == "tap")
            if _entry is None or _entry["mode"] == "auto":
                self._close_ctrl(t)   # 조작 없음 = 카메라를 잡고 있을 이유가 없다
            else:
                self._open_ctrl(t, "click", _entry["mode"], _when_label(_entry))
            if self._tap_this_shot:
                _tm = _entry["_timing"]
                self._tap_hold, self._tap_charge = _tm["hold"], _tm["charge"]
                self._tap_release, self._tap_post = _tm["release"], _tm["post"]
                self.tap_full_charge_interval = _tm["full_charge_interval"]
            # 이 발을 풀차지로 쏠지 여기서 정한다 (톡톡이 중 주기적 풀차지).
            self._force_full_charge = (
                self._tap_this_shot
                and self.tap_full_charge_interval > 0
                and t - self._last_full_charge_t >= self.tap_full_charge_interval
            )
            # 의도한 차지가 시작된 순간에만 홀드를 건다. 미리 걸어 두면 그 전에 우연히
            # 완성된 풀차지를 붙잡아 판정이 면역 구간 안에서 헛돌아 버린다.
            #
            # **늦게 시작해도 그대로 진행한다** — 재장전이 겹쳐 예정 시각을 놓치는 일이
            # 흔한데(톡톡이면 1.5초마다 재장전한다), 거기서 포기하면 그 사이클은 판정이
            # 아예 없다. 떼기 시각을 판정 예정 시각이 아니라 **이번 차지 기준**으로 잡으면
            # 늦은 만큼 판정도 늦어질 뿐, 면역이 이미 끝난 뒤라 목적은 그대로 달성된다.
            if self._ch_charge_start_t > 0 and t >= self._ch_charge_start_t:
                need = bm.charge_hold_thresholds(self.name)[-1][0]
                self._hold_release_t = (
                    t + self._effective_charge_time(bm, t) + need + _CTRL_FRAME
                )
                self._ch_charge_start_t = -1.0
                self._force_full_charge = True  # 판정에는 풀차지가 필요하다
            bm.state.setdefault("charging", {})[self.name] = True
            bm._invalidate_buffs_cache()
            if self.ammo == 1 and not self._has_infinite_ammo(bm, t):
                bm.notify("last_bullet_fire", t, self.name)

        if self._charge_phase == "charging":
            if self._tap_this_shot and not self._force_full_charge:
                # 톡톡이: 누르는 시간이 고정이고, 그중 사격 전 딜레이를 뺀 만큼만 차지된다.
                # 차지속도 버프로 유효 차지 시간이 그 아래로 내려가면 풀차지 샷이 된다.
                self._charge_end_t = self._charge_start_t + self._tap_hold
                if t < self._charge_end_t:
                    return events
                is_full = self._tap_charge >= self._effective_charge_time(bm, t)
            else:
                # 풀차지 도달을 래치한다 — 도달 후 버프가 빠져 유효 차지 시간이 늘어나도
                # 이미 채운 차지가 풀리지는 않기 때문이다 (홀드 중 특히 중요).
                if self._charge_full_t < 0:
                    self._charge_end_t = self._charge_start_t + self._effective_charge_time(bm, t)
                    if t < self._charge_end_t:
                        return events
                    self._charge_full_t = t
                is_full = True
                # `풀 차지 상태를 N초 이상 유지 시` — 풀차지 도달 후 유지 시간을 재서 발동한다.
                # 판정은 임계를 넘는 **그 순간 1회뿐**이다(유저 확인): 계속 들고 있어도 다시
                # 판정하지 않으므로, 버스트 중에 홀드를 시작하면 버스트가 끝나도 발동하지 않는다.
                _phase_before, _reload_before = self._charge_phase, self.reloading_until
                self._notify_charge_hold(t, bm)
                # **이 프레임의 판정이** 강제 재장전·탄환 제거를 걸었으면 이 발은 나가지 않는다
                # (밀크 부끄러움 — 유저 확인: 들고 있던 풀차지 샷이 취소된다).
                # 판정과 무관하게 이미 재장전 중이던 경우는 종전 동작을 그대로 둔다.
                if (self._charge_phase != _phase_before
                        or self.reloading_until != _reload_before):
                    return events
                # 홀드: 풀차지가 끝나도 시퀀스가 지정한 시각까지 떼지 않는다.
                # 대기 중에도 charging=True라 "차지 중" 조건 버프가 유지된다 (실제 게임과 동일).
                if self._hold_release_t >= 0 and t < self._hold_release_t:
                    return events
            events.extend(self._charge_fire(t, bm, enemy, cfg, is_full))

        elif self._charge_phase == "post_delay" and t >= self._post_delay_end_t:
            if self._pending_auto_reload:
                self._pending_auto_reload = False
                self._auto_reload(t, bm)
            self._charge_phase = "ready"
            return self._tick_charge(t, bm, enemy, cfg)

        return events

    def _weapon_gauge_lands(self, bm: BuffManager) -> bool:
        """이 무기 사격이 버스트 게이지를 채우는가.

        보스가 사라진 동안(`vanish` 패턴)에는 평타가 빗나가 **평타 몫의 게이지도 안 찬다.**
        스킬이 채우는 게이지(스킬 대미지 히트·게이지 충전 효과)는 그대로 찬다(유저 확인,
        2026-09-13) — 그래서 충전 창 전체를 닫지 않고 무기 사격의 가산 자리에서만 거른다.
        무기 변경 모드의 스킬 대미지 사격은 딜 게이트(`sim_result._is_normal`)가 스킬로 보므로
        여기서도 스킬로 둔다 — 딜은 들어가는데 게이지만 빠지는 어긋남을 만들지 않는다.
        사라짐 중에 이 사격이 들어가는 것은 유저가 확인했다(나유타 `기억 연소`, 2026-09-15).

        속성보호막은 거꾸로다 — **막힌 스킬 대미지는 게이지를 안 채우고 무기 사격 몫은 채운다**
        (유저 확인 2026-09-15). 그래서 스킬 대미지 사격만 보호막을 묻는다.
        """
        if not self._wc_is_skill_damage():
            return not bm.state.get("boss_vanish", False)
        blocks = bm.state.get("boss_shield_blocks")
        return blocks is None or not blocks(self.name)

    def _burst_gain(self, buffs: dict, hit_count: int, full_charge: bool = False,
                    burst_energy: float | None = None) -> float:
        """이번 발사가 만드는 버스트 게이지(%). 충전 창 판정은 하지 않는다.

        `full_charge`는 **풀차지 샷이면서 카메라가 이 니케를 보고 있을 때만** True다 —
        판정은 부르는 쪽(`_charge_fire`)이 한다. 게이지 배율은 대미지 배율과 같은
        `full_charge_mult`를 쓴다(CDN `버스트게이지(풀차지)/100`과 78/78 일치).

        `burst_energy`를 주면 무기값 대신 그 값을 쓴다 — 무기값과 다른 버충 계수를 갖는
        스킬 히트용이다(`data/burst_gauge.json` `_exceptions`, 라피 : 레드 후드 부착 대미지).

        충전 속도 버프는 수령자와 무관하게 같은 시전자 기준식을 쓴다(정본:
        docs/mechanics/버스트 게이지.md). 시전자의 발당 기준값으로 환산된 히트당 고정
        가산이며, 현재 공격의 무기값·스킬값·풀차지 배율은 이 가산항에 관여하지 않는다.
        """
        be = self.burst_energy if burst_energy is None else burst_energy
        gain = be * hit_count
        if full_charge:
            gain *= self.weapon.get("full_charge_mult", 100.0) / 100.0
        return gain + hit_count * buffs.get("burst_charge_speed_flat", 0.0)

    def _notify_charge_hold(self, t: float, bm: BuffManager) -> None:
        """`charge_hold:N` 트리거 발생. 풀차지 유지 시간이 N을 넘긴 첫 프레임에 1회.

        임계값은 이 캐릭터가 실제로 쓰는 값만 본다(`BuffManager.charge_hold_thresholds`).
        임계를 넘긴 뒤에도 계속 들고 있을 수 있으나 재판정은 없다 — 한 번의 차지에 한 번이다.
        `_charge_hold_fired`는 차지를 새로 시작할 때 비워진다.
        """
        if self._charge_full_t < 0:
            return
        held = t - self._charge_full_t
        for value, raw in bm.charge_hold_thresholds(self.name):
            if raw in self._charge_hold_fired or held < value:
                continue
            self._charge_hold_fired.add(raw)
            bm.notify(f"charge_hold:{raw}", t, self.name)

    def _charge_fire(
        self, t: float, bm: BuffManager, enemy: dict, cfg: dict, is_full: bool
    ) -> list[HitEvent]:
        """차지 무기 1발 발사 처리. `is_full=False`면 논차지 샷(톡톡이)."""
        events = []
        self._apply_wc_first_coeff()
        if is_full:
            self._last_full_charge_t = t
            self._force_full_charge = False
            bm.notify("full_charge", t, self.name)
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        buffs["is_element_match"] = self.element_match(bm)
        # 적정거리는 이 발의 버프(풀차지 트리거 뒤)로 판정한다 — 사거리 ▲가 구간을 넓히므로.
        # 무기군 목록 판정(거리 없음)은 버프와 무관해 종전 자리와 값이 같다
        is_optimal = in_optimal_range(enemy, self.name, self.weapon_type, buffs)
        if enemy.get("core_px", 0) > 0:
            P_core = _core_hit_prob(
                self._current_spread(buffs),
                enemy.get("core_px", 50),
            )
        else:
            P_core = 0.0
        expected = cfg.get("rng_mode") == "expected"

        debug_char = cfg.get("_debug_char")
        in_debug_window = (
            debug_char == self.name
            and cfg.get("_debug_t0", -1.0) <= t <= cfg.get("_debug_t1", -1.0)
        )

        # 펠릿 분할은 연사 경로(`_fire`)와 **같은 규칙이다** — 계수를 펠릿으로 나눠
        # 펠릿마다 따로 코어를 판정하고, 총구 수만큼 그 묶음이 더 나간다.
        # 차지 무기가 전부 펠릿 1이던 동안에는 통짜 1히트와 같은 말이었는데,
        # 펠릿 15인 차지 SG(드레이크 : 그레이트 빌런 `오버 오버 드라이브`)가 나오면서
        # 갈라졌다 — **모드 사격도 샷건 펠릿 경로를 타고 탄착군도 기본 SG와 같다**
        # (유저 확인, 2026-09-03). 대미지 총량은 분할해도 같고, 달라지는 것은
        # 펠릿 단위 판정과 `pellet_hit`·`squad_body_hit`·`crit_hit`·`core_hit` 발동 횟수다.
        pellet_fixed = buffs.get("pellet_count_fixed", 0.0)
        if pellet_fixed > 0:
            split = max(1, int(round(pellet_fixed)))
        else:
            split = max(1, self.pellets + int(round(buffs.get("pellet_count", 0.0))))
        hit_count = split * self.muzzles

        is_full_burst = bm.state.get("full_burst", False)
        # 좌표 모드면 착탄점이 코어·파츠·저지원·본체를 가른다(`_coord_pellet`). 좌표 off는 None
        geom = enemy.get(GEOM_KEY)
        # 좌표 off에서 표적을 겨눴으면 그 표적에 떨어진다(`_stage_pellet`, boss_pattern.py §조준)
        stage = self._stage_aim(bm) if geom is None else None
        aimed = geom is not None or stage is not None
        core_fracs: list[float] = []
        crit_fracs: list[float] = []
        coord_fracs: list[tuple[float, float]] = []     # 좌표 모드·표적을 겨눈 발 — 펠릿마다 (파츠 명중 몫, 본체 명중 몫)

        def _tag(c: bool, i: int) -> str:
            if hit_count > 1:
                # 펠릿 분할 히트는 연사 경로와 같은 태그를 쓴다 — `_is_normal()`이
                # 아는 형태가 그것뿐이라 풀차지 태그를 펠릿과 겹쳐 쓸 수 없다.
                return f"core:pellet:{i}" if c else f"pellet:{i}"
            if is_full:
                return "core+full_charge_hit" if c else "full_charge_hit"
            # 논차지 샷은 일반 발사와 같은 취급 (차지 배율 없음)
            return "core" if c else "normal"

        for i in range(hit_count):
            # P_core가 1이면 판정할 게 없으므로 기대값 모드에서도 코어 히트로 남긴다.
            # 좌표 모드는 여기서 뽑지 않는다 — 착탄점을 `_coord_pellet`이 뽑는다(같은 자리의 난수).
            # 표적을 겨눈 좌표 off 발은 코어가 없다
            is_core = False if aimed else (
                (P_core >= 1.0) if expected else (random.random() < P_core))
            coeff = (self.weapon["damage_coeff"] / split) if split > 1 else None
            ht = default_hit_type(
                is_core=is_core,
                core_prob=(P_core if expected and not aimed else None),
                is_full_burst=is_full_burst,
                is_optimal_range=is_optimal,
                is_normal_atk=not self._wc_is_skill_damage(),
                is_weapon_mode_skill=self._wc_is_skill_damage(),
                is_full_charge=is_full,
                is_pierce_damage=bool(buffs.get("pierce_enabled")),
                is_armor_break_damage=bool(buffs.get("armor_break_enabled")),
                is_projectile_explosion=(self.base_weapon_type == "RL"),
                coeff=coeff,
                _debug_factors=in_debug_window,
            )
            if in_debug_window and i == 0:
                print(f"t={t:.3f}s  base_atk={self.base_atk:,}  enemy_def={enemy.get('def', 31784):,}")
            if aimed:
                more = {"skill_name": self._wc_name} if self._wc_is_skill_damage() else {}
                evs, part_f, body_f, core_frac, crit_frac = (
                    self._coord_pellet(geom, t, bm, enemy, buffs, ht, expected, lambda c, i=i: _tag(c, i), more)
                    if geom is not None
                    else self._stage_pellet(stage, t, enemy, buffs, ht, expected, lambda c, i=i: _tag(c, i), more))
                events.extend(evs)
                if hit_count > 1:
                    bm.notify("pellet_hit", t, self.name)
                core_fracs.append(core_frac)
                crit_fracs.append(crit_frac)
                coord_fracs.append((part_f, body_f))
                continue
            res = calc_damage(
                base_atk=self.base_atk, buffs=buffs, weapon=self.weapon,
                hit_type=ht, enemy_def=enemy.get("def", 31784),
                expected=expected,
            )
            if in_debug_window and i == 0:
                print()
            tag = _tag(is_core, i)
            events.append(HitEvent(t=t, caster=self.name, damage=res["damage"],
                                   is_crit=res["is_crit"], hit_tag=tag,
                                   **({"skill_name": self._wc_name}
                                      if self._wc_is_skill_damage() else {}),
                                   **_reach_hit(enemy, ht, res, buffs, parts_skill=False,
                                                base_atk=self.base_atk, weapon=self.weapon,
                                                expected=expected)))
            if hit_count > 1:
                bm.notify("pellet_hit", t, self.name)
            core_fracs.append(P_core if expected else (1.0 if is_core else 0.0))
            crit_fracs.append(res["crit_frac"])
        infinite_ammo = bool(buffs.get("infinite_ammo"))
        is_last = (self.ammo == 1 and not infinite_ammo)
        if self._in_weapon_change:
            # weapon_change의 duration_bullets 카운트 (_fire()와 동일 취지).
            # _tick_charge()는 _fire()를 거치지 않고 자체 발사 처리를 하므로 여기에도 필요하다.
            self._wc_shots += 1
        if not infinite_ammo:
            self.ammo -= 1
            if self._sim_log is not None:
                self._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=self.name, ammo=self.ammo))
            bm.notify("squad_ammo_consume", t, self.name)
        # 발사 → 명중 순서, 명중은 탄 단위(총구 수만큼) — `_fire()`와 같은 규약이다.
        # `풀 차지 공격 시`(발사)와 `풀 차지 공격 명중 시`(명중)도 같은 축으로 가른다.
        bm.notify("on_attack", t, self.name)
        if is_full:
            bm.notify("full_charge_fire", t, self.name)
        else:
            # `풀 차지 공격이 아닌 일반 공격` — 톡톡이(논차지 샷)에만 나간다.
            # 컨트롤이 없으면 차지 무기의 모든 발사가 풀차지라 이 이벤트는 한 번도 안 난다
            # (크러스트 `마이야르`·`든든한 요리`가 컨트롤 없이는 통째로 죽는 이유).
            bm.notify("non_full_charge_fire", t, self.name)
        for bullet_core in _bullet_core_fracs(core_fracs, self.muzzles):
            bm.notify("hit_count", t, self.name, core_frac=bullet_core)
        if is_full:
            # 「자신이 가한 피해량의 N%」(`dealt_fixed_damage`)가 읽는 **그 탄**의 대미지 — 이미 방어력·버프·
            # 크리·코어가 적용된 값이다. 명중이 탄 단위라 이 발의 히트 합을 총구 수로 나눈다
            dealt = sum(ev.damage for ev in events) / self.muzzles
            for _ in range(self.muzzles):
                bm.notify("full_charge_hit", t, self.name, dealt=dealt)
        # 일반 공격 명중이면 충전 창·풀차지·피격 대상 종류와 무관하게 시전자 기준값을
        # 갱신한다. weapon_change 스킬 대미지는 일반 공격이 아니므로 제외한다.
        if not self._wc_is_skill_damage():
            bm.mark_normal_attack_landed(self.name)
        # 버스트 게이지. **풀차지 배율은 카메라가 이 니케를 보고 있을 때만 붙는다** —
        # 2024-04-25 "SR, RL 니케를 바라보고 있을 경우 차지 시간에 따라 버스트 게이지를
        # 추가로 획득"이 이것이다. 루주 1인 스쿼드 실측이 카메라 有 7발 / 無 18발로
        # 갈리는 것이 근거다(docs/mechanics/버스트 게이지.md).
        # 히트 수는 위 발사 루프가 센 것과 같은 값이다(펠릿 × 총구).
        if self._weapon_gauge_lands(bm):
            gauge_buffs = bm.get_buffs(self.name, "__enemy__", t)
            bm.add_burst_gauge(
                self._burst_gain(gauge_buffs, hit_count,
                                 full_charge=(is_full and self.name in bm.state["camera"])),
                t, self.name,
                "weapon:full_charge" if is_full else "weapon")
        body_ev = "squad_part_hit" if enemy.get("has_parts", False) else "squad_body_hit"
        # 히트 브로드캐스트는 **펠릿마다** 나간다 (연사 경로와 같다). 발당 1회로 세면
        # 펠릿 15짜리 모드 사격이 팀에게 1히트로 보인다.
        if not aimed:
            for core_frac in core_fracs:
                _notify_frac(bm, body_ev, self.name, 1.0 - core_frac,
                             lambda: bm.notify_team_hit(body_ev, t, self.name))
        else:
            # 좌표 모드 — 착탄점이 파츠면 파츠 명중, 표적 없는 코어 밖 본체면 본체 명중(`_coord_pellet`).
            # 표적을 겨눈 좌표 off 발은 그 표적이 파츠면 파츠 명중(`_stage_pellet`)
            for part_f, body_f in coord_fracs:
                _notify_frac(bm, "squad_part_hit", self.name, part_f,
                             lambda: bm.notify_team_hit("squad_part_hit", t, self.name))
                _notify_frac(bm, "squad_body_hit", self.name, body_f,
                             lambda: bm.notify_team_hit("squad_body_hit", t, self.name))
        if not self._wc_is_skill_damage():
            bm.consume_bullet_buffs(self.name, t)
        for crit_frac in crit_fracs:
            _notify_frac(bm, "crit_hit", self.name, crit_frac,
                         lambda: bm.notify("crit_hit", t, self.name))
        for core_frac in core_fracs:
            _notify_frac(bm, "core_hit", self.name, core_frac,
                         lambda: bm.notify("core_hit", t, self.name))
        if is_last:
            # `_fire()`와 같은 규약 — 명중이라 총구 수만큼.
            for _ in range(self.muzzles):
                bm.notify("last_bullet", t, self.name)

        # 톡톡이는 **사격 후 딜레이를 줄이는 컨트롤이다** — 풀차지로 나갔든 아니든
        # 떼기 + 덜 지운 사격 후 딜레이만 기다린다. 그래서 차지속도 버프로 차지가 짧아진
        # 구간에서는 풀차지 샷을 초당 3~4발 낼 수 있다.
        if self._tap_this_shot:
            self._post_delay_end_t = t + self._tap_release + self._tap_post
        else:
            # DOWN_Charge는 차지가 0초여도 CDN 연사속도가 주기의 하한이다.
            self._post_delay_end_t = max(t + self.post_fire_delay,
                                         self._charge_start_t + self._min_fire_cycle)
            # 엄폐 니케 + 재장 ≥100%: 딜레이 중 자동재장전 예약 (장탄 유지)
            if self.cover_during_delay and buffs.get("reload_speed_pct", 0.0) >= 100.0:
                self._pending_auto_reload = True
        self._charge_phase = "post_delay"
        self._charge_full_t = -1.0
        self._hold_release_t = -1.0
        self._hold_until_close_entry = None
        bm.state.setdefault("charging", {})[self.name] = False
        bm._invalidate_buffs_cache()
        return events

    # ── weapon_change ─────────────────────────────────────────────────────

    def _apply_wc_first_coeff(self) -> None:
        """무기 변경 세션의 **첫 발**만 `최초 대미지` 계수로 쏘게 한다.

        `self.weapon`은 `_tick_weapon_change()`가 만든 임시 dict이고 그 함수가 발사
        처리 후 원복하므로, 여기서 복사본으로 갈아끼워도 기본 무기는 오염되지 않는다.
        발사 처리 **직전**에 호출되므로 판정 기준은 `_wc_shots == 0`이다
        (`_fire()`는 이 뒤에서, `_charge_fire()`는 대미지 계산 뒤에서 카운트를 올린다).

        한 tick에 두 발이 나갈 수 있으므로(연사 24/s + dt 0.05s) 첫 발이 아닐 때도
        **일반 계수로 되돌려** 쓴다 — 되돌리지 않으면 같은 tick의 두 번째 발까지
        최초 대미지로 나간다.
        """
        if not self._in_weapon_change or self._wc_first_coeff is None:
            return
        coeff = self._wc_first_coeff if self._wc_shots == 0 else self._wc_normal_coeff
        if coeff is not None and self.weapon.get("damage_coeff") != coeff:
            self.weapon = {**self.weapon, "damage_coeff": coeff}

    def _wc_is_skill_damage(self) -> bool:
        """지금 사격이 **스킬 대미지**로 취급되는 무기 변경 모드 안인가.

        기본은 아니다 — 모드 사격도 일반 공격이라는 게 일반 규칙이고
        (`docs/GAMEPLAY.md` §무기 변경), 예외만 효과에 `skill_damage`로 적는다.
        스킬 대미지인 모드는 **발수로 소모되는 버프를 먹지 않는다** — 실제 사격이
        아니라 스킬이 나가는 것이기 때문이다(유저 인게임 확인, 나유타 `기억 연소`).
        """
        return self._in_weapon_change and self._wc_skill_damage

    def _tick_weapon_change(
        self, t: float, bm: BuffManager, enemy: dict, cfg: dict, wc_eff: dict
    ) -> list[HitEvent]:
        """
        weapon_change 활성 중 발사 루프.

        발사 방식(charge / auto / auto_warmup)을 정해 `_tick_charge()` 또는
        `_tick_auto()`에 위임한다. 판정은 효과의 `charge`가 정본이고 없을 때만
        변경 무기의 `weapon_type` 기본값으로 떨어진다. 기존 CharState 필드
        (weapon, weapon_type, mech, fire_mode, pellets, charge_time_base, post_fire_delay)
        를 임시 교체하고 처리 후 원복한다.

        `duration_bullets`가 있으면 **실제 발사 발수를 세어**(`_wc_shots`) 소진 시
        end_weapon_change().
        """
        # ── 모드 진입 재장전 ──────────────────────────────────────────
        # **무기를 바꿔 드는 동안 재장전 모션이 한 번 들어간다** (드레이크 : 그레이트 빌런
        # 유저 실측 2026-09-03 — 풀버스트 잔여 8.0초에 첫 발, 그 뒤 6발은 재장전 없이 연속).
        # 길이는 고정 상수가 아니라 **이 캐릭터의 재장전 시간**이라 재장전 속도 버프를
        # 그대로 먹는다 — 유저가 잰 약 0.5초가 기본 SG 1.50초에 버프가 먹은 값이다.
        #
        # **선언한 모드만 탄다(`entry_reload`).** 인게임에서는 모든 무기 변경이 이 모션을
        # 가질 가능성이 높지만, 다른 모드는 실측이 없어 켜면 딜이 조용히 움직인다.
        # 실측이 붙는 대로 하나씩 켜고, 전원 확인되면 기본값으로 올린다.
        if (self._wc_new_session and wc_eff.get("entry_reload")
                and self._wc_entry_reload_until < 0):
            self._wc_entry_reload_until = t + self._reload_duration(bm, t)
            if self._sim_log is not None:
                self._sim_log.reload_log.append(
                    ReloadLogEntry(t=t, caster=self.name, event="모드 진입 재장전 시작"))
        if self._wc_entry_reload_until >= 0:
            if t < self._wc_entry_reload_until:
                return []
            self._wc_entry_reload_until = -1.0
            # 차지는 재장전이 끝난 **뒤에** 시작한다. 초기화하지 않으면 모드 진입 시각
            # 기준으로 차지가 이미 돌고 있던 것으로 잡혀 재장전이 공짜가 된다.
            self._charge_phase = "ready"
            self._charge_start_t = t
            self._charge_full_t = -1.0
            if self._sim_log is not None:
                self._sim_log.reload_log.append(
                    ReloadLogEntry(t=t, caster=self.name, event="모드 진입 재장전 완료"))

        # weapon_change effect의 스킬 레벨별 damage_coeff 결정
        skill_lv = _get_skill_lv(self.char, wc_eff)
        dc = wc_eff.get("damage_coeff", {})
        if isinstance(dc, dict):
            coeff = float(dc.get(skill_lv, dc.get("10", 0.0)))
        else:
            coeff = float(dc)

        # `최초 대미지` / `일반 대미지` 2단 계수. dc(=일반 대미지)는 위에서 이미 풀었고,
        # 첫 발 전용 계수만 여기서 푼다. 필드가 없으면 None → 기존 동작 그대로.
        fdc = wc_eff.get("first_damage_coeff")
        if isinstance(fdc, dict):
            self._wc_first_coeff = float(fdc.get(skill_lv, fdc.get("10", 0.0)))
        elif fdc is not None:
            self._wc_first_coeff = float(fdc)
        else:
            self._wc_first_coeff = None
        self._wc_normal_coeff = coeff
        # 모드 사격이 스킬 대미지로 취급되는 예외(나유타 `기억 연소`).
        # 기본은 일반 공격이다 — `docs/GAMEPLAY.md` §무기 변경.
        self._wc_skill_damage = bool(wc_eff.get("skill_damage"))
        self._wc_name = wc_eff.get("name", "")

        wc_weapon_type = wc_eff.get("weapon_type", "SR")
        wc_mech = _MECHANICS["weapon_type_defaults"].get(wc_weapon_type, {})
        # **변경 무기의 차지 여부도 무기 유형과 독립이다.** 원문이 차지를 적으면 차지고,
        # 무기군 기본값의 `type`은 그 표기가 없을 때의 폴백이다 — 드레이크 : 그레이트 빌런
        # `오버 오버 드라이브`가 SG인 채로 차지하는 첫 사례라 유형만으로는 못 가른다.
        # `charge`가 없는 종전 항목은 폴백이 그대로 SR/RL=차지, SMG/MG/SG=연사로 간다.
        wc_charge = wc_eff.get("charge")
        if wc_charge is None:
            wc_fire_mode = wc_mech.get("type", "charge")
        elif wc_charge:
            wc_fire_mode = "charge"
        else:
            wc_fire_mode = "auto" if wc_mech.get("type", "charge") == "charge" else wc_mech["type"]
        wc_max_ammo = wc_eff.get("max_ammo", 1)
        wc_charge_time = wc_eff.get("charge_time", 1.0)
        wc_full_charge_mult = wc_eff.get("full_charge_mult", 100.0)
        wc_reload_time = wc_eff.get("reload_time", self.weapon.get("reload_time", 1.5))
        wc_core_dmg_mult = wc_eff.get("core_dmg_mult", self.weapon.get("core_dmg_mult", 200.0))

        # 변경 무기의 발사 메카닉. 수동 실측(weapon_delays `_weapon_change`) → 스킬 텍스트에
        # 명시된 값(wc_eff) → CDN(wc_cdn) → 변경 무기군 기본값 순으로 떨어진다.
        wc_over = _DELAYS.get("_weapon_change", {}).get(self.name, {}).get(wc_eff.get("name", ""), {})
        # CDN은 변경 무기의 자기 레코드를 주지 않고 **연사만** 스킬 값 칸에 준다
        # (parse_nikke `weapon_change_fire_rate` — 효과의 `source` 슬롯으로 찾는다).
        # 값이 하나뿐이라 예열 곡선이 아니라 고정 연사로 읽어 하한·상한에 같이 둔다 — 벨벳 MG
        # 실측이 게이지 100% 고정이다. 연사 모드(auto)는 상한을 읽지 않는다.
        wc_cdn_rate = (self.weapon.get("weapon_change_fire_rate") or {}).get(wc_eff.get("source", ""))
        wc_cdn = {"fire_rate": wc_cdn_rate, "fire_rate_max": wc_cdn_rate} if wc_cdn_rate else None
        # **딜레이 두 키도 같은 3계층을 탄다.** 종전에는 이 둘만 `wc_over`보다 위에서
        # 계산돼 실측층을 건너뛰었다 — `_weapon_change`에 적어도 조용히 무시됐다는 뜻이다
        # (weapon_delays.json `_comment`가 선언한 우선순위와 어긋났다).
        wc_post_fire_delay = _pick("post_fire_delay", wc_over, wc_eff, wc_mech, default=0.0)
        wc_fire_rate = float(_pick("fire_rate", wc_over, wc_eff, wc_cdn, wc_mech,
                                   default=wc_mech.get("fire_rate_min", 1.0)))
        wc_fire_rate_max = _pick("fire_rate_max", wc_over, wc_eff, wc_cdn, wc_mech)
        wc_warmup_bullets = float(_pick("warmup_bullets", wc_over, wc_eff, wc_mech, default=1.0))
        wc_pellets = int(_pick("pellets", wc_over, wc_eff, wc_mech, default=1))
        wc_muzzles = int(_pick("muzzles", wc_over, wc_eff, default=1))
        # 변경 무기의 버스트 게이지는 CDN에 없어(연사만 있다) 무기군 기본값으로 떨어진다
        # (weapon_mechanics.json weapon_type_defaults.burst_energy).
        wc_burst_energy = float(_pick("burst_energy", wc_over, wc_eff, wc_mech, default=0.0))

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
        orig_burst_energy      = self.burst_energy
        orig_fire_rate         = self.fire_rate
        orig_fire_rate_max     = self.fire_rate_max
        orig_warmup_bullets    = self.warmup_bullets
        orig_charge_time       = self.charge_time_base
        orig_post_delay        = self.post_fire_delay
        orig_cover_during_delay = self.cover_during_delay
        orig_min_fire_cycle    = self._min_fire_cycle
        orig_ammo              = self.ammo if not was_ready else None

        self.weapon              = wc_weapon_dict
        self.weapon_type         = wc_weapon_type
        self.mech                = wc_mech or orig_mech
        self.fire_mode           = wc_fire_mode
        self.pellets             = wc_pellets
        self.muzzles             = wc_muzzles
        self.burst_energy        = wc_burst_energy
        self.fire_rate           = wc_fire_rate
        self.fire_rate_max       = wc_fire_rate_max
        self.warmup_bullets      = wc_warmup_bullets
        self.charge_time_base    = wc_charge_time
        self.post_fire_delay     = wc_post_fire_delay
        self.cover_during_delay  = _pick("cover_during_delay", wc_over, wc_eff,
                                         default=self.cover_during_delay)
        # 주기 하한은 `DOWN_Charge`에만 거는데 변경 무기의 발사 입력은 CDN에 없다 — 원래 무기의
        # 하한을 물려주지 않는다.
        self._min_fire_cycle     = 0.0

        # 실효 최대 장탄. 스킬 텍스트에 `(사용 무기 변경 시 최대 장탄 수 효과 갱신)`이 있는
        # 무기 변경만 최대 장탄 수 버프를 받는다(`max_ammo_buff_applies`). 문구가 없으면 표기 고정.
        # 판단은 `_full_ammo()` 한 곳이 한다 — 재장전·탄환 충전 상한도 같은 값을 봐야 한다.
        if wc_max_ammo == -1:
            wc_ammo_full = 999999
        else:
            wc_ammo_full = self._full_ammo(bm, t)

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
            self._wc_ammo_borrowed = True
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
        self.burst_energy        = orig_burst_energy
        self.fire_rate           = orig_fire_rate
        self.fire_rate_max       = orig_fire_rate_max
        self.warmup_bullets      = orig_warmup_bullets
        self.charge_time_base    = orig_charge_time
        self.post_fire_delay     = orig_post_delay
        self.cover_during_delay  = orig_cover_during_delay
        self._min_fire_cycle     = orig_min_fire_cycle
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
            self._wc_entry_reload_until = -1.0
            # 마지막 발과 같은 tick에 잡힌 변경 무기 재장전 예약은 무효
            # (변경 무기는 재장전하지 않는다 — 장탄 소진이 곧 모드 종료).
            # **연사 모드만이 아니다** — 차지 모드도 모드 안에서 잡힌 재장전을 들고
            # 나오면 만탄으로 복귀한 직후에 그 재장전이 그대로 돌아간다.
            self.reloading_until = -1.0
            self.next_fire_time = t
            # **모드가 끝나면 원래 무기는 만탄으로 돌아온다** (유저 확인 2026-08-08·2026-09-09).
            # 발사한 발수도 지속시간도 진입 전 잔탄도 보지 않는다 — `_buffed_ammo`로 그 캐릭터의
            # **실효** 최대 장탄을 채운다(장비 옵션·큐브·소장품·스킬 버프 반영. `self.weapon`은
            # 위에서 이미 원래 무기로 원복돼 있다). 여기서 `_full_ammo`를 부르면 안 된다 —
            # `end_weapon_change`가 아직 아래에 있어 모드가 살아 있고, 그쪽은 **모드 장탄**을 준다.
            #
            # 종전에는 `orig_ammo`(진입부에서 잡은 잔탄)로 되돌렸는데, 그 값은 발사가 일어나는
            # tick에서 `was_ready`가 거짓이라 **모드의 잔탄**으로 잡혔다 → SMG가 1발만 들고
            # 나와 곧바로 재장전이 삽입됐다. 츠바이·스노우 화이트·맥스웰·E.H.가 모두 그랬다.
            # `탄환 N% 제거`가 붙은 모드(라플라스 : 얼티밋 히어로·드레이크 : 그레이트 빌런)는
            # 아래 종료 이벤트가 만탄을 덮어 정상적으로 재장전한다 — 그래서 순서가 이대로여야 한다.
            self.ammo = self._buffed_ammo(bm, t)
            self._wc_ammo_full = None
            self._wc_ammo_borrowed = False   # 여기서 이미 원복했다 (tick의 만료 처리와 중복 금지)
            self._wc_ammo_restored = True    # 〃 — tick 쪽이 덮어쓰지 않도록
            # 장탄 원복이 끝난 뒤에 종료 이벤트를 쏜다 — event:state_end로 발동하는
            # 장탄 조작 효과(라플라스 `탄환 100% 제거`)가 원복에 덮이지 않도록.
            bm.end_weapon_change(self.name, t)

        return events

    def _fixed_charge_time(self, bm: BuffManager) -> float:
        """charge_time_fixed 버프의 fixed_value(초). 복수이면 가장 나중에 부여된 값.

        fixed_value 없이 stat만 붙은 버프는 "차지 속도 버프를 무시하고 표기 시간으로
        고정"이므로 후보가 없으면 charge_time_base를 그대로 쓴다. 원문에 초 수치가
        적혀 있으면 그 값이 `fixed_value`로 파싱돼 있어야 한다 — 빠뜨리면 여기로
        떨어져 **단축이 통째로 사라진다**(아니스 : 스타 `슈팅 스타2`가 그 사례였다).

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

    def _has_infinite_ammo(self, bm: BuffManager, t: float) -> bool:
        """현재 장탄 수 무한 버프가 활성인가."""
        return bool(bm.get_buffs(self.name, "__enemy__", t).get("infinite_ammo"))

    def _fixed_reload_time(self, bm: BuffManager) -> float | None:
        """reload_time_fixed 버프의 고정 재장전 시간(초). 복수이면 최대값. 없으면 None.

        _active를 직접 읽는다 (고정값 계열은 get_buffs의 수치 합산 경로를 타지 않는다).

        `fixed_value`뿐 아니라 레벨별 `values`도 읽는다 — **"고정"은 *다른 버프의 영향을
        받지 않는다*는 뜻이지 *스킬 레벨과 무관하다*는 뜻이 아니다.** 원문이
        `[재장전 속도 {0}% 증가 상태로 고정]`이면 레벨마다 고정값이 다르다
        (질 `슈퍼 캅` — Lv1 0.454s ~ Lv10 0.0004s). `values`만 있는 항목을 건너뛰면
        후보가 비어 고정이 통째로 무시되고 재장전이 기본 시간으로 돌아간다.
        """
        max_val: float | None = None
        for ab in bm._active:
            if ab.effect.get("stat") != "reload_time_fixed":
                continue
            if self.name not in (ab.target_chars or []):
                continue
            val = bm._get_value(ab.effect, ab)
            if val is not None:
                max_val = float(val) if max_val is None else max(max_val, float(val))
        return max_val

    # ── 컨트롤 실행층 (정본: docs/CONTROL.md) ──────────────────────────
    #
    # 조작 원시타입은 둘뿐이고 둘 다 시작·끝을 가진 구간이다:
    #   click : 누르는 동안 차지, 떼는 순간 발사. 짧게 끊으면 톡톡이, 길게 잡으면 홀드
    #   cover : 구간 내내 사격·차징 안 함. 진입 시 재장전이 걸린다
    # 엄폐 중에는 차징도 사격도 불가능하므로 두 컨트롤은 구조적으로 충돌하지 않는다.
    # 정책(기본 전략)과 명시 시퀀스는 이 구간을 만드는 생산자일 뿐, 실행층은 둘을 구분하지 않는다.

    def _tick_cover(self, t: float) -> bool:
        """엄폐 구간의 만료를 처리하고 '지금 엄폐 중인가'를 반환."""
        if self._cover_until_reload:
            if self.reloading_until > 0:
                return True
            self._exit_cover(t)   # duration 미지정 = 재장전이 끝나는 순간 이탈
            return False
        if self._cover_until > 0:
            if t < self._cover_until:
                return True
            self._exit_cover(t)
        return False

    def _expire_timed_cover(self, t: float, bm: BuffManager) -> None:
        """유한 엄폐 종료 시 진행 중 재장전을 실전처럼 끊는다.

        탄이 남아 있으면 즉시 사격으로 복귀할 수 있다. 탄창이 0이면 클립 하나가
        들어오기 전에는 쏠 수 없으므로, 다음 `_finish_reload()` 직후 취소를 예약한다.
        duration 없는 엄폐는 완충까지 유지되므로 이 경로를 타지 않는다.
        """
        if self._cover_until <= 0 or t < self._cover_until:
            return
        self._exit_cover(t)
        if self.reloading_until <= 0:
            return
        if self.ammo > 0:
            self._cancel_reload(t, bm, "재장전 취소(엄폐 해제)")
        else:
            self._reload_cancel_after_clip = True

    def _enter_cover(self, t: float, bm: BuffManager, duration: float | None, label: str,
                     ctrl_input: str = "cover", priority: int = 0):
        """엄폐 진입 — 사격·차징을 멈추고, 탄이 덜 찼으면 재장전을 건다.

        `duration=None`이면 재장전이 끝나는 순간까지만 엄폐한다. 재장전보다 길게 잡으면
        그만큼 사격이 더 멈춘다 — 재장전을 직접 걸던 종전 모델로는 표현할 수 없던 구간이다.

        `priority`는 이 구간을 연 정책의 등급이다. 구간이 열려 있는 동안의 "엄폐 유지"
        요청이 이 값을 그대로 쓴다 — docs/CONTROL.md §조작자는 한 명.
        """
        self._ctrl_open_prio = priority
        if duration is None:
            self._cover_until_reload = True
            self._cover_until = -1.0
        else:
            self._cover_until_reload = False
            self._cover_until = t + float(duration)
        self._reload_cancel_after_clip = False
        # 엄폐하면 들고 있던 차지는 무효다 (재장전이 걸리지 않는 경우에도 마찬가지)
        if self.fire_mode == "charge":
            self._charge_phase = "ready"
        self._charge_full_t = -1.0
        self._hold_release_t = -1.0
        self._hold_until_close_entry = None
        bm.state.setdefault("charging", {})[self.name] = False
        bm.notify("event:cover", t, self.name)
        # 엄폐는 클릭을 대체한다 — 열려 있던 클릭 구간을 닫고 엄폐 구간을 연다.
        # `cover_all`(space)은 **버튼 하나로 전원**이라 카메라를 잡지 않는다 — 그래서 원시
        # 입력을 따로 남기고 점유 계산에서 뺀다(docs/CONTROL.md §조작자는 한 명).
        self._cover_all = ctrl_input == "cover_all"
        self._open_ctrl(t, ctrl_input, "cover", label)
        # 엄폐와 재장전은 별개 사건이다 — 탄이 만렙이면 엄폐만 하고 재장전은 걸리지 않는다.
        # 엄폐 로그를 재장전에 얹으면 그 경우가 통째로 안 보인다.
        if self._sim_log is not None:
            self._sim_log.reload_log.append(ReloadLogEntry(t=t, caster=self.name, event=label))
        # 이미 재장전 중이면 다시 걸지 않는다 — 걸면 진행 중인 재장전이 처음부터 다시 시작된다
        if self.reloading_until <= 0 and self.ammo < self._full_ammo(bm, t):
            self._start_reload(t, bm)
        bm._invalidate_buffs_cache()

    def _exit_cover(self, t: float):
        self._close_ctrl(t)
        self._cover_until = -1.0
        self._cover_until_reload = False
        # 엄폐 동안 밀린 발사를 몰아 쏘지 않는다 (weapon_change 이탈과 같은 취지)
        self.next_fire_time = max(self.next_fire_time, t)
        if self.fire_mode == "charge":
            self._charge_phase = "ready"

    def cover_blocked(self, t: float, bm: BuffManager) -> bool:
        """`cover_disabled`(「특이 사항 : 버스트 스킬 시전 중 엄폐 불가」)가 켜져 있는가.

        켜져 있으면 **엄폐 진입이 막힌다**(유저 결정 2026-09-14) — 정책·명시 시퀀스·전체 엄폐가
        전부 이 한 곳을 본다. 재장전은 그대로 하지만 엄폐물 뒤가 아니다(`in_cover`).
        이미 엄폐 중일 때 켜지면 그 자리에서 엄폐가 풀린다(`_drop_blocked_cover`).
        """
        return bm.has_live_stat(self.name, "cover_disabled", t)

    def _drop_blocked_cover(self, t: float, bm: BuffManager) -> None:
        """엄폐 중에 `cover_disabled`가 켜지면 **즉시 엄폐가 풀린다**(유저 확인 2026-09-15).

        자세만 푼다 — 진행 중인 재장전은 끊지 않는다(엄폐 불가여도 재장전은 한다, 엄폐물 뒤가 아닐
        뿐이다). 이번 사이클의 엄폐 앵커는 되돌리지 않는다: 풀린 엄폐를 모드가 끝난 뒤 다시 열지 않는다.
        """
        if not (self._cover_until_reload or self._cover_until > 0):
            return
        if not self.cover_blocked(t, bm):
            return
        self._exit_cover(t)
        if self._sim_log is not None:
            self._sim_log.reload_log.append(ReloadLogEntry(t=t, caster=self.name,
                                                           event="엄폐 해제(엄폐 불가)"))

    def in_cover(self, t: float) -> bool:
        """보스 공격이 엄폐물에 막히는 자세인가.

        엄폐 구간(컨트롤)이거나 **재장전 중**이다 — 재장전은 엄폐해서 한다(GAMEPLAY §컨트롤,
        자동 재장전도 엄폐물 뒤에서 한다 — 유저 확인 2026-09-15).
        """
        return (self._cover_until_reload or (self._cover_until > 0 and t < self._cover_until)
                or self.reloading_until > 0)

    def _reset_action(self, t: float, bm: BuffManager) -> None:
        """전투불능·부활 공용 — 진행 중인 조작·재장전·차지를 전부 내려놓는다."""
        self._close_ctrl(t)
        self._cover_until = -1.0
        self._cover_until_reload = False
        self._reload_cancel_after_clip = False
        self.reloading_until = -1.0
        self._post_reload_end_t = -1.0
        if self.fire_mode == "charge":
            self._charge_phase = "ready"
        self._charge_full_t = -1.0
        self._hold_release_t = -1.0
        bm.state.setdefault("charging", {})[self.name] = False

    def on_down(self, t: float, bm: BuffManager) -> None:
        self._reset_action(t, bm)

    def on_revive(self, t: float, bm: BuffManager) -> None:
        """부활 — 만탄으로 바로 싸운다. 밀린 발사를 몰아 쏘지 않는다."""
        self._reset_action(t, bm)
        self.ammo = self._full_ammo(bm, t)
        self.next_fire_time = t

    def _pump_ctrl_seq(self, t: float, bm: BuffManager) -> bool:
        """명시 시퀀스 — 정책과 같은 입구로 들어가는 또 하나의 액션 생산자.

        기본 전략(정책)이 표현하지 못하는 복잡한 조작 시퀀스를 시각으로 직접 적는 통로다.
        유저가 시각을 콕 집은 것이므로 정책보다 우선하고, 엄폐 중이어도 적용된다.
        엄폐를 열었으면 True (그 틱은 자세 전환으로 소비된다).
        """
        entered = False
        while self._ctrl_seq_i < len(self._ctrl_seq):
            act = self._ctrl_seq[self._ctrl_seq_i]
            if t < float(act.get("t", 0.0)):
                break
            self._ctrl_seq_i += 1
            kind = act.get("action")
            if kind == "cover" and self.cover_blocked(t, bm):
                # 지정한 조작이 조용히 사라지지 않게 로그에 남긴다
                if self._sim_log is not None:
                    self._sim_log.reload_log.append(
                        ReloadLogEntry(t=t, caster=self.name, event="엄폐 불가(시퀀스 무시)"))
            elif kind == "cover":
                self._enter_cover(t, bm, act.get("duration"), "엄폐(시퀀스)",
                                  priority=_PRIO_SEQ)
                entered = True
            elif kind == "hold" and self.fire_mode == "charge":
                # 다음 풀차지를 `until`(절대 시각)까지 들고 있는다. until이 없으면 홀드하지 않는다.
                # 절대 시각이라 릴리즈가 안 와서 영원히 안 쏘는 폭주가 구조적으로 없다.
                until = act.get("until")
                self._hold_until_close_entry = None
                self._hold_release_t = -1.0 if until is None else float(until)
                self._ctrl_open_prio = _PRIO_SEQ
        return entered

    def _apply_cover_policy(self, t: float, bm: BuffManager) -> bool:
        """기본 전략(정책)들의 진입점. 조건이 맞으면 엄폐 구간을 하나 연다. 열었으면 True.

        정책은 여럿이지만 만들어 내는 구간은 하나(cover)뿐이라, 이미 엄폐 중이면 아무도
        새로 열지 않는다 — 정책 간 우선순위 판정이 필요 없는 이유다. 다만 **버스트 엄폐컨을
        먼저 본다**: 구간이 훨씬 길고, 장전컨이 노리는 재장전은 그 구간 안에서 어차피 따라온다.
        """
        if not self._owns(bm):
            return False  # 카메라를 잡고 있지 않다 (docs/CONTROL.md §조작자는 한 명)
        if self._cover_until_reload or self._cover_until > 0:
            return False  # 이미 엄폐 중
        # 모드 탄창 로직을 흔들지 않도록 weapon_change 중에는 걸지 않는다
        if self._in_weapon_change or bm.get_weapon_change(self.name) is not None:
            return False
        return self._apply_burst_cover(t, bm) or self._apply_reload_cover(t, bm)

    def _apply_click_schedule(self, t: float, bm: BuffManager) -> None:
        """클릭 스케줄의 **떼는 시점**을 갱신한다. 정본: docs/CONTROL.md §홀드.

        누름(톡톡이)은 차지 시작 시점에 래치하지만(`_tick_charge()`), 홀드는 **떼는 시점을
        고르는** 조작이라 매 틱 평가한다 — 풀버스트가 시작되면 이미 차고 있던 한 발도 그대로
        들고 있는 것이 실제 조작이다.

        `hold`       풀버스트 종료 `lead`초 전을 떼기 시각으로 잡는다. 그때까지는 풀차지에
                     도달해도 발사하지 않으므로 **발수로 소모되는 버프가 유지되고**, 그 구간의
                     스킬 대미지가 전부 그 버프를 받는다. 마지막 한 발도 같은 버프를 싣는다.
                     엄폐컨과 목적이 같지만 차지형은 이쪽이 낫다 — 엄폐는 차지를 버리는데
                     홀드는 들고 있는 동안 차지 배율까지 챙긴다.
        `hold_until_close` 끝 시각을 예측하지 않고 상태 창이 닫히는 틱에 즉시 뗀다.
                     `burst_chain`과만 짝지어 게이지 충족 뒤부터 풀버스트 시작까지 한 발을 아낀다.
        `hold_judge` `charge_hold:N` 판정이 본인 버스트가 끝난 직후에 떨어지도록 차지 시작
                     시각을 역산한다 (밀크 : 블루밍 바니 부끄러움).
        """
        # 동적 창이 닫힌 틱에는 조작 소유권과 무관하게 홀드를 푼다. warn/shared에는 소유권
        # 전환이 없고, solo에서도 이 함수가 방어선이 되어 무한 홀드를 막는다.
        if (self._hold_until_close_entry is not None
                and not self._when_open(self._hold_until_close_entry, t, bm)):
            self._hold_until_close_entry = None
            self._hold_release_t = -1.0
            self._ctrl_anchor_kind = ""
            self._close_ctrl(t)
        if self.fire_mode != "charge" or not self._owns(bm):
            return
        entry = self._click_entry(t, bm, _CLICK_HOLD_MODES)
        if entry is None:
            return
        if entry["mode"] == "hold_until_close":
            self._hold_until_close_entry = entry
            self._hold_release_t = math.inf
            self._ctrl_open_prio = entry["_prio"]
            # 창이 열리기 전에 시작한 차지도 여기서부터 사용자가 들고 있는다. 차지 시작점의
            # `_tick_charge()`만 로그를 열게 두면 그런 사이클이 조작 점유에서 통째로 빠진다.
            self._open_ctrl(t, "click", entry["mode"], _when_label(entry))
            return
        # 떼는 시각은 **창 끝 `lead`초 전**이다. 상태 창은 그 끝이 풀버스트 종료이고,
        # 앵커 구간은 `앵커+오프셋+len`이다. 사이클당 1회 가드는 둘 다 **앵커 기준값**으로
        # 판정한다 — 구간 끝은 오프셋을 타서 같은 사이클인지 물을 수 없다.
        if entry.get("window") is not None:
            anchor = end = bm.state.get("full_burst_end_t", -1.0)
        else:
            at = self._anchor_at(entry, t, bm)
            if at is None:
                return
            anchor, end = at[0], at[1] + entry["len"]
        if anchor <= 0 or anchor == self._hold_ctrl_anchor:
            return  # 이 사이클에서 이미 걸었다
        self._hold_ctrl_anchor = anchor
        self._ctrl_anchor_kind, self._ctrl_anchor_val = "hold", anchor
        # 이 사이클의 홀드는 이 등급으로 연다 — `hold_judge`는 떼기 시각이 나중(차지 시작
        # 시점)에 잡히지만 등급은 여기서 정해진다.
        self._ctrl_open_prio = entry["_prio"]
        lead = float(entry.get("lead", _HOLD_LEAD_DEFAULT))

        if entry["mode"] == "hold":
            self._hold_release_t = end - lead
            return

        # `charge_hold_after_fb` — 본인 버스트가 **끝난 직후에** `charge_hold:N` 판정이
        # 떨어지도록 차지 시작 시각을 역산한다. 밀크 : 블루밍 바니의 부끄러움 조작이다:
        # 버스트 중에는 `부끄러움 면역`이라 판정이 헛돌고, 판정은 차지당 1회뿐이므로
        # **버스트가 끝나갈 때 차지를 시작**해야 한다 (정본: docs/CONTROL.md §홀드).
        #
        #   판정 시각 = 풀버스트 종료 + lead
        #   차지 시작 = 판정 시각 − 차지 시간 − 유지 임계
        #
        # 그때까지는 사격을 보류한다(엄폐가 아니라 손을 떼고 기다리는 조작).
        thresholds = bm.charge_hold_thresholds(self.name)
        if not thresholds:
            return  # `charge_hold:N`을 쓰지 않는 캐릭터에는 의미가 없다
        need = thresholds[-1][0]
        self._ch_judge_t = anchor + lead
        self._ch_charge_start_t = self._ch_judge_t - self._effective_charge_time(bm, t) - need

    def _apply_burst_cover(self, t: float, bm: BuffManager) -> bool:
        """버스트 엄폐컨 — 본인이 버스트를 쓴 사이클의 풀버스트 동안 엄폐한다.
        정본: docs/CONTROL.md §버스트 엄폐컨.

        `own_full_burst`: 풀버스트가 시작됐고 이번 사이클에 본인이 버스트를 썼으면,
        풀버스트가 끝날 때까지(+`extend`) 엄폐해 한 발도 쏘지 않는다. 종료 시각은
        진입 시점에 확정돼 있으므로(`full_burst_end_t`) 예측이 필요 없다 — 정책 A와 같다.

        **탄약 상태를 보지 않는다.** 목적이 재장전이 아니라 "쏘지 않는 것"이기 때문이다.
        재장전 중이어도 엄폐에 들어간다(어차피 쏘지 못하는데 자세만 다른 상태다).
        """
        req = self._want_burst_cover(t, bm)
        if req is None:
            return False
        anchor, duration = req
        self._cover_ctrl_anchor = anchor
        self._ctrl_anchor_kind, self._ctrl_anchor_val = "cover", anchor
        self._enter_cover(t, bm, duration, "엄폐 시작(버스트 엄폐컨)",
                          priority=self.cover_priority)
        return True

    def _want_burst_cover(self, t: float, bm: BuffManager) -> tuple[float, float] | None:
        """버스트 엄폐컨이 지금 열리고 싶은가 — **부작용 없이** 묻는다. (앵커, 지속)|None.

        조율 단계(`_arbitrate_control()`)가 카메라 주인을 정하려면 정책에 부작용 없이
        물어볼 수 있어야 한다 — docs/CONTROL.md §판정 자리.
        """
        if self.cover_policy != "own_full_burst":
            return None
        if self.cover_blocked(t, bm):
            return None
        if not bm.state.get("full_burst", False):
            return None
        if not bm.state.get("burst_casted", {}).get(self.name):
            return None
        anchor = bm.state.get("full_burst_end_t", -1.0)
        if anchor <= 0 or anchor == self._cover_ctrl_anchor:
            return None  # 이 사이클에서 이미 걸었다
        duration = anchor - t + self.cover_extend
        if duration <= 0:
            return None
        return anchor, duration

    def _apply_reload_cover(self, t: float, bm: BuffManager) -> bool:
        """장전컨 — 재장전을 유리한 구간에 밀어 넣는다. 정본: docs/CONTROL.md §장전컨.

        **진입 시각은 앵커 하나로 적는다** — `t >= anchor + offset − minus`.
        정책 넷은 그 한 줄의 특수화이고, 조립 시점에 desugar된다(`_RELOAD_POLICIES`):

        A `before_fb_end`   `{fb_end, offset: −lead}`
              풀버스트 종료 `lead`초 전에 엄폐. 종료 시각이 확정돼 있어 예측이 필요 없다.
              재장 0초 구간을 놓치지 않는 용도.
        B `into_fb`         `{next_fb_start, offset: +margin, minus: reload_total}`
              다음 풀버스트 시작 직후(`margin`초 뒤)에 재장전이 끝나도록 역산. 시작 시각은
              직전 사이클 주기로 예측한다. 완료가 시작보다 빠르면 최대장탄 증가 버프를
              놓치므로 margin>0.
        C `finish_by_fb_end` `{fb_end, offset: −margin, minus: reload_total}`
              풀버스트가 **끝나기 전에 재장전이 끝나도록** 역산. 버스트 게이지 충전 창을
              만탄으로 여는 조작이다 — 창이 2~5초라 거기서 재장전이 걸리면 그 사이클의
              버충이 통째로 날아간다. A와 달리 진입 시각이 고정 오프셋이 아니라 **그 시점의
              실제 재장전 시간**에서 나오고, `minus`가 있는 이유가 그것이다.
        D `finish_by_own_buff_end` `{own_buff_end, offset: −margin, minus: reload_total}`
              본인이 직접 발동한 이름 있는 버프가 끝나기 전에 재장전을 완료한다. 대상을
              조회하지 않아 지연 resolve의 결정 시점을 앞당기지 않는다.

        `if_dry`를 켜면 그 시점에 남은 장탄을 보고 어차피 비버스트에 재장전이 걸릴 때만
        건다 (아래 §소진 예측). 기준이 이번 풀버스트 종료 시각이라 `fb_end`계 앵커 전용이다.
        """
        anchor = self._want_reload_cover(t, bm)
        if anchor is None:
            return False
        self._reload_ctrl_anchor = anchor
        self._ctrl_anchor_kind, self._ctrl_anchor_val = "reload", anchor
        self._enter_cover(t, bm, self.reload_cover_dur, "엄폐 시작(장전컨)",
                          priority=self.reload_priority)
        return True

    def _want_reload_cover(self, t: float, bm: BuffManager) -> float | None:
        """장전컨이 지금 열리고 싶은가 — **부작용 없이** 묻는다. 앵커 시각 또는 None."""
        if self.reload_when is None:
            return None
        if self.cover_blocked(t, bm):
            return None
        if self.reloading_until > 0 or self._post_reload_end_t > 0:
            return None
        if self.ammo >= self._full_ammo(bm, t):
            return None

        # **정책 네 갈래가 이 두 줄로 접힌다.** 앵커가 자기 게이트를 들고
        # (A·C의 `full_burst`, B의 관측치, D의 본인 발동 버프), `lead`·`margin`의 차이는
        # 조립 시점에 오프셋으로 흡수됐다 — `_build_reload_when()`.
        at = self._anchor_at(self.reload_when, t, bm)
        if at is None or t < at[1]:
            return None
        anchor = at[0]
        if self.reload_if_dry and not self._dry_before_next_fb(t, bm, anchor):
            return None

        if anchor == self._reload_ctrl_anchor:
            return None  # 이 사이클에서 이미 걸었다
        return anchor

    def _dry_before_next_fb(self, t: float, bm: BuffManager, fb_end: float) -> bool:
        """남은 장탄으로 다음 풀버스트 시작까지 버티지 못하면 True (`reload.if_dry`).

        "어차피 비버스트에 재장전이 걸릴 상황이냐"를 판정한다. 버텨 낼 시간은
        **풀버스트 잔여 + 비버스트 구간 전체**다 — 다음 풀버스트가 시작된 뒤에
        비는 건 그 구간에서 채우면 되므로 여기서 볼 일이 아니다.

            버텨야 하는 시간 = (풀버스트 종료 - 현재) + (다음 풀버스트 시작 - 풀버스트 종료)
            쏠 수 있는 시간  = 남은 장탄 / 현재 연사 속도

        다음 풀버스트 시작은 정책 B와 같은 관측치(`next_fb_start_pred`, 직전 사이클
        주기)를 쓴다. **관측치가 없는 첫 사이클에는 걸지 않는다** — 비버스트가
        얼마나 긴지 모르는 채로 거는 재장전은 판정이 아니라 추측이다.

        연사 속도는 판정 시점의 값을 그대로 쓴다. 판정 시점이 풀버스트 끝자락이라
        MG는 예열이 최고로 오른 상태이고, 재장전을 거치면 예열이 식어 실제로는 더
        느리게 쏜다 — 그래서 이 예측은 **마르는 쪽으로 보수적**이다.
        """
        nxt = bm.state.get("next_fb_start_pred", -1.0)
        if nxt <= 0:
            return False
        need = (fb_end - t) + max(0.0, nxt - fb_end)
        have = self.ammo / max(self._current_fire_rate(bm, t), 0.01)
        return have < need

    def _reload_duration(self, bm: BuffManager, t: float) -> float:
        """현재 버프를 반영한 재장전 **1회** 소요 시간(초).

        클립 무기에서는 이게 클립 하나를 채우는 시간이다. 탄창이 다 찰 때까지의
        시간이 필요하면 `_reload_total_duration()`을 쓴다.
        """
        fixed = self._fixed_reload_time(bm)
        if fixed is not None:
            # "재장전 시간 N초로 고정" — 절대 고정이라 reload_speed_pct를 타지 않는다
            return fixed
        speed_pct = bm.get_buffs(self.name, "__enemy__", t).get("reload_speed_pct", 0.0) / 100.0
        return self.weapon["reload_time"] * max(0.0, 1.0 - speed_pct)

    def _clip_refill(self, bm: BuffManager, t: float) -> float:
        """재장전 1회가 채우는 탄창 **비율** (0 < x ≤ 1).

        기본값은 CDN `reload_bullet`에서 온 `1 / clip_count`(통짜 1.0, 클립 SG·RL 1/3,
        그레이브 1/2)이고, 여기에 「재장전 비율 N% ▼」 버프가 **곱해진다**.
        50% ▼는 비율을 절반으로 만든다 — 그레이브 `방열`이 걸리면 1/2 → 1/4이라
        빈 탄창을 채우는 데 재장전이 2회에서 **4회**로 늘어난다 (유저 확인, 2026-08-28).
        재장전 **속도**(`reload_speed_pct`, 1회에 걸리는 시간)와는 다른 축이다.
        """
        base = 1.0 / self.clip_count
        pct = bm.get_buffs(self.name, "__enemy__", t).get("reload_ratio_pct", 0.0)
        if pct:
            base *= max(0.0, 1.0 + pct / 100.0)
        # 0으로 떨어지면 영원히 못 채운다. 1발은 채우게 두고(하한은 _clip_gain의 max(1,…)),
        # 1.0을 넘으면 통짜 재장전이다.
        return min(1.0, base)

    def _is_clip_reload(self, bm: BuffManager, t: float) -> bool:
        """지금 굴러가는 재장전이 클립 장전인가.

        기본이 통짜인 무기라도 「재장전 비율 ▼」가 걸려 있으면 클립 장전이 되므로
        정적인 `is_clip`이 아니라 **그 시점의 비율**로 판정한다.
        무기 변경 모드 중에는 탄창이 그 모드 무기의 것이므로 클립 규칙을 적용하지 않는다.
        """
        return self._clip_refill(bm, t) < 1.0 and bm.get_weapon_change(self.name) is None

    def _clip_gain(self, full: int, bm: BuffManager, t: float) -> int:
        """클립 1회가 채우는 발수 = **현재** 최대 장탄 × 채움 비율을 **반올림**한 값
        (1/3인 경우를 유저가 확인, 2026-08-19).

        장탄 증가 버프가 붙으면 클립당 발수도 같이 커진다 → 빈 탄창은 대개 3회로 찬다.
        다만 반올림이 내려가는 장탄(31발 → 클립 10발)에서는 30발까지 채운 뒤 남은 1발을
        채우는 **4번째 클립**이 붙는다. 올림으로 두면 이 한 번이 사라져 재장전이 짧아진다.
        `round()`가 아니라 `floor(x + 0.5)`인 이유는 파이썬의 은행가 반올림을 피하기 위함이다.
        """
        return max(1, math.floor(full * self._clip_refill(bm, t) + 0.5))

    def _reload_total_duration(self, bm: BuffManager, t: float) -> float:
        """지금 재장전을 시작하면 **탄창이 다 찰 때까지** 걸리는 시간(초).

        클립 무기는 남은 탄에 따라 클립을 여러 번(빈 탄창이면 3회, 반올림이 내려가는
        장탄이면 4회) 반복하므로 1회 시간과 다르다.
        장전컨 정책 B(`into_fb`)처럼 "재장전이 끝나는 시각"을 역산하는 쪽이 이걸 쓴다.
        """
        one = self._reload_duration(bm, t)
        if not self._is_clip_reload(bm, t):
            return one
        full = self._full_ammo(bm, t)
        clips = math.ceil(max(0, full - self.ammo) / self._clip_gain(full, bm, t))
        return one * max(1, clips)

    def _start_reload(self, t: float, bm: BuffManager, label: str = "재장전 시작"):
        self.reloading_until = t + self._reload_duration(bm, t)
        self._reload_in_weapon_change = bm.get_weapon_change(self.name) is not None
        # 차지 중에 재장전이 걸리면 차지는 무효다. 재장전 후에는 처음부터 다시 차지한다
        # (초기화하지 않으면 남아 있던 _charge_start_t로 재장전 직후 즉시 발사된다).
        if self.fire_mode == "charge":
            self._charge_phase = "ready"
        self._charge_full_t = -1.0
        self._hold_release_t = -1.0
        self._hold_until_close_entry = None
        bm.state.setdefault("charging", {})[self.name] = False
        bm._invalidate_buffs_cache()
        # 예열은 재장전으로 리셋되지 않는다. 재장전 동안의 미사격은 _cool_warmup이 시간 비례로 냉각.
        if self._sim_log is not None:
            self._sim_log.reload_log.append(ReloadLogEntry(t=t, caster=self.name, event=label))

    def _cancel_reload(self, t: float, bm: BuffManager,
                       label: str = "재장전 취소(탄충)"):
        """진행 중인 재장전을 **완료시키지 않고** 끊는다 (탄충 취소 컨트롤).

        `_finish_reload`와 반드시 달라야 하는 것이 둘 있다.
        - `event:full_reload`를 발동시키지 않는다. 재장전은 끝난 게 아니라 취소됐다 —
          여기서 알리면 `재장전 완료 시` 스킬이 공짜로 한 번 더 터진다.
        - 장탄을 채우지 않는다. 이미 탄환 충전이 채운 값이 정답이다.
        재장전 완료 후 딜레이(`post_reload_delay`)도 걸지 않는다. 완료 모션이 없기 때문이다.
        """
        self.reloading_until = -1.0
        self._reload_in_weapon_change = False
        self._reload_cancel_after_clip = False
        if self._sim_log is not None:
            self._sim_log.reload_log.append(
                ReloadLogEntry(t=t, caster=self.name, event=label))

    def _full_ammo(self, bm: BuffManager, t: float) -> int:
        # 무기 변경 모드 중이면 그 모드의 장탄으로 채운다
        wc_eff = bm.get_weapon_change(self.name)
        base_override: int | None = None
        if wc_eff is not None:
            wc_max = wc_eff.get("max_ammo", -1)
            if wc_max != -1:
                # 원문 `최대 장탄 수 : N발 X [게이지/스택] 개수`. **표기 장탄 자체가 카운터에
                # 비례**하므로 장탄 *버프*와는 다른 층이고, `max_ammo_buff_applies`(괄호구)와
                # 무관하게 곱한다. 값을 다시 재는 시점은 다른 장탄과 같아야 한다 —
                # `_wc_ammo_full` 캐시를 쓰는 이유가 그것이다(모드 진입·재장전 완료뿐).
                # 매 tick 재면 종료 조건(`모든 탄환 발사 시`)만 흔들려 탄이 마른 채
                # 끝나지 않는 모드가 생긴다. (E.H. `인 투 더 헤븐`)
                ref = wc_eff.get("max_ammo_scaling_ref")
                if ref:
                    if self._wc_ammo_full is None:
                        n = bm.ref_count(self.name, ref)
                        # None은 "그런 이름이 없다" → 배수 1. 0은 진짜 0이라 0발이 맞다
                        # (모드가 첫 tick에 `모든 탄환 발사 시`로 스스로 끝난다).
                        self._wc_ammo_full = int(wc_max) * (1 if n is None else int(n))
                    return self._wc_ammo_full
                # `(사용 무기 변경 시 최대 장탄 수 효과 갱신)` 문구가 없으면 표기 장탄 고정.
                if not wc_eff.get("max_ammo_buff_applies"):
                    return int(wc_max)
                # 문구가 있으면 **변경 무기의 표기 장탄을 기본값 삼아** 장탄 버프를 얹는다
                # (GAMEPLAY §무기 메카닉). 장탄 버프·장비 장탄 옵션·큐브·소장품을 가리지 않는다.
                # 값은 장탄을 채운 시점의 것을 물려 쓴다 — 위 `_wc_ammo_full` 주석.
                if self._wc_ammo_full is not None:
                    return self._wc_ammo_full
                base_override = int(wc_max)
        full = self._buffed_ammo(bm, t, base_override)
        if base_override is not None:
            self._wc_ammo_full = full   # 이 세션 동안 고정
        return full

    def _buffed_ammo(self, bm: BuffManager, t: float, base: int | None = None) -> int:
        """`base`(미지정이면 무기 기본 장탄)에 최대 장탄 버프를 얹은 실효 장탄."""
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        if base is None:
            base = self.weapon["max_ammo"]
        # 장탄 % 버프는 소스(장비 옵션 단계·큐브·소장품·스킬 버프)마다 따로 발수로
        # 반올림한 뒤 더한다 — 합산 후 한 번 반올림하면 조합에 따라 1발씩 어긋난다.
        ammo_gain = int(_quant_sum(base, buffs, "max_ammo_pct", 1.0))
        ammo_flat = int(round(buffs.get("max_ammo_flat", 0.0)))
        # 감소 버프가 겹쳐도 최대 장탄은 1발 아래로 내려가지 않는다 (GAMEPLAY.md §무기 메카닉).
        # 하한이 없으면 0발이 되어 재장전만 무한 반복하며 한 발도 쏘지 못한다.
        return max(1, base + ammo_gain + ammo_flat)

    def _finish_reload(self, t: float, bm: BuffManager):
        """재장전 1회를 완료한다. 클립 무기는 탄창이 다 찼을 때만 '완료'다.

        클립 장전은 탄창의 1/3만 채우고 곧바로 다음 클립으로 이어진다 — 중간 클립에서는
        `event:full_reload`도 `post_reload_delay`도 없다. 트리거 원문이 "최대 장탄 수
        재장전 완료 시"이므로 최대 장탄에 도달한 마지막 클립만 완료로 센다 (유저 확인,
        2026-08-19). 이어 붙이는 동안 `reloading_until`이 계속 >0이라 사격은 그대로 막힌다
        — 오토는 3연속으로 끝까지 굴린다. 엄폐를 끊어 1/3·2/3만 채우고 나오는 컨트롤은
        아직 표현하지 않는다.
        """
        # 재장전 완료는 실효 장탄을 다시 재는 두 사건 중 하나다 (GAMEPLAY §무기 메카닉).
        self._wc_ammo_full = None
        full = self._full_ammo(bm, t)
        if self._is_clip_reload(bm, t):
            self.ammo = min(full, self.ammo + self._clip_gain(full, bm, t))
            if self.ammo < full:
                if self._sim_log is not None:
                    self._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=self.name, ammo=self.ammo))
                if self._reload_cancel_after_clip:
                    self._cancel_reload(t, bm, "재장전 취소(엄폐 해제)")
                    self.next_fire_time = max(self.next_fire_time, t)
                    return
                self._start_reload(t, bm, "클립 재장전")
                return
        else:
            self.ammo = full
        self.reloading_until = -1.0
        self._reload_in_weapon_change = False
        self._reload_cancel_after_clip = False
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
        self._wc_ammo_full = None   # 재장전 완료 — 실효 장탄을 다시 잰다
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
        self._enemy = enemy
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

        # 캐릭터별 버스트 사용 패턴 — {이름: "every:3" | [1, 3, 5, ...]}.
        # **후보에서 빼는 게 아니라 그 단계의 맨 뒤로 미는 것**이다. 그래서 대신 쓸 사람이
        # 쿨이면 여전히 나가고(막히지 않는다), 대신 쓸 사람이 준비돼 있으면 그쪽이 먼저 나간다.
        # 예: 마스트 : 로망틱 메이드 `every:3` + B2 20초 동료 → 3의 배수 사이클에만 실제 사용.
        # `burst_sequence`(명시 순서)를 준 경우에는 그쪽이 전부 결정하므로 무시된다.
        self._burst_pattern: dict = config.get("burst_pattern") or {}

        # **딜레이 버스트** — 차례가 온 뒤 유저가 몇 초 뒤에 버튼을 누르나. 정본:
        # docs/CONTROL.md §L0. `{이름: 초}`이고 기본은 0(즉시)이라 안 주면 종전과 같다.
        # 러너가 `control["burst"]["delay"]`를 모아 넘긴다 — 패턴과 같은 통로다.
        self._burst_delay: dict = config.get("burst_delay") or {}
        # 지금 단계가 **열린** 시각. 딜레이는 `max(이 값, 그 사람 쿨 해제)`부터 잰다 —
        # 그게 곧 "버튼에 불이 들어온 시각"이다.
        self._stage_open_t: float = -1.0

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

        # 게이지 사이클 판정 방식 — "fixed"(종전 고정 시간) / "accumulate"(실누적).
        # 두 모델이 갈리는 곳은 `_gauge_ready()` 한 곳뿐이다. 게이지 자체는 두 모드
        # 모두에서 똑같이 계산되고 로그에 남으므로 나란히 비교할 수 있다.
        self._gauge_mode: str = config.get("burst_gauge_mode", "fixed")

        # ["fixed" 전용] 버스트 게이지 충전 완료 **시각**.
        # 첫 버스트는 burst_regen_time 무시, first_burst_time에 발동
        _first_burst_t = config.get("first_burst_time", 3.0)
        self.gauge_full_at: dict[str, float] = {
            c["name"]: _first_burst_t for c in squad
        }

        # 버스트 진행 상태
        # "idle" / "stage:N" / "reenter:N" / "switching" / "full_burst"
        self._phase: str = "idle"
        self._next_action_t: float = math.inf
        self._full_burst_end_t: float = -1.0
        # 다음 풀버스트 시작 예측 — **직전 사이클 주기 관측이 정답에 가장 가깝다.**
        # 관측치가 없는 첫 사이클에만 쿨타임 사슬로 메운다(`_predict_next_fb_start()`).
        self._last_fb_start_t: float = -1.0
        self._obs_next_fb: float = -1.0
        self._cd_next_fb: float = -1.0    # 관측이 없는 동안 쓰는 쿨타임 기반 예측

        # 쿨타임 대기 중인 단계의 후보 목록 (대기가 아니면 None).
        # _next_action_t는 두 가지가 섞여 있다 — 의도된 딜레이(단계 전환 0.1s,
        # reenter 0.5s, 풀버스트 진입 0.05s)와 "전원 쿨이라 기다린다"는 예측.
        # 앞쪽은 지켜야 하고 뒤쪽은 쿨이 바뀌면 다시 계산해야 한다.
        # 이 목록이 채워져 있을 때만 재계산해서 둘을 구분한다.
        self._cd_wait_candidates: list[str] | None = None

        # reenter 대기 중인 단계
        self._reenter_stage: str = ""

        # **이번 사이클에 재진입을 이미 연 단계.** 재진입은 단계마다 사이클당 한 번이다
        # (docs/GAMEPLAY.md §버스트 재진입 — "같은 단계를 다시 한 번 사용").
        # 상한이 없으면 재진입 자리에서 뽑힌 동료가 또 재진입 니케일 때 슬롯이 계속
        # 열려 그 단계에 영원히 갇힌다 — 풀버스트가 한 번도 돌지 않는다.
        self._reenter_done_stages: set[str] = set()

        # 풀버스트 진입 시 발동할 버스트 대미지 (버프 적용 후 계산)
        self._pending_burst_dmg: list[tuple[str, dict, int]] = []  # (caster, eff, hit_count)

        # 현재 풀버스트 사이클의 3단계 버스트 발동자와 그 발동 시각 (fullburst_duration 귀속용)
        self._fb_caster: str = ""
        self._fb_caster_t: float = -1.0

        # verbose 로그 (simulate에서 주입)
        self._log: SimLog | None = None

    @property
    def enemy_def(self):
        """적 방어력. **조회 시점에 읽는다** — 보스 패턴이 방어력을 바꾸는데 `__init__`에서
        값을 붙들어 두면 버스트 딜만 옛 방어력으로 계산된다. `enemy`는 `simulate()`가 들고
        도는 같은 dict 객체라 패턴이 없으면 늘 같은 값이다."""
        return self._enemy.get("def", 31784)

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
            # 관측이 아직 없는 사이클(= 첫 사이클)의 예측을 **여기서 한 번만** 낸다.
            # 두 가지가 이 자리를 강제한다:
            #   ① 값이 사이클 내내 고정이어야 한다. 매 틱 다시 내면 정책의 앵커가 계속
            #      바뀌어 **「사이클당 1회」 가드가 무력화**되고 같은 엄폐가 연달아 열린다.
            #   ② 첫 풀버스트 **전에는** 낼 수 없다. 그 구간을 정하는 건 쿨타임이 아니라
            #      게이지인데(전원 쿨이 0이다) 사슬은 게이지를 안 본다.
            if self._obs_next_fb <= 0.0:
                self._cd_next_fb = self._predict_next_fb_start(t)

        # ── idle → 게이지 충전 완료 시 1단계 진입 ─────────────────────────
        _at_max = (self._max_burst_count is not None and self._burst_count >= self._max_burst_count)
        if self._phase == "idle" and not _at_max:
            if self._gauge_ready(t, state):
                # 버스트 흐름 로그에는 **"accumulate"에서만** 적는다. "fixed"에서는
                # 게이지가 사이클을 판정하지 않아 이 줄이 오해를 부르고, 무엇보다
                # 종전 baseline이 한 줄도 움직이면 안 된다(회귀 판정의 기준).
                # 게이지 내역 자체는 두 모드 모두 gauge_log에 남는다.
                if self._log is not None and self._gauge_mode == "accumulate":
                    self._log.burst_log.append(BurstLogEntry(
                        t=t, event=f"게이지 만충 {state.get('burst_gauge', 0.0):.1f}% → 1단계 진입 (소모)",
                        caster=""))
                # 1단계 진입이 게이지를 소모한다. 100을 넘긴 몫은 여기서 사라진다
                # (초과분은 이월되지 않는다 — 유저 인게임 확인).
                state["burst_gauge"] = 0.0
                # 새 사이클이 시작되는 순간 비운다. 그 전까지는 직전 사이클 사용자를
                # 유지해야 풀버스트 종료 뒤 `burst_charge` 창의 게이트가 같은 답을 본다.
                state["burst_cycle_users"] = {"1": set(), "2": set(), "3": set()}
                self._reenter_done_stages.clear()
                self._phase = "stage:1"
                self._next_action_t = self._stage_open_t = t
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

        # ── 단계 스킬 사용 (본 차례 · 재진입 자리 공통) ────────────────────
        # **두 자리가 같은 코드를 쓴다.** 재진입 자리도 "그 단계를 한 번 쓰고 다음으로
        # 넘어간다"는 점에서 본 차례와 다르지 않다. 갈라 두었을 때 재진입 쪽만 `_try_use_stage`의
        # 재진입 반환값을 버려서, 재진입 니케 둘이 같은 단계에 서면 슬롯이 닫히지 않고
        # 그 단계에 갇혔다.
        if self._phase.startswith(("stage:", "reenter:")) and t >= self._next_action_t - 1e-9:
            in_reenter_slot = self._phase.startswith("reenter:")
            stage = self._reenter_stage if in_reenter_slot else self._phase.split(":")[1]

            if in_reenter_slot:
                # 재진입 단계 진입 이벤트 발생 (burst_enter:N 조건 트리거용)
                for n in self.squad_names:
                    bm.notify(f"burst_enter:{stage}", t, n)

            # 재진입 자리에서는 해당 단계 후보 중 쿨이 풀린 캐릭터를 재선출한다
            # (재진입을 연 사람은 방금 썼으므로 이미 쿨이다).
            ev, advanced, reenter_info = self._try_use_stage(stage, t, bm, state)
            events.extend(ev)

            # `reenter_info`가 돌아왔다는 것은 **버스트가 이미 나갔다**는 뜻이다 —
            # `advanced`만 False일 뿐이다. 재진입을 더 열지 않기로 해도 그 단계는 끝났으므로
            # 둘을 합쳐 "이번에 누가 썼나"로 본다.
            used = advanced or reenter_info is not None

            if reenter_info and stage not in self._reenter_done_stages:
                # reenter: 같은 단계 재진입 대기 (사용자는 딜레이 후 재선출).
                # 단계마다 사이클당 한 번만 연다 — `_reenter_done_stages` 주석 참조.
                self._reenter_done_stages.add(stage)
                self._reenter_stage = stage
                self._phase = f"reenter:{stage}"
                self._next_action_t = self._stage_open_t = (
                    t + self.config.get("burst_reenter_delay", 0.5))
            elif used:
                if stage == "3":
                    self._phase = "switching"
                    self._next_action_t = t + 0.05
                else:
                    next_stage = str(int(stage) + 1)
                    self._phase = f"stage:{next_stage}"
                    self._next_action_t = self._stage_open_t = (
                        t + self.config.get("burst_switch_delay", 0.1))
                    for n in self.squad_names:
                        bm.notify(f"burst_enter:{next_stage}", t, n)
            # used가 False면 전원 쿨타임 중이다. `_try_use_stage`가 잡아 둔 시각까지
            # 이 자리에서 기다린다 — 재진입 자리도 짝의 쿨이 돌아오면 그때 쓴다.

        # ── 전환 딜레이 → 풀버스트 진입 ───────────────────────────────────
        if self._phase == "switching" and t >= self._next_action_t - 1e-9:
            self._phase = "full_burst"
            # fullburst_duration 버프(초) 합산.
            # 동일 **효과**가 all_allies target으로 여러 캐릭터에 등록되어도
            # 풀버스트 지속 시간 기여는 1회만 집계한다. 중복 판정 키는
            # (caster, 스킬, 효과명)이다 — caster 하나로 접으면 **한 캐릭터가 가진
            # 서로 다른 두 효과**가 하나로 뭉개진다(소다 : 트윙클링 바니의
            # `시간 연장 I`(+2)·`시간 연장 II`(+3)는 `[하위 효과 중복 적용]`이라 +5여야 한다).
            # _fb_caster(3단계 발동자)의 버프는 본인이 직접 풀버스트를 발동할 때만 적용.
            seen_effects: set[tuple] = set()
            fb_ext = 0.0
            for ab in bm._active:
                if ab.effect.get("stat") != "fullburst_duration":
                    continue
                key = (ab.caster, ab.effect.get("source"), ab.effect.get("name"))
                if key in seen_effects:
                    continue
                # burst_cast 타이밍으로 등록된 fullburst_duration은
                # 해당 caster가 이번 풀버스트의 3단계 발동자이고, **이번 버스트에서 실제로
                # 부여된** 것일 때만 반영한다. 이 stat은 보관 편의상 `duration: -1`(영구)로
                # 적히므로(`PARSING.md` §4 「풀 버스트 타임 동안 지속」) 조건이 붙은 항목은
                # 한 번 켜지면 조건이 거짓이 된 뒤에도 `_active`에 남는다 — 발동 시각을 같이
                # 보지 않으면 그 뒤 모든 자기 버스트 사이클에 계속 실린다.
                # 조건이 없는 기존 보유자(모더니아 `신세계` · 이사벨 `소닉 체이서 5`)는 자기
                # 버스트마다 재발동해 `activated_at`이 갱신되므로 영향이 없다.
                # (D `처단 3` — `self_stun_immune`이 36.95초 뒤 거짓이 된다)
                timings = ab.effect.get("trigger", {}).get("timing", [])
                if "burst_cast" in timings and (
                        ab.caster != self._fb_caster
                        or ab.activated_at < self._fb_caster_t - 1e-9):
                    continue
                val = ab.effect.get("fixed_value")
                if val is None:
                    lv = _get_skill_lv(self.char_states[ab.caster].char, ab.effect)
                    vals = ab.effect.get("values", {})
                    val = float(vals.get(lv, vals.get("10", 0.0)))
                fb_ext += float(val)
                seen_effects.add(key)
            self._full_burst_end_t = t + max(1.0, 10.0 + fb_ext)
            state["full_burst"] = True
            # 장전컨(docs/CONTROL.md)이 쓰는 사이클 정보를 state에 공개한다.
            # 종료 시각은 여기서 확정 — 정책 A는 예측 없이 이 값을 그대로 쓴다.
            # 다음 시작 시각은 반응형(게이지·쿨)이라 확정할 수 없어 직전 주기로 예측한다.
            state["full_burst_end_t"] = self._full_burst_end_t
            if self._last_fb_start_t >= 0.0:
                self._obs_next_fb = t + (t - self._last_fb_start_t)
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

        # ── 충전 창 갱신 ──────────────────────────────────────────────────
        # **풀버스트가 끝나기 전까지 게이지는 차지 않는다**(유저 인게임 확인).
        # 그 조건이 `_phase == "idle"`과 정확히 같다 — stage:*/reenter:*/switching/
        # full_burst는 전부 그 바깥이다. 조건을 여기 한 줄에 가두면 발사·스킬 경로는
        # 언제 충전되는지 몰라도 되고, 초과분 폐기·소모 시점이 자동으로 따라온다.
        #
        # 이 tick()은 char_states.tick()보다 **먼저** 돈다. 그래서 프레임 t에 쏜 몫은
        # 프레임 t+1의 게이트에서 판정된다 — 1프레임(0.0167초) 지연이고, 기존 사이클
        # 판정 관례와 같다.
        state["burst_gauge_charging"] = (self._phase == "idle")

        # ── 다음 풀버스트 시작 예측 ────────────────────────────────────────
        # **관측이 있으면 관측이 이긴다.** 재 보니 직전 사이클 주기 외삽이 쿨타임 사슬보다
        # 정확했다 — 사슬은 **앞으로 들어올 쿨감을 못 보기** 때문이다
        # (`burst_cooldown_reduce`는 스킬이 뿌리는 즉시 효과라 미래 값을 알 수 없다).
        # 관측이 없는 동안만 사슬 값을 쓰고, 그 값은 풀버스트 종료 때 한 번 잡힌다.
        # 정본: docs/CONTROL.md §다음 풀버스트 예측.
        state["next_fb_start_pred"] = (
            self._obs_next_fb if self._obs_next_fb > 0.0 else self._cd_next_fb)

        return events

    def _predict_next_fb_start(self, t: float) -> float:
        """다음 풀버스트가 시작할 시각. 없으면 `-1.0`. 정본: docs/CONTROL.md §장전컨.

        **남은 버스트 쿨타임으로 단계 사슬(1→2→3)을 앞으로 굴린다.** 종전에는 직전 사이클
        주기를 그대로 다음에도 쓴다는 관측 외삽이었는데, 거기엔 두 구멍이 있었다 —
        관측치가 없는 **첫 사이클에는 값이 아예 없었고**(정책 B가 안 걸렸다),
        **딜레이 버스트가 사이클마다 다르면** 지난 주기에 섞인 딜레이가 다음 사이클을
        거짓으로 예측했다. 둘 다 "지난 사이클을 보고 다음을 짐작한다"는 데서 나온다.

        쿨타임은 **확정값**이고(`burst_ready_at` — 쿨감 버프까지 반영된 미래 시각)
        전투 시작부터 있으므로 두 구멍이 함께 사라진다. 사슬이 "누가 누를지"를 이미
        고르므로 그 사람의 딜레이도 더할 수 있다.

            열림₁ = max(기준, 게이지 준비)          기준 = 풀버스트 중이면 그 종료, 아니면 지금
            누름ₖ = min over 후보 n ( max(열림ₖ, 쿨 해제[n]) + 딜레이[n] )
            열림ₖ₊₁ = 누름ₖ + burst_switch_delay
            예측 = 누름₃ + 0.05                     (switching → 풀버스트 진입 딜레이)

        **게이지는 보지 않는다**(유저 결정). `fixed`에서는 게이지 제약이
        `풀버스트 종료 + burst_regen_time`이라 이것도 확정값이므로 사슬에 넣지만,
        `accumulate`에서는 실누적이라 확정값이 없어 뺀다 — 그쪽이 병목인 조합
        (충전 시간이 긴 덱, §버충 컨트롤)에서는 **예측이 이르게 나온다.** 하한이라는 뜻이다.

        §순환 위험 규칙 1을 만족한다 — 확정값만 보고 미래를 읽지 않는다.
        """
        # 이번 사이클이 끝나면 상한에 닿는가. 닿으면 다음 풀버스트는 없다.
        in_fb = self._phase == "full_burst"
        if (self._max_burst_count is not None
                and self._burst_count + (1 if in_fb else 0) >= self._max_burst_count):
            return -1.0

        # 기준 시각 — 풀버스트 중이면 그게 끝나기 전에는 다음 사이클이 시작될 수 없다.
        base = self._full_burst_end_t if in_fb else t
        if self._gauge_mode != "accumulate":
            # `fixed`의 게이지 제약은 전원의 `gauge_full_at`이다(`_gauge_ready()`).
            if in_fb:
                # 그 값은 아직 **지난** 사이클 것이다. 풀버스트가 끝나는 순간
                # `종료 + burst_regen_time`으로 다시 잡히므로 그걸 미리 센다.
                base += max(self.char_states[n].char.get("burst_regen_time", 2.0)
                            for n in self.squad_names)
            else:
                base = max(base, max(self.gauge_full_at.values()))

        cycle_idx = self._burst_count + (1 if in_fb else 0)
        # **이미 진행 중인 사이클은 남은 단계만 센다.** `stage:2`인데 1단계부터 다시 세면
        # 방금 쓴 사람이 쿨이라 **다음 사이클**을 예측해 버린다(한 사이클 통째로 어긋난다).
        first = 1
        if self._phase.startswith(("stage:", "reenter:")):
            first = int(self._phase.split(":")[1])
            base = max(t, self._next_action_t if self._next_action_t < math.inf else t)
        elif self._phase == "switching":
            return max(t, self._next_action_t) + 0.05

        at = base
        for stage in (str(i) for i in range(first, 4)):
            cands = self._predict_candidates(stage, cycle_idx)
            if not cands:
                return -1.0   # 그 단계를 쓸 사람이 없다 — 사이클이 영영 안 돈다
            at = min(max(at, self.burst_ready_at.get(n, 0.0))
                     + float(self._burst_delay.get(n, 0.0)) for n in cands)
            if stage != "3":
                at += self.config.get("burst_switch_delay", 0.1)
        return at + 0.05

    def _predict_candidates(self, stage: str, cycle_idx: int) -> list[str]:
        """예측용 단계 후보. `_try_use_stage()`가 쓰는 것과 같은 출처.

        패턴(`_pattern_rank`)은 보지 않는다 — 패턴은 후보를 **빼는 게 아니라 뒤로 미는**
        것이라, "이 단계가 언제 넘어갈 수 있나"의 답은 후보 전체의 최솟값 그대로다.
        """
        if (self._burst_sequence is not None
                and cycle_idx < len(self._burst_sequence)):
            return self._burst_sequence[cycle_idx].get(stage, [])
        return self.burst_order.get(stage, [])

    def _pattern_rank(self, name: str, cycle: int) -> int:
        """이번 사이클의 우선순위 등급. 낮을수록 먼저 쓴다 (`sorted`는 안정 정렬이라
        같은 등급끼리는 입력 순서가 유지된다).

          0 — 패턴이 있고 **이번 사이클이 그 차례**다. 패턴 없는 동료보다 앞선다
          1 — 패턴이 없다. 평소 순서
          2 — 패턴이 있지만 이번 사이클이 아니다. 맨 뒤 — 앞사람이 전부 쿨이면 그래도 나간다

        빈 목록(`[]`)은 "어느 사이클도 차례가 아니다" = 항상 등급 2다. 패턴 없음(`None`)과
        구분해야 하므로 falsy 검사를 쓰지 않는다.
        """
        pat = self._burst_pattern.get(name)
        if pat is None:
            return 1
        if isinstance(pat, str) and pat.startswith("every:"):
            n = int(pat.split(":", 1)[1])
            due = n > 0 and cycle % n == 0
        else:
            due = cycle in set(pat)
        return 0 if due else 2

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
            if self._burst_pattern:
                cycle = self._burst_count + 1   # 1-based — 유저가 세는 "N번째 버스트"
                candidates = sorted(candidates, key=lambda n: self._pattern_rank(n, cycle))
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
            if bm.is_stunned(name) or bm.is_down(name):
                continue
            # **딜레이 버스트** — 이 사람이 차례인데 유저가 아직 안 누른다.
            # 정본: docs/CONTROL.md §L0 · §딜레이 버스트.
            #
            # **뒷사람이 대신 나가지 않는다.** 조작자가 한 명이라 버튼을 늦게 누르면 그
            # 단계 전체가 밀린다 — 여기서 `continue` 하면 "미룬 게 아니라 건너뛴 것"이 되어
            # 조작이 표현되지 않는다.
            if (d := float(self._burst_delay.get(name, 0.0))) > 0:
                # **버튼에 불이 들어온 시각부터 잰다** = 단계가 열렸고 + 이 사람 쿨이 풀렸다.
                # 단계가 열린 시각만 기준으로 삼으면, 쿨이 그보다 늦게 풀리는 사이클에서
                # 딜레이가 조용히 무효가 된다 — 지정한 조작이 아무 일도 안 하는 자리다.
                press_at = max(self._stage_open_t, self.burst_ready_at.get(name, 0.0)) + d
                if t < press_at - 1e-9:
                    # **쿨 대기 목록은 세우지 않는다.** 세우면 tick()의 쿨감 반영 분기가
                    # `min()`으로 이 시각을 도로 앞당겨 딜레이가 사라진다.
                    self._next_action_t = press_at
                    return [], False, None
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
            buffs["is_element_match"] = cs.element_match(bm)

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
                    expected=(self.config.get("rng_mode") == "expected"),
                )
                if in_debug_window:
                    print()
                _rule = eff.get("target", "")
                events.append(HitEvent(
                    t=t, caster=name, damage=res["damage"],
                    is_crit=res["is_crit"], hit_tag="bonus_damage",
                    skill_name=eff.get("name", "버스트 스킬"),
                    rule=_rule if isinstance(_rule, str) else "",
                ))
        self._pending_burst_dmg.clear()
        return events

    def _gauge_ready(self, t: float, state: dict) -> bool:
        """1단계에 진입할 수 있는가. **두 모델이 갈리는 유일한 지점이다.**

        - "accumulate" — 실누적 게이지가 100%에 닿았는가.
          `first_burst_time`은 보지 않는다(하한도 두지 않는다 — 유저 결정).
          전투 시작 시점도 `idle`이라 0에서 그대로 차오른다.
        - "fixed"      — 종전대로 `gauge_full_at`(시각)에 닿았는가.
        """
        if self._gauge_mode == "accumulate":
            return state.get("burst_gauge", 0.0) >= 100.0 - 1e-9
        return all(t >= self.gauge_full_at[n] - 1e-9 for n in self.squad_names)

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
        """버스트 사용 후 재진입할 단계를 반환한다. 없으면 None.

        두 출처를 본다 — ① 이번 버스트가 낸 `burst_reentry` instant(`handle_burst_reentry`가
        적어 둔 1회분, 여기서 꺼내 지운다) ② 활성 `burst_stage_override:reenterN` 상태 버프.
        """
        pending = bm.state.get("pending_reentry")
        if pending and name in pending:
            return pending.pop(name)
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
        state.setdefault("burst_cycle_users", {}).setdefault(stage, set()).add(name)

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
        # 「아군이 버스트 스킬 사용 시」는 스쿼드 전체에 브로드캐스트한다 — `event:[버프명]`과
        # 같은 규약으로, 반응하는 캐릭터 본인을 caster로 넘겨 조건·대상을 자기 기준으로
        # 평가하게 한다(GAMEPLAY.md §트리거 발동 의미). 시전자 자신도 「아군」에 포함된다
        # (`all_allies`가 시전자 포함인 것과 같은 읽기). 루피 : 윈터 쇼퍼 `쇼핑`
        for _sq in bm.squad_names:
            bm.notify("event:ally_burst_cast", t, _sq)

        is_reenter = self._phase.startswith("reenter:")
        event_label = f"reenter:{stage} 사용" if is_reenter else f"stage:{stage} 사용"
        if self._log is not None:
            self._log.burst_log.append(BurstLogEntry(t=t, event=event_label, caster=name))

        # 3단계 버스트 발동자를 기록 (fullburst_duration 귀속용)
        if stage == "3":
            self._fb_caster = name
            self._fb_caster_t = t

        # 스킬3의 instant/damage 타입은 모두 위 bm.notify("burst_cast") 경로에서 처리된다

        return events


def _later_burst_cast_buffs(bm: BuffManager, caster: str, eff: dict) -> frozenset[str]:
    """`eff`보다 **뒤에** 서술된 같은 `burst_cast` 트리거 buff들의 이름.

    parsed_skills.json의 배열 순서는 원문 `■` 블록 순서를 그대로 보존한다
    (GAMEPLAY.md §효과 실행 순서). 딜 블록보다 뒤에 적힌 버프는 그 딜에 실리지 않으므로,
    계산이 풀버스트로 밀리는 보류 딜에서 제외할 이름 집합을 만든다.

    목록은 `bm.char_effects()`에서 받는다 — 애장품 캐릭터는 원본에 안 쓰는 판본이
    섞여 있어 서술 순서가 실제 실행 순서와 어긋나기 때문이다.
    """
    effs = bm.char_effects(caster)
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
        """target 필드를 캐릭터명 목록으로 변환 (아군 only).

        해석은 `bm._resolve_target()`에 위임한다 — 예전에는 여기서 `self`·`all_allies`만
        처리하고 나머지를 전부 시전자로 폴백해, `allies_lowest_hp:2` 같은 대상이 붙은
        회복이 조용히 시전자 자신에게만 들어갔다 (트리나 `네이처 그레이스 2·3`).
        instant는 지속시간이 없어 지연 resolve가 의미 없으므로 발동 시점 상태로 즉시 판정한다.
        적 대상 센티널·스쿼드 밖 이름은 걸러 아군만 남긴다.
        """
        target = eff.get("target", "self")
        names = bm._resolve_target(target, caster)
        allies = [n for n in names if n in char_states]
        # 매칭 아군이 없으면 무발동 — 시전자로 폴백하지 않는다.
        return allies

    def _effective_max_ammo(cs: "CharState", t: float) -> int:
        # 재장전이 채우는 최대치와 같은 값이어야 한다 — 탄환 충전의 기준·상한도 이것이다.
        # (무기 변경 모드 장탄 상한 처리도 _full_ammo가 함께 맡는다)
        return cs._full_ammo(bm, t)

    def _cancel_reload_if_full(cs: "CharState", t: float, max_ammo: int):
        # 탄충 취소 컨트롤 — 재장전 중에 탄창이 꽉 차면 재장전을 끊고 바로 쏜다.
        # 켠 캐릭터에게만 걸린다. 정본: docs/CONTROL.md §탄충 취소
        if (cs.reload_cancel_on_full and cs.reloading_until > 0
                and cs.ammo >= max_ammo):
            cs._cancel_reload(t, bm)

    def handle_ammo_charge_pct(eff, caster, t, val):
        target_names = _resolve_targets(eff, caster)
        for name in target_names:
            cs = char_states.get(name)
            if cs is None:
                continue
            max_ammo = _effective_max_ammo(cs, t)
            charge = round(max_ammo * (val / 100.0))
            # 음수(`탄환 100% 제거`)면 0 아래로 내려갈 수 있다 — 탄창의 **현재** 탄이 아니라
            # 최대 장탄의 비율을 빼기 때문이다. 반쯤 남은 탄창에서 -100%를 맞으면 음수가 되고,
            # 그 뒤 재장전은 0까지 기어 올라오는 데만 여러 번을 쓴다. 탄창은 0 미만이 없다.
            cs.ammo = max(0, min(cs.ammo + charge, max_ammo))
            if cs._sim_log is not None:
                cs._sim_log.ammo_log.append(AmmoLogEntry(t=t, caster=name, ammo=cs.ammo))
            _cancel_reload_if_full(cs, t, max_ammo)
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
            _cancel_reload_if_full(cs, t, max_ammo)

    def handle_burst_charge_pct(eff, caster, t, val):
        # 「버스트 게이지 충전 N%」. 스킬 텍스트 값을 **그대로** 가산한다 —
        # 히트당 값이 아니라 이미 게이지 %라서 히트 수를 곱하지 않는다.
        #
        # **target: all_allies여도 1회만 더한다.** 게이지가 스쿼드 공용 1개이기 때문이다.
        # 헬름 `진두지휘 3` 14.31이 아레나 코드에서도 풀차지 샷당 1회 가산인 것이 근거다.
        # 대상에 스쿼드원이 하나도 없으면 아무 일도 일어나지 않는다.
        if not _resolve_targets(eff, caster):
            return
        # 버스트 충전 속도는 히트당 시전자 기준 가산이라, 히트가 없는 이 1회 가산에는
        # 붙이지 않는다.
        bm.add_burst_gauge(val, t, caster, f"charge_pct:{eff.get('name', '')}")

    def handle_burst_cooldown_reduce(eff, caster, t, val):
        target_names = _resolve_targets(eff, caster)
        for name in target_names:
            burst_ctrl.burst_ready_at[name] = max(t, burst_ctrl.burst_ready_at.get(name, 0.0) - val)

    def handle_heal_hp_pct(eff, caster, t, val):
        # `scaling: max_hp`는 원문 「**시전자의** 최종 최대 체력 비례 N% 회복」이다 — 받는 사람의
        # 최대 체력이 아니다(docs/PARSING.md `heal_hp_pct`). 아군 전체 힐도 모두 같은 양을 받는다.
        target_names = _resolve_targets(eff, caster)
        hp = bm.state["hp"]
        caster_based = eff.get("scaling") == "max_hp"
        for name in target_names:
            base_hp = bm.state["base_stats"].get(name, {}).get("hp", 0.0)
            max_hp = bm.effective_max_hp(name)
            heal_base = bm.effective_max_hp(caster) if caster_based else base_hp
            heal = heal_base * val / 100.0 * bm.heal_received_mult(name, t)
            hp[name] = min(hp.get(name, base_hp) + heal, max_hp)
            bm.sync_hp(name)
            # `heal_source`는 이 회복을 **건** 쪽이다 — 받는 쪽(`name`)과 구분해야
            # 「자신이 사용한 회복 효과가 아니라면」(`not_self_caused_heal`)이 성립한다.
            bm.notify("event:heal_received", t, name, heal_source=caster)

    def handle_current_hp_reduce(eff, caster, t, val):
        # `[현재 체력 N% ▼]`은 *현재* 체력의 N%다 — 최대 체력 기준 정액이 아니다.
        # 곱연산이라 체력은 0에 수렴할 뿐 0이 되지 않는다 (GAMEPLAY.md §값 산정).
        target_names = _resolve_targets(eff, caster)
        hp = bm.state["hp"]
        for name in target_names:
            base_hp = bm.state["base_stats"].get(name, {}).get("hp", 0.0)
            cur = hp.get(name, base_hp)
            hp[name] = max(cur * (1.0 - val / 100.0), 0.0)
            bm.sync_hp(name)

    def handle_cover_heal_pct(eff, caster, t, val):
        # 엄폐물 최대 체력의 N% 회복. **부서진 엄폐물은 되살아나지 않는다**(유저 확인 — 재생성 없음).
        # `scaling: "max_hp"`면 기준이 **시전자의 최종 최대 체력**이다 — 원문 「시전자의 최종 최대 체력
        # 비례 엄폐물 체력 회복」(슈가 `블랙 타이푼 3`). 기준 표기가 없는 문형(나가·츠바이·리타)은 엄폐물 기준.
        #
        # 회복받은 엄폐물의 주인에게 `event:cover_healed`를 보낸다 — **가득 차 있어도 보낸다**
        # (유저 확인 2026-09-14, `event:heal_received` 오버힐 규칙의 엄폐물판. GAMEPLAY §트리거
        # 발동 의미). 부서진 엄폐물은 회복되지 않으므로 보내지 않는다. 티아 `파충류 애호가`
        cur, mx = bm.state["cover_hp"], bm.state["cover_max_hp"]
        caster_based = eff.get("scaling") == "max_hp"
        for name in _resolve_targets(eff, caster):
            if cur.get(name, 0.0) > 0.0 and val:
                base = bm.effective_max_hp(caster) if caster_based else mx[name]
                cur[name] = min(mx[name], cur[name] + base * val / 100.0)
                bm.notify("event:cover_healed", t, name)

    def handle_shield_heal_pct(eff, caster, t, val):
        # `[보호막 체력 회복 N%]` — **이미 있는 보호막이 깎인 만큼 되돌린다.**
        # `cover_heal_pct`의 보호막판이고 기준 규약도 같다: `scaling: "max_hp"`면
        # **시전자의 최종 최대 체력** N%, 표기가 없으면 그 대상의 보호막 최대치 N%다
        # (지금 로스터의 보유자 셋은 전부 전자다 — `PARSING.md` §7-10).
        #
        # 없는 보호막을 새로 만들지 않는다 — 생성은 `shield_from_max_hp_pct`,
        # 생성량 증폭은 `next_shield_hp_pct`, 회복은 이쪽으로 축이 셋이다.
        # 보호막은 보스 공격 패턴이 있을 때만 깎이므로 **기본 경로에서는 늘 만피 = 회복량 0**이다
        # (`cover_heal_pct`와 같은 자리). 회복 이벤트는 쏘지 않는다 — 「보호막 체력 회복 시」를
        # 트리거로 쓰는 효과가 로스터에 없다(⬜ 생기면 `event:cover_healed`의 오버힐 규약을 따른다).
        # 킬로 `자가 수복` · 라푼젤 : 퓨어 그레이스 `프레이 3`
        if not val:
            return
        caster_based = eff.get("scaling") == "max_hp"
        for name in _resolve_targets(eff, caster):
            base = bm.effective_max_hp(caster) if caster_based else bm.shield_capacity(name)
            bm.heal_shield(name, base * val / 100.0, t)

    def handle_decoy_heal_pct(eff, caster, t, val):
        # `[시전자의 최종 최대 체력 비례 디코이 회복 N%]` — **이미 있는 분신이 깎인 만큼 되돌린다.**
        # `shield_heal_pct`(보호막)·`cover_heal_pct`(엄폐물)와 같은 층이고 대상만 분신이다:
        # `scaling: "max_hp"`면 **시전자의 최종 최대 체력** N%, 표기가 없으면 그 대상의 분신
        # 최대치 N%다(지금 보유자 라이는 둘 다 전자 — `PARSING.md` §7-10).
        #
        # 없는 분신을 새로 만들지 않는다 — 생성은 `decoy`다. 분신은 보스 공격 패턴이 있을 때만
        # 깎이므로 **기본 경로에서는 늘 만피 = 회복량 0**이다. 주기판(`[N초 간격]`)도 같은 핸들러가
        # 받는다 — `tick_interval`이 붙은 instant는 타이머가 같은 자리를 반복 호출한다.
        # 라이 `선배의 응원 2`(60발마다) · `선배의 모범 2`(버스트, 1초 간격 10초)
        if not val:
            return
        caster_based = eff.get("scaling") == "max_hp"
        for name in _resolve_targets(eff, caster):
            base = bm.effective_max_hp(caster) if caster_based else bm.decoy_capacity(name)
            bm.heal_decoy(name, base * val / 100.0, t)

    def handle_cover_revive(eff, caster, t, val):
        # `[엄폐물 체력 N%로 엄폐물 부활]` — **부서진 엄폐물 전용**이다.
        # `cover_heal_pct`(살아 있는 엄폐물만 회복)와 정확히 배타이고, 그쪽의
        # 「부서진 엄폐물은 되살아나지 않는다」 규약을 여는 유일한 경로다.
        #
        # 기준은 그 **대상의** 엄폐물 최대 체력이다 — 원문에 「시전자의 …」 수식이 없다
        # (있으면 붙는다. 같은 캐릭터 비스킷 스킬1이 그 예다). `cover_heal_pct`의
        # 기준 표기 규약과 같다.
        #
        # `event:cover_healed`는 보내지 않는다 — 원문이 「회복」이 아니라 「부활」이고,
        # 그 트리거의 정본 서술이 「부서진 엄폐물은 회복되지 않으므로 무발동」이다
        # (GAMEPLAY §트리거 발동 의미). ⬜ 인게임 미확인 — 부활을 회복으로 치는지는
        # 확인되지 않았고, 유일한 소비자는 티아 `파충류 애호가`다.
        #
        # 엄폐물은 보스 공격 패턴이 있을 때만 부서지므로 기본 경로에서는 대상이 0기다.
        # 비스킷 `산책 훈련`(`allies_broken_cover_random:2`) ·
        # 베이 `퍼스트 위너`(애장품 3단계, `self` + `not_self_cover_alive` + `max_trigger:1`)
        if not val:
            raise ValueError(f"{caster} `{eff.get('name')}`: cover_revive에 체력 % 수치가 없다")
        cur, mx = bm.state["cover_hp"], bm.state["cover_max_hp"]
        for name in _resolve_targets(eff, caster):
            if cur.get(name, 0.0) > 0.0:
                continue        # 멀쩡한 엄폐물은 대상이 아니다
            bm.revive_cover(name, mx.get(name, 0.0) * val / 100.0)

    def handle_burst_reentry(eff, caster, t, val):
        # `[버스트 재진입 N단계]` — `fixed_value`가 단계 N. 이번 버스트 1회의 사건이라 buff로
        # 남기지 않고 시전자 앞으로 적어 두면, `_cast_burst` 직후 `_check_reenter`가 꺼내 간다
        # (상태 buff `burst_stage_override:reenterN`과 같은 자리). 대상 표기(아군 전체)는
        # 게이지가 스쿼드 공용이라는 서술일 뿐 재진입은 한 번이다. 티아 · 앨리스 : 원더랜드 바니
        if not val:
            raise ValueError(f"{caster} `{eff.get('name')}`: burst_reentry에 단계(fixed_value)가 없다")
        bm.state.setdefault("pending_reentry", {})[caster] = str(int(val))

    def handle_revive(eff, caster, t, val):
        # `[체력 N%로 부활]` — values가 부활 직후 체력 %다. 값 없는 revive는 데이터 누락이다.
        if not val:
            raise ValueError(f"{caster} `{eff.get('name')}`: revive에 체력 % 수치가 없다")
        for name in _resolve_targets(eff, caster):
            if not bm.is_down(name):
                continue
            bm.revive(name, t, val)
            char_states[name].on_revive(t, bm)
            if bm.state.get("_on_revive") is not None:
                bm.state["_on_revive"](t, name, caster)

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
    bm.register_instant_handler("burst_charge_pct", handle_burst_charge_pct)
    bm.register_instant_handler("burst_cooldown_reduce", handle_burst_cooldown_reduce)
    bm.register_instant_handler("heal_hp_pct", handle_heal_hp_pct)
    bm.register_instant_handler("current_hp_reduce", handle_current_hp_reduce)
    bm.register_instant_handler("force_reload", handle_force_reload)
    bm.register_instant_handler("cover_heal_pct", handle_cover_heal_pct)
    bm.register_instant_handler("shield_heal_pct", handle_shield_heal_pct)
    bm.register_instant_handler("decoy_heal_pct", handle_decoy_heal_pct)
    bm.register_instant_handler("cover_revive", handle_cover_revive)
    bm.register_instant_handler("burst_reentry", handle_burst_reentry)
    bm.register_instant_handler("revive", handle_revive)


# ── simulate ──────────────────────────────────────────────────────────────

def _check_names(names: list[str], allow_unparsed: bool) -> None:
    """스쿼드 이름을 정본 JSON 두 곳과 대조한다.

    스크랩만 되고 아직 파싱하지 않은 캐릭터(출시 직후 신캐)는 `parsed_nikke.json`에는 있고
    `parsed_skills.json`에는 없다. 효과 조회가 `.get(name, [])`이라 그대로 두면
    스탯·무기만 정상이고 스킬이 0개인 니케로 조용히 돌아가 — 에러 없이 그럴듯한
    오답이 나온다. 여기서 끊는다 (docs/ALIASES.md).
    """
    unknown = [n for n in names if n not in _NIKKE]
    if unknown:
        raise ValueError(
            f"parsed_nikke.json에 없는 캐릭터: {unknown}\n"
            f"  정식 명칭을 써야 한다. 별칭 표: docs/ALIASES.md"
        )
    if allow_unparsed:
        return
    unparsed = [n for n in names if n not in _PARSED_SKILLS]
    if unparsed:
        raise ValueError(
            f"스킬이 파싱되지 않은 캐릭터: {unparsed}\n"
            f"  이대로 돌리면 스킬 0개로 계산되어 결과가 조용히 틀린다.\n"
            f"  ① 별칭을 쓴 것은 아닌지 확인 — `마스트` → `마스트 : 로망틱 메이드` (docs/ALIASES.md)\n"
            f"  ② 파싱 전 신규 캐릭터를 의도적으로 돌리는 것이라면 "
            f"config['allow_unparsed']=True (CLI: --allow-unparsed)"
        )


def _burst_charge_carriers(squad: list[dict]) -> list[str]:
    """버충 컨트롤(충전 창 한정 톡톡이)을 켠 캐릭터 목록. 정본: docs/CONTROL.md §버충 컨트롤."""
    def _on(c: dict) -> bool:
        ctrl = c.get("control") or {}
        if ((ctrl.get("tap_fire") or {}).get("window")) == "burst_charge":
            return True   # 종전 키
        return any(e.get("mode") == "tap" and e.get("window") == "burst_charge"
                   for e in (ctrl.get("click") or []))

    return [c["name"] for c in squad if _on(c)]


def _resolve_cameras(squad: list[dict], cfg: dict) -> frozenset[str]:
    """카메라를 받은 니케 집합. 풀차지 게이지 배율이 붙는 대상이다.

    **버충 담당이 있으면 그 사람 하나로 끝난다 — `camera_mode`를 보지 않는다.**
    충전 창은 2~5초뿐이고 그 안에서 한 명을 계속 클릭하는 조작이라 나눠 가질 수 없다.
    카메라가 그 사람에게 묶이는 건 **버충 조작의 비용**이기도 하다 — 톡톡이는 논차지라
    배율을 못 받으므로, 그 창에서 아무도 풀차지 배율을 못 받는다. 이걸 다른 니케에게
    흘리면 있지도 않은 이득이 생긴다. 두 명 이상이면 즉시 실패한다(조용히 틀리지 않는다).

    버충 담당이 없을 때만 `camera_mode`가 갈린다:

    - `"single"`(기본) — 정확히 1명. 실제 게임의 제약이다.
      `config["camera"]`가 명시되면 그것이 이긴다. 빈 문자열은 **아무도 보지 않는다**는
      뜻이다(스쿼드에 없는 이름도 같다) — 유도로 떨어지지 않는다. 미지정(None)이면
      컨트롤을 켠 캐릭터가 **정확히 1명**일 때 그 사람 (좌클릭·엄폐는 보고 있는 니케에만
      걸리므로 컨트롤을 준다는 게 곧 카메라를 거기 둔다는 뜻이다 — 유저 확인).
      그 외(0명·2명 이상)는 **3번 자리** — 전투가 시작되면 카메라는 거기서 출발하고
      유저가 z·x·c·v·b로 1~5번을 오간다 (유저 확인).
    - `"shared"` — 컨트롤을 켠 **전원**이 받는다(없으면 3번 자리). 컨트롤 정책은 이미
      "여러 명 동시 조작"을 비현실적 상한으로 허용하는데(docs/CONTROL.md) 카메라만
      1명으로 남으면 조작과 카메라가 따로 논다. 상한을 쓰기로 했으면 카메라도 같이
      올린다 — **상한이지 실전값이 아니다.**

    효과는 `_charge_fire()`의 풀차지 배율 한 줄뿐이다 — 대미지·컨트롤 경로는
    이 값을 보지 않는다. 비차지 무기는 `full_charge_mult`가 없어 무영향이다.
    """
    # 모드 검증은 버충 분기보다 **먼저** 한다 — 오타를 버충 담당 유무에 따라
    # 잡았다 놓쳤다 하면 그게 더 나쁘다.
    mode = cfg.get("camera_mode", "single")
    if mode not in ("single", "shared"):
        raise ValueError(
            f'camera_mode는 "single" 또는 "shared"여야 한다: {mode!r}. docs/CONTROL.md §카메라')

    carriers = _burst_charge_carriers(squad)
    if len(carriers) > 1:
        raise ValueError(
            f"버충 컨트롤은 한 명만 켤 수 있다 (카메라를 나눠 가질 수 없다): {carriers}. "
            f"docs/CONTROL.md §버충 컨트롤")
    if carriers:
        return frozenset(carriers)

    named = cfg.get("camera")
    if named is not None:
        names = [named] if isinstance(named, str) else list(named)
        names = [n for n in names if n]
        if mode == "single" and len(names) > 1:
            raise ValueError(
                f'camera_mode="single"에는 카메라를 한 명만 줄 수 있다: {names}. '
                f'여러 명을 보려면 camera_mode="shared". docs/CONTROL.md §카메라')
        return frozenset(names)

    controlled = [c["name"] for c in squad if c.get("control")]
    if mode == "shared" and controlled:
        return frozenset(controlled)
    # 컨트롤 1명 유도는 **그 사람이 차지 무기일 때만** 한다. 카메라의 효과는 풀차지 게이지
    # 배율 한 줄뿐이라(`_charge_fire`), 비차지 무기에게 주면 카메라가 통째로 죽는다 —
    # `S39_나가라피`에서 장전컨을 가진 라피 : 레드 후드(MG)에게 가서 카메라 "없음"과
    # 결과가 완전히 같았다. 그 자리의 3번은 아니스 : 스타(RL)였고, 유저도 충전 창에는
    # 그쪽을 본다. 컨트롤을 준다는 게 곧 카메라라는 규칙은 유지하되, 카메라가 의미를
    # 갖는 대상일 때만 적용한다.
    if len(controlled) == 1 and _is_charge_nikke(controlled[0]):
        return frozenset(controlled)
    if len(squad) >= 3:
        return frozenset({squad[2]["name"]})
    return frozenset({squad[0]["name"]}) if squad else frozenset()


def _pump_squad_seq(t: float, bm: BuffManager, squad: list[dict],
                    char_states: dict[str, "CharState"]) -> None:
    """스쿼드 시퀀스 — 카메라 이동과 전체 엄폐를 시각으로 찍는다.
    정본: docs/CONTROL.md §스쿼드 시퀀스.

    조율보다 **먼저** 돈다: `focus`는 그 틱의 조작자를 유저가 못박는 것이라 조율이 그 값을
    보고 결정해야 한다. `cover_all`(space)은 **보고 있는 1명만 빼고** 전원을 엄폐시킨다 —
    space를 누른 채로도 그 한 명은 클릭으로 계속 사격·차징하기 때문이다.
    """
    state = bm.state
    seq, i = state["_squad_seq"], state["_squad_seq_i"]
    while i < len(seq) and t >= float(seq[i].get("t", 0.0)):
        act = seq[i]
        i += 1
        kind = act.get("action")
        if kind == "focus":
            state["ctrl_focus_forced"] = str(act.get("target") or "")
        elif kind == "cover_all":
            keep = state.get("ctrl_focus_forced") or state.get("ctrl_owner") or ""
            for char in squad:
                cs = char_states[char["name"]]
                if cs.name == keep:
                    continue
                # 무기 변경 모드는 건너뛴다 — 엄폐 정책과 같은 가드다(모드 탄창 로직을
                # 흔든다). 게다가 그 모드는 tick 순서상 엄폐 검사보다 먼저 처리되어
                # **엄폐시켜 놓아도 계속 쏜다** — 걸어 두면 로그만 남고 조작은 없다.
                if cs._in_weapon_change or bm.get_weapon_change(cs.name) is not None:
                    continue
                if cs.cover_blocked(t, bm):
                    continue
                cs._enter_cover(t, bm, act.get("duration"), "엄폐(전체 엄폐)",
                                ctrl_input="cover_all")
    state["_squad_seq_i"] = i


def _arbitrate_control(t: float, bm: BuffManager, squad: list[dict],
                       char_states: dict[str, "CharState"],
                       static_camera: frozenset) -> None:
    """이번 틱의 조작자(=카메라)를 정한다. 정본: docs/CONTROL.md §조작자는 한 명.

    **char tick 이전에** 돌아야 한다 — 캐릭터 tick 안에서 정하면 스쿼드 자리 순서가 답을
    바꾼다(§순환 위험 규칙 2). 정책에는 부작용 없이 묻고(`_wants_control()`), 승자만 실제로
    조작한다(`_owns()`).

    **등급이 먼저, 그다음이 후입 우선.** 승자는 "이게 더 급해서" 정해진다 — 놓치면 사이클이
    밀리는 조작(상)이 버프가 새는 조작(중)을 이기고, 그게 언제든 재개 가능한 조작(하)을
    이긴다. 같은 등급 안에서만 **나중에 들어온 요청**이 가져간다. 등급 없이 후입만 보면 전투
    내내 클릭을 잡는 상시 톡톡이가 "이 시각에 꼭 해야 하는" 조작을 밀어낸다.
    뺏긴 쪽은 조작이 풀리고(`_release_control()`), 카메라가 비면 다시 요청해 복귀한다.

    **전환에는 비용이 없다** (유저 확인 2026-08-29 — 광클해도 불이익이 없다). 그래서 최소 점유
    시간을 두지 않는다. 채터링은 두 겹으로 막힌다 — 같은 등급에서는 **에지 판정**이(계속
    원하는 것은 새 요청이 아니므로 뺏은 쪽이 놓기 전까지 도로 뺏기지 않는다), 등급이 다를
    때는 **선점이 한 방향뿐**이라(하가 상을 도로 못 뺏는다) 진동하지 않는다.
    """
    state = bm.state
    mode = state["ctrl_mode"]
    wants: list[tuple[int, "CharState", str, int, bool]] = []
    for i, char in enumerate(squad):
        cs = char_states[char["name"]]
        req = cs._wants_control(t, bm)
        edge = req is not None and not cs._ctrl_want_prev   # 새 요청인가 (후입 판정)
        cs._ctrl_want_prev = req is not None
        if req is not None:
            wants.append((i, cs, req[0], req[1], edge))

    if mode != "solo":
        # 전원을 동시에 조작하는 상한 모드. 카메라도 정적 유도값 그대로다.
        if mode == "strict" and len(wants) > 1:
            raise ValueError(
                f"t={t:.3f}s: 같은 시각에 여러 니케를 조작할 수 없다 — "
                + " · ".join(f"{c.name}({k})" for _, c, k, _, _ in wants)
                + '. control_mode="warn"은 상한으로 허용하고 "solo"는 직렬화한다. '
                  "docs/CONTROL.md §조작자는 한 명")
        state["camera"] = static_camera
        return

    forced = state.get("ctrl_focus_forced") or ""
    if forced:
        # 유저가 카메라를 못박았다 — 조율보다 우선한다(명시 시퀀스가 정책보다 우선하는
        # 현행 규칙과 같다). 보고 있지 않게 된 니케는 조작이 풀린다.
        prev = state["ctrl_owner"]
        if prev and prev != forced:
            char_states[prev]._release_control(t, bm)
        if prev != forced:
            state["ctrl_owner_since"] = t
        state["ctrl_owner"] = forced
        state["camera"] = frozenset({forced})
        return

    def _rank(w: tuple) -> tuple:
        """정렬 키: **등급 > 에지(후입) > 스쿼드 자리**. 마지막 항이 동점을 결정론으로 만든다."""
        return (-w[3], not w[4], w[0])

    owner = state["ctrl_owner"]
    cur = next((w for w in wants if w[1].name == owner), None)
    if cur is None:
        # 동적 창 홀드는 미래의 절대 떼기 시각이 없다. 창이 닫혀 요청이 사라진 순간 직접
        # 풀지 않으면 이전 owner의 `math.inf`가 남아 영원히 발사하지 못한다.
        if owner and char_states[owner]._hold_until_close_entry is not None:
            char_states[owner]._release_control(t, bm)
        owner = ""      # 더 이상 원하지 않는다 → 놓는다
    if not owner:
        if wants:
            # 카메라가 비었다 — 가장 급한 요청에게 준다(복귀 포함).
            owner, state["ctrl_owner_since"] = sorted(wants, key=_rank)[0][1].name, t
    else:
        # 도전자는 **등급이 더 높거나, 같은 등급의 새 요청**이다. 낮은 등급은 새 요청이어도
        # 뺏지 못한다 — 상시 톡톡이가 엄폐컨을 밀어내는 것이 정확히 그 경우였다.
        chal = [w for w in wants if w[1].name != owner
                and (w[3] > cur[3] or (w[3] == cur[3] and w[4]))]
        if chal:
            pick = sorted(chal, key=_rank)[0]
            char_states[owner]._release_control(t, bm)
            state["ctrl_preempt"][owner] = state["ctrl_preempt"].get(owner, 0) + 1
            owner, state["ctrl_owner_since"] = pick[1].name, t
    state["ctrl_owner"] = owner
    # 카메라는 조작 주인을 따라간다 — 조작이 없으면 정적 유도값으로 돌아간다
    state["camera"] = frozenset({owner}) if owner else static_camera


def _check_squad_seq(seq: list, squad: list[dict]) -> list[dict]:
    """스쿼드 시퀀스를 시각순으로 정렬하고 조립 시점에 검증한다.

    조용히 무시되는 입력을 만들지 않는다 — 이름을 틀리면 카메라가 아무 데도 안 가고,
    그 결과는 "카메라 없음"과 구별되지 않는다.
    """
    names = {c["name"] for c in squad}
    out = sorted(seq, key=lambda a: float(a.get("t", 0.0)))
    for act in out:
        kind = act.get("action")
        if kind not in ("focus", "cover_all"):
            raise ValueError(
                f"모르는 스쿼드 시퀀스 액션: {kind!r}. \"focus\" 또는 \"cover_all\"여야 한다. "
                f"docs/CONTROL.md §스쿼드 시퀀스")
        if kind == "focus":
            tgt = act.get("target") or ""
            if tgt and tgt not in names:
                raise ValueError(
                    f"focus 대상이 스쿼드에 없다: {tgt!r} (스쿼드 {sorted(names)}). "
                    f"docs/CONTROL.md §스쿼드 시퀀스")
    return out


def _is_charge_nikke(name: str) -> bool:
    """풀차지 게이지 배율을 받을 수 있는 니케인가 (SR·RL). 카메라 유도 판정용."""
    return _pick("full_charge_mult",
                 _DELAYS["_exceptions"].get(name), _NIKKE.get(name)) is not None


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
             결과가 완전히 결정론적이 된다. 회귀 하네스(runner/snapshot.py)와
             CLI(runner/sim.py)가 사용한다.

    난수를 아예 없애고 싶으면 `config={"rng_mode": "expected"}`를 쓴다 —
    크리·코어히트를 확률 판정 대신 기대값으로 태워 1회 실행으로 기대딜이 나온다.
    (시뮬의 난수원은 이 둘뿐이라 시드 없이도 결과가 완전히 결정론적이다.
     대신 히트별 크리/코어 구분이 사라진다 — docs/CALCULATOR.md §기대값 모드)
    """
    if seed is not None:
        random.seed(seed)

    if config and "part_break_interval" in config:
        # 2026-09-19에 적으로 옮겼다 — 옛 자리에 적으면 조용히 무발동이 되므로 막는다
        raise ValueError("part_break_interval은 config가 아니라 enemy의 칸이다(간단 모드 보스의 파츠 파괴 주기) — "
                         "enemy={\"part_break_interval\": 초}로 준다")
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    enm = {**DEFAULT_ENEMY, **(enemy or {})}
    duration = cfg["duration"]
    pbi = enm["part_break_interval"]
    if isinstance(pbi, bool) or not isinstance(pbi, (int, float)) or pbi < 0:
        raise ValueError(f"enemy.part_break_interval은 0 이상의 수(초)여야 한다: {pbi!r}")
    # 보스 거리는 무기군 목록을 **대신한다** — 둘을 같이 적으면 어느 쪽이 이기는지가 조용한 결과 차이가 된다
    dist = enm.get("distance")
    if dist is not None:
        if isinstance(dist, bool) or not isinstance(dist, (int, float)) or not dist > 0:
            raise ValueError(f"enemy.distance는 0보다 큰 수여야 한다: {dist!r}")
        if enm.get("optimal_range_weapons"):
            raise ValueError("enemy에 distance와 optimal_range_weapons를 같이 적을 수 없다 — 거리가 있으면 "
                             "니케마다 적정 구간(CDN bonusrange)과 비교하므로 무기군 목록이 뜻이 없다")
    # 보스 패턴은 무거운 초기화보다 먼저 검사한다 — 잘못 적은 스크립트는 즉시 실패시킨다.
    # 간단 모드(패턴 없음)는 보스를 만들지 않는다. 좌표(`enemy["coord"]`)는 패턴 모드의 스위치다(`boss_mode`)
    enemy_mode = boss_mode(enm)
    boss_patterns = (validate_boss_patterns(enm["patterns"], weapon_types=_WEAPON_TYPES,
                                            squad_size=len(squad), coord=enemy_mode == COORD)
                     if enemy_mode != SIMPLE else None)

    if cfg["rng_mode"] not in ("random", "expected"):
        raise ValueError(f'rng_mode는 "random" 또는 "expected"여야 한다: {cfg["rng_mode"]!r}')

    squad = [{**DEFAULT_CHAR, **c} for c in squad]
    _check_names([c["name"] for c in squad], bool(cfg["allow_unparsed"]))

    if cfg["burst_gauge_mode"] not in ("fixed", "accumulate"):
        raise ValueError(
            f'burst_gauge_mode는 "fixed" 또는 "accumulate"여야 한다: {cfg["burst_gauge_mode"]!r}')
    # 풀차지 게이지 배율이 붙는 한 명. `_charge_fire()`가 cfg에서 읽는다.
    if cfg["control_mode"] not in _CTRL_MODES:
        raise ValueError(
            f"control_mode는 {' · '.join(_CTRL_MODES)} 중 하나여야 한다: "
            f"{cfg['control_mode']!r}. docs/CONTROL.md §조작자는 한 명")
    cfg["_camera"] = _resolve_cameras(squad, cfg)

    base_stats: dict[str, dict] = {c["name"]: calc_base_stats(c) for c in squad}

    state: dict = {
        "full_burst":   False,
        # 장전컨(docs/CONTROL.md)용 풀버스트 사이클 정보. BurstController가 갱신
        "full_burst_end_t":   -1.0,  # 현재 풀버스트 종료 시각 (진입 시 확정)
        "next_fb_start_pred": -1.0,  # 다음 풀버스트 시작 예측 (직전 사이클 주기 기준)
        "burst_casted": {c["name"]: False for c in squad},
        # 현재 사이클의 단계별 버스트 사용자. 풀버스트 종료 뒤 충전 창까지 유지하고 다음
        # 1단계 진입 때 비운다 — 런타임 gate가 종료 전 장전과 종료 후 클릭을 한 사이클로 묶는다.
        "burst_cycle_users": {"1": set(), "2": set(), "3": set()},
        # 버스트 게이지 — **스쿼드 공용 1개**다. 만충 100, 초과분은 버려진다.
        # 가산은 BuffManager.add_burst_gauge() 한 곳으로만 들어온다.
        "burst_gauge":  0.0,
        # 일반 공격을 1회라도 명중시킨 니케들. 이 니케가 **아군에게** 건 버충속은
        # CDN `(발당)` 대신 `(대상)` 게이지를 참조한다. 게이지 초기화와 무관하게 유지된다.
        "normal_attack_landed": set(),
        # 지금이 충전 창인가. BurstController.tick()이 매 프레임 `_phase == "idle"`로 갱신한다.
        # 전투 시작 시점은 idle이므로 True에서 출발한다.
        "burst_gauge_charging": True,
        # 보스가 사라졌는가(`vanish` 패턴). 보스 스케줄러가 프레임 맨 앞에서 갱신한다 —
        # 무기 사격이 게이지를 채울지를 `CharState._weapon_gauge_lands()`가 이것으로 판정한다.
        "boss_vanish":  False,
        # 속성보호막이 이 캐스터의 딜을 막는가 — `BossScript.shield_blocks`. 보스 패턴이 없으면 None.
        # 막힌 스킬 대미지 몫의 게이지를 거르는 두 자리(`_weapon_gauge_lands`·스킬 대미지 핸들러)가 본다.
        "boss_shield_blocks": None,
        # 조작자(카메라)는 한 명 — `_arbitrate_control()`이 매 프레임 갱신한다.
        # 정본: docs/CONTROL.md §조작자는 한 명.
        "ctrl_mode":    cfg["control_mode"],
        "ctrl_owner":   "",     # 지금 조작 중인 니케 (빈 문자열 = 아무도 조작 안 함)
        "ctrl_owner_since": -1.0,
        "ctrl_preempt": {},     # 이름 → 조작을 뺏긴 횟수
        "ctrl_focus_forced": "", # 스쿼드 시퀀스가 못박은 카메라 (빈 문자열 = 자동)
        "_squad_seq":   _check_squad_seq(cfg.get("sequence") or [], squad),
        "_squad_seq_i": 0,
        # 카메라가 보고 있는 니케 집합. 조작이 있으면 주인을 따라가고, 없으면 정적 유도값이다
        # (`_resolve_cameras()`). 풀차지 게이지 배율이 이 집합에만 붙는다.
        "camera":       cfg["_camera"],
        # 전투불능 니케. 보스 공격 패턴이 없으면 늘 비어 있다 — 비어 있는 동안은 대상 해석·발동
        # 게이트가 전부 종전과 같은 경로다.
        "down":         set(),
        # 엄폐물 체력. 보스 공격이 엄폐 중인 니케 대신 깎는다. 부서지면 재생성되지 않는다.
        # 최대 체력은 기본값(임의값) 위에 `cover_hp_pct`를 얹은 값이다 — 보스 패턴이 있을 때
        # 프레임마다 `bm.sync_cover_hp()`가 갱신한다. 없으면 기본값 그대로다.
        "cover_base_hp": {c["name"]: float(cfg["cover_hp"]) for c in squad},
        "cover_max_hp": {c["name"]: float(cfg["cover_hp"]) for c in squad},
        "cover_hp":     {c["name"]: float(cfg["cover_hp"]) for c in squad},
        "hp_pct":       {c["name"]: 100.0 for c in squad},
        "hp":           {c["name"]: float(base_stats[c["name"]]["hp"]) for c in squad},
        "base_stats":   base_stats,
        # 기대값 모드에서 확률 이벤트(크리·코어히트·`prob:` 조건)를 소수 누적 발화시키는 잔여분
        # 키: (이벤트명, 캐릭터명) → 누적값
        "rng_acc":      {},
        # 기대값 모드 여부. buff_manager의 `prob:` 조건이 난수 대신 누적 발화를 쓰는 판정
        "rng_expected": cfg.get("rng_mode") == "expected",
        "stacks":       {c["name"]: {} for c in squad},
        "gauges":       {c["name"]: {} for c in squad},
        "burst_stages": {c["name"]: _NIKKE[c["name"]]["burst_stage"] for c in squad},
        "enemy":        enm,
        # 패턴 모드 — 니케마다 이번 프레임의 (조준점, 겨눈 곳). 겨눈 곳은 표적 이름 · `AIM_ADDS`(쫄몹) · ""(본체),
        # 조준점은 좌표 모드만 있다(좌표 off는 None). 조율 뒤에 `_resolve_aims`가 정하고 사격·스킬이 읽는다.
        # 간단 모드는 비어 있다. 정본: boss_pattern.py §조준
        "aim":          {},
        # 표적 이름 → 종류("parts"·"interrupt") — 좌표 off에서 겨눈 표적의 회계를 사격이 가른다(`_stage_aim`)
        "target_kinds": {},
        # 레이어 2 「저지 우선 타격」 — 카메라 니케가 산 저지원 → 쫄몹 → 벌칙 파츠 순으로 겨눈다(패턴 모드에서만 읽는다)
        "aim_interrupt": bool(cfg.get("aim_interrupt")),
    }

    enemy_code = enm.get("code", "")

    char_states: dict[str, CharState] = {
        c["name"]: CharState(c, float(base_stats[c["name"]]["atk"]), enemy_code)
        for c in squad
    }
    squad_names = set(char_states)
    for cs in char_states.values():
        gated = list(cs._click_sched) + list(cs._aim_sched)
        if cs.reload_when is not None:
            gated.append(cs.reload_when)
        for spec in gated:
            gate = spec.get("gate")
            if gate is not None and gate["burst_user"] not in squad_names:
                raise ValueError(
                    f"{cs.name}: gate.burst_user {gate['burst_user']!r}가 스쿼드에 없다. "
                    f"정식 명칭을 쓴다. docs/CONTROL.md §런타임 게이트")

    bm = BuffManager(squad, state)
    # `scaling: "max_ammo_count"`(「최종 최대 장탄 수 1발 당」)가 수령자의 실효 최대 장탄을 읽는 창구
    bm.max_ammo_provider = (lambda name, t: char_states[name]._full_ammo(bm, t)
                            if name in char_states else None)
    burst_ctrl = BurstController(squad, cfg, char_states, enm)
    _register_instant_handlers(bm, char_states, burst_ctrl)

    # 보스 패턴 스케줄러. 없으면 None이고, 아래 모든 보스 자리가 그대로 건너뛴다.
    boss: BossScript | None = None
    if boss_patterns is not None:
        def _superior(caster: str, code: str) -> bool:
            # 속성보호막 통과 — 로스터 코드 상성이거나 `element_code_override` 버프로 그 코드에
            # 우월해졌거나. 인게임이 후자도 인정하고, 버프라 조회 시점에 봐야 한다.
            return (is_element_match(_NIKKE[caster].get("element_code", ""), code)
                    or bm.element_override_match(caster, code))
        # 보스 공격의 무작위 대상은 **자기 난수열**을 쓴다 — 전역 `random`을 같이 쓰면 공격
        # 하나를 넣는 것만으로 크리·코어 판정 순서가 통째로 밀린다.
        # 기대값 모드는 시드와 무관하게 결과가 같아야 하므로 **고정 시드**다(유저 결정 2026-09-15) —
        # 「누구를 때리나」는 기대값으로 펼 수 없는 선택이라(전투불능이 비선형) 난수열을 고정한다.
        # 쫄몹이 있을 때 니케 스킬의 무작위 적 대상(`enemies_random:N`)도 같은 난수열이다.
        if cfg["rng_mode"] == "expected":
            boss_rng = random.Random(_EXPECTED_BOSS_SEED)
        else:
            boss_rng = random.Random(seed) if seed is not None else random.Random()
        boss = BossScript(boss_patterns, enm, _superior, rng=boss_rng)
        state["boss_shield_blocks"] = boss.shield_blocks
        state["target_kinds"] = boss.target_kinds
        if boss.has_summons:
            # 쫄몹이 살아 있는 동안만 적 대상을 적마다 푼다 — 없으면 None으로 종전 센티널 경로
            bm.enemy_resolver = (lambda target, caster: boss.resolve_enemies(target, bm.enemy_has_state, caster)
                                 if boss.has_adds else None)
        state["_on_revive"] = lambda t, name, by: boss.log_squad(t, "", "revive", f"{name} ← {by}")

        def _enemy_buff_cleanse(eff: dict, caster: str, t: float, val: float | None) -> None:
            # 「적 이로운 효과 해제 N개」(로산나 `온 더 렘 2`) — 보스 버프 패턴을 끈다. 보스 패턴이 없으면
            # 적에게 이로운 효과가 없으므로 핸들러도 없다(종전과 같은 무발동).
            if "__enemy__" in bm._resolve_target(eff.get("target", "self"), caster):
                boss.dispel(int(val or 1), t)
        bm.register_instant_handler("enemy_buff_cleanse", _enemy_buff_cleanse)

    # 에임 컨트롤은 좌표 모드 보스의 표적을 이름으로 겨눈다 — 여기서 대조한다(캐릭터만으로는 보스를 모른다)
    for cs in char_states.values():
        for e in cs._aim_sched:
            if boss is None or boss.coord is None:
                raise ValueError(f"{cs.name}: 에임 컨트롤(control.aim)은 좌표 모드 보스(enemy.coord)에서만 쓴다 — "
                                 f"간단 모드·좌표 off에는 겨눌 좌표가 없다. docs/CONTROL.md §에임")
            if e["at"] != "core" and e["at"] not in boss.target_kinds:
                raise ValueError(
                    f"{cs.name}: 에임 표적 {e['at']!r}가 보스 스크립트에 없다 — "
                    f"{' · '.join(sorted(boss.target_kinds)) or '표적 없음'} 또는 core. docs/CONTROL.md §에임")

    sim_log = SimLog() if verbose else None
    burst_ctrl._log = sim_log
    for cs in char_states.values():
        cs._sim_log = sim_log
    result = SimResult(duration=duration, log=sim_log)
    result.char_total = {c["name"]: 0 for c in squad}

    # damage 핸들러: bm.tick()/_activate()에서 호출되는 damage 효과를 처리
    _dot_events: list[HitEvent] = []

    def _coord_skill_hits(cs: CharState, geom, eff: dict, ht: dict, res: dict, buffs: dict, t: float,
                          hit_tag: str, expected: bool) -> list[HitEvent]:
        """좌표 모드 — 스킬 히트 하나가 표적에 **따로** 넣는 히트. 정본: boss_pattern.py §좌표 모드.

        「파츠 포함」 전체기(`hits_parts`)는 산 파츠 전부(저지원은 아니다), 관통 대미지·발사체 폭발 스킬은 시전자의
        조준점을 착탄점으로(탄 분포 없이) 관통·폭발 원 안의 표적. 본체 히트의 크리 판정을 그대로 쓰고
        트리거·게이지는 따로 안 낸다(좌표 off 다중 타격과 같은 규약)."""
        if eff.get("hits_parts"):
            hit = [g for g in geom.targets if g.kind == "parts"]
        else:
            grow = cs._area_radius(geom, buffs, ht["is_pierce_damage"], ht["is_projectile_explosion"])
            if grow <= 0.0:
                return []
            ax, ay = cs._aim_point(bm, geom)
            hit = [g for g in geom.targets if g.shape.contains(ax, ay, grow)]
        out = []
        for g in hit:
            pht = dict(ht, is_part=g.kind == "parts", is_core=False, core_prob=None, is_core_damage=False,
                       crit_override=None if expected else res["is_crit"], _debug_factors=False)
            r = calc_damage(base_atk=cs.base_atk, buffs=buffs, weapon=cs.weapon, hit_type=pht,
                            enemy_def=enm.get("def", 31784), expected=expected)
            out.append(HitEvent(t=t, caster=cs.name, damage=r["damage"], is_crit=r["is_crit"],
                                hit_tag=hit_tag, skill_name=eff.get("name", eff.get("stat", "")),
                                target=g.name, extra=True))
        return out

    def _handle_damage_eff(eff: dict, caster: str, t: float):
        if eff.get("target") == "all_projectiles":
            return
        cs = char_states.get(caster)
        if cs is None:
            return

        # 「누적 → 폭발」 방출 — 계수가 없다. 대미지가 누적기(`target_effect`)가 모은 양
        # **그 자체**라 DealForm을 타지 않는다(유저 결정 2026-09-22: 누적은 방어력 적용 후
        # 값이고 방출은 그대로 꽂는다 — 재적용하면 이중 경감이다). 분배 대미지 판정이라
        # ⑥층 `split_dmg_pct`만 얹는다 — 트로니가 `효율 증가`로 자기 폭발을 키우는 경로다.
        if eff.get("stat", "") == "accum_split_damage":
            ref = eff.get("target_effect", "")
            amount = bm.accum_discharge(ref, t) if ref else 0.0
            if amount <= 0.0:
                return
            _b = bm.get_buffs(caster, "__enemy__", t)
            amount *= 1.0 + _b.get("split_dmg_pct", 0.0) / 100.0
            _rule = eff.get("target", "")
            _dot_events.append(HitEvent(
                t=t, caster=caster, damage=int(amount), is_crit=False,
                hit_tag="accum_split_damage", skill_name=eff.get("name", ref),
                rule=_rule if isinstance(_rule, str) else "", split=True,
            ))
            return

        # 「자신이 가한 피해량의 N% 만큼 고정 대미지」 — 계수가 공격력이 아니라 **트리거한 탄이 준 대미지**다
        # (`full_charge_hit`이 notify에 싣는 `dealt`). 이미 방어력·버프·크리·코어가 적용된 값이라 위 방출과
        # 같은 이유로 DealForm을 다시 타지 않는다 — 고정 대미지라 어떤 층도 얹지 않는다.
        # 자신은 명중 트리거(`hit_count:[이름]`)도 버스트 게이지도 내지 않는다. (에밀리아 `대정령의 철퇴`)
        if eff.get("stat", "") == "dealt_fixed_damage":
            dealt = float(bm.notify_ctx("dealt", 0.0) or 0.0)
            vals = eff.get("values")
            pct = (float(vals.get(_get_skill_lv(cs.char, eff), vals.get("10", 0.0))) if vals
                   else float(eff.get("fixed_value", 0.0)))
            amount = dealt * pct / 100.0
            if amount <= 0.0:
                return
            _rule = eff.get("target", "")
            _dot_events.append(HitEvent(
                t=t, caster=caster, damage=int(amount), is_crit=False,
                hit_tag="dealt_fixed_damage", skill_name=eff.get("name", "dealt_fixed_damage"),
                rule=_rule if isinstance(_rule, str) else "",
            ))
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
                for ref_eff in bm.char_effects(caster):
                    if ref_eff.get("name") != ref_name:
                        continue
                    ref_stat = ref_eff.get("stat", "")
                    ref_parts = ref_stat.split(":")
                    if len(ref_parts) > 1 and ref_parts[1].lstrip("-").isdigit():
                        hit_count = int(ref_parts[1])
                    break
            # 원문 블록 순서 = 실행 순서: 이 딜보다 뒤에 서술된 같은 burst_cast 버프는
            # 계산이 풀버스트로 밀려도 실리면 안 된다 (GAMEPLAY.md §효과 실행 순서).
            eff_with_coeff["_exclude_buffs"] = _later_burst_cast_buffs(bm, caster, eff)
            burst_ctrl._pending_burst_dmg.append((caster, eff_with_coeff, hit_count))
            return

        # damage_formula: "normal_attack" → is_normal_atk=True で일반 공격 버프 적용
        is_normal = eff.get("damage_formula") == "normal_attack"
        buffs = bm.get_buffs(caster, "__enemy__", t)
        buffs["is_element_match"] = cs.element_match(bm)
        is_full_burst = bm.state.get("full_burst", False)
        stat = eff.get("stat", "damage")
        stat_parts = stat.split(":")
        base_stat = stat_parts[0]
        # hit_count 결정
        # - "damage" + hit_count_gauge_ref → 게이지 값만큼 히트
        # - "sequential_damage:N" → N회 (순차 공격)
        # - "sequential_damage:이름" → 게이지/스택 수만큼 히트 (scaling 값 무관)
        # - "<any_damage_stat>:이름" → 게이지/스택/소환체 수만큼 히트
        #   (아인 "armor_break_damage:니어 페더" — 생존 페더 수만큼 개별 발사.
        #    히트를 합치면 크리가 히트마다 판정되지 않고 히트 수 집계도 무너진다)
        # - "<any_damage_stat>:N" (N이 정수) → 1트리거당 N회 발사 (예: bonus_damage:5)
        # - "damage" + scaling=stack_count → scaling_ref 게이지/스택 수만큼 히트
        hit_count = 1
        gauge_ref = eff.get("hit_count_gauge_ref")
        if gauge_ref:
            hit_count = int(bm.state.get("gauges", {}).get(caster, {}).get(gauge_ref, 0))
        elif len(stat_parts) > 1 and stat_parts[1].lstrip("-").isdigit():
            hit_count = int(stat_parts[1])
        elif len(stat_parts) > 1:
            # "<damage_stat>:이름" 형태 — scaling 값 무관하게 게이지/스택/소환체 수 읽기
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
        # scaling: "max_hp_additive" → 시전자의 최종 최대 체력 N%를 공격력에 더한 뒤 계산.
        # 버프가 아니라 이 히트에만 얹는 항이라 buffs 사본의 atk_flat에 넣는다
        # (`atk_from_hp_pct`와 같은 자리 · docs/PARSING.md §scaling).
        _scaling = eff.get("scaling")
        _scalings = _scaling if isinstance(_scaling, list) else ([_scaling] if _scaling else [])
        if "max_hp_additive" in _scalings:
            _pct = float(eff.get("scaling_hp_pct", 0.0))
            buffs = dict(buffs)
            buffs["atk_flat"] = buffs.get("atk_flat", 0.0) + bm.effective_max_hp(caster) * _pct / 100.0

        weapon_type = cs.weapon.get("weapon_type", "")
        ht = default_hit_type(
            is_normal_atk=is_normal,
            is_full_burst=is_full_burst,
            # core_damage는 "코어 명중 대미지"가 명시된 확정 코어 히트 (core_hit condition이 코어 유무를 게이팅)
            is_core=(enm.get("core_px", 0) > 0 and is_normal) or base_stat == "core_damage",
            is_core_damage=(base_stat == "core_damage"),
            # 파츠 판정은 원문이 파츠를 명시한 스킬(hits_parts)에만 붙는다 — 파츠 보스일 때만.
            # reach 파츠가 살아 있거나 좌표 모드면 파츠 몫은 파츠 히트가 따로 받으므로 본체 히트는 판정을 내려놓는다
            is_part=(bool(eff.get("hits_parts")) and enm.get("has_parts", False)
                     and not enm.get(PART_REACH_KEY) and enm.get(GEOM_KEY) is None),
            is_optimal_range=(is_normal and in_optimal_range(enm, caster, weapon_type, buffs)),
            # 「방어력 무시 버스트 스킬 대미지」는 두 축의 복합이라 플래그를 함께 켠다
            # (베스티 : 택티컬 업 `미사일 컨테이너 온라인 3`)
            is_burst_damage=(base_stat in ("burst_damage", "armor_break_burst_damage")),
            # 대상 설명이 '적 전체에게'인 버스트 대미지 → burst_dmg_aoe_pct 수혜
            is_aoe_burst=(base_stat in ("burst_damage", "armor_break_burst_damage")
                          and target_field == "all_enemies"),
            # 대상 설명이 '~ 적 1기에게'인 버스트 대미지 → burst_dmg_single_pct 수혜.
            # 원문 문구가 가르는 축이라 `enemies_*:1` 계열만이다 — `대상에게`(target)·
            # `타겟에게`(boss)·`동일 적 대상에게`(same_target)는 문구가 달라 제외한다
            # (IMPL-STATUS `burst_dmg_single_pct`). 위 AoE판과 배타.
            is_single_burst=(base_stat in ("burst_damage", "armor_break_burst_damage")
                             and isinstance(target_field, str)
                             and target_field.startswith("enemies_")
                             and target_field.endswith(":1")),
            is_pierce_damage=(base_stat == "pierce_damage"),
            is_armor_break_damage=(base_stat in ("armor_break_damage",
                                                 "armor_break_burst_damage")),
            is_dot=(base_stat == "dot_damage"),
            is_projectile_explosion=(base_stat == "projectile_explosion_damage"
                                     or (is_normal and cs.base_weapon_type == "RL")),
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

        # 쫄몹이 있을 때 `_land`가 딜을 나누는 칸(정본: boss_pattern.py §쫄몹). 딜은 보스 기준으로 한 번만
        # 산정한다. 지속 대미지 틱은 효과가 붙은 적에게만 간다 — 규칙을 틱마다 다시 풀지 않는다.
        hit_rule = target_field if isinstance(target_field, str) else ""
        hit_to = None
        if boss is not None and boss.has_summons and eff.get("tick_interval"):
            _ab = next((a for a in bm._active if a.effect is eff and a.caster == caster), None)
            if _ab is not None and _ab.target_chars is not None:
                hit_to = tuple(x for x in _ab.target_chars if _is_enemy(x))

        geom = enm.get(GEOM_KEY)
        for _ in range(hit_count):
            if in_debug_window:
                print(f"t={t:.3f}s  [{eff.get('name', stat)}]  base_atk={cs.base_atk:,}  enemy_def={enm.get('def', 31784):,}")
            res = calc_damage(
                base_atk=cs.base_atk, buffs=buffs, weapon=cs.weapon,
                hit_type=ht, enemy_def=enm.get("def", 31784),
                expected=(cfg.get("rng_mode") == "expected"),
            )
            if in_debug_window:
                print()
            hit_tag = "normal_skill" if is_normal else base_stat
            _dot_events.append(HitEvent(
                t=t, caster=caster, damage=res["damage"],
                is_crit=res["is_crit"], hit_tag=hit_tag,
                skill_name=eff.get("name", stat),
                rule=hit_rule, split=(base_stat == "split_damage"), to=hit_to,
                **(_reach_hit(enm, ht, res, buffs, parts_skill=bool(eff.get("hits_parts")),
                              base_atk=cs.base_atk, weapon=cs.weapon,
                              expected=(cfg.get("rng_mode") == "expected"))
                   if geom is None else {}),
            ))
            if geom is not None:
                _dot_events.extend(_coord_skill_hits(
                    cs, geom, eff, ht, res, buffs, t, hit_tag, cfg.get("rng_mode") == "expected"))
            # hit_count:[스킬명] 이벤트 — named damage effect 명중마다 발생.
            # 이 히트의 크리 여부를 함께 실어 보낸다 (`trigger_hit_crit` 조건용).
            # 기대값 모드에는 is_crit이 없으므로 crit_frac을 소수 누적해 같은 장기
            # 빈도로 발화시킨다 — 일반 공격의 crit_hit 처리와 같은 규약이다.
            if eff_name:
                hit_crit = res["is_crit"]
                if not hit_crit and cfg.get("rng_mode") == "expected":
                    _crit_fired: list[int] = []
                    _notify_frac(bm, f"skill_crit:{eff_name}", caster,
                                 res.get("crit_frac", 0.0), lambda: _crit_fired.append(1))
                    hit_crit = bool(_crit_fired)
                bm.notify(f"hit_count:{eff_name}", t, caster, hit_crit=hit_crit)

        # 스킬 대미지도 무기와 **같은 히트당 값**으로 게이지를 준다. 풀차지 배율은 없다.
        # 리버렐리오(무기 1발 14.0 + 추가타 5 × 5.6 = 42.0%)와 스노우 화이트 : 헤비암즈
        # (14.0 + 6 × 5.6 = 47.6%) 실측이 이 규칙을 결정했다 — 배율이 추가타에도
        # 붙었다면 둘 다 실측의 절반 발수에 만충했어야 한다.
        # 헤비암즈의 6은 **스킬 히트만** 센 것이다(오토 파이어 1 = 1 + 오토 파이어 2 = 5).
        # 정본 문서 채점표의 "7히트"는 무기 1발까지 포함한 총 히트 수라 자리가 다르다.
        # ⬜ DoT 틱도 게이지를 주는지는 미검증이다. 지금은 다른 스킬 히트와 같게 둔다
        #    (docs/DATA_VERIFY.md §버스트 게이지).
        # 무기값과 다른 버충 계수를 갖는 스킬은 `data/burst_gauge.json` `_exceptions`가
        # 대신 값을 준다. 지금은 라피 : 레드 후드 `부착형 유탄 4` 하나뿐이고, 왜 다른지는
        # 모른다 — 다타격이 아님은 유저가 인게임에서 확인했다(부착 7회).
        # **속성보호막에 막힌 스킬 대미지는 게이지를 안 채운다**(유저 확인 2026-09-15) — 무기 사격
        # 게이지와 게이지 충전 효과는 막혀도 채운다. 딜은 다음 프레임 `_land`의 `boss.gate`가 거르고,
        # 게이지는 이 효과가 나간 프레임의 보스 상태로 판정한다.
        gauge_src = eff_name or stat
        gauge_be = (BURST_GAUGE_EXCEPTIONS.get(caster, {})
                    .get(gauge_src, {}).get("burst_energy"))
        if boss is None or not boss.shield_blocks(caster):
            bm.add_burst_gauge(cs._burst_gain(buffs, hit_count, burst_energy=gauge_be), t, caster,
                               f"skill:{gauge_src}")

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

        def _gauge_event_cb(t: float, caster: str, source: str, amount: float, gauge: float):
            sim_log.gauge_log.append(GaugeLogEntry(
                t=t, caster=caster, source=source, amount=amount, gauge=gauge,
            ))
        bm.register_gauge_event_handler(_gauge_event_cb)

        # 카메라는 풀차지 **게이지** 배율에만 쓰이므로 사이클을 판정하는 모드에서만 적는다
        # (위 만충 로그와 같은 이유 — "fixed" baseline 불변).
        if cfg["burst_gauge_mode"] == "accumulate":
            # 스쿼드 순서로 적는다 — frozenset 순회 순서는 실행마다 달라질 수 있어
            # 로그가 흔들리면 스냅샷 diff가 가짜로 뜬다.
            _cams = [c["name"] for c in squad if c["name"] in cfg["_camera"]]
            _who = " · ".join(_cams) if _cams else "없음"
            if len(_cams) > 1:
                _who += f'  [camera_mode="shared" — 비현실적 상한]'
            sim_log.burst_log.append(BurstLogEntry(
                t=0.0, event=f"카메라 초점: {_who}", caster=""))

    def _apply_lifesteal(ev: HitEvent, bm: BuffManager, base_stats: dict, t: float):
        buffs = bm.get_buffs(ev.caster, "__enemy__", t)
        ls = buffs.get("lifesteal_pct", 0.0)
        if ls <= 0.0:
            return
        heal = ev.damage * ls / 100.0 * bm.heal_received_mult(ev.caster, t)
        hp = bm.state["hp"]
        bs = base_stats.get(ev.caster, {})
        base_hp = float(bs.get("hp", 0.0))
        max_hp = bm.effective_max_hp(ev.caster)
        hp[ev.caster] = min(hp.get(ev.caster, base_hp) + heal, max_hp)
        bm.sync_hp(ev.caster)
        # 라이프스틸의 `heal_source`는 **때린 본인**이다 (자체 판단 2026-09-21 — 유저 확인 전).
        # 버프를 건 아군이 따로 있어도 회복은 그 니케 자신의 공격에서 나오고, `buffs`는
        # 합산값이라 여러 라이프스틸이 겹쳤을 때 어느 아군의 몫인지 가를 수 없다.
        # ⬜ 인게임에서 아군이 걸어 준 라이프스틸을 「남이 쓴 회복 효과」로 치는지는 미확인.
        bm.notify("event:heal_received", t, ev.caster, heal_source=ev.caster)

    def _land_boss(ev: HitEvent, t: float) -> None:
        if boss is not None and not boss.gate(ev):
            return
        result.hits.append(ev)
        result.char_total[ev.caster] += ev.damage
        _apply_lifesteal(ev, bm, base_stats, t)
        # 「누적 → 폭발」 누적기 — 보스가 실제로 받은 딜만 센다. 쫄몹 몫은 `boss.route`가
        # 이미 갈라 갔고 저지원은 총딜 밖이라 여기 오지 않는다 (트로니 · 도로시)
        bm.accumulate_damage(ev.caster, ev.damage, t)
        if boss is None or not (ev.part_damage or ev.interrupt_damage):
            return
        # 좌표 off 다중 타격 — 같은 발이 닿은 파츠마다 히트가 하나씩 더 들어가 총딜에 더해진다. 닿은 저지원은
        # 총딜 밖(`boss.interrupt_dealt`)이라 흡혈만 붙인다 (정본: boss_pattern.py §파츠 다중 타격). 게이트는
        # 본체 히트가 이미 지났다
        for name in boss.part_hits(ev, t):
            pev = replace(ev, damage=ev.part_damage, reach=0, part_damage=0, part=name,
                          interrupt_reach=0, interrupt_damage=0)
            result.hits.append(pev)
            result.char_total[pev.caster] += pev.damage
            _apply_lifesteal(pev, bm, base_stats, t)
            bm.accumulate_damage(pev.caster, pev.damage, t)
        for _name in boss.interrupt_hits(ev, t):
            _apply_lifesteal(replace(ev, damage=ev.interrupt_damage), bm, base_stats, t)

    def _land_target(ev: HitEvent, t: float) -> None:
        """표적에 떨어진 히트(좌표 모드의 착탄 · 좌표 off의 겨눈 발). 게이트(사라짐·속성보호막)는 본체 히트와 같고, **파츠 히트는 총딜에,
        저지원 히트는 총딜 밖**(`boss.interrupt_dealt`)으로 간다(유저 결정 2026-09-18). 흡혈은 둘 다 받는다.
        쫄몹으로 나누지 않는다 — 이미 그 표적에 떨어진 히트다."""
        if not boss.gate(ev):
            return
        if boss.hit_target(ev, t) == "parts":
            result.hits.append(ev)
            result.char_total[ev.caster] += ev.damage
        _apply_lifesteal(ev, bm, base_stats, t)

    def _land(ev: HitEvent, t: float) -> None:
        """히트 하나를 결과에 넣는다. 보스 게이트(사라짐·속성보호막)에 막히면 아무 데도 안 남는다
        — 딜도, 흡혈도, 표적 체력도.

        쫄몹이 살아 있으면(또는 쫄몹에 붙은 지속 대미지 틱이면) 먼저 적마다 나눈다(`boss.route`). 보스 몫만
        게이트·파츠 표적·총딜로 가고, 쫄몹 몫은 쫄몹 체력으로 간다 — 총딜에 없다(유저 결정 2026-09-16).
        표적에 떨어진 히트(`ev.target`)는 `_land_target`으로 간다. 조준 딜이 누구에게 가는가는 시전자가 겨눈 곳이다
        (`boss.aim_of`, boss_pattern.py §조준)."""
        if ev.target:
            _land_target(ev, t)
            return
        if boss is not None and boss.has_summons and (ev.to is not None or boss.has_adds):
            for target, w in boss.route(ev, bm.enemy_has_state):
                part = ev if w == 1.0 else replace(ev, damage=round(ev.damage * w),
                                                   part_damage=round(ev.part_damage * w),
                                                   interrupt_damage=round(ev.interrupt_damage * w))
                if target == ENEMY:
                    _land_boss(part, t)
                elif boss.hit_add(part, target, t):
                    _apply_lifesteal(part, bm, base_stats, t)
            return
        _land_boss(ev, t)

    squad_order = [c["name"] for c in squad]

    def _attack_targets(spec, t: float) -> list[str]:
        """이 발이 누구를 때리나. 정본: boss_pattern.py §공격.

        **도발은 전체 공격(all)을 뺀 모든 공격을 끈다**(유저 확인 2026-09-15) — 도발 중인 니케가
        자리를 먼저 가져가고 남은 자리를 원래 규칙으로 채운다. 도발에 안 끌리는 공격은
        `ignore_taunt`로 적는다.
        - all — 산 니케 전원. 도발·은신과 무관하다.
        - slot — 정해진 자리. 도발자가 자리를 먼저 가져가고 남은 자리를 적힌 순서로 채운다. 은신과 무관하다.
        - random:N · top_atk:N — 남은 자리를 은신이 아닌 산 니케에서 규칙대로 채운다. 전원이 은신이면
          은신을 무시한다(⬜ 인게임 미확인, docs/DATA_VERIFY.md).
        """
        alive = bm._alive()
        if spec.rule == "all":
            return alive
        seats = len(spec.slots) if spec.rule == "slot" else spec.n
        taunt = [] if spec.ignore_taunt else bm.taunters(t)[:seats]
        if spec.rule == "slot":
            listed = [squad_order[i] for i in spec.slots
                      if squad_order[i] in alive and squad_order[i] not in taunt]
            return taunt + listed[:seats - len(taunt)]
        rest = [n for n in alive if n not in taunt]
        pool = [n for n in rest if not bm.has_live_stat(n, "stealth", t)] or rest
        need = spec.n - len(taunt)
        if need <= 0 or not pool:
            return taunt
        if spec.rule == "random":
            picked = boss_rng.sample(pool, min(need, len(pool)))
        else:
            picked = sorted(pool, key=bm._effective_atk, reverse=True)[:need]
        return taunt + [n for n in squad_order if n in picked]

    def _boss_attack(hit: AttackHit, t: float) -> None:
        """보스 공격 한 발을 대상마다 층에 나눠 넣는다.

        피해 = max((보스 공격력 − 니케 최종 방어력) × 계수% × (100% + 받는 피해 증감%), 1)
        — 니케가 적을 때리는 식(damage.py ②·①·⑥)과 같은 모양이다(유저 결정). 크리는 없다.

        층 (유저 확인):
          비관통 — 맨 앞 한 층만 받는다. 보호막 → (엄폐 중이고 엄폐물이 살아 있으면) 엄폐물
                   → (엄폐 중이 **아니고** 분신이 살아 있으면) 분신 → 체력.
                   **앞 층이 깨져도 남은 피해는 넘어가지 않는다.** 보호막이 여럿이면 나중에 생긴
                   하나가 맨 앞이다(⬜ 순서는 잠정).
          관통   — 보호막 **전부**·(엄폐 중이면) 엄폐물·체력이 **같은 피해를 각각** 받는다.
                   분신은 관통도 가르지 않는다 — 막이 아니라 별개 개체다.
        엄폐물이 부서졌으면 엄폐해도 막아 주지 않는다. **분신은 엄폐물의 짝이다** — 엄폐물이
        엄폐 중에 대신 맞는 자리를, 분신은 나와서 사격 중일 때 대신 맞는다(유저 2026-09-21,
        ⬜ 인게임 미확인). 무적은 체력 피해만 0으로 한다 —
        피격 이벤트는 그대로 나간다(⬜ 인게임 미확인, docs/DATA_VERIFY.md).

        `받는 대미지 균등 분배`(`received_dmg_split_even`)가 걸려 있으면 계산이 끝난 피해를
        집단 머릿수로 나눠 멤버마다 `_land`한다 — 아래 주석 참조.
        """
        spec = hit.spec
        atk = spec.atk if spec.atk is not None else float(enm.get("atk", DEFAULT_BOSS_ATK))
        # 적에게 걸린 「시전자 기준 공격력 ▼」(`atk_caster_based_pct`)를 **정액**으로 깎는다.
        # 아군판 `enemy_def_down_flat`이 적 방어력을 깎는 것의 공격력판이고 부호 규약도 같다
        # (감소면 음수). 적 공격력은 이 식에만 쓰이므로 딜 계산에는 닿지 않는다 — 키리 `곁눈질`.
        # ⬜ 쫄몹이 쏜 발은 깎지 않는다: `hit.source`가 쫄몹의 **표시 이름**이라 적 id
        # (`__enemy__:<패턴>#<번호>`)로 되돌릴 배선이 없다. 쫄몹에게 이 디버프를 걸고 그 쫄몹이
        # 쏘는 조합은 아직 로스터에 없다 (docs/DATA_VERIFY.md §보스 → 니케 피해).
        if not hit.source:
            atk = max(atk + bm.enemy_atk_down_flat("__enemy__", t), 0.0)
        for name in _attack_targets(spec, t):
            if bm.is_down(name):
                continue
            dmg = (max(atk - bm._effective_def(name), 0.0) * spec.coeff / 100.0
                   * max(0.0, 1.0 + bm.incoming_dmg_pct(name, t) / 100.0))
            dmg = max(dmg, 1.0)
            # 받는 대미지 균등 분배 — **맞은 니케 기준으로 계산이 끝난 피해**를 집단이 똑같이 나눠 진다
            # (폴리 `도그 테라피 2` · 율하 `위크 메이커 2` · 자칼 `치얼업 자칼`).
            # 방어력·받는 피해 증감은 맞은 니케 것으로 한 번만 본다 — 원문이 나누는 대상이 「받는
            # 대미지」이기 때문이다(⬜ 인게임 미확인: 멤버마다 자기 방어력으로 다시 계산하는지).
            # 보호막·엄폐물·무적·불굴은 멤버마다 자기 것이 막고, 피격 이벤트는 **맞은 니케만** 받는다 —
            # 나눠 진 쪽은 피해를 받았을 뿐 맞은 것이 아니다(⬜ 인게임 미확인).
            group = bm.split_group(name, t)
            if group:
                share = dmg / len(group)
                for member in group:
                    _land_squad(member, share, hit, t, notify_hit=(member == name))
            else:
                _land_squad(name, dmg, hit, t, notify_hit=True)

    def _land_squad(name: str, dmg: float, hit: AttackHit, t: float, *, notify_hit: bool) -> None:
        """계산이 끝난 한 발의 피해를 니케 하나의 층에 넣는다 — `_boss_attack`의 대상별 몸통.

        층 규칙과 무적·불굴 처리는 `_boss_attack` docstring이 정본이다. `notify_hit`이 거짓이면
        피격 이벤트를 쏘지 않는다(균등 분배로 피해만 나눠 받은 멤버).
        """
        spec = hit.spec
        if bm.is_down(name):
            return
        cs = char_states[name]
        # 엄폐 불가(`cover_disabled`)면 재장전 중이어도 엄폐물 뒤가 아니다
        covered = (cs.in_cover(t) and state["cover_hp"][name] > 0.0
                   and not cs.cover_blocked(t, bm))
        shield = bm.absorb_shield(name, dmg, t, pierce=spec.pierce)
        cover = 0.0
        if spec.pierce or shield <= 0.0:
            if covered:
                cover = min(state["cover_hp"][name], dmg)
                state["cover_hp"][name] -= cover
                if state["cover_hp"][name] <= 0.0:
                    bm.break_cover(name)
                    boss.log_squad(t, hit.pattern, "cover_break", name)
        to_hp = dmg if (spec.pierce or (shield <= 0.0 and cover <= 0.0)) else 0.0
        # 분신(`decoy`) — 보호막·엄폐물 **다음**, 체력 **바로 앞** 층이다.
        # **엄폐 중이 아닐 때만** 대신 맞는다: 엄폐물이 엄폐 중에 대신 맞는 것의 짝으로,
        # 분신은 니케가 나와서 **사격 중일 때** 대신 맞는다(유저 2026-09-21, ⬜ 인게임 미확인 —
        # `docs/DATA_VERIFY.md` §보스 → 니케 피해). 그래서 엄폐물과 분신은 사실상 배타다.
        # 받았으면 그 한 발은 거기서 끝난다(보호막·엄폐물과 같은 규약) — 관통도 가르지 않는다.
        decoy = 0.0
        if to_hp and not covered:
            decoy = bm.absorb_decoy(name, to_hp, t)
            if decoy > 0.0:
                to_hp = 0.0
        if to_hp and bm.has_live_stat(name, "invincible", t):
            to_hp = 0.0
        # 불굴(`undying`) — 체력이 0이 될 발을 1 남기고 받는다. 쓰러지지 않았으니 아래 임계 이벤트는
        # 정상으로 나간다(유저 확인 2026-09-15).
        if (to_hp and state["hp"][name] - to_hp <= 0.0
                and bm.has_live_stat(name, "undying", t)):
            to_hp = max(state["hp"][name] - 1.0, 0.0)
        # 「전투불능에 이르는 공격에 피격 시」(`event:lethal_hit`) — 무적·불굴에 막히지 않은 치명 발을
        # **받기 전에** 알린다. 거기서 켜진 불굴이 **이 발**을 받아야 트리거가 뜻을 가지므로 불굴을 한 번
        # 더 본다(마키마 `발각된 모양이네`). 이미 불굴이면 위에서 체력 1로 깎여 치명이 아니라 나가지 않는다.
        # 쓰러진 **뒤**의 `event:self_down`과 다른 축이다. 균등 분배로 나눠 받은 몫에도 나간다 — 피격
        # 이벤트는 맞은 니케만 받지만 치명 판정은 체력 층의 일이다(⬜ 인게임 미확인, docs/DATA_VERIFY.md).
        if to_hp and state["hp"][name] - to_hp <= 0.0:
            bm.notify("event:lethal_hit", t, name)
            if bm.has_live_stat(name, "undying", t):
                to_hp = max(state["hp"][name] - 1.0, 0.0)
        # **체력이 0에 닿은 발은 곧바로 전투불능이다.** 임계 이벤트(`hp_below:T`)를 쏘지 않는다 —
        # 쏘면 「체력 20% 이하 도달 시 최대 체력 ▲」(목단 `근성`)가 이미 0이 된 체력을 되살린다
        # (유저 확인 2026-09-15 — 인게임도 그냥 쓰러진다).
        fell = bool(to_hp) and state["hp"][name] - to_hp <= 0.0
        if to_hp:
            state["hp"][name] = max(0.0, state["hp"][name] - to_hp)
            if not fell:
                bm.sync_hp(name)
        # 공격에 딸린 디버프는 **체력에 피해가 들어간 발만** 건다(유저 확인 2026-09-15) — 보호막·엄폐물이
        # 받았거나 무적이면 안 걸리고, 이 발로 쓰러지면 걸어 봐야 곧바로 사라진다. 피격 트리거보다 먼저 —
        # 맞은 발의 효과가 붙은 뒤에 니케가 반응한다.
        # 균등 분배로 피해만 나눠 받은 멤버(`notify_hit` 거짓)에게는 안 건다 — 피격 이벤트와 같은 이유다.
        if spec.debuffs and to_hp and not fell and notify_hit:
            boss.note_debuff(hit.pattern, sum(
                bm.apply_boss_effect(d.effect, name, t) for d in spec.debuffs))
        if notify_hit:
            bm.notify("received_hit", t, name)
        if cover:
            bm.notify("event:cover_hit", t, name)
        fell = fell and not bm.is_down(name)
        if fell:
            state["hp"][name] = 0.0     # 피격 트리거의 회복이 끼어들었어도 쓰러진 발이다
        boss.note_attack(hit.pattern, to_hp)
        result.squad_hits.append(SquadHitEntry(
            t=t, pattern=hit.pattern, target=name, damage=dmg, pierce=spec.pierce,
            decoy=decoy, shield=shield, cover=cover, hp=to_hp,
            hp_after=state["hp"][name], down=fell, by=hit.source))
        if fell:
            bm.knock_down(name, t)
            cs.on_down(t, bm)
            boss.log_squad(t, hit.pattern, "down", f"{name} ({hit.source})" if hit.source else name)
            bm.notify_down(name, t)     # 정리가 끝난 뒤 — 부활이 여기서 나올 수 있다

    def _boss_debuff(hit: AttackHit, t: float) -> None:
        """`debuff` 패턴의 한 발 — 대상을 공격과 같은 규칙(도발·은신 포함, 유저 확인)으로 고르고 목록의 디버프를
        니케마다 건다. 면역·전투불능이면 안 붙는다."""
        spec = hit.spec
        names = _attack_targets(spec, t)
        n = 0
        for d in spec.debuffs:
            landed = [name for name in names if bm.apply_boss_effect(d.effect, name, t)]
            n += len(landed)
            missed = [name for name in names if name not in landed]
            boss.log_squad(t, hit.pattern, "debuff",
                           f"{d.name} → {' · '.join(landed) or '없음'}"
                           + (f" (면역: {' · '.join(missed)})" if missed else ""))
        boss.note_debuff(hit.pattern, n)

    # 보스 지속 피해(`dot` 디버프)의 틱 예약: ActiveBuff uid → [걸린 시각, 다음 틱, 만료, 버프]
    _dot_sched: dict[int, list] = {}

    def _boss_dots(t: float) -> None:
        """보스가 건 지속 피해의 틱. 정본: boss_pattern.py §디버프.

        틱 피해 = max((보스 공격력 − 니케 최종 방어력) × 계수% × 중첩 × (100% + 받는 피해 증감%), 1) —
        **체력만 받는다**(보호막·엄폐물 무시, 유저 확인). 무적·불굴·전투불능은 공격과 같다. 피격 이벤트는 쏘지
        않는다(⬜ 인게임 미확인). 첫 틱은 걸린 뒤 interval초이고, 다시 걸리면(중첩·갱신) 그때부터 다시 잰다 —
        니케 지속 대미지의 재발동 규약과 같다. 만료 시각에 떨어지는 틱까지 들어간다(같은 규약).

        예약을 버프와 따로 드는 이유: 만료 시각의 마지막 틱이 올 때 `bm.tick`이 버프를 이미 치웠다. 그래서
        버프가 사라졌어도 만료 시각에 닿았으면 남은 틱을 넣고, 그 전에 사라졌으면(해제·전투불능·패턴 종료)
        남은 틱을 버린다."""
        live = {ab.uid: ab for ab in bm._by_stat(DOT_STAT) if ab.caster == "__enemy__"}
        for uid, ab in live.items():
            s = _dot_sched.get(uid)
            if s is None or s[0] != ab.activated_at:
                _dot_sched[uid] = [ab.activated_at,
                                   ab.activated_at + ab.effect["_boss_interval"], ab.expires_at, ab]
            else:
                s[2] = ab.expires_at
        for uid in list(_dot_sched):
            _, next_t, expires, ab = _dot_sched[uid]
            if uid not in live and t < expires - 1e-9:
                del _dot_sched[uid]
                continue
            interval = ab.effect["_boss_interval"]
            while next_t <= min(t, expires) + 1e-9:
                for name in list(ab.target_chars or []):
                    _dot_tick(ab, name, t)
                next_t += interval
            if next_t > expires + 1e-9:
                del _dot_sched[uid]
            else:
                _dot_sched[uid][1] = next_t

    def _dot_tick(ab, name: str, t: float) -> None:
        if bm.is_down(name):
            return
        eff = ab.effect
        atk = eff.get("_boss_atk") or float(enm.get("atk", DEFAULT_BOSS_ATK))
        dmg = (max(atk - bm._effective_def(name), 0.0) * eff["_boss_coeff"] / 100.0 * ab.stack
               * max(0.0, 1.0 + bm.incoming_dmg_pct(name, t) / 100.0))
        dmg = max(dmg, 1.0)
        to_hp = 0.0 if bm.has_live_stat(name, "invincible", t) else dmg
        if (to_hp and state["hp"][name] - to_hp <= 0.0
                and bm.has_live_stat(name, "undying", t)):
            to_hp = max(state["hp"][name] - 1.0, 0.0)
        # 체력이 0에 닿은 틱은 임계 이벤트 없이 곧바로 전투불능이다 — 공격과 같은 규약
        fell = bool(to_hp) and state["hp"][name] - to_hp <= 0.0
        if to_hp:
            state["hp"][name] = max(0.0, state["hp"][name] - to_hp)
            if not fell:
                bm.sync_hp(name)
        pattern = eff["_boss_pattern"]
        result.squad_hits.append(SquadHitEntry(
            t=t, pattern=pattern, target=name, damage=dmg, pierce=False,
            hp=to_hp, hp_after=state["hp"][name], down=fell, source=eff["name"]))
        if fell:
            bm.knock_down(name, t)
            char_states[name].on_down(t, bm)
            boss.log_squad(t, pattern, "down", f"{name} ({eff['name']})")
            bm.notify_down(name, t)

    def _boss_state_effects(t: float) -> None:
        """`begin_frame` 직후 — 닫히거나 해제된 패턴의 효과를 풀고, 열린 보스 버프를 적에게 붙인다. 순서가 이래야
        같은 프레임에 닫혔다 다시 열린 순환 패턴이 새 효과를 잃지 않는다."""
        for pid in boss.released:
            bm.release_boss_effects(pid, t)
        boss.released.clear()
        for eff in boss.enemy_effects:
            bm.apply_boss_effect(eff, "__enemy__", t)
        boss.enemy_effects.clear()

    aim_log: list[tuple[float, str, str]] = []

    def _resolve_aims(t: float) -> None:
        """패턴 모드 — 니케마다 이번 프레임에 겨눌 곳을 정한다(`state["aim"]` · `boss.aim_of`). 조율(카메라) **뒤**,
        캐릭터 tick 앞. 정본: boss_pattern.py §조준 · docs/CONTROL.md §에임.

          손 에임       (좌표 모드) 조작을 잡은 니케(solo = 카메라 주인)에 열린 `control["aim"]` 항목이 있으면 그 표적
          카메라 니케   `boss.aim_target` — 레이어 2(`aim_interrupt`)면 산 저지원 → 쫄몹 → 벌칙 파츠 → 본체,
                       레이어 1이면 쫄몹 → 본체
          나머지       풀버스트 중이거나 [사격 집중](`focus_fire`)을 받았으면 카메라 니케의 조준, 아니면 쫄몹 → 본체
        조준점은 좌표 모드만 있다 — 표적이면 그 중심, 쫄몹·본체면 자동 에임(쫄몹은 좌표가 없다). 좌표 off는 None.
        겨누던 것이 사라지면 다음 프레임부터 다음 순서로 떨어진다(프레임 맨 앞의 산 집합을 본다)."""
        geom = boss.geom
        cams = [n for n in squad_order if n in state["camera"]]
        layer2 = state["aim_interrupt"]

        def point(target: str):
            if geom is None:
                return None
            c = geom.center(target) if target and target != AIM_ADDS else None
            return c if c is not None else geom.auto_aim

        def hand(name: str) -> tuple | None:
            if geom is None:
                return None
            cs = char_states[name]
            e = cs._aim_entry(t, bm, geom) if cs._owns(bm) else None
            return (geom.center(e["at"]), e["at"]) if e is not None else None

        def camera_aim(name: str) -> tuple:
            got = hand(name)
            if got is not None:
                return got
            target = boss.aim_target(True, layer2)
            return point(target), target

        rest = boss.aim_target(False, layer2)
        lead = camera_aim(cams[0]) if cams else (point(rest), rest)
        focus = state["full_burst"]
        aims: dict[str, tuple] = {}
        for name in squad_order:
            if name in cams:
                aims[name] = lead if name == cams[0] else camera_aim(name)
                continue
            got = hand(name)     # warn·strict 상한 모드는 전원이 조작을 잡는다
            if got is not None:
                aims[name] = got
            elif focus or bm.has_live_stat(name, "focus_fire", t):
                aims[name] = lead
            else:
                aims[name] = (point(rest), rest)
        # 겨눈 곳이 바뀐 니케만 적는다 — 보고(`boss_summary` [에임])가 구간으로 접는다
        for name, (_, target) in aims.items():
            prev = state["aim"].get(name)
            if prev is None or prev[1] != target:
                aim_log.append((t, name, target))
        state["aim"] = aims
        boss.aim_of = {name: target for name, (_, target) in aims.items()}

    # 보스 상태는 전투 시작 효과보다도 먼저 정한다 — t=0 프레임의 누구도 기본 상태를 읽으면
    # 안 된다(`core_hit` 조건의 전투 시작 버프 등). 이때 나온 이벤트는 루프 첫 프레임의
    # 통지 자리에서 나간다. 루프의 t=0 호출은 전이가 이미 끝나 있어 아무것도 안 한다.
    _boss_events: list[str] = []
    if boss is not None:
        _boss_events += boss.begin_frame(0.0, enm)
        _boss_state_effects(0.0)
        state["boss_vanish"] = boss.vanished
        if boss.has_summons:
            state["enemy_count"] = boss.enemy_count

    bm.battle_start(0.0)

    # battle_start 버프 적용 후 장탄을 실제 max_ammo로 초기화
    for cs in char_states.values():
        cs.ammo = cs._full_ammo(bm, 0.0)
        if sim_log is not None:
            sim_log.ammo_log.append(AmmoLogEntry(t=0.0, caster=cs.name, ammo=cs.ammo))

    # 파츠 파괴 주기 (enemy["part_break_interval"], 초). 0이면 무발동.
    # `event:part_destroy`는 원래 notify 호출처가 없어 영구 무발동이었다 — 보스 sim에서
    # 파츠가 실제로 파괴되지 않기 때문. 파츠 파괴에 반응하는 캐릭터(아크레인저 블랙 배터리)를
    # 두 모드로 비교하기 위한 스위치다: 기본은 무발동, 주기를 주면 그 간격으로 발생.
    # **간단 모드(보스 패턴 없음)의 칸이다** — 패턴 모드에서는 파괴가 표적이 실제로 깨질 때만
    # 나가야 하므로 적으면 `boss_mode`가 거절한다(유저 결정 2026-09-15 「둘이 함께 켜지지 않는다」 ·
    # 2026-09-19 적으로 옮김). 둘 다 켜 두면 이벤트가 이중으로 나갔다.
    _part_break_interval = float(enm["part_break_interval"])
    _next_part_break = _part_break_interval if _part_break_interval > 0 else math.inf

    t = 0.0
    while t <= duration:
        # 보스 상태 확정 — 맨 앞. 이 프레임의 누구도 읽기 전에 코어·방어력·적정거리·사라짐이
        # 정해져야 한다.
        if boss is not None:
            _boss_events += boss.begin_frame(t, enm)
            _boss_state_effects(t)
            state["boss_vanish"] = boss.vanished
            # 적 수(보스 1 + 산 쫄몹)도 프레임 맨 앞에 정한다 — 프레임 안에서 쫄몹이 죽어도 다음 프레임에 반영
            if boss.has_summons:
                state["enemy_count"] = boss.enemy_count

        bm.tick(t)

        # 엄폐물 최대 체력(`cover_hp_pct`)의 증감을 현재 체력에 옮긴다 — 보스 공격·엄폐물 회복·
        # 「엄폐물 체력이 가장 낮은 아군」이 이 프레임에 읽기 전에. 패턴이 없으면 엄폐물이 깎이지 않으므로
        # 기본값 그대로 둔다 — 늘 가득 찬 엄폐물이라 배율이 결과를 바꾸지 않는다.
        if boss is not None:
            for char in squad:
                bm.sync_cover_hp(char["name"], t)

        if t >= _next_part_break:
            for char in squad:
                bm.notify("event:part_destroy", t, char["name"])
            _next_part_break += _part_break_interval

        # 보스 이벤트 — 파츠 파괴 주기와 **같은 자리**라 두 발생원이 같은 규약이 된다.
        # `bm.tick` 뒤인 이유도 같다: 만료 정리보다 앞서 버프를 붙이면 같은 프레임에 지워질 수 있다.
        if _boss_events:
            for ev_name in _boss_events:
                for char in squad:
                    bm.notify(ev_name, t, char["name"])
            _boss_events.clear()
        # 사라진 쫄몹을 적 효과의 대상에서 지운다 — 사망 통지 **뒤라서** 「[상태] 적 사망 시」가 죽은 쫄몹에
        # 붙어 있던 상태를 아직 본다
        if boss is not None and boss.gone:
            bm.drop_enemies(boss.gone, t)
            boss.gone.clear()

        # 보스 공격·디버프 — 통지 자리 바로 뒤. 피격이 낳는 버프(`received_hit_count` 등)도 만료 정리가
        # 끝난 뒤에 붙어야 같은 프레임에 지워지지 않는다. 지속 피해 틱이 **먼저**다 — 니케 지속 대미지가
        # `bm.tick`에서 틱을 넣고 나서 재발동을 받는 것과 같은 순서라, interval마다 다시 걸리는 지속 피해도
        # 틱을 잃지 않는다. 같은 프레임의 발은 패턴 선언 순으로 나간다.
        if boss is not None:
            _boss_dots(t)
            if boss.attacks:
                for _hit in boss.attacks:
                    if isinstance(_hit.spec, AttackSpec):
                        _boss_attack(_hit, t)
                    else:
                        _boss_debuff(_hit, t)
                boss.attacks.clear()

        for ev in _dot_events:
            _land(ev, t)
        _dot_events.clear()

        for ev in burst_ctrl.tick(t, bm, state):
            _land(ev, t)

        # 스쿼드 시퀀스 → 조작자(카메라) 결정 → 캐릭터. 순서의 근거는
        # docs/CONTROL.md §판정 자리 (틱 내 순서에 답이 달라지지 않게 한다).
        _pump_squad_seq(t, bm, squad, char_states)
        _arbitrate_control(t, bm, squad, char_states, cfg["_camera"])
        # 패턴 모드 — 카메라가 정해진 뒤 니케마다 겨눌 곳을 정한다(손 에임은 카메라를 잡은 니케에게만).
        # 겨눌 표적·쫄몹이 없는 스크립트는 전원 본체라 건너뛴다
        if boss is not None and (boss.geom is not None or boss.target_kinds or boss.has_summons):
            _resolve_aims(t)

        for char in squad:
            for ev in char_states[char["name"]].tick(t, bm, enm, cfg):
                _land(ev, t)

        t += DT

    # 전투가 끝날 때까지 열려 있던 조작 구간을 닫는다 — 조작자 관점 로그가 마지막 구간을
    # 통째로 잃지 않게 한다 (docs/CONTROL.md §두 관점).
    for cs in char_states.values():
        cs._close_ctrl(duration)
    if sim_log is not None:
        sim_log.control_preempt = dict(state["ctrl_preempt"])

    # 루프 종료 직후 남은 `_dot_events`를 한 번 더 수거한다. 이 버퍼는 "다음 프레임
    # 시작에 수거"되는 구조라 마지막 프레임에서 burst_ctrl.tick()/char tick()이 새로
    # 채운 몫은 다음 프레임이 없어 수거되지 못한 채 사라진다(손실은 duration 대비
    # 미미하지만 경로는 확실하다) — 여기서 마저 비운다.
    for ev in _dot_events:
        _land(ev, duration)
    _dot_events.clear()

    if boss is not None:
        boss.finish(duration)
        result.boss_log = boss.log
        result.boss_score = boss.score
        result.boss_unmodeled = list(boss.unmodeled)
        if boss.has_summons:
            result.add_char_total = {c["name"]: round(boss.add_dealt.get(c["name"], 0.0)) for c in squad}
            result.add_total = sum(result.add_char_total.values())
            result.add_overkill = round(boss.add_overkill)
        if boss.coord is not None or boss.interrupt_dealt:
            result.interrupt_char_total = {c["name"]: round(boss.interrupt_dealt.get(c["name"], 0.0))
                                           for c in squad}
            result.interrupt_total = sum(result.interrupt_char_total.values())
        result.aim_log = aim_log

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
            "cube": {"name": "렐릭 베어 큐브", "level": 5},
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
