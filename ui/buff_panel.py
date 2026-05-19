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


@st.cache_data(hash_funcs={SimResult: id})
def _build_buff_data(
    result: SimResult, char_sel: str, team_names: tuple[str, ...]
) -> tuple[go.Figure | None, pd.DataFrame]:
    """차트 세그먼트 + 시스템 버프 테이블을 함께 반환."""
    segments: list[dict] = []
    system_rows: list[dict] = []
    open_segs: dict[tuple, dict] = {}

    for ev in result.log.buff_events:
        if ev.target != char_sel:
            continue
        bname = ev.name
        is_system = bname.startswith("장비") or bname.startswith("소장품") or bname.startswith("큐브")
        key = (bname, ev.caster)

        if ev.kind == "activate":
            if key in open_segs:
                open_segs[key]["만료(s)"] = round(ev.t, 3)
                if not is_system:
                    segments.append(open_segs[key])
            seg = {
                "버프명": bname,
                "stat": ev.stat or "",
                "시전자": ev.caster,
                "시작(s)": round(ev.t, 3),
                "만료(s)": round(
                    min(ev.expires_at, result.duration) if ev.expires_at != math.inf else result.duration,
                    3,
                ),
                "값": _fmt_value(ev.stat, ev.value),
                "_is_reload": False,
            }
            open_segs[key] = seg
            if is_system:
                system_rows.append(seg)
            else:
                segments.append(seg)
        elif ev.kind == "expire":
            if key in open_segs:
                open_segs[key]["만료(s)"] = round(ev.t, 3)
                del open_segs[key]

    # 재장전 구간 추가
    if result.log:
        start_t: float | None = None
        for e in result.log.reload_log:
            if e.caster != char_sel:
                continue
            if e.event == "재장전 시작":
                start_t = e.t
            elif e.event == "재장전 완료" and start_t is not None:
                segments.append({
                    "버프명": "재장전",
                    "stat": "",
                    "시전자": char_sel,
                    "시작(s)": round(start_t, 3),
                    "만료(s)": round(e.t, 3),
                    "값": "—",
                    "_is_reload": True,
                })
                start_t = None

    # 시스템 버프 테이블 (중복 제거)
    if system_rows:
        system_df = pd.DataFrame(system_rows)[["버프명", "stat", "값"]].drop_duplicates(
            subset=["버프명", "stat"]
        ).reset_index(drop=True)
        system_df = system_df[~(system_df["버프명"].str.startswith("장비") & system_df["값"].isin(["0%", "0"]))]
    else:
        system_df = pd.DataFrame()

    if not segments:
        return None, system_df

    df = pd.DataFrame(segments)
    df["레이블"] = df.apply(_make_label, axis=1)

    # 정렬: 재장전(-1) → 선택 캐릭터(0) → 팀 순서(1~)
    caster_order: dict[str, int] = {char_sel: 0}
    rank = 1
    for n in team_names:
        if n != char_sel:
            caster_order[n] = rank
            rank += 1

    df["_sort_key"] = df.apply(
        lambda r: -1 if r["_is_reload"] else caster_order.get(r["시전자"], 999),
        axis=1,
    )
    df = df.sort_values(["_sort_key", "레이블"])
    labels = list(dict.fromkeys(df["레이블"]))

    fig = go.Figure()
    for label in labels:
        sub = df[df["레이블"] == label]
        for _, row in sub.iterrows():
            width = row["만료(s)"] - row["시작(s)"]
            if width <= 0:
                continue
            is_reload = row["_is_reload"]
            fig.add_trace(go.Bar(
                x=[width],
                y=[label],
                base=[row["시작(s)"]],
                orientation="h",
                showlegend=False,
                marker_color="#888888" if is_reload else None,
                hovertemplate=(
                    f"재장전<br>t={row['시작(s)']}s ~ {row['만료(s)']}s<extra></extra>"
                    if is_reload else
                    f"{row['버프명']}<br>시전: {row['시전자']}<br>"
                    f"값: {row['값']}<br>"
                    f"t={row['시작(s)']}s ~ {row['만료(s)']}s<extra></extra>"
                ),
            ))

    if result.log:
        for entry in result.log.burst_log:
            if entry.event in ("full_burst 시작", "full_burst 종료"):
                color = "#FF6B35" if entry.event == "full_burst 시작" else "#888888"
                dash = "dot" if entry.event == "full_burst 시작" else "dash"
                fig.add_vline(x=entry.t, line=dict(color=color, dash=dash, width=1.5))

    fig.update_layout(
        barmode="overlay",
        xaxis_title="시간 (초)",
        xaxis=dict(range=[0, result.duration], side="top"),
        yaxis=dict(autorange="reversed"),
        height=max(200, 30 * len(labels) + 60),
        margin=dict(t=20, b=40),
    )
    return fig, system_df


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

    fig, system_df = _build_buff_data(result, char_sel, tuple(team_names))

    if not system_df.empty:
        st.markdown("**상시 적용 버프** (큐브 · 소장품 · 장비)")
        st.dataframe(system_df, hide_index=True, use_container_width=False)

    if fig is None:
        display_name = enemy_label if char_sel == "__enemy__" else char_sel
        st.info(f"{display_name}에게 적용된 버프 없음")
        return

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
