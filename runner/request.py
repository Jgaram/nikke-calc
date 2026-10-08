"""시뮬 요청 — 다른 프로그램이 이 레포를 평가기로 부를 때의 **입력 정본**.

    python -m runner.request          # 요청 스키마(JSON Schema 2020-12)를 stdout에 낸다

요청 한 건은 JSON 객체 하나다. 모양은 계산기의 층 구조를 그대로 따른다 — 실행(`sim`) · 적(`boss`·`enemy`) ·
스쿼드 단위 조작(`operation`) · 육성(`profile`, 2.5층) · 니케별 운용(`chars`, 3층 호출자 오버라이드).
`chars`의 어휘는 회귀 하네스 스쿼드(`runner/snapshot.py` `SQUADS`의 `chars`)와 같다.

**정본은 `SCHEMA`다.** 형·어휘·범위는 스키마가 보고(`validate_schema`), 스키마로 적을 수 없는 것 — 스쿼드에
있는 이름인가, 컨트롤 어휘(`timeline.validate_control`), 큐브·버스트 패턴 카탈로그 — 은 `check()`가 이어서 본다.
숫자 범위·이름 목록은 손으로 다시 적지 않고 계산기·러너의 정본 상수에서 만든다.

표준을 따른다:
  - 스키마: JSON Schema 2020-12 (`validate_schema`는 이 파일이 쓰는 부분집합만 구현한다 — §지원 키워드)
  - 오류 위치: JSON Pointer (RFC 6901) — `RequestError.path`
  - 배치 공통값 위에 줄을 얹는 병합: JSON Merge Patch (RFC 7386) — `merge_patch`

CLI 옵션(`--tap "프리카:4.0"` 등)은 사람용 표기일 뿐이다 — `runner.sim.args_to_request()`가 이 모양으로 바꾼 뒤
같은 길을 탄다. 형식 설명은 docs/SIM-JSON.md §요청.
"""

from __future__ import annotations

import json
import math
import re
import sys

from calculator import timeline
from runner import spec

VERSION = 2

# ── 스키마 조각 ──────────────────────────────────────────────────────────────


def _int(lo: int | None = None, hi: int | None = None) -> dict:
    s: dict = {"type": "integer"}
    if lo is not None:
        s["minimum"] = lo
    if hi is not None:
        s["maximum"] = hi
    return s


def _num(**kw) -> dict:
    return {"type": "number", **kw}


def _off(s: dict) -> dict:
    """`false` = 그 축을 끈다(아래 층 값 포함). `null`은 RFC 7386대로 「적지 않음」이라 끄는 값이 따로 있어야 한다."""
    return {"anyOf": [s, {"const": False}]}


NAME = {"type": "string", "minLength": 1}
_SQUAD_MAX = 5

# 큐브 — 이름 하나 또는 {name, level}. 레벨 생략 = 아래 층 값. 이름 카탈로그는 `check()`가 본다(`spec.check_cube`)
_CUBE_LV_MAX = max(len(v.get("values", {})) for v in spec.CUBES.values())
CUBE = {"anyOf": [NAME, {"type": "object", "required": ["name"], "additionalProperties": False,
                         "properties": {"name": NAME, "level": _int(1, _CUBE_LV_MAX)}}]}

# 컨트롤 — docs/CONTROL.md §설정 스키마. 키·창·앵커·행위 어휘는 `timeline.validate_control()`이 정본이라
# 여기서는 **형**만 본다(숫자 칸에 문자열이 오면 시뮬 도중에야 터지므로). `false` = 그 축을 끈다(레이어 값 포함).
_CTRL_NUM = ("rate", "release", "full_charge_interval", "lead", "offset", "len", "margin", "extend", "duration")
_CTRL_PART = {"type": "object", "properties": {
    **{k: _num() for k in _CTRL_NUM},
    "priority": {"type": ["string", "integer"]},
    "if_dry": {"type": "boolean"}, "cancel_on_full": {"type": "boolean"},
    "gate": {"type": "object", "required": list(timeline._GATE_KEYS), "additionalProperties": False,
             "properties": {"burst_stage": {"enum": ["1", "2", "3"]}, "burst_user": NAME}},
}}
_CTRL_TYPES = {
    "click": {"type": "array", "items": _CTRL_PART},
    "tap_fire": _CTRL_PART, "hold": _CTRL_PART, "reload": _CTRL_PART, "cover": _CTRL_PART,
    "burst": {"type": "object", "additionalProperties": False, "properties": {
        # 패턴 이름(data/char_defaults.json `_burst_patterns`) 또는 사이클 목록(1부터)
        "pattern": _off({"anyOf": [NAME, {"type": "array", "items": _int(1)}]}),
        "delay": _num(minimum=0),
    }},
    "sequence": {"type": "array"},
    "priority": {"type": ["string", "integer"]},
    "aim": {"type": "array", "items": _CTRL_PART},
}
assert set(_CTRL_TYPES) == set(timeline._CONTROL_KEYS), "컨트롤 키가 늘었다 — _CTRL_TYPES에 형을 적는다"
CONTROL = {"type": "object", "additionalProperties": False,
           "properties": {k: _off(v) for k, v in _CTRL_TYPES.items()}}

