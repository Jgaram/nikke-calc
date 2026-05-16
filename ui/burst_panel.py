"""버스트 & 대미지 분석 패널."""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculator.sim_result import SimResult
from ui.image_utils import get_image_b64


def render(result: SimResult) -> None:
    st.subheader("버스트 & 대미지 분석")

    _burst_section(result)
    st.divider()
    _damage_section(result)


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
                "end": None,
                "casters": dict(pending_stages),  # 복사
            })
            pending_stages.clear()
        elif e.event == "full_burst 종료" and bursts:
            bursts[-1]["end"] = e.t

    col1, col2 = st.columns(2)
    col1.metric("풀버스트 횟수", len(bursts))

    st.markdown("##### 풀버스트 목록")

    for idx, b in enumerate(bursts):
        end_str = f"{b['end']:.3f}s" if b["end"] is not None else "—"
        header = f"**#{idx+1}**  시작 {b['start']:.3f}s  /  종료 {end_str}"

        # 헤더 + 캐릭터 이미지를 한 행에
        casters = list(b["casters"].keys())
        # 이미지 최대 5칸 (여백 포함)
        img_cols = st.columns([2, 1, 1, 1, 1, 1])
        img_cols[0].markdown(header)
        for j in range(5):
            if j < len(casters):
                name = casters[j]
                img = get_image_b64(name)
                with img_cols[j + 1]:
                    if img:
                        st.image(img, width=56, caption=name)
                    else:
                        st.caption(name)
            # 빈 슬롯은 그냥 비워둠


# ── 대미지 분석 ────────────────────────────────────────────────────────────


def _damage_section(result: SimResult) -> None:
    st.markdown("#### 대미지 분석")

    chars = sorted(result.char_total.keys(), key=lambda n: -result.char_total[n])
    totals = [result.char_total[c] for c in chars]
    team_total = result.team_total or 1

    # 캐릭터 이미지 + 이름 레이블
    img_labels = []
    for c in chars:
        img = get_image_b64(c)
        img_labels.append(c)  # Plotly는 텍스트만 지원하므로 이름 사용

    # 캐릭터별 총 대미지 막대
    fig = go.Figure(go.Bar(
        x=chars,
        y=totals,
        text=[f"{v:,}<br>({v/team_total*100:.1f}%)" for v in totals],
        textposition="outside",
    ))
    fig.update_layout(yaxis_title="총 대미지", height=300, margin=dict(t=20, b=40))
    st.plotly_chart(fig, use_container_width=True)

    # 차트 아래 이미지 행
    img_row = st.columns(len(chars))
    for i, c in enumerate(chars):
        img = get_image_b64(c)
        with img_row[i]:
            if img:
                st.image(img, width=64)
            st.caption(c)

    # 스킬별 집계 표
    st.markdown("##### 스킬별 집계")

    char_filter = st.multiselect(
        "캐릭터 필터",
        options=chars,
        default=chars,
        key="burst_char_filter",
    )

    tag_dmg: dict[tuple, int] = defaultdict(int)
    tag_cnt: dict[tuple, int] = defaultdict(int)

    for ev in result.hits:
        if ev.caster not in char_filter:
            continue
        key = (ev.caster, ev.skill_name, ev.hit_tag)
        tag_dmg[key] += ev.damage
        tag_cnt[key] += 1

    rows = []
    for (caster, skill, tag), dmg in sorted(tag_dmg.items(), key=lambda x: -x[1]):
        rows.append({
            "캐릭터": caster,
            "스킬명": skill,
            "hit_tag": tag,
            "총 대미지": dmg,
            "히트수": tag_cnt[(caster, skill, tag)],
            "평균": dmg // max(tag_cnt[(caster, skill, tag)], 1),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=350)

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
