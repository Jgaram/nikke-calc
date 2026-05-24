"""버스트 & 대미지 분석 패널."""

from __future__ import annotations

import bisect
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculator.base_stat import calc_base_stats
from calculator.sim_result import SimResult
from ui.image_utils import get_image_b64


def _fmt_rem(t: float, duration: float) -> str:
    rem = max(0.0, duration - t)
    m, s = divmod(int(round(rem)), 60)
    return f"{m}:{s:02d}"


def render_overview(result: SimResult) -> None:
    st.subheader("대미지 분석")
    _base_stat_section()
    _damage_section(result)


def _base_stat_section() -> None:
    squad: list[dict] | None = st.session_state.get("squad")
    if not squad:
        return

    rows = []
    for char in squad:
        s = calc_base_stats(char)
        rows.append({
            "캐릭터": char["name"],
            "공격력": f"{s['atk']:,}",
            "체력":   f"{s['hp']:,}",
            "방어력": f"{s['def']:,}",
        })

    st.markdown("#### 기본 스탯")
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_burst_hits(result: SimResult) -> None:
    st.subheader("버스트별 히트 수")
    _burst_section(result)


# ── 버스트 타임라인 ────────────────────────────────────────────────────────


def _compute_bursts(result: SimResult) -> list[dict]:
    """burst_log에서 풀버스트 목록을 계산. stage_start = B1 사용 시점."""
    if not result.log:
        return []
    bursts: list[dict] = []
    pending_stages: dict[str, float] = {}
    for e in result.log.burst_log:
        if e.event.startswith("stage:") or e.event.startswith("reenter:"):
            pending_stages[e.caster] = e.t
        elif e.event == "full_burst 시작":
            bursts.append({
                "start": e.t,
                "stage_start": min(pending_stages.values()) if pending_stages else e.t,
                "end": None,
                "casters": dict(pending_stages),
            })
            pending_stages.clear()
        elif e.event == "full_burst 종료" and bursts:
            bursts[-1]["end"] = e.t
    return bursts


