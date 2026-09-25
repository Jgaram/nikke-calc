"""파싱 대조 — 스킬 원문의 숫자·부속 블록·화살표가 파싱 결과에 빠짐없이 옮겨졌는가.

`scraper/nikke_scraped.json`(프리뷰면 `preview_skills.json`)의 원문과 `data/parsed_skills.json`을
**기계적으로** 대조한다. 문구의 뜻은 해석하지 않는다 — 해석 없이 판정되는 부분만 본다.

  N. 값        원문의 `{i}` 자리는 (레벨 1~10 값 열 **전체**가) 어느 효과엔가 옮겨졌고, 효과의
               레벨별 값은 전부 원문의 어느 `{i}`에서 왔는가. 부호 반전(▼)과 `TRANSFORMS`에
               등록한 환산만 인정한다. `{i}` 없이 숫자를 적은 수치 블록(`[차지 시간 0.7초로 고정]`)도
               그 숫자가 어느 효과엔가 있어야 한다.
  O. 부속 블록  값 블록 뒤의 `[N초 유지]`·`[지속]`·`[N 중첩]`·`[N초 간격]`·`[해제 불가]`·`[N발 유지]`·
               `[전투 중 N회 발동]`·`[부활 시 유지]`·`[N회 순차 공격]`이 그 값을 받은 효과의 필드에
               있는가 (`PARSING.md` Step 4 표). 블록 안의 「X [카운터] 수」는 `scaling: stack_count` +
               `scaling_ref`로, 대상 문구의 「(파츠 포함)」은 damage의 `hits_parts`로 옮겨졌는가.
  P. 화살표     buff 값의 부호가 원문 화살표와 맞는가 — ▲ 양수 / ▼ 음수 (`PARSING.md` §6).
  Q. 블록 선례   같은 문구의 값 블록을 로스터가 한 가지로만 옮겨 왔는데(다른 캐릭터 2명 이상 만장일치)
               이 캐릭터만 다른 (type, stat, 부호)로 옮겼는가.
  R. 키 숫자    timing·condition·target 키의 숫자 인자(`hit_count:6` · `self_stack_above:골든 칩:10`)가
               원문에 있는 숫자인가. 없으면 옮기다 틀렸거나 원문 밖 지식에서 왔다.
  S. 문구 조각   「자신을 제외한」→ `*excl*` · 「시전자 기준 … ▲」→ `*caster_based*` · 「명중 시」↔ 발사 축 금지
               처럼 조각 하나가 요구하는 키 성분이 있는가 (`FRAGMENTS`). 통문구로는 처음 보는 문구도
               조각으로는 판정된다.

N·O·P·R·S는 **처음 보는 문구에도 성립한다** — 새 메커니즘이 와도 숫자·부속 블록·화살표의 문법과
문구 조각은 같다. Q는 선례가 있는 문구에만 성립한다. 선례가 없는 문구는 판정하지 않고 캐릭터 감사표에
**선례 없음**으로 모은다 — AI가 스스로 판단한 자리이자 유저가 검토할 자리다.

**파싱 실수 유형이 새로 드러나면** 일회성 전수 스캔으로 끝내지 않는다. 숫자·부속 블록 문법이면
N·O·R에, 문구 조각이면 `FRAGMENTS`에 한 줄을 더해 다음 캐릭터부터 코드가 막게 한다.

## 값 연결

`{i}`와 효과는 **값 열 전체**가 같을 때만 잇는다(`runner/precedent.py`는 레벨 10 하나로 잇는다).
레벨 10개가 모두 같은 우연은 없다 — 로스터 1,410자리 중 값 열로 효과를 못 찾는 자리는 도입 시점에
하나뿐이었고 그것도 환산(`TRANSFORMS`)이었다. 같은 판본에서 두 `{i}`의 값 열이 같으면 값만으로는
못 가르므로 화살표 부호 → 로스터 선례 → 원문 순서로 좁히고, 순서로 짝지은 연결은 **추정**으로 두어
O·Q·S 판정에서 뺀다(틀린 짝으로 틀린 경보를 내지 않는다). P는 블록이 아니라 효과 쪽에서 보므로
값 중복과 무관하게 판정된다.

## 문구 선례 (감사표 전용)

clause 머리의 트리거 문구(「풀 차지 공격 시」)와 대상 문구(「자신을 제외한 아군 전체에게」)도 로스터의
선례와 대조해 보여 준다. 머리 문구를 쪼개는 것은 휴리스틱이라 **게이트가 아니라 참고**다 —
`⚠`는 틀렸다는 뜻이 아니라 선례와 다르니 근거를 대라는 뜻이다.

## 미발동 (`--sim`)

정적 대조가 못 잡는 가장 흔한 결함은 **조용히 안 켜지는 효과**다(그레이브 `방열` 계열, 에이드
passive, `core_hit_count` — 조건이 거짓이거나 담체가 없거나 엔진 분기가 없어 에러 없이 꺼져 있었다).
`--sim`은 시나리오 `## 검증 스쿼드`(또는 `--squad`)를 돌려 이 캐릭터의 효과마다 발동 횟수를 세고,
0회인 효과를 트리거·조건과 함께 뽑는다. 기본 적은 파츠·코어·쫄몹·보스 공격이 없으므로 그쪽 조건은
0회가 정상이다 — 목록은 「정상 0회」와 「조용히 죽은 효과」를 사람이 가르는 출발점이다.

사용:
  python -m runner.parsecheck                           # 로스터 전체 N~S (doclint가 부른다). 위반 시 exit 1
  python -m runner.parsecheck "레이븐"                   # 캐릭터 감사표 — clause별 블록 → 효과 · 선례 대조
  python -m runner.parsecheck "레이븐" --sim             # + 시나리오 검증 스쿼드의 미발동 효과
  python -m runner.parsecheck "레이븐" --sim --squad "레이븐,크라운,나가,이브,라피 : 레드 후드"
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SCRAPED = ROOT / "scraper" / "nikke_scraped.json"
PREVIEW = ROOT / "scraper" / "preview_skills.json"
SKILLS = ROOT / "data" / "parsed_skills.json"
NIKKE = ROOT / "data" / "parsed_nikke.json"
SCENARIOS = ROOT / "docs" / "scenarios"

TEST_PREFIX = "test_"
TOL = 0.011          # 원문은 소수 둘째 자리 문자열이다 — 파싱이 float로 옮기며 생기는 반올림만 허용
MIN_PRECEDENT = 2    # 블록 선례가 「만장일치」로 서려면 필요한 다른 캐릭터 수

_SLOT = re.compile(r"\{(\d+)\}")
_NUM = re.compile(r"\d+(?:\.\d+)?")
_WS = re.compile(r"\s+")
_ARROW = re.compile(r"\s*([▲▼])")
# `[휘슬 : 공격력 {0}% ▲]`의 `휘슬 : ` — 이름 있는 상태의 이름은 캐릭터마다 달라 선례 키에서 뗀다
_STATE_NAME = re.compile(r"^[^:\[\]]+?\s:\s(?=.*#)")
# `[공격력 {0}% X 사제 탄창 개수 ▲]`의 `X 사제 탄창 개수` — 값이 그 카운터에 비례한다는 표시.
# stat을 바꾸지 않으므로(→ `scaling: stack_count` + `scaling_ref`) 선례 키에서는 뗀다
_MULT = re.compile(r"\s+X\s+(.+?)(?=\s*[▲▼]|\s+(?:추가 |지속 |분배 )?대미지$)")
# 수치 블록에서 숫자가 아닌 숫자 — 슬롯 번호(`스킬 2`)와 이름(`Mk2`)
_NOT_QUANTITY = re.compile(r"스킬\s*\d|Mk\d+")
CODES = ("풍압", "수냉", "작열", "전격", "철갑")


# ── 부속 블록 ──────────────────────────────────────────────────────────────
# 앞 블록이 만든 효과의 **필드**로 옮겨지는 블록 (`PARSING.md` Step 4 표). 이름 · 패턴 · 옮겨질 필드.
MODIFIERS: tuple[tuple[str, re.Pattern, str], ...] = (
    ("duration", re.compile(r"(\d+(?:\.\d+)?)\s*초\s*유지"), "duration"),
    ("duration", re.compile(r"(\d+(?:\.\d+)?)\s*초\s*X\s*.+?횟수만큼\s*유지"),
     "duration + duration_scaling"),
    ("infinite", re.compile(r"지속|풀 버스트 타임 동안 지속"), "duration -1"),
    ("stack", re.compile(r"(\d+)\s*중첩"), "max_stack"),
    ("stack", re.compile(r"최대\s*(\d+)\s*회"), "max_stack"),
    ("interval", re.compile(r"(\d+(?:\.\d+)?)\s*초\s*간격"), "tick_interval"),
    ("irremovable", re.compile(r"해제 불가"), "polarity *_irremovable"),
    ("bullets", re.compile(r"(\d+)\s*발\s*유지"), "duration_bullets"),
    ("battle_limit", re.compile(r"전투 중\s*(\d+)\s*회\s*발동"), "max_trigger"),
    ("revive", re.compile(r"부활 시 유지"), "persist_on_revive"),
    ("sequential", re.compile(r"(\d+)\s*회\s*순차\s*공격"), "stat sequential_damage:N"),
    ("sequential", re.compile(r".+?만큼\s*(?:순차|연속)\s*공격"), "stat sequential_damage:[게이지]"),
    ("count_attack", re.compile(r".+?(?:갯수|개수|수)\s*만큼\s*공격"), "scaling stack_count"),
)
# 선례 키의 맥락 — 같은 문구라도 뒤따르는 부속 블록이 type·stat을 가른다
# (`[버스트 스킬 재사용 시간 N초 ▼]`는 수명이 붙으면 buff `burst_cooldown`, 없으면 instant
# `burst_cooldown_reduce` — `PARSING.md` §6. `[N회 순차 공격]`이 붙으면 `sequential_damage`)
_CTX = {"duration": "수명", "infinite": "수명", "stack": "수명", "bullets": "수명",
        "sequential": "순차", "interval": "간격"}

# ── 화살표 예외 ───────────────────────────────────────────────────────────
# ▼ 문구를 **양수**로 적는 것이 규약인 stat. 값은 사유다(사유 없는 예외는 검사를 무력화한다).
SIGN_POSITIVE_REDUCTION: dict[str, str] = {
    "burst_cooldown": "양수 = 재사용 시간 감소량 (`PARSING.md` §6 · 목단 `열혈` · 밀크 `승부욕 2`)",
}


def _reload_fixed(base: float):
    """`[재장전 속도 N% 증가 상태로 고정]` → 재장전 시간(초) = 기본 재장전 × (1 − N/100).

    `GAMEPLAY.md` §값 산정. 질 `슈퍼 캅`(1.0초 × …) · 엑시아 애장품 3단계 `인베이젼 3`(2.0초 × 0.05).
    AI가 손으로 곱하는 자리라 코드가 같은 식으로 다시 계산해 맞춰 본다.
    """
    return lambda v: tuple(round(base * (1 - x / 100), 4) for x in v)


# stat → (설명, 기본값 dict를 받아 환산 함수를 돌려주는 팩토리)
TRANSFORMS: dict[str, tuple[str, object]] = {
    "reload_time_fixed": ("기본 재장전 × (1 − N/100)",
                          lambda nikke: _reload_fixed(float(nikke.get("reload_time") or 0))),
}

# ── 예외 ───────────────────────────────────────────────────────────────────
# 판정 키(출력 그대로) → 사유. **사유 없이 등록하지 않는다.** 원문과 다르게 옮긴 것이 의도된 파싱
# 결정일 때만 쓴다 — 결정의 근거가 `note`·`PARSING-CHARS.md`에 있어야 한다.
EXEMPT: dict[str, str] = {
    # O — 부속 블록
    "마르차나 : 마린 스터디 / 스킬2 / 휘슬 / [5 중첩]":
        "원문 `[5 중첩]` + `[휘슬 중첩량 4개 ▲]`를 max_stack 9 하나로 접었다(효과 note)",
    "길로틴 : 윈터 슬레이어 / 스킬2 / 경험치 / [100 중첩]":
        "두 clause가 한 중첩 풀을 채워 중첩 수를 게이지 `경험치`(gauge_max 100)로 옮겼다(효과 note, "
        "유저 확인 2026-09-13)",
    # Q — 블록 선례
    "솔린 : 프로스트 티켓 / 스킬1 / 버스트 스킬 재사용 시간 감소":
        "원문에 수명 블록이 없는데 지속형 buff `burst_cooldown`으로 옮겼다 — 선례는 전부 instant "
        "`burst_cooldown_reduce`. 알려진 이탈이다: 솔린 시나리오 §해석 선언이 「instant로 바꿔도 정상 상태 "
        "결과 동일, 표기만 어긋나 있다」고 적었고 `PARSING-CHARS.md` 사쿠라 행이 선례 갈림으로 기록한다",
    # R — 키 숫자 (원문 밖 지식에서 온 숫자)
    "홍련 : 흑영 / 스킬1 / `gauge_eq:파죽:2`":
        "`[공격 횟수 별 효과]`의 3회·6회·9회를 단계 게이지 `파죽` 1·2·3으로 옮겼다 — 2는 6회 단계의 순번",
    "미하라 : 본딩 체인 / 스킬1 / `burst_enter:3`":
        "원문 「지정된 타이밍에」 — 그 타이밍(전투 시작 · 풀버스트 종료 · 3단계 진입)은 원문 밖 인게임 지식이다",
    "일레그 : 붐 앤 쇼크 / 스킬2 / `squad_ammo_consume:100`":
        "「유령 포획 시」를 탄 소비 100발로 근사했다(효과 note — 개인 hit_count:100 기준 근사)",
}


# ── 원문 구조 ──────────────────────────────────────────────────────────────

@dataclass
class Link:
    eff: dict
    field: str       # 값을 받은 필드 (`values` · `damage_coeff` · `trigger_values` …)
    how: str         # "같음" · "부호 반전" · 환산 설명
    sign: int        # +1 / -1
    weak: bool = False   # 값 열이 같은 자리끼리 순서로 짝지은 추정 연결 — O·Q·S 판정에서 뺀다


@dataclass
class Block:
    pos: int
    text: str
    lead: str                  # 이 블록 앞(직전 블록 뒤)의 본문 — 「효과 1 : 적 전체에게」 같은 국소 대상
    slots: list[int]
    mod: tuple[str, float | None] | None
    links: list[Link] = field(default_factory=list)
    ctx: str = ""

    @property
    def kind(self) -> str:
        if self.slots:
            return "value"
        return "mod" if self.mod else ("quantity" if _NUM.search(self.text) else "tag")


@dataclass
class Clause:
    text: str
    head: str
    blocks: list[Block]


@dataclass
class Version:
    """한 캐릭터의 한 스킬 판본 (기본 슬롯 또는 애장품 N단계 슬롯)."""
    char: str
    source: str
    favorite: int | None
    skill_name: str
    template: str
    cooldown: str | None
    levels: tuple[str, ...]
    slots: dict[int, tuple[float, ...]]
    effects: list[dict]
    clauses: list[Clause]
    preview: bool = False

    @property
    def label(self) -> str:
        fav = f" · 애장품 {self.favorite}단계" if self.favorite else ""
        return f"{self.source}{fav}"

    def key(self, *parts: str) -> str:
        return " / ".join([self.char, self.label, *parts])


def mod_kind(text: str) -> tuple[str, float | None] | None:
    s = text.strip()
    for name, rx, _field in MODIFIERS:
        m = rx.fullmatch(s)
        if m:
            return name, (float(m.group(1)) if m.groups() else None)
    return None


def split_blocks(body: str) -> list[tuple[int, int, str]]:
    """최상위 `[...]` 블록 (시작, 끝, 안쪽 문자열). `[최대 [록 온] 대상 수 10 ▲]`처럼 한 겹 중첩도 받는다."""
    out: list[tuple[int, int, str]] = []
    depth, start = 0, None
    for i, ch in enumerate(body):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append((start, i, body[start + 1:i]))
                start = None
    return out


def tokenize(template: str) -> list[Clause]:
    clauses: list[Clause] = []
    for raw in template.split("■"):
        if not raw.strip():
            continue
        spans = split_blocks(raw)
        head = raw[:spans[0][0]] if spans else raw
        blocks: list[Block] = []
        prev_end = 0
        for pos, (s, e, inner) in enumerate(spans):
            slots = [int(x) for x in _SLOT.findall(inner)]
            blocks.append(Block(pos, inner, raw[prev_end:s], slots,
                                None if slots else mod_kind(inner)))
            prev_end = e + 1
        # 맥락: 값 블록 뒤를 다음 비부속 블록 전까지 훑는다
        for i, b in enumerate(blocks):
            if b.kind == "mod":
                continue
            kinds = set()
            for nxt in blocks[i + 1:]:
                if nxt.kind != "mod":
                    break
                if (c := _CTX.get(nxt.mod[0])):
                    kinds.add(c)
            b.ctx = "·".join(sorted(kinds))
        clauses.append(Clause(raw, " ".join(head.split()), blocks))
    return clauses


def block_key(text: str) -> str:
    """선례 키 — 자리·숫자·이름 있는 상태의 이름·코드 이름을 지운 블록 문구."""
    t = _MULT.sub("", _SLOT.sub("#", text).strip())
    t = _NUM.sub("#", t)
    t = _STATE_NAME.sub("", t.strip())
    for c in CODES:
        t = t.replace(c, "<코드>")
    t = _ARROW.sub(r" \1", t)
    return _WS.sub(" ", t).strip()


def phrase_key(text: str) -> str:
    t = _NUM.sub("#", _SLOT.sub("#", text))
    for c in CODES:
        t = t.replace(c, "<코드>")
    return _WS.sub(" ", t).strip()


def stat_key(stat) -> str:
    return str(stat).split(":", 1)[0]


_WEAPONS = {"AR", "SR", "SG", "SMG", "MG", "RL"}
_CLASSES = {"화력형", "방어형", "지원형"}


def key_shape(key) -> str:
    """문구 선례 비교용 키 모양 — 숫자·이름 인자는 떼고 코드는 `<코드>`로 접는다.

    문구 쪽도 숫자는 `#`, 코드는 `<코드>`로 접으므로(`phrase_key`) 키도 같은 수준으로 맞춘다.
    `allies_code:수냉` → `allies_code:<코드>` · `same_target:마계흑룡파` → `same_target` ·
    `hit_count:6` → `hit_count` · `on_attack_count:{0}` → `on_attack_count` ·
    `event:state_end:망상` → `event:state_end` · `allies_code_weapon:전격:AR` → `allies_code_weapon:<코드>:AR`
    """
    head, *args = str(key).split(":")
    out = [head]
    if head == "event" and args:
        out.append(args.pop(0))
    for a in args:
        if a in CODES:
            out.append("<코드>")
        elif a in _WEAPONS or a in _CLASSES:
            out.append(a)
    return ":".join(out)


def _levels(d, levels: tuple[str, ...]) -> tuple[float, ...] | None:
    if not isinstance(d, dict) or not all(k in d for k in levels):
        return None
    try:
        return tuple(round(float(d[k]), 4) for k in levels)
    except (TypeError, ValueError):
        return None


def _close(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= TOL for x, y in zip(a, b))


def _scalars(effs: list[dict]) -> list[float]:
    """판본 효과들에 적힌 모든 스칼라 수치 + 키 꼬리 숫자(`hit_count:6`의 6)."""
    out: list[float] = []
    for e in effs:
        for k, v in e.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(abs(float(v)))
        trg = e.get("trigger") or {}
        tgt = e.get("target")
        texts = [*(trg.get("timing") or []), *(trg.get("condition") or []), str(e.get("stat")),
                 *(tgt if isinstance(tgt, list) else [tgt])]
        for t in texts:
            out.extend(float(m) for m in _NUM.findall(str(t)))
    return out


# ── 적재 ───────────────────────────────────────────────────────────────────

def _raw_versions(entry: dict):
    for n, (name, blk) in enumerate((entry.get("스킬") or {}).items(), 1):
        yield f"스킬{n}", None, name, blk
    for st in (entry.get("애장품") or {}).get("단계별") or []:
        if st.get("교체슬롯"):
            yield f"스킬{st['교체슬롯']}", st.get("단계"), st.get("스킬명", ""), st


def load() -> tuple[list[Version], dict[str, list[dict]], dict]:
    """파싱된 캐릭터의 판본 전부. 파싱된 효과가 없는 판본(애장품 미파싱)은 뺀다 — doclint H의 몫이다."""
    parsed = json.loads(SKILLS.read_text(encoding="utf-8"))
    parsed = {c: e for c, e in parsed.items() if not c.startswith(TEST_PREFIX)}
    nikke = json.loads(NIKKE.read_text(encoding="utf-8"))
    scraped = json.loads(SCRAPED.read_text(encoding="utf-8"))
    preview = json.loads(PREVIEW.read_text(encoding="utf-8")) if PREVIEW.exists() else {}

    versions: list[Version] = []
    for char, effects in parsed.items():
        entry, is_preview = scraped.get(char), False
        if not isinstance(entry, dict):
            entry, is_preview = preview.get(char), True
        if not isinstance(entry, dict):
            continue
        for source, fav, name, blk in _raw_versions(entry):
            effs = [e for e in effects if e.get("source") == source and e.get("favorite") == fav]
            if not effs:
                continue
            raw_vals = blk.get("values") or {}
            levels = tuple(sorted(raw_vals, key=int))
            slots: dict[int, tuple[float, ...]] = {}
            for i in sorted({int(x) for x in _SLOT.findall(blk.get("template") or "")}):
                try:
                    slots[i] = tuple(round(float(raw_vals[lv][i]), 4) for lv in levels)
                except (IndexError, KeyError, TypeError, ValueError):
                    pass      # 자리 수와 값 수가 어긋남 — N이 「옮겨지지 않은 자리」로 잡는다
            versions.append(Version(char, source, fav, name, blk.get("template") or "",
                                    blk.get("쿨타임"), levels, slots, effs,
                                    tokenize(blk.get("template") or ""), is_preview))
    _link_all(versions, nikke)
    return versions, parsed, nikke


# ── 값 연결 ────────────────────────────────────────────────────────────────

def _candidates(v: Version, slot: int, nikke: dict) -> list[Link]:
    """이 자리의 값 열을 그대로(또는 부호 반전·등록 환산으로) 가진 효과 필드."""
    want = v.slots.get(slot)
    if want is None:
        return []
    out: list[Link] = []
    for e in v.effects:
        tf = TRANSFORMS.get(stat_key(e.get("stat")))
        for f, raw in e.items():
            got = _levels(raw, v.levels)
            if got is None or f == "trigger":
                continue
            if _close(want, got):
                out.append(Link(e, f, "같음", 1))
            elif _close(tuple(-x for x in want), got):
                out.append(Link(e, f, "부호 반전", -1))
            elif tf and f == "values" and _close(tf[1](nikke.get(v.char, {}))(want), got):
                out.append(Link(e, f, tf[0], 1))
    return out


def _arrow(text: str) -> int:
    return 1 if "▲" in text else (-1 if "▼" in text else 0)


def _sign_ok(link: Link, arrow: int, slot_vals: tuple[float, ...]) -> bool:
    """buff 값의 부호가 화살표와 맞는가. 화살표가 없거나 buff가 아니면 판정하지 않는다."""
    if arrow == 0 or link.eff.get("type") != "buff" or link.how not in ("같음", "부호 반전"):
        return True
    if stat_key(link.eff.get("stat")) in SIGN_POSITIVE_REDUCTION:
        return True
    if not all(x >= 0 for x in slot_vals):
        return True
    return link.sign == arrow


def _mapping(link: Link) -> tuple[str, str, int]:
    return (str(link.eff.get("type")), stat_key(link.eff.get("stat")), link.sign)


def _link_all(versions: list[Version], nikke: dict) -> None:
    """블록마다 값을 받은 효과를 붙인다. 값 열이 같은 자리끼리는 화살표 → 선례 → 순서로 가른다."""
    pending: list[tuple[Version, Block, int, list[Link]]] = []
    for v in versions:
        dup = Counter(v.slots.values())
        for cl in v.clauses:
            for b in cl.blocks:
                for i in b.slots:
                    every = _candidates(v, i, nikke)
                    cands = [c for c in every if c.field == "values"]
                    if not cands:
                        # `[{2}초 유지]` → `duration_values` · 무기 변경 계수 → `damage_coeff` 등.
                        # 표시용으로만 붙는다 — O·Q·S는 `values` 연결만 본다
                        b.links.extend(every)
                        continue
                    if dup.get(v.slots.get(i), 0) > 1 and len({id(c.eff) for c in cands}) > 1:
                        pending.append((v, b, i, cands))
                    else:
                        b.links.extend(cands)

    lex = build_block_lexicon(versions)      # 모호하지 않은 연결만으로 만든 선례
    groups: dict[tuple[int, tuple], list[tuple[Version, Block, int, list[Link]]]] = defaultdict(list)
    for item in pending:
        v, b, i, cands = item
        arrow = _arrow(b.text)
        narrowed = [c for c in cands if _sign_ok(c, arrow, v.slots[i])] or cands
        prec = lex.get((block_key(b.text), b.ctx), {})
        votes = Counter(m for ch, ms in prec.items() if ch != v.char for m in ms)
        if votes:
            best = [c for c in narrowed if votes.get(_mapping(c))]
            if best:
                top = max(votes[_mapping(c)] for c in best)
                narrowed = [c for c in best if votes[_mapping(c)] == top]
        if len({id(c.eff) for c in narrowed}) == 1:
            b.links.extend(narrowed)
        else:
            groups[(id(v), v.slots[i])].append((v, b, i, narrowed))

    # 그래도 남으면 원문 순서 ↔ 배열 순서로 짝짓되 추정으로 표시한다
    for items in groups.values():
        effs: list[dict] = []
        for _, _, _, cands in items:
            for c in cands:
                if all(c.eff is not e for e in effs):
                    effs.append(c.eff)
        v = items[0][0]
        order = {id(e): n for n, e in enumerate(v.effects)}
        effs.sort(key=lambda e: order[id(e)])
        for n, (_, b, _, cands) in enumerate(items):
            pick = [c for c in cands if n < len(effs) and c.eff is effs[n]] or cands
            b.links.extend(Link(c.eff, c.field, c.how, c.sign, weak=True) for c in pick)


def build_block_lexicon(versions: list[Version]
                        ) -> dict[tuple[str, str], dict[str, set[tuple[str, str, int]]]]:
    """(블록 키, 맥락) → {캐릭터: 그 문구를 옮긴 (type, stat, 부호)}. 추정 연결은 쓰지 않는다.

    조회하는 쪽이 자기 캐릭터를 빼고 센다(`unanimous`·`_verdict`) — 선례는 언제나 「다른 캐릭터」다.
    """
    lex: dict[tuple[str, str], dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for v in versions:
        for cl in v.clauses:
            for b in cl.blocks:
                for ln in b.links:
                    if not ln.weak and ln.field == "values" and ln.how in ("같음", "부호 반전"):
                        lex[(block_key(b.text), b.ctx)][v.char].add(_mapping(ln))
    return lex


def unanimous(prec: dict[str, set], char: str) -> tuple[tuple[str, str, int], int] | None:
    """다른 캐릭터들이 만장일치로 쓴 매핑과 그 인원. 선례가 모자라거나 갈려 있으면 None."""
    others = {ch: ms for ch, ms in prec.items() if ch != char}
    maps = {m for ms in others.values() for m in ms}
    if len(others) >= MIN_PRECEDENT and len(maps) == 1:
        return next(iter(maps)), len(others)
    return None


# ── 판정 ───────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    check: str        # N ~ S
    key: str          # 예외 키 (출력 그대로)
    detail: str

    @property
    def exempt(self) -> str | None:
        return EXEMPT.get(self.key)


def _mod_ok(mod: tuple[str, float | None], effs: list[dict]) -> bool:
    kind, n = mod
    if kind == "duration":
        # 레벨별 지속(`duration_values`)·중첩 비례 지속(`duration_scaling`)은 숫자 하나로 안 적힌다
        return any((e.get("duration") is not None and abs(float(e["duration"]) - n) < 1e-6)
                   or e.get("duration_values") or e.get("duration_scaling") for e in effs)
    if kind == "infinite":
        buffs = [e for e in effs if e.get("type") == "buff"]
        return not buffs or any(e.get("duration") == -1 for e in buffs)
    if kind == "stack":
        return any(e.get("max_stack") == int(n) for e in effs)
    if kind == "interval":
        return any(e.get("tick_interval") is not None and abs(float(e["tick_interval"]) - n) < 1e-6
                   for e in effs)
    if kind == "irremovable":
        buffs = [e for e in effs if e.get("type") == "buff"]
        return not buffs or any(str(e.get("polarity", "")).endswith("_irremovable") for e in buffs)
    if kind == "bullets":
        return any(e.get("duration_bullets") == int(n) for e in effs)
    if kind == "battle_limit":
        return any(e.get("max_trigger") == int(n) for e in effs)
    if kind == "revive":
        return any(e.get("persist_on_revive") for e in effs)
    if kind == "sequential":
        # `[N회 순차 공격]` → `sequential_damage:N`. 횟수가 게이지에서 오는 `[X 만큼 순차 공격]`은
        # `sequential_damage:X`(메이든 : 아이스 로즈 `MP` · 스노우 화이트 : 헤비암즈 `오토 파이어 레디`)
        seq = [str(e.get("stat", "")) for e in effs if str(e.get("stat", "")).startswith("sequential_damage")]
        return bool(seq) if n is None else any(s.split(":")[-1] == f"{int(n)}" for s in seq)
    if kind == "count_attack":
        # `[X 갯수만큼 공격]` → 직전 damage에 `scaling: stack_count` + `scaling_ref: X` (`PARSING.md` Step 4)
        return any("stack_count" in (e.get("scaling") if isinstance(e.get("scaling"), list)
                                     else [e.get("scaling")]) for e in effs)
    return True


# ── 문구 조각 규칙 (S) ────────────────────────────────────────────────────
# 새 캐릭터의 문구는 통째로는 처음 봐도 **조각**은 익숙하다 — 「자신을 제외한」·「시전자 기준」·
# 「공격 시」/「명중 시」는 어떤 새 조합에 섞여 와도 같은 키 성분을 요구한다. 통문구 선례(Q)가 못 보는
# 새 문구를 조각으로 판정한다.
#
# **로스터 전수에서 위반 0인 규칙만 둔다.** 파싱 실수 유형이 새로 드러나면 일회성 전수 스캔으로 끝내지
# 말고 여기에 한 줄을 더한다 — 그래야 다음 캐릭터에서 같은 실수가 다시 안 난다. 마지막 칸은 그 규칙을
# 낳은 사건이다.
_FIRE_KEYS = {"on_attack", "on_attack_count", "full_charge_fire", "full_charge_fire_count",
              "non_full_charge_fire_count", "last_bullet_fire"}
_HIT_KEYS = {"hit_count", "full_charge_hit", "full_charge_hit_count", "crit_hit_count",
             "pellet_hit_count", "pellet_hit_in_shot", "core_hit_count", "part_hit_count", "last_bullet"}


def _targets(e: dict) -> list[str]:
    t = e.get("target")
    return [str(x) for x in (t if isinstance(t, list) else [t])]


def _scalings(e: dict) -> list[str]:
    s = e.get("scaling")
    return [str(x) for x in (s if isinstance(s, list) else [s])]


# (어디서, 조각, 요구, 위반 설명, 출처). 「대상」·「블록」은 효과 하나를, 「트리거」는 clause 효과들의
# timing 키 prefix 집합을 받는다
FRAGMENTS: tuple[tuple[str, re.Pattern, object, str, str], ...] = (
    ("대상", re.compile(r"자신을 제외한"),
     lambda e: any("excl" in t for t in _targets(e)),
     "대상 문구에 「자신을 제외한」이 있는데 대상 키가 자신 제외(`*excl*`)가 아니다",
     "786b2ce 메이든 : 아이스 로즈 `블레스 유`"),
    ("블록", re.compile(r"시전자 기준.*[▲▼]"),
     lambda e: "caster_based" in str(e.get("stat")),
     "「시전자 기준 … ▲/▼」인데 stat이 시전자 기준(`*caster_based*`)이 아니다",
     "fb37191 솔린 : 프로스트 티켓 `티켓 효과`"),
    ("블록", re.compile(r"시전자의? (?:최종 )?최대 체력 비례.*회복"),
     lambda e: bool({"max_hp", "max_hp_additive"} & set(_scalings(e))),
     "「시전자의 최종 최대 체력 비례 … 회복」인데 `scaling: max_hp`가 없다",
     "b92e018 로스터 전수 통일 (`PARSING.md` §7-10)"),
    ("트리거", re.compile(r"명중 시"),
     lambda keys: not (keys & _FIRE_KEYS),
     "「명중 시」인데 발사 축 키(`on_attack*`·`*_fire*`)다",
     "5abe65e 공격·명중 분리 (`PARSING.md` §4-1 「공격과 명중」)"),
    ("트리거", re.compile(r"^(?!.*명중).*(?:공격|사격) 시"),
     lambda keys: not (keys & _HIT_KEYS),
     "「공격 시」·「사격 시」인데 명중 축 키(`hit_count`·`*_hit*`)다",
     "5abe65e 공격·명중 분리 (`PARSING.md` §4-1 「공격과 명중」)"),
)


def check_fragments(v: Version) -> list[Finding]:
    """S — 문구 조각 규칙. 추정 연결(값 중복을 순서로 짝지은 것)은 보지 않는다."""
    out: list[Finding] = []
    for cl in v.clauses:
        effs = _clause_effects(cl)
        tp = timing_phrase(cl.head)
        for where, rx, ok, what, why in FRAGMENTS:
            if where == "트리거":
                keys = {str(t).split(":", 1)[0] for e in effs
                        for t in (e.get("trigger") or {}).get("timing") or []}
                if tp and rx.search(tp) and effs and not ok(keys):
                    out.append(Finding("S", v.key(f"「{tp}」"),
                                       f"{what} — {', '.join(sorted(keys))} (규칙 출처: {why})"))
                continue
            for b, lt in zip(cl.blocks, block_targets(cl)):
                text = lt if where == "대상" else b.text
                if not text or not rx.search(text):
                    continue
                for ln in b.links:
                    if ln.weak or (where == "블록" and ln.field != "values") or ok(ln.eff):
                        continue
                    shown = ln.eff.get("target") if where == "대상" else ln.eff.get("stat")
                    out.append(Finding("S", v.key(str(ln.eff.get("name")), f"「{rx.pattern}」"),
                                       f"{what} — `{shown}` (규칙 출처: {why})"))
    return out


# 키 숫자가 원문에 없어도 되는 자리 — 중첩·게이지 문턱은 「최대 중첩 상태라면」처럼 한도에서 온다
_CAP_KEYS = ("self_stack_above", "target_stack_above", "gauge_above", "gauge_eq", "gauge_below")


def check_key_numbers(v: Version, char_effects: list[dict]) -> list[Finding]:
    """R — timing·condition·target 키의 숫자 인자가 원문에서 왔는가.

    원문에 없는 숫자는 옮기다 틀렸거나(소다 「10 / 20 / 30 중첩 이상」이 `:9 / :19 / :29`였다) 원문 밖
    지식에서 왔다. 후자면 근거를 `EXEMPT`에 적는다. 원문 숫자로 치는 것: template의 숫자, 레벨 무관
    상수 자리의 값, 쿨타임, 0·1(「없다면」·「명중 시」의 암묵값), 「양 옆」의 2, 중첩·게이지 문턱이면
    이 캐릭터 효과의 `max_stack`·`gauge_max`. **원문에 「N초 마다」가 없고 CDN 쿨타임도 null인
    `every:Ns`는 보지 않는다** — 그 주기는 원문 밖(유저 인게임 확인)에서만 온다(`PARSING.md` §4-1).
    """
    out: list[Finding] = []
    allowed = {float(x) for x in _NUM.findall(_SLOT.sub(" ", v.template))}
    allowed |= {p[0] for p in v.slots.values() if len(set(p)) == 1}
    cooldown = {float(x) for x in _NUM.findall(v.cooldown or "")}
    allowed |= cooldown | {0.0, 1.0}
    if "양 옆" in v.template:
        allowed.add(2.0)
    caps = {float(e[k]) for e in char_effects for k in ("max_stack", "gauge_max")
            if isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool)}
    every_in_text = bool(re.search(r"초\s*마다", v.template))
    seen: set[str] = set()
    for e in v.effects:
        trg = e.get("trigger") or {}
        tgt = e.get("target")
        for k in [*(trg.get("timing") or []), *(trg.get("condition") or []),
                  *(tgt if isinstance(tgt, list) else [tgt])]:
            if not isinstance(k, str) or k in seen:
                continue
            head, *args = k.split(":")
            if head == "every" and not cooldown and not every_in_text:
                continue
            for a in args:
                a = a[:-1] if a.endswith("s") else a
                if not re.fullmatch(r"\d+(?:\.\d+)?", a):
                    continue
                n = float(a)
                if n in allowed or (head in _CAP_KEYS and n in caps):
                    continue
                seen.add(k)
                out.append(Finding("R", v.key(f"`{k}`"),
                                   f"{e.get('name')}의 `{k}` — 숫자 {a}가 원문에 없다 "
                                   f"(원문 숫자: {', '.join(f'{x:g}' for x in sorted(allowed - {0.0, 1.0})) or '-'})"))
    return out


def check_version(v: Version, nikke: dict, lex, char_effects: list[dict] | None = None) -> list[Finding]:
    out: list[Finding] = check_key_numbers(v, char_effects if char_effects is not None else v.effects)
    out += check_fragments(v)

    # N — 자리마다 옮겨졌는가 / 효과의 값 열마다 출처가 있는가
    linked_fields: set[tuple[int, str]] = set()
    for i, want in v.slots.items():
        cands = _candidates(v, i, nikke)
        for c in cands:
            linked_fields.add((id(c.eff), c.field))
        if cands:
            continue
        # 레벨 무관 상수 자리는 스칼라(`fixed_value`·키 꼬리)로 옮겨도 같다
        if len(set(want)) == 1 and any(abs(abs(want[0]) - s) <= TOL for s in _scalars(v.effects)):
            continue
        out.append(Finding("N", v.key(f"{{{i}}}"),
                           f"옮겨지지 않은 자리 — 레벨 {v.levels[0]} {want[0]} … 레벨 {v.levels[-1]} {want[-1]}"))
    if len(v.slots) != len(set(_SLOT.findall(v.template))):
        out.append(Finding("N", v.key("values"), "template의 `{i}` 자리 수와 레벨별 값 수가 다르다"))
    for e in v.effects:
        for f, raw in e.items():
            got = _levels(raw, v.levels)
            if got is None or f == "trigger" or (id(e), f) in linked_fields:
                continue
            out.append(Finding("N", v.key(f"{e.get('name')}.{f}"),
                               f"원문 어느 자리에도 없는 값 열 — 레벨 {v.levels[0]} {got[0]} … "
                               f"레벨 {v.levels[-1]} {got[-1]}"))
    # N — `{i}` 없는 수치 블록: 화살표·「고정」이 붙은 것만 (횟수·단계 표기는 구조로 옮겨져 스칼라가 아니다)
    scal = _scalars(v.effects)
    tf_vals: list[float] = []
    for e in v.effects:
        if (tf := TRANSFORMS.get(stat_key(e.get("stat")))):
            fn = tf[1](nikke.get(v.char, {}))
            tf_vals.append(fn)
    for cl in v.clauses:
        for b in cl.blocks:
            if b.kind != "quantity" or not (_arrow(b.text) or "고정" in b.text):
                continue
            nums = [float(x) for x in _NUM.findall(_NOT_QUANTITY.sub("", b.text))]
            miss = []
            for x in nums:
                cands = {x, x / 100} | {fn((x,))[0] for fn in tf_vals}
                if not any(abs(c - s) <= 1e-6 for c in cands for s in scal):
                    miss.append(x)
            if miss:
                out.append(Finding("N", v.key(f"[{b.text}]"),
                                   f"수치 {', '.join(f'{x:g}' for x in miss)}가 이 판본 어느 효과에도 없다"))

    # P — 화살표. **효과 중심**으로 본다: 값 열이 같은 자리가 여럿이어도(루드밀라 `노블레스 오블리주`의
    # `[방어력 N% ▼]`·`[공격력 N% ▼]`) 그 자리들의 화살표가 한 방향이면 부호가 정해진다. 블록 쪽에서 보면
    # 값 중복을 부호로 가르는 연결 단계가 틀린 부호를 가려 버린다
    arrows: dict[int, set[int]] = defaultdict(set)
    for cl in v.clauses:
        for b in cl.blocks:
            for i in b.slots:
                arrows[i].add(_arrow(b.text))
    for e in v.effects:
        got = _levels(e.get("values"), v.levels)
        if e.get("type") != "buff" or got is None or stat_key(e.get("stat")) in SIGN_POSITIVE_REDUCTION:
            continue
        src = [i for i, p in v.slots.items() if all(x >= 0 for x in p)
               and (_close(p, got) or _close(tuple(-x for x in p), got))]
        dirs = set().union(*(arrows.get(i, {0}) for i in src)) if src else set()
        sign = 1 if all(x >= 0 for x in got) else (-1 if all(x <= 0 for x in got) else 0)
        if len(dirs) == 1 and 0 not in dirs and sign and sign not in dirs:
            out.append(Finding("P", v.key(str(e.get("name"))),
                               f"원문 {'▲' if 1 in dirs else '▼'}인데 값이 {'음수' if sign < 0 else '양수'}"))

    # O · Q
    for cl in v.clauses:
        prev: Block | None = None
        last_value: Block | None = None
        own: set[str] = set()          # 마지막 값 블록 바로 뒤에 붙은 부속 블록 종류
        for b, lead in zip(cl.blocks, block_targets(cl, raw=True)):
            # 「적 전체에게(파츠 포함)」 — 대상 문구의 꼬리도 부속 블록이다. 파츠 대미지 ▲가 실리는 히트는
            # `hits_parts`가 붙은 것뿐이라 빠지면 조용히 본체만 때린다(레이븐 `템페스트`, 2026-09-10 보강)
            if "파츠 포함" in lead and b.kind == "value":
                for ln in b.links:
                    if not ln.weak and ln.eff.get("type") == "damage" and not ln.eff.get("hits_parts"):
                        out.append(Finding("O", v.key(str(ln.eff.get("name")), "(파츠 포함)"),
                                           f"대상 문구에 「(파츠 포함)」이 있는데 `[{b.text}]`의 damage에 "
                                           f"`hits_parts: true`가 없다"))
            if b.kind == "mod":
                owner = None
                if prev is not None and prev.kind == "value":
                    owner = prev
                elif prev is not None and prev.kind == "tag" and last_value is not None \
                        and b.mod[0] not in own \
                        and all(ln.eff.get("type") == "buff" for ln in last_value.links):
                    # 속성형 블록 뒤의 유지·해제 블록은 clause 전체 것이다(`PARSING.md` Step 4 —
                    # 나유타 `[기억 흡수 : 명중률 N%▲] [30중첩] [중첩량 증감 효과 면역] [지속] [해제 불가]`).
                    # 한 **상태**를 쪼갠 경우라 값 블록이 buff일 때만이다 — 대미지 뒤 `[기절] [2초 유지]`는 기절의
                    # 것이고, 값 블록이 같은 종류를 이미 가졌으면(타키나 `[받는 대미지 N%▲] [5초 유지] [기절]
                    # [2초 유지]`) 뒤의 것은 태그의 것이다
                    owner = last_value
                strong = [ln for ln in (owner.links if owner else []) if not ln.weak]
                if strong:
                    effs = list({id(ln.eff): ln.eff for ln in strong}.values())
                    if not _mod_ok(b.mod, effs):
                        names = "·".join(str(e.get("name")) for e in effs)
                        field_ = next(f for n, _, f in MODIFIERS if n == b.mod[0])
                        out.append(Finding("O", v.key(names, f"[{b.text}]"),
                                           f"`[{owner.text}]` 뒤의 `[{b.text}]`가 {field_}에 없다"))
                if owner is not None and owner is prev:
                    own.add(b.mod[0])
                continue
            prev = b
            if b.kind != "value":
                continue
            last_value, own = b, set()
            mult = _MULT.search(b.text)
            for ln in b.links:
                if ln.weak:
                    continue
                if mult and ln.field == "values":
                    # 「X [카운터] 수」 → `scaling: stack_count` + `scaling_ref: [카운터]` (검사 J의 일반형)
                    sc = ln.eff.get("scaling")
                    ref = str(ln.eff.get("scaling_ref") or "")
                    if "stack_count" not in (sc if isinstance(sc, list) else [sc]) \
                            or not ref or not mult.group(1).startswith(ref):
                        out.append(Finding("O", v.key(str(ln.eff.get("name")), "X"),
                                           f"`[{b.text}]`의 「X {mult.group(1)}」가 `scaling: stack_count` + "
                                           f"`scaling_ref`에 없다 (지금 {sc!r} / {ref or '-'})"))
                if ln.field != "values" or ln.how not in ("같음", "부호 반전"):
                    continue
                un = unanimous(lex.get((block_key(b.text), b.ctx), {}), v.char)
                if un and _mapping(ln) != un[0]:
                    t, s, sg = un[0]
                    out.append(Finding("Q", v.key(str(ln.eff.get("name"))),
                                       f"`[{b.text}]` → {ln.eff.get('type')} `{ln.eff.get('stat')}`"
                                       f"{'(음수)' if ln.sign < 0 else ''} · 선례 {un[1]}명은 전부 "
                                       f"{t} `{s}`{'(음수)' if sg < 0 else ''}"))
    return out


CHECK_TITLES = {
    "N": "원문 값 대조 (`{i}` 레벨 값 열 · 수치 블록 ↔ parsed_skills)",
    "O": "부속 블록 대조 ([N초 유지]·[지속]·[N 중첩]·[N초 간격]·[해제 불가]… ↔ 효과 필드)",
    "P": "화살표 부호 (▲ 양수 / ▼ 음수 ↔ buff 값)",
    "Q": "블록 선례 (로스터가 만장일치로 옮긴 문구를 다르게 옮겼는가)",
    "R": "키 숫자 (timing·condition·target 키의 숫자 ↔ 원문 숫자)",
    "S": "문구 조각 규칙 (「자신을 제외한」·「시전자 기준」·「공격/명중 시」… ↔ 키 성분)",
}


def check_roster(checks: str = "NOPQRS") -> bool:
    """로스터 전체 판정. doclint가 부른다. 반환: 예외 밖 위반이 있으면 True."""
    versions, parsed, nikke = load()
    lex = build_block_lexicon(versions)
    by_check: dict[str, list[Finding]] = defaultdict(list)
    for v in versions:
        for f in check_version(v, nikke, lex, parsed[v.char]):
            by_check[f.check].append(f)

    n_slots = sum(len(v.slots) for v in versions)
    fail = False
    used_exempt: set[str] = set()
    for c in checks:
        print(f"\n=== {c}. {CHECK_TITLES[c]} ===")
        found = by_check.get(c, [])
        bad = [f for f in found if not f.exempt]
        used_exempt |= {f.key for f in found if f.exempt}
        for f in bad:
            print(f"  {f.key}  — {f.detail}")
        if bad:
            fail = True
            print(_HINT[c])
        else:
            n_ex = sum(1 for f in found if f.exempt)
            scope = (f"판본 {len(versions)}개 · 자리 {n_slots}개" if c == "N"
                     else f"판본 {len(versions)}개")
            print(f"  (일치 — {scope}" + (f", 예외 {n_ex}건)" if n_ex else ")"))
    # 쓰이지 않는 예외는 검사를 조용히 넓힌다 — 고쳐졌으면 지운다
    stale = sorted(k for k in EXEMPT if k not in used_exempt and k.split(" / ")[0] in
                   {v.char for v in versions})
    if stale and set(checks) == set("NOPQRS"):
        fail = True
        print("\n  쓰이지 않는 예외 (runner/parsecheck.py EXEMPT) — 해소됐으면 지운다:")
        for k in stale:
            print(f"    {k}")
    return fail


_HINT = {
    "N": "    → 값을 원문에서 다시 옮긴다(밸런스 패치로 원문만 바뀐 경우 포함). 환산이 규칙이면 "
         "`TRANSFORMS`에, 의도된 결정이면 `EXEMPT`에 사유와 함께 등록한다",
    "O": "    → 부속 블록은 버리지 않는다(`PARSING.md` Step 4). 앞 효과의 필드로 옮긴다. "
         "의도된 결정이면 `EXEMPT`에 사유와 함께 등록한다",
    "P": "    → ▲는 양수, ▼는 음수다(`PARSING.md` §6). 규약상 ▼를 양수로 적는 stat이면 "
         "`SIGN_POSITIVE_REDUCTION`에 사유와 함께 등록한다",
    "R": "    → 원문의 숫자를 다시 옮긴다(「N 중첩 이상」은 `:N` 그대로 — `PARSING.md` §4-2). 원문 밖 지식에서 "
         "온 숫자면 근거를 `note`에 적고 `EXEMPT`에 사유와 함께 등록한다",
    "S": "    → 조각이 요구하는 키 성분으로 고친다. 이 캐릭터에서만 조각의 뜻이 다르면 근거를 시나리오 "
         "`## 해석 선언`에 적고 `EXEMPT`에 사유와 함께 등록한다. 규칙 자체는 `FRAGMENTS`",
    "Q": "    → 선례대로 고치거나, 다르게 옮긴 근거를 시나리오 `## 해석 선언`에 적고 `EXEMPT`에 "
         "사유와 함께 등록한다. `python -m runner.parsecheck \"<이름>\"`이 선례 캐릭터를 보여 준다",
}


# ── 캐릭터 감사표 ──────────────────────────────────────────────────────────

_SEPS = (" 시 ", "라면 ", "한하여 ", " 때 ", "경우 ", "마다 ", "후 ")
_TIMING = re.compile(r"^(.*?(?: 시| 마다|마다|후))(?:\s|$)")
# 트리거를 그 횟수판으로 바꾸는 블록 (`PARSING.md` §4-1 · 7-3) — 문구 선례의 맥락
_COUNT_BLOCKS = ("사용 횟수 별", "시작 횟수 별", "하위 효과 중복 적용", "공격 횟수 별")
# 대상 문구 뒤에 붙는 꼬리 — 「적 전체에게 (파츠 포함)\n효과 1 :」
_LABEL_TAIL = re.compile(r"\s*(?:효과\s*\d*|기능|추가 효과)\s*:?\s*$")
_PAREN_TAIL = re.compile(r"\s*\([^()]*\)\s*$")


def target_phrase(text: str) -> str:
    """「…에게」로 끝나는 대상 문구. 없으면 ""."""
    h = _PAREN_TAIL.sub("", _LABEL_TAIL.sub("", " ".join(text.split())).rstrip(" :"))
    if not h.endswith("에게"):
        return ""
    cut = 0
    for sep in _SEPS:
        i = h.rfind(sep)
        if i >= 0:
            cut = max(cut, i + len(sep))
    h = h[cut:]
    if ":" in h:                      # 「효과 1 : 적 전체에게」
        h = h.rsplit(":", 1)[1]
    return h.strip()


def timing_phrase(head: str) -> str:
    m = _TIMING.search(" ".join(head.split()))
    return m.group(1).strip() if m else ""


def _clause_effects(cl: Clause) -> list[dict]:
    out: list[dict] = []
    for b in cl.blocks:
        for ln in b.links:
            if not ln.weak and all(ln.eff is not e for e in out):
                out.append(ln.eff)
    return out


def _target_of(e: dict):
    t = e.get("target")
    return key_shape(t) if isinstance(t, str) else tuple(key_shape(x) for x in t or [])


def _timing_of(e: dict) -> tuple[str, ...]:
    return tuple(key_shape(t) for t in (e.get("trigger") or {}).get("timing") or [])


# 대상 문구가 머리에서 블록까지 이어지지 않는 하위 구조 — 「기능 : … 대상에게 … 효과 :」
_SUBLABEL = re.compile(r"기능\s*:|효과\s*\d*\s*:|추가 효과|대미지\s*:")


def block_targets(cl: Clause, *, raw: bool = False) -> list[str]:
    """블록마다 걸리는 대상 문구. 머리의 「…에게」가 기본이고 블록 사이 「효과 2 : 자신에게」가 덮는다.

    블록 앞 본문이 `기능 :`·`효과 1 :` 같은 하위 구조인데 제 대상 문구가 없으면 **모름**("")으로
    둔다 — 설명문 속 대상(「첫발에 한하여 대상에게 지속 대미지를 적용」)은 문장 중간이라 못 뗀다.
    `raw`면 문구를 떼어 내지 않은 앞 본문 전체를 준다 — 「(파츠 포함)」 같은 꼬리를 보는 용도.
    """
    out: list[str] = []
    cur = ""
    for b in cl.blocks:
        if target_phrase(b.lead):
            cur = b.lead if raw else target_phrase(b.lead)
        elif _SUBLABEL.search(b.lead):
            cur = ""
        out.append(cur)
    return out


def phrase_lexicons(versions: list[Version]):
    """(대상 문구 → 대상 키, (트리거 문구, 횟수판 여부) → timing 키) 선례. 조회 때 자기 자신을 뺀다."""
    tgt: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    tim: dict[tuple[str, bool], dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for v in versions:
        for cl in v.clauses:
            counted = any(k in b.text for b in cl.blocks for k in _COUNT_BLOCKS)
            if (tp := timing_phrase(cl.head)):
                for e in _clause_effects(cl):
                    tim[(phrase_key(tp), counted)][v.char].add(_timing_of(e))
            for b, lt in zip(cl.blocks, block_targets(cl)):
                if lt:
                    for ln in b.links:
                        if not ln.weak:
                            tgt[phrase_key(lt)][v.char].add(_target_of(ln.eff))
    return tgt, tim


def _verdict(prec: dict[str, set], mine, char: str, same=None) -> tuple[str, str]:
    """(기호, 설명). 인원은 캐릭터 수로 센다 — 한 캐릭터가 같은 문구를 여러 번 써도 한 표다.

    ✓ 다수 선례와 같음 · ~ 소수 선례와만 같거나 선례가 1명뿐인데 다름 · ⚠ 2명 이상의 다수 선례와 다름 ·
    · 선례 없음. `same(mine, 선례)`로 「같음」을 넓힐 수 있다(트리거: 두 clause를 한 효과로 합쳐
    timing을 둘 가진 항목은 각 문구의 선례를 **포함**하면 같다).
    """
    same = same or (lambda a, b: a == b)
    others = {ch: ms for ch, ms in prec.items() if ch != char}
    if not others:
        return "·", "선례 없음"
    votes = Counter(m for ms in others.values() for m in ms)
    (top, n_top), *_ = votes.most_common(1)
    who = ", ".join(sorted(ch for ch, ms in others.items() if top in ms)[:3])
    agree = sum(1 for ms in others.values() if any(same(mine, m) for m in ms))
    if agree >= n_top:
        rest = sum(1 for m in votes if not same(mine, m))
        return "✓", f"선례 {agree}명 같음" + (f" · 다른 방식 {rest}종" if rest else "")
    if agree or n_top < MIN_PRECEDENT:
        return "~", (f"소수 선례 {agree}명과만 같음 · " if agree else "") + \
            f"{'다수 ' if agree else '선례 '}{n_top}명은 {_fmt_map(top)} ({who})"
    return "⚠", f"선례는 {_fmt_map(top)} ({n_top}명 · {who})"


def _covers(mine: tuple, prec: tuple) -> bool:
    return set(prec) <= set(mine)


def _fmt_map(m) -> str:
    if isinstance(m, tuple) and len(m) == 3 and m[2] in (1, -1):
        return f"{m[0]} `{m[1]}`" + ("(음수)" if m[2] < 0 else "")
    if isinstance(m, tuple):
        return "+".join(map(str, m)) or "-"
    return str(m)


def audit(name: str, loaded=None) -> int:
    versions, parsed, nikke = loaded or load()
    mine = [v for v in versions if v.char == name]
    if not mine:
        print(f"`{name}`의 파싱 항목이 없다 — 정식 명칭인지(`docs/ALIASES.md`), 파싱을 마쳤는지 확인한다")
        return 2
    lex = build_block_lexicon(versions)
    tgt_lex, tim_lex = phrase_lexicons(versions)
    others = len({v.char for v in versions}) - 1
    print(f"[{name}] 원문 ↔ 파싱 대조 · 선례 = 파싱 완료 {others}명 (자기 자신 제외)")
    print("  ✓ 다수 선례와 같음   ~ 소수 선례와만 같음   ⚠ 어느 선례와도 다름   · 선례 없음 (AI 판단 구역)\n")

    findings: list[Finding] = []
    # 판본(기본 · 애장품 N단계)마다 같은 문구가 되풀이되므로 내용으로 묶고 판본은 뒤에 모아 적는다
    news: dict[str, list[str]] = defaultdict(list)
    diffs: dict[str, list[str]] = defaultdict(list)

    def judge(prec: dict, got, slot: str, label: str, same=None) -> str:
        mark, why = _verdict(prec, got, name, same)
        if mark in ("⚠", "~"):
            diffs[f"{mark} {label} → {_fmt_map(got)} — {why}"].append(slot)
        elif mark == "·":
            news[f"{label} → {_fmt_map(got)}"].append(slot)
        return f"{mark} {why}"

    for v in sorted(mine, key=lambda x: (x.source, x.favorite or 0)):
        findings += check_version(v, nikke, lex, parsed[name])
        print(f"{v.label} 「{v.skill_name}」" + (" (프리뷰)" if v.preview else "")
              + (f"  쿨타임 {v.cooldown}" if v.cooldown else ""))
        for cl in v.clauses:
            print(f"  ■ {cl.head or '(머리 문구 없음)'}")
            counted = any(k in b.text for b in cl.blocks for k in _COUNT_BLOCKS)
            ceffs = _clause_effects(cl)
            tp = timing_phrase(cl.head)
            for g in sorted({_timing_of(e) for e in ceffs}):
                if tp:
                    verdict = judge(tim_lex.get((phrase_key(tp), counted), {}), g,
                                    v.label, f"트리거 「{tp}」", _covers)
                    print(f"      트리거 「{tp}」 → {_fmt_map(g)}   {verdict}")
                else:
                    print(f"      트리거 문구 없음 → {_fmt_map(g)}")
            targets = block_targets(cl)
            shown = ""
            prev: Block | None = None
            for b, lt in zip(cl.blocks, targets):
                if lt and lt != shown:
                    shown = lt
                    span = [bb for bb, t in zip(cl.blocks[b.pos:], targets[b.pos:]) if t == lt]
                    for g in sorted({_target_of(ln.eff) for bb in span for ln in bb.links
                                     if not ln.weak}, key=str):
                        verdict = judge(tgt_lex.get(phrase_key(lt), {}), g, v.label, f"대상 「{lt}」")
                        print(f"      대상 「{lt}」 → {_fmt_map(g)}   {verdict}")
                if b.kind == "mod":
                    strong = [ln for ln in (prev.links if prev else []) if not ln.weak]
                    if prev is not None and prev.kind == "value" and strong:
                        effs = list({id(ln.eff): ln.eff for ln in strong}.values())
                        ok = _mod_ok(b.mod, effs)
                        print(f"        [{b.text}]  {'✓' if ok else '✗'}")
                    else:
                        print(f"        [{b.text}]")
                    continue
                prev = b
                if b.kind != "value":
                    print(f"      [{b.text}]")
                    continue
                if not b.links:
                    print(f"      [{b.text}]  ✗ 값을 받은 효과 없음")
                    continue
                for ln in b.links:
                    e = ln.eff
                    how = "" if ln.how == "같음" else f" ({ln.how})"
                    weak = " (값 중복 — 순서로 짝지음)" if ln.weak else ""
                    line = (f"      [{b.text}]  → {e.get('name')} · {e.get('type')} `{e.get('stat')}`"
                            f"{how}{weak}")
                    if ln.weak or ln.field != "values" or ln.how not in ("같음", "부호 반전"):
                        print(line)
                        continue
                    verdict = judge(lex.get((block_key(b.text), b.ctx), {}), _mapping(ln),
                                    v.label, f"[{b.text}] ({e.get('name')})")
                    print(f"{line}   {verdict}")
        # 원문 블록과 연결되지 않은 효과 — 고정 블록(`[도발]`)·본문 수치·파생 항목. 틀린 게 아니라 눈으로 볼 자리
        linked = {id(ln.eff) for cl in v.clauses for b in cl.blocks for ln in b.links}
        rest = [e for e in v.effects if id(e) not in linked]
        if rest:
            print("  값 블록과 안 이어진 효과 (고정 블록·본문 수치·파생 항목 — 원문과 눈으로 대조):")
            for e in rest:
                trg = e.get("trigger") or {}
                print(f"      {e.get('name')} · {e.get('type')} `{e.get('stat')}` · "
                      f"timing={'+'.join(trg.get('timing') or []) or '-'}"
                      + (f" · cond={'+'.join(trg['condition'])}" if trg.get("condition") else "")
                      + (f" · fixed={e['fixed_value']}" if "fixed_value" in e else ""))
        print()

    print("── 요약 ──")
    for c in "NOPQRS":
        fs = [f for f in findings if f.check == c]
        bad = [f for f in fs if not f.exempt]
        tail = f" (예외 {len(fs) - len(bad)}건)" if len(fs) - len(bad) else ""
        print(f"  {c}. {CHECK_TITLES[c].split(' (')[0]}: " + ("✓" if not bad else f"✗ {len(bad)}건") + tail)
        for f in bad:
            print(f"      {f.key} — {f.detail}")
    print(f"\n  선례와 다름 {len(diffs)}건 — 선례대로 고치거나, 다르게 옮긴 근거를 시나리오 `## 해석 선언`에 적는다")
    for d, slots in diffs.items():
        print(f"      {d}  [{' · '.join(dict.fromkeys(slots))}]")
    print(f"  선례 없음 {len(news)}건 — AI가 판단한 자리다. 단계 3 보고에 그대로 올린다")
    for n, slots in news.items():
        print(f"      {n}  [{' · '.join(dict.fromkeys(slots))}]")
    return 1 if any(not f.exempt for f in findings) else 0


# ── 미발동 (--sim) ─────────────────────────────────────────────────────────

def scenario_squads(name: str) -> list[list[str]]:
    """시나리오 `## 검증 스쿼드` 절의 파이썬 리스트 표기(`["a", "b", …]`)를 전부 뽑는다."""
    path = SCENARIOS / f"{name.replace(':', '_')}.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## 검증 스쿼드\s*$(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        return []
    out: list[list[str]] = []
    for lit in re.findall(r"`(\[[^`\]]*\])`", m.group(1)):
        try:
            squad = ast.literal_eval(lit)
        except (ValueError, SyntaxError):
            continue
        if isinstance(squad, list) and squad and all(isinstance(x, str) for x in squad) \
                and name in squad and squad not in out:
            out.append(squad)
    return out


def fired(name: str, squads: list[list[str]], duration: float | None = None
          ) -> tuple[list[dict], Counter, list[str], set[str]]:
    """스쿼드마다 한 번씩 돌려 이 캐릭터 효과의 이름별 발동 횟수를 합친다.

    반환: (효과 목록, 이름별 횟수, 실행 요약, 이 캐릭터가 실제로 쓴 버스트 단계).
    """
    from calculator.buff_manager import char_effects
    from calculator.timeline import simulate
    from runner import spec

    from calculator.buff_manager import BuffManager

    counts: Counter = Counter()
    effects: list[dict] = []
    runs: list[str] = []
    stages: set[str] = set()
    # 무기 변경 진입은 버프 로그에 이름이 안 남고(`state["weapon_change"]`에만 적힌다) 모드 사격도
    # `기본 공격`으로 집계된다. 진입 경로인 `_activate()`를 이 실행 동안만 엿봐 이름을 센다 —
    # 호출은 진입 시도라 발동권·토글 해제로 막힌 것도 1회로 잡힌다. 0회 판정에는 충분하다.
    real = BuffManager._activate

    def spy(self, eff, caster, t, *a, **kw):
        if caster == name and eff.get("type") == "weapon_change":
            counts[eff.get("name")] += 1
        return real(self, eff, caster, t, *a, **kw)

    for members in squads:
        squad = spec.build_squad(members, {})
        config = {"first_burst_time": 3.0}
        if duration:
            config["duration"] = duration
        config = spec.build_config(squad, config)
        BuffManager._activate = spy
        try:
            result = simulate(squad, config=config, enemy=None, verbose=True, seed=1)
        finally:
            BuffManager._activate = real
        me = next(c for c in squad if c["name"] == name)
        effects = char_effects(name, me.get("favorite_stage"))
        log = result.log
        counts.update(e.name for e in log.buff_events if e.kind == "activate" and e.caster == name)
        counts.update(e.name for e in log.instant_events if e.caster == name)
        counts.update(h.skill_name for h in result.hits if h.caster == name)
        # 런타임 조건 passive(`self_state:` 등)는 켜질 때 activate 로그 없이 붙는다 — 풀버스트 진입
        # 스냅샷에 있으면 발동으로 친다(엠마 : 택티컬 업 `포메이션 LT 3~7`)
        shown = {b.name for snap in log.buff_snapshots for bs in snap.buffs_by_char.values()
                 for b in bs if b.caster == name}
        counts.update({n: 1 for n in shown if not counts.get(n)})
        stages |= {e.event.split(":")[1].split()[0] for e in log.burst_log
                   if e.caster == name and e.event.startswith("stage:")}
        runs.append(f"{', '.join(members)}  (총딜 {result.squad_total:,} · "
                    f"{name} {result.char_total.get(name, 0):,})")
    return effects, counts, runs, stages


# 기본 적(`timeline.DEFAULT_ENEMY` — 파츠·코어·코드·보스 패턴 없음)에서는 열리지 않는 키 → 여는 방법.
# 근거는 `IMPL-STATUS.md`의 각 행(「기본은 무발동」·「패턴이 없으면 무발동」). 여기 걸린 0회는 정상이고,
# **여기 없는 0회가 조용히 죽은 효과 후보다.** 키 prefix로 맞춘다.
DORMANT_ON_DEFAULT: dict[str, str] = {
    "event:part_destroy": "파츠 파괴 (`--has-parts --part-break-interval N`)",
    "part_hit_count": "파츠 (`--has-parts`)",
    "core_hit_count": "코어 (`--core-px N`)",
    "core_hit": "코어 (`--core-px N`)",
    "target_code": "적 코드 (`--enemy-code`)",
    "optimal_range": "적정 사거리 (적 `optimal_range_weapons` · `--distance`)",
    "received_hit_count": "보스 공격 (`--boss`)",
    "event:lethal_hit": "보스 공격 (`--boss`)",
    "event:cover_hit": "보스 공격 (`--boss`)",
    "event:shield_consumed": "보스 공격 (`--boss`)",
    "event:ally_hp_below": "아군 피해 (`--boss`)",
    "event:adjacent_hp_below": "아군 피해 (`--boss`)",
    "hp_below": "피해 (`--boss`)",
    "event:ally_down": "전투불능 (`--boss`)",
    "event:self_down": "전투불능 (`--boss`)",
    "allies_down_top_atk_excl": "전투불능 아군 (`--boss`)",
    "allies_down_class_random": "전투불능 아군 (`--boss`)",
    "not_self_cover_alive": "엄폐물 파괴 (`--boss`)",
    "allies_broken_cover_random": "엄폐물 파괴 (`--boss`)",
    "enemy_death": "적 처치 — 쫄몹 패턴 (`--boss`)",
    "target_is_add": "쫄몹 (`--boss`)",
    "enemy_count_above": "적 2기 이상 — 쫄몹 패턴 (`--boss`)",
    "event:projectile_destroy": "파괴 가능 발사체 (`--boss`)",
    "event:target_spawn": "보스 패턴 `emit` (`--boss`)",
    "multi_hit": "동시 명중 N기 — 쫄몹 패턴 (`--boss`)",
    "hp_below_count": "피해 (`--boss`)",
    "self_hp_below": "체력 감소 — 보스 공격(`--boss`)이나 자기 체력 소모",
    "self_undying": "불굴 — 치명타 피격 (`--boss`)",
    # 오토(조작 없음)에서는 차지 무기가 늘 풀 차지로 쏘고 엄폐도 재장전 때만 한다
    "non_full_charge_fire_count": "톡톡이 조작 (`--tap`)",
    "charge_hold": "풀 차지 유지 조작 (`--hold-ctrl`)",
    "charge_hold_count": "풀 차지 유지 조작 (`--hold-ctrl`)",
    "event:cover": "엄폐 조작 (`--cover-ctrl`)",
}


def dormant_reason(eff: dict) -> str | None:
    trg = eff.get("trigger") or {}
    tgt = eff.get("target")
    keys = [*(trg.get("timing") or []), *(trg.get("condition") or []),
            *(tgt if isinstance(tgt, list) else [tgt])]
    for k in map(str, keys):
        for p, why in DORMANT_ON_DEFAULT.items():
            if k == p or k.startswith(p + ":"):
                return why
    return None


def _unimplemented_keys() -> dict[tuple[str, str], str]:
    """IMPL-STATUS 마스터에서 **그 카테고리의 행이 전부** ❌·🚫인 (카테고리, 키 prefix) → 기호.

    상태는 카테고리마다 따로다 — `core_hit_count`는 timing ✅ · condition ❌다. 한 카테고리 안에서
    갈린 키(`same_target`)는 구현된 형태가 있으므로 표시하지 않는다.
    """
    from runner import doclint
    marks: dict[tuple[str, str], set[str]] = defaultdict(set)
    for cat, key, mark in doclint._master_rows():
        marks[(cat, key)].add(mark)
    return {k: ("🚫" if "🚫" in ms else "❌") for k, ms in marks.items() if ms <= {"❌", "🚫"}}


def _effect_keys(e: dict) -> list[tuple[str, str]]:
    trg = e.get("trigger") or {}
    tgt = e.get("target")
    out = [("stat", str(e.get("stat")))] if e.get("stat") else []
    out += [("timing", str(t)) for t in trg.get("timing") or []]
    out += [("condition", str(c)) for c in trg.get("condition") or []]
    out += [("target", str(t)) for t in (tgt if isinstance(tgt, list) else [tgt]) if t]
    return out


# 시나리오가 「이 효과는 0회가 맞다」고 적는 표현 — 체크리스트의 네거티브 항목·타임라인의 미발동 칸
_ZERO_NOTE = re.compile(r"0회|발동 금지|미발동|무발동|발동하지 않")


def scenario_zero_lines(name: str) -> list[str]:
    path = SCENARIOS / f"{name.replace(':', '_')}.md"
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if _ZERO_NOTE.search(ln)]


def _documented_zero(eff_name: str, lines: list[str]) -> bool:
    """시나리오가 이 효과의 0회를 적어 두었는가. 「사랑의 탄환 1~3」처럼 번호를 묶어 쓰므로 번호 뗀 이름도 본다."""
    base = re.sub(r"\s*\d+$", "", eff_name)
    return any(eff_name in ln or (base and base in ln) for ln in lines)


def report_fired(name: str, squads: list[list[str]], duration: float | None) -> int:
    if not squads:
        print(f"[{name}] 돌릴 스쿼드가 없다 — 시나리오 `## 검증 스쿼드`에 `[\"...\"]` 표기가 없으면 "
              f"`--squad`로 준다")
        return 2
    effects, counts, runs, stages = fired(name, squads, duration)
    dead_keys = _unimplemented_keys()
    silent = {e.get("name") for e in effects if not counts.get(e.get("name"))}
    # 아군이 걸어 주는 상태(교차 담체 — doclint K)의 주인. 스쿼드에 없으면 그 상태를 읽는 효과는 0회가 정상이다
    members = {m for s in squads for m in s}
    carriers: dict[str, set[str]] = defaultdict(set)
    for owner, effs in json.loads(SKILLS.read_text(encoding="utf-8")).items():
        for x in effs:
            if x.get("type") in ("buff", "debuff", "weapon_change") and x.get("name"):
                carriers[x["name"]].add(owner)

    def run_reason(e: dict) -> str | None:
        """이 실행의 스쿼드 구성 탓인 0회 — 쓰지 않은 버스트 · 0회인 선행 상태 · 스쿼드에 없는 담체."""
        trg = e.get("trigger") or {}
        for t in map(str, trg.get("timing") or []):
            if t.startswith("squad_burst_cast:") and t.split(":")[1] not in stages:
                return (f"이 스쿼드들에서 {t.split(':')[1]}단계 버스트를 안 썼다(쓴 단계: "
                        f"{', '.join(sorted(stages)) or '없음'})")
            if t.split(":")[0] in ("burst_cast", "burst_cast_count") and not stages:
                return "이 스쿼드들에서 버스트를 한 번도 안 썼다"
            ev = t.split(":", 2)[-1] if t.startswith("event:state_end:") else t.split(":", 1)[-1]
            if t.startswith("event:") and ev in silent:
                return f"선행 효과 `{ev}`가 0회"
        refs = [str(c).split(":", 1)[1] for c in trg.get("condition") or [] if str(c).startswith("self_state:")]
        tgt = e.get("target")
        refs += [str(t).split(":", 1)[1] for t in (tgt if isinstance(tgt, list) else [tgt])
                 if str(t).startswith(("allies_with_buff:", "enemies_with_buff:"))]
        for ref in refs:
            if ref in silent:
                return f"선행 상태 `{ref}`가 0회"
            owners = carriers.get(ref, set()) - {name}
            if owners and not (owners & members) and name not in carriers.get(ref, set()):
                return f"상태 `{ref}`를 거는 {', '.join(sorted(owners))}이(가) 스쿼드에 없다"
        return None
    print(f"[{name}] 효과별 발동 횟수 — 기본 적(파츠·코어·코드·보스 패턴 없음) · seed 1")
    for r in runs:
        print(f"  스쿼드: {r}")
    print()
    notes = scenario_zero_lines(name)
    explained: list[str] = []
    documented: list[str] = []
    unexplained: list[str] = []
    for e in effects:
        n = counts.get(e.get("name"), 0)
        trg = e.get("trigger") or {}
        dead = [(cat, k) for cat, k in _effect_keys(e) if (cat, k.split(":", 1)[0]) in dead_keys]
        marks = sorted({f"{dead_keys[(cat, k.split(':', 1)[0])]} `{k}`" for cat, k in dead})
        tag = f"  ← 미구현 키 {', '.join(marks)}" if marks else ""
        what = f"{e.get('name')} · {e.get('type')}" + (f" `{e['stat']}`" if e.get("stat") else "")
        if n:
            print(f"    {n:4d}회  {what}{tag}")
            continue
        why = dormant_reason(e)
        why = f"기본 적에서 안 열림: {why}" if why else run_reason(e)
        if not why and any(cat != "stat" for cat, _ in dead):
            why = "트리거·조건·대상에 미구현 키가 있다"      # 미구현 stat만으로는 발동이 안 막힌다
        bucket = explained
        if not why and _documented_zero(str(e.get("name")), notes):
            why, bucket = "시나리오가 0회를 적어 두었다", documented
        line = (f"  ✗ 0회  {what} · timing={'+'.join(trg.get('timing') or []) or '-'}"
                + (f" · cond={'+'.join(trg['condition'])}" if trg.get("condition") else "")
                + (f"  — {why}" if why else "") + tag)
        print(line)
        (bucket if why else unexplained).append(what)
    print(f"\n  0회 {len(explained) + len(documented) + len(unexplained)}건 — 환경·구성·미구현 탓 "
          f"{len(explained)}건 · 시나리오에 적힌 0회 {len(documented)}건 · "
          f"**설명 안 되는 0회 {len(unexplained)}건**")
    if unexplained:
        print("    → 스쿼드 구성·버스트 운용으로 안 열리는 것인지, 조용히 죽은 효과인지 하나씩 가른다. 정상이면 "
              "시나리오 체크리스트에 「발동 금지」로 적고, 아니면 담체(`PARSING.md` §상태의 담체)·조건·"
              "엔진 분기(`_timing_match`·`_condition_ok`)를 본다")
        for u in unexplained:
            print(f"      {u}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m runner.parsecheck",
        description="파싱 대조 — 원문의 숫자·부속 블록·화살표가 파싱에 옮겨졌는가")
    ap.add_argument("name", nargs="?", help="캐릭터 정식 명칭. 없으면 로스터 전체 판정")
    ap.add_argument("--sim", action="store_true",
                    help="시나리오 검증 스쿼드를 돌려 미발동 효과를 뽑는다")
    ap.add_argument("--squad", action="append", default=[],
                    help="--sim에 쓸 스쿼드 (콤마 구분 정식 명칭). 여러 번 지정 가능. 주면 시나리오 대신 쓴다")
    ap.add_argument("--duration", type=float, help="--sim 시뮬 시간(초). 기본 180")
    args = ap.parse_args()

    if not args.name:
        raise SystemExit(1 if check_roster() else 0)

    code = audit(args.name)
    if args.sim:
        print()
        squads = ([[n.strip() for n in s.split(",") if n.strip()] for s in args.squad]
                  or scenario_squads(args.name))
        code = max(code, report_fired(args.name, squads, args.duration))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
