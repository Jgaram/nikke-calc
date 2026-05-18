"""NIKKE 시뮬레이터 디버깅 대시보드."""

import sys
import os
import importlib

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

# data 파일 변경 감지 → 관련 모듈 리로드
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_WATCH_FILES = [
    os.path.join(_DATA_DIR, "parsed_skills.json"),
    os.path.join(_DATA_DIR, "parsed_nikke.json"),
]
_mtimes = {f: os.path.getmtime(f) for f in _WATCH_FILES if os.path.exists(f)}
_prev_mtimes = st.session_state.get("_data_mtimes", {})

if _mtimes != _prev_mtimes:
    import calculator.buff_manager as _bm
    import calculator.timeline as _tl
    importlib.reload(_bm)
    importlib.reload(_tl)
    st.session_state["_data_mtimes"] = _mtimes
    st.session_state.pop("result", None)  # 이전 결과 무효화

from calculator.timeline import simulate, DEFAULT_CONFIG
from ui import team_panel, burst_panel, buff_panel, hit_panel


st.set_page_config(
    page_title="NIKKE 시뮬 디버거",
    layout="wide",
)
st.title("NIKKE 시뮬레이터 디버거")


def _make_char(name: str, stat: dict, burst_regen_time: float) -> dict:
    return {
        "name": name,
        "level": stat["level"],
        "breakthrough": stat["breakthrough"],
        "core_enhancement": stat["core_enhancement"],
        "affinity": stat["affinity"],
        "skill_levels": {"1": stat["skill_lv1"], "2": stat["skill_lv2"], "3": stat["skill_lv3"]},
        "burst_regen_time": burst_regen_time,
        "equipment": {
            "머리": {"level": stat["equip_lv_head"], "skills": []},
            "몸통": {"level": stat["equip_lv_body"], "skills": []},
            "팔":   {"level": stat["equip_lv_arm"],  "skills": []},
            "다리": {"level": stat["equip_lv_leg"],  "skills": []},
        },
        "equip_skills": {
            "atk_pct":          stat["equip_atk_pct"],
            "element_bonus":    stat["equip_element_bonus"],
            "max_ammo_pct":     stat["equip_max_ammo_pct"],
            "crit_rate":        stat["equip_crit_rate"],
            "crit_dmg":         stat["equip_crit_dmg"],
            "charge_speed_pct": stat["equip_charge_speed_pct"],
            "charge_dmg_pct":   stat["equip_charge_dmg_pct"],
            "accuracy_pct":     stat["equip_accuracy_pct"],
            "def_pct":          stat["equip_def_pct"],
        },
        "cube": {"name": stat["cube_name"], "level": stat["cube_level"]},
        "console": {
            "common_level": stat["console_common"],
            "class_level": stat["console_class"],
            "company_level": stat["console_company"],
        },
        "collection_stage": stat["collection_stage"],
    }


# ── 팀 구성 (상단 expander) ───────────────────────────────────────────────

with st.expander("팀 구성", expanded=st.session_state.get("result") is None):
    cfg = team_panel.render()
    if cfg:
        team = [
            _make_char(cc["name"], cc["stat"], cc["burst_regen_time"])
            for cc in cfg["char_configs"]
        ]
        sim_config = {
            **DEFAULT_CONFIG,
            "duration": cfg["duration"],
            "max_burst_count": cfg.get("max_burst_count"),
            "burst_sequence": cfg.get("burst_sequence"),
        }

        with st.spinner("시뮬레이션 실행 중…"):
            try:
                result = simulate(team, config=sim_config, enemy=cfg.get("enemy"), verbose=True)
                st.session_state["result"] = result
                st.session_state["team_names"] = [cc["name"] for cc in cfg["char_configs"]]
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
    tab_burst, tab_buff, tab_hit = st.tabs(["버스트 & 대미지", "버프 타임라인", "히트 추적"])

    with tab_burst:
        burst_panel.render(result)
    with tab_buff:
        buff_panel.render(result, team_names)
    with tab_hit:
        hit_panel.render(result)
