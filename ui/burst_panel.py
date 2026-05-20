"""버스트 & 대미지 분석 패널."""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculator.sim_result import SimResult
from ui.image_utils import get_image_b64


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
        end_str = f"{b['end']:.3f}s" if b["end"] is not None else "—"
        header = f"**#{idx+1}**  시작 {b['start']:.3f}s  /  종료 {end_str}"

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


def _damage_section(result: SimResult) -> None:
    st.markdown("#### 대미지 분석")

    chars = sorted(result.char_total.keys(), key=lambda n: -result.char_total[n])
    totals = [result.char_total[c] for c in chars]
    team_total = result.squad_total or 1

    # 캐릭터 이미지 + 이름 레이블
    img_labels = []
    for c in chars:
        img = get_image_b64(c)
        img_labels.append(c)  # Plotly는 텍스트만 지원하므로 이름 사용

    # 캐릭터별 총 대미지 막대 + 스킬별 비율 스택
    from collections import defaultdict as _dd
    # 캐릭터 × 스킬명 딜량 집계
    skill_dmg: dict[str, dict[str, int]] = {c: _dd(int) for c in chars}
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
            # 호버: 해당 스킬 정보만
            hovertemplate="%{x}<br>" + skill + "<br>%{y:,}<extra></extra>",
            # 내부 텍스트: 스킬별 비율
            text=[
                f"{skill_dmg[c][skill]/result.char_total[c]*100:.0f}%"
                if result.char_total[c] and skill_dmg[c][skill]/result.char_total[c] >= 0.05
                else ""
                for c in chars
            ],
            textposition="inside",
            insidetextanchor="middle",
        ))
    # 막대 위: 캐릭터 총 딜량 + 팀 기여 비율
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
    st.plotly_chart(fig, use_container_width=True)

    # 차트 아래 이미지 행
    img_row = st.columns(len(chars))
    for i, c in enumerate(chars):
        img = get_image_b64(c)
        with img_row[i]:
            if img:
                st.image(img, width=64)
            st.caption(c)

    # 버스트 사이클별 대미지
    if result.log:
        burst_starts = [e.t for e in result.log.burst_log if e.event == "full_burst 시작"]
        if burst_starts:
            st.markdown("##### 버스트 사이클별 대미지")
            _cycle_chart(result, burst_starts, chars)


def _cycle_chart(result: SimResult, burst_starts: list[float], chars: list[str]) -> None:
    boundaries = [0.0] + burst_starts + [math.inf]
    cycle_labels = []
    data: dict[str, list[int]] = defaultdict(list)

    for i in range(len(boundaries) - 1):
        t0, t1 = boundaries[i], boundaries[i + 1]
        label = f"사이클{i}" if t1 != math.inf else f"사이클{i}(끝)"
        cycle_labels.append(label)
        for c in chars:
            dmg = sum(e.damage for e in result.hits if e.caster == c and t0 <= e.t < t1)
            data[c].append(dmg)

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
    st.plotly_chart(fig, use_container_width=True)
