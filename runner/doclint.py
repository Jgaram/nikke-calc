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
  E. docs/*.md · .agent/skills/*/*.md가 백틱으로 지목한 `파일.py/json` · `함수()`가 실재하는가
  I. 같은 문서(+README·AGENTS)가 지목한 **경로**(`a/b.md`, `](a/b)`)가 실재하는가.
     E가 파일명만 보므로 디렉터리 이동은 E로 안 잡힌다 — 그 사각지대를 메운다
  F. ALIASES 정식 명칭 실재 · 원본/이격 주의 목록
  G. 프리뷰(출시 전 카드 파싱) 항목의 수명 — 출시됐는데 정식 등록 안 된 상태를 막는다
  H. 애장품 캐릭터의 스킬 판본 완비 여부 (실패로 잡지 않는 진행 상황 목록)
  J. 중첩형 지속 대미지(`max_stack > 1` + `dot_damage`)에 `scaling: stack_count`가 있는가.
     빠지면 중첩이 쌓여도 틱 대미지가 1중첩에 머문다 — 로그에 흔적이 없는 조용한 오류다
  L. 부착 규칙(`char_defaults.json`·`tactics.json`의 `_rules`)의 `when`·`apply` 어휘가
     `spec.WHEN_KEYS`·`spec.APPLY_KEYS` 안에 있는가, `tactic` 라벨이 실재하는가.
     스키마를 좁고 닫힌 채로 두는 강제 장치다 (docs/CONTROL.md §부착)

키 매칭은 첫 콜론 이전 prefix 기준 (예: `hit_count:다탄두:3` ↔ 문서 `hit_count:N`).

사용:
  python -m runner.doclint            # 정합 검사. 불일치 시 exit 1
  python -m runner.doclint --usage    # + 키별 사용 캐릭터 수 (one-off 식별용)
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from calculator.buff_manager import WEAPON_CHANGE_STATE

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "data" / "parsed_skills.json"
NIKKE = ROOT / "data" / "parsed_nikke.json"
IMPL = ROOT / "docs" / "IMPL-STATUS.md"
CHARS = ROOT / "docs" / "PARSING-CHARS.md"  # 현황 목록(완료/프리뷰/예정) 정본
PREVIEW = ROOT / "scraper" / "preview_skills.json"  # 출시 전 카드 전사본
SCRAPED = ROOT / "scraper" / "nikke_scraped.json"
CALC = ROOT / "calculator"
GAMEPLAY = ROOT / "docs" / "GAMEPLAY.md"
HARNESS = ROOT / "docs" / "HARNESS.md"
ALIASES = ROOT / "docs" / "ALIASES.md"

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
    "_make_cube_effect": "archive/xlcalc/XLCALC.md 이력 항목이 기술하는 **개명 전** 이름. 현재는 "
                         "`_make_cube_effects()`. 이력은 당시 이름으로 남는 게 맞다",
}
# 검사 E가 훑는 문서 (명세·이력 문서도 코드 이름을 지목하면 대상).
# 스킬 폴더(`.agent/skills/<name>/`)의 문서도 대상 — 한 스킬에서만 쓰는 문서는
# docs/가 아니라 그쪽에 두므로, 여기서 빼면 그만큼 검사 사각지대가 된다.
REF_DOCS = sorted((ROOT / "docs").glob("*.md")) + sorted((ROOT / ".agent" / "skills").glob("*/*.md"))
# `archive/`(구 Streamlit UI)는 훑지 않는다 — 동결된 코드라 문서가 지목할 대상이 아니다.
REF_SRC_GLOBS = ("calculator/*.py", "scraper/*.py", "runner/*.py",
                 ".agent/skills/*/*.py")


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

def _roster_section(title: str) -> list[str]:
    """현황 목록의 한 소절(`### 완료` 등)에 나열된 캐릭터 이름."""
    names: list[str] = []
    inside = False
    for line in CHARS.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith(f"### {title}"):
            inside = True
            continue
        if inside:
            if s.startswith("#"):  # 다음 소절(### 진행 중 등)에서 종료
                break
            if s:
                names.append(s)
    return names


