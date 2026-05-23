"""팀 구성 패널 — 이미지 클릭으로 캐릭터 선택."""

from __future__ import annotations

import json
import os

import streamlit as st

from ui.image_utils import get_image_b64

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_NIKKE_PATH = os.path.join(_DATA_DIR, "parsed_nikke.json")

_SLOT_COUNT = 5
_GRID_COLS  = 10

# B1, B2, B3는 팀 구성 테스트용 가상 캐릭터로, 실제 이미지가 없다. 정상 동작임.

_CUBE_OPTIONS = ["재장", "탄충", "체력", "차속", "파츠"]
_COLLECTION_OPTIONS = ["SR0", "SR1", "SR2", "SR3", "SR4", "SR5", "SR6", "SR7",
                       "SR8", "SR9", "SR10", "SR11", "SR12", "SR13", "SR14", "SR15"]

# 기본 스탯값 (app.py _make_char 기준)
_DEFAULTS = {
    "level": 400,
    "breakthrough": 3,
    "core_enhancement": 0,
    "affinity": 30,
    "skill_lv1": 10,
    "skill_lv2": 10,
    "skill_lv3": 10,
    "cube_name": "재장",
    "cube_level": 15,
    "equip_lv_head": 5,
    "equip_lv_body": 5,
    "equip_lv_arm":  5,
    "equip_lv_leg":  5,
    "equip_atk_pct":          20,
    "equip_element_bonus":     0,
    "equip_max_ammo_pct":    120,
    "equip_crit_rate":         0,
    "equip_crit_dmg":          0,
    "equip_charge_speed_pct":  0,
    "equip_charge_dmg_pct":    0,
    "equip_accuracy_pct":      0,
    "equip_def_pct":           0,
    "console_common": 180,
    "console_class": 100,
    "console_company": 100,
    "collection_stage": "SR15",
}


