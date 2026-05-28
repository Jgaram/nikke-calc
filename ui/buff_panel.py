"""버프 & 히트 이벤트 추적 패널."""

from __future__ import annotations

import math

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
import streamlit as st

from calculator.sim_result import SimResult
from ui.image_utils import get_image_b64


def _fmt_rem(t: float, duration: float) -> str:
    """경과 시간 t → 남은 전투 시간 M:SS 문자열."""
    rem = max(0.0, duration - t)
    m, s = divmod(int(round(rem)), 60)
    return f"{m}:{s:02d}"

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

    # 재장전 행 고정: 실제 재장전이 없어도 행이 사라지지 않도록 더미 세그먼트 삽입
    segments.append({
        "버프명": "재장전",
        "stat": "",
        "시전자": char_sel,
        "시작(s)": 0.0,
        "만료(s)": 0.001,
        "값": "—",
        "_is_reload": True,
    })

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
    data_labels = list(dict.fromkeys(df["레이블"]))

    # autorange="reversed"는 categoryarray 첫 항목을 맨 위에 표시 → 빈 행을 앞에
    labels = ["　", "　　"] + data_labels

    _COLORS = pc.qualitative.Plotly
    label_color = {lbl: _COLORS[i % len(_COLORS)] for i, lbl in enumerate(data_labels)}

    fig = go.Figure()
    # 빈 행용 투명 더미 트레이스 — 트레이스 없으면 Plotly가 카테고리 자체를 렌더링하지 않음
    fig.add_trace(go.Bar(
        x=[0, 0], y=["　", "　　"], base=[0, 0], orientation="h",
        showlegend=False, hoverinfo="skip",
        marker_color="rgba(0,0,0,0)",
    ))

    # 일반 버프 / 재장전을 각각 트레이스 1개로 배칭
    norm_x, norm_y, norm_base, norm_colors, norm_hover = [], [], [], [], []
    rel_x, rel_y, rel_base, rel_hover = [], [], [], []

    duration = result.duration
    for _, row in df.iterrows():
        w = row["만료(s)"] - row["시작(s)"]
        if w <= 0:
            continue
        if row["_is_reload"]:
            rel_x.append(w)
            rel_y.append(row["레이블"])
            rel_base.append(row["시작(s)"])
            rel_hover.append(
                f"재장전<br>시간: {_fmt_rem(row['시작(s)'], duration)} ~ {_fmt_rem(row['만료(s)'], duration)}"
            )
        else:
            norm_x.append(w)
            norm_y.append(row["레이블"])
            norm_base.append(row["시작(s)"])
            norm_colors.append(label_color[row["레이블"]])
            norm_hover.append(
                f"{row['버프명']}<br>시전: {row['시전자']}<br>"
                f"값: {row['값']}<br>시간: {_fmt_rem(row['시작(s)'], duration)} ~ {_fmt_rem(row['만료(s)'], duration)}"
            )

    if norm_x:
        fig.add_trace(go.Bar(
            x=norm_x, y=norm_y, base=norm_base,
            orientation="h", showlegend=False,
            marker=dict(color=norm_colors),
            customdata=norm_hover,
            hovertemplate="%{customdata}<extra></extra>",
        ))
    if rel_x:
        fig.add_trace(go.Bar(
            x=rel_x, y=rel_y, base=rel_base,
            orientation="h", showlegend=False,
            marker=dict(color="#888888"),
            customdata=rel_hover,
            hovertemplate="%{customdata}<extra></extra>",
        ))

    has_b3_imgs = False
    if result.log:
        for entry in result.log.burst_log:
            if entry.event in ("full_burst 시작", "full_burst 종료"):
                color = "#FF6B35" if entry.event == "full_burst 시작" else "#888888"
                dash = "dot" if entry.event == "full_burst 시작" else "dash"
                fig.add_vline(x=entry.t, line=dict(color=color, dash=dash, width=1.5))

        # 버스트 시작선 위에 B3 캐릭터 이미지 표시
        pending_b3: list[str] = []
        burst_b3_list: list[tuple[float, list[str]]] = []
        for e in result.log.burst_log:
            if e.event in ("stage:3 사용", "reenter:3 사용"):
                pending_b3.append(e.caster)
            elif e.event == "full_burst 시작":
                burst_b3_list.append((e.t, list(pending_b3)))
                pending_b3 = []

        # 가로: 1행(30px) 기준 너비 고정, 세로: 2행 높이로 더 많이 보이게
        sizey = 1.8 / len(labels)
        img_h_px = 45
        img_w = max(1.5, result.duration * img_h_px / 1.3 / 750)

        for burst_t, b3_casters in burst_b3_list:
            n = len(b3_casters)
            for i, name in enumerate(b3_casters):
                img_src = get_image_b64(name)
                if not img_src:
                    continue
                fig.add_layout_image(dict(
                    source=img_src,
                    xref="x",
                    yref="paper",
                    x=burst_t + i * img_w,  # 좌측 끝을 버스트 시작선에 맞춤
                    y=1.0,
                    sizex=img_w,
                    sizey=sizey,
                    sizing="fill",           # 상단 기준 자르기 (yanchor="top"과 조합)
                    xanchor="left",
                    yanchor="top",
                    layer="above",
                ))
                has_b3_imgs = True

    tick_step = 30
    tickvals = [i * tick_step for i in range(int(result.duration / tick_step) + 1)]
    ticktext = [_fmt_rem(v, result.duration) for v in tickvals]

    fig.update_layout(
        barmode="overlay",
        xaxis_title="시간",
        xaxis=dict(range=[0, result.duration], side="top", tickvals=tickvals, ticktext=ticktext),
        yaxis=dict(autorange="reversed", categoryorder="array", categoryarray=labels),
        height=max(200, 30 * len(labels) + 60),
        margin=dict(t=30, b=40),
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

    if fig is None:
        display_name = enemy_label if char_sel == "__enemy__" else char_sel
        st.info(f"{display_name}에게 적용된 버프 없음")
        if not system_df.empty:
            st.markdown("**상시 적용 버프** (큐브 · 소장품 · 장비)")
            st.dataframe(system_df, hide_index=True, use_container_width=False)
        return

    st.plotly_chart(fig, use_container_width=True)

    _ammo_chart(result, char_sel)

    if not system_df.empty:
        st.markdown("**상시 적용 버프** (큐브 · 소장품 · 장비)")
        st.dataframe(system_df, hide_index=True, use_container_width=False)


@st.cache_data(hash_funcs={SimResult: id})
def _build_ammo_data(result: SimResult, char_sel: str):
    if not result.log or not result.log.ammo_log:
        return None, 0
    entries = [(e.t, e.ammo) for e in result.log.ammo_log if e.caster == char_sel]
    if not entries:
        return None, 0
    max_ammo = max(a for _, a in entries)
    return entries, max_ammo


def _ammo_chart(result: SimResult, char_sel: str) -> None:
    entries, max_ammo = _build_ammo_data(result, char_sel)
    if entries is None:
        return

    ts = [t for t, _ in entries]
    ammos = [a for _, a in entries]

    # 전투 끝까지 마지막 값 연장
    ts.append(result.duration)
    ammos.append(ammos[-1])

    duration = result.duration
    tick_step = 30
    tickvals = [i * tick_step for i in range(int(duration / tick_step) + 1)]
    ticktext = [_fmt_rem(v, duration) for v in tickvals]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ts, y=ammos,
        mode="lines",
        line=dict(shape="hv", color="#4C9BE8", width=1.5),
        fill="tozeroy",
        fillcolor="rgba(76,155,232,0.15)",
        hovertemplate="t=%{x:.2f}s<br>남은 탄환: %{y}<extra></extra>",
    ))

    fig.update_layout(
        xaxis=dict(range=[0, duration], side="top", tickvals=tickvals, ticktext=ticktext, title="시간"),
        yaxis=dict(range=[0, max_ammo * 1.05], title="남은 탄환"),
        height=120,
        margin=dict(t=30, b=10, l=50, r=10),
        showlegend=False,
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
