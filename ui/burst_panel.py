"""버스트 & 대미지 분석 패널."""

from __future__ import annotations

import bisect
import math
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculator.sim_result import SimResult
from ui.image_utils import get_image_b64


def _fmt_rem(t: float, duration: float) -> str:
    rem = max(0.0, duration - t)
    m, s = divmod(int(round(rem)), 60)
    return f"{m}:{s:02d}"


def render_overview(result: SimResult) -> None:
    st.subheader("대미지 분석")
    _damage_section(result)


def render_burst_hits(result: SimResult) -> None:
    st.subheader("버스트별 히트 수")
    _burst_section(result)


# ── 버스트 타임라인 ────────────────────────────────────────────────────────


def _burst_section(result: SimResult) -> None:
    st.markdown("#### 버스트 타임라인")

    if not result.log:
        st.info("버스트 로그 없음")
        return

    log = result.log

    # 풀버스트 단위로 묶기
    # burst_log 순서: stage:N 사용... → full_burst 시작 → full_burst 종료
    bursts: list[dict] = []
    pending_stages: dict[str, float] = {}  # caster → t

    for e in log.burst_log:
        if e.event.startswith("stage:") or e.event.startswith("reenter:"):
            pending_stages[e.caster] = e.t
        elif e.event == "full_burst 시작":
            bursts.append({
                "start": e.t,
                "stage_start": min(pending_stages.values()) if pending_stages else e.t,
                "end": None,
                "casters": dict(pending_stages),  # 복사
            })
            pending_stages.clear()
        elif e.event == "full_burst 종료" and bursts:
            bursts[-1]["end"] = e.t

    col1, col2 = st.columns(2)
    col1.metric("풀버스트 횟수", len(bursts))

    all_chars = sorted(result.char_total.keys())
    selected_char = st.radio(
        "히트 상세 캐릭터",
        options=["전체"] + all_chars,
        horizontal=True,
        key="burst_char_radio",
    )

    st.markdown("##### 풀버스트 목록")

    for idx, b in enumerate(bursts):
        end_str = _fmt_rem(b["end"], result.duration) if b["end"] is not None else "—"
        header = f"**#{idx+1}**  시작 {_fmt_rem(b['start'], result.duration)}  /  종료 {end_str}"

        casters = list(b["casters"].keys())
        t0 = b["stage_start"]  # 버스트 1단계 사용 시점부터
        t1 = b["end"] if b["end"] is not None else math.inf

        total_burst_dmg = sum(e.damage for e in result.hits if t0 <= e.t < t1)

        # 레이아웃: 헤더 | 이미지들(한 칸) | 딜량 텍스트
        col_header, col_imgs, col_stats = st.columns([4, 2, 3])

        col_header.markdown(header)

        # 이미지들을 한 칸 안에서 가로로 붙여넣기 (HTML flex)
        imgs_html = '<div style="display:flex;gap:2px;align-items:flex-start;">'
        for name in casters:
            img = get_image_b64(name)
            if img:
                imgs_html += (
                    f'<div style="flex:1;min-width:0;">'
                    f'<div style="width:100%;padding-top:130%;position:relative;overflow:hidden;border-radius:3px;">'
                    f'<img src="{img}" style="position:absolute;top:0;left:0;width:100%;height:100%;'
                    f'object-fit:cover;object-position:top;"></div></div>'
                )
            else:
                imgs_html += f'<div style="flex:1;font-size:10px;color:#888;">{name}</div>'
        imgs_html += '</div>'
        col_imgs.markdown(imgs_html, unsafe_allow_html=True)

        col_stats.markdown(
            f'<div style="font-size:12px;color:#ccc;line-height:1.6;">{total_burst_dmg:,}</div>',
            unsafe_allow_html=True,
        )

        # 버스트 구간 히트 상세
        burst_hits = [e for e in result.hits if t0 <= e.t < t1]

        filtered = burst_hits if selected_char == "전체" else [e for e in burst_hits if e.caster == selected_char]
        with st.expander(f"#{idx+1} 구간 히트 상세"):
            if not filtered:
                st.info("이 구간에 히트 없음")
            else:
                tag_dmg: dict[tuple, int] = defaultdict(int)
                tag_cnt: dict[tuple, int] = defaultdict(int)
                for ev in filtered:
                    key = (ev.caster, ev.skill_name, ev.hit_tag)
                    tag_dmg[key] += ev.damage
                    tag_cnt[key] += 1

                rows = []
                for (caster, skill, tag), dmg in sorted(tag_dmg.items(), key=lambda x: -x[1]):
                    cnt = tag_cnt[(caster, skill, tag)]
                    rows.append({
                        "캐릭터": caster,
                        "스킬명": skill,
                        "hit_tag": tag,
                        "히트수": cnt,
                        "총 대미지": dmg,
                        "평균": dmg // max(cnt, 1),
                    })

                st.dataframe(pd.DataFrame(rows), use_container_width=True)


