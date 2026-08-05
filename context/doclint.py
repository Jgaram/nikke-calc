"""문서 정합 린터 (Claude 전용 유지보수 도구).

문서가 **코드·데이터를 재서술한 부분**만 기계로 검사한다. calculator 로직 검사가
아니라 문서 관리 도구다.

검사하는 것 = "코드/데이터를 보면 답이 나오는데 문서에도 적혀 있는 것"(이중 진실).
검사하지 않는 것 = 게임 메커니즘 명세(GAMEPLAY·DATA_VERIFY·CONTROL)와 결정·이력
기록(HARNESS 운영 규칙·PARSING 매핑 규칙). 이쪽은 코드가 하류라 대조할 원본이 없다.

검사 항목:
  A. parsed_skills.json에 쓰인 모든 키(stat/timing/condition/target)가 IMPL-STATUS
     마스터 테이블에 존재하는가 (미등록 키 = 문서 누락)
  B. PARSING-CHARS.md '현황 목록 § 완료' ↔ parsed_skills.json 캐릭터 키 일치 (유령/누락 항목)
  C. IMPL-STATUS 마스터의 구현 상태(✅⚠️❌🚫) ↔ calculator/*.py 실제 흔적
     (✅인데 코드에 없음 / ❌인데 코드에 있음). 텍스트 휴리스틱이라 STATUS_EXEMPT 예외 있음
  D. '사본'이라고 선언된 표 ↔ 정본 표의 수치 일치 (MIRRORS 등록분)
  E. context/*.md · .claude/skills/*/*.md가 백틱으로 지목한 `파일.py/json` · `함수()`가 실재하는가

키 매칭은 첫 콜론 이전 prefix 기준 (예: `hit_count:다탄두:3` ↔ 문서 `hit_count:N`).

사용:
  python -m context.doclint            # 정합 검사. 불일치 시 exit 1
  python -m context.doclint --usage    # + 키별 사용 캐릭터 수 (one-off 식별용)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "data" / "parsed_skills.json"
NIKKE = ROOT / "data" / "parsed_nikke.json"
IMPL = ROOT / "context" / "IMPL-STATUS.md"
CHARS = ROOT / "context" / "PARSING-CHARS.md"  # 현황 목록(완료/예정) 정본
CALC = ROOT / "calculator"
GAMEPLAY = ROOT / "context" / "GAMEPLAY.md"
HARNESS = ROOT / "context" / "HARNESS.md"

_BACKTICK = re.compile(r"`([^`]+)`")
_PAREN = re.compile(r"[(（][^)）]*[)）]")
_NUM = re.compile(r"\d+(?:\.\d+)?")
STATUS_MARKS = ("✅", "⚠️", "❌", "🚫")
DONE_MARKS = ("✅", "⚠️")  # 구현됐다고 주장하는 표기

# ── 검사 C 예외 ────────────────────────────────────────────────────────────
# 코드에 키 문자열이 그대로 나타나지 않지만 구현된 키(또는 그 반대). 값은 **사유**다.
# 사유 없이 등록하지 않는다 — 사유 없는 예외는 검사를 조용히 무력화한다.
# 키 끝의 `*`는 prefix 매칭.
STATUS_EXEMPT: dict[str, str] = {
    "enemies_*": "단일 적 시뮬이라 `_resolve_target()`의 `startswith(\"enemies\")` 일반 "
                 "분기가 전부 `__enemy__` 센티널로 처리한다. 개별 키 리터럴이 없다",
    "target_and_nearby": "위 `enemies` 일반 분기와 같은 센티널 경로",
    "[캐릭터명]": "target 값이 스쿼드 이름 리터럴일 때의 패턴 표기. 코드에는 "
                  "`target in squad_names` 형태로만 존재한다",
    "effect_interval": "`_dispatch_instant()` 내부에서 `target_effect`와 함께 처리. "
                       "stat 문자열을 직접 조회하지 않는다",
    "gauge_charge_enabled": "buff로 등록만 되고 게이지 로직이 `gauge_id`로 동작한다",
    "auto_damage": "파서 단계에서 `is_normal_atk`/`damage_formula`로 번역된다. "
                   "계산기는 원래 stat 이름을 보지 않는다",
    "event": "`timing == event` 표기용 일반 명사라 코드 전역에 등장한다. 텍스트 대조 불가",
    "all_projectiles": "미지원 처리(early return)가 키 리터럴을 쓴다. 코드에 있어도 미구현이 맞다",
    "armor_break_enabled": "소비측만 있다 — timeline이 `buffs.get(\"armor_break_enabled\")`로 "
                           "읽지만 buff_manager에 등록(`_STAT_TO_BUFF` 또는 boolean 플래그 분기)이 "
                           "없어 buffs에 절대 들어가지 않는다. 항상 False다. ❌가 맞다",
}

# ── 검사 D: 선언된 사본 ↔ 정본 ─────────────────────────────────────────────
# 사본을 새로 둘 때는 문서에 "이 표는 사본이다. 정본은 X" 선언을 붙이고 여기 등록한다.
MIRRORS = [
    {
        "name": "사이클 간격 패턴",
        "copy": (HARNESS, "| 구성 | 정상 간격열 |"),
        "source": (GAMEPLAY, "#### 사이클 간격 패턴"),
    },
]

# ── 검사 E 예외 ────────────────────────────────────────────────────────────
# 로컬에 없는 게 정상인 이름. 값은 사유.
# 취소선(~~...~~) 안의 이름은 과거 기록이므로 자동으로 제외된다 — 여기 등록할 필요 없다.
REF_EXEMPT: dict[str, str] = {
    "character_id_map.json": "CDN 원격 경로 (`scraper/cdn_fetch.py` ID_MAP_PATH). 로컬 파일 아님",
    "favorite_rare_map.json": "CDN 원격 경로 (FAVORITE_RARE_MAP_PATH). 로컬 파일 아님",
    "unparsed_skills.json": "`_unparseable`이 나올 때만 생기는 예정 파일. 지금 없는 게 정상",
    "_make_cube_effect": "XLCALC.md 이력 항목이 기술하는 **개명 전** 이름. 현재는 "
                         "`_make_cube_effects()`. 이력은 당시 이름으로 남는 게 맞다",
}
# 검사 E가 훑는 문서 (명세·이력 문서도 코드 이름을 지목하면 대상).
# 스킬 폴더(`.claude/skills/<name>/`)의 문서도 대상 — 한 스킬에서만 쓰는 문서는
# context/가 아니라 그쪽에 두므로, 여기서 빼면 그만큼 검사 사각지대가 된다.
REF_DOCS = sorted((ROOT / "context").glob("*.md")) + sorted((ROOT / ".claude" / "skills").glob("*/*.md"))
REF_SRC_GLOBS = ("calculator/*.py", "ui/*.py", "scraper/*.py", "context/*.py",
                 ".claude/skills/*/*.py", "app.py")


def prefix(key: str) -> str:
    """키를 첫 콜론 이전 prefix로 정규화. 따옴표·공백 제거."""
    key = key.strip().strip('"').strip("'").strip()
    return key.split(":", 1)[0]


# ── 실데이터: parsed_skills.json에서 실제 사용된 키 수집 ──────────────────

def load_used() -> tuple[dict[str, dict[str, set[str]]], list[str]]:
    """반환: used[category][prefix] = {그 키를 쓴 캐릭터 집합}, 캐릭터 목록."""
    data = json.loads(SKILLS.read_text(encoding="utf-8"))
    used: dict[str, dict[str, set[str]]] = {
        c: defaultdict(set) for c in ("stat", "timing", "condition", "target")
    }
    chars = [c for c in data if not c.startswith("test_")]
    for char in chars:
        for eff in data[char]:
            if isinstance(eff.get("stat"), str):
                used["stat"][prefix(eff["stat"])].add(char)
            tgt = eff.get("target")
            if isinstance(tgt, str):
                used["target"][prefix(tgt)].add(char)
            trig = eff.get("trigger", {})
            for t in trig.get("timing", []) or []:
                if isinstance(t, str):
                    used["timing"][prefix(t)].add(char)
            for c in trig.get("condition", []) or []:
                if isinstance(c, str):
                    used["condition"][prefix(c)].add(char)
    return used, chars


# ── 문서: IMPL-STATUS 마스터 테이블 파싱 ───────────────────────────────────

def _master_rows() -> list[tuple[str, str, str]]:
    """마스터 테이블의 (카테고리, 키 prefix, 구현상태 기호) 행 목록.

    구현 상태 컬럼은 헤더에서 위치를 찾는다 (target 테이블처럼 앞에 다른 기호
    컬럼이 있어도 안전하게).
    """
    rows: list[tuple[str, str, str]] = []
    cat: str | None = None
    status_col: int | None = None
    for line in IMPL.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("## stat 마스터 테이블"):
            cat, status_col = "stat", None
        elif s.startswith("## trigger/condition 마스터 테이블"):
            cat, status_col = None, None  # ### timing / ### condition을 기다림
        elif s == "### timing":
            cat, status_col = "timing", None
        elif s == "### condition":
            cat, status_col = "condition", None
        elif s.startswith("## target 마스터 테이블"):
            cat, status_col = "target", None
        elif s.startswith("## "):  # 그 외 상위 섹션(빠른 참조 등)은 수집 중단
            cat, status_col = None, None
        if not (cat and s.startswith("|")):
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if not cells:
            continue
        if "구현 상태" in cells:            # 헤더 행
            status_col = cells.index("구현 상태")
            continue
        if set(s) <= set("|-: "):           # 구분선
            continue
        if status_col is None or status_col >= len(cells):
            continue
        mark = next((m for m in STATUS_MARKS if cells[status_col].startswith(m)), None)
        if not mark:
            continue
        for tok in _BACKTICK.findall(cells[0]):   # 한 행에 키가 여러 개일 수 있다
            rows.append((cat, prefix(tok), mark))
    return rows


def load_documented() -> dict[str, set[str]]:
    """카테고리별 문서 등록 키 prefix 집합."""
    doc: dict[str, set[str]] = {c: set() for c in ("stat", "timing", "condition", "target")}
    for cat, key, _mark in _master_rows():
        doc[cat].add(key)
    return doc


# ── 문서: PARSING-CHARS.md 현황 목록 § 완료 ──────────────────────────────

def load_roster_done() -> list[str]:
    names: list[str] = []
    in_done = False
    for line in CHARS.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("### 완료"):
            in_done = True
            continue
        if in_done:
            if s.startswith("#"):  # 다음 소절(### 진행 중 등)에서 종료
                break
            if s:
                names.append(s)
    return names


# ── 검사 C: 구현 상태 ↔ 코드 ───────────────────────────────────────────────

def _exempt_reason(key: str) -> str | None:
    for pat, why in STATUS_EXEMPT.items():
        if pat.endswith("*") and key.startswith(pat[:-1]):
            return why
        if key == pat:
            return why
    return None


def check_status(verbose: bool = False) -> bool:
    """반환: 불일치 있으면 True."""
    code = "\n".join(p.read_text(encoding="utf-8") for p in sorted(CALC.glob("*.py")))

    # 같은 키가 카테고리마다 다른 상태로 등록될 수 있다 (예: core_hit_count는
    # timing ✅ / condition ❌). 코드는 한 덩어리라 카테고리별 대조가 불가능하므로,
    # "어느 한 곳이라도 구현됐다고 적혀 있으면 코드에 흔적이 있어야 한다"로 본다.
    claimed: dict[str, bool] = {}
    where: dict[str, list[str]] = defaultdict(list)
    for cat, key, mark in _master_rows():
        claimed[key] = claimed.get(key, False) or (mark in DONE_MARKS)
        where[key].append(f"{cat}:{mark}")

    bad: list[str] = []
    for key, is_claimed in sorted(claimed.items()):
        if _exempt_reason(key):
            continue
        present = re.search(rf"(?<!\w){re.escape(key)}(?!\w)", code) is not None
        if is_claimed and not present:
            bad.append(f"  구현 표기인데 코드에 흔적 없음  {key}  ({', '.join(where[key])})")
        elif not is_claimed and present:
            bad.append(f"  미구현 표기인데 코드에 흔적 있음  {key}  ({', '.join(where[key])})")

    print("\n=== C. 구현 상태 정합 (IMPL-STATUS 마스터 ↔ calculator/*.py) ===")
    if bad:
        print("\n".join(bad))
        print("  → 문서가 낡았거나, 코드 흔적이 실제 구현이 아니다. 후자면 "
              "STATUS_EXEMPT에 사유와 함께 등록한다.")
    else:
        print(f"  (일치 — 키 {len(claimed)}종, 예외 {len(STATUS_EXEMPT)}건)")
    if verbose:
        for pat, why in STATUS_EXEMPT.items():
            print(f"    예외 {pat}: {why}")
    return bool(bad)


# ── 검사 D: 선언된 사본 ↔ 정본 ─────────────────────────────────────────────

def _table_after(path: Path, anchor: str) -> list[list[str]]:
    """앵커 이후 첫 마크다운 표의 데이터 행(셀 리스트)을 반환."""
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if anchor in l)
    except StopIteration:
        return []
    rows: list[list[str]] = []
    seen_table = False
    for line in lines[start:]:
        s = line.strip()
        if s.startswith("|"):
            seen_table = True
            if set(s) <= set("|-: "):
                continue
            rows.append([c.strip() for c in s.split("|")[1:-1]])
        elif seen_table and not s.startswith("|"):
            break
    return rows[1:] if rows else []   # 첫 행은 헤더


def _mirror_map(rows: list[list[str]]) -> dict[str, tuple[str, ...]]:
    """{정규화 라벨: 나머지 셀에서 뽑은 수치 튜플}. 라벨의 괄호 보충설명은 무시."""
    out: dict[str, tuple[str, ...]] = {}
    for cells in rows:
        if not cells:
            continue
        label = _PAREN.sub("", cells[0]).replace("`", "").replace("*", "")
        label = re.sub(r"\s+", " ", label).strip()
        nums = tuple(_NUM.findall(" ".join(cells[1:])))
        if label:
            out[label] = nums
    return out


def check_mirrors() -> bool:
    """반환: 불일치 있으면 True."""
    print("\n=== D. 선언된 사본 ↔ 정본 ===")
    fail = False
    for m in MIRRORS:
        cp, cp_anchor = m["copy"]
        sp, sp_anchor = m["source"]
        copy_map = _mirror_map(_table_after(cp, cp_anchor))
        src_map = _mirror_map(_table_after(sp, sp_anchor))
        if not copy_map or not src_map:
            fail = True
            print(f"  [{m['name']}] 표를 못 찾음 "
                  f"(사본 {len(copy_map)}행 / 정본 {len(src_map)}행). 앵커 확인 필요")
            continue
        only_copy = sorted(set(copy_map) - set(src_map))
        only_src = sorted(set(src_map) - set(copy_map))
        diff = [k for k in set(copy_map) & set(src_map) if copy_map[k] != src_map[k]]
        if only_copy or only_src or diff:
            fail = True
            print(f"  [{m['name']}] {cp.name} ↔ {sp.name}")
            for k in only_copy:
                print(f"    사본에만 있는 행: {k}")
            for k in only_src:
                print(f"    정본에만 있는 행: {k}")
            for k in sorted(diff):
                print(f"    수치 불일치 {k}: 사본 {copy_map[k]} / 정본 {src_map[k]}")
        else:
            print(f"  [{m['name']}] 일치 ({len(copy_map)}행)")
    return fail


# ── 검사 E: 문서가 지목한 파일·함수가 실재하는가 ──────────────────────────

_STRIKE = re.compile(r"~~.*?~~", re.S)
_REF_FILE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*\.(?:py|json|xlsx))`")
_REF_FUNC = re.compile(r"`([a-z_][a-zA-Z0-9_]*)\(\)`")


def check_refs() -> bool:
    """반환: 불일치 있으면 True."""
    src = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for g in REF_SRC_GLOBS for p in sorted(ROOT.glob(g))
    )
    print("\n=== E. 문서가 지목한 파일·함수 실재 여부 (context/*.md · skills) ===")
    fail = False
    for md in REF_DOCS:
        doc = _STRIKE.sub("", md.read_text(encoding="utf-8"))  # 과거 이름 기록은 제외
        bad: list[str] = []
        for name in sorted(set(_REF_FILE.findall(doc))):
            if name in REF_EXEMPT or list(ROOT.glob("**/" + name)):
                continue
            bad.append(f"파일 {name}")
        for name in sorted(set(_REF_FUNC.findall(doc))):
            if name in REF_EXEMPT or re.search(rf"(?<!\w){re.escape(name)}(?!\w)", src):
                continue
            bad.append(f"함수 {name}()")
        if bad:
            fail = True
            print(f"  [{md.relative_to(ROOT).as_posix()}] " + " · ".join(bad))
    if not fail:
        print(f"  (일치 — 문서 {len(REF_DOCS)}개, 예외 {len(REF_EXEMPT)}건)")
    else:
        print("  → 이름이 바뀌었으면 문서를 고치고, 원격 경로·예정 파일이면 "
              "REF_EXEMPT에 사유와 함께 등록한다.")
    return fail


def main() -> int:
    used, chars = load_used()
    doc = load_documented()
    done = load_roster_done()
    verbose = "--usage" in sys.argv

    fail = False

    # 하드코딩 캐릭터명 target은 마스터의 `[캐릭터명]` 패턴에 해당 → 미등록으로 보지 않음
    nikke_names = set(json.loads(NIKKE.read_text(encoding="utf-8")))

    # 검사 A: 미등록 키
    print("=== A. 미등록 키 (parsed_skills.json에 있으나 IMPL-STATUS 마스터에 없음) ===")
    any_unknown = False
    for cat in ("stat", "timing", "condition", "target"):
        unknown = sorted(
            k for k in used[cat]
            if k not in doc[cat] and not (cat == "target" and k in nikke_names)
        )
        if unknown:
            fail = any_unknown = True
            for k in unknown:
                owners = sorted(used[cat][k])
                print(f"  [{cat}] {k}  ← {', '.join(owners)}")
    if not any_unknown:
        print("  (없음)")

    # 검사 B: 로스터 정합
    print("\n=== B. 로스터 정합 (PARSING-CHARS 현황§완료 ↔ parsed_skills.json) ===")
    done_set, char_set = set(done), set(chars)
    phantom = sorted(done_set - char_set)   # 완료 목록엔 있으나 JSON엔 없음
    missing = sorted(char_set - done_set)   # JSON엔 있으나 완료 목록엔 없음
    if phantom:
        fail = True
        print("  유령(완료 목록엔 있으나 JSON 없음):", ", ".join(phantom))
    if missing:
        fail = True
        print("  누락(JSON엔 있으나 완료 목록 없음):", ", ".join(missing))
    if not phantom and not missing:
        print("  (일치)")

    # 검사 C·D·E
    fail |= check_status(verbose)
    fail |= check_mirrors()
    fail |= check_refs()

    if verbose:
        print("\n=== 키별 사용 캐릭터 수 (one-off = 1명 전용) ===")
        for cat in ("stat", "timing", "condition", "target"):
            items = sorted(used[cat].items(), key=lambda kv: (-len(kv[1]), kv[0]))
            one_off = [k for k, v in items if len(v) == 1]
            print(f"\n[{cat}] 총 {len(items)}종 / one-off {len(one_off)}종")
            for k, v in items:
                mark = "  ← one-off: " + next(iter(v)) if len(v) == 1 else ""
                print(f"  {len(v):2d}  {k}{mark}")

    print(f"\n캐릭터 {len(chars)}명 · 완료목록 {len(done)}명 · "
          f"결과: {'FAIL' if fail else 'OK'}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
