"""팀 구성 패널 — 캐릭터 선택 및 시뮬 실행."""

from __future__ import annotations

import json
import os

import streamlit as st

from ui.image_utils import get_image_b64

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@st.cache_data
def _load_char_names() -> list[str]:
    """parsed_skills.json에 있는 캐릭터만 반환 (시뮬 가능한 목록)."""
    with open(os.path.join(_DATA_DIR, "parsed_skills.json"), encoding="utf-8") as f:
        d = json.load(f)
    return sorted(d.keys())


def render() -> dict | None:
    """팀 구성 UI를 렌더링하고, 실행 버튼이 눌리면 config dict를 반환한다."""
    st.subheader("팀 구성")

    char_names = _load_char_names()
    options = ["(없음)"] + char_names

    prev = st.session_state.get("team_selection", ["(없음)"] * 5)

    cols = st.columns(5)
    selected: list[str] = []

    for i, col in enumerate(cols):
        with col:
            val = col.selectbox(
                f"슬롯 {i+1}",
                options,
                index=options.index(prev[i]) if prev[i] in options else 0,
                key=f"char_slot_{i}",
            )
            selected.append(val)
            # 선택된 캐릭터 이미지 표시
            if val != "(없음)":
                img = get_image_b64(val)
                if img:
                    col.image(img, use_container_width=True)
                else:
                    col.caption("이미지 없음")

    st.session_state["team_selection"] = selected

    with st.expander("공통 설정", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            duration = st.slider("시뮬 시간 (초)", 30, 300, 180, step=10)
            skill_lv = st.slider("스킬 레벨", 1, 10, 10)
        with c2:
            level = st.slider("캐릭터 레벨", 1, 400, 400, step=10)
            burst_regen = st.slider("버스트 충전 시간 (초)", 0.5, 5.0, 2.0, step=0.5)

    run = st.button("시뮬 실행", type="primary", use_container_width=True)

    if run:
        chars = [n for n in selected if n != "(없음)"]
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
