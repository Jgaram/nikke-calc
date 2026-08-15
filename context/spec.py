"""기본 육성 스펙 + 캐릭터별 기본 레이어 (Claude 전용 러너 공용).

`simulate()`에 넘길 캐릭터 dict를 만드는 유일한 자리다. 러너 셋이 전부 여기를 쓴다 —
`context/snapshot.py`(회귀 하네스) · `context/sim.py`(단발 CLI) ·
`.agent/skills/report-squad/scripts/report.py`(딜량 보고서). 세 도구의 총딜을 서로 비교할 수 있는 건
기본 스펙이 하나이기 때문이다.

합성 순서 (뒤가 이긴다, dict는 재귀 병합 / 리스트·스칼라는 교체):

    DEFAULT_CHAR  →  data/char_defaults.json[이름]  →  육성 프로필(선택)  →  호출자 오버라이드

**육성 프로필**은 고정 스펙 대신 *실제 계정의 육성 상태*로 돌릴 때만 끼는 선택 레이어다
(`profiles/<이름>.json`, `scraper/profile_fetch.py`가 만든다). 캐릭터별 기본 레이어 **뒤**에
오는 이유는 그 레이어의 장비 옵션 값이 고정 스펙을 전제로 잡힌 것이기 때문이다(미하라의
23.22%). 컨트롤·버스트 패턴은 육성이 아니라 운용이므로 프로필에 담기지 않고 그대로 살아남는다.

**`calculator/`는 이 모듈을 임포트하지 않는다.** `timeline.simulate()`는 넘겨받은 캐릭터
dict만 보고, 기본 컨트롤·장비 옵션을 스스로 채우지 않는다 — 기본값이 시뮬 결과를 소리 없이
바꾸면 안 되기 때문이다(context/CONTROL.md). 레이어를 얹는 책임은 언제나 러너 쪽에 있다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# ── 오버로드 장비 옵션 ─────────────────────────────────────────────────────
# 인게임 오버로드 옵션은 **줄 단위**로 붙고 줄마다 레벨 1~15가 있다. 수치의 정본은
# `data/base_stat_tables/equipment_skills.json`(소수 표기)이고, `equip_skills`는 퍼센트
# 표기의 합산값이라 100을 곱해 쓴다.
_EQUIP_SKILL_TABLE: dict = json.loads(
    (_ROOT / "data" / "base_stat_tables" / "equipment_skills.json").read_text(encoding="utf-8"))

OVERLOAD_LV = 10          # 기본 스펙이 잡는 옵션 레벨


def overload(option: str, lines: int, lv: int = OVERLOAD_LV) -> float:
    """오버로드 옵션 `lines`줄의 합산 퍼센트. `equip_skills`에 그대로 넣는 단위다.

    예: `overload("atk_pct", 2)` → 22.22 (레벨 10 공격력 옵션 2줄).
    손으로 적은 어림값 대신 인게임 표에서 유도하기 위한 자리다.
    """
    vals = _EQUIP_SKILL_TABLE[option]["values"]
    if not 1 <= lv <= len(vals):
        raise ValueError(f"{option}: 레벨은 1~{len(vals)}이어야 한다 ({lv})")
    return round(vals[lv - 1] * 100 * lines, 4)


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
    # 우월코드 4줄 · 공격력 2줄 · 최대장탄 2줄, 전부 레벨 10 (→ 88.6 / 22.22 / 129.64).
    "equip_skills": {
        "atk_pct": overload("atk_pct", 2),
        "element_bonus": overload("element_bonus", 4),
        "max_ammo_pct": overload("max_ammo_pct", 2),
        "crit_rate": 0,
        "crit_dmg": 0,
        "charge_speed_pct": 0,
        "charge_dmg_pct": 0,
        "accuracy_pct": 0,
        "def_pct": 0,
    },
    "cube": {"name": "렐릭 베어 큐브", "level": 15},
    "console": {"common_level": 180, "class_level": 100, "company_level": 100},
    "collection_stage": "SR15",
    "control": {},
}


def _load_char_defaults() -> dict[str, dict]:
    with open(_ROOT / "data" / "char_defaults.json", encoding="utf-8") as f:
        d = json.load(f)
    return {k: v for k, v in d.items() if not k.startswith("_")}


CHAR_DEFAULTS: dict[str, dict] = _load_char_defaults()


# ── 육성 프로필 (2.5층, 선택) ──────────────────────────────────────────────
# 고정 스펙 대신 **실제 계정의 육성 상태**로 돌릴 때만 끼는 레이어. 정본은
# `profiles/<이름>.json`(gitignore, `scraper/profile_fetch.py`가 만든다).
#
# 프로필은 **육성만** 담는다. 컨트롤·버스트 패턴은 운용이라 조합·상황에 달려 있고 계정
# 상태로 결정되지 않으므로 담지 않는다 — 실수로 들어오면 로드에서 끊는다.

PROFILE_DIR = _ROOT / "profiles"

GROWTH_KEYS = frozenset({
    "level", "breakthrough", "core_enhancement", "affinity", "skill_levels",
    "equipment", "equip_skills", "collection_stage", "console", "cube",
})

# 레벨 정책. 인게임 캐릭터 레벨은 **동기화 소대에 넣었는지**에 달려 있어 육성 상태가 아니라
# 편성 상태에 가깝고, 솔로레이드는 레벨이 고정된다. 그래서 프로필은 레벨을 담지 않고
# 러너가 정책으로 정한다.
#   fixed : 기본 스펙 레벨(`DEFAULT_CHAR["level"]` = 400) 그대로. **기본값** — 솔로레이드 기준.
#   sync  : 동기화 소대 레벨(`_account.synchro_level`). 소대 밖 캐릭터도 같은 값으로 계산한다
#           (소대에 넣기만 하면 그 레벨이 되므로).
LEVEL_MODES = ("fixed", "sync")


class GrowthProfile:
    """육성 프로필 한 벌. `layer(이름)`이 그 캐릭터의 2.5층을 준다.

    미보유 캐릭터는 기본적으로 에러다. 고정 스펙으로 조용히 떨어지면 "내 계정 기준"이라는
    결과가 실제로는 만렙 가상 캐릭터를 섞은 게 되기 때문이다. `allow_unowned=True`로 허용할
    수 있고, 그때 대체된 이름은 `unowned`에 쌓여 러너가 결과에 함께 낸다.
    """

    def __init__(self, data: dict, allow_unowned: bool = False,
                 level_mode: str = "fixed"):
        if level_mode not in LEVEL_MODES:
            raise SystemExit(f"레벨 정책은 {LEVEL_MODES} 중 하나여야 한다 ({level_mode!r})")
        self.meta: dict = data.get("_meta") or {}
        self.account: dict = data.get("_account") or {}
        self.chars: dict[str, dict] = data.get("chars") or {}
        self.allow_unowned = allow_unowned
        self.level_mode = level_mode
        self.unowned: list[str] = []
        if level_mode == "sync" and not self.account.get("synchro_level"):
            raise SystemExit(
                f"프로필 '{self.meta.get('name', '?')}'에 동기화 소대 레벨이 없다 — "
                f"레벨 정책 sync를 쓸 수 없다. 프로필을 다시 받는다.")

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or "?")

    def layer(self, char_name: str) -> dict:
        entry = self.chars.get(char_name)
        if entry is None:
            if not self.allow_unowned:
                raise SystemExit(
                    f"[{char_name}] 육성 프로필 '{self.name}'에 없다 — 미보유이거나 수집 후 "
                    f"영입한 캐릭터다. 고정 스펙으로 대체하려면 미보유 허용 옵션을 쓴다"
                    f"(sim.py `--allow-unowned`). 최근에 영입했다면 프로필을 다시 받는다."
                )
            if char_name not in self.unowned:
                self.unowned.append(char_name)
            return {}
        out = copy.deepcopy(entry)
        # 콘솔은 계정 단위라 캐릭터가 아니라 `_account`에 있다. 비어 있으면 1층 값이 남는다.
        if self.account.get("console"):
            out["console"] = copy.deepcopy(self.account["console"])
        # 레벨은 정책이 정한다. fixed면 아예 손대지 않아 1층의 400이 그대로 남는다.
        if self.level_mode == "sync":
            out["level"] = self.account["synchro_level"]
        return out

    def cube_notes(self, squad: list[dict]) -> list[str]:
        """스쿼드가 쓰는 큐브를 실제로 그 레벨로 갖고 있는지. 모르면 아무 말도 하지 않는다.

        큐브는 프로필에 담기지 않는다(자유롭게 갈아끼우므로 육성이 아니라 케이스가 정하는
        축이다). 대신 `_account.cubes`에 **장착 중인 것에서 관찰된 보유 하한**이 있으므로,
        거기에 못 미치는 큐브를 요구하는 계산이면 "실제로는 못 하는 세팅"임을 알린다.
        하한일 뿐이라 목록에 없는 큐브는 판단하지 않는다 — 없다고 단정하면 오탐이 된다.
        """
        owned = self.account.get("cubes") or {}
        if not owned:
            return []
        short = {}
        for c in squad:
            cube = c.get("cube") or {}
            nm, lv = cube.get("name"), cube.get("level")
            if nm in owned and lv is not None and lv > owned[nm]:
                short[nm] = (lv, owned[nm])
        return [f"요구 큐브 레벨이 관찰된 보유분보다 높다: "
                + ", ".join(f"{nm} Lv{need}(보유 관찰 {have})" for nm, (need, have) in short.items())
                + ". 관찰분은 장착 중이던 큐브에서 온 **하한**이라 실제로는 더 높을 수 있다"] if short else []

    def notes(self, names: list[str]) -> list[str]:
        """이 스쿼드에 걸리는 프로필 경고. 러너가 이탈 보고와 함께 그대로 낸다."""
        out = []
        if not self.account.get("console"):
            out.append(f"프로필 '{self.name}'에 콘솔 레벨이 없다 — 기본 스펙 값"
                       f"(공통 180 / 클래스 100 / 기업 100)으로 계산했다.")
        out += self.account.get("console_warnings") or []
        # 스킬 레벨은 레벨과 달리 고정되지 않는다. 기본 스펙(10/10/10)보다 낮으면 딜이 그만큼
        # 낮게 나오는데, 수치만 보면 조합이 나쁜 것처럼 읽히므로 따로 알린다.
        under = {n: lv for n in names
                 if (lv := [v for v in (self.chars.get(n) or {}).get("skill_levels", {}).values()
                            if v < 10])}
        if under:
            out.append("스킬 레벨이 10 미만인 캐릭터: "
                       + ", ".join(f"{n} {'/'.join(str(v) for v in (self.chars[n]['skill_levels']).values())}"
                                   for n in under)
                       + ". 딜이 낮게 나오는 게 정상이다 — 조합 탓이 아니다.")
        low = {n: self.chars[n]["_favorite_stage"] for n in names
               if "_favorite_stage" in (self.chars.get(n) or {})}
        if low:
            out.append("애장품 단계가 3 미만인데 계산기는 3단계로만 파싱돼 있다 — "
                       f"이 캐릭터의 딜은 과대평가다: {low} (context/PARSING.md §애장품)")
        if self.unowned:
            out.append(f"프로필에 없어 **기본 스펙으로 대체**한 캐릭터: {self.unowned}")
        return out

    def level_text(self) -> str:
        if self.level_mode == "sync":
            return f"동기화 소대 레벨 {self.account['synchro_level']}"
        return f"레벨 {DEFAULT_CHAR['level']} 고정 (솔로레이드 기준)"

    def header(self) -> str:
        m = self.meta
        return (f"육성 프로필 '{self.name}' 적용 — 고정 스펙 아님. 다른 보고서와 총딜을 직접 "
                f"비교하지 않는다. ({self.level_text()}, 수집 {m.get('fetched_at', '?')}, "
                f"로스터 {m.get('roster', '?')}종)")


def load_profile(name: str, allow_unowned: bool = False,
                 level_mode: str = "fixed") -> GrowthProfile:
    """`profiles/<name>.json` → `GrowthProfile`. 없거나 형식이 어긋나면 끊는다."""
    path = PROFILE_DIR / f"{name}.json"
    if not path.exists():
        have = sorted(p.stem for p in PROFILE_DIR.glob("*.json")
                      if not p.name.endswith(".raw.json")) if PROFILE_DIR.exists() else []
        raise SystemExit(
            f"육성 프로필 '{name}'이 없다 ({path}). "
            f"있는 프로필: {have or '없음'}. 만들려면 `python scraper/profile_fetch.py`."
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "chars" not in data:
        raise SystemExit(f"{path}: `chars` 키가 없다 — profile_fetch.py가 만든 파일이 아니다.")
    for char_name, entry in data["chars"].items():
        bad = sorted(k for k in entry if not k.startswith("_") and k not in GROWTH_KEYS)
        if bad:
            raise SystemExit(
                f"{path}: [{char_name}]에 육성이 아닌 키가 있다 {bad}. 프로필은 육성만 담는다 "
                f"— 컨트롤·버스트 패턴은 운용이라 data/char_defaults.json이나 호출부에 둔다."
            )
    return GrowthProfile(data, allow_unowned, level_mode)


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


def char_layer(name: str, members: list[str] | None = None) -> dict:
    """캐릭터별 기본 레이어(장비 옵션·컨트롤 차이분). 없으면 빈 dict.

    `members`(스쿼드 전원)를 주면 **조합 조건부 컨트롤**(`_control_rules`)까지 얹은 모습을
    준다. 조건은 버스트 패턴과 같은 `_when_ok` 어휘를 쓰고, **맞는 규칙 전부**가 순서대로
    `control`에 병합된다 (패턴은 값이 하나뿐이라 먼저 맞는 하나가 이기지만, 컨트롤은
    톡톡이+홀드처럼 겹쳐 쓰는 게 정상이기 때문이다).
    """
    layer = CHAR_DEFAULTS.get(name, {})
    if members is None:
        return layer
    out = copy.deepcopy(layer)
    for rule in layer.get("_control_rules") or []:
        if _when_ok(name, rule.get("when") or {}, members):
            out = deep_merge(out, {"control": rule.get("control") or {}})
    return out


def build_char(name: str, over: dict | None = None, base: dict | None = None,
               no_layer: bool = False, members: list[str] | None = None,
               profile: GrowthProfile | None = None) -> dict:
    """이름 → `simulate()`에 넘길 캐릭터 dict 하나.

    base     : 기본 스펙을 갈아끼울 때만 준다 (보고서 스펙의 `defaults` 등). 기본은 DEFAULT_CHAR.
    over     : 이 캐릭터만의 오버라이드. **캐릭터별 기본 레이어보다 우선한다.**
    no_layer : 레이어를 아예 건너뛴다. 재귀 병합이라 `{"control": {}}`를 얹는 걸로는
               기본 컨트롤이 지워지지 않기 때문에, 끄려면 이 플래그를 쓴다.
    members  : 스쿼드 전원. 조합 조건부 컨트롤(`_control_rules`)을 판정하는 데만 쓴다.
               주지 않으면 조건부 레이어는 붙지 않는다.
    profile  : 육성 프로필(2.5층). 캐릭터별 기본 레이어 **뒤**, 호출자 오버라이드 **앞**.
               회귀 하네스(`context/snapshot.py`)는 절대 주지 않는다 — golden baseline은
               고정 스펙 전용이다.
    """
    c = copy.deepcopy(base or DEFAULT_CHAR)
    if not no_layer:
        c = deep_merge(c, char_layer(name, members))
    if profile is not None:
        c = deep_merge(c, profile.layer(name))
    c = deep_merge(c, over)
    c["name"] = name
    if is_preview(name):
        bad = {k: v for k, v in (c.get("skill_levels") or {}).items() if v != 10}
        if bad:
            raise ValueError(
                f"{name}: 프리뷰 캐릭터는 스킬 레벨 10으로만 실행할 수 있다 (요청 {bad}). "
                "출시 전 카드가 레벨 10 기준이라 1~9 계수가 존재하지 않는다 — "
                "출시 후 char-add 단계 R(정식 등록)에서 채운다"
            )
    return c


def build_squad(names: list[str], chars: dict[str, dict] | None = None,
                base: dict | None = None, no_layer: set[str] | None = None,
                profile: GrowthProfile | None = None) -> list[dict]:
    """이름 목록 → 캐릭터 dict 목록. `chars`는 캐릭터별 오버라이드."""
    over = chars or {}
    skip = no_layer or set()
    explicit = {n for n, v in over.items() if "burst_pattern" in (v or {})}
    return resolve_patterns(
        [build_char(n, over.get(n), base, n in skip, names, profile) for n in names], explicit)


# ── 버스트 운용 패턴 ───────────────────────────────────────────────────────
# 캐릭터마다 "몇 번째 풀버스트에 버스트를 쓰는가"가 정해져 있는 경우가 있다
# (마스트 : 로망틱 메이드 = 3의 배수 사이클이 정석). 카탈로그는 `_burst_patterns`에,
# 그중 기본으로 쓸 이름은 `burst_pattern`에 적는다 — 후자는 캐릭터 dict에 남아
# 이탈 보고에 그대로 잡힌다.
#
# 패턴은 **후보에서 빼는 게 아니라 뒤로 미는 것**이다(timeline `_pattern_rank`) —
# 대신 쓸 사람이 없거나 쿨이면 그냥 예정대로 나가므로 단계가 막히지 않는다.
#
# 다만 "정석"이 조합에 달린 경우가 있다(마스트의 3의 배수는 20초 쿨 2버가 있어야 성립).
# 그건 `_burst_pattern_when` 조건으로 표현하고, 조건이 안 맞으면 아예 걸지 않는다.
# 조건은 맞는데 **어느 패턴이냐가** 조합에 달린 경우는 `_burst_pattern_rules`로 갈아끼운다.


def _nikke() -> dict:
    global _NIKKE_CACHE
    if _NIKKE_CACHE is None:
        with open(_ROOT / "data" / "parsed_nikke.json", encoding="utf-8") as f:
            _NIKKE_CACHE = json.load(f)
    return _NIKKE_CACHE


_NIKKE_CACHE: dict | None = None


def _same_stage_others(name: str, members: list[str]) -> list[str]:
    """같은 버스트 단계의 다른 멤버들 (`burst_stage: "A"`는 어느 단계로도 센다)."""
    nk = _nikke()
    my_stage = str(nk.get(name, {}).get("burst_stage", ""))
    return [
        m for m in members
        if m != name and str(nk.get(m, {}).get("burst_stage", "")) in (my_stage, "A")
    ]


def max_burst_floor(names: list[str]) -> int | None:
    """스쿼드가 잘리지 않으려면 필요한 최소 풀버스트 상한. 없으면 None.

    캐릭터 레이어의 `_max_burst_count`에서 온다 (아르카나처럼 사이클이 빨라 기본
    상한에 걸리는 캐릭터). **시뮬은 상한이 없으므로**(`timeline` 기본 `None`)
    이 값은 상한을 두는 쪽 — 지금은 보고서 러너 — 만 쓴다.
    """
    vals = [v for n in names
            if (v := (CHAR_DEFAULTS.get(n) or {}).get("_max_burst_count"))]
    return max(vals) if vals else None


def is_preview(name: str) -> bool:
    """출시 전 카드 이미지 기준으로 등록된 캐릭터인가.

    `parse_nikke.py`가 `scraper/preview_skills.json` 출신 항목에만 `preview: true`를 붙인다.
    출시되면 스크랩 항목이 이겨서 플래그가 사라진다.
    """
    return bool(_nikke().get(name, {}).get("preview"))


def preview_note(names: list[str]) -> str:
    """스쿼드에 프리뷰 캐릭터가 있으면 결과에 붙일 경고 한 줄. 없으면 빈 문자열.

    러너(`snapshot.py`·`sim.py`·`report.py`)가 결과와 함께 그대로 출력한다 —
    카드 기준 추정값이 검증된 수치인 것처럼 읽히면 안 된다(AGENTS.md §Simulation invariants).
    """
    pv = [n for n in names if is_preview(n)]
    if not pv:
        return ""
    return (f"[프리뷰 · 미검증] {', '.join(pv)} — 출시 전 카드(스킬 레벨 10) 기준. "
            "인게임 검증 전이므로 수치·발동 조건이 바뀔 수 있다")


def burst_stage(name: str) -> str:
    """캐릭터의 버스트 단계 — `"1"`·`"2"`·`"3"`·`"A"`. 모르면 빈 문자열."""
    return str(_nikke().get(name, {}).get("burst_stage", ""))


def _when_ok(name: str, cond: dict, members: list[str]) -> bool:
    """레이어 기본값(버스트 패턴·조건부 컨트롤)의 적용 조건. 지원하는 키는 아래 넷.

    `same_stage_cd_max: N` — **같은 버스트 단계에 쿨타임 N초 이하인 다른 멤버가 있을 때만.**
    마스트 : 로망틱 메이드의 "3의 배수"가 20초 쿨 2버와 함께일 때만 성립하는 걸 표현한다.
    `same_stage_other: true` — 같은 단계에 **다른 멤버가 하나라도 있을 때만.** 자기가 그
    단계의 유일한 멤버면 패턴(특히 "안 씀")을 걸어봐야 의미가 없으므로 아예 떼어낸다.
    `with_member: [이름...]` — 목록 중 **하나라도 스쿼드에 있을 때만.**
    `position: N` — 스쿼드 배치 순서가 N번째일 때만 (1 = 가장 왼쪽).

    조건이 안 맞으면 패턴을 걸지 않는다 — 그 조합에서는 평소 순서(왼쪽부터)가 맞다.
    """
    nk = _nikke()
    for key, val in cond.items():
        if key == "same_stage_cd_max":
            ok = any(
                float(nk.get(m, {}).get("burst_cooldown") or 1e9) <= val
                for m in _same_stage_others(name, members)
            )
        elif key == "same_stage_other":
            ok = bool(_same_stage_others(name, members)) == bool(val)
        elif key == "with_member":
            ok = any(m in members for m in val)
        elif key == "position":
            ok = name in members and members.index(name) + 1 == val
        else:
            raise SystemExit(f"[{name}] 알 수 없는 레이어 조건 키: {key!r}")
        if not ok:
            return False
    return True


def resolve_patterns(squad: list[dict], explicit: set[str] | None = None) -> list[dict]:
    """**레이어 기본** 패턴을 조합에 맞춰 확정한다 (제자리 수정 후 그대로 반환).

    두 단계다 —
      ① `_burst_pattern_when`이 안 맞으면 패턴을 통째로 떼어낸다.
      ② `_burst_pattern_rules`(먼저 맞는 것 하나)가 있으면 그 이름으로 갈아끼운다.
         같은 조건 안에서도 배치·동료에 따라 정석이 갈릴 때 쓴다
         (마스트 : 로망틱 메이드 — 가장 왼쪽이면 1,3,5,9,11,14, 아니면 3의 배수).

    explicit : 호출자가 `burst_pattern`을 직접 준 캐릭터 이름들. 이쪽은 조건을 보지 않는다 —
               **지정은 언제나 이긴다.** 값이 우연히 레이어 기본값과 같아도 마찬가지다.

    캐릭터 dict에 확정된 이름만 남으므로 이탈 보고에도 "실제로 걸린 패턴"만 나온다.
    """
    members = [c["name"] for c in squad]
    named = explicit or set()
    for c in squad:
        name = c["name"]
        if name in named or not c.get("burst_pattern"):
            continue
        layer = CHAR_DEFAULTS.get(name) or {}
        cond = layer.get("_burst_pattern_when")
        if cond and not _when_ok(name, cond, members):
            c.pop("burst_pattern", None)
            continue
        for rule in layer.get("_burst_pattern_rules") or []:
            if _when_ok(name, rule.get("when") or {}, members):
                c["burst_pattern"] = rule["use"]
                break
    return squad


def burst_pattern_of(name: str, chosen: str | None) -> object | None:
    """패턴 이름 → 실제 값(`"every:3"` 또는 사이클 목록). 못 찾으면 에러로 끊는다."""
    if not chosen:
        return None
    catalog = (CHAR_DEFAULTS.get(name) or {}).get("_burst_patterns") or {}
    if chosen not in catalog:
        raise SystemExit(
            f"[{name}] 버스트 패턴 '{chosen}'이 data/char_defaults.json에 없다. "
            f"등록된 패턴: {list(catalog) or '없음'}"
        )
    return catalog[chosen]


def build_config(squad: list[dict], config: dict | None = None) -> dict:
    """캐릭터 dict의 `burst_pattern`을 모아 `config["burst_pattern"]`으로 넘긴다.

    `burst_sequence`를 명시한 config는 건드리지 않는다 — 그쪽이 사이클별 순서를
    전부 결정하므로 패턴이 개입할 자리가 없다.
    """
    cfg = copy.deepcopy(config or {})
    if cfg.get("burst_sequence"):
        return cfg
    pats = {}
    for c in squad:
        v = burst_pattern_of(c["name"], c.get("burst_pattern"))
        if v is not None:
            pats[c["name"]] = v
    if pats:
        cfg["burst_pattern"] = {**pats, **(cfg.get("burst_pattern") or {})}
    return cfg


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
        if k.startswith("_") or (not prefix and k in _SKIP_KEYS):
            continue
        key = f"{prefix}{k}"
        stop = prefix.startswith("control.")     # 정책 안쪽은 더 쪼개지 않는다
        if isinstance(v, dict) and v and not stop:
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def char_deviations(char: dict, members: list[str] | None = None,
                    profile: GrowthProfile | None = None
                    ) -> list[tuple[str, object, object, str]]:
    """캐릭터 dict가 기준선에서 얼마나 벗어났는지. `(키, 기준값, 실제값, 출처)` 목록.

    출처는 `레이어`(data/char_defaults.json) 또는 `지정`(호출자 오버라이드).
    `members`를 줘야 조합 조건부 컨트롤이 `지정`이 아니라 `레이어`로 잡힌다.

    **기준선은 프로필 유무로 갈린다.** 프로필 없이는 1층(고정 스펙)이고, 프로필을 끼면
    `1층+레이어+프로필`이 기준선이 된다 — 그러지 않으면 육성 키 전부가 이탈로 잡혀
    (캐릭터당 열 줄 남짓) 정작 봐야 할 호출자 지정이 묻힌다. 프로필을 썼다는 사실 자체는
    `format_deviations`가 머리줄로 따로 알린다.
    """
    name = char.get("name", "")
    ref = build_char(name, members=members, profile=profile)
    base = _flatten(ref if profile is not None else DEFAULT_CHAR)
    layered = _flatten(ref)                                 # 레이어(+프로필)까지만 적용한 모습
    cur = _flatten(char)

    out = []
    for k in sorted(set(base) | set(cur)):
        b, c = base.get(k, "없음"), cur.get(k, "없음")
        if b == c or (b == {} and k not in cur):
            continue        # `control: {}` → 하위 정책 줄로 이미 드러난다
        src = "레이어" if layered.get(k, "없음") == c else "지정"
        out.append((k, b, c, src))
    return out


def squad_deviations(squad: list[dict], profile: GrowthProfile | None = None
                     ) -> dict[str, list[tuple]]:
    """스쿼드 전체의 기준선 이탈. 벗어난 캐릭터만 담는다."""
    members = [c.get("name", "") for c in squad]
    return {c.get("name", "?"): d for c in squad
            if (d := char_deviations(c, members, profile))}


def format_deviations(squad: list[dict], indent: str = "",
                      profile: GrowthProfile | None = None) -> str:
    """기준선 이탈을 사람이 읽는 블록으로. 이탈이 없으면 그렇다고 한 줄로 알린다.

    프리뷰(출시 전 카드 기준) 캐릭터가 끼어 있으면 그 경고를 맨 위에 붙인다 —
    이탈 보고와 같은 이유로, 수치만 보고 검증된 결과로 오해하면 안 되기 때문이다.
    육성 프로필을 끼웠으면 그 사실과 프로필 경고도 같은 자리에서 알린다.
    """
    names = [c.get("name", "") for c in squad]
    note = preview_note(names)
    head = [f"{indent}⚠ {note}"] if note else []
    if profile is not None:
        head.append(f"{indent}⚠ {profile.header()}")
        head += [f"{indent}⚠ {n}"
                 for n in profile.notes(names) + profile.cube_notes(squad)]
    dev = squad_deviations(squad, profile)
    label = "프로필(2.5층)" if profile is not None else "기본 스펙(1층)"
    if not dev:
        if profile is not None:
            return "\n".join(head + [f"{indent}프로필 그대로 — 추가 지정 없음."])
        return "\n".join(head + [f"{indent}기본 스펙(1층) 그대로 — 컨트롤 자동 · 공통 장비 옵션."])
    lines = head + [f"{indent}⚠ {label} 이탈 {len(dev)}명 —"]
    for nm, items in dev.items():
        for k, b, c, src in items:
            lines.append(f"{indent}  [{nm}] {k}: {_fmt(b)} → {_fmt(c)}  ({src})")
    return "\n".join(lines)
