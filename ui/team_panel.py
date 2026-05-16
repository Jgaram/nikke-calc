"""팀 구성 패널 — 이미지 그리드 클릭으로 캐릭터 선택."""

from __future__ import annotations

import json
import os

import streamlit as st

from ui.image_utils import get_image_b64

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

_SLOT_COUNT = 5
_GRID_COLS  = 8   # 캐릭터 그리드 한 행의 열 수


@st.cache_data
def _load_char_names() -> list[str]:
    with open(os.path.join(_DATA_DIR, "parsed_skills.json"), encoding="utf-8") as f:
        d = json.load(f)
    return sorted(d.keys())


def _init_state() -> None:
    if "team_slots" not in st.session_state:
        st.session_state["team_slots"] = [None] * _SLOT_COUNT  # None = 빈 슬롯
    if "active_slot" not in st.session_state:
        st.session_state["active_slot"] = 0  # 현재 편집 중인 슬롯 인덱스


def render() -> dict | None:
    """팀 구성 UI. 실행 버튼이 눌리면 config dict 반환, 아니면 None."""
    _init_state()
    char_names = _load_char_names()

    # ── 섹션 1: 선택된 팀 슬롯 ──────────────────────────────────────────────
    st.markdown("### 팀 구성")
    _render_team_slots()

    st.divider()

    # ── 섹션 2: 캐릭터 그리드 ───────────────────────────────────────────────
    active = st.session_state["active_slot"]
    st.markdown(f"**슬롯 {active + 1}** 에 추가할 캐릭터를 클릭하세요")

    _render_char_grid(char_names)

    st.divider()

    # ── 섹션 3: 설정 + 실행 ─────────────────────────────────────────────────
    with st.expander("공통 설정", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            duration   = st.slider("시뮬 시간 (초)", 30, 300, 180, step=10)
            skill_lv   = st.slider("스킬 레벨", 1, 10, 10)
        with c2:
            level       = st.slider("캐릭터 레벨", 1, 400, 400, step=10)
            burst_regen = st.slider("버스트 충전 시간 (초)", 0.5, 5.0, 2.0, step=0.5)

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
        }
    return None


# ── 팀 슬롯 렌더링 ────────────────────────────────────────────────────────


def _render_team_slots() -> None:
    slots = st.session_state["team_slots"]
    active = st.session_state["active_slot"]
    cols = st.columns(_SLOT_COUNT)

    for i, col in enumerate(cols):
        name = slots[i]
        is_active = (i == active)
        border_color = "#FF4B4B" if is_active else "#555"

        with col:
            if name:
                img = get_image_b64(name)
                # 이미지 + 이름 표시
                if img:
                    st.markdown(
                        f'<div style="border:2px solid {border_color};border-radius:8px;'
                        f'overflow:hidden;text-align:center;">'
                        f'<img src="{img}" style="width:100%;display:block;">'
                        f'<div style="font-size:11px;padding:2px 4px;'
                        f'background:#111;color:#eee;white-space:nowrap;'
                        f'overflow:hidden;text-overflow:ellipsis;">{name}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="border:2px solid {border_color};border-radius:8px;'
                        f'padding:8px;text-align:center;font-size:12px;">{name}</div>',
                        unsafe_allow_html=True,
                    )
                # 슬롯 선택 / 제거 버튼
                bcol1, bcol2 = st.columns(2)
                with bcol1:
                    if st.button("선택", key=f"slot_sel_{i}", use_container_width=True):
                        st.session_state["active_slot"] = i
                        st.rerun()
                with bcol2:
                    if st.button("✕", key=f"slot_del_{i}", use_container_width=True):
                        st.session_state["team_slots"][i] = None
                        st.session_state["active_slot"] = i
                        st.rerun()
            else:
                # 빈 슬롯
                st.markdown(
                    f'<div style="border:2px dashed {border_color};border-radius:8px;'
                    f'height:90px;display:flex;align-items:center;justify-content:center;'
                    f'color:#888;font-size:13px;">슬롯 {i+1}</div>',
                    unsafe_allow_html=True,
                )
                if st.button("편집", key=f"slot_sel_{i}", use_container_width=True):
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
            with cols[j]:
                img = get_image_b64(name)
                # 이미 선택된 캐릭터는 어둡게 표시
                opacity = "0.35" if is_selected else "1.0"
                if img:
                    st.markdown(
                        f'<div style="text-align:center;opacity:{opacity};">'
                        f'<img src="{img}" style="width:100%;border-radius:6px;">'
                        f'<div style="font-size:10px;color:#ccc;margin-top:2px;'
                        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                        f'{name}</div></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="opacity:{opacity};text-align:center;'
                        f'font-size:11px;padding:4px;">{name}</div>',
                        unsafe_allow_html=True,
                    )
                if st.button(
                    "✓" if is_selected else "+",
                    key=f"grid_{name}",
                    use_container_width=True,
                    disabled=is_selected,
                ):
                    st.session_state["team_slots"][active] = name
                    # 다음 빈 슬롯으로 자동 이동
                    for k in range(_SLOT_COUNT):
                        if st.session_state["team_slots"][k] is None:
                            st.session_state["active_slot"] = k
                            break
                    st.rerun()