# 니케별 운용(3층) — 하네스 `SQUADS[...]["chars"]`와 같은 어휘
CHAR = {"type": "object", "additionalProperties": False, "properties": {
    "control": CONTROL,
    "cube": CUBE,
    "weapon_mode_swap": {"type": "boolean"},
    "favorite_stage": _int(*spec._RANGES["favorite_stage"]),
}}

# 육성(2.5층) — docs/SIM-JSON.md §육성. 표기를 펴는 일(`overload` 이름 매칭·줄 수)은 `spec.normalize_growth`가 한다
_LV_MAX = max(int(k) for t in spec.LEVEL_TABLE.values() for k in t)
_SKILL = _int(1, spec.SKILL_LV_MAX)
_EQUIP_KEYS = sorted(k for k in spec._EQUIP_SKILL_TABLE if not k.startswith("_"))
_OL_LV = _int(1, max(len(spec._EQUIP_SKILL_TABLE[k]["values"]) for k in _EQUIP_KEYS))
_CONSOLE_LV = {"anyOf": [_int(0), {"type": "object", "additionalProperties": _int(0)}]}
CONSOLE = {"type": "object", "additionalProperties": False, "properties": {   # 적지 않은 칸은 아래 층 값
    "common_level": _int(0), "class_level": _CONSOLE_LV, "company_level": _CONSOLE_LV}}
_GEAR = {"type": "object", "additionalProperties": False, "properties": {
    "tier": {"enum": [spec.NO_ITEM, "기업", *spec.GEAR_TIERS]},
    "corp": NAME,
    "level": _int(0, spec.GEAR_LEVEL_MAX),
    "skills": {"type": "array", "items": {"type": "object", "required": ["id", "lv"], "additionalProperties": False,
                                          "properties": {"id": {"enum": _EQUIP_KEYS}, "lv": _OL_LV}}},
}}
GROWTH = {"type": "object", "additionalProperties": False, "patternProperties": {"^_": {}}, "properties": {
    "level": _int(1, _LV_MAX),
    **{k: _int(lo, hi) for k, (lo, hi) in spec._RANGES.items()},
    "skill_levels": {"anyOf": [
        _SKILL,
        {"type": "string", "pattern": r"^\s*\d+\s*/\s*\d+\s*/\s*\d+\s*$"},
        {"type": "array", "items": _SKILL, "minItems": 3, "maxItems": 3},
        {"type": "object", "additionalProperties": False, "properties": {s: _SKILL for s in ("1", "2", "3")}},
    ]},
    "overload": {"type": "object", "additionalProperties": {"anyOf": [_int(0), {"type": "array", "items": _OL_LV}]}},
    "equip_skills": {"type": "object", "additionalProperties": False, "properties": {
        k: {"anyOf": [_num(minimum=0), {"type": "array", "items": _num(minimum=0)}]} for k in _EQUIP_KEYS}},
    "equipment": {"type": "object", "additionalProperties": False,
                  "properties": {p: _GEAR for p in ("머리", "몸통", "팔", "다리")}},
    "collection_stage": {"enum": [spec.NO_ITEM, *sorted(spec.COLLECTION_STAGES)]},
    "console": CONSOLE,
    "cube": CUBE,
}}
assert set(GROWTH["properties"]) == spec.GROWTH_KEYS | {spec.OVERLOAD_KEY}, "육성 키가 늘었다 — GROWTH에 형을 적는다"
PROFILE = {"anyOf": [
    {"type": "string", "minLength": 1, "description": "profiles/<이름>.json — profile-sync가 만든 내 계정 프로필"},
    {"type": "object", "additionalProperties": False, "properties": {
        "base": {"enum": list(spec.PROFILE_BASES)},
        "chars": {"type": "object", "additionalProperties": GROWTH},
        "_meta": {"type": "object", "properties": {"name": {"type": "string"}}},
        "_account": {"type": "object", "additionalProperties": False, "properties": {
            "console": CONSOLE, "synchro_level": _int(1, _LV_MAX),
            "cubes": {"type": "object", "additionalProperties": _int(1, _CUBE_LV_MAX)}}},
    }},
]}

