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


def render(result: SimResult) -> None:
    st.subheader("히트 추적")

    chars = sorted(result.char_total.keys(), key=lambda n: -result.char_total[n])

    char_sel = st.selectbox("캐릭터 선택", chars, key="hit_char_sel")

    char_hits = [e for e in result.hits if e.caster == char_sel]

    skill_values = _load_skill_values()

    # ── 스킬별 집계 ───────────────────────────────────────────────────────
    st.markdown("#### 스킬별 집계")

    tag_dmg: dict[tuple, int] = defaultdict(int)
    tag_cnt: dict[tuple, int] = defaultdict(int)
    tag_vals: dict[tuple, list[int]] = defaultdict(list)
    for ev in char_hits:
        key = (ev.skill_name, ev.hit_tag)
        tag_dmg[key] += ev.damage
        tag_cnt[key] += 1
        tag_vals[key].append(ev.damage)

    grand_total = sum(tag_dmg.values())

    agg_rows = []
    for (skill, tag), dmg in sorted(tag_dmg.items(), key=lambda x: -x[1]):
        cnt = tag_cnt[(skill, tag)]
        vals = tag_vals[(skill, tag)]

        # 최빈값
        freq: dict[int, int] = defaultdict(int)
        for v in vals:
            freq[v] += 1
        mode_val = max(freq, key=lambda v: (freq[v], v))

        # value: parsed_skills에서 lv10 계수 룩업
        sv = skill_values.get(skill, {})
        coeff = sv.get(tag)
        value_str = f"{coeff:.2f}%" if coeff is not None else ""

        ratio = dmg / grand_total * 100 if grand_total else 0.0

        agg_rows.append({
            "스킬명": skill,
            "stat": tag,
            "value": value_str,
            "히트수": cnt,
            "총 대미지": dmg,
            "비율": f"{ratio:.1f}%",
            "최빈값": mode_val,
        })

    st.dataframe(pd.DataFrame(agg_rows), use_container_width=True, height=300, hide_index=True)

    st.divider()

    # ── 히트 이벤트 필터 ──────────────────────────────────────────────────
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

    total_dmg = sum(e.damage for e in filtered)
    st.markdown(f"**{len(filtered):,}건** / 합계 대미지 **{total_dmg:,}**")

    hit_rows = [
        {
            "시각(s)": round(e.t, 3),
            "스킬명": e.skill_name,
            "hit_tag": e.hit_tag,
            "대미지": e.damage,
            "크리": "✓" if e.is_crit else "",
        }
        for e in filtered
    ]
    st.dataframe(pd.DataFrame(hit_rows), use_container_width=True, height=400, hide_index=True)
