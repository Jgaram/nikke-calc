"""NIKKE 시뮬레이터 디버깅 대시보드."""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

from calculator.timeline import simulate, DEFAULT_CONFIG
from ui import team_panel, burst_panel, buff_panel


st.set_page_config(
    page_title="NIKKE 시뮬 디버거",
    layout="wide",
)
st.title("NIKKE 시뮬레이터 디버거")


def _make_char(name: str, level: int, skill_level: int, burst_regen_time: float) -> dict:
    return {
        "name": name,
        "level": level,
        "breakthrough": 3,
        "core_enhancement": 0,
        "affinity": 30,
        "skill_level": skill_level,
        "burst_regen_time": burst_regen_time,
        "equipment": {p: {"level": 5, "skills": []} for p in ["머리", "몸통", "팔", "다리"]},
        "equip_skills": {"atk_pct": 20, "max_ammo_pct": 120},
        "cube": {"name": "재장", "level": 15},
        "console": {"common_level": 180, "class_level": 100, "company_level": 100},
        "collection_stage": "SR15",
    }


# ── 팀 구성 (상단 expander) ───────────────────────────────────────────────

with st.expander("팀 구성", expanded=st.session_state.get("result") is None):
    cfg = team_panel.render()
    if cfg:
        team = [
            _make_char(n, cfg["level"], cfg["skill_level"], cfg["burst_regen_time"])
            for n in cfg["chars"]
        ]
        sim_config = {**DEFAULT_CONFIG, "duration": cfg["duration"]}

        with st.spinner("시뮬레이션 실행 중…"):
            try:
                result = simulate(team, config=sim_config, verbose=True)
                st.session_state["result"] = result
                st.session_state["team_names"] = cfg["chars"]
                st.rerun()
            except Exception as e:
                st.error(f"시뮬 오류: {e}")
                raise


# ── 결과 탭 ───────────────────────────────────────────────────────────────

result = st.session_state.get("result")

if result is None:
    st.info("위에서 캐릭터를 선택하고 **▶ 시뮬 실행**을 누르세요.")
else:
    team_names = st.session_state.get("team_names", [])
    st.caption(f"팀: {' / '.join(team_names)}  |  팀 총 딜: {result.team_total:,}")
    tab1, tab2 = st.tabs(["버스트 & 대미지", "버프 & 히트 추적"])
    with tab1:
        burst_panel.render(result)
    with tab2:
        buff_panel.render(result)