# ── 대미지 분석 ────────────────────────────────────────────────────────────


@st.cache_data(hash_funcs={SimResult: id})
def _build_damage_fig(result: SimResult) -> tuple[go.Figure, list[str]]:
    chars = sorted(result.char_total.keys(), key=lambda n: -result.char_total[n])
    totals = [result.char_total[c] for c in chars]
    team_total = result.squad_total or 1

    skill_dmg: dict[str, dict[str, int]] = {c: defaultdict(int) for c in chars}
    for ev in result.hits:
        if ev.caster in skill_dmg:
            skill_dmg[ev.caster][ev.skill_name] += ev.damage
    all_skills = sorted({s for c in chars for s in skill_dmg[c]})

    fig = go.Figure()
    for skill in all_skills:
        fig.add_trace(go.Bar(
            name=skill,
            x=chars,
            y=[skill_dmg[c][skill] for c in chars],
            hovertemplate="%{x}<br>" + skill + "<br>%{y:,}<extra></extra>",
            text=[
                f"{skill_dmg[c][skill]/result.char_total[c]*100:.0f}%"
                if result.char_total[c] and skill_dmg[c][skill]/result.char_total[c] >= 0.05
                else ""
                for c in chars
            ],
            textposition="inside",
            insidetextanchor="middle",
        ))
    for c, v in zip(chars, totals):
        fig.add_annotation(
            x=c, y=v,
            text=f"<b>{v:,}</b><br>{v/team_total*100:.1f}%",
            showarrow=False, yanchor="bottom",
            font=dict(size=11), align="center",
        )
    fig.update_layout(
        barmode="stack",
        yaxis_title="총 대미지",
        height=380,
        margin=dict(t=50, b=40),
        legend=dict(orientation="h", y=-0.2),
    )
    return fig, chars


@st.cache_data(hash_funcs={SimResult: id})
def _build_cycle_fig(result: SimResult, chars: tuple[str, ...]) -> go.Figure | None:
    if not result.log:
        return None
    burst_starts = [e.t for e in result.log.burst_log if e.event == "full_burst 시작"]
    if not burst_starts:
        return None

    boundaries = [0.0] + burst_starts + [math.inf]
    n = len(boundaries) - 1
    data: dict[str, list[int]] = {c: [0] * n for c in chars}

    for e in result.hits:
        if e.caster not in data:
            continue
        idx = bisect.bisect_right(boundaries, e.t) - 1
        if 0 <= idx < n:
            data[e.caster][idx] += e.damage

    cycle_labels = [
        f"사이클{i}" if i < n - 1 else f"사이클{i}(끝)"
        for i in range(n)
    ]

    fig = go.Figure()
    for c in chars:
        fig.add_trace(go.Bar(name=c, x=cycle_labels, y=data[c]))
    fig.update_layout(
        barmode="stack",
        xaxis_title="사이클",
        yaxis_title="대미지",
        height=300,
        margin=dict(t=20, b=40),
    )
    return fig


def _damage_section(result: SimResult) -> None:
    st.markdown("#### 대미지 분석")

    fig, chars = _build_damage_fig(result)
    st.plotly_chart(fig, use_container_width=True)

    cycle_fig = _build_cycle_fig(result, tuple(chars))
    if cycle_fig is not None:
        st.markdown("##### 버스트 사이클별 대미지")
        st.plotly_chart(cycle_fig, use_container_width=True)


