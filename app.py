"""NIKKE 시뮬레이터 디버깅 대시보드."""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

# calculator/*.py 파일이 실제로 변경됐을 때만 모듈을 재임포트
_CALC_DIR = os.path.join(os.path.dirname(__file__), "calculator")
_calc_mtime = max(
    (os.path.getmtime(os.path.join(_CALC_DIR, f)) for f in os.listdir(_CALC_DIR) if f.endswith(".py")),
    default=0.0,
)
if st.session_state.get("_calc_mtime") != _calc_mtime:
    for _k in [k for k in sys.modules if k.startswith("calculator")]:
        del sys.modules[_k]
    st.session_state["_calc_mtime"] = _calc_mtime

from calculator.timeline import simulate, DEFAULT_CONFIG
from ui import team_panel, burst_panel, buff_panel, hit_panel, skill_panel


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


# ── 스쿼드 구성 (상단 expander) ───────────────────────────────────────────────

with st.expander("스쿼드 구성", expanded=st.session_state.get("result") is None):
    cfg = team_panel.render()
    if cfg:
        squad = [
            _make_char(cc["name"], cc["stat"], cc["burst_regen_time"])
            for cc in cfg["char_configs"]
        ]
        sim_config = {
            **DEFAULT_CONFIG,
            "duration": cfg["duration"],
            "burst_switch_delay": cfg.get("burst_use_delay", 0.1),
            "max_burst_count": cfg.get("max_burst_count"),
            "burst_sequence": cfg.get("burst_sequence"),
            "no_burst_char": cfg.get("no_burst_char"),
        }

        with st.spinner("시뮬레이션 실행 중…"):
            try:
                result = simulate(squad, config=sim_config, enemy=cfg.get("enemy"), verbose=True)
                st.session_state["result"] = result
                st.session_state["squad_names"] = [cc["name"] for cc in cfg["char_configs"]]
                st.session_state["char_skill_levels"] = {
                    cc["name"]: {"1": cc["stat"]["skill_lv1"], "2": cc["stat"]["skill_lv2"], "3": cc["stat"]["skill_lv3"]}
                    for cc in cfg["char_configs"]
                }
                st.rerun()
            except Exception as e:
                st.error(f"시뮬 오류: {e}")
                raise


# ── 결과 탭 ───────────────────────────────────────────────────────────────

result = st.session_state.get("result")

if result is None:
    st.info("위에서 캐릭터를 선택하고 **▶ 시뮬 실행**을 누르세요.")
else:
    squad_names = st.session_state.get("squad_names", [])
    st.caption(f"스쿼드: {' / '.join(squad_names)}  |  스쿼드 총 딜: {result.squad_total:,}")
    tab_overview, tab_burst_hits, tab_buff, tab_hit, tab_skill = st.tabs(
        ["개요", "버스트별 히트 수", "버프 타임라인", "히트 추적", "스킬 원문"]
    )

    with tab_overview:
        burst_panel.render_overview(result)
    with tab_burst_hits:
        burst_panel.render_burst_hits(result)
        hit_panel.render_aggregate_only(result, char_sel_key="burst_char_radio")
    with tab_buff:
        buff_panel.render(result, squad_names)
    with tab_hit:
        hit_panel.render_filter_only(result)
    with tab_skill:
        skill_panel.render(squad_names, st.session_state.get("char_skill_levels", {}))