_TACTIC = {"type": "object", "required": ["name"], "additionalProperties": False,
           "properties": {"name": {"enum": sorted(spec.TACTICS)}, "target": NAME}}

SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/Jgaram/nikke-calc/runner/request.py",
    "title": "nikke-calc 시뮬 요청",
    "description": "docs/SIM-JSON.md §요청. 적지 않은 칸은 기본값(기본 스펙·기본 적·180초)이다. 객체 안의 null은 "
                   "RFC 7386대로 그 칸을 적지 않은 것과 같다 — 이 스키마는 null을 걷어 낸 뒤의 모양이다.",
    "type": "object",
    "required": ["squad"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": VERSION},
        "id": {"description": "배치 결과 줄에 그대로 돌려준다 (아무 JSON 값)"},
        "squad": {"type": "array", "items": NAME, "minItems": 1, "maxItems": _SQUAD_MAX,
                  "description": "정식 명칭(docs/ALIASES.md), 배치 순서. 같은 니케를 두 번 적어도 된다"},
        "sim": {"type": "object", "additionalProperties": False, "properties": {
            "duration": _num(exclusiveMinimum=0),
            "expected": {"type": "boolean"},
            "seed": _int(),
            "first_burst": _num(minimum=0),
            "burst_gauge_mode": {"enum": ["fixed", "accumulate"]},
            "allow_unparsed": {"type": "boolean"},
        }},
        "boss": {"anyOf": [
            {"type": "string", "minLength": 1, "description": "data/boss_presets.json 프리셋 이름, 또는 .json 스크립트 경로"},
            {"type": "object", "description": "인라인 보스 스크립트 — 형식은 runner/boss.py 모듈 docstring"},
        ]},
        "enemy": {"type": "object", "additionalProperties": False, "properties": {
            "def": _int(0),
            "code": {"enum": ["풍압", "수냉", "작열", "전격", "철갑"]},
            "core_px": _num(minimum=0),
            "distance": _num(minimum=0),
            "has_parts": {"type": "boolean"},
            "part_break_interval": _num(minimum=0),
            "optimal_range_weapons": {"type": "array", "items": {"enum": ["AR", "SR", "SMG", "SG", "MG", "RL"]}},
        }},
        "operation": {"type": "object", "additionalProperties": False, "properties": {
            "camera": {"anyOf": [{"type": "string", "description": "\"\" = 아무도 안 본다"},
                                 {"type": "array", "items": NAME}]},
            "camera_mode": {"enum": ["single", "shared"]},
            "control_mode": {"enum": list(timeline._CTRL_MODES)},
            "no_burst": NAME,
            "auto": {"anyOf": [{"type": "boolean"}, {"type": "array", "items": NAME}]},
            "tactics": {"type": "array", "items": _TACTIC},
        }},
        "profile": PROFILE,
        "profile_level": {"enum": list(spec.LEVEL_MODES)},
        "chars": {"type": "object", "additionalProperties": CHAR},
    },
}


# ── 검사 ─────────────────────────────────────────────────────────────────────

class RequestError(ValueError):
    """요청이 잘못됐다. `path`는 잘못된 칸의 JSON Pointer(RFC 6901) — 최상위 자체면 `""`."""

    def __init__(self, path: str, message: str):
        super().__init__(f"{path or '/'}: {message}")
        self.path = path
        self.detail = message


