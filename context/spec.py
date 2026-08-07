"""기본 육성 스펙 + 캐릭터별 기본 레이어 (Claude 전용 러너 공용).

`simulate()`에 넘길 캐릭터 dict를 만드는 유일한 자리다. 러너 셋이 전부 여기를 쓴다 —
`context/snapshot.py`(회귀 하네스) · `context/sim.py`(단발 CLI) ·
`.claude/skills/report/report.py`(딜량 보고서). 세 도구의 총딜을 서로 비교할 수 있는 건
기본 스펙이 하나이기 때문이다.

합성 순서 (뒤가 이긴다, dict는 재귀 병합 / 리스트·스칼라는 교체):

    DEFAULT_CHAR  →  data/char_defaults.json[이름]  →  호출자 오버라이드

**`calculator/`는 이 모듈을 임포트하지 않는다.** `timeline.simulate()`는 넘겨받은 캐릭터
dict만 보고, 기본 컨트롤·장비 옵션을 스스로 채우지 않는다 — 기본값이 시뮬 결과를 소리 없이
바꾸면 안 되기 때문이다(context/CONTROL.md). 레이어를 얹는 책임은 언제나 러너 쪽에 있다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# ── 기본 육성 스펙 ─────────────────────────────────────────────────────────
# 정본. 항목 근거·의미는 context/HARNESS.md §기본 스펙.
DEFAULT_CHAR: dict = {
    "level": 400,
    "breakthrough": 3,
    "core_enhancement": 0,
    "affinity": 30,
    "skill_levels": {"1": 10, "2": 10, "3": 10},
    "burst_regen_time": 2.0,
    "weapon_mode_swap": False,
    "equipment": {p: {"level": 5, "skills": []} for p in ("머리", "몸통", "팔", "다리")},
    "equip_skills": {
        "atk_pct": 20,
        "element_bonus": 80,
        "max_ammo_pct": 120,
        "crit_rate": 0,
        "crit_dmg": 0,
        "charge_speed_pct": 0,
        "charge_dmg_pct": 0,
        "accuracy_pct": 0,
        "def_pct": 0,
    },
    "cube": {"name": "재장", "level": 15},
    "console": {"common_level": 180, "class_level": 100, "company_level": 100},
    "collection_stage": "SR15",
    "control": {},
}


def _load_char_defaults() -> dict[str, dict]:
    with open(_ROOT / "data" / "char_defaults.json", encoding="utf-8") as f:
        d = json.load(f)
    return {k: v for k, v in d.items() if not k.startswith("_")}


CHAR_DEFAULTS: dict[str, dict] = _load_char_defaults()


def deep_merge(base: dict, over: dict | None) -> dict:
    """dict를 재귀 병합한다 (over 우선). 리스트는 통째로 교체."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k.startswith("_"):      # `_note` 같은 주석 키는 시뮬에 넘기지 않는다
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def char_layer(name: str) -> dict:
    """캐릭터별 기본 레이어(장비 옵션·컨트롤 차이분). 없으면 빈 dict."""
    return CHAR_DEFAULTS.get(name, {})


def build_char(name: str, over: dict | None = None, base: dict | None = None,
               no_layer: bool = False) -> dict:
    """이름 → `simulate()`에 넘길 캐릭터 dict 하나.

    base     : 기본 스펙을 갈아끼울 때만 준다 (보고서 스펙의 `defaults` 등). 기본은 DEFAULT_CHAR.
    over     : 이 캐릭터만의 오버라이드. **캐릭터별 기본 레이어보다 우선한다.**
    no_layer : 레이어를 아예 건너뛴다. 재귀 병합이라 `{"control": {}}`를 얹는 걸로는
               기본 컨트롤이 지워지지 않기 때문에, 끄려면 이 플래그를 쓴다.
    """
    c = copy.deepcopy(base or DEFAULT_CHAR)
    if not no_layer:
        c = deep_merge(c, char_layer(name))
    c = deep_merge(c, over)
    c["name"] = name
    return c


def build_squad(names: list[str], chars: dict[str, dict] | None = None,
                base: dict | None = None, no_layer: set[str] | None = None) -> list[dict]:
    """이름 목록 → 캐릭터 dict 목록. `chars`는 캐릭터별 오버라이드."""
    over = chars or {}
    skip = no_layer or set()
    return [build_char(n, over.get(n), base, n in skip) for n in names]


# ── 1층 이탈 보고 ──────────────────────────────────────────────────────────
# 규칙: **1층(기본 육성 스펙 · 컨트롤 자동)이 아닌 상태로 돌린 결과는 언제나 그 사실을
# 함께 낸다.** 레이어든 호출자 오버라이드든 마찬가지다 — 수치만 보고 "기본 스펙 결과"로
# 오해하는 게 이 프로젝트에서 가장 조용히 틀리는 경로라서, 러너가 출력에 강제로 싣는다.
# 유저에게 답할 때도 이 줄을 그대로 옮긴다.

_SKIP_KEYS = ("name", "equipment")  # equipment는 부위별 dict라 노이즈만 된다


def _fmt(v) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}={_fmt(x)}" for k, x in v.items()) + "}" if v else "없음"
    return str(v)


def _flatten(d: dict, prefix: str = "") -> dict:
    """중첩 dict → `키.경로` 평탄화. `control.<정책>`은 통째로 한 줄이 되게 거기서 멈춘다."""
    out: dict = {}
    for k, v in d.items():
        if not prefix and k in _SKIP_KEYS:
            continue
        key = f"{prefix}{k}"
        stop = prefix.startswith("control.")     # 정책 안쪽은 더 쪼개지 않는다
        if isinstance(v, dict) and v and not stop:
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def char_deviations(char: dict) -> list[tuple[str, object, object, str]]:
    """캐릭터 dict가 1층에서 얼마나 벗어났는지. `(키, 기본값, 실제값, 출처)` 목록.

    출처는 `레이어`(data/char_defaults.json) 또는 `지정`(호출자 오버라이드).
    """
    name = char.get("name", "")
    base = _flatten(DEFAULT_CHAR)
    layered = _flatten(build_char(name))     # 레이어까지만 적용한 모습
    cur = _flatten(char)

    out = []
    for k in sorted(set(base) | set(cur)):
        b, c = base.get(k, "없음"), cur.get(k, "없음")
        if b == c or (b == {} and k not in cur):
            continue        # `control: {}` → 하위 정책 줄로 이미 드러난다
        src = "레이어" if layered.get(k, "없음") == c else "지정"
        out.append((k, b, c, src))
    return out


def squad_deviations(squad: list[dict]) -> dict[str, list[tuple]]:
    """스쿼드 전체의 1층 이탈. 벗어난 캐릭터만 담는다."""
    return {c.get("name", "?"): d for c in squad if (d := char_deviations(c))}


def format_deviations(squad: list[dict], indent: str = "") -> str:
    """1층 이탈을 사람이 읽는 블록으로. 이탈이 없으면 그렇다고 한 줄로 알린다."""
    dev = squad_deviations(squad)
    if not dev:
        return f"{indent}기본 스펙(1층) 그대로 — 컨트롤 자동 · 공통 장비 옵션."
    lines = [f"{indent}⚠ 기본 스펙(1층) 이탈 {len(dev)}명 —"]
    for nm, items in dev.items():
        for k, b, c, src in items:
            lines.append(f"{indent}  [{nm}] {k}: {_fmt(b)} → {_fmt(c)}  ({src})")
    return "\n".join(lines)
