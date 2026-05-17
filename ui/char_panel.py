"""캐릭터별 상세 패널 — 받는 버프 & 입힌 대미지."""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculator.sim_result import SimResult, analyze_damage
from ui.image_utils import get_image_b64


def _safe_key(prefix: str, name: str) -> str:
    return f"{prefix}_{hash(name) & 0xFFFFFFFF}"


def render(result: SimResult, char_name: str) -> None:
    img = get_image_b64(char_name)
    if img:
        col_img, col_title = st.columns([1, 8])
        with col_img:
            st.markdown(
                f'<div style="width:100%;padding-top:130%;position:relative;'
                f'overflow:hidden;border-radius:6px;">'
                f'<img src="{img}" style="position:absolute;top:0;left:0;'
                f'width:100%;height:100%;object-fit:cover;object-position:top;">'
                f'</div>',
                unsafe_allow_html=True,
            )
        with col_title:
            st.markdown(f"## {char_name}")
    else:
        st.markdown(f"## {char_name}")

    st.markdown("#### 받는 버프")
    _buff_section(result, char_name)
    st.divider()
    st.markdown("#### 입힌 대미지")
    _damage_section(result, char_name)


# ── 받는 버프 ─────────────────────────────────────────────────────────────


def _buff_section(result: SimResult, char_name: str) -> None:
    if not result.log or not result.log.buff_events:
        st.info("버프 이벤트 없음 (verbose=True 필요)")
        return

    segments: list[dict] = []
    open_segs: dict[tuple, dict] = {}

    for ev in result.log.buff_events:
        if ev.target != char_name:
            continue
        key = (ev.name, ev.caster)
        if ev.kind == "activate":
            if key in open_segs:
                open_segs[key]["만료(s)"] = round(ev.t, 3)
                segments.append(open_segs[key])
            seg = {
                "버프명": ev.name,
                "시전자": ev.caster,
                "시작(s)": round(ev.t, 3),
                "만료(s)": round(min(ev.expires_at, result.duration) if ev.expires_at != math.inf else result.duration, 3),
            }
            open_segs[key] = seg
            segments.append(seg)
        elif ev.kind == "expire":
            if key in open_segs:
                open_segs[key]["만료(s)"] = round(ev.t, 3)
                del open_segs[key]

    if not segments:
        st.info(f"{char_name}에게 적용된 버프 없음")
        return

    df = pd.DataFrame(segments)
    df = df[df["만료(s)"] > df["시작(s)"]]

    buff_names = df["버프명"].unique().tolist()
    fig = go.Figure(go.Bar(
        x=df["만료(s)"] - df["시작(s)"],
        y=df["버프명"],
        base=df["시작(s)"],
        orientation="h",
        showlegend=False,
        customdata=df[["시전자", "시작(s)", "만료(s)"]].values,
        hovertemplate="%{y}<br>시전: %{customdata[0]}<br>t=%{customdata[1]}s ~ %{customdata[2]}s<extra></extra>",
    ))
    fig.update_layout(
        barmode="overlay",
        xaxis_title="시간 (초)",
        xaxis=dict(range=[0, result.duration]),
        height=max(200, 30 * len(buff_names) + 60),
        margin=dict(t=20, b=40),
    )
    st.plotly_chart(fig, use_container_width=True, key=_safe_key("buff_gantt", char_name))

    st.markdown("##### 시전자별 버프 목록")
    for caster in sorted(df["시전자"].unique()):
        sub = df[df["시전자"] == caster].drop_duplicates(subset=["버프명", "시작(s)"]).copy()
        img = get_image_b64(caster)
        col_img, col_tbl = st.columns([1, 8])
        with col_img:
            if img:
                st.markdown(
                    f'<div style="width:100%;padding-top:130%;position:relative;'
                    f'overflow:hidden;border-radius:6px;">'
                    f'<img src="{img}" style="position:absolute;top:0;left:0;'
                    f'width:100%;height:100%;object-fit:cover;object-position:top;">'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.caption(caster)
        with col_tbl:
            st.dataframe(
                sub[["버프명", "시작(s)", "만료(s)"]].reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
                key=_safe_key(f"buff_tbl_{char_name}", caster),
            )


# ── 입힌 대미지 ───────────────────────────────────────────────────────────


def _damage_section(result: SimResult, char_name: str) -> None:
    char_hits = [e for e in result.hits if e.caster == char_name]
    if not char_hits:
        st.info(f"{char_name}의 히트 없음")
        return

    bd = analyze_damage(result, char_name)
    total = bd.total or 1

    # 상단 메트릭
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("총 대미지", f"{bd.total:,}")
    with m2:
        st.metric("기본 공격", f"{bd.normal_atk.damage:,}", f"{bd.normal_atk.pct(total):.1f}%", delta_color="off")
    with m3:
        st.metric("스킬", f"{bd.skill.damage:,}", f"{bd.skill.pct(total):.1f}%", delta_color="off")
    with m4:
        st.metric("히트 수", f"{len(char_hits):,}")

    # 버스트 구간별 메트릭 (log 있을 때만)
    if result.log:
        fb_total = bd.fb_self.damage + bd.fb_other.damage + bd.non_fb.damage or 1
        b1, b2, b3 = st.columns(3)
        with b1:
            st.metric("풀버스트(본인)", f"{bd.fb_self.damage:,}", f"{bd.fb_self.pct(fb_total):.1f}%", delta_color="off")
        with b2:
            st.metric("풀버스트(타인)", f"{bd.fb_other.damage:,}", f"{bd.fb_other.pct(fb_total):.1f}%", delta_color="off")
        with b3:
            st.metric("비풀버스트", f"{bd.non_fb.damage:,}", f"{bd.non_fb.pct(fb_total):.1f}%", delta_color="off")

    st.divider()

    # 스킬별 집계 표
    st.markdown("##### 스킬별 집계")
    tag_dmg: dict[tuple, int] = defaultdict(int)
    tag_cnt: dict[tuple, int] = defaultdict(int)
    for ev in char_hits:
        key = (ev.skill_name, ev.hit_tag)
        tag_dmg[key] += ev.damage
        tag_cnt[key] += 1

    rows = []
    for (skill, tag), dmg in sorted(tag_dmg.items(), key=lambda x: -x[1]):
        cnt = tag_cnt[(skill, tag)]
        rows.append({
            "스킬명": skill,
            "hit_tag": tag,
            "히트수": cnt,
            "총 대미지": dmg,
            "비율(%)": round(dmg / total * 100, 1),
            "평균": dmg // max(cnt, 1),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 key=_safe_key("skill_tbl", char_name))

    # 버스트 사이클별 스킬 대미지 차트
    if result.log:
        burst_starts = [e.t for e in result.log.burst_log if e.event == "full_burst 시작"]
        if burst_starts:
            st.markdown("##### 버스트 사이클별 스킬 대미지")
            _cycle_skill_chart(char_hits, burst_starts, result.duration, char_name)


def _cycle_skill_chart(
    char_hits: list,
    burst_starts: list[float],
    duration: float,
    char_name: str,
) -> None:
    boundaries = [0.0] + burst_starts + [math.inf]
    skills = sorted({e.skill_name for e in char_hits})
    cycle_labels = [
        f"사이클{i}" if boundaries[i + 1] != math.inf else f"사이클{i}(끝)"
        for i in range(len(boundaries) - 1)
    ]

    data: dict[str, list[int]] = defaultdict(list)
    for i in range(len(boundaries) - 1):
        t0, t1 = boundaries[i], boundaries[i + 1]
        for skill in skills:
            dmg = sum(e.damage for e in char_hits if e.skill_name == skill and t0 <= e.t < t1)
            data[skill].append(dmg)

    fig = go.Figure()
    for skill in skills:
        fig.add_trace(go.Bar(name=skill, x=cycle_labels, y=data[skill]))

    fig.update_layout(
        barmode="stack",
        xaxis_title="사이클",
        yaxis_title="대미지",
        height=300,
        margin=dict(t=20, b=40),
    )
    st.plotly_chart(fig, use_container_width=True, key=_safe_key("cycle_skill", char_name))
