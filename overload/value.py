"""옵션 한계가치 — "이 줄 하나가 스쿼드 총딜을 몇 % 올리나".

비용(모듈)과 가치(딜)를 잇는 자리다. `reach.py`·`policy.py`가 여기서 나온 숫자를
쓰고, 여기는 계산기를 그대로 부른다.

## 왜 줄 목록이 1차 자료인가

계산기 입력은 옵션별 **합산 퍼센트**(`equip_skills`)지만, 장탄·차속은 같은 레벨끼리만
합산한 뒤 그룹마다 따로 반올림한다(`buff_manager._equip_option_groups`). 합계 스칼라
129.64에서는 그게 레벨 10 두 줄인지 다른 조합인지 되돌릴 수 없어 **줄을 하나 더한
결과를 정확히 만들 수 없다**. 그래서 이 패키지는 `Build`(줄 목록)를 들고 다니고
`equip_skills()`로 그때그때 계산기 입력을 만든다.

## 여기서 내는 것과 안 내는 것

낸다: 줄 **하나**의 값(`per_line`), 그 캐릭터의 덱 비중(`share`), 크확·크댐 교차항
(`crit_cross`).

안 낸다: 여러 줄을 합친 값. 서로 다른 옵션은 더해지지 않고 곱해지므로 조립은
`policy.Values.worth()`가 한다 (근거와 실측은 `README.md` §가치 모델).

`level_curve()`·`stack_curve()`·`separability_error()`는 그 구조가 아직 성립하는지
새 덱에서 다시 재보는 도구다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from calculator.timeline import simulate
from context import spec as char_spec

from .mechanics import OPTIONS, STAT_KEY

# 한 부위 3칸 × 4부위. 줄 하나는 (옵션, 레벨).
Line = tuple[str, int]
Build = tuple[Line, ...]

PIECES = 4
SLOTS_PER_PIECE = 3
MAX_LINES = PIECES * SLOTS_PER_PIECE

REF_LEVEL = 10   # 한계가치를 재는 기준 레벨. 기본 스펙이 쓰는 레벨이기도 하다

# **타이밍을 흔드는 옵션.** 장탄은 재장전 시점을, 차속은 발사 간격을 바꾸므로 그 캐릭터의
# 딜뿐 아니라 팀 버프가 언제 터지는지까지 바꾼다 — 한 명만 강화해도 다른 캐릭터의 딜이
# 움직인다. 나머지 일곱은 배율만 건드려 캐릭터 사이로 새지 않는다.
#
# 이 분류는 측정으로 잡았다 (2026-08-20, 리틀 머메이드·크라운·라피 : 레드 후드·미하라 :
# 본딩 체인·헬름 / 철갑 코어 40px). fast와 exact가 일곱 옵션에서는 소수 셋째 자리까지
# 같았고, 차속은 최대 0.49%p, 장탄은 부호까지 어긋났다. 새 덱에서 의심되면
# `separability_error()`로 다시 잰다.
TIMING_OPTIONS: frozenset[str] = frozenset({"장탄", "차속"})


def default_build() -> Build:
    """기본 스펙의 오버로드 구성 — 우코 4줄 · 공 2줄 · 장탄 2줄, 전부 레벨 10.

    정본은 `context/spec.py`의 `DEFAULT_CHAR["equip_skills"]`다. 여기서는 그 값을
    줄 목록으로 되짚어 만들고, 되짚은 결과가 정본과 같은지 즉시 확인한다.
    """
    build: Build = (("우코", 10),) * 4 + (("공", 10),) * 2 + (("장탄", 10),) * 2
    want = char_spec.DEFAULT_CHAR["equip_skills"]
    got = equip_skills(build)
    for stat, v in want.items():
        mine = sum(got.get(stat) or [])
        if abs(mine - float(v if not isinstance(v, list) else sum(v))) > 1e-6:
            raise AssertionError(f"기본 스펙 재구성 불일치: {stat} {mine} != {v}")
    return build


def equip_skills(build: Build) -> dict[str, list[float]]:
    """줄 목록 → 계산기 입력(`equip_skills`). 줄별 퍼센트 리스트로 낸다.

    리스트로 내야 장탄·차속의 그룹 반올림이 인게임과 같아진다. 그룹 반올림이 없는
    스탯은 계산기가 어차피 합계로 접으므로 리스트로 줘도 결과가 같다.
    """
    out: dict[str, list[float]] = {STAT_KEY[o]: [] for o in OPTIONS}
    for opt, lv in build:
        out[STAT_KEY[opt]].append(char_spec.overload(STAT_KEY[opt], 1, lv))
    return out


def line_pct(option: str, level: int) -> float:
    """옵션 한 줄의 인게임 수치(%). 정본은 `equipment_skills.json`."""
    return char_spec.overload(STAT_KEY[option], 1, level)


def add_line(build: Build, option: str, level: int) -> Build:
    return tuple(sorted(build + ((option, level),)))


@dataclass
class DeckContext:
    """가치를 재는 배경 — 누구와 함께, 누구를 상대로, 어떤 육성으로.

    한계가치는 이 배경에 딸린 값이다. 같은 우코 줄이라도 약점 속성이 아니면 0이고,
    크확은 함께 선 버퍼가 이미 밀어놨으면 값이 떨어진다.
    """

    names: list[str]
    builds: dict[str, Build] = field(default_factory=dict)
    enemy: dict | None = None
    config: dict | None = None
    profile: object | None = None

    def build_of(self, name: str) -> Build:
        return self.builds.get(name, default_build())

    def squad(self, builds: dict[str, Build] | None = None) -> list[dict]:
        use = {**{n: self.build_of(n) for n in self.names}, **(builds or {})}
        over = {n: {"equip_skills": equip_skills(b)} for n, b in use.items()}
        return char_spec.build_squad(self.names, chars=over, profile=self.profile)

    def run(self, builds: dict[str, Build] | None = None) -> dict[str, int]:
        """캐릭터별 누적 딜. 기대값 모드라 결정론적이다 — 유한차분에 난수가 섞이면 안 된다."""
        cfg = {"rng_mode": "expected", **(self.config or {})}
        res = simulate(self.squad(builds), config=cfg, enemy=self.enemy)
        return {**res.char_total, "_총합": res.squad_total}


@dataclass
class Marginals:
    """한 덱에서 잰 옵션별 한계가치.

    `per_line[캐릭터][옵션]` = 그 캐릭터에게 레벨 `ref_level` 줄 하나를 더했을 때의
    **스쿼드 총딜 상승률(%)**. 분모가 스쿼드 총딜인 이유는 유저가 비교하는 단위가
    덱 합계라서다 — 딜 비중이 낮은 캐릭터의 옵션은 그만큼 값이 낮게 나와야 맞다.

    `share`와 `crit_cross`는 줄이 **여럿 붙었을 때**를 위한 것이다. 한계가치를 그냥
    더하면 안 된다 — 서로 다른 배율은 곱해지고, 크확·크댐은 곱 이상으로 얽힌다
    (§교차항). 그 조립은 `policy.Values.worth()`가 한다.
    """

    ref_level: int
    baseline: dict[str, int]
    per_line: dict[str, dict[str, float]]
    runs: int
    share: dict[str, float] = field(default_factory=dict)
    crit_cross: dict[str, float] = field(default_factory=dict)

    def line(self, char: str, option: str, level: int) -> float:
        """줄 하나의 가치(스쿼드 총딜 %). 레벨은 인게임 수치표에 비례한다 — 실측 확인됐다."""
        base = self.per_line[char][option]
        if base == 0.0:
            return 0.0
        return base * line_pct(option, level) / line_pct(option, self.ref_level)

    def ranking(self, char: str) -> list[tuple[str, float]]:
        return sorted(self.per_line[char].items(), key=lambda kv: -kv[1])


def marginals(ctx: DeckContext, ref_level: int = REF_LEVEL,
              options: tuple[str, ...] = OPTIONS, mode: str = "auto") -> Marginals:
    """옵션별 한계가치를 잰다.

    정의는 exact다 — "그 캐릭터에게 줄 하나를 더했을 때 **스쿼드 총딜**이 몇 % 오르나".
    다른 캐릭터에게 새는 몫까지 그 줄의 값으로 친다. 실제로 그만큼 이득이기 때문이다.

    mode="exact" — 캐릭터마다 따로 얹는다. 옵션당 인원수만큼 돌린다 (5인 9옵션 = 47회)
    mode="fast"  — 옵션마다 전원에게 동시에 얹고 1회 돌린 뒤 캐릭터별 딜 증가분으로 쪼갠다
                   (11회). 캐릭터 사이에 영향이 없을 때만 exact와 같다
    mode="auto"  — 기본. `TIMING_OPTIONS`만 exact, 나머지는 fast (5인 9옵션 = 19회)

    `separability_error()`가 fast를 써도 되는지 판정한다. 회 수에 1을 더하는 것은
    크확·크댐 교차항 측정이다.
    """
    if mode not in ("fast", "exact", "auto"):
        raise ValueError(f'mode는 "fast"·"exact"·"auto" 중 하나여야 한다: {mode!r}')

    base = ctx.run()
    total = base["_총합"]
    if not total:
        raise ValueError("기준 딜이 0이다 — 덱 구성을 확인해야 한다")

    per: dict[str, dict[str, float]] = {n: {} for n in ctx.names}
    runs = 1

    for opt in options:
        exact = mode == "exact" or (mode == "auto" and opt in TIMING_OPTIONS)
        if exact:
            for n in ctx.names:
                got = ctx.run({n: add_line(ctx.build_of(n), opt, ref_level)})
                runs += 1
                per[n][opt] = (got["_총합"] - total) / total * 100.0
        else:
            bumped = {n: add_line(ctx.build_of(n), opt, ref_level) for n in ctx.names}
            got = ctx.run(bumped)
            runs += 1
            for n in ctx.names:
                per[n][opt] = (got[n] - base[n]) / total * 100.0

    # 크확·크댐 교차항. 기대 크리 배율이 `1 + 크확 × (0.5 + 크댐)` 꼴이라 둘은 곱셈
    # 채널로 안 갈라지고 쌍선형으로 얽힌다 — 실측에서 크댐 2줄이 크확 한계가치를
    # 62% 올렸다. 둘 다 배율만 건드리는 옵션이라 fast로 재도 된다.
    cross: dict[str, float] = {}
    if {"크확", "크댐"} <= set(options):
        bumped = {n: add_line(add_line(ctx.build_of(n), "크확", ref_level), "크댐", ref_level)
                  for n in ctx.names}
        got = ctx.run(bumped)
        runs += 1
        for n in ctx.names:
            both = (got[n] - base[n]) / total * 100.0
            cross[n] = both - per[n]["크확"] - per[n]["크댐"]

    share = {n: base[n] / total for n in ctx.names}
    return Marginals(ref_level, base, per, runs, share, cross)


def separability_error(ctx: DeckContext, option: str, ref_level: int = REF_LEVEL
                       ) -> dict[str, float]:
    """한 캐릭터만 강화했을 때 **다른** 캐릭터의 딜이 얼마나 흔들리는지(%).

    0이면 fast 모드가 정확하다. 0이 아니면 그 옵션은 팀에 새는 축이라
    exact 모드로 재야 한다.
    """
    base = ctx.run()
    out: dict[str, float] = {}
    for n in ctx.names:
        got = ctx.run({n: add_line(ctx.build_of(n), option, ref_level)})
        leak = sum(abs(got[m] - base[m]) for m in ctx.names if m != n)
        out[n] = leak / base["_총합"] * 100.0
    return out


def level_curve(ctx: DeckContext, char: str, option: str,
                levels: tuple[int, ...] = (1, 5, 10, 15)) -> dict[int, float]:
    """레벨별로 직접 잰 줄 가치(스쿼드 총딜 %). 비례 가정을 검산하는 자리다."""
    base = ctx.run()
    total = base["_총합"]
    out = {}
    for lv in levels:
        got = ctx.run({char: add_line(ctx.build_of(char), option, lv)})
        out[lv] = (got["_총합"] - total) / total * 100.0
    return out


def stack_curve(ctx: DeckContext, char: str, option: str, upto: int = 4,
                level: int = REF_LEVEL) -> list[float]:
    """줄을 1개씩 쌓아가며 잰 누적 가치(%). 체감(오목)이 얼마나 되는지 본다."""
    base = ctx.run()
    total = base["_총합"]
    build = ctx.build_of(char)
    out = []
    for _ in range(upto):
        build = add_line(build, option, level)
        got = ctx.run({char: build})
        out.append((got["_총합"] - total) / total * 100.0)
    return out


def with_baseline(names: list[str], build: Build | None = None, **kw) -> DeckContext:
    """전원이 같은 구성을 쓰는 덱 배경. 기본은 기본 스펙 구성이다."""
    b = build if build is not None else default_build()
    return DeckContext(names=list(names), builds={n: b for n in names}, **kw)
