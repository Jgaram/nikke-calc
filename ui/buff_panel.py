"""버프 & 히트 이벤트 추적 패널."""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculator.sim_result import SimResult

def render(result: SimResult, team_names: list[str] | None = None) -> None:
    _buff_section(result, team_names or [])


# ── 버프 타임라인 ──────────────────────────────────────────────────────────


def _buff_section(result: SimResult, team_names: list[str]) -> None:
    st.markdown("#### 버프 타임라인")

    if not result.log or not result.log.buff_events:
        st.info("버프 이벤트 없음")
        return

    chars = sorted(result.char_total.keys())
    enemy_label = "타겟 랩쳐"
    options = chars + [enemy_label]
    sel = st.selectbox("버프 적용 대상 캐릭터", options, key="buff_char_sel")
    char_sel = "__enemy__" if sel == enemy_label else sel

    segments: list[dict] = []
    open_segs: dict[tuple, dict] = {}

    for ev in result.log.buff_events:
        if ev.target != char_sel:
            continue
        key = (ev.name, ev.caster)
        if ev.kind == "activate":
            if key in open_segs:
                open_segs[key]["만료(s)"] = round(ev.t, 3)
                segments.append(open_segs[key])
            seg = {
                "버프명": ev.name,
                "stat": ev.stat or "",
                "시전자": ev.caster,
                "시작(s)": round(ev.t, 3),
                "만료(s)": round(min(ev.expires_at, result.duration) if ev.expires_at != math.inf else result.duration, 3),
                "값": _fmt_value(ev.stat, ev.value),
            }
            open_segs[key] = seg
            segments.append(seg)
        elif ev.kind == "expire":
            if key in open_segs:
                open_segs[key]["만료(s)"] = round(ev.t, 3)
                del open_segs[key]

    if not segments:
        display_name = enemy_label if char_sel == "__enemy__" else char_sel
        st.info(f"{display_name}에게 적용된 버프 없음")
        return

    df = pd.DataFrame(segments)
    df["레이블"] = df.apply(_make_label, axis=1)

    # 시전자 기준 정렬 (팀 순서 우선, 없으면 가나다순)
    caster_order = {n: i for i, n in enumerate(team_names)} if team_names else {}
    df["_sort_key"] = df["시전자"].map(lambda c: caster_order.get(c, 999))
    df = df.sort_values(["_sort_key", "레이블"])
    labels = list(dict.fromkeys(df["레이블"]))  # 순서 유지 중복 제거

    fig = go.Figure()
    for label in labels:
        sub = df[df["레이블"] == label]
        for _, row in sub.iterrows():
            width = row["만료(s)"] - row["시작(s)"]
            if width <= 0:
                continue
            bname = row["버프명"]
            is_system = bname.startswith("장비") or bname.startswith("소장품") or bname.startswith("큐브")
            fig.add_trace(go.Bar(
                x=[width],
                y=[label],
                base=[row["시작(s)"]],
                orientation="h",
                showlegend=False,
                marker_color="#666666" if is_system else None,
                hovertemplate=(
                    f"{bname}<br>시전: {row['시전자']}<br>"
                    f"값: {row['값']}<br>"
                    f"t={row['시작(s)']}s ~ {row['만료(s)']}s<extra></extra>"
                ),
            ))

    fig.update_layout(
        barmode="overlay",
        xaxis_title="시간 (초)",
        xaxis=dict(range=[0, result.duration]),
        height=max(200, 30 * len(labels) + 60),
        margin=dict(t=20, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)


def _make_label(r) -> str:
    caster = r["시전자"]
    buff = r["버프명"]
    stat = r["stat"]
    val = r["값"]

    inner = ""
    if stat and val != "—":
        inner = f"{stat} {val}"
    elif stat:
        inner = stat
    elif val != "—":
        inner = val

    return f"[{caster}] {buff} ({inner})" if inner else f"[{caster}] {buff}"


def _fmt_value(stat: str | None, value: float | None) -> str:
    if value is None:
        return "—"
    if stat and "pct" in stat:
        return f"{value:g}%"
    return f"{value:g}"
