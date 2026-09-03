"""기본 육성 스펙 + 캐릭터별 기본 레이어 (Claude 전용 러너 공용).

`simulate()`에 넘길 캐릭터 dict를 만드는 유일한 자리다. 러너 셋이 전부 여기를 쓴다 —
`runner/snapshot.py`(회귀 하네스) · `runner/sim.py`(단발 CLI) ·
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
바꾸면 안 되기 때문이다(docs/CONTROL.md). 레이어를 얹는 책임은 언제나 러너 쪽에 있다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from calculator import timeline
from calculator.base_stat import NO_ITEM, calc_base_stats

_ROOT = Path(__file__).resolve().parent.parent

# ── 오버로드 장비 옵션 ─────────────────────────────────────────────────────
# 인게임 오버로드 옵션은 **줄 단위**로 붙고 줄마다 레벨 1~15가 있다. 수치의 정본은
# `data/base_stat_tables/equipment_skills.json`(소수 표기)이고, `equip_skills`는 퍼센트
# 표기의 합산값이라 100을 곱해 쓴다.
#
# `equip_skills`의 값은 **스칼라(합산) 또는 줄별 퍼센트 리스트**다. 최대 장탄·차지 속도는
# 인게임이 **같은 레벨끼리만 합산한 뒤 단계마다 따로 반올림**하므로(GAMEPLAY.md §무기
# 메카닉), 단계가 섞인 장비는 리스트로 적어야 한다 — `overload_lines()`가 그 형태를 만든다.
# 줄이 전부 같은 레벨이면 어차피 한 그룹이라 스칼라와 결과가 같다(기본 스펙이 그렇다).
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


def overload_lines(option: str, lines: int, lv: int = OVERLOAD_LV) -> list[float]:
    """`overload()`와 같은 옵션을 **줄별 퍼센트 리스트**로. 단계를 섞을 때 쓴다.

    예: `overload_lines("max_ammo_pct", 2) + overload_lines("max_ammo_pct", 1, lv=7)`
    → `[64.82, 64.82, 52.5]` (레벨 10 2줄 + 레벨 7 1줄). 계산기가 같은 값끼리 묶어
    그룹당 한 번 반올림한다.
    """
    return [overload(option, 1, lv)] * lines


# ── 기본 육성 스펙 ─────────────────────────────────────────────────────────
# 정본. 항목 근거·의미는 docs/HARNESS.md §기본 스펙.
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
    # 애장품 단계 0(미보유)~3. 애장품이 없는 캐릭터에는 아무 영향이 없다.
    # 애장품은 소장품 슬롯을 공유하고 스탯이 SR15와 같으므로 `collection_stage`는 그대로 둔다
    # — 이 키가 바꾸는 건 스킬 판본뿐이다(`calculator/buff_manager.char_effects()`).
    "favorite_stage": 3,
    "control": {},
}


def _load_char_defaults() -> dict[str, dict]:
    with open(_ROOT / "data" / "char_defaults.json", encoding="utf-8") as f:
        d = json.load(f)
    return {k: v for k, v in d.items() if not k.startswith("_")}


CHAR_DEFAULTS: dict[str, dict] = _load_char_defaults()


# ── 택틱 카탈로그 ──────────────────────────────────────────────────────────
# **택틱 이름의 정본** — 이름·목적·`manual` 스위치, 그리고 대상을 이름으로 못 박을 수 없는
# 규칙(`pick`)만 여기 산다. 대상이 이름으로 정해진 규칙은 니케별 레이어(CHAR_DEFAULTS의
# `_rules`)에 있고 `tactic` 라벨로 이 카탈로그를 참조한다. 정본: docs/CONTROL.md §택틱.
# 어느 쪽이든 러너에서 캐릭터별 `control`로 전개되고 사라진다 — `calculator/`는 택틱을 모른다.


def _load_tactics() -> dict[str, dict]:
    with open(_ROOT / "data" / "tactics.json", encoding="utf-8") as f:
        d = json.load(f)
    return {k: v for k, v in d.items() if not k.startswith("_")}


TACTICS: dict[str, dict] = _load_tactics()

# 버충 담당에서 빼는 니케 — **풀차지가 곧 버충**이라 논차지로 바꾸면 본체가 사라진다.
# 헬름 `진두지휘 3` · 맥스웰 `출력 전환 시퀀스 2`는 `full_charge_hit`에 게이지가 붙어 있고,
# 스노우 화이트 : 헤비암즈는 풀차지를 해야 `오토 파이어`의 다타격이 난다.
_BURST_CHARGE_EXCLUDE = ("헬름", "맥스웰 : 오디너리 미케닉", "스노우 화이트 : 헤비암즈")


def pick_burst_charge_carrier(members: list[str]) -> str | None:
    """버충 담당을 고른다 — **SR 우선 → RL → 톡톡이 가능한 니케만.**
    정본: docs/CONTROL.md §담당 선택.

    **엔진은 담당을 고르지 않는다**(AGENTS.md §Simulation invariants). 이 규칙이 사는
    자리가 여기다 — 러너가 고르고 캐릭터 `control`로 넘긴다.
    """
    nk = _nikke()
    cands = []
    for i, m in enumerate(members):
        d = nk.get(m, {})
        if d.get("full_charge_only") or m in _BURST_CHARGE_EXCLUDE:
            continue
        # **차지 무기만 후보다.** 무기 유형이 아니라 차지 여부로 가른다 — 버충 톡톡이는
        # 차지를 끊어 쏘는 조작이라 비차지 무기에는 애초에 걸리지 않는다(엔진도 무시한다).
        # RL 파스칼이 유형만 보면 후보로 잡히던 자리다.
        wt = d.get("weapon_type", "")
        if wt not in ("SR", "RL") or d.get("is_charge") is False:
            continue
        cands.append((0 if wt == "SR" else 1, i, m))
    return sorted(cands)[0][2] if cands else None


PICKERS = {"burst_charge_carrier": pick_burst_charge_carrier}


def tactic_overrides(tactic: str, members: list[str],
                     target: str | None = None) -> dict[str, dict]:
    """택틱 하나를 스쿼드에 전개한다 → `{이름: apply}`.

    `manual` 택틱(버충 등)을 CLI·하네스에서 켤 때 쓴다. `target`을 주면 `pick` 규칙의
    대상을 그것으로 못박는다 — 자동 선택 규칙을 손으로 덮어쓰는 통로다.

    산출물은 **호출자 오버라이드**(`build_squad(chars=...)`)로 들어간다. 즉 수동 택틱은
    「지정」이라 자동 규칙보다 뒤에 얹히고, 이탈 보고에도 그렇게 잡힌다.
    """
    t = TACTICS.get(tactic)
    if t is None:
        raise SystemExit(f"모르는 택틱: {tactic!r}. 있는 것: {sorted(TACTICS)}")
    rules = t.get("_rules") or []
    # 조건이 조립 결과(육성·스탯)를 볼 수 있으므로, `when`이 있는 규칙이 하나라도 있으면
    # 그때만 잠정 스쿼드를 세워 판정한다 — 없으면 세우지 않는다(버충이 그렇다).
    ctx = _RuleCtx(build_squad(members)) if any(r.get("when") for r in rules) else None
    out: dict[str, dict] = {}
    for rule in rules:
        who = rule.get("who")
        if who is None:
            who = target or PICKERS[rule["pick"]](members)
        if not who or who not in members:
            continue
        if ctx is not None and not _when_ok(who, rule.get("when") or {}, ctx):
            continue
        out[who] = deep_merge(out.get(who, {}), rule.get("apply") or {})
    return out


# ── 육성 프로필 (2.5층, 선택) ──────────────────────────────────────────────
# 고정 스펙 대신 **실제 계정의 육성 상태**로 돌릴 때만 끼는 레이어. 정본은
# `profiles/<이름>.json`(gitignore, `scraper/profile_fetch.py`가 만든다).
#
# 프로필은 **육성만** 담는다. 컨트롤·버스트 패턴은 운용이라 조합·상황에 달려 있고 계정
# 상태로 결정되지 않으므로 담지 않는다 — 실수로 들어오면 로드에서 끊는다.

PROFILE_DIR = _ROOT / "profiles"

GROWTH_KEYS = frozenset({
    "level", "breakthrough", "core_enhancement", "affinity", "skill_levels",
    "equipment", "equip_skills", "collection_stage", "favorite_stage", "console", "cube",
})

# 레벨 정책. 인게임 캐릭터 레벨은 **동기화 소대에 넣었는지**에 달려 있어 육성 상태가 아니라
# 편성 상태에 가깝고, 솔로레이드는 레벨이 고정된다. 그래서 프로필은 레벨을 담지 않고
# 러너가 정책으로 정한다.
#   fixed : 기본 스펙 레벨(`DEFAULT_CHAR["level"]` = 400) 그대로. **기본값** — 솔로레이드 기준.
#   sync  : 동기화 소대 레벨(`_account.synchro_level`). 소대 밖 캐릭터도 같은 값으로 계산한다
#           (소대에 넣기만 하면 그 레벨이 되므로).
LEVEL_MODES = ("fixed", "sync")

# 프로필에 **없는** 이름을 계산할 상태 — 이제 막 영입한 모습이다.
# 기본 스펙으로 떨어뜨리면 만렙 가상 캐릭터가 섞여 "내 계정 기준"이라는 결과가 거짓말이 되고,
# 에러로 끊으면 "지금 뽑으면 얼마나 나오나"를 아예 물어볼 수 없다. 미육성은 둘 다 피하면서
# **바닥값**을 준다. 대체한 이름은 `notes()`가 반드시 결과에 싣는다.
#
# 여기에 일부러 없는 것 둘:
#   레벨 — 정책이 정한다(§LEVEL_MODES). 미육성이라고 레벨이 낮은 게 아니다.
#   큐브 — 육성이 아니라 케이스가 정하는 축이라 1층 값이 그대로 남는다(프로필도 담지 않는다).
# 콘솔은 계정 단위라 `layer()`가 `_account.console`을 똑같이 얹는다 — 미보유와 무관하다.
UNGROWN: dict = {
    "breakthrough": 0,
    "core_enhancement": 0,
    "affinity": 1,                              # 호감도 표는 1부터 (미투자 0이 아니다)
    "skill_levels": {"1": 1, "2": 1, "3": 1},
    "equipment": {p: {"tier": NO_ITEM} for p in ("머리", "몸통", "팔", "다리")},
    "equip_skills": {k: 0 for k in DEFAULT_CHAR["equip_skills"]},
    "collection_stage": NO_ITEM,
    "favorite_stage": 0,
}


class GrowthProfile:
    """육성 프로필 한 벌. `layer(이름)`이 그 캐릭터의 2.5층을 준다.

    프로필에 없는 이름은 **미육성**(§UNGROWN)으로 계산한다. 고정 스펙으로 떨어뜨리면
    "내 계정 기준"이라는 결과가 실제로는 만렙 가상 캐릭터를 섞은 게 되기 때문이다.
    대체한 이름은 `ungrown`에 쌓여 `notes()`가 결과에 함께 낸다.
    """

    def __init__(self, data: dict, level_mode: str = "fixed"):
        if level_mode not in LEVEL_MODES:
            raise SystemExit(f"레벨 정책은 {LEVEL_MODES} 중 하나여야 한다 ({level_mode!r})")
        self.meta: dict = data.get("_meta") or {}
        self.account: dict = data.get("_account") or {}
        self.chars: dict[str, dict] = data.get("chars") or {}
        self.level_mode = level_mode
        self.ungrown: list[str] = []
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
            # 미보유이거나 수집 후 영입한 캐릭터 → 미육성. 콘솔은 계정 것이라 아래에서
            # 똑같이 얹히고, 레벨도 다른 캐릭터와 같은 정책을 받는다.
            if char_name not in self.ungrown:
                self.ungrown.append(char_name)
            entry = UNGROWN
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
        # 애장품 단계는 스킬 판본을 바꾸므로(`buff_manager.char_effects()`) 딜에 직접 걸린다.
        # 기본 스펙은 3단계라, 낮은 단계로 계산된 캐릭터는 조합 탓처럼 읽히지 않게 따로 알린다.
        low = {n: lv for n in names
               if (lv := (self.chars.get(n) or {}).get("favorite_stage")) is not None and lv < 3}
        if low:
            out.append("애장품 단계가 3 미만인 캐릭터: "
                       + ", ".join(f"{n} {lv}단계" for n, lv in low.items())
                       + ". 그 단계의 스킬 판본으로 계산했다 — 기본 스펙(3단계)보다 "
                       "딜이 낮게 나오는 게 정상이다.")
        if self.ungrown:
            out.append(f"프로필에 없어 **미육성으로 계산**한 캐릭터: {self.ungrown}. "
                       f"미보유이거나 수집 후 영입한 캐릭터다 — 돌파·스킬·장비가 전부 바닥인 "
                       f"상태라 딜이 낮게 나오는 게 정상이다(레벨만 다른 캐릭터와 같다). "
                       f"최근에 영입했다면 프로필을 다시 받는다.")
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


def load_profile(name: str, level_mode: str = "fixed") -> GrowthProfile:
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
    return GrowthProfile(data, level_mode)


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
    """캐릭터별 기본 레이어 중 **무조건분**(장비 옵션·컨트롤 차이분). 없으면 빈 dict.

    조건부는 여기 없다 — `_rules`는 스쿼드가 다 조립된 뒤에야 판정할 수 있어
    `resolve_rules()`가 따로 얹는다 (정본: docs/CONTROL.md §부착). `_`로 시작하는 키는
    `deep_merge()`가 걸러 내므로 카탈로그·규칙·주석이 캐릭터 dict로 새지 않는다.
    """
    return CHAR_DEFAULTS.get(name, {})


def build_char(name: str, over: dict | None = None, base: dict | None = None,
               no_layer: bool = False, profile: GrowthProfile | None = None) -> dict:
    """이름 → `simulate()`에 넘길 캐릭터 dict 하나.

    base     : 기본 스펙을 갈아끼울 때만 준다 (보고서 스펙의 `defaults` 등). 기본은 DEFAULT_CHAR.
    over     : 이 캐릭터만의 오버라이드. **캐릭터별 기본 레이어보다 우선한다.**
    no_layer : 레이어를 아예 건너뛴다. 재귀 병합이라 `{"control": {}}`를 얹는 걸로는
               기본 컨트롤이 지워지지 않기 때문에, 끄려면 이 플래그를 쓴다.
               조건부 규칙(`_rules`)도 함께 건너뛴다 — `resolve_rules()`가 같은 집합을 받는다.
    profile  : 육성 프로필(2.5층). 캐릭터별 기본 레이어 **뒤**, 호출자 오버라이드 **앞**.
               회귀 하네스(`runner/snapshot.py`)는 절대 주지 않는다 — golden baseline은
               고정 스펙 전용이다.

    **조건부 규칙은 여기서 붙지 않는다.** 이름 하나로는 조합도 육성 결과도 알 수 없어서다 —
    `build_squad()`가 전원을 조립한 뒤 `resolve_rules()`로 얹는다.
    """
    c = copy.deepcopy(base or DEFAULT_CHAR)
    if not no_layer:
        c = deep_merge(c, char_layer(name))
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
    """이름 목록 → 캐릭터 dict 목록. `chars`는 캐릭터별 오버라이드.

    **2단계다** — ① 전원을 조립하고 ② 그 결과를 보고 조건부 규칙을 판정해 얹는다.
    조건이 육성 결과(장비 옵션·공격력 순위)를 보려면 조립이 먼저 끝나 있어야 하기 때문이다.
    """
    over = chars or {}
    skip = no_layer or set()
    squad = [build_char(n, over.get(n), base, n in skip, profile) for n in names]
    return resolve_rules(squad, over, skip)


# ── 조건부 부착 규칙 ───────────────────────────────────────────────────────
# **컨트롤도 버스트 패턴도 같은 규칙 하나로 붙는다.** 정본: docs/CONTROL.md §부착.
#
#     { "when": {…}, "apply": { "control": {…} }, "tactic": "라벨(선택)" }
#
# 사는 곳은 둘. **대상이 이름으로 정해지면** 니케별 레이어(`data/char_defaults.json`의
# `_rules`), **코드가 고르거나 수동 스위치면** 택틱 카탈로그(`data/tactics.json`).
#
# 병합 의미는 하나다 — **조건이 맞는 규칙 전부를 순서대로 병합하고 뒤가 이긴다.**
# 컨트롤은 클릭과 엄폐처럼 축이 다르면 겹쳐 쓰는 게 정상이라 누적되고, 버스트 패턴은
# 값이 하나라 마지막 규칙이 자연히 이긴다.
#
# `apply`가 쓸 수 있는 키는 `control` 하나로 닫혀 있다. 그래서 `when`이 읽는 축(멤버·배치·
# 버스트 단계·육성·정적 스탯)과 겹치지 않고, 판정이 **한 패스로 끝난다** — 규칙이 자기가
# 만든 결과를 다시 보는 일이 원리적으로 불가능하다(CONTROL.md §순환 위험).
# 버스트 패턴은 `control["burst"]["pattern"]`으로 그 안에 산다 — 버스트도 유저가 직접
# 누르는 컨트롤이기 때문이다(CONTROL.md §L0의 다섯째 버튼). 종전 형제 키 표기는
# `_norm_rule()`이 받아 같은 자리로 옮긴다.
#
# 버스트 패턴은 **후보에서 빼는 게 아니라 뒤로 미는 것**이다(timeline `_pattern_rank`) —
# 대신 쓸 사람이 없거나 쿨이면 그냥 예정대로 나가므로 단계가 막히지 않는다.


def _nikke() -> dict:
    global _NIKKE_CACHE
    if _NIKKE_CACHE is None:
        with open(_ROOT / "data" / "parsed_nikke.json", encoding="utf-8") as f:
            _NIKKE_CACHE = json.load(f)
    return _NIKKE_CACHE


_NIKKE_CACHE: dict | None = None


def _equip_total(val) -> float:
    """`equip_skills` 항목 하나 → 합산 퍼센트. 줄별 리스트도 스칼라도 같은 축으로 편다."""
    if isinstance(val, (list, tuple)):
        return float(sum(val))
    return float(val or 0)


def static_atk(char: dict) -> float:
    """**조립 시점의** 공격력 — 기본 스탯 + 장비 옵션. 런타임 버프는 보지 않는다.

    스탯 순위 조건(`atk_rank`)의 계산 경로다. 시뮬을 돌리지 않고 구하는 정적 값이라
    지연 resolve 버프를 들여다보지 않는다 — CONTROL.md §순환 위험 규칙 3에 걸리지 않는
    이유가 이것이다. 대신 런타임 `buff_manager._effective_atk()`(활성 버프 포함)와는
    다른 값이므로, "그 버프가 실제로 누구에게 갈까"의 **근사**다.
    """
    pct = _equip_total((char.get("equip_skills") or {}).get("atk_pct", 0))
    for part in (char.get("equipment") or {}).values():
        for sk in part.get("skills") or []:
            if sk.get("id") == "atk_pct":
                pct += _EQUIP_SKILL_TABLE["atk_pct"]["values"][int(sk["lv"]) - 1] * 100
    return calc_base_stats(char)["atk"] * (1 + pct / 100)


class _RuleCtx:
    """규칙 `when`이 보는 것 — **조립이 끝난 스쿼드**. 스쿼드 하나당 하나 만든다."""

    def __init__(self, squad: list[dict]):
        self.squad = squad
        self.names: list[str] = [c["name"] for c in squad]
        self.by_name: dict[str, dict] = {c["name"]: c for c in squad}
        self._atk: dict[str, float] | None = None

    @property
    def atk(self) -> dict[str, float]:
        """이름 → 정적 공격력. 쓰는 규칙이 있을 때만 계산한다."""
        if self._atk is None:
            self._atk = {c["name"]: static_atk(c) for c in self.squad}
        return self._atk


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


WHEN_KEYS = ("same_stage_cd_max", "same_stage_other", "with_member", "position",
             "equip_skill_min", "atk_rank")
# **`apply`가 쓸 수 있는 키는 `control` 하나다.** 버스트도 컨트롤이므로(유저가 아이콘·a·s·d로
# 직접 누른다 — docs/CONTROL.md §L0) 버스트 패턴이 `control["burst"]["pattern"]`으로 들어오면서
# 형제 키가 사라졌다. **닫힘의 근거는 그대로다** — `when`이 읽는 축(멤버·배치·버스트 단계·
# 육성·정적 스탯)을 `apply`가 쓰지 않으므로 규칙이 자기 결과를 다시 보는 일이 원리적으로
# 없고, 판정이 한 패스로 끝난다. 키가 하나로 줄어도 이 성질은 유지된다.
APPLY_KEYS = ("control",)


def _when_ok(name: str, cond: dict, ctx: _RuleCtx) -> bool:
    """부착 규칙의 적용 조건. 지원하는 키는 `WHEN_KEYS` 여섯. 모르는 키는 조립 시점에 실패한다.

    **조합 축** — 스쿼드 명단만 본다.
    `same_stage_cd_max: N` — 같은 버스트 단계에 쿨타임 N초 이하인 **다른 멤버가 있을 때만.**
    마스트 : 로망틱 메이드의 "3의 배수"가 20초 쿨 2버와 함께일 때만 성립하는 걸 표현한다.
    `same_stage_other: true` — 같은 단계에 **다른 멤버가 하나라도 있을 때만.** 자기가 그
    단계의 유일한 멤버면 패턴(특히 "안 씀")을 걸어봐야 의미가 없으므로 아예 떼어낸다.
    `with_member: [이름...]` — 목록 중 **하나라도 스쿼드에 있을 때만.**
    `position: N` — 스쿼드 배치 순서가 N번째일 때만 (1 = 가장 왼쪽).

    **육성·스탯 축** — 조립이 끝난 스쿼드를 본다. 이쪽은 **가드로만 쓴다**(CONTROL.md §부착):
    조건이 깨지면 컨트롤을 떼어내 자동으로 돌릴 뿐, 다른 컨트롤로 갈아끼우지 않는다.
    육성에 따라 더 나은 컨트롤을 고르는 건 계산기가 아니라 쓰는 사람의 일이다.
    `equip_skill_min: {옵션: 하한}` — 그 장비 옵션의 **합산 퍼센트**가 하한 이상일 때만.
    줄별 리스트로 적힌 실계정 프로필도 같은 축으로 편다(`_equip_total`).
    `atk_rank: {top: N, exclude: [이름...]}` — `exclude`를 뺀 스쿼드에서 정적 공격력
    **N위 안**일 때만. **동률은 안에 들지 않는다** — 순위가 갈리지 않으면 버프가 남에게
    갈 수 있고, 그 경우를 걸러 내는 게 이 조건의 목적이기 때문이다.
    """
    nk = _nikke()
    members = ctx.names
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
        elif key == "equip_skill_min":
            es = (ctx.by_name.get(name) or {}).get("equip_skills") or {}
            ok = all(_equip_total(es.get(opt, 0)) >= float(floor)
                     for opt, floor in val.items())
        elif key == "atk_rank":
            if bad := set(val) - {"top", "exclude"}:
                raise SystemExit(f"[{name}] atk_rank가 모르는 키를 받았다: {sorted(bad)}")
            excl = set(val.get("exclude") or []) | {name}
            mine = ctx.atk.get(name, 0.0)
            ahead = sum(1 for m, a in ctx.atk.items() if m not in excl and a >= mine)
            ok = ahead < int(val.get("top", 1))
        else:
            raise SystemExit(f"[{name}] 알 수 없는 부착 조건 키: {key!r}. "
                             f"있는 것: {list(WHEN_KEYS)}")
        if not ok:
            return False
    return True


def _rules_for(name: str, members: list[str]) -> list[tuple[str | None, dict]]:
    """이 니케에게 걸릴 수 있는 규칙 전부 `[(택틱 라벨, 규칙), ...]`. 조건은 아직 안 본다.

    니케별 레이어의 `_rules`가 먼저고, **자동**(`manual`이 아닌) 택틱의 규칙이 뒤에 온다 —
    뒤가 이기므로 코드가 고른 담당(`pick`)이 캐릭터 기본을 덮는다.
    """
    out: list[tuple[str | None, dict]] = []
    for rule in (CHAR_DEFAULTS.get(name) or {}).get("_rules") or []:
        out.append((rule.get("tactic"), _norm_rule(rule)))
    for tname, t in TACTICS.items():
        if t.get("manual"):
            continue
        for rule in t.get("_rules") or []:
            who = rule.get("who") or PICKERS[rule["pick"]](members)
            if who == name:
                out.append((tname, _norm_rule(rule)))
    return out


def _norm_rule(rule: dict) -> dict:
    """규칙의 `apply`를 정규화한다 — 종전 `burst_pattern` 형제 키를 컨트롤 안으로 옮긴다.

    **읽는 자리를 하나로 만드는 게 목적이다.** `resolve_rules()`와 `applied_tactics()`가
    같은 모양을 보게 여기서 한 번만 편다. 정본: docs/CONTROL.md §부착.
    """
    apply = rule.get("apply") or {}
    if "burst_pattern" not in apply:
        return rule
    apply = dict(apply)
    pat = apply.pop("burst_pattern")
    apply = deep_merge(apply, {"control": {"burst": {"pattern": pat}}})
    return {**rule, "apply": apply}


def resolve_rules(squad: list[dict], overrides: dict[str, dict] | None = None,
                  no_layer: set[str] | None = None) -> list[dict]:
    """조립이 끝난 스쿼드에 **조건부 부착 규칙**을 얹는다 (제자리 수정 후 그대로 반환).

    `build_squad()`의 2단계 중 ②다. 조건이 조합뿐 아니라 **육성 결과**(장비 옵션 합계)와
    **스탯 순위**까지 볼 수 있는 것은 여기가 조립 이후이기 때문이다.

    overrides : 호출자 오버라이드. 규칙을 얹은 뒤 **다시 얹는다** — 지정은 언제나 이긴다.
                버스트 패턴을 직접 준 캐릭터가 조건을 보지 않는 것도 이 재적용의 귀결이다.
    no_layer  : 레이어를 끈 이름들(`sim.py --auto`). 규칙도 같이 건너뛴다.

    캐릭터 dict에는 확정된 결과만 남으므로 이탈 보고에도 "실제로 걸린 것"만 나온다.
    """
    over = overrides or {}
    skip = no_layer or set()
    ctx = _RuleCtx(squad)
    for c in squad:
        name = c["name"]
        if name in skip:
            continue
        applied: dict = {}
        for _label, rule in _rules_for(name, ctx.names):
            if not _when_ok(name, rule.get("when") or {}, ctx):
                continue
            applied = deep_merge(applied, rule.get("apply") or {})
        if not applied:
            continue
        if bad := set(applied) - set(APPLY_KEYS):
            raise SystemExit(f"[{name}] 규칙 apply가 쓸 수 없는 키를 썼다: {sorted(bad)}. "
                             f"쓸 수 있는 것: {list(APPLY_KEYS)}")
        c.update(deep_merge(deep_merge(c, applied), over.get(name)))
    for c in squad:
        _fold_burst_pattern(c)
    return squad


def _fold_burst_pattern(char: dict) -> None:
    """종전 표기 `char["burst_pattern"]`(지정)을 `control["burst"]["pattern"]`으로 접는다.

    **답을 한 자리에만 둔다.** 두 자리에 남으면 이탈 보고가 레이어 패턴과 지정 패턴을
    나란히 찍어, 실제로는 지정이 이겼는데 레이어가 살아 있는 것처럼 읽힌다 — 이 레포에서
    가장 조용히 틀리는 경로다(AGENTS.md §Simulation invariants).

    **키의 유무로 판정한다.** `None`은 "패턴 없이 간다"는 유효한 지정이고(CLI `없음`),
    그걸 값으로 구분하지 않으면 끄는 방법이 사라진다.
    """
    if "burst_pattern" not in char:
        return
    pat = char.pop("burst_pattern")
    burst = char.setdefault("control", {}).setdefault("burst", {})
    if pat is None:
        burst.pop("pattern", None)
        if not burst:
            char["control"].pop("burst", None)
    else:
        burst["pattern"] = pat


def _has_applied(char: dict, apply: dict) -> bool:
    """`apply`의 잎값이 캐릭터 dict에 그대로 살아 있는가 (부분집합 검사).

    조건이 맞았어도 실제로 붙지 않는 경우가 있다 — 레이어를 껐거나(`--auto`), 호출자가
    같은 자리를 다른 값으로 덮었거나. 그때 택틱이 걸렸다고 보고하면 거짓말이 된다.
    """
    for k, v in apply.items():
        cur = char.get(k, None)
        if isinstance(v, dict):
            if not isinstance(cur, dict) or not _has_applied(cur, v):
                return False
        elif cur != v:
            return False
    return True


def applied_tactics(squad: list[dict]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """이 스쿼드의 **자동** 택틱 현황 → `(붙은 것, 조건은 맞았으나 안 붙은 것)`.

    라벨은 규칙에 붙어 있고(`tactic`), 규칙이 사는 곳은 니케별 레이어든 택틱 카탈로그든
    상관없다. `apply`가 쓰는 키와 `when`이 읽는 키가 겹치지 않으므로 조립 후에 다시 재도
    조건 판정은 같은 답이 나온다.

    **조건이 맞는 것만으로는 걸렸다고 하지 않는다.** `--auto`(레이어 끄기)나 호출자
    오버라이드로 실제로는 안 붙는 경우가 있어서, 캐릭터 dict에 남아 있는지까지 본다 —
    그렇지 않으면 컨트롤이 하나도 없는 결과에 택틱 머리줄이 붙는다.
    """
    ctx = _RuleCtx(squad)
    on: dict[str, list[str]] = {}
    off: dict[str, list[str]] = {}
    for name in ctx.names:
        for label, rule in _rules_for(name, ctx.names):
            if not label or not _when_ok(name, rule.get("when") or {}, ctx):
                continue
            bucket = on if _has_applied(ctx.by_name[name], rule.get("apply") or {}) else off
            bucket.setdefault(label, []).append(name)
    return on, off


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
    """캐릭터의 **버스트 조작**을 모아 시뮬 config로 넘긴다.
    `control["burst"]["pattern"]` → `config["burst_pattern"]` ·
    `control["burst"]["delay"]` → `config["burst_delay"]`.

    버스트는 스쿼드 상태머신(`BurstController`)이 굴리므로 캐릭터가 아니라 config로 간다 —
    패턴이 원래 그 통로를 쓰고 있었고 딜레이도 같은 통로를 탄다. `calculator/`가 ②(부착)를
    모른다는 불변식은 그대로다.

    **종전 표기 `char["burst_pattern"]`도 받고, 있으면 그쪽이 이긴다.** 보고서 스펙·CLI
    `--burst-pattern`이 쓰는 **지정** 자리라서다 — 지정은 언제나 레이어를 이긴다
    (`없음`/`null`로 끄는 것까지 포함하므로 값이 아니라 **키의 유무**로 판정한다).

    `burst_sequence`를 명시한 config는 건드리지 않는다 — 그쪽이 사이클별 순서를
    전부 결정하므로 패턴이 개입할 자리가 없다.
    """
    cfg = copy.deepcopy(config or {})
    delays = {c["name"]: d for c in squad
              if (d := ((c.get("control") or {}).get("burst") or {}).get("delay"))}
    if delays:
        cfg["burst_delay"] = {**delays, **(cfg.get("burst_delay") or {})}
    if cfg.get("burst_sequence"):
        return cfg
    pats = {}
    for c in squad:
        chosen = ((c.get("control") or {}).get("burst") or {}).get("pattern")
        if "burst_pattern" in c:
            chosen = c["burst_pattern"]     # 종전 표기 = 지정. 언제나 이긴다
        v = burst_pattern_of(c["name"], chosen)
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
    if isinstance(v, list) and v and all(isinstance(e, dict) and "mode" in e for e in v):
        # 클릭 스케줄(`control.click`)은 항목마다 dict라 그대로 찍으면 한 줄이 길다.
        # 읽는 사람이 알아야 하는 건 **어느 구간에서 무엇을 하나**뿐이다.
        # 앵커 구간에는 `window`가 없으므로 표기는 `timeline`이 만든다(정본 한 곳).
        return " → ".join(f"{timeline._when_label(e)}:{e['mode']}" for e in v)
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


def char_deviations(char: dict, ref: dict | None = None,
                    profile: GrowthProfile | None = None
                    ) -> list[tuple[str, object, object, str]]:
    """캐릭터 dict가 기준선에서 얼마나 벗어났는지. `(키, 기준값, 실제값, 출처)` 목록.

    출처는 `레이어`(data/char_defaults.json의 무조건분 + 조건부 규칙) 또는 `지정`
    (호출자 오버라이드).

    ref : 오버라이드 없이 조립한 **같은 스쿼드**의 같은 캐릭터. 조건부 규칙이 조합·육성을
          보므로 이름만으로는 기준선을 세울 수 없어 `squad_deviations()`가 만들어 넘긴다.
          주지 않으면 조합·조건부 규칙 없이 이름만으로 세운다(단발 조회용).

    **기준선은 프로필 유무로 갈린다.** 프로필 없이는 1층(고정 스펙)이고, 프로필을 끼면
    `1층+레이어+프로필`이 기준선이 된다 — 그러지 않으면 육성 키 전부가 이탈로 잡혀
    (캐릭터당 열 줄 남짓) 정작 봐야 할 호출자 지정이 묻힌다. 프로필을 썼다는 사실 자체는
    `format_deviations`가 머리줄로 따로 알린다.
    """
    if ref is None:
        ref = build_char(char.get("name", ""), profile=profile)
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
    """스쿼드 전체의 기준선 이탈. 벗어난 캐릭터만 담는다.

    기준선은 **같은 명단을 오버라이드 없이 조립한 스쿼드**다. 조건부 규칙이 조합·육성을
    보므로 캐릭터 하나만 따로 세워서는 "레이어가 줬을 모습"을 알 수 없다.
    """
    members = [c.get("name", "") for c in squad]
    ref = {c["name"]: c for c in build_squad(members, profile=profile)}
    return {c.get("name", "?"): d for c in squad
            if (d := char_deviations(c, ref.get(c.get("name", "")), profile))}


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
    # 택틱은 러너에서 전개되고 사라지므로(docs/CONTROL.md §택틱) 결과에 흔적이 남는 자리가
    # 여기뿐이다 — 어떤 택틱이 누구에게 걸렸는지 한 줄로 싣는다.
    # 조건은 맞았는데 안 붙은 것(`--auto`·오버라이드)은 **없다고 명시**한다. 줄을 그냥
    # 빼면 "조건이 안 맞았다"와 구분되지 않아, 켜 둔 줄 알고 결과를 읽게 된다.
    tacts, dropped = applied_tactics(squad)
    if tacts:
        head.append(f"{indent}택틱: " + " · ".join(
            f"{t}({', '.join(who)})" for t, who in tacts.items()))
    if dropped:
        head.append(f"{indent}택틱 없음 — 조건은 맞으나 붙지 않았다: " + " · ".join(
            f"{t}({', '.join(who)})" for t, who in dropped.items()))
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
