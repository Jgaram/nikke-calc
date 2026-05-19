"""스킬 원문 패널 — 시뮬에 사용된 캐릭터의 스킬 설명 표시."""

from __future__ import annotations

import json
import os
import re

import streamlit as st

_SCRAPED_PATH = os.path.join(os.path.dirname(__file__), "..", "scraper", "nikke_scraped.json")
_SKILL_LABELS = ["스킬 1", "스킬 2", "버스트"]


@st.cache_data
def _load_scraped() -> dict:
    with open(_SCRAPED_PATH, encoding="utf-8") as f:
        return json.load(f)


def _fill_template(template: str, values: list[str]) -> str:
    result = template
    for i, v in enumerate(values):
        result = result.replace(f"{{{i}}}", v)
    return result


def _render_skill_card(skill_label: str, skill_name: str, data: dict, level: int) -> None:
    cooldown = data.get("쿨타임")
    template = data.get("template", "")
    values_map = data.get("values", {})

    lv_key = str(level)
    if lv_key not in values_map:
        lv_key = max(values_map.keys(), key=int) if values_map else None

    filled = _fill_template(template, values_map[lv_key]) if lv_key else template

    cd_str = f" · 쿨타임 {cooldown}" if cooldown else ""
    st.markdown(f"**{skill_label} — {skill_name}**{cd_str}  \n`Lv {level}`")
    st.markdown(filled)


def render(team_names: list[str], skill_levels: dict[str, dict[str, int]]) -> None:
    """
    team_names: 팀 캐릭터명 리스트
    skill_levels: {캐릭터명: {"1": lv, "2": lv, "3": lv}}
    """
    scraped = _load_scraped()

    for name in team_names:
        st.markdown(f"### {name}")
        if name not in scraped:
            st.warning(f"`{name}` — 스크레이프 데이터 없음")
            continue

        skills_data = scraped[name].get("스킬", {})
        skill_entries = list(skills_data.items())  # [(스킬명, 데이터), ...]
        char_levels = skill_levels.get(name, {"1": 10, "2": 10, "3": 10})

        cols = st.columns(3)
        for idx, (skill_name, skill_data) in enumerate(skill_entries[:3]):
            level = char_levels.get(str(idx + 1), 10)
            with cols[idx]:
                _render_skill_card(_SKILL_LABELS[idx], skill_name, skill_data, level)

        st.divider()
