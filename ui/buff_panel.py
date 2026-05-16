"""버프 & 히트 이벤트 추적 패널."""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculator.sim_result import SimResult
from ui.image_utils import get_image_b64


def render(result: SimResult) -> None:
    st.subheader("버프 & 히트 추적")

    _buff_section(result)
    st.divider()
    _hit_filter_section(result)


# ── 버프 스냅샷 ────────────────────────────────────────────────────────────


def _buff_section(result: SimResult) -> None:
    st.markdown("#### 버프 스냅샷 (풀버스트 진입 시점)")

    if not result.log or not result.log.buff_snapshots:
        st.info("버프 스냅샷 없음")
        return

    chars = sorted(result.char_total.keys())

    char_sel = st.selectbox("버프 적용 대상 캐릭터", chars, key="buff_char_sel")

    # 버프 목록 수집 (중복 제거 없이 시점별로)
    rows = []
    for snap in result.log.buff_snapshots:
        buff_list = snap.buffs_by_char.get(char_sel, [])
        for b in buff_list:
            exp = result.duration if b.expires_at == math.inf else min(b.expires_at, result.duration)
            rows.append({
                "버프명": b.name,
                "시전자": b.caster,
                "시작(s)": round(snap.t, 3),
                "만료(s)": round(exp, 3),
            })

    if rows:
        df = pd.DataFrame(rows)

        # Gantt 차트
        fig = go.Figure()
        buff_names = df["버프명"].unique().tolist()
        for name in buff_names:
            sub = df[df["버프명"] == name]
            for _, row in sub.iterrows():
                fig.add_trace(go.Bar(
                    x=[row["만료(s)"] - row["시작(s)"]],
                    y=[name],
                    base=[row["시작(s)"]],
                    orientation="h",
                    showlegend=False,
                    hovertemplate=(
                        f"{name}<br>시전: {row['시전자']}<br>"
                        f"t={row['시작(s)']}s ~ {row['만료(s)']}s<extra></extra>"
                    ),
                ))

        fig.update_layout(
            barmode="overlay",
            xaxis_title="시간 (초)",
            xaxis=dict(range=[0, result.duration]),
            height=max(200, 30 * len(buff_names) + 60),
            margin=dict(t=20, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)

        # 표 + 시전자 이미지
        st.markdown("##### 버프 목록")
        # 시전자별로 고유 이미지를 행 옆에 표시
        unique_casters = df["시전자"].unique().tolist()

        for caster in unique_casters:
            sub = df[df["시전자"] == caster].copy()
            img = get_image_b64(caster)
            col_img, col_tbl = st.columns([1, 6])
            with col_img:
                if img:
                    st.image(img, width=64)
                st.caption(caster)
            with col_tbl:
                st.dataframe(
                    sub[["버프명", "시작(s)", "만료(s)"]].reset_index(drop=True),
                    use_container_width=True,
                    hide_index=True,
                )
    else:
        st.info(f"{char_sel}에게 적용된 버프 스냅샷 없음")

    # 전체 스냅샷 원시 데이터
    with st.expander("전체 스냅샷 원시 데이터"):
        for i, snap in enumerate(result.log.buff_snapshots):
            st.markdown(f"**스냅샷 #{i+1}  (t={snap.t:.3f}s)**")
            for char, buffs in snap.buffs_by_char.items():
                if not buffs:
                    continue
                col_img, col_tbl = st.columns([1, 8])
                with col_img:
                    img = get_image_b64(char)
                    if img:
                        st.image(img, width=48)
                    st.caption(char)
                with col_tbl:
                    buf_rows = []
                    for b in buffs:
                        exp_str = "영구" if b.expires_at == math.inf else f"{b.expires_at:.2f}s"
                        buf_rows.append({"버프명": b.name, "시전자": b.caster, "만료": exp_str})
                    st.dataframe(pd.DataFrame(buf_rows), use_container_width=True, hide_index=True)


# ── 히트 이벤트 필터 ──────────────────────────────────────────────────────


def _hit_filter_section(result: SimResult) -> None:
    st.markdown("#### 히트 이벤트 필터")

    chars = sorted(result.char_total.keys())

    c1, c2, c3 = st.columns(3)

    with c1:
        f_chars = st.multiselect("캐릭터", chars, default=chars, key="hit_f_chars")

    # 선택된 캐릭터의 스킬명 목록 (캐릭터 변경 시 자동 갱신)
    available_skills = sorted({
        e.skill_name for e in result.hits if e.caster in f_chars
    })

    with c2:
        f_skills = st.multiselect(
            "스킬",
            available_skills,
            default=available_skills,
            key="hit_f_skills",
        )

    with c3:
        t_range = st.slider(
            "시간 범위 (초)",
            0.0, float(result.duration),
            (0.0, float(result.duration)),
            step=1.0,
            key="hit_f_time",
        )

    filtered = [
        e for e in result.hits
        if e.caster in f_chars
        and e.skill_name in f_skills
        and t_range[0] <= e.t <= t_range[1]
    ]

    total_dmg = sum(e.damage for e in filtered)
    st.markdown(f"**{len(filtered):,}건** / 합계 대미지 **{total_dmg:,}**")

    rows = [
        {
            "시각(s)": round(e.t, 3),
            "캐릭터": e.caster,
            "스킬명": e.skill_name,
            "hit_tag": e.hit_tag,
            "대미지": e.damage,
            "크리": "✓" if e.is_crit else "",
        }
        for e in filtered
    ]
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=400)
