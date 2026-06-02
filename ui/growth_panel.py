"""성장 효율 분석 탭."""
import copy
import json
import os

import pandas as pd
import streamlit as st

from calculator.timeline import simulate

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_NIKKE_PATH = os.path.join(_DATA_DIR, "parsed_nikke.json")
_MANUFACTURERS = ["엘리시온", "미실리스", "테트라", "필그림", "어브노말"]
_CLASSES = ["화력형", "지원형", "방어형"]
_CUBE_OPTIONS = ["재장", "탄충", "파츠", "체력", "차속"]
_COLLECTION_OPTIONS = [f"SR{i}" for i in range(16)]


@st.cache_data(show_spinner=False)
def _load_char_info() -> dict[str, dict]:
    with open(_NIKKE_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return {k: {"manufacturer": v.get("manufacturer", ""), "class": v.get("class", "")} for k, v in d.items()}


@st.cache_data(show_spinner=False)
def _cached_simulate(squad_json: str, config_json: str, enemy_json: str) -> dict:
    squad = json.loads(squad_json)
    config = json.loads(config_json)
    enemy = json.loads(enemy_json)
    r = simulate(squad, config=config, enemy=enemy)
    return {"squad_total": r.squad_total, "char_total": r.char_total}


def _squad_hash(squad: list[dict]) -> str:
    return json.dumps([{
        "name": c["name"],
        "level": c["level"],
        "skill_levels": c["skill_levels"],
        "breakthrough": c["breakthrough"],
        "core_enhancement": c["core_enhancement"],
        "affinity": c.get("affinity"),
        "cube": c["cube"],
        "equipment": {k: v["level"] for k, v in c["equipment"].items()},
        "equip_skills": c.get("equip_skills"),
        "console": c["console"],
        "collection_stage": c.get("collection_stage"),
    } for c in squad], sort_keys=True)


def _extract_global_current(squad: list[dict], char_info: dict) -> dict:
    """현재 squad에서 공통 글로벌 스탯 현재값 추출."""
    console_company: dict[str, int] = {m: 100 for m in _MANUFACTURERS}
    console_class: dict[str, int] = {c: 100 for c in _CLASSES}
    for char in squad:
        info = char_info.get(char["name"], {})
        mfr = info.get("manufacturer", "")
        cls = info.get("class", "")
        if mfr in console_company:
            console_company[mfr] = char["console"]["company_level"]
        if cls in console_class:
            console_class[cls] = char["console"]["class_level"]

    cube_levels: dict[str, int] = {c: 15 for c in _CUBE_OPTIONS}
    for char in squad:
        cube_name = char["cube"].get("name", "")
        if cube_name in cube_levels:
            cube_levels[cube_name] = char["cube"]["level"]

    return {
        "sync_level": squad[0]["level"] if squad else 400,
        "console_common": squad[0]["console"]["common_level"] if squad else 180,
        "console_company": console_company,
        "console_class": console_class,
        "cube_levels": cube_levels,
    }


def _build_modified_squad(
    squad: list[dict],
    char_info: dict,
    global_ov: dict,
    char_overrides: dict[str, dict],
) -> list[dict]:
    modified = copy.deepcopy(squad)
    for char in modified:
        name = char["name"]
        info = char_info.get(name, {})

        char["level"] = global_ov["sync_level"]
        char["console"]["common_level"] = global_ov["console_common"]

        mfr = info.get("manufacturer", "")
        if mfr in global_ov["console_company"]:
            char["console"]["company_level"] = global_ov["console_company"][mfr]

        cls = info.get("class", "")
        if cls in global_ov["console_class"]:
            char["console"]["class_level"] = global_ov["console_class"][cls]

        ov = char_overrides.get(name, {})

        for field, target in [("skill_1", ("skill_levels", "1")), ("skill_2", ("skill_levels", "2")), ("skill_3", ("skill_levels", "3"))]:
            if field in ov:
                char[target[0]][target[1]] = ov[field]
        for field in ("breakthrough", "core_enhancement", "affinity", "collection_stage"):
            if field in ov:
                char[field] = ov[field]

        # 큐브 종류 변경 후 해당 큐브 타입의 레벨 적용
        new_cube_name = ov.get("cube_name", char["cube"]["name"])
        char["cube"]["name"] = new_cube_name
        char["cube"]["level"] = global_ov["cube_levels"].get(new_cube_name, char["cube"]["level"])

        for part in ("머리", "몸통", "팔", "다리"):
            key = f"equip_{part}"
            if key in ov:
                char["equipment"][part]["level"] = ov[key]

        if "equip_skills" in char:
            for stat_key in ("atk_pct", "element_bonus", "max_ammo_pct", "crit_rate", "crit_dmg",
                             "charge_speed_pct", "charge_dmg_pct", "accuracy_pct", "def_pct"):
                if stat_key in ov:
                    char["equip_skills"][stat_key] = ov[stat_key]

    return modified


def _render_global_section(current: dict) -> dict:
    """공통 스탯 섹션 렌더링. 수정된 global_ov 반환."""
    st.subheader("공통 스탯")

    g1, g2 = st.columns(2)
    with g1:
        sync_level = st.number_input(
            f"싱크로레벨  (현재 {current['sync_level']})",
            min_value=1, value=current["sync_level"],
            key="_gp_global_sync",
        )
    with g2:
        console_common = st.number_input(
            f"공통 콘솔  (현재 {current['console_common']})",
            min_value=0, step=10, value=current["console_common"],
            key="_gp_global_console_common",
        )

    st.markdown("**제조사 콘솔**")
    mfr_cols = st.columns(len(_MANUFACTURERS))
    console_company: dict[str, int] = {}
    for col, mfr in zip(mfr_cols, _MANUFACTURERS):
        with col:
            cur = current["console_company"].get(mfr, 100)
            console_company[mfr] = st.number_input(
                f"{mfr}  ({cur})", min_value=0, step=10, value=cur,
                key=f"_gp_global_mfr_{mfr}",
            )

    st.markdown("**클래스 콘솔**")
    cls_cols = st.columns(len(_CLASSES))
    console_class: dict[str, int] = {}
    for col, cls in zip(cls_cols, _CLASSES):
        with col:
            cur = current["console_class"].get(cls, 100)
            console_class[cls] = st.number_input(
                f"{cls}  ({cur})", min_value=0, step=10, value=cur,
                key=f"_gp_global_cls_{cls}",
            )

    st.markdown("**큐브 레벨**")
    cube_cols = st.columns(len(_CUBE_OPTIONS))
    cube_levels: dict[str, int] = {}
    for col, cube in zip(cube_cols, _CUBE_OPTIONS):
        with col:
            cur = current["cube_levels"].get(cube, 15)
            cube_levels[cube] = st.slider(
                f"{cube}  ({cur})", 1, 15, cur,
                key=f"_gp_global_cube_{cube}",
            )

    return {
        "sync_level": sync_level,
        "console_common": console_common,
        "console_company": console_company,
        "console_class": console_class,
        "cube_levels": cube_levels,
    }


def _render_char_section(char: dict) -> dict:
    """캐릭터별 스탯 입력 (접힘 기본). 오버라이드 dict 반환."""
    name = char["name"]
    ov: dict = {}

    with st.expander(name, expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            ov["skill_1"] = st.slider(
                f"스킬1  (현재 {char['skill_levels']['1']})",
                1, 10, char["skill_levels"]["1"], key=f"_gp_{name}_skill1",
            )
            ov["skill_2"] = st.slider(
                f"스킬2  (현재 {char['skill_levels']['2']})",
                1, 10, char["skill_levels"]["2"], key=f"_gp_{name}_skill2",
            )
            ov["skill_3"] = st.slider(
                f"버스트  (현재 {char['skill_levels']['3']})",
                1, 10, char["skill_levels"]["3"], key=f"_gp_{name}_skill3",
            )
            ov["breakthrough"] = st.slider(
                f"돌파  (현재 {char['breakthrough']})",
                0, 3, char["breakthrough"], key=f"_gp_{name}_bt",
            )
            ov["core_enhancement"] = st.slider(
                f"코어강화  (현재 {char['core_enhancement']})",
                0, 7, char["core_enhancement"], key=f"_gp_{name}_core",
            )
            cur_aff = char.get("affinity", 30)
            ov["affinity"] = st.slider(
                f"호감도  (현재 {cur_aff})",
                0, 40, cur_aff, key=f"_gp_{name}_affinity",
            )
            cur_cube = char["cube"].get("name", "재장")
            ov["cube_name"] = st.selectbox(
                f"큐브 종류  (현재 {cur_cube})",
                _CUBE_OPTIONS,
                index=_CUBE_OPTIONS.index(cur_cube) if cur_cube in _CUBE_OPTIONS else 0,
                key=f"_gp_{name}_cube_name",
            )
            cur_col = char.get("collection_stage", "SR15")
            ov["collection_stage"] = st.selectbox(
                f"소장품  (현재 {cur_col})",
                _COLLECTION_OPTIONS,
                index=_COLLECTION_OPTIONS.index(cur_col) if cur_col in _COLLECTION_OPTIONS else len(_COLLECTION_OPTIONS) - 1,
                key=f"_gp_{name}_collection",
            )
        with c2:
            for part, part_label in [("머리", "장비(머리)"), ("몸통", "장비(몸통)"), ("팔", "장비(팔)"), ("다리", "장비(다리)")]:
                cur_eq = char["equipment"][part]["level"]
                ov[f"equip_{part}"] = st.slider(
                    f"{part_label}  (현재 {cur_eq})",
                    0, 5, cur_eq, key=f"_gp_{name}_equip_{part}",
                )
            eq = char.get("equip_skills", {})
            st.markdown("**장비 스킬**")
            eq1, eq2, eq3 = st.columns(3)
            with eq1:
                ov["atk_pct"]          = st.number_input("공격력 %",      min_value=0.0, value=float(eq.get("atk_pct", 0)),          step=0.01, format="%.2f", key=f"_gp_{name}_eq_atk")
                ov["crit_rate"]        = st.number_input("크리 확률 %",   min_value=0.0, value=float(eq.get("crit_rate", 0)),         step=0.01, format="%.2f", key=f"_gp_{name}_eq_crit_r")
                ov["charge_speed_pct"] = st.number_input("차지 속도 %",   min_value=0.0, value=float(eq.get("charge_speed_pct", 0)),  step=0.01, format="%.2f", key=f"_gp_{name}_eq_chrspd")
            with eq2:
                ov["max_ammo_pct"]     = st.number_input("최대 장탄 %",   min_value=0.0, value=float(eq.get("max_ammo_pct", 0)),      step=0.01, format="%.2f", key=f"_gp_{name}_eq_ammo")
                ov["crit_dmg"]         = st.number_input("크리 대미지 %", min_value=0.0, value=float(eq.get("crit_dmg", 0)),          step=0.01, format="%.2f", key=f"_gp_{name}_eq_crit_d")
                ov["charge_dmg_pct"]   = st.number_input("차지 대미지 %", min_value=0.0, value=float(eq.get("charge_dmg_pct", 0)),    step=0.01, format="%.2f", key=f"_gp_{name}_eq_chrdmg")
            with eq3:
                ov["element_bonus"]    = st.number_input("우월 코드 %",   min_value=0.0, value=float(eq.get("element_bonus", 0)),     step=0.01, format="%.2f", key=f"_gp_{name}_eq_elem")
                ov["accuracy_pct"]     = st.number_input("명중률 %",      min_value=0.0, value=float(eq.get("accuracy_pct", 0)),      step=0.01, format="%.2f", key=f"_gp_{name}_eq_acc")
                ov["def_pct"]          = st.number_input("방어력 %",      min_value=0.0, value=float(eq.get("def_pct", 0)),           step=0.01, format="%.2f", key=f"_gp_{name}_eq_def")

    return ov


def render(result, squad: list[dict], sim_config: dict, enemy: dict | None) -> None:
    if not squad:
        st.info("스쿼드를 먼저 구성하고 시뮬을 실행하세요.")
        return

    # 스쿼드/기준 스탯 변경 시 모든 _gp_ 상태 초기화
    cur_hash = _squad_hash(squad)
    if st.session_state.get("_gp_squad_hash") != cur_hash:
        for k in [k for k in list(st.session_state.keys()) if k.startswith("_gp_")]:
            del st.session_state[k]
        st.session_state["_gp_squad_hash"] = cur_hash

    char_info = _load_char_info()
    baseline_squad_total = result.squad_total
    baseline_char_total = result.char_total
    squad_names = [c["name"] for c in squad]

    st.caption(f"기준 총딜량: **{baseline_squad_total:,}**")
    st.divider()

    selected_names: list[str] = st.multiselect(
        "수정할 캐릭터 선택",
        options=squad_names,
        default=squad_names,
        key="_gp_char_sel",
    )

    # 공통 스탯
    current_global = _extract_global_current(squad, char_info)
    global_ov = _render_global_section(current_global)

    st.divider()

    # 캐릭터별 스탯 (접힘 기본)
    char_overrides: dict[str, dict] = {}
    for char in squad:
        name = char["name"]
        if name not in selected_names:
            continue
        char_overrides[name] = _render_char_section(char)

    st.divider()

    if st.button("비교 실행", type="primary"):
        modified = _build_modified_squad(squad, char_info, global_ov, char_overrides)
        with st.spinner("비교 시뮬 실행 중…"):
            new_res = _cached_simulate(
                json.dumps(modified),
                json.dumps(sim_config),
                json.dumps(enemy),
            )

        rows: list[dict] = []
        for name in squad_names:
            base = baseline_char_total.get(name, 0)
            new = new_res["char_total"].get(name, 0)
            delta = new - base
            pct = delta / base * 100 if base > 0 else 0.0
            rows.append({
                "캐릭터": name,
                "기준 총딜": base,
                "변경 총딜": new,
                "절대 증가량": delta,
                "증가율 %": round(pct, 3),
            })

        new_squad_total = new_res["squad_total"]
        squad_delta = new_squad_total - baseline_squad_total
        squad_pct = squad_delta / baseline_squad_total * 100 if baseline_squad_total > 0 else 0.0
        rows.append({
            "캐릭터": "스쿼드 합",
            "기준 총딜": baseline_squad_total,
            "변경 총딜": new_squad_total,
            "절대 증가량": squad_delta,
            "증가율 %": round(squad_pct, 3),
        })
        st.session_state["_gp_result_rows"] = rows

    # ── 결과 표시 ─────────────────────────────────────────────────────────
    if "_gp_result_rows" not in st.session_state:
        return

    rows = st.session_state["_gp_result_rows"]
    df = pd.DataFrame(rows)

    st.subheader("비교 결과")
    st.dataframe(
        df.style.format({
            "기준 총딜": "{:,}",
            "변경 총딜": "{:,}",
            "절대 증가량": "{:+,}",
            "증가율 %": "{:+.3f}%",
        }),
        use_container_width=True,
        hide_index=True,
    )

    char_rows = [r for r in rows if r["캐릭터"] != "스쿼드 합"]
    if char_rows:
        chart_data = pd.DataFrame(char_rows).set_index("캐릭터")["증가율 %"]
        st.subheader("캐릭터별 증가율 %")
        st.bar_chart(chart_data)