def _burst_section(result: SimResult) -> None:
    st.markdown("#### 버스트 타임라인")

    if not result.log:
        st.info("버스트 로그 없음")
        return

    bursts = _compute_bursts(result)

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
        # 구간: 현재 버스트의 B1 사용 시점 → 다음 버스트의 B1 사용 시점 (미포함)
        t0 = b["stage_start"]
        t1 = bursts[idx + 1]["stage_start"] if idx + 1 < len(bursts) else result.duration

        # 레이아웃: 헤더 | 이미지들
        col_header, col_imgs = st.columns([4, 2])

        col_header.markdown(header)

        # 이미지들을 한 칸 안에서 가로로 붙여넣기 (HTML flex)
        imgs_html = '<div style="display:flex;gap:2px;align-items:flex-start;">'
        for name in casters:
            img = get_image_b64(name)
            if img:
                imgs_html += (
                    f'<div style="width:30px;flex-shrink:0;">'
                    f'<div style="width:100%;padding-top:130%;position:relative;overflow:hidden;border-radius:3px;">'
                    f'<img src="{img}" style="position:absolute;top:0;left:0;width:100%;height:100%;'
                    f'object-fit:cover;object-position:top;"></div></div>'
                )
            else:
                imgs_html += f'<div style="width:30px;flex-shrink:0;font-size:9px;color:#888;">{name}</div>'
        imgs_html += '</div>'
        col_imgs.markdown(imgs_html, unsafe_allow_html=True)

        # 버스트 구간 히트 상세
        burst_hits = [e for e in result.hits if t0 <= e.t < t1]
        fb_start = b["start"]
        fb_end   = b["end"]

        filtered = burst_hits if selected_char == "전체" else [e for e in burst_hits if e.caster == selected_char]
        expander_label = f"#{idx+1} 구간 히트 상세 ({_fmt_rem(t0, result.duration)} - {_fmt_rem(t1, result.duration)})"
        with st.expander(expander_label):
            if not filtered:
                st.info("이 구간에 히트 없음")
            else:
                def _fb_state(t: float) -> str:
                    if fb_end is not None and fb_start <= t <= fb_end:
                        return "풀버스트"
                    return "비버스트"

                # 팰릿: 동일 시각·캐릭터·스킬의 hit들을 하나의 샷으로 묶기
                shot_groups: dict[tuple, list] = defaultdict(list)
                non_pellet: list = []
                for ev in filtered:
                    if ev.hit_tag.startswith("pellet:") or ev.hit_tag.startswith("core:pellet:"):
                        shot_groups[(round(ev.t, 6), ev.caster, ev.skill_name, _fb_state(ev.t))].append(ev)
                    else:
                        non_pellet.append(ev)

                tag_dmg: dict[tuple, int] = defaultdict(int)
                tag_cnt: dict[tuple, int] = defaultdict(int)
                tag_vals: dict[tuple, list[int]] = defaultdict(list)

                for (_, caster, skill_name, fb), group in shot_groups.items():
                    pps = len(group)
                    key = (caster, skill_name, f"팰릿 {pps}발", fb)
                    shot_dmg = sum(e.damage for e in group)
                    tag_dmg[key] += shot_dmg
                    tag_cnt[key] += 1
                    tag_vals[key].append(shot_dmg)

                for ev in non_pellet:
                    key = (ev.caster, ev.skill_name, ev.hit_tag, _fb_state(ev.t))
                    tag_dmg[key] += ev.damage
                    tag_cnt[key] += 1
                    tag_vals[key].append(ev.damage)

                rows = []
                for (caster, skill, tag, fb), dmg in sorted(tag_dmg.items(), key=lambda x: -x[1]):
                    cnt = tag_cnt[(caster, skill, tag, fb)]
                    vals = tag_vals[(caster, skill, tag, fb)]
                    freq: dict[int, int] = defaultdict(int)
                    for v in vals:
                        freq[v] += 1
                    mode_val = max(freq, key=lambda v: (freq[v], v))
                    rows.append({
                        "캐릭터": caster,
                        "스킬명": skill,
                        "hit_tag": tag,
                        "버스트": fb,
                        "히트수": cnt,
                        "총 대미지": f"{dmg:,}",
                        "최빈값": f"{mode_val:,}",
                    })

                total_dmg_sum = sum(tag_dmg.values())
                rows.append({
                    "캐릭터": "합계",
                    "스킬명": "",
                    "hit_tag": "",
                    "버스트": "",
                    "히트수": "",
                    "총 대미지": f"{total_dmg_sum:,}",
                    "최빈값": "",
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
        showlegend=False,
    )
    return fig, chars


@st.cache_data(hash_funcs={SimResult: id})
def _build_cycle_fig(result: SimResult, chars: tuple[str, ...], stage_starts: tuple[float, ...]) -> go.Figure | None:
    if not stage_starts:
        return None

    # 경계: 0 → B1_1 사용 → B1_2 사용 → … → 시뮬 종료
    boundaries = [0.0] + list(stage_starts) + [result.duration]
    n = len(boundaries) - 1
    data: dict[str, list[int]] = {c: [0] * n for c in chars}

    for e in result.hits:
        if e.caster not in data:
            continue
        i = bisect.bisect_right(boundaries, e.t) - 1
        if 0 <= i < n:
            data[e.caster][i] += e.damage

    dur = result.duration
    cycle_labels: list[str] = []
    for i in range(n):
        t_start = boundaries[i]
        t_end   = boundaries[i + 1]
        if i == 0 and t_start == 0.0:
            label = f"사이클0 (~{_fmt_rem(t_end, dur)})"
        else:
            label = f"사이클{i} ({_fmt_rem(t_start, dur)}-{_fmt_rem(t_end, dur)})"
        cycle_labels.append(label)

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

    bursts = _compute_bursts(result)
    stage_starts = tuple(b["stage_start"] for b in bursts)
    cycle_fig = _build_cycle_fig(result, tuple(chars), stage_starts)
    if cycle_fig is not None:
        st.markdown("##### 버스트 사이클별 대미지")
        st.plotly_chart(cycle_fig, use_container_width=True)


