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
from .buff_manager import (
    BuffManager, _QUANT_PARTS_KEY, _get_skill_lv,
    BURST_GAUGE_EXCEPTIONS,
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
# `control` 자체가 쓸 수 있는 키. 여기도 닫는다 — 오타 난 축은 조용히 아무것도 안 한다.
_CONTROL_KEYS = ("click", "tap_fire", "hold", "reload", "cover", "burst",
                 "sequence", "priority")

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

        # charge (SR/RL)
        if self.fire_mode == "charge":
            charge_time_raw = char.get("charge_time_frames")
            if charge_time_raw is not None:
                self.charge_time_base: float = charge_time_raw / 60.0
            else:
                self.charge_time_base = weapon_data["charge_time"]
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

    def _wants_control(self, t: float, bm: BuffManager) -> tuple[str, int] | None:
        """지금 이 니케를 조작하고 싶은가 — **부작용 없이** 묻는다. 조율 단계 전용.
        `(요청 종류, 등급)` 또는 None.

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
            if self._wc_ammo_borrowed:
                # 시한부 연사 모드가 duration으로 끝났다. 진입 시 덮어쓴 모드 장탄
                # (무한 장탄이면 센티널 999999)이 그대로 남아 원래 무기의 탄창으로
                # 새어 나가면 모드가 끝난 뒤에도 재장전이 사라진다.
                # 모드 종료 = 재장전 완료 상태로 본다 (유저 확인). 모더니아 `섬멸 모드`.
                self.ammo = self._full_ammo(bm, t)
                self._wc_ammo_borrowed = False

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
        is_optimal = self.weapon_type in enemy.get("optimal_range_weapons", [])

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
        for i in range(hit_count):
            # 히트마다 독립 샘플링 (SG: 10회, 기타: 1회). 기대값 모드는 판정 대신 확률을 넘긴다
            # (P_core가 1이면 판정할 게 없으므로 기대값 모드에서도 코어 히트로 남긴다)
            is_core = (P_core >= 1.0) if expected else (random.random() < P_core)
            coeff = (self.weapon["damage_coeff"] / split) if split > 1 else None
            ht = default_hit_type(
                is_core=is_core,
                core_prob=(P_core if expected else None),
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
                                      if self._wc_is_skill_damage() else {})))
            bm.notify("pellet_hit", t, self.name)
            body_ev = "squad_part_hit" if enemy.get("has_parts", False) else "squad_body_hit"
            core_frac = P_core if expected else (1.0 if is_core else 0.0)
            _notify_frac(bm, body_ev, self.name, 1.0 - core_frac,
                         lambda: bm.notify_team_hit(body_ev, t, self.name))
            _notify_frac(bm, "crit_hit", self.name, res["crit_frac"],
                         lambda: bm.notify("crit_hit", t, self.name))
            _notify_frac(bm, "core_hit", self.name, core_frac,
                         lambda: bm.notify("core_hit", t, self.name))

        # 일반 공격 명중은 충전 창 밖에서도 시전자 기준 버충값을 `(발당)`→`(대상)`으로
        # 전환한다. 구조물 명중도 같은 방아쇠지만 현재 시뮬에는 구조물 대상이 없다.
        if not self._wc_is_skill_damage():
            bm.mark_normal_attack_landed(self.name)

        # 버스트 게이지: 히트 수만큼. 오토 무기라 풀차지 배율이 걸릴 자리가 없다.
        gauge_buffs = bm.get_buffs(self.name, "__enemy__", t)
        bm.add_burst_gauge(self._burst_gain(gauge_buffs, hit_count), t, self.name, "weapon")

        # hit_count: 발사 1회당 1회 (펠릿 수와 무관). pellet_hit은 루프 내 펠릿마다 발생
        bm.notify("hit_count", t, self.name)
        bm.notify("on_attack", t, self.name)
        if not self._wc_is_skill_damage():
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
        is_optimal = self.weapon_type in enemy.get("optimal_range_weapons", [])
        if is_full:
            self._last_full_charge_t = t
            self._force_full_charge = False
            bm.notify("full_charge", t, self.name)
        buffs = bm.get_buffs(self.name, "__enemy__", t)
        buffs["is_element_match"] = self.element_match(bm)
        if enemy.get("core_px", 0) > 0:
            P_core = _core_hit_prob(
                self._current_spread(buffs),
                enemy.get("core_px", 50),
            )
        else:
            P_core = 0.0
        expected = cfg.get("rng_mode") == "expected"
        # P_core가 1이면 판정할 게 없으므로 기대값 모드에서도 코어 히트로 남긴다
        is_core = (P_core >= 1.0) if expected else (random.random() < P_core)

        debug_char = cfg.get("_debug_char")
        in_debug_window = (
            debug_char == self.name
            and cfg.get("_debug_t0", -1.0) <= t <= cfg.get("_debug_t1", -1.0)
        )

        is_full_burst = bm.state.get("full_burst", False)
        ht = default_hit_type(
            is_core=is_core,
            core_prob=(P_core if expected else None),
            is_full_burst=is_full_burst,
            is_optimal_range=is_optimal,
            is_normal_atk=not self._wc_is_skill_damage(),
            is_weapon_mode_skill=self._wc_is_skill_damage(),
            is_full_charge=is_full,
            is_pierce_damage=bool(buffs.get("pierce_enabled")),
            is_armor_break_damage=bool(buffs.get("armor_break_enabled")),
            is_projectile_explosion=(self.base_weapon_type == "RL"),
            _debug_factors=in_debug_window,
        )
        if in_debug_window:
            print(f"t={t:.3f}s  base_atk={self.base_atk:,}  enemy_def={enemy.get('def', 31784):,}")
        res = calc_damage(
            base_atk=self.base_atk, buffs=buffs, weapon=self.weapon,
            hit_type=ht, enemy_def=enemy.get("def", 31784),
            expected=expected,
        )
        if in_debug_window:
            print()
        if is_full:
            tag = "core+full_charge_hit" if is_core else "full_charge_hit"
        else:
            # 논차지 샷은 일반 발사와 같은 취급 (차지 배율 없음)
            tag = "core" if is_core else "normal"
        events.append(HitEvent(t=t, caster=self.name, damage=res["damage"],
                               is_crit=res["is_crit"], hit_tag=tag,
                               **({"skill_name": self._wc_name}
                                  if self._wc_is_skill_damage() else {})))
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
        bm.notify("hit_count", t, self.name)
        if is_full:
            bm.notify("full_charge_hit", t, self.name)
        # 일반 공격 명중이면 충전 창·풀차지·피격 대상 종류와 무관하게 시전자 기준값을
        # 갱신한다. weapon_change 스킬 대미지는 일반 공격이 아니므로 제외한다.
        if not self._wc_is_skill_damage():
            bm.mark_normal_attack_landed(self.name)
        # 버스트 게이지. **풀차지 배율은 카메라가 이 니케를 보고 있을 때만 붙는다** —
        # 2024-04-25 "SR, RL 니케를 바라보고 있을 경우 차지 시간에 따라 버스트 게이지를
        # 추가로 획득"이 이것이다. 루주 1인 스쿼드 실측이 카메라 有 7발 / 無 18발로
        # 갈리는 것이 근거다(docs/mechanics/버스트 게이지.md).
        # 차지 무기도 총구가 2개면 그만큼 히트가 는다(펠릿은 SG뿐이라 여기선 1).
        gauge_buffs = bm.get_buffs(self.name, "__enemy__", t)
        bm.add_burst_gauge(
            self._burst_gain(gauge_buffs, self.pellets * self.muzzles,
                             full_charge=(is_full and self.name in bm.state["camera"])),
            t, self.name,
            "weapon:full_charge" if is_full else "weapon")
        body_ev = "squad_part_hit" if enemy.get("has_parts", False) else "squad_body_hit"
        core_frac = P_core if expected else (1.0 if is_core else 0.0)
        _notify_frac(bm, body_ev, self.name, 1.0 - core_frac,
                     lambda: bm.notify_team_hit(body_ev, t, self.name))
        bm.notify("on_attack", t, self.name)
        if not self._wc_is_skill_damage():
            bm.consume_bullet_buffs(self.name, t)
        _notify_frac(bm, "crit_hit", self.name, res["crit_frac"],
                     lambda: bm.notify("crit_hit", t, self.name))
        _notify_frac(bm, "core_hit", self.name, core_frac,
                     lambda: bm.notify("core_hit", t, self.name))
        if is_last:
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
        # 변경 무기는 CDN 레코드가 없어 ②층이 비고 무기군 기본값으로 떨어진다
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
        self.cover_during_delay  = wc_eff.get("cover_during_delay", self.cover_during_delay)
        # 변경 무기는 CDN에 레코드 자체가 없다 — 원래 무기의 주기 하한을 물려주지 않는다.
        self._min_fire_cycle     = 0.0

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
            if wc_fire_mode in ("auto", "auto_warmup"):
                # 마지막 발과 같은 tick에 잡힌 변경 무기 재장전 예약은 무효
                # (변경 무기는 재장전하지 않는다 — 장탄 소진이 곧 모드 종료)
                self.reloading_until = -1.0
                self.next_fire_time = t
            self.ammo = orig_ammo if orig_ammo is not None else self.weapon["max_ammo"]
            self._wc_ammo_borrowed = False   # 여기서 이미 원복했다 (tick의 만료 처리와 중복 금지)
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
            if kind == "cover":
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
        if wc_eff is not None:
            wc_max = wc_eff.get("max_ammo", -1)
            if wc_max != -1:
                return int(wc_max)
        buffs = bm.get_buffs(self.name, "__enemy__", t)
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
            if bm.is_stunned(name):
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
                events.append(HitEvent(
                    t=t, caster=name, damage=res["damage"],
                    is_crit=res["is_crit"], hit_tag="bonus_damage",
                    skill_name=eff.get("name", "버스트 스킬"),
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

        is_reenter = self._phase.startswith("reenter:")
        event_label = f"reenter:{stage} 사용" if is_reenter else f"stage:{stage} 사용"
        if self._log is not None:
            self._log.burst_log.append(BurstLogEntry(t=t, event=event_label, caster=name))

        # 3단계 버스트 발동자를 기록 (fullburst_duration 귀속용)
        if stage == "3":
            self._fb_caster = name

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
        # `[현재 체력 N% ▼]`은 *현재* 체력의 N%다 — 최대 체력 기준 정액이 아니다.
        # 곱연산이라 체력은 0에 수렴할 뿐 0이 되지 않는다 (GAMEPLAY.md §값 산정).
        target_names = _resolve_targets(eff, caster)
        hp = bm.state["hp"]
        for name in target_names:
            base_hp = bm.state["base_stats"].get(name, {}).get("hp", 0.0)
            cur = hp.get(name, base_hp)
            hp[name] = max(cur * (1.0 - val / 100.0), 0.0)
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
    bm.register_instant_handler("burst_charge_pct", handle_burst_charge_pct)
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

    cfg = {**DEFAULT_CONFIG, **(config or {})}
    enm = {**DEFAULT_ENEMY, **(enemy or {})}
    duration = cfg["duration"]

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
    }

    enemy_code = enm.get("code", "")

    char_states: dict[str, CharState] = {
        c["name"]: CharState(c, float(base_stats[c["name"]]["atk"]), enemy_code)
        for c in squad
    }
    squad_names = set(char_states)
    for cs in char_states.values():
        gated = list(cs._click_sched)
        if cs.reload_when is not None:
            gated.append(cs.reload_when)
        for spec in gated:
            gate = spec.get("gate")
            if gate is not None and gate["burst_user"] not in squad_names:
                raise ValueError(
                    f"{cs.name}: gate.burst_user {gate['burst_user']!r}가 스쿼드에 없다. "
                    f"정식 명칭을 쓴다. docs/CONTROL.md §런타임 게이트")

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
            ))
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
        gauge_src = eff_name or stat
        gauge_be = (BURST_GAUGE_EXCEPTIONS.get(caster, {})
                    .get(gauge_src, {}).get("burst_energy"))
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

        # 스쿼드 시퀀스 → 조작자(카메라) 결정 → 캐릭터. 순서의 근거는
        # docs/CONTROL.md §판정 자리 (틱 내 순서에 답이 달라지지 않게 한다).
        _pump_squad_seq(t, bm, squad, char_states)
        _arbitrate_control(t, bm, squad, char_states, cfg["_camera"])

        for char in squad:
            name = char["name"]
            for ev in char_states[name].tick(t, bm, enm, cfg):
                result.hits.append(ev)
                result.char_total[name] += ev.damage
                _apply_lifesteal(ev, bm, base_stats, t)

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
        result.hits.append(ev)
        result.char_total[ev.caster] += ev.damage
        _apply_lifesteal(ev, bm, base_stats, duration)
    _dot_events.clear()

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
