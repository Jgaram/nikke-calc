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


def _overlay_css(keys: list[str]) -> str:
    """주어진 버튼 key 목록에 대해 투명 오버레이 CSS를 생성한다."""
    rules = []
    for key in keys:
        sel = f'div[data-testid="stButton"]:has(button[kind="secondary"][data-testid="baseButton-secondary"][key="{key}"])'
        # Streamlit이 key를 DOM에 노출하지 않으므로, aria-label로 타겟팅
        # 버튼 텍스트(" ")가 고유하지 않으니 부모 컨테이너 순서 대신
        # 공통 클래스로 처리: slot_sel_*, slot_del_*, grid_* prefix 기준
    # key prefix별로 규칙 하나씩
    slot_sel = ", ".join(
        f'button[data-testid="baseButton-secondary"]:has(+ [style*="display: none"])'
        for _ in ["placeholder"]
    )
    return ""


def render() -> dict | None:
    """팀 구성 UI. 실행 버튼이 눌리면 config dict 반환, 아니면 None."""
    _init_state()
    _skills_path = os.path.join(_DATA_DIR, "parsed_skills.json")
    char_names = _load_char_names(os.path.getmtime(_skills_path))

    # CSS: slot_sel 버튼(공백 텍스트)을 이미지 위 투명 오버레이로
    # Streamlit은 버튼을 stVerticalBlock > stButton 구조로 렌더링.
    # 이미지 markdown 바로 다음 stButton이 오버레이 대상.
    # 가장 안정적인 방법: 버튼을 이미지 아래 숨기고 이미지를 label로 쓰는 건 불가.
    # → 버튼 텍스트를 비우고 부모 relative div 내에서 absolute 처리.
    # Streamlit 구조: div.stColumn > div.stVerticalBlock > div.stMarkdown + div.stButton
    # stMarkdown 다음 sibling stButton을 absolute로 올림.
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

    with st.expander("공통 설정", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            duration   = st.slider("시뮬 시간 (초)", 30, 300, 180, step=10)
            skill_lv   = st.slider("스킬 레벨", 1, 10, 10)
        with c2:
            level       = st.slider("캐릭터 레벨", 1, 400, 400, step=10)
            burst_regen = st.slider("버스트 충전 시간 (초)", 0.5, 5.0, 2.0, step=0.5)

    with st.expander("랩쳐 설정", expanded=False):
        _CODE_OPTIONS = ["없음", "전격", "수냉", "작열", "풍압", "철갑"]
        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            enemy_def = st.number_input("방어력", min_value=0, value=31784, step=100)
        with ec2:
            code_sel = st.selectbox("속성", _CODE_OPTIONS, index=0)
            enemy_code = None if code_sel == "없음" else code_sel
        with ec3:
            has_core = st.checkbox("코어 있음", value=False)

    if st.button("▶ 시뮬 실행", type="primary", use_container_width=True):
        chars = [n for n in st.session_state["team_slots"] if n is not None]
        if not chars:
            st.error("캐릭터를 1명 이상 선택하세요.")
            return None
        return {
            "chars": chars,
            "duration": duration,
            "skill_level": skill_lv,
            "level": level,
            "burst_regen_time": burst_regen,
            "enemy": {
                "def": enemy_def,
                "code": enemy_code,
                "has_core": has_core,
            },
        }
    return None


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
