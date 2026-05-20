"""히트 추적 패널 — 스킬별 집계 표 + 히트 이벤트 필터."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

from calculator.sim_result import SimResult

_SKILLS_PATH = Path(__file__).parent.parent / "data" / "parsed_skills.json"


@st.cache_data
def _load_skill_values() -> dict[str, dict[str, float]]:
    """skill_name → stat → value(lv10) 룩업 테이블."""
    with open(_SKILLS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    table: dict[str, dict[str, float]] = {}
    for effects in raw.values():
        for eff in effects:
            name = eff.get("name", "")
            stat = eff.get("stat", "")
            values = eff.get("values", {})
            if name and stat and values:
                val = values.get("10") or values.get(max(values, key=int))
                table.setdefault(name, {})[stat] = val
    return table


def _is_pellet_tag(tag: str) -> bool:
    return tag.startswith("pellet:") or tag.startswith("core:pellet:")


def _pellet_idx(tag: str) -> int:
    return int(tag.rsplit(":", 1)[-1])


def _build_agg_rows(hits: list) -> list[dict]:
    """hits → 스킬별 집계 rows (캐릭터 컬럼 포함)."""
    skill_values = _load_skill_values()

    shot_groups: dict[tuple, list] = defaultdict(list)
    non_pellet_hits = []
    for ev in hits:
        if _is_pellet_tag(ev.hit_tag):
            shot_groups[(round(ev.t, 6), ev.caster, ev.skill_name)].append(ev)
        else:
            non_pellet_hits.append(ev)

    tag_dmg: dict[tuple, int] = defaultdict(int)
    tag_cnt: dict[tuple, int] = defaultdict(int)
    tag_vals: dict[tuple, list[int]] = defaultdict(list)

    for (_, caster, skill_name), group in shot_groups.items():
        pps = len(group)
        key = (caster, skill_name, pps)
        shot_dmg = sum(e.damage for e in group)
        tag_dmg[key] += shot_dmg
        tag_cnt[key] += 1
        tag_vals[key].append(shot_dmg)

    for ev in non_pellet_hits:
        key = (ev.caster, ev.skill_name, ev.hit_tag)
        tag_dmg[key] += ev.damage
        tag_cnt[key] += 1
        tag_vals[key].append(ev.damage)

    grand_total = sum(tag_dmg.values())

    agg_rows = []
    for (caster, skill, tag), dmg in sorted(tag_dmg.items(), key=lambda x: -x[1]):
        cnt = tag_cnt[(caster, skill, tag)]
        vals = tag_vals[(caster, skill, tag)]

        freq: dict[int, int] = defaultdict(int)
        for v in vals:
            freq[v] += 1
        mode_val = max(freq, key=lambda v: (freq[v], v))

        if isinstance(tag, int):
            display_tag = f"팰릿 {tag}발"
            value_str = ""
        else:
            display_tag = tag
            sv = skill_values.get(skill, {})
            coeff = sv.get(tag)
            value_str = f"{coeff:.2f}%" if coeff is not None else ""

        ratio = dmg / grand_total * 100 if grand_total else 0.0

        agg_rows.append({
            "캐릭터": caster,
            "스킬명": skill,
            "stat": display_tag,
            "value": value_str,
            "히트수": cnt,
            "총 대미지": dmg,
            "비율": f"{ratio:.1f}%",
            "최빈값": mode_val,
        })

    return agg_rows


def render_aggregate_only(result: SimResult, char_sel_key: str = "burst_char_radio") -> None:
    """버스트별 히트 수 탭 하단: 전체 전투 기간 스킬별 집계."""
    char_sel = st.session_state.get(char_sel_key, "전체")

    hits = result.hits if char_sel == "전체" else [e for e in result.hits if e.caster == char_sel]
    label = "전체" if char_sel == "전체" else char_sel
    st.markdown(f"#### 스킬별 집계 (전체 전투, {label})")

    rows = _build_agg_rows(hits)
    if not rows:
        st.info("히트 없음")
        return

    df = pd.DataFrame(rows)
    if char_sel != "전체":
        df = df.drop(columns=["캐릭터"])
    st.dataframe(df, use_container_width=True, height=300, hide_index=True)


def render_filter_only(result: SimResult) -> None:
    """히트 추적 탭: 캐릭터 선택 + 히트 이벤트 필터."""
    st.subheader("히트 추적")

    chars = sorted(result.char_total.keys(), key=lambda n: -result.char_total[n])
    char_sel = st.selectbox("캐릭터 선택", chars, key="hit_char_sel")
    char_hits = [e for e in result.hits if e.caster == char_sel]

    st.markdown("#### 히트 이벤트 필터")

    c1, c2 = st.columns(2)
    available_skills = sorted({e.skill_name for e in char_hits})

    with c1:
        f_skills = st.multiselect(
            "스킬",
            available_skills,
            default=available_skills,
            key="hit_f_skills",
        )

    with c2:
        t_range = st.slider(
            "시간 범위 (초)",
            0.0, float(result.duration),
            (0.0, float(result.duration)),
            step=1.0,
            key="hit_f_time",
        )

    filtered = [
        e for e in char_hits
        if e.skill_name in f_skills
        and t_range[0] <= e.t <= t_range[1]
    ]

    filtered_pellet_groups: dict[tuple, list] = defaultdict(list)
    hit_rows = []

    for e in filtered:
        if _is_pellet_tag(e.hit_tag):
            filtered_pellet_groups[(round(e.t, 6), e.skill_name)].append(e)
        else:
            hit_rows.append({
                "시각(s)": round(e.t, 3),
                "스킬명": e.skill_name,
                "hit_tag": e.hit_tag,
                "대미지": e.damage,
                "크리": "✓" if e.is_crit else "",
            })

    for (t, skill_name), group in filtered_pellet_groups.items():
        n = len(group)
        total_dmg = sum(e.damage for e in group)
        crit_cnt = sum(1 for e in group if e.is_crit)
        crit_str = "✓" if crit_cnt == n else f"{crit_cnt}/{n}" if crit_cnt else ""
        hit_rows.append({
            "시각(s)": round(t, 3),
            "스킬명": skill_name,
            "hit_tag": f"팰릿 {n}발",
            "대미지": total_dmg,
            "크리": crit_str,
        })

    hit_rows.sort(key=lambda r: r["시각(s)"])

    total_dmg_filtered = sum(r["대미지"] for r in hit_rows)
    st.markdown(f"**{len(hit_rows):,}건** / 합계 대미지 **{total_dmg_filtered:,}**")
    st.dataframe(pd.DataFrame(hit_rows), use_container_width=True, height=400, hide_index=True)
