"""보스 입력 — 레이드 보스 프리셋과 보스 스크립트 파일을 `simulate(enemy=)`의 적 dict로 만든다.

    python -m runner.sim "..." --boss "솔로 레이드 S40"             # 프리셋만 — 스탯·속성 (간단 모드)
    python -m runner.sim "..." --boss 스크립트.json --view boss      # 스크립트 파일 (패턴 모드)

**calculator/는 이 모듈과 `data/boss_presets.json`을 모른다.** 캐릭터 dict를 `runner/spec.py`가 만들듯
적 dict는 여기서 만들고, 엔진은 전개가 끝난 dict만 받는다. 패턴 포맷 자체의 정본은
`calculator/boss_pattern.py` 모듈 docstring이고, 이 모듈은 그 위에 **프리셋 참조**만 얹는다.

프리셋은 게임 테이블에서 옮긴 것(스탯·속성, 스킬 → 공격 spec, 파츠 체력)만 담는다. 스킬을 언제·어떤
순서로 쓰는지는 테이블에 없어 **스크립트가 적는다**(유저 결정 2026-09-15). 옮기는 규칙과 출처는
`docs/DATA_VERIFY.md` §레이드 보스 스탯.

─────────────────────────────────────────────────────────────────────────────
스크립트 파일 — 적 dict 하나 (JSON, `.json`으로 끝나야 파일로 읽는다)
─────────────────────────────────────────────────────────────────────────────
  {
    "preset": "솔로 레이드 S40",    프리셋 이름. 그 `enemy`(atk·def·code)가 먼저 깔리고 아래 칸이 덮는다
    "note": "",                    사람용 메모
    "core_px": 40,                 그 밖의 최상위 칸은 `timeline.DEFAULT_ENEMY`의 키만 받는다
    "patterns": [
      {"id": "총", "kind": "attack", "skill": "Luxurious Guns",
       "after": ["start", "총"], "delay": 4, "until": {"time": 1}, "repeat": 0},
      {"id": "알", "kind": "interrupt", "until": {"time": 7, "targets_cleared": true},
       "targets": [{"part": "Egg Sac", "share": 0.3}]}
    ]
  }

**`skill`** (attack 패턴) — 프리셋 `skills`의 이름. 그 `spec`이 깔리고 패턴에 적은 `spec`이 **칸 단위로**
  덮는다 — `"spec": {"hits": 6}`만 적어도 된다(발수 ⬜를 바꿔 볼 때). attack 밖의 kind에 적으면 거절한다.
  프리셋 `unmodeled`에 있는 이름이면 그 사유를 달고 거절한다 — 모델 없는 스킬이 딜 없는 공격으로 조용히
  돌면 안 된다.
**`part`** (interrupt·parts의 표적 항목) — 프리셋 `parts`의 이름. `name`은 파츠 이름, `hp`는 프리셋 체력이
  기본이고 항목에 적은 칸이 이긴다.
`skill`·`part`는 `preset`이 있어야 쓴다.

잘못 적힌 참조와 모르는 최상위 칸은 즉시 실패시킨다 — `boss_pattern.validate`와 같은 이유다(조용히
무시되면 딜은 그럴듯하게 나오고 발견이 늦다). 패턴 안쪽 검사는 `simulate()`가 부르는 `validate`가 한다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from calculator.boss_pattern import MODE_LABELS, SIMPLE, boss_mode
from calculator.timeline import DEFAULT_ENEMY

_ROOT = Path(__file__).resolve().parent.parent
PRESETS = _ROOT / "data" / "boss_presets.json"

_FILE_FIELDS = frozenset(DEFAULT_ENEMY) | {"preset", "note"}
_TARGET_KINDS = frozenset({"interrupt", "parts"})


def load_presets() -> dict[str, dict]:
    """프리셋 이름 → 프리셋. `_`로 시작하는 키(설명)는 뺀다."""
    with open(PRESETS, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def build_enemy(raw: dict, presets: dict[str, dict] | None = None) -> dict:
    """스크립트 dict(프리셋 참조 포함)를 엔진이 받는 적 dict로 전개한다. 입력은 건드리지 않는다."""
    if not isinstance(raw, dict):
        raise ValueError(f"보스 스크립트는 dict여야 한다: {type(raw).__name__}")
    extra = sorted(set(raw) - _FILE_FIELDS)
    if extra:
        raise ValueError(f"보스 스크립트: 모르는 칸 {extra} — 쓸 수 있는 칸: {' · '.join(sorted(_FILE_FIELDS))}")
    presets = load_presets() if presets is None else presets
    name = raw.get("preset")
    preset = None
    if name is not None:
        if name not in presets:
            raise ValueError(f"모르는 보스 프리셋 {name!r} — {' · '.join(presets) or '등록된 프리셋 없음'}")
        preset = presets[name]

    raw = copy.deepcopy(raw)
    enemy = dict(preset["enemy"]) if preset else {}
    enemy.update({k: v for k, v in raw.items() if k not in ("preset", "note")})
    if isinstance(enemy.get("patterns"), list):  # list가 아니면 validate가 거절한다
        enemy["patterns"] = [_expand_pattern(p, i, preset, name)
                             for i, p in enumerate(enemy["patterns"])]
    return enemy


def _expand_pattern(p, i: int, preset: dict | None, pname: str | None):
    if not isinstance(p, dict):
        return p  # 모양 검사는 validate 몫
    where = f"enemy.patterns[{i}]" + (f" {p['id']!r}" if isinstance(p.get("id"), str) else "")
    if "skill" in p:
        skill = p.pop("skill")
        if p.get("kind") != "attack":
            raise ValueError(f"{where}: skill은 attack 패턴에만 쓴다 (kind {p.get('kind')!r})")
        entry = _lookup(preset, pname, "skills", skill, where, "skill")
        over = p.get("spec", {})
        if not isinstance(over, dict):
            raise ValueError(f"{where}: skill과 함께 쓰는 spec은 덮어쓸 칸만 적은 dict여야 한다: {over!r}")
        p["spec"] = {**copy.deepcopy(entry["spec"]), **over}
    if p.get("kind") in _TARGET_KINDS and isinstance(p.get("targets"), list):
        p["targets"] = [_expand_target(t, preset, pname, f"{where}.targets[{j}]")
                        for j, t in enumerate(p["targets"])]
    return p


def _expand_target(t, preset: dict | None, pname: str | None, where: str):
    if not isinstance(t, dict) or "part" not in t:
        return t
    part = t.pop("part")
    entry = _lookup(preset, pname, "parts", part, where, "part")
    return {"name": part, "hp": entry["hp"], **t}


def _lookup(preset: dict | None, pname: str | None, section: str, key, where: str, field: str) -> dict:
    if preset is None:
        raise ValueError(f"{where}: {field} {key!r} — 프리셋 참조는 스크립트 최상위에 preset이 있어야 쓴다")
    if section == "skills" and key in preset.get("unmodeled", {}):
        raise ValueError(f"{where}: {key!r}는 공격으로 옮기지 못한 스킬이다 — {preset['unmodeled'][key]}")
    table = preset.get(section, {})
    if key not in table:
        raise ValueError(f"{where}: 프리셋 {pname!r}에 없는 {field} {key!r} — {' · '.join(table) or '없음'}")
    return table[key]


def load_boss(arg: str) -> tuple[dict, str]:
    """`--boss` 값 하나 → (적 dict, 출력용 이름). `.json`으로 끝나면 스크립트 파일, 아니면 프리셋 이름."""
    if arg.lower().endswith(".json"):
        path = Path(arg)
        if not path.is_file():
            raise ValueError(f"보스 스크립트 파일이 없다: {arg}")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        label = path.name
        if isinstance(raw, dict) and raw.get("preset"):
            label += f" (프리셋 {raw['preset']})"
    else:
        raw, label = {"preset": arg}, arg
    return build_enemy(raw), label


def describe(enemy: dict, label: str) -> str:
    """이탈 보고 자리에 싣는 한 줄 — 기본 적이 아닌 보스로 돌렸다는 사실과 그 수치."""
    e = {**DEFAULT_ENEMY, **enemy}
    parts = [f"공격력 {e['atk']:,}", f"방어력 {e['def']:,}", e["code"] or "속성 없음"]
    if e["core_px"]:
        parts.append(f"코어 {e['core_px']}px")
    if e.get("distance") is not None:
        parts.append(f"거리 {e['distance']:g}")
    if e["part_break_interval"]:
        parts.append(f"파츠 파괴 {e['part_break_interval']:g}초마다")
    mode = boss_mode(e)
    parts.append(MODE_LABELS[mode] + (f" (패턴 {len(e['patterns'])}개)" if mode != SIMPLE else ""))
    return f"적: {label} — {' · '.join(parts)}"


# ── 자체검산 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    from calculator.boss_pattern import validate

    presets = load_presets()

    # ── 검산 1: 등록된 프리셋은 전부 엔진 검사를 통과한다 — 스킬마다 attack 패턴, 파츠마다 표적
    for pname, pre in presets.items():
        assert set(pre["skills"]).isdisjoint(pre.get("unmodeled", {})), f"{pname}: skills·unmodeled 중복"
        pats = [{"id": s, "kind": "attack", "skill": s} for s in pre["skills"]]
        if pre.get("parts"):
            pats.append({"id": "파츠", "kind": "interrupt", "targets": [{"part": p} for p in pre["parts"]]})
        enemy = build_enemy({"preset": pname, "patterns": pats}, presets)
        validate(enemy["patterns"], squad_size=5)
        assert set(pre["enemy"]) <= set(DEFAULT_ENEMY), f"{pname}: enemy에 모르는 칸"
    print(f"검산 1 — 프리셋 {len(presets)}개: 스킬·파츠 전부 validate 통과")

    # ── 검산 2: 프리셋만 주면 스탯·속성만 깔리고 패턴은 없다 · 파일 칸이 프리셋을 덮는다
    s40, _ = load_boss("솔로 레이드 S40")
    assert s40 == {"atk": 114344, "def": 31784, "code": "풍압"}, s40
    assert build_enemy({"preset": "솔로 레이드 S40", "def": 1, "note": "메모"}, presets)["def"] == 1
    print(f"검산 2 — {describe(s40, '솔로 레이드 S40')}")

    # ── 검산 3: skill 전개 — 프리셋 spec 위에 패턴 spec이 칸 단위로 덮인다
    raw = {"preset": "솔로 레이드 S40", "patterns": [
        {"id": "총", "kind": "attack", "skill": "Luxurious Guns", "spec": {"hits": 6}},
        {"id": "알", "kind": "interrupt", "until": {"time": 7, "targets_cleared": True},
         "targets": [{"part": "Egg Sac", "share": 0.3}, {"part": "Egg Sac", "name": "알2", "hp": 5}]},
    ]}
    before = copy.deepcopy(raw)
    enemy = build_enemy(raw, presets)
    assert raw == before, "입력을 건드렸다"
    assert enemy["patterns"][0]["spec"] == {"coeff": 20, "target": "top_atk:1", "hits": 6}
    assert enemy["patterns"][1]["targets"] == [{"name": "Egg Sac", "hp": 60832186.59, "share": 0.3},
                                               {"name": "알2", "hp": 5}]
    validate(enemy["patterns"], squad_size=5)
    assert presets["솔로 레이드 S40"]["skills"]["Luxurious Guns"]["spec"]["hits"] == 5, "프리셋을 건드렸다"
    print("검산 3 — skill·part 전개 · 칸 단위 덮어쓰기 · 입력·프리셋 불변")

    # ── 검산 4: 잘못된 참조는 전부 거절한다
    P = "솔로 레이드 S40"
    bad_cases = {
        "모르는 최상위 칸":       {"preset": P, "patern": []},
        "모르는 프리셋":          {"preset": "솔로 레이드 S99"},
        "preset 없이 skill":     {"patterns": [{"kind": "attack", "skill": "Luxurious Guns"}]},
        "preset 없이 part":      {"patterns": [{"kind": "parts", "targets": [{"part": "Egg Sac"}]}]},
        "attack 아닌 skill":     {"preset": P, "patterns": [{"kind": "idle", "skill": "Luxurious Guns"}]},
        "모델 없는 스킬":          {"preset": P, "patterns": [{"kind": "attack", "skill": "Luxurious Mercenaries"}]},
        "모르는 스킬":            {"preset": P, "patterns": [{"kind": "attack", "skill": "Luxurious Gun"}]},
        "모르는 파츠":            {"preset": P, "patterns": [{"kind": "parts", "targets": [{"part": "Egg"}]}]},
        "spec이 dict 아님":      {"preset": P, "patterns": [{"kind": "attack", "skill": "Luxurious Guns",
                                                          "spec": 6}]},
        "dict 아님":             ["preset", P],
    }
    for label, case in bad_cases.items():
        try:
            build_enemy(case, presets)
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: {label}")
    for label, arg in {"없는 파일": "없는_스크립트.json"}.items():
        try:
            load_boss(arg)
        except ValueError:
            continue
        raise AssertionError(f"거절하지 않았다: {label}")
    print(f"검산 4 — 잘못된 참조 {len(bad_cases) + 1}종 전부 거절")
