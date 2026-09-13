"""
보스 패턴 — 시간에 따라 진행하는 보스 스크립트 (1단계)

보스를 「방어력·코어 크기·파츠 여부」 세 스칼라가 아니라 **서로를 잇는 패턴들**로 적는다.
종전 스칼라(`DEFAULT_ENEMY`의 `def`·`core_px`·`has_parts`·`optimal_range_weapons`)는 전투
내내 고정된 기본 상태로 남고, 열린 패턴이 그 위를 시간에 따라 덮어쓴다.
**`enemy["patterns"]`가 비면 스케줄러를 아예 만들지 않는다** — 이 기능 이전과 계산이 한 자리도
달라지면 안 되고, 회귀 baseline이 전부 그 기준이다.

사용 (timeline.simulate가 부르는 자리):
  pats = validate(enemy["patterns"], weapon_types=...)   즉시 실패시키는 검사
  boss = BossScript(pats, enemy, superior)
  프레임마다   events = boss.begin_frame(t, enemy)       맨 앞 — 전이 → 적 상태 기록
               boss.admit(ev, t)                         히트마다 — 게이트 통과면 흡수 후 True
  루프 종료 뒤 boss.finish(duration)                     열린 패턴을 `end`로 닫는다

의존: damage(코드 상성 목록) · sim_result(평타 판정·로그 자료구조)뿐이라 순환이 없다.

─────────────────────────────────────────────────────────────────────────────
포맷 — 패턴 하나
─────────────────────────────────────────────────────────────────────────────
  {
    "id": "저지1",          고유 이름. 없으면 "<kind><그 kind 안의 순번>" (interrupt1 …)
    "kind": "interrupt",    아래 종류표
    "after": ["출현"],      시작 조건. 기본 ["start"] ("start" = 전투 시작, 예약어)
    "delay": 0.0,           after 충족 후 추가 지연(초)
    "until": {...},         종료 조건. 없으면 전투 끝까지
    "repeat": 1,            열릴 수 있는 횟수. 0이면 무제한
    "emit": [],             시작할 때 스쿼드 전원에게 쏠 이벤트 (BOSS_EVENTS)
    "emit_end": [],         끝날 때 (전투 종료로 닫힐 때는 쏘지 않는다)
    "note": "",             사람용 메모
    ... kind별 칸
  }

구간은 `[시작, 끝)` 반개구간이다 — 엔진의 다른 구간 판정과 같은 규약.

**after** — 항목은 `"패턴id"` 또는 `{"node": "패턴id", "outcome": "expired"}`.
  여럿이면 **OR**다(하나라도 새로 끝나면 열린다). AND는 두지 않는다 — 필요해지면 `after_all`을
  따로 만든다. `outcome`을 적으면 그 사유로 끝났을 때만 열린다(저지 실패 분기).
  판정은 「그 패턴이 **또** 끝났는가」다 — 열 때 항목마다 그때까지의 종료 횟수를 소비 기록에
  적고, 그보다 늘었을 때만 다시 연다. 이 한 줄이 `after: ["start", "마지막"]` + `repeat: 0`을
  사이클로 만든다. 열려 있는 동안 쌓인 종료도 소비 전이라, 끝나는 프레임에 곧바로 다시 열린다.

**until** — `{"time": 15.0, "targets_cleared": true, "after": [{"node": "저지1", "delay": 2.0}]}`
  먼저 오는 쪽(OR). 같은 프레임에 함께 성립하면 `targets_cleared` > `after` > `time` —
  제한시간이 끝나는 바로 그 프레임에 마지막 저지원을 깼다면 인게임에서는 성공이다.
  `until.after`는 **열린 뒤의** 종료만 센다.

**outcome**: cleared(표적 전멸) · expired(제한시간) · followed(다른 패턴을 따라) · end(전투 끝)

| kind | 칸 | 하는 일 |
|---|---|---|
| idle / groggy | — | 아무것도 안 함 (groggy는 이름만 다르다 — 보스 공격 모델이 없어 구분할 게 없다) |
| buff | enemy: {def_mult, def_add} | def ← def × def_mult + def_add (열린 순서대로 겹친다) |
| core | core_px (>0) | 코어를 연다 |
| parts | targets | 살아 있는 표적이 있으면 has_parts=True |
| interrupt | targets | 저지. has_parts는 안 건드린다 |
| shield | code | 그 코드에 우월한 캐스터의 딜만 들어간다 |
| vanish | — | 평타 무효(평타 몫의 버스트 게이지 포함). 스킬 딜·스킬 게이지는 그대로 |
| move | weapons | optimal_range_weapons 교체 (좌표가 없어 적정거리 무기군으로 근사) |
| attack / summon / debuff | spec | **예약. 구간만 차지하고 효과 없음** → SimResult.boss_unmodeled |

예약 셋이 구간을 정상적으로 차지하는 이유: 저지 실패 뒤 공격 패턴이 다음 패턴을 미루는 게 실제
거동이라, 효과가 없다고 구간까지 없애면 뒤가 통째로 당겨진다. `spec`은 엔진이 읽지 않는
자리이고 **예약 종류에만** 허용한다.

**표적** (parts·interrupt의 targets 항목)
  {"name": "저지원A", "hp": 2e8, "share": 1.0, "score": 1000000, "core_px": 0,
   "emit_on_destroy": ["event:part_destroy"],
   "x": 0, "y": 0, "w": 0, "h": 0, "rotation": 0, "shape": "circle", "reachable_by": []}
  hp 0 = 안 깨지는 표적. share = 스쿼드 딜 중 이 표적이 받는 비율(합이 1을 넘어도 된다 —
  관통·광역). core_px > 0이면 살아 있는 동안 코어가 열린다. 좌표 칸(x … reachable_by)은
  **포맷에만 있고 엔진은 읽지 않는다** — 좌표 모델이 들어오는 날 `share`를 좌표에서 유도하는
  것으로 갈아끼운다(교체 지점은 `BossScript.admit` 한 곳).
  표적에 들어간 딜도 총딜에 그대로 남는다. 체력 풀은 「언제 깨지는가」만 세는 카운터다.

적 상태 합성: 기본값에서 출발해 열린 패턴을 시작 시각 순(같으면 선언 순)으로 덮어쓴다.
`core_px`만 예외로 **살아 있는 것 중 가장 큰 값**(기본값 포함) — 코어가 둘이면 큰 쪽을 겨냥한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .damage import DEFAULT_ENEMY_DEF, _CODE_ADVANTAGE
from .sim_result import BossLogEntry, HitEvent, _is_normal

# 시각 비교 여유. 루프가 `t += DT`로 시각을 쌓아 180초까지 가면 5e-13쯤 어긋나서, 그대로 `>=`로
# 재면 「20초 뒤」가 한 프레임 늦게(20.017초) 끝난다. 프레임(1.67e-2초)보다 한참 작아 한 프레임
# 일찍 터질 일은 없다.
_EPS = 1e-9

START = "start"

OUTCOMES = ("cleared", "expired", "followed", "end")

# 보스가 스쿼드에 쏠 수 있는 이벤트 — **닫힌 집합이다.** 파싱은 돼 있는데 발생처가 없어 죽어 있던
# 어휘 중 보스 공격 모델 없이 낼 수 있는 것만 연다. 이름을 잘못 적으면 영영 무발동인데 딜은
# 그럴듯하게 나와 발견이 늦다. 피격 계열(received_hit·event:ally_down …)은 보스→니케 피해 모델이
# 생기는 날 연다.
BOSS_EVENTS = ("event:part_destroy", "event:target_spawn", "event:projectile_destroy",
               "enemy_death")

# 적 상태 중 패턴이 바꿀 수 있는 칸. 여기 없는 키는 패턴으로 못 바꾼다.
OVERLAY_FIELDS = ("def", "core_px", "has_parts", "optimal_range_weapons")

_COMMON_FIELDS = frozenset({"id", "kind", "after", "delay", "until", "repeat",
                            "emit", "emit_end", "note"})
_KIND_FIELDS: dict[str, frozenset[str]] = {
    "idle":      frozenset(),
    "groggy":    frozenset(),
    "buff":      frozenset({"enemy"}),
    "core":      frozenset({"core_px"}),
    "parts":     frozenset({"targets"}),
    "interrupt": frozenset({"targets"}),
    "shield":    frozenset({"code"}),
    "vanish":    frozenset(),
    "move":      frozenset({"weapons"}),
    "attack":    frozenset({"spec"}),
    "summon":    frozenset({"spec"}),
    "debuff":    frozenset({"spec"}),
}
_REQUIRED: dict[str, tuple[str, ...]] = {
    "buff": ("enemy",), "core": ("core_px",), "parts": ("targets",),
    "interrupt": ("targets",), "shield": ("code",), "move": ("weapons",),
}
RESERVED_KINDS = frozenset({"attack", "summon", "debuff"})
_TARGET_KINDS = frozenset({"parts", "interrupt"})
_UNTIL_FIELDS = frozenset({"time", "targets_cleared", "after"})
_BUFF_FIELDS = frozenset({"def_mult", "def_add"})
_TARGET_FIELDS = frozenset({"name", "hp", "share", "score", "core_px", "emit_on_destroy",
                            "x", "y", "w", "h", "rotation", "shape", "reachable_by"})
# 뒤 패턴의 `after` 필터가 요구하는 앞 패턴의 종료 조건. 없으면 그 분기는 영영 안 열린다.
_OUTCOME_NEEDS = {"cleared": "until.targets_cleared", "expired": "until.time",
                  "followed": "until.after"}


# ── 정규화된 스크립트 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class TargetSpec:
    name: str
    hp: float
    share: float = 1.0
    score: int = 0
    core_px: int = 0
    emits: tuple[str, ...] = ()

    @property
    def breakable(self) -> bool:
        return self.hp > 0


@dataclass(frozen=True)
class Pattern:
    idx: int                                        # 선언 순서 — 같은 시각에 열린 것끼리의 합성 순서
    id: str
    kind: str
    after: tuple[tuple[str, str | None], ...]       # (노드, outcome 필터)
    delay: float = 0.0
    until_time: float | None = None
    until_cleared: bool = False
    until_after: tuple[tuple[str, float], ...] = ()  # (노드, 지연)
    repeat: int = 1
    emit: tuple[str, ...] = ()
    emit_end: tuple[str, ...] = ()
    note: str = ""
    # kind별
    def_mult: float = 1
    def_add: float = 0
    core_px: int = 0
    code: str = ""
    weapons: tuple[str, ...] = ()
    targets: tuple[TargetSpec, ...] = ()


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _events(v, where: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(e, str) for e in v):
        raise ValueError(f"{where}는 이벤트 이름 list여야 한다: {v!r}")
    bad = [e for e in v if e not in BOSS_EVENTS]
    if bad:
        raise ValueError(f"{where}: 보스가 낼 수 없는 이벤트 {bad} — 쓸 수 있는 것: "
                         f"{' · '.join(BOSS_EVENTS)}")
    return tuple(v)


def _unknown(raw: dict, allowed: frozenset[str], where: str) -> None:
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ValueError(f"{where}: 모르는 칸 {extra} — 쓸 수 있는 칸: {sorted(allowed)}")


def _target(raw, where: str) -> TargetSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}는 dict여야 한다: {raw!r}")
    _unknown(raw, _TARGET_FIELDS, where)
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{where}: name이 필요하다")
    where = f"{where} {name!r}"
    if "hp" not in raw:
        raise ValueError(f"{where}: hp가 필요하다 (안 깨지는 표적이면 0)")
    hp, share, score = raw["hp"], raw.get("share", 1.0), raw.get("score", 0)
    core_px = raw.get("core_px", 0)
    if not _is_num(hp) or hp < 0:
        raise ValueError(f"{where}: hp는 0 이상의 수여야 한다: {hp!r}")
    if not _is_num(share) or share < 0:
        raise ValueError(f"{where}: share는 0 이상의 수여야 한다: {share!r}")
    if not _is_int(score) or score < 0:
        raise ValueError(f"{where}: score는 0 이상의 정수여야 한다: {score!r}")
    if not _is_int(core_px) or core_px < 0:
        raise ValueError(f"{where}: core_px는 0 이상의 정수여야 한다: {core_px!r}")
    for k in ("x", "y", "w", "h", "rotation"):
        if k in raw and not _is_num(raw[k]):
            raise ValueError(f"{where}: {k}는 수여야 한다: {raw[k]!r}")
    if "shape" in raw and not isinstance(raw["shape"], str):
        raise ValueError(f"{where}: shape는 문자열이어야 한다: {raw['shape']!r}")
    rb = raw.get("reachable_by", [])
    if not isinstance(rb, list) or not all(isinstance(x, str) for x in rb):
        raise ValueError(f"{where}: reachable_by는 문자열 list여야 한다: {rb!r}")
    return TargetSpec(name=name, hp=hp, share=share, score=score, core_px=core_px,
                      emits=_events(raw.get("emit_on_destroy"), f"{where}.emit_on_destroy"))


def validate(patterns, *, weapon_types: frozenset[str] | None = None) -> list[Pattern]:
    """스크립트를 검사해 정규화한다. **잘못 적힌 것은 전부 즉시 실패시킨다.**

    칸 이름을 잘못 적어 영영 무발동이 되는 쪽이 시뮬이 안 도는 것보다 훨씬 늦게 발견된다.
    그래서 조용히 무시될 수 있는 입력 — 모르는 칸·없는 참조·영영 안 열리는 분기 — 을 남기지
    않는다. `weapon_types`를 주면 `move.weapons`를 그 집합으로 검사한다(정본은 로스터 데이터라
    이 모듈이 목록을 따로 들지 않는다).
    """
    if not isinstance(patterns, list):
        raise ValueError(f"enemy.patterns는 list여야 한다: {type(patterns).__name__}")
    out: list[Pattern] = []
    seen_kind: dict[str, int] = {}
    for i, raw in enumerate(patterns):
        where = f"enemy.patterns[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}는 dict여야 한다: {raw!r}")
        kind = raw.get("kind")
        if kind not in _KIND_FIELDS:
            raise ValueError(f"{where}: 모르는 kind {kind!r} — {' · '.join(_KIND_FIELDS)}")
        seen_kind[kind] = seen_kind.get(kind, 0) + 1
        pid = raw.get("id", f"{kind}{seen_kind[kind]}")
        if not isinstance(pid, str) or not pid:
            raise ValueError(f"{where}: id는 비어 있지 않은 문자열이어야 한다: {pid!r}")
        if pid == START:
            raise ValueError(f"{where}: id {START!r}는 전투 시작을 가리키는 예약어다")
        where = f"패턴 {pid!r}"

        allowed = _COMMON_FIELDS | _KIND_FIELDS[kind]
        extra = sorted(set(raw) - allowed)
        if extra:
            elsewhere = [k for k in extra if any(k in f for f in _KIND_FIELDS.values())]
            if elsewhere:
                raise ValueError(f"{where}: kind {kind!r}에 없는 칸 {elsewhere} "
                                 f"(쓸 수 있는 kind별 칸: {sorted(_KIND_FIELDS[kind]) or '없음'})")
            raise ValueError(f"{where}: 모르는 칸 {extra}")
        for req in _REQUIRED.get(kind, ()):
            if req not in raw:
                raise ValueError(f"{where}: kind {kind!r}에는 {req!r}가 필요하다")

        # ── after ──
        after_raw = raw.get("after", [START])
        if not isinstance(after_raw, list) or not after_raw:
            raise ValueError(f"{where}: after는 비어 있지 않은 list여야 한다 "
                             f"(빈 목록은 영영 안 열린다): {after_raw!r}")
        after: list[tuple[str, str | None]] = []
        for a in after_raw:
            if isinstance(a, str):
                node, outcome = a, None
            elif isinstance(a, dict):
                _unknown(a, frozenset({"node", "outcome"}), f"{where}.after 항목")
                node, outcome = a.get("node"), a.get("outcome")
                if outcome is not None and outcome not in _OUTCOME_NEEDS:
                    raise ValueError(
                        f"{where}.after: outcome {outcome!r}로는 열 수 없다 — "
                        f"{' · '.join(_OUTCOME_NEEDS)} 중 하나 ('end'는 전투가 끝난 뒤라 영영 안 열린다)")
            else:
                raise ValueError(f"{where}.after 항목은 문자열이나 dict여야 한다: {a!r}")
            if not isinstance(node, str) or not node:
                raise ValueError(f"{where}.after: node가 필요하다: {a!r}")
            if node == START and outcome is not None:
                raise ValueError(f"{where}.after: {START!r}에는 outcome을 붙일 수 없다")
            after.append((node, outcome))

        delay = raw.get("delay", 0.0)
        if not _is_num(delay) or delay < 0:
            raise ValueError(f"{where}: delay는 0 이상의 수여야 한다: {delay!r}")
        repeat = raw.get("repeat", 1)
        if not _is_int(repeat) or repeat < 0:
            raise ValueError(f"{where}: repeat는 0 이상의 정수여야 한다 (0 = 무제한): {repeat!r}")
        note = raw.get("note", "")
        if not isinstance(note, str):
            raise ValueError(f"{where}: note는 문자열이어야 한다: {note!r}")

        # ── until ──
        until = raw.get("until") or {}
        if not isinstance(until, dict):
            raise ValueError(f"{where}: until은 dict여야 한다: {until!r}")
        _unknown(until, _UNTIL_FIELDS, f"{where}.until")
        until_time = until.get("time")
        if until_time is not None and (not _is_num(until_time) or until_time <= 0):
            # 0초 구간은 순환 스크립트에서 한 프레임에 무한히 돌 수 있고, 「켜졌다 같은 프레임에
            # 꺼지는 상태」는 뜻이 없다.
            raise ValueError(f"{where}: until.time은 0보다 커야 한다: {until_time!r}")
        until_cleared = until.get("targets_cleared", False)
        if not isinstance(until_cleared, bool):
            raise ValueError(f"{where}: until.targets_cleared는 bool이어야 한다: {until_cleared!r}")
        ua_raw = until.get("after", [])
        if not isinstance(ua_raw, list):
            raise ValueError(f"{where}: until.after는 list여야 한다: {ua_raw!r}")
        until_after: list[tuple[str, float]] = []
        for a in ua_raw:
            if not isinstance(a, dict):
                raise ValueError(f"{where}.until.after 항목은 {{'node', 'delay'}} dict여야 한다: {a!r}")
            _unknown(a, frozenset({"node", "delay"}), f"{where}.until.after 항목")
            node, d = a.get("node"), a.get("delay", 0.0)
            if not isinstance(node, str) or not node:
                raise ValueError(f"{where}.until.after: node가 필요하다: {a!r}")
            if node == pid:
                raise ValueError(f"{where}.until.after: 자기 자신을 따라 끝날 수는 없다")
            if node == START:
                raise ValueError(f"{where}.until.after: {START!r}는 열리기 전에 끝나 있어 "
                                 f"영영 따라 끝나지 않는다")
            if not _is_num(d) or d < 0:
                raise ValueError(f"{where}.until.after: delay는 0 이상의 수여야 한다: {d!r}")
            until_after.append((node, d))

        kw: dict = {}
        # ── kind별 ──
        if kind == "buff":
            en = raw["enemy"]
            if not isinstance(en, dict) or not en:
                raise ValueError(f"{where}: enemy는 def_mult·def_add를 담은 dict여야 한다: {en!r}")
            _unknown(en, _BUFF_FIELDS, f"{where}.enemy")
            m, a = en.get("def_mult", 1), en.get("def_add", 0)
            if not _is_num(m) or m < 0:
                raise ValueError(f"{where}.enemy: def_mult는 0 이상의 수여야 한다: {m!r}")
            if not _is_num(a):
                raise ValueError(f"{where}.enemy: def_add는 수여야 한다: {a!r}")
            kw.update(def_mult=m, def_add=a)
        elif kind == "core":
            cp = raw["core_px"]
            if not _is_int(cp) or cp <= 0:
                raise ValueError(f"{where}: core_px는 양의 정수여야 한다: {cp!r}")
            kw["core_px"] = cp
        elif kind == "shield":
            code = raw["code"]
            if code not in _CODE_ADVANTAGE:
                raise ValueError(f"{where}: 모르는 속성 코드 {code!r} — "
                                 f"{' · '.join(_CODE_ADVANTAGE)}")
            kw["code"] = code
        elif kind == "move":
            ws = raw["weapons"]
            if not isinstance(ws, list) or not all(isinstance(w, str) for w in ws):
                raise ValueError(f"{where}: weapons는 무기군 문자열 list여야 한다: {ws!r}")
            if weapon_types is not None:
                bad = [w for w in ws if w not in weapon_types]
                if bad:
                    raise ValueError(f"{where}: 모르는 무기군 {bad} — {' · '.join(sorted(weapon_types))}")
            kw["weapons"] = tuple(ws)
        elif kind in RESERVED_KINDS:
            if "spec" in raw and not isinstance(raw["spec"], dict):
                raise ValueError(f"{where}: spec은 dict여야 한다: {raw['spec']!r}")
        if kind in _TARGET_KINDS:
            tr = raw["targets"]
            if not isinstance(tr, list) or not tr:
                raise ValueError(f"{where}: 표적 없는 {kind} — targets가 비었다")
            targets = [_target(x, f"{where}.targets[{j}]") for j, x in enumerate(tr)]
            names = [x.name for x in targets]
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                raise ValueError(f"{where}: 표적 이름 중복 {dup}")
            kw["targets"] = tuple(targets)
        if until_cleared:
            if kind not in _TARGET_KINDS:
                raise ValueError(f"{where}: until.targets_cleared는 표적이 있는 kind"
                                 f"({' · '.join(sorted(_TARGET_KINDS))})에만 쓴다")
            if not any(x.breakable for x in kw["targets"]):
                raise ValueError(f"{where}: 깰 수 있는 표적(hp > 0)이 없는데 until.targets_cleared — "
                                 f"영영 cleared로 안 끝난다")

        out.append(Pattern(
            idx=i, id=pid, kind=kind, after=tuple(after), delay=delay,
            until_time=until_time, until_cleared=until_cleared,
            until_after=tuple(until_after), repeat=repeat,
            emit=_events(raw.get("emit"), f"{where}.emit"),
            emit_end=_events(raw.get("emit_end"), f"{where}.emit_end"),
            note=note, **kw))

    # ── 서로 참조 ──
    by_id: dict[str, Pattern] = {}
    for p in out:
        if p.id in by_id:
            raise ValueError(f"패턴 id 중복: {p.id!r} (id를 안 적었다면 자동 이름끼리 부딪힌 것이다)")
        by_id[p.id] = p
    for p in out:
        for node, outcome in p.after:
            if node == START:
                continue
            if node not in by_id:
                raise ValueError(f"패턴 {p.id!r}.after: 없는 패턴 {node!r}")
            if outcome is not None:
                q = by_id[node]
                has = {"cleared": q.until_cleared, "expired": q.until_time is not None,
                       "followed": bool(q.until_after)}[outcome]
                if not has:
                    raise ValueError(
                        f"패턴 {p.id!r}.after: {node!r}는 {_OUTCOME_NEEDS[outcome]}이 없어 "
                        f"{outcome!r}로 끝날 수 없다 — 이 분기는 영영 안 열린다")
        for node, _ in p.until_after:
            if node not in by_id:
                raise ValueError(f"패턴 {p.id!r}.until.after: 없는 패턴 {node!r}")

    # 전투 시작에서 이어지지 않는 패턴은 무엇을 적었든 영영 안 열린다.
    reach = {START}
    grew = True
    while grew:
        grew = False
        for p in out:
            if p.id not in reach and any(node in reach for node, _ in p.after):
                reach.add(p.id)
                grew = True
    dead = [p.id for p in out if p.id not in reach]
    if dead:
        raise ValueError(f"전투 시작에서 이어지지 않아 영영 안 열리는 패턴: {dead}")
    return out


# ── 실행 ──────────────────────────────────────────────────────────────────

@dataclass
class _Target:
    spec: TargetSpec
    dealt: float = 0.0
    destroyed: bool = False


@dataclass
class _Run:
    """패턴 하나의 실행 상태. 다시 열리면 표적·막은 딜이 새로 시작한다."""
    p: Pattern
    active: bool = False
    start_t: float = 0.0
    opens: int = 0
    consumed_after: list[int] = field(default_factory=list)
    consumed_until: list[int] = field(default_factory=list)
    targets: list[_Target] = field(default_factory=list)
    blocked: float = 0.0


class BossScript:
    """스케줄러 + 적 상태 오버레이 + 딜 게이트 + 표적 체력 풀.

    `superior(caster, code)` — 그 캐스터의 딜이 `code` 속성보호막을 통과하는가. 로스터 코드
    상성이거나 `element_code_override` 버프로 우월해졌거나다(인게임이 후자도 인정한다). 후자가
    버프라 호출 시점에 봐야 해서 timeline이 콜백으로 넘긴다.
    """

    def __init__(self, patterns: list[Pattern], enemy: dict,
                 superior: Callable[[str, str], bool]):
        self._superior = superior
        self._runs = [_Run(p) for p in patterns]
        # 기본 상태 — 패턴이 없을 때의 적. 매 프레임 여기서 출발해 덮어쓴다.
        self._base_def = enemy.get("def", DEFAULT_ENEMY_DEF)
        self._base_core = enemy.get("core_px", 0)
        self._base_parts = enemy.get("has_parts", False)
        self._base_weapons = enemy.get("optimal_range_weapons", [])
        # 노드별 종료 기록 (시각, outcome). START는 첫 프레임에 한 번 끝난다.
        self._ends: dict[str, list[tuple[float, str]]] = {p.id: [] for p in patterns}
        self._ends[START] = []
        # 흡수 자리에서 깨진 표적의 이벤트는 다음 프레임 맨 앞에서 걷는다(최대 한 프레임 지연).
        # `_dot_events`를 다음 프레임 시작에 수거하는 것과 같은 규약이다.
        self._carry: list[str] = []
        # 이번 프레임의 게이트·흡수 대상 — `_apply()`가 채운다
        self._vanish: list[_Run] = []
        self._shields: list[_Run] = []
        self._absorbers: list[_Run] = []

        # 보스가 사라졌는가. 딜 게이트는 `admit()`이 직접 하고, timeline은 이 값을 state에 실어
        # 무기 사격의 버스트 게이지를 거른다(평타가 빗나가면 그 게이지도 안 찬다).
        self.vanished = False
        self.log: list[BossLogEntry] = []
        self.score = 0
        self.unmodeled: list[str] = []

    # ── 프레임 맨 앞 ──

    def begin_frame(self, t: float, enemy: dict) -> list[str]:
        """전이를 확정하고 적 상태를 `enemy`에 기록한다. 스쿼드에 쏠 이벤트를 돌려준다.

        **이 프레임의 누구도 적 상태를 읽기 전에** 불러야 한다 — 코어 직경·방어력·적정거리가
        프레임 안에서 어긋나면 안 된다. 같은 시각에 두 번 불러도 결과가 같다(전이가 이미 끝나
        있으면 아무것도 안 한다).
        """
        events, self._carry = self._carry, []
        if not self._ends[START]:
            self._ends[START].append((t, START))
        self._close_due(t, events)
        # 여는 건 한 바퀴면 된다. 열린 패턴은 같은 프레임에 끝날 수 없다 — 표적은 새로 차고,
        # until.time은 0보다 크고, until.after는 열린 뒤의 종료만 센다. 그래서 열기가 또 다른
        # 종료를 낳지 않고 순환 스크립트도 한 프레임에 갇히지 않는다.
        for run in self._runs:
            if not run.active:
                trig = self._ready(run, t)
                if trig is not None:
                    self._open(run, t, trig, events)
        self._apply(enemy)
        return events

    def _matching(self, node: str, outcome: str | None) -> list[float]:
        return [te for te, oc in self._ends[node] if outcome is None or oc == outcome]

    def _ready(self, run: _Run, t: float) -> str | None:
        p = run.p
        if p.repeat and run.opens >= p.repeat:
            return None
        for i, (node, outcome) in enumerate(p.after):
            ends = self._matching(node, outcome)
            k = run.consumed_after[i] if run.opens else 0
            # 소비하지 않은 종료 중 가장 이른 것부터 지연을 잰다
            if len(ends) > k and t >= ends[k] + p.delay - _EPS:
                return node if outcome is None else f"{node}({outcome})"
        return None

    def _close_due(self, t: float, events: list[str]) -> None:
        """이 프레임에 끝나는 패턴을 사유와 함께 확정하고 닫는다.

        cleared·expired는 서로와 무관하게 정해지지만 followed는 **같은 프레임의 다른 종료**에
        기대므로 퍼뜨려서 구한다. 같은 프레임에 둘 다 성립하면 followed가 expired를 이긴다.
        """
        due: dict[str, str] = {}
        for run in self._runs:
            if not run.active:
                continue
            p = run.p
            if p.until_cleared and all(x.destroyed for x in run.targets if x.spec.breakable):
                due[p.id] = "cleared"
            elif p.until_time is not None and t >= run.start_t + p.until_time - _EPS:
                due[p.id] = "expired"
        grew = True
        while grew:
            grew = False
            for run in self._runs:
                p = run.p
                if not run.active or due.get(p.id) in ("cleared", "followed"):
                    continue
                for i, (node, delay) in enumerate(p.until_after):
                    ends, k = self._ends[node], run.consumed_until[i]
                    if len(ends) > k:
                        te = ends[k][0]
                    elif node in due:
                        te = t          # 이 프레임에 끝난다
                    else:
                        continue
                    if t >= te + delay - _EPS:
                        due[p.id] = "followed"
                        grew = True
                        break
        for run in self._runs:
            if run.p.id in due:
                self._close(run, t, due[run.p.id], events)

    def _open(self, run: _Run, t: float, trigger: str, events: list[str]) -> None:
        p = run.p
        run.active = True
        run.start_t = t
        run.opens += 1
        run.consumed_after = [len(self._matching(n, o)) for n, o in p.after]
        run.consumed_until = [len(self._ends[n]) for n, _ in p.until_after]
        run.targets = [_Target(s) for s in p.targets]
        run.blocked = 0.0
        if p.kind in RESERVED_KINDS and p.id not in self.unmodeled:
            self.unmodeled.append(p.id)
        detail = f"after {trigger}" + (f" +{p.delay:g}s" if p.delay else "")
        if p.kind in RESERVED_KINDS:
            detail += " · 효과 모델 없음"
        self.log.append(BossLogEntry(t=t, pattern=p.id, kind=p.kind, event="start",
                                     detail=detail))
        events.extend(p.emit)

    def _close(self, run: _Run, t: float, outcome: str, events: list[str]) -> None:
        p = run.p
        run.active = False
        self._ends[p.id].append((t, outcome))
        bits = []
        breakable = [x for x in run.targets if x.spec.breakable]
        if breakable:
            bits.append(f"표적 {sum(x.destroyed for x in breakable)}/{len(breakable)} 파괴")
        if p.kind in ("shield", "vanish"):
            bits.append(f"막은 딜 {round(run.blocked):,}")
        self.log.append(BossLogEntry(t=t, pattern=p.id, kind=p.kind, event="end",
                                     outcome=outcome, detail=" · ".join(bits)))
        if outcome != "end":
            events.extend(p.emit_end)

    def _apply(self, enemy: dict) -> None:
        """열린 패턴을 시작 시각 순(같으면 선언 순)으로 기본 상태 위에 덮어쓴다."""
        live = sorted((r for r in self._runs if r.active), key=lambda r: (r.start_t, r.p.idx))
        d, core = self._base_def, self._base_core
        parts, weapons = self._base_parts, self._base_weapons
        vanish, shields, absorbers = [], [], []
        for r in live:
            p = r.p
            if p.kind == "buff":
                d = d * p.def_mult + p.def_add
            elif p.kind == "core":
                core = max(core, p.core_px)
            elif p.kind == "move":
                weapons = list(p.weapons)
            elif p.kind == "shield":
                shields.append(r)
            elif p.kind == "vanish":
                vanish.append(r)
            elif p.kind in _TARGET_KINDS:
                alive = [x for x in r.targets if not x.destroyed]
                if alive:
                    if p.kind == "parts":
                        parts = True
                    core = max(core, max(x.spec.core_px for x in alive))
                if any(x.spec.breakable for x in alive):
                    absorbers.append(r)
        enemy["def"] = d
        enemy["core_px"] = core
        enemy["has_parts"] = parts
        enemy["optimal_range_weapons"] = weapons
        self._vanish, self._shields, self._absorbers = vanish, shields, absorbers
        self.vanished = bool(vanish)

    # ── 히트마다 ──

    def admit(self, ev: HitEvent, t: float) -> bool:
        """이 히트가 들어가는가. 들어가면 표적에 흡수하고 True.

        **거른 뒤에 흡수한다.** 안 들어간 딜로 저지원이 깨지면 안 된다 — 그래야 「속성보호막을
        두르고 저지를 띄운다」는 연계가 제대로 어려워진다.

        게이트는 결과 이벤트 자리에 있다. 사라짐은 평타만 빼고, 발사로 파생된 스킬과 이미 걸린
        지속 대미지는 보스가 화면에 없어도 들어간다 — 트리거는 이미 처리된 뒤다.
        """
        if self._vanish and _is_normal(ev):
            self._vanish[0].blocked += ev.damage
            return False
        for r in self._shields:
            # 보호막이 여럿 겹치면 전부 이겨야 한다
            if not self._superior(ev.caster, r.p.code):
                r.blocked += ev.damage
                return False
        for r in self._absorbers:
            for x in r.targets:
                if x.destroyed or not x.spec.breakable:
                    continue
                # 교체 지점: 조준·좌표 모델이 생기면 손으로 적은 share를 좌표에서 유도한 값으로
                x.dealt += ev.damage * x.spec.share
                if x.dealt >= x.spec.hp:
                    x.destroyed = True
                    self.score += x.spec.score
                    self.log.append(BossLogEntry(
                        t=t, pattern=r.p.id, kind=r.p.kind, event="destroy",
                        detail=x.spec.name + (f" · 점수 {x.spec.score:,}" if x.spec.score else "")))
                    self._carry.extend(x.spec.emits)
        return True

    # ── 루프 종료 뒤 ──

    def finish(self, t: float) -> None:
        for run in self._runs:
            if run.active:
                self._close(run, t, "end", [])


def legacy_to_patterns(enemy: dict) -> dict:
    """종전 스칼라를 「전투 시작에 열려 끝까지 가는 패턴」으로 옮긴 적을 돌려준다.

    **검산 전용이다. 엔진은 안 쓴다** — 스칼라를 그대로 두는 편이 회귀에 안전하다. 두 적이 같은
    전투를 내면 오버레이 배선이 기본 경로와 어긋나지 않았다는 증거가 된다.
    """
    if enemy.get("patterns"):
        raise ValueError("이미 패턴이 있는 적은 등가 변환의 대상이 아니다")
    out = {k: v for k, v in enemy.items() if k not in OVERLAY_FIELDS and k != "patterns"}
    out.update(core_px=0, has_parts=False, optimal_range_weapons=[])
    pats: list[dict] = [{"id": "기본 방어력", "kind": "buff",
                         "enemy": {"def_mult": 0, "def_add": enemy.get("def", DEFAULT_ENEMY_DEF)}}]
    if enemy.get("core_px", 0) > 0:
        pats.append({"id": "기본 코어", "kind": "core", "core_px": enemy["core_px"]})
    if enemy.get("has_parts"):
        pats.append({"id": "기본 파츠", "kind": "parts", "targets": [{"name": "파츠", "hp": 0}]})
    if enemy.get("optimal_range_weapons"):
        pats.append({"id": "기본 적정거리", "kind": "move",
                     "weapons": list(enemy["optimal_range_weapons"])})
    out["patterns"] = pats
    return out


# ── 자체검산 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    from .damage import is_element_match

    DT = 1 / 60
    BASE = {"def": 31784, "code": "수냉", "core_px": 0, "has_parts": False,
            "optimal_range_weapons": []}
    ROSTER = {"전격캐": "전격", "작열캐": "작열"}

    def superior(caster: str, code: str) -> bool:
        return is_element_match(ROSTER.get(caster, ""), code)

    def run(patterns, until_t, hits=None, base=None):
        """timeline 루프를 흉내 낸다: 프레임 맨 앞 전이 → 히트 흡수. 프레임별 적 상태도 남긴다."""
        enemy = dict(base or BASE)
        boss = BossScript(validate(patterns, weapon_types=frozenset({"SG", "SMG", "SR"})),
                          enemy, superior)
        frames, admitted, fired = [], [], []
        t = 0.0
        while t <= until_t:
            fired += [(t, e) for e in boss.begin_frame(t, enemy)]
            frames.append((t, dict(enemy), boss.vanished))
            for ev in (hits(t) if hits else []):
                if boss.admit(ev, t):
                    admitted.append(ev)
            t += DT
        boss.finish(until_t)
        return boss, frames, admitted, fired

    def starts(boss, pid):
        return [e.t for e in boss.log if e.pattern == pid and e.event == "start"]

    def ends(boss, pid):
        return [(e.t, e.outcome) for e in boss.log if e.pattern == pid and e.event == "end"]

    def near(a, b):
        return abs(a - b) < DT / 2

    def state_at(frames, t):
        return next(s for ft, s, _ in frames if near(ft, t) or ft > t)

    normal = lambda c, d: HitEvent(t=0, caster=c, damage=d, is_crit=False, hit_tag="normal")
    skill = lambda c, d: HitEvent(t=0, caster=c, damage=d, is_crit=False, hit_tag="dot_damage",
                                  skill_name="지속딜")

    # ── 검산 1: 순차 — A(5초) → B(3초) → C가 5.0s·8.0s에 갈린다
    b, *_ = run([{"id": "A", "kind": "idle", "until": {"time": 5}},
                 {"id": "B", "kind": "idle", "after": ["A"], "until": {"time": 3}},
                 {"id": "C", "kind": "idle", "after": ["B"]}], 10)
    assert near(starts(b, "B")[0], 5.0) and near(starts(b, "C")[0], 8.0), b.log
    assert ends(b, "C") == [(10, "end")], ends(b, "C")
    print(f"검산 1 — 순차: B {starts(b, 'B')[0]:.3f}s · C {starts(b, 'C')[0]:.3f}s")

    # ── 검산 2: 동시 — 같은 after를 나눠 가진 둘이 함께 열린다 / 시각 여유 (20초가 20.017로 밀리지 않는다)
    b, *_ = run([{"id": "A", "kind": "idle", "until": {"time": 20}},
                 {"id": "B1", "kind": "idle", "after": ["A"]},
                 {"id": "B2", "kind": "groggy", "after": ["A"], "delay": 1.5}], 25)
    assert starts(b, "B1") == [ends(b, "A")[0][0]] and near(starts(b, "B1")[0], 20.0)
    assert near(starts(b, "B2")[0], 21.5)
    print(f"검산 2 — 동시·지연: B1 {starts(b, 'B1')[0]:.3f}s · B2 {starts(b, 'B2')[0]:.3f}s")

    # ── 검산 3·4: 저지 성공 / 실패
    def interrupt_script():
        return [
            {"id": "저지", "kind": "interrupt", "after": [{"node": "대기"}],
             "until": {"time": 15, "targets_cleared": True},
             "emit": ["event:target_spawn"],
             "targets": [{"name": "저지원A", "hp": 1000, "score": 500,
                          "emit_on_destroy": ["enemy_death"]},
                         {"name": "저지원B", "hp": 1000, "share": 0.5},
                         {"name": "본체", "hp": 0}]},
            {"id": "대기", "kind": "idle", "until": {"time": 2}},
            {"id": "성공", "kind": "groggy", "after": [{"node": "저지", "outcome": "cleared"}],
             "until": {"time": 5}},
            {"id": "광역기", "kind": "attack", "after": [{"node": "저지", "outcome": "expired"}],
             "until": {"time": 5}, "spec": {"damage_pct": 300}},
        ]
    # 2초부터 초당 60프레임 × 10 = 600딜 → A는 1000에서 약 3.67초, B(절반)는 약 5.33초에 깨진다
    b, _, _, fired = run(interrupt_script(), 30, hits=lambda t: [normal("전격캐", 10)])
    (t_end, oc), = ends(b, "저지")
    destroys = [e for e in b.log if e.event == "destroy"]
    assert oc == "cleared" and near(t_end, destroys[-1].t + DT), (t_end, destroys)
    assert starts(b, "성공") == [t_end] and not starts(b, "광역기")
    assert b.score == 500 and not b.unmodeled
    assert (2.0, "event:target_spawn") in [(round(ft, 6), e) for ft, e in fired]
    t_death = next(ft for ft, e in fired if e == "enemy_death")
    assert near(t_death, destroys[0].t + DT), "파괴 이벤트는 다음 프레임 맨 앞에서 나간다"
    print(f"검산 3 — 저지 성공: {t_end:.3f}s cleared · 점수 {b.score} · 실패 분기 무발동")

    b, *_ = run(interrupt_script(), 30)
    (t_end, oc), = ends(b, "저지")
    assert oc == "expired" and near(t_end, 17.0) and not starts(b, "성공")
    assert near(starts(b, "광역기")[0], 17.0) and b.unmodeled == ["광역기"]
    print(f"검산 4 — 저지 실패: {t_end:.3f}s expired → 광역기(예약, 효과 없음) 구간 차지")

    # ── 우선순위: 제한시간이 끝나는 바로 그 프레임에 마지막 저지원이 깨졌다면 성공이다
    lim = 1.0
    b, *_ = run([{"id": "저지", "kind": "interrupt", "until": {"time": lim, "targets_cleared": True},
                  "targets": [{"name": "X", "hp": 100}]}], 3,
                hits=lambda t: [normal("전격캐", 100)] if lim - DT - DT / 2 < t < lim - DT / 2 else [])
    assert ends(b, "저지")[0][1] == "cleared", ends(b, "저지")
    print(f"검산 5 — 우선순위: 마감 프레임에 전멸 → {ends(b, '저지')[0][1]}")

    # ── 검산 6: 속성보호막 — 우월 코드만 통과하고, 막힌 딜은 표적도 못 깎는다
    b, frames, admitted, _ = run(
        [{"id": "보호막", "kind": "shield", "code": "수냉", "until": {"time": 5}},
         {"id": "파츠", "kind": "parts", "targets": [{"name": "팔", "hp": 10 ** 9}]}],
        10, hits=lambda t: [normal("전격캐", 10), skill("작열캐", 30)])
    n_fire = sum(1 for e in admitted if e.caster == "작열캐")
    n_elec = sum(1 for e in admitted if e.caster == "전격캐")
    assert n_elec == len(frames) and n_fire == len(frames) - 300, (n_fire, len(frames))
    팔 = b._runs[1].targets[0]
    assert 팔.dealt == 10 * n_elec + 30 * n_fire, "막힌 딜이 표적을 깎았다"
    shield_end = next(e for e in b.log if e.pattern == "보호막" and e.event == "end")
    assert shield_end.detail == "막은 딜 9,000", shield_end
    print(f"검산 6 — 속성보호막: 작열 300프레임 차단 · 팔이 받은 딜 {팔.dealt:,.0f} ({shield_end.detail})")

    # ── 검산 7: 사라짐 — 평타만 빠지고 스킬·지속딜은 들어간다 / 사라진 구간이 [1, 3)이다
    # (평타 게이지를 거르는 자리는 timeline `CharState._weapon_gauge_lands()` — 스킬 게이지는 그대로 찬다)
    b, frames, admitted, _ = run(
        [{"id": "출현", "kind": "idle", "until": {"time": 1}},
         {"id": "사라짐", "kind": "vanish", "after": ["출현"], "until": {"time": 2}}],
        4, hits=lambda t: [normal("전격캐", 1), skill("전격캐", 1)])
    n_normal = sum(1 for e in admitted if e.hit_tag == "normal")
    n_skill = sum(1 for e in admitted if e.hit_tag == "dot_damage")
    gone = [ft for ft, _, v in frames if v]
    assert n_skill == len(frames) and n_normal == len(frames) - 120, (n_normal, n_skill)
    assert near(gone[0], 1.0) and near(gone[-1], 3.0 - DT) and len(gone) == 120
    print(f"검산 7 — 사라짐: 평타 {len(frames) - n_normal}프레임 빠짐 · 스킬 전부 들어감 · "
          f"사라진 구간 {gone[0]:.3f}~{gone[-1]:.3f}s")

    # ── 검산 8: 코어 파츠 — 표적이 깨질 때 코어·파츠도 함께 닫힌다. 합성은 큰 코어
    b, frames, _, _ = run(
        [{"id": "코어", "kind": "core", "core_px": 30},
         {"id": "약점", "kind": "parts", "targets": [{"name": "코어 파츠", "hp": 600, "core_px": 45}]}],
        3, hits=lambda t: [skill("전격캐", 10)] if t >= 1 - DT / 2 else [])
    dt_ = next(e.t for e in b.log if e.event == "destroy")
    before, after = state_at(frames, dt_), state_at(frames, dt_ + DT)
    assert before["core_px"] == 45 and before["has_parts"] is True, before
    assert after["core_px"] == 30 and after["has_parts"] is False, after
    assert near(dt_, 2.0 - DT)
    print(f"검산 8 — 코어 파츠: {dt_:.3f}s 파괴 → 다음 프레임 core {before['core_px']}→{after['core_px']} · "
          f"has_parts {before['has_parts']}→{after['has_parts']}")

    # ── 검산 9: 사이클 — after 순환 + repeat 0 / 오버레이(방어력·적정거리)가 구간을 따른다
    b, frames, _, _ = run(
        [{"id": "강화", "kind": "buff", "after": ["start", "돌진"], "repeat": 0,
          "until": {"time": 2}, "enemy": {"def_mult": 2, "def_add": 100}},
         {"id": "후퇴", "kind": "move", "after": ["강화"], "repeat": 0, "until": {"time": 3},
          "weapons": ["SR"]},
         {"id": "돌진", "kind": "move", "after": ["후퇴"], "repeat": 0, "until": {"time": 1},
          "weapons": ["SG", "SMG"]}], 20)
    st = starts(b, "강화")
    assert len(st) == 4 and all(near(x, 6.0 * i) for i, x in enumerate(st)), st
    assert state_at(frames, 1.0)["def"] == 31784 * 2 + 100
    assert state_at(frames, 3.0)["def"] == 31784 and state_at(frames, 3.0)["optimal_range_weapons"] == ["SR"]
    assert state_at(frames, 5.5)["optimal_range_weapons"] == ["SG", "SMG"]
    assert state_at(frames, 6.5)["optimal_range_weapons"] == []
    print(f"검산 9 — 사이클: 강화 {' · '.join(f'{x:.3f}' for x in st)}s")

    # ── 검산 10: 등가 변환 — 종전 스칼라와 같은 적 상태가 나온다
    for legacy in ({"def": 31784, "code": "수냉"},
                   {"def": 50000, "code": "작열", "core_px": 52, "has_parts": True,
                    "optimal_range_weapons": ["SG", "SMG"]}):
        full = {**BASE, **legacy}
        conv = legacy_to_patterns(full)
        enemy = {**BASE, **{k: v for k, v in conv.items() if k != "patterns"}}
        boss = BossScript(validate(conv["patterns"]), enemy, superior)
        boss.begin_frame(0.0, enemy)
        for k in OVERLAY_FIELDS:
            assert enemy[k] == full[k], (k, enemy[k], full[k])
    print("검산 10 — 등가 변환: 스칼라 넷이 같다")

    # ── 검산 11: 잘못된 스크립트는 전부 거절한다
    idle = {"id": "A", "kind": "idle"}
    tg = [{"name": "X", "hp": 10}]
    bad_cases = {
        "모르는 kind":            [{"kind": "roar"}],
        "모르는 칸":              [{"kind": "idle", "untill": {"time": 1}}],
        "kind에 없는 칸":         [{"kind": "core", "core_px": 10, "targets": tg}],
        "id 중복":               [idle, dict(idle)],
        "자동 id 충돌":           [{"kind": "idle"}, {"id": "idle1", "kind": "idle"}],
        "예약어 id":             [{"id": "start", "kind": "idle"}],
        "없는 패턴 참조":          [{"kind": "idle", "after": ["B"]}],
        "until.after 자기 자신":   [{"id": "A", "kind": "idle", "until": {"after": [{"node": "A"}]}}],
        "until.after 없는 패턴":   [{"kind": "idle", "until": {"after": [{"node": "Z"}]}}],
        "until.time 0":          [{"kind": "idle", "until": {"time": 0}}],
        "until.time 음수":        [{"kind": "idle", "until": {"time": -1}}],
        "없는 속성 코드":          [{"kind": "shield", "code": "빛"}],
        "core_px 0":             [{"kind": "core", "core_px": 0}],
        "표적 없는 parts":         [{"kind": "parts", "targets": []}],
        "표적 없는 interrupt":     [{"kind": "interrupt"}],
        "표적 이름 중복":          [{"kind": "parts", "targets": tg + tg}],
        "음수 hp":               [{"kind": "parts", "targets": [{"name": "X", "hp": -1}]}],
        "음수 share":            [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "share": -0.1}]}],
        "hp 없는 표적":           [{"kind": "parts", "targets": [{"name": "X"}]}],
        "표적의 모르는 칸":        [{"kind": "parts", "targets": [{"name": "X", "hp": 1, "hpp": 2}]}],
        "깰 표적 없이 cleared":    [{"kind": "interrupt", "until": {"targets_cleared": True},
                                  "targets": [{"name": "X", "hp": 0}]}],
        "표적 kind 아닌 cleared":  [{"kind": "idle", "until": {"targets_cleared": True}}],
        "빈 after":              [{"kind": "idle", "after": []}],
        "outcome end":           [idle, {"kind": "idle", "after": [{"node": "A", "outcome": "end"}]}],
        "불가능한 outcome":        [idle, {"kind": "idle", "after": [{"node": "A", "outcome": "expired"}]}],
        "start에 outcome":        [{"kind": "idle", "after": [{"node": "start", "outcome": "expired"}]}],
        "시작에서 끊긴 패턴":       [{"id": "P", "kind": "idle", "after": ["Q"], "until": {"time": 1}},
                                 {"id": "Q", "kind": "idle", "after": ["P"], "until": {"time": 1}}],
        "모르는 이벤트":           [{"kind": "idle", "emit": ["event:part_destory"]}],
        "모르는 무기군":           [{"kind": "move", "weapons": ["LMG"]}],
        "예약 아닌 kind의 spec":   [{"kind": "idle", "spec": {}}],
        "없앤 칸(게이지 토글)":     [{"kind": "vanish", "blocks_burst_gauge": False}],
        "모르는 buff 칸":          [{"kind": "buff", "enemy": {"atk_mult": 2}}],
        "repeat 음수":            [{"kind": "idle", "repeat": -1}],
        "list 아님":             {"kind": "idle"},
    }
    for label, pats in bad_cases.items():
        try:
            validate(pats, weapon_types=frozenset({"SG", "SMG", "SR"}))
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: {label}")
    print(f"검산 11 — 잘못된 스크립트 {len(bad_cases)}종 전부 거절")

    print("\n모든 검산 통과.")
