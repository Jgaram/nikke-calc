"""버프 & 히트 이벤트 추적 패널."""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculator.sim_result import SimResult
from ui.image_utils import get_image_b64


def render(result: SimResult) -> None:
    st.subheader("버프 추적")

    _buff_section(result)


# ── 버프 스냅샷 ────────────────────────────────────────────────────────────


def _buff_section(result: SimResult) -> None:
    st.markdown("#### 버프 타임라인")

    if not result.log or not result.log.buff_events:
        st.info("버프 이벤트 없음")
        return

    chars = sorted(result.char_total.keys())
    char_sel = st.selectbox("버프 적용 대상 캐릭터", chars, key="buff_char_sel")

    # buff_events에서 (버프명, 시전자) 단위로 활성 구간 목록을 재구성한다.
    # activate 이벤트가 올 때마다 새 구간 시작, expire 이벤트에서 닫는다.
    # 영구 버프는 expire 이벤트가 없으므로 전투 종료 시각으로 닫는다.
    segments: list[dict] = []  # {버프명, 시전자, 시작, 만료}
    open_segs: dict[tuple, dict] = {}  # (버프명, 시전자) → 현재 열린 구간

    for ev in result.log.buff_events:
        if ev.target != char_sel:
            continue
        key = (ev.name, ev.caster)
        if ev.kind == "activate":
            if key in open_segs:
                # 재발동: 이전 구간 닫고 새 구간 열기
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

    if segments:
        df = pd.DataFrame(segments)

        # Gantt 차트
        def _make_label(r):
            parts = []
            if r["stat"]:
                parts.append(r["stat"])
            if r["값"] != "—":
                parts.append(f"({r['값']})")
            suffix = " ".join(parts)
            return f"[{r['버프명']}] {suffix}".strip() if suffix else f"[{r['버프명']}]"
        df["레이블"] = df.apply(_make_label, axis=1)
        fig = go.Figure()
        labels = df["레이블"].unique().tolist()
        for label in labels:
            sub = df[df["레이블"] == label]
            bname = sub.iloc[0]["버프명"]
            for _, row in sub.iterrows():
                width = row["만료(s)"] - row["시작(s)"]
                if width <= 0:
                    continue
                fig.add_trace(go.Bar(
                    x=[width],
                    y=[label],
                    base=[row["시작(s)"]],
                    orientation="h",
                    showlegend=False,
                    hovertemplate=(
                        f"{bname}<br>값: {row['값']}<br>시전: {row['시전자']}<br>"
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

        # 표 + 시전자 이미지
        st.markdown("##### 버프 목록")
        unique_casters = df["시전자"].unique().tolist()
        for caster in unique_casters:
            sub = df[df["시전자"] == caster].drop_duplicates(subset=["버프명", "시작(s)"]).copy()
            img = get_image_b64(caster)
            col_img, col_tbl = st.columns([1, 6])
            with col_img:
                if img:
                    st.image(img, width=64)
                st.caption(caster)
            with col_tbl:
                st.dataframe(
                    sub[["버프명", "stat", "값", "시작(s)", "만료(s)"]].reset_index(drop=True),
                    use_container_width=True,
                    hide_index=True,
                )
    else:
        st.info(f"{char_sel}에게 적용된 버프 없음")


def _fmt_value(stat: str | None, value: float | None) -> str:
    if value is None:
        return "—"
    if stat and "pct" in stat:
        return f"{value:g}%"
    return f"{value:g}"