@st.cache_data
def _load_burst_info(mtime: float = 0.0) -> dict[str, dict]:
    """캐릭터명 → {stage: str, cooldown: float} 매핑."""
    with open(_NIKKE_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return {
        k: {
            "stage": str(v.get("burst_stage", "")),
            "cooldown": float(v.get("burst_cooldown", 40.0)),
        }
        for k, v in d.items()
    }


@st.cache_data
def _load_char_names(mtime: float = 0.0) -> list[str]:
    with open(os.path.join(_DATA_DIR, "parsed_skills.json"), encoding="utf-8") as f:
        d = json.load(f)
    return sorted(d.keys())


def _init_state() -> None:
    if "team_slots" not in st.session_state:
        st.session_state["team_slots"] = [None] * _SLOT_COUNT
    if "active_slot" not in st.session_state:
        st.session_state["active_slot"] = 0
    if "same_stats" not in st.session_state:
        st.session_state["same_stats"] = True
    if "burst_max_count" not in st.session_state:
        st.session_state["burst_max_count"] = 14
    if "burst_sequence" not in st.session_state:
        st.session_state["burst_sequence"] = []
    if "no_burst_char" not in st.session_state:
        st.session_state["no_burst_char"] = "없음"
    if "first_burst_time" not in st.session_state:
        st.session_state["first_burst_time"] = 3.0


def render() -> dict | None:
    """팀 구성 UI. 실행 버튼이 눌리면 config dict 반환, 아니면 None."""
    _init_state()

    # 이전 리런에서 시뮬 버튼이 눌린 경우: 저장된 config를 즉시 반환 (그리드 렌더링 스킵)
    if st.session_state.get("_simulating"):
        st.session_state.pop("_simulating")
        return st.session_state.pop("_pending_config", None)

    _skills_path = os.path.join(_DATA_DIR, "parsed_skills.json")
    _nikke_mtime = os.path.getmtime(_NIKKE_PATH) if os.path.exists(_NIKKE_PATH) else 0.0
    burst_info = _load_burst_info(_nikke_mtime)
    char_names = _load_char_names(os.path.getmtime(_skills_path))

    st.markdown("""
<style>
/* 팀 슬롯 / 그리드: 이미지 div + 바로 다음 stButton을 묶는 relative 컨테이너 */
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stMarkdown"] + div[data-testid="stButton"] > button[kind="secondary"]) {
    position: relative;
}
/* 이미지 바로 다음 secondary 버튼 → 투명 오버레이 */
div[data-testid="stVerticalBlock"] > div[data-testid="stMarkdown"] + div[data-testid="stButton"] > button[kind="secondary"] {
    position: absolute;
    inset: 0;
    width: 100%;
    height: calc(100% - 28px);  /* 이름 텍스트 영역 제외 */
    background: transparent !important;
    border: none !important;
    color: transparent !important;
    cursor: pointer;
    z-index: 10;
    top: 0;
    padding: 0;
}
div[data-testid="stVerticalBlock"] > div[data-testid="stMarkdown"] + div[data-testid="stButton"] > button[kind="secondary"]:hover {
    background: rgba(255,255,255,0.07) !important;
}
</style>
""", unsafe_allow_html=True)

    _render_team_slots()

    st.divider()

    active = st.session_state["active_slot"]
    st.markdown(f"**슬롯 {active + 1}** 에 추가할 캐릭터를 클릭하세요")

    _render_char_grid(char_names)

    st.divider()

    # ── 스탯 설정 ─────────────────────────────────────────────────────────
    with st.expander("스탯 설정", expanded=False):
        same_stats = st.checkbox(
            "모두 동일 스탯 적용",
            value=st.session_state["same_stats"],
            key="same_stats_checkbox",
        )
        st.session_state["same_stats"] = same_stats

        if same_stats:
            common_stat = _render_stat_form("common")
            char_stats = [common_stat] * _SLOT_COUNT
        else:
            slots = st.session_state["team_slots"]
            char_stats = []
            stat_cols = st.columns(_SLOT_COUNT)
            for i, col in enumerate(stat_cols):
                name = slots[i]
                label = name if name else f"슬롯 {i+1}"
                with col:
                    with st.expander(label, expanded=False):
                        if name:
                            s = _render_stat_form(f"slot_{i}")
                        else:
                            st.caption("캐릭터 없음")
                            s = _stat_defaults()
                    char_stats.append(s)

    # ── 랩쳐 설정 ─────────────────────────────────────────────────────────
    _WEAPON_ORDER = ["SG", "SMG", "AR", "MG", "SR"]
    with st.expander("랩쳐 설정", expanded=False):
        _CODE_LABELS = {
            "없음": "없음",
            "전격": "전격 (약점: 철갑)",
            "수냉": "수냉 (약점: 전격)",
            "작열": "작열 (약점: 수냉)",
            "풍압": "풍압 (약점: 작열)",
            "철갑": "철갑 (약점: 풍압)",
        }
        enemy_def  = st.number_input("랩쳐 방어력", min_value=0, value=31784, step=100)
        code_label = st.selectbox("랩쳐 속성", list(_CODE_LABELS.values()), index=0)
        has_core   = st.checkbox("코어 있음", value=False)
        _LABEL_TO_CODE = {v: k for k, v in _CODE_LABELS.items()}
        enemy_code = None if code_label == "없음" else _LABEL_TO_CODE[code_label]

        use_optimal = st.checkbox("적정거리 설정", value=False)
        if use_optimal:
            opt_min, opt_max = st.select_slider(
                "적정거리 무기군 범위  ← 근거리 · 원거리 →",
                options=_WEAPON_ORDER,
                value=("SG", "SR"),
            )
            i_min = _WEAPON_ORDER.index(opt_min)
            i_max = _WEAPON_ORDER.index(opt_max)
            optimal_range_weapons = _WEAPON_ORDER[i_min : i_max + 1]
            st.caption(f"적용: {' · '.join(optimal_range_weapons)}")
        else:
            optimal_range_weapons = []

    # ── 전투 시간 및 버스트 설정 ──────────────────────────────────────────
    active_team = [n for n in st.session_state["team_slots"] if n is not None]
    burst_regen, burst_use_delay, burst_max_count, burst_sequence, no_burst_char, duration, first_burst_time = _render_burst_settings(active_team, burst_info)

    if st.button("▶ 시뮬 실행", type="primary", use_container_width=True):
        chars = [n for n in st.session_state["team_slots"] if n is not None]
        if not chars:
            st.error("캐릭터를 1명 이상 선택하세요.")
            return None

        # 버스트 시퀀스 유효성 검사 (일반 슬롯 기준: stage별 최소 1명)
        if burst_sequence is not None:
            for idx, entry in enumerate(burst_sequence):
                for stage in ("1", "2", "3"):
                    if not entry.get(stage):
                        label = {"1": "B1→2", "2": "B2→3", "3": "B3"}[stage]
                        st.error(f"버스트 {idx + 1}회차 {label}(일반 슬롯)에 캐릭터를 지정하세요.")
                        return None

        slots = st.session_state["team_slots"]
        char_configs = []
        for i, name in enumerate(slots):
            if name is None:
                continue
            s = char_stats[i]
            char_configs.append({
                "name": name,
                "stat": s,
                "burst_regen_time": burst_regen,
            })

        st.session_state["_pending_config"] = {
            "char_configs": char_configs,
            "duration": duration,
            "burst_regen_time": burst_regen,
            "burst_use_delay": burst_use_delay,
            "first_burst_time": first_burst_time,
            "enemy": {
                "def": enemy_def,
                "code": enemy_code,
                "has_core": has_core,
                "optimal_range_weapons": optimal_range_weapons,
            },
            "max_burst_count": burst_max_count,
            "burst_sequence": burst_sequence,
            "no_burst_char": no_burst_char,
        }
        st.session_state["_simulating"] = True
        st.rerun()
    return None


def _stat_defaults() -> dict:
    return dict(_DEFAULTS)


def _render_stat_form(key_prefix: str) -> dict:
    """스탯 입력 폼을 렌더링하고 dict 반환."""
    d = _DEFAULTS
    c1, c2 = st.columns(2)
    with c1:
        level      = st.number_input("레벨", 1, 1000, d["level"], key=f"{key_prefix}_level")
        breakthrough = st.slider("한계 돌파", 0, 3, d["breakthrough"], key=f"{key_prefix}_breakthrough")
        core_enh   = st.slider("코어 강화", 0, 10, d["core_enhancement"], key=f"{key_prefix}_core")
        affinity   = st.slider("호감도", 0, 40, d["affinity"], key=f"{key_prefix}_affinity")
    with c2:
        cube_name  = st.selectbox("큐브", _CUBE_OPTIONS,
                                   index=_CUBE_OPTIONS.index(d["cube_name"]),
                                   key=f"{key_prefix}_cube_name")
        cube_level = st.slider("큐브 레벨", 1, 15, d["cube_level"], key=f"{key_prefix}_cube_level")
        collection = st.selectbox("소장품 단계", _COLLECTION_OPTIONS,
                                   index=_COLLECTION_OPTIONS.index(d["collection_stage"]),
                                   key=f"{key_prefix}_collection")

    st.markdown("**스킬 레벨**")
    sk1, sk2, sk3 = st.columns(3)
    with sk1:
        skill_lv1 = st.slider("1스킬", 1, 10, d["skill_lv1"], key=f"{key_prefix}_skill_lv1")
    with sk2:
        skill_lv2 = st.slider("2스킬", 1, 10, d["skill_lv2"], key=f"{key_prefix}_skill_lv2")
    with sk3:
        skill_lv3 = st.slider("3스킬", 1, 10, d["skill_lv3"], key=f"{key_prefix}_skill_lv3")

    st.markdown("**장비 레벨**")
    el1, el2, el3, el4 = st.columns(4)
    with el1:
        equip_lv_head = st.slider("머리", 0, 5, d["equip_lv_head"], key=f"{key_prefix}_equip_lv_head")
    with el2:
        equip_lv_body = st.slider("몸통", 0, 5, d["equip_lv_body"], key=f"{key_prefix}_equip_lv_body")
    with el3:
        equip_lv_arm  = st.slider("팔",   0, 5, d["equip_lv_arm"],  key=f"{key_prefix}_equip_lv_arm")
    with el4:
        equip_lv_leg  = st.slider("다리", 0, 5, d["equip_lv_leg"],  key=f"{key_prefix}_equip_lv_leg")

    st.markdown("**장비 스킬**")
    eq1, eq2, eq3 = st.columns(3)
    with eq1:
        eq_atk_pct         = st.number_input("공격력 %",      0, 500, d["equip_atk_pct"],          key=f"{key_prefix}_eq_atk_pct")
        eq_crit_rate       = st.number_input("크리 확률 %",   0, 500, d["equip_crit_rate"],         key=f"{key_prefix}_eq_crit_rate")
        eq_charge_spd      = st.number_input("차지 속도 %",   0, 500, d["equip_charge_speed_pct"],  key=f"{key_prefix}_eq_charge_spd")
    with eq2:
        eq_max_ammo        = st.number_input("최대 장탄 %",   0, 500, d["equip_max_ammo_pct"],      key=f"{key_prefix}_eq_max_ammo")
        eq_crit_dmg        = st.number_input("크리 대미지 %", 0, 500, d["equip_crit_dmg"],          key=f"{key_prefix}_eq_crit_dmg")
        eq_charge_dmg      = st.number_input("차지 대미지 %", 0, 500, d["equip_charge_dmg_pct"],    key=f"{key_prefix}_eq_charge_dmg")
    with eq3:
        eq_element_bonus   = st.number_input("우월 코드 %",   0, 500, d["equip_element_bonus"],     key=f"{key_prefix}_eq_element_bonus")
        eq_accuracy        = st.number_input("명중률 %",      0, 500, d["equip_accuracy_pct"],      key=f"{key_prefix}_eq_accuracy")
        eq_def_pct         = st.number_input("방어력 %",      0, 500, d["equip_def_pct"],           key=f"{key_prefix}_eq_def_pct")

    st.markdown("**콘솔**")
    co1, co2, co3 = st.columns(3)
    with co1:
        con_common  = st.number_input("공통", 0, 999, d["console_common"], step=10, key=f"{key_prefix}_con_common")
    with co2:
        con_class   = st.number_input("역할군", 0, 999, d["console_class"], step=10, key=f"{key_prefix}_con_class")
    with co3:
        con_company = st.number_input("기업", 0, 999, d["console_company"], step=10, key=f"{key_prefix}_con_company")

    return {
        "level": level,
        "skill_lv1": skill_lv1,
        "skill_lv2": skill_lv2,
        "skill_lv3": skill_lv3,
        "breakthrough": breakthrough,
        "core_enhancement": core_enh,
        "affinity": affinity,
        "cube_name": cube_name,
        "cube_level": cube_level,
        "equip_lv_head": equip_lv_head,
        "equip_lv_body": equip_lv_body,
        "equip_lv_arm":  equip_lv_arm,
        "equip_lv_leg":  equip_lv_leg,
        "equip_atk_pct":          eq_atk_pct,
        "equip_element_bonus":    eq_element_bonus,
        "equip_max_ammo_pct":     eq_max_ammo,
        "equip_crit_rate":        eq_crit_rate,
        "equip_crit_dmg":         eq_crit_dmg,
        "equip_charge_speed_pct": eq_charge_spd,
        "equip_charge_dmg_pct":   eq_charge_dmg,
        "equip_accuracy_pct":     eq_accuracy,
        "equip_def_pct":          eq_def_pct,
        "console_common": con_common,
        "console_class": con_class,
        "console_company": con_company,
        "collection_stage": collection,
    }


# ── 팀 슬롯 렌더링 ────────────────────────────────────────────────────────


def _img_html(img: str | None, pct: int, name: str, border: str = "") -> str:
    border_style = f"border:2px solid {border};border-radius:8px;overflow:hidden;" if border else ""
    inner = (
        f'<img src="{img}" style="position:absolute;top:0;left:0;width:100%;'
        f'height:100%;object-fit:cover;object-position:top;">'
        if img else
        f'<div style="position:absolute;inset:0;display:flex;align-items:center;'
        f'justify-content:center;font-size:11px;color:#888;">{name}</div>'
    )
    return (
        f'<div style="{border_style}">'
        f'<div style="width:100%;padding-top:{pct}%;position:relative;overflow:hidden;">'
        f'{inner}</div></div>'
    )


def _render_team_slots() -> None:
    slots = st.session_state["team_slots"]
    active = st.session_state["active_slot"]
    outer = st.columns([3, 1, 1, 1, 1, 1, 3])
    cols = outer[1:6]

    for i, col in enumerate(cols):
        name = slots[i]
        is_active = (i == active)
        border_color = "#FF4B4B" if is_active else "#555"

        with col:
            if name:
                img = get_image_b64(name)
                st.markdown(
                    f'<div style="border:2px solid {border_color};border-radius:8px;overflow:hidden;">'
                    f'<div style="width:100%;padding-top:130%;position:relative;overflow:hidden;">'
                    + (f'<img src="{img}" style="position:absolute;top:0;left:0;width:100%;'
                       f'height:100%;object-fit:cover;object-position:top;">' if img else
                       f'<div style="position:absolute;inset:0;display:flex;align-items:center;'
                       f'justify-content:center;font-size:11px;color:#888;">{name}</div>')
                    + f'</div>'
                    f'<div style="font-size:11px;padding:2px 4px;background:#111;color:#eee;'
                    f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("✕", key=f"slot_del_{i}", use_container_width=True):
                    st.session_state["team_slots"][i] = None
                    st.session_state["active_slot"] = i
                    st.rerun()
            else:
                st.markdown(
                    f'<div style="border:2px dashed {border_color};border-radius:8px;'
                    f'padding-top:130%;position:relative;">'
                    f'<div style="position:absolute;top:50%;left:0;width:100%;'
                    f'transform:translateY(-50%);text-align:center;color:#888;font-size:13px;">'
                    f'슬롯 {i+1}</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("선택", key=f"slot_sel_{i}", use_container_width=True):
                    st.session_state["active_slot"] = i
                    st.rerun()


# ── 캐릭터 그리드 렌더링 ──────────────────────────────────────────────────


def _render_char_grid(char_names: list[str]) -> None:
    slots = st.session_state["team_slots"]
    active = st.session_state["active_slot"]

    rows = [char_names[i:i+_GRID_COLS] for i in range(0, len(char_names), _GRID_COLS)]

    for row in rows:
        cols = st.columns(_GRID_COLS)
        for j, name in enumerate(row):
            is_selected = name in slots
            opacity = "0.35" if is_selected else "1.0"
            with cols[j]:
                img = get_image_b64(name)
                st.markdown(
                    f'<div style="opacity:{opacity};">'
                    + (
                        f'<div style="width:100%;padding-top:130%;position:relative;'
                        f'overflow:hidden;border-radius:4px;">'
                        f'<img src="{img}" style="position:absolute;top:0;left:0;width:100%;'
                        f'height:100%;object-fit:cover;object-position:top;"></div>'
                        if img else
                        f'<div style="padding-top:130%;position:relative;border-radius:4px;background:#222;">'
                        f'<div style="position:absolute;inset:0;display:flex;align-items:center;'
                        f'justify-content:center;font-size:9px;color:#888;">{name}</div></div>'
                    )
                    + '</div>',
                    unsafe_allow_html=True,
                )
                if is_selected:
                    if st.button("취소", key=f"grid_{name}", use_container_width=True):
                        idx = slots.index(name)
                        st.session_state["team_slots"][idx] = None
                        st.session_state["active_slot"] = idx
                        st.rerun()
                else:
                    if st.button("선택", key=f"grid_{name}", use_container_width=True):
                        st.session_state["team_slots"][active] = name
                        for k in range(_SLOT_COUNT):
                            if st.session_state["team_slots"][k] is None:
                                st.session_state["active_slot"] = k
                                break
                        st.rerun()


# ── 버스트 설정 UI ────────────────────────────────────────────────────────


def _autofill_burst_sequence(
    max_count: int,
    stage_chars: dict[str, list[str]],
    burst_info: dict[str, dict],
    burst_regen: float,
) -> list[dict[str, list[str]]]:
    """
    쿨타임 기반 버스트 순서 자동 생성.

    NIKKE 표준 사이클 20s 기준 (20s CD → 매 버스트, 40s CD → 2번에 1번).
    재진입 슬롯(B1→1, B2→2)은 자동 채우기에서 비워 둔다.
    """
    none_label = "—"
    cycle_interval = 20.0  # NIKKE 표준 버스트 사이클
    last_used: dict[str, float] = {}
    result: list[dict] = []

    for i in range(max_count):
        cycle_t = i * cycle_interval
        entry: dict[str, list[str]] = {}
        for stage in ("1", "2", "3"):
            chars = stage_chars.get(stage, [])
            available = [
                c for c in chars
                if cycle_t >= last_used.get(c, -9999.0) + burst_info.get(c, {}).get("cooldown", 40.0) - 1e-9
            ]
            if stage == "3":
                # B3: 단일 슬롯
                assigned = available[0] if available else none_label
                entry[stage] = [assigned]
                if assigned != none_label:
                    last_used[assigned] = cycle_t
            else:
                # slot 0 (B1→1 / B2→2) = 재진입 슬롯: 항상 비움
                # slot 1 (B1→2 / B2→3) = 일반 슬롯: 사용 가능 캐릭터 배정
                assigned = available[0] if available else none_label
                entry[stage] = [none_label, assigned]
                if assigned != none_label:
                    last_used[assigned] = cycle_t
        result.append(entry)
    return result


def _render_burst_settings(
    active_team: list[str],
    burst_info: dict[str, dict],
) -> tuple[float, float, int | None, list[dict] | None, str | None, float, float]:
    """
    전투 시간 및 버스트 설정 UI.
    반환: (burst_regen, burst_use_delay, max_burst_count, burst_sequence, no_burst_char, duration, first_burst_time)
    """
    none_label = "—"
    _NO_BURST_NONE = "없음"

    with st.expander("전투 시간 및 버스트 설정", expanded=False):
        tc1, tc2 = st.columns(2)
        with tc1:
            duration = st.slider("시뮬 시간 (초)", 30, 180, 180, step=10)
        with tc2:
            first_burst_time = st.slider(
                "첫 버스트 사용 시간 (초)",
                0.0, 5.0,
                st.session_state["first_burst_time"],
                step=0.1,
                key="first_burst_time_slider",
            )
            st.session_state["first_burst_time"] = first_burst_time

        bc1, bc2 = st.columns(2)
        with bc1:
            burst_regen     = st.slider("버스트 충전 시간 (초)", 0.0, 5.0, 2.0, step=0.1)
        with bc2:
            burst_use_delay = st.slider("버스트 사용 딜레이 (초)", 0.0, 0.5, 0.1, step=0.02)

        use_max = st.checkbox(
            "최대 버스트 횟수 지정",
            value=st.session_state["burst_max_count"] > 0,
            key="burst_use_max",
        )

        if not use_max:
            st.session_state["burst_max_count"] = 0
            st.session_state["burst_sequence"] = []
            return burst_regen, burst_use_delay, None, None, None, duration, first_burst_time

        max_count = int(st.number_input(
            "최대 버스트 횟수",
            min_value=1,
            max_value=100,
            value=max(1, st.session_state["burst_max_count"]),
            step=1,
            key="burst_max_count_input",
        ))
        st.session_state["burst_max_count"] = max_count

        use_order = st.checkbox(
            "버스트 사용 순서 직접 지정",
            value=len(st.session_state["burst_sequence"]) > 0,
            key="burst_use_order",
        )

        if not use_order:
            st.session_state["burst_sequence"] = []
            no_burst_opts = [_NO_BURST_NONE] + active_team
            prev_nbc = st.session_state.get("no_burst_char", _NO_BURST_NONE)
            if prev_nbc not in no_burst_opts:
                prev_nbc = _NO_BURST_NONE
            no_burst_sel = st.selectbox(
                "버스트 미사용 캐릭터",
                no_burst_opts,
                index=no_burst_opts.index(prev_nbc),
                key="no_burst_char_sel",
            )
            st.session_state["no_burst_char"] = no_burst_sel
            no_burst_char = None if no_burst_sel == _NO_BURST_NONE else no_burst_sel
            return burst_regen, burst_use_delay, max_count, None, no_burst_char, duration, first_burst_time

        # 단계별 후보 캐릭터 목록 (팀 내, 입력 순서 유지)
        stage_chars: dict[str, list[str]] = {"1": [], "2": [], "3": []}
        for name in active_team:
            s = burst_info.get(name, {}).get("stage", "")
            if s in stage_chars:
                stage_chars[s].append(name)

        opts: dict[str, list[str]] = {
            s: [none_label] + stage_chars[s] for s in ("1", "2", "3")
        }

        # 자동 채우기 / 모두 지우기 버튼
        btn_col1, btn_col2, _ = st.columns([1, 1, 4])
        if btn_col1.button("자동 채우기", key="burst_autofill"):
            filled = _autofill_burst_sequence(max_count, stage_chars, burst_info, burst_regen)
            st.session_state["burst_sequence"] = filled
            for i, entry in enumerate(filled):
                for stage, max_slots in (("1", 2), ("2", 2), ("3", 1)):
                    chars = entry.get(stage, [])
                    for slot in range(max_slots):
                        val = chars[slot] if slot < len(chars) else none_label
                        st.session_state[f"bseq_{i}_{stage}_{slot}"] = val
            st.rerun()
        if btn_col2.button("모두 지우기", key="burst_clear"):
            st.session_state["burst_sequence"] = []
            for i in range(max_count):
                for stage, max_slots in (("1", 2), ("2", 2), ("3", 1)):
                    for slot in range(max_slots):
                        st.session_state[f"bseq_{i}_{stage}_{slot}"] = none_label
            st.rerun()

        # 기존 시퀀스를 max_count 길이에 맞게 조정
        prev_seq: list[dict] = list(st.session_state["burst_sequence"])
        while len(prev_seq) < max_count:
            prev_seq.append({"1": [], "2": [], "3": []})
        prev_seq = prev_seq[:max_count]

        st.markdown(
            "<div style='font-size:12px;color:#888;margin-bottom:4px;'>"
            "B1→1·B2→2 = 재진입 슬롯 (해당 버스트 스킬 사용 후 같은 단계 유지) &nbsp;·&nbsp; "
            "B1→2·B2→3·B3 = 일반 슬롯 (다음 단계로 진행) &nbsp;·&nbsp; "
            "각 단계에 일반 슬롯 1명 이상 필요 &nbsp;·&nbsp; "
            "<span style='color:#f0a500;'>⚠ 자동 채우기는 재진입을 반영하지 않습니다</span>"
            "</div>",
            unsafe_allow_html=True,
        )

        hcols = st.columns([0.5, 1, 1, 1, 1, 1])
        for col, label in zip(hcols, ["**#**", "**B1→1**", "**B1→2**", "**B2→2**", "**B2→3**", "**B3**"]):
            col.markdown(label)

        new_seq: list[dict] = []
        for i in range(max_count):
            entry = prev_seq[i]
            row = st.columns([0.5, 1, 1, 1, 1, 1])
            row[0].markdown(f"**{i + 1}**")

            def _sel(col, stage: str, slot: int, _entry=entry, _i=i) -> str:
                chars = _entry.get(stage, [])
                cur = chars[slot] if slot < len(chars) else ""
                opt_list = opts[stage]
                cur_idx = opt_list.index(cur) if cur in opt_list else 0
                return col.selectbox(
                    f"burst_{_i}_{stage}_{slot}",
                    opt_list,
                    index=cur_idx,
                    key=f"bseq_{_i}_{stage}_{slot}",
                    label_visibility="collapsed",
                )

            b1a = _sel(row[1], "1", 0)
            b1b = _sel(row[2], "1", 1)
            b2a = _sel(row[3], "2", 0)
            b2b = _sel(row[4], "2", 1)
            b3a = _sel(row[5], "3", 0)

            new_seq.append({
                "1": [n for n in (b1a, b1b) if n != none_label],
                "2": [n for n in (b2a, b2b) if n != none_label],
                "3": [n for n in (b3a,) if n != none_label],
            })

        st.session_state["burst_sequence"] = new_seq
        return burst_regen, burst_use_delay, max_count, new_seq, None, duration, first_burst_time  # 직접 설정 시 no_burst_char 무효