def pointer(*parts) -> str:
    """경로 조각 → JSON Pointer. `~`·`/`는 RFC 6901대로 escape한다."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


# 요청 v1(배치 키 = CLI 옵션 이름)에서 옮겨 온 키 — 모르는 키 오류에 새 자리를 알려 준다
_V1_KEYS = {
    **{k: f"/sim/{k.replace('-', '_')}" for k in ("expected", "seed", "duration", "burst-gauge-mode", "allow-unparsed")},
    "first-burst": "/sim/first_burst",
    **{k: f"/operation/{k.replace('-', '_')}" for k in ("camera", "camera-mode", "control-mode", "no-burst", "auto")},
    "tactic": "/operation/tactics",
    "enemy-def": "/enemy/def", "enemy-code": "/enemy/code", "core-px": "/enemy/core_px",
    "distance": "/enemy/distance", "has-parts": "/enemy/has_parts", "part-break-interval": "/enemy/part_break_interval",
    **{k: "/chars/<니케>/control" for k in ("controls", "tap", "click", "reload-ctrl", "cover-ctrl", "hold-ctrl",
                                             "aim", "cancel-on-full", "burst-delay", "burst-pattern")},
    "cube": "/chars/<니케>/cube", "mode-swap": "/chars/<니케>/weapon_mode_swap",
    "favorite": "/chars/<니케>/favorite_stage", "profile-level": "/profile_level",
}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _shape(s: dict) -> str:
    """스키마 하나를 사람이 읽는 짧은 말로 — anyOf 실패 메시지용."""
    if "enum" in s:
        return " · ".join(json.dumps(x, ensure_ascii=False) for x in s["enum"])
    if "anyOf" in s:
        return " | ".join(_shape(x) for x in s["anyOf"])
    t = s.get("type", "아무 값")
    return " | ".join(t) if isinstance(t, list) else t


def validate_schema(value, schema: dict = SCHEMA, path: str = "") -> None:
    """JSON Schema 부분집합으로 검사한다. 어긋나면 첫 위반을 `RequestError`로.

    §지원 키워드: type · enum · const · minimum · maximum · exclusiveMinimum · minLength · pattern ·
    items · minItems · maxItems · properties · required · additionalProperties · patternProperties · anyOf.
    `integer`는 파이썬 int만 받는다(`3.0`은 거절) — 계산기가 정수 칸을 int로 본다.
    """
    if "anyOf" in schema:
        errs = []
        for sub in schema["anyOf"]:
            try:
                validate_schema(value, sub, path)
                break
            except RequestError as e:
                errs.append(e)
        else:
            # 형은 맞았는데 안쪽이 틀린 갈래가 하나면 그 오류가 더 정확하다
            deep = [e for e in errs if e.path != path]
            if len(deep) == 1:
                raise deep[0]
            raise RequestError(path, f"{_shape(schema)} 중 하나여야 한다 (받은 값 {_brief(value)})")
        return
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            raise RequestError(path, f"{' | '.join(types)}여야 한다 (받은 값 {_brief(value)})")
    if "const" in schema and value != schema["const"]:
        raise RequestError(path, f"{schema['const']!r}여야 한다 (받은 값 {_brief(value)})")
    if "enum" in schema and value not in schema["enum"]:
        raise RequestError(path, f"{_shape(schema)} 중 하나여야 한다 (받은 값 {_brief(value)})")
    if _TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            raise RequestError(path, f"{schema['minimum']} 이상이어야 한다 (받은 값 {value})")
        if "maximum" in schema and value > schema["maximum"]:
            raise RequestError(path, f"{schema['maximum']} 이하여야 한다 (받은 값 {value})")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise RequestError(path, f"{schema['exclusiveMinimum']}보다 커야 한다 (받은 값 {value})")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise RequestError(path, "빈 문자열이다")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise RequestError(path, f"형식이 맞지 않다 (받은 값 {value!r})")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            lo, hi = schema.get("minItems", 0), schema.get("maxItems", "∞")
            raise RequestError(path, f"항목 {lo}~{hi}개여야 한다 (받은 것 {len(value)}개)")
        if "items" in schema:
            for i, v in enumerate(value):
                validate_schema(v, schema["items"], path + pointer(i))
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                raise RequestError(path, f"{k!r} 칸이 필요하다")
        props, pats = schema.get("properties", {}), schema.get("patternProperties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            sub = props.get(k)
            if sub is None:
                sub = next((s for p, s in pats.items() if re.search(p, k)), None)
            if sub is None:
                if extra is False:
                    raise RequestError(path + pointer(k), _unknown_key(k, props, path))
                sub = extra if isinstance(extra, dict) else {}
            validate_schema(v, sub, path + pointer(k))


def _brief(v) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 60 else s[:57] + "…"


def _unknown_key(k: str, props: dict, path: str) -> str:
    msg = f"모르는 키 {k!r}. 받는 키: {', '.join(props) or '없음'}"
    if path == "" and (moved := _V1_KEYS.get(k.replace("_", "-"))):
        msg += f" — 요청 v1의 키다. v2에서는 {moved}"
    return msg


def check(req: dict) -> None:
    """스키마 뒤의 의미 검사 — 이름 참조·컨트롤 어휘·카탈로그. 어긋나면 `RequestError`."""
    squad = req["squad"]
    known = spec._nikke()
    for i, n in enumerate(squad):
        if n not in known:
            raise RequestError(pointer("squad", i), f"모르는 니케 {n!r} — 정식 명칭만 받는다 (docs/ALIASES.md)")
    members = set(squad)

    def member(name, *where: object) -> None:
        if name not in members:
            raise RequestError(pointer(*where), f"{name!r}는 스쿼드에 없다 — 스쿼드: {squad}")

    op = req.get("operation") or {}
    if op.get("no_burst") is not None:
        member(op["no_burst"], "operation", "no_burst")
    cam = op.get("camera")
    if isinstance(cam, str) and cam:
        member(cam, "operation", "camera")
    elif isinstance(cam, list):
        for i, n in enumerate(cam):
            member(n, "operation", "camera", i)
    if isinstance(op.get("auto"), list):
        for i, n in enumerate(op["auto"]):
            member(n, "operation", "auto", i)
    for i, t in enumerate(op.get("tactics") or []):
        if "target" in t:
            member(t["target"], "operation", "tactics", i, "target")

    if req.get("profile_level", "fixed") != "fixed" and req.get("profile") is None:
        raise RequestError("/profile_level", "profile과 함께만 의미가 있다")

    for n, entry in (req.get("chars") or {}).items():
        member(n, "chars", n)
        if "cube" in entry:
            try:
                spec.check_cube(spec.cube_dict(entry["cube"]), n, need_level=False)
            except ValueError as e:
                raise RequestError(pointer("chars", n, "cube"), str(e)) from None
        ctrl = entry.get("control")
        if ctrl is None:
            continue
        try:
            timeline.validate_control({k: v for k, v in ctrl.items() if v is not False}, n)
        except ValueError as e:
            raise RequestError(pointer("chars", n, "control"), str(e)) from None
        pat = (ctrl.get("burst") or {}).get("pattern")
        if isinstance(pat, str):
            try:
                spec.burst_pattern_of(n, pat)
            except SystemExit as e:
                raise RequestError(pointer("chars", n, "control", "burst", "pattern"), str(e.code)) from None
        for axis in ("click", "reload"):
            items = ctrl.get(axis)
            for i, item in (enumerate(items) if isinstance(items, list) else [(None, items)]):
                gate = (item or {}).get("gate")
                if gate:
                    where = ("chars", n, "control", axis) + ((i,) if i is not None else ()) + ("gate", "burst_user")
                    member(gate["burst_user"], *where)


def validate(req) -> dict:
    """요청 하나를 끝까지 검사한다 — null 걷기 → 스키마 → 의미. 통과하면 걷어 낸 요청을 돌려준다."""
    if isinstance(req, dict):
        req = merge_patch({}, req)      # 객체 안의 null = 적지 않음 (RFC 7386)
    validate_schema(req)
    check(req)
    return req


# ── 병합 · 읽기 ──────────────────────────────────────────────────────────────

def merge_patch(target, patch):
    """JSON Merge Patch (RFC 7386). 객체는 재귀 병합, `null`은 그 칸을 지운다(= 기본값), 나머지는 교체."""
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


def _no_dup(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    if dup := sorted({k for k in keys if keys.count(k) > 1}):
        raise ValueError(f"같은 키를 두 번 적었다: {dup}")
    return dict(pairs)


def loads(text: str, what: str = "요청"):
    """JSON 문자열 → 값. 한 객체 안의 중복 키는 거절한다(뒤 값이 조용히 이기지 않게)."""
    try:
        return json.loads(text, object_pairs_hook=_no_dup)
    except json.JSONDecodeError as e:
        raise RequestError("", f"{what}을 JSON으로 읽지 못했다: {e}") from None
    except ValueError as e:
        raise RequestError("", f"{what}: {e}") from None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(SCHEMA, ensure_ascii=False, indent=2))