def load_roster_done() -> list[str]:
    return _roster_section("완료")


def load_preview() -> dict[str, dict]:
    """preview_skills.json의 캐릭터 항목. 파일이 없으면 빈 dict."""
    if not PREVIEW.exists():
        return {}
    data = json.loads(PREVIEW.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


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
    print("\n=== E. 문서가 지목한 파일·함수 실재 여부 (docs/*.md · skills) ===")
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


# ── 검사 I: 문서가 지목한 **경로**가 실재하는가 ───────────────────────────
#
# 검사 E는 백틱 안의 `이름.py` 같은 **파일명**만 본다 — 슬래시가 들어간 순간 매칭되지
# 않아서, 문서가 죽은 디렉터리 경로를 가리켜도 아무도 못 잡았다. 폴더를 옮기는 순간
# 전 문서가 조용히 거짓말을 시작하는 구조라 여기서 막는다.

# 레포에 없는 게 정상인 경로. 값은 **사유**다(사유 없는 예외는 검사를 무력화한다).
# 접두사로 판정한다 — 로컬엔 있고 CI엔 없는 것들이 섞여 있어서, 파일 존재로 판정하면
# 같은 문서가 로컬에서만 통과하고 CI에서 깨진다.
# `archive/`·`profiles/`·`reports/`처럼 REPO_ROOTS에 없는 최상위는 아래에 적지 않는다 —
# 그쪽은 애초에 검사 대상이 아니라 여기 적어 봐야 아무 일도 하지 않는다.
# 여기 있어야 하는 것은 **검사 대상 폴더 안인데 없는 게 정상인 경로**뿐이다.
PATH_EXEMPT: dict[str, str] = {
    "scraper/.session_cookie": "blablalink 로그인 세션(계정 접근권). 절대 커밋하지 않는다",
    "image/preview/": "출시 전 카드 이미지 임시 보관소. 등록된 프리뷰가 없으면 폴더 자체가 "
                      "없는 게 정상이다 (검사 G도 빈 glob을 정상으로 본다)",
}

# 검사 대상으로 볼 최상위 폴더. 여기서 시작하지 않는 토큰은 레포 경로가 아니다 —
# 원격 API 경로(`Game/...`), 레포 슬러그(`Jgaram/nikke-calc`), 분수식(`1/(N-1`),
# 슬래시 커맨드(`/char-impl`)가 전부 여기서 걸러진다. 로컬·CI가 같은 결과를 내도록
# 실제 디렉터리를 훑지 않고 못박아 둔다.
REPO_ROOTS = ("calculator/", "runner/", "docs/", "baseline/", "data/", "scraper/",
              "image/", ".github/", ".agent/", ".claude/")

# 백틱 안(`a/b.py`) 또는 마크다운 링크(](a/b.md))의 슬래시 포함 토큰.
_REF_PATH = re.compile(r"`([^`\s]*/[^`\s]*)`|\]\(([^)\s]*/[^)\s]*)\)")
# 자리표시자·수식·키 표기가 섞인 토큰은 경로가 아니다 (`docs/scenarios/<이름>.md`,
# `1/(0.2+release`, `full_burst_start_count:1/2/3`).
_NOT_PATH = re.compile(r"[<>{}()\[\]:−]")


def _norm_path(raw: str) -> str | None:
    """문서 표기를 레포 상대 경로로. 검사 대상이 아니면 None."""
    p = raw.strip()
    if ".." in p or _NOT_PATH.search(p):
        return None
    p = p.split("#", 1)[0].split("§", 1)[0].strip()
    p = p.rstrip(".,·;")                 # 문장부호가 붙어 오는 경우
    if not p.startswith(REPO_ROOTS):
        return None
    return p


def check_paths() -> bool:
    """반환: 불일치 있으면 True."""
    docs = REF_DOCS + [ROOT / "README.md", ROOT / "AGENTS.md"]
    print("\n=== I. 문서가 지목한 경로 실재 여부 (docs · skills · README · AGENTS) ===")
    fail = False
    for md in docs:
        doc = _STRIKE.sub("", md.read_text(encoding="utf-8"))   # 이력은 당시 이름으로 남는다
        bad: list[str] = []
        for m in _REF_PATH.finditer(doc):
            p = _norm_path(m.group(1) or m.group(2))
            if p is None or any(p.startswith(k) for k in PATH_EXEMPT):
                continue
            # 글롭(`docs/*.md`)은 부모 폴더가 있으면 됐다 — 매칭 결과 수까지 보지 않는다
            target = ROOT / (p.rsplit("/", 1)[0] if "*" in p else p)
            if not target.exists():
                bad.append(p)
        if bad:
            fail = True
            print(f"  [{md.relative_to(ROOT).as_posix()}] " + " · ".join(sorted(set(bad))))
    if not fail:
        print(f"  (일치 — 문서 {len(docs)}개, 예외 {len(PATH_EXEMPT)}건)")
    else:
        print("  → 폴더를 옮겼으면 문서를 고치고, 레포 밖 경로면 PATH_EXEMPT에 "
              "사유와 함께 등록한다. 옛 경로를 기록으로 남길 때는 취소선(~~...~~)을 쓴다")
    return fail


# ── 검사 F: ALIASES 정식 명칭 실재 · 원본/이격 주의 목록 ────────────────────
#
# ALIASES.md의 1열은 전부 `parsed_nikke.json`의 키여야 한다(재서술 → 기계 검사).
# 그리고 §주의 표는 "원본도 이격도 파싱된 계열" 집합의 재서술이다 —
# 그 계열의 기본 이름은 원본을 뜻하므로(해석 규칙 2), 빠지면 이격으로 잘못 읽힌다.

def check_aliases() -> bool:
    """반환: 불일치 있으면 True."""
    print("\n=== F. ALIASES 정식 명칭 ↔ parsed_nikke · 원본/이격 주의 목록 ===")
    nikke = json.loads(NIKKE.read_text(encoding="utf-8"))
    skills = set(json.loads(SKILLS.read_text(encoding="utf-8")))

    def col0(anchor: str) -> set[str]:
        """앵커 이후 첫 표의 1열. §주의는 백틱(`레드 후드`), 별칭 표는 맨몸 이름."""
        return {r[0].strip("`*").strip() for r in _table_after(ALIASES, anchor) if r and r[0]}

    # 누락 검사는 §주의 표만 본다 — 별칭 표에도 있다고 넘어가면 검사가 무력해진다
    caution = col0("## 주의")
    # §별칭 표는 부제 있음/없음 두 표로 갈려 있어 두 번째 표를 따로 집는다
    names = caution | col0("## 별칭 표") | col0("부제 없는 캐릭터")

    fail = False
    ghost = sorted(n for n in names if n not in nikke)
    if ghost:
        fail = True
        print("  유령(ALIASES엔 있으나 parsed_nikke.json 없음):", ", ".join(ghost))
        print("    → 오타이거나 스크래퍼가 키를 바꿨다(가칭 → 정식 명칭). 문서를 고친다")

    # 원본·이격이 모두 파싱된 계열 = 기본 이름이 원본을 뜻하는 계열
    both = sorted(
        base for base in {n.split(" : ")[0] for n in nikke if " : " in n}
        if base in skills and any(
            n.startswith(base + " : ") and n in skills for n in nikke
        )
    )
    missing = [b for b in both if b not in caution]
    if missing:
        fail = True
        print("  §주의 표 누락(원본·이격 모두 파싱됨):", ", ".join(missing))
        print("    → 기본 이름이 원본을 뜻한다는 사실이 문서에 없으면 이격으로 잘못 읽힌다")

    if not fail:
        print(f"  (일치 — 정식 명칭 {len(names)}개, 원본·이격 병존 {len(both)}계열)")
    return fail


# ── 검사 G: 프리뷰 항목 수명 ───────────────────────────────────────────────
#
# 프리뷰 = 출시 전 카드 이미지로 파싱한 캐릭터(`scraper/preview_skills.json`).
# 출시되면 스크랩 원문과 대조(char-add 단계 R)해 정본으로 승격해야 한다. 방치되면
# 계산기가 카드 기준 추정값을 정본인 척 계속 쓰게 되므로, 여기서 강제로 실패시킨다.

def check_preview(chars: list[str]) -> bool:
    """반환: 문제 있으면 True."""
    print("\n=== G. 프리뷰 항목 수명 (preview_skills.json) ===")
    # 프리뷰가 없어도 계속한다 — **아래 흔적 검사는 정식 등록 직후 상태에서 도는 검사**다
    preview = load_preview()
    skills = set(chars)
    scraped = set(json.loads(SCRAPED.read_text(encoding="utf-8")))
    fail = False

    released = sorted(n for n in preview if n in scraped)
    if released:
        fail = True
        print("  출시됨 — 정식 등록 필요:", ", ".join(released))
        print("    → `python -m scraper.preview_diff <이름>`으로 원문을 대조한 뒤 "
              "char-add 단계 R(.agent/skills/char-add/PREVIEW.md)을 돌린다")

    # 카드는 스킬 레벨 10 기준이다. 다른 레벨이 있으면 보간한 추정치가 섞인 것
    for name, entry in sorted(preview.items()):
        for skill_name, skill in (entry.get("스킬") or {}).items():
            lv = sorted(str(k) for k in (skill.get("values") or {}))
            if lv and lv != ["10"]:
                fail = True
                print(f"  레벨 10 외의 값이 섞임: {name} / {skill_name} → {lv}")
                print("    → 카드에 없는 레벨은 추정치다. 지운다")

    # 현황 목록은 `### 프리뷰`에 있어야 한다. `### 완료`에 있으면 정식 등록 전인데 완료로 잡힌 것
    roster_preview = set(_roster_section("프리뷰"))
    roster_done = set(load_roster_done())
    for name in sorted(preview):
        if name not in skills:
            continue          # 아직 파싱 전 — 검사 B의 관심사가 아니다
        if name in roster_done:
            fail = True
            print(f"  PARSING-CHARS `### 완료`에 프리뷰 캐릭터가 있음: {name}")
            print("    → 정식 등록 전에는 완료가 아니다. `### 프리뷰`로 옮긴다")
        elif name not in roster_preview:
            fail = True
            print(f"  PARSING-CHARS `### 프리뷰`에 누락: {name}")

    # 정식 등록이 끝났는데 남은 프리뷰 표기는 오염원이다 — 다음 세션이 검증 끝난 캐릭터를
    # 미검증으로 읽는다. 프리뷰 항목이 없는 캐릭터의 시나리오·이미지에 흔적이 있으면 잡는다.
    for md in (sorted((ROOT / "docs" / "scenarios").glob("*.md"))
               + sorted((ROOT / "docs" / "mechanics").glob("*.md"))):
        name = md.stem.replace(" _ ", " : ")
        if name in preview:
            continue
        doc = md.read_text(encoding="utf-8")
        marks = [m for m in ("## 프리뷰 미확정", "(프리뷰)") if m in doc]
        if marks:
            fail = True
            print(f"  프리뷰 흔적 남음: {md.relative_to(ROOT).as_posix()} → {' · '.join(marks)}")
            print("    → 확정된 해석은 `## 해석 선언`으로 옮기고 프리뷰 표기는 지운다 "
                  "(char-add 단계 R Step 6)")
    stale_img = sorted(p.name for p in (ROOT / "image" / "preview").glob("*")
                       if not any(p.stem.startswith(n.replace(" : ", " _ "))
                                  for n in preview))
    if stale_img:
        fail = True
        print("  프리뷰 흔적 남음: image/preview/ →", ", ".join(stale_img))
        print("    → 정식 이미지는 image/에 따로 수집된다. 카드 이미지는 지운다")

    # 카드의 이름 표기가 CDN과 다르면(공백·콜론 간격, 부제 추가) 위 released 검사가 이름
    # 매칭에 실패해 출시를 놓친다. 미파싱 캐릭터 전체(대부분은 그냥 아직 파싱 안 한 구캐)를
    # 나열해봐야 아무도 안 읽으므로, 프리뷰 이름과 **닮은 것만** 후보로 좁혀 보여준다.
    #
    # 괄호를 떼고 비교하지 않는다 — `(가칭)`은 정식 이름의 일부이고 `레이`와 `레이 (가칭)`은
    # 서로 다른 캐릭터다(ALIASES.md §주의). 후보는 어디까지나 사람이 확인할 힌트다.
    if not preview:
        if not fail:
            print("  (등록된 프리뷰 없음 — 남은 흔적도 없음)")
        return fail

    unparsed = sorted(n for n in scraped
                      if n not in skills and not n.startswith("test_"))
    print(f"  프리뷰 {len(preview)}명 / 스크랩됐지만 미파싱 {len(unparsed)}명")
    for name in sorted(set(preview) - set(released)):
        # 부제(" : ") 앞이 같으면 후보. 이건 괄호와 달리 안전하다 — 부제는 이격을 뜻하고
        # 카드에 부제가 빠진 채 공개되는 일이 있다. 괄호는 이름의 일부라 절대 떼지 않는다.
        base = name.split(" : ")[0].strip()
        cand = sorted(set(difflib.get_close_matches(name, unparsed, n=5, cutoff=0.7))
                      | {u for u in unparsed if u.split(" : ")[0].strip() == base})
        if cand:
            print(f"    [{name}] 표기가 달라 출시를 놓쳤을 수 있다 — 후보: {', '.join(cand)}")
            print("      이름이 닮았을 뿐 별개 캐릭터일 수 있다(ALIASES.md §주의). 확인 후:")
            print(f"      → `python -m scraper.preview_diff \"{name}\" --as \"<출시명>\"`")
    return fail


def check_favorite() -> bool:
    """검사 H: 애장품 캐릭터의 스킬 판본 4벌이 모두 파싱돼 있는가.

    애장품 캐릭터는 슬롯마다 판본이 둘이라(기본 / 애장품 N단계) 실질 6개 스킬을 파싱한다.
    빠진 판본이 있으면 그 단계로 돌릴 때 `char_effects()`가 끊는다 — 여기서는 **어느
    캐릭터의 어느 판본이 남았는지**를 목록으로 보여줄 뿐 실패로 잡지 않는다.
    등록 자체가 진행 중인 상태이지 문서와 데이터가 어긋난 상태는 아니기 때문이다.
    """
    skills = json.loads(SKILLS.read_text(encoding="utf-8"))
    nikke = json.loads(NIKKE.read_text(encoding="utf-8"))
    print("\n=== H. 애장품 스킬 판본 (기본 3슬롯 + 애장품 1·2·3단계) ===")
    todo = []
    for name, effs in skills.items():
        slots = (nikke.get(name) or {}).get("favorite_slots")
        if not slots:
            continue
        have = {(e["source"], e.get("favorite")) for e in effs}
        want = [(f"스킬{s}", None) for s in slots] + \
               [(f"스킬{s}", i + 1) for i, s in enumerate(slots)]
        miss = [f"스킬{src[2:]}({'애장품 %d단계' % fav if fav else '기본'})"
                for src, fav in sorted(want, key=lambda p: (p[0], p[1] or 0))
                if (src, fav) not in have]
        if miss:
            # 단계 S에서 쓰는 판본: 1~S단계가 교체한 슬롯은 그 단계 판본, 나머지는 기본
            ok = [s for s in range(4)
                  if all((f"스킬{slot}", (i + 1 if i < s else None)) in have
                         for i, slot in enumerate(slots))]
            todo.append((name, miss, ok))
    if not todo:
        print("  (전원 완비)")
        return False
    for name, miss, ok in sorted(todo):
        print(f"  [{name}] 미파싱 {len(miss)}개: {', '.join(miss)} "
              f"→ 가능 단계 {','.join(map(str, ok)) or '없음'}")
    print(f"  → {len(todo)}명. 나머지 판본은 char-add 단계 2로 파싱한다")
    return False


# ── 검사 J: 중첩형 지속 대미지 계약 ────────────────────────────────────────
#
# `[... 지속 대미지] [N 중첩]` 원문은 인스턴스가 **병존**한다(`GAMEPLAY.md` §버프 스택 —
# `[N 중첩]` 표기가 *없는* DoT만 갱신된다). 병존하면 한 틱의 대미지는 계수 × 현재 중첩인데,
# 엔진은 그 곱을 `scaling: stack_count`가 붙은 항목에만 적용한다(`timeline.py`).
# 그래서 `max_stack > 1`인 `dot_damage`에 그 표시가 빠지면 **중첩은 쌓이는데 대미지는
# 1중첩에 머문다** — 틱 횟수가 맞아서 시뮬 로그로는 보이지 않는다. 레이븐 `쇼크웨이브`가
# 정확히 그 상태였다(2026-08-23 제보, 총딜 356M → 1,065M).
#
# 원 검사는 Moris-kr이 자기 포크에 unittest로 만들었다(`moris/master` ed39da4). 이 레포에는
# 테스트 러너가 없고 이건 파싱 데이터의 정합이라 doclint로 옮겼다. 같은 테스트의 나머지
# 절반(틱이 중첩만큼 커지는가)은 골든 스냅샷 `레이드_이브레이븐`이 수치로 붙들고 있다.

def check_stacking_dot() -> bool:
    """반환: 불일치 있으면 True."""
    data = json.loads(SKILLS.read_text(encoding="utf-8"))
    print("\n=== J. 중첩형 지속 대미지 (max_stack>1 dot_damage ↔ scaling:stack_count) ===")
    missing: list[str] = []
    total = 0
    for name, effects in data.items():
        if name.startswith("test_") or not isinstance(effects, list):
            continue
        for eff in effects:
            if not isinstance(eff, dict):
                continue
            if not str(eff.get("stat", "")).startswith("dot_damage"):
                continue
            if int(eff.get("max_stack", 1) or 1) <= 1:
                continue
            total += 1
            if eff.get("scaling") != "stack_count":
                missing.append(f"{name} / {eff.get('name')}")
    for m in missing:
        print(f"  scaling 누락: {m}")
    if missing:
        print('    → 중첩이 쌓여도 틱 대미지가 1중첩에 머문다. 파싱에 '
              '`"scaling": "stack_count"`를 넣는다 (`PARSING.md` §N중첩)')
    else:
        print(f"  (일치 — 중첩형 지속 대미지 {total}건)")
    return bool(missing)


# ── 검사 K: 상태 참조의 담체 ──────────────────────────────────────────────
#
# `self_state:X` · `not_self_state:X` · `event:state_end:X`는 **X라는 이름의 버프가
# `_active`에 살아 있는가**로 판정된다(`buff_manager._has_self_state` / `tick()`의 만료
# 처리). instant는 `_active`에 들어가지 않으므로 — 주기 instant도 `_instant_timers`로
# 따로 돈다 — 상태 이름이 instant에만 붙어 있으면 그 상태는 **영원히 성립하지 않고,
# 종료 이벤트도 영원히 안 뜬다.** 조건이 거짓일 뿐이라 에러도 로그도 없이 조용히 꺼진다.
#
# 그레이브가 정확히 그 상태였다(2026-08-28): `미래 예지`가 `current_hp_reduce` instant의
# 이름이라 버스트 종료 이벤트가 없었고, 그 뒤에 달린 `방열` 계열 5개(탄창 비우기 ·
# 재장전 비율 ▼ · 아군 전체 버스트 게이지 +38.96% · 관통 대미지 +48.4%)와 `과열 II·III`가
# 통째로 미발동이었다. 고친 뒤 그레이브 개인 딜 +24%.
#
# `weapon_change` 모드명은 `_active`가 아니라 `state["weapon_change"]`에 있고
# `_has_self_state`가 그쪽도 보므로 통과시킨다(목단 `무기 변경`).

_PERSISTENT_TYPES = {"buff", "debuff", "weapon_change"}


def check_state_carrier() -> bool:
    """반환: 불일치 있으면 True."""
    data = json.loads(SKILLS.read_text(encoding="utf-8"))
    print("\n=== K. 상태 참조의 담체 (self_state·state_end ↔ 지속 효과) ===")
    bad: list[str] = []
    total = 0
    for name, effects in data.items():
        if name.startswith("test_") or not isinstance(effects, list):
            continue
        kinds: dict[str, set] = {}
        wc_modes = set()
        for eff in effects:
            if not isinstance(eff, dict):
                continue
            if eff.get("name"):
                kinds.setdefault(eff["name"], set()).add(eff.get("type"))
            if eff.get("type") == "weapon_change":
                wc_modes.add(eff.get("name"))
                wc_modes.add(eff.get("mode_name"))

        refs: set[str] = set()
        for eff in effects:
            if not isinstance(eff, dict):
                continue
            trig = eff.get("trigger") or {}
            for cond in trig.get("condition", []) or []:
                for pre in ("self_state:", "not_self_state:"):
                    if str(cond).startswith(pre):
                        refs.add(str(cond)[len(pre):])
            for timing in trig.get("timing", []) or []:
                if str(timing).startswith("event:state_end:"):
                    refs.add(str(timing)[len("event:state_end:"):])

        for ref in sorted(refs):
            total += 1
            if ref in wc_modes or ref == WEAPON_CHANGE_STATE:
                continue          # 무기 변경 모드는 state["weapon_change"]가 담체다
            types = kinds.get(ref)
            if types is None:
                bad.append(f"{name} / {ref}  — 그 이름의 효과가 없다")
            elif not (types & _PERSISTENT_TYPES):
                bad.append(f"{name} / {ref}  — {sorted(t for t in types if t)}에만 붙어 있다")

    for b in bad:
        print(f"  담체 없음: {b}")
    if bad:
        print("    → 이 상태는 영원히 성립하지 않는다(조건이 조용히 거짓). 상태 이름을 "
              "그 상태와 같이 사는 **지속 효과**에 붙인다 (`PARSING.md` §상태의 담체)")
    else:
        print(f"  (일치 — 상태 참조 {total}건)")
    return bool(bad)


def check_attach_rules() -> bool:
    """검사 L: 부착 규칙 스키마가 **좁고 닫혀** 있는가. 반환: 위반 있으면 True.

    규칙(`_rules`)은 `data/char_defaults.json`과 `data/tactics.json` 양쪽에 살고 스키마가
    같다 (docs/CONTROL.md §부착). 여기서 강제하는 것 셋 —

      ① `when` 키는 `spec.WHEN_KEYS` 안에만. 모르는 조건은 조립 시점에도 실패하지만,
         **아무도 안 돌린 규칙**은 그때까지 발견되지 않으므로 여기서 미리 잡는다.
      ② `apply` 키는 `spec.APPLY_KEYS`(`control`·`burst_pattern`) 안에만. 이 닫힘이
         `when`이 읽는 축과 겹치지 않게 만들어 판정을 한 패스로 끝나게 하는 근거다.
      ③ `tactic` 라벨은 `data/tactics.json`의 키여야 한다 — 라벨은 문자열이라 오탈자가
         나면 이탈 보고에 유령 택틱이 뜬다.

    `burst_pattern`은 그 캐릭터의 `_burst_patterns` 카탈로그에 있는 이름이어야 한다.
    """
    from runner import spec

    print("\n=== L. 부착 규칙 스키마 (char_defaults · tactics의 `_rules`) ===")
    bad: list[str] = []
    total = 0

    def audit(where: str, name: str, rules, catalog: dict) -> None:
        nonlocal total
        for i, rule in enumerate(rules or []):
            total += 1
            tag = f"{where} [{name}] #{i + 1}"
            if extra := set(rule) - {"when", "apply", "tactic", "who", "pick", "_note"}:
                bad.append(f"{tag}: 규칙에 모르는 키 {sorted(extra)}")
            if unknown := set(rule.get("when") or {}) - set(spec.WHEN_KEYS):
                bad.append(f"{tag}: when 키 {sorted(unknown)} — 있는 것 {list(spec.WHEN_KEYS)}")
            apply = rule.get("apply") or {}
            if not apply:
                bad.append(f"{tag}: apply가 비었다")
            if unknown := set(apply) - set(spec.APPLY_KEYS):
                bad.append(f"{tag}: apply 키 {sorted(unknown)} — 있는 것 {list(spec.APPLY_KEYS)}")
            if (label := rule.get("tactic")) and label not in spec.TACTICS:
                bad.append(f"{tag}: 모르는 택틱 라벨 {label!r} — data/tactics.json에 없다")
            pat = apply.get("burst_pattern")
            if pat is not None and pat not in catalog:
                bad.append(f"{tag}: 버스트 패턴 {pat!r}이 `_burst_patterns`에 없다")

    for name, layer in spec.CHAR_DEFAULTS.items():
        audit("char_defaults", name, layer.get("_rules"),
              layer.get("_burst_patterns") or {})
        if stale := {k for k in layer if k.startswith("_burst_pattern_")}:
            bad.append(f"char_defaults [{name}]: 종전 키 {sorted(stale)} — `_rules`로 옮긴다")
    for tname, t in spec.TACTICS.items():
        for rule in t.get("_rules") or []:
            if not rule.get("who") and not rule.get("pick"):
                bad.append(f"tactics [{tname}]: 규칙에 who도 pick도 없다")
            if (p := rule.get("pick")) and p not in spec.PICKERS:
                bad.append(f"tactics [{tname}]: 모르는 pick {p!r} — 있는 것 {sorted(spec.PICKERS)}")
        audit("tactics", tname, t.get("_rules"), {})
        if "roles" in t:
            bad.append(f"tactics [{tname}]: 종전 키 `roles` — `_rules`로 옮긴다")

    for b in bad:
        print(f"  {b}")
    if bad:
        print("    → 스키마는 좁고 닫혀 있어야 한다. 어휘를 넓히려면 `spec.WHEN_KEYS`·"
              "`spec.APPLY_KEYS`와 `docs/CONTROL.md §부착`을 함께 고친다")
    else:
        print(f"  (일치 — 규칙 {total}건)")
    return bool(bad)


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
    # 프리뷰 캐릭터는 §완료가 아니라 §프리뷰에 있어야 한다 — 대조는 검사 G가 한다
    preview_names = set(load_preview())
    done_set, char_set = set(done) - preview_names, set(chars) - preview_names
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

    # 검사 C·D·E·I·F·G
    fail |= check_status(verbose)
    fail |= check_mirrors()
    fail |= check_refs()
    fail |= check_paths()
    fail |= check_aliases()
    fail |= check_preview(chars)
    fail |= check_favorite()
    fail |= check_stacking_dot()
    fail |= check_state_carrier()
    fail |= check_attach_rules()

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
