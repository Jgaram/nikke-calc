"""팀 구성 패널 — 이미지 클릭으로 캐릭터 선택."""

from __future__ import annotations

import json
import os

import streamlit as st

from ui.image_utils import get_image_b64

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

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
def _load_char_names(_mtime: float = 0.0) -> list[str]:
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


def render() -> dict | None:
    """팀 구성 UI. 실행 버튼이 눌리면 config dict 반환, 아니면 None."""
    _init_state()
    _skills_path = os.path.join(_DATA_DIR, "parsed_skills.json")
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

    st.markdown("### 팀 구성")
    _render_team_slots()

    st.divider()

    active = st.session_state["active_slot"]
    st.markdown(f"**슬롯 {active + 1}** 에 추가할 캐릭터를 클릭하세요")

    _render_char_grid(char_names)

    st.divider()

    # ── 스탯 설정 ─────────────────────────────────────────────────────────
    same_stats = st.checkbox(
        "모두 동일 스탯 적용",
        value=st.session_state["same_stats"],
        key="same_stats_checkbox",
    )
    st.session_state["same_stats"] = same_stats

    if same_stats:
        with st.expander("공통 스탯 설정", expanded=False):
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

    # ── 시뮬 설정 ─────────────────────────────────────────────────────────
    with st.expander("시뮬·랩쳐 설정", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            duration    = st.slider("시뮬 시간 (초)", 30, 300, 180, step=10)
            burst_regen = st.slider("버스트 충전 시간 (초)", 0.5, 5.0, 2.0, step=0.5)
        with c2:
            _CODE_OPTIONS = ["없음", "전격", "수냉", "작열", "풍압", "철갑"]
            enemy_def  = st.number_input("랩쳐 방어력", min_value=0, value=31784, step=100)
            code_sel   = st.selectbox("랩쳐 속성", _CODE_OPTIONS, index=0)
            has_core   = st.checkbox("코어 있음", value=False)
            enemy_code = None if code_sel == "없음" else code_sel

    if st.button("▶ 시뮬 실행", type="primary", use_container_width=True):
        chars = [n for n in st.session_state["team_slots"] if n is not None]
        if not chars:
            st.error("캐릭터를 1명 이상 선택하세요.")
            return None

        slots = st.session_state["team_slots"]
        char_configs = []
        stat_idx = 0
        for i, name in enumerate(slots):
            if name is None:
                continue
            s = char_stats[i]
            char_configs.append({
                "name": name,
                "stat": s,
                "burst_regen_time": burst_regen,
            })
            stat_idx += 1

        return {
            "char_configs": char_configs,
            "duration": duration,
            "burst_regen_time": burst_regen,
            "enemy": {
                "def": enemy_def,
                "code": enemy_code,
                "has_core": has_core,
            },
        }
    return None


def _stat_defaults() -> dict:
    return dict(_DEFAULTS)


def _render_stat_form(key_prefix: str) -> dict:
    """스탯 입력 폼을 렌더링하고 dict 반환."""
    d = _DEFAULTS
    c1, c2 = st.columns(2)
    with c1:
        level      = st.number_input("레벨", 1, 400, d["level"], key=f"{key_prefix}_level")
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
        con_common  = st.number_input("공통", 0, 200, d["console_common"], step=10, key=f"{key_prefix}_con_common")
    with co2:
        con_class   = st.number_input("역할군", 0, 160, d["console_class"], step=10, key=f"{key_prefix}_con_class")
    with co3:
        con_company = st.number_input("기업", 0, 160, d["console_company"], step=10, key=f"{key_prefix}_con_company")

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
